#!/usr/bin/env python3
"""tools/borradores_higiene.py — la higiene diaria de la carpeta Borradores de Gmail (14-oct-2026).

POR QUÉ EXISTE. El 14-oct había 99 borradores: 14 grupos duplicados y unos 60 caducados o ya
contestados. El hook `borrador_unico_guard.py` solo frena duplicados de menos de 1 h, así que la
basura se volvía a acumular. Norma de {{TITULAR}}: «buzón siempre organizado, sin cosas innecesarias,
sin preguntar» (`feedback-borrador-mejorado-borra-el-anterior`).

DETERMINISTA, SIN LLM. Revisa Borradores por IMAP en las cuentas de `correo_outbox.ACCOUNTS` y
aplica estas reglas, EN ESTE ORDEN (la primera que casa manda):

  1. duplicado     mismo X-GM-THRID, o mismo destinatario + asunto normalizado (sin Re:/Fwd:):
                   queda el más nuevo, el resto a la Papelera.
  2. ya-contestado hay un ENVIADO por la cuenta, posterior al borrador, en su hilo o a su
                   destinatario.
  3. basura        sin destinatario, cuerpo vacío o solo firma, o asunto «NO ENVIAR»/«descartado».
  4. caducado      más de 30 días y NO protegido.
  5. protegido     dominio clínico/de NED o palabra clave: nunca cae por la regla 4; si lleva más
                   de 14 días se PROPONE a Vega. Las reglas 1-3 sí se aplican aunque esté protegido.
  6. aviso         CC a la antigua coordinadora de prensa, o propone llamada/reunión en su cuerpo propio
                   (sin la cita del hilo): no se borra, va a Vega.

MURO.
  · Nunca borra a fondo: COPY a la Papelera de Gmail (recuperable 30 días) y solo después
    \\Deleted + UID EXPUNGE de los UIDs que se copiaron CON ÉXITO. Nunca un EXPUNGE a secas.
  · `--dry-run` es el modo por defecto y abre el buzón en SOLO LECTURA: no puede escribir.
  · Egress fail-closed: solo imap.gmail.com:993 (`correo_outbox._assert_host`).
  · Nada de avisos a {{TITULAR}}: el resumen va como una propuesta al buzón de Vega.
  · El correo es DATO: este tool no interpreta ni ejecuta nada de lo que lee en un cuerpo.

Uso:
  python3 tools/borradores_higiene.py [--dry-run]          # por defecto: solo mira e informa
  python3 tools/borradores_higiene.py --apply              # mueve a la Papelera
  python3 tools/borradores_higiene.py --cuenta titular  # una sola cuenta
"""
import datetime
import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
import ssl
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hashlib
import _casa  # noqa: E402
import _secrets  # noqa: E402
import correo_outbox as OB  # noqa: E402

HOST, PORT = "imap.gmail.com", 993          # la única boca permitida (la comprueba OB._assert_host)
ORIGEN = "borradores-higiene"
DIAS_CADUCADO = 30
DIAS_ESCALAR_PROTEGIDO = 14
# Huella sha256 de la dirección de la antigua coordinadora de prensa (14-oct-26): sin PII en claro.
CC_AVISO_SHA = {"8b8fd06095ad30a0aa7d8f1ebe68865f0c34fb8d6b68fabe4b5a07cd3690b4ec"}
LOTE_FETCH = 25

DOMINIOS_PROTEGIDOS = (
    "{{CENTRO}}.net", "dfci.harvard.edu", "carm.es", "usz.ch", "{{CENTRO}}.ch", "chuv.ch",
    "fredhutch.org", "vallhebron.cat", "clinic.cat", "um.es", "ejaso.com", "healthincode.com",
    "cegat.de", "tellmegen.com", "biomedicallogistics.com", "cicams.ac.cn", "163.com",
    "regenelead.com", "contactofoundation.org", "anthropic.com",
)
RE_PALABRAS_PROTEGIDAS = re.compile(
    r"biopsia|muestra|ensayo|trial|\bHLA\b|\bWGS\b|laminillas|bloque|slides|tumor|oncolog|patolog",
    re.I)
RE_LLAMADA = re.compile(r"llamada|videollamada|reuni[oó]n|\bcall\b|meeting", re.I)
RE_NO_ENVIAR = re.compile(r"NO\s+ENVIAR|descartado", re.I)
RE_PREFIJO_ASUNTO = re.compile(r"^\s*((re|fwd?|rv|rf|enc)\s*(\[\d+\])?\s*:\s*)+", re.I)
RE_INICIO_CITA = re.compile(
    r"^(on .{5,200} wrote:|el .{5,200} escribi[oó]:|-{2,}\s*(original|forwarded|mensaje)\b.*)\s*$", re.I)
