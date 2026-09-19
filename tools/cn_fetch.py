#!/usr/bin/env python3
"""Lee webs chinas geobloqueadas sin tocar la VPN.

Los servidores de varios gobiernos provinciales chinos (Hainan, p. ej.) sólo
aceptan conexiones desde IP chinas: desde España la conexión TCP ni siquiera se
abre. Comprobado el 11-sep-2026 con check-host.net: de 40 nodos en 25 países
sólo respondió el de Hong Kong.

Esto prueba varias vías por orden y devuelve la primera que trae texto real.
Ninguna necesita cambiar la VPN (que rompe Tailscale y corta Claude).

    python3 tools/cn_fetch.py <url> [--out fichero] [--json]

Sale 0 si consiguió el texto; 1 si ninguna vía funcionó.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
TIMEOUT = 60
MIN_LEN = 800  # menos que esto suele ser una página de error o un captcha


def _get(url: str, headers: dict | None = None, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _quote(url: str) -> str:
    return urllib.parse.quote(url, safe="")


def v_directo(url: str) -> str:
    return _get(url, {"Accept-Language": "zh-CN,zh;q=0.9"}, timeout=15)


def v_jina(url: str) -> str:
    return _get(f"https://r.jina.ai/{url}")


def v_jina_texto(url: str) -> str:
    return _get(f"https://r.jina.ai/{url}", {"x-respond-with": "text"})


def v_vps(url: str) -> str:
    """Descarga desde el servidor de Hong Kong (Alibaba SWAS), si está vivo.

    Sirve para lo que bloquea a Europa pero no a China. NO sirve para
    qionghai/wst.hainan.gov.cn (puerto cerrado también desde allí) ni para
    ChiCTR, DXY o el registro CDE, que filtran por cabeceras de navegador.
    """
    import shlex
    import subprocess

    host = os.environ.get("BTP_CN_VPS", "root@47.243.53.161")
    clave = os.path.expanduser(os.environ.get("BTP_CN_VPS_KEY", "~/.ssh/hk_lecheng"))
    if not os.path.exists(clave):
        raise RuntimeError("sin clave del VPS")
    cmd = (
        f"curl -sL -m 40 -A {shlex.quote(UA)} -H 'Accept-Language: zh-CN,zh;q=0.9' "
        f"{shlex.quote(url)}"
    )
    r = subprocess.run(
        ["ssh", "-i", clave, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=no", host, cmd],
        capture_output=True, timeout=90,
    )
    return r.stdout.decode("utf-8", "replace")


def v_vps_proxy(url: str) -> str:
    """Desde el servidor de Hong Kong, a través de un proxy HTTP con IP china.

    Es la única vía que abre qionghai/wst.hainan.gov.cn, DXY y el registro CDE.
    El proxy sale de `BTP_CN_PROXY` o de /opt/proxy_ok.txt en el servidor, que
    `scan.sh` refresca; los proxies públicos caducan, así que si falla hay que
    volver a barrer (ver la memoria del geobloqueo).
    """
    import shlex
    import subprocess

    host = os.environ.get("BTP_CN_VPS", "root@47.243.53.161")
    clave = os.path.expanduser(os.environ.get("BTP_CN_VPS_KEY", "~/.ssh/hk_lecheng"))
    if not os.path.exists(clave):
        raise RuntimeError("sin clave del VPS")
    proxy = os.environ.get("BTP_CN_PROXY", "")
    px = f"--proxy {shlex.quote(proxy)}" if proxy else '--proxy "http://$(head -1 /opt/proxy_ok.txt)"'
    cmd = (
        f"curl -sL -m 40 {px} -A {shlex.quote(UA)} "
        f"-H 'Accept-Language: zh-CN,zh;q=0.9' {shlex.quote(url)}"
    )
    r = subprocess.run(
        ["ssh", "-i", clave, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=no", host, cmd],
        capture_output=True, timeout=90,
    )
    return r.stdout.decode("utf-8", "replace")


def v_wayback(url: str) -> str:
    api = f"https://archive.org/wayback/available?url={_quote(url)}"
    data = json.loads(_get(api, timeout=30))
    snap = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not snap.get("available"):
        raise RuntimeError("sin copia en Wayback")
    return _get(snap["url"], timeout=45)


def v_timetravel(url: str) -> str:
    # Memento: busca la copia más reciente en cualquier archivo público
    hdr = _get(f"http://timetravel.mementoweb.org/timemap/link/{url}", timeout=45)
    for line in hdr.splitlines():
        if 'rel="memento"' in line or "memento" in line:
            enlace = line.split(">")[0].lstrip(" <")
            if enlace.startswith("http"):
                return _get(enlace, timeout=45)
    raise RuntimeError("sin memento")


def v_codetabs(url: str) -> str:
    return _get(f"https://api.codetabs.com/v1/proxy?quest={_quote(url)}", timeout=45)


def v_allorigins(url: str) -> str:
    return _get(f"https://api.allorigins.win/raw?url={_quote(url)}", timeout=45)


VIAS = [
    ("directo", v_directo),
    ("r.jina.ai", v_jina),
    ("r.jina.ai/texto", v_jina_texto),
    ("vps-hk", v_vps),
    ("vps-proxy-cn", v_vps_proxy),
    ("wayback", v_wayback),
    ("codetabs", v_codetabs),
    ("allorigins", v_allorigins),
    ("timetravel", v_timetravel),
]


def fetch(url: str) -> tuple[str, str, list[str]]:
    """Devuelve (via, texto, errores)."""
    errores = []
    for nombre, via in VIAS:
        try:
            texto = via(url)
        except Exception as e:  # noqa: BLE001 - queremos seguir probando vías
            errores.append(f"{nombre}: {type(e).__name__} {e}")
            continue
        if len(texto) < MIN_LEN or "403 Forbidden" in texto[:200]:
            errores.append(f"{nombre}: respuesta corta o bloqueada ({len(texto)} B)")
            continue
        return nombre, texto, errores
    return "", "", errores


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    url = args[0]
    salida = None
    if "--out" in sys.argv:
        salida = sys.argv[sys.argv.index("--out") + 1]

    via, texto, errores = fetch(url)
    if not via:
        print("NINGUNA VÍA FUNCIONÓ", file=sys.stderr)
        for e in errores:
            print("  ·", e, file=sys.stderr)
        return 1

    if salida:
        with open(salida, "w", encoding="utf-8") as f:
            f.write(texto)
        print(f"{via} → {salida} ({len(texto)} B)")
    elif "--json" in sys.argv:
        print(json.dumps({"via": via, "len": len(texto), "texto": texto[:200000]}, ensure_ascii=False))
    else:
        print(f"[via: {via}]")
        print(texto[:200000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
