#!/usr/bin/env python
"""refcon_run.py — REFCON (copias por célula sin referencia) sobre el Flex, fase 2 del hígado célula a célula.

Solo por la ventanilla (entorno refcon, torch 2.10 con MPS):
    python3 tools/lector_clinico.py procesa flex_refcon -- <paso>
Pasos:
    hueso      infiere las 1.993 células del hueso + prueba de determinismo (256 células dos veces)
    higado     infiere las 19.047 del hígado en trozos de 2.000 (reanudable). Exige cache/refcon/gate.json aplicable
    clasifica  clasificador propio de REFCON (call_sample, por defecto) sobre el hígado: fila secundaria; si no cabe
               en memoria se declara no ejecutado
REGLAS_HIGADO_CELULA_v1.json, bloque REFCON_D2b. Código de REFCON sin modificar en ~/.claude-science/refcon/04d9d24.
Muro: -I, BTP_VENTANILLA=1, zona clínica y sandbox sin red (_hc.arranque). Células por índice de fila («c<i>»), nunca
barcodes. stdout: solo agregados.
"""
import json
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _hc  # noqa: E402

TROZO = 2000
DET_N = 256
TOL_DET = 1e-4
INFER = os.path.join(_hc.REFCON_DIR, "scripts", "infer.py")


def _carga(t):
    import numpy as np
    import scipy.sparse as sp
    X = sp.load_npz(os.path.join(_hc.CACHE_CIERRE, f"X_{t}.npz")).tocsr()
    # caché propia del cierre (algunas columnas son arrays de objetos de pandas): allow_pickle como en _final.T
    o = np.load(os.path.join(_hc.CACHE_CIERRE, f"obs_{t}.npz"), allow_pickle=True)
    return X, {k: np.asarray(o[k]) for k in ("gene_ids", "simbolo", "chrom", "start", "tipo")}


def simbolos_unicos(sym):
    """Símbolos como nombres de gen (el clasificador de REFCON busca marcadores por símbolo); duplicados con -1, -2…"""
    vistos, out = {}, []
    for s in [str(x) for x in sym]:
        if s in vistos:
            vistos[s] += 1
            out.append(f"{s}-{vistos[s]}")
        else:
            vistos[s] = 0
            out.append(s)
    return out


def genes_autosomas(o):
    """Índices de genes de chr1-chr22 con coordenada, ordenados por (cromosoma, inicio); orden estable en empates."""
    import numpy as np
    chrom = o["chrom"].astype(str)
    num = np.array([int(c[3:]) if c.startswith("chr") and c[3:].isdigit() else 0 for c in chrom])
    start = o["start"].astype(np.int64)
    idx = np.where((num >= 1) & (num <= 22) & (start >= 0))[0]
    orden = np.lexsort((start[idx], num[idx]))
    return idx[orden], num, start


def var_refcon(o, gi, num, start):
    import numpy as np
    import pandas as pd
    n = num[gi]
    st = start[gi]
    off, acc = {}, 0
    for c in range(1, 23):
        off[c] = acc
        m = n == c
        acc += (int(st[m].max()) + 1) if m.any() else 0
    cb = np.zeros(len(gi), dtype=np.int64)
    cb[0] = 1
    cb[1:][n[1:] != n[:-1]] = 1
    return pd.DataFrame({"gene_id": o["gene_ids"][gi].astype(str), "chromosome": [f"chr{c}" for c in n], "start": st,
                         "end": st + 1, "abspos": np.array([off[c] for c in n], dtype=np.int64) + st, "chr_boundary": cb},
                        index=simbolos_unicos(o["simbolo"][gi]))


def escribe_entrada(t, filas, ruta):
    import anndata as an
    import numpy as np
    import pandas as pd
    X, o = _carga(t)
    gi, num, start = genes_autosomas(o)
    ad = an.AnnData(X=X[filas][:, gi].astype(np.int32), var=var_refcon(o, gi, num, start),
                    obs=pd.DataFrame(index=[f"c{i}" for i in filas]))
    ad.write_h5ad(ruta)
    return ad.shape


def infiere(h5ad, outdir):
    """scripts/infer.py sin cambios (ens3, mps, cell-batch 16). Borra los pred_*.pt intermedios (regenerables)."""
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", INFER, "--data", h5ad, "--out", outdir, "--device", "mps", "--cell-batch", "16"],
                       capture_output=True, text=True)
    with open(os.path.join(outdir, "log.txt"), "w") as f:
        f.write(r.stdout + "\n--- stderr ---\n" + r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"infer.py rc {r.returncode} (log en caché)")
    for f in os.listdir(outdir):
        if f.startswith("pred_") and f.endswith(".pt"):
            os.remove(os.path.join(outdir, f))
    rt = _hc.lee_json(os.path.join(outdir, "runtime.json"))
    rt["segundos_pared"] = round(time.time() - t0, 1)
    return rt


