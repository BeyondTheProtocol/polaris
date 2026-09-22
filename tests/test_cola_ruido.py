#!/usr/bin/env python3
"""El aviso de jobs caídos distingue lo VIVO del cementerio (22-sep-2026).

Ese día el Tablero decía «43 job(s) del lazo caídos» y al abrirlos: 39 venían de dos clases
muertas —una desde el 6-ago, otra desde el 12-sep—, y solo 2 eran de esta semana. Un aviso que
cuenta historia junto con trabajo pendiente se deja de leer, que es peor que no avisar.
"""
import importlib.util
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("sg", os.path.join(RAIZ, "tools", "seguimiento.py"))
SG = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SG)
fallos = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)


AHORA = 1_790_000_000.0
d = tempfile.mkdtemp(prefix="cola-caidos-")


def job(nombre, dias):
    r = os.path.join(d, nombre)
    open(r, "w").write("{}")
    os.utime(r, (AHORA - dias * 86400, AHORA - dias * 86400))
    return nombre


nombres = [job("hoy.json", 0.2), job("ayer.json", 1), job("semana.json", 6.5),
           job("viejo.json", 8), job("agosto.json", 49)]
vivos, viejos = SG._fallos_por_edad(nombres, ahora=AHORA, base=d)
ok(sorted(vivos) == ["ayer.json", "hoy.json", "semana.json"], "vivos mal: %s" % vivos)
ok(sorted(viejos) == ["agosto.json", "viejo.json"], "cementerio mal: %s" % viejos)

# El corte es por DÍAS y se puede mover sin tocar el resto.
v2, _ = SG._fallos_por_edad(nombres, dias=30, ahora=AHORA, base=d)
ok(len(v2) == 4, "con 30 días entran 4: %s" % v2)

# Nombres sueltos, no rutas: es lo que da _listdir_json, y fue el bug del primer intento.
ok(SG._fallos_por_edad(["hoy.json"], ahora=AHORA, base=d)[0] == ["hoy.json"],
   "tiene que aceptar NOMBRES y unirlos a su carpeta")

# Un fichero ilegible cuenta como vivo: el aviso nunca esconde lo que no puede mirar.
v3, vj3 = SG._fallos_por_edad(["no-existe.json"], ahora=AHORA, base=d)
ok(v3 == ["no-existe.json"] and not vj3, "lo ilegible cuenta como VIVO: %s %s" % (v3, vj3))

# El umbral vive en una constante con nombre, no escondido en el texto del aviso.
ok(getattr(SG, "DIAS_FALLO_VIVO", None) == 7, "el corte son 7 días: %r" % getattr(SG, "DIAS_FALLO_VIVO", None))

src = open(os.path.join(RAIZ, "tools", "seguimiento.py"), encoding="utf-8").read()
ok("caídos esta semana" in src, "el texto del aviso tiene que acotar el periodo")
ok("_fallos_por_edad(data[\"fallos\"]" in src, "el render no usa el separador")

if fallos:
    print("ROJO:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK: el aviso de caídos separa esta semana del cementerio")
