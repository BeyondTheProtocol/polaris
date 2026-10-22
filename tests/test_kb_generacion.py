#!/usr/bin/env python3
"""test_kb_generacion.py — el RAG nunca sirve vectores de otra generación (issue #17).

POR QUÉ EXISTE (auditoría externa 22-sep-2026, punto 4.5). `build()` hacía el swap del .db,
soltaba el lock y DESPUÉS calculaba los vectores. En ese hueco —minutos sobre el corpus real—
el lector casaba la fila i del .npy viejo con el rowid i+1 del .db nuevo, y devolvía un pasaje
con el coseno de otro texto. Se fija aquí:

  1. durante un reindexado se sirve la generación anterior ENTERA (.db y vectores);
  2. el manifiesto lleva el id de generación y un hash por fragmento;
  3. vectores de otra generación no se sirven aunque estén en la ruta buena: se busca por texto;
  4. un .db sin manifiesto (anterior a esto) busca por texto, no con un .npy suelto;
  5. si los embeddings fallan, la generación sale solo-texto y el índice se publica igual;
  6. las generaciones viejas se podan (se conservan la actual y la anterior).

No necesita onnxruntime ni el modelo: `kb.kb_embed` se sustituye por un doble determinista, así
que corre también en el CI público. Lo que se prueba es el emparejamiento .db ↔ vectores, no la
calidad semántica.
"""
import glob
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import kb  # noqa: E402

try:
    import numpy as np
except ImportError:   # sin numpy no hay capa vectorial que probar
    print("SKIP: numpy no está en este intérprete")
    sys.exit(77)

DIM = 16
# Una consulta sin ningún término en el corpus: el brazo BM25 no aporta nada y lo que devuelve
# retrieve() sale SOLO del brazo vectorial. El doble la embebe igual que ALFA.
CONSULTA = "zzqqxx"
ALFA = "# Alfa\n\nPasaje alfa sobre el primer tema de prueba, con texto suficiente."
BETA = "# Beta\n\nPasaje beta sobre el segundo tema de prueba, con texto suficiente."
GAMMA = "# Gamma\n\nPasaje gamma sobre el tercer tema de prueba, con texto suficiente."
DELTA = "# Delta\n\nPasaje delta sobre el cuarto tema de prueba, con texto suficiente."


def _vec(texto):
    h = hashlib.sha256(texto.strip().encode("utf-8")).digest()
    v = np.random.default_rng(int.from_bytes(h[:8], "big")).standard_normal(DIM).astype("float32")
    return v / np.linalg.norm(v)


class _EmbedDoble:
    """Sustituto de kb_embed: vectores deterministas por hash y un gancho que se dispara al
    embeber pasajes, que es justo el momento en que el build viejo ya había publicado el .db."""
    DIM = DIM

    def __init__(self):
        self.gancho = None
        self.falla = False

    def disponible(self):
        return True

    def embed_texts(self, texts, prefix="passage: "):
        if self.falla:
            raise RuntimeError("fallo simulado del modelo")
        if prefix == "passage: " and self.gancho:
            g, self.gancho = self.gancho, None
            g()
        return np.stack([_vec(t) for t in texts])

    def embed_query(self, text):
        return _vec(ALFA if text == CONSULTA else text)


