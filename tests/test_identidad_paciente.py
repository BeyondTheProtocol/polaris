#!/usr/bin/env python3
"""test_identidad_paciente.py — estar en su carpeta no prueba que el informe sea suyo.

NORMA: `feedback-verificar-identidad-paciente-en-informe` (clase BLOQUEO) — «antes de usar datos
de un informe clínico, verificar nombre+fecha de nacimiento: estar en su carpeta NO prueba que
sea suyo».

NO USA NI UN DATO CLÍNICO REAL. Todo el fichero corre contra un titular INVENTADO en un overlay
de mentira (BTP_REPO apunta a un directorio temporal), y los informes de prueba están escritos a
mano aquí. Probar esta norma con documentos suyos sería meter su filiación —y la de terceros—
en la transcripción, que es la misma clase de fuga que evita el resto del muro.

EL FILO ESTÁ EN NO DAR FALSOS POSITIVOS. Un informe clínico está LLENO de fechas (de prueba, de
visita, de informe) y de nombres (médicos, hospitales). Por eso solo cuentan los campos
ETIQUETADOS: «Paciente:», «Fecha de nacimiento:», «Data de naixement:». Una fecha suelta no es
una filiación, y un detector que lo creyera saltaría en todos los documentos.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="identidad_")
_os.makedirs(_os.path.join(_TMP, "tools"))
TITULAR = {"nombre": "Prudencia", "apellidos": ["Ferronato"],
           "nacimiento": ["14/02/1970", "1970-02-14", "14-02-1970", "14 de febrero de 1970"]}
with open(_os.path.join(_TMP, "tools", "perfil.local.json"), "w", encoding="utf-8") as fh:
    json.dump({"titular": TITULAR}, fh)
with open(_os.path.join(_TMP, "tools", "identidad.local.json"), "w", encoding="utf-8") as fh:
    json.dump({"dob_patrones": [r"14[./-]0?2[./-](19)?70"]}, fh)

_os.environ["BTP_REPO"] = _TMP
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "tools"))
import identidad_paciente as ip  # noqa: E402

SUYO = """HOSPITAL UNIVERSITARI
Informe de anatomía patológica
Paciente: Prudencia Ferronato Gómez
Fecha de nacimiento: 14/02/1970
Fecha de la prueba: 03/09/2026
Fecha del informe: 11/09/2026
Diagnóstico: hallazgo de prueba, valor 42.
"""
AJENO = """HOSPITAL DE BELLVITGE
Informe de traumatología
Paciente: Anselmo Quintanilla Bo
Fecha de nacimiento: 02/11/1948
Fecha de la intervención: 14/02/1970
Implante: megaprótesis.
"""
SOLO_NOMBRE = "Paciente: Prudencia Ferronato\nFecha de la prueba: 03/09/2026\n"
SOLO_FECHA = "Fecha de nacimiento: 14-02-1970\nServicio: oncología médica\n"
SIN_FILIACION = """Resultado de laboratorio
Fecha de extracción: 01/09/2026
Hemoglobina 13,2 g/dL
Ki-67 {{N}}%
"""
CATALAN = "Pacient: Prudencia Ferronato\nData de naixement: 14/02/1970\n"
INGLES = "Patient: Prudencia Ferronato\nDate of birth: 1970-02-14\n"

fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


v, _ = ip.verificar(SUYO)
check("informe suyo (nombre + fecha etiquetados) → coincide", v == "coincide")
v, det = ip.verificar(AJENO)
check("informe de OTRA persona que no la nombra → otro_paciente", v == "otro_paciente")
v2, _ = ip.verificar(AJENO + "\nEstudio comparado con el previo de Prudencia Ferronato.\n")
check("encabezado ajeno PERO la menciona en el cuerpo → ambiguo (se sirve con aviso)",
      v2 == "ambiguo")
check("   …y `ambiguo` sí se sirve: rechazarlo cerraría 41 informes suyos de imagen",
      ip.SIRVE["ambiguo"] and "ambiguo" in ip.PIDE_AVISO)
check("   …y NO reproduce el nombre ajeno en la explicación",
      "Anselmo" not in det and "Quintanilla" not in det and "02/11/1948" not in det)
v, _ = ip.verificar(SOLO_NOMBRE)
check("solo el nombre → parcial (la norma pide los dos)", v == "parcial")
v, _ = ip.verificar(SOLO_FECHA)
check("solo la fecha → parcial", v == "parcial")
v, _ = ip.verificar(SIN_FILIACION)
check("hoja de laboratorio sin filiación → no_consta", v == "no_consta")
v, _ = ip.verificar(CATALAN)
check("filiación en catalán (Pacient / Data de naixement) → coincide", v == "coincide")
v, _ = ip.verificar(INGLES)
check("filiación en inglés (Patient / Date of birth) → coincide", v == "coincide")

# ── el filo: fechas y nombres que NO son filiación ────────────────────────────────────────
v, _ = ip.verificar(SUYO.replace("Fecha de nacimiento: 14/02/1970", "Fecha de nacimiento: 14/02/1970\nDr. Anselmo Quintanilla — oncología"))
check("el nombre del MÉDICO no convierte el informe en ajeno", v == "coincide")
v, _ = ip.verificar("Fecha de la prueba: 02/11/1948\nFecha del informe: 03/09/2026\n"
                    "Paciente: Prudencia Ferronato\nFecha de nacimiento: 14/02/1970\n")
check("una fecha suelta con pinta de nacimiento no dispara nada", v == "coincide")
v, _ = ip.verificar("Ki-67 {{N}}%\nRB1 perdido\nfecha 14/02/1970 en la muestra\n")
check("una fecha sin etiqueta NO acredita: sigue siendo no_consta", v == "no_consta")
v, _ = ip.verificar(SUYO.upper())
check("mayúsculas: PACIENTE / FECHA DE NACIMIENTO también valen", v == "coincide")
v, _ = ip.verificar(SUYO.replace("Paciente:", "Paciente ="))
check("separador '=' en vez de ':' también vale", v == "coincide")

# ── sin overlay no se puede acreditar nada ────────────────────────────────────────────────
_os.rename(_os.path.join(_TMP, "tools", "perfil.local.json"), _os.path.join(_TMP, "tools", "perfil.off"))
import importlib  # noqa: E402
importlib.reload(ip)
v, _ = ip.verificar(SUYO)
check("sin overlay del titular → sin_overlay (no se inventa)", v == "sin_overlay")
check("   …y sin_overlay NO se sirve (fail-closed)", ip.SIRVE.get("sin_overlay") is False)
check("otro_paciente NO se sirve", ip.SIRVE.get("otro_paciente") is False)
check("parcial y no_consta SÍ se sirven, pero con aviso",
      ip.SIRVE["parcial"] and ip.SIRVE["no_consta"]
      and "parcial" in ip.PIDE_AVISO and "no_consta" in ip.PIDE_AVISO)
check("coincide se sirve SIN aviso", ip.SIRVE["coincide"] and "coincide" not in ip.PIDE_AVISO)
_os.rename(_os.path.join(_TMP, "tools", "perfil.off"), _os.path.join(_TMP, "tools", "perfil.local.json"))

# ── la ventanilla: un veredicto que no sirve tiene que RECHAZAR de verdad ─────────────────
LECTOR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                       "tools", "lector_clinico.py")
zona = _os.path.join(_TMP, "zona")
_os.makedirs(zona, exist_ok=True)
ajeno = _os.path.join(zona, "informe_ajeno.md")
with open(ajeno, "w", encoding="utf-8") as fh:
    fh.write(AJENO)
p = subprocess.run([_sys.executable, LECTOR, ajeno], capture_output=True, text=True, timeout=20,
                   env=dict(_os.environ, BTP_REPO=_TMP))
check("la ventanilla rechaza una ruta que ni siquiera es zona clínica (antes que nada)",
      p.returncode == 1 and "zona clínica" in (p.stderr or ""))
check("   …y no ha escrito el contenido por stdout", "Anselmo" not in (p.stdout or ""))

for desc, ok in casos:
    print(("  ✅ " if ok else "  ❌ ") + desc)
shutil.rmtree(_TMP, ignore_errors=True)
print()
print("RESULTADO identidad_paciente: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ LA CARPETA NO ACREDITA AL PACIENTE" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
