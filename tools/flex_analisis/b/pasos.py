"""Pasos del analista B del Flex. Importado por `flex_b.py` (que fija -I, muro y rutas).

Cada paso lee lo que dejó el anterior en `analisis_B/` y escribe sus propias salidas con sha256.
stdout: solo agregados (fracciones, IC, n, medias). Nunca barcodes ni identificadores de bloque.
"""
import glob
import os

import numpy as np

from flex_b import MUESTRAS, OUT, RAW, SEMILLA, guardar_json, imprime, sha256  # noqa: F401

PER_SAMPLE = os.path.join(RAW, "MP", "SC_MULTI_CS", "SC_MULTI_CORE",
                          "STRUCTIFY_PER_SAMPLE_OUTS", "fork0", "files")


def _dir_muestra(nombre):
    bloque = MUESTRAS[nombre]
    for base in (PER_SAMPLE, RAW):
        for cand in glob.glob(os.path.join(base, "**", bloque), recursive=True):
            if os.path.isdir(cand):
                return cand
    return None


def _anon(rel):
    for bloque in MUESTRAS.values():
        rel = rel.replace(bloque, "<BLOQUE>")
    return rel


# ---------------------------------------------------------------------------
# explorar: inventario sin abrir expresión
# ---------------------------------------------------------------------------
def explorar(a):
    inv = {}
    for nombre in MUESTRAS:
        d = _dir_muestra(nombre)
        if d is None:
            inv[nombre] = "NO ENCONTRADA"
            continue
        ficheros = []
        for r, _, fs in os.walk(d):
            for f in fs:
                p = os.path.join(r, f)
                ficheros.append((_anon(os.path.relpath(p, d)), os.path.getsize(p)))
        inv[nombre] = {"n_ficheros": len(ficheros), "ficheros": sorted(ficheros)}
    raiz = []
    for r, ds, fs in os.walk(RAW):
        if r[len(RAW):].count(os.sep) > 7:
            continue
        for f in fs:
            raiz.append(_anon(os.path.relpath(os.path.join(r, f), RAW)))
    inv["raiz_ficheros"] = sorted(raiz)[:400]
    imprime(inv)


# ---------------------------------------------------------------------------
# utilidades comunes
# ---------------------------------------------------------------------------
HEPATO = ["ALB", "APOA1", "SERPINA1", "HP", "TF"]
OSEO = ["COL1A1", "SPP1", "ACP5", "CTSK"]
EPITELIO = ["EPCAM", "KRT8", "KRT18", "KRT19"]


def _h5(nombre, cual="filtered_feature_barcode_matrix"):
    cands = glob.glob(os.path.join(PER_SAMPLE, "%s_%s.h5" % (MUESTRAS[nombre], cual)))
    if not cands:
        raise FileNotFoundError("sin %s para %s" % (cual, nombre))
    return cands[0]


def _leer(nombre):
    import scanpy as sc
    ad = sc.read_10x_h5(_h5(nombre), gex_only=True)
    ad.var["symbol"] = ad.var_names.astype(str)
    ad.var_names_make_unique()
    ad.obs["muestra"] = nombre
    return ad


def _mad_corte(x, k=3.0):
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return med - k * mad, med + k * mad, med, mad


def _h5ad(nombre, etapa):
    return os.path.join(OUT, "%s_%s.h5ad" % (nombre, etapa))


def _cp10k_pseudobulk(ad, genes):
    """Pseudobulk CP10k: suma de UMI del gen en todas las células / total de UMI * 1e4."""
    import scipy.sparse as sp
    X = ad.layers["counts"] if "counts" in ad.layers else ad.X
    tot = float(X.sum())
    out = {}
    for g in genes:
        if g in ad.var_names:
            col = X[:, ad.var_names.get_loc(g)]
            s = float(col.sum()) if sp.issparse(col) else float(np.sum(col))
            frac = float((col > 0).mean()) if sp.issparse(col) else float(np.mean(col > 0))
            out[g] = {"cp10k_pseudobulk": round(s / tot * 1e4, 3), "frac_celulas_>=1UMI": round(frac, 4)}
        else:
            out[g] = "fuera del panel"
    return out


# ---------------------------------------------------------------------------
# qc: paso 1 (MAD + Scrublet) y paso 2 (control de tejido). El paso 3 (ARN ambiental)
# necesita la anotación y se informa en `anotar`.
# ---------------------------------------------------------------------------
def qc(a):
    import scanpy as sc
    sc.settings.n_jobs = 1
    informe = {"semilla": SEMILLA, "regla_MAD": "mediana ± 3·MAD (escala 1.4826) sobre log1p(UMI) y "
               "log1p(genes); se quitan las células fuera por cualquiera de los dos lados",
               "scrublet": "scanpy.pp.scrublet, umbral automático, random_state=20261016",
               "muestras": {}}
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        ad = _leer(nombre)
        ad.layers["counts"] = ad.X.copy()
        r = {"n_celulas_cellranger": int(ad.n_obs), "n_genes_panel": int(ad.n_vars)}
        mt = ad.var_names.str.startswith("MT-")
        r["sondas_MT"] = int(mt.sum())
        sc.pp.calculate_qc_metrics(ad, inplace=True, percent_top=None)
        if mt.sum() > 0:
            ad.var["mt"] = mt
            sc.pp.calculate_qc_metrics(ad, qc_vars=["mt"], inplace=True, percent_top=None)
        lu = np.log1p(ad.obs["total_counts"].to_numpy())
        lg = np.log1p(ad.obs["n_genes_by_counts"].to_numpy())
        lo_u, hi_u, med_u, mad_u = _mad_corte(lu)
        lo_g, hi_g, med_g, mad_g = _mad_corte(lg)
        pasa = (lu >= lo_u) & (lu <= hi_u) & (lg >= lo_g) & (lg <= hi_g)
        r["UMI_por_celula"] = {"mediana": float(np.expm1(med_u)), "corte_bajo": float(np.expm1(lo_u)),
                               "corte_alto": float(np.expm1(hi_u)),
                               "n_fuera_bajo": int((lu < lo_u).sum()), "n_fuera_alto": int((lu > hi_u).sum())}
        r["genes_por_celula"] = {"mediana": float(np.expm1(med_g)), "corte_bajo": float(np.expm1(lo_g)),
                                 "corte_alto": float(np.expm1(hi_g)),
                                 "n_fuera_bajo": int((lg < lo_g).sum()), "n_fuera_alto": int((lg > hi_g).sum())}
        if mt.sum() > 0:
            pm = ad.obs["pct_counts_mt"].to_numpy()
            _, hi_m, med_m, _ = _mad_corte(pm)
            r["pct_mito"] = {"mediana": float(med_m), "corte_alto": float(hi_m), "n_fuera": int((pm > hi_m).sum())}
            pasa &= pm <= hi_m
        else:
            r["pct_mito"] = "sin sondas MT en el panel: no se aplica"
        ad.obs["qc_mad_pasa"] = pasa
        r["n_tras_MAD"] = int(pasa.sum())
        # Scrublet sobre las que pasan MAD
        sub = ad[pasa].copy()
        sc.pp.scrublet(sub, random_state=SEMILLA, verbose=False)
        dbl = sub.obs["predicted_doublet"].astype(bool).to_numpy()
        r["scrublet"] = {"n_dobletes": int(dbl.sum()), "frac_dobletes": round(float(dbl.mean()), 4),
                         "umbral": float(sub.uns["scrublet"]["threshold"]) if "scrublet" in sub.uns else None,
                         "score_mediana": round(float(np.median(sub.obs["doublet_score"])), 4)}
        ad.obs["doublet_score"] = np.nan
        ad.obs.loc[sub.obs_names, "doublet_score"] = sub.obs["doublet_score"]
        ad.obs["doblete"] = False
        ad.obs.loc[sub.obs_names, "doblete"] = dbl
        ad.obs["qc_pasa"] = ad.obs["qc_mad_pasa"] & ~ad.obs["doblete"].astype(bool)
        r["n_tras_QC"] = int(ad.obs["qc_pasa"].sum())
        limpio = ad[ad.obs["qc_pasa"]].copy()
        # paso 2: control de tejido sobre las células que pasan QC
        hep = _cp10k_pseudobulk(limpio, HEPATO)
        oseo = _cp10k_pseudobulk(limpio, OSEO)
        r["control_tejido"] = {"programa_hepatocitario": hep, "programa_oseo_osteoclasto": oseo,
                               "suma_cp10k_hepato": round(sum(v["cp10k_pseudobulk"] for v in hep.values()
                                                              if isinstance(v, dict)), 2),
                               "suma_cp10k_oseo": round(sum(v["cp10k_pseudobulk"] for v in oseo.values()
                                                            if isinstance(v, dict)), 2)}
        X = limpio.layers["counts"]
        idx = [limpio.var_names.get_loc(g) for g in HEPATO if g in limpio.var_names]
        ndet = np.asarray((X[:, idx] > 0).sum(axis=1)).ravel()
        r["control_tejido"]["celulas_>=3_de_5_genes_hepato"] = {"n": int((ndet >= 3).sum()),
                                                                 "frac": round(float((ndet >= 3).mean()), 4)}
        idx_o = [limpio.var_names.get_loc(g) for g in OSEO if g in limpio.var_names]
        ndet_o = np.asarray((X[:, idx_o] > 0).sum(axis=1)).ravel()
        r["control_tejido"]["celulas_>=3_de_4_genes_oseo"] = {"n": int((ndet_o >= 3).sum()),
                                                               "frac": round(float((ndet_o >= 3).mean()), 4)}
        ruta = _h5ad(nombre, "qc")
        ad.write_h5ad(ruta, compression="gzip")
        r["salida_h5ad"] = {"ruta": ruta, "sha256": sha256(ruta)}
        informe["muestras"][nombre] = r
    # decisión STOP (regla fijada antes de mirar): hígado tiene que tener ≥1 % de células con ≥3/5
    # genes hepatocitarios; hueso no puede tener más células hepatocitarias (fracción) que el hígado.
    m = informe["muestras"]
    if "higado" in m and "hueso" in m:
        fh = m["higado"]["control_tejido"]["celulas_>=3_de_5_genes_hepato"]["frac"]
        fb = m["hueso"]["control_tejido"]["celulas_>=3_de_5_genes_hepato"]["frac"]
        informe["control_tejido_decision"] = {
            "regla": "PASA si frac_hepato(hígado) ≥ 0.01 y frac_hepato(hueso) < frac_hepato(hígado)",
            "frac_hepato_higado": fh, "frac_hepato_hueso": fb,
            "veredicto": "PASA" if (fh >= 0.01 and fb < fh) else "STOP"}
    ruta, h = guardar_json("01_qc.json", informe)
    informe["salida_json"] = {"ruta": ruta, "sha256": h}
    imprime(informe)


