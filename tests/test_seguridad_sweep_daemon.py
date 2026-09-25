#!/usr/bin/env python3
"""test_seguridad_sweep_daemon.py — el barrido de seguridad DESACOPLADO de auto-mejora (C, 2/7/26),
con BRECHA vs HIGIENE (14/7/26 — corrige la recaída del 13-jul en que `audit_comites`, un check de
papeleo, apagó el lazo entero durante 7,5h sin brecha real; revisado tras devolución del comité de
verificación, mismo día, sobre el contrato roto de run_checks()).

Verifica: (1) verde → heartbeat 'ok', sin código rojo, sin aviso; (2) BRECHA en rojo → código rojo
disparado + heartbeat 'critico_bloqueado', exit 1; (3) solo HIGIENE en rojo (sin brecha) → NUNCA
código rojo, SÍ aviso operativo (errores.registrar), heartbeat 'higiene_pendiente', exit 3; (4) el
propio harness se cae (excepción al importar o ejecutar run_checks) → aviso OPERATIVO (NO código
rojo) + heartbeat 'fallo', exit 2; (5) CONTRATO ROTO (C2): run_checks() NO lanza pero devuelve algo
que no es un dict con "brecha" (None, o un dict sin esa clave) → FAIL-CLOSED: se trata como BRECHA
(código rojo, exit 1), NUNCA como verde por omisión de clave — el agujero que el comité de
verificación encontró en la primera versión de este fix (`resultado.get("brecha") or []` vivía
fuera de cualquier validación). Aislado: monkeypatchea seguridad_sweep.run_checks,
codigo_rojo.trigger y errores.registrar — nunca corre el sweep real ni toca HALT/Telegram.
"""
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="ssd_test_")
os.environ["BTP_STATE_DIR"] = _TMP

import seguridad_sweep_daemon as ssd  # noqa: E402

