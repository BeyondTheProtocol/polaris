# Flex Polaris: especificación congelada v1 (fase 4), 16-oct-2026

Se congela antes de abrir ninguna expresión. Los analistas son ciegos al informe ChromaLogic. Todo lo que no esté aquí es EXPLORATORIO y no tiene control de multiplicidad.

## Entradas
- `~/Clinico-PRIVADO/flex/931c64af777e/` (tar sha256 931c64af…dcbd02). Matrices por muestra en `MP/SC_MULTI_CS/SC_MULTI_CORE/STRUCTIFY_PER_SAMPLE_OUTS/fork0/files/` (`26-28381_M1` = hígado, `B26-22813_M2` = hueso). Panel: Chromium Human Transcriptome Probe Set v2.0.0, GRCh38-2024-A.
- Prohibido: anotaciones de tipo celular de 10x, `analysis/` de Cell Ranger, el informe ChromaLogic y la nota del 15-oct.

## Pasos fijos (en este orden)
1. **QC por muestra:** genes/UMI por célula, % mitocondrial (si hay sondas MT), Scrublet (semilla 20261016). Los umbrales se fijan por MAD (3 MAD) y se informan con su n.
2. **Control de tejido:** pseudobulk de programa hepatocitario (ALB, APOA1, SERPINA1, HP, TF) y óseo/osteoclasto (COL1A1, SPP1, ACP5, CTSK). Si el hígado no muestra hepatocitos o el hueso sí, **STOP**.
3. **ARN ambiental:** fracción de cuentas hepatocitarias en células inmunes/endoteliales. Solo diagnóstico; no se corrige.
4. **Anotación:** Leiden (semilla 20261016) más marcadores canónicos.
5. **Malignas:** consenso de inferCNV (infercnvpy) y CopyKAT, con referencia no epitelial (inmunes + endotelio), y exigiendo identidad epitelial (EPCAM, KRT8, KRT18, KRT19). Se informa la concordancia entre métodos y la fracción.

## Medidas primarias (por muestra, sobre células malignas)
Para cada gen: **fracción de células con ≥1 UMI**, IC95 por bootstrap (1.000 réplicas, semilla 20261016), media de CP10k entre las positivas y n. Si un gen no tiene sonda incluida, se escribe «fuera del panel». Un 0 con sonda se escribe «0 detectadas, no implica ausencia».

- **A. Presentación antigénica:** B2M (1 sonda), TAP1, TAP2, NLRC5, PSMB8, PSMB9, IRF1, STAT1. Más un score de módulo APM (scanpy score_genes) por célula, cruzado con el score NE (CHGA, SYP, {{DIANA3}}, {{DIANA4}}, NEUROD1) y con la fase S/G2M. HLA-A/B/C/E: fuera del panel.
- **B. Antígenos cáncer-testículo y de linaje:** PRAME, MAGEA4, MAGEA1, MAGEA6, MAGEA10, MAGEA11, MAGEA12, MAGEC1, MAGEC2, CTAG2, ANKRD30A, TPBG. Antes de dar ninguna cifra de MAGEA hay que hacer una auditoría de especificidad: alinear las sondas de cada MAGEA contra los transcritos de todos los parálogos (secuencias públicas de Ensembl/RefSeq; no sale ningún dato de la paciente) y usar `*_raw_probe_bc_matrix.h5` por sonda. Si una sonda casa con más de un parálogo, la cifra queda «no resoluble a nivel de parálogo».
- **C. Dianas de superficie (ADC/CAR):** TACSTD2, ERBB2, ERBB3, CD276, NECTIN4, CEACAM5, {{DIANA2}}, SEZ6, {{DIANA}}, FOLR1, SLC39A6, VTCN1, MSLN, CLDN6, MUC1, GPNMB, ROR1, FGFR1.
- **D. Comparación hígado-hueso:** las medidas A-C en las dos muestras. Si el hueso no pasa QC o tiene menos de 200 malignas, queda INDETERMINADA.
- **E. Capa de expresión para el WES:** h5ad congelado, malignas etiquetadas, sha256. Join por Ensembl ID.

## Secundarias (descriptivas)
- Microambiente: CD8A/CD8B, CD4, FOXP3, PDCD1, HAVCR2, LAG3, TOX, CD68/CD163, TREM2, SPP1, FAP, IFNG, GZMB, CXCL9/10/13. Fracciones de tipo celular con su n.
- Eje ER (ESR1, PGR, GREB1, TFF1) y NE por célula: ¿una población o dos?
- RB1, CDKN2A, CCNE1, E2F1 en no ciclantes.

## Reglas
- ARN ≠ proteína: nunca se usan nombres de IHQ («RE», «HER2-low») sobre el Flex.
- Sin umbral de «positivo» clínico: solo fracciones con su IC.
- Cada cifra lleva n, IC y sello [verificado: script + sha256 de la salida].
- Scripts en el repo (`tools/flex_analisis/`), con versiones fijadas, semillas y `NUMBA_NUM_THREADS=1`. Salidas en zona clínica. stdout solo con agregados.
- Egress 0 con datos de la paciente.
