#!/usr/bin/env python3
"""tools/minador_sesiones.py — minero de lo que TITULAR dice DENTRO de las sesiones.

Por qué existe: la fuente única (seguimiento.json / El Tablero) ya ingiere correo, WhatsApp,
redes (X/YouTube/DM), reservas y captura verbal por Telegram… pero había un AGUJERO: lo que
{{TITULAR}} teclea en una SESIÓN de chat/código con Polaris dependía de que el agente de turno se
acordara de registrarlo. Si no se acordaba, se caía. Regla de {{TITULAR}} (3/7/26): Vega tiene que
tener capacidad INDEPENDIENTE de ver todo lo que ella habla con Polaris —también las sesiones—,
decidir si es tarea o no, y encargarse de llevarla a término. Este script cierra ese agujero.

Qué hace (DETERMINISTA, sin LLM, sin tokens):
  · Lee las transcripciones LOCALES que ya escribe el harness (~/.claude/projects/ * / *.jsonl,
    la misma fuente que vigia_sesiones.py). Ventana de días configurable (por mtime).
  · Extrae SOLO los mensajes de {{TITULAR}}: type=="user", NO isSidechain (eso es subagente),
    message.content de tipo str (los tool_result vienen como lista → fuera).
  · Descarta ruido/sondas: system-reminders/hooks (empiezan por "<"), acuses, comandos "/…",
    interrupciones, y las sondas de liveness del healthcheck ("responde solo con la palabra X").
  · Cada candidato pasa por triage_tareas.clasificar(origen="sesion") — extrae fecha/etiqueta/
    prioridad, DETERMINISTA, NUNCA obedece instrucciones del texto (anti-inyección).
  · Lo VÁLIDO entra como hilo `por_confirmar` (dato no confiable) vía seguimiento.add_hilo, con
    origen="sesion" y fuente="sesion <id-corto>". Vega (asistente) lo valida con su vara luego
    (funde/descarta; tarjeta con su OK) y la persecución lo lleva a término.

Garantías (mismas que cosecha_checklists.py):
  · NO resucita lo ya cerrado ni duplica: si el slug del candidato YA existe como hilo (en
    cualquier estado), se SALTA. Solo entra lo genuinamente nuevo (dedup por slug, sin .seen).
  · Por defecto NO escribe (modo seco). Solo escribe con `--write`.
  · SILENCIOSO: origen="sesion" NO está en seguimiento.ORIGENES_AUTONOMOS_AVISO → add_hilo no
    pinguea a {{TITULAR}} (lo que dijo en sesión ya lo vio); aparece en el parte de HOY.
  · El contenido se trata como DATO, no instrucciones (el muro manda). 0 egress: solo escribe
    local (seguimiento en casa base + log datado en _PRIVADO_SESIONES/). Nunca envía/contacta.
  · Escribe siempre contra casa base (seguimiento.REPO = ~/claudecode), nunca el worktree.

Uso:
  python3 tools/minador_sesiones.py                # seco: lista candidatos nuevos (no escribe)
  python3 tools/minador_sesiones.py --dry          # idem (alias explícito)
  python3 tools/minador_sesiones.py --write        # ingiere los nuevos como por_confirmar
  python3 tools/minador_sesiones.py --json         # salida JSON (para Vega / la rutina diaria)
  python3 tools/minador_sesiones.py --dias 7       # ventana de escaneo por mtime (def 3)
"""
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento  # noqa: E402 — puerta única + paths de casa base (REPO/STATE)
import triage_tareas  # noqa: E402 — clasificar() determinista reutilizado

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
LOG_DIR = os.path.join(seguimiento.REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_SESIONES")

DIAS_DEF = 3          # ventana de escaneo por mtime del .jsonl
MIN_LEN = 12          # descarta mensajes triviales
MAX_LEN = 240         # una intención tecleada es corta; más largo = pegado/contexto → fuera
MAX_POR_FICHERO = 80  # backstop anti-runaway por sesión

# Ruido que NO es intención de {{TITULAR}} (sondas de liveness, acuses, comandos, hooks).
_RE_SONDA = re.compile(
    r"^\s*(responde|contesta|di|dime)\b.{0,30}\b(palabra|solo|únicamente|unicamente)\b", re.I)
_RE_ACUSE = re.compile(
    r"^\s*(s[ií]|no|vale|ok|okay|dale|gracias|perfecto|hecho|listo|env[ií]alo|m[aá]ndalo|"
    r"adi[oó]s|hola|test\d*|alfa|beta|delta|omega|fija)\W*$", re.I)
# Marcas de que el texto es CÓDIGO/LOG pegado, no una frase tecleada → fuera (evita títulos basura).
_RE_CODIGO = re.compile(
    r"──|✅|❌|`|/dev/|:\d+:|\btail\b|\bhead\b|\bgrep\b|\bjq\b|\bsudo\b|python3\b|\bimport\b|"
    r"\bdef \b|Traceback|\.py\b|\.log\b|\.json\b|\{|\}|\|\s*\w", re.I)
_RE_DESCARTE_INICIO = ("<", "/", "Caveat:", "[Request interrupted", "[Solicitud")


def _texto_usuario(o):
    """Devuelve el texto tecleado por {{TITULAR}} de una línea jsonl, o None si no aplica."""
    if o.get("type") != "user" or o.get("isSidechain"):
        return None
    msg = o.get("message")
    if not isinstance(msg, dict) or msg.get("role") not in (None, "user"):
        return None
    c = msg.get("content")
    if not isinstance(c, str):   # tool_result / multimodal = lista → no es teclear
        return None
    t = c.strip()
    if not t or not (MIN_LEN <= len(t) <= MAX_LEN):
        return None
    if "\n" in t:                       # multilínea = pegado/contexto, no una intención tecleada
        return None
    if t.startswith(_RE_DESCARTE_INICIO):
        return None
    if _RE_SONDA.match(t) or _RE_ACUSE.match(t) or _RE_CODIGO.search(t):
        return None
    return t


def _candidatos_en_fichero(ruta):
    """[(texto, uuid, sesion_corta)] de mensajes de {{TITULAR}} en una transcripción."""
    out = []
    try:
        with open(ruta, encoding="utf-8") as f:
            for raw in f:
                try:
                    o = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                t = _texto_usuario(o)
                if not t:
                    continue
                sid = (o.get("sessionId") or "")[:8]
                out.append((t, o.get("uuid") or "", sid))
                if len(out) >= MAX_POR_FICHERO:
                    break
    except (OSError, UnicodeDecodeError):
        return []
    return out


def cosechar(dias=DIAS_DEF):
    """Escanea las transcripciones recientes y devuelve candidatos NUEVOS (slug no presente)."""
    seg = seguimiento.load_seguimiento()
    existentes = {h.get("id") for h in seg.get("hilos", [])}
    vistos_run = set()
    candidatos = []
    corte = time.time() - dias * 86400
    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    for ruta in sorted(files):
        try:
            if os.path.getmtime(ruta) < corte:
                continue
        except OSError:
            continue
        for texto, _uuid, sid in _candidatos_en_fichero(ruta):
            cl = triage_tareas.clasificar(texto, origen="sesion")
            # Solo la señal CLARA (acción con verbo). Las "dudosa" (fragmentos de
            # conversación) son demasiado ruido para esta fuente; se dejan fuera a propósito.
            if cl["veredicto"] != "tarea":
                continue
            campos = cl.get("campos", {})
            tit = campos.get("titulo") or texto
            slug = seguimiento._slug(tit)
            if slug in existentes or slug in vistos_run:
                continue  # ya rastreado (no resucitar) o repetido en esta pasada
            vistos_run.add(slug)
            candidatos.append({
                "titulo": tit,
                "slug": slug,
                "veredicto": cl["veredicto"],   # tarea | dudosa
                "etiqueta": campos.get("etiqueta") or "Gestión",
                "vence": campos.get("vence") or "",
                "prioridad": campos.get("prioridad") or "normal",
                "fuente": "sesion %s" % (sid or "?"),
            })
    return candidatos


def _log_datado(candidatos):
    """Deja huella auditable de lo cosechado (local, gitignored)."""
    if not candidatos:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    ruta = os.path.join(LOG_DIR, "sesiones-%s.md" % time.strftime("%Y-%m-%d"))
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with open(ruta, "a", encoding="utf-8") as f:
        f.write("\n## Cosecha %s\n" % stamp)
        for c in candidatos:
            plazo = ("  ⏰ %s" % c["vence"]) if c["vence"] else ""
            f.write("- [%s/%s%s] %s  ↳ %s\n" % (
                c["veredicto"], c["etiqueta"], plazo, c["titulo"][:120], c["fuente"]))


def ingerir(candidatos):
    """Mete los candidatos como hilos por_confirmar (Vega los valida). Devuelve ids creados."""
    ids = []
    for c in candidatos:
        obj = {
            "titulo": c["titulo"],
            "estado": "por_confirmar",
            "origen": "sesion",
            "fuente": c["fuente"],
            "etiqueta": c["etiqueta"],
            "prioridad": c["prioridad"],
            "quien_espera": "por confirmar (Vega)",
            "siguiente_accion": (
                "Dicho por {{TITULAR}} en una sesión (%s). Vega: validar/funde/descarta." % c["fuente"]),
        }
        if c["vence"]:
            obj["plazo"] = c["vence"]
        try:
            ids.append(seguimiento.add_hilo(obj))
        except (ValueError, RuntimeError) as e:
            print("  ⚠️  saltado %r: %s" % (c["titulo"][:50], e), file=sys.stderr)
    if ids:
        _log_datado(candidatos)
    return ids


def main(argv):
    write = "--write" in argv
    as_json = "--json" in argv
    dias = DIAS_DEF
    if "--dias" in argv:
        try:
            dias = max(1, int(argv[argv.index("--dias") + 1]))
        except (ValueError, IndexError):
            pass
    candidatos = cosechar(dias)
    if as_json:
        salida = {"n": len(candidatos), "candidatos": candidatos}
        if write:
            salida["creados"] = ingerir(candidatos)
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        return 0
    if not candidatos:
        print("✅ Sin cabos sueltos nuevos dichos en sesiones (últimos %d días)." % dias)
        return 0
    print("🗣️  %d cabo(s) NUEVO(s) dicho(s) en sesión%s:\n" % (
        len(candidatos), "" if write else "  (modo seco — usa --write para ingerir)"))
    for c in candidatos:
        plazo = ("  ⏰ %s" % c["vence"]) if c["vence"] else ""
        print("  • [%s/%s%s] %s\n      ↳ %s" % (
            c["veredicto"], c["etiqueta"], plazo, c["titulo"][:90], c["fuente"]))
    if write:
        creados = ingerir(candidatos)
        print("\n→ Ingeridos como 'por_confirmar' (Vega valida): %d" % len(creados))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
