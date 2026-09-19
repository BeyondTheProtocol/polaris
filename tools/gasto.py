#!/usr/bin/env python3
"""tools/gasto.py — LEDGER de gasto de APIs de PAGO por uso.

El agujero que `coste.py` señalaba: las APIs de pago (Grok/Perplexity/OpenAI/Gemini/GLM…)
no registraban su gasto, así que el sistema gastaba A CIEGAS. Cada tool de pago llama aquí
tras una respuesta y deja una línea en `.gasto_ledger.jsonl`; `coste.py` la lee y te muestra
el gasto REAL por proveedor. Determinista, sin red, sin LLM (no gasta nada).

Formato de línea (lo que coste.py espera): {"ts","tool","model","input_tokens","output_tokens","usd"}.

Uso desde un tool de pago:
    import gasto
    gasto.registrar("grok", model, u.get("input_tokens"), u.get("output_tokens"))

`usd` se estima con PRECIOS si no se pasa. ⚠️ PRECIOS es ESTIMADO (€/millón de tokens):
ajústalo con la factura real de cada proveedor — el ledger guarda los TOKENS exactos igual.
"""
import json
import os
from datetime import datetime, timezone

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gasto_ledger.jsonl")

# € por MILLÓN de tokens (input, output). ESTIMADO — corregir con factura real.
PRECIOS = {
    "grok-4.3":        {"in": 3.0,  "out": 15.0},
    "grok":            {"in": 3.0,  "out": 15.0},
    "sonar":           {"in": 1.0,  "out": 1.0},
    "sonar-pro":       {"in": 3.0,  "out": 15.0},
    "gpt-5":           {"in": 1.25, "out": 10.0},
    "gemini-2.5-pro":  {"in": 1.25, "out": 10.0},
    "gemini-3":        {"in": 1.25, "out": 10.0},
    "glm-5.2":         {"in": 0.6,  "out": 2.2},
}


def _precio(model):
    if not model:
        return None
    if model in PRECIOS:
        return PRECIOS[model]
    for k, v in PRECIOS.items():           # prefijo (p. ej. "grok-4.3-xxx")
        if model.startswith(k):
            return v
    return None


def registrar(tool, model, input_tokens=0, output_tokens=0, usd=None):
    """Apunta una llamada de API de pago en el ledger. Best-effort: nunca rompe al llamador."""
    it = int(input_tokens or 0)
    ot = int(output_tokens or 0)
    if usd is None:
        p = _precio(model)
        usd = round((it * p["in"] + ot * p["out"]) / 1_000_000, 6) if p else None
    linea = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool, "model": model,
        "input_tokens": it, "output_tokens": ot, "usd": usd,
    }
    try:
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception:
        pass  # el coste NUNCA debe tumbar una respuesta del sistema
    return linea


if __name__ == "__main__":
    # auto-test: registra una línea sintética y confirma que se escribe y se relee
    import sys
    r = registrar("autotest", "grok-4.3", 1000, 500)
    assert r["usd"] is not None and r["input_tokens"] == 1000
    with open(LEDGER, encoding="utf-8") as f:
        ultima = json.loads(f.readlines()[-1])
    ok = ultima["tool"] == "autotest" and ("ts" in ultima)
    print("✅ gasto.py OK — ledger:", LEDGER, "· usd estimado:", r["usd"]) if ok else print("❌ fallo")
    sys.exit(0 if ok else 1)
