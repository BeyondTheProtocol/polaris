#!/usr/bin/env python3
"""tools/seguridad_sweep.py — barrido de seguridad CONTINUO de toda la constelación.

Filosofía ({{TITULAR}}, 21/6/26): "todo se tiene que replicar en las cajitas-estrella" — pero
HEREDADO, no copiado. Un solo vigía cubre el sistema Y cada caja (como una sola pared
cubre todas las estrellas). "Irán saliendo cosas": una foto fija no basta; esto corre EN
BUCLE (rutina diaria) y a mano cuando se quiera.

Qué barre (todo lo que protege el sistema y, por herencia, cada caja):
  · Suite del MURO (test_fuga, test_muro_fase0, test_halt) — la pared compartida.
  · Choke-point de salida + freno de gasto + cola — la puerta y los frenos compartidos.
  · `audit_constelacion`: los 13 cortafuegos de CADA caja (cobertura por estrella), SIN
    `--strict` — los FAIL de verdad (fuga A5, huérfana/activa-sin-dueño A7/A11, PII A13,
    caja ilegible) ya son FAIL sin `--strict` y bastan para BRECHA.
  · `audit_constelacion --strict`: el mismo auditor pero con WARN→FAIL (plantilla vieja,
    experto sin registrar, placeholder, revisión/caducidad vencidas) — es PAPELEO, no
    seguridad; corre aparte y cuenta como HIGIENE (ver corrección 14/7/26 más abajo).
  · `audit_comites`: agentes ↔ registro.
  · `gitleaks`: secretos en lo versionado (lo que podría salir en un push).

CLASIFICACIÓN (2/7/26 recaída, corregido 14/7/26 — el comité de verificación devolvió la
primera versión de este fix: dos veces un check de HIGIENE ya había apagado el lazo entero,
CERO brechas reales — 11-jul caja zombi/deadlock, 13-jul `audit_comites` con `ceci`/
`tipografia` sin registrar → 7,5h de Vega muda —, y la propia auditoría descubrió una
TERCERA mina idéntica: `audit_constelacion --strict` promueve a FAIL su A14 ("caja
vencida") entre otros WARN de papeleo, así que una caja con `caduca` pasada apagaría el
lazo por fecha vencida, no por brecha. La caja `viaje-{{CIUDAD}}-prueba` estaba a un día de
detonarla — {{TITULAR}} ya la archivó a mano; este fix cierra el agujero ESTRUCTURAL para que
no vuelva a pasar con la siguiente caja que venza). Cada check tiene una CLASE:
  · BRECHA  — seguridad real (muro, choke-point, freno, cola, los FAIL reales de las
    cajas, gitleaks). Si sale en rojo, sigue mereciendo CÓDIGO ROJO exactamente como hoy.
  · HIGIENE — papeleo/completitud (agentes↔registro; el papeleo de las cajas bajo
    `--strict`; gitleaks no instalado — no verificado ≠ verde). Si sale en rojo, hay que
    AVISAR fuerte, pero NUNCA parar el lazo entero por eso.
FAIL-CLOSED: un check nuevo sin clase explícita cuenta como BRECHA por defecto (ver
`_clase()`) — así nadie se cuela como "solo higiene" por olvido.

Verde = sistema y cajas seguros. BRECHA en rojo → la rutina debe ALERTAR FUERTE (código
rojo), igual que siempre. HIGIENE en rojo → avisa, pero no HALT (eso lo decide el
daemon/llamador con `run_checks()`, no este módulo). Solo lee y reporta; no toca nada
hacia fuera. Sin dependencias (stdlib + subprocess).
"""
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable or "python3"

# Clases de seguridad de un check (ver _clase()).
BRECHA = "brecha"     # seguridad real: si falla, código rojo (para todo)
HIGIENE = "higiene"   # papeleo/completitud: si falla, avisa, nunca para el lazo
CLASES_VALIDAS = (BRECHA, HIGIENE)

