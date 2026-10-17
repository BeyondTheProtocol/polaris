#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_flex_explorador.py — contrato y validador del export curado del explorador single-cell
(tools/flex_analisis/explorador/_contrato.py), con un FIXTURE SINTÉTICO. Cero datos de la paciente:
el test no lee la zona clínica.

Comprueba:
  1. el constructor de obs/uns y el validador aceptan un export sintético bien formado (h5ad desde cero,
     web/{higado,hueso,manifiesto}.json con el contrato de la página);
  2. MUTANTES que el validador DEBE tumbar: una columna obs de más · un barcode 10x en el índice · un
     barcode dentro de uns · un id de bloque (forma de accesión) en un attr · una capa «unspliced» ·
     «tumor» en un grupo del hígado · un grupo fuera de vocabulario · el nombre de la titular en el h5ad ·
     una fecha en un JSON · un id de muestra (forma laxa, en memoria) en un JSON · sha del manifiesto roto;
     y los del red-team del 17-oct: A4 (solo dos estados del manifiesto; fecha_ok AAAA-MM-DD de calendario
     y solo en el publicado), C1 (la clave vieja con el nombre ya no vale), B1-B3 (sondas, selección, loo,
     adn y método de CNV del contrato web) y el check de reproducción del acta (ESR1 del hígado contra su
     LOO) y los veredictos del hueso tomados del acta (B4). Los mutantes de una lesión recalculan el sha
     del manifiesto: tienen que caer por su propio motivo;
  3. exporta.py no arranca sin la ventanilla (rc 2, stdout vacío) y está registrado en lector_clinico
     con el intérprete de nescore.
Sin numpy/anndata se re-ejecuta con el env nescore; sin ese env, SKIP (77). Sin el diccionario de
puerta_n1 (jaula sin overlays), SKIP (77): la puerta es fail-closed y aquí no se finge.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

_NESCORE = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
try:
    import anndata as an
    import h5py
    import numpy as np
    import pandas as pd
    import scipy.sparse as sp
except ModuleNotFoundError:
    if os.path.isfile(_NESCORE) and os.path.realpath(sys.executable) != os.path.realpath(_NESCORE):
        os.execv(_NESCORE, [_NESCORE, os.path.abspath(__file__)] + sys.argv[1:])
    print("SKIP: sin anndata/h5py ni env nescore; test_flex_explorador no puede correr")
    sys.exit(77)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(RAIZ, "tools", "flex_analisis", "explorador")
PY = sys.executable
fallos = []
total = [0]


