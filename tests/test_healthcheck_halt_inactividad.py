#!/usr/bin/env python3
"""test_healthcheck_halt_inactividad.py — el HALT no puede disparar alarmas de silencio.

Dos checks, el mismo fallo: «daemon inactivo» y `boca_muda`.

EL FALLO (medido el 20-sep-2026). `daemon_inactivo:com.btp.wa-tareas` llevaba **1.177
detecciones en 42 días** de un daemon que funciona: esa misma mañana había corrido y estacionado
191 mensajes en la bandeja de Vega.

LA CAUSA. Durante un HALT los daemons SÍ arrancan, pero se paran en seco y escriben en stderr
(«MURO: HALT activo → no estaciono»). Su `.out` no se toca, y el check de inactividad mide
justamente el mtime del `.out` → los canta como muertos. El mismo fichero ya consultaba
`salida.halted()` en otros dos sitios; este check era el que faltaba.

POR QUÉ IMPORTA, y no es cosmético. 1.177 avisos falsos no son vigilancia, son **fatiga de
alarma**: entrenan a todo el mundo (a {{TITULAR}} la primera) a ignorar el canal donde algún día
habrá un daemon muerto de verdad. Y avisar de que los daemons no trabajan mientras el sistema
está en pausa TOTAL no informa de nada: que no trabajen es exactamente lo que el HALT pide.

Es el mismo criterio que ya se aplicó a la batería de tests (`tests/_entorno._sin_halt`, 13
baterías rojas solo porque el sistema estaba parado) y el mismo que el money-gate que este
check ya tenía: cuando hay una razón sistémica conocida para que algo esté quieto, no se grita.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
# Igual que test_healthcheck_deadman: estado aislado y mutis, porque aquí se toca `salida`.
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="halt_inact_test_")
os.environ["BTP_TEST_BATTERY"] = "1"
import healthcheck as hc  # noqa: E402
import salida             # noqa: E402
# `seguimiento` se importa AQUÍ, antes de tocar nada, y no dentro de main(). Motivo
# medido el 20-sep-26: al llamar a `hc._check_roster_daemons()` aparece
# `<casa base>/tools` al FRENTE del sys.path, así que un `import seguimiento` posterior
# resuelve al fichero de CASA BASE y no al de la rama. El test se quedaba probando el
# código sin el parche y pasaba en verde: verificar en rama antes de fusionar era
# teatro. Deuda abierta con la reproducción; aquí solo se evita pisar la mina.
import seguimiento as seg  # noqa: E402

_pass = _fail = 0


def ok(cond, que):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ✓ %s" % que)
    else:
        _fail += 1
        print("  ✗ %s" % que)


def main():
    orig = salida.halted

    # 1) CON HALT: ni una alerta de inactividad, y se deja constancia de por qué.
    try:
        salida.halted = lambda: True
        alertas, info = hc._check_roster_daemons()
        inact = [c for c, _ in alertas if c.startswith("daemon_inactivo:")]
        ok(not inact, "con HALT activo no se emite ninguna alerta `daemon_inactivo`")
        ok(info.get("inactivos_suprimidos_por_halt") is True,
           "la supresión queda marcada en info (no es un silencio mudo)")
        ok(info.get("inactivos_h") == {}, "no se reporta ningún daemon como inactivo")
    finally:
        salida.halted = orig

    # 2) SIN HALT: el check sigue vivo. Este es el lado que de verdad protege — un arreglo que
    #    apagara la alerta del todo «pasaría» el punto 1 y dejaría el sistema ciego.
    try:
        salida.halted = lambda: False
        alertas, info = hc._check_roster_daemons()
        ok(info.get("inactivos_suprimidos_por_halt") is not True,
           "sin HALT el check NO se suprime")
        ok("inactivos_h" in info,
           "sin HALT se sigue calculando la inactividad de cada daemon")
    finally:
        salida.halted = orig

    # 3) El MISMO criterio para `boca_muda`, que es el canario de la mordaza. Durante un HALT
    #    no sale ni un mensaje POR DISEÑO, así que gritaba cada día de pausa. Hueco real medido:
    #    15 y 16-sep sin un solo envío, con HALT activo hasta el 17. El canario existe para el
    #    silencio que NADIE pidió, no para el que pidió el kill-switch.
    # Hay que FABRICAR la condición que dispara el canario (hoy 0 envíos, ayer >= 3). Sin esto
    # el test pasaba por el motivo equivocado: el día que lo escribí ya habían salido 4 mensajes,
    # así que `boca_muda` no habría cantado ni con el fallo puesto. Lo destapó un mutante.
    import datetime as _dt
    _hoy = _dt.datetime.now()
    _sal = os.path.join(hc.STATE, "salida")
    os.makedirs(_sal, exist_ok=True)
    _f_hoy = os.path.join(_sal, "enviados-%s.jsonl" % _hoy.strftime("%Y-%m-%d"))
    _ayer = (_hoy - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    _f_ayer = os.path.join(_sal, "enviados-%s.jsonl" % _ayer)
    if os.path.exists(_f_hoy):
        os.remove(_f_hoy)                          # hoy: CERO envíos
    with open(_f_ayer, "w", encoding="utf-8") as fh:
        fh.write('{"a":1}\n{"a":2}\n{"a":3}\n')  # ayer: 3 -> el sistema sí hablaba

    _en_ventana = _hoy.hour >= 14                  # el canario solo mira a partir de las 14:00
    try:
        salida.halted = lambda: True
        alertas, info = hc._check_boca_muda()
        ok(not [c for c, _ in alertas if c == "boca_muda"],
           "con HALT activo `boca_muda` no canta pese a 0 envíos hoy y 3 ayer")
        ok(info.get("boca_muda_suprimido_por_halt") is True,
           "la supresión de boca_muda queda marcada en info")
    finally:
        salida.halted = orig

    # El control que da sentido al anterior: SIN halt y con esa misma condición, TIENE que cantar.
    # Sin él, «arreglar» esto podría ser apagar el canario y nadie se enteraría.
    try:
        salida.halted = lambda: False
        alertas, _info = hc._check_boca_muda()
        canta = bool([c for c, _ in alertas if c == "boca_muda"])
        if _en_ventana:
            ok(canta, "sin HALT y con 0 envíos hoy, el canario SÍ canta")
        else:
            print("  ⏭️  antes de las 14:00 el canario no mira: este control no se ejerce ahora")
    finally:
        salida.halted = orig

    try:
        salida.halted = lambda: False
        _alertas, info = hc._check_boca_muda()
        ok(info.get("boca_muda_suprimido_por_halt") is not True,
           "sin HALT el canario de la mordaza sigue armado")
    finally:
        salida.halted = orig

    # 4) `seguimiento.frescura()`, el tercer sitio de la misma familia. Durante un HALT los
    #    agentes no corren, sus latidos envejecen y HOY.md no se regenera: eso no es un
    #    dead-man, es el kill-switch trabajando. Coste medido: 727 + 679 detecciones, las dos
    #    remitidas TRES veces por sesiones distintas sin que nadie buscara por qué volvían.
    #
    #    Se FUERZA la condición (un agente parado de mentira). Sin esto el test pasaría por el
    #    motivo equivocado: hoy está todo sano y no habría ni un aviso de vida que suprimir.
    _orig_hb = seg._heartbeats_problema
    try:
        seg._heartbeats_problema = lambda: [("agente-de-mentira", "parado", 99.0)]

        salida.halted = lambda: False
        avisos = seg.frescura(None)
        vivos = [a for a in avisos if "agente-de-mentira" in a]
        ok(bool(vivos), "sin HALT, un agente parado SÍ se reporta (el dead-man sigue armado)")

        salida.halted = lambda: True
        avisos = seg.frescura(None)
        vivos = [a for a in avisos if "agente-de-mentira" in a]
        ok(not vivos, "con HALT, el agente parado NO se reporta: no correr es lo que se pidió")
        # 20-sep-2026: esto era `ok(bool(git_) or True, …)` — un assert que NO PUEDE fallar, o sea
        # ninguno. Y encima dependía del git real. Ahora se fija el productor y se comprueba de
        # verdad que ese aviso atraviesa el HALT (es estado de git: un cabo suelto sigue colgando
        # esté el sistema parado o no, como dice el docstring de frescura()).
        _orig_colgados = seg.worktrees_colgados
        seg.worktrees_colgados = lambda: [{"rama": "worktree-de-mentira", "ahead": 2,
                                           "edad_dias": 9}]   # fijo, no derivado del umbral
        try:
            avisos = seg.frescura(None)                      # sigue con HALT activo
            git_ = [a for a in avisos if "worktree-de-mentira" in a]
            ok(bool(git_), "el aviso de ramas sin fusionar SÍ sale con HALT (no depende de él)")
            seg.worktrees_colgados = lambda: []
            ok(not [a for a in seg.frescura(None) if "SIN fusionar" in a],
               "sin ramas colgadas no se inventa el aviso (control inverso)")
        finally:
            seg.worktrees_colgados = _orig_colgados
    finally:
        seg._heartbeats_problema = _orig_hb
        salida.halted = orig

    print("test_healthcheck_halt_inactividad: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
