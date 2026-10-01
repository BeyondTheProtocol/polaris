#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""incongruencias_caso.py — cuándo dos fuentes del caso dicen cosas distintas del mismo dato.

POR QUÉ (1-oct-26, plan «Vega al mando», Fase 2b)
-------------------------------------------------
{{TITULAR}} pidió que Vega le pregunte por «las incongruencias que encuentre». Las había y nadie las
cruzaba: el mismo bloque de mama se informó tres veces (2024 en origen, 2024 revisión externa, 2026
revisión del {{CENTRO}}) con RP 5 % / 100 % / 20 %, Ki-67 {{N}}% / 40 % y HER2 «0 con tinción incompleta»
frente a «0 sin tinción», que no es un detalle: ultralow o null cambia ensayos.

QUÉ HACE (local, sin red, sin modelo)
-------------------------------------
1. Lee el TEXTO ORIGINAL de los PDF de anatomía patológica y molecular del historial (pdftotext).
   Las traducciones, resúmenes y consultas se leen aparte como COPIAS: nunca son fuente de verdad.
2. Extrae, por documento: RE, RP, Ki-67, HER2 (score y, si lo dice, null/ultralow),
   cromogranina, sinaptofisina, {{DIANA3}}; la MUESTRA (mama, hueso, hígado, ganglio) y la fecha.
   Lo que no casa con seguridad no se extrae: mejor «no consta» que una cifra inventada.
3. Cruza:
   · misma muestra + mismo marcador + valores distintos → INCONGRUENCIA (se avisa, nivel B);
   · muestras distintas → HETEROGENEIDAD (biología esperable: se lista, no se avisa);
   · copia con una cifra que no aparece en ningún informe original → COPIA QUE NO CUADRA;
   · documento clínico del caso fechado antes de 2023 → FECHA IMPOSIBLE en el historial.
4. Escribe INCONGRUENCIAS-DEL-CASO.md junto al estado vivo (zona clínica privada) y
   `_incongruencias.json` con la primera vez que se vio cada una. El parte de la mañana
   (atascos.py) enseña las nuevas, en N1: marcador, muestra, valores y mes; sin centros ni nombres.

Describe, no juzga: cuál de dos lecturas manda lo deciden sus médicos.

Uso:  python3 tools/incongruencias_caso.py [--json]
Ganchos de test: BTP_HISTORIAL, y `analizar(docs)` con textos ya extraídos.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import historial  # noqa: E402

CARPETAS = ("02 · Anatomía patológica y biopsias", "01 · Molecular y genómica")
TOL_PUNTOS = 5            # diferencias de porcentaje por debajo de esto = redondeo, no se avisan
FECHA_MINIMA = "2023-01-01"
_RE_COPIA = re.compile(r"bilingual|MTB|consult|translation|traducci|resumen|ICF|justificaci|"
                       r"re-biopsy|_EN|_ES|^\d{4}-\d{2}-\d{2} - HMM - [0-9a-f]{20,}", re.I)
_MUESTRA = (("higado", re.compile(r"h[ií]gado|hep[aá]tic|liver|leber", re.I)),
            ("hueso", re.compile(r"[oó]se[ao]|hueso|ilion|il[ií]aca|ileum|iliac|\bbone\b|knochen|cresta", re.I)),
            ("ganglio", re.compile(r"ganglio|lymph|adenopat", re.I)),
            ("mama", re.compile(r"\bmama\b|mamari|breast|\bBAG\b", re.I)))