# ---------------------------------------------------------------------------
# anotar: paso 4 (Leiden + marcadores canónicos) y paso 3 (ARN ambiental, diagnóstico)
# ---------------------------------------------------------------------------
MARCADORES = {
    "hepatocito": HEPATO,
    "colangiocito": ["KRT7", "SOX9", "CFTR"],
    "epitelio_tumoral": EPITELIO,
    "T": ["CD3D", "CD3E", "CD2", "TRAC", "IL7R"],
    "NK": ["NKG7", "GNLY", "KLRD1", "PRF1"],
    "B": ["MS4A1", "CD79A", "CD19"],
    "plasma": ["MZB1", "JCHAIN", "IGKC"],
    "mieloide": ["CD68", "CD14", "LYZ", "C1QA", "CD163"],
    "neutrofilo": ["S100A8", "S100A9", "CSF3R", "FCGR3B"],
    "mastocito": ["CPA3", "KIT", "TPSB2"],
    "pDC": ["LILRA4", "IL3RA"],
    "endotelio": ["PECAM1", "VWF", "CDH5", "CLDN5"],
    "fibroblasto": ["COL1A2", "DCN", "LUM", "PDGFRB"],
    "osteoblasto": ["RUNX2", "SP7", "BGLAP", "IBSP"],
    "osteoclasto": ["ACP5", "CTSK", "MMP9"],
    "pericito_musculo_liso": ["RGS5", "ACTA2", "MCAM"],
    "eritroide": ["HBB", "HBA2", "GYPA"],
    "melanocito": ["MLANA", "PMEL", "SOX10"],
}
NO_EPITELIALES_REF = ["T", "NK", "B", "plasma", "mieloide", "neutrofilo", "mastocito", "pDC", "endotelio"]
UMBRAL_SCORE_TIPO = 0.10  # media de score_genes del clúster; por debajo → indeterminado


def _normaliza(ad):
    import scanpy as sc
    ad.X = ad.layers["counts"].copy()
    sc.pp.normalize_total(ad, target_sum=1e4)
    sc.pp.log1p(ad)
    return ad


def anotar(a):
    import scanpy as sc
    sc.settings.n_jobs = 1
    informe = {"semilla": SEMILLA, "leiden": "resolución 1.0, flavor igraph, 2 iteraciones, "
               "sobre vecinos k=15 de 50 PCs (2.000 HVG seurat)", "umbral_score_tipo": UMBRAL_SCORE_TIPO,
               "marcadores": MARCADORES, "muestras": {}}
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        ad = sc.read_h5ad(_h5ad(nombre, "qc"))
        ad = ad[ad.obs["qc_pasa"].astype(bool)].copy()
        _normaliza(ad)
        ad.raw = ad
        sc.pp.highly_variable_genes(ad, n_top_genes=2000, flavor="seurat")
        sc.pp.pca(ad, n_comps=50, mask_var="highly_variable", svd_solver="arpack", random_state=SEMILLA)
        sc.pp.neighbors(ad, n_neighbors=15, n_pcs=50, random_state=SEMILLA)
        sc.tl.leiden(ad, resolution=1.0, flavor="igraph", n_iterations=2, directed=False,
                     random_state=SEMILLA, key_added="leiden")
        faltan = {}
        for tipo, genes in MARCADORES.items():
            presentes = [g for g in genes if g in ad.var_names]
            if len(presentes) < len(genes):
                faltan[tipo] = sorted(set(genes) - set(presentes))
            sc.tl.score_genes(ad, presentes, score_name="score_" + tipo, random_state=SEMILLA)
        cols = ["score_" + t for t in MARCADORES]
        medias = ad.obs.groupby("leiden", observed=True)[cols].mean()
        asign = {}
        detalle = {}
        for cl, fila in medias.iterrows():
            orden = fila.sort_values(ascending=False)
            top = orden.index[0].replace("score_", "")
            asign[cl] = top if orden.iloc[0] >= UMBRAL_SCORE_TIPO else "indeterminado"
            detalle[str(cl)] = {"n": int((ad.obs["leiden"] == cl).sum()), "tipo": asign[cl],
                                "top3": [(k.replace("score_", ""), round(float(v), 3))
                                         for k, v in orden.iloc[:3].items()]}
        ad.obs["tipo"] = ad.obs["leiden"].map(asign).astype(str)
        # identidad epitelial por célula: ≥2 de los 4 genes (EPCAM, KRT8, KRT18, KRT19) con ≥1 UMI
        X = ad.layers["counts"]
        idx = [ad.var_names.get_loc(g) for g in EPITELIO if g in ad.var_names]
        ad.obs["n_genes_epitelio"] = np.asarray((X[:, idx] > 0).sum(axis=1)).ravel()
        ad.obs["epitelial_celula"] = ad.obs["n_genes_epitelio"] >= 2
        frac_tipo = ad.obs["tipo"].value_counts()
        r = {"n_celulas": int(ad.n_obs), "n_clusters_leiden": int(len(asign)),
             "marcadores_fuera_del_panel": faltan,
             "tipos": {t: {"n": int(n), "frac": round(float(n) / ad.n_obs, 4)} for t, n in frac_tipo.items()},
             "clusters": detalle,
             "epitelial_celula_>=2de4": {"n": int(ad.obs["epitelial_celula"].sum()),
                                         "frac": round(float(ad.obs["epitelial_celula"].mean()), 4)}}
        # paso 3: ARN ambiental. Fracción de UMI hepatocitarios en inmunes + endotelio.
        ref = ad.obs["tipo"].isin(NO_EPITELIALES_REF).to_numpy()
        hidx = [ad.var_names.get_loc(g) for g in HEPATO if g in ad.var_names]
        Xr = X[ref]
        tot = float(Xr.sum())
        hep = float(Xr[:, hidx].sum())
        alb = float((Xr[:, ad.var_names.get_loc("ALB")] > 0).mean()) if "ALB" in ad.var_names else None
        r["arn_ambiental"] = {"n_celulas_referencia": int(ref.sum()),
                              "frac_UMI_hepato_en_inmunes_endotelio": round(hep / tot, 5) if tot else None,
                              "frac_inmunes_endotelio_con_ALB>=1": round(alb, 4) if alb is not None else None,
                              "nota": "solo diagnóstico; no se corrige"}
        ruta = _h5ad(nombre, "anotado")
        ad.write_h5ad(ruta, compression="gzip")
        r["salida_h5ad"] = {"ruta": ruta, "sha256": sha256(ruta)}
        informe["muestras"][nombre] = r
    ruta, h = guardar_json("02_anotacion.json", informe)
    informe["salida_json"] = {"ruta": ruta, "sha256": h}
    imprime(informe)


