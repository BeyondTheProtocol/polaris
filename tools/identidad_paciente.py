#!/usr/bin/env python3
"""tools/identidad_paciente.py — estar en su carpeta NO prueba que el informe sea suyo.

NORMA (registro: tools/normas.json) `feedback-verificar-identidad-paciente-en-informe`, clase
BLOQUEO: «antes de usar datos de un informe clínico, verificar nombre+fecha de nacimiento: estar
en su carpeta NO prueba que sea suyo».

POR QUÉ NO ES PARANOIA. La carpeta clínica de {{TITULAR}} no contiene solo informes suyos: hay
documentación de terceros (su padre tiene su propio caso en Bellvitge, y por el correo entran
PDFs de otras personas). Un dato leído de un informe ajeno y usado como suyo es el peor error
posible de este sistema entero, y la ÚNICA señal fiable de a quién pertenece un informe está
DENTRO del documento: la cabecera de filiación. La ruta no lo es.

QUÉ MIRA, Y POR QUÉ SOLO ESO. Únicamente campos ETIQUETADOS («Paciente:», «Fecha de
nacimiento:», «Data de naixement:», «DOB:»…). No basta con buscar fechas sueltas: un informe
clínico está LLENO de fechas —de prueba, de visita, de informe— y cualquier detector que tome
una fecha suelta por una fecha de nacimiento daría falso positivo en cada documento. La
etiqueta es lo que convierte una fecha en una filiación.

VEREDICTOS:
  · `coincide`       — el documento dice su nombre Y su fecha de nacimiento. Único caso verde.
  · `otro_paciente`  — el documento trae filiación ETIQUETADA que NO es la suya → no se sirve.
  · `parcial`        — solo uno de los dos. No acredita: la norma pide los dos.
  · `no_consta`      — sin filiación etiquetada (una página suelta, un recorte). No acredita.
  · `sin_overlay`    — no hay `tools/perfil.local.json` con quién es el titular.

LOS DATOS DEL TITULAR NO VIVEN AQUÍ. Salen de `tools/perfil.local.json` y
`tools/identidad.local.json`, los dos gitignored, igual que hace `publicar.py`: este fichero se
publica, así que meter su nombre y su fecha de nacimiento lo convertiría en la ficha que
pretende proteger.

NUNCA DEVUELVE EL VALOR AJENO. Si el informe es de otra persona, el veredicto dice que no es
suyo y NO repite el nombre ni la fecha que encontró: sería meter el dato de un tercero en la
transcripción, que es la misma clase de fuga que evita todo lo demás del muro.
"""
import io
import json
import os
import re
import unicodedata

ROOT = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Etiquetas que de verdad identifican al PACIENTE. `nombre`, `apellidos`, `filiacion` y
# `patient` a secas estaban aquí y hubo que sacarlos: casaban con «Nombre del estudio»,
# «Nombre del centro» y «Apellidos del facultativo». Medido sobre sus 498 transcripciones .md,
# «nombre» a secas disparaba 23 falsos positivos él solo.
ETIQUETAS_NOMBRE = ("nombre del paciente", "nombre y apellidos", "nom i cognoms",
                    "patient name", "paciente", "pacient", "patient")
# El orden importa: `_valores_etiquetados` corta en la PRIMERA etiqueta que casa en cada línea,
# así que «patient name» va antes que «patient». Y «patient» a secas no pega con «Patient ID:»
# ni «Patient Position:» porque detrás exige un separador inmediato, no otra palabra.
ETIQUETAS_DOB = ("fecha de nacimiento", "fecha nacimiento", "f. nacimiento", "f.nacimiento",
                 "f. nac", "f.nac", "fnac", "fec. nac", "data de naixement", "data naixement",
                 "date of birth", "birth date", "nacimiento", "nascimento", "dob")
# Etiqueta + separador (: = - espacio largo) + valor hasta fin de línea o doble espacio/pipe.
_SEP = r"\s*[:=\-–]\s*|\s{2,}"
_MAX_VALOR = 90


