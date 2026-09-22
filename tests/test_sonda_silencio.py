#!/usr/bin/env python3
"""Sonda del silencio (tools/sonda_silencio.py) con ficheros SINTÉTICOS.

Congela lo que nació del caso real del 21-sep-2026: una tarea programada que arrancó, murió a
los 7 segundos y se quedó cinco horas marcada como «corriendo» sin avisar a nadie.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("ss", os.path.join(RAIZ, "tools", "sonda_silencio.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
fallos = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)


AHORA = 1_790_000_000.0
tmp = tempfile.mkdtemp(prefix="sonda-silencio-")


def sesion(nombre, **campos):
    ruta = os.path.join(tmp, "local_%s.json" % nombre)
    json.dump(campos, open(ruta, "w", encoding="utf-8"))
    return ruta


# El caso real: nace de una tarea, vive 7 s. Marcas en MILISEGUNDOS, como las escribe la app.
sesion("muerta", scheduledTaskId="rodaje-guard-clinico", sessionId="local_muerta",
       title="Cerrar el rodaje del guard clínico (24 h)",
       createdAt=(AHORA - 18000) * 1000, lastActivityAt=(AHORA - 18000 + 7) * 1000)
# Una tarea que sí trabajó: dos horas de vida.
sesion("sana", scheduledTaskId="informe-diario", sessionId="local_sana",
       createdAt=(AHORA - 10000) * 1000, lastActivityAt=(AHORA - 2800) * 1000)
# Una sesión normal de {{TITULAR}}, cortísima: NO es de una tarea, no se mira.
sesion("humana", sessionId="local_humana",
       createdAt=(AHORA - 60) * 1000, lastActivityAt=(AHORA - 58) * 1000)

m = S.tareas_mudas(ahora=AHORA, patron=os.path.join(tmp, "local_*.json"))
ok(len(m) == 1, "debería salir 1 tarea muda y salen %d: %s" % (len(m), m))
ok(m and m[0]["tarea"] == "rodaje-guard-clinico", "la muda es la del rodaje: %s" % m)
ok(m and abs(m[0]["vivio_s"] - 7) < 0.5, "vivió 7 s: %s" % m)
ok(m and abs(m[0]["hace_h"] - 5) < 0.1, "fue hace 5 h: %s" % m)

# Marcas en segundos (por si la app cambia de unidad): el mismo caso se sigue cazando.
tmp2 = tempfile.mkdtemp(prefix="sonda-silencio-s-")
json.dump({"scheduledTaskId": "t", "createdAt": AHORA - 100, "lastActivityAt": AHORA - 95},
          open(os.path.join(tmp2, "local_x.json"), "w"))
ok(len(S.tareas_mudas(ahora=AHORA, patron=os.path.join(tmp2, "local_*.json"))) == 1,
   "segundos o milisegundos, el hallazgo es el mismo")

# Un fichero corrupto no puede tumbar la sonda: una sonda que peta no vigila nada.
open(os.path.join(tmp, "local_roto.json"), "w").write("{esto no es json")
ok(len(S.tareas_mudas(ahora=AHORA, patron=os.path.join(tmp, "local_*.json"))) == 1,
   "un json roto se salta, no rompe la sonda")

# Cola: un job lleva 9 h en processing y hay fallos viejos.
cola = os.path.join(tmp, "queue")
for sub in ("processing", "failed"):
    os.makedirs(os.path.join(cola, sub))
p = os.path.join(cola, "processing", "job1.json")
open(p, "w").write("{}")
os.utime(p, (AHORA - 9 * 3600, AHORA - 9 * 3600))
viejo = os.path.join(cola, "failed", "f1.json")
open(viejo, "w").write("{}")
os.utime(viejo, (AHORA - 10 * 86400, AHORA - 10 * 86400))
nuevo = os.path.join(cola, "failed", "f2.json")
open(nuevo, "w").write("{}")
os.utime(nuevo, (AHORA - 3600, AHORA - 3600))

at = S.jobs_atascados(ahora=AHORA, cola=cola)
ok(len(at) == 1 and abs(at[0]["horas"] - 9) < 0.2, "job atascado 9 h: %s" % at)
f = S.fallos_olvidados(ahora=AHORA, cola=cola)
ok(f["total"] == 2 and f["olvidados"] == 1, "2 caídos, 1 olvidado: %s" % f)
ok(abs(f["dias_del_mas_viejo"] - 10) < 0.1, "el más viejo, 10 días: %s" % f)
ok(S.fallos_olvidados(ahora=AHORA, cola=os.path.join(tmp, "vacio")) is None,
   "sin cola no se inventa un hallazgo")

# El informe dice en voz alta lo que NO cubre: que una tarea corra entera no prueba que cumpliera.
texto = "\n".join(S._informe({"tareas_mudas": m, "jobs_atascados": at, "fallos": f}))
ok("rodaje-guard-clinico" in texto, "el informe nombra la tarea: %s" % texto)

if fallos:
    print("ROJO:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK: sonda del silencio (tarea muda, unidades, json roto, cola atascada, fallos viejos)")

# ── enganchada al healthcheck: el aviso tiene que NACER de ahí, no de que alguien la llame ──
hc = open(os.path.join(RAIZ, "tools", "healthcheck.py"), encoding="utf-8").read()
ok("import sonda_silencio" in hc, "healthcheck no importa la sonda: entonces no avisa sola")
ok("silencio_tarea_muda" in hc and "silencio_job_atascado" in hc,
   "faltan las claves de alerta del silencio en healthcheck")
ok('_t["hace_h"] <= 48' in hc, "el aviso debe limitarse a lo reciente (48 h) o es ruido diario")
if fallos:
    print("ROJO:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK: la sonda está enganchada al healthcheck")
