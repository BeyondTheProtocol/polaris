#!/usr/bin/env python3
"""tools/deid_eval.py — banco de medida de la de-identificación: recall por entidad, con cifra.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

POR QUÉ (P4 paso 5). Hasta hoy nadie sabía cuánto se le escapaba a `deid.py`: redacta y verifica
con los mismos patrones. Esto la mide contra un corpus ANOTADO A MANO (formato brat: .txt + .ann),
con cada capa por separado y juntas, y da dos cifras que no son la misma:

  1. COBERTURA por entidad (sobre `deid.detectar`): una entidad cuenta como cubierta si las capas
     tapan TODOS sus caracteres que no son espacio. Tapar media dirección no vale. Se da también la
     cobertura parcial (tocada al menos un carácter) para ver dónde falla la frontera y no la
     detección. Y la precisión por carácter: cuánto de lo tapado era de verdad un identificador.
  2. FUGAS en la salida REAL de `de_identificar` (lo que va a producción, que tapa en cadena): para
     las entidades largas y específicas (nombres, ids, email, teléfono, calle), ¿sigue su texto,
     normalizado, en la salida? Eso es una fuga literal, no una estimación.

Corpus:
  · MEDDOCAN test (Zenodo 4279323, CC-BY-4.0): 250 casos clínicos SINTÉTICOS en castellano. Trae
    nombre de paciente y email, que CONTACTO-I no mide. En `_data/meddocan/` (no se versiona).
  · CONTACTO-I test (PhysioNet, con acuerdo de uso): historias reales. Pendiente de la cuenta de ella.
  · Una muestra suya anotada a mano: pendiente (paso 6). Esa NO sale de zona clínica.

El diccionario propio (sus identificadores) se APAGA con corpus públicos: no pinta nada ahí y
mezclaría la medida. Con su muestra se deja encendido.

MUESTRA SUYA (zona clínica). Solo por la ventanilla: `lector_clinico.py procesa deid_eval -- …`
(fuera de ella estos modos se niegan: el guard y los permisos no dejan tocar la zona de otro modo,
y así cada acceso queda en su log). La carpeta tiene que ser zona clínica por el MISMO predicado del
guard (`zonas_clinicas.es_ruta_clinica`); fail-closed.
  --preparar <dir> --origenes <lista.json>   crea <dir> (0700) y copia cada origen a NN.txt (0600,
                                             sin sobrescribir) + MANIFIESTO.json
  --guardar-anotacion <dir> --doc NN         lee de stdin [{"texto","etiqueta"}] y escribe
                                             NN.gold.json y NN.ann (cada aparición, la más larga
                                             primero, sin solapes). Si un texto no está en el
                                             documento, no escribe nada y lo dice.
  --brat <dir>                               la medida: solo imprime cifras agregadas.

Uso:
  python3 tools/deid_eval.py --brat _data/meddocan/meddocan/test/brat --sin-diccionario
  python3 tools/deid_eval.py --brat <dir> --capas regex,regex+ner --json salida.json
"""
import argparse
import collections
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deid  # noqa: E402

# Entidades cuya aparición literal en la salida es una fuga inequívoca (largas y específicas).
ETIQUETAS_FUGA = ("NOMBRE_SUJETO_ASISTENCIA", "NOMBRE_PERSONAL_SANITARIO", "ID_SUJETO_ASISTENCIA",
                  "ID_ASEGURAMIENTO", "ID_CONTACTO_ASISTENCIAL", "ID_TITULACION_PERSONAL_SANITARIO",
                  "CORREO_ELECTRONICO", "NUMERO_TELEFONO", "NUMERO_FAX", "CALLE",
                  "FAMILIARES_SUJETO_ASISTENCIA")
LONGITUD_MIN_FUGA = 4       # «Ana» sola aparece en cualquier texto: por debajo no se afirma fuga


