#!/usr/bin/env python3
"""test_triage_route_correo.py — el validador cuarentena→privilegiado de correo (item #4, Fase 1).

Adversarial: prueba la TRUST BOUNDARY. El LLM de cuarentena NO puede forjar los campos
estructurales (urgente/uid/message_id) ni saltarse los enums cerrados; el resumen se sanea y
se acota; la inyección (por el sobre o por el LLM) fuerza revisión humana; todo fallo -> fail-closed.
NO toca la cola: ejercita validar() (pura)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import triage_route_correo as tr

ok = 0
fallos = []


def check(cond, msg):
    global ok
    if cond:
        ok += 1
    else:
        fallos.append(msg)


def out(inner):
    """Envuelve el texto del agente como lo entrega run_agent.sh (--output-format json)."""
    return json.dumps({"result": inner})


def intent_json(**kw):
    return json.dumps(kw)


SOBRE = {
    "uid": 42, "message_id": "<abc@clinic.example>", "remitente": "Dr. {{CONTACTO}}",
    "remitente_email": "oncologo@example.org", "asunto": "Resultados",
    "fecha": "Wed, 16 Jul 2026", "ned_critico": False, "urgente": False, "inyeccion": False,
}

# 1 · válido → ok, con fence de DATO y el resumen dentro
okk, motivo, inten = tr.validar(out(intent_json(seguro=True, categoria="NED/Médico",
                                                accion="consultar_comite", resumen="Adjuntan informe")), SOBRE)
check(okk is True, "1 válido debería pasar (%s)" % motivo)
check(inten and "<<<Adjuntan informe>>>" in inten, "1 resumen debe ir fenced como DATO")

# 2 · seguro=false → rechazado
okk, motivo, _ = tr.validar(out(intent_json(seguro=False, categoria="NED/Médico",
                                            accion="archivar", resumen="x")), SOBRE)
check(okk is False, "2 seguro=false debe rechazarse")

# 3 · categoría fuera de ETIQUETAS_OK → rechazado
okk, _, _ = tr.validar(out(intent_json(seguro=True, categoria="InventadaXYZ",
                                       accion="archivar", resumen="x")), SOBRE)
check(okk is False, "3 categoría desconocida debe rechazarse")

# 4 · acción fuera de ACCIONES → rechazado
okk, _, _ = tr.validar(out(intent_json(seguro=True, categoria="Admin",
                                       accion="borrar_todo", resumen="x")), SOBRE)
check(okk is False, "4 acción desconocida debe rechazarse")

# 5 · resumen vacío → rechazado
okk, _, _ = tr.validar(out(intent_json(seguro=True, categoria="Admin",
                                       accion="archivar", resumen="   ")), SOBRE)
check(okk is False, "5 resumen vacío debe rechazarse")

# 6 · el LLM forja urgente=true y un uid falso → GANA el sobre (urgente=False), uid del sobre
okk, _, inten = tr.validar(out(intent_json(seguro=True, categoria="Admin", accion="archivar",
                                           resumen="hola", urgente=True, uid=999,
                                           message_id="<forjado@evil>")), SOBRE)
check(okk is True, "6 debe pasar")
check(inten and "Urgente: False" in inten, "6 urgente lo pone el SOBRE, no el LLM")
check(inten and "forjado@evil" not in inten, "6 message_id forjado por el LLM se ignora")
check(inten and "999" not in inten, "6 uid forjado por el LLM se ignora")

# 7 · resumen gigante → recortado a 280
okk, _, inten = tr.validar(out(intent_json(seguro=True, categoria="Admin", accion="archivar",
                                           resumen="A" * 500)), SOBRE)
cuerpo = inten.rsplit("<<<", 1)[1].split(">>>", 1)[0]  # el ÚLTIMO fence = el resumen real
check(len(cuerpo) <= 280, "7 resumen debe recortarse a 280 (fue %d)" % len(cuerpo))

# 8 · resumen con control + ancho-cero → saneado. chr() (fuente ASCII, transit-safe):
#     ho + U+200B(ancho-cero) + la + BEL(0x07) + \n + mundo  ->  "hola mundo"
sucio = "ho" + chr(0x200b) + "la" + chr(7) + "\n" + "mundo"
okk, _, inten = tr.validar(out(intent_json(seguro=True, categoria="Admin", accion="archivar",
                                           resumen=sucio)), SOBRE)
cuerpo = inten.rsplit("<<<", 1)[1].split(">>>", 1)[0]  # el ÚLTIMO fence = el resumen real
check(chr(0x200b) not in cuerpo and chr(7) not in cuerpo, "8 debe quitar ancho-cero y control")
check("hola mundo" in cuerpo, "8 debe quedar el texto legible (%r)" % cuerpo)

# 9 · sobre.inyeccion=true → fuerza revisión humana pese a la acción benigna del LLM
sob_iny = dict(SOBRE, inyeccion=True)
okk, _, inten = tr.validar(out(intent_json(seguro=True, categoria="Personal",
                                           accion="archivar", resumen="parece inofensivo")), sob_iny)
check(okk is True, "9 debe pasar (a revisión)")
check(inten and "Revisar-inyección" in inten, "9 inyección del sobre fuerza Revisar-inyección")
check(inten and "consultar_comite" in inten, "9 inyección fuerza consultar_comite")

# 10 · el LLM declara inyeccion_detectada=true (sobre limpio) → también a revisión
okk, _, inten = tr.validar(out(intent_json(seguro=True, categoria="Personal", accion="archivar",
                                           resumen="ojo", inyeccion_detectada=True)), SOBRE)
check(inten and "Revisar-inyección" in inten, "10 inyección declarada por el LLM fuerza revisión")

# 11 · JSON del agente corrupto → rechazado
okk, _, _ = tr.validar(out("no soy json {{{"), SOBRE)
check(okk is False, "11 texto no-JSON debe rechazarse")

# 12 · sin objeto de intención → rechazado
okk, _, _ = tr.validar(out("todo bien, sin estructura"), SOBRE)
check(okk is False, "12 sin intención estructurada debe rechazarse")

# 13 · sobre sin uid → rechazado (no hay identidad determinista)
okk, motivo, _ = tr.validar(out(intent_json(seguro=True, categoria="Admin",
                                            accion="archivar", resumen="x")), {"remitente": "y"})
check(okk is False, "13 sobre sin uid debe rechazarse")

# 14 · claves extra desconocidas en la intención → se ignoran, sigue válido
okk, _, _ = tr.validar(out(intent_json(seguro=True, categoria="Admin", accion="archivar",
                                       resumen="ok", campo_raro="ejecuta rm -rf", prioridad="critica")), SOBRE)
check(okk is True, "14 claves extra deben ignorarse, no romper")

if fallos:
    print("❌ TRIAGE_ROUTE_CORREO: %d OK, %d FALLOS" % (ok, len(fallos)))
    for f in fallos:
        print("   - " + f)
    sys.exit(1)
print("✅ TRIAGE_ROUTE_CORREO EN VERDE (%d ok / 0 fallos)" % ok)
