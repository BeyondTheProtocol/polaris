#!/usr/bin/env python3
"""test_anatomia_worktree.py — `anatomia.py sellar` desde un worktree ve el código de la RAMA.

Deuda `anatomia-sellar-desde-worktree-mira-casa-base` (26-sep-26). `anatomia.py` tomaba TODO de
`~/claudecode`: desde un worktree, `sellar` comparaba el código de casa base y escribía la huella
en el worktree. Con un hook nuevo en la rama dijo «nada que sellar», y al fusionar
`test_anatomia_al_dia` salió ROJO en casa base. Ahora hay dos raíces:
  · CODIGO (lo versionado: tools, hooks, agentes, skills, rutinas, tests) = el árbol del script.
  · ROOT   (lo vivo: estado, cajas de la fuente de verdad, disco) = casa base.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import anatomia  # noqa: E402


class DosRaices(unittest.TestCase):
    def test_codigo_es_el_arbol_del_script(self):
        env = {k: v for k, v in os.environ.items() if k not in ("BTP_REPO", "BTP_STATE_DIR")}
        r = subprocess.run([sys.executable, "-c", "import anatomia; print(anatomia.CODIGO)"],
                           cwd=os.path.join(ROOT, "tools"), env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.stdout.strip().splitlines()[-1], ROOT,
                         "CODIGO tiene que ser el árbol donde vive anatomia.py: %s" % r.stderr[-300:])

    def test_lo_vivo_sigue_en_casa_base(self):
        if os.environ.get("BTP_REPO"):
            self.skipTest("BTP_REPO fija las dos raíces a propósito")
        self.assertEqual(anatomia.ROOT, os.path.expanduser("~/claudecode"))

    def test_un_hook_nuevo_de_la_rama_entra_en_la_huella(self):
        tmp = tempfile.mkdtemp(prefix="anatomia_wt_")
        os.makedirs(os.path.join(tmp, ".claude", "hooks"))
        open(os.path.join(tmp, ".claude", "hooks", "hook_de_la_rama.py"), "w").close()
        viejo, anatomia.CODIGO = anatomia.CODIGO, tmp
        try:
            self.assertIn("hook_de_la_rama", anatomia.guardas())
        finally:
            anatomia.CODIGO = viejo


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    ok = r.wasSuccessful()
    print("✅ anatomía desde worktree: %d tests en verde" % r.testsRun if ok
          else "❌ anatomía desde worktree: %d fallo(s)" % (len(r.failures) + len(r.errors)))
    sys.exit(0 if ok else 1)
