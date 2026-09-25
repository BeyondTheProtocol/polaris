#!/usr/bin/env python3
"""web_revertir.py — deshacer lo que la web publicó sola, en un comando.

POR QUÉ EXISTE (20-sep-2026). Que la web se actualice sola solo es aceptable si deshacerlo es
trivial. Si para quitar algo hubiera que abrir una sesión, explicarlo y mergear —justo lo que
{{TITULAR}} quería dejar de hacer—, el automatismo le habría cambiado un trabajo por otro peor.

Por eso cada publicación automática es un commit aislado con `[auto]` en el asunto: para que
esto sea posible sin tener que entender nada.

  python3 tools/web_revertir.py --ultimo     # revierte la última publicación automática
  python3 tools/web_revertir.py --listar     # qué ha publicado el sistema, y cuándo
  python3 tools/web_revertir.py <sha>        # una concreta
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WEB = os.environ.get("BTP_WEB_REPO", "/Users/polaris/projects/titular-{{APELLIDO}}-case")
BASE = "main"


def _git(*args, cwd=None, check=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd or WEB,
                       capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise RuntimeError("git %s → %s" % (" ".join(args), (r.stderr or r.stdout).strip()[:300]))
    return (r.stdout or "").strip()


def listar(n=15):
    """Publicaciones automáticas, de la más reciente a la más vieja."""
    _git("fetch", "origin", BASE)
    salida = _git("log", "origin/" + BASE, "--grep", r"^\[auto\]", "-n", str(n),
                  "--format=%h\t%ad\t%s", "--date=short")
    filas = []
    for linea in salida.splitlines():
        partes = linea.split("\t")
        if len(partes) == 3:
            filas.append(tuple(partes))
    return filas


def revertir(sha):
    """Revierte por PR, no empujando a `main` directo: el historial de la web queda legible y
    Netlify despliega igual al mergear."""
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
        m = subprocess.run(["gh", "pr", "merge", "--squash", "--delete-branch", rama],
                           cwd=wt, capture_output=True, text=True, timeout=180)
        return {"sha": sha, "pr": url, "merged": m.returncode == 0,
                "error": None if m.returncode == 0 else (m.stderr or m.stdout).strip()[:300]}
    except Exception as e:
        return {"sha": sha, "error": str(e)[:400], "merged": False}
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
            print("el sistema no ha publicado nada solo todavía.")
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
    if res.get("merged"):
        print("✅ revertido y publicado. PR: %s" % res.get("pr", ""))
        return 0
    print("⛔ no se pudo completar: %s" % (res.get("error") or "?"), file=sys.stderr)
    if res.get("pr"):
        print("   el PR de reversión está abierto: %s" % res["pr"], file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
