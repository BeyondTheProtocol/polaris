#!/usr/bin/env python3
"""Tests para tools/elicit.py — sin llamadas reales a la API.

Estrategia:
  - Respuesta del API MOCKEADA (fixture que replica el formato real de Elicit).
  - Valida parseo al formato mínimo interno ({id, title, authors, year, doi, ...}).
  - Valida que sin clave en Llavero se lanza RuntimeError con texto claro.
  - Valida que el borde se invoca y bloquea si la query contiene contenido sensible.
  - Valida límites de max_results y campos superfluous.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

# ── Fixtures (replica el formato real del API de Elicit) ──────────────────────

MOCK_PAPERS_RAW = {
    "papers": [
        {
            "elicitId":    "ss-1001",
            "title":       "PD-L1 expression in triple-negative breast cancer",
            "authors":     ["Smith A", "Jones B", "Lee C"],
            "year":        2022,
            "abstract":    "Background: PD-L1 is expressed in approximately 20% of TNBC cases.",
            "doi":         "10.1016/j.breast.2022.01.001",
            "pmid":        "35123456",
            "venue":       "Breast Cancer Research",
            "citedByCount": 45,
            "urls":        ["https://example.com/paper1"],
        },
        {
            "elicitId":    "ss-1002",
            "title":       "[CDK4/6 inhibitors in HR+ breast cancer: a meta-analysis].",
            "authors":     ["Garcia M", "Chen X"],
            "year":        2021,
            "abstract":    None,
            "doi":         "10.1200/JCO.2021.01.002",
            "pmid":        "34111222",
            "venue":       "Journal of Clinical Oncology",
            "citedByCount": 120,
            "urls":        [],
        },
        {
            "elicitId":    "ss-1003",
            "title":       "FGFR1 amplification mechanisms",
            "authors":     ["Park S", "Kim D", "Liu Y", "Wang Z", "Brown T", "White R", "Black N"],
            "year":        2020,
            "abstract":    "FGFR1 is amplified in approximately 10% of breast cancers.",
            "doi":         "",
            "pmid":        "",
            "venue":       "Nature Cancer",
            "citedByCount": 0,
            "urls":        [],
        },
    ],
    "warnings": [],
}

MOCK_EMPTY = {"papers": [], "warnings": []}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _with_key(fn):
    """Decorador: finge que la clave de Elicit existe en el Llavero."""
    def wrapper(self):
        with patch("elicit._load_key", return_value="elk_fakekey1234"):
            fn(self)
    return wrapper


def _with_key_and_borde(fn):
    """Decorador doble: clave + borde siempre aprobado (para tests de parseo puro)."""
    def wrapper(self):
        with patch("elicit._load_key", return_value="elk_fakekey1234"):
            with patch("elicit._borde_check", return_value=True):
                fn(self)
    return wrapper


# ── Tests de parseo ───────────────────────────────────────────────────────────

class TestParseo(unittest.TestCase):

    @_with_key_and_borde
    def test_campos_minimos_presentes(self):
        from elicit import buscar
        papers = buscar("PD-L1 breast cancer", _raw_override=MOCK_PAPERS_RAW)
        self.assertEqual(len(papers), 3)
        p = papers[0]
        for campo in ("id", "title", "authors", "year", "doi", "pmid",
                      "venue", "citations", "abstract", "urls"):
            self.assertIn(campo, p, f"campo '{campo}' ausente")

    @_with_key_and_borde
    def test_valores_primer_paper(self):
        from elicit import buscar
        papers = buscar("PD-L1 breast cancer", _raw_override=MOCK_PAPERS_RAW)
        p = papers[0]
        self.assertEqual(p["id"], "ss-1001")
        self.assertIn("PD-L1", p["title"])
        self.assertEqual(p["year"], 2022)
        self.assertEqual(p["doi"], "10.1016/j.breast.2022.01.001")
        self.assertEqual(p["pmid"], "35123456")
        self.assertEqual(p["citations"], 45)

    @_with_key_and_borde
    def test_abstract_nulo_se_convierte_a_string_vacio(self):
        from elicit import buscar
        papers = buscar("CDK4/6 inhibitors", _raw_override=MOCK_PAPERS_RAW)
        self.assertEqual(papers[1]["abstract"], "")

    @_with_key_and_borde
    def test_autores_limitados_a_6(self):
        """Paper con 7 autores: el campo 'authors' debe tener máx 6."""
        from elicit import buscar
        papers = buscar("amplification mechanisms", _raw_override=MOCK_PAPERS_RAW)
        self.assertLessEqual(len(papers[2]["authors"]), 6)

    @_with_key_and_borde
    def test_titulo_limpio_sin_corchetes(self):
        """Los títulos con corchetes del API se limpian."""
        from elicit import buscar
        papers = buscar("CDK4/6 inhibitors breast cancer", _raw_override=MOCK_PAPERS_RAW)
        self.assertFalse(papers[1]["title"].startswith("["))

    @_with_key_and_borde
    def test_sin_resultados_devuelve_lista_vacia(self):
        from elicit import buscar
        papers = buscar("nonexistent query test", _raw_override=MOCK_EMPTY)
        self.assertEqual(papers, [])

    @_with_key_and_borde
    def test_campos_no_superfluous(self):
        """La salida solo contiene los campos del formato mínimo interno."""
        from elicit import buscar
        CAMPOS_OK = {"id", "title", "authors", "year", "doi", "pmid",
                     "venue", "citations", "abstract", "urls"}
        papers = buscar("amplification breast cancer", _raw_override=MOCK_PAPERS_RAW)
        for p in papers:
            extra = set(p.keys()) - CAMPOS_OK
            self.assertEqual(extra, set(), f"campos superfluous: {extra}")


# ── Tests de clave ────────────────────────────────────────────────────────────

class TestSinClave(unittest.TestCase):

    def test_sin_clave_lanza_runtime_error(self):
        from elicit import buscar
        with patch("elicit._load_key",
                   side_effect=RuntimeError("falta btp-elicit-api en Llavero")):
            with self.assertRaises(RuntimeError) as ctx:
                buscar("test query", _raw_override=None)
        self.assertIn("btp-elicit-api", str(ctx.exception))

    def test_clave_sin_prefijo_elk_lanza_error(self):
        from elicit import _load_key
        with patch("elicit.get_secret", return_value="INVALID_KEY_NO_PREFIX"):
            with self.assertRaises(RuntimeError) as ctx:
                _load_key()
        self.assertIn("elk_", str(ctx.exception))

    def test_clave_ausente_lanza_error(self):
        from elicit import _load_key
        with patch("elicit.get_secret", return_value=None):
            with self.assertRaises(RuntimeError):
                _load_key()


# ── Tests del borde ───────────────────────────────────────────────────────────

class TestBorde(unittest.TestCase):

    @_with_key
    def test_borde_se_invoca(self):
        from elicit import buscar
        with patch("elicit._borde_check", return_value=True) as mock_borde:
            buscar("PD-L1 breast cancer", _raw_override=MOCK_PAPERS_RAW)
        mock_borde.assert_called_once_with("PD-L1 breast cancer")

    @_with_key
    def test_borde_bloquea_query_sensible(self):
        """Si el borde devuelve False, se lanza RuntimeError y no se llama a la API."""
        from elicit import buscar
        with patch("elicit._borde_check", return_value=False):
            with patch("elicit._post") as mock_post:
                with self.assertRaises(RuntimeError) as ctx:
                    buscar("query sensible", _raw_override=None)
                mock_post.assert_not_called()
        self.assertIn("Borde bloqueó", str(ctx.exception))

    @_with_key
    def test_borde_bloqueado_no_llama_api_ni_con_override(self):
        """Con borde False, ni siquiera se procesa el _raw_override."""
        from elicit import buscar
        with patch("elicit._borde_check", return_value=False):
            with self.assertRaises(RuntimeError):
                buscar("sensible", _raw_override=MOCK_PAPERS_RAW)


# ── Tests de límites ──────────────────────────────────────────────────────────

class TestLimites(unittest.TestCase):

    @_with_key_and_borde
    def test_max_results_se_clampea_a_100(self):
        """max_results > 100 debe clamplearse a 100 antes de la llamada."""
        from elicit import buscar, MAX_RESULTS
        capturado = {}

        def fake_post(key, body):
            capturado["body"] = body
            return MOCK_PAPERS_RAW

        with patch("elicit._post", side_effect=fake_post):
            buscar("PD-L1 breast cancer", max_results=999)
        self.assertLessEqual(capturado["body"]["maxResults"], MAX_RESULTS)

    @_with_key_and_borde
    def test_max_results_minimo_es_1(self):
        from elicit import buscar
        capturado = {}

        def fake_post(key, body):
            capturado["body"] = body
            return MOCK_PAPERS_RAW

        with patch("elicit._post", side_effect=fake_post):
            buscar("PD-L1 breast cancer", max_results=0)
        self.assertGreaterEqual(capturado["body"]["maxResults"], 1)

    @_with_key_and_borde
    def test_search_mode_se_pasa_correctamente(self):
        from elicit import buscar
        capturado = {}

        def fake_post(key, body):
            capturado["body"] = body
            return MOCK_PAPERS_RAW

        with patch("elicit._post", side_effect=fake_post):
            buscar("PD-L1 breast cancer", search_mode="keyword")
        self.assertEqual(capturado["body"]["searchMode"], "keyword")

    @_with_key_and_borde
    def test_corpus_pubmed_se_pasa_correctamente(self):
        from elicit import buscar
        capturado = {}

        def fake_post(key, body):
            capturado["body"] = body
            return MOCK_PAPERS_RAW

        with patch("elicit._post", side_effect=fake_post):
            buscar("PD-L1 breast cancer", corpus="pubmed")
        self.assertEqual(capturado["body"]["corpus"], "pubmed")


# ── Tests de error HTTP ───────────────────────────────────────────────────────

class TestErroresHTTP(unittest.TestCase):
    """Verifica que _post convierte HTTPError en RuntimeError con mensaje claro.
    Testeamos directamente _post (que ya convierte el HTTPError) para evitar
    el problema de construir HTTPError con read() en Python 3.14."""

    def test_error_401_mensaje_claro(self):
        from elicit import _post
        import io, urllib.error
        # Construir HTTPError con fp real (necesario en Python 3.14)
        fp = io.BytesIO(b'{"error": "invalid key"}')
        e = urllib.error.HTTPError("https://elicit.com/api/v1/search", 401,
                                   "Unauthorized", {}, fp)
        with patch("urllib.request.urlopen", side_effect=e):
            with self.assertRaises(RuntimeError) as ctx:
                _post("elk_fake", {"query": "test", "maxResults": 1})
        self.assertIn("401", str(ctx.exception))

    def test_error_403_mensaje_de_plan(self):
        from elicit import _post
        import io, urllib.error
        fp = io.BytesIO(b'{"error": "plan too low"}')
        e = urllib.error.HTTPError("https://elicit.com/api/v1/search", 403,
                                   "Forbidden", {}, fp)
        with patch("urllib.request.urlopen", side_effect=e):
            with self.assertRaises(RuntimeError) as ctx:
                _post("elk_fake", {"query": "test", "maxResults": 1})
        self.assertIn("403", str(ctx.exception))
        self.assertIn("Plan", str(ctx.exception))

    def test_error_429_rate_limit(self):
        from elicit import _post
        import io, urllib.error
        fp = io.BytesIO(b'{"error": "rate limit"}')
        e = urllib.error.HTTPError("https://elicit.com/api/v1/search", 429,
                                   "Too Many Requests", {}, fp)
        with patch("urllib.request.urlopen", side_effect=e):
            with self.assertRaises(RuntimeError) as ctx:
                _post("elk_fake", {"query": "test", "maxResults": 1})
        self.assertIn("429", str(ctx.exception))


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    n_fail = len(result.result.failures) + len(result.result.errors)
    print("\n%s" % ("OK" if n_fail == 0 else f"FAIL ({n_fail})"))
    sys.exit(0 if n_fail == 0 else 1)
