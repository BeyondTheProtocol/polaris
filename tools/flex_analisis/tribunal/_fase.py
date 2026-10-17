"""P5 — ¿El ~68 % de malignas en S+G2M es artefacto de score_genes con datos dispersos?
(REGLAS_TRIBUNAL_v2.json «fase».)

1. score_genes_cell_cycle sobre la muestra entera (como B) y sobre solo malignas (como A).
2. Mismo score en referencia no ciclante (T, NK, mieloide, endotelio) y hepatocitos.
3. Nulo: 200 pares de listas aleatorias con la misma distribución de expresión (bins) y tamaño que S y G2M.
4. Fracción con S_score y G2M_score exactamente 0 (o sin ningún gen de la lista detectado).
5. Fase por decil de UMI.
6. Método alternativo por cuentas: carga S+G2M observada frente a la esperada por profundidad (Poisson
   con la tasa media de la muestra); «ciclante» si P(X >= obs) < 0,01. Y MKI67/TOP2A directos con
   referencia emparejada por UMI.
Salida: tribunal/fase.json.
"""
import numpy as np
import scanpy as sc
from scipy.stats import poisson

from _comun import SEED, carga, guarda, out, wilson

S_GENES = ["MCM5", "PCNA", "TYMS", "FEN1", "MCM2", "MCM4", "RRM1", "UNG", "GINS2", "MCM6", "CDCA7", "DTL", "PRIM1",
           "UHRF1", "MLF1IP", "HELLS", "RFC2", "RPA2", "NASP", "RAD51AP1", "GMNN", "WDR76", "SLBP", "CCNE2", "UBR7",
           "POLD3", "MSH2", "ATAD2", "RAD51", "RRM2", "CDC45", "CDC6", "EXO1", "TIPIN", "DSCC1", "BLM", "CASP8AP2",
           "USP1", "CLSPN", "POLA1", "CHAF1B", "BRIP1", "E2F8"]
G2M_GENES = ["HMGB2", "CDK1", "NUSAP1", "UBE2C", "BIRC5", "TPX2", "TOP2A", "NDC80", "CKS2", "NUF2", "CKS1B", "MKI67",
             "TMPO", "CENPF", "TACC3", "FAM64A", "SMC4", "CCNB2", "CKAP2L", "CKAP2", "AURKB", "BUB1", "KIF11", "ANP32E",
             "TUBB4B", "GTSE1", "KIF20B", "HJURP", "CDCA3", "HN1", "CDC20", "TTK", "CDC25C", "KIF2C", "RANGAP1",
             "NCAPD2", "DLGAP5", "CDCA2", "CDCA8", "ECT2", "KIF23", "HMMR", "AURKA", "PSRC1", "ANLN", "LBR", "CKAP5",
             "CENPE", "CTCF", "NEK2", "G2E3", "GAS2L3", "CBX5", "CENPA"]
NOCICL = ["T", "NK", "mieloide", "endotelio"]


def _w(k, n):
    p, lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "frac": p, "ic95": [lo, hi]}


def ciclantes(phase):
    return np.isin(np.asarray(phase).astype(str), ["S", "G2M"])


def normaliza(ad):
    a = ad.copy()
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    return a


def fase(a, s, g2m, seed=SEED):
    """a ya normalizado (log1p CP10k)."""
    sc.tl.score_genes_cell_cycle(a, s_genes=s, g2m_genes=g2m, random_state=seed)
    return a.obs["S_score"].values, a.obs["G2M_score"].values, a.obs["phase"].astype(str).values


