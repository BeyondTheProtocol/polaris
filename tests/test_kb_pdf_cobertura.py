#!/usr/bin/env python3
"""Issue #18: PDFs reales sintéticos; extracción, persistencia y consulta end-to-end."""
import contextlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import kb
try:
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
except ImportError:
    print("SKIP: cobertura PDF necesita pypdf (dependencia opcional de kb).")
    sys.exit(77)


def pdf(path, pages, blank=()):
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    for number in range(1, pages + 1):
        page = writer.add_blank_page(width=400, height=400)
        if number in blank:
            continue
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data((f'BT /F1 12 Tf 10 300 Td (marcapagina{number} '
                         'Documento sintetico para comprobar cobertura.) Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = stream
    with open(path, 'wb') as out:
        writer.write(out)


class CoberturaPdf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kb_cobertura_')
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        for name, value in dict(FV=root, DB=root+'/.kb_index.db',
                                IDX=root+'/.kb_index.json', VEC=root+'/.kb_index.vectors.npy').items():
            p = patch.object(kb, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(kb.kb_embed, 'disponible', return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.path = Path(root) / '_PUBLICO_documento.pdf'

    def build(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            kb.build()
        return out.getvalue()

    def metadata(self, path=None):
        with sqlite3.connect(kb.DB) as con:
            return con.execute('SELECT paginas_total, paginas_leidas, paginas_vacias, '
                               'ocr_aplicado, truncado, error_extraccion FROM pdf_cobertura '
                               'WHERE path=?', (path or self.path.name,)).fetchone()

    def ask(self, query, scope='public'):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            kb.ask(query, scope=scope)
        return out.getvalue()

    def test_long_pdf_real_extraction_and_warning(self):
        pdf(self.path, 100, blank=(2, 80, 90))
        output = self.build()
        self.assertEqual(self.metadata(), (100, 80, 2, 0, 1, 0))
        self.assertIn('1 truncados', output)
        self.assertIn('2 páginas leídas sin texto', output)
        self.assertIn('OCR no aplicado', output)
        result = kb.retrieve('marcapagina79', scope='public')
        self.assertTrue(result)
        self.assertEqual(len(result[0]), 4)  # API histórica
        self.assertIn('PDF truncado: leídas 80 de 100 páginas', result[0][1])
        self.assertIn('PDF truncado', self.ask('marcapagina79'))
        self.assertEqual(kb.retrieve('marcapagina81', scope='public'), [])
        # El aviso no se convierte en evidencia consultable.
        self.assertEqual(kb.retrieve('truncado', scope='public'), [])
        self.assertNotIn('PDF truncado', result[0][2])

    def test_vector_only_result_also_warns(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('numpy no disponible para probar recuperación vectorial')
        pdf(self.path, 81)
        embedded = []

        def embed(texts, prefix="passage: "):
            embedded.extend(texts)
            return np.ones((len(texts), 2), dtype="float32") / np.sqrt(2)

        with patch.object(kb.kb_embed, 'disponible', return_value=True), \
                patch.object(kb.kb_embed, 'embed_texts', side_effect=embed), \
                patch.object(kb.kb_embed, 'embed_query', return_value=np.ones(2, dtype="float32") / np.sqrt(2)):
            self.build()
            # Sin coincidencia léxica: el pasaje llega exclusivamente por vectores.
            self.assertIn('PDF truncado', self.ask('zzconsultaunica'))
        self.assertTrue(embedded)
        self.assertFalse(any('PDF truncado' in text for text in embedded))

    def test_boundary_and_reindex_clear_warning(self):
        for pages in (81, 80, 79, 81):
            with self.subTest(pages=pages):
                pdf(self.path, pages)
                self.build()
                self.assertEqual(self.metadata(), (pages, min(pages, 80), 0, 0, int(pages > 80), 0))
                self.assertEqual('PDF truncado' in self.ask('marcapagina1'), pages > 80)

    def test_no_text_still_has_coverage(self):
        pdf(self.path, 81, blank=range(1, 82))
        self.assertIn('1 truncados', self.build())
        self.assertEqual(self.metadata(), (81, 80, 80, 0, 1, 0))
        with sqlite3.connect(kb.DB) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0], 0)

    def test_broken_pdf_is_not_complete(self):
        self.path.write_bytes(b'not a PDF')
        self.build()
        self.assertEqual(self.metadata(), (None, 0, 0, 0, 0, 1))

    def test_scope_and_unrelated_passages(self):
        private = Path(self.tmp.name) / '_PRIVADO_largo.pdf'
        pdf(private, 81)
        Path(self.tmp.name, '_PUBLICO_nota.md').write_text('Nota sin truncamiento: farolillo y texto sintetico suficiente.')
        self.build()
        self.assertNotIn(private.name, self.ask('marcapagina1'))
        self.assertNotIn('PDF truncado', self.ask('marcapagina1'))
        self.assertIn('PDF truncado', self.ask('marcapagina1', scope='all'))
        self.assertNotIn('PDF truncado', self.ask('farolillo'))

    def test_passage_keeps_warning_across_database_swap(self):
        pdf(self.path, 81)
        self.build()
        retrieved = kb.retrieve('marcapagina1', scope='public')
        pdf(self.path, 1)
        self.build()
        # Una consulta que ya obtuvo sus pasajes conserva SU cobertura al publicarse otro índice.
        with patch.object(kb, 'retrieve', return_value=retrieved):
            self.assertIn('PDF truncado: leídas 80 de 81 páginas', self.ask('marcapagina1'))
        self.assertNotIn('PDF truncado', self.ask('marcapagina1'))


if __name__ == '__main__':
    unittest.main()
