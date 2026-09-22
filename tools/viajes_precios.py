#!/usr/bin/env python3
"""Precios reales de vuelos y hoteles vía SerpApi (google_flights + google_hotels).
Salida mínima (pocos tokens): lista de dicts con solo los campos útiles. Sin dependencias (stdlib).

La clave se lee del Llavero: item `btp-serpapi`, cuenta `key`.
Si falta, error claro y salida no-cero (fail-closed).

Setup (1 vez):
  security add-generic-password -s btp-serpapi -a key -w <TU_KEY_SERPAPI>

Uso:
  python3 tools/viajes_precios.py vuelos --from AGP --to ZRH --date 2026-07-06
  python3 tools/viajes_precios.py vuelos --from AGP --to ZRH --date 2026-07-06 --oneway
  python3 tools/viajes_precios.py hoteles --place "{{CIUDAD}}" --in 2026-07-06 --out 2026-07-09 --pax 3

Importable (devuelve lista de dicts o lanza RuntimeError si falta la clave):
  from viajes_precios import buscar_vuelos, buscar_hoteles
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret

SERPAPI_URL = "https://serpapi.com/search"
MAX_VUELOS = 8
MAX_HOTELES = 8


# ─────────────────────────────────────────────────────────────────────────────
# Llave
# ─────────────────────────────────────────────────────────────────────────────

def _load_key():
    key = (get_secret("btp-serpapi", None, None) or "").strip()
    if not key:
        raise RuntimeError(
            "falta btp-serpapi en Llavero: "
            "security add-generic-password -s btp-serpapi -a key -w <TU_KEY>"
        )
    return key


# ─────────────────────────────────────────────────────────────────────────────
# Borde (obligatorio antes de cualquier egress)
# ─────────────────────────────────────────────────────────────────────────────

def _borde_check(texto, destino="serpapi"):
    import borde  # 🔴 BORDE no-bypassable: SerpApi = tercero NO confiable
    return borde.guard_cli(texto, destino)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

def _get(params):
    url = SERPAPI_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:400]
        except Exception:
            pass
        raise RuntimeError("SerpApi error %d: %s" % (e.code, detail)) from e
    except Exception as e:
        raise RuntimeError("Red: %s" % e) from e


# ─────────────────────────────────────────────────────────────────────────────
# Parseo mínimo — vuelos
# ─────────────────────────────────────────────────────────────────────────────

def _parse_vuelos(raw):
    """Extrae top MAX_VUELOS vuelos con solo los campos útiles."""
    resultados = []
    # SerpApi devuelve best_flights y other_flights
    for bloque in ("best_flights", "other_flights"):
        for item in raw.get(bloque) or []:
            # Cada item puede tener un array 'flights' (tramos) y precio top-level
            price = item.get("price")
            currency = item.get("currency") or raw.get("search_parameters", {}).get("currency", "EUR")
            flights = item.get("flights") or []
            if not flights:
                continue
            first = flights[0]
            last = flights[-1]
            escalas = max(0, len(flights) - 1)
            vuelo = {
                "aerolinea": first.get("airline") or first.get("airline_logo", ""),
                "vuelo": first.get("flight_number") or "",
                "salida": first.get("departure_airport", {}).get("time") or "",
                "llegada": last.get("arrival_airport", {}).get("time") or "",
                "escalas": escalas,
                "duracion_min": item.get("total_duration"),
                "precio": price,
                "moneda": currency,
            }
            resultados.append(vuelo)
            if len(resultados) >= MAX_VUELOS:
                return resultados
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# Parseo mínimo — hoteles
# ─────────────────────────────────────────────────────────────────────────────

def _parse_hoteles(raw):
    """Extrae top MAX_HOTELES hoteles con solo los campos útiles."""
    resultados = []
    for prop in raw.get("properties") or []:
        hotel = {
            "nombre": prop.get("name") or "",
            "precio_noche": prop.get("rate_per_night", {}).get("lowest") if isinstance(prop.get("rate_per_night"), dict) else prop.get("rate_per_night"),
            "precio_total": prop.get("total_rate", {}).get("lowest") if isinstance(prop.get("total_rate"), dict) else prop.get("total_rate"),
            "rating": prop.get("overall_rating"),
            "cancelacion": prop.get("check_in_time") and "flexible" in str(prop.get("description", "")).lower() or None,
            "link": prop.get("link") or "",
        }
        resultados.append(hotel)
        if len(resultados) >= MAX_HOTELES:
            return resultados
    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# API pública (importable)
# ─────────────────────────────────────────────────────────────────────────────

def buscar_vuelos(origen, destino, fecha, *, oneway=False, adultos=1, moneda="EUR", _raw_override=None):
    """Devuelve lista de dicts (vuelos) o lanza RuntimeError.
    _raw_override: inyecta una respuesta mockeada (para tests, evita llamadas reales).
    Solo manda a SerpApi: origen, destino, fecha, nº pax, moneda. NADA clínico ni PII."""
    key = _load_key()
    # El texto que sale a SerpApi son parámetros no sensibles; lo que comprobamos con
    # el borde es la consulta textual (origen+destino) para que no lleve PII/clínico.
    consulta = "%s %s %s" % (origen, destino, fecha)
    if not _borde_check(consulta, "serpapi"):
        raise RuntimeError("Borde bloqueó la consulta (contenido sensible o HALT activo)")

    if _raw_override is not None:
        return _parse_vuelos(_raw_override)

    params = {
        "engine": "google_flights",
        "departure_id": origen.upper(),
        "arrival_id": destino.upper(),
        "outbound_date": fecha,
        "type": "2" if oneway else "1",   # 1=ida+vuelta, 2=solo ida
        "adults": str(adultos),
        "currency": moneda,
        "api_key": key,
        "hl": "es",
    }
    raw = _get(params)
    return _parse_vuelos(raw)


def buscar_hoteles(lugar, fecha_in, fecha_out, *, adultos=2, moneda="EUR", _raw_override=None):
    """Devuelve lista de dicts (hoteles) o lanza RuntimeError.
    _raw_override: inyecta una respuesta mockeada (para tests, evita llamadas reales).
    Solo manda a SerpApi: lugar, fechas, nº pax, moneda. NADA clínico ni PII."""
    key = _load_key()
    consulta = "%s %s %s" % (lugar, fecha_in, fecha_out)
    if not _borde_check(consulta, "serpapi"):
        raise RuntimeError("Borde bloqueó la consulta (contenido sensible o HALT activo)")

    if _raw_override is not None:
        return _parse_hoteles(_raw_override)

    params = {
        "engine": "google_hotels",
        "q": lugar,
        "check_in_date": fecha_in,
        "check_out_date": fecha_out,
        "adults": str(adultos),
        "currency": moneda,
        "api_key": key,
        "hl": "es",
    }
    raw = _get(params)
    return _parse_hoteles(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Formato CLI legible
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_vuelos(vuelos):
    if not vuelos:
        print("(sin resultados de vuelos)")
        return
    print("Vuelos encontrados (%d):" % len(vuelos))
    for i, v in enumerate(vuelos, 1):
        escalas = "directo" if v["escalas"] == 0 else "%d escala(s)" % v["escalas"]
        dur = ""
        if v.get("duracion_min"):
            h, m = divmod(v["duracion_min"], 60)
            dur = "  %dh%02dm" % (h, m)
        precio = ("  %s %s" % (v["precio"], v["moneda"])) if v.get("precio") else ""
        print("  [%d] %s %s  %s→%s  %s%s%s" % (
            i,
            v["aerolinea"] or "?",
            v["vuelo"] or "",
            v["salida"] or "?",
            v["llegada"] or "?",
            escalas,
            dur,
            precio,
        ))


def _fmt_hoteles(hoteles):
    if not hoteles:
        print("(sin resultados de hoteles)")
        return
    print("Hoteles encontrados (%d):" % len(hoteles))
    for i, h in enumerate(hoteles, 1):
        precio = ""
        if h.get("precio_noche"):
            precio = "  %s/noche" % h["precio_noche"]
        elif h.get("precio_total"):
            precio = "  %s total" % h["precio_total"]
        rating = ("  ★%.1f" % h["rating"]) if h.get("rating") else ""
        link = ("  %s" % h["link"][:60]) if h.get("link") else ""
        print("  [%d] %s%s%s%s" % (i, h["nombre"] or "?", precio, rating, link))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _usage():
    print(
        "Uso:\n"
        "  python3 tools/viajes_precios.py vuelos --from AGP --to ZRH --date 2026-07-06 [--oneway] [--pax N] [--moneda EUR]\n"
        "  python3 tools/viajes_precios.py hoteles --place '{{CIUDAD}}' --in 2026-07-06 --out 2026-07-09 [--pax N] [--moneda EUR]"
    )


def _take(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            print("%s necesita un valor." % flag, file=sys.stderr)
            sys.exit(2)
        val = args[i + 1]
        del args[i:i + 2]
        return val
    return default


def main():
    args = list(sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        _usage()
        return 0

    subcmd = args.pop(0)

    if subcmd == "vuelos":
        origen = _take(args, "--from")
        destino = _take(args, "--to")
        fecha = _take(args, "--date")
        oneway = "--oneway" in args
        if "--oneway" in args:
            args.remove("--oneway")
        pax = int(_take(args, "--pax", "1"))
        moneda = _take(args, "--moneda", "EUR")

        if not origen or not destino or not fecha:
            print("vuelos requiere --from, --to y --date.", file=sys.stderr)
            _usage()
            return 2

        try:
            vuelos = buscar_vuelos(origen, destino, fecha, oneway=oneway, adultos=pax, moneda=moneda)
            _fmt_vuelos(vuelos)
        except RuntimeError as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1

    elif subcmd == "hoteles":
        lugar = _take(args, "--place")
        fecha_in = _take(args, "--in")
        fecha_out = _take(args, "--out")
        pax = int(_take(args, "--pax", "2"))
        moneda = _take(args, "--moneda", "EUR")

        if not lugar or not fecha_in or not fecha_out:
            print("hoteles requiere --place, --in y --out.", file=sys.stderr)
            _usage()
            return 2

        try:
            hoteles = buscar_hoteles(lugar, fecha_in, fecha_out, adultos=pax, moneda=moneda)
            _fmt_hoteles(hoteles)
        except RuntimeError as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1

    else:
        print("Subcomando desconocido: %s" % subcmd, file=sys.stderr)
        _usage()
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
