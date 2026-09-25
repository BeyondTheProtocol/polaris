#!/usr/bin/env python3
"""test_x_guardados_enriquecido.py — un guardado SIN texto no es un guardado SIN contenido.

Qué se protege aquí (25-jul-2026): la cosecha de guardados tiene que traerse el contenido REAL
de cada uno —cuerpo del artículo largo, texto sin truncar, enlaces expandidos, post citado— y
etiquetar con TODO eso, no con los 280 caracteres visibles.

El fallo que lo motiva: 25 guardados llegaban como «(solo link)» porque `xurl bookmarks` devuelve
`text` truncado y sin `article`/`note_tweet`. Dentro había artículos de miles de palabras, hilos
completos y hasta un informe entero, y llevaban dos meses descartados por «ruido». Era un fallo de
CAPTURA, no de criterio.

Y la otra mitad, igual de importante: si la lectura del contenido FALLA, la cosecha no se cae y
queda marcado `enriquecido: False` — «no pude leerlo» nunca se confunde con «no había nada».
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
import io

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import x_guardados  # noqa: E402
import _xurl  # noqa: E402

# Un guardado «solo-enlace» real (el caso que se perdía) y lo que la API sí sabe de él.
BOOKMARKS = [{
    "id": "999", "author_id": "42", "author_username": "alguien", "author_name": "Alguien",
    "text": "https://t.co/xxxx", "created_at": "2026-07-25T10:00:00.000Z",
    "url": "https://x.com/alguien/status/999",
}]
RICOS = {"999": {
    "cuerpo": "Loop Engineering: un roadmap tecnico para un lazo autonomo. Paso 0: solo montes "
              "un loop si existe un check independiente del agente.",
    "articulo_titulo": "Loop Engineering",
    "enlaces": [{"url": "https://github.com/ejemplo/repo", "titulo": "GitHub - ejemplo/repo"}],
    "cita_id": "888", "cita_texto": "el post citado, completo y sin truncar",
}}


class Enriquecido(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = (x_guardados.OUT_DIR, x_guardados.STORE_FILE, x_guardados.SEEN_FILE,
                      _xurl.bookmarks, _xurl.posts_ricos)
        x_guardados.OUT_DIR = self.tmp
        x_guardados.STORE_FILE = os.path.join(self.tmp, "guardados.jsonl")
        x_guardados.SEEN_FILE = os.path.join(self.tmp, "seen.json")
        _xurl.bookmarks = lambda max_results=10: list(BOOKMARKS)

    def tearDown(self):
        (x_guardados.OUT_DIR, x_guardados.STORE_FILE, x_guardados.SEEN_FILE,
         _xurl.bookmarks, _xurl.posts_ricos) = self._orig

    def _cosechar(self):
        with redirect_stdout(io.StringIO()):
            rc = x_guardados.cmd_fetch(notify=False, show_all=True, max_results=10)
        self.assertEqual(rc, 0)
        return [json.loads(l) for l in open(x_guardados.STORE_FILE, encoding="utf-8") if l.strip()][0]

    def test_guarda_el_contenido_de_detras_del_enlace(self):
        _xurl.posts_ricos = lambda ids, **kw: dict(RICOS)
        rec = self._cosechar()
        self.assertTrue(rec["enriquecido"])
        self.assertEqual(rec["articulo_titulo"], "Loop Engineering")
        self.assertIn("check independiente del agente", rec["cuerpo"])
        self.assertEqual(rec["cita_texto"], "el post citado, completo y sin truncar")
        self.assertEqual(rec["enlaces"][0]["url"], "https://github.com/ejemplo/repo")

    def test_etiqueta_con_el_contenido_no_con_los_280_caracteres(self):
        """El tuit visible no tiene ni una palabra clasificable: el tag sale del cuerpo."""
        _xurl.posts_ricos = lambda ids, **kw: dict(RICOS)
        self.assertEqual(x_guardados._flag(BOOKMARKS[0]["text"]), [],
                         "precondición: el texto visible no da ningún tag")
        rec = self._cosechar()
        self.assertIn("sistema", rec["tags"],
                      "un guardado solo-enlace sobre el lazo tiene que salir etiquetado como sistema")

    def test_si_falla_la_lectura_no_se_cae_y_lo_dice(self):
        def explota(ids, **kw):
            raise _xurl.XurlError("token caducado")
        _xurl.posts_ricos = explota
        rec = self._cosechar()
        self.assertIs(rec["enriquecido"], False,
                      "«no pude leerlo» tiene que quedar marcado, no pasar por «sin contenido»")
        self.assertNotIn("cuerpo", rec)

    def test_el_digest_muestra_lo_de_detras_del_enlace(self):
        _xurl.posts_ricos = lambda ids, **kw: dict(RICOS)
        self._cosechar()
        buf = io.StringIO()
        with redirect_stdout(buf):
            x_guardados.cmd_listar(None)
        salida = buf.getvalue()
        self.assertIn("Loop Engineering", salida)
        self.assertIn("check independiente del agente", salida)
        self.assertIn("github.com/ejemplo/repo", salida)


if __name__ == "__main__":
    r = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(Enriquecido))
    if r.wasSuccessful():
        print("✅ X_GUARDADOS ENRIQUECIDO EN VERDE (%d ok / 0 fallos)" % r.testsRun)
    sys.exit(0 if r.wasSuccessful() else 1)