def run(*a):
    zl = np.load(out("higado_calls.npz"))
    zb = np.load(out("hueso_calls.npz"), allow_pickle=True)
    res = {"reglas": "REGLAS_TRIBUNAL_v2.json/fase"}
    rng = np.random.default_rng(SEED)
    for t in ("higado", "hueso"):
        ad = carga(t)
        sym = ad.var["simbolo"].values
        s_ids = [ad.var_names[i] for i in np.where(np.isin(sym, S_GENES))[0]]
        g_ids = [ad.var_names[i] for i in np.where(np.isin(sym, G2M_GENES))[0]]
        umi = np.asarray(ad.X.sum(axis=1)).ravel()
        tipo = ad.obs["tipo"].values
        grupos = {"nociclante_ref": np.isin(tipo, NOCICL), "hepatocito": tipo == "hepatocito",
                  "malignas_A": ad.obs["pob_usada"].values.astype(bool)}
        if t == "higado":
            grupos["malignas_8p11_p99"] = zl["8p11_p99"]
            grupos["luminal_T"] = zl["lum"]
        else:
            grupos["malignas_T_limpio"] = zb["maligna_T_limpio_T"].astype(bool)
            grupos["estroma"] = tipo == "fibroblasto_estelar"
        an = normaliza(ad)
        S, G, ph = fase(an, s_ids, g_ids)
        r = {"n_S_genes": len(s_ids), "n_G2M_genes": len(g_ids)}
        r["muestra_entera"] = {k: _w(int(ciclantes(ph[m]).sum()), int(m.sum())) for k, m in grupos.items() if m.sum()}
        r["exactamente_cero_S_y_G2M"] = {k: float(((S[m] == 0) & (G[m] == 0)).mean()) for k, m in grupos.items() if m.sum()}
        ndet_s = np.asarray((ad.X[:, np.isin(ad.var_names, s_ids)] > 0).sum(axis=1)).ravel()
        ndet_g = np.asarray((ad.X[:, np.isin(ad.var_names, g_ids)] > 0).sum(axis=1)).ravel()
        r["ningun_gen_S_ni_G2M_detectado"] = {k: float(((ndet_s[m] == 0) & (ndet_g[m] == 0)).mean()) for k, m in grupos.items() if m.sum()}
        r["genes_ciclo_detectados_mediana"] = {k: float(np.median(ndet_s[m] + ndet_g[m])) for k, m in grupos.items() if m.sum()}
        # por decil de UMI (toda la muestra)
        q = np.quantile(umi, np.linspace(0, 1, 11))
        dec = np.clip(np.digitize(umi, q[1:-1]), 0, 9)
        r["ciclante_por_decil_UMI"] = [float(ciclantes(ph[dec == d]).mean()) for d in range(10)]
        # como A: solo malignas
        mA = grupos["malignas_A"]
        if mA.sum() >= 20:
            _, _, phA = fase(an[mA].copy(), s_ids, g_ids)
            r["solo_malignas_A_como_A"] = _w(int(ciclantes(phA).sum()), int(mA.sum()))
        # nulo: listas aleatorias con la misma distribución de expresión
        mean_g = np.asarray(ad.X.mean(axis=0)).ravel()
        orden = np.argsort(mean_g)
        bins = np.empty(len(mean_g), int)
        bins[orden] = np.arange(len(mean_g)) * 25 // len(mean_g)
        idx_s = np.where(np.isin(ad.var_names, s_ids))[0]
        idx_g = np.where(np.isin(ad.var_names, g_ids))[0]
        excl = set(idx_s) | set(idx_g)
        cand = {b: np.array([i for i in np.where(bins == b)[0] if i not in excl]) for b in range(25)}
        nulo = {k: [] for k in grupos}
        for it in range(int(a[0]) if a else 100):
            rs = [int(rng.choice(cand[bins[i]])) for i in idx_s]
            rg = [int(rng.choice(cand[bins[i]])) for i in idx_g]
            _, _, phn = fase(an, list(ad.var_names[rs]), list(ad.var_names[rg]), seed=SEED + it + 1)
            for k, m in grupos.items():
                if m.sum():
                    nulo[k].append(float(ciclantes(phn[m]).mean()))
        r["nulo_listas_aleatorias"] = {k: {"media": float(np.mean(v)), "p2_5": float(np.percentile(v, 2.5)),
                                          "p97_5": float(np.percentile(v, 97.5)), "n_iter": len(v)} for k, v in nulo.items() if v}
        # método por cuentas
        cs = np.asarray(ad.X[:, np.concatenate([idx_s, idx_g])].sum(axis=1)).ravel()
        tasa = cs.sum() / umi.sum()
        pv = poisson.sf(cs - 1, tasa * umi)
        cic = pv < 0.01
        r["por_cuentas_ciclante_p01"] = {k: _w(int(cic[m].sum()), int(m.sum())) for k, m in grupos.items() if m.sum()}
        for gname in ("MKI67", "TOP2A"):
            j = np.where(sym == gname)[0][0]
            v = np.asarray(ad.X[:, j].todense()).ravel() > 0
            r[f"{gname}_det"] = {k: _w(int(v[m].sum()), int(m.sum())) for k, m in grupos.items() if m.sum()}
        res[t] = r
        print(f"== {t}")
        for k in ("muestra_entera", "por_cuentas_ciclante_p01", "MKI67_det"):
            print(" ", k, {g: round(v["frac"], 3) for g, v in r[k].items()})
        print("  nulo:", {g: round(v["media"], 3) for g, v in r["nulo_listas_aleatorias"].items()})
        print("  ningún gen S/G2M detectado:", {g: round(v, 3) for g, v in r["ningun_gen_S_ni_G2M_detectado"].items()})
        print("  cero exacto:", {g: round(v, 3) for g, v in r["exactamente_cero_S_y_G2M"].items()})
        print("  decil UMI:", [round(x, 2) for x in r["ciclante_por_decil_UMI"]], "| solo malignas A:", r.get("solo_malignas_A_como_A", {}).get("frac"))
        del ad, an
    guarda(res, "fase.json")
