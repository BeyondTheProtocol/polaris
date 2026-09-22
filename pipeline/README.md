# Pipeline de neoantígenos — "listo para FASTQ"

> **Qué es:** el cableado EJECUTABLE del pipeline especificado en
> `00_FUENTE-DE-VERDAD/04 · IA/Pipeline-Neoantigenos-Listo-para-FASTQ-2026-06-21.md`.
> Deja el motor montado y probado con datos sintéticos para que, **el día que llegue
> la biopsia de Zúrich (VCF + HLA), solo haya que apuntarlo y correr** — sin perder una semana.
>
> **Muro (innegociable):** apoyo a la decisión, NO consejo médico. Describe y equipa,
> NO concluye. Los datos genómicos crudos (VCF/HLA/FASTQ) se procesan **SOLO en local**.
> A las APIs externas solo va terminología genérica (gen, accession). Egress cero para
> péptidos/HLA/PII, enforced en `bin/muro.py`.

## Estado

| | |
|---|---|
| Motor de presentación (Etapa D) | ✅ MHCflurry 2.2.1 local, probado |
| Filtro LOH-HLA (Etapa B) | ✅ probado |
| Filtro de expresión (Etapa C) | ✅ probado |
| Anotación estructural (AlphaFold-DB) | ✅ probado (sin clave) |
| Muro de egress | ✅ 6/6 |
| Datos de ejemplo | ✅ sintéticos, en `data_ejemplo/` |
| **Validación con un caso humano REAL** | ✅ 19-sep-26 · dataset público de Sid {{CONTACTO}} (osteosarcoma, CC0) · 15/16 variantes y 417/417 péptidos de la referencia pVACseq · Spearman 0,88 |
| Anotación de péptidos desde un VCF clínico | ✅ `bin/preparar_reales.py` (VEP + proteoma Ensembl, local) |
| Etapas pesadas (alineamiento, calling, NetMHCpan, MS) | 📄 especificadas; gated en `docs/PIEZAS-GATED.md` |

## Mapa de etapas → qué corre hoy

```
VCF + HLA (de Zúrich)                ← hoy: data_ejemplo/ sintético
   │
   ├─ Etapa B  filtro LOH-HLA        ✅ local
   ├─ Etapa C  filtro de expresión   ✅ local
   ├─ Etapa D  presentación pMHC     ✅ MHCflurry local (NetMHCpan = gated)
   ├─ (anotación) estructura 3D      ✅ AlphaFold-DB (genérico)
   └─ Etapa E  inmunogenicidad       📄 pVACtools/NeoFox/PredIG = gated (Docker)
        │
        ▼
   DOSSIER priorizado (salidas/*.tsv)
```

## Correr con un VCF REAL (validado 19-sep-26)

Un VCF clínico **no trae el péptido mutante**; hay que generarlo antes. Eso lo hace
`bin/preparar_reales.py`, en local y sin salir a ninguna API:

```bash
.venv-pipeline/bin/python pipeline/bin/preparar_reales.py vcf \
  --vcf <somatico.VEP.vcf.gz> --proteoma <Homo_sapiens.GRCh38.pep.all.fa.gz> \
  --salida <preparado.pep.vcf>          # + subcomandos `expresion` (RSEM) y `hla` (OptiType)
```

Requisitos del VCF: anotado con **VEP** (campo `CSQ`) y, muy importante, **sin
`--per_gene`** — con esa bandera VEP deja una sola consecuencia por gen y se pierden las
variantes que solo son codificantes en un transcrito alternativo (en el caso de Sid, así
se perdió NME1 G24R, que pVACseq sí encontró). El proteoma debe ser de la **misma release
de Ensembl** que el VEP; si no, el control de referencia aborta la corrida.

Cubre missense e inframe indel. **Frameshift y stop_gained no**: se cuentan y se avisan.

## Correr con datos de EJEMPLO (lo que funciona hoy)

