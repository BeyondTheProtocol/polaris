#!/usr/bin/env python3
"""test_healthcheck_state_aislado.py — `healthcheck` tiene que respetar `BTP_STATE_DIR`.

20-sep-26. `healthcheck.py` fijaba `STATE = REPO/tools/state` sin mirar la variable, mientras
`_casa.state_dir()`, `_lock.py` y una decena de tools más la leen. Consecuencia medida: un script
de prueba con `BTP_STATE_DIR` a un tmpdir dejó su clave inventada en
`tools/state/healthcheck/last_alert_state-operativo.json` del repo REAL.

Lo que estaba en juego no es la limpieza: `tests/test_healthcheck_deadman.py` monta
`BTP_STATE_DIR` + `BTP_TEST_BATTERY` y su comentario dice que `salida.send` enmudece «solo si
BTP_TEST_BATTERY=1 Y el STATE está aislado: hacen falta las dos». La segunda no se cumplía, así
que la protección contra repetir los 14 mensajes del 12-jul-26 se apoyaba en algo que no pasaba.

El import cachea las rutas, así que cada caso corre en su propio subproceso.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def rutas(entorno):
    """(STATE, HC) tal y como los ve un healthcheck importado con ese entorno."""
    env = dict(os.environ, **entorno)
    env.pop("BTP_STATE_DIR", None) if entorno.get("BTP_STATE_DIR") is None else None
    code = ("import sys; sys.path.insert(0, %r);"
            "import healthcheck as hc; print(hc.STATE); print(hc.HC)" % os.path.join(ROOT, "tools"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return (None, None)
    lineas = r.stdout.strip().splitlines()
    return (lineas[0], lineas[1]) if len(lineas) >= 2 else (None, None)


def main():
    tmp = tempfile.mkdtemp(prefix="hc_aislado_")

    state, hc = rutas({"BTP_STATE_DIR": tmp})
    check("con BTP_STATE_DIR, STATE es esa ruta", state == tmp)
    check("con BTP_STATE_DIR, HC cuelga de ella", hc == os.path.join(tmp, "healthcheck"))
    check("con BTP_STATE_DIR, NADA apunta al estado vivo del repo",
          state is not None and not state.startswith(os.path.join(ROOT, "tools", "state")))

    env_sin = {k: v for k, v in os.environ.items() if k != "BTP_STATE_DIR"}
    code = ("import sys; sys.path.insert(0, %r);"
            "import healthcheck as hc; print(hc.STATE)" % os.path.join(ROOT, "tools"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env_sin)
    # Sin la variable, el valor de producción no se mueve: es el que usa el daemon, cuyo plist
    # no la define. Si esto cambiara, healthcheck escribiría su estado en otro sitio sin avisar.
    check("sin la variable, sigue siendo REPO/tools/state (producción intacta)",
          r.returncode == 0 and r.stdout.strip().endswith(os.path.join("tools", "state")))

    print("test_healthcheck_state_aislado: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