GENES_DIAG = ["EPCAM", "KRT8", "KRT18", "KRT19", "KRT7", "CDH1", "ESR1", "GATA3", "FOXA1", "TRPS1", "AR",
              "ERBB2", "CHGA", "SYP", "{{DIANA3}}", "{{DIANA4}}", "NEUROD1", "ALB", "PTPRC", "CD3E", "CD68", "PECAM1",
              "COL1A2", "MKI67", "TOP2A", "VIM", "HBB", "MALAT1", "NEAT1", "XIST", "TTR", "APOB", "CYP3A4"]


def diagnostico(a):
    """EXPLORATORIO: qué expresan los clústeres (top genes por clúster y detección de genes clave).
    Solo agregados por clúster."""
    import scanpy as sc
    out = {}
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        ad = sc.read_h5ad(_h5ad(nombre, "anotado"))
        sc.tl.rank_genes_groups(ad, "leiden", method="wilcoxon", n_genes=12, use_raw=False)
        X = ad.layers["counts"]
        genes = [g for g in GENES_DIAG if g in ad.var_names]
        idx = [ad.var_names.get_loc(g) for g in genes]
        det = (X[:, idx] > 0).toarray()
        r = {}
        for cl in ad.obs["leiden"].cat.categories:
            m = (ad.obs["leiden"] == cl).to_numpy()
            top = [str(x) for x in ad.uns["rank_genes_groups"]["names"][cl]]
            r[str(cl)] = {"n": int(m.sum()), "tipo": ad.obs.loc[m, "tipo"].iloc[0],
                          "UMI_mediana": float(np.median(ad.obs.loc[m, "total_counts"])),
                          "top_genes": top,
                          "frac_det": {g: round(float(det[m, i].mean()), 2) for i, g in enumerate(genes)}}
        out[nombre] = r
    imprime(out)


# ---------------------------------------------------------------------------
# cnv: paso 5. inferCNV (infercnvpy) + CopyKAT, referencia no epitelial, consenso.
# ---------------------------------------------------------------------------
ESTROMA = ["fibroblasto", "pericito_musculo_liso", "osteoblasto", "osteoclasto", "eritroide", "melanocito"]
REF_DIR = os.path.join(OUT, "ref")
R_BIN = os.path.join(os.path.expanduser("~"), ".claude-science", "conda", "envs", "r", "bin", "Rscript")
AQUI = os.path.dirname(os.path.abspath(__file__))


def _gtf(a):
    """GTF público (Ensembl 110, GRCh38). Se copia a analisis_B/ref para que el análisis sea autocontenido."""
    import shutil
    os.makedirs(REF_DIR, exist_ok=True)
    dest = os.path.join(REF_DIR, "Homo_sapiens.GRCh38.110.gtf.gz")
    if not os.path.exists(dest):
        src = [x.split("=", 1)[1] for x in a.extra if x.startswith("gtf=")]
        if not src:
            raise SystemExit("falta --extra gtf=<ruta al GTF de Ensembl 110>")
        shutil.copy(src[0], dest)
    return dest


def _posiciones_gtf(gtf, ad):
    """Cromosoma/inicio/fin por Ensembl gene_id (líneas `gene` del GTF). Join por ID, no por símbolo.
    Solo autosomas + X/Y con prefijo chr (lo que espera infercnvpy)."""
    import gzip
    import pandas as pd
    pos = {}
    with gzip.open(gtf, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.split("\t", 9)
            if f[2] != "gene":
                continue
            i = f[8].find('gene_id "')
            gid = f[8][i + 9:f[8].find('"', i + 9)]
            pos[gid] = ("chr" + f[0], int(f[3]), int(f[4]))
    ids = ad.var["gene_ids"].astype(str).str.split(".").str[0]
    df = pd.DataFrame([pos.get(g, (None, np.nan, np.nan)) for g in ids], index=ad.var_names,
                      columns=["chromosome", "start", "end"])
    validos = {"chr%s" % c for c in list(range(1, 23)) + ["X", "Y"]}
    df.loc[~df["chromosome"].isin(validos), ["chromosome", "start", "end"]] = [None, np.nan, np.nan]
    ad.var["chromosome"] = df["chromosome"]
    ad.var["start"] = df["start"]
    ad.var["end"] = df["end"]


def _burden(ad):
    X = ad.obsm["X_cnv"]
    return np.asarray(abs(X).mean(axis=1)).ravel()


def _copykat(ad, nombre, ref_mask, obs_mask, n_cores):
    """Exporta UMI crudos con ids opacos, lanza CopyKAT, devuelve Serie de predicción por célula."""
    import subprocess
    import pandas as pd
    import scipy.io
    import scipy.sparse as sp
    d = os.path.join(OUT, "copykat", nombre)
    os.makedirs(d, exist_ok=True)
    pred_f = os.path.join(d, "copykat_prediction.txt")
    # CopyKAT guarda su propia predicción en el paso 9 (antes del heatmap del paso 10, que con
    # ~9.500 células se queda sin memoria en R): si el wrapper murió ahí, la predicción vale igual.
    propio = os.path.join(d, "%s_copykat_prediction.txt" % nombre)
    if not os.path.exists(pred_f) and os.path.exists(propio):
        pred_f = propio
    sel = ref_mask | obs_mask
    sub = ad[sel]
    opacos = np.array(["c%06d" % i for i in range(sub.n_obs)])
    if not os.path.exists(pred_f):
        scipy.io.mmwrite(os.path.join(d, "counts.mtx"), sp.csc_matrix(sub.layers["counts"].T))
        open(os.path.join(d, "genes.txt"), "w").write("\n".join(sub.var_names) + "\n")
        open(os.path.join(d, "cells.txt"), "w").write("\n".join(opacos) + "\n")
        open(os.path.join(d, "ref.txt"), "w").write("\n".join(opacos[ref_mask[sel]]) + "\n")
        log = open(os.path.join(d, "copykat.log"), "w")
        rc = subprocess.call([R_BIN, os.path.join(AQUI, "copykat_run.R"), d, nombre, str(n_cores)],
                             stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, OMP_NUM_THREADS="1"))
        log.close()
        if not os.path.exists(pred_f) and os.path.exists(propio):
            pred_f = propio
        if not os.path.exists(pred_f):
            return None, "CopyKAT falló (rc=%d) sin dejar predicción; ver copykat.log en zona clínica" % rc
    pred = pd.read_csv(pred_f, sep="\t")
    mapa = dict(zip(opacos, sub.obs_names))
    s = pd.Series("no_evaluada", index=ad.obs_names, dtype=object)
    s.loc[[mapa[c] for c in pred["cell.names"] if c in mapa]] = pred["copykat.pred"].to_numpy()
    return s, None


