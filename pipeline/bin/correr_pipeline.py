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
import datetime as _dt
import math
import hashlib
import json
import sys
from pathlib import Path

# permite ejecutar desde la raíz del repo
sys.path.insert(0, str(Path(__file__).resolve().parent))

import leer_entradas as ent  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_DEFECTO = RAIZ / "pipeline" / "config" / "pipeline.yaml"

# 22-sep-26 (auditoría 2.3): antes había constantes aquí y el YAML no se leía nunca, así
# que tocar pipeline.yaml no cambiaba nada y af_min no se aplicaba. Ahora los umbrales
# salen SOLO del YAML, validados, y su hash va en cada salida. Rango permitido por clave:
UMBRALES_VALIDOS = {
    "tpm_min": (0.0, 1e6),             # transcrito por debajo = no expresado
    "rank_presentacion": (0.0, 100.0),  # %rank de presentación
    "af_min": (0.0, 1.0),               # VAF mínima
}


class ConfigInvalida(ValueError):
    """La configuración no se puede usar tal cual. Se para: un umbral mal escrito que se
    ignora en silencio es un filtro que nadie sabe que no está."""


def cargar_config(ruta: str | Path) -> tuple[dict, str]:
    """(umbrales validados, sha256 del fichero). Claves de más, de menos o fuera de
    rango -> ConfigInvalida."""
    import yaml
    crudo = Path(ruta).read_bytes()
    try:
        cfg = yaml.safe_load(crudo) or {}
    except yaml.YAMLError as e:
        raise ConfigInvalida(f"{ruta}: YAML ilegible ({e})") from e
    um = cfg.get("umbrales")
    if not isinstance(um, dict):
        raise ConfigInvalida(f"{ruta}: falta el bloque 'umbrales'")
    faltan = sorted(set(UMBRALES_VALIDOS) - set(um))
    sobran = sorted(set(um) - set(UMBRALES_VALIDOS))
    if faltan or sobran:
        raise ConfigInvalida(f"{ruta}: umbrales que faltan {faltan} / desconocidos {sobran}")
    out = {}
    for k, (lo, hi) in UMBRALES_VALIDOS.items():
        v = um[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigInvalida(f"{ruta}: umbrales.{k} no es un número ({v!r})")
        if not lo <= float(v) <= hi:
            raise ConfigInvalida(f"{ruta}: umbrales.{k}={v} fuera de [{lo}, {hi}]")
        out[k] = float(v)
    return out, hashlib.sha256(crudo).hexdigest()


def _valido(x: float, lo: float, hi: float) -> bool:
    """Un número presente pero no finito o fuera de dominio NO es un dato: es basura.
    (24-sep-26, issue #10 del repo público: AF NaN/inf/1.2 o TPM NaN/inf acababan en
    `evaluacion=completa` si el otro campo era válido.)"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(x) and lo <= float(x) <= hi


def filtrar(variantes: list[dict], expr: dict[str, float] | None,
            umbrales: dict) -> tuple[list[dict], list[tuple]]:
    """Aplica expresión y VAF. Cada candidato lleva su estado EXPLÍCITO por filtro:
      ok          medido, válido y pasa el umbral
      no_medido   no hay dato (sin fichero de expresión, gen ausente en él, o sin VAF)
    Lo medido que no pasa se descarta con motivo. Lo no medido NO se descarta ni se da
    por bueno: se queda como evaluación 'incompleta', separado de las completas.
    (Antes un gen ausente del fichero de expresión pasaba como si estuviera expresado.)
    Un valor PRESENTE pero inválido (NaN, inf, AF fuera de [0,1], TPM negativo) no es ni
    lo uno ni lo otro: se descarta con su motivo, nunca se cuenta como medido. No aborta
    la corrida entera — un valor corrupto en una variante no debe tirar el dossier —,
    pero queda contado en el manifiesto y avisado por pantalla."""
    candidatos, descartes = [], []
    vistos: set = set()
    for v in variantes:
        if not v.get("peptide"):
            continue
        clave = (v.get("gene"), v.get("aa"))
        tpm = expr.get(v.get("gene")) if expr is not None else None
        if tpm is None:
            est_expr = "no_medido"
        elif not _valido(tpm, 0.0, math.inf):
            if clave not in vistos:
                descartes.append((*clave, f"TPM inválido ({tpm!r}): no es un número finito >= 0"))
            vistos.add(clave)
            continue
        elif tpm < umbrales["tpm_min"]:
            if clave not in vistos:
                descartes.append((*clave, f"expresión baja (TPM={tpm} < {umbrales['tpm_min']})"))
            vistos.add(clave)
            continue
        else:
            est_expr = "ok"
        af = v.get("af")
        if af is None:
            est_af = "no_medido"
        elif not _valido(af, 0.0, 1.0):
            if clave not in vistos:
                descartes.append((*clave, f"AF inválida ({af!r}): no es un número finito en [0, 1]"))
            vistos.add(clave)
            continue
        elif af < umbrales["af_min"]:
            if clave not in vistos:
                descartes.append((*clave, f"VAF baja (AF={af} < {umbrales['af_min']})"))
            vistos.add(clave)
            continue
        else:
            est_af = "ok"
        candidatos.append({**v, "tpm": tpm, "estado_expresion": est_expr, "estado_af": est_af,
                           "evaluacion": "completa" if est_expr == est_af == "ok"
                           else "incompleta"})
    return candidatos, descartes


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipeline de neoantígenos (local, egress cero).")
    ap.add_argument("--vcf", required=True)
    ap.add_argument("--hla", required=True)
    ap.add_argument("--loh", default=None, help="alelos perdidos por LOH (se excluyen)")
    ap.add_argument("--expresion", default=None, help="TPM por gen (filtro de expresión)")
    ap.add_argument("--salida", required=True)
    ap.add_argument("--config", default=str(CONFIG_DEFECTO),
                    help="YAML de umbrales (por defecto pipeline/config/pipeline.yaml)")
    ap.add_argument("--muestra-tumor", default=None,
                    help="muestra tumoral del VCF; obligatoria si hay varias sin ##tumor_sample")
    ap.add_argument("--estructura", action="store_true",
                    help="anota estructura 3D vía AlphaFold-DB (solo terminología genérica)")
    ap.add_argument("--no-presentacion", action="store_true",
                    help="salta MHCflurry (para smoke test sin cargar el modelo)")
    args = ap.parse_args()

    print("== Pipeline de neoantígenos (local) ==", flush=True)

    try:
        umbrales, config_sha = cargar_config(args.config)
    except ConfigInvalida as e:
        print(f"  🛑 ABORTO — configuración inválida: {e}")
        return 2
    print(f"  config: {Path(args.config).name} sha256={config_sha[:12]}  umbrales={umbrales}")

    # --- Lectura local ---
    try:
        variantes, meta = ent.leer_variantes_meta(args.vcf, args.muestra_tumor)
    except ent.MuestraAmbigua as e:
        print(f"  🛑 ABORTO — muestra tumoral ambigua: {e}")
        return 2
    print(f"  muestra tumoral: {meta['muestra_tumor']} ({meta['muestra_tumor_motivo']})")
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
    if not hla_c1 and not args.no_presentacion:
        print("  🛑 ABORTO — no queda ningún alelo HLA-I tras el filtro LOH: la presentación "
              "saldría vacía y se leería como 'nada se presenta'.")
        return 2

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

    candidatos, descartes = filtrar(variantes, expr if args.expresion else None, umbrales)
    for gene, aa, motivo in descartes:
        print(f"  - descartado por {motivo}: {gene} {aa}")
    n_inval = sum(1 for _, _, m in descartes if "inválid" in m)
    if n_inval:
        print(f"  ⚠️  {n_inval} variante(s) con un valor PRESENTE pero inválido (NaN, inf o fuera "
              "de rango). No se cuentan como medidas ni entran en el dossier.")
    n_inc = len({(v['gene'], v['aa']) for v in candidatos if v["evaluacion"] == "incompleta"})
    print(f"  candidatos tras filtros de expresión y VAF: {len(candidatos)} péptidos "
          f"({len({(v['gene'], v['aa']) for v in candidatos})} variantes; "
          f"{len(descartes)} variantes descartadas; {n_inc} con evaluación INCOMPLETA "
          "por falta de dato, separadas al final)")

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
        pres = predecir_presentacion(peptidos, hla_c1, umbral_rank=umbrales["rank_presentacion"])
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
    # Las evaluaciones completas primero; las incompletas (falta expresión o VAF) al final,
    # nunca mezcladas con ellas.
    df["_inc"] = df["evaluacion"] != "completa"
    orden = ["_inc"]
    if "presentation_score" in df.columns and df["presentation_score"].notna().any():
        orden.append("presentation_score")
    df = df.sort_values(orden, ascending=[True] + [False] * (len(orden) - 1)).drop(columns="_inc")
    df["config_sha256"] = config_sha

    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.salida, sep="\t", index=False)
    manifiesto = {
        "generado": _dt.datetime.now().isoformat(timespec="seconds"),
        "config": Path(args.config).name, "config_sha256": config_sha, "umbrales": umbrales,
        "vcf": Path(args.vcf).name, **meta,
        "expresion": Path(args.expresion).name if args.expresion else None,
        "loh": Path(args.loh).name if args.loh else None,
        "hla_clase1_usados": len(hla_c1),
        "filas": len(df),
        "filas_completas": int((df["evaluacion"] == "completa").sum()),
        "filas_incompletas": int((df["evaluacion"] != "completa").sum()),
        "variantes_descartadas": [{"gene": g, "aa": a, "motivo": m} for g, a, m in descartes],
    }
    Path(str(args.salida) + ".manifiesto.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=1, default=str))
    print(f"\n  DOSSIER escrito: {args.salida}  ({len(df)} filas)")
    print(f"  manifiesto: {args.salida}.manifiesto.json")
    print("\n--- vista rápida ---")
    cols = [c for c in ["gene", "aa", "peptide", "tpm", "evaluacion", "best_allele",
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
