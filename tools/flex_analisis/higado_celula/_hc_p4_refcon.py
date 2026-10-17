"""Post hoc 2, P4 (POSTHOC_2_PLAN.json): clasificador propio de REFCON (call_sample, por defecto) en submuestras.

Hijo del paso p4_refcon de flex_hc_posthoc2: lo lanza _hc_posthoc2.py con el python del entorno refcon DENTRO del mismo
sandbox sin red. Lee las submuestras que eligió el padre (cache/posthoc2/p4_permutacion.npy), la inferencia ens3 ya
cacheada del hígado y las cuentas crudas; escribe etiquetas por índice de fila en caché (nunca barcodes). stdout: solo
agregados. NO decide ningún veredicto.
Uso (solo desde el padre): python -I _hc_p4_refcon.py <dir_cache_posthoc2>
"""
import gc
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
REPLICAS = 3
NS = (5000, 4000, 3000)


def main(argv):
    if os.environ.get("BTP_VENTANILLA") != "1" or os.environ.get("HC_SANDBOX") != "1":
        print("RECHAZADO: _hc_p4_refcon.py solo corre como hijo de flex_hc_posthoc2 (ventanilla y sandbox sin red).", file=sys.stderr)
        return 2
    sys.path.insert(0, AQUI)
    import numpy as np
    import _hc
    import refcon_run as RR
    sys.path.insert(0, _hc.REFCON_DIR)
    d2 = argv[0]
    perm = np.load(os.path.join(d2, "p4_permutacion.npy"))
    dref = _hc.cached("refcon", "higado")
    trozos = sorted(x for x in os.listdir(dref) if x.startswith("trozo_"))
    z0 = np.load(os.path.join(dref, trozos[0], "ens3_cn.npz"), allow_pickle=True)
    genes = z0["genes"].astype(str)
    G = len(genes)
    del z0
    libre = RR._memoria_libre()
    elegido = next((n for n in NS if 6 * n * G * 8 <= libre), None)
    info = {"genes": G, "memoria_libre_GB": libre / 1e9, "N_candidatos": list(NS), "replicas": REPLICAS,
            "memoria_estimada_GB_por_N": {str(n): 6 * n * G * 8 / 1e9 for n in NS}, "N": elegido}
    salida = os.path.join(d2, "p4_info.json")
    if elegido is None:
        info.update({"ejecutado": False, "motivo": "NO EJECUTABLE: ni 3.000 células caben en la memoria libre medida"})
        with open(salida, "w") as f:
            json.dump(_hc.r_(info), f, indent=1, sort_keys=True)
        print("P4:", {k: info[k] for k in ("ejecutado", "motivo", "memoria_libre_GB")})
        return 0
    from refcon.downstream.classify import call_sample
    from refcon.downstream.markers import ssmww_confident_normal
    X, o = RR._carga("higado")
    gi, _, _ = RR.genes_autosomas(o)
    assert list(RR.simbolos_unicos(o["simbolo"][gi])) == list(genes), "genes de REFCON fuera de orden"
    # celda «c<i>» -> (trozo, fila)
    donde = {}
    for t_ in trozos:
        cells = np.load(os.path.join(dref, t_, "ens3_cn.npz"), allow_pickle=True)["cells"].astype(str)
        for k, c in enumerate(cells):
            donde[int(c[1:])] = (t_, k)
    assert len(donde) == X.shape[0]
    info["ejecutado"] = True
    info["por_replica"] = []
    for r in range(REPLICAS):
        idx = np.sort(perm[r * elegido:(r + 1) * elegido])
        pred = np.full((len(idx), G), np.nan, dtype=np.float32)
        porto = {}
        for j, i in enumerate(idx):
            t_, k = donde[int(i)]
            porto.setdefault(t_, []).append((j, k))
        for t_, pares in porto.items():
            cn = np.load(os.path.join(dref, t_, "ens3_cn.npz"), allow_pickle=True)["cn"]
            js, ks = zip(*pares)
            pred[list(js)] = cn[list(ks)].astype(np.float32)
            del cn
        counts = X[idx][:, gi].toarray()
        labels, inf = call_sample(counts, genes, pred)
        conf = ssmww_confident_normal(counts, genes)["norm_mask"].astype(bool)
        np.savez(os.path.join(d2, f"p4_rep{r}.npz"), idx=idx.astype(np.int64), labels=np.asarray(labels).astype(np.int8),
                 conf_normal=conf)
        fila = {"replica": r, "n": int(len(idx)), "status": inf.get("status"), "n_conf": inf.get("n_conf"),
                "K": inf.get("K"), "cluster_corrs": inf.get("cluster_corrs"),
                "aneuploides": int((labels == 1).sum()), "diploides": int((labels == 0).sum()), "sin_etiqueta_PURE": int((labels == -1).sum())}
        for extra in ("aneuploidy_score", "soft_call", "pure_thr"):
            if extra in inf:
                fila[extra] = inf[extra]
        info["por_replica"].append(fila)
        print("P4 réplica", r, {k: fila[k] for k in ("n", "status", "n_conf", "K", "aneuploides", "diploides", "sin_etiqueta_PURE")}, flush=True)
        del counts, pred, labels, conf
        gc.collect()
    with open(salida, "w") as f:
        json.dump(_hc.r_(info), f, indent=1, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