def _explor_correlacion(ad, nombre, ref_mask, no_epi, epi1):
    """EXPLORATORIO. Plantilla = perfil CNV medio de las malignas por consenso (propias si ≥50, si no
    las de la otra muestra, que comparte genes y ventanas). Célula «maligna_B_explor» si su correlación
    de Pearson con la plantilla supera el percentil 97,5 de las células de referencia, tiene identidad
    epitelial (≥1/4) y no está en un clúster inmune/endotelio/estroma."""
    import scanpy as sc
    X = ad.obsm["X_cnv"]
    X = X.toarray() if hasattr(X, "toarray") else np.asarray(X)
    propias = ad.obs["maligna_B"].to_numpy()
    if propias.sum() >= 50:
        plantilla, origen = X[propias].mean(axis=0), "propia (%d malignas por consenso)" % int(propias.sum())
    else:
        otro = [m for m in MUESTRAS if m != nombre][0]
        ruta = _h5ad(otro, "cnv")
        if not os.path.exists(ruta):
            ad.obs["maligna_B_explor"] = False
            return {"estado": "sin plantilla: ni ≥50 malignas propias ni h5ad de la otra muestra"}
        ao = sc.read_h5ad(ruta)
        Xo = ao.obsm["X_cnv"]
        Xo = Xo.toarray() if hasattr(Xo, "toarray") else np.asarray(Xo)
        mo = ao.obs["maligna_B"].to_numpy()
        if Xo.shape[1] != X.shape[1] or mo.sum() < 50:
            ad.obs["maligna_B_explor"] = False
            return {"estado": "plantilla de la otra muestra no utilizable (ventanas distintas o <50 malignas)"}
        plantilla, origen = Xo[mo].mean(axis=0), "%s (%d malignas por consenso)" % (otro, int(mo.sum()))
    Xc = X - X.mean(axis=1, keepdims=True)
    pc = plantilla - plantilla.mean()
    den = np.sqrt((Xc ** 2).sum(axis=1)) * np.sqrt((pc ** 2).sum())
    corr = np.where(den > 0, (Xc @ pc) / np.where(den > 0, den, 1), 0.0)
    ad.obs["cnv_corr_plantilla"] = corr
    umbral = float(np.percentile(corr[ref_mask], 97.5))
    alta = corr > umbral
    ad.obs["maligna_B_explor"] = alta & epi1 & ~no_epi
    ad.obs["maligna_B_explor_sin_epi"] = alta & ~no_epi
    por_tipo = {t: {"n": int((ad.obs["tipo"] == t).sum()),
                    "frac_corr_alta": round(float(alta[(ad.obs["tipo"] == t).to_numpy()].mean()), 3),
                    "corr_mediana": round(float(np.median(corr[(ad.obs["tipo"] == t).to_numpy()])), 3)}
                for t in ad.obs["tipo"].unique()}
    return {"plantilla": origen, "umbral_corr_p97.5_ref": round(umbral, 4),
            "corr_mediana_ref": round(float(np.median(corr[ref_mask])), 4),
            "n_maligna_B_explor": int(ad.obs["maligna_B_explor"].sum()),
            "frac_de_QC": round(float(ad.obs["maligna_B_explor"].mean()), 4),
            "n_corr_alta_sin_exigir_marcador_epitelial": int(ad.obs["maligna_B_explor_sin_epi"].sum()),
            "por_tipo": por_tipo,
            "concordancia_con_copykat_en_evaluadas": {
                "aneuploid_y_corr_alta": int((alta & (ad.obs["copykat"] == "aneuploid").to_numpy()).sum()),
                "diploid_y_corr_alta": int((alta & (ad.obs["copykat"] == "diploid").to_numpy()).sum())}}


def cnv(a):
    import infercnvpy as icnv
    import scanpy as sc
    sc.settings.n_jobs = 1
    gtf = _gtf(a)
    n_cores = int(os.environ.get("FLEX_B_CORES", "8"))
    informe = {"semilla": SEMILLA, "gtf": {"fichero": os.path.basename(gtf), "sha256": sha256(gtf)},
               "infercnv": "infercnvpy.tl.infercnv window=100 step=10 lfc_clip=3 dynamic_threshold=1.5, "
                           "sin chrX/chrY; referencia = clústeres inmunes + endotelio",
               "regla_infercnv": "carga CNV por célula = media |X_cnv|; aneuploide si carga > mediana_ref + 3·MAD_ref "
                                 "Y su clúster CNV (Leiden sobre X_cnv) tiene mediana > ese umbral",
               "copykat": "copykat 1.2.5, id.type=S, ngene.chr=5, win.size=25, KS.cut=0.1, hg20, "
                          "norm.cell.names = la misma referencia (≤2000, submuestreada con semilla)",
               "regla_maligna": "epitelial (≥1 de EPCAM/KRT8/KRT18/KRT19 con ≥1 UMI; clúster no inmune/endotelio/estroma) "
                                "Y aneuploide por inferCNV Y aneuploide por CopyKAT. Variante estricta: ≥2 de 4.",
               "muestras": {}}
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        ad = sc.read_h5ad(_h5ad(nombre, "anotado"))
        r = {"n_celulas": int(ad.n_obs)}
        _posiciones_gtf(gtf, ad)
        r["genes_con_posicion"] = int(ad.var["chromosome"].notna().sum())
        ref_cats = [t for t in NO_EPITELIALES_REF if t in set(ad.obs["tipo"])]
        ref_mask = ad.obs["tipo"].isin(ref_cats).to_numpy()
        r["referencia"] = {"tipos": ref_cats, "n": int(ref_mask.sum())}
        icnv.tl.infercnv(ad, reference_key="tipo", reference_cat=ref_cats, window_size=100, step=10,
                         n_jobs=n_cores)
        carga = _burden(ad)
        ad.obs["cnv_carga"] = carga
        lo, hi, med, mad = _mad_corte(carga[ref_mask])
        umbral = med + 3 * mad
        icnv.tl.pca(ad, n_comps=min(30, ad.obsm["X_cnv"].shape[1] - 1))
        icnv.pp.neighbors(ad, n_neighbors=15, random_state=SEMILLA)
        icnv.tl.leiden(ad, resolution=1.0, random_state=SEMILLA, flavor="igraph", n_iterations=2, directed=False)
        med_cl = ad.obs.groupby("cnv_leiden", observed=True)["cnv_carga"].median()
        cl_aneu = set(med_cl[med_cl > umbral].index)
        celula_alta = carga > umbral
        ad.obs["infercnv_aneuploide"] = celula_alta & ad.obs["cnv_leiden"].isin(cl_aneu).to_numpy()
        comp = {}
        for cl in med_cl.index:
            m_cl = (ad.obs["cnv_leiden"] == cl).to_numpy()
            comp[str(cl)] = {"n": int(m_cl.sum()), "carga_mediana": round(float(med_cl[cl]), 5),
                             "frac_referencia": round(float(ref_mask[m_cl].mean()), 3),
                             "tipos_top3": {t: int(n) for t, n in ad.obs.loc[m_cl, "tipo"].value_counts().head(3).items()}}
        r["infercnv_clusters"] = comp
        r["infercnv"] = {"umbral_carga": round(float(umbral), 5), "carga_mediana_ref": round(float(med), 5),
                         "n_clusters_cnv": int(len(med_cl)), "n_clusters_cnv_aneuploides": len(cl_aneu),
                         "frac_ref_sobre_umbral": round(float(celula_alta[ref_mask].mean()), 4),
                         "n_aneuploides": int(ad.obs["infercnv_aneuploide"].sum()),
                         "frac_aneuploides": round(float(ad.obs["infercnv_aneuploide"].mean()), 4)}
        # CopyKAT sobre todas las no-referencia + referencia (≤2000)
        rng = np.random.default_rng(SEMILLA)
        ref_idx = np.flatnonzero(ref_mask)
        if len(ref_idx) > 2000:
            ref_idx = rng.choice(ref_idx, 2000, replace=False)
        ref_ck = np.zeros(ad.n_obs, bool)
        ref_ck[ref_idx] = True
        # CopyKAT guarda una matriz de distancias célula×célula: con 16 GB de RAM el tope son
        # ~10.000 células. Si hay más no-referencia, se submuestrean con la semilla y las no
        # evaluadas quedan fuera del consenso (se informa cuántas).
        max_obs = int(os.environ.get("FLEX_B_CK_MAX", "8000"))
        obs_idx = np.flatnonzero(~ref_mask)
        if len(obs_idx) > max_obs:
            obs_idx = rng.choice(obs_idx, max_obs, replace=False)
        obs_ck = np.zeros(ad.n_obs, bool)
        obs_ck[obs_idx] = True
        r["copykat_submuestreo"] = {"n_no_referencia": int((~ref_mask).sum()), "n_enviadas": int(obs_ck.sum()),
                                    "n_referencia_enviadas": int(ref_ck.sum())}
        pred, err = _copykat(ad, nombre, ref_ck, obs_ck, n_cores)
        if pred is None:
            ad.obs["copykat"] = "no_evaluada"
            r["copykat"] = {"error": err}
            ad.obs["copykat_aneuploide"] = False
        else:
            ad.obs["copykat"] = pred.to_numpy()
            ad.obs["copykat_aneuploide"] = (ad.obs["copykat"] == "aneuploid").to_numpy()
            vc = ad.obs["copykat"].value_counts()
            r["copykat"] = {"n_evaluadas": int((ad.obs["copykat"] != "no_evaluada").sum()),
                            "conteos": {k: int(v) for k, v in vc.items()},
                            "frac_ref_llamada_aneuploide": round(float(ad.obs.loc[ref_ck, "copykat_aneuploide"].mean()), 4)}
        # identidad epitelial y consenso
        no_epi = ad.obs["tipo"].isin(NO_EPITELIALES_REF + ESTROMA).to_numpy()
        epi1 = (ad.obs["n_genes_epitelio"] >= 1).to_numpy() & ~no_epi
        epi2 = (ad.obs["n_genes_epitelio"] >= 2).to_numpy() & ~no_epi
        inf = ad.obs["infercnv_aneuploide"].to_numpy()
        ck = ad.obs["copykat_aneuploide"].to_numpy()
        ad.obs["maligna_B"] = epi1 & inf & ck
        ad.obs["maligna_B_estricta"] = epi2 & inf & ck
        # concordancia entre métodos en las células no referencia que CopyKAT llegó a clasificar
        # (aneuploid/diploid). Sus `not.defined` no pueden entrar en el consenso y se cuentan aparte.
        definida = ad.obs["copykat"].isin(["aneuploid", "diploid"]).to_numpy()
        eval_ck = definida & ~ref_mask
        r["copykat_no_definidas_no_referencia"] = int((~definida & ~ref_mask & obs_ck).sum())
        r["EXPLORATORIO_infercnv_aneuploide_epitelial_sin_veredicto_copykat"] = int(
            (ad.obs["infercnv_aneuploide"].to_numpy() & ~definida & ~ref_mask
             & (ad.obs["n_genes_epitelio"] >= 1).to_numpy()
             & ~ad.obs["tipo"].isin(NO_EPITELIALES_REF + ESTROMA).to_numpy()).sum())
        acc = float((inf[eval_ck] == ck[eval_ck]).mean()) if eval_ck.any() else None
        kappa = None
        if eval_ck.any():
            p1, p2 = inf[eval_ck].mean(), ck[eval_ck].mean()
            pe = p1 * p2 + (1 - p1) * (1 - p2)
            kappa = (acc - pe) / (1 - pe) if pe < 1 else None
        r["concordancia_no_referencia"] = {"n": int(eval_ck.sum()), "acuerdo": round(acc, 4) if acc else None,
                                           "kappa": round(kappa, 4) if kappa is not None else None,
                                           "solo_infercnv": int((inf & ~ck & eval_ck).sum()),
                                           "solo_copykat": int((ck & ~inf & eval_ck).sum()),
                                           "ambos": int((inf & ck & eval_ck).sum())}
        r["malignas"] = {"n": int(ad.obs["maligna_B"].sum()), "frac_de_QC": round(float(ad.obs["maligna_B"].mean()), 4),
                         "n_estricta_2de4": int(ad.obs["maligna_B_estricta"].sum()),
                         "aneuploides_ambos_sin_marcador_epitelial": int((inf & ck & ~epi1 & ~no_epi).sum()),
                         "aneuploides_ambos_en_cluster_no_epitelial": int((inf & ck & no_epi).sum())}
        r["malignas_por_tipo_cluster"] = {t: int(n) for t, n in
                                          ad.obs.loc[ad.obs["maligna_B"], "tipo"].value_counts().items()}
        # EXPLORATORIO (fuera de la especificación, decidido tras ver que en hígado el consenso da 0):
        # correlación del perfil CNV de cada célula con el perfil medio de las malignas por consenso
        # (las de la propia muestra si hay ≥50; si no, las de la otra muestra, mismas ventanas).
        r["EXPLORATORIO_maligna_por_correlacion_CNV"] = _explor_correlacion(ad, nombre, ref_mask, no_epi, epi1)
        ruta = _h5ad(nombre, "cnv")
        ad.write_h5ad(ruta, compression="gzip")
        r["salida_h5ad"] = {"ruta": ruta, "sha256": sha256(ruta)}
        informe["muestras"][nombre] = r
        imprime({nombre: r})
    ruta, h = guardar_json("03_cnv%s.json" % ("" if a.muestra is None else "_" + a.muestra), informe)
    imprime({"salida_json": {"ruta": ruta, "sha256": h}})


