#!/usr/bin/env python3
"""tools/historial.py — el historial médico como sistema: partir, clasificar, indexar.

EL PROBLEMA QUE RESUELVE. El historial vivía en una carpeta plana de Drive con 276 PDFs
y una taxonomía que no era determinista: la anatomía patológica —el documento que fija
diagnóstico y receptores— estaba repartida entre `Lab`, `Prueba` e `Imagen`, según en qué
pensara quien archivó ese día. Lo molecular (DIPCAN, Guardant360, {{TEST_MOL}}), que es lo que
mueve la ruta a NED, compartía cajón `Prueba` con una valoración antropométrica
deportiva. Los duplicados se resolvían con parches en el nombre (`(2)`, `[lab]`, `[pdi]`)
y el índice se mantenía a mano, así que llevaba dos meses y medio desincronizado.

EL PRINCIPIO. **La carpeta es la clasificación, el nombre es la identidad, el índice se
genera.** Nada se clasifica dos veces y nada se mantiene a mano. La categoría desaparece
del nombre del fichero (ya la da la carpeta) y el centro pasa a ser obligatorio, porque
ya hay cuatro centros en juego y «(MD Anderson)» solo cuando es exótico deja de servir.

🔒 MURO — dato clínico N2 (nombre + fecha de nacimiento + hospital). TODO ocurre EN LOCAL:
   poppler y tesseract son binarios de la máquina. Ni una llamada saliente. La subida a
   Drive es un paso aparte y explícito (`drive.py` / conector MCP), nunca desde aquí.

Disciplina, como `archivar.py` y `ocr_informes.py`: DRY-RUN por defecto — sin `--apply`
no escribe un byte. Nunca toca el PDF de origen. Idempotente: dedup por sha256, repetir
una corrida no duplica nada.

Sin dependencias de Python: parte los PDF con **poppler** (`pdfseparate` + `pdfunite`),
que está en el PATH, en vez de pypdf, que solo vive en el `.venv` y dejaría la
herramienta fuera del alcance del `/usr/bin/python3` con el que corre `tests/test_all.sh`.

Uso:
  python3 tools/historial.py mapa <pdf>                  # qué documentos ve dentro
  python3 tools/historial.py partir <pdf> --cortes <json> [--apply]
  python3 tools/historial.py ingerir <carpeta|pdf> … [--apply]   # PDFs ya sueltos
  python3 tools/historial.py indice [--apply]            # regenera el índice (CSV)
  python3 tools/historial.py estado                      # qué hay en cada carpeta
"""
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime

HOME = os.path.expanduser("~")
REPO = os.environ.get("BTP_REPO") or os.path.join(HOME, "claudecode")
# Espejo local del historial. Es estado vivo de {{TITULAR}}, vive en casa base y ya es zona
# clínica para el guard ([[feedback-estado-vivo-resuelve-casa-base]]).
#
# Vive DENTRO de la fuente de verdad y no en `informes/` (donde nació) por una razón que no
# es de gusto: `kb.py` solo indexa lo que cuelga de `00_FUENTE-DE-VERDAD/`, así que desde
# `informes/` el historial ordenado era invisible para el RAG —Polaris conocía el vertedero
# de copias sueltas y no el archivo bueno—. Aquí lo indexa solo, y de paso hereda el candado:
# `_PRIVADO_` en la ruta ya es zona clínica para el guard y sensibilidad `private` para el RAG.
RAIZ = os.environ.get("BTP_HISTORIAL") or os.path.join(
    REPO, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "_PRIVADO_CLINICO", "_historial")

# Las carpetas, en el orden en que se ven en Drive. El número va delante para fijar el
# orden: `01` es lo molecular a propósito, no lo más antiguo — es lo que se manda cuando
# se busca acceso a un ensayo, y es lo que más veces se abre.
CARPETAS = [
    ("indice",     "00 · Índice y resumen"),
    ("molecular",  "01 · Molecular y genómica"),
    ("patologia",  "02 · Anatomía patológica y biopsias"),
    ("imagen",     "03 · Imagen"),
    ("laboratorio", "04 · Laboratorio"),
    ("consulta",   "05 · Consultas y evolutivo"),
    ("ingreso",    "06 · Hospitalización y urgencias"),
    ("soporte",    "07 · Enfermería, nutrición y soporte"),
    ("tramite",    "08 · Peticiones, consentimientos y trámites"),
    ("paquetes",   "99 · Paquetes enviados"),
]
CLAVE_A_CARPETA = dict(CARPETAS)

# Las mismas carpetas, ya creadas en `01 · Tratamiento > Historial médico` de Drive. Los ids
# se fijan aquí para que `subir` no tenga que adivinarlas por nombre (dos carpetas pueden
# llamarse igual en Drive; el id no miente).
CARPETAS_DRIVE = {
    "00 · Índice y resumen": "1xw7d8Dy8P_7n8jzPPjAHSSEE-76MY50T",
    "01 · Molecular y genómica": "1oxthM-k8Ob8dPaiqYzM-z85Zj2F1NEm-",
    "02 · Anatomía patológica y biopsias": "1ISc508RTkfVHUnhXpUANwZSVBGoznp9H",
    "03 · Imagen": "1pJ67-RNU9ZIc3WfY6gbvt-QHeU19fBsB",
    "04 · Laboratorio": "1mTrAznnm6vPQm_lCvBSdm9rNR11fRFgY",
    "05 · Consultas y evolutivo": "1NTIyiPKnepV4gr5zPUCkdIMp6Hc0pYVe",
    "06 · Hospitalización y urgencias": "1oH9G0dsR9Vb_5T7dGcY4HFSn-vISfCln",
    "07 · Enfermería, nutrición y soporte": "1PGH906IfsSbI-Q3BnK3ILi0ol7uvXSFK",
    "08 · Peticiones, consentimientos y trámites": "1wZ_GPLIawLocsUwkt0BjLg2aLeCkITHw",
    "99 · Paquetes enviados": "1ob-UoRXNm1Qp4SABi35toil2V_o5y2EY",
}

