#!/usr/bin/env python
"""Auditoría de especificidad de las sondas Flex v2 de MAGE/PRAME/CTAG (solo datos PÚBLICOS).

Uso:  python -I tools/flex_analisis/b/sondas_magea.py <dir_trabajo> \
          --probeset <csv 10x> --cdna <Homo_sapiens.GRCh38.cdna.all.fa.gz de Ensembl>

1. Probe set público de 10x (Chromium Human Transcriptome Probe Set v2.0.0, GRCh38-2024-A).
2. cDNA de TODOS los transcritos de Ensembl (fichero FTP público; el REST se cayó el 16-oct).
3. Dos barridos por sonda (50 nt) en ambas orientaciones:
   a) FAMILIA: Hamming mínimo en ventana deslizante contra todos los transcritos de los genes
      MAGE*, PRAME*, CTAG*, CSAG*, NDN, NSMCE3, TRO, ANKRD30*, TPBG (sonda entera y mitades 25 nt).
   b) TRANSCRITOMA ENTERO: búsqueda exacta de la sonda entera y de cada mitad (LHS/RHS) sobre
      todos los transcritos (~250k). Las sondas Flex ligan dos mitades: una señal cruzada exige
      que las DOS mitades casen en el mismo gen.
4. Regla PRE-FIJADA: una sonda es «no resoluble a nivel de parálogo» si algún gen distinto del
   objetivo tiene (a) la sonda entera a ≤3 desajustes en la familia, o (b) ambas mitades exactas
   en cualquier gen del transcritoma. Un gen es «no resoluble» si TODAS sus sondas incluidas lo
   son; si solo algunas, se informa qué sondas resuelven (la cifra por gen sale solo de esas).

Salida: <dir_trabajo>/sondas_mage_auditoria.json. Sin datos de la paciente.
"""
import argparse
import bisect
import csv
import gzip
import hashlib
import json
import os
import re
import time

import numpy as np

GENES_OBJETIVO = ["PRAME", "MAGEA4", "MAGEA1", "MAGEA6", "MAGEA10", "MAGEA11", "MAGEA12",
                  "MAGEC1", "MAGEC2", "CTAG2", "ANKRD30A", "TPBG"]
FAMILIA = re.compile(r"^(MAGE|PRAME|CTAG|CSAG|ANKRD30|TPBG$|NDN$|NSMCE3$|TRO$)")
MAX_MM_COMPLETA = 3
COMP = str.maketrans("ACGTN", "TGCAN")


def rc(s):
    return s.translate(COMP)[::-1]


