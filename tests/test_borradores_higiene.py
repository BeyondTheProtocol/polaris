#!/usr/bin/env python3
"""test_borradores_higiene.py — la higiene de Borradores borra lo que debe y NUNCA lo que no (14-oct-2026).

Con un IMAP falso (sin red, sin Llavero). Fija:
  · cada regla: duplicado, ya-contestado, basura, caducado, aviso (CC / llamada);
  · lo protegido (dominio clínico o palabra clave) NO cae por caducado, y a los 14 días va a Vega;
  · lo protegido SÍ cae si es duplicado o ya contestado;
  · el dry-run abre en solo lectura y no escribe NADA (ni IMAP, ni registro, ni Vega);
  · --apply copia a la Papelera (\\Trash) y UID EXPUNGE solo de lo copiado con éxito;
  · el host falla cerrado y una cuenta sin contraseña se salta.
"""
import imaplib
import json
import os
import sys
import tempfile
import time
from email.message import EmailMessage

_TMP = tempfile.mkdtemp(prefix="test_borradores_higiene_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import borradores_higiene as H  # noqa: E402
H.CC_AVISO_SHA = set(H.CC_AVISO_SHA) | {"72111036f700f94875ddabc7cc5d7ee330fa54bcc54cfd545c08e652926da73b"}  # dirección de ejemplo del test
import correo_outbox as OB  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("FAIL:", name)


AHORA = time.mktime((2026, 10, 14, 8, 0, 0, 0, 0, -1))
DIA = 86400.0


def _fecha(ts):
    return imaplib.Time2Internaldate(ts)


def _mime(to, subject, body, cc=None, mixed=False):
    m = EmailMessage()
    if to:
        m["To"] = to
    if cc:
        m["Cc"] = cc
    m["Subject"] = subject
    m.set_content(body)
    if mixed:
        m.add_attachment(b"x", maintype="application", subtype="pdf", filename="a.pdf")
    return m.as_bytes()


class FakeIMAP:
    """Lo mínimo de imaplib que usa la tool; registra todo lo que se escribe."""
    def __init__(self, borradores, enviados, copy_falla=()):
        self.drafts = borradores            # {uid: (thrid, ts, raw_bytes)}
        self.sent = enviados                # {uid: (thrid, ts, raw_header)}
        self.copy_falla = set(copy_falla)
        self.carpeta, self.readonly = None, None
        self.escrituras, self.copiados, self.expunged, self.flagged = [], [], [], []

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Borradores"',
                      b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Papelera"',
                      b'(\\HasNoChildren \\All) "/" "[Gmail]/Todos"']

    def select(self, carpeta, readonly=False):
        self.carpeta, self.readonly = carpeta.strip('"'), readonly
        return "OK", [b"1"]

    def logout(self):
        return "BYE", []

    def _split(self, raw):
        cab, _, cuerpo = raw.partition(b"\r\n\r\n")
        if not _:
            cab, _, cuerpo = raw.partition(b"\n\n")
        return cab + b"\r\n\r\n", cuerpo

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        if cmd == "SEARCH":
            fuente = self.drafts if self.carpeta == "[Gmail]/Borradores" else self.sent
            return "OK", [" ".join(str(u) for u in sorted(fuente)).encode()]
        if cmd == "FETCH":
            lote, items = args
            en_drafts = self.carpeta == "[Gmail]/Borradores"
            fuente = self.drafts if en_drafts else self.sent
            data = []
            for u in [int(x) for x in lote.split(",")]:
                if u not in fuente:
                    continue
                thrid, ts, raw = fuente[u]
                cab, cuerpo = self._split(raw)
                etiqueta = "BODY[HEADER]" if en_drafts else "BODY[HEADER.FIELDS (TO CC)]"   # como Gmail real
                meta = ('%d (UID %d X-GM-THRID %s INTERNALDATE "%s" %s {%d}'
                        % (u, u, thrid, _fecha(ts).strip('"'), etiqueta, len(cab))).encode()
                data.append((meta, cab))
                if en_drafts:
                    data.append((b" BODY[TEXT]<0> {%d}" % len(cuerpo), cuerpo))
                data.append(b")")
            return "OK", data
        # a partir de aquí: escrituras
        self.escrituras.append((cmd,) + args)
        if cmd == "COPY":
            uid = int(args[0])
            if uid in self.copy_falla:
                return "NO", [b"fallo"]
            self.copiados.append((uid, args[1].strip('"')))
            return "OK", [b"ok"]
        if cmd == "STORE":
            self.flagged += [int(x) for x in args[0].split(",")]
            return "OK", [b"ok"]
        if cmd == "EXPUNGE":
            self.expunged += [int(x) for x in args[0].split(",")]
            return "OK", [b"ok"]
        return "BAD", []


