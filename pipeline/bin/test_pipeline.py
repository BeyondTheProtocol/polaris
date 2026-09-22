#!/usr/bin/env python3
"""
test_pipeline.py — pruebas del pipeline con datos SINTÉTICOS (sin red salvo el
test de estructura, que es opcional). No usa datos reales de {{TITULAR}}.

Correr:  .venv-pipeline/bin/python pipeline/bin/test_pipeline.py
"""
from __future__ import annotations
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "pipeline" / "bin"))
EJ = RAIZ / "pipeline" / "data_ejemplo"

import leer_entradas as ent           # noqa: E402
from muro import comprobar_salida, MuroError, es_termino_generico  # noqa: E402

fallos = 0


def check(cond, msg):
    global fallos
    print(("  OK   " if cond else "  FALLO ") + msg)
    if not cond:
        fallos += 1


print("== test: lectura de entradas ==")
variantes = ent.leer_variantes(EJ / "ejemplo.vcf")
check(len(variantes) == 4, f"4 variantes leídas (got {len(variantes)})")
hla = ent.leer_hla(EJ / "ejemplo.hla.txt")
check(len(ent.clase1(hla)) == 6, "6 alelos clase I")
check(len(ent.clase2(hla)) == 2, "2 alelos clase II")
perd = ent.leer_hla(EJ / "ejemplo.hla_perdidos.txt")
kept = ent.alelos_no_perdidos(ent.clase1(hla), perd)
check("HLA-B*08:01" not in kept, "LOH excluye HLA-B*08:01")
expr = ent.leer_expresion(EJ / "ejemplo.expresion.tsv")
check(expr.get("SYNTH1") == 0.3, "expresión SYNTH1 = 0.3")

print("== test: muro de privacidad ==")
for url, carga, debe_bloquear in [
    ("https://evil.com/x", "TP53", True),
    ("https://rest.ensembl.org/x", "##fileformat=VCFv4.2", True),
    ("https://alphafold.ebi.ac.uk/x", "HLA-A*02:01", True),
    ("https://rest.ensembl.org/x", "{{TITULAR}}", True),
    ("https://rest.ensembl.org/lookup/symbol/homo_sapiens/TP53", "TP53", False),
]:
    bloqueo = False
    try:
        comprobar_salida(url, carga)
    except MuroError:
        bloqueo = True
    check(bloqueo == debe_bloquear, f"muro {url[:35]} carga={carga[:18]!r}")

check(es_termino_generico("P04637") and not es_termino_generico("HLA-A*02:01"),
      "es_termino_generico distingue accession de alelo")

print("== test: presentación local (MHCflurry) — opcional, requiere modelos ==")
try:
    from presentacion_local import predecir_presentacion
    df = predecir_presentacion(["GILGFVFTL", "NLVPMVATV"], ["HLA-A*02:01"])
    check(not df.empty and df["presentation_score"].max() > 0.5,
          "epítopos A*02:01 conocidos puntúan alto")
except Exception as e:  # noqa: BLE001
    print(f"  (saltado: MHCflurry no disponible — {type(e).__name__})")

# --- CONTROL NEGATIVO (15-jul-26) ---------------------------------------------
# Un VCF sin peptido anotado (como uno clinico real) NO puede devolver "sin
# candidatos" en silencio ni reventar con un volcado de pysam. Debe abortar a
# gritos (EntradaNoAnotada). Es el fallo que le habria dicho a {{TITULAR}} "no tienes
# neoantigenos" cuando la verdad es "no supe leer tu fichero".
import tempfile, os as _os
from leer_entradas import leer_variantes, EntradaNoAnotada
_v = tempfile.NamedTemporaryFile("w", suffix=".vcf", delete=False)
_v.write("##fileformat=VCFv4.2\n##contig=<ID=chr17>\n"
         '##INFO=<ID=DP,Number=1,Type=Integer,Description="d">\n'
         "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
         "chr17\t7676154\t.\tG\tA\t.\tPASS\tDP=120\n")
_v.close()
try:
    leer_variantes(_v.name)
    check(False, "VCF sin PEP deberia abortar (EntradaNoAnotada), no seguir")
except EntradaNoAnotada:
    check(True, "VCF real (sin PEP) aborta a gritos, no escribe dossier vacio")
except Exception as _e:  # noqa: BLE001
    check(False, "VCF sin PEP: esperaba EntradaNoAnotada, no %s" % type(_e).__name__)
finally:
    _os.unlink(_v.name)

# --- MURO por host+ruta: el EMBOSS del EBI (B-antigen de SNAF) bloqueado -------
import muro as _m
check(_m.host_permitido("https://www.ebi.ac.uk/proteins/api/x"),
      "UniProt (ruta buena) PASA")
check(not _m.host_permitido("https://www.ebi.ac.uk/Tools/services/rest/emboss_needle/run"),
      "EMBOSS Needle del EBI (fuga de secuencia del tumor) BLOQUEADO")
check(not _m.host_permitido("https://www.ebi.ac.uk/Tools/sss/ncbiblast/run"),
      "BLAST remoto BLOQUEADO")

print(f"\n=== {'TODO OK' if fallos == 0 else str(fallos) + ' FALLO(S)'} ===")
sys.exit(1 if fallos else 0)
