#!/usr/bin/env python3
"""tools/puerta_n1.py — la Puerta de N1: lo que entra en `~/Laminillas-N1/` o sale hacia un
modelo de visión, mirado ANTES y fail-closed.

POR QUÉ EXISTE (1-oct-26, plan «laminillas DFCI», F1.4). Las copias de revisión de sus láminas
(píxeles sin identificar, nombre opaco) pueden salir del Mac hacia otros modelos de visión y a una
GPU en la nube (decisión 3 y 6 de {{TITULAR}}). Lo que no puede salir es lo que la identifica: su
nombre, una accesión de anatomía patológica, una fecha, un teléfono, un canario sembrado. La puerta
lo mira en los textos (CSV, GeoJSON, manifiesto, prompts) y la usan `exporta_n1`, `vision_n1` y
`nube_n1`, siempre en el proceso padre, nunca en uno con red.

QUÉ MIRA (el plan, «Puerta de N1»):
  · ABORTA si `deid.detectar` ve un tramo de capa `diccionario`, `regex:campo_nombre`,
    `regex:email`, `regex:dni`, `regex:nhc`, `regex:nombre_titular` o `lista:nombres`; si casa
    `caso_publico._RE_CODIGO_AP` o la forma laxa propia de la accesión; si `borde.hay_canario`;
    con las regex propias de fecha, teléfono y MRN; y con una casi-coincidencia con el nombre o
    apellidos de la titular (también pegados, en CamelCase o con un carácter invisible en medio).
  · IGNORA las capas clínicas y genómicas (`regex:marcador`, `regex:cifra_clinica`, `hgvs*`, …) y
    `lista:vetados`: el texto N1 habla de Ki67 y RE a propósito.
  · NO USA `regex:fecha`, `regex:telefono` ni `identificador_directo()`: saltan con rangos
    («38.5-44.1»), tablas y versiones, y la última devuelve una tupla.
  · CSV y JSON/GeoJSON por ESQUEMA: lo que parsea como número no pasa por los detectores de texto,
    pero un número entero de ≥9 cifras (también escrito «612345678.0» o «6.12345678e8») y una
    fecha AAAAMMDD se rechazan, igual que una cabecera o clave de identificador (MRN, NHC…).
    Cabeceras, claves y cadenas pasan por los detectores. En un GeoJSON, ninguna cadena (no solo
    las de `properties`) puede llevar una ruta o un nombre de imagen.

AMPLIACIONES DEL 1-OCT-26 (revisión del muro, hallazgos 11-21): accesión con espacio, minúscula,
punto o prefijo pegado; fechas en formato EE. UU., año delante, AAAAMMDD, mes abreviado o sin «de»;
móvil 3-3-3/3-2-2-2, fijo 2-3-2-2, prefijo 00 y formato NANP; MRN. Las tiras hexadecimales largas
(sha256 del manifiesto, uuid de las features, el valor de una clave sha*/hash) se tapan para los
detectores de FORMA (accesión, fecha, teléfono): no identifican y los hacían saltar al azar (nueve
cifras seguidas dentro de un hash ya hacían saltar el de teléfono antes de esta revisión).

SEGUNDA PASADA (1-oct-26, verificadores independientes): todo detector mira además el texto
NORMALIZADO (`_normaliza`): sin caracteres de formato ni marcas combinantes (un nombre en NFD, como
los escribe macOS, o con U+034F en medio), NFKC (cifras y letras de ancho completo), homoglifos
cirílicos y griegos a latino, cualquier espacio Unicode (NBSP, U+2009…) a espacio y cualquier raya
a guion. Accesión «26-28381» laxa y con separadores / : , o salto de línea; fechas con guion bajo,
espacio o guion delante, DDMMAAAA, «abril de 2026», mes delante en español, mes romano; teléfono
pegado tras «Tel.», con separadores mezclados o NANP con 00; MRN con cualquier separador, cifras
agrupadas, prefijo de dos letras, «MR#», «Record:», y cabeceras/claves que lo llevan dentro
(«Patient_MRN», «mrn_id», «M.R.N.», BOM). Y se quitan los falsos positivos que midieron (2-oct-26):
«Llama 70B 4096», «sizes 26 28381», «mean 26.28381»; «OCT4», «Oct-4», «c-Jun 2», «mar 1», «jun 2
genes», «sample 12 ago», «area 20260430.5»; «counts 701 233 845», «bbox 612 345 678 901»;
«rosacea», «prosaic», «metallothionein»; «record number 100000 nuclei»; «ratio a / b» y
«Tumor/Stroma/Other» en un GeoJSON; un área con forma de fecha o «1.5e9» en una columna de medida.
Coste DECLARADO que queda (fail-closed, sin forma de separarlo de una fuga): «NOV 3» y «seed
19790314»; tríos 3-3-3 con prefijo español válido («coords 812 345 678», «mean 612.345.678» y,
desde la tercera pasada, «total 612,345,678 px»); y palabras a distancia ≤2 de una parte de la
titular, que es la tolerancia del plan («stallion»).

TERCERA Y ÚLTIMA PASADA (2-oct-26, verificador independiente). Cerrado: la accesión corta de
caso_publico («24b-1043», «24B.1043», «24B_1043»; con espacios, «24B 1043» y «24 B 1043» solo con
año 2015-2029 delante y ≥4 cifras, para que «Llama 70B 4096» y «sizes 13B 1000» sigan pasando;
«24B 000 1043»; «24B·0001043»: los puntos medios se normalizan a punto); fechas con año de dos
cifras y el mes en letra («30-apr-26», «14-mar-79», «30abr26», «30 abr 26»), mes español delante
(«marzo 14»), «30 . 04 . 2026», «March the 14th», «30-IV-26» y «30-may-26»; teléfonos
«34612345678», «001 617 632 3000», «612,345,678», «612_345_678»; MRN «pt id», «PID», «EMPI» y
cualquier signo entre la etiqueta y el número («MRN ≠ 12345678»); en CSV/JSON, una CADENA que es un
número de ≥9 cifras (entre comillas o agrupado con coma, punto, guion bajo o espacio). Y el aviso
del primer envío: el evento solo cuenta si su id no se usó antes (en ningún destino) y su línea
del registro de salida.py nombra ESE destino y ESA confianza (su ts) y es posterior a ella.

LÍMITES DECLARADOS (regla de convergencia: solo los explota un agente que fabrica el texto o el
fichero a propósito; obstáculo y rastro, no frontera). Cada uno con su test que lo fija:
  · `test_limite_declarado_variantes_de_texto`: siguen PASANDO letras cambiadas por cifras o
    separadas una a una («Le0cadia», «L e o c a d i a»), cifras sueltas («6 1 2 3 4 5 6 7 8»,
    «2 4 B 0 0 0 1 0 4 3»), «6l2 345 678», etiquetas que no son de identificador («acct
    12345678»), un año suelto («dob 1979») y «3 nov». Cerrarlas haría saltar texto N1 corriente.
  · `test_limite_declarado_aviso_con_registro_escrito_a_mano`: el registro de salida.py es un
    fichero de casa base sin firma; una línea escrita a mano con un id nuevo, el destino, el ts de
    la confianza y la hora de ahora, más el evento sellado con ese id, callan el aviso. Queda el
    evento en la cadena.

FAIL-CLOSED: sin diccionario de la titular, sin deny-list de nombres o sin `canarios.json`
legible, la puerta no abre («puerta sin diccionario: no exporto»). Los tres cargadores de abajo
tragan el error y devuelven vacío; desde un worktree o una jaula sin overlays la puerta quedaría
abierta en silencio, que es justo lo que esto impide. La comprobación vive DENTRO de `motivos()`:
cualquier vía de librería (exporta_texto, revisar_csv, un pipeline futuro) la pasa, no solo los
CLI.

ESTADO: se fija con `_casa.state_dir()` ANTES de importar `borde` (sin eso, `borde.STATE` cuelga del
árbol del fichero y un worktree no ve ni los canarios ni las nubes confiadas de casa base).

Nunca imprime el texto que dispara: solo la capa y la posición.
"""
import calendar
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _casa  # noqa: E402

os.environ["BTP_STATE_DIR"] = _casa.state_dir()       # (a) del plan: antes de importar borde
import borde  # noqa: E402
import deid  # noqa: E402
import seguimiento as seg  # noqa: E402
from caso_publico import _RE_CODIGO_AP  # noqa: E402

if os.path.realpath(borde.STATE) != os.path.realpath(_casa.state_dir()):
    raise ImportError("puerta_n1: borde se importó antes con otro estado (%s); importa "
                      "puerta_n1 primero" % borde.STATE)

CAPAS_ABORTA = frozenset({"diccionario", "regex:campo_nombre", "regex:email", "regex:dni",
                          "regex:nhc", "regex:nombre_titular", "lista:nombres"})

_MESES_ES = ("enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
             "noviembre|diciembre")
# Ampliación (no estaba en el plan): el cuerpo N1 va en INGLÉS para el laboratorio, así que
# «30 April 2026» y «April 30, 2026» cuentan igual que «30 de abril».
_MESES_EN = ("January|February|March|April|May|June|July|August|September|October|November|"
             "December")
# Mes como palabra, sin distinguir mayúsculas: inglés entero y abreviado, español entero y sus
# abreviaturas propias. «set» (setiembre) no entra: «3 set of» es inglés corriente.
_MES_TOK = (r"(?:january|february|march|april|may|june|july|august|september|october|november|"
            r"december|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
            r"octubre|noviembre|diciembre|sept|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|ene|"
            r"abr|ago|dic)")
