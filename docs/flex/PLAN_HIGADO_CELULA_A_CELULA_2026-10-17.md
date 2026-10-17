# Flex hígado célula a célula: plan de investigación (fase 1), 17-oct-2026

> BORRADOR. Fase 1: solo literatura y plan. **No se ha calculado nada sobre datos de la paciente** en esta sesión. Apoyo a la decisión, no consejo médico. Egress de datos de la paciente: 0 (a Grok solo salió una pregunta de método genérica, sin genes, cifras ni identificadores, aprobada por `enruta.py`).

## TL;DR

1. **El cuello de botella no es la herramienta, es la vara.** La definición de ChromaLogic y la regla v4 de Polaris tienen el mismo diseño (inferCNV con referencia de hepatocitos pericentrales + no epiteliales; control de periportales/reactivos; hold-out) y las dos lo cumplen (Polaris v4: AUC 0,998-0,999, FP 0/490). Polaris lo descartó post hoc al exigir que la separación **sobreviva sin genes de linaje**. Antes de probar más métodos hay que decidir esa vara (decisión 1). Con la vara actual, ningún método basado solo en expresión tiene buena probabilidad. [inferencia mía, sobre el acta del 16-17 oct y el informe ChromaLogic, ambos leídos hoy]
2. **El único método que mete información nueva es Clonalscope con prior de segmentos del WES del hígado** (Nat Methods 2025, PMID 40954304). Queda **bloqueado hasta que llegue el WES** y no está instalado. Su probabilidad depende del WES: el prior acota segmentos, pero la evidencia por célula sigue siendo expresión, y sin chr8/chr11 los brazos ya dieron AUC 0,585. Sube solo si el WES muestra eventos fuera de chr8/chr11 que no estén ya en la plantilla del hueso.
3. **Barato y local, ahora:** un modelo explícito de dobletes heterotípicos hepatocito-tumor (scDblFinder, scCDC). Ataca la pregunta abierta del acta: de dónde sale el residuo, que hoy solo se deduce por eliminación. Probabilidad de resolver la llamada: baja-media. Coste: unas 4-6 h con reglas congeladas antes. La matriz de solo sondas de unión queda detrás: limpia gDNA, que en el hígado es unas 70 veces más escaso por célula que en el hueso, y no toca el ARN ambiental tumoral, que es el problema.
4. **El ADN agregado de 8p11 en el hígado no tiene potencia.** Con 0-14 UMI por grupo no puede discriminar entre vías. Hacen falta unas 100 UMI de sondas silentes por grupo (cálculo abajo). Se reclasifica como «no informativo por potencia», no como fallo.
5. **Si nada funciona:** ninguna prueba ortogonal etiqueta barcodes del Flex (los núcleos se disociaron). WES, FISH de FGFR1, IHQ y Xenium/Visium HD dan **fracción y pureza**, no identidad por célula. El criterio de éxito ortogonal tiene que ser poblacional.

## Cómo define ChromaLogic las células tumorales del hígado (verificado: texto del informe HTML extraído hoy con `python3 -I` a stdout)