# Códigos de centro. Cortos y estables: entran en el nombre de CADA fichero, así que un
# centro nuevo se añade aquí y no se improvisa en el nombre.
CENTROS = {
    "HUVH": "Hospital Universitari Vall d'Hebron (Barcelona)",
    "{{CENTRO}}": "Vall d'Hebron Institute of Oncology (Barcelona)",
    "CETIR": "CETIR Centre Mèdic / ASCIRES (Barcelona)",
    "HMM": "Hospital General Universitario Morales Meseguer (Murcia)",
    "{{CENTRO}}": "Hospital Clínico Universitario Virgen de la {{CENTRO}} (Murcia)",
    "HSL": "Hospital Santa Lucía / Rosell (Cartagena)",
    "MDA": "MD Anderson Cancer Center",
    "{{CENTRO}}": "{{CENTRO}} {{CIUDAD}} {{CENTRO}}",
    # El {{CENTRO}} PIDE, pero quien EMITE el molecular es el instituto de patología del
    # universitario: el FoundationOne CDx del hueso sale con membrete del USZ.
    "USZ": "UniversitätsSpital {{CIUDAD}} — Institut für Pathologie und Molekularpathologie",
    "Guardant": "Guardant Health",
    "DIPCAN": "Estudio DIPCAN",
    # Cajón honesto para el sistema público murciano: hay 25 informes (anatomía patológica,
    # radiodiagnóstico, ECG) que NO nombran hospital en ninguna página; lo único que
    # acreditan es el emisor regional. Poner «HMM» ahí sería inventarse el centro.
    "SMS": "Servicio Murciano de Salud (hospital no identificado en el informe)",
    # Proveedores privados que emiten informe propio. Van aquí porque el centro es
    # obligatorio en el nombre y «sin-centro» en un informe que SÍ dice quién lo firma
    # sería mentir por omisión.
    "OnMyMeal": "OnMyMeal (nutrición oncológica)",
    "NeverSurrender": "Never Surrender (valoración física)",
    "SOLTI": "SOLTI (grupo académico de investigación en cáncer)",
}

# Alias que aparecen en el NOMBRE del fichero cuando el PDF no dice quién lo emite (o no
# tiene capa de texto). Quien nombró esos ficheros tenía el documento delante.
ALIAS_EN_NOMBRE = {
    "onmymeal": "OnMyMeal",
    "never surrender": "NeverSurrender",
    "solti": "SOLTI",
    "hope-focus": "SOLTI",
    "hope focus": "SOLTI",
    "guardant": "Guardant",
    "dipcan": "DIPCAN",
}


def detecta_centro_en_nombre(nombre):
    """Centro deducido del NOMBRE del fichero. Último recurso, cuando el PDF no lo dice."""
    n = _norm(nombre)
    for alias, codigo in ALIAS_EN_NOMBRE.items():
        if alias in n:
            return codigo
    return "?"

# Señales de contenido → carpeta. El orden IMPORTA: se para en la primera que casa, y las
# más específicas van antes. Es justo lo que la taxonomía vieja no tenía: un documento de
# anatomía patológica casa por `patologia` antes de llegar a `laboratorio`, así que no
# puede acabar en dos sitios según el día.
SENALES = [
    ("molecular", r"(?i){{TEST_MOL}}|{{CENTRO}}\s*ε|{{TEST_MOL}}|epsilon|gene expression|fusion test|"
                  r"mutation test|copy number|guardant|foundation.?one|dipcan|"
                  r"secuenciaci[óo]n|\bngs\b|exoma|panel gen[ée]tic|biopsia l[íi]quida|"
                  r"variant allele|\bvaf\b|neoantigen|inmunopeptidom"),
    ("patologia", r"(?i)anatom[íi]a\s*patol[óo]g|anatomia\s*patol[òo]g|informe.{0,12}patol|"
                  r"histolog|inmunohistoqu[íi]m|immunohistochem|citolog|biops|\bbag\b|\bpaaf\b|"
                  r"receptor(es)?\s*hormonal|her2|ki.?67"),
    # `pet` va con frontera de palabra a la fuerza: el patrón `pet.?[ct]` casaba con
    # «PETICión» y mandaba a Imagen cualquier petición de prueba, incluidos los informes
    # de laboratorio, que empiezan por «Petición nº».
    ("imagen",    r"(?i)\bpet[\s\-/]?(?:tc|ct|tac)\b|\btac\b|\btc\b de|resonan|"
                  r"\brm\b |\brmn\b|ecograf|"
                  r"mamograf|gammagraf|densitom|radiolog|diagn[òo]stic per la imatge|"
                  r"pruebas de imagen|recist|\bmip\b|dicom|"
                  # ECG y ecocardiograma: no son imagen en sentido estricto, pero es donde
                  # los puso quien montó el archivo en Drive y no hay carpeta de pruebas
                  # funcionales. Sin esto caían en el cajón de trámites.
                  r"electrocardiogra|\becg\b|ecocardiogra"),
    ("laboratorio", r"(?i)informe de laboratori|laboratoris? cl[íi]nics?|"
                    r"an[áa]lisis cl[íi]nic|anal[íi]tic|hemogram|"
                    r"bioqu[íi]mic|ionograma|marcador(es)? tumoral|microbiolog|hemocultiv|"
                    r"exudado|serolog|coagulaci[óo]n"),
    ("ingreso",   r"(?i)informe de alta|alta de hospitalizaci|alta de urgencias|urg[èe]nci|"
                  r"epicrisis|ingreso hospitalario"),
    ("soporte",   r"(?i)enfermer[íi]a|cuidados al alta|nutrici[óo]n|diet[ée]tic|psicolog|"
                  r"psiquiatr|fisioterap|composici[óo]n corporal|antropom[ée]tric"),
    ("tramite",   r"(?i)consentimiento informado|consentiment informat|petici[óo]n de|"
                  r"solicitud|autorizaci[óo]n|formulario|historia cl[íi]nica [íi]ntegra"),
    ("consulta",  r"(?i)curs cl[íi]nic|curso cl[íi]nico|consultas? externas?|"
                  r"informe cl[íi]nic|visita|evolutiv"),
]


# ─────────────────────────── utilidades ───────────────────────────

