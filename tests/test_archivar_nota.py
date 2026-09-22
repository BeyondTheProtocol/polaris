#!/usr/bin/env python3
"""test_archivar_nota.py — el comando de 1 paso que persiste un entregable a la fuente de verdad.
Aislado (BTP_FV_DIR a un tmp; sin reindex para no tocar el RAG real)."""
import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="arch_test_")
os.environ["BTP_FV_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import archivar_nota as an  # noqa: E402
an.FV = _TMP  # el módulo leyó BTP_FV_DIR al importar; lo fijamos por si acaso

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    hoy = datetime.date.today().isoformat()

    # 1. archiva nota nueva (sin reindex), datada, con cabecera generada
    ruta, rel, reidx = an.archivar("Investigación de prueba", "cuerpo **md**.",
                                   tema="04 · IA/Notas", reindex=False)
    ok(os.path.exists(ruta), "crea el .md en la fuente de verdad")
    ok(hoy in os.path.basename(ruta), "el nombre va datado (YYYY-MM-DD)")
    txt = open(ruta, encoding="utf-8").read()
    # El fichero empieza por frontmatter `tipo: decision` — sin eso, honestidad_lint --repo
    # barría CERO documentos (exige esa clave y no la escribía nadie), mientras CLAUDE.md lo
    # presenta como el apoyo contra la falsa certeza. Lo que se archiva aquí es justo lo que
    # sostiene decisiones, o sea lo que hay que auditar.
    ok(txt.startswith("---\ntipo: decision"), "frontmatter tipo: decision (lo barre el linter)")
    ok("\n# Investigación de prueba" in txt, "cabecera con el título")
    ok("BORRADOR" in txt and "cuerpo **md**." in txt, "marca estado + conserva el cuerpo")
    ok(reidx is False, "no reindexa cuando reindex=False")

    # 2. contenido que YA empieza por # → respeta su cabecera (no la duplica)
    ruta2, _, _ = an.archivar("Otra", "# Mi propio título\n\ntexto", tema="04 · IA/Notas", reindex=False)
    _t2 = open(ruta2, encoding="utf-8").read()
    ok("# Mi propio título" in _t2 and _t2.count("# Mi propio título") == 1,
       "respeta la cabecera propia del contenido (no duplica)")
    ok(_t2.startswith("---\ntipo: decision"), "y también lleva frontmatter")

    # 3. slug seguro (sin barras ni signos raros en el nombre)
    base = os.path.basename(an.archivar("a/b: c?", "x", reindex=False)[0])
    ok("/" not in base and ":" not in base and "?" not in base, "slug de fichero seguro")

    # 4. desde un WORKTREE se archiva en CASA BASE, no en el árbol del worktree.
    # El 12-sep-2026 se perdieron 4 entregables por esto: 00_FUENTE-DE-VERDAD/ está gitignored,
    # así que lo escrito en el worktree no viaja en el merge y muere con el worktree. Encima
    # kb.py reindexa SIEMPRE casa base, o sea que el tool decía «RAG reindexado» y la nota no
    # estaba en el índice. Este test es el freno para que no vuelva.
    casa = tempfile.mkdtemp(prefix="arch_casa_")
    os.makedirs(os.path.join(casa, "00_FUENTE-DE-VERDAD"), exist_ok=True)
    wt = os.path.join(casa, ".claude", "worktrees", "rama-x")
    os.makedirs(wt, exist_ok=True)
    _env_fv = os.environ.pop("BTP_FV_DIR", None)
    os.environ["BTP_REPO"] = casa
    try:
        fv, origen = an._resolver_fv(root=wt)
        ok(fv == os.path.join(casa, "00_FUENTE-DE-VERDAD"), "desde worktree resuelve a casa base")
        ok(origen == "casa-base", "y lo dice (origen='casa-base')")
        # BTP_FV_DIR sigue mandando por encima de todo (tests y usos explícitos)
        os.environ["BTP_FV_DIR"] = _TMP
        ok(an._resolver_fv(root=wt) == (_TMP, "BTP_FV_DIR"), "BTP_FV_DIR tiene prioridad")
        # Sin casa base accesible: no se pierde la nota, pero se marca huérfana para poder avisar
        del os.environ["BTP_FV_DIR"]
        os.environ["BTP_REPO"] = os.path.join(casa, "no-existe")
        ok(an._resolver_fv(root=wt)[1] == "huerfano", "sin casa base marca 'huerfano' (main avisa)")
    finally:
        os.environ.pop("BTP_REPO", None)
        if _env_fv is not None:
            os.environ["BTP_FV_DIR"] = _env_fv

    print("RESULTADO archivar_nota: %d OK, %d fallos" % (_pass, _fail))
    print("✅ ARCHIVAR_NOTA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
