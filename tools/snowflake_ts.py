#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snowflake_ts.py — Derivación determinista de la fecha/hora de un tuit de X
a partir de su identificador (Snowflake ID).

ANEXO PERICIAL. Acompaña al "Informe Forense-Legal — Caso Cuenta" (§3).
Permite que CUALQUIER perito reproduzca, sin depender de terceros ni de
servicios online, las marcas temporales de los tuits citados en el informe.

────────────────────────────────────────────────────────────────────────
FÓRMULA (no es un secreto: es la especificación pública de Snowflake de X)
────────────────────────────────────────────────────────────────────────
Cada identificador de un tuit es un "Snowflake ID" de 64 bits. Los 22 bits
inferiores codifican secuencia y máquina; los bits altos son la marca
temporal en milisegundos contados DESDE LA EPOCH DE TWITTER, no desde la
epoch Unix. Por tanto:

    timestamp_ms_unix = (ID >> 22) + 1288834974657

donde:
    ID                = identificador numérico del tuit
    >> 22             = descarta los 22 bits bajos (máquina + secuencia)
    1288834974657     = epoch de Twitter en ms  (= 2010-11-04 01:42:54.657 UTC)

La fecha/hora UTC se obtiene dividiendo ese valor entre 1000 (→ segundos
Unix) y convirtiéndolo a UTC. La operación es ENTERA y DETERMINISTA: el
mismo ID produce siempre el mismo instante, en cualquier máquina.

────────────────────────────────────────────────────────────────────────
VALIDACIÓN (coincide al segundo con el informe; verificable con --selftest)
────────────────────────────────────────────────────────────────────────
    E3  1650153690957840385 → 2023-04-23 15:04:32 UTC
    E4  1650154257679634432 → 2023-04-23 15:06:48 UTC
    E10 2067545751979164145 → 2026-06-18 09:51:35 UTC

────────────────────────────────────────────────────────────────────────
USO
────────────────────────────────────────────────────────────────────────
    python3 tools/snowflake_ts.py 1650154257679634432
    python3 tools/snowflake_ts.py https://x.com/Jodetemazo/status/1650154257679634432
    python3 tools/snowflake_ts.py 1650153690957840385 1650154257679634432
    python3 tools/snowflake_ts.py --selftest

Acepta uno o varios argumentos, cada uno un ID numérico o una URL del tipo
x.com/<usuario>/status/<id> (también twitter.com, con o sin https://, con
parámetros ?... tras el id). Sin dependencias externas (solo stdlib).

Salida por cada entrada:
    <id> → YYYY-MM-DD HH:MM:SS UTC (HH:MM:SS CEST)
La hora local de España (CEST en verano / CET en invierno) se añade entre
paréntesis como conveniencia; la marca PROBATORIA es la UTC.
"""

import re
import sys
from datetime import datetime, timezone, timedelta

# Epoch de Twitter en milisegundos Unix: 2010-11-04T01:42:54.657Z
TWITTER_EPOCH_MS = 1288834974657

# Casos del informe forense (§3) usados por --selftest.
KNOWN_CASES = [
    ("E3", 1650153690957840385, "2023-04-23 15:04:32 UTC"),
    ("E4", 1650154257679634432, "2023-04-23 15:06:48 UTC"),
    ("E10", 2067545751979164145, "2026-06-18 09:51:35 UTC"),
]

# Captura el id de una URL .../status/<id> (o /statuses/<id>) o un id suelto.
_STATUS_RE = re.compile(r"(?:status(?:es)?/)?(\d{5,25})(?:[/?#].*)?$", re.IGNORECASE)


def parse_id(arg):
    """Extrae el Snowflake ID (int) de un id suelto o de una URL de X.

    Lanza ValueError si el argumento no contiene un id de tuit plausible.
    """
    s = arg.strip()
    # Si es puramente numérico, úsalo directo.
    if s.isdigit():
        return int(s)
    # Si es una URL/cadena, busca el último componente .../status/<id>.
    m = _STATUS_RE.search(s)
    if m:
        return int(m.group(1))
    raise ValueError(f"no se reconoce un ID de tuit en: {arg!r}")


def snowflake_to_utc(tweet_id):
    """Convierte un Snowflake ID (int) a datetime UTC (aware).

    timestamp_ms_unix = (id >> 22) + TWITTER_EPOCH_MS
    """
    if tweet_id <= 0:
        raise ValueError(f"ID no válido (debe ser un entero positivo): {tweet_id}")
    ms = (tweet_id >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def to_spain_local(dt_utc):
    """Devuelve (datetime_local, abreviatura) en hora de España.

    Usa la base de zonas horarias (zoneinfo, stdlib) para aplicar CEST/CET
    correctamente según DST. Si no estuviera disponible, cae a un offset
    fijo UTC+2 (CEST), que es el correcto para los tuits del informe (verano).
    """
    try:
        from zoneinfo import ZoneInfo
        local = dt_utc.astimezone(ZoneInfo("Europe/Madrid"))
        return local, local.tzname()  # "CEST" o "CET"
    except Exception:
        local = dt_utc.astimezone(timezone(timedelta(hours=2)))
        return local, "CEST"


def format_line(tweet_id):
    """Formatea una línea de salida para un id ya parseado."""
    dt_utc = snowflake_to_utc(tweet_id)
    local, tzname = to_spain_local(dt_utc)
    utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    local_str = local.strftime(f"%H:%M:%S {tzname}")
    return f"{tweet_id} → {utc_str} ({local_str})"


def selftest():
    """Reproduce los casos del informe y comprueba que cuadran al segundo."""
    ok = True
    for label, tid, expected in KNOWN_CASES:
        got = snowflake_to_utc(tid).strftime("%Y-%m-%d %H:%M:%S UTC")
        match = "OK" if got == expected else "FALLA"
        if got != expected:
            ok = False
        print(f"[{match}] {label:<3} {tid} → {got}  (esperado: {expected})")
    print("\nFórmula: ((ID >> 22) + %d) ms  ·  epoch Twitter = %d" %
          (TWITTER_EPOCH_MS, TWITTER_EPOCH_MS))
    print("Resultado:", "TODOS COINCIDEN ✓" if ok else "DISCREPANCIA ✗")
    return 0 if ok else 1


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args[0] in ("--selftest", "--self-test", "--test"):
        return selftest()

    exit_code = 0
    for arg in args:
        try:
            tid = parse_id(arg)
            print(format_line(tid))
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
