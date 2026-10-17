"""Etapa FINAL y control positivo de REFCON (REGLAS_HIGADO_CELULA_v1 + ENMIENDA_1). Todo desde cachés.

Vías: criterio estándar (v4 + exclusión de dobletes), criterio estricto (sin genes de linaje), REFCON (si es
aplicable). Para cada una: C1 AUC, C2 FP, C3 r por brazo con el hueso, C4 estabilidad de dianas (un número para
todas), C5 fracción frente a la H&E; C6 (8p11) retirado. Residuo con scDblFinder, matriz scCDC y dianas finales.
Salidas: higado_celula.json, metricas_higado_celula.csv, dianas_higado_celula.csv y aux/ (tablas de dianas del código
reutilizado). stdout: solo agregados.
"""
import collections
import contextlib
import io
import os
import subprocess
import time

import numpy as np
import pandas as pd

import _hc
import _a  # noqa: F401  (ambiente: hilos a 1, TC y _c)
from _a import TC, _c
import _final as F
import _amb_final as AF
import _hc_dobletes as HD
import refcon_run as RR

R = _hc.lee_json(_hc.REGLAS)
R4 = AF.R4
POST_HOC = []
DIANAS3 = ["ANKRD30A", "TPBG", "ERBB3"]
DIANAS6 = R["dianas_final"]["genes"]
TESTIGOS = AF.R1["exito_descontaminacion"]["testigos"]
AMP8 = R4["dosis_8p11_11q13"]["8p11_amplicon"]
NBOOT = 1000


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def con_matriz(t, X):
    T_ = F.T(t)
    T_.X = X.tocsr()
    T_.umi = np.asarray(T_.X.sum(axis=1)).ravel().astype(float)
    return T_


# ---------------------------------------------------------------- brazos (regla del cierre, parametrizada)
def brazos(L, B, mal68, refB, cand, nulo, hep, quitar_linaje=True, excluir=None):
    """Copia parametrizada de _final.brazos_genes: con (True, EXCLUIR_CHR) tiene que dar lo mismo (se comprueba)."""
    excluir = _c.EXCLUIR_CHR if excluir is None else excluir
    chrom, start = L.o["chrom"], L.o["start"]
    ok = (start >= 0) & np.isin(chrom, list(_c.CENTROMEROS_MB)) & ~np.isin(chrom, list(excluir))
    cpL = np.asarray(L.X.sum(axis=0)).ravel() / L.X.sum() * 1e4
    cpB = np.asarray(B.X.sum(axis=0)).ravel() / B.X.sum() * 1e4
    ok &= (cpL >= 0.5) & (cpB >= 0.5)

    def pb(T_, m):
        return np.asarray(T_.X[m].sum(axis=0)).ravel() / max(T_.X[m].sum(), 1) * 1e4

    def lfc(a, b):
        return np.abs(np.log2((a + 0.05) / (b + 0.05)))
    if quitar_linaje:
        lin = (lfc(pb(B, mal68), pb(B, refB)) > 1) | (lfc(pb(L, cand), pb(L, nulo)) > 1) | (lfc(pb(L, hep), pb(L, nulo)) > 1)
        ok &= ~lin
    brazo = np.array([(c + ("q" if (c in _c.ACROCENTRICOS or s_ >= _c.CENTROMEROS_MB.get(c, 0) * 1e6) else "p")) if o_ else ""
                      for c, s_, o_ in zip(chrom, start, ok)])
    cuenta = pd.Series(brazo[ok]).value_counts()
    validos = sorted([b for b, n in cuenta.items() if n >= 20], key=lambda x: (int(x[3:-1]), x[-1]))
    ok &= np.isin(brazo, validos)
    return ok, brazo, validos


class Ctx:
    """Todo lo común: matrices crudas, QC, dobletes, grupos, genes de cada criterio y plantillas del hueso."""

    def __init__(self):
        self.L0, self.B0 = F.T("higado"), F.T("hueso")
        L0, B0 = self.L0, self.B0
        self.qc = AF.qc_v4(L0)
        self.D = HD.scdbl("dbr006")
        self.Dn = HD.scdbl("dbrNULL")
        assert len(self.D["doblete"]) == L0.n == len(self.Dn["doblete"])
        self.gsel = AF.genes_v4(L0)
        self.lin = AF.linaje(L0, B0, L0)
        self.genes = {"estandar": self.gsel, "estricto": self.gsel & ~self.lin}
        qc_d = dict(self.qc)
        qc_d["ngen"] = np.where(self.D["doblete"], -1, self.qc["ngen"])
        self.grp = {"base": AF.grupos_v4(L0, qc_d), "sin_exclusion_dobletes": AF.grupos_v4(L0, self.qc)}
        self.nulo = np.isin(L0.tipo, AF.NULO)
        self.hep_tipo = L0.tipo == "hepatocito"
        self.mal68 = B0.o["trib_maligna_T_limpio_T"].astype(bool)
        self.refB = np.isin(B0.tipo, TC.REF_TIPOS) & B0.o["trib_limpio_T"].astype(bool)
        args = (L0, B0, self.mal68, self.refB, ~self.nulo & ~self.hep_tipo, self.nulo, self.hep_tipo)
        g0, b0, v0, _ = F.brazos_genes(*args)
        g1, b1, v1 = brazos(*args)
        assert (g0 == g1).all() and v0 == v1 and (b0[g0] == b1[g1]).all(), "brazos local != _final.brazos_genes"
        self.arm = {"primaria": (g1, b1, v1),
                    "con_linaje": brazos(*args, quitar_linaje=False),
                    "con_amplicones": brazos(*args, excluir={"chrX", "chrY", "chrM"})}
        self.plantilla = {k: F.centra(F.valores_brazo(B0, *a, self.refB))[self.mal68].mean(axis=0) for k, a in self.arm.items()}


# ---------------------------------------------------------------- 8p11 (C6: comprobación de la retirada)
def potencia_8p11(L0):
    o = L0.o
    G = o["son_ven_counts"].astype(np.int64)
    ns = o["son_ven_nsondas"].astype(float)
    pool = np.isin(L0.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2)
    tasa = G[pool].sum(axis=0) / (pool.sum() * np.maximum(ns, 1))
    sil = (o["son_ven_chr"].astype(str) == "chr8") & (tasa < 0.001) & (ns > 0)
    tot = int(G[pool][:, sil].sum())
    return {"UMI_silentes_8p11_pool": tot, "sondas_silentes": float(ns[sil].sum()), "genes_silentes": int(sil.sum()),
            "minimo_regla": 200, "RETIRADO": bool(tot < 200), "n_pool": int(pool.sum())}


