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

# Órdenes de envío de {{TITULAR}}. Imperativo y en segunda persona: «envíalo», «mándaselo», «publica».
# NO entran las formas condicionales o de tercera persona («habría que enviar», «cuando lo envíe»),
# que son conversación sobre el envío, no la orden. (Vivía en ok_envio_prompt.py; aquí la usan el
# que emite el permiso y los que lo comprueban contra el transcript: una sola regex, no dos.)
ORDEN = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"env[ií]a(?:lo|la|le|selo|sela|melo)?\b|env[ií]ame\b|"
    r"m[áa]nda(?:lo|la|le|selo|sela|melo)?\b|"
    r"publ[ií]ca(?:lo|la)?\b|"
    r"resp[óo]nde(?:le|les|lo|la)?\b|contesta(?:le|les)?\b|"
    r"dale\s+a\s+enviar\b|"
    r"ya\s+puedes\s+(?:enviar|mandar|publicar)\b|"
    r"adelante\s+con\s+el\s+(?:env[ií]o|correo|mensaje)\b|"
    # Publicar en su web es lo mismo que enviar: sale al mundo y lleva su nombre.
    # `a\s*la` y no `a\s+la` a propósito: ella escribió «Añade ala cronologia» (20-sep-26) y el
    # permiso no se abrió por un espacio. Un freno que exige escribir sin erratas es un freno que
    # acaba estorbando, y entonces se quita — que es peor que no tenerlo.
    r"(?:p[óo]n|s[úu]be|a[ñn][áa]de|mete|a[ñn][áa]d[ae]?)(?:lo|la|le)?\s+"
    r"(?:a|en)\s*la\s+(?:web|cronolog[íi]a|timeline|l[íi]nea\s+de\s+tiempo)\b|"
    r"que\s+salga\s+en\s+la\s+web\b"
    r")", re.I)

# Si el mensaje habla de dejarlo en borrador, NO es una orden de envío aunque use el verbo.
FRENA = re.compile(r"(no\s+(?:lo\s+)?(?:env[ií]es|mandes|publiques)|d[ée]jalo\s+en\s+borrador|"
                   r"solo\s+(?:el\s+)?borrador|sin\s+enviar)", re.I)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CAMPOS_DESTINO = ("to", "cc", "bcc", "recipient", "recipients")
_DRAFT = re.compile(r"__(create_draft|update_draft)$")


def es_orden(texto):
    return bool(texto) and not FRENA.search(texto) and bool(ORDEN.search(texto))


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


def texto_prompt(entrada):
    """Texto del mensaje de usuario, sin los <system-reminder> que el harness antepone."""
    c = (entrada.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    return re.sub(r"^(\s*<system-reminder>.*?</system-reminder>)+", "", c, flags=re.S).lstrip()


def _es_humano(e):
    """Prompt TECLEADO por una persona (mismo criterio que healthcheck, censo del 11-sep-26)."""
    if e.get("type") != "user" or e.get("origin") != {"kind": "human"} or e.get("isSidechain") is not False:
        return False
    t = texto_prompt(e)
    return t is not None and not t.startswith(AUTOMATICOS)


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
                continue
            if e.get("type") == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
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
                      "cambios": [e for _, e in cambios]}


def comprobar_envio(ctx, entrada):
    """"" si la llamada casa con lo que ella aprobó; si no, el motivo."""
    entrada = entrada or {}
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
        return None, "el permiso es de otra sesión", {}
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
