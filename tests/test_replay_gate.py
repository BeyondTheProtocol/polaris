#!/usr/bin/env python3
"""test_replay_gate.py — el replay del gate de salida parte bien los turnos y cuenta bien.

Transcript sintético en un tmp (BTP_PROJECTS_DIR), nunca los de verdad. Contrato:
  · un turno = de un mensaje de {{TITULAR}} (claude-desktop) al siguiente; los tool_result, avisos de
    hooks, <task-notification> y sub-agentes NO abren turno;
  · la respuesta que se audita es la ÚLTIMA de texto del turno; las tools, todas las del turno;
  · un check que salta en la respuesta cuenta 1, y el que no, 0;
  · --gate compara contra otro fichero de gate.
"""
import json
import os
import sys
import tempfile
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="test_replay_gate_")
_PROJ = os.path.join(_TMP, "-Users-polaris-claudecode")
os.makedirs(_PROJ)
os.environ["BTP_PROJECTS_DIR"] = _TMP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import replay_gate as rg  # noqa: E402


def _user(t, **kw):
    o = {"type": "user", "entrypoint": "claude-desktop", "message": {"role": "user", "content": t}}
    o.update(kw)
    return o


def _asis(texto=None, tool=None):
    c = []
    if texto:
        c.append({"type": "text", "text": texto})
    if tool:
        c.append({"type": "tool_use", "name": tool[0], "input": tool[1]})
    return {"type": "assistant", "message": {"role": "assistant", "content": c}}


DELEGA = ("No hecho: nada enviado, y no puedo borrar el borrador equivocado de {{CENTRO}}, "
          "tienes que hacerlo tú desde Gmail esta misma tarde.")

EVENTOS = [
    _user("mira el correo de {{CENTRO}}"),
    _asis("Voy a mirarlo.", ("Bash", {"command": "python3 tools/correo_imap.py buscar {{CENTRO}}"})),
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
    _asis(DELEGA),                                       # última respuesta del turno 1
    _user("Stop hook feedback:\n[gate_salida.py]: corrige esto"),   # NO abre turno
    _user("<task-notification>hecho</task-notification>"),          # NO abre turno
    _user("sub-agente", isSidechain=True),                          # NO abre turno
    _user("vale, gracias"),
    _asis("De nada. Quedo con el borrador en su sitio y el hilo del tablero actualizado."),
]


class ReplayGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ruta = os.path.join(_PROJ, "s.jsonl")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("{no es json\n")                      # fail-soft
            for e in EVENTOS:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        cls.ruta = ruta

    def test_parte_en_dos_turnos(self):
        t = rg.turnos(self.ruta)
        self.assertEqual(len(t), 2, t)

    def test_audita_la_ultima_respuesta_y_todas_las_tools(self):
        texto, tools = rg.turnos(self.ruta)[0]
        self.assertEqual(texto, DELEGA)
        self.assertIn("Bash", tools)
        self.assertTrue(any("correo_imap" in x for x in tools))

    def test_cuenta_el_disparo(self):
        r = rg.replay(dias=1, solo=["no_puedo_falso"], n_ejemplos=5)
        self.assertEqual(r["turnos"], 2)
        self.assertEqual(r["hits"]["no_puedo_falso"]["n"], 1)
        self.assertEqual(len(r["hits"]["no_puedo_falso"]["ejemplos"]), 1)

    def test_ventana_deja_fuera_lo_viejo(self):
        viejo = os.path.join(_PROJ, "viejo.jsonl")
        with open(viejo, "w", encoding="utf-8") as f:
            for e in EVENTOS:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        hace = time.time() - 30 * 86400
        os.utime(viejo, (hace, hace))
        try:
            self.assertEqual(rg.replay(dias=1, solo=["no_puedo_falso"])["turnos"], 2)
        finally:
            os.remove(viejo)

    def test_compara_contra_otro_gate(self):
        otro = os.path.join(_TMP, "gate_mudo.py")
        with open(otro, "w", encoding="utf-8") as f:
            f.write("MIN_CHARS = 80\nURGENTE = ()\nSIN_MINIMO = set()\n"
                    "CHECKS = {'no_puedo_falso': lambda t, tools=None: None}\n")
        g = rg.cargar_gate(otro, "gate_mudo")
        self.assertEqual(rg.replay(dias=1, gate=g)["hits"], {})


if __name__ == "__main__":
    unittest.main()
