#!/usr/bin/env python3
"""test_token_rotacion.py — canjear un refresh token no es mirar, es gastar.

NORMA: `feedback-refresh-token-es-de-un-solo-uso` (clase BLOQUEO) — «canjear un refresh token es
una operación DESTRUCTIVA (rotación de un solo uso) — nunca lo hagas como diagnóstico "de solo
lectura" sin guardar el par nuevo».

El marcador es `grant_type=refresh_token`, el parámetro del propio estándar OAuth 2.0: si está en
el comando, ese comando canja. No hay heurística que calibrar, y por eso el test se centra en las
FORMAS de escribirlo (curl con -d, JSON, comillas, espacios) y en que la puerta sancionada
—`_oauth_refresh`— nunca se bloquee a sí misma.

Sobre los ~2.900 comandos Bash reales de los transcripts, este patrón aparece 0 veces: el guard
no puede romper nada que se estuviera haciendo.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import subprocess  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "token_rotacion_guard.py")
ENV = dict(_os.environ)
ENV.pop("BTP_TOKEN_OK", None)


def _rc(cmd, env=None, tool="Bash"):
    p = subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=20,
                       env=env or ENV,
                       input=json.dumps({"tool_name": tool, "tool_input": {"command": cmd}}))
    return p.returncode, (p.stderr or "")


DENY = [
    ("curl -d 'grant_type=refresh_token&refresh_token=X' https://mcp.consensus.app/token",
     "curl con -d, la forma de manual"),
    ('curl -X POST -d "grant_type=refresh_token" https://scite.ai/oauth/token', "comillas dobles"),
    ("curl --data 'grant_type = refresh_token' https://x/token", "espacios alrededor del ="),
    ('curl -H "Content-Type: application/json" -d \'{"grant_type": "refresh_token"}\' https://x/token',
     "cuerpo JSON con dos puntos"),
    ("curl -d 'GRANT_TYPE=REFRESH_TOKEN' https://x/token", "mayúsculas"),
    ("python3 -c \"import urllib.request; urllib.request.urlopen('https://x/token', b'grant_type=refresh_token')\"",
     "urllib desde la línea de comandos"),
    ("echo probando && curl -d grant_type=refresh_token https://x/token", "encadenado detrás de otro"),
]

ALLOW = [
    ("python3 -c \"import sys; sys.path.insert(0,'tools'); import _oauth_refresh; print(_oauth_refresh.renovar(d, u))\"",
     "LA PUERTA: canjea y guarda el par nuevo"),
    ("python3 tools/_oauth_refresh.py", "la puerta, invocada como script"),
    ("grep -rn 'grant_type' tools/", "buscar el término no es canjearlo"),
    ("cat tools/_oauth_refresh.py", "leer el módulo"),
    ("curl -d 'grant_type=authorization_code&code=X' https://x/token",
     "el OTRO grant_type (login inicial) no rota nada guardado"),
    ("curl https://x/token", "un GET al endpoint no canjea"),
    ("echo refresh_token", "mencionar la palabra"),
    ("python3 tools/ia_health.py", "un tool que por dentro quizá renueve: el guard no lo ve"),
]

fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


print("=== DEBE DENEGAR (canjea de verdad) ===")
for cmd, desc in DENY:
    rc, _ = _rc(cmd)
    check(desc, rc == 2)
    print(("  ✅ " if rc == 2 else "  ❌ PASA (MAL): ") + desc)

print()
print("=== DEBE PASAR ===")
for cmd, desc in ALLOW:
    rc, msg = _rc(cmd)
    check(desc, rc == 0)
    print(("  ✅ " if rc == 0 else "  ❌ DENEGADO (MAL): ") + desc)

print()
print("=== LO DEMÁS ===")
rc, msg = _rc(DENY[0][0])
check("el mensaje nombra la puerta (_oauth_refresh)", "_oauth_refresh" in msg)
check("el mensaje explica POR QUÉ duele (los 28 días de Consensus)", "28 d" in msg)
check("BTP_TOKEN_OK=1 → PASA", _rc(DENY[0][0], env=dict(ENV, BTP_TOKEN_OK="1"))[0] == 0)
check("BTP_TOKEN_OK=0 NO cuenta", _rc(DENY[0][0], env=dict(ENV, BTP_TOKEN_OK="0"))[0] == 2)
check("tool que no es Bash → PASA", _rc(DENY[0][0], tool="Write")[0] == 0)
p = subprocess.run([_sys.executable, GUARD], input="{no es json", capture_output=True,
                   text=True, timeout=20, env=ENV)
check("payload ilegible → PASA (fail-OPEN deliberado)", p.returncode == 0)
for desc, ok in casos[len(DENY) + len(ALLOW):]:
    print(("  ✅ " if ok else "  ❌ ") + desc)

print()
print("RESULTADO token_rotacion: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ EL REFRESH TOKEN NO SE QUEMA MIRANDO" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
