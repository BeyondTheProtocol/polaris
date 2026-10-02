#!/usr/bin/env python3
"""tools/laminillas_sello.py — leer (y verificar) el sello de la congelación (0) del piloto.

Plan «laminillas DFCI», Puerta del piloto, punto 3: «Todo se sella junto (fecha, sha256) y la
extensión reutiliza el sello». El sello lo ESCRIBE `laminillas_congela.congela` (tipo
«congelacion-tanda»: JSON plano con su `sha256` = sha256 del JSON canónico de todo lo demás —
claves ordenadas, sin espacios, UTF-8, sin NaN— y una `fecha` AAAA-MM-DD que da quien congela).
Este módulo lo LEE para todo el código de medida (segmentación, registro, métricas) con la misma
regla de sha256, y es la puerta: sin sello, o con un sello al que le falta alguna sección
(`SECCIONES_BASE`; registro y métricas piden `SECCIONES_MODULO_B`), no se mide ninguna lámina diana
(`exige`). Las de suelo (P-HER2NEG, P-HER2) sí se miden antes: es la pre-congelación.

Régimen: `umbral(lamina, regimen=…)` lee el T, la banda, los rótulos («high floor» incluido, de
`umbral.por_regimen`) y el T del vector propio (`T_propio_por_regimen`) del régimen pedido, para
pasar a reglas clásicas tras el chequeo de GrandQC en P-KI67 sin recongelar.

Lo que lee el módulo B (registro, métricas) del sello:
  · `vectores` (H, DAB, tercero) y `residuo.por_lamina[<lámina>].dab_propio` si la lámina lleva
    vector DAB propio;
  · `umbral`: T, banda y rótulos por compartimento («nucleo», «anillo»), y el p99,9 del régimen
    que rige; `residuo.por_lamina[<lámina>].T_propio` si existe;
  · `fp_her2[compartimento]` = {k, n, fraccion, ic95};
  · `modulo_b` = {"registro": {...}, "metricas": {...}}: los parámetros finos del registro y de las
    métricas (`laminillas_metricas.contenido_partida()`), cotejados con `registro`, `hotspot` y
    `regla_L` del mismo sello: si no casan, el sello no vale para medir.

`sella` escribe ese mismo formato (lo usa `laminillas_congela.congela`, y los tests); nunca pisa un
sello existente. Recongelación: una sola vez, con el sha256 del anterior y la causa, y la causa
solo puede ser un error de ejecución (`CAUSAS_RECONGELA`: lector, I0, segmentador; plan, punto 3:
«Otra causa: no se recongela»; y regla_artefacto, el error de implementación de la regla de
artefacto «saturados no H/DAB» que la actualización 19 del plan, 2-oct, declara error de
ejecución y manda recongelar).

RUTA CANÓNICA ÚNICA: el sello de la tanda se llama siempre `congelacion.json` (`FICHERO`) y, por la
ventanilla (BTP_VENTANILLA=1, cwd = SESION), vive en SESION. `exige` solo acepta ese fichero, vuelto
a leer de disco (un `Sello` en memoria no basta), con su sha256; un sello recongelado exige además
su anterior archivado (`congelacion.anterior-<sha12>.json`, como lo deja `congela.recongela`) con
el sha256 que cita y sin otra recongelación detrás. Un segundo sello «primero» en la misma
carpeta, junto a un anterior archivado, no se escribe. [inferido: con la ventanilla fuera, en los
tests, la carpeta es libre; el nombre, no.]

Solo stdlib.
"""
import glob
import hashlib
import json
import os
import re

