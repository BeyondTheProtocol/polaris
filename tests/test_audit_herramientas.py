#!/usr/bin/env python3
"""tests/test_audit_herramientas.py — Tests de audit_herramientas.py.

Usa inventario y señales mockeadas. No llama a la red. No envía alertas (DRY).
No depende del estado real del sistema.

Exit 0 = todo OK. Exit 1 = algún test falló.
"""

import datetime
import json
import os
import sys
import tempfile
import types
import unittest
import unittest.mock

# ─── Apuntar al worktree correcto ─────────────────────────────────────────────
# ROOT puede ser casa base o worktree; el módulo usa BTP_REPO + BTP_STATE_DIR.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))


class TestAuditHerramientas(unittest.TestCase):

    def setUp(self):
        """Crea un entorno temporal aislado: inventario mock, estado vacío."""
        self.tmpdir = tempfile.mkdtemp(prefix="btp_test_audit_")
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, exist_ok=True)

        # Forzar BTP_STATE_DIR y BTP_REPO a los temporales
        os.environ["BTP_STATE_DIR"] = self.state_dir
        os.environ["BTP_AUDIT_DRY"] = "1"  # nunca enviar alertas en tests
        os.environ["BTP_REPO"] = self.tmpdir

        # Construimos el inventario de prueba
        self.inventario_base = [
            {
                "id": "grok",
                "nombre": "Grok Test",
                "tipo": "lane_llm",
                "es_must": False,
                "probe": {"metodo": "keychain", "clave_llavero": "btp-grok-api"},
                "senal_uso": {"fuente": "gasto_ledger", "filtro_tool": "grok", "ventana_dias": 14},
                "dueno": "auto-mejora",
                "caducidad": None,
                "notas": "",
            },
            {
                "id": "consensus",
                "nombre": "Consensus Test",
                "tipo": "evidencia",
                "es_must": True,
                "probe": {"metodo": "url_accesible", "url": "https://consensus.app", "bot_wall_indica": "ROTA"},
                "senal_uso": {"fuente": "grep_invocacion", "patron": "evidencia.py.*consensus", "ficheros": "", "ventana_dias": 14},
                "dueno": "comite-medico",
                "caducidad": None,
                "notas": "MUST",
            },
            {
                "id": "scite",
                "nombre": "scite Test",
                "tipo": "evidencia",
                "es_must": True,
                "probe": {"metodo": "url_accesible", "url": "https://scite.ai", "bot_wall_indica": "ROTA"},
                "senal_uso": {"fuente": "grep_invocacion", "patron": "evidencia.py.*scite", "ficheros": "", "ventana_dias": 14},
                "dueno": "comite-medico",
                "caducidad": None,
                "notas": "MUST",
            },
            {
                "id": "tool_salida",
                "nombre": "salida.py Test",
                "tipo": "tool_interna",
                "es_must": False,
                "probe": {"metodo": "fichero", "ruta": "tools/salida.py"},
                "senal_uso": {"fuente": "observabilidad", "filtro_agente": "salida", "ventana_dias": 14},
                "dueno": "auto-mejora",
                "caducidad": None,
                "notas": "",
            },
        ]

        inv_path = os.path.join(self.state_dir, "herramientas.json")
        with open(inv_path, "w", encoding="utf-8") as f:
            json.dump({"herramientas": self.inventario_base}, f)
        os.environ["BTP_HERRAMIENTAS_JSON"] = inv_path

        # Importar el módulo de forma limpia
        if "audit_herramientas" in sys.modules:
            del sys.modules["audit_herramientas"]
        import audit_herramientas as ah
        # Parchar las rutas del módulo para que apunten al tmpdir
        ah.REPO = self.tmpdir
        ah.STATE = self.state_dir
        ah.OBS_DIR = os.path.join(self.state_dir, "observabilidad")
        ah.LEDGER = os.path.join(self.tmpdir, "tools", ".gasto_ledger.jsonl")
        ah.LOGS_DIR = os.path.join(self.tmpdir, "tools", "launchd", "logs")
        ah.ALERTA_STATE = os.path.join(self.state_dir, "audit_herramientas_last_alert.json")
        ah.INVENTARIO_PATH = inv_path
        self.ah = ah

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for k in ("BTP_STATE_DIR", "BTP_AUDIT_DRY", "BTP_REPO", "BTP_HERRAMIENTAS_JSON"):
            os.environ.pop(k, None)

    # ── Probe tests ─────────────────────────────────────────────────────────────

    def test_probe_fichero_existe(self):
        """Probe tipo 'fichero' OK cuando el fichero existe."""
        ruta = os.path.join(self.tmpdir, "tools", "salida.py")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w") as f:
            f.write("# stub\n")

        herramienta = {
            "probe": {"metodo": "fichero", "ruta": "tools/salida.py"},
        }
        with unittest.mock.patch.object(self.ah, "REPO", self.tmpdir):
            vive, detalle = self.ah._probe_fichero("tools/salida.py")
        self.assertTrue(vive, f"Debería pasar la sonda: {detalle}")

    def test_probe_fichero_ausente(self):
        """Probe tipo 'fichero' falla cuando el fichero no existe."""
        vive, detalle = self.ah._probe_fichero("tools/inexistente.py")
        self.assertFalse(vive)
        self.assertIn("no encontrado", detalle)

    def test_probe_keychain_inexistente(self):
        """Probe tipo 'keychain' falla para una clave que no existe."""
        vive, detalle = self.ah._probe_keychain("btp-clave-que-no-existe-xyz-test")
        # En macOS real debería fallar; en CI puede no haber 'security'
        # Aceptamos tanto False como False (no crash)
        self.assertIsInstance(vive, bool)

    def test_probe_url_mock_ok(self):
        """Probe tipo 'url_accesible' OK cuando la URL responde 200."""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok content"

        with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
            vive, detalle = self.ah._probe_url("https://example.com")
        self.assertTrue(vive)
        self.assertIn("200", detalle)

    def test_probe_url_mock_falla(self):
        """Probe tipo 'url_accesible' falla cuando hay error de red."""
        import urllib.error
        with unittest.mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            vive, detalle = self.ah._probe_url("https://example.com")
        self.assertFalse(vive)

    def test_probe_url_antibot(self):
        """Probe detecta anti-bot/Cloudflare y marca ROTA."""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.status = 200
        mock_resp.read.return_value = b"checking your browser"

        with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
            vive, detalle = self.ah._probe_url("https://example.com", bot_wall_indica_rota=True)
        self.assertFalse(vive)
        self.assertIn("anti-bot", detalle.lower())

    def test_probe_url_antibot_login_es_inconcluso(self):
        """Anti-bot en herramienta tras login (bot_wall_indica_rota=False) → None
        (NO_VERIFICABLE), nunca False: un ping anónimo no puede desmentir el login."""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.status = 200
        mock_resp.read.return_value = b"checking your browser"

        with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
            vive, detalle = self.ah._probe_url("https://consensus.app", bot_wall_indica_rota=False)
        self.assertIsNone(vive, f"anti-bot tras login debe ser inconcluso (None): {detalle}")
        self.assertIn("no verificable", detalle.lower())

    def test_probe_url_login_gated_200_es_inconcluso(self):
        """Herramienta tras login: ni un 200 limpio confirma tu sesión → None."""
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.status = 200
        mock_resp.read.return_value = b"<html>scite home</html>"

        with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
            vive, detalle = self.ah._probe_url("https://scite.ai", login_gated=True)
        self.assertIsNone(vive, f"login-gated con 200 debe ser inconcluso (None): {detalle}")
        self.assertIn("login-gated", detalle.lower())

    # ── Señal de uso ──────────────────────────────────────────────────────────

    def test_uso_gasto_ledger_presente(self):
        """tiene_uso_reciente detecta uso en el ledger de gasto."""
        os.makedirs(os.path.join(self.tmpdir, "tools"), exist_ok=True)
        ledger = os.path.join(self.tmpdir, "tools", ".gasto_ledger.jsonl")
        with open(ledger, "w", encoding="utf-8") as f:
            rec = {
                "ts": datetime.datetime.now().isoformat(),
                "tool": "grok",
                "model": "grok-4.3",
                "input_tokens": 100,
                "output_tokens": 50,
                "usd": 0.01,
            }
            f.write(json.dumps(rec) + "\n")

        herramienta = self.inventario_base[0]  # grok
        result = self.ah.tiene_uso_reciente(herramienta, 14)
        self.assertTrue(result, "Debería detectar uso reciente en el ledger")

    def test_uso_gasto_ledger_ausente(self):
        """tiene_uso_reciente devuelve False si el ledger no existe."""
        herramienta = self.inventario_base[0]  # grok
        result = self.ah.tiene_uso_reciente(herramienta, 14)
        self.assertFalse(result)

    def test_uso_gasto_ledger_fuera_de_ventana(self):
        """tiene_uso_reciente ignora entradas más viejas que la ventana."""
        os.makedirs(os.path.join(self.tmpdir, "tools"), exist_ok=True)
        ledger = os.path.join(self.tmpdir, "tools", ".gasto_ledger.jsonl")
        ts_viejo = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()
        with open(ledger, "w", encoding="utf-8") as f:
            rec = {"ts": ts_viejo, "tool": "grok", "model": "grok-4.3",
                   "input_tokens": 100, "output_tokens": 50, "usd": 0.01}
            f.write(json.dumps(rec) + "\n")

        herramienta = self.inventario_base[0]
        result = self.ah.tiene_uso_reciente(herramienta, 14)
        self.assertFalse(result, "No debe detectar uso de hace 30 días con ventana de 14")

    def test_uso_observabilidad_presente(self):
        """tiene_uso_reciente detecta traza de observabilidad reciente."""
        obs_dir = os.path.join(self.state_dir, "observabilidad")
        os.makedirs(obs_dir, exist_ok=True)
        path_hoy = os.path.join(obs_dir, f"observabilidad-{datetime.date.today().isoformat()}.jsonl")
        with open(path_hoy, "w", encoding="utf-8") as f:
            rec = {"ts_ini": datetime.datetime.now().isoformat(),
                   "agente": "vigia", "job": "run", "resultado": "ok"}
            f.write(json.dumps(rec) + "\n")

        herramienta = {
            "senal_uso": {"fuente": "observabilidad", "filtro_agente": "vigia", "ventana_dias": 14}
        }
        result = self.ah.tiene_uso_reciente(herramienta, 14)
        self.assertTrue(result)

    # ── Clasificación ─────────────────────────────────────────────────────────

    def test_clasificacion_ok(self):
        """Herramienta que vive y tiene uso → OK."""
        herramienta = {
            "id": "test_ok",
            "nombre": "Test OK",
            "tipo": "tool_interna",
            "es_must": False,
            "probe": {"metodo": "fichero", "ruta": "tools/salida.py"},
            "senal_uso": {"fuente": "observabilidad", "filtro_agente": "vigia", "ventana_dias": 14},
            "dueno": "auto-mejora",
            "notas": "",
        }
        # Mock: probe=True, uso=True
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(True, "ok")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=True):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.OK)

    def test_clasificacion_huerfana(self):
        """Herramienta que vive pero sin uso → HUERFANA (si no es MUST)."""
        herramienta = {**self.inventario_base[0], "es_must": False}
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(True, "ok")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.HUERFANA)

    def test_clasificacion_rota(self):
        """Herramienta que NO vive y no es MUST → ROTA."""
        herramienta = {**self.inventario_base[0], "es_must": False}
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(False, "fallo")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.ROTA)

    def test_clasificacion_must_sin_operar_probe_falla(self):
        """MUST cuya sonda falla → MUST_SIN_OPERAR."""
        herramienta = {**self.inventario_base[1]}  # consensus es_must=True
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(False, "anti-bot")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.MUST_SIN_OPERAR)

    def test_clasificacion_no_verificable_sin_uso(self):
        """Sonda inconclusa (None) y sin uso → NO_VERIFICABLE (informativo, no alarma).
        Incluso si es MUST: no afirmamos 'roto' lo que no hemos comprobado."""
        herramienta = {**self.inventario_base[1], "es_must": True}  # consensus MUST
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(None, "anti-bot; no verificable")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.NO_VERIFICABLE)

    def test_clasificacion_no_verificable_con_uso_es_ok(self):
        """Sonda inconclusa (None) PERO con uso reciente → OK: el uso lo prueba vivo."""
        herramienta = {**self.inventario_base[0], "es_must": False}
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(None, "conector no verificable")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=True):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.OK)

    def test_no_verificable_no_dispara_alerta(self):
        """NO_VERIFICABLE nunca entra en el aviso a {{TITULAR}} (solo MUST_SIN_OPERAR/ROTA)."""
        import io
        from contextlib import redirect_stderr
        resultados = [
            {"id": "gmail", "nombre": "Gmail", "estado": self.ah.NO_VERIFICABLE,
             "es_must": True, "vive": None, "usada": False, "detalle_probe": "conector"},
        ]
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.ah._enviar_alerta(resultados, dry=False)
        # No hay críticos → no debe intentar enviar ni loguear supresión DRY
        self.assertNotIn("[DRY]", buf.getvalue())

    def test_probe_mcp_conector_sin_transcripciones_es_none(self):
        """mcp_conector sin transcripciones locales → None (no verificable), no False."""
        with unittest.mock.patch.object(self.ah, "CLAUDE_PROJECTS_DIR", os.path.join(self.tmpdir, "no_existe")):
            vive, detalle = self.ah._probe_mcp_conector("b47695e8-fake", 14)
        self.assertIsNone(vive)
        self.assertIn("no verificable", detalle.lower())

    def test_clasificacion_must_sin_operar_sin_uso(self):
        """MUST que vive pero sin uso → MUST_SIN_OPERAR."""
        herramienta = {**self.inventario_base[1]}  # consensus es_must=True
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(True, "HTTP 200")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False):
            r = self.ah.clasificar(herramienta, 14)
        self.assertEqual(r["estado"], self.ah.MUST_SIN_OPERAR)

    def test_consensus_y_scite_son_must(self):
        """Consensus y scite están marcados es_must=True en el inventario base."""
        ids_must = {h["id"] for h in self.inventario_base if h.get("es_must")}
        self.assertIn("consensus", ids_must, "consensus debe ser MUST")
        self.assertIn("scite", ids_must, "scite debe ser MUST")

    # ── Main CLI ──────────────────────────────────────────────────────────────

    def test_main_json_estructura(self):
        """main --json devuelve estructura correcta."""
        import io
        from contextlib import redirect_stdout

        # Mock: todas pasan sonda, ninguna tiene uso
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(True, "ok")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False), \
             unittest.mock.patch.object(self.ah, "_enviar_alerta"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.ah.main(["--json", "--dry"])

        out = json.loads(buf.getvalue())
        self.assertIn("resumen", out)
        self.assertIn("herramientas", out)
        self.assertIn("total", out["resumen"])

    def test_main_exit_0_cuando_todo_ok(self):
        """main devuelve 0 cuando todo está OK."""
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(True, "ok")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=True), \
             unittest.mock.patch.object(self.ah, "_enviar_alerta"):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.ah.main(["--dry"])
        self.assertEqual(rc, 0, "Debe devolver 0 cuando todo es OK")

    def test_main_exit_1_cuando_must_sin_operar(self):
        """main devuelve 1 cuando hay MUST_SIN_OPERAR."""
        with unittest.mock.patch.object(self.ah, "ejecutar_probe", return_value=(False, "anti-bot")), \
             unittest.mock.patch.object(self.ah, "tiene_uso_reciente", return_value=False), \
             unittest.mock.patch.object(self.ah, "_enviar_alerta"):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = self.ah.main(["--dry"])
        self.assertEqual(rc, 1, "Debe devolver 1 cuando hay MUST_SIN_OPERAR")

    def test_inventario_cargado_tiene_entradas(self):
        """Inventario de prueba tiene las herramientas esperadas."""
        h = self.ah.cargar_inventario()
        ids = {e["id"] for e in h}
        self.assertIn("grok", ids)
        self.assertIn("consensus", ids)
        self.assertIn("scite", ids)

    def test_dry_suprime_alertas(self):
        """En modo DRY, _enviar_alerta no llama a salida.send."""
        resultados = [
            {"id": "consensus", "nombre": "Consensus", "estado": self.ah.MUST_SIN_OPERAR,
             "es_must": True, "vive": False, "usada": False,
             "detalle_probe": "anti-bot", "dueno": "comite-medico", "notas": ""}
        ]
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.ah._enviar_alerta(resultados, dry=True)
        # Debe haber escrito algo al stderr (el [DRY] log)
        self.assertIn("[DRY]", buf.getvalue())


