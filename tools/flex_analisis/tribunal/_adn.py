"""P2 — ADN genómico y FGFR1 (espec. v2 EXPLORATORIA del tribunal, REGLAS_TRIBUNAL_v2.json «adn» + ENMIENDA_1).

1. Tipo de sonda (spliced/unspliced) de las dianas del amplicón y controles.
2. Fondo de ADN por célula, delta = UMI por sonda unspliced nula y célula. Nulas: receptores
   olfativos (^OR[0-9]). Contraste: unspliced de genes con 0 cuentas spliced en ambos tejidos.
   Validación contra Cell Ranger: delta × nº sondas unspliced incluidas / UMI por célula.
3. UMI esperadas por ADN = delta × nº sondas unspliced × copias/2, frente a observadas, por gen.
4. FGF3: su sonda spliced frente a las unspliced.
5. Lectura de copias por ADN en el hueso (inmune a la fuga del hígado): genes de ventana silentes
   en ARN en las malignas del hígado; tasa por sonda en malignas del hueso / delta.
Salidas: tribunal/adn.json y adn_celulas.npz (vectores por célula en el orden de obs_A, sin barcodes).
"""
import gc

import h5py
import numpy as np
import pandas as pd

from _comun import (REF_TIPOS, TEJIDOS, boot, guarda, obs_A, out, probe_set, ruta, stream_h5)

DIANAS = ["FGFR1", "ZNF703", "NSD3", "CCND1", "FGF3", "FGF4", "FGF19"]
COPIAS = {"FGFR1": 33, "ZNF703": 33, "NSD3": 33, "CCND1": 37, "FGF3": 37, "FGF4": 37, "FGF19": 37}
CONTROLES = ["ACTB", "B2M", "KRT8", "KRT18", "KRT19", "ERBB2", "ESR1", "GATA3", "MKI67", "TOP2A"]
VENT = {"8p11": ("chr8", 37.0e6, 39.0e6), "{{LOCUS}}": ("chr11", 69.0e6, 70.5e6)}


def coords():
    import os
    import anndata as an
    from _comun import B_DIR
    b = an.read_h5ad(os.path.join(B_DIR, "flex_B_congelado.h5ad"), backed="r")
    v = b.var[["symbol", "chromosome", "start", "end", "ensembl_id"]].copy()
    b.file.close()
    return v.set_index("ensembl_id")


def genes_ventana(cv, w):
    ch, a, b = VENT[w]
    m = (cv["chromosome"].astype(str) == ch) & (cv["start"] > a) & (cv["end"] < b)
    return set(cv.index[m])


def matriz_sondas(t, bcs, probe_ids=None):
    path = ruta(t, "_raw_probe_bc_matrix.h5")
    filas = None
    if probe_ids is not None:
        with h5py.File(path, "r") as f:
            fid = np.array([x.decode() for x in f["matrix/features/id"][:]])
        filas = np.where(np.isin(fid, list(probe_ids)))[0]
    M, ids, _ = stream_h5(path, bcs, filas=filas)
    return M.tocsc(), pd.Index(ids)


