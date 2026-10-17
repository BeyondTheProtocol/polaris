"""Orientación: estructura (claves y tamaños) de las salidas de A y B, y columnas obs de los h5ad
congelados. Solo nombres de claves y agregados; ningún barcode.
Args: A:<fichero> | B:<fichero> ... ; MAXP=<prof> en el entorno."""
import json
import os

from _comun import A_DIR, B_DIR, sha256


def esquema(o, prof=0, maxp=3):
    pad = "  " * prof
    if isinstance(o, dict):
        for k, v in list(o.items())[:60]:
            if isinstance(v, (dict, list)) and prof < maxp and not (isinstance(v, list) and v and not isinstance(v[0], (dict, list))):
                print(f"{pad}{k}: {type(v).__name__}[{len(v)}]")
                esquema(v, prof + 1, maxp)
            else:
                print(f"{pad}{k}: {repr(v)[:160]}")
    elif isinstance(o, list):
        if o and isinstance(o[0], (dict, list)) and prof < maxp:
            esquema(o[0], prof + 1, maxp)
        else:
            print(f"{pad}{repr(o)[:160]}")


def run(*quien):
    if not quien:
        for d in (A_DIR, B_DIR):
            print("#####", os.path.basename(d))
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f)
                print(f"  {f}  {os.path.getsize(p)/1e6:.1f} MB")
        return
    for f in quien:
        p = os.path.join(A_DIR if f.startswith("A:") else B_DIR, f[2:])
        print("=====", f, sha256(p)[:12])
        if p.endswith(".json"):
            esquema(json.load(open(p)), maxp=int(os.environ.get("MAXP", 3)))
        elif p.endswith(".h5ad"):
            import anndata as an
            ad = an.read_h5ad(p, backed="r")
            print(ad)
            for c in ad.obs.columns:
                s = ad.obs[c]
                if s.dtype.name in ("category", "object", "bool"):
                    print("  ", c, dict(s.value_counts().head(12)))
                else:
                    print("  ", c, "num", float(s.min()), float(s.median()), float(s.max()))
