#!/usr/bin/env python
"""Explorador single-cell del Flex: export CURADO y REAL (opción C del plan del explorador), SOLO dentro
de la zona clínica (~/Clinico-PRIVADO/flex/explorador/). Nada sale de ahí sin el OK de {{TITULAR}}.

Corre SOLO por la ventanilla:
    python3 tools/lector_clinico.py procesa flex_explorador -- exportar [--sondas todas|spliced]
    python3 tools/lector_clinico.py procesa flex_explorador -- validar
    python3 tools/lector_clinico.py procesa flex_explorador -- diagnostico

Qué hace `exportar` (en este orden):
  1. U3: lee config.csv del pool por la vía del tribunal (probe barcode → hígado/hueso). Cuenta cuántas
     muestras lleva el pool y cuántas son de la paciente; de otras no entra nada. Ningún id de bloque ni
     el nombre del pool se escribe en ninguna salida: quedan en memoria como formas prohibidas.
  2. Células del QC común (las de las cachés del cierre, mismo orden), alineadas por barcode contra la
     matriz filtrada antes de sustituir el barcode por un índice secuencial (c00000…).
  3. Reconstruye desde <muestra>_raw_probe_bc_matrix.h5 la matriz de sondas que cruzan una unión
     exón-exón (region == spliced, included) sumada por gen, y la de las demás sondas, para MEDIR el
     delta. HALLAZGO (diagnostico, 17-oct): en este probe set solo 7.497 de 53.519 sondas included son
     «spliced» y 7 de los 10 genes del panel tienen 0; una matriz solo-spliced no sirve al explorador.
     Por eso el modo por defecto es `--sondas todas`: X = cuentas de Cell Ranger (todas las sondas
     included), la misma base del acta, con el ADN tratado como el cierre (término de ADN en el suelo
     del hueso). `--sondas spliced` produce el export literal solo-spliced (declarado, no recomendado).
  4. Grupos con vocabulario cerrado (hígado: luminal_alta_confianza 993 del análisis higado_dianas;
     hueso: malignas_limpias 68 del tribunal), tipo celular ^[a-z_]+$, UMAP por lesión (semilla).
  5. Anotaciones (uns, no obs): suelo ambiental por gen y grupo con la definición única del cierre,
     referencia emparejada por profundidad, veredicto del acta (lectura de Polaris, borrador), CNV por
     célula solo en el hueso (score de ventana log2 obs/esp del tribunal frente a la referencia con GEM
     limpio, INFERIDO de la expresión; null sin GEM limpio), pseudobulk 8p11 del hígado. En la web, además
     (red-team del 17-oct): sondas [unión, total] por gen, ADN genómico de la muestra (cotejado con
     tribunal/adn.json), genes que participan en la selección del grupo (circularidad) y su cifra LOO, y
     el CHECK DE REPRODUCCIÓN del acta, que para el export si no cuadra.
  6. explorador.h5ad desde cero + web/{manifiesto,higado,hueso}.json + INFORME_LIMPIEZA.{json,md}.
  7. Validación fail-closed de TODO (árbol HDF5, JSON, markdown; puerta_n1). Si falla, se borran las
     salidas de esta ejecución y rc ≠ 0.

Muro: stdout solo agregados (ni barcodes, ni ids de bloque, ni nombre del pool). Egress 0.
Semilla única: la del cierre (20261016). Hilos a 1 para que el UMAP sea reproducible.
"""
import os
import sys

if not sys.flags.isolated:
    os.execv(sys.executable, [sys.executable, "-I", os.path.abspath(__file__)] + sys.argv[1:])

for _v in ("NUMBA_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[_v] = "1"
os.environ.setdefault("MPLBACKEND", "Agg")

import argparse  # noqa: E402
import gc  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.dirname(AQUI)))
HOME = os.path.expanduser("~")
sys.path.insert(0, AQUI)
import _contrato as C  # noqa: E402
import _suelo_todos as ST  # noqa: E402  («Loupe público»: suelo y referencia de TODOS los genes)

sys.path.insert(0, os.path.join(AQUI, "..", "cierre"))
import _c  # noqa: E402  (trae tribunal/_comun como _c.TC y pone tribunal/ en sys.path)

TC = _c.TC
import _suelo  # noqa: E402  (tribunal: INMUNES, TRAZ, pesos_umi)

sys.path.insert(0, os.path.join(AQUI, "..", "higado_dianas"))
import _h as HD  # noqa: E402  (REGLAS_HIGADO_v1: panel de identidad y marcadores de exclusión de la vía 1)

SEED = _c.SEED
OUT_BASE = os.path.join(TC.FLEX, "explorador")
OUT = OUT_BASE                      # modo «spliced»: OUT_BASE/solo_spliced (se fija en main, tras leer --sondas)
HIGADO_DIANAS = os.path.join(TC.FLEX, "higado_dianas", "conjuntos_higado_dianas.npz")
HIGADO_DIANAS_JSON = os.path.join(TC.FLEX, "higado_dianas", "higado_dianas.json")   # vía 1 por gen (LOO)
ADN_JSON = os.path.join(TC.OUT, "adn.json")                                          # tribunal: ADN genómico
# Quién participa en la selección del grupo de interés (B1: su cifra en el grupo es circular). Sale de las
# reglas congeladas, no se escribe a mano: hígado = vía 1 de REGLAS_HIGADO_v1; hueso = EPI4 del tribunal.
ROLES_SELECCION = {
    "higado": {"identidad": list(HD.PANEL), "fgfr1_alto": ["FGFR1"],
               "exclusion": list(HD.HEP) + list(HD.INMUNE) + list(HD.ENDO) + list(HD.ESTROMA),
               # LIGADOS_ER de las reglas + la circularidad de programa que declara el acta (l.117: NY-BR-1)
               "ligado_er": sorted(set(HD.LIGADOS_ER) | set(C.CIRCULAR_PROGRAMA_HIGADO))},
    "hueso": {"identidad": list(TC.EPI4)},
}
# Referencia emparejada del grupo de interés: hígado R1 de higado_dianas (NK + mieloide + endotelio, lo
# que lleva la columna «referencia» de la tabla del acta); hueso REF_TIPOS con GEM limpio (tribunal/cierre).
REF_EMPAREJADA = {"higado": ["NK", "mieloide", "endotelio"], "hueso": list(TC.REF_TIPOS)}
UMAP_PARAMS = {"normalizacion": "normalize_total(target_sum=1e4) + log1p sobre X",
               "genes": "2000 muy variables (flavor seurat)", "escalado": "scale(max_value=10)",
               "pca": "50 componentes, arpack, random_state = semilla", "vecinos": "15, sobre 50 PCs, random_state = semilla",
               "umap": "min_dist 0.5, random_state = semilla, hilos a 1"}
MODO = "todas"
PROHIBIDAS = set()
ESCRITOS = []
ZINFO = {}                          # resumen de los Zarr por lesión, para el informe
T0 = time.time()


def avisos():
    base = [
        "Apoyo a la decisión. No es diagnóstico. Requiere validación por oncología molecular.",
        "ARN no es proteína: la IHQ manda.",
        "n = 1, FFPE, exploratorio.",
        "Hígado: tumor frente a hepatocito NO resuelto célula a célula; luminal_alta_confianza (993) es un "
        "subconjunto de alta confianza, no un recuento de células tumorales.",
        "IHQ HER2 0 (el ARN de ERBB2 no equivale a HER2-low).",
        "CNV por célula solo en el hueso (score de ventana del tribunal; null en células sin GEM limpio). En el "
        "hígado, solo pseudobulk de 8p11.",
    ]
    if MODO == "todas":
        base.append("Matriz estándar de Cell Ranger, todas las sondas (la misma base del acta); en el hueso incluye ADN "
                    "genómico: las sondas que caen dentro de un exón también hibridan con el ADN genómico del FFPE, y el "
                    "suelo ambiental del hueso lleva ese término.")
    else:
        base.append("X = cuentas de las sondas que cruzan una unión exón-exón (solo 7.497 de 53.519 en este probe set): "
                    "la mayoría de los genes del panel quedan sin señal. Export literal, no recomendado.")
    # B5 y B4 del red-team (17-oct): se añaden, los siete de arriba se mantienen
    base.append("Nivel por gen: mediana por célula detectada (CP10k); el acta da pseudobulk, así que no tienen por qué coincidir.")
    base.append("Veredicto por gen: %s." % C.ETIQUETA_VEREDICTO)
    return base


# ── Muro ───────────────────────────────────────────────────────────────────────────────────────
def _politica():
    for ruta in (os.path.join(RAIZ_REPO, ".claude", "hooks", "zonas_clinicas.py"),
                 os.path.join(HOME, "claudecode", ".claude", "hooks", "zonas_clinicas.py")):
        if os.path.exists(ruta):
            spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise RuntimeError("no encuentro zonas_clinicas.py")


def preparar():
    """Fail-closed: solo por ventanilla; entrada, cachés y salida tienen que ser zona clínica."""
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("RECHAZADO: flex_explorador solo corre por la ventanilla "
              "(python3 tools/lector_clinico.py procesa flex_explorador -- …).", file=sys.stderr)
        sys.exit(2)
    zc = _politica()
    for r in (OUT, TC.BASE, _c.CACHE, os.path.dirname(HIGADO_DIANAS), TC.OUT):
        if not zc.es_ruta_clinica(r):
            print("RECHAZADO: una entrada o la salida no es zona clínica.", file=sys.stderr)
            sys.exit(2)
    os.makedirs(os.path.join(OUT, "web"), exist_ok=True)
    return zc


def di(*partes):
    """stdout con última red: nada que el contrato prohíba sale por aquí."""
    s = " ".join(str(p) for p in partes)
    m = C.cadena_prohibida(s, PROHIBIDAS)
    if m:
        raise RuntimeError("MURO: stdout iba a llevar %s" % m)
    print(s, flush=True)