# ---------------------------------------------------------------- residuo (D2a)
def _origenes(D, m):
    c = collections.Counter(D["origen"][m & D["doblete"]])
    tumor = sum(v for k, v in c.items() if ("epitelial" in k or "no_asignado" in k))
    hep = sum(v for k, v in c.items() if "hepatocito" in k)
    return {"n_dobletes": int((m & D["doblete"]).sum()), "con_epitelial_o_no_asignado": int(tumor), "con_hepatocito": int(hep),
            "top": {k: int(v) for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:8]}}


def residuo(ctx):
    L0 = ctx.L0
    CB = AF.obj("higado", "cellbender_fpr0.01")
    out = {"matriz_primaria": "cellbender_fpr0.01", "doblete_primario": "dbr006"}
    tes = np.isin(L0.sym, TESTIGOS)
    h5 = np.isin(L0.sym, TC.HEP5)
    pool = np.isin(L0.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2)
    for mname, T_ in (("cellbender_fpr0.01", CB), ("cruda", L0)):
        tplus = np.asarray(T_.X[:, tes].sum(axis=1)).ravel() > 0
        hplus = np.asarray(T_.X[:, h5].sum(axis=1)).ravel() > 0
        for dname, D in (("dbr006", ctx.D), ("dbrNULL", ctx.Dn)):
            d = D["doblete"]
            r = {}
            for nombre, grupo, marca in (("nulo_testigos", ctx.nulo, tplus), ("pool_HEP5_informativo", pool, hplus)):
                a = grupo & marca
                b = grupo & ~marca
                E = _hc.w(int((a & d).sum()), int(a.sum()))
                r[nombre] = {"n_grupo": int(grupo.sum()), "marca_mas": _hc.w(int(a.sum()), int(grupo.sum())),
                             "marca_mas_sin_dobletes": _hc.w(int((a & ~d).sum()), int((grupo & ~d).sum())),
                             "E_dobletes_entre_marca_mas": E,
                             "dobletes_entre_marca_menos": _hc.w(int((b & d).sum()), int(b.sum())),
                             "OR_doblete_marca_mas_vs_menos": _hc.katz(int((a & d).sum()), int((a & ~d).sum()), int((b & d).sum()), int((b & ~d).sum())),
                             "origenes_dobletes_del_grupo": _origenes(D, grupo)}
            out[f"{mname}|{dname}"] = r
    p = out["cellbender_fpr0.01|dbr006"]["nulo_testigos"]
    e = p["E_dobletes_entre_marca_mas"]["frac"] or 0.0
    if e >= 0.5:
        lect = "LOS DOBLETES EXPLICAN EL RESIDUO"
    elif e >= 0.2 and p["OR_doblete_marca_mas_vs_menos"]["excluye_1"]:
        lect = "LOS DOBLETES EXPLICAN UNA PARTE DEL RESIDUO"
    else:
        lect = "LOS DOBLETES NO EXPLICAN EL RESIDUO"
    out["LECTURA"] = lect
    out["dobletes_totales"] = {"dbr006": _hc.w(int(ctx.D["doblete"].sum()), L0.n), "dbrNULL": _hc.w(int(ctx.Dn["doblete"].sum()), L0.n),
                               "por_tipo_dbr006": {t: _hc.w(int((ctx.D["doblete"] & (L0.tipo == t)).sum()), int((L0.tipo == t).sum()))
                                                   for t in sorted(set(L0.tipo))}}
    return out


# ---------------------------------------------------------------- scCDC
def sccdc(ctx):
    Mh, gcg_h, err_h = HD.sccdc("higado")
    Mb, gcg_b, err_b = HD.sccdc("hueso")
    sym = dict(zip(ctx.L0.o["gene_ids"].astype(str), ctx.L0.sym.astype(str)))
    info = {"higado": {"error": err_h, "n_GCG": len(gcg_h), "GCG": sorted(sym.get(g, g) for g in gcg_h)},
            "hueso": {"error": err_b, "n_GCG": len(gcg_b), "GCG": sorted(sym.get(g, g) for g in gcg_b)}}
    if Mh is None:
        info.update({"valida": False, "motivo": "sin matriz del hígado"})
        return info, None
    for t, g in (("higado", gcg_h), ("hueso", gcg_b)):
        s = set(sym.get(x, x) for x in g)
        info[t]["HEP5_en_GCG"] = sorted(s & set(TC.HEP5))
        info[t]["testigos_en_GCG"] = sorted(s & set(TESTIGOS))
    Lc = con_matriz("higado", Mh)
    if Mb is None:
        Bc = ctx.B0
        POST_HOC.append("SCCDC_HUESO_CRUDO (previsto en ENMIENDA_1): scCDC sin matriz del hueso; la batería usa el hueso crudo")
    else:
        Bc = con_matriz("hueso", Mb)
    with contextlib.redirect_stdout(io.StringIO()):
        ex = AF.exito(ctx.L0, Lc, ctx.B0, Bc)
    info["bateria_AMBIENTE_v1"] = ex
    info["valida"] = bool(ex["EXITO"] and ex["RETENCION"])
    return info, Lc


# ---------------------------------------------------------------- una vía, una variante
def clon(V, plantilla, sel, extras):
    perfil = V[sel].mean(axis=0)
    r = corr(perfil, plantilla)
    idx = np.where(sel)[0]
    rng = np.random.default_rng(_hc.SEED)
    Vs = V[idx]
    rs = np.array([corr(Vs[rng.integers(0, len(idx), len(idx))].mean(axis=0), plantilla) for _ in range(NBOOT)])
    out = {"r": r, "ic95_bootstrap": [float(np.percentile(rs, 2.5)), float(np.percentile(rs, 97.5))], "n_celulas": int(len(idx))}
    for k, m in extras.items():
        out["control_" + k] = corr(V[m].mean(axis=0), plantilla) if m.any() else None
    return out


