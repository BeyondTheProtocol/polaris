#!/usr/bin/env python3
"""test_permiso_libro_usos.py — lo que se gasta de un permiso vive en el libro, con cerrojo.

POR QUÉ (14-oct-26). El contador de usos iba dentro del permiso y se refirmaba en cada uso:
quien gastaba necesitaba la clave de firma, que una sesión remota (SSH) no puede leer. Y el
guard miraba «¿usado?» y apuntaba después, sin cerrojo y reescribiendo el fichero entero: dos
envíos a la vez pasaban los dos.

QUÉ SE FIJA. Mirar y apuntar es una sola operación bajo `flock`:
  · de N usos simultáneos del mismo permiso, pasa exactamente uno (hilos y procesos);
  · cada ítem de un lote sale una vez, y con el último el lote queda gastado;
  · web_novedad gasta hasta su tope y ni uno más, lo retoquen como lo retoquen;
  · apuntes de permisos distintos, escritos a la vez, no se pisan.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import permiso_envio as P  # noqa: E402

HIJO = ("import sys; sys.path.insert(0, sys.argv[1]); import permiso_envio as P; "
        "print(int(P.consumir({'session_id': 's', 'prompt_id': 'p', 'nonce': 'n'}, sys.argv[2])))")


class LibroDeUsos(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="libro-")
        self._estado = os.environ.get("BTP_STATE_DIR")
        os.environ["BTP_STATE_DIR"] = self.tmp
        self.d = {"session_id": "s", "prompt_id": "p", "nonce": "n"}

    def tearDown(self):
        if self._estado is None:
            os.environ.pop("BTP_STATE_DIR", None)
        else:
            os.environ["BTP_STATE_DIR"] = self._estado
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _a_la_vez(self, fn, n=24):
        salida, puerta = [], threading.Barrier(n)

        def _uno(i):
            puerta.wait()
            salida.append(fn(i))
        hilos = [threading.Thread(target=_uno, args=(i,)) for i in range(n)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        return salida

    def _lineas(self):
        with open(P.usados_path(), encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_de_muchos_usos_a_la_vez_pasa_uno(self):
        res = self._a_la_vez(lambda i: P.consumir(self.d, "hilo-%d" % i))
        self.assertEqual(sum(1 for r in res if r), 1, res)
        self.assertEqual(len(self._lineas()), 1)
        self.assertTrue(P._usado(self.d))

    def test_entre_procesos_tambien_pasa_uno(self):
        env = dict(os.environ, BTP_STATE_DIR=self.tmp)
        procs = [subprocess.Popen([sys.executable, "-c", HIJO, os.path.join(ROOT, "tools"), "p%d" % i],
                                  stdout=subprocess.PIPE, text=True, env=env) for i in range(8)]
        res = [int(p.communicate(timeout=60)[0].strip()) for p in procs]
        self.assertEqual(sum(res), 1, res)

    def test_consumir_dos_veces_seguidas(self):
        self.assertTrue(P.consumir(self.d, "a"))
        self.assertFalse(P.consumir(self.d, "b"))

    def test_permisos_distintos_a_la_vez_no_se_pisan(self):
        res = self._a_la_vez(lambda i: P.consumir(dict(self.d, prompt_id="p%d" % i), "x"))
        self.assertTrue(all(res))
        self.assertEqual(len(self._lineas()), len(res), "ningún apunte se perdió")

    def test_cada_item_del_lote_sale_una_vez(self):
        res = self._a_la_vez(lambda i: P.gastar_item(self.d, "huella-%d" % (i % 3), 3))
        self.assertEqual(sum(1 for r in res if r), 3, res)
        self.assertEqual(P.items_gastados(self.d), {"huella-0", "huella-1", "huella-2"})
        self.assertTrue(P._usado(self.d), "con el último ítem el lote queda gastado")
        self.assertFalse(P.gastar_item(self.d, "huella-nueva", 3))

    def test_un_lote_a_medias_no_esta_gastado(self):
        self.assertTrue(P.gastar_item(self.d, "h1", 3))
        self.assertFalse(P._usado(self.d))
        self.assertFalse(P.gastar_item(self.d, "h1", 3), "el mismo ítem, dos veces no")

    def test_reemitir_el_permiso_del_mismo_mensaje_no_devuelve_lo_gastado(self):
        """Lo gastado se cuenta por (sesión, mensaje), no por emisión. Si no, bastaría con pedir
        otra vez la firma del mismo mensaje (otro `nonce`) para que el ítem ya enviado, o las
        entradas ya publicadas, volvieran a salir."""
        otro = dict(self.d, nonce="otra-emision")
        self.assertTrue(P.gastar_item(self.d, "h1", 3))
        self.assertEqual(P.items_gastados(otro), set(["h1"]))
        self.assertFalse(P.gastar_item(otro, "h1", 3), "el ítem ya salió, lo firme quien lo firme")
        self.assertTrue(P.gastar_item(otro, "h2", 3))
        self.assertTrue(P.gastar_item(self.d, "h3", 3))
        self.assertTrue(P._usado(otro), "tres ítems entre las dos emisiones: lote gastado")

    def test_reemitir_tampoco_reinicia_el_contador_de_web_novedad(self):
        otro = dict(self.d, nonce="otra-emision")
        self.assertEqual(P.gastar_uno(self.d, None, 3)["usos"], 1)
        self.assertEqual(P.gastar_uno(otro, None, 3)["usos"], 2)
        self.assertEqual(P.gastar_uno(self.d, None, 3)["usos"], 3)
        with self.assertRaises(PermissionError):
            P.gastar_uno(otro, None, 3)

    def test_web_novedad_gasta_hasta_su_tope_y_ni_uno_mas(self):
        def _uno(_i):
            try:
                return P.gastar_uno(self.d, None, 3)["usos"]
            except PermissionError:
                return 0
        res = self._a_la_vez(_uno, n=12)
        self.assertEqual(sorted(r for r in res if r), [1, 2, 3], res)
        self.assertTrue(P._usado(self.d))

    def test_gastado_del_todo_no_admite_usos_sueltos(self):
        self.assertTrue(P.consumir(self.d, "envio"))
        with self.assertRaises(PermissionError):
            P.gastar_uno(self.d, None, 3)
        self.assertFalse(P.gastar_item(self.d, "h", 3))

    def test_el_libro_no_crece_sin_fin(self):
        for i in range(P.MAX_LIBRO + 40):
            P.consumir(dict(self.d, prompt_id="p%d" % i), "x")
        self.assertEqual(len(self._lineas()), P.MAX_LIBRO)
        self.assertTrue(P._usado(dict(self.d, prompt_id="p%d" % (P.MAX_LIBRO + 39))))

    def test_una_linea_rota_no_tumba_el_libro(self):
        with open(P.usados_path(), "w", encoding="utf-8") as f:
            f.write("esto no es json\n")
        self.assertFalse(P._usado(self.d))
        self.assertTrue(P.consumir(self.d, "x"))
        self.assertTrue(P._usado(self.d))


if __name__ == "__main__":
    unittest.main(verbosity=1)
