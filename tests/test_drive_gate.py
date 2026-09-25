#!/usr/bin/env python3
"""test_drive_gate.py — la boca de subida a Drive no puede tragarse un clínico.

`drive.py` era la única boca hacia fuera sin puerta: `create`/`upload_file`/`update_content`
mandaban cualquier ruta local a una cuenta de Drive COMPARTIDA sin consultar `borde`, sin
dejar borrador en el outbox y sin pedir confirmación (la única que había era la de `delete`).
Y es la boca que mejor encaja con el formato en el que vive el N2: los PDFs del hospital.

El gate tiene que morir ANTES de tocar la red: aquí no hay credenciales, así que si el test
llegara a `_service()` fallaría por otra razón y no probaría nada. Por eso se comprueba el
MENSAJE, no solo que reviente.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import drive  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _falla_con(fn, *a, **kw):
    """Devuelve el mensaje de la excepción, o None si no lanzó."""
    try:
        fn(*a, **kw)
    except BaseException as e:      # SystemExit incluido (el HALT hace sys.exit)
        return "%r" % e
    return None


def main():
    tmp = tempfile.mkdtemp(prefix="drive_gate_")

    # Una ruta que es zona clínica por NOMBRE DE SEGMENTO (familia A): vale en cualquier
    # disco, así que el test no depende de que exista el clínico real de {{TITULAR}}.
    clin_dir = os.path.join(tmp, "_PRIVADO_CLINICO")
    os.makedirs(clin_dir, exist_ok=True)
    clin = os.path.join(clin_dir, "informe.pdf")
    with open(clin, "wb") as f:
        f.write(b"%PDF-1.4 fake")

    normal = os.path.join(tmp, "notas.txt")
    with open(normal, "w", encoding="utf-8") as f:
        f.write("apuntes de una reunión, nada sensible\n")

    # 1) Sin TTY (que es como corre SIEMPRE un agente), un clínico no sube. Y muere en el
    #    gate, no en la red: el mensaje lo demuestra.
    for fn, args in ((drive.create, (clin,)), (drive.upload_file, (clin,))):
        msg = _falla_con(fn, *args)
        check("%s con ruta clínica → bloqueado" % fn.__name__, msg is not None)
        check("%s muere en el gate, no en la red" % fn.__name__,
              msg is not None and ("TTY" in msg or "clínica" in msg or "BLOQUEADA" in msg))

    # 2) La política es obligatoria: si no se puede saber qué es clínico, no se sube NADA.
    orig = drive._politica_clinica
    drive._politica_clinica = lambda: (_ for _ in ()).throw(ImportError("boom"))
    try:
        msg = _falla_con(drive.create, normal)
        check("sin política cargable → fail-closed incluso para lo normal", msg is not None)
        check("y lo dice", msg is not None and "polític" in msg.lower())
    finally:
        drive._politica_clinica = orig

    # 3) El borde sigue mandando sobre lo no-clínico: si dice que no, no se sube.
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import borde
    orig_guard = borde.guard_cli
    borde.guard_cli = lambda *a, **kw: False
    try:
        msg = _falla_con(drive.create, normal)
        check("borde deniega → subida bloqueada", msg is not None and "borde" in msg.lower())
    finally:
        borde.guard_cli = orig_guard

    # 4) Y con el borde permitiendo, lo normal PASA el gate (luego morirá por credenciales,
    #    que es otra cosa: lo que se prueba es que el gate no lo para).
    borde.guard_cli = lambda *a, **kw: True
    try:
        msg = _falla_con(drive.create, normal)
        paro_el_gate = msg is not None and ("BLOQUEADA" in msg or "TTY" in msg)
        check("fichero normal NO lo para el gate", not paro_el_gate)
    finally:
        borde.guard_cli = orig_guard

    # 5) El HALT sigue mandando y va PRIMERO.
    halt = os.path.join(tmp, ".HALT")
    cwd = os.getcwd()
    os.chdir(tmp)
    open(halt, "w").close()
    try:
        msg = _falla_con(drive.create, normal)
        check("HALT activo → no sube nada", msg is not None and "SystemExit" in msg)
    finally:
        os.chdir(cwd)
        os.remove(halt)

    print("RESULTADO drive gate: %d OK, %d fallos" % (_pass, _fail))
    print("✅ GATE DE DRIVE EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
