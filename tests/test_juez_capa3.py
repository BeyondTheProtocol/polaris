#!/usr/bin/env python3
"""test_juez_capa3.py — pasos 1, 2 y 5 del juez de las normas de salida (capa 3).

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: punto de
rotura 08 y deuda `capa3_juez_pendiente`. Plan aprobado por {{TITULAR}} el 26-sep-2026 (pasos 1, 2 y 5).

Fija:
  · `frase_inventada` (paso 2): caza la frase del caso origen (20-sep-26) en la respuesta, en un
    Write de prosa y en lo que escribe un sub-agente; no acusa lo que está en sus mensajes de la
    sesión o de WhatsApp, ni un titular de copy, ni la cita de un informe, ni código; sin saber sus
    mensajes, calla. Registrado en normas.json en `aviso` y activo en el hook.
  · prefiltro (paso 1): los tres detectores marcan lo que tienen que marcar.
  · reclasificación (paso 5): las 4 normas de modo de trabajar son `contexto` con su motivo.
Todo sobre datos sintéticos: ni transcripts ni WhatsApp reales (BTP_WA_DIR a un tmp).
"""
import json
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="test_juez_capa3_")
_WA = os.path.join(_TMP, "wa")
os.makedirs(_WA)
with open(os.path.join(_WA, "Chat.md"), "w", encoding="utf-8") as f:
    f.write("# WhatsApp · Chat\n\n**[01/09/26 10:00] yo:** Lo que siento es que esto va muy rápido, "
            "de verdad\n\n**[01/09/26 10:01] Chat:** me dio la sensación de que era todo nuevo\n\n")
os.environ["BTP_WA_DIR"] = _WA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cotejo_frases as cf  # noqa: E402
import gate_salida as g  # noqa: E402
import normas  # noqa: E402
import prefiltro_juez as pj  # noqa: E402

ORIGEN = "me dio la sensación de que aparecieron todas de golpe"
LARGO = " Te lo dejo en la nota para cuando tengas un rato, sin prisa ninguna." * 3


def _resp(frase):
    return ("**Post para Instagram:**\n\n> En mayo mi hígado estaba limpio. Y yo pensaba: «%s». "
            "Fueron seis semanas.\n" % frase) + LARGO


