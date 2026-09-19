#!/usr/bin/env python3
"""radar_cn_vps.py — barrido de los registros chinos desde el VPS de Hong Kong (18-sep-2026).

Vive en el servidor de Alibaba (/opt/radar_cn_vps.py) y se ejecuta con el venv de
/opt/radar-venv. Usa Chromium headless porque ChiCTR y el CDE no responden a curl:
  · ChiCTR devuelve 405 a cualquier GET/POST directo, con cookie de sesion o sin ella.
  · El CDE pinta su tabla por JavaScript y su buscador no reacciona a un POST.

Desde aqui la IP es asiatica, asi que no hace falta el proxy para ChiCTR; el CDE se
intenta directo y, si falla, por el proxy de /opt/proxy_ok.txt.

VERIFICADO el 18-sep-2026 desde este mismo VPS:
  · CTIS  → FUNCIONA (20 resultados de "breast cancer"). Automatizable a diario.
  · ChiCTR → BLOQUEADO. Su WAF corta las IPs de datacenter: devuelve 405 con el texto
    "su acceso ha sido bloqueado por posible amenaza de seguridad", con proxy y sin el.
  · CDE   → NO. Directo no conecta; por el proxy publico sirve una pagina vacia.
Los dos ultimos siguen en el carril de navegador local (tools/radar_navegador.py).

Uso:
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py ctis "breast cancer"
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py chictr "breast cancer"
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py cde 乳腺癌
Salida: JSON por stdout, en el formato que ingiere tools/radar_navegador.py.
"""
import json
import sys

from playwright.sync_api import sync_playwright

ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-extensions", "--no-zygote", "--renderer-process-limit=1"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")


def chictr(pagina, termino):
    pagina.goto("https://www.chictr.org.cn/searchprojEN.html", timeout=60000)
    pagina.wait_for_timeout(1500)
    # el primer input de texto del formulario es "Public title"
    caja = pagina.locator("input[type=text]").first
    caja.click()
    caja.fill(termino)
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(3500)
    filas = pagina.locator("table tr")
    out = []
    for i in range(filas.count()):
        t = filas.nth(i).inner_text()
        if "ChiCTR" not in t:
            continue
        celdas = [c.strip() for c in t.split("\t") if c.strip()]
        rid = ""
        for c in celdas:
            if c.startswith("ChiCTR"):
                rid = c.split()[0]
                break
        if not rid:
            continue
        out.append({"id": rid,
                    "titulo": (celdas[2] if len(celdas) > 2 else "")[:220],
                    "cond": (celdas[3] if len(celdas) > 3 else "")[:80],
                    "fecha": (celdas[4] if len(celdas) > 4 else "")[:12]})
    return out


def cde(pagina, termino):
    pagina.goto("http://www.chinadrugtrials.org.cn/clinicaltrials.prosearch.dhtml",
                timeout=60000)
    pagina.wait_for_timeout(2000)
    caja = pagina.locator("input[type=text]").first
    caja.click()
    caja.fill(termino)          # el buscador ignora fill sin evento: por eso el Enter
    pagina.keyboard.press("Enter")
    pagina.wait_for_timeout(4000)
    texto = pagina.inner_text("body")
    out = []
    for linea in texto.split("\n"):
        if "CTR" not in linea:
            continue
        partes = [p.strip() for p in linea.split("\t")]
        rid = ""
        for p in partes:
            if p.startswith("CTR") and p[3:].isdigit():
                rid = p
                break
        if not rid:
            continue
        out.append({"id": rid,
                    "estado": (partes[2] if len(partes) > 2 else "")[:20],
                    "farmaco": (partes[3] if len(partes) > 3 else "")[:60],
                    "cond": (partes[4] if len(partes) > 4 else "")[:110],
                    "titulo": (partes[5] if len(partes) > 5 else
                               (partes[4] if len(partes) > 4 else ""))[:180]})
    return out


def ctis(pagina, termino):
    """CTIS (UE) SI se deja automatizar desde el VPS: no tiene el WAF que bloquea a ChiCTR."""
    import re as _re
    pagina.goto("https://euclinicaltrials.eu/ctis-public/search", timeout=90000)
    pagina.wait_for_timeout(6000)
    pagina.locator("input[type=text]").first.fill(termino)
    pagina.get_by_role("button", name=_re.compile("^Search$", _re.I)).first.click(timeout=20000)
    pagina.wait_for_timeout(7000)
    texto = pagina.inner_text("body")
    out = []
    for bloque in _re.split(r"\n(?=\d{4}-\d{6}-\d{2}-\d{2})", texto):
        m = _re.match(r"(\d{4}-\d{6}-\d{2}-\d{2})", bloque)
        if not m:
            continue
        rid = m.group(1)
        dec = (_re.search(r"Decision date:\s*([0-9/]+)", bloque) or [None, ""])[1]
        cond = (_re.search(r"Medical condition:\s*([^\n]{0,140})", bloque) or [None, ""])[1]
        loc = (_re.search(r"Location\(s\):\s*([^\n]{0,200})", bloque) or [None, ""])[1]
        paises = "/".join(sorted({x.split(":")[0].strip() for x in loc.split(",")
                                  if x.strip() and not _re.search(
                                      r"pending|recruiting|Authorised|Ongoing", x, _re.I)}))
        out.append({"id": rid, "fecha": dec.strip(),
                    "titulo": bloque.split("\n")[0].replace(rid + " - ", "")[:220],
                    "cond": cond.strip(), "loc": paises[:80]})
    return out


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "uso: radar_cn_vps.py <ctis|chictr|cde> <termino>"}))
        return 2
    fuente, termino = sys.argv[1], sys.argv[2]
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True, args=ARGS)
        ctx = navegador.new_context(user_agent=UA, locale="zh-CN",
                                    viewport={"width": 1400, "height": 1000})
        pagina = ctx.new_page()
        try:
            fn = {"ctis": ctis, "chictr": chictr, "cde": cde}.get(fuente)
            if fn is None:
                print(json.dumps({"error": "fuente desconocida: " + fuente}))
                navegador.close()
                return 2
            items = fn(pagina, termino)
        except Exception as e:                                  # noqa: BLE001
            print(json.dumps({"error": f"{e.__class__.__name__}: {str(e)[:200]}"}))
            navegador.close()
            return 1
        navegador.close()
    print(json.dumps(items, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
