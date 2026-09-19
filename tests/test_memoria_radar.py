#!/usr/bin/env python3
"""test_memoria_radar.py — los dos pellizcos sobre el grafo de memorias (sin motor nuevo).

Aísla casa base + carpeta de memorias en un tmp (BTP_REPO / BTP_MEMORY_DIR / BTP_STATE_DIR).
Verifica que:
  · RESUCITAR propone una memoria DORMIDA (fecha vieja DENTRO del cuerpo) que casa con el
    foco (brújula + hilos vivos), y NO propone una RECIENTE igual de relevante;
  · la dormancia va por la FECHA escrita en la lección, no por el mtime (que aquí es nuevo
    para TODOS los ficheros recién creados, como tras una consolidación de git);
  · una dormida que NO casa con el foco no se propone (filtro de relevancia, score>0);
  · ENLAZAR sugiere vecinas por similitud, NUNCA la propia memoria, NUNCA una ya enlazada;
  · es OFFLINE (no abre sockets) y FAIL-SOFT (estado/fichero ausente no lanza).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("perfil")
import datetime
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_memradar_")
_MEM = os.path.join(_TMP, "memory")
_STATE = os.path.join(_TMP, "state")
os.makedirs(_MEM, exist_ok=True)
os.makedirs(_STATE, exist_ok=True)
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_MEMORY_DIR"] = _MEM
os.environ["BTP_STATE_DIR"] = _STATE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import memoria_radar as M  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _mem(nombre, cuerpo):
    with open(os.path.join(_MEM, nombre), "w", encoding="utf-8") as f:
        f.write("---\nname: %s\nmetadata:\n  type: feedback\n---\n\n%s\n"
                % (nombre[:-3], cuerpo))


HOY = datetime.date.today()


def _hace(dias):
    """Fecha DD/MM/YY de hace `dias` días, en el formato que escribe {{TITULAR}}."""
    f = HOY - datetime.timedelta(days=dias)
    return "%d/%d/%s" % (f.day, f.month, str(f.year)[-2:])


# --- Corpus de prueba ----------------------------------------------------------------
# DORMIDA + relevante al foco (biopsia/Zúrich): debe resucitar.
_mem("feedback-biopsia-{{CIUDAD}}-vieja.md",
     "Lección sobre la biopsia en {{CIUDAD}} con {{CONTACTO}} y los cores de neoantigenos. "
     "Anotada hace mucho (%s)." % _hace(120))
# RECIENTE + relevante: NO debe resucitar (no está dormida).
_mem("feedback-biopsia-reciente.md",
     "Apunte fresco sobre la biopsia en {{CIUDAD}} y {{CONTACTO}} (%s)." % _hace(1))
# DORMIDA pero IRRELEVANTE al foco: NO debe resucitar (filtro de relevancia).
_mem("feedback-formato-pdf-viejo.md",
     "Tooling de PDF: usar fitz y Chrome print-to-pdf, no poppler (%s)." % _hace(150))
# Vecina temática de la dormida (para el test de ENLAZAR), sin enlace previo.
_mem("project-contacto-pipeline.md",
     "{{CONTACTO}} {{CONTACTO}} trae el pipeline de neoantigenos y los cores de la biopsia en {{CIUDAD}} "
     "(%s)." % _hace(3))

# Brújula + hilos: el FOCO de hoy = biopsia en Zúrich con {{CONTACTO}}.
with open(os.path.join(_STATE, "cumbre.json"), "w", encoding="utf-8") as f:
    json.dump({
        "ruta_actual": "vacuna personalizada hacia NED",
        "aqui_estamos": "biopsia",
        "salientes": [
            {"id": "biopsia", "estado": "en_curso",
             "titulo": "Re-biopsia en {{CIUDAD}} con {{CONTACTO}}",
             "bloqueo": "confirmar cores de neoantigenos",
             "siguiente_accion": "checklist molecular"},
        ],
    }, f)
with open(os.path.join(_STATE, "seguimiento.json"), "w", encoding="utf-8") as f:
    json.dump({"hilos": [
        {"estado": "en_curso", "titulo": "Confirmar fecha de la biopsia en {{CIUDAD}}"},
        {"estado": "hecho", "titulo": "Algo ya cerrado e irrelevante"},
    ]}, f)


# --- 1) FOCO ------------------------------------------------------------------------
foco = M.foco_actual()
check("foco recoge la brújula ({{CIUDAD}}/biopsia)", "{{CIUDAD}}" in foco.lower() and "biopsia" in foco.lower())
check("foco recoge hilo vivo", "confirmar fecha" in foco.lower())
check("foco IGNORA hilo 'hecho'", "irrelevante" not in foco.lower())


# --- 2) RESUCITAR -------------------------------------------------------------------
res = M.resucitar(dias_dormida=30, n=3)
slugs = [r["slug"] for r in res]
check("resucita la DORMIDA relevante", "feedback-biopsia-{{CIUDAD}}-vieja" in slugs)
check("NO resucita la RECIENTE relevante", "feedback-biopsia-reciente" not in slugs)
check("NO resucita la DORMIDA irrelevante (PDF)", "feedback-formato-pdf-viejo" not in slugs)
check("la dormida reporta muchos días (por la fecha del cuerpo, no el mtime)",
      any(r["slug"] == "feedback-biopsia-{{CIUDAD}}-vieja" and r["dias_sin_tocar"] >= 100 for r in res))

# Sin foco → no inventa nada.
check("foco vacío → 0 candidatos", M.resucitar(dias_dormida=30, n=3, foco_texto="") == [])


# --- 3) ENLAZAR ---------------------------------------------------------------------
enl = M.enlazar("feedback-biopsia-{{CIUDAD}}-vieja.md", n=3)
check("enlazar ok", enl.get("ok") is True)
sug_slugs = [s["slug"] for s in enl["sugerencias"]]
check("sugiere la vecina temática ({{CONTACTO}}/pipeline)", "project-contacto-pipeline" in sug_slugs)
check("NUNCA se sugiere a sí misma", "feedback-biopsia-{{CIUDAD}}-vieja" not in sug_slugs)

# Si ya está enlazada, no la vuelve a sugerir.
_mem("feedback-ya-enlaza.md",
     "Lección sobre biopsia {{CIUDAD}} {{CONTACTO}} neoantigenos cores, ya enlaza a "
     "[[project-contacto-pipeline]] (%s)." % _hace(2))
enl2 = M.enlazar("feedback-ya-enlaza.md", n=3)
check("no re-sugiere una ya enlazada", "project-contacto-pipeline" not in [s["slug"] for s in enl2["sugerencias"]])
check("reporta las ya enlazadas", "project-contacto-pipeline" in enl2.get("ya_enlazadas", []))


# --- 4) FAIL-SOFT -------------------------------------------------------------------
enl3 = M.enlazar("no-existe-jamas.md")
check("enlazar de inexistente → ok:false sin lanzar", enl3.get("ok") is False)

# Estado ausente → foco vacío, no lanza.
os.remove(os.path.join(_STATE, "cumbre.json"))
os.remove(os.path.join(_STATE, "seguimiento.json"))
try:
    M.foco_actual()
    check("foco sin estado no lanza", True)
except Exception:
    check("foco sin estado no lanza", False)


print("\n%d OK, %d FALLOS" % (_pass, _fail))
sys.exit(1 if _fail else 0)
