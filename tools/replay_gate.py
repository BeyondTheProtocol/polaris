#!/usr/bin/env python3
"""tools/replay_gate.py — pasa las respuestas REALES de las sesiones por el gate de salida.

POR QUÉ (22-sep-26). `replay_guard.py` reproduce el tráfico de los hooks PreToolUse (muro), pero el
gate de salida es un hook Stop: lo que audita es la respuesta final, y eso nadie lo reproducía.
Resultado medido ese día: `no_puedo_falso` y `china_omitida` llevaban 0 disparos en toda su vida
mientras {{TITULAR}} seguía corrigiendo esas mismas clases. Un check que nunca salta no se distingue de
un check roto si no se prueba contra conversaciones de verdad. Esto es esa prueba.

QUÉ HACE
  1. Recorre `~/.claude/projects/*/*.jsonl` (o BTP_PROJECTS_DIR) en la ventana pedida.
  2. Parte cada sesión en TURNOS: de un mensaje de {{TITULAR}} (claude-desktop, no sidechain, no
     avisos inyectados; filtro de `cosecha_correcciones._texto_de_mensaje`) al siguiente.
  3. De cada turno saca la ÚLTIMA respuesta de texto y las tools usadas, en el mismo formato que
     `gate_salida._tools_del_turno` (nombre + command/file_path/pattern/subagent_type).
  4. Corre CADA check de `CHECKS` directamente (sin pasar por normas.json: así se mide también un
     check que aún no está registrado) y cuenta disparos, con ejemplos para etiquetar.

  --gate RUTA   compara contra otro gate_salida.py (p.ej. `git show HEAD:… > /tmp/viejo.py`).

Local, determinista, sin red. Los ejemplos se de-identifican con `borde` si está disponible.
Uso:
  python3 tools/replay_gate.py [--dias 14] [--check X] [--ejemplos 20] [--gate viejo.py] [--json]
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cosecha_correcciones as cc  # noqa: E402

GATE = os.path.join(REPO, ".claude", "hooks", "gate_salida.py")


def cargar_gate(ruta=GATE, nombre="gate_replay"):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _es_turno_de_titular(o):
    if o.get("type") != "user" or o.get("isSidechain") or o.get("isMeta"):
        return False
    if o.get("entrypoint") and o.get("entrypoint") != cc.ENTRYPOINT_HUMANO:
        return False
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
        return False
    t = cc._texto_de_mensaje(o)
    return bool(t) and not t.lstrip().startswith("[SYSTEM NOTIFICATION")


def turnos(ruta):
    """[(respuesta_final, tools)] de un transcript. Fail-soft."""
    try:
        lineas = open(ruta, encoding="utf-8", errors="replace").read().splitlines()
    except Exception:
        return []
    out, tools, ultimo, abierto = [], [], "", False
    for ln in lineas:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if _es_turno_de_titular(o):
            if abierto and ultimo:
                out.append((ultimo, tools))
            tools, ultimo, abierto = [], "", True
            continue
        if not abierto or o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        c = (o.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        textos = [x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"]
        if any(t.strip() for t in textos):
            ultimo = "\n".join(textos).strip()
        for x in c:
            if not isinstance(x, dict) or x.get("type") != "tool_use":
                continue
            tools.append(x.get("name") or "")
            entrada = x.get("input") or {}
            for campo in ("command", "file_path", "pattern", "subagent_type"):
                v = entrada.get(campo)
                if isinstance(v, str):
                    tools.append(v[:400])
    if abierto and ultimo:
        out.append((ultimo, tools))
    return out


def _deid(s):
    try:
        import borde
        s, _n = borde.de_identificar(s)
    except Exception:
        pass
    return s


def replay(dias=14, gate=None, solo=None, n_ejemplos=20):
    g = gate or cargar_gate()
    base = os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")
    corte = time.time() - dias * 86400
    n_turnos = 0
    hits = {}
    for ruta in sorted(glob.glob(os.path.join(base, "*", "*.jsonl"))):
        try:
            if os.path.getmtime(ruta) < corte:
                continue
        except OSError:
            continue
        for texto, tools in turnos(ruta):
            n_turnos += 1
            corta = len(texto) < g.MIN_CHARS
            if any(u in texto for u in g.URGENTE):
                continue
            for nombre, f in g.CHECKS.items():
                if solo and nombre not in solo:
                    continue
                if nombre == "citas_fabricadas":        # abre red: fuera del replay
                    continue
                if corta and nombre not in g.SIN_MINIMO:
                    continue
                try:
                    motivo = f(texto, tools)
                except Exception:
                    motivo = None
                if motivo:
                    h = hits.setdefault(nombre, {"n": 0, "ejemplos": []})
                    h["n"] += 1
                    if len(h["ejemplos"]) < n_ejemplos:
                        h["ejemplos"].append({"sesion": os.path.basename(ruta)[:8],
                                              "motivo": _deid(motivo[:220]),
                                              "respuesta": _deid(texto[:300])})
    return {"dias": dias, "turnos": n_turnos, "hits": hits}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dias", type=int, default=14)
    ap.add_argument("--check", action="append")
    ap.add_argument("--ejemplos", type=int, default=20)
    ap.add_argument("--gate", help="otro gate_salida.py con el que comparar")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    nuevo = replay(a.dias, None, a.check, a.ejemplos)
    viejo = replay(a.dias, cargar_gate(a.gate, "gate_viejo"), a.check, 0) if a.gate else None
    if a.json:
        print(json.dumps({"nuevo": nuevo, "viejo": viejo}, ensure_ascii=False, indent=1))
        return 0
    print("Turnos reproducidos: %d (últimos %d días)\n" % (nuevo["turnos"], a.dias))
    nombres = sorted(set(nuevo["hits"]) | set((viejo or {}).get("hits", {})))
    print("%-26s %8s %8s" % ("check", "viejo" if viejo else "", "nuevo"))
    for c in nombres:
        v = (viejo or {}).get("hits", {}).get(c, {}).get("n", 0) if viejo else ""
        print("%-26s %8s %8d" % (c, v, nuevo["hits"].get(c, {}).get("n", 0)))
    for c in (a.check or []):
        for e in nuevo["hits"].get(c, {}).get("ejemplos", []):
            print("\n[%s] %s\n   ↳ %s" % (e["sesion"], e["motivo"], e["respuesta"].replace("\n", " ")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
