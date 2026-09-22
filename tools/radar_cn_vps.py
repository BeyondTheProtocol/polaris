#!/usr/bin/env python3
"""radar_cn_vps.py — barrido de los registros chinos desde el VPS de Hong Kong (18-sep-2026).

Vive en el servidor de Alibaba (/opt/radar_cn_vps.py) y se ejecuta con el venv de
/opt/radar-venv. Usa Chromium headless porque ChiCTR y el CDE no responden a curl:
  · ChiCTR devuelve 405 a cualquier GET/POST directo, con cookie de sesion o sin ella.
  · El CDE pinta su tabla por JavaScript y su buscador no reacciona a un POST.

VERIFICADO desde este mismo VPS (18-sep y 20-sep-2026):
  · CTIS  → FUNCIONA (20 resultados de "breast cancer"). Automatizable a diario.
  · ICTRP → FUNCIONA (20-sep). El portal de la OMS (trialsearch.who.int) es el ESPEJO de ChiCTR:
    importa su fichero (ultimo, 14-sep-2026) y abre HTTP 200 desde el VPS y desde Espana. Su
    buscador avanzado (AdvSearch.aspx) es ASP.NET con postback; el boton de pais es un
    UpdatePanel (postback parcial, no navega), por eso se espera al elemento y no a la
    navegacion. OJO, medido el 20-sep: el campo "intervencion" NO casa con los registros ChiCTR
    ("breast cancer" x "vaccine" x China = 0 aunque existan), asi que la pasada que vale es
    condicion x pais x VENTANA DE FECHA DE REGISTRO, y el filtro por diana se hace aqui con
    PALABRAS_PERFIL sobre el titulo. Los pares condicion x intervencion se corren ademas como
    segunda pasada: casan con NCT/EUCTR que tienen centros en China.
  · ChiCTR → BLOQUEADO. Su WAF corta las IPs de datacenter: devuelve 405 con el texto
    "su acceso ha sido bloqueado por posible amenaza de seguridad", con proxy y sin el.
    Se cubre por ICTRP; la ficha individual se abre en Trial2.aspx?TrialID=ChiCTR<numero>.
  · CDE   → CORREGIDO 20-sep-2026: esto decia "NO, directo no conecta" y ya no es verdad. Desde
    el VPS por curl seguia sin conectar la ultima vez que se probo, pero verificado HOY, en
    sesion, con Python puro (sin VPS ni navegador, dos peticiones: GET cookies + POST del
    formulario de busqueda) que SI hay una via directa: `tools/cde_fetch.py` ya la implementa
    y trae test (`tests/test_cde_fetch.py`). Esa es la via a usar primero; el carril de
    navegador (tools/radar_navegador.py) queda de respaldo, ya no como unica opcion.
  · ChinaXiv → FUERA del barrido diario (20-sep): 46.276 items de todas las disciplinas, ~2.128
    en 2026 dominados por fisica y psicologia, su buscador ignora el parametro GET y Europe PMC
    solo indexa 13 preprints suyos. Rendimiento oncologico ~0. Los preprints chinos que importan
    van a medRxiv/bioRxiv/Research Square, que Europe PMC si indexa (tema cn-preprints).

Uso:
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py ctis "breast cancer"
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py ictrp perfil --dias 14     # la bateria entera
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py ictrp "breast cancer" --interv "TROP2"
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py chictr "breast cancer"      # bloqueado, queda por si cambia
  /opt/radar-venv/bin/python /opt/radar_cn_vps.py cde perfil                  # todos los TERMINOS_CDE
Salida: JSON por stdout, en el formato que ingiere tools/radar_navegador.py.

Anti-inyeccion: lo que devuelve cada registro es DATO EXTERNO. Solo se extraen campos escalares
(id, titulo, estado, fecha, resultados) y el titulo se recorta. Nada se ejecuta ni se interpola.
"""
import json
import re
import sys

ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-extensions", "--no-zygote", "--renderer-process-limit=1"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")

# --- CDE por PERFIL, no por 乳腺癌 generico (20-sep-2026) ------------------------------------
# Por buscar solo "{{DIAGNOSTICO}}" la cola trajo un ensayo ALK de pulmon. Cada termino apunta a
# una diana o a un codigo de molecula de su perfil (luminal B HR+/HER2-, NE focal, FGF19/FGFR4,
# CCND1/FGFR1, CDK2, {{DIANA2}}, TROP2, TIL). Los codigos son nombres publicos de compuestos, no PII.
TERMINOS_CDE = [
    "乳腺癌 新抗原 疫苗",                  # neoantigeno + vacuna en mama
    "乳腺癌 个体化 mRNA 疫苗",             # vacuna mRNA individualizada
    "肿瘤新生抗原 实体瘤",                 # neoantigenos en tumor solido (cestas)
    "激素受体阳性 HER2阴性 CDK4/6抑制剂治疗失败",  # HR+/HER2- tras fallo a CDK4/6
    "乳腺癌 CDK2",
    "乳腺癌 FGFR",
    "FGFR4 FGF19",
    "乳腺 神经内分泌癌",                   # carcinoma neuroendocrino de mama
    "{{DIANA2}} 抗体偶联",                       # ADC anti-{{DIANA2}}
    "TROP2 激素受体阳性",                  # ADC anti-TROP2 en HR+
    "乳腺癌 TIL 细胞治疗",
    # codigos de molecula (publicos): TROP2-ADC, CDK2/4/6, CDK4/6, {{DIANA2}}-ADC, FGFR4, B7-H3, TROP2
    "SKB264", "TQB3616", "TQB3126", "SHR6390", "ZL-1310", "BB102", "SSGJ-612", "SI-B036",
]

