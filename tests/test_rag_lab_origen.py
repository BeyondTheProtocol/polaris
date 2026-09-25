#!/usr/bin/env python3
"""test_rag_lab_origen.py — una analítica activa de RAG_md no puede ser una transcripción a mano
si existe el PDF: se extrae del PDF (lector_clinico.py --texto).

POR QUÉ EXISTE (25-sep-2026). Las analíticas HUVH del 19-ago, 26-ago y 8-sep estaban en RAG_md
como «transcrito §A6 puesta-al-dia-11-sep»: una tabla copiada a mano que se comía filas enteras
(ALT 44 y 38) y rangos (AST, ALT, CEA). Lo encontró la revisión del PR 223 cotejando con los PDF;
kb.py, los dossiers y la web se apoyaban en esas transcripciones y daban por ausente un dato que
existía. Se re-extrajeron del PDF y las viejas quedaron como `.sustituido-25sep` (no se indexan).

Regla que fija: un .md activo de laboratorio cuyo nombre u `origin:` diga «transcri…» tiene que
declarar `sin_pdf: true` en el frontmatter (no hay PDF del que extraer). Si no, falla.

Dos partes: (1) la lógica sobre un RAG_md de prueba en un tmp (corre siempre, también en CI);
(2) el RAG_md real, que solo existe en casa base (gitignored): si no está, esa parte se salta.
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("BTP_REPO") or ROOT
RAG_MD_REAL = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "00 · Bandeja de entrada", "RAG_md")

_LAB = re.compile(r" - Lab - ", re.I)
_TRANSCRI = re.compile(r"transcri", re.I)
_DEL_PDF = re.compile(r"extracci[oó]n\b.*\bPDF|capa de texto del PDF", re.I)


def _frontmatter(texto):
    m = re.match(r"^---\n(.*?)\n---", texto, re.S)
    return m.group(1) if m else ""


def transcripciones_sin_pdf_declarado(dir_rag):
    """Labs activos (.md) que parecen transcritos a mano y no declaran `sin_pdf: true`."""
    malos = []
    for nombre in sorted(os.listdir(dir_rag)):
        if not nombre.endswith(".md") or not _LAB.search(nombre):
            continue
        with open(os.path.join(dir_rag, nombre), encoding="utf-8") as f:
            fm = _frontmatter(f.read(4000))
        origin = next((l for l in fm.splitlines() if l.lower().startswith("origin:")), "")
        if not (_TRANSCRI.search(nombre) or _TRANSCRI.search(origin)):
            continue
        # Un origen sacado del PDF puede NOMBRAR la transcripción a la que sustituye
        # («Extracción mecánica del PDF… Sustituye a la transcripción…»): eso no es transcribir.
        if not _TRANSCRI.search(nombre) and _DEL_PDF.search(origin):
            continue
        if re.search(r"^sin_pdf:\s*true\s*$", fm, re.M | re.I):
            continue
        malos.append(nombre)
    return malos


def _escribe(d, nombre, fm):
    with open(os.path.join(d, nombre), "w", encoding="utf-8") as f:
        f.write(f"---\n{fm}\n---\nALT  44  U/L  10 - 49\n")


class LogicaTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="rag_lab_origen_")

    def test_transcrito_sin_declarar_falla(self):
        _escribe(self.d, "2026-08-19 - Lab - Analítica - HUVH (transcrito §A6).md",
                 'date: "2026-08-19"\norigin: "tabla §A6"')
        self.assertEqual(len(transcripciones_sin_pdf_declarado(self.d)), 1)

    def test_origin_transcrito_sin_declarar_falla(self):
        _escribe(self.d, "2026-08-26 - Lab - Analítica - HUVH.md",
                 'date: "2026-08-26"\norigin: "Transcripción manual del informe en papel"')
        self.assertEqual(len(transcripciones_sin_pdf_declarado(self.d)), 1)

    def test_transcrito_con_sin_pdf_pasa(self):
        _escribe(self.d, "2021-01-01 - Lab - Analítica - papel (transcrito).md",
                 'date: "2021-01-01"\nsin_pdf: true')
        self.assertEqual(transcripciones_sin_pdf_declarado(self.d), [])

    def test_extraido_del_pdf_pasa_y_sustituido_no_cuenta(self):
        _escribe(self.d, "2026-08-19 - Lab - Analítica - HUVH (extraído del PDF 25-sep).md",
                 'date: "2026-08-19"\norigin: "Extracción mecánica del PDF original. '
                 'Sustituye a la transcripción de la tabla §A6"')
        with open(os.path.join(self.d, "2026-08-19 - Lab - HUVH (transcrito).md.sustituido-25sep"), "w") as f:
            f.write("---\norigin: transcrito\n---\n")
        self.assertEqual(transcripciones_sin_pdf_declarado(self.d), [])


class RagRealTest(unittest.TestCase):
    def test_rag_md_real_sin_transcripciones_a_mano(self):
        if not os.path.isdir(RAG_MD_REAL):
            self.skipTest("RAG_md no está (solo casa base)")
        malos = transcripciones_sin_pdf_declarado(RAG_MD_REAL)
        self.assertEqual(malos, [], "Analíticas activas transcritas a mano sin `sin_pdf: true` "
                         "(extráelas del PDF con lector_clinico.py --texto): " + "; ".join(malos))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    fallos = len(r.failures) + len(r.errors)
    print(f"test_rag_lab_origen: {r.testsRun - fallos} OK, {fallos} fallos"
          + (f", {len(r.skipped)} saltado(s)" if r.skipped else ""))
    sys.exit(1 if fallos else 0)
