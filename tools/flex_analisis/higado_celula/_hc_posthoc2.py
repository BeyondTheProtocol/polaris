"""Post hoc 2 declarados (POSTHOC_2_PLAN.json) de la fase 2 del hígado célula a célula. NO deciden ningún veredicto.

P1 sensibilidad de C3 (spike-in de las 68 del hueso y mitades) · P2 composición y reparto estratificado de H1/H2 ·
P3 scDblFinder en configuración Flex y sin clusters · P4 clasificador propio de REFCON en submuestras · P5 qué son las
llamadas con >= 2 HEP5. Reutiliza el código canónico (_hc_final, _amb_final, _final) sin copiarlo, salvo
grupos_estratificado (copia de _hc_final.grupos_semilla con el reparto H1/H2 por tipo; se comprueba que el hold-out es
idéntico). Salidas en posthoc2/ (o $HC_POSTHOC2_OUT); cachés en cache/posthoc2/. stdout: solo agregados.
"""
import collections
import contextlib
import errno
import io
import json
import os
import socket
import subprocess
import sys
import types

import numpy as np
import pandas as pd
import scipy.sparse as sp

import _hc
import _a
from _a import TC
import _final as F
import _amb_final as AF
import _hc_dobletes as HD
import _hc_final as HF

PLAN = os.path.join(_hc.AQUI, "POSTHOC_2_PLAN.json")
PLAN_SHA = "9aa82ab214b0fb0bfdd962a4c25c7c178d819f6225945b8c10c14ecc53c841f8"  # commit 155aafd6, antes del código
SCRIPTS = ["posthoc2.py", "_hc_posthoc2.py", "_hc_p4_refcon.py", "dobletes_posthoc2.R", "POSTHOC_2_PLAN.json",
           "_hc.py", "_hc_final.py", "_hc_dobletes.py", "refcon_run.py", "higado_celula.py"]
CONFIGS = ("dbrsd1", "sinclusters", "sinclusters_dbrsd1")
TIPOS_H = AF.NULO + ["fibroblasto_estelar"]
TESTIGOS = HF.TESTIGOS
NSPLIT = 1000
SEMILLAS = 20
REFCON_PY = os.path.join(_hc.ENVS, "refcon", "bin", "python")
R_SCRIPT = os.path.join(_hc.AQUI, "dobletes_posthoc2.R")


def cache2(*n):
    return _hc.cachep("posthoc2", *n)


def quieto():
    s = contextlib.ExitStack()
    s.enter_context(contextlib.redirect_stdout(io.StringIO()))
    s.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return s


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def jacc(a, b):
    u = int((a | b).sum())
    return float((a & b).sum() / u) if u else None


def serie(x, corte=0.3):
    x = np.asarray(x, float)
    return {"n": int(len(x)), "mediana": float(np.median(x)), "p2_5": float(np.percentile(x, 2.5)),
            "p97_5": float(np.percentile(x, 97.5)), "frac_mayor_0_3": float((x > corte).mean())}


def mediana_iqr(x):
    x = np.asarray(x, float)
    if len(x) == 0:
        return None
    return {"mediana": float(np.median(x)), "q25": float(np.percentile(x, 25)), "q75": float(np.percentile(x, 75))}


def kappa(a, b):
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    if len(a) == 0:
        return {"n": 0, "acuerdo": None, "kappa": None}
    po = float((a == b).mean())
    pa, pb = a.mean(), b.mean()
    pe = float(pa * pb + (1 - pa) * (1 - pb))
    return {"n": int(len(a)), "acuerdo": po, "kappa": (po - pe) / (1 - pe) if pe < 1 else None,
            "ambos": int((a & b).sum()), "solo_clasificador": int((a & ~b).sum()), "solo_llamada": int((~a & b).sum())}


def comp(L0, m):
    return {t: int((m & (L0.tipo == t)).sum()) for t in TIPOS_H}


def tvd(c1, c2):
    n1, n2 = sum(c1.values()), sum(c2.values())
    return 0.5 * sum(abs(c1[t] / n1 - c2[t] / n2) for t in TIPOS_H) if n1 and n2 else None


def sha_salida_canonica(nombre):
    return _hc.sha256(os.path.join(_hc.BASE, nombre))


