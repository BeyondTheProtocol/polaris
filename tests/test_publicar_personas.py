#!/usr/bin/env python3
"""test_publicar_personas.py — una ficha de persona decide su anonimato, y el espejo lo cumple.

25-sep-2026, FUGA REAL en el espejo público: una ficha de `tools/config/personas/` que pedía no
nombrar a esa persona salió con el nombre de pila tapado y el apellido en claro (en `persona`,
en el handle y en la URL de su perfil). La deny-list solo tapaba lo copiado a mano en
`nombres.local.json`. Además, varias fichas se renombraban al mismo `contacto.json` y la última
pisaba a las demás sin que nadie se enterara. Lo que fija, con nombres de pega:

  1. Toda ficha real declara `anonimato` (alto | publico): una ficha nueva sin nivel no pasa.
  2. `alto` tapa nombre completo, apellido (con o sin tilde), handle y URL de perfil.
  3. No se lleva por delante palabras corrientes ni código (`Madrid`, `zipfile.ZipFile`).
  4. El barrido final caza el apellido si sobrevive.
  5. El teléfono de la titular (overlay `titular.telefonos`) se veta con cualquier separador.
  6. La ficha `alto` no se publica; una ficha sin nivel bloquea la publicación (rc=3);
     dos orígenes que acaban en la misma ruta pública bloquean la publicación (rc=1).

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
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
import publicar  # noqa: E402

FICHA_ALTO = {
    "slug": "zenon", "anonimato": "alto", "persona": "Zenón Quiroga",
    "agente": "consejero-test",
    "fuentes": [{"id": "x", "handle": "zenonquiroga", "url": "https://x.com/zenonquiroga"},
                {"id": "yt", "handle": "zq.lab", "url": "https://www.youtube.com/@zq.lab"}],
}
FICHA_CORTA = {"slug": "pim", "anonimato": "alto", "persona": "Tadeo Pim"}
FICHA_PUBLICA = {"slug": "gala", "anonimato": "publico", "persona": "Gala Figura"}


def _git(d, *args):
    subprocess.run(["git", "-C", d] + list(args), check=True, capture_output=True)


def _repo(fichas, extra=None):
    """Un repo de pega con overlay mínimo, las fichas dadas y ficheros extra versionados."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "tools", "config", "personas"))
    with io.open(os.path.join(d, "tools", "perfil.local.json"), "w", encoding="utf-8") as fh:
        json.dump({"titular": {"nombre": "Zoraida", "apellidos": [], "nacimiento": [], "contactos": [],
                               "telefonos": ["+34 611 22 33 44"]},
                   "sustituciones": [[r"\bVetada\b", "{{X}}"]], "bloques": []}, fh)
    with io.open(os.path.join(d, "tools", "nombres.local.json"), "w", encoding="utf-8") as fh:
        json.dump({"nombres": ["Zenobia"]}, fh)
    for nombre, ficha in fichas.items():
        with io.open(os.path.join(d, "tools", "config", "personas", nombre), "w", encoding="utf-8") as fh:
            fh.write(ficha if isinstance(ficha, str) else json.dumps(ficha, ensure_ascii=False))
    for rel, texto in (extra or {}).items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with io.open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            fh.write(texto)
    with io.open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write("*.local.json\n_arbol/\n")      # como en el repo real: el overlay no se versiona
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    return d


class ConRepo(unittest.TestCase):
    """Recarga `publicar` con BTP_REPO apuntando al repo de pega: las SUSTITUCIONES y los
    vetados se construyen al importar, así que es la única forma honesta de probarlos."""
    fichas = {"zenon.json": FICHA_ALTO, "pim.json": FICHA_CORTA, "gala.json": FICHA_PUBLICA}
    extra = None

    def setUp(self):
        self.tmp = _repo(self.fichas, self.extra)
        self._env = os.environ.get("BTP_REPO")
        os.environ["BTP_REPO"] = self.tmp
        self.p = importlib.reload(publicar)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("BTP_REPO", None)
        else:
            os.environ["BTP_REPO"] = self._env
        importlib.reload(publicar)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestFichasReales(unittest.TestCase):
    def test_toda_ficha_real_declara_su_anonimato(self):
        base = os.path.join(RAIZ, "tools", "config", "personas")
        fichas = sorted(f for f in os.listdir(base) if f.endswith(".json"))
        self.assertTrue(fichas)
        for f in fichas:
            with io.open(os.path.join(base, f), encoding="utf-8") as fh:
                nivel = json.load(fh).get("anonimato")
            self.assertIn(nivel, publicar.NIVELES_ANONIMATO, "%s sin `anonimato` válido" % f)

    def test_el_repo_real_no_tiene_fichas_sin_cubrir_ni_colisiones(self):
        self.assertEqual(publicar._personas_sin_cubrir(), [])
        entran = [r for r in publicar.versionados() if publicar._entra(r)]
        self.assertEqual(publicar._colisiones(entran), [])


