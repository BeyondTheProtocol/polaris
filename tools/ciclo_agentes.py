#!/usr/bin/env python3
"""tools/ciclo_agentes.py — el ciclo de vida de cada agente: id, a qué turno pertenece y por qué terminó.

POR QUÉ EXISTE (14-sep-2026). @IAenBruto preguntó en X: *«¿Polaris conserva el agent_id y el motivo de
salida?»*. La respuesta verificada era «a medias»: los encargos de la cola guardan estado final y el
motivo de cada intento, pero de los agentes que se lanzan dentro de una sesión solo quedaba nombre y
ok/fail (`traza_subagente.py`). El dato existía, desperdigado: el harness deja `agent-<id>.meta.json`
(con el `toolUseId` que liga el agente a la llamada de la sesión), la notificación de fin en el
transcript padre trae `<status>` y `<summary>`, y el transcript del subagente acaba con un
`stop_reason`. Nadie lo juntaba. Esto lo junta.

QUÉ HACE
  · `apuntar_lanzamiento(datos)` — lo llama el hook PostToolUse: agent_id, tool_use_id, sesión, tipo,
    modelo, si es en segundo plano y el status inicial. SIN prompt, SIN descripción, SIN respuesta.
  · `cerrar()` — para cada agente sin final resuelve cómo terminó, por este orden:
      1. notificación de fin en el transcript padre con su tool_use_id → completed/failed/stopped + resumen;
      2. `toolUseResult` síncrono con status final;
      3. diario del workflow con un `result` de ese agente → completed;
      4. último registro de su transcript: `end_turn` → completed; sin cerrar y sin actividad en
         HORAS_CORTADO → cortado; si no, sigue vivo (no se escribe final).
    También descubre en disco los agentes que no pasaron por el hook (workflows, sesiones sin hook).
    Idempotente por agent_id: añade UNA línea «terminal» por agente, nunca reescribe.
  · `estado()`, `huerfanos()` — lectura.
  · `ttl()` — SOLO informa de transcripts de subagentes viejos. No borra ni mueve nada bajo
    ~/.claude: borrar es irreversible y esos ficheros los usan coste, caja y los replays. Decide {{TITULAR}}.

El registro va por días (`tools/state/agentes/ciclo-AAAA-MM-DD.jsonl`, casa base) para que
`rotar_logs.py` lo caduque igual que borde y outbox.

Uso:
  python3 tools/ciclo_agentes.py cerrar [--dias 7]
  python3 tools/ciclo_agentes.py estado [--sesion <id>]
  python3 tools/ciclo_agentes.py huerfanos [--horas 6]
  python3 tools/ciclo_agentes.py ttl [--dias 30]
"""
import glob
import json
import os
import re
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

REPO_VIVO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO_VIVO, "tools", "state")
DIR = os.path.join(STATE, "agentes")
HORAS_CORTADO = 6
HERRAMIENTAS = ("Agent", "Task")
ESTADOS_FINALES = ("completed", "failed", "stopped", "cortado")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")


def _projects():
    return os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")