RE_CIERRE = re.compile(
    r"^(saludos|un abrazo|abrazos|gracias|muchas gracias|best|best regards|regards|kind regards|"
    r"cheers|titular|titular gonz[aá]lez|m\.?)[\s,.!;:-]*$", re.I)


# ── lectura y normalización ──────────────────────────────────────────────────────────────────

def _asunto_norm(s):
    return re.sub(r"\s+", " ", RE_PREFIJO_ASUNTO.sub("", s or "")).strip().lower()


def _emails(valor):
    """Direcciones planas en minúsculas de una cabecera To/Cc."""
    return sorted({a.strip().lower() for _, a in email.utils.getaddresses([valor or ""]) if a and "@" in a})


def _dominio_protegido(addr):
    dom = addr.rsplit("@", 1)[-1].lower()
    return any(dom == d or dom.endswith("." + d) for d in DOMINIOS_PROTEGIDOS)


def _cuerpo_propio(texto):
    """Lo que escribió ella: sin líneas citadas (`>`) ni lo que cuelga de «On … wrote:»."""
    out = []
    for ln in (texto or "").splitlines():
        if RE_INICIO_CITA.match(ln.strip()):
            break
        if ln.lstrip().startswith(">"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def _solo_firma(cuerpo):
    """Vacío, o nada más que despedida/firma (tras el delimitador `-- ` no cuenta)."""
    lineas = []
    for ln in (cuerpo or "").splitlines():
        if re.match(r"^--\s*$", ln):
            break
        if ln.strip():
            lineas.append(ln.strip())
    return all(RE_CIERRE.match(ln) for ln in lineas)


def _texto_de(raw_header, raw_text):
    """Texto plano del cuerpo (parcial) del borrador. Nunca lanza: lo ilegible es cuerpo vacío."""
    try:
        msg = email.message_from_bytes(raw_header + b"\r\n" + (raw_text or b""), policy=email.policy.default)
        parte = msg.get_body(preferencelist=("plain", "html")) if msg.is_multipart() else msg
        if parte is None:
            return ""
        txt = parte.get_content() if parte.get_content_maintype() == "text" else ""
        if parte.get_content_type() == "text/html":
            txt = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", txt))
        return txt
    except Exception:
        return ""


def _parse_fetch(data):
    """Respuesta de un UID FETCH → {uid: {"thrid", "ts", "literales": [bytes, …]}}.
    Mira todos los trozos (Gmail pone UID/THRID antes o después del literal)."""
    out = {}
    estado = {"literales": None, "meta": []}

    def cierra():
        if estado["literales"] is None:
            return
        txt = b" ".join(estado["meta"])
        mu = re.search(rb"\bUID (\d+)", txt)
        if not mu:
            return
        mt = re.search(rb"X-GM-THRID (\d+)", txt)
        md = re.search(rb'INTERNALDATE "[^"]+"', txt)
        ts = 0.0
        if md:
            try:
                ts = time.mktime(imaplib.Internaldate2tuple(md.group(0)))
            except Exception:
                ts = 0.0
        out[int(mu.group(1))] = {"thrid": mt.group(1).decode() if mt else "", "ts": ts,
                                 "literales": estado["literales"]}

    for item in data or []:
        if isinstance(item, tuple):
            prefijo, lit = item[0], item[1]
            if re.match(rb"^\s*\d+ \(", prefijo):
                cierra()
                estado["literales"], estado["meta"] = [], []
            if estado["literales"] is not None:
                estado["meta"].append(prefijo)
                estado["literales"].append(lit)
        elif isinstance(item, bytes) and estado["literales"] is not None:
            estado["meta"].append(item)
    cierra()
    return out


def _uids(M, *criterio):
    typ, data = M.uid("SEARCH", None, *criterio)
    return [int(x) for x in data[0].split()] if typ == "OK" and data and data[0] else []


def _fetch(M, uids, items):
    """UID FETCH por lotes → dict uid→datos (ver _parse_fetch)."""
    res = {}
    for i in range(0, len(uids), LOTE_FETCH):
        lote = ",".join(str(u) for u in uids[i:i + LOTE_FETCH])
        typ, data = M.uid("FETCH", lote, items)
        if typ != "OK":
            raise RuntimeError("IMAP FETCH falló: %s" % typ)
        res.update(_parse_fetch(data))
    return res


def _carpeta_con_atributo(M, atributo, defecto):
    try:
        typ, data = M.list()
        if typ == "OK":
            for raw in data or []:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                if atributo in line:
                    m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return defecto


def _papelera(M):
    return _carpeta_con_atributo(M, "\\Trash", "[Gmail]/Trash")


def leer_borradores(M):
    """Selecciona Borradores en SOLO LECTURA y devuelve (carpeta, [borrador, …])."""
    carpeta = OB._drafts_folder(M)
    typ, _ = M.select('"%s"' % carpeta, readonly=True)
    if typ != "OK":
        raise RuntimeError("no pude abrir la carpeta %r" % carpeta)
    uids = _uids(M, "ALL")
    datos = _fetch(M, uids, "(X-GM-THRID INTERNALDATE BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.60000>)")
    borradores = []
    for uid in uids:
        d = datos.get(uid)
        if not d or not d["literales"]:
            continue
        cab = d["literales"][0]
        texto = _texto_de(cab, d["literales"][1] if len(d["literales"]) > 1 else b"")
        h = email.message_from_bytes(cab, policy=email.policy.default)
        borradores.append({
            "uid": uid, "thrid": d["thrid"], "ts": d["ts"],
            "asunto": str(h.get("Subject") or ""),
            "to": _emails(str(h.get("To") or "")), "cc": _emails(str(h.get("Cc") or "")),
            "texto": texto, "propio": _cuerpo_propio(texto),
            "con_adjunto": (h.get_content_type() or "").startswith("multipart/mixed"),
        })
    return carpeta, borradores


def leer_enviados(M, desde_ts):
    """Lo ENVIADO por la cuenta desde `desde_ts`: [{thrid, ts, to}]. Mira «Todo el correo»
    (SOLO LECTURA) con X-GM-RAW in:sent, como el resto de la pila de correo."""
    import correo_imap
    correo_imap._select_todo_el_correo(M)
    dia = datetime.date.fromtimestamp(desde_ts - 86400).strftime("%Y/%m/%d")
    typ, data = M.uid("SEARCH", "X-GM-RAW", '"in:sent after:%s"' % dia)
    uids = [int(x) for x in data[0].split()] if typ == "OK" and data and data[0] else []
    datos = _fetch(M, uids, "(X-GM-THRID INTERNALDATE BODY.PEEK[HEADER.FIELDS (TO CC)])")
    out = []
    for d in datos.values():
        cab = email.message_from_bytes(d["literales"][0] if d["literales"] else b"",
                                       policy=email.policy.default)
        out.append({"thrid": d["thrid"], "ts": d["ts"],
                    "to": _emails(str(cab.get("To") or "")) + _emails(str(cab.get("Cc") or ""))})
    return out


# ── las reglas (puro: sin red) ───────────────────────────────────────────────────────────────

def es_protegido(b):
    if any(_dominio_protegido(a) for a in b["to"] + b["cc"]):
        return True
    return bool(RE_PALABRAS_PROTEGIDAS.search(b["asunto"] + "\n" + b["texto"]))


def es_basura(b):
    if not b["to"] or RE_NO_ENVIAR.search(b["asunto"]):
        return True
    return (not b["con_adjunto"]) and _solo_firma(b["propio"])


def ya_contestado(b, enviados):
    for s in enviados:
        if s["ts"] <= b["ts"]:
            continue
        if (b["thrid"] and s["thrid"] == b["thrid"]) or set(b["to"]) & set(s["to"]):
            return True
    return False


def _duplicados(borradores):
    """uids a descartar por duplicado. En cada grupo queda el más nuevo que NO sea basura (si lo
    más nuevo fuera basura, quitar los viejos dejaría el hilo sin versión vigente)."""
    grupos = {}
    for b in borradores:
        if b["thrid"]:
            grupos.setdefault(("hilo", b["thrid"]), []).append(b)
        if b["to"] and _asunto_norm(b["asunto"]):
            grupos.setdefault(("dest", tuple(b["to"]), _asunto_norm(b["asunto"])), []).append(b)
    caen = set()
    for g in grupos.values():
        if len(g) < 2:
            continue
        vigentes = [b for b in g if not es_basura(b)] or g
        queda = max(vigentes, key=lambda b: (b["ts"], b["uid"]))
        caen |= {b["uid"] for b in g if b["uid"] != queda["uid"]}
    return caen


def clasificar(borradores, enviados, ahora, cuenta=""):
    """Decisiones: [{uid, cuenta, asunto, destinatario, regla, accion}] con accion en
    papelera | escalar | aviso. Una sola decisión de borrado por borrador; los avisos se suman."""
    dup = _duplicados(borradores)
    out = []

    def dec(b, regla, accion):
        out.append({"uid": b["uid"], "cuenta": cuenta, "asunto": b["asunto"],
                    "destinatario": ", ".join(b["to"]), "regla": regla, "accion": accion})

    for b in sorted(borradores, key=lambda x: x["uid"]):
        dias = (ahora - b["ts"]) / 86400.0 if b["ts"] else 0.0
        protegido = es_protegido(b)
        if b["uid"] in dup:
            dec(b, "duplicado", "papelera")
        elif ya_contestado(b, enviados):
            dec(b, "ya-contestado", "papelera")
        elif es_basura(b):
            dec(b, "basura", "papelera")
        elif dias > DIAS_CADUCADO and not protegido:
            dec(b, "caducado", "papelera")
        else:
            if protegido and dias > DIAS_ESCALAR_PROTEGIDO:
                dec(b, "protegido-antiguo", "escalar")
            if any(hashlib.sha256(a.lower().encode()).hexdigest() in CC_AVISO_SHA for a in b["cc"] + b["to"]):
                dec(b, "cc-antigua-coordinadora", "aviso")
            if RE_LLAMADA.search(b["propio"]):
                dec(b, "propone-llamada", "aviso")
    return out


# ── IMAP ─────────────────────────────────────────────────────────────────────────────────────

def _conectar(user, servicio):
    """Conexión autenticada. None si falta la contraseña de aplicación (se salta con aviso)."""
    pw = _secrets.get(servicio)
    if not pw:
        return None
    OB._assert_host(HOST, PORT)
    M = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=ssl.create_default_context())
    M.login(user, pw)
    return M


