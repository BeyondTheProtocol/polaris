#!/usr/bin/env python3
"""test_rutinas_latido.py — que una rutina parada se note a tiempo (25-sep-2026).

Tres fallos de la misma clase, «el vigía mira un número que no es el real»:
  1. `healthcheck._roster_meta` daba cadencia SEMANAL a toda rutina con `Weekday`: la auto-mejora
     (lun/mié/vie/dom, hueco real 2 días) avisaba de inactividad a los 21 días.
  2. `RUTINAS_NED` no declaraba periodo: una rutina que dejaba de arrancar con su último latido en
     «ok» no avisaba nunca. Aquí se comprueba que el periodo declarado cuadra con su plist.
  3. `radar_ned_diario._ya_hay_job_del_comite` buscaba en `running/`, que la cola no tiene.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
"""
import json
import os
import plistlib
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="rutinas_latido_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import healthcheck as hc        # noqa: E402
import radar_ned_diario as rnd  # noqa: E402
import cola                     # noqa: E402
from _entorno import exige_cola_aislada  # noqa: E402

_pass = _fail = 0


def ok(cond, msg):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ✅", msg)
    else:
        _fail += 1
        print("  ❌", msg)


H = 3600
D = 86400


def cadencia_tests():
    cad = hc._cadencia_calendario
    auto_mejora = [{"Hour": 5, "Minute": 8, "Weekday": w} for w in (1, 3, 5, 0)]
    ok(cad(auto_mejora) == 2 * D, "auto-mejora lun/mié/vie/dom → 2 días, no 7")
    ok(cad({"Hour": 5, "Minute": 40}) == D, "una vez al día → 1 día")
    ok(cad([{"Hour": 8, "Minute": 20}, {"Hour": 14, "Minute": 0}]) == 18 * H + 20 * 60,
       "dos veces al día (8:20 y 14:00) → el hueco largo, 18 h 20 min")
    ok(cad({"Hour": 5, "Minute": 0, "Weekday": 1}) == 7 * D, "una vez a la semana → 7 días")
    ok(cad({"Hour": 5, "Minute": 0, "Weekday": 7}) == 7 * D, "Weekday 7 es domingo, igual que 0")
    ok(cad({"Minute": 15}) == H, "sin Hour → cada hora")
    ok(cad({"Day": 1, "Hour": 9, "Minute": 0}) == 30 * D, "mensual → 30 días (conservador)")
    ok(cad([]) is None and cad("basura") is None, "ilegible → None (no se alerta)")
    ok(cad({"Hour": "x"}) is None, "valor no numérico → None")


def roster_meta_tests():
    """Que `_roster_meta` USE la cadencia real: los tests de healthcheck lo mockean, así que
    revertir solo esa llamada dejaba todo en verde (cazado por verificación, 25-sep)."""
    real = hc._dir_plists
    hc._dir_plists = lambda: os.path.join(ROOT, "tools", "launchd")
    try:
        meta = hc._roster_meta()
    finally:
        hc._dir_plists = real
    ok(meta.get("com.btp.auto-mejora", (None,))[0] == 2 * D,
       "_roster_meta da 2 días a la auto-mejora con su plist real (antes 7 → aviso a los 21)")


def periodo_declarado_tests():
    """El periodo que declara RUTINAS_NED no puede ser más corto que el hueco real de su plist
    (avisaría de más) ni más de 2× (avisaría tarde)."""
    for d in hc.RUTINAS_NED:
        p = os.path.join(ROOT, "tools", "launchd", d["plist"] + ".plist")
        ok(os.path.exists(p), "%s: su plist %s existe" % (d["agente"], d["plist"]))
        if not os.path.exists(p):
            continue
        with open(p, "rb") as fh:
            pl = plistlib.load(fh)
        ok(pl.get("EnvironmentVariables", {}).get("BTP_AGENT") == d["agente"],
           "%s: el plist corre ese mismo agente" % d["agente"])
        real_h = hc._cadencia_calendario(pl.get("StartCalendarInterval")) / H
        if d["plist"] == "com.btp.radar-lit":
            real_h = 31 * 24          # mensual: el mes más largo, no los 30 conservadores
            # (el latido del agente lo escribe también radar_ned_dia.sh a diario: esto es cota)
        ok(real_h <= d["periodo_h"] <= 2 * real_h,
           "%s: periodo declarado %d h cuadra con su plist (%d h)" % (d["agente"], d["periodo_h"], real_h))


