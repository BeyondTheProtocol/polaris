#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""precompact_traspaso.py — PreCompact: vuelca el estado de la sesión ANTES de compactar.

EL PROBLEMA (24-sep-26)
-----------------------
Al compactar se pierde lo que inyectó SessionStart, las decisiones ya cerradas y el paso en el
que íbamos; después se vuelven a preguntar cosas que {{TITULAR}} ya contestó. `settings.json` tenía
SessionStart, UserPromptSubmit, Stop, PreToolUse y PostToolUse, pero no PreCompact.

CONTRATO (verificado el 24-sep-26 en code.claude.com/docs/en/hooks.md)
----------------------------------------------------------------------
Entrada: `session_id`, `transcript_path`, `cwd`, `trigger` (manual|auto), `custom_instructions`.
PreCompact puede BLOQUEAR (exit 2 / decision:block) y descarta `systemMessage`; no admite
`additionalContext`. Por eso aquí solo se ESCRIBE (tools/continuity.py → traspaso_guardar) y quien
lo devuelve al contexto es `session_start.sh` cuando `source == "compact"`.

QUÉ SACA, sin LLM
-----------------
Rama, worktree y lo sin commitear; ficheros tocados por Write/Edit; el último «Paso N de M» que
escribí; decisiones cerradas (planes aprobados y respuestas a AskUserQuestion); los últimos
prompts humanos recortados; y un prompt de continuación. Las decisiones en texto libre («vale,
WES») NO se detectan: haría falta un LLM y aquí no hay.

