"""sha256 de cada fichero de un árbol de código de terceros (sin .git), ordenado. Uso: python -I _lock_codigo.py <dir>"""
import hashlib
import os
import sys


def lista(raiz):
    out = []
    for base, dirs, fs in os.walk(raiz):
        dirs[:] = sorted(d for d in dirs if d != ".git" and d != "__pycache__")
        for f in sorted(fs):
            p = os.path.join(base, f)
            h = hashlib.sha256(open(p, "rb").read()).hexdigest()
            out.append((os.path.relpath(p, raiz), h))
    return sorted(out)


if __name__ == "__main__":
    for rel, h in lista(sys.argv[1]):
        print(h, rel)
