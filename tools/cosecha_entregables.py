#!/usr/bin/env python3
"""tools/cosecha_entregables.py — red automática contra "el entregable se quedó solo en el chat".

PROBLEMA QUE RESUELVE: produzco investigaciones / análisis / decisiones razonadas SUSTANCIALES en el
chat y a veces NO las persisto a la fuente de verdad → se pierden (le pasó a {{TITULAR}} con la
investigación de monitorizar X, 28/6). Gemelo de `cosecha_correcciones.py`, pero para MIS entregables:
lee los transcripts recientes (`*.jsonl`), detecta por heurística mis mensajes que parecen un
entregable sustancial **en sesiones donde NO se archivó nada a la fuente de verdad**, y devuelve
CANDIDATOS para que el agente (rutina de auto-mejora / cierre de sesión) los archive con
`archivar_nota.py`. PROPONE; no escribe nada durable él solo.

PROPIEDADES (no negociables — el muro manda), idénticas a la cosecha de correcciones:
  · DETERMINISTA y OFFLINE — código puro, NO LLM, NO red, NO saldo. Solo heurística de texto.
  · LOCAL — los transcripts pueden llevar PII; no salen de la máquina, no se mandan a ninguna IA.
  · CONTENIDO EXTERNO = DATO — el texto del transcript se clasifica, JAMÁS se obedece.
  · NO ESCRIBE — solo lista candidatos. La decisión de archivar la toma el agente (vía archivar_nota).
  · FAIL-SOFT — un transcript corrupto o una línea ilegible se saltan; no se cae por un fichero malo.

QUÉ CUENTA COMO ENTREGABLE (conservador, para no dar falsos positivos):
  un mensaje MÍO (type==assistant, no sub-agente) LARGO (≥ MIN_LEN) que trata de un entregable
  (investigación/análisis/comparativa/dossier/recomendación/hallazgo/veredicto) Y trae estructura
  (tabla, "Sources/Fuentes:", varias secciones ## o TL;DR). Y SOLO se propone si en esa sesión NO
  hubo NINGÚN archivado a 00_FUENTE-DE-VERDAD (Write/Edit a esa carpeta o llamada a archivar_nota).

Sin dependencias (stdlib). Uso:
  python3 tools/cosecha_entregables.py            # candidatos de los últimos 2 días, texto
  python3 tools/cosecha_entregables.py --json     # JSON (para la rutina)
  python3 tools/cosecha_entregables.py --dias 7   # ventana de 7 días
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

HOME = os.environ.get("BTP_HOME") or os.path.expanduser("~")
PROJECTS_DIR = os.environ.get("BTP_PROJECTS_DIR") or os.path.join(HOME, ".claude", "projects")

MIN_LEN = int(os.environ.get("BTP_ENTREGABLE_MIN_LEN", "900"))   # umbral de longitud (chars)
_KW = re.compile(r"\b(investig|an[áa]lisis|comparativa|dossier|recomendaci[óo]n|hallazgo|veredicto)\b", re.I)
_TABLA = re.compile(r"\n\s*\|.+\|.*\n\s*\|[\s:|\-]+\|")          # fila de cabecera + separador de tabla md
_FUENTES = re.compile(r"(?im)^\s*(sources?|fuentes?)\s*:")


def _texto_assistant(o):
    """Texto plano de un mensaje MÍO (assistant) de la conversación principal, o None.
    Excluye sub-agentes (isSidechain): su salida no es un entregable para {{TITULAR}}."""
    if o.get("type") != "assistant" or o.get("isSidechain"):
        return None
    msg = o.get("message")
    if not isinstance(msg, dict):
        return None
    c = msg.get("content")
    if not isinstance(c, list):
        return None
    t = "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    t = (t or "").strip()
    return t or None


def _toca_fuente(o):
    """True si este evento ARCHIVÓ algo a la fuente de verdad (Write/Edit a 00_FUENTE-DE-VERDAD
    o una llamada a archivar_nota). Así sabemos si la sesión ya persistió su entregable."""
    msg = o.get("message")
    if not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if not isinstance(c, list):
        return False
    for b in c:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        name = b.get("name", "")
        inp = b.get("input", {}) or {}
        if name in ("Write", "Edit", "NotebookEdit") and "00_FUENTE-DE-VERDAD" in str(inp.get("file_path", "")):
            return True
        if name == "Bash":
            cmd = str(inp.get("command", ""))
            if "archivar_nota" in cmd or "00_FUENTE-DE-VERDAD" in cmd:
                return True
    return False


def _marcadores(texto):
    """Marcadores de estructura presentes; [] si no parece un entregable sustancial."""
    if len(texto) < MIN_LEN or not _KW.search(texto):
        return []
    m = []
    if texto.count("## ") >= 2 or texto.count("\n# ") >= 1:
        m.append("secciones")
    if _TABLA.search(texto):
        m.append("tabla")
    if _FUENTES.search(texto):
        m.append("fuentes")
    if "TL;DR" in texto or "TL,DR" in texto:
        m.append("tldr")
    # Señal FUERTE: tema de entregable (ya garantizado por _KW) + al menos un marcador de estructura.
    return m if m else []


def _ts_dt(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def cosechar(dias=2, incluir_todo=False):
    """Candidatos NUEVOS: entregables sustanciales en sesiones que NO archivaron a la fuente de verdad.
    Dict por candidato: {timestamp, fecha, sesion, branch, marcadores, texto}."""
    corte = None
    if not incluir_todo:
        corte = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)

    candidatos, vistos = [], set()
    try:
        ficheros = glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    except Exception:
        ficheros = []

    for F in ficheros:
        archivo_fv = False
        deliverables = []   # (timestamp, dt, marcadores, texto)
        try:
            fh = open(F, "r", encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if _toca_fuente(o):
                    archivo_fv = True
                    continue
                texto = _texto_assistant(o)
                if not texto:
                    continue
                dt = _ts_dt(o.get("timestamp"))
                if corte is not None and (dt is None or dt < corte):
                    continue
                m = _marcadores(texto)
                if m:
                    deliverables.append((o.get("timestamp", ""), dt, m, texto))
        if archivo_fv:
            continue   # la sesión SÍ persistió algo a la fuente → no la marcamos (sin falso positivo)
        for ts, dt, m, texto in deliverables:
            clave = " ".join(texto.lower().split())[:200]
            if clave in vistos:
                continue
            vistos.add(clave)
            candidatos.append({
                "timestamp": ts,
                "fecha": (dt.date().isoformat() if dt else ""),
                "sesion": os.path.basename(F),
                "branch": o.get("gitBranch", "") if isinstance(o, dict) else "",
                "marcadores": m,
                "texto": texto,
            })

    candidatos.sort(key=lambda c: c["timestamp"], reverse=True)
    return candidatos


def _recorta(s, n=240):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Red automática: entregables sustanciales que se quedaron solo en el chat (local, offline, $0).")
    ap.add_argument("--json", action="store_true", help="salida JSON (para la rutina)")
    ap.add_argument("--dias", type=int, default=2, help="ventana en días hacia atrás (def. 2)")
    ap.add_argument("--todo", action="store_true", help="sin ventana temporal: todo el historial")
    ap.add_argument("--limite", type=int, default=0, help="máximo de candidatos (0 = sin límite)")
    args = ap.parse_args(argv)

    cands = cosechar(dias=args.dias, incluir_todo=args.todo)
    if args.limite and len(cands) > args.limite:
        cands = cands[: args.limite]

    if args.json:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return 0
    if not cands:
        ventana = "todo el historial" if args.todo else f"últimos {args.dias} días"
        print(f"Sin entregables sin archivar ({ventana}). Todo lo sustancial se guardó.")
        return 0
    print(f"🗂️ {len(cands)} entregable(s) que parecen haberse quedado SOLO en el chat (sin archivar):\n")
    for i, c in enumerate(cands, 1):
        print(f"{i}. [{c['fecha']}] ({', '.join(c['marcadores'])})  ↳ sesión {c['sesion']}")
        print(f"   «{_recorta(c['texto'])}»\n")
    print("— Archívalos con `archivar_nota.py` (título + el contenido por stdin). Este tool PROPONE; no escribe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