def _norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def _seguro(nombre, limite=120):
    """Nombre de fichero utilizable: sin separadores ni caracteres que rompan el FS.

    La extensión se separa a mano y no con `splitext`, que en «Curso clínico (Dra.
    Barberi)» tomaba « Barberi)» por extensión y truncaba el nombre en «(Dra.». Aquí
    solo cuenta como extensión lo que de verdad lo parece: hasta 5 caracteres
    alfanuméricos pegados al final tras un punto.
    """
    nombre = (nombre or "documento").replace("/", "-").replace("\\", "-").replace("\x00", "")
    nombre = re.sub(r'[:*?"<>|\r\n\t]', "_", nombre).strip(" .") or "documento"
    m = re.search(r"\.([A-Za-z0-9]{1,5})$", nombre)
    raiz, ext = (nombre[:m.start()], m.group(0)) if m else (nombre, "")
    return raiz[:limite] + ext


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def texto_paginas(pdf, desde, hasta):
    """Texto de un rango de páginas. 100% local (poppler), sin red.

    `-layout` no es cosmético: los informes de laboratorio de Vall d'Hebron ponen la
    etiqueta y el valor en dos columnas, y sin él salen todas las etiquetas juntas y
    todos los valores después. Las fechas de extracción quedaban ilegibles.
    """
    try:
        out = subprocess.run(["pdftotext", "-layout", "-f", str(desde), "-l", str(hasta),
                              "-q", pdf, "-"], capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def n_paginas(pdf):
    try:
        out = subprocess.run(["pdfinfo", pdf], capture_output=True, timeout=60)
        m = re.search(r"Pages:\s+(\d+)", out.stdout.decode("utf-8", "replace"))
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


# Un curso clínico es una NOTA DE EVOLUCIÓN: en una sola página el oncólogo cita la
# biopsia, el PET y el perfil molecular. Clasificarlo por «qué palabras salen» lo manda a
# patología o a molecular según la página. Se reconoce por su encabezado y gana a todo.
ENCABEZADO_CURSO = re.compile(r"(?i)curs cl[íi]nic|curso cl[íi]nico")

# Los informes del portal de Murcia no traen membrete de servicio: traen una REJILLA de
# campos administrativos («N.H.C. … Servicio … F. Ingreso … Nº Cama»). Clasificarlos por
# palabras del cuerpo los mandaba a cualquier sitio —un alta de oncología acababa en
# patología porque dentro se cita la biopsia—. Lo que dice qué SON es qué campos tiene la
# rejilla: si hay fecha de alta y número de cama, hubo un ingreso; si hay hora de cita, fue
# una consulta. Se miran sobre texto normalizado (sin acentos, en minúsculas).
CABECERAS = [
    # el anatomopatológico se anuncia él solo, y gana a todo lo demás
    ("patologia",   r"informe anatomopatologic|informe de anatomia patologic"),
    # Enfermería ANTES que el alta: «Informe de cuidados de enfermería AL ALTA» lo firma
    # enfermería y va a su carpeta; la rejilla de abajo, que solo ve `F. Alta`, lo mandaba a
    # Hospitalización.
    ("soporte",     r"informe de (cuidados de )?enfermeri|informe de enfermeri|"
                    r"cuidados de enfermeria"),
    # rejilla de hospitalización: alta o cama. El ingreso solo no basta (una consulta de
    # hospital de día también trae fecha de ingreso).
    ("ingreso",     r"f\.?\s*alta\b|n[º°o]?\.?\s*cama\b"),
    # rejilla de consulta externa
    ("consulta",    r"f\.?\s*consulta\b|h\.?\s*cita\b"),
    # cabecera de los laboratorios de Murcia. Va DESPUÉS de las dos de arriba y antes que
    # nada del cuerpo: estos informes llevan «Servicio Remitente: URGENCIAS», que sin esta
    # regla los mandaba a Hospitalización y urgencias.
    ("laboratorio", r"datos peticion|resultados sin firma facultativa"),
    ("imagen",      r"unidad de imagen|servicio de radiodiagnostic"),
]


def clasificar(texto):
    """Carpeta que le toca a un documento. La CABECERA manda sobre el cuerpo.

    Mirar el texto entero hacía que un informe de laboratorio que menciona de pasada la
    biopsia acabara en patología. Lo que dice qué ES un documento son sus primeras
    líneas —el membrete del servicio que lo emite—, no lo que cita por dentro.
    """
    if ENCABEZADO_CURSO.search(texto[:600]):
        return "consulta"
    # 1200 y no 600: el membrete bilingüe de Vall d'Hebron (centro, dirección, municipio,
    # país, teléfono, en catalán y castellano) gasta media página antes de decir qué
    # servicio emite el documento, que es el dato que de verdad clasifica.
    cabecera, n = _norm(texto[:1200]), _norm(texto)
    for clave, patron in CABECERAS:
        if re.search(patron, cabecera):
            return clave
    for clave, patron in SENALES:
        if re.search(patron, cabecera):
            return clave
    for clave, patron in SENALES:
        if re.search(patron, n):
            return clave
    return "tramite"      # cajón honesto: si no sé qué es, no lo meto en uno clínico


def detecta_centro(texto):
    # Espacios colapsados: el membrete del portal parte la dirección en dos líneas
    # («C/ Marqués de los\nVélez»), y sin esto el marcador no casaba en la mitad de los
    # documentos — la misma radiografía salía con centro en una copia y sin centro en otra.
    n = " ".join(_norm(texto).split())
    if "cetir" in n or "ascires" in n:
        return "CETIR"
    if "{{CENTRO}}" in n or "institut d'investigacio oncologica" in n:
        return "{{CENTRO}}"
    if "vall d'hebron" in n or "vall dhebron" in n or "vall d hebron" in n:
        return "HUVH"
    if "morales meseguer" in n:
        return "HMM"
    if "{{CENTRO}}" in n:
        return "{{CENTRO}}"
    if "santa lucia" in n or "rosell" in n:
        return "HSL"
    if "anderson" in n:
        return "MDA"
    # USZ ANTES que {{CENTRO}}: el informe molecular del hueso lleva las dos cabeceras («Original
    # an: {{CENTRO}}»), y quien lo firma es el instituto de patología del universitario.
    # Se mira por marcas del EMISOR (dominio, nombre del instituto), no por la mención suelta.
    if "usz.ch" in n or "molekularpathologie" in n or "universitatsspital" in n:
        return "USZ"
    if "{{CENTRO}}" in n:
        return "{{CENTRO}}"
    if "guardant" in n:
        return "Guardant"
    if "dipcan" in n:
        return "DIPCAN"
    # Último recurso, y solo cuando NINGÚN hospital se ha nombrado: los informes del portal
    # de Murcia no llevan membrete, pero sí la dirección postal en la rejilla. Verificado
    # sobre el archivo: de 157 documentos con «Marqués de los Vélez», 106 nombran además a
    # Morales Meseguer y ninguno a otro hospital salvo uno, que es una derivación y ya se ha
    # resuelto arriba por su nombre. (No se usa el N.H.C. para esto: es identificador de
    # paciente y no se versiona en el repo.)
    # Los dos trozos por separado y no la frase entera: el membrete va a dos columnas y el
    # texto sale interleado («c/ marques de los … telefono … velez, s/n»), así que la frase
    # completa no casa en la mayoría de los documentos donde la dirección SÍ está.
    if "marques de los" in n and "velez" in n:
        return "HMM"
    # Último de todos: el documento acredita el sistema murciano (tarjeta CARM o número de
    # historia del portal) pero no nombra hospital. Verificado sobre el archivo: los 83
    # documentos con `N.H.C.` que SÍ nombran hospital nombran uno murciano —Morales Meseguer,
    # {{CENTRO}} o Santa Lucía—, nunca uno de fuera. Así que esto acota, no inventa.
    if re.search(r"\bcarm\d", n) or "n.h.c." in n or "nhc:" in n:
        return "SMS"
    return "?"


# La fecha del documento va SIEMPRE en un campo con nombre. Coger «la primera fecha que
# aparezca» fechaba las notas por una fecha citada dentro del texto (una biopsia de 2024
# mencionada en una visita de 2026), que es peor que no tener fecha: parece correcta.
# El día y el mes pueden venir sin cero delante y el año con dos dígitos: los laboratorios
# de Vall d'Hebron escriben `14/7/26`. Exigir `\d{2}` en los tres dejaba esos informes sin
# fecha, que es el dato con el que se nombran.
_FECHA = r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})"
CAMPOS_FECHA = re.compile(
    r"(?i)(?:data i hora presa de la mostra|data d'obtenci[óo]|"
    r"fecha de obtenci[óo]n de las muestras|data i hora|data informe|"
    r"data d[eo] validaci|data exploraci[óo]?|data de la visita|"
    r"fecha de exploraci[óo]n|fecha del informe|fecha de emisi[óo]n|"
    # Murcia: la rejilla administrativa del portal fecha con `F. Consulta` o `Fecha del
    # estudio`, y sin esto los informes de consulta externa se fechaban por la primera fecha
    # suelta de la página, que a veces es la de una prueba citada dentro.
    r"fecha del estudio|f\.?\s*consulta|"
    r"data de l'informe|f\.?\s*visita)\s*:?\s*" + _FECHA)