def evalua(ctx, L, grp, score, call, etiqueta, V_arm=None):
    """Batería D3 de una llamada. score y call: arrays por célula. V_arm: {clave: (V, plantilla)} para C3."""
    pool, hep, pp, ref = grp["pool"], grp["hep"], grp["pp"], grp["ref"]
    hnr = hep & ~ref
    m = {"FP_H2": _hc.w(int((call & grp["h2"]).sum()), int(grp["h2"].sum())),
         "FP_periportales": _hc.w(int((call & pp).sum()), int(pp.sum())),
         "en_pool": _hc.w(int((call & pool).sum()), int(pool.sum())),
         "en_no_referencia": _hc.w(int((call & grp["no_ref"]).sum()), int(grp["no_ref"].sum())),
         "en_hepatocitos_QC": _hc.w(int((call & hep).sum()), int(hep.sum()))}
    a_h, a_p, a_n = F.auc(score[pool], score[hep]), F.auc(score[pool], score[pp]), F.auc(score[pool], score[hnr])
    c1 = {"AUC_pool_vs_hepatocitos": a_h, "ic95_hep": _hc.hanley(a_h, int(pool.sum()), int(hep.sum())),
          "AUC_pool_vs_periportales": a_p, "ic95_pp": _hc.hanley(a_p, int(pool.sum()), int(pp.sum())),
          "AUC_pool_vs_hepatocitos_no_referencia_INFORMATIVO": a_n, "ic95_hnr": _hc.hanley(a_n, int(pool.sum()), int(hnr.sum()))}
    c1["PASA"] = bool(a_h >= 0.6 and a_p >= 0.6)
    c2 = {"FP_periportales": m["FP_periportales"], "FP_H2": m["FP_H2"],
          "PASA": bool(m["FP_periportales"]["frac"] <= 0.01 and m["FP_H2"]["frac"] <= 0.02)}
    cp = call & pool
    c3 = {}
    if V_arm is not None and cp.sum() >= 2:
        extras = {"hepatocitos_QC": hep, "pool_no_llamadas": pool & ~call}
        for k, (V, pl) in V_arm.items():
            c3[k] = clon(V, pl, cp, extras)
        c3["PASA"] = bool(c3["primaria"]["r"] > 0.3)
    else:
        c3["PASA"] = False
        c3["nota"] = "sin llamadas suficientes en el pool"
    det = {g: (float((ctx.L0.g(g)[cp] > 0).mean()) if cp.any() else None) for g in DIANAS6}
    nr = call & grp["no_ref"]
    c5 = {"fraccion_sobre_QC": _hc.w(int(nr.sum()), int(grp["keep"].sum())),
          "fraccion_sobre_pool_INFORMATIVO": _hc.w(int(cp.sum()), int(pool.sum())), "banda_HE": [0.58, 0.82]}
    f5 = c5["fraccion_sobre_QC"]["frac"]
    c5["PASA"] = bool(f5 is not None and 0.58 <= f5 <= 0.82)
    return {"etiqueta": etiqueta, "metricas": m, "C1": c1, "C2": c2, "C3": c3, "C5": c5, "deteccion_dianas_pool": det,
            "umbral": None}


def V_brazos(ctx, L, ref):
    return {k: (F.centra(F.valores_brazo(L, *ctx.arm[k], ref)), ctx.plantilla[k]) for k in ctx.arm}


def criterio(ctx, L, grp, genes, etiqueta):
    with contextlib.redirect_stdout(io.StringIO()):
        r4, call, sc = AF.v4(L, grp, genes)
    ev = evalua(ctx, L, grp, sc, call, etiqueta, V_brazos(ctx, L, grp["ref"]))
    ev["umbral"] = r4["umbral_corr"]
    ev["n_genes"] = int(genes.sum())
    ev["n_ventanas"] = r4["n_ventanas"]
    ev["v4_internas"] = {"EXITO_v4": r4["EXITO_v4"], "RESUELTO_v4": r4["RESUELTO_v4"], "AUC_v4": r4["AUC_pool_vs_hepatocitos"]}
    return ev, call


def composicion_dobletes(ctx, grp_sin, call_sin):
    """Control de dobletes (informativo): en la variante SIN exclusión, cuántas llamadas son dobletes y de qué origen."""
    D = ctx.D
    nr = call_sin & grp_sin["no_ref"]
    pool = grp_sin["pool"]
    a, b = call_sin & pool, pool & ~call_sin
    return {"dobletes_entre_llamadas": _hc.w(int((nr & D["doblete"]).sum()), int(nr.sum())),
            "dobletes_en_QC": _hc.w(int((grp_sin["keep"] & D["doblete"]).sum()), int(grp_sin["keep"].sum())),
            "OR_doblete_llamadas_vs_no_llamadas_en_pool": _hc.katz(int((a & D["doblete"]).sum()), int((a & ~D["doblete"]).sum()),
                                                                   int((b & D["doblete"]).sum()), int((b & ~D["doblete"]).sum())),
            "origenes_entre_llamadas": _origenes(D, nr)}


# ---------------------------------------------------------------- REFCON
def _refcon_log2(t, ctx):
    """Matriz log2(max(cociente, 1e-3)) de REFCON (células en el orden de la caché) y los índices de gen de la caché."""
    T_ = ctx.L0 if t == "higado" else ctx.B0
    gi, _, _ = RR.genes_autosomas(T_.o)
    nombres = RR.simbolos_unicos(T_.o["simbolo"][gi])
    if t == "hueso":
        rutas = [_hc.cachep("refcon", "hueso", "ens3_cn.npz")]
    else:
        d = _hc.cached("refcon", "higado")
        rutas = [os.path.join(d, x, "ens3_cn.npz") for x in sorted(os.listdir(d)) if x.startswith("trozo_")]
    bloques, celdas = [], []
    for p in rutas:
        z = np.load(p, allow_pickle=True)
        assert list(z["genes"].astype(str)) == nombres, "genes de REFCON fuera de orden"
        bloques.append(z["cn"].astype(np.float32))
        celdas += list(z["cells"].astype(str))
    assert celdas == [f"c{i}" for i in range(T_.n)], "células de REFCON fuera de orden o incompletas"
    cn = np.concatenate(bloques)
    del bloques
    # ENMIENDA_2: REFCON deja NaN en genes sin predicción. Se imputan con la convención del propio REFCON
    # (refcon.downstream.common.impute: media del gen entre células; gen todo NaN → cociente 1), ANTES del log2.
    nan = ~np.isfinite(cn)
    info = {"celdas_NaN": int(nan.sum()), "frac_NaN": float(nan.mean()), "genes_todo_NaN": int(nan.all(axis=0).sum()),
            "genes_con_algun_NaN": int(nan.any(axis=0).sum())}
    if nan.any():
        with np.errstate(all="ignore"):
            cm = np.nanmean(np.where(nan, np.nan, cn), axis=0)
        cm = np.where(np.isfinite(cm), cm, 1.0).astype(np.float32)
        cn[nan] = np.take(cm, np.where(nan)[1])
    del nan
    np.maximum(cn, 1e-3, out=cn)
    np.log2(cn, out=cn)
    _NAN_REFCON[t] = info
    return cn, gi, [_hc.sha256(p) for p in rutas]


