#!/usr/bin/env python3
"""Datos de EJEMPLO del explorador single-cell (vista previa pública de /celula-a-celula/explorar).

Por qué existe (18-oct-2026, plan aprobado «fase 1 del visor web»): la página se construye y se revisa en una
vista previa pública, así que ahí solo puede haber datos INVENTADOS. Hasta hoy los generaba un script de la web
con su propio formato; este los genera con los MISMOS constructores del contrato (_contrato.py) que el export
real y los pasa por el MISMO validador (valida_salidas). Así la web se construye sobre un único formato.

Nada del análisis real: tamaños de grupo inventados (N_INTERES_EJEMPLO, no los del acta), veredictos
«sin_veredicto», cifras aleatorias con semilla fija, genes extra con nombre EJ0000…; todo va con ejemplo: true.
No lee la zona clínica. Determinista: dos ejecuciones dan los mismos JSON.

Uso:
    python3 tools/flex_analisis/explorador/ejemplo.py                 # genera y valida en una carpeta temporal
    python3 tools/flex_analisis/explorador/ejemplo.py --web <dir>     # además copia a <dir> (public/celula/explorador)
"""
import os
import sys

_NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
try:
    import anndata as an
    import numpy as np
    import pandas as pd
    import scipy.sparse as sp
except ModuleNotFoundError:
    if os.path.isfile(_NESCORE) and os.path.realpath(sys.executable) != os.path.realpath(_NESCORE):
        os.execv(_NESCORE, [_NESCORE, os.path.abspath(__file__)] + sys.argv[1:])
    print("Falta anndata/numpy y el env nescore", file=sys.stderr)
    sys.exit(77)

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import warnings  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _contrato as C  # noqa: E402
import _suelo_todos as ST  # noqa: E402