```bash
.venv-pipeline/bin/python pipeline/bin/correr_pipeline.py \
  --vcf       pipeline/data_ejemplo/ejemplo.vcf \
  --hla       pipeline/data_ejemplo/ejemplo.hla.txt \
  --loh       pipeline/data_ejemplo/ejemplo.hla_perdidos.txt \
  --expresion pipeline/data_ejemplo/ejemplo.expresion.tsv \
  --salida    pipeline/salidas/dossier_ejemplo.tsv \
  --estructura
```

## El día que llegue la biopsia

1. Los ficheros reales (VCF, HLA, LOH, TPM) se guardan en `_PRIVADO_CLINICO/`
   (FUERA de git, nunca aquí).
2. Se editan las rutas en `config/pipeline.yaml` (o se pasan por `--vcf/--hla/...`).
3. Se corre el mismo comando apuntando a los reales. Egress cero.
4. Para subir calidad: activar las piezas gated (`docs/PIEZAS-GATED.md`) →
   NetMHCpan como predictor de referencia + pVACtools (Docker) como orquestador
   + OncoKB/COSMIC para priorizar drivers.

## Tests

```bash
.venv-pipeline/bin/python pipeline/bin/test_pipeline.py        # sintéticos (siempre corren)
.venv-pipeline/bin/python pipeline/bin/test_datos_reales.py    # caso humano real (salta si no hay datos)
```

El segundo valida contra el **dataset público de Sid {{CONTACTO}}** (osteosarcoma, CC0):
comprueba que se leen variantes y péptidos, que el HLA de OptiType coincide con el que
usó pVACseq, que el VAF sale de la muestra **tumoral**, que ningún péptido generado existe
en la proteína de referencia, el solapamiento con la referencia pVACseq del propio bucket
y que el muro sigue bloqueando el egress. **Se salta limpio** si los datos no están en
disco (viven en `_cajita/datos_publicos/sid_osteosarc/`, fuera de git, con su README:
URLs, fechas, tamaños y sha256). `tests/test_all.sh` lo llama vía
`tests/test_pipeline_datos_reales.py` (rc=77 = saltado).

### Qué destapó la validación real (19-sep-26)

| Fallo | Estado |
|---|---|
| El pipeline exigía el péptido anotado en el VCF; ningún VCF clínico lo trae | ✅ `bin/preparar_reales.py` lo genera en local |
| `leer_entradas` leía el VAF de la **primera** muestra = la NORMAL en un VCF tumor-normal | ✅ usa `##tumor_sample` |
| Solo se usaba el primer péptido de cada variante; el resto se perdía en silencio | ✅ una fila por péptido |
| `Protein_position` de VEP `--total_length` (`63/446`) rompía el cálculo | ✅ parseado; guardarraíl de discordancia lo cazó antes de escribir nada |
| VEP `--per_gene` aguas arriba pierde variantes codificantes | ⚠️ no arreglable aquí: se avisa fuerte |

## Ficheros

- `bin/correr_pipeline.py` — runner (CLI).
- `bin/leer_entradas.py` — lectura local de VCF/HLA/LOH/expresión.
- `bin/presentacion_local.py` — Etapa D con MHCflurry (local, egress cero).
- `bin/clientes_api.py` — clientes AlphaFold/Ensembl/UniProt (solo genérico).
- `bin/muro.py` — guardián de privacidad (bloquea egress de crudo/PII).
- `bin/preparar_reales.py` — VCF con VEP + proteoma -> péptidos; RSEM -> TPM; OptiType -> HLA.
- `bin/test_pipeline.py` — pruebas con datos sintéticos.
- `bin/test_datos_reales.py` — pruebas con el caso humano público (se salta si no hay datos).
- `config/pipeline.yaml` — umbrales y rutas.
- `config/requirements.txt` — dependencias congeladas.
- `data_ejemplo/` — datos 100 % sintéticos (NADA real).
- `docs/INSTALADO.md` / `docs/PIEZAS-GATED.md` — qué corre hoy / qué espera a {{TITULAR}}.
- `salidas/` — dossiers generados (gitignored salvo `.gitkeep`).
```
