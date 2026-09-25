#!/usr/bin/env python3
"""subagente_contexto.py — hook SubagentStart: cada subagente arranca sabiendo lo que {{TITULAR}} ya corrigió.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

EL AGUJERO (P8, verificado en code.claude.com/docs/en/sub-agents el 25-sep-2026)
  Un subagente recibe CLAUDE.md y las reglas sin `paths:`, pero NO la memoria automática
  (MEMORY.md) ni el recall que `memoria_recall.sh` inyecta en cada mensaje de {{TITULAR}}: ese hook es
  UserPromptSubmit y solo corre en el hilo principal. El subagente trabaja sin las lecciones de sus
  correcciones. Medido en fase 0: el 48 % de sus informes con hallazgo del gate, frente al 16 %.

QUÉ INYECTA (additionalContext, «before its first prompt» según la doc)
  1. Bloque fijo: «Las que no se olvidan nunca» de MEMORY.md, una línea por norma.
  2. Bloque variable: `memoria_radar.py recall` con el ENCARGO como consulta si está en el
     transcript de la sesión madre; si no, con la `description` del agente en .claude/agents/.
  Tope 9.500 caracteres: la doc corta a 10.000 y, si se pasa, al subagente solo le llegan 2.000
  de vista previa. No hay margen para pasarse.

A/B (para saber si sirve, no suponerlo)
  Brazo por hash del `agent_id`: par → inyecta, impar → control sin nada. La traza
  (tools/state/subagente_contexto.jsonl, SIN contenido) guarda agent_id, brazo, tipo y de dónde
  salió la consulta. `gate_subagente.py` apunta el mismo agent_id con sus hallazgos: cruzando las
  dos se compara la tasa por brazo. Criterio del plan: si a los 14 días el brazo con inyección no
  baja al menos un 30 %, esta pieza se retira. `BTP_SUBCTX_AB=0` inyecta a todos.

FAIL-OPEN, sin excepciones: SubagentStart no puede bloquear y este hook tampoco lo intenta.
Cualquier error sale 0 en silencio. Bypass `BTP_SUBCTX_OFF=1`.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
TRAZA = os.environ.get("BTP_SUBCTX_TRAZA") or os.path.join(REPO, "tools", "state", "subagente_contexto.jsonl")
MEMORY_MD = os.environ.get("BTP_MEMORY_MD") or os.path.expanduser(
    "~/.claude/projects/-Users-polaris-claudecode/memory/MEMORY.md")
AGENTS_DIR = os.path.join(REPO, ".claude", "agents")
TOPE = 9500
TOPE_FIJO = 3800
COLA_TRANSCRIPT = 400_000   # bytes del final del transcript que se leen, no más


def brazo(agent_id):
    if os.environ.get("BTP_SUBCTX_AB") == "0":
        return "inyecta"
    h = int(hashlib.sha256((agent_id or "").encode()).hexdigest(), 16)
    return "inyecta" if h % 2 == 0 else "control"


def bloque_fijo():
    """Las críticas de MEMORY.md, una línea cada una: «Título — gancho» recortado."""
    try:
        texto = open(MEMORY_MD, encoding="utf-8").read()
    except Exception:
        return ""
    m = re.search(r"^## Las que no se olvidan nunca\s*\n(.*?)(?=^## |\Z)", texto, re.S | re.M)
    if not m:
        return ""
    lineas = []
    for ln in m.group(1).splitlines():
        mm = re.match(r"-\s*\[([^\]]+)\]\([^)]*\)\s*[—-]\s*(.*)", ln.strip())
        if mm:
            gancho = " ".join(mm.group(2).split())
            gancho = gancho if len(gancho) <= 110 else gancho[:109] + "…"
            lineas.append("· %s: %s" % (mm.group(1), gancho))
    out = "Normas de {{TITULAR}} que no se olvidan nunca (resumen de su memoria):\n" + "\n".join(lineas)
    return out if len(out) <= TOPE_FIJO else out[:TOPE_FIJO - 1] + "…"


def encargo(transcript_path):
    """Prompt del último Agent/Task del hilo madre que aún no tiene resultado. '' si no hay."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - COLA_TRANSCRIPT))
            cola = fh.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return ""
    lanzados, resueltos = [], set()
    for ln in cola:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        c = (o.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                p = (b.get("input") or {}).get("prompt")
                if isinstance(p, str) and p.strip():
                    lanzados.append((b.get("id"), p))
            elif b.get("type") == "tool_result":
                resueltos.add(b.get("tool_use_id"))
    for tid, p in reversed(lanzados):
        if tid not in resueltos:
            return p
    return ""


def descripcion(agent_type):
    if not agent_type or "/" in agent_type or agent_type.startswith("."):
        return ""
    try:
        for ln in open(os.path.join(AGENTS_DIR, agent_type + ".md"), encoding="utf-8"):
            if ln.startswith("description:"):
                return ln.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def recall(consulta, presupuesto):
    if not consulta or presupuesto < 300:
        return ""
    py = os.path.join(REPO, ".venv-embed", "bin", "python")
    py = py if os.access(py, os.X_OK) else sys.executable
    try:
        r = subprocess.run([py, os.path.join(REPO, "tools", "memoria_radar.py"), "recall",
                            "--query", consulta[:2000], "--fichas", "2", "--extra", "3"],
                           capture_output=True, text=True, timeout=4, cwd=REPO)
        out = (r.stdout or "").strip()
    except Exception:
        return ""
    return out if len(out) <= presupuesto else out[:presupuesto - 1] + "…"


def construir(agent_type, transcript_path):
    """(texto, origen_consulta). Nunca pasa de TOPE."""
    fijo = bloque_fijo()
    consulta = encargo(transcript_path)
    origen = "encargo" if consulta else "descripcion"
    if not consulta:
        consulta = descripcion(agent_type)
        if not consulta:
            origen = "ninguna"
    cab = ("🧭 CONTEXTO DE POLARIS PARA ESTE SUBAGENTE. No viste la conversación ni la memoria de "
           "{{TITULAR}}: estas son sus normas ya aprobadas. Si algo choca con tu encargo, manda el muro "
           "de CLAUDE.md.\n\n")
    variable = recall(consulta, TOPE - len(cab) - len(fijo) - 4)
    texto = cab + fijo + ("\n\n" + variable if variable else "")
    return texto[:TOPE], origen


def _trazar(fila):
    try:
        os.makedirs(os.path.dirname(TRAZA), exist_ok=True)
        with open(TRAZA, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except Exception:
        pass


def procesar(datos):
    if os.environ.get("BTP_SUBCTX_OFF") == "1" or not isinstance(datos, dict):
        return None
    agent_id = datos.get("agent_id") or ""
    agent_type = datos.get("agent_type") or ""
    b = brazo(agent_id)
    fila = {"ts": round(time.time(), 1), "agent_id": agent_id, "agente": agent_type, "brazo": b}
    if b == "control":
        fila["origen"] = encargo(datos.get("transcript_path") or "") and "encargo" or "sin_encargo"
        _trazar(fila)
        return None
    texto, origen = construir(agent_type, datos.get("transcript_path") or "")
    fila.update({"origen": origen, "chars": len(texto)})
    _trazar(fila)
    if not texto.strip():
        return None
    return {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": texto}}


def ab(dias=14, traza_gate=None):
    """Tasa de informes con hallazgo (avisado) por brazo, cruzando por agent_id. Sin contenido."""
    traza_gate = traza_gate or os.path.join(os.path.dirname(TRAZA), "gate_subagente.jsonl")
    corte = time.time() - dias * 86400

    def filas(ruta):
        try:
            for ln in open(ruta, encoding="utf-8"):
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if o.get("ts", 0) >= corte and o.get("agent_id"):
                    yield o
        except Exception:
            return

    brazos = {o["agent_id"]: o["brazo"] for o in filas(TRAZA)}
    res = {"inyecta": [0, 0], "control": [0, 0]}
    for o in filas(traza_gate):
        b = brazos.get(o["agent_id"])
        if b in res:
            res[b][0] += 1
            res[b][1] += bool(o.get("checks"))
    return {b: {"informes": n, "con_aviso": k, "tasa": round(k / n, 3) if n else None}
            for b, (n, k) in res.items()}


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--ab":
        print(json.dumps(ab(int(sys.argv[2]) if len(sys.argv) > 2 else 14), ensure_ascii=False, indent=1))
        return 0
    try:
        out = procesar(json.load(sys.stdin))
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
