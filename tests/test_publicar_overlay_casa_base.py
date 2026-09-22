#!/usr/bin/env python3
"""`publicar.py` encuentra sus overlays desde un worktree, y SOLO desde un worktree. Sintético.

Qué fija, y por qué existe: los overlays de despersonalización (`perfil.local.json`,
`nombres.local.json`) son gitignored, así que no llegan a un worktree. Desde una rama se leían
vacíos, `_overlay_ok()` se plantaba y no se podía publicar. Eso NO era un agujero —el
fail-closed hacía su trabajo—, pero dejaba la herramienta inservible justo donde se trabaja
siempre. Ahora se buscan también en casa base, como ya hacen `seguimiento._cargar_nombres_deny`
y `subir_historial_drive._ruta_identidad`.

EL LÍMITE ES EL TEST QUE MÁS IMPORTA. La primera versión de este arreglo heredaba el overlay a
cualquier directorio, y eso tumbó
`test_publicar_fuga.py::test_sin_overlay_aborta_en_vez_de_publicar_en_crudo`: convertía el
fail-closed en un fail-open en cualquier máquina que tenga el repo en casa. Por eso la herencia
se limita a un worktree DE ESTE repo, reconocido por lo que dice git (un `.git` que es fichero
y apunta a `<casa base>/.git/worktrees/<nombre>`), no por la ruta. Un worktree es el mismo repo
con los mismos permisos; un directorio cualquiera no.
"""
import io
import json
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import publicar as P  # noqa: E402

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def arbol(tmp, nombre, nombres=None, titular=None, sustituciones=None, worktree_de=None):
    """Un árbol con tools/ y, si se piden, sus overlays. Con `worktree_de` se le pone el `.git`
    de fichero que git deja en un worktree de verdad."""
    d = os.path.join(tmp, nombre)
    os.makedirs(os.path.join(d, "tools"), exist_ok=True)
    if nombres is not None:
        with io.open(os.path.join(d, "tools", "nombres.local.json"), "w", encoding="utf-8") as f:
            json.dump({"nombres": nombres}, f)
    if titular is not None or sustituciones is not None:
        with io.open(os.path.join(d, "tools", "perfil.local.json"), "w", encoding="utf-8") as f:
            json.dump({"titular": {"nombre": titular or ""},
                       "sustituciones": sustituciones or [], "bloques": []}, f)
    if worktree_de:
        with io.open(os.path.join(d, ".git"), "w", encoding="utf-8") as f:
            f.write("gitdir: %s\n" % os.path.join(worktree_de, ".git", "worktrees", nombre))
    return d


tmp = tempfile.mkdtemp(prefix="pub-overlay-")
ROOT0 = P.ROOT
try:
    base_buena = arbol(tmp, "casa", nombres=["Mengánez", "Zutánez"], titular="Fulanita",
                       sustituciones=[["ay", "uy"]])

    # ── 1. EL LÍMITE: un directorio que no es worktree NO hereda ─────────────────────────
    print("== un árbol cualquiera NO hereda el overlay de casa base ==")
    P.ROOT = arbol(tmp, "suelto")
    check(P._casa_base_si_soy_worktree() is None, "no se reconoce casa base desde un dir suelto")
    check(len(P._overlay_ok()) == 3, "_overlay_ok() bloquea y nombra las 3 piezas que faltan")
    check(P._nombres_de_terceros() == (), "la deny-list se queda vacía, no hereda por la puerta de atrás")

    # ── 2. Un worktree sin overlay SÍ va a buscarlo a casa base ──────────────────────────
    print("== worktree sin overlay + casa base con overlay ==")
    P.ROOT = arbol(tmp, "rama_pelada", worktree_de=base_buena)
    check(P._casa_base_si_soy_worktree() == base_buena,
          "reconoce casa base por el gitdir (%s)" % P._casa_base_si_soy_worktree())
    check(sorted(P._nombres_de_terceros()) == ["Mengánez", "Zutánez"],
          "lee la deny-list de casa base (%s)" % (P._nombres_de_terceros(),))
    check(P._titular("nombre") == ["Fulanita"], "lee el titular de casa base")
    check(len(P._perfil()["sustituciones"]) == 1, "lee las sustituciones de casa base")
    check(P._overlay_ok() == [], "y ya se puede publicar desde la rama")

    # ── 3. Si casa base tampoco tiene overlay, sigue cerrado ─────────────────────────────
    print("== worktree de una casa base pelada: NO se publica ==")
    P.ROOT = arbol(tmp, "rama_huerfana", worktree_de=arbol(tmp, "casa_pelada"))
    check(len(P._overlay_ok()) == 3, "sin overlay por ninguna parte, el fail-closed sigue cerrado")

    # ── 4. Con overlay en las dos: la deny-list SUMA, el perfil ELIGE ────────────────────
    print("== overlay en las dos partes ==")
    P.ROOT = arbol(tmp, "rama_propia", nombres=["Perénguez"], titular="Menganita",
                   sustituciones=[["a", "b"], ["c", "d"]], worktree_de=base_buena)
    check(sorted(P._nombres_de_terceros()) == ["Mengánez", "Perénguez", "Zutánez"],
          "la deny-list UNE las dos (%s)" % (sorted(P._nombres_de_terceros()),))
    check(P._titular("nombre") == ["Menganita"], "el perfil se queda con el del árbol de trabajo")
    check(len(P._perfil()["sustituciones"]) == 2, "  y con SUS sustituciones, sin mezclar")

    # ── 5. Un overlay roto no tumba al otro ──────────────────────────────────────────────
    print("== overlay corrupto ==")
    with io.open(os.path.join(P.ROOT, "tools", "nombres.local.json"), "w", encoding="utf-8") as f:
        f.write("{esto no es json")
    check(sorted(P._nombres_de_terceros()) == ["Mengánez", "Zutánez"],
          "un JSON roto en una parte no se lleva por delante la otra")

    # ── 6. El filtro de longitud no se pierde por el camino ──────────────────────────────
    print("== nombres cortos ==")
    P.ROOT = arbol(tmp, "rama_cortos", nombres=["Ana", "Bo"],
                   worktree_de=arbol(tmp, "casa_cortos", nombres=["Li", "Robertita"]))
    check(P._nombres_de_terceros() == ("Robertita",),
          "solo pasan los de >=4 letras, vengan de donde vengan (%s)"
          % (P._nombres_de_terceros(),))

    # ── 7. Un .git que no apunta a un worktree no cuela ──────────────────────────────────
    print("== .git raro ==")
    d = arbol(tmp, "git_raro")
    with io.open(os.path.join(d, ".git"), "w", encoding="utf-8") as f:
        f.write("gitdir: %s\n" % os.path.join(base_buena, ".git", "modules", "algo"))
    P.ROOT = d
    check(P._casa_base_si_soy_worktree() is None,
          "un gitdir de submódulo (u otra cosa) no se toma por un worktree")
finally:
    P.ROOT = ROOT0
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fallos:
    print("❌ %d fallo(s):" % len(fallos))
    for f in fallos:
        print("   -", f)
    sys.exit(1)
print("✅ publicar.py halla sus overlays desde un worktree, y solo desde un worktree")
