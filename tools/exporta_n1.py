#!/usr/bin/env python3
"""tools/exporta_n1.py — la ÚNICA boca que escribe en `~/Laminillas-N1/` (plan «laminillas DFCI»,
F1.4). Corre dentro de `exporta.sb` (lee SESION, escribe solo N1, sin red).

QUÉ EXPORTA, y qué mira antes de escribir un byte:
  · `tiff`  — copia N1 de lámina entera (P-HE y las de VALIS completo, que suben a la nube):
      - píxeles SIN recomprimir: los datos de entropía de cada tesela se copian byte a byte, y
        sus cabeceras en la forma canónica de libjpeg (para una tesela de libjpeg, sus mismos
        bytes; ver «Tercera pasada»);
      - solo IFDs teselados en JPEG (un IFD en tiras es una etiqueta o una macro: aborta), y cada
        IFD k>0 tiene que ser un nivel de la pirámide: factor potencia de 2, mayor que el del IFD
        anterior, con la misma proporción que L0 (±1 %). Una etiqueta teselada aborta;
      - lista blanca de tags = los del formato Grundium, con TIPO y NÚMERO de valores exactos
        (un tag permitido de tipo BYTE con 30 valores es texto en claro) y VALORES acotados
        (322/323 múltiplo de 16, 532 uno de los juegos estándar); los demás se quitan y se
        anotan; el 297 (PageNumber) solo en la forma del Grundium o la canónica, y se reescribe a
        la canónica (k, nº de IFDs); el 270 (ImageDescription) se reescribe a una constante; 282/283
        (resolución) se reescriben a su valor canónico (6 cifras significativas, X = Y, mpp
        plausible, y en los niveles k>0 el de L0 o el de L0/factor): sus 8 bytes ya no llevan
        texto;
      - cada tesela tiene que ser JPEG con SOLO DQT, DHT, SOF0 y SOS (APPn, COM, DRI, bytes tras
        EOI…: aborta), porque ahí cabe texto; y sus ids de componente tienen que casar con el 262
        de su IFD (FOTOMÉTRICA: si no, la Puerta y libtiff/OpenSlide ven otra imagen);
      - texto en el cristal: OCR (tesseract --psm 3 y --psm 11, palabras con confianza ≥60), lo
        leído por la Puerta de N1 (fechas y accesiones sin umbral de confianza) y detector de
        trazos de rotulador sobre el nivel ×8 completo Y cada nivel menor. Si salta, la lámina no
        entra hasta que {{TITULAR}} la mire (paso 1-bis, con su hoja) y lo diga tecleando en su terminal
        la orden de la hoja (lleva `--hoja <huella>`: ver «Paso 1-bis»);
      - sin RELLENO: cada nivel es un número entero de teselas y cada tesela un JPEG de su lado
        exacto (RELLENO DE TESELA, abajo); si no, aborta sin paso 1-bis.
  · `png`   — imagen para modelos de visión: PNG de lado largo ≤1024 px, con el mpp (o
      «thumbnail») en el nombre opaco y en el manifiesto; OCR + Puerta + trazos; se reescribe sin
      metadatos.
  · `texto` — CSV, JSON, GeoJSON, MD o TXT, por la Puerta de N1 (por esquema).

VERIFICACIÓN EN EL ENVÍO (`revalidar_n1`, la usan vision_n1 y nube_n1 justo antes de enviar): el
manifiesto no es prueba de procedencia (es un JSON sin firma y cualquiera puede escribir en N1).
PNG: exactamente lo que escribe `png_n1` (RGB de 8 bits sin entrelazar —sin paleta ni alfa—,
solo IHDR, IDAT contiguos e IEND, CRC, nada tras IEND, y el flujo zlib de los IDAT termina justo
donde acaban los píxeles: ni un byte detrás), ≤1024 px, y OCR + Puerta + trazos de nuevo. TIFF:
`copia_tiff_n1` en modo verificación, es decir, la copia canónica que esta tool sacaría del
fichero tiene que ser el fichero mismo, byte a byte (sha256 del manifiesto), y OCR + Puerta +
trazos del ×8 y los niveles menores otra vez; si saltan, solo sale con un `n1_revision_humana`
de la cadena atado a ese sha256 y con `via: 'tty'` (paso 1-bis), que solo sella
`borde.revisar_cristal_en_tty` (2-oct-26). Texto: la Puerta sobre los bytes.

Todo pasa antes por `puerta_n1.exigir_diccionario()` (fail-closed) y el nombre opaco por la puerta.
El manifiesto (`manifiesto.json`, sha256 por fichero) lo escribe solo esta tool, bajo cerrojo, y
pasa él mismo por la puerta. No sobrescribe nada.

TERCERA Y ÚLTIMA PASADA (2-oct-26, verificador independiente):
  · PNG en el envío: sale su recodificación CANÓNICA (`png_canonico`: los mismos píxeles, otros
    bytes), no el fichero, que podía llevar el nombre en claro en bloques zlib almacenados.
  · NOMBRE en el envío (`nombre_n1`, lo usan revalidar_n1, vision_n1 y nube_n1): solo los que
    escribe esta tool (RE_NOMBRE_TIFF, RE_NOMBRE_PNG, RE_NOMBRE_TEXTO), con la parte libre por la
    Puerta. «24B0001043.png» colado en N1 era el nombre del objeto en la nube.
  · TESELAS: leídas campo a campo (longitudes exactas, cada tabla definida una vez y usada, un
    barrido baseline), reescritas en la forma canónica de libjpeg (`tesela_canonica`) y TODAS las
    de TODOS los niveles decodificadas con libjpeg-turbo en estricto (`decodifica_jpeg`: un aviso
    de «Corrupt JPEG data» o bytes sobrantes es error). Una DQT de más, un SOF0 alargado o texto en
    la entropía de L0, fuera. Sin libturbojpeg 3 no se exporta ni se envía.
  · ×4: entero por el OCR + Puerta, y cada nivel mayor que el ×8, reducido a su tamaño, tiene que
    parecerse a él (`incoherencia` ≤ UMBRAL_COHERENCIA). Un rótulo pintado solo en el ×4 o solo
    en L0 es motivo 1-bis SOLO si mueve la media de una ventana de la coherencia más del umbral:
    los pequeños salen (cotas medidas en LÍMITES DECLARADOS, `rotulo_de_un_solo_nivel`).
  · Láminas REALES del Grundium (2-oct-26, ver «Niveles por TESELAS» y RELLENO): el ×4 y la
    coherencia se calculan por teselas (mismo resultado que sobre el nivel entero, sin pasar el
    tope de Pillow ni subirlo), y en la coherencia el blanco 255 del relleno no escaneado no cuenta.
  · RELLENO DE TESELA (2-oct-26): todo lo que se mira (OCR, Puerta, trazos, coherencia) se recorta
    al ancho y alto declarados, y lo pintado en el relleno de una tesela del borde (más allá de w o
    h) salía a N1 con rc 0 y un OCR lo leía decodificando la tesela entera. Medido en las 15 láminas
    por la ventanilla (`laminillas_exporta -- diagnostico relleno`, solo números): ninguno de sus 47
    niveles tiene relleno (ancho y alto múltiplos de 512) y sus 114 235 teselas declaran 512×512 en
    el SOF0. Así que la Puerta exige eso (`_comprueba_sin_relleno` y `tesela_canonica`): cada nivel,
    un número entero de teselas, y cada tesela, un JPEG del lado exacto de la tesela (ni relleno ni
    un JPEG más pequeño que deje parte sin cubrir o guarde MCUs que no se decodifican). Si no,
    PuertaCerrada SIN paso 1-bis (la miniatura no lo enseña). Más estricto que «relleno liso»: no
    hay umbral de ruido que fijar ni queda la banda del MCU que el relleno comparte con lo visible.
    Coste: un TIFF de otro escáner con relleno no sale; habría que medirlo y decidir otra vez.
  · PASO 1-BIS (2-oct-26): VISTO-N1 solo confirma lo que enseñó la hoja. La orden de la hoja lleva
    `--hoja <huella>` y exporta_n1 se para antes de preguntar si lo que ve no es lo que la hoja
    enseña o si hay más de TOPE_VENTANAS_1BIS ventanas de coherencia (ver «Paso 1-bis», abajo).
  · FOTOMÉTRICA (2-oct-26, 2.ª verificación adversarial): el 262 de la copia es el de origen y la
    Puerta decodifica por los ids del SOF0; libtiff, OpenSlide y tifffile, por el 262. Teselas
    YCbCr con 262 = 2 y un rótulo solo en el croma salían con rc 0 (la Puerta no lo leía; OpenSlide
    lo pintaba con +31 de luma). Ahora cada tesela casa con el 262 de su IFD o no sale, también en
    la verificación del envío (`_IDS_POR_FOTOMETRICA`; `Fotometrica` en el test).
LÍMITES DECLARADOS (regla de convergencia: solo los explota un agente que fabrica el fichero a
propósito; obstáculo y rastro, no frontera), cada uno con su test:
  · `test_limite_declarado_texto_en_valores_de_pixel`: lo que va en los VALORES de los píxeles
    (RGB de un PNG, coeficientes DCT de una tesela) es la imagen y sale con ella; la recodificación
    lo saca de los bytes en claro, no de la imagen decodificada (y un PNG de puro ruido puede salir
    en bloques zlib almacenados).
  · `test_limite_declarado_valores_de_una_dqt_usada`: los valores de una DQT o DHT que SÍ se usa
    son libres (63 bytes de AC por DQT en una tesela lisa, sin cambiar un píxel). Exigir las tablas
    de IJG rompería una lámina con tablas propias del escáner.
  · `test_limite_declarado_microtexto_en_l0`: L0 (y un ×2) no pasa por el OCR; un rótulo de ~24 px
    en L0 es 6 px en el ×4 y 3 en el ×8, y ni el OCR ni la coherencia lo ven. Más: medido el
    2-oct-26 (2.ª verificación), un rótulo oscuro pintado directamente sobre tejido en L0 ANTES de
    reducir —como lo haría un escáner— sale con 60 y 80 px de alto en L0 con los dos Pillow
    probados (11.3, el de /usr/bin/python3 que usa test_all, y 12.2, el de Homebrew); entre 100 y
    180 px sale o no según la posición y la versión (140, 160 y 180 salen en alguna), y el OCR del
    ×4 lo cierra siempre desde 200 px (200-400 medidos con las dos). Sobre tejido el OCR no es
    monótono. Cotas fijadas (80 sale, 240 se cierra) en
    `test_limite_declarado_rotulo_de_un_solo_nivel`. Dentro de este mismo
    límite, la coherencia no compara el resto de la división por el factor en el borde derecho e
    inferior de cada nivel (< 8 px de L0, < 2 del ×4: `_zona_coherencia`); el resto de la imagen,
    sí, ventanas parciales del borde incluidas (`CoherenciaBordes`, 2-oct-26).
  · `test_limite_declarado_rotulo_de_un_solo_nivel` (2-oct-26, 2.ª verificación): un rótulo pegado
    en UN solo nivel después de sacar los demás solo lo ve la coherencia, que promedia ventanas de
    16×16 px del ×8 (128×128 de L0) con umbral 24. Medido (tinta (40, 30, 60) sobre tejido): solo
    en L0 sale con rc 0 hasta 60 px de alto si cae dentro de una fila de ventanas (80 se cierra) y
    hasta 100 px si cae a caballo de dos (120 se cierra); solo en el ×4, hasta 14 px dentro (16 se
    cierra) y 24 a caballo (28 se cierra), y el OCR del ×4 no lo lee. La copia N1 lleva sus
    píxeles y `revalidar_n1` la acepta. Hay que fabricar el fichero: un escáner pinta el rótulo en
    todos los niveles, y ahí el OCR del ×4 cierra uno de 100-120 px en las posiciones medidas.
  · `test_limite_declarado_rotulo_solo_en_el_croma` (2-oct-26): con el 262 casando, la Puerta y
    los lectores ven la misma imagen, pero el OCR lee en luma: un rótulo pintado solo en el croma
    (Y la del fondo) sale con Cb +60, +90, −60 y Cr +90 (70 px de alto en el ×8); el OCR del ×4
    cierra Cb +120 y Cr +60, y los trazos, Cr −60. Una persona o un modelo de visión sí lo ven.
  · `test_limite_declarado_blanco_en_nivel_fino`: sin el polígono de la zona escaneada, el blanco
    casi puro (≥250 en los tres canales) de una ventana de relleno de L0 o del ×4 no cuenta en la
    coherencia: una forma pintada en blanco sobre el vidrio de un nivel fino no la ve (tampoco la
    veía antes: 255 frente a vidrio ~229 era el falso positivo del relleno). Al ×4 lo lee el OCR.
  · `test_limite_declarado_huella_calculable_sin_hoja`: la huella del paso 1-bis (`huella_1bis`)
    no es secreta ni una firma: ata la hoja a lo que se exporta. Quien la calcula a propósito, sin
    generar la hoja, llega a la pregunta; VISTO-N1 sigue haciendo falta en la terminal de {{TITULAR}}.
Coste: si una lámina REAL del Grundium da un aviso de libjpeg o trae un SOF0 o unos ids de
componente fuera de lo admitido, no entra (fail-closed) y hay que mirarlo; si sus niveles no se
parecen, pasa al paso 1-bis. Medido el 2-oct-26 en las láminas reales (diagnostico y qc
coherencia, solo números): ninguna tesela fuera; el 297, el relleno y el tope de Pillow del ×4 del
hueso, que cerraban las 15, arreglados aquí (ver arriba).

Uso:
  python3 tools/exporta_n1.py tiff  <lamina.tif> --opaco P-HE [--revisado-en-tty --hoja <huella>]
  python3 tools/exporta_n1.py png   <captura.png> --opaco P-KI67 --nivel x8 --mpp 2.0048
  python3 tools/exporta_n1.py png   <mini.png> --opaco P-KI67 --nivel thumbnail
  python3 tools/exporta_n1.py texto <tabla.csv> --nombre P-KI67-regiones.csv
"""
import argparse
import ctypes
import fcntl
import hashlib
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import zlib
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puerta_n1 as puerta  # noqa: E402 — fija el estado antes de que nadie importe borde
from puerta_n1 import PuertaCerrada  # noqa: E402

