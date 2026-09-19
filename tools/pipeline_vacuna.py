#!/usr/bin/env python3
"""tools/pipeline_vacuna.py — tablero VIVO del flujo biopsia → vacuna (neoantígenos).

Rastrea EN QUÉ ETAPA está el pipeline computacional de neoantígenos y si cada GATE de
calidad pasa/falla/está pendiente — para que, cuando llegue el tejido de Zúrich (~jul),
no se pierda ni una semana y se vea de un vistazo qué está hecho / pendiente / bloqueado.

Espejo del patrón de `seguimiento.py` (estado determinista, escritura atómica, allowlist
de campos CERRADA, sin dependencias). Las 6 etapas y sus gates salen del documento canónico
`04 · IA/Pipeline-Neoantigenos-Listo-para-FASTQ-2026-06-21.md` (comité médico, 2026-06-21).

  A  biopsia                 — recepción y fraccionamiento de cores (vigente tras filtro de {{CONTACTO}})
  B  preservación / QC tejido — gate 0: DV200, snap-frozen, pureza, profundidad, FastQC/MultiQC
  C  extracción / secuenciación — QC + alineamiento + cuantificación de expresión
  D  HLA / expresión          — gate B: tipado HLA-I/II 4 dígitos + LOH-HLA; expresión exigida
  E  priorización neoantígenos — gates C/D/E: variantes, presentación pMHC, inmunogenicidad
  F  dossier listo            — gate de salida: tabla priorizada + caveats; "listo para diseñar"

MURO (innegociable — el tablero es para MIRARLO, no para concluir):
  · Apoyo a la decisión, NO consejo médico. Esto DESCRIBE y EQUIPA el flujo; NO concluye,
    NO diseña la vacuna, NO promete neoantígenos ni eficacia.
  · EGRESS CERO de crudo/PII: este tablero guarda SOLO el ESTADO del flujo y los gates
    (nombre de etapa, umbral GENÉRICO de laboratorio, estado enum, nota corta operativa).
    Los VALORES clínicos crudos (DV200 real, profundidad real, alelos HLA, VCF, métricas)
    siguen en `_PRIVADO_CLINICO/` (fuera de git) — NUNCA entran aquí. Un guardia
    (`_rechaza_pii`) bloquea por construcción que un valor crudo/secuencia se cuele en una nota.
  · El estado vivo vive en `tools/state/pipeline_vacuna.json` (gitignored, NO viaja a git ni a
    los worktrees). Se resuelve a casa base (BTP_REPO o ~/claudecode), igual que seguimiento.py.
  · Quién pasa cada gate es un humano cualificado (no un botón): este módulo solo REGISTRA el
    estado que un humano declara; no mide, no decide, no avanza solo.

Uso (CLI):
  python3 tools/pipeline_vacuna.py init           # siembra las 6 etapas + gates (idempotente)
  python3 tools/pipeline_vacuna.py estado         # imprime el resumen (texto)
  python3 tools/pipeline_vacuna.py once           # imprime el JSON de resumen y sale (debug)
  python3 tools/pipeline_vacuna.py set-etapa C en_curso
  python3 tools/pipeline_vacuna.py set-gate B hla_loh ok --nota "evaluado, sin pérdida en HLA-A"
"""
import json
import os
import re
import sys
from datetime import datetime

# El estado VIVO vive SOLO en casa base (tools/state/ está gitignored: NO viaja a los worktrees).
# Una sesión en su worktree que mueva una etapa debe escribir en la ÚNICA libreta viva que lee
# El Observatorio, no en una copia desechable. Mismo criterio que seguimiento.py / observatorio.py.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
PV = os.path.join(STATE, "pipeline_vacuna.json")

# Documento de especificación (para citar la procedencia; se LEE de casa base, no se versiona).
DOC = "00_FUENTE-DE-VERDAD/04 · IA/Pipeline-Neoantigenos-Listo-para-FASTQ-2026-06-21.md"

# --- enums CERRADOS (allowlist fail-closed, anti-inyección: un estado/etapa desconocido se RECHAZA) ---
ESTADOS_ETAPA = ("pendiente", "en_curso", "hecho", "bloqueado")
ESTADOS_GATE = ("pendiente", "ok", "fallo", "na")   # na = no aplica a este caso
GATE_EMOJI = {"pendiente": "·", "ok": "✓", "fallo": "✗", "na": "—"}
ETAPA_EMOJI = {"pendiente": "·", "en_curso": "▸", "hecho": "✓", "bloqueado": "■"}


