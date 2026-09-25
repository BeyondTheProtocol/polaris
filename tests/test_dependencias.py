#!/usr/bin/env python3
"""Test de tools/dependencias.py: el grafo ve los enlaces por RUTA, no solo los imports.

Por qué: la razón de hacerlo propio en vez de instalar graphify (25-sep-2026) es que en
Polaris la mayoría de enlaces son rutas dentro de cadenas (subprocess, .sh, plists de
launchd, settings.json). Si un día el grafo deja de verlas, este test se pone rojo.
Corre sobre un repo de juguete (sin git), no sobre el real.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import dependencias  # noqa: E402

FICHEROS = {
    "tools/base.py": "X = 1\n",
    "tools/usa_import.py": "import base\n",
    "tools/usa_ruta.py": "import subprocess\nsubprocess.run(['python3', 'tools/base.py'])\n",
    "tools/usa_junto.py": "import os\nP = os.path.join(AQUI, 'hermano.sh')\n",
    "tools/hermano.sh": "#!/bin/sh\npython3 \"$(dirname $0)/usa_import.py\"\n",
    "tools/launchd/com.x.plist": "<string>/Users/polaris/claudecode/tools/usa_ruta.py</string>\n",
    ".claude/settings.json": '{"command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/gancho.py"}\n',
    ".claude/hooks/gancho.py": "import sys\n",
    "README.md": "Mira `tools/base.py`.\n",
    "tools/nadie.py": "pass\n",
    "tools/roto.py": "def (:\n  tools/base.py\n",
}


class TestDependencias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="deps-")
        for rel, txt in FICHEROS.items():
            p = os.path.join(cls.tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(txt)
        cls.g = dependencias.Grafo(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def arista(self, o, d, t):
        self.assertIn((o, d, t), self.g.aristas, "falta %s -[%s]-> %s" % (o, t, d))

    def test_cada_tipo_de_enlace(self):
        self.arista("tools/usa_import.py", "tools/base.py", "import")
        self.arista("tools/usa_ruta.py", "tools/base.py", "ruta")
        self.arista("tools/usa_junto.py", "tools/hermano.sh", "ruta")        # por nombre junto al origen
        self.arista("tools/hermano.sh", "tools/usa_import.py", "ruta")
        self.arista("tools/launchd/com.x.plist", "tools/usa_ruta.py", "launchd")  # ruta absoluta
        self.arista(".claude/settings.json", ".claude/hooks/gancho.py", "config")
        self.arista("README.md", "tools/base.py", "doc")

    def test_py_que_no_parsea_no_tumba_el_grafo(self):
        self.arista("tools/roto.py", "tools/base.py", "ruta")

    def test_quien_directo_y_hondo(self):
        directo = self.g.quien("tools/base.py")
        self.assertEqual(set(directo), {"tools/usa_import.py", "tools/usa_ruta.py", "README.md", "tools/roto.py"})
        hondo = self.g.quien("tools/base.py", hondo=True)
        # base ← usa_ruta ← plist ; base ← usa_import ← hermano.sh ← usa_junto
        for o in ("tools/launchd/com.x.plist", "tools/hermano.sh", "tools/usa_junto.py"):
            self.assertIn(o, hondo)
        self.assertNotIn("tools/base.py", hondo)

    def test_huerfanos(self):
        hs = self.g.huerfanos("tools")
        self.assertIn("tools/nadie.py", hs)
        self.assertNotIn("tools/base.py", hs)

    def test_buscar_por_nombre(self):
        self.assertEqual(self.g.buscar("base"), "tools/base.py")
        self.assertEqual(self.g.buscar("hermano.sh"), "tools/hermano.sh")
        with self.assertRaises(SystemExit):
            self.g.buscar("no_existe")


if __name__ == "__main__":
    unittest.main()