- **QC:** de 20.625 barcodes a 16.891 células. Fuera si genes < 500, MT > 10 %, Scrublet > 0,25 o CellSweep α > 0,7 (fila «ALL case-1 thresholds»: 3.734 células). Además, 164 dobletes excluidos por subclúster. Polaris parte de 19.047 tras su propio QC: **diferencia de método**.
- **Anotación primero, CNV después.** Siete compartimentos reclusterizados (36 subclústeres), etiquetados por marcadores con revisión ciega. Siete subclústeres «Tumour» suman 13.217 células, más «Cili_0» (173, programa FOXJ1+, GATA3+).
- **inferCNV:** ventanas de 100 genes, GENCODE v32. Referencia: hepatocitos pericentrales (1.045) + inmunes/estroma/endotelio. Hold-out del 20 % de la referencia no hepatocitaria (311). Control negativo: periportales/reactivos (891).
- **Llamada maligna:** **solo el criterio de correlación** con el perfil consenso. Ellos mismos declaran que el criterio de carga «no separa» y lo dejan como sensibilidad. Resultado: 12.933 de 13.217 «Tumour» (97,9 %) + 165 «Cili_0» = **13.098**. Con la regla estricta (correlación + carga): **321** en el grupo «Tumour», 324 con las 3 de «Cili_0».
- **Controles:** 0/891 periportales y 4/311 del hold-out llamados malignos. Concordancia hígado-hueso: r 0,90 por ventana (nulo 0,20), 13/14 regiones concordantes, 262/269 tumorales del hueso con el perfil del hígado.
- **Amplicones incluidos:** el perfil usa todo el genoma, chr8 y chr11 incluidos (8p11-12 z 8,36; {{LOCUS}} z 1,22 en el hígado). Todo es CNV inferida de ARN y ellos lo marcan «unconfirmed».
- **Lectura [inferencia mía]:** es la misma clase de diseño que la regla v4 de Polaris, y los dos lo cumplen. La diferencia está en si se exige independencia de linaje y de amplicones. Polaris lo exige; ChromaLogic no lo plantea. Son **definiciones distintas**, no un error de nadie.

## Qué se ha probado ya (no se reabre)

inferCNV/infercnvpy · CopyKAT · SCEVAN · regla tipo DFCI v4 (cumple; descartada post hoc sin linaje) · SoupX/DecontX/CellBender (AUC de cierre 0,589-0,602) · referencia externa 10x Liver_BC1 (AUC 0,56-0,57) · metacélulas (r con el hueso 0,12) · ADN agregado de sondas unspliced (0-14 UMI por grupo). Numbat y XClone, inviables: el Flex no conserva alelos.

## Métodos candidatos, por probabilidad de éxito y coste

Todo «probabilidad» es **inferencia mía** sobre la evidencia citada. Ninguna fuente prueba el método en Flex de metástasis hepática.

