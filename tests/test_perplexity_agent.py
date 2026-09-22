#!/usr/bin/env python3
"""test_perplexity_agent.py — el carril de citas sigue trayendo CITAS tras la migración.

19-sep-2026. Perplexity retira `Sonar Chat Completions` el **27-sep-2026** (su propia doc: «Sonar
Chat Completions is now Agent API»). `tools/perplexity.py` llamaba a `/v1/sonar`, así que el
carril se habría caído solo en ocho días. Migrado a `/v1/agent`.

Lo que este test protege es lo que casi se pierde por el camino, probándolo en vivo:

  1. **Sin `tools:[{"type":"web_search"}]` el agente NO busca.** En la primera prueba real
     devolvió una descripción **inventada** de un ensayo clínico y cero fuentes. Un carril de
     citas sin citas no es el mismo carril más barato: es otro peor, disfrazado.
  2. **Los filtros viejos revientan** (`search_recency_filter` → 400 «unknown field»); ahora se
     traducen a `instructions`.
  3. **La respuesta va en `output[]`**, no en `choices[0]`: el texto en un item `message` y las
     fuentes en uno `search_results`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import perplexity as px  # noqa: E402

RESPUESTA_AGENTE = {
    "output": [
        {"type": "search_results", "results": [
            {"title": "Ficha del ensayo", "url": "https://clinicaltrials.gov/study/NCT0", "date": "2026-01-01"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "La respuesta."}]},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 20},
}
RESPUESTA_LEGADO = {
    "choices": [{"message": {"content": "Respuesta vieja."}}],
    "search_results": [{"title": "Fuente vieja", "url": "https://ejemplo.org"}],
}


class TestLeerSalida(unittest.TestCase):
    def test_texto_y_fuentes_del_formato_nuevo(self):
        texto, fuentes = px._leer_salida(RESPUESTA_AGENTE)
        self.assertEqual(texto, "La respuesta.")
        self.assertEqual(len(fuentes), 1)
        self.assertIn("clinicaltrials", fuentes[0]["url"])

    def test_el_formato_viejo_se_sigue_entendiendo(self):
        """Mientras `/v1/sonar` viva, una respuesta suya no puede quedar en blanco."""
        texto, fuentes = px._leer_salida(RESPUESTA_LEGADO)
        self.assertEqual(texto, "Respuesta vieja.")
        self.assertEqual(len(fuentes), 1)

    def test_una_respuesta_rara_no_revienta(self):
        texto, fuentes = px._leer_salida({"vaya": "cosa"})
        self.assertIn("vaya", texto)
        self.assertEqual(fuentes, [])


class TestLaPeticion(unittest.TestCase):
    def setUp(self):
        self.enviado = {}
        self._post = px.post
        px.post = lambda url, key, body, metodo="POST": (
            self.enviado.update({"url": url, "body": body, "metodo": metodo}) or (RESPUESTA_AGENTE, None))
        self._borde = sys.modules.get("borde")

    def tearDown(self):
        px.post = self._post

    def _chat(self, **opts):
        import borde
        b = borde.guard_cli
        borde.guard_cli = lambda *a, **k: True
        try:
            px.run_chat("pplx-test", opts.pop("model", "perplexity/sonar"), "una pregunta", opts)
        finally:
            borde.guard_cli = b
        return self.enviado

    def test_va_al_agente_y_pide_buscar(self):
        env = self._chat()
        self.assertEqual(env["url"], px.AGENT_URL)
        self.assertIn({"type": "web_search"}, env["body"]["tools"])
        self.assertGreater(env["body"]["max_steps"], 1)

    def test_no_manda_los_filtros_que_el_agente_rechaza(self):
        env = self._chat(recency="week", academic=True, domains=["pubmed.ncbi.nlm.nih.gov"])
        for muerto in ("search_recency_filter", "search_mode", "search_domain_filter"):
            self.assertNotIn(muerto, env["body"], "%s da 400 en el agente" % muerto)
        self.assertIn("week", env["body"]["instructions"])
        self.assertIn("pubmed", env["body"]["instructions"])

    def test_el_modelo_lleva_proveedor(self):
        """`sonar` a secas ya no vale: el agente exige `proveedor/modelo`."""
        self.assertEqual(self._chat(model="sonar")["body"]["model"], "perplexity/sonar")


if __name__ == "__main__":
    unittest.main()