def paso_hueso():
    import numpy as np
    d = _hc.cached("refcon", "hueso")
    X, _ = _carga("hueso")
    n = X.shape[0]
    print("hueso: entrada", escribe_entrada("hueso", np.arange(n), os.path.join(d, "entrada.h5ad")))
    rt = infiere(os.path.join(d, "entrada.h5ad"), d)
    print("hueso: REFCON", {k: rt[k] for k in ("device", "n_cells", "n_genes", "total_inference_s", "cells_per_s")})
    dets = []
    for k in (1, 2):
        dk = _hc.cached("refcon", f"det{k}")
        escribe_entrada("hueso", np.arange(DET_N), os.path.join(dk, "entrada.h5ad"))
        infiere(os.path.join(dk, "entrada.h5ad"), dk)
        dets.append(np.load(os.path.join(dk, "ens3_cn.npz"), allow_pickle=True)["cn"])
    dif = float(np.nanmax(np.abs(dets[0] - dets[1])))
    res = {"n_celulas": DET_N, "max_dif_abs": dif, "identico_bit_a_bit": bool(np.array_equal(dets[0], dets[1], equal_nan=True)),
           "tolerancia": TOL_DET, "PASA": bool(dif <= TOL_DET), "device": rt["device"]}
    with open(os.path.join(_hc.cached("refcon"), "determinismo.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
    print("determinismo REFCON:", res)
    return 0


def paso_higado():
    import numpy as np
    g = os.path.join(_hc.cached("refcon"), "gate.json")
    if not os.path.exists(g) or not _hc.lee_json(g).get("aplicable"):
        print("RECHAZADO: el control positivo del hueso (gate_refcon) no declara REFCON aplicable; el hígado no se infiere.")
        return 3
    X, _ = _carga("higado")
    n = X.shape[0]
    trozos = [np.arange(i, min(i + TROZO, n)) for i in range(0, n, TROZO)]
    # Varios trabajadores a la vez (cada célula se infiere por separado: el orden no cambia el resultado). Cada trozo se
    # toma con un cerrojo O_EXCL; un trozo hecho (ens3 + runtime) se salta. «desc» recorre del último al primero.
    orden = list(range(len(trozos)))[::-1] if (len(sys.argv) > 2 and sys.argv[2] == "desc") else list(range(len(trozos)))
    for k in orden:
        filas = trozos[k]
        d = _hc.cached("refcon", "higado", f"trozo_{k:02d}")
        if os.path.exists(os.path.join(d, "ens3_cn.npz")) and os.path.exists(os.path.join(d, "runtime.json")):
            print(f"trozo {k}: ya estaba", flush=True)
            continue
        cerrojo = os.path.join(d, "en_curso.lock")
        try:
            fd = os.open(cerrojo, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            print(f"trozo {k}: lo tiene otro trabajador", flush=True)
            continue
        try:
            escribe_entrada("higado", filas, os.path.join(d, "entrada.h5ad"))
            rt = infiere(os.path.join(d, "entrada.h5ad"), d)
        finally:
            os.remove(cerrojo)
        print(f"trozo {k + 1}/{len(trozos)}: {rt['n_cells']} células, {rt['device']}, {rt['total_inference_s']} s", flush=True)
    return 0


def _memoria_libre():
    r = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True).stdout
    pag = 16384
    tot = 0
    for linea in r.splitlines():
        if linea.startswith(("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")):
            tot += int(linea.split(":")[1].strip().rstrip("."))
    return tot * pag


def paso_clasifica():
    import numpy as np
    sys.path.insert(0, _hc.REFCON_DIR)
    d = _hc.cached("refcon", "higado")
    trozos = sorted(x for x in os.listdir(d) if x.startswith("trozo_"))
    preds, cells, genes = [], [], None
    for t_ in trozos:
        z = np.load(os.path.join(d, t_, "ens3_cn.npz"), allow_pickle=True)
        preds.append(z["cn"].astype(np.float32))
        cells += list(z["cells"].astype(str))
        genes = z["genes"].astype(str)
    n, G = sum(p.shape[0] for p in preds), len(genes)
    necesita = 6 * n * G * 8
    libre = _memoria_libre()
    out = {"n": n, "genes": G, "memoria_estimada_GB": necesita / 1e9, "memoria_libre_GB": libre / 1e9}
    if libre < necesita:
        out.update({"ejecutado": False, "motivo": "no cabe en memoria (regla: se declara no ejecutado)"})
    else:
        from refcon.downstream.classify import call_sample
        X, o = _carga("higado")
        gi, _, _ = genes_autosomas(o)
        assert list(simbolos_unicos(o["simbolo"][gi])) == list(genes)
        pred = np.concatenate(preds)
        del preds
        labels, info = call_sample(X[:, gi].toarray(), genes, pred)
        np.save(os.path.join(_hc.cached("refcon"), "clasifica_labels.npy"), labels.astype(np.int8))
        out.update({"ejecutado": True, "info": info, "aneuploides": int((labels == 1).sum()), "diploides": int((labels == 0).sum())})
    with open(os.path.join(_hc.cached("refcon"), "clasifica.json"), "w") as f:
        json.dump(_hc.r_(out), f, indent=1, sort_keys=True, default=str)
    print("clasifica:", {k: v for k, v in out.items() if k != "info"})
    return 0


def main(argv):
    _hc.arranque(__file__, argv, "flex_refcon")
    pasos = {"hueso": paso_hueso, "higado": paso_higado, "clasifica": paso_clasifica}
    if not argv or argv[0] not in pasos:
        print(__doc__)
        return 2
    return int(pasos[argv[0]]() or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
