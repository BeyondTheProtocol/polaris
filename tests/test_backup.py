#!/usr/bin/env python3
"""test_backup.py — lo que protege TODO lo demás no tenía ni un test.

`tools/backup.sh` es la única copia de la fuente de verdad (incluido lo clínico) y de la memoria
durable. Ha fallado dos veces en silencio y las dos por lo mismo, una ruta que no era la de esta
máquina:

  · 12→25-jul-2026: rutas hardcodeadas a `/Users/titular` tras la migración al Air. **13 días
    sin copia** y nadie se enteró, porque el script sale 0 cuando el disco no está montado (eso es
    deliberado: el USB no siempre está enchufado) y el fallo se veía igual que un día sin disco.
  · 31-jul-2026: el glob solo cogía los proyectos con «claudecode» en el nombre, así que
    `~/.claude/projects/-Users-polaris/memory` se quedaba fuera. El log decía «fuentes: …memory»
    y parecía cubierto. Un backup que parece completo y no lo es engaña más que no tener backup.

Por eso este test mira dos cosas: que NO vuelva a haber un `/Users/<alguien>` escrito a mano, y
que el descubrimiento de la memoria coja TODOS los proyectos, no una selección.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.path.join(ROOT, "tools", "backup.sh")


def _dry(home, repo):
    """Corre el script en modo prueba: dice qué copiaría y no toca nada."""
    env = dict(os.environ, HOME=home, BTP_REPO=repo, BTP_BACKUP_DRY="1")
    r = subprocess.run(["bash", BACKUP], env=env, capture_output=True, text=True, timeout=30)
    return r.stdout


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="backup-")
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "claudecode")
        os.makedirs(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _memoria(self, slug):
        d = os.path.join(self.home, ".claude", "projects", slug, "memory")
        os.makedirs(d)
        return d

    def test_ninguna_ruta_de_usuario_escrita_a_mano(self):
        """La regresión que costó 13 días sin copia. Se caza aquí o se caza en un desastre."""
        with open(BACKUP, encoding="utf-8") as f:
            txt = f.read()
        # Se ignoran los comentarios: ahí SÍ se nombran las rutas viejas, justo para explicar
        # por qué no se hardcodean.
        codigo = "\n".join(l for l in txt.splitlines() if not l.lstrip().startswith("#"))
        self.assertEqual([], re.findall(r"/Users/[a-zA-Z]", codigo),
                         "hay una ruta /Users/<alguien> escrita a mano: eso mató el backup 13 días")

    def test_copia_el_repo_y_TODA_la_memoria(self):
        m1 = self._memoria("-Users-polaris-claudecode")
        m2 = self._memoria("-Users-polaris")            # el que se quedaba fuera
        out = _dry(self.home, self.repo)
        self.assertIn(self.repo, out)
        self.assertIn(m1, out)
        self.assertIn(m2, out, "la memoria de las sesiones del HOME no puede quedarse fuera")

    def test_si_no_hay_memoria_lo_DICE(self):
        """Sin aviso, «copié el repo» y «copié todo» se leen igual en el log."""
        out = _dry(self.home, self.repo)
        self.assertIn("AVISO: no veo la memoria durable", out)
        self.assertIn(self.repo, out)                   # el repo se copia igual

    def test_un_proyecto_sin_carpeta_memory_no_rompe_nada(self):
        os.makedirs(os.path.join(self.home, ".claude", "projects", "-otro-proyecto"))
        m = self._memoria("-Users-polaris-claudecode")
        out = _dry(self.home, self.repo)
        self.assertIn(m, out)
        self.assertNotIn("-otro-proyecto", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
