#!/usr/bin/env python3
"""test_inventario_uso.py — el uso de las piezas lo mide un script con trazas reales, y el ciclo
de vida (60 días → observación, 90 y huérfana → propuesta de retiro) no da nada por muerto sin
datos.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

Lo que no puede pasar, y por eso se prueba:
  · que leer una pieza (`cat tools/x.py`) cuente como usarla;
  · que el último uso retroceda: los transcripts se purgan a ~30 días y lo acumulado es la única
    memoria más larga;
  · que el ciclo proponga retirar algo que lanza launchd, que llama un hook o que importa otra
    pieza en uso, o que lo proponga antes de tener 90 días de medida;
  · que el aviso a Vega se repita cada noche con la misma lista.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import inventario as I  # noqa: E402


class TestQueEsUsar(unittest.TestCase):
    def test_ejecutar_si_leer_no(self):
        casos = {
            "python3 tools/fichas.py comprobar": {"fichas.py"},
            "cd ~/claudecode && python3 /Users/x/claudecode/tools/kb.py ask 'y'": {"kb.py"},
            "bash tools/backup.sh": {"backup.sh"},
            "tools/lazo_telegram.sh on": {"lazo_telegram.sh"},
            "sed -n 1,5p tools/a.py && tools/deploy_ff.sh": {"deploy_ff.sh"},
            ".venv/bin/python -u tools/hooks/muro_commit_guard.py": {"hooks/muro_commit_guard.py"},
            "python3 -m tools.onco buscar x": {"onco.py"},
            "cat tools/onco.py": set(),
            "grep -n def tools/x.py": set(),
        }
        for cmd, esperado in casos.items():
            self.assertEqual(I.piezas_lanzadas(cmd), esperado, cmd)


class TestSesiones(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="proy_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _linea(self, ts, cmd, nombre="Bash"):
        return json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [
            {"type": "tool_use", "name": nombre, "input": {"command": cmd}}]}}) + "\n"

    def test_se_queda_con_el_ultimo_uso_de_cada_pieza(self):
        with open(os.path.join(self.d, "s.jsonl"), "w") as fh:
            fh.write(self._linea("2026-09-20T10:00:00Z", "python3 tools/onco.py x"))
            fh.write(self._linea("2026-09-22T10:00:00Z", "python3 tools/onco.py y"))
            fh.write(self._linea("2026-09-23T10:00:00Z", "cat tools/kb.py"))
            fh.write('{"roto": \n')
        v = I._usos_sesiones(30, raiz=self.d)
        self.assertEqual(v, {"onco.py": "2026-09-22T10:00:00"})

    def test_los_transcripts_viejos_no_se_leen(self):
        p = os.path.join(self.d, "viejo.jsonl")
        with open(p, "w") as fh:
            fh.write(self._linea("2026-01-01T10:00:00Z", "python3 tools/onco.py x"))
        os.utime(p, (0, 0))
        self.assertEqual(I._usos_sesiones(2, raiz=self.d), {})


class TestAcumula(unittest.TestCase):
    def setUp(self):
        self.e = tempfile.mkdtemp(prefix="estado_")

    def tearDown(self):
        shutil.rmtree(self.e, ignore_errors=True)

    def test_el_ultimo_uso_nunca_retrocede_y_la_medida_empieza_una_vez(self):
        hoy = datetime(2026, 9, 25, 3, 30)
        I.actualizar_uso(2, self.e, {"sesion": {"onco.py": "2026-09-24T10:00:00"}}, hoy)
        uso, _ = I.actualizar_uso(2, self.e, {"sesion": {"onco.py": "2026-09-01T10:00:00"}},
                                  datetime(2026, 9, 30, 3, 30))
        self.assertEqual(uso["piezas"]["onco.py"]["ultimo_uso"], "2026-09-24T10:00:00")
        self.assertEqual(uso["_meta"]["fecha_inicio_medida"], "2026-09-23")

    def test_lo_que_no_es_pieza_no_entra(self):
        uso, _ = I.actualizar_uso(2, self.e, {"sesion": {"no_existe_nunca.py": "2026-09-24"}})
        self.assertEqual(uso["piezas"], {})


class TestCiclo(unittest.TestCase):
    HOY = date(2026, 12, 31)
    USO = {"_meta": {"fecha_inicio_medida": "2026-08-26"}, "piezas": {
        "usada.py": {"ultimo_uso": "2026-12-20T10:00:00"}}}

    def _fila(self, pieza, estado="huerfana", commit="2026-06-01", citas=()):
        return {"pieza": pieza, "estado": estado, "ultimo_commit": commit,
                "citada_por": list(citas)}

    def _fases(self, filas, uso=None):
        return {f["pieza"]: f["fase"] for f in I.ciclo(self.HOY, uso or self.USO, filas)}

    def test_retiro_solo_si_90_dias_y_huerfana(self):
        f = self._fases([self._fila("muerta.py"), self._fila("citada.py", "viva")])
        self.assertEqual(f, {"muerta.py": "propuesta-retiro", "citada.py": "observacion"})

    def test_60_dias_es_observacion(self):
        uso = {"_meta": {"fecha_inicio_medida": "2026-10-20"}, "piezas": {}}
        self.assertEqual(self._fases([self._fila("x.py")], uso), {"x.py": "observacion"})

    def test_sin_90_dias_de_medida_no_se_propone_retiro(self):
        uso = {"_meta": {"fecha_inicio_medida": "2026-12-01"}, "piezas": {}}
        self.assertEqual(self._fases([self._fila("x.py")], uso), {})

    def test_un_commit_reciente_cuenta_como_vida(self):
        self.assertEqual(self._fases([self._fila("x.py", commit="2026-12-15")]), {})

    def test_entrada_y_citada_por_hook_quedan_fuera(self):
        filas = [self._fila("d.py", "entrada"),
                 self._fila("h.py", "viva", citas=[".claude/hooks/gate_salida.py"])]
        self.assertEqual(self._fases(filas), {})

    def test_una_libreria_hereda_el_uso_de_quien_la_importa(self):
        filas = [self._fila("lib.py", "viva", citas=["tools/usada.py", "tests/test_lib.py"])]
        self.assertEqual(self._fases(filas), {})


class TestProponer(unittest.TestCase):
    def setUp(self):
        self.e = tempfile.mkdtemp(prefix="estado_")

    def tearDown(self):
        shutil.rmtree(self.e, ignore_errors=True)

    def test_propone_a_vega_una_vez_por_lista(self):
        filas = [{"pieza": "fichas.py", "fase": "observacion", "dias_sin_uso": 61,
                  "estado": "viva"}]
        self.assertTrue(I.proponer_ciclo(filas, self.e))
        self.assertFalse(I.proponer_ciclo(filas, self.e))
        with open(os.path.join(self.e, "vega", "propuestas_hilos.jsonl")) as fh:
            lineas = [json.loads(l) for l in fh]
        self.assertEqual(len(lineas), 1)
        self.assertEqual(lineas[0]["origen"], "inventario.py --ciclo")
        self.assertIn("clasificar no es borrar", lineas[0]["nota"])


class TestDaemon(unittest.TestCase):
    def test_plist_y_registro(self):
        import plistlib
        with open(os.path.join(RAIZ, "tools", "launchd", "com.btp.inventario-uso.plist"),
                  "rb") as fh:
            pl = plistlib.load(fh)
        self.assertEqual(pl["ProgramArguments"][-2:],
                         ["/Users/polaris/claudecode/tools/inventario.py", "--noche"])
        self.assertFalse(pl["RunAtLoad"])
        with open(os.path.join(RAIZ, "tools", "launchd", "REGISTRO.json")) as fh:
            self.assertIn("com.btp.inventario-uso", json.load(fh)["daemons"])


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    ok = r.testsRun - len(r.failures) - len(r.errors)
    print("RESULTADO inventario-uso: %d OK, %d fallos" % (ok, len(r.failures) + len(r.errors)))
    sys.exit(0 if r.wasSuccessful() else 1)