TIPO = "congelacion-tanda"
FICHERO = "congelacion.json"
ARCHIVO_ANTERIOR = "congelacion.anterior-%s.json"
# regla_artefacto: actualización 19 del plan (DAB recortado contado como artefacto no H/DAB)
CAUSAS_RECONGELA = ("lector", "I0", "segmentador", "regla_artefacto")
LAMINAS_SUELO = frozenset({"P-HER2NEG", "P-HER2"})
T_ALTO = 0.25                      # plan (0): «Si >0,25 … rótulo «high floor»»
_RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Secciones de un sello de congelación (0) completo. Las BASE son las que exige toda medida de
# células (= `laminillas_segmenta.SECCIONES_SELLO`, la guardia del módulo A); el registro y las
# métricas necesitan además las suyas (T y tasa de P-HER2, sus parámetros y los que cotejan).
SECCIONES_BASE = ("umbral", "vectores", "residuo", "precongelacion", "parametros")
SECCIONES_MODULO_B = SECCIONES_BASE + ("fp_her2", "registro", "hotspot", "regla_L", "modulo_b",
                                       "medida", "semillas")

# Compartimento de medida por lámina (plan, F3: nucleares → núcleo; citoplasmáticos y HER2 → anillo).
COMPARTIMENTO = {"P-KI67": "nucleo", "P-RE": "nucleo", "P-RP": "nucleo", "P-RA": "nucleo",
                 "P-{{DIANA3}}": "nucleo", "P-P63": "nucleo", "P-SYN": "anillo", "P-CHGA": "anillo",
                 "P-CK19": "anillo", "P-HER2": "anillo", "P-HER2NEG": "anillo",
                 "P-AE1AE3": "anillo"}


class SelloAusente(RuntimeError):
    """Se pidió medir una lámina diana sin sello."""


class SelloInvalido(RuntimeError):
    """El sello existe pero no casa (sha256, tipo, fecha o sección que falta)."""


