#!/usr/bin/env python3
"""tools/cosecha_whatsapp.py — pre-filtro determinista de tareas desde los volcados de WhatsApp.

Por qué existe: `wa_tracker.py` ya vuelca WhatsApp (texto + voz transcrita + OCR) a
`00_FUENTE-DE-VERDAD/_PRIVADO_WHATSAPP/<chat>.md`, pero NADA lo leía para crear tareas. Este
script es el ESLABÓN que faltaba de "Vega recoge tareas sola de WhatsApp/voz → /calma" (petición
de {{TITULAR}}, 11-jul-26), SIN meter a Claude en el bucle de captura.

Diseño (clave — decisión de {{TITULAR}}, 12-jul): el carril determinista NO acierta, solo PROPONE.
El juez de calidad es **Vega** (el agente `asistente`, que es LLM):

    WhatsApp .md ──(pre-filtro determinista, barato)──▶ bandeja wa_candidatos.json
                                                          │
                                          Vega juzga (LLM) cada candidato
                                                          │
                                       solo las tareas REALES ──▶ /calma (por_confirmar)

Así el tablero NUNCA ve la charla (Vega la descarta antes), y es barato: el determinista le pasa a
Vega una lista corta, no los miles de mensajes crudos (que además no conviene que un LLM lea
enteros, por muro y coste). El determinista sobre-captura a propósito (recall alto); Vega pone la
precisión.

Qué hace (DETERMINISTA, sin LLM, sin tokens, sin red):
  · Recorre los `.md` de _PRIVADO_WHATSAPP/ (los produce wa_tracker; no reimplementa la ingesta).
  · Toma solo mensajes RECIENTES (ventana de días) y no reprocesa (watermark por chat).
  · Cada mensaje pasa por triage_tareas.clasificar (determinista; NUNCA obedece el texto).
  · Lo que NO es puro ruido (veredicto ≠ "no") se ESTACIONA en la bandeja (título de-identificado,
    chat, fuente). No crea tarjeta: eso lo hará Vega tras juzgar.

MURO / seguridad:
  · Leer el volcado local + escribir la bandeja local = NO es egress. Solo escribe en tools/state/.
  · Título de-identificado (deid.py) antes de estacionar (PII/clínico).
  · Excluye chats con pinta clínica/médica (defensa en profundidad).
  · La bandeja es privada (state/, gitignored). Cuando Vega ascienda una tarea real, entra como
    `por_confirmar` + `privado=True` (no sale por Telegram hasta que {{TITULAR}} la valide).
  · HALT-aware: con .HALT no escribe. El modo seco solo simula (0 efectos).
  · GATED: aunque el plist esté cargado, `--stage` es INERTE sin el OK de {{TITULAR}} (flag
    BTP_WA_COSECHA_OK=1 o fichero-toggle .claude/hooks/.wa_cosecha_on).

Uso:
  python3 tools/cosecha_whatsapp.py               # SECO: lista lo que estacionaría (no escribe)
  python3 tools/cosecha_whatsapp.py --dry         # idem
  python3 tools/cosecha_whatsapp.py --since 3     # ventana de 3 días (def. 2)
  python3 tools/cosecha_whatsapp.py --json        # salida JSON (para Vega / la rutina)
  python3 tools/cosecha_whatsapp.py --stage       # ESTACIONA en la bandeja (requiere gate + sin HALT)
"""
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento  # noqa: E402 — paths de casa base (REPO/STATE) + dedup (_slug/buscar_similar)
import triage_tareas  # noqa: E402 — clasificar() determinista reutilizado
try:
    import deid  # noqa: E402 — de-identificación de PII/clínico del título
except Exception:  # noqa: BLE001 — si falta, seguimos (la bandeja es privada y Vega revisa)
    deid = None