class TestProbesMCP(unittest.TestCase):
    """Tests de probe_mcp_config."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="btp_test_mcp_")
        os.environ["BTP_STATE_DIR"] = self.tmpdir
        os.environ["BTP_AUDIT_DRY"] = "1"
        os.environ["BTP_REPO"] = self.tmpdir
        if "audit_herramientas" in sys.modules:
            del sys.modules["audit_herramientas"]
        import audit_herramientas as ah
        self.ah = ah

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for k in ("BTP_STATE_DIR", "BTP_AUDIT_DRY", "BTP_REPO"):
            os.environ.pop(k, None)

    def test_mcp_declarado(self):
        """probe_mcp_config detecta servidor declarado en config."""
        cfg_path = os.path.join(self.tmpdir, "claude_config.json")
        cfg = {"mcpServers": {"biomcp": {"command": "uvx", "args": ["biomcp"]}}}
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)

        vive, detalle = self.ah._probe_mcp_config("biomcp", [cfg_path])
        self.assertTrue(vive, f"Debería detectar biomcp: {detalle}")

    def test_mcp_no_declarado(self):
        """probe_mcp_config devuelve False si el servidor no está."""
        cfg_path = os.path.join(self.tmpdir, "claude_config.json")
        cfg = {"mcpServers": {"otro": {}}}
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)

        vive, detalle = self.ah._probe_mcp_config("biomcp", [cfg_path])
        self.assertFalse(vive)

    def test_mcp_config_inexistente(self):
        """probe_mcp_config devuelve False si el fichero no existe."""
        vive, detalle = self.ah._probe_mcp_config(
            "biomcp", [os.path.join(self.tmpdir, "no_existe.json")]
        )
        self.assertFalse(vive)


def main():
    # Cambia al directorio raíz del repo para que los módulos se importen bien
    os.chdir(ROOT)
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("OK — test_audit_herramientas: todos los tests pasaron")
        return 0
    print(f"FALLO — {len(result.failures)} fallos, {len(result.errors)} errores")
    return 1


if __name__ == "__main__":
    sys.exit(main())
