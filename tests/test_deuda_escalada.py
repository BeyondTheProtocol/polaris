#!/usr/bin/env python3
"""test_deuda_escalada.py — mientras haya un hallazgo repetido sin cerrar, NADIE dice «todo en verde».

Esto es la palanca del plan «que quede arreglado» (25-jul-2026). El diagnóstico: Polaris DETECTA
excelente y CIERRA fatal — el bug `Task`/`Agent` del muro se re-reportó **14 veces en 8 días** y no
pasó nada. Un detector más no arregla eso; lo que faltaba era que **ignorar escueza**.

Dos mitades:
  1. **La real**: si `tools/state/deuda.json` tiene deuda ESCALADA (repetida ≥3 veces, ≥2 si toca
     muro/clínico), este test FALLA. Por eso `test_all.sh` no puede salir en verde con un fallo
     conocido encima de la mesa.
  2. **Las reglas**: que `cerrar` sea imposible sin un test que EXISTA y esté enganchado en
     `test_all.sh`; que `visto` escale; y que un hallazgo cerrado que reaparece se marque REGRESIÓN.

OJO (13-sep-2026): las reglas cierran deudas con un test DE MENTIRA que pasa, nunca con este
fichero. Hasta ese día usaban `tests/test_deuda_escalada.py`, y en cuanto `cerrar` empezó a
EJECUTAR el test, este fichero se lanzaba a sí mismo cuatro veces por nivel: agotó los procesos
del mini. Ningún test de cierre puede apuntar a sí mismo.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)


def _test_de_mentira(tmp):
    """Un test que PASA, enganchado en un `test_all.sh` de juguete dentro de `tmp`.

    Devuelve (ruta_del_test, ruta_del_test_all). Así las reglas de cierre se prueban sin tocar el
    `test_all.sh` real y sin que este fichero se ejecute a sí mismo."""
    ruta = os.path.join(tmp, "test_zz_de_mentira_verde.py")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("print('ok')\n")
    ta = os.path.join(tmp, "test_all.sh")
    with open(ta, "w", encoding="utf-8") as f:
        f.write("runpy test_zz_de_mentira_verde.py\n")
    return ruta, ta


class DeudaReal(unittest.TestCase):
    """La mitad que mira el estado DE VERDAD: si hay deuda escalada, esto se pone rojo."""

    def test_no_hay_deuda_escalada_sin_cerrar(self):
        import deuda
        esc = deuda.escaladas()
        if esc:
            detalle = "\n".join(
                "   🔴 %s · %dx · NED:%s%s\n      %s"
                % (k, v.get("veces", 0), v.get("impacto_ned", "?"),
                   " ⛔muro" if v.get("muro") else "", (v.get("que") or "")[:110])
                for k, v in esc.items())
            self.fail("%d hallazgo(s) detectados varias veces y SIN CERRAR. No se puede decir «todo "
                      "en verde» con esto abierto — ciérralos con un test "
                      "(`deuda.py cerrar <clave> --test …`) o justifica el aplazamiento:\n%s"
                      % (len(esc), detalle))


class LibroEnCasaBase(unittest.TestCase):
    """El libro se lee SIEMPRE de casa base, aunque corras desde un worktree (30-jul-2026).

    Sin esto la mitad de arriba mentía: `tools/state/` no está versionado, así que desde un
    worktree no existe, el libro se leía vacío y el test salía VERDE en la rama y rojo en casa
    base. Cualquier sesión que validara antes de fusionar creía que no había deuda escalada. Se
    cazó fusionando los KPIs de NED, con dos hallazgos escalados que la rama no veía.
    """

    def test_el_libro_no_se_lee_del_worktree(self):
        previo = {k: os.environ.pop(k, None) for k in ("BTP_STATE_DIR", "BTP_REPO")}
        try:
            sys.modules.pop("deuda", None)
            import deuda
            self.assertEqual(os.path.expanduser("~/claudecode/tools/state"), deuda.STATE)
            self.assertNotIn(".claude/worktrees", deuda.STATE)
            # El test que se exige al CERRAR sí es el del árbol en el que trabajas: ahí es donde
            # lo acabas de escribir.
            self.assertTrue(deuda.TEST_ALL.endswith(os.path.join("tests", "test_all.sh")))
        finally:
            for k, v in previo.items():
                if v is not None:
                    os.environ[k] = v
            sys.modules.pop("deuda", None)


class ReglasDeCierre(unittest.TestCase):
    """Las reglas, sobre un libro de juguete (no toca el estado real)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["BTP_STATE_DIR"] = self.tmp
        sys.modules.pop("deuda", None)
        import deuda
        self.d = deuda
        self.test_ok, self.d.TEST_ALL = _test_de_mentira(self.tmp)

    def tearDown(self):
        os.environ.pop("BTP_STATE_DIR", None)
        sys.modules.pop("deuda", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repetir_escala_a_la_tercera(self):
        self.d.abrir("x", "algo que vuelve")
        self.assertEqual(self.d.visto("x")["estado"], "abierto", "la 2ª vez todavía no escala")
        self.assertEqual(self.d.visto("x")["estado"], "escalado", "la 3ª vez SÍ escala")
        self.assertEqual(len(self.d.escaladas()), 1)

    def test_lo_del_muro_escala_antes(self):
        self.d.abrir("m", "toca el muro", ned="alto", muro=True)
        self.assertEqual(self.d.visto("m")["estado"], "escalado",
                         "muro/clínico escala a la 2ª (filtro NED)")

    def test_no_se_puede_cerrar_sin_test(self):
        self.d.abrir("y", "algo")
        ok, motivo = self.d.cerrar("y")
        self.assertFalse(ok)
        self.assertIn("test", motivo)

    def test_no_se_puede_cerrar_con_un_test_que_no_existe(self):
        self.d.abrir("y", "algo")
        ok, _m = self.d.cerrar("y", test="tests/test_que_no_existe_jamas.py")
        self.assertFalse(ok)

    def test_no_se_puede_cerrar_con_un_test_que_nadie_corre(self):
        """Existe pero no está en test_all.sh: un test que nadie corre no cierra nada."""
        suelto = os.path.join(ROOT, "tests", "_suelto_tmp_deuda.py")
        with open(suelto, "w") as f:
            f.write("# test suelto, a propósito no enganchado\n")
        try:
            self.d.abrir("y", "algo")
            ok, motivo = self.d.cerrar("y", test="tests/_suelto_tmp_deuda.py")
            self.assertFalse(ok)
            self.assertIn("test_all.sh", motivo)
        finally:
            os.remove(suelto)

    def test_se_cierra_con_un_test_de_verdad(self):
        self.d.abrir("y", "algo")
        ok, motivo = self.d.cerrar("y", test=self.test_ok)
        self.assertTrue(ok, motivo)
        self.assertEqual(self.d.escaladas(), {})

    def test_si_vuelve_despues_de_cerrado_es_REGRESION(self):
        self.d.abrir("y", "algo")
        self.d.cerrar("y", test=self.test_ok)
        it = self.d.visto("y", "ha vuelto")
        self.assertEqual(it["estado"], "abierto")
        self.assertTrue(it.get("regresion"))
        self.assertIsNone(it["test"], "al reabrirse pierde el test: hay que volver a demostrarlo")

    def test_deja_latido_para_que_healthcheck_lo_vigile(self):
        self.d.abrir("y", "algo")
        hb = os.path.join(self.tmp, "heartbeat", "deuda.json")
        self.assertTrue(os.path.exists(hb), "sin latido, nadie vigila al vigilante")
        d = json.load(open(hb))
        self.assertEqual(d["agente"], "deuda")
        self.assertIn("ts", d)


class Remision(unittest.TestCase):
    """R4 (29-jul-26): lo que se va se calla; lo que va y viene, no.

    La mitad peligrosa del cambio. Remitir existe para que la suite deje de estar roja por alarmas
    caducadas, y el riesgo evidente es que se convierta en la puerta de atrás para callar cualquier
    cosa sin arreglarla. Estos casos son los que impiden eso.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["BTP_STATE_DIR"] = self.tmp
        sys.modules.pop("deuda", None)
        import deuda
        self.d = deuda
        self.test_ok, self.d.TEST_ALL = _test_de_mentira(self.tmp)

    def tearDown(self):
        os.environ.pop("BTP_STATE_DIR", None)
        sys.modules.pop("deuda", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _escalar(self, clave="x"):
        self.d.abrir(clave, "condición que se detecta sola")
        self.d.visto(clave)
        self.d.visto(clave)
        self.assertEqual(len(self.d.escaladas()), 1, "preparación: debería estar escalado")
        return clave

    def test_remitir_calla_la_suite(self):
        k = self._escalar()
        ok, _m = self.d.remitir(k, "la condición dejó de detectarse")
        self.assertTrue(ok)
        self.assertEqual(self.d.escaladas(), {}, "remitido no puede poner roja la suite")

    def test_remitir_NO_es_cerrar(self):
        """Lo que separa esto de una puerta trasera: sigue en el libro y sigue sin test."""
        k = self._escalar()
        self.d.remitir(k, "se fue")
        it = self.d.abiertas().get(k)
        self.assertIsNotNone(it, "remitido sigue ABIERTO: no está cerrado, solo callado")
        self.assertNotEqual(it["estado"], "cerrado")
        self.assertIsNone(it["test"], "remitir no puede inventarse un test")

    def test_si_vuelve_despierta_y_escala_otra_vez(self):
        k = self._escalar()
        self.d.remitir(k, "se fue")
        it = self.d.visto(k, "ha vuelto")
        self.assertIn(k, self.d.escaladas(), "volvió: tiene que gritar de nuevo")
        self.assertEqual(it["remisiones"], 1, "la historia de recaídas no se borra al volver")

    def test_a_la_tercera_recaida_se_queda_INTERMITENTE(self):
        k = self._escalar()
        for _ in range(3):
            self.d.remitir(k, "se fue")
            self.d.visto(k, "y volvió")
        it = self.d.abiertas()[k]
        self.assertEqual(it["estado"], "intermitente")
        self.assertEqual(it["remisiones"], 3)
        self.assertIn(k, self.d.escaladas(), "intermitente grita como escalado")

    def test_un_intermitente_ya_no_se_puede_callar(self):
        k = self._escalar()
        for _ in range(3):
            self.d.remitir(k, "se fue")
            self.d.visto(k, "y volvió")
        ok, motivo = self.d.remitir(k, "intento callarlo otra vez")
        self.assertFalse(ok, "un fallo que va y viene no se calla más")
        self.assertIn("INTERMITENTE", motivo)
        self.assertIn(k, self.d.escaladas())

    def test_un_intermitente_SI_se_puede_cerrar_con_test(self):
        """R1 manda por encima de R4: la salida legítima sigue siendo demostrarlo."""
        k = self._escalar()
        for _ in range(3):
            self.d.remitir(k, "se fue")
            self.d.visto(k, "y volvió")
        ok, motivo = self.d.cerrar(k, test=self.test_ok)
        self.assertTrue(ok, motivo)
        self.assertEqual(self.d.escaladas(), {})

    def test_no_se_remite_lo_que_ya_estaba_cerrado(self):
        self.d.abrir("y", "algo")
        self.d.cerrar("y", test=self.test_ok)
        ok, _m = self.d.remitir("y", "da igual")
        self.assertFalse(ok)


if __name__ == "__main__":
    suite = unittest.TestSuite()
    for cls in (DeudaReal, ReglasDeCierre, Remision):
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(cls))
    res = unittest.TextTestRunner(verbosity=0).run(suite)
    if res.wasSuccessful():
        print("✅ DEUDA/ESCALADA EN VERDE (%d casos · sin hallazgos repetidos sin cerrar)"
              % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
