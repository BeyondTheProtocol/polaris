#!/usr/bin/env python3
"""
correr_pipeline.py — runner del pipeline de neoantígenos (versión "lista para FASTQ").

Cablea las etapas que YA podemos correr en local sin licencia (Etapa B-filtro,
filtro de expresión, Etapa D presentación con MHCflurry, anotación estructural por
AlphaFold) y emite una TABLA DE DOSSIER priorizada. Las etapas pesadas (alineamiento,
calling de variantes, NetMHCpan, MS) quedan especificadas en el doc del pipeline y
documentadas como gated en docs/PIEZAS-GATED.md.

USO (con datos de EJEMPLO, lo que corre hoy):
    .venv-pipeline/bin/python pipeline/bin/correr_pipeline.py \
        --vcf      pipeline/data_ejemplo/ejemplo.vcf \
        --hla      pipeline/data_ejemplo/ejemplo.hla.txt \
        --loh      pipeline/data_ejemplo/ejemplo.hla_perdidos.txt \
        --expresion pipeline/data_ejemplo/ejemplo.expresion.tsv \
        --salida   pipeline/salidas/dossier_ejemplo.tsv

EL DÍA QUE LLEGUE ZÚRICH: se cambian las rutas --vcf/--hla por las reales (que viven
en _PRIVADO_CLINICO/, fuera de git) y se corre igual. Egress cero para VCF/HLA/péptidos.

MURO: apoyo a la decisión, NO consejo médico. Describe y equipa, no concluye.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# permite ejecutar desde la raíz del repo
sys.path.insert(0, str(Path(__file__).resolve().parent))

import leer_entradas as ent  # noqa: E402

UMBRAL_TPM = 1.0       # transcrito no expresado = no presentado
UMBRAL_RANK = 2.0      # %rank de presentación; fuerte <= 0.5


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipeline de neoantígenos (local, egress cero).")
    ap.add_argument("--vcf", required=True)
    ap.add_argument("--hla", required=True)
    ap.add_argument("--loh", default=None, help="alelos perdidos por LOH (se excluyen)")
    ap.add_argument("--expresion", default=None, help="TPM por gen (filtro de expresión)")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--estructura", action="store_true",
                    help="anota estructura 3D vía AlphaFold-DB (solo terminología genérica)")
    ap.add_argument("--no-presentacion", action="store_true",
                    help="salta MHCflurry (para smoke test sin cargar el modelo)")
    args = ap.parse_args()

    print("== Pipeline de neoantígenos (local) ==", flush=True)

    # --- Lectura local ---
    variantes = ent.leer_variantes(args.vcf)
    hla_todos = ent.leer_hla(args.hla)
    perdidos = ent.leer_hla(args.loh) if args.loh else []
    expr = ent.leer_expresion(args.expresion) if args.expresion else {}
    n_var = len({(v["chrom"], v["pos"], v["alt"]) for v in variantes})
    n_pep = len({v["peptide"] for v in variantes if v.get("peptide")})
    print(f"  variantes leídas: {n_var}  | péptidos mutantes únicos: {n_pep}")
    print(f"  HLA tipados: {len(hla_todos)}  | perdidos por LOH: {len(perdidos)}")

    # --- Etapa B (filtro LOH) ---
    hla_c1 = ent.alelos_no_perdidos(ent.clase1(hla_todos), perdidos)
    print(f"  HLA-I tras filtro LOH: {hla_c1}")

    # --- Filtro de expresión (Etapa C) ---
    # ── GUARDA CONTRA EL FALSO NEGATIVO SILENCIOSO (15-jul-26) ──────────────────
    # Este pipeline NO calcula el peptido mutante: lo espera ANOTADO en el VCF (campo
    # PEP del ejemplo sintetico; en produccion lo genera pVACseq/VEP). Un VCF real NO
    # lo trae. Antes, sin peptido, se saltaba todas las variantes y escribia un dossier
    # VACIO con exit 0 -> se leeria como "no tienes neoantigenos" cuando la verdad es
    # "no supe leer tu fichero". Es el peor fallo posible sobre el dato de una biopsia.
    con_peptido = [v for v in variantes if v.get("peptide")]
    if variantes and not con_peptido:
        print()
        print("  🛑 ABORTO — el VCF tiene %d variantes pero NINGUNA trae el peptido mutante." % len(variantes))
        print("     Este pipeline NO calcula el peptido: lo espera ya anotado en el VCF.")
        print("     Un VCF clinico crudo NO lo trae -> hay que anotarlo antes (pVACseq o")
        print("     VEP + plugins Wildtype/Frameshift).")
        print()
        print("     ⚠️  ESTO NO SIGNIFICA QUE NO HAYA NEOANTIGENOS. Significa que el")
        print("        fichero no se pudo interpretar. NO se escribe dossier: un dossier")
        print("        vacio se leeria como una respuesta, y seria MENTIRA.")
        return 2

    candidatos = []
    descartados: set = set()
    for v in variantes:
        if not v.get("peptide"):
            continue
        tpm = expr.get(v.get("gene"), None)
        expresado = (tpm is None) or (tpm >= UMBRAL_TPM)
        if expr and not expresado:
            clave = (v.get("gene"), v.get("aa"))
            if clave not in descartados:   # una línea por variante, no por péptido
                print(f"  - descartado por expresión baja (TPM={tpm}): {v['gene']} {v['aa']}")
            descartados.add(clave)
            continue
        v["tpm"] = tpm
        candidatos.append(v)
    print(f"  candidatos tras filtro de expresión: {len(candidatos)} péptidos "
          f"({len({(v['gene'], v['aa']) for v in candidatos})} variantes; "
          f"{len(descartados)} variantes descartadas por TPM<{UMBRAL_TPM})")

    if not candidatos:
        print("  (sin candidatos) — nada que predecir.")
        return 0

    # --- Etapa D (presentación, LOCAL) ---
    import pandas as pd
    filas = []
    if args.no_presentacion:
        for v in candidatos:
            filas.append({**v, "best_allele": None, "presentation_score": None,
                          "presentado": None})
    else:
        from presentacion_local import predecir_presentacion
        peptidos = [v["peptide"] for v in candidatos]
        pres = predecir_presentacion(peptidos, hla_c1, umbral_rank=UMBRAL_RANK)
        pres_por_pep = {r["peptide"]: r for _, r in pres.iterrows()} if not pres.empty else {}
        for v in candidatos:
            r = pres_por_pep.get(v["peptide"], {})
            filas.append({
                **v,
                "best_allele": r.get("best_allele"),
                "presentation_score": r.get("presentation_score"),
                "presentation_percentile": r.get("presentation_percentile"),
                "presentado": r.get("presentado"),
            })

    df = pd.DataFrame(filas)

    # --- Anotación estructural opcional (egress genérico) ---
    if args.estructura:
        from clientes_api import uniprot_accession_de_gen, alphafold_por_accession
        af_urls = {}
        for g in sorted({v["gene"] for v in candidatos if v.get("gene")}):
            try:
                acc = uniprot_accession_de_gen(g)
                af = alphafold_por_accession(acc) if acc else None
                af_urls[g] = (af or {}).get("pdbUrl")
            except Exception as e:  # noqa: BLE001
                af_urls[g] = f"(error: {e})"
        df["alphafold_pdb"] = df["gene"].map(af_urls)

    # --- Priorización (orden conceptual del doc) ---
    if "presentation_score" in df.columns and df["presentation_score"].notna().any():
        df = df.sort_values("presentation_score", ascending=False)

    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.salida, sep="\t", index=False)
    print(f"\n  DOSSIER escrito: {args.salida}  ({len(df)} filas)")
    print("\n--- vista rápida ---")
    cols = [c for c in ["gene", "aa", "peptide", "tpm", "best_allele",
                        "presentation_score", "presentado", "alphafold_pdb"]
            if c in df.columns]
    # 19-sep-26: con datos reales hay miles de filas (una por péptido); se muestran
    # las 20 primeras. El dossier completo está en el TSV.
    print(df[cols].head(20).to_string(index=False))
    print("\n[CAVEAT TESLA] binding/presentación != inmunogenicidad != eficacia. "
          "Describe y equipa, NO concluye. Material de apoyo, no consejo médico.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
