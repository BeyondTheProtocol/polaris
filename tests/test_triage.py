#!/usr/bin/env python3
"""test_triage.py — el gestor de tareas (triage_tareas): decide qué es tarea y qué no, y mete lo
claro en la lista única (seguimiento). Política «añade lo claro, pregunta lo dudoso». + EL MURO.
Aísla todo en un tmp; no toca el repo real.

Ejecuta:  python3 tests/test_triage.py
"""
import importlib
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="test_triage_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

import seguimiento  # noqa: E402
importlib.reload(seguimiento)
import triage_tareas as tt  # noqa: E402
importlib.reload(tt)

MANANA = (__import__("datetime").date.today() + __import__("datetime").timedelta(days=1)).isoformat()
_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ok ·", name)
    else:
        _fail += 1
        print("  FALLA ·", name)


def main():
    # ───── clasificar: tarea / no / dudosa ─────
    c1 = tt.clasificar("recuérdame llamar a la aseguradora mañana", "chat")
    check("acción explícita → tarea", c1["veredicto"] == "tarea")
    check("extrae fecha 'mañana' → vence", c1["campos"].get("vence") == MANANA)
    check("limpia el título (sin 'recuérdame')", "recuérdame" not in c1["campos"].get("titulo", "").lower())

    check("saludo/acuse → no", tt.clasificar("gracias!", "chat")["veredicto"] == "no")
    check("'ok vale' → no", tt.clasificar("ok vale", "chat")["veredicto"] == "no")
    check("frase sin señal → dudosa", tt.clasificar("lo de {{CONTACTO}}", "voz")["veredicto"] == "dudosa")
    check("verbo en frase larga → tarea", tt.clasificar("hay que comprar los billetes a Zúrich", "chat")["veredicto"] == "tarea")
    check("etiqueta por palabra clave (token Meta → Gestión)",
          tt.clasificar("desbloquear el token de Meta", "chat")["campos"].get("etiqueta") == "Gestión")
    check("etiqueta clínica (biopsia → NED)",
          tt.clasificar("preparar el checklist de la biopsia", "chat")["campos"].get("etiqueta") == "NED")

    # ───── triar: lo claro entra en la lista única; lo dudoso a la cola; el ruido se descarta ─────
    r1 = tt.triar("recuérdame pedir cita con la oncóloga el viernes", "chat")
    check("tarea clara → creada", r1["accion"] == "creada" and r1.get("id"))
    items = seguimiento.recopilar().get("items", [])
    check("la tarea creada está en la lista única (la que lee el parte)",
          any(r1["id"] == i.get("id") for i in items))

    r2 = tt.triar("gracias, genial", "chat")
    check("ruido → descartada (no crea)", r2["accion"] == "descartada")

    r3 = tt.triar("lo de {{CONTACTO}}", "voz")
    check("dudosa → a la cola de preguntar", r3["accion"] == "preguntar")
    check("la dudosa queda encolada", any("{{CONTACTO}}" in d.get("texto", "") for d in tt.listar_dudosas()))

    # ───── EL MURO ─────
    src = open(os.path.join(TOOLS, "triage_tareas.py"), encoding="utf-8").read()
    VIAS = ["import salida", "from salida", "salida.", "import requests", "import socket",
            "import smtplib", "from urllib.request", "http.client", "subprocess"]
    presentes = [p for p in VIAS if p in src]
    check("triage_tareas no tiene NINGUNA vía hacia fuera (%s)" % (presentes or "limpio"), not presentes)

    # comportamiento: triar NO dispara nada de salida.py
    import salida
    trampa = []
    orig = {}
    for fn in ("report_to_titular", "report_file_to_titular", "alerta_critica", "react_to_titular"):
        if hasattr(salida, fn):
            orig[fn] = getattr(salida, fn)
            setattr(salida, fn, (lambda nm: (lambda *a, **k: trampa.append(nm)))(fn))
    try:
        tt.triar("recuérdame revisar el correo mañana", "chat")
        tt.triar("hola", "chat")
        tt.triar("algo ambiguo sin verbo", "chat")
    finally:
        for fn, f in orig.items():
            setattr(salida, fn, f)
    check("triar NO dispara salida (%s)" % (trampa or "ninguna"), trampa == [])

    print("\nRESULTADO triage: %d OK, %d fallos" % (_pass, _fail))
    print("✅ TRIAGE EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
