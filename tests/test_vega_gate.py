#!/usr/bin/env python3
"""tests/test_vega_gate.py — el gate determinista de Vega ahorra sin perder fiabilidad.

Verifica el contrato:
  · primera pasada / marcador corrupto / fuente ilegible → NOVEDAD (fail-safe: corre el LLM).
  · nada cambió → sin novedad (rc 1, coste 0).
  · correo nuevo / hilo o plazo que cambió → NOVEDAD + delta con SOLO lo que cambió.
  · 0 red, 0 tokens: el gate jamás importa nada de red ni manda contenido a una IA.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "tools", "vega_gate.py")
PY = sys.executable or "python3"

ok = 0
fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("  ✗ " + msg)


def run(env, *args):
    """Corre vega_gate.py; devuelve (rc, stdout)."""
    e = dict(os.environ)
    e.update(env)
    p = subprocess.run([PY, GATE, *args], capture_output=True, text=True, env=e)
    return p.returncode, p.stdout.strip()


def main():
    tmp = tempfile.mkdtemp()
    state = os.path.join(tmp, "state")
    fuente = os.path.join(tmp, "fuente")
    bandeja = os.path.join(tmp, "BANDEJA.md")
    os.makedirs(os.path.join(state, "correo"), exist_ok=True)
    env = {"BTP_STATE_DIR": state, "BTP_FUENTE_DIR": fuente, "BTP_BANDEJA": bandeja}
    buzon = os.path.join(state, "correo", "buzon.json")
    mark = os.path.join(state, "vega_gate", "last_seen.json")

    # 1. primera pasada → novedad (rc 0)
    rc, out = run(env, "check")
    check(rc == 0, "primera pasada debe ser NOVEDAD (rc 0), fue rc=%d" % rc)

    # sella la línea base
    run(env, "mark")

    # 2. nada cambió → sin novedad (rc 1)
    rc, _ = run(env, "check")
    check(rc == 1, "sin cambios debe ser SIN NOVEDAD (rc 1), fue rc=%d" % rc)

    # 3. aparece correo nuevo → novedad (rc 0) + delta menciona el buzón
    with open(buzon, "w") as f:
        json.dump({"mensajes": [{"id": "m1"}]}, f)
    rc, _ = run(env, "check")
    check(rc == 0, "correo nuevo debe ser NOVEDAD (rc 0), fue rc=%d" % rc)
    _, delta = run(env, "delta")
    check("buz" in delta.lower(), "el delta debe mencionar el buzón; fue: %r" % delta)

    # tras sellar, sin más cambios → sin novedad
    run(env, "mark")
    rc, _ = run(env, "check")
    check(rc == 1, "tras sellar el correo, sin cambios = SIN NOVEDAD (rc 1), fue rc=%d" % rc)

    # 4. llega OTRO correo → novedad
    with open(buzon, "w") as f:
        json.dump({"mensajes": [{"id": "m1"}, {"id": "m2"}]}, f)
    rc, _ = run(env, "check")
    check(rc == 0, "segundo correo debe ser NOVEDAD (rc 0), fue rc=%d" % rc)

    # 5. marcador corrupto → fail-safe novedad
    run(env, "mark")
    with open(mark, "w") as f:
        f.write("no-es-json{")
    rc, _ = run(env, "check")
    check(rc == 0, "marcador corrupto debe ser NOVEDAD (rc 0, fail-safe), fue rc=%d" % rc)

    # 6. buzón corrupto (existe pero ilegible) → fail-safe novedad
    run(env, "mark")
    # repara el marcador (mark sobre buzón válido ya lo hizo), corrompe el buzón:
    with open(buzon, "w") as f:
        f.write("{roto")
    rc, _ = run(env, "check")
    check(rc == 0, "buzón ilegible debe ser NOVEDAD (rc 0, fail-safe), fue rc=%d" % rc)

    # 7. el gate NO importa red (auditoría estática del fuente: nada de requests/urllib/smtplib).
    with open(GATE, encoding="utf-8") as f:
        src = f.read()
    for prohibido in ("import requests", "urllib.request", "import socket", "smtplib", "http.client"):
        check(prohibido not in src, "el gate NO debe usar red: encontrado %r" % prohibido)

    # 8. BTP_GATE_SOURCES acota la novedad a las fuentes del daemon: el ruido de seguimiento.json
    #    (que reescriben otros daemons cada pocos minutos) NO debe encender un daemon scopeado a
    #    'buzon'; el correo nuevo sí. (Raíz del derroche de Vega del 25/6: se encendía 24/7 por el
    #    ruido de seguimiento, no por correo real.)
    segui = os.path.join(state, "seguimiento.json")
    env_buzon = dict(env, BTP_GATE_SOURCES="buzon")
    with open(buzon, "w") as f:
        json.dump({"mensajes": [{"id": "s1"}]}, f)
    with open(segui, "w") as f:
        json.dump({"items": [1]}, f)
    # (a) scope=buzon: sella, cambia SOLO seguimiento → NO hay novedad
    run(env_buzon, "mark")
    with open(segui, "w") as f:
        json.dump({"items": [1, 2, 3]}, f)
    rc, _ = run(env_buzon, "check")
    check(rc == 1, "scope=buzon debe IGNORAR el ruido de seguimiento (rc 1), fue rc=%d" % rc)
    # (b) sin scope: el mismo tipo de cambio en seguimiento SÍ es novedad
    run(env, "mark")
    with open(segui, "w") as f:
        json.dump({"items": [1, 2, 3, 4, 5]}, f)
    rc, _ = run(env, "check")
    check(rc == 0, "sin scope, seguimiento cambiado debe ser NOVEDAD (rc 0), fue rc=%d" % rc)
    # (c) scope=buzon: correo NUEVO sí enciende
    run(env_buzon, "mark")
    with open(buzon, "w") as f:
        json.dump({"mensajes": [{"id": "s1"}, {"id": "s2"}]}, f)
    rc, _ = run(env_buzon, "check")
    check(rc == 0, "scope=buzon: correo nuevo debe ser NOVEDAD (rc 0), fue rc=%d" % rc)

    # 9. Punto ciego tapado (11-jul-2026): correo NUEVO solo en el buzón de titular@ (cuenta
    #    del conector Gmail, antes invisible al gate) también debe encender novedad — no solo el
    #    buzón por defecto (titular.mgp@). Usa la MISMA función de slug que produce el fichero real
    #    (correo_imap.path_por_cuenta), para no depender de un nombre de fichero hardcodeado.
    # OJO: path_por_cuenta() calcula la ruta sobre correo.CORREO_DIR, un constante de módulo
    # fijado en el PRIMER import de correo.py en ESTE proceso — hay que fijar BTP_STATE_DIR
    # (al mismo tempdir que ya usan los subprocesos, `state`) ANTES de importar, si no calcularía
    # la ruta por defecto (~/claudecode/...) y el fichero que escribimos aquí no sería el mismo
    # que lee el subproceso vega_gate.py (que si recibe `state` por su propio env).
    os.environ["BTP_STATE_DIR"] = state
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import correo_imap as _ci
    buzon_gonp = _ci.path_por_cuenta("titular@gmail.com")[1]
    with open(buzon, "w") as f:
        json.dump({"mensajes": [{"id": "s1"}, {"id": "s2"}]}, f)   # estable, sin cambios desde (c)
    run(env, "mark")
    rc, _ = run(env, "check")
    check(rc == 1, "sin cambios (ni buzon ni buzon_titular) = SIN NOVEDAD, fue rc=%d" % rc)
    os.makedirs(os.path.dirname(buzon_gonp), exist_ok=True)
    with open(buzon_gonp, "w") as f:
        json.dump({"mensajes": [{"id": "g1"}]}, f)
    rc, _ = run(env, "check")
    check(rc == 0, "correo NUEVO solo en titular@ debe ser NOVEDAD (rc 0), fue rc=%d" % rc)
    run(env, "mark")
    rc, _ = run(env, "check")
    check(rc == 1, "tras sellar titular@, sin más cambios = SIN NOVEDAD, fue rc=%d" % rc)
    with open(buzon_gonp, "w") as f:
        json.dump({"mensajes": [{"id": "g1"}, {"id": "g2"}]}, f)
    rc, _ = run(env, "check")
    check(rc == 0, "SEGUNDO correo en titular@ también enciende novedad, fue rc=%d" % rc)

    print("RESULTADO vega_gate.py: %d OK, %d fallos" % (ok, fail))
    if fail == 0:
        print("✅ VEGA_GATE EN VERDE")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
