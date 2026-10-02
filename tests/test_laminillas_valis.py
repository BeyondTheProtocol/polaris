#!/usr/bin/env python3
"""tests/test_laminillas_valis.py — el respaldo (4) del registro con VALIS DE VERDAD (2-oct-26).

Hallazgo del piloto real (2-oct-26): `registro-par P-KI67 P-CK19 --valis` acabó con rc 0 y los
tres FC grandes con «VALIS: sin resultado». El subproceso al venv `valis` NUNCA había corrido:
  1. VALIS 1.2.0, con `non_rigid_registrar_cls=None`, no crea `non_rigid_reg_kwargs` y su
     `cleanup()` lo toca al final de `register()`: AttributeError, tragado (devuelve None);
  2. el script hacía `json.dumps` de `slide_dimensions_wh` (numpy int64): TypeError, rc 1.
Y el convenio de `Slide.M` se había supuesto al revés (VALIS aplica inv(M) a los puntos): con
VALIS corriendo, habría devuelto la transformada INVERSA.

Aquí, con dos pares sintéticos de transformada CONOCIDA (`laminillas_humo.imagenes_registro`:
giro de 8° y traslación; uno además en espejo), `laminillas_registro.respaldo_valis` tiene que:
  (a) correr (rc 0, `corrio`) y recuperar la verdad con error < 1 px (2 µm a la escala del humo),
      en directo y en espejo;
  (b) correr igual DENTRO de una jaula `analisis` generada como la de la ventanilla (sin red,
      escritura solo en SESION y su tmp) con el entorno en lista blanca de la ventanilla.
El humo `procesa laminillas_humo -- registro-valis` hace (a)+(b) por la ventanilla de verdad.

SIN DATOS: imágenes sintéticas. Cada corrida de VALIS tarda ~10 s (no es lento). Sin los venvs
`patologia` y `valis` (o sin sandbox-exec para (b)), SKIP (rc 77).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(RAIZ, "tools")
sys.path.insert(0, TOOLS)
VENVS = os.path.expanduser("~/.polaris-venvs")
VENV_PAT = os.path.join(VENVS, "patologia", "bin", "python")
VENV_VALIS = os.path.join(VENVS, "valis", "bin", "python")
SANDBOX = "/usr/bin/sandbox-exec"
HAY_VENVS = os.path.exists(VENV_PAT) and os.path.exists(VENV_VALIS)

CORRE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import laminillas_humo as H
import laminillas_registro as R
out = {}
for caso in sys.argv[2].split(","):
    espejo = caso == "espejo"
    f, m, T, mask = H.imagenes_registro(espejo=espejo, **H.VALIS_VERDAD)
    M, info = R.respaldo_valis(f, m, H.MPP_REGISTRO, espejo=espejo,
                               tmpdir=os.environ.get("TMPDIR"))
    out[caso] = {"info": info}
    if M is not None:
        emax, emed = H.error_transformada_um(M, T, mask)
        out[caso].update(error_max_um=emax, error_mediana_um=emed,
                         det=float(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0]))
print("RESULTADO " + json.dumps(out))
"""


def _lee(r):
    lineas = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULTADO ")]
    if r.returncode != 0 or not lineas:
        raise AssertionError("rc %s\n%s" % (r.returncode, r.stderr[-2000:]))
    return json.loads(lineas[-1][len("RESULTADO "):])


@unittest.skipUnless(HAY_VENVS, "sin venvs patologia/valis")
class ConvenioSlideM(unittest.TestCase):
    """(a) fuera de jaula: VALIS corre y `respaldo_valis` recupera la transformada conocida."""

    @classmethod
    def setUpClass(cls):
        cls.t = os.path.realpath(tempfile.mkdtemp(prefix="lam-valis-"))
        env = dict(os.environ, TMPDIR=cls.t)
        r = subprocess.run([VENV_PAT, "-c", CORRE, TOOLS, "directo,espejo"], env=env,
                           capture_output=True, text=True, timeout=1800)
        cls.r = r

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t, ignore_errors=True)

    def _caso(self, caso):
        import laminillas_humo as H
        o = _lee(self.r)[caso]
        self.assertTrue(o["info"]["corrio"], o["info"].get("motivo"))
        self.assertEqual(o["info"]["rc"], 0)
        # Tolerancia: < 1 px a la escala del humo (2 µm/px) en TODO punto de tejido de la móvil
        self.assertLess(o["error_max_um"], H.VALIS_TOL_PX * H.MPP_REGISTRO, o)
        return o

    def test_directo(self):
        o = self._caso("directo")
        self.assertGreater(o["det"], 0)

    def test_espejo(self):
        """La global dice espejo: la móvil va volteada a VALIS y la matriz conserva el espejo."""
        o = self._caso("espejo")
        self.assertLess(o["det"], 0)


@unittest.skipUnless(HAY_VENVS and os.path.exists(SANDBOX), "sin venvs o sin sandbox-exec")
class DentroDeAnalisis(unittest.TestCase):
    """(b) la misma ruta que el registro real: proceso del venv patologia, en una jaula `analisis`
    generada por `laminillas_jaulas` como la de la ventanilla y con su entorno en lista blanca,
    que lanza el venv valis por subproceso."""

    @classmethod
    def setUpClass(cls):
        import laminillas_jaulas as J
        import laminillas_ventanilla as V
        cls.t = os.path.realpath(tempfile.mkdtemp(prefix="lam-valis-jaula-"))
        sesion = os.path.join(cls.t, "sesion")
        for d in (sesion, os.path.join(sesion, "tmp"), os.path.join(sesion, "cache")):
            os.makedirs(d, mode=0o700)
        conf = V.CONF["laminillas_humo"]
        perfil = J.escribe(conf["perfil"], destino_dir=os.path.join(cls.t, "jaulas"),
                           sesion=sesion, tmpdir=os.path.join(sesion, "tmp"))
        cls.r = subprocess.run([SANDBOX, "-f", perfil, VENV_PAT, "-c", CORRE, TOOLS, "directo"],
                               env=V.entorno(conf, sesion), cwd=sesion, capture_output=True,
                               text=True, timeout=1800)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t, ignore_errors=True)       # el perfil lleva raíces clínicas: fuera

    def test_valis_corre_en_la_jaula(self):
        import laminillas_humo as H
        o = _lee(self.r)["directo"]
        self.assertTrue(o["info"]["corrio"], o["info"].get("motivo"))
        self.assertLess(o["error_max_um"], H.VALIS_TOL_PX * H.MPP_REGISTRO, o)


if __name__ == "__main__":
    if not HAY_VENVS:
        print("SKIP: faltan los venvs patologia/valis")
        sys.exit(77)
    unittest.main()