# ─────────────────────────── el esqueleto canónico ───────────────────────────
# Etapas A–F con sus gates de calidad. Los UMBRALES son GENÉRICOS (de laboratorio, públicos):
# describen la vara, no el valor del paciente. El valor real del paciente NUNCA se guarda aquí.
def _esqueleto():
    return [
        {
            "id": "A", "nombre": "Biopsia",
            "que": "Recepción y fraccionamiento de cores (5-6 cores, lesión diana; vigente tras filtro de {{CONTACTO}}).",
            "estado": "pendiente",
            "gates": [
                {"id": "cores", "nombre": "Cores suficientes y bien fraccionados",
                 "umbral": "5-6 cores; tumor + germinal + excedente para MS", "estado": "pendiente"},
                {"id": "diana", "nombre": "Lesión diana correcta muestreada",
                 "umbral": "core de la lesión accionable (no necrótica)", "estado": "pendiente"},
            ],
        },
        {
            "id": "B", "nombre": "Preservación / QC tejido",
            "que": "Gate 0 del dossier: materia prima apta antes de analizar (sin esto, GIGO).",
            "estado": "pendiente",
            "gates": [
                {"id": "dv200", "nombre": "Integridad de RNA (DV200)",
                 "umbral": "DV200 >= 30 %", "estado": "pendiente"},
                {"id": "snap_frozen", "nombre": "RNA en tejido snap-frozen (no solo FFPE)",
                 "umbral": "snap-frozen preferente (preserva splicing/fusiones)", "estado": "pendiente"},
                {"id": "pureza", "nombre": "Pureza tumoral estimada y registrada",
                 "umbral": "estimada por CN/VAF (afecta sensibilidad somática y LOH)", "estado": "pendiente"},
                {"id": "fastqc", "nombre": "FastQC/MultiQC sin banderas críticas",
                 "umbral": "Q30, adaptadores, duplicación OK", "estado": "pendiente"},
            ],
        },
        {
            "id": "C", "nombre": "Extracción / secuenciación",
            "que": "Profundidad alcanzada + alineamiento + cuantificación de expresión.",
            "estado": "pendiente",
            "gates": [
                {"id": "prof_wes_tumor", "nombre": "Profundidad WES tumor",
                 "umbral": "~200-300x efectivos (post-dedup)", "estado": "pendiente"},
                {"id": "prof_wes_normal", "nombre": "Profundidad WES normal (pareado)",
                 "umbral": "~100x", "estado": "pendiente"},
                {"id": "prof_rna", "nombre": "Profundidad RNA-seq",
                 "umbral": ">= 100-200M lecturas PE stranded", "estado": "pendiente"},
                {"id": "align", "nombre": "Alineamiento GRCh38 (ALT-aware) + STAR 2-pass",
                 "umbral": "BAM analítico DNA y RNA; junctions para fusiones", "estado": "pendiente"},
            ],
        },
        {
            "id": "D", "nombre": "HLA / expresión",
            "que": "Gate B: tipado HLA-I/II y pérdida alélica (LOH); base de expresión.",
            "estado": "pendiente",
            "gates": [
                {"id": "hla_tipado", "nombre": "HLA-I y II tipados a 4 dígitos",
                 "umbral": ">= 2 herramientas concuerdan (OptiType/HLA-LA/arcasHLA)", "estado": "pendiente"},
                {"id": "hla_loh", "nombre": "LOH-HLA evaluado; alelos perdidos excluidos",
                 "umbral": "LOHHLA con pureza/ploidía; predecir alelo perdido = ruido", "estado": "pendiente"},
                {"id": "expresion", "nombre": "Expresión cuantificada (TPM por transcrito)",
                 "umbral": "Salmon/kallisto; no expresado = no presentado", "estado": "pendiente"},
            ],
        },
        {
            "id": "E", "nombre": "Priorización neoantígenos",
            "que": "Gates C/D/E: variantes (RNA-céntrico), presentación pMHC e inmunogenicidad.",
            "estado": "pendiente",
            "gates": [
                {"id": "variantes", "nombre": "Variantes por consenso + RNA-céntrico",
                 "umbral": "SNV Mutect2 ∩ Strelka2; fusiones ≥2 callers; splicing vs normales", "estado": "pendiente"},
                {"id": "presentacion", "nombre": "Presentación pMHC (solo alelos no perdidos)",
                 "umbral": "%rank EL <= 2 % (fuerte <= 0.5 %); filtrado por LOH", "estado": "pendiente"},
                {"id": "inmunogenicidad", "nombre": "Inmunogenicidad (anti-binding-a-secas)",
                 "umbral": "pVACtools + NeoFox + PredIG; no-canónicos puntuados", "estado": "pendiente"},
                {"id": "ms", "nombre": "Corroboración física por MS (si hay tejido excedente)",
                 "umbral": "NeoDisc/iPepGen sobre DB del paciente — Nivel 3, opcional", "estado": "pendiente"},
            ],
        },
        {
            "id": "F", "nombre": "Dossier listo",
            "que": "Gate de salida: tabla priorizada + caveats. 'Listo para que un cualificado diseñe la vacuna'.",
            "estado": "pendiente",
            "gates": [
                {"id": "tabla", "nombre": "Tabla priorizada con evidencia por candidato",
                 "umbral": "presentación + inmunogenicidad + features por candidato", "estado": "pendiente"},
                {"id": "caveats", "nombre": "Caveats declarados (TESLA una vez) + sin sobre-promesa",
                 "umbral": "sin nº prometido, sin eficacia, marcado investigacional", "estado": "pendiente"},
            ],
        },
    ]