| # | Método | Qué tiene de nuevo frente a lo probado | ¿Independiente de linaje? | Probabilidad / coste | Evidencia (tier) |
|---|---|---|---|---|---|
| 1 | **Clonalscope con prior de segmentos del WES hepático** | Mete los segmentos de ADN del propio tumor del hígado como prior. Es la única vía con información genómica externa a la expresión | Sí, si se excluyen chr8/chr11 del prior o se informa aparte | **Condicionada al WES / medio.** Media-baja por defecto, porque la señal por célula sigue siendo expresión y los brazos sin chr8/11 ya dieron 0,585. Sube si el WES trae eventos nuevos fuera de chr8/11. Bloqueado por el WES; instalar el paquete en R | Método, Nat Methods 2025, [10.1038/s41592-025-02773-5](https://doi.org/10.1038/s41592-025-02773-5), PMID 40954304. Abstract: etiqueta malignas con prior de DNA bulk. Validado en tumores gastrointestinales y spatial, **no en Flex ni en hígado**. Benchmark: [10.64898/2026.04.12.718050](https://doi.org/10.64898/2026.04.12.718050) (preprint; lo sitúa entre las fiables «expression-centric» cuando no hay alelos) |
| 2 | **Modelo explícito de dobletes heterotípicos hepatocito-tumor** (scDblFinder con simulación dirigida; scCDC para contaminación gen a gen) | Ataca el origen del residuo, que el acta deja «por eliminación, sin dato directo». Lo probado era Scrublet genérico | Sí: modela la mezcla, no el linaje | **Baja-media / bajo-medio.** scDblFinder y scCDC no están instalados | scDblFinder, método, [10.12688/f1000research.73600.2](https://doi.org/10.12688/f1000research.73600.2); scCDC, método, Genome Biol 2024, [10.1186/s13059-024-03284-w](https://doi.org/10.1186/s13059-024-03284-w). Ninguno validado en Flex |
| 3 | **inferCNV/CopyKAT sobre la matriz de sondas de unión (spliced)** | Quita el ADN genómico de la señal de expresión, sonda a sonda (`*_raw_probe_bc_matrix.h5`). Lo probado usaba la matriz a nivel de gen | No por sí solo. Se mide con la vara de la decisión 1 | **Baja / bajo** (infercnvpy y copykat ya instalados). Limpia gDNA, que en el hígado es escaso (unas 70 veces menos por célula que en el hueso, `REGLAS_VIAS_PROPIAS_v1`); el ARN ambiental tumoral entra igual por las sondas de unión | Mecanismo: según la documentación de Cell Ranger, las sondas que no cruzan una unión exón-exón pueden hibridar gDNA, y el gDNA sube en FFPE con decrosslinking > 70 °C y en muestras de baja complejidad. **Leído solo como resumen del buscador; la página dio 429 y no se abrió.** Sin paper que lo use para CNV (no encontrado) |
| 4 | Clasificadores pan-cáncer (scMalignantFinder, Seesaw, scATOMIC, CaSee, PreCanCell) | Ninguno: es otra lectura de expresión | **No: dependen del linaje por construcción** | Baja / bajo. Solo como concordancia, nunca como veredicto | scMalignantFinder [10.1038/s42003-025-07942-y](https://doi.org/10.1038/s42003-025-07942-y); Seesaw [10.1038/s42003-026-10172-5](https://doi.org/10.1038/s42003-026-10172-5) (AUC mediana 0,93 en 44 datasets); scATOMIC [10.1038/s41467-023-37353-8](https://doi.org/10.1038/s41467-023-37353-8); CaSee [10.1038/s41388-022-02478-5](https://doi.org/10.1038/s41388-022-02478-5); PreCanCell [10.1016/j.csbj.2023.07.009](https://doi.org/10.1016/j.csbj.2023.07.009); DeepMalignant (preprint) [10.64898/2026.06.29.734828](https://doi.org/10.64898/2026.06.29.734828). Benchmarks computacionales, entrenados en 3'/5' frescos, no en Flex |
| 5 | Transferencia supervisada desde las 68 malignas del hueso | Ninguno real: con genes de linaje aprende linaje, y sin ellos repite la plantilla de brazos del cierre (eje 2, AUC 0,585) | Solo sin linaje, y entonces no es nuevo | **Baja / bajo** | Sin paper que lo haga entre lesiones del mismo paciente en Flex (no encontrado en PubMed ni Consensus) |
| 6 | Foundation models (Geneformer, incluido `nvidia/geneformer_V2_104M_CLcancer`; scGPT; UCE) | Embeddings preentrenados | No | **Baja / medio** (descarga de pesos, GPU o CPU lenta; sin torch en `nescore`) | Evaluación zero-shot: limitaciones frente a baselines simples, Genome Biol 2025, [10.1186/s13059-025-03574-x](https://doi.org/10.1186/s13059-025-03574-x). Para malignidad no se encontró evaluación (sin fuente). HF: [ctheodoris/Geneformer](https://hf.co/ctheodoris/Geneformer), [nvidia/geneformer_V2_104M_CLcancer](https://hf.co/nvidia/geneformer_V2_104M_CLcancer) |

Inviables o ya cerrados: Numbat y XClone (necesitan alelos; Numbat [10.1038/s41587-022-01468-y](https://doi.org/10.1038/s41587-022-01468-y), XClone [10.1038/s41467-024-51026-0](https://doi.org/10.1038/s41467-024-51026-0)). Deconvolución con atlas hepáticos (MacParland [10.1038/s41467-018-06318-7](https://doi.org/10.1038/s41467-018-06318-7); Guilliams [10.1016/j.cell.2021.12.018](https://doi.org/10.1016/j.cell.2021.12.018)): el acta ya la dio por no discriminante. Un atlas no aporta nada contra un problema de ambiente tumoral dentro de los hepatocitos.

Contexto de benchmarks de CNV (benchmarks computacionales, no Flex):
- Schmid 2025, Nat Commun, [10.1038/s41467-025-62359-9](https://doi.org/10.1038/s41467-025-62359-9): el rendimiento depende del dataset y de la referencia; los métodos con información alélica son más robustos en datos de gotas grandes.
- Song 2025, Brief Bioinform, [10.1093/bib/bbaf076](https://doi.org/10.1093/bib/bbaf076): Numbat el mejor; CopyKAT, el mejor con solo matriz.
- Chen 2025, Precis Clin Med, [10.1093/pcmedi/pbaf011](https://doi.org/10.1093/pcmedi/pbaf011): la elección de referencia y la profundidad mandan.
- Esto apunta en la misma dirección que lo vivido: **sin alelos, la referencia lo decide**. En el hígado no hay referencia epitelial mamaria normal (acta, precondiciones del cierre).

FFPE y Flex en tumores:
- Yan 2026, Front Genet, [10.3389/fgene.2026.1755978](https://doi.org/10.3389/fgene.2026.1755978): 12 pares FFPE/congelado; inferCNV detecta malignas candidatas en FFPE.
- Xirenayi 2025, preprint, [10.1101/2025.11.25.690485](https://doi.org/10.1101/2025.11.25.690485): snFlex en ccRCC.
- snPATHO-seq, [10.1038/s42003-024-07043-2](https://doi.org/10.1038/s42003-024-07043-2).
- **Ninguno trata una metástasis hepática con ambiente tumoral alto.** Validez externa limitada.

## Criterio de éxito prefijado (comparable a los anteriores)

Se congela en `REGLAS_HIGADO_CELULA_v1.json` **antes** de abrir datos. Mismas cifras que v4 y cierre, para que sea comparable:

1. **AUC ≥ 0,6** del score frente a todos los hepatocitos de A y frente a los periportales/reactivos. Se mide **en la rama que salga de la decisión 1**: con genes de linaje, sin ellos, o las dos informadas.
2. **FP ≤ 1 % en periportales/reactivos y ≤ 2 % en H2**, con IC de Wilson, y la cota superior de Wilson informada junto al punto.
3. **ADN 8p11:** solo cuenta si hay potencia. Regla: razón de tasas (sondas silentes frente a OR) llamadas/no llamadas con IC que excluye 1, y **solo si cada grupo tiene ≥ 100 UMI de sondas silentes**. Si no, «no informativo por potencia», y no cuenta ni a favor ni en contra.
   - Cálculo [inferencia mía]: si el pool es mayoritariamente tumor, la razón esperada llamadas/no llamadas es pequeña. Con pureza 0,95 frente a 0,6 y unas 20 copias frente a 2, sale (2+18×0,95)/(2+18×0,6) ≈ 1,5. Para ver 1,5 con α 0,05 bilateral y potencia 0,8 hacen falta unas 2×(1,96+0,84)²/ln(1,5)² ≈ 95 UMI por grupo.
   - Las 0-14 observadas quedan unas 7-10 veces por debajo. Las 100 UMI son una cota inferior: la calibración con OR suma varianza.
   - Para que el criterio sea decidible: en la fase 2, antes de congelar, se comprueba si el total de UMI silentes de 8p11 en el pool llega a 2 × 100. Si no llega, el criterio se retira desde el principio y no se deja para declararlo «no informativo» después.
4. **Correlación con el clon del hueso:** r de Pearson por brazo **sin chr8 ni chr11** > 0,3 (primaria, la de VIAS_v1). Con amplicones, se informa aparte y no decide (es la vara de ChromaLogic, r 0,90 por ventana, y no es comparable).
5. **Estabilidad de dianas:** rango < 10 puntos para ANKRD30A, TPBG y ERBB3 entre vías.
6. **Chequeo poblacional:** la fracción llamada tiene que caer dentro del 58-82 % del H&E. Se recontrasta con la pureza del WES cuando llegue.

## Decisiones para {{TITULAR}}

1. **La vara de linaje.** ¿La llamada célula a célula exige independencia de linaje (vara actual de Polaris), o basta linaje + control de periportales + control de dobletes (vara de ChromaLogic y de la v4)? Con la segunda, el hígado ya está «resuelto» por la v4. Con la primera, solo el WES lo puede mover.
2. **Cuándo lanzar.** Lanzar ya, con reglas congeladas, la vía de dobletes (y la de sondas de unión, rebajada), o esperar al WES y lanzar Clonalscope.
3. **Gastar bloque.** FISH de FGFR1 o IHQ ER/GATA3 frente a HepPar-1 para tener una fracción poblacional, frente a reservar el bloque.

## Si nada funciona: pruebas ortogonales (dan fracción, no identidad por barcode)

| Prueba | Qué resuelve | Qué no resuelve | Coste / bloqueo |
|---|---|---|---|
| **WES del bloque hepático** | Pureza, segmentos de copia (8p11, {{LOCUS}}, brazos), mismo clon que el hueso. Habilita el método 1 | La identidad de cada célula del Flex | Ya pedido en la ruta (fecha: no lo sé) |
| **FISH de FGFR1 en corte del bloque** | % de núcleos con amplificación en tejido, directo y por núcleo | No se vincula a barcodes | Barato si queda bloque (U5 del plan del 16-oct: no lo sé) |
| **IHQ ER/GATA3 frente a HepPar-1/arginasa** | Fracción tumor/hepatocito en el mismo bloque | Lo mismo | Barato; compite por bloque |
| **Xenium o Visium HD con panel a medida** | Célula a célula en tejido, incluido el ambiente | No se vincula a barcodes del Flex; panel de cientos de genes; caro | Proveedor y presupuesto: sin explorar |

## UNKNOWNS

- Fecha del WES hepático y si traerá segmentos de copia utilizables como prior.
- Cuánto bloque hepático queda para FISH, IHQ o spatial.
- Si los exports por célula de ChromaLogic (`CASE-02_M1_cell_metadata.csv`, según su informe) están en zona clínica. Servirían para medir **concordancia por barcode** con cualquier vía nueva, no como verdad.
- Dónde corrió SCEVAN: no aparece en los entornos de `~/.claude-science` (no verificado en esta sesión).
- La página de algoritmos de Cell Ranger sobre gDNA: no se pudo abrir (429). Hay que cotejarla antes de citarla fuera.
- Si existe algún grupo con Flex en metástasis hepáticas que haya publicado su llamada maligna: no encontrado en PubMed, Consensus ni bioRxiv. bioRxiv vía MCP no admite búsqueda por palabra clave: solo se revisaron 30 títulos recientes de bioinformática. «No encontrado» no es «no existe».

## Fuentes buscadas

- PubMed (MCP y E-utilities).
- Consensus: 1 búsqueda; la segunda dio límite de tasa.
- bioRxiv MCP: listado sin palabra clave.
- Hugging Face Hub.
- WebSearch: 10x; la página dio 429.
- Grok: pregunta genérica aprobada por `enruta.py`. Solo se citan las referencias que se cotejaron en PubMed o Crossref. Lo de scCancerDx no se cita, porque no se verificó.
- Capa china: se abrieron los abstracts de scMalignantFinder, Seesaw, CaSee, PreCanCell, el benchmark de Brief Bioinform y el BCLM pareado de Cancer Res 2026 ([10.1158/0008-5472.CAN-26-1201](https://doi.org/10.1158/0008-5472.CAN-26-1201), 13 pacientes; no detalla en el abstract cómo llama las malignas). No se abrieron CDE ni ChiCTR: no aplican a una pregunta de método.

## Entornos locales (verificado hoy con `ls` de site-packages y librerías R)

- `nescore`: scanpy 1.11.5, anndata, infercnvpy 0.6.1, celltypist 1.7.1, decoupler 2.2.0, scrublet 0.2.3, scikit-learn, leidenalg.
- `cellbender`: cellbender 0.4.0.post1 (commit f36ae299), torch 2.4.1.
- `ambiente-r`: Seurat, SoupX, celda (DecontX), scran.
- `r`: copykat.
- **No instalados:** scDblFinder, scCDC, Clonalscope, SCEVAN, scvi-tools, scGPT, Geneformer.