def mover_a_papelera(M, carpeta, uids):
    """COPY a la Papelera y, solo de los UIDs copiados, \\Deleted + UID EXPUNGE.
    Devuelve los UIDs que de verdad se movieron."""
    if not uids:
        return []
    typ, _ = M.select('"%s"' % carpeta)                  # escritura
    if typ != "OK":
        raise RuntimeError("no pude abrir %r para escribir" % carpeta)
    papelera = _papelera(M)
    copiados = []
    for uid in uids:
        typ, _ = M.uid("COPY", str(uid), '"%s"' % papelera)
        if typ == "OK":
            copiados.append(uid)
    if copiados:
        conj = ",".join(str(u) for u in copiados)
        M.uid("STORE", conj, "+FLAGS", "(\\Deleted)")
        M.uid("EXPUNGE", conj)
    return copiados


# ── estado: registro y buzón de Vega ─────────────────────────────────────────────────────────

def _registro_path():
    return os.path.join(_casa.state_dir(), "correo", "borradores_higiene.jsonl")


def _buzon_path():
    return os.path.join(_casa.state_dir(), "vega", "propuestas_hilos.jsonl")


def apuntar_registro(movidos, ahora):
    ruta = _registro_path()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    ts = datetime.datetime.fromtimestamp(ahora).isoformat(timespec="seconds")
    with open(ruta, "a", encoding="utf-8") as f:
        for d in movidos:
            f.write(json.dumps({"ts": ts, "cuenta": d["cuenta"], "uid": d["uid"],
                                "asunto": d["asunto"], "destinatario": d["destinatario"],
                                "regla": d["regla"]}, ensure_ascii=False) + "\n")


