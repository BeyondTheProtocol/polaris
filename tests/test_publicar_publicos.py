#!/usr/bin/env python3
"""test_publicar_publicos.py — la excepción con consentimiento sale, y solo ella.

25-sep-2026: con una ficha de persona en `anonimato: alto`, su nombre se tapaba también en
«🙏 Gracias» del README, donde la regla de {{TITULAR}} manda nombrar a quien aporta (y esa persona
consintió). El arreglo es un overlay local (`tools/publicos.local.json`) de cadenas EXACTAS,
cada una con los ficheros donde vale y la fecha y el origen del consentimiento. Lo que fija:

  a. El README publicado del repo real lleva el nombre enlazado de «Gracias».
  b. En el mismo fichero, un nombre de pila suelto y otro nombre completo con ese nombre de
     pila siguen tapados; y la excepción no vale fuera de sus ficheros.
  c. Una cadena que no está en el overlay, o una entrada sin consentimiento, sin ficheros o
     con un nombre suelto, no pasa.

Los casos b y c usan nombres inventados.
"""
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _entorno  # noqa: E402
import publicar  # noqa: E402

GRACIAS_REAL = "[{{CONTACTO}}](https://contacto)"

CONSENT = {"fecha": "2026-09-25", "origen": "test"}
FICHAS = {
    "zenon.json": {"slug": "zenon", "anonimato": "alto", "persona": "Zenón Quiroga",
                   "fuentes": [{"handle": "zq", "url": "https://zenonquiroga.example"}]},
    "artiaga.json": {"slug": "artiaga", "anonimato": "alto", "persona": "Zenón Artiaga"},
    "pim.json": {"slug": "pim", "anonimato": "alto", "persona": "Tadeo Pim"},
}
EXCEPCIONES = [
    {"texto": "Zenón Quiroga", "ficheros": ["README.md"], "consentimiento": CONSENT},
    {"texto": "https://zenonquiroga.example", "ficheros": ["README.md"], "consentimiento": CONSENT},
    # Mal formadas: ninguna de estas puede destapar nada.
    {"texto": "Tadeo Pim", "ficheros": ["README.md"]},                                  # sin consentimiento
    {"texto": "Tadeo Pim", "ficheros": [], "consentimiento": CONSENT},                  # sin ficheros
    {"texto": "Zenón", "ficheros": ["README.md"], "consentimiento": CONSENT},           # nombre suelto
    {"texto": "Tadeo Pim", "ficheros": ["README.md"], "consentimiento": {"fecha": "2026-09-25"}},
]
README = ("## 🙏 Gracias\n\n"
          "- **[Zenón Quiroga](https://zenonquiroga.example)**: una revisión.\n"
          "- Zenón ayudó también, y Zenón Artiaga y Tadeo Pim.\n")
NOTA = "Idea de Zenón Quiroga (https://zenonquiroga.example).\n"


def _git(d, *args):
    subprocess.run(["git", "-C", d] + list(args), check=True, capture_output=True)


def _repo():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "tools", "config", "personas"))
    ficheros = {
        "tools/perfil.local.json": {"titular": {"nombre": "Zoraida", "apellidos": [], "nacimiento": [],
                                                "contactos": []},
                                    "sustituciones": [[r"\bVetada\b", "{{X}}"]], "bloques": []},
        "tools/nombres.local.json": {"nombres": ["Zenobia"]},
        "tools/publicos.local.json": {"excepciones": EXCEPCIONES},
    }
    for nombre, ficha in FICHAS.items():
        ficheros["tools/config/personas/" + nombre] = ficha
    for rel, d_ in ficheros.items():
        with io.open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            json.dump(d_, fh, ensure_ascii=False)
    for rel, texto in {"README.md": README, "tools/nota.md": NOTA,
                       ".gitignore": "*.local.json\n_arbol/\n"}.items():
        with io.open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            fh.write(texto)
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    return d


class ConRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = _repo()
        self._env = os.environ.get("BTP_REPO")
        os.environ["BTP_REPO"] = self.tmp
        self.p = importlib.reload(publicar)
        self.destino = os.path.join(self.tmp, "_arbol")
        self.assertEqual(self.p.publicar(self.destino), 0)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("BTP_REPO", None)
        else:
            os.environ["BTP_REPO"] = self._env
        importlib.reload(publicar)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def leer(self, rel):
        with io.open(os.path.join(self.destino, rel), encoding="utf-8") as fh:
            return fh.read()


class TestExcepcion(ConRepo):
    def test_a_la_excepcion_sale_enlazada_en_su_fichero(self):
        self.assertIn("[Zenón Quiroga](https://zenonquiroga.example)", self.leer("README.md"))

    def test_b_nombre_suelto_y_otro_completo_siguen_tapados(self):
        readme = self.leer("README.md")
        linea = readme.splitlines()[-1]
        self.assertNotIn("Zenón", linea)
        self.assertNotIn("Artiaga", linea)
        self.assertEqual(linea.count("{{CONTACTO}}"), 3)

    def test_b_fuera_de_sus_ficheros_no_vale(self):
        nota = self.leer(os.path.join("tools", "nota.md"))
        self.assertNotIn("Quiroga", nota)
        self.assertNotIn("zenonquiroga", nota)

    def test_c_lo_que_no_esta_o_esta_mal_formado_no_pasa(self):
        readme = self.leer("README.md")
        self.assertNotIn("Tadeo", readme)
        self.assertNotIn("Pim", readme)
        self.assertEqual(self.p._excepciones_ignoradas(), 4)
        self.assertEqual(set(self.p._EXCEPCIONES), {"README.md"})

    def test_c_el_barrido_sigue_cazandola_fuera_de_su_fichero(self):
        self.assertTrue(self.p.vetados_en("Zenón Quiroga", rel="tools/nota.md"))
        self.assertTrue(self.p.vetados_en("Zenón Quiroga"))
        self.assertFalse(self.p.vetados_en("Zenón Quiroga", rel="README.md"))
        self.assertTrue(self.p.vetados_en("Zenón Quirogas y Tadeo Pim", rel="README.md"))


class TestRepoReal(unittest.TestCase):
    """a. Con el overlay real: el README publicado lleva su «Gracias» con nombre y enlace."""

    def test_a_readme_real_lleva_el_nombre_enlazado(self):
        if _entorno.es_espejo():
            self.skipTest("árbol del espejo: los nombres ya vienen sustituidos")
        if not publicar._EXCEPCIONES.get("README.md"):
            self.skipTest("sin tools/publicos.local.json (overlay local)")
        with io.open(os.path.join(RAIZ, "README.md"), encoding="utf-8") as fh:
            original = fh.read()
        self.assertIn(GRACIAS_REAL, original)
        salida = publicar.despersonalizar(original, rel="README.md")
        self.assertIn(GRACIAS_REAL, salida)
        self.assertEqual(publicar.vetados_en(salida, rel="README.md"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
