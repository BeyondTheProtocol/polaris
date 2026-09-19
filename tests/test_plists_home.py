#!/usr/bin/env python3
"""Test: instalar un plist no puede tumbar el lazo por una ruta de otra máquina.

POR QUÉ EXISTE (2-sep-2026, deuda `plists-del-repo-con-ruta-de-usuario-muerta`, NED alto).

Este repo es unión de dos equipos (el Air y el mini) y sus plists van versionados con la ruta
absoluta de quien los escribió. El 2-sep-2026, 48 de los 66 plists de `tools/launchd/`
apuntaban a `/Users/titular/claudecode/...`, un usuario que **no existe** en el mini.

El sistema funcionaba porque los plists ya INSTALADOS en `~/Library/LaunchAgents` tenían la
ruta buena: `activar_daemon.py` reescribe el home al instalar. Pero `btp_run.sh install` hacía
`cp -f` literal del repo al directorio de LaunchAgents. Un solo `install` habría machacado los
tres plists del lazo — dispatcher, healthcheck y bot-telegram — con rutas muertas, y el lazo
se habría caído entero sin que nadie lo notara hasta la siguiente pasada.

Lo que se prueba aquí no es que los plists tengan una ruta concreta: eso fallaría en el Air y
además obligaría a versionar la ruta de una máquina, que es justo el problema. Se prueba que
**los dos caminos de instalación reescriben el home**, de modo que da igual con qué ruta se
haya versionado el fichero.
"""

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SH = os.path.join(ROOT, "tools", "btp_run.sh")
PLISTS = os.path.join(ROOT, "tools", "launchd")


class InstalarNoPuedeRomperElLazo(unittest.TestCase):
    def test_btp_run_no_copia_plists_en_crudo(self):
        """`cp -f` de un plist es la bomba. Tiene que pasar por una reescritura del home."""
        src = io.open(RUN_SH, encoding="utf-8").read()
        lineas_cp = [l.strip() for l in src.split("\n")
                     if re.search(r"cp\s+-f\s+.*PLIST_DIR", l)]
        self.assertEqual(
            lineas_cp, [],
            "btp_run.sh copia un plist en crudo desde el repo:\n  %s\n"
            "El repo es unión de dos máquinas: la ruta versionada puede ser de la otra. "
            "Tiene que reescribirse el home al instalar (sed sobre /Users/*/claudecode), o un "
            "`install` deja el lazo apuntando a un usuario que no existe."
            % "\n  ".join(lineas_cp))

    def test_btp_run_reescribe_el_home_en_install_y_en_start(self):
        src = io.open(RUN_SH, encoding="utf-8").read()
        # Debe haber reescritura en los DOS caminos que escriben en LaunchAgents.
        reescrituras = re.findall(r'sed -E "s#/Users/\[\^/\\?"\]\+/claudecode#\$REPO#g"', src)
        self.assertGreaterEqual(
            len(reescrituras), 2,
            "esperaba reescritura del home en `install` Y en el fallback de `start`; "
            "encontradas %d" % len(reescrituras))

    def test_la_reescritura_funciona_de_verdad(self):
        """No basta con que la línea esté: se ejecuta el sed sobre un plist de mentira."""
        falso = ('<string>/Users/otro_usuario_que_no_existe/claudecode/tools/x.sh</string>\n'
                 '<string>/Users/polaris/claudecode/tools/y.sh</string>')
        with tempfile.NamedTemporaryFile("w", suffix=".plist", delete=False,
                                         encoding="utf-8") as f:
            f.write(falso)
            ruta = f.name
        try:
            repo = os.path.join(os.path.expanduser("~"), "claudecode")
            r = subprocess.run(
                ["sed", "-E", 's#/Users/[^/"]+/claudecode#%s#g' % repo, ruta],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("otro_usuario_que_no_existe", r.stdout,
                             "la reescritura dejó pasar una ruta de otro usuario")
            self.assertEqual(r.stdout.count(repo), 2,
                             "las dos rutas tenían que quedar apuntando a %s:\n%s"
                             % (repo, r.stdout))
        finally:
            os.unlink(ruta)

    def test_los_plists_del_lazo_existen_y_son_parseables(self):
        """Si un plist del lazo desaparece o se corrompe, install falla en silencio."""
        for label in ("com.btp.dispatcher", "com.btp.healthcheck", "com.btp.bot-telegram"):
            p = os.path.join(PLISTS, label + ".plist")
            self.assertTrue(os.path.exists(p), "falta el plist del lazo: %s" % label)
            r = subprocess.run(["plutil", "-lint", p], capture_output=True, text=True,
                               timeout=30)
            self.assertEqual(r.returncode, 0, "%s no es un plist válido:\n%s"
                             % (label, r.stdout or r.stderr))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(InstalarNoPuedeRomperElLazo))
    if res.wasSuccessful():
        print("✅ PLISTS EN VERDE (%d casos · instalar reescribe el home, no copia en crudo)"
              % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