_MES_ES_ABR = r"(?:ene|feb|mar|abr|may|jun|jul|ago|sept?|set|oct|nov|dic)"
# Sin año, el mes ABREVIADO solo cuenta escrito como mes («Apr», «APR», «abr.»): en minúscula
# «mar 1», «jun 2 genes», «sep 3 channels» o «12 ago» son palabras corrientes (falsos positivos de
# la revisión, 2-oct-26). Con año, cualquier caja («30-abr-2026»). El nombre entero, siempre.
_MES_LARGO = (r"(?:january|february|march|april|may|june|july|august|september|october|november|"
              r"december|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
              r"octubre|noviembre|diciembre)")
_ABREV = ("sept", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
          "ene", "abr", "ago", "dic")
_MES_ABR_CAJA = r"(?:%s)" % "|".join("%s|%s" % (a.capitalize(), a.upper()) for a in _ABREV)
_MES_SIN_ANIO = r"(?:(?i:%s)|%s|(?i:%s)\.)" % (_MES_LARGO, _MES_ABR_CAJA, "|".join(_ABREV))
_DIA = r"(?:0?[1-9]|[12]\d|3[01])"
_MES = r"(?:0?[1-9]|1[0-2])"
_DIA2 = r"(?:0[1-9]|[12]\d|3[01])"
_MES2 = r"(?:0[1-9]|1[0-2])"
_ANIO = r"(?:19\d\d|20\d\d)"
_ROMANO = r"(?:XII|XI|IX|X|VIII|VII|VI|IV|V|III|II|I)"
# Inicio de una fecha numérica: ni una cifra, ni «.,/», ni «cifra-» delante. Un guion tras una
# letra sí («P-HE-14-03-1979», hallazgo 12 bis); tras una cifra no («512-12-12-2026»).
_FINI = r"(?<![\d.,/])(?<!\d-)"
# Una regex por forma (se recorren todas; antes era una sola con grupos numerados).
RES_FECHA = (
    re.compile(r"(?<!\d)\d{4}-%s-%s(?!\d)" % (_MES, _DIA)),                               # ISO
    re.compile(r"%s%s([/.\-])%s\1(?:%s|\d{2})(?!\d)" % (_FINI, _DIA, _MES, _ANIO)),       # d/m/a
    re.compile(r"%s%s([/.\-])%s\1(?:%s|\d{2})(?!\d)" % (_FINI, _MES, _DIA, _ANIO)),       # m/d/a
    re.compile(r"%s%s([/.\-])%s\1%s(?!\d)" % (_FINI, _ANIO, _MES, _DIA)),                 # a/m/d
    # Guion bajo o espacio de separador: solo con año de 4 cifras y día y mes de 2 (30_04_2026,
    # «2026 04 30»); «12 10 26» sería cualquier fila de cifras.
    re.compile(r"%s%s([_ ])%s\1%s(?!\d)" % (_FINI, _DIA2, _MES2, _ANIO)),
    re.compile(r"%s%s([_ ])%s\1%s(?!\d)" % (_FINI, _MES2, _DIA2, _ANIO)),
    re.compile(r"%s%s([_ ])%s\1%s(?!\d)" % (_FINI, _ANIO, _MES2, _DIA2)),
    # Ocho cifras: AAAAMMDD, DDMMAAAA y MMDDAAAA. No la parte entera de un decimal («area
    # 20260430.5»); «20260430.0» sí (es un entero).
    re.compile(r"(?<![\d.])%s%s%s(?!\d)(?![.,]0*[1-9])" % (_ANIO, _MES2, _DIA2)),
    re.compile(r"(?<![\d.])%s%s%s(?!\d)(?![.,]0*[1-9])" % (_DIA2, _MES2, _ANIO)),
    re.compile(r"(?<![\d.])%s%s%s(?!\d)(?![.,]0*[1-9])" % (_MES2, _DIA2, _ANIO)),
    # 30 de abril / 30 de abr de 2026
    re.compile(r"(?i:\b\d{1,2}\s+de\s+(?:%s|%s)\b)" % (_MESES_ES, _MES_ES_ABR)),
    # 30APR2026, 30 abril 2026, 30-abr-2026, 30th of April 2026 (con año: cualquier caja)
    re.compile(r"(?i:(?<![\d.])%s(?:st|nd|rd|th)?[ \t\-./]{0,3}(?:of[ \t]+)?%s(?![a-z])"
               r"\.?[ \t\-./,]{0,3}(?:de[ \t]+)?%s(?!\d))" % (_DIA, _MES_TOK, _ANIO)),
    # 30 Apr, 30 APR, 30 abr., 30 April, 4-Oct (sin año: el mes abreviado, escrito como mes)
    re.compile(r"(?<![\d.])%s(?i:st|nd|rd|th)?[ \t\-./]{0,3}(?i:of[ \t]+)?%s(?![A-Za-z])"
               % (_DIA, _MES_SIN_ANIO)),
    # Apr 30, APR 30, April 30 (inglés, sin año; con año lo coge la de abajo). Con al menos un
    # separador entre mes y día y sin guion: un nombre de gen o de marcador («OCT4», «SEPT9»,
    # «DEC1», «MARCH5», «Oct-4», «c-Jun 2») no es una fecha.
    re.compile(r"(?<![A-Za-z])(?<![A-Za-z]-)(?:(?i:%s)|%s|(?i:%s)\.)(?![A-Za-z])\.?[ \t./]{1,3}%s"
               r"(?i:st|nd|rd|th)?(?!\d)(?![.,]\d)"
               % ("january|february|march|april|may|june|july|august|september|october|november|"
                  "december", "|".join("%s|%s" % (a.capitalize(), a.upper()) for a in _ABREV[:12]),
                  "|".join(_ABREV[:12]), _DIA)),
    # Mes delante en español o pegado, con el AÑO obligatorio: «abr 30 2026», «abril 30, 2026»,
    # «APR302026».
    re.compile(r"(?i:(?<![a-z])%s(?![a-z])\.?[ \t\-./]{0,3}%s(?:st|nd|rd|th)?,?[ \t\-./]{0,3}%s(?!\d))"
               % (_MES_TOK, _DIA, _ANIO)),
    # Apr 2026, abril de 2026, April of 2026
    re.compile(r"(?i:(?<![a-z])%s(?![a-z])\.?[ \t\-./,]{0,3}(?:de[ \t]+|of[ \t]+)?%s(?!\d))"
               % (_MES_TOK, _ANIO)),
    # Mes en romano, con año: 30.IV.2026
    re.compile(r"(?<![\d.])%s[ .\-/]{1,2}%s[ .\-/]{1,2}%s(?!\d)" % (_DIA, _ROMANO, _ANIO)),
)
RES_FECHA += (
    # Tercera pasada (2-oct-26). Año de DOS cifras con el mes en letra: «30-apr-26», «14-mar-79»,
    # «30abr26», «30 abr 26» (cualquier caja: con año ya no es «4 mar» suelto).
    re.compile(r"(?i:(?<![\d.])%s(?:st|nd|rd|th)?[ \t\-./]{0,3}%s(?![a-z])\.?[ \t\-./]{0,3}\d{2}"
               r"(?!\d)(?![.,]\d))" % (_DIA, _MES_TOK)),
    # Mes en español entero DELANTE y sin año: «marzo 14», «abril 30» (en inglés ya estaba).
    re.compile(r"(?i:(?<![a-z])(?:%s)(?![a-z])[ \t./]{1,3}%s(?!\d)(?![.,]\d))" % (_MESES_ES, _DIA)),
    # Separador con espacios alrededor y año de 4 cifras: «30 . 04 . 2026», «2026 / 04 / 30».
    re.compile(r"%s%s[ ]{1,2}([/.\-])[ ]{1,2}%s[ ]{1,2}\1[ ]{1,2}%s(?!\d)" % (_FINI, _DIA, _MES, _ANIO)),
    re.compile(r"%s%s[ ]{1,2}([/.\-])[ ]{1,2}%s[ ]{1,2}\1[ ]{1,2}%s(?!\d)" % (_FINI, _MES, _DIA, _ANIO)),
    re.compile(r"%s%s[ ]{1,2}([/.\-])[ ]{1,2}%s[ ]{1,2}\1[ ]{1,2}%s(?!\d)" % (_FINI, _ANIO, _MES, _DIA)),
    # «March the 14th»
    re.compile(r"(?i:(?<![a-z])%s(?![a-z])[ \t]+the[ \t]+%s(?:st|nd|rd|th)?(?!\d))" % (_MES_LARGO, _DIA)),
    # Mes romano con año de dos cifras y el mismo separador: «30-IV-26», «30.IV.26».
    re.compile(r"(?<![\d.])%s([.\-/])%s\1\d{2}(?!\d)" % (_DIA, _ROMANO)),
)
# «may» en minúscula sin año, entre espacios, es el verbo («cells may 12»): no cuenta como fecha.
# Pegado a un guion, punto o barra («30-may-26») es el mes.
_RE_MAY_VERBO = re.compile(r"(?<![A-Za-z\-./])may(?![A-Za-z\-./])")
_RE_ANIO = re.compile(r"(?:19|20)\d\d")
# Fecha de 8 cifras escrita como número (CSV/JSON): AAAAMMDD, DDMMAAAA, MMDDAAAA.
_RE_FECHA8 = re.compile(r"^(?:%s%s%s|%s%s%s|%s%s%s)$" % (_ANIO, _MES2, _DIA2, _DIA2, _MES2, _ANIO,
                                                        _MES2, _DIA2, _ANIO))