# ─────────────────────────── guardia anti-PII (muro de datos) ───────────────────────────
# El tablero guarda ESTADO + umbrales genéricos. Una NOTA libre podría ser un agujero por donde
# se cuele un valor crudo / secuencia / alelo concreto del paciente. Esto lo cierra por construcción:
# rechaza notas que parezcan secuencia de nucleótidos o un alelo HLA tipado concreto.
_RE_SECUENCIA = re.compile(r"[ACGTUN]{12,}", re.IGNORECASE)          # tramo de secuencia
_RE_HLA_TIPADO = re.compile(r"\bHLA-[A-DRQP]+[0-9]*\*\d{2}:\d{2}", re.IGNORECASE)  # p.ej. HLA-A*02:01
_MAX_NOTA = 160


def _rechaza_pii(texto):
    """Devuelve un motivo (str) si el texto parece llevar dato crudo/secuencia/alelo concreto; si no, None.
    Conservador a propósito (el muro pesa sobre la comodidad): umbral genérico SÍ ('DV200 >= 30 %'),
    valor del paciente NO ('DV200 = 18 %' con un alelo tipado o una secuencia)."""
    if texto is None:
        return None
    t = str(texto)
    if len(t) > _MAX_NOTA:
        return "nota demasiado larga (máx %d): el tablero guarda estado, no informes" % _MAX_NOTA
    if _RE_SECUENCIA.search(t):
        return "parece contener una secuencia de nucleótidos (dato crudo no permitido en el tablero)"
    if _RE_HLA_TIPADO.search(t):
        return "parece contener un alelo HLA tipado concreto del paciente (dato crudo no permitido)"
    return None


# ─────────────────────────── persistencia ───────────────────────────
def _write_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load():
    """Carga el tablero. Ausente → estructura vacía (no se siembra sola: hay que `init`).
    Ilegible → NO asume vacío (ocultaría progreso): devuelve _error para avisar fuerte."""
    try:
        with open(PV, encoding="utf-8") as f:
            d = json.load(f) or {}
    except FileNotFoundError:
        return {"etapas": [], "actualizado": None}
    except Exception as e:
        return {"etapas": [], "_error": "pipeline_vacuna.json ilegible: %r" % e}
    d.setdefault("etapas", [])
    return d


def init(forzar=False):
    """Siembra las 6 etapas + gates del documento canónico. Idempotente: si ya existe, NO machaca
    el progreso (estados declarados por un humano) salvo `forzar=True`. Devuelve el tablero."""
    actual = load()
    if actual.get("etapas") and not forzar:
        return actual
    d = {
        "version": 1,
        "doc": DOC,
        "creado": datetime.now().strftime("%Y-%m-%d"),
        "actualizado": datetime.now().strftime("%Y-%m-%d"),
        "etapas": _esqueleto(),
    }
    _write_atomic(PV, d)
    return d