CONSTANTE_270 = b"polaris-n1\x00"
_OPACO = r"[A-Z][A-Z0-9]{0,3}(?:-[A-Z0-9]{1,12}){0,3}"
RE_OPACO = re.compile(r"^%s$" % _OPACO)
RE_NOMBRE_TEXTO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.(csv|json|geojson|md|txt)$")
# Los únicos nombres que escribe esta tool (lista blanca del envío, tercera pasada): la copia de
# lámina «P-HE.n1.tif», la imagen «P-KI67__x8__mpp2.0048.png» o «P-KI67__thumbnail.png», y un texto
# con RE_NOMBRE_TEXTO. El opaco va además por la Puerta.
RE_NOMBRE_TIFF = re.compile(r"^(%s)\.n1\.tif$" % _OPACO)
RE_NOMBRE_PNG = re.compile(r"^(%s)__(?:(?:L0|x4|x8)__mpp\d{1,3}\.\d{4}|thumbnail)\.png$" % _OPACO)
LADO_MAX_PNG = 1024
NIVELES = ("L0", "x4", "x8", "thumbnail")
CONF_OCR = 60
RE_PALABRA_OCR = re.compile(r"[A-Za-z0-9]{4,}")
PALABRA_VISTO = puerta.borde.PALABRA_VISTO_N1          # "VISTO-N1": la pide borde.revisar_cristal_en_tty

# Tags del formato (medido sobre las 15 láminas: 254, 256-259, 262, 270, 274, 277, 282-284, 296,
# 297, 322-325, 530, 532) con TIPO y NÚMERO DE VALORES exactos (hallazgo 20, 1-oct-26): un tag
# permitido de tipo BYTE o con un count libre es un sitio donde esconder texto, y se rechaza.
# Tipos y counts según la especificación TIFF 6.0 para cada tag (el BYTE no lo usa ninguno);
# los valores de los tags enumerados, acotados a lo que el formato admite.
# (tipos, count; None = el nº de teselas; 0 = cualquiera —solo el 270, que se reescribe—, valores)
# Segunda pasada (hallazgo 20 bis): un tag con tipo y count correctos pero valor libre seguía
# llevando ~20 bytes por IFD (282/283: 8 cada uno; 297: 4). 282/283 se reescriben a su forma
# canónica (ver `_resoluciones`); 297, 322/323 y 532 se acotan aquí. Los juegos de 532 son los
# estándar (JPEG YCbCr de rango completo, RGB, YCbCr de vídeo); inferencia: el Grundium escribe el
# primero, el valor por defecto de libtiff. Si una lámina real trae otro, aborta (fail-closed) y
# hay que añadirlo aquí a la vista.
# 297 (PageNumber), medido el 2-oct-26 sobre las láminas reales (diagnostico de 744306d): el
# Grundium escribe (log2 del factor del nivel, nº de IFDs − 1) —(0,2), (2,2), (3,2) en una lámina
# de 3 niveles; (0,3), (2,3), (3,3), (4,3) en B-HE-2—, con página ≥ total en los niveles k>0. Aquí
# solo se acota; la forma la mira `_comprueba_297` con la pirámide ya leída, y la copia N1 lleva
# su forma CANÓNICA (k, nº de IFDs), derivada de la estructura: el valor de origen nunca se copia.
_CORTO_LARGO = {3, 4}
_RBW_ESTANDAR = frozenset({(0, 1, 255, 1, 128, 1, 255, 1, 128, 1, 255, 1),
                           (0, 1, 255, 1, 0, 1, 255, 1, 0, 1, 255, 1),
                           (16, 1, 235, 1, 128, 1, 240, 1, 128, 1, 240, 1)})


def _lado_tesela(v):
    return 16 <= v[0] <= 4096 and v[0] % 16 == 0


TAGS_FORMATO = {
    254: ({3, 4}, 1, lambda v: v[0] in (0, 1)),              # NewSubfileType
    256: (_CORTO_LARGO, 1, None),                            # ImageWidth
    257: (_CORTO_LARGO, 1, None),                            # ImageLength
    258: ({3}, 3, lambda v: list(v) == [8, 8, 8]),           # BitsPerSample
    259: ({3}, 1, lambda v: v[0] == 7),                      # Compression = JPEG
    262: ({3}, 1, lambda v: v[0] in (2, 6)),                 # RGB o YCbCr
    270: ({2}, 0, None),                                     # ImageDescription → constante
    274: ({3}, 1, lambda v: 1 <= v[0] <= 8),                 # Orientation
    277: ({3}, 1, lambda v: v[0] == 3),                      # SamplesPerPixel
    282: ({5}, 1, lambda v: v[0] >= 1 and v[1] >= 1),        # XResolution → canónica
    283: ({5}, 1, lambda v: v[0] >= 1 and v[1] >= 1),        # YResolution → canónica
    284: ({3}, 1, lambda v: v[0] == 1),                      # PlanarConfiguration
    296: ({3}, 1, lambda v: v[0] in (1, 2, 3)),              # ResolutionUnit
    297: ({3}, 2, lambda v: v[0] < 64 and v[1] < 64),        # PageNumber → canónico (_comprueba_297)
    322: (_CORTO_LARGO, 1, _lado_tesela),                    # TileWidth (múltiplo de 16)
    323: (_CORTO_LARGO, 1, _lado_tesela),                    # TileLength
    324: ({4, 16}, None, None),                              # TileOffsets
    325: ({3, 4, 16}, None, None),                           # TileByteCounts
    530: ({3}, 2, lambda v: all(x in (1, 2, 4) for x in v)), # YCbCrSubSampling
    532: ({5}, 6, lambda v: tuple(v) in _RBW_ESTANDAR),      # ReferenceBlackWhite
}
TAGS_BLANCOS = {t: tipos for t, (tipos, _c, _v) in TAGS_FORMATO.items()}   # compatibilidad
_TAM = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}
_FMT = {1: "B", 3: "H", 4: "I", 16: "Q"}
_FMT_VAL = {**_FMT, 5: "II"}                 # para mirar valores: un RATIONAL son dos LONG


# ── TIFF: lectura ──────────────────────────────────────────────────────────────────────────
class Tiff:
    """Lector mínimo de TIFF/BigTIFF: IFDs de la cadena principal con sus entradas crudas."""

    def __init__(self, ruta):
        self.f = open(ruta, "rb")
        cab = self.f.read(16)
        if cab[:2] not in (b"II", b"MM"):
            raise PuertaCerrada("no es un TIFF")
        self.bo = "<" if cab[:2] == b"II" else ">"
        ver = struct.unpack(self.bo + "H", cab[2:4])[0]
        if ver == 42:
            self.big = False
            off = struct.unpack(self.bo + "I", cab[4:8])[0]
        elif ver == 43:
            self.big = True
            off = struct.unpack(self.bo + "Q", cab[8:16])[0]
        else:
            raise PuertaCerrada("versión TIFF desconocida")
        self.ifds = []
        vistos = set()
        while off:
            if off in vistos or len(self.ifds) > 64:
                raise PuertaCerrada("cadena de IFDs en bucle")
            vistos.add(off)
            ifd, off = self._lee_ifd(off)
            self.ifds.append(ifd)

    def _lee_ifd(self, off):
        f, bo = self.f, self.bo
        f.seek(off)
        if self.big:
            n = struct.unpack(bo + "Q", f.read(8))[0]
            tam_e, inline = 20, 8
        else:
            n = struct.unpack(bo + "H", f.read(2))[0]
            tam_e, inline = 12, 4
        crudo = f.read(n * tam_e)
        sig = struct.unpack(bo + ("Q" if self.big else "I"), f.read(8 if self.big else 4))[0]
        ent = {}
        for i in range(n):
            e = crudo[i * tam_e:(i + 1) * tam_e]
            tag, tipo = struct.unpack(bo + "HH", e[:4])
            cnt = struct.unpack(bo + ("Q" if self.big else "I"), e[4:4 + (8 if self.big else 4)])[0]
            campo = e[4 + (8 if self.big else 4):]
            tam = _TAM.get(tipo)
            if tam is None:
                raise PuertaCerrada("tipo TIFF desconocido %d en tag %d" % (tipo, tag))
            nb = tam * cnt
            if nb <= inline:
                val = campo[:nb]
            else:
                p = struct.unpack(bo + ("Q" if self.big else "I"), campo)[0]
                f.seek(p)
                val = f.read(nb)
                if len(val) != nb:
                    raise PuertaCerrada("tag %d truncado" % tag)
            ent[tag] = (tipo, cnt, val)
        return ent, sig

    def enteros(self, ifd, tag):
        tipo, cnt, val = ifd[tag]
        if tipo not in _FMT:
            raise PuertaCerrada("tag %d no es entero" % tag)
        return list(struct.unpack(self.bo + _FMT[tipo] * cnt, val))

    def lee(self, off, n):
        self.f.seek(off)
        b = self.f.read(n)
        if len(b) != n:
            raise PuertaCerrada("tesela truncada")
        return b


# ── Teselas JPEG: forma canónica y decodificación estricta (tercera pasada, 2-oct-26) ────────────
# Hasta hoy la tesela se copiaba tal cual si solo traía DQT, DHT, SOF0 y SOS, y eso dejaba sitio:
# una tabla DQT de más que ningún componente usa (64 bytes libres), un SOF0 más largo de lo que
# dicen sus componentes, o texto en lugar de los datos de entropía de una tesela de L0 (que el OCR
# no mira). Ahora cada tesela se LEE campo a campo (longitudes exactas; tablas definidas una vez y
# todas usadas; un solo barrido baseline entrelazado), se REESCRIBE en la forma canónica de libjpeg
# (SOI, DQT en el orden en que las usan los componentes, SOF0, DHT en el orden del barrido —DC y AC
# de cada componente—, SOS, entropía, EOI; para una tesela escrita por libjpeg, sus mismos bytes) y
# se DECODIFICA con libjpeg-turbo en modo estricto: cualquier aviso de libjpeg (datos corruptos, fin
# prematuro, bytes sobrantes antes de un marcador) aborta. Los datos de entropía no se recomprimen.
_SOI, _EOI = b"\xff\xd8", b"\xff\xd9"
_IDS_COMPONENTES = frozenset({(1, 2, 3), (0, 1, 2), (82, 71, 66)})     # YCbCr, YCbCr desde 0, «RGB»
# FOTOMÉTRICA (262) frente a los ids del SOF0 (2-oct-26, verificador independiente, 2.ª pasada).
# Sin APPn (se rechazan), libjpeg —la Puerta: libjpeg-turbo y Pillow— decide el espacio de color
# por los ids: (1,2,3) y cualquier otro → YCbCr, que convierte a RGB; «RGB» → sin conversión.
# libtiff, OpenSlide y tifffile mandan por el 262: 2 (RGB) → sin conversión; 6 (YCbCr) → convierte.
# Si no casan, la Puerta y el lector de después ven OTRA imagen. Medido en sintético: teselas YCbCr
# (ids 1,2,3) con un rótulo solo en el croma y 262 = 2 salían a N1 con rc 0 (la Puerta lo ve con
# la misma luma que el fondo, −2 niveles, y su OCR no lee nada) y OpenSlide y tifffile lo pintan
# con +31 de luma, legible por el mismo OCR. Ahora cada tesela tiene que casar con el 262 de su
# IFD (`_IDS_POR_FOTOMETRICA`, en `copia_tiff_n1`, también en la verificación del envío). Las 15
# láminas reales: 262 = 6 (YCbCr) en L0, verificado en el manifiesto de SESION por la ventanilla
# (solo el enum); sus ids, inferencia (libtiff con JPEG YCbCr escribe 1,2,3).
_IDS_POR_FOTOMETRICA = {6: frozenset({(1, 2, 3), (0, 1, 2)}), 2: frozenset({(82, 71, 66)})}


def _seg(marca, cuerpo):
    return b"\xff" + bytes([marca]) + struct.pack(">H", len(cuerpo) + 2) + cuerpo


def _lee_dqt(cuerpo, dqt):
    j = 0
    while j < len(cuerpo):
        pq, tq = cuerpo[j] >> 4, cuerpo[j] & 15
        if pq != 0 or tq > 3:
            raise PuertaCerrada("tesela con una DQT que no es baseline de 8 bits (Pq=%d, Tq=%d)" % (pq, tq))
        vals = cuerpo[j + 1:j + 65]
        if len(vals) != 64 or 0 in vals:
            raise PuertaCerrada("tesela con una DQT de longitud o valores no válidos")
        if tq in dqt:
            raise PuertaCerrada("tesela con la DQT %d definida dos veces" % tq)
        dqt[tq] = vals
        j += 65