def resumen(a):
    """Inventario de salidas de analisis_B/ con sha256 (sin abrir contenido)."""
    out = {}
    for r, _, fs in os.walk(OUT):
        for f in sorted(fs):
            p = os.path.join(r, f)
            if f.endswith((".json", ".h5ad", ".txt", ".sha256", ".gz")) and "copykat" not in r:
                out[os.path.relpath(p, OUT)] = {"bytes": os.path.getsize(p), "sha256": sha256(p)}
    imprime(out)


def diagcnv(a):
    """EXPLORATORIO: perfil CNV medio por cromosoma y por grupo (tipo / CopyKAT), solo agregados."""
    import scanpy as sc
    out = {}
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        ad = sc.read_h5ad(_h5ad(nombre, "cnv"))
        X = ad.obsm["X_cnv"]
        X = X.toarray() if hasattr(X, "toarray") else np.asarray(X)
        chr_pos = ad.uns["cnv"]["chr_pos"]
        orden = sorted(chr_pos.items(), key=lambda kv: kv[1])
        limites = [(c, s, (orden[i + 1][1] if i + 1 < len(orden) else X.shape[1])) for i, (c, s) in enumerate(orden)]
        grupos = {"referencia": ad.obs["tipo"].isin(NO_EPITELIALES_REF).to_numpy(),
                  "hepatocito": (ad.obs["tipo"] == "hepatocito").to_numpy(),
                  "indeterminado": (ad.obs["tipo"] == "indeterminado").to_numpy(),
                  "epitelio_tumoral": (ad.obs["tipo"] == "epitelio_tumoral").to_numpy(),
                  "copykat_aneuploid": (ad.obs["copykat"] == "aneuploid").to_numpy(),
                  "copykat_diploid_no_ref": ((ad.obs["copykat"] == "diploid") & ~ad.obs["tipo"].isin(NO_EPITELIALES_REF)).to_numpy()}
        r = {}
        for g, m in grupos.items():
            if m.sum() == 0:
                continue
            perfil = {c: round(float(X[m][:, s:e].mean()), 4) for c, s, e in limites}
            r[g] = {"n": int(m.sum()), "carga_media": round(float(np.abs(X[m]).mean()), 5),
                    "frac_valores_no_cero": round(float((X[m] != 0).mean()), 4), "perfil_cromosomas": perfil}
        r["copykat_x_tipo"] = {t: {k: int(v) for k, v in ad.obs.loc[ad.obs["tipo"] == t, "copykat"].value_counts().items()}
                               for t in ad.obs["tipo"].unique()}
        out[nombre] = r
    imprime(out)


# ---------------------------------------------------------------------------
# medidas: tablas A-D sobre células malignas (maligna_B)
# ---------------------------------------------------------------------------
GENES_A = ["B2M", "TAP1", "TAP2", "NLRC5", "PSMB8", "PSMB9", "IRF1", "STAT1"]
HLA = ["HLA-A", "HLA-B", "HLA-C", "HLA-E"]
NE = ["CHGA", "SYP", "{{DIANA3}}", "{{DIANA4}}", "NEUROD1"]
GENES_B = ["PRAME", "MAGEA4", "MAGEA1", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12", "MAGEC1", "MAGEC2",
           "CTAG2", "ANKRD30A", "TPBG"]
GENES_C = ["TACSTD2", "ERBB2", "ERBB3", "CD276", "NECTIN4", "CEACAM5", "{{DIANA2}}", "SEZ6", "{{DIANA}}", "FOLR1",
           "SLC39A6", "VTCN1", "MSLN", "CLDN6", "MUC1", "GPNMB", "ROR1", "FGFR1"]
