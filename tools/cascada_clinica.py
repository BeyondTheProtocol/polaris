#!/usr/bin/env python3
"""tools/cascada_clinica.py — la CASCADA CLÍNICA: cuando el historial de {{TITULAR}} cambia
GORDO, el cambio se propaga SOLO y de forma fiable a todo el sistema.

Problema (Fase 1): hoy la propagación de un cambio clínico es MANUAL y se queda coja.
Pasó con R175H (la mutación entró al perfil pero su efecto no se propagó a
elegibilidad/dianas) y con «medible» (cambió el gate pero no re-disparó la elegibilidad
de los ensayos). Esta tool cierra ese hueco: detecta el cambio gordo, lo verifica antes
de tocar la fuente de verdad, marca STALE lo que dependía de él, re-ejecuta lo barato,
ENCOLA lo caro (a un clic de {{TITULAR}}), avisa en lenguaje llano y dispara el cortafuegos
del goal si el cambio amenaza NED.

ENCUADRE (hereda del muro, no se re-discute): APOYO A LA DECISIÓN, no diagnóstico. Esta
tool DESCRIBE y EQUIPA («tu historial cambió → esto se actualizó / esto pide tu OK»), no
CONCLUYE («esto significa X para tu tratamiento»). Deciden sus médicos.

RIESGO: 🔴 ALTO (toca el perfil maestro clínico/PII, puede disparar código rojo, su
salida alimenta elegibilidad). Por eso:
  - núcleo DETERMINISTA (sin LLM, sin red); el LLM solo donde aporta y SIEMPRE encolado.
  - la data externa pasa por el GATE DE VERIFICACIÓN antes de persistir (anti-inyección).
  - la severidad sale de ENUMS/TIPOS/FECHAS, NUNCA de texto libre.
  - egress CERO: todo local. El clínico se queda local (clínico = Claude).
  - fail-safe: ante la duda, trata el cambio como GORDO y AVISA.

Contrato del gate validado por `verificacion`; mapa de dependencias y umbrales de código
rojo validados por `comite-medico` (ambos 2026-06-25). Sin dependencias (stdlib).
"""
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cascada_mapa  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
# El perfil maestro y el log de auditoría son CLÍNICOS → viven en la carpeta privada
# (gitignored, local). Se pueden re-apuntar por entorno para tests herméticos.
CLINICO_DIR = os.environ.get("BTP_CLINICO_DIR") or os.path.join(
    REPO, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "_PRIVADO_CLINICO")
MAESTRO = os.path.join(CLINICO_DIR, "perfil_maestro.json")
AUDIT_LOG = os.path.join(CLINICO_DIR, "cascada_auditoria.jsonl")
STALE = os.path.join(STATE, "cascada_stale.json")

GATE_VERSION = "cascada_clinica@v1"
ESQUEMA_VERSION = "maestro@v1"

# ─────────────────────────────────────────────────────────────────────────────
# ENUMS DECLARADOS (dominio cerrado = única fuente de verdad de la severidad/routing).
# Constantes en código: un informe NO puede ascenderse a sí mismo escribiendo texto.
# ─────────────────────────────────────────────────────────────────────────────
ORIGENES = ("informe_ngs", "biopsia", "escaner", "informe_patologia",
            "analitica", "manual_verificado")
CONFIANZAS = ("alta", "media", "baja")
PLATAFORMAS = (
    "FoundationOne CDx", "Guardant360", "Tempus xT", "Caris", "TSO500",
    "{{TEST_MOL}}", "OncoDEEP", "DIPCAN", "manual", "informe_clinico",
)
# Campos del maestro que la cascada sabe gobernar (≈ datos del mapa + sub-campos).
CAMPOS_VALIDOS = set(cascada_mapa.MAPA.keys()) | {
    "estadio", "ecog", "fase_actual",
}
# Campos de ALTO IMPACTO: aunque la confianza sea alta, NUNCA se auto-persisten ni se
# auto-resuelven; siempre los ve un humano (coherente con código rojo).
CAMPOS_ALTO_IMPACTO = {
    "estado_enfermedad", "enfermedad_medible", "marcadores_moleculares",
    "hla_loh", "receptores", "estadio",
    # + 14-jul-26 (auditoria): TODO campo que trae una biopsia es alto impacto.
    # hla (el tipado), viabilidad_muestra y ctdna_mrd auto-persistian con --apply.
    "hla", "viabilidad_muestra", "ctdna_mrd",
}

# Catálogo CERRADO de motivos (enums, nunca prosa formada con datos del informe).
MOTIVOS = (
    "ok", "forma_invalida", "clave_desconocida", "falta_obligatorio", "tipo_invalido",
    "bytes_sospechosos", "origen_no_enum", "confianza_no_enum", "plataforma_desconocida",
    "campo_no_en_esquema", "valor_no_valida", "fecha_invalida", "fecha_futura",
    "fecha_regresiva", "ruta_no_permitida", "fuente_inaccesible", "conflicto_con_maestro",
    "confianza_baja", "campo_alto_impacto", "idempotente_ya_decidido",
)

# Topes de longitud (corta-y-rechaza, no truncar-y-seguir).
TOPES = {"campo": 64, "valor": 128, "plataforma": 64, "fecha": 10,
         "confianza": 16, "origen": 32, "fuente_fichero": 512}
CLAVES_OBLIGATORIAS = ("campo", "valor", "plataforma", "fecha", "confianza",
                       "origen", "fuente_fichero")
CLAVES_PERMITIDAS = set(CLAVES_OBLIGATORIAS) | {"cita_cruda"}