def _norm(s):
    """minúsculas sin acentos: 'Fecha de Nacimiento' y 'FECHA DE NACIMIENTO' son la misma."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# Casa base: los overlays son gitignored y en un worktree no existen, así que la ventanilla
# rechazaba por identidad TODO lo que se abría desde una rama (deuda
# identidad-paciente-overlay-no-viaja-al-worktree, 25-sep-26). Mismo arreglo que
# `seguimiento._cargar_overlay`: primero el del checkout, si no, el de casa base. Con BTP_REPO
# puesto a propósito (tests, otra instalación) se respeta y no se busca en otro sitio: un test que
# quita el overlay tiene que ver «sin overlay», no el perfil real de la casa.
CASA_BASE = None if os.environ.get("BTP_REPO") else os.path.expanduser("~/claudecode")


def _overlay(nombre):
    for raiz in dict.fromkeys(r for r in (ROOT, CASA_BASE) if r):
        try:
            with io.open(os.path.join(raiz, "tools", nombre), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            continue
    return {}


def titular():
    """{'nombre','apellidos','nacimiento','dob_patrones'} desde los overlays. {} si no hay."""
    t = (_overlay("perfil.local.json").get("titular") or {})
    if not t.get("nombre"):
        return {}
    nac = t.get("nacimiento")
    nac = [nac] if isinstance(nac, str) else list(nac or [])
    ap = t.get("apellidos")
    ap = [ap] if isinstance(ap, str) else list(ap or [])
    return {"nombre": t["nombre"], "apellidos": ap, "nacimiento": nac,
            "dob_patrones": list(_overlay("identidad.local.json").get("dob_patrones") or [])}


def _valores_etiquetados(texto, etiquetas):
    """Valores de los campos ETIQUETADOS que aparezcan. Solo texto, nunca se devuelve fuera."""
    out = []
    n = _norm(texto)
    for linea_n, linea in zip(n.splitlines(), texto.splitlines()):
        for et in etiquetas:
            i = linea_n.find(et)
            if i < 0:
                continue
            resto = linea[i + len(et):]
            m = re.match(_SEP, resto)
            if not m:
                continue
            # El valor puede no estar en la MISMA celda que la etiqueta. En las transcripciones
            # en tabla markdown («| **F. Nacimiento:** | | 12/05/1990 |») entre etiqueta y valor
            # hay una celda vacía, y quedarse con el primer trozo devolvía «**»: la fecha real no
            # se veía y la ventanilla marcaba como AJENOS informes que sí son suyos — las tres
            # radiografías de tórax de 2024 (24-sep-2026). Se recorren las celdas y se coge la
            # primera con contenido de verdad, sin los asteriscos del markdown.
            for celda in re.split(r"\s{2,}|\||\t", resto[m.end():]):
                celda = celda.strip().strip("*_`~").strip()
                if celda:
                    out.append(celda[:_MAX_VALOR])
                    break
            break
    return out


def _es_su_nombre(valor, t):
    """¿Ese valor la nombra a ELLA? Basta el NOMBRE DE PILA, y el apellido NO basta.

    Las dos mitades de esta regla salen de los datos, no de la intuición:
      · exigir nombre Y apellido en el mismo campo marcaba como ajenas 53 transcripciones suyas,
        porque muchas escriben solo «Paciente: <nombre>» o abrevian el apellido.
      · aceptar el APELLIDO suelto sería peor que no comprobar nada: su padre tiene su propio
        caso clínico (Bellvitge) y COMPARTE APELLIDO con ella. Un informe suyo casaría por
        apellido y se serviría como si fuera de ella — exactamente el error que esta norma
        existe para impedir. El nombre de pila es lo que los distingue.
    """
    v = _norm(valor)
    return _norm(t["nombre"]) in v


def _solo_digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def _es_su_fecha(valor, t):
    """Compara por DÍGITOS: 14/02/1970, 14-02-1970, 14.02.1970 y el 19700214 de DICOM son la
    misma fecha, y el overlay no puede enumerar todas las formas que usa cada hospital."""
    v = _norm(valor)
    if any(_norm(f) in v for f in t["nacimiento"]):
        return True
    dv = _solo_digitos(valor)
    if dv:
        for f in t["nacimiento"]:
            df = _solo_digitos(f)
            if len(df) >= 6 and (df in dv or dv in df):
                return True
            if len(df) == 8 and len(dv) == 8 and sorted((df[:2], df[2:4])) == sorted((dv[:2], dv[2:4])) \
                    and df[4:] == dv[4:]:
                return True     # dd/mm vs mm/dd del mismo año
    for pat in t["dob_patrones"]:
        try:
            if re.search(pat, valor, re.I):
                return True
        except re.error:
            continue
    return False


# Proporción mínima de caracteres imprimibles para considerar que esto ES un documento de
# texto. Medido sobre su árbol clínico real (28.440 ficheros, recuento sin leer contenido):
# 23.851 no tienen extensión y 4.521 son `.dcm` (imagen DICOM). Leídos como UTF-8 producen
# basura, y esa basura casaba por accidente con las etiquetas: 830 salían `otro_paciente`, 721
# de ellos `.dcm`. Enganchar eso a un rechazo habría tumbado la ventanilla. Un binario no se
# juzga: se dice que no se puede acreditar y ya.
_MIN_IMPRIMIBLE = 0.85
# Formatos que NUNCA se juzgan por el contenido decodificado, pase el ratio lo que pase. Un PDF
# poco comprimido puede superar el umbral de imprimibles y entonces su basura casa con las
# etiquetas por accidente: 5 PDFs salían `otro_paciente` y no hay forma de saber si eran de
# verdad de otra persona o ruido. Ante la duda, no se juzga — que es la misma regla que el
# resto del muro.
EXT_NO_TEXTO = (".pdf", ".dcm", ".zip", ".gz", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
                ".dic", ".ima", ".nii", ".docx", ".xlsx", ".pptx", ".mp4", ".mov", ".heic")


def es_textual(texto, ruta=None, muestra=4000):
    """¿Esto es un documento de texto, o un binario leído como si lo fuera?"""
    if ruta and str(ruta).lower().endswith(EXT_NO_TEXTO):
        return False
    m = texto[:muestra]
    if not m.strip():
        return False
    ok = sum(1 for c in m if c.isprintable() or c in "\n\r\t")
    return (ok / len(m)) >= _MIN_IMPRIMIBLE


def verificar(texto, ruta=None):
    """(veredicto, explicación-SIN-datos-ajenos). Ver los veredictos en la cabecera."""
    t = titular()
    if not t:
        return "sin_overlay", ("no hay tools/perfil.local.json: esta instalación no sabe quién "
                               "es el titular, así que no puede acreditar nada")
    if not es_textual(texto, ruta):
        return "no_textual", ("esto no es un documento de texto (imagen, DICOM, PDF…): la "
                              "filiación no se puede leer así, y esta ventanilla sirve texto")
    nombres = _valores_etiquetados(texto, ETIQUETAS_NOMBRE)
    fechas = _valores_etiquetados(texto, ETIQUETAS_DOB)
    nombre_ok = any(_es_su_nombre(v, t) for v in nombres)
    fecha_ok = any(_es_su_fecha(v, t) for v in fechas)
    # Filiación etiquetada que NO es la suya: el caso que la norma teme. No se repite el valor.
    nombre_ajeno = bool(nombres) and not nombre_ok
    fecha_ajena = bool(fechas) and not fecha_ok
    if nombre_ajeno or fecha_ajena:
        que = []
        if nombre_ajeno:
            que.append("el nombre del paciente")
        if fecha_ajena:
            que.append("la fecha de nacimiento")
        que = " ni ".join(que)
        # ¿La nombra a ELLA en alguna parte, aunque el encabezado diga otra cosa? Medido sobre
        # sus 498 transcripciones: de las 110 con filiación ajena, 41 SÍ la mencionan en el
        # cuerpo (un estudio de comparación, un encabezado heredado del export) y 69 no la
        # mencionan en ninguna parte. Rechazar las 110 habría cerrado 41 informes suyos —y 93
        # de las 110 están en «03 · Imagen», justo sus PET-TC, el dato más importante que
        # tiene. Solo se RECHAZA cuando el documento no la nombra en absoluto; si aparece, se
        # sirve con el aviso más fuerte y que lo confirme una persona.
        if _norm(t["nombre"]) in _norm(texto):
            return "ambiguo", ("%s del encabezado NO es la suya, aunque el documento sí la "
                               "menciona en alguna parte (no se reproduce el dato ajeno)" % que)
        return "otro_paciente", ("%s que trae el documento NO es la suya, y a ella no la nombra "
                                 "en ninguna parte (no se reproduce aquí: es el dato de otra "
                                 "persona)" % que)
    if nombre_ok and fecha_ok:
        return "coincide", "el documento dice su nombre y su fecha de nacimiento"
    if nombre_ok or fecha_ok:
        return "parcial", ("solo consta %s; la norma pide los dos para acreditar"
                           % ("el nombre" if nombre_ok else "la fecha de nacimiento"))
    return "no_consta", ("el documento no trae filiación etiquetada (ni «Paciente:» ni «Fecha de "
                         "nacimiento:»): estar en su carpeta no prueba que sea suyo")


# `no_textual` SE SIRVE: hasta hoy la ventanilla servía esos bytes tal cual, y convertir esto en
# un rechazo sería cambiar su comportamiento por la puerta de atrás, en nombre de una norma que
# habla de otra cosa. Se sirve con aviso, que es lo honesto: no se ha acreditado nada.
SIRVE = {"coincide": True, "parcial": True, "no_consta": True, "no_textual": True,
         "ambiguo": True, "otro_paciente": False, "sin_overlay": False}
PIDE_AVISO = ("parcial", "no_consta", "no_textual", "ambiguo")