# El hueco entre el marcador y su cifra no cruza de línea ni pasa por OTRO marcador
# («Sinaptofisina (+) · Ki 67 aprox. 15 %» no es sinaptofisina 15 %), y la cifra va pegada al «%»
# («Score RP : 25» + salto + «% de núcleos…» no es RP 25 %).
_PCT = r"(?:(?!Ki[- ]?67|HER|sinapto|synapto|cromogr|chromogr|{{DIANA3}}|\bR[EP]\b|\b[EP]R\b)[^%\n·;]){0,45}?(\d{1,3})[ \t]*%"
_MARC = {
    "RE": re.compile(r"(?:receptor(?:es)? (?:de )?estr[oó]genos?|estrogen receptors?|\bER\b|\bRE\b)" + _PCT, re.I),
    "RP": re.compile(r"(?:receptor(?:es)? (?:de )?progesterona|progesterone receptors?|\bPR\b|\bRP\b)" + _PCT, re.I),
    "Ki-67": re.compile(r"Ki[- ]?67" + _PCT, re.I),
    "cromogranina": re.compile(r"(?:cromogranina|chromogranin)(?: A)?" + _PCT, re.I),
    "sinaptofisina": re.compile(r"(?:sinaptofisina|synaptophysin)" + _PCT, re.I),
}
_CUALI = {
    "cromogranina": re.compile(r"(?:cromogranina|chromogranin)(?: A)?\s*:?\s*(positiv\w*|negativ\w*|focal|\(\+\)|\(-\))", re.I),
    "sinaptofisina": re.compile(r"(?:sinaptofisina|synaptophysin)\s*:?\s*(positiv\w*|negativ\w*|\(\+\)|\(-\)|expresi[oó]n heterog\w*)", re.I),
    "{{DIANA3}}": re.compile(r"{{DIANA3}}\s*[:(]?\s*(positiv\w*|negativ\w*|expresi[oó]n nuclear\w*)", re.I),
}
_HER2 = re.compile(r"HER-?2(?:/?neu)?[^\n]{0,60}?\(?\s*(0|1\+|2\+|3\+)\s*\)?", re.I)
_HER2_NULL = re.compile(r"ausencia de tinci[oó]n|no (membrane )?staining|sin tinci[oó]n|\bnull\b", re.I)
_HER2_ULTRA = re.compile(r"incomplet\w* y casi imperceptible|barely perceptible|faint|ultra-?low", re.I)


def _norm_cuali(v):
    v = v.lower()
    if v.startswith("positiv") or v == "(+)" or "expresi" in v:
        return "positiva"
    if v.startswith("negativ") or v == "(-)":
        return "negativa"
    return v


def muestra_de(texto, nombre):
    """La muestra del informe. Primero el nombre del fichero (lo puso quien lo archivó), luego el
    texto del diagnóstico. Si dos casan en el texto, no se decide: «?»."""
    for clave, rx in _MUESTRA:
        if rx.search(nombre):
            return clave
    diag = texto[:2500]
    hits = [clave for clave, rx in _MUESTRA if rx.search(diag)]
    return hits[0] if len(hits) == 1 else "?"


def extraer(texto):
    """{marcador: valor} del texto de un informe. Un marcador con dos valores distintos en el
    mismo documento no se extrae (informe que cita otro: ambiguo)."""
    out = {}
    for m, rx in _MARC.items():
        vals = {int(x) for x in rx.findall(texto) if 0 <= int(x) <= 100}
        if len(vals) == 1:
            out[m] = "%d%%" % vals.pop()
    for m, rx in _CUALI.items():
        if m in out:
            continue
        vals = {_norm_cuali(x) for x in rx.findall(texto)}
        if len(vals) == 1:
            out[m] = vals.pop()
    scores = set()
    sub = set()
    for mt in _HER2.finditer(texto):
        scores.add(mt.group(1))
        ventana = texto[mt.start(): mt.end() + 160]
        if _HER2_ULTRA.search(ventana):
            sub.add("ultralow")
        elif _HER2_NULL.search(ventana):
            sub.add("null")
    if len(scores) == 1:
        s = scores.pop()
        out["HER2"] = s + (" (%s)" % sub.pop() if s == "0" and len(sub) == 1 else "")
    return out


def _texto_pdf(ruta):
    try:
        return subprocess.run(["pdftotext", "-layout", ruta, "-"], capture_output=True, text=True,
                              timeout=90).stdout
    except Exception:  # noqa: BLE001
        return ""


def documentos(raiz=None):
    """[(nombre, fecha, es_copia, texto)] de las carpetas de AP y molecular."""
    raiz = raiz or historial.RAIZ
    out = []
    for c in CARPETAS:
        d = os.path.join(raiz, c)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(".pdf") or "(2)" in f:
                continue
            fecha = f[:10] if re.match(r"\d{4}-\d{2}-\d{2}", f) else ""
            out.append((f, fecha, bool(_RE_COPIA.search(f)), _texto_pdf(os.path.join(d, f))))
    return out


def _num(v):
    m = re.match(r"(\d+)%", v)
    return int(m.group(1)) if m else None