SEMILLA = 424242
N_CELULAS = {"higado": 1600, "hueso": 420}
N_INTERES_EJEMPLO = {"higado": 170, "hueso": 45}       # INVENTADOS: no son los del análisis
N_GENES_EXTRA = 1200
GENES = list(C.GENES_WEB) + ["EJ%04d" % i for i in range(N_GENES_EXTRA)]
GENES_WEB_EJEMPLO = list(C.GENES_WEB)[:40]
TIPOS_LESION = {
    "higado": (["epitelial", "no_asignado"],
               ["hepatocito", "NK", "mieloide", "endotelio", "fibroblasto_estelar", "epitelial", "colangiocito"]),
    "hueso": (["epitelial", "no_asignado"],
              ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito", "endotelio", "osteoclasto", "eritroide"]),
}


def lesion(les, rng):
    """Una lesión inventada: tipos, grupo de interés, matriz de cuentas con algo de señal y UMAP por grupos."""
    n, k = N_CELULAS[les], N_INTERES_EJEMPLO[les]
    base, resto = TIPOS_LESION[les]
    tipo = np.array(list(rng.choice(base, k)) + list(rng.choice(resto, n - k)), dtype=object)
    interes = np.zeros(n, bool)
    interes[:k] = True
    limpio = rng.random(n) > 0.15 if les == "hueso" else None
    grupo = C.grupos_lesion(les, tipo, interes, limpio)
    tipo_c = C.tipo_canonico(tipo)
    G = len(GENES)
    tasa = rng.gamma(0.4, 0.6, G)                                   # nivel de base por gen
    grupos = sorted(set(grupo.astype(str)))
    centro = {g: rng.normal(size=2) * 8 for g in grupos}
    favorito = {g: rng.choice(G, 25, replace=False) for g in grupos}  # genes un poco más altos en cada grupo
    filas = []
    umap = np.zeros((n, 2))
    for i in range(n):
        g = str(grupo[i])
        t = tasa.copy()
        t[favorito[g]] *= 6
        prof = rng.uniform(0.6, 1.6)
        filas.append(sp.csr_matrix(rng.poisson(t * prof).astype(np.int32)))
        umap[i] = centro[g] + rng.normal(size=2) * 1.2
    X = sp.vstack(filas).tocsr()
    X.sort_indices()
    umi = np.asarray(X.sum(axis=1)).ravel().astype(np.int64)
    falta = umi == 0
    if falta.any():                                                  # una UMI mínima: el contrato exige >= 1
        X = X.tolil()
        for i in np.where(falta)[0]:
            X[i, 0] = 1
        X = X.tocsr()
        umi = np.asarray(X.sum(axis=1)).ravel().astype(np.int64)
    return {"lesion": les, "n": n, "X": X, "umi": umi, "grupo": grupo, "tipo_c": tipo_c, "interes": interes,
            "limpio": limpio, "umap": umap}


def marcadores(L, todos, excl):
    """Wilcoxon de scanpy sobre los datos INVENTADOS, igual que exporta.marcadores_lesion."""
    import scanpy as sc
    keep = ~excl
    Xn = (sp.diags(1e4 / L["umi"].astype(float)) @ sp.csr_matrix(L["X"], dtype=np.float64)).tocsr()[:, keep]
    Xn.data = np.log1p(Xn.data)
    grupo = np.asarray(L["grupo"]).astype(str)
    presentes = [g for g in todos["grupos"] if (grupo == g).sum() >= 2]
    a = an.AnnData(X=Xn, obs=pd.DataFrame({"g": pd.Categorical(grupo, categories=todos["grupos"])},
                                          index=[str(i) for i in range(len(grupo))]),
                   var=pd.DataFrame(index=np.asarray(GENES)[keep]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sc.tl.rank_genes_groups(a, "g", groups=presentes, reference="rest", method="wilcoxon",
                                n_genes=C.MARCADORES_N, pts=True, use_raw=False)
    r = a.uns["rank_genes_groups"]
    pos = {s: i for i, s in enumerate(GENES)}
    col = {g: k for k, g in enumerate(todos["grupos"])}
    out = {}
    for g in presentes:
        lfc = np.asarray(r["logfoldchanges"][g], float)
        fin = np.isfinite(lfc)
        genes = [str(x) for x, ok in zip(r["names"][g], fin) if ok]
        out[g] = {"gen": genes, "logfc": [float(np.round(x, 4)) for x in lfc[fin]],
                  "fdr": [float("%.4g" % min(max(x, 0.0), 1.0)) for x in np.asarray(r["pvals_adj"][g], float)[fin]],   # 4 cifras: puerta_n1 cierra la notación ingeniera larga
                  "frac_grupo": [float(np.round(x, 4)) for x in r["pts"].loc[genes, g]],
                  "frac_resto": [float(np.round(x, 4)) for x in r["pts_rest"].loc[genes, g]],
                  "suelo": [float(todos["suelo"][pos[x], col[g]]) for x in genes]}
    return out


def agregados(L, rng):
    """Suelo de todos los genes y agregados: los MISMOS van al JSON y al Zarr."""
    les, n, G = L["lesion"], L["n"], len(GENES)
    grupo = np.asarray(L["grupo"]).astype(str)
    g_ids = C.grupos_zarr(les, grupo)
    ref = np.isin(grupo, C.GRUPOS[les]["referencia"])
    excl = np.zeros(G, bool)
    excl[-3:] = True                                                 # tres genes «sin sonda» de ejemplo
    todos = ST.calcula(L["X"], rng.dirichlet(np.ones(G)), float(rng.uniform(0.2, 1.5)), L["umi"],
                       rng.uniform(0, 0.004, n) if les == "hueso" else np.zeros(n), rng.integers(0, 4, G),
                       grupo, g_ids, ref, rng.uniform(0.2, 2.0, int(ref.sum())), excl)
    ns = rng.integers(1, 7, G)
    ns[excl] = 0
    nu = np.minimum(rng.integers(0, 3, G), ns)
    perfil = {"cromosoma": [], "inicio_mb": [], "log2_rel": []}
    nivel = 0.0
    for c in range(1, 23):
        for b in range(int(rng.integers(6, 20))):
            nivel = 0.7 * nivel + rng.normal(scale=0.15)
            perfil["cromosoma"].append("chr%d" % c)
            perfil["inicio_mb"].append(float(10 * b))
            perfil["log2_rel"].append(float(np.round(nivel, 3)))
    adn_interes = float(np.round(rng.uniform(0.002, 0.03), 5))
    adn = {g: (adn_interes if g == g_ids[0] else float(np.round(rng.uniform(0.002, 0.04), 5)))
           for g in g_ids if (grupo == g).any()}
    return {"todos": todos, "excl": excl, "ns": ns, "nu": nu, "marc": marcadores(L, todos, excl),
            "perfil": perfil, "adn": adn, "adn_interes": adn_interes}


def web(L, A, rng):
    """web/<lesión>.json del contrato, con los números INVENTADOS y los mismos agregados que el Zarr."""
    les = L["lesion"]
    g = C.GRUPOS[les]
    g_ids = A["todos"]["grupos"]
    rol = {g["interes"]: "interes", g["otras"]: "otras"}
    tipos = sorted(set(L["tipo_c"].astype(str)))
    um = L["umap"]
    esc = [((um[:, k] - um[:, k].min()) / (um[:, k].max() - um[:, k].min())) * 20 - 10 for k in range(2)]
    plano = []
    for i in range(L["n"]):
        plano += [round(float(esc[0][i]), 2), round(float(esc[1][i]), 2)]
    pos = {s: i for i, s in enumerate(GENES)}
    col = {x: k for k, x in enumerate(g_ids)}
    genes = GENES_WEB_EJEMPLO
    expr, suelo, refd = {}, {}, {}
    Xc = L["X"].tocsc()
    for gname in genes:
        j = pos[gname]
        v = np.asarray(Xc[:, j].todense()).ravel()
        nz = np.where(v > 0)[0]
        expr[gname] = {"i": [int(i) for i in nz], "v": [float("%.3g" % x) for x in v[nz] / L["umi"][nz] * 1e4]}
        suelo[gname] = {x: float(A["todos"]["suelo"][j, col[x]]) for x in g_ids}
        refd[gname] = float(A["todos"]["referencia"][j])
    if les == "hueso":
        cnv = {"ventanas": list(C.CNV_VENTANAS), "metodo": C.CNV_METODOS["todas"],
               "valores": [[(round(float(x), 3) if lim else None) for x, lim in zip(rng.normal(size=L["n"]), L["limpio"])]
                           for _ in C.CNV_VENTANAS]}
        sel = C.seleccion_lesion(genes, {"identidad": ["EPCAM", "KRT19"]})
        loo = {}
    else:
        cnv = {"pseudobulk": {"ventana": "8p11", "percentil_genoma": int(rng.integers(40, 99))}}
        sel = C.seleccion_lesion(genes, {"identidad": ["ESR1", "KRT19", "FOXA1"], "fgfr1_alto": ["FGFR1"],
                                         "exclusion": ["ALB", "PTPRC"], "ligado_er": ["CCND1", "PGR", "AR"]})
        loo = {x: {"n": int(rng.integers(50, 400)), "frac": float(np.round(rng.random(), 4)),
                   "ref": float(np.round(rng.random(), 4)), "cp10k_pseudobulk": float(np.round(rng.uniform(0.5, 40), 1))}
               for x in sel if x == "ESR1" or rng.random() < 0.4}   # ESR1 siempre: la página enseña ese caso
    sondas = {x: [int(A["nu"][pos[x]]), int(A["ns"][pos[x]])] for x in genes}
    adn = {"sondas_dentro_de_exon": 40000, "sondas_panel": 50000, "frac_lecturas_estimada": float(np.round(rng.uniform(0.002, 0.03), 5)),
           "frac_lecturas_cell_ranger": float(np.round(rng.uniform(0.002, 0.03), 5)), "frac_lecturas_interes": A["adn_interes"]}
    n0 = L["n"] + int(rng.integers(80, 300))
    qc = C.construye_qc({"n_inicial": n0, "n_final": L["n"], "umbral_UMI": [float(rng.integers(80, 200)), float(rng.integers(6000, 12000))],
                         "umbral_genes": [float(rng.integers(60, 150)), float(rng.integers(2000, 4000))],
                         "umbral_pct_MT": "sin sondas MT en el panel: no aplicable",
                         "excluidas_UMI_genes": int(rng.integers(40, 200)), "scrublet_dobletes": int(rng.integers(10, 80)),
                         "scrublet_umbral": float(np.round(rng.uniform(0.2, 0.4), 3)),
                         "mediana_UMI_inicial": float(np.median(L["umi"]) * 0.95), "mediana_UMI_final": float(np.median(L["umi"])),
                         "mediana_genes_inicial": float(rng.integers(300, 900)), "mediana_genes_final": float(rng.integers(300, 900)),
                         "n_sondas_MT": 0})
    gpos = {x: i for i, x in enumerate(g_ids)}
    tpos = {x: i for i, x in enumerate(tipos)}
    return {"version": 1, "lesion": les, "n_celulas": L["n"],
            "grupos": [{"id": x, "rol": rol.get(x, "referencia"), "n": int((L["grupo"] == x).sum())} for x in g_ids],
            "tipos": tipos,
            "celulas": {"umap": plano, "grupo": [gpos[x] for x in L["grupo"].astype(str)],
                        "tipo": [tpos[x] for x in L["tipo_c"].astype(str)]},
            "genes": genes, "expr": expr, "suelo": suelo, "referencia": refd,
            "veredicto": {x: "sin_veredicto" for x in genes}, "sondas": sondas, "seleccion": sel, "loo": loo,
            "adn": adn, "cnv": cnv, "qc": qc,
            "marcadores": A["marc"], "perfil_bins": A["perfil"], "adn_por_grupo": A["adn"]}


def _json(p, obj):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


sha_zarr = C.sha_arbol          # huella del árbol de un Zarr (la misma que comprueba la web)


def genera(out):
    """Export de ejemplo completo en `out` (con h5ad, como el real) y validado con el contrato."""
    rng = np.random.default_rng(SEMILLA)
    C.N_INTERES = dict(N_INTERES_EJEMPLO)                            # solo en este proceso, para el ejemplo
    Ls = {t: lesion(t, rng) for t in C.LESIONES}
    As = {t: agregados(Ls[t], rng) for t in C.LESIONES}
    Lh, Lb = Ls["higado"], Ls["hueso"]
    obs = C.construye_obs(["higado"] * Lh["n"] + ["hueso"] * Lb["n"], np.concatenate([Lh["grupo"], Lb["grupo"]]),
                          np.concatenate([Lh["tipo_c"], Lb["tipo_c"]]), np.vstack([Lh["umap"], Lb["umap"]]))
    webs = {t: web(Ls[t], As[t], rng) for t in C.LESIONES}
    cnv_h = pd.DataFrame({wv: np.array([np.nan if x is None else x for x in webs["hueso"]["cnv"]["valores"][i]])
                          for i, wv in enumerate(C.CNV_VENTANAS)}, index=obs.index[Lh["n"]:])
    uns = C.construye_uns(suelo={t: webs[t]["suelo"] for t in C.LESIONES},
                          referencia={t: webs[t]["referencia"] for t in C.LESIONES},
                          veredicto={t: webs[t]["veredicto"] for t in C.LESIONES}, cnv_hueso=cnv_h,
                          cnv_higado_pseudobulk=webs["higado"]["cnv"]["pseudobulk"],
                          avisos=["DATOS DE EJEMPLO, inventados: no son de la paciente."], version=C.version_contrato("todas"))
    X = sp.vstack([Lh["X"], Lb["X"]]).tocsr().astype(np.int32)
    var = pd.DataFrame({"ensembl": ["ENSGEJ%09d" % i for i in range(len(GENES))]}, index=pd.Index(GENES))
    os.makedirs(os.path.join(out, "web"), exist_ok=True)
    an.AnnData(X=X, obs=obs, var=var, uns=uns).write_h5ad(os.path.join(out, "explorador.h5ad"))
    for t in C.LESIONES:
        _json(os.path.join(out, "web", "%s.json" % t), webs[t])
    fich = [{"ruta": "%s.json" % t, "sha256": _sha(os.path.join(out, "web", "%s.json" % t))} for t in C.LESIONES]
    _json(os.path.join(out, "web", "manifiesto.json"),
          {"version": 1, "sello": "sin_verificar", C.CLAVE_OK_TITULAR: False, "lesiones": list(C.LESIONES),
           "ficheros": fich, "modo_sondas": C.ETIQUETA_MODO["todas"]})
    idx = list(obs.index)
    for t, ids in (("higado", idx[:Lh["n"]]), ("hueso", idx[Lh["n"]:])):
        L, A = Ls[t], As[t]
        C.escribe_zarr_lesion(os.path.join(out, C.ZARR_DIR, "%s.zarr" % t), t, L["X"], L["umi"], GENES,
                              list(var["ensembl"]), L["grupo"], L["tipo_c"], L["umap"], ids, A["todos"],
                              C.version_contrato("todas"), "todas", A["adn"], A["ns"], A["nu"], A["marc"], A["perfil"])
    return C.valida_salidas(out, prohibidas=set(), con_informe=False)


def copia_web(out, destino):
    """A la carpeta pública de la web: JSON con ejemplo: true, Zarr tal cual y manifiesto de la web."""
    os.makedirs(destino, exist_ok=True)
    fich = []
    for t in C.LESIONES:
        obj = json.load(open(os.path.join(out, "web", "%s.json" % t), encoding="utf-8"))
        obj = {"version": obj["version"], "lesion": obj["lesion"], "ejemplo": True,
               **{k: v for k, v in obj.items() if k not in ("version", "lesion")}}
        p = os.path.join(destino, "%s.json" % t)
        _json(p, obj)
        fich.append({"ruta": "%s.json" % t, "sha256": _sha(p)})
    zarrs = []
    for t in C.LESIONES:
        z = os.path.join(destino, C.ZARR_DIR, "%s.zarr" % t)
        if os.path.exists(z):
            shutil.rmtree(z)
        shutil.copytree(os.path.join(out, C.ZARR_DIR, "%s.zarr" % t), z)
        zarrs.append({"ruta": "%s/%s.zarr" % (C.ZARR_DIR, t), "sha256_arbol": C.sha_arbol(z)})
    man = {"version": 1, "ejemplo": True, "sello": "sin_verificar", C.CLAVE_OK_TITULAR: False,
           "modo_sondas": C.ETIQUETA_MODO["todas"], "lesiones": list(C.LESIONES), "ficheros": fich,
           "zarr": zarrs, "matriz_completa": "ejemplo"}
    with open(os.path.join(destino, "manifiesto.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(man, ensure_ascii=False, indent=2) + "\n")
    return man


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--web", help="carpeta pública de la web (public/celula/explorador) donde copiar el ejemplo")
    a = p.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="explorador_ejemplo_")
    try:
        val = genera(tmp)
        print("ejemplo válido con el contrato:", json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ("n_celulas", "n_genes")}
                                                             for k, v in val.items() if isinstance(v, dict)}, ensure_ascii=False))
        if a.web:
            man = copia_web(tmp, a.web)
            print("copiado a", a.web, "·", ", ".join("%s %s" % (f["ruta"], f["sha256"][:12]) for f in man["ficheros"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