def proponer_a_vega(movidos, escalar, avisos, ahora):
    """UNA línea mía sin resolver en el buzón de Vega (se sustituye la de antes, no se suma).
    Solo si hay algo que contar. Devuelve True si escribió."""
    if not (movidos or escalar or avisos):
        return False
    import _lock
    ruta = _buzon_path()
    ts = datetime.datetime.fromtimestamp(ahora).isoformat(timespec="seconds")
    detalle = ["%s <%s> (%s)" % (d["asunto"][:50], d["destinatario"][:40], d["regla"])
               for d in escalar + avisos]
    mia = {"ts": ts, "origen": ORIGEN,
           "titulo": "Borradores: %d a la Papelera, %d protegidos antiguos, %d para rehacer"
                     % (len(movidos), len(escalar), len(avisos)),
           "nota": "; ".join(detalle)[:600],
           "ruta": "tools/state/correo/borradores_higiene.jsonl"}
    with _lock.lock("vega-propuestas", timeout=20.0):
        try:
            with open(ruta, encoding="utf-8") as f:
                lineas = [x for x in f.read().splitlines() if x.strip()]
        except OSError:
            lineas = []
        resto = []
        for x in lineas:
            try:
                d = json.loads(x)
            except ValueError:
                resto.append(x)
                continue
            if d.get("origen") == ORIGEN and not d.get("estado"):
                continue
            resto.append(x)
        resto.append(json.dumps(mia, ensure_ascii=False))
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(resto) + "\n")
        os.replace(tmp, ruta)
    return True