# Campos que GANAN al resto, en este orden, y por qué no valía meterlos en la alternación de
# arriba: `CAMPOS_FECHA` casa con la primera etiqueta que aparece EN LA PÁGINA, no con la más
# fiable. Dos casos reales lo rompían:
#   · Zúrich escribe `Eingang` (entrada de la muestra) ANTES que `Ausgang` (salida del
#     informe). El FoundationOne del hueso se archivaba por el 20-07, cuando llegó la
#     muestra, en vez de por el 19-08, cuando se emitió el resultado.
#   · Un alta de hospitalización escribe `F. Ingreso` antes que `F. Alta`, y un alta se fecha
#     el día que se firma, no el día que entraste.
CAMPOS_FECHA_PRIO = [
    re.compile(r"(?i)(?:ausgang|berichtsdatum)\s*:?\s*" + _FECHA),
    re.compile(r"(?i)f\.?\s*alta\s*:?\s*" + _FECHA),
]
NACIMIENTO = ("27", "12", "1990")


def _valida(d, mes, a):
    a = "20" + a if len(a) == 2 else a
    if not (1990 <= int(a) <= 2100 and 1 <= int(mes) <= 12 and 1 <= int(d) <= 31):
        return None
    if (d.zfill(2), mes.zfill(2), a) == NACIMIENTO:
        return None
    return "%s-%s-%s" % (a, mes.zfill(2), d.zfill(2))


def detecta_fecha(texto):
    """Fecha del documento: primero por campo con nombre, y solo si no hay, la primera
    suelta que no sea la de nacimiento (la cabecera de VH lleva `Data Naixement` siempre)."""
    for rx in CAMPOS_FECHA_PRIO:
        m = rx.search(texto)
        if m:
            f = _valida(*m.groups())
            if f:
                return f
    m = CAMPOS_FECHA.search(texto)
    if m:
        f = _valida(*m.groups())
        if f:
            return f
    for m in re.finditer(_FECHA, texto):
        f = _valida(*m.groups())
        if f:
            return f
    return ""


# Basura que traen los nombres del portal y que no dice nada del documento: el índice con el
# que el portal ordenaba su lista (`01_`, `19_`) y las extensiones dobles.
_PREFIJO_INDICE = re.compile(r"^\d{1,2}[_-]\s*")
# Nombres que son solo un número o una fecha: el fichero de laboratorio `12_01_2024.pdf` o
# `20-377541665.pdf`. Como descripción no valen para nada.
_NOMBRE_MUDO = re.compile(r"^[\d\s_.\-]+$")

# Qué poner cuando el nombre viejo no describía nada. Es genérico a propósito: describe el
# tipo de documento, que es lo único que se sabe con certeza, y no inventa un detalle.
DESC_POR_DEFECTO = {
    "molecular": "Informe molecular",
    "patologia": "Anatomía patológica",
    "imagen": "Prueba de imagen",
    "laboratorio": "Analítica",
    "consulta": "Informe de consulta",
    "ingreso": "Informe de alta",
    "soporte": "Informe de soporte",
    "tramite": "Documento",
    "paquetes": "Paquete enviado",
    "indice": "Índice",
}


def limpia_descripcion(stem, clave=None):
    """Descripción utilizable a partir del nombre viejo del fichero.

    Quita el índice del portal y, si lo que queda no describe nada (un número de petición,
    una fecha), devuelve el genérico de la carpeta. Un fichero llamado `20-377541665` no se
    encuentra nunca; `Analítica` sí.
    """
    d = _PREFIJO_INDICE.sub("", (stem or "").strip())
    d = re.sub(r"\s+", " ", d.replace("_", " ")).strip(" -.")
    if not d or _NOMBRE_MUDO.match(d):
        return DESC_POR_DEFECTO.get(clave, "Documento")
    return d


def nombre_documento(fecha, centro, descripcion):
    """`AAAA-MM-DD - CENTRO - Descripción` — sin categoría: esa la da la carpeta.

    Lo que falta se dice, no se disimula: `sin-fecha` y `sin-centro` son etiquetas que se
    buscan con un grep. Antes el centro desconocido salía como `?`, que el saneado del
    nombre convertía en `_`, y un guion bajo en medio del nombre no se distingue de un
    nombre correcto — el hueco quedaba invisible justo en el sitio donde hay que verlo.
    """
    if not centro or centro == "?":
        centro = "sin-centro"
    return _seguro("%s - %s - %s" % (fecha or "sin-fecha", centro, descripcion))