def _lee_dht(cuerpo, dht):
    j = 0
    while j < len(cuerpo):
        tc, th = cuerpo[j] >> 4, cuerpo[j] & 15
        if tc > 1 or th > 3:
            raise PuertaCerrada("tesela con una DHT de clase o índice no válidos")
        cuentas = cuerpo[j + 1:j + 17]
        if len(cuentas) != 16:
            raise PuertaCerrada("tesela con una DHT truncada")
        n = sum(cuentas)
        simbolos = cuerpo[j + 17:j + 17 + n]
        if not 0 < n <= 256 or len(simbolos) != n or len(set(simbolos)) != n:
            raise PuertaCerrada("tesela con una DHT de símbolos no válidos")
        if tc == 0 and max(simbolos) > 11:
            raise PuertaCerrada("tesela con una DHT de DC con categorías imposibles")
        if tc == 1 and any(s not in (0x00, 0xF0) and not 1 <= (s & 15) <= 10 for s in simbolos):
            raise PuertaCerrada("tesela con una DHT de AC con símbolos imposibles")
        codigo = 0                                        # código prefijo válido (como jdhuff.c)
        for si in range(1, 17):
            codigo += cuentas[si - 1]
            if codigo >= (1 << si):
                raise PuertaCerrada("tesela con una DHT que no es un código de Huffman válido")
            codigo <<= 1
        if (tc, th) in dht:
            raise PuertaCerrada("tesela con la DHT %d/%d definida dos veces" % (tc, th))
        dht[(tc, th)] = cuerpo[j + 1:j + 17 + n]
        j += 17 + n


def tesela_canonica(b, tw=None, th=None, fotometrica=None):
    """Los bytes CANÓNICOS de la tesela JPEG `b` (ver arriba), o PuertaCerrada. `tw`/`th`: el lado
    de la tesela del TIFF; el SOF0 no puede pasar de él. `fotometrica`: el 262 de su IFD; si se
    da, los ids del SOF0 tienen que casar con él (`_IDS_POR_FOTOMETRICA`)."""
    if b[:2] != _SOI:
        raise PuertaCerrada("tesela sin SOI")
    i, n = 2, len(b)
    dqt, dht, sof, sos, entropia = {}, {}, None, None, None
    while True:
        if i >= n:
            raise PuertaCerrada("tesela JPEG incompleta (sin EOI)")
        if b[i] != 0xFF:
            raise PuertaCerrada("tesela con bytes sueltos fuera de un segmento")
        while i < n and b[i] == 0xFF:
            i += 1
        if i >= n:
            raise PuertaCerrada("tesela JPEG incompleta (sin EOI)")
        m = b[i]
        i += 1
        if m == 0xD9:                                        # EOI
            if b[i:].strip(b"\x00"):
                raise PuertaCerrada("tesela con datos tras EOI")
            break
        nombre = {0xDB: "DQT", 0xC4: "DHT", 0xC0: "SOF0", 0xDA: "SOS"}.get(m)
        if not nombre:
            raise PuertaCerrada("tesela con segmento JPEG no permitido 0xFF%02X" % m)
        if sos is not None:
            raise PuertaCerrada("tesela con un segmento tras el barrido (solo uno, baseline)")
        if i + 2 > n:
            raise PuertaCerrada("segmento truncado")
        ln = struct.unpack(">H", b[i:i + 2])[0]
        if ln < 2 or i + ln > n:
            raise PuertaCerrada("segmento truncado")
        cuerpo = b[i + 2:i + ln]
        i += ln
        if nombre == "DQT":
            _lee_dqt(cuerpo, dqt)
        elif nombre == "DHT":
            _lee_dht(cuerpo, dht)
        elif nombre == "SOF0":
            if sof is not None or len(cuerpo) < 6:
                raise PuertaCerrada("tesela con dos SOF0 o uno truncado")
            prec, alto, ancho, nf = struct.unpack(">BHHB", cuerpo[:6])
            if len(cuerpo) != 6 + 3 * nf:
                raise PuertaCerrada("tesela con un SOF0 de longitud distinta de la exacta")
            comps = [tuple(cuerpo[6 + 3 * c:9 + 3 * c]) for c in range(nf)]
            if prec != 8 or nf != 3 or tuple(c[0] for c in comps) not in _IDS_COMPONENTES:
                raise PuertaCerrada("tesela con un SOF0 fuera del formato (8 bits, 3 componentes)")
            if fotometrica is not None and tuple(c[0] for c in comps) not in \
                    _IDS_POR_FOTOMETRICA.get(fotometrica, ()):
                raise PuertaCerrada("tesela con ids de componente %s y fotométrica (262) %d: la Puerta "
                                    "(libjpeg, por los ids) y un lector que manda por el 262 (libtiff, "
                                    "OpenSlide) verían colores distintos: no exporto"
                                    % (list(c[0] for c in comps), fotometrica))
            if any(not (1 <= c[1] >> 4 <= 2 and 1 <= c[1] & 15 <= 2) for c in comps[:1]) \
                    or any(c[1] != 0x11 for c in comps[1:]) or any(c[2] > 3 for c in comps):
                raise PuertaCerrada("tesela con un SOF0 de muestreo o tablas fuera del formato")
            if not 1 <= ancho <= 65535 or not 1 <= alto <= 65535:
                raise PuertaCerrada("tesela cuyo JPEG no tiene dimensiones")
            if tw and th and (ancho, alto) != (tw, th):      # RELLENO DE TESELA (2-oct-26)
                raise PuertaCerrada("tesela cuyo JPEG mide %dx%d y la tesela del TIFF %dx%d: lo que no "
                                    "cubre, o lo que el JPEG guarda y no se decodifica, nadie lo mira "
                                    "(el Grundium escribe teselas enteras): no exporto"
                                    % (ancho, alto, tw, th))
            sof = (cuerpo[:6], comps)
        else:                                                # SOS y datos de entropía
            if sof is None or not cuerpo:
                raise PuertaCerrada("tesela con el SOS antes del SOF0")
            ns = cuerpo[0]
            if len(cuerpo) != 1 + 2 * ns + 3 or ns != 3:
                raise PuertaCerrada("tesela con un SOS de longitud distinta de la exacta o no entrelazado")
            sel = [tuple(cuerpo[1 + 2 * c:3 + 2 * c]) for c in range(ns)]
            if [s[0] for s in sel] != [c[0] for c in sof[1]] or cuerpo[1 + 2 * ns:] != b"\x00\x3f\x00" \
                    or any((s[1] >> 4) > 3 or (s[1] & 15) > 3 for s in sel):
                raise PuertaCerrada("tesela con un SOS que no es un barrido baseline de los tres componentes")
            sos = (cuerpo, sel)
            ini = i
            while i < n:                                     # entropía hasta el primer marcador
                if b[i] == 0xFF and i + 1 < n and b[i + 1] != 0x00:
                    if 0xD0 <= b[i + 1] <= 0xD7:
                        raise PuertaCerrada("tesela con RSTn (sin DRI permitido)")
                    break
                i += 2 if b[i] == 0xFF else 1
            entropia = b[ini:i]
    if sof is None or sos is None or entropia is None:
        raise PuertaCerrada("tesela JPEG incompleta (sin SOF0 o sin SOS)")
    usadas_q = []
    for c in sof[1]:
        if c[2] not in usadas_q:
            usadas_q.append(c[2])
    usadas_h = []
    for _cs, t in sos[1]:
        for clave in ((0, t >> 4), (1, t & 15)):
            if clave not in usadas_h:
                usadas_h.append(clave)
    if set(dqt) != set(usadas_q):
        raise PuertaCerrada("tesela con tablas DQT que no usa ningún componente, o que faltan")
    if set(dht) != set(usadas_h):
        raise PuertaCerrada("tesela con tablas DHT que no usa el barrido, o que faltan")
    out = bytearray(_SOI)
    for tq in usadas_q:
        out += _seg(0xDB, bytes([tq]) + dqt[tq])
    out += _seg(0xC0, sof[0] + b"".join(bytes(c) for c in sof[1]))
    for tc, th_ in usadas_h:
        out += _seg(0xC4, bytes([tc << 4 | th_]) + dht[(tc, th_)])
    out += _seg(0xDA, sos[0]) + entropia + _EOI
    return bytes(out)


# libjpeg-turbo 3 (TurboJPEG, de Homebrew jpeg-turbo) por ctypes: Pillow no avisa de los «Corrupt
# JPEG data» y los decodifica igual; TJPARAM_STOPONWARNING los convierte en error. Sin la librería,
# no se exporta ni se envía (fail-closed).
_RUTAS_TJ = ("/opt/homebrew/lib/libturbojpeg.0.dylib", "/opt/homebrew/opt/jpeg-turbo/lib/libturbojpeg.0.dylib",
             "/usr/local/lib/libturbojpeg.0.dylib", "/usr/local/opt/jpeg-turbo/lib/libturbojpeg.0.dylib")
_TJ = []


class _EscalaTJ(ctypes.Structure):
    _fields_ = [("num", ctypes.c_int), ("denom", ctypes.c_int)]


def _tj():
    if _TJ:
        return _TJ[0]
    import ctypes.util
    for ruta in (ctypes.util.find_library("turbojpeg"),) + _RUTAS_TJ:
        if not ruta:
            continue
        try:
            lib = ctypes.CDLL(ruta)
            lib.tj3Init
        except (OSError, AttributeError):
            continue
        h, i, s, b = ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_char_p
        lib.tj3Init.restype, lib.tj3Init.argtypes = h, [i]
        lib.tj3Set.argtypes = [h, i, i]
        lib.tj3Get.argtypes = [h, i]
        lib.tj3SetScalingFactor.argtypes = [h, _EscalaTJ]
        lib.tj3DecompressHeader.argtypes = [h, b, s]
        lib.tj3Decompress8.argtypes = [h, b, s, ctypes.c_void_p, i, i]
        lib.tj3GetErrorStr.restype, lib.tj3GetErrorStr.argtypes = b, [h]
        lib.tj3Destroy.argtypes = [h]
        _TJ.append(lib)
        return lib
    raise PuertaCerrada("sin libturbojpeg 3 (Homebrew jpeg-turbo) no puedo decodificar las teselas: "
                        "no exporto ni envío")


