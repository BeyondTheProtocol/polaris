#!/usr/bin/env python3
"""tools/laminillas_capas.py — las capas N1 del piloto que revisa el panel de visión.

Plan «laminillas DFCI», punto 5-bis (requisito BLOQUEANTE del tribunal) y actualización 15
(«Contrato para la siembra N1»). La calibración sobre capas N1 reales (5-bis (i), segunda parte:
`panel_vision/siembra_n1.py`) y la revisión por cada modelo habilitado (5-bis (ii):
`calibra.revisa_capas`, que comprueba `laminillas_proc.tribunal_listo`) leen
`~/Laminillas-N1/panel_vision/capas_piloto.json`. Esto es lo que lo escribe.

ORDEN (la lanza SOLO la ventanilla, en `exporta.sb`: lee SESION, escribe solo N1, sin red):
  python3 tools/lector_clinico.py procesa laminillas_exporta -- capas-piloto
Códigos: 0 hecho; 2 uso; 3 una capa ya en N1 con otros píxeles (o la Puerta para la orden);
4 escrito, pero alguna tarea queda por debajo de MINIMO capas (lo dice); 5 falta el sello o un
producto previo (lo dice; no se escribe nada).

QUÉ PINTA (el contrato que leen los dos consumidores, con los colores y escalas de la PREGUNTA del
protocolo, `calibra._TAREA_TXT`, sin una palabra cambiada):
  nucleos    P-HE a 0,5 µm/px (MPP_PREGUNTA de siembra_n1), 1024×1024 px; cada núcleo de InstanSeg
             (segmentación sellada de `piloto-ibis P-HE`) con SU contorno ámbar (255,190,0) de 1 px
             (borde interior por etiqueta: dos núcleos que se tocan conservan cada uno el suyo).
  ck19       P-CK19 a 0,5 µm/px, 1024×1024 px; la máscara CK19+ sellada (`piloto/ck19/mascara`,
             1 µm/px, vecino más próximo) en contorno magenta (255,0,255) de 2 px (`sinteticos._trazo`).
  artefacto  P-HE a 0,5 µm/px, 1024×1024 px; lo que marcó el detector de artefactos de la H&E
             (GrandQC por núcleo, si corrió en la segmentación sellada): la unión de esos núcleos
             dilatada 8 µm, en contorno naranja (255,110,0) de 2 px. Si GrandQC no corrió (régimen de
             reglas clásicas: lo esperable), la capa no lleva contorno y `origen` lo dice; el panel
             busca entonces artefactos sin marcar en la H&E tal cual.
  registro   superposición en el marco de P-CK19 (la referencia), 768×768 px al mpp del ×8 NATIVO de
             P-CK19 (≈2,005 µm/px: ≈1540 µm, como el campo de 1536 µm de la calibración sintética):
             pesos de ODsum sobre el I0 local (la imagen que usa el registro) de P-CK19 (A, amarillo)
             y de la móvil llevada a la referencia (B, violeta) con la transformada que usa el
             pipeline (la de su FC verificado; la global fuera), pintados con
             `sinteticos.pinta_registro`, y la barra gris (50,50,50) de 100 µm abajo en el centro.
             `contexto.mpp`. Pares (ii) P-KI67↔P-CK19 y (ii-bis) P-HER2NEG/P-HER2↔P-CK19.
  figura     figura de informe de 768×768 px como `sinteticos.caso_figura`: un panel de P-CK19 a 0,5
             µm/px (384 px) en el centro, un punto por núcleo, rojo si el anillo CK19 supera T_CK19
             de la máscara sellada y azul si no, y en tres esquinas la etiqueta «CK-19», la barra de
             50 µm y la leyenda pos/neg (los huecos «50 um» y cuadrado-texto, más anchos que en
             sinteticos: ver HUECO_UNIDAD_EM). `contexto` = la especificación (marcador, mpp,
             escala_um, escala_px) y `siembra.<fichero>.cajas` = dónde está cada elemento.

QUÉ ESCRIBE: cada capa a N1 SOLO por la Puerta, la vía PNG de `exporta_n1.exporta_png` (nombre opaco,
OCR + Puerta + trazos, manifiesto con sha256; la de `laminillas_exporta <OPACO> png`), con el PNG por
una tubería (`/dev/fd/N`): exporta.sb no deja escribir nada fuera de N1 y un PNG sin revisar no se
deja en N1. Se comprueba después que los píxeles en N1 son los pintados. Una capa que la Puerta
rechaza NO entra en el JSON: se cuenta en `rechazadas_puerta` y se pasa al campo siguiente del orden
sellado. Luego `<n1>/panel_vision/capas_piloto.json`, también por la Puerta (`revisar_json`):
  {"tareas": {tarea: [fichero, …]}, "contexto": {fichero: {"mpp", …}},
   "siembra": {fichero_figura: {"cajas": {"etiqueta"|"escala"|"leyenda": [x0, y0, x1, y1]}}},
   "origen": {fichero: {tarea, lámina, campo, …}}, "seleccion": {…}, "version", "generacion",
   "huella_sha256", "sello_sha256", "rechazadas_puerta", "faltan"}
Nombres: `PV-<TAREA>-R<ronda>-<nn>__L0__mpp0.5000.png` (y `__x8__mpp<del ×8>.png` en registro).
«L0» es la etiqueta de `exporta_n1` para una imagen a mpp propio (la usa ya `calibra a-n1`):
exporta_n1 no tiene nivel ×2, y el mpp del nombre es el que manda (`siembra_n1.RE_MPP_NOMBRE`).
Reanudable: una corrida con la MISMA huella (productos de SESION, láminas, sello, exporta_n1, esta
versión y sus parámetros) reutiliza las capas que ya están en N1 con el mismo sha256; una huella
distinta (productos recalculados tras la recongelación) abre una ronda nueva con nombres nuevos.

SELECCIÓN DE CAMPOS (sellada, reproducible y declarada en el JSON): semilla = `semillas.maestra`
del sello de la congelación (0) + DESPLAZAMIENTO[tarea] (como `laminillas_proc._semilla`);
permutación de los candidatos y, en las vistas y la figura, sin solape. Candidatos SOLO por tejido,
núcleos y máscara: densidad de núcleos de InstanSeg por cuadrante del campo (P-HE o P-CK19),
fracción de máscara CK19 en el campo (borde de máscara), cobertura del FC verificado del par
(registro). NUNCA por DAB ni positividad de una diana: la planificación no lee ni un píxel (de
ninguna lámina) ni ningún objeto de P-KI67 (lo fija un test, con su mutante). La figura usa P-CK19,
que es denominador, no diana.

CUÁNTAS CAPAS (CUOTAS, y por qué). `calibra.py sembrar-n1` (`siembra_n1.genera_conjunto_n1`) usa
n = 30 por clase: 30 casos con error y 30 controles por tarea, repartidos en rueda sobre las capas;
el criterio de calibración pide n ≥ 30 por clase. En nucleos, ck19 y artefacto cada caso es una
vista aleatoria de 512 px (de una capa de 1024: cuatro veces su área) en una de 8 orientaciones;
con 15 capas cada una da ~4 casos (~2 errores y ~2 controles), vistas distintas. En registro y
figura la siembra usa la capa ENTERA: un control ES la capa (en registro, con ≤20 µm de más), así
que k capas son k controles distintos; con 30, los 30 controles son 30 imágenes. Coste en 5-bis
(ii): cada modelo habilitado revisa TODAS las capas de sus tareas (Claude, en ck19, registro,
figura y artefacto: 15 + 30 + 30 + 15 = 90). MINIMO = 10 por tarea: por debajo, código 4 y `faltan`
(el JSON se escribe igual con lo que hay: la tarea corta se ve).

LÍMITES DECLARADOS:
  · nucleos y artefacto, solo P-HE: la pregunta dice H&E. La detección nuclear de P-KI67 (IHQ), la
    que bloquea el piloto, NO la revisa este panel con este protocolo (haría mentir a la pregunta;
    otra pregunta = otra calibración).
  · registro: solo los FC VERIFICADOS de cada par (los no verificados ya están fuera del mapa); el
    campo puede incluir tejido de otros FC (con la global, como el pipeline). (iii) H&E↔IHQ no se
    pinta. Los campos de registro pueden solaparse hasta la mitad.
  · Las lecturas de píxeles de láminas diana (P-KI67 en registro) no se anotan en
    `lecturas_diana.jsonl`: exporta.sb no escribe en SESION. Constan en `origen` del JSON.
  · Los colores de la capa los ve el detector de trazos de exporta_n1 igual que en la calibración
    sintética (sinteticos los eligió fuera de sus bandas); el tejido real puede hacer saltar el
    OCR o los trazos: esa capa no entra y se dice.

Requiere numpy, scipy y Pillow (venv `patologia`).
"""
import hashlib
import json
import math
import os
import re
import sys
import threading

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
_PANEL = os.path.join(_AQUI, "panel_vision")
if _PANEL not in sys.path:
    sys.path.insert(1, _PANEL)

