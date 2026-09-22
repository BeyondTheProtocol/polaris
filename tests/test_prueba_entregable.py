#!/usr/bin/env python3
"""test_prueba_entregable.py — el gate que impide cerrar un job que no entregó nada.

20-sep-2026, deuda `cola-marca-done-sin-verificar-cambio-real` (3 detecciones): el dispatcher
cerraba mirando solo el código de salida, y un job de 6,03 USD para arreglar `tools/seguimiento.py`
se marcó hecho sin que el cambio existiera.

Lo que se protege:
  · sin huella en disco NO se cumple (que es la dirección que importa: fail-closed);
  · una huella VIEJA tampoco cuenta — si no, un job reencolado daría por bueno lo que dejó otro;
  · lo que no se entiende (tipo raro, JSON corrupto, instante ilegible) se rechaza, no se asume;
  · la lista de extensiones que el lazo no puede escribir no se desincroniza del muro.
"""
import json
import os
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))


class TestCumple(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="prueba-repo-")
        self.state = os.path.join(self.repo, "tools", "state")
        os.makedirs(self.state)
        os.environ["BTP_REPO"] = self.repo
        os.environ["BTP_STATE_DIR"] = self.state
        sys.modules.pop("prueba_entregable", None)
        import prueba_entregable
        self.p = prueba_entregable
        self.ahora = time.time()

    def tearDown(self):
        for k in ("BTP_REPO", "BTP_STATE_DIR"):
            os.environ.pop(k, None)
        sys.modules.pop("prueba_entregable", None)

    def _escribir(self, rel, edad_h=0.0):
        ruta = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("contenido")
        t = self.ahora - edad_h * 3600
        os.utime(ruta, (t, t))
        return ruta

    # ── ruta ────────────────────────────────────────────────────────────────────────────
    def test_lo_que_no_existe_no_cumple(self):
        ok, motivo = self.p.cumple({"tipo": "ruta", "ruta": "04/informe.md"}, self.ahora - 60)
        self.assertFalse(ok)
        self.assertIn("no existe", motivo)

    def test_un_fichero_tocado_despues_cumple(self):
        self._escribir("04/informe.md")
        ok, _ = self.p.cumple({"tipo": "ruta", "ruta": "04/informe.md"}, self.ahora - 60)
        self.assertTrue(ok)

    def test_un_fichero_viejo_no_cumple(self):
        """El job no lo escribió: ya estaba. Es el caso del reencolado."""
        self._escribir("04/informe.md", edad_h=48)
        ok, motivo = self.p.cumple({"tipo": "ruta", "ruta": "04/informe.md"}, self.ahora - 60)
        self.assertFalse(ok)
        self.assertIn("no se toco", motivo)

    def test_carpeta_vacia_no_cumple_y_con_hijo_nuevo_si(self):
        os.makedirs(os.path.join(self.repo, "04", "notas"))
        ok, _ = self.p.cumple({"tipo": "ruta", "ruta": "04/notas"}, self.ahora - 60)
        self.assertFalse(ok)
        self._escribir("04/notas/nueva.md")
        ok, _ = self.p.cumple({"tipo": "ruta", "ruta": "04/notas"}, self.ahora - 60)
        self.assertTrue(ok)

    # ── deuda ───────────────────────────────────────────────────────────────────────────
    def _libro(self, contenido):
        with open(os.path.join(self.state, "deuda.json"), "w", encoding="utf-8") as f:
            json.dump(contenido, f)

    def test_deuda_sin_la_clave_no_cumple(self):
        self._libro({"otra": {"anotado_ts": self.ahora}})
        ok, motivo = self.p.cumple({"tipo": "deuda", "clave": "la-mia"}, self.ahora - 60)
        self.assertFalse(ok)
        self.assertIn("sin entrada", motivo)

    def test_deuda_anotada_despues_cumple_y_antes_no(self):
        self._libro({"la-mia": {"anotado_ts": self.ahora - 7200, "abierto_ts": self.ahora - 9000}})
        ok, _ = self.p.cumple({"tipo": "deuda", "clave": "la-mia"}, self.ahora - 60)
        self.assertFalse(ok, "una anotación de hace dos horas no la dejó este job")
        self._libro({"la-mia": {"anotado_ts": self.ahora, "abierto_ts": self.ahora - 9000}})
        ok, _ = self.p.cumple({"tipo": "deuda", "clave": "la-mia"}, self.ahora - 60)
        self.assertTrue(ok)

    def test_libro_corrupto_no_cumple(self):
        with open(os.path.join(self.state, "deuda.json"), "w", encoding="utf-8") as f:
            f.write("{esto no es json")
        ok, _ = self.p.cumple({"tipo": "deuda", "clave": "la-mia"}, self.ahora - 60)
        self.assertFalse(ok)

    # ── fail-closed ─────────────────────────────────────────────────────────────────────
    def test_lo_que_no_se_entiende_se_rechaza(self):
        for mala in ({"tipo": "commit", "rama": "x"}, {"tipo": "ruta"}, {}, None, "ruta",
                     {"tipo": "deuda", "clave": ""}):
            ok, _ = self.p.cumple(mala, self.ahora - 60)
            self.assertFalse(ok, "%r tenía que rechazarse" % (mala,))
        self._escribir("04/informe.md")
        ok, _ = self.p.cumple({"tipo": "ruta", "ruta": "04/informe.md"}, "ayer")
        self.assertFalse(ok, "un instante de referencia ilegible no puede dar por bueno nada")

    def test_no_usa_subprocess_ni_red(self):
        """Corre en el punto del lazo que no se puede permitir colgarse."""
        fuente = open(os.path.join(RAIZ, "tools", "prueba_entregable.py"), encoding="utf-8").read()
        codigo = fuente.split('"""', 2)[-1]        # fuera el docstring: ahí SÍ se nombran, explicando por qué no están
        for prohibido in ("import subprocess", "import urllib", "import socket",
                          "import requests", "import cola"):
            self.assertNotIn(prohibido, codigo,
                             "prueba_entregable no puede depender de %s" % prohibido.split()[-1])


class TestListaDeExtensiones(unittest.TestCase):
    def test_no_se_desincroniza_del_muro(self):
        """Si el muro deja de dejar escribir otra extensión, la cola tiene que enterarse."""
        sys.path.insert(0, os.path.join(RAIZ, ".claude", "hooks"))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "muro_guard_lectura", os.path.join(RAIZ, ".claude", "hooks", "muro_guard.py"))
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        import cola
        faltan = set(guard.WRITE_EXEC_EXT) - set(cola.PRUEBA_EXT_PROHIBIDAS)
        self.assertEqual(set(), faltan,
                         "el muro prohíbe escribir %s y cola.py lo aceptaría como prueba" % sorted(faltan))


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ PRUEBA DE ENTREGABLE EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
