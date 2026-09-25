#!/usr/bin/env python3
"""test_coste_modelos.py — todo modelo que gastamos tiene precio, y el medidor ve TODOS los transcripts.

POR QUÉ EXISTE (13-sep-2026). Precondición del plan de la caja: antes de fabricar expertos hay que
poder ver lo que cuestan. Ese día el medidor mentía por dos lados a la vez:

  · `claude-opus-5`, `claude-sonnet-5` y `claude-fable-5-1` NO estaban en la tabla de precios, y
    `price_for` es de coincidencia exacta: el grueso del gasto se tarifaba a CERO. Haiku tampoco
    cobraba, porque en los transcripts va con fecha (`claude-haiku-4-5-20251001`).
  · Solo se leía `<proyecto>/*.jsonl`. Los subagentes y los agentes de Workflow viven más abajo
    (225 de 942 transcripts del último mes), así que su gasto no existía para el panel.

Y la tabla mezclaba escalas: Opus 4.8 al triple de su tarifa, Sonnet y Haiku a la suya. Se pasó toda
a la tarifa oficial de platform.claude.com/docs/en/about-claude/pricing, cotejada ese mismo día.
"""
import glob
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import coste  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    tabla = coste.precios()

    # --- 1. Tarifa oficial de los modelos 5 (cotejada el 13-sep-2026) ---
    op5, so5, fa51 = (tabla.get(m) or {} for m in ("claude-opus-5", "claude-sonnet-5",
                                                   "claude-fable-5-1"))
    ok(op5.get("input") == 5.0 and op5.get("output") == 25.0, "Opus 5 a $5/$25")
    ok(so5.get("input") == 2.0 and so5.get("output") == 10.0, "Sonnet 5 a $2/$10")
    ok(fa51.get("input") == 10.0 and fa51.get("cache_read") == 0.25,
       "Fable 5.1 a $10 con la lectura de caché a $0,25 (0,025×)")
    ok((tabla.get("claude-opus-4-8") or {}).get("input") == 5.0,
       "Opus 4.8 ya no va al triple de su tarifa")
    for m, pr in tabla.items():
        if m.startswith("claude-"):
            ok(pr.get("cache_write_1h") == 2 * pr.get("input", 0),
               "%s: la caché de 1 hora vale 2× el input" % m)

    # --- 2. El sufijo de fecha no deja un modelo sin precio ---
    ok(coste.price_for("claude-haiku-4-5-20251001", tabla) is tabla.get("claude-haiku-4-5"),
       "claude-haiku-4-5-20251001 cobra como claude-haiku-4-5")
    ok(coste.price_for("claude-inventado-9", tabla) is None, "un modelo desconocido sigue sin precio")

    # --- 3. La caché de 1 hora se tarifa a su precio y no se cuenta dos veces ---
    t = coste.empty()
    coste.add(t, {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 3_000_000,
                  "cache_creation": {"ephemeral_1h_input_tokens": 2_000_000,
                                     "ephemeral_5m_input_tokens": 1_000_000}})
    ok(t["cache_write"] == 3_000_000 and t["cache_write_1h"] == 2_000_000,
       "add separa la parte de 1 hora dentro del total")
    ok(abs(coste.usd(t, op5) - (2 * 10.0 + 1 * 6.25)) < 1e-9,
       "usd: 2M a 1 hora ($10) + 1M a 5 min ($6,25) en Opus 5")
    ok(coste.tokens_total(t) == 3_000_000, "tokens_total no suma la parte de 1 hora dos veces")
    viejo = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0}
    ok(abs(coste.usd(viejo, op5) - 5.0) < 1e-9, "un dict de 4 claves (llamadores viejos) sigue valiendo")

    # --- 4. transcripts() ve subagentes y agentes de workflow, no el diario ---
    with tempfile.TemporaryDirectory() as tmp:
        sesion = "0f0e0d0c-1111-2222-3333-444455556666"
        rutas = {
            "principal": os.path.join(tmp, sesion + ".jsonl"),
            "subagente": os.path.join(tmp, sesion, "subagents", "agent-a1.jsonl"),
            "workflow": os.path.join(tmp, sesion, "subagents", "workflows", "wf_abc", "agent-b2.jsonl"),
            "diario": os.path.join(tmp, sesion, "subagents", "workflows", "wf_abc", "journal.jsonl"),
        }
        linea = ('{"timestamp":"2026-09-13T10:00:00Z","message":{"model":"claude-opus-5",'
                 '"usage":{"input_tokens":100,"output_tokens":10}}}\n')
        for r in rutas.values():
            os.makedirs(os.path.dirname(r), exist_ok=True)
            with open(r, "w") as f:
                f.write(linea)
        vistos = set(coste.transcripts(tmp))
        ok(rutas["principal"] in vistos, "transcripts ve la sesión principal")
        ok(rutas["subagente"] in vistos, "transcripts ve los subagentes")
        ok(rutas["workflow"] in vistos, "transcripts ve los agentes de Workflow")
        ok(rutas["diario"] not in vistos, "el diario del workflow no se cuenta como gasto")
        por_modelo, _d, _s = coste.scan(sorted(vistos))
        ok(por_modelo["claude-opus-5"]["input"] == 300, "scan suma los tres transcripts con uso")

    # --- 5. Los consumidores usan la misma vista (sin copias que diverjan) ---
    for nombre in ("anatomia.py", "observatorio.py"):
        with open(os.path.join(ROOT, "tools", nombre), encoding="utf-8") as f:
            src = f.read()
        ok("coste.transcripts(" in src, "%s escanea con coste.transcripts" % nombre)
    import usage_coste
    ok(usage_coste.DEFAULT_PRECIOS is coste.DEFAULT_PRECIOS,
       "usage_coste ya no tiene su propia copia de la tabla")

    # --- 6. EL QUE HABRÍA CAZADO EL AGUJERO: todo modelo claude-* gastado en 30 días tiene precio ---
    corte = time.time() - 30 * 86400
    modelos = set()
    patron = re.compile(r'"model":"(claude-[^"]+)"')
    for proy in coste.proyectos_del_repo():
        for f in coste.transcripts(proy):
            try:
                if os.path.getmtime(f) < corte:
                    continue
                with open(f, encoding="utf-8", errors="replace") as fh:
                    modelos.update(patron.findall(fh.read()))
            except OSError:
                continue
    sin_precio = sorted(m for m in modelos if coste.price_for(m, tabla) is None)
    ok(not sin_precio, "todo modelo claude-* de los transcripts de 30 días tiene precio: faltan %s"
       % sin_precio)

    print("test_coste_modelos: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