def sha(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def leer_fasta(ruta):
    """-> lista de (transcrito, gen_symbol, gene_id, secuencia)."""
    out = []
    nombre = sym = gid = None
    buf = []
    op = gzip.open if ruta.endswith(".gz") else open
    with op(ruta, "rt") as fh:
        for line in fh:
            if line.startswith(">"):
                if nombre:
                    out.append((nombre, sym, gid, "".join(buf).upper()))
                h = line[1:].split()
                nombre = h[0]
                sym = gid = ""
                for t in h[1:]:
                    if t.startswith("gene_symbol:"):
                        sym = t.split(":", 1)[1]
                    elif t.startswith("gene:"):
                        gid = t.split(":", 1)[1].split(".")[0]
                buf = []
            else:
                buf.append(line.strip())
    if nombre:
        out.append((nombre, sym, gid, "".join(buf).upper()))
    return out


def min_hamming(probe, seq):
    L = len(probe)
    if len(seq) < L:
        return L
    a = np.frombuffer(seq.encode(), dtype=np.uint8)
    p = np.frombuffer(probe.encode(), dtype=np.uint8)
    w = np.lib.stride_tricks.sliding_window_view(a, L)
    return int((w != p).sum(axis=1).min())


class Transcritoma:
    """Concatenación de todos los transcritos con separador; búsqueda exacta -> genes."""

    def __init__(self, registros):
        partes, self.offs, self.genes = [], [], []
        pos = 0
        for nombre, sym, gid, seq in registros:
            partes.append(seq)
            self.offs.append(pos)
            self.genes.append(sym or gid)
            pos += len(seq) + 1
        self.texto = "#".join(partes)

    def genes_con(self, k):
        hits = set()
        for q in (k, rc(k)):
            i = self.texto.find(q)
            while i != -1:
                hits.add(self.genes[bisect.bisect_right(self.offs, i) - 1])
                i = self.texto.find(q, i + 1)
        return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dir_trabajo")
    ap.add_argument("--probeset", required=True)
    ap.add_argument("--cdna", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.dir_trabajo, exist_ok=True)

    with open(a.probeset) as fh:
        filas = [l for l in fh if not l.startswith("#")]
    sondas = [r for r in csv.DictReader(filas) if r["gene_name"] in GENES_OBJETIVO]

    registros = leer_fasta(a.cdna)
    familia = [r for r in registros if FAMILIA.match(r[1] or "")]
    tx = Transcritoma(registros)
    genes_familia = sorted({r[1] for r in familia})

    resultados = []
    for r in sondas:
        p = r["probe_seq"].upper()
        lhs, rhs = p[:25], p[25:]
        objetivo = r["gene_name"]
        hits = {}
        for nombre, sym, gid, seq in familia:
            best = min(min_hamming(p, seq), min_hamming(p, rc(seq)))
            if best <= 10:
                h = hits.setdefault(sym, {"mm_completa": 50, "mm_lhs": 25, "mm_rhs": 25})
                h["mm_completa"] = min(h["mm_completa"], best)
                h["mm_lhs"] = min(h["mm_lhs"], min_hamming(lhs, seq), min_hamming(lhs, rc(seq)))
                h["mm_rhs"] = min(h["mm_rhs"], min_hamming(rhs, seq), min_hamming(rhs, rc(seq)))
        g_full = tx.genes_con(p)
        g_lhs = tx.genes_con(lhs)
        g_rhs = tx.genes_con(rhs)
        ambas = sorted((g_lhs & g_rhs) - {objetivo})
        cruz_fam = sorted(g for g, h in hits.items() if g != objetivo and h["mm_completa"] <= MAX_MM_COMPLETA)
        propio = hits.get(objetivo, {}).get("mm_completa", 50)
        cruzados = sorted(set(cruz_fam) | set(ambas))
        resultados.append({
            "gen": objetivo, "probe_id": r["probe_id"], "included": r["included"], "region": r["region"],
            "mm_propio_familia": propio, "exacta_en_objetivo_transcritoma": objetivo in g_full,
            "cruza_familia_<=3mm": cruz_fam, "ambas_mitades_exactas_en_otro_gen": ambas,
            "lhs_exacta_en": sorted(g_lhs - {objetivo}), "rhs_exacta_en": sorted(g_rhs - {objetivo}),
            "resoluble": not cruzados and (propio == 0 or objetivo in g_full),
            "hits_familia": hits})

    por_gen = {}
    for g in GENES_OBJETIVO:
        rs = [x for x in resultados if x["gen"] == g and x["included"] == "TRUE"]
        if not rs:
            por_gen[g] = {"estado": "fuera del panel" if not any(x["gen"] == g for x in resultados)
                          else "sondas excluidas por 10x", "n_sondas": 0}
            continue
        ok = [x["probe_id"] for x in rs if x["resoluble"]]
        por_gen[g] = {"n_sondas": len(rs), "resolubles": ok,
                      "no_resolubles": [[x["probe_id"], x["cruza_familia_<=3mm"],
                                         x["ambas_mitades_exactas_en_otro_gen"]] for x in rs if not x["resoluble"]],
                      "estado": "resoluble" if len(ok) == len(rs) else
                      ("parcial" if ok else "no resoluble a nivel de parálogo")}

    salida = {"probeset_sha256": sha(a.probeset), "cdna_sha256": sha(a.cdna), "cdna_fichero": os.path.basename(a.cdna),
              "fecha": time.strftime("%Y-%m-%d"),
              "regla": {"max_mm_completa_familia": MAX_MM_COMPLETA, "o_bien": "ambas mitades exactas en otro gen"},
              "n_transcritos_total": len(registros), "genes_familia": genes_familia,
              "n_transcritos_familia": len(familia), "por_gen": por_gen, "sondas": resultados}
    out = os.path.join(a.dir_trabajo, "sondas_mage_auditoria.json")
    json.dump(salida, open(out, "w"), indent=1, ensure_ascii=False)
    print(json.dumps({"por_gen": por_gen, "n_transcritos_total": len(registros),
                      "n_genes_familia": len(genes_familia), "salida": out, "sha256": sha(out)},
                     indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