ssd.STATE = _TMP
ssd.HB_DIR = os.path.join(_TMP, "heartbeat")

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _leer_hb():
    p = os.path.join(ssd.HB_DIR, "seguridad-sweep.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _clear():
    if os.path.isdir(ssd.HB_DIR):
        for f in os.listdir(ssd.HB_DIR):
            os.remove(os.path.join(ssd.HB_DIR, f))


def _stub_modulo(nombre, **atributos):
    """Inyecta un módulo falso en sys.modules para que 'import X' dentro de run() lo recoja."""
    m = types.ModuleType(nombre)
    for k, v in atributos.items():
        setattr(m, k, v)
    sys.modules[nombre] = m
    return m


def _resultado(brecha=None, higiene=None):
    return {"brecha": brecha or [], "higiene": higiene or [], "n_cajas": "",
            "gitleaks_omitido": False, "detalle": []}


def main():
    # 1. Sweep VERDE (sin brecha ni higiene) → heartbeat 'ok', sin código rojo, sin aviso.
    _clear()
    disparos_rojo = []
    errores_registrados = []
    _stub_modulo("seguridad_sweep", run_checks=lambda: _resultado())
    _stub_modulo("codigo_rojo", trigger=lambda motivo, detalle="": disparos_rojo.append((motivo, detalle)))
    _stub_modulo("errores", OPERATIVO="operativo",
                registrar=lambda **kw: errores_registrados.append(kw))
    rc = ssd.run()
    ok(rc == 0, "sweep verde → exit 0")
    ok(disparos_rojo == [], "sweep verde → NO dispara código rojo")
    ok(errores_registrados == [], "sweep verde → NO registra ningún aviso")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "ok", "sweep verde → heartbeat 'ok'")

    # 2. BRECHA en rojo (con o sin higiene también en rojo) → código rojo + 'critico_bloqueado'.
    #    Es la MISMA severidad de siempre: una brecha real para todo el lazo, exit 1.
    _clear()
    disparos_rojo.clear()
    errores_registrados.clear()
    _stub_modulo("seguridad_sweep", run_checks=lambda: _resultado(
        brecha=[("muro · fuga (choke-point)", "detalle simulado de fuga")],
        higiene=[("comités · agentes↔registro", "ceci sin registrar")],
    ))
    rc = ssd.run()
    ok(rc == 1, "BRECHA en rojo → exit 1")
    ok(len(disparos_rojo) == 1, "BRECHA en rojo → dispara código rojo exactamente una vez")
    ok("BRECHA" in disparos_rojo[0][0] or "seguridad" in disparos_rojo[0][0].lower(),
       "el motivo del código rojo nombra el barrido de seguridad / BRECHA")
    ok("muro · fuga" in disparos_rojo[0][1], "el detalle del código rojo nombra el check de BRECHA que falló")
    # La EVIDENCIA, no solo el nombre (31-jul-26). El informe decía «gitleaks · secretos en rojo»
    # ocho veces entre el 26 y el 27-jul y, al leerlo después, no había forma de saber si fue una
    # fuga real o un falso positivo — que es LA pregunta para decidir si se reanuda. La salida del
    # check ya la devolvía run_checks(); la rama de BRECHA la tiraba (la de higiene, menos grave,
    # sí la guardaba). Un cortafuegos que no se puede auditar acaba ignorándose.
    ok("detalle simulado de fuga" in disparos_rojo[0][1],
       "el informe lleva la SALIDA del check, no solo su nombre (si no, no se puede auditar)")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "critico_bloqueado",
       "BRECHA en rojo → heartbeat 'critico_bloqueado'")

    # 3. SOLO HIGIENE en rojo (sin ninguna brecha) → NUNCA código rojo, SÍ aviso operativo,
    #    heartbeat distinto, exit 3. Este es el caso exacto de la recaída del 13-jul:
    #    `audit_comites` solo (agentes sin registrar), cero brechas reales.
    _clear()
    disparos_rojo.clear()
    errores_registrados.clear()
    _stub_modulo("seguridad_sweep", run_checks=lambda: _resultado(
        higiene=[("comités · agentes↔registro", "ceci, tipografia sin registrar")],
    ))
    rc = ssd.run()
    ok(rc == 3, "solo HIGIENE en rojo → exit 3 (no 1)")
    ok(disparos_rojo == [], "solo HIGIENE en rojo → NUNCA dispara código rojo (no HALT)")
    ok(len(errores_registrados) == 1, "solo HIGIENE en rojo → SÍ registra un aviso")
    if errores_registrados:
        kw = errores_registrados[0]
        ok(kw.get("severidad") == "operativo", "el aviso de higiene usa severidad OPERATIVO (aviso, no HALT)")
        ok("HIGIENE" in kw.get("error", "") or "higiene" in kw.get("error", "").lower(),
           "el aviso nombra que es HIGIENE, no brecha")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "higiene_pendiente",
       "solo HIGIENE en rojo → heartbeat 'higiene_pendiente' (distinto de 'critico_bloqueado')")

    # 4a. HARNESS caído: run_checks() lanza una excepción (no un resultado con rojo)
    #     → aviso OPERATIVO, heartbeat 'fallo', y NUNCA código rojo (esto es fontanería, no brecha).
    _clear()
    disparos_rojo.clear()
    errores_registrados.clear()

    def _explota():
        raise RuntimeError("boom: seguridad_sweep petó por su cuenta")

    _stub_modulo("seguridad_sweep", run_checks=_explota)
    rc = ssd.run()
    ok(rc == 2, "harness caído (excepción al ejecutar) → exit 2")
    ok(disparos_rojo == [], "harness caído → NUNCA dispara código rojo (no es una brecha detectada)")
    ok(len(errores_registrados) == 1 and errores_registrados[0].get("severidad") == "operativo",
       "harness caído → registra error OPERATIVO")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "fallo", "harness caído → heartbeat 'fallo'")

    # 4b. HARNESS caído al IMPORTAR seguridad_sweep (módulo roto/faltante) → mismo tratamiento.
    _clear()
    disparos_rojo.clear()
    errores_registrados.clear()
    if "seguridad_sweep" in sys.modules:
        del sys.modules["seguridad_sweep"]
    orig_import = ssd.__builtins__["__import__"] if isinstance(ssd.__builtins__, dict) else ssd.__builtins__.__import__

    def _import_roto(name, *a, **k):
        if name == "seguridad_sweep":
            raise ImportError("módulo seguridad_sweep no disponible (simulado)")
        return orig_import(name, *a, **k)

    import builtins
    builtins.__import__ = _import_roto
    try:
        rc = ssd.run()
    finally:
        builtins.__import__ = orig_import
    ok(rc == 2, "harness caído (import roto) → exit 2")
    ok(disparos_rojo == [], "import roto → NUNCA dispara código rojo")
    ok(len(errores_registrados) == 1 and errores_registrados[0].get("severidad") == "operativo",
       "import roto → registra error OPERATIVO")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "fallo", "import roto → heartbeat 'fallo'")

    # 5a. CONTRATO ROTO (C2): run_checks() NO lanza, devuelve None. FAIL-CLOSED → BRECHA
    #     (código rojo), exit 1. Antes de esta corrección, `resultado.get("brecha")`
    #     habría reventado con AttributeError FUERA del try/except → proceso muerto sin
    #     avisar ni disparar código rojo.
    _clear()
    disparos_rojo.clear()
    errores_registrados.clear()
    _stub_modulo("seguridad_sweep", run_checks=lambda: None)
    rc = ssd.run()
    ok(rc == 1, "run_checks() devuelve None → exit 1 (BRECHA, fail-closed)")
    ok(len(disparos_rojo) == 1, "run_checks() devuelve None → SÍ dispara código rojo")
    ok("contrato" in disparos_rojo[0][0].lower(), "el motivo del código rojo nombra el contrato roto")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "critico_bloqueado",
       "run_checks() devuelve None → heartbeat 'critico_bloqueado' (no 'fallo')")

    # 5b. CONTRATO ROTO (C2): run_checks() devuelve un dict SIN la clave "brecha". Antes
    #     de esta corrección, `resultado.get("brecha") or []` leía esto como "sin brecha"
    #     → verde silencioso (fail-OPEN por omisión de clave). Ahora: BRECHA, exit 1.
    _clear()
    disparos_rojo.clear()
    errores_registrados.clear()
    _stub_modulo("seguridad_sweep", run_checks=lambda: {"higiene": [], "n_cajas": ""})
    rc = ssd.run()
    ok(rc == 1, "dict sin clave 'brecha' → exit 1 (BRECHA, fail-closed, no verde por omisión)")
    ok(len(disparos_rojo) == 1, "dict sin clave 'brecha' → SÍ dispara código rojo")
    hb = _leer_hb()
    ok(hb is not None and hb.get("estado") == "critico_bloqueado",
       "dict sin clave 'brecha' → heartbeat 'critico_bloqueado'")

    # limpieza de los stubs inyectados, para no contaminar otros tests del runner
    for nombre in ("seguridad_sweep", "codigo_rojo", "errores"):
        sys.modules.pop(nombre, None)

    print("RESULTADO seguridad_sweep_daemon: %d OK, %d fallos" % (_pass, _fail))
    print("✅ SEGURIDAD-SWEEP-DAEMON EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
