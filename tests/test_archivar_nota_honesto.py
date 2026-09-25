#!/usr/bin/env python3
"""tests/test_archivar_nota_honesto.py — que archivar_nota NO diga «RAG reindexado» si no lo está.

El fallo que lo motiva (detectado 5 veces antes de cerrarse, 20-sep-2026): `kb.build()` NO lanza
excepción cuando se NIEGA a indexar — devuelve 1 y lo explica por stderr, para no reemplazar un
índice bueno por uno sin los PDFs del historial. Nadie miraba ese retorno, así que el tool
imprimía «✓ archivado … (RAG reindexado)» sobre un índice intacto. La nota quedaba fuera del RAG
y el sistema creía lo contrario. Eso es mentir, que es lo peor que puede hacer el sistema
(CLAUDE.md §El muro, mantra nº 1).

Dos mentiras posibles, y el test cubre las dos direcciones:
  · decir que reindexó cuando no (la grave);
  · decir que NO reindexó cuando sí (pasaba al comparar rutas en dialectos distintos: aquí la
    ruta cuelga del repo, en el índice cuelga de la fuente de verdad).

Sin red y sin tocar el índice real: kb se sustituye por un doble.
"""
import datetime
import os
import sqlite3
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "tools"))

import archivar_nota as an  # noqa: E402

FALLOS, OK = [], []


def ok(caso, cond, detalle=""):
    (OK if cond else FALLOS).append(caso)
    print(("  ok  " if cond else "  FALLO  ") + caso + ("" if cond else f" {detalle}"))


class KbFalso:
    """Doble de kb: DB propia y un build() cuyo código de salida se elige en cada caso."""

    def __init__(self, db, rc=None, indexa=None):
        self.DB, self._rc, self._indexa = db, rc, indexa

    def build(self):
        if self._indexa:
            con = sqlite3.connect(self.DB)
            con.execute("CREATE TABLE IF NOT EXISTS chunks (path, title, sensitivity, body)")
            con.execute("INSERT INTO chunks VALUES (?,?,?,?)", (self._indexa, "t", "public", "b"))
            con.commit()
            con.close()
        return self._rc


tmp = tempfile.mkdtemp(prefix="archnota-")
FV_TMP = os.path.join(tmp, "00_FUENTE-DE-VERDAD")
os.makedirs(FV_TMP)
an.FV = FV_TMP
an.ROOT = tmp

# --- 1. _en_el_indice: los dos dialectos de ruta y el ausente ---------------------------------
db = os.path.join(tmp, "i.db")
con = sqlite3.connect(db)
con.execute("CREATE TABLE chunks (path, title, sensitivity, body)")
con.execute("INSERT INTO chunks VALUES (?,?,?,?)", ("04 · IA/Notas/x.md", "t", "public", "b"))
con.commit()
con.close()
kb = KbFalso(db)

ok("encuentra la nota aunque el índice guarde la ruta SIN el prefijo de la fuente de verdad",
   an._en_el_indice(kb, "00_FUENTE-DE-VERDAD/04 · IA/Notas/x.md"))
ok("encuentra la nota con la ruta tal cual está en el índice",
   an._en_el_indice(kb, "04 · IA/Notas/x.md"))
ok("una nota que NO está en el índice da False",
   not an._en_el_indice(kb, "00_FUENTE-DE-VERDAD/04 · IA/Notas/no-existe.md"))
ok("sin fichero de índice da False, no revienta",
   not an._en_el_indice(KbFalso(os.path.join(tmp, "no-hay.db")), "04 · IA/Notas/x.md"))


def archiva_con(kb_doble, titulo):
    sys.modules["kb"] = kb_doble
    try:
        return an.archivar(titulo, "cuerpo de prueba", tema="04 · IA/Notas", reindex=True)
    finally:
        sys.modules.pop("kb", None)


# --- 2. La mentira grave: build() se niega (rc=1) y el tool NO puede decir que reindexó -------
_r, _rel, reidx = archiva_con(KbFalso(os.path.join(tmp, "vacia.db"), rc=1), "Nota con kb negado")
ok("kb.build() devuelve 1 (se negó) -> NO se declara reindexado", reidx is False, f"-> {reidx}")

# --- 3. build() dice que sí pero la nota no entró: tampoco se declara reindexado --------------
db2 = os.path.join(tmp, "j.db")
con = sqlite3.connect(db2)
con.execute("CREATE TABLE chunks (path, title, sensitivity, body)")
con.commit()
con.close()
_r, _rel, reidx2 = archiva_con(KbFalso(db2, rc=None), "Nota que no entra al indice")
ok("build() sin error pero la nota NO está en el índice -> NO se declara reindexado",
   reidx2 is False, f"-> {reidx2}")

# --- 4. El camino bueno sí se reconoce (no vale mentir al revés) -------------------------------
db3 = os.path.join(tmp, "k.db")
_r, rel3, reidx3 = archiva_con(
    # La fecha, la misma que pone archivar_nota.py (date.today()). Estaba escrita a mano
    # («2026-09-20»): pasó el día que se escribió y se puso ROJO a medianoche, en casa base
    # también, sin que nada del código hubiera cambiado. Un test no puede depender del reloj.
    KbFalso(db3, rc=None,
            indexa="04 · IA/Notas/nota-buena-%s.md" % datetime.date.today().isoformat()),
    "Nota buena")
ok("build() ok y la nota SÍ está en el índice -> se declara reindexado", reidx3 is True,
   f"-> {reidx3} (rel={rel3})")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_archivar_nota_honesto: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