# ── orquestación ─────────────────────────────────────────────────────────────────────────────

def revisar_cuenta(M, cuenta, ahora, aplicar):
    """Una cuenta: lee, clasifica y (si `aplicar`) mueve. Propaga errores: decide el que llama."""
    carpeta, borradores = leer_borradores(M)
    if not borradores:
        return {"cuenta": cuenta, "total": 0, "decisiones": [], "movidos": []}
    fechas = [b["ts"] for b in borradores if b["ts"]]
    enviados = leer_enviados(M, min(fechas) if fechas else ahora)
    decisiones = clasificar(borradores, enviados, ahora, cuenta)
    movidos = []
    if aplicar:
        quieren = [d for d in decisiones if d["accion"] == "papelera"]
        hechos = set(mover_a_papelera(M, carpeta, [d["uid"] for d in quieren]))
        movidos = [d for d in quieren if d["uid"] in hechos]
    return {"cuenta": cuenta, "total": len(borradores), "decisiones": decisiones, "movidos": movidos}


def pasada(aplicar=False, solo_cuenta=None, ahora=None, conectar=None, out=print):
    """Todas las cuentas. Devuelve {"resultados", "saltadas", "errores", …}. Un fallo de una
    cuenta no tumba a la otra; cada una cierra su sesión."""
    ahora = time.time() if ahora is None else ahora
    conectar = conectar or _conectar
    res = {"resultados": [], "saltadas": [], "errores": []}
    for user, servicio in OB.ACCOUNTS.items():
        if solo_cuenta and solo_cuenta.lower() not in user.lower():
            continue
        M = None
        try:
            M = conectar(user, servicio)
            if M is None:
                res["saltadas"].append(user)
                out("AVISO %s: falta la contraseña de aplicación '%s' en el Llavero; cuenta saltada."
                    % (user, servicio))
                continue
            res["resultados"].append(revisar_cuenta(M, user, ahora, aplicar))
        except Exception as e:                      # política: la decide la capa más externa
            res["errores"].append((user, "%s: %s" % (type(e).__name__, e)))
            out("ERROR %s: %s: %s" % (user, type(e).__name__, e))
        finally:
            if M is not None:
                try:
                    M.logout()
                except Exception:
                    pass
    movidos = [d for r in res["resultados"] for d in r["movidos"]]
    todas = [d for r in res["resultados"] for d in r["decisiones"]]
    escalar = [d for d in todas if d["accion"] == "escalar"]
    avisos = [d for d in todas if d["accion"] == "aviso"]
    if aplicar:
        if movidos:
            apuntar_registro(movidos, ahora)
        proponer_a_vega(movidos, escalar, avisos, ahora)
    res["movidos"], res["escalar"], res["avisos"] = movidos, escalar, avisos
    return res


def _informe(res, aplicar, out=print):
    out("== Higiene de Borradores (%s) ==" % ("APLICADO" if aplicar else "dry-run: no se toca nada"))
    for r in res["resultados"]:
        out("%s: %d borradores" % (r["cuenta"], r["total"]))
        for d in r["decisiones"]:
            etiqueta = {"papelera": "PAPELERA", "escalar": "A VEGA  ", "aviso": "AVISO   "}[d["accion"]]
            marca = " [movido]" if d in r["movidos"] else ""
            out("  %s %-18s uid=%-6s %s -> %s%s" % (etiqueta, d["regla"], d["uid"],
                                                    d["asunto"][:60], d["destinatario"][:50], marca))
        if not r["decisiones"]:
            out("  (nada que hacer)")
    n = sum(1 for r in res["resultados"] for d in r["decisiones"] if d["accion"] == "papelera")
    out("Resumen: %d a la Papelera%s, %d protegidos antiguos a Vega, %d avisos a Vega."
        % (n, (" (%d movidos)" % len(res["movidos"])) if aplicar else " (propuestos)",
           len(res["escalar"]), len(res["avisos"])))


def main(argv):
    aplicar = "--apply" in argv
    if aplicar and "--dry-run" in argv:
        print("--apply y --dry-run a la vez no tienen sentido.", file=sys.stderr)
        return 2
    cuenta = None
    if "--cuenta" in argv:
        i = argv.index("--cuenta")
        cuenta = argv[i + 1] if i + 1 < len(argv) else None
    res = pasada(aplicar=aplicar, solo_cuenta=cuenta)
    _informe(res, aplicar)
    return 1 if res["errores"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
