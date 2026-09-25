#!/usr/bin/env python3
"""tests/test_identidad_tabla.py — la ventanilla no puede marcar como AJENO lo que es suyo.

EL FALLO (24-sep-2026). Las tres radiografías de tórax de 2024 —las que documentan la punta del
catéter de su reservorio— las rechazaba la ventanilla clínica como de «otro paciente». No lo eran:
mismo CIP, mismo NHC, mismo DNI que el informe de implantación del port. El culpable era el
parser: en una transcripción en tabla markdown («| **F. Nacimiento:** | | 12/05/1990 |») entre la
etiqueta y el valor hay una celda vacía, y se quedaba con «**» en vez de con la fecha.

Un falso positivo del muro no es inofensivo: esconde SU PROPIA documentación justo cuando hace
falta. Esto lo congela.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import identidad_paciente as ip  # noqa: E402

OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


FECHA = "12/05/1990"

# 1. El caso real: tabla con celda vacía entre etiqueta y valor.
v = ip._valores_etiquetados("| **F. Nacimiento:** | | %s |" % FECHA, ip.ETIQUETAS_DOB)
ok("tabla markdown con celda vacía: saca la fecha, no los asteriscos", v == [FECHA], "-> %r" % v)

# 2. Las formas de siempre siguen funcionando (no arreglar rompiendo).
for texto in ("F. Nacimiento: %s" % FECHA,
              "| F. Nacimiento: | %s |" % FECHA,
              "**Fecha de nacimiento:** %s" % FECHA,
              "FECHA DE NACIMIENTO:  %s" % FECHA,
              "Fecha de Nacimiento = %s" % FECHA):
    v = ip._valores_etiquetados(texto, ip.ETIQUETAS_DOB)
    ok("sigue leyendo %r" % texto[:34], v == [FECHA], "-> %r" % v)

# 3. El nombre, igual: en tabla con celda vacía.
v = ip._valores_etiquetados("| **Paciente:** | | NOMBRE APELLIDO |", ip.ETIQUETAS_NOMBRE)
ok("el nombre en tabla con celda vacía también se lee", v == ["NOMBRE APELLIDO"], "-> %r" % v)

# 4. Y una etiqueta sin valor ninguno sigue sin inventarse nada.
v = ip._valores_etiquetados("| **F. Nacimiento:** | | |", ip.ETIQUETAS_DOB)
ok("etiqueta sin valor no devuelve nada (no se inventa)", v == [], "-> %r" % v)

# 5. El veredicto de arriba: con la fecha del titular, la tabla debe COINCIDIR.
try:
    t = ip.titular()
except Exception:                                  # noqa: BLE001
    t = None
if t and t.get("dob"):
    fila = "| **F. Nacimiento:** | | %s |" % t["dob"]
    r = ip.verificar(fila)
    ok("con su fecha real en tabla, verificar() no dice «ajeno»",
       (r.get("veredicto") if isinstance(r, dict) else r) != "otro_paciente", "-> %r" % (r,))
else:
    print("  ⏭️  (sin overlay del titular en este árbol: el caso extremo a extremo no se corre)")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_identidad_tabla: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