_n = [1000]


def borrador(ts_dias, to="alguien@ejemplo.org", asunto="Hola", cuerpo="Texto real del correo.", cc=None,
             thrid=None, mixed=False):
    _n[0] += 1
    thrid = thrid or str(_n[0])        # un hilo distinto por defecto: lo contrario sería un duplicado
    return (thrid, AHORA - ts_dias * DIA, _mime(to, asunto, cuerpo, cc, mixed))


def enviado(dias_atras, to, thrid="999"):
    m = EmailMessage()
    m["To"] = to
    return (thrid, AHORA - dias_atras * DIA, m.as_bytes())


def correr(drafts, sent=None, aplicar=False, **kw):
    M = FakeIMAP(drafts, sent or {}, **kw)
    res = H.pasada(aplicar=aplicar, ahora=AHORA, conectar=lambda u, s: M if u == "titular.mgp@gmail.com" else None,
                   out=lambda *a: None)
    return M, res


def reglas(res):
    return {d["uid"]: d["regla"] for d in res["resultados"][0]["decisiones"]} if res["resultados"] else {}


# 1. duplicado: por hilo y por destinatario + asunto normalizado
M, res = correr({1: borrador(5, thrid="7"), 2: borrador(2, thrid="7"),
                 3: borrador(5, to="x@y.example", asunto="Re: Cita"), 4: borrador(1, to="x@y.example", asunto="Cita")})
r = reglas(res)
check("dup hilo: cae el viejo", r.get(1) == "duplicado" and 2 not in r)
check("dup destinatario+asunto sin Re:", r.get(3) == "duplicado" and 4 not in r)

# 2. ya contestado: por hilo, por destinatario, y no si el envío es anterior
M, res = correr({1: borrador(5, thrid="7", to="a@b.com"), 2: borrador(5, to="c@d.com", asunto="Otro"),
                 3: borrador(5, to="e@f.example", asunto="Otro2")},
                {10: enviado(2, "zz@zz.example", thrid="7"), 11: enviado(2, "c@d.com"),
                 12: enviado(9, "e@f.example")})
r = reglas(res)
check("contestado por hilo", r.get(1) == "ya-contestado")
check("contestado por destinatario", r.get(2) == "ya-contestado")
check("envío ANTERIOR al borrador no cuenta", 3 not in r)

# 3. basura
M, res = correr({1: borrador(1, to=""), 2: borrador(1, asunto="NO ENVIAR esto", to="a@b.com"),
                 3: borrador(1, asunto="X", to="g@h.example", cuerpo=""),
                 4: borrador(1, asunto="Y", to="i@j.example", cuerpo="Saludos,\nTitular\n-- \nFirma larga"),
                 5: borrador(1, asunto="Z", to="k@l.example", cuerpo="", mixed=True),
                 6: borrador(1, asunto="W", to="m@n.example", cuerpo="Texto con contenido real.")})
r = reglas(res)
check("basura sin destinatario", r.get(1) == "basura")
check("basura NO ENVIAR", r.get(2) == "basura")
check("basura cuerpo vacío", r.get(3) == "basura")
check("basura solo firma", r.get(4) == "basura")
check("vacío pero con adjunto NO es basura", 5 not in r)
check("cuerpo real sobrevive", 6 not in r)

# 4/5. caducado y protegido
M, res = correr({1: borrador(40, to="a@b.com", asunto="Viejo"),
                 2: borrador(10, to="a@b.com", asunto="Reciente"),
                 3: borrador(40, to="dr@{{CENTRO}}.net", asunto="Consulta"),
                 4: borrador(20, to="a@b.com", asunto="Sobre la biopsia"),
                 5: borrador(10, to="dr@{{CENTRO}}.net", asunto="Reciente clínico"),
                 6: borrador(40, to="q@w.example", asunto="Caduca", cuerpo="Hola, ¿el HLA ya está?")})