def _buscar_etapa(d, etapa_id):
    for e in d.get("etapas", []):
        if e.get("id") == etapa_id:
            return e
    return None


def set_etapa(etapa_id, estado, nota=None):
    """Cambia el estado de una ETAPA. Valida el enum (fail-closed) y la nota (anti-PII).
    Cambio LOCAL: no envía/publica/contacta. Devuelve la etapa actualizada."""
    etapa_id = str(etapa_id).upper().strip()
    if estado not in ESTADOS_ETAPA:
        raise ValueError("estado de etapa inválido %r (usa %s)" % (estado, "/".join(ESTADOS_ETAPA)))
    motivo = _rechaza_pii(nota)
    if motivo:
        raise ValueError("MURO: %s" % motivo)
    d = load()
    if d.get("_error"):
        raise RuntimeError(d["_error"])
    e = _buscar_etapa(d, etapa_id)
    if not e:
        raise KeyError("no existe la etapa %r (válidas: %s)" % (
            etapa_id, "/".join(x["id"] for x in d.get("etapas", []))))
    e["estado"] = estado
    if nota is not None:
        e["nota"] = str(nota).strip()
    d["actualizado"] = datetime.now().strftime("%Y-%m-%d")
    _write_atomic(PV, d)
    return e


def set_gate(etapa_id, gate_id, estado, nota=None):
    """Cambia el estado de un GATE de calidad dentro de una etapa. Valida enum + nota (anti-PII).
    NO avanza la etapa sola: que un gate pase es algo que declara un humano cualificado, y el estado
    de la etapa lo decide ese humano. Devuelve el gate actualizado."""
    etapa_id = str(etapa_id).upper().strip()
    gate_id = str(gate_id).strip()
    if estado not in ESTADOS_GATE:
        raise ValueError("estado de gate inválido %r (usa %s)" % (estado, "/".join(ESTADOS_GATE)))
    motivo = _rechaza_pii(nota)
    if motivo:
        raise ValueError("MURO: %s" % motivo)
    d = load()
    if d.get("_error"):
        raise RuntimeError(d["_error"])
    e = _buscar_etapa(d, etapa_id)
    if not e:
        raise KeyError("no existe la etapa %r" % etapa_id)
    for g in e.get("gates", []):
        if g.get("id") == gate_id:
            g["estado"] = estado
            if nota is not None:
                g["nota"] = str(nota).strip()
            d["actualizado"] = datetime.now().strftime("%Y-%m-%d")
            _write_atomic(PV, d)
            return g
    raise KeyError("no existe el gate %r en la etapa %s (válidos: %s)" % (
        gate_id, etapa_id, "/".join(g["id"] for g in e.get("gates", []))))


