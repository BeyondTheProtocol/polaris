# Lo que ya está INSTALADO y PROBADO (corre hoy, sin clave de {{TITULAR}})

> Todo esto se instaló y verificó en local (arm64, macOS) el 2026-06-22. Egress cero
> para datos crudos. Sin licencia ni registro de {{TITULAR}}. Probado con datos sintéticos.

## Entorno

- **venv dedicado:** `~/claudecode/.venv-pipeline` (Python 3.12). Separado de las
  otras venvs del sistema para no chocar dependencias.
- Reinstalar todo desde cero: `pip install -r pipeline/config/requirements.txt`
  (+ `mhcflurry-downloads fetch models_class1_presentation` una vez).

## Piezas instaladas

| Pieza | Versión | Rol en el pipeline | Egress |
|---|---|---|---|
| **MHCflurry** | 2.2.1 | Etapa D — presentación pMHC clase I (motor abierto, local) | CERO |
| modelos `models_class1_presentation` | 2.2.0 | pesos descargados (público, una vez) | — |
| **pysam** | (última) | lectura de VCF (Etapa C → entradas) | CERO |
| **pandas / requests / pyyaml** | (última) | tabla de dossier + clientes API | — |
| cliente **AlphaFold-DB** | propio | estructura 3D por accession (genérico) | solo gen/accession |
| cliente **Ensembl REST** | propio | lookup de gen por símbolo (genérico) | solo símbolo de gen |
| cliente **UniProt** | propio | símbolo de gen → accession (genérico) | solo símbolo de gen |
| **muro.py** | propio | bloquea egress de VCF/HLA/PII (probado: 6/6) | — |

## Verificación hecha

- `predecir_presentacion(["GILGFVFTL","NLVPMVATV"], ["HLA-A*02:01"])` → ambos
  epítopos canónicos de A*02:01 puntúan alto; SIINFEKL (de ratón) puntúa bajo.
  La biología sale coherente → el motor está bien cableado.
- Run completo con datos sintéticos → dossier priorizado, LOH filtrado, expresión
  filtrada, estructura AlphaFold anotada (ESR1→P03372, TP53→P04637, PIK3CA→P42336).
- `muro.py`: bloquea host no permitido, VCF crudo, alelo HLA, nombre, secuencia cruda;
  permite gen genérico a host permitido. 6/6.
- `test_pipeline.py`: TODO OK.

## Cómo correrlo

Ver `pipeline/README.md`.
