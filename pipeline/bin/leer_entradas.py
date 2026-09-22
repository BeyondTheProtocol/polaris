"""
leer_entradas.py — lectura LOCAL de las entradas del pipeline.

Todo se lee y se queda en local. Nada de esto sale a ninguna API.
"""
from __future__ import annotations
from pathlib import Path
import pysam


def leer_hla(ruta: str | Path) -> list[str]:
    """Lee alelos HLA (uno por línea, ignora comentarios #)."""
    out = []
    for ln in Path(ruta).read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            out.append(ln)
    return out


def clase1(alelos: list[str]) -> list[str]:
    return [a for a in alelos if a.startswith(("HLA-A", "HLA-B", "HLA-C"))]


def clase2(alelos: list[str]) -> list[str]:
    return [a for a in alelos if a.startswith(("HLA-D",))]


def alelos_no_perdidos(todos: list[str], perdidos: list[str]) -> list[str]:
    """REGLA DURA: excluye los alelos perdidos por LOH antes de predecir."""
    perd = {p.strip() for p in perdidos}
    return [a for a in todos if a not in perd]


def leer_expresion(ruta: str | Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for ln in Path(ruta).read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if not ln or ln.lower().startswith("gene"):
            continue
        parts = ln.split("\t")
        if len(parts) >= 2:
            try:
                out[parts[0].strip()] = float(parts[1])
            except ValueError:
                pass
    return out


class EntradaNoAnotada(RuntimeError):
    """El VCF no trae lo que el pipeline necesita para poder interpretarlo.
    Se lanza a proposito: es MUCHO mejor abortar a gritos que devolver 'sin candidatos',
    que se leeria como 'no tienes neoantigenos' y seria mentira."""


def leer_variantes(ruta_vcf: str | Path) -> list[dict]:
    """
    Lee el VCF (local) y extrae candidatos. Espera los INFO sintéticos GENE/AA/PEP
    del ejemplo; en producción el péptido mutante lo genera pVACseq desde el VCF
    anotado (VEP) + la secuencia de proteína. Aquí lo tomamos del campo PEP si
    existe (modo ejemplo) para poder cablear y probar la cadena completa.
    """
    out: list[dict] = []
    vcf = pysam.VariantFile(str(ruta_vcf))

    # ── GUARDA DE CABECERA (15-jul-26) ─────────────────────────────────────────
    # Este modulo NO calcula el peptido mutante: lo espera ANOTADO en el VCF. El fichero
    # de ejemplo lo trae en un campo INFO inventado (PEP), pero un VCF CLINICO REAL no
    # lo trae. Antes: pysam reventaba con "ValueError: Invalid header" al pedir GENE —
    # un volcado que no le dice nada a nadie. Ahora: se comprueba la cabecera y se aborta
    # con un mensaje que un humano entienda.
    campos = set(vcf.header.info.keys())
    if "PEP" not in campos:
        vcf.close()
        raise EntradaNoAnotada(
            "El VCF NO trae el peptido mutante anotado (falta el campo INFO 'PEP').\n"
            "\n"
            "  Este pipeline no CALCULA el peptido: lo espera ya anotado. Un VCF clinico\n"
            "  crudo no lo trae — hay que anotarlo antes con pVACseq, o con VEP + los\n"
            "  plugins Wildtype/Frameshift.\n"
            "\n"
            "  ⚠️  ESTO NO SIGNIFICA QUE NO HAYA NEOANTIGENOS. Significa que este fichero\n"
            "     todavia no se puede interpretar. NO se escribe dossier: un dossier\n"
            "     vacio se leeria como una respuesta, y seria MENTIRA.\n"
            "\n"
            "  Campos INFO que SI trae el fichero: %s" % (", ".join(sorted(campos)) or "(ninguno)")
        )

    # 19-sep-26 (validación con datos reales de Sid): en un VCF Mutect2 tumor-normal la
    # PRIMERA muestra suele ser la NORMAL. Antes se leía el AF de la primera -> VAF de la
    # normal (~0) presentada como VAF del tumor. Se usa ##tumor_sample si existe.
    tumor = None
    for hrec in vcf.header.records:
        if hrec.key == "tumor_sample":
            tumor = hrec.value
    muestras = list(vcf.header.samples)
    if tumor not in muestras:
        tumor = muestras[0] if muestras else None

    for rec in vcf:
        if rec.filter.keys() and "PASS" not in rec.filter.keys():
            continue
        info = rec.info
        gene = info.get("GENE")
        gene = gene[0] if isinstance(gene, tuple) else gene
        aa = info.get("AA")
        aa = aa[0] if isinstance(aa, tuple) else aa
        pep = info.get("PEP")
        # 19-sep-26: una variante real genera VARIOS péptidos (ventanas 8-11 que
        # contienen el cambio; ver preparar_reales.py). Antes se tomaba solo el
        # primero y el resto se perdía en silencio. Ahora: una fila por péptido.
        peps = list(pep) if isinstance(pep, tuple) else [pep]
        af = None
        if tumor is not None and "AF" in rec.samples[tumor]:
            af = rec.samples[tumor]["AF"]
            af = af[0] if isinstance(af, tuple) else af
        for p_ in peps:
            out.append({
                "chrom": rec.chrom, "pos": rec.pos,
                "ref": rec.ref, "alt": ",".join(rec.alts or []),
                "gene": gene, "aa": aa, "peptide": p_, "af": af,
            })
    vcf.close()
    return out
