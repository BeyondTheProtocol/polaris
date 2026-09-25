#!/usr/bin/env python3
"""test_subagente_contexto.py — cada subagente arranca con las normas de {{TITULAR}} (P8, pieza B).

Fija el contrato de `.claude/hooks/subagente_contexto.py` con un MEMORY.md y un transcript falsos:
  · el bloque fijo sale de «Las que no se olvidan nunca», una línea por norma, recortada;
  · la consulta del recall es el ENCARGO pendiente (Agent sin tool_result); sin él, la descripción;
  · el contexto nunca pasa de 9.500 caracteres (la doc corta a 10.000);
  · A/B: el brazo depende solo del agent_id, el control no recibe nada y los dos dejan traza;
  · la traza no lleva el encargo; `--ab` cruza con la traza de gate_subagente por agent_id;
  · fail-open: entrada rara → silencio. Bypass BTP_SUBCTX_OFF=1.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="test_subctx_")
os.environ["BTP_SUBCTX_TRAZA"] = os.path.join(_TMP, "subagente_contexto.jsonl")
os.environ["BTP_MEMORY_MD"] = os.path.join(_TMP, "MEMORY.md")
for v in ("BTP_SUBCTX_OFF", "BTP_SUBCTX_AB"):
    os.environ.pop(v, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("subagente_contexto", os.path.join(ROOT, ".claude", "hooks", "subagente_contexto.py"))
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

_pass = _fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % nombre)


def escribe(ruta, texto):
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(texto)


def main():
    escribe(os.environ["BTP_MEMORY_MD"],
            "# Memoria\n\n## Temas\n- [X](x.md) — no entra\n\n## Las que no se olvidan nunca\n\n"
            "- [Codigo rojo](feedback-codigo-rojo.md) — REGLA INQUEBRANTABLE — si algo amenaza el goal, PARAR\n"
            "- [Nunca mentir](feedback-nunca-mentir.md) — " + "prefiere no lo sé " * 30 + "\n\n## Cómo se usa\n- nada\n")
    tr = os.path.join(_TMP, "madre.jsonl")
    lineas = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Agent",
                                                       "input": {"prompt": "ENCARGO-VIEJO ya resuelto"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Agent",
                                                       "input": {"prompt": "ENCARGO-SECRETO revisa ensayos CAR-T"}}]}},
    ]
    escribe(tr, "\n".join(json.dumps(x) for x in lineas) + "\n{roto\n")

    fijo = sc.bloque_fijo()
    check("bloque fijo trae las críticas", "Codigo rojo:" in fijo and "Nunca mentir:" in fijo)
    check("bloque fijo no trae otras secciones", "no entra" not in fijo)
    check("cada norma recortada a ~110", all(len(l) < 140 for l in fijo.splitlines()[1:]))
    check("encargo = el pendiente, no el resuelto", sc.encargo(tr).startswith("ENCARGO-SECRETO"))
    check("sin transcript → ''", sc.encargo(os.path.join(_TMP, "no.jsonl")) == "")

    # Recall falso para no depender de las memorias reales ni del reloj.
    consultas = []
    sc.recall = lambda q, pres: (consultas.append(q), ("R" * 50000)[:pres])[1]
    texto, origen = sc.construir("comite-medico", tr)
    check("consulta = encargo", origen == "encargo" and consultas[-1].startswith("ENCARGO-SECRETO"))
    check("nunca pasa de 9.500", len(texto) <= sc.TOPE)
    texto2, origen2 = sc.construir("tipo-que-no-existe", os.path.join(_TMP, "no.jsonl"))
    check("sin encargo ni descripción → origen ninguna", origen2 == "ninguna")

    ids = ["agent-%d" % i for i in range(40)]
    brazos = [sc.brazo(i) for i in ids]
    check("A/B reparte en los dos brazos", "inyecta" in brazos and "control" in brazos)
    check("el brazo es estable por agent_id", brazos == [sc.brazo(i) for i in ids])
    inyecta = next(i for i, b in zip(ids, brazos) if b == "inyecta")
    control = next(i for i, b in zip(ids, brazos) if b == "control")
    out = sc.procesar({"agent_id": inyecta, "agent_type": "Explore", "transcript_path": tr})
    check("brazo inyecta → additionalContext SubagentStart",
          out and out["hookSpecificOutput"]["hookEventName"] == "SubagentStart")
    check("brazo control → nada", sc.procesar({"agent_id": control, "agent_type": "Explore", "transcript_path": tr}) is None)
    otro_control = [i for i, b in zip(ids, brazos) if b == "control"][1]
    os.environ["BTP_SUBCTX_AB"] = "0"
    check("BTP_SUBCTX_AB=0 inyecta también al control",
          sc.procesar({"agent_id": otro_control, "agent_type": "Explore", "transcript_path": tr}) is not None)
    os.environ.pop("BTP_SUBCTX_AB")
    os.environ["BTP_SUBCTX_OFF"] = "1"
    check("bypass", sc.procesar({"agent_id": inyecta, "agent_type": "Explore", "transcript_path": tr}) is None)
    os.environ.pop("BTP_SUBCTX_OFF")
    check("entrada rara → silencio", sc.procesar("no es un dict") is None)

    traza = open(os.environ["BTP_SUBCTX_TRAZA"], encoding="utf-8").read()
    check("la traza no lleva el encargo", "ENCARGO" not in traza and "CAR-T" not in traza)
    check("la traza lleva los dos brazos", '"control"' in traza and '"inyecta"' in traza)

    gate = os.path.join(_TMP, "gate_subagente.jsonl")
    ahora = time.time()
    escribe(gate, "\n".join(json.dumps(x) for x in [
        {"ts": ahora, "agent_id": inyecta, "checks": []},
        {"ts": ahora, "agent_id": control, "checks": ["cita_no_respalda"]},
        {"ts": ahora, "agent_id": "desconocido", "checks": ["x"]},
    ]) + "\n")
    r = sc.ab(14, traza_gate=gate)
    check("--ab: control con aviso", r["control"]["con_aviso"] >= 1)
    check("--ab: inyecta sin aviso", r["inyecta"]["con_aviso"] == 0 and r["inyecta"]["informes"] >= 1)

    print("test_subagente_contexto: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