def decodifica_jpeg(b, den=1):
    """(ancho, alto, RGB) de la tesela `b` a 1/`den` (1, 2, 4, 8: escala DCT de libjpeg, que igual
    recorre TODOS los datos de entropía). Estricto: un aviso de libjpeg es PuertaCerrada."""
    lib = _tj()
    h = lib.tj3Init(1)                                       # TJINIT_DECOMPRESS
    if not h:
        raise PuertaCerrada("libturbojpeg no arranca")
    try:
        def falla(que):
            return PuertaCerrada("tesela que no decodifica limpia (%s: %s): no exporto ni envío"
                                 % (que, (lib.tj3GetErrorStr(h) or b"?").decode("latin-1")[:120]))
        if lib.tj3Set(h, 0, 1) != 0:                         # TJPARAM_STOPONWARNING
            raise falla("modo estricto")
        if lib.tj3DecompressHeader(h, b, len(b)) != 0:
            raise falla("cabecera")
        w, alto = lib.tj3Get(h, 5), lib.tj3Get(h, 6)         # TJPARAM_JPEGWIDTH / JPEGHEIGHT
        if den != 1 and lib.tj3SetScalingFactor(h, _EscalaTJ(1, den)) != 0:
            raise falla("escala 1/%d" % den)
        sw, sh = -(-w // den), -(-alto // den)
        buf = ctypes.create_string_buffer(sw * sh * 3)
        if lib.tj3Decompress8(h, b, len(b), buf, 0, 0) != 0:     # TJPF_RGB
            raise falla("datos")
        return sw, sh, buf.raw
    finally:
        lib.tj3Destroy(h)


def _nivel_x8(t):
    """Índice del IFD cuyo ancho es L0/8 (±1 %), o None."""
    w0 = t.enteros(t.ifds[0], 256)[0]
    for k, ifd in enumerate(t.ifds):
        w = t.enteros(ifd, 256)[0]
        if w and abs(w0 / float(w) - 8.0) <= 0.08:
            return k
    return None


def _comprueba_tags(t, k, ifd):
    """Tipo, número de valores y valores de cada tag de la lista blanca (hallazgo 20). Los que no
    están en la lista no se miran aquí: se quitan."""
    for tag, (tipo, cnt, val) in sorted(ifd.items()):
        fmt = TAGS_FORMATO.get(tag)
        if fmt is None:
            continue
        tipos, n, valido = fmt
        if tipo not in tipos:
            raise PuertaCerrada("IFD %d: tag %d con tipo %d no permitido (formato: tipo %s)"
                                % (k, tag, tipo, "/".join(str(x) for x in sorted(tipos))))
        if n and cnt != n:
            raise PuertaCerrada("IFD %d: tag %d con %d valores (formato: %d)" % (k, tag, cnt, n))
        if valido is not None and not valido(list(struct.unpack(t.bo + _FMT_VAL[tipo] * cnt, val))):
            raise PuertaCerrada("IFD %d: tag %d con un valor fuera del formato" % (k, tag))
    for tag in (256, 257, 259):
        if tag not in ifd:
            raise PuertaCerrada("IFD %d sin el tag %d" % (k, tag))


def _comprueba_piramide(t):
    """Cada IFD k>0 tiene que ser un nivel de la pirámide de L0 (hallazgo 16): factor potencia de
    2 y mayor que el del anterior, y las dos dimensiones a L0/factor (±1 %, o ±1 px por el
    redondeo). Un IFD que no lo es (una etiqueta teselada, una macro) aborta: el OCR solo mira
    el ×8 y los niveles menores, así que lo que no es pirámide no se revisaría."""
    w0, h0 = t.enteros(t.ifds[0], 256)[0], t.enteros(t.ifds[0], 257)[0]
    if not w0 or not h0:
        raise PuertaCerrada("IFD 0 sin dimensiones")
    previo = 1
    factores = {0: 1}
    for k in range(1, len(t.ifds)):
        w, h = t.enteros(t.ifds[k], 256)[0], t.enteros(t.ifds[k], 257)[0]
        fr = int(round(w0 / float(w))) if w else 0
        if fr < 2 or fr & (fr - 1) or fr <= previo or not h:
            raise PuertaCerrada("IFD %d (%dx%d) no es un nivel de la pirámide de L0 (%dx%d): "
                                "¿etiqueta o macro? no exporto" % (k, w, h, w0, h0))
        if (abs(w - w0 / float(fr)) > max(1.0, 0.01 * w0 / fr)
                or abs(h - h0 / float(fr)) > max(1.0, 0.01 * h0 / fr)):
            raise PuertaCerrada("IFD %d (%dx%d) no es un nivel de la pirámide: no tiene la proporción "
                                "de L0 (%dx%d, factor %d). ¿Etiqueta o macro? no exporto"
                                % (k, w, h, w0, h0, fr))
        previo = fr
        factores[k] = fr
    return factores


def _sof0_dims(t, o, c):
    """(ancho, alto) del SOF0 de la tesela en `o` (`c` bytes), leyendo solo hasta él (los segmentos
    de cabecera; si no caben en 64 KiB, la tesela entera). PuertaCerrada si no hay un SOF0 antes del
    barrido o la cabecera está rota. La forma entera la mira `tesela_canonica`."""
    for n in sorted({min(c, 65536), c}):
        dims = _sof0_en(t.lee(o, n), completo=n == c)
        if dims:
            return dims
    raise PuertaCerrada("tesela sin SOF0 antes del barrido")


def _sof0_en(b, completo):
    """(ancho, alto) del SOF0 de los bytes `b` del principio de una tesela; None si se acaban antes
    y no es la tesela entera (`completo`)."""
    if b[:2] != _SOI:
        raise PuertaCerrada("tesela sin SOI")
    i = 2
    while True:
        if i >= len(b):
            if completo:
                raise PuertaCerrada("tesela con la cabecera rota antes del SOF0")
            return None
        if b[i] != 0xFF:
            raise PuertaCerrada("tesela con la cabecera rota antes del SOF0")
        while i < len(b) and b[i] == 0xFF:
            i += 1
        if i + 8 > len(b):
            if completo:
                raise PuertaCerrada("tesela con la cabecera rota antes del SOF0")
            return None
        m = b[i]
        if m in (0xDA, 0xD9, 0x01) or 0xD0 <= m <= 0xD8:
            raise PuertaCerrada("tesela sin SOF0 antes del barrido")
        ln = struct.unpack(">H", b[i + 1:i + 3])[0]
        if m == 0xC0:
            if ln < 8:
                raise PuertaCerrada("tesela con un SOF0 truncado")
            alto, ancho = struct.unpack(">HH", b[i + 4:i + 8])
            return ancho, alto
        if ln < 2:
            raise PuertaCerrada("tesela con un segmento de longitud no válida")
        i += 1 + ln


def _comprueba_sin_relleno(t, sof0=True):
    """RELLENO DE TESELA (2-oct-26): ningún IFD puede tener relleno. Cada nivel tiene que ser un
    número entero de teselas (ancho y alto múltiplos de 322/323) y, con `sof0`, cada tesela no
    vacía tiene que declarar en su SOF0 exactamente el lado de la tesela (solo la cabecera; en la
    copia y en las decodificaciones lo exige además `tesela_canonica`). Si no, PuertaCerrada SIN
    paso 1-bis: lo que cae fuera no lo enseña la miniatura ni lo mira nadie."""
    for k, ifd in enumerate(t.ifds):
        w, h, tw, th = _dims(t, k)
        if not tw or not th or w % tw or h % th:
            raise PuertaCerrada("IFD %d (%dx%d) no es un número entero de teselas de %dx%d: las del borde "
                                "llevan RELLENO, píxeles más allá del ancho o el alto que nada de lo que "
                                "mira la Puerta ve (el Grundium no escribe relleno): no exporto"
                                % (k, w, h, tw, th))
        if not sof0:
            continue
        for i, (o, c) in enumerate(zip(t.enteros(ifd, 324), t.enteros(ifd, 325))):
            if c and _sof0_dims(t, o, c) != (tw, th):
                raise PuertaCerrada("IFD %d, tesela %d: su JPEG no mide lo que la tesela (%dx%d): lo que no "
                                    "cubre, o lo que guarda y no se decodifica, no lo mira nadie: no "
                                    "exporto" % (k, i, tw, th))


def _forma_297(k, n):
    """El PageNumber CANÓNICO del IFD `k` de `n`: (k, n). Es lo que lleva la copia N1."""
    return k, n


def _comprueba_297(t, factores):
    """Lista blanca del 297 de ORIGEN por IFD, derivada de la pirámide (`factores`): la forma del
    Grundium (log2 del factor, n − 1), medida en las láminas reales, o la canónica (k, n) de la
    copia N1 (modo verificación). Cualquier otra aborta; el valor nunca se copia (lo reescribe
    `copia_tiff_n1`). Los valores ya vienen acotados a <64 por `_comprueba_tags`."""
    n = len(t.ifds)
    for k, ifd in enumerate(t.ifds):
        if 297 not in ifd:
            continue
        v = tuple(t.enteros(ifd, 297))
        grundium = (factores[k].bit_length() - 1, n - 1)
        if v not in (grundium, _forma_297(k, n)):
            raise PuertaCerrada("IFD %d: tag 297 (PageNumber) %s: ni la forma del Grundium %s ni la "
                                "canónica %s: no exporto" % (k, v, grundium, _forma_297(k, n)))


_UM_POR_UNIDAD = {2: 25400.0, 3: 10000.0}     # ResolutionUnit: pulgada, centímetro


def _res_canonica(v):
    """La resolución (px por unidad) con 6 cifras significativas, como fracción: idempotente
    (aplicada a su salida da lo mismo) y sin sitio para texto en numerador ni denominador."""
    return Fraction("%.6g" % v)


def _resoluciones(t, factores):
    """{k: (unidad, Fraction)} con la resolución CANÓNICA de cada IFD que la trae (hallazgo 20
    bis). Lanza si: falta X o Y, X ≠ Y (píxel no cuadrado), el mpp no es plausible (0,01-1.000
    µm/px), o un nivel k>0 no trae ni la de L0 ni la de L0/factor (±2 %). Los niveles k>0 se
    escriben DERIVADOS de L0, así que solo L0 aporta un número libre (y acotado)."""
    def lee(k):
        ifd = t.ifds[k]
        tiene = [tag in ifd for tag in (282, 283)]
        if not any(tiene):
            return None
        if not all(tiene):
            raise PuertaCerrada("IFD %d con solo una de XResolution/YResolution" % k)
        unidad = t.enteros(ifd, 296)[0] if 296 in ifd else 2          # TIFF 6.0: pulgada
        nx, dx = struct.unpack(t.bo + "II", ifd[282][2])
        ny, dy = struct.unpack(t.bo + "II", ifd[283][2])
        vx, vy = nx / float(dx), ny / float(dy)
        if abs(vx - vy) > 1e-6 * max(vx, vy):
            raise PuertaCerrada("IFD %d: XResolution ≠ YResolution (píxel no cuadrado): no exporto" % k)
        if unidad == 1:
            if abs(vx - 1.0) > 1e-6:
                raise PuertaCerrada("IFD %d: resolución sin unidad distinta de 1" % k)
        elif not (0.01 <= _UM_POR_UNIDAD[unidad] / vx <= 1000):
            raise PuertaCerrada("IFD %d: resolución fuera de lo plausible (%.4g µm/px): no exporto"
                                % (k, _UM_POR_UNIDAD[unidad] / vx))
        return unidad, vx

    out = {}
    r0 = lee(0)
    if r0:
        out[0] = (r0[0], _res_canonica(r0[1]))
    for k in range(1, len(t.ifds)):
        rk = lee(k)
        if rk is None:
            continue
        if r0 is None or rk[0] != r0[0]:
            raise PuertaCerrada("IFD %d con resolución y L0 sin ella o en otra unidad: no exporto" % k)
        v0, f = float(out[0][1]), factores[k]
        if abs(rk[1] - v0) <= 0.02 * v0:
            out[k] = (r0[0], out[0][1])
        elif abs(rk[1] - v0 / f) <= 0.02 * v0 / f:
            out[k] = (r0[0], _res_canonica(v0 / f))
        else:
            raise PuertaCerrada("IFD %d: resolución que no es la de L0 ni la de L0/%d: no exporto" % (k, f))
    return out


def _imagen_de_nivel(t, k):
    from PIL import Image
    ifd = t.ifds[k]
    w, h = t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]
    tw, th = t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]
    offs, cnts = t.enteros(ifd, 324), t.enteros(ifd, 325)
    porfila = (w + tw - 1) // tw
    lienzo = Image.new("RGB", (porfila * tw, ((h + th - 1) // th) * th), (255, 255, 255))
    for i, (o, c) in enumerate(zip(offs, cnts)):
        if not c:
            continue
        tesela = Image.open(io.BytesIO(t.lee(o, c))).convert("RGB")
        lienzo.paste(tesela, ((i % porfila) * tw, (i // porfila) * th))
    return lienzo.crop((0, 0, w, h))


# ── Niveles por TESELAS (2-oct-26) ────────────────────────────────────────────────────────────
# El ×4 de las láminas de hueso tiene 478-557 Mpx: entero, Pillow lanza DecompressionBombError en
# `_imagen_de_nivel` (más del doble de Image.MAX_IMAGE_PIXELS) y no cabe con holgura en memoria.
# Su OCR y su coherencia se hacen por REGIONES, decodificando solo las teselas que las tocan y con
# las MISMAS cuentas que sobre el nivel entero (mismos trozos, misma reducción Lanczos, mismas
# ventanas): el resultado es el del nivel entero, sin subir MAX_IMAGE_PIXELS.
LADO_TROZO, SOLAPE_TROZO = 6000, 1200   # trozos del OCR: los de `_trozos`
LADO_FUENTE = 4096                      # lado máximo de nivel que se decodifica de una vez al reducir
FILAS_BANDA = 1024                      # filas del ×8 por banda de la coherencia (múltiplo de 16)


def _dims(t, k):
    ifd = t.ifds[k]
    return t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0], t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]


def _region_de_nivel(t, k, caja):
    """Los píxeles de `_imagen_de_nivel(t, k).crop(caja)`, decodificando solo las teselas que tocan
    `caja` (px del nivel, dentro de él), con el mismo decodificador (Pillow)."""
    from PIL import Image
    w, h, tw, th = _dims(t, k)
    offs, cnts = t.enteros(t.ifds[k], 324), t.enteros(t.ifds[k], 325)
    porfila = (w + tw - 1) // tw
    x0, y0, x1, y1 = caja
    lienzo = Image.new("RGB", (x1 - x0, y1 - y0), (255, 255, 255))
    for fy in range(y0 // th, (y1 - 1) // th + 1):
        for fx in range(x0 // tw, (x1 - 1) // tw + 1):
            i = fy * porfila + fx
            if cnts[i]:
                tesela = Image.open(io.BytesIO(t.lee(offs[i], cnts[i]))).convert("RGB")
                lienzo.paste(tesela, (fx * tw - x0, fy * th - y0))
    return lienzo


def _mosaico_region(t, k, den, caja):
    """Los píxeles de `_mosaico(t, k, den).crop(caja)` (caja en px del nivel a 1/`den`), con
    libjpeg-turbo en estricto, decodificando solo las teselas que la tocan."""
    from PIL import Image
    w, h, tw, th = _dims(t, k)
    offs, cnts = t.enteros(t.ifds[k], 324), t.enteros(t.ifds[k], 325)
    porfila = (w + tw - 1) // tw
    tws, ths = tw // den, th // den                 # tw y th son múltiplos de 16 (_lado_tesela)
    x0, y0, x1, y1 = caja
    lienzo = Image.new("RGB", (x1 - x0, y1 - y0), (255, 255, 255))
    for fy in range(y0 // ths, (y1 - 1) // ths + 1):
        for fx in range(x0 // tws, (x1 - 1) // tws + 1):
            i = fy * porfila + fx
            if cnts[i]:
                sw, sh, rgb = decodifica_jpeg(tesela_canonica(t.lee(offs[i], cnts[i]), tw, th), den)
                lienzo.paste(Image.frombytes("RGB", (sw, sh), rgb), (fx * tws - x0, fy * ths - y0))
    return lienzo


def _tam_escalado(w, h, e):
    """Tamaño de la imagen `w`×`h` reducida a `e`, como lo calcula `lecturas_ocr`."""
    return (w, h) if e == 1.0 else (max(1, int(w * e)), max(1, int(h * e)))


def _trozo_de_nivel(t, k, e, caja):
    """`caja` (px de la imagen reducida a `e`) del nivel `k` reducido con Lanczos como en
    `lecturas_ocr`, construida por bloques de ≤LADO_FUENTE px de nivel. Cada bloque se reduce con
    `resize(box=…)` desde una región con margen ≥ el soporte del filtro (3 px de salida), así que
    cada píxel sale de las mismas muestras y con los mismos pesos que en la imagen entera."""
    from PIL import Image
    if e == 1.0:
        return _region_de_nivel(t, k, caja)
    w, h, _tw, _th = _dims(t, k)
    we, he = _tam_escalado(w, h, e)
    sx, sy = w / float(we), h / float(he)
    x0, y0, x1, y1 = caja
    out = Image.new("RGB", (x1 - x0, y1 - y0))
    px, py = max(1, int(LADO_FUENTE / sx)), max(1, int(LADO_FUENTE / sy))
    for by in range(y0, y1, py):
        for bx in range(x0, x1, px):
            bx1, by1 = min(x1, bx + px), min(y1, by + py)
            fx0, fy0, fx1, fy1 = bx * sx, by * sy, bx1 * sx, by1 * sy
            mx, my = 3 * sx + 2, 3 * sy + 2
            rx0, ry0 = max(0, int(fx0 - mx)), max(0, int(fy0 - my))
            rx1, ry1 = min(w, int(math.ceil(fx1 + mx))), min(h, int(math.ceil(fy1 + my)))
            fuente = _region_de_nivel(t, k, (rx0, ry0, rx1, ry1))
            out.paste(fuente.resize((bx1 - bx, by1 - by), Image.LANCZOS,
                                    box=(fx0 - rx0, fy0 - ry0, fx1 - rx0, fy1 - ry0)), (bx - x0, by - y0))
    return out


def lecturas_ocr_nivel(t, k, escalas):
    """Las pasadas de `lecturas_ocr(_imagen_de_nivel(t, k), escalas)` —mismas escalas, mismos
    trozos (`_cajas_trozos`), mismos psm y en el mismo orden— sin tener nunca el nivel entero en
    memoria: cada trozo se construye desde sus teselas (`_trozo_de_nivel`)."""
    w, h, _tw, _th = _dims(t, k)
    pasadas = []
    for e in escalas:
        for caja in _cajas_trozos(*_tam_escalado(w, h, e)):
            trozo = _trozo_de_nivel(t, k, e, caja)
            for psm in PSMS:
                pasadas.append(_ocr(trozo, psm))
    return pasadas


# ── Texto en el cristal ────────────────────────────────────────────────────────────────────
def _ocr(img, psm):
    """[(palabra, confianza)] de TODO lo que tesseract ve con `--psm psm`, sin umbral (el umbral
    lo aplica quien mira). La imagen va por stdin: en `exporta.sb` no hay más sitio donde escribir
    que N1."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    try:
        p = subprocess.run(["tesseract", "stdin", "stdout", "--psm", str(psm), "tsv"],
                           input=buf.getvalue(), capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PuertaCerrada("no pude pasar el OCR (%s): sin OCR no exporto" % type(e).__name__)
    if p.returncode != 0:
        raise PuertaCerrada("tesseract falló (rc=%d): sin OCR no exporto" % p.returncode)
    out = []
    for ln in p.stdout.decode("utf-8", "replace").splitlines()[1:]:
        c = ln.split("\t")
        if len(c) >= 12 and c[11].strip():
            try:
                conf = float(c[10])
            except ValueError:
                continue
            out.append((c[11].strip(), conf))
    return out


def _cajas_trozos(w, h):
    """Las cajas de los trozos del OCR de una imagen `w`×`h`: la imagen entera si cabe en un trozo;
    si no, trozos de LADO_TROZO px que se solapan SOLAPE_TROZO px, por filas. Toda caja de
    ≤SOLAPE_TROZO px de lado cae entera en algún trozo: un rótulo de hasta 1200 px de alto y ancho
    en la escala que se mira no se parte (el más alto que tesseract lee a una escala es ~400 px:
    los de 300-400 px solo se leen reducidos, ver `lecturas_ocr`; el solape es ≥ 2× eso)."""
    if w <= LADO_TROZO and h <= LADO_TROZO:
        return [(0, 0, w, h)]
    paso = LADO_TROZO - SOLAPE_TROZO
    return [(x, y, min(w, x + LADO_TROZO), min(h, y + LADO_TROZO))
            for y in range(0, max(1, h - SOLAPE_TROZO), paso)
            for x in range(0, max(1, w - SOLAPE_TROZO), paso)]


def _trozos(img):
    cajas = _cajas_trozos(*img.size)
    if len(cajas) == 1:
        yield img
        return
    for caja in cajas:
        yield img.crop(caja)


# PNG a ×4, ×2, ×1 y ×0,5 (2-oct-26). La confianza de tesseract no crece con la escala y depende
# del dibujo exacto del rótulo: «2B9W» de 16 px dibujado con Pillow 12 se lee con 74-90 a ×0,75-1,5
# y con menos de 60 a ×2-6. Medido sobre 70 rótulos sintéticos de una palabra (10-24 px, dibujados
# con Pillow 11 y 12): (2, 0,5) lee 62; (4, 2, 1, 0,5) lee 66, sin falsos positivos nuevos en 12
# texturas limpias y con +3,4 s por PNG de 1024×768. «2B9W» a 10 y 18 px sigue sin leerse: límite
# conocido (nota «Puerta N1: OCR de texto pequeño y versión de Pillow», 1-oct-26).
ESCALAS_PNG = (4.0, 2.0, 1.0, 0.5)
ESCALAS_X8 = (1.0, 0.5, 0.25)
PSMS = (3, 11)
# Iniciales rotuladas («L.Q.T.»): ningún tramo llega a 4 alfanuméricos seguidos.
RE_INICIALES = re.compile(r"^(?:[A-Za-z]\.){2,}[A-Za-z]?\.?$")


def lecturas_ocr(img, escalas=(1.0,)):
    """[[(palabra, confianza)]]: una lista por pasada (escala × trozo × psm).

    Multiescala (desviación del plan, medida en el test sintético, 1 y 2-oct-26): un rótulo de
    300-400 px de alto sobre fondo liso solo se lee REDUCIDO (a ×1 da confianza 31; a ×0,5, 92),
    y uno de 10 px solo AMPLIADO (×2 o ×4; a ×1 nada). La confianza no crece con la escala: un
    rótulo de 14-16 px se lee a ×1 y no a ×2. Por eso el PNG se mira a ×4, ×2, ×1 y ×0,5
    (ESCALAS_PNG) y el nivel ×8 a ×1, ×0,5 y ×0,25. Y con psm 3 y psm 11: palabras sueltas sobre
    textura que psm 3 no segmenta, psm 11 sí. Los tres hechos están fijados en
    test_ocr_multiescala_y_psm11 (hallazgo 27)."""
    from PIL import Image
    img = img.convert("RGB")
    pasadas = []
    for e in escalas:
        im = img if e == 1.0 else img.resize((max(1, int(img.width * e)), max(1, int(img.height * e))),
                                              Image.LANCZOS)
        for trozo in _trozos(im):
            for psm in PSMS:
                pasadas.append(_ocr(trozo, psm))
    return pasadas


def _sospechosas(pasadas):
    return [w for p in pasadas for w, c in p if c >= CONF_OCR and RE_PALABRA_OCR.search(w)]


def texto_en_imagen(img, escalas=(1.0,)):
    """Palabras sospechosas: ≥4 alfanuméricos seguidos, confianza ≥60, en cualquier pasada."""
    return _sospechosas(lecturas_ocr(img, escalas))


def _motivos_texto_ocr(pasadas):
    """Lo que el OCR lee, por la Puerta de N1 (hallazgo 17): por pasada, las palabras de
    confianza ≥60 juntas en una cadena, por `puerta.motivos`; fechas y accesiones también en lo
    leído con MENOS confianza (un «30.4.26» no llega a 4 alfanuméricos seguidos); e iniciales.
    Devuelve las capas que saltaron, nunca el texto."""
    capas = set()
    for p in pasadas:
        altas = " ".join(w for w, c in p if c >= CONF_OCR)
        if altas.strip():
            capas.update(capa for capa, _pos in puerta.motivos(altas))
        if any(RE_INICIALES.match(w) for w, c in p if c >= CONF_OCR):
            capas.add("iniciales")
        todas = " ".join(w for w, _c in p)
        if todas.strip():
            capas.update(puerta.motivos_de_forma(todas))
    return sorted(capas)


def trazos_de_rotulador(img):
    """Nº de píxeles con color de rotulador: saturados y fuera de los tonos de H&E/DAB
    (verde-cian, azul muy vivo), negro neutro y ROJO muy saturado. Devuelve (n_px, umbral).

    Rojo (2-oct-26; commit bd0c129 y nota «Puerta N1: OCR de texto pequeño y versión de Pillow»):
    tono ≥245 o ≤10 (HSV de PIL), S≥200, V≥60. En las 15 láminas reales (solo recuento) hay
    píxeles en esa ventana (hasta 1944 en un ×8 de IHQ; origen sin mirar: eosina, hematíes,
    cromógeno o rotulador), pero ninguna de las 45 imágenes llega al umbral con trazos + rojo; la
    más cerca, el campo ×8 de P-RE, al 71 % (372 de 524). Un rotulador (200,30,40) tiene S 217:
    con S≥220 se escaparía. En sintético el rojo supera el umbral 90 de 90 veces en el campo ×8
    (150-500 px); en miniatura, 29/30 a 48 px, 10/30 a 24 px y 0/30 a 12 px. Límite que queda: un
    trazo rojo de menos de ~24 px en una miniatura."""
    from PIL import ImageChops
    h, s, v = img.convert("RGB").convert("HSV").split()

    def banda(canal, lo, hi):
        return canal.point(lambda x: 255 if lo <= x < hi else 0)

    verde = ImageChops.multiply(ImageChops.multiply(banda(h, 42, 142), banda(s, 128, 256)),
                                banda(v, 50, 256))
    azul = ImageChops.multiply(ImageChops.multiply(banda(h, 142, 184), banda(s, 192, 256)),
                               banda(v, 76, 256))
    negro = ImageChops.multiply(banda(v, 0, 38), banda(s, 0, 64))
    tono_rojo = ImageChops.lighter(banda(h, 245, 256), banda(h, 0, 11))
    rojo = ImageChops.multiply(ImageChops.multiply(tono_rojo, banda(s, 200, 256)), banda(v, 60, 256))
    mascara = ImageChops.lighter(ImageChops.lighter(ImageChops.lighter(verde, azul), negro), rojo)
    n = mascara.histogram()[255]
    umbral = max(50, int(0.0005 * img.width * img.height))
    return n, umbral


def _motivos_ocr(pasadas, con_puerta):
    """Los motivos de texto de `revisar_cristal` a partir de sus pasadas de OCR."""
    motivos = []
    hits = _sospechosas(pasadas)
    if hits:
        motivos.append("OCR: %d palabra(s) de ≥4 alfanuméricos" % len(hits))
    if con_puerta:
        capas = _motivos_texto_ocr(pasadas)
        if capas:
            motivos.append("OCR: lo leído no pasa la Puerta de N1 (%s)" % ", ".join(capas))
    return motivos


def revisar_cristal(img, escalas=ESCALAS_X8, con_puerta=False, trazos=True):
    """[motivos] si hay texto o trazos; [] si está limpio. No devuelve el texto leído.

    `con_puerta=True` (exporta y la re-validación del envío): lo leído pasa además por la Puerta
    de N1, que exige el diccionario (fail-closed). Por defecto no, para no cambiar la revisión
    local de la ingesta (`laminillas_ingesta.cristal`, en `analisis.sb`, donde no sé si los
    overlays se leen). `trazos=False`: solo OCR (el ×4 entero; sus trazos ya los ve el ×8)."""
    if con_puerta:
        puerta.exigir_diccionario()      # aunque el OCR no lea nada (residuo del hallazgo 15)
    motivos = _motivos_ocr(lecturas_ocr(img, escalas), con_puerta)
    if trazos:
        n, umbral = trazos_de_rotulador(img)
        if n >= umbral:
            motivos.append("trazos de rotulador: %d px (umbral %d)" % (n, umbral))
    return motivos


# ── Coherencia de la pirámide (tercera pasada, 2-oct-26) ──────────────────────────────────────
# El OCR mira el ×8 y lo menor; un ×4 rotulado con un ×8 limpio salía. Ahora el ×4 pasa ENTERO por
# el OCR, y cada nivel mayor que el ×8 (L0, ×2, ×4), reducido al tamaño del ×8 con la escala DCT
# de libjpeg (media de bloques), tiene que PARECERSE al ×8: media por ventana de 16×16 px del ×8,
# por canal, a menos de UMBRAL_COHERENCIA niveles (0-255). Una pirámide de verdad sale de L0 y se
# parece a sí misma (ruido JPEG y filtro de reducción: pocos niveles); un rótulo pegado en un solo
# nivel se aparta SI mueve la media de una ventana (16×16 px del ×8, 128×128 de L0) más del
# umbral: los pequeños no (hasta 60-100 px de L0 o 14-24 del ×4 según caigan dentro de una fila
# de ventanas o a caballo de dos: `test_limite_declarado_rotulo_de_un_solo_nivel`). Lo que se
# aparta es un motivo del cristal, como el OCR: paso 1-bis.
#
# RELLENO (2-oct-26, medido en las 15 láminas reales con `qc coherencia`, solo números): la
# coherencia saltaba en las 15 (diferencia 29-245) y TODAS las ventanas que pasaban el umbral caían
# en la zona no escaneada, con L0 y ×4 en blanco 255 exacto y el ×8 con otro color (vidrio
# ~205-238; en P-HE, ~107-138). exporta_n1 no tiene el polígono de la zona escaneada (está en el
# manifiesto de SESION), así que el relleno se reconoce en el nivel FINO: en una ventana con ≥50 %
# (FRACCION_RELLENO) de píxeles en (255, 255, 255) exacto, los píxeles casi blancos (los tres
# canales ≥ CLARO_RELLENO: el relleno y el halo JPEG de su borde) dejan de sumar en la media de los
# DOS niveles; los demás suman como siempre (Σ(fino − ×8) sobre ellos, entre 256, umbral 24). Es la
# cuenta de antes menos el término «blanco del relleno frente al ×8», que era lo que saltaba. Una
# mancha o un rótulo oscuro pintado en el relleno de L0 o del ×4 salta igual o más que antes (antes
# ese término, de signo contrario, lo tapaba: rótulo de L0 sobre relleno, 28 → 45 en sintético).
# Las ventanas con <50 % de blanco puro (tejido y vidrio escaneado) se comparan enteras, como antes.
# Medido en sintético (bordes de relleno alineados o no con la ventana, tejido o vidrio hasta el
# borde, relleno del ×8 a 229, 214 o 120): máximo 23 (una ventana de borde con <50 % de blanco,
# comparada entera como antes), frente a 25-135 de antes; salta solo si el ×8
# cambia a un color lejos del vidrio justo en el borde del relleno (51), que en las 15 no se ha
# visto (las ventanas que saltaban eran blanco 255 entero). LÍMITE DECLARADO:
# `test_limite_declarado_blanco_en_nivel_fino`.
VENTANA_COHERENCIA = 16
UMBRAL_COHERENCIA = 24
FRACCION_RELLENO = 0.5
CLARO_RELLENO = 250        # en una ventana de relleno no suma el píxel con los tres canales ≥ 250


def _mosaico(t, k, den):
    """El nivel `k` decodificado a 1/`den` con libjpeg-turbo (estricto), como imagen PIL."""
    from PIL import Image
    ifd = t.ifds[k]
    w, h = t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]
    tw, th = t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]
    porfila = (w + tw - 1) // tw
    lienzo = Image.new("RGB", (porfila * tw // den, ((h + th - 1) // th) * th // den), (255, 255, 255))
    for i, (o, c) in enumerate(zip(t.enteros(ifd, 324), t.enteros(ifd, 325))):
        if not c:
            continue
        sw, sh, rgb = decodifica_jpeg(tesela_canonica(t.lee(o, c), tw, th), den)
        lienzo.paste(Image.frombytes("RGB", (sw, sh), rgb), ((i % porfila) * tw // den, (i // porfila) * th // den))
    return lienzo.crop((0, 0, -(-w // den), -(-h // den)))


def _difs_ventanas(a, b, ventana=VENTANA_COHERENCIA, alto=None):
    """[diferencia de cada ventana] (0-255, cualquier canal; entero, ceil), por filas, entre `a`
    (nivel fino reducido) y `b` (×8), del mismo tamaño, por ventana de `ventana`×`alto` px (alto =
    ventana si no se da; el tamaño de `a`, múltiplo de los dos): la de sus medias, sin los píxeles
    casi blancos de `a` si la ventana es relleno (ver RELLENO arriba). Las medias y la fracción de
    relleno van entre el área REAL de la ventana (las parciales del borde: ver `_ventanas`)."""
    from PIL import Image, ImageChops
    a, b = a.convert("RGB"), b.convert("RGB")
    nw, nh = a.width // ventana, a.height // (alto or ventana)
    enteras = ImageChops.difference(a.resize((nw, nh), Image.BOX), b.resize((nw, nh), Image.BOX))
    r, g, z = enteras.split()
    dif = list(ImageChops.lighter(ImageChops.lighter(r, g), z).getdata())
    ra, ga, za = a.split()
    minimo = ImageChops.darker(ImageChops.darker(ra, ga), za)
    if minimo.getextrema()[1] < 255:                 # ni un píxel en blanco puro: como siempre
        return dif
    blanco = list(minimo.point(lambda x: 255 if x == 255 else 0).convert("F")
                  .resize((nw, nh), Image.BOX).getdata())                  # 255 × fracción de 255 exacto
    color = minimo.point(lambda x: 255 if x < CLARO_RELLENO else 0)
    medias = None
    out = []
    for i, d in enumerate(dif):
        if blanco[i] / 255.0 < FRACCION_RELLENO:
            out.append(d)
            continue
        if medias is None:      # Σ(canal × máscara)/área de la ventana, en coma flotante, de `a` y de `b`
            medias = [list(ImageChops.multiply(c, color).convert("F").resize((nw, nh), Image.BOX).getdata())
                      for c in a.split() + b.split()]
        out.append(int(math.ceil(round(max(abs(medias[c][i] - medias[c + 3][i]) for c in range(3)), 3))))
    return out


def _dif_ventanas(a, b, ventana=VENTANA_COHERENCIA, alto=None):
    """Máxima diferencia de `_difs_ventanas` (la de la ventana que más se aparta)."""
    return max(_difs_ventanas(a, b, ventana, alto))


def _ventanas(w, h, ventana=VENTANA_COHERENCIA):
    """[(caja, ancho, alto)]: bloques que parten TODA la imagen `w`×`h` en ventanas de
    ventana×ventana y, en el borde derecho e inferior y en su esquina, las ventanas PARCIALES que
    sobran (w % ventana columnas, h % ventana filas). Hasta el 2-oct-26 (verificador independiente)
    la coherencia redondeaba hacia abajo a múltiplos de la ventana y esa franja (hasta 15 px del
    ×8, ~120 px de L0) no se comparaba: un rótulo pintado solo en L0 ahí salía limpio."""
    nw, nh = w // ventana, h // ventana
    out = []
    for x0, x1, vx in ((0, nw * ventana, ventana), (nw * ventana, w, w % ventana)):
        for y0, y1, vy in ((0, nh * ventana, ventana), (nh * ventana, h, h % ventana)):
            if x1 > x0 and y1 > y0:
                out.append(((x0, y0, x1, y1), vx, vy))
    return out


def _dif_toda_detalle(a, b, ventana=VENTANA_COHERENCIA, umbral=None):
    """(máximo, ventanas) de `_difs_ventanas` sobre TODA `a`/`b` (del mismo tamaño): las ventanas
    enteras y las parciales del borde (`_ventanas`), cada una con su media entre su área real.
    `ventanas` = [(x0, y0, x1, y1, diferencia)] de las que pasan `umbral` (px de `a`); con
    `umbral` None, vacía."""
    peor, malas = 0, []
    for (x0, y0, x1, y1), vx, vy in _ventanas(a.width, a.height, ventana):
        nw = (x1 - x0) // vx
        for i, d in enumerate(_difs_ventanas(a.crop((x0, y0, x1, y1)), b.crop((x0, y0, x1, y1)), vx, vy)):
            peor = max(peor, d)
            if umbral is not None and d > umbral:
                X, Y = x0 + (i % nw) * vx, y0 + (i // nw) * vy
                malas.append((X, Y, X + vx, Y + vy, d))
    return peor, malas


def _dif_toda(a, b, ventana=VENTANA_COHERENCIA):
    """El máximo de `_dif_toda_detalle`."""
    return _dif_toda_detalle(a, b, ventana)[0]


def incoherencia(a, b, ventana=VENTANA_COHERENCIA):
    """Máxima diferencia (0-255, cualquier canal) entre las medias por ventana de `a` (nivel fino
    reducido al tamaño del ×8) y `b` (×8), sobre TODO lo que tienen en común —ventanas parciales
    del borde incluidas—, con el relleno de `a` aparte (`_dif_ventanas`)."""
    w, h = min(a.width, b.width), min(a.height, b.height)
    if not w or not h:
        return 0
    caja = (0, 0, w, h)
    return _dif_toda(a.crop(caja), b.crop(caja), ventana)


def _zona_coherencia(k, w, h, den, w8, h8):
    """(ancho, alto) en px del ×8 de lo que se compara del nivel `k` (`w`×`h`, a 1/`den` del ×8):
    sus píxeles reducidos ENTEROS (cada uno, den×den px del nivel; el último parcial, si lo hay,
    mezcla el relleno de la tesela) que el ×8 también tiene. Lo que queda sin comparar es el resto
    de la división, < `den` px del nivel por borde (menos de 8 px de L0, 2 del ×4: dentro del límite
    del microtexto). Si queda más —el ×8 más corto que el nivel reducido, cosa que la tolerancia
    del ±1 % de `_comprueba_piramide` deja pasar—, esa franja no la vería nadie: PuertaCerrada."""
    W, H = min(w // den, w8), min(h // den, h8)
    if not W or not H or w - W * den >= den or h - H * den >= den:
        raise PuertaCerrada("IFD %d (%dx%d): el ×8 (%dx%d) no cubre el nivel reducido a 1/%d; quedan "
                            "%d columnas y %d filas del nivel sin comparar: no puedo revisar el "
                            "cristal entero, no exporto" % (k, w, h, w8, h8, den, w - W * den, h - H * den))
    return W, H


def _coherencia_nivel_detalle(t, k, den, img8):
    """(diferencia, ventanas): la coherencia del nivel `k` con el ×8 (`img8`) sobre todo
    `_zona_coherencia`, por bandas de FILAS_BANDA filas del ×8 —las mismas ventanas que
    `incoherencia` sobre esa zona (las bandas empiezan en múltiplos de la ventana; las parciales del
    borde inferior caen en la última) y el mismo resultado, sin el nivel reducido entero en
    memoria— y las ventanas que pasan UMBRAL_COHERENCIA, [(x0, y0, x1, y1, diferencia)] en px del
    ×8: lo que la hoja del paso 1-bis enseña (2-oct-26)."""
    w, h, _tw, _th = _dims(t, k)
    W, H = _zona_coherencia(k, w, h, den, img8.width, img8.height)
    v = VENTANA_COHERENCIA
    banda = v * max(1, FILAS_BANDA // v)
    peor, malas = 0, []
    for y in range(0, H, banda):
        caja = (0, y, W, min(H, y + banda))
        p, m = _dif_toda_detalle(_mosaico_region(t, k, den, caja), img8.crop(caja), v, UMBRAL_COHERENCIA)
        peor = max(peor, p)
        malas += [(x0, y0 + y, x1, y1 + y, d) for x0, y0, x1, y1, d in m]
    return peor, malas


def _coherencia_nivel(t, k, den, img8):
    """La diferencia de `_coherencia_nivel_detalle`."""
    return _coherencia_nivel_detalle(t, k, den, img8)[0]


def _visto_en_tty(opaco, sha256, motivos):
    """Paso 1-bis: {{TITULAR}} miró la miniatura y lo dice tecleando VISTO-N1 en su terminal. Lo pide y
    lo sella `borde.revisar_cristal_en_tty` (2-oct-26), la única vía que sella `n1_revision_humana`:
    rechaza si lo lanza Claude Code, si stdin no es el terminal de control o si no hay TTY, y cada
    rechazo deja `n1_revision_rechazada` en la cadena. Aquí solo se traduce a PuertaCerrada."""
    try:
        return puerta.borde.revisar_cristal_en_tty(opaco, sha256, motivos)
    except RuntimeError as e:
        raise PuertaCerrada(str(e))


# ── TIFF: escritura ────────────────────────────────────────────────────────────────────────
def copia_tiff_n1(t):
    """(bytes del TIFF N1, informe). Lanza PuertaCerrada ante cualquier cosa fuera del formato.

    Es idempotente: aplicada a su propia salida devuelve los mismos bytes. `verificar_tiff_n1` lo
    usa como modo verificación (la copia de un TIFF N1 legítimo es él mismo)."""
    bo, big = t.bo, t.big
    salida = io.BytesIO()
    cab = 16 if big else 8
    salida.write(b"\x00" * cab)
    ifds_nuevos, quitados, niveles = [], set(), []
    if not t.ifds:
        raise PuertaCerrada("TIFF sin IFDs")
    for k, ifd in enumerate(t.ifds):             # forma de cada IFD antes de leer ningún valor
        for tag in (322, 323, 324, 325):
            if tag not in ifd:
                raise PuertaCerrada("IFD %d no teselado (¿etiqueta o macro?): no exporto" % k)
        _comprueba_tags(t, k, ifd)
    _comprueba_sin_relleno(t, sof0=False)        # el SOF0 de cada tesela, en tesela_canonica
    factores = _comprueba_piramide(t)
    _comprueba_297(t, factores)
    resoluciones = _resoluciones(t, factores)
    w0 = None
    for k, ifd in enumerate(t.ifds):
        if t.enteros(ifd, 259)[0] != 7:
            raise PuertaCerrada("IFD %d no es JPEG: no se puede revisar tesela a tesela" % k)
        if 284 in ifd and t.enteros(ifd, 284)[0] != 1:
            raise PuertaCerrada("IFD %d en planos separados" % k)
        w, h = t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]
        w0 = w0 or w
        niveles.append({"ancho": w, "alto": h, "factor": round(w0 / float(w), 3)})
        offs, cnts = t.enteros(ifd, 324), t.enteros(ifd, 325)
        tw, th = t.enteros(ifd, 322)[0], t.enteros(ifd, 323)[0]
        if not tw or not th:
            raise PuertaCerrada("IFD %d con tesela de lado 0" % k)
        if len(offs) != len(cnts) or len(offs) != ((w + tw - 1) // tw) * ((h + th - 1) // th):
            raise PuertaCerrada("IFD %d: offsets y tamaños no casan con el nº de teselas" % k)
        if 262 not in ifd:                       # sin él, cada lector supone una cosa: FOTOMÉTRICA
            raise PuertaCerrada("IFD %d sin fotométrica (262): no sé qué colores verá el lector" % k)
        fot = t.enteros(ifd, 262)[0]
        nuevos_offs, nuevos_cnts = [], []
        for o, c in zip(offs, cnts):
            if not c:
                nuevos_offs.append(0)
                nuevos_cnts.append(0)
                continue
            b = tesela_canonica(t.lee(o, c), tw, th, fot)    # forma canónica (3.ª pasada) y 262
            decodifica_jpeg(b, 8)                         # TODAS las teselas: limpia o aborta
            nuevos_offs.append(salida.tell())
            nuevos_cnts.append(len(b))
            salida.write(b)
            if salida.tell() % 2:
                salida.write(b"\x00")
        ent = {}
        for tag, (tipo, cnt, val) in ifd.items():      # tipo/count/valores: _comprueba_tags
            if tag not in TAGS_FORMATO:
                quitados.add(tag)
                continue
            ent[tag] = (tipo, cnt, val)
        if 270 in ent:
            ent[270] = (2, len(CONSTANTE_270), CONSTANTE_270)
        if 297 in ent:                                 # PageNumber: el canónico, no el de origen
            ent[297] = (3, 2, struct.pack(bo + "HH", *_forma_297(k, len(t.ifds))))
        if k in resoluciones:                          # 282/283: el valor canónico, no sus bytes
            fr = resoluciones[k][1]
            if fr.numerator >= 2 ** 32 or fr.denominator >= 2 ** 32:
                raise PuertaCerrada("IFD %d: resolución que no cabe en un RATIONAL" % k)
            crudo = struct.pack(bo + "II", fr.numerator, fr.denominator)
            ent[282] = (5, 1, crudo)
            ent[283] = (5, 1, crudo)
        tipo_off = 16 if big else 4
        ent[324] = (tipo_off, len(nuevos_offs), struct.pack(bo + _FMT[tipo_off] * len(nuevos_offs), *nuevos_offs))
        ent[325] = (tipo_off, len(nuevos_cnts), struct.pack(bo + _FMT[tipo_off] * len(nuevos_cnts),
                                                            *nuevos_cnts))
        ifds_nuevos.append(ent)
    # IFDs al final, encadenados
    inline = 8 if big else 4
    primero = None
    previo_sig = None
    for ent in ifds_nuevos:
        # valores largos primero
        fuera = {}
        for tag, (tipo, cnt, val) in sorted(ent.items()):
            if len(val) > inline:
                fuera[tag] = salida.tell()
                salida.write(val)
                if salida.tell() % 2:
                    salida.write(b"\x00")
        pos = salida.tell()
        if previo_sig is not None:
            salida.seek(previo_sig)
            salida.write(struct.pack(bo + ("Q" if big else "I"), pos))
            salida.seek(pos)
        primero = pos if primero is None else primero
        n = len(ent)
        salida.write(struct.pack(bo + ("Q" if big else "H"), n))
        for tag, (tipo, cnt, val) in sorted(ent.items()):
            salida.write(struct.pack(bo + "HH", tag, tipo))
            salida.write(struct.pack(bo + ("Q" if big else "I"), cnt))
            if tag in fuera:
                salida.write(struct.pack(bo + ("Q" if big else "I"), fuera[tag]))
            else:
                salida.write(val + b"\x00" * (inline - len(val)))
        previo_sig = salida.tell()
        salida.write(b"\x00" * (8 if big else 4))
    if not big and salida.tell() >= 2 ** 32:
        raise PuertaCerrada("la copia no cabe en TIFF clásico")
    salida.seek(0)
    if big:
        salida.write((b"II" if bo == "<" else b"MM") + struct.pack(bo + "HHHQ", 43, 8, 0, primero))
    else:
        salida.write((b"II" if bo == "<" else b"MM") + struct.pack(bo + "HI", 42, primero))
    mpp = None
    if 0 in resoluciones and resoluciones[0][0] in _UM_POR_UNIDAD:   # acotado en _resoluciones
        unidad, fr = resoluciones[0]
        mpp = round(_UM_POR_UNIDAD[unidad] / float(fr), 5)
    return salida.getvalue(), {"niveles": niveles, "tags_quitados": sorted(quitados), "mpp": mpp}


# ── PNG ────────────────────────────────────────────────────────────────────────────────────
def _chunks_png(b):
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        raise PuertaCerrada("no es PNG")
    i, out = 8, []
    while i < len(b):
        ln = struct.unpack(">I", b[i:i + 4])[0]
        out.append(b[i + 4:i + 8].decode("latin-1"))
        i += 12 + ln
    return out


def png_n1(ruta):
    """(bytes PNG sin metadatos, imagen). Lanza si no es PNG o pasa de 1024 px de lado largo."""
    from PIL import Image
    with open(ruta, "rb") as fh:
        crudo = fh.read()
    _chunks_png(crudo)
    img = Image.open(io.BytesIO(crudo))
    if img.format != "PNG":
        raise PuertaCerrada("no es PNG")
    if max(img.size) > LADO_MAX_PNG:
        raise PuertaCerrada("PNG de %dx%d: el lado largo pasa de %d px" % (img.width, img.height, LADO_MAX_PNG))
    b, limpia = png_canonico(img)
    estructura_png_n1(b)            # lo que se escribe es exactamente lo que el envío acepta
    return b, limpia


def png_canonico(img):
    """(bytes, imagen): los píxeles RGB de `img` recodificados como los escribe `png_n1` (RGB de 8
    bits, zlib con `optimize`, sin metadatos). Es también lo que SALE de un PNG N1 (tercera pasada):
    los bytes del fichero no, que pueden llevar el texto en claro en bloques zlib almacenados."""
    from PIL import Image
    limpia = Image.new("RGB", img.size)
    limpia.paste(img.convert("RGB"))
    buf = io.BytesIO()
    limpia.save(buf, "PNG", optimize=True)
    return buf.getvalue(), limpia


# ── Verificación en el envío (vision_n1, nube_n1): el contenido, no el manifiesto ─────────────
_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"


def _chunks_png_estricto(b):
    """[(tipo, datos)] de un PNG con CRC correcto, IHDR primero, IEND al final y NADA detrás."""
    if b[:8] != _FIRMA_PNG:
        raise PuertaCerrada("no es PNG")
    i, out = 8, []
    while True:
        if i + 12 > len(b):
            raise PuertaCerrada("PNG truncado")
        ln = struct.unpack(">I", b[i:i + 4])[0]
        tipo = b[i + 4:i + 8]
        if i + 12 + ln > len(b):
            raise PuertaCerrada("PNG truncado")
        datos = b[i + 8:i + 8 + ln]
        if zlib.crc32(tipo + datos) & 0xFFFFFFFF != struct.unpack(">I", b[i + 8 + ln:i + 12 + ln])[0]:
            raise PuertaCerrada("PNG con un chunk de CRC roto")
        out.append((tipo.decode("latin-1"), datos))
        i += 12 + ln
        if tipo == b"IEND":
            break
    if i != len(b):
        raise PuertaCerrada("PNG con bytes tras IEND")
    if out[0][0] != "IHDR" or len(out[0][1]) != 13 or [t for t, _ in out].count("IHDR") != 1:
        raise PuertaCerrada("PNG sin un IHDR único al principio")
    return out


def dimensiones_png(datos):
    """(ancho, alto) del IHDR de un PNG que pasa `_chunks_png_estricto`."""
    return struct.unpack(">II", _chunks_png_estricto(datos)[0][1][:8])


def estructura_png_n1(datos):
    """(ancho, alto) si `datos` tiene EXACTAMENTE la forma que escribe `png_n1`; si no, lanza
    (hallazgo 9 bis, 2-oct-26). Un PNG colado podía llevar texto en sitios que ningún píxel usa:
    una paleta (PLTE) de un PNG indexado, bytes tras el fin del flujo zlib dentro de los IDAT, o
    un canal alfa (el OCR mira RGB). Así que:
      - IHDR: RGB (tipo de color 2), 8 bits, sin entrelazar —ni paleta, ni gris, ni alfa—;
      - chunks: IHDR, uno o más IDAT seguidos, IEND; nada más (ni PLTE, ni tEXt…), CRC correctos
        y nada tras IEND;
      - el flujo zlib de los IDAT termina justo donde acaban los píxeles (alto × (1 + 3·ancho)
        bytes, cada fila con un filtro 0-4): ni un byte de más dentro ni detrás.
    `png_n1` la pasa a lo que escribe, así que exporta_n1 nunca deja en N1 un tipo 3."""
    chunks = _chunks_png_estricto(datos)
    w, h, prof, color, comp, filtro, entrel = struct.unpack(">IIBBBBB", chunks[0][1])
    tipos = [t for t, _ in chunks]
    raros = sorted(set(tipos) - {"IHDR", "IDAT", "IEND"})
    if raros:
        raise PuertaCerrada("PNG N1 con chunk(s) %s: solo salen IHDR/IDAT/IEND" % ",".join(raros))
    if (prof, color, comp, filtro, entrel) != (8, 2, 0, 0, 0):
        raise PuertaCerrada("PNG N1 que no es RGB de 8 bits sin entrelazar (profundidad %d, tipo de "
                            "color %d, entrelazado %d): solo sale lo que escribe exporta_n1"
                            % (prof, color, entrel))
    n_idat = len(tipos) - 2
    if n_idat < 1 or tipos != ["IHDR"] + ["IDAT"] * n_idat + ["IEND"]:
        raise PuertaCerrada("PNG N1 con los chunks fuera de orden: no sale")
    if not w or not h:
        raise PuertaCerrada("PNG N1 sin dimensiones")
    if max(w, h) > LADO_MAX_PNG:
        raise PuertaCerrada("PNG N1 de %dx%d: el lado largo pasa de %d px" % (w, h, LADO_MAX_PNG))
    fila = 1 + 3 * w
    esperado = h * fila
    d = zlib.decompressobj()
    try:
        crudo = d.decompress(b"".join(x for t, x in chunks if t == "IDAT"), esperado + 1)
    except zlib.error:
        raise PuertaCerrada("PNG N1 con el flujo zlib roto: no sale")
    if len(crudo) != esperado or not d.eof or d.unused_data or d.unconsumed_tail:
        raise PuertaCerrada("PNG N1 cuyo flujo IDAT no termina justo en los píxeles (bytes de más "
                            "dentro o detrás del zlib): no sale")
    if any(crudo[i] > 4 for i in range(0, esperado, fila)):
        raise PuertaCerrada("PNG N1 con un filtro de fila no válido: no sale")
    return w, h


def verificar_png_n1(datos):
    """Lanza si `datos` no es un PNG que exporta_n1 dejaría salir: la forma exacta de `png_n1`
    (`estructura_png_n1`), ≤1024 px, y sin texto ni trazos (OCR + Puerta + trazos, de nuevo).
    Devuelve la imagen."""
    from PIL import Image
    estructura_png_n1(datos)
    img = Image.open(io.BytesIO(datos))
    img.load()
    motivos = revisar_cristal(img.convert("RGB"), ESCALAS_PNG, con_puerta=True)
    if motivos:
        raise PuertaCerrada("texto en la imagen (%s): no sale" % "; ".join(motivos))
    return img


def verificar_tiff_n1(ruta, sha256_esperado):
    """`copia_tiff_n1` en modo verificación: la copia canónica que esta tool sacaría de `ruta`
    tiene que ser `ruta` misma (su sha256 = el del manifiesto), sin tags quitados y con nivel ×8.
    Así un 270 distinto de la constante, un tag de más, una resolución no canónica, una tesela con
    APPn o bytes escondidos entre teselas no salen. Y el cristal, otra vez (hallazgo 9 bis: el
    nombre rasterizado en los píxeles de un TIFF colado): OCR + Puerta + trazos del ×8 y los
    niveles menores; si saltan, solo sale con un `n1_revision_humana` de la cadena atado a ESTE
    sha256 (paso 1-bis de exporta_tiff). Devuelve los bytes verificados (los que se envían)."""
    t = Tiff(ruta)
    try:
        copia, inf = copia_tiff_n1(t)
        if inf["tags_quitados"]:
            raise PuertaCerrada("TIFF N1 con tags fuera de la lista blanca (%s): no sale"
                                % ",".join(str(x) for x in inf["tags_quitados"]))
        sha = hashlib.sha256(copia).hexdigest()
        if sha != sha256_esperado:
            raise PuertaCerrada("TIFF N1 que no es su copia canónica (bytes de más o fuera de sitio, "
                                "un 270 distinto de la constante, una resolución no canónica): no sale")
        motivos = _motivos_cristal_tiff(t)
    finally:
        t.f.close()
    if motivos and not puerta.revision_humana(sha):
        raise PuertaCerrada("texto en el cristal del TIFF N1 (%s) sin revisión humana (paso 1-bis) "
                            "atada a este sha256: no sale" % "; ".join(motivos))
    return copia


def nombre_n1(nombre):
    """`nombre` si tiene la forma de un nombre que escribe esta tool (RE_NOMBRE_TIFF, RE_NOMBRE_PNG
    o RE_NOMBRE_TEXTO: lista blanca) y su parte libre pasa la Puerta; si no, PuertaCerrada (tercera
    pasada: «24B0001043.png» o «Leocadia-Quintanar__x8.png», colados en N1, salían y ese nombre era
    el del objeto en la nube). El mensaje no repite el nombre."""
    m = RE_NOMBRE_TIFF.match(nombre or "") or RE_NOMBRE_PNG.match(nombre or "")
    if m:
        raiz = m.group(1)
    elif RE_NOMBRE_TEXTO.match(nombre or "") and nombre != "manifiesto.json":
        raiz = nombre.rsplit(".", 1)[0]
    else:
        raise PuertaCerrada("nombre N1 que no es de los que escribe exporta_n1 (P-HE.n1.tif, "
                            "P-KI67__x8__mpp2.0048.png, P-KI67-t.csv): no sale")
    puerta.revisar_texto(raiz, "nombre N1")
    return nombre


def revalidar_n1(ruta, sha256_esperado):
    """Los bytes de un fichero N1 que pueden salir, tras volver a validar su NOMBRE (`nombre_n1`)
    y su CONTENIDO según su tipo (el manifiesto no es prueba de procedencia: hallazgo 9). TIFF:
    modo verificación; PNG: `verificar_png_n1`, y lo que sale es su recodificación canónica
    (`png_canonico`), no los bytes del fichero; texto: la Puerta sobre los bytes. Lanza
    PuertaCerrada si no."""
    puerta.exigir_diccionario()
    nombre = os.path.basename(ruta)
    nombre_n1(nombre)
    ext = os.path.splitext(nombre)[1].lower()
    if ext in (".tif", ".tiff"):
        return verificar_tiff_n1(ruta, sha256_esperado)
    with open(ruta, "rb") as fh:
        datos = fh.read()
    if hashlib.sha256(datos).hexdigest() != sha256_esperado:
        raise PuertaCerrada("sha256 distinto del manifiesto: %s" % nombre)
    if ext == ".png":
        img = verificar_png_n1(datos)
        canon, limpia = png_canonico(img)
        estructura_png_n1(canon)
        if limpia.tobytes() != img.convert("RGB").tobytes():
            raise PuertaCerrada("PNG N1 cuya recodificación cambia los píxeles: no sale")
        return canon
    elif ext in puerta.EXT_TEXTO:
        puerta.revisar_bytes_texto(nombre, datos)
    else:
        raise PuertaCerrada("%s: ese tipo no sale de N1" % nombre)
    return datos


# ── Escritura en N1 ────────────────────────────────────────────────────────────────────────
def _escribe_n1(nombre, datos, entrada):
    puerta.exigir_diccionario()
    base = puerta.n1_dir()
    os.makedirs(base, mode=0o700, exist_ok=True)
    destino = os.path.join(base, nombre)
    with open(os.path.join(base, ".manifiesto.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        # «Ya existe» = hay algo en la ruta (también un symlink colgante) O el manifiesto ya tiene
        # ese nombre, en cualquier caja (APFS no distingue): borrar el fichero a mano no abre la
        # puerta a sustituir en silencio su sha256 (residuo del hallazgo 28).
        man = puerta.cargar_manifiesto()
        if os.path.lexists(destino) or any(k.lower() == nombre.lower() for k in man["ficheros"]):
            raise PuertaCerrada("%s ya existe en N1 o en su manifiesto: no sobrescribo" % nombre)
        entrada = dict(entrada, sha256=__import__("hashlib").sha256(datos).hexdigest(), bytes=len(datos))
        man["ficheros"][nombre] = entrada
        texto_man = json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True)
        puerta.revisar_manifiesto(texto_man)          # por esquema: `bytes` es len(datos)
        tmp = os.path.join(base, ".tmp-" + nombre)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(datos)
        os.replace(tmp, destino)
        tmpm = puerta.manifiesto_path() + ".tmp"
        with open(tmpm, "w", encoding="utf-8") as fh:
            fh.write(texto_man)
        os.replace(tmpm, puerta.manifiesto_path())
    return destino, entrada


def _opaco(op):
    if not RE_OPACO.match(op or ""):
        raise PuertaCerrada("nombre opaco no válido (forma P-HE, P-KI67, B-HE-1)")
    puerta.revisar_texto(op, "nombre opaco")
    return op


def _motivos_cristal_tiff(t):
    """[motivos] del OCR + Puerta + trazos sobre el nivel ×8 y cada nivel menor (hallazgo 16); del
    OCR + Puerta sobre el ×4 entero; y de la coherencia de cada nivel mayor que el ×8 con el ×8
    (tercera pasada). Lo usan la exportación y la re-validación del envío (hallazgo 9 bis). El ×4
    y la coherencia van por teselas (`lecturas_ocr_nivel`, `_coherencia_nivel`); si aun así algo
    no cabe (Pillow DecompressionBombError, memoria) o no se puede leer, PuertaCerrada: no sale."""
    return _cristal_tiff_detalle(t)[0]


def _cristal_tiff_detalle(t):
    """(motivos, ventanas): los motivos de `_motivos_cristal_tiff` y las ventanas de la coherencia
    que pasan el umbral, [[k, x0, y0, x1, y1, diferencia]] en px del ×8 (`_coherencia_nivel_detalle`)."""
    from PIL import Image
    try:
        return _motivos_cristal_tiff_crudo(t)
    except PuertaCerrada:
        raise
    except (Image.DecompressionBombError, MemoryError, OSError, ValueError) as e:
        raise PuertaCerrada("no pude revisar el cristal del TIFF (%s): no exporto ni envío" % type(e).__name__)


def _motivos_cristal_tiff_crudo(t):
    k8 = _nivel_x8(t)
    if k8 is None:
        raise PuertaCerrada("sin nivel ×8 no puedo revisar el cristal entero: no exporto")
    factores = _comprueba_piramide(t)
    lado8 = max(t.enteros(t.ifds[k8], 256)[0], t.enteros(t.ifds[k8], 257)[0])
    motivos, img8, ventanas = [], None, []
    for k, ifd in enumerate(t.ifds):
        if max(t.enteros(ifd, 256)[0], t.enteros(ifd, 257)[0]) <= lado8:
            img = _imagen_de_nivel(t, k)
            img8 = img if k == k8 else img8
            motivos += ["IFD %d: %s" % (k, m) for m in revisar_cristal(img, con_puerta=True)]
    for k in range(k8):
        den = factores[k8] // factores[k]
        if den not in (2, 4, 8):
            raise PuertaCerrada("IFD %d: nivel a ×%d del ×8, no sé reducirlo: no exporto" % (k, den))
        d, malas = _coherencia_nivel_detalle(t, k, den, img8)
        ventanas += [[k] + list(m) for m in malas]
        if d > UMBRAL_COHERENCIA:
            motivos.append("IFD %d: reducido no se parece al ×8 (diferencia %d; umbral %d)"
                           % (k, d, UMBRAL_COHERENCIA))
        if den == 2:                       # el ×4: OCR entero (por teselas), sin trazos; con Puerta
            puerta.exigir_diccionario()
            motivos += ["IFD %d (×4): %s" % (k, m) for m in
                        _motivos_ocr(lecturas_ocr_nivel(t, k, ESCALAS_X8), con_puerta=True)]
    return motivos, ventanas


# ── Paso 1-bis: VISTO-N1 confirma SOLO lo que enseñó su hoja (2-oct-26) ───────────────────────
# La hoja (`laminillas_exporta.hoja_1bis`, desde la revisión r5 de la ingesta) enseña los sitios del
# OCR, los trazos y, desde hoy, cada ventana de la coherencia que saltó (el recorte del nivel fino
# junto al mismo campo del ×8). Su orden lleva `--hoja <huella>`: el sha256 (16 hex) de lo que
# enseña —este exporta_n1 (su sha256), el TIFF de origen (su sha256), los motivos y las ventanas—.
# exporta_n1 calcula la misma huella con lo que ve AL EXPORTAR y, si no coincide (otra versión,
# otro TIFF, otro motivo, otra ventana), se para ANTES de preguntar. Con más de TOPE_VENTANAS_1BIS
# ventanas tampoco pregunta: la hoja no las ofrece («TODAVÍA NO: demasiadas ventanas»). 24 = cuatro
# páginas de seis pares de recortes por lámina: a 10-20 s por par, 4-8 min por lámina y en torno a
# 1 h para las 15 en el peor caso; más que eso ya no se revisa, se aprueba a ciegas. Medido el
# 2-oct-26 en las 15 láminas reales (`laminillas_exporta -- diagnostico coherencia`, solo números):
# diferencia máxima 2-9 en sus 30 niveles finos y NINGUNA ventana sobre el umbral; el tope no
# recorta nada real, solo lo que una pirámide de verdad no produce.
TOPE_VENTANAS_1BIS = 24
RE_HUELLA = re.compile(r"^[0-9a-f]{16}$")


def _sha256_fichero(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for trozo in iter(lambda: fh.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def huella_1bis(sha_exporta, sha_tiff, motivos, ventanas):
    """La huella (16 hex) de lo que enseña la hoja del paso 1-bis: sha256 de este exporta_n1 y del
    TIFF de origen, los motivos (en su orden) y las ventanas [[k, x0, y0, x1, y1, diferencia]]
    (ordenadas). La calculan la hoja (desde la r5) y `exporta_tiff` (desde lo que ve al exportar)."""
    canon = {"exporta_n1": str(sha_exporta), "tiff": str(sha_tiff), "motivos": [str(m) for m in motivos],
             "ventanas": sorted([int(x) for x in v] for v in ventanas)}
    texto = json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def _comprueba_hoja_1bis(ruta, motivos, ventanas, hoja):
    """Antes de pedir VISTO-N1: con más de TOPE_VENTANAS_1BIS ventanas, o sin la huella de la hoja,
    o con otra, PuertaCerrada (no se pregunta ni se sella nada)."""
    if len(ventanas) > TOPE_VENTANAS_1BIS:
        raise PuertaCerrada("%d ventanas de la coherencia no se parecen al ×8 (tope %d): TODAVÍA NO, demasiadas "
                            "para revisarlas a mano; no pregunto" % (len(ventanas), TOPE_VENTANAS_1BIS))
    if not hoja:
        raise PuertaCerrada("el paso 1-bis va con la huella de su hoja (--hoja, en la orden que lleva la hoja "
                            "de `procesa laminillas_qc -- hoja-1bis`): sin ella no sé qué has mirado; no pregunto")
    propia = huella_1bis(_sha256_fichero(os.path.abspath(__file__)), _sha256_fichero(ruta), motivos, ventanas)
    if hoja != propia:
        raise PuertaCerrada("la Puerta ve hoy en esta lámina algo que su hoja del paso 1-bis no enseña (otra "
                            "versión de exporta_n1, otro TIFF, otro motivo u otra ventana): no pregunto. Repite "
                            "`procesa laminillas_qc -- cristal` y `hoja-1bis` de esta lámina")


def exporta_tiff(ruta, opaco, revisado_en_tty=False, hoja=None):
    _opaco(opaco)
    t = Tiff(ruta)
    try:
        datos, inf = copia_tiff_n1(t)                # formato, pirámide y teselas, antes que nada
        motivos, ventanas = _cristal_tiff_detalle(t)
    finally:
        t.f.close()
    revision = "limpio"
    if motivos:
        if not revisado_en_tty:
            raise PuertaCerrada("texto en el cristal (%s): no entra en N1 hasta el paso 1-bis ({{TITULAR}} mira "
                                "su hoja y teclea la orden que lleva)" % "; ".join(motivos))
        _comprueba_hoja_1bis(ruta, motivos, ventanas, hoja)       # antes de preguntar
        # Atada al sha256 de la copia N1: la re-validación del envío vuelve a pasar el OCR, y solo
        # esta revisión deja salir ESTOS bytes (hallazgo 9 bis). La sella borde, con `via: 'tty'`.
        _visto_en_tty(opaco, hashlib.sha256(datos).hexdigest(), motivos)
        revision = "revisado-en-tty"
    return _escribe_n1("%s.n1.tif" % opaco, datos,
                       {"tipo": "tiff-n1", "mpp": inf["mpp"], "niveles": inf["niveles"],
                        "tags_quitados": inf["tags_quitados"], "cristal": revision})


def exporta_png(ruta, opaco, nivel, mpp=None):
    _opaco(opaco)
    if nivel not in NIVELES:
        raise PuertaCerrada("nivel tiene que ser uno de %s" % ", ".join(NIVELES))
    if nivel != "thumbnail":
        if mpp is None or not (0 < mpp < 1000):
            raise PuertaCerrada("falta el mpp (µm/px) de la imagen")
        nombre = "%s__%s__mpp%.4f.png" % (opaco, nivel, mpp)
    else:
        nombre = "%s__thumbnail.png" % opaco
    datos, img = png_n1(ruta)
    motivos = revisar_cristal(img, ESCALAS_PNG, con_puerta=True)
    if motivos:
        raise PuertaCerrada("texto en la imagen (%s): no entra en N1" % "; ".join(motivos))
    return _escribe_n1(nombre, datos, {"tipo": "png", "nivel": nivel, "mpp": mpp,
                                       "ancho": img.width, "alto": img.height})


def exporta_texto(ruta, nombre):
    if not RE_NOMBRE_TEXTO.match(nombre or "") or nombre == "manifiesto.json":
        raise PuertaCerrada("nombre de fichero de texto no válido")
    puerta.revisar_texto(nombre.rsplit(".", 1)[0], "nombre")
    if os.path.splitext(nombre)[1].lower() != os.path.splitext(ruta)[1].lower():
        raise PuertaCerrada("la extensión del nombre no casa con la del origen")
    with open(ruta, "rb") as fh:
        datos = fh.read()
    puerta.revisar_bytes_texto(nombre, datos)          # se escribe exactamente lo revisado
    return _escribe_n1(nombre, datos, {"tipo": os.path.splitext(nombre)[1].lstrip(".").lower()})


def main(argv):
    ap = argparse.ArgumentParser(prog="exporta_n1.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("tiff")
    a.add_argument("origen")
    a.add_argument("--opaco", required=True)
    a.add_argument("--revisado-en-tty", action="store_true")
    a.add_argument("--hoja")
    b = sub.add_parser("png")
    b.add_argument("origen")
    b.add_argument("--opaco", required=True)
    b.add_argument("--nivel", required=True)
    b.add_argument("--mpp", type=float)
    c = sub.add_parser("texto")
    c.add_argument("origen")
    c.add_argument("--nombre", required=True)
    args = ap.parse_args(argv)
    try:
        puerta.exigir_diccionario()
        if args.cmd == "tiff":
            if args.hoja is not None and not RE_HUELLA.match(args.hoja):
                raise PuertaCerrada("--hoja: la huella son 16 cifras hexadecimales (la de la orden de la hoja)")
            destino, ent = exporta_tiff(args.origen, args.opaco, args.revisado_en_tty, args.hoja)
        elif args.cmd == "png":
            destino, ent = exporta_png(args.origen, args.opaco, args.nivel, args.mpp)
        else:
            destino, ent = exporta_texto(args.origen, args.nombre)
    except PuertaCerrada as e:
        print("🛑 %s" % e)
        return 3
    print("✅ N1: %s (sha256 %s…)" % (os.path.basename(destino), ent["sha256"][:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
