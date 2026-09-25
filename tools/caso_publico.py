#!/usr/bin/env python3
"""caso_publico.py — construye el panel clínico público de helptitular.com/datos.

POR QUÉ EXISTE (24-sep-2026, plan `stateful-moseying-wren`, aprobado por {{TITULAR}}). Lo que un
oncólogo o un laboratorio necesita para evaluar el caso existía, pero partido en tres estados:
analíticas ya estructuradas (`tools/state/biomarcadores/timeline.json`), cifras en notas y prosa
en `ESTADO-ACTUAL.md`. Nadie de fuera podía hacerse una idea sin leerse veinte documentos.
Directriz de {{TITULAR}} (24-sep): «incluye todo lo que nos acerque a NED y que ayude a la gente que
está ayudándonos; nos importa poco la privacidad». Lo único que sigue fuera: claves
administrativas, nombres de terceros, datos de su padre y el material de la denuncia.

ENTRADAS
  · `00_FUENTE-DE-VERDAD/01 · Tratamiento/caso/fuente.json` — perfil curado a mano (ficha, líneas,
    enfermedad medible, molecular, material, lo que se busca). Cada dato: {valor, fuente, sello}.
    Curado y no parseado de ESTADO-ACTUAL.md: ese fichero es prosa que cambia de forma cada semana
    y un parser frágil publicaría basura sin avisar.
  · `tools/state/biomarcadores/timeline.json` — las analíticas (lo genera `biomarcadores.py`).
  · `<web>/content/{es,en}/timeline.yml` — la cronología YA pública de la web. Se LEE, no se copia
    a mano, para que la cronología de la web y la del panel no puedan divergir. Sus fechas son
    texto libre («ene–feb 2024», «2021», «8–9 abr 2026»): cada una se modela con su PRECISIÓN
    (día, mes, año, rango). Un evento de precisión mes se dibuja como franja, no como una línea
    en un día inventado. Lo que no se puede interpretar va a `avisos` y NO se dibuja.

SALIDAS
  · `tools/state/caso/caso_privado.json` — todo, con la ruta de cada fuente (para cotejar).
  · `<web>/app/data/caso.json` (lo importa la página) y `<web>/public/datos/caso.json` (descarga):
    lo público, subconjunto estricto del privado, sin rutas.

FAIL-CLOSED. Si un dato no tiene fuente o sello válido, si aparece un nombre de tercero, un
identificador administrativo o léxico vetado, NO se escribe nada y sale rc=1 con la lista.
El freno de contenido es `web_lint.revisar_caso()` (la excepción auditada al lint de la web:
lo clínico se permite SOLO dentro de esta estructura con sello; la prosa suelta sigue vetada).

Uso:
  python3 tools/caso_publico.py build --web <repo web>   # escribe privado + público
  python3 tools/caso_publico.py check --web <repo web>   # valida sin escribir
  python3 tools/caso_publico.py fechas "ene–feb 2024"    # prueba el lector de fechas
  python3 tools/caso_publico.py --selftest
"""
import calendar
import json
import os
import re
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
FUENTE = os.environ.get("BTP_CASO_FUENTE") or os.path.join(
    REPO, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "caso", "fuente.json")
BIOMARCADORES = os.path.join(STATE, "biomarcadores", "timeline.json")
PRIVADO = os.path.join(STATE, "caso", "caso_privado.json")
WEB = os.environ.get("BTP_WEB") or "/Users/polaris/projects/titular-{{APELLIDO}}-case"
# Dos copias idénticas: la que importa la página (se hornea en el HTML estático) y la que se
# sirve para descargar (un laboratorio la quiere en crudo, no rascada de la página).
SALIDA_WEB = os.path.join("app", "data", "caso.json")
DESCARGA_WEB = os.path.join("public", "datos", "caso.json")

SELLOS = ("verificado", "inferido", "dicho", "sin_verificar")

# Dónde vive cada dato del esquema. Lo que no esté aquí NO se publica (se avisa).
DATOS_SIMPLES = {
    "ficha": ("diagnostico", "fecha_diagnostico", "edad_diagnostico", "estadio", "histologia",
              "ecog", "estado_actual"),
}
LISTAS = {
    "ficha": ("receptores", "sitios"),
    "enfermedad_medible": ("recist", "volumen", "lesiones", "pet"),
    "molecular": ("muestras", "alteraciones", "firmas", "falta"),
}
# `reservorio` (24-sep-26, v2 del panel): la longitud del catéter en cada TC, para enseñar que
# no se ha movido. Cada medida es un dato {valor, fecha, longitud_mm, margen_mm, fuente, sello}.
RAIZ_LISTAS = ("lineas", "material", "se_busca", "reservorio")
RAIZ_SIMPLES = ("nunca_recibido",)

