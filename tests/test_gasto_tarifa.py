#!/usr/bin/env python3
"""test_gasto_tarifa.py — un modelo sin tarifa se DICE, nunca se tarifa a cero en silencio.

POR QUÉ EXISTE (24-sep-2026). `claude-opus-5-5` apareció en los transcripts sin fila en la tabla de
`coste.py`, y como `price_for` es de coincidencia exacta se tarifó a CERO. La causa raíz no era esa
fila: era que había DOS tablas de precios (`coste.DEFAULT_PRECIOS` y `gasto.PRECIOS`, con otras
claves y otro emparejamiento) más DOS matchers propios en `usage_coste.py`. Todo eso diverge solo.

El gemelo ya estaba vivo en el ledger, medido ese día: 466 de 1383 líneas de casa base iban con
`usd: null` (todas `perplexity/sonar`, que no casaba con la fila `sonar`) y `coste.py` las sumaba
como 0 con `float(r.get("usd", 0) or 0)`. El informe decía «~$0,01» sin inmutarse.

Lo que vigila esta batería:
  1. No vuelve a haber una tabla ni un matcher paralelos.
  2. El modelo por defecto de cada tool de PAGO tiene tarifa (el freno de la clase entera).
  3. Modelo desconocido → `usd: null` + `sin_tarifa: true`. Cero es «no costó»; null es «no lo sé».
  4. El lector del ledger NO suma lo que no sabe tarifar: lo cuenta y lo enseña.
  5. El tier gratis de NVIDIA se decide por PROVEEDOR, no por el nombre del modelo.
  6. El ledger es el de CASA BASE, no el del worktree desde el que se llame.
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
import coste  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _fuente(nombre):
    with open(os.path.join(TOOLS, nombre), encoding="utf-8") as f:
        return f.read()


def main():
    tabla = coste.precios()

    # --- 1. Una sola tabla y un solo matcher ---------------------------------
    src_gasto = _fuente("gasto.py")
    ok(not re.search(r"^PRECIOS\s*=", src_gasto, re.M),
       "gasto.py ya no tiene su propia tabla PRECIOS")
    ok("coste.price_for(" in src_gasto, "gasto.py resuelve el precio con coste.price_for")
    src_uc = _fuente("usage_coste.py")
    ok("_price_for = coste.price_for" in src_uc and "_usd = coste.usd" in src_uc,
       "usage_coste.py ya no tiene matcher ni suma propios")
    import usage_coste
    ok(usage_coste.DEFAULT_PRECIOS is coste.DEFAULT_PRECIOS,
       "usage_coste comparte la tabla, no una copia")
    ok(usage_coste._usd is coste.usd, "usage_coste tarifa la caché de 1 hora como coste.py")

    # --- 2. EL FRENO: el modelo por defecto de cada tool de pago tiene tarifa --
    # Se lee del CÓDIGO de cada tool, no de una lista a mano: si alguien cambia el modelo por
    # defecto a uno sin fila (que es exactamente lo que pasó con Opus 5.5), esto se pone rojo.
    for fichero, patron, tool in (
            ("grok.py", r'model = "([^"]+)"', "grok"),
            ("perplexity.py", r'DEFAULT_MODEL = "([^"]+)"', "perplexity"),
            ("gemini.py", r'DEFAULT_MODEL = "([^"]+)"', "gemini"),
            ("chatgpt.py", r'DEFAULT_MODEL = "([^"]+)"', "chatgpt"),
            ("glm.py", r'DEFAULT_MODEL = "([^"]+)"', "glm")):
        m = re.search(patron, _fuente(fichero))
        ok(m is not None, "%s: se encuentra su modelo por defecto" % fichero)
        if m:
            ok(coste.price_for(m.group(1), tabla, tool) is not None,
               "%s: el modelo por defecto (%s) tiene tarifa" % (fichero, m.group(1)))

    # --- 3. Desconocido → null + sin_tarifa, jamás 0 --------------------------
    import gasto
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BTP_GASTO_LEDGER"] = os.path.join(tmp, "l.jsonl")
        try:
            r = gasto.registrar("openrouter", "proveedor-inventado/modelo-x", 1000, 500)
            ok(r["usd"] is None, "modelo desconocido → usd null (no 0)")
            ok(r.get("sin_tarifa") is True, "modelo desconocido → la línea lo dice: sin_tarifa")
            ok(r["input_tokens"] == 1000 and r["output_tokens"] == 500,
               "los TOKENS se apuntan aunque no haya tarifa")
            c = gasto.registrar("grok", "grok-4.3", 1_000_000, 0)
            ok(c["usd"] == 3.0 and "sin_tarifa" not in c, "modelo con tarifa → usd calculado")
            p = gasto.registrar("perplexity", "perplexity/sonar", 1_000_000, 0)
            ok(p["usd"] == 1.0, "perplexity/sonar cobra (el caso de las 466 líneas nulas)")
            lineas = [json.loads(x) for x in open(os.environ["BTP_GASTO_LEDGER"], encoding="utf-8")]
            ok(len(lineas) == 3 and lineas[0]["sin_tarifa"] is True,
               "la marca sin_tarifa queda ESCRITA en el fichero")
        finally:
            os.environ.pop("BTP_GASTO_LEDGER", None)

    # --- 4. El lector no suma lo que no sabe ---------------------------------
    filas = [
        {"tool": "grok", "model": "grok-4.3", "input_tokens": 1_000_000, "output_tokens": 0,
         "usd": 3.0},
        {"tool": "perplexity", "model": "perplexity/sonar", "input_tokens": 1_000_000,
         "output_tokens": 0, "usd": None},                      # histórico: se re-tarifa
        {"tool": "openrouter", "model": "nadie/sabe", "input_tokens": 9_000_000,
         "output_tokens": 9_000_000, "usd": None, "sin_tarifa": True},
    ]
    total, n_usd, n_retar, sin = coste.ledger_usd(filas, tabla)
    ok(abs(total - 4.0) < 1e-9, "suma lo tarifable (3 + 1) y NO mete la desconocida como 0")
    ok(n_usd == 1 and n_retar == 1, "distingue la ya tarifada de la re-tarifada a posteriori")
    ok(sin == {("openrouter", "nadie/sabe"): 1}, "la desconocida se nombra, con su tool y su modelo")

    # --- 5. El gratis de NVIDIA es del PROVEEDOR, no del nombre --------------
    ok(coste.price_for("google/gemini-2.5-flash", tabla, "openrouter") is None,
       "un google/* servido por OpenRouter NO es gratis")
    ok(coste.price_for("meta/llama-3.1", tabla, "nvidia") == {
        "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
       "un meta/* servido por NVIDIA NIM sí es su tier gratis")

    # --- 6. El ledger vive en casa base, no en el worktree -------------------
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "tools"))
        os.environ["BTP_REPO"] = tmp
        try:
            ok(gasto._ledger() == os.path.join(tmp, "tools", ".gasto_ledger.jsonl"),
               "gasto.py escribe en el ledger de casa base (BTP_REPO), no en el suyo")
            ok(coste._ledger_path() == gasto._ledger(),
               "coste.py lee exactamente el mismo fichero que gasto.py escribe")
        finally:
            os.environ.pop("BTP_REPO", None)

    # --- 7. El ledger REAL: nada se suma a ciegas ----------------------------
    reales = coste.read_ledger()
    if reales:
        _t, _n, _r, sin_real = coste.ledger_usd(reales, tabla)
        desconocidos = sum(sin_real.values())
        ok(True, "ledger real leído (%d líneas, %d sin tarifa declaradas aparte)"
           % (len(reales), desconocidos))
        ok(all(coste.price_for(m, tabla, t) is None for (t, m) in sin_real),
           "lo que sale como «sin tarifa» es de verdad intarifable, no un fallo de lectura")

    print("test_gasto_tarifa: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
