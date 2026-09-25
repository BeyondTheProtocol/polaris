#!/usr/bin/env python3
"""test_entorno_cola_aislada.py — un test no puede escribir en la cola VIVA del lazo.

20-sep-2026, deuda `test-healthcheck-poluciona-cola-real`. `tests/test_healthcheck.py::recover_tests`
planta jobs de mentira en `failed/` haciendo `import cola as q`. Si `cola` se resolvió contra casa
base —porque alguien lo importó ANTES de que el test fijara `BTP_STATE_DIR`—, esos fixtures caen en
la cola de producción. Pasó: `9-x-jobB2.json` acabó en `tools/state/queue/failed/`, el healthcheck
lo tomó por un job real, encoló una investigación de pago y escaló un hallazgo en el libro.

El arreglo no es «acordarse»: es `_entorno.exige_cola_aislada()`, que mira DÓNDE apunta `cola.QUEUE`
justo antes de escribir y aborta en rojo si es la cola viva. Da igual cómo se aísle el test (variable
de entorno o parcheando la ruta): lo que se comprueba es el destino real.

Aquí se protege que el guardia (a) pare el caso que de verdad ocurrió, (b) no moleste cuando el
estado está aislado, y (c) siga invocado desde el test que lo necesita.
"""
import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(RAIZ, "tests")
CASA = os.path.expanduser("~/claudecode")


def _correr(codigo, env_extra):
    """Ejecuta un fragmento en un proceso limpio y devuelve (rc, salida)."""
    env = dict(os.environ)
    env.pop("BTP_STATE_DIR", None)
    env.update(env_extra)
    p = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                       env=env, cwd=RAIZ, timeout=60)
    return p.returncode, p.stdout + p.stderr


_PREAMBULO = (
    "import sys, os\n"
    "sys.path.insert(0, %r)\n"
    "sys.path.insert(0, %r)\n" % (TESTS, os.path.join(RAIZ, "tools"))
)


class TestGuardiaDeCola(unittest.TestCase):

    def test_para_cuando_la_cola_es_la_viva(self):
        """El caso REAL: `cola` importado antes de aislar → la cola resuelta es la de producción."""
        rc, salida = _correr(
            _PREAMBULO + "import cola\n"
            "from _entorno import exige_cola_aislada\n"
            "exige_cola_aislada()\n"
            "print('NO DEBERIA LLEGAR AQUI')\n",
            {"BTP_REPO": CASA})
        self.assertEqual(1, rc, "el guardia tiene que abortar en ROJO, no seguir ni saltar")
        self.assertIn("cola VIVA", salida)
        self.assertNotIn("NO DEBERIA LLEGAR AQUI", salida)

    def test_no_molesta_con_el_estado_aislado(self):
        tmp = tempfile.mkdtemp(prefix="cola-aislada-")
        rc, salida = _correr(
            _PREAMBULO + "import cola\n"
            "from _entorno import exige_cola_aislada\n"
            "exige_cola_aislada()\n"
            "print('SIGUE')\n",
            {"BTP_REPO": CASA, "BTP_STATE_DIR": tmp})
        self.assertEqual(0, rc, salida[-300:])
        self.assertIn("SIGUE", salida)

    def test_tambien_vale_parcheando_la_ruta(self):
        """Tres tests se aíslan sustituyendo `cola.QUEUE`; el guardia tiene que aceptarlo."""
        tmp = tempfile.mkdtemp(prefix="cola-parche-")
        rc, salida = _correr(
            _PREAMBULO + "import cola, os\n"
            "cola.QUEUE = os.path.join(%r, 'queue')\n" % tmp +
            "from _entorno import exige_cola_aislada\n"
            "exige_cola_aislada()\n"
            "print('SIGUE')\n",
            {"BTP_REPO": CASA})
        self.assertEqual(0, rc, salida[-300:])

    def test_todos_los_que_tocan_la_cola_lo_invocan(self):
        """Un guardia que nadie llama no protege de nada. Y si mañana otro test toca la cola,
        que este se ponga rojo hasta que lo llame también."""
        sin_guardia = []
        for nombre in sorted(os.listdir(TESTS)):
            if not nombre.startswith("test_") or not nombre.endswith(".py"):
                continue
            if nombre == "test_entorno_cola_aislada.py":
                continue
            with open(os.path.join(TESTS, nombre), encoding="utf-8") as f:
                src = f.read()
            toca = "cola.QUEUE" in src or "q.QUEUE" in src
            if toca and "exige_cola_aislada" not in src:
                sin_guardia.append(nombre)
        self.assertEqual([], sin_guardia,
                         "tests que escriben en la cola sin pasar por el guardia: %s" % sin_guardia)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ COLA AISLADA EN LOS TESTS (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
