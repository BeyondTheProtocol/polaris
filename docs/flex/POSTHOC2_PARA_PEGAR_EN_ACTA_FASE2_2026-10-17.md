# Para pegar en el acta de la fase 2 (post hoc 2, 17-oct)

Destino: `00_FUENTE-DE-VERDAD/01 · Tratamiento/flex-hígado-célula-a-célula-resultado-de-la-fase-2-2026-10-17.md` (casa base, fuera de git). **YA PEGADO (17-oct, 23:51)**, con las correcciones de la verificación, mediante `tools/archivar_nota.py`; el acta quedó byte a byte igual a lo construido (`cmp`). No volver a pegarlo. Para que `archivar_nota` aceptara el cuerpo, el H1 del acta subió encima del bloque PREVALECE.

- **Bloque A**: justo debajo del bloque «Red-team (17-oct) · PREVALECE» (después del punto 12, con una línea en blanco).
- **Bloque B**: sección nueva entre «## Post hoc declarados (no deciden ningún veredicto)» y «## Limitaciones».

---

## Bloque A

> **Post hoc 2 (17-oct), declarados · NO cambian ningún veredicto** (detalle y fuentes en la sección «Post hoc 2 (17-oct), declarados»). Matizan los puntos 2, 4, 5, 8 y 9 de arriba:
> - Punto 4: C3 reconoce un perfil idéntico al del hueso: las mismas 68 células, por mitades y pasadas por la referencia hepática, dan r 0,88 (control optimista: misma muestra, elegidas por consenso de inferCNV y usadas para fijar el filtro de linaje). Las llamadas dan 0,19-0,21. No prueba potencia para un clon hermano en otro tejido o estado, y no responde a la objeción de los 34 brazos: el 0,3 sigue sin nulo. Sigue sin distinguir «otro clon», «otro estado» o «no tumor».
> - Punto 2: el ruido de C2 es del cuantil, no del reparto de tipos (estratificar no lo arregla). Las 5 FP de H2 son de los controles más profundos; 3/5 llevan ARN tumoral y 3/5 son dobletes con la configuración Flex. Profundidad, ARN tumoral y marca de doblete van juntos y con n = 5 no se separan.
> - Punto 9: con dbr.sd = 1, scDblFinder marca el 22,3 % y E del residuo pasa a «explican»; sin clusters, 8,8 % y «no explican». El residuo depende de la configuración.
> - Punto 5: el clasificador propio de REFCON corrió en submuestras y cae a su rama de «muestra pura» (1 diploide de referencia): no da llamada por célula aquí.
> - Punto 8: el HEP5 de las 628 es mayoritariamente ambiente según CellBender (0,6 % conserva ≥ 2 HEP5); no se comportan como hepatocitos (mediana de 3 UMI de HEP5 frente a 49). «Ambiente» lo dice la misma herramienta que quita esas cuentas, no un dato independiente.

---

## Bloque B

## Post hoc 2 (17-oct), declarados

> **No cambian ningún veredicto.** Siguen: estándar NO RESUELTO, estricto NO RESUELTO; REFCON estándar NO RESUELTO, REFCON estricto FRÁGIL. Cambian la lectura.
> Plan `tools/flex_analisis/higado_celula/POSTHOC_2_PLAN.json` (sha256 9aa82ab2…, commit 155aafd6), escrito y commiteado antes del código (commit 5d77aaad) y antes de calcular, con las lecturas fijadas a priori. La etapa final se negaba a correr si el plan no tenía ese sha. P3 y P4 no lo comprueban: que corrieran después del plan lo prueba el registro de la ventanilla (`.claude/logs/clinico-access.log`): P3 a las 22:43:48 y P4 a las 22:45:58, después del commit del plan (22:35:29) y del código (22:43:45); no hay ninguna ejecución de posthoc2 anterior.
> Fuentes: **PH2** = `~/Clinico-PRIVADO/flex/higado_celula/posthoc2/posthoc2.json` (entre corchetes, la clave); **CSV** = `posthoc2/p2_semillas_holdout.csv`. Todo leído con `lector_clinico.py`: es **verificado** salvo lo marcado «lectura» (inferencia mía).
> Procesador `flex_hc_posthoc2` (nuevo, con test). Egress 0: dentro del sandbox, un connect a loopback dio EPERM [muro]. La etapa final, dos ejecuciones con sha256 idénticos (`MANIFIESTO_POSTHOC2.json`, `dos_ejecuciones.identicas = true`); P3 y P4 corrieron una vez, en caché; las 8 salidas canónicas de la fase 2, intactas.