VERSION = "capas-piloto/1"
TAREAS = ("nucleos", "ck19", "registro", "figura", "artefacto")      # = calibra.TAREAS (test)
N1_PANEL = "panel_vision"                                             # = calibra.N1_PANEL
CAPAS_PILOTO = "capas_piloto.json"                                    # = calibra.CAPAS_PILOTO
PARCIAL = "capas_piloto.parcial.json"
PREFIJO = "PV"
CODIGO = {"nucleos": "NUC", "ck19": "CK19", "registro": "REG", "figura": "FIG", "artefacto": "ART"}
RE_RONDA = re.compile(r"^%s-[A-Z0-9]+-R(\d{1,3})-\d{2}__" % PREFIJO)
HE, FIJA = "P-HE", "P-CK19"
PARES_REGISTRO = ("P-KI67", "P-HER2NEG", "P-HER2")       # (ii) y (ii-bis), contra P-CK19
MPP_VISTA = 0.5                                           # = siembra_n1.MPP_PREGUNTA
NIVEL_VISTA, NIVEL_REGISTRO = "L0", "x8"
GEOMETRIA = {"lado_vista": 1024, "lado_registro": 768, "factor_registro": 8,
             "lado_figura": 768, "panel_figura": 384}
CUOTAS = {"nucleos": 15, "ck19": 15, "artefacto": 15, "figura": 30, "registro": 30}
MINIMO = 10
RECHAZOS_SEGUIDOS_MAX = 8               # la Puerta rechaza todo: algo sistémico, no el campo; paro
DESPLAZAMIENTO = {"nucleos": 601, "ck19": 602, "registro": 603, "figura": 604, "artefacto": 605}
# Candidatos (inferencia mía; el plan no da cifra): densidad de núcleos de InstanSeg en CADA
# cuadrante del campo (tejido en los cuatro: la siembra pone el error en un cuadrante dado) y, en
# CK19, una fracción de máscara que asegura borde (epitelio y estroma en el mismo campo).
CRITERIO = {
    "nucleos": {"lamina": HE, "densidad_min_mm2_por_cuadrante": 600.0},
    "artefacto": {"lamina": HE, "densidad_min_mm2_por_cuadrante": 300.0},
    "ck19": {"lamina": FIJA, "densidad_min_mm2_por_cuadrante": 600.0, "fraccion_mascara": [0.05, 0.7]},
    "figura": {"lamina": FIJA, "densidad_min_mm2_por_cuadrante": 600.0, "fraccion_mascara": [0.1, 0.9]},
    "registro": {"lamina": FIJA, "cobertura_fc_verificado_min": 0.25},
}
OD_FONDO, OD_TEJIDO = 0.05, 0.35        # ODsum → peso de registro: 0 en vidrio, 1 en tejido
SIGMA_OD = 0.8                          # px, como la gaussiana de `sinteticos.caso_registro`
DILATA_ARTEFACTO_UM = 8.0
MARGEN_NUCLEOS_UM = 40.0
MARCADOR_FIGURA = "CK-19"               # de `sinteticos.MARCADORES`: OCR-seguro (sin 4 alfanuméricos)
# Huecos (en «em», el tamaño de la fuente) entre el número y «um» de la barra y entre el cuadrado de
# color y «pos»/«neg». Con los de `sinteticos.caso_figura` (un espacio; 6 px) tesseract lee «25um» y
# «Mneg» (cuadrado + texto) a ×1 y ×0,5 con confianza ≥60, y la Puerta de N1 rechaza TODAS las
# figuras (medido el 2-oct-26 con exporta_n1.lecturas_ocr sobre figuras sintéticas de 512 px; a
# 768 px pasaba, por poco). Con 0,6 em el texto es el mismo y se lee en palabras sueltas.
HUECO_UNIDAD_EM, HUECO_LEYENDA_EM = 0.6, 0.6
# Cada elemento de la figura pasa por los cuatro cuadrantes en cuatro figuras seguidas.
DISPOSICIONES = (
    {"etiqueta": "superior_izquierdo", "escala": "superior_derecho", "leyenda": "inferior_izquierdo"},
    {"etiqueta": "superior_derecho", "escala": "inferior_izquierdo", "leyenda": "inferior_derecho"},
    {"etiqueta": "inferior_izquierdo", "escala": "inferior_derecho", "leyenda": "superior_izquierdo"},
    {"etiqueta": "inferior_derecho", "escala": "superior_izquierdo", "leyenda": "superior_derecho"},
)
NOTA_SELECCION = ("fields chosen by tissue only: nuclear density per quadrant (InstanSeg), CK19 "
                  "mask fraction, verified consensus-fragment cover; never by DAB or positivity of "
                  "any target; sealed permutation")
LECTOR = None                           # el lector único por defecto (los tests lo sustituyen)
# Los textos fijos de `origen`: pasan por la Puerta con el esqueleto ANTES de exportar nada (una
# palabra que se parezca a un nombre del titular pararía la orden al final, con todo exportado).
TEXTO = {
    "nucleos": "InstanSeg nuclei (sealed segmentation), own amber outline",
    "ck19": "sealed CK19+ mask (nearest neighbour from its grid), magenta",
    "artefacto_gq": "GrandQC marks per nucleus (sealed segmentation), union dilated 8 um",
    "artefacto_sin": "GrandQC did not run on P-HE (classical regime): no orange outline",
    "figura": "report figure: P-CK19 panel, dot red if its CK19 ring is above T_CK19 (denominator, no target)",
    "registro": "ODsum weights over local I0: A = P-CK19, B = moving slide through the pipeline transform "
                "(verified FC; global elsewhere)",
}


class FaltaSello(RuntimeError):
    """Sin sello válido de la congelación (0), o sin `semillas.maestra`: no se elige nada."""


class CapaAjena(RuntimeError):
    """El nombre que tocaría ya está en N1 con OTROS píxeles: no se sobrescribe ni se reutiliza."""


class PuertaSistemica(RuntimeError):
    """La Puerta rechaza RECHAZOS_SEGUIDOS_MAX campos seguidos de una tarea: no es el campo."""


# ── utilidades ──────────────────────────────────────────────────────────────────────────────
def _np():
    import numpy as np
    return np


