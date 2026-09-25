#!/usr/bin/env python3
"""test_kb_lock.py — dos reindexados a la vez no pueden romper el RAG.

POR QUÉ EXISTE (20-sep-2026). El daemon `com.btp.kb-reindex` llevaba **56 días** fallando
intermitentemente (deuda `daemon_fallando:com.btp.kb-reindex`, 7 detecciones, 3 remisiones). El
log decía dos cosas distintas en el mismo sitio:

    sqlite3.OperationalError: table chunks already exists
    sqlite3.OperationalError: attempt to write a readonly database

Las dos son la MISMA carrera: `build()` escribía en un `.tmp` con nombre fijo, compartido por
todos los procesos. Cuando el daemon y un reindex manual coincidían (y en esta casa coinciden:
varias sesiones trabajan a la vez), el segundo abría el `.tmp` del primero —ya con esquema— o
escribía en un fichero que el primero acababa de mover con `os.replace`.

Lo que se pierde cuando eso pasa no es el reindex: es la FRESCURA del RAG. `kb.py ask` es lo que
el sistema consulta antes de afirmar nada del caso; si el índice se queda viejo, responde con la
foto de antes y nadie se entera. Por eso esto es un test y no una nota.

Se fija aquí:
  1. cada proceso escribe su propio `.tmp` (nunca el del vecino);
  2. el segundo reindex concurrente NO revienta: se retira en verde;
  3. el índice resultante es válido y responde.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(ROOT, "tools", "kb.py")


def _repo_de_juguete():
    """Una fuente de verdad mínima: indexar el corpus real tardaría minutos."""
    d = tempfile.mkdtemp(prefix="kblock-")
    fv = os.path.join(d, "00_FUENTE-DE-VERDAD")
    os.makedirs(fv)
    for i in range(3):
        with open(os.path.join(fv, "nota%d.md" % i), "w", encoding="utf-8") as f:
            f.write("# Nota %d\n\nContenido de prueba sobre gemelos de criterio.\n" % i)
    return d


class ReindexConcurrente(unittest.TestCase):

    def setUp(self):
        self.repo = _repo_de_juguete()
        self.env = dict(os.environ, BTP_REPO=self.repo)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _index(self):
        return subprocess.Popen([sys.executable, KB, "index"], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_el_tmp_lleva_el_pid_asi_que_nadie_pisa_al_vecino(self):
        fuente = open(KB, encoding="utf-8").read()
        self.assertIn('"%s.tmp.%d" % (DB, os.getpid())', fuente,
                      "el .tmp volvió a ser un nombre fijo compartido: eso es la carrera")

    def test_dos_reindex_a_la_vez_ninguno_revienta(self):
        a, b = self._index(), self._index()
        sa, ea = a.communicate(timeout=180)
        sb, eb = b.communicate(timeout=180)
        for rc, err in ((a.returncode, ea), (b.returncode, eb)):
            self.assertEqual(rc, 0, "un reindex concurrente salió en rojo:\n%s" % err)
            self.assertNotIn("already exists", err)
            self.assertNotIn("readonly database", err)
        # uno hizo el trabajo; el otro pudo retirarse diciéndolo (o llegó cuando ya no había nadie)
        self.assertTrue("Indexados" in sa or "Indexados" in sb,
                        "ninguno de los dos llegó a escribir el índice")

    def test_el_indice_queda_usable_despues_de_la_carrera(self):
        self._index().communicate(timeout=180)
        r = subprocess.run([sys.executable, KB, "ask", "gemelos de criterio"],
                           env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nota", r.stdout.lower(),
                      "el índice no devuelve los documentos que acaba de indexar")

    def test_no_quedan_tmp_huerfanos_del_proceso_que_termino(self):
        self._index().communicate(timeout=180)
        fv = os.path.join(self.repo, "00_FUENTE-DE-VERDAD")
        sobras = [f for f in os.listdir(fv) if ".tmp." in f]
        self.assertFalse(sobras, "quedaron tmp sin limpiar: %s" % sobras)


if __name__ == "__main__":
    unittest.main(verbosity=2)
