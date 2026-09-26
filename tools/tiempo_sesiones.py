#!/usr/bin/env python3
"""tools/tiempo_sesiones.py — ¿en qué se va el TIEMPO de reloj de las sesiones con Polaris?

Por qué existe (26-sep-2026): `usage_coste.py` mide tokens y `vigia_sesiones.py` cuántas sesiones
hay abiertas, pero nada medía el tiempo. La idea vino de X (@aiteamdigital: «de 7 horas, 2,5 eran
de espera a mis decisiones») y de la sospecha de {{TITULAR}} de que se corren demasiados tests. La
primera medición (19→26-sep) dio test_all = 8,4 % del tiempo activo: 782 pasadas, 473 bloqueando
(mediana 4 min, ~35 h-sesión); de ahí salió la regla «test_all una vez, al final».

Qué hace (DETERMINISTA, local, sin red, sin LLM):
  · Lee las transcripciones del harness (~/.claude/projects/*/*.jsonl), solo las sesiones de
    escritorio (entrypoint == "claude-desktop"); las de daemons (sdk-cli) no las mira nadie.
  · Ordena los eventos user/assistant (sin subagentes) y reparte cada hueco entre dos eventos:
      - hueco que acaba en un mensaje de {{TITULAR}} → «esperando a {{TITULAR}}» (o «ausente» si > 30 min);
      - hueco entre un tool_use y su resultado → la herramienta (tests, bash, subagentes, mcp…);
      - el resto → el modelo pensando/escribiendo.
  · Las horas son horas-SESIÓN: con sesiones en paralelo se suman, no son horas de reloj.
  · Cuenta como test solo lo que EJECUTA un test (bash tests/…, python3 tests/test_…, pytest),
    no un grep que menciona test_all.

Uso:
  python3 tools/tiempo_sesiones.py              # últimos 7 días
  python3 tools/tiempo_sesiones.py --dias 30
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import time

AUSENCIA = 30 * 60  # más de 30 min sin respuesta = no estaba, no «esperando»
RAIZ_PROYECTOS = os.path.expanduser("~/.claude/projects")

_TEST_ALL = re.compile(r"(?:^|[;&|(]\s*|\bbash\s+)\S*tests/test_all\.sh")
_TEST_SUELTO = re.compile(r"(?:^|[;&|(]\s*|\b(?:bash|python3?|\S*/python3?)\s+)\S*tests/test_[\w.-]+\.(?:py|sh)|\bpytest\b")


def _ts(d):
    return dt.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()


def es_de_titular(d):
    """Un mensaje que tecleó ella: user, no subagente, no meta, no tool_result, no <inyectado>."""
    if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip()) and not c.lstrip().startswith("<")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        t = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        return bool(t.strip()) and not t.lstrip().startswith("<")
    return False


def categoria(tool_use):
    """Bloque de tiempo al que va una herramienta."""
    n = tool_use.get("name", "")
    if n == "Bash":
        c = (tool_use.get("input") or {}).get("command", "")
        if _TEST_ALL.search(c):
            return "tests: test_all.sh"
        if _TEST_SUELTO.search(c):
            return "tests: sueltos"
        return "bash"
    if n in ("Agent", "Task"):
        return "subagentes"
    if n.startswith("mcp__"):
        return "mcp"
    if n == "AskUserQuestion":
        return "pregunta a {{TITULAR}}"
    return "otras herramientas"


def _eventos(ruta):
    ev = []
    with open(ruta, encoding="utf-8", errors="replace") as fh:
        for linea in fh:
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if "timestamp" in d and not d.get("isSidechain") and d.get("type") in ("user", "assistant"):
                ev.append(d)
    ev.sort(key=_ts)
    return ev


def medir(rutas):
    """Reparte el tiempo de las sesiones de escritorio de `rutas`. Devuelve un dict con el reparto."""
    bloques = collections.Counter()
    esperas, test_all = [], []
    sesiones = mensajes = 0
    for ruta in rutas:
        ev = _eventos(ruta)
        if not ev or ev[0].get("entrypoint") != "claude-desktop":
            continue
        sesiones += 1
        for a, b in zip(ev, ev[1:]):
            hueco = _ts(b) - _ts(a)
            if hueco <= 0:
                continue
            if es_de_titular(b):
                mensajes += 1
                if hueco > AUSENCIA:
                    bloques["ausente (>30 min)"] += hueco
                else:
                    bloques["esperando a {{TITULAR}}"] += hueco
                    esperas.append(hueco)
                continue
            cat = None
            if a.get("type") == "assistant" and b.get("type") == "user":
                for x in (a.get("message") or {}).get("content") or []:
                    if isinstance(x, dict) and x.get("type") == "tool_use":
                        cat = categoria(x)
            if cat:
                bloques[cat] += min(hueco, AUSENCIA)
                if hueco > AUSENCIA:
                    bloques["ausente (>30 min)"] += hueco - AUSENCIA
                if cat == "tests: test_all.sh":
                    test_all.append(hueco)
            elif hueco > AUSENCIA:
                bloques["ausente (>30 min)"] += hueco
            else:
                bloques["modelo pensando/escribiendo"] += hueco
    return {"sesiones": sesiones, "mensajes": mensajes, "bloques": bloques,
            "esperas": sorted(esperas), "test_all": sorted(test_all)}


def _mediana(xs):
    return xs[len(xs) // 2] if xs else 0.0


def informe(r):
    b = r["bloques"]
    activo = sum(v for k, v in b.items() if not k.startswith("ausente"))
    out = [f"sesiones de escritorio: {r['sesiones']} · mensajes de {{TITULAR}}: {r['mensajes']}",
           f"tiempo activo: {activo / 3600:.1f} h-sesión (se suman las sesiones en paralelo)"]
    for k, v in b.most_common():
        pct = "" if k.startswith("ausente") or not activo else f"{100 * v / activo:5.1f} %"
        out.append(f"  {k:30s} {v / 3600:6.1f} h  {pct}")
    if r["esperas"]:
        e = r["esperas"]
        out.append(f"espera a {{TITULAR}}: mediana {_mediana(e) / 60:.1f} min · p90 {e[int(.9 * len(e))] / 60:.1f} min")
    bloq = [x for x in r["test_all"] if x > 20]  # < 20 s = lanzado en segundo plano
    out.append(f"test_all.sh: {len(r['test_all'])} pasadas · {len(bloq)} bloqueando "
               f"(mediana {_mediana(bloq) / 60:.1f} min)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dias", type=float, default=7)
    ap.add_argument("--raiz", default=RAIZ_PROYECTOS, help=argparse.SUPPRESS)
    args = ap.parse_args()
    corte = time.time() - args.dias * 86400
    rutas = [p for p in glob.glob(os.path.join(args.raiz, "*", "*.jsonl")) if os.path.getmtime(p) >= corte]
    print(informe(medir(rutas)))


if __name__ == "__main__":
    main()