WA_DIR = os.path.join(seguimiento.REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_WHATSAPP")
STATE = getattr(seguimiento, "STATE", os.path.join(seguimiento.REPO, "tools", "state"))
WATERMARK = os.path.join(STATE, "wa_cosecha.json")
BANDEJA = os.path.join(STATE, "tareas", "wa_candidatos.json")  # inbox que juzga Vega

# Kill-switch HALT (mismo contrato que run_agent.sh / btp_dispatcher.sh). Override en tests.
HALT_FILES = os.environ.get("BTP_HALT_FILES", "").split(":") if os.environ.get("BTP_HALT_FILES") \
    else [os.path.join(seguimiento.REPO, ".HALT"), os.path.expanduser("~/.btp.HALT")]

# Encendido gated: inerte hasta el OK de {{TITULAR}}.
GATE_FLAG = "BTP_WA_COSECHA_OK"
GATE_FILE = os.path.join(seguimiento.REPO, ".claude", "hooks", ".wa_cosecha_on")

VENTANA_DIAS = 2  # solo mensajes de los últimos N días (batch diario)
MAX_BANDEJA = 300  # backstop: no dejar crecer la bandeja sin límite

# Chats a EXCLUIR por nombre (pinta clínica/médica → riesgo N2). Defensa en profundidad.
_RE_CHAT_EXCLUIR = re.compile(
    r"\bonc[oó]|hospital|cl[ií]nic|\bdr[a]?\.?\b|doctor|biopsi|radioter|enfermer|farmac|m[eé]dic|"
    r"{{CENTRO}}|dana.?farber|meseguer|{{CENTRO}}|vall.?d|{{CENTRO}}|contacto|contacto|contacto|c[aá]rdenas", re.I)

# Línea de mensaje del .md de wa_tracker:  **[dd/mm/yy HH:MM] quién:** contenido
_RE_MSG = re.compile(r"^\*\*\[(\d{2}/\d{2}/\d{2} \d{2}:\d{2})\]\s+(.+?):\*\*\s+(.*)$")
_MARCADORES = ("🎙️", "🖼️")
_AUDIO_PENDIENTE = "[audio no descargado"


def _halted():
    return any(p and os.path.exists(p) for p in HALT_FILES)


def _gate_abierto():
    return os.environ.get(GATE_FLAG) == "1" or os.path.exists(GATE_FILE)


def _load_json(ruta, default):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — ausente/corrupto → default
        return default


def _save_json(ruta, payload):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta)


def _parse_ts(s):
    try:
        return datetime.datetime.strptime(s, "%d/%m/%y %H:%M")
    except (ValueError, TypeError):
        return None


def _mensajes(ruta, desde):
    """[(ts, who, texto)] de mensajes con fecha >= `desde` (datetime)."""
    out = []
    try:
        with open(ruta, encoding="utf-8") as f:
            for raw in f:
                m = _RE_MSG.match(raw.rstrip("\n"))
                if not m:
                    continue
                ts = _parse_ts(m.group(1))
                if not ts or ts < desde:
                    continue
                texto = m.group(3).strip()
                for mk in _MARCADORES:
                    texto = texto.replace(mk, "").strip()
                if texto and not texto.startswith(_AUDIO_PENDIENTE):
                    out.append((ts, m.group(2).strip(), texto))
    except (OSError, UnicodeDecodeError):
        pass
    return out


def _deid(titulo):
    if not deid:
        return titulo
    try:
        return (deid.de_identificar(titulo) or titulo).strip()
    except Exception:  # noqa: BLE001 — si el de-id falla, la bandeja es privada y Vega revisa
        return titulo