r = reglas(res)
check("caducado sin proteger cae", r.get(1) == "caducado")
check("reciente se queda", 2 not in r)
check("protegido por dominio NO cae por caducado", r.get(3) == "protegido-antiguo")
check("protegido por palabra NO cae y a >14d va a Vega", r.get(4) == "protegido-antiguo")
check("protegido <14d no se escala", 5 not in r)
check("palabra HLA en el cuerpo protege", r.get(6) == "protegido-antiguo")
check("ninguno de los protegidos va a la Papelera",
      not any(d["accion"] == "papelera" and d["uid"] in (3, 4, 6) for d in res["resultados"][0]["decisiones"]))

# protegido SÍ cae si es duplicado o contestado
M, res = correr({1: borrador(30, to="dr@{{CENTRO}}.net", asunto="Plan", thrid="5"),
                 2: borrador(3, to="dr@{{CENTRO}}.net", asunto="Plan", thrid="5"),
                 3: borrador(20, to="dr@{{CENTRO}}.net", asunto="Otra cosa")},
                {10: enviado(1, "dr@{{CENTRO}}.net")})
r = reglas(res)
check("protegido duplicado cae", r.get(1) == "duplicado")
check("protegido ya contestado cae", r.get(2) == "ya-contestado" and r.get(3) == "ya-contestado")

# 6. avisos
M, res = correr({1: borrador(2, to="a@b.com", asunto="Con CC", cc="coordinadora@prensa.example"),
                 2: borrador(2, to="c@d.com", asunto="Propuesta", cuerpo="¿Hacemos una videollamada el jueves?"),
                 3: borrador(2, to="e@f.example", asunto="Cita", cuerpo="Vale, gracias.\n\nOn lun wrote:\n> una llamada?")})
dec = res["resultados"][0]["decisiones"]
check("aviso CC", any(d["uid"] == 1 and d["regla"] == "cc-antigua-coordinadora" and d["accion"] == "aviso" for d in dec))
check("aviso llamada", any(d["uid"] == 2 and d["regla"] == "propone-llamada" for d in dec))
check("la llamada solo en la cita NO avisa", not any(d["uid"] == 3 for d in dec))

# dry-run: no escribe nada
drafts = {1: borrador(40, to="a@b.com", asunto="Viejo"), 2: borrador(20, to="dr@{{CENTRO}}.net", asunto="Clínico")}
M, res = correr(drafts, aplicar=False)
check("dry-run: sin escrituras IMAP", M.escrituras == [])
check("dry-run: carpeta de solo lectura", M.readonly is True)
check("dry-run: sin registro", not os.path.exists(H._registro_path()))
check("dry-run: sin buzón de Vega", not os.path.exists(H._buzon_path()))
check("dry-run: decide igualmente", reglas(res).get(1) == "caducado")

# apply: COPY a \Trash, expunge solo de lo copiado
drafts = {1: borrador(40, to="a@b.com", asunto="Viejo uno"), 2: borrador(41, to="c@d.com", asunto="Viejo dos"),
          3: borrador(50, to="dr@{{CENTRO}}.net", asunto="Clínico")}
M, res = correr(drafts, aplicar=True, copy_falla={2})
check("apply: copia a la carpeta \\Trash", M.copiados == [(1, "[Gmail]/Papelera")])
check("apply: marca \\Deleted solo lo copiado", M.flagged == [1])
check("apply: UID EXPUNGE solo lo copiado", M.expunged == [1])
check("apply: nunca un EXPUNGE a secas", not any(e[0] == "EXPUNGE" and len(e) == 1 for e in M.escrituras))
check("apply: lo protegido ni se copia", 3 not in [u for u, _ in M.copiados])
reg = [json.loads(x) for x in open(H._registro_path(), encoding="utf-8")]
check("apply: registro solo de lo movido", [x["uid"] for x in reg] == [1] and reg[0]["regla"] == "caducado")
check("apply: el registro lleva los campos", {"ts", "cuenta", "uid", "asunto", "destinatario", "regla"} <= set(reg[0]))
buzon = [json.loads(x) for x in open(H._buzon_path(), encoding="utf-8")]
check("apply: una entrada en Vega con el origen", [b["origen"] for b in buzon] == ["borradores-higiene"])
M, res = correr({1: borrador(40, to="a@b.com", asunto="Otro viejo")}, aplicar=True)
buzon = [json.loads(x) for x in open(H._buzon_path(), encoding="utf-8")]
check("apply de nuevo: sustituye, no suma", len(buzon) == 1)