# ─────────────────────────── mapa ───────────────────────────

def mapa(pdf):
    """Página a página: qué documento parece empezar ahí. Para revisar ANTES de cortar."""
    total = n_paginas(pdf)
    filas = []
    for p in range(1, total + 1):
        txt = texto_paginas(pdf, p, p)
        filas.append({
            "pagina": p,
            "chars": len(txt.strip()),
            "escaneada": len(txt.strip()) < 40,
            "fecha": detecta_fecha(txt),
            "centro": detecta_centro(txt),
            "carpeta": clasificar(txt) if txt.strip() else "?",
            "cabecera": " ".join(txt.split())[:70],
        })
    return {"pdf": pdf, "paginas": total, "filas": filas}


# ─────────────────────────── partir ───────────────────────────

# Intérprete que tenga pypdf. Vive en el .venv del repo y esta herramienta corre con el
# python del sistema (el mismo con el que `tests/test_all.sh` la ejecuta), así que se llama
# como subproceso en vez de importarlo.
VENV_PY = os.path.join(REPO, ".venv", "bin", "python3")

_RECORTA = """
import sys
from pypdf import PdfReader, PdfWriter
src, dst, desde, hasta = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
r = PdfReader(src); w = PdfWriter()
for i in range(desde - 1, hasta):
    w.add_page(r.pages[i])
w.compress_identical_objects()
with open(dst, "wb") as f:
    w.write(f)
"""


def _extrae_con_pypdf(pdf, desde, hasta, destino):
    """Copia las páginas tal cual. El texto sale IDÉNTICO al original: pypdf mueve los
    objetos de página, no vuelve a renderizar nada."""
    if not os.path.exists(VENV_PY):
        return False
    try:
        r = subprocess.run([VENV_PY, "-c", _RECORTA, pdf, destino, str(desde), str(hasta)],
                           capture_output=True, timeout=300)
        return r.returncode == 0 and os.path.exists(destino)
    except Exception:
        return False


def _extrae_con_poppler(pdf, desde, hasta, destino):
    """Respaldo sin dependencias. `pdfseparate` + `pdfunite` arrastra TODAS las fuentes
    del documento a CADA trozo —27 documentos de un PDF de 5 MB pesaban 508 MB—, así que
    hace falta pasarlo por `pdftocairo`, que lo deja en la centésima parte. El precio: el
    texto se reposiciona (mismo contenido, distinta indentación), y por eso este camino es
    el segundo y no el primero."""
    tmp = destino + ".partes"
    os.makedirs(tmp, exist_ok=True)
    try:
        subprocess.run(["pdfseparate", "-f", str(desde), "-l", str(hasta), pdf,
                        os.path.join(tmp, "p-%d.pdf")], check=True, capture_output=True,
                       timeout=300)
        partes = sorted(
            (f for f in os.listdir(tmp) if f.endswith(".pdf")),
            key=lambda f: int(re.search(r"(\d+)", f).group(1)))
        if not partes:
            return False
        crudo = destino + ".crudo"
        subprocess.run(["pdfunite"] + [os.path.join(tmp, f) for f in partes] + [crudo],
                       check=True, capture_output=True, timeout=300)
        subprocess.run(["pdftocairo", "-pdf", crudo, destino], check=True,
                       capture_output=True, timeout=300)
        os.remove(crudo)
        return os.path.exists(destino)
    finally:
        for f in os.listdir(tmp) if os.path.isdir(tmp) else []:
            os.remove(os.path.join(tmp, f))
        if os.path.isdir(tmp):
            os.rmdir(tmp)


def _extrae_rango(pdf, desde, hasta, destino):
    return (_extrae_con_pypdf(pdf, desde, hasta, destino)
            or _extrae_con_poppler(pdf, desde, hasta, destino))


def partir(pdf, cortes, apply=False):
    """`cortes`: [{desde, hasta, carpeta, centro, fecha, descripcion}]. Escribe con --apply.

    Comprueba ANTES de escribir que los cortes cubren el PDF entero y no se solapan: una
    página perdida en un historial clínico no se nota hasta que hace falta.
    """
    total = n_paginas(pdf)
    cubierto = []
    for c in cortes:
        cubierto += list(range(int(c["desde"]), int(c["hasta"]) + 1))
    faltan = sorted(set(range(1, total + 1)) - set(cubierto))
    repes = sorted({p for p in cubierto if cubierto.count(p) > 1})

    resultado = {"total_paginas": total, "faltan": faltan, "duplicadas": repes,
                 "documentos": [], "escritos": 0}
    if faltan or repes:
        return resultado          # fail-closed: no se corta un historial a medias

    for c in cortes:
        carpeta = CLAVE_A_CARPETA.get(c["carpeta"], CLAVE_A_CARPETA["tramite"])
        nombre = nombre_documento(c.get("fecha"), c.get("centro"), c["descripcion"]) + ".pdf"
        destino = os.path.join(RAIZ, carpeta, nombre)
        doc = {"desde": c["desde"], "hasta": c["hasta"], "carpeta": carpeta,
               "ruta": os.path.relpath(destino, RAIZ),
               "paginas": int(c["hasta"]) - int(c["desde"]) + 1}
        if apply:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            if _extrae_rango(pdf, int(c["desde"]), int(c["hasta"]), destino):
                doc["sha256"] = sha256(destino)
                resultado["escritos"] += 1
        resultado["documentos"].append(doc)
    return resultado


# ─────────────────────────── ingerir ───────────────────────────

# `partir` es para el paquete gordo que baja el hospital: un PDF con 27 informes dentro.
# `ingerir` es la otra puerta, y es la que mueve el volumen: los ~450 PDF que ya vienen
# separados —el volcado plano de Drive, las carpetas del portal, lo que se descargó suelto—
# y a los que solo les falta carpeta y nombre. Comparte TODO el criterio con `partir`
# (`clasificar`, `detecta_centro`, `detecta_fecha`, `nombre_documento`): si un día cambia
# cómo se clasifica, cambia para las dos puertas a la vez.

# Solo entra PDF. Un .docx o un .xlsx no se puede leer con poppler, no se puede cotejar
# contra su original y el índice cuenta páginas: entrarían como documentos mudos. Se listan
# aparte para decidirlos a mano, que es mejor que archivarlos a ciegas.
EXT_DOC = (".pdf",)