def cargar_brat(directorio):
    """[(id, texto, [(ini, fin, etiqueta, superficie)])]. Tramos discontinuos se parten."""
    docs = []
    for ann in sorted(glob.glob(os.path.join(directorio, "*.ann"))):
        base = ann[:-4]
        with open(base + ".txt", encoding="utf-8", newline="") as fh:
            texto = fh.read()
        oro = []
        with open(ann, encoding="utf-8") as fh:
            for linea in fh:
                if not linea.startswith("T"):
                    continue
                _tid, info, superficie = linea.rstrip("\n").split("\t", 2)
                etiqueta, rangos = info.split(" ", 1)
                rangos = rangos.split(";")
                for r in rangos:
                    a, b = (int(x) for x in r.split())
                    # la superficie del .ann es la que se coteja con el .txt; en un tramo partido
                    # brat la da unida por espacios, así que ahí se toma la del texto
                    oro.append((a, b, etiqueta, superficie if len(rangos) == 1 else texto[a:b]))
        docs.append((os.path.basename(base), texto, oro))
    return docs


def offsets_cuadran(docs):
    """El .ann coincide con el .txt: si no, todo lo demás mide basura."""
    return sum(texto[a:b] != sup for _id, texto, oro in docs for a, b, _e, sup in oro)


def _cubierto(texto, a, b, tapado):
    chars = [i for i in range(a, b) if not texto[i].isspace()]
    if not chars:
        return True, True
    dentro = [i in tapado for i in chars]
    return all(dentro), any(dentro)


def puntuar(docs, detecciones):
    """Cobertura por etiqueta + precisión por carácter. `detecciones[i]` = [(ini, fin, capa)]."""
    por = collections.defaultdict(lambda: {"n": 0, "cubiertas": 0, "parciales": 0})
    chars_tapados = chars_tapados_oro = 0
    for (_id, texto, oro), dets in zip(docs, detecciones):
        tapado = set()
        for a, b, _c in dets:
            tapado.update(range(max(0, a), min(len(texto), b)))
        en_oro = set()
        for a, b, et, _s in oro:
            en_oro.update(range(a, b))
            total, parcial = _cubierto(texto, a, b, tapado)
            p = por[et]
            p["n"] += 1
            p["cubiertas"] += total
            p["parciales"] += parcial
        visibles = {i for i in tapado if not texto[i].isspace()}
        chars_tapados += len(visibles)
        chars_tapados_oro += len(visibles & en_oro)
    n = sum(p["n"] for p in por.values())
    cub = sum(p["cubiertas"] for p in por.values())
    par = sum(p["parciales"] for p in por.values())
    return {
        "entidades": n,
        "recall_estricto": round(cub / n, 4) if n else None,
        "recall_parcial": round(par / n, 4) if n else None,
        "precision_caracter": round(chars_tapados_oro / chars_tapados, 4) if chars_tapados else None,
        "por_etiqueta": {et: dict(p, recall=round(p["cubiertas"] / p["n"], 3))
                         for et, p in sorted(por.items(), key=lambda kv: -kv[1]["n"])},
    }


def fugas(docs, salidas):
    """Entidades de ETIQUETAS_FUGA cuyo texto normalizado sigue en la salida real."""
    por = collections.defaultdict(lambda: {"n": 0, "fugas": 0})
    for (_id, _texto, oro), salida in zip(docs, salidas):
        sal = deid.borde._normalizar(salida)[1]
        for _a, _b, et, sup in oro:
            if et not in ETIQUETAS_FUGA:
                continue
            s = deid.borde._normalizar(sup.strip())[1]
            if len(s) < LONGITUD_MIN_FUGA:
                continue
            por[et]["n"] += 1
            por[et]["fugas"] += s in sal
    n = sum(p["n"] for p in por.values())
    f = sum(p["fugas"] for p in por.values())
    return {"entidades": n, "fugas": f, "tasa_fuga": round(f / n, 4) if n else None,
            "por_etiqueta": dict(sorted(por.items(), key=lambda kv: -kv[1]["n"]))}


def evaluar(docs, capas=("regex", "regex+ner")):
    textos = [t for _i, t, _o in docs]
    ner_spans = None
    res = {}
    for capa in capas:
        con_ner = capa.endswith("+ner")
        t0 = time.time()
        if con_ner and ner_spans is None:
            ner_spans = deid._spans_ner_lote(textos)
        dets = [deid.detectar(t, ner=con_ner, spans_ner=(ner_spans[i] if con_ner else None))
                for i, t in enumerate(textos)]
        salidas = []
        for i, t in enumerate(textos):
            if con_ner:
                tt, _ = deid._enmascarar_spans(t, ner_spans[i])
                salidas.append(deid.de_identificar(tt)[0])
            else:
                salidas.append(deid.de_identificar(t)[0])
        res[capa] = {"cobertura": puntuar(docs, dets), "fugas_salida_real": fugas(docs, salidas),
                     "segundos": round(time.time() - t0, 1)}
    return res


