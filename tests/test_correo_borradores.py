#!/usr/bin/env python3
"""test_correo_borradores.py — el borrador se prepara solo, antes de avisar (13-oct-2026).

Verifica, SIN RED y sin modelo (lector y redactor inyectados):
  · correo_responder.candidatos(): solo hilos confirmados «te toca», de persona real, NED o
    media, movidos hace poco y sin borrador previo.
  · pasada(): pocos por pasada, destinatario = remitente del SOBRE, respuesta colgada del
    hilo (in_reply_to), y el resultado apuntado en el hilo.
  · si el redactor no puede, queda dicho en el hilo y en el mensaje del día (nada mudo), y no
    se reintenta hasta pasadas unas horas.
  · el extracto que viaja a Telegram va sin enlaces.
  · correo_imap._texto_de: saca text/plain, cae a HTML sin etiquetas, ignora adjuntos.
  · el borrador sale en HTML con lo del modelo escapado, y con In-Reply-To.
  · BTP_CORREO_BORRADORES=0 lo apaga.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage

_TMP = tempfile.mkdtemp(prefix="test_correo_borradores_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
os.environ.pop("BTP_CORREO_BORRADORES", None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pendientes as p          # noqa: E402
import correo_responder as cr   # noqa: E402
import correo_imap as ci        # noqa: E402
import correo_outbox            # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


AHORA = datetime(2026, 10, 13, 12, 0, 0)
hace = lambda d: p._iso(AHORA - timedelta(days=d))   # noqa: E731
YO = "titular.mgp@gmail.com"


def hilo(asunto, **kw):
    base = {"ultimo_nudge_ts": None, "n_nudges": 0, "estado": "abierto", "prioridad": "ned",
            "remitente": "Dra. Ejemplo", "remitente_email": "doctora@{{CENTRO}}.net", "asunto": asunto,
            "primer_visto": hace(3), "turno": "titular", "ultimo_ts_hilo": hace(2),
            "message_id": "<%s@x>" % asunto.replace(" ", ""), "cuenta": YO}
    base.update(kw)
    return base


def limpia(led):
    for f in (p.LEDGER, p.LEDGER + ".lock"):
        if os.path.exists(f):
            os.remove(f)
    p._guardar_raw(led)


def main():
    limpia({
        "ok1": hilo("Cita"),
        "ok2": hilo("Elegibilidad", prioridad="media"),
        "ok3": hilo("Tercero"),
        "no_turno": hilo("Sin comprobar", turno=None),
        "no_viejo": hilo("Parado", ultimo_ts_hilo=hace(40)),
        "no_robot": hilo("Aviso", remitente_email="no-reply@avisos.example"),
        "no_baja": hilo("Promo", prioridad="baja"),
        "no_hecho": hilo("Ya", borrador_ts=hace(1)),
        "no_sobre": hilo("Sin sobre", message_id=""),
        "no_espera": hilo("Contestado", estado="esperando"),
    })
    c = [t for t, _ in cr.candidatos(ahora=AHORA)]
    check("candidatos: solo los confirmados, recientes, de persona y sin borrador",
          sorted(c) == ["ok1", "ok2", "ok3"])

    leidos, llamadas = [], []

    def leer(mid, cuenta):
        leidos.append((mid, cuenta))
        return "Hola {{TITULAR}}, ¿te va bien el jueves?"

    def redactar(**kw):
        llamadas.append(kw)
        return {"ok": True, "texto": "Hola. Sí, el jueves me va bien. Mira https://ejemplo.test/x gracias.",
                "sospechoso": False}

    r = cr.pasada(ahora=AHORA, leer=leer, redactar=redactar)
    led = p.cargar()
    check("no más de 2 por pasada", r["hechos"] == 2 and len(llamadas) == 2 and r["candidatos"] == 3)
    k = llamadas[0]
    check("el destinatario es el remitente del sobre", k["remitente_email"] == "doctora@{{CENTRO}}.net")
    check("va colgado del hilo y desde la cuenta del hilo",
          k["in_reply_to"].startswith("<") and k["account"] == YO)
    hechos = [t for t in ("ok1", "ok2", "ok3") if led[t].get("borrador_ts")]
    check("queda apuntado en el hilo", len(hechos) == 2)
    ex = led[hechos[0]]["borrador_extracto"]
    check("el extracto no lleva enlaces", "http" not in ex and "[enlace]" in ex)
    check("ya no son candidatos", len(cr.candidatos(ahora=AHORA)) == 1)
    txt = p.texto_diario(ahora=AHORA)
    check("el mensaje del día lleva el texto del borrador", "📝" in txt and "el jueves me va bien" in txt)

    # el redactor no puede: se dice, y no se machaca en cada pasada
    r = cr.pasada(ahora=AHORA, leer=leer,
                  redactar=lambda **kw: {"ok": False, "etapa": "redaccion", "motivo": "Claude sin capacidad"})
    led = p.cargar()
    falta = next(t for t in ("ok1", "ok2", "ok3") if not led[t].get("borrador_ts"))
    check("si no puede redactar queda dicho en el hilo",
          r["sin_redactar"] == 1 and "sin capacidad" in led[falta].get("borrador_pendiente", ""))
    check("…y en el mensaje del día", "sin borrador todavía" in p.texto_diario(ahora=AHORA))
    check("no reintenta antes de 6 h", cr.candidatos(ahora=AHORA + timedelta(hours=1)) == [])
    check("y sí después", len(cr.candidatos(ahora=AHORA + timedelta(hours=7))) == 1)

    # sin cuerpo
    limpia({"a": hilo("Vacio")})
    r = cr.pasada(ahora=AHORA, leer=lambda m, c: "", redactar=redactar)
    check("sin cuerpo no se redacta nada", r["sin_cuerpo"] == 1 and r["hechos"] == 0
          and p.cargar()["a"].get("borrador_pendiente"))

    # dry no escribe
    limpia({"a": hilo("Seco")})
    cr.pasada(ahora=AHORA, leer=leer, redactar=redactar, dry=True)
    check("dry no toca el ledger", "borrador_ts" not in p.cargar()["a"])

    # apagado
    os.environ["BTP_CORREO_BORRADORES"] = "0"
    n = len(llamadas)
    r = cr.pasada(ahora=AHORA, leer=leer, redactar=redactar)
    check("BTP_CORREO_BORRADORES=0 lo apaga", len(llamadas) == n and r["hechos"] == 0)
    os.environ.pop("BTP_CORREO_BORRADORES")

    # leer_cuerpo: extracción de texto
    m = EmailMessage()
    m["From"], m["Subject"] = "a@b.c", "x"
    m.set_content("Texto plano​ aquí")
    m.add_alternative("<p>HTML</p>", subtype="html")
    m.add_attachment(b"%PDF-secreto", maintype="application", subtype="pdf", filename="informe.pdf")
    t = ci._texto_de(m.as_bytes())
    check("saca el texto plano, sin unicode oculto ni adjuntos",
          t == "Texto plano aquí" and "secreto" not in t)
    h = EmailMessage()
    h.set_content("<html><style>p{}</style><p>Hola <b>{{TITULAR}}</b></p><script>x()</script></html>", subtype="html")
    t = ci._texto_de(h.as_bytes())
    check("sin text/plain cae al HTML sin etiquetas ni scripts",
          "Hola" in t and "{{TITULAR}}" in t and "<" not in t and "x()" not in t)

    class Mb:
        cerrado = False

        def buscar_raw(self, q):
            return [5] if q == "rfc822msgid:abc@x" else []

        def fetch_cuerpo(self, uid):
            return m.as_bytes()

        def close(self):
            self.cerrado = True
    mb = Mb()
    check("leer_cuerpo localiza por Message-ID y cierra",
          ci.leer_cuerpo("<abc@x>", YO, abrir=lambda u, s: mb) == "Texto plano aquí" and mb.cerrado)
    check("leer_cuerpo sin localizar devuelve vacío", ci.leer_cuerpo("<otro@x>", YO, abrir=lambda u, s: Mb()) == "")
    src = open(os.path.join(ROOT, "tools", "correo_imap.py"), encoding="utf-8").read()
    check("el cuerpo se baja con PEEK (no marca como leído)", "BODY.PEEK[]" in src and "(BODY[]" not in src)

    # el borrador real: HTML escapado + In-Reply-To (sin tocar Gmail)
    captado = {}
    orig_redactar, orig_dejar = cr.redactar_borrador, correo_outbox.dejar_borrador
    cr.redactar_borrador = lambda **kw: {"ok": True, "texto": "Hola.\n\nUso <b>esto</b> & más.",
                                         "sospechoso": False, "brain": "fake", "motivo": "ok"}
    correo_outbox.dejar_borrador = lambda cuenta, service, msg: captado.setdefault("msg", msg) and "Borradores"
    try:
        r = cr.dejar_borrador_respuesta(account=YO, remitente_nombre="Dra.", remitente_email="doctora@{{CENTRO}}.net",
                                        asunto="Cita", cuerpo_hilo="x", intencion="y", in_reply_to="abc@x")
    finally:
        cr.redactar_borrador, correo_outbox.dejar_borrador = orig_redactar, orig_dejar
    msg = captado.get("msg")
    html = msg.get_body(preferencelist=("html",)).get_content() if msg else ""
    check("el borrador lleva HTML con lo del modelo escapado",
          r.get("ok") and "<p>Hola.</p>" in html and "&lt;b&gt;" in html and "<b>" not in html)
    check("y cuelga del hilo original", msg and msg["In-Reply-To"] == "<abc@x>" and msg["To"] == "doctora@{{CENTRO}}.net")

    print("\nRESULTADO correo_borradores: %d OK, %d fallos" % (_pass, _fail))
    print("✅ BORRADORES AUTOMÁTICOS EN VERDE" if not _fail else "❌ HAY FALLOS")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
