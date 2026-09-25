#!/usr/bin/env python3
"""borrador_unico_guard.py — un correo, UN borrador. El mejorado sustituye, no acompaña.

NORMA (registro: tools/normas.json) `feedback-borrador-mejorado-borra-el-anterior`, clase
BLOQUEO y `repetida: true`: «al rehacer/mejorar un borrador de correo, BORRAR el anterior —
nunca dejar dos versiones del mismo correo en Borradores».

MEDIDO ANTES DE ESCRIBIRLO, sobre los transcripts reales: 47 llamadas a `create_draft`, y **8
pares (destinatario, asunto) con más de un borrador** — uno de ellos SEIS veces («Possible
candidate — NCT06691035»). Son **14 borradores redundantes de 47**, casi un tercio. Y la
alternativa correcta ya existía y se usaba: 19 llamadas a `update_draft`.

POR QUÉ DUELE. Ella abre Borradores y ve tres versiones del mismo correo a la misma oncóloga,
sin saber cuál es la buena. El riesgo real no es el desorden: es mandar la vieja.

CÓMO. Lleva una libreta local de qué hilos ya tienen borrador nuestro
(`tools/state/borradores_correo.json`) y deniega un `create_draft` para un hilo que ya lo tiene.
La clave es el `threadId` si viene, y si no el par (destinatario, asunto normalizado) — sin el
`Re:`/`RE:`/`Fwd:` delante, que es lo que hace que «Re: Sample at {{CENTRO}}» y «Sample at {{CENTRO}}»
parezcan dos correos distintos cuando son el mismo hilo.

`update_draft` NO se toca: es justo lo que la norma pide que se haga en vez de crear otro.
Y refresca la libreta, porque mejorar el borrador es trabajo legítimo.

CADUCA A 30 DÍAS. Si ella borró el borrador a mano en Gmail, la libreta se queda desfasada y
bloquearía un correo legítimo; un mes es más que suficiente para un hilo vivo.

BORRAR LIBERA (24-sep-26). El propio aviso dice «borra el anterior y crea el nuevo», pero el
guard no escuchaba `delete_draft`: tras borrar, la libreta seguía marcando el correo y el
`create_draft` nuevo se denegaba igual (pasó con la respuesta a Penguin). Ahora, en PostToolUse,
`create_draft` apunta el `id` del borrador que devuelve Gmail y `delete_draft` suelta la entrada
con ese `id`. Un `update_draft` con solo `draftId` refresca su entrada en vez de inventar una
clave vacía. Borradores creados antes de esto no tienen `id` apuntado: siguen tirando de caducidad.

`update_draft` SUELTA EL BORRADOR DEL HILO (visto el 24-sep-26: el threadId cambió tras
actualizar una respuesta). Para una respuesta dentro de un hilo, lo seguro es borrar y crear con
`replyToMessageId`, y el aviso lo dice.

FAIL-OPEN, como el resto de guards de hoy: esto atrapa un despiste, y si el guard revienta,
dejarla sin poder redactar correo sería peor que el despiste.
Escotilla explícita: `BTP_BORRADOR_OK=1`.

Contrato de hooks (code.claude.com/docs/hooks): exit 0 permite · exit 2 deniega.
"""
import json
import os
import re
import sys
import time

CADUCIDAD = 30 * 24 * 3600
_RE_PREFIJOS = re.compile(r"^\s*((re|rv|fwd|fw|res|aw)\s*:\s*|\[extern\]\s*)+", re.I)


def _casa_base():
    ov = os.environ.get("BTP_REPO")
    raiz = os.path.abspath(ov) if ov else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return raiz.split(os.sep + os.path.join(".claude", "worktrees") + os.sep)[0]


def _libreta():
    return os.environ.get("BTP_BORRADORES_JSON") or os.path.join(
        _casa_base(), "tools", "state", "borradores_correo.json")


