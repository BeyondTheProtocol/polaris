#!/usr/bin/env python3
"""tools/cn.py — escape libre: buscar CUALQUIER cosa en internet chino (20-sep-2026).

POR QUÉ EXISTE: `tools/cde_fetch.py` (hermano de este módulo) sólo mira el registro de
ensayos. {{TITULAR}} pidió, literal, «quiero tener escape libre de buscar cualquier cosa allí, no
solo ensayos» — foros, noticias, papers, lo que sea, igual que se investiga en Europa.

MOTORES — medido HOY, en esta sesión, con Python puro (stdlib) desde España, sin proxy ni VPN,
consulta de prueba «乳腺癌 新抗原 疫苗»:

  · **so.com** (Qihoo 360) — funciona, pero necesita sesión: un `urlopen` suelto entra en un
    bucle de redirección infinita (302 en bucle). Con `http.cookiejar` (igual que `cde_fetch`)
    responde 200 con resultados reales y relevantes. Esto CORRIGE lo que se creía: "200,
    453 KB" a secas no bastaba, hacía falta la cookie para no quedarse en el 302.
  · **cn.bing.com** — responde 200 y el `<title>` refleja la consulta, PERO el cuerpo puede
    traer una página señuelo: en dos pruebas de hoy devolvió resultados sobre un foro ruso de
    gestor de dispositivos y un tutorial de `sort()` en Python — nada que ver con la consulta,
    aunque el término buscado sí aparecía sueltos en la página (probable trampa anti-scraping:
    HTML estático con contenido de relleno). Por eso esta tool NO se fía de "200 + el término
    aparece en algún sitio": cada resultado se compara contra los términos de la consulta y el
    que no comparte ninguno se descarta, motor por motor. Si Bing no aporta nada relevante a
    una consulta, se cuenta como "sin resultados relevantes", nunca como error silencioso.
  · **medsci.cn** (portal médico chino) — funciona sin sesión especial, resultados reales y
    relevantes (confirmado con contenido real sobre vacunas de neoantígeno).
  · **baidu.com**, **sogou.com**, **zhihu.com** — quedan FUERA (según lo ya diagnosticado:
    reto anti-bot o 403); no se han vuelto a probar hoy, así que si algo cambió `fuentes` lo
    dirá la próxima vez que se corra, porque comprueba en vivo.

DEGRADADO HONESTO: si un motor cae (red, HTTP, parseo) se anota el motivo y se sigue con los
otros dos. Si los TRES fallan, o si ninguno de los tres trae un resultado relevante, `buscar()`
devuelve `ok=False` con el motivo — nunca una lista vacía disfrazada de "no hay resultados",
que es justo la falsa certeza que el muro prohíbe.

TRADUCCIÓN: si la consulta no lleva caracteres chinos, se intenta traducir con el modelo LOCAL
(`tools/local.py`, egress cero) antes de mandarla a un motor externo — nunca con un LLM de
fuera, porque puede llevar contenido sensible. Si el local no responde (no está levantado, o
Ollama no tiene el modelo), se avisa explícitamente y se busca con la consulta tal cual, en el
idioma original: mejor un resultado pobre que fingir que se tradujo.

MURO: la consulta (antes de traducir) pasa por `borde.egress_cientifico()` — fail-closed. Sólo
términos científicos genéricos; nunca el nombre de {{TITULAR}}, datos de contacto o una huella
genómica específica. Todo lo que vuelve de un motor es DATO EXTERNO: los títulos y fragmentos
se sanean (sin etiquetas HTML, una línea) y no se interpolan en ningún prompt.

Uso:
  python3 tools/cn.py buscar "<consulta en chino o español>" [--json] [--max N]
  python3 tools/cn.py abrir <url> [--requiere "texto"]
  python3 tools/cn.py fuentes [--json]

  (como módulo)  buscar(consulta) -> dict · abrir(url) -> ver tools/cn_fetch.fetch
"""
from __future__ import annotations

import argparse
import html as _html_mod
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import borde  # noqa: E402
import cn_fetch  # noqa: E402
import local  # noqa: E402

UA = cn_fetch.UA
TIMEOUT = 20