NUNCA bloquea la compactación: cualquier error → exit 0 sin salida. Bypass BTP_TRASPASO_OFF=1.
"""
import json
import os
import re
import subprocess
import sys

# Código de ESTE árbol (el de al lado); el almacenamiento lo resuelve continuity contra casa base.
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")

COLA_BYTES = 4 * 1024 * 1024
MAX_PROMPTS = 3
MAX_PROMPT_CHARS = 300
MAX_FICHEROS = 25
RE_PASO = re.compile(r"\b[Pp]aso\s+(\d+)\s*(?:de|/)\s*(\d+)")
RE_SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
RE_PLAN_RUTA = re.compile(r"saved to:\s*(\S+\.md)")
RE_TITULO = re.compile(r"^#\s+(.+)$", re.M)


def _lineas_cola(ruta):
    with open(ruta, "rb") as f:
        f.seek(0, 2)
        tam = f.tell()
        f.seek(max(0, tam - COLA_BYTES))
        datos = f.read()
    if tam > COLA_BYTES:
        datos = datos.split(b"\n", 1)[-1]      # la primera línea puede venir cortada
    for l in datos.splitlines():
        try:
            yield json.loads(l)
        except ValueError:
            continue


def _texto(contenido):
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        partes = []
        for b in contenido:
            if isinstance(b, dict) and b.get("type") == "text":
                partes.append(b.get("text", ""))
            elif isinstance(b, str):
                partes.append(b)
        return "\n".join(partes)
    return ""


def extraer(transcript_path):
    """Recorre la cola del transcript. Devuelve un dict con lo que se puede saber sin LLM."""
    r = {"ficheros": [], "paso": None, "planes": [], "respuestas": [], "prompts": []}
    usos = {}                                   # tool_use_id → nombre
    vistos = set()
    for d in _lineas_cola(transcript_path):
        tipo = d.get("type")
        msg = d.get("message") or {}
        cont = msg.get("content")
        if tipo == "assistant" and isinstance(cont, list):
            for b in cont:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    for m in RE_PASO.finditer(b.get("text", "")):
                        r["paso"] = "%s de %s" % (m.group(1), m.group(2))
                elif b.get("type") == "tool_use":
                    usos[b.get("id")] = b.get("name")
                    inp = b.get("input") or {}
                    if b.get("name") in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                        p = inp.get("file_path") or inp.get("notebook_path")
                        if p and p not in vistos:
                            vistos.add(p)
                            r["ficheros"].append(p)
        elif tipo == "user":
            if isinstance(cont, list):
                for b in cont:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    nombre = usos.get(b.get("tool_use_id"))
                    txt = _texto(b.get("content"))
                    if nombre == "ExitPlanMode" and "approved your plan" in txt:
                        ruta = RE_PLAN_RUTA.search(txt)
                        tit = RE_TITULO.search(txt.split("Approved Plan:", 1)[-1])
                        r["planes"].append("%s%s" % (tit.group(1).strip() if tit else "plan aprobado",
                                                     (" (" + ruta.group(1) + ")") if ruta else ""))
                    elif nombre == "AskUserQuestion" and not b.get("is_error"):
                        r["respuestas"].append(" ".join(txt.split())[:400])
            if d.get("origin") == {"kind": "human"}:
                t = RE_SYSREM.sub("", _texto(cont)).strip()
                if t:
                    t = " ".join(t.split())
                    r["prompts"].append(t[:MAX_PROMPT_CHARS] + ("…" if len(t) > MAX_PROMPT_CHARS else ""))
    r["prompts"] = r["prompts"][-MAX_PROMPTS:]
    r["ficheros"] = r["ficheros"][-MAX_FICHEROS:]
    return r


def _git(cwd, *args):
    try:
        p = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=4)
        # rstrip, no strip: en `status --short` el espacio inicial es la columna del índice
        return p.stdout.rstrip() if p.returncode == 0 else ""
    except Exception:
        return ""


def componer(entrada, datos):
    cwd = entrada.get("cwd") or os.getcwd()
    rama = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    raiz = _git(cwd, "rev-parse", "--show-toplevel") or cwd
    sucio = _git(cwd, "status", "--short")
    L = ["TRASPASO ANTES DE COMPACTAR (%s)" % (entrada.get("trigger") or "?"),
         "Rama: %s · árbol: %s" % (rama, raiz),
         "Paso: %s" % (datos["paso"] or "no consta (no escribí «Paso N de M»)")]
    if entrada.get("custom_instructions"):
        L.append("Instrucción de /compact: %s" % str(entrada["custom_instructions"])[:300])
    L.append("")
    L.append("Decisiones cerradas (no volver a preguntarlas):")
    if datos["planes"] or datos["respuestas"]:
        L += ["  · plan aprobado: %s" % p for p in datos["planes"]]
        L += ["  · respondió: %s" % a for a in datos["respuestas"]]
    else:
        L.append("  · ninguna registrada por plan aprobado ni por AskUserQuestion")
    L.append("")
    L.append("Sin commitear:" if sucio else "Sin commitear: nada")
    if sucio:
        L += ["  " + s for s in sucio.splitlines()[:30]]
    if datos["ficheros"]:
        L.append("Ficheros tocados en la sesión:")
        L += ["  · " + f for f in datos["ficheros"]]
    if datos["prompts"]:
        L.append("")
        L.append("Últimos prompts de {{TITULAR}} (recortados):")
        L += ["  » " + p for p in datos["prompts"]]
    L.append("")
    ultimo_plan = datos["planes"][-1] if datos["planes"] else None
    L.append("Prompt de continuación: retoma en la rama %s%s, en el paso %s. Comprueba el estado real "
             "(git status, tests) antes de decir qué queda."
             % (rama, (", siguiendo " + ultimo_plan) if ultimo_plan else "",
                datos["paso"] or "que indique el último mensaje"))
    return "\n".join(L)


def main():
    if os.environ.get("BTP_TRASPASO_OFF") == "1":
        return 0
    try:
        entrada = json.loads(sys.stdin.read() or "{}")
        sid = entrada.get("session_id")
        tp = entrada.get("transcript_path")
        if not sid:
            return 0
        datos = extraer(tp) if tp and os.path.isfile(tp) else \
            {"ficheros": [], "paso": None, "planes": [], "respuestas": [], "prompts": []}
        sys.path.insert(0, TOOLS)
        import continuity
        continuity.traspaso_guardar(sid, componer(entrada, datos))
    except Exception:
        pass                                    # fail-open: nunca bloquear la compactación
    return 0


if __name__ == "__main__":
    sys.exit(main())