# Etiquetas de la cronología web que son curso clínico. Divulgación, IA y Equipo no lo son:
# se guardan pero no se dibujan en el carril de eventos.
TAGS_CLINICOS = {
    "Antecedentes": "antecedentes", "Síntomas": "sintomas", "Diagnóstico": "diagnostico",
    "Primera línea": "tratamiento", "Segunda línea": "tratamiento", "Tercera línea": "tratamiento",
    "Ingreso urgente": "ingreso", "Molecular": "molecular", "Progresión": "progresion",
    "Ensayos clínicos": "tratamiento",
}
TAGS_NO_CLINICOS = {"Divulgación", "IA", "Equipo"}

# Se curan y validan igual, pero NO salen al público: van solo al privado. El perfil molecular
# ya lo enseña /ciencia y /datos se quedó en la clínica ({{TITULAR}}, 24-sep-2026, v2 del panel).
SOLO_PRIVADO = ("molecular",)


class ErrorCaso(Exception):
    """Fallo de validación. `fallos` lleva la lista completa, no solo el primero."""

    def __init__(self, fallos):
        super().__init__("; ".join(fallos[:5]) + (" …" if len(fallos) > 5 else ""))
        self.fallos = fallos


# ── fechas en texto libre de la cronología web ──────────────────────────────────────────────

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
    "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}
_M = r"([a-zé]{3})\.?"
_GUION = r"\s*[–—-]\s*"
_PATRONES_FECHA = (
    # 2021
    ("anio", re.compile(r"^(\d{4})$")),
    # 31 oct 2023
    ("dia", re.compile(r"^(\d{1,2})\s+" + _M + r"\s+(\d{4})$")),
    # Oct 31, 2023
    ("dia_en", re.compile(r"^" + _M + r"\s+(\d{1,2}),\s*(\d{4})$")),
    # 8–9 abr 2026 · 26-30 jun 2026
    ("dias", re.compile(r"^(\d{1,2})" + _GUION + r"(\d{1,2})\s+" + _M + r"\s+(\d{4})$")),
    # Apr 8–9, 2026
    ("dias_en", re.compile(r"^" + _M + r"\s+(\d{1,2})" + _GUION + r"(\d{1,2}),\s*(\d{4})$")),
    # ene–feb 2024
    ("meses", re.compile(r"^" + _M + _GUION + _M + r"\s+(\d{4})$")),
    # jul 2024
    ("mes", re.compile(r"^" + _M + r"\s+(\d{4})$")),
)


def _mes(txt):
    m = MESES.get(txt.lower()[:3])
    if not m:
        raise ValueError("mes desconocido: %r" % txt)
    return m


def _iso(a, m, d):
    datetime(a, m, d)  # valida (31 feb revienta aquí, no en la gráfica)
    return "%04d-%02d-%02d" % (a, m, d)


def _fin_mes(a, m):
    return _iso(a, m, calendar.monthrange(a, m)[1])


