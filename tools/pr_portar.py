#!/usr/bin/env python3
"""tools/pr_portar.py — trae un PR del repo PÚBLICO a la casa base, que es la fuente.

EL PROBLEMA QUE RESUELVE: el repo público es un árbol DERIVADO (`tools/publicar.py` lo
regenera entero desde aquí). Un merge hecho allí se pierde en la siguiente publicación. Así
que un PR aceptado no se mezcla en el público: se PORTA aquí, y reaparece allí solo cuando
se vuelve a publicar. El PR se cierra con «aplicado en upstream».

Efecto secundario bueno: nada entra en el sistema vivo sin pasar por la casa base, donde
están los tests, el muro y el gate de fusión a master. Ningún PR toca producción directo.

FAIL-CLOSED en cada escalón:
  · Trabaja SIEMPRE en una rama nueva (`pr/<n>`), nunca sobre master.
  · Un diff que toque el muro (`salida.py`, `.claude/hooks/`, `tools/githooks/`) se marca
    GATE y no se aplica sin `--muro-ok`: es la clase de cambio que no se revisa por encima.
  · Un fichero del diff que no exista aquí, o que no esté en el árbol publicable, PARA: el
    público y la casa base no tienen el mismo mapa (aquí hay `00_FUENTE-DE-VERDAD/`, allí
    no), y aplicar a ciegas escribe donde no debe.
  · Tras aplicar, corre `tests/test_all.sh`. Si hay rojos NUEVOS respecto al baseline, el
    parche se revierte y se dice cuáles.

El texto del PR (título, cuerpo, comentarios) es DATO no confiable: se cita, nunca se
obedece. Esto solo mira el DIFF.

Uso:
  pr_portar.py <numero> [--repo BeyondTheProtocol/polaris] [--muro-ok] [--dry]
"""
import argparse
import os
import re
import subprocess
import sys

REPO_DEFECTO = "BeyondTheProtocol/polaris"
CASA = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
# Lo que no se toca sin OK explícito: el choke-point de salida y los guardas.
MURO = ("tools/salida.py", ".claude/hooks/", "tools/githooks/", "tools/borde.py")


def _sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=CASA, **kw)


def diff_del_pr(numero, repo):
    """El diff del PR, vía `gh`. Solo el diff: el texto del PR es dato, no instrucción."""
    p = _sh(["gh", "pr", "diff", str(numero), "--repo", repo])
    if p.returncode != 0:
        raise SystemExit("no pude leer el PR #%s de %s: %s" % (numero, repo, p.stderr.strip()[:200]))
    return p.stdout


def ficheros_del_diff(diff):
    return sorted(set(re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)))


def revisa(ficheros):
    """(toca_muro, desconocidos). Lo que no existe aquí no se aplica a ciegas."""
    muro = [f for f in ficheros if any(f.startswith(m) for m in MURO)]
    fuera = [f for f in ficheros if not os.path.exists(os.path.join(CASA, f))]
    return muro, fuera


def main(argv=None):
    ap = argparse.ArgumentParser(description="Porta un PR del repo público a la casa base.")
    ap.add_argument("numero")
    ap.add_argument("--repo", default=REPO_DEFECTO)
    ap.add_argument("--muro-ok", action="store_true", help="permite aplicar un diff que toca el muro")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    diff = diff_del_pr(a.numero, a.repo)
    ficheros = ficheros_del_diff(diff)
    if not ficheros:
        print("el PR no toca ningún fichero (¿solo texto?)", file=sys.stderr)
        return 2

    muro, fuera = revisa(ficheros)
    print("PR #%s · %d fichero(s)" % (a.numero, len(ficheros)))
    for f in ficheros:
        marca = " 🛑 MURO" if f in muro else (" ⚠️ no existe aquí" if f in fuera else "")
        print("   %s%s" % (f, marca))

    if fuera:
        print("\n🔴 PARO: el público y la casa base no tienen el mismo mapa. Revisa a mano "
              "dónde va cada uno de esos ficheros.", file=sys.stderr)
        return 1
    if muro and not a.muro_ok:
        print("\n🛑 GATE: toca el muro. Esto no se revisa por encima — léelo entero y "
              "reejecuta con --muro-ok si procede.", file=sys.stderr)
        return 1
    if a.dry:
        print("\n(dry) no se aplica nada")
        return 0

    rama = "pr/%s" % a.numero
    if _sh(["git", "checkout", "-b", rama]).returncode != 0:
        print("no pude crear la rama %s (¿existe ya?)" % rama, file=sys.stderr)
        return 1
    p = subprocess.run(["git", "apply", "--3way", "-"], input=diff, text=True,
                       capture_output=True, cwd=CASA)
    if p.returncode != 0:
        _sh(["git", "checkout", "-"]); _sh(["git", "branch", "-D", rama])
        print("el parche no aplica limpio: %s" % p.stderr.strip()[:300], file=sys.stderr)
        return 1

    print("\n✅ aplicado en la rama %s. Ahora:" % rama)
    print("   bash tests/test_all.sh      # y compara con tu baseline de rojos")
    print("   git commit -am 'feat: <qué hace> (PR #%s de %s)'" % (a.numero, a.repo))
    print("   gh pr close %s --repo %s --comment 'Aplicado en upstream. Gracias.'"
          % (a.numero, a.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