# --- ICTRP: pares condicion x intervencion (20-sep-2026) --------------------------------------
ICTRP_CONDICION = "breast cancer"
ICTRP_PAIS = "China"
ICTRP_INTERVENCIONES = [
    "neoantigen", "personalized vaccine", "mRNA vaccine", "tumor infiltrating lymphocyte",
    "CDK2", "FGFR", "{{DIANA2}}", "TROP2", "antibody-drug conjugate",
]
# Filtro LOCAL sobre el titulo para la pasada condicion x pais x fecha (donde no hay intervencion).
# Sin esto la pasada por fecha trae cribado, lactancia y cirugia reconstructiva.
PALABRAS_PERFIL = re.compile(
    r"neoantigen|vaccine|mRNA|tumou?r[- ]infiltrating|\bTIL\b|CDK2|CDK4/6|FGFR|FGF19|{{DIANA2}}|TROP-?2|"
    r"antibody[- ]drug|\bADC\b|conjugate|bispecific|neuroendocrine|hormone receptor|HR[- ]positive|"
    r"HR\+|HER2[- ]negative|HER2[- ]low|luminal|CAR[- ]?T|cell therapy|radioligand|177Lu|"
    r"oligometasta|\bSERD\b|estrogen receptor|B7-H3|HER3|ctDNA|residual disease",
    re.I)

_P = "#ctl00_ContentPlaceHolder1_"


def parse_filas_ictrp(filas_texto):
    """Filas de GridViewSearch (inner_text con tabulaciones) → lista de dicts. Pura, sin red.
    Columnas medidas el 20-sep-2026: Recruitment status | Prospective Registration | Main ID |
    (vacio) | Public Title | Date of Registration | Results available."""
    out, vistos = [], set()
    for t in filas_texto:
        if not isinstance(t, str) or "\t" not in t:
            continue
        celdas = [c.replace("\xa0", "").strip() for c in t.split("\t")]
        rid = ""
        for c in celdas:
            m = re.match(r"^(ChiCTR\d{10}|NCT\d{8}|EUCTR[\w-]+|CTRI/[\w/]+|jRCT\w+|KCT\d+|"
                         r"ISRCTN\d+|DRKS\d+|ACTRN\d+|IRCT\w+|JPRN-\w+|TCTR\d+|PACTR\d+|"
                         r"RBR-\w+|NL\d+|SLCTR/[\w/]+|LBCTR\d+|ITMCTR\w+|CTRN\w+|PER-\d+-\d+|"
                         r"RPCEC\d+|REPEC\w+|CRIS\w+|U1111-\d{4}-\d{4})$", c)
            if m:
                rid = m.group(1)
                break
        if not rid or rid in vistos:
            continue
        vistos.add(rid)
        no_vacias = [c for c in celdas if c]
        estado = no_vacias[0] if no_vacias and no_vacias[0] != rid else ""
        titulo = ""
        fecha = ""
        res = ""
        despues = celdas[celdas.index(rid) + 1:] if rid in celdas else []
        for c in despues:
            if not c:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", c):
                fecha = c
            elif not titulo:
                titulo = c
            elif fecha:
                res = c          # lo que venga tras la fecha es la columna "Results available"
        out.append({"id": rid, "estado": estado[:20], "titulo": titulo[:220], "fecha": fecha[:10],
                    "resultados": bool(res and res.lower() not in ("no", "-", "")),
                    "registro": "ChiCTR" if rid.startswith("ChiCTR") else re.sub(r"[\d/].*", "", rid)})
    return out