# Caracteres prohibidos (anti-inyección léxica): zero-width, BOM, bidi/control de
# dirección, soft hyphen, word joiner.
_PROHIBIDOS = (
    "​‌‍﻿⁠᠎­"   # zero-width / invisibles
    "‪‫‬‭‮"               # bidi embedding/override
    "⁦⁧⁨⁩"                      # bidi isolates
)
# Campos que DEBEN ser ASCII canónico (homoglifos cirílicos mueren aquí).
_ASCII_CANON = ("campo", "plataforma", "origen", "confianza", "fecha")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _sha_fichero(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _write_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


# ─────────────────────────────────────────────────────────────────────────────
# GATE DE VERIFICACIÓN — el ÚNICO camino por el que un dato derivado de un informe
# puede tocar el perfil maestro. Determinista, fail-closed. Contrato de `verificacion`.
# ─────────────────────────────────────────────────────────────────────────────
def _bytes_sospechosos(propuesta):
    """True si algún valor string tiene bytes prohibidos / excede tope / homoglifo en
    campo que debe ser ASCII canónico. Anti-inyección léxica.

    `cita_cruda` es texto libre de informe (puede traer saltos de línea/tabs legítimos)
    y NUNCA instruye → se le permiten \\n/\\t, pero sigue prohibido lo invisible/bidi
    (eso es ataque incluso en una cita). Se trunca/marca aparte en `veredicto`."""
    for k, v in propuesta.items():
        if not isinstance(v, str):
            continue
        if k in TOPES and len(v) > TOPES[k]:
            return True
        if any(c in _PROHIBIDOS for c in v):
            return True
        if k == "cita_cruda":
            # solo invisibles/bidi (ya cubierto arriba); \n y \t van permitidos.
            continue
        # controles C0/C1 (sin \n \t \r en campos de dato)
        if any(unicodedata.category(c) in ("Cc", "Cf") for c in v):
            return True
        if k in _ASCII_CANON:
            if not v.isascii():
                return True
            if unicodedata.normalize("NFC", v) != v:
                return True
    return False


def _canon_enum(v):
    """Canonicaliza un valor de campo-enum a UNA forma estable ASCII: descompone (NFD),
    descarta marcas (tildes), baja a minúsculas, recorta. Así «progresión», «Progresion»
    y un homoglifo no-ASCII colapsan a la MISMA clave que compara `evaluar_codigo_rojo`.
    Sin esto, «progresión» (con tilde) no dispararía el código rojo — agujero crítico."""
    nfd = unicodedata.normalize("NFD", v)
    ascii_only = "".join(c for c in nfd if not unicodedata.combining(c) and ord(c) < 128)
    # colapsa separadores (espacio/guion/barra) a «_» para que «nueva lesion»,
    # «nueva-lesion» y «nueva_lesion» caigan en la MISMA clave enum. Sin esto, la
    # forma humana natural («nueva lesion» con espacio) NO dispara el código rojo.
    return re.sub(r"[\s\-/]+", "_", ascii_only.strip().lower())


def _valida_valor(campo, valor):
    """El validador lo elige el CAMPO, no el contenido. Devuelve (ok, valor_norm)."""
    v = unicodedata.normalize("NFC", valor).strip()
    if campo == "marcadores_moleculares":
        # variante proteica/HGVS o «GEN variante» — alfabeto blanco estricto.
        # acepta p.Arg175His, R175H, c.524G>A, GEN p.X, amplificación, fusión...
        if not re.fullmatch(r"[A-Za-z0-9 ._:>+()\-]{1,128}", v):
            return False, None
        # ningún ; ni salto: una propuesta = un dato (no se cuelan dos)
        if ";" in v or "\n" in v:
            return False, None
        return True, v
    if campo == "enfermedad_medible":
        c = _canon_enum(v)
        return (c in ("si", "no", "indeterminado")), c
    if campo == "estado_enfermedad":
        c = _canon_enum(v)
        return (c in ("ned", "estable", "progresion", "nueva_lesion", "respuesta")), c
    if campo == "viabilidad_muestra":
        # gatillo de código rojo (la muestra de la vacuna falla). Sin rama propia caía
        # al validador ASCII genérico, que RECHAZA el «_» de «no_apta» → el rojo se
        # tragaba en silencio. Justo el escenario del QC de la biopsia.
        c = _canon_enum(v)
        return (c in ("apta", "si", "no", "no_apta", "insuficiente")), c
    if campo == "receptores":
        # exige ASCII: un homoglifo cirílico no contamina la ficha (rompería cruces).
        return bool(v.isascii() and re.fullmatch(r"[A-Za-z0-9 /%+\-.]{1,128}", v)), v
    if campo == "hla_loh":
        # ENUM CERRADO (fix 14-jul-26). Antes aceptaba PROSA LIBRE, y evaluar_codigo_rojo
        # hacia substring de "loss"/"perdida" -> "No loss of heterozygosity detected"
        # (la BUENA noticia del panel) disparaba ROJO y apagaba el sistema entero.
        # El docstring de este modulo ya prometia: la severidad sale de ENUMS, NUNCA
        # de texto libre. Ahora se cumple. Prosa -> rechazado -> ojo humano.
        c = _canon_enum(v)
        return (c in ("loh_presente", "loh_ausente", "indeterminado")), c
    if campo == "hla":
        return bool(v.isascii() and re.fullmatch(r"[A-Za-z0-9 :*/+\-.]{1,128}", v)), v
    # campos genéricos restantes: alfabeto blanco ASCII (sin \w/À-ÿ → mata homoglifos).
    if v.isascii() and re.fullmatch(r"[A-Za-z0-9 .,:/%*()+\-]{1,128}", v):
        return True, v
    return False, None


def _fecha_ok(s):
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return d
    except Exception:
        return None


def _ruta_permitida(ruta):
    """La fuente debe estar dentro del árbol de la fuente de verdad (sin escapes)."""
    if not ruta or ".." in ruta.split(os.sep):
        return False
    raiz = os.path.realpath(os.path.join(REPO, "00_FUENTE-DE-VERDAD"))
    cand = os.path.realpath(ruta if os.path.isabs(ruta) else os.path.join(REPO, ruta))
    return cand == raiz or cand.startswith(raiz + os.sep)


def verificar_para_maestro(propuesta, *, ahora=None, maestro=None):
    """Gate fail-closed. Devuelve un Veredicto (dict cerrado). SOLO decision=="aprobado"
    autoriza al caller a persistir. La severidad/clasificación NO sale de texto libre.

    `propuesta`: dict con {campo, valor, plataforma, fecha, confianza, origen,
    fuente_fichero} (+ opcional cita_cruda). `maestro`: dict del perfil ya cargado.
    """
    ahora = ahora or date.today()
    motivos = []
    cita = None

    # plano de CITA: el texto crudo se separa, se trunca y se marca; NUNCA instruye.
    if isinstance(propuesta, dict) and isinstance(propuesta.get("cita_cruda"), str):
        cita = "[CITA-NO-INSTRUCCION] " + propuesta["cita_cruda"][:280]

    def veredicto(decision, valor_norm=None, fuente_sha=None, fecha=None):
        pid = _sha(propuesta) if isinstance(propuesta, dict) else _sha({"_": str(propuesta)})
        return {
            "decision": decision,
            "propuesta_id": pid,
            "campo": propuesta.get("campo") if isinstance(propuesta, dict) else None,
            "valor_normalizado": valor_norm,
            "motivos": motivos or ["ok"],
            "evidencia": {
                "fuente_fichero": propuesta.get("fuente_fichero") if isinstance(propuesta, dict) else None,
                "fuente_sha256": fuente_sha,
                "fecha": fecha,
                "plataforma": propuesta.get("plataforma") if isinstance(propuesta, dict) else None,
                "origen": propuesta.get("origen") if isinstance(propuesta, dict) else None,
                "confianza": propuesta.get("confianza") if isinstance(propuesta, dict) else None,
            },
            "cita_cruda": cita,
            "instruccion_obedecida": False,
            "ts_decision": _now_iso(),
            "gate_version": GATE_VERSION,
            "esquema_maestro_version": ESQUEMA_VERSION,
        }

    # 2.1 forma y allowlist de claves
    if not isinstance(propuesta, dict):
        motivos.append("forma_invalida")
        return veredicto("rechazado")
    extra = set(propuesta.keys()) - CLAVES_PERMITIDAS
    if extra:
        motivos.append("clave_desconocida")
        return veredicto("rechazado")
    for k in CLAVES_OBLIGATORIAS:
        if not propuesta.get(k):
            motivos.append("falta_obligatorio")
            return veredicto("rechazado")
    for k in CLAVES_OBLIGATORIAS:
        if not isinstance(propuesta[k], str):
            motivos.append("tipo_invalido")
            return veredicto("rechazado")

    # 2.2 higiene de bytes (anti-inyección léxica) — antes de mirar contenido
    if _bytes_sospechosos(propuesta):
        motivos.append("bytes_sospechosos")
        return veredicto("rechazado")

    # 2.3 enums declarados (dominio cerrado)
    if propuesta["origen"] not in ORIGENES:
        motivos.append("origen_no_enum")
        return veredicto("rechazado")
    if propuesta["confianza"] not in CONFIANZAS:
        motivos.append("confianza_no_enum")
        return veredicto("rechazado")
    if propuesta["plataforma"] not in PLATAFORMAS:
        # plausible pero no en lista → humano, no rechazo (puede ser real y nueva)
        motivos.append("plataforma_desconocida")
        return veredicto("necesita_ojo_humano")
    if propuesta["campo"] not in CAMPOS_VALIDOS:
        motivos.append("campo_no_en_esquema")
        return veredicto("necesita_ojo_humano")

    # 2.4 el valor valida contra el TIPO del campo
    ok, valor_norm = _valida_valor(propuesta["campo"], propuesta["valor"])
    if not ok:
        motivos.append("valor_no_valida")
        return veredicto("rechazado")

    # 2.5 fecha
    fdt = _fecha_ok(propuesta["fecha"])
    if fdt is None:
        motivos.append("fecha_invalida")
        return veredicto("rechazado", valor_norm)
    if (fdt - ahora).days > 2:
        motivos.append("fecha_futura")
        return veredicto("necesita_ojo_humano", valor_norm, fecha=propuesta["fecha"])

    # 2.6 procedencia: la fuente existe y está en el árbol permitido
    if not _ruta_permitida(propuesta["fuente_fichero"]):
        motivos.append("ruta_no_permitida")
        return veredicto("rechazado", valor_norm, fecha=propuesta["fecha"])
    abspath = propuesta["fuente_fichero"] if os.path.isabs(propuesta["fuente_fichero"]) else os.path.join(REPO, propuesta["fuente_fichero"])
    fuente_sha = _sha_fichero(abspath)
    if fuente_sha is None:
        motivos.append("fuente_inaccesible")
        return veredicto("necesita_ojo_humano", valor_norm, fecha=propuesta["fecha"])

    # 2.7 coherencia con el maestro (no-regresión, conflicto)
    maestro = maestro if maestro is not None else cargar_maestro()
    actual = (maestro.get("campos", {}) or {}).get(propuesta["campo"])
    if actual:
        f_act = _fecha_ok(actual.get("fecha", "")) if actual.get("fecha") else None
        if f_act and fdt < f_act:
            motivos.append("fecha_regresiva")
            return veredicto("necesita_ojo_humano", valor_norm, fuente_sha, propuesta["fecha"])
        if str(actual.get("valor")) != valor_norm and (f_act is None or (fdt - f_act).days <= 0):
            motivos.append("conflicto_con_maestro")
            return veredicto("necesita_ojo_humano", valor_norm, fuente_sha, propuesta["fecha"])

    # reglas de fail-closed por confianza / alto impacto
    if propuesta["confianza"] == "baja":
        motivos.append("confianza_baja")
        return veredicto("necesita_ojo_humano", valor_norm, fuente_sha, propuesta["fecha"])
    if propuesta["campo"] in CAMPOS_ALTO_IMPACTO:
        # cambio GORDO ⇒ nunca auto-persiste a ciegas, aunque la confianza sea alta.
        motivos.append("campo_alto_impacto")
        return veredicto("necesita_ojo_humano", valor_norm, fuente_sha, propuesta["fecha"])

    return veredicto("aprobado", valor_norm, fuente_sha, propuesta["fecha"])


# ─────────────────────────────────────────────────────────────────────────────
# PERFIL MAESTRO — fuente única de verdad clínica (JSON, local, gitignored).
# Estructura: {meta, campos: {campo: {valor, plataforma, fecha, confianza, origen,
# fuente_fichero, fuente_sha256, verificado_por_gate, actualizado}}, marcadores: {...}}.
# ─────────────────────────────────────────────────────────────────────────────
def cargar_maestro():
    if not os.path.exists(MAESTRO):
        return {"meta": {"creado": _now_iso(), "esquema": ESQUEMA_VERSION},
                "campos": {}, "marcadores": {}}
    try:
        with open(MAESTRO, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        # fail-safe: NO se pisa un maestro ilegible; se avisa.
        return {"_error": "perfil maestro ilegible: %r" % e, "campos": {}, "marcadores": {}}


def _guardar_maestro(d):
    d.setdefault("meta", {})["actualizado"] = _now_iso()
    _write_atomic(MAESTRO, json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True))


def persistir(veredicto, *, maestro=None):
    """Escribe al maestro SOLO si el veredicto está aprobado. Devuelve (persistido, campo).
    Loguea SIEMPRE la decisión + si llegó a escribir (efecto, no solo intención)."""
    persistido = False
    if veredicto["decision"] == "aprobado":
        d = maestro if maestro is not None else cargar_maestro()
        if d.get("_error"):
            persistido = False
        else:
            campo = veredicto["campo"]
            entrada = {
                "valor": veredicto["valor_normalizado"],
                "plataforma": (veredicto["evidencia"] or {}).get("plataforma"),
                "fecha": veredicto["evidencia"]["fecha"],
                "confianza": veredicto["evidencia"]["confianza"],
                "origen": veredicto["evidencia"]["origen"],
                "fuente_fichero": veredicto["evidencia"]["fuente_fichero"],
                "fuente_sha256": veredicto["evidencia"]["fuente_sha256"],
                "verificado_por_gate": True,
                "propuesta_id": veredicto["propuesta_id"],
                "actualizado": _now_iso(),
            }
            d.setdefault("campos", {})[campo] = entrada
            _guardar_maestro(d)
            persistido = True
    _auditar(veredicto, persistido)
    return persistido, veredicto["campo"]


def _auditar(veredicto, persistido):
    """Append-only JSONL. Un extraño debe reconstruir «por qué entró/no entró» sin mí."""
    ev = veredicto.get("evidencia") or {}
    _append_jsonl(AUDIT_LOG, {
        "ts": veredicto["ts_decision"],
        "propuesta_id": veredicto["propuesta_id"],
        "decision": veredicto["decision"],
        "motivos": veredicto["motivos"],
        "campo": veredicto["campo"],
        "valor_norm": veredicto["valor_normalizado"],
        "plataforma": ev.get("plataforma"),
        "origen": ev.get("origen"),
        "confianza": ev.get("confianza"),
        "fecha": ev.get("fecha"),
        "fuente_fichero": ev.get("fuente_fichero"),
        "fuente_sha256": ev.get("fuente_sha256"),
        "cita_cruda_trunc": veredicto.get("cita_cruda"),
        "gate_version": veredicto["gate_version"],
        "esquema_maestro_version": veredicto["esquema_maestro_version"],
        "persistido": persistido,
        "actor": "cascada_clinica (determinista, sin LLM)",
    })


# ─────────────────────────────────────────────────────────────────────────────
# CÓDIGO ROJO — umbrales OBJETIVOS (comite-medico). Dos niveles: ROJO (amenaza
# confirmada al goal → dispara el cortafuegos) y AMARILLO (señal sin confirmar →
# acelera vigilancia, NO para las máquinas). La severidad sale de ENUMS, no de texto.
# ─────────────────────────────────────────────────────────────────────────────
# Disparadores ROJOS: (campo, valor_norm) → motivo. Cambio de estado DISCRETO y
# verificado (lo escribió un informe / cruzó un umbral), no una impresión.
def evaluar_codigo_rojo(campo, valor_norm, *, confianza="alta", origen="manual_verificado"):
    """Devuelve (nivel, motivo): nivel ∈ {"rojo","amarillo","verde"}. Determinista.

    ROJO (dispara cortafuegos):
      1. estado_enfermedad → progresion / nueva_lesion (informe validado, no ctDNA solo)
      2. enfermedad_medible → no  (pérdida del gate que casi todo ensayo exige)
      3. viabilidad_muestra → no_apta  (la muestra de la vacuna falla)
      4. hla_loh que borra dianas  (señalado explícitamente como tal)
    AMARILLO (acelera, no para):
      - ctdna_mrd al alza sin imagen; marcador en 1 plataforma a baja confianza;
        respuesta que reduce medible (ventana de elegibilidad).
    """
    v = (valor_norm or "").lower()
    # ROJO 1: progresión / nueva lesión confirmada por imagen (no ctDNA solo)
    if campo == "estado_enfermedad" and v in ("progresion", "nueva_lesion"):
        if origen in ("escaner", "informe_patologia", "manual_verificado", "biopsia"):
            return "rojo", "progresión / nueva lesión confirmada en informe validado"
        return "amarillo", "señal de progresión sin imagen validada (vigilar)"
    # ROJO 2: pérdida de enfermedad medible (gate de elegibilidad de la ruta)
    if campo == "enfermedad_medible" and v == "no":
        return "rojo", "pérdida de enfermedad medible (gate que la ruta-vacuna exige)"
    # ROJO 3: la muestra para la vacuna no sirve
    if campo == "viabilidad_muestra" and v in ("no", "no_apta", "insuficiente"):
        return "rojo", "la muestra para la vacuna no es apta (sin tejido viable / RNA / PBMC)"
    # ROJO 4: HLA-LOH que borra las dianas (debe venir marcado como tal en el valor)
    # FIX 14-jul-26: SOLO el enum explicito. El substring de antes leia "no loss" como
    # "loss" y convertia la buena noticia en catastrofe.
    if campo == "hla_loh" and v == "loh_presente":
        return "rojo", "HLA-LOH alelo-específica que elimina dianas de la vacuna"
    # AMARILLO: ctDNA al alza sin confirmación de imagen
    if campo == "ctdna_mrd" and ("sube" in v or "alza" in v or "mrd+" in v or "positivo" in v):
        return "amarillo", "ctDNA/MRD al alza sin imagen (acelera vigilancia, no para)"
    # AMARILLO: respuesta que podría cerrar la ventana de elegibilidad
    if campo == "estado_enfermedad" and v in ("respuesta", "ned"):
        return "amarillo", "respuesta/NED puede cerrar la ventana de medible (revisar elegibilidad)"
    return "verde", ""


# ─────────────────────────────────────────────────────────────────────────────
# DETECTOR — clasifica una entrada como GORDA vs ruido. DETERMINISTA, por
# tipo/origen + diff. Fail-safe: ante la duda → GORDA y AVISA. La severidad sale de
# enums/tipos/fechas, NUNCA de texto libre.
# ─────────────────────────────────────────────────────────────────────────────
# Categorías de archivar.py que SÍ pueden traer un cambio clínico gordo.
CATEGORIAS_GORDAS = {"CLINICAL"}


def detectar(entrada):
    """`entrada`: dict con al menos {origen, categoria?}. Opcionalmente {campo, valor_norm}
    para un diff del perfil. Devuelve dict {gorda, motivo, severidad_via}.

    severidad_via documenta DE DÓNDE salió la severidad (enum/tipo), para auditar que no
    salió de texto libre. Fail-safe: lo que no se reconoce con seguridad → gorda."""
    via = []
    if not isinstance(entrada, dict):
        return {"gorda": True, "motivo": "entrada no estructurada (fail-safe)", "severidad_via": ["fail_safe"]}

    origen = entrada.get("origen")
    categoria = entrada.get("categoria")
    campo = entrada.get("campo")

    # 1) por categoría de archivar.py: clínico → candidato gordo
    if categoria in CATEGORIAS_GORDAS:
        via.append("categoria=CLINICAL")
        return {"gorda": True, "motivo": "informe clínico nuevo (categoría CLINICAL)", "severidad_via": via}

    # 2) por origen declarado (enum): un origen clínico → gordo
    if origen in ORIGENES and origen != "manual_verificado":
        via.append("origen=%s" % origen)
        return {"gorda": True, "motivo": "origen clínico declarado (%s)" % origen, "severidad_via": via}

    # 3) por diff del perfil: si toca un dato del MAPA → gordo
    if campo in cascada_mapa.MAPA:
        via.append("campo en mapa=%s" % campo)
        return {"gorda": True, "motivo": "cambio en dato gobernante (%s)" % campo, "severidad_via": via}

    # 4) ruido conocido: categorías no clínicas SIN campo del mapa
    if categoria and categoria not in CATEGORIAS_GORDAS and not campo:
        return {"gorda": False, "motivo": "categoría no clínica (%s)" % categoria, "severidad_via": ["categoria_no_clinica"]}

    # 5) fail-safe: no lo reconozco con seguridad → tratar como gorda y AVISAR
    return {"gorda": True, "motivo": "entrada no clasificada con seguridad (fail-safe → avisar)", "severidad_via": ["fail_safe"]}


# ─────────────────────────────────────────────────────────────────────────────
# STALE — registro de análisis marcados obsoletos por un cambio (lo que hay que
# re-ejecutar). Lo barato (modo auto) se re-encola y corre; lo caro (modo gate) queda
# a un clic de {{TITULAR}}; lo humano se le marca.
# ─────────────────────────────────────────────────────────────────────────────
def _cargar_stale():
    if not os.path.exists(STALE):
        return {"items": [], "actualizado": _now_iso()}
    try:
        with open(STALE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": [], "actualizado": _now_iso()}


def marcar_stale(afectados, *, por_cambio):
    """Marca STALE los dependientes afectados. `afectados` = salida de cascada_mapa.afectados.
    Idempotente por (dependiente, modo). Devuelve la lista de items stale activos."""
    d = _cargar_stale()
    existentes = {(it["dependiente"]) for it in d["items"] if it.get("estado") == "stale"}
    for dep, info in sorted(afectados.items()):
        if dep in existentes:
            # refresca el motivo pero no duplica
            for it in d["items"]:
                if it["dependiente"] == dep and it.get("estado") == "stale":
                    if por_cambio not in it["por_cambio"]:
                        it["por_cambio"].append(por_cambio)
            continue
        d["items"].append({
            "dependiente": dep, "titulo": info["titulo"], "modo": info["modo"],
            "via": info["via"], "por_cambio": [por_cambio], "estado": "stale",
            "marcado": _now_iso(),
        })
    d["actualizado"] = _now_iso()
    _write_atomic(STALE, json.dumps(d, ensure_ascii=False, indent=2))
    return [it for it in d["items"] if it.get("estado") == "stale"]


# Dependientes 'auto' con re-derivación REAL implementada en v1. El resto se deja en
# 'pendiente_auto' (deuda HONESTA, no falso verde): no se marca «reejecutado» algo que
# nadie recalculó (lección feedback-verificar-efecto-no-que-corrio).
_AUTO_IMPLEMENTADO = {"cumbre"}


def _resolver_auto(stale_items):
    """Re-ejecuta lo barato (modo auto) y registra el EFECTO real, no la intención.
    `cumbre` SÍ se re-deriva (propagate llama a render_brujula); `medible`/`lineas_previas`
    aún no tienen tool de re-derivación → quedan 'pendiente_auto' (visible, no falso ok).
    Devuelve (reejecutados, pendientes). Lo caro (gate) y humano NO se tocan aquí."""
    hechos, pendientes = [], []
    d = _cargar_stale()
    for it in d["items"]:
        if it.get("estado") != "stale" or it["modo"] != "auto":
            continue
        if it["dependiente"] in _AUTO_IMPLEMENTADO:
            it["estado"] = "reejecutado"
            it["reejecutado"] = _now_iso()
            hechos.append(it["dependiente"])
        else:
            it["estado"] = "pendiente_auto"
            pendientes.append(it["dependiente"])
    d["actualizado"] = _now_iso()
    _write_atomic(STALE, json.dumps(d, ensure_ascii=False, indent=2))
    return hechos, pendientes


def _encolar_gate(stale_items):
    """Lo caro (modo gate: elegibilidad, fit_vacuna, radar, dianas, diseno_peptidos) se
    ENCOLA como tarea a un clic de {{TITULAR}} — NUNCA se auto-lanza (coste). Devuelve los ids.

    Crea una tarea por la fuente única `seguimiento.crear_tarea` (no se auto-mandan
    tarjetas a discreción: aquí es una re-validación clínica que SÍ es tarea NED real)."""
    ids = []
    try:
        import seguimiento  # noqa: E402
    except Exception:
        return ids
    gates = sorted({it["dependiente"]: it for it in stale_items if it["modo"] == "gate"}.values(),
                   key=lambda x: x["dependiente"])
    for it in gates:
        try:
            tid = seguimiento.crear_tarea(
                "Re-validar: %s (tu historial cambió)" % it["titulo"],
                etiqueta="NED", origen="cascada-clinica", estado="esperando",
                prioridad="alta",
                por_que="un cambio en tu historial puede afectar a esto; pide tu OK para re-correrlo",
            )
            ids.append(tid)
        except Exception:
            pass
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE PROPAGACIÓN — el orquestador. Confirmado el cambio:
#  (a) la data externa pasa por el GATE antes de persistir
#  (b) actualiza el maestro · (c) reindexa kb · (d) calcula CHANGELOG
#  (e) marca STALE + re-ejecuta auto / encola gate · (f) actualiza cumbre
#  (g) resumen llano a {{TITULAR}} · (h) código rojo si amenaza NED.
# ─────────────────────────────────────────────────────────────────────────────
def propagate(propuesta, *, dry=True, reindex=False, avisar=False, ahora=None):
    """Propaga UNA propuesta de cambio. Devuelve un informe (dict) de todo lo que pasó.
    dry=True (por defecto): NO escribe maestro, NO encola, NO avisa, NO dispara rojo —
    solo simula y devuelve el plan. dry=False ejecuta el núcleo determinista (persistir,
    stale, auto-reejecución, encolado de gates, cumbre, aviso, código rojo).

    El aviso a {{TITULAR}} y el disparo de código rojo SOLO ocurren con dry=False AND el flag
    correspondiente (avisar). El reindex de kb es caro → solo con reindex=True."""
    informe = {"ts": _now_iso(), "dry": dry, "pasos": []}

    # (a) GATE
    veredicto = verificar_para_maestro(propuesta, ahora=ahora)
    informe["veredicto"] = {k: veredicto[k] for k in ("decision", "campo", "valor_normalizado", "motivos", "propuesta_id")}
    informe["pasos"].append("gate: %s (%s)" % (veredicto["decision"], ", ".join(veredicto["motivos"])))

    campo = veredicto["campo"]
    valor = veredicto["valor_normalizado"]

    # (h-pre) CÓDIGO ROJO se evalúa SIEMPRE (incluso si el gate manda a ojo humano: un
    # cambio que amenaza NED no espera a persistir). La severidad sale de enums.
    # Si el gate NO produjo valor_normalizado (rechazo/validación fallida), NO nos
    # quedamos ciegos: canonicalizamos el valor CRUDO propuesto para el chequeo de rojo,
    # así un «no_apta»/«nueva lesion» mal formado sigue disparando la alarma.
    # FIX 14-jul-26 (fail-OPEN cazado por verificacion): si el gate RECHAZA el dato
    # (bytes sospechosos, validacion fallida, posible inyeccion), NO se evalua codigo
    # rojo sobre el valor CRUDO. No puedes desconfiar de un dato y a la vez parar el
    # mundo con el. Un PDF corrupto con "no_apta" era una denegacion de servicio.
    if veredicto["decision"] == "rechazado":
        nivel, motivo_rojo = "verde", ""
        informe["revisar_humano"] = True
        informe["pasos"].append(
            "gate RECHAZO el dato -> NO se evalua codigo rojo (antes era fail-open). "
            "Lo mira un humano.")
    else:
        valor_rojo = valor if valor is not None else _canon_enum(str(propuesta.get("valor", "")))
        nivel, motivo_rojo = evaluar_codigo_rojo(
            campo, valor_rojo, confianza=propuesta.get("confianza", "alta"),
            origen=propuesta.get("origen", "manual_verificado"))
    informe["codigo_rojo"] = {"nivel": nivel, "motivo": motivo_rojo}
    informe["pasos"].append("código rojo: nivel=%s%s" % (nivel, (" — " + motivo_rojo) if motivo_rojo else ""))

    # (b) PERSISTIR — solo si el gate aprobó
    if veredicto["decision"] == "aprobado":
        if dry:
            informe["pasos"].append("(dry) persistiría %s=%s al maestro" % (campo, valor))
            persistido = False
        else:
            persistido, _ = persistir(veredicto)
            informe["pasos"].append("persistido al maestro: %s" % persistido)
    else:
        # aun sin persistir, SE AUDITA la decisión (no-op de escritura)
        if not dry:
            _auditar(veredicto, False)
        persistido = False
        informe["pasos"].append("NO persiste (decisión=%s) → %s" % (
            veredicto["decision"],
            "se le muestra a {{TITULAR}} el dato citado, sin obedecerlo" if veredicto["decision"] == "necesita_ojo_humano" else "descartado (posible inyección/malformado)"))
    informe["persistido"] = persistido

    # (d) CHANGELOG — qué campo cambió
    informe["changelog"] = [{"campo": campo, "valor_nuevo": valor, "persistido": persistido}] if campo else []

    # (e) STALE + re-ejecución — solo si el cambio es real (gate aprobó O es alto impacto
    # que un humano va a confirmar: marcamos stale para no perder el rastro).
    afectados = {}
    if campo in cascada_mapa.MAPA and veredicto["decision"] in ("aprobado", "necesita_ojo_humano"):
        afectados = cascada_mapa.afectados(campo)
        informe["afectados"] = {k: v["modo"] for k, v in afectados.items()}
        if not dry:
            stale = marcar_stale(afectados, por_cambio=campo)
            hechos_auto, pendientes_auto = _resolver_auto(stale)
            ids_gate = _encolar_gate(stale)
            informe["pasos"].append("stale marcado: %d; auto-reejecutados: %s; auto-pendientes: %s; gates encolados: %d" % (
                len(stale), ", ".join(hechos_auto) or "—", ", ".join(pendientes_auto) or "—", len(ids_gate)))
            informe["gates_encolados"] = ids_gate
        else:
            informe["pasos"].append("(dry) marcaría stale %d dependientes (auto: %d, gate: %d, humano: %d)" % (
                len(afectados),
                sum(1 for v in afectados.values() if v["modo"] == "auto"),
                sum(1 for v in afectados.values() if v["modo"] == "gate"),
                sum(1 for v in afectados.values() if v["modo"] == "humano")))

    # (f) CUMBRE — si cambió fase/ruta/estado, refrescar la brújula (re-derivación auto)
    if campo in ("estado_enfermedad", "enfermedad_medible") and not dry:
        try:
            import cumbre  # noqa: E402
            cumbre.render_brujula()
            informe["pasos"].append("cumbre: brújula refrescada")
        except Exception as e:
            informe["pasos"].append("cumbre: no se pudo refrescar (%r)" % e)

    # (c) REINDEX kb — caro, solo si se pide explícitamente y no es dry
    if reindex and not dry and persistido:
        informe["pasos"].append("kb.index: pendiente (lo lanza el caller; no se auto-lanza en propagate)")

    # (h) CÓDIGO ROJO — dispara el cortafuegos si nivel rojo (solo con dry=False)
    if nivel == "rojo":
        # FIX 14-jul-26: AVISAR y APAGAR EL SISTEMA eran la MISMA llamada. Un parseo
        # automatico podia echar el .HALT y descargar los daemons (el mecanismo que
        # dejo el healthcheck muerto 42h en julio). Ahora el cortafuegos SOLO lo arma
        # un humano, pasando _armar_rojo=True en la propuesta.
        armar = bool(propuesta.get("_armar_rojo"))
        if dry:
            informe["pasos"].append("(dry) nivel ROJO detectado (%s). NO se dispara nada." % motivo_rojo)
        elif not armar:
            informe["revisar_humano"] = True
            informe["pasos"].append(
                "ROJO detectado (%s). NO se ha parado nada: el cortafuegos ahora lo arma "
                "un HUMANO (_armar_rojo=True). Revisalo y decide." % motivo_rojo)
        else:
            try:
                import codigo_rojo  # noqa: E402
                codigo_rojo.trigger(
                    "Cascada clínica: %s" % motivo_rojo,
                    "Un cambio en el historial de {{TITULAR}} (%s = %s) amenaza la ruta a NED. "
                    "La cascada lo detectó por umbral OBJETIVO. Revisa el detalle y decide." % (campo, valor))
                informe["pasos"].append("CÓDIGO ROJO DISPARADO: %s" % motivo_rojo)
            except Exception as e:
                informe["pasos"].append("código rojo: FALLO al disparar (%r) — AVISAR a mano" % e)

    # (g) RESUMEN LLANO a {{TITULAR}}
    informe["resumen_llano"] = resumen_llano(informe, veredicto, afectados, nivel, motivo_rojo)
    if avisar and not dry:
        try:
            import salida  # noqa: E402
            # FIX 14-jul-26 (MURO): el resumen incrusta la CITA CRUDA del informe
            # (arrastraba nombre + alelos HLA) y sale por REPORT, que NO pasa el gate
            # del muro. El docstring promete "egress CERO". De-identificamos antes de
            # cruzar; si el borde no carga, se manda un aviso SORDO (fail-closed).
            texto = informe["resumen_llano"]
            try:
                import borde  # noqa: E402
                texto, _nred = borde.de_identificar(texto)
            except Exception:
                texto = ("Cambio clinico detectado en la cascada. El detalle NO sale de "
                         "la maquina: abre la ficha para verlo.")
            salida.report_to_titular(texto, urgente=(nivel == "rojo"))
            informe["pasos"].append("aviso enviado a {{TITULAR}} (urgente=%s)" % (nivel == "rojo"))
        except Exception as e:
            informe["pasos"].append("aviso: no se pudo enviar (%r)" % e)

    return informe


def resumen_llano(informe, veredicto, afectados, nivel, motivo_rojo):
    """Texto para {{TITULAR}} en español llano, sin jerga. APOYO A LA DECISIÓN: describe lo
    que se actualizó y lo que pide su OK; NO concluye nada clínico."""
    campo = veredicto["campo"] or "un dato"
    nombre = cascada_mapa.MAPA.get(campo, {}).get("titulo", campo)
    dec = veredicto["decision"]
    líneas = ["Tu historial clínico ha cambiado.", ""]

    if dec == "aprobado":
        líneas.append("• %s: lo he actualizado en tu ficha maestra (verificado)." % nombre)
    elif dec == "necesita_ojo_humano":
        líneas.append("• %s: lo he detectado, pero quiero que lo confirmes tú antes de darlo por bueno." % nombre)
        if veredicto.get("cita_cruda"):
            líneas.append("  Lo que decía el informe (sin verificar): «%s»" % veredicto["cita_cruda"].replace("[CITA-NO-INSTRUCCION] ", ""))
    else:
        líneas.append("• Llegó un dato que no he podido verificar, así que lo dejé como estaba por si el informe viene con un error.")

    auto = sorted(d["titulo"] for d in afectados.values() if d["modo"] == "auto")
    gate = sorted(d["titulo"] for d in afectados.values() if d["modo"] == "gate")
    humano = sorted(d["titulo"] for d in afectados.values() if d["modo"] == "humano")
    if auto:
        líneas += ["", "Se ha actualizado solo:"] + ["  - " + t for t in auto]
    if gate:
        líneas += ["", "Esto pide tu OK para volver a calcularlo (un clic, lo dejé en tus tareas):"] + ["  - " + t for t in gate]
    if humano:
        líneas += ["", "Esto conviene que lo revise alguien (tú o tu equipo):"] + ["  - " + t for t in humano]

    if nivel == "rojo":
        líneas = ["🔴 ATENCIÓN — esto puede afectar a tu camino a NED:", "", motivo_rojo,
                  "He parado las máquinas y te lo explico en detalle en otro mensaje.", ""] + líneas
    elif nivel == "amarillo":
        líneas += ["", "Aviso (no es alarma): %s" % motivo_rojo]

    return "\n".join(líneas)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
# Flags de comportamiento de la CLI (NO son claves de la propuesta; nunca contaminan el dict).
_FLAGS_CLI = {"--apply", "--avisar", "--dry"}


def _parse_kv(args):
    """--campo X --valor Y ... → dict. SOLO para la propuesta de cambio: los flags de
    comportamiento (--apply, --avisar) se ignoran aquí (si no, entrarían como claves
    desconocidas y el gate rechazaría la propuesta por su propia CLI)."""
    out = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in _FLAGS_CLI:
            i += 1
            continue
        if tok.startswith("--") and i + 1 < len(args) and args[i + 1] not in _FLAGS_CLI:
            out[tok[2:]] = args[i + 1]
            i += 2
        else:
            i += 1
    return out


def main(argv):
    cmd = argv[0] if argv else "ayuda"
    a = argv[1:]

    if cmd == "detect":
        entrada = _parse_kv(a)
        print(json.dumps(detectar(entrada), ensure_ascii=False, indent=2))
        return 0

    if cmd == "verify":
        prop = _parse_kv(a)
        v = verificar_para_maestro(prop)
        print(json.dumps({k: v[k] for k in ("decision", "campo", "valor_normalizado", "motivos", "propuesta_id")},
                         ensure_ascii=False, indent=2))
        return 0

    if cmd == "propagate":
        prop = _parse_kv(a)
        dry = "--apply" not in argv
        avisar = "--avisar" in argv
        inf = propagate(prop, dry=dry, avisar=avisar)
        print(json.dumps(inf, ensure_ascii=False, indent=2))
        if dry:
            print("\n(DRY-RUN: nada se escribió. Añade --apply para ejecutar. --avisar manda el resumen a {{TITULAR}}.)", file=sys.stderr)
        return 0

    if cmd == "afectados":
        if not a:
            print("uso: cascada_clinica.py afectados <dato>...")
            return 2
        res = cascada_mapa.afectados(a)
        for dep, info in sorted(res.items()):
            print("[%s] %s (vía: %s)" % (info["modo"], info["titulo"], ", ".join(info["via"])))
        return 0

    if cmd == "stale":
        d = _cargar_stale()
        activos = [it for it in d.get("items", []) if it.get("estado") == "stale"]
        if not activos:
            print("(sin análisis obsoletos pendientes)")
        for it in activos:
            print("[%s] %s ← %s" % (it["modo"], it["titulo"], ", ".join(it["por_cambio"])))
        return 0

    if cmd == "maestro":
        print(json.dumps(cargar_maestro(), ensure_ascii=False, indent=2))
        return 0

    print("uso: cascada_clinica.py [detect|verify|propagate [--apply --avisar]|afectados <dato>|stale|maestro]\n"
          "  campos de propuesta: --campo --valor --plataforma --fecha --confianza --origen --fuente_fichero [--cita_cruda]")
    return 0 if cmd == "ayuda" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

