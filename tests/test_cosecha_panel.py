#!/usr/bin/env python3
"""test_cosecha_panel.py — al cerrar un turno con un panel de comités sale UN dossier, y nada más.

Paso 3 del plan de la caja (13-sep-2026). Lo que protege, por orden:
  1. Que NO grite: un turno sin panel (0-1 comités, agentes de fábrica, subagentes de turnos
     anteriores) no produce nada. Un aviso que salta en cada turno se ignora.
  2. Que no repita: el mismo panel no da dos dossiers ni dos avisos.
  3. Que el registro de medición no guarde ni una palabra de lo que dijeron los subagentes.
  4. Que sea fail-open de verdad: entrada rota, sesión inexistente o bypass → sale 0 sin decir nada.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("estado")
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(RAIZ, ".claude", "hooks", "cosecha_panel.py")
SID = "11111111-2222-3333-4444-555555555555"
FRASE_SECRETA = "frase-que-no-debe-salir-del-dossier-7731"

CONTRATO = """Resumen.

```json
{"decisiones": [{"titulo": "%s", "impacto": "alta", "recomendacion": "x", "porque": "y",
                 "fuente": "PMID abierto hoy"}], "acciones": []}
```"""


class TestCosechaPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cosecha-panel-")
        self.repo = os.path.join(self.tmp, "repo")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(os.path.join(self.repo, ".claude", "agents"))
        for slug in ("comite-medico", "verificacion"):
            open(os.path.join(self.repo, ".claude", "agents", slug + ".md"), "w").close()
        self.proy = os.path.join(self.tmp, "projects", "-proyecto")
        self.sub = os.path.join(self.proy, SID, "subagents")
        os.makedirs(self.sub)
        self.tp = os.path.join(self.proy, SID + ".jsonl")
        self.t0 = 1_780_000_000.0
        self._transcript([(self.t0 - 3600, "pedido viejo"), (self.t0, "compara las dos opciones")])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── utilidades ───────────────────────────────────────────────────────────────────
    def _transcript(self, pedidos):
        with open(self.tp, "w", encoding="utf-8") as f:
            for ts, texto in pedidos:
                iso = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z")
                f.write(json.dumps({"type": "user", "timestamp": iso,
                                    "message": {"role": "user", "content": texto}}) + "\n")
                # un tool_result posterior NO es un pedido de {{TITULAR}}
                f.write(json.dumps({"type": "user", "timestamp": iso, "message": {
                    "role": "user", "content": [{"type": "tool_result", "content": "x"}]}}) + "\n")

    def _subagente(self, agid, tipo, texto, ts):
        meta = os.path.join(self.sub, agid + ".meta.json")
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({"agentType": tipo, "description": "d"}, f)
        with open(os.path.join(self.sub, agid + ".jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"message": {"role": "assistant",
                                            "content": [{"type": "text", "text": texto}]}}) + "\n")
        os.utime(meta, (ts, ts))

    def _correr(self, datos=None, crudo=None, **env_extra):
        env = dict(os.environ, BTP_REPO=self.repo, BTP_STATE_DIR=self.state, **env_extra)
        env.pop("BTP_CAJA_OFF", None) if "BTP_CAJA_OFF" not in env_extra else None
        entrada = crudo if crudo is not None else json.dumps(
            datos if datos is not None else {"session_id": SID, "transcript_path": self.tp})
        p = subprocess.run([sys.executable, HOOK], input=entrada, capture_output=True, text=True,
                           env=env, timeout=30)
        return p.returncode, p.stdout.strip()

    def _dossiers(self):
        return sorted(f for f in os.listdir(self.sub) if f.startswith("dossier-"))

    def _panel(self):
        self._subagente("agent-a", "comite-medico", CONTRATO % ("opción A con " + FRASE_SECRETA),
                        self.t0 + 10)
        self._subagente("agent-b", "verificacion", CONTRATO % "la cita no aplica", self.t0 + 20)

    # ── 1. no gritar ─────────────────────────────────────────────────────────────────
    def test_un_solo_comite_no_da_dossier(self):
        self._subagente("agent-a", "comite-medico", CONTRATO % "algo", self.t0 + 10)
        self.assertEqual((0, ""), self._correr())
        self.assertEqual([], self._dossiers())

    def test_agentes_de_fabrica_no_cuentan_como_panel(self):
        self._subagente("agent-a", "comite-medico", CONTRATO % "algo", self.t0 + 10)
        self._subagente("agent-x", "Explore", "busqué ficheros", self.t0 + 15)
        self._subagente("agent-y", "general-purpose", "otra cosa", self.t0 + 16)
        self.assertEqual((0, ""), self._correr())

    def test_subagentes_de_un_turno_anterior_no_cuentan(self):
        self._subagente("agent-a", "comite-medico", CONTRATO % "viejo", self.t0 - 100)
        self._subagente("agent-b", "verificacion", CONTRATO % "viejo", self.t0 - 50)
        self.assertEqual((0, ""), self._correr())
        self.assertEqual([], self._dossiers())

    def test_sesion_sin_subagentes_no_hace_nada(self):
        shutil.rmtree(self.sub)
        self.assertEqual((0, ""), self._correr())

    # ── 2. el panel de verdad ────────────────────────────────────────────────────────
    def test_panel_de_dos_comites_da_un_dossier_y_un_aviso(self):
        self._panel()
        rc, out = self._correr()
        self.assertEqual(0, rc)
        msg = json.loads(out)["systemMessage"]
        self.assertIn("comite-medico", msg)
        self.assertIn("verificacion", msg)
        self.assertIn("2 decisiones", msg)
        self.assertEqual(1, len(self._dossiers()))
        self.assertIn(os.path.join(self.sub, self._dossiers()[0]), msg)
        with open(os.path.join(self.sub, self._dossiers()[0]), encoding="utf-8") as f:
            self.assertIn(FRASE_SECRETA, f.read(), "el dossier trae lo que dijeron")

    def test_no_repite_el_mismo_panel(self):
        self._panel()
        self._correr()
        self.assertEqual((0, ""), self._correr(), "segunda pasada: calla")
        self.assertEqual(1, len(self._dossiers()))

    def test_asiento_sin_contrato_se_dice(self):
        self._subagente("agent-a", "comite-medico", CONTRATO % "con contrato", self.t0 + 10)
        self._subagente("agent-b", "verificacion", "Te lo cuento en prosa, sin JSON.", self.t0 + 20)
        rc, out = self._correr()
        self.assertIn("sin bloque JSON del contrato: verificacion", json.loads(out)["systemMessage"])

    def test_panel_sin_ningun_contrato_calla_pero_lo_apunta(self):
        """El caso REAL del 13-sep: dos comités, ninguno cerró con el JSON. Un dossier vacío es ruido."""
        self._subagente("agent-a", "comite-medico", "Prosa sin JSON.", self.t0 + 10)
        self._subagente("agent-b", "verificacion", "Más prosa sin JSON.", self.t0 + 20)
        self.assertEqual((0, ""), self._correr())
        self.assertEqual([], [f for f in self._dossiers() if f.endswith(".md")], "sin dossier vacío")
        with open(os.path.join(self.state, "caja", "dossiers.jsonl"), encoding="utf-8") as f:
            fila = json.loads(f.read().strip().splitlines()[-1])
        self.assertTrue(fila["vacio"])
        self.assertEqual(["comite-medico", "verificacion"], fila["sin_contrato"])
        self.assertEqual((0, ""), self._correr(), "y no lo vuelve a apuntar en la siguiente pasada")
        with open(os.path.join(self.state, "caja", "dossiers.jsonl"), encoding="utf-8") as f:
            self.assertEqual(1, len(f.read().strip().splitlines()))

    # ── 3. el registro no guarda contenido ───────────────────────────────────────────
    def test_el_registro_no_guarda_lo_que_dijeron(self):
        self._panel()
        self._correr()
        log = os.path.join(self.state, "caja", "dossiers.jsonl")
        with open(log, encoding="utf-8") as f:
            crudo = f.read()
        fila = json.loads(crudo.strip().splitlines()[-1])
        self.assertEqual(["comite-medico", "verificacion"], fila["comites"])
        self.assertNotIn(FRASE_SECRETA, crudo)
        self.assertNotIn("compara las dos opciones", crudo, "ni el pedido de {{TITULAR}}")

    # ── 4. fail-open ─────────────────────────────────────────────────────────────────
    def test_entrada_rota_sale_0_callado(self):
        self.assertEqual((0, ""), self._correr(crudo="esto no es json"))

    def test_stop_hook_active_no_hace_nada(self):
        self._panel()
        datos = {"session_id": SID, "transcript_path": self.tp, "stop_hook_active": True}
        self.assertEqual((0, ""), self._correr(datos=datos))
        self.assertEqual([], self._dossiers())

    def test_bypass(self):
        self._panel()
        self.assertEqual((0, ""), self._correr(BTP_CAJA_OFF="1"))
        self.assertEqual([], self._dossiers())

    def test_transcript_inexistente(self):
        self._panel()
        datos = {"session_id": SID, "transcript_path": os.path.join(self.proy, "no-existe.jsonl")}
        self.assertEqual((0, ""), self._correr(datos=datos))

    # ── 5. enganchado ────────────────────────────────────────────────────────────────
    def test_esta_enganchado_en_stop(self):
        with open(os.path.join(RAIZ, ".claude", "settings.json"), encoding="utf-8") as f:
            ajustes = json.load(f)
        comandos = [h.get("command", "") for bloque in ajustes["hooks"].get("Stop", [])
                    for h in bloque.get("hooks", [])]
        self.assertTrue(any("cosecha_panel.py" in c for c in comandos))
        self.assertTrue(os.access(HOOK, os.X_OK), "el hook tiene que ser ejecutable")


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ COSECHA DEL PANEL EN VERDE (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