def _opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _decodificar(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _get(opener, url, timeout=TIMEOUT) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
    return _decodificar(raw)


def _limpiar(fragmento: str) -> str:
    """Título o fragmento en UNA línea, sin etiquetas ni markdown — es dato externo."""
    t = re.sub(r"(?is)<[^>]+>", " ", fragmento or "")
    t = _html_mod.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _es_chino(texto: str) -> bool:
    return any("一" <= c <= "鿿" for c in texto)


def _relevante(hit: dict, terminos: list) -> bool:
    if not terminos:
        return True
    bajo = (hit.get("titulo", "") + " " + hit.get("snippet", "")).lower()
    return any(t.lower() in bajo for t in terminos if t.strip())


# --- motores: cada uno (query, opener) -> lista de {titulo, url, snippet} --------------------

def v_so(query: str, opener) -> list:
    url = "https://www.so.com/s?q=" + urllib.parse.quote(query)
    html_txt = _get(opener, url)
    hits = []
    for bloque in html_txt.split('<li class="res-list">')[1:]:
        m = re.search(r'data-mdurl="([^"]*)"[^>]*>(.*?)</a></h3>', bloque, re.S)
        if not m:
            continue
        md = re.search(r'<p class="res-desc">(.*?)</p>', bloque, re.S)
        hits.append({
            "titulo": _limpiar(m.group(2)),
            "url": _html_mod.unescape(m.group(1)),
            "snippet": _limpiar(md.group(1)) if md else "",
        })
    return hits


def v_bing(query: str, opener) -> list:
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
    html_txt = _get(opener, url)
    hits = []
    for bloque in html_txt.split('<li class="b_algo"')[1:]:
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a></h2>', bloque, re.S)
        if not m:
            continue
        sp = re.search(r'<p class="b_lineclamp2"[^>]*>(.*?)</p>', bloque, re.S)
        hits.append({
            "titulo": _limpiar(m.group(2)),
            "url": _html_mod.unescape(m.group(1)),
            "snippet": _limpiar(sp.group(1)) if sp else "",
        })
    return hits


def v_medsci(query: str, opener) -> list:
    url = "https://www.medsci.cn/search?q=" + urllib.parse.quote(query)
    html_txt = _get(opener, url)
    hits = []
    for m in re.finditer(
            r'<h2><a target="_blank" href="([^"]*)"[^>]*>(.*?)</a></h2>'
            r'(?:\s*<p class="text-justify">(.*?)</p>)?', html_txt, re.S):
        url_res = m.group(1)
        if url_res.startswith("/"):
            url_res = "https://www.medsci.cn" + url_res
        hits.append({
            "titulo": _limpiar(m.group(2)),
            "url": _html_mod.unescape(url_res),
            "snippet": _limpiar(m.group(3) or ""),
        })
    return hits


MOTORES = [("so.com", v_so), ("cn.bing.com", v_bing), ("medsci.cn", v_medsci)]

# Puertas ya diagnosticadas como cerradas (para `fuentes`); no se llevan en `buscar()`.
CERRADAS = {
    "baidu.com": "https://www.baidu.com/s?wd={q}",
    "sogou.com": "https://www.sogou.com/web?query={q}",
    "zhihu.com": "https://www.zhihu.com/search?q={q}",
}


def _traducir_si_hace_falta(consulta: str) -> tuple:
    """(consulta_a_usar, traducido, aviso). Nunca lanza: si el local no responde, avisa y
    devuelve la consulta original."""
    if _es_chino(consulta):
        return consulta, False, ""
    prompt = (
        "Traduce estos terminos de busqueda ingeniera al chino simplificado, como palabras "
        "clave SUELTAS para un buscador, NO como una frase pegada. Separalas con UN espacio "
        "entre cada una. Ejemplo: la entrada \"breast cancer immunotherapy\" debe dar "
        "exactamente \"乳腺癌 免疫治疗\" (con espacio), nunca \"乳腺癌免疫治疗\" (sin espacio).\n"
        "Responde SOLO las palabras clave en chino, sin pinyin, sin explicar nada, sin "
        "comillas.\n\nTERMINOS:\n<<<\n%s\n>>>" % consulta
    )
    traduccion = local.responder(prompt, fallback="")
    if traduccion.strip() and _es_chino(traduccion):
        return traduccion.strip(), True, ""
    return consulta, False, (
        "el modelo local no tradujo (no disponible o sin chino en la respuesta): "
        "se busca con la consulta tal cual, en el idioma original")


def buscar(consulta: str, *, max_resultados: int = 20, opener=None) -> dict:
    """Busca `consulta` en los 3 motores que funcionan hoy, fusiona y deduplica por URL.

    `ok=False` cuando el muro bloquea la consulta, o cuando los tres motores caen, o cuando
    ninguno trae un resultado relevante — nunca una lista vacía silenciosa.
    """
    ok_muro, motivo_muro = borde.egress_cientifico(consulta, destino="cn-buscador")
    if not ok_muro:
        return {"ok": False, "motivo": "muro: " + motivo_muro, "resultados": [], "motores": {}}

    consulta_usada, traducido, aviso_traduccion = _traducir_si_hace_falta(consulta)
    opener = opener or _opener()

    motores_estado = {}
    resultados = []
    vistos = set()
    terminos = consulta_usada.split()
    for nombre, fn in MOTORES:
        try:
            hits = fn(consulta_usada, opener)
        except Exception as e:  # noqa: BLE001 - un motor caído no corta los demás
            motores_estado[nombre] = "caído: %s %s" % (type(e).__name__, e)
            continue
        relevantes = [h for h in hits if _relevante(h, terminos)]
        motores_estado[nombre] = (
            "%d/%d relevantes" % (len(relevantes), len(hits)) if hits else "sin resultados")
        for h in relevantes:
            if h["url"] in vistos:
                continue
            vistos.add(h["url"])
            h2 = dict(h, motor=nombre)
            resultados.append(h2)

    caidos = [n for n, s in motores_estado.items() if s.startswith("caído")]
    if len(caidos) == len(MOTORES):
        return {
            "ok": False,
            "motivo": "los 3 motores cayeron: " + "; ".join(
                "%s (%s)" % (n, motores_estado[n]) for n in caidos),
            "resultados": [], "motores": motores_estado,
            "consulta_usada": consulta_usada, "traducido": traducido,
            "aviso_traduccion": aviso_traduccion,
        }
    if not resultados:
        return {
            "ok": False,
            "motivo": "ningún motor trajo un resultado relevante para esta consulta "
                      "(no es lo mismo que 'no hay nada': puede que el término no case con "
                      "el índice de estos buscadores)",
            "resultados": [], "motores": motores_estado,
            "consulta_usada": consulta_usada, "traducido": traducido,
            "aviso_traduccion": aviso_traduccion,
        }

    return {
        "ok": True, "consulta_original": consulta, "consulta_usada": consulta_usada,
        "traducido": traducido, "aviso_traduccion": aviso_traduccion,
        "motores": motores_estado, "resultados": resultados[:max_resultados],
    }


def abrir(url: str, requiere: str = "") -> dict:
    """Envoltorio fino sobre `cn_fetch.fetch`: valida que lo que vuelve es contenido real,
    no un reto anti-bot. `--requiere` exige además una cadena concreta en la página."""
    via, texto, errores = cn_fetch.fetch(url, requiere=requiere)
    return {"ok": bool(via), "via": via, "texto": texto, "errores": errores}


def fuentes(opener=None) -> dict:
    """Comprueba EN VIVO qué puertas están abiertas hoy (los 3 motores + las 3 ya cerradas),
    para que dentro de un mes se sepa si algo cambió."""
    opener = opener or _opener()
    estado = {}
    for nombre, fn in MOTORES:
        try:
            hits = fn("乳腺癌", opener)
            estado[nombre] = "abierta (%d resultados de prueba)" % len(hits) if hits \
                else "abierta pero sin resultados de prueba"
        except Exception as e:  # noqa: BLE001
            estado[nombre] = "cerrada: %s %s" % (type(e).__name__, e)
    for nombre, plantilla in CERRADAS.items():
        url = plantilla.format(q=urllib.parse.quote("乳腺癌"))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with opener.open(req, timeout=TIMEOUT) as r:
                cuerpo = r.read()
            estado[nombre] = "responde %s, %d B (revisar a mano si ya sirve)" % (
                r.status, len(cuerpo))
        except Exception as e:  # noqa: BLE001
            estado[nombre] = "cerrada: %s %s" % (type(e).__name__, e)
    return estado


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buscar")
    b.add_argument("consulta")
    b.add_argument("--json", action="store_true")
    b.add_argument("--max", type=int, default=20)

    a = sub.add_parser("abrir")
    a.add_argument("url")
    a.add_argument("--requiere", default="")

    sub.add_parser("fuentes")

    args = p.parse_args(argv)

    if args.cmd == "buscar":
        r = buscar(args.consulta, max_resultados=args.max)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0 if r["ok"] else 1
        if not r["ok"]:
            print("NO SE PUDO BUSCAR: " + r["motivo"], file=sys.stderr)
            return 1
        if r["traducido"]:
            print(f"[traducido a: {r['consulta_usada']}]")
        if r["aviso_traduccion"]:
            print("⚠️  " + r["aviso_traduccion"])
        print("motores: " + " · ".join(f"{n}={s}" for n, s in r["motores"].items()))
        for hit in r["resultados"]:
            print(f"\n[{hit['motor']}] {hit['titulo']}\n  {hit['url']}\n  {hit['snippet']}")
        return 0

    if args.cmd == "abrir":
        r = abrir(args.url, requiere=args.requiere)
        if not r["ok"]:
            print("NINGUNA VÍA FUNCIONÓ", file=sys.stderr)
            for e in r["errores"]:
                print("  ·", e, file=sys.stderr)
            return 1
        print(f"[vía: {r['via']}]")
        print(r["texto"][:200000])
        return 0

    if args.cmd == "fuentes":
        for nombre, estado in fuentes().items():
            print(f"  {nombre:20s} {estado}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
