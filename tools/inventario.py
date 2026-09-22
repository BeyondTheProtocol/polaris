#!/usr/bin/env python3
"""tools/inventario.py — qué piezas de Polaris están VIVAS y cuáles no las llama nadie.

POR QUÉ (19-sep-2026, problema nº2 de `docs/lo-que-falta.md`). Hay 189 herramientas, 33 agentes
y 68 daemons, y el catálogo crece más rápido que la memoria de nadie. El auditor vigila los
charters de las cajas; el inventario no lo vigilaba nada. Síntoma real del mismo día: un modelo
local de 5 GB descargado una semana antes **al que ningún código llamaba**.

QUÉ MIDE (y qué NO). Esto no instrumenta nada ni gasta un token: cruza cuatro señales que ya
existen en el repo.

  1. 📌 referencias en código y config — ¿la nombra otra tool, un test, un agente, una regla?
  2. ⏰ daemons — ¿la ejecuta un plist de launchd?
  3. 🧾 registros del lazo — ¿aparece en el ledger del borde o en los logs de launchd?
  4. 📅 antigüedad — último commit que la tocó (`git log`)

Con eso clasifica cada herramienta:

  · `viva`      — la llama alguien que no es ella misma ni su test
  · `solo-test` — únicamente la nombra su propio test: existe para pasar, no para usarse
  · `huerfana`  — NADIE la nombra. Candidata a retirada
  · `entrada`   — nadie la nombra porque es un punto de entrada (daemon o CLI documentada)

**Clasificar no es borrar.** Una huérfana puede ser una pieza nueva a medio cablear, y por eso
la salida dice desde cuándo no se toca: decidir es de quien manda, no de este script.

Uso:
  python3 tools/inventario.py                 # resumen por estado
  python3 tools/inventario.py --huerfanas     # solo las que no llama nadie
  python3 tools/inventario.py --json
  python3 tools/inventario.py --agentes       # lo mismo para .claude/agents/
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
AGENTES = os.path.join(REPO, ".claude", "agents")
# Dónde se busca quién nombra a quién. El estado vivo NO cuenta: que una tool aparezca en un log
# no prueba que alguien la llame hoy, y meterlo daría por viva media casa.
AMBITOS = ("tools", "tests", ".claude/agents", ".claude/rules", ".claude/hooks", "evals",
           "pipeline", "docs", "CLAUDE.md", "AGENTS.md", "README.md")
SALTAR = re.compile(r"^(_|test_)")


def _ficheros(carpeta, sufijo=".py"):
    if not os.path.isdir(carpeta):
        return []
    return sorted(f for f in os.listdir(carpeta) if f.endswith(sufijo))


def _grep(termino, palabra=False):
    """Ficheros del repo que mencionan el término, sin el estado vivo ni los .git.

    `palabra=True` exige límite de palabra: hace falta para buscar el módulo desnudo
    (`cn_fetch`, sin `.py`), que es como lo nombran el panel y las reglas. Sin eso,
    `inventario.py` daba por huérfanas piezas que sí se citan — 3 de 9 en la primera pasada."""
    cmd = ["git", "-C", REPO, "grep", "-l"] + (["-w"] if palabra else ["-F"]) + [termino, "--"]
    r = subprocess.run(cmd + list(AMBITOS), capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]


def _daemons():
    """Qué scripts ejecuta launchd, leídos de los plists del repo."""
    fuera = set()
    d = os.path.join(TOOLS, "launchd")
    for f in _ficheros(d, ".plist"):
        try:
            with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                texto = fh.read()
        except OSError:
            continue
        for m in re.finditer(r"([A-Za-z0-9_]+\.(?:py|sh))", texto):
            fuera.add(m.group(1))
    return fuera


def _ultimo_commit(rel):
    r = subprocess.run(["git", "-C", REPO, "log", "-1", "--format=%as", "--", rel],
                       capture_output=True, text=True)
    return (r.stdout or "").strip() or "?"


def clasificar(nombre, por_daemon, carpeta_rel="tools"):
    """Devuelve (estado, quién la nombra). `nombre` es el fichero, p.ej. `onco.py`.

    La extensión se quita SIEMPRE, no solo a los `.py`: un agente se invoca por su nombre a
    secas (`subagent_type="diseno"`), y buscando «diseno.md» no aparece nadie. Con el corte
    solo para `.py`, 27 de los 33 agentes salían huérfanos siendo todos usados."""
    modulo = os.path.splitext(nombre)[0]
    rel = os.path.join(carpeta_rel, nombre)
    citas = set()
    for termino in (nombre, "import %s" % modulo, "tools.%s" % modulo):
        for f in _grep(termino):
            if f != rel:
                citas.add(f)
    for f in _grep(modulo, palabra=True):      # el módulo desnudo: «cn_fetch» en el panel
        if f != rel:
            citas.add(f)
    propio_test = {c for c in citas if os.path.basename(c) in ("test_%s.py" % modulo,)}
    ajenas = citas - propio_test
    if nombre in por_daemon:
        return "entrada", sorted(citas)
    if ajenas:
        return "viva", sorted(ajenas)
    if propio_test:
        return "solo-test", sorted(propio_test)
    return "huerfana", []


def inventario(carpeta=TOOLS, sufijo=".py"):
    por_daemon = _daemons()
    out = []
    for nombre in _ficheros(carpeta, sufijo):
        if SALTAR.match(nombre):
            continue
        estado, quien = clasificar(nombre, por_daemon, os.path.relpath(carpeta, REPO))
        out.append({"pieza": nombre, "estado": estado, "citada_por": quien[:6],
                    "citas": len(quien), "ultimo_commit": _ultimo_commit(
                        os.path.join(os.path.relpath(carpeta, REPO), nombre))})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Qué piezas están vivas y cuáles no llama nadie")
    p.add_argument("--huerfanas", action="store_true")
    p.add_argument("--agentes", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    filas = inventario(AGENTES, ".md") if a.agentes else inventario()
    if a.huerfanas:
        filas = [f for f in filas if f["estado"] in ("huerfana", "solo-test")]
    if a.json:
        print(json.dumps(filas, ensure_ascii=False, indent=1))
        return 0

    por_estado = {}
    for f in filas:
        por_estado.setdefault(f["estado"], []).append(f)
    print("📦 %d pieza(s) en %s" % (len(filas), "agentes" if a.agentes else "tools"))
    for estado in ("huerfana", "solo-test", "entrada", "viva"):
        grupo = por_estado.get(estado) or []
        if not grupo:
            continue
        print("\n%s %s — %d" % ({"huerfana": "🔴", "solo-test": "🟠",
                                 "entrada": "⏰", "viva": "🟢"}[estado], estado, len(grupo)))
        if estado in ("huerfana", "solo-test") or a.huerfanas:
            for f in sorted(grupo, key=lambda x: x["ultimo_commit"]):
                print("   %-34s último commit %s%s" % (
                    f["pieza"], f["ultimo_commit"],
                    ("  ← " + f["citada_por"][0]) if f["citada_por"] else ""))
    print("\nClasificar no es borrar: una huérfana puede ser algo nuevo a medio cablear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
