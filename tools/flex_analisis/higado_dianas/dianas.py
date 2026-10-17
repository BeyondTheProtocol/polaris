"""dianas.py — dianas del hígado sin resolver la identidad célula a célula (REGLAS_HIGADO_v1.json).

Uso: MURO_ALLOW_CLINICAL=1 <nescore>/python -I tools/flex_analisis/higado_dianas/dianas.py <paso>
Pasos: run (todo el cálculo desde las cachés del cierre) | manifiesto | comprueba (77 si faltan cachés)
Tres vías: (1) conjunto de alta confianza por programa luminal con LOO y malla de sensibilidad; (2) deconvolución
del pseudobulk del pool; (3) ancla externa en las 68 malignas limpias del hueso. stdout: solo agregados.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "Agg")

import _h  # noqa: E402  (hilos a 1 antes de numpy)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import _c  # noqa: E402
import _final as F  # noqa: E402
from _h import (APM, COMPARTIMENTOS, CT, DIANAS, DOBLETE, ENDO, ESTROMA, HEP, INMUNE, INMUNE_HUESO_EXTRA, LIGADOS_ER,  # noqa: E402
                MARCADORES, NEG_HEP, NO_RESOLUBLE, PANEL, REF1, SEED)
from _c import wilson  # noqa: E402

POST_HOC = []
TODOS = DIANAS + CT + APM + NEG_HEP
# POST HOC (16-oct-2026, tras ver la primera ejecución). No cambian ningún veredicto congelado: añaden una columna
# «lectura_post_hoc» y diagnósticos, declarados en el json.
def _p(x):
    return "%.1f %%" % (100 * x)


# Plantillas: los números salen de la fila calculada (f = tabla, nunca escritos a mano).
LECTURA_POST_HOC = {
    "FGFR1": lambda f: "«discordante» es artefacto de saturación: en las 68 k = %d de 68 y la cota de Wilson (%.3f) queda bajo el suelo (%.3f). "
                       "En CP10k: hígado %.0f, hueso %.0f." % (f["hueso68_k"], f["hueso68_wilson_lo"], f["hueso68_suelo"], f["via1_cp10k"], f["hueso68_cp10k"]),
    "CCND1": lambda f: "«discordante» es artefacto de saturación: en las 68 k = %d de 68, Wilson %.3f frente a suelo %.3f. CP10k: hígado %.0f, hueso %.0f."
                       % (f["hueso68_k"], f["hueso68_wilson_lo"], f["hueso68_suelo"], f["via1_cp10k"], f["hueso68_cp10k"]),
    "ERBB2": lambda f: "ARN, no HER2-low (su IHQ es HER2 0). CP10k hígado %.2f frente a %.2f en la referencia inmune/endotelio; hueso %.2f."
                       % (f["via1_cp10k"], f["via1_cp10k_ref_R1"], f["hueso68_cp10k"]),
    "ERBB3": lambda f: "Falla la vía 1 por R2: los hepatocitos lo expresan (%s). Frente a inmunes/endotelio supera (Wilson inf. %s > %s). "
                       "Hueso: 68 %s, 87 %s." % (_p(f["via1_ref_R2_hepatocito"]), _p(f["via1_wilson_lo"]), _p(f["via1_ref_R1_inmune_endo"]),
                                               "positiva" if f["hueso68_positiva"] else "no positiva", "positiva" if f["hueso87_positiva"] else "no positiva"),
    "CD276": lambda f: "B7-H3 también en los compartimentos no tumorales (hepatocito %s, NK %s, mieloide %s, endotelio %s, estroma %s)."
                       % tuple(_p(f["via1_ref_" + c]) for c in COMPARTIMENTOS),
    "CHGA": lambda f: "Marcador NE, no diana: «real» = expresión tumoral. Seleccionado por programa luminal: NE disperso dentro de la población luminal (el cierre: sin subpoblación NE aparte).",
    "SYP": lambda f: "Marcador NE, no diana (ver CHGA).",
    "{{DIANA3}}": lambda f: "Factor NE, no diana. Hígado %s, hueso (68) %s." % (_p(f["via1_frac"]), _p(f["hueso68_frac"])),
    "GPNMB": lambda f: "Lo expresan los macrófagos (mieloide %s); el tumor %s." % (_p(f["via1_ref_mieloide"]), _p(f["via1_frac"])),
    "NCAM1": lambda f: "CD56: %d de %d en el conjunto; NK %s." % (f["via1_k"], f["via1_n"], _p(f["via1_ref_NK"])),
    "AR": lambda f: "Lo expresan los hepatocitos (%s); el tumor %s." % (_p(f["via1_ref_R2_hepatocito"]), _p(f["via1_frac"])),
    "SEZ6": lambda f: "Vía 1: fracción %s (Wilson inf. %s) frente a la referencia inmune/endotelio %s; la vía 2 lo marca solo porque no compara con los compartimentos."
                      % (_p(f["via1_frac"]), _p(f["via1_wilson_lo"]), _p(f["via1_ref_R1_inmune_endo"])),
}


class Muestra:
    """Una muestra del cierre con columnas densas de los genes que se usan y su contexto de suelo."""

    def __init__(self, t):
        self.T = F.T(t)
        T_ = self.T
        self.t = t
        self.sym = T_.sym
        self.umi = T_.umi
        self.tipo = T_.tipo
        self.n = T_.n
        o = T_.o
        self.ns, self.nu, self.p_amb = o["nsondas"], o["nsondas_unsp"], o["p_amb"]
        if t == "higado":
            self.limpio = np.ones(self.n, bool)
            self.delta = np.zeros(self.n)
        else:
            self.limpio = o["trib_limpio_T"].astype(bool)
            self.delta = o["son_or_sum"] / float(o["son_n_or"])
        inm = np.isin(self.tipo, F.INMUNES) & self.limpio
        traz = np.isin(self.sym, F.TRAZ[t])
        self.rho = float(T_.X[inm][:, traz].sum() / self.umi[inm].sum() / max(self.p_amb[traz].sum(), 1e-12))
        tnk = np.isin(self.tipo, ["T", "NK"]) & self.limpio
        if tnk.sum() < 30:
            tnk = inm
        trt = np.isin(self.sym, F.TRAZ_TUMOR)
        self.rho_t = float(T_.X[tnk][:, trt].sum() / self.umi[tnk].sum() / max(self.p_amb[trt].sum(), 1e-12))
        self._col = {}
        self._nd = {}

    def j(self, g):
        h = np.where(self.sym == g)[0]
        return int(h[0]) if len(h) else None

    def col(self, g):
        if g not in self._col:
            j = self.j(g)
            self._col[g] = None if j is None else np.asarray(self.T.X[:, j].todense()).ravel()
        return self._col[g]

    def ndet(self, gs):
        key = tuple(sorted(gs))
        if key not in self._nd:
            self._nd[key] = self.T.ndet([g for g in gs if self.j(g) is not None])
        return self._nd[key]


# ---------------------------------------------------------------- medida por gen en un conjunto
def mide(M, g, mal, refs, cur=None):
    j = M.j(g)
    fila = {"gen": g}
    if j is None or M.ns[j] == 0:
        fila["nota"] = "fuera del panel"
        return fila
    v = M.col(g)
    n = int(mal.sum())
    if n == 0:
        return fila
    k = int((v[mal] > 0).sum())
    f, lo, hi = wilson(k, n)
    umi = M.umi
    lam = M.p_amb[j] * M.rho * umi[mal] + M.delta[mal] * M.nu[j]
    lam_t = M.p_amb[j] * max(M.rho, M.rho_t) * umi[mal] + M.delta[mal] * M.nu[j]
    suelo, suelo_t = float(np.mean(1 - np.exp(-lam))), float(np.mean(1 - np.exp(-lam_t)))
    cp = float(v[mal].sum() / umi[mal].sum() * 1e4)
    fila.update({"n": n, "k": k, "frac": f, "wilson_lo": lo, "wilson_hi": hi, "n_sondas": int(M.ns[j]),
                 "cp10k_pseudobulk": cp,
                 "media_cp10k_pos": float(np.mean(v[mal][v[mal] > 0] / umi[mal][v[mal] > 0] * 1e4)) if k else None,
                 "suelo": suelo, "suelo_rho_tumoral": suelo_t, "sobre_suelo": bool(lo - suelo > 0),
                 "sobre_suelo_rho_tumoral": bool(lo - suelo_t > 0)})
    for nombre, rm in refs.items():
        if rm.sum() == 0:
            continue
        wts = _c.TC_suelo().pesos_umi(umi[mal], umi[rm])
        fila["ref_" + nombre] = float(np.sum(wts * (v[rm] > 0)) / max(wts.sum(), 1e-12))
        fila["cp10k_ref_" + nombre] = float(v[rm].sum() / umi[rm].sum() * 1e4)
    if k == 0 and cur is not None:
        cota = 3.0 / n / np.mean(umi[mal]) * 1e4
        c5 = cur.get(str(int(M.ns[j])) if int(M.ns[j]) < 4 else ">=4")
        fila.update({"cota_sup_cp10k_regla3": cota, "cp10k_5pct_curva": c5, "cero_informativo": bool(c5 is not None and cota < c5)})
    return fila


# ---------------------------------------------------------------- vía 1: conjuntos de alta confianza
def fgfr1_alto(M, ref):
    s = M.col("FGFR1") / np.maximum(M.umi, 1)
    dec = _c.deciles(M.umi)
    gd = F.grupos_decil(dec, ref, etiqueta=f"FGFR1_alto {M.t}")
    thr = np.full(M.n, np.inf)
    for d in range(10):
        thr[dec == d] = np.quantile(s[gd[d]], 0.90)
    return s > thr


def conjunto(M, base, k, umi_min, usar_fgfr1, excl_min, panel, alto, inmune):
    m = base & (M.ndet(panel) >= k) & (M.umi >= umi_min)
    if usar_fgfr1:
        m &= alto
    for gs in (HEP, inmune, ENDO, ESTROMA):
        m &= M.ndet(gs) < excl_min
    return m


def positiva_v1(f):
    if "k" not in f:
        return False
    return bool(f["k"] >= 10 and f["wilson_lo"] > f["suelo"] and f["wilson_lo"] > f.get("ref_R1", 1)
                and f["wilson_lo"] > f.get("ref_R2", 1))


def via1(L):
    reg = _h.R["via1_alta_confianza"]
    base = np.isin(L.tipo, ["epitelial", "no_asignado"])
    ref1 = np.isin(L.tipo, REF1)
    alto = fgfr1_alto(L, ref1)
    refs = {"R1": ref1, "R2": L.tipo == "hepatocito"}
    refs.update({c: L.tipo == c for c in COMPARTIMENTOS})
    pr = reg["primario"]
    excl = {">= 2": 2, ">= 1": 1}

    def construye(k, umi_min, usar_f, ex, gen=None):
        panel = [g for g in PANEL if g != gen]
        return conjunto(L, base, k, umi_min, usar_f and gen != "FGFR1", excl[ex], panel, alto, INMUNE)

    k_prim = pr["k"]
    prim = construye(k_prim, pr["UMI_min"], True, pr["exclusiones"])
    if prim.sum() < 200:
        for kk in sorted(reg["malla_sensibilidad"]["k"], reverse=True):
            c = construye(kk, pr["UMI_min"], True, pr["exclusiones"])
            if c.sum() >= 200:
                k_prim, prim = kk, c
                break
        POST_HOC.append(f"regla_si_primario_pequeno aplicada: primario k={k_prim}, n={int(prim.sum())}")
    cur = F.curva(L.T.X, prim, L.ns, L.umi[prim])
    loo = PANEL + ["FGFR1"]
    filas = {}
    for g in TODOS:
        mal = construye(k_prim, pr["UMI_min"], True, pr["exclusiones"], gen=g) if g in loo else prim
        f = mide(L, g, mal, refs, cur)
        f["LOO"] = g in loo
        f["positiva_via1"] = positiva_v1(f)
        if f.get("k"):
            vp = L.col(g)[mal] > 0
            dob = L.ndet(DOBLETE)[mal] >= 1
            f["doblete_inmune_en_pos"] = float(dob[vp].mean())
            f["doblete_inmune_en_neg"] = float(dob[~vp].mean()) if (~vp).any() else None
        filas[g] = f
    malla = []
    ms = reg["malla_sensibilidad"]
    for k in ms["k"]:
        for um in ms["UMI_min"]:
            for usar_f in ms["FGFR1_alto"]:
                for ex in ms["exclusiones"]:
                    c = construye(k, um, usar_f, ex)
                    fila_m = {"k": k, "UMI_min": um, "FGFR1_alto": usar_f, "exclusiones": ex, "n": int(c.sum())}
                    if c.sum() >= 50:
                        for g in DIANAS:
                            mal = construye(k, um, usar_f, ex, gen=g) if g in loo else c
                            if mal.sum() < 50:
                                fila_m[g] = None
                                continue
                            fila_m[g] = positiva_v1(mide(L, g, mal, {"R1": refs["R1"], "R2": refs["R2"]}))
                    malla.append(fila_m)
    dm = pd.DataFrame(malla)
    rob = {}
    for g in DIANAS:
        if g in dm:
            v = dm[g].dropna()
            rob[g] = {"celdas": int(len(v)), "positivas": int(v.astype(bool).sum()),
                      "frac": float(v.astype(bool).mean()) if len(v) else None}
    # POST HOC (16-oct): especificidad interna. La regla primaria SIN la exclusión por tipo de A, sobre las células
    # que A tipó como no tumorales: cuántas pasarían.
    sin_tipo = conjunto(L, np.ones(L.n, bool), pr["k"], pr["UMI_min"], True, excl[pr["exclusiones"]], PANEL, alto, INMUNE)
    no_tum = np.isin(L.tipo, ["hepatocito", "NK", "mieloide", "endotelio", "fibroblasto_estelar"])
    POST_HOC.append("ESPECIFICIDAD_INTERNA_HIGADO: la regla primaria sin exclusión por tipo deja pasar %d de %d células tipadas "
                    "no tumorales (hepatocitos %d de %d)." % (int((sin_tipo & no_tum).sum()), int(no_tum.sum()),
                                                               int((sin_tipo & (L.tipo == "hepatocito")).sum()), int((L.tipo == "hepatocito").sum())))
    info = {"k_primario": k_prim, "n_primario": int(prim.sum()), "n_base": int(base.sum()),
            "n_FGFR1_alto_en_base": int((alto & base).sum()), "UMI_mediana_primario": float(np.median(L.umi[prim])),
            "rho": L.rho, "rho_tumoral": L.rho_t, "n_refs": {k: int(v.sum()) for k, v in refs.items()}, "curva_5pct": cur,
            "n_malla": {f"k{r['k']}|UMI{r['UMI_min']}|F{int(r['FGFR1_alto'])}|ex{r['exclusiones']}": r["n"] for r in malla}}
    return prim, filas, rob, dm, info


# ---------------------------------------------------------------- vía 2: deconvolución del pool
def via2(L, cur):
    pool = np.isin(L.tipo, ["epitelial", "no_asignado"]) & (L.T.ndet(_c.HEP5) < 2)
    marc_g = sorted(set(sum(MARCADORES.values(), [])))
    genes = [g for g in sorted(set(TODOS + marc_g)) if L.j(g) is not None]
    gi = np.array([L.j(g) for g in genes])
    Xg = L.T.X[:, gi].toarray().astype(np.float64)
    amb = L.p_amb[gi]
    grupos = [pool] + [L.tipo == c for c in COMPARTIMENTOS]
    idx = [np.where(m)[0] for m in grupos]
    marc = np.array([genes.index(g) for g in marc_g if g in genes])

    def estima(ix_list, rho):
        cps = [Xg[ix].sum(axis=0) / L.umi[ix].sum() * 1e4 for ix in ix_list]
        return _h.deconvoluciona(cps[0], np.vstack(cps[1:]), amb, rho, marc)

    res = {}
    rng = np.random.default_rng(SEED)
    variantes = {"primaria_rho_no_tumoral": L.rho, "sin_restar_ambiente": 0.0, "rho_tumoral": L.rho_t}
    for nombre, rho in variantes.items():
        w, wT, T, Pst = estima(idx, rho)
        d = {"rho": rho, "pesos": dict(zip(COMPARTIMENTOS, w.tolist())), "w_tumor": wT, "T": dict(zip(genes, T.tolist())),
             "max_no_tumor": dict(zip(genes, Pst.max(axis=0).tolist()))}
        if nombre == "primaria_rho_no_tumoral":
            bs, wb = [], []
            for _ in range(1000):
                r_ = estima([rng.choice(ix, len(ix)) for ix in idx], rho)
                bs.append(r_[2])
                wb.append(r_[0])
            bs, wb = np.array(bs), np.array(wb)
            d["ic95"] = {g: [float(np.nanpercentile(bs[:, i], 2.5)), float(np.nanpercentile(bs[:, i], 97.5))] for i, g in enumerate(genes)}
            d["pesos_ic95"] = {c: [float(np.percentile(wb[:, i], 2.5)), float(np.percentile(wb[:, i], 97.5))] for i, c in enumerate(COMPARTIMENTOS)}
            d["marcadores_residuo_T_mediana_abs"] = float(np.median(np.abs(T[marc])))
        res[nombre] = d
    out = {}
    pri = res["primaria_rho_no_tumoral"]
    for g in genes:
        j = L.j(g)
        c5 = cur.get(str(int(L.ns[j])) if int(L.ns[j]) < 4 else ">=4")
        T, (lo, hi) = pri["T"][g], pri["ic95"][g]
        mx = pri["max_no_tumor"][g]
        out[g] = {"T_cp10k": T, "ic95": [lo, hi], "c5": c5, "T_sin_restar_ambiente": res["sin_restar_ambiente"]["T"][g],
                  "T_rho_tumoral": res["rho_tumoral"]["T"][g], "enriquecimiento_vs_max_no_tumor": (T / mx) if mx > 0 else None,
                  "positiva_via2": bool(c5 is not None and lo >= c5), "traza": bool(lo > 0 and (c5 is None or lo < c5))}
    POST_HOC.append("VIA2_LECTURA: NNLS da pesos ~0 a hepatocito, endotelio y estroma y ~2 %% a NK+mieloide (w_tumor %.3f): el "
                    "pseudobulk del pool no lleva contaminación medible de esos compartimentos, y T_g es casi el pool menos el ambiente. "
                    "Los genes de hepatocito salen con T < 0: el ambiente real del pool es menor que rho, así que la resta es conservadora "
                    "para las dianas. La variante rho tumoral es degenerada (w_tumor %.3f) y no se lee. positiva_via2 no compara con los "
                    "compartimentos: la vía 1 es la que mide «por encima del fondo»."
                    % (res["primaria_rho_no_tumoral"]["w_tumor"], res["rho_tumoral"]["w_tumor"]))
    meta ={k: {kk: vv for kk, vv in v.items() if kk not in ("T", "ic95", "max_no_tumor")} for k, v in res.items()}
    meta["n_pool"] = int(pool.sum())
    meta["n_compartimentos"] = {c: int(len(ix)) for c, ix in zip(COMPARTIMENTOS, idx[1:])}
    return out, meta


# ---------------------------------------------------------------- vía 3: hueso
def via3(B):
    o = B.T.o
    m68 = o["trib_maligna_T_limpio_T"].astype(bool)
    m87 = o["trib_maligna_T_todas"].astype(bool)
    refm = np.isin(B.tipo, _c.TC.REF_TIPOS) & B.limpio
    cur = F.curva(B.T.X, m68, B.ns, B.umi[m68])
    filas = {}
    for nombre, m in (("68", m68), ("87", m87)):
        for g in TODOS:
            f = mide(B, g, m, {"REF": refm}, cur)
            f["positiva_hueso"] = bool("k" in f and f["k"] >= 5 and f["wilson_lo"] > f["suelo"] and f["wilson_lo"] > f["ref_REF"])
            if "cp10k_pseudobulk" in f:
                f["det_predicha_2000"] = float(1 - np.exp(-f["cp10k_pseudobulk"] * 2000 / 1e4))
            filas[(nombre, g)] = f
    alto = fgfr1_alto(B, refm)
    verdad_neg = np.isin(B.tipo, list(_c.TC.REF_TIPOS) + ["fibroblasto_estelar", "osteoclasto", "eritroide"]) & B.limpio & ~m87
    ctrl = []
    for usar_f in (True, False):
        for k in (2, 3, 4, 5, 6):
            c = conjunto(B, np.ones(B.n, bool), k, 0, usar_f, 2, PANEL, alto, INMUNE + INMUNE_HUESO_EXTRA)
            ctrl.append({"FGFR1_alto": usar_f, "k": k, "n_llamadas": int(c.sum()),
                         "sensibilidad_68": _c.w(int((c & m68).sum()), int(m68.sum())),
                         "especificidad": _c.w(int((~c & verdad_neg).sum()), int(verdad_neg.sum())),
                         "llamadas_fuera_de_87": int((c & ~m87).sum())})
    # POST HOC (16-oct, tras ver la primera ejecución): la MISMA regla de alta confianza en el hueso, para separar
    # «diferencia entre lesiones» de «diferencia de selección». Dos celdas fijadas aquí: k2 con FGFR1_alto, k3 sin él.
    misma = {}
    for etiqueta, k, usar_f in (("k2F", 2, True), ("k3", 3, False)):
        c = conjunto(B, np.ones(B.n, bool), k, 0, usar_f, 2, PANEL, alto, INMUNE + INMUNE_HUESO_EXTRA)
        for g in TODOS:
            f = mide(B, g, c, {"REF": refm})
            if "k" in f:
                misma.setdefault(g, {}).update({
                    f"hueso_misma_regla_{etiqueta}_n": f["n"], f"hueso_misma_regla_{etiqueta}_frac": f["frac"],
                    f"hueso_misma_regla_{etiqueta}_wilson_lo": f["wilson_lo"], f"hueso_misma_regla_{etiqueta}_ref": f["ref_REF"],
                    f"hueso_misma_regla_{etiqueta}_cp10k": f["cp10k_pseudobulk"],
                    f"hueso_misma_regla_{etiqueta}_positiva": bool(f["k"] >= 5 and f["wilson_lo"] > f["suelo"] and f["wilson_lo"] > f["ref_REF"])})
    POST_HOC.append("HUESO_MISMA_REGLA: la regla de alta confianza del hígado aplicada al hueso (k2 con FGFR1_alto; k3 sin él), "
                    "columnas hueso_misma_regla_* de la tabla. Separa diferencia entre lesiones de diferencia de selección.")
    return filas, ctrl, misma, {"n_ref": int(refm.sum()), "n_verdad_neg": int(verdad_neg.sum()), "rho": B.rho, "rho_tumoral": B.rho_t,
                         "UMI_mediana_68": float(np.median(B.umi[m68]))}


# ---------------------------------------------------------------- veredicto (REGLAS «veredicto»)
def veredicto(g, f1, rob, f2, f3):
    if f1.get("nota") == "fuera del panel":
        return "no medible (fuera del panel)"
    if g in NO_RESOLUBLE:
        return "no medible (parálogo)"
    if f1.get("k") == 0 and not f1.get("cero_informativo", False):
        return "no medible (cero no informativo)"
    p1, p2 = f1["positiva_via1"], f2["positiva_via2"]
    r = (rob or {}).get("frac")
    robusta = r is not None and r >= 0.75
    if p1 and robusta and p2:
        v = "diana real en el hígado"
    elif (p1 and p2 and not robusta) or (p1 != p2) or (p1 and f2["traza"]):
        v = "débil"
    else:
        v = "no" + (" (cero informativo)" if f1.get("k") == 0 else "")
    if f3 is not None and "positiva_hueso" in f3:
        v += "; " + ("coherente con el hueso" if f3["positiva_hueso"] == p1 else "discordante con el hueso")
    return v


def run():
    POST_HOC.clear()
    F.POST_HOC.clear()
    L, B = Muestra("higado"), Muestra("hueso")
    res = {"reglas": "REGLAS_HIGADO_v1.json", "reglas_sha256": _c.sha256(_h.REGLAS), "semilla": SEED,
           "cache_sha256": {f: _c.sha256(_c.cachep(f)) for f in sorted(os.listdir(_c.CACHE))}}
    prim, f1, rob, dm, info1 = via1(L)
    print("VÍA 1: n primario", info1["n_primario"], "k", info1["k_primario"], "UMI mediana", round(info1["UMI_mediana_primario"]),
          "| base", info1["n_base"], "| FGFR1_alto en base", info1["n_FGFR1_alto_en_base"], "| rho", round(L.rho, 3),
          "rho_t", round(L.rho_t, 3))
    print("   n malla:", info1["n_malla"])
    f2, meta2 = via2(L, info1["curva_5pct"])
    p2m = meta2["primaria_rho_no_tumoral"]
    print("VÍA 2: pool", meta2["n_pool"], "pesos", {k: round(v, 4) for k, v in p2m["pesos"].items()}, "w_tumor", round(p2m["w_tumor"], 3),
          "| residuo |T| en marcadores (mediana)", round(p2m["marcadores_residuo_T_mediana_abs"], 3))
    print("   w_tumor variantes:", {k: round(v["w_tumor"], 3) for k, v in meta2.items() if isinstance(v, dict) and "w_tumor" in v})
    f3, ctrl, hueso_misma, meta3 = via3(B)
    c4 = [c for c in ctrl if c["FGFR1_alto"] and c["k"] == info1["k_primario"]][0]
    POST_HOC.append("FUNDAMENTO_VIA1_HUESO: la regla de alta confianza aplicada al hueso tiene especificidad %.3f y sensibilidad %.2f "
                    "frente a las 68 (k %d, FGFR1_alto): el conjunto es un subconjunto de células muy luminales, no el tumor entero. "
                    "Las fracciones de la vía 1 describen ese subconjunto." % (c4["especificidad"]["frac"], c4["sensibilidad_68"]["frac"], c4["k"]))
    print("CONTROL vía 1 en hueso (FGFR1_alto, k, n, sens68, espec):",
          [(c["FGFR1_alto"], c["k"], c["n_llamadas"], round(c["sensibilidad_68"]["frac"], 2), round(c["especificidad"]["frac"], 3)) for c in ctrl])
    xs, ys, gs = [], [], []
    for g in DIANAS:
        a, b = f1[g], f3[("68", g)]
        if "cp10k_pseudobulk" in a and "cp10k_pseudobulk" in b:
            xs.append(np.log2((a["cp10k_pseudobulk"] + 0.05) / (a["cp10k_ref_R1"] + 0.05)))
            ys.append(np.log2((b["cp10k_pseudobulk"] + 0.05) / (b["cp10k_ref_REF"] + 0.05)))
            gs.append(g)
    from scipy.stats import spearmanr
    rs = float(spearmanr(xs, ys).statistic)
    rng = np.random.default_rng(SEED)
    perm = np.array([spearmanr(xs, rng.permutation(ys)).statistic for _ in range(10000)])
    p_perm = float((1 + np.sum(np.abs(perm) >= abs(rs))) / 10001)
    # POST HOC: el bloque de dianas ~0 en las dos lesiones empata en log2(0,05/0,05) e infla la correlación. Se da
    # también sobre las detectables (fracción >= 2 % en el conjunto del hígado o en las 68).
    det = [i for i, g in enumerate(gs) if max(f1[g].get("frac") or 0, f3[("68", g)].get("frac") or 0) >= 0.02]
    xd, yd = [xs[i] for i in det], [ys[i] for i in det]
    rs_d = float(spearmanr(xd, yd).statistic)
    perm_d = np.array([spearmanr(xd, rng.permutation(yd)).statistic for _ in range(10000)])
    coh = {"spearman_log2_conjunto_sobre_ref": rs, "p_permutacion": p_perm, "n_dianas": len(gs),
           "POST_HOC_detectables": {"spearman": rs_d, "p_permutacion": float((1 + np.sum(np.abs(perm_d) >= abs(rs_d))) / 10001),
                                    "n_dianas": len(det), "dianas": [gs[i] for i in det]}}
    print("   detectables (>= 2 % en alguna lesión): Spearman", round(rs_d, 3), "n", len(det), "p", round(coh["POST_HOC_detectables"]["p_permutacion"], 4))
    n_rt = sum(1 for g in DIANAS if f1[g].get("sobre_suelo_rho_tumoral"))
    POST_HOC.append("RHO_TUMORAL_VIA1: con el suelo de rho tumoral (%.3f), %d de %d dianas quedan por encima en el conjunto de alta "
                    "confianza: %s." % (L.rho_t, n_rt, len(DIANAS), ", ".join(g for g in DIANAS if f1[g].get("sobre_suelo_rho_tumoral")) or "ninguna"))
    print("VÍA 3: Spearman hígado-AC vs hueso-68 (log2 sobre ref)", round(rs, 3), "p", round(p_perm, 4), "n", len(gs))
    filas = []
    for g in TODOS:
        a = f1[g]
        b = f2.get(g, {"positiva_via2": False, "traza": False})
        c = f3[("68", g)]
        c87 = f3[("87", g)]
        clase = "diana" if g in DIANAS else ("control_CT" if g in CT else ("presentacion" if g in APM else "control_negativo_hepatocito"))
        fila = {"gen": g, "clase": clase, "n_sondas": a.get("n_sondas"),
                "via1_n": a.get("n"), "via1_k": a.get("k"), "via1_frac": a.get("frac"), "via1_wilson_lo": a.get("wilson_lo"),
                "via1_wilson_hi": a.get("wilson_hi"), "via1_suelo": a.get("suelo"), "via1_suelo_rho_tumoral": a.get("suelo_rho_tumoral"),
                "via1_ref_R1_inmune_endo": a.get("ref_R1"), "via1_ref_R2_hepatocito": a.get("ref_R2"),
                **{"via1_ref_" + cc: a.get("ref_" + cc) for cc in COMPARTIMENTOS},
                "via1_cp10k": a.get("cp10k_pseudobulk"), "via1_cp10k_ref_R1": a.get("cp10k_ref_R1"),
                "via1_positiva": a.get("positiva_via1"), "via1_LOO": a.get("LOO"), "via1_robustez_malla": (rob.get(g) or {}).get("frac"),
                "via1_doblete_pos": a.get("doblete_inmune_en_pos"), "via1_doblete_neg": a.get("doblete_inmune_en_neg"),
                "via1_cero_informativo": a.get("cero_informativo"),
                "via2_T_cp10k": b.get("T_cp10k"), "via2_ic95_lo": (b.get("ic95") or [None, None])[0],
                "via2_ic95_hi": (b.get("ic95") or [None, None])[1], "via2_c5": b.get("c5"),
                "via2_T_sin_restar_amb": b.get("T_sin_restar_ambiente"), "via2_T_rho_tumoral": b.get("T_rho_tumoral"),
                "via2_enriq_vs_max_no_tumor": b.get("enriquecimiento_vs_max_no_tumor"), "via2_positiva": b.get("positiva_via2"),
                "via2_traza": b.get("traza"),
                "hueso68_k": c.get("k"), "hueso68_frac": c.get("frac"), "hueso68_wilson_lo": c.get("wilson_lo"),
                "hueso68_suelo": c.get("suelo"), "hueso68_ref": c.get("ref_REF"), "hueso68_cp10k": c.get("cp10k_pseudobulk"),
                "hueso68_positiva": c.get("positiva_hueso"), "hueso87_frac": c87.get("frac"), "hueso87_positiva": c87.get("positiva_hueso"),
                "higadoAC_det_predicha_2000": (1 - np.exp(-a["cp10k_pseudobulk"] * 2000 / 1e4)) if "cp10k_pseudobulk" in a else None,
                "hueso68_det_predicha_2000": c.get("det_predicha_2000")}
        if clase == "diana":
            fila["veredicto"] = veredicto(g, a, rob.get(g), b, c)
        elif clase == "control_CT":
            fila["veredicto"] = "control CT: fuera del panel" if a.get("nota") == "fuera del panel" else "control CT (cerrado)"
        elif clase == "presentacion":
            fila["veredicto"] = "nivel relativo (no se puede afirmar pérdida)"
        else:
            fila["veredicto"] = "control negativo (dobletes de hepatocito)"
        notas = []
        if a.get("nota"):
            notas.append(a["nota"])
        if a.get("n_sondas") == 1:
            notas.append("1 sonda")
        if g in LIGADOS_ER:
            notas.append("ligado a ER: el panel de identidad lo favorece")
        if g in ("ESR1", "FGFR1"):
            notas.append("LOO: medido con conjunto construido sin él")
        if g in NO_RESOLUBLE:
            notas.append("no resoluble (parálogo de MAGEA3)")
        fila["notas"] = "; ".join(notas)
        fila["lectura_post_hoc"] = LECTURA_POST_HOC[g](fila) if g in LECTURA_POST_HOC else ""
        if g in hueso_misma:
            fila.update(hueso_misma[g])
        filas.append(fila)
    df = pd.DataFrame(filas).round(6)
    df.to_csv(_h.outp("tabla_dianas_higado.csv"), index=False)
    dm.to_csv(_h.outp("malla_via1.csv"), index=False)
    _h.guarda_npz("conjuntos_higado_dianas.npz", primario_alta_confianza=prim)
    res.update({"via1": {"info": info1, "robustez": rob, "por_gen": f1}, "via2": {"meta": meta2, "por_gen": f2},
                "via3": {"meta": meta3, "por_gen": {f"{k[0]}|{k[1]}": v for k, v in f3.items()},
                         "control_verdad_conocida_via1": ctrl, "coherencia_global": coh},
                "POST_HOC": sorted(set(POST_HOC + F.POST_HOC))})
    _h.guarda(res, "higado_dianas.json")
    pd.set_option("display.width", 320)
    pd.set_option("display.max_rows", 200)
    pd.set_option("display.max_colwidth", 60)
    cols = ["gen", "n_sondas", "via1_k", "via1_frac", "via1_wilson_lo", "via1_suelo", "via1_ref_R1_inmune_endo", "via1_ref_R2_hepatocito",
            "via1_robustez_malla", "via2_T_cp10k", "via2_ic95_lo", "via2_c5", "hueso68_frac", "hueso68_ref", "veredicto"]
    print(df[cols].round(3).to_string(index=False))
    print("POST_HOC:", res["POST_HOC"])


def manifiesto():
    d = _h.out_dir()
    sal = {f: _c.sha256(os.path.join(d, f)) for f in sorted(os.listdir(d))
           if os.path.isfile(os.path.join(d, f)) and not f.startswith("MANIFIESTO")}
    scr = {f: _c.sha256(os.path.join(_h.AQUI, f)) for f in sorted(os.listdir(_h.AQUI)) if f.endswith((".py", ".json"))}
    try:
        commit = subprocess.check_output(["git", "-C", _h.AQUI, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        commit = "desconocido"
    _h.guarda({"salidas": sal, "scripts": scr, "caches": {f: _c.sha256(_c.cachep(f)) for f in sorted(os.listdir(_c.CACHE))},
               "commit_al_generar": commit, "semilla": SEED}, "MANIFIESTO_HIGADO_DIANAS.json")
    for f, h in sal.items():
        print(f, h)


def main(argv):
    if argv and argv[0] == "comprueba":
        faltan = [f for f in ("X_higado.npz", "X_hueso.npz", "obs_higado.npz", "obs_hueso.npz") if not os.path.exists(os.path.join(_c.CACHE, f))]
        print("faltan cachés: %d" % len(faltan) if faltan else "cachés presentes")
        return 77 if faltan else 0
    if argv and argv[0] == "manifiesto_ruta":
        print(os.path.join(_h.SALIDA, "MANIFIESTO_HIGADO_DIANAS.json"))
        return 0
    if not argv or argv[0] not in ("run", "manifiesto"):
        print(__doc__)
        return 2
    if argv[0] == "run":
        run()
    else:
        manifiesto()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
