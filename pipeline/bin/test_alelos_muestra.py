#!/usr/bin/env python3
"""
test_alelos_muestra.py — auditoría externa del 22-sep-26, hallazgos 2.1, 2.2 y 2.3.

Todo con VCF y proteoma SINTÉTICOS generados aquí (nunca datos reales):
  2.1  cada ALT de un multialélico se anota con SU consecuencia, nunca con la de otro;
       multialélico con CSQ solo del ALT2, indel, transcritos alternativos.
  2.2  una sola regla de muestra tumoral: 2 muestras sin cabecera PARA; con cabecera
       elige la correcta; lo elegido queda en la salida.
  2.3  la config se lee del YAML y se valida; af_min se aplica; TPM ausente = no medido.

Correr:  .venv-pipeline/bin/python pipeline/bin/test_alelos_muestra.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "pipeline" / "bin"))

import correr_pipeline as cp          # noqa: E402
import leer_entradas as ent           # noqa: E402
import preparar_reales as pr          # noqa: E402
from muestras import MuestraAmbigua   # noqa: E402

fallos = 0


def check(cond, msg):
    global fallos
    print(("  OK   " if cond else "  FALLO ") + msg)
    if not cond:
        fallos += 1


TMP = Path(tempfile.mkdtemp(prefix="test_alelos_muestra_"))

# Proteínas inventadas (sin repeticiones de 8-mer, para que ninguna ventana mutante
# exista por casualidad en la WT). P1 = transcrito canónico, P2 = alternativo.
P1 = "MSTNPKPQRKTKRNTNRRPQDVKFPGGGQIVGGVYLLPRRGPRLGVRATRKTSERSQPRGRRQPIPKAWE"
P2 = "MDEHLCFYWAGINVTSRQKPMLDEHCFYWAGINVTSRQKPLMAC"
FASTA = TMP / "prot.fa"
FASTA.write_text(
    ">ENSP00000000001.1 pep chromosome:SINT:1:1:1:1 gene:ENSG00000000001.1 "
    "transcript:ENST00000000001.1 gene_symbol:SINTA\n" + P1 + "\n"
    ">ENSP00000000002.1 pep chromosome:SINT:1:1:1:1 gene:ENSG00000000001.1 "
    "transcript:ENST00000000002.1 gene_symbol:SINTA\n" + P2 + "\n")

CSQ_FMT = ("Allele|Consequence|SYMBOL|Gene|Feature|ENSP|Protein_position|Amino_acids|"
           "HGVSp|CANONICAL|MANE_SELECT")


def csq(allele, conseq, pos, aa, tx=1, canon="YES", mane="NM_SINT.1"):
    return "|".join([allele, conseq, "SINTA", "ENSG00000000001", f"ENST0000000000{tx}",
                     f"ENSP0000000000{tx}", pos, aa, "", canon, mane])


def vcf_vep(nombre, filas, muestras=("TUMOR",), cabecera_extra=()):
    """filas: (pos, ref, alts, [csq...], [af_por_muestra])."""
    cab = ["##fileformat=VCFv4.2", "##contig=<ID=chrS>",
           f'##INFO=<ID=CSQ,Number=.,Type=String,Description="Consequence annotations from '
           f'Ensembl VEP. Format: {CSQ_FMT}">',
           '##FORMAT=<ID=AF,Number=A,Type=Float,Description="VAF">',
           '##FILTER=<ID=PASS,Description="All filters passed">', *cabecera_extra,
           "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(muestras)]
    lineas = []
    for pos, ref, alts, csqs, afs in filas:
        lineas.append("\t".join(["chrS", str(pos), ".", ref, ",".join(alts), ".", "PASS",
                                 "CSQ=" + ",".join(csqs), "AF", *afs]))
    ruta = TMP / nombre
    ruta.write_text("\n".join(cab + lineas) + "\n")
    return str(ruta)


def leer_salida(ruta):
    """{(pos, alt): (TX, set(PEP), AF)} del VCF preparado."""
    import pysam
    out = {}
    v = pysam.VariantFile(ruta)
    for rec in v:
        af = rec.samples["TUMOR"]["AF"]
        out[(rec.pos, rec.alts[0])] = (rec.info["TX"], set(rec.info["PEP"]),
                                       af[0] if isinstance(af, tuple) else af)
    v.close()
    return out


def descartes(ruta):
    filas = Path(ruta + ".descartes.tsv").read_text().splitlines()[1:]
    return {(int(f.split("\t")[1]), f.split("\t")[3]): f.split("\t")[4] for f in filas}


# ─────────────────────────────────────────────────────────────── 2.1 alelos
print("== 2.1: representación VEP de los alelos ==")
check(pr._alelos_vep("C", ["A", "G"]) == ["A", "G"], "multialélico SNV: ALT tal cual")
check(pr._alelos_vep("GAAA", ["G"]) == ["-"], "deleción bialélica: '-'")
check(pr._alelos_vep("G", ["GTTT"]) == ["TTT"], "inserción bialélica: sin anclaje")
check(pr._alelos_vep("C", ["CA", "CAA", "A"]) == ["CA", "CAA", "A"],
      "multialélico con anclajes distintos: no se recorta ninguno (visto en VEP real)")
check(pr._alelos_vep("GGT", ["G", "GGTGT"]) == ["-", "GTGT"],
      "multialélico con anclaje común: se recorta en todos (visto en VEP real)")

print("== 2.1: cada ALT con SU consecuencia ==")
# Aminoácidos de referencia sacados de la propia WT (1-based), para que el control
# anti off-by-one de preparar_reales no descarte nada por error del test.
r10, r46, r20, r5b = P1[9], P1[45], P1[19], P2[4]
ruta = vcf_vep("vep.vcf", [
    # (a) multialélico, CSQ SOLO para ALT2 (G). ALT1 (A) no puede heredar nada.
    (1000, "C", ["A", "G"], [csq("G", "missense_variant", "10", f"{r10}/W")], ["0.1,0.3"]),
    # (b) multialélico, CSQ distinta para cada ALT: cada uno su péptido.
    (2000, "G", ["T", "C"], [csq("T", "missense_variant", "46", f"{r46}/Y"),
                             csq("C", "missense_variant", "46", f"{r46}/H")], ["0.2,0.4"]),
    # (c) indel multialélico con anclaje común: CSQ solo de la inserción in-frame.
    (3000, "GAAA", ["G", "GAAAAAA"], [csq("AAAAAA", "inframe_insertion", "30-31", "-/W")],
     ["0.15,0.25"]),
    # (d) deleción in-frame bialélica.
    (4000, "GCAG", ["G"], [csq("-", "inframe_deletion", "20", f"{r20}/-")], ["0.33"]),
    # (e) transcritos alternativos: el alternativo va PRIMERO en CSQ; debe ganar el MANE.
    (5000, "A", ["T"], [csq("T", "missense_variant", "5", f"{r5b}/W", tx=2, canon="", mane=""),
                        csq("T", "missense_variant", "10", f"{r10}/W")], ["0.5"]),
    # (f) missense SOLO en el transcrito alternativo (intrónica en el canónico): se toma.
    (6000, "A", ["C"], [csq("C", "intron_variant", "", ""),
                        csq("C", "missense_variant", "5", f"{r5b}/W", tx=2, canon="", mane="")],
     ["0.6"]),
])
sal = str(TMP / "prep.vcf")
r = pr.anotar_vcf(ruta, str(FASTA), sal)
o = leer_salida(sal)
d = descartes(sal)

check((1000, "G") in o and all("W" in p for p in o[(1000, "G")][1]),
      "(a) ALT2 con CSQ propia: todos sus péptidos llevan el cambio (W)")
check((1000, "A") not in o, "(a) ALT1 SIN CSQ propia NO recibe el péptido de ALT2")
check(d.get((1000, "A")) == "sin_csq_del_alelo", "(a) ALT1 descartado con motivo registrado")
check(abs(o.get((1000, "G"), (0, 0, 0))[2] - 0.3) < 1e-6, "(a) AF del ALT2 = 0.3 (no la del ALT1)")

pl, pp = o.get((2000, "T"), (0, set(), 0))[1], o.get((2000, "C"), (0, set(), 0))[1]
check(pl and pp and not (pl & pp), "(b) cada ALT con sus propios péptidos, sin mezcla")
check(all("Y" in p for p in pl) and all("H" in p for p in pp),
      "(b) péptidos de T llevan Y y los de C llevan H")
check(abs(o.get((2000, "C"), (0, 0, 0))[2] - 0.4) < 1e-6, "(b) AF del ALT2 = 0.4")

check((3000, "GAAAAAA") in o, "(c) inserción in-frame del multialélico anotada")
check((3000, "G") not in o and d.get((3000, "G")) == "sin_csq_del_alelo",
      "(c) la deleción sin CSQ propia NO toma la de la inserción")
check(abs(o.get((3000, "GAAAAAA"), (0, 0, 0))[2] - 0.25) < 1e-6, "(c) AF del ALT2 = 0.25")

pep_d = o.get((4000, "G"), (0, set(), 0))[1]
check(bool(pep_d) and all(p not in P1 for p in pep_d), "(d) deleción in-frame: péptidos de unión")

check(o.get((5000, "T"), ("",))[0] == "ENST00000000001",
      "(e) transcritos alternativos: gana el MANE/canónico aunque vaya segundo")
check(o.get((6000, "C"), ("",))[0] == "ENST00000000002",
      "(f) missense solo en el alternativo: se anota con el alternativo")
check(r.get("registros_multialelicos") == 3 and r.get("alelos") == 9,
      f"recuento: 3 multialélicos, 9 alelos ({r.get('registros_multialelicos')}, {r.get('alelos')})")

# ─────────────────────────────────────────────────────────────── 2.2 muestra
print("== 2.2: muestra tumoral ==")
fila = [(1000, "C", ["G"], [csq("G", "missense_variant", "10", f"{r10}/W")], ["0.40", "0.01"])]
dos = vcf_vep("dos.vcf", fila, muestras=("TUMOR", "NORMAL"))
try:
    pr.anotar_vcf(dos, str(FASTA), str(TMP / "x.vcf"))
    check(False, "preparar_reales: 2 muestras sin cabecera deberían PARAR")
except SystemExit as e:
    check("##tumor_sample" in str(e), "preparar_reales: 2 muestras sin cabecera -> PARA")

con = vcf_vep("con.vcf", fila, muestras=("TUMOR", "NORMAL"),
              cabecera_extra=("##tumor_sample=TUMOR", "##normal_sample=NORMAL"))
r2 = pr.anotar_vcf(con, str(FASTA), str(TMP / "con.prep.vcf"))
check(r2["muestra_tumor"] == "TUMOR", "preparar_reales: con cabecera elige TUMOR (1ª columna)")
check(abs(leer_salida(str(TMP / "con.prep.vcf"))[(1000, "G")][2] - 0.40) < 1e-6,
      "preparar_reales: el AF es el del tumor (0.40), no el de la normal")
txt = (TMP / "con.prep.vcf").read_text()
check("##preparar_reales_muestra_tumor=TUMOR" in txt
      and "##preparar_reales_muestra_tumor_motivo=cabecera ##tumor_sample" in txt,
      "preparar_reales: muestra y motivo quedan escritos en la salida")

mala = vcf_vep("mala.vcf", fila, muestras=("TUMOR", "NORMAL"),
               cabecera_extra=("##tumor_sample=OTRA",))
try:
    pr.anotar_vcf(mala, str(FASTA), str(TMP / "y.vcf"))
    check(False, "cabecera que apunta a muestra inexistente debería PARAR")
except SystemExit:
    check(True, "preparar_reales: ##tumor_sample inexistente -> PARA")

r3 = pr.anotar_vcf(dos, str(FASTA), str(TMP / "exp.vcf"), muestra_tumor="TUMOR")
check(r3["muestra_tumor"] == "TUMOR" and "indicada" in r3["muestra_tumor_motivo"],
      "preparar_reales: --muestra-tumor explícita desbloquea el caso sin cabecera")


def vcf_pep(nombre, muestras, extra=(), alts="G"):
    cab = ["##fileformat=VCFv4.2", "##contig=<ID=chrS>",
           '##INFO=<ID=GENE,Number=1,Type=String,Description="g">',
           '##INFO=<ID=AA,Number=1,Type=String,Description="a">',
           '##INFO=<ID=PEP,Number=.,Type=String,Description="p">',
           '##FORMAT=<ID=AF,Number=A,Type=Float,Description="VAF">', *extra,
           "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(muestras)]
    afs = ["0.40", "0.01"][:len(muestras)] if alts == "G" else ["0.40,0.2", "0.01,0.0"]
    fila = "\t".join(["chrS", "1000", ".", "C", alts, ".", "PASS",
                      "GENE=SINTA;AA=p.K10E;PEP=SINTPEPAA", "AF", *afs[:len(muestras)]])
    ruta = TMP / nombre
    ruta.write_text("\n".join(cab + [fila]) + "\n")
    return ruta


try:
    ent.leer_variantes(vcf_pep("p2.vcf", ("NORMAL", "TUMOR")))
    check(False, "leer_entradas: 2 muestras sin cabecera deberían PARAR")
except MuestraAmbigua:
    check(True, "leer_entradas: 2 muestras sin cabecera -> PARA (misma regla)")
vs, meta = ent.leer_variantes_meta(vcf_pep("p2c.vcf", ("NORMAL", "TUMOR"),
                                           extra=("##tumor_sample=TUMOR",)))
check(meta["muestra_tumor"] == "TUMOR" and abs(vs[0]["af"] - 0.01) < 1e-6,
      "leer_entradas: con cabecera lee el AF de TUMOR (2ª columna aquí)")
try:
    ent.leer_variantes(vcf_pep("pm.vcf", ("TUMOR",), alts="G,T"))
    check(False, "leer_entradas: multialélico con PEP debería PARAR")
except ent.EntradaNoAnotada:
    check(True, "leer_entradas: multialélico con un solo PEP -> PARA")

# Issue #8: una muestra única no permite contradecir su etiqueta de normal.
normal_pep = vcf_pep("normal_unica_pep.vcf", ("NORMAL",),
                     extra=("##normal_sample=NORMAL",))
try:
    ent.leer_variantes_meta(normal_pep)
    check(False, "leer_entradas: muestra única marcada normal debería PARAR")
except MuestraAmbigua as e:
    check("##normal_sample" in str(e),
          "leer_entradas: muestra única normal -> error que identifica la contradicción")

normal_vep = vcf_vep(
    "normal_unica_vep.vcf",
    [(1000, "C", ["G"], [csq("G", "missense_variant", "10", f"{r10}/W")], ["0.40"])],
    muestras=("NORMAL",), cabecera_extra=("##normal_sample=NORMAL",))
normal_salida = TMP / "normal_unica.prep.vcf"
try:
    pr.anotar_vcf(normal_vep, str(FASTA), str(normal_salida))
    check(False, "preparar_reales: muestra única marcada normal debería PARAR")
except SystemExit as e:
    check("##normal_sample" in str(e),
          "preparar_reales: muestra única normal -> error que identifica la contradicción")
check(not normal_salida.exists() and not Path(str(normal_salida) + ".descartes.tsv").exists(),
      "preparar_reales: contradicción de muestra no genera salidas")

# El nombre no determina el papel: sin metadata, se conserva el contrato de muestra única.
vs_unica, meta_unica = ent.leer_variantes_meta(vcf_pep("unica_sin_etiqueta.vcf", ("NORMAL",)))
check(meta_unica["muestra_tumor"] == "NORMAL" and len(vs_unica) == 1,
      "leer_entradas: muestra única sin etiqueta conserva el comportamiento documentado")

# ─────────────────────────────────────────────────────────────── 2.3 config
print("== 2.3: configuración y estados de no medido ==")
um, sha = cp.cargar_config(cp.CONFIG_DEFECTO)
check(set(um) == {"tpm_min", "rank_presentacion", "af_min"} and len(sha) == 64,
      "pipeline.yaml se carga, valida y da hash")
for nombre, cuerpo in [
    ("af_fuera", "umbrales: {tpm_min: 1, rank_presentacion: 2, af_min: 2}"),
    ("falta", "umbrales: {tpm_min: 1, rank_presentacion: 2}"),
    ("errata", "umbrales: {tpm_min: 1, rank_presentacion: 2, af_min: 0.05, af_mim: 0.1}"),
    ("texto", "umbrales: {tpm_min: uno, rank_presentacion: 2, af_min: 0.05}"),
]:
    p = TMP / f"{nombre}.yaml"
    p.write_text(cuerpo + "\n")
    try:
        cp.cargar_config(p)
        check(False, f"config '{nombre}' debería rechazarse")
    except cp.ConfigInvalida:
        check(True, f"config '{nombre}' rechazada")

U = {"tpm_min": 1.0, "rank_presentacion": 2.0, "af_min": 0.05}
vs = [{"gene": "A", "aa": "p.1", "peptide": "AAAAAAAA", "af": 0.3},
      {"gene": "B", "aa": "p.2", "peptide": "BBBBBBBB", "af": 0.3},    # sin TPM en el fichero
      {"gene": "C", "aa": "p.3", "peptide": "CCCCCCCC", "af": 0.01},   # VAF bajo af_min
      {"gene": "D", "aa": "p.4", "peptide": "DDDDDDDD", "af": None},   # sin VAF
      {"gene": "E", "aa": "p.5", "peptide": "EEEEEEEE", "af": 0.3}]    # TPM bajo
cand, desc = cp.filtrar(vs, {"A": 10.0, "C": 10.0, "D": 10.0, "E": 0.2}, U)
por = {c["gene"]: c for c in cand}
check(por["A"]["evaluacion"] == "completa", "medido y dentro de umbral -> completa")
check(por.get("B", {}).get("estado_expresion") == "no_medido"
      and por["B"]["evaluacion"] == "incompleta",
      "gen ausente del fichero de expresión -> no_medido/incompleta (antes pasaba como expresado)")
check("C" not in por and any(g == "C" and "VAF" in m for g, _, m in desc),
      "af_min SE APLICA: VAF 0.01 descartada con motivo")
check(por.get("D", {}).get("estado_af") == "no_medido", "sin VAF -> no_medido, no se da por bueno")
check("E" not in por, "TPM bajo -> descartado")
cand2, _ = cp.filtrar(vs[:1], None, U)
check(cand2[0]["estado_expresion"] == "no_medido", "sin fichero de expresión -> no_medido")

print(f"\n=== {'TODO OK' if fallos == 0 else str(fallos) + ' FALLO(S)'} ===")
sys.exit(1 if fallos else 0)