# (etiqueta, comando, clase). Cada uno debe salir 0 = verde.
# La clase es el 3er campo; si se omite, _clase() la trata como BRECHA (fail-closed).
CHECKS = [
    ("muro · fuga (choke-point)",        ["bash", "tests/test_fuga.sh"],                                     BRECHA),
    ("muro · fase0 endurecido (60)",     [PY, "tests/test_muro_fase0.py"],                                   BRECHA),
    ("muro · HALT (kill-switch)",        ["bash", "tests/test_halt.sh"],                                     BRECHA),
    ("salida · choke-point único",       [PY, "tests/test_salida.py"],                                       BRECHA),
    ("coste · freno de gasto",           [PY, "tests/test_cost_guard.py"],                                   BRECHA),
    ("cola · schema/anti-bomba",         [PY, "tests/test_cola.py"],                                         BRECHA),
    ("CAJAS · 13 cortafuegos (c/u)",     [PY, "tools/audit_constelacion.py", "--quiet"],                     BRECHA),
    ("CAJAS · papeleo (plantilla/placeholder/caducidad)",
                                          [PY, "tools/audit_constelacion.py", "--strict", "--quiet"],         HIGIENE),
    ("comités · agentes↔registro",       [PY, "tools/audit_comites.py"],                                     HIGIENE),
]

# Allowlist EXACTA de las etiquetas de HIGIENE (papeleo/completitud). Es la contraparte
# del fail-closed de `_clase()`: si mañana alguien REBAJA por error un check de BRECHA a
# HIGIENE (p.ej. "muro · fuga"), esta lista NO cambia sola — hay que tocarla a mano, y
# `tests/test_seguridad_sweep.py` compara CHECKS contra ella y se pone en rojo si no
# coinciden. Que un test necesite tocarse para permitir una rebaja es la fricción a
# propósito (memoria: "Comité valida sus sugerencias" — nada se rebaja sin revisión).
ETIQUETAS_HIGIENE_ESPERADAS = frozenset({
    "CAJAS · papeleo (plantilla/placeholder/caducidad)",
    "comités · agentes↔registro",
})


def _clase(check):
    """Clase de seguridad de un check: su 3er campo si es válido; si no, BRECHA por
    defecto. FAIL-CLOSED a propósito: un check nuevo que alguien añada sin pensar en su
    clase nunca se cuela silenciosamente como higiene — hay que REBAJARLO a mano."""
    if len(check) > 2 and check[2] in CLASES_VALIDAS:
        return check[2]
    return BRECHA


def _run(cmd, timeout=300):
    """(ok, salida). Un check que NO TERMINA a tiempo se reintenta UNA vez antes de darlo por
    rojo (25-jul-26).

    Por qué: 'falló' y 'no terminó' no son lo mismo, y aquí se contaban igual. Con varias
    sesiones trabajando a la vez — que es como trabaja {{TITULAR}} — dos baterías pesadas compitiendo
    por la CPU bastan para que un check se pase de los 300 s; si es de clase BRECHA, eso PARA el
    sistema entero por una brecha que no existe. Pasó ese día a las 16:39: `muro · fuga` y
    `salida · choke-point` en rojo, código rojo disparado, bot/dispatcher/healthcheck
    descargados… y cinco minutos después los dos en verde corridos a mano. El reintento cuesta
    minutos; el apagón en falso le cuesta el sistema mientras espera resultados de biopsia.
    El segundo intento va con el DOBLE de margen, porque si el primero se comió el reloj es
    justo cuando la máquina estaba cargada. Si el reintento también expira, ES rojo: se declara
    con su motivo a la vista, nunca en silencio."""
    for intento in (1, 2):
        t = timeout * intento
        try:
            p = subprocess.run(cmd, cwd=REPO, capture_output=True, timeout=t)
            out = (p.stdout.decode("utf-8", "ignore") + p.stderr.decode("utf-8", "ignore"))
            return p.returncode == 0, out.strip()
        except subprocess.TimeoutExpired:
            if intento == 1:
                sys.stderr.write("seguridad_sweep: '%s' no terminó en %ds → reintento con %ds "
                                 "(¿máquina cargada?)\n" % (" ".join(cmd), t, timeout * 2))
                continue
            return False, ("NO TERMINÓ: el check superó %ds en dos intentos seguidos. Esto NO "
                           "prueba una brecha: prueba que no se pudo comprobar. Míralo a mano "
                           "antes de dar por buena una fuga (bash %s)." % (t, " ".join(cmd)))
        except Exception as e:
            return False, repr(e)


