#!/usr/bin/env python3
"""El vigía no puede confundir «no veo» con «está muerto» (22-sep-2026).

Caso real: un aviso dio por caídos seis daemons y el libro de deuda por «999 días sin latir»
cuando todos habían latido hacía minutos. Dos causas: el healthcheck corriendo desde un WORKTREE
(donde los latidos no existen, porque no se versionan) y un hipo de lectura del directorio.
"""
import importlib.util
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("hc", os.path.join(RAIZ, "tools", "healthcheck.py"))
HC = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(HC)
except Exception as e:                      # healthcheck arrastra medio repo; si no carga, se dice
    print("SKIP: healthcheck no importable aquí (%s)" % type(e).__name__)
    sys.exit(77)
fallos = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)


DAEMONS = ({"agente": "a", "label": "el barrido", "cadencia_h": 27},
           {"agente": "b", "label": "el calendario", "cadencia_h": 27},
           {"agente": "c", "label": "el bot", "cadencia_h": 27})

# 1. Directorio inexistente (el caso del worktree): no se juzga, y se dice por qué.
puedo, motivo = HC._puedo_juzgar_latidos(hb_dir="/no/existe/latidos", daemons=DAEMONS)
ok(puedo is False, "sin directorio de latidos NO se puede juzgar")
ok(motivo and "no existe" in motivo, "el motivo tiene que nombrar la causa: %r" % motivo)

# 2. Directorio vacío: mismo caso (worktree recién creado).
vacio = tempfile.mkdtemp(prefix="latidos-vacios-")
puedo, motivo = HC._puedo_juzgar_latidos(hb_dir=vacio, daemons=DAEMONS)
ok(puedo is False, "directorio de latidos VACÍO no autoriza a declarar muerto a nadie")
ok(motivo and "worktree" in motivo, "el motivo debe apuntar al worktree: %r" % motivo)

# 3. Con latidos de verdad, sí se juzga.
lleno = tempfile.mkdtemp(prefix="latidos-")
json.dump({"ts": "2026-09-22T05:00:00Z", "estado": "ok"},
          open(os.path.join(lleno, "a.json"), "w"))
puedo, motivo = HC._puedo_juzgar_latidos(hb_dir=lleno, daemons=DAEMONS)
ok(puedo is True and motivo is None, "con latidos presentes el vigía SÍ juzga: %r" % motivo)

# 4. Sin daemons declarados no se inventa una alarma.
ok(HC._puedo_juzgar_latidos(hb_dir="/no/existe", daemons=())[0] is True,
   "sin daemons que vigilar no hay nada que juzgar")

# 5. El umbral de cascada existe y es bajo: tres a la vez ya es sospecha de lectura.
ok(getattr(HC, "UMBRAL_CASCADA_LATIDOS", 99) <= 3,
   "el umbral de cascada debe ser 3 o menos: %r" % getattr(HC, "UMBRAL_CASCADA_LATIDOS", None))

# 6. El cableado: el corto y el colapso tienen que estar DENTRO de _salud_daemons, no sueltos.
src = open(os.path.join(RAIZ, "tools", "healthcheck.py"), encoding="utf-8").read()
cuerpo = src[src.index("def _salud_daemons("):]
cuerpo = cuerpo[:cuerpo.index("\ndef ", 10)]
ok("_puedo_juzgar_latidos()" in cuerpo, "_salud_daemons no llama al guardia de ceguera")
ok("vigia_sin_latidos" in cuerpo, "falta la alerta de «no puedo juzgar»")
ok("vigia_lectura_latidos" in cuerpo, "falta el colapso de la cascada de «sin señal»")
ok(cuerpo.index("_puedo_juzgar_latidos()") < cuerpo.index("for d in VEGA_DAEMONS"),
   "el guardia va ANTES de recorrer los daemons, o ya se han emitido las falsas alarmas")

if fallos:
    print("ROJO:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK: el vigía distingue «no veo» de «está muerto» (worktree, directorio vacío, cascada)")