def _leer(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _escribir(p, d):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def _norm_asunto(s):
    """Sin `Re:`/`RE:`/`Fwd:`/`[Extern]` delante, en minúsculas y sin espacios de más."""
    s = _RE_PREFIJOS.sub("", str(s or ""))
    while True:
        s2 = _RE_PREFIJOS.sub("", s)
        if s2 == s:
            break
        s = s2
    return re.sub(r"\s+", " ", s).strip().lower()


def _destinatarios(ti):
    v = ti.get("to") or ti.get("recipient") or ti.get("recipients") or ti.get("email") or ""
    if isinstance(v, list):
        v = ",".join(str(x) for x in v)
    dirs = re.findall(r"[\w.+-]+@[\w.-]+", str(v))
    return ",".join(sorted(d.lower() for d in dirs)) or str(v).strip().lower()


def clave(ti):
    """Identidad del correo: el hilo si se sabe; si no, destinatario + asunto normalizado."""
    hilo = ti.get("threadId") or ti.get("thread_id") or ti.get("thread")
    if hilo:
        return "hilo:%s" % hilo
    return "asunto:%s|%s" % (_destinatarios(ti), _norm_asunto(ti.get("subject")))


def _es(tool, sufijo):
    return tool == sufijo or tool.endswith("__" + sufijo)


def _draft_id(resp):
    """El `id` del borrador en la respuesta de create_draft, venga como dict, lista de bloques
    de texto o JSON en cadena. None si no se encuentra."""
    pila = [resp]
    while pila:
        x = pila.pop()
        if isinstance(x, dict):
            v = x.get("id")
            if isinstance(v, str) and re.fullmatch(r"r-?\d+", v):
                return v
            pila.extend(x.values())
        elif isinstance(x, list):
            pila.extend(x)
        elif isinstance(x, str) and x.lstrip()[:1] in ("{", "["):
            try:
                pila.append(json.loads(x))
            except Exception:
                pass
    return None


def _post(tool, ti, data, p, libro):
    """PostToolUse: create_draft apunta su id; delete_draft suelta la entrada de ese id."""
    if _es(tool, "create_draft"):
        did = _draft_id(data.get("tool_response"))
        k = clave(ti)
        if did and k in libro:
            libro[k]["draft"] = did
            _escribir(p, libro)
    elif _es(tool, "delete_draft"):
        did = ti.get("draftId") or ti.get("draft_id") or ti.get("id")
        fuera = [k for k, v in libro.items() if did and (v or {}).get("draft") == did]
        for k in fuera:
            libro.pop(k, None)
        if fuera:
            _escribir(p, libro)
    return 0


def main():
    data = json.loads(sys.stdin.read())
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if not any(_es(tool, t) for t in ("create_draft", "update_draft", "delete_draft")):
        return 0
    p = _libreta()
    if data.get("hook_event_name") == "PostToolUse":
        return _post(tool, ti, data, p, _leer(p))
    if _es(tool, "delete_draft"):
        return 0
    if os.environ.get("BTP_BORRADOR_OK") == "1":
        return 0
    libro = _leer(p)
    ahora = time.time()
    for k, v in list(libro.items()):
        if ahora - float((v or {}).get("ts") or 0) > CADUCIDAD:
            libro.pop(k, None)
    k = clave(ti)

    if _es(tool, "update_draft"):
        # Mejorar el borrador que ya hay es EXACTAMENTE lo que pide la norma.
        did = ti.get("draftId") or ti.get("draft_id")
        suya = [kk for kk, v in libro.items() if did and (v or {}).get("draft") == did]
        if suya:
            libro[suya[0]]["ts"] = ahora
        elif ti.get("subject") or _destinatarios(ti):
            ent = {"ts": ahora, "veces": int((libro.get(k) or {}).get("veces") or 1)}
            if did:
                ent["draft"] = did
            libro[k] = ent
        else:
            return 0      # solo draftId y sin entrada: no inventar una clave vacía
        _escribir(p, libro)
        return 0

    previo = libro.get(k)
    if previo:
        dias = (ahora - float(previo.get("ts") or 0)) / 86400.0
        sys.stderr.write(
            "BORRADOR ⛔ ya hay un borrador tuyo para este mismo correo (hace %s).\n"
            "   Nunca dos versiones del mismo correo en Borradores: cuando abra Gmail no va a\n"
            "   saber cuál es la buena, y el riesgo de verdad es que mande la vieja.\n"
            "   Haz una de las dos:\n"
            "     · `update_draft` sobre el que ya existe (lo encuentras con `list_drafts`), o\n"
            "     · borra el anterior (`delete_draft`) y entonces crea el nuevo.\n"
            "   Si es una RESPUESTA dentro de un hilo, borra y crea con `replyToMessageId`:\n"
            "   `update_draft` saca el borrador del hilo.\n"
            "   Si de verdad son dos correos distintos: BTP_BORRADOR_OK=1.\n"
            % ("menos de una hora" if dias < 0.05 else "%.0f día(s)" % max(dias, 1)))
        return 2

    libro[k] = {"ts": ahora, "veces": 1}
    _escribir(p, libro)
    return 0


if __name__ == "__main__":
    # Watchdog (25-sep-26): si tardo más que el timeout de settings (5 s), deniego yo antes de
    # que Claude Code me cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("borrador_unico_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(5, 'borrador_unico_guard')
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)   # FAIL-OPEN deliberado (ver cabecera)
