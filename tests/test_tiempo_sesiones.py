#!/usr/bin/env python3
"""test_tiempo_sesiones.py — el reparto de tiempo de las sesiones cuadra con una sesión sintética.

Sin red, sin LLM, sin tocar ~/.claude: fabrica JSONL en un tmp con huecos conocidos.
"""
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import tiempo_sesiones as ts  # noqa: E402

_pass = 0
_fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f"  ❌ {nombre}")


def _t(seg):
    base = datetime.datetime(2026, 9, 26, 10, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(seconds=seg)).isoformat().replace("+00:00", "Z")


def _user(seg, texto, entry="claude-desktop"):
    return {"type": "user", "timestamp": _t(seg), "entrypoint": entry, "message": {"content": texto}}


def _tool(seg, tid, name, cmd=""):
    return {"type": "assistant", "timestamp": _t(seg), "entrypoint": "claude-desktop",
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": {"command": cmd}}]}}


def _result(seg, tid):
    return {"type": "user", "timestamp": _t(seg), "entrypoint": "claude-desktop",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


def _texto(seg):
    return {"type": "assistant", "timestamp": _t(seg), "entrypoint": "claude-desktop",
            "message": {"content": [{"type": "text", "text": "hecho"}]}}


def _escribe(d, nombre, eventos):
    p = os.path.join(d, nombre)
    with open(p, "w") as fh:
        for e in eventos:
            fh.write(json.dumps(e) + "\n")
    return p


tmp = tempfile.mkdtemp(prefix="tiempo_sesiones_test_")
sesion = _escribe(tmp, "a.jsonl", [
    _user(0, "haz X"),                                  # arranque
    _tool(10, "t1", "Bash", "bash tests/test_all.sh"),  # 10 s modelo
    _result(250, "t1"),                                 # 240 s test_all
    _tool(260, "t2", "Bash", "grep -n test_all x.md"),  # 10 s modelo
    _result(265, "t2"),                                 # 5 s bash (grep NO es test)
    _tool(270, "t3", "Bash", "python3 tests/test_foo.py"),
    _result(300, "t3"),                                 # 30 s test suelto
    _texto(310),                                        # 10 s modelo
    _user(430, "siguiente"),                            # 120 s esperando a {{TITULAR}}
    _texto(440),                                        # 10 s modelo
    _user(440 + 3600, "vuelvo"),                        # 1 h → ausente
])
daemon = _escribe(tmp, "b.jsonl", [_user(0, "rutina", entry="sdk-cli"), _texto(500)])

r = ts.medir([sesion, daemon])
b = r["bloques"]
check("solo cuenta la sesión de escritorio", r["sesiones"] == 1)
check("dos mensajes de {{TITULAR}} (el arranque no cierra hueco)", r["mensajes"] == 2)
check("test_all = 240 s", b["tests: test_all.sh"] == 240)
check("test suelto = 30 s", b["tests: sueltos"] == 30)
check("un grep que nombra test_all es bash, no test", b["bash"] == 5)
check("esperando a {{TITULAR}} = 120 s", b["esperando a {{TITULAR}}"] == 120)
check("hueco de 1 h = ausente", b["ausente (>30 min)"] == 3600)
check("modelo = 10+10+5+10+10 s", b["modelo pensando/escribiendo"] == 45)
check("test_all registrado para la mediana", r["test_all"] == [240])
check("un tool_result no cuenta como mensaje de {{TITULAR}}", not ts.es_de_titular(_result(1, "x")))
check("un <system-reminder> no cuenta como mensaje de {{TITULAR}}",
      not ts.es_de_titular(_user(1, "<system-reminder>x</system-reminder>")))
check("pytest es test", ts.categoria({"name": "Bash", "input": {"command": "pytest -q"}}) == "tests: sueltos")
check("cd && bash tests/test_all.sh es test_all",
      ts.categoria({"name": "Bash", "input": {"command": "cd /x && bash tests/test_all.sh"}}) == "tests: test_all.sh")
check("el informe se genera", "test_all.sh: 1 pasadas · 1 bloqueando" in ts.informe(r))

print(f"tiempo_sesiones: {_pass} OK · {_fail} fallos")
sys.exit(1 if _fail else 0)