def _gitleaks():
    if not shutil.which("gitleaks"):
        return None, "gitleaks no instalado (omitido)"
    # Modo git (por defecto): escanea lo VERSIONADO (lo que viajaría en un push). Redacta
    # los hallazgos. exit 0 = sin fugas, 1 = fugas encontradas. (gitleaks 8.x)
    return _run(["gitleaks", "detect", "--redact"])


def run_checks():
    """Corre TODOS los checks (CHECKS + gitleaks) y devuelve el veredicto ESTRUCTURADO,
    ya separado por clase. No imprime nada y no decide política (HALT o no) — eso es
    de `main()` para el humano y del daemon para el lazo 24/7. Fuente de verdad única
    para que ningún consumidor tenga que re-derivar la clasificación de un exit code.

    Devuelve:
      {"brecha": [(nombre, cola_recortada), ...],   # checks de BRECHA en rojo
       "higiene": [(nombre, cola_recortada), ...],  # checks de HIGIENE en rojo
       "n_cajas": str,                               # línea de audit_constelacion, si la hay
       "gitleaks_omitido": bool,                      # True si gitleaks no está instalado
       "detalle": [(nombre, clase, ok, salida_completa), ...]}  # TODOS los checks corridos
    """
    rojo_brecha, rojo_higiene, detalle = [], [], []
    n_cajas = ""
    for check in CHECKS:
        name, cmd = check[0], check[1]
        clase = _clase(check)
        ok, out = _run(cmd)
        if "audit_constelacion" in " ".join(cmd):
            n_cajas = next((ln for ln in out.splitlines() if "constelaci" in ln.lower()), "")
        detalle.append((name, clase, ok, out))
        if not ok:
            (rojo_brecha if clase == BRECHA else rojo_higiene).append((name, out[-280:]))

    gitleaks_omitido = False
    ok_gl, out_gl = _gitleaks()
    if ok_gl is None:
        # No instalado = NO VERIFICADO, no "verde". Un check de BRECHA que no se pudo
        # correr no cuenta como aprobado (eso era un verde silencioso: ni brecha ni
        # higiene, nadie se enteraba). Cuenta como HIGIENE — avisa sin parar el lazo,
        # porque la ausencia del binario es un problema de entorno, no una fuga
        # confirmada (instrucción explícita del comité de verificación, 14/7/26).
        gitleaks_omitido = True
        rojo_higiene.append(("gitleaks · omitido (no instalado, sin verificar)",
                              "gitleaks no está en PATH; el check de secretos no corrió"))
    else:
        # gitleaks siempre es BRECHA (secretos en lo versionado = fuga real).
        detalle.append(("gitleaks · secretos en lo versionado", BRECHA, ok_gl, out_gl))
        if not ok_gl:
            rojo_brecha.append(("gitleaks · secretos", (out_gl or "")[-280:]))

    return {"brecha": rojo_brecha, "higiene": rojo_higiene, "n_cajas": n_cajas,
            "gitleaks_omitido": gitleaks_omitido, "detalle": detalle}


def main(argv):
    quiet = "--quiet" in argv
    print("🛡️  barrido de seguridad de la constelación — %s" % time.strftime("%Y-%m-%d %H:%M"))

    r = run_checks()

    for name, clase, ok, out in r["detalle"]:
        if not quiet or not ok:
            if ok:
                marca = "✅"
            elif clase == BRECHA:
                marca = "⛔"
            else:
                marca = "⚠️"
            print("  %s %s" % (marca, name))
    if r["gitleaks_omitido"]:
        print("  ➖ gitleaks · secretos (omitido, no instalado)")
    if r["n_cajas"]:
        print("  · %s" % r["n_cajas"].strip())

    if r["brecha"]:
        print("\n❌ %d EN ROJO — BRECHA (seguridad real, revisar YA):" % len(r["brecha"]))
        for nombre, cola in r["brecha"]:
            print("  · %s\n      %s" % (nombre, cola.replace("\n", " | ")[:220]))
    if r["higiene"]:
        print("\n⚠️  %d EN ROJO — HIGIENE (papeleo/completitud; avisa, NO para el sistema):" % len(r["higiene"]))
        for nombre, cola in r["higiene"]:
            print("  · %s\n      %s" % (nombre, cola.replace("\n", " | ")[:220]))

    if r["brecha"]:
        return 1
    if r["higiene"]:
        return 3
    print("\n✅ TODO EN VERDE — el sistema y CADA caja, seguros.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
