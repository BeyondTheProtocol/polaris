#!/usr/bin/env python3
"""tools/release.py — el ritual de release de Polaris (determinista, local, $0, sin LLM).

Polaris no usa semver: es un sistema vivo, versionamos por FECHA. Una *release* = un
merge a `master` que mejora el sistema (o su ruta clínica) de forma observable. Este
helper deja constancia de cada salto: añade la entrada al CHANGELOG.md y crea un tag
datado sobre el HEAD de `master`.

Uso:
  python3 tools/release.py "El arnés a 10/10" --items "observabilidad;evals de drift"
  python3 tools/release.py "Fix X" --ref b6245ab8        # taggear un commit concreto
  python3 tools/release.py "Solo tag" --no-changelog     # no tocar CHANGELOG
  python3 tools/release.py --dry-run "..."               # enseña qué haría, sin escribir
  python3 tools/release.py --list                        # lista las releases (tags)

Convención de tag: vAAAA.MM.DD, con sufijo -2, -3… si ya hay uno ese día.

Muro: LOCAL únicamente — NUNCA hace push. El tag y el bump de CHANGELOG se quedan en
casa base; comitea el CHANGELOG con tu flujo de git normal (te lo recuerda al final).
"""
import argparse
import datetime
import os
import subprocess
import sys

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
CHANGELOG = os.path.join(REPO, "CHANGELOG.md")


def _git(*args, check=True):
    """Corre git -C REPO ... y devuelve stdout (str). Nunca hace push."""
    if "push" in args:
        raise SystemExit("release.py: el muro no permite push.")
    out = subprocess.run(["git", "-C", REPO, *args],
                         capture_output=True, text=True)
    if check and out.returncode != 0:
        raise SystemExit("git %s falló: %s" % (" ".join(args), out.stderr.strip()))
    return out.stdout.strip()


def _hoy():
    return datetime.date.today().strftime("%Y.%m.%d")


def _tags():
    """Lista de tags de release (vAAAA.MM.DD[-N]), más nuevos primero."""
    raw = _git("tag", "--list", "v*", "--sort=-creatordate")
    return [t for t in raw.splitlines() if t.strip()]


def _siguiente_tag():
    """Tag de hoy, con sufijo -2/-3… si ya existe alguno de hoy."""
    base = "v" + _hoy()
    existentes = set(_git("tag", "--list", base + "*").splitlines())
    if base not in existentes:
        return base
    n = 2
    while "%s-%d" % (base, n) in existentes:
        n += 1
    return "%s-%d" % (base, n)


def cmd_list():
    tags = _tags()
    if not tags:
        print("(sin releases todavía — usa: release.py \"qué mejoró\")")
        return 0
    print("RELEASES de Polaris (%d):\n" % len(tags))
    for t in tags:
        # fecha del tag + sujeto del commit que apunta
        fecha = _git("log", "-1", "--format=%cd", "--date=short", t, check=False)
        subj = _git("log", "-1", "--format=%s", t, check=False)
        print("  %-16s  %s  %s" % (t, fecha or "?", subj or ""))
    return 0


def _entrada_changelog(tag, titulo, items, ref_hash):
    lineas = ["## %s — %s" % (tag, titulo)]
    for it in items:
        it = it.strip()
        if it:
            lineas.append("- %s" % it)
    lineas.append("- _merge_ `%s`" % ref_hash)
    return "\n".join(lineas) + "\n\n"


def _insertar_changelog(bloque):
    """Inserta el bloque justo antes de la primera entrada `## v...` existente."""
    if not os.path.exists(CHANGELOG):
        raise SystemExit("No existe %s — créalo primero." % CHANGELOG)
    with open(CHANGELOG, encoding="utf-8") as f:
        lineas = f.readlines()
    idx = next((i for i, ln in enumerate(lineas) if ln.startswith("## v")), None)
    if idx is None:  # no hay releases aún: insertar tras el primer separador '---'
        idx = next((i for i, ln in enumerate(lineas) if ln.strip() == "---"), len(lineas) - 1) + 1
    lineas.insert(idx, bloque)
    with open(CHANGELOG, "w", encoding="utf-8") as f:
        f.writelines(lineas)


def cmd_release(titulo, items, ref, no_changelog, dry):
    tag = _siguiente_tag()
    ref_full = _git("rev-parse", "--short", ref)   # valida que el ref existe
    print("Release: %s  →  %s (%s)" % (tag, ref, ref_full))
    bloque = _entrada_changelog(tag, titulo, items, ref_full)
    if dry:
        print("\n[DRY-RUN] crearía el tag %s sobre %s" % (tag, ref_full))
        if not no_changelog:
            print("[DRY-RUN] añadiría a CHANGELOG.md:\n")
            print("\n".join("    " + l for l in bloque.rstrip().splitlines()))
        return 0
    if not no_changelog:
        _insertar_changelog(bloque)
        print("✓ CHANGELOG.md actualizado")
    _git("tag", "-a", tag, ref, "-m", "%s (%s)" % (titulo, tag))
    print("✓ tag %s creado sobre %s (LOCAL, sin push)" % (tag, ref_full))
    if not no_changelog:
        print("\n→ Recuerda comitear el bump del CHANGELOG (tu flujo de git habitual).")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Ritual de release de Polaris (local, $0).")
    p.add_argument("titulo", nargs="?", help="qué mejoró (título corto de la release)")
    p.add_argument("--items", default="", help="puntos separados por ';'")
    p.add_argument("--ref", default="master", help="commit/ref a taggear (def. master)")
    p.add_argument("--no-changelog", action="store_true", help="solo tag, no tocar CHANGELOG")
    p.add_argument("--dry-run", action="store_true", help="enseña qué haría, sin escribir")
    p.add_argument("--list", action="store_true", help="lista las releases (tags)")
    a = p.parse_args(argv)

    if a.list:
        return cmd_list()
    if not a.titulo:
        p.print_help()
        return 1
    items = a.items.split(";") if a.items else []
    return cmd_release(a.titulo, items, a.ref, a.no_changelog, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