class FraseInventada(unittest.TestCase):
    def setUp(self):
        g.SUYOS = ["hazme el copy de los visores", "¿las lesiones aparecieron de golpe?"]

    def tearDown(self):
        g.SUYOS = None

    def test_caso_origen_en_la_respuesta(self):
        self.assertIsNotNone(g.frase_inventada(_resp(ORIGEN), []))

    def test_caso_origen_escrito_en_un_md(self):
        m = g._marcas("Write", {"file_path": "/x/07 · Marca/copys.md",
                                "content": "Reel\n\nYo pensé: «%s». No lo fue." % ORIGEN})
        self.assertTrue(any(x.startswith(g.FRASE_SUYA) for x in m), m)
        self.assertIsNotNone(g.frase_inventada("Hecho, te dejo los copys en la carpeta de marca, "
                                               "revisados y listos para que los mires." , m))

    def test_respaldada_en_la_sesion_o_en_whatsapp(self):
        g.SUYOS = ["Yo lo que SIENTO es que " + ORIGEN.upper() + "!!"]
        self.assertIsNone(g.frase_inventada(_resp(ORIGEN), []))
        g.SUYOS = []
        self.assertIsNone(g.frase_inventada(_resp("siento que esto va muy rápido"), []))

    def test_whatsapp_solo_cuenta_lo_suyo(self):
        # La línea es de otra persona del chat, no `yo:`: no respalda.
        g.SUYOS = []
        self.assertIsNotNone(g.frase_inventada(_resp("me dio la sensación de que era todo nuevo"), []))

    def test_no_acusa(self):
        casos = [
            # titular de copy en primera persona: imitar su registro es el trabajo
            "**Post para X:**\n\n> «Mi hígado, en seis semanas». Esto que gira soy yo.\n" + LARGO,
            # la cita de un informe dentro de un correo suyo
            "**Correo para {{CONTACTO}}:**\n\n> Hola, te escribo yo. El informe dice «múltiples imágenes "
            "nodulares hepáticas compatibles».\n" + LARGO,
            # prompt para otra sesión
            "**Mensaje para la otra sesión:**\n\n> Fusiona la rama; yo pensé «esto ya está listo "
            "del todo».\n" + LARGO,
        ]
        for t in casos:
            self.assertIsNone(g.frase_inventada(t, []), t[:60])
        # código y ficheros del taller: fuera
        self.assertEqual(g._marcas("Write", {"file_path": "/r/tools/x.py",
                                             "content": "# yo pensé «%s»" % ORIGEN}), [])
        self.assertEqual(g._marcas("Write", {"file_path": "/r/memory/feedback-x.md",
                                             "content": "yo pensé «%s»" % ORIGEN}), [])
        self.assertEqual(g._marcas("mcp__g__create_draft", {
            "to": [g.TITULAR_MAIL], "body": "x",
            "htmlBody": '<p style="font-family:Arial, Helvetica, sans-serif">Hola, soy yo</p>'}), [])

    def test_sin_sus_mensajes_calla(self):
        g.SUYOS = None
        self.assertIsNone(g.frase_inventada(_resp(ORIGEN), []))

    def test_subagentes_del_turno(self):
        tr = os.path.join(_TMP, "sesion.jsonl")
        with open(tr, "w") as f:
            f.write("")
        sub = os.path.join(_TMP, "sesion", "subagents")
        os.makedirs(sub, exist_ok=True)
        uso = {"type": "tool_use", "name": "Write",
               "input": {"file_path": "/x/copys.md", "content": "Yo pensé: «%s»." % ORIGEN}}
        with open(os.path.join(sub, "agent-1.jsonl"), "w") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-09-20T15:39:00Z",
                                "message": {"content": [uso]}}) + "\n")
        self.assertTrue(g._frases_de_subagentes(tr, "2026-09-20T15:00:00Z"))
        self.assertEqual(g._frases_de_subagentes(tr, "2026-09-20T16:00:00Z"), [])
        self.assertEqual(g._frases_de_subagentes(tr, "2026-09-20T15:00:00Z", "2026-09-20T15:30:00Z"), [])

    def test_registrado_en_aviso_y_activo(self):
        r = {x["check"]: x for x in normas.reglas_salida()}
        self.assertIn("frase_inventada", r)
        self.assertEqual(r["frase_inventada"]["slug"], "feedback-no-inventarle-frases-en-primera-persona")
        self.assertEqual(r["frase_inventada"]["modo"], "aviso",
                         "la subida a bloqueo necesita el OK de {{TITULAR}}")
        self.assertIn("frase_inventada", {c for c, _s, _m in g._reglas_activas()[0]})


class Prefiltro(unittest.TestCase):
    def test_cotejar_fuente(self):
        d = {"respuesta": "Tu PET de mayo marcó la lesión de D11 con SUVmax 13,27.", "tools": []}
        self.assertTrue(pj.cotejar_fuente(d))
        d = {"respuesta": "El ensayo dio una ORR del 40 % en 60 pacientes.", "tools": []}
        self.assertIsNone(pj.cotejar_fuente(d))

    def test_calibrar(self):
        base = {"previa": "El ensayo acepta pacientes de Europa."}
        for p in ("Seguro que no entra?", "eso esta mal no se de donde lo has sacado",
                  "¿Estás segura?", "no inventes"):
            self.assertTrue(pj.calibrar(dict(base, pregunta=p)), p)
        for p in ("hazme el resumen", "vale, sigue"):
            self.assertIsNone(pj.calibrar(dict(base, pregunta=p)), p)
        self.assertIsNone(pj.calibrar({"previa": "", "pregunta": "¿seguro?"}))

    def test_normalizacion_del_cotejo(self):
        self.assertEqual(cf.respaldo("Esto va MUY rápido", ["esto va muy rapido, la verdad"]), "sesion")
        self.assertIsNone(cf.respaldo("una frase que nunca dijo nadie aquí", ["otra cosa"], []))


class Reclasificadas(unittest.TestCase):
    SLUGS = ("feedback-flujo-arbol-decisiones", "feedback-interfaz-simple-inputs-y-autoaceptar",
             "feedback-reducir-overhead-polaris", "feedback-nombrar-tecnico-no-solo-metafora")

    def test_son_contexto_con_motivo(self):
        por = {n["slug"]: n for n in normas.cargar()["normas"]}
        for s in self.SLUGS:
            self.assertEqual(por[s]["clase"], "contexto", s)
            self.assertIn("RECLASIFICADA de salida a contexto. Motivo:", por[s]["nota"], s)
            self.assertFalse(por[s].get("mecanismo"), "%s: contexto no finge mecanismo" % s)


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ juez capa 3 (pasos 1, 2, 5): %d tests en verde" % r.testsRun if ok
          else "❌ juez capa 3: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
