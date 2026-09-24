#!/usr/bin/env python3
"""Tests para verifica_citas.verificar_doi() con respuestas simuladas."""

import json
import sys
import os
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.verifica_citas import verificar_doi, verificar_citas


class TestVerificarDOI(unittest.TestCase):
    """Tests con respuestas simuladas para no depender de red."""

    def _mock_response(self, status=200, data=None):
        mock = Mock()
        mock.status = status
        mock.read.return_value = json.dumps(data).encode("utf-8") if data else b"{}"
        mock.__enter__ = lambda self: self
        mock.__exit__ = lambda self, *args: None
        return mock

    def test_doi_valido_en_doior(self):
        """DOI válido encontrado en doi.org (primera agencia)."""
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            mock_urlopen.return_value = self._mock_response(200, {"type": "handle"})
            resultado = verificar_doi("10.1000/abc123")
            self.assertEqual(resultado["estado"], "valido")
            self.assertEqual(resultado["agencia"], "doi.org")

    def test_doi_valido_en_crossref(self):
        """DOI válido encontrado en Crossref (doi.org da 404)."""
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            def side_effect(req, **kwargs):
                if "doi.org" in req.full_url:
                    raise HTTPError(req.full_url, 404, "Not Found", {}, None)
                elif "crossref" in req.full_url:
                    return self._mock_response(200, {"type": "work"})
                raise URLError("no deberia llegar aqui")

            mock_urlopen.side_effect = side_effect
            resultado = verificar_doi("10.1000/xyz789")
            self.assertEqual(resultado["estado"], "valido")
            self.assertEqual(resultado["agencia"], "Crossref")

    def test_doi_valido_en_datacite(self):
        """DOI válido encontrado en DataCite (las primeras dos dan 404)."""
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            def side_effect(req, **kwargs):
                if "doi.org" in req.full_url or "crossref" in req.full_url:
                    raise HTTPError(req.full_url, 404, "Not Found", {}, None)
                elif "datacite" in req.full_url:
                    return self._mock_response(200, {"type": "dataset"})
                raise URLError("no deberia llegar aqui")

            mock_urlopen.side_effect = side_effect
            resultado = verificar_doi("10.5000/dataset1")
            self.assertEqual(resultado["estado"], "valido")
            self.assertEqual(resultado["agencia"], "DataCite")

    def test_doi_no_encontrado_en_todas(self):
        """DOI que no existe en ninguna agencia → no_encontrado."""
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            def side_effect(req, **kwargs):
                raise HTTPError(req.full_url, 404, "Not Found", {}, None)

            mock_urlopen.side_effect = side_effect
            resultado = verificar_doi("10.9999/noexiste")
            self.assertEqual(resultado["estado"], "no_encontrado")
            self.assertIn("doi.org", resultado["detalle"])
            self.assertIn("Crossref", resultado["detalle"])
            self.assertIn("DataCite", resultado["detalle"])

    def test_fallo_red_en_todas(self):
        """Fallo de red en todas las agencias → indeterminado."""
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            def side_effect(req, **kwargs):
                raise URLError("network error")

            mock_urlopen.side_effect = side_effect
            resultado = verificar_doi("10.1000/ejemplo")
            self.assertEqual(resultado["estado"], "indeterminado")

    def test_mixto_404_y_error_red(self):
        """Mixto: algunas 404, otras error de red → indeterminado (fail-safe)."""
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            def side_effect(req, **kwargs):
                if "doi.org" in req.full_url:
                    raise HTTPError(req.full_url, 404, "Not Found", {}, None)
                elif "crossref" in req.full_url:
                    raise URLError("network error")
                elif "datacite" in req.full_url:
                    raise HTTPError(req.full_url, 404, "Not Found", {}, None)
                raise URLError("no deberia llegar aqui")

            mock_urlopen.side_effect = side_effect
            resultado = verificar_doi("10.1000/ejemplo2")
            self.assertEqual(resultado["estado"], "indeterminado")

    def test_doi_invalido_formato(self):
        """DOI mal formado → invalido sin llamar a red."""
        resultado = verificar_doi("not-a-doi")
        self.assertEqual(resultado["estado"], "invalido")

        resultado = verificar_doi("")
        self.assertEqual(resultado["estado"], "invalido")

    def test_doi_sin_doi(self):
        """DOI vacío → invalido."""
        resultado = verificar_doi(None)
        self.assertEqual(resultado["estado"], "invalido")


class TestVerificarCitas(unittest.TestCase):
    """Tests para verificar_citas() con lista de citas."""

    def test_lista_con_doi_valido(self):
        """Lista con DOI válido."""
        citas = [{"doi": "10.1000/abc", "titulo": "Ejemplo"}]
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            mock_urlopen.return_value = Mock(
                status=200,
                read=lambda: b'{"type": "handle"}',
                __enter__=lambda self: self,
                __exit__=lambda self, *args: None,
            )
            resultados = verificar_citas(citas)
            self.assertEqual(len(resultados), 1)
            self.assertEqual(resultados[0]["estado_doi"], "valido")

    def test_lista_sin_doi(self):
        """Cita sin DOI → estado sin_doi."""
        citas = [{"titulo": "Sin DOI"}]
        resultados = verificar_citas(citas)
        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["estado_doi"], "sin_doi")

    def test_lista_mixta(self):
        """Lista mixta: válido, no encontrado, sin DOI."""
        citas = [
            {"doi": "10.1000/valido"},
            {"doi": "10.9999/noexiste"},
            {"titulo": "sin doi"},
        ]
        with patch("tools.verifica_citas.urlopen") as mock_urlopen:
            def side_effect(req, **kwargs):
                if "valido" in req.full_url:
                    return Mock(
                        status=200,
                        read=lambda: b'{"type": "handle"}',
                        __enter__=lambda self: self,
                        __exit__=lambda self, *args: None,
                    )
                raise HTTPError(req.full_url, 404, "Not Found", {}, None)

            mock_urlopen.side_effect = side_effect
            resultados = verificar_citas(citas)
            self.assertEqual(resultados[0]["estado_doi"], "valido")
            self.assertEqual(resultados[1]["estado_doi"], "no_encontrado")
            self.assertEqual(resultados[2]["estado_doi"], "sin_doi")


if __name__ == "__main__":
    unittest.main()