_NAN_REFCON = {}


def _refcon_brazos(Lg, gi, arm, ref):
    g_ok, brazo, validos = arm
    col = {j: k for k, j in enumerate(gi)}
    A = np.zeros((Lg.shape[1], len(validos)), dtype=np.float32)
    for j, b in enumerate(validos):
        cols = [col[x] for x in np.where(g_ok & (brazo == b))[0] if x in col]
        A[cols, j] = 1.0 / len(cols)
    V = Lg @ A
    V = V - V[ref].mean(axis=0)
    return V - V.mean(axis=1, keepdims=True)


def gate_refcon():
    """Control positivo de REFCON en el hueso (antes de inferir el hígado). Escribe cache/refcon/gate.json."""
    ctx = Ctx()
    Lg, gi, shas = _refcon_log2("hueso", ctx)
    B0 = ctx.B0
    amp = [k for k, j in enumerate(gi) if B0.sym[j] in AMP8]
    s = Lg[:, amp].mean(axis=1)
    g1 = {"genes_8p11_en_REFCON": [str(B0.sym[gi[k]]) for k in amp], "mediana_68": float(np.median(s[ctx.mal68])),
          "p95_referencia": float(np.quantile(s[ctx.refB], 0.95))}
    g1["PASA"] = bool(g1["mediana_68"] > g1["p95_referencia"])
    V = _refcon_brazos(Lg, gi, ctx.arm["primaria"], ctx.refB)
    r = corr(V[ctx.mal68].mean(axis=0), ctx.plantilla["primaria"])
    g2 = {"r_REFCON_vs_plantilla_expresion": r, "brazos": len(ctx.arm["primaria"][2]), "PASA": bool(r > 0.3)}
    det_p = _hc.cachep("refcon", "determinismo.json")
    det = _hc.lee_json(det_p) if os.path.exists(det_p) else None
    res = {"G1_8p11": g1, "G2_brazos": g2, "determinismo": det, "ens3_sha256": shas, "NaN_REFCON_hueso": _NAN_REFCON.get("hueso"),
           "aplicable": bool(g1["PASA"] and g2["PASA"])}
    import json
    with open(_hc.cachep("refcon", "gate.json"), "w") as f:
        json.dump(_hc.r_(res), f, indent=1, sort_keys=True)
    print("gate REFCON:", {"G1": g1["PASA"], "G1_mediana_68": round(g1["mediana_68"], 4), "G1_p95_ref": round(g1["p95_referencia"], 4),
                           "G2": g2["PASA"], "G2_r": round(r, 4), "aplicable": res["aplicable"],
                           "determinismo": det and det.get("PASA")})
    return 0


def refcon(ctx):
    gp = _hc.cachep("refcon", "gate.json")
    if not os.path.exists(gp):
        return {"ejecutado": False, "motivo": "sin gate"}, {}
    gate = _hc.lee_json(gp)
    out = {"gate": gate}
    if not gate["aplicable"]:
        out.update({"ejecutado": False, "motivo": "REFCON no aplicable a este Flex (control positivo del hueso falla)"})
        return out, {}
    Lb, gib, _ = _refcon_log2("hueso", ctx)
    Lg, gi, shas = _refcon_log2("higado", ctx)
    out["ens3_sha256_higado"] = shas
    out["NaN_REFCON"] = dict(_NAN_REFCON)
    grp = ctx.grp["base"]
    plant_R = _refcon_brazos(Lb, gib, ctx.arm["primaria"], ctx.refB)[ctx.mal68].mean(axis=0)
    V_L = _refcon_brazos(Lg, gi, ctx.arm["primaria"], grp["ref"])
    llamadas = {}
    for nombre, genes in (("refcon_estandar", ctx.genes["estandar"]), ("refcon_estricto", ctx.genes["estricto"])):
        cols = np.where(genes[gi])[0]
        X = Lg[:, cols]
        sc = AF.corr_con(X, X[grp["pool"]].mean(axis=0))
        del X
        thr, call, _ = AF.llamada_v4(sc, grp)
        ev = evalua(ctx, ctx.L0, grp, sc, call, nombre,
                    {"primaria": (V_L, plant_R), "frente_a_plantilla_expresion": (V_L, ctx.plantilla["primaria"])})
        ev["umbral"] = thr
        ev["n_genes"] = int(len(cols))
        out[nombre] = ev
        llamadas[nombre] = call
    cp = _hc.cachep("refcon", "clasifica.json")
    if os.path.exists(cp):
        cl = _hc.lee_json(cp)
        out["clasificador_propio_SECUNDARIO"] = cl
        lp = _hc.cachep("refcon", "clasifica_labels.npy")
        if cl.get("ejecutado") and os.path.exists(lp):
            lab = np.load(lp) == 1
            out["clasificador_propio_SECUNDARIO"]["en_grupos"] = {
                k: _hc.w(int((lab & grp[k]).sum()), int(grp[k].sum())) for k in ("pool", "pp", "h2", "hep", "ref")}
            out["clasificador_propio_SECUNDARIO"]["fraccion_sobre_QC"] = _hc.w(int((lab & grp["no_ref"]).sum()), int(grp["keep"].sum()))
    else:
        out["clasificador_propio_SECUNDARIO"] = {"ejecutado": False, "motivo": "no corrido"}
    out["ejecutado"] = True
    return out, llamadas


