#!/usr/bin/env python3
"""cosecha_panel.py — hook `Stop`: si en este turno trabajó un PANEL, sale UN dossier, no N volcados.

POR QUÉ EXISTE (13-sep-2026, paso 3 del plan de la caja). `tools/caja.py` ya sabe juntar lo que
dijeron N subagentes en un dossier priorizado y trazable, pero había que llamarlo a mano: un turno
que lanzaba `comite-medico` + `verificacion` le seguía entregando a {{TITULAR}} dos respuestas para que
las juntara ella. Esto lo llama solo al cerrar el turno.

QUÉ HACE
  · Mira los subagentes de ESTA sesión que arrancaron en ESTE turno (desde el último mensaje de
    {{TITULAR}}). Si hay al menos dos COMITÉS distintos del gabinete (fichas en `.claude/agents/`),
    los cosecha con `caja.cosecha` y escribe el dossier con `caja.dossier`.
  · Deja el dossier JUNTO a los transcripts de esos subagentes
    (`~/.claude/projects/<proyecto>/<sesión>/subagents/dossier-<huella>.md`). No crea ningún sitio
    nuevo donde vivan datos del caso: está donde ya estaban las respuestas que resume.
  · Avisa en una línea (`systemMessage`) de dónde está, cuántas decisiones hay y si alguna salió
    sin fuente o algún asiento no cumplió el contrato JSON.
  · Apunta en `tools/state/caja/dossiers.jsonl` (casa base) QUIÉN participó y el resultado, SIN una
    palabra del contenido: sirve para medir, no para leer.

QUÉ NO HACE
  · No bloquea nunca. Está en modo AVISO (el plan: primero medir, luego decidir).
  · No repite: si el dossier de ese mismo conjunto de asientos ya existe, calla.
  · No mira agentes de fábrica (Explore, Plan, general-purpose) ni a los de workflow: solo comités.
  · No vive dentro de `traza_subagente.py`, que promete por escrito no guardar ni prompt ni
    respuesta. Este fichero SÍ lee respuestas; por eso va aparte y lo dice arriba.

FAIL-OPEN: cualquier error, fichero raro o duda → sale 0 sin decir nada. Bypass `BTP_CAJA_OFF=1`.

Uso directo:
  echo '{"session_id":"…","transcript_path":"…"}' | python3 .claude/hooks/cosecha_panel.py
  python3 .claude/hooks/cosecha_panel.py --replay 20    # sobre sesiones reales: cuántos turnos
                                                        # lo habrían disparado (no escribe nada)
"""
import datetime
import glob
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# El CÓDIGO (caja, paso_consolidacion) es el del árbol en el que corre la sesión.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools"))
# Las fichas de comités y el ESTADO, los de casa base: el sistema vivo es uno.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
LOG = os.path.join(STATE, "caja", "dossiers.jsonl")
MIN_COMITES = 2


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _es_pedido(reg):
    """¿Este registro del transcript es un mensaje de {{TITULAR}} (no un tool_result ni un meta)?"""
    if reg.get("type") != "user" or reg.get("isMeta"):
        return False
    contenido = (reg.get("message") or {}).get("content")
    if isinstance(contenido, str):
        return bool(contenido.strip())
    if isinstance(contenido, list):
        return any(isinstance(b, dict) and b.get("type") == "text" for b in contenido)
    return False


def _texto(reg):
    contenido = (reg.get("message") or {}).get("content")
    if isinstance(contenido, str):
        return contenido
    return " ".join(b.get("text", "") for b in (contenido or [])
                    if isinstance(b, dict) and b.get("type") == "text")


def turnos(transcript_path):
    """[(epoch, texto)] de cada mensaje de {{TITULAR}}, en orden. Solo se parsean las líneas de usuario."""
    out = []
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for linea in f:
                if '"type":"user"' not in linea and '"type": "user"' not in linea:
                    continue
                try:
                    reg = json.loads(linea)
                except Exception:
                    continue
                if _es_pedido(reg):
                    ts = _epoch(reg.get("timestamp"))
                    if ts is not None:
                        out.append((ts, _texto(reg)))
    except OSError:
        return []
    return out


def es_comite(slug):
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        return False
    return os.path.isfile(os.path.join(REPO, ".claude", "agents", slug + ".md"))


def _comites_en(subagents_dir, desde, hasta=None):
    """Slugs de comité cuyos subagentes arrancaron en la ventana [desde, hasta)."""
    vistos = set()
    for meta in glob.glob(os.path.join(subagents_dir, "*.meta.json")):
        try:
            ts = os.path.getmtime(meta)
            if ts < desde or (hasta is not None and ts >= hasta):
                continue
            with open(meta, encoding="utf-8") as f:
                tipo = (json.load(f) or {}).get("agentType")
        except Exception:
            continue
        if es_comite(tipo):
            vistos.add(tipo)
    return vistos


