#!/usr/bin/env python3
"""Tests para tools/viajes_precios.py.

Estrategia:
  - Respuestas de SerpApi MOCKEADAS (sin llamadas reales ni gasto de crédito).
  - Valida parseo a salida mínima (lista de dicts con solo los campos útiles).
  - Valida que sin key en Llavero se lanza RuntimeError con texto claro.
  - Valida que el borde se invoca (y bloquea si el texto contiene PII/clínico).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Apunta al repo raíz para importar tools/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

# ── Fixtures mockeados (respuesta SerpApi típica) ────────────────────────────

MOCK_VUELOS_RAW = {
    "search_parameters": {"currency": "EUR"},
    "best_flights": [
        {
            "flights": [
                {
                    "airline": "Vueling",
                    "flight_number": "VY1234",
                    "departure_airport": {"time": "07:30"},
                    "arrival_airport":   {"time": "09:45"},
                }
            ],
            "total_duration": 135,
            "price": 89,
            "currency": "EUR",
        },
        {
            "flights": [
                {
                    "airline": "Iberia",
                    "flight_number": "IB5678",
                    "departure_airport": {"time": "12:00"},
                    "arrival_airport":   {"time": "14:30"},
                },
                {
                    "airline": "Iberia",
                    "flight_number": "IB5679",
                    "departure_airport": {"time": "15:00"},
                    "arrival_airport":   {"time": "16:45"},
                },
            ],
            "total_duration": 285,
            "price": 142,
            "currency": "EUR",
        },
    ],
    "other_flights": [],
}

MOCK_HOTELES_RAW = {
    "properties": [
        {
            "name": "Hotel {{CIUDAD}} Center",
            "rate_per_night": {"lowest": "€120"},
            "total_rate": {"lowest": "€360"},
            "overall_rating": 4.5,
            "link": "https://example.com/hotel1",
            "description": "Flexible cancellation available",
        },
        {
            "name": "Apartamento Limmat",
            "rate_per_night": {"lowest": "€95"},
            "total_rate": {"lowest": "€285"},
            "overall_rating": 4.2,
            "link": "https://example.com/hotel2",
            "description": "Near city center",
        },
    ]
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _with_key(fn):
    """Decorador: finge que la key existe en Llavero."""
    def wrapper(self):
        with patch("viajes_precios._load_key", return_value="FAKE_KEY"):
            fn(self)
    return wrapper


# ── Tests ────────────────────────────────────────────────────────────────────

class TestParseoVuelos(unittest.TestCase):

    @_with_key
    def test_parseo_basico(self):
        from viajes_precios import buscar_vuelos
        vuelos = buscar_vuelos("AGP", "ZRH", "2026-07-06",
                               oneway=True, _raw_override=MOCK_VUELOS_RAW)
        self.assertGreater(len(vuelos), 0)
        v = vuelos[0]
        self.assertIn("aerolinea", v)
        self.assertIn("vuelo", v)
        self.assertIn("salida", v)
        self.assertIn("llegada", v)
        self.assertIn("escalas", v)
        self.assertIn("duracion_min", v)
        self.assertIn("precio", v)
        self.assertIn("moneda", v)

    @_with_key
    def test_vuelo_directo_0_escalas(self):
        from viajes_precios import buscar_vuelos
        vuelos = buscar_vuelos("AGP", "ZRH", "2026-07-06",
                               oneway=True, _raw_override=MOCK_VUELOS_RAW)
        self.assertEqual(vuelos[0]["escalas"], 0)
        self.assertEqual(vuelos[0]["aerolinea"], "Vueling")
        self.assertEqual(vuelos[0]["precio"], 89)

    @_with_key
    def test_vuelo_con_escala(self):
        from viajes_precios import buscar_vuelos
        vuelos = buscar_vuelos("AGP", "ZRH", "2026-07-06",
                               oneway=True, _raw_override=MOCK_VUELOS_RAW)
        # segundo resultado: 2 tramos → 1 escala
        self.assertEqual(vuelos[1]["escalas"], 1)

    @_with_key
    def test_sin_resultados(self):
        from viajes_precios import buscar_vuelos
        vuelos = buscar_vuelos("AGP", "ZRH", "2026-07-06",
                               _raw_override={"best_flights": [], "other_flights": []})
        self.assertEqual(vuelos, [])

    @_with_key
    def test_max_resultados(self):
        from viajes_precios import buscar_vuelos, MAX_VUELOS
        # Genera más items que el límite
        many = {"best_flights": [MOCK_VUELOS_RAW["best_flights"][0]] * (MAX_VUELOS + 5),
                "other_flights": []}
        vuelos = buscar_vuelos("AGP", "ZRH", "2026-07-06", _raw_override=many)
        self.assertLessEqual(len(vuelos), MAX_VUELOS)


class TestParseoHoteles(unittest.TestCase):

    @_with_key
    def test_parseo_basico(self):
        from viajes_precios import buscar_hoteles
        hoteles = buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                                 _raw_override=MOCK_HOTELES_RAW)
        self.assertGreater(len(hoteles), 0)
        h = hoteles[0]
        self.assertIn("nombre", h)
        self.assertIn("precio_noche", h)
        self.assertIn("precio_total", h)
        self.assertIn("rating", h)
        self.assertIn("link", h)

    @_with_key
    def test_valores_correctos(self):
        from viajes_precios import buscar_hoteles
        hoteles = buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                                 _raw_override=MOCK_HOTELES_RAW)
        self.assertEqual(hoteles[0]["nombre"], "Hotel {{CIUDAD}} Center")
        self.assertEqual(hoteles[0]["rating"], 4.5)

    @_with_key
    def test_sin_resultados(self):
        from viajes_precios import buscar_hoteles
        hoteles = buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                                 _raw_override={"properties": []})
        self.assertEqual(hoteles, [])


class TestSinKey(unittest.TestCase):

    def test_vuelos_sin_key_da_error_claro(self):
        """Sin key en Llavero debe lanzar RuntimeError con 'btp-serpapi'."""
        from viajes_precios import buscar_vuelos
        # _load_key real (sin mock) intentará el Llavero; en el entorno de test no existe
        with patch("viajes_precios._load_key",
                   side_effect=RuntimeError("falta btp-serpapi en Llavero: "
                                            "security add-generic-password -s btp-serpapi -a key -w <TU_KEY>")):
            with self.assertRaises(RuntimeError) as ctx:
                buscar_vuelos("AGP", "ZRH", "2026-07-06", _raw_override=MOCK_VUELOS_RAW)
        self.assertIn("btp-serpapi", str(ctx.exception))

    def test_hoteles_sin_key_da_error_claro(self):
        from viajes_precios import buscar_hoteles
        with patch("viajes_precios._load_key",
                   side_effect=RuntimeError("falta btp-serpapi en Llavero")):
            with self.assertRaises(RuntimeError) as ctx:
                buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                               _raw_override=MOCK_HOTELES_RAW)
        self.assertIn("btp-serpapi", str(ctx.exception))


class TestBorde(unittest.TestCase):
    """El borde debe invocarse siempre, y bloquear si el texto es sensible."""

    @_with_key
    def test_borde_se_invoca_en_vuelos(self):
        from viajes_precios import buscar_vuelos
        with patch("viajes_precios._borde_check", return_value=True) as mock_borde:
            buscar_vuelos("AGP", "ZRH", "2026-07-06",
                          oneway=True, _raw_override=MOCK_VUELOS_RAW)
        mock_borde.assert_called_once()

    @_with_key
    def test_borde_se_invoca_en_hoteles(self):
        from viajes_precios import buscar_hoteles
        with patch("viajes_precios._borde_check", return_value=True) as mock_borde:
            buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                           _raw_override=MOCK_HOTELES_RAW)
        mock_borde.assert_called_once()

    @_with_key
    def test_borde_bloquea_si_contenido_sensible(self):
        """Si el borde devuelve False (contenido sensible), se lanza RuntimeError y NO se llama a SerpApi."""
        from viajes_precios import buscar_vuelos
        with patch("viajes_precios._borde_check", return_value=False):
            with patch("viajes_precios._get") as mock_get:
                with self.assertRaises(RuntimeError) as ctx:
                    buscar_vuelos("AGP", "ZRH", "2026-07-06",
                                  _raw_override=None)  # sin override → intentaría _get
                mock_get.assert_not_called()
        self.assertIn("Borde bloqueó", str(ctx.exception))

    @_with_key
    def test_borde_bloquea_hoteles_sensibles(self):
        from viajes_precios import buscar_hoteles
        with patch("viajes_precios._borde_check", return_value=False):
            with patch("viajes_precios._get") as mock_get:
                with self.assertRaises(RuntimeError):
                    buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                                   _raw_override=None)
                mock_get.assert_not_called()


class TestSalidaMinimaNoTieneClavesSuperfluas(unittest.TestCase):
    """La salida no debe incluir claves del JSON crudo que no necesita el consumidor."""

    CLAVES_PERMITIDAS_VUELO = {"aerolinea", "vuelo", "salida", "llegada",
                                "escalas", "duracion_min", "precio", "moneda"}
    CLAVES_PERMITIDAS_HOTEL = {"nombre", "precio_noche", "precio_total",
                                "rating", "cancelacion", "link"}

    @_with_key
    def test_vuelos_solo_campos_minimos(self):
        from viajes_precios import buscar_vuelos
        vuelos = buscar_vuelos("AGP", "ZRH", "2026-07-06",
                               _raw_override=MOCK_VUELOS_RAW)
        for v in vuelos:
            extra = set(v.keys()) - self.CLAVES_PERMITIDAS_VUELO
            self.assertEqual(extra, set(), "campo(s) superfluo(s) en vuelo: %s" % extra)

    @_with_key
    def test_hoteles_solo_campos_minimos(self):
        from viajes_precios import buscar_hoteles
        hoteles = buscar_hoteles("{{CIUDAD}}", "2026-07-06", "2026-07-09",
                                 _raw_override=MOCK_HOTELES_RAW)
        for h in hoteles:
            extra = set(h.keys()) - self.CLAVES_PERMITIDAS_HOTEL
            self.assertEqual(extra, set(), "campo(s) superfluo(s) en hotel: %s" % extra)


if __name__ == "__main__":
    ok = unittest.main(verbosity=2, exit=False)
    n_fail = len(ok.result.failures) + len(ok.result.errors)
    print("\n%s" % ("OK" if n_fail == 0 else "FAIL (%d)" % n_fail))
    sys.exit(0 if n_fail == 0 else 1)
