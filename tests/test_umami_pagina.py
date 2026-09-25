#!/usr/bin/env python3
"""tests/test_umami_pagina.py — que una métrica «de una página» no sea en realidad la del SITIO.

El fallo que lo motiva (20-sep-2026, a punto de llegar a {{TITULAR}}): para medir /lesiones se filtró
la API de Umami con `&url=/lesiones`. Ese parámetro **no existe**: la API lo ignora en silencio y
devuelve el sitio ENTERO con un 200 alegre. Salieron 5262 vistas para /biopsia-osea cuando la
página tenía 65. Comprobado en sesión: `&url=/NO-EXISTE-XYZ` devuelve exactamente las mismas
cifras que no filtrar. El parámetro bueno es `path=`.

Dar el total del sitio como si fuera el de una página es mentir con números, que es la forma de
mentira más difícil de pillar a ojo (CLAUDE.md §El muro, mantra nº 1: «dar una rebanada por el
total»). Por eso `stats_pagina()` compara filtrado contra sin filtrar y se NIEGA a devolver nada
si el filtro no recortó: fail-closed, no best-effort.

Sin red: `umami.api` se sustituye por un doble que imita las dos APIs posibles.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import umami  # noqa: E402

OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


def _doble(filtra):
    """Devuelve un api() falso. `filtra=True` imita una API que respeta `path=`;
    `filtra=False`, la que lo ignora y siempre devuelve el sitio entero."""
    TOTAL = {"pageviews": 5262, "visitors": 1418, "visits": 1622, "bounces": 496, "totaltime": 180634}
    PAGINA = {"pageviews": 65, "visitors": 59, "visits": 63, "bounces": 9, "totaltime": 126}

    def api(path, key):
        hay_filtro = "path=" in path
        return dict(PAGINA if (hay_filtro and filtra) else TOTAL)
    return api


_api_real = umami.api
try:
    # 1. API que filtra: cifras de la página, y el tiempo por visita sale de la página, no del sitio.
    umami.api = _doble(filtra=True)
    r = umami.stats_pagina("/biopsia-osea", 0, 1, key="k", wid="w")
    ok("con filtro que funciona, devuelve las cifras de la PÁGINA", r["vistas"] == 65, "-> %r" % r)
    ok("no cuela las del sitio (5262)", r["vistas"] != 5262, "-> %r" % r["vistas"])
    ok("seg_por_visita se calcula sobre la página", r["seg_por_visita"] == 2, "-> %r" % r["seg_por_visita"])

    # 2. API que IGNORA el filtro: tiene que NEGARSE, no devolver el total como si fuera la página.
    umami.api = _doble(filtra=False)
    err = ""
    try:
        r2 = umami.stats_pagina("/biopsia-osea", 0, 1, key="k", wid="w")
    except RuntimeError as e:
        err = str(e)
    ok("si el filtro no recorta, LANZA en vez de devolver el total", bool(err),
       "-> devolvió %r" % (r2 if not err else None))
    ok("y el error nombra la página y avisa de no usar las cifras", "/biopsia-osea" in err and "NO uses" in err,
       "-> %r" % err[:120])
finally:
    umami.api = _api_real

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_umami_pagina: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
