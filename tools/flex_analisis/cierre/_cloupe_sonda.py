"""Lector mínimo de .cloupe (10x, versión 9): cabecera, índice JSON y bloques comprimidos.
IDs de bloque enmascarados en todo lo que se imprime. Uso: python -I _cloupe_sonda.py <hueso|higado> [indice]"""
import gzip
import json
import os
import re
import sys
import zlib

F = os.path.expanduser("~/Clinico-PRIVADO/flex/cloupe_10x")


def fichero(tejido):
    fs = sorted((x for x in os.listdir(F) if x.endswith(".cloupe")), key=lambda x: os.path.getsize(os.path.join(F, x)))
    return os.path.join(F, fs[0] if tejido == "hueso" else fs[-1])  # hueso = el pequeño (1.993 células)


def mask(s):
    return re.sub(r"B?\d{2}-\d{5}_M\d", "<ID>", s)


def bloque(f, b):
    f.seek(b["Start"])
    raw = f.read(b["End"] - b["Start"])
    if b.get("CompressionType") == 1:
        try:
            return gzip.decompress(raw)
        except OSError:
            return zlib.decompress(raw)
    if b.get("CompressionType") == 2:
        # trozos gzip; Index (uint64, sin comprimir) = [tamaño de trozo descomprimido, inicios de trozo...]
        import numpy as np
        ixb = b["Index"]
        f.seek(ixb["Start"])
        e = np.frombuffer(f.read(ixb["End"] - ixb["Start"]), dtype="<u8")
        ini = sorted(set(int(x) for x in e[1:].tolist())) + [len(raw)]
        return b"".join(gzip.decompress(raw[a:z]) for a, z in zip(ini[:-1], ini[1:]) if z > a)
    return raw


def indice(p, todos=False):
    """Índice de la primera cabecera; con todos=True, lista de índices de la cadena de cabeceras
    (nextHeaderOffset), que es donde Loupe guarda lo añadido después (p. ej., anotación de tipos)."""
    out = []
    with open(p, "rb") as f:
        off = 0
        tam = os.path.getsize(p)
        while off < tam:
            f.seek(off)
            txt = f.read(8192).split(b"\x00")[0]
            try:
                h = json.loads(txt)
            except ValueError:
                break
            out.append(json.loads(bloque(f, h["indexBlock"])))
            nxt = h.get("nextHeaderOffset", 0)
            if not todos or not nxt or nxt <= off:
                break
            off = nxt
    return out if todos else out[0]


def esquema(o, pre="", prof=0):
    if prof > 5:
        return
    if isinstance(o, dict):
        if {"Start", "End"} <= set(o):
            print(pre, "BLOQUE", {k: o[k] for k in o if k != "Index"})
            return
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                print(pre + mask(str(k)) + ":")
                esquema(v, pre + "  ", prof + 1)
            else:
                print(pre + mask(str(k)) + ":", mask(repr(v))[:120])
    elif isinstance(o, list):
        print(pre, f"[lista {len(o)}]")
        for x in o[:4]:
            esquema(x, pre + "  ", prof + 1)


if __name__ == "__main__":
    esquema(indice(fichero(sys.argv[1])))


def clusterings(tejido):
    p = fichero(tejido)
    ix = indice(p)
    with open(p, "rb") as f:
        for c in ix["Clusterings"]:
            meta = bloque(f, c["Metadata"]).decode("utf-8", "replace") if c.get("Metadata") else ""
            f.seek(c["Assignments"]["Start"])
            raw = f.read(16)
            print(mask(c["Name"]), "| meta:", mask(meta)[:300], "| asig bytes0:", raw[:8])
        for t in ix["CellTracks"]:
            print("TRACK", mask(t["Name"]), mask(bloque(f, t["Metadata"]).decode("utf-8", "replace"))[:300])


def arr(f, b):
    """Decodifica un bloque a numpy. Kind 24: cadenas de ancho fijo ArrayWidth. Resto: ancho deducido de
    bytes/ArraySize; Kind 14 = coma flotante."""
    import numpy as np
    raw = bloque(f, b)
    n = b["ArraySize"]
    if b["Kind"] == 24:
        wd = b["ArrayWidth"]
        return np.array([raw[i * wd:(i + 1) * wd].rstrip(b"\x00").decode() for i in range(n)])
    bpe = len(raw) // n
    dt = {4: "<f4", 8: "<f8"}[bpe] if b["Kind"] == 14 else {1: "<u1", 2: "<u2", 4: "<u4", 8: "<u8"}[bpe]
    return np.frombuffer(raw[: n * bpe], dtype=dt)


def tipos(tejido):
    p = fichero(tejido)
    ix = indice(p)
    with open(p, "rb") as f:
        m = ix["Matrices"][0]
        for k in ("Barcodes", "UMICounts", "CSCPointers", "CSCIndices", "CSCValues", "CSRPointers"):
            raw = bloque(f, m[k])
            print(k, "Kind", m[k]["Kind"], "bytes/elem", len(raw) / m[k]["ArraySize"])
        pr = ix["Projections"][0]
        print("UMAP bytes/elem", len(bloque(f, pr["Matrix"])) / pr["Matrix"]["ArraySize"], "Dims", pr["Dims"])
        c = ix["Clusterings"][0]["Assignments"]
        print("Asignaciones bytes/elem", len(bloque(f, c)) / c["ArraySize"])
        print("Cells per tag:", __import__("collections").Counter(arr(f, ix["CellTracks"][1]["Values"]).tolist()))
    ixb = indice(fichero("higado"))
    print("cloupe grande: barcodes", ixb["Matrices"][0]["BarcodeCount"], "tracks", [mask(t["Name"]) for t in ixb["CellTracks"]],
          "clusterings", [mask(c["Name"]) for c in ixb["Clusterings"]][:3])


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[2] == "tipos":
    tipos(sys.argv[1])
if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[2] == "cadena":
    ixs = indice(fichero(sys.argv[1]), todos=True)
    print("cabeceras:", len(ixs))
    for i, ix in enumerate(ixs[1:], 1):
        print("### cabecera", i)
        esquema({k: v for k, v in ix.items() if v not in ([], None, "")})
