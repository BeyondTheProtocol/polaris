#!/usr/bin/env python3
"""tests/test_cumbre_integridad.py — que la cadena a NED no pueda quedarse mutilada en silencio.

EL INCIDENTE (20-sep-2026). `tools/state/cumbre.json` apareció con 27 bytes: solo
`{"aqui_estamos": "biopsia"}`. Sin `meta`, sin `ruta_actual` y sin un solo saliente. Lo que se
perdió no fue un fichero de configuración: es la CADENA DE OBJETIVOS hacia NED, el cimiento con
el que el sistema decide qué es prioritario (regla de {{TITULAR}}, 26/6/26). Tres consecuencias, y
ninguna gritó:

  · `cumbre.py` reventaba con `KeyError: 'meta'`, que no dice ni qué pasó ni qué hacer;
  · `tests/test_digest.sh` llevaba el día en rojo por «falta el foco» (el «AQUÍ ESTAMOS»);
  · `seguimiento.py` prioriza por impacto-NED leyendo este fichero — priorizaba a ciegas.

Se recuperó entera desde `BRUJULA-NED.md` (su render fiel, escrito 9 minutos antes del
destrozo), verificando el round-trip: re-renderizar el JSON reconstruido devuelve la misma
brújula byte a byte.

Este test es el freno, en tres capas:
  1. escribir un estado sin las claves obligatorias es IMPOSIBLE (fail-closed en `_write_atomic`);
  2. leer un fichero ya mutilado da un error que se entiende y dice cómo restaurar;
  3. el estado VIVO de casa base está entero (si no hay estado vivo, se SALTA con rc=77).
"""
import json
import os
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
# A propósito el árbol ACTUAL: se prueba el cumbre.py de la rama en la que estás trabajando.
sys.path.insert(0, os.path.join(AQUI, "..", "tools"))

_TMP = tempfile.mkdtemp(prefix="test_cumbre_integridad_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)

import cumbre  # noqa: E402

FALLOS, OK = [], []


def ok(caso, cond, detalle=""):
    (OK if cond else FALLOS).append(caso)
    print(("  ok  " if cond else "  FALLO  ") + caso + ("" if cond else f" {detalle}"))


CUMBRE_TMP = os.path.join(os.environ["BTP_STATE_DIR"], "cumbre.json")
cumbre.CUMBRE = CUMBRE_TMP
cumbre.BRUJULA_MD = os.path.join(_TMP, "BRUJULA-NED.md")

# --- 1. No se puede escribir un estado mutilado -------------------------------------------------
ENTERO = dict(cumbre.SEED)

for falta in ("meta", "ruta_actual", "salientes"):
    roto = {k: v for k, v in ENTERO.items() if k != falta}
    try:
        cumbre._write_atomic(CUMBRE_TMP, roto)
        ok("escribir un estado sin `%s` se rechaza" % falta, False, "-> se escribió")
    except ValueError as e:
        ok("escribir un estado sin `%s` se rechaza" % falta, falta in str(e), f"-> {e}")

# El caso EXACTO del incidente: solo el puntero, nada más.
try:
    cumbre._write_atomic(CUMBRE_TMP, {"aqui_estamos": "biopsia"})
    ok("el payload exacto del incidente (27 bytes) se rechaza", False, "-> se escribió")
except ValueError:
    ok("el payload exacto del incidente (27 bytes) se rechaza", True)

# Una lista de salientes VACÍA también mutila la cadena, aunque la clave esté.
try:
    cumbre._write_atomic(CUMBRE_TMP, dict(ENTERO, salientes=[]))
    ok("una cadena con 0 salientes se rechaza", False, "-> se escribió")
except ValueError:
    ok("una cadena con 0 salientes se rechaza", True)

# --- 2. El estado entero SÍ se escribe, y se lee ------------------------------------------------
cumbre._write_atomic(CUMBRE_TMP, ENTERO)
ok("un estado entero se escribe sin protestar", os.path.getsize(CUMBRE_TMP) > 500)
ok("y se lee de vuelta", cumbre.load()["meta"] == ENTERO["meta"])
ok("no deja tmp huérfanos", not [f for f in os.listdir(os.path.dirname(CUMBRE_TMP))
                                 if ".tmp." in f])

# --- 3. Leer un fichero YA mutilado se explica, no revienta con KeyError -------------------------
with open(CUMBRE_TMP, "w", encoding="utf-8") as f:
    json.dump({"aqui_estamos": "biopsia"}, f)
try:
    cumbre.load()
    ok("leer un fichero mutilado avisa en claro", False, "-> devolvió el dict roto")
except ValueError as e:
    txt = str(e)
    ok("leer un fichero mutilado avisa en claro",
       "mutilado" in txt and "BRUJULA-NED.md" in txt, f"-> {txt[:90]}")
except KeyError:
    ok("leer un fichero mutilado avisa en claro", False, "-> KeyError críptico, el bug original")

# --- 4. El estado VIVO de casa base está entero -------------------------------------------------
VIVO = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    "tools", "state", "cumbre.json")
if not os.path.exists(VIVO):
    print("test_cumbre_integridad: sin estado vivo (%s) — SALTADO" % VIVO)
    sys.exit(77)

with open(VIVO, encoding="utf-8") as f:
    vivo = json.load(f)
for k in cumbre.OBLIGATORIAS:
    ok("el cumbre.json VIVO tiene `%s`" % k, bool(vivo.get(k)),
       "-> la cadena a NED está mutilada en casa base; restaura desde BRUJULA-NED.md")
ids = {s.get("id") for s in vivo.get("salientes", [])}
ok("el cumbre.json VIVO tiene salientes con id", all(ids) and len(ids) >= 3, f"-> {sorted(ids)}")
aqui = vivo.get("aqui_estamos")
ok("`aqui_estamos` apunta a un saliente que existe", aqui in ids,
   f"-> aqui_estamos={aqui!r} no está entre {sorted(ids)}")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_cumbre_integridad: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