# Teléfonos. Del plan: «+» y ≥8 cifras, o 9 seguidas empezando por 6-9. Ampliación (hallazgo
# 13): prefijo 00, grupos 3-3-3 / 3-2-2-2 (móvil) y 2-3-2-2 (fijo o móvil), y el formato NANP de
# EE. UU. Segunda pasada: separadores mezclados («612 345-678», «617 632-3000»), barra, dos
# espacios, y pegado tras una letra y un punto, coma, barra o guion («Tel.612 345 678»,
# «movil-612 345 678»). Un rango «38.5-44.1» no casa: los grupos son enteros de 2-4 cifras y
# delante no puede haber «cifra.». Exención: si todos los grupos son múltiplos de 16 son
# dimensiones de una red («768-512-256», como el «512-128-256» del plan), y si todos lo son de 10,
# cifras redondas («600 300 150» por brazo); no un teléfono. Un teléfono así es 1 entre ~1.000
# (límite declarado: «690 120 340» pasa).
#
# Falsos positivos quitados (2-oct-26): una fila de ≥4 grupos de cifras es una lista («bbox 612
# 345 678 901»), no un teléfono: ni grupo delante ni grupo de 3 detrás. Y el primer grupo español
# tiene que ser un prefijo que exista: 6xx, 71x-74x (móviles), 81x-88x y 91x-98x (fijos); 70x, 75x-
# 79x, 80x y 90x no son de una persona («counts 701 233 845», «epochs 700 350 175»). Siguen
# saltando, y es el coste declarado del formato 3-3-3, tríos con prefijo válido («coords 812 345
# 678», «mean 612.345.678», que en español es además 612 millones).
# Tercera pasada (2-oct-26): coma y guion bajo pegados también separan («612,345,678»,
# «612_345_678»); con espacio detrás, la coma es una lista («612, 345, 678») y no cuenta.
_TSEP = r"(?:[ ]{0,2}[.\-/][ ]{0,2}|[ ]{1,2}|[,_])"
_TEL_FIN = r"(?![\d]|[.,]\d)(?!%s\d{3})" % _TSEP
_TEL_INI = (r"(?<!\d)(?<!\d[.,/\-_])(?<!\d )(?<!\d  )(?<!\d [.\-/])(?<!\d[.\-/] )"
            r"(?<!\d [.\-/] )")
_PREF34 = r"(?:(?:\+|00)?[ ]?34%s?)?" % _TSEP
_ES3 = r"(?:6\d\d|7[1-4]\d|[89][1-8]\d)"          # primer grupo de 3 de un número español
_ES2 = r"(?:6\d|7[1-4]|[89][1-8])"                 # primer grupo de 2 (2-3-2-2)
RES_TELEFONO = (
    re.compile(r"\+\d(?:[\s.\-]?\d){7,}"),
    re.compile(r"(?<!\d)(?:00\d{2}[ .\-]?)?[6-9]\d{8}(?!\d)"),
    re.compile(r"(?<!\d)001[2-9]\d{2}[2-9]\d{6}(?!\d)"),                  # NANP con 00, sin separar
    re.compile(r"(?<!\d)(?:\+|00)?34[ .\-]?[6-9]\d{8}(?!\d)"),           # «34612345678» (3.ª pasada)
    re.compile(_TEL_INI + _PREF34 + r"(%s)%s(\d{3})%s(\d{3})" % (_ES3, _TSEP, _TSEP) + _TEL_FIN),
    re.compile(_TEL_INI + _PREF34 + r"(%s)%s(\d{2})%s(\d{2})%s(\d{2})" % ((_ES3,) + (_TSEP,) * 3)
               + _TEL_FIN),
    re.compile(_TEL_INI + _PREF34 + r"(%s)%s(\d{3})%s(\d{2})%s(\d{2})" % ((_ES2,) + (_TSEP,) * 3)
               + _TEL_FIN),
    # NANP con «+1», «1» o «001» delante («001 617 632 3000», 3.ª pasada)
    re.compile(_TEL_INI + r"(?:(?:\+|00)?1%s?)?([2-9]\d{2})%s([2-9]\d{2})%s(\d{4})" % ((_TSEP,) * 3)
               + _TEL_FIN),
    re.compile(_TEL_INI + r"(?:(?:\+|00)?1%s?)?\(([2-9]\d{2})\)[ ]{0,2}([2-9]\d{2})%s(\d{4})"
               % (_TSEP, _TSEP) + _TEL_FIN),
)
RE_TELEFONO = RES_TELEFONO[0]          # compatibilidad: el «+…» del plan
_TEL_SIN_EXENCION = 4                  # los cuatro primeros no son grupos: sin exención de redondos
# Identificador hospitalario de EE. UU. (hallazgo 21): «MRN 12345678», «medical record number …».
# Segunda pasada: cualquier separador entre etiqueta y número («MRN=…», «MRN (…)», «MRN is …»),
# cifras agrupadas («1234-5678»), prefijo de hasta dos letras, sin tope de cifras, «MR#», «Med.
# Rec. #». Las etiquetas genéricas («record», «unit» y «patient» + number/no/#/:) exigen ≥7
# cifras: «record number 100000 nuclei» o «patient number 12345 of the cohort» no son un MRN.
# «patient id», «hospital number» (el MRN británico), MRN, MR# y Med. Rec., ≥5.
# Tercera pasada (2-oct-26): «pt id», «PID», «EMPI» (índice maestro de pacientes de un hospital de
# EE. UU.) y cualquier signo entre la etiqueta y el número («MRN ≠ 12345678», «MRN → …»).
_MRN_ESPECIFICA = (r"(?<![a-z])(?:m\.?[ ]?r\.?[ ]?n\.?(?![a-z])|mr[ ]?(?:#|no\.?(?![a-z]))|"
                   r"med(?:ical|\.)?\s*rec(?:ord|\.)?(?![a-z])(?:\s*(?:number|num|no\.?|#|n[º°o]\.?))?|"
                   r"patient[\s_\-]*(?:id(?![a-z])|identifier)|hospital\s*(?:number|no\.?)(?![a-z])|"
                   r"pt\.?[\s_\-]*(?:id(?![a-z])|identifier|#|no\.?(?![a-z]))|pid(?![a-z])|empi(?![a-z]))")
_MRN_GENERICA = (r"(?<![a-z])(?:record|unit|patient)\s*(?:#|number(?![a-z])|num(?![a-z])|"
                 r"no\.?(?![a-z])|:)")
_MRN_SEP = r"[^a-z0-9]{0,6}(?:(?:is|was|number|no)(?![a-z])[^a-z0-9]{0,3})?"
_MRN_VALOR = r"[a-z]{0,2}\d(?:[ \-.]?\d){%d,}(?![0-9])"
RE_MRN = re.compile(_MRN_ESPECIFICA + _MRN_SEP + _MRN_VALOR % 4
                    + "|" + _MRN_GENERICA + _MRN_SEP + _MRN_VALOR % 6, re.I)
# Cabecera de CSV o clave de JSON que dice «aquí va un identificador»: su valor numérico no pasa por
# los detectores de texto, así que la columna entera queda fuera. Por PIEZAS (ver `es_campo_id`):
# «Patient_MRN», «case_mrn», «mrn_id», «MRN#», «mrn1», «M.R.N.», «PatientMRN», «MRN;x» (CSV con
# punto y coma, una sola celda) y la cabecera con BOM cuentan; «mRNA_level» no.
_PIEZAS_ID = frozenset({"mrn", "mrns", "nhc", "pid", "empi"})
_PARES_ID = frozenset({("medical", "record"), ("med", "rec"), ("record", "number"), ("record", "no"),
                       ("record", "num"), ("record", "id"), ("patient", "id"), ("patient", "identifier"),
                       ("pt", "id"),
                       ("patient", "number"), ("patient", "num"), ("patient", "no"),
                       ("historia", "clinica"), ("hospital", "number")})
_RE_ID_PEGADO = re.compile(r"mrn(?!a)|nhc|medicalrecord|medrec|patient(?:id|identifier|number|num|no)|"
                           r"record(?:number|num|no|id)|historiaclinica|hospitalnumber")
