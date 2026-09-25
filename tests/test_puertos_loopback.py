#!/usr/bin/env python3
"""test_puertos_loopback.py — nada escucha fuera de loopback sin estar en la lista cerrada.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026
(punto S14: «el relay del Observatorio escucha en la IP de Tailscale; choca con "cero puertos"»).

Qué comprueba:
  · Parte fija (corre en cualquier sitio): el parser de `netstat -anv` y el casado contra
    tools/puertos_excepciones.json, con salidas de muestra.
  · Parte viva (solo macOS): lee los LISTEN reales de la máquina con `netstat -anv -p tcp`, que
    a diferencia de `lsof` sin sudo SÍ ve los sockets de root (sshd, VNC y SMB los abre launchd)
    y los de la extensión de Tailscale. Todo lo que escuche fuera de 127.0.0.1/::1 y no case con
    una entrada de la lista → ROJO. Además, si `tailscale serve` tiene Funnel (público) → ROJO.

Las entradas en estado «revisar» no ponen rojo (llevan su deuda abierta en tools/deuda.py);
se listan en cada pasada para que no se olviden.
"""
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXC_PATH = os.path.join(ROOT, "tools", "puertos_excepciones.json")
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
EFIMERO_MIN = 32768
TAILSCALE_BINS = ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "tailscale")

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── lógica ──────────────────────────────────────────────────────────────────────────────

def parse_netstat(texto):
    """Devuelve [(proceso, pid, host, puerto)] de las líneas LISTEN de `netstat -anv`."""
    out = []
    for linea in texto.splitlines():
        if "LISTEN" not in linea or not linea.startswith("tcp"):
            continue
        campos = linea.split()
        local = campos[3]
        host, _, puerto = local.rpartition(".")
        m = re.search(r"LISTEN\s+\d+\s+\d+\s+\d+\s+\d+\s+(.+?):(\d+)\s+[0-9a-f]{5}\s", linea)
        if not puerto.isdigit() or not m:
            continue
        out.append((m.group(1).strip(), int(m.group(2)), host, int(puerto)))
    return out


def alcance(host):
    """'loopback' | 'tailnet' | 'todas' (comodín o cualquier otra interfaz: LAN incluida)."""
    if host in ("*", ""):
        return "todas"
    try:
        ip = ipaddress.ip_address(host.split("%")[0])   # «fe80::1%lo0» → sin la zona
    except ValueError:
        return "todas"
    if ip.is_loopback:
        return "loopback"
    if (ip.version == 4 and ip in TAILNET_V4) or (ip.version == 6 and ip in TAILNET_V6):
        return "tailnet"
    return "todas"


def casa(entrada, proceso, puerto, alc, argv):
    if entrada["proceso"] != proceso:
        return False
    if entrada["puerto"] == "dinamico":
        if puerto < EFIMERO_MIN:
            return False
    elif entrada["puerto"] != puerto:
        return False
    # «tailnet» no cubre «todas»: una excepción de tailnet no deja abrir a la LAN.
    if entrada["alcance"] == "tailnet" and alc != "tailnet":
        return False
    if entrada.get("argv_contiene") and entrada["argv_contiene"] not in (argv or ""):
        return False
    return True


def evaluar(escuchas, excepciones, argv_de):
    """Devuelve (infractores, usadas) — infractores = escuchas sin excepción que case."""
    infractores, usadas = [], set()
    for proceso, pid, host, puerto in escuchas:
        alc = alcance(host)
        if alc == "loopback":
            continue
        argv = argv_de(pid)
        hit = [i for i, e in enumerate(excepciones) if casa(e, proceso, puerto, alc, argv)]
        if hit:
            usadas.update(hit)
        else:
            infractores.append((proceso, pid, host, puerto, alc))
    return infractores, usadas


def funnel_activo(serve_json):
    if not serve_json:
        return False
    return bool(serve_json.get("AllowFunnel")) and any(serve_json["AllowFunnel"].values())


# ── parte fija ──────────────────────────────────────────────────────────────────────────

MUESTRA = """\
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)   rxbytes txbytes rhiwat shiwat  process:pid    state  options
tcp4       0      0  127.0.0.1.8787         *.*                    LISTEN    0 0 131072 131072           Python:951    00000 00000006 00000000000018e8 00000000 00000800      1      0 000000
tcp4       0      0  100.114.113.73.8788    *.*                    LISTEN    0 0 131072 131072           Python:1450   00000 00000006 000000000000310e 00000000 00000800      2      0 000000
tcp6       0      0  fd7a:115c:a1e0::.9090  *.*                    LISTEN    0 0 131072 131072 io.tailscale.ipn:1032   00180 00000006 0000000000001dd5 00000000 00000800      1      0 000000
tcp46      0      0  *.56457                *.*                    LISTEN    0 0 131072 131072         rapportd:625    00100 00000006 000000000000cd5d 00000000 00080800      1      0 000000
tcp4       0      0  *.22                   *.*                    LISTEN    0 0 131072 131072           launchd:1     00000 00000006 0000000000000001 00000000 00000800      1      0 000000
tcp4       0      0  *.9999                 *.*                    LISTEN    0 0 131072 131072     Google Chrome:777   00000 00000006 0000000000000001 00000000 00000800      1      0 000000
tcp4       0      0  192.168.1.20.8788      *.*                    LISTEN    0 0 131072 131072           Python:1450   00000 00000006 000000000000310e 00000000 00000800      2      0 000000
tcp4       0      0  127.0.0.1.50000        10.0.0.1.443           ESTABLISHED 0 0 131072 131072       Python:1   00000 00000006 0 0 0 1 0 0
"""


