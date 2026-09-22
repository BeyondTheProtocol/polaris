#!/usr/bin/env python3
"""tools/cde_fetch.py — el registro chino de ensayos (CDE/NMPA), sin navegador (20-sep-2026).

POR QUÉ EXISTE: `radar_cn_vps.py` y `radar_navegador.py` llevaban meses diciendo que el CDE
(chinadrugtrials.org.cn, registro OBLIGATORIO de ensayos de fármacos en China) sólo se podía
mirar a mano con un navegador — ni el VPS de Hong Kong lo abre, ni `curl` a pelo. Verificado
HOY, en ESTA sesión, con Python puro (stdlib, sin VPS ni VPN): sí se puede, con dos peticiones:

  1. GET a `/clinicaltrials.index.dhtml` con un `http.cookiejar` — responde 202 y dos cookies
     de sesión (`FSSBBIl1UgzbN7N80T` / `...S`). Sin esto, el POST de abajo da error.
  2. POST a `/clinicaltrials.searchlist.dhtml` con esas cookies, `Referer` de la propia página
     y los campos del buscador avanzado (`indication`, `drugs_name`, `reg_no`, etc.) → 200 con
     una tabla HTML real: registro (CTRxxxxxxxx), estado, fármaco, indicación y título.

CORRECCIÓN a lo que se creía cerrado en el libro de deuda (`cde-secondlevel-sin-test-ni-
implementar`): esa entrada daba por hecho que el campo `secondLevel` es el interruptor que
activa el filtro de indicación (secondLevel=0 → país entero, secondLevel=1 → sólo la
indicación). Probado HOY con las dos sesiones limpias, mismo POST, sólo cambiando ese campo:
`secondLevel=0` CON `indication=乳腺癌` también da 821 registros, igual que `secondLevel=1`.
Y con los campos vacíos, los DOS valores dan el país entero (36.635). O sea: lo que filtra es
que el campo `indication` (o cualquier otro campo de búsqueda) tenga contenido, no el valor de
`secondLevel`. Tampoco se reprodujo la otra hipótesis (que omitir campos hace que el servidor
reutilice la consulta anterior de la sesión): una sesión nueva con SÓLO `indication` ya filtra
bien. Por eso esta tool manda igualmente el formulario con los 18 campos completos (no cuesta
nada y es lo más parecido a lo que manda un navegador real), pero el docstring no repite la
causa-raíz de la deuda porque no se sostuvo al probarla — el hallazgo real es que el buscador
SÍ funciona por POST directo, cosa que antes nadie había probado sin navegador.

FICHA: la URL de detalle usa un id INTERNO de 32 hex (`?id=<hash>`), NO el código CTRxxxxxxxx
(pedir la ficha con el código da "获取信息失败！" — "fallo al obtener la información", verificado
hoy). El id sale de la columna del listado (atributo `id=` de cada fila). La ficha trae
investigador principal, teléfono, email y criterios de inclusión/exclusión (verificado en la
ficha CTR20263478, ensayo de BYL719 en {{DIAGNOSTICO}}).

AVISO OBLIGATORIO — el buscador del CDE NO es de texto completo. Indexa fármaco, indicación,
patrocinador y centro, no el cuerpo del ensayo. Un término clínico específico (p.ej. un gen o
un biomarcador) puede dar CERO resultados sin que eso signifique que no hay ensayos relevantes
en China: puede que el término no aparezca en el campo indexado. Por eso cada búsqueda con 0
resultados imprime ese aviso explícito — nunca "no hay nada", siempre "el buscador no lo
encontró en los campos que indexa".

Uso:
  python3 tools/cde_fetch.py buscar --indication "乳腺癌" [--pagina 1] [--json]
  python3 tools/cde_fetch.py buscar --drugs-name "曲妥珠单抗"
  python3 tools/cde_fetch.py ficha <id de 32 hex> [--json]

  (como módulo)
    sesion = SesionCDE()
    sesion.buscar(indication="乳腺癌", currentpage=1) -> dict
    sesion.ficha("cef5d26806204eca8fc5d04a96a30c2f") -> dict
    parsear_lista(html) / parsear_ficha(html, id) -> puros, sin red (para tests)

Anti-inyección: lo que devuelve el CDE es DATO EXTERNO. Sólo se extraen campos escalares
(registro, estado, fármaco, indicación, título, nombres de investigador, teléfono, email,
texto de criterios) y nada se ejecuta ni se interpola en un prompt.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://www.chinadrugtrials.org.cn"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
TIMEOUT = 30

# Los 18 campos del formulario de búsqueda avanzada. Se mandan TODOS, vacíos los que no se
# usan: es lo que manda un navegador real y evita cualquier comportamiento de sesión raro.
CAMPOS = [
    "keywords", "reg_no", "indication", "case_no", "drugs_name", "drugs_type", "appliers",
    "communities", "researchers", "agencies", "state", "secondLevel", "currentpage", "rule",
    "sort", "ckm_index", "id", "sort2",
]

ERROR_FICHA = "获取信息失败"  # lo que devuelve la ficha cuando el id no es el hash interno


def _decodificar(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def texto_visible(html: str) -> str:
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    t = re.sub(r"(?is)<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


class SesionCDE:
    """Una sesión = las dos cookies de `clinicaltrials.index.dhtml`. Se reutiliza entre
    llamadas (buscar/ficha) para no repetir el GET inicial en cada petición."""

    def __init__(self):
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj))
        self._abierta = False

    def _abrir(self):
        if self._abierta:
            return
        req = urllib.request.Request(
            BASE + "/clinicaltrials.index.dhtml", headers={"User-Agent": UA})
        self._opener.open(req, timeout=TIMEOUT)
        self._abierta = True

    def _post(self, path: str, form: dict, referer: str) -> str:
        self._abrir()
        data = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            BASE + path, data=data, method="POST",
            headers={
                "User-Agent": UA, "Referer": referer,
                "Content-Type": "application/x-www-form-urlencoded",
            })
        with self._opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read()
        return _decodificar(raw)

    def _get(self, path: str, referer: str) -> str:
        self._abrir()
        req = urllib.request.Request(
            BASE + path, headers={"User-Agent": UA, "Referer": referer})
        with self._opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read()
        return _decodificar(raw)

    def buscar(self, *, indication="", keywords="", reg_no="", case_no="", drugs_name="",
               drugs_type="", appliers="", communities="", researchers="", agencies="",
               state="", currentpage=1, second_level=1) -> dict:
        form = {c: "" for c in CAMPOS}
        form.update({
            "indication": indication, "keywords": keywords, "reg_no": reg_no,
            "case_no": case_no, "drugs_name": drugs_name, "drugs_type": drugs_type,
            "appliers": appliers, "communities": communities, "researchers": researchers,
            "agencies": agencies, "state": state,
            "secondLevel": str(second_level), "currentpage": str(currentpage),
        })
        html = self._post(
            "/clinicaltrials.searchlist.dhtml", form,
            referer=BASE + "/clinicaltrials.index.dhtml")
        return parsear_lista(html)

    def ficha(self, id_interno: str) -> dict:
        html = self._get(
            "/clinicaltrials.searchlistdetail.dhtml?id=" + urllib.parse.quote(id_interno),
            referer=BASE + "/clinicaltrials.searchlist.dhtml")
        return parsear_ficha(html, id_interno)


# --- parsing puro: sin red, para que los tests no dependan de ella ---------------------------

_RE_FILA = re.compile(r'<tr style=" color:#535353">(.*?)</tr>', re.S)
_RE_ID = re.compile(r'id="([a-f0-9]{32})"')
_RE_A = re.compile(r"<a[^>]*>(.*?)</a>", re.S)
_RE_PAGINAS = re.compile(r"共\s*<i>(\d+)</i>\s*页")
_RE_TOTAL = re.compile(r"共\s*<i>(\d+)</i>\s*条")
_RE_PAGINA_ACTUAL = re.compile(r"当前第\s*<i>(\d+)</i>\s*页")


def parsear_lista(html: str) -> dict:
    """(dict) total de registros, páginas, página actual y la lista de la página pedida.

    Sin red: opera sobre el HTML ya descargado. `total == 0` no es un error: es el CDE
    diciendo que no encontró nada en los campos que indexa (ver aviso del docstring).
    """
    m_total = _RE_TOTAL.search(html)
    total = int(m_total.group(1)) if m_total else 0
    m_pag = _RE_PAGINAS.search(html)
    paginas = int(m_pag.group(1)) if m_pag else 0
    m_act = _RE_PAGINA_ACTUAL.search(html)
    pagina_actual = int(m_act.group(1)) if m_act else 0

    registros = []
    for bloque in _RE_FILA.findall(html):
        m_id = _RE_ID.search(bloque)
        if not m_id:
            continue
        enlaces = [texto_visible(a) for a in _RE_A.findall(bloque)]
        # orden observado: registro, estado, fármaco, indicación, título
        while len(enlaces) < 5:
            enlaces.append("")
        registros.append({
            "id": m_id.group(1),
            "reg_no": enlaces[0],
            "estado": enlaces[1],
            "farmaco": enlaces[2],
            "indicacion": enlaces[3],
            "titulo": enlaces[4],
        })

    aviso = ""
    if total == 0:
        aviso = ("cero resultados: el CDE no es de texto completo (indexa fármaco, "
                 "indicación, patrocinador y centro, no el cuerpo del ensayo) — esto NO "
                 "prueba que no haya ensayos relevantes en China.")

    return {
        "total": total, "paginas": paginas, "pagina_actual": pagina_actual,
        "registros": registros, "aviso": aviso,
    }


_RE_LABEL_TD = lambda etiqueta: re.compile(  # noqa: E731
    re.escape(etiqueta) + r"\s*</th>\s*<td[^>]*>(.*?)</td>", re.S)


def parsear_ficha(html: str, id_interno: str = "") -> dict:
    """(dict) puro sobre el HTML de una ficha ya descargada. `ok=False` cuando el id no era
    el hash interno (el CDE devuelve "获取信息失败" — no hay nada más que rascar en esa página)."""
    visible = texto_visible(html)
    if ERROR_FICHA in visible:
        return {"ok": False, "id": id_interno, "motivo": "id no es el hash interno del CDE"}

    def campo(etiqueta):
        m = _RE_LABEL_TD(etiqueta).search(html)
        return texto_visible(m.group(1)) if m else ""

    reg_no = campo("登记号")
    titulo = campo("试验通俗题目")

    # investigador principal: acota la búsqueda al bloque de "主要研究者信息" para no
    # confundir sus 姓名/电话/Email con los de cualquier otra tabla de la página.
    i = html.find("主要研究者信息")
    bloque_pi = html[i:i + 2500] if i != -1 else ""
    nombre_pi = texto_visible(_RE_LABEL_TD("姓名").search(bloque_pi).group(1)) \
        if _RE_LABEL_TD("姓名").search(bloque_pi) else ""
    telefono = texto_visible(_RE_LABEL_TD("电话").search(bloque_pi).group(1)) \
        if _RE_LABEL_TD("电话").search(bloque_pi) else ""
    email = texto_visible(_RE_LABEL_TD("Email").search(bloque_pi).group(1)) \
        if _RE_LABEL_TD("Email").search(bloque_pi) else ""

    def criterios(etiqueta):
        m = re.search(re.escape(etiqueta) + r".*?<table class=\"subSearch\">(.*?)</table>",
                       html, re.S)
        if not m:
            return []
        celdas = re.findall(r'width="90%"[^>]*>(.*?)</td>', m.group(1), re.S)
        return [texto_visible(c) for c in celdas if texto_visible(c)]

    return {
        "ok": True, "id": id_interno, "reg_no": reg_no, "titulo": titulo,
        "investigador_principal": nombre_pi, "telefono": telefono, "email": email,
        "criterios_inclusion": criterios("入选标准"),
        "criterios_exclusion": criterios("排除标准"),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buscar")
    b.add_argument("--indication", default="")
    b.add_argument("--keywords", default="")
    b.add_argument("--reg-no", default="")
    b.add_argument("--drugs-name", default="")
    b.add_argument("--appliers", default="")
    b.add_argument("--researchers", default="")
    b.add_argument("--agencies", default="")
    b.add_argument("--pagina", type=int, default=1)
    b.add_argument("--json", action="store_true")

    f = sub.add_parser("ficha")
    f.add_argument("id")
    f.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    sesion = SesionCDE()

    if args.cmd == "buscar":
        r = sesion.buscar(
            indication=args.indication, keywords=args.keywords, reg_no=args.reg_no,
            drugs_name=args.drugs_name, appliers=args.appliers,
            researchers=args.researchers, agencies=args.agencies, currentpage=args.pagina)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"{r['total']} registros · página {r['pagina_actual']}/{r['paginas']}")
            if r["aviso"]:
                print("⚠️  " + r["aviso"])
            for reg in r["registros"]:
                print(f"  {reg['reg_no']} · {reg['estado']} · {reg['farmaco']} · "
                      f"{reg['indicacion'][:40]} [id={reg['id']}]")
        return 0

    if args.cmd == "ficha":
        r = sesion.ficha(args.id)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        elif not r["ok"]:
            print("NO SE PUDO LEER: " + r["motivo"], file=sys.stderr)
            return 1
        else:
            print(f"{r['reg_no']} · {r['titulo']}")
            print(f"IP: {r['investigador_principal']} · tel {r['telefono']} · {r['email']}")
            print(f"Inclusión: {len(r['criterios_inclusion'])} criterios")
            print(f"Exclusión: {len(r['criterios_exclusion'])} criterios")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
