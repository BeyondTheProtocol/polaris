#!/usr/bin/env python3
"""tests/test_interfaces_colima.py — que `ensure_colima` arranque UN perfil y ajuste ese mismo.

El fallo que lo motiva (deuda `interfaces-sh-colima-dns-sin-comillas`, inventario de disco del
15-oct-2026): `tools/interfaces.sh` hacía `colima start --dns ${COLIMA_DNS}` sin comillas, con
COLIMA_DNS="1.1.1.1 8.8.8.8". `colima start [profile] [flags]` toma un `--dns` por IP, así que la
segunda IP se leía como NOMBRE DE PERFIL: nació un perfil colima «8.8.8.8» con 7,7 GiB de disco.
Y como `colima status` y `colima ssh` sin perfil miran `default`, el ajuste de MTU de la VM nunca
caía sobre la VM que se acababa de arrancar.

Corre el punto de entrada real (`interfaces.sh up`) con un `colima` y un `docker` FALSOS al frente
del PATH. El colima falso apunta su argv; el docker falso dice «apagado», así que `prechequeo`
corta con exit 1 antes de compose y de los relays. No toca ni colima, ni docker, ni el Llavero.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(AQUI, "..", "tools", "interfaces.sh")

FALLOS, OK = [], []


def ok(caso, cond, detalle=""):
    (OK if cond else FALLOS).append(caso)
    print(("  ok  " if cond else "  FALLO  ") + caso + ("" if cond else f" {detalle}"))


IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
CON_VALOR = {"--dns", "-n", "-p", "--profile"}

COLIMA_FALSO = """#!%(py)s
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_COLIMA_LOG"], "a") as f:
    f.write(json.dumps(a) + "\\n")
if "ssh" in a and "cat" in a:
    print("1500")