def cosechar(ventana_dias=VENTANA_DIAS):
    """Escanea los volcados y devuelve (candidatos_nuevos, watermark_actualizado).
    Dedup contra los hilos ya existentes Y contra lo ya estacionado en la bandeja."""
    seg = seguimiento.load_seguimiento()
    existentes = {h.get("id") for h in seg.get("hilos", [])}
    ya_bandeja = {c.get("slug") for c in _load_json(BANDEJA, {}).get("candidatos", [])}
    desde_ventana = datetime.datetime.now() - datetime.timedelta(days=ventana_dias)
    wm = _load_json(WATERMARK, {})
    nuevo_wm = dict(wm)
    vistos_run = set()
    candidatos = []
    for ruta in sorted(glob.glob(os.path.join(WA_DIR, "*.md"))):
        chat = os.path.basename(ruta)[:-3]
        if _RE_CHAT_EXCLUIR.search(chat):
            continue
        last = _parse_ts(wm.get(chat))
        corte = max(desde_ventana, last) if last else desde_ventana
        max_ts = last
        for ts, who, texto in _mensajes(ruta, corte):
            if max_ts is None or ts > max_ts:
                max_ts = ts
            cl = triage_tareas.clasificar(texto, origen="whatsapp")
            if cl["veredicto"] == "no":
                continue
            campos = cl.get("campos", {})
            tit = _deid((campos.get("titulo") or texto))[:140].strip()
            if len(tit) < 6:
                continue
            slug = seguimiento._slug(tit)
            if slug in existentes or slug in ya_bandeja or slug in vistos_run:
                continue
            vistos_run.add(slug)
            candidatos.append({
                "titulo": tit, "slug": slug, "veredicto": cl["veredicto"],
                "etiqueta": campos.get("etiqueta") or "Gestión",
                "vence": campos.get("vence") or "",
                "prioridad": campos.get("prioridad") or "normal",
                "chat": chat, "who": who,
                "fuente": "whatsapp:%s @ %s" % (chat, ts.strftime("%d/%m/%y %H:%M")),
            })
        if max_ts is not None:
            nuevo_wm[chat] = max_ts.strftime("%d/%m/%y %H:%M")
    return candidatos, nuevo_wm


def estacionar(candidatos, nuevo_wm):
    """Añade los candidatos a la bandeja que juzga Vega (dedup por slug). Avanza el watermark.
    NO crea tarjetas — eso lo hace Vega tras juzgar. Devuelve cuántos se estacionaron."""
    banj = _load_json(BANDEJA, {"candidatos": []})
    if not isinstance(banj.get("candidatos"), list):
        banj = {"candidatos": []}
    existentes = {c.get("slug") for c in banj["candidatos"]}
    n = 0
    for c in candidatos:
        if c["slug"] in existentes:
            continue
        banj["candidatos"].append(c)
        existentes.add(c["slug"])
        n += 1
    # backstop anti-crecimiento: nos quedamos con los más recientes
    banj["candidatos"] = banj["candidatos"][-MAX_BANDEJA:]
    banj["actualizado"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    _save_json(BANDEJA, banj)
    _save_json(WATERMARK, nuevo_wm)
    return n


def main(argv):
    stage = "--stage" in argv or "--write" in argv
    as_json = "--json" in argv
    ventana = VENTANA_DIAS
    if "--since" in argv:
        i = argv.index("--since")
        if i + 1 < len(argv):
            try:
                ventana = int(argv[i + 1])
            except ValueError:
                pass

    halt = _halted()
    if stage and halt:
        print("MURO: HALT activo → no estaciono (cosecha_whatsapp).", file=sys.stderr)
        return 0
    if stage and not _gate_abierto():
        print("⛔ Gate cerrado: activa %s=1 o crea %s para permitir --stage." % (
            GATE_FLAG, os.path.relpath(GATE_FILE, seguimiento.REPO)), file=sys.stderr)
        return 0

    candidatos, nuevo_wm = cosechar(ventana)

    if as_json:
        salida = {"n": len(candidatos), "ventana_dias": ventana, "candidatos": candidatos,
                  "halt": halt, "gate": _gate_abierto()}
        if stage:
            salida["estacionados"] = estacionar(candidatos, nuevo_wm)
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        return 0

    if halt and not stage:
        print("⚠️  HALT activo: un run real NO habría corrido. Solo simulación (nada escrito).\n")
    if not candidatos:
        print("✅ Sin candidatos nuevos en WhatsApp (ventana %d días)." % ventana)
        return 0
    print("💬 %d candidato(s) para que Vega juzgue (ventana %d días)%s:\n" % (
        len(candidatos), ventana, "" if stage else "  (SECO — usa --stage para estacionar)"))
    for c in candidatos:
        plazo = ("  ⏰ %s" % c["vence"]) if c["vence"] else ""
        print("  • [%s/%s%s] %s\n      ↳ %s" % (
            c["veredicto"], c["etiqueta"], plazo, c["titulo"][:90], c["fuente"]))
    if stage:
        n = estacionar(candidatos, nuevo_wm)
        print("\n→ Estacionados en la bandeja de Vega: %d (Vega juzga y asciende los reales)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