ER = ["ESR1", "PGR", "GREB1", "TFF1"]
RB_EJE = ["RB1", "CDKN2A", "CCNE1", "E2F1"]
# Tirosh et al. 2016 (Science 352:189), listas S y G2M usadas por scanpy/Seurat.
S_GENES = ["MCM5", "PCNA", "TYMS", "FEN1", "MCM2", "MCM4", "RRM1", "UNG", "GINS2", "MCM6", "CDCA7", "DTL", "PRIM1",
           "UHRF1", "MLF1IP", "HELLS", "RFC2", "RPA2", "NASP", "RAD51AP1", "GMNN", "WDR76", "SLBP", "CCNE2", "UBR7",
           "POLD3", "MSH2", "ATAD2", "RAD51", "RRM2", "CDC45", "CDC6", "EXO1", "TIPIN", "DSCC1", "BLM", "CASP8AP2",
           "USP1", "CLSPN", "POLA1", "CHAF1B", "BRIP1", "E2F8"]
G2M_GENES = ["HMGB2", "CDK1", "NUSAP1", "UBE2C", "BIRC5", "TPX2", "TOP2A", "NDC80", "CKS2", "NUF2", "CKS1B", "MKI67",
             "TMPO", "CENPF", "TACC3", "FAM64A", "SMC4", "CCNB2", "CKAP2L", "CKAP2", "AURKB", "BUB1", "KIF11", "ANP32E",
             "TUBB4B", "GTSE1", "KIF20B", "HJURP", "CDCA3", "HN1", "CDC20", "TTK", "CDC25C", "KIF2C", "RANGAP1",
             "NCAPD2", "DLGAP5", "CDCA2", "CDCA8", "ECT2", "KIF23", "HMMR", "AURKA", "PSRC1", "ANLN", "LBR", "CKAP5",
             "CENPE", "CTCF", "NEK2", "G2E3", "GAS2L3", "CBX5", "CENPA"]
N_BOOT = 1000


def _medida(cuentas, totales, rng):
    """Fracción de células con ≥1 UMI, IC95 bootstrap (percentil, N_BOOT réplicas), media CP10k
    entre positivas y n. `cuentas` y `totales` son vectores por célula (UMI del gen, UMI totales)."""
    n = int(len(cuentas))
    pos = cuentas > 0
    k = int(pos.sum())
    if n == 0:
        return {"n": 0, "frac": None, "nota": "sin células"}
    frac = k / n
    boots = rng.binomial(n, frac, size=N_BOOT) / n if n else np.zeros(N_BOOT)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    out = {"n": n, "n_pos": k, "frac": round(frac, 4), "ic95": [round(float(lo), 4), round(float(hi), 4)]}
    if k == 0:
        out["nota"] = "0 detectadas, no implica ausencia"
        out["media_cp10k_pos"] = None
    else:
        out["media_cp10k_pos"] = round(float(np.mean(cuentas[pos] / totales[pos] * 1e4)), 3)
    return out


def _tabla(ad, genes, mask, rng):
    X = ad.layers["counts"]
    tot = np.asarray(X.sum(axis=1)).ravel()
    sub = X[mask]
    tots = tot[mask]
    t = {}
    for g in genes:
        if g not in ad.var_names:
            t[g] = "fuera del panel"
            continue
        c = sub[:, ad.var_names.get_loc(g)].toarray().ravel()
        t[g] = _medida(c, tots, rng)
    return t


def _probe_matrix(nombre, barcodes, genes):
    """Lee del `raw_probe_bc_matrix.h5` solo las columnas de `barcodes` y las sondas de `genes`.
    Devuelve (sondas:list[dict], matriz dense sondas×células)."""
    import h5py
    ruta = _h5(nombre, "raw_probe_bc_matrix")
    with h5py.File(ruta, "r") as f:
        m = f["matrix"]
        bcs = m["barcodes"][:].astype(str)
        feat = m["features"]
        ids = feat["id"][:].astype(str)
        names = feat["name"][:].astype(str)
        extra = {}
        for k in feat.keys():
            if k not in ("id", "name", "_all_tag_keys") and isinstance(feat[k], h5py.Dataset) \
                    and feat[k].shape == ids.shape:
                extra[k] = feat[k][:].astype(str)
        # el gen puede venir en `name` o dentro del id `ENSG|GEN|hash`
        gen_de_id = np.array([i.split("|")[1] if "|" in i else "" for i in ids])
        sel_feat = np.flatnonzero(np.isin(names, genes) | np.isin(gen_de_id, genes))
        names = np.where(np.isin(names, genes), names, gen_de_id)
        fila_de = {int(i): j for j, i in enumerate(sel_feat)}
        pos_bc = {b: i for i, b in enumerate(bcs)}
        cols = [pos_bc[b] for b in barcodes if b in pos_bc]
        indptr = m["indptr"][:]
        data = m["data"]
        indices = m["indices"]
        M = np.zeros((len(sel_feat), len(cols)), dtype=np.float32)
        for j, c in enumerate(cols):
            s, e = int(indptr[c]), int(indptr[c + 1])
            if e <= s:
                continue
            idx = indices[s:e]
            dat = data[s:e]
            hit = np.isin(idx, sel_feat)
            for i, v in zip(idx[hit], dat[hit]):
                M[fila_de[int(i)], j] = v
        sondas = [{"probe_id": ids[i], "gen": names[i], **{k: v[i] for k, v in extra.items()}} for i in sel_feat]
    return sondas, M, len(cols)


def _cnv_en_genes(ad, genes, mal, ref):
    """Media de X_cnv (log-ratio suavizado de infercnvpy) en la ventana que contiene cada gen,
    en malignas y en referencia. Aproximación: ventana = índice del gen dentro de su cromosoma // step."""
    import pandas as pd
    out = {}
    if "X_cnv" not in ad.obsm or "cnv" not in ad.uns:
        return "sin X_cnv"
    chr_pos = ad.uns["cnv"]["chr_pos"]
    step = 10
    var = ad.var[ad.var["chromosome"].notna()].copy()
    X = ad.obsm["X_cnv"]
    for g in genes:
        if g not in var.index:
            out[g] = "sin posición o fuera del panel"
            continue
        ch = var.loc[g, "chromosome"]
        if ch not in chr_pos:
            out[g] = "cromosoma excluido"
            continue
        genes_ch = var[var["chromosome"] == ch].sort_values("start")
        i = int(np.flatnonzero(genes_ch.index == g)[0])
        n_vent = (chr_pos[ch + "_fin"] if ch + "_fin" in chr_pos else None)
        # ventanas del cromosoma: desde chr_pos[ch] hasta la siguiente posición de inicio
        inicios = sorted(chr_pos.values())
        j = inicios.index(chr_pos[ch])
        fin = inicios[j + 1] if j + 1 < len(inicios) else X.shape[1]
        w = min(chr_pos[ch] + i // step, fin - 1)
        col = X[:, w].toarray().ravel() if hasattr(X, "toarray") else np.asarray(X[:, w]).ravel()
        out[g] = {"cromosoma": ch, "ventana": int(w), "media_malignas": round(float(col[mal].mean()), 4),
                  "media_referencia": round(float(col[ref].mean()), 4),
                  "frac_malignas_>0.1": round(float((col[mal] > 0.1).mean()), 3),
                  "frac_malignas_<-0.1": round(float((col[mal] < -0.1).mean()), 3)}
    return out


def _auditoria_sondas():
    import json
    import shutil
    ruta = os.path.join(REF_DIR, "sondas_mage_auditoria.json")
    if not os.path.exists(ruta):
        # La auditoría es pública (sondas 10x + cDNA Ensembl) y vive en el repo; se copia a ref/
        # para que el análisis quede autocontenido.
        repo = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(AQUI))), "docs", "flex",
                            "sondas_mage_auditoria_B.json")
        if not os.path.exists(repo):
            return None
        os.makedirs(REF_DIR, exist_ok=True)
        shutil.copy(repo, ruta)
    return json.load(open(ruta))


def _conjunto(a):
    """Columna de malignas y sufijo de salida: la de la especificación o la EXPLORATORIA."""
    if any(x == "conjunto=explor" for x in a.extra):
        return "maligna_B_explor", "_EXPLORATORIO"
    return "maligna_B", ""


