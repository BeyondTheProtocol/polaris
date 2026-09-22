#!/usr/bin/env python3
"""test_evidencia_no_cuelga.py — el carril de evidencia FALLA RÁPIDO, no se cuelga (27/7/26).

Nace de un hallazgo feo: el token OAuth de Consensus caducó el 29-jun y, durante 28 días, CADA
consulta abría un flujo de autorización por navegador y se quedaba BLOQUEADA esperando un callback
que nadie iba a dar (en el lazo, en un daemon o en una rutina no hay nadie para pulsar nada). Se
comía 120 s por intento y `ia_health` decía 🟢 OK porque el FICHERO del token existía.

Tres invariantes, tres cosas que no pueden volver:
  1. Sin `--login`, un cliente MCP que necesite autorizar NO abre el navegador: falla en segundos
     y dice el paso exacto. (Solo se comprueba si el venv existe; si no, se salta.)
  2. `ia_health` no puede decir OK con un token CADUCADO: presencia ≠ validez.
  3. El registro le pone TIMEOUT a los cerebros del carril de evidencia, para que un cuelgue del
     servicio no estanque la cadena de la centralita.

Estilo test_ia/test_borde: cada caso suma OK/fallo; exit = nº de fallos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── 1 · Sin --login no hay navegador: falla rápido y dice qué hacer ─────────────────────
def test_falla_rapido():
    # El venv vive en casa base (~/claudecode); un worktree no se lo lleva, así que se busca allí
    # como segunda opción para que el test EJERCITE el invariante también desde una rama.
    venv = os.path.join(ROOT, ".venv-consensus", "bin", "python")
    if not os.path.exists(venv):
        venv = os.path.expanduser("~/claudecode/.venv-consensus/bin/python")
    script = os.path.join(ROOT, "tools", "consensus_mcp.py")
    if not os.path.exists(venv):
        print("  (saltado: sin .venv-consensus en esta máquina)")
        return
    t0 = time.time()
    try:
        p = subprocess.run([venv, script, "una pregunta ingeniera generica"],
                           capture_output=True, text=True, timeout=45)
        tardo, rc, err = time.time() - t0, p.returncode, (p.stderr or "")
    except subprocess.TimeoutExpired:
        ok(False, "consensus_mcp SIGUE colgándose (>45 s) sin --login")
        return
    if rc == 0:
        ok(True, "consensus_mcp responde (el OAuth está vivo): nada que probar aquí")
        return
    ok(tardo < 30, "sin --login, el fallo es RÁPIDO (%.1f s, antes se colgaba para siempre)" % tardo)
    ok("--login" in err,
       "el error dice el paso exacto para arreglarlo (evidencia.py consensus --login)")
    ok("TaskGroup" not in err.split("ERROR:")[-1],
       "el motivo real no queda sepultado bajo el ExceptionGroup del SDK")


# ── 2 · ia_health no puede dar OK a un token caducado ──────────────────────────────────
def test_health_honesto():
    import ia_health
    tmp = tempfile.mkdtemp(prefix="tok_test_")
    os.environ["BTP_REPO"] = tmp
    # probe() refresca de paso la señal de crédito contra la API REAL de Anthropic. En una batería
    # eso es una llamada de red por test y un efecto sobre el estado de coste que otros tests leen.
    os.environ["BTP_SIN_SONDA_CREDITO"] = "1"
    carpeta = os.path.join(tmp, "tools", "state", "consensus")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, "tokens.json")

    def escribe(expires_in, edad_seg):
        json.dump({"access_token": "x", "expires_in": expires_in, "refresh_token": "y"},
                  open(ruta, "w"))
        t = time.time() - edad_seg
        os.utime(ruta, (t, t))

    # El caso real del 27/7: token de 4 h escrito hace 28 días, PERO con refresh token. Caducado no
    # es muerto: el cliente lo renueva solo antes de conectar (_oauth_refresh), así que reportar
    # ámbar aquí sería la falsa alarma simétrica al falso verde — y en scite, cuyo token dura 15 min,
    # dejaría el panel en ámbar permanente por un carril que funciona.
    escribe(14400, 28 * 86400)
    salida = [x for x in ia_health.probe()["ias"] if x["ia"] == "Consensus"]
    ok(bool(salida), "la sonda sigue devolviendo la fila de Consensus")
    if salida:
        fila = salida[0]
        ok(fila["code"] == "OK" and "renueva" in fila["detalle"],
           "caducado CON refresh → OK, y dice que se renueva solo")

    # Sin refresh token sí hace falta el humano: eso es lo que tiene que cantar.
    json.dump({"access_token": "x", "expires_in": 14400}, open(ruta, "w"))
    t = time.time() - 28 * 86400
    os.utime(ruta, (t, t))
    fila = [x for x in ia_health.probe()["ias"] if x["ia"] == "Consensus"][0]
    ok(fila["code"] == "DEGRADED", "caducado SIN refresh → DEGRADADA (era el falso verde)")
    ok("--login" in fila["detalle"], "y dice el paso exacto para revivirlo")
    # token recién emitido → sí vale
    escribe(14400, 60)
    fila = [x for x in ia_health.probe()["ias"] if x["ia"] == "Consensus"][0]
    ok(fila["code"] == "OK", "token vigente → OK (no se pasa de fail-closed a inútil)")
    # sin fichero → degradada, como siempre
    os.remove(ruta)
    fila = [x for x in ia_health.probe()["ias"] if x["ia"] == "Consensus"][0]
    ok(fila["code"] == "DEGRADED" and "sin token" in fila["detalle"],
       "sin fichero de token → DEGRADADA")
    os.environ.pop("BTP_REPO", None)


# ── 3 · El registro le pone timeout al carril de evidencia ──────────────────────────────
def test_timeout_en_registro():
    reg = json.load(open(os.path.join(ROOT, "tools", "peripheries.json"), encoding="utf-8"))
    for c in reg.get("cerebros", []):
        if c.get("name") in ("consensus", "scite", "perplexity"):
            ok(isinstance(c.get("timeout"), int) and c["timeout"] <= 200,
               "%s tiene timeout acotado en el registro (%s)" % (c["name"], c.get("timeout")))


def main():
    test_falla_rapido()
    test_health_honesto()
    test_timeout_en_registro()
    print("RESULTADO carril de evidencia: %d OK, %d fallos" % (_pass, _fail))
    if not _fail:
        print("✅ EVIDENCIA NO SE CUELGA")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