# Accesión AP en forma laxa (hallazgo 11): `_RE_CODIGO_AP` distingue mayúsculas, no admite
# espacios y exige `\b` delante, así que «24B 0001043», «24b0001043», «26B.0008505»,
# «HE_24B0001043» o «slide24B0001043» pasaban. Aquí solo se exige que no haya una cifra delante.
# Segunda pasada: separadores / : , y salto de línea, «B-2026-22813», y la cuarta forma de
# caso_publico («26-28381») también pegada a letras («x_26-28381») o con guion bajo. Con separador entre
# las cifras y la B, ≥5 cifras detrás (las accesiones van rellenas con ceros: «24B 0001043»):
# «Llama 70B 4096» o «batch 32 b 128» no son una accesión.
_SEP_AP = r"\s{0,2}[-._/:,]?\s{0,2}"
_SEP_AP1 = r"(?:\s{1,2}|\s{0,2}[-._/:,]\s{0,2})"
RE_CODIGO_AP_N1 = re.compile(
    r"(?<![0-9])(?:e[-_ ]?)?B20\d\d" + _SEP_AP + r"\d{3,}"
    + r"|(?<![0-9A-Za-z])(?:e[-_ ]?)?B" + _SEP_AP1 + r"20\d\d" + _SEP_AP + r"\d{3,}"
    + r"|(?<![0-9])VH" + _SEP_AP + r"\d{2}" + _SEP_AP + r"B(?:" + _SEP_AP + r"\d{3,})?"
    + r"|(?<![0-9])\d{2}B\d{3,}"
    + r"|(?<![0-9])\d{2}" + _SEP_AP + r"B" + _SEP_AP + r"\d{5,}"
    + r"|(?<![0-9.,])\d{2}(?:\s{0,2}-\s{0,2}|_)\d{5}(?![0-9]|[.,]\d)"
    # Tercera pasada (2-oct-26), la forma CORTA que documenta caso_publico («24B-1043»):
    #  · B pegada a las cifras y un signo detrás, ≥3 cifras: «24b-1043», «24B.1043», «24B_1043»;
    #  · con espacios, solo con año de 2015-2029 delante y ≥4 cifras detrás: «24B 1043», «24 B 1043»
    #    («Llama 70B 4096», «sizes 13B 1000» y «x20 B 100» siguen pasando);
    #  · el relleno de ceros en un grupo aparte: «24B 000 1043».
    + r"|(?<![0-9])\d{2}B\s{0,2}[-._/:,]\s{0,2}\d{3,}(?![0-9])"
    + r"|(?<![0-9.,])(?:1[5-9]|2\d)" + _SEP_AP + r"B" + _SEP_AP + r"\d{4,}(?![0-9]|[.,]\d)"
    + r"|(?<![0-9])\d{2}" + _SEP_AP + r"B" + _SEP_AP + r"0{1,6}[ ._\-]{1,2}\d{3,}(?![0-9])", re.I)
# La cuarta forma («26-28381») solo con guion o guion bajo (2-oct-26): con espacio son dos números
# («sizes 26 28381»), con punto un decimal («mean 26.28381») y con barra un cociente («26/28381
# positive»); esas tres grafías de la accesión no se distinguen de una medida y pasan (límite).

# Homoglifos que se leen como latinas (cirílico y griego): bastan para escribir una accesión, un
# mes, «MRN» o un nombre con otra grafía. La µ (micro) no se toca: «µm» es texto N1 corriente.
_HOMOGLIFOS = str.maketrans({
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0425": "X", "\u0423": "Y",
    "\u0406": "I", "\u0408": "J", "\u0405": "S", "\u0430": "a", "\u0435": "e", "\u043e": "o",
    "\u0440": "p", "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j",
    "\u0455": "s", "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
    "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P",
    "\u03a4": "T", "\u03a7": "X", "\u03a5": "Y", "\u03bf": "o", "\u03bd": "v",
})
# Tiras hexadecimales opacas (sha256, md5, uuid de las features de un GeoJSON): se tapan SOLO
# para los detectores de forma (accesión laxa, fecha, teléfono), que saltaban al azar dentro de un
# hash («…12b3456…», nueve cifras seguidas). Diccionario, canarios, MRN y titular miran el texto
# sin tapar: un canario puede ser hexadecimal. ≥16 caracteres, con letras y cifras: una accesión
# con un sufijo pegado («24b0001043a1», 12) sigue a la vista. Los prefijos cortos de un hash
# (12) solo se tapan por esquema: valor hexadecimal de una clave sha*/md5/hash (ver _Hash).
_RE_HEX_OPACO = re.compile(
    r"(?<![0-9A-Za-z])(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    r"|(?=[0-9a-fA-F]*[a-fA-F])(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]{16,})(?![0-9A-Za-z])")
_RE_CLAVE_HASH = re.compile(r"(?:^|[_\-])(?:sha(?:1|224|256|384|512)?|md5|hash|digest)(?:$|[_\-])",
                            re.I)
_RE_VALOR_HEX = re.compile(r"[0-9a-fA-F]{8,128}")


class _Hash(str):
    """Valor hexadecimal de una clave sha*/md5/hash de un JSON: opaco para los detectores de
    forma (como _RE_HEX_OPACO), visible para diccionario, canarios y titular."""
RE_ENTERO_LARGO = re.compile(r"^[+-]?\d{9,}$")
_RE_OCHO_CIFRAS = re.compile(r"^[+-]?(\d{8})(?:\.0*)?$")
# En un GeoJSON N1 no va ni una ruta ni un nombre de imagen (plan, F5; desde el 1-oct-26 en
# CUALQUIER cadena del GeoJSON, no solo en `properties`). Segunda pasada: la barra tiene que ir
# seguida de un nombre («ratio a / b» no es una ruta), URI file:, ruta relativa que empieza por
# una carpeta de sistema («Volumes/scan/slide», «Users/x/…») o por ./ ../, y los formatos WSI que
# faltaban (.vsi, .bif, .qptiff, .isyntax, .dcm…). Una lista de clases «Tumor/Stroma/Other» o
# «H/E/DAB» no es una ruta: la ruta relativa sin carpeta de sistema delante pasa (límite).
RE_RUTA = re.compile(
    r"(?:^|[\s\"'(=])(?:/(?=[\w.~-])|~/|[A-Za-z]:[\\/]|\.{1,2}/)"
    r"|\bfile:/"
    r"|(?<![\w.~-])(?:Volumes|Users|home|mnt|media|private|tmp|var|opt|Library|Desktop|Downloads|"
    r"Documents|OneDrive|Dropbox|Google[ _]?Drive|My[ _]?Drive|Mi[ _]unidad)/"
    r"|\.(?:tiff?|svs|ndpi|mrxs|czi|scn|vsi|bif|qptiff|isyntax|dcm|kfb|sdpc|tf2|tf8|btf|jp2|png|"
    r"jpe?g|gif|bmp|webp|heic|zip|pptx)\b", re.I)

EXENTAS_CASI = frozenset({"pares", "pared", "mirar", "miran"})


class PuertaCerrada(RuntimeError):
    """La puerta no deja pasar esto (o no puede mirar). Nunca lleva el texto que disparó."""


