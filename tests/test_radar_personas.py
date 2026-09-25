#!/usr/bin/env python3
"""test_radar_personas.py — que un gemelo de criterio no pueda dejar de aprender EN SILENCIO.

POR QUÉ EXISTE (20-sep-2026). El radar de {{CONTACTO}} (`consejero-arquitectura`) estuvo
**86 días sin un solo pase**: `Radar-{{CONTACTO}}.md` no se tocó desde el cableado del 26-jun. La causa
real estaba escrita en el log ({{CONTACTO}} no tiene X; sus canales piden WebFetch, que el perfil
autónomo del muro no permite), pero el sistema **nunca lo cantó**, porque una pasada sin nada que
minar y una pasada que no se puede ejecutar **se reportaban igual** y la rutina cerraba en verde.

Detectar no basta: lo que cierra ese agujero es este test. Lo que se fija aquí:
  1. `bloqueado` NUNCA se confunde con `sin_novedad` (ni por fuente ni en el resultado global).
  2. El delta es determinista y persistido (mismo contenido → `sin_novedad`).
  3. `bloqueado_desde` sobrevive entre pases: sin eso el healthcheck no puede medir la antigüedad.
  4. El reloj de «rancio» SOLO lo mueve un pase ejecutable — si no, un gemelo con todos los
     carriles muertos parecería fresco por el mero hecho de haberlo intentado.
  5. El watchdog del healthcheck dispara de verdad sobre ese estado.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import radar_personas  # noqa: E402

CARRILES_VALIDOS = set(radar_personas.CARRIL_AUTONOMO)


def _cfg(fuentes, slug="test"):
    return {"slug": slug, "persona": "Test", "agente": "consejero-test",
            "doc_vivo": "x.md", "fuentes": fuentes}


class ConfigsDeGemelos(unittest.TestCase):
    """Las configs reales del repo: que ninguna esté rota o mienta sobre su carril."""

    def test_todas_las_configs_cargan_y_declaran_lo_minimo(self):
        slugs = radar_personas.slugs()
        self.assertTrue(slugs, "no hay configs en tools/config/personas/")
        for slug in slugs:
            cfg = radar_personas.cargar(slug)
            for campo in ("persona", "agente", "lente", "doc_vivo", "fuentes"):
                self.assertIn(campo, cfg, "%s: falta «%s»" % (slug, campo))
            self.assertTrue(cfg["fuentes"], "%s: sin fuentes" % slug)
            for f in cfg["fuentes"]:
                self.assertIn(f.get("carril"), CARRILES_VALIDOS,
                              "%s/%s: carril inválido %r" % (slug, f.get("id"), f.get("carril")))
                if f["carril"] == "local":
                    self.assertTrue(f.get("ruta"), "%s/%s: carril local sin ruta" % (slug, f.get("id")))
                if f["carril"] == "grok" and not (f.get("handle") or "").strip():
                    # un handle vacío es legítimo (aún no lo tenemos), pero tiene que decir por qué
                    self.assertTrue(f.get("pendiente"),
                                    "%s/%s: grok sin handle y sin `pendiente` que lo explique"
                                    % (slug, f.get("id")))

    def test_cada_config_apunta_a_un_agente_que_existe(self):
        for slug in radar_personas.slugs():
            cfg = radar_personas.cargar(slug)
            ficha = os.path.join(ROOT, ".claude", "agents", "%s.md" % cfg["agente"])
            self.assertTrue(os.path.exists(ficha),
                            "%s apunta a un agente inexistente: %s" % (slug, cfg["agente"]))


class DeltaYCarriles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="radarpers-")
        self.casa = os.path.join(self.tmp, "casa")
        os.makedirs(self.casa)
        os.environ["BTP_STATE_DIR"] = os.path.join(self.tmp, "state")
        os.environ["BTP_REPO"] = self.casa
        self.fuente = os.path.join(self.casa, "digest.md")
        with open(self.fuente, "w", encoding="utf-8") as f:
            f.write("criterio v1")

    def tearDown(self):
        for k in ("BTP_STATE_DIR", "BTP_REPO"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pase(self, cfg, **kw):
        resumen, estado = radar_personas.pase(cfg, **kw)
        radar_personas.escribir_estado(cfg["slug"], estado)
        return resumen

    def test_local_novedad_luego_sin_novedad_y_otra_vez_novedad_al_cambiar(self):
        cfg = _cfg([{"id": "d", "carril": "local", "ruta": "digest.md"}])
        self.assertEqual(self._pase(cfg)["resultado"], "novedad")
        self.assertEqual(self._pase(cfg)["resultado"], "sin_novedad")
        with open(self.fuente, "a", encoding="utf-8") as f:
            f.write("\ncriterio v2")
        self.assertEqual(self._pase(cfg)["resultado"], "novedad")

    def test_fuente_local_ausente_es_bloqueado_no_sin_novedad(self):
        cfg = _cfg([{"id": "d", "carril": "local", "ruta": "no-existe.md"}])
        r = self._pase(cfg)
        self.assertEqual(r["fuentes"]["d"]["resultado"], "bloqueado")
        self.assertEqual(r["resultado"], "bloqueado")

    def test_grok_sin_handle_es_bloqueado_con_motivo(self):
        cfg = _cfg([{"id": "x", "carril": "grok", "handle": "",
                     "pendiente": "lo pasa {{TITULAR}}"}])
        f = self._pase(cfg)["fuentes"]["x"]
        self.assertEqual(f["resultado"], "bloqueado")
        self.assertIn("{{TITULAR}}", f["motivo"])

    def test_grok_sin_resultado_es_bloqueado_no_sin_novedad(self):
        """El caso que se comió a {{CONTACTO}}: sin créditos/sesión, la salida vacía parecía 'nada nuevo'."""
        class _R:
            returncode, stdout, stderr = 1, "", "sin creditos"
        f = radar_personas._pase_grok({"handle": "alguien"}, {}, run=lambda *a, **k: _R())
        self.assertEqual(f["resultado"], "bloqueado")

    def test_no_respuesta_de_grok_es_bloqueado_no_novedad(self):
        """20-sep-26, en vivo con @{{CONTACTO}}: grok contestó «no se especificó ningún perfil»
        con rc=0, y el radar lo contó como NOVEDAD porque el texto era nuevo. Una no-respuesta
        que cambia de huella cada pase es una tool mintiendo éxito."""
        class _R:
            returncode, stderr = 0, ""
            stdout = ("No se especifico ningun perfil (nombre de usuario, enlace u otra "
                      "referencia) en tu consulta. Por lo tanto, no es posible acceder.")
        f = radar_personas._pase_grok({"handle": "alguien"}, {}, run=lambda *a, **k: _R())
        self.assertEqual(f["resultado"], "bloqueado")
        self.assertIn("no miro el perfil", f["motivo"])

    def test_el_prompt_nombra_el_handle_o_grok_no_mira(self):
        """`--handles` es solo el filtro de la API: si el prompt no dice @handle, el modelo
        responde que no le diste perfil. Verificado en vivo con el mismo prompt en ambos modos."""
        visto = {}
        class _R:
            returncode, stderr = 0, ""
            stdout = "1. 20 sep 2026 — un post real"
        def _run(cmd, **kw):
            visto["prompt"] = cmd[-1]
            return _R()
        radar_personas._pase_grok({"handle": "{{CONTACTO}}", "prompt": "lo ultimo"}, {}, run=_run)
        self.assertIn("@{{CONTACTO}}", visto["prompt"])

    def test_el_estado_NUNCA_persiste_el_contenido_externo(self):
        """Regla de {{TITULAR}} (20-sep-26): «que TÚ aprendas, no tienes por qué guardar nada, solo el
        conocimiento». El texto traído de fuera se destila en el momento y muere; al estado solo
        van huellas. Además, parte de ese material es curso de pago: no se archiva."""
        cfg = _cfg([{"id": "x", "carril": "grok", "handle": "alguien"}])
        class _R:
            returncode, stderr = 0, ""
            stdout = "1. 20 sep 2026 — contenido externo que NO debe quedarse en disco"
        import subprocess as _sp
        orig = _sp.run
        resumen, estado = radar_personas.pase(cfg, run=lambda *a, **k: _R())
        radar_personas.escribir_estado("test", estado)
        # al agente sí le llega, para que lo destile ahora
        self.assertIn("_texto_efimero", resumen["fuentes"]["x"])
        # al disco no llega nada del contenido
        crudo = open(radar_personas._ruta_estado("test"), encoding="utf-8").read()
        self.assertNotIn("contenido externo", crudo)
        self.assertNotIn("_texto_efimero", crudo)
        self.assertIn("huella", crudo)

    def test_carriles_nuevos_bloquean_con_instruccion_en_vez_de_fingir(self):
        # Society sin cookie: dice EXACTAMENTE qué tiene que hacer {{TITULAR}}
        f = radar_personas._pase_sesion(
            {"url": "https://x.test/feed", "secreto": "btp-no-existe-jamas"}, {})
        self.assertEqual(f["resultado"], "bloqueado")
        self.assertIn("add-generic-password", f["motivo"])
        # YouTube sin handle ni canal_id
        self.assertEqual(radar_personas._pase_yt({}, {})["resultado"], "bloqueado")
        # http sin url
        self.assertEqual(radar_personas._pase_http({}, {})["resultado"], "bloqueado")

    def test_http_no_da_novedad_con_una_pagina_de_login(self):
        """Un muro de login rinde 4 palabras: eso es BLOQUEADO, no «hay algo nuevo»."""
        class _R:
            returncode, stderr = 0, ""
            stdout = "<html><body><h1>Inicia sesión</h1></body></html>"
        f = radar_personas._pase_http({"url": "https://x.test"}, {}, run=lambda *a, **k: _R())
        self.assertEqual(f["resultado"], "bloqueado")
        self.assertIn("texto util", f["motivo"])

    def test_solo_web_es_bloqueado_global_aunque_sean_cinco_fuentes(self):
        cfg = _cfg([{"id": "ig", "carril": "web", "url": "u"},
                    {"id": "yt", "carril": "web", "url": "u"}])
        r = self._pase(cfg)
        self.assertEqual(r["resultado"], "bloqueado")

    def test_web_en_modo_interactivo_es_manual_no_bloqueado(self):
        cfg = _cfg([{"id": "ig", "carril": "web", "url": "u", "como_leer": "mira"}])
        r = self._pase(cfg, interactivo=True)
        self.assertEqual(r["fuentes"]["ig"]["resultado"], "manual")

    def test_bloqueado_desde_no_se_reinicia_en_cada_pase(self):
        cfg = _cfg([{"id": "d", "carril": "local", "ruta": "no-existe.md"}])
        primero = self._pase(cfg)["fuentes"]["d"]["bloqueado_desde"]
        segundo = self._pase(cfg)["fuentes"]["d"]["bloqueado_desde"]
        self.assertEqual(primero, segundo,
                         "si `bloqueado_desde` se reinicia, un carril muerto nunca cumple años "
                         "y el watchdog jamás dispara")

    def test_el_reloj_de_rancio_solo_lo_mueve_un_pase_ejecutable(self):
        cfg = _cfg([{"id": "ig", "carril": "web", "url": "u"}])
        self._pase(cfg)
        est = radar_personas.leer_estado("test")
        self.assertIsNotNone(est.get("ultimo_pase"))
        self.assertIsNone(est.get("ultimo_pase_ejecutable"),
                          "intentarlo y no poder NO cuenta como refrescar el gemelo")

    def test_salud_marca_rancio_y_carril_muerto_pero_no_al_que_nunca_paso(self):
        cfg_dir = os.path.join(self.tmp, "personas")
        os.makedirs(cfg_dir)
        for slug in ("viejo", "nuevo"):
            with open(os.path.join(cfg_dir, "%s.json" % slug), "w", encoding="utf-8") as f:
                json.dump({"persona": slug, "agente": "a", "lente": "l", "doc_vivo": "d",
                           "fuentes": [{"id": "d", "carril": "local", "ruta": "digest.md"}]}, f)
        orig = radar_personas.CONFIG_DIR
        radar_personas.CONFIG_DIR = cfg_dir
        try:
            radar_personas.escribir_estado("viejo", {
                "slug": "viejo", "persona": "viejo",
                "ultimo_pase": "2026-01-01T00:00:00",
                "ultimo_pase_ejecutable": "2026-01-01T00:00:00",
                "fuentes": {"d": {"resultado": "bloqueado", "motivo": "m",
                                  "bloqueado_desde": "2026-01-01T00:00:00"}},
            })
            s = radar_personas.salud()
            self.assertTrue(s["viejo"]["rancio"])
            self.assertTrue(s["viejo"]["carriles_muertos"])
            self.assertTrue(s["nuevo"]["nunca_paso"])
            self.assertFalse(s["nuevo"]["rancio"], "sin baseline no se alarma")
        finally:
            radar_personas.CONFIG_DIR = orig

    def test_el_healthcheck_dispara_sobre_un_gemelo_rancio(self):
        import healthcheck
        def _salud_falsa(**_kw):
            return {"contacto": {"persona": "{{CONTACTO}}", "agente": "consejero-arquitectura",
                               "dias_sin_pase_ejecutable": 86, "rancio": True,
                               "nunca_paso": False, "carriles_muertos":
                               [{"fuente": "society", "dias": 86, "motivo": "carril web"}]}}
        orig = radar_personas.salud
        radar_personas.salud = _salud_falsa
        try:
            alertas, info = healthcheck._check_gemelos_rancios()
        finally:
            radar_personas.salud = orig
        claves = [a[0] for a in alertas]
        self.assertIn("gemelo_contacto_rancio", claves)
        self.assertIn("gemelo_contacto_carril_muerto_society", claves)
        self.assertIn("contacto", info)


class ShimDeAndrea(unittest.TestCase):
    """`radar_contacto.py` lo referencian la allowlist de auto-mejora y el paso 7 de su ficha."""

    def test_el_shim_sigue_respondiendo(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "radar_contacto.py")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("{{CONTACTO}}", r.stdout)

    def test_el_shim_explica_lo_de_la_x_en_vez_de_omitir_en_silencio(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "radar_contacto.py"), "x"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no tiene X", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