def _sha_fichero(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for trozo in iter(lambda: fh.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def _lee_json(ruta):
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh)


def _lee_json_o_nada(ruta):
    try:
        return _lee_json(ruta)
    except (OSError, ValueError):
        return None


def _proc():
    import laminillas_proc as P
    return P


def _sint():
    import sinteticos as S
    return S


def _lector(lector):
    if lector is not None:
        return lector
    if LECTOR is not None:
        return LECTOR
    import laminillas_lector as L
    return L


def nombre_capa(tarea, ronda, i, nivel, mpp):
    """(opaco, nombre N1) de la capa: el nombre que escribe `exporta_n1.exporta_png(…, opaco,
    nivel, mpp)`."""
    opaco = "%s-%s-R%d-%02d" % (PREFIJO, CODIGO[tarea], int(ronda), int(i))
    return opaco, "%s__%s__mpp%.4f.png" % (opaco, nivel, mpp)


# ── la Puerta de N1 (importada, no editada) ───────────────────────────────────────────────────
class PuertaN1Png:
    """`exporta_n1` + `puerta_n1`, tal cual: la vía PNG de `exporta_png` (OCR + Puerta + trazos,
    nombre opaco, manifiesto con sha256), la lectura del manifiesto y la Puerta sobre el JSON."""
    nombre = "exporta_n1.exporta_png + puerta_n1.revisar_json"

    def __init__(self):
        import exporta_n1
        self.E = exporta_n1
        self.p = exporta_n1.puerta
        self.p.exigir_diccionario()

    def version(self):
        return _sha_fichero(os.path.join(_AQUI, "exporta_n1.py"))

    def n1_dir(self):
        return self.p.n1_dir()

    def manifiesto(self):
        return self.p.cargar_manifiesto().get("ficheros") or {}

    def canonico(self, rgb):
        from PIL import Image
        np = _np()
        return self.E.png_canonico(Image.fromarray(np.ascontiguousarray(rgb), "RGB"))[0]

    def exporta(self, datos, opaco, nivel, mpp):
        """`datos` (PNG) por `exporta_png`, que lee una RUTA: se le da `/dev/fd/N` de una tubería
        (nada se escribe fuera de N1 ni antes de la Puerta). Devuelve (nombre, sha256)."""
        r, w = os.pipe()

        def escribe():
            try:
                vista = memoryview(datos)
                while vista:
                    vista = vista[os.write(w, vista):]
            except OSError:                        # BrokenPipeError: exporta_png no leyó
                pass
            finally:
                os.close(w)
        hilo = threading.Thread(target=escribe, daemon=True)
        hilo.start()
        try:
            destino, ent = self.E.exporta_png("/dev/fd/%d" % r, opaco, nivel, mpp)
        finally:
            os.close(r)
            hilo.join(30)
        return os.path.basename(destino), ent["sha256"]

    def es_rechazo(self, e):
        """¿La Puerta rechazó ESTA imagen? Si el fallo es del sistema (sin diccionario: fail-closed),
        no es un rechazo de la capa: la orden para."""
        if not isinstance(e, self.E.PuertaCerrada):
            return False
        try:
            self.p.exigir_diccionario()
        except Exception:                                   # noqa: BLE001
            return False
        return True

    def lee(self, nombre):
        from PIL import Image
        np = _np()
        with Image.open(os.path.join(self.n1_dir(), nombre)) as im:
            return np.array(im.convert("RGB"))

    def revisa_json(self, texto):
        self.p.revisar_json(texto, CAPAS_PILOTO)


# ── entradas de SESION (solo lectura) ───────────────────────────────────────────────────────
class MascaraBits:
    """La máscara CK19+ sellada (`np.packbits(mask, axis=None)`) sin desempaquetarla entera: por
    filas, ventanas y recuentos por bloque (una lámina a 1 µm/px son cientos de MB)."""

    def __init__(self, bits, forma):
        np = _np()
        self.bits = np.asarray(bits, np.uint8).reshape(-1)
        self.H, self.W = int(forma[0]), int(forma[1])
        self._bloques = {}

    def filas(self, r0, r1):
        np = _np()
        r0, r1 = max(0, int(r0)), min(self.H, int(r1))
        if r1 <= r0:
            return np.zeros((0, self.W), bool)
        b0, b1 = r0 * self.W, r1 * self.W
        y0, y1 = b0 // 8, -(-b1 // 8)
        u = np.unpackbits(self.bits[y0:y1])
        return u[b0 - 8 * y0:b1 - 8 * y0].reshape(r1 - r0, self.W).view(bool)

    def ventana(self, r0, r1, c0, c1):
        np = _np()
        out = np.zeros((max(0, r1 - r0), max(0, c1 - c0)), bool)
        rr0, rr1, cc0, cc1 = max(0, r0), min(self.H, r1), max(0, c0), min(self.W, c1)
        if rr1 > rr0 and cc1 > cc0:
            out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = self.filas(rr0, rr1)[:, cc0:cc1]
        return out

    def bloques(self, k):
        """Píxeles de máscara por bloque k×k: (ceil(H/k), ceil(W/k))."""
        np = _np()
        if k in self._bloques:
            return self._bloques[k]
        ny, nx = -(-self.H // k), -(-self.W // k)
        out = np.zeros((ny, nx), np.int64)
        for by in range(ny):
            f = self.filas(by * k, (by + 1) * k)
            if f.shape[1] < nx * k:
                f = np.pad(f, ((0, 0), (0, nx * k - f.shape[1])))
            out[by] = f.reshape(f.shape[0], nx, k).sum(axis=(0, 2))
        self._bloques[k] = out
        return out


def _huella_entradas(base, rels, extra):
    h = hashlib.sha256()
    for rel in sorted(set(rels)):
        p = os.path.join(base, rel)
        h.update(rel.encode("utf-8") + b"=")
        h.update((_sha_fichero(p) if os.path.isfile(p) else "-").encode("ascii") + b";")
    h.update(json.dumps(extra, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    return h.hexdigest()


def carga_sello(base):
    """(Sello verificado, semilla maestra). FaltaSello si no hay sello completo o semilla."""
    import laminillas_sello as SL
    try:
        s = SL.exige(os.path.join(base, SL.FICHERO), [FIJA, "P-KI67", HE], SL.SECCIONES_BASE)
    except (SL.SelloAusente, SL.SelloInvalido) as e:
        raise FaltaSello("%s: %s" % (type(e).__name__, e))
    maestra = (s.d.get("semillas") or {}).get("maestra")
    if isinstance(maestra, bool) or not isinstance(maestra, int):
        raise FaltaSello("el sello no trae `semillas.maestra` entera: la selección no estaría sellada")
    return s, maestra


def carga_entradas(base, sello, pares=PARES_REGISTRO):
    """Los productos del piloto que usan la selección y el pintado, verificados contra el sello y la
    huella de sus entradas. Lo que falta deja su tarea en `faltan` (con el motivo); no para la orden.
    NUNCA lee objetos de P-KI67 ni de ninguna diana: núcleos de P-HE y P-CK19, máscara y objetos
    de P-CK19 (denominador), el FC del piloto y las transformadas del registro."""
    np = _np()
    P = _proc()
    import laminillas_comun as C
    ent = {"faltan": {}, "rels": ["congelacion.json"], "laminas": {}, "motivos": {}}

    def falta(tareas, motivo):
        for t in tareas:
            ent["faltan"].setdefault(t, motivo)

    # P-HE: núcleos de la segmentación sellada (nucleos, artefacto)
    try:
        nuc = P.nucleos_lamina(base, HE)
        if (nuc.get("resumen") or {}).get("sello") != sello.sha256:
            raise P.FaltaEntrada("núcleos de P-HE de otro sello: repite `piloto-ibis P-HE`")
        ent["he"] = {"centroide": np.asarray(nuc["centroide"], np.float64).reshape(-1, 2),
                     "pol_xy": np.asarray(nuc["pol_xy"], np.float64).reshape(-1, 2),
                     "pol_offs": np.asarray(nuc["pol_offs"], np.int64),
                     "grandqc": (np.asarray(nuc["grandqc"], bool).reshape(-1) if "grandqc" in nuc
                                 else None),
                     "mpp_l0": float(nuc["mpp_l0"])}
        ent["rels"] += P._rels_nucleos(base, HE)
        ent["laminas"][HE] = True
    except P.FaltaEntrada as e:
        falta(("nucleos", "artefacto"), str(e))
    # P-CK19: núcleos (selección), máscara (ck19, figura) y objetos (puntos de la figura)
    try:
        nuc = P.nucleos_lamina(base, FIJA)
        if (nuc.get("resumen") or {}).get("sello") != sello.sha256:
            raise P.FaltaEntrada("núcleos de P-CK19 de otro sello: repite `piloto-ibis P-CK19`")
        rels_ck = P._rels_nucleos(base, FIJA)
        hu = P._huella(base, rels_ck + ["congelacion.json"])
        mk_json = os.path.join(P.DIR_PILOTO, "ck19", "mascara.json")
        mk_npz = os.path.join(P.DIR_PILOTO, "ck19", "mascara.npz")
        if not (os.path.isfile(os.path.join(base, mk_json)) and os.path.isfile(os.path.join(base, mk_npz))):
            raise P.FaltaEntrada("falta la máscara CK19: corre antes `piloto-ibis P-CK19`")
        mk = _lee_json(os.path.join(base, mk_json))
        if mk.get("entradas") != hu:
            raise P.FaltaEntrada("la máscara CK19 no es de los núcleos y el sello vigentes: repite "
                                 "`piloto-ibis P-CK19`")
        with np.load(os.path.join(base, mk_npz), allow_pickle=False) as z:
            bits, forma = z["bits"], tuple(int(v) for v in z["forma"])
        ent["ck"] = {"centroide": np.asarray(nuc["centroide"], np.float64).reshape(-1, 2),
                     "mpp_l0": float(nuc["mpp_l0"])}
        ent["mascara"] = {"m": MascaraBits(bits, forma), "origen": [float(v) for v in mk["origen_l0"]],
                          "mpp": float(mk["mpp"]), "mpp_l0": float(mk["mpp_l0"]),
                          "T": float(mk["umbral"]["T"])}
        ent["rels"] += rels_ck + [mk_json, mk_npz]
        ent["laminas"][FIJA] = True
    except P.FaltaEntrada as e:
        falta(("ck19", "figura"), str(e))
    if "mascara" in ent:
        try:
            ob = P.carga_objetos(base, FIJA)
            if ob.get("sello") != sello.sha256 or ob.get("entradas") != P._huella(
                    base, P._rels_nucleos(base, FIJA) + ["congelacion.json"]):
                raise P.FaltaEntrada("objetos de P-CK19 de otro sello o de otros núcleos: repite "
                                     "`piloto-ibis P-CK19`")
            ent["ck"]["objetos"] = {"centroide": np.asarray(ob["centroide"], np.float64).reshape(-1, 2),
                                    "dab_anillo": np.asarray(ob["dab_anillo"], np.float64).reshape(-1)}
            ent["rels"] += [P._rel("objetos_%s.npz" % FIJA), P._rel("objetos_%s.json" % FIJA)]
        except P.FaltaEntrada as e:
            falta(("figura",), str(e))
    # registro: FC del piloto y transformadas de (ii)/(ii-bis); solo FC verificados
    rel_fc = P.REL_FC
    if not os.path.isfile(os.path.join(base, rel_fc)):
        falta(("registro",), "falta el FC del piloto: corre antes `registro-par P-KI67 P-CK19`")
    else:
        sha_fc = _sha_fichero(os.path.join(base, rel_fc))
        with np.load(os.path.join(base, rel_fc), allow_pickle=False) as z:
            ids = [str(x) for x in z["ids"].tolist()]
            fcs = {"ids": ids, "mascaras": np.asarray(z["mascaras"], bool), "mpp": float(z["mpp"])}
        regs = {}
        for movil in pares:
            rel = P._rel("registro", "%s.json" % movil)
            if not os.path.isfile(os.path.join(base, rel)):
                continue
            if not C.esta_hecho(base, "registro-par", movil, FIJA):
                ent["motivos"]["registro " + movil] = "sin «hecho» vigente de registro-par"
                continue
            d = _lee_json(os.path.join(base, rel))
            if d.get("sello") != sello.sha256 or d.get("fc_piloto_sha256") != sha_fc:
                ent["motivos"]["registro " + movil] = "de otro sello o de otro FC: repite registro-par"
                continue
            rs = d.get("resumen") or {}
            glob = (rs.get("global") or {}).get("matriz_um")
            ver = {}
            for f in rs.get("fragmentos") or []:
                if f.get("pasa") is True and f.get("matriz_um") is not None and str(f.get("id")) in ids:
                    ver[str(f["id"])] = np.asarray(f["matriz_um"], np.float64)
            if not ver:
                ent["motivos"]["registro " + movil] = "ningún FC verificado"
                continue
            regs[movil] = {"global": None if glob is None else np.asarray(glob, np.float64),
                           "verificados": ver}
            ent["rels"].append(rel)
            ent["laminas"][movil] = True
        if regs:
            ent["fcs"], ent["registros"] = fcs, regs
            ent["rels"].append(rel_fc)
            ent["laminas"][FIJA] = True
        else:
            falta(("registro",), "ningún par (ii)/(ii-bis) con FC verificado y vigente")
    return ent


# ── selección (sin leer un píxel: solo tejido, núcleos, máscara y FC) ───────────────────────
def _conteos_celda(xy, celda, nx, ny):
    np = _np()
    ix = np.floor(xy[:, 0] / celda).astype(np.int64)
    iy = np.floor(xy[:, 1] / celda).astype(np.int64)
    ok = (ix >= 0) & (iy >= 0) & (ix < nx) & (iy < ny)
    return np.bincount(iy[ok] * nx + ix[ok], minlength=nx * ny).reshape(ny, nx).astype(np.float64)


def _sumas(a, k):
    """S[i, j] = a[i:i+k, j:j+k].sum(), para cada ventana k×k que cabe."""
    np = _np()
    c = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    c[1:, 1:] = a.cumsum(0).cumsum(1)
    return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]


def _mascara_por_celda(mk, celda, nx, ny):
    """Píxeles de máscara CK19 por celda de la rejilla (px L0 de P-CK19), por el centro de bloques
    de ~8 µm."""
    np = _np()
    fm = mk["mpp"] / mk["mpp_l0"]
    k = max(1, int(round(8.0 / mk["mpp"])))
    b = mk["m"].bloques(k)
    yy, xx = np.mgrid[0:b.shape[0], 0:b.shape[1]]
    ix = np.floor((mk["origen"][0] + (xx + 0.5) * k * fm) / celda).astype(np.int64).ravel()
    iy = np.floor((mk["origen"][1] + (yy + 0.5) * k * fm) / celda).astype(np.int64).ravel()
    ok = (ix >= 0) & (iy >= 0) & (ix < nx) & (iy < ny)
    return np.bincount(iy[ok] * nx + ix[ok], weights=b.ravel()[ok].astype(np.float64),
                       minlength=nx * ny).reshape(ny, nx)


def _candidatos_campo(xy, dims, mpp_l0, lado_px, densidad_min, mascara=None, fraccion=None):
    """Campos cuadrados de `lado_px` a 0,5 µm/px sobre una rejilla de 1/4 de campo, con densidad de
    núcleos ≥ `densidad_min` (/mm²) en CADA cuadrante y, si se da, fracción de máscara CK19 en
    [lo, hi]. [(i, j, x0, y0)] (x0, y0 en px L0), en orden de rejilla."""
    np = _np()
    lado_l0 = lado_px * MPP_VISTA / mpp_l0
    celda = lado_l0 / 4.0
    nx, ny = int(dims[0] // celda), int(dims[1] // celda)
    if nx < 4 or ny < 4:
        return []
    q = _sumas(_conteos_celda(xy, celda, nx, ny), 2)
    minimo = densidad_min * (2 * celda * mpp_l0 / 1000.0) ** 2
    n_i, n_j = ny - 3, nx - 3
    ok = np.ones((n_i, n_j), bool)
    for di in (0, 2):
        for dj in (0, 2):
            ok &= q[di:di + n_i, dj:dj + n_j] >= minimo
    if mascara is not None:
        fm = mascara["mpp"] / mascara["mpp_l0"]
        frac = _sumas(_mascara_por_celda(mascara, celda, nx, ny), 4) / ((lado_l0 / fm) ** 2)
        ok &= (frac >= fraccion[0]) & (frac <= fraccion[1])
    ii, jj = np.nonzero(ok)
    return [(int(i), int(j), int(round(j * celda)), int(round(i * celda))) for i, j in zip(ii, jj)]


def _orden_sellado(candidatos, rng, sep=4, limite=None):
    """Permutación sellada de los candidatos [(i, j, …)] y, de ella, los que no se solapan con un
    elegido antes (dos campos de rejilla se solapan si |Δi| < sep y |Δj| < sep; sep 0 = sin esa
    regla). Hasta `limite`."""
    np = _np()
    if not candidatos:
        return []
    ocupado = None
    if sep:
        ocupado = np.zeros((max(c[0] for c in candidatos) + sep + 1,
                            max(c[1] for c in candidatos) + sep + 1), bool)
    out = []
    for k in rng.permutation(len(candidatos)):
        c = candidatos[int(k)]
        if sep:
            i, j = c[0], c[1]
            if ocupado[i:i + sep, j:j + sep].any():
                continue
            ocupado[max(0, i - sep + 1):i + sep, max(0, j - sep + 1):j + sep] = True
        out.append(c)
        if limite is not None and len(out) >= limite:
            break
    return out


def _candidatos_registro(ent, lector, geo):
    """[(par, b, a, ux0, uy0)] (µm del marco de P-CK19): campos de `lado_registro` px al ×8 nativo
    de P-CK19 en una rejilla de medio campo, con cobertura del FC VERIFICADO de ese par ≥ el
    criterio. Solo máscaras de FC y la lista de verificados: ni un píxel."""
    np = _np()
    _f, mpp8, _wh = lector.abre(FIJA).nivel(geo["factor_registro"])
    lado_um = geo["lado_registro"] * mpp8
    fcs = ent["fcs"]
    mpp_fc = fcs["mpp"]
    Hf, Wf = fcs["mascaras"].shape[1:]
    paso, lado_fc = lado_um / 2.0, lado_um / mpp_fc
    cob_min = CRITERIO["registro"]["cobertura_fc_verificado_min"]
    na = int(max(0, math.floor((Wf * mpp_fc - lado_um) / paso))) + 1
    nb = int(max(0, math.floor((Hf * mpp_fc - lado_um) / paso))) + 1
    # La cobertura, sobre bloques de k px del FC (≤1/16 de campo): las máscaras de FC de una lámina
    # real son rejillas ×8 enteras (decenas de MB cada una) y una suma acumulada a resolución
    # completa pasaría de un GB.
    k = max(1, int(lado_fc // 16))
    hb, wb = -(-Hf // k), -(-Wf // k)
    out = []
    for par in PARES_REGISTRO:
        reg = (ent.get("registros") or {}).get(par)
        if not reg:
            continue
        union = np.zeros((hb * k, wb * k), bool)
        for i in sorted(reg["verificados"]):
            union[:Hf, :Wf] |= fcs["mascaras"][fcs["ids"].index(i)]
        c = np.zeros((hb + 1, wb + 1))
        c[1:, 1:] = union.reshape(hb, k, wb, k).sum(axis=(1, 3)).cumsum(0).cumsum(1)
        del union
        for b in range(nb):
            for a in range(na):
                ux0, uy0 = a * paso, b * paso
                c0, r0 = int(round(ux0 / mpp_fc / k)), int(round(uy0 / mpp_fc / k))
                c1, r1 = min(wb, int(round((ux0 + lado_um) / mpp_fc / k))), min(hb, int(round((uy0 + lado_um) /
                                                                                               mpp_fc / k)))
                if c1 <= c0 or r1 <= r0:
                    continue
                if (c[r1, c1] - c[r0, c1] - c[r1, c0] + c[r0, c0]) / (lado_fc * lado_fc) >= cob_min:
                    out.append((par, b, a, ux0, uy0))
    return out


def planifica(base, lector=None, geo=None, cuotas=None):
    """{"plan": {tarea: [campo, …]}, "faltan", …}: los campos candidatos de cada tarea, en el orden
    sellado en que se intentan. No lee ni un píxel: del lector solo `abre` (dimensiones, mpp y
    niveles)."""
    np = _np()
    lector = _lector(lector)
    geo = dict(GEOMETRIA, **(geo or {}))
    cuotas = dict(CUOTAS, **(cuotas or {}))
    sello, maestra = carga_sello(base)
    ent = carga_entradas(base, sello)
    plan = {}
    faltan = dict(ent["faltan"])

    def rng(t):
        return np.random.default_rng(int(maestra) + int(DESPLAZAMIENTO[t]))

    def limite(t):
        return 4 * int(cuotas[t]) + 20

    for t in ("nucleos", "artefacto"):
        if t in faltan:
            continue
        cands = _candidatos_campo(ent["he"]["centroide"], lector.abre(HE).dimensiones_l0, ent["he"]["mpp_l0"],
                                  geo["lado_vista"], CRITERIO[t]["densidad_min_mm2_por_cuadrante"])
        plan[t] = [{"lamina": HE, "x0": x0, "y0": y0, "lado": geo["lado_vista"]}
                   for _i, _j, x0, y0 in _orden_sellado(cands, rng(t), 4, limite(t))]
    for t, lado in (("ck19", geo["lado_vista"]), ("figura", geo["panel_figura"])):
        if t in faltan:
            continue
        cands = _candidatos_campo(ent["ck"]["centroide"], lector.abre(FIJA).dimensiones_l0, ent["ck"]["mpp_l0"],
                                  lado, CRITERIO[t]["densidad_min_mm2_por_cuadrante"], ent["mascara"],
                                  CRITERIO[t]["fraccion_mascara"])
        plan[t] = [{"lamina": FIJA, "x0": x0, "y0": y0, "lado": lado}
                   for _i, _j, x0, y0 in _orden_sellado(cands, rng(t), 4, limite(t))]
    if "registro" not in faltan:
        cands = _candidatos_registro(ent, lector, geo)
        orden = _orden_sellado(cands, rng("registro"), 0, limite("registro"))
        plan["registro"] = [{"lamina": FIJA, "par": par, "ux0": ux0, "uy0": uy0, "lado": geo["lado_registro"]}
                            for par, _b, _a, ux0, uy0 in orden]
    for t in TAREAS:
        if t not in faltan and not plan.get(t):
            faltan[t] = "ningún campo cumple el criterio de selección"
    return {"plan": plan, "faltan": faltan, "sello": sello, "ent": ent, "geo": geo, "cuotas": cuotas}


# ── pintado ─────────────────────────────────────────────────────────────────────────────────
def _lee(lector, op, mpp, x0, y0, w, h):
    np = _np()
    a = np.asarray(lector.lee_region(op, mpp, int(x0), int(y0), int(w), int(h)))
    return np.ascontiguousarray(a[..., :3]).astype(np.uint8)


def _etiquetas(pol_xy, pol_offs, idx, x0, y0, f, H, W):
    """Imagen de etiquetas (int32) de los polígonos `idx` (px L0) en el campo: el píxel j cubre L0
    [x0 + j·f, x0 + (j+1)·f) (convención `laminillas_color.a_pixel`); Pillow pinta con el centro
    del píxel en el entero, de ahí el −0,5."""
    from PIL import Image, ImageDraw
    np = _np()
    img = Image.new("I", (W, H), 0)
    d = ImageDraw.Draw(img)
    for k, i in enumerate(idx, 1):
        pts = (pol_xy[pol_offs[i]:pol_offs[i + 1]] - (x0, y0)) / f - 0.5
        if len(pts) >= 3:
            d.polygon([(float(p[0]), float(p[1])) for p in pts], fill=int(k))
    return np.array(img, np.int32)


def _borde_etiquetas(lab):
    """Borde interior de 1 px de CADA etiqueta (8-vecindad; el borde de la imagen cuenta como fuera)."""
    np = _np()
    H, W = lab.shape
    p = np.pad(lab, 1)
    b = np.zeros((H, W), bool)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                b |= p[1 + dy:1 + dy + H, 1 + dx:1 + dx + W] != lab
    return b & (lab > 0)


def _en_campo(xy, x0, y0, lado_l0, margen):
    np = _np()
    return np.nonzero((xy[:, 0] >= x0 - margen) & (xy[:, 0] < x0 + lado_l0 + margen) &
                      (xy[:, 1] >= y0 - margen) & (xy[:, 1] < y0 + lado_l0 + margen))[0]


def pinta_nucleos(lector, campo, ent, k=0, geo=None):
    S = _sint()
    he = ent["he"]
    n, f = campo["lado"], MPP_VISTA / he["mpp_l0"]
    rgb = _lee(lector, HE, MPP_VISTA, campo["x0"], campo["y0"], n, n)
    sel = _en_campo(he["centroide"], campo["x0"], campo["y0"], n * f, MARGEN_NUCLEOS_UM / he["mpp_l0"])
    lab = _etiquetas(he["pol_xy"], he["pol_offs"], sel, campo["x0"], campo["y0"], f, n, n)
    rgb[_borde_etiquetas(lab)] = S.COLOR_NUCLEOS
    return {"rgb": rgb, "nivel": NIVEL_VISTA, "mpp": MPP_VISTA, "contexto": {"mpp": MPP_VISTA},
            "origen": {"lamina": HE, "campo_l0": [campo["x0"], campo["y0"], round(n * f, 1)],
                       "capa": TEXTO["nucleos"]}}


def pinta_artefacto(lector, campo, ent, k=0, geo=None):
    from scipy import ndimage as ndi
    S = _sint()
    he = ent["he"]
    n, f = campo["lado"], MPP_VISTA / he["mpp_l0"]
    rgb = _lee(lector, HE, MPP_VISTA, campo["x0"], campo["y0"], n, n)
    detector = TEXTO["artefacto_sin"]
    if he["grandqc"] is not None:
        detector = TEXTO["artefacto_gq"]
        sel = _en_campo(he["centroide"], campo["x0"], campo["y0"], n * f, MARGEN_NUCLEOS_UM / he["mpp_l0"])
        sel = sel[he["grandqc"][sel]]
        if len(sel):
            zona = _etiquetas(he["pol_xy"], he["pol_offs"], sel, campo["x0"], campo["y0"], f, n, n) > 0
            zona = ndi.binary_fill_holes(ndi.binary_dilation(zona, structure=S._disco(DILATA_ARTEFACTO_UM /
                                                                                      MPP_VISTA)))
            rgb[S._trazo(zona, 2)] = S.COLOR_QC
    return {"rgb": rgb, "nivel": NIVEL_VISTA, "mpp": MPP_VISTA, "contexto": {"mpp": MPP_VISTA},
            "origen": {"lamina": HE, "campo_l0": [campo["x0"], campo["y0"], round(n * f, 1)],
                       "detector": detector}}


def _mascara_en_campo(mk, x0, y0, n, f):
    """La máscara CK19 (vecino más próximo) en el campo de n×n px a 0,5 µm/px con origen L0 (x0, y0)."""
    np = _np()
    fm = mk["mpp"] / mk["mpp_l0"]
    cen = (np.arange(n) + 0.5) * f
    cols = np.floor((x0 + cen - mk["origen"][0]) / fm).astype(np.int64)
    rows = np.floor((y0 + cen - mk["origen"][1]) / fm).astype(np.int64)
    r0, c0 = int(rows.min()), int(cols.min())
    win = mk["m"].ventana(r0, int(rows.max()) + 1, c0, int(cols.max()) + 1)
    return win[(rows - r0)[:, None], (cols - c0)[None, :]]


def pinta_ck19(lector, campo, ent, k=0, geo=None):
    S = _sint()
    ck, mk = ent["ck"], ent["mascara"]
    n, f = campo["lado"], MPP_VISTA / ck["mpp_l0"]
    rgb = _lee(lector, FIJA, MPP_VISTA, campo["x0"], campo["y0"], n, n)
    rgb[S._trazo(_mascara_en_campo(mk, campo["x0"], campo["y0"], n, f), 2)] = S.COLOR_CK19
    return {"rgb": rgb, "nivel": NIVEL_VISTA, "mpp": MPP_VISTA, "contexto": {"mpp": MPP_VISTA},
            "origen": {"lamina": FIJA, "campo_l0": [campo["x0"], campo["y0"], round(n * f, 1)],
                       "capa": TEXTO["ck19"]}}


def pinta_figura(lector, campo, ent, k=0, geo=None):
    """Figura de informe con la disposición `k % 4` (cada elemento recorre los cuatro cuadrantes)."""
    from PIL import Image, ImageDraw
    S = _sint()
    np = _np()
    geo = dict(GEOMETRIA, **(geo or {}))
    lado, P = geo["lado_figura"], campo["lado"]
    ck, mk = ent["ck"], ent["mascara"]
    f = MPP_VISTA / ck["mpp_l0"]
    panel = _lee(lector, FIJA, MPP_VISTA, campo["x0"], campo["y0"], P, P)
    img = Image.new("RGB", (lado, lado), (255, 255, 255))
    off = (lado - P) // 2
    img.paste(Image.fromarray(panel, "RGB"), (off, off))
    d = ImageDraw.Draw(img)
    rpt = max(2, lado // 200)
    ob = ck["objetos"]
    for i in _en_campo(ob["centroide"], campo["x0"], campo["y0"], P * f, 0.0):
        v = ob["dab_anillo"][i]
        x = off + (ob["centroide"][i, 0] - campo["x0"]) / f - 0.5
        y = off + (ob["centroide"][i, 1] - campo["y0"]) / f - 0.5
        if np.isfinite(v) and off <= x < off + P and off <= y < off + P:
            d.ellipse((x - rpt, y - rpt, x + rpt, y + rpt), fill=S.COLOR_POS if v > mk["T"] else S.COLOR_NEG)
    validas = [L for L in S.ESCALAS_FIGURA_UM if 0.08 * lado <= L / S.MPP_FIGURA <= 0.2 * lado] or \
        [S.ESCALAS_FIGURA_UM[0]]
    L = validas[k % len(validas)]
    fs = max(12, int(round(0.045 * lado)))
    fuente = S._fuente(fs)
    cajas = {}
    for elem, q in DISPOSICIONES[k % len(DISPOSICIONES)].items():
        x0, y0, x1, y1 = S._caja_esquina(q, lado)
        cajas[elem] = [int(x0), int(y0), int(x1), int(y1)]
        if elem == "etiqueta":
            d.text((x0, y0), MARCADOR_FIGURA, fill=S.COLOR_TEXTO, font=fuente)
        elif elem == "escala":
            largo, grueso, by = int(round(L / S.MPP_FIGURA)), max(3, lado // 128), y0 + fs // 2
            d.rectangle((x0, by, x0 + largo - 1, by + grueso - 1), fill=S.COLOR_BARRA)
            num = "%d" % L                         # «<L> um», con el hueco de HUECO_UNIDAD_EM
            d.text((x0, by + grueso + 4), num, fill=S.COLOR_TEXTO, font=fuente)
            d.text((x0 + d.textlength(num, font=fuente) + HUECO_UNIDAD_EM * fs, by + grueso + 4), "um",
                   fill=S.COLOR_TEXTO, font=fuente)
        else:
            for j, (col, txt) in enumerate(zip((S.COLOR_POS, S.COLOR_NEG), ("pos", "neg"))):
                ry = y0 + j * (fs + 6)
                d.rectangle((x0, ry, x0 + fs - 1, ry + fs - 1), fill=col)
                d.text((x0 + fs + HUECO_LEYENDA_EM * fs, ry), txt, fill=S.COLOR_TEXTO, font=fuente)
    ctx = {"marcador": MARCADOR_FIGURA, "mpp": S.MPP_FIGURA, "escala_um": int(L),
           "escala_px": int(round(L / S.MPP_FIGURA))}
    return {"rgb": np.array(img), "nivel": NIVEL_VISTA, "mpp": S.MPP_FIGURA, "contexto": ctx, "cajas": cajas,
            "origen": {"lamina": FIJA, "campo_l0": [campo["x0"], campo["y0"], round(P * f, 1)],
                       "capa": TEXTO["figura"], "disposicion": k % len(DISPOSICIONES)}}


def _odsum(rgb, i0):
    np = _np()
    od = -np.log10(np.clip(rgb.astype(np.float64), 1.0, 255.0) / np.clip(i0, 1.0, None))
    return np.clip(od, 0.0, None).sum(axis=-1)


def _peso(od):
    np = _np()
    return np.clip((od - OD_FONDO) / (OD_TEJIDO - OD_FONDO), 0.0, 1.0)


def _i0_rejilla(lector, op, xs_l0, ys_l0):
    np = _np()
    X, Y = np.meshgrid(xs_l0, ys_l0)
    return np.asarray(lector.i0_local(op)(X, Y), np.float64).reshape(len(ys_l0), len(xs_l0), 3)


def pinta_registro(lector, campo, ent, k=0, geo=None):
    """Superposición A (P-CK19) / B (la móvil, en el marco de P-CK19) con `sinteticos.pinta_registro`."""
    from scipy import ndimage as ndi
    S = _sint()
    np = _np()
    geo = dict(GEOMETRIA, **(geo or {}))
    n, fac, par = campo["lado"], geo["factor_registro"], campo["par"]
    lamA = lector.abre(FIJA)
    _f, mpp8A, _wh = lamA.nivel(fac)
    mppA = float(lamA.mpp_l0)
    dsA = mpp8A / mppA
    x0_l0 = int(round(round(campo["ux0"] / mpp8A) * dsA))
    y0_l0 = int(round(round(campo["uy0"] / mpp8A) * dsA))
    ux0, uy0 = x0_l0 * mppA, y0_l0 * mppA
    rgbA = _lee(lector, FIJA, mpp8A, x0_l0, y0_l0, n, n)
    cen = (np.arange(n) + 0.5) * dsA
    wA = _peso(ndi.gaussian_filter(_odsum(rgbA, _i0_rejilla(lector, FIJA, x0_l0 + cen, y0_l0 + cen)), SIGMA_OD))
    # B: cada píxel de la referencia, con la transformada de su FC verificado (o la global fuera)
    reg, fcs = ent["registros"][par], ent["fcs"]
    PX, PY = np.meshgrid(ux0 + (np.arange(n) + 0.5) * mpp8A, uy0 + (np.arange(n) + 0.5) * mpp8A)
    grupo = np.full((n, n), -1, np.int64)                  # −1: global; g ≥ 0: FC verificado g
    cc = np.floor(PX / fcs["mpp"]).astype(np.int64)
    rr = np.floor(PY / fcs["mpp"]).astype(np.int64)
    Hf, Wf = fcs["mascaras"].shape[1:]
    dentro = (cc >= 0) & (rr >= 0) & (cc < Wf) & (rr < Hf)
    usados = []
    for fid in sorted(reg["verificados"]):
        m = np.zeros((n, n), bool)
        m[dentro] = fcs["mascaras"][fcs["ids"].index(fid)][rr[dentro], cc[dentro]]
        m &= grupo == -1
        if m.any():
            grupo[m] = len(usados)
            usados.append(fid)
    lamB = lector.abre(par)
    _fb, mpp8B, (W8, H8) = lamB.nivel(fac)
    mppB = float(lamB.mpp_l0)
    dsB = mpp8B / mppB
    wB = np.zeros((n, n))
    for g in range(-1, len(usados)):
        sel = grupo == g
        M = reg["global"] if g == -1 else reg["verificados"][usados[g]]
        if not sel.any() or M is None:
            continue
        Mi = np.linalg.inv(np.asarray(M, np.float64))
        u = (Mi[0, 0] * PX[sel] + Mi[0, 1] * PY[sel] + Mi[0, 2]) / mppB / dsB - 0.5   # px ×8 de B
        v = (Mi[1, 0] * PX[sel] + Mi[1, 1] * PY[sel] + Mi[1, 2]) / mppB / dsB - 0.5
        bx0, bx1 = max(0, int(math.floor(u.min())) - 2), min(int(W8), int(math.ceil(u.max())) + 3)
        by0, by1 = max(0, int(math.floor(v.min())) - 2), min(int(H8), int(math.ceil(v.max())) + 3)
        if bx1 <= bx0 or by1 <= by0:
            continue
        rgbB = _lee(lector, par, mpp8B, int(round(bx0 * dsB)), int(round(by0 * dsB)), bx1 - bx0, by1 - by0)
        odB = _odsum(rgbB, _i0_rejilla(lector, par, (bx0 + np.arange(bx1 - bx0) + 0.5) * dsB,
                                       (by0 + np.arange(by1 - by0) + 0.5) * dsB))
        mB = _peso(ndi.gaussian_filter(odB, SIGMA_OD))
        wB[sel] = ndi.map_coordinates(mB, [v - by0, u - bx0], order=1, mode="constant", cval=0.0)
    rgb = np.clip(np.rint(S.pinta_registro(wA, wB)), 0, 255).astype(np.uint8)
    largo, grueso = int(round(100.0 / mpp8A)), max(3, n // 160)
    barra_y = n - int(round(0.03 * n))
    rgb[barra_y - grueso:barra_y, n // 2 - largo // 2:n // 2 - largo // 2 + largo] = S.COLOR_BARRA
    mpp = round(float(mpp8A), 4)
    return {"rgb": rgb, "nivel": NIVEL_REGISTRO, "mpp": mpp, "contexto": {"mpp": mpp},
            "origen": {"lamina": FIJA, "par": par, "fc_verificados": usados,
                       "campo_um_fija": [round(ux0, 1), round(uy0, 1), round(n * mpp8A, 1)],
                       "capa": TEXTO["registro"]}}


PINTA = {"nucleos": pinta_nucleos, "ck19": pinta_ck19, "registro": pinta_registro, "figura": pinta_figura,
         "artefacto": pinta_artefacto}


# ── la orden ────────────────────────────────────────────────────────────────────────────────
def _ronda(n1, huella, man):
    panel = os.path.join(n1, N1_PANEL)
    for f in (PARCIAL, CAPAS_PILOTO):
        d = _lee_json_o_nada(os.path.join(panel, f))
        if isinstance(d, dict) and d.get("huella_sha256") == huella and isinstance(d.get("generacion"), int):
            return d["generacion"]
    usadas = [int(m.group(1)) for k in man for m in [RE_RONDA.match(k)] if m]
    return max(usadas, default=0) + 1


def _escribe_json(exp, ruta, doc):
    texto = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    exp.revisa_json(texto)
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(texto)
    os.replace(tmp, ruta)
    return ruta


def _esqueleto(ronda, huella, sello_sha, geo, cuotas):
    return {"version": VERSION, "generacion": int(ronda), "huella_sha256": huella, "sello_sha256": sello_sha,
            "tareas": {}, "contexto": {}, "siembra": {}, "origen": {},
            "seleccion": {"semilla": "congelacion.json semillas.maestra + desplazamiento",
                          "desplazamiento": dict(DESPLAZAMIENTO), "criterio": CRITERIO,
                          "nota": NOTA_SELECCION, "geometria_px": dict(geo), "cuotas": dict(cuotas),
                          "minimo": MINIMO},
            "rechazadas_puerta": {t: 0 for t in TAREAS}, "faltan": {}}


def _muestra_fija(doc):
    """Todo el texto fijo que puede llevar el JSON (claves y valores que no salen de los datos): el
    esqueleto y un `origen`, `contexto` y `siembra` de muestra con cada clave y cada TEXTO."""
    muestra = {"origen": {t: {"lamina": HE, "par": PARES_REGISTRO, "tarea": t, "campo_l0": [0, 0, 1.0],
                              "campo_um_fija": [0.0, 0.0, 1.0], "fc_verificados": [], "disposicion": 0,
                              "capa": TEXTO.get(t, ""), "detector": [TEXTO["artefacto_gq"], TEXTO["artefacto_sin"]]}
                          for t in TAREAS},
               "contexto": {"marcador": MARCADOR_FIGURA, "mpp": MPP_VISTA, "escala_um": 50, "escala_px": 100},
               "siembra": {"cajas": {e: [0, 0, 1, 1] for e in DISPOSICIONES[0]}},
               "faltan": ["sin_entradas", "sin_campos", "por_debajo_del_minimo"]}
    return {"esqueleto": doc, "muestra": muestra}


def produce(base, exportador=None, lector=None, geo=None, cuotas=None, log=print):
    """La orden `capas-piloto`. Devuelve {"rc", "doc", "ruta"}; FaltaEntrada y CapaAjena suben."""
    np = _np()
    import laminillas_comun as C
    lector = _lector(lector)
    exp = exportador if exportador is not None else PuertaN1Png()
    try:
        pl = planifica(base, lector, geo, cuotas)
    except FaltaSello as e:
        log("🛑 sin sello válido de la congelación (0): %s" % e)
        return {"rc": 5, "motivo": str(e)}
    geo, cuotas, ent, sello = pl["geo"], pl["cuotas"], pl["ent"], pl["sello"]
    laminas = C.lee_manifiesto(base).get("laminas") or {}
    # La huella: lo que cambia QUÉ píxeles salen. Cuotas y MINIMO no (el orden sellado con un límite
    # mayor empieza igual): subir la cuota completa la misma generación.
    extra = {"version": VERSION, "geo": geo, "criterio": CRITERIO,
             "desplazamiento": DESPLAZAMIENTO, "puerta": exp.version(),
             "od": [OD_FONDO, OD_TEJIDO, SIGMA_OD], "marcador": MARCADOR_FIGURA,
             "huecos": [HUECO_UNIDAD_EM, HUECO_LEYENDA_EM],
             "laminas": {op: (laminas.get(op) or {}).get("sha256") for op in sorted(ent["laminas"])}}
    huella = _huella_entradas(base, ent["rels"], extra)
    n1 = exp.n1_dir()
    ronda = _ronda(n1, huella, exp.manifiesto())
    doc = _esqueleto(ronda, huella, sello.sha256, geo, cuotas)
    exp.revisa_json(json.dumps(_muestra_fija(doc), ensure_ascii=False, sort_keys=True))  # antes de exportar
    _escribe_json(exp, os.path.join(n1, N1_PANEL, PARCIAL),
                  {"version": VERSION, "generacion": int(ronda), "huella_sha256": huella})
    for clave, motivo in sorted(ent["motivos"].items()):
        log("  aviso, %s: %s" % (clave, motivo))
    for t in TAREAS:
        if t in pl["faltan"]:
            log("⛔ %s: sin capas · %s" % (t, pl["faltan"][t]))
            doc["faltan"][t] = "sin_entradas" if t in ent["faltan"] else "sin_campos"
            continue
        aceptadas, seguidos = [], 0
        for campo in pl["plan"][t]:
            if len(aceptadas) >= cuotas[t]:
                break
            if seguidos >= RECHAZOS_SEGUIDOS_MAX:
                raise PuertaSistemica("%s: la Puerta rechazó %d campos seguidos: no es el campo, es otra "
                                      "cosa (OCR, trazos, el pintado): paro" % (t, seguidos))
            capa = PINTA[t](lector, campo, ent, len(aceptadas), geo)
            datos = exp.canonico(capa["rgb"])
            opaco, nombre = nombre_capa(t, ronda, len(aceptadas) + 1, capa["nivel"], capa["mpp"])
            man = exp.manifiesto()
            if nombre in man:                       # reanudación: vale si sus PÍXELES son estos
                if not np.array_equal(exp.lee(nombre), capa["rgb"]):
                    raise CapaAjena("%s ya está en N1 con otros píxeles (generación %d): no sobrescribo ni "
                                    "reutilizo" % (nombre, ronda))
                log("  %s: %s ya estaba en N1 (mismos píxeles)" % (t, nombre))
            else:
                try:
                    escrito, _sha = exp.exporta(datos, opaco, capa["nivel"], capa["mpp"])
                except Exception as e:                                   # noqa: BLE001
                    if not exp.es_rechazo(e):
                        raise
                    doc["rechazadas_puerta"][t] += 1
                    seguidos += 1
                    log("  🛑 %s: un campo no pasa la Puerta (%s): no entra; sigo con el siguiente"
                        % (t, str(e)[:200]))
                    continue
                if escrito != nombre:
                    raise CapaAjena("exporta_n1 escribió %s y esperaba %s" % (escrito, nombre))
            seguidos = 0
            if not np.array_equal(exp.lee(nombre), capa["rgb"]):
                raise CapaAjena("%s: los píxeles en N1 no son los pintados" % nombre)
            aceptadas.append(nombre)
            doc["contexto"][nombre] = capa["contexto"]
            doc["origen"][nombre] = dict(capa["origen"], tarea=t)
            if t == "figura":
                doc["siembra"][nombre] = {"cajas": capa["cajas"]}
        if aceptadas:
            doc["tareas"][t] = aceptadas
        if len(aceptadas) < MINIMO:
            doc["faltan"][t] = "por_debajo_del_minimo"
        log("%s %s: %d capas (cuota %d, mínimo %d; %d rechazadas por la Puerta; %d campos candidatos)" % (
            "✅" if len(aceptadas) >= MINIMO else "⚠️", t, len(aceptadas), cuotas[t], MINIMO,
            doc["rechazadas_puerta"][t], len(pl["plan"][t])))
    if not doc["tareas"]:
        log("🛑 ninguna tarea con capas: no escribo %s" % CAPAS_PILOTO)
        return {"rc": 4, "doc": doc, "ruta": None}
    ruta = _escribe_json(exp, os.path.join(n1, N1_PANEL, CAPAS_PILOTO), doc)
    try:
        os.remove(os.path.join(n1, N1_PANEL, PARCIAL))
    except OSError:
        pass
    rc = 4 if doc["faltan"] else 0
    log("%s %s · generación %d · %d capas%s" % ("✅" if rc == 0 else "⚠️", ruta, ronda,
                                          sum(len(v) for v in doc["tareas"].values()),
                                          "" if rc == 0 else " · faltan: %s" % ", ".join(sorted(doc["faltan"]))))
    return {"rc": rc, "doc": doc, "ruta": ruta}


def main_ventanilla(argv=()):
    """`procesa laminillas_exporta -- capas-piloto` (la llama `laminillas_exporta.main`)."""
    if argv:
        print("uso: procesa laminillas_exporta -- capas-piloto", file=sys.stderr)
        return 2
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("🛑 solo corre por la ventanilla", file=sys.stderr)
        return 2
    P = _proc()
    try:
        r = produce(os.getcwd())
    except P.FaltaEntrada as e:
        print("🛑 falta un producto previo: %s" % e)
        return 5
    except (CapaAjena, PuertaSistemica) as e:
        print("🛑 %s" % e)
        return 3
    return r["rc"]