### P1 · Sensibilidad de C3: ¿qué r alcanza un clon verdadero?

C3 es expresión media por brazo (34 brazos, sin genes de linaje ni chr8/chr11), no inferCNV. Las 68 malignas limpias del hueso se pasaron por la tubería del hígado (referencia hepática de la base).

| Serie (1.000 particiones 34/34 de las 68; plantilla = la otra mitad, con referencia ósea) | Mediana | 2,5-97,5 | r > 0,3 |
|---|---|---|---|
| Las 68 del hueso, mitad A con **referencia hepática** [P1.r_por_brazo.mitades_primaria.r_hep_A] | **0,881** | 0,810-0,932 | 100 % |
| Las 68 del hueso, mitad A con referencia ósea (techo de ruido) [r_hueso_A] | 0,917 | 0,865-0,953 | 100 % |
| 34 llamadas del pool (estándar base) al azar [r_llam34] | **0,187** | 0,005-0,364 | 11,1 % |
| 34 no llamadas del pool al azar [r_nollam34] | 0,139 | −0,094-0,352 | 7,4 % |
| Todas las llamadas del pool (8.756) [r_llam_todas] | 0,206 | 0,108-0,292 | 1,4 % |

- Cota superior circular (las 68 enteras con referencia hepática frente a su propia plantilla): 0,963 [P1.r_por_brazo.circular_68_ref_hepatica_vs_plantilla_canonica_COTA_SUPERIOR.primaria]. Cambiar a la referencia hepática cuesta 0,036 de r [coste_referencia_hepatica].
- El r canónico se reproduce exacto: 0,2108 [r_canonica_llamadas_pool_estandar_recalculada].
- **Lectura por las reglas del plan: «0,3 era ALCANZABLE»**; las llamadas se parecen al clon del hueso «MENOS QUE UN CLON VERDADERO a n emparejado», y las no llamadas, también [LECTURA_NO_DECIDE].
- **Spike-in en la llamada.** El estándar llama a las 68 (68/68; Wilson 94,7-100 %) [P1.estandar.spikes_llamados]. El estricto, sin genes de linaje, llama a **6/68 (8,8 %; 4,1-17,9 %)** [P1.estricto.spikes_llamados], mientras llama al 25,8 % del pool. Mediana del score de las 68 en el estricto: 0,084, frente a un umbral de 0,262 y 0,324 en las llamadas del pool [P1.estricto.score_mediana].
- Chequeo: el consenso y el umbral reproducen la base canónica (8.756/12.561 y H2 5/141; 3.246/12.561, pp 5/426 y H2 2/141) [reproduce_base_canonica]. Añadir las 68 al último trozo de infercnvpy cambia 11 y 10 llamadas del pool (Jaccard 0,9987 y 0,9969) [perturbacion_del_ultimo_trozo]; esas filas no se usan.
- Lectura: la métrica de C3 alcanza 0,88 con la población del hueso, y las llamadas del hígado quedan muy por debajo de ese control optimista (0,21). El estricto apenas reconoce al clon óseo: el consenso del pool hepático sin linaje no es el perfil de ese clon. La llamada estándar llama a las 68 seguramente porque comparten el linaje mamario, no por copias (inferencia mía). Con estos datos no se separa «el hígado tiene otro perfil de brazos (otro clon o subclón)», «mismo clon en otro estado de expresión» y «no es tumor».
- Límite: las 68 se eligieron en el hueso con un inferCNV de consenso (perfil compartido por construcción), salen de la misma muestra y fijaron el filtro de linaje. El 0,88 es **optimista**; cuánto, **no lo sé**. La distancia a 0,21 es grande. No prueba potencia para un clon hermano en otro tejido o estado, ni responde a la objeción de los 34 brazos (IC de Fisher de r 0,21: −0,14 a 0,51; acta, PREVALECE 4).