def _escribe_json(ruta, obj):
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    ESCRITOS.append(ruta)
    return ruta


def _escribe_texto(ruta, texto):
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(texto)
    ESCRITOS.append(ruta)
    return ruta


def _r(x, nd=4):
    if x is None:
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def _sig3(x):
    return float("%.3g" % x)


# ── U3: config.csv ─────────────────────────────────────────────────────────────────────────────
def leer_config():
    """config.csv del pool, en memoria. Devuelve ({tejido: id de muestra}, resumen agregado, prohibidas)."""
    with open(os.path.join(TC.BASE, "MP", "outs", "config.csv"), encoding="utf-8") as fh:
        txt = fh.read()
    secciones, actual = {}, None
    for line in txt.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            actual = s[1:-1].strip().lower()
            secciones[actual] = []
            continue
        if actual is not None:
            secciones[actual].append([x.strip() for x in s.split(",")])
    filas = secciones.get("samples", [])[1:]
    paciente, ajenas, pbcs = {}, [], []
    for f in filas:
        if len(f) < 2:
            continue
        sid, pbc = f[0], f[1]
        pbcs.append(pbc)
        if pbc in TC.PROBE_BC_TEJIDO:
            paciente[TC.PROBE_BC_TEJIDO[pbc]] = sid
        else:
            ajenas.append(sid)
    prohib = set()
    for sid in list(paciente.values()) + ajenas:
        prohib |= C.formas_laxas(sid)
    for p in pbcs:
        prohib.add(p.lower())
    pool = set()
    for f in secciones.get("libraries", [])[1:]:
        for x in f[:2]:
            if x:
                pool.add(x)
                pool.add(os.path.basename(x.rstrip("/")))
    for p in pool:
        if len(p) >= 4:
            prohib.add(p.lower())
        prohib |= C.formas_laxas(p)
    resumen = {"n_filas_samples": len(filas), "n_muestras_paciente": len(paciente), "n_muestras_ajenas": len(ajenas),
               "tejidos_resueltos": sorted(paciente), "secciones": sorted(secciones),
               "n_libraries": max(0, len(secciones.get("libraries", [])) - 1),
               "n_formas_prohibidas_en_memoria": len(prohib)}
    try:
        resumen["coincide_con_tribunal_comun_muestras"] = bool(TC.muestras() == paciente)
    except Exception as e:  # con muestras ajenas, _comun.muestras() revienta: se registra, no se oculta
        resumen["coincide_con_tribunal_comun_muestras"] = False
        resumen["tribunal_comun_muestras_error"] = type(e).__name__
    try:
        ficheros = os.listdir(TC.PER_SAMPLE)
        resumen["n_ficheros_per_sample"] = len(ficheros)
        # el id puede ir en medio del nombre (molecule_info_<id>_M#.h5), no solo al principio (C2 del red-team)
        resumen["n_ficheros_per_sample_de_la_paciente"] = sum(any(s in f for s in paciente.values()) for f in ficheros)
    except OSError:
        resumen["n_ficheros_per_sample"] = None
    if set(paciente) != set(TC.TEJIDOS):
        raise RuntimeError("U3: no se resuelven las dos muestras de la paciente desde config.csv")
    return paciente, resumen, prohib


def contexto_acta():
    """Entradas de la zona clínica que el export solo LEE: tribunal/adn.json (ADN genómico) y la vía 1 de
    higado_dianas.json (cifras LOO del acta)."""
    with open(ADN_JSON, encoding="utf-8") as fh:
        adn = json.load(fh)
    with open(HIGADO_DIANAS_JSON, encoding="utf-8") as fh:
        via1 = json.load(fh)["via1"]["por_gen"]
    return {"adn": adn, "via1": via1}


def pool_crudo_ruido():
    """C3 (higiene, solo se ANOTA): la matriz cruda del POOL (MP/outs/raw_feature_bc_matrix.h5), que el export
    no usa, lleva barcodes bajo probe barcodes de otras muestras de la corrida. Cuenta, sin escribir ni un
    sufijo ni un barcode: sufijos de la paciente = los de la matriz filtrada del pool. Nada se mueve ni se borra."""
    import h5py
    outs = os.path.join(TC.BASE, "MP", "outs")
    with h5py.File(os.path.join(outs, "filtered_feature_bc_matrix.h5"), "r") as f:
        suyos = {b.decode()[16:] for b in f["matrix/barcodes"][:]}
    with h5py.File(os.path.join(outs, "raw_feature_bc_matrix.h5"), "r") as f:
        suf = np.array([b.decode()[16:] for b in f["matrix/barcodes"][:]])
        indptr = f["matrix/indptr"][:]
        data = f["matrix/data"]
        tot = np.zeros(len(indptr) - 1, dtype=np.int64)
        for s0 in range(0, data.shape[0], 8_000_000):
            d = data[s0:s0 + 8_000_000].astype(np.int64)
            col = np.searchsorted(indptr, np.arange(s0, s0 + len(d)), side="right") - 1
            tot += np.bincount(col, weights=d, minlength=len(tot)).astype(np.int64)
    ajeno = ~np.isin(suf, list(suyos))
    return {"n_barcodes": int(len(suf)), "n_sufijos": int(len(set(suf.tolist()))), "n_sufijos_de_la_paciente": len(suyos),
            "n_sufijos_ajenos": int(len(set(suf[ajeno].tolist()))), "n_barcodes_ajenos": int(ajeno.sum()),
            "umi_ajenas": int(tot[ajeno].sum()), "frac_umi_ajenas": _r(tot[ajeno].sum() / max(tot.sum(), 1), 6),
            "max_umi_por_barcode_ajeno": int(tot[ajeno].max()) if ajeno.any() else 0,
            "n_barcodes_ajenos_con_100_umi_o_mas": int((tot[ajeno] >= 100).sum()),
            "usado_por_el_export": False, "accion": "ninguna: solo se anota (no se mueve ni se borra nada sin el OK de la titular)"}


# ── Matrices ───────────────────────────────────────────────────────────────────────────────────
def sondas_incluidas():
    ps = TC.probe_set()
    ps = ps[ps["included"]].copy()
    ps["region"] = ps["region"].astype(str)
    return ps


def matrices_sondas(t, bcs, ps, gene_ids):
    """cells × genes desde la matriz de sondas: S (sondas de unión exón-exón, region == spliced) y U (las
    demás). Devuelve también el nº de sondas de unión por gen."""
    import h5py
    import scipy.sparse as sp
    path = TC.ruta(t, "_raw_probe_bc_matrix.h5")
    with h5py.File(path, "r") as f:
        fid = np.array([x.decode() for x in f["matrix/features/id"][:]])
    filas = np.where(np.isin(fid, ps["probe_id"].values))[0]
    M, ids, _ = TC.stream_h5(path, bcs, filas=filas)
    M = M.tocsc()
    reg = dict(zip(ps["probe_id"], ps["region"]))
    gid = dict(zip(ps["probe_id"], ps["gene_id"]))
    col = {g: j for j, g in enumerate(gene_ids)}
    out = {}
    nsp = np.zeros(len(gene_ids), dtype=np.int32)
    for region in ("spliced", "resto"):
        sel = np.array([(reg[p] == "spliced") == (region == "spliced") for p in ids])
        rows = np.where(sel)[0]
        cols = np.array([col.get(gid[ids[i]], -1) for i in rows], dtype=np.int64)
        ok = cols >= 0
        P = sp.csr_matrix((np.ones(int(ok.sum()), dtype=np.int32), (rows[ok], cols[ok])),
                          shape=(len(ids), len(gene_ids)))
        out[region] = (M @ P).tocsr().astype(np.int32)
        out[region].sort_indices()
        if region == "spliced":
            nsp = np.bincount(cols[ok], minlength=len(gene_ids)).astype(np.int32)
        out["n_sondas_" + region] = int(ok.sum())
    del M
    gc.collect()
    return out["spliced"], out["resto"], nsp, out["n_sondas_spliced"], out["n_sondas_resto"]


def ambiente_spliced(t, ps, gene_ids):
    """Perfil de ambiente solo con sondas de unión: barcodes raw NO llamados con 10 <= UMI total <= 100
    (la misma selección que _suelo.ambiente, sobre la matriz raw de genes)."""
    import h5py
    bc, tt = TC.barcodes_raw(t)
    llam = set(TC.llamadas(t))
    sel = np.array([b for b, x in zip(bc, tt) if 10 <= x <= 100 and b not in llam])
    del bc, tt
    gc.collect()
    path = TC.ruta(t, "_raw_probe_bc_matrix.h5")
    with h5py.File(path, "r") as f:
        fid = np.array([x.decode() for x in f["matrix/features/id"][:]])
    sps = ps[ps["region"] == "spliced"]
    filas = np.where(np.isin(fid, sps["probe_id"].values))[0]
    v, ids, _ = TC.stream_h5(path, sel, filas=filas, modo="suma")
    gid = dict(zip(sps["probe_id"], sps["gene_id"]))
    col = {g: j for j, g in enumerate(gene_ids)}
    s = np.zeros(len(gene_ids), dtype=np.float64)
    for p, val in zip(ids, v):
        j = col.get(gid.get(p))
        if j is not None:
            s[j] += val
    return s / max(s.sum(), 1.0), int(len(sel)), float(s.sum())


