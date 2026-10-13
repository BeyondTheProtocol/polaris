#!/usr/bin/env python3
"""test_pendientes_hilo.py — «¿a quién le toca?» se decide por el HILO, no por una flag.

Nace del 13-oct-2026: el ledger de pendientes tenía 1557 hilos abiertos y 38 cerrados porque
solo sabía que {{TITULAR}} había contestado si el mensaje original conservaba \\Answered y seguía
en la ventana del buzón. Cierra la deuda `vega-no-detecta-respuesta-en-hilo-trackeado`.

Verifica, con un Gmail simulado (cero red):
  · correo_imap.estado_hilo: último mensaje de un tercero → te toca; último de ella → le toca
    al otro; respuesta desde la OTRA cuenta o en hilo aparte → le toca al otro; no localizado
    → encontrado=False (nunca se da por contestado lo que no se ha visto).
  · «X» y «Re: X» son UN pendiente; las entradas viejas con «Re:» siguen sirviendo de alias.
  · un mensaje nuevo REABRE un hilo cerrado o en espera; el mismo correo viejo, no.
  · revisar_hilos: pasa a `esperando` lo contestado, no toca lo de acción requerida, fusiona
    dos entradas del mismo hilo, saca del silencio un NED reciente, y no cambia nada si
    falla la consulta.
  · re-pinchar: una vez por mensaje suyo y contando días hábiles.
  · posponer: «mañana <id>» calla ese hilo, no los demás.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

_TMP = tempfile.mkdtemp(prefix="test_pendientes_hilo_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
os.environ.pop("BTP_PENDIENTES_SOLO_PARTE", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pendientes as p     # noqa: E402
import correo              # noqa: E402
import correo_imap as ci   # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


AHORA = datetime(2026, 10, 13, 12, 0, 0)   # martes, UTC naive (como el ledger)
YO, YO2 = "titular.mgp@gmail.com", "titular@gmail.com"
DOC = "doctora@{{CENTRO}}.net"


def rfc(dt):
    return format_datetime(dt.replace(tzinfo=timezone.utc))


def ts(dt):
    return dt.replace(tzinfo=timezone.utc).timestamp()


class Falso:
    """Una cuenta de Gmail simulada. Entiende lo justo de X-GM-RAW que usa estado_hilo."""

    def __init__(self, cuenta, mensajes):
        self.cuenta, self.m, self.cerrado = cuenta, {x["uid"]: x for x in mensajes}, False

    def buscar_raw(self, q):
        out = []
        for u, x in self.m.items():
            if q.startswith("rfc822msgid:"):
                ok = x.get("mid") == q.split(":", 1)[1]
            elif q.startswith("in:sent to:"):
                ok = bool(x.get("sent")) and x.get("to") == q.split("to:", 1)[1].split()[0]
            elif q.startswith("from:"):
                quien = q.split("from:", 1)[1].split(" subject:")[0].strip()
                asunto = q.split("subject:(", 1)[1].rstrip(")")
                ok = quien in x["from"] and asunto.lower() in x["subject"].lower()
            else:
                ok = False
            if ok:
                out.append(u)
        return sorted(out)

    def thrid(self, uid):
        return self.m[uid]["thrid"]

    def uids_de_hilo(self, thrid):
        return sorted(u for u, x in self.m.items() if x["thrid"] == thrid)

    def fetch_header(self, uid):
        x = self.m[uid]
        return {"from": x["from"], "subject": x["subject"], "date": x["date"],
                "message_id": "<%s>" % x.get("mid", ""), "flags": ""}

    def close(self):
        self.cerrado = True


def mundo(*cuentas):
    d = {c.cuenta: c for c in cuentas}

    def abrir(user, _secret):
        if user not in d:
            raise ci.FaltaClave(user)
        return d[user]
    return abrir


def m(uid, frm, subject, dt, *, mid="", thrid="1", sent=False, to=""):
    return {"uid": uid, "from": frm, "subject": subject, "date": rfc(dt), "mid": mid,
            "thrid": thrid, "sent": sent, "to": to}


def limpia():
    for f in (p.LEDGER, p.LEDGER + ".lock"):
        if os.path.exists(f):
            os.remove(f)


def entrante(asunto, *, horas=48, email=DOC, nombre="Dra. Ejemplo", mid="a1", flags="",
             cuenta=YO):
    return {"remitente": nombre, "remitente_email": email, "asunto": asunto, "flags": flags,
            "fecha": rfc(AHORA - timedelta(hours=horas)), "message_id": "<%s>" % mid,
            "cuenta": cuenta, "inyeccion": False}


BASE = {"ultimo_nudge_ts": None, "n_nudges": 0, "estado": "abierto", "prioridad": "ned",
        "remitente": "Dra. Ejemplo", "remitente_email": DOC}
DOS = [(YO, "x"), (YO2, "y")]


def t_estado_hilo():
    t0 = AHORA - timedelta(hours=48)
    a = Falso(YO, [m(1, DOC, "Cita biopsia", t0, mid="a1")])
    r = ci.estado_hilo("<a1>", remitente_email=DOC, asunto="Cita biopsia", abrir=mundo(a), cuentas=DOS)
    check("último de un tercero → te toca", r["encontrado"] and r["turno"] == "titular")
    check("cierra la conexión que abrió", a.cerrado)

    a = Falso(YO, [m(1, DOC, "Cita biopsia", t0, mid="a1"),
                   m(2, YO, "Re: Cita biopsia", t0 + timedelta(hours=3), sent=True, to=DOC)])
    r = ci.estado_hilo("<a1>", remitente_email=DOC, asunto="Cita biopsia", abrir=mundo(a), cuentas=DOS)
    check("último suyo en el hilo → le toca al otro", r["turno"] == "otro" and r["via"] == "hilo")

    a = Falso(YO, [m(1, DOC, "Cita biopsia", t0, mid="a1"),
                   m(2, YO, "Re: Cita biopsia", t0 + timedelta(hours=3), sent=True, to=DOC),
                   m(3, DOC, "Re: Cita biopsia", t0 + timedelta(hours=9))])
    r = ci.estado_hilo("<a1>", remitente_email=DOC, asunto="Cita biopsia", abrir=mundo(a), cuentas=DOS)
    check("el otro volvió a escribir → te toca otra vez", r["turno"] == "titular" and r["n"] == 3)

    a = Falso(YO, [m(1, DOC, "Cita biopsia", t0, mid="a1")])
    b = Falso(YO2, [m(7, YO2, "Sobre la cita", t0 + timedelta(hours=5), thrid="9", sent=True, to=DOC)])
    r = ci.estado_hilo("<a1>", remitente_email=DOC, asunto="Cita biopsia", fecha=rfc(t0),
                       abrir=mundo(a, b), cuentas=DOS)
    check("contestó desde la OTRA cuenta → le toca al otro",
          r["turno"] == "otro" and r["via"] == "enviado-aparte")

    a = Falso(YO, [m(1, DOC, "Cita biopsia", t0, mid="a1")])
    b = Falso(YO2, [m(7, YO2, "Otra cosa", t0 - timedelta(days=9), thrid="9", sent=True, to=DOC)])
    r = ci.estado_hilo("<a1>", remitente_email=DOC, asunto="Cita biopsia", fecha=rfc(t0),
                       abrir=mundo(a, b), cuentas=DOS)
    check("un envío suyo ANTERIOR al mensaje no cuenta como respuesta", r["turno"] == "titular")

    r = ci.estado_hilo("<noexiste>", remitente_email="x@y.z", asunto="Nada",
                       abrir=mundo(Falso(YO, [])), cuentas=DOS)
    check("no localizado → encontrado=False", r["encontrado"] is False)

    a = Falso(YO, [m(1, DOC, "Re: Cita biopsia", t0, mid="zz")])
    r = ci.estado_hilo("", remitente_email=DOC, asunto="RE: Cita biopsia", abrir=mundo(a), cuentas=DOS)
    check("sin Message-ID se localiza por remitente + asunto base", r["encontrado"])

    try:
        ci.estado_hilo("<a1>", abrir=mundo(), cuentas=DOS)
        check("sin ninguna clave → FaltaClave", False)
    except ci.FaltaClave:
        check("sin ninguna clave → FaltaClave", True)
    src = open(os.path.join(ROOT, "tools", "correo_imap.py"), encoding="utf-8").read()
    check("correo_imap sigue sin poder enviar", "import smtplib" not in src)
    check("…y sin mutar el buzón", not any(
        x in src for x in ('"store"', '"copy"', '"expunge"', '"append"', "readonly=False")))
    check("es_mia reconoce sus cuentas y no las ajenas",
          ci.es_mia("{{TITULAR}} <%s>" % YO2) and not ci.es_mia(DOC))


def t_un_hilo_una_entrada():
    limpia()
    p.sincronizar([entrante("Cita biopsia"), entrante("Re: Cita biopsia", horas=30, mid="a2")], ahora=AHORA)
    led = p.cargar()
    check("«X» y «Re: X» colapsan en una entrada", len(led) == 1)
    e = next(iter(led.values()))
    check("guarda el Message-ID del entrante más reciente",
          e.get("message_id") == "<a2>" and e.get("cuenta") == YO)

    limpia()
    viejo = correo._aviso_key(DOC, "Re: Informe")
    p._guardar_raw({viejo: dict(BASE, primer_visto="2026-09-01T00:00:00", asunto="Re: Informe")})
    p.sincronizar([entrante("Re: Informe", mid="v1")], ahora=AHORA)
    led = p.cargar()
    check("una entrada vieja con «Re:» se reutiliza (no nace duplicado)", list(led) == [viejo])
    check("…y se rellena con el Message-ID", led[viejo].get("message_id") == "<v1>")
    ok, _ = p.cerrar(viejo[:10], motivo="ok")
    check("«ok <id>» sigue funcionando con el id viejo", ok)


def t_reapertura():
    limpia()
    p.sincronizar([entrante("Cita biopsia")], ahora=AHORA)
    tid = next(iter(p.cargar()))

    def _cierra(led):
        led[tid].update(estado="cerrado", motivo_cierre="ok", cerrado_ts=p._iso(AHORA))
    p._con_lock(_cierra)
    r = p.sincronizar([entrante("Cita biopsia")], ahora=AHORA + timedelta(hours=1))
    check("el mismo correo viejo NO reabre un hilo cerrado",
          p.cargar()[tid]["estado"] == "cerrado" and not r["reabiertos"])
    nuevo = dict(entrante("Re: Cita biopsia", mid="a9"), fecha=rfc(AHORA + timedelta(hours=20)))
    r = p.sincronizar([nuevo], ahora=AHORA + timedelta(days=2))
    e = p.cargar()[tid]
    check("un mensaje posterior al cierre REABRE el hilo",
          e["estado"] == "abierto" and r["reabiertos"] == [tid])
    check("…sin arrastrar el cierre ni los avisos viejos", "cerrado_ts" not in e and e["n_nudges"] == 0)


def t_revisar_hilos():
    limpia()
    p.sincronizar([entrante("Cita biopsia", mid="h1"),
                   entrante("Resultados analítica", mid="h2", email="lab@{{CENTRO}}.net", nombre="Lab"),
                   entrante("Documentacion: caduca el viernes", mid="h3",
                            email="arxiu@hospital.example", nombre="Arxiu"),
                   entrante("Oferta 2x1", mid="h4", email="promo@tienda.example", nombre="Tienda")],
                  ahora=AHORA)
    led = p.cargar()
    por_mid = {e["message_id"]: k for k, e in led.items()}
    check("el de promoción es baja", led[por_mid["<h4>"]]["prioridad"] == "baja")
    suyo = AHORA - timedelta(hours=20)
    vistos = []

    def consulta(e):
        vistos.append(e["message_id"])
        if e["message_id"] in ("<h1>", "<h3>"):
            return {"encontrado": True, "turno": "otro", "thrid": e["message_id"], "cuenta": YO,
                    "ultimo_ts": ts(suyo), "via": "hilo"}
        return {"encontrado": False, "errores": []}

    p.revisar_hilos(ahora=AHORA, consulta=consulta)
    led = p.cargar()
    check("solo consulta NED y media", "<h4>" not in vistos and len(vistos) == 3)
    check("contestado → esperando", led[por_mid["<h1>"]]["estado"] == "esperando")
    check("esperando_desde es la hora de SU mensaje (UTC)",
          led[por_mid["<h1>"]]["esperando_desde"] == p._iso(suyo))
    check("acción requerida NO se cierra aunque ella contestara",
          led[por_mid["<h3>"]]["estado"] == "abierto" and led[por_mid["<h3>"]]["turno"] == "otro")
    check("no localizado → sigue abierto y se cuenta",
          led[por_mid["<h2>"]]["estado"] == "abierto"
          and led[por_mid["<h2>"]].get("hilo_no_encontrado") == 1)
    ned, media = p.a_avisar(ahora=AHORA)
    check("lo que espera al otro ya no avisa", por_mid["<h1>"] not in [t for t, _ in ned + media])
    vistos.clear()
    p.revisar_hilos(ahora=AHORA + timedelta(hours=1), consulta=consulta)
    check("no vuelve a preguntar por el mismo hilo antes de 6 h", vistos == [])

    nuevo = dict(entrante("Re: Cita biopsia", mid="h1b"), fecha=rfc(AHORA - timedelta(hours=7)))
    p.sincronizar([nuevo], ahora=AHORA)
    check("respuesta del otro reabre un hilo en espera",
          p.cargar()[por_mid["<h1>"]]["estado"] == "abierto")

    limpia()
    p.sincronizar([entrante("Cita biopsia", mid="f1")], ahora=AHORA)

    def revienta(_e):
        raise OSError("421 server busy")
    r = p.revisar_hilos(ahora=AHORA, consulta=revienta)
    e = next(iter(p.cargar().values()))
    check("si Gmail falla no se cambia nada y se dice",
          e["estado"] == "abierto" and r["errores"] and r["revisados"] == 0)

    limpia()
    k1, k2 = correo._aviso_key(DOC, "Informe AP"), correo._aviso_key(DOC, "Re: Informe AP")
    p._guardar_raw({k1: dict(BASE, primer_visto="2026-10-01T00:00:00", asunto="Informe AP"),
                    k2: dict(BASE, primer_visto="2026-10-02T00:00:00", asunto="Re: Informe AP")})
    p.revisar_hilos(ahora=AHORA, consulta=lambda e: {
        "encontrado": True, "turno": "titular", "thrid": "77", "cuenta": YO,
        "ultimo_ts": ts(AHORA - timedelta(days=1)), "via": "hilo"})
    led = p.cargar()
    check("dos entradas del mismo hilo → una abierta y otra fusionada",
          sorted(x["estado"] for x in led.values()) == ["abierto", "fusionado"])
    fus = next(k for k, x in led.items() if x["estado"] == "fusionado")
    dueno = led[fus]["fusionado_en"]
    check("la fusionada apunta a la dueña", dueno in led and dueno != fus)
    p.sincronizar([dict(entrante(led[fus]["asunto"], mid="n1"))], ahora=AHORA)
    check("un mensaje que cae en la clave fusionada va a la dueña",
          p.cargar()[dueno].get("message_id") == "<n1>")

    limpia()
    p._guardar_raw({"s1": dict(BASE, primer_visto="2026-10-05T00:00:00", asunto="Reciente", silenciado=True),
                    "s2": dict(BASE, primer_visto="2026-07-12T00:00:00", asunto="Antiguo", silenciado=True)})
    p.revisar_hilos(ahora=AHORA, consulta=lambda e: {
        "encontrado": True, "turno": "titular", "thrid": e["asunto"], "cuenta": YO, "via": "hilo",
        "ultimo_ts": ts(AHORA - timedelta(days=3 if e["asunto"] == "Reciente" else 90))})
    led = p.cargar()
    check("NED silenciado con hilo reciente vuelve a avisar", "silenciado" not in led["s1"])
    check("NED silenciado de hace meses sigue callado pero abierto",
          led["s2"].get("silenciado") and led["s2"]["estado"] == "abierto")


def t_repinchar_y_posponer():
    limpia()
    p._guardar_raw({
        "w1": dict(BASE, primer_visto="2026-10-01T00:00:00", asunto="Ping", estado="esperando",
                   esperando_desde="2026-10-09T10:00:00"),      # viernes → 2 hábiles
        "w2": dict(BASE, primer_visto="2026-10-01T00:00:00", asunto="Viejo", estado="esperando",
                   esperando_desde="2026-10-02T10:00:00")})     # 7 hábiles
    check("re-pinchar cuenta días hábiles (el finde no suma)",
          [t for t, _ in p.esperando_vencidos(ahora=AHORA)] == ["w2"])
    check("dias_habiles vie→mar = 2", p.dias_habiles(datetime(2026, 10, 9, 10), AHORA) == 2)

    def _marca(led):
        led["w2"]["repinchado_de"] = led["w2"]["esperando_desde"]
    p._con_lock(_marca)
    check("un hilo ya re-pinchado no se repite", p.esperando_vencidos(ahora=AHORA) == [])

    limpia()
    p._guardar_raw({"aaaa1111": dict(BASE, primer_visto="2026-10-10T00:00:00", asunto="Uno"),
                    "bbbb2222": dict(BASE, primer_visto="2026-10-10T00:00:00", asunto="Dos")})
    ok, _ = p.posponer("aaaa", modo="manana", ahora=AHORA)
    check("«mañana <id>» calla ese hilo y solo ese",
          ok and [t for t, _ in p.a_avisar(ahora=AHORA)[0]] == ["bbbb2222"])
    check("…y mañana vuelve",
          "aaaa1111" in [t for t, _ in p.a_avisar(ahora=AHORA + timedelta(days=1))[0]])
    ok, _ = p.posponer("bbbb", modo="espero", ahora=AHORA)
    check("«espero <id>» lo pasa a esperando", ok and p.cargar()["bbbb2222"]["estado"] == "esperando")
    ok, _ = p.posponer("zzzz", modo="manana", ahora=AHORA)
    check("id inexistente no toca nada", not ok)


def t_diario():
    limpia()
    for f in (p.DIARIO_ESTADO, p.DIARIO_EN_SECO):
        if os.path.exists(f):
            os.remove(f)
    hace = lambda d: p._iso(AHORA - timedelta(days=d))
    p._guardar_raw({
        "c1": dict(BASE, primer_visto=hace(9), asunto="Elegibilidad", turno="titular", ultimo_ts_hilo=hace(7)),
        "c2": dict(BASE, primer_visto=hace(2), asunto="Cita", turno="titular", ultimo_ts_hilo=hace(1)),
        "c3": dict(BASE, primer_visto=hace(2), asunto="Sin comprobar"),                 # sin turno
        "c4": dict(BASE, primer_visto=hace(9), asunto="Viejo", turno="titular", silenciado=True),
        "c5": dict(BASE, primer_visto=hace(9), asunto="Promo", turno="titular", prioridad="baja"),
        "w1": dict(BASE, primer_visto=hace(20), asunto="Ping", estado="esperando", esperando_desde=hace(11))})
    txt = p.texto_diario(ahora=AHORA)
    check("lo NED de más de 3 días hábiles va primero y marcado",
          txt.splitlines()[1].startswith("🔴") and "Elegibilidad" in txt.splitlines()[1])
    check("no echa en cara lo no comprobado, lo silenciado ni lo de baja",
          not any(x in txt for x in ("Sin comprobar", "Viejo", "Promo")))
    check("incluye lo que el otro no ha contestado", "No te han contestado (1)" in txt and "Ping" in txt)

    martes = datetime(2026, 10, 13, 11, 0)
    enviados = []
    os.environ["BTP_PENDIENTES_SOLO_PARTE"] = "1"
    r = p.diario(ahora=AHORA, ahora_local=martes, avisar=lambda t: enviados.append(t) or {"delivered": True})
    check("en seco no manda nada y lo deja escrito",
          not enviados and r.get("en_seco") and "Elegibilidad" in open(p.DIARIO_EN_SECO, encoding="utf-8").read())
    os.environ.pop("BTP_PENDIENTES_SOLO_PARTE")
    r = p.diario(ahora=AHORA, ahora_local=datetime(2026, 10, 17, 11, 0),
                 avisar=lambda t: enviados.append(t) or {"delivered": True})
    check("en sábado no sale", not enviados)
    r = p.diario(ahora=AHORA, ahora_local=martes, avisar=lambda t: {"delivered": False})
    check("si no se entrega no se sella (se reintenta)", not r["enviado"] and not os.path.exists(p.DIARIO_ESTADO)
          or "enviado" not in __import__("json").load(open(p.DIARIO_ESTADO)))
    r = p.diario(ahora=AHORA, ahora_local=martes, avisar=lambda t: enviados.append(t) or {"delivered": True})
    check("encendido, manda UN mensaje", r["enviado"] and len(enviados) == 1)
    p.diario(ahora=AHORA, ahora_local=martes.replace(hour=17), avisar=lambda t: enviados.append(t) or {"delivered": True})
    check("y no repite el mismo día", len(enviados) == 1)
    check("el «no te han contestado» queda dicho una vez", p.esperando_vencidos(ahora=AHORA) == [])
    p.diario(ahora=AHORA + timedelta(days=1), ahora_local=martes + timedelta(days=1),
             avisar=lambda t: enviados.append(t) or {"delivered": True})
    check("al día siguiente insiste con lo que sigue abierto",
          len(enviados) == 2 and "Elegibilidad" in enviados[1] and "Ping" not in enviados[1])


def t_prueba_real_13oct():
    """Lo que destapó la primera pasada contra Gmail real (13-oct, 19:13): una respuesta
    automática y un boletín de hospital salían como «te toca», y hilos de hace meses salían
    con «llevas 50 días hábiles sin contestar»."""
    from email.message import EmailMessage
    def cab(**k):
        x = EmailMessage()
        for a, b in k.items():
            x[a.replace("_", "-")] = b
        return x
    check("boletín (List-Unsubscribe) es de máquina", ci.cabeceras_de_maquina(cab(List_Unsubscribe="<mailto:x@y>")))
    check("respuesta automática (Auto-Submitted) es de máquina", ci.cabeceras_de_maquina(cab(Auto_Submitted="auto-replied")))
    check("un correo normal no lo es", not ci.cabeceras_de_maquina(cab(Auto_Submitted="no", Subject="Cita")))

    t0 = AHORA - timedelta(hours=48)
    a = Falso(YO, [m(1, DOC, "Automatische Antwort: [Extern] Informe", t0, mid="au")])
    r = ci.estado_hilo("<au>", remitente_email=DOC, asunto="Informe", abrir=mundo(a), cuentas=DOS)
    check("«Automatische Antwort» se marca automático por el asunto", r.get("automatico") is True)
    a = Falso(YO, [m(1, DOC, "Vacunas disponibles", t0, mid="bo")])
    a.es_automatico = lambda uid: True
    r = ci.estado_hilo("<bo>", remitente_email=DOC, asunto="Vacunas disponibles", abrir=mundo(a), cuentas=DOS)
    check("y por las cabeceras del último mensaje", r.get("automatico") is True)

    limpia()
    hace = lambda d: p._iso(AHORA - timedelta(days=d))
    p._guardar_raw({
        "r1": dict(BASE, primer_visto=hace(3), asunto="Reciente", turno="titular", ultimo_ts_hilo=hace(2)),
        "a1": dict(BASE, primer_visto=hace(3), asunto="Boletín", turno="titular", ultimo_ts_hilo=hace(2), auto=True),
        "v1": dict(BASE, primer_visto=hace(80), asunto="De agosto", turno="titular", ultimo_ts_hilo=hace(70)),
        "w1": dict(BASE, primer_visto=hace(90), asunto="Ping viejo", estado="esperando", esperando_desde=hace(80)),
        "n1": dict(BASE, primer_visto=hace(3), asunto="Perdido", hilo_no_encontrado=3,
                   hilo_visto_ts=hace(2))})
    txt = p.texto_diario(ahora=AHORA)
    check("lo de máquina no sale como «te toca»", "Boletín" not in txt)
    check("lo de hace meses no se echa en cara, se cuenta en una línea",
          "De agosto" not in txt and "1 hilo de hace más de un mes" in txt)
    check("ni se re-pincha lo que lleva más de un mes callado", "Ping viejo" not in txt)
    check("…pero sigue abierto y se puede listar",
          [t for t, _e, _d in p.te_toca(ahora=AHORA, antiguos=True)] == ["v1"])
    check("lo de máquina tampoco entra en el aviso viejo",
          "a1" not in [t for t, _ in sum(p.a_avisar(ahora=AHORA), [])])
    vistos = []
    p.revisar_hilos(ahora=AHORA, consulta=lambda e: vistos.append(e["asunto"]) or {"encontrado": False})
    check("tras 3 intentos sin localizarlo deja de preguntar por ese hilo", "Perdido" not in vistos)
    p.revisar_hilos(ahora=AHORA, consulta=lambda e: {
        "encontrado": True, "turno": "titular", "thrid": e["asunto"], "cuenta": YO,
        "ultimo_ts": ts(AHORA - timedelta(days=1)), "via": "hilo", "automatico": e["asunto"] == "Reciente"},
        forzar=True)
    check("revisar_hilos guarda la marca de automático", p.cargar()["r1"].get("auto") is True)


def main():
    t_prueba_real_13oct()
    t_diario()
    t_estado_hilo()
    t_un_hilo_una_entrada()
    t_reapertura()
    t_revisar_hilos()
    t_repinchar_y_posponer()
    print("\nRESULTADO pendientes_hilo: %d OK, %d fallos" % (_pass, _fail))
    print("✅ PENDIENTES POR HILO EN VERDE" if not _fail else "❌ HAY FALLOS")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