def run(*a):
    ps = probe_set()
    ps = ps[ps["included"]].copy()
    cv = coords()
    oa = obs_A()
    res = {"reglas": "REGLAS_TRIBUNAL_v2.json/adn + ENMIENDA_1"}

    tipo = {}
    for g in DIANAS + CONTROLES:
        s = ps[ps["gene_name"] == g]
        tipo[g] = {"spliced": int((s.region == "spliced").sum()), "unspliced": int((s.region == "unspliced").sum())}
    res["tipo_sonda"] = tipo
    print("1. sondas (spliced, unspliced):", {g: (t["spliced"], t["unspliced"]) for g, t in tipo.items()})

    nul_ids = set(ps[ps["gene_name"].str.match(r"^OR\d") & (ps.region == "unspliced")]["probe_id"])
    vent = {w: genes_ventana(cv, w) for w in VENT}
    vent_ids = set(ps[ps["gene_id"].isin(set().union(*vent.values()))]["probe_id"])
    keep = nul_ids | vent_ids | set(ps[ps["gene_name"].isin(DIANAS + CONTROLES)]["probe_id"])
    n_unsp_total = int((ps.region == "unspliced").sum())
    res["nulos"] = {"conjunto": "receptores olfativos ^OR[0-9], unspliced", "n_sondas": len(nul_ids),
                    "n_sondas_unspliced_incluidas_panel": n_unsp_total}

    cel = {}
    tasas_mal_higado = None
    for t in ("higado", "hueso"):
        o = oa[oa["muestra"] == t]
        M, ids = matriz_sondas(t, o["bc"].values, keep)
        meta = ps.set_index("probe_id").reindex(ids)
        reg, gid, sym = meta["region"].values, meta["gene_id"].values, meta["gene_name"].values
        umi = o["total_counts"].values.astype(float)
        mal = o["pob_usada"].values.astype(bool)
        ref = o["tipo"].isin(REF_TIPOS).values
        mn = np.isin(ids, list(nul_ids))
        delta = np.asarray(M[:, mn].sum(axis=1)).ravel() / mn.sum()
        r = {"n": int(len(o)), "n_malignas_A": int(mal.sum()),
             "delta_media": {"todas": float(delta.mean()), "malignas_A": float(delta[mal].mean()),
                             "referencia": float(delta[ref].mean())},
             "frac_UMI_ADN_estimada": float(delta.mean() * n_unsp_total / umi.mean()),
             "frac_UMI_ADN_estimada_malignas_A": float(delta[mal].mean() * n_unsp_total / umi[mal].mean())}
        # 3. dianas
        d = {}
        for g in DIANAS + CONTROLES:
            mg = sym == g
            if not mg.any():
                continue
            mu = mg & (reg == "unspliced")
            ob = np.asarray(M[:, mg].sum(axis=1)).ravel()
            for nombre, msk in (("malignas_A", mal), ("referencia", ref)):
                cop = COPIAS.get(g, 2) if nombre == "malignas_A" else 2
                esp = delta[msk] * mu.sum() * cop / 2.0
                d.setdefault(g, {})[nombre] = {
                    "n": int(msk.sum()), "sondas_u": int(mu.sum()), "sondas_s": int((mg & (reg == "spliced")).sum()),
                    "copias_supuestas": cop, "UMI_obs": int(ob[msk].sum()),
                    "UMI_obs_por_celula": float(ob[msk].mean()), "UMI_esperadas_ADN": float(esp.sum()),
                    "frac_UMI_atribuible_ADN": float(min(1.0, esp.sum() / max(ob[msk].sum(), 1))),
                    "det_obs": boot(int((ob[msk] > 0).sum()), int(msk.sum())),
                    "det_esperada_solo_ADN": float((1 - np.exp(-esp)).mean())}
        r["dianas"] = d
        # 4. FGF3 por sonda
        r["FGF3_por_sonda"] = {f"{reg[j]}_{ids[j][-7:]}": {"UMI_malignas_A": int(M[mal, j].sum()),
                                                            "UMI_resto": int(M[~mal, j].sum())}
                               for j in np.where(sym == "FGF3")[0]}
        # tasas por sonda en malignas (para elegir genes silentes en el hígado)
        tasas = {}
        for w, gs in vent.items():
            for g in gs:
                mg = (gid == g) & (reg == "unspliced")
                if mg.any():
                    tasas[g] = float(M[mal][:, mg].sum() / (mal.sum() * mg.sum()))
        if t == "higado":
            tasas_mal_higado = tasas
        else:
            lect = {}
            for w, gs in vent.items():
                silent = [g for g in gs if g in tasas_mal_higado and tasas_mal_higado[g] < 1e-3]
                mg = np.isin(gid, silent) & (reg == "unspliced")
                cw = np.asarray(M[:, mg].sum(axis=1)).ravel()
                cel[f"hueso_ventana_silente_{w}"] = cw
                lect[w] = {"genes_silentes": sorted(cv.loc[silent, "symbol"].astype(str)), "n_sondas": int(mg.sum())}
                for nombre, msk in (("malignas_A", mal), ("referencia", ref), ("resto_no_malignas", ~mal & ~ref)):
                    tasa = cw[msk].sum() / (msk.sum() * max(mg.sum(), 1))
                    dm = delta[msk].mean()
                    lect[w][nombre] = {"n": int(msk.sum()), "UMI_obs": int(cw[msk].sum()),
                                       "UMI_esperadas_diploide": float(dm * mg.sum() * msk.sum()),
                                       "copias_estimadas": float(2 * tasa / dm) if dm > 0 else None}
            r["lectura_copias_ADN_ventanas"] = lect
            print("5. copias por ADN (hueso):", {w: {k: (v["UMI_obs"], round(v["copias_estimadas"], 1)) for k, v in x.items()
                                                    if isinstance(v, dict)} for w, x in lect.items()},
                  {w: x["genes_silentes"] for w, x in lect.items()})
        cel[f"{t}_delta"] = delta
        res[t] = r
        print(f"== {t}: delta media {r['delta_media']}; fracción UMI de ADN estimada {r['frac_UMI_ADN_estimada']:.3f}")
        for g in DIANAS + ["ACTB", "ERBB2", "ESR1"]:
            x = d.get(g, {}).get("malignas_A")
            if x:
                print(f"   {g}: UMI/célula {x['UMI_obs_por_celula']:.2f}, esperadas ADN {x['UMI_esperadas_ADN']:.1f} de {x['UMI_obs']}, "
                      f"atribuible {x['frac_UMI_atribuible_ADN']:.3f}, det {x['det_obs'][0]:.2f} (solo ADN {x['det_esperada_solo_ADN']:.2f})")
        print("   FGF3 por sonda:", r["FGF3_por_sonda"])
        del M
        gc.collect()
    res["cell_ranger_referencia"] = {"hueso": 0.271, "higado": 0.002}
    guarda(res, "adn.json")
    np.savez_compressed(out("adn_celulas.npz"), **cel)
    print("guardado adn.json")
