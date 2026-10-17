"""Auditoría de especificidad de sondas (tabla B; espec.: obligatoria antes de cualquier cifra MAGEA).

Cada sonda (50 nt, dos mitades ligadas de 25) se alinea contra TODOS los transcritos públicos de
Ensembl 110 (cDNA + ncRNA), en ambas orientaciones, admitiendo ≤2 desapareamientos (búsqueda
exhaustiva por semillas: con ≤2 fallos en 50 nt, al menos una de 3 semillas de ~16 nt es exacta).
Control positivo: cada sonda debe casar con 0 fallos con un transcrito de su propio gen.
Solo usa secuencias públicas del panel y de Ensembl: ningún dato de la paciente.
Salida: analisis_A/paralogos_sondas.csv y paralogos.json.
"""
import bisect
import gzip
import os

import pandas as pd

from _comun import BASE, REF, guarda_json, out

GENES_B = ["PRAME", "MAGEA4", "MAGEA1", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12", "MAGEC1",
           "MAGEC2", "CTAG2", "ANKRD30A", "TPBG"]
COMP = str.maketrans("ACGT", "TGCA")


def rc(s):
    return s.translate(COMP)[::-1]


def carga_transcritos():
    seqs, genes = [], []
    for fn in ("Homo_sapiens.GRCh38.cdna.all.fa.gz", "Homo_sapiens.GRCh38.ncrna.fa.gz"):
        with gzip.open(os.path.join(REF, fn), "rt") as f:
            cur, g = [], None
            for line in f:
                if line.startswith(">"):
                    if g is not None:
                        seqs.append("".join(cur)); genes.append(g)
                    h = line[1:].strip()
                    g = h.split("gene_symbol:", 1)[1].split()[0] if "gene_symbol:" in h else \
                        h.split("gene:", 1)[1].split()[0]
                    cur = []
                else:
                    cur.append(line.strip().upper())
            if g is not None:
                seqs.append("".join(cur)); genes.append(g)
    starts, pos = [], 0
    for s in seqs:
        starts.append(pos)
        pos += len(s) + 1
    big = "#".join(seqs)
    return big, starts, genes


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def busca(probe, big, starts, genes, maxmm=2):
    """{gen: min_desapareamientos} para hits ≤maxmm de probe (50 nt) en big."""
    L = len(probe)
    cortes = [(0, L // 3), (L // 3, 2 * L // 3), (2 * L // 3, L)]
    hits = {}
    for a, b in cortes:
        seed = probe[a:b]
        i = big.find(seed)
        while i != -1:
            ini = i - a
            if ini >= 0 and ini + L <= len(big):
                ventana = big[ini:ini + L]
                if "#" not in ventana:
                    mm = hamming(probe, ventana)
                    if mm <= maxmm:
                        t = bisect.bisect_right(starts, ini) - 1
                        g = genes[t]
                        hits[g] = min(hits.get(g, 99), mm)
            i = big.find(seed, i + 1)
    return hits


def estricta(df):
    """Regla estricta (declarada viendo solo distancias de secuencia): específica si no casa con otro
    gen con ≤2 fallos Y el parálogo más cercano está a ≥5 fallos con ≥2 en cada mitad de 25 nt."""
    return (df["especifica"] & (df["fallos_total"] >= 5)
            & (df[["fallos_mitad1", "fallos_mitad2"]].min(axis=1) >= 2))


def rehace_resumen():
    """Rehace paralogos.json desde paralogos_sondas.csv con la regla estricta (sin repetir la búsqueda)."""
    df = pd.read_csv(out("paralogos_sondas.csv"))
    df["especifica_estricta"] = estricta(df)
    df.to_csv(out("paralogos_sondas.csv"), index=False)
    o = "antisentido" if df["propio_0mm_antisentido"].mean() > df["propio_0mm_sentido"].mean() else "sentido"
    res = {"orientacion_que_casa_con_su_gen": o,
           "control_positivo_frac_sondas_0mm_con_su_gen": float(df[f"propio_0mm_{o}"].mean()),
           "regla": "específica = sin otro gen a ≤2 fallos (búsqueda exhaustiva Ensembl 110 cDNA+ncRNA) y parálogo CT más cercano a ≥5 fallos con ≥2 en cada mitad"}
    for g in GENES_B:
        d = df[df["gen"] == g]
        if d.empty:
            res[g] = "fuera del panel"
            continue
        ok = d["especifica_estricta"] & d["included"]
        res[g] = {"n_sondas_incluidas": int(d["included"].sum()), "n_especificas_estrictas": int(ok.sum()),
                  "paralogo_mas_cercano_por_sonda": [f"{a}:{b} ({c1}+{c2})" for a, b, c1, c2 in
                                                     zip(d["paralogo_mas_cercano"], d["fallos_total"], d["fallos_mitad1"], d["fallos_mitad2"])],
                  "veredicto": "resoluble a nivel de parálogo" if ok.any() else "no resoluble a nivel de parálogo"}
    guarda_json(res, "paralogos.json")
    for k, v in res.items():
        print(k, v if not isinstance(v, dict) else (v.get("veredicto"), v.get("n_especificas_estrictas")))


def run(*a):
    if a and a[0] == "resumen":
        return rehace_resumen()
    ps = pd.read_csv(os.path.join(BASE, "MP", "outs", "probe_set.csv"), comment="#")
    fam = ps["gene_name"].str.match(r"^(MAGE|CTAG|PRAME|ANKRD30|TPBG)")
    sel = ps[ps["gene_name"].isin(GENES_B) | fam].copy()
    big, starts, genes = carga_transcritos()
    print("transcritos cargados:", len(genes), "| sondas a auditar:", len(sel))
    filas = []
    for _, r in sel.iterrows():
        s = r["probe_seq"].upper()
        hf = busca(s, big, starts, genes)
        hr = busca(rc(s), big, starts, genes)
        filas.append({"probe_id": r["probe_id"], "gen": r["gene_name"], "included": bool(r["included"]),
                      "region": r["region"],
                      "hits_sentido": ";".join(f"{g}:{m}" for g, m in sorted(hf.items())),
                      "hits_antisentido": ";".join(f"{g}:{m}" for g, m in sorted(hr.items())),
                      "propio_0mm_sentido": hf.get(r["gene_name"], 99) == 0,
                      "propio_0mm_antisentido": hr.get(r["gene_name"], 99) == 0,
                      "otros_genes_sentido": ";".join(sorted(g for g in hf if g != r["gene_name"])),
                      "otros_genes_antisentido": ";".join(sorted(g for g in hr if g != r["gene_name"]))})
    df = pd.DataFrame(filas)
    # Parte 2: distancia exhaustiva (fuerza bruta, cualquier nº de fallos) al parálogo más cercano
    # dentro de las familias CT (MAGE/CTAG/CSAG/XAGE/GAGE/PAGE/SSX/PRAME), por mitad de la sonda.
    import numpy as np
    import re
    fam_re = re.compile(r"^(MAGE|CTAG|CSAG|XAGE|GAGE|PAGE|SSX|PRAME)")
    fam_tx = [(g, big[starts[i]:(starts[i + 1] - 1 if i + 1 < len(starts) else len(big))])
              for i, g in enumerate(genes) if fam_re.match(g)]
    lut = np.frombuffer(b"ACGTN#", dtype=np.uint8)
    def enc(x):
        return np.frombuffer(x.encode(), dtype=np.uint8)
    cerc = []
    for _, r in sel.iterrows():
        pr = rc(r["probe_seq"].upper())  # orientación del transcrito (la sonda es antisentido)
        pa = enc(pr)
        L = len(pa)
        mejor = (99, 99, 99, "")
        for g, tx in fam_tx:
            if g == r["gene_name"] or len(tx) < L:
                continue
            ta = enc(tx)
            w = np.lib.stride_tricks.sliding_window_view(ta, L)
            mm = (w != pa)
            tot = mm.sum(axis=1)
            j = int(tot.argmin())
            if tot[j] < mejor[0]:
                mejor = (int(tot[j]), int(mm[j, :L // 2].sum()), int(mm[j, L // 2:].sum()), g)
        cerc.append(mejor)
    df["paralogo_mas_cercano"] = [c[3] for c in cerc]
    df["fallos_total"] = [c[0] for c in cerc]
    df["fallos_mitad1"] = [c[1] for c in cerc]
    df["fallos_mitad2"] = [c[2] for c in cerc]
    sentido = df["propio_0mm_sentido"].mean() >= df["propio_0mm_antisentido"].mean()
    o = "sentido" if sentido else "antisentido"
    df["otros_genes"] = df[f"otros_genes_{o}"]
    df["especifica"] = df["otros_genes"] == ""
    df.to_csv(out("paralogos_sondas.csv"), index=False)
    resumen = {"orientacion_que_casa_con_su_gen": o,
               "control_positivo_frac_sondas_0mm_con_su_gen": float(df[f"propio_0mm_{o}"].mean())}
    for g in GENES_B:
        d = df[df["gen"] == g]
        if d.empty:
            resumen[g] = "fuera del panel"
            continue
        resumen[g] = {"n_sondas": int(len(d)), "n_incluidas": int(d["included"].sum()),
                      "n_especificas_incluidas": int((d["especifica"] & d["included"]).sum()),
                      "control_positivo_ok": int(d[f"propio_0mm_{o}"].sum()),
                      "otros_genes_casados": sorted({x for v in d["otros_genes"] for x in v.split(";") if x}),
                      "paralogo_mas_cercano_por_sonda": [f"{a}:{b} ({c1}+{c2})" for a, b, c1, c2 in
                                                         zip(d["paralogo_mas_cercano"], d["fallos_total"], d["fallos_mitad1"], d["fallos_mitad2"])],
                      "veredicto": ("resoluble (≥1 sonda incluida específica)"
                                    if (d["especifica"] & d["included"]).any()
                                    else "no resoluble a nivel de parálogo")}
    guarda_json(resumen, "paralogos.json")
    for k, v in resumen.items():
        print(k, v)
