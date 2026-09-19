#!/usr/bin/env python3
"""test_pipeline_vacuna.py — tablero del flujo biopsia → vacuna.

Cubre: siembra idempotente de las 6 etapas + gates; cambios de estado con enums CERRADOS
(fail-closed); el GUARDIA ANTI-PII (muro de datos: secuencias/alelos tipados rechazados);
y un TEST DE MURO de egress (el JSON guardado NUNCA contiene crudo/PII por construcción).
Aísla todo en un tmp; no toca el repo real."""
import os
import sys
import json
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_pipeline_vacuna_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pipeline_vacuna as pv  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def expect_error(name, fn, *a, **kw):
    """Pasa si fn lanza ValueError/KeyError (validación que DEBE fallar)."""
    try:
        fn(*a, **kw)
    except (ValueError, KeyError):
        check(name, True)
        return
    check(name, False)


def main():
    # ── siembra ────────────────────────────────────────────────────────────────
    d = pv.init()
    check("init siembra 6 etapas", len(d["etapas"]) == 6)
    check("etapas son A-F", [e["id"] for e in d["etapas"]] == ["A", "B", "C", "D", "E", "F"])
    check("cada etapa empieza pendiente", all(e["estado"] == "pendiente" for e in d["etapas"]))
    check("todas las etapas traen gates", all(e.get("gates") for e in d["etapas"]))
    # gate canónico clave: DV200 en la etapa B con umbral GENÉRICO (no un valor del paciente)
    b = next(e for e in d["etapas"] if e["id"] == "B")
    dv = next((g for g in b["gates"] if g["id"] == "dv200"), None)
    check("gate DV200 presente en B", dv is not None)
    check("umbral DV200 es genérico (>= 30 %)", dv and "30" in dv["umbral"] and "%" in dv["umbral"])

    # ── idempotencia: init no machaca el progreso humano ─────────────────────────
    pv.set_etapa("A", "hecho")
    pv.set_gate("B", "dv200", "ok")
    d2 = pv.init()  # segundo init sin forzar
    a2 = next(e for e in d2["etapas"] if e["id"] == "A")
    check("init es idempotente: NO borra el progreso", a2["estado"] == "hecho")
    dv2 = next(g for g in next(e for e in d2["etapas"] if e["id"] == "B")["gates"] if g["id"] == "dv200")
    check("init idempotente: gate ya puesto se conserva", dv2["estado"] == "ok")
    # forzar SÍ resiembra
    d3 = pv.init(forzar=True)
    a3 = next(e for e in d3["etapas"] if e["id"] == "A")
    check("init --forzar resiembra a pendiente", a3["estado"] == "pendiente")

    # ── enums CERRADOS (fail-closed, anti-inyección) ─────────────────────────────
    expect_error("estado de etapa inválido se RECHAZA", pv.set_etapa, "A", "volando")
    expect_error("estado de gate inválido se RECHAZA", pv.set_gate, "B", "dv200", "quizas")
    expect_error("etapa inexistente se RECHAZA", pv.set_etapa, "Z", "hecho")
    expect_error("gate inexistente se RECHAZA", pv.set_gate, "B", "no_existe", "ok")
    # enum válido SÍ pasa, y normaliza la etapa en minúscula
    g = pv.set_gate("b", "dv200", "fallo")
    check("set_gate acepta etapa en minúscula", g["estado"] == "fallo")
    check("estados de gate válidos cubren ok/fallo/pendiente/na",
          set(pv.ESTADOS_GATE) == {"pendiente", "ok", "fallo", "na"})

    # ── GUARDIA ANTI-PII (muro de datos) ─────────────────────────────────────────
    expect_error("nota con secuencia de nucleótidos se RECHAZA",
                 pv.set_gate, "B", "dv200", "ok", nota="el read fue ACGTACGTACGTACGT")
    expect_error("nota con alelo HLA tipado concreto se RECHAZA",
                 pv.set_gate, "D", "hla_tipado", "ok", nota="resultó HLA-A*02:01")
    expect_error("nota demasiado larga se RECHAZA (no es un informe)",
                 pv.set_etapa, "C", "en_curso", nota="x" * 300)
    # nota GENÉRICA y corta SÍ pasa (umbral/observación operativa sin dato del paciente)
    g2 = pv.set_gate("B", "dv200", "ok", nota="dentro de umbral, revisado por patologia")
    check("nota genérica corta SÍ se acepta", g2.get("nota", "").startswith("dentro de umbral"))

    # ── resumen (lo que consume el Observatorio) ─────────────────────────────────
    pv.init(forzar=True)
    pv.set_etapa("A", "hecho")
    pv.set_gate("A", "cores", "ok")
    pv.set_gate("A", "diana", "ok")
    pv.set_etapa("B", "en_curso")
    pv.set_gate("B", "fastqc", "fallo")
    pv.set_gate("E", "ms", "na")
    r = pv.resumen()
    check("resumen: 6 etapas", r["n_etapas"] == 6)
    check("resumen: 1 etapa hecha", r["etapas_hechas"] == 1)
    check("resumen: etapa actual es B (en curso)", r["etapa_actual"] == "B")
    check("resumen: cuenta gates ok", r["gates_ok"] == 2)
    check("resumen: cuenta gates fallo", r["gates_fallo"] == 1)
    check("resumen: cuenta gates na", r["gates_na"] == 1)
    check("resumen: lleva el encuadre no-diagnóstico", "no consejo médico" in r.get("encuadre", ""))
    check("resumen: cita la procedencia (doc canónico)", "Pipeline-Neoantigenos" in r.get("doc", ""))

    # ── TEST DE MURO: el JSON guardado NO contiene crudo/PII ─────────────────────
    # (egress cero verificable: aunque alguien intente, el guardia no deja entrar el dato; y el
    #  esquema solo guarda estado + umbrales genéricos. Barremos el fichero entero.)
    raw = open(pv.PV, encoding="utf-8").read()
    import re
    check("muro: el JSON no contiene secuencias de nucleótidos",
          re.search(r"[ACGT]{12,}", raw) is None)
    check("muro: el JSON no contiene alelos HLA tipados concretos",
          re.search(r"HLA-[A-DRQP]+[0-9]*\*\d{2}:\d{2}", raw) is None)
    # los umbrales genéricos SÍ pueden mencionar 'HLA' o 'DV200' (terminología pública) — verificamos
    # que esa terminología está pero SIN valor del paciente.
    check("muro: terminología pública SÍ presente (HLA, DV200)", "HLA" in raw and "DV200" in raw)

    # ── carga corrupta no asume vacío (no oculta progreso) ───────────────────────
    open(pv.PV, "w", encoding="utf-8").write("{ esto no es json válido")
    dc = pv.load()
    check("carga corrupta → _error (no finge vacío)", bool(dc.get("_error")))
    rc = pv.resumen()
    check("resumen sobre corrupto → _error propagado", bool(rc.get("_error")))

    print("RESULTADO pipeline_vacuna.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ PIPELINE-VACUNA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
