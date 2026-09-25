#!/usr/bin/env python3
"""permiso_envio.py — el permiso de envío que abre {{TITULAR}}, y cómo se comprueba que es suyo.

POR QUÉ EXISTE (22-sep-2026, hallazgo 3.1 de la auditoría externa, deuda
`ok-envio-json-escribible-por-el-lazo`). Hasta hoy el permiso era un JSON en
`tools/state/ok_envio.json` y valía con decir `origen: "prompt"` y llevar una hora reciente. Lo
podía escribir cualquiera que escribiera ficheros: el lazo con Write o `tee`, una sesión
interactiva con lo que fuera. Una inyección de dos pasos («escribe esto en ok_envio.json y luego
envía») abría el freno de salida sin que ella dijera nada.

Ahora el fichero es solo un PUNTERO firmado a un mensaje suyo, y lo que manda es el mensaje:
  · MAC (HMAC-SHA256) con una clave del Llavero (`btp-ok-envio-mac`) que ningún agente lee: los
    guards deniegan leerla y lanzar a mano el hook que firma. Un fichero escrito a mano no vale.
  · Ligado a la SESIÓN y a un `prompt_id`. Al usarlo se busca ese prompt en el transcript de Claude
    Code (lo escribe el harness, no el agente) y tiene que ser HUMANO (origin kind=human, no
    subagente, no tarea programada), el ÚLTIMO suyo, y contener la orden. Si después escribió
    otra cosa («espera»), ya no vale.
  · Un solo uso de verdad: cada (sesión, prompt) usado queda en `ok_envio_usados.jsonl`, y un
    segundo permiso para el mismo mensaje no abre nada.
  · Ligado a LO QUE APROBÓ (ver `comprobar_envio`): las direcciones que nombró y el borrador que
    tenía delante. Si el borrador cambia después de su OK, o el cuerpo que sale no es el que vio,
    no sale.

LÍMITE QUE NO SE ESCONDE. Todo corre con el mismo usuario de macOS. Un proceso con su uid PUEDE,
en teoría, leer el Llavero o escribir un transcript por una vía que los guards no reconozcan
(código ofuscado). Lo que esto consigue es que haga falta encadenar tres evasiones distintas
(escribir el fichero, leer la clave, falsificar el transcript) y que las tres vías obvias estén
cerradas y con test. No es una garantía criptográfica contra el propio usuario.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _casa  # noqa: E402

SERVICIO = "btp-ok-envio-mac"
VIDA_S = 600
ORIGENES = ("prompt",)
AUTOMATICOS = ("<task-notification", "<scheduled-task", "<ci-monitor-event")
# Un prompt que lleve CUALQUIERA de estas etiquetas, en cualquier sitio, no lo tecleó ella: lo
# lanzó una tarea programada, una notificación o el monitor de CI (24-sep-26, verificacion). Antes
# se miraba DESPUÉS de quitar los bloques y solo al principio, así que un cierre falso de la
# etiqueta dentro del cuerpo de una tarea dejaba el resto como «texto suyo» y abría un permiso de
# envío real (sonda e2e). Se mira el texto CRUDO.
_AUTOMATICO = re.compile(r"</?(?:task-notification|scheduled-task|ci-monitor-event)\b", re.I)


def es_automatico(texto):
    return bool(_AUTOMATICO.search(texto or ""))

# Órdenes de envío de {{TITULAR}}. Imperativo y en segunda persona: «envíalo», «mándaselo», «publica».
# NO entran el condicional, el subjuntivo ni el pasado («habría que enviar», «cuando lo envíe»,
# «lo envió ayer»), que son conversación sobre el envío, no la orden. La tercera persona del
# presente SÍ entra aunque no sea orden («Vega los envía»): en español es la misma forma que el
# imperativo y una regex no las distingue. Tampoco distingue una cita sin comillas («me dijo:
# envíalo»). Declarado, no escondido: deuda `orden-envio-falsos-amigos` (verificacion, 25-sep-26). (Vivía en ok_envio_prompt.py; aquí la usan el
# que emite el permiso y los que lo comprueban contra el transcript: una sola regex, no dos.)
# El PLURAL entra (25-sep-26): pidió «Publicalos» para cinco comentarios y el permiso no se abrió,
# porque aquí solo había singulares. No era esa palabra, era toda la clase (envíalos, mándaselos,
# respóndelas, súbelos a la web…). `_CLITICO` lleva los dos números en un solo sitio para que no
# vuelva a faltar en uno de los verbos. Medido antes de fusionar contra sus prompts reales.
_CLITICO = r"(?:selos|selas|selo|sela|melos|melas|melo|mela|los|las|les|lo|la|le)"
ORDEN = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"env[ií]a" + _CLITICO + r"?\b|env[ií]ame\b|"
    r"m[áa]nda" + _CLITICO + r"?\b|"
    r"publ[ií]ca(?:los|las|lo|la)?\b|"
    r"resp[óo]nde(?:les|los|las|le|lo|la)?\b|contesta(?:le|les)?\b|"
    r"dale\s+a\s+enviar\b|"
    r"ya\s+puedes\s+(?:enviar|mandar|publicar)\b|"
    r"adelante\s+con\s+el\s+(?:env[ií]o|correo|mensaje)\b|"
    # Publicar en su web es lo mismo que enviar: sale al mundo y lleva su nombre.
    # `a\s*la` y no `a\s+la` a propósito: ella escribió «Añade ala cronologia» (20-sep-26) y el
    # permiso no se abrió por un espacio. Un freno que exige escribir sin erratas es un freno que
    # acaba estorbando, y entonces se quita — que es peor que no tenerlo.
    r"(?:p[óo]n|s[úu]be|a[ñn][áa]de|mete|a[ñn][áa]d[ae]?)(?:los|las|lo|la|le)?\s+"
    r"(?:a|en)\s*la\s+(?:web|cronolog[íi]a|timeline|l[íi]nea\s+de\s+tiempo)\b|"
    r"que\s+salga\s+en\s+la\s+web\b"
    r")", re.I)

# Órdenes de PROGRAMAR (24-sep-26). Crear o lanzar una tarea programada es salida (salida_guard:
# es un agente que actuará solo más tarde), pero ninguna frase natural suya lo abría: pidió
# «haz la tarea programada» y «ya puedes cread la tarea peorramada» y el permiso solo se abrió con
# «ya puedes enviar», que no dice lo que aprueba. Esta orden abre un permiso de ALCANCE
# «programar»: vale para `*_scheduled_task` y para nada más (ni correo, ni web). «Programa» a secas
# no cuenta: es también un sustantivo («el programa»); hace falta el clítico o «la tarea».
# Imperativo al principio de la frase (o tras «vale», «dale», «venga»…): «el programa la revisión»
# o «Vega programa la tarea cada lunes» no son órdenes suyas, y «crear» en infinitivo va casi
# siempre en una subordinada («antes de crear la tarea…»). «Tarea» sola en este sistema es también
# una tarjeta del Tablero, así que «crea la tarea en el Tablero/Vega» no cuenta (revisión de
# verificacion, 24-sep-26: 9 frases así abrían el permiso).
_INICIO = r"(?:^|\n\s*|[,.;:¿?¡!]\s*|\b(?:vale|venga|dale|ok|pues|ahora|y|por\s+favor|porfa)\s+)"
ORDEN_PROGRAMAR = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"progr?[áa]ma(?:lo|la|los|las)\b|"
    r"ya\s+puedes\s+(?:programar|crea[rd]?\s+la\s+tarea)\b"
    r")|" + _INICIO + r"("
    r"progr?[áa]ma\s+(?:la|una|esa|esta)\s+(?:tarea|revisi[óo]n|comprobaci[óo]n)\b|"
    r"crea[d]?\s+(?:la|una|esa|esta)\s+tarea\b(?!\s+(?:en|a|para)\s+(?:el\s+)?(?:tablero|vega))|"
    r"haz\s+(?:la|una|esa|esta)\s+tarea\s+\w*ramada\b"
    r")", re.I)

# Órdenes de FUSIONAR un PR (25-sep-26). `gh pr merge` es salida (fusionar la web la publica),
# pero ninguna frase suya para ESO lo abría: escribió «fusiona» para el #224 y el freno no lo
# reconoció, porque aquí solo había envía/manda/publica. Abre un permiso de alcance «fusionar»:
# vale para `gh pr merge` del PR del que se hablaba y para nada más. Imperativo al principio de
# frase (como programar): «hay que fusionar», «¿fusionamos?», «la fusión de ayer» no son órdenes.
# «fusiona a casa base» tampoco: eso es local, no pasa por este freno y no debe abrir nada.
ORDEN_FUSIONAR = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"fusi[óo]na(?:lo|la|los|las)\b|m[ée]rgea(?:lo|la|los|las)\b|"
    r"dale\s+a\s+(?:fusionar|mergear)\b|"
    r"ya\s+puedes\s+(?:fusionar|mergear)\b"
    r")|" + _INICIO + r"("
    r"(?:fusiona|m[ée]rgea)\b(?!\s+(?:a|en|con)\s+(?:la\s+)?(?:casa\s+base|master|base)\b)|"
    r"haz\s+(?:el\s+)?merge\b"
    r")", re.I)

# Si el mensaje habla de dejarlo en borrador, NO es una orden de envío aunque use el verbo.
# `(?:lo|la|los|las)`: «no la envíes, envíala mañana» abría un envío hoy (verificacion, 24-sep-26).
# Plural (25-sep-26): si la orden entiende «publícalos», el freno tiene que entender el plural y
# los clíticos que lo acompañan, o una frase que frena y ordena a la vez abre el permiso HOY:
# «déjalos en borrador, publícalos mañana», «solo los borradores, envíalos mañana», «no se los
# envíes todavía, envíaselos mañana» (verificacion, con las frases compiladas contra las dos
# versiones). Las formas con se/me ya se colaban en singular; se cierran a la vez.
FRENA = re.compile(r"(no\s+(?:(?:me|te|se)\s+)?(?:(?:lo|la|los|las|le|les)\s+)?"
                   r"(?:env[ií]es|mandes|publiques)|"
                   r"d[ée]ja(?:se|me)?(?:los|las|lo|la)\s+en\s+borrador|"
                   r"solo\s+(?:el\s+|los\s+)?borrador(?:es)?|sin\s+enviar|"
                   r"no\s+(?:(?:lo|la|los|las)\s+)?programes|sin\s+programar|"
                   r"no\s+(?:hay\s+que|hace\s+falta)\s+crear|no\s+(?:la\s+)?crees\b|sin\s+crear|"
                   r"antes\s+de\s+crear|"
                   r"no\s+(?:(?:lo|la|los|las)\s+)?(?:fusiones|mergees)|sin\s+fusionar|"
                   r"no\s+hagas\s+(?:el\s+)?merge)", re.I)

# Tools que un permiso de alcance «programar» deja pasar. Todo lo demás exige una orden de envío.
# `update_`: cambiar una tarea existente es cambiar lo que un agente hará solo (verificacion).
PROGRAMAN = re.compile(r"__(?:create|run|update)[_-]?scheduled[_-]?task$", re.I)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CAMPOS_DESTINO = ("to", "cc", "bcc", "recipient", "recipients")
_DRAFT = re.compile(r"__(create_draft|update_draft)$")


# Lo que el harness y los hooks PEGAN al mensaje: recordatorios de worktree, contexto del lazo,
# memorias recordadas, avisos de tareas. Llega dentro del mismo campo `prompt`, así que sin quitarlo
# el emisor estaría juzgando texto que ella no ha escrito (visto en vivo el 24-sep-26: el permiso
# que había abierto otra sesión llevaba de motivo un `<system-reminder>` del harness). Una frase de
# envío dentro de uno de estos bloques —o en una memoria que la cite— NO es una orden suya.
INYECTADO = re.compile(
    r"<(system-reminder|task-notification|scheduled-task|ci-monitor-event)\b.*?</\1>|"
    r"<(?:task-notification|scheduled-task|ci-monitor-event)\b[^>]*/?>", re.S | re.I)


def solo_suyo(texto):
    """El texto sin los bloques que pega el harness: lo que de verdad escribió ella."""
    return INYECTADO.sub(" ", texto or "").strip()


TODO = frozenset({"envio", "programar", "fusionar"})


def alcance(texto):
    """Qué abre su mensaje: «envio», «programar», «fusionar», combinados o nada. Un envío vale
    también para programar y para fusionar (así se publicó el #220 con «publícalo»); programar y
    fusionar NO valen para enviar ni publicar por otra vía."""
    if es_automatico(texto):
        return set()
    texto = solo_suyo(texto)
    if not texto or FRENA.search(texto):
        return set()
    out = set()
    if ORDEN.search(texto):
        out |= TODO
    if ORDEN_PROGRAMAR.search(texto):
        out.add("programar")
    if ORDEN_FUSIONAR.search(texto):
        out.add("fusionar")
    return out


def es_orden(texto):
    return bool(alcance(texto))


def permite(ctx, que):
    """¿El permiso validado (su `ctx`) cubre `que` («envio» | «programar» | «fusionar»)? Un ctx sin
    alcance (formato anterior) se lee como envío, que lo cubría todo; un alcance VACÍO no cubre
    nada (antes `or` lo convertía en permiso total)."""
    a = (ctx or {}).get("alcance")
    return que in (TODO if a is None else a)


def token_path():
    return os.path.join(_casa.state_dir(), "ok_envio.json")


def usados_path():
    return os.path.join(_casa.state_dir(), "ok_envio_usados.jsonl")


# ── la clave ─────────────────────────────────────────────────────────────────────────────────
def _llavero_leer():
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICIO, "-w"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        return None


def _llavero_crear():
    import getpass
    valor = secrets.token_hex(32)
    cuenta = os.environ.get("USER") or getpass.getuser()
    try:
        subprocess.run(["security", "add-generic-password", "-a", cuenta, "-s", SERVICIO,
                        "-w", valor], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return _llavero_leer()          # si otra sesión la creó a la vez, gana la que quedó


CLAVE_TEST = None   # los tests en proceso la fijan aquí; nunca por entorno (ver `clave`)


def _state_aislado():
    """True si el estado NO es el de casa base (tests): ahí no se crea nada en el Llavero real."""
    try:
        return os.path.realpath(_casa.state_dir()) != os.path.realpath(
            os.path.join(_casa.casa_base(), "tools", "state"))
    except Exception:
        return True


def clave(permitir_env=False, crear=False):
    """bytes o None. `permitir_env` SOLO para los hooks: su entorno lo pone el harness, y los
    tests lo usan para no tocar el Llavero real. Una tool que lanza el agente (web_novedad,
    ok_envio.py) NO lo permite: ese entorno lo elige quien la lanza, y bastaría con
    `BTP_OK_ENVIO_CLAVE=x python3 tools/web_novedad.py` para firmar lo que quisiera."""
    if CLAVE_TEST:
        return CLAVE_TEST
    if permitir_env and os.environ.get("BTP_OK_ENVIO_CLAVE"):
        return os.environ["BTP_OK_ENVIO_CLAVE"].encode()
    if os.environ.get("BTP_OK_ENVIO_SIN_LLAVERO"):
        return None
    v = _llavero_leer() or (_llavero_crear() if crear and not _state_aislado() else None)
    return v.encode() if v else None


def _canon(d):
    return json.dumps({k: v for k, v in d.items() if k != "mac"}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def firmar(d, k):
    d = dict(d)
    d["mac"] = hmac.new(k, _canon(d), hashlib.sha256).hexdigest()
    return d


def _mac_ok(d, k):
    return (isinstance(d.get("mac"), str) and k is not None
            and hmac.compare_digest(d["mac"], hmac.new(k, _canon(d), hashlib.sha256).hexdigest()))


def _escribir(d):
    ruta = token_path()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".%d.tmp" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, ruta)


# ── emitir (solo ok_envio_prompt.py) ─────────────────────────────────────────────────────────
def emitir(prompt, session_id, prompt_id, transcript_path, k):
    d = {"ts": datetime.now().replace(microsecond=0).isoformat(),
         "origen": "prompt",
         "motivo": re.sub(r"\s+", " ", prompt).strip()[:200],
         "hash_prompt": hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest(),
         "session_id": session_id or "", "prompt_id": prompt_id or "",
         "transcript_path": transcript_path or "",
         "nonce": secrets.token_hex(8), "usos": 0}
    _escribir(firmar(d, k))
    return d


# ── leer y comprobar ─────────────────────────────────────────────────────────────────────────
def borrar():
    try:
        os.remove(token_path())
    except Exception:
        pass


def leer(k):
    """(dict|None, motivo). Firma, origen y caducidad. No mira el transcript."""
    try:
        with open(token_path(), encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, "nadie lo ha pedido en un mensaje"
    except Exception:
        borrar()
        return None, "permiso ilegible"
    if not isinstance(d, dict):
        borrar()
        return None, "permiso ilegible"
    if not _mac_ok(d, k):
        # Escrito a mano, con otra clave o retocado: no vale y no se deja ahí para reintentar.
        borrar()
        return None, "el permiso no lleva la firma del hook que lee su mensaje"
    if d.get("origen") not in ORIGENES:
        borrar()
        return None, "el permiso no nació de un mensaje suyo (origen %r)" % d.get("origen")
    try:
        edad = (datetime.now() - datetime.fromisoformat(d["ts"])).total_seconds()
    except Exception:
        borrar()
        return None, "permiso ilegible"
    if edad > VIDA_S or edad < -60:
        borrar()
        return None, "el permiso caducó"
    return d, ""


def _usado(d):
    try:
        with open(usados_path(), encoding="utf-8") as f:
            for linea in f:
                try:
                    u = json.loads(linea)
                except Exception:
                    continue
                if u.get("session_id") == d.get("session_id") and u.get("prompt_id") == d.get("prompt_id"):
                    return True
    except FileNotFoundError:
        pass
    return False


def marcar_usado(d, por=""):
    ruta = usados_path()
    try:
        lineas = open(ruta, encoding="utf-8").read().splitlines()[-499:]
    except Exception:
        lineas = []
    lineas.append(json.dumps({"ts": datetime.now().replace(microsecond=0).isoformat(),
                              "session_id": d.get("session_id"), "prompt_id": d.get("prompt_id"),
                              "nonce": d.get("nonce"), "por": por}, ensure_ascii=False))
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    borrar()


def _crudo(entrada):
    c = (entrada.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c if isinstance(c, str) else None


def texto_prompt(entrada):
    """Texto del mensaje de usuario, sin los <system-reminder> que el harness antepone."""
    c = _crudo(entrada)
    if c is None:
        return None
    # Se quitan TODOS los bloques inyectados, no solo los del principio (24-sep-26): una orden
    # pegada al FINAL por un hook colaba igual, y el test la reprodujo.
    return solo_suyo(c)


def _es_humano(e):
    """Prompt TECLEADO por una persona (mismo criterio que healthcheck, censo del 11-sep-26).
    El prompt de una tarea programada llega con origin human: se descarta por su texto CRUDO."""
    if e.get("type") != "user" or e.get("origin") != {"kind": "human"} or e.get("isSidechain") is not False:
        return False
    crudo = _crudo(e)
    return crudo is not None and not es_automatico(crudo)


def _cuerpo(e):
    b = e.get("body")
    if not isinstance(b, str):
        return None
    return hashlib.sha256("\n".join(l.rstrip() for l in b.replace("\r\n", "\n").strip().split("\n"))
                          .encode("utf-8")).hexdigest()


def _emails(valor):
    fuera = set()
    if isinstance(valor, str):
        fuera.update(m.lower() for m in EMAIL.findall(valor))
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            fuera |= _emails(v)
    elif isinstance(valor, dict):
        for v in valor.values():
            fuera |= _emails(v)
    return fuera


def destinatarios(entrada):
    """Direcciones a las que va una llamada (to/cc/bcc/recipient…), en minúsculas."""
    fuera = set()
    for c in _CAMPOS_DESTINO:
        if c in (entrada or {}):
            fuera |= _emails(entrada[c])
    return fuera


def contexto(d):
    """Lee el transcript al que apunta el permiso y devuelve (ok, motivo, ctx).

    ctx = {texto, emails (los que nombró), en_vista (borrador que tenía delante), hilos, drafts,
    cambios (borradores tocados DESPUÉS de su orden)}."""
    ruta = d.get("transcript_path") or ""
    if not ruta or not d.get("prompt_id"):
        return False, "el permiso no apunta a ningún mensaje suyo", {}
    try:
        f = open(ruta, encoding="utf-8")
    except Exception:
        return False, "no encuentro el transcript de la sesión", {}
    humanos, drafts_antes, cambios, suyo = [], [], [], None
    visto = []                      # lo que le escribí en la vuelta a la que contesta
    with f:
        for linea in f:
            try:
                e = json.loads(linea)
            except Exception:
                continue
            if not isinstance(e, dict):
                continue
            if _es_humano(e):
                humanos.append(e)
                if e.get("promptId") == d["prompt_id"]:
                    suyo = e
                elif suyo is None:
                    visto = []
                continue
            if e.get("type") == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
                    if suyo is None and isinstance(b, dict) and b.get("type") == "text":
                        visto.append(b.get("text") or "")
                    if (isinstance(b, dict) and b.get("type") == "tool_use"
                            and _DRAFT.search(b.get("name", "")) and isinstance(b.get("input"), dict)):
                        (cambios if suyo is not None else drafts_antes).append(
                            (len(humanos), b["input"]))
    if suyo is None:
        return False, "su mensaje no está en el transcript como prompt humano", {}
    if humanos[-1] is not suyo and humanos[-1].get("promptId") != d["prompt_id"]:
        return False, "después de esa orden escribió otra cosa", {}
    texto = texto_prompt(suyo) or ""
    if not es_orden(texto):
        return False, "su mensaje no contiene una orden de envío", {}
    n_suyo = len(humanos)            # índice (1-based) de su orden entre los prompts humanos
    hilos, drafts, en_vista = {}, {}, None
    for n, e in drafts_antes:
        info = {"cuerpo": _cuerpo(e), "destinos": destinatarios(e)}
        if e.get("replyToMessageId"):
            hilos[e["replyToMessageId"]] = info
        if e.get("draftId"):
            drafts[e["draftId"]] = info
        if n == n_suyo - 1:          # hecho en la vuelta a la que ella contesta: lo tenía delante
            en_vista = info
    return True, "", {"texto": texto, "emails": {m.lower() for m in EMAIL.findall(texto)},
                      "en_vista": en_vista, "hilos": hilos, "drafts": drafts,
                      "cambios": [e for _, e in cambios], "alcance": alcance(texto),
                      "prs_suyos": _prs(texto), "prs_vistos": _prs("\n".join(visto))}


# Números de PR en un texto: «#224», «PR 224», «el 224», «.../pull/224».
_PR_EN_TEXTO = re.compile(r"(?:#|\bPRs?\s*#?\s*|\bel\s+|/pull/)(\d{1,6})\b", re.I)
# El PR de un `gh pr merge`: el primer argumento numérico o la URL …/pull/N.
_MERGE = re.compile(r"\bgh\s+pr\s+merge\b(?P<resto>[^;&|\n]*)")


def _prs(texto):
    return {int(n) for n in _PR_EN_TEXTO.findall(texto or "")}


def pr_de_merge(comando):
    """(es_merge, número|None) de una orden Bash."""
    m = _MERGE.search(comando or "")
    if not m:
        return False, None
    for tok in m.group("resto").split():
        u = re.search(r"/pull/(\d+)", tok)
        if u:
            return True, int(u.group(1))
        if tok.isdigit():
            return True, int(tok)
    return True, None


def comprobar_fusion(ctx, numero):
    """"" si el merge de `numero` es el PR del que ella hablaba; si no, el motivo. Candado: si su
    mensaje nombra PRs, tiene que ser uno de esos; si no nombra ninguno, el que yo le había puesto
    delante (mi último mensaje antes del suyo), igual que `en_vista` en los borradores."""
    if numero is None:
        return "el merge no dice qué PR: no puedo comprobar que sea el que ella aprobó"
    suyos = ctx.get("prs_suyos") or set()
    if suyos:
        return "" if numero in suyos else "ella dijo el PR %s, no el %s" % (
            ", ".join(str(n) for n in sorted(suyos)), numero)
    if numero in (ctx.get("prs_vistos") or set()):
        return ""
    return "el PR %s no estaba en lo que ella tenía delante al decirlo" % numero


def comprobar_envio(ctx, entrada, tool=""):
    """"" si la llamada casa con lo que ella aprobó; si no, el motivo. Con `tool`, además, un
    permiso que solo abrió «programar» no deja pasar nada que no sea una tarea programada, y uno que
    solo abrió «fusionar», nada que no sea el `gh pr merge` del PR del que se hablaba."""
    entrada = entrada or {}
    es_merge, numero = (pr_de_merge(entrada.get("command"))
                        if (tool or "") == "Bash" else (False, None))
    que = ("programar" if PROGRAMAN.search(tool or "")
           else "fusionar" if es_merge else "envio")
    if tool and not permite(ctx, que):
        a = (ctx or {}).get("alcance") or set()
        if que == "envio":
            pidio = " y ".join(x for x in ("programar", "fusionar") if x in a) or "otra cosa"
            return "ella pidió %s, no enviar ni publicar" % pidio
        return ("su orden no cubre programar una tarea" if que == "programar"
                else "su orden no cubre fusionar un PR")
    if que == "fusionar":
        return comprobar_fusion(ctx, numero)
    destinos = destinatarios(entrada)
    if ctx.get("emails") and destinos - ctx["emails"]:
        return "va a %s y ella nombró %s" % (", ".join(sorted(destinos - ctx["emails"])),
                                             ", ".join(sorted(ctx["emails"])))
    cuerpo = _cuerpo(entrada)
    did = entrada.get("draftId")
    hilo = entrada.get("messageId") or entrada.get("replyToMessageId") or entrada.get("threadId")
    for c in ctx.get("cambios", []):
        if (did and c.get("draftId") == did) or (hilo and c.get("replyToMessageId") == hilo):
            return "el borrador cambió después de su OK: tiene que ver la versión nueva"
    aprobado = (ctx.get("drafts", {}).get(did) if did else None) or \
               (ctx.get("hilos", {}).get(hilo) if hilo else None) or ctx.get("en_vista")
    if aprobado:
        if cuerpo and aprobado["cuerpo"] and cuerpo != aprobado["cuerpo"]:
            return "el texto que sale no es el del borrador que ella vio"
        if destinos and aprobado["destinos"] and destinos - aprobado["destinos"] - ctx.get("emails", set()):
            return "va a %s, que no estaba en el borrador que ella vio" % ", ".join(
                sorted(destinos - aprobado["destinos"]))
    return ""


def validar(k, sesion=None):
    """(dict|None, motivo, ctx). Todo menos lo específico de la llamada. `sesion` = la del
    PreToolUse; None cuando lo comprueba una tool (web_novedad), que no la conoce."""
    d, motivo = leer(k)
    if not d:
        return None, motivo, {}
    if sesion is not None and sesion != d.get("session_id"):
        # 25-sep-26: «el permiso es de otra sesión» a secas sonaba a que su orden había llegado y
        # se había atribuido mal. Lo que pasa es que su mensaje AQUÍ no abrió ninguno (el hueco es
        # único y guarda el último de cualquier sesión): hay que decirle qué frase lo abre.
        return None, ("su mensaje en esta sesión no abrió permiso (el que hay es de otra sesión). "
                      "Lo abren: «publícalo», «envíalo», «fusiónalo», «prográmalo»"), {}
    if _usado(d):
        borrar()
        return None, "ese mensaje suyo ya abrió un envío", {}
    ok, motivo, ctx = contexto(d)
    if not ok:
        borrar()
        return None, motivo, {}
    return d, "", ctx


def gastar_uno(d, k, maximo):
    """Para web_novedad: su petición puede traer hasta `maximo` entradas. Re-firma el contador
    (retocarlo a mano rompe la firma) y al agotarse lo marca como usado."""
    d = dict(d)
    d["usos"] = int(d.get("usos", 0)) + 1
    if d["usos"] >= maximo:
        marcar_usado(d, "web_novedad")
    else:
        _escribir(firmar(d, k))
    return d