def parte_fija(exc):
    esc = parse_netstat(MUESTRA)
    check("parser: 7 LISTEN (ignora ESTABLISHED)", len(esc) == 7)
    check("parser: proceso con espacios", ("Google Chrome", 777, "*", 9999) in esc)
    check("parser: IPv6 de la tailnet", ("io.tailscale.ipn", 1032, "fd7a:115c:a1e0::", 9090) in esc)
    check("alcance loopback", alcance("127.0.0.1") == "loopback")
    check("alcance tailnet v4", alcance("100.114.113.73") == "tailnet")
    check("alcance tailnet v6", alcance("fd7a:115c:a1e0::") == "tailnet")
    check("alcance LAN = todas", alcance("192.168.1.20") == "todas")
    check("alcance comodín = todas", alcance("*") == "todas")

    # Lista de muestra propia: la real cambia al apagar servicios (el 25-sep se retiró el 8788)
    # y la parte fija no puede depender de ella.
    exc_m = [
        {"proceso": "Python", "puerto": 8788, "alcance": "tailnet", "argv_contiene": "preview_remoto.py 8787 8788"},
        {"proceso": "io.tailscale.ipn", "puerto": 9090, "alcance": "tailnet"},
        {"proceso": "rapportd", "puerto": "dinamico", "alcance": "todas"},
        {"proceso": "launchd", "puerto": 22, "alcance": "todas"},
    ]
    argv = {1450: "/usr/bin/python3 /x/tools/preview_remoto.py 8787 8788"}.get
    inf, _ = evaluar(esc, exc_m, argv)
    claves = {(p, puerto, a) for p, _, _, puerto, a in inf}
    check("desconocido en todas las interfaces → rojo", ("Google Chrome", 9999, "todas") in claves)
    check("relay en la LAN no lo cubre su excepción de tailnet", ("Python", 8788, "todas") in claves)
    check("relay 8788 en la tailnet con su argv → permitido", ("Python", 8788, "tailnet") not in claves)
    check("rapportd dinámico → permitido", ("rapportd", 56457, "todas") not in claves)
    check("loopback nunca es infractor", all(a != "loopback" for *_, a in inf))

    inf2, _ = evaluar([("Python", 1450, "100.114.113.73", 8788)], exc_m, lambda pid: "python3 otro.py")
    check("mismo puerto con otro programa → rojo", len(inf2) == 1)

    check("funnel: sin clave → no", not funnel_activo({"TCP": {"9090": {"HTTP": True}}}))
    check("funnel: activo → sí", funnel_activo({"AllowFunnel": {"polaris.x.ts.net:443": True}}))

    for i, e in enumerate(exc):
        ok = {"proceso", "puerto", "alcance", "estado", "por_que"} <= set(e)
        check("excepción %d con campos completos" % i, ok)
        check("excepción %d estado válido" % i, e.get("estado") in ("excepcion", "revisar"))
        check("excepción %d alcance válido" % i, e.get("alcance") in ("tailnet", "todas"))
        if e.get("estado") == "revisar":
            check("excepción %d «revisar» lleva deuda" % i, bool(e.get("deuda")))


# ── parte viva ──────────────────────────────────────────────────────────────────────────

def _argv(pid):
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def _serve_json():
    for b in TAILSCALE_BINS:
        if os.path.exists(b) or shutil.which(b):
            try:
                r = subprocess.run([b, "serve", "status", "--json"], capture_output=True, text=True,
                                   timeout=10)
                return json.loads(r.stdout) if r.stdout.strip() else {}
            except Exception:
                return None
    return None


def parte_viva(exc):
    if sys.platform != "darwin":
        print("  (parte viva saltada: no es macOS)")
        return
    try:
        texto = subprocess.run(["netstat", "-anv", "-p", "tcp"], capture_output=True, text=True,
                               timeout=20).stdout
    except Exception as e:
        check("netstat -anv se puede leer (%s)" % e, False)
        return
    esc = parse_netstat(texto)
    check("netstat devuelve algún LISTEN", len(esc) > 0)
    inf, usadas = evaluar(esc, exc, _argv)
    for proceso, pid, host, puerto, alc in inf:
        check("escucha fuera de loopback SIN excepción: %s:%d en %s:%d (%s)"
              % (proceso, pid, host, puerto, alc), False)
    serve = _serve_json()
    if serve is not None:
        check("tailscale serve sin Funnel (nada público)", not funnel_activo(serve))
    pendientes = [e for i, e in enumerate(exc) if e["estado"] == "revisar" and i in usadas]
    if pendientes:
        print("  ⚠️ %d escucha(s) en «revisar» (deuda abierta, no rojo):" % len(pendientes))
        for e in pendientes:
            print("     · %s:%s (%s) → %s" % (e["proceso"], e["puerto"], e["alcance"], e["deuda"]))
    sobrantes = [e for i, e in enumerate(exc) if i not in usadas and e["puerto"] != "dinamico"]
    for e in sobrantes:
        print("  ℹ️ excepción sin nada escuchando ahora (¿apagado? quítala): %s:%s"
              % (e["proceso"], e["puerto"]))


def main():
    with open(EXC_PATH, encoding="utf-8") as f:
        exc = json.load(f)["escuchas"]
    parte_fija(exc)
    parte_viva(exc)
    print("%s puertos_loopback: %d ok, %d fallos" % ("✅" if _fail == 0 else "❌", _pass, _fail))
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
