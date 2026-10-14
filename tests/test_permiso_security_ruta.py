#!/usr/bin/env python3
"""test_permiso_security_ruta.py — el Llavero se llama por su ruta, no por el PATH (13-oct-26).

EL HUECO. `tools/permiso_envio.py` y `tools/_secrets.py` lanzaban `security` por su nombre
desnudo. El binario que corre entonces lo elige el PATH de quien lanza el proceso: con un
`security` falso en un directorio anterior, ese programa recibe el secreto que se guarda (va en
argv) y contesta lo que quiera cuando se le pide la clave que firma el permiso de envío.

QUÉ SE FIJA. Con un `security` falso el primero del PATH, las cuatro llamadas (leer y guardar,
en los dos módulos) siguen resolviendo a `/usr/bin/security`. No se ejecuta nada: se intercepta
`subprocess.run`, se mira qué programa habría corrido con ESE PATH y se devuelve una respuesta
de mentira. El Llavero real no se toca.
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import _secrets  # noqa: E402
import permiso_envio as P  # noqa: E402

REAL = "/usr/bin/security"


class _Resp(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


class TestSecurityRutaAbsoluta(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sec-falso-")
        self.falso = os.path.join(self.tmp, "security")
        with open(self.falso, "w") as f:
            f.write("#!/bin/sh\necho CLAVE-DEL-FALSO\n")
        os.chmod(self.falso, os.stat(self.falso).st_mode | stat.S_IXUSR)
        self._path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.tmp + os.pathsep + self._path
        self.lanzados = []
        # Los dos módulos comparten el mismo `subprocess`: basta con cambiarlo una vez.
        self._run_real = _secrets.subprocess.run
        _secrets.subprocess.run = self._run_falso

    def tearDown(self):
        _secrets.subprocess.run = self._run_real
        os.environ["PATH"] = self._path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_falso(self, argv, **_kw):
        """No ejecuta nada. Apunta qué binario habría corrido con el PATH del test."""
        self.lanzados.append((argv[0], shutil.which(argv[0])))
        if "add-generic-password" in argv:
            self._guardado = argv[argv.index("-w") + 1]
            return _Resp()
        return _Resp(getattr(self, "_guardado", "valor-de-prueba") + "\n")

    def _exige_real(self, cuantas):
        self.assertEqual(len(self.lanzados), cuantas, self.lanzados)
        for pedido, resuelto in self.lanzados:
            self.assertEqual(pedido, REAL, "se lanzó %r en vez de la ruta absoluta" % pedido)
            self.assertNotEqual(resuelto, self.falso, "el PATH coló el security falso")

    def test_el_falso_es_el_primero_del_path(self):
        """Si esto no se cumple, el resto del test no prueba nada."""
        self.assertEqual(shutil.which("security"), self.falso)

    def test_secrets_leer(self):
        self.assertEqual(_secrets._keychain("btp-prueba-no-existe"), "valor-de-prueba")
        self._exige_real(1)

    def test_secrets_guardar(self):
        self.assertEqual(_secrets.set("btp-prueba-no-existe", "abc123"), 6)
        self._exige_real(2)             # guardar + releer para comprobar

    def test_permiso_leer(self):
        valor, estado = P._llavero_estado()
        self.assertEqual((valor, estado), ("valor-de-prueba", "ok"))
        self._exige_real(1)

    def test_permiso_crear(self):
        self.assertTrue(P._llavero_crear())
        self._exige_real(2)             # crear + releer

    def test_el_lector_generico_no_sirve_para_las_claves_que_firman_el_permiso(self):
        """`_secrets.get("btp-firmante-ed25519")` era la vía más corta para sacarlas."""
        for servicio in ("btp-ok-envio-mac", "btp-firmante-ed25519", " BTP-Firmante-ED25519 "):
            self.assertIsNone(_secrets._keychain(servicio))
            self.assertEqual(_secrets.get(servicio, default="nada"), "nada")
            with self.assertRaises(ValueError):
                _secrets.set(servicio, "abc")
        self.assertEqual(self.lanzados, [], "ni siquiera se llama al Llavero")

    def test_ninguna_llamada_por_nombre_desnudo_en_el_fuente(self):
        """Invariante de estructura: una llamada nueva por nombre desnudo en estos dos ficheros
        se ve aquí aunque nadie escriba su test."""
        import re
        for rel in ("tools/_secrets.py", "tools/permiso_envio.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                fuente = f.read()
            self.assertIsNone(re.search(r"\[\s*['\"]security['\"]", fuente),
                              "%s lanza `security` por su nombre desnudo" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=1)