def check(cond, desc):
    total[0] += 1
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def carga(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


C = carga("_contrato", os.path.join(EXP, "_contrato.py"))
try:
    C._puerta().exigir_diccionario()
except Exception as e:  # PuertaCerrada o import roto: fail-closed, no verde falso
    print("SKIP: puerta_n1 sin diccionario (%s); el validador no puede mirar" % type(e).__name__)
    sys.exit(77)

# Formas «prohibidas» sintéticas (lo que en el export real sale de config.csv en memoria)
PROHIB = C.formas_laxas("AB-12345_M1") | C.formas_laxas("B12-34567_M2") | {"a-d10", "a-d11", "poolsintetico"}
rng = np.random.default_rng(7)
GENES = C.GENES_WEB[:40] + ["GEN%02d" % i for i in range(20)]
N = {"higado": 1200, "hueso": 300}


def lesion_sintetica(les):
    n = N[les]
    X = sp.random(n, len(GENES), density=0.08, random_state=int(rng.integers(1e6)), format="csr")
    X.data = np.ceil(X.data * 5).astype(np.int32)
    X = X.astype(np.int32)
    g = C.GRUPOS[les]
    k = C.N_INTERES[les]
    tipos_A = list(C.TIPOS)
    if les == "higado":
        base = ["epitelial", "no_asignado"]
        resto = ["hepatocito", "NK", "mieloide", "endotelio", "fibroblasto_estelar", "epitelial", "colangiocito"]
    else:
        base = ["epitelial", "no_asignado"]
        resto = ["T", "NK", "B", "plasmatica", "mieloide", "neutrofilo", "mastocito", "endotelio", "osteoclasto",
                 "eritroide", "fibroblasto_estelar", "hepatocito"]
    assert set(base + resto) <= set(tipos_A)
    tipo = np.array(list(rng.choice(base, k)) + list(rng.choice(resto, n - k)), dtype=object)
    interes = np.zeros(n, bool)
    interes[:k] = True
    limpio = rng.random(n) > 0.2 if les == "hueso" else None
    grupo = C.grupos_lesion(les, tipo, interes, limpio)
    tipo_c = C.tipo_canonico(tipo)
    umap = rng.normal(size=(n, 2)) * 3
    return {"X": X, "grupo": grupo, "tipo_c": tipo_c, "umap": umap, "interes": interes, "n": n}


def web_sintetico(les, L):
    g = C.GRUPOS[les]
    g_ids = [g["interes"]] + [r for r in g["referencia"] if (L["grupo"] == r).any()] + [g["otras"]]
    rol = {g["interes"]: "interes", g["otras"]: "otras"}
    tipos = sorted(set(L["tipo_c"].astype(str)))
    um = L["umap"]
    esc = [((um[:, k] - um[:, k].min()) / (um[:, k].max() - um[:, k].min())) * 20 - 10 for k in range(2)]
    plano = []
    for i in range(L["n"]):
        plano += [round(float(esc[0][i]), 2), round(float(esc[1][i]), 2)]
    genes = GENES[:30]
    umi = np.asarray(L["X"].sum(axis=1)).ravel().astype(float) + 1
    expr, suelo, ref = {}, {}, {}
    for j, gname in enumerate(genes):
        v = np.asarray(L["X"][:, j].todense()).ravel()
        nz = np.where(v > 0)[0]
        expr[gname] = {"i": [int(i) for i in nz], "v": [float("%.3g" % x) for x in v[nz] / umi[nz] * 1e4]}
        suelo[gname] = {gid: round(float(rng.random() * 0.3), 3) for gid in g_ids}
        ref[gname] = round(float(rng.random()), 4)
    if les == "hueso":
        cnv = {"ventanas": list(C.CNV_VENTANAS), "metodo": C.CNV_METODOS["todas"],
               "valores": [[round(float(x), 3) for x in rng.normal(size=L["n"])] for _ in C.CNV_VENTANAS]}
        sel = C.seleccion_lesion(genes, {"identidad": ["EPCAM", "KRT19"]})
        loo = {}
    else:
        cnv = {"pseudobulk": {"ventana": "8p11", "percentil_genoma": 97}}
        sel = C.seleccion_lesion(genes, {"identidad": ["ESR1", "KRT19", "FOXA1"], "fgfr1_alto": ["FGFR1"],
                                         "exclusion": ["ALB", "PTPRC"], "ligado_er": ["CCND1", "PGR", "AR"]})
        loo = {"ESR1": {"n": 600, "frac": 0.5, "ref": 0.4, "cp10k_pseudobulk": 3.9},
               "FGFR1": {"n": 3000, "frac": 0.99, "ref": 0.95, "cp10k_pseudobulk": 230.0}}
    sondas = {gname: [int(rng.integers(0, 2)), int(rng.integers(2, 6))] for gname in genes}
    adn = {"sondas_dentro_de_exon": 46000, "sondas_panel": 53500, "frac_lecturas_estimada": 0.01,
           "frac_lecturas_cell_ranger": 0.02, "frac_lecturas_interes": 0.005}
    gpos = {x: i for i, x in enumerate(g_ids)}
    tpos = {x: i for i, x in enumerate(tipos)}
    return {"version": 1, "lesion": les, "n_celulas": L["n"],
            "grupos": [{"id": x, "rol": rol.get(x, "referencia"), "n": int((L["grupo"] == x).sum())} for x in g_ids],
            "tipos": tipos,
            "celulas": {"umap": plano, "grupo": [gpos[x] for x in L["grupo"].astype(str)],
                        "tipo": [tpos[x] for x in L["tipo_c"].astype(str)]},
            "genes": genes, "expr": expr, "suelo": suelo, "referencia": ref,
            "veredicto": C.veredictos_lesion(les, genes), "sondas": sondas, "seleccion": sel, "loo": loo, "adn": adn,
            "cnv": cnv}


def escribe_json(p, obj):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))