def _huella(cosechado):
    base = "|".join("%s@%.3f" % (c["agente"], c["ts"]) for c in cosechado)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _apuntar(fila):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except Exception:
        pass


def cerrar_turno(datos):
    """Devuelve el `systemMessage` a emitir, o None si no hay nada que consolidar."""
    if not isinstance(datos, dict) or datos.get("stop_hook_active"):
        return None
    tp, sid = datos.get("transcript_path"), datos.get("session_id")
    if not tp or not sid:
        return None
    sub = os.path.join(os.path.dirname(tp), sid, "subagents")
    if len(glob.glob(os.path.join(sub, "*.meta.json"))) < MIN_COMITES:
        return None                                  # barato: la mayoría de turnos salen aquí
    ts = turnos(tp)
    if not ts:
        return None
    desde, pedido = ts[-1]
    if len(_comites_en(sub, desde)) < MIN_COMITES:
        return None

    import caja
    cosechado = [c for c in caja.cosecha(sub, desde) if es_comite(c["agente"])]
    comites = sorted({c["agente"] for c in cosechado})
    if len(comites) < MIN_COMITES:
        return None
    huella = _huella(cosechado)
    ruta = os.path.join(sub, "dossier-%s.md" % huella)
    marca_vacio = os.path.join(sub, "dossier-%s.vacio" % huella)
    if os.path.exists(ruta) or os.path.exists(marca_vacio):
        return None                                  # ya consolidado: no repetir el aviso

    contribs = caja.contribuciones(cosechado)
    n_dec = sum(len(c["decisiones"]) for c in contribs)
    n_acc = sum(len(c["acciones"]) for c in contribs)
    mudos = sorted({c["agente"] for c in contribs if c["degradado"]})
    fila = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "sesion": sid[:8], "comites": comites,
            "asientos": len(cosechado), "decisiones": n_dec, "acciones": n_acc,
            "sin_contrato": mudos}

    # Panel sin NADA estructurado (nadie cerró con el JSON del contrato): un dossier vacío no junta
    # nada y el aviso sería ruido. Medido el 13-sep sobre el último panel real: 0 decisiones, los dos
    # asientos sin contrato, porque el contrato solo viaja en las órdenes de decide_peticion y no en
    # los subagentes que se lanzan desde el chat. Se apunta para MEDIR el cumplimiento y se calla.
    if n_dec == 0 and n_acc == 0:
        open(marca_vacio, "w").close()
        fila.update({"dossier": None, "vacio": True})
        _apuntar(fila)
        return None

    intencion = ((pedido or "").strip().splitlines() or ["(sin intención declarada)"])[0][:200]
    md, rc = caja.dossier(cosechado, intencion or "(sin intención declarada)")
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(md)
    os.replace(tmp, ruta)
    fila.update({"sin_fuente": bool(rc), "dossier": os.path.basename(ruta)})
    _apuntar(fila)

    partes = ["📋 Panel de %d comités (%s) juntado en un dossier: %s"
              % (len(comites), ", ".join(comites), ruta),
              "%d decisiones" % n_dec]
    if rc:
        partes.append("⚠️ alguna decisión SIN fuente trazable")
    if mudos:
        partes.append("sin bloque JSON del contrato: %s" % ", ".join(mudos))
    return " · ".join(partes)


def replay(n=20):
    """Sobre sesiones REALES: cuántos turnos habrían disparado el dossier. No escribe nada."""
    import caja
    sesiones = caja.sesiones(limite=n)
    turnos_con_sub = disparan = 0
    for s in sesiones:
        tp = os.path.dirname(s["dir"]) + ".jsonl"
        ts = [t for t, _ in turnos(tp)]
        for i, desde in enumerate(ts):
            hasta = ts[i + 1] if i + 1 < len(ts) else None
            metas = [m for m in glob.glob(os.path.join(s["dir"], "*.meta.json"))
                     if os.path.getmtime(m) >= desde and (hasta is None or os.path.getmtime(m) < hasta)]
            if not metas:
                continue
            turnos_con_sub += 1
            if len(_comites_en(s["dir"], desde, hasta)) >= MIN_COMITES:
                disparan += 1
    print("sesiones: %d · turnos con subagentes: %d · turnos que darían dossier: %d"
          % (len(sesiones), turnos_con_sub, disparan))
    return 0


def main(argv):
    if "--replay" in argv:
        i = argv.index("--replay")
        n = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 20
        return replay(n)
    if os.environ.get("BTP_CAJA_OFF") == "1":
        return 0
    try:
        msg = cerrar_turno(json.load(sys.stdin))
    except Exception:
        return 0
    if msg:
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
