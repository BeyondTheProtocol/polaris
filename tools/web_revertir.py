#!/usr/bin/env python3
"""web_revertir.py — deshacer lo que la web publicó sola, en un comando.

POR QUÉ EXISTE (20-sep-2026). Que la web se actualice sola solo es aceptable si deshacerlo es
trivial. Si para quitar algo hubiera que abrir una sesión, explicarlo y mergear —justo lo que
{{TITULAR}} quería dejar de hacer—, el automatismo le habría cambiado un trabajo por otro peor.

Por eso cada publicación automática es un commit aislado con `[auto]` en el asunto: para que
esto sea posible sin tener que entender nada.

QUIÉN PUEDE (13-oct-2026). Hasta hoy, cualquiera: este fichero fusionaba la reversión sin
comprobar ningún permiso, y está en la allowlist. Deshacer también cambia lo que ve el mundo, así
que ahora NO fusiona: prepara el PR de reversión y dice su número y su commit. Sale con el
«fusiona» de {{TITULAR}} de siempre, por la vía que ya vigila el muro (`gh pr merge <N>
--match-head-commit <SHA>`), sin gramática ni permiso nuevos.

  python3 tools/web_revertir.py --ultimo     # revierte la última publicación automática
  python3 tools/web_revertir.py --listar     # qué ha publicado el sistema, y cuándo
  python3 tools/web_revertir.py <sha>        # una concreta
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import web_publicar as WP  # noqa: E402

WEB = os.environ.get("BTP_WEB_REPO", "/Users/polaris/projects/titular-{{APELLIDO}}-case")
BASE = "main"


def _git(*args, cwd=None, check=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd or WEB,
                       capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise RuntimeError("git %s → %s" % (" ".join(args), (r.stderr or r.stdout).strip()[:300]))
    return (r.stdout or "").strip()


def filtra(filas, cuerpos_de_reversiones):
    """De las filas (sha_largo, sha, fecha, asunto) con `[auto]`, las que siguen siendo una
    publicación VIVA.

    Fuera las que son ellas mismas una reversión (`[auto] revertir …`) y las que ya se
    revirtieron: sin esto, `--ultimo` podía elegir una reversión y «deshacer el deshacer», o
    volver a revertir algo que ya no estaba publicado."""
    revertidos = set(re.findall(r"This reverts commit ([0-9a-f]{7,40})", cuerpos_de_reversiones or ""))
    vivas = []
    for largo, corto, fecha, asunto in filas:
        if re.match(r"\[auto\]\s+revertir\b", asunto):
            continue
        if any(largo.startswith(r) for r in revertidos):
            continue
        vivas.append((corto, fecha, asunto))
    return vivas


def listar(n=15):
    """Publicaciones automáticas vivas, de la más reciente a la más vieja."""
    _git("fetch", "origin", BASE)
    salida = _git("log", "origin/" + BASE, "--grep", r"^\[auto\]", "-n", str(n * 3),
                  "--format=%H\t%h\t%ad\t%s", "--date=short")
    filas = [tuple(x.split("\t")) for x in salida.splitlines() if x.count("\t") == 3]
    cuerpos = _git("log", "origin/" + BASE, "--grep", "This reverts commit", "-n", "200",
                   "--format=%b")
    return filtra(filas, cuerpos)[:n]


def revertir(sha):
    """Prepara la reversión como PR, no empujando a `main` directo: el historial de la web queda
    legible y Netlify despliega igual al fusionar. Aquí no se fusiona nada."""
    wt = tempfile.mkdtemp(prefix="webrev-")
    rama = "auto/revert-%s" % sha[:8]
    try:
        _git("fetch", "origin", BASE)
        _git("worktree", "add", "-b", rama, wt, "origin/" + BASE)
        _git("revert", "--no-edit", sha, cwd=wt)
        _git("push", "-q", "origin", rama, cwd=wt)
        titulo = "[auto] revertir %s" % sha[:8]
        pr = subprocess.run(
            ["gh", "pr", "create", "--base", BASE, "--head", rama, "--title", titulo,
             "--body", "Revierte una publicación automática. Pedido con web_revertir.py."],
            cwd=wt, capture_output=True, text=True, timeout=180)
        url = (pr.stdout or pr.stderr).strip().splitlines()[-1] if (pr.stdout or pr.stderr) else ""
        info = WP.pr_info(rama, wt) or {}
        return {"sha": sha, "pr": url, "numero": info.get("number"),
                "cabeza": info.get("headRefOid"), "error": None}
    except Exception as e:
        return {"sha": sha, "error": str(e)[:400]}
    finally:
        subprocess.run(["git", "-C", WEB, "worktree", "remove", "--force", wt],
                       capture_output=True, timeout=60)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if not os.path.isdir(WEB):
        print("no encuentro el repo de la web en %s" % WEB, file=sys.stderr)
        return 2

    if args[0] == "--listar":
        filas = listar()
        if not filas:
            print("no hay ninguna publicación automática viva.")
            return 0
        for sha, fecha, asunto in filas:
            print("  %s  %s  %s" % (sha, fecha, asunto))
        return 0

    if args[0] == "--ultimo":
        filas = listar(1)
        if not filas:
            print("no hay ninguna publicación automática que revertir.")
            return 1
        sha = filas[0][0]
        print("revirtiendo %s (%s)…" % (sha, filas[0][2]))
    else:
        sha = args[0]

    res = revertir(sha)
    if res.get("error") or not res.get("numero"):
        print("⛔ no pude preparar la reversión: %s" % (res.get("error") or "no encuentro el PR"),
              file=sys.stderr)
        return 1
    print("🟡 reversión PREPARADA, sin publicar: PR #%s, cabeza %s\n   %s"
          % (res["numero"], res["cabeza"], res["pr"]))
    print("   Enséñale a {{TITULAR}} ese número y ese commit. Con su «fusiona», cuando el CI esté verde:")
    print("   gh pr merge %s --squash --delete-branch --match-head-commit %s"
          % (res["numero"], res["cabeza"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