def _detalle(docs, capa):
    """Qué escapa (entidades no tapadas enteras) y qué se tapa de más, para diagnosticar. Imprime
    texto de los documentos: por eso solo se permite por la ventanilla y sobre zona clínica."""
    con_ner = capa.endswith("+ner")
    textos = [t for _i, t, _o in docs]
    ner = deid._spans_ner_lote(textos) if con_ner else [None] * len(textos)
    escapan = collections.Counter()
    de_mas = collections.Counter()
    for (nn, texto, oro), sp in zip(docs, ner):
        dets = deid.detectar(texto, ner=con_ner, spans_ner=sp)
        tapado = set()
        for a, b, _c in dets:
            tapado.update(range(a, b))
        en_oro = set()
        for a, b, et, sup in oro:
            en_oro.update(range(a, b))
            if not _cubierto(texto, a, b, tapado)[0]:
                escapan[(et, sup)] += 1
        for a, b, c in dets:
            fuera = [i for i in range(a, b) if i not in en_oro and not texto[i].isspace()]
            if len(fuera) > (b - a) // 2:
                de_mas[(c, texto[a:b].strip()[:40])] += 1
    print("\n## Escapan (%s)" % capa)
    for (et, sup), n in escapan.most_common(60):
        print("  %3d  %-34s %r" % (n, et, sup))
    print("\n## Tapado de más, lo más repetido (%s)" % capa)
    for (c, frag), n in de_mas.most_common(40):
        print("  %3d  %-34s %r" % (n, c, frag))


def _tabla(res):
    capas = list(res)
    lin = ["| Métrica | " + " | ".join(capas) + " |", "|---|" + "---|" * len(capas)]
    for clave, nombre in (("recall_estricto", "Recall estricto (entidad entera tapada)"),
                          ("recall_parcial", "Recall parcial (tocada)"),
                          ("precision_caracter", "Precisión por carácter")):
        lin.append("| %s | %s |" % (nombre, " | ".join(str(res[c]["cobertura"][clave]) for c in capas)))
    lin.append("| Fugas literales en la salida real | %s |" % " | ".join(
        "%d/%d" % (res[c]["fugas_salida_real"]["fugas"], res[c]["fugas_salida_real"]["entidades"])
        for c in capas))
    etiquetas = list(res[capas[0]]["cobertura"]["por_etiqueta"])
    lin += ["", "| Etiqueta | n | " + " | ".join(capas) + " |", "|---|---|" + "---|" * len(capas)]
    for et in etiquetas:
        n = res[capas[0]]["cobertura"]["por_etiqueta"][et]["n"]
        lin.append("| %s | %d | %s |" % (et, n, " | ".join(
            str(res[c]["cobertura"]["por_etiqueta"].get(et, {}).get("recall", "—")) for c in capas)))
    return "\n".join(lin)


# ── muestra suya, por la ventanilla ─────────────────────────────────────────────────────────
def _exige_ventanilla(directorio):
    """(ok, motivo). Estos modos tocan zona clínica: solo dentro de `lector_clinico procesa` y solo
    sobre una carpeta que el predicado del guard llame clínica."""
    if os.environ.get("BTP_VENTANILLA") != "1":
        return False, "solo por la ventanilla: python3 tools/lector_clinico.py procesa deid_eval -- …"
    try:
        import lector_clinico
        zc = lector_clinico.ZC
    except Exception as e:
        return False, "no pude cargar la política de zonas: %r" % e
    if zc is None or not zc.es_ruta_clinica(os.path.abspath(directorio)):
        return False, "%s no es zona clínica: aquí no se escribe" % directorio
    return True, ""


