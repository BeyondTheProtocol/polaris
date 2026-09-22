#!/usr/bin/env python3
"""backup.py — backup cifrado del sistema con restic a un USB. (Beyond the Protocol)

Uso:
  python3 tools/backup.py init       # inicializa el repo restic en el USB (1 vez)
  python3 tools/backup.py run        # backup incremental de ~/claudecode
  python3 tools/backup.py verify     # restore de prueba a /tmp + comprobacion (restore PROBADO)
  python3 tools/backup.py snapshots  # lista las copias

Contraseña: del Llavero (servicio btp-restic-password). Guardala TAMBIEN en papel:
perderla = backup IRRECUPERABLE (restic cifra; no hay recuperacion sin password).
Destino: env BTP_RESTIC_REPO, o por defecto /Volumes/POLARIS-BACKUP/restic.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret

SRC = os.path.expanduser("~/claudecode")
RESTIC_REPO = os.environ.get("BTP_RESTIC_REPO", "/Volumes/POLARIS-BACKUP/restic")
EXCLUDES = ["--exclude", ".venv", "--exclude", ".venv-biomcp", "--exclude", ".venv-cbioportal",
            "--exclude", "**/__pycache__", "--exclude", "**/node_modules",
            "--exclude", ".git/objects", "--exclude", "*.tmp", "--exclude", ".DS_Store"]


def password():
    pw = get_secret("btp-restic-password")
    if not pw:
        sys.exit("Falta la contraseña restic en el Llavero (btp-restic-password).\n"
                 "  security add-generic-password -U -a \"$USER\" -s btp-restic-password -w")
    return pw


def restic(*args):
    env = dict(os.environ, RESTIC_REPOSITORY=RESTIC_REPO, RESTIC_PASSWORD=password())
    return subprocess.run(["restic", *args], env=env, text=True)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    parent = os.path.dirname(RESTIC_REPO)
    if cmd != "init" and not os.path.isdir(parent):
        sys.exit("No veo el USB en %s. Conectalo (o exporta BTP_RESTIC_REPO)." % parent)

    if cmd == "init":
        sys.exit(restic("init").returncode)
    if cmd == "run":
        sys.exit(restic("backup", SRC, *EXCLUDES).returncode)
    if cmd == "snapshots":
        sys.exit(restic("snapshots").returncode)
    if cmd == "verify":
        d = tempfile.mkdtemp(prefix="restic-verify-")
        rc = restic("restore", "latest", "--target", d, "--include",
                    os.path.join(SRC, "CLAUDE.md")).returncode
        found = []
        for root, _, files in os.walk(d):
            if "CLAUDE.md" in files:
                found.append(os.path.join(root, "CLAUDE.md"))
        ok = rc == 0 and found
        if ok:
            with open(found[0]) as a, open(os.path.join(SRC, "CLAUDE.md")) as b:
                ok = a.read() == b.read()
        print("Restore de prueba en %s -> %s" % (d, "OK (contenido idéntico)" if ok else "FALLO"))
        sys.exit(0 if ok else 1)
    sys.exit("uso: init | run | verify | snapshots")


if __name__ == "__main__":
    main()
