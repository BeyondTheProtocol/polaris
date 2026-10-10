#!/usr/bin/env python3
"""test_entrega_real_avisos.py — los llamantes de `salida.report_to_titular`/`alerta_critica` LEEN lo que `salida`
hizo de verdad antes de marcar nada como avisado (10-oct-26, deuda `entrega-dada-por-hecha-sin-mirar-resultado-de-salida`).

`report_to_titular` no lanza cuando no entrega: devuelve un dict. Aplazado al parte (cupo diario TOPE_AVISOS_DIA) y
retenido (silencio nocturno) LLEGAN solos; bloqueado (HALT, sin chat_id, envío fallido) NO. Cada caso de abajo
provoca el segundo y comprueba que la marca de «avisado» (o el cooldown, o el sello, o el mensaje «enviado»)
no se pone, y que con aplazado SÍ. Salida FALSA: nada sale de verdad. Todo en directorios temporales.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="entrega_real_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.environ["BTP_CLINICO_DIR"] = os.path.join(_TMP, "clinico")
os.environ["BTP_HISTORIAL"] = os.path.join(_TMP, "historial")
for _d in ("state", "clinico", "historial"):
    os.makedirs(os.path.join(_TMP, _d), exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import errores  # noqa: E402,F401  (ANTES de nada: reservas mete ~/claudecode/tools al frente del sys.path)
import salida  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


RES = {
    "entregado": {"delivered": True, "blocked": False, "reason": "entregado (fake)"},
    "aplazado": {"delivered": False, "blocked": False, "reason": "aplazado al parte diario", "aplazado": True},
    "bloqueado": {"delivered": False, "blocked": True, "reason": "HALT activo: salida en pausa total"},
    "ilegible": None,
}
_modo = ["entregado"]
_enviados = []


def _fake(texto, *a, **k):
    _enviados.append(texto)
    return RES[_modo[0]]


salida.report_to_titular = _fake
salida.alerta_critica = lambda texto, *a, **k: (_enviados.append(texto), RES[_modo[0]])[1]
salida.halted = lambda: False


def con_modo(modo):
    _modo[0] = modo
    del _enviados[:]


def callado(fn, *a, **k):
    """Ejecuta fn silenciando stdout/stderr; devuelve (resultado, stdout, stderr)."""
    so, se = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        r = fn(*a, **k)
    return r, so.getvalue(), se.getvalue()


# ── reservas.avisar ─────────────────────────────────────────────────────────────────────────
def reservas_tests():
    import reservas
    def sembrar():
        json.dump({"encargos": [{"id": "e1", "estado": "a_un_clic", "titulo": "Tren a Zúrich", "importe_eur": 40.0,
                                 "riesgo": "verde", "razones": [], "url": "", "avisado": False}]},
                  open(reservas.RESERVAS, "w"))
    def avisado():
        return json.load(open(reservas.RESERVAS))["encargos"][0]["avisado"]
    for modo in ("bloqueado", "ilegible"):
        sembrar(); con_modo(modo)
        (_t, n), _o, _e = callado(reservas.avisar)
        ok(n == 0 and avisado() is False, "reservas/%s: NO se marca avisado (se reintenta)" % modo)
    sembrar(); con_modo("aplazado")
    (_t, n), _o, _e = callado(reservas.avisar)
    ok(n == 1 and avisado() is True, "reservas/aplazado: llega con el parte → SÍ se marca avisado")
    sembrar(); con_modo("entregado")
    (_t, n), _o, _e = callado(reservas.avisar)
    ok(n == 1 and avisado() is True, "reservas/entregado: se marca avisado")
    sembrar()
    salida.report_to_titular = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("canal caído"))
    (_t, n), _o, _e = callado(reservas.avisar)
    ok(n == 0 and avisado() is False, "reservas/excepción: tampoco se marca (antes se marcaba SIEMPRE)")
    salida.report_to_titular = _fake


# ── cascada_clinica._anotar_aviso ────────────────────────────────────────────────────────────
def cascada_tests():
    import cascada_clinica as cc
    for modo, llega in (("entregado", True), ("aplazado", True), ("bloqueado", False), ("ilegible", False)):
        inf = {"pasos": []}
        r = cc._anotar_aviso(inf, RES[modo], True)
        ok(r is llega, "cascada/%s: llega=%s" % (modo, llega))
        ok(("revisar_humano" in inf) is (not llega), "cascada/%s: revisar_humano solo si NO llegó" % modo)
        ok(any("NO LLEGÓ" in p for p in inf["pasos"]) is (not llega) and not any("aviso enviado" in p for p in inf["pasos"]),
           "cascada/%s: el paso dice la verdad (nunca «aviso enviado» sin serlo)" % modo)


# ── correo_triage.enviar_digest_intradia ─────────────────────────────────────────────────────
def digest_tests():
    import correo_triage as ct
    guardado = []
    ct._digest_calcula = lambda cuentas, n: ("📬 Correo nuevo (no urgente):\n   • Marina: «hola»", {"id1"}, set())
    ct._guardar_digest_visto = lambda ids: guardado.append(set(ids))
    for modo in ("bloqueado", "ilegible"):
        del guardado[:]; con_modo(modo)
        enviado, _t = callado(ct.enviar_digest_intradia)[0]
        ok(enviado is False and guardado == [], "digest/%s: no se marcan los correos como vistos ni se dice enviado" % modo)
    for modo in ("aplazado", "entregado"):
        del guardado[:]; con_modo(modo)
        enviado, _t = callado(ct.enviar_digest_intradia)[0]
        ok(enviado is True and guardado == [{"id1"}], "digest/%s: llega → se marcan vistos" % modo)
    del guardado[:]; con_modo("entregado")
    enviado, _t = callado(ct.enviar_digest_intradia, dry=True)[0]
    ok(enviado is False and guardado == [] and _enviados == [], "digest/dry: ni envía ni marca")


# ── estado_caso.actualizar ───────────────────────────────────────────────────────────────────
def estado_caso_tests():
    import estado_caso as ec
    ec._marca_path = lambda: os.path.join(_TMP, "marca_ec.json")
    ec._estado_path = lambda: os.path.join(_TMP, "estado_ec.md")
    ec.render = lambda eventos, hilos: "x"
    ec.seguimiento.load_seguimiento = lambda: {"hilos": []}
    ec._esperas = lambda hilos: []
    ev = [{"ts": "2026-10-10T10:00:00", "fichero": "informe.pdf"}]
    ec._leer_eventos = lambda: ev
    ec.casar = lambda e, esperas: [{"id": "h1", "titulo": "espera X"}]
    ec._promesas = lambda hoy: [({"id": "p1", "vence": "2026-10-01", "texto": "t"}, 5)]
    import promesas_caso
    promesas_caso.texto_sin_pii = lambda p: "promesa sin PII"
    def marca():
        try:
            return json.load(open(ec._marca_path()))
        except Exception:
            return {}
    for modo in ("bloqueado", "ilegible"):
        if os.path.exists(ec._marca_path()):
            os.remove(ec._marca_path())
        con_modo(modo)
        callado(ec.actualizar, avisar=True)
        m = marca()
        ok(not m.get("promesas_avisadas") and not m.get("ultimo_ts"),
           "estado_caso/%s: ni promesa «avisada» ni `ultimo_ts` avanzado (se reintenta)" % modo)
    os.remove(ec._marca_path()) if os.path.exists(ec._marca_path()) else None
    con_modo("aplazado")
    callado(ec.actualizar, avisar=True)
    m = marca()
    ok(m.get("promesas_avisadas") == ["p1"] and m.get("ultimo_ts"), "estado_caso/aplazado: llega → se marca todo")


# ── x_dms._do_watch: no marcar visto hasta que el aviso llegue ───────────────────────────────
def x_dms_watch_tests():
    import x_dms
    class _DT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 10, 12, 0, 0)
    x_dms.datetime = types.SimpleNamespace(datetime=_DT, date=datetime.date)
    x_dms.OUT_DIR = os.path.join(_TMP, "x_dms")
    nuevo = [{"id": "m2", "handle": "doctora", "mine": False, "text": "hola", "ts": 1760000000000}]
    base = [{"id": "m1", "handle": "doctora", "mine": False, "text": "viejo", "ts": 1750000000000}]
    wf = os.path.join(x_dms.OUT_DIR, "watch-doctora.json")
    def ids():
        return set(json.load(open(wf))["ids"])
    for modo in ("bloqueado", "ilegible"):
        os.makedirs(x_dms.OUT_DIR, exist_ok=True)
        x_dms._fetch = lambda n: base
        con_modo("entregado")
        callado(x_dms._do_watch, "doctora", False, 10)       # línea base (first)
        x_dms._fetch = lambda n: base + nuevo
        con_modo(modo)
        callado(x_dms._do_watch, "doctora", True, 10)
        ok(ids() == {"m1"}, "x_dms watch/%s: el mensaje NUEVO no se marca visto (se reintenta)" % modo)
        con_modo("entregado")
        callado(x_dms._do_watch, "doctora", True, 10)
        ok(ids() == {"m1", "m2"}, "x_dms watch/%s: al volver la salida, sí avisa y marca" % modo)
        os.remove(wf)
    x_dms._fetch = lambda n: base
    con_modo("entregado")
    callado(x_dms._do_watch, "doctora", True, 10)
    x_dms._fetch = lambda n: base + nuevo
    con_modo("aplazado")
    callado(x_dms._do_watch, "doctora", True, 10)
    ok(ids() == {"m1", "m2"}, "x_dms watch/aplazado: llega con el parte → se marca visto")


# ── rituales.main ────────────────────────────────────────────────────────────────────────────
def rituales_tests():
    import rituales
    rituales.org_wrap = lambda *a, **k: "ritual de prueba"
    for modo in ("bloqueado", "ilegible"):
        con_modo(modo)
        rc, out, err = callado(rituales.main, ["org-wrap"])
        ok(rc == 1 and "[enviado]" not in out, "rituales/%s: rc 1 y NO imprime «[enviado]»" % modo)
    con_modo("entregado")
    rc, out, _e = callado(rituales.main, ["org-wrap"])
    ok(rc == 0 and "[enviado]" in out, "rituales/entregado: rc 0 y «[enviado]»")
    con_modo("aplazado")
    rc, out, _e = callado(rituales.main, ["org-wrap"])
    ok(rc == 0 and "[enviado]" not in out and "aplazado" in out, "rituales/aplazado: rc 0 pero dice «en camino», no «enviado»")


# ── centinela_ned.run: no sellar visto si el aviso no llegó ──────────────────────────────────
def centinela_tests():
    import centinela_ned as cn
    sellos = []
    cn.detectar = lambda: ([("correo", "📩 te ha escrito {{CONTACTO}}")], {"mark": 1})
    cn._guardar_mark = lambda d: sellos.append(d)
    cn._heartbeat = lambda *a, **k: None
    for modo in ("bloqueado", "ilegible"):
        del sellos[:]; con_modo(modo)
        callado(cn.run)
        ok(sellos == [], "centinela/%s: NO se sella visto (se reintenta, no se pierde la señal NED)" % modo)
    for modo in ("aplazado", "entregado"):
        del sellos[:]; con_modo(modo)
        callado(cn.run)
        ok(sellos == [{"mark": 1}], "centinela/%s: llega → se sella" % modo)


# ── borde_gateway._avisar_puerta_caida: el cooldown solo arranca si llegó ────────────────────
def borde_tests():
    import borde_gateway as bg
    bg.GW_DIR = os.path.join(_TMP, "gw")
    st = os.path.join(bg.GW_DIR, "aviso_puerta.json")
    for modo in ("bloqueado", "ilegible"):
        con_modo(modo)
        callado(bg._avisar_puerta_caida, "sin cerebro")
        ok(not os.path.exists(st), "borde_gateway/%s: NO arranca el cooldown (el próximo 503 reintenta)" % modo)
    con_modo("aplazado")
    callado(bg._avisar_puerta_caida, "sin cerebro")
    ok(os.path.exists(st), "borde_gateway/aplazado: llega → arranca el cooldown")


# ── seguimiento._avisar_tarea_nueva ──────────────────────────────────────────────────────────
def seguimiento_tests():
    import seguimiento as sg
    sellados = []
    sg._es_urgente_nueva = lambda hilo, cumbre: True
    sg.load_cumbre = lambda: {}
    sg._sellar_ultimo_aviso = lambda hid, ahora: sellados.append(hid)
    for modo in ("bloqueado", "ilegible"):
        del sellados[:]; con_modo(modo)
        callado(sg._avisar_tarea_nueva, {"id": "t1", "titulo": "Llamar al hospital"})
        ok(sellados == [], "seguimiento/%s: NO se sella `ultimo_aviso` (se reintenta)" % modo)
    for modo in ("aplazado", "entregado"):
        del sellados[:]; con_modo(modo)
        callado(sg._avisar_tarea_nueva, {"id": "t1", "titulo": "Llamar al hospital"})
        ok(sellados == ["t1"], "seguimiento/%s: llega → se sella" % modo)


# ── vigia._avisar_a_titular ───────────────────────────────────────────────────────────────────
def vigia_tests():
    import vigia
    vigia.ALERTA_STATE = os.path.join(_TMP, "vigia_alerta.json")
    anom = [{"tipo": "bot_inestable", "key": "crash:X", "clase": "necesita_codigo"}]
    for modo in ("bloqueado", "ilegible"):
        con_modo(modo)
        r = callado(vigia._avisar_a_titular, anom)[0]
        ok(r is False and not os.path.exists(vigia.ALERTA_STATE), "vigia/%s: no se da por avisado (sin estado anti-spam)" % modo)
    con_modo("aplazado")
    r = callado(vigia._avisar_a_titular, anom)[0]
    ok(r is True and os.path.exists(vigia.ALERTA_STATE), "vigia/aplazado: llega → se guarda el estado anti-spam")


# ── healthcheck._escalar_boca_muda: el sello diario solo si el grito salió ───────────────────
def boca_muda_tests():
    import healthcheck as hc
    sello = os.path.join(_TMP, "hc", "boca_muda.flag")
    alertas = [("boca_muda", "la boca está muda")]
    for modo in ("bloqueado", "ilegible"):
        chk = {}; con_modo(modo)
        r = callado(hc._escalar_boca_muda, alertas, sello, chk)[0]
        ok(r is False and not os.path.exists(sello) and "boca_muda_escalada_error" in chk,
           "boca_muda/%s: SIN sello (se vuelve a gritar) y el fallo queda en el chequeo" % modo)
    chk = {}; con_modo("entregado")
    r = callado(hc._escalar_boca_muda, alertas, sello, chk)[0]
    ok(r is True and os.path.exists(sello), "boca_muda/entregado: grita y sella")
    # dos gritos, sale uno y el otro no: se sella (el que salió ya avisó; repetirlo cada ciclo saltaría HALT y silencio)
    os.remove(sello)
    resultados = iter([RES["entregado"], RES["bloqueado"]])
    salida.alerta_critica = lambda texto, *a, **k: next(resultados)
    chk = {}
    r = callado(hc._escalar_boca_muda, [("a", "x"), ("b", "y")], sello, chk)[0]
    ok(r is True and os.path.exists(sello), "boca_muda/uno sale y otro no: se sella (no se repite el grito cada ciclo)")
    salida.alerta_critica = lambda texto, *a, **k: (_enviados.append(texto), RES[_modo[0]])[1]


# ── laminillas_ventanilla._aviso_memoria: el sello anti-spam de 6 h solo si el aviso llegó ───
def laminillas_tests():
    import laminillas_ventanilla as lv
    lv.AVISO_SELLO = os.path.join(_TMP, "lam", ".aviso-memoria")
    os.makedirs(os.path.dirname(lv.AVISO_SELLO), exist_ok=True)
    for modo in ("bloqueado", "ilegible"):
        if os.path.exists(lv.AVISO_SELLO):
            os.remove(lv.AVISO_SELLO)
        con_modo(modo)
        callado(lv._aviso_memoria, "piloto", "poca memoria", [])
        ok(not os.path.exists(lv.AVISO_SELLO), "laminillas/%s: NO arranca el anti-spam de 6 h (se reintenta)" % modo)
    con_modo("aplazado")
    callado(lv._aviso_memoria, "piloto", "poca memoria", [])
    ok(os.path.exists(lv.AVISO_SELLO), "laminillas/aplazado: llega → arranca el anti-spam")


# ── sync_playbook.nudge_frescura: «ya avisé hoy» solo si el aviso llegó ──────────────────────
def sync_playbook_tests():
    import sync_playbook as sp
    d = os.path.join(_TMP, "sp"); os.makedirs(d, exist_ok=True)
    sp.SYNC_DIR = d
    sp.CONTENT_SEEN = os.path.join(d, "content_seen.json")
    sp.LAST_NUDGE = os.path.join(d, "last_nudge")
    sp._local_info = lambda path: (True, "abc123", None)
    sp._audit = lambda *a, **k: None
    def preparar():
        json.dump({"sha": "abc123", "first_seen": "2020-01-01 00:00:00"}, open(sp.CONTENT_SEEN, "w"))
        if os.path.exists(sp.LAST_NUDGE):
            os.remove(sp.LAST_NUDGE)
    for modo in ("bloqueado", "ilegible"):
        preparar(); con_modo(modo)
        callado(sp.nudge_frescura, 14, False, local_path="x")
        ok(not os.path.exists(sp.LAST_NUDGE), "sync_playbook/%s: NO se sella «ya avisé hoy» (se reintenta)" % modo)
    preparar(); con_modo("aplazado")
    callado(sp.nudge_frescura, 14, False, local_path="x")
    ok(os.path.exists(sp.LAST_NUDGE), "sync_playbook/aplazado: llega → se sella")


# ── reconciliar_estado._avisar: el cooldown que sella _debe_avisar se deshace si no llegó ────
def reconciliar_tests():
    import reconciliar_estado as re_
    d = os.path.join(_TMP, "recon"); os.makedirs(d, exist_ok=True)
    re_.RECON_DIR = d
    re_.SEEN = os.path.join(d, "avisos_vistos.json")
    rep = {"cerrados": ["a"], "propuestas": [], "inconsistentes": ["x"], "huerfanos": []}
    firma = re_._firma(rep)
    def sellado():
        try:
            return firma in json.load(open(re_.SEEN))
        except Exception:
            return False
    for modo in ("bloqueado", "ilegible"):
        if os.path.exists(re_.SEEN):
            os.remove(re_.SEEN)
        con_modo(modo)
        callado(re_._avisar, rep)
        ok(not sellado(), "reconciliar_estado/%s: el cooldown NO queda sellado (se reintenta)" % modo)
    if os.path.exists(re_.SEEN):
        os.remove(re_.SEEN)
    con_modo("aplazado")
    callado(re_._avisar, rep)
    ok(sellado(), "reconciliar_estado/aplazado: llega → el cooldown queda sellado")


def main():
    global _fail
    for t in (cascada_tests, digest_tests, estado_caso_tests, x_dms_watch_tests, rituales_tests, centinela_tests,
              borde_tests, seguimiento_tests, vigia_tests, boca_muda_tests, laminillas_tests, sync_playbook_tests, reconciliar_tests,
              reservas_tests):      # reservas el último: reservas.py antepone ~/claudecode/tools (REPO) al sys.path
        try:
            t()
        except Exception as exc:    # un bloque roto no tapa a los demás
            _fail += 1
            print("  ✗ %s lanzó %r" % (t.__name__, exc))
    print("RESULTADO entrega_real_avisos: %d OK, %d fallos" % (_pass, _fail))
    print("✅ ENTREGA_REAL_AVISOS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