# ── Fail-closed: sin diccionario no se mira, y sin mirar no se exporta ─────────────────────
def _canarios_legibles():
    try:
        with open(borde.CANARIO_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(d, list) and any(isinstance(x, str) and x for x in d)


_ABIERTA = None          # tamaños de los cargadores, una vez comprobados en este proceso


def exigir_diccionario():
    """Lanza PuertaCerrada si falta cualquiera de los tres cargadores. Devuelve sus tamaños.
    Un éxito se recuerda en el proceso (motivos() lo pide en cada celda); un fallo, nunca."""
    global _ABIERTA
    if _ABIERTA is not None:
        return dict(_ABIERTA)
    faltan = []
    n_dic = len(deid._diccionario())
    if n_dic == 0:
        faltan.append("diccionario de la titular vacío (perfil/identidad/nombres .local.json)")
    if not seg._NOMBRES_DENY:
        faltan.append("deny-list de nombres vacía (nombres.local.json)")
    if not _canarios_legibles():
        faltan.append("canarios.json ilegible o vacío (%s)" % borde.CANARIO_FILE)
    if not _titular_partes():
        faltan.append("titular sin nombre ni apellidos (perfil.local.json)")
    if faltan:
        raise PuertaCerrada("puerta sin diccionario: no exporto · " + "; ".join(faltan))
    _ABIERTA = {"diccionario": n_dic, "nombres": len(seg._NOMBRES_DENY)}
    return dict(_ABIERTA)


# ── Casi-coincidencia con la titular ───────────────────────────────────────────────────────
def _plano(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


_PARTES = None


def _titular_partes():
    global _PARTES
    if _PARTES:
        return _PARTES
    partes = set()
    for perfil in deid._leer_overlay("perfil.local.json"):
        t = perfil.get("titular") or {}
        for k in ("nombre", "apellidos"):
            for v in deid._lista(t.get(k)):
                partes.update(w for w in re.findall(r"[a-z]+", _plano(v)) if len(w) >= 4)
    _PARTES = frozenset(partes)
    return _PARTES


_ESPACIOS = frozenset("\t  ᠎  　")
_QUITAR = frozenset({"Cf", "Mn", "Me"})
# Puntos medios y viñetas, a punto (tercera pasada: «24B·0001043», «30·04·2026»).
_PUNTOS = frozenset("··•‧∙⋅・･")


def _normaliza(texto):
    """(texto normalizado, mapa de cada posición al texto original). Quita los caracteres de
    formato (ancho cero, BOM, guion blando) y las marcas combinantes (un nombre en NFD, como los
    escribe macOS; U+034F; selectores de variante), pasa cada carácter por NFKC (ancho completo,
    ligaduras), los homoglifos cirílicos y griegos a latino, cualquier espacio Unicode y el
    tabulador a espacio, y cualquier raya a guion. Los saltos de línea se quedan."""
    out, mapa = [], []
    for i, c in enumerate(texto or ""):
        cat = unicodedata.category(c)
        if cat in _QUITAR:
            continue
        if c in _ESPACIOS or cat == "Zs":
            r = " "
        elif cat == "Pd" or c == "−":
            r = "-"
        elif c in _PUNTOS:
            r = "."
        else:
            r = unicodedata.normalize("NFKC", c).translate(_HOMOGLIFOS)
        for x in r:
            if unicodedata.category(x) in _QUITAR:
                continue
            out.append(x)
            mapa.append(i)
    return "".join(out), mapa


_sin_invisibles = _normaliza          # nombre anterior (casi_coincidencias, pruebas externas)


def _trozos_camel(palabra):
    """[(ini, fin)] de las piezas CamelCase de una palabra: «LeocadiaRosa» → Leocadia, Rosa;
    «HTMLParser» → HTML, Parser. Una palabra sin cambios de caja es una sola pieza."""
    cortes = [0]
    for i in range(1, len(palabra)):
        a, b = palabra[i - 1], palabra[i]
        sig = palabra[i + 1] if i + 1 < len(palabra) else ""
        if (a.islower() and b.isupper()) or (a.isupper() and b.isupper() and sig.islower()):
            cortes.append(i)
    cortes.append(len(palabra))
    return [(x, y) for x, y in zip(cortes, cortes[1:]) if y > x]


def _distancia(a, b, tope):
    """Damerau-Levenshtein (OSA): una transposición cuenta 1. Corta en cuanto pasa de `tope`."""
    if abs(len(a) - len(b)) > tope:
        return tope + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            coste = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + coste)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > tope:
            return tope + 1
        prev2, prev = prev, cur
    return prev[-1]


def _casa_palabra(w, partes):
    """¿La palabra `w` (ya plana) está cerca de una parte de la titular? La longitud que manda es
    la de la PALABRA: «pero» (4) no casa con un apellido de 5 a distancia 2."""
    if len(w) < 4 or w in EXENTAS_CASI:
        return False
    tope = 2 if len(w) >= 5 else 1
    return any(_distancia(w, p, tope) <= tope for p in partes)


def _contiene_parte(w, partes):
    """¿Una parte de la titular va PEGADA dentro de `w` («quintanartallon», «heleocadiahe»)? Solo
    dentro de una palabra: quitar los espacios entre palabras haría saltar «micro sample».

    - parte de ≥6 letras: exacta en cualquier sitio; a distancia ≤1 solo al principio o al final
      («quintnaartalon»). Una ventana a distancia 1 en medio hacía saltar palabras corrientes
      («metallothionein» ⊃ «tallot»; falso positivo de la revisión, 2-oct-26).
    - parte de 4-5 letras: exacta al principio o al final con ≤2 letras de resto («rosahe»), o en
      cualquier sitio si la palabra lleva además OTRA parte («rosaquintanar»). Una parte corta
      dentro de una palabra larga es casi siempre una palabra corriente («rosacea», «prosaic»).

    Lo pegado con mayúsculas («LeocadiaRosa», «HeLeocadia») lo cogen las piezas CamelCase."""
    exactas = [p for p in partes if p in w and len(w) > len(p)]
    for p in partes:
        if len(w) <= len(p):
            continue
        if len(p) >= 6:
            if p in w:
                return True
            if (_distancia(w[:len(p)], p, 1) <= 1 or _distancia(w[-len(p):], p, 1) <= 1):
                return True
        elif p in w:
            if ((w.startswith(p) or w.endswith(p)) and len(w) - len(p) <= 2) \
                    or any(q != p for q in exactas):
                return True
    return False


def casi_coincidencias(texto, partes=None):
    """[(ini, fin)] de palabras a distancia ≤2 (≥5 letras) o ≤1 (4 letras) de su nombre o
    apellidos, mirando también cada pieza CamelCase («LeocadiaRosa»), la palabra normalizada
    («Leo\\u200bcadia», un «GonzálezPérez» en NFD, homoglifos) y las partes pegadas dentro de
    una palabra («QUINTANARTALLON»). Posiciones sobre el texto original."""
    partes = _titular_partes() if partes is None else partes
    out = []
    limpio, mapa = _normaliza(texto)
    for m in re.finditer(r"[^\W\d_]+", limpio):
        bruto = m.group(0)
        w = _plano(bruto)
        piezas = [_plano(bruto[a:b]) for a, b in _trozos_camel(bruto)]
        if (_casa_palabra(w, partes) or any(_casa_palabra(x, partes) for x in piezas if x != w)
                or _contiene_parte(w, partes)):
            out.append((mapa[m.start()], mapa[m.end() - 1] + 1))
    return out


# ── El juez ────────────────────────────────────────────────────────────────────────────────
def _fechas(texto):
    for rx in RES_FECHA:
        for m in rx.finditer(texto):
            s = m.group(0)
            if _RE_MAY_VERBO.search(s) and not _RE_ANIO.search(s):
                continue
            yield m.start()


def _telefonos(texto):
    for i, rx in enumerate(RES_TELEFONO):
        for m in rx.finditer(texto):
            grupos = [int(g) for g in re.findall(r"\d+", m.group(0))]
            if i >= _TEL_SIN_EXENCION and (all(g % 16 == 0 for g in grupos)
                                           or all(g % 10 == 0 for g in grupos)):
                continue                                 # dimensiones de red o cifras redondas
            yield m.start()


def _tapa_hex(original, limpio):
    if isinstance(original, _Hash):
        return " " * len(limpio)
    return _RE_HEX_OPACO.sub(lambda h: " " * len(h.group(0)), limpio)


def motivos_de_forma(texto):
    """Solo fecha y accesión, por su forma (sin diccionario): para lo que el OCR lee con poca
    confianza, donde el resto de capas daría ruido. Devuelve el conjunto de capas."""
    limpio, _mapa = _normaliza(texto)
    tapado = _tapa_hex(texto, limpio)
    capas = set()
    if RE_CODIGO_AP_N1.search(tapado) or _RE_CODIGO_AP.search(limpio):
        capas.add("codigo_ap")
    if next(_fechas(tapado), None) is not None:
        capas.add("fecha")
    return capas


def motivos(texto):
    """[(capa, posición)] de todo lo que aborta en `texto`. Vacío = pasa. Sin diccionario,
    PuertaCerrada (fail-closed también por la vía de librería).

    Diccionario, accesión de caso_publico y canarios miran el texto tal cual Y el normalizado
    (un identificador del diccionario con un ancho cero o en ancho completo dentro); MRN, forma
    laxa de la accesión, fecha, teléfono y titular, el normalizado."""
    exigir_diccionario()
    texto = texto or ""
    out = []
    limpio, mapa = _normaliza(texto)
    variantes = [(texto, None)] + ([(limpio, mapa)] if limpio != texto else [])
    for t, mp in variantes:
        pos = (lambda p: p) if mp is None else (lambda p, mp=mp: mp[min(p, len(mp) - 1)] if mp else 0)
        for a, _b, capa in deid.detectar(t):
            if capa in CAPAS_ABORTA:
                out.append((capa, pos(a)))
        m = _RE_CODIGO_AP.search(t)
        if m:
            out.append(("codigo_ap", pos(m.start())))
        if borde.hay_canario(t):
            out.append(("canario", -1))
    for m in RE_MRN.finditer(limpio):
        out.append(("mrn", mapa[m.start()]))
    # detectores de forma: sobre el texto normalizado y con las tiras hexadecimales tapadas
    tapado = _tapa_hex(texto, limpio)
    for m in RE_CODIGO_AP_N1.finditer(tapado):
        out.append(("codigo_ap", mapa[m.start()]))
    for p in _fechas(tapado):
        out.append(("fecha", mapa[p]))
    for p in _telefonos(tapado):
        out.append(("telefono", mapa[p]))
    for a, _b in casi_coincidencias(texto):
        out.append(("casi_titular", a))
    return sorted(set(out), key=lambda x: (x[1], x[0]))


def revisar_texto(texto, donde="texto"):
    m = motivos(texto)
    if m:
        raise PuertaCerrada("%s: %s" % (donde, ", ".join("%s@%d" % x for x in m[:8])))


def _es_numero(s):
    try:
        float(s.strip().replace("_", "x"))
        return bool(s.strip())
    except ValueError:
        return False


class _Num(float):
    """Un float de un JSON con su grafía (`crudo`): «1.5e9» no es «612345678.0»."""
    crudo = None


def _num_json(s):
    n = _Num(s)
    n.crudo = s
    return n


# Piezas de una cabecera o clave de MEDIDA («Nucleus: Area µm^2», «area_um2», «Num Detections»):
# un valor de 8 cifras con forma de fecha ahí es una medida (un área de 20260430 µm² es 0,2 cm²
# de tejido), no una fecha (falso positivo del hallazgo 18, 2-oct-26). La regla de ≥9 cifras del
# plan no se exime.
_PIEZAS_MEDIDA = frozenset({"area", "perimeter", "perim", "length", "width", "height", "diameter",
                            "count", "counts", "total", "sum", "mean", "median", "std", "min",
                            "max", "um", "px", "pixels", "nuclei", "cells", "bytes", "size",
                            "intensity", "num", "n"})


def _es_campo_medida(campo):
    if not campo:
        return False
    return any(p in _PIEZAS_MEDIDA for _w, ps in _piezas_campo(campo) for p in ps)


def _cifras_significativas(crudo):
    mantisa = re.split(r"[eE]", crudo)[0]
    return len(re.sub(r"[^0-9]", "", mantisa).strip("0"))


def _numero_prohibido(valor, crudo=None, campo=None):
    """Motivo si un número es en realidad un identificador o una fecha; None si no.
    `valor` es el número (int/float), `crudo` la cadena tal como venía (CSV, o el float de un JSON
    leído con `_num_json`) y `campo`, la cabecera o clave donde va."""
    if crudo is None and isinstance(valor, _Num):
        crudo = valor.crudo
    if crudo is not None and RE_ENTERO_LARGO.match(crudo):
        return "entero de ≥9 cifras"
    if isinstance(valor, int) and RE_ENTERO_LARGO.match(str(valor)):
        return "entero de ≥9 cifras"
    if isinstance(valor, float) and math.isfinite(valor) and valor.is_integer() and abs(valor) >= 1e8:
        # En notación ingeniera, solo si la mantisa tiene cifras de identificador: «6.12345678e8»
        # sí; «1.5e9» o «2.5e8» (un área redonda en µm²) no (falso positivo del hallazgo 18).
        if not (crudo and "e" in crudo.lower() and _cifras_significativas(crudo) < 5):
            return "entero de ≥9 cifras escrito como decimal o en notación ingeniera"
    if crudo is not None and "e" in crudo.lower():
        if _cifras_significativas(crudo) >= 9:
            return "notación ingeniera con ≥9 cifras significativas"
    if _es_campo_medida(campo):
        return None
    entero = None
    if isinstance(valor, int):
        entero = valor
    elif isinstance(valor, float) and math.isfinite(valor) and valor.is_integer():
        entero = int(valor)
    ocho = []
    if entero is not None:
        ocho.append(str(abs(entero)))
    if crudo is not None:                    # «04302026» como número pierde el cero de delante
        m = _RE_OCHO_CIFRAS.match(unicodedata.normalize("NFKC", crudo.strip()))
        if m:
            ocho.append(m.group(1))
    if any(_RE_FECHA8.match(x) for x in ocho):
        return "fecha AAAAMMDD/DDMMAAAA/MMDDAAAA"
    return None


# Tercera pasada (2-oct-26): una CADENA de un CSV o un JSON que es un número de ≥9 cifras —entre
# comillas («"123456789"», «"0123456789"») o agrupado con coma, punto, guion bajo, apóstrofo o
# espacio («"612,345,678"», «1_2345_6789»)— es el mismo identificador que la regla de ≥9 cifras del
# plan para los números: no pasa como texto. El guion no agrupa: «512-128-256» es del plan y pasa.
_RE_CIFRAS_AGRUPADAS = re.compile(r"[+-]?(?:\d+|\d{1,4}(?:[_,.'’   ]\d{2,4})+)")


def _cadena_numerica_prohibida(s):
    """Motivo si la cadena `s` es un número de ≥9 cifras escrito como texto; None si no."""
    t = unicodedata.normalize("NFKC", s or "").strip()
    if _RE_CIFRAS_AGRUPADAS.fullmatch(t) and len(re.sub(r"\D", "", t)) >= 9:
        return "cadena de ≥9 cifras (entre comillas o agrupadas)"
    return None


def _piezas_campo(s):
    """[[piezas]] de un nombre de campo, por tramos de letras: «Patient_MRN» → [[patient], [mrn]];
    «PatientMRN» → [[patient, mrn]] (CamelCase); cada pieza, plana y en minúscula."""
    limpio = unicodedata.normalize("NFKD", _normaliza(s)[0])
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))       # «clínica» → clinica
    tramos = []
    for m in re.finditer(r"[A-Za-z]+", limpio):
        w = m.group(0)
        tramos.append((w.lower(), [w[a:b].lower() for a, b in _trozos_camel(w)]))
    return tramos