# ---------------------------------------------------------------- POST HOC declarados (no deciden ningún veredicto)
def grupos_semilla(L0, qc, semilla, excluir=None):
    """Copia de _amb_final.grupos_v4 con la semilla del hold-out como parámetro (con la semilla del ambiente y sin
    exclusión tiene que dar exactamente lo mismo: se comprueba en posthoc). excluir: máscara de células fuera del QC."""
    keep = (qc["ngen"] >= 500) & (qc["scrublet"] <= 0.25) & (qc["decontx_cont"] <= 0.7)
    if excluir is not None:
        keep = keep & ~excluir
    pc, pp, sz = AF.zonas(L0)
    noh = np.isin(L0.tipo, AF.NULO + ["fibroblasto_estelar"])
    rng = np.random.default_rng(semilla)
    hold = np.zeros(L0.n, bool)
    for t in AF.NULO + ["fibroblasto_estelar"]:
        idx = np.where(noh & keep & (L0.tipo == t))[0]
        hold[rng.choice(idx, int(round(0.2 * len(idx))), replace=False)] = True
    hidx = np.where(hold)[0]
    h1 = np.zeros(L0.n, bool)
    h1[rng.choice(hidx, len(hidx) // 2, replace=False)] = True
    h2 = hold & ~h1
    ref = keep & (pc | noh) & ~hold
    pool = np.isin(L0.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2) & keep
    return {"keep": keep, "pc": pc & keep, "pp": pp & keep, "sin_zona": sz, "ref": ref, "h1": h1, "h2": h2, "pool": pool,
            "hep": (L0.tipo == "hepatocito") & keep, "no_ref": keep & ~ref & ~hold}


def _resumen_v4(L, grp, genes):
    with contextlib.redirect_stdout(io.StringIO()):
        r4, call, sc = AF.v4(L, grp, genes)
    m = r4["metricas"]
    return {"FP_H2": m["FP_H2"], "FP_periportales": m["FP_periportales"], "C2_pasaria": bool(m["FP_H2"]["frac"] <= 0.02 and m["FP_periportales"]["frac"] <= 0.01),
            "AUC_hep": r4["AUC_pool_vs_hepatocitos"], "AUC_pp": r4["AUC_pool_vs_periportales"], "umbral": r4["umbral_corr"],
            "llamadas_pool": m["en_pool"], "fraccion_QC": _hc.w(int((call & grp["no_ref"]).sum()), int(grp["keep"].sum()))}, call


SEMILLAS_POSTHOC = 20


def posthoc(ctx, llam):
    """Diagnósticos añadidos tras ver la etapa de depuración. Declarados en POST_HOC; no entran en ningún veredicto."""
    L0, D = ctx.L0, ctx.D["doblete"]
    out = {}
    g_sin = ctx.grp["sin_exclusion_dobletes"]
    assert all((grupos_semilla(L0, ctx.qc, _a.SEED)[k] == g_sin[k]).all() for k in g_sin), "grupos_semilla != grupos_v4"
    # 1) partición FIJA: los grupos de la v4 sin exclusión, quitando los dobletes de TODAS las máscaras. Separa «los dobletes
    #    rompen la especificidad» de «otro hold-out de ~140 células, donde el p98,5 lo fijan ~2 células».
    gf = {k: (v & ~D) if k != "sin_zona" else v for k, v in g_sin.items()}
    out["particion_fija_sin_dobletes"] = {c: _resumen_v4(L0, gf, ctx.genes[c])[0] for c in ("estandar", "estricto")}
    POST_HOC.append("POSTHOC_PARTICION_FIJA: v4 con el hold-out de la versión sin exclusión y los dobletes quitados de todas las máscaras (informativo)")
    # 2) barrido de semillas del hold-out con dobletes excluidos: solo distribución, no veredicto
    filas = []
    for s in range(1, SEMILLAS_POSTHOC + 1):
        g = grupos_semilla(L0, ctx.qc, _a.SEED + s, excluir=D)
        for c in ("estandar", "estricto"):
            r, _ = _resumen_v4(L0, g, ctx.genes[c])
            filas.append({"criterio": c, "semilla": s, "FP_H2_k": r["FP_H2"]["k"], "FP_H2_n": r["FP_H2"]["n"], "FP_pp_k": r["FP_periportales"]["k"],
                          "FP_pp_n": r["FP_periportales"]["n"], "C2": r["C2_pasaria"], "llamadas_pool": r["llamadas_pool"]["k"],
                          "fraccion_QC": r["fraccion_QC"]["frac"], "umbral": r["umbral"]})
    df = pd.DataFrame(filas)
    df.map(_hc.r_).to_csv(_hc.outp("posthoc_semillas_holdout.csv"), index=False)
    out["semillas_holdout"] = {c: {"n_semillas": int((df.criterio == c).sum()), "C2_pasa_en": int(df[df.criterio == c]["C2"].sum()),
                                   "FP_H2_k_min_mediana_max": [int(df[df.criterio == c]["FP_H2_k"].min()), float(df[df.criterio == c]["FP_H2_k"].median()), int(df[df.criterio == c]["FP_H2_k"].max())],
                                   "FP_pp_k_min_mediana_max": [int(df[df.criterio == c]["FP_pp_k"].min()), float(df[df.criterio == c]["FP_pp_k"].median()), int(df[df.criterio == c]["FP_pp_k"].max())],
                                   "fraccion_QC_min_mediana_max": [float(df[df.criterio == c]["fraccion_QC"].min()), float(df[df.criterio == c]["fraccion_QC"].median()), float(df[df.criterio == c]["fraccion_QC"].max())]}
                               for c in ("estandar", "estricto")}
    POST_HOC.append(f"POSTHOC_SEMILLAS_HOLDOUT: {SEMILLAS_POSTHOC} semillas del hold-out con dobletes excluidos; solo distribución (informativo)")
    # 3) profundidad: ¿la correlación con el consenso selecciona por UMI? y ¿los dobletes del residuo tienen más UMI?
    umi = L0.umi
    prof = {}
    for c in ("estandar", "estricto"):
        g = ctx.grp["base"]
        call = llam[(c, "base")]
        a, b = call & g["pool"], g["pool"] & ~call
        prof[c] = {"UMI_mediana_llamadas_pool": float(np.median(umi[a])) if a.any() else None,
                   "UMI_mediana_no_llamadas_pool": float(np.median(umi[b])) if b.any() else None}
    out["profundidad_llamadas"] = prof
    CB = AF.obj("higado", "cellbender_fpr0.01")
    tplus = (np.asarray(CB.X[:, np.isin(L0.sym, TESTIGOS)].sum(axis=1)).ravel() > 0) & ctx.nulo
    out["profundidad_residuo"] = {"UMI_mediana_nulo_testigo_mas_doblete": float(np.median(umi[tplus & D])),
                                  "UMI_mediana_nulo_testigo_mas_singlete": float(np.median(umi[tplus & ~D])),
                                  "UMI_mediana_nulo_testigo_menos_singlete": float(np.median(umi[ctx.nulo & ~tplus & ~D]))}
    POST_HOC.append("POSTHOC_PROFUNDIDAD: UMI de llamadas frente a no llamadas del pool y de los dobletes del residuo (informativo)")
    return out


# ---------------------------------------------------------------- veredictos
def veredicto(ev, c4_pasa, alt_falla):
    if not (ev["C1"]["PASA"] and ev["C2"]["PASA"]):
        return "NO RESUELTO", [c for c in ("C1", "C2") if not ev[c]["PASA"]]
    fallan = [c for c in ("C3", "C5") if not ev[c]["PASA"]]
    if not c4_pasa:
        fallan.append("C4")
    if alt_falla:
        fallan.append("C1/C2 en matriz alternativa válida")
    return ("RESUELTO" if not fallan else "FRÁGIL"), sorted(fallan)


def _commit():
    try:
        return subprocess.check_output(["git", "-C", _hc.AQUI, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "desconocido"


def run(*args):
    t0 = time.time()
    POST_HOC.clear()
    ctx = Ctx()
    L0, B0 = ctx.L0, ctx.B0
    res = {"reglas_sha256": _hc.sha256(_hc.REGLAS), "enmienda_sha256": _hc.sha256(_hc.ENMIENDA),
           "enmienda2_sha256": _hc.sha256(_hc.ENMIENDA2), "commit": _commit(),
           "semilla": _hc.SEED, "codigo_reutilizado_sha256": {
               "_amb_final.py": _hc.sha256(os.path.join(_a.AQUI, "_amb_final.py")),
               "REGLAS_HIGADO_v4.json": _hc.sha256(os.path.join(_a.AQUI, "REGLAS_HIGADO_v4.json")),
               "_final.py": _hc.sha256(os.path.join(_c.AQUI, "_final.py")), "_c.py": _hc.sha256(os.path.join(_c.AQUI, "_c.py"))}}
    for k, v in R["codigo_reutilizado"].items():
        base = os.path.basename(k)
        if base in res["codigo_reutilizado_sha256"] and not v.startswith(res["codigo_reutilizado_sha256"][base]):
            POST_HOC.append(f"CODIGO_REUTILIZADO_CAMBIADO: {base} no tiene el sha de las reglas")
    res["dobletes"] = {"dbr006_sha256": ctx.D["sha256"], "dbrNULL_sha256": ctx.Dn["sha256"],
                       "n_dbr006": int(ctx.D["doblete"].sum()), "n_dbrNULL": int(ctx.Dn["doblete"].sum())}
    res["grupos"] = {v: {k: int(m.sum()) for k, m in g.items()} for v, g in ctx.grp.items()}
    res["genes"] = {k: int(v.sum()) for k, v in ctx.genes.items()}
    res["brazos"] = {k: {"n_brazos": len(a[2]), "n_genes": int(a[0].sum())} for k, a in ctx.arm.items()}
    print("grupos base:", res["grupos"]["base"])
    print("dobletes dbr 0,06:", res["dobletes"]["n_dbr006"], "| dbr por defecto:", res["dobletes"]["n_dbrNULL"], "de", L0.n)
    res["C6_8p11"] = potencia_8p11(L0)
    print("C6 8p11:", res["C6_8p11"])
    res["residuo"] = residuo(ctx)
    print("residuo:", res["residuo"]["LECTURA"], "| E =", _hc.r_(res["residuo"]["cellbender_fpr0.01|dbr006"]["nulo_testigos"]["E_dobletes_entre_marca_mas"]))
    res["scCDC"], Lc = sccdc(ctx)
    print("scCDC: válida", res["scCDC"]["valida"], "| GCG hígado", res["scCDC"]["higado"]["n_GCG"], "hueso", res["scCDC"]["hueso"]["n_GCG"])
    vias, llam = {}, {}
    for crit in ("estandar", "estricto"):
        g = ctx.genes[crit]
        vias[crit] = {}
        for var in ("base", "sin_exclusion_dobletes"):
            ev, call = criterio(ctx, L0, ctx.grp[var], g, f"{crit}|{var}")
            vias[crit][var] = ev
            llam[(crit, var)] = call
        if res["scCDC"]["valida"]:
            ev, call = criterio(ctx, Lc, ctx.grp["base"], g, f"{crit}|sccdc")
            vias[crit]["sccdc"] = ev
            llam[(crit, "sccdc")] = call
        vias[crit]["control_dobletes"] = composicion_dobletes(ctx, ctx.grp["sin_exclusion_dobletes"], llam[(crit, "sin_exclusion_dobletes")])
        for var, ev in vias[crit].items():
            if var != "control_dobletes":
                print(f"== {crit}|{var}: AUC hep {ev['C1']['AUC_pool_vs_hepatocitos']:.3f} pp {ev['C1']['AUC_pool_vs_periportales']:.3f}"
                      f" | FP pp {ev['C2']['FP_periportales']['k']}/{ev['C2']['FP_periportales']['n']} H2 {ev['C2']['FP_H2']['k']}/{ev['C2']['FP_H2']['n']}"
                      f" | r {ev['C3'].get('primaria', {}).get('r', float('nan')):.3f} | fracción QC {ev['C5']['fraccion_sobre_QC']['frac']:.3f}"
                      f" | pool {ev['metricas']['en_pool']['k']}/{ev['metricas']['en_pool']['n']}")
    sx = vias["estandar"]["sin_exclusion_dobletes"]
    rep = {"pool": (sx["metricas"]["en_pool"]["k"], sx["metricas"]["en_pool"]["n"]),
           "FP_pp": (sx["C2"]["FP_periportales"]["k"], sx["C2"]["FP_periportales"]["n"]), "FP_H2": (sx["C2"]["FP_H2"]["k"], sx["C2"]["FP_H2"]["n"])}
    res["reproduce_v4_ambiente"] = {"obtenido": rep, "esperado": {"pool": (6245, 13320), "FP_pp": (0, 490), "FP_H2": (1, 159)}}
    res["reproduce_v4_ambiente"]["IDENTICO"] = bool(rep == {"pool": (6245, 13320), "FP_pp": (0, 490), "FP_H2": (1, 159)})
    if not res["reproduce_v4_ambiente"]["IDENTICO"]:
        POST_HOC.append(f"REPRODUCCION_V4: la variante sin exclusión no reproduce ambiente.json: {rep}")
    print("reproduce v4 de ambiente:", res["reproduce_v4_ambiente"]["IDENTICO"], rep)
    res["REFCON"], llam_r = refcon(ctx)
    for k, call in llam_r.items():
        vias[k] = {"base": res["REFCON"][k]}
        llam[(k, "base")] = call
        ev = res["REFCON"][k]
        print(f"== {k}: AUC hep {ev['C1']['AUC_pool_vs_hepatocitos']:.3f} pp {ev['C1']['AUC_pool_vs_periportales']:.3f}"
              f" | FP pp {ev['C2']['FP_periportales']['k']}/{ev['C2']['FP_periportales']['n']} H2 {ev['C2']['FP_H2']['k']}/{ev['C2']['FP_H2']['n']}"
              f" | r {ev['C3'].get('primaria', {}).get('r', float('nan')):.3f} | fracción QC {ev['C5']['fraccion_sobre_QC']['frac']:.3f}")
    if not res["REFCON"].get("ejecutado"):
        print("REFCON:", res["REFCON"].get("motivo"))
    # C4: un número para todas las vías que pasan C1 y C2 en su base (ENMIENDA_1, C4_CONJUNTO)
    pasan = [v for v in vias if vias[v]["base"]["C1"]["PASA"] and vias[v]["base"]["C2"]["PASA"]]
    conj = [(v, var) for v in pasan for var in vias[v] if var != "control_dobletes"]
    c4 = {"conjuntos": [f"{a}|{b}" for a, b in conj]}
    for gname in DIANAS6:
        vals = [vias[a][b]["deteccion_dianas_pool"][gname] for a, b in conj]
        vals = [x for x in vals if x is not None]
        c4[gname] = {"min": min(vals) if vals else None, "max": max(vals) if vals else None,
                     "rango_puntos": (100 * (max(vals) - min(vals))) if vals else None, "decide": gname in DIANAS3}
    c4["PASA"] = bool(conj) and all(c4[g]["rango_puntos"] is not None and c4[g]["rango_puntos"] < 10 for g in DIANAS3)
    if not conj:
        c4["estado"] = "NO APLICABLE: ninguna vía pasa C1 y C2"
    elif len(conj) == 1:
        c4["estado"] = "DEGENERADO: un solo conjunto de llamadas (rango 0 por construcción); no mide estabilidad"
    else:
        c4["estado"] = "calculado"
    res["C4_estabilidad_dianas"] = c4
    # informativo (POST HOC): el mismo rango sobre TODOS los conjuntos de llamadas, pasen o no C1 y C2
    todos = [(a, b) for a in vias for b in vias[a] if b != "control_dobletes"]
    res["C4_informativo_todos_los_conjuntos_POSTHOC"] = {
        "conjuntos": [f"{a}|{b}" for a, b in todos],
        **{gname: 100 * (max(vias[a][b]["deteccion_dianas_pool"][gname] for a, b in todos) - min(vias[a][b]["deteccion_dianas_pool"][gname] for a, b in todos))
           for gname in DIANAS6}}
    POST_HOC.append("POSTHOC_C4_TODOS: rango de dianas sobre todos los conjuntos de llamadas, válidos o no (informativo)")
    print("C4:", c4["estado"], {g: c4[g]["rango_puntos"] for g in DIANAS6}, "| PASA", c4["PASA"], "| sobre", c4["conjuntos"])
    print("C4 informativo (todos):", {g: round(res["C4_informativo_todos_los_conjuntos_POSTHOC"][g], 1) for g in DIANAS6})
    res["POSTHOC_ANALISIS"] = posthoc(ctx, llam)
    ph = res["POSTHOC_ANALISIS"]
    print("POSTHOC partición fija:", {c: (v["FP_H2"]["k"], v["FP_H2"]["n"], v["FP_periportales"]["k"], v["FP_periportales"]["n"], v["C2_pasaria"])
                                      for c, v in ph["particion_fija_sin_dobletes"].items()})
    print("POSTHOC semillas:", ph["semillas_holdout"])
    print("POSTHOC profundidad:", ph["profundidad_llamadas"], ph["profundidad_residuo"])
    ver = {}
    for v in vias:
        alt = "sccdc" in vias[v] and not (vias[v]["sccdc"]["C1"]["PASA"] and vias[v]["sccdc"]["C2"]["PASA"])
        ver[v], fallan = veredicto(vias[v]["base"], c4["PASA"], alt)
        vias[v]["VEREDICTO"] = {"veredicto": ver[v], "fallan": fallan,
                                "decide_D1": v in ("estandar", "estricto")}
    res["vias"] = vias
    res["CONCLUSION_D1"] = f"Con el criterio estándar: {ver['estandar']}. Con el estricto: {ver['estricto']}."
    print("VEREDICTOS:", ver)
    print(res["CONCLUSION_D1"])
    # dianas finales en las llamadas fuera de referencia de cada vía (base)
    tabs = []
    for v in vias:
        g = ctx.grp["base"]
        cj = {"malignas_v4": llam[(v, "base")] & g["no_ref"], "pool_primario": g["pool"]}
        df = AF.dianas(L0, B0, cj, v, "base")
        tabs.append(df[df["gen"].isin(DIANAS6)])
    dfa = pd.concat(tabs).round(6)
    dfa = dfa[(dfa["muestra"] == "higado") & (dfa["conjunto"] == "malignas_v4")]
    cols = [c for c in ("metodo", "gen", "n", "k", "frac", "wilson_lo", "wilson_hi", "suelo", "por_encima_suelo", "ref_emparejada", "supera_ref") if c in dfa]
    dfa = dfa[cols].sort_values(["gen", "metodo"]).reset_index(drop=True)
    dfa["sostenida"] = dfa["por_encima_suelo"].astype(str).eq("True") & dfa["supera_ref"].astype(str).eq("True")
    dfa.to_csv(_hc.outp("dianas_higado_celula.csv"), index=False)
    if {"estandar", "estricto"} <= set(dfa["metodo"]):
        p = dfa.pivot(index="gen", columns="metodo", values="frac")
        res["dianas_diferencia_estandar_menos_estricto_puntos"] = {g: float(100 * (p.loc[g, "estandar"] - p.loc[g, "estricto"])) for g in p.index}
    pd.set_option("display.width", 250)
    print(dfa.round(3).to_string(index=False))
    # tabla de métricas
    filas = []
    for v in vias:
        for var, ev in vias[v].items():
            if var in ("control_dobletes", "VEREDICTO"):
                continue
            c3p = ev["C3"].get("primaria", {})
            filas.append({"via": v, "variante": var, "llamadas_pool": ev["metricas"]["en_pool"]["k"], "n_pool": ev["metricas"]["en_pool"]["n"],
                          "AUC_hep": ev["C1"]["AUC_pool_vs_hepatocitos"], "AUC_hep_ic": ev["C1"]["ic95_hep"],
                          "AUC_pp": ev["C1"]["AUC_pool_vs_periportales"], "AUC_pp_ic": ev["C1"]["ic95_pp"],
                          "FP_pp": f"{ev['C2']['FP_periportales']['k']}/{ev['C2']['FP_periportales']['n']}",
                          "FP_pp_wilson_sup": ev["C2"]["FP_periportales"]["ic95_wilson"][1],
                          "FP_H2": f"{ev['C2']['FP_H2']['k']}/{ev['C2']['FP_H2']['n']}", "FP_H2_wilson_sup": ev["C2"]["FP_H2"]["ic95_wilson"][1],
                          "r_brazos": c3p.get("r"), "r_ic": c3p.get("ic95_bootstrap"),
                          "fraccion_QC": ev["C5"]["fraccion_sobre_QC"]["frac"], "fraccion_QC_ic": ev["C5"]["fraccion_sobre_QC"]["ic95_wilson"],
                          "C1": ev["C1"]["PASA"], "C2": ev["C2"]["PASA"], "C3": ev["C3"]["PASA"], "C5": ev["C5"]["PASA"],
                          **{g + "_det": ev["deteccion_dianas_pool"][g] for g in DIANAS6}})
    pd.DataFrame(filas).map(_hc.r_).to_csv(_hc.outp("metricas_higado_celula.csv"), index=False)
    res["POST_HOC"] = sorted(set(POST_HOC + AF.POST_HOC))
    res["segundos_total"] = None  # el tiempo no entra en la salida (rompería el sha entre ejecuciones)
    p = _hc.guarda_json(res, "higado_celula.json")
    print("POST_HOC:", res["POST_HOC"])
    print("guardado", os.path.basename(p), "en", round(time.time() - t0), "s")
    return 0


# ---------------------------------------------------------------- manifiesto
def _arbol(d, excluir=("cache",)):
    """sha256 de las salidas. Fuera: cache/ y toda carpeta de primer nivel que empiece por «_» (_rep2, _depura, _test_rep*)."""
    sal = {}
    for raiz, dirs, fs in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not (raiz == d and (x in excluir or x.startswith("_"))))
        for f in sorted(fs):
            if not f.startswith("MANIFIESTO"):
                rel = os.path.relpath(os.path.join(raiz, f), d)
                sal[rel] = _hc.sha256(os.path.join(raiz, f))
    return dict(sorted(sal.items()))


def manifiesto(*args):
    d = _hc.BASE
    sal = _arbol(d)
    rep2 = os.path.join(d, "_rep2")
    dos = None
    if os.path.isdir(rep2):
        s2 = _arbol(rep2, excluir=())
        dif = sorted(k for k in set(sal) | set(s2) if sal.get(k) != s2.get(k))
        dos = {"n_salidas": len(sal), "identicas": not dif, "difieren": dif}
    cache = {}
    for raiz, _, fs in os.walk(_hc.CACHE):
        for f in sorted(fs):
            if f.endswith((".csv", ".json", ".npz", ".npy", ".txt")) and "entrada" not in f:
                p = os.path.join(raiz, f)
                cache[os.path.relpath(p, _hc.CACHE)] = _hc.sha256(p)
    entradas = {f: _hc.sha256(os.path.join(_hc.CACHE_CIERRE, f)) for f in ("X_higado.npz", "X_hueso.npz", "obs_higado.npz", "obs_hueso.npz")}
    for f in ("qc_v4_higado.npz", "X_higado_cellbender_fpr0.01.npz", "decontx_contaminacion_higado.npy"):
        entradas["ambiente/" + f] = _hc.sha256(os.path.join(_a.CACHE, f))
    scr = {f: _hc.sha256(os.path.join(_hc.AQUI, f)) for f in sorted(os.listdir(_hc.AQUI)) if f.endswith((".py", ".json", ".R", ".txt", ".csv"))}
    # las salidas selladas de las fases anteriores siguen intactas (el código reutilizado escribe; se redirigió a aux/)
    selladas = {}
    for fase, man in (("ambiente", os.path.join(_hc.FLEX, "ambiente", "MANIFIESTO_AMBIENTE.json")),
                      ("cierre", os.path.join(_hc.FLEX, "cierre", "MANIFIESTO_CIERRE.json"))):
        ms = _hc.lee_json(man)["salidas"]
        dif = sorted(f for f, h in ms.items() if not os.path.exists(os.path.join(os.path.dirname(man), f))
                     or _hc.sha256(os.path.join(os.path.dirname(man), f)) != h)
        selladas[fase] = {"n": len(ms), "difieren": dif, "intactas": not dif}
    m = {"salidas": sal, "dos_ejecuciones": dos, "salidas_selladas_previas": selladas, "cache": dict(sorted(cache.items())),
         "entradas": entradas, "scripts_y_locks": scr,
         "reglas_sha256": _hc.sha256(_hc.REGLAS), "enmienda_sha256": _hc.sha256(_hc.ENMIENDA), "enmienda2_sha256": _hc.sha256(_hc.ENMIENDA2),
         "commit_al_generar": _commit(), "semilla": _hc.SEED}
    import json
    with open(os.path.join(d, "MANIFIESTO_HIGADO_CELULA.json"), "w") as f:
        json.dump(m, f, indent=1, ensure_ascii=False, sort_keys=True)
    print("MANIFIESTO:", len(sal), "salidas | dos ejecuciones:", dos and {k: v for k, v in dos.items() if k != "difieren"},
          "| difieren:", dos and dos["difieren"][:5], "| selladas previas:", {k: (v["intactas"], v["difieren"][:3]) for k, v in selladas.items()})
    return 0 if ((dos is None or dos["identicas"]) and all(v["intactas"] for v in selladas.values())) else 1
