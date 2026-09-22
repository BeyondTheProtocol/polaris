#!/usr/bin/env python3
"""test_kpi_ned.py — los dos números de NED se calculan bien y el latido escuece cuando toca.

Esto es el freno del cambio estructural «cerrar el bucle de auto-mejora» (30-jul-2026). El bucle
sabía detectar y sabía cerrar deuda, pero no tenía ninguna medida de si el sistema está más cerca de
NED que ayer, así que solo podía crecer. `tools/kpi_ned.py` pone dos números y este test cubre las
tres cosas que, si se rompen en silencio, dejan la métrica de adorno:

  1. Que los números salgan bien (incluida la fuente que NO existe: no saber = desfasado, no verde).
  2. Que la primera pasada NO abra deuda (sin marca previa no hay nada con qué comparar).
  3. Que a los 30 días sin mejorar SÍ la abra — y que una mejora, aunque sea de un solo número,
     reinicie el reloj.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

DIA = 86400


def _escribe(d, nombre, contenido):
    with open(os.path.join(d, nombre), "w", encoding="utf-8") as f:
        json.dump(contenido, f, ensure_ascii=False)


def _hilo(hid, **kw):
    h = {"id": hid, "titulo": hid, "estado": "en_curso"}
    h.update(kw)
    return h


class KpiNed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kpi-ned-")
        os.environ["BTP_STATE_DIR"] = self.tmp
        # `deuda` fija su ruta de estado al importarse: hay que recargarlo DESPUÉS de tocar el env,
        # o el test escribiría en el libro de deuda de verdad.
        self.deuda = importlib.reload(importlib.import_module("deuda"))
        self.kpi = importlib.reload(importlib.import_module("kpi_ned"))
        hoy = time.strftime("%Y-%m-%d")
        viejo = time.strftime("%Y-%m-%d", time.localtime(time.time() - 34 * DIA))
        _escribe(self.tmp, "pipeline_vacuna.json", {"actualizado": viejo})
        _escribe(self.tmp, "cumbre.json", {"actualizado": hoy})
        _escribe(self.tmp, "deuda.json", {})
        _escribe(self.tmp, "seguimiento.json", {"actualizado": hoy, "hilos": [
            # 3 abiertos etiquetados, de los que 2 son accionables (reloj por `plazo` y por `gate`)
            _hilo("a", objetivo_ned="directo", dueno="tecnico",
                  siguiente_accion="hacer", plazo="2026-09-01"),
            _hilo("b", objetivo_ned="indirecto", dueno="git",
                  siguiente_accion="hacer", gate="firma_titular"),
            _hilo("c", objetivo_ned="directo", siguiente_accion="hacer"),   # sin dueño → no cuenta
            # 1 abierto sin etiquetar
            _hilo("d", dueno="prensa", siguiente_accion="hacer", plazo="2026-09-01"),
            # cerrados: fuera del cálculo entero
            _hilo("e", estado="hecho", objetivo_ned="directo"),
            _hilo("f", estado="descartado"),
        ]})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("BTP_STATE_DIR", None)

    # 1 · los números
    def test_latencia_cuenta_lo_viejo_y_lo_que_falta(self):
        lat = self.kpi.latencia()
        por_fichero = {f["fichero"]: f for f in lat["fuentes"]}
        self.assertTrue(por_fichero["pipeline_vacuna.json"]["fuera_de_presupuesto"])
        self.assertEqual(34, por_fichero["pipeline_vacuna.json"]["edad_dias"])
        self.assertFalse(por_fichero["cumbre.json"]["fuera_de_presupuesto"])
        self.assertEqual(1, lat["desfasadas"])
        self.assertEqual(34, lat["peor_edad_dias"])

    def test_fuente_que_no_existe_cuenta_como_desfasada(self):
        os.remove(os.path.join(self.tmp, "cumbre.json"))
        lat = self.kpi.latencia()
        cumbre = [f for f in lat["fuentes"] if f["fichero"] == "cumbre.json"][0]
        self.assertIsNone(cumbre["edad_dias"])
        self.assertTrue(cumbre["fuera_de_presupuesto"])   # no saber ≠ estar al día
        self.assertEqual(2, lat["desfasadas"])

    def test_cobertura_separa_etiquetado_de_accionable(self):
        cob = self.kpi.cobertura()
        self.assertEqual(4, cob["abiertos"])          # los 2 cerrados no cuentan
        self.assertEqual(3, cob["etiquetados"])
        self.assertEqual(2, cob["accionables"])       # 'c' se cae por no tener dueño
        self.assertEqual(75, cob["pct_etiquetado"])   # 3/4
        self.assertEqual(67, cob["pct_accionable"])   # 2/3

    def test_deuda_json_sin_campo_actualizado_cae_a_mtime(self):
        deuda_j = [f for f in self.kpi.latencia()["fuentes"] if f["fichero"] == "deuda.json"][0]
        self.assertEqual("mtime", deuda_j["base"])
        self.assertFalse(deuda_j["fuera_de_presupuesto"])

    # 2 · la primera pasada no grita
    def test_primer_latido_no_abre_deuda(self):
        estancado, dias, _ = self.kpi.latido_y_meta()
        self.assertFalse(estancado)
        self.assertEqual(0, dias)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "kpi_ned_snapshot.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "heartbeat", "kpi-ned.json")))
        self.assertNotIn("kpi-ned-estancado", self.deuda._cargar())

    # 3 · a los 30 días sin mover un número, escuece
    def test_estancado_abre_deuda(self):
        self.kpi.latido_y_meta(hoy_ts=time.time() - 40 * DIA)
        estancado, dias, _ = self.kpi.latido_y_meta()
        self.assertTrue(estancado)
        self.assertGreaterEqual(dias, self.kpi.DIAS_ESTANCADO)
        libro = self.deuda._cargar()
        self.assertIn("kpi-ned-estancado", libro)
        self.assertEqual("alto", libro["kpi-ned-estancado"]["impacto_ned"])

    def test_una_mejora_reinicia_el_reloj_y_no_abre_deuda(self):
        self.kpi.latido_y_meta(hoy_ts=time.time() - 40 * DIA)
        # basta con mover UNO de los tres: se pone al día la fuente desfasada
        _escribe(self.tmp, "pipeline_vacuna.json", {"actualizado": time.strftime("%Y-%m-%d")})
        estancado, dias, act = self.kpi.latido_y_meta()
        self.assertFalse(estancado)
        self.assertEqual(0, dias)
        self.assertEqual(0, act["desfasadas"])
        self.assertNotIn("kpi-ned-estancado", self.deuda._cargar())

    def test_un_bajon_no_cuenta_como_mejora_al_recuperarse(self):
        """Trinquete: se compara contra la MEJOR marca, no contra la pasada anterior.

        Si no, bastaría con empeorar un día y recuperarse al siguiente para reiniciar el reloj
        eternamente sin haber mejorado nada — una puerta trasera para no cerrar nunca nada.
        """
        _escribe(self.tmp, "pipeline_vacuna.json", {"actualizado": time.strftime("%Y-%m-%d")})
        self.kpi.latido_y_meta(hoy_ts=time.time() - 40 * DIA)     # marca: 0 desfasadas
        viejo = time.strftime("%Y-%m-%d", time.localtime(time.time() - 34 * DIA))
        _escribe(self.tmp, "pipeline_vacuna.json", {"actualizado": viejo})   # empeora a 1
        self.kpi.latido_y_meta(hoy_ts=time.time() - 20 * DIA)
        _escribe(self.tmp, "pipeline_vacuna.json", {"actualizado": time.strftime("%Y-%m-%d")})
        estancado, _, _ = self.kpi.latido_y_meta()                # vuelve a 0: NO es mejora
        self.assertTrue(estancado)


if __name__ == "__main__":
    unittest.main(verbosity=2)