def preparar(directorio, origenes):
    ok, motivo = _exige_ventanilla(directorio)
    if not ok:
        print("RECHAZADO: " + motivo, file=sys.stderr)
        return 1
    os.makedirs(directorio, mode=0o700, exist_ok=True)
    ruta_m = os.path.join(directorio, "MANIFIESTO.json")
    try:
        with open(ruta_m, encoding="utf-8") as fh:
            previo = {m["origen"]: m for m in json.load(fh)}
    except (OSError, ValueError):
        previo = {}
    # los que ya estaban conservan su número; los nuevos siguen la numeración
    siguiente = max([int(m["n"]) for m in previo.values()] + [0]) + 1
    manifiesto = list(previo.values())
    for origen in origenes:
        if origen in previo:
            continue
        nn = "%02d" % siguiente
        siguiente += 1
        dest = os.path.join(directorio, nn + ".txt")
        with open(origen, "rb") as fh:
            datos = fh.read()
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print("ya existe %s: no se sobrescribe" % nn, file=sys.stderr)
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(datos)
        manifiesto.append({"n": nn, "origen": origen, "copiado": True})
    fd = os.open(ruta_m, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(manifiesto, fh, ensure_ascii=False, indent=1)
    print("en la muestra: %d documentos (%d nuevos)" % (len(manifiesto), len(manifiesto) - len(previo)))
    return 0


def anotacion_a_brat(texto, anotacion):
    """(lineas_ann, no_encontrados). Cada texto anotado se marca en TODAS sus apariciones; los
    más largos primero y sin solapar, para que «Pérez» no cuente dos veces dentro del nombre."""
    ocupado = set()
    tramos, faltan = [], []
    for item in sorted(anotacion, key=lambda x: -len(x["texto"])):
        sup, et = item["texto"], item["etiqueta"]
        if not sup or "\n" in sup:
            faltan.append(sup)
            continue
        # «contexto» (opcional) desambigua un texto corto: se marca solo dentro de ese fragmento
        # («F» del campo Sexo, no la de «F. Nacimiento»)
        ctx = item.get("contexto")
        rel = ctx.find(sup) if ctx else 0
        if ctx and rel < 0:
            faltan.append(sup)
            continue
        aguja = ctx or sup
        encontrado, desde = False, 0
        while True:
            a = texto.find(aguja, desde)
            if a < 0:
                break
            desde = a + 1
            a += rel
            b = a + len(sup)
            # frontera de palabra: «Murcia» no se marca dentro de «Murciano», ni «Ana» en «Anatomía»
            if (a > 0 and texto[a - 1].isalnum() and sup[0].isalnum()) or \
               (b < len(texto) and texto[b].isalnum() and sup[-1].isalnum()):
                continue
            encontrado = True
            if not ocupado.intersection(range(a, b)):
                ocupado.update(range(a, b))
                tramos.append((a, b, et, sup))
        if not encontrado:
            faltan.append(sup)
    tramos.sort()
    lineas = ["T%d\t%s %d %d\t%s" % (i, et, a, b, sup) for i, (a, b, et, sup) in enumerate(tramos, 1)]
    return lineas, faltan


def guardar_anotacion(directorio, nn, anotacion):
    ok, motivo = _exige_ventanilla(directorio)
    if not ok:
        print("RECHAZADO: " + motivo, file=sys.stderr)
        return 1
    with open(os.path.join(directorio, nn + ".txt"), encoding="utf-8", newline="") as fh:
        texto = fh.read()
    lineas, faltan = anotacion_a_brat(texto, anotacion)
    if faltan:
        print("NO GUARDO: %d texto(s) no aparecen tal cual en %s: %s"
              % (len(faltan), nn, json.dumps(faltan, ensure_ascii=False)), file=sys.stderr)
        return 4
    for nombre, contenido in ((nn + ".gold.json", json.dumps(anotacion, ensure_ascii=False, indent=1)),
                              (nn + ".ann", "\n".join(lineas) + ("\n" if lineas else ""))):
        ruta = os.path.join(directorio, nombre)
        tmp = ruta + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contenido)
        os.replace(tmp, ruta)
    print("%s: %d entidades anotadas (%d apariciones)" % (nn, len(anotacion), len(lineas)))
    # un texto corto que aparece varias veces puede estar marcando lo que no es («33» en «9.33»)
    cuenta = collections.Counter(l.split("\t", 2)[2] for l in lineas)
    dudosos = {k: v for k, v in cuenta.items() if v > 1 and len(k) <= 4}
    if dudosos:
        print("   revisa, cortos repetidos: %s" % json.dumps(dudosos, ensure_ascii=False))
    return 0