def sha256_de(obj):
    """La misma regla que `laminillas_congela.sha256_de`."""
    c = json.dumps({k: v for k, v in obj.items() if k != "sha256"}, sort_keys=True,
                   separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(c.encode("utf-8")).hexdigest()


class Sello:
    """Sello verificado. Solo se construye por `carga` o `sella`."""

    def __init__(self, d, ruta=None):
        self.d = d
        self.fecha = d["fecha"]
        self.sha256 = d["sha256"]
        self.anterior = d.get("anterior")
        self.ruta = ruta

    @property
    def contenido(self):
        return self.d

    def seccion(self, nombre):
        s = self.d.get(nombre)
        if not isinstance(s, dict):
            raise SelloInvalido("el sello %s… no tiene la sección «%s»" % (self.sha256[:12], nombre))
        return s

    def modulo_b(self, parte):
        mb = self.d.get("modulo_b")
        if not isinstance(mb, dict) or not isinstance(mb.get(parte), dict):
            raise SelloInvalido("el sello %s… no trae los parámetros del módulo B («modulo_b.%s»): "
                                "la congelación debe incluir laminillas_metricas.contenido_partida()"
                                % (self.sha256[:12], parte))
        return mb[parte]

    # ── umbral ────────────────────────────────────────────────────────────────────────────
    def _por_lamina(self, lamina):
        return ((self.d.get("residuo") or {}).get("por_lamina") or {}).get(lamina) or {}

    def regimen_que_rige(self):
        return self.seccion("umbral").get("rige")

    def umbral(self, lamina, compartimento=None, regimen=None):
        """{T, banda, p999, rige_T, regimen, rotulos, vector_propio} de una lámina y compartimento.

        `regimen` (None = el que rige): el plan sella los dos T (GrandQC y reglas clásicas) para
        que el chequeo de GrandQC en P-KI67 (punto 5) pueda pasar a reglas clásicas SIN recongelar;
        tiene que ser uno de `umbral.regimenes`."""
        comp = compartimento or COMPARTIMENTO.get(lamina)
        if comp not in ("nucleo", "anillo"):
            raise SelloInvalido("compartimento desconocido para %s" % lamina)
        u = self.seccion("umbral")
        regs = u.get("regimenes") or {}
        rige = u.get("rige")
        if regimen is not None and regimen not in regs:
            raise SelloInvalido("régimen «%s» no sellado (sellados: %s)"
                                % (regimen, ", ".join(sorted(regs)) or "ninguno"))
        regimen = regimen or rige
        reg = (regs.get(regimen) or {}).get(comp, {})
        try:
            if regimen == rige:
                T = float(u["T"][comp])
                banda = [float(x) for x in u["banda"][comp]]
            else:
                T = float(reg["T"])
                banda = [float(x) for x in reg["banda"]]
        except (KeyError, TypeError, ValueError):
            raise SelloInvalido("el sello no trae T/banda del compartimento «%s» (régimen %s)"
                                % (comp, regimen))
        # «high floor» y rótulos de tanda POR RÉGIMEN (`umbral.por_regimen`, como lo sella
        # congela): si tras el chequeo de GrandQC rige el otro, hereda los suyos, no los del sellado
        pr = (u.get("por_regimen") or {}).get(regimen)
        rot_tanda = (pr.get("rotulos") if isinstance(pr, dict)
                     else (u.get("rotulos") if regimen == rige else [])) or []
        alto = ("high floor" in rot_tanda or bool((pr or {}).get("high_floor"))
                or reg.get("estado") == "alto" or T > T_ALTO)
        out = {"T": T, "banda": banda, "p999": reg.get("p999"), "rige_T": reg.get("rige"),
               "regimen": regimen, "rige_sellado": rige,
               "rotulos": list(reg.get("rotulos") or []),
               "compartimento": comp, "vector_propio": False}
        pl = self._por_lamina(lamina)
        if regimen == rige:
            propio = pl.get("T_propio")
        else:
            propio = (pl.get("T_propio_por_regimen") or {}).get(regimen)
            if propio is None and pl.get("T_propio") is not None:
                raise SelloInvalido("%s lleva vector DAB propio pero el sello no trae su T del "
                                    "régimen «%s» (T_propio_por_regimen)" % (lamina, regimen))
        if isinstance(propio, dict) and isinstance(propio.get(comp), dict):
            pc = propio[comp]
            out.update({"T": float(pc["T"]), "banda": pc.get("banda", banda),
                        "p999": pc.get("p999"), "rige_T": pc.get("rige"), "vector_propio": True})
            out["rotulos"] = list(pc.get("rotulos") or []) + ["slide-specific DAB vector"]
            # el «high floor» de la tanda no se pierde con el vector propio, y un T propio > 0,25
            # también lo lleva (plan (0): banda obligatoria en toda cifra)
            alto = alto or pc.get("estado") == "alto" or float(pc["T"]) > T_ALTO
        if alto and "high floor" not in out["rotulos"]:
            out["rotulos"].append("high floor")
        out["banda_obligatoria"] = bool(alto)
        return out

    def fp_her2(self, compartimento, regimen=None, extremo="T"):
        """Tasa de falsos positivos de P-HER2 {k, n, fraccion, ic95} medida en el umbral `extremo`
        («T», «T_bajo» o «T_alto» de la banda) del `regimen` (None = el que rige).

        Lo que `laminillas_congela` sella hoy es solo `fp_her2[comp]` (T del régimen que rige).
        Los extremos de la banda se leen de `fp_her2[comp]["banda"][extremo]` y los otros
        regímenes de `fp_her2_regimenes[regimen][comp]`; si no están, `None` (extremos) o
        SelloInvalido (régimen): no se inventa una tasa que no se midió."""
        rige = self.regimen_que_rige()
        if regimen is None or regimen == rige:
            fp = (self.d.get("fp_her2") or {}).get(compartimento)
        else:
            fp = ((self.d.get("fp_her2_regimenes") or {}).get(regimen) or {}).get(compartimento)
            if fp is None:
                raise SelloInvalido("el sello no trae la tasa de P-HER2 del régimen «%s» (solo la "
                                    "del que rige, «%s»): pedírsela a la congelación" % (regimen, rige))
        if not isinstance(fp, dict) or "ic95" not in fp:
            raise SelloInvalido("el sello no trae la tasa de P-HER2 del compartimento «%s»"
                                % compartimento)
        if extremo == "T":
            return fp
        if extremo not in ("T_bajo", "T_alto"):
            raise ValueError("extremo: T, T_bajo o T_alto")
        b = (fp.get("banda") or {}).get(extremo)
        return b if isinstance(b, dict) and "ic95" in b else None

    def vectores(self, lamina=None):
        v = self.seccion("vectores")
        out = {"H": v["H"], "DAB": v["DAB"], "tercero": v.get("tercero")}
        propio = self._por_lamina(lamina).get("dab_propio") if lamina else None
        if propio:
            out = {"H": v["H"], "DAB": propio, "tercero": None}
        return out

    def cita(self):
        """Lo que va a Métodos y al registro de ejecución de cada cifra."""
        return {"sello_sha256": self.sha256, "sello_fecha": self.fecha,
                "recongelado": bool(self.anterior)}


def _sesion_ventanilla():
    """SESION si se corre por la ventanilla (cwd = SESION); si no, None (tests)."""
    if os.environ.get("BTP_VENTANILLA") == "1":
        return os.path.realpath(os.getcwd())
    return None


def _comprueba_ruta(ruta):
    """El sello de tanda solo vive en `<SESION>/congelacion.json`."""
    if os.path.basename(str(ruta)) != FICHERO:
        raise SelloInvalido("el sello de la tanda solo se llama «%s» (ruta canónica única)" % FICHERO)
    ses = _sesion_ventanilla()
    if ses is not None and os.path.realpath(os.path.dirname(os.path.abspath(ruta))) != ses:
        raise SelloInvalido("por la ventanilla, el sello solo vive en SESION")


def _anteriores_archivados(carpeta):
    return sorted(glob.glob(os.path.join(carpeta, ARCHIVO_ANTERIOR % "*")))


def sella(ruta, contenido, fecha, anterior=None, causa=None):
    """Escribe un sello «congelacion-tanda» (formato de `laminillas_congela`) en la ruta canónica
    (`<carpeta>/congelacion.json`). Nunca pisa uno existente. `anterior` (Sello) solo para la
    recongelación única; exige `causa` ∈ CAUSAS_RECONGELA. Sin `anterior`, no se escribe un
    segundo sello «primero» donde ya hay un anterior archivado."""
    if not isinstance(fecha, str) or not _RE_FECHA.match(fecha):
        raise SelloInvalido("fecha AAAA-MM-DD obligatoria (la da quien congela)")
    if not isinstance(contenido, dict) or not contenido:
        raise SelloInvalido("contenido del sello vacío")
    _comprueba_ruta(ruta)
    carpeta = os.path.dirname(os.path.abspath(ruta))
    d = dict(contenido)
    d.update({"tipo": TIPO, "fecha": fecha})
    if anterior is not None:
        if anterior.anterior:
            raise SelloInvalido("ya se recongeló una vez (plan: «una vez»); esto va a incertidumbres")
        if causa not in CAUSAS_RECONGELA:
            raise SelloInvalido("recongelación solo por error de ejecución (%s); otra causa no "
                                "recongela, va a incertidumbres" % ", ".join(CAUSAS_RECONGELA))
        d["anterior"] = {"sha256": anterior.sha256, "causa": causa}
        d["recongelado"] = True
    else:
        if causa is not None:
            raise SelloInvalido("`causa` solo acompaña a una recongelación (con `anterior`)")
        if _anteriores_archivados(carpeta):
            raise SelloInvalido("ya hay un sello anterior archivado en esta SESION: un sello "
                                "«primero» nuevo no se escribe (solo cabe la recongelación única)")
        d["anterior"] = None
        d.pop("recongelado", None)
    d.pop("sha256", None)
    d["sha256"] = sha256_de(d)
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), mode=0o700, exist_ok=True)
    with open(ruta, "x", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False)
    return Sello(d, ruta)


