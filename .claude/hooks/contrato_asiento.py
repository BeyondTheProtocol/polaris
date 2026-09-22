#!/usr/bin/env python3
"""contrato_asiento.py — hook PreToolUse (Agent|Task): el contrato de la caja viaja con cada asiento.

POR QUÉ EXISTE (13-sep-2026). El paso 3 de la caja (`cosecha_panel.py`) junta en un dossier lo que
dijeron los comités de un turno, pero solo puede juntar lo que viene ESTRUCTURADO: el bloque JSON del
contrato. Ese contrato viajaba en las órdenes de `decide_peticion` y no llegaba a los subagentes que se
lanzan desde el chat. Medido sobre el último panel real: 0 decisiones, los dos asientos sin JSON.

QUÉ HACE
  · Si la llamada es `Agent` a un asiento de ANÁLISIS (`caja.ASIENTOS_CON_CONTRATO`), añade al final
    de su `prompt` el texto `caja.CONTRATO_ASIENTO`. Nada más.
  · Devuelve la entrada ENTERA con solo el prompt cambiado: `updatedInput` REEMPLAZA la entrada del
    tool (doc oficial de hooks), así que perder `description`, `model` o `run_in_background` sería
    cambiar la llamada sin querer.

QUÉ NO HACE, a propósito
  · NO pone `permissionDecision`. Con `allow` el tool se saltaría el sistema de permisos; este hook
    no decide nada sobre permisos, solo añade un texto. Un test lo asserta.
  · No toca a los asientos de redacción ni a los operativos (ver la lista en caja.py), ni repite si el
    prompt ya lleva el contrato, ni mira otros tools.
  · No va dentro de los hooks del muro (matcher "*"): tiene su propio matcher, y ningún otro hook del
    repo devuelve `updatedInput`, que es lo que la doc pide para no pisarse.

FAIL-OPEN: ante cualquier duda, sale 0 sin salida y la llamada sigue tal cual. Bypass `BTP_CONTRATO_OFF=1`.

Uso directo:
  echo '{"tool_name":"Agent","tool_input":{...}}' | python3 .claude/hooks/contrato_asiento.py
  python3 .claude/hooks/contrato_asiento.py --replay 14   # llamadas Agent REALES de N días: a cuántas
                                                          # se les añadiría (no escribe nada)
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools"))

HERRAMIENTAS = ("Agent", "Task")
REPO_VIVO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")

# ── Ventanilla clínica desde un worktree (13-sep-2026, deuda subagente-en-worktree-pierde-…) ──
# Un `verificacion` lanzado desde una sesión aislada en worktree devolvió un cotejo SOLO con
# resúmenes: sus 26 comandos acabaron en «worktree-isolated session's commands must run inside its
# worktree». Reproducido el mismo día: el subagente SÍ tiene Bash (en primer y en segundo plano); lo
# que el arnés rechaza es ejecutar FUERA del worktree, y el prompt le había dicho «repo: casa base» con
# la ventanilla en ruta relativa, así que hacía `cd` a casa base. Con ruta absoluta y sin `cd`, funciona.
# Estos son los agentes que leen informes originales por la ventanilla (lista de lector_clinico.py).
LECTORES_CLINICOS = frozenset({"verificacion", "comite-medico", "oncologo-virtual",
                               "herramientas-medicas"})
MARCA_VENTANILLA = "VENTANILLA CLÍNICA DESDE UN WORKTREE"


def aviso_ventanilla():
    return ("\n\n---\n🔒 " + MARCA_VENTANILLA + ": esta sesión está aislada en un worktree y el "
            "arnés rechaza cualquier comando que se ejecute FUERA de él. Para leer un informe "
            "original usa la ventanilla con RUTA ABSOLUTA y SIN `cd` a casa base: "
            "`python3 %s/tools/lector_clinico.py \"<ruta absoluta del informe>\"`. Si es un PDF "
            "escaneado o un DICOM, la ventanilla lo rechaza por stdout a propósito: añade "
            "`--a <destino en zona clínica>` (copia intacta en disco) o `--binario` (para tubearlo "
            "a pdftoppm/pdftotext). Si aun así no "
            "puedes abrir un original, NO lo sustituyas por un resumen: escribe «BLOQUEADO: original "
            "no abierto» en ese dato." % REPO_VIVO)


def en_worktree(cwd):
    return "/.claude/worktrees/" in str(cwd or "")


def reescribir(tool_name, tool_input, cwd=None):
    """La entrada nueva (dict) con lo que haya que añadir al prompt; None si se deja como está.

    Añade el contrato a los asientos de análisis y, si la sesión está en un worktree, el aviso de la
    ventanilla clínica a los agentes que leen informes. Un solo hook para las dos cosas: si dos hooks
    devolvieran `updatedInput` para el mismo tool, ganaría el último en terminar (doc oficial)."""
    import caja
    if tool_name not in HERRAMIENTAS or not isinstance(tool_input, dict):
        return None
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    tipo = tool_input.get("subagent_type")
    extra = ""
    if tipo in caja.ASIENTOS_CON_CONTRATO and caja.MARCA_CONTRATO not in prompt:
        extra += caja.CONTRATO_ASIENTO
    if tipo in LECTORES_CLINICOS and en_worktree(cwd) and MARCA_VENTANILLA not in prompt:
        extra += aviso_ventanilla()
    if not extra:
        return None
    nuevo = dict(tool_input)
    nuevo["prompt"] = prompt.rstrip() + extra
    return nuevo


def anadir_contrato(tool_name, tool_input):
    """Compatibilidad: solo el contrato (sin mirar el worktree). Lo usa el replay."""
    return reescribir(tool_name, tool_input, cwd=None)


def replay(dias=14):
    """Sobre llamadas Agent REALES de los últimos N días: a cuántas se les añadiría el contrato."""
    import collections
    import coste
    corte = time.time() - dias * 86400
    total = cambian = campos_rotos = 0
    por_asiento = collections.Counter()
    for proy in coste.proyectos_del_repo():
        for f in coste.transcripts(proy):
            try:
                if os.path.getmtime(f) < corte:
                    continue
                fh = open(f, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for linea in fh:
                    if '"name":"Agent"' not in linea and '"name":"Task"' not in linea:
                        continue
                    try:
                        reg = json.loads(linea)
                    except Exception:
                        continue
                    for b in ((reg.get("message") or {}).get("content") or []):
                        if not (isinstance(b, dict) and b.get("type") == "tool_use"
                                and b.get("name") in HERRAMIENTAS):
                            continue
                        total += 1
                        nuevo = anadir_contrato(b["name"], b.get("input"))
                        if nuevo is None:
                            continue
                        cambian += 1
                        por_asiento[nuevo.get("subagent_type")] += 1
                        viejo = b.get("input") or {}
                        if set(nuevo) != set(viejo) or any(nuevo[k] != viejo[k] for k in viejo if k != "prompt"):
                            campos_rotos += 1
    print("llamadas Agent en %d días: %d · recibirían el contrato: %d · con algún campo alterado "
          "además del prompt: %d" % (dias, total, cambian, campos_rotos))
    print("por asiento:", por_asiento.most_common())
    return 1 if campos_rotos else 0


def main(argv):
    if "--replay" in argv:
        i = argv.index("--replay")
        dias = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 14
        return replay(dias)
    if os.environ.get("BTP_CONTRATO_OFF") == "1":
        return 0
    try:
        datos = json.load(sys.stdin)
        nuevo = reescribir(datos.get("tool_name"), datos.get("tool_input"), datos.get("cwd"))
    except Exception:
        return 0
    if nuevo is not None:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "updatedInput": nuevo}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