def es_campo_id(s):
    """¿Esta cabecera de CSV o clave de JSON anuncia un identificador (MRN, NHC, nº de historia,
    patient id…)? Por piezas, no por la cadena entera: «Patient_MRN», «case_mrn», «mrn_id»,
    «MRN#», «mrn1», «M.R.N.», «PatientMRN», «patientid», «MRN;x», con BOM. «mRNA_level» no."""
    tramos = _piezas_campo(s)
    piezas = [p for _w, ps in tramos for p in ps]
    if any(p in _PIEZAS_ID for p in piezas) or any(w in _PIEZAS_ID for w, _ps in tramos):
        return True
    if any(_RE_ID_PEGADO.search(w) for w, _ps in tramos):
        return True
    if any((a, b) in _PARES_ID for a, b in zip(piezas, piezas[1:])):
        return True
    siglas, run = [], ""                       # «M.R.N.» / «N.H.C.»: letras sueltas seguidas
    for w, _ps in tramos:
        if len(w) == 1:
            run += w
        else:
            siglas.append(run)
            run = ""
    siglas.append(run)
    return any(("mrn" in x or "nhc" in x) for x in siglas)


def _cadenas_csv(texto):
    """Cadenas a mirar de un CSV: cabeceras y celdas no numéricas. Números que son un
    identificador o una fecha (ver `_numero_prohibido`), fuera; y una columna con cabecera de
    identificador (MRN, NHC…), fuera entera."""
    filas = list(csv.reader(io.StringIO(texto)))
    # Cabecera: la primera fila, si tiene alguna celda que no es un número.
    cab = filas[0] if filas and any(x.strip() and not _es_numero(x) for x in filas[0]) else []
    for i, fila in enumerate(filas):
        for j, celda in enumerate(fila):
            c = celda.strip()
            if not c:
                continue
            if es_campo_id(c):
                raise PuertaCerrada("csv: cabecera de identificador en fila %d, columna %d" % (i, j))
            if _es_numero(c):
                campo = cab[j] if i > 0 and j < len(cab) else None
                mot = _numero_prohibido(float(c), c, campo)
                if mot:
                    raise PuertaCerrada("csv: %s en fila %d, columna %d" % (mot, i, j))
                continue
            mot = _cadena_numerica_prohibida(c)
            if mot:
                raise PuertaCerrada("csv: %s en fila %d, columna %d" % (mot, i, j))
            yield "fila %d, columna %d" % (i, j), c


def _cadenas_json(obj, ruta="$", en_properties=False, rutas_en_todo=False, campo=None):
    """Cadenas a mirar de un JSON (claves incluidas). `rutas_en_todo` (GeoJSON): RE_RUTA se
    aplica a TODAS las cadenas y claves; si no, solo dentro de `properties`. `campo`: la clave
    de la que cuelga el valor (para `_numero_prohibido`)."""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        mot = _numero_prohibido(obj, campo=campo)
        if mot:
            raise PuertaCerrada("json: %s en %s" % (mot, ruta))
        return
    if isinstance(obj, str):
        if (en_properties or rutas_en_todo) and RE_RUTA.search(obj):
            raise PuertaCerrada("geojson: ruta o nombre de imagen en %s" % ruta)
        if not isinstance(obj, _Hash) and _cadena_numerica_prohibida(obj):
            raise PuertaCerrada("json: %s en %s" % (_cadena_numerica_prohibida(obj), ruta))
        yield ruta, obj
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            k = str(k)
            if es_campo_id(k):
                raise PuertaCerrada("json: clave de identificador en %s" % ruta)
            if _cadena_numerica_prohibida(k):
                raise PuertaCerrada("json: %s en una clave de %s" % (_cadena_numerica_prohibida(k), ruta))
            if rutas_en_todo and RE_RUTA.search(k):
                raise PuertaCerrada("geojson: ruta o nombre de imagen en una clave de %s" % ruta)
            yield ruta + ".<clave>", k
            dentro = en_properties or k == "properties"
            if isinstance(v, str) and _RE_CLAVE_HASH.search(k) and _RE_VALOR_HEX.fullmatch(v):
                v = _Hash(v)                         # «sello_sha256_prefijo»: opaco por esquema
            yield from _cadenas_json(v, "%s.%s" % (ruta, k), dentro, rutas_en_todo, k)
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _cadenas_json(v, "%s[%d]" % (ruta, i), en_properties, rutas_en_todo, campo)
        return
    raise PuertaCerrada("json: tipo inesperado en %s" % ruta)


def revisar_csv(texto, donde="csv"):
    exigir_diccionario()
    for pos, c in _cadenas_csv(texto):
        revisar_texto(c, "%s %s" % (donde, pos))


def _carga_json(texto, donde):
    try:
        return json.loads(texto, parse_float=_num_json)
    except ValueError as e:
        raise PuertaCerrada("%s: no es JSON válido (%s)" % (donde, type(e).__name__))


def revisar_json(texto, donde="json"):
    exigir_diccionario()
    for pos, c in _cadenas_json(_carga_json(texto, donde)):
        revisar_texto(c, "%s %s" % (donde, pos))


def revisar_geojson(texto, donde="geojson"):
    """Como revisar_json, pero ni una ruta ni un nombre de imagen en NINGUNA cadena: el nombre de
    la imagen original (con su accesión) viaja en `metadata` o en la raíz, no solo en
    `properties` (hallazgo 19)."""
    exigir_diccionario()
    for pos, c in _cadenas_json(_carga_json(texto, donde), rutas_en_todo=True):
        revisar_texto(c, "%s %s" % (donde, pos))


EXT_TEXTO = {".csv": revisar_csv, ".json": revisar_json, ".geojson": revisar_geojson,
             ".txt": revisar_texto, ".md": revisar_texto}