def medidas(a):
    import scanpy as sc
    from scipy.stats import spearmanr
    col, sufijo = _conjunto(a)
    informe = {"semilla": SEMILLA, "conjunto_malignas": col,
               "aviso": ("EXPLORATORIO: malignas por correlación CNV, fuera de la especificación" if sufijo else
                         "especificación: consenso inferCNV + CopyKAT + identidad epitelial"), "bootstrap": N_BOOT, "definicion": "fracción de células malignas (maligna_B) con "
               "≥1 UMI; IC95 bootstrap percentil; media CP10k (UMI/total·1e4) entre positivas; n",
               "muestras": {}}
    aud = _auditoria_sondas()
    informe["auditoria_sondas_MAGE"] = ({"sha256_fichero": sha256(os.path.join(REF_DIR, "sondas_mage_auditoria.json")),
                                         "por_gen": aud["por_gen"]} if aud else "NO DISPONIBLE: tabla B de MAGEA sin cifra")
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        rng = np.random.default_rng(SEMILLA)
        ad = sc.read_h5ad(_h5ad(nombre, "cnv"))
        mal = ad.obs[col].astype(bool).to_numpy() if col in ad.obs else np.zeros(ad.n_obs, bool)
        r = {"n_malignas": int(mal.sum()), "conjunto": col}
        if mal.sum() == 0:
            r["nota"] = "sin malignas: tablas vacías"
            informe["muestras"][nombre] = r
            continue
        # scores por célula (sobre log-normalizado, en .X)
        sc.tl.score_genes(ad, [g for g in GENES_A if g in ad.var_names], score_name="score_APM", random_state=SEMILLA)
        sc.tl.score_genes(ad, [g for g in NE if g in ad.var_names], score_name="score_NE", random_state=SEMILLA)
        sc.tl.score_genes(ad, [g for g in ER if g in ad.var_names], score_name="score_ER", random_state=SEMILLA)
        s_g = [g for g in S_GENES if g in ad.var_names]
        g2m = [g for g in G2M_GENES if g in ad.var_names]
        sc.tl.score_genes_cell_cycle(ad, s_genes=s_g, g2m_genes=g2m, random_state=SEMILLA)
        r["ciclo"] = {"n_genes_S_en_panel": len(s_g), "n_genes_G2M_en_panel": len(g2m),
                      "fases_malignas": {k: int(v) for k, v in ad.obs.loc[mal, "phase"].value_counts().items()}}
        # A
        r["A_presentacion_antigenica"] = _tabla(ad, GENES_A, mal, rng)
        r["A_HLA"] = {g: ("fuera del panel" if g not in ad.var_names else "EN PANEL (inesperado)") for g in HLA}
        apm = ad.obs.loc[mal, "score_APM"].to_numpy()
        ne = ad.obs.loc[mal, "score_NE"].to_numpy()
        fase = ad.obs.loc[mal, "phase"].astype(str).to_numpy()
        rho, p = spearmanr(apm, ne)
        terc = np.quantile(ne, [1 / 3, 2 / 3])
        r["A_APM_x_NE"] = {"spearman_rho": round(float(rho), 4), "p": float(p),
                           "APM_media_por_tercil_NE": {"bajo": round(float(apm[ne <= terc[0]].mean()), 4),
                                                       "medio": round(float(apm[(ne > terc[0]) & (ne <= terc[1])].mean()), 4),
                                                       "alto": round(float(apm[ne > terc[1]].mean()), 4)},
                           "n_por_tercil": [int((ne <= terc[0]).sum()), int(((ne > terc[0]) & (ne <= terc[1])).sum()),
                                            int((ne > terc[1]).sum())]}
        r["A_APM_x_fase"] = {f: {"n": int((fase == f).sum()), "APM_media": round(float(apm[fase == f].mean()), 4)}
                             for f in sorted(set(fase))}
        r["A_score_APM_malignas"] = {"media": round(float(apm.mean()), 4), "mediana": round(float(np.median(apm)), 4),
                                     "frac_>0": round(float((apm > 0).mean()), 4)}
        # B (gen) + B por sonda con auditoría
        tb = _tabla(ad, GENES_B, mal, rng)
        if aud:
            for g, est in aud["por_gen"].items():
                if g in tb and isinstance(tb[g], dict):
                    tb[g]["auditoria_sondas"] = est["estado"]
                    if est["estado"] == "no resoluble a nivel de parálogo":
                        tb[g]["cifra"] = "no resoluble a nivel de parálogo"
        r["B_antigenos_CT_linaje"] = tb
        bcs = ad.obs_names[mal].to_numpy()
        sondas, M, ncol = _probe_matrix(nombre, bcs, GENES_B)
        tot_mal = np.asarray(ad.layers["counts"][mal].sum(axis=1)).ravel()
        por_sonda = {}
        for i, s in enumerate(sondas):
            med = _medida(M[i], tot_mal, rng)
            est = None
            if aud:
                for x in aud["sondas"]:
                    if x["probe_id"] == s["probe_id"]:
                        est = {"resoluble": x["resoluble"], "cruza_familia_<=3mm": x["cruza_familia_<=3mm"],
                               "ambas_mitades_exactas_en_otro_gen": x["ambas_mitades_exactas_en_otro_gen"]}
            por_sonda[s["probe_id"].split("|")[-1] + ":" + s["gen"]] = {**med, "auditoria": est,
                                                                       **{k: v for k, v in s.items() if k not in ("probe_id", "gen")}}
        r["B_por_sonda"] = {"n_celulas_malignas_en_matriz_sondas": ncol, "sondas": por_sonda}
        # resumen por gen usando SOLO sondas resolubles
        if aud:
            res_gen = {}
            for g in GENES_B:
                ok_ids = set(aud["por_gen"].get(g, {}).get("resolubles", []))
                filas = [i for i, s in enumerate(sondas) if s["gen"] == g and s["probe_id"] in ok_ids]
                if filas:
                    res_gen[g] = _medida(M[filas].sum(axis=0), tot_mal, rng)
                    res_gen[g]["n_sondas_resolubles_usadas"] = len(filas)
                else:
                    res_gen[g] = "sin sondas resolubles" if g in aud["por_gen"] else "no auditado"
            r["B_por_gen_solo_sondas_resolubles"] = res_gen
        # C
        r["C_dianas_superficie"] = _tabla(ad, GENES_C, mal, rng)
        # contexto (no es medida primaria): la misma tabla A en las células de referencia no epiteliales
        ref = ad.obs["tipo"].isin(NO_EPITELIALES_REF).to_numpy()
        r["A_contexto_referencia_no_maligna"] = {"n": int(ref.sum()), "tabla": _tabla(ad, GENES_A, ref, rng),
                                                 "score_APM_media": round(float(ad.obs.loc[ref, "score_APM"].mean()), 4)}
        # EXPLORATORIO: señal inferCNV en la ventana de genes concretos (malignas vs referencia).
        r["EXPLORATORIO_cnv_ventana_gen"] = _cnv_en_genes(ad, ["FGFR1", "ERBB2", "CCND1", "MYC", "ESR1", "RB1",
                                                               "CDKN2A", "PTEN", "TP53"], mal, ref)
        ad.write_h5ad(_h5ad(nombre, "medidas" + sufijo), compression="gzip")
        informe["muestras"][nombre] = r
    # D: comparación y regla INDETERMINADA
    m = informe["muestras"]
    if "hueso" in m:
        qc = __import__("json").load(open(os.path.join(OUT, "01_qc.json")))
        stop = qc.get("control_tejido_decision", {}).get("veredicto") != "PASA"
        m["hueso"]["D_estado"] = ("INDETERMINADA (STOP en control de tejido)" if stop else
                                  "INDETERMINADA (<200 malignas)" if m["hueso"]["n_malignas"] < 200 else "COMPARABLE")
    ruta, h = guardar_json("04_medidas%s%s.json" % (sufijo, "" if a.muestra is None else "_" + a.muestra), informe)
    informe["salida_json"] = {"ruta": ruta, "sha256": h}
    imprime(informe)


# ---------------------------------------------------------------------------
# secundarias (descriptivas)
# ---------------------------------------------------------------------------
MICRO_T = ["CD8A", "CD8B", "CD4", "FOXP3", "PDCD1", "HAVCR2", "LAG3", "TOX", "IFNG", "GZMB", "CXCL13"]
MICRO_MIEL = ["CD68", "CD163", "TREM2", "SPP1", "CXCL9", "CXCL10"]
MICRO_FIB = ["FAP"]