# El nombre del volcado viejo de Drive: `AAAA-MM-DD - Categoría - Descripción`. La FECHA y la
# DESCRIPCIÓN de ahí son mejor dato que lo que se saca del texto (las puso una persona
# mirando el documento). La CATEGORÍA no: es justo la taxonomía que no era determinista y la
# razón de esta herramienta, así que se tira y se vuelve a clasificar por contenido.
_NOMBRE_VIEJO = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*-\s*([^-]{1,20}?)\s*-\s*(.+)$")
# Analíticas del portal, que se llamaban solo por su fecha: `12_01_2024.pdf`, `24-05-2024.pdf`.
_NOMBRE_FECHA = re.compile(r"^(\d{1,2})[-_.](\d{1,2})[-_.](\d{4})$")
# Los informes de DIPCAN llevan la fecha pegada al final en formato máquina:
# `DIPCAN19715_Bioquimica_20240722113201.pdf`. Dentro del PDF no hay campo de fecha, así que
# sin esto los cinco entraban como `sin-fecha`.
_NOMBRE_SELLO = re.compile(r"[_-](\d{4})(\d{2})(\d{2})\d{0,6}$")


def _del_nombre(stem):
    """(fecha, descripción, centro) rescatados del nombre viejo. Vacíos si no dice nada."""
    m = _NOMBRE_VIEJO.match(stem)
    if m:
        fecha, medio, desc = m.group(1), m.group(2).strip(), m.group(3).strip()
        # Si el trozo del medio ya es un código de centro, el fichero viene del esquema
        # nuevo (ya pasó por aquí) y ese centro es fiable.
        return fecha, desc, (medio if medio in CENTROS else "")
    m = _NOMBRE_FECHA.match(stem)
    if m:
        f = _valida(m.group(1), m.group(2), m.group(3))
        if f:
            return f, "", ""
    m = _NOMBRE_SELLO.search(stem)
    if m:
        f = _valida(m.group(3), m.group(2), m.group(1))
        if f:
            return f, _NOMBRE_SELLO.sub("", stem).strip(" _-"), ""
    # Último recurso: una fecha ISO metida en medio del nombre, como en
    # `Molecular {{TEST_MOL}} LiquidBiopsy 2026-04-07 EN+orig`. Va la ÚLTIMA de todas las reglas
    # porque un nombre puede citar una fecha que no es la del documento; pero cuando no hay
    # nada mejor, es preferible a que el PDF se feche por la primera cifra que aparezca
    # dentro (que puso tres de estos informes en 2001, 2007 y 2009).
    sueltas = re.findall(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", stem)
    if sueltas:
        a, mes, d = sueltas[-1]
        f = _valida(d, mes, a)
        if f:
            return f, stem, ""
    return "", "", ""


def _texto_de(pdf, paginas=3):
    """Texto para clasificar. Si el PDF es un escaneo sin capa de texto, tira de OCR local.

    Sin esto los escaneos caían todos en el cajón `08 · trámites` con `sin-fecha`: no es que
    estuvieran mal clasificados, es que no había NADA que clasificar.
    """
    txt = texto_paginas(pdf, 1, paginas)
    if len(txt.strip()) >= 120:
        return txt, False
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ocr_informes
        from pathlib import Path as _P
        return ocr_informes.ocr_pdf(_P(pdf)), True
    except Exception:
        return txt, False


def _archivados():
    """sha256 → ruta relativa de lo que YA está en el historial. Es lo que hace que repetir
    una ingesta no duplique nada, y que las cuatro copias del mismo informe entren una vez."""
    ya = {}
    for _clave, carpeta in CARPETAS:
        d = os.path.join(RAIZ, carpeta)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".pdf"):
                p = os.path.join(d, f)
                ya[sha256(p)] = os.path.join(carpeta, f)
    return ya


def _candidatos(rutas, excluir):
    fuera = re.compile(excluir) if excluir else None
    out, no_doc = [], []
    for r in rutas:
        if os.path.isfile(r):
            hallados = [r]
        else:
            hallados = []
            for dp, dns, fns in os.walk(r):
                dns[:] = [d for d in dns if not d.startswith(".")]
                hallados += [os.path.join(dp, f) for f in fns if not f.startswith(".")]
        for p in sorted(hallados):
            if fuera and fuera.search(p):
                continue
            (out if p.lower().endswith(EXT_DOC) else no_doc).append(p)
    return out, no_doc


def ingerir(rutas, apply=False, excluir=None, carpeta_forzada=None, descripcion=None):
    """Clasifica, renombra y COPIA al historial PDFs que ya vienen sueltos.

    Copia, nunca mueve: el origen se queda intacto hasta que {{TITULAR}} vea el resultado.
    """
    ya = _archivados()
    vistos = dict(ya)                      # sha → dónde está (lo de antes + lo de esta corrida)
    pdfs, no_doc = _candidatos(rutas, excluir)
    res = {"vistos": len(pdfs), "no_documento": no_doc, "duplicados": [],
           "documentos": [], "dudosos": [], "escritos": 0, "ocr": 0}

    for p in pdfs:
        h = sha256(p)
        if h in vistos:
            res["duplicados"].append({"origen": p, "ya_en": vistos[h]})
            continue

        stem = os.path.splitext(os.path.basename(p))[0]
        f_nom, d_nom, c_nom = _del_nombre(stem)
        txt, con_ocr = _texto_de(p)
        if con_ocr:
            res["ocr"] += 1

        clave = carpeta_forzada or clasificar(txt)
        carpeta = CLAVE_A_CARPETA.get(clave, CLAVE_A_CARPETA["tramite"])
        centro = c_nom or detecta_centro(txt)
        if centro == "?":
            centro = detecta_centro_en_nombre(stem)
        fecha = f_nom or detecta_fecha(txt)
        desc = descripcion or limpia_descripcion(d_nom or stem, clave)

        nombre = nombre_documento(fecha, centro, desc) + ".pdf"
        destino = os.path.join(RAIZ, carpeta, nombre)
        # Dos documentos distintos pueden merecer el mismo nombre (dos analíticas del mismo
        # día en el mismo centro). El sha ya dijo que NO son el mismo fichero, así que no se
        # pisan: se desempata con un sufijo, que es feo pero no pierde un documento.
        n, base = 2, destino
        while destino in [d["destino_abs"] for d in res["documentos"]] or os.path.exists(destino):
            destino = base[:-4] + (" (%d).pdf" % n)
            n += 1

        doc = {"origen": p, "carpeta": carpeta, "fichero": os.path.basename(destino),
               "destino_abs": destino, "fecha": fecha, "centro": centro, "sha": h,
               "ocr": con_ocr}
        motivos = []
        if not fecha:
            motivos.append("sin fecha")
        if centro == "?":
            motivos.append("centro desconocido")
        if clave == "tramite" and not carpeta_forzada:
            motivos.append("cajón por defecto (nada casó)")
        if motivos:
            doc["motivos"] = motivos
            res["dudosos"].append(doc)

        if apply:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(p, destino)
            # El escaneo entra con su transcripción al lado: sin el sidecar el RAG solo ve
            # píxeles y el documento estaría archivado pero mudo para Polaris.
            if con_ocr and txt.strip():
                with open(destino[:-4] + ".ocr.txt", "w", encoding="utf-8") as fh:
                    fh.write(txt)
            res["escritos"] += 1
        vistos[h] = os.path.join(carpeta, os.path.basename(destino))
        res["documentos"].append(doc)

    return res