def revision(directorio, nns):
    """REVISION-NN.md: el documento con cada identificador anotado entre ⟦ ⟧ y su etiqueta, para
    que una persona compruebe en dos minutos si falta o sobra algo. En zona clínica, 0600."""
    ok, motivo = _exige_ventanilla(directorio)
    if not ok:
        print("RECHAZADO: " + motivo, file=sys.stderr)
        return 1
    por_nn = {i: (t, o) for i, t, o in cargar_brat(directorio)}
    for nn in nns:
        if nn not in por_nn:
            print("%s no está anotado" % nn, file=sys.stderr)
            return 2
        texto, oro = por_nn[nn]
        partes, cursor = [], 0
        for a, b, et, _s in sorted(oro):
            if a < cursor:
                continue
            partes += [texto[cursor:a], "⟦%s⟧₍%s₎" % (texto[a:b], et.split("_")[0].lower())]
            cursor = b
        partes.append(texto[cursor:])
        cuerpo = ("# Revisión de la anotación %s\n\n"
                  "Cada identificador anotado va entre ⟦ ⟧ con su tipo. Mira si **falta** alguno "
                  "(un nombre, número, fecha, dirección o centro sin corchetes) o si **sobra** "
                  "alguno. Apúntalo abajo y listo.\n\n## Lo que falta o sobra\n\n- \n\n---\n\n"
                  % nn) + "".join(partes)
        ruta = os.path.join(directorio, "REVISION-%s.md" % nn)
        fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(cuerpo)
        print("REVISION-%s.md: %d identificadores marcados" % (nn, len(oro)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--brat")
    ap.add_argument("--preparar")
    ap.add_argument("--origenes")
    ap.add_argument("--guardar-anotacion")
    ap.add_argument("--doc")
    ap.add_argument("--revision", help="con --brat: NN,NN,… genera REVISION-NN.md para que la revise una persona")
    ap.add_argument("--rehacer", action="store_true",
                    help="con --guardar-anotacion: regenera desde el NN.gold.json ya guardado")
    ap.add_argument("--capas", default="regex,regex+ner")
    ap.add_argument("--sin-diccionario", action="store_true",
                    help="apaga sus identificadores (obligatorio con corpus públicos)")
    ap.add_argument("--json")
    ap.add_argument("--detalle", action="store_true",
                    help="lista lo que escapa y lo que se tapa de más (solo por la ventanilla)")
    a = ap.parse_args(argv)
    if a.preparar:
        with open(a.origenes, encoding="utf-8") as fh:
            return preparar(a.preparar, json.load(fh))
    if a.guardar_anotacion:
        if a.rehacer:
            ok, motivo = _exige_ventanilla(a.guardar_anotacion)
            if not ok:
                print("RECHAZADO: " + motivo, file=sys.stderr)
                return 1
            with open(os.path.join(a.guardar_anotacion, a.doc + ".gold.json"), encoding="utf-8") as fh:
                return guardar_anotacion(a.guardar_anotacion, a.doc, json.load(fh))
        return guardar_anotacion(a.guardar_anotacion, a.doc, json.load(sys.stdin))
    if not a.brat:
        ap.error("falta --brat, --preparar o --guardar-anotacion")
    if a.revision:
        return revision(a.brat, a.revision.split(","))
    if a.sin_diccionario:
        deid._DICC = []
    docs = cargar_brat(a.brat)
    if not docs:
        print("sin documentos .ann en %s" % a.brat, file=sys.stderr)
        return 2
    malos = offsets_cuadran(docs)
    if malos:
        print("🛑 %d anotaciones no cuadran con su .txt: no mido basura" % malos, file=sys.stderr)
        return 3
    res = evaluar(docs, tuple(a.capas.split(",")))
    if a.detalle:
        ok, motivo = _exige_ventanilla(a.brat)
        if not ok:
            print("RECHAZADO --detalle: " + motivo, file=sys.stderr)
            return 1
        _detalle(docs, a.capas.split(",")[-1])
    meta = {"corpus": os.path.abspath(a.brat), "documentos": len(docs),
            "diccionario": not a.sin_diccionario, "fecha": time.strftime("%Y-%m-%d %H:%M")}
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"meta": meta, "resultados": res}, fh, ensure_ascii=False, indent=2)
    print("%d documentos · %s\n" % (len(docs), a.brat))
    print(_tabla(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