# ─────────────────────────── lectura / resumen (lo que consume El Observatorio) ───────────────────────────
def resumen():
    """Resumen SOLO-LECTURA para la tarjeta del Observatorio y el CLI. Aritmética simple,
    sin LLM, sin efectos. Reporta progreso por etapa y el conteo de gates (ok/fallo/pendiente)."""
    d = load()
    if d.get("_error"):
        return {"_error": d["_error"]}
    etapas = d.get("etapas", [])
    out_etapas = []
    g_ok = g_fallo = g_pend = g_na = 0
    e_hecho = e_bloq = 0
    etapa_actual = None
    for e in etapas:
        gates = e.get("gates", [])
        cuenta = {"ok": 0, "fallo": 0, "pendiente": 0, "na": 0}
        for g in gates:
            cuenta[g.get("estado", "pendiente")] = cuenta.get(g.get("estado", "pendiente"), 0) + 1
        g_ok += cuenta["ok"]; g_fallo += cuenta["fallo"]
        g_pend += cuenta["pendiente"]; g_na += cuenta["na"]
        est = e.get("estado", "pendiente")
        if est == "hecho":
            e_hecho += 1
        elif est == "bloqueado":
            e_bloq += 1
        if etapa_actual is None and est in ("en_curso", "bloqueado"):
            etapa_actual = e.get("id")
        out_etapas.append({
            "id": e.get("id"), "nombre": e.get("nombre"), "estado": est,
            "que": e.get("que", ""), "nota": e.get("nota", ""),
            "gates": [{"id": g.get("id"), "nombre": g.get("nombre"),
                       "umbral": g.get("umbral", ""), "estado": g.get("estado", "pendiente"),
                       "nota": g.get("nota", "")} for g in gates],
            "gates_ok": cuenta["ok"], "gates_fallo": cuenta["fallo"],
            "gates_pendiente": cuenta["pendiente"], "gates_na": cuenta["na"],
            "gates_total": len(gates),
        })
    # primera etapa no-hecha = "siguiente" si ninguna está explícitamente en curso/bloqueada
    if etapa_actual is None:
        for e in etapas:
            if e.get("estado") != "hecho":
                etapa_actual = e.get("id"); break
    return {
        "doc": d.get("doc", DOC),
        "actualizado": d.get("actualizado"),
        "n_etapas": len(etapas),
        "etapas_hechas": e_hecho,
        "etapas_bloqueadas": e_bloq,
        "etapa_actual": etapa_actual,
        "gates_total": g_ok + g_fallo + g_pend + g_na,
        "gates_ok": g_ok, "gates_fallo": g_fallo, "gates_pendiente": g_pend, "gates_na": g_na,
        "etapas": out_etapas,
        # recordatorio del encuadre, para que la tarjeta lo muestre (caveat canónico, una vez):
        "encuadre": "Apoyo a la decisión, no consejo médico. Describe y equipa el flujo; no concluye.",
    }


# ─────────────────────────── CLI ───────────────────────────
def _print_resumen(r):
    if r.get("_error"):
        print("⚠️  " + r["_error"]); return
    print("✦ Pipeline biopsia → vacuna  ·  actualizado %s" % (r.get("actualizado") or "—"))
    print("   etapas hechas %d/%d · gates ✓%d ✗%d ·%d —%d · etapa actual: %s" % (
        r["etapas_hechas"], r["n_etapas"], r["gates_ok"], r["gates_fallo"],
        r["gates_pendiente"], r["gates_na"], r.get("etapa_actual") or "—"))
    for e in r["etapas"]:
        print("  %s %s · %s  (gates ✓%d ✗%d ·%d)" % (
            ETAPA_EMOJI.get(e["estado"], "·"), e["id"], e["nombre"],
            e["gates_ok"], e["gates_fallo"], e["gates_pendiente"]))
        for g in e["gates"]:
            print("       %s %-16s %s" % (GATE_EMOJI.get(g["estado"], "·"), g["id"], g["umbral"]))
    print("   " + r["encuadre"])


def main(argv):
    try:
        return _main(argv)
    except (ValueError, KeyError, RuntimeError) as e:
        # Error de usuario / muro: mensaje limpio, no un stack trace (el CLI no es teatro).
        print("✗ %s" % e, file=sys.stderr)
        return 1


def _main(argv):
    if not argv or argv[0] in ("estado", "resumen"):
        _print_resumen(resumen()); return 0
    cmd = argv[0]
    if cmd == "init":
        init(forzar=("--forzar" in argv))
        print("✓ tablero sembrado (6 etapas + gates) en %s" % PV)
        _print_resumen(resumen()); return 0
    if cmd == "once":
        print(json.dumps(resumen(), ensure_ascii=False, indent=2)); return 0
    if cmd == "set-etapa" and len(argv) >= 3:
        nota = None
        if "--nota" in argv:
            nota = argv[argv.index("--nota") + 1]
        e = set_etapa(argv[1], argv[2], nota=nota)
        print("✓ etapa %s → %s" % (e["id"], e["estado"])); return 0
    if cmd == "set-gate" and len(argv) >= 4:
        nota = None
        if "--nota" in argv:
            nota = argv[argv.index("--nota") + 1]
        g = set_gate(argv[1], argv[2], argv[3], nota=nota)
        print("✓ gate %s/%s → %s" % (argv[1].upper(), g["id"], g["estado"])); return 0
    print(__doc__.split("Uso")[1] if "Uso" in __doc__ else "uso: init | estado | once | set-etapa | set-gate")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