# ─────────────────────────── índice ───────────────────────────

CABECERA = ["Fecha", "Carpeta", "Centro", "Centro completo", "Documento",
            "Transcripción ES", "Traducción EN", "Páginas", "sha256", "Fichero"]


def _fila_de(ruta_abs, carpeta):
    base = os.path.basename(ruta_abs)
    raiz = os.path.splitext(base)[0]
    m = re.match(r"^(\d{4}-\d{2}-\d{2}|sin-fecha) - ([^-]+) - (.+)$", raiz)
    fecha, centro, desc = (m.group(1), m.group(2).strip(), m.group(3)) if m else ("", "?", raiz)
    carpeta_abs = os.path.dirname(ruta_abs)
    return [
        fecha, carpeta, centro, CENTROS.get(centro, ""), desc,
        "sí" if os.path.exists(os.path.join(carpeta_abs, raiz + "_ES.md")) else "",
        "sí" if os.path.exists(os.path.join(carpeta_abs, raiz + "_EN.md")) else "",
        n_paginas(ruta_abs), sha256(ruta_abs)[:16], base,
    ]


def indice(apply=False):
    """Regenera el índice LEYENDO los ficheros. Es lo que mata la desincronización: el
    índice deja de ser algo que alguien se acuerda de actualizar."""
    filas, por_carpeta, hashes = [], {}, {}
    for _clave, carpeta in CARPETAS:
        d = os.path.join(RAIZ, carpeta)
        if not os.path.isdir(d):
            continue
        pdfs = sorted(f for f in os.listdir(d) if f.lower().endswith(".pdf"))
        por_carpeta[carpeta] = len(pdfs)
        for f in pdfs:
            p = os.path.join(d, f)
            fila = _fila_de(p, carpeta)
            hashes.setdefault(fila[8], []).append(f)
            filas.append(fila)

    duplicados = {h: v for h, v in hashes.items() if len(v) > 1}
    salida = os.path.join(RAIZ, CLAVE_A_CARPETA["indice"], "INDICE_historial.csv")
    if apply:
        os.makedirs(os.path.dirname(salida), exist_ok=True)
        with open(salida, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Historial clínico — índice generado por tools/historial.py"])
            w.writerow(["Generado", datetime.now().strftime("%Y-%m-%d %H:%M")])
            w.writerow([])
            w.writerow(["Carpeta", "Documentos"])
            for carpeta, n in por_carpeta.items():
                w.writerow([carpeta, n])
            w.writerow(["TOTAL", sum(por_carpeta.values())])
            w.writerow([])
            w.writerow(CABECERA)
            w.writerows(filas)
    return {"total": len(filas), "por_carpeta": por_carpeta,
            "duplicados": duplicados, "salida": salida if apply else None}


def pendientes_de_subir(locales, en_drive):
    """Los ficheros locales cuyo nombre NO está ya en la carpeta de Drive.

    Nombre exacto, no subcadena: con subcadena «X.pdf» se daba por subido si arriba había
    «X.pdf.bak». Y normalizado NFC, porque macOS guarda los acentos descompuestos (NFD) y
    Drive los devuelve compuestos: sin normalizar, «ANATOMÍA» local ≠ «ANATOMÍA» de Drive y se
    volvería a subir un informe que ya está (pasó con 5 ficheros el 11-sep-2026).
    """
    arriba = {unicodedata.normalize("NFC", n) for n in en_drive}
    return [f for f in locales if unicodedata.normalize("NFC", f) not in arriba]


# ─────────────────────────── CLI ───────────────────────────

def main(argv):
    cmd = (argv[0] if argv else "estado").lower()
    apply = "--apply" in argv

    if cmd == "mapa":
        if len(argv) < 2:
            print("uso: historial.py mapa <pdf>", file=sys.stderr)
            return 2
        m = mapa(argv[1])
        print("%s — %d páginas" % (os.path.basename(m["pdf"]), m["paginas"]))
        for f in m["filas"]:
            print("  p%-3d %-11s %-8s %-12s %s%s" % (
                f["pagina"], f["fecha"] or "—", f["centro"], f["carpeta"],
                "[escaneada] " if f["escaneada"] else "", f["cabecera"][:52]))
        return 0

    if cmd == "partir":
        if len(argv) < 2:
            print("uso: historial.py partir <pdf> --cortes <json> [--apply]", file=sys.stderr)
            return 2
        ruta_cortes = None
        for i, a in enumerate(argv):
            if a == "--cortes" and i + 1 < len(argv):
                ruta_cortes = argv[i + 1]
        if not ruta_cortes:
            print("falta --cortes <json>", file=sys.stderr)
            return 2
        with open(ruta_cortes, encoding="utf-8") as f:
            cortes = json.load(f)
        r = partir(argv[1], cortes, apply=apply)
        if r["faltan"] or r["duplicadas"]:
            print("⛔ los cortes no cubren el PDF entero — no se escribe nada.")
            print("   páginas sin asignar: %s" % (r["faltan"] or "ninguna"))
            print("   páginas en dos documentos: %s" % (r["duplicadas"] or "ninguna"))
            return 1
        for d in r["documentos"]:
            print("  p%-3s-%-3s → %s" % (d["desde"], d["hasta"], d["ruta"]))
        print("\n%s%d documentos, %d páginas cubiertas de %d"
              % ("" if apply else "DRY-RUN: ", len(r["documentos"]),
                 sum(d["paginas"] for d in r["documentos"]), r["total_paginas"]))
        if not apply:
            print("Repite con --apply para escribirlos.")
        return 0

    if cmd == "ingerir":
        rutas = [a for a in argv[1:] if not a.startswith("--")]
        if not rutas:
            print("uso: historial.py ingerir <carpeta|pdf> … [--apply] [--excluir <regex>] "
                  "[--carpeta <clave>] [--descripcion <texto>]", file=sys.stderr)
            return 2
        opt = {}
        for i, a in enumerate(argv):
            if a in ("--excluir", "--carpeta", "--descripcion") and i + 1 < len(argv):
                opt[a[2:]] = argv[i + 1]
                if argv[i + 1] in rutas:
                    rutas.remove(argv[i + 1])
        if opt.get("carpeta") and opt["carpeta"] not in CLAVE_A_CARPETA:
            print("carpeta desconocida: %s (claves: %s)"
                  % (opt["carpeta"], ", ".join(k for k, _ in CARPETAS)), file=sys.stderr)
            return 2
        r = ingerir(rutas, apply=apply, excluir=opt.get("excluir"),
                    carpeta_forzada=opt.get("carpeta"), descripcion=opt.get("descripcion"))

        print("PDF mirados %d · nuevos %d · duplicados %d · no-PDF %d · con OCR %d"
              % (r["vistos"], len(r["documentos"]), len(r["duplicados"]),
                 len(r["no_documento"]), r["ocr"]))
        por_carpeta = {}
        for d in r["documentos"]:
            por_carpeta[d["carpeta"]] = por_carpeta.get(d["carpeta"], 0) + 1
        for c in sorted(por_carpeta):
            print("  %-42s %3d" % (c, por_carpeta[c]))
        # En tandas cortas se enseña el nombre resultante: revisar 20 nombres a ojo antes de
        # escribir cuesta un minuto y es lo que evita archivar mal 20 documentos.
        if len(r["documentos"]) <= 25:
            print()
            for d in r["documentos"]:
                print("   %s / %s" % (d["carpeta"][:5], d["fichero"]))
        if r["dudosos"]:
            print("\n⚠️  %d dudosos (míralos ANTES de --apply):" % len(r["dudosos"]))
            for d in r["dudosos"]:
                print("   [%s] %s" % (", ".join(d["motivos"]), os.path.basename(d["origen"])[:72]))
        if r["no_documento"]:
            print("\n%d ficheros que no son PDF, sin tocar:" % len(r["no_documento"]))
            for p in r["no_documento"][:10]:
                print("   %s" % os.path.basename(p)[:72])
            if len(r["no_documento"]) > 10:
                print("   … y %d más" % (len(r["no_documento"]) - 10))
        if apply:
            print("\n✓ copiados %d documentos al historial." % r["escritos"])
            print("  Regenera el índice:  python3 tools/historial.py indice --apply")
        else:
            print("\nDRY-RUN: no se ha escrito un byte. Repite con --apply.")
        return 0

    if cmd == "indice":
        r = indice(apply=apply)
        for carpeta, n in r["por_carpeta"].items():
            print("  %-42s %3d" % (carpeta, n))
        print("  %-42s %3d" % ("TOTAL", r["total"]))
        if r["duplicados"]:
            print("\n⚠️  %d documentos duplicados por contenido:" % len(r["duplicados"]))
            for h, v in r["duplicados"].items():
                print("   %s → %s" % (h, ", ".join(v)))
        if r["salida"]:
            print("\n✓ %s" % r["salida"])
        else:
            print("\nDRY-RUN: repite con --apply para escribir el índice.")
        return 0

    if cmd == "subir":
        # Lo ejecuta TITULAR en su terminal, no un agente: `drive.py` exige teclear
        # SUBIR-CLINICO en un TTY real por cada fichero de zona clínica, y eso es el muro
        # funcionando, no un fallo. Aquí solo se le ordena el trabajo y se salta lo que ya
        # está arriba, para que no tenga que confirmar dos veces lo mismo.
        drive = os.path.join(REPO, "tools", "drive.py")
        pend = []
        for _clave, carpeta in CARPETAS:
            fid = CARPETAS_DRIVE.get(carpeta)
            d = os.path.join(RAIZ, carpeta)
            if not fid or not os.path.isdir(d):
                continue
            r = subprocess.run([sys.executable, drive, "list", "--folder", fid, "--json"],
                               capture_output=True, timeout=180)
            if r.returncode != 0:
                # Sin listado no se sabe qué hay arriba: seguir daría todo por pendiente y
                # subiría 900 duplicados. Mejor pararse y decirlo.
                print("⛔ no se pudo listar %s en Drive: %s"
                      % (carpeta, r.stderr.decode("utf-8", "replace").strip()[:200]))
                return 1
            en_drive = [x["name"] for x in json.loads(r.stdout.decode("utf-8"))]
            locales = sorted(x for x in os.listdir(d) if not x.startswith("."))
            for f in pendientes_de_subir(locales, en_drive):
                pend.append((os.path.join(d, f), fid, carpeta, f))
        print("%d ficheros pendientes de subir." % len(pend))
        if not apply:
            for _p, _fid, carpeta, f in pend[:8]:
                print("   %s / %s" % (carpeta, f[:60]))
            if len(pend) > 8:
                print("   … y %d más" % (len(pend) - 8))
            print("\nDRY-RUN. Para subir de verdad, EN TU TERMINAL:")
            print("   python3 tools/historial.py subir --apply")
            print("Cada fichero te pedirá teclear SUBIR-CLINICO (gate del muro).")
            return 0
        ok = err = 0
        for ruta, fid, carpeta, f in pend:
            r = subprocess.run([sys.executable, drive, "upload", ruta, fid], timeout=900)
            if r.returncode == 0:
                ok += 1
                print("   ✓ %s" % f[:70])
            else:
                err += 1
                print("   ✗ %s" % f[:70])
        print("\nsubidos %d · fallidos %d" % (ok, err))
        return 1 if err else 0

    if cmd == "estado":
        for _clave, carpeta in CARPETAS:
            d = os.path.join(RAIZ, carpeta)
            n = len([f for f in os.listdir(d)]) if os.path.isdir(d) else 0
            print("  %-42s %3d %s" % (carpeta, n, "" if os.path.isdir(d) else "(no existe)"))
        return 0

    print(__doc__.split("Uso:")[-1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