def leer_fecha(texto):
    """Texto libre → {desde, hasta, precision} o None si no es interpretable.

    precision ∈ dia | mes | anio. `desde == hasta` solo con precisión día sin rango: es el
    único caso que se dibuja como línea; todo lo demás es franja."""
    t = (texto or "").strip()
    for tipo, rx in _PATRONES_FECHA:
        m = rx.match(t.lower())
        if not m:
            continue
        g = m.groups()
        try:
            if tipo == "anio":
                a = int(g[0])
                return {"desde": _iso(a, 1, 1), "hasta": _iso(a, 12, 31), "precision": "anio"}
            if tipo == "dia":
                a, mm, d = int(g[2]), _mes(g[1]), int(g[0])
                return {"desde": _iso(a, mm, d), "hasta": _iso(a, mm, d), "precision": "dia"}
            if tipo == "dia_en":
                a, mm, d = int(g[2]), _mes(g[0]), int(g[1])
                return {"desde": _iso(a, mm, d), "hasta": _iso(a, mm, d), "precision": "dia"}
            if tipo == "dias":
                a, mm = int(g[3]), _mes(g[2])
                d1, d2 = int(g[0]), int(g[1])
                if d2 < d1:
                    return None
                return {"desde": _iso(a, mm, d1), "hasta": _iso(a, mm, d2), "precision": "dia"}
            if tipo == "dias_en":
                a, mm = int(g[3]), _mes(g[0])
                d1, d2 = int(g[1]), int(g[2])
                if d2 < d1:
                    return None
                return {"desde": _iso(a, mm, d1), "hasta": _iso(a, mm, d2), "precision": "dia"}
            if tipo == "meses":
                a, m1, m2 = int(g[2]), _mes(g[0]), _mes(g[1])
                if m2 < m1:
                    return None
                return {"desde": _iso(a, m1, 1), "hasta": _fin_mes(a, m2), "precision": "mes"}
            if tipo == "mes":
                a, mm = int(g[1]), _mes(g[0])
                return {"desde": _iso(a, mm, 1), "hasta": _fin_mes(a, mm), "precision": "mes"}
        except ValueError:
            return None
    return None


def fecha_iso_parcial(txt):
    """«2024», «2024-02», «2024-02-15» (lo que usa fuente.json) → {desde, hasta, precision}."""
    if txt is None:
        return None
    t = str(txt).strip()
    try:
        if re.fullmatch(r"\d{4}", t):
            a = int(t)
            return {"desde": _iso(a, 1, 1), "hasta": _iso(a, 12, 31), "precision": "anio"}
        if re.fullmatch(r"\d{4}-\d{2}", t):
            a, m = map(int, t.split("-"))
            return {"desde": _iso(a, m, 1), "hasta": _fin_mes(a, m), "precision": "mes"}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            a, m, d = map(int, t.split("-"))
            return {"desde": _iso(a, m, d), "hasta": _iso(a, m, d), "precision": "dia"}
    except ValueError:
        return None
    return None


# ── lector mínimo de la cronología web (el formato exacto de content/*/timeline.yml) ────────
# La casa es stdlib: no hay PyYAML. Este lector entiende SOLO la forma que usa ese fichero
# (lista `entries:` de mapas con escalares y bloques plegados `>`), y ante cualquier otra
# cosa FALLA: mejor un build roto que una cronología leída a medias.

def _escalar(v):
    v = v.strip()
    if v in ("true", "false"):
        return v == "true"
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return json.loads(v)
    return v


