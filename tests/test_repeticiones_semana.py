#!/usr/bin/env python3
"""test_repeticiones_semana.py — la cifra semanal de correcciones repetidas (P8).

Aísla todo en un tmp (BTP_PROJECTS_DIR, BTP_MEMORY_DIR, BTP_REPO, BTP_GATE_LOG) y fija:
  · «ya te lo dije» de {{TITULAR}} cuenta como `explicita`; «hazlo otra vez» NO;
  · un mensaje de OTRA sesión de Claude no es {{TITULAR}} aunque diga «ya te lo dije»;
  · el transcript de un subagente (carpeta subagents/) no cuenta como {{TITULAR}};
  · `tras_subagente` se marca solo si hubo Agent/Task desde el mensaje anterior de {{TITULAR}};
  · el gate cuenta pares (semana, sesión, norma) distintos e ignora líneas sin `ts`;
  · `--guardar` escribe en tools/state de BTP_REPO y sin el texto de {{TITULAR}}.
"""
import datetime
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_repes_")
_PROJ = os.path.join(_TMP, "projects", "-Users-polaris-claudecode")
os.makedirs(os.path.join(_PROJ, "s1", "subagents"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "memory"), exist_ok=True)
os.makedirs(os.path.join(_TMP, "repo", "tools", "state"), exist_ok=True)
os.environ["BTP_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ["BTP_MEMORY_DIR"] = os.path.join(_TMP, "memory")
os.environ["BTP_REPO"] = os.path.join(_TMP, "repo")
os.environ["BTP_GATE_LOG"] = os.path.join(_TMP, "gate.jsonl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import repeticiones_semana as rs  # noqa: E402

_pass = _fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % nombre)


NOW = datetime.datetime.now(datetime.timezone.utc)
TS = (NOW - datetime.timedelta(minutes=30)).isoformat().replace("+00:00", "Z")


def user(texto):
    return {"type": "user", "entrypoint": "claude-desktop", "timestamp": TS,
            "message": {"role": "user", "content": texto}}


def agente():
    return {"type": "assistant", "timestamp": TS, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "Agent", "input": {"prompt": "x"}}]}}


def escribe(ruta, eventos):
    with open(ruta, "w", encoding="utf-8") as fh:
        for e in eventos:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def main():
    escribe(os.path.join(_PROJ, "s1.jsonl"), [
        user("Revisa el borrador del correo para la clínica, por favor."),
        agente(),
        user("Ya te lo dije: las tablas van con la hora en negrita."),      # explícita, tras subagente
        user("Cuántas veces tengo que pedirte que mires los correos antes."),  # explícita, SIN subagente
        user("El borrador no lo encuentro, hazlo otra vez."),               # NO cuenta
        user("Another Claude session sent a message: <cross-session-message from=\"x\">"
             "ya te lo dije</cross-session-message>"),                      # NO es {{TITULAR}}
    ])
    escribe(os.path.join(_PROJ, "s1", "subagents", "agent-1.jsonl"), [
        user("Ya te lo dije, esto va en tabla."),                           # subagente: NO cuenta
    ])
    ts = NOW.timestamp() - 60
    with open(os.environ["BTP_GATE_LOG"], "w", encoding="utf-8") as fh:
        for o in ({"ts": ts, "session_hash": "a", "slug": "feedback-x"},
                  {"ts": ts, "session_hash": "a", "slug": "feedback-x"},   # mismo par: 1
                  {"ts": ts, "session_hash": "b", "slug": "feedback-x"},
                  {"session_hash": "c", "slug": "feedback-y"}):            # sin ts: fuera
            fh.write(json.dumps(o) + "\n")

    res = rs.serie(semanas=2, ahora=NOW)
    ev = res["eventos"]
    textos = [e["texto"] for e in ev]
    check("dos repeticiones explícitas", len([e for e in ev if "explicita" in e["tipos"]]) == 2)
    check("«hazlo otra vez» no cuenta", not any("hazlo otra vez" in t for t in textos))
    check("mensaje de otra sesión no cuenta", not any("cross-session" in t for t in textos))
    check("el subagente no es {{TITULAR}}", not any("esto va en tabla" in t for t in textos))
    tras = {e["texto"][:12]: e["tras_subagente"] for e in ev}
    check("tras_subagente = True tras un Agent", tras.get("Ya te lo dij") is True)
    check("tras_subagente se resetea por turno", tras.get("Cuántas vece") is False)
    semana = res["semanas"][-1]
    check("cifra de la semana = 2", semana["cifra"] == 2)
    check("tras_subagente de la semana = 1", semana["tras_subagente"] == 1)
    check("gate cuenta pares distintos y salta sin ts", semana["gate"] == 2)
    check("hay 2 filas de semana", len(res["semanas"]) == 2)

    rs.main(["--guardar", "--semanas", "2"])
    salida = os.path.join(os.environ["BTP_REPO"], "tools", "state", "repeticiones_semana.json")
    check("--guardar escribe en tools/state de BTP_REPO", os.path.isfile(salida))
    if os.path.isfile(salida):
        guardado = json.load(open(salida, encoding="utf-8"))
        check("lo guardado no lleva texto de {{TITULAR}}",
              all("texto" not in e for e in guardado["eventos"]))

    print("test_repeticiones_semana: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
