# Datos de EJEMPLO (100 % sintéticos)

> ⚠️ **NADA aquí es real ni de {{TITULAR}}.** Son datos inventados para CABLEAR y PROBAR
> el pipeline en seco. Variantes, alelos HLA y péptidos son ficticios o epítopos
> de control públicos de inmunología (p. ej. influenza GILGFVFTL). No hay PII, ni
> datos genómicos crudos del caso. El día que llegue la biopsia de Zúrich, estos
> ficheros se SUSTITUYEN por los reales (que se quedan en `_PRIVADO_CLINICO/`,
> nunca aquí ni en git).

## Ficheros

| Fichero | Qué simula | Equivalente real (Zúrich) |
|---|---|---|
| `ejemplo.vcf` | VCF somático mínimo (SNV/indel) anotado | salida de Mutect2 ∩ Strelka2 (Etapa C-DNA) |
| `ejemplo.hla.txt` | alelos HLA-I y II (4 dígitos) | salida de OptiType/HLA-LA/arcasHLA (Etapa B) |
| `ejemplo.hla_perdidos.txt` | alelos perdidos por LOH | salida de LOHHLA (Etapa B) — se EXCLUYEN |
| `ejemplo.peptidos.tsv` | péptidos candidatos + alelo (entrada a Etapa D) | derivados de las variantes expresadas |
| `ejemplo.expresion.tsv` | TPM por gen (filtro de expresión) | salida de Salmon/kallisto (Etapa C) |

## Muro de privacidad (recordatorio)

A las APIs externas (AlphaFold-DB, Ensembl) solo va **terminología genérica**
(símbolo de gen, accession de proteína). El binding/presentación se calcula
**100 % en local** con MHCflurry → **egress cero** para péptidos y HLA.