### P2 · Reparto de H1/H2 y las FP de la base

| Serie (semillas 1-20) | C1 pasa | C2 pasa | C5 pasa | C2 y C5 | Etiqueta informativa | FP en H2 (mín-mediana-máx; media) |
|---|---|---|---|---|---|---|
| Estándar, aleatoria (= post hoc canónico) | 20 | 10 | 3 | **0** | 10 FRÁGIL, 10 NO RESUELTO | 0-2,5-7; 2,75 |
| Estándar, **estratificada por tipo** | 20 | **8** | 3 | 0 | 8 FRÁGIL, 12 NO RESUELTO | 0-3-9; 3,65 |
| Estricto, aleatoria | 20 | 9 | 0 | 0 | 9 FRÁGIL, 11 NO RESUELTO | 0-2-7; 2,4 |
| Estricto, estratificada | 20 | 9 | 0 | 0 | 9 FRÁGIL, 11 NO RESUELTO | 0-2-7; 2,4 |

Fuente: [P2.barridos.<criterio>|<partición>] y CSV.

- El barrido aleatorio reproduce el canónico en las 40 filas [P2.reproduce_posthoc_semillas_canonico]. La estratificación usa el mismo hold-out y el mismo score por semilla; solo cambia qué mitad es H1.
- Emparejado por semilla (estándar): 7 pasan con las dos, 3 solo con la aleatoria, 1 solo con la estratificada y 9 con ninguna [P2.barridos.estandar|emparejado.C2_por_semilla]. El desequilibrio de tipos entre H1 y H2 no se relaciona con las FP (Spearman −0,005; en el estricto, −0,061) [desequilibrio_aleatoria].
- **Lectura por la regla del plan: «el ruido de C2 es el del cuantil con n ≈ 140, no el reparto de tipos»** (los dos criterios).
- C1 pasa en 20/20 semillas (AUC frente a hepatocitos ≥ 0,9997 en el estándar y ≥ 0,882 en el estricto) [AUC_hep_min]. Con eso queda **verificada** la inferencia del juez: en el aleatorio, 10 semillas serían FRÁGIL y 10 NO RESUELTO; ninguna, RESUELTO.
- Base canónica (semilla 0) estratificada: el estándar da 3/141 en H2 (2,1 %: sigue fallando por una célula); el estricto, 6/141 en H2 y 6/426 en periportales [P2.barridos.<criterio>|estratificada.base_semilla_0].
- Composición de la base: H1 = 19 NK, 33 endotelio, 42 estrelladas y 46 mieloides; H2 = 17, 39, 32 y 53 [base_semilla_0.H1/H2]. En las 20 semillas aleatorias, las 55 FP del estándar fueron 22 NK, 11 endotelio, 17 estrelladas y 5 mieloides [FP_H2_por_tipo_total]: los NK son un 13 % del hold-out (36 de 281, igual en todas las semillas) y el 40 % de las FP (cálculo mío sobre esos recuentos).

Las 5 FP de H2 del estándar base y las 3 células de H1 por encima del umbral [P2.FP_de_la_base]:

| Grupo | Tipo | UMI | Testigos tumorales (CellBender) | Doblete con dbr.sd = 1 |
|---|---|---|---|---|
| FP H2 | NK | 3.765 | 2 UMI | sí |
| FP H2 | mieloide | 2.674 | 1 UMI | no |
| FP H2 | estrellada | 2.099 | 0 | sí |
| FP H2 | endotelio | 2.970 | 0 | sí |
| FP H2 | NK | 3.021 | 3 UMI | no |
| H1 sobre el umbral | estrellada | 7.468 | 3 UMI | sí |
| H1 sobre el umbral | estrellada | 2.990 | 1 UMI | sí |
| H1 sobre el umbral | mieloide | 2.366 | 0 | sí |

- Comparador, las 136 no llamadas de H2: mediana de 1.397,5 UMI (IQR 1.023-1.849), testigos en el 14,0 % (19/136) y dobletes con dbr.sd = 1 en el 22,1 % [estandar_H2_no_llamadas_AGREGADO]. FP con testigos, 3/5 frente a 19/136: exacto de Fisher p = 0,027 (OR 9,2) [estandar_testigo_FP_vs_no_llamadas].
- Las 8 son singletes de dbr006 por construcción (la base excluye sus dobletes) y todas superan el percentil 75 de UMI de las no llamadas.
- **Lectura por la regla del plan: «C2 falla en controles con ARN tumoral».** Matiz: las 8 células que cruzan el umbral son los controles más profundos. «Testigo+» (≥ 1 UMI) y la marca de doblete suben las dos con la profundidad, así que profundidad, ARN tumoral y marca de doblete van juntos y con n = 5-8 no se separan. Lo sostenible: el umbral y las FP los fijan los controles más profundos, no una muestra al azar de células inmunes.
- Estricto: sus 5 FP de periportales son hepatocitos de 1.249 a 8.974 UMI; 1/5 con testigos y 2/5 dobletes con dbr.sd = 1 [estricto_FP_periportales].

### P3 · scDblFinder en la configuración para Flex y sin clusters

| Configuración | Dobletes totales | En el pool QC | Entre las llamadas del pool del estándar base | E del residuo (CellBender; IC) | Lectura de la regla D2a (informativa) |
|---|---|---|---|---|---|
| dbr 0,06 (canónica) | 9,0 % (1.708) | 5,7 % | 0 (excluidos) | 42,7 % (38,5-47,1) | una parte |
| dbr por defecto (canónica) | 17,3 % (3.297) | 13,0 % | 10,7 % | 64,3 % (60,1-68,4) | explican |
| **dbr.sd = 1** (viñeta, Flex) | **22,3 % (4.256)** | 18,0 % | **17,8 % (1.557/8.756)** | **73,5 % (69,5-77,2)** | **explican** |
| **Sin clusters**, removeUnidentifiable = FALSE | 8,8 % (1.670) | 7,5 % | 6,4 % | 18,8 % (15,7-22,4) | **no explican** |
| Sin clusters + dbr.sd = 1 (cruce, declarado) | 27,8 % (5.298) | 27,3 % | 30,2 % | 54,1 % (49,8-58,4) | explican |

Fuente: [P3.configuraciones.<config>.total / pool_QC_sin_exclusion / entre_llamadas_pool_estandar_base / residuo_cellbender.E_dobletes_entre_marca_mas / LECTURA_REGLA_D2a_INFORMATIVA].