def _distintos(a, b):
    # HER2: «0» a secas no contradice «0 (null)» ni «0 (ultralow)»: solo no lo especifica.
    if a.split(" ")[0] == b.split(" ")[0] and ("(" not in a or "(" not in b):
        return False
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        return abs(na - nb) > TOL_PUNTOS
    # «positiva» no contradice un porcentaje mayor que 0 (ni «negativa» un 0 %)
    for x, n in ((a, nb), (b, na)):
        if n is not None and ((x == "positiva" and n > 0) or (x == "negativa" and n == 0)):
            return False
    return a != b


def analizar(docs):
    """docs = [(nombre, fecha, es_copia, texto)] → dict con observaciones e incidencias."""
    obs = []
    fechas_raras = []
    for nombre, fecha, copia, texto in docs:
        if fecha and fecha < FECHA_MINIMA:
            fechas_raras.append({"documento": nombre, "fecha": fecha})
        if not texto.strip():
            continue
        mu = muestra_de(texto, nombre)
        for marc, val in extraer(texto).items():
            obs.append({"documento": nombre, "fecha": fecha, "copia": copia, "muestra": mu,
                        "marcador": marc, "valor": val})
    originales = [o for o in obs if not o["copia"] and o["muestra"] != "?"]
    grupos = {}
    for o in originales:
        grupos.setdefault((o["muestra"], o["marcador"]), []).append(o)
    incong = []
    for (mu, marc), lista in sorted(grupos.items()):
        # Una lectura por valor (la más antigua); hay incongruencia si ALGÚN par se contradice.
        # (Comparar solo contra lo ya guardado escondía «0 ultralow» frente a «0 null» detrás de un
        # «0» a secas que es compatible con los dos.)
        vals = []
        for o in sorted(lista, key=lambda x: x["fecha"]):
            if o["valor"] not in {v["valor"] for v in vals}:
                vals.append(o)
        choca = any(_distintos(a["valor"], b["valor"]) for i, a in enumerate(vals) for b in vals[i + 1:])
        if choca:
            incong.append({"clave": "%s|%s|%s" % (mu, marc, "/".join(sorted(v["valor"] for v in vals))),
                           "muestra": mu, "marcador": marc,
                           "lecturas": [{"valor": v["valor"], "fecha": v["fecha"], "documento": v["documento"]}
                                        for v in sorted(vals, key=lambda x: x["fecha"])]})
    hetero = []
    por_marc = {}
    for o in originales + [o for o in obs if o["copia"] and o["muestra"] not in ("?", "mama")]:
        por_marc.setdefault(o["marcador"], {}).setdefault(o["muestra"], set()).add(o["valor"])
    for marc, por_mu in sorted(por_marc.items()):
        if len(por_mu) > 1:
            hetero.append({"marcador": marc, "por_muestra": {k: sorted(v) for k, v in sorted(por_mu.items())}})
    valores_orig = {}
    for o in originales:
        valores_orig.setdefault((o["muestra"], o["marcador"]), []).append(o["valor"])
        valores_orig.setdefault(("*", o["marcador"]), []).append(o["valor"])
    copias = []
    for o in obs:
        # Copia de muestra conocida → contra los originales de esa muestra; de muestra «?» → contra
        # todos. Sin originales con los que comparar, no se juzga.
        ref = valores_orig.get((o["muestra"] if o["muestra"] != "?" else "*", o["marcador"]))
        if not o["copia"] or not ref:
            continue
        if all(_distintos(o["valor"], v) for v in ref):
            copias.append({"clave": "copia|%s|%s|%s" % (o["documento"][:60], o["marcador"], o["valor"]),
                           "documento": o["documento"], "marcador": o["marcador"], "valor": o["valor"],
                           "originales": sorted(set(ref))})
    return {"observaciones": obs, "incongruencias": incong, "heterogeneidad": hetero,
            "copias": copias, "fechas_raras": fechas_raras}


# ── Salida ───────────────────────────────────────────────────────────────────────────────────

def _ruta_md():
    return os.path.join(os.path.dirname(historial.RAIZ.rstrip(os.sep)), "INCONGRUENCIAS-DEL-CASO.md")


def _ruta_json():
    return os.path.join(os.path.dirname(historial.RAIZ.rstrip(os.sep)), "_incongruencias.json")


def _mes(fecha):
    meses = "ene feb mar abr may jun jul ago sep oct nov dic".split()
    try:
        return "%s-%s" % (meses[int(fecha[5:7]) - 1], fecha[:4])
    except (ValueError, IndexError):
        return "sin fecha"