def export_sintetico(d):
    Ls = {les: lesion_sintetica(les) for les in C.LESIONES}
    Lh, Lb = Ls["higado"], Ls["hueso"]
    X = sp.vstack([Lh["X"], Lb["X"]]).tocsr()
    obs = C.construye_obs(["higado"] * Lh["n"] + ["hueso"] * Lb["n"], np.concatenate([Lh["grupo"], Lb["grupo"]]),
                          np.concatenate([Lh["tipo_c"], Lb["tipo_c"]]), np.vstack([Lh["umap"], Lb["umap"]]))
    var = pd.DataFrame({"ensembl": ["ENSG%011d" % i for i in range(len(GENES))]}, index=pd.Index(GENES))
    webs = {les: web_sintetico(les, Ls[les]) for les in C.LESIONES}
    cnv_h = pd.DataFrame({wv: np.asarray(webs["hueso"]["cnv"]["valores"][i]) for i, wv in enumerate(C.CNV_VENTANAS)},
                         index=obs.index[Lh["n"]:])
    uns = C.construye_uns(suelo={les: webs[les]["suelo"] for les in C.LESIONES},
                          referencia={les: webs[les]["referencia"] for les in C.LESIONES},
                          veredicto={les: webs[les]["veredicto"] for les in C.LESIONES},
                          cnv_hueso=cnv_h, cnv_higado_pseudobulk={"ventana": "8p11", "percentil_genoma": 97},
                          avisos=["Apoyo a la decisión. No es diagnóstico.", "ARN no es proteína."])
    a = an.AnnData(X=X, obs=obs, var=var, uns=uns)
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    os.makedirs(os.path.join(d, "web"), exist_ok=True)
    for les in C.LESIONES:
        escribe_json(os.path.join(d, "web", "%s.json" % les), webs[les])
    fich = [{"ruta": "%s.json" % les, "sha256": C.sha256_fichero(os.path.join(d, "web", "%s.json" % les))} for les in C.LESIONES]
    escribe_json(os.path.join(d, "web", "manifiesto.json"),
                 {"version": 1, "sello": "sin_verificar", C.CLAVE_OK_TITULAR: False, "lesiones": list(C.LESIONES), "ficheros": fich,
                  "modo_sondas": C.ETIQUETA_MODO["todas"]})
    # «Loupe público»: un Zarr por lesión con TODOS los genes y su suelo (el mismo constructor que exporta.py)
    idx = list(obs.index)
    for les, ids in (("higado", idx[:Lh["n"]]), ("hueso", idx[Lh["n"]:])):
        escribe_zarr_sintetico(os.path.join(d, C.ZARR_DIR, "%s.zarr" % les), les, Ls[les], ids)
    return Ls


def suelo_todos_sintetico(les, L):
    ST = C._suelo_todos()
    n, G = L["X"].shape
    grupo = np.asarray(L["grupo"]).astype(str)
    g_ids = C.grupos_zarr(les, grupo)
    umi = np.asarray(L["X"].sum(axis=1)).ravel().astype(np.int64) + 1
    ref = np.isin(grupo, C.GRUPOS[les]["referencia"])
    excl = np.zeros(G, bool)
    excl[-2:] = True                                  # dos genes «sin sonda»
    return ST.calcula(L["X"], rng.dirichlet(np.ones(G)), 1.5, umi, rng.uniform(0, .01, n), rng.integers(0, 4, G),
                      grupo, g_ids, ref, rng.uniform(0, 2, int(ref.sum())), excl), umi


def escribe_zarr_sintetico(p, les, L, ids):
    todos, umi = suelo_todos_sintetico(les, L)
    C.escribe_zarr_lesion(p, les, L["X"], umi, GENES, ["ENSG%011d" % i for i in range(len(GENES))], L["grupo"],
                          L["tipo_c"], L["umap"], ids, todos, C.version_contrato("todas"), "todas")


def reescribe_zarr(p, f, csr=False):
    """Lee el Zarr, aplica f(AnnData) y lo vuelve a escribir con los trozos del contrato (y, si csr, X en csr)."""
    a = an.read_zarr(p)
    f(a)
    shutil.rmtree(p)
    a.write_zarr(p)
    C.fija_X_zarr(p, sp.csr_matrix(a.X) if csr else sp.csc_matrix(a.X))


def valida(d):
    return C.valida_salidas(d, PROHIB, con_informe=False)


def debe_fallar(d, desc, esperado=None):
    try:
        valida(d)
        check(False, "MUTANTE no detectado: " + desc)
    except C.ValidacionFallida as e:
        ok = esperado is None or esperado in str(e)
        check(ok, "mutante tumbado: %s (%s)" % (desc, str(e)[:90]))


def copia(base):
    d = tempfile.mkdtemp(prefix="flexexp_")
    shutil.rmtree(d)
    shutil.copytree(base, d)
    return d


