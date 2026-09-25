#!/usr/bin/env python3
"""tools/gasto.py — LEDGER de gasto de APIs de PAGO por uso.

El agujero que `coste.py` señalaba: las APIs de pago (Grok/Perplexity/OpenAI/Gemini/GLM…)
no registraban su gasto, así que el sistema gastaba A CIEGAS. Cada tool de pago llama aquí
tras una respuesta y deja una línea en `.gasto_ledger.jsonl`; `coste.py` la lee y te muestra
el gasto REAL por proveedor. Determinista, sin red, sin LLM (no gasta nada).

Formato de línea: {"ts","tool","model","input_tokens","output_tokens","usd"} y, cuando el
modelo no tiene tarifa, `"usd": null` MÁS `"sin_tarifa": true`.

Uso desde un tool de pago:
    import gasto
    gasto.registrar("grok", model, u.get("input_tokens"), u.get("output_tokens"))

UNA SOLA TABLA DE PRECIOS (24-sep-2026). Aquí había una tabla `PRECIOS` propia, con otras
claves (`in`/`out`), etiquetada en € y con coincidencia por PREFIJO — un espejo de la de
`coste.py` que divergía sola. Eso es exactamente lo que produjo el agujero de `claude-opus-5-5`
(modelo sin fila → tarifado a CERO), y aquí el gemelo ya estaba VIVO: `perplexity.py` apunta
`perplexity/sonar`, que no casaba con la fila `sonar`, así que la línea salía con `usd: null`
y `coste.py` la sumaba como 0 sin decir nada. Ahora la tabla es la de `coste.py` y punto
(mismo camino que tomó `usage_coste.py` el 13-sep-2026).

Modelo sin tarifa → `usd: null` + `sin_tarifa: true`, NUNCA 0. Cero significa «no costó»;
null significa «no lo sé». `tests/test_gasto_tarifa.py` vigila las dos cosas.

Y lo que se sabe tarifar se AVISA a `cost_guard` (`via="api"`), que es quien gatea los topes: sin
eso, las seis tools de pago gastaban fuera del contador. Avisa, no gatea — ver `_a_cost_guard`.
"""
import json
import os
import sys
from datetime import datetime, timezone

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))


def _ledger():
    """Ruta del ledger, SIEMPRE la de casa base (patrón canónico del repo, ver observabilidad.py).

    24-sep-2026: el ledger está en `.gitignore`, así que es estado vivo — y esto resolvía la ruta
    con `__file__`. Una tool de pago llamada desde un worktree (que es lo NORMAL: CLAUDE.md ordena
    aislar cada sesión) apuntaba su gasto en el ledger DE ESE worktree, que no se fusiona y
    desaparece al podarlo: gasto real que casa base no ve jamás. Misma clase que el bug de
    `seguimiento.py` del 24/6 ([[feedback-estado-vivo-resuelve-casa-base]]).
    Si la casa base no existe (CI portable en Linux), se cae al directorio local en vez de fallar."""
    base = os.environ.get("BTP_GASTO_LEDGER")
    if base:
        return base
    try:
        sys.path.insert(0, TOOLS_DIR)
        from _casa import casa_base, es_proceso_de_test
        # Un test nunca escribe en el ledger real (25-sep-2026: test_perplexity_agent dejaba ahí su
        # simulacro). Mismo freno de clase que `cost_guard`.
        if es_proceso_de_test():
            import tempfile
            return os.path.join(tempfile.gettempdir(), "btp-test-gasto-%d.jsonl" % os.getuid())
        raiz = casa_base()
    except Exception:
        raiz = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
    casa = os.path.join(raiz, "tools")
    return os.path.join(casa if os.path.isdir(casa) else TOOLS_DIR, ".gasto_ledger.jsonl")


LEDGER = _ledger()


def _tabla():
    """La tabla de `coste.py` (con el override de `.precios.json` ya aplicado), o None.

    Import perezoso y defensivo: esto corre en caliente, después de cada respuesta de una API
    de pago, y `registrar` no puede tumbar al llamador. `coste` es stdlib pura y no escanea
    nada al importarse, así que el coste del import es el de leer un .py."""
    try:
        if TOOLS_DIR not in sys.path:
            sys.path.insert(0, TOOLS_DIR)
        import coste
        return coste.precios(), coste
    except Exception:
        return None, None