def frase_n1(i):
    """Una línea sin centros ni nombres, apta para el parte."""
    lect = " · ".join("%s (%s)" % (l["valor"], _mes(l["fecha"])) for l in i["lecturas"])
    return "%s en la muestra de %s: %s" % (i["marcador"], i["muestra"], lect)


def render(r):
    lin = ["# Incongruencias del caso", "",
           "> Se regenera solo (tools/incongruencias_caso.py) desde el TEXTO ORIGINAL de los informes "
           "de anatomía patológica y molecular. Describe; cuál manda lo deciden sus médicos.", "",
           "## Mismo dato, misma muestra, valores distintos", ""]
    if r["incongruencias"]:
        for i in r["incongruencias"]:
            lin.append("### %s · muestra de %s" % (i["marcador"], i["muestra"]))
            lin += ["- **%s** · %s · `%s`" % (l["valor"], l["fecha"] or "sin fecha", l["documento"])
                    for l in i["lecturas"]]
            lin.append("")
    else:
        lin += ["Ninguna.", ""]
    lin += ["## Distinto entre muestras (heterogeneidad: biología esperable, no error)", ""]
    lin += ["- %s: %s" % (h["marcador"], " · ".join("%s %s" % (k, "/".join(v)) for k, v in h["por_muestra"].items()))
            for h in r["heterogeneidad"]] or ["Nada."]
    lin += ["", "## Copias (traducciones, resúmenes, consultas) con una cifra que no está en ningún informe", ""]
    lin += ["- %s = %s en `%s` (en los informes: %s)" % (c["marcador"], c["valor"], c["documento"],
                                                        ", ".join(c["originales"])) for c in r["copias"]] or ["Ninguna."]
    lin += ["", "## Fechas imposibles en el historial (antes de %s)" % FECHA_MINIMA, ""]
    lin += ["- %s · `%s`" % (f["fecha"], f["documento"]) for f in r["fechas_raras"]] or ["Ninguna."]
    lin += ["", "## Lo que se extrajo (%d lecturas)" % len(r["observaciones"]), "",
            "| Fecha | Muestra | Marcador | Valor | Copia | Documento |", "|---|---|---|---|---|---|"]
    lin += ["| %s | %s | %s | %s | %s | %s |" % (o["fecha"], o["muestra"], o["marcador"], o["valor"],
                                                "sí" if o["copia"] else "", o["documento"][:70])
            for o in sorted(r["observaciones"], key=lambda x: (x["fecha"], x["marcador"]))]
    return "\n".join(lin) + "\n"


def actualizar(docs=None, hoy=None):
    """Analiza, escribe el md y el json (con la primera vez que se vio cada incongruencia)."""
    hoy = (hoy or date.today()).isoformat()
    r = analizar(documentos() if docs is None else docs)
    try:
        prev = json.load(open(_ruta_json(), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prev = {}
    vistas = prev.get("primera_vez", {})
    for i in r["incongruencias"] + r["copias"]:
        vistas.setdefault(i["clave"], hoy)
    os.makedirs(os.path.dirname(_ruta_md()), exist_ok=True)
    with open(_ruta_md(), "w", encoding="utf-8") as fh:
        fh.write(render(r))
    with open(_ruta_json(), "w", encoding="utf-8") as fh:
        json.dump({"actualizado": hoy, "primera_vez": vistas,
                   "incongruencias": [dict(i, frase=frase_n1(i), primera_vez=vistas[i["clave"]])
                                      for i in r["incongruencias"]],
                   "n_copias": len(r["copias"]), "n_fechas_raras": len(r["fechas_raras"])},
                  fh, ensure_ascii=False, indent=1)
    return r


def nuevas(desde):
    """Frases N1 de las incongruencias vistas por primera vez en `desde` o después (para el parte)."""
    try:
        d = json.load(open(_ruta_json(), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [i["frase"] for i in d.get("incongruencias", []) if i.get("primera_vez", "") >= desde]


def main(argv):
    r = actualizar()
    if "--json" in argv:
        print(json.dumps({k: v for k, v in r.items() if k != "observaciones"}, ensure_ascii=False, indent=1))
    else:
        print("lecturas: %d · incongruencias: %d · heterogeneidad: %d · copias que no cuadran: %d · "
              "fechas imposibles: %d" % (len(r["observaciones"]), len(r["incongruencias"]),
                                        len(r["heterogeneidad"]), len(r["copias"]), len(r["fechas_raras"])))
        for i in r["incongruencias"]:
            print("  · " + frase_n1(i))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
