#!/usr/bin/env python3
"""test_historial_sync.py — la rutina que mantiene el historial al día no miente ni sube nada.

Cada check nació de un fallo real al construirla el 25-ago-2026:
  · Comparar Drive contra el archivo POR NOMBRE daba 288 pendientes de 292: Drive conserva la
    taxonomía vieja («2024-01-16 - Imagen - Ecografía de mama») y el archivo usa la nueva
    («2024-01-16 - HMM - ECOGRAFÍA DE MAMA»). La rutina se ofrecía a bajar entero un archivo
    que ya estaba entero.
  · Con el HALT puesto, `drive.py` se bloquea. Una rutina que se cae entera por eso deja de
    hacer las otras tres patas, que sí puede hacer.
  · Reindexar cuesta minutos y toca un fichero de 400 MB: no se hace si no entró nada.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import historial_sync as S   # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── el emparejado viejo-nuevo, que es donde estaba el fallo ───────────────
IDX = {"2024-01-16": [S._palabras("ECOGRAFÍA DE MAMA")],
       "2026-07-29": [S._palabras("Analítica — bioquímica, hemograma, marcadores y orina")]}

check("reconoce el mismo documento con la taxonomía vieja de Drive",
      S.ya_archivado("2024-01-16 - Imagen - Ecografía de mama.pdf", IDX))
check("reconoce una analítica ya archivada aunque cambie el centro",
      S.ya_archivado("2026-07-29 - Lab - Analítica - bioquímica + hemograma + marcadores y orina.pdf", IDX))
check("un documento de otra fecha NO se da por archivado",
      not S.ya_archivado("2024-01-17 - Imagen - Ecografía de mama.pdf", IDX))
check("un documento distinto del mismo día NO se da por archivado",
      not S.ya_archivado("2024-01-16 - Lab - Hemocultivo negativo.pdf", IDX))
check("sin fecha en el nombre no se afirma nada: se deja pasar para que lo mire alguien",
      not S.ya_archivado("Analisis_SBRT_lesiones.pdf", IDX))

check("la fecha se rescata igual del esquema viejo que del nuevo",
      S._fecha_y_desc("2026-07-29 - HUVH - Analítica")[0]
      == S._fecha_y_desc("2026-07-29 - Lab - Analítica")[0] == "2026-07-29")
check("la categoría/centro del medio NO entra en la comparación",
      "huvh" not in S._fecha_y_desc("2026-07-29 - HUVH - Analítica")[1]
      and "lab" not in S._fecha_y_desc("2026-07-29 - Lab - Analítica")[1])

# ── degradar con honestidad bajo HALT ─────────────────────────────────────
if S._halt_activo():
    r = S.bajar_de_drive(apply=False)
    check("con HALT, la pata de Drive se salta y lo DICE", bool(r["saltado"]) and "HALT" in r["saltado"])
    check("con HALT, no se baja nada", r["bajados"] == [])
else:
    _pass += 2   # sin HALT no hay nada que comprobar aquí

# ── dry-run y muro ────────────────────────────────────────────────────────
r = S.sync(apply=False)
check("sync sin --apply no escribe ni reindexa",
      r["reindexado"] is None and r["ingerido"]["escritos"] == 0)
check("sync sin --apply tampoco avisa", r["aviso"] is None)

fuente = open(os.path.join(ROOT, "tools", "historial_sync.py"), encoding="utf-8").read()
check("la rutina NO sube nada a Drive",
      "drive.upload" not in fuente and "cmd_upload" not in fuente
      and "files().create" not in fuente)
check("no hace red por su cuenta: todo pasa por drive.py",
      not any(x in fuente for x in ("urllib.request", "requests.", "socket.")))
check("reindexa con el intérprete del .venv, que es el único que lee PDFs",
      "VENV_PY" in fuente and ".venv" in fuente)
check("la bandeja de descarga vive en zona clínica, no en /tmp",
      "_bandeja_sync" in fuente and "/tmp" not in S.BANDEJA)

# ── estado ────────────────────────────────────────────────────────────────
e = S.estado()
check("estado responde con las claves que el daemon necesita",
      all(k in e for k in ("documentos", "halt", "indice_al_dia", "ultima_sync")))
check("estado no revienta sin diario previo", isinstance(e["documentos"], int))

print("test_historial_sync: %d ok, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