def umap_lesion(X, gene_ids):
    import anndata as an
    import scanpy as sc
    a = an.AnnData(X=X.astype(np.float32).copy())
    a.var_names = [str(g) for g in gene_ids]
    sc.pp.normalize_total(a, target_sum=1e4)
    sc.pp.log1p(a)
    sc.pp.highly_variable_genes(a, n_top_genes=2000, flavor="seurat")
    a = a[:, a.var["highly_variable"].values].copy()
    sc.pp.scale(a, max_value=10)
    sc.tl.pca(a, n_comps=50, svd_solver="arpack", random_state=SEED)
    sc.pp.neighbors(a, n_neighbors=15, n_pcs=50, random_state=SEED)
    sc.tl.umap(a, random_state=SEED, min_dist=0.5)
    um = np.asarray(a.obsm["X_umap"], dtype=np.float64).copy()
    del a
    gc.collect()
    return um


def score_ventana(X, gene_ids, genes_ens, ref):
    """Lectura del tribunal (tribunal/_comun.score_ventana): log2((obs + 0,5) / (esp + 0,5)),
    esp = UMI_i × fracción de la ventana en el pseudobulk de la referencia."""
    idx = np.where(np.isin(gene_ids, list(genes_ens)))[0]
    obs = np.asarray(X[:, idx].sum(axis=1)).ravel().astype(float)
    umi = np.asarray(X.sum(axis=1)).ravel().astype(float)
    f = obs[ref].sum() / max(umi[ref].sum(), 1.0)
    return np.log2((obs + 0.5) / (umi * f + 0.5)), len(idx)