def _dirs_proyecto():
    """Carpetas de proyecto a recorrer: todas si hay BTP_PROJECTS_DIR (tests); si no, las del repo."""
    if os.environ.get("BTP_PROJECTS_DIR"):
        base = _projects()
        return sorted(os.path.join(base, d) for d in os.listdir(base)
                      if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
    try:
        import coste
        return coste.proyectos_del_repo()
    except Exception:
        return []


# ── registro ─────────────────────────────────────────────────────────────────────────────────
def _ruta_dia(ts=None):
    return os.path.join(DIR, "ciclo-%s.jsonl" % time.strftime("%Y-%m-%d", time.localtime(ts or time.time())))


def _escribir(fila):
    os.makedirs(DIR, exist_ok=True)
    with open(_ruta_dia(), "a", encoding="utf-8") as f:
        f.write(json.dumps(fila, ensure_ascii=False) + "\n")


def _leer_registro():
    filas = []
    for f in sorted(glob.glob(os.path.join(DIR, "ciclo-*.jsonl"))):
        try:
            with open(f, encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        filas.append(json.loads(ln))
                    except Exception:
                        continue
        except OSError:
            continue
    return filas


def apuntar_lanzamiento(datos):
    """Desde el hook PostToolUse. Devuelve la fila escrita o None. Nunca lanza."""
    try:
        if not isinstance(datos, dict) or datos.get("tool_name") not in HERRAMIENTAS:
            return None
        entrada = datos.get("tool_input") if isinstance(datos.get("tool_input"), dict) else {}
        resp = datos.get("tool_response") if isinstance(datos.get("tool_response"), dict) else {}
        agent_id = resp.get("agentId")
        tipo = entrada.get("subagent_type") or resp.get("agentType")
        if not isinstance(agent_id, str) or not _SLUG.match(agent_id):
            return None
        if tipo is not None and (not isinstance(tipo, str) or not _SLUG.match(tipo)):
            tipo = None
        fila = {"evento": "lanzado", "ts": time.time(), "agent_id": agent_id,
                "tool_use_id": datos.get("tool_use_id"),
                "sesion": str(datos.get("session_id") or "")[:36],
                "transcript": datos.get("transcript_path"),
                "tipo": tipo, "async": bool(resp.get("isAsync")),
                "modelo": resp.get("resolvedModel"), "status": resp.get("status"),
                "fuente": "hook"}
        _escribir(fila)
        return fila
    except Exception:
        return None


# ── descubrir en disco ───────────────────────────────────────────────────────────────────────
def _metas():
    """(agent_id, meta_path, meta, transcript_padre, workflow_dir|None) de todos los subagentes en disco."""
    for proy in _dirs_proyecto():
        pat_s = os.path.join(proy, "*", "subagents", "agent-*.meta.json")
        pat_w = os.path.join(proy, "*", "subagents", "workflows", "wf_*", "agent-*.meta.json")
        for patron, es_wf in ((pat_s, False), (pat_w, True)):
            for mp in glob.glob(patron):
                aid = os.path.basename(mp)[len("agent-"):-len(".meta.json")]
                try:
                    meta = json.load(open(mp, encoding="utf-8"))
                except Exception:
                    meta = {}
                if es_wf:
                    sesion_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(mp))))
                    wf = os.path.dirname(mp)
                else:
                    sesion_dir = os.path.dirname(os.path.dirname(mp))
                    wf = None
                yield aid, mp, meta, sesion_dir + ".jsonl", wf


def _en_cualquier_proyecto(ruta):
    """La misma sesión puede quedar repartida entre carpetas de proyecto: una sesión que entra en un
    worktree sigue escribiendo su transcript en la carpeta de ese worktree, pero los meta.json de sus
    subagentes quedan donde nacieron (visto el 14-sep-2026: 35 agentes «vivo» por esto). Si la ruta no
    existe, se busca la misma ruta relativa en las demás carpetas de proyecto."""
    if not ruta or os.path.exists(ruta):
        return ruta
    base = _projects()
    try:
        rel = os.path.relpath(ruta, base)
    except ValueError:
        return ruta
    partes = rel.split(os.sep)
    if len(partes) < 2 or partes[0] in ("..", "."):
        return ruta
    cola = os.path.join(*partes[1:])
    for d in sorted(glob.glob(os.path.join(base, "*"))):
        candidata = os.path.join(d, cola)
        if os.path.exists(candidata):
            return candidata
    return ruta


# ── resolver el final ────────────────────────────────────────────────────────────────────────
_NOTIF = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>.*?<status>([a-z_]+)</status>"
                    r"(?:.*?<summary>(.*?)</summary>)?", re.S)


def _por_notificacion(transcript, tool_use_id):
    if not tool_use_id or not transcript or not os.path.exists(transcript):
        return None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if tool_use_id not in ln or "<status>" not in ln:
                    continue
                texto = ln.replace("\\n", "\n")
                for m in _NOTIF.finditer(texto):
                    if m.group(1).strip() == tool_use_id and m.group(2) in ("completed", "failed", "stopped"):
                        resumen = re.sub(r"\s+", " ", (m.group(3) or "")).strip()[:200]
                        return m.group(2), resumen, "notificacion"
    except OSError:
        return None
    return None


def _por_resultado_sincrono(transcript, agent_id):
    if not transcript or not os.path.exists(transcript):
        return None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if agent_id not in ln or '"toolUseResult"' not in ln:
                    continue
                try:
                    tur = json.loads(ln).get("toolUseResult")
                except Exception:
                    continue
                if isinstance(tur, dict) and tur.get("agentId") == agent_id:
                    st = tur.get("status")
                    if st in ("completed", "failed", "stopped"):
                        return st, "", "resultado"
    except OSError:
        return None
    return None