sys.exit(0)
"""

DOCKER_FALSO = """#!/bin/bash
[ "${FAKE_DOCKER_UP:-0}" = "1" ] && [ "${1:-}" = "info" ] && exit 0
exit 1
"""


def perfil(args):
    """El perfil al que apunta una llamada a colima, como lo resolvería colima."""
    for i, a in enumerate(args):
        if a in ("-p", "--profile") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--profile="):
            return a.split("=", 1)[1]
    sub = next((a for a in args if not a.startswith("-")), None)
    if sub in ("start", "status"):
        resto = args[args.index(sub) + 1:]
        i = 0
        while i < len(resto):
            a = resto[i]
            if a == "--":
                break
            if a in CON_VALOR:
                i += 2
                continue
            if not a.startswith("-"):
                return a
            i += 1
    return "default"


def subcomando(args):
    salta = False
    for a in args:
        if salta:
            salta = False
            continue
        if a in ("-p", "--profile"):
            salta = True
            continue
        if not a.startswith("-"):
            return a
    return None


def dns_de(args):
    return [args[i + 1] for i, a in enumerate(args) if a in ("--dns", "-n") and i + 1 < len(args)]


def posicionales_ip(args):
    """IPs sueltas, que no son el valor de un flag: lo que colima tomaría como perfil."""
    sueltas, i = [], 0
    while i < len(args):
        a = args[i]
        if a in CON_VALOR:
            i += 2
            continue
        if IP.match(a):
            sueltas.append(a)
        i += 1
    return sueltas


def corre(entorno_extra):
    tmp = tempfile.mkdtemp(prefix="interfaces-colima-")
    fakebin = os.path.join(tmp, "bin")
    os.makedirs(fakebin)
    for nombre, cuerpo in (("colima", COLIMA_FALSO % {"py": sys.executable}),
                           ("docker", DOCKER_FALSO)):
        p = os.path.join(fakebin, nombre)
        with open(p, "w") as f:
            f.write(cuerpo)
        os.chmod(p, 0o755)
    # REPO falso con token: así token() no llama a `security` (el Llavero real).
    tokdir = os.path.join(tmp, "repo", "tools", "state", "borde_gateway")
    os.makedirs(tokdir)
    with open(os.path.join(tokdir, "token"), "w") as f:
        f.write("token-de-prueba\n")
    log = os.path.join(tmp, "colima.log")
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("BTP_COLIMA") or k.startswith("DOCKER_")
                   or k in ("BTP_SKIP_COLIMA", "BTP_VM_MTU", "COLIMA_PROFILE"))}
    # Locale UTF-8 como en la Terminal: el bash 3.2 de macOS lee `$VAR»` como la variable
    # «VAR\xc2» y, con set -u, revienta. Le pasó al propio arreglo (16-oct-26); en C no se ve.
    env.update({"PATH": fakebin + ":/usr/bin:/bin", "BTP_REPO": os.path.join(tmp, "repo"),
                "FAKE_COLIMA_LOG": log, "LC_ALL": "en_US.UTF-8"})
    env.update(entorno_extra)
    r = subprocess.run(["/bin/bash", SCRIPT, "up"], env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, errors="replace", timeout=60)
    llamadas = []
    if os.path.exists(log):
        with open(log) as f:
            llamadas = [json.loads(l) for l in f if l.strip()]
    return r, llamadas


def de(llamadas, sub):
    return [c for c in llamadas if subcomando(c) == sub]


# ── 1. Docker apagado, valores por defecto: arranca `default` con un --dns por IP ──
r, ll = corre({})
starts, status, sshs = de(ll, "start"), de(ll, "status"), de(ll, "ssh")
ok("el script no revienta por una variable sin definir", "unbound variable" not in r.stderr, f"-> {r.stderr[-300:]}")
ok("docker apagado → un solo `colima start`", len(starts) == 1, f"-> {ll} · stderr={r.stderr[-300:]}")
if starts:
    s = starts[0]
    ok("cada IP del DNS va tras su propio --dns", dns_de(s) == ["1.1.1.1", "8.8.8.8"], f"-> {s}")
    ok("ninguna IP suelta que colima tome como nombre de perfil", not posicionales_ip(s), f"-> {s}")
    ok("arranca el perfil `default`", perfil(s) == "default", f"-> perfil {perfil(s)!r} en {s}")
ok("hay `colima status` y `colima ssh`", bool(status) and bool(sshs), f"-> {ll}")
perfiles = {perfil(c) for c in starts + status + sshs}
ok("start, status y ssh apuntan al MISMO perfil", len(perfiles) == 1, f"-> {perfiles} en {ll}")
mtu = [c for c in sshs if "mtu" in c and "1280" in c and "set" in c]
ok("el MTU se ajusta en la VM arrancada", bool(mtu) and perfil(mtu[0]) == perfil(starts[0]) if starts and mtu else False,
   f"-> {sshs}")

# ── 2. Perfil y DNS a medida: tres IPs, perfil propio, y todo cae en ese perfil ──
r, ll = corre({"BTP_COLIMA_PROFILE": "polaris", "BTP_COLIMA_DNS": "9.9.9.9 1.0.0.1 8.8.4.4"})
starts = de(ll, "start")
ok("perfil a medida: arranca `polaris`", bool(starts) and perfil(starts[0]) == "polaris", f"-> {ll}")
ok("perfil a medida: tres --dns, ninguna IP suelta",
   bool(starts) and dns_de(starts[0]) == ["9.9.9.9", "1.0.0.1", "8.8.4.4"] and not posicionales_ip(starts[0]),
   f"-> {starts}")
ok("perfil a medida: status y ssh también en `polaris`",
   {perfil(c) for c in de(ll, "status") + de(ll, "ssh")} == {"polaris"}, f"-> {ll}")

# ── 3. Docker ya encendido: no arranca nada, pero el MTU mira el perfil configurado ──
r, ll = corre({"FAKE_DOCKER_UP": "1", "BTP_COLIMA_PROFILE": "polaris"})
ok("docker encendido → no hay `colima start`", not de(ll, "start"), f"-> {ll}")
ok("docker encendido → status/ssh en el perfil configurado",
   bool(de(ll, "status")) and {perfil(c) for c in de(ll, "status") + de(ll, "ssh")} == {"polaris"},
   f"-> {ll}")

# ── 4. BTP_SKIP_COLIMA=1: colima ni se toca ──
r, ll = corre({"BTP_SKIP_COLIMA": "1"})
ok("BTP_SKIP_COLIMA=1 → ninguna llamada a colima", ll == [], f"-> {ll}")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_interfaces_colima: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
