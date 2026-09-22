#!/usr/bin/env python3
"""test_healthcheck_deadman.py — dos hallazgos de la auditoría del 25-jul-26.

Nº5 — el dead-man del dispatcher exigía `pending>0`. Con la cola VACÍA y el dispatcher en ciclo
arranque-y-salir (los `exit 0` tempranos, con KeepAlive relanzando cada 10 s), launchd lo daba por
sano con status 0 y ninguna capa alertaba: un mensaje de {{TITULAR}} por Telegram entraba a una cola sin
ejecutor durante horas. El latido es la señal de VIDA del motor y no depende de que haya trabajo.
Dos exenciones legítimas: HALT activo (el kill-switch funcionando) y daemon sin cargar (lazo apagado
a conciencia, de lo que ya avisa el roster).

Nº4 — el acuse automático decía «lo estoy mirando» y detrás no miraba nadie (el propio docstring lo
admitía como TODO). Eso es peor que el grito seco: {{TITULAR}} lee que alguien se puso y deja de
vigilarlo. Ahora `_encolar_investigacion` encola de verdad, y cuando NO puede (HALT), la nota lo
dice en llano en vez de prometer.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
# Mutis obligatorio ANTES de importar salida (31-jul-26): este test hace
# `salida.halted = lambda: False`, o sea que quita la única protección que impediría la entrega,
# y la batería lo corre. Sin esto, un report_to_titular por debajo le llega a {{TITULAR}} de verdad —
# que es exactamente lo que pasó el 12-jul-26 con 14 mensajes. `salida.send` enmudece solo si
# BTP_TEST_BATTERY=1 Y el STATE está aislado: hacen falta las dos.
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="deadman_test_")
os.environ["BTP_TEST_BATTERY"] = "1"
import healthcheck as hc  # noqa: E402
import cola as q          # noqa: E402
import salida             # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _entorno import exige_cola_aislada as _exige_cola_aislada  # noqa: E402
_exige_cola_aislada()              # nada de fixtures en la cola de producción (20-sep-26)

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # ── Nº5: la exención del dead-man ──────────────────────────────────────────────
    orig_halted, orig_estado = salida.halted, hc._launchctl_estado

    salida.halted = lambda: True
    check("HALT activo → exento (el kill-switch no es una avería)",
          hc._dispatcher_apagado_a_proposito() is True)

    salida.halted = lambda: False
    hc._launchctl_estado = lambda: {"com.btp.otro": ("-", "0")}
    check("daemon sin cargar → exento (lazo apagado a conciencia)",
          hc._dispatcher_apagado_a_proposito() is True)

    hc._launchctl_estado = lambda: {hc.DISPATCHER_LABEL: ("1234", "0")}
    check("daemon cargado y sin HALT → NO exento, el latido rancio alerta",
          hc._dispatcher_apagado_a_proposito() is False)

    # Fail-CLOSED: si no se puede leer launchctl, no se exime. Un motor que puede estar muerto y una
    # consulta que falla no son lo mismo, y aquí el error caro es callarse.
    hc._launchctl_estado = lambda: None
    check("launchctl ilegible → NO exento (fail-closed hacia avisar)",
          hc._dispatcher_apagado_a_proposito() is False)

    salida.halted, hc._launchctl_estado = orig_halted, orig_estado

    # ── Nº4: el acuse encola de verdad ─────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        q.QUEUE = os.path.join(tmp, "queue")
        q._ensure_dirs()
        salida.halted = lambda: False
        jid = hc._encolar_investigacion("daemon_fallando:x", "El daemon x falló su última pasada.")
        check("sin HALT → devuelve un id de job", bool(jid))
        pend = os.listdir(os.path.join(q.QUEUE, "pending"))
        check("el job existe de verdad en pending/", len(pend) == 1)

        job = q._load(os.path.join(q.QUEUE, "pending", pend[0]))
        check("el job pasa la allowlist CERRADA de la cola (sin campos nuevos)",
              q._validate(job) is None)
        check("la intención nombra la clave estable, no un texto genérico",
              "daemon_fallando:x" in job["intencion"])
        check("la intención manda cerrarla con salud.py resuelto",
              "salud.py resuelto" in job["intencion"])
        check("la intención ofrece el libro de deuda si no se puede cerrar",
              "deuda.py abrir" in job["intencion"])
        check("procedencia trazable al healthcheck", job["procedencia"].startswith("healthcheck:"))
        # 20-sep-26: el job declara su entregable (la anotación que su propio encargo pide), o el
        # dispatcher no lo cierra. Antes se cerraba con rc=0 aunque no quedara nada escrito.
        check("el job declara qué tiene que dejar escrito",
              job.get("prueba") == {"tipo": "deuda", "clave": "daemon_fallando:x"})
        check("caduca (si el lazo está parado no se acumula para siempre)", bool(job["expira"]))

        # Con HALT no se promete lo que no va a pasar
        salida.halted = lambda: True
        jid2 = hc._encolar_investigacion("otra_clave", "otra cosa")
        check("con HALT → None (no encola ni promete)", jid2 is None)
        check("y no deja job suelto en la cola",
              len(os.listdir(os.path.join(q.QUEUE, "pending"))) == 1)

        salida.halted = orig_halted

    print("test_healthcheck_deadman: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
