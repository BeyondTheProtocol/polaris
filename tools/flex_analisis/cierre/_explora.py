"""Exploración de orientación (solo agregados): lista salidas del tribunal y resume claves de JSON/npz.
Nunca imprime barcodes. Uso: python -I _explora.py [ruta relativa a ~/Clinico-PRIVADO/flex ...]"""
import json
import os
import sys

import numpy as np

F = os.path.expanduser("~/Clinico-PRIVADO/flex")


def mask(t):
    """Enmascara ids de bloque en TODO lo que se imprime (incidente del 16-oct: el listado por defecto los sacó)."""
    import re
    return re.sub(r"B?\d{2}-\d{5}_M\d", "<ID>", str(t))


def keys(o, p=0, maxp=2):
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, dict) and p < maxp:
                print("  " * p + mask(k) + ":")
                keys(v, p + 1, maxp)
            else:
                print("  " * p + mask(f"{k}: {repr(v)[:220]}"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--busca":
        import re
        pat = re.compile(a[1], re.I)
        for r, ds, fs in os.walk(F):
            for x in fs:
                q = os.path.relpath(os.path.join(r, x), F)
                if pat.search(q):
                    q = re.sub(r"B?\d{2}-\d{5}_M\d", "<ID>", q)
                    print("  ", q, f"{os.path.getsize(os.path.join(r, x))/1e6:.1f}MB")
        sys.exit(0)
    if not a:
        for d in ("tribunal", "analisis_A", "analisis_B", "931c64af777e", "931c64af777e/MP/outs"):
            p = os.path.join(F, d)
            print("##", d)
            for f in sorted(os.listdir(p)):
                q = os.path.join(p, f)
                print("  ", mask(f), f"{os.path.getsize(q)/1e6:.1f}MB" if os.path.isfile(q) else "<dir>")
    else:
        for f in a:
            p = os.path.join(F, f)
            print("=====", mask(f))
            if os.path.isdir(p):
                for r, ds, fs in os.walk(p):
                    for x in fs:
                        q = os.path.join(r, x)
                        print("  ", mask(os.path.relpath(q, p)), f"{os.path.getsize(q)/1e6:.1f}MB")
            elif p.endswith(".json"):
                o = json.load(open(p))
                if isinstance(o, dict) and "filas" in o:
                    o = {k: v for k, v in o.items() if k != "filas"}
                keys(o, maxp=int(os.environ.get("MAXP", 2)))
            elif p.endswith(".npz"):
                z = np.load(p, allow_pickle=True)
                for k in z.files:
                    v = z[k]
                    if v.dtype == bool:
                        print(" ", k, v.shape, "True:", int(v.sum()))
                    else:
                        print(" ", k, v.shape, v.dtype)
            elif p.endswith(".h5ad"):
                import anndata as an
                h = an.read_h5ad(p, backed="r")
                print(h.shape, "obs:", list(h.obs.columns))
                h.file.close()
            elif p.endswith(".csv"):
                import pandas as pd
                d = pd.read_csv(p)
                print(d.shape, list(d.columns))