- Las llamadas sin exclusión reproducen la v4 (6.245/13.320) [P3.llamadas_sin_exclusion_reproducen_v4]. De las 6.831 llamadas sin exclusión fuera de referencia (el denominador de la función canónica `composicion_dobletes`), el 34,2 % son dobletes con dbr.sd = 1 (2.333/6.831; 12,1 % con dbr 0,06, 826/6.831) [control_dobletes_llamadas_estandar_sin_exclusion.dobletes_entre_llamadas]. La razón de odds llamadas/no llamadas en el pool baja de 13,0 a 8,3 [OR_doblete_llamadas_vs_no_llamadas_en_pool].
- dbr.sd = 1 contiene los 1.708 dobletes de dbr 0,06: inferido del Jaccard, que vale exactamente 1.708/4.256 = 0,401 [jaccard_con_dbr006]; no es un recuento directo. Sin clusters solapa poco (Jaccard 0,38).
- **Lectura por la regla del plan: «el 9,0 % era cota inferior en el pool», CONFIRMADO** (sin clusters, 7,5 % frente a 5,7 %, IC sin solape) [P3.LECTURA_NO_DECIDE].
- Proxy de homotípicos: el 8,4 % de las llamadas del pool tiene más del doble de la mediana de UMI del pool, frente al 0,18 % de las no llamadas [P3.proxy_homotipicos_UMI].
- Lectura: la cifra de dobletes de este Flex va del 8,8 % al 27,8 % según una opción del método, y la lectura del residuo pasa de «no explican» a «explican». Con esta herramienta, en esta muestra, **no se puede atribuir el residuo a dobletes con seguridad**. Con la configuración de sus autores para Flex, cerca de 1 de cada 6 llamadas que sobrevivieron a la exclusión canónica sería un doblete.

### P4 · Clasificador propio de REFCON (classify.py) en submuestras

- Memoria libre medida en el hijo: 9,0 GB; N = 5.000 (estimado 4,2 GB); 3 réplicas disjuntas [P4.info].
- En las 3, su detector de diploides confiables (marcadores de SCEVAN) encuentra **1 célula** (un mieloide), y el mínimo es 2 [P4.info.por_replica.n_conf; P4.por_replica.diploides_confiables_por_tipo]. El clasificador cae a su rama «PURE_soft»: una llamada **de muestra**, no por célula, por dispersión (aneuploidy score 0,142-0,143 frente al corte 0,135, «tumor-ish») [P4.info.por_replica]. Etiqueta aneuploides al 100 % de las células, hepatocitos QC incluidos (411/411, 400/400 y 416/416) [P4.por_replica.aneuploide_en.hep].
- **Lectura por la regla del plan: «no separa».** El «estable» de la regla es trivial: todo sale igual.
- Lectura: el clasificador de REFCON no da llamada por célula en este Flex con 5.000 células. Su score de muestra queda en el borde superior del hueco que los autores dejan entre su techo diploide (0,127) y su suelo aneuploide (0,143) de validación (según el comentario del código de REFCON; no cotejado con el artículo). **No lo sé** si con las 19.047 células encontraría ≥ 2 diploides de referencia; la clasificación completa no cabe en memoria.

### P5 · Qué son las 628 llamadas con ≥ 2 marcadores hepáticos

| Grupo | n | UMI (mediana) | UMI de HEP5 (mediana) | Conserva ≥ 2 HEP5 tras CellBender | Testigos tumorales (CellBender) | Dobletes con dbr.sd = 1 |
|---|---|---|---|---|---|---|
| **628 llamadas con HEP5** (186 epiteliales, 442 sin tipo) | 628 | 2.444 | 3 | **0,6 % (4)** | **76,4 %** | 32,3 % |
| 184 no llamadas con HEP5 | 184 | 1.527,5 | 3 | 1,6 % | 52,7 % | 5,4 % |
| Llamadas del pool | 8.756 | 2.083 | 0 | 0 % | 70,2 % | 17,8 % |
| Hepatocitos QC | 1.576 | 2.871,5 | 49 | 98,9 % | 9,5 % | 10,6 % |

Fuente: [P5.estandar.llamadas_HEP5 / no_llamadas_HEP5 / llamadas_pool; P5.hepatocitos_QC].