def _precio(model, tool=None):
    """Dict de precios $/millón del modelo, o None si no hay tarifa. Nunca lanza.

    `tool` importa: el tier gratis de NVIDIA NIM se decide por PROVEEDOR. Sin pasarlo,
    un `google/gemini-2.5-flash` servido por OpenRouter (que se paga) se tarifaría gratis."""
    if not model:
        return None
    tabla, coste = _tabla()
    if not tabla:
        return None
    try:
        return coste.price_for(model, tabla, tool)
    except Exception:
        return None


def _a_cost_guard(usd, tool):
    """Apunta el gasto en el contador que gatean los topes. Best-effort, y NUNCA bloquea.

    24-sep-2026, deuda `cost-guard-ciego-apis-de-pago`: verificado con grep que ninguna de las seis
    tools de pago (grok, perplexity, gemini, chatgpt, glm, openrouter) llamaba a `cost_guard`. Su
    gasto vivía solo en este ledger, que los topes diario y mensual NO leen: se tarifaban a cero
    POR CONSTRUCCIÓN, y se podía pasar del tope sin que sonara nada.

    AVISA, no GATEA, y es deliberado: `registrar` se llama DESPUÉS de una respuesta que ya se pagó,
    así que frenar aquí no ahorraría ese dólar y sí podría cortar trabajo a mitad. Poner
    `check_before_job` en las seis tools es otra decisión, con el filtro de «el coste nunca corta
    el camino a NED» delante.

    Se pasa un float y no un dict a propósito: aquí `usd` SIEMPRE es un número calculado, no un
    campo que pueda faltar. Ese es justo el matiz que `tools/ia.py:387` se salta (hace `or 0.0`
    sobre un campo ausente y esquiva el coste pesimista → deuda `ia-py-esquiva-el-coste-pesimista`).
    Una llamada SIN tarifa no llega hasta aquí: cero significaría «no costó», y no lo sabemos."""
    try:
        if TOOLS_DIR not in sys.path:
            sys.path.insert(0, TOOLS_DIR)
        import cost_guard
        cost_guard.add_cost(float(usd), job_id="api-%s" % (tool or "?"), via="api")
    except Exception:
        pass  # el contador NUNCA debe tumbar una respuesta del sistema


def registrar(tool, model, input_tokens=0, output_tokens=0, usd=None):
    """Apunta una llamada de API de pago en el ledger. Best-effort: nunca rompe al llamador."""
    it = int(input_tokens or 0)
    ot = int(output_tokens or 0)
    sin_tarifa = False
    if usd is None:
        p = _precio(model, tool)
        if p:
            usd = round((it * p.get("input", 0) + ot * p.get("output", 0)) / 1_000_000, 6)
        else:
            usd, sin_tarifa = None, True
    linea = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool, "model": model,
        "input_tokens": it, "output_tokens": ot, "usd": usd,
    }
    # Quién llamó, si lo dice el entorno: `enruta.probar` lanza sus pings con BTP_ORIGEN=ping,
    # para que el contador por carril separe «Responde solo: OK» de contenido real.
    if os.environ.get("BTP_ORIGEN"):
        linea["origen"] = os.environ["BTP_ORIGEN"][:40]
    if sin_tarifa:
        # Explícito en el fichero: quien lea el ledger sabe que este gasto existió y no se
        # pudo tarifar, en vez de verlo desaparecer dentro de un total.
        linea["sin_tarifa"] = True
    if isinstance(usd, (int, float)) and usd > 0:
        _a_cost_guard(usd, tool)
    try:
        destino = _ledger()          # se re-resuelve por si BTP_* cambió (tests)
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "a", encoding="utf-8") as f:
            f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception:
        pass  # el coste NUNCA debe tumbar una respuesta del sistema
    return linea


if __name__ == "__main__":
    # auto-test: registra una línea sintética y confirma que se escribe y se relee
    r = registrar("autotest", "grok-4.3", 1000, 500)
    assert r["usd"] is not None and r["input_tokens"] == 1000
    with open(LEDGER, encoding="utf-8") as f:
        ultima = json.loads(f.readlines()[-1])
    ok = ultima["tool"] == "autotest" and ("ts" in ultima)
    print("✅ gasto.py OK — ledger:", LEDGER, "· usd estimado:", r["usd"]) if ok else print("❌ fallo")
    sys.exit(0 if ok else 1)