def revisar_bytes_texto(nombre, crudo):
    """La puerta sobre el CONTENIDO (bytes) de un fichero de texto N1 llamado `nombre`."""
    ext = os.path.splitext(nombre)[1].lower()
    if ext not in EXT_TEXTO:
        raise PuertaCerrada("tipo de texto no admitido en N1: %s" % ext)
    try:
        texto = crudo.decode("utf-8")
    except UnicodeDecodeError:
        raise PuertaCerrada("%s no es UTF-8" % os.path.basename(nombre))
    EXT_TEXTO[ext](texto, os.path.basename(nombre))


def revisar_fichero_texto(ruta):
    ext = os.path.splitext(ruta)[1].lower()
    if ext not in EXT_TEXTO:
        raise PuertaCerrada("tipo de texto no admitido en N1: %s" % ext)
    with open(ruta, "rb") as fh:
        crudo = fh.read()
    revisar_bytes_texto(os.path.basename(ruta), crudo)


# ── N1: carpeta, manifiesto, hashes ────────────────────────────────────────────────────────
def n1_dir():
    """`~/Laminillas-N1/`. `BTP_N1_DIR` lo cambia (tests); el destino de verdad lo fija la jaula."""
    return os.path.realpath(os.environ.get("BTP_N1_DIR") or os.path.expanduser("~/Laminillas-N1"))


def manifiesto_path():
    return os.path.join(n1_dir(), "manifiesto.json")


