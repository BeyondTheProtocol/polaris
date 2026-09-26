#!/usr/bin/env python3
"""test_gate_s15.py — los 12 checks de salida que nacieron del punto S15.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: «28 de
53 normas de salida sin mecanismo» (24 al recontarlo esa noche). De esas 24, doce caben en una regex
o en las tools del turno y aquí están; las otras doce justifican en su `nota` de normas.json por qué
no (y `test_las_que_no_caben_lo_justifican` lo exige).
26-sep-26 (plan del juez, capa 3, pasos 2 y 5): de esas doce, `no-inventarle-frases` pasó a tener
check (`frase_inventada`, en aviso, ver tests/test_juez_capa3.py) y cuatro se reclasificaron a
`contexto` con su motivo en la nota. Quedan 7 de salida sin mecanismo (49 de salida, 42 con él).

Cada check con POSITIVOS (lo que tiene que cazar) y NEGATIVOS (los falsos positivos que el replay de
30 días, 2.781 turnos reales, encontró en la primera versión, reescritos sin datos suyos). Además el
contrato: registrados en normas.json, activos en el hook y en modo `aviso`. Nada pasa a bloqueo
sin el OK de {{TITULAR}} (escalera del gate).
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402
import normas  # noqa: E402

LARGO = " Te dejo el resto del detalle en la nota archivada para cuando tengas un rato." * 8

# (texto, tools). tools=[] = se leyó el turno y no se usó nada; None = no se sabe.
CASOS = {
    "proceso_en_respuesta": {
        "pos": [("Tienes razón, fallé ahí. Lección guardada: la próxima vez miro el hilo antes.", []),
                ("Vale, me lo apunto y no volverá a pasar. El dato correcto es el del informe.", [])],
        "neg": [  # replay: en cierres de sistema, la memoria es el entregable
            ("TL;DR: casa base en verde y la memoria nueva ya está registrada en el índice.", []),
            ("El conector sigue igual y el dato correcto es el del informe.", [])],
    },
    "jerga_interna": {
        "pos": [("Montado hoy: muro B1 y B2 más choke-point, lazo P1 con dispatcher un-cerebro.", []),
                ("Estado: P1 hecho, P3 en curso, A4 bloqueado, todo lo demás en verde por ahora.", [])],
        "neg": [  # replay: un daemon con nombre propio, repetido, no es jerga amontonada
            ("El dispatcher está vivo; reinicié el dispatcher y el dispatcher responde bien.", []),
            ("P2 (la escalera del gate) está hecha y P6 (el modelo local) no se engancha.", []),
            ("El fichero `tools/dispatcher.py` y `fail-open` salen en código, no en prosa.", [])],
    },
    "correo_sin_html": {
        "pos": [("Tienes el borrador para {{CONTACTO}} en Gmail, sin enviar.",
                 ["mcp__gmail__create_draft", g.SIN_HTML])],
        "neg": [("Tienes el borrador para {{CONTACTO}} en Gmail, sin enviar.",
                 ["mcp__gmail__create_draft"]),
                ("Tienes el borrador para {{CONTACTO}} en Gmail, sin enviar.", None)],
    },
    "borrador_sin_voz": {
        "pos": [("**Borrador para {{CONTACTO}} (no enviado):**\n\n> Hola {{CONTACTO}}, ¿te va bien el jueves?\n",
                 ["Bash"]),
                ("**DM para {{CONTACTO}} (X):**\n\n> Hi {{CONTACTO}}! Quick update from my side.\n", []),
                # 25-sep-26 (punto 08): sin etiqueta, pero es un correo suyo en primera persona.
                ("Te dejo el correo, listo para mandar:\n\n> Hola Mafalda, gracias por preguntarme "
                 "qué quiero sacar del bloque. Te cuento lo que tengo en mente.\n", ["Read"])],
        "neg": [
            # 25-sep-26: un prompt para otra sesión y la cita de un tercero no son su voz.
            ("**Mensaje para la otra sesión:**\n\n> Fusiona a casa base la rama claude/drive-x, "
             "que tengo la suite en verde.\n", []),
            ("**Lo que dice en su post:**\n\n> «Le escribo desde el móvil y decide qué "
             "herramienta usar» (25-abr-2026)\n", []),
            ("Mensaje para Natán, muy personal, como lo dijiste:\n\n> Hola Natán, apuntada la de "
             "mañana a las 17:00, ahí estaré con lo que me pediste.\n", []),
            ("**DM para {{CONTACTO}} (X):**\n\n> Hi {{CONTACTO}}! Quick update from my side.\n",
             ["Agent", "voz-titular"]),
            # replay: un cierre que lista lo que espera su OK no es un texto en su nombre
            ("**Qué espera tu OK**, todo en borrador para que firmes tú:\n\n> publícalo\n", []),
            # replay: retoques de un borrador ya voiceado en un turno anterior
            ("Cambio de sujeto, el resto se queda.\n\n**Post para LinkedIn:**\n\n> I need to get "
             "my genome sequenced.\n", []),
            ("**Borrador para {{CONTACTO}}:**\n\n> Hola {{CONTACTO}}.\n", None)],
    },
    "dm_formato_email": {
        "pos": [("**DM para Instagram:**\n\n> Estimado Carlos, le escribo para comentarle una idea."
                 "\n> Atentamente, {{TITULAR}}\n", [])],
        "neg": [  # replay: los dos disparos eran correos que nombraban LinkedIn
            ("El correo a {{CENTRO}}, y aparte lo comparto en LinkedIn:\n\n> Dear Dr. Keller, thanks."
             "\n", []),
            ("**DM para Instagram:**\n\n> ¡Holaa! Me encantó tu vídeo 💜 ¿hablamos?\n", [])],
    },
    "suplemento_clinico": {
        "pos": [("Ficha del suplemento de AHCC: la marca Kinoko tiene certificado de pureza y lote "
                 "trazable. Además, un metaanálisis sugiere que mejora el apetito.", []),
                ("El suplemento de vitamina D en cápsulas de esta marca está certificado; "
                 "pregúntale a Lola antes de empezar.", [])],
        "neg": [  # replay: un ensayo que menciona cápsulas no es una ficha de producto
            ("TROPION-Breast06 es de un solo brazo; la marca del fármaco va en cápsulas y el "
             "PMID 12345678 describe la fase 2.", []),
            ("Ficha del suplemento: marca Solgar, certificado de terceros, lote trazable, sin "
             "excipientes raros. Es el mejor en calidad-precio.", [])],
    },
    "cierre_sin_lo_tuyo": {
        "pos": [("Hecho. Arreglé las dos regex del gate y la batería va en verde." + LARGO, [])],
        "neg": [("Hecho. Arreglé las dos regex del gate y la batería va en verde." + LARGO
                 + "\n\nLo tuyo ahora: nada.", []),
                ("Hecho. Arreglé las dos regex." + LARGO + "\n\n**Siguiente:** fusionar con tu OK.",
                 []),
                ("Arreglé las dos regex del gate y la batería va en verde." + LARGO, [])],
    },
    "marca_lidera_causa": {
        "pos": [("Te dejo el pitch a la marca:\n\n> Tengo {{DIAGNOSTICO}} y me encantaría "
                 "colaborar con vosotros.\n", [])],
        "neg": [("Te dejo el pitch a la marca:\n\n> Soy ingeniera y construyo herramientas con IA; "
                 "me encaja una colab con vuestro producto.\n", []),
                # replay: un hilo de X sobre el caso no es un pitch a una marca
                ("## X: el hilo\n\n> En mayo, un PET no encontró nada en mi hígado.\n", [])],
    },
    "correo_no_existe": {
        "pos": [("No hay ningún correo de {{CENTRO}} con los resultados todavía.",
                 ["mcp__gmail__search_threads"]),
                # 25-sep-26: la persona como sujeto, justo el fallo del 22-jun (el AWB).
                ("Confirmado que Foundation aún no ha escrito, así que la frase es cierta.",
                 ["mcp__gmail__search_threads"])],
        "neg": [("No hay ningún correo de {{CENTRO}} con los resultados todavía.",
                 ["mcp__gmail__search_threads", "python3 tools/correo_imap.py buscar {{CENTRO}}"]),
                ("No lo veo por la API, que va con retraso; lo confirmo en el navegador.",
                 ["mcp__gmail__search_threads"]),
                ("No hay ningún correo de {{CENTRO}} con los resultados todavía.", ["Read"]),
                # 25-sep-26: leer el buzón del poller IMAP es mirar en vivo.
                ("No hay ningún correo de {{CENTRO}} con los resultados todavía.",
                 ["mcp__gmail__search_threads", "tools/state/correo/buzon.json"]),
                # Un plan condicional no concluye nada.
                ("Si no ha contestado para el 1-oct, la vía cálida es escribir a otra persona.",
                 ["mcp__gmail__search_threads"])],
    },
    "respuesta_en_ingles": {
        "pos": [("This is the summary of what I did today: I fixed the two regexes in the gate and "
                 "the battery is green. The rest of the work is in the branch and it was not merged "
                 "yet, because that is your decision. There is one red test that is not mine, and "
                 "it is about the debt book. I will leave it for the next session with the notes.",
                 [])],
        "neg": [  # replay: el borrador en inglés para un tercero va tras el separador
            ("Todo junto, en un solo mensaje para {{CONTACTO}}:\n\n---\n\nQuick follow-up on the PET. "
             "I cross-matched it against the liver lesions on the same-day CT and it was not "
             "validated by a radiologist. This is the result and what I think it means for the "
             "next scan, and what I would like to ask you about it when you have the time.", []),
            ("API Error: the request was flagged. " + "This is not a response to you. " * 12, [])],
    },
    "tono_builder": {
        "pos": [("Post para X:\n\n```\nBuilding in public mi sistema de IA 🚀 let's go\n```\n", [])],
        "neg": [  # replay: 10x Genomics y el envío (shipping) de muestras no son tono builder
            ("Post para LinkedIn:\n\n> We use 10x Flex and the samples shipped on Monday.\n", []),
            ("Post para X:\n\n```\nSoy ingeniera y construyo el sistema que ordena mi caso.\n```\n",
             [])],
    },
    "lola_sin_nota": {
        "pos": [("He añadido el pulsioxímetro a la nota de la compra.",
                 ["Bash", "osascript -e 'tell application \"Notes\" to ... Comprar para TB06'"])],
        "neg": [("He añadido el pulsioxímetro a las dos notas.",
                 ["Bash", "osascript ... Comprar para TB06", "osascript ... Consultar con Lola"]),
                ("Ya está en «Comprar para TB06», que también recoge la nota de Lola.",
                 ["osascript ... Comprar para TB06"]),
                ("He añadido el pulsioxímetro a la nota.", None)],
    },
}


class S15(unittest.TestCase):
    def test_positivos(self):
        for check, c in CASOS.items():
            for t, tools in c["pos"]:
                self.assertIsNotNone(g.CHECKS[check](t, tools), "%s NO cazó: %s" % (check, t[:70]))

    def test_negativos(self):
        for check, c in CASOS.items():
            for t, tools in c["neg"]:
                self.assertIsNone(g.CHECKS[check](t, tools),
                                  "%s falso positivo: %s" % (check, t[:70]))

    def test_registrados_activos_y_en_aviso(self):
        reglas = {r["check"]: r for r in normas.reglas_salida()}
        for check in CASOS:
            self.assertIn(check, reglas, "%s no está declarado en normas.json" % check)
            self.assertEqual(reglas[check]["modo"], "aviso",
                             "%s: la subida a bloqueo necesita el OK de {{TITULAR}}" % check)
        activas = {c for c, _s, _m in g._reglas_activas()[0]}
        self.assertTrue(set(CASOS) <= activas, "no corren en el hook: %s" % (set(CASOS) - activas))

    def test_en_aviso_no_bloquean(self):
        t, tools = CASOS["proceso_en_respuesta"]["pos"][0]
        h = [x for x in g.revisar(t + " Sigue todo lo demás igual que antes.", tools)
             if x[0] == "proceso_en_respuesta"]
        self.assertTrue(h)
        self.assertEqual(g._bloquean(h, "aviso"), [])

    def test_marca_de_borrador_sin_html(self):
        """`_marcas` es lo que deja ver la ENTRADA de create_draft; el replay usa la misma."""
        self.assertEqual(g._marcas("mcp__g__create_draft", {"to": ["tercero@correo.example"], "body": "x"}),
                         [g.SIN_HTML])
        self.assertEqual(g._marcas("mcp__g__create_draft",
                                   {"to": ["tercero@correo.example"], "body": "x", "htmlBody": "<b>x</b>"}), [])
        self.assertEqual(g._marcas("mcp__g__create_draft", {"to": [g.TITULAR_MAIL], "body": "x"}), [])
        self.assertEqual(g._marcas("mcp__g__update_draft", {"draftId": "1", "body": "x"}),
                         [g.SIN_HTML])
        self.assertEqual(g._marcas("Bash", {"command": "ls"}), [])

    def test_las_que_no_caben_lo_justifican(self):
        """Ninguna norma de salida se queda sin mecanismo en silencio: o lo tiene, o su nota dice
        por qué no (S15)."""
        d = normas.cargar()
        mudas = [n["slug"] for n in d["normas"]
                 if n.get("clase") == "salida" and not n.get("mecanismo")
                 and "S15" not in (n.get("nota") or "")]
        self.assertEqual(mudas, [], "normas de salida sin mecanismo ni justificación: %s" % mudas)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ gate S15: %d tests en verde" % r.testsRun if ok
          else "❌ gate S15: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