def carga(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        raise SelloAusente("no hay sello en %s" % os.path.basename(str(ruta)))
    except (OSError, ValueError) as e:
        raise SelloInvalido("sello ilegible (%s)" % type(e).__name__)
    if not isinstance(d, dict) or d.get("tipo") != TIPO:
        raise SelloInvalido("no es un sello de congelación («%s»)" % TIPO)
    if "sha256" not in d or not _RE_FECHA.match(str(d.get("fecha", ""))):
        raise SelloInvalido("sello sin sha256 o sin fecha AAAA-MM-DD")
    try:
        bueno = sha256_de(d)
    except ValueError:
        raise SelloInvalido("sello con NaN o infinito")
    if bueno != d["sha256"]:
        raise SelloInvalido("sha256 del sello no casa: se tocó después de sellar")
    return Sello(d, ruta)


def _comprueba_cadena(s):
    """Un sello recongelado lleva su anterior archivado, íntegro y sin recongelación propia."""
    ant = s.anterior
    if not ant:
        return
    if not isinstance(ant, dict) or ant.get("causa") not in CAUSAS_RECONGELA:
        raise SelloInvalido("recongelación sin causa de ejecución (%s)" % ", ".join(CAUSAS_RECONGELA))
    carpeta = os.path.dirname(os.path.abspath(s.ruta))
    archivo = os.path.join(carpeta, ARCHIVO_ANTERIOR % str(ant.get("sha256", ""))[:12])
    try:
        previo = carga(archivo)
    except SelloAusente:
        raise SelloInvalido("sello recongelado sin su anterior archivado")
    if previo.sha256 != ant.get("sha256") or previo.anterior:
        raise SelloInvalido("la cadena de recongelación no casa (una sola, con el sha256 citado)")


def faltan_secciones(sello, secciones):
    """Secciones (objetos JSON) que faltan en un sello."""
    return [k for k in secciones if not isinstance(sello.d.get(k), dict)]


def exige(sello, laminas, secciones=SECCIONES_BASE):
    """Devuelve el Sello verificado (acepta Sello, ruta o None). Si no hay sello y alguna de
    `laminas` es diana (no de suelo), lanza SelloAusente: no se mide.

    Solo vale el sello de la ruta canónica (`congelacion.json`; por la ventanilla, el de SESION),
    vuelto a leer de disco aunque llegue como `Sello`, con su cadena de recongelación y COMPLETO:
    todas las `secciones` (por defecto las de la congelación (0) que exige también la guardia del
    módulo A; el registro y las métricas piden `SECCIONES_MODULO_B`). Incompleto: SelloInvalido."""
    if sello is not None:
        ruta = sello.ruta if isinstance(sello, Sello) else sello
        if ruta is None:
            raise SelloInvalido("un Sello sin fichero no vale: el código de medida relee el sello")
        _comprueba_ruta(ruta)
        try:
            leido = carga(ruta)
        except SelloAusente:
            leido = None
        if leido is not None:
            if isinstance(sello, Sello) and leido.sha256 != sello.sha256:
                raise SelloInvalido("el Sello en memoria no es el del fichero")
            _comprueba_cadena(leido)
            faltan = faltan_secciones(leido, secciones)
            if faltan:
                raise SelloInvalido("sello %s… incompleto (faltan %s): no es una congelación (0) "
                                    "completa" % (leido.sha256[:12], ", ".join(faltan)))
        sello = leido
    dianas = sorted(set(laminas) - LAMINAS_SUELO)
    if sello is None and dianas:
        raise SelloAusente("sin sello no mido láminas diana (%s): primero la congelación (0)"
                           % ", ".join(dianas))
    return sello