def cargar_manifiesto():
    try:
        with open(manifiesto_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except OSError:
        return {"version": 1, "ficheros": {}}
    if not isinstance(d, dict) or not isinstance(d.get("ficheros"), dict):
        raise PuertaCerrada("manifiesto N1 con forma inesperada")
    return d


def revisar_manifiesto(texto):
    """El manifiesto N1 por la puerta, por esquema: como `revisar_json`, salvo el VALOR de `bytes`
    en cada entrada de `ficheros`, que es len(datos) y lo pone `exporta_n1`, no el usuario. Una
    copia N1 de lámina entera pesa cientos de MB o GB, y ese entero de ≥9 cifras abortaba toda
    exportación de TIFF (1-oct-26); de 19 a 21 MB, además, un tamaño tiene forma de AAAAMMDD.
    Solo se exime un entero ≥0 en `$.ficheros.<nombre>.bytes`: la clave se sigue mirando, y el
    mismo número en cualquier otra clave, o en un JSON/CSV de usuario, sigue abortando."""
    exigir_diccionario()
    try:
        man = json.loads(texto, parse_float=_num_json)
    except ValueError as e:
        raise PuertaCerrada("manifiesto: no es JSON válido (%s)" % type(e).__name__)
    if not isinstance(man, dict) or not isinstance(man.get("ficheros"), dict):
        raise PuertaCerrada("manifiesto N1 con forma inesperada")
    ficheros = {}
    for nombre, ent in man["ficheros"].items():
        if isinstance(ent, dict) and type(ent.get("bytes")) is int and ent["bytes"] >= 0:
            ent = dict(ent, bytes=0)                 # el valor no se mira; la clave, sí
        ficheros[nombre] = ent
    for pos, c in _cadenas_json(dict(man, ficheros=ficheros)):
        revisar_texto(c, "manifiesto %s" % pos)


def sha256_fichero(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fichero_n1_verificado(ruta, manifiesto=None):
    """(nombre, entrada) si `ruta` vive en N1 y su sha256 casa con el manifiesto; si no, lanza."""
    real = os.path.realpath(ruta)
    base = n1_dir()
    if os.path.dirname(real) != base:
        raise PuertaCerrada("fuera de N1: %s" % os.path.basename(ruta))
    nombre = os.path.basename(real)
    man = manifiesto if manifiesto is not None else cargar_manifiesto()
    ent = man["ficheros"].get(nombre)
    if not ent:
        raise PuertaCerrada("no está en el manifiesto N1: %s" % nombre)
    if sha256_fichero(real) != ent.get("sha256"):
        raise PuertaCerrada("sha256 distinto del manifiesto: %s" % nombre)
    return nombre, ent


# ── Confianza del destino (vision-n1:*, nube-n1:*) ─────────────────────────────────────────
def _eventos_de(destino):
    """Los eventos de la cadena con `"destino": destino`, en orden."""
    if not os.path.isdir(borde.BORDE_DIR):
        return
    for fn in sorted(f for f in os.listdir(borde.BORDE_DIR) if f.startswith("ledger-")):
        with open(os.path.join(borde.BORDE_DIR, fn), encoding="utf-8") as fh:
            for ln in fh:
                if destino not in ln:
                    continue
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("destino") == destino:
                    yield rec


def evento_confianza(destino):
    """El último evento `trust_cloud` de `destino` en la cadena, o None si no hay o si hay un
    `revoke_cloud` posterior."""
    ultimo = None
    for rec in _eventos_de(destino):
        if rec.get("evento") == "trust_cloud":
            ultimo = rec
        elif rec.get("evento") == "revoke_cloud":
            ultimo = None
    return ultimo


def exigir_confianza(destino, prefijo):
    """Lanza PuertaCerrada salvo que no haya HALT, `destino` empiece por `prefijo`, sea
    `es_trusted`, la cadena esté íntegra y su último `trust_cloud` lleve `via: 'tty'`. Devuelve
    ese evento."""
    if borde.halted():
        raise PuertaCerrada("HALT activo: no sale nada hacia '%s'" % destino)
    if not isinstance(destino, str) or not destino.startswith(prefijo) or len(destino) <= len(prefijo):
        raise PuertaCerrada("destino '%s' no vale: tiene que ser %s<proveedor>" % (destino, prefijo))
    if not borde.es_trusted(destino):
        raise PuertaCerrada("'%s' no está confiado (paso de {{TITULAR}}: trust-cloud en su terminal)" % destino)
    ok, det = borde.verificar_cadena()
    if not ok:
        raise PuertaCerrada("la cadena del borde no está íntegra: %s" % det)
    ok, det = cola_coincide_con_head()
    if not ok:
        raise PuertaCerrada("la cadena del borde no está íntegra: %s" % det)
    ev = evento_confianza(destino)
    if not ev or ev.get("via") != "tty":
        raise PuertaCerrada("'%s' está en la lista pero su trust_cloud no se tecleó en TTY" % destino)
    return ev


def cola_coincide_con_head():
    """(ok, detalle): el último sello de la cadena es el que dice `head.txt`. `verificar_cadena`
    recorre seq, prev y hash pero no mira la cola: borrar la ÚLTIMA línea (un revoke_cloud
    reciente) dejaba la cadena «íntegra» (nuevo problema del hallazgo 22). Bajo el cerrojo de la
    cadena, para no leer a medias un `_sellar` en curso."""
    with borde._Lock():
        seq, h = borde._read_head()
        ultimo = None
        ficheros = sorted((f for f in os.listdir(borde.BORDE_DIR) if f.startswith("ledger-")),
                          reverse=True) if os.path.isdir(borde.BORDE_DIR) else []
        for fn in ficheros:
            with open(os.path.join(borde.BORDE_DIR, fn), encoding="utf-8") as fh:
                lineas = [ln for ln in fh if ln.strip()]
            if lineas:
                ultimo = lineas[-1]
                break
    if ultimo is None:
        return (seq == 0, "cadena vacía" if seq == 0 else "head.txt dice seq=%d y no hay cadena" % seq)
    try:
        rec = json.loads(ultimo)
    except ValueError:
        return False, "última línea de la cadena ilegible"
    if (rec.get("seq"), rec.get("hash")) != (seq, h):
        return False, ("la cola no es la de head.txt (último seq=%s, head seq=%d): ¿cola truncada?"
                       % (rec.get("seq"), seq))
    return True, "cola = head (seq=%d)" % seq


EVENTO_REVISION = borde.EVENTO_REVISION_N1             # "n1_revision_humana"


def revision_humana(sha256):
    """True si la cadena está íntegra (y su cola es la de head.txt) y tiene un `n1_revision_humana`
    con `via: 'tty'` atado a ESTE sha256 (paso 1-bis: {{TITULAR}} miró la miniatura y tecleó VISTO-N1).
    Ese evento solo lo sella `borde.revisar_cristal_en_tty` (o la puerta de las fixtures de test,
    que deja alarma antes): hasta el 2-oct-26 cualquier `_sellar` lo fabricaba, y uno sin `via`, o
    una línea escrita a mano en el ledger, ya no cuenta. Lo usa la re-validación del envío de un
    TIFF cuyo OCR salta: sin esa revisión, no sale."""
    if not os.path.isdir(borde.BORDE_DIR) or not sha256:
        return False
    try:
        if not borde.verificar_cadena()[0] or not cola_coincide_con_head()[0]:
            return False
    except Exception:                                       # noqa: BLE001 — ilegible: no cuenta
        return False
    for fn in sorted(f for f in os.listdir(borde.BORDE_DIR) if f.startswith("ledger-")):
        with open(os.path.join(borde.BORDE_DIR, fn), encoding="utf-8") as fh:
            for ln in fh:
                if EVENTO_REVISION not in ln or sha256 not in ln:
                    continue
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("evento") == EVENTO_REVISION \
                        and rec.get("sha256") == sha256 and rec.get("via") == "tty":
                    return True
    return False


# ── Aviso del primer envío a cada destino ──────────────────────────────────────────────────
# El evento ya no basta (cierre del hueco que dejó declarado f3c2a2f, 2-oct-26): cualquiera que
# importe borde sella un `n1_aviso_primer_envio` y, hasta hoy, eso callaba el aviso. Ahora el aviso
# sale por salida.py ANTES de sellar, lleva un id (`Aviso N1-<12 hex>`, en el TEXTO: salida.py no
# devuelve ninguno) y el evento lleva ese id; solo cuenta como «ya avisado» si el id está en lo que
# salida.py registró de verdad: lo entregado (salida/enviados-*.jsonl), lo retenido por el silencio
# nocturno (notif/holding-*.jsonl) o lo aplazado al parte (aplazados/, también lo ya archivado).
# Un evento sin registro no calla nada: el siguiente envío vuelve a avisar y deja una alarma.
EVENTO_AVISO = "n1_aviso_primer_envio"
EVENTO_AVISO_SIN_REGISTRO = "n1_aviso_sin_registro"
_RE_ID_AVISO = re.compile(r"^N1-[0-9a-f]{12}$")


def _ficheros_registro_salida():
    """Los ficheros donde salida.py deja el TEXTO de un aviso a {{TITULAR}} que salió o va a salir."""
    import glob
    try:
        import salida  # noqa: E402 — solo para saber dónde escribe (su STATE)
        st = getattr(salida, "STATE", None)
    except Exception:                                       # noqa: BLE001
        st = None
    st = st or os.environ.get("BTP_STATE_DIR") or _casa.state_dir()
    rutas = []
    for patron in (("salida", "enviados-*.jsonl"), ("notif", "holding-*.jsonl"),
                   ("aplazados", "*.jsonl"), ("aplazados", "entregados", "*.jsonl")):
        rutas += sorted(glob.glob(os.path.join(st, *patron)))
    return rutas


def _epoca_utc(ts):
    """Segundos de un `ts` de la cadena («2026-10-02T10:00:00Z», UTC), o None."""
    try:
        return calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _epoca_local(ts):
    """Segundos de un `ts` de salida.py («2026-10-02T12:00:00», hora local), o None."""
    try:
        return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def aviso_registrado(id_aviso, destino=None, confianza=None):
    """¿Está el aviso `id_aviso` en lo que salida.py entregó, retuvo o aplazó?

    Con `destino` y `confianza` (el evento trust_cloud vigente), además (tercera pasada, 2-oct-26:
    el aviso se callaba sellando un evento con el id de un aviso VIEJO, que sí estaba en el
    registro): la línea del registro tiene que ser la de ESE aviso («Aviso <id>:»), nombrar ESE
    destino y ESA confianza (su `ts` va en el texto: «trust-cloud del <ts>») y llevar una hora
    posterior a ella. El id de un aviso a otro destino, o de antes de re-confiar, no vale."""
    if not isinstance(id_aviso, str) or not _RE_ID_AVISO.match(id_aviso):
        return False
    t0 = None
    if confianza is not None:
        t0 = _epoca_utc(confianza.get("ts"))
        if t0 is None:
            return False
    rx_dest = (re.compile(r"(?<![\w:\-])%s(?![\w:\-])" % re.escape(destino)) if destino else None)
    for ruta in _ficheros_registro_salida():
        try:
            with open(ruta, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    if id_aviso not in ln:
                        continue
                    if destino is None and confianza is None:
                        return True
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    texto = rec.get("texto") if isinstance(rec, dict) else None
                    if not isinstance(texto, str) or ("Aviso %s:" % id_aviso) not in texto:
                        continue
                    if rx_dest is not None and not rx_dest.search(texto):
                        continue
                    if confianza is not None:
                        t = _epoca_local(rec.get("ts") or "")
                        if ("trust-cloud del %s" % confianza.get("ts")) not in texto or t is None or t < t0:
                            continue
                    return True
        except OSError:
            continue
    return False


def _eventos_cadena():
    """Todos los eventos de la cadena, en orden."""
    if not os.path.isdir(borde.BORDE_DIR):
        return
    for fn in sorted(f for f in os.listdir(borde.BORDE_DIR) if f.startswith("ledger-")):
        with open(os.path.join(borde.BORDE_DIR, fn), encoding="utf-8") as fh:
            for ln in fh:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    yield rec


def _estado_aviso(destino):
    """(pendiente, eventos de aviso sin registro en salida desde el último trust_cloud). Un
    EVENTO_AVISO cuenta solo si su id no salió ya en otro EVENTO_AVISO anterior (de cualquier
    destino) y su línea del registro es la de este destino y esta confianza (`aviso_registrado`)."""
    pendiente, sin_registro, confianza, usados = False, [], None, set()
    for rec in _eventos_cadena():
        ev = rec.get("evento")
        if ev == EVENTO_AVISO:
            id_aviso = rec.get("aviso")
            repetido = id_aviso in usados
            if isinstance(id_aviso, str):
                usados.add(id_aviso)
            if rec.get("destino") != destino or not pendiente:
                continue
            if not repetido and aviso_registrado(id_aviso, destino, confianza):
                pendiente, sin_registro = False, []
            else:
                sin_registro.append(rec)
        elif ev == "trust_cloud" and rec.get("destino") == destino:
            pendiente, sin_registro, confianza = True, [], rec
    return pendiente, sin_registro


def aviso_pendiente(destino):
    """True si desde el último `trust_cloud` de `destino` no se ha avisado a {{TITULAR}} de un envío.
    La deduplicación vive en la cadena (evento EVENTO_AVISO), no en un fichero aparte: re-confiar
    un destino revocado vuelve a pedir aviso. Un EVENTO_AVISO cuyo id no está en el registro de
    salida.py no cuenta (forjarlo no calla el aviso)."""
    return _estado_aviso(destino)[0]


def avisar_primer_envio(destino, evento, n_ficheros, n_bytes, avisar=False):
    """Antes del PRIMER envío a `destino` (desde su último trust_cloud), un aviso a {{TITULAR}} por
    `salida.report_to_titular` (HALT, silencio nocturno y presupuesto diario de avisos los aplica
    salida.py). Sin aviso aceptado no hay envío: fail-closed.

    `avisar` es OPT-IN (norma: ningún test le escribe a {{TITULAR}}): lo ponen los CLI. Una llamada de
    librería con `avisar=False` y el aviso pendiente se para aquí, en vez de enviar callada.

    El aviso lleva un id (`Aviso N1-<12 hex>`) en la primera línea del texto y el evento
    EVENTO_AVISO lo repite; si un aviso anterior se selló sin registro en salida.py (forjado), se
    sella EVENTO_AVISO_SIN_REGISTRO de alarma y el texto se lo dice. Sin ruta de fichero en el
    texto: la casa de estilo de salida.py borra la línea que nombra `tools/….py` (y el texto de
    antes era una sola línea: salía vacío). Devuelve el veredicto de salida, o None si no tocaba
    avisar."""
    pendiente, sin_registro = _estado_aviso(destino)
    if not pendiente:
        return None
    if not avisar:
        raise PuertaCerrada("primer envío a '%s' sin aviso a {{TITULAR}}: el aviso es opt-in "
                            "(avisar=True; el CLI lo pone)" % destino)
    import salida  # noqa: E402 — solo aquí: el resto de la puerta no habla con nadie
    if salida.halted():
        raise PuertaCerrada("HALT activo: ni aviso ni envío a '%s'" % destino)
    if sin_registro:
        borde._sellar({"evento": EVENTO_AVISO_SIN_REGISTRO, "destino": destino, "nivel": "alarma",
                       "n": len(sin_registro),
                       "seqs": [rec.get("seq") for rec in sin_registro][:10]})
    id_aviso = "N1-" + os.urandom(6).hex()
    texto = ("Aviso %s: primer envío de copias N1 de tus láminas (sin identificar) a %s.\n"
             "%d fichero(s), %d bytes. Lo autoriza tu trust-cloud del %s.\n"
             "Para cortarlo, en tu terminal: borde.py revoke-cloud %s"
             % (id_aviso, destino, n_ficheros, n_bytes, evento.get("ts") if evento else "?", destino))
    if sin_registro:
        texto += ("\nOjo: en la cadena había %d aviso(s) de este primer envío sellado(s) sin pasar "
                  "por aquí (sin registro en salida). Por eso te aviso otra vez." % len(sin_registro))
    r = salida.report_to_titular(texto, voz="sobria", fuente="n1-primer-envio")
    if not isinstance(r, dict) or not (r.get("delivered") or r.get("aplazado") or r.get("retenido")):
        raise PuertaCerrada("no pude avisar a {{TITULAR}} del primer envío a '%s' (%s): no se envía"
                            % (destino, (r or {}).get("reason") if isinstance(r, dict) else r))
    borde._sellar({"evento": EVENTO_AVISO, "destino": destino, "nivel": "info", "aviso": id_aviso,
                   "veredicto": str(r.get("reason"))[:80]})
    return r


def main(argv):
    """`puerta_n1.py revisar <fichero>…` — mira ficheros de texto con la puerta (exit 0/3)."""
    if len(argv) < 2 or argv[0] != "revisar":
        print("uso: puerta_n1.py revisar <fichero.csv|json|geojson|txt|md>…")
        return 2
    try:
        exigir_diccionario()
        for r in argv[1:]:
            revisar_fichero_texto(r)
    except PuertaCerrada as e:
        print("🛑 %s" % e)
        return 3
    print("✅ pasa la puerta")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
