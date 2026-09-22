#!/usr/bin/env python3
"""
preparar_reales.py — convierte salidas REALES de un laboratorio al formato de entrada
del pipeline. 100 % local, egress CERO (no llama a ninguna API).

Nace el 19-sep-26 al validar el pipeline contra un caso humano real y público (dataset
abierto de Sid {{CONTACTO}}, osteosarcoma, CC0). Destapó el hueco que los datos sintéticos
escondían: un VCF clínico real NO trae el péptido mutante, y el pipeline lo exigía.

Subcomandos:

  vcf        VCF somático anotado con VEP (campo CSQ) + proteoma de referencia Ensembl
             (FASTA pep.all, MISMA release que el VEP) -> VCF con INFO GENE/AA/PEP que
             lee `leer_entradas.leer_variantes`. Genera en local TODAS las ventanas de
             longitud 8-11 que contienen el cambio. Soporta missense e inframe
             (deleción/inserción). NO soporta frameshift ni stop (necesitan la secuencia
             de cDNA): se cuentan y se avisa, nunca se callan.
  expresion  RSEM genes.results (gene_id ENSG) -> TSV gene<TAB>tpm por SÍMBOLO, con el
             mapeo ENSG->símbolo sacado del mismo proteoma local.
  hla        resultado de OptiType (A1..C2) -> un alelo HLA-X*NN:NN por línea.

Control de referencia (anti off-by-one): para cada variante se comprueba que el
aminoácido de referencia que dice VEP coincide con el de la proteína en esa posición
(1-based). Si no coincide, la variante se DESCARTA y se cuenta como discordante: una
discordancia alta = build/release equivocada, y el script lo grita.

MURO: apoyo a la decisión, NO consejo médico. Describe y equipa, no concluye.
"""
from __future__ import annotations

import argparse
import gzip
import sys
from collections import Counter
from pathlib import Path

import pysam

LONGITUDES = (8, 9, 10, 11)
CONSEC_OK = ("missense_variant", "inframe_deletion", "inframe_insertion",
             "protein_altering_variant")
CONSEC_NO_SOPORTADAS = ("frameshift_variant", "stop_gained", "stop_lost",
                        "start_lost")
UMBRAL_DISCORDANCIA = 0.05   # >5 % de REF discordante => build/release equivocada


# --------------------------------------------------------------------------- proteoma
def _abrir(ruta: str | Path):
    ruta = str(ruta)
    return gzip.open(ruta, "rt") if ruta.endswith(".gz") else open(ruta)


def leer_proteoma(ruta_fasta: str | Path) -> tuple[dict[str, str], dict[str, str]]:
    """Devuelve ({ENSP_sin_version: secuencia}, {ENSG_sin_version: símbolo})."""
    prot: dict[str, str] = {}
    simbolo: dict[str, str] = {}
    actual, trozos = None, []
    with _abrir(ruta_fasta) as fh:
        for ln in fh:
            if ln.startswith(">"):
                if actual:
                    prot[actual] = "".join(trozos)
                campos = ln[1:].split()
                actual = campos[0].split(".")[0]
                trozos = []
                g = s = None
                for c in campos[1:]:
                    if c.startswith("gene:"):
                        g = c[5:].split(".")[0]
                    elif c.startswith("gene_symbol:"):
                        s = c[12:]
                if g and s:
                    simbolo[g] = s
            else:
                trozos.append(ln.strip())
    if actual:
        prot[actual] = "".join(trozos)
    return prot, simbolo


# --------------------------------------------------------------------------- péptidos
def proteina_mutante(wt: str, pos: str, aa: str) -> tuple[str, int, int] | None:
    """Aplica el cambio de VEP (Protein_position, Amino_acids) a la proteína WT.

    Devuelve (mutante, ini, fin): [ini, fin) es la región cambiada en la mutante
    (0-based). Para una deleción pura, ini == fin marca la unión. None si el REF de
    VEP no coincide con la proteína (discordancia) o el formato no es interpretable.
    """
    if "/" not in aa:
        return None
    ref, alt = aa.split("/", 1)
    ref = "" if ref == "-" else ref
    alt = "" if alt == "-" else alt
    if "*" in ref or "*" in alt or "X" in alt:
        return None
    # VEP con --total_length escribe "63/446" (posición/longitud): la longitud fuera.
    # (Primer fallo real del 19-sep-26: 93/93 discordantes por no quitarla.)
    pos = pos.split("/", 1)[0]
    try:
        partes = [int(x) for x in pos.split("-") if x not in ("", "?")]
    except ValueError:
        return None
    if not partes:
        return None
    s = partes[0]
    e = partes[-1]
    if ref:
        # sustitución / deleción: ref ocupa [s-1, s-1+len(ref)) en la WT (1-based -> 0-based)
        ini = s - 1
        if wt[ini:ini + len(ref)] != ref:
            return None
        mut = wt[:ini] + alt + wt[ini + len(ref):]
        return mut, ini, ini + len(alt)
    # inserción pura: VEP da "s-e" con e = s+1 -> insertar entre s y e
    if e != s + 1 or s > len(wt):
        return None
    mut = wt[:s] + alt + wt[s:]
    return mut, s, s + len(alt)


