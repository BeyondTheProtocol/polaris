#!/usr/bin/env python3
"""test_radar_no_silenciar.py — un descarte SIN abrir el enlace no puede silenciar el radar.

POR QUÉ EXISTE (12-sep-2026). El 20-jul-2026 un triaje «skim de texto, sin abrir enlaces» descartó
~176 guardados escribiendo sus ids en una línea del `Mejoras-log.md` *precisamente para que
dedupasen*. El dedup de `radar_taller` busca el id literal en ese fichero, así que los 176 quedaron
silenciados para siempre sin que nadie hubiera abierto un enlace. Entre ellos, OpenMed, Colibri,
tencentDB, Fleet Deck y WANDR: el radar llevaba desde julio devolviendo 2 candidatos de 242, y esas
herramientas tuvieron que reaparecer a mano revisando los guardados el 12-sep. Medido ese día:
2 candidatos antes, 141 después.

El fallo de fondo es de arquitectura, no de criterio: el radar guardaba «ya ANALIZADO» y «ya
DECIDIDO» en el mismo blob de texto. Este test fija la separación.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="radar_nosil_")
os.makedirs(os.path.join(_TMP, "00_FUENTE-DE-VERDAD", "04 · IA", "Auto-mejora"), exist_ok=True)
os.environ["BTP_REPO"] = _TMP
import radar_taller as R  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    silenciado = "x:1111111111111111111"   # descartado CON veredicto → sí debe silenciar
    enterrado = "x:2222222222222222222"    # descartado por skim → NO debe silenciar

    open(R.MEJ_LOG, "w", encoding="utf-8").write(
        "# Log\n\n"
        "**Descarte con veredicto (enlace abierto, motivo escrito):** %s ya está cubierto por cost_guard.\n\n"
        "<!-- RADAR:NO-SILENCIAR inicio -->\n"
        "> Triaje por skim, sin abrir enlaces.\n"
        "%s\n"
        "<!-- RADAR:NO-SILENCIAR fin -->\n" % (silenciado, enterrado))

    blob = R._texto_ya_registrado()
    ok(silenciado in blob, "el descarte CON veredicto sigue contando para el dedup")
    ok(enterrado not in blob, "el descarte por SKIM deja de contar (no silencia)")

    def cand(cid):
        return {"url": "https://x.com/u/status/%s" % cid.split(":")[1], "texto": "", "handle": "u"}

    ok(R._ya_visto(cand(silenciado), blob) is True, "candidato con veredicto: se salta")
    ok(R._ya_visto(cand(enterrado), blob) is False, "candidato solo ojeado: vuelve a salir")

    # El historial NO se toca: el id enterrado sigue escrito en el fichero, solo deja de dedupar.
    ok(enterrado in open(R.MEJ_LOG, encoding="utf-8").read(),
       "el historial se conserva intacto en disco")

    # Sin marcadores, todo silencia como siempre (no cambiamos el comportamiento por defecto).
    open(R.MEJ_LOG, "w", encoding="utf-8").write("# Log\n\n%s\n%s\n" % (silenciado, enterrado))
    blob2 = R._texto_ya_registrado()
    ok(silenciado in blob2 and enterrado in blob2, "sin marcadores no cambia nada")

    # Casa base por defecto: desde un worktree el radar leía el árbol equivocado y devolvía 0.
    del os.environ["BTP_REPO"]
    import importlib
    importlib.reload(R)
    ok(R.ROOT == os.path.expanduser("~/claudecode"),
       "sin BTP_REPO resuelve a casa base, no al worktree")

    print("RESULTADO radar_no_silenciar: %d OK, %d fallos" % (_pass, _fail))
    print("✅ RADAR NO-SILENCIAR EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