def perfil_bins(X, gene_ids, mt, mr, cv, bin_bp=10e6):
    """Como tribunal/_clon.perfil: log2 CP10k tumor/ref (+0,05), mediana por bin de 10 Mb (>= 5 genes),
    centrado por la mediana; genes con CP10k pseudobulk >= 0,5 en la lesión."""
    import pandas as pd
    tot = np.asarray(X.sum(axis=0)).ravel().astype(float)
    cp = tot / max(tot.sum(), 1.0) * 1e4
    ok = cp >= 0.5
    ct = np.asarray(X[mt].sum(axis=0)).ravel() / max(X[mt].sum(), 1.0) * 1e4
    cr = np.asarray(X[mr].sum(axis=0)).ravel() / max(X[mr].sum(), 1.0) * 1e4
    lr = np.log2((ct + 0.05) / (cr + 0.05))
    v = cv.reindex(gene_ids)
    ok = ok & v["chromosome"].notna().values & v["start"].notna().values
    d = pd.DataFrame({"chr": v["chromosome"].values[ok].astype(str), "bin": (v["start"].values[ok] // bin_bp).astype(int),
                      "lr": lr[ok]})
    d = d[~d["chr"].isin(["chrX", "chrY", "chrM"])]
    p = d.groupby(["chr", "bin"])["lr"].agg(["median", "size"])
    p = p[p["size"] >= 5]["median"]
    return p - p.median(), int(ok.sum())


def perfil_dict(P):
    """Serie (chr, bin de 10 Mb) → listas del contrato; solo autosomas chr1-chr22."""
    filas = [(c, b, v) for (c, b), v in P.items() if C.RE_CROMOSOMA.match(str(c)) and np.isfinite(v)]
    return {"cromosoma": [str(c) for c, _, _ in filas], "inicio_mb": [float(b * 10) for _, b, _ in filas],
            "log2_rel": [float(np.round(v, 3)) for _, _, v in filas]}


def marcadores_lesion(X, umi, grupo, g_ids, simb, excl, todos):
    """Marcadores por grupo del contrato (cada grupo frente al resto de la lesión): Wilcoxon de scanpy sobre
    log1p(CP10k), FDR de Benjamini-Hochberg, MARCADORES_N por grupo, con el suelo del gen en ese grupo al lado.
    Solo grupos del contrato y genes con sonda; nunca sobre selecciones libres (comité médico, 18-oct)."""
    import anndata as an
    import pandas as pd
    import scanpy as sc
    import scipy.sparse as sp
    keep = ~np.asarray(excl, bool)
    nombres = np.asarray(an.utils.make_index_unique(pd.Index(np.asarray(simb).astype(str))))
    Xn = (sp.diags(1e4 / np.asarray(umi, float)) @ sp.csr_matrix(X, dtype=np.float64)).tocsr()[:, keep]
    Xn.data = np.log1p(Xn.data)
    grupo = np.asarray(grupo).astype(str)
    presentes = [g for g in g_ids if (grupo == g).sum() >= 2]
    a = an.AnnData(X=Xn, obs=pd.DataFrame({"g": pd.Categorical(grupo, categories=g_ids)}, index=[str(i) for i in range(len(grupo))]),
                   var=pd.DataFrame(index=nombres[keep]))
    sc.tl.rank_genes_groups(a, "g", groups=presentes, reference="rest", method="wilcoxon", n_genes=C.MARCADORES_N,
                            pts=True, use_raw=False)
    r = a.uns["rank_genes_groups"]
    pos = {s: i for i, s in enumerate(nombres)}
    col = {g: k for k, g in enumerate(todos["grupos"])}
    out = {}
    for g in presentes:
        lfc = np.asarray(r["logfoldchanges"][g], float)
        fin = np.isfinite(lfc)                  # un logFC infinito (cero en un lado) no se enseña como cifra
        genes = [str(x) for x, ok in zip(r["names"][g], fin) if ok]
        out[g] = {"gen": genes,
                  "logfc": [float(np.round(x, 4)) for x in lfc[fin]],
                  "fdr": [float(min(max(x, 0.0), 1.0)) for x in np.asarray(r["pvals_adj"][g], float)[fin]],
                  "frac_grupo": [float(np.round(x, 4)) for x in r["pts"].loc[genes, g]],
                  "frac_resto": [float(np.round(x, 4)) for x in r["pts_rest"].loc[genes, g]],
                  "suelo": [float(todos["suelo"][pos[x], col[g]]) for x in genes]}
    return out


# ── Una lesión ─────────────────────────────────────────────────────────────────────────────────
def procesa_lesion(t, ps, cv, ctx):
    import scipy.sparse as sp
    L = {"lesion": t}
    ad = TC.carga(t)                                   # QC común, orden de las cachés, índice = barcode
    bcs = ad.obs_names.values.astype(str)
    gene_ids = ad.var_names.values.astype(str)
    simb = ad.var["simbolo"].values.astype(str)
    Xf = ad.X.tocsr().astype(np.int32)
    Xf.sort_indices()
    # cachés del cierre: alineación célula a célula (UMI y tipo exactos) antes de olvidar el barcode
    Xc = sp.load_npz(_c.cachep("X_%s.npz" % t)).tocsr()
    o = np.load(_c.cachep("obs_%s.npz" % t), allow_pickle=True)
    o = {k: o[k] for k in o.files}
    umi_f = np.asarray(Xf.sum(axis=1)).ravel().astype(np.int64)
    ali = {"n": int(len(bcs)), "n_cache": int(Xc.shape[0]),
           "umi_iguales": bool(Xc.shape == Xf.shape and (umi_f == o["umi"]).all()),
           "tipo_iguales": bool(len(o["tipo"]) == len(bcs) and (o["tipo"].astype(str) == ad.obs["tipo"].values.astype(str)).all()),
           "gene_ids_iguales": bool((o["gene_ids"].astype(str) == gene_ids).all()),
           "matriz_cache_igual_a_filtrada": bool(Xc.shape == Xf.shape and (Xc != Xf).nnz == 0)}
    if not all(v for k, v in ali.items() if k.endswith("iguales") or k.startswith("matriz")):
        raise RuntimeError("alineación con las cachés del cierre rota en %s: %s" % (t, ali))
    del Xc
    gc.collect()
    tipo = ad.obs["tipo"].values.astype(str)
    del ad
    gc.collect()
    L.update(n=len(bcs), gene_ids=gene_ids, simbolo=simb, tipo=tipo, alineacion=ali)

    # sondas de unión (S) y resto (U): siempre, para medir el delta; X según el modo
    S, U, nsp, n_s, n_u = matrices_sondas(t, bcs, ps, gene_ids)
    suma = (S + U).tocsr()
    suma.sort_indices()
    ali["celdas_distintas_S_mas_U_frente_a_filtrada"] = int((suma != Xf).nnz)
    ali["umi_filtrada_millones"] = _r(Xf.sum() / 1e6, 3)
    ali["umi_S_mas_U_millones"] = _r(suma.sum() / 1e6, 3)
    del suma
    umi_s = np.asarray(S.sum(axis=1)).ravel().astype(np.int64)
    ns_tot = o["nsondas"].astype(int)
    L.update(n_sondas={"union_exon_exon": n_s, "resto": n_u}, genes_sin_sonda_union=int((nsp == 0).sum()),
             n_sondas_union_gen=nsp)

    # grupos
    if t == "higado":
        z = np.load(HIGADO_DIANAS)
        interes = z["primario_alta_confianza"].astype(bool)
        limpio = np.ones(len(bcs), bool)
    else:
        interes = o["trib_maligna_T_limpio_T"].astype(bool)
        limpio = o["trib_limpio_T"].astype(bool)
    assert len(interes) == len(bcs)
    grupo = C.grupos_lesion(t, tipo, interes, limpio)
    tipo_c = C.tipo_canonico(tipo)
    L.update(interes=interes, limpio=limpio, grupo=grupo, tipo_c=tipo_c)

    # delta: qué daría una matriz solo de sondas de unión, frente a la filtrada y frente al acta
    tot_f, tot_s = float(Xf.sum()), float(S.sum())
    delta = {"umi_union_frac_del_total": _r(tot_s / tot_f, 4), "umi_perdida_total_frac_si_solo_union": _r(1 - tot_s / tot_f, 4), "panel": {}}
    for g in C.PANEL_10:
        j = np.where(simb == g)[0]
        if len(j) == 0:
            delta["panel"][g] = {"nota": "fuera del panel"}
            continue
        j = j[0]
        vf = np.asarray(Xf[:, j].todense()).ravel()
        vs = np.asarray(S[:, j].todense()).ravel()
        ff = float((vf[interes] > 0).mean())
        fs = float((vs[interes] > 0).mean())
        acta = C.ACTA_FRAC[t].get(g)
        delta["panel"][g] = {"n_sondas_union": int(nsp[j]), "n_sondas_total": int(ns_tot[j]),
                             "umi_perdida_frac_si_solo_union": _r(1 - vs.sum() / max(vf.sum(), 1), 4),
                             "frac_detectada_filtrada": _r(ff, 4), "frac_detectada_solo_union": _r(fs, 4),
                             "delta_puntos_union_menos_filtrada": _r(100 * (fs - ff), 2),
                             "frac_acta": acta,
                             "delta_puntos_filtrada_menos_acta": _r(100 * (ff - acta), 2) if acta is not None else None,
                             "delta_puntos_union_menos_acta": _r(100 * (fs - acta), 2) if acta is not None else None}
    L["delta"] = delta

    # X, UMI, ambiente y término de ADN según el modo
    if MODO == "spliced":
        X, umi = S, umi_s
        p_amb, n_vac, umi_vac = ambiente_spliced(t, ps, gene_ids)
        delta_adn = np.zeros(len(bcs))
        nu = np.zeros(len(gene_ids), dtype=int)
        excl_sin_sonda = nsp == 0
        nota_amb = "perfil de ambiente recalculado solo con sondas de unión; sin término de ADN"
    else:
        X, umi = Xf, umi_f
        p_amb = np.asarray(o["p_amb"], float)
        n_vac, umi_vac = int(o["n_vacios"]), float(o["umi_vacios"])
        nu = o["nsondas_unsp"].astype(int)
        delta_adn = np.zeros(len(bcs)) if t == "higado" else o["son_or_sum"].astype(float) / float(o["son_n_or"])
        excl_sin_sonda = ns_tot == 0
        nota_amb = "perfil de ambiente y término de ADN del cierre (cachés), misma fórmula que _final.tablas"
    del U, S
    gc.collect()
    if (umi == 0).any():
        raise RuntimeError("%s: %d células sin UMI en X" % (t, int((umi == 0).sum())))
    L["X"] = X

    # UMAP
    L["umap"] = umap_lesion(X, gene_ids)

    # rho con el trazador NO tumoral (definición única del cierre)
    inm = np.isin(tipo, _suelo.INMUNES) & limpio
    traz = np.isin(simb, _suelo.TRAZ[t])
    rho = float(X[inm][:, traz].sum() / max(umi[inm].sum(), 1) / max(p_amb[traz].sum(), 1e-12))
    L["ambiente"] = {"modo_sondas": MODO, "nota": nota_amb, "n_barcodes_vacios": n_vac, "umi_vacios_millones": _r(umi_vac / 1e6, 4),
                     "trazador": list(_suelo.TRAZ[t]), "rho": _r(rho, 4), "n_inmunes_rho": int(inm.sum()),
                     "p_trazador_ambiente": _r(p_amb[traz].sum(), 5),
                     "delta_adn_mediana_por_sonda": _r(np.median(delta_adn), 6) if t == "hueso" else 0.0}
    # referencia emparejada del grupo de interés
    ref = np.isin(tipo, REF_EMPAREJADA[t]) & limpio
    w = _suelo.pesos_umi(umi[interes].astype(float), umi[ref].astype(float))
    L["ref_emparejada"] = {"tipos": REF_EMPAREJADA[t], "n": int(ref.sum())}

    # genes del contrato web presentes
    genes, excluidos = [], {}
    for g in C.GENES_WEB:
        j = np.where(simb == g)[0]
        if len(j) == 0:
            excluidos[g] = "fuera del panel"
        elif excl_sin_sonda[j[0]]:
            excluidos[g] = "sin sonda en este modo"
        else:
            genes.append(g)
    L.update(genes=genes, genes_excluidos=excluidos,
             sondas_union_genes_web={g: [int(nsp[np.where(simb == g)[0][0]]), int(ns_tot[np.where(simb == g)[0][0]])] for g in genes})

    # B1 · circularidad: quién participa en la selección del grupo de interés y, si el acta lo midió sin
    # esa circularidad (LOO de higado_dianas, vía 1), esa cifra. Solo en el modo «todas» (es la base del acta).
    sel = C.seleccion_lesion(genes, ROLES_SELECCION[t])
    loo = {}
    if t == "higado" and MODO == "todas":
        for g in sel:
            f = ctx["via1"].get(g) or {}
            if f.get("LOO") is True:
                loo[g] = {"n": int(f["n"]), "frac": _r(f.get("frac"), 4), "ref": _r(f.get("ref_R1"), 4),
                          "cp10k_pseudobulk": _r(f.get("cp10k_pseudobulk"), 1)}
    # B1 · CHECK DE REPRODUCCIÓN del acta (fail-closed en el modo «todas»): fracción detectada en el grupo
    # de interés de cada gen con cifra en el acta; los del ACTA_LOO, contra su LOO.
    frac_grupo = {}
    for g in C.ACTA_FRAC[t]:
        j = np.where(simb == g)[0]
        if len(j):
            frac_grupo[g] = float((np.asarray(X[:, j[0]].todense()).ravel()[interes] > 0).mean())
    repro, no_cuadran = C.reproduce_acta(t, frac_grupo, loo)
    L.update(seleccion=sel, loo=loo, reproduccion_acta={"genes": repro, "no_cuadran": no_cuadran,
                                                        "exigido": MODO == "todas"})
    if MODO == "todas" and no_cuadran:
        raise RuntimeError("%s: el export no reproduce el acta en %s (check de reproducción)" % (t, ", ".join(no_cuadran)))

    # B2 · ADN genómico: fracción de UMI (no de lecturas) estimada con la fórmula del tribunal (_adn.py: delta medio ×
    # nº de sondas dentro de exón / UMI medio), en toda la lesión (cotejo con adn.json) y en el grupo de interés.
    if MODO == "todas":
        aj = ctx["adn"]
        if int(aj["nulos"]["n_sondas_unspliced_incluidas_panel"]) != n_u:
            raise RuntimeError("%s: nº de sondas dentro de exón distinto del de tribunal/adn.json" % t)
        dl = o["son_or_sum"].astype(float) / float(o["son_n_or"])
        f_todas = float(dl.mean() * n_u / umi_f.mean())
        f_int = float(dl[interes].mean() * n_u / umi_f[interes].mean())
        f_trib = float(aj[t]["frac_UMI_ADN_estimada"])
        if abs(f_todas - f_trib) > 0.02 * f_trib:
            raise RuntimeError("%s: la fracción de ADN recalculada no cuadra con tribunal/adn.json" % t)
        L["adn"] = {"sondas_dentro_de_exon": int(n_u), "sondas_panel": int(n_s + n_u),
                    "frac_lecturas_estimada": _r(f_todas, 5), "frac_lecturas_cell_ranger": _r(float(aj["cell_ranger_referencia"][t]), 5),
                    "frac_lecturas_interes": _r(f_int, 5)}
        L["adn_meta"] = {"frac_tribunal_adn_json": _r(f_trib, 5), "n_sondas_nulas_OR_cache": int(o["son_n_or"]),
                         "n_sondas_nulas_OR_adn_json": int(aj["nulos"]["n_sondas"]),
                         "frac_malignas_A_adn_json": _r(float(aj[t]["frac_UMI_ADN_estimada_malignas_A"]), 5),
                         "nota": "frac_lecturas_interes es del grupo de interés de este export (993 / 68), no de las malignas de A (adn.json)"}
        # 18-oct: la fórmula del tribunal es un cociente de MEDIAS; célula a célula da >= 1 en células con pocas UMI
        # (una lectura en una sonda nula × ~46.000 sondas), así que se exporta por GRUPO y se cuenta el diagnóstico.
        fc = dl * n_u / umi_f
        L["adn_celula_diag"] = {"n_celulas": int(len(fc)), "n_estimacion_ge_1": int((fc >= 1).sum())}
        L["_adn_dl"] = (dl, int(n_u), umi_f)
    else:
        L["adn"], L["adn_meta"] = None, {"nota": "modo spliced: X no lleva sondas dentro de exón; no aplica"}
        L["adn_celula_diag"], L["_adn_dl"] = None, None
    g_ids = [C.GRUPOS[t]["interes"]] + [r for r in C.GRUPOS[t]["referencia"] if (grupo == r).any()] + [C.GRUPOS[t]["otras"]]
    L["grupos_web"] = g_ids
    L["adn_por_grupo"] = {}
    if L["_adn_dl"] is not None:            # misma forma que el tribunal: cociente de medias, por grupo
        dl_, n_u_, umi_ = L.pop("_adn_dl")
        L["adn_por_grupo"] = {g: float(dl_[grupo == g].mean() * n_u_ / umi_[grupo == g].mean())
                              for g in g_ids if (grupo == g).any()}
        di("%s: ADN por célula (no se exporta): %d de %d células con estimación >= 1"
           % (t, L["adn_celula_diag"]["n_estimacion_ge_1"], L["adn_celula_diag"]["n_celulas"]))
    else:
        L.pop("_adn_dl", None)
    expr, suelo, refd = {}, {}, {}
    for g in genes:
        j = int(np.where(simb == g)[0][0])
        v = np.asarray(X[:, j].todense()).ravel().astype(float)
        nz = np.where(v > 0)[0]
        expr[g] = {"i": [int(i) for i in nz], "v": [_sig3(x) for x in (v[nz] / umi[nz] * 1e4)]}
        suelo[g] = {}
        for gid_ in g_ids:
            m = grupo == gid_
            lam = p_amb[j] * rho * umi[m] + delta_adn[m] * nu[j]
            suelo[g][gid_] = _r(np.mean(1 - np.exp(-lam)), 3) if m.any() else 0.0
        refd[g] = _r(np.sum(w * (v[ref] > 0)) / max(w.sum(), 1e-12), 4)
    L.update(expr=expr, suelo=suelo, referencia=refd, veredicto=C.veredictos_lesion(t, genes))
    # «Loupe público» (plan aprobado por la titular, 17-oct): suelo y referencia de TODOS los genes con la misma
    # fórmula. Fail-closed: tiene que reproducir exactamente los de los genes de la web y no dejar ningún gen
    # con sonda sin suelo (condición del comité médico para abrir los 18.132 genes).
    todos = ST.calcula(X, p_amb, rho, umi, delta_adn, nu, grupo, g_ids, ref, w, excl_sin_sonda)
    malos = ST.coincide_con_web(todos, [str(s) for s in simb], suelo, refd)
    if malos:
        raise RuntimeError("%s: el suelo de todos los genes no reproduce el de la web (%d discrepancias)" % (t, len(malos)))
    ST.valida_completo(todos, len(simb))
    L.update(suelo_todos=todos, umi=umi, n_sondas_gen=ns_tot,
             marcadores=marcadores_lesion(X, umi, grupo, g_ids, simb, excl_sin_sonda, todos))

    # CNV
    if t == "hueso":
        refc = np.isin(tipo, TC.REF_TIPOS) & limpio
        vals, ng = [], {}
        for wv in C.CNV_VENTANAS:
            s, k = score_ventana(X, gene_ids, TC.genes_ventana(cv, wv), refc)
            vals.append([(_r(x, 3) if lim else None) for x, lim in zip(s, limpio)])    # null sin GEM limpio (como el tribunal)
            ng[wv] = k
        L["cnv"] = {"ventanas": list(C.CNV_VENTANAS), "metodo": C.CNV_METODOS[MODO], "valores": vals}
        arr = [np.array([np.nan if x is None else x for x in v], float) for v in vals]
        L["cnv_meta"] = {"n_referencia_gem_limpio": int(refc.sum()), "n_genes_ventana": ng, "n_celulas_con_valor": int(limpio.sum()),
                         "n_celulas_null_sin_gem_limpio": int((~limpio).sum()),
                         "mediana_interes": {wv: _r(np.nanmedian(arr[i][interes]), 3) for i, wv in enumerate(C.CNV_VENTANAS)},
                         "mediana_referencia": {wv: _r(np.nanmedian(arr[i][refc]), 3) for i, wv in enumerate(C.CNV_VENTANAS)},
                         "p99_referencia": {wv: _r(np.nanpercentile(arr[i][refc], 99), 3) for i, wv in enumerate(C.CNV_VENTANAS)}}
    else:
        P, n_ok = perfil_bins(X, gene_ids, interes, ref, cv)
        key = ("chr8", int(38.4e6 // 10e6))
        pct = int(round(100 * float((P < P.loc[key]).mean()))) if key in P.index else None
        L["cnv"] = {"pseudobulk": {"ventana": "8p11", "percentil_genoma": pct}}
        L["cnv_meta"] = {"n_bins": int(len(P)), "n_genes_usados": n_ok, "referencia": REF_EMPAREJADA[t],
                         "log2_rel_bin_8p11": _r(P.loc[key], 3) if key in P.index else None}
    # 18-oct: el perfil por bins entero, en las dos lesiones (interés frente a la referencia emparejada)
    L["perfil"] = perfil_dict(P if t == "higado" else perfil_bins(X, gene_ids, interes, ref, cv)[0])
    di("%s: n %d · interés %d · grupos %d · genes web %d (excluidos %d) · vacíos %d · rho %.3f · %.0f s"
       % (t, L["n"], int(interes.sum()), len(g_ids), len(genes), len(excluidos), n_vac, rho, time.time() - T0))
    return L


# ── Salidas ─────────────────────────────────────────────────────────────────────────────────────
def web_json(L):
    t = L["lesion"]
    g_ids = L["grupos_web"]
    gpos = {g: i for i, g in enumerate(g_ids)}
    rol = {C.GRUPOS[t]["interes"]: "interes", C.GRUPOS[t]["otras"]: "otras"}
    tipos = sorted(set(L["tipo_c"].astype(str)))
    tpos = {x: i for i, x in enumerate(tipos)}
    um = L["umap"]
    esc = []
    for k in range(2):
        lo, hi = float(um[:, k].min()), float(um[:, k].max())
        esc.append(((um[:, k] - lo) / max(hi - lo, 1e-12)) * 20 - 10)
    umap_plano = []
    for i in range(L["n"]):
        umap_plano.extend([round(float(esc[0][i]), 2), round(float(esc[1][i]), 2)])
    obj = {"version": 1, "lesion": t, "n_celulas": int(L["n"]),
           "grupos": [{"id": g, "rol": rol.get(g, "referencia"), "n": int((L["grupo"] == g).sum())} for g in g_ids],
           "tipos": tipos,
           "celulas": {"umap": umap_plano, "grupo": [gpos[g] for g in L["grupo"].astype(str)],
                       "tipo": [tpos[x] for x in L["tipo_c"].astype(str)]},
           "genes": list(L["genes"]), "expr": L["expr"], "suelo": L["suelo"], "referencia": L["referencia"],
           "veredicto": L["veredicto"], "sondas": {g: L["sondas_union_genes_web"][g] for g in L["genes"]},
           "seleccion": L["seleccion"], "loo": L["loo"], "adn": L["adn"], "cnv": L["cnv"], "qc": L["qc"]}
    return _escribe_json(os.path.join(OUT, "web", "%s.json" % t), obj)


def h5ad(Ls):
    import anndata as an
    import pandas as pd
    import scipy.sparse as sp
    Lh, Lb = Ls["higado"], Ls["hueso"]
    assert (Lh["gene_ids"] == Lb["gene_ids"]).all(), "las dos lesiones no comparten el panel"
    X = sp.vstack([Lh["X"], Lb["X"]]).tocsr().astype(np.int32)
    X.sort_indices()
    n_h, n_b = Lh["n"], Lb["n"]
    obs = C.construye_obs(np.array(["higado"] * n_h + ["hueso"] * n_b), np.concatenate([Lh["grupo"], Lb["grupo"]]),
                          np.concatenate([Lh["tipo_c"], Lb["tipo_c"]]), np.vstack([Lh["umap"], Lb["umap"]]))
    var = pd.DataFrame({"ensembl": Lh["gene_ids"].astype(str)}, index=pd.Index(Lh["simbolo"].astype(str)))
    dup = int(var.index.duplicated().sum())
    idx_hueso = obs.index[n_h:]
    cnv_h = pd.DataFrame({wv: np.array([np.nan if x is None else x for x in Lb["cnv"]["valores"][i]], dtype=np.float64)
                          for i, wv in enumerate(C.CNV_VENTANAS)}, index=idx_hueso)
    uns = C.construye_uns(suelo={t: Ls[t]["suelo"] for t in C.LESIONES},
                          referencia={t: Ls[t]["referencia"] for t in C.LESIONES},
                          veredicto={t: Ls[t]["veredicto"] for t in C.LESIONES},
                          cnv_hueso=cnv_h, cnv_higado_pseudobulk=dict(Lh["cnv"]["pseudobulk"]), avisos=avisos(),
                          version=C.version_contrato(MODO))
    a = an.AnnData(X=X, obs=obs, var=var, uns=uns)
    a.var_names_make_unique()
    p = os.path.join(OUT, "explorador.h5ad")
    a.write_h5ad(p)
    ESCRITOS.append(p)
    return p, {"n_obs": int(a.n_obs), "n_var": int(a.n_vars), "simbolos_duplicados_renombrados": dup,
               "nnz_millones": _r(X.nnz / 1e6, 3), "modo_sondas": MODO}


def informe(Ls, resumen_u3, h5info, validadores, ruta_h5, pool):
    quitado = [
        "barcodes 10x reales -> índice secuencial c00000… (alineación célula a célula verificada antes contra las cachés del cierre: UMI, tipo, gene_ids y matriz idénticos)",
        "config.csv, qc_sample_metrics, web_summary, rutas y attrs de los h5 de 10x (library_ids, chemistry, gem_groups…): el h5ad nace desde cero",
        "identificadores de bloque de las dos muestras, probe barcodes y nombre del pool: leídos en memoria desde config.csv y usados solo como formas prohibidas del validador",
        "fechas, centro sanitario e identidad de la persona: nada de eso se escribe; el validador lo busca (patrones propios + puerta_n1)",
        "obs reducido a lesion, grupo, tipo_celular, umap_1, umap_2; var a símbolo + ensembl; uns a la allow-list; sin layers, raw, obsm, varm ni obsp",
        ("capa de sondas no-unión: NO existe como capa; en modo «todas» sus cuentas van sumadas dentro de X (como en Cell Ranger y en el acta)"
         if MODO == "todas" else "sondas no-unión: fuera de X y de toda salida (modo spliced literal)"),
    ]
    por_grupo = {t: {g: int((Ls[t]["grupo"] == g).sum()) for g in Ls[t]["grupos_web"]} for t in C.LESIONES}
    ver_fuente = {t: {g: {"veredicto": v[0], "linea_acta": v[1]} for g, v in C.VEREDICTO_ACTA[t].items() if g in Ls[t]["genes"]}
                  for t in C.LESIONES}
    sin_ver = {t: [g for g, v in Ls[t]["veredicto"].items() if v == "sin_veredicto"] for t in C.LESIONES}
    decisiones = [
        "SONDAS (hallazgo del diagnostico, decide el modo): en este probe set solo 7.497 de las 53.519 sondas included cruzan una unión "
        "exón-exón (region == spliced); las 46.022 restantes van dentro de un exón. 7 de los 10 genes del panel tienen 0 sondas de unión "
        "(TPBG, ERBB3, SLC39A6, ERBB2, TACSTD2, MUC1, FGFR1) y 56 de los 77 genes del contrato web quedarían sin señal. Una matriz "
        "solo-unión no es «la misma matriz más limpia»: es otro ensayo, más pobre y sin la mayoría de las dianas. "
        + ("Modo elegido: «todas» (X = cuentas de Cell Ranger, la base de todas las cifras del acta); el ADN genómico que capturan las "
           "sondas dentro de exón se trata como el cierre (término delta_ADN × nº de sondas no-unión en el suelo del hueso). El export "
           "literal solo-unión sigue disponible con --sondas spliced." if MODO == "todas" else
           "Modo elegido: «spliced» literal (X solo con sondas de unión), a sabiendas de que deja sin señal la mayoría de las dianas."),
        "Grupo de interés del hígado = primario_alta_confianza (993) de higado_dianas; nunca «tumor».",
        "Grupo de interés del hueso = maligna_T_limpio_T (68) del tribunal; referencias solo con GEM limpio (limpio_T).",
        "Referencia emparejada: hígado R1 (NK + mieloide + endotelio: la columna «referencia» de la tabla del acta); hueso REF_TIPOS con GEM limpio. Pesos por decil de UMI (pesos_umi del tribunal).",
        ("Suelo ambiental: definición única del cierre (REGLAS_CIERRE_v3 T5) con rho del trazador NO tumoral (hígado HEP5 en inmunes; hueso "
         "COL1A1+COL1A2+SPP1 en inmunes con GEM limpio), perfil de ambiente p_amb de las cachés del cierre (barcodes vacíos 10-100 UMI) y, en el "
         "hueso, el término de ADN delta_i × nº de sondas no-unión del gen (delta_i = UMI por sonda nula OR por célula, cachés). Sin diferencias "
         "con _final.tablas salvo que aquí se da por GRUPO (interés, cada referencia, otras) y no solo por conjunto."
         if MODO == "todas" else
         "Suelo ambiental: misma definición del cierre pero con p_amb recalculado solo con sondas de unión y sin término de ADN (no hay sonda intrónica en X)."),
        "CNV por célula en el hueso: score de ventana del tribunal (log2 obs/esp frente al pseudobulk de la referencia con GEM limpio; tribunal/_comun.score_ventana), "
        "INFERIDO DE LA EXPRESIÓN (ARN) frente a la referencia, no una lectura de ADN; método en la web = %s (B3: sin «_spliced» en el modo todas); " % C.CNV_METODOS[MODO] +
        "las malignas del hueso se llamaron con un inferCNV del genoma entero, así que su ventana alta no es una medida independiente de esa llamada; "
        "null en células sin GEM limpio como el score8p11_limpio_T de hueso_calls. Se elige frente a la lectura de ADN del cierre (t4), que vive en sondas no-unión "
        "y es una lectura de copias agregada, y frente al inferCNV propio, cuya salida por célula es una correlación con un consenso (no una ventana).",
        "Hígado: solo pseudobulk de 8p11 (log2 CP10k interés/R1 por bins de 10 Mb, mediana, centrado; percentil del bin en el genoma, como clon.json), con genes de CP10k >= 0,5 en el hígado.",
        "UMAP por lesión sobre X (parámetros en umap_parametros); coordenadas crudas en obs, reescaladas por eje a [-10, 10] en web/*.json.",
        "Veredicto por gen (%s): hígado desde la tabla «Dianas del HÍGADO» (columna Veredicto); hueso, por prevalencia, desde la fila «Dianas en el tumor "
        "del hueso (68 limpias)» del ESTADO FINAL (l.102), la fila «PRAME y MAGE-A4» (l.99: cerrado_por_arn) y las filas de la tabla del hígado con columna "
        "Hueso (68): ESR1 l.124, CHGA/SYP/{{DIANA3}} l.131, FGFR1/CCND1 l.132 y la fila «No» l.133 (hueso <= 6 %%). Lo que no está en esas fuentes: "
        "sin_veredicto («no valorado en esa fila»). HER3 del hígado (l.123, «SOSTENIDO con matiz») va como sostenido_con_matiz, distinto "
        "de con_matiz (l.124-127, sin «se sostiene»). Genes con veredicto en otra fila que el contrato no mapea: " % C.ETIQUETA_VEREDICTO
        + json.dumps(C.VEREDICTO_FUERA_DE_CONTRATO) + ". Filas con cifra en una lesión cuyo veredicto no se traslada a ella: "
        + json.dumps(C.VEREDICTO_NO_TRASLADADO, ensure_ascii=False),
        "CIRCULARIDAD (B1 del red-team): los genes que participan en la selección del grupo de interés van en `seleccion` (hígado: panel de identidad, "
        "FGFR1_alto, marcadores de exclusión y genes ligados a ER de REGLAS_HIGADO_v1 vía 1, más NY-BR-1/ANKRD30A como ligado_er por la circularidad de "
        "programa que declara el acta (l.117), sin LOO; hueso: EPI4 del tribunal) y la página los rotula. Si el acta "
        "midió el gen sin esa circularidad (LOO de higado_dianas, vía 1), esa cifra va en `loo` y es la que se enseña y la que se coteja con el acta.",
        "CHECK DE REPRODUCCIÓN (B1, fail-closed en el modo todas): cada cifra de ACTA_FRAC (tabla «Dianas del HÍGADO», columnas Hígado y Hueso, "
        "incluidos ESR1 y CHGA/SYP/{{DIANA3}}) frente a la fracción detectada en el grupo de interés del export, o frente al LOO para ESR1 del hígado; "
        "tolerancia = redondeo del acta. Si no cuadra, el export se para.",
        "ADN GENÓMICO (B2): `adn` en cada web/<lesión>.json con las sondas dentro de exón / del panel y la fracción de UMI (moléculas) de ADN estimada "
        "(fórmula de tribunal/_adn.py: delta medio × nº de sondas dentro de exón / UMI medio; recalculada y cotejada con tribunal/adn.json, tolerancia 2 %), "
        "la de Cell Ranger (adn.json, cell_ranger_referencia) y la del grupo de interés de este export. `sondas` = [unión, total] por gen.",
        "Tipos celulares: el `tipo` de las cachés (anotación de A) mapeado a ^[a-z_]+$ (T -> linfocito_t, NK -> nk, B -> linfocito_b).",
        "TODOS LOS GENES («Loupe público», plan aprobado por la titular el 17-oct; condición del comité médico): suelo ambiental por grupo y "
        "referencia emparejada para cada gen con sonda, con la MISMA fórmula que los genes de la web (_suelo_todos.py; fail-closed: reproduce "
        "exactamente los de la web y ningún gen con sonda queda sin suelo). Van en var de un AnnData-Zarr por lesión (zarr/<lesión>.zarr): "
        "X = CP10k a 3 cifras significativas, como `expr` de la web, en csc_matrix con trozos de %d elementos para que la página lea gen a gen "
        "(Vitessce 4.1.0 lee csc por columna; csr le obliga a recorrer la matriz entera). Vive FUERA de web/: publicarlo es un paso aparte "
        "(condiciones del laboratorio y decisión de la titular)." % C.ZARR_CHUNK,
        "LO QUE UN ingeniero NECESITA PARA NO MALINTERPRETAR (inventario del comité médico, plan aprobado por la titular el 18-oct): en el "
        "Zarr, UMI y nº de genes detectados por célula, fracción de ADN genómico POR GRUPO con la fórmula del tribunal (cociente de "
        "medias: delta medio × sondas dentro de exón / UMI medio; en las dos lesiones porque está cotejada con tribunal/adn.json en las "
        "dos; vacía en el modo spliced). POST HOC: el plan decía por célula, pero célula a célula esa fórmula da >= 1 en células con "
        "pocas UMI (cuántas: ver adn_celula_diag), así que no se exporta por célula. Sondas por gen (total y de unión), marcadores por grupo del contrato (Wilcoxon de scanpy sobre log1p CP10k frente al "
        "resto de la lesión, FDR BH, %d por grupo, con el suelo del gen al lado; los de logFC infinito no se enseñan) y el perfil por bins "
        "de 10 Mb de las dos lesiones (expresión, no ADN). En web/<lesión>.json, el QC agregado de A (el que fija las células). La "
        "probabilidad de doblete por célula NO se exporta: estas células ya son las que dejó A tras quitar los dobletes de Scrublet "
        "(el QC lo cuenta)." % C.MARCADORES_N,
        "Validador, excepciones declaradas: (1) solo en el manifiesto PUBLICADO (sello verificado + ok_titular true), el valor de fecha_ok, y solo si es "
        "AAAA-MM-DD de calendario, no cuenta como fecha y se enmascara en la copia que pasa por puerta_n1 (A4; la clave ok_titular ya no necesita excepción, C1); "
        "(2) el índice secuencial c00000…cNNNNN queda exento de las tiras de 5 cifras de los ids (un índice denso contiene por construcción toda cifra menor que n); "
        "las formas completas de los ids sí se buscan en el índice.",
    ]
    post_hoc = [
        "El modo de sondas se decidió DESPUÉS de ver el diagnostico (conteo de sondas por región y por gen): no cambia ninguna llamada ni veredicto del acta, "
        "pero es una desviación del encargo literal («matriz spliced de verdad») y se declara como tal.",
        "El delta solo-unión frente a la filtrada y frente al acta se mide y se declara, no se interpreta.",
    ]
    if MODO == "todas":
        aviso1 = ("Veredicto del red-team (17-oct, por delegación de la titular): se publicaría SOLO esta versión (todas_incluidas) y SOLO "
                  "web/higado.json, web/hueso.json y web/manifiesto.json; explorador.h5ad nunca en público (solo por petición) y solo_spliced/ "
                  "nunca (anexo técnico interno). NO aprobado para publicar: sello sin_verificar, ok_titular false y sin fecha_ok hasta que el "
                  "laboratorio responda a la pregunta pendiente y la titular firme.")
    else:
        aviso1 = ("AVISO: export literal SOLO con sondas de unión exón-exón. 7 de las 10 dianas del panel (TPBG, ERBB3, SLC39A6, ERBB2, "
                  "TACSTD2, MUC1, FGFR1) salen a CERO por no tener ninguna sonda de ese tipo. Estas NO son las cifras del acta. "
                  "Pendiente de decisión de la titular con verificacion.")
    inf = {"modo_sondas": C.ETIQUETA_MODO[MODO], "aviso_primera_linea": aviso1, "version": C.version_contrato(MODO),
           "estado": "sin_verificar; sin OK de la titular; pendiente de decisión; nada sale de la zona clínica",
           "sha256": {os.path.relpath(p, OUT): C.sha256_fichero(p) for p in sorted(set(ESCRITOS)) if os.path.isfile(p)},
           "tamano_kb": {os.path.relpath(p, OUT): _r(os.path.getsize(p) / 1024, 1) for p in sorted(set(ESCRITOS)) if os.path.isfile(p)},
           "n_celulas": {t: int(Ls[t]["n"]) for t in C.LESIONES}, "n_por_grupo": por_grupo,
           "h5ad": h5info, "U3_config_pool": resumen_u3,
           "alineacion_barcode_cache": {t: Ls[t]["alineacion"] for t in C.LESIONES},
           "sondas": {t: {**Ls[t]["n_sondas"], "genes_sin_sonda_union": Ls[t]["genes_sin_sonda_union"]} for t in C.LESIONES},
           "sondas_union_y_total_genes_web": Ls["higado"]["sondas_union_genes_web"],
           "delta_solo_union": {t: Ls[t]["delta"] for t in C.LESIONES},
           "ambiente": {t: Ls[t]["ambiente"] for t in C.LESIONES},
           "referencia_emparejada": {t: Ls[t]["ref_emparejada"] for t in C.LESIONES},
           "cnv": {t: Ls[t]["cnv_meta"] for t in C.LESIONES},
           "umap_parametros": UMAP_PARAMS,
           "genes_web": {t: {"n": len(Ls[t]["genes"]), "excluidos": Ls[t]["genes_excluidos"]} for t in C.LESIONES},
           "veredicto_fuente_acta": ver_fuente, "genes_sin_veredicto": sin_ver, "etiqueta_veredicto": C.ETIQUETA_VEREDICTO,
           "reproduccion_acta": {t: Ls[t]["reproduccion_acta"] for t in C.LESIONES},
           "seleccion": {t: Ls[t]["seleccion"] for t in C.LESIONES}, "loo": {t: Ls[t]["loo"] for t in C.LESIONES},
           "adn": {t: {"web": Ls[t]["adn"], "meta": Ls[t]["adn_meta"]} for t in C.LESIONES},
           "pool_crudo_higiene": pool, "zarr": ZINFO,
           "adn_celula_diag": {t: Ls[t]["adn_celula_diag"] for t in C.LESIONES},
           "quitado": quitado, "decisiones": decisiones, "post_hoc": post_hoc, "validadores": validadores}
    md = ["> ⚠️ %s" % aviso1, "",
          "# INFORME DE LIMPIEZA · explorador single-cell (export curado, zona clínica) · modo_sondas = %s" % inf["modo_sondas"], "",
          "Estado: %s. Versión: %s." % (inf["estado"], inf["version"]), "",
          "## Qué hay", "- `%s` (%s)" % (os.path.basename(ruta_h5), json.dumps(h5info, ensure_ascii=False)),
          "- `web/manifiesto.json`, `web/higado.json`, `web/hueso.json`",
          "- `%s/higado.zarr`, `%s/hueso.zarr` (todos los genes, fuera de web/; %s)" % (
              C.ZARR_DIR, C.ZARR_DIR, json.dumps(ZINFO, ensure_ascii=False)), "", "## sha256 y tamaño (KB)"]
    for k in inf["sha256"]:
        md.append("- `%s` · %s · %s KB" % (k, inf["sha256"][k], inf["tamano_kb"][k]))
    md += ["", "## Células y grupos"]
    for t in C.LESIONES:
        md.append("- %s: n %d · " % (t, inf["n_celulas"][t]) + " · ".join("%s %d" % kv for kv in por_grupo[t].items()))
    md += ["", "## U3 · muestras del pool (config.csv)", "```", json.dumps(resumen_u3, ensure_ascii=False, indent=1), "```",
           "Lectura: %d filas en [samples], %d suyas y %d ajenas. %s" % (
               resumen_u3["n_filas_samples"], resumen_u3["n_muestras_paciente"], resumen_u3["n_muestras_ajenas"],
               "No hay muestras ajenas: todo el pool es de la paciente." if resumen_u3["n_muestras_ajenas"] == 0
               else "HAY muestras ajenas: de ellas no entra nada (solo se leen los ficheros per-sample de las dos muestras de la paciente)."),
           "", "## Qué se quitó"] + ["- " + q for q in quitado]
    md += ["", "## Alineación barcode ↔ cachés del cierre", "```", json.dumps(inf["alineacion_barcode_cache"], ensure_ascii=False, indent=1), "```"]
    md += ["", "## Sondas: qué daría una matriz solo de sondas de unión exón-exón (delta), grupo de interés"]
    for t in C.LESIONES:
        d = Ls[t]["delta"]
        md.append("- %s: las sondas de unión suman el %.1f %% de las UMI de la matriz filtrada (se perdería el %.1f %%)"
                  % (t, 100 * d["umi_union_frac_del_total"], 100 * d["umi_perdida_total_frac_si_solo_union"]))
        md.append("")
        md.append("| gen | sondas unión/total | UMI perdida si solo unión | frac filtrada (X en modo todas) | frac solo unión | Δ pts unión − filtrada | frac acta | Δ pts filtrada − acta | Δ pts unión − acta |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        for g, x in d["panel"].items():
            if "nota" in x:
                md.append("| %s | %s | | | | | | | |" % (g, x["nota"]))
                continue
            md.append("| %s | %d/%d | %.1f %% | %.3f | %.3f | %+.2f | %s | %s | %s |" % (
                g, x["n_sondas_union"], x["n_sondas_total"], 100 * x["umi_perdida_frac_si_solo_union"], x["frac_detectada_filtrada"],
                x["frac_detectada_solo_union"], x["delta_puntos_union_menos_filtrada"],
                "" if x["frac_acta"] is None else "%.2f" % x["frac_acta"],
                "" if x["delta_puntos_filtrada_menos_acta"] is None else "%+.2f" % x["delta_puntos_filtrada_menos_acta"],
                "" if x["delta_puntos_union_menos_acta"] is None else "%+.2f" % x["delta_puntos_union_menos_acta"]))
        md.append("")
    md += ["## Ambiente y referencia emparejada", "```",
           json.dumps({"ambiente": inf["ambiente"], "referencia": inf["referencia_emparejada"]}, ensure_ascii=False, indent=1), "```",
           "", "## CNV", "```", json.dumps(inf["cnv"], ensure_ascii=False, indent=1), "```",
           "", "## UMAP", "```", json.dumps(UMAP_PARAMS, ensure_ascii=False, indent=1), "```",
           "", "## Genes de la web (sondas unión/total por gen)", "```", json.dumps(inf["genes_web"], ensure_ascii=False, indent=1),
           json.dumps(inf["sondas_union_y_total_genes_web"], ensure_ascii=False), "```",
           "", "## Reproducción del acta (check fail-closed)", "```", json.dumps(inf["reproduccion_acta"], ensure_ascii=False, indent=1), "```",
           "", "## Selección del grupo de interés (circularidad) y cifras LOO", "```",
           json.dumps({"seleccion": inf["seleccion"], "loo": inf["loo"]}, ensure_ascii=False, indent=1), "```",
           "", "## ADN genómico (lo que enseña la página)", "```", json.dumps(inf["adn"], ensure_ascii=False, indent=1), "```",
           "", "## Higiene: matriz cruda del pool (solo se anota)",
           "La matriz cruda del POOL (MP/outs/raw_feature_bc_matrix.h5) no la usa el export (el suelo sale de las crudas por muestra). Lleva "
           "barcodes bajo probe barcodes de otras muestras de la corrida, a nivel de ruido. No se ha movido ni borrado nada: lo decide la titular.",
           "```", json.dumps(pool, ensure_ascii=False, indent=1), "```",
           "", "## Veredicto del acta (%s; fuente: %s)" % (C.ETIQUETA_VEREDICTO, C.ACTA), "```", json.dumps(ver_fuente, ensure_ascii=False, indent=1), "```",
           "Sin veredicto: " + json.dumps(sin_ver, ensure_ascii=False),
           "", "## Decisiones"] + ["- " + d for d in decisiones] + ["", "## Post hoc declarados"] + ["- " + p for p in post_hoc]
    md += ["", "## Validadores", "```", json.dumps(validadores, ensure_ascii=False, indent=1), "```", ""]
    _escribe_json(os.path.join(OUT, "INFORME_LIMPIEZA.json"), inf)
    _escribe_texto(os.path.join(OUT, "INFORME_LIMPIEZA.md"), "\n".join(md))
    return inf


def borra_escritos():
    for p in set(ESCRITOS):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
        except OSError:
            pass


def zarrs(Ls):
    """«Loupe público»: un AnnData-Zarr por lesión con TODOS los genes (X en csc, gen a gen), fuera de web/."""
    n_h = Ls["higado"]["n"]
    idx = C.indice_secuencial(n_h + Ls["hueso"]["n"])
    out = {}
    for t, ids in (("higado", idx[:n_h]), ("hueso", idx[n_h:])):
        L = Ls[t]
        p = os.path.join(OUT, C.ZARR_DIR, "%s.zarr" % t)
        if os.path.exists(p):
            shutil.rmtree(p)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        ESCRITOS.append(p)
        info = C.escribe_zarr_lesion(p, t, L["X"], L["umi"], L["simbolo"], L["gene_ids"], L["grupo"], L["tipo_c"],
                                     L["umap"], ids, L["suelo_todos"], C.version_contrato(MODO), MODO,
                                     L["adn_por_grupo"], L["n_sondas_gen"], L["n_sondas_union_gen"],
                                     L["marcadores"], L["perfil"])
        tam = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)
        n_f = sum(len(fs) for _, _, fs in os.walk(p))
        out[t] = {**info, "n_genes_sin_sonda": len(L["suelo_todos"]["excluidos"]), "tamano_mb": _r(tam / 2 ** 20, 1),
                  "n_ficheros": n_f}
    return out


# ── Órdenes ─────────────────────────────────────────────────────────────────────────────────────
def exportar(a):
    global PROHIBIDAS
    paciente, resumen_u3, PROHIBIDAS = leer_config()
    di("U3: filas en [samples] %d · de la paciente %d · ajenas %d · coincide con tribunal/_comun %s · modo sondas %s"
       % (resumen_u3["n_filas_samples"], resumen_u3["n_muestras_paciente"], resumen_u3["n_muestras_ajenas"],
          resumen_u3["coincide_con_tribunal_comun_muestras"], MODO))
    ps = sondas_incluidas()
    cv = TC.coords()
    ctx = contexto_acta()
    pool = pool_crudo_ruido()
    di("pool crudo (no lo usa el export): sufijos %d · de la paciente %d · ajenos %d · barcodes ajenos %d · UMI ajenas %d (%.3f %%)"
       % (pool["n_sufijos"], pool["n_sufijos_de_la_paciente"], pool["n_sufijos_ajenos"], pool["n_barcodes_ajenos"],
          pool["umi_ajenas"], 100 * pool["frac_umi_ajenas"]))
    Ls = {}
    with open(os.path.join(TC.A_DIR, "qc.json"), encoding="utf-8") as fh:
        qc_a = json.load(fh)
    for t in C.LESIONES:
        Ls[t] = procesa_lesion(t, ps, cv, ctx)
        Ls[t]["qc"] = C.construye_qc(qc_a[t])
        if Ls[t]["qc"]["n_final"] != Ls[t]["n"]:   # el QC de A es el que fija las células del explorador
            raise RuntimeError("%s: el QC de A no deja las mismas células que el explorador" % t)
    ruta_h5, h5info = h5ad(Ls)
    ZINFO.update(zarrs(Ls))
    for t in C.LESIONES:
        web_json(Ls[t])
    fich = [{"ruta": "%s.json" % t, "sha256": C.sha256_fichero(os.path.join(OUT, "web", "%s.json" % t))} for t in C.LESIONES]
    _escribe_json(os.path.join(OUT, "web", "manifiesto.json"),
                  {"version": 1, "sello": "sin_verificar", C.CLAVE_OK_TITULAR: False, "lesiones": list(C.LESIONES), "ficheros": fich,
                   "modo_sondas": C.ETIQUETA_MODO[MODO]})
    # informe provisional → validación de todo → informe definitivo → validación del informe
    informe(Ls, resumen_u3, h5info, {"estado": "pendiente"}, ruta_h5, pool)
    try:
        val = C.valida_salidas(OUT, PROHIBIDAS, con_informe=True)
        val["resultado"] = "PASA"
        inf = informe(Ls, resumen_u3, h5info, val, ruta_h5, pool)
        C.valida_json_generico(os.path.join(OUT, "INFORME_LIMPIEZA.json"), PROHIBIDAS)
        C.valida_texto(os.path.join(OUT, "INFORME_LIMPIEZA.md"), PROHIBIDAS)
    except C.ValidacionFallida as e:
        print("VALIDACIÓN FALLIDA: %s. Se borran las salidas de esta ejecución." % e, file=sys.stderr)
        borra_escritos()
        return 1
    di("n por grupo:", json.dumps(inf["n_por_grupo"], ensure_ascii=False))
    di("tamaños KB:", json.dumps(inf["tamano_kb"], ensure_ascii=False))
    di("delta (puntos, filtrada − acta | solo unión − acta):",
       json.dumps({t: {g: [x.get("delta_puntos_filtrada_menos_acta"), x.get("delta_puntos_union_menos_acta")]
                       for g, x in Ls[t]["delta"]["panel"].items()} for t in C.LESIONES}, ensure_ascii=False))
    di("validadores: PASA ·", json.dumps({k: v for k, v in val.items() if k != "resultado"}, ensure_ascii=False))
    di("hecho en %.0f s · modo_sondas %s · salida en la zona clínica del Flex (%s)"
       % (time.time() - T0, C.ETIQUETA_MODO[MODO], os.path.relpath(OUT, TC.FLEX)))
    return 0


def validar(a):
    global PROHIBIDAS
    _, resumen_u3, PROHIBIDAS = leer_config()
    try:
        val = C.valida_salidas(OUT, PROHIBIDAS, con_informe=True)
    except C.ValidacionFallida as e:
        print("VALIDACIÓN FALLIDA: %s" % e, file=sys.stderr)
        return 1
    di("validadores: PASA ·", json.dumps(val, ensure_ascii=False))
    return 0


def diagnostico(a):
    """Solo agregados: clases de formas prohibidas, choque con el índice secuencial y cobertura de sondas
    de los genes del contrato web (vocabulario público). Ningún valor de identificador se imprime."""
    import h5py
    global PROHIBIDAS
    _, resumen_u3, PROHIBIDAS = leer_config()
    digit = [p for p in PROHIBIDAS if p.isdigit()]
    print("formas prohibidas:", len(PROHIBIDAS), "· solo cifras:", len(digit),
          "· longitudes (min, max):", (min(map(len, PROHIBIDAS)), max(map(len, PROHIBIDAS))))
    idx = C.indice_secuencial(21040)
    choques = [(i, C.cadena_prohibida(s, PROHIBIDAS)) for i, s in enumerate(idx)]
    choques = [(i, m) for i, m in choques if m]
    print("índices secuenciales que chocan:", len(choques), "· motivos:", sorted({m for _, m in choques}))
    ps = sondas_incluidas()
    reg = ps["region"].value_counts()
    print("sondas included:", int(len(ps)), "· region spliced:", int(reg.get("spliced", 0)),
          "· otras regiones:", int(len(ps) - reg.get("spliced", 0)), "· nº de valores distintos de region:", int(len(reg)))
    with h5py.File(TC.ruta("higado", "_filtered_feature_barcode_matrix.h5"), "r") as f:
        nombres = set(x.decode() for x in f["matrix/features/name"][:])
        ids_h5 = set(x.decode() for x in f["matrix/features/id"][:])
    print("genes en la matriz filtrada:", len(nombres), "· gene_id de probe_set que están en la matriz:",
          int(ps["gene_id"].isin(ids_h5).sum()), "de", int(len(ps)))
    spl = ps[ps["region"] == "spliced"]
    por_gen_s = spl.groupby("gene_name").size()
    por_gen_t = ps.groupby("gene_name").size()
    filas = []
    for g in C.GENES_WEB:
        filas.append("%s:%s/%d/%d" % (g, "si" if g in nombres else "NO", int(por_gen_s.get(g, 0)), int(por_gen_t.get(g, 0))))
    print("gen:en_matriz/sondas_union/sondas_total ->", " ".join(filas))
    # pre-validación de las cadenas que irán al h5ad (símbolos y Ensembl) y de los textos fijos del informe
    hits = {}
    for s in list(nombres) + list(ids_h5):
        m = C.cadena_prohibida(s, PROHIBIDAS)
        if m:
            hits[m] = hits.get(m, 0) + 1
    print("símbolos/Ensembl que chocan con los patrones propios:", hits or 0)
    if hits:   # clase del token que choca (longitud y tipo), nunca su valor
        for p_ in sorted(PROHIBIDAS):
            k = sum(1 for s in nombres if C.cadena_prohibida(s, {p_}))
            if k:
                print("   token len=%d alpha=%s digit=%s -> %d símbolos" % (len(p_), p_.isalpha(), p_.isdigit(), k))
    for capas, etiqueta in ((("casi_titular",), "tolerando casi_titular en tokens"), ((), "estricto")):
        try:
            n, tol = C._puerta_lote(list(nombres) + list(ids_h5), "var", ignorar_capas=capas)
            print("puerta_n1 sobre símbolos + Ensembl (%s): PASA (%d únicas, %d toleradas)" % (etiqueta, n, tol))
        except C.ValidacionFallida as e:
            print("puerta_n1 sobre símbolos + Ensembl (%s): CIERRA ->" % etiqueta, e)
    return 0


def main(argv=None):
    global MODO
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("orden", choices=["exportar", "validar", "diagnostico"])
    p.add_argument("--sondas", choices=list(C.MODOS_SONDAS), default="todas",
                   help="todas = cuentas de Cell Ranger (base del acta; por defecto); spliced = solo sondas de unión exón-exón (literal, no recomendado)")
    p.add_argument("--prueba", action="store_true",
                   help="escribe en <salida>/_prueba: ensayo de un cambio sin pisar el export vigente (si la validación falla, "
                        "borra solo lo de esta ejecución, que con esto nunca es lo vigente)")
    a = p.parse_args(argv)
    MODO = a.sondas
    global OUT
    OUT = OUT_BASE if MODO == "todas" else os.path.join(OUT_BASE, "solo_spliced")
    if a.prueba:
        OUT = os.path.join(OUT, "_prueba")
    preparar()
    return {"exportar": exportar, "validar": validar, "diagnostico": diagnostico}[a.orden](a) or 0


if __name__ == "__main__":
    sys.exit(main())
