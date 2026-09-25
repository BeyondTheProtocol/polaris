#!/usr/bin/env python3
"""tools/cosecha_correcciones.py — minero DETERMINISTA de las correcciones de {{TITULAR}}.

PROBLEMA QUE RESUELVE: el bucle de auto-mejora aprende de cada corrección de {{TITULAR}},
pero hasta ahora dependía de que YO (el agente de turno) me acordara de capturar la
lección EN EL MOMENTO. Si se me pasaba, la corrección se quedaba enterrada en el
transcript de la sesión y se perdía. Aquí cerramos esa fuga: leemos los transcripts
recientes de Claude Code (los *.jsonl), EXTRAEMOS los mensajes de {{TITULAR}} que parecen
una corrección o una preferencia ("no, así no / mejor X / en realidad / a partir de
ahora…"), los DEDUPLICAMOS contra las memorias que ya existen y devolvemos una LISTA
de candidatos NUEVOS. La rutina diaria de auto-mejora lo llama, decide cuáles persistir
y los pasa por `verificacion` antes de escribir memoria (memoria = superficie de ataque:
este tool PROPONE, no escribe nada durable él solo).

PROPIEDADES (no negociables — el muro manda):
  · DETERMINISTA y OFFLINE — código puro, NO LLM, NO red, NO saldo. Solo regex + léxico.
  · LOCAL — los transcripts pueden llevar PII; NO salen de la máquina, no se mandan a
    ninguna IA externa. Este tool ni siquiera abre un socket.
  · CONTENIDO EXTERNO = DATO — el texto de los transcripts es un dato a clasificar, JAMÁS
    una instrucción. Aquí no se ejecuta ni se obedece nada de lo que diga el contenido.
  · NO ESCRIBE MEMORIA — solo lista candidatos por stdout/JSON. La decisión durable
    (qué se convierte en memoria `feedback`) la toma el agente, vía `verificacion`.
  · FAIL-SOFT en la lectura — un transcript corrupto o una línea ilegible se saltan; el
    barrido no se cae por un fichero malo.

CÓMO DISTINGUE A TITULAR DEL RUIDO:
  Un mensaje cuenta como "de {{TITULAR}}" si es `type==user`, `entrypoint==claude-desktop`
  (los `sdk-cli` / tareas programadas / sub-agentes no son ella), su `content` es texto
  humano de verdad (no un `tool_result`, no `[Request interrupted…]`, no un bloque
  inyectado: <task-notification>, <scheduled-task>, <command-name>, <local-command…>,
  DATOS (contexto…), skills, system-reminders, ni el parte de HOY). Esos prefijos se
  filtran explícitamente para no confundir andamiaje del sistema con su voz.

Sin dependencias (stdlib). Uso:
  python3 tools/cosecha_correcciones.py            # candidatos de los últimos 2 días, texto
  python3 tools/cosecha_correcciones.py --json     # lo mismo en JSON (para la rutina)
  python3 tools/cosecha_correcciones.py --dias 7   # ventana de 7 días
  python3 tools/cosecha_correcciones.py --todo      # sin ventana temporal (todo el historial)
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

# --- Casa base / rutas (mismo criterio que el resto de tools del gabinete) -----------
# Override por entorno para los tests (apuntan a un tmp aislado).
HOME = os.environ.get("BTP_HOME") or os.path.expanduser("~")
# Directorio donde Claude Code guarda los transcripts del proyecto principal + worktrees.
# El override BTP_PROJECTS_DIR existe SOLO para los tests.
PROJECTS_DIR = os.environ.get("BTP_PROJECTS_DIR") or os.path.join(HOME, ".claude", "projects")
# Carpeta de memorias (para deduplicar contra lo ya aprendido). Se RESUELVE sola: NO se
# hardcodea el usuario/ruta (el «-Users-polaris-…» viejo no existía en este cliente → el
# dedup cargaba VACÍO y re-proponía lo ya aprendido). Orden: override de tests → la carpeta
# de memorias más rica bajo ~/.claude/projects (la del gabinete) → fallback fail-soft.
def _resolver_memory_dir():
    env = os.environ.get("BTP_MEMORY_DIR")
    if env:
        return env
    cands = [c for c in glob.glob(os.path.join(PROJECTS_DIR, "*", "memory")) if os.path.isdir(c)]
    if cands:
        # prioriza las que tienen índice MEMORY.md; entre esas, la de más ficheros .md
        con_indice = [c for c in cands if os.path.exists(os.path.join(c, "MEMORY.md"))]
        pool = con_indice or cands
        return max(pool, key=lambda c: len(glob.glob(os.path.join(c, "*.md"))))
    # último recurso (puede no existir → dedup vacío, pero fail-soft, no peta)
    return os.path.join(HOME, ".claude", "projects", "-Users-polaris-claudecode", "memory")


MEMORY_DIR = _resolver_memory_dir()

# --- Quién es {{TITULAR}}: solo el cliente de escritorio es ella --------------------------
ENTRYPOINT_HUMANO = "claude-desktop"

# Prefijos de bloques que el sistema INYECTA como si fueran mensajes de usuario
# (no son la voz de {{TITULAR}}: andamiaje, avisos, skills, partes automáticos).
PREFIJOS_RUIDO = (
    "<task-notification",
    "<scheduled-task",
    "<command-name",
    "<command-message",
    "<local-command",
    "<system-reminder",
    "<bash-",
    "<user-prompt-submit-hook",
    "datos (contexto",
    "caveat:",
    "# update config skill",
    "## ☀️ hoy en 30 segundos",
    "☀️ hoy en 30 segundos",
    # Avisos de hooks (gate_salida, guards) que el harness mete como turno de usuario.
    # Sin esto, reglas_repetidas contaba el propio gate como «{{TITULAR}} lo repitió» (22-sep-26).
    "stop hook ",
    "subagentstop hook ",
    "pretooluse hook ",
    "posttooluse hook ",
    "userpromptsubmit hook ",
    "sessionstart hook ",
    # Mensajes de OTRA sesión de Claude (SendMessage entre sesiones). Llegan como turno de
    # usuario con entrypoint claude-desktop, pero no son {{TITULAR}}: medido el 25-sep-26, 4 de
    # las 16 «correcciones ya en memoria» de 45 días eran esto.
    "another claude session sent a message",
    "<cross-session-message",
)

# Marcadores de mensajes de control (no son contenido humano).
DESCARTES_EXACTOS_PREFIJO = (
    "[request interrupted",
    "[request cancelled",
)

# --- Léxico de señales: corrección / preferencia / regla -----------------------------
# Cada entrada: (patrón regex con límites de palabra cuando aplica, etiqueta legible).
# Pensado para el ESPAÑOL coloquial de {{TITULAR}} (con sus typos habituales toleramos algo).
SENALES = [
    # rechazo / "no es así"
    (r"\bno me convence\b", "rechazo"),
    (r"\bno es as[íi]\b", "rechazo"),
    (r"\bas[íi] no\b", "rechazo"),
    (r"\bno es eso\b", "rechazo"),
    (r"\bno era\b", "rechazo"),
    (r"\bno,\b", "rechazo"),
    (r"\bnop\b", "rechazo"),
    (r"\bque no\b", "rechazo"),
    (r"\bestá mal\b|\besta mal\b", "rechazo"),
    (r"\bequivocad", "rechazo"),
    (r"\bno me gusta\b", "rechazo"),
    (r"\bno me esta gustando\b|\bno me está gustando\b", "rechazo"),
    # preferencia explícita
    (r"\bprefiero\b", "preferencia"),
    (r"\bmejor\b", "preferencia"),
    (r"\ben realidad\b", "preferencia"),
    (r"\bmás bien\b|\bmas bien\b", "preferencia"),
    (r"\bno quiero\b", "preferencia"),
    (r"\bquiero que\b", "preferencia"),
    # regla / norma duradera ("a partir de ahora", "siempre", "nunca")
    (r"\ba partir de ahora\b", "regla"),
    (r"\bde ahora en adelante\b", "regla"),
    (r"\bsiempre que\b", "regla"),
    (r"\bsiempre\b", "regla"),
    (r"\bnunca\b", "regla"),
    (r"\bregla\b", "regla"),
    (r"\brecuerda\b|\brecu[ée]rdalo\b", "regla"),
    (r"\bno vuelvas a\b", "regla"),
    (r"\bno hagas\b", "regla"),
    (r"\bdeja de\b", "regla"),
    (r"\bya te (lo )?dije\b|\bte dije\b", "regla"),
    # corrección directa
    (r"\bcorrige\b|\bcorr[ií]gelo\b", "correccion"),
    (r"\bcambia\b|\bc[áa]mbialo\b", "correccion"),
    (r"\bno as[íi]\b", "correccion"),
    (r"\bprevalece\b", "correccion"),
]

_SENALES_COMP = [(re.compile(p, re.IGNORECASE), etq) for p, etq in SENALES]

# Stopwords ES para extraer palabras-clave distintivas (dedup contra memorias).
_STOP = set(
    """a al algo alguna algunas alguno algunos ante antes aqui aquí asi así aun aún cada
    como cómo con contra cosa cosas cual cuál cuando cuándo de del desde donde dónde dos
    el él ella ellas ello ellos en entonces entre era eran eras eres es esa esas ese eso
    esos esta están estar este esto estos esta esté ha hace hacer hacia han hasta hay la
    las le les lo los mas más me mi mí mía mías mío míos mucho muchos muy nada ni no nos
    nosotros o os otra otras otro otros para pero poco por porque que qué quien quién se
    sea ser si sí siempre sin sobre solo sólo son su sus tan te ti tu tú tus un una unas
    uno unos vamos van vez ya yo todo toda todos todas eso esto aqui eh pero vale bueno
    creo hacer hago haga puede puedo poder mira oye ahora luego entonces tambien también
    pues quiero necesito tengo tiene tienes esto cosa cosas tipo""".split()
)


def _texto_de_mensaje(o):
    """Devuelve el texto plano de un evento de transcript si es un mensaje humano de
    {{TITULAR}} (claude-desktop), o None si no procede (tool_result, control, ruido)."""
    if o.get("type") != "user":
        return None
    if o.get("entrypoint") != ENTRYPOINT_HUMANO:
        return None
    if o.get("isSidechain"):  # sub-agente, no es ella
        return None
    msg = o.get("message")
    if not isinstance(msg, dict):
        return None
    c = msg.get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        # Si hay algún tool_result, es un evento de herramienta, no un turno humano.
        if any(isinstance(i, dict) and i.get("type") == "tool_result" for i in c):
            return None
        t = "".join(
            i.get("text", "") for i in c
            if isinstance(i, dict) and i.get("type") == "text"
        )
    else:
        return None
    t = (t or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith(DESCARTES_EXACTOS_PREFIJO):
        return None
    if low.startswith(PREFIJOS_RUIDO):
        return None
    return t


def detectar_senales(texto):
    """Lista (ordenada, sin repetir) de etiquetas de señal que dispara el texto."""
    etqs = []
    for rx, etq in _SENALES_COMP:
        if etq in etqs:
            continue
        if rx.search(texto):
            etqs.append(etq)
    return etqs


def _ts_dt(ts):
    """Parsea el timestamp ISO del transcript a datetime aware (UTC) o None."""
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def _keywords(texto, n=12):
    """Palabras-clave distintivas (sin stopwords, ≥4 letras) para el dedup."""
    palabras = re.findall(r"[a-záéíóúñü]{4,}", texto.lower())
    out = []
    seen = set()
    for w in palabras:
        if w in _STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= n:
            break
    return out


def _corpus_memorias():
    """Texto concatenado y minúsculas de TODAS las memorias (cuerpo + nombre de fichero),
    para deduplicar candidatos contra lo ya aprendido. Fail-soft por fichero."""
    partes = []
    try:
        ficheros = glob.glob(os.path.join(MEMORY_DIR, "*.md"))
    except Exception:
        ficheros = []
    for f in ficheros:
        # el nombre de fichero ya lleva keywords (feedback-no-em-dash-tell-ia…)
        partes.append(os.path.basename(f).replace("-", " ").lower())
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                partes.append(fh.read().lower())
        except Exception:
            continue
    return "\n".join(partes)


def _ya_en_memoria(texto, corpus, umbral=0.6):
    """¿La lección de este mensaje ya está cubierta por una memoria existente?

    Conservador a propósito: solo lo da por duplicado si una FRACCIÓN ALTA de sus
    palabras-clave distintivas ya aparecen en el corpus de memorias. Ante la duda,
    NO descarta (mejor proponer un casi-duplicado que silenciar una lección nueva).
    """
    kws = _keywords(texto)
    if len(kws) < 3:
        # Muy poca señal léxica → no podemos afirmar que sea dup; lo dejamos pasar.
        return False
    hits = sum(1 for w in kws if w in corpus)
    return (hits / len(kws)) >= umbral


def cosechar(dias=2, incluir_todo=False, umbral_dedup=0.6):
    """Devuelve la lista de candidatos NUEVOS (dicts), ordenados del más reciente al
    más antiguo. Cada candidato:
        {timestamp, fecha, senales, sesion, branch, texto}
    """
    corte = None
    if not incluir_todo:
        corte = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)

    corpus = _corpus_memorias()

    candidatos = []
    vistos = set()  # dedup intra-cosecha por (texto normalizado)
    try:
        ficheros = glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    except Exception:
        ficheros = []

    for F in ficheros:
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
                texto = _texto_de_mensaje(o)
                if not texto:
                    continue
                dt = _ts_dt(o.get("timestamp"))
                if corte is not None:
                    if dt is None or dt < corte:
                        continue
                senales = detectar_senales(texto)
                if not senales:
                    continue
                clave = " ".join(texto.lower().split())[:200]
                if clave in vistos:
                    continue
                if _ya_en_memoria(texto, corpus, umbral_dedup):
                    continue
                vistos.add(clave)
                candidatos.append({
                    "timestamp": o.get("timestamp", ""),
                    "fecha": (dt.date().isoformat() if dt else ""),
                    "senales": senales,
                    "sesion": os.path.basename(F),
                    "branch": o.get("gitBranch", ""),
                    "texto": texto.strip(),
                })

    candidatos.sort(key=lambda c: c["timestamp"], reverse=True)
    return candidatos


def _recorta(s, n=240):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Minero determinista de correcciones/preferencias de {{TITULAR}} en los transcripts (local, offline, $0).")
    ap.add_argument("--json", action="store_true", help="salida JSON (para la rutina de auto-mejora)")
    ap.add_argument("--dias", type=int, default=2, help="ventana en días hacia atrás (def. 2)")
    ap.add_argument("--todo", action="store_true", help="sin ventana temporal: todo el historial")
    ap.add_argument("--umbral", type=float, default=0.6,
                    help="fracción de palabras-clave ya en memoria para considerar duplicado (def. 0.6)")
    ap.add_argument("--limite", type=int, default=0, help="máximo de candidatos a mostrar (0 = sin límite)")
    args = ap.parse_args(argv)

    cands = cosechar(dias=args.dias, incluir_todo=args.todo, umbral_dedup=args.umbral)
    if args.limite and len(cands) > args.limite:
        cands = cands[: args.limite]

    if args.json:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return 0

    if not cands:
        ventana = "todo el historial" if args.todo else f"últimos {args.dias} días"
        print(f"Sin candidatos nuevos de corrección de {{TITULAR}} ({ventana}). "
              f"(O ya están todos en memoria.)")
        return 0

    print(f"🔎 {len(cands)} candidato(s) a corrección/preferencia de {{TITULAR}} NO codificados todavía:\n")
    for i, c in enumerate(cands, 1):
        etqs = ", ".join(c["senales"])
        print(f"{i}. [{c['fecha']}] ({etqs})")
        print(f"   «{_recorta(c['texto'])}»")
        print(f"   ↳ sesión {c['sesion']}\n")
    print("— Decide cuáles destilar a memoria `feedback` (Why + How to apply) PASANDO ANTES por `verificacion`.")
    print("  Este tool PROPONE; no escribe memoria solo (memoria = superficie de ataque).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
