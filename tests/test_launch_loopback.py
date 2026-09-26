#!/usr/bin/env python3
"""test_launch_loopback.py — un launch.json no puede abrir un preview a toda la red.

Verifica `.claude/hooks/launch_loopback_guard.py` ejecutándolo como lo ejecuta Claude Code (stdin
JSON, exit 2 = deny). El positivo es el launch.json REAL que el 26-sep-26 dejó `*:3217` abierto
(`PORT=3217 node .output/server/index.mjs`, sin HOST). Idea de {{CONTACTO}}
(https://contacto), con su agente KAI, revisión del 25-sep-2026 (S14: nada escucha fuera de
loopback).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "launch_loopback_guard.py")


def cfg(*configs):
    return json.dumps({"version": "0.0.1", "configurations": list(configs)}, indent=2)


REAL = {"name": "web-ruta-animada", "runtimeExecutable": "env",
        "runtimeArgs": ["PORT=3217", "node", "/x/.output/server/index.mjs"], "port": 3217}


# Medido con lsof el 26-sep-26 en la web real (Nuxt 4.4.4): ni vite.server.hmr.host ni el hook
# vite:extendConfig atan el HMR del servidor (siguió en *:24678); el plugin 'post' sí.
NUXT_SIN_HOOK = "export default defineNuxtConfig({ vite: { server: { hmr: { host: '127.0.0.1' } } } })\n"
NUXT_EXTEND = ("export default defineNuxtConfig({ hooks: { 'vite:extendConfig'(c) { c.server ||= {};"
               " c.server.hmr = { ...c.server.hmr, host: '127.0.0.1' } } } })\n")
NUXT_CON_HOOK = ("export default defineNuxtConfig({ vite: { plugins: [{ name: 'hmr-solo-en-este-mac',"
                 " enforce: 'post', config(config) { const hmr = config.server?.hmr;"
                 " if (hmr && typeof hmr === 'object' && !hmr.server) hmr.host ??= '127.0.0.1' } }] } })\n")


def correr(tool, entrada, env_extra=None):
    env = dict(os.environ)
    env.pop("BTP_LOOPBACK_OK", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": tool, "tool_input": entrada}),
                       capture_output=True, text=True, env=env, timeout=10)
    return r.returncode, r.stderr


class Write(unittest.TestCase):
    RUTA = "/tmp/algo/.claude/launch.json"

    def w(self, texto, **kw):
        return correr("Write", {"file_path": self.RUTA, "content": texto}, **kw)[0]

    def test_el_caso_real_se_deniega(self):
        rc, err = correr("Write", {"file_path": self.RUTA, "content": cfg(REAL)})
        self.assertEqual(rc, 2)
        self.assertIn("HOST=127.0.0.1", err)
        self.assertIn("web-ruta-animada", err)

    def test_con_host_loopback_pasa(self):
        for args in (["HOST=127.0.0.1", "PORT=3217", "node", "x.mjs"],
                     ["NITRO_HOST=localhost", "node", "x.mjs"],
                     ["-m", "http.server", "8000", "--bind", "127.0.0.1"],
                     ["run", "dev", "--", "-H", "127.0.0.1"],
                     ["run", "dev", "--", "--host=localhost"]):
            self.assertEqual(self.w(cfg({"name": "a", "runtimeExecutable": "env",
                                         "runtimeArgs": args, "port": 1})), 0, args)

    def test_host_en_env_pasa(self):
        c = dict(REAL, env={"HOST": "127.0.0.1"})
        self.assertEqual(self.w(cfg(c)), 0)

    def test_host_abierto_se_deniega(self):
        for args in (["HOST=0.0.0.0", "node", "x.mjs"], ["vite", "--host"],
                     ["-m", "http.server", "8000"], ["next", "dev", "-H", "0.0.0.0"]):
            self.assertEqual(self.w(cfg({"name": "a", "runtimeExecutable": "npx",
                                         "runtimeArgs": args, "port": 1})), 2, args)

    def test_vite_por_defecto_ya_es_localhost(self):
        self.assertEqual(self.w(cfg({"name": "v", "runtimeExecutable": "npx",
                                     "runtimeArgs": ["vite"], "port": 5173})), 0)

    def test_pnpm_dev_de_nuxt_exige_hmr_atado(self):
        """El launch.json real de la web: `pnpm dev` → `nuxt dev`. La app va a localhost, pero el
        HMR del servidor se abre en *:24678 salvo que nuxt.config lo ate con un plugin Vite 'post'
        (medido con lsof el 26-sep-26 en la web real, Nuxt 4.4.4). Sin él → deny; con él → pasa."""
        raiz = tempfile.mkdtemp()
        os.makedirs(os.path.join(raiz, ".claude"))
        with open(os.path.join(raiz, "package.json"), "w") as fh:
            json.dump({"scripts": {"dev": "pnpm update-fundraiser --force && nuxt dev"}}, fh)
        c = cfg({"name": "helptitular-dev", "runtimeExecutable": "pnpm",
                 "runtimeArgs": ["dev", "--port", "3005"], "port": 3005})
        ruta = os.path.join(raiz, ".claude", "launch.json")
        with open(os.path.join(raiz, "nuxt.config.ts"), "w") as fh:
            fh.write(NUXT_SIN_HOOK)
        rc, err = correr("Write", {"file_path": ruta, "content": c})
        self.assertEqual(rc, 2)
        self.assertIn("hmr-solo-en-este-mac", err)
        with open(os.path.join(raiz, "nuxt.config.ts"), "w") as fh:
            fh.write(NUXT_CON_HOOK)
        self.assertEqual(correr("Write", {"file_path": ruta, "content": c})[0], 0)
        # El mismo `pnpm dev` pero con `next dev` detrás sí se abre a la red.
        with open(os.path.join(raiz, "package.json"), "w") as fh:
            json.dump({"scripts": {"dev": "next dev"}}, fh)
        self.assertEqual(correr("Write", {"file_path": ruta, "content": c})[0], 2)

    def test_solo_url_no_arranca_nada(self):
        self.assertEqual(self.w(cfg({"name": "u", "url": "http://localhost:3000", "port": 3000})), 0)

    def test_otro_fichero_no_se_mira(self):
        rc, _ = correr("Write", {"file_path": "/tmp/launch.json", "content": cfg(REAL)})
        self.assertEqual(rc, 0)

    def test_escotilla(self):
        self.assertEqual(self.w(cfg(REAL), env_extra={"BTP_LOOPBACK_OK": "1"}), 0)

    def test_fail_open_con_json_roto(self):
        self.assertEqual(self.w("{ esto no es json"), 0)


class Edit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, ".claude"))
        self.ruta = os.path.join(self.dir, ".claude", "launch.json")
        with open(self.ruta, "w") as fh:
            fh.write(cfg(dict(REAL, runtimeArgs=["HOST=127.0.0.1"] + REAL["runtimeArgs"])))

    def test_quitar_el_host_con_edit_se_deniega(self):
        rc, _ = correr("Edit", {"file_path": self.ruta, "old_string": '"HOST=127.0.0.1",',
                                "new_string": ""})
        self.assertEqual(rc, 2)

    def test_cambiar_el_puerto_con_host_pasa(self):
        rc, _ = correr("Edit", {"file_path": self.ruta, "old_string": "3217", "new_string": "3218",
                                "replace_all": True})
        self.assertEqual(rc, 0)

    def test_multiedit(self):
        rc, _ = correr("MultiEdit", {"file_path": self.ruta, "edits": [
            {"old_string": '"HOST=127.0.0.1",', "new_string": ""}]})
        self.assertEqual(rc, 2)


def bash(comando, cwd, env_extra=None):
    env = dict(os.environ)
    env.pop("BTP_LOOPBACK_OK", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, HOOK], capture_output=True, text=True, env=env, timeout=10,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": comando},
                                         "cwd": cwd}))
    return r.returncode, r.stderr


class Bash(unittest.TestCase):
    """26-sep-26: los dos servidores que pusieron rojo test_puertos_loopback se lanzaron por Bash
    (nuxt dev con el HMR en *:24678; node servidor.js en *:4600), sin launch.json."""

    def setUp(self):
        self.nuxt = tempfile.mkdtemp()
        with open(os.path.join(self.nuxt, "package.json"), "w") as fh:
            json.dump({"scripts": {"dev": "nuxt dev", "test": "vitest"}}, fh)
        with open(os.path.join(self.nuxt, "nuxt.config.ts"), "w") as fh:
            fh.write(NUXT_SIN_HOOK)
        self.vacio = tempfile.mkdtemp()

    def test_el_caso_real_nuxt_dev_se_deniega(self):
        for cmd in ("pnpm dev --port 3217", "npm run dev", "npx nuxt dev --host 127.0.0.1",
                    "./node_modules/.bin/nuxt dev", "nohup pnpm dev > /tmp/x.log 2>&1 &"):
            rc, err = bash(cmd, self.nuxt)
            self.assertEqual(rc, 2, cmd)
            self.assertIn("hmr-solo-en-este-mac", err)

    def test_extendconfig_no_basta(self):
        """En la web real el hook vite:extendConfig no llega al HMR del servidor (siguió en
        *:24678): no puede contar como arreglo."""
        with open(os.path.join(self.nuxt, "nuxt.config.ts"), "w") as fh:
            fh.write(NUXT_EXTEND)
        self.assertEqual(bash("pnpm dev", self.nuxt)[0], 2)

    def test_la_config_real_de_la_web_pasa(self):
        """El nuxt.config de la rama fix/dev-hmr-loopback de la web (medido: HMR en 127.0.0.1)."""
        real = os.path.expanduser("~/projects/.mgc-staging/dev-hmr-loopback/nuxt.config.ts")
        if not os.path.exists(real):
            self.skipTest("no está la rama de la web en este Mac")
        with open(real, encoding="utf-8") as fh, \
                open(os.path.join(self.nuxt, "nuxt.config.ts"), "w") as out:
            out.write(fh.read())
        self.assertEqual(bash("pnpm dev --port 3217", self.nuxt)[0], 0)

    def test_cd_se_sigue(self):
        self.assertEqual(bash("cd %s && pnpm dev" % self.nuxt, "/")[0], 2)

    def test_nuxt_con_hook_pasa(self):
        with open(os.path.join(self.nuxt, "nuxt.config.ts"), "w") as fh:
            fh.write(NUXT_CON_HOOK)
        self.assertEqual(bash("pnpm dev --port 3217", self.nuxt)[0], 0)

    def test_sin_host_se_deniega(self):
        for cmd in ("python3 -m http.server 8000", "PORT=3217 node .output/server/index.mjs",
                    "next dev", "npx vite --host", "uvicorn app:app --host 0.0.0.0",
                    "echo hola; python3 -m http.server 8000 &", "next dev -H 0.0.0.0"):
            self.assertEqual(bash(cmd, self.vacio)[0], 2, cmd)

    def test_con_host_o_seguro_por_defecto_pasa(self):
        for cmd in ("python3 -m http.server 8000 --bind 127.0.0.1",
                    "HOST=127.0.0.1 PORT=3217 node .output/server/index.mjs",
                    "next dev -H 127.0.0.1", "npx vite", "vite build", "uvicorn app:app"):
            self.assertEqual(bash(cmd, self.vacio)[0], 0, cmd)

    def test_no_servidores_no_se_tocan(self):
        """Sin falsos positivos: comillas, grep, instalar, tests, git."""
        for cmd in ("git commit -m 'arreglo nuxt dev y vite --host'", "grep -rn vite src | head",
                    "npm install vite", "pnpm test", "ls -la && git status",
                    "node servidor.js 4600", "cat nuxt.config.ts"):
            self.assertEqual(bash(cmd, self.nuxt)[0], 0, cmd)

    def test_escotilla(self):
        self.assertEqual(bash("pnpm dev", self.nuxt, {"BTP_LOOPBACK_OK": "1"})[0], 0)

    def test_fail_open_con_comillas_rotas(self):
        self.assertEqual(bash("pnpm dev 'sin cerrar", self.nuxt)[0], 0)


class Registro(unittest.TestCase):
    def _matchers(self):
        with open(os.path.join(ROOT, ".claude", "settings.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return [b.get("matcher", "") for b in d["hooks"]["PreToolUse"]
                if any("launch_loopback_guard.py" in h["command"] for h in b["hooks"])]

    def test_enganchado_en_settings(self):
        self.assertTrue(any("Write" in m for m in self._matchers()))

    def test_enganchado_tambien_a_bash(self):
        self.assertTrue(any("Bash" in m.split("|") for m in self._matchers()))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ launch loopback: %d tests en verde" % r.testsRun if ok
          else "❌ launch loopback: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