- Se reproducen 628 y el denominador de 812 [P5.estandar.reproduce, denominador_812]. En el estricto salen 282: **no reproduce el 279 del red-team** [P5.estricto.reproduce: IDENTICO = false, esperado 279, obtenido 282]. Explicación, **cálculo mío** sobre `higado_celula.json` (vias.estricto.base.metricas): fuera de referencia y fuera del pool hay 3.533 − 3.246 = 287 llamadas; sin las 282 quedan 5, que son hepatocitos llamados fuera de referencia; de los 8 hepatocitos llamados, los otros 3 están en la referencia, cuya única zona hepática es la pericentral. El red-team restó los 8. Cuadra con las 5 FP de periportales del estricto (5/426).
- **Lectura por las reglas del plan: «su HEP5 es mayoritariamente ambiente»; la regla de multipletes no se cumple** (UMI 1,17 veces la de las llamadas del pool, por debajo de 1,5) [P5.LECTURA_NO_DECIDE].
- Lectura: son células como las del pool, con 2-3 UMI de ARN hepático que CellBender quita y con testigos tumorales en 3 de cada 4. Llevan más dobletes que las llamadas del pool con cualquier configuración (32,3 % frente a 17,8 % con dbr.sd = 1). Que entren en el denominador de las dianas no mete células que se comporten como hepatocitos (lectura mía); mete algo más de dobletes. El efecto sobre las tasas lo midió el red-team (≤ 0,6 puntos; no re-verificado aquí).

### Qué cambia en la lectura (no en el veredicto)

1. **C3 (punto 4 del red-team).** Se añade a «no se demuestra ni se descarta semejanza con el clon del hueso»: **C3 reconoce un perfil idéntico al del hueso (r 0,88; control optimista: misma muestra, elegidas por consenso de inferCNV y usadas para fijar el filtro de linaje); las llamadas dan 0,19-0,21; el 0,3 sigue sin nulo.** No prueba potencia para un clon hermano en otro tejido o estado, y no dice si son otro clon, el mismo clon en otro estado o no tumor. Sigue retirado «no depende de ningún umbral».
2. **C2 (punto 2).** Se mantiene que C2 mide ruido nominal, y se añade quién cruza el umbral: los controles más profundos, con ARN tumoral o marca de doblete, sin que con n = 5-8 se pueda separar una cosa de otra. Estratificar H1/H2 por tipo no lo arregla (8/20 frente a 10/20). La etiqueta por semilla es FRÁGIL o NO RESUELTO, nunca RESUELTO, ahora con C1 por semilla verificado [fuente: P2.barridos y CSV].
3. **Dobletes (punto 9).** Confirmado que el 9,0 % es cota inferior en el pool. La lectura del residuo («una parte») depende de la configuración: con la de los autores para Flex, «explican»; sin clusters, «no explican».
4. **REFCON (punto 5).** «classify.py, no ejecutado» pasa a «ejecutado en 3 submuestras de 5.000: cae a su rama de muestra pura y no da llamada por célula».
5. **Las 628 (punto 8).** Su HEP5 es mayoritariamente ambiente según CellBender (0,6 % lo conserva); no se comportan como hepatocitos (mediana de 3 UMI de HEP5 frente a 49).
6. **Ruta a NED:** nada nuevo cae ni se levanta. Las dianas, el WES y la IHQ quedan como estaban. Lectura mía: P1 refuerza que por expresión no se encuentra en el hígado el perfil de brazos del clon del hueso; separar «otro clon» de «no tumor» necesita ADN (el WES del hígado, que ya está en el plan de {{CONTACTO}}).

### Reproducibilidad de los post hoc 2

- Commits en `worktree-agent-a91f10f6313ccc273`: plan 155aafd6 · código, procesador, fichas y test 5d77aaad (el código de esta ejecución; el de la fase 2 canónica no se tocó).
- Pasos: `p3_dobletes` (R, una vez; comprueba que los binarios del paso canónico son la matriz cruda del cierre) · `p4_refcon` (hijo con el python de refcon, una vez) · `final` dos veces (`posthoc2/` y `_posthoc2_rep2/`) · `manifiesto`.
- Cachés en `cache/posthoc2/`, con sha en el MANIFIESTO. Ninguna salida lleva barcodes ni índices de fila; las FP van en filas anónimas.
- Test `tests/test_flex_higado_celula.py` ampliado (estratificado sintético, R sintético dos veces idéntico, muro del procesador y de su hijo, plan, MANIFIESTO_POSTHOC2): pasa entero.