def _por_diario(wf_dir, agent_id):
    if not wf_dir:
        return None
    j = os.path.join(wf_dir, "journal.jsonl")
    try:
        with open(j, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if agent_id in ln and '"result"' in ln:
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if r.get("type") == "result" and r.get("agentId") == agent_id:
                        return "completed", "resultado del workflow", "diario"
    except OSError:
        return None
    return None


def _por_transcript(ruta, ahora):
    if not os.path.exists(ruta):
        return None
    ultimo = None
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    ultimo = json.loads(ln)
                except Exception:
                    continue
    except OSError:
        return None
    msg = (ultimo or {}).get("message") or {}
    if msg.get("stop_reason") == "end_turn":
        return "completed", "end_turn", "transcript"
    if ahora - os.path.getmtime(ruta) > HORAS_CORTADO * 3600:
        motivo = "sin cerrar: último stop_reason=%s" % (msg.get("stop_reason") or "ninguno")
        return "cortado", motivo, "transcript"
    return None


def cerrar(dias=7, ahora=None):
    """Resuelve y apunta el final de los agentes que aún no lo tienen. Devuelve {agent_id: estado}."""
    ahora = ahora or time.time()
    corte = ahora - dias * 86400
    registro = _leer_registro()
    terminados = {f["agent_id"] for f in registro if f.get("evento") == "terminal"}
    lanzados = {f["agent_id"]: f for f in registro if f.get("evento") == "lanzado"}
    resueltos = {}
    for aid, mp, meta, padre, wf in _metas():
        if aid in terminados:
            continue
        try:
            if os.path.getmtime(mp) < corte:
                continue
        except OSError:
            continue
        lanz = lanzados.get(aid)
        tuid = (lanz or {}).get("tool_use_id") or meta.get("toolUseId")
        if not lanz:
            _escribir({"evento": "lanzado", "ts": os.path.getmtime(mp), "agent_id": aid,
                       "tool_use_id": tuid, "sesion": os.path.basename(padre)[:-len(".jsonl")][:36],
                       "transcript": padre, "tipo": meta.get("agentType"),
                       "async": meta.get("requestShape") == "background",
                       "modelo": None, "status": None, "fuente": "disco"})
        transcript_padre = _en_cualquier_proyecto((lanz or {}).get("transcript") or padre)
        sub = _en_cualquier_proyecto(mp[:-len(".meta.json")] + ".jsonl")
        final = (_por_notificacion(transcript_padre, tuid)
                 or _por_resultado_sincrono(transcript_padre, aid)
                 or _por_diario(wf, aid)
                 or _por_transcript(sub, ahora))
        if not final:
            resueltos[aid] = "vivo"
            continue
        estado, motivo, fuente = final
        fin_ts = os.path.getmtime(sub) if os.path.exists(sub) else None
        _escribir({"evento": "terminal", "ts": ahora, "agent_id": aid, "estado": estado,
                   "motivo": motivo, "fuente": fuente, "fin_ts": fin_ts})
        terminados.add(aid)
        resueltos[aid] = estado
    return resueltos



# ── dónde se atascó (destilado de {{CONTACTO}} {{CONTACTO}}, 20-sep-26) ────────────────────────────────
# Su punto: antes de darle OTRA herramienta a un agente que falla, mira DÓNDE se atasca. No es
# lo mismo no entender el encargo, que no poder ejecutarlo, que perder el hilo a mitad — son tres
# arreglos distintos, y el reflejo de añadir un MCP más no cura ninguno. Aquí ya guardábamos el
# motivo de salida; lo que faltaba era agruparlo para poder RESPONDER a esa pregunta.
#
# Clasifica por señales del motivo, y cuando no hay señal dice `sin_clasificar` — nunca inventa
# una causa, que sería peor que no clasificar.
ATASCOS = (
    ("encargo",   re.compile(r"(no\s+(entend|comprend)|ambig|falta\s+context|no\s+se\s+qu[eé]|"
                             r"prompt|instruc\w*\s+(poco|nada)\s+clara|sin\s+encargo)", re.I)),
    ("ejecucion", re.compile(r"(error|exception|traceback|denied|denegad|permiso|timeout|"
                             r"not\s+found|no\s+existe|fall[oó]|rc=[1-9]|exit\s+code\s+[1-9]|"
                             r"rate\s*limit|sin\s+saldo|credit)", re.I)),
    ("retomar",   re.compile(r"(context\w*\s+(lleno|agotad|overflow)|compact|se\s+perdi[oó]|"
                             r"reanud|retom|cortad|interrumpid|stopped|l[ií]mite\s+de\s+turnos)", re.I)),
)


def clasificar_atasco(estado, motivo=""):
    """`ok` si terminó bien; si no, en cuál de los tres puntos se atascó. Nunca adivina."""
    if estado == "completed":
        return "ok"
    texto = "%s %s" % (estado or "", motivo or "")
    for clase, rx in ATASCOS:
        if rx.search(texto):
            return clase
    return "sin_clasificar"


def atascos(dias=7):
    """Recuento por punto de atasco: la pregunta «¿por qué fallan nuestros agentes?» respondida
    con datos y no con impresiones. Solo cuenta finales ya resueltos."""
    corte = time.time() - dias * 86400
    cuenta = {}
    for f in _leer_registro():
        if f.get("evento") != "terminal" or (f.get("ts") or 0) < corte:
            continue
        clase = clasificar_atasco(f.get("estado"), f.get("motivo", ""))
        cuenta[clase] = cuenta.get(clase, 0) + 1
    return cuenta


# ── lectura ──────────────────────────────────────────────────────────────────────────────────
def estado(sesion=None):
    """Una fila por agente: lanzamiento + final (si lo hay)."""
    filas = {}
    for f in _leer_registro():
        aid = f.get("agent_id")
        if not aid:
            continue
        fila = filas.setdefault(aid, {"agent_id": aid, "estado": "vivo"})
        if f.get("evento") == "lanzado":
            for k in ("tool_use_id", "sesion", "tipo", "async", "modelo", "ts", "fuente"):
                if f.get(k) is not None and k not in fila:
                    fila[k] = f.get(k)
        elif f.get("evento") == "terminal":
            fila.update({"estado": f.get("estado"), "motivo": f.get("motivo"),
                         "fin_fuente": f.get("fuente"), "fin_ts": f.get("fin_ts")})
    out = list(filas.values())
    if sesion:
        out = [r for r in out if str(r.get("sesion") or "").startswith(sesion)]
    return sorted(out, key=lambda r: r.get("ts") or 0)


def huerfanos(horas=HORAS_CORTADO, ahora=None):
    ahora = ahora or time.time()
    return [r for r in estado() if r["estado"] in ("vivo", "cortado")
            and ahora - (r.get("ts") or ahora) > horas * 3600]


def ttl(dias=30, ahora=None):
    """SOLO informa. No borra ni mueve nada bajo ~/.claude."""
    ahora = ahora or time.time()
    corte = ahora - dias * 86400
    con_final = {r["agent_id"] for r in estado() if r["estado"] in ESTADOS_FINALES}
    viejos = total_bytes = cerrados = 0
    for aid, mp, _meta, _padre, _wf in _metas():
        sub = mp[:-len(".meta.json")] + ".jsonl"
        try:
            if os.path.getmtime(sub) >= corte:
                continue
            viejos += 1
            total_bytes += os.path.getsize(sub)
            cerrados += aid in con_final
        except OSError:
            continue
    return {"dias": dias, "transcripts_viejos": viejos, "mb": round(total_bytes / 1048576, 1),
            "con_final_registrado": cerrados, "accion": "ninguna: solo informe"}


def main(argv):
    cmd = argv[0] if argv else "estado"

    def _arg(flag, defecto):
        return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else defecto

    if cmd == "cerrar":
        r = cerrar(int(_arg("--dias", 7)))
        cuenta = {}
        for e in r.values():
            cuenta[e] = cuenta.get(e, 0) + 1
        print("agentes revisados sin final previo: %d · %s" % (len(r), cuenta))
        return 0
    if cmd == "estado":
        filas = estado(_arg("--sesion", None))
        for f in filas[-40:]:
            print("%-18s %-22s %-10s %-9s %s" % (f["agent_id"], (f.get("tipo") or "?")[:22],
                                                 f["estado"], f.get("fin_fuente") or "-",
                                                 (f.get("motivo") or "")[:70]))
        cuenta = {}
        for f in filas:
            cuenta[f["estado"]] = cuenta.get(f["estado"], 0) + 1
        print("\n%d agentes · %s" % (len(filas), cuenta))
        return 0
    if cmd == "huerfanos":
        for f in huerfanos(int(_arg("--horas", HORAS_CORTADO))):
            print("%s %s %s" % (f["agent_id"], f.get("tipo"), f["estado"]))
        return 0
    if cmd == "ttl":
        print(json.dumps(ttl(int(_arg("--dias", 30))), ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        sys.stderr.write("ciclo_agentes: %r\n" % e)
        sys.exit(0)