# nada que contar: no toca Vega
os.remove(H._buzon_path())
M, res = correr({1: borrador(1, to="a@b.com", asunto="Fresco")}, aplicar=True)
check("sin movimientos ni protegidos: Vega intacto", not os.path.exists(H._buzon_path()))

# host fail-closed y cuenta sin contraseña
llamadas = []
_orig = (H.imaplib.IMAP4_SSL, H._secrets.get, OB.IMAP_HOST)
H.imaplib.IMAP4_SSL = lambda *a, **k: llamadas.append(a) or FakeIMAP({}, {})
H._secrets.get = lambda servicio: "pw"
try:
    OB.IMAP_HOST = "evil.example.com"
    try:
        H._conectar("titular.mgp@gmail.com", "svc")
        cerrado = False
    except RuntimeError:
        cerrado = True
    check("host distinto: aborta", cerrado)
    check("host distinto: ni se abre el socket", llamadas == [])
finally:
    OB.IMAP_HOST = _orig[2]
    H.imaplib.IMAP4_SSL, H._secrets.get = _orig[0], _orig[1]
H._secrets.get = lambda servicio: ""
try:
    check("sin contraseña: _conectar devuelve None", H._conectar("titular.mgp@gmail.com", "svc") is None)
finally:
    H._secrets.get = _orig[1]
# 14-oct-26 · la 1ª pasada real marcó 42/42 como basura: Gmail no garantiza el orden de los ítems.
# (a) el cuerpo llega ANTES que la cabecera → se elige cada literal por su etiqueta, no por posición.
cab = b"To: Dan <dan@onco.example>\r\nSubject: Curative\r\n\r\n"
data = [(b'7 (UID 7 X-GM-THRID 55 INTERNALDATE "14-Oct-2026 09:00:00 +0000" BODY[TEXT]<0> {12}', b"Hola Dan, si"),
        (b" BODY[HEADER] {%d}" % len(cab), cab), b")"]
d = H._parse_fetch(data)[7]
check("orden Gmail: _parte coge la cabecera aunque venga 2ª", H._parte(d, b"BODY[HEADER]") == cab)
check("orden Gmail: _parte coge el cuerpo aunque venga 1º", H._parte(d, b"BODY[TEXT]") == b"Hola Dan, si")
check("sin la etiqueta pedida devuelve None (no se juzga)", H._parte(d, b"BODY[HEADER.FIELDS") is None)

# (b) freno: si una pasada quiere mover más del 30 % de la bandeja, no mueve nada.
M, res = correr({i: borrador(1, to="", asunto="x%d" % i) for i in range(1, 11)}, aplicar=True)
r0 = res["resultados"][0]
check("freno: 10/10 basura → frenado, 0 movidos", r0.get("frenado") == 10 and not r0["movidos"])
check("freno: ninguna escritura IMAP", not M.escrituras)

# cita de Gmail partida en dos líneas: la «call» del remitente no cuenta como propuesta suya
txt = "Email works best for me.\n\nOn Tue, Oct 13, 2026 at 11:50 PM Lexie <\nlexie@ia.example> wrote:\n> I'd love to set up a quick call"
check("cita partida: se corta antes de «wrote:»", "call" not in H._cuerpo_propio(txt) and "Email works" in H._cuerpo_propio(txt))

msgs = []
res = H.pasada(ahora=AHORA, conectar=lambda u, s: None, out=msgs.append)
check("sin contraseña: se salta con aviso", len(res["saltadas"]) == len(OB.ACCOUNTS) and msgs)

# registro estructural: plist y concern
plist = open(os.path.join(ROOT, "tools", "launchd", "com.btp.borradores-higiene.plist"), encoding="utf-8").read()
check("plist: 07:40 y apunta a la tool", "<integer>7</integer>" in plist and "<integer>40</integer>" in plist
      and "borradores_higiene.py" in plist)
reg_ld = json.load(open(os.path.join(ROOT, "tools", "launchd", "REGISTRO.json"), encoding="utf-8"))
check("REGISTRO: concern borradores-higiene",
      any(v.get("concern") == "borradores-higiene" for v in json.dumps(reg_ld) and
          (reg_ld.get("jobs") or reg_ld.get("daemons") or [x for x in reg_ld.values() if isinstance(x, dict)][0]).values()))

print("%d ok, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