class Generaciones(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kbgen-")
        self.fv = os.path.join(self.dir, "00_FUENTE-DE-VERDAD")
        os.makedirs(self.fv)
        self._orig = (kb.FV, kb.DB, kb.VEC, kb.kb_embed)
        kb.FV = self.fv
        kb.DB = os.path.join(self.fv, ".kb_index.db")
        kb.VEC = os.path.join(self.fv, ".kb_index.vectors.npy")
        self.embed = kb.kb_embed = _EmbedDoble()

    def tearDown(self):
        kb.FV, kb.DB, kb.VEC, kb.kb_embed = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def _corpus(self, *textos):
        for f in glob.glob(os.path.join(self.fv, "*.md")):
            os.remove(f)
        # El nombre fija el orden de build() (sorted) y por tanto el rowid de cada pasaje.
        for i, t in enumerate(textos):
            with open(os.path.join(self.fv, "%02d.md" % i), "w", encoding="utf-8") as f:
                f.write(t + "\n")

    def _npy(self):
        # os.listdir y no glob("*.npy"): glob se salta los ficheros que empiezan por punto, y
        # todos los del índice empiezan por punto — el test miraría una carpeta vacía.
        return sorted(f for f in os.listdir(self.fv) if f.endswith(".npy"))

    def _build(self):
        with redirect_stdout(io.StringIO()):
            kb.build()

    def _vectorial(self):
        """Lo que devuelve retrieve() cuando SOLO habla el brazo vectorial."""
        return [body.strip() for (_p, _t, body, _s) in kb.retrieve(CONSULTA, k=1, scope="all")]

    def _manifiesto(self):
        con = sqlite3.connect(kb.DB)
        try:
            return dict(con.execute("SELECT clave, valor FROM kb_manifiesto"))
        finally:
            con.close()

    def test_durante_el_reindexado_se_sirve_la_generacion_anterior_entera(self):
        self._corpus(ALFA, BETA, GAMMA)
        self._build()
        gen_a = self._manifiesto()["generacion"]
        self.assertEqual(self._vectorial(), [ALFA.strip()])

        # La generación B pone ALFA en otra fila: con el .db nuevo y los vectores viejos, la fila
        # donde estaba ALFA (rowid 1) ahora es DELTA. Eso es exactamente lo que servía el bug.
        self._corpus(DELTA, ALFA, BETA)
        visto = {}

        def en_la_ventana():
            visto["gen"] = kb._generacion_de(kb.DB)
            visto["res"] = self._vectorial()

        self.embed.gancho = en_la_ventana
        self._build()
        self.assertIn("res", visto, "el gancho no se disparó: el test no ha mirado la ventana")
        self.assertNotEqual(visto["res"], [DELTA.strip()],
                            "en la ventana se sirvió el pasaje de otra generación")
        self.assertEqual(visto["res"], [ALFA.strip()],
                         "en la ventana tenía que servirse la generación anterior, entera")
        self.assertEqual(visto["gen"], gen_a)
        # Y al terminar, la nueva, también entera.
        self.assertNotEqual(self._manifiesto()["generacion"], gen_a)
        self.assertEqual(self._vectorial(), [ALFA.strip()])

    def test_el_manifiesto_lleva_generacion_y_hash_por_fragmento(self):
        self._corpus(ALFA, BETA, GAMMA)
        self._build()
        man = self._manifiesto()
        self.assertRegex(man["generacion"], kb._RE_GENERACION)
        self.assertEqual(man["vectores"], "si")
        self.assertEqual(man["dim"], str(DIM))
        con = sqlite3.connect(kb.DB)
        filas = con.execute("SELECT rowid, body FROM chunks ORDER BY rowid").fetchall()
        con.close()
        self.assertEqual(int(man["fragmentos"]), len(filas))
        vec_path, sha_path = kb._rutas_generacion(kb.DB, man["generacion"])
        hashes = np.load(sha_path)
        self.assertEqual(hashes.shape, (len(filas), 16))
        mat = np.load(vec_path)
        self.assertEqual(mat.shape, (len(filas), DIM))
        for rowid, body in filas:   # la huella es del texto Y de su vector, fila a fila
            self.assertEqual(hashes[rowid - 1].tobytes(), hashlib.sha256(
                body.encode("utf-8") + b"\0" + mat[rowid - 1].tobytes()).digest()[:16])

    def test_vectores_de_otra_generacion_no_se_sirven(self):
        self._corpus(ALFA, BETA, GAMMA)
        self._build()
        viejos = kb._rutas_generacion(kb.DB, self._manifiesto()["generacion"])
        guardados = [p + ".copia" for p in viejos]
        for p, c in zip(viejos, guardados):
            shutil.copy(p, c)
        self._corpus(DELTA, ALFA, BETA)
        self._build()
        # Alguien (una copia a mano, un build a medias) deja los vectores de A con el nombre de B.
        for c, p in zip(guardados, kb._rutas_generacion(kb.DB, self._manifiesto()["generacion"])):
            shutil.copy(c, p)
        self.assertEqual(self._vectorial(), [],
                         "vectores de otra generación: tenía que caer a búsqueda por texto")
        # Solo el .npy cambiado (los hashes sí son de esta generación): la huella lo caza igual.
        self._build()
        vec_b = kb._rutas_generacion(kb.DB, self._manifiesto()["generacion"])[0]
        shutil.copy(guardados[0], vec_b)
        self.assertEqual(self._vectorial(), [])
        # Y la búsqueda por texto sigue viva.
        self.assertTrue(kb.retrieve("delta cuarto tema", k=1, scope="all"))

    def test_un_db_sin_manifiesto_busca_solo_por_texto(self):
        con = sqlite3.connect(kb.DB)
        con.execute(kb._SCHEMA)
        con.executemany("INSERT INTO chunks(path, title, sensitivity, body) VALUES (?,?,?,?)",
                        [("%d.md" % i, "t", "internal", t) for i, t in enumerate((DELTA, ALFA))])
        con.commit()
        con.close()
        np.save(kb.VEC, np.stack([_vec(ALFA), _vec(DELTA)]))   # .npy legado, desalineado
        self.assertEqual(self._vectorial(), [])
        self.assertTrue(kb.retrieve("delta cuarto tema", k=1, scope="all"))

    def test_si_fallan_los_embeddings_se_publica_solo_texto(self):
        self._corpus(ALFA, BETA)
        self.embed.falla = True
        self._build()
        man = self._manifiesto()
        self.assertEqual(man["vectores"], "no")
        self.assertEqual(self._npy(), [])
        self.assertEqual(self._vectorial(), [])
        self.assertTrue(kb.retrieve("alfa primer tema", k=1, scope="all"))

    def test_se_podan_las_generaciones_viejas(self):
        np.save(kb.VEC, np.zeros((1, DIM), "float32"))   # .npy legado de antes del cambio
        self.assertIn(os.path.basename(kb.VEC), self._npy())
        gens = []
        for textos in ((ALFA,), (ALFA, BETA), (ALFA, BETA, GAMMA)):
            self._corpus(*textos)
            self._build()
            gens.append(self._manifiesto()["generacion"])
        esperado = sorted(os.path.basename(p) for g in gens[1:] for p in kb._rutas_generacion(kb.DB, g))
        self.assertEqual(self._npy(), esperado, "debían quedar solo la generación actual y la anterior")


if __name__ == "__main__":
    unittest.main(verbosity=2)