def ventanas_mutantes(wt: str, mut: str, ini: int, fin: int,
                      longitudes=LONGITUDES) -> list[str]:
    """Todas las subcadenas de `mut` de las longitudes dadas que tocan el cambio y
    NO existen tal cual en la proteína WT (evita 'neoantígenos' que son propios)."""
    out: list[str] = []
    vistos: set[str] = set()
    for L in longitudes:
        if fin > ini:
            lo, hi = ini - L + 1, fin - 1          # la ventana [a, a+L) debe solapar [ini, fin)
        else:
            lo, hi = ini - L + 1, ini - 1          # deleción: debe cruzar la unión
        for a in range(max(0, lo), min(hi, len(mut) - L) + 1):
            p = mut[a:a + L]
            if len(p) != L or p in vistos or p in wt or "*" in p or "X" in p or "U" in p:
                continue
            vistos.add(p)
            out.append(p)
    return out


# --------------------------------------------------------------------------- VCF
def _campos_csq(header) -> list[str]:
    desc = header.info["CSQ"].description
    return desc.split("Format:")[1].strip().strip('"').split("|")


def _elegir_csq(entradas: list[dict], alelo: str) -> dict | None:
    cand = [c for c in entradas
            if any(k in c.get("Consequence", "") for k in CONSEC_OK)
            and c.get("ENSP") and c.get("Protein_position") and c.get("Amino_acids")
            and (not alelo or c.get("Allele") in (alelo, "-"))]
    if not cand:
        cand = [c for c in entradas
                if any(k in c.get("Consequence", "") for k in CONSEC_OK)
                and c.get("ENSP") and c.get("Protein_position") and c.get("Amino_acids")]
    if not cand:
        return None
    cand.sort(key=lambda c: (not c.get("MANE_SELECT"), c.get("CANONICAL") != "YES"))
    return cand[0]


def _alelo_vep(ref: str, alt: str) -> str:
    """Representación del alelo en CSQ: VEP quita la base de anclaje común en indels."""
    if len(ref) != len(alt) and ref[:1] == alt[:1]:
        return alt[1:] or "-"
    return alt


