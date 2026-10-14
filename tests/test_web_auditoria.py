#!/usr/bin/env python3
"""test_web_auditoria.py — la auditoría semanal solo propone lo que pasa el umbral, y dice lo que no mide.

POR QUÉ EXISTE (14-oct-2026). {{TITULAR}} pidió una auditoría semanal de la web con Umami; el comité
`diseno` fijó umbrales porque con ~300 visitantes al día casi nada es firme y «ir mejorándola»
sin umbral inventa cambios. Se fija:

  1. cada señal salta con su caso y NO salta por debajo de su mínimo;
  2. sin clave o sin datos, el resultado dice «sin datos», nunca «todo bien»;
  3. lo que la v1 no puede medir (embudos, 3D, rebote por dispositivo) va listado como no medido;
  4. el resultado se guarda por semana y el guardián no lo repite.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="webaud-")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import web_auditoria as A  # noqa: E402

HOY = datetime.date(2026, 10, 19)    # lunes


def _api(semana, previas):
    """Un Umami falso. `semana` y `previas` son dicts: visitantes, eventos {nombre: n},
    referrers {fuente: n}. Distingue la ventana por su duración en días."""
    def api(ruta, dias):
        datos = semana if dias == 7 else previas
        if "/stats" in ruta:
            return {"visitors": datos["visitantes"], "visits": datos["visitantes"]}
        if "type=event" in ruta:
            return [{"x": k, "y": v} for k, v in datos["eventos"].items()]
        if "type=referrer" in ruta:
            return [{"x": k, "y": v} for k, v in datos["referrers"].items()]
        return []
    return api


NORMAL_SEMANA = {"visitantes": 2100, "eventos": {"Donar": 30, "Contacto": 2, "Bio-link": 60},
                 "referrers": {"google.com": 400, "l.instagram.com": 380}}
NORMAL_PREVIAS = {"visitantes": 8400, "eventos": {"Donar": 120, "Contacto": 8, "Bio-link": 240},
                  "referrers": {"google.com": 1600, "l.instagram.com": 1500}}


def _claves(res):
    return {s["clave"] for s in res["senales"]}


class Senales(unittest.TestCase):

    def test_una_semana_normal_no_propone_nada(self):
        res = A.auditar(HOY, _api=_api(NORMAL_SEMANA, NORMAL_PREVIAS))
        self.assertEqual(set(), _claves(res))
        self.assertTrue(res["con_datos"])

    def test_404_salta_desde_5(self):
        sem = dict(NORMAL_SEMANA, eventos=dict(NORMAL_SEMANA["eventos"], Error404=5))
        self.assertIn("error-404", _claves(A.auditar(HOY, _api=_api(sem, NORMAL_PREVIAS))))
        sem = dict(NORMAL_SEMANA, eventos=dict(NORMAL_SEMANA["eventos"], Error404=4))
        self.assertNotIn("error-404", _claves(A.auditar(HOY, _api=_api(sem, NORMAL_PREVIAS))))

    def test_donar_cae_mas_del_30_por_ciento(self):
        sem = dict(NORMAL_SEMANA, eventos=dict(NORMAL_SEMANA["eventos"], Donar=18))
        self.assertIn("donar-cae", _claves(A.auditar(HOY, _api=_api(sem, NORMAL_PREVIAS))))

    def test_donar_con_poca_base_no_se_juzga(self):
        prev = dict(NORMAL_PREVIAS, eventos=dict(NORMAL_PREVIAS["eventos"], Donar=40))  # 10/semana
        sem = dict(NORMAL_SEMANA, eventos=dict(NORMAL_SEMANA["eventos"], Donar=1))
        self.assertNotIn("donar-cae", _claves(A.auditar(HOY, _api=_api(sem, prev))))

    def test_fuente_nueva_o_pico(self):
        sem = dict(NORMAL_SEMANA, referrers=dict(NORMAL_SEMANA["referrers"], **{"elespanol.com": 120}))
        self.assertIn("fuente-nueva", _claves(A.auditar(HOY, _api=_api(sem, NORMAL_PREVIAS))))
        sem = dict(NORMAL_SEMANA, referrers={"google.com": 1300, "l.instagram.com": 380})
        self.assertIn("fuente-pico", _claves(A.auditar(HOY, _api=_api(sem, NORMAL_PREVIAS))))

    def test_contacto_a_cero_dos_semanas_es_posible_rotura(self):
        sem = dict(NORMAL_SEMANA, eventos={"Donar": 30, "Bio-link": 60})
        self.assertNotIn("evento-a-cero", _claves(A.auditar(HOY, _api=_api(sem, NORMAL_PREVIAS))),
                         "una semana a cero aún no")
        sem2 = dict(NORMAL_SEMANA, eventos={"Donar": 30, "Bio-link": 60})
        res = A.auditar(HOY, _api=_api(sem2, NORMAL_PREVIAS), semana_anterior={"Contacto": 0})
        self.assertIn("evento-a-cero", _claves(res))


class Honestidad(unittest.TestCase):

    def test_sin_datos_no_es_todo_bien(self):
        res = A.auditar(HOY, _api=lambda ruta, dias: None)
        self.assertFalse(res["con_datos"])
        self.assertEqual([], res["senales"])
        self.assertIn("sin datos", res["resumen"])

    def test_lo_que_no_mide_se_dice(self):
        res = A.auditar(HOY, _api=_api(NORMAL_SEMANA, NORMAL_PREVIAS))
        self.assertTrue(any("embudo" in x for x in res["no_medido"]))
        self.assertTrue(any("3D" in x for x in res["no_medido"]))

    def test_se_guarda_por_semana(self):
        res = A.auditar(HOY, _api=_api(NORMAL_SEMANA, NORMAL_PREVIAS))
        ruta = A.guardar(res)
        self.assertTrue(ruta.endswith("2026-W43.json"))
        self.assertTrue(A.hecha_esta_semana(HOY))
        self.assertFalse(A.hecha_esta_semana(HOY + datetime.timedelta(days=7)))
        with open(ruta, encoding="utf-8") as f:
            self.assertEqual(res["semana"], json.load(f)["semana"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
