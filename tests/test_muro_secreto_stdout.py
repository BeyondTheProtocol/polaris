#!/usr/bin/env python3
"""test_muro_secreto_stdout.py — el muro no deja QUEMAR un secreto en la transcripción.

NORMA: `feedback-secreto-impreso-esta-quemado` — «un secreto que imprimo por pantalla queda
escrito en la transcripción del chat, que se guarda: está quemado». Clase BLOQUEO, y hasta
hoy (18-sep-26) con `mecanismo: null`: era texto que yo tenía que recordar.

QUÉ CUBRÍA YA EL MURO (verificado ejecutando el guard, no de memoria): `SECRET_HINTS` en
`check_subcommand()` deniega cualquier subcomando que NOMBRE un fichero de secretos, así que
`cat .env`, `base64 tools/.telegram_secrets.json` y `ls ~/.ssh` ya caían. El agujero no era
nombrar: era que la shell IMPRIME cosas que el guard nunca llega a ver, porque las expande
DESPUÉS de que él haya mirado:

  1. `echo $ANTHROPIC_API_KEY` — el guard tokeniza un literal `$ANTHROPIC_API_KEY`; bash
     escribe la clave. Es el hermano exacto de `$(...)`: expansión que derrota al análisis
     por tokens, y por eso el check vive al lado de esa comprobación.
  2. `cat tools/.tele*.json` — el glob no casa con ningún SECRET_HINT como literal, pero
     resuelve al fichero de secretos. El guard NO puede juzgar por la cadena: tiene que
     expandir el patrón y mirar lo que sale (norma `feedback-muro-soak-antes-de-fusionar`:
     «el guard se juzga por ARGUMENTO resuelto, no por la cadena del comando»).

LO QUE NO SE PUEDE ROMPER: el trabajo normal del lazo. El intento del 17-sep denegaba
`ls -la` y `git status` y hubo que revertirlo; esos dos casos son ahora regresión fija.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige  # noqa: E402
_exige("sin-halt")
import json  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "muro_guard.py")

# El glob se resuelve contra el cwd que manda el harness: un directorio de mentira con un
# fichero de secretos dentro, para no depender de que exista el de verdad (ni tocarlo).
_TMP = tempfile.mkdtemp(prefix="muro_secreto_")
open(_os.path.join(_TMP, ".telegram_secrets.json"), "w").write("{}\n")
open(_os.path.join(_TMP, "app_secrets.json"), "w").write("{}\n")
open(_os.path.join(_TMP, "_secrets.py"), "w").write("# el modulo que LEE los secretos\n")
open(_os.path.join(_TMP, "notas.md"), "w").write("hola\n")
open(_os.path.join(_TMP, "config.json"), "w").write("{}\n")

ENV = dict(_os.environ, MURO_PROFILE="privileged")
ENV.pop("MURO_ALLOW_CLINICAL", None)

DENY = [
    # (1) expansión de una variable de entorno con pinta de secreto
    ("echo $ANTHROPIC_API_KEY", "echo de una API key por stdout"),
    ('echo "${TELEGRAM_BOT_TOKEN}"', "${} entre comillas dobles (sí expande)"),
    ("echo ${GITHUB_TOKEN:-vacio}", "${VAR:-default} también expande"),
    ("python3 tools/salida.py --token $BOT_TOKEN", "secreto como argumento (queda en el log)"),
    ("printf '%s' $OPENAI_API_KEY", "printf en vez de echo, mismo quemado"),
    ("echo $AWS_SECRET_ACCESS_KEY", "credencial de AWS"),
    ("echo $DB_PASSWORD", "password"),
    # (2) glob que RESUELVE a un fichero de secretos
    ("cat .tele*.json", "glob que destapa el fichero de secretos"),
    ("cat a*.json", "glob cuyo LITERAL no casa con SECRET_HINTS pero cuya expansion si"),
    ("head -5 .*_secrets.json", "glob con punto inicial"),
]

ALLOW = [
    # La regresión del 17-sep: esto NO se puede denegar.
    ("ls -la", "listar el directorio (la regresión que tumbó el intento anterior)"),
    ("git status", "git status (idem)"),
    ("git status --short", "git de lectura"),
    ("ls tools/", "listar tools"),
    ("grep -rn token tools/", "grepear la palabra token (no imprime ninguna variable)"),
    ("python3 tools/seguimiento.py revisar", "el lazo corriendo su tool"),
    # Variables que NO son secretos: el check mira el NOMBRE, no el `$`.
    ("echo $PATH", "$PATH no es un secreto"),
    ("echo $HOME", "$HOME no es un secreto"),
    ("echo ${PWD}", "$PWD no es un secreto"),
    # Globs que NO resuelven a ningún secreto.
    ("cat *.md", "glob que solo casa con markdown"),
    ("cat config.json", "fichero normal por su nombre"),
    ("cat c*.json", "glob que solo casa con config.json"),
    ("ls *.py", "glob que casa con _secrets.py: es CODIGO, no un almacen de claves"),
    ("ls tools/*.py | head -80", "el comando cotidiano que el falso positivo tumbo"),
]


def denegado(cmd):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": _TMP}
    p = subprocess.run([_sys.executable, GUARD], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=20, env=ENV, cwd=_TMP)
    return p.returncode != 0


fallos = 0
print("=== DEBE DENEGAR (secreto que acabaría en la transcripción) ===")
for cmd, desc in DENY:
    if denegado(cmd):
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ PASA (MAL): %s  ->  %s" % (desc, cmd[:60]))

print()
print("=== DEBE PASAR (el trabajo legítimo del lazo) ===")
for cmd, desc in ALLOW:
    if not denegado(cmd):
        print("  ✅ %s" % desc)
    else:
        fallos += 1
        print("  ❌ DENEGADO (MAL): %s  ->  %s" % (desc, cmd[:60]))

print()
print("RESULTADO secreto→stdout: %d fallos" % fallos)
print("✅ EL SECRETO NO SALE POR STDOUT" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
