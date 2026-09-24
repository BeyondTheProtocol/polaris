#!/usr/bin/env python3
"""test_inventario_viejas_modelos.py — issues #2 (`--viejas N`) y #5 (`--modelos`).

Casos del PR #23 de j7j7j7 (24-sep-2026), pasados a unittest como el resto de la batería, más
lo que faltaba: el repo git temporal con fechas sintéticas que pedía el issue #2, un modelo de
fuera de la lista de familias que el PR usaba de prefiltro, `:latest` citado sin la etiqueta, y
que un `git grep` roto se diga en vez de dar todos los modelos por no usados.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import inventario  # noqa: E402

OLLAMA = """NAME              ID              SIZE      MODIFIED
qwen2.5:7b       aaa             4 GB      2 days ago
qwen2.5:14b      bbb             9 GB      1 week ago
nomic-embed-text:latest ccc      274 MB    3 weeks ago
"""


class Viejas(unittest.TestCase):
    def test_filtra_y_ordena_de_mas_vieja_a_mas_nueva(self):
        filas = [{"pieza": "reciente.py", "estado": "viva", "ultimo_commit": "2026-09-20"},
                 {"pieza": "vieja.py", "estado": "huerfana", "ultimo_commit": "2026-06-01"},
                 {"pieza": "media.py", "estado": "entrada", "ultimo_commit": "2026-07-15"}]
        r = inventario.herramientas_viejas(60, filas=filas, hoy=date(2026, 9, 24))
        self.assertEqual([f["pieza"] for f in r], ["vieja.py", "media.py"])
        self.assertEqual(r[0]["estado"], "huerfana")
        self.assertGreater(r[0]["dias_sin_tocar"], r[1]["dias_sin_tocar"])

    def test_fecha_desconocida_no_cuenta_y_n_negativo_falla(self):
        filas = [{"pieza": "sin_fecha.py", "estado": "viva", "ultimo_commit": "?"}]
        self.assertEqual(inventario.herramientas_viejas(0, filas=filas, hoy=date(2026, 9, 24)), [])
        with self.assertRaises(ValueError):
            inventario.herramientas_viejas(-1, filas=[])

    def test_en_un_repo_git_de_verdad_con_fechas_sinteticas(self):
        """Lo que pedía el issue #2: un repo git temporal, no filas escritas a mano."""
        repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo, "tools"))
        base = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@localhost",
                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@localhost")
        subprocess.run(["git", "init", "-q"], cwd=repo, env=base, check=True)
        for nombre, fecha in (("vieja.py", "2026-01-10T12:00:00"),
                              ("media.py", "2026-06-01T12:00:00"),
                              ("nueva.py", "2026-09-23T12:00:00")):
            with open(os.path.join(repo, "tools", nombre), "w") as f:
                f.write("# %s\n" % nombre)
            env = dict(base, GIT_AUTHOR_DATE=fecha, GIT_COMMITTER_DATE=fecha)
            subprocess.run(["git", "add", "tools/" + nombre], cwd=repo, env=env, check=True)
            subprocess.run(["git", "commit", "-q", "-m", nombre], cwd=repo, env=env, check=True)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "inventario.py"), "--viejas", "60"],
                           env=dict(base, BTP_REPO=repo), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        lineas = [l for l in r.stdout.splitlines() if ".py" in l]
        self.assertEqual([l.split()[0] for l in lineas], ["vieja.py", "media.py"],
                         "de más vieja a más nueva, y la de ayer no sale")
        self.assertIn("no sugiere retirar", r.stdout, "clasificar no es borrar (issue #2)")


class Modelos(unittest.TestCase):
    def test_parsea_ollama_list(self):
        self.assertEqual(inventario._modelos_ollama(OLLAMA),
                         ["qwen2.5:7b", "qwen2.5:14b", "nomic-embed-text:latest"])

    def test_compara_el_nombre_completo(self):
        codigo = 'MODELO = "qwen2.5:7b"\nEMB = "nomic-embed-text"\n'
        self.assertEqual(inventario.modelos_no_usados(OLLAMA, codigo), ["qwen2.5:14b"])

    def test_un_modelo_de_otra_familia_citado_cuenta_como_usado(self):
        """El PR prefiltraba con llama|mistral|contacto|phi|qwen|deepseek|codellama: esto salía
        «no usado» aunque el código lo citara."""
        self.assertNotIn("nomic-embed-text:latest",
                         inventario.modelos_no_usados(OLLAMA, "ollama pull nomic-embed-text"))

    def test_de_verdad_contra_git_grep(self):
        repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo, "tools"))
        with open(os.path.join(repo, "tools", "x.py"), "w") as f:
            f.write('M = "qwen2.5:7b"\nE = "nomic-embed-text"\n')
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@l",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@l")
        for orden in (["git", "init", "-q"], ["git", "add", "tools/x.py"],
                      ["git", "commit", "-q", "-m", "x"]):
            subprocess.run(orden, cwd=repo, env=env, check=True)
        with patch.object(inventario, "REPO", repo):
            self.assertEqual(inventario.modelos_no_usados(OLLAMA), ["qwen2.5:14b"])

    def test_sin_ollama_falla_explicito(self):
        with patch.object(inventario.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(RuntimeError) as cm:
                inventario.modelos_no_usados()
        self.assertIn("ollama no está instalado", str(cm.exception))

    def test_un_git_grep_roto_se_dice(self):
        """Fail-closed: antes, un fallo de git daba código vacío y TODOS salían «no usados»."""
        roto = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal: not a git repo")
        with patch.object(inventario.subprocess, "run", return_value=roto):
            with self.assertRaises(RuntimeError) as cm:
                inventario.modelos_no_usados(OLLAMA)
        self.assertIn("git grep falló", str(cm.exception))

    def test_ninguna_coincidencia_no_es_un_error(self):
        nada = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        with patch.object(inventario.subprocess, "run", return_value=nada):
            self.assertEqual(len(inventario.modelos_no_usados(OLLAMA)), 3)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print("✅ INVENTARIO --viejas/--modelos EN VERDE" if res.wasSuccessful() else "❌ revisar fallos")
    sys.exit(0 if res.wasSuccessful() else 1)
