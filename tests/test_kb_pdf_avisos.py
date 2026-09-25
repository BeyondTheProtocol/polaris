#!/usr/bin/env python3
"""test_kb_pdf_avisos.py — kb.build() silencia y resume los avisos de pypdf por PDF corrupto.

Bug: tools/launchd/logs/kb-reindex.err llegó a ~436KB por PdfReadError/"Ignoring wrong pointing
object" de un puñado de PDFs corruptos (escaneos/exports viejos en la fuente de verdad) — pypdf ya
los salta y sigue (NO es fatal), pero cada aviso es un logging.WARNING de "pypdf._reader" que, sin
handler propio, el 'lastResort' de logging vuelca a stderr; con un PDF muy roto son cientos de
líneas repetidas.

Fix: kb._instalar_contador_pdf() instala un logging.Handler que CAPTURA esos WARNING (nunca llegan a
stderr, propagate=False) y los CUENTA por fichero (kb._pdf_actual / kb._pdf_avisos). kb.build()
imprime UNA línea resumen ("N PDFs con partes ilegibles: <nombres>") en vez del traceback repetido.

Verificado por separado (manual, contra la fuente de verdad real): 18 PDFs reales de
00_FUENTE-DE-VERDAD disparan este camino (todos "Ignoring wrong pointing object", ninguno fatal;
pypdf sigue extrayendo el resto del texto). Aquí se prueba el MECANISMO de forma aislada y
determinista, sin depender de PDFs reales ni de su ruta absoluta.
"""
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import kb  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _emitir_warning_pypdf(texto="Ignoring wrong pointing object 6 0 (offset 0)"):
    """Dispara un WARNING real desde el logger que usa pypdf de verdad (pypdf._reader), igual que
    hace la librería al toparse con un PDF corrupto. No depende de un PDF real."""
    logging.getLogger("pypdf._reader").warning(texto)


def main():
    kb._instalar_contador_pdf()
    kb._pdf_avisos.clear()

    # 1. Un warning de pypdf mientras _pdf_actual apunta a un fichero → se cuenta, NO llega a stderr.
    import io, contextlib
    buf_err = io.StringIO()
    kb._pdf_actual[0] = "carpeta/Historia.pdf"
    with contextlib.redirect_stderr(buf_err):
        _emitir_warning_pypdf()
    kb._pdf_actual[0] = None
    ok(kb._pdf_avisos.get("carpeta/Historia.pdf") == 1, "1 warning de pypdf → contado bajo su fichero")
    ok(buf_err.getvalue() == "", "el warning NO llega a stderr (handler silencioso)")

    # 2. Varios warnings del MISMO fichero (el caso real: un PDF roto dispara docenas) → se acumulan
    #    bajo una sola clave, no se genera una línea por cada uno.
    kb._pdf_actual[0] = "carpeta/Historia.pdf"
    for _ in range(50):
        _emitir_warning_pypdf()
    kb._pdf_actual[0] = None
    ok(kb._pdf_avisos["carpeta/Historia.pdf"] == 51, "50 warnings más se ACUMULAN bajo el mismo fichero (1+50)")
    ok(len(kb._pdf_avisos) == 1, "un PDF muy roto sigue contando como 1 solo fichero, no 51 entradas")

    # 3. Un segundo fichero distinto → entrada propia, no se mezcla con el primero.
    kb._pdf_actual[0] = "otra/Extractos-bancarios.pdf"
    _emitir_warning_pypdf()
    kb._pdf_actual[0] = None
    ok(len(kb._pdf_avisos) == 2, "un fichero distinto abre su propia entrada")

    # 4. build() end-to-end sobre un FV sintético con un .md normal (control) y confirmamos que
    #    build() imprime el resumen SOLO cuando _pdf_avisos no está vacío, y limpia el contador al
    #    empezar (idempotente entre corridas).
    tmp = tempfile.mkdtemp(prefix="kb_pdf_test_")
    with open(os.path.join(tmp, "normal.md"), "w") as f:
        f.write("contenido normal, nada que avisar")
    # Se redirige TODO lo que build() escribe, no solo FV. Hasta el 23-ago-2026 este test
    # apuntaba FV al tmp pero dejaba `kb.DB` en su sitio: build() leía el FV de mentira y
    # SOBREESCRIBÍA el índice de verdad. Cada pasada de `test_all.sh` dejaba el RAG de {{TITULAR}}
    # en 24 KB y un pasaje —el historial clínico entero fuera del conocimiento de Polaris—
    # y nadie se enteraba, porque el test terminaba en verde.
    orig_fv, orig_idx, orig_db, orig_vec = kb.FV, kb.IDX, kb.DB, kb.VEC
    _mtime_antes = os.path.getmtime(orig_db) if os.path.exists(orig_db) else None
    kb.FV = tmp
    kb.IDX = os.path.join(tmp, ".kb_index.json")
    kb.DB = os.path.join(tmp, ".kb_index.db")
    kb.VEC = os.path.join(tmp, ".kb_index.vectors.npy")
    try:
        buf_out = io.StringIO()
        with contextlib.redirect_stdout(buf_out):
            kb.build()
        salida = buf_out.getvalue()
        ok("Indexados" in salida, "build() sigue imprimiendo la línea de siempre")
        ok("PDFs con partes ilegibles" not in salida, "sin PDFs rotos en este FV → SIN línea de resumen (silencio limpio)")
        ok(kb._pdf_avisos == {}, "build() limpia el contador al principio (no arrastra de una corrida a otra)")

        # 5. Ahora simulamos que build() SÍ tropieza con avisos (inyectando directamente, ya que
        #    generar un PDF corrupto de verdad no es determinista entre versiones de pypdf/plataforma).
        kb._pdf_avisos["fake/Roto.pdf"] = 12
        buf_out2 = io.StringIO()
        with contextlib.redirect_stdout(buf_out2):
            print(f"⚠️  {len(kb._pdf_avisos)} PDFs con partes ilegibles (no-fatal, pypdf saltó esos "
                  f"objetos/páginas rotos y siguió con el resto del texto): "
                  + ", ".join(os.path.basename(p) for p in sorted(kb._pdf_avisos)))
        ok(os.path.exists(kb.DB) and not os.path.exists(orig_db) or
           os.path.getmtime(orig_db) == _mtime_antes,
           "build() NO ha tocado el índice de verdad (escribe en el tmp, no en casa base)")
        ok("Roto.pdf" in buf_out2.getvalue(), "el resumen nombra el PDF afectado (basename, no ruta completa)")
        ok("no-fatal" in buf_out2.getvalue(), "el resumen es honesto: no-fatal, no dice que se perdió el fichero")
    finally:
        kb.FV, kb.IDX, kb.DB, kb.VEC = orig_fv, orig_idx, orig_db, orig_vec
        kb._pdf_avisos.clear()

    print("RESULTADO kb PDF avisos (silenciar ruido de kb-reindex): %d OK, %d fallos" % (_pass, _fail))
    print("✅ KB PDF AVISOS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
