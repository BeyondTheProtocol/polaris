#!/usr/bin/env python3
"""Tests de la presencia por Claude Code (hook presencia_cc.py + healthcheck._procesar_presencia_cc).

Lo que se protege (plan del 11-sep-26, deuda `dead_man_ausencia`):
  · que un prompt TECLEADO por {{TITULAR}} en Claude Code cuente como presencia (si no, el dead-man
    baja el gasto al 30% mientras ella trabaja: 1241 detecciones);
  · que NADA automático cuente: el lazo, `claude -p` (aunque herede claude-desktop), avisos de
    subagentes, mensajes entre sesiones, tareas programadas (salen {"kind":"human"} y solo las
    delata el texto <scheduled-task …>, verificado con sonda);
  · que el hook sea fail-open para el prompt y no guarde nunca el texto;
  · canario: si el harness deja de escribir `origin` en los transcripts, se pone rojo en vez de
    volver en silencio al falso positivo de siempre.
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "presencia_cc.py")
_TMP = tempfile.mkdtemp(prefix="presencia_cc_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_TEST_BATTERY"] = "1"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as hc  # noqa: E402

MARCAS_LAZO = ("BTP_AGENT_DEPTH", "BTP_AGENT", "MURO_PROFILE")


def _utc(delta=timedelta(0)):
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _entrada(pid, texto="hola, sigue con lo de ayer", origin=None, **extra):
    d = {"type": "user", "promptId": pid, "isSidechain": False, "entrypoint": "claude-desktop",
         "origin": {"kind": "human"} if origin is None else origin, "timestamp": _utc(),
         "message": {"role": "user", "content": texto}}
    d.update(extra)
    if d["origin"] == "SIN":
        del d["origin"]
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_TMP)
        self.proyectos = os.path.join(self.dir, "projects")
        os.makedirs(os.path.join(self.proyectos, "p"))
        hc.HC = os.path.join(self.dir, "hc")
        os.makedirs(hc.HC)
        hc.LAST_SEEN = os.path.join(hc.HC, "last_seen.json")
        hc.DEGRADED = os.path.join(hc.HC, "degraded.flag")
        hc.PRESENCIA_PEND = os.path.join(hc.HC, "presencia_pendiente.jsonl")
        hc.PROYECTOS_CC = self.proyectos
        self.transcript = os.path.join(self.proyectos, "p", "s.jsonl")
        open(self.transcript, "w").close()

    def poner(self, *entradas, transcript=None):
        with open(transcript or self.transcript, "a", encoding="utf-8") as f:
            for e in entradas:
                f.write(json.dumps(e) + "\n")

    def encolar(self, pid, transcript=None, hace_h=0):
        with open(hc.PRESENCIA_PEND, "a", encoding="utf-8") as f:
            f.write(json.dumps({"prompt_id": pid, "session_id": "s",
                                "transcript_path": transcript or self.transcript,
                                "ts": time.time() - hace_h * 3600}) + "\n")

    def last_seen(self):
        try:
            return json.load(open(hc.LAST_SEEN, encoding="utf-8"))
        except FileNotFoundError:
            return None


class TestVeredicto(Base):
    def test_prompt_humano_marca_y_levanta_degradado(self):
        open(hc.DEGRADED, "w").close()
        self.poner(_entrada("p1"))
        self.encolar("p1")
        info = hc._procesar_presencia_cc()
        self.assertEqual(info["marcadas"], 1)
        self.assertEqual(self.last_seen()["fuente"], "claude-code")
        self.assertFalse(os.path.exists(hc.DEGRADED))
        self.assertFalse(os.path.exists(hc.PRESENCIA_PEND))

    def test_con_system_reminder_delante_sigue_siendo_humano(self):
        self.poner(_entrada("p1", "<system-reminder>\nworktree…\n</system-reminder>\nhaz esto"))
        self.encolar("p1")
        self.assertEqual(hc._procesar_presencia_cc()["marcadas"], 1)

    def test_nada_automatico_cuenta(self):
        casos = {
            "sin_origin_claude_p": _entrada("a", origin="SIN"),
            # forma real de un prompt de CronCreate / /loop (sonda del 11-sep-26): sin origin, isMeta
            "cron_de_sesion": _entrada("r", "SONDA: texto normal", origin="SIN", isMeta=True),
            "task_notification": _entrada("b", origin={"kind": "task-notification"}),
            "peer": _entrada("c", origin={"kind": "peer", "from": "x", "hostInjected": True}),
            "human_con_claves_extra": _entrada("d", origin={"kind": "human", "x": 1}),
            "kind_desconocido": _entrada("e", origin={"kind": "cron"}),
            "tarea_programada": _entrada("f", '<scheduled-task name="x" file="y">\nThis is an automated run'),
            "aviso_como_humano": _entrada("g", "<task-notification>\n<task-id>1</task-id>"),
            "ci_monitor": _entrada("h", "<ci-monitor-event>…"),
            "reminder_y_tarea": _entrada("i", "<system-reminder>r</system-reminder>\n<scheduled-task name=\"x\">"),
            "sidechain": _entrada("j", isSidechain=True),
            "entrypoint_sdk": _entrada("k", entrypoint="sdk-cli"),
            "sin_entrypoint": _entrada("l", entrypoint=None),
            "viejo": _entrada("m", timestamp=_utc(timedelta(hours=30))),
            "futuro": _entrada("n", timestamp=_utc(-timedelta(hours=2))),
            "sin_timestamp": _entrada("o", timestamp="basura"),
            "no_es_user": _entrada("q", type="assistant"),
        }
        for e in casos.values():
            self.poner(e)
            self.encolar(e["promptId"])
        info = hc._procesar_presencia_cc()
        self.assertEqual(info["marcadas"], 0, info)
        # la entrada `assistant` no es un prompt: no se encuentra y se reintenta hasta caducar
        self.assertEqual(sum(info["rechazos"].values()) + info["reintentos"], len(casos), info)
        self.assertIsNone(self.last_seen())

    def test_transcript_fuera_de_proyectos_no_cuenta(self):
        fuera = os.path.join(self.dir, "fuera.jsonl")
        self.poner(_entrada("p1"), transcript=fuera)
        self.encolar("p1", transcript=fuera)
        info = hc._procesar_presencia_cc()
        self.assertEqual(info["marcadas"], 0)
        self.assertIn("transcript_fuera_de_proyectos", info["rechazos"])

    def test_transcript_en_memory_o_subcarpeta_no_cuenta(self):
        for sub in (("p", "memory", "s.jsonl"), ("p", "sesion", "subagents", "s.jsonl")):
            ruta = os.path.join(self.proyectos, *sub)
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            self.poner(_entrada("m" + sub[1]), transcript=ruta)
            self.encolar("m" + sub[1], transcript=ruta)
        info = hc._procesar_presencia_cc()
        self.assertEqual(info["marcadas"], 0)
        self.assertEqual(info["rechazos"].get("transcript_fuera_de_proyectos"), 2)

    def test_symlink_que_sale_de_proyectos_no_cuenta(self):
        fuera = os.path.join(self.dir, "fuera.jsonl")
        self.poner(_entrada("p1"), transcript=fuera)
        enlace = os.path.join(self.proyectos, "p", "enlace.jsonl")
        os.symlink(fuera, enlace)
        self.encolar("p1", transcript=enlace)
        self.assertEqual(hc._procesar_presencia_cc()["marcadas"], 0)

    def test_aun_no_escrito_se_reintenta_y_caduca(self):
        self.encolar("p1")
        info = hc._procesar_presencia_cc()
        self.assertEqual(info["reintentos"], 1)
        self.assertTrue(os.path.exists(hc.PRESENCIA_PEND))
        self.poner(_entrada("p1"))
        self.assertEqual(hc._procesar_presencia_cc()["marcadas"], 1)
        self.encolar("nunca", hace_h=25)
        info = hc._procesar_presencia_cc()
        self.assertIn("no_aparece", info["rechazos"])
        self.assertFalse(os.path.exists(hc.PRESENCIA_PEND))

    def test_cola_rota_no_revienta(self):
        with open(hc.PRESENCIA_PEND, "w") as f:
            f.write("esto no es json\n{}\n")
        info = hc._procesar_presencia_cc()
        self.assertEqual(info["rechazos"].get("cola_ilegible"), 2)
        with open(self.transcript, "w") as f:
            f.write("{roto\n")
        self.encolar("p1", hace_h=25)
        hc._procesar_presencia_cc()   # no lanza

    def test_run_procesa_la_cola_antes_del_dead_man(self):
        src = open(os.path.join(ROOT, "tools", "healthcheck.py"), encoding="utf-8").read()
        run = src[src.index("def run():"):]
        self.assertLess(run.index("_procesar_presencia_cc()"), run.index("dias = _dias_sin_senal()"))


class TestMarkSeen(Base):
    def test_historial_por_fuente_y_ts_mas_reciente(self):
        hc.mark_seen("telegram", cuando=datetime(2026, 9, 10, 9, 0, 0))
        hc.mark_seen("claude-code", cuando=datetime(2026, 9, 11, 12, 0, 0))
        ls = self.last_seen()
        self.assertEqual((ls["ts"], ls["fuente"]), ("2026-09-11T12:00:00", "claude-code"))
        self.assertEqual(ls["fuentes"]["telegram"], "2026-09-10T09:00:00")
        # una señal más vieja de otra fuente no retrasa el reloj
        hc.mark_seen("telegram", cuando=datetime(2026, 9, 9, 9, 0, 0))
        ls = self.last_seen()
        self.assertEqual(ls["ts"], "2026-09-11T12:00:00")
        self.assertEqual(ls["fuentes"]["telegram"], "2026-09-10T09:00:00")

    def test_last_seen_antiguo_se_conserva(self):
        with open(hc.LAST_SEEN, "w") as f:
            json.dump({"ts": "2026-09-05T17:54:05", "fuente": "telegram"}, f)
        hc.mark_seen("claude-code", cuando=datetime(2026, 9, 11, 12, 0, 0))
        self.assertEqual(self.last_seen()["fuentes"]["telegram"], "2026-09-05T17:54:05")

    def test_dias_sin_senal_lee_el_ts_de_arriba(self):
        hc.mark_seen("claude-code")
        self.assertLess(hc._dias_sin_senal(), 0.01)


class TestHook(Base):
    def correr(self, payload, **env_extra):
        env = {k: v for k, v in os.environ.items() if k not in MARCAS_LAZO}
        env.update(BTP_STATE_DIR=self.dir, BTP_REPO=ROOT, **env_extra)
        return subprocess.run([sys.executable, HOOK], input=payload, env=env,
                              capture_output=True, text=True, timeout=20)

    def cola(self):
        f = os.path.join(self.dir, "healthcheck", "presencia_pendiente.jsonl")
        return [json.loads(l) for l in open(f)] if os.path.exists(f) else []

    def payload(self, prompt="hola, secreto clínico"):
        return json.dumps({"session_id": "s", "transcript_path": self.transcript, "prompt_id": "p1",
                           "prompt": prompt, "hook_event_name": "UserPromptSubmit"})

    def test_apunta_sin_guardar_el_prompt(self):
        r = self.correr(self.payload())
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        cola = self.cola()
        self.assertEqual(len(cola), 1)
        self.assertEqual(set(cola[0]), {"prompt_id", "session_id", "transcript_path", "ts"})
        self.assertNotIn("secreto", json.dumps(cola))

    def test_el_lazo_no_apunta(self):
        for marca in MARCAS_LAZO:
            self.assertEqual(self.correr(self.payload(), **{marca: "1"}).returncode, 0)
        self.assertEqual(self.cola(), [])

    def test_automatismos_no_apuntan(self):
        for p in ("<task-notification>\n…", '<scheduled-task name="x">', "  <ci-monitor-event>"):
            self.correr(self.payload(p))
        self.assertEqual(self.cola(), [])

    def test_fail_open(self):
        for basura in ("", "no json", "[]", json.dumps({"prompt": "x"})):
            r = self.correr(basura)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.cola(), [])

    def test_enganchado_en_user_prompt_submit(self):
        cfg = json.load(open(os.path.join(ROOT, ".claude", "settings.json"), encoding="utf-8"))
        cmds = [h.get("command", "") for b in cfg["hooks"]["UserPromptSubmit"] for h in b["hooks"]]
        self.assertTrue(any("presencia_cc.py" in c for c in cmds))


class TestCanario(unittest.TestCase):
    """Si el harness deja de escribir `origin`, la presencia por Claude Code muere en silencio
    (fail-closed) y vuelve el falso positivo. Esto lo convierte en rojo."""

    def test_los_transcripts_recientes_siguen_trayendo_origin(self):
        raiz = os.path.expanduser("~/.claude/projects")
        limite = time.time() - 14 * 86400
        fs = [f for f in glob.glob(os.path.join(raiz, "*", "*.jsonl")) if os.path.getmtime(f) > limite]
        con_desktop = con_origin = 0
        for f in sorted(fs, key=os.path.getmtime)[-60:]:
            try:
                for linea in open(f, encoding="utf-8"):
                    if '"type":"user"' not in linea.replace(" ", "") or "claude-desktop" not in linea:
                        continue
                    d = json.loads(linea)
                    if d.get("type") == "user" and d.get("entrypoint") == "claude-desktop" \
                            and d.get("promptId"):
                        con_desktop += 1
                        con_origin += "origin" in d
            except Exception:
                continue
        if con_desktop == 0:
            self.skipTest("sin sesiones de escritorio en 14 días: nada que vigilar")
        self.assertGreater(con_origin, 0, "el harness ya no escribe `origin` en los transcripts: "
                           "la presencia por Claude Code ha dejado de funcionar")


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if res.wasSuccessful():
        print("✅ PRESENCIA POR CLAUDE CODE EN VERDE (%d tests)" % res.testsRun)
        sys.exit(0)
    print("❌ PRESENCIA POR CLAUDE CODE EN ROJO: %d fallos, %d errores"
          % (len(res.failures), len(res.errors)))
    sys.exit(1)
