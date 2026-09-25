#!/usr/bin/env python3
"""test_kickstart_bootstrap.py — el autofix sabe levantar un daemon caído del dominio entero.

EL FALLO (20-sep-2026). `healthcheck._kickstart_daemon` solo llamaba a `launchctl kickstart -k`,
que exige que el job YA esté cargado. Si se caía del todo (`bootout`), el autofix se rendía y
hacía falta una mano humana. Coste medido: `daemon_roster_caido:com.btp.dispatcher` y
`:com.btp.bot-telegram` a **1.251 detecciones cada uno**. Y con el dispatcher abajo la cola no se
vacía, así que los encargos que debían cerrar esas alertas tampoco corrían: el sistema se quedaba
gritando lo único que no podía arreglar solo. Otra sesión lo dejó diagnosticado el 19-sep y seguía
sin arreglar — detectar no es arreglar.

QUÉ SE PRUEBA, y por qué así. No se tocan daemons de verdad: levantar y tirar el dispatcher del
lazo 24/7 para comprobar una rama de código es peor que el bug. Se sustituye `subprocess.run` y se
comprueba la SECUENCIA, que es lo que define el arreglo:
  · kickstart OK            → no se toca nada más (no se reinicia lo que ya funciona)
  · kickstart falla         → `enable` y luego `bootstrap` (el `enable` importa: `launchctl
                              disable` deja una marca PERSISTENTE que hace fallar el bootstrap
                              con «Input/output error», que no dice nada y no es transitorio)
  · bootstrap falla 1ª vez  → un reintento, porque ese error sí es a veces transitorio
  · sin plist               → no se inventa nada: False
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as hc  # noqa: E402

_pass = _fail = 0


def ok(cond, que):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ✓ %s" % que)
    else:
        _fail += 1
        print("  ✗ %s" % que)


class _R:
    def __init__(self, rc):
        self.returncode = rc
        self.stdout = self.stderr = b""


def _con_launchctl(respuestas, hay_plist=True):
    """Sustituye subprocess.run y os.path.isfile. Devuelve (resultado, verbos_llamados)."""
    llamadas = []
    orig_run, orig_isfile, orig_sleep = hc.subprocess.run, os.path.isfile, hc.time.sleep

    def fake_run(cmd, **kw):
        verbo = cmd[1] if len(cmd) > 1 else "?"
        llamadas.append(verbo)
        return _R(respuestas.get(verbo, [0])[min(
            llamadas.count(verbo) - 1, len(respuestas.get(verbo, [0])) - 1)])

    try:
        hc.subprocess.run = fake_run
        os.path.isfile = lambda p: hay_plist if "LaunchAgents" in str(p) else orig_isfile(p)
        hc.time.sleep = lambda *_a, **_k: None
        return hc._kickstart_daemon("com.btp.prueba"), llamadas
    finally:
        hc.subprocess.run, os.path.isfile, hc.time.sleep = orig_run, orig_isfile, orig_sleep


def main():
    # 1) El camino feliz no cambia: si kickstart va, no se toca nada más.
    res, llamadas = _con_launchctl({"kickstart": [0]})
    ok(res is True, "kickstart OK → revive")
    ok(llamadas == ["kickstart"], "kickstart OK → NO se hace enable ni bootstrap: %s" % llamadas)

    # 2) El arreglo: kickstart falla (job fuera del dominio) → enable + bootstrap.
    res, llamadas = _con_launchctl({"kickstart": [1], "enable": [0], "bootstrap": [0]})
    ok(res is True, "kickstart falla pero bootstrap levanta → revive")
    ok("enable" in llamadas, "se hace `enable` antes (la marca persistente de disable)")
    ok("enable" in llamadas and "bootstrap" in llamadas
       and llamadas.index("enable") < llamadas.index("bootstrap"),
       "enable va ANTES de bootstrap (sin el fallback, ninguno de los dos existe)")

    # 3) El «Input/output error» de launchd es a veces transitorio: un reintento.
    res, llamadas = _con_launchctl({"kickstart": [1], "enable": [0], "bootstrap": [1, 0]})
    ok(res is True, "bootstrap falla la 1ª y va a la 2ª → revive")
    ok(llamadas.count("bootstrap") == 2, "se reintenta el bootstrap exactamente una vez")

    # 4) Y no se insiste para siempre.
    res, llamadas = _con_launchctl({"kickstart": [1], "enable": [0], "bootstrap": [1, 1]})
    ok(res is False, "bootstrap falla dos veces → se rinde y lo dice")
    ok(llamadas.count("bootstrap") == 2, "no hay un tercer intento")

    # 5) Sin plist no hay nada que levantar: no se inventa.
    res, llamadas = _con_launchctl({"kickstart": [1]}, hay_plist=False)
    ok(res is False, "sin plist → False")
    ok("bootstrap" not in llamadas, "sin plist no se intenta bootstrap")

    print("test_kickstart_bootstrap: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
