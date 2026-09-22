#!/usr/bin/env python3
"""
test_datos_reales.py — valida el pipeline contra un caso humano REAL y PÚBLICO.

Datos: dataset abierto de Sid {{CONTACTO}} (osteosarcoma), bucket S3 público, licencia
CC0 1.0 según el AWS Open Data Registry. NADA de {{TITULAR}} entra aquí.

Se SALTA LIMPIO (exit 0) si los ficheros no están en disco, para no obligar a nadie a
descargar 180 MB ni romper CI. Cómo conseguirlos: ver
  _cajita/datos_publicos/sid_osteosarc/README.md   (fuera de git)

Qué comprueba (control de cordura, no de calidad clínica):
  1. Se leen variantes del VCF preparado (> 0) y péptidos (> 0).
  2. Los 5 alelos HLA de OptiType se parsean y coinciden con los que usó pVACseq.
  3. El VAF sale de la muestra TUMORAL, no de la normal.
  4. Todo péptido generado contiene el cambio y NO existe en la proteína de referencia.
  5. Solapamiento con la referencia pVACseq publicada en el mismo bucket.
  6. Egress CERO: ninguna URL de las entradas/salidas pasaría el muro.

Correr:  .venv-pipeline/bin/python pipeline/bin/test_datos_reales.py
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "pipeline" / "bin"))

DATOS = Path("/Users/polaris/claudecode/_cajita/datos_publicos/sid_osteosarc")
PROTEOMA = Path("/Users/polaris/claudecode/_cajita/datos_publicos/ensembl_113/"
                "Homo_sapiens.GRCh38.pep.all.fa.gz")
VCF_PREP = DATOS / "preparado" / "sid_ucla2025_01.pep.vcf"
HLA = DATOS / "preparado" / "sid.hla.txt"
REF_PVAC = DATOS / "referencia_pvacseq" / "SG.WGS_SG.WGS.UCLA.2025.01.tumor.all_epitopes.tsv"

HLA_ESPERADO = ["HLA-A*01:01", "HLA-B*08:01", "HLA-B*27:05", "HLA-C*01:02", "HLA-C*07:01"]

fallos = 0


def check(cond, msg):
    global fallos
    print(("  OK   " if cond else "  FALLO ") + msg)
    if not cond:
        fallos += 1


def main() -> int:
    faltan = [p for p in (VCF_PREP, HLA, REF_PVAC, PROTEOMA) if not p.exists()]
    if faltan:
        print("== test con datos reales (Sid, osteosarcoma, CC0) ==")
        print("  SALTADO: no están en disco -> " + ", ".join(p.name for p in faltan))
        print("  (cómo obtenerlos: _cajita/datos_publicos/sid_osteosarc/README.md)")
        return 0

    import leer_entradas as ent
    from preparar_reales import leer_proteoma
    from muro import host_permitido

    print("== test con datos reales (Sid, osteosarcoma, CC0) ==")

    variantes = ent.leer_variantes(VCF_PREP)
    n_var = len({(v["chrom"], v["pos"], v["alt"]) for v in variantes})
    peps = {v["peptide"] for v in variantes if v.get("peptide")}
    check(n_var > 0, f"variantes leídas del VCF real: {n_var}")
    check(len(peps) > 0, f"péptidos mutantes generados: {len(peps)}")
    check(all(8 <= len(p) <= 11 for p in peps), "todos los péptidos miden 8-11 aa")

    alelos = ent.clase1(ent.leer_hla(HLA))
    check(alelos == HLA_ESPERADO, f"HLA-I parseado = el de pVACseq/OptiType: {alelos}")

    # VAF: la muestra tumoral, no la normal (el fallo real del 19-sep-26).
    afs = [v["af"] for v in variantes if v.get("af") is not None]
    check(len(afs) == len(variantes), "toda variante trae VAF")
    check(any(a > 0.02 for a in afs), f"VAF de la muestra TUMORAL (máx={max(afs):.3f})")

    # Cada péptido contiene el cambio y no existe en la proteína de referencia.
    prot, _ = leer_proteoma(PROTEOMA)
    import pysam
    malos, revisados = [], 0
    v = pysam.VariantFile(str(VCF_PREP))
    for rec in v:
        wt = prot.get(rec.info["ENSP"])
        if not wt:
            continue
        for p in rec.info["PEP"]:
            revisados += 1
            if p in wt:
                malos.append((rec.info["GENE"], p))
    v.close()
    check(revisados > 0 and not malos,
          f"ningún péptido ({revisados} revisados) existe en la proteína WT "
          f"{'' if not malos else '-> ' + str(malos[:3])}")

    # Solapamiento con pVACseq (referencia publicada en el mismo bucket).
    import csv
    pv_por_var: dict[tuple, set] = {}
    with open(REF_PVAC) as fh:
        for fila in csv.DictReader(fh, delimiter="\t"):
            ref, alt = fila["Reference"], fila["Variant"]
            # pVACseq da Start 0-based en SNV/MNV; en indel, Start = POS del VCF.
            pos = int(fila["Start"]) + (0 if len(ref) != len(alt) else 1)
            k = (fila["Chromosome"], pos)
            pv_por_var.setdefault(k, set())
            if 8 <= int(fila["Peptide Length"]) <= 11:
                pv_por_var[k].add(fila["MT Epitope Seq"])
    pv_keys = set(pv_por_var)
    nuestras_keys = {(v["chrom"], v["pos"]) for v in variantes}
    comunes_var = pv_keys & nuestras_keys
    # Solo se mide el generador de péptidos donde AMBOS tienen la variante: si la
    # variante no está (NME1, perdida por --per_gene aguas arriba), sus péptidos
    # tampoco, y mezclarlo escondería cuál de los dos fallos es cuál.
    pv_pep = {p for k in comunes_var for p in pv_por_var[k]}
    check(len(comunes_var) >= 15,
          f"variantes de pVACseq recuperadas: {len(comunes_var)}/{len(pv_keys)} "
          "(falta NME1: el VEP del dataset corrió con --per_gene)")
    peps_comunes = {p for p in pv_pep if p in peps}
    check(len(peps_comunes) == len(pv_pep),
          f"péptidos 8-11 de pVACseq reproducidos: {len(peps_comunes)}/{len(pv_pep)}")

    # Egress cero: nada de esto sale, y el muro lo confirmaría.
    check(not host_permitido("https://sid-contacto-osteosarc-dataset.s3.us-west-2.amazonaws.com/x"),
          "el muro bloquea subir nada al bucket (egress cero)")
    check(not host_permitido("https://www.ebi.ac.uk/Tools/services/rest/emboss_needle/run"),
          "el muro sigue bloqueando alineamiento remoto")

    print(f"\n=== datos reales: {'TODO OK' if fallos == 0 else str(fallos) + ' FALLO(S)'} ===")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
