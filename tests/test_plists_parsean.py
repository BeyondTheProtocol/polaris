#!/usr/bin/env python3
"""test_plists_parsean.py — todo plist de tools/launchd/ lo lee EL MISMO parser que usa `activar_daemon.py`.

POR QUÉ EXISTE (10-oct-26). El plist de la nocturna llevaba en un comentario XML la cadena `--kick`: un doble guion dentro
de un comentario es inválido para `plistlib` (expat estricto), aunque `plutil -lint` lo dé por bueno. `activar_daemon.py`
respondió «plist ilegible … line 15, column 64» y la rutina no se pudo cargar. Ningún test lo cazó:
  · `test_plists_home.py` prueba que la instalación REESCRIBE EL HOME (sustitución de texto); nunca parsea el XML;
  · `test_healthcheck_drift_plists.py` compara rutas/labels entre repo e instalados, no la sintaxis.
Se fija:
  1. los 70+ plists del repo parsean con `plistlib.load` (el parser de activar_daemon), todos, por nombre;
  2. ningún comentario XML contiene un doble guion (la causa concreta, para que el mensaje sea claro);
  3. `activar_daemon.py com.btp.suite-nocturna --dry` sale con rc 0 contra un HOME de prueba (sin tocar el real).
"""
import glob
import os
import plistlib
import re
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


plists = sorted(glob.glob(os.path.join(RAIZ, "tools", "launchd", "*.plist")))
print("1) todos parsean con plistlib (el parser de activar_daemon)")
check(len(plists) > 50, "se ven los plists del repo (%d): el test no pasa en vacío" % len(plists))
malos = []
for p in plists:
    try:
        with open(p, "rb") as f:
            plistlib.load(f)
    except Exception as e:   # noqa: BLE001
        malos.append("%s: %s" % (os.path.basename(p), str(e)[:70]))
check(not malos, "ningún plist ilegible (%s)" % malos)

print("2) ningún comentario XML con doble guion")
con_guiones = []
for p in plists:
    t = open(p, encoding="utf-8").read()
    for m in re.finditer(r"<!--(.*?)-->", t, re.S):
        if "--" in m.group(1):
            con_guiones.append(os.path.basename(p))
            break
check(not con_guiones, "sin «--» dentro de comentarios (%s)" % con_guiones)

print("3) activar_daemon --dry de la nocturna")
home = tempfile.mkdtemp(prefix="plists_home_")
os.makedirs(os.path.join(home, "Library", "LaunchAgents"))
env = dict(os.environ, HOME=home)
for k in ("BTP_REPO", "BTP_STATE_DIR"):
    env.pop(k, None)
r = subprocess.run([sys.executable, os.path.join(RAIZ, "tools", "activar_daemon.py"), "com.btp.suite-nocturna", "--dry"],
                   capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL, cwd=RAIZ)
check(r.returncode == 0 and "DRY:" in r.stdout and "ilegible" not in r.stderr,
      "rc 0 y «DRY: …» (rc=%d, stderr=%r)" % (r.returncode, r.stderr[-120:]))
check(not os.listdir(os.path.join(home, "Library", "LaunchAgents")), "y no instala nada en el HOME de prueba")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
