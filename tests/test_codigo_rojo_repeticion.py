#!/usr/bin/env python3
"""test_codigo_rojo_repeticion.py — el canal de socorro grita una vez, y por lo que es.

POR QUÉ EXISTE (12-sep-2026). De las 17 activaciones registradas en `Gestion/CODIGO-ROJO.md`,
**15 eran el mismo motivo**: el barrido de seguridad en rojo, 10 de ellas en 48 horas (26-27 jul).
Solo 2 fueron eventos únicos, y uno de esos dos era el que de verdad importaba: Fred Hutch
declarando a {{TITULAR}} no elegible para la vacuna personalizada.

El canal de `alerta_critica` atraviesa el HALT y el silencio nocturno a propósito, y el propio
código advierte de no desgastarlo. Diez avisos de socorro por una condición ya avisada hacen que
el siguiente, el de verdad, compita con el ruido.

Lo delicado es que callar un aviso de socorro es peligroso, así que este test protege sobre todo
lo que NO puede pasar: que un motivo nuevo se quede callado, o que parar y explicar dependan del
aviso. Aislado en tmp: no toca el HALT real ni escribe a Telegram.
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="cr_rep_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_TEST_BATTERY"] = "1"
import codigo_rojo as cr  # noqa: E402

# Aislar TODO lo que toca el sistema vivo.
cr.HALT_FILES = (os.path.join(_TMP, ".btp.HALT"), os.path.join(_TMP, ".HALT"))
cr.ROJO_MD = os.path.join(_TMP, "CODIGO-ROJO.md")
cr.HUELLAS = os.path.join(_TMP, "huellas.json")
cr.BTP_RUN = os.path.join(_TMP, "no-existe.sh")
cr._stop_launchd = lambda: None

_alertas = []
cr.salida.alerta_critica = lambda t: (_alertas.append(t) or {"delivered": True, "reason": "stub"})

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def limpiar():
    del _alertas[:]
    for h in cr.HALT_FILES:
        if os.path.exists(h):
            os.remove(h)
    if os.path.exists(cr.HUELLAS):
        os.remove(cr.HUELLAS)


def main():
    MISMO = "Barrido de seguridad de la constelación en ROJO (BRECHA)"

    # --- 1. La primera vez avisa ---
    limpiar()
    cr.trigger(MISMO, "detalle")
    ok(len(_alertas) == 1, "la primera activación avisa")
    ok(all(os.path.exists(h) for h in cr.HALT_FILES), "y para las máquinas")

    # --- 2. La MISMA condición, con el HALT puesto, NO vuelve a avisar ---
    for _ in range(9):
        cr.trigger(MISMO, "detalle")
    ok(len(_alertas) == 1, "nueve repeticiones más y sigue habiendo UN aviso (había 10)")

    # --- 3. …pero parar y explicar se hacen SIEMPRE, aunque el aviso se calle ---
    ok(all(os.path.exists(h) for h in cr.HALT_FILES), "el HALT sigue puesto en cada repetición")
    informe = open(cr.ROJO_MD, encoding="utf-8").read()
    ok(informe.count("# 🔴 CÓDIGO ROJO") == 10,
       "las 10 quedan escritas en el informe: se calla el aviso, no el registro")

    # --- 4. LO QUE NO PUEDE PASAR: un motivo NUEVO se queda callado ---
    # Este es el caso que importa de verdad. El 26-jun el motivo nuevo era «Fred Hutch declara a
    # {{TITULAR}} NO ELEGIBLE para la vacuna». Si un mecanismo anti-ruido tapara eso, sobra el
    # mecanismo entero.
    cr.trigger("Fred Hutch declara a {{TITULAR}} NO ELEGIBLE para la vacuna personalizada", "")
    ok(len(_alertas) == 2, "un motivo NUEVO avisa aunque el HALT ya esté puesto por otro")
    ok("NO ELEGIBLE" in _alertas[-1], "y el aviso que llega es el del motivo nuevo")

    # --- 5. Los números dentro del motivo no crean motivos falsos ---
    limpiar()
    cr.trigger("seguridad_sweep detectó 2 check(s) en rojo", "")
    cr.trigger("seguridad_sweep detectó 3 check(s) en rojo", "")
    ok(len(_alertas) == 1, "«2 checks» y «3 checks» son la misma condición, no dos")

    # --- 6. clear() (acto humano) borra las huellas: después, todo vuelve a avisar ---
    limpiar()
    cr.trigger(MISMO, "")
    cr.trigger(MISMO, "")
    ok(len(_alertas) == 1, "repetida, un aviso")
    os.environ["BTP_PRESENCE_OK"] = "1"
    ok(cr.clear("probando") is True, "clear funciona con presencia humana")
    ok(not os.path.exists(cr.HUELLAS), "clear borra las huellas")
    cr.trigger(MISMO, "")
    ok(len(_alertas) == 2, "tras levantarlo, la misma condición vuelve a avisar")
    del os.environ["BTP_PRESENCE_OK"]

    # --- 7. Si la condición vuelve sin HALT puesto, es un evento nuevo: avisa ---
    limpiar()
    cr.trigger(MISMO, "")
    for h in cr.HALT_FILES:            # alguien lo levantó a mano, sin pasar por clear()
        os.remove(h)
    cr.trigger(MISMO, "")
    ok(len(_alertas) == 2, "sin HALT puesto, la condición que vuelve cuenta como evento nuevo")

    # --- 8. A las 24 h, un recordatorio. Uno. ---
    limpiar()
    cr.trigger(MISMO, "")
    d = cr._huellas_cargar()
    for k in d:
        d[k]["ultimo_aviso"] = time.time() - (cr.RECORDATORIO_H + 1) * 3600
    cr._huellas_guardar(d)
    cr.trigger(MISMO, "")
    ok(len(_alertas) == 2, "pasadas 24 h con la condición viva, llega UN recordatorio")
    cr.trigger(MISMO, "")
    ok(len(_alertas) == 2, "y solo uno: el reloj se reinicia con el recordatorio")

    print("RESULTADO codigo_rojo_repeticion: %d OK, %d fallos" % (_pass, _fail))
    print("✅ ANTI-REPETICIÓN DEL CÓDIGO ROJO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