def _gmm_bic(x, rng):
    from sklearn.mixture import GaussianMixture
    x = x.reshape(-1, 1)
    out = {}
    for k in (1, 2):
        g = GaussianMixture(k, random_state=SEMILLA, n_init=3).fit(x)
        out[k] = {"bic": round(float(g.bic(x)), 2), "medias": [round(float(v), 4) for v in g.means_.ravel()],
                  "pesos": [round(float(v), 4) for v in g.weights_]}
    out["poblaciones_por_BIC"] = 2 if out[2]["bic"] + 10 < out[1]["bic"] else 1
    return out


def secundarias(a):
    import scanpy as sc
    col, sufijo = _conjunto(a)
    informe = {"semilla": SEMILLA, "nota": "descriptivas; sin control de multiplicidad", "conjunto_malignas": col,
               "muestras": {}}
    for nombre in (sorted(MUESTRAS) if a.muestra is None else [a.muestra]):
        rng = np.random.default_rng(SEMILLA)
        ruta_m = _h5ad(nombre, "medidas" + sufijo)
        # si `medidas` no escribió h5ad (0 malignas en ese conjunto), se parte del de cnv
        ad = sc.read_h5ad(ruta_m if os.path.exists(ruta_m) else _h5ad(nombre, "cnv"))
        if col not in ad.obs:
            ad.obs[col] = False
        for nombre_s, genes in (("score_ER", ER), ("score_NE", NE)):
            if nombre_s not in ad.obs:
                sc.tl.score_genes(ad, [g for g in genes if g in ad.var_names], score_name=nombre_s, random_state=SEMILLA)
        if "phase" not in ad.obs:
            sc.tl.score_genes_cell_cycle(ad, s_genes=[g for g in S_GENES if g in ad.var_names],
                                         g2m_genes=[g for g in G2M_GENES if g in ad.var_names], random_state=SEMILLA)
        r = {"n_celulas_QC": int(ad.n_obs), "conjunto": col}
        vc = ad.obs["tipo"].value_counts()
        r["fracciones_tipo_celular"] = {t: {"n": int(n), "frac": round(float(n) / ad.n_obs, 4)} for t, n in vc.items()}
        r["fracciones_tipo_celular"][col] = {"n": int(ad.obs[col].sum()),
                                             "frac": round(float(ad.obs[col].mean()), 4)}
        tnk = ad.obs["tipo"].isin(["T", "NK"]).to_numpy()
        miel = ad.obs["tipo"].isin(["mieloide", "neutrofilo"]).to_numpy()
        fib = ad.obs["tipo"].isin(["fibroblasto"]).to_numpy()
        r["microambiente_T_NK"] = _tabla(ad, MICRO_T, tnk, rng) if tnk.any() else "sin células T/NK anotadas"
        # células T por marcador directo (CD3E ≥1 UMI) en toda la muestra, por si Leiden no las separó
        if "CD3E" in ad.var_names:
            c3 = ad.layers["counts"][:, ad.var_names.get_loc("CD3E")].toarray().ravel() > 0
            r["celulas_CD3E>=1_toda_la_muestra"] = {"n": int(c3.sum()), "frac": round(float(c3.mean()), 4),
                                                   "tabla_T_en_CD3E+": _tabla(ad, MICRO_T, c3, rng) if c3.sum() >= 20 else "n<20"}
        r["microambiente_mieloide"] = _tabla(ad, MICRO_MIEL, miel, rng) if miel.any() else "sin mieloides"
        r["microambiente_fibroblasto"] = _tabla(ad, MICRO_FIB, fib, rng) if fib.any() else "sin fibroblastos"
        # CD3E+ dentro de T/NK para separar T de NK (descriptivo)
        if tnk.any() and "CD3E" in ad.var_names:
            c = ad.layers["counts"][tnk][:, ad.var_names.get_loc("CD3E")].toarray().ravel()
            r["microambiente_T_NK"]["frac_CD3E>=1_en_T_NK"] = round(float((c > 0).mean()), 4)
        mal = ad.obs[col].astype(bool).to_numpy()
        if mal.sum() >= 50:
            er = ad.obs.loc[mal, "score_ER"].to_numpy()
            ne = ad.obs.loc[mal, "score_NE"].to_numpy()
            r["eje_ER_vs_NE"] = {"n": int(mal.sum()), "tabla_ER": _tabla(ad, ER, mal, rng),
                                 "GMM_score_ER": _gmm_bic(er, rng), "GMM_score_NE": _gmm_bic(ne, rng),
                                 "spearman_ER_NE": round(float(__import__("scipy.stats").stats.spearmanr(er, ne)[0]), 4),
                                 "cuadrantes_(score>0)": {"ER+NE+": int(((er > 0) & (ne > 0)).sum()),
                                                          "ER+NE-": int(((er > 0) & (ne <= 0)).sum()),
                                                          "ER-NE+": int(((er <= 0) & (ne > 0)).sum()),
                                                          "ER-NE-": int(((er <= 0) & (ne <= 0)).sum())}}
            g1 = mal & (ad.obs["phase"].astype(str) == "G1").to_numpy()
            r["RB_eje_en_malignas_no_ciclantes_G1"] = {"n": int(g1.sum()), "tabla": _tabla(ad, RB_EJE, g1, rng)}
        else:
            r["eje_ER_vs_NE"] = "menos de 50 malignas"
        informe["muestras"][nombre] = r
    ruta, h = guardar_json("05_secundarias%s%s.json" % (sufijo, "" if a.muestra is None else "_" + a.muestra), informe)
    informe["salida_json"] = {"ruta": ruta, "sha256": h}
    imprime(informe)


# ---------------------------------------------------------------------------
# congelar: capa E, h5ad único con malignas etiquetadas
# ---------------------------------------------------------------------------
def congelar(a):
    import anndata as an
    import scanpy as sc
    import importlib.metadata as im
    partes = []
    for nombre in sorted(MUESTRAS):
        ruta_m = _h5ad(nombre, "medidas_EXPLORATORIO")
        ad = sc.read_h5ad(ruta_m if os.path.exists(ruta_m) else _h5ad(nombre, "medidas"))
        ad.X = ad.layers["counts"].copy()
        del ad.layers["counts"]
        for k in list(ad.obsm.keys()):
            if k != "X_cnv":
                del ad.obsm[k]
        ad.obsp.clear() if hasattr(ad.obsp, "clear") else None
        ad.raw = None
        ad.uns = {}
        ad.obs_names = [nombre + ":" + b for b in ad.obs_names]
        partes.append(ad)
    todo = an.concat(partes, join="outer", merge="first", uns_merge=None)
    todo.var["ensembl_id"] = todo.var["gene_ids"].astype(str)
    scripts = {f: sha256(os.path.join(AQUI, f)) for f in sorted(os.listdir(AQUI)) if f.endswith((".py", ".R"))}
    todo.uns["procedencia"] = {
        "analista": "B", "fecha": __import__("time").strftime("%Y-%m-%d"), "semilla": SEMILLA,
        "especificacion": "docs/flex/ESPEC_CONGELADA_v1.md sha256 6165b9ef8a7b452cdce055d1542ca38a72eeb0c66c4453083278151f6a4030dc",
        "X": "UMI crudos (Cell Ranger filtered_feature_barcode_matrix); obs.maligna_B = consenso inferCNV+CopyKAT+identidad epitelial",
        "join_WES": "var.ensembl_id (Ensembl gene_id GRCh38-2024-A)",
        "versiones": {p: im.version(p) for p in ("scanpy", "anndata", "infercnvpy", "numpy", "scipy", "scikit-learn", "scrublet")},
        "copykat": "1.2.5 (R 4.5.3)", "scripts_sha256": scripts}
    ruta = os.path.join(OUT, "flex_B_congelado.h5ad")
    todo.write_h5ad(ruta, compression="gzip")
    h = sha256(ruta)
    open(ruta + ".sha256", "w").write(h + "  " + os.path.basename(ruta) + "\n")
    imprime({"h5ad": ruta, "sha256": h, "n_celulas": int(todo.n_obs), "n_genes": int(todo.n_vars),
             "malignas_por_muestra_consenso": {m: int(n) for m, n in todo.obs.groupby("muestra", observed=True)["maligna_B"].sum().items()},
             "malignas_por_muestra_EXPLORATORIO": ({m: int(n) for m, n in todo.obs.groupby("muestra", observed=True)["maligna_B_explor"].sum().items()}
                                                   if "maligna_B_explor" in todo.obs else "sin columna"),
             "scripts_sha256": scripts})