# ---------------------------------------------------------------- muro: prueba positiva del sandbox
def prueba_sandbox():
    """connect a 127.0.0.1:9 (descarte, sin datos). Dentro de sandbox-exec (deny network*) tiene que dar EPERM."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", 9))
        s.close()
        r = "conectó"
    except OSError as e:
        r = errno.errorcode.get(e.errno, str(e.errno))
    return {"HC_SANDBOX": os.environ.get("HC_SANDBOX"), "connect_loopback": r, "red_denegada": r == "EPERM"}


# ---------------------------------------------------------------- lectores de cachés propias
def scdbl2(nombre):
    p = cache2(f"scdbl_{nombre}.csv")
    d = pd.read_csv(p, keep_default_na=False)
    assert (d["idx"].values == np.arange(len(d))).all()
    return {"doblete": d["clase"].values == "doublet", "score": d["score"].values.astype(float),
            "origen": d["origen"].astype(str).values, "ambiguo": d["ambiguo"].astype(str).values == "TRUE", "sha256": _hc.sha256(p)}


def dobletes_todos(ctx):
    D = {"dbr006": ctx.D, "dbrNULL": ctx.Dn}
    for c in CONFIGS:
        D[c] = scdbl2(c)
        assert len(D[c]["doblete"]) == ctx.L0.n
    return D


# ---------------------------------------------------------------- P3 preparación: R (una vez, cacheado)
def p3_dobletes():
    din = os.path.join(_hc.CACHE, "dobletes")
    L0 = F.T("higado")
    X = L0.X.tocsr().astype(np.int32)
    X.sort_indices()
    # los binarios del paso canónico tienen que ser exactamente la matriz cruda de la caché del cierre
    ok = (np.array_equal(np.fromfile(os.path.join(din, "higado_p.bin"), dtype="<i4"), X.indptr)
          and np.array_equal(np.fromfile(os.path.join(din, "higado_i.bin"), dtype="<i4"), X.indices)
          and np.array_equal(np.fromfile(os.path.join(din, "higado_x.bin"), dtype="<i4"), X.data)
          and open(os.path.join(din, "higado_tipo.txt")).read().split("\n")[:-1] == list(L0.tipo.astype(str)))
    print("binarios canónicos = matriz cruda del cierre:", ok)
    if not ok:
        return 1
    d2 = os.path.dirname(cache2("x"))
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    env["PATH"] = os.path.join(_hc.ENVS, "dobletes", "bin") + os.pathsep + env.get("PATH", "")
    r = subprocess.run([_hc.RSCRIPT, "--vanilla", R_SCRIPT, din, d2], env=env, capture_output=True, text=True)
    with open(cache2("log_p3.txt"), "w") as f:
        f.write(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(f"FALLO dobletes_posthoc2.R (rc {r.returncode}); ver log en caché")
        return 1
    return 0


# ---------------------------------------------------------------- P4 preparación: refcon (una vez, cacheado)
def p4_refcon():
    n = F.T("higado").n
    perm = np.random.default_rng(_hc.SEED + 104).permutation(n)
    np.save(cache2("p4_permutacion.npy"), perm)
    r = subprocess.run([REFCON_PY, "-I", os.path.join(_hc.AQUI, "_hc_p4_refcon.py"), os.path.dirname(cache2("x"))],
                       env=dict(os.environ), capture_output=True, text=True)
    with open(cache2("log_p4.txt"), "w") as f:
        f.write(r.stdout + "\n--- stderr ---\n" + r.stderr)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(f"FALLO _hc_p4_refcon.py (rc {r.returncode}); ver log en caché")
        return 1
    return 0


# ---------------------------------------------------------------- P1
def p1(ctx, canon):
    L0, B0 = ctx.L0, ctx.B0
    assert (L0.o["gene_ids"].astype(str) == B0.o["gene_ids"].astype(str)).all(), "genes del hígado y del hueso distintos"
    gb = ctx.grp["base"]
    i68 = np.where(ctx.mal68)[0]
    assert len(i68) == 68
    Tc = types.SimpleNamespace(X=sp.vstack([L0.X, B0.X[i68]]).tocsr(), umi=np.concatenate([L0.umi, B0.umi[i68]]),
                               o=L0.o, n=L0.n + 68)
    gpad = {k: np.concatenate([v, np.zeros(68, bool)]) for k, v in gb.items()}
    spk = np.r_[np.zeros(L0.n, bool), np.ones(68, bool)]
    out, llam = {"n_spike": 68}, {}
    for crit in ("estandar", "estricto"):
        genes = ctx.genes[crit]
        with quieto():
            C, _ = AF.infercnv_v4(L0, genes, gb)
        cons = C[gb["pool"]].mean(axis=0)
        sc = AF.corr_con(C, cons)
        thr, call, m = AF.llamada_v4(sc, gb)
        del C
        ev = canon["vias"][crit]["base"]
        obt = {"pool": [m["en_pool"]["k"], m["en_pool"]["n"]], "FP_H2": [m["FP_H2"]["k"], m["FP_H2"]["n"]],
               "FP_pp": [m["FP_periportales"]["k"], m["FP_periportales"]["n"]]}
        esp = {"pool": [ev["metricas"]["en_pool"]["k"], ev["metricas"]["en_pool"]["n"]],
               "FP_H2": [ev["C2"]["FP_H2"]["k"], ev["C2"]["FP_H2"]["n"]],
               "FP_pp": [ev["C2"]["FP_periportales"]["k"], ev["C2"]["FP_periportales"]["n"]]}
        with quieto():
            Cx, _ = AF.infercnv_v4(Tc, genes, gpad)
        scx = AF.corr_con(Cx, cons)
        del Cx
        s68 = scx[spk]
        cx = scx[:L0.n] > thr
        pool = gb["pool"]
        out[crit] = {
            "reproduce_base_canonica": {"obtenido": obt, "esperado": esp, "IDENTICO": obt == esp,
                                        "umbral": thr, "umbral_canonico": ev["umbral"]},
            "spikes_llamados": _hc.w(int((s68 > thr).sum()), 68),
            "score_mediana": {"spikes_68": float(np.median(s68)), "pool": float(np.median(sc[pool])),
                              "pool_llamadas": float(np.median(sc[call & pool])), "H1": float(np.median(sc[gb["h1"]])),
                              "umbral_p98_5_H1": thr},
            "fraccion_pool_llamada": _hc.w(int((call & pool).sum()), int(pool.sum())),
            "perturbacion_del_ultimo_trozo": {"max_abs_dscore_filas_higado": float(np.abs(scx[:L0.n] - sc).max()),
                                              "jaccard_llamadas_pool": jacc(call & pool, cx & pool),
                                              "llamadas_pool_que_cambian": int(((call ^ cx) & pool).sum())}}
        llam[crit] = (call, sc, thr)
    # r por brazo (C3 es expresión por brazo, no inferCNV)
    VB, Vh = {}, {}
    circ = {}
    for k in ("primaria", "con_linaje", "con_amplicones"):
        arm = ctx.arm[k]
        VB[k] = F.centra(F.valores_brazo(B0, *arm, ctx.refB))[ctx.mal68]
        assert np.allclose(VB[k].mean(axis=0), ctx.plantilla[k]), "plantilla recalculada != canónica"
        Vc = F.centra(F.valores_brazo(Tc, *arm, gpad["ref"]))
        Vh[k] = Vc[spk]
        if k == "primaria":
            VL = Vc[:L0.n]
            VL_dir = F.centra(F.valores_brazo(L0, *arm, gb["ref"]))
            assert np.allclose(VL, VL_dir), "filas del hígado de la matriz ampliada != canónicas"
            del VL_dir
        circ[k] = corr(Vh[k].mean(axis=0), ctx.plantilla[k])
    call_std = llam["estandar"][0]
    pc = np.where(call_std & gb["pool"])[0]
    pn = np.where(~call_std & gb["pool"])[0]
    r_can = corr(VL[pc].mean(axis=0), ctx.plantilla["primaria"])
    rng, rngp = np.random.default_rng(_hc.SEED + 101), np.random.default_rng(_hc.SEED + 102)
    ser = {k: [] for k in ("r_hep_A", "r_hueso_A", "r_llam34", "r_nollam34", "r_llam_todas")}
    vb, vh = VB["primaria"], Vh["primaria"]
    media_pc = VL[pc].mean(axis=0)
    for _ in range(NSPLIT):
        p = rng.permutation(68)
        A, B = p[:34], p[34:]
        tB = vb[B].mean(axis=0)
        ser["r_hep_A"].append(corr(vh[A].mean(axis=0), tB))
        ser["r_hueso_A"].append(corr(vb[A].mean(axis=0), tB))
        ser["r_llam34"].append(corr(VL[rngp.choice(pc, 34, replace=False)].mean(axis=0), tB))
        ser["r_nollam34"].append(corr(VL[rngp.choice(pn, 34, replace=False)].mean(axis=0), tB))
        ser["r_llam_todas"].append(corr(media_pc, tB))
    res = {k: serie(v) for k, v in ser.items()}
    h = res["r_hep_A"]
    if h["mediana"] >= 0.3:
        l1 = "ALCANZABLE: un clon verdadero de 34 células con la referencia hepática supera 0,3 en la mediana"
    elif h["p97_5"] < 0.3:
        l1 = "NO ALCANZABLE: ni el percentil 97,5 de un clon verdadero de 34 células llega a 0,3"
    else:
        l1 = "A VECES: un clon verdadero de 34 células supera 0,3 en una fracción de las particiones"

    def frente_al_clon(x):
        if x < h["p2_5"]:
            return "MENOS QUE UN CLON VERDADERO a n emparejado"
        if x > h["p97_5"]:
            return "MÁS QUE UN CLON VERDADERO a n emparejado"
        return "COMO UN CLON VERDADERO a n emparejado (dentro del intervalo 2,5-97,5)"
    out["r_por_brazo"] = {
        "circular_68_ref_hepatica_vs_plantilla_canonica_COTA_SUPERIOR": circ,
        "r_canonica_llamadas_pool_estandar_recalculada": r_can, "r_canonica_en_higado_celula_json": canon["vias"]["estandar"]["base"]["C3"]["primaria"]["r"],
        "mitades_primaria": res, "n_brazos": len(ctx.arm["primaria"][2]),
        "LECTURA_NO_DECIDE": {"alcanzable_0_3": l1,
                              "coste_referencia_hepatica": res["r_hueso_A"]["mediana"] - h["mediana"],
                              "llamadas_34": frente_al_clon(res["r_llam34"]["mediana"]),
                              "no_llamadas_34": frente_al_clon(res["r_nollam34"]["mediana"])}}
    return out, llam


# ---------------------------------------------------------------- P2
def grupos_estratificado(L0, qc, semilla, excluir=None):
    """Copia de _hc_final.grupos_semilla con el reparto H1/H2 estratificado por tipo: mismas extracciones del rng para el
    hold-out (misma referencia); después, por tipo y en el orden NULO + fibroblasto_estelar, floor(n_t/2) a H1 con el
    mismo rng. La célula sobrante de un estrato impar va a H2."""
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
    h1 = np.zeros(L0.n, bool)
    for t in AF.NULO + ["fibroblasto_estelar"]:
        it = np.where(hold & (L0.tipo == t))[0]
        if len(it) // 2:
            h1[rng.choice(it, len(it) // 2, replace=False)] = True
    h2 = hold & ~h1
    ref = keep & (pc | noh) & ~hold
    pool = np.isin(L0.tipo, ["epitelial", "no_asignado"]) & ~(L0.ndet(TC.HEP5) >= 2) & keep
    return {"keep": keep, "pc": pc & keep, "pp": pp & keep, "sin_zona": sz, "ref": ref, "h1": h1, "h2": h2, "pool": pool,
            "hep": (L0.tipo == "hepatocito") & keep, "no_ref": keep & ~ref & ~hold}


def etiqueta(c1, c2, c5):
    if not (c1 and c2):
        return "NO RESUELTO"
    if not c5:
        return "FRÁGIL"
    return "RESUELTO posible (C3 y C4 sin calcular)"


def ficha_celulas(ctx, m, sc, thr, CB, D, h2):
    """Filas anónimas (sin índice) de las células de la máscara, ordenadas por margen sobre el umbral."""
    L0 = ctx.L0
    tes = np.isin(L0.sym, TESTIGOS)
    tcb = np.asarray(CB.X[:, tes].sum(axis=1)).ravel()
    tcr = np.asarray(L0.X[:, tes].sum(axis=1)).ravel()
    s006 = D["dbr006"]["score"]
    sh2 = np.sort(s006[h2])
    filas = []
    for i in np.where(m)[0]:
        f = {"tipo": str(L0.tipo[i]), "UMI": float(L0.umi[i]), "genes": int(ctx.qc["ngen"][i]),
             "UMI_testigos_cellbender": float(tcb[i]), "UMI_testigos_cruda": float(tcr[i]),
             "testigo_mas_cellbender": bool(tcb[i] > 0), "testigo_mas_cruda": bool(tcr[i] > 0),
             "score_dbr006": float(s006[i]), "percentil_score_dbr006_en_H2": float(np.searchsorted(sh2, s006[i], side="right") / max(len(sh2), 1)),
             "clase_dbr006": "doublet" if D["dbr006"]["doblete"][i] else "singlet",
             **{f"clase_{c}": ("doublet" if D[c]["doblete"][i] else "singlet") for c in CONFIGS},
             "margen_sobre_umbral": float(sc[i] - thr)}
        filas.append(f)
    return sorted(filas, key=lambda f: -f["margen_sobre_umbral"])


def p2(ctx, D, CB):
    from scipy.stats import fisher_exact, spearmanr
    L0 = ctx.L0
    Dd = ctx.D["doblete"]
    gb = ctx.grp["base"]
    canon = pd.read_csv(os.path.join(_hc.BASE, "posthoc_semillas_holdout.csv"))
    filas, base = [], {}
    repro = []
    for s in range(0, SEMILLAS + 1):
        gr = HF.grupos_semilla(L0, ctx.qc, _a.SEED + s, excluir=Dd)
        ge = grupos_estratificado(L0, ctx.qc, _a.SEED + s, excluir=Dd)
        assert all((gr[k] == ge[k]).all() for k in ("keep", "ref", "pool", "no_ref", "hep", "pp")) and ((gr["h1"] | gr["h2"]) == (ge["h1"] | ge["h2"])).all(), \
            "el hold-out estratificado no es el mismo que el aleatorio"
        if s == 0:
            assert all((gr[k] == gb[k]).all() for k in gb), "la semilla 0 no reproduce la base canónica"
        for crit in ("estandar", "estricto"):
            with quieto():
                r4, _, sc = AF.v4(L0, gr, ctx.genes[crit])
            a_h, a_p = r4["AUC_pool_vs_hepatocitos"], r4["AUC_pool_vs_periportales"]
            c1 = bool(a_h >= 0.6 and a_p >= 0.6)
            for part, g in (("aleatoria", gr), ("estratificada", ge)):
                thr, call, m = AF.llamada_v4(sc, g)
                fh, fp = m["FP_H2"], m["FP_periportales"]
                c2 = bool(fh["frac"] <= 0.02 and fp["frac"] <= 0.01)
                fr = int((call & g["no_ref"]).sum()) / int(g["keep"].sum())
                c5 = bool(0.58 <= fr <= 0.82)
                c_h1, c_h2 = comp(L0, g["h1"]), comp(L0, g["h2"])
                fila = {"semilla": s, "particion": part, "criterio": crit, "n_H1": int(g["h1"].sum()), "n_H2": int(g["h2"].sum()),
                        **{f"H1_{t}": c_h1[t] for t in TIPOS_H}, **{f"H2_{t}": c_h2[t] for t in TIPOS_H},
                        "desequilibrio_TVD": tvd(c_h1, c_h2), "umbral": thr,
                        "FP_H2_k": fh["k"], **{f"FP_H2_{t}": int((call & g["h2"] & (L0.tipo == t)).sum()) for t in TIPOS_H},
                        "H1_sobre_umbral": int((call & g["h1"]).sum()),
                        **{f"H1_sobre_umbral_{t}": int((call & g["h1"] & (L0.tipo == t)).sum()) for t in TIPOS_H},
                        "FP_pp_k": fp["k"], "FP_pp_n": fp["n"], "C2": c2, "AUC_hep": a_h, "AUC_pp": a_p, "C1": c1,
                        "llamadas_pool": m["en_pool"]["k"], "fraccion_QC": fr, "C5": c5, "C2_y_C5": bool(c2 and c5),
                        "etiqueta_informativa": etiqueta(c1, c2, c5)}
                filas.append(fila)
                if s == 0:
                    base[(crit, part)] = (call, sc, thr, g)
            if s >= 1:
                cr = canon[(canon.criterio == crit) & (canon.semilla == s)].iloc[0]
                f = [x for x in filas if x["semilla"] == s and x["criterio"] == crit and x["particion"] == "aleatoria"][0]
                repro.append(bool(int(cr["FP_H2_k"]) == f["FP_H2_k"] and int(cr["FP_pp_k"]) == f["FP_pp_k"]
                                  and int(cr["llamadas_pool"]) == f["llamadas_pool"]))
        print(f"P2 semilla {s}: hecho", flush=True)
    df = pd.DataFrame(filas)
    df.map(_hc.r_).to_csv(_hc.outp("p2_semillas_holdout.csv"), index=False)
    out = {"reproduce_posthoc_semillas_canonico": {"filas_comparadas": len(repro), "identicas": int(sum(repro)), "TODAS": bool(all(repro))}}
    res = {}
    for crit in ("estandar", "estricto"):
        for part in ("aleatoria", "estratificada"):
            d = df[(df.criterio == crit) & (df.particion == part) & (df.semilla >= 1)]
            res[f"{crit}|{part}"] = {
                "n_semillas": int(len(d)), "C1_pasa": int(d.C1.sum()), "C2_pasa": int(d.C2.sum()), "C5_pasa": int(d.C5.sum()),
                "C2_y_C5": int(d.C2_y_C5.sum()), "etiquetas": {k: int(v) for k, v in d.etiqueta_informativa.value_counts().items()},
                "FP_H2_min_mediana_max": [int(d.FP_H2_k.min()), float(d.FP_H2_k.median()), int(d.FP_H2_k.max())],
                "FP_H2_media": float(d.FP_H2_k.mean()),
                "FP_pp_min_mediana_max": [int(d.FP_pp_k.min()), float(d.FP_pp_k.median()), int(d.FP_pp_k.max())],
                "fraccion_QC_min_mediana_max": [float(d.fraccion_QC.min()), float(d.fraccion_QC.median()), float(d.fraccion_QC.max())],
                "AUC_hep_min": float(d.AUC_hep.min()), "AUC_pp_min": float(d.AUC_pp.min()),
                "desequilibrio_TVD_mediana": float(d.desequilibrio_TVD.median()),
                "FP_H2_por_tipo_total": {t: int(d[f"FP_H2_{t}"].sum()) for t in TIPOS_H},
                "H1_sobre_umbral_por_tipo_total": {t: int(d[f"H1_sobre_umbral_{t}"].sum()) for t in TIPOS_H}}
            b = df[(df.criterio == crit) & (df.particion == part) & (df.semilla == 0)].iloc[0]
            res[f"{crit}|{part}"]["base_semilla_0"] = {k: _hc.r_(b[k]) for k in ("n_H1", "n_H2", "FP_H2_k", "FP_pp_k", "C1", "C2", "C5", "fraccion_QC", "umbral", "etiqueta_informativa", "desequilibrio_TVD")}
            res[f"{crit}|{part}"]["base_semilla_0"]["H1"] = {t: int(b[f"H1_{t}"]) for t in TIPOS_H}
            res[f"{crit}|{part}"]["base_semilla_0"]["H2"] = {t: int(b[f"H2_{t}"]) for t in TIPOS_H}
        a = df[(df.criterio == crit) & (df.particion == "aleatoria") & (df.semilla >= 1)].set_index("semilla")
        e = df[(df.criterio == crit) & (df.particion == "estratificada") & (df.semilla >= 1)].set_index("semilla")
        emp = {"ambas_pasan": int((a.C2 & e.C2).sum()), "solo_aleatoria": int((a.C2 & ~e.C2).sum()),
               "solo_estratificada": int((~a.C2 & e.C2).sum()), "ninguna": int((~a.C2 & ~e.C2).sum())}
        dif = int(e.C2.sum()) - int(a.C2.sum())
        if int(e.C2.sum()) >= 15 and int(a.C2.sum()) <= 12:
            lect = "C2 MEDÍA SOBRE TODO EL SORTEO DE TIPOS"
        elif abs(dif) <= 3:
            lect = "EL RUIDO DE C2 ES EL DEL CUANTIL CON n ≈ 140, NO EL REPARTO DE TIPOS"
        else:
            lect = "INTERMEDIO: el reparto de tipos mueve C2 pero no lo explica entero"
        rho = spearmanr(a.desequilibrio_TVD.values, a.FP_H2_k.values)
        res[f"{crit}|emparejado"] = {"C2_por_semilla": emp, "diferencia_estratificada_menos_aleatoria": dif, "LECTURA_NO_DECIDE": lect,
                                     "desequilibrio_aleatoria": {"TVD_mediana_C2_pasa": float(a[a.C2].desequilibrio_TVD.median()) if a.C2.any() else None,
                                                                 "TVD_mediana_C2_falla": float(a[~a.C2].desequilibrio_TVD.median()) if (~a.C2).any() else None,
                                                                 "spearman_TVD_FP_H2": float(rho[0]), "p": float(rho[1])}}
    out["barridos"] = res
    # FP de la base (semilla 0, partición aleatoria = la canónica)
    fp = {}
    call, sc, thr, g = base[("estandar", "aleatoria")]
    h2 = g["h2"]
    fp["estandar_FP_H2"] = ficha_celulas(ctx, call & h2, sc, thr, CB, D, h2)
    fp["estandar_H1_sobre_umbral"] = ficha_celulas(ctx, call & g["h1"], sc, thr, CB, D, h2)
    tes = np.isin(L0.sym, TESTIGOS)
    tcb = np.asarray(CB.X[:, tes].sum(axis=1)).ravel() > 0
    nl = h2 & ~call
    fp["estandar_H2_no_llamadas_AGREGADO"] = {"n": int(nl.sum()), "testigo_mas_cellbender": _hc.w(int((nl & tcb).sum()), int(nl.sum())),
                                              "UMI": mediana_iqr(L0.umi[nl]), "por_tipo": comp(L0, nl),
                                              "score_dbr006": mediana_iqr(D["dbr006"]["score"][nl]),
                                              **{f"dobletes_{c}": _hc.w(int((nl & D[c]["doblete"]).sum()), int(nl.sum())) for c in CONFIGS}}
    k_fp = int((call & h2 & tcb).sum())
    n_fp = int((call & h2).sum())
    odds, pf = fisher_exact([[k_fp, n_fp - k_fp], [int((nl & tcb).sum()), int((nl & ~tcb).sum())]])
    fp["estandar_testigo_FP_vs_no_llamadas"] = {"FP_testigo_mas": _hc.w(k_fp, n_fp), "fisher_odds": float(odds) if np.isfinite(odds) else None, "fisher_p": float(pf)}
    frac_nl = (nl & tcb).sum() / max(nl.sum(), 1)
    fp["LECTURA_NO_DECIDE"] = ("C2 FALLA EN CONTROLES CON ARN TUMORAL" if (k_fp >= 3 and k_fp / max(n_fp, 1) > frac_nl)
                               else "NO SE SOSTIENE que C2 falle por controles con ARN tumoral")
    call_e, sc_e, thr_e, g_e = base[("estricto", "aleatoria")]
    fp["estricto_FP_H2"] = ficha_celulas(ctx, call_e & g_e["h2"], sc_e, thr_e, CB, D, g_e["h2"])
    fp["estricto_FP_periportales"] = ficha_celulas(ctx, call_e & g_e["pp"], sc_e, thr_e, CB, D, g_e["h2"])
    out["FP_de_la_base"] = fp
    return out


# ---------------------------------------------------------------- P3
def p3(ctx, D, llam):
    L0 = ctx.L0
    gs, gb = ctx.grp["sin_exclusion_dobletes"], ctx.grp["base"]
    with quieto():
        _, call_sin, _ = AF.v4(L0, gs, ctx.genes["estandar"])
    rep = [int((call_sin & gs["pool"]).sum()), int(gs["pool"].sum())]
    call_b = llam["estandar"][0]
    cpb = call_b & gb["pool"]
    out = {"llamadas_sin_exclusion_reproducen_v4": {"obtenido": rep, "esperado": [6245, 13320], "IDENTICO": rep == [6245, 13320]}}
    res = {}
    for c in ("dbr006", "dbrNULL") + CONFIGS:
        Dv = D[c]
        d = Dv["doblete"]
        proxy = types.SimpleNamespace(L0=L0, D=Dv, Dn=Dv, nulo=ctx.nulo)
        with quieto():
            rr = HF.residuo(proxy)
            cd = HF.composicion_dobletes(proxy, gs, call_sin)
        res[c] = {"total": _hc.w(int(d.sum()), L0.n),
                  "por_tipo": {t: _hc.w(int((d & (L0.tipo == t)).sum()), int((L0.tipo == t).sum())) for t in sorted(set(L0.tipo))},
                  "pool_QC_sin_exclusion": _hc.w(int((d & gs["pool"]).sum()), int(gs["pool"].sum())),
                  "control_dobletes_llamadas_estandar_sin_exclusion": cd,
                  "entre_llamadas_pool_estandar_base": _hc.w(int((d & cpb).sum()), int(cpb.sum())),
                  "jaccard_con_dbr006": jacc(d, D["dbr006"]["doblete"]),
                  "residuo_cellbender": rr["cellbender_fpr0.01|dbr006"]["nulo_testigos"],
                  "residuo_cruda_E": rr["cruda|dbr006"]["nulo_testigos"]["E_dobletes_entre_marca_mas"],
                  "LECTURA_REGLA_D2a_INFORMATIVA": rr["LECTURA"]}
    out["configuraciones"] = res
    lo = res["dbr006"]["pool_QC_sin_exclusion"]["ic95_wilson"][1]
    conf = {c: bool(res[c]["pool_QC_sin_exclusion"]["ic95_wilson"][0] > lo) for c in ("sinclusters", "sinclusters_dbrsd1")}
    out["LECTURA_NO_DECIDE"] = {"cota_inferior_en_el_pool": ("CONFIRMADO: el 9,0 % era cota inferior en el pool" if any(conf.values())
                                                             else "NO CONFIRMADO: sin clusters no da más dobletes en el pool que dbr006 (IC solapan o menor)"),
                                "por_configuracion": conf}
    med = float(np.median(L0.umi[gb["pool"]]))
    out["proxy_homotipicos_UMI"] = {"mediana_UMI_pool_base": med,
                                    "llamadas_pool_UMI_mayor_2x": _hc.w(int((cpb & (L0.umi > 2 * med)).sum()), int(cpb.sum())),
                                    "no_llamadas_pool_UMI_mayor_2x": _hc.w(int((gb["pool"] & ~call_b & (L0.umi > 2 * med)).sum()), int((gb["pool"] & ~call_b).sum()))}
    return out


# ---------------------------------------------------------------- P4
def p4(ctx, llam):
    pi = cache2("p4_info.json")
    if not os.path.exists(pi):
        return {"ejecutado": False, "motivo": "no corrido (falta el paso p4_refcon)"}
    info = _hc.lee_json(pi)
    out = {"info": info}
    if not info.get("ejecutado"):
        return out
    L0 = ctx.L0
    gb = ctx.grp["base"]
    with quieto():
        rout, llam_r = HF.refcon(ctx)
    llamadas = {"inferCNV_estandar": llam["estandar"][0], "inferCNV_estricto": llam["estricto"][0],
                "REFCON_receta_v4_estandar": llam_r.get("refcon_estandar"), "REFCON_receta_v4_estricto": llam_r.get("refcon_estricto")}
    reps = []
    for r in range(int(info["replicas"])):
        z = np.load(cache2(f"p4_rep{r}.npz"))
        idx, lab, conf = z["idx"], z["labels"].astype(int), z["conf_normal"].astype(bool)
        an = lab == 1
        sub = {k: v[idx] for k, v in gb.items()}
        tipo = L0.tipo[idx]
        fila = {"replica": r, "n": int(len(idx)), "PURE_sin_etiqueta": int((lab == -1).sum()),
                "aneuploide_en": {k: _hc.w(int((an & sub[k]).sum()), int(sub[k].sum())) for k in ("pool", "hep", "pp", "h1", "h2", "ref", "keep")},
                "aneuploide_por_tipo": {t: _hc.w(int((an & (tipo == t)).sum()), int((tipo == t).sum())) for t in sorted(set(L0.tipo))},
                "fraccion_fuera_de_referencia_sobre_QC": _hc.w(int((an & sub["no_ref"]).sum()), int(sub["keep"].sum())),
                "diploides_confiables_por_tipo": {t: int((conf & (tipo == t)).sum()) for t in sorted(set(L0.tipo))},
                "concordancia_en_pool": {k: (kappa(an[sub["pool"]], v[idx][sub["pool"]]) if v is not None else None) for k, v in llamadas.items()}}
        reps.append(fila)
    out["por_replica"] = reps
    hep = [x["aneuploide_en"]["hep"]["frac"] for x in reps]
    pool = [x["aneuploide_en"]["pool"]["frac"] for x in reps]
    out["rango_entre_replicas_puntos"] = {"pool": 100 * (max(pool) - min(pool)), "hep": 100 * (max(hep) - min(hep))}
    out["LECTURA_NO_DECIDE"] = {
        "separa": ("EL CLASIFICADOR PROPIO SEPARA (hepatocitos <= 5 % y pool >= 50 % en las tres réplicas)"
                   if all(h is not None and h <= 0.05 for h in hep) and all(p is not None and p >= 0.5 for p in pool)
                   else "EL CLASIFICADOR PROPIO NO SEPARA con la heurística declarada"),
        "estable": "ESTABLE" if out["rango_entre_replicas_puntos"]["pool"] < 10 else "NO ESTABLE"}
    return out


# ---------------------------------------------------------------- P5
def p5(ctx, D, llam, CB):
    L0 = ctx.L0
    gb = ctx.grp["base"]
    h5n = L0.ndet(TC.HEP5)
    h5cb = CB.ndet(TC.HEP5)
    h5u = L0.suma(TC.HEP5)
    tes = np.isin(L0.sym, TESTIGOS)
    tcb = np.asarray(CB.X[:, tes].sum(axis=1)).ravel() > 0
    tcr = np.asarray(L0.X[:, tes].sum(axis=1)).ravel() > 0
    ep = np.isin(L0.tipo, ["epitelial", "no_asignado"])
    hep_t = L0.tipo == "hepatocito"
    m812 = gb["no_ref"] & ep & (h5n >= 2)

    def rasgos(m):
        n = int(m.sum())
        r = {"n": n, "por_tipo": {t: int((m & (L0.tipo == t)).sum()) for t in sorted(set(L0.tipo)) if (m & (L0.tipo == t)).any()},
             "UMI": mediana_iqr(L0.umi[m]), "genes": mediana_iqr(ctx.qc["ngen"][m]), "UMI_HEP5": mediana_iqr(h5u[m]),
             "n_HEP5_detectados": {str(k): int((m & (h5n == k)).sum()) for k in range(0, 6) if (m & (h5n == k)).any()},
             "conserva_2_HEP5_en_cellbender": _hc.w(int((m & (h5cb >= 2)).sum()), n),
             "testigo_mas_cellbender": _hc.w(int((m & tcb).sum()), n), "testigo_mas_cruda": _hc.w(int((m & tcr).sum()), n),
             "score_dbr006": mediana_iqr(D["dbr006"]["score"][m]), "dobletes_dbr006": int((m & D["dbr006"]["doblete"]).sum()),
             **{f"dobletes_{c}": _hc.w(int((m & D[c]["doblete"]).sum()), n) for c in CONFIGS}}
        o = collections.Counter(D["dbrsd1"]["origen"][m & D["dbrsd1"]["doblete"]])
        r["origen_dobletes_dbrsd1_top"] = {k: int(v) for k, v in sorted(o.items(), key=lambda kv: (-kv[1], kv[0]))[:5]}
        return r
    out = {}
    for crit, esperado in (("estandar", 628), ("estricto", 279)):
        call = llam[crit][0]
        m = call & gb["no_ref"] & ~gb["pool"] & ~hep_t
        assert not (m & ~m812).any(), "llamadas fuera del pool que no son epitelial/no asignado con >= 2 HEP5"
        out[crit] = {"reproduce": {"obtenido": int(m.sum()), "esperado": esperado, "IDENTICO": int(m.sum()) == esperado},
                     "denominador_812": {"obtenido": int(m812.sum()), "esperado": 812, "IDENTICO": int(m812.sum()) == 812},
                     "llamadas_HEP5": rasgos(m), "no_llamadas_HEP5": rasgos(m812 & ~call),
                     "llamadas_pool": rasgos(call & gb["pool"])}
    out["hepatocitos_QC"] = rasgos(gb["hep"])
    s = out["estandar"]
    ret = s["llamadas_HEP5"]["conserva_2_HEP5_en_cellbender"]["frac"]
    ump = s["llamadas_pool"]["UMI"]["mediana"]
    um = s["llamadas_HEP5"]["UMI"]["mediana"]
    mult = um > 1.5 * ump and any(s["llamadas_HEP5"][f"dobletes_{c}"]["frac"] > s["llamadas_pool"][f"dobletes_{c}"]["frac"] for c in CONFIGS)
    out["LECTURA_NO_DECIDE"] = {
        "ambiente": ("SU HEP5 ES MAYORITARIAMENTE AMBIENTE (< 50 % conserva >= 2 HEP5 en CellBender)" if ret < 0.5
                     else "SU HEP5 NO ES SOLO AMBIENTE (>= 50 % conserva >= 2 HEP5 en CellBender): doblete físico o estado hepatocyte-like, que estos datos no separan"),
        "multipletes": ("COMPATIBLES CON MULTIPLETES" if mult else "NO se cumple la regla de multipletes (UMI > 1,5 × pool y más dobletes que el pool)"),
        "UMI_mediana_628_sobre_pool": um / ump}
    return out


# ---------------------------------------------------------------- etapa de cálculo
def _commit():
    try:
        return subprocess.check_output(["git", "-C", _hc.AQUI, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "desconocido"


def final():
    plan_sha = _hc.sha256(PLAN)
    if plan_sha != PLAN_SHA:
        print("RECHAZADO: POSTHOC_2_PLAN.json no es el commiteado antes del código (sha distinto).")
        return 3
    plan = _hc.lee_json(PLAN)
    canon_sha = {k: sha_salida_canonica(k) for k in plan["base"]["salidas_canonicas_sha256"]}
    if canon_sha != plan["base"]["salidas_canonicas_sha256"]:
        print("RECHAZADO: las salidas canónicas no son las del plan.")
        return 3
    falta = [c for c in CONFIGS if not os.path.exists(cache2(f"scdbl_{c}.csv"))]
    if falta:
        print("RECHAZADO: faltan cachés de P3 (corre antes p3_dobletes):", falta)
        return 3
    res = {"NO_CAMBIA_NINGUN_VEREDICTO": True, "plan_sha256": plan_sha, "commit": _commit(),
           "scripts_sha256": {f: _hc.sha256(os.path.join(_hc.AQUI, f)) for f in SCRIPTS},
           "reglas_sha256": _hc.sha256(_hc.REGLAS), "salidas_canonicas_sha256_intactas": canon_sha,
           "muro": prueba_sandbox()}
    print("muro:", res["muro"])
    HF.POST_HOC.clear()
    ctx = HF.Ctx()
    canon = _hc.lee_json(os.path.join(_hc.BASE, "higado_celula.json"))
    D = dobletes_todos(ctx)
    res["P3_cache_sha256"] = {c: D[c]["sha256"] for c in CONFIGS}
    CB = AF.obj("higado", "cellbender_fpr0.01")
    res["P1"], llam = p1(ctx, canon)
    print("P1:", {c: (res["P1"][c]["reproduce_base_canonica"]["IDENTICO"], res["P1"][c]["spikes_llamados"]["k"]) for c in ("estandar", "estricto")},
          {k: round(v["mediana"], 3) for k, v in res["P1"]["r_por_brazo"]["mitades_primaria"].items()}, flush=True)
    res["P2"] = p2(ctx, D, CB)
    print("P2:", res["P2"]["reproduce_posthoc_semillas_canonico"], {k: v.get("C2_pasa", v.get("LECTURA_NO_DECIDE")) for k, v in res["P2"]["barridos"].items()}, flush=True)
    res["P3"] = p3(ctx, D, llam)
    print("P3:", {c: round(v["total"]["frac"], 4) for c, v in res["P3"]["configuraciones"].items()}, flush=True)
    res["P4"] = p4(ctx, llam)
    print("P4:", res["P4"].get("LECTURA_NO_DECIDE", res["P4"].get("info", {}).get("motivo", res["P4"].get("motivo"))), flush=True)
    res["P5"] = p5(ctx, D, llam, CB)
    print("P5:", {c: res["P5"][c]["reproduce"] for c in ("estandar", "estricto")}, res["P5"]["LECTURA_NO_DECIDE"], flush=True)
    res["POST_HOC_2"] = ["P1_SPIKEIN_Y_MITADES", "P2_COMPOSICION_Y_ESTRATIFICADO", "P3_SCDBLFINDER_FLEX_Y_SIN_CLUSTERS",
                         "P4_CLASIFICADOR_REFCON_SUBMUESTRAS", "P5_LLAMADAS_CON_HEP5"]
    res["POST_HOC_canonico_reutilizado"] = sorted(set(HF.POST_HOC + AF.POST_HOC))
    p = _hc.guarda_json(res, "posthoc2.json")
    print("guardado", os.path.basename(p))
    return 0


# ---------------------------------------------------------------- manifiesto
def _arbol(d):
    sal = {}
    for raiz, dirs, fs in os.walk(d):
        dirs.sort()
        for f in sorted(fs):
            if not f.startswith("MANIFIESTO"):
                sal[os.path.relpath(os.path.join(raiz, f), d)] = _hc.sha256(os.path.join(raiz, f))
    return dict(sorted(sal.items()))


def manifiesto():
    d = os.path.join(_hc.BASE, "posthoc2")
    rep2 = os.path.join(_hc.BASE, "_posthoc2_rep2")
    sal = _arbol(d)
    dos = None
    if os.path.isdir(rep2):
        s2 = _arbol(rep2)
        dif = sorted(k for k in set(sal) | set(s2) if sal.get(k) != s2.get(k))
        dos = {"n_salidas": len(sal), "identicas": not dif, "difieren": dif}
    cache = {}
    dc = os.path.dirname(cache2("x"))
    for f in sorted(os.listdir(dc)):
        if f.endswith((".csv", ".json", ".npz", ".npy", ".txt")):
            cache[f] = _hc.sha256(os.path.join(dc, f))
    canon_man = _hc.lee_json(os.path.join(_hc.BASE, "MANIFIESTO_HIGADO_CELULA.json"))
    dif_c = sorted(f for f, h in canon_man["salidas"].items() if _hc.sha256(os.path.join(_hc.BASE, f)) != h)
    m = {"salidas": sal, "dos_ejecuciones": dos, "cache_posthoc2": cache, "plan_sha256": _hc.sha256(PLAN),
         "scripts_sha256": {f: _hc.sha256(os.path.join(_hc.AQUI, f)) for f in SCRIPTS}, "reglas_sha256": _hc.sha256(_hc.REGLAS),
         "salidas_canonicas_fase2": {"n": len(canon_man["salidas"]), "difieren": dif_c, "intactas": not dif_c},
         "commit_al_generar": _commit(), "semilla": _hc.SEED, "NO_CAMBIA_NINGUN_VEREDICTO": True}
    with open(os.path.join(d, "MANIFIESTO_POSTHOC2.json"), "w") as f:
        json.dump(m, f, indent=1, ensure_ascii=False, sort_keys=True)
    print("MANIFIESTO_POSTHOC2:", len(sal), "salidas | dos ejecuciones:", dos and {k: v for k, v in dos.items() if k != "difieren"},
          "| difieren:", dos and dos["difieren"][:5], "| canónicas intactas:", not dif_c)
    return 0 if ((dos is not None and dos["identicas"]) and not dif_c) else 1


if __name__ == "__main__":
    print("módulo: usa posthoc2.py por la ventanilla", file=sys.stderr)
    sys.exit(2)
