#!/usr/bin/env python3
"""test_subir_historial_drive.py — la vía de subida con permiso de {{TITULAR}} no se ensancha sola.

Es la única subida clínica a Drive que no pasa por el TTY, y la cubre una regla de permisos.
Estos checks fijan lo que la hace estrecha: solo el historial, solo sus carpetas de Drive, solo
lo que es de {{TITULAR}}, sin duplicar. Sin red.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("identidad")
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import subir_historial_drive as S   # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── identidad ─────────────────────────────────────────────────────────────
# Las marcas reales (DOB, DNI, NHC) viven en `tools/identidad.local.json`, gitignored: un
# test que las escribiera en claro sería la fuga que este tool existe para evitar. Se leen
# de ahí, y si no hay overlay el bloque no corre — y se dice.
_ID = S._identidad()
_DOB = "{{FECHA_NAC}}" if S.es_suyo("{{FECHA_NAC}}") else None
if S.hay_identidad():
    check("fecha ES", S.es_suyo("Fecha de nacimiento %s" % _DOB) if _DOB else True)
    check("fecha con puntos (CA/DE)", S.es_suyo("Data Naixement %s" % _DOB.replace("/", ".")) if _DOB else True)
    for ident in _ID.get("ids", [])[:1]:
        check("identificador numérico", S.es_suyo("DNI: %sE" % ident))
else:
    print("  ⚠️  sin tools/identidad.local.json: no se prueba el reconocimiento positivo")

check("solo el nombre NO basta (hubo un informe de una amiga en su carpeta)",
      not S.es_suyo("Paciente: Fulana De Tal Ejemplo"))
check("otra fecha de nacimiento no es suya", not S.es_suyo("Fecha de nacimiento 01/09/1984"))
check("sin overlay no reconoce nada (fail-closed)",
      not S._compilar("no_existe_esta_clave").search("01/02/1970 DNI 11223344"))

# ── clasificar: dedup, herencia de sidecars, retener lo no verificable ───
C = "03 · Imagen"
_MARCA = (_DOB or "").replace("/", ".")
textos = {"/h/03 · Imagen/a.pdf": "Data Naixement %s" % _MARCA,
          "/h/03 · Imagen/a_ES.md": "Paciente: Fulana De Tal · DNI [DNI]",
          "/h/03 · Imagen/b.pdf": "escaneo sin texto",
          "/h/03 · Imagen/ya.pdf": _DOB or ""}
loc = [(r, C) for r in textos]
subir, retener = S.clasificar(loc, {C: {"ya.pdf"}}, texto=lambda r: textos[r])
sub = {os.path.basename(r) for r, _ in subir}
ret = {os.path.basename(r) for r, _ in retener}
check("lo que ya está en Drive no se vuelve a subir", "ya.pdf" not in sub | ret)
check("PDF verificado sube", "a.pdf" in sub)
check("la transcripción anonimizada hereda la verificación de su PDF", "a_ES.md" in sub)
check("lo no verificable se retiene", ret == {"b.pdf"})

import unicodedata as u   # noqa: E402
nfd = u.normalize("NFD", "ANATOMÍA.pdf")
s2, r2 = S.clasificar([("/h/03 · Imagen/" + nfd, C)], {C: {u.normalize("NFC", nfd)}},
                      texto=lambda r: "{{FECHA_NAC}}")
check("dedup inmune a NFC/NFD", s2 == [] and r2 == [])

# ── destino fijo ──────────────────────────────────────────────────────────
check("destino sale de la tabla del historial", S.destino(C) == S.H.CARPETAS_DRIVE[C])
try:
    S.destino("Mi unidad")
    check("una carpeta fuera del historial revienta", False)
except ValueError:
    check("una carpeta fuera del historial revienta", True)

fuente = open(os.path.join(ROOT, "tools", "subir_historial_drive.py"), encoding="utf-8").read()
check("no acepta un id de carpeta por argumento", "--folder" not in fuente and "argv.index" not in fuente)
check("dry-run por defecto", '"--apply" in argv' in fuente)
check("respeta el HALT antes de tocar la red",
      fuente.index("HALT activo") < fuente.index("import drive "))

print("test_subir_historial_drive: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