def anotar_vcf(ruta_vcf: str, ruta_fasta: str, ruta_salida: str,
               solo_pass: bool = True, longitudes: tuple[int, ...] = LONGITUDES) -> dict:
    prot, _ = leer_proteoma(ruta_fasta)
    vin = pysam.VariantFile(ruta_vcf)
    if "CSQ" not in vin.header.info:
        raise SystemExit("ERROR: el VCF no trae anotación VEP (INFO CSQ). Anótalo con VEP antes.")
    campos = _campos_csq(vin.header)
    tumor = None
    for rec in vin.header.records:
        if rec.key == "tumor_sample":
            tumor = rec.value
    muestras = list(vin.header.samples)
    if tumor not in muestras:
        tumor = muestras[-1] if muestras else None

    # 19-sep-26 (caso Sid): si el VEP corrió con --per_gene solo hay UNA consecuencia por
    # gen (la del transcrito elegido). Una variante que es intrónica en el MANE pero
    # MISSENSE en un transcrito alternativo se pierde entera: así se nos escapó NME1
    # G24R, que pVACseq sí encontró. Para neoantígenos hay que anotar con TODOS los
    # transcritos. No se puede arreglar aquí: se avisa fuerte.
    cmdline = ""
    for rec in vin.header.records:
        if rec.key == "VEP-command-line":
            cmdline = str(rec.value)
    if "--per_gene" in cmdline:
        print("  ⚠️  AVISO: este VCF se anotó con VEP --per_gene (una consecuencia por gen).\n"
              "     Las variantes codificantes en transcritos NO elegidos se PIERDEN.\n"
              "     Para neoantígenos, re-anotar con VEP sin --per_gene.", file=sys.stderr)

    cont: Counter = Counter()
    filas = []
    for rec in vin:
        cont["registros"] += 1
        if solo_pass and (not rec.filter.keys() or "PASS" not in rec.filter.keys()):
            continue
        cont["pass"] += 1
        csq_raw = rec.info.get("CSQ") or ()
        entradas = [dict(zip(campos, c.split("|"))) for c in csq_raw]
        consecs = {k for c in entradas for k in c.get("Consequence", "").split("&")}
        if not consecs & set(CONSEC_OK):
            for k in CONSEC_NO_SOPORTADAS:
                if k in consecs:
                    cont["no_soportada:" + k] += 1
                    break
            continue
        alt = (rec.alts or [""])[0]
        csq = _elegir_csq(entradas, _alelo_vep(rec.ref, alt))
        if not csq:
            cont["sin_csq_proteica"] += 1
            continue
        ensp = csq["ENSP"].split(".")[0]
        wt = prot.get(ensp)
        if not wt:
            cont["ensp_no_en_proteoma"] += 1
            continue
        cont["codificantes_evaluadas"] += 1
        r = proteina_mutante(wt, csq["Protein_position"], csq["Amino_acids"])
        if r is None:
            cont["ref_discordante"] += 1
            continue
        mut, ini, fin = r
        peps = ventanas_mutantes(wt, mut, ini, fin, longitudes)
        if not peps:
            cont["sin_ventanas"] += 1
            continue
        af = None
        if tumor and "AF" in rec.samples[tumor]:
            v = rec.samples[tumor]["AF"]
            af = v[0] if isinstance(v, tuple) else v
        # HGVSp viene VACÍO si VEP corrió --offline sin FASTA (caso Sid): se construye
        # el cambio desde Amino_acids + Protein_position (quitando el "/longitud").
        hgvsp = csq.get("HGVSp", "")
        if ":" in hgvsp:
            aa = hgvsp.split(":", 1)[1]
        else:
            r_, a_ = csq["Amino_acids"].split("/", 1)
            aa = f"p.{r_}{csq['Protein_position'].split('/', 1)[0]}{a_}"
        filas.append((rec.chrom, rec.pos, rec.ref, alt, csq.get("SYMBOL") or csq.get("Gene"),
                      aa.replace("%3D", "="), csq["Feature"], ensp, csq["Consequence"], peps, af))
        cont["con_peptidos"] += 1
        cont["peptidos"] += len(peps)
    vin.close()

    ev = cont["codificantes_evaluadas"]
    if ev and cont["ref_discordante"] / ev > UMBRAL_DISCORDANCIA:
        raise SystemExit(
            f"ERROR: {cont['ref_discordante']}/{ev} variantes con aminoácido de referencia "
            "DISCORDANTE con el proteoma. Casi seguro build/release distinta entre el VEP y "
            "el FASTA. No se escribe nada: un dossier sobre la referencia equivocada miente.")

    contigs = sorted({f[0] for f in filas})
    cab = ["##fileformat=VCFv4.2",
           f"##preparar_reales_longitudes={','.join(str(L) for L in longitudes)}",
           "##source=pipeline/bin/preparar_reales.py (VEP CSQ + proteoma Ensembl local)",
           f"##preparar_reales_origen={Path(ruta_vcf).name}",
           f"##preparar_reales_proteoma={Path(ruta_fasta).name}",
           f"##preparar_reales_muestra_tumor={tumor}"]
    cab += [f"##contig=<ID={c}>" for c in contigs]
    cab += ['##INFO=<ID=GENE,Number=1,Type=String,Description="Símbolo del gen (VEP SYMBOL)">',
            '##INFO=<ID=AA,Number=1,Type=String,Description="Cambio proteico (VEP HGVSp)">',
            '##INFO=<ID=TX,Number=1,Type=String,Description="Transcrito Ensembl elegido (MANE>canónico)">',
            '##INFO=<ID=ENSP,Number=1,Type=String,Description="Proteína Ensembl usada">',
            '##INFO=<ID=CONSEQ,Number=1,Type=String,Description="Consecuencia VEP">',
            '##INFO=<ID=PEP,Number=.,Type=String,Description="Péptidos mutantes que contienen el cambio (generados en local)">',
            '##FILTER=<ID=PASS,Description="All filters passed">',
            '##FORMAT=<ID=AF,Number=1,Type=Float,Description="VAF en la muestra tumoral">',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tTUMOR"]
    with open(ruta_salida, "w") as out:
        out.write("\n".join(cab) + "\n")
        for chrom, pos, ref, alt, gene, aa, tx, ensp, conseq, peps, af in filas:
            info = (f"GENE={gene};AA={aa.replace(';', ',')};TX={tx};ENSP={ensp};"
                    f"CONSEQ={conseq.replace(';', ',')};PEP={','.join(peps)}")
            afs = "." if af is None else f"{af:.4f}"
            out.write(f"{chrom}\t{pos}\t.\t{ref}\t{alt}\t.\tPASS\t{info}\tAF\t{afs}\n")
    return dict(cont)


# --------------------------------------------------------------------------- expresión / HLA
def convertir_expresion(ruta_rsem: str, ruta_fasta: str, ruta_salida: str) -> dict:
    _, simbolo = leer_proteoma(ruta_fasta)
    tpm: dict[str, float] = {}
    sin_simbolo = 0
    with _abrir(ruta_rsem) as fh:
        cab = fh.readline().rstrip("\n").split("\t")
        i_g, i_t = cab.index("gene_id"), cab.index("TPM")
        for ln in fh:
            p = ln.rstrip("\n").split("\t")
            g = p[i_g].split(".")[0]
            s = simbolo.get(g)
            if not s:
                sin_simbolo += 1
                continue
            tpm[s] = max(tpm.get(s, 0.0), float(p[i_t]))   # símbolo duplicado (PAR): máx
    with open(ruta_salida, "w") as out:
        out.write(f"# TPM por símbolo desde {Path(ruta_rsem).name} (RSEM); ENSG->símbolo del "
                  f"proteoma {Path(ruta_fasta).name}. Genes sin proteína en el proteoma: fuera.\n")
        out.write("gene\ttpm\n")
        for s in sorted(tpm):
            out.write(f"{s}\t{tpm[s]}\n")
    return {"genes_con_simbolo": len(tpm), "genes_sin_simbolo_proteico": sin_simbolo}


def convertir_hla_optitype(ruta: str, ruta_salida: str) -> list[str]:
    lineas = Path(ruta).read_text().splitlines()
    cab = lineas[0].split("\t")
    fila = lineas[1].split("\t")
    alelos: list[str] = []
    for col in ("A1", "A2", "B1", "B2", "C1", "C2"):
        v = fila[cab.index(col)].strip()
        if v:
            a = v if v.startswith("HLA-") else "HLA-" + v
            if a not in alelos:
                alelos.append(a)
    Path(ruta_salida).write_text(
        f"# HLA clase I desde OptiType ({Path(ruta).name}); homocigotos deduplicados.\n"
        + "\n".join(alelos) + "\n")
    return alelos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("vcf")
    a.add_argument("--vcf", required=True)
    a.add_argument("--proteoma", required=True)
    a.add_argument("--salida", required=True)
    a.add_argument("--incluir-no-pass", action="store_true")
    a.add_argument("--longitudes", default=",".join(str(L) for L in LONGITUDES),
                   help="longitudes de péptido a generar (por defecto 8,9,10,11; "
                        "pVACseq clase I suele usar 9,10,11,12)")
    b = sub.add_parser("expresion")
    b.add_argument("--rsem", required=True)
    b.add_argument("--proteoma", required=True)
    b.add_argument("--salida", required=True)
    c = sub.add_parser("hla")
    c.add_argument("--optitype", required=True)
    c.add_argument("--salida", required=True)
    args = ap.parse_args()

    if args.cmd == "vcf":
        r = anotar_vcf(args.vcf, args.proteoma, args.salida,
                       solo_pass=not args.incluir_no_pass,
                       longitudes=tuple(int(x) for x in args.longitudes.split(",")))
    elif args.cmd == "expresion":
        r = convertir_expresion(args.rsem, args.proteoma, args.salida)
    else:
        r = {"alelos": convertir_hla_optitype(args.optitype, args.salida)}
    for k, v in sorted(r.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
