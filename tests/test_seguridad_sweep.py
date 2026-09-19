#!/usr/bin/env python3
"""test_seguridad_sweep.py — clasificación BRECHA vs HIGIENE de seguridad_sweep.py (14/7/26,
revisado tras devolución del comité de verificación).

Por qué existe: hasta hoy no había NINGÚN test directo de seguridad_sweep.py (solo del
daemon, que mockeaba `main()` entero). La recaída del 13-jul (un check de HIGIENE —
`audit_comites`— tumbó el lazo 7,5h sin brecha real) nació aquí: la lista CHECKS trataba
TODO fallo como si fuera brecha. Este test verifica, sobre el módulo REAL (sin tocar
subprocess de verdad — se monkeypatchea `_run`/`CHECKS`/`_gitleaks`):
  1. Un check clasificado "higiene" en rojo NO cuenta como brecha (`run_checks()`).
  2. Un check clasificado "brecha" en rojo SÍ cuenta como brecha.
  3. FAIL-CLOSED: un check SIN clase explícita (2-tupla vieja) que falla se trata como
     BRECHA por defecto — nunca se cuela como higiene por olvido.
  4. `main()` devuelve 1 si hay brecha, 3 si SOLO hay higiene, 0 si todo verde.
  5. ALLOWLIST EXACTA (C3, comité de verificación 14/7/26): el fallo simétrico al del
     13-jul es que alguien REBAJE por error un check real (p.ej. "muro · fuga") de
     BRECHA a HIGIENE — los tests de arriba pasarían igual (12/12) y una fuga real
     dejaría de parar el sistema. Este test fija el conjunto EXACTO de etiquetas HIGIENE
     contra `seguridad_sweep.ETIQUETAS_HIGIENE_ESPERADAS` y el número de filas BRECHA
     esperado: cualquier rebaja (o subida) futura de un check se sale de esta allowlist
     y pone el test en rojo, obligando a revisión humana explícita.
  6. `audit_constelacion` corre DOS veces con clases distintas (C1): sin `--strict` es
     BRECHA (los FAIL reales: fuga A5, huérfana/activa-sin-dueño A7/A11, PII A13, caja
     ilegible ya son FAIL sin `--strict`); con `--strict` es HIGIENE (WARN→FAIL de
     papeleo: plantilla vieja, experto sin registrar, placeholder, caducidad/revisión
     vencidas — la MISMA clase de fallo que apagó el lazo el 11-jul y el 13-jul, solo
     que esta vez viviendo dentro de audit_constelacion en vez de audit_comites).

CANARIO DEL AUTOR (verificado a mano, no solo declarado): rompí adrede la clasificación
—marcando la fila real de `audit_comites` como BRECHA, y por separado subiendo la fila
"CAJAS · papeleo" a BRECHA— y confirmé que el test 5 (allowlist) se pone en ROJO en
ambos casos. Luego restauré el fix y volvió a verde. Ver informe de la sesión para el
log de esa verificación.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import seguridad_sweep as ss  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# run_checks() hace `" ".join(cmd)` para detectar audit_constelacion, así que el "cmd"
# de cada check falso debe seguir siendo una lista de strings (no un bool a pelo).
_CMD_OK = ["__fake_ok__"]
_CMD_ROJO = ["__fake_rojo__"]


def _check(nombre, verde, *clase):
    """Construye una fila de CHECKS de prueba: (nombre, cmd_falso, [clase])."""
    cmd = _CMD_OK if verde else _CMD_ROJO
    return (nombre, cmd) + tuple(clase)


def _con_checks(checks, gitleaks_ok=True):
    """Context manager mínimo: sustituye CHECKS/_run/_gitleaks, corre, restaura."""
    class _Ctx:
        def __enter__(self):
            self._checks_orig = ss.CHECKS
            self._run_orig = ss._run
            self._gitleaks_orig = ss._gitleaks
            ss.CHECKS = checks
            ss._run = lambda cmd, timeout=300: (cmd == _CMD_OK, "" if cmd == _CMD_OK else "rojo simulado")
            ss._gitleaks = lambda: (gitleaks_ok, "")
            return ss

        def __exit__(self, *a):
            ss.CHECKS = self._checks_orig
            ss._run = self._run_orig
            ss._gitleaks = self._gitleaks_orig
    return _Ctx()


def main():
    # 1. Check de HIGIENE en rojo (comités↔registro simulado) → NO es brecha.
    with _con_checks([_check("comités · agentes↔registro", False, ss.HIGIENE)]):
        r = ss.run_checks()
        ok(r["brecha"] == [], "higiene en rojo → NO aparece en brecha")
        ok(len(r["higiene"]) == 1, "higiene en rojo → SÍ aparece en higiene")

    # 2. Check de BRECHA en rojo (muro simulado) → SÍ es brecha.
    with _con_checks([_check("muro · fuga (choke-point)", False, ss.BRECHA)]):
        r = ss.run_checks()
        ok(len(r["brecha"]) == 1, "brecha en rojo → SÍ aparece en brecha")
        ok(r["higiene"] == [], "brecha en rojo → NO aparece en higiene")

    # 3. FAIL-CLOSED: check SIN clase explícita (2-tupla, como antes de este fix) que
    #    falla → cuenta como BRECHA por defecto, nunca se cuela como higiene.
    with _con_checks([_check("check nuevo sin clasificar", False)]):
        r = ss.run_checks()
        ok(len(r["brecha"]) == 1, "check sin clase (2-tupla) en rojo → BRECHA por defecto (fail-closed)")
        ok(r["higiene"] == [], "check sin clase en rojo → NUNCA se cuela como higiene")

    # 3b. Lo mismo para _clase() directamente (unidad más pequeña).
    ok(ss._clase(("x", _CMD_OK)) == ss.BRECHA, "_clase() de una 2-tupla → BRECHA por defecto")
    ok(ss._clase(("x", _CMD_OK, "algo-que-no-existe")) == ss.BRECHA,
       "_clase() con una clase inválida → BRECHA por defecto (fail-closed, no confía en basura)")
    ok(ss._clase(("x", _CMD_OK, ss.HIGIENE)) == ss.HIGIENE, "_clase() respeta HIGIENE cuando es válida")

    # 4. main(): 0 si todo verde, 3 si SOLO higiene en rojo, 1 si hay brecha (con o sin higiene).
    checks_verde = [_check("muro · ok", True, ss.BRECHA), _check("comités · ok", True, ss.HIGIENE)]
    checks_solo_higiene = [_check("muro · ok", True, ss.BRECHA), _check("comités · rojo", False, ss.HIGIENE)]
    checks_con_brecha = [_check("muro · rojo", False, ss.BRECHA), _check("comités · rojo", False, ss.HIGIENE)]

    with _con_checks(checks_verde):
        ok(ss.main(["--quiet"]) == 0, "main(): todo verde → exit 0")
    with _con_checks(checks_solo_higiene):
        ok(ss.main(["--quiet"]) == 3, "main(): solo higiene en rojo → exit 3 (no 1)")
    with _con_checks(checks_con_brecha):
        ok(ss.main(["--quiet"]) == 1, "main(): hay brecha (con o sin higiene) → exit 1")

    # 5. ALLOWLIST EXACTA sobre el CHECKS real (no mockeado): protege contra el fallo
    #    SIMÉTRICO al del 13-jul — rebajar por error un check de BRECHA a HIGIENE. Si
    #    alguien toca CHECKS (añade, quita, o cambia la clase de una fila) sin tocar
    #    ETIQUETAS_HIGIENE_ESPERADAS a la vez, este bloque se pone en rojo.
    etiquetas_higiene_reales = {c[0] for c in ss.CHECKS if ss._clase(c) == ss.HIGIENE}
    ok(etiquetas_higiene_reales == ss.ETIQUETAS_HIGIENE_ESPERADAS,
       "las etiquetas HIGIENE del CHECKS real son EXACTAMENTE las esperadas (ni una de más, "
       "ni una de menos) — real=%r esperado=%r" % (etiquetas_higiene_reales, ss.ETIQUETAS_HIGIENE_ESPERADAS))

    n_brecha_reales = sum(1 for c in ss.CHECKS if ss._clase(c) == ss.BRECHA)
    ok(n_brecha_reales == len(ss.CHECKS) - len(ss.ETIQUETAS_HIGIENE_ESPERADAS),
       "el número de filas BRECHA en CHECKS es el esperado (%d)" % n_brecha_reales)

    # 5b. Muestras nombradas: los checks de seguridad "de bandera" (muro/salida/gitleaks)
    #     deben seguir siendo BRECHA por nombre explícito, no solo por conteo — si el
    #     conteo cuadra mágicamente tras un despiste (una fila BRECHA→HIGIENE compensada
    #     por otra HIGIENE→BRECHA), esto lo caza igual.
    for etiqueta_brecha in ("muro · fuga (choke-point)", "muro · HALT (kill-switch)",
                            "salida · choke-point único", "cola · schema/anti-bomba"):
        fila = next((c for c in ss.CHECKS if c[0] == etiqueta_brecha), None)
        ok(fila is not None and ss._clase(fila) == ss.BRECHA,
           "%r sigue clasificada BRECHA" % etiqueta_brecha)

    # 6. audit_constelacion corre dos veces con clases distintas (sin --strict = BRECHA,
    #    con --strict = HIGIENE) — confirma que las dos filas existen y con la clase que toca.
    sin_strict = next((c for c in ss.CHECKS
                        if any("audit_constelacion" in p for p in c[1]) and "--strict" not in c[1]), None)
    con_strict = next((c for c in ss.CHECKS
                        if any("audit_constelacion" in p for p in c[1]) and "--strict" in c[1]), None)
    ok(sin_strict is not None and ss._clase(sin_strict) == ss.BRECHA,
       "audit_constelacion SIN --strict está en CHECKS y es BRECHA")
    ok(con_strict is not None and ss._clase(con_strict) == ss.HIGIENE,
       "audit_constelacion CON --strict está en CHECKS y es HIGIENE (papeleo)")

    # 7. gitleaks ausente (C4): ya NO es un verde silencioso — cuenta como HIGIENE.
    with _con_checks([_check("muro · ok", True, ss.BRECHA)], gitleaks_ok=None):
        r = ss.run_checks()
        ok(r["gitleaks_omitido"] is True, "gitleaks ausente → gitleaks_omitido=True")
        ok(r["brecha"] == [], "gitleaks ausente → NO cuenta como brecha")
        ok(len(r["higiene"]) == 1 and "gitleaks" in r["higiene"][0][0],
           "gitleaks ausente → SÍ cuenta como higiene (avisa, no para el lazo)")

    # 8. TIMEOUT ≠ BRECHA (25-jul-26). El día del falso código rojo, dos checks pesados se
    #    pasaron del reloj con la máquina cargada y el sistema entero se paró por una brecha
    #    que no existía. Ahora un check que no termina se reintenta UNA vez con el doble de
    #    margen; si el reintento también expira es rojo, pero con un motivo que dice que NO se
    #    pudo comprobar, no que haya una fuga.
    import time as _t
    _t0 = _t.time()
    _ok_lento, _out_lento = ss._run(["sleep", "5"], timeout=1)
    _dur = _t.time() - _t0
    ok(_ok_lento is False, "el que no termina ni al reintento sigue siendo rojo")
    ok(_dur >= 2.9, "hubo reintento de verdad (1s + 2s), no un solo intento")
    ok("NO TERMINÓ" in _out_lento and "no prueba una brecha" in _out_lento.lower(),
       "el motivo distingue 'no se pudo comprobar' de 'hay una fuga'")
    ok(ss._run(["sleep", "1.5"], timeout=1)[0] is True,
       "el que solo necesitaba un poco más de margen se salva en el reintento (era el falso positivo)")
    _t0 = _t.time()
    _ok_falla, _ = ss._run(["false"], timeout=5)
    ok(_ok_falla is False and (_t.time() - _t0) < 1.0,
       "un fallo REAL sigue siendo rojo al primer intento (no se reintenta, no se tapa)")

    print("RESULTADO seguridad_sweep: %d OK, %d fallos" % (_pass, _fail))
    print("✅ SEGURIDAD-SWEEP EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
