#!/usr/bin/env python3
"""tools/deid.py — De-identificador del NÚCLEO F3b (capa de CONTEXTO para el cerebro local/gratis).

Plan-motor: typed-swinging-wand / F3b. Objetivo: que el cerebro GRATIS/LOCAL (Ollama, egress-cero)
pueda responder CON contexto del caso SIN ver jamás clínico/PII en crudo. Esta capa convierte texto
del caso → versión DE-IDENTIFICADA sin los identificadores que estos patrones reconocen (PII + huella
clínica/genómica específica + términos vetados del muro). Lo que no reconocen pasa: ver HONESTIDAD.

REUSA, NO REINVENTA (SOLO LECTURA sobre `borde.py`):
  · Las MISMAS regex y deny-lists del borde (`borde._RE_*`, `_NOMBRE_TITULAR`, y los deny-lists de
    `seguimiento` que el borde ya consolida: `_TERMINOS_VETADOS`, `_NOMBRES_DENY`).
  · El MISMO normalizador (`borde._normalizar`: NFKD + quita combinantes + zero-width) para cerrar
    la evasión por homoglifo/acento falso/espaciado, ANTES de enmascarar.
  · La revalidación usa el propio juez del muro: tras de-identificar, `borde.clasificar()` sobre la
    salida no debe ver nada. Si ve algo, la de-id se considera fallida. OJO: es el MISMO detector
    que redacta, así que esto caza restos, no demuestra anonimato (auditoría 3.3, 24-sep-26).

POR QUÉ existe (y no basta `borde.de_identificar`): el `de_identificar` de F0 es "mínimo" — cubre un
subconjunto de las clases y NO los deny-lists ni variantes cortas/genotipos/citobandas/exones. Aquí se
enmascara **la UNIÓN de todo lo que `clasificar` detecta**, en orden de especificidad, de forma que la
salida pase el juez del muro. Es un superconjunto estricto de F0, no una copia divergente.

HONESTIDAD (defensa en profundidad, no garantía única): una de-id por patrones SIEMPRE es porosa
(notación nueva, ofuscación que el normalizador no prevé). Por eso F3b es DEFENSA EN PROFUNDIDAD: la
garantía REAL es ARQUITECTÓNICA — lo que necesita juicio clínico va a Claude (trusted), y el cerebro
gratis/local solo recibe contexto ya de-identificado Y revalidado por `borde.clasificar`. La de-id
reduce la superficie; el muro la cierra. Cada salida se REVALIDA: si algo escapa al patrón, el juez
del muro lo caza y el contexto se DESCARTA (fail-closed), no se envía a medio limpiar.

CAPA NER (25-sep-26, P4; idea de {{CONTACTO}} (https://contacto), con su agente KAI,
revisión del 25-sep-2026). `ner=True` / `--ner` añade ANTES del regex un detector distinto, el
modelo del BSC entrenado con historias reales (`deid_ner.py`, en `.venv-deid`, egress 0). Se unen
los tramos de los dos, nunca se intersectan. Opcional hasta que el banco de medida diga cuánto
suma: arranca en ~6 s por llamada y `contexto_caso` lo llamaría por cada pasaje. Si se pide y
falla, NO se degrada en silencio al regex: `de_identificar_verificado` devuelve None.

DICCIONARIO PROPIO (25-sep-26, P4 paso 4; misma atribución que la capa NER). Siempre activo:
los identificadores CONCRETOS de la titular que ya viven en los overlays gitignored (titular de
`perfil.local.json`; ids y patrones de nacimiento de `identidad.local.json`; lugares de su ruta de
`nombres.local.json`), del checkout y de casa base, unidos. Un regex genérico caza «un DNI»; esto
caza SU DNI aunque venga sin etiqueta, con puntos o partido. Aquí no se escribe ningún valor.

CLI:
  python3 tools/deid.py "<texto>"            # imprime el texto de-identificado + nº de redacciones
  python3 tools/deid.py --ner "<texto>"      # lo mismo con la capa NER del BSC delante
  python3 tools/deid.py --check "<texto>"    # ¿quedan identificadores detectables? (exit 0 no / 3 sí)
  python3 tools/deid.py --selftest
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde  # noqa: E402 — SOLO LECTURA: regex + deny-lists + normalizador + juez del muro
import seguimiento as seg  # noqa: E402 — deny-lists del muro (la misma fuente que usa el borde)

MARCA = "[REDACTADO]"

# Fechas: el borde NO las trata como sensibles (una fecha sola no identifica), pero en el CONTEXTO
# del caso una fecha concreta (cita, biopsia, viaje) SÍ es un identificador cuasi-único combinado con
# el resto. F3b las enmascara de más (defensa en profundidad propia de la capa de contexto; el borde
# queda intacto). Cubre ISO (2026-07-08), europeo (08/07/2026, 8-7-26) y "8 de julio[ de 2026]".
_MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_RE_FECHA = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"
    r"|\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
    r"|\b\d{1,2}\s+de\s+(?:%s)(?:\s+de\s+\d{2,4})?\b" % _MESES, re.I)

# Orden de enmascarado: de MÁS específico/largo a MÁS corto, para que un patrón amplio no se
# coma el contexto antes de que los específicos hagan su trabajo, y para no dejar restos. Cada
# entrada reusa una regex/deny-list YA definida en el borde (no se redefine ninguna aquí).
# Cubre la UNIÓN de detectores de borde.clasificar():
#   PII:        email, DNI/NIE, teléfono, nombre de {{TITULAR}}, nombres en deny-list
#   genómico:   HGVS (p./c.), HGVS corto (R175H), HLA, rsID, coord. cromosómica, cromosoma,
#               citobanda (del 17p), exón, genotipo VCF (0/1)
#   clínico:    marcador (TP53, Ki-67…), cifra clínica (ER 80%)
#   muro:       términos vetados (vacuna/contacto/neoantíg…)
_REGEX_ORDENADAS = (
    ("email", borde._RE_EMAIL),
    ("dni", borde._RE_DNI),
    ("telefono", borde._RE_PHONE),
    # NHC: el juez del muro lo caza desde el 2-sep-2026, pero si no se REDACTA aqui la de-id
    # sale «SUCIA» y no sirve. Detectar y enmascarar tienen que ir juntos o esto no cierra.
    ("nhc", borde._RE_NHC),
    ("hgvs", borde._RE_VAR),
    ("hla", borde._RE_HLA),
    ("rsid", borde._RE_RS),
    ("coordenada", borde._RE_CHR),
    ("cromosoma", borde._RE_CHR_W),
    ("citobanda", borde._RE_CITO),
    ("exon", borde._RE_EXON),
    ("cifra_clinica", borde._RE_CLIN_PCT),
    ("fecha", _RE_FECHA),
    ("genotipo", borde._RE_GT),
    ("marcador", borde._RE_CLIN),
    ("hgvs_corto", borde._RE_VAR_CORTO),
    ("nombre_titular", borde._NOMBRE_TITULAR),
)


def _enmascarar_deny(texto):
    """Enmascara nombres propios en deny-list y términos vetados del muro, palabra a palabra,
    sobre el texto YA normalizado (mismo criterio que `clasificar`). Reusa los deny-lists del muro
    vía `seguimiento` (la misma fuente que consolida el borde). Devuelve (texto, n)."""
    n = 0

    # nombres propios de la deny-list (match de palabra completa, case-insensitive)
    def _rep_palabra(m):
        nonlocal n
        w = m.group(0)
        if w.lower() in seg._NOMBRES_DENY:
            n += 1
            return MARCA
        return w

    texto = re.sub(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]+", _rep_palabra, texto)

    # términos vetados del muro (substring, case-insensitive) — vacuna/contacto/neoantíg…
    for term in seg._TERMINOS_VETADOS:
        if not term:
            continue
        patt = re.compile(re.escape(term), re.I)
        texto, k = patt.subn(MARCA, texto)
        n += k
    return texto, n


_OVERLAYS_DIR = [os.path.dirname(os.path.abspath(__file__)),
                 os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools")]
_DICC = None


def _leer_overlay(nombre):
    """Une (no elige) el overlay del checkout y el de casa base, como `seguimiento._cargar_overlay`:
    en un worktree el fichero no existe y la capa se quedaría vacía sin avisar."""
    out, vistos = [], set()
    for d in _OVERLAYS_DIR:
        ruta = os.path.realpath(os.path.join(d, nombre))
        if ruta in vistos:
            continue
        vistos.add(ruta)
        try:
            with open(ruta, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            pass
    return out


def _lista(v):
    return [v] if isinstance(v, str) else [x for x in (v or []) if isinstance(x, str)]


def _patron_literal(valor):
    """Regex de un valor concreto, tolerante a cómo llega escrito: sin tildes ni mayúsculas (se
    aplica sobre el texto normalizado), y en los alfanuméricos con separadores entre caracteres
    («12.345.678», «12 345 678»)."""
    norm = borde._normalizar(valor)[0].strip()
    if not norm:
        return None
    if re.fullmatch(r"[A-Za-z0-9]+", norm) and any(c.isdigit() for c in norm):
        cuerpo = r"[\s.\-/]?".join(re.escape(c) for c in norm)
        return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % cuerpo, re.I)
    # frontera = no letra ni dígito, y el guion bajo cuenta como separador: su nombre llega también
    # como «TITULAR_GONZALEZ_PEREZ» en nombres de fichero, y con `\b` eso no casaba (25-sep-26)
    palabras = [re.escape(w) for w in norm.split()]
    return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % r"[\s_]+".join(palabras), re.I)


def _diccionario():
    """[regex] con los identificadores concretos de la titular. Se carga una vez."""
    global _DICC
    if _DICC is not None:
        return _DICC
    valores, patrones = set(), []
    for perfil in _leer_overlay("perfil.local.json"):
        t = perfil.get("titular") or {}
        for k in ("nombre", "apellidos", "nacimiento", "contactos", "telefonos"):
            valores.update(_lista(t.get(k)))
    for ident in _leer_overlay("identidad.local.json"):
        valores.update(_lista(ident.get("ids")))
        # identificadores suyos que SOLO sirven para tapar (CIP, SNS, NASS, códigos de estudios y
        # laboratorios, variantes de su nombre, su calle): clave aparte para no cambiar qué
        # reconoce `subir_historial_drive` con `ids` (25-sep-26, banco sobre su muestra)
        valores.update(_lista(ident.get("deid_terminos")))
        for p in _lista(ident.get("dob_patrones")):
            try:
                patrones.append(re.compile(p, re.I))
            except re.error:
                pass
    for nombres in _leer_overlay("nombres.local.json"):
        valores.update(_lista(nombres.get("lugares_ruta")))
    # de más largo a más corto: «Fernández Ortega» antes que «Ortega» no deja restos
    for v in sorted(valores, key=len, reverse=True):
        rx = _patron_literal(v)
        if rx:
            patrones.append(rx)
    _DICC = patrones
    return _DICC


def _enmascarar_diccionario(texto):
    n = 0
    for rx in _diccionario():
        texto, k = rx.subn(MARCA, texto)
        n += k
    return texto, n


# Campos de filiación ETIQUETADOS (25-sep-26, P4 paso 5). El banco de medida sobre MEDDOCAN lo
# enseñó: 55 de 500 nombres de paciente se escapaban aun con el NER delante, y casi todos eran el
# valor de «Nombre: Ignacio.». El modelo del BSC no tiene etiqueta de nombre de paciente (en
# CONTACTO-I venían tapados). Las etiquetas salen de `identidad_paciente` (medidas sobre sus 498
# transcripciones), más «nombre»/«apellidos» a secas: allí se quitaron porque para DECIDIR de quién
# es un informe daban falsos positivos («Nombre del estudio»); aquí exigen el separador pegado
# («Nombre:»), y un falso positivo solo tapa unas palabras de más. Se tapa el VALOR, no la etiqueta.
try:
    from identidad_paciente import ETIQUETAS_NOMBRE as _ET_NOMBRE_FILIACION
except Exception:                                   # sin él, las de siempre: no se queda vacía
    _ET_NOMBRE_FILIACION = ("nombre del paciente", "nombre y apellidos", "nom i cognoms",
                            "patient name", "paciente", "pacient", "patient")
# Campos de ASISTENCIA (25-sep-26, banco sobre su muestra): en sus informes escapaban «Sexo: F»,
# el nº de cama, el «Nombre del solicitante», el centro de salud y los números de episodio y de
# muestra. El valor de un campo con estas etiquetas es un identificador sea cual sea su forma, así
# que se tapa entero. Los servicios, diagnósticos y fechas NO están aquí (las fechas las tapa el
# detector de fechas; lo clínico no se toca).
_ET_ASISTENCIA = {
    "sexo", "edad", "f.nacim (edad)", "f.nacim(edad)", "f.nac(edad)", "f.nac (edad)", "cama", "n cama", "no cama", "nº cama", "n.º cama",
    "nhc", "n.h.c.", "no historia", "nº historia", "n historia", "historia clinica",
    "no historia clinica", "nº historia clinica", "dni", "nie", "dni/nie", "dni / nie",
    "dni/nie/ppte", "nass", "n.s.s.", "nº s.s.", "no s.s.", "nss", "sns", "c.i.p.", "cip",
    "c.i.p.sns", "c.i.p.aut.", "c.i.p.a.", "cip-sns", "cip-aut", "c.i.p.autonomico",
    "cip de c. autonoma", "cip de c autonoma", "cip autonomico", "nº tsi",
    "no acto", "nº acto", "nº acto c.", "no peticion", "nº peticion", "no muestra", "nº muestra",
    "no estudio", "nº estudio", "numero prueba", "episodio", "no episodio", "nº episodio",
    "ref. externa", "referencia", "lab id", "patient initials", "patient mrn", "biopsia",
    "registro biopsia", "analisis",
    "domicilio", "direccion", "poblacion", "codigo postal", "municipio", "provincia",
    "telefono", "telf.", "tlf.", "centro de salud", "centro a.p.", "centro de extraccion",
    "c. extraccion", "denominacion del centro", "procedencia", "hospital",
    "nombre del solicitante", "solicitante", "medico solicitante", "responsable 1",
    "responsable 2", "nombre responsable1", "nombre responsable2", "nombre patologo responsable",
    "atendida por", "atendido por", "fdo", "fdo.", "firmado por", "reporting physician",
}
_ET_CAMPO = sorted({borde._normalizar(e)[0].lower() for e in
                    set(_ET_NOMBRE_FILIACION) | _ET_ASISTENCIA |
                    {"nombre", "apellidos", "apellido", "cognoms", "primer apellido",
                     "segundo apellido", "1a apellido", "1o apellido", "2o apellido",
                     "1ª apellido", "1º apellido", "2º apellido", "nombre paciente",
                     "nombre y apellidos"}},
                   key=len, reverse=True)
_RE_CAMPO_NOMBRE = re.compile(
    # etiqueta + separador: «:»/«=» o, en tabla markdown, el cierre de su celda «** |»
    r"(?<![\w])(?:%s)\s*[*_]*\s*(?:[:=]|\|)[\s|*_]*(?P<v>[^\n|\t]{1,80}?)"
    # el valor acaba en fin de línea, celda, dos espacios o la siguiente etiqueta de UNA palabra
    r"(?=\s{2,}|\s*\||\t|\n|$|\s+[A-Za-z.º]{2,12}\s*:)" % "|".join(re.escape(e) for e in _ET_CAMPO),
    re.I | re.M)


def _campos_nombre(norm):
    """[(ini, fin)] de los valores de campos de nombre, sin espacios ni puntuación de los bordes."""
    out = []
    for m in _RE_CAMPO_NOMBRE.finditer(norm):
        a, b = m.span("v")
        # se recortan espacios y adornos markdown, no los corchetes de un [REDACTADO] previo ni los
        # paréntesis de «(30 Años)»: recortarlos dejaba «[[REDACTADO]» o «[REDACTADO])»
        while a < b and norm[a] in " \t*_":
            a += 1
        while b > a and norm[b - 1] in " \t*_.,;:":
            b -= 1
        if b > a:
            out.append((a, b))
    return out


class NERNoDisponible(RuntimeError):
    """Se pidió la capa NER y no respondió. Quien la pidió no puede recibir un resultado sin ella."""


TIMEOUT_NER_S = 120


def _python_ner():
    """Python de `.venv-deid`: variable de entorno, raíz de este checkout o casa base (los worktrees
    no llevan el venv; vive una vez, en la casa base, como los demás `.venv-*`)."""
    cand = [os.environ.get("BTP_VENV_DEID", "")]
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cand.append(os.path.join(raiz, ".venv-deid"))
    try:
        comun = subprocess.run(["git", "-C", raiz, "rev-parse", "--path-format=absolute",
                                "--git-common-dir"], capture_output=True, text=True, timeout=5)
        if comun.returncode == 0 and comun.stdout.strip():
            cand.append(os.path.join(os.path.dirname(comun.stdout.strip()), ".venv-deid"))
    except Exception:
        pass
    for c in cand:
        py = os.path.join(c, "bin", "python") if c else ""
        if py and os.path.isfile(py):
            return py
    return None


def _spans_ner(texto):
    """[(ini, fin, etiqueta, score)] del modelo del BSC. Lanza NERNoDisponible si no hay respuesta
    válida: nada de devolver [] como si el texto no tuviera identificadores."""
    py = _python_ner()
    if not py:
        raise NERNoDisponible("no encuentro .venv-deid (ver tools/deid_ner.py)")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deid_ner.py")
    try:
        r = subprocess.run([py, script], input=texto, capture_output=True, text=True,
                           timeout=TIMEOUT_NER_S)
    except subprocess.TimeoutExpired:
        raise NERNoDisponible("el NER no respondió en %d s" % TIMEOUT_NER_S)
    if r.returncode != 0:
        raise NERNoDisponible("el NER salió con %d: %s" % (r.returncode, (r.stderr or "")[-200:]))
    try:
        datos = json.loads(r.stdout)
        return [(int(d["ini"]), int(d["fin"]), d["etiqueta"], d["score"]) for d in datos]
    except Exception as e:
        raise NERNoDisponible("salida del NER ilegible: %r" % e)


def _enmascarar_spans(texto, spans):
    """Tapa los tramos [ini, fin) (fusionando los que se tocan o solapan). Devuelve (texto, n)."""
    fusion = []
    for a, b, *_ in sorted(spans):
        if a >= b or a < 0 or b > len(texto):
            continue
        if fusion and a <= fusion[-1][1] + 1:
            fusion[-1][1] = max(fusion[-1][1], b)
        else:
            fusion.append([a, b])
    for a, b in reversed(fusion):
        texto = texto[:a] + MARCA + texto[b:]
    return texto, len(fusion)


def _spans_ner_lote(textos):
    """Como `_spans_ner` para muchos textos cargando el modelo UNA vez (el banco de medida pasa
    cientos de documentos; a ~6 s de arranque por llamada serían media hora solo de cargas)."""
    py = _python_ner()
    if not py:
        raise NERNoDisponible("no encuentro .venv-deid (ver tools/deid_ner.py)")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deid_ner.py")
    r = subprocess.run([py, script, "--lote"], input=json.dumps(textos, ensure_ascii=False),
                       capture_output=True, text=True, timeout=TIMEOUT_NER_S * max(1, len(textos)))
    if r.returncode != 0:
        raise NERNoDisponible("el NER salió con %d: %s" % (r.returncode, (r.stderr or "")[-200:]))
    datos = json.loads(r.stdout)
    if len(datos) != len(textos):
        raise NERNoDisponible("el NER devolvió %d resultados para %d textos" % (len(datos), len(textos)))
    return [[(int(d["ini"]), int(d["fin"]), d["etiqueta"], d["score"]) for d in doc] for doc in datos]


def _mapa_normalizado(texto):
    """(texto_normalizado, mapa) con mapa[j] = índice en `texto` del que sale el carácter j. El
    normalizador del borde va carácter a carácter (NFKD + quitar combinantes y zero-width), así que
    normalizar por trozos da lo mismo que normalizar entero; `test_deid_eval` lo comprueba."""
    partes, mapa = [], []
    for i, c in enumerate(texto):
        n = borde._normalizar(c)[0]
        partes.append(n)
        mapa.extend([i] * len(n))
    return "".join(partes), mapa


def detectar(texto, ner=False, spans_ner=None):
    """[(ini, fin, capa)] sobre el texto ORIGINAL: lo que cada capa ve, sin tapar nada. Es lo que
    necesita el banco de medida (`deid_eval.py`) para puntuar por entidad; `de_identificar` sigue
    siendo lo que se usa en producción. Cada capa mira el texto sin tapar, así que esto es la UNIÓN
    de lo que detectan; `de_identificar` tapa en cadena y puede diferir donde un tapado previo
    rompe el contexto del siguiente (por eso el banco mide también las fugas en su salida real)."""
    out = []
    if ner:
        for a, b, et, _s in (spans_ner if spans_ner is not None else _spans_ner(texto)):
            out.append((a, b, "ner:" + et))
    norm, mapa = _mapa_normalizado(texto)

    def _add(m, capa):
        if m.end() > m.start():
            out.append((mapa[m.start()], mapa[m.end() - 1] + 1, capa))

    for rx in _diccionario():
        for m in rx.finditer(norm):
            _add(m, "diccionario")
    for a, b in _campos_nombre(norm):
        out.append((mapa[a], mapa[b - 1] + 1, "regex:campo_nombre"))
    for etq, rx in _REGEX_ORDENADAS:
        for m in rx.finditer(norm):
            _add(m, "regex:" + etq)
    for m in re.finditer(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]+", norm):
        if m.group(0).lower() in seg._NOMBRES_DENY:
            _add(m, "lista:nombres")
    for term in seg._TERMINOS_VETADOS:
        if term:
            for m in re.finditer(re.escape(term), norm, re.I):
                _add(m, "lista:vetados")
    return out


def de_identificar(texto, ner=False):
    """(texto_deid, n_redacciones). Normaliza igual que el borde y enmascara la UNIÓN de todas las
    clases que `borde.clasificar` detecta. NO envía nada: solo transforma. El llamante DEBE revalidar
    con `sin_identificadores_detectados()` antes de usar la salida (fail-closed).

    `ner=True`: primero la capa NER del BSC sobre el texto ORIGINAL (la normalización quita tildes y
    el modelo se entrenó con ellas); luego todo lo de siempre. Lanza NERNoDisponible si falla."""
    if not isinstance(texto, str) or not texto.strip():
        return ("" if not isinstance(texto, str) else texto), 0
    n = 0
    if ner:
        texto, n = _enmascarar_spans(texto, _spans_ner(texto))
    # MISMO normalizador que el borde: cierra homoglifo/acento falso/zero-width antes de enmascarar.
    norm, _low = borde._normalizar(texto)
    out, k = _enmascarar_diccionario(norm)
    n += k
    out, k = _enmascarar_spans(out, _campos_nombre(out))
    n += k
    for _etq, rx in _REGEX_ORDENADAS:
        out, k = rx.subn(MARCA, out)
        n += k
    out, k = _enmascarar_deny(out)
    n += k
    # colapsa marcadores contiguos para que el contexto sea legible ("[REDACTADO] [REDACTADO]" → uno)
    out = re.sub(r"(?:%s)(?:\s+(?:%s))+" % (re.escape(MARCA), re.escape(MARCA)), MARCA, out)
    return out, n


def sin_identificadores_detectados(texto):
    """(ok, motivo) — ¿quedan identificadores QUE ESTOS PATRONES SEPAN VER? ok=True si ni el juez
    del muro `borde.clasificar` (PII + clínico/genómico + términos vetados) ni el detector de fechas
    de F3b encuentran nada. Si algo escapó, False y el contexto NO debe enviarse.

    ⚠️ NO es un certificado de anonimato (24-sep-26, auditoría externa, hallazgo 3.3). Redacta y
    verifica con los MISMOS patrones: un nombre, un domicilio o un diagnóstico que no reconocen
    pasan los dos pasos igual (reproducido: 0 redacciones y «limpio»). Por eso se llamaba `limpio`
    y ya no. Lo que decide si algo del caso puede salir es su PROCEDENCIA (`ia.ask(...,
    sensible_forzado=True)`, ver `contexto_caso`), no este veredicto."""
    sensible, motivo = borde.clasificar(texto)
    if sensible:
        return False, motivo
    if _RE_FECHA.search(texto):
        return False, "fecha superviviente (cuasi-identificador)"
    return True, "sin identificadores detectados"


# Alias DEPRECADO: el nombre prometía más de lo que el código sabe. No lo uses en código nuevo.
limpio = sin_identificadores_detectados


def de_identificar_verificado(texto, procedencia=None, ner=False):
    """(texto_deid|None, n, ok, motivo). De-identifica Y revalida con los mismos patrones. Si queda
    algo que reconocen, devuelve texto=None (fail-closed): el contexto se DESCARTA, nunca se manda a
    medio redactar. `ok=True` significa «sin identificadores detectados», no «anónimo».

    `procedencia="N2"` (lo que sale del caso): el motivo lo dice. La redacción es la misma; lo que
    cambia es que quien llama NO puede tratar la salida como no sensible (ver `contexto_caso`)."""
    try:
        deid, n = de_identificar(texto, ner=True) if ner else de_identificar(texto)
    except NERNoDisponible as e:
        return None, 0, False, "capa NER pedida y caída: %s" % e
    ok, motivo = sin_identificadores_detectados(deid)
    if ok and procedencia == "N2":
        motivo = "sin identificadores detectados — procedencia N2: sigue siendo sensible"
    return (deid if ok else None), n, ok, motivo


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def _selftest():
    import subprocess
    r = subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "tests", "test_deid.py")])
    return r.returncode


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--selftest":
        return _selftest()
    ner = "--ner" in argv
    argv = [a for a in argv if a != "--ner"]
    if argv and argv[0] == "--check":
        deid, n, ok, motivo = de_identificar_verificado(" ".join(argv[1:]), ner=ner)
        print(("◻️ sin identificadores detectados (no certifica anonimato)" if ok else "🛑 quedan identificadores")
              + " — %s  (redacciones: %d)" % (motivo, n))
        if ok:
            print(deid)
        return 0 if ok else 3
    try:
        deid, n = de_identificar(" ".join(argv), ner=ner)
    except NERNoDisponible as e:
        print("🛑 capa NER pedida y caída: %s. No devuelvo nada a medias." % e, file=sys.stderr)
        return 1
    ok, motivo = sin_identificadores_detectados(deid)
    print(deid)
    print("\n— %d redacciones · %s (%s)" %
          (n, "sin identificadores detectados" if ok else "quedan identificadores", motivo), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