def mutar_lesion(base, les, f):
    """Copia del export con la lesión mutada y el sha del manifiesto RECALCULADO: el mutante tiene que caer
    por su propio motivo, no porque el sha deje de cuadrar."""
    d = copia(base)
    p = os.path.join(d, "web", "%s.json" % les)
    obj = json.load(open(p))
    f(obj)
    escribe_json(p, obj)
    m = os.path.join(d, "web", "manifiesto.json")
    man = json.load(open(m))
    man["ficheros"] = [{"ruta": x["ruta"], "sha256": C.sha256_fichero(os.path.join(d, "web", x["ruta"]))} for x in man["ficheros"]]
    escribe_json(m, man)
    return d


def main():
    base = tempfile.mkdtemp(prefix="flexexp_base_")
    Ls = export_sintetico(base)
    # 1) el export sintético bien formado pasa entero
    try:
        res = valida(base)
        check(res["explorador.h5ad"]["n_celulas"] == N["higado"] + N["hueso"], "h5ad sintético pasa el validador (n células)")
        check(res["web/higado.json"]["n_genes"] == 30 and res["web/hueso.json"]["n_genes"] == 30, "web/*.json sintéticos pasan el validador")
        check(res["web/manifiesto.json"].get("estado") == "export" and C.CLAVE_OK_TITULAR == "ok_titular",
              "manifiesto del export (ok_titular false, sin fecha_ok) pasa entero por puerta_n1, sin excepción de clave (C1)")
        check(res["web/manifiesto.json"].get("modo_sondas") == "todas_incluidas", "el validador devuelve el modo_sondas del manifiesto")
    except C.ValidacionFallida as e:
        check(False, "el export sintético bien formado NO pasa: %s" % e)
        return
    check(int((Ls["higado"]["grupo"] == "luminal_alta_confianza").sum()) == 993
          and int((Ls["hueso"]["grupo"] == "malignas_limpias").sum()) == 68, "constructor de grupos: 993 / 68")
    check(not any("tumor" in g or "malign" in g for g in set(Ls["higado"]["grupo"])), "ningún grupo del hígado lleva tumor/malign")
    for n_mal in (992, 994):
        try:
            C.grupos_lesion("higado", np.array(["epitelial"] * 1200), np.arange(1200) < n_mal)
            check(False, "grupos_lesion acepta n=%d en el hígado" % n_mal)
        except AssertionError:
            check(True, "grupos_lesion rechaza n=%d en el hígado (assert 993)" % n_mal)
    try:
        C.tipo_canonico(np.array(["epitelial", "tumoral"]))
        check(False, "tipo_canonico acepta un tipo desconocido")
    except ValueError:
        check(True, "tipo_canonico es fail-closed con un tipo fuera de la caché")
    # tiras de cifras de un id: frontera de cifra, e índice secuencial exento (excepción declarada)
    check(C.cadena_prohibida("bloque 12345 del pool", {"12345"}) == "identificador_de_muestra_o_pool", "tira de cifras suelta: prohibida")
    check(C.cadena_prohibida("ENSG00000012345", {"12345"}) is None, "tira de cifras dentro de un Ensembl: no es el id")
    check(C.cadena_prohibida("c12345", {"12345"}) is None, "índice secuencial c12345: exento de la tira de cifras")
    check(C.cadena_prohibida("c12345", {"ab-12345_m1", "ab12345m1"}) is None and
          C.cadena_prohibida("x ab12345m1 y", {"ab12345m1"}) == "identificador_de_muestra_o_pool", "la forma completa del id sigue prohibida en cualquier cadena")
    check(C.cadena_prohibida("MARCH10", PROHIB) is None and C.cadena_prohibida("17-oct-26", PROHIB) == "fecha", "mes+año es fecha; MARCH10 no")

    # 1b) Zarr por lesión («Loupe público»): pasa, todos los genes, X en csc, suelo en cada gen con sonda
    zh, zb = res.get("zarr/higado.zarr", {}), res.get("zarr/hueso.zarr", {})
    check(zh.get("n_celulas") == N["higado"] and zb.get("n_celulas") == N["hueso"]
          and zh.get("n_genes") == len(GENES) and zh.get("n_genes_sin_sonda") == 2,
          "zarr sintéticos pasan el validador (células por lesión, TODOS los genes, 2 sin sonda)")
    import zarr
    zg = zarr.open_group(os.path.join(base, "zarr", "higado.zarr"), mode="r")
    check(zg["X"].attrs["encoding-type"] == "csc_matrix" and zg["X"]["data"].chunks[0] <= C.ZARR_CHUNK,
          "zarr: X en csc_matrix con trozos del contrato (la web lee gen a gen)")
    check(C.sig3(np.array([1234.5, 0.012345, 99.95])).tolist() == [float("%.3g" % x) for x in (1234.5, 0.012345, 99.95)],
          "sig3 vectorizado = «%.3g» de la web")

    # 2) mutantes
    zh = os.path.join("zarr", "higado.zarr")
    zb = os.path.join("zarr", "hueso.zarr")
    d = copia(base)
    reescribe_zarr(os.path.join(d, zh), lambda a: None, csr=True)
    debe_fallar(d, "zarr: X en csr (la web recorrería la matriz entera por gen)", "csc_matrix")

    d = copia(base)

    def _sin_suelo(a):
        a.var.iloc[3, a.var.columns.get_loc(C.PREFIJO_SUELO + "otras")] = np.nan
    reescribe_zarr(os.path.join(d, zh), _sin_suelo)
    debe_fallar(d, "zarr: un gen con sonda sin suelo en un grupo", "sin suelo")

    d = copia(base)

    def _excluido_con_valor(a):
        a.uns["genes_sin_sonda"] = np.array(list(a.uns["genes_sin_sonda"])[:1], dtype=object)
    reescribe_zarr(os.path.join(d, zh), _excluido_con_valor)
    debe_fallar(d, "zarr: un gen sin sonda que deja de declararse (queda sin suelo)", "sin suelo")

    d = copia(base)

    def _barcode_en_var(a):
        v = list(a.var_names)
        v[5] = "AAACCCAAGAAACACT-1"
        a.var_names = v
    reescribe_zarr(os.path.join(d, zh), _barcode_en_var)
    debe_fallar(d, "zarr: un barcode 10x en var", "barcode_10x")

    d = copia(base)

    def _tumor(a):
        g = a.obs["grupo"].astype(str).values.copy()
        g[np.where(g == "otras")[0][:5]] = "tumor"
        a.obs["grupo"] = g
    reescribe_zarr(os.path.join(d, zh), _tumor)
    debe_fallar(d, "zarr: «tumor» en un grupo del hígado", "tumor")

    d = copia(base)

    def _uns_extra(a):
        a.uns["nota"] = "x"
    reescribe_zarr(os.path.join(d, zh), _uns_extra)
    debe_fallar(d, "zarr: una clave de uns fuera del contrato", "uns fuera del contrato")

    d = copia(base)

    def _celulas_del_higado(a):
        a.obs_names = C.indice_secuencial(a.n_obs)       # índices válidos y ordenados, pero los del hígado
    reescribe_zarr(os.path.join(d, zb), _celulas_del_higado)
    debe_fallar(d, "zarr: el del hueso con índices de células del hígado", "no son las de esa lesión")

    d = copia(base)
    shutil.rmtree(os.path.join(d, zb))
    debe_fallar(d, "zarr: falta el del hueso", "no existe")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    a.obs["barcode"] = ["x"] * a.n_obs
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "una columna obs de más", "allow-list")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    idx = list(a.obs_names)
    idx[0] = "AAACCCAAGAAACACT-1"
    a.obs_names = idx
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "un barcode 10x en el índice de obs")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    a.uns["avisos"] = list(a.uns["avisos"]) + ["célula AAACCCAAGAAACACT-1 revisada"]
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "un barcode dentro de uns", "barcode_10x")

    d = copia(base)
    with h5py.File(os.path.join(d, "explorador.h5ad"), "r+") as f:
        f["X"].attrs["nota"] = "bloque 23-67890"      # forma de accesión de caso_publico, sin solaparse con el id sintético
    debe_fallar(d, "un id de bloque (forma de accesión) en un attr", "codigo_ap")

    d = copia(base)
    with h5py.File(os.path.join(d, "explorador.h5ad"), "r+") as f:
        f["var"].attrs["muestra"] = "AB-12345_M1"
    debe_fallar(d, "el id de muestra leído en memoria, en un attr", "identificador_de_muestra_o_pool")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    a.layers["unspliced"] = a.X.copy()
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "una capa unspliced")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    g = a.obs["grupo"].astype(str).values.copy()
    m = (a.obs["lesion"].values == "higado") & (g == "otras")
    g[np.where(m)[0][:5]] = "tumor"
    a.obs["grupo"] = g
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "«tumor» en un grupo del hígado")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    g = a.obs["grupo"].astype(str).values.copy()
    m = (a.obs["lesion"].values == "hueso") & (g == "otras")
    g[np.where(m)[0][:5]] = "estroma"
    a.obs["grupo"] = g
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "un grupo fuera de vocabulario", "vocabulario")

    d = copia(base)
    a = an.read_h5ad(os.path.join(d, "explorador.h5ad"))
    a.uns["avisos"] = list(a.uns["avisos"]) + ["revisado por {{TITULAR}}"]
    a.write_h5ad(os.path.join(d, "explorador.h5ad"))
    debe_fallar(d, "el nombre de la titular dentro del h5ad")

    debe_fallar(mutar_lesion(base, "higado", lambda o: o["grupos"][0].__setitem__("id", "tumor_alta_confianza")),
                "«tumor» en un grupo del hígado (web/higado.json)", "higado.json")

    debe_fallar(mutar_lesion(base, "hueso", lambda o: o["veredicto"].__setitem__(o["genes"][0], "diana_recomendada")),
                "un veredicto fuera de vocabulario (web/hueso.json)", "veredicto")

    d = copia(base)
    p = os.path.join(d, "web", "manifiesto.json")
    obj = json.load(open(p))
    obj["sello"] = "sin_verificar 2026-10-17"
    escribe_json(p, obj)
    debe_fallar(d, "una fecha en el manifiesto", "fecha")

    d = copia(base)
    p = os.path.join(d, "web", "manifiesto.json")
    obj = json.load(open(p))
    obj["ficheros"][0]["sha256"] = "0" * 64
    escribe_json(p, obj)
    debe_fallar(d, "sha256 del manifiesto que no cuadra", "sha256")

    d = copia(base)
    p = os.path.join(d, "web", "manifiesto.json")
    obj = json.load(open(p))
    obj["modo_sondas"] = "spliced_parcial"
    escribe_json(p, obj)
    debe_fallar(d, "modo_sondas fuera de vocabulario en el manifiesto", "modo_sondas")

    d = copia(base)
    p = os.path.join(d, "web", "manifiesto.json")
    obj = json.load(open(p))
    del obj["modo_sondas"]
    escribe_json(p, obj)
    debe_fallar(d, "manifiesto sin modo_sondas", "contrato")

    debe_fallar(mutar_lesion(base, "hueso", lambda o: o["tipos"].__setitem__(0, "poolSintetico")),
                "el nombre del pool (en memoria) en un JSON", "identificador_de_muestra_o_pool")

    # A4 · fecha_ok: solo dos estados coherentes del manifiesto; la fecha solo en fecha_ok y con forma AAAA-MM-DD
    def manifiesto(cambios, quitar=()):
        d = copia(base)
        p = os.path.join(d, "web", "manifiesto.json")
        obj = json.load(open(p))
        obj.update(cambios)
        for k in quitar:
            obj.pop(k, None)
        escribe_json(p, obj)
        return d
    publicado = {"sello": "verificado", "ok_titular": True, "fecha_ok": "2026-10-20"}
    try:
        r = C.valida_salidas(manifiesto(publicado), PROHIB, con_informe=False)
        check(r["web/manifiesto.json"]["estado"] == "publicado", "manifiesto publicado (verificado + ok_titular + fecha_ok AAAA-MM-DD) pasa")
    except C.ValidacionFallida as e:
        check(False, "el manifiesto publicado bien formado NO pasa: %s" % e)
    for fecha, desc in (("20-10-2026", "fecha_ok con formato DD-MM-AAAA"), ("2026-10-20T10:00", "fecha_ok con hora"),
                        ("2026-13-45", "fecha_ok que no es fecha de calendario"), ("2026-10-20 ok", "fecha_ok con texto pegado"),
                        ("hoy", "fecha_ok sin fecha"), (20261020, "fecha_ok numérica")):
        debe_fallar(manifiesto({**publicado, "fecha_ok": fecha}), "A4: " + desc)
    debe_fallar(manifiesto({**publicado, "ok_titular": False}), "A4: fecha_ok con ok_titular false", "estado admitido")
    debe_fallar(manifiesto(publicado, quitar=("fecha_ok",)), "A4: ok_titular true sin fecha_ok", "estado admitido")
    debe_fallar(manifiesto({"fecha_ok": "2026-10-20"}), "A4: fecha_ok en el manifiesto del export (sin_verificar)", "estado admitido")
    debe_fallar(manifiesto({**publicado, "sello": "verificado 2026-10-20"}), "A4: una fecha en otra clave sigue cerrando", "fecha")
    debe_fallar(manifiesto({**publicado, "fecha": "2026-10-20"}), "A4: otra clave con fecha (fuera del contrato)")
    debe_fallar(manifiesto({"ok_" + "titular": False}, quitar=("ok_titular",)), "C1: la clave vieja con el nombre de la titular ya no vale")

    # B1/B2/B3 · campos nuevos del contrato web
    lesion_mutada = lambda les, f: mutar_lesion(base, les, f)  # noqa: E731
    debe_fallar(lesion_mutada("higado", lambda o: o["sondas"].__setitem__(o["genes"][0], [4, 3])), "sondas con unión > total", "sondas")
    debe_fallar(lesion_mutada("higado", lambda o: o["sondas"].pop(o["genes"][0])), "sondas sin un gen de la lista", "sondas")
    debe_fallar(lesion_mutada("higado", lambda o: o["seleccion"].__setitem__(o["genes"][0], "favorito")), "papel de selección fuera de vocabulario", "seleccion")
    debe_fallar(lesion_mutada("higado", lambda o: o["loo"].__setitem__("TPBG", {"n": 1, "frac": 0.1, "ref": 0.1, "cp10k_pseudobulk": 1.0})),
                "loo de un gen que no participa en la selección", "loo")
    debe_fallar(lesion_mutada("higado", lambda o: o["loo"]["ESR1"].__setitem__("frac", 1.5)), "loo con fracción fuera de [0,1]", "loo")
    debe_fallar(lesion_mutada("hueso", lambda o: o["loo"].__setitem__("x", {})), "loo en el hueso", "loo")
    debe_fallar(lesion_mutada("hueso", lambda o: o["adn"].__setitem__("frac_lecturas_cell_ranger", 2.0)), "adn con fracción > 1", "adn")
    debe_fallar(lesion_mutada("hueso", lambda o: o.__setitem__("adn", None)), "adn null en el modo todas", "adn")
    debe_fallar(lesion_mutada("hueso", lambda o: o["cnv"].__setitem__("metodo", C.CNV_METODOS["spliced"])),
                "B3: método de CNV «_spliced» en el modo todas", "cnv")
    check("_spliced" not in C.CNV_METODOS["todas"], "B3: el método de CNV del modo todas no lleva «_spliced»")

    # B1 · check de reproducción del acta (unidad): cuadra, no cuadra, y ESR1 del hígado contra su LOO
    fr = {g: v for g, v in C.ACTA_FRAC["higado"].items() if v is not None}
    filas, malos = C.reproduce_acta("higado", {**fr, "ESR1": 0.737}, {"ESR1": {"frac": 0.584}})
    check(not malos and filas["ESR1"]["fuente"].startswith("loo") and filas["ESR1"]["circular_en_el_grupo"] == 0.737,
          "reproduce_acta: ESR1 del hígado se coteja con su LOO (0,584 ≈ 58 %) y deja la cifra circular anotada")
    _, malos = C.reproduce_acta("higado", {**fr, "ESR1": 0.737}, {})
    check(malos == ["ESR1"], "reproduce_acta: sin LOO, ESR1 del hígado NO cuadra (la cifra circular no vale)")
    _, malos = C.reproduce_acta("higado", {**fr, "TPBG": 0.262}, {"ESR1": {"frac": 0.584}})
    check(malos == ["TPBG"], "reproduce_acta: 26,2 % frente a 25 % del acta no cuadra (tolerancia de redondeo)")
    _, malos = C.reproduce_acta("hueso", {**{g: v for g, v in C.ACTA_FRAC["hueso"].items() if v is not None}, "{{DIANA3}}": 1 / 68})
    check(not malos, "reproduce_acta: {{DIANA3}} del hueso 1/68 cuadra con 1,5 % (un decimal)")
    _, malos = C.reproduce_acta("hueso", {g: v for g, v in C.ACTA_FRAC["hueso"].items() if v is not None and g != "ESR1"})
    check(malos == ["ESR1"], "reproduce_acta: un gen del acta que falta en el export no cuadra")
    # B4 · veredictos del hueso desde el acta
    vh = C.veredictos_lesion("hueso", ["ESR1", "CHGA", "SYP", "{{DIANA3}}", "CCND1", "FGFR1", "PRAME", "MAGEA4", "ERBB2", "ROR1", "FOLR1", "ACTB"])
    check(vh == {"ESR1": "sin_veredicto", "CHGA": "marcador_ne", "SYP": "marcador_ne", "{{DIANA3}}": "marcador_ne", "CCND1": "solo_nivel",
                 "FGFR1": "solo_nivel", "PRAME": "cerrado_por_arn", "MAGEA4": "cerrado_por_arn", "ERBB2": "referencia_igual_o_mas",
                 "ROR1": "no", "FOLR1": "no", "ACTB": "sin_veredicto"},
          "B4: veredictos del hueso tomados del acta (l.131/132/99/133; l.102 prevalece; ESR1 sin el matiz del hígado)")
    check(not any(C.VEREDICTO_FUERA_DE_CONTRATO.values()), "B4: ningún gen con veredicto en el acta queda fuera del contrato")
    # Red-team de cifras (17-oct) · «SOSTENIDO con matiz» (HER3 del hígado, l.123) no es lo mismo que «Con matiz»
    # (l.124-127): la web pintaba HER2 (IHQ 0) como «se sostiene».
    vg = C.VEREDICTO_ACTA["higado"]
    check([g for g, v in vg.items() if v[0] == "sostenido_con_matiz"] == ["ERBB3"] and vg["ERBB3"][1] == 123,
          "fallo 1: en el hígado solo HER3 (ERBB3, l.123) es sostenido_con_matiz")
    check(all(vg[g] == ("con_matiz", n) for g, n in (("ESR1", 124), ("ERBB2", 125), ("NECTIN4", 126), ("SLC39A6", 127))),
          "fallo 1: ESR1, HER2, Nectin-4 y LIV-1 del hígado son con_matiz (l.124-127), sin «sostenido»")
    check(all(v[0] != "sostenido_con_matiz" for v in C.VEREDICTO_ACTA["hueso"].values()),
          "fallo 1: el hueso no tiene ningún sostenido_con_matiz (l.102: HER3 del hueso está en el límite)")
    check("ESR1" not in C.VEREDICTO_ACTA["hueso"] and "ESR1" in C.VEREDICTO_NO_TRASLADADO["hueso"],
          "fallo 6: ESR1 del hueso no hereda el matiz del hígado (l.124) y se declara como no trasladado")
    # Red-team de cifras (17-oct) · NY-BR-1 lleva la circularidad de programa que declara el acta (l.117)
    reglas_h = json.load(open(os.path.join(RAIZ, "tools", "flex_analisis", "higado_dianas", "REGLAS_HIGADO_v1.json"), encoding="utf-8"))
    fuera = reglas_h["via1_alta_confianza"]["fuera_del_panel_y_por_que"]
    check(C.CIRCULAR_PROGRAMA_HIGADO == ("ANKRD30A",) and "ANKRD30A" in fuera and "circular" in fuera,
          "fallo 4: ANKRD30A es circular por programa (REGLAS_HIGADO_v1 fuera_del_panel_y_por_que) y el contrato lo declara")
    src_exp = open(os.path.join(EXP, "exporta.py"), encoding="utf-8").read()
    check('"ligado_er": sorted(set(HD.LIGADOS_ER) | set(C.CIRCULAR_PROGRAMA_HIGADO))' in src_exp,
          "fallo 4: exporta.py mete CIRCULAR_PROGRAMA_HIGADO en el papel ligado_er del hígado")
    sel_h = C.seleccion_lesion(["ANKRD30A", "TPBG", "PGR"], {"ligado_er": sorted({"PGR", "AR", "CCND1"} | set(C.CIRCULAR_PROGRAMA_HIGADO))})
    check(sel_h == {"ANKRD30A": "ligado_er", "PGR": "ligado_er"}, "fallo 4: seleccion_lesion rotula ANKRD30A como ligado_er (TPBG no)")

    # 3) exporta.py: sin ventanilla no arranca; registrado en la ventanilla con nescore
    env = dict(os.environ)
    env.pop("BTP_VENTANILLA", None)
    r = subprocess.run([PY, os.path.join(EXP, "exporta.py"), "validar"], env=env, capture_output=True, text=True, timeout=300)
    check(r.returncode == 2 and "ventanilla" in r.stderr, "exporta.py rechaza correr sin BTP_VENTANILLA (rc=2)")
    check(r.stdout.strip() == "", "y no escribe nada en stdout al rechazar")
    src = open(os.path.join(RAIZ, "tools", "lector_clinico.py"), encoding="utf-8").read()
    check('"flex_explorador": ("tools/flex_analisis/explorador/exporta.py"' in src and "nescore" in src.split('"flex_explorador"')[1][:300],
          "flex_explorador registrado en lector_clinico (dict FLEX) con el intérprete de nescore")
    for nombre in ("exporta.py", "_contrato.py"):
        txt = open(os.path.join(EXP, nombre), encoding="utf-8").read()
        check(C._caso_publico()._RE_CODIGO_AP.search(txt) is None and C.RE_BARCODE.search(txt) is None,
              "%s sin ids de bloque ni barcodes en el código" % nombre)


if __name__ == "__main__":
    main()
    print("\n%d comprobaciones, %d fallos" % (total[0], len(fallos)))
    sys.exit(1 if fallos else 0)