class TestSustitucion(ConRepo):
    def test_apellido_handle_y_url_desaparecen(self):
        texto = ('"persona": "Zenón Quiroga",\n'
                 '"handle": "zenonquiroga", "url": "https://x.com/zenonquiroga"\n'
                 "canal https://www.youtube.com/@zq.lab — lo dijo Quiroga, y QUIROGA_OK, quiroga_bot\n"
                 "sin tilde: Zenon Quiroga; pegado: QuirogaBot\n")
        salida = self.p.despersonalizar(texto)
        for resto in ("Quiroga", "quiroga", "QUIROGA", "Zenón", "Zenon", "zenon", "zq.lab"):
            self.assertNotIn(resto, salida)
        self.assertIn("{{CONTACTO}}", salida)

    def test_no_rompe_palabras_ni_codigo(self):
        texto = "Viaje a Zenonia y Quirogas; with zipfile.PimFile(r) as z: pim = 1\nTadeo Pim firma."
        salida = self.p.despersonalizar(texto)
        self.assertIn("Zenonia", salida)            # empieza por el nombre, pero es otra palabra
        self.assertIn("Quirogas", salida)
        self.assertIn("zipfile.PimFile(r)", salida)  # apellido corto: solo palabra suelta exacta
        self.assertIn("pim = 1", salida)
        self.assertNotIn("Tadeo Pim", salida)

    def test_la_ficha_publica_no_se_toca(self):
        self.assertIn("Gala Figura", self.p.despersonalizar("según Gala Figura"))

    def test_el_barrido_caza_el_apellido_si_sobrevive(self):
        self.assertTrue(self.p.vetados_en("lo firmó Quiroga"))
        self.assertTrue(self.p.vetados_en("lo firmó Quíroga"))


class TestTelefonoTitular(ConRepo):
    def test_su_telefono_se_veta_con_cualquier_separador(self):
        for forma in ("611223344", "611 22 33 44", "611-223-344", "+34 611.22.33.44"):
            self.assertTrue(self.p.vetados_en("Llama al %s" % forma), forma)
        self.assertFalse(self.p.vetados_en("Llama al 600000000"))


class TestPublicar(ConRepo):
    extra = {"tools/nota.md": "Consejo de Zenón Quiroga (@zenonquiroga).\n"}

    def test_publica_sin_la_ficha_alto_y_con_la_publica(self):
        destino = os.path.join(self.tmp, "_arbol")
        self.assertEqual(self.p.publicar(destino), 0)
        personas = os.listdir(os.path.join(destino, "tools", "config", "personas"))
        self.assertEqual(personas, ["gala.json"])
        with io.open(os.path.join(destino, "tools", "nota.md"), encoding="utf-8") as fh:
            nota = fh.read()
        self.assertNotIn("Quiroga", nota)
        self.assertNotIn("zenonquiroga", nota)


class TestVetoConservaElGit(ConRepo):
    extra = {"tools/nota.md": "Llama al 611 22 33 44\n"}

    def test_un_veto_vacia_el_arbol_pero_no_borra_el_git_del_espejo(self):
        destino = os.path.join(self.tmp, "_arbol")
        os.makedirs(os.path.join(destino, ".git"))
        with io.open(os.path.join(destino, ".git", "HEAD"), "w") as fh:
            fh.write("ref: refs/heads/master\n")
        self.assertEqual(self.p.publicar(destino, forzar=True), 1)
        self.assertEqual(os.listdir(destino), [".git"])


class TestFichaSinNivel(ConRepo):
    fichas = {"zenon.json": dict(FICHA_ALTO, anonimato=None), "rota.json": "{no es json"}

    def test_ficha_sin_anonimato_bloquea_la_publicacion(self):
        faltan = self.p._overlay_ok()
        self.assertTrue(any("zenon.json" in f for f in faltan))
        self.assertTrue(any("rota.json" in f for f in faltan))
        self.assertEqual(self.p.publicar(os.path.join(self.tmp, "_arbol")), 3)


class TestColision(ConRepo):
    # `zenobia.md` (nombre de la deny-list) se renombra a `contacto.md`, que ya existe.
    fichas = {"gala.json": FICHA_PUBLICA}
    extra = {"tools/zenobia.md": "uno\n", "tools/contacto.md": "dos\n"}

    def test_dos_origenes_a_la_misma_ruta_bloquean(self):
        entran = [r for r in self.p.versionados() if self.p._entra(r)]
        self.assertEqual(self.p._colisiones(entran), [("tools/contacto.md", 2)])
        self.assertEqual(self.p.publicar(os.path.join(self.tmp, "_arbol")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