def cola_comite_tests():
    exige_cola_aislada()       # escribe fixtures en la cola: nunca en la viva
    for sub in cola.SUBDIRS:
        os.makedirs(os.path.join(cola.QUEUE, sub), exist_ok=True)
    ok(cola.QUEUE.startswith(_TMP), "la cola del test es la temporal, no la viva")
    ok(rnd._ya_hay_job_del_comite() is False, "cola vacía → no hay comité")
    p = os.path.join(cola.QUEUE, "processing", "1-x.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"agente": "comite-medico"}, fh)
    ok(rnd._ya_hay_job_del_comite() is True, "comité en processing/ → se ve (antes se miraba running/)")
    os.remove(p)
    p = os.path.join(cola.QUEUE, "pending", "1-y.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"agente": "comite-medico"}, fh)
    ok(rnd._ya_hay_job_del_comite() is True, "comité en pending/ → se ve")
    os.remove(p)


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def latido_externo_tests():
    """F2: el ping solo lleva la base fija + un UUID validado (+ «/fail»). Ni cuerpo, ni cabeceras
    con datos, ni nombres. Sin UUID en el Llavero no sale nada."""
    import salida
    uuid = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
    pedidas = []

    def abrir(req, timeout=None):
        pedidas.append(req)
        return _Resp()

    real_uuid, real_halt, real_sleep = hc._uuid_latido, salida.halted, hc.time.sleep
    salida.halted = lambda: False
    try:
        hc._uuid_latido = lambda: None
        ok(hc._latido_externo(abrir=abrir) == "sin-configurar" and not pedidas,
           "sin UUID en el Llavero → no sale nada")
        hc._uuid_latido = lambda: uuid
        ok(hc._latido_externo(abrir=abrir) == "ok", "con UUID → ping ok")
        req = pedidas[-1]
        ok(req.full_url == "https://hc-ping.com/" + uuid, "la URL es base fija + UUID, nada más")
        ok(req.data is None and req.get_method() == "GET", "sin cuerpo: GET vacío")
        ok(dict(req.header_items()) == {"User-agent": "btp"},
           "sin cabeceras con datos (solo un User-Agent neutro)")
        ok(hc._latido_externo(fallo=True, abrir=abrir) == "ok"
           and pedidas[-1].full_url == "https://hc-ping.com/" + uuid + "/fail",
           "el vigía roto manda /fail, sin más")
        for malo in ("../evil", uuid + "?x=1", "https://otro.com/" + uuid, "nombre"):
            hc._uuid_latido = lambda m=malo: m if hc._UUID_RE.match(m) else None
            ok(hc._latido_url() is None, "lo que no es un UUID no forma URL (%s)" % malo[:20])
        hc._uuid_latido = lambda: hc.LLAVERO_INACCESIBLE
        n = len(pedidas)
        ok(hc._latido_externo(abrir=abrir) == "llavero-inaccesible" and len(pedidas) == n,
           "Llavero que no se deja leer → se dice, no se confunde con «sin configurar»")
        ok(hc._SinRedirecciones().redirect_request(None, None, 302, "", {}, "https://x") is None,
           "un 3xx no se sigue a otro host")
        hc._uuid_latido = lambda: uuid
        salida.halted = lambda: True
        n = len(pedidas)
        ok(hc._latido_externo(abrir=abrir) == "halt" and len(pedidas) == n, "con .HALT no sale")
        salida.halted = lambda: False

        def cae(req, timeout=None):
            raise OSError("sin red")
        hc.time.sleep = lambda *_: None
        ok(hc._latido_externo(abrir=cae) == "error", "sin red → 'error', no lanza")
    finally:
        hc._uuid_latido, salida.halted, hc.time.sleep = real_uuid, real_halt, real_sleep


def recuperar_arranque_tests():
    """F1: tras arrancar, una rutina NED «atrasada» y cargada se relanza UNA vez, sin -k."""
    import datetime as _dt
    import salida
    hb_dir = os.path.join(_TMP, "hb_arranque")
    os.makedirs(hb_dir, exist_ok=True)
    os.makedirs(hc.HC, exist_ok=True)
    reales = (hc.HB_DIR, hc._arranque_ts, hc._launchctl_estado, hc._kickstart_sin_matar,
              hc._marcar_acuse_autofix, salida.halted, hc._hay_dns, hc._sci_de)
    lanzadas = []

    def hb(agente, est, dias):
        ts = (_dt.datetime.utcnow() - _dt.timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(hb_dir, agente + ".json"), "w") as fh:
            json.dump({"agente": agente, "ts": ts, "estado": est}, fh)

    barrido = [d["plist"] for d in hc.RUTINAS_NED if d["agente"] == "git"][0]
    try:
        hc.HB_DIR = hb_dir
        hc._launchctl_estado = lambda: {"com.btp.auto-mejora": ("-", "0"),
                                        barrido: ("123", "0"),
                                        "com.btp.radar-lit": ("-", "0")}
        hc._kickstart_sin_matar = lambda label: (lanzadas.append(label), True)[1]
        hc._marcar_acuse_autofix = lambda *a: None
        hc._hay_dns = lambda *a, **k: True
        hc._sci_de = lambda label: None          # sin horario conocido: no bloquea
        salida.halted = lambda: False
        hb("auto-mejora", "ok", 4)
        hb("git", "ok", 3)
        hb("comite-medico", "ok", 2)
        marca = os.path.join(hc.HC, "recuperado_arranque.json")
        if os.path.exists(marca):
            os.remove(marca)

        ahora = 2_000_000_000
        hc._arranque_ts = lambda: ahora - 3 * H
        ok(hc._recuperar_tras_arranque(ahora).get("fuera_de_ventana") and not lanzadas,
           "arrancó hace 3 h → fuera de ventana, no toca nada")
        hc._arranque_ts = lambda: ahora - 300
        hc._hay_dns = lambda *a, **k: False
        ok(hc._recuperar_tras_arranque(ahora).get("sin_red") and not lanzadas
           and not os.path.exists(marca), "sin red → no relanza ni deja marca (reintenta luego)")
        hc._hay_dns = lambda *a, **k: True
        hora = hc.datetime.fromtimestamp(ahora)
        hc._sci_de = lambda label: {"Hour": (hora.hour + 1) % 24, "Minute": hora.minute}
        info = hc._recuperar_tras_arranque(ahora)
        ok(not lanzadas and "le toca sola" in info.get("auto-mejora", ""),
           "le toca sola dentro de 1 h → no se duplica")
        os.remove(marca)
        hc._sci_de = lambda label: {"Hour": (hora.hour + 5) % 24, "Minute": hora.minute}
        hc._arranque_ts = lambda: ahora - 300
        info = hc._recuperar_tras_arranque(ahora)
        ok(lanzadas == ["com.btp.auto-mejora"],
           "relanza la atrasada; no la que ya corre ni la que está al día")
        ok("git" in info, "la que ya corre queda anotada, sin tocar")
        hc._recuperar_tras_arranque(ahora)
        ok(lanzadas == ["com.btp.auto-mejora"], "una sola vez por arranque")
        os.remove(marca)
        salida.halted = lambda: True
        ok(hc._recuperar_tras_arranque(ahora).get("halt") and lanzadas == ["com.btp.auto-mejora"],
           "con .HALT no relanza")
        salida.halted = lambda: False
        hc._launchctl_estado = lambda: {}
        lanzadas.clear()
        hc._recuperar_tras_arranque(ahora)
        ok(lanzadas == [], "descargada de launchd → no se enciende (eso es gate de {{TITULAR}})")
    finally:
        (hc.HB_DIR, hc._arranque_ts, hc._launchctl_estado, hc._kickstart_sin_matar,
         hc._marcar_acuse_autofix, salida.halted, hc._hay_dns, hc._sci_de) = reales


def plist_arranque_tests():
    """RunAtLoad se queda en false a propósito (al cargar, el vigía entero haría kickstart -k de
    daemons que launchd aún levanta). La recuperación va en la primera pasada normal, así que la
    ventana tiene que cubrir de sobra un StartInterval."""
    with open(os.path.join(ROOT, "tools", "launchd", "com.btp.healthcheck.plist"), "rb") as fh:
        pl = plistlib.load(fh)
    ok(pl.get("RunAtLoad") is False, "el vigía NO corre al cargar (RunAtLoad false)")
    ok(hc.RECUPERAR_VENTANA_SEG >= 2 * pl["StartInterval"],
       "la ventana de recuperación cubre al menos dos pasadas del vigía")


def proximo_disparo_tests():
    import datetime as _dt
    f = hc._minutos_hasta_disparo
    lunes_5 = _dt.datetime(2026, 9, 28, 5, 0)          # lunes
    am = [{"Hour": 5, "Minute": 8, "Weekday": w} for w in (1, 3, 5, 0)]
    ok(f(am, lunes_5) == 8, "auto-mejora el lunes a las 5:00 → le toca en 8 min")
    ok(f(am, _dt.datetime(2026, 9, 28, 5, 9)) == 2 * 1440 - 1, "pasada la de hoy → la del miércoles")
    ok(f({"Hour": 5, "Minute": 40}, _dt.datetime(2026, 9, 28, 6, 0)) == 1420, "diaria ya pasada → mañana")
    ok(f({"Day": 1, "Hour": 9, "Minute": 0}, _dt.datetime(2026, 10, 1, 8, 0)) == 60, "mensual hoy → 60 min")
    ok(f({"Day": 1, "Hour": 9, "Minute": 0}, _dt.datetime(2026, 10, 2, 8, 0)) is None, "mensual otro día → lejos")
    ok(f(None, lunes_5) is None, "sin horario → no se sabe")


def main():
    print("test_rutinas_latido")
    cadencia_tests()
    roster_meta_tests()
    periodo_declarado_tests()
    cola_comite_tests()
    latido_externo_tests()
    recuperar_arranque_tests()
    plist_arranque_tests()
    proximo_disparo_tests()
    print("RESULTADO: %d OK, %d fallos" % (_pass, _fail))
    print("✅ EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