def _ictrp_buscar(pagina, condicion, intervencion="", pais=ICTRP_PAIS, desde="", hasta=""):
    """Una busqueda en AdvSearch.aspx. Devuelve (filas_parseadas, total_declarado, truncado)."""
    pagina.goto("https://trialsearch.who.int/AdvSearch.aspx", timeout=60000,
                wait_until="domcontentloaded")
    pagina.fill(_P + "txtCondition", condicion)
    pagina.fill(_P + "txtIntervention", intervencion or "")
    if pais:
        pagina.select_option(_P + "lstCountries", pais)
        pagina.click(_P + "butAdd")               # UpdatePanel: postback parcial, no navega
        pagina.wait_for_selector(_P + "lstCountriesSelected option[value='%s']" % pais)
    if desde:
        pagina.fill(_P + "txtDateStart", desde)   # dd/mm/yyyy
    if hasta:
        pagina.fill(_P + "txtDateEnd", hasta)
    pagina.click(_P + "btnSearch")
    pagina.wait_for_function("document.body.innerText.includes('records for') || "
                             "document.body.innerText.includes('No results')")
    pagina.wait_for_timeout(600)
    texto = pagina.inner_text("body")
    m = re.search(r"(\d+) records for (\d+) trials", texto)
    total = int(m.group(2)) if m else 0
    if total == 0:
        return [], 0, False
    if total > 10:
        # 100 por pagina (el maximo del selector); si aun asi hay mas, se DICE (truncado)
        pagina.select_option(_P + "ddlPageSize", "100")
        pagina.wait_for_timeout(2500)
    filas = pagina.locator(_P + "GridViewSearch tr")
    textos = [filas.nth(i).inner_text() for i in range(filas.count())]
    return parse_filas_ictrp(textos), total, total > 100


def _fecha_ddmmyyyy(d):
    return d.strftime("%d/%m/%Y")


def ictrp(pagina, termino, interv="", dias=None):
    """termino='perfil' → bateria completa; si no, una busqueda condicion(=termino) x interv."""
    from datetime import date, timedelta
    if termino != "perfil":
        desde = _fecha_ddmmyyyy(date.today() - timedelta(days=int(dias))) if dias else ""
        hasta = _fecha_ddmmyyyy(date.today()) if dias else ""
        items, total, trunc = _ictrp_buscar(pagina, termino, interv, desde=desde, hasta=hasta)
        for it in items:
            it["terminos"] = f"{termino} x {interv or '*'}"
        if trunc:
            items.append({"error": f"ICTRP declara {total} ensayos y solo se leyeron 100"})
        return items
    # --- bateria de perfil ---
    dias = int(dias or 14)
    hoy = date.today()
    salida, vistos, avisos = [], set(), []
    # 1) condicion x pais x ventana de fecha de registro, filtrado LOCAL por PALABRAS_PERFIL
    items, total, trunc = _ictrp_buscar(pagina, ICTRP_CONDICION, "", desde=_fecha_ddmmyyyy(
        hoy - timedelta(days=dias)), hasta=_fecha_ddmmyyyy(hoy))
    if trunc:
        avisos.append(f"ventana {dias}d: ICTRP declara {total} y solo se leyeron 100")
    for it in items:
        if PALABRAS_PERFIL.search(it["titulo"] or ""):
            it["terminos"] = f"{ICTRP_CONDICION} x {ICTRP_PAIS} x {dias}d"
            salida.append(it)
            vistos.add(it["id"])
    # 2) los pares condicion x intervencion (sin fecha: casan con NCT/EUCTR con centros en China)
    for interv in ICTRP_INTERVENCIONES:
        try:
            items, total, trunc = _ictrp_buscar(pagina, ICTRP_CONDICION, interv)
        except Exception as e:                              # noqa: BLE001
            avisos.append(f"{interv}: {e.__class__.__name__}")
            continue
        if trunc:
            avisos.append(f"{interv}: ICTRP declara {total} y solo se leyeron 100")
        for it in items:
            if it["id"] in vistos:
                continue
            vistos.add(it["id"])
            it["terminos"] = f"{ICTRP_CONDICION} x {interv}"
            salida.append(it)
    for a in avisos:
        salida.append({"error": a})
    return salida


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


def _cde_una(pagina, termino):
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
                               (partes[4] if len(partes) > 4 else ""))[:180],
                    "terminos": termino})
    return out


def cde(pagina, termino):
    """termino='perfil' → todos los TERMINOS_CDE, deduplicados por CTR."""
    terminos = TERMINOS_CDE if termino == "perfil" else [termino]
    out, vistos = [], set()
    for t in terminos:
        for it in _cde_una(pagina, t):
            if it["id"] in vistos:
                continue
            vistos.add(it["id"])
            out.append(it)
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


def _opcion(argv, nombre, defecto=None):
    if nombre in argv:
        i = argv.index(nombre)
        if i + 1 < len(argv):
            return argv[i + 1]
    return defecto


def main():
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(json.dumps({"error": "uso: radar_cn_vps.py <ctis|ictrp|chictr|cde> <termino|perfil> "
                                   "[--interv X] [--dias N]"}))
        return 2
    fuente, termino = argv[0], argv[1]
    interv = _opcion(argv, "--interv", "")
    dias = _opcion(argv, "--dias")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True, args=ARGS)
        ctx = navegador.new_context(user_agent=UA, locale="zh-CN",
                                    viewport={"width": 1400, "height": 1000})
        pagina = ctx.new_page()
        pagina.set_default_timeout(45000)
        try:
            if fuente == "ictrp":
                items = ictrp(pagina, termino, interv=interv, dias=dias)
            else:
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