def leer_timeline_yml(ruta):
    with open(ruta, encoding="utf-8") as f:
        lineas = f.read().splitlines()
    entradas, actual, plegado, errores = [], None, None, []
    for n, raw in enumerate(lineas, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            if plegado:
                actual[plegado].append("")
            continue
        if raw == "entries:":
            continue
        if plegado and raw.startswith("      "):
            actual[plegado].append(raw.strip())
            continue
        plegado = None
        m = re.match(r"^  - (\w+):\s*(.*)$", raw) or re.match(r"^    (\w+):\s*(.*)$", raw)
        if not m:
            errores.append("línea %d no reconocida: %r" % (n, raw[:60]))
            continue
        if raw.startswith("  - "):
            actual = {}
            entradas.append(actual)
        if actual is None:
            errores.append("línea %d fuera de una entrada" % n)
            continue
        clave, valor = m.group(1), m.group(2)
        if valor in (">", ">-", "|", "|-"):
            actual[clave] = []
            plegado = clave
        else:
            actual[clave] = _escalar(valor)
    if errores:
        raise ErrorCaso(["%s: %s" % (os.path.basename(ruta), e) for e in errores])
    for e in entradas:
        for k, v in list(e.items()):
            if isinstance(v, list):
                e[k] = " ".join(x for x in v if x).strip()
    return entradas


def eventos_web(web):
    """La cronología pública como eventos con fecha modelada. Devuelve (eventos, avisos)."""
    es = leer_timeline_yml(os.path.join(web, "content", "es", "timeline.yml"))
    ruta_en = os.path.join(web, "content", "en", "timeline.yml")
    en = leer_timeline_yml(ruta_en) if os.path.exists(ruta_en) else []
    avisos = []
    if en and len(en) != len(es):
        avisos.append({"tipo": "cronologia_en", "detalle":
                       "la cronología EN tiene %d entradas y la ES %d: títulos EN no emparejados"
                       % (len(en), len(es))})
        en = []
    eventos = []
    for i, e in enumerate(es):
        fecha = leer_fecha(e.get("date"))
        tag = e.get("tag") or ""
        if tag in TAGS_CLINICOS:
            clase = TAGS_CLINICOS[tag]
        elif tag in TAGS_NO_CLINICOS:
            clase = None
        else:
            avisos.append({"tipo": "etiqueta_desconocida",
                           "detalle": "«%s» (%s): etiqueta sin clasificar, no se dibuja"
                           % (e.get("title"), tag)})
            clase = None
        if fecha is None:
            avisos.append({"tipo": "fecha_no_interpretable",
                           "detalle": "«%s»: la fecha «%s» no se puede leer con precisión; "
                                      "no se dibuja" % (e.get("title"), e.get("date"))})
            continue
        titulo = {"es": e.get("title")}
        if en:
            fe = leer_fecha(en[i].get("date"))
            if fe == fecha:
                titulo["en"] = en[i].get("title")
            else:
                avisos.append({"tipo": "cronologia_en", "detalle":
                               "entrada %d: la fecha EN «%s» no coincide con la ES «%s»"
                               % (i, en[i].get("date"), e.get("date"))})
        eventos.append({
            "id": "web-%02d" % i,
            "fecha_texto": e.get("date"),
            **fecha,
            "etiqueta": tag,
            "clase": clase,
            "dibujar": clase is not None,
            "destacado": bool(e.get("highlight")),
            "titulo": titulo,
            "enlace": e.get("link"),
        })
    return eventos, avisos


# ── fuente curada ───────────────────────────────────────────────────────────────────────────

def _es_dato(x):
    return isinstance(x, dict) and ("sello" in x or "fuente" in x or "valor" in x)


def validar_fuente(f):
    """Lista de fallos del perfil curado. Vacía = válido. No para en el primero."""
    fallos = []
    if not isinstance(f, dict):
        return ["fuente.json no es un objeto"]
    fuentes = f.get("fuentes")
    if not isinstance(fuentes, dict) or not fuentes:
        return ["falta el diccionario `fuentes`"]
    for fid, fu in fuentes.items():
        if not isinstance(fu, dict) or not fu.get("ruta") or not fu.get("publico"):
            fallos.append("fuentes.%s: necesita `ruta` y `publico`" % fid)

    def chequear(dato, donde):
        if not isinstance(dato, dict):
            fallos.append("%s: no es un dato {valor, fuente, sello}" % donde)
            return
        if dato.get("fuente") not in fuentes:
            fallos.append("%s: fuente «%s» no está en `fuentes`" % (donde, dato.get("fuente")))
        if dato.get("sello") not in SELLOS:
            fallos.append("%s: sello «%s» no válido (%s)"
                          % (donde, dato.get("sello"), "/".join(SELLOS)))

    for bloque, claves in DATOS_SIMPLES.items():
        for c in claves:
            if c in (f.get(bloque) or {}):
                chequear(f[bloque][c], "%s.%s" % (bloque, c))
    for c in RAIZ_SIMPLES:
        if c in f:
            chequear(f[c], c)
    for bloque, claves in LISTAS.items():
        for c in claves:
            lista = (f.get(bloque) or {}).get(c)
            if lista is None:
                continue
            if not isinstance(lista, list):
                fallos.append("%s.%s: debería ser una lista" % (bloque, c))
                continue
            for i, d in enumerate(lista):
                chequear(d, "%s.%s[%d]" % (bloque, c, i))
    for c in RAIZ_LISTAS:
        lista = f.get(c)
        if lista is None:
            continue
        if not isinstance(lista, list):
            fallos.append("%s: debería ser una lista" % c)
            continue
        for i, d in enumerate(lista):
            chequear(d, "%s[%d]" % (c, i))
    return fallos


def proyectar_fuente(f):
    """Solo lo que el esquema conoce. Devuelve (proyección, avisos por lo que se queda fuera)."""
    avisos, out = [], {}
    conocidas = {"version", "actualizado", "fuentes"} | set(DATOS_SIMPLES) | set(LISTAS) \
        | set(RAIZ_LISTAS) | set(RAIZ_SIMPLES)
    for k in f:
        if k not in conocidas:
            avisos.append({"tipo": "clave_desconocida",
                           "detalle": "`%s` no está en el esquema: no se publica" % k})
    for bloque in set(DATOS_SIMPLES) | set(LISTAS):
        if bloque not in f:
            continue
        permitidas = set(DATOS_SIMPLES.get(bloque, ())) | set(LISTAS.get(bloque, ()))
        out[bloque] = {}
        for k, v in f[bloque].items():
            if k in permitidas:
                out[bloque][k] = v
            else:
                avisos.append({"tipo": "clave_desconocida",
                               "detalle": "`%s.%s` no está en el esquema: no se publica"
                               % (bloque, k)})
    for c in RAIZ_LISTAS + RAIZ_SIMPLES:
        if c in f:
            out[c] = f[c]
    return out, avisos


# ── analíticas ──────────────────────────────────────────────────────────────────────────────

def _direccion(valor, lo, hi, fuera):
    if not fuera:
        return None
    if hi is not None and valor > hi:
        return "alto"
    if lo is not None and valor < lo:
        return "bajo"
    return "fuera"  # el informe lo marcó y su rango no está: se respeta, sin inventar lado


def analiticas(bio):
    grupos = {}
    for gk, g in (bio.get("grupos") or {}).items():
        analitos = []
        for a in g.get("analitos", []):
            # Lo DERIVADO (p. ej. BUN/creatinina = urea/2,14 ÷ creatinina, con una banda 10-20 que no
            # sale de ningún informe) no se publica: la página promete que cada cifra viene de un
            # informe, y salía con ref_de «informe», ▲ y ×límite (revisión del PR 223, 25-sep).
            if "(derivado)" in (a.get("nombre") or "") or any(
                    str(p.get("fuente", "")).startswith("derivado") for p in a.get("puntos", [])):
                continue
            ref = a.get("ref") or {}
            puntos = []
            for p in a.get("puntos", []):
                if p.get("confianza") == "baja":
                    continue  # por_confirmar: nunca se grafica (lo decide biomarcadores.py)
                # El rango de SU informe (los labs difieren: CA 15-3 tiene LSN 23,5, 32,4 y 35
                # según el hospital). Si el informe no lo trae, la banda más frecuente, y se dice.
                propio = p.get("ref_high") is not None
                lo = p.get("ref_low") if propio else ref.get("low")
                hi = p.get("ref_high") if propio else ref.get("high")
                fuera = bool(p.get("fuera"))
                if not fuera and not propio:
                    # Sin rango en su informe, nadie marcó el punto. Si se dibuja en ×LSN contra
                    # la banda, se juzga contra esa misma banda; si no, saldría a 2× y «dentro».
                    fuera = (hi is not None and p["valor"] > hi) or \
                            (lo is not None and p["valor"] < lo)
                puntos.append({"f": p["fecha"], "v": p["valor"], "lo": lo, "hi": hi,
                               "ref_de": "informe" if propio else "banda",
                               "fuera": _direccion(p["valor"], lo, hi, fuera)})
            puntos.sort(key=lambda x: x["f"])  # la gráfica une puntos en orden: no fiarse
            if puntos:
                analitos.append({"key": a["key"], "nombre": a["nombre"], "unidad": a["unidad"],
                                 "ref": a.get("ref"), "puntos": puntos})
        if analitos:
            grupos[gk] = {"nombre": g.get("nombre"), "analitos": analitos}
    return {
        "rango": bio.get("rango_fechas"),
        "n_analiticas": bio.get("n_analiticas"),
        "generado": bio.get("generado"),
        "sello": "extraido",
        # Honesto con el origen: desde el 25-sep las tres analíticas de ago-sep 2026 se extraen del
        # PDF original (antes, de una transcripción que omitía filas y rangos).
        "fuente": {"es": "Informes de laboratorio, leídos por el lector de analíticas y fechados "
                         "por el día de la extracción. Las tres de agosto y septiembre de 2026 se "
                         "extraen del PDF original del laboratorio. Solo muestras de sangre y solo lo "
                         "leído con confianza alta o media; nada calculado por nosotros.",
                   "en": "Lab reports, read by the lab parser and dated by the day the sample was "
                         "drawn. The three from August and September 2026 are extracted from the "
                         "lab's original PDF. Blood samples only, only values read with high or "
                         "medium confidence, nothing calculated by us."},
        "grupos": grupos,
    }


# ── construcción ────────────────────────────────────────────────────────────────────────────

def construir(fuente, bio, web):
    fallos = validar_fuente(fuente)
    if fallos:
        raise ErrorCaso(fallos)
    proy, avisos = proyectar_fuente(fuente)
    eventos, av_ev = eventos_web(web)
    avisos += av_ev
    comun = {
        "version": 1,
        "generado": datetime.now().isoformat(timespec="seconds"),
        "actualizado": fuente.get("actualizado"),
        **proy,
        "eventos": eventos,
        "analiticas": analiticas(bio),
        "avisos": avisos,
    }
    privado = dict(comun, fuentes=fuente["fuentes"])
    publico = dict({k: v for k, v in comun.items() if k not in SOLO_PRIVADO},
                   fuentes={k: {"publico": v["publico"]}
                            for k, v in fuente["fuentes"].items()})
    import web_lint  # el freno de contenido de la web; aquí, su excepción auditada
    fallos = web_lint.revisar_caso(publico)
    if fallos:
        raise ErrorCaso(["[%s] %s" % (c, m) for c, m in fallos])
    futuras = citas_futuras(publico, date.today())
    if futuras:
        raise ErrorCaso(futuras)
    return privado, publico


# ── ninguna cita futura con día en lo público ────────────────────────────────────────────────
# 25-sep-2026: el panel publicó «pruebas la semana del 28-sep» y «primera dosis el 1-oct» junto al
# hospital. Con un acosador activo, el día y el lugar de una cita futura dicen dónde encontrarla
# (memoria feedback-no-publicar-citas-futuras-con-lugar). Solo el mes; la fecha, cuando haya pasado.
_MESES_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
             "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_MESES_EN = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
             "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_MESES_LARGOS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
                 "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
                 "diciembre": 12, "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
                 "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
_ABREV = "|".join(sorted(set(_MESES_ES) | set(_MESES_EN), key=len, reverse=True))
_LARGOS = "|".join(sorted(_MESES_LARGOS, key=len, reverse=True))
_RE_FECHAS = [
    ("iso", re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")),
    ("dmy", re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2}|20\d\d)\b")),  # 28-9-26
    ("d_de_mes", re.compile(r"\b(\d{1,2}) de (%s)(?: de (20\d\d))?\b" % _LARGOS, re.I)),
    ("d_mes", re.compile(r"\b(\d{1,2})[- ](%s|%s)\.?(?:[- ,]+(20\d\d))?\b" % (_LARGOS, _ABREV), re.I)),
    ("mes_d", re.compile(r"\b(%s|%s)\.? (\d{1,2})(?:, (20\d\d))?\b" % (_LARGOS, _ABREV), re.I)),
]


def _mes(txt):
    t = txt.lower()
    return _MESES_LARGOS.get(t) or _MESES_ES.get(t[:3]) or _MESES_EN.get(t[:3])


def citas_futuras(pub, hoy):
    """Rutas y textos del público con una FECHA DE DÍA posterior a `hoy` (vacío = nada que tapar).
    Solo el mes («oct 2026», «2026-10») no cuenta. Sin año, se asume el de `hoy`."""
    out = []

    def fecha(a, m, d):
        try:
            return date(a, m, d)
        except ValueError:
            return None

    def mira(s, ruta):
        for tipo, rx in _RE_FECHAS:
            for g in rx.finditer(s):
                if tipo == "iso":
                    f = fecha(int(g[1]), int(g[2]), int(g[3]))
                elif tipo == "dmy":
                    a = int(g[3]); a = a + 2000 if a < 100 else a
                    f = fecha(a, int(g[2]), int(g[1]))
                elif tipo in ("d_de_mes", "d_mes"):
                    f = fecha(int(g[3]) if g[3] else hoy.year, _mes(g[2]) or 0, int(g[1]))
                else:
                    f = fecha(int(g[3]) if g[3] else hoy.year, _mes(g[1]) or 0, int(g[2]))
                if f and f > hoy:
                    out.append("%s: fecha futura con día «%s» (una cita futura no se publica con su día; "
                               "solo el mes)" % (ruta, g[0]))

    def recorre(o, ruta):
        if isinstance(o, dict):
            for k, v in o.items():
                if k not in ("generado",):
                    recorre(v, "%s.%s" % (ruta, k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                recorre(v, "%s[%d]" % (ruta, i))
        elif isinstance(o, str):
            mira(o, ruta)
    recorre(pub, "$")
    return out


def es_subconjunto(pub, priv, ruta="$"):
    """Lista de rutas del público que NO están en el privado (vacía = subconjunto estricto)."""
    malas = []
    if isinstance(pub, dict):
        if not isinstance(priv, dict):
            return [ruta]
        for k, v in pub.items():
            if k not in priv:
                malas.append("%s.%s" % (ruta, k))
            elif k != "generado":
                malas += es_subconjunto(v, priv[k], "%s.%s" % (ruta, k))
    elif isinstance(pub, list):
        if not isinstance(priv, list) or len(pub) != len(priv):
            return [ruta]
        for i, (a, b) in enumerate(zip(pub, priv)):
            malas += es_subconjunto(a, b, "%s[%d]" % (ruta, i))
    elif pub != priv:
        malas.append(ruta)
    return malas


def _cargar(ruta, que):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ErrorCaso(["no existe %s: %s" % (que, ruta)])
    except ValueError as e:
        raise ErrorCaso(["%s no es JSON válido (%s): %s" % (que, e, ruta)])


def _escribir(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # compacto: la página lo hornea en su HTML y cada byte viaja a cada visita
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp, ruta)


def cmd_build(web, escribir=True):
    fuente = _cargar(FUENTE, "el perfil curado")
    bio = _cargar(BIOMARCADORES, "las analíticas")
    privado, publico = construir(fuente, bio, web)
    malas = es_subconjunto(publico, privado)
    if malas:
        raise ErrorCaso(["el público no es subconjunto del privado: %s" % ", ".join(malas[:5])])
    if escribir:
        _escribir(PRIVADO, privado)
        _escribir(os.path.join(web, SALIDA_WEB), publico)
        _escribir(os.path.join(web, DESCARGA_WEB), publico)
    return publico


def _selftest():
    casos = {
        "2021": ("2021-01-01", "2021-12-31", "anio"),
        "31 oct 2023": ("2023-10-31", "2023-10-31", "dia"),
        "ene–feb 2024": ("2024-01-01", "2024-02-29", "mes"),
        "8–9 abr 2026": ("2026-04-08", "2026-04-09", "dia"),
        "26-30 jun 2026": ("2026-06-26", "2026-06-30", "dia"),
        "Apr 8–9, 2026": ("2026-04-08", "2026-04-09", "dia"),
        "Oct 31, 2023": ("2023-10-31", "2023-10-31", "dia"),
        "jul 2024": ("2024-07-01", "2024-07-31", "mes"),
    }
    for txt, (d, h, p) in casos.items():
        r = leer_fecha(txt)
        assert r == {"desde": d, "hasta": h, "precision": p}, (txt, r)
    for malo in ("primavera 2024", "31 feb 2024", "9–8 abr 2026", "", "hacia 2022"):
        assert leer_fecha(malo) is None, malo
    assert _direccion(10, 2, 8, True) == "alto"
    assert _direccion(1, 2, 8, True) == "bajo"
    assert _direccion(5, None, None, True) == "fuera"
    assert _direccion(50, 2, 8, False) is None
    print("✅ caso_publico selftest OK")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "--selftest":
        return _selftest()
    if argv[0] == "fechas":
        for t in argv[1:]:
            print("%-20s → %s" % (t, leer_fecha(t)))
        return 0
    web = WEB
    if "--web" in argv:
        web = argv[argv.index("--web") + 1]
    if argv[0] not in ("build", "check"):
        print(__doc__)
        return 2
    try:
        pub = cmd_build(web, escribir=argv[0] == "build")
    except ErrorCaso as e:
        print("⛔ el panel NO se genera (%d fallo(s)):" % len(e.fallos))
        for x in e.fallos:
            print("  · %s" % x)
        return 1
    n_ev = sum(1 for e in pub["eventos"] if e["dibujar"])
    n_an = sum(len(g["analitos"]) for g in pub["analiticas"]["grupos"].values())
    print("✅ panel %s: %d eventos dibujables, %d analitos, %d avisos"
          % ("escrito" if argv[0] == "build" else "válido", n_ev, n_an, len(pub["avisos"])))
    for a in pub["avisos"]:
        print("  ⚠️  [%s] %s" % (a["tipo"], a["detalle"]))
    if argv[0] == "build":
        print("  privado: %s\n  público: %s" % (PRIVADO, os.path.join(web, SALIDA_WEB)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
