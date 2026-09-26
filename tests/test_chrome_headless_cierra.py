#!/usr/bin/env python3
"""test_chrome_headless_cierra.py — un script de captura que muere a medias no deja Chrome vivo.

POR QUÉ EXISTE (26-sep-26, deuda `chrome_headless_huerfano`). `tools/captura_visor.mjs` solo
cerraba Chrome en los caminos felices. Tres ejecuciones que murieron a medias dejaron tres Chrome
headless huérfanos con dos pestañas cada uno al 100 % de CPU durante ~27 h (unos 6 de los 10
núcleos). Todo Polaris iba lento y nadie avisó. Ahora `tools/_chrome_headless.mjs` cierra Chrome
pase lo que pase con el script, salvo un `kill -9` (eso lo limpia bucles_colgados.py).

Se lanza el ayudante de verdad (Chrome real) y se mata el script de tres maneras: SIGTERM, una
excepción sin atrapar y el tope de tiempo. En las tres no puede quedar ningún proceso con su
perfil y la carpeta del perfil tiene que desaparecer.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AYUDANTE = os.path.join(ROOT, "tools", "_chrome_headless.mjs")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

SCRIPT = """
import {{ writeSync }} from 'node:fs'
import {{ lanzarChrome }} from {ayudante!r}
const {{ perfil, pid }} = lanzarChrome([], {{ topeMs: {tope} }})
// Síncrono: en macOS console.log a una tubería es asíncrono y la línea podía no llegar a Python
// hasta que node salía (el caso SIGTERM esperaba al tope de 60 s y fallaba).
writeSync(1, pid + ' ' + perfil + '\\n')
{final}
"""


def _vivos(perfil):
    out = subprocess.run(["/bin/ps", "-axwwo", "pid=,command="], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if perfil in l]


@unittest.skipUnless(os.path.exists(CHROME), "sin Google Chrome en esta máquina")
class ChromeSeCierraSiempre(unittest.TestCase):

    def _lanzar(self, final, tope=60000):
        d = tempfile.mkdtemp(prefix="chrome-cierra-")
        js = os.path.join(d, "s.mjs")
        with open(js, "w", encoding="utf-8") as f:
            f.write(SCRIPT.format(ayudante=AYUDANTE, tope=tope, final=final))
        p = subprocess.Popen(["node", js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        linea = p.stdout.readline().strip()
        if not linea:   # leer stderr solo si falla: read() espera a que node termine
            self.fail("el ayudante no arrancó Chrome: %s" % p.stderr.read()[:300])
        pid, perfil = linea.split(" ", 1)
        return p, perfil

    def _sin_restos(self, perfil):
        fin = time.time() + 10
        while time.time() < fin and _vivos(perfil):
            time.sleep(0.2)
        self.assertEqual(_vivos(perfil), [], "quedó Chrome vivo con el perfil %s" % perfil)
        self.assertFalse(os.path.exists(perfil), "no se borró el perfil %s" % perfil)

    def test_sigterm_a_mitad(self):
        p, perfil = self._lanzar("setInterval(() => {}, 1000)")
        time.sleep(1.5)
        if not _vivos(perfil):
            p.kill()
            self.fail("Chrome no llegó a arrancar: la prueba no probaría nada (node rc=%r, stderr=%r)"
                      % (p.poll(), p.stderr.read()[:300]))
        p.terminate()
        p.wait(timeout=10)
        self._sin_restos(perfil)

    def test_excepcion_sin_atrapar(self):
        p, perfil = self._lanzar("setTimeout(() => { throw new Error('se rompe a medias') }, 1500)")
        p.wait(timeout=20)
        self.assertNotEqual(p.returncode, 0)
        self._sin_restos(perfil)

    def test_tope_de_tiempo(self):
        p, perfil = self._lanzar("setInterval(() => {}, 1000)", tope=2000)
        p.wait(timeout=20)
        self.assertEqual(p.returncode, 1)
        self._sin_restos(perfil)

    def test_salida_normal(self):
        p, perfil = self._lanzar("setTimeout(() => process.exit(0), 1500)")
        p.wait(timeout=20)
        self.assertEqual(p.returncode, 0)
        self._sin_restos(perfil)


if __name__ == "__main__":
    unittest.main(verbosity=2)
