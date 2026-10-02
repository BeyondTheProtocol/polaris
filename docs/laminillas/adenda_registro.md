# Adenda de registro nº 1 · laminillas DFCI (re-especificación declarada; tope 3)

**Estado:** BORRADOR SELLABLE, 2-oct-2026. **Manda el JSON gemelo** (`docs/laminillas/adenda_registro.json`), que lleva el sha256 de este MD. Un test exige la paridad (§7, paso 3).
**Sello base:** congelación (0) recongelada.
- Prefijo verificado: `d014ff7df6e4016e` (2-oct, con `lector_clinico.py procesa laminillas_congela -- verifica`).
- `verifica` solo imprime 16 caracteres; los 64 los rellena el implementador al sellar (§6).

**Superpone solo:** `modulo_b.registro`. Quedan intactos:
- T, vectores, residuo y reglas de artefacto;
- máscara CK19, hotspot, regla de L y métricas.

Lo que cambia aguas abajo, en §6.9.
**Clasificación:** desviación declarada por error de especificación, por validez del modelo, de la topología y del instrumento. La autorizó la patrocinadora ({{TITULAR}}, actualización 20 del plan) **después** de que el orquestador viera el % de Ki67 sin registro. No es error de ejecución ni recongelación.
**Libro de adendas:** `SESION/registro_adendas.jsonl` (solo se añade, encadenado por hash); allí se comprueba la regla «cuarta adenda → rechazo». Esta es la nº 1 de un máximo de 3 (§8). Métodos dice «re-specified k time(s)» con la k del libro.
**Sala limpia:** el autor no ha visto el % de Ki67 de la escalera. Sí vio, porque el plan se lo encargó, el reconocimiento de DAB por píxel de 1-oct (con la fila de P-KI67) y agregados del denominador CK19 y de P-HER2. Ninguna decisión de esta adenda sale de ahí (§8.2).

## TL;DR
1. **Causa:** tres errores de especificación.
   - Un modelo euclídeo con escala 1 que la física de los cortes no garantiza.
   - Una topología que registra cada lámina directamente contra P-CK19, cuando la textura a escala celular solo se conserva entre cortes cercanos. El control HER2NEG↔HER2 da 113 inliers y 91/93 ventanas válidas con p90 de 5 µm; KI67, HER2NEG y HER2 contra CK19, ≤ 6 inliers y 0-9 ventanas válidas.
   - Un instrumento de precisión (correlación de textura nuclear a 1 µm/px, «≥15 picos») que solo mide entre cortes adyacentes. Entre cortes lejanos ni la transformada de VALIS lo pasa.
2. **Topología nueva:** registro **encadenado por vecinos**. El orden de las IHQ se estima solo con geometría (persistencia de la textura). Cada par adyacente se registra y las transformadas se componen hasta P-CK19, con la incertidumbre propagada por salto.
3. **Modelo por salto:** por pieza de tejido, similitud → afín → afín + B-spline (elastix), con cotas físicas. Después, la similitud que VALIS ya devolvió. VALIS en modo serie (vecinos) queda solo como sensibilidad.
4. **Rasgos:** DISK + LightGlue (pesos ya sellados) sobre hematoxilina suavizada, a 8 µm/px para emparejar y 4 µm/px para afinar; RANSAC de scikit-image con semilla.
5. **Instrumento:** validación cruzada espacial con franja de exclusión (bloques de 600 µm, franja de 100 µm, 5 orígenes). La puerta usa una **cota superior del p90** del error por punto de la transformada final, al **97,5 % nominal por ruta**, sumada por salto a lo largo del camino con su amplificación.
   - La elección de peldaño dentro de cada ruta no se corrige por multiplicidad.
   - La cobertura del procedimiento entero la mide V12.
   - La arquitectura de densidad nuclear entre los extremos actúa como veto.
6. **Validez:** se fija aquí y se prueba en dos fases, antes de tocar KI67.
   - Desarrollo: HER2NEG↔CK19 y sintéticos S_dev.
   - Confirmación, una vez: HER2↔CK19, HER2NEG↔HER2, sintéticos S_conf (incluida una pila sintética para la cadena), controles negativos y referencia independiente de estructuras.
7. **Criterios del plan intactos en su letra:**
   - p90 < 50 µm, ahora demostrado con su cota superior;
   - cierre ≤ 3° y ≤ 50 µm en el centroide;
   - ≥ 50 % del área;
   - «≥15 picos» pasa a «≥30 puntos en ≥4 celdas, con estadístico de orden».
8. **Escala de arquitectura, solo como último recurso declarado:** si un par lejano solo se acota entre 50 y 200 µm, entra un análisis secundario cerrado: el Spearman regional de (a) a L ∈ {300, 400} µm y el mapa. Relaja la puerta de 50 µm del protocolo, así que choca con el mandato de {{TITULAR}}. Por eso solo entra agotada la escala de vecindad, y nunca sustituye a (ii), (a), (a-bis) ni al denominador CK19. Que sea el máximo físico con cortes lejanos es inferencia (E12).

---

## 1. Causa y evidencia (solo geometría)

### 1.1 Cronología (UTC, 2-oct-2026)

| Hora | Hecho | Fuente |
|---|---|---|
| 07:54-08:36 | primera pasada del piloto: (ii) < 50 % por protocolo (SIFT 0-3 inliers; fase, 1 ventana válida); rige la escalera sin registro | plan, actualización 18 (09:54-10:36 locales) |
| antes de 10:59 | tercera pasada: VALIS corre y la cota `valis_escala_tol` 0,01 lo rechaza antes de la puerta TRE | plan, estado ≈13:05 local |
| 10:59:12 | el orquestador ve el % global de Ki67 de la escalera | notas de integridad |
| 11:14:24 | lo ve un subagente de diagnóstico | notas de integridad |
| ≈11:45 | mandato de {{TITULAR}} (actualización 20) | plan |
| ≈11:53 y 11:58 | diagnósticos de geometría KI67→CK19 y HER2NEG→CK19 | fecha de los logs |
| 12:26 | primer borrador de esta adenda | este documento |
| ≈12:45-13:10 | revisión de dos críticos; diagnóstico numérico completo commiteado (995fb22, con el control HER2NEG↔HER2); esta adenda incorpora las dos cosas | git log; mensajes del orquestador |
| ≈13:20-13:40 | commit 74acc82; segunda revisión del metodólogo (no apto, 6 bloqueantes; 3 cambiaron la estadística); corregidos | git log; mensaje del orquestador |
| ≈13:40-14:00 | commit a760cd9; revisión final del metodólogo (no apto por un bloqueante de redacción, B1); corregido en esta versión | git log; mensaje del orquestador |

### 1.2 Evidencia

**Resultado por protocolo, con la misma prominencia que el resto:** (ii) KI67↔CK19 verificó < 50 % del área de consenso y rigió la escalera sin registro. Se informa junto al resultado re-especificado, nunca en su lugar.

| # | Hecho | Estado |
|---|---|---|
| E1 | El sello fija «euclidea, escala 1; no rigido despues» (`laminillas_congela.REGISTRO`); el plan lo basa en «Escala 1 [inferido]», sin fuente | **verificado** (Read) |
| E2 | Cada corte en parafina cambia de tamaño frente al bloque: de −0,5 % a 10,5 % según tejido y dirección. La DE por corte es de 1,2-2,4 % sin músculo. Entre la dirección de corte y la perpendicular hay hasta 4,2 puntos | **verificado**: resumen de Jones 1994, PMID 8168406, por Europe PMC. Modelo animal (rata); solo resumen; validez externa limitada (no es mama humana) |
| E3 | El no rígido supera a la afín salvo a resoluciones más gruesas de 16 µm/px. En consecutivos HyReCo: afín 20,2 → deformable 5,3 µm de mediana | **verificado** (Lotz et al. 2023, PMC10704256, texto completo) |
| E4 | VALIS 1.2.0 usa similitud por defecto (`registration.py:71`). La cota sellada 0,01, inferencia mía de entonces, rechazó escala 1,0127 y anisotropía 1,0003 (KI67→CK19). En el mismo diagnóstico: HER2NEG→CK19, 1,0173; KI67 en espejo, 1,0077. **Las cotas de §2.4 se eligieron conociendo esas escalas; las apoya Jones 1994 (rata, solo resumen).** | **verificado** (diagnósticos y código de VALIS) |
| E5 | Entre láminas lejanas, el instrumento (b)/(b') no pasa con ninguna de las 7 transformadas probadas, VALIS incluida. KI67→CK19, FC1 (135 ventanas): ≤ 14 válidas en (b) y ≤ 10 en (b'); FC2 ≤ 2/100; FC3 ≤ 2/59. HER2NEG→CK19, FC1: ≤ 7 en (b), ≤ 11 en (b'). Razón pico/segundo mediana 1,05-1,15 (umbral 1,5); pico NCC mediano 0,11-0,14. **Contra sí misma: 135/135, razón 8,9, pico 1,0** | **verificado** (tabla extraída por script, solo campos de geometría) |
| E6 | ACROBAT, frase completa: «The lowest mean TREs across all landmarks of 63.29 µm and 122.21 µm therefore cannot be assumed to allow a cell-level registration, but neighbourhoods of cells can be assumed to be registered correctly. Furthermore, depending on the section spacing, actual cell-level correspondence between the sections is impossible to determine». Lotz: en consecutivos la ganancia «stagnates between 15.5 and 7.8 µm/px», y lo atribuye a «missing correspondences caused by larger slice distances» | **verificado** (`acrobat2022.txt` 412-417; Lotz) |
| E7 | SIFT a ×8 sobre ODsum entre tinciones: 91 putativas (ratio 0,8), 10 a < 100 µm de VALIS. El filtro del eslabón 1 (500 µm, inferencia sellada) deja 2, porque esa inicialización cae a 634-750 µm de VALIS. Por FC: 3 inliers. Sin CLAHE, con hematoxilina o a ×32 tampoco mejora | **verificado** (`diag_ki67.log`) |
| E8 | IoU de tejido ×8 tras la similitud global de VALIS: 0,60 (KI67) y 0,58 (HER2NEG). Los FC1-3 suman 6,64 de 7,00 mm² | **verificado**. Que las piezas se muevan por separado es **inferencia** |
| E9 | Unos 31 sitios asumen euclídea 3×3. Grep en `laminillas_registro.py`: 43 `matriz_um`, 23 `_aplica(`, 18 `linalg.inv(`, 3 `EuclideanTransform`, 12 `angulo(` | la cifra 31 **sin verificar**; el grep **verificado** |
| E10 | El control positivo HER2NEG→HER2 del diagnóstico murió por memoria (19,7 GB) | **sin verificar** |
| E11 | (El implementador no expuesto recoteja estas cifras desde el producto del diagnóstico, salvo 113 inliers y 91/93, ya cotejadas.)<br>**Control HER2NEG→HER2** (láminas casi idénticas).<br>· SIFT v1: 209 putativas, **113 inliers**; por FC, 131/22/47.<br>· (b) v1: **91/93 ventanas válidas (p90 5 µm)**, 33/43 y 44/48.<br>· Pico NCC mediano 0,44; razón pico/segundo 3,4.<br>Frente a P-CK19:<br>· KI67, 91 putativas, 5 coherentes con VALIS (< 50 µm), 6 inliers;<br>· HER2NEG, 86, 3 y 3;<br>· HER2, 112, 1 y 3.<br>(b) con la transformada de VALIS: 0-9 válidas de 59-135; NCC 0,13; razón 1,10-1,13. KI67→HER2NEG también cae (3 inliers) | el mensaje del commit 995fb22 (verificado con `git show`) trae 113 inliers y 91/93 con p90 de 5 µm. El resto lo reenvió el orquestador desde el diagnóstico: **no recotejado** por mí |
| E12 | **La textura a escala celular solo se conserva entre cortes cercanos.** HER2NEG y HER2 están contiguas o casi, y CK19, KI67 y HER2NEG están lejos entre sí en el bloque. El error de especificación es también de **topología**: registrar cada lámina directamente contra P-CK19 | **inferencia** (del agente de diagnóstico y mía) sobre E11 y E6 (Lotz: el error crece con la distancia entre cortes) |
| E13 | La escala de VALIS frente a CK19 da 1,0127 (KI67), 1,0173 (HER2NEG) y 1,0301 (HER2). Como HER2NEG y HER2 son casi idénticas, su escala a 850 px lleva un ruido de al menos ~1 %. El mapa de densidad v1 cae a ~100 µm (p90 104-124 µm) en los FC2-3 del control, donde SIFT y fase dan 8-15 µm. Desde VALIS devuelve **desplazamiento exactamente 0 en 6 de 9 FC** (causa sin investigar: sospecha de error de ejecución, §7) | reenviado por el orquestador, **no recotejado**; lo del ruido es **inferencia** |

No encontré error de ejecución en la cadena SIFT: lo dice el mapa del código y, leyendo el código, no vi ninguno. Es inferencia sobre lectura, sin re-ejecutar.

---

## 2. Modelo de transformación y orden fijo

### 2.0 Topología: registro encadenado por vecinos
Es el estándar de la reconstrucción 3D por cortes seriados y el modo por defecto de VALIS (`align_to_reference=False`).
1. **Orden de los cortes, solo con geometría.**
   - **Láminas:** las 11 de `IHQ_CON_TEJIDO` del sello base (todas menos P-AE1AE3; `laminillas_congela.py:80`, igual que `SERIE_IHQ` de `laminillas_f3.py:71`). P-HE entra solo como dato informativo.
   - Para cada par (i, j): global y emparejado de piezas a 8 µm/px (§3); después DISK+LightGlue a 4 µm/px dentro de las piezas emparejadas.
   - **Proximidad S_ij** = inliers de RANSAC-similitud por mm² de FC: mide cuánta textura fina persiste, que decae con la distancia entre cortes (E11, E12).
   - Seriación exacta (Held-Karp, `orden_cortes`, hasta 15 láminas) maximizando la suma de S entre vecinos.
   - **Estabilidad:** 200 réplicas bootstrap de los emparejamientos (semilla + 12); se informa la frecuencia de cada vecino. Si una adyacencia del camino aparece en < 80 % de las réplicas, U_camino es el máximo sobre los órdenes con frecuencia ≥ 10 %, **y siempre sobre el orden de Held-Karp**, aunque aparezca en menos del 10 %.
   - **Sensibilidad:** el orden con la métrica del plan (densidad de centroides a 8 µm/px, F3, «Orden de los cortes»). El orden de VALIS en modo serie se informa como comparador.
   - Rótulo: «estimated section order (texture persistence); spacing unknown».
2. **Cuándo se calcula el orden.**
   - **Desarrollo:** sin P-KI67 y sin P-P63, la lámina sorteada para la confirmación (§4.4).
   - **Confirmación:** se añade P-P63, con el código ya sellado.
   - **Las 11 juntas:** solo como primer paso de `registro-par P-KI67`, con el código ya sellado en `anexo_codigo`. Se informa si cambia el orden relativo de las demás.

   Así ningún código se cambia después de ver un S_ij de P-KI67.
3. **Segmentación de las intermedias.** Las piezas y la medida I-b necesitan centroides InstanSeg de todas las láminas de la cadena. Se segmentan con la configuración sellada (es el paso 1 de F3); **no se lee ninguna positividad**.
4. **Camino.** Toda T_{A→B} se compone por el **camino mínimo A…B** del orden, sin rodeo por P-CK19. Test: a ±2 µm frente a la composición explícita. Cada salto adyacente se registra con la escalera de §2.2.
5. **Marco de coordenadas: P-CK19**, como ya está sellado: `REGLA_L` con `rejilla_anclada="P-CK19"` (`laminillas_congela.py:156`), y además el denominador de toda diana es CK19. El error entre A y B depende del camino, no del marco. Un marco central toca `regla_L`, que está fuera de la lista blanca: aquí no se hace.
6. **Dos rutas, cada una con su propio presupuesto de error.**
   - Se evalúan la cadena y el par A↔B directo.
   - Cada candidato se acota al **97,5 % nominal**; en la cadena, cada salto a 1 − 0,025/m. Ese Bonferroni **solo cubre la elección entre las dos rutas**.
   - **Dentro de cada ruta, la elección de peldaño no se corrige por multiplicidad.** Gana el primero que pasa entre R1, R2 y R3 (más R4 en el directo), más el cambio por IC pareado. En el peor caso, la tasa de falso pase sube hasta K × 2,5 % (K = 3, o 4 en el directo).
   - **La cobertura del procedimiento entero la mide V12** (límite inferior de Clopper-Pearson ≥ 0,90). No se afirma una cobertura conjunta del 95 %.
   - Alternativa considerada y no adoptada: nivel 1 − 0,025/(K·m) por candidato. Exige n mínimo de 46 / 107 / 174 (K = 3; m = 1 / 2 / 3) y 49 en el directo con R4. Deja más FC sin certificar, y V12 ya mide la cobertura real.
   - Rige la cadena si pasa; si no, el directo si pasa.
   - **R4** (la similitud guardada de VALIS, que es KI67→CK19) solo entra en la ruta directa P-KI67↔P-CK19.
7. **Intermedias y eje NE** (prioridad de {{TITULAR}}).
   - **Riesgo:** si el DAB de una diana intermedia tapa la hematoxilina (nuclear o citoplásmico, según el marcador), sus zonas positivas pierden landmarks. Lo verificado se sesgaría hacia sus zonas negativas, y el riesgo es el mismo para una diana NE que para una luminal. No se usa ninguna medida de intensidad para decidir a cuáles afecta.
   - **No se excluyen intermedias por regla:** excluir solo las NE rompería la simetría luminal/NE que pide {{TITULAR}}, y excluir por intensidad de DAB usaría datos de diana.
   - **Detector a ciegas, solo para el tribunal** (tras congelar el registro y calculado por un proceso no expuesto):
     - Ki67 en los FC verificados por la cadena frente a los verificados por el directo;
     - la positividad de cada diana intermedia dentro y fuera del área verificada.
   - **Sensibilidad, definida por mecanismo:** una cadena que solo admite como intermedias las láminas de suelo del sello (`SUELO` = P-HER2NEG y P-HER2, sin DAB de diana por diseño del protocolo) y salta toda diana con DAB, nuclear o citoplásmica. Se informa y nunca se elige. Así no hay que escoger un subconjunto (ni el luminal ni el NE) ni mirar intensidades.

### 2.1 Unidades: pieza y FC
- **Máscara.** Las piezas y los FC se sacan de la máscara provisional sellada, que no depende de la tinción (raster de centroides InstanSeg a 8 µm/px, σ 16 µm, umbral fijo de 250 núcleos/mm²).
  - Se recalcula desde los centroides de segmentación, nunca desde `objetos_*.npz`.
  - La máscara sobre ODsum (lleva DAB) queda solo como sensibilidad.
- **Pieza.** Componente de esa máscara, en **las dos láminas**, ≥ 0,02 mm².
  - Las piezas unidas por puentes de < 100 µm se separan con una apertura de radio 50 µm y partición geodésica: cada píxel de la máscara original va a la semilla abierta más cercana dentro de la máscara. Así la forma se conserva.
- **Emparejado.** Cada pieza móvil se empareja contra la unión del tejido fijo (§3).
- **FC.** Componente de (pieza_ref ∩ T_emparejado(pieza_móvil)) ≥ 0,02 mm², erosionado 50 µm para medir: los bordes del cilindro no se corresponden entre cortes. En F3, el consenso ≥ 6 de 11 se hace sobre estas máscaras.
- **Quiralidad: una por pareja.** Cada pieza se comprueba en la otra quiralidad. Si allí da ≥ 2× inliers, «piece chirality conflict» y la pieza queda fuera.

### 2.2 Escalera por FC de cada salto (y del par directo), orden fijo

| Peldaño | Modelo | Herramienta | Ajusta con | Papel |
|---|---|---|---|---|
| R1 | similitud (θ, s, tx, ty) | `skimage.measure.ransac(SimilarityTransform)` | landmarks del pliegue | candidato |
| R2 | afín (6 gdl) | `skimage.measure.ransac(AffineTransform)` | ídem | candidato |
| R3 | lineal + B-spline | itk-elastix 0.25.4 | NCC de hematoxilina suavizada en el pliegue, landmarks del pliegue y energía de flexión | candidato. Parte de R2 si está bien condicionada (razón de momentos de los landmarks ≤ 4) y cumple cotas; si no, de R1; si ninguna cumple, no se intenta |
| R4 | la similitud que VALIS ya devolvió, sin reajuste ni proyección | producto de la vuelta gastada (§5) | nada | solo en la ruta directa P-KI67↔P-CK19 y solo si ningún propio pasa en el FC |
| R5 | VALIS **en modo serie** (`align_to_reference=False`, vecinos), rígido y no rígido por defecto (OpticalFlowWarper), sin micro, sobre las IHQ de la cadena | VALIS 1.2.0, una corrida | toda la imagen | **solo sensibilidad**: no tiene medida independiente y nunca verifica un FC del análisis principal |

ANTs SyN (antes R3b) **se retira**: su semilla no se aplica y su convenio de ejes es arriesgado (§10). El segundo motor no rígido es R5, como sensibilidad.

### 2.3 Regla de elección
- Gana **el primero, en orden R1 → R2 → R3, que pasa la puerta** (§4.5).
- Se cambia a un peldaño posterior que también pase **solo si el IC 95 % pareado de p90(R_posterior) − p90(R_actual) queda entero por debajo de −5 µm**. Ese IC es un bootstrap por celdas, sobre las mismas celdas, y exige ≥ 10 celdas; con menos, no se cambia.
- Si ningún propio pasa: R4.
- R1-R3 se calculan siempre y se informan todos. Del ganador se informa la cota superior.
- Por qué así: respeta «el primero que pasa» y solo lo deja atrás cuando la ganancia en precisión es real (> 5 µm, ≈ 1 px a 4 µm/px). Esa precisión importa: el TRE fija la erosión de CK19 y L (§6.9).

### 2.4 Cotas (un peldaño que las incumple no pasa; nunca se recortan)
Parte lineal A = U·Σ·Vᵀ; s = √det A; anisotropía = σ₁/σ₂.

| Magnitud | Cota | Justificación | Estado |
|---|---|---|---|
| Escala isotrópica | 0,90 ≤ s ≤ 1,10 | DE por corte ≤ 2,4 % → la DE de la diferencia entre dos cortes es ≈ 3,4 %; 3 DE ≈ 10 %. VALIS solo exige 1/3-3. Elegida conociendo E4 | inferencia (rata, resumen) |
| Anisotropía σ₁/σ₂ | ≤ 1,10 **si** la razón de momentos de los landmarks es ≤ 4; si no, R2 solo pasa con max \|A·x − S·x\| ≤ 25 µm sobre el FC frente a R1 | Jones: hasta 4,2 puntos entre direcciones. En un FC delgado la escala transversal no se puede estimar. Se informan las escalas transversal y longitudinal por separado | inferencia |
| Cizalla (cambio del ángulo recto) | ≤ 5,46° | se deduce de σ₁/σ₂ ≤ 1,10 | derivada |
| Quiralidad | det A > 0 en todo peldaño | el espejo solo entra por la bandera de pareja | regla |
| Giro por pieza | sin cota (búsqueda de 360°) | **no lo sé**: no encontré una fuente que lo acote | — |
| Deformación local del no rígido, respecto a su lineal | estiramientos principales de J(Id+u) en [0,90; 1,10] y giro local ≤ 5° en ≥ 99 % de los píxeles del FC erosionado | misma física que la escala; un cilindro largo puede curvarse (por eso no se pone tope absoluto de desplazamiento) | inferencia |
| Pliegues del campo | det J > 0 en el 100 % del FC | más estricto que RegWSI (< 0,1 %); el FC ya está erosionado | regla |
| Conservación de densidad | ∫det J en cada celda de 600 µm con ≥ 90 % de tejido, ±20 % frente a la lineal; área del FC ±3 % | el no rígido no puede crear ni quitar núcleos comprimiendo | inferencia |
| Contención en campo común | ≥ 0,95 | la sellada (v1) | inferencia sellada |
| Inversa | T = A∘(Id+u): A se invierte de forma analítica y solo Id+u de forma iterativa (`itk.IterativeInverseDisplacementFieldImageFilter`); \|T(T⁻¹x) − x\| p99 ≤ 2 µm | sin una inversa fiable no se pueden llevar núcleos | regla |

La comprobación de densidad de la lista post-congelación (punto (3), < 15 %) no cambia; ahora usa la transformada v2.

---

## 3. Emparejado y preprocesado (fijo; lo único que se ajusta va en §4.4, en desarrollo)

| Paso | Elección | Por qué |
|---|---|---|
| Lectura | ×8 nativo (2,0044 µm/px, medido) por el lector único; relleno pintado del vidrio local | sin cambios |
| Canal | hematoxilina OD con los vectores de tanda del sello (DAB propio si lo lleva), recortada a [0; 1,0]. **Reduce, no excluye, la dependencia del DAB**: hay diafonía DAB→H (V11) | única tinción común a todas; la deconvolución mejoró la afín IHQ-IHQ de 161,5 a 122,4 µm (StainBridge) |
| Resoluciones | media por bloques: 8,0176 µm/px (f = 4) para global y emparejado; 4,0088 µm/px (f = 2) para afinar por FC, con la hematoxilina suavizada (gaussiana σ = 8 µm) | Lotz: la ganancia se satura hacia 8 µm/px y el mejor resultado consecutivo fue a 3,9 µm/px; el suavizado quita el detalle nuclear, que no se corresponde entre cortes |
| CLAHE | 128 µm, clip 0,01 (los sellados), solo para DISK | como RegWSI |
| Zonas sin rasgos | a < 50 µm del borde de tejido o del de escaneo; a < 20 px del relleno de un recorte girado | bordes sin correspondencia; artefacto del giro |
| Rasgos | `kornia.feature.DISK`, pesos sellados en F1.0 (sha256 `9c2ee4de…`), n = 4096, NMS 5, umbral 0, CPU, `torch.use_deterministic_algorithms(True)` | los pesos que VALIS ya usa en la jaula |
| Emparejador | `kornia.feature.LightGlueMatcher('disk')`, pesos sellados (sha256 `b5b21d47…`), umbral 0,1. **Antes de cada corrida se comprueba el sha256 del fichero en caché** (kornia lo baja con `torch.hub` sin hash) | ídem |
| Estimador | RANSAC de scikit-image (`SimilarityTransform` y `AffineTransform`), residuo 25 µm en px de cada resolución, 10.000 intentos, `rng` = semilla + 4 | `cv2.estimateAffinePartial2D` no acepta USAC_MAGSAC y `cv2.setRNGSeed` no siembra USAC (crítico de registro; no recotejado por mí). Una sola librería, determinista y ya probada en v1 |
| Global | lámina entera a 8 µm/px; 24 giros (15°) × 2 quiralidades. Gana la de más inliers. La quiralidad se acepta con ≥ 30 inliers y ≥ 2× la otra; si no, decide el eslabón 1 (IoU de máscaras con margen ≥ 0,10, regla sellada) | búsqueda exhaustiva como RegWSI e HistoKat; DISK no es invariante al giro (inferencia) |
| Escala en la búsqueda | fija en 1; el ajuste la estima dentro de las cotas | mpp igual verificado |
| Emparejado de piezas | cada pieza móvil contra la unión del tejido fijo, en un recorte de ±2 mm alrededor de donde la pone la global (o todo el tejido fijo, si no hay global o no hay candidata). 24 giros a 8 µm/px; similitud. Acepta con ≥ 15 inliers y ≥ 2× la mejor pose alternativa (a > 30° o a > 300 µm). Si no, «fragment pairing ambiguous» y la pieza hereda la global | ninguna herramienta modela fragmentos (notas); StainBridge lo declara como límite |
| Filtro de radio | se elimina `init_radio_um` | E7 |
| Elastix (R3) | `itk-elastix` 0.25.4. Spacing **en mm** (0,004 mm en el nivel base); pirámide `4 4 2 2 1 1` sobre 4 µm/px (16 → 8 → 4); B-spline con rejilla final de 0,25 mm. `MultiMetricMultiResolutionRegistration` con tres métricas: `AdvancedNormalizedCorrelation` (peso 1), `CorrespondingPointsEuclideanDistanceMetric` (peso w_p) y `TransformBendingEnergyPenalty` (peso w_b). (w_p, w_b) ∈ {0,1; 1; 10}², **calibrados solo en desarrollo** (§4.4). Optimizador `AdaptiveStochasticGradientDescent`, `ImageSampler Full`, 300 iteraciones por nivel, un hilo. Máscara fija = FC ∩ bloques de ajuste, sin la franja | nombres de parámetro cotejados en los cuadernos de ITKElastix (ejemplos 09, 11 y 22 y el de puntos en memoria). Pesos en escala de mm, para que la regularización pese (crítico) |
| Convenio | elastix da fija→móvil; un test de verdad conocida fija el sentido; se guardan directa e inversa | E9 |
| Semillas | maestra 20261002, derivadas por suma fija: global +1, piezas +2, RANSAC +4, señuelos +6, nulo +7, bootstrap +8, aclarado de puntos +9, S_dev +10, S_conf +11, bootstrap del orden +12, pilas de V12 +13…+32, sorteo de la lámina de confirmación +33 (salió P-P63), **orígenes de la validación cruzada +34+i (i = 0…4: +34…+38)**. +3 y +5 quedan libres | se sellan antes; el cargador rechaza una adenda posterior que cambie la maestra; **un test exige que todas las derivadas sean distintas** (antes, los orígenes +30…+34 pisaban las pilas y el sorteo) |

---

## 4. El instrumento de medida v2

### 4.1 Separación entre ajuste y medida
**Validación cruzada espacial con franja de exclusión.**
- **Tablero:** bloques de 600 µm, paridad A/B, **5 orígenes** desplazados por semilla (+34…+38; uniformes en [0; 600)² µm). 2 pliegues por origen.
- **Ajuste:** usa los bloques de su paridad **menos una franja de 100 µm** junto a los bloques de medida (landmarks, máscara de elastix).
- **Medida:** solo puntos a ≥ 100 µm dentro de un bloque de medida, en un núcleo de 400×400 µm.
- **Transformada final:** se reajusta con todo, con los mismos parámetros, y tiene que cumplir las cotas, igual que los 10 ajustes de pliegue.
- **Error por punto de la transformada final** (cota por desigualdad triangular): e_final(p) ≤ e_cv(p) + |T_final(p) − T_pliegue(¬p)(p)|. La puerta usa e_final, no e_cv.
- Si un punto se mide en varios orígenes, cuenta su máximo.

**Fuga prohibida y con test:** ningún punto ni píxel a menos de 100 µm de un bloque de medida entra en el ajuste que se mide.

### 4.2 Medidas

| Id | Qué | Cómo | Papel |
|---|---|---|---|
| **I-a** | landmarks retenidos | emparejamientos DISK+LightGlue del pliegue de medida, confirmados solo con datos de ese pliegue: dentro de cada bloque, similitud RANSAC sobre sus emparejamientos (≥ 4 inliers, residuo ≤ 25 µm). Aclarado: como mucho uno por celda de 50×50 µm (elección con semilla), para bajar la autocorrelación | **certifica** |
| **I-b** | arquitectura | densidad de centroides InstanSeg (todos los núcleos, detectados sobre RGB, sin positividad), 8 µm/px, σ 16 µm. La móvil se hace **llevando centroides** con la transformada que se mide (la del pliegue y la final), no remuestreando. Ventanas 100 % dentro del FC erosionado; NCC con pico subpíxel. Válida si: pico ≥ τ (q99 del nulo estratificado por contenido de borde, §4.4), razón de curvaturas del pico ≤ 3 y eje mayor a 1σ ≤ 16 µm. **Un pico en el borde de la búsqueda con NCC ≥ τ cuenta como el radio de búsqueda** (censura). Suficiente con ≥ 50 % de ventanas candidatas válidas (contando las censuradas) y el mínimo de ventanas válidas sin solape de su escala. **Dos configuraciones, en un único campo `veto_ib_por_escala`:**<br>· **vecindad**: ventana 384 µm (en el núcleo de 400 µm del bloque cuando va dentro de la VC), búsqueda ±128 µm, censura en 128 µm, umbral de veto 50 µm, ≥ 4 ventanas válidas sin solape; por salto y en los extremos;<br>· **arquitectura**: ventana 768 µm, búsqueda ±400 µm, censura en 400 µm, umbral de veto 200 µm, ≥ 2 ventanas válidas sin solape (inferencia: en cilindros de ~1 mm no caben más), τ propio con el nulo a ≥ 1,5 mm; solo en los extremos.<br>· **Estadístico del veto en las dos escalas:** el p90 con censura si hay ≥ 10 ventanas válidas; con menos, **el máximo** de los desplazamientos.<br>· Se informa **qué fracción de los FC de arquitectura queda sin veto** porque no caben 2 ventanas | **veta**, no certifica |
| **I-c** | deformaciones conocidas | sintéticos (V1, V3b, V11) | validación |
| **I-d** | cierre | **puerta del plan en el centroide del FC**: ≤ 3° y ≤ 50 µm a escala de vecindad; ≤ 3° y ≤ la cota del par a escala de arquitectura. **Solo es puerta entre transformadas estimadas de forma independiente** (cadena frente a directo, o pares sin saltos en común). Entre composiciones que comparten saltos es tautológico: no es puerta y se declara. Rejilla cada 100 µm, solo informativa, con referencia √(Σ p90²) | puerta (centroide) |
| **I-e** | comprobación ciega con señuelos | recortes N1 de hematoxilina en gris (sin DAB), ≤ 1024 px, damero de 128 µm. Un candidato verdadero y señuelos escalados a p90 añadido de 25 y 50 µm; dos subagentes a ciegas | **informativa** (V8); nunca puerta |

### 4.3 Qué rige y qué sustituye a «≥ 15 picos»
- **TRE acotado (U):** cota superior del p90 de e_final al **97,5 % nominal por candidato y ruta** (§2.0.6). En la cadena es U_camino y cada salto se acota a 1 − 0,025/m. La elección de peldaño no se corrige por multiplicidad; la cobertura real del procedimiento la mide V12. Hay dos estimadores; manda el mayor.
  - **Estadístico de orden:** el k-ésimo menor, con k el mínimo tal que P(Bin(n; q) ≤ k − 1) ≥ nivel, con q = 1 − 0,10/m. Con m = 1 al 97,5 %: n = 36 da el máximo. Como referencia al 95 %: n = 30 da el máximo, n = 50 el 49.º y n = 100 el 96.º.
  - **Bootstrap por celdas** (2.000 réplicas, semilla), solo con ≥ 10 celdas.
- **I-a suficiente:** n ≥ n_min(m) puntos aclarados, en ≥ 4 celdas de una rejilla fija de 600 µm.
  - Fórmula: **n_min(m) = ⌈ln(0,025/m) / ln(1 − 0,10/m)⌉**: con ese n, el máximo de la muestra acota el cuantil 1 − 0,10/m al nivel 1 − 0,025/m.
  - Tabla: m = 1 → 36; 2 → 86; 3 → 142; 4 → 201; 5 → 263; 6 → 327; 7 → 392; 8 → 459; 9 → 527; 10 → 597.
  - Con 11 láminas, m puede llegar a 10.
- **Por qué:** con un p90 puntual, n = 30 y 4 bloques, la puerta pasaría la mitad de las veces aunque el p90 real fuera 50 µm. Con la cota, «p90 < 50 µm» queda acotado a nivel nominal y no solo estimado; la cobertura real, con la elección de peldaño incluida, la mide V12. Dentro de una celda los errores están correlacionados: por eso el aclarado, y por eso el bootstrap por celdas cuando hay ≥ 10. Los 15 de v1 contaban picos de un instrumento que no sirve (E5). **Es estadística; no se miró ningún resultado de diana.**
- **Fragmento pequeño** (I-a insuficiente), regla del plan con puntos: todos sus e_final < 50 µm, con ≥ 5 puntos → «small fragment: registration verified on n points». Si no, «small fragment: registration not independently verified». Va rotulado.
- **Propagación por el camino** (m saltos, T = T_m ∘ … ∘ T_1):
  - el error del salto i se propaga por los saltos siguientes y se amplifica con su constante de Lipschitz;
  - con la cota de la unión, el p90 del camino queda acotado por la suma ponderada de los cuantiles p(1 − 0,10/m) de cada salto;
  - la cadena entera se certifica al 97,5 % (§2.0.6): cada salto al nivel 1 − 0,025/m.

  **U_camino = Σᵢ Λᵢ · Uᵢ**, con:
  - **Λᵢ = Π_{j>i} max_FC ‖J_{T_j}‖₂**, medido sobre la transformada estimada (Λ_m = 1);
  - **Uᵢ = U_{1−0,025/m}(p_{1−0,10/m})** del salto i, calculado **solo con los landmarks que caen en la imagen del FC del par A–B en el marco del salto i**.

  Sin Λ la cota se quedaría corta: con estiramientos de hasta 1,10, un 5 % con m = 2, un 10,3 % con m = 3 y un 16 % con m = 4.
  Con m = 1 es el U del p90 directo, al 97,5 % (ruta directa).
  Suficiencia por salto: n_min(m) de la tabla de arriba (con m = 3, ≥ 142 puntos por salto, porque 1 − (1 − 0,10/3)ⁿ ≥ 1 − 0,025/3).
  Es conservadora (inferencia: los errores de salto no se compensan en el peor caso). Se dice así.
- **Veto en los extremos:** I-b entre A y B directamente, con la transformada compuesta (modalidad independiente, sin ajuste sobre los extremos), con la configuración de la escala del único campo `veto_ib_por_escala` (§4.2). Si es suficiente y su p90 con censura llega al umbral de la escala, el FC no pasa. Así se caza la deriva de la composición.
- **Distancia a los landmarks** (condición de bloques 600/100):
  - se mide la distancia de cada núcleo del FC al punto de I-a más cercano;
  - si más del 10 % de los núcleos quedan a > 300 µm, la zona a más de 300 µm de cualquier punto no cuenta como verificada;
  - es validación cruzada con estructura espacial (Roberts et al. 2017, doi 10.1111/ecog.02881; Milà et al. 2022, doi 10.1111/2041-210X.13851; solo resumen, abierto en Crossref).
- **Valor exportado aguas abajo** (`tre_p90_um`) = **U_camino** (o la U del directo, si rige el directo). L y la erosión de CK19 lo consumen sin cambiar su código (§6.9).

### 4.4 Validez del instrumento, fijada antes de KI67
**Fase de desarrollo** (aquí se ajusta y se arregla código):
- **Datos:** HER2NEG↔CK19 y **S_dev**, el ×8 de P-HER2NEG con D1-D10 y semilla + 10.
- **Previo:** segmentación, con la configuración sellada y sin positividad, de las IHQ que falten.
- **Salidas, selladas en `anexo_desarrollo`:**
  - τ de I-b en las dos configuraciones: q99 del pico de ≥ 200 ventanas nulas (zona no correspondiente a ≥ 1 mm en vecindad y a ≥ 1,5 mm en arquitectura, en el mismo FC o en otro), estratificado por contenido de borde;
  - (w_p, w_b) de elastix: el par con menor U en validación cruzada; si empatan, el de mayor w_b;
  - **el orden de la cadena SIN P-KI67 y SIN P-P63** (§2.0.2), con su estabilidad;
  - E11 (ya medido en 995fb22; el anexo lo recoge con sus hashes).

**Fase de confirmación** (una corrida por adenda; `valido` se decide **solo** aquí):
- **Pares ya mirados en el diagnóstico de 995fb22**, y así se declara: HER2↔CK19 y HER2NEG↔HER2 (V6).
- **Par no inspeccionado:** **P-P63↔P-CK19**. P-P63 se sorteó entre {P-CHGA, P-{{DIANA3}}, P-P63, P-RA, P-RE, P-RP, P-SYN} (orden alfabético) con `numpy.random.default_rng(20261002 + 33).integers(0, 7)`. El sorteo se hizo el 2-oct con numpy 2.4.6 y la semilla +33 en lugar de +13, porque +13…+32 son las pilas de V12. **El sorteo con +13 no se ejecutó nunca.** Hubo dos intentos, los dos con +33: el primero falló al importar numpy (python del sistema) sin dar resultado, y el segundo dio P-P63. No hay bifurcación que declarar en el control de validez. P-P63 queda fuera del orden de desarrollo; entra en la confirmación con el código ya sellado.
- **Sintéticos S_conf:** el ×8 de P-CK19 y P-HER2 con D1-D10 y semilla + 11, y las pilas de V12.
- **Controles negativos.**
- **Ningún salto de las dos fases usa imágenes de P-KI67.** Si P-KI67 cae entre dos láminas de un camino de validación, el camino la salta (salto más largo entre sus vecinas). Se declara.

| Id | Criterio (confirmación) | Umbral |
|---|---|---|
| V1 | Recuperación en S_conf.<br>D1: s 0,95, θ +7°, t (150; −80) µm · D2: s 1,05, θ −7° · D3: anisotropía 1,06 a 30° · D4: cizalla 4°<br>D5: D2 + campo B-spline aleatorio (control cada 500 µm, máx. 40 µm) · D6: campo de máx. 80 µm · D7: D5 + gamma 0,8 y vector H girado 5° · D8: D5 + desgarro (banda de 150 µm)<br>D9: s 1,20 · D10: campo de máx. 200 µm con estiramientos fuera de cota | D1-D8: error frente a la verdad en rejilla de 100 µm, p90 ≤ 10 µm y máximo ≤ 30 µm; U de I-a a ±10 µm del p90 verdadero en ≥ 90 %. D9-D10: ningún peldaño pasa |
| V3a | Señuelos después de ajustar, **por escala**: a la transformada aceptada de cada FC de confirmación se le suma un señuelo de cuatro tipos (traslación, giro, escala, campo suave), escalado a un p90 añadido dado, y se juzga con la puerta de su escala. **Dos variantes:**<br>· **V3a-c**: el señuelo se suma a la transformada compuesta del par; prueba el veto de los extremos y el cierre;<br>· **V3a-s**: se suma a un solo salto (cada salto por turno); prueba la U.<br>Los umbrales valen para las dos | **Vecindad:** 150 y 300 µm no pasan por ninguna vía; 60 µm detectado en ≥ 95 %.<br>**Arquitectura:** 250 y 400 µm no pasan por ninguna vía; 150 µm detectado como ≥ 100 µm en ≥ 95 % |
| V3b | Señuelo antes de ajustar (equivarianza): se aplica una d conocida (similitud + campo, p90 60 µm) a la móvil de cada par de confirmación y se corre el método entero | transformada recuperada frente a la esperada (T∘d⁻¹): p90 ≤ 10 µm; la U cambia ≤ 10 µm |
| V5 | Cobertura | en cada par de confirmación (P-P63↔P-CK19 incluido), los FC con I-a suficiente suman ≥ 50 % del área de FC |
| V6 | Control positivo del plan: HER2NEG↔HER2 (mirado en 995fb22; se declara) | área verificada ≥ 50 % con U < 50 µm |
| V7 | Determinismo: HER2↔CK19 corrido dos veces | transformadas iguales a ±0,1 µm en rejilla; U idéntica |
| V9 | **Referencia independiente.** Centroides de estructuras mucho mayores que la distancia entre cortes (huecos de la máscara ≥ 50 µm: vacuolas adiposas, luces, vasos), emparejados con el algoritmo húngaro, sin landmarks manuales. Compuerta de 100 µm a escala de vecindad y de 300 µm a escala de arquitectura | en ≥ 90 % de los FC de confirmación con ≥ 20 pares: p90_instrumento ≥ p90_referencia − 10 µm (el instrumento no es optimista). Si ningún FC llega a 20, se agrupa por par de láminas (≥ 20). Si tampoco: V9 no evaluable y `valido` = false |
| V10 | Controles negativos: P-CK19 frente a B-HE-1 (otro bloque), y HER2NEG con las piezas permutadas entre FC | área verificada ≤ 5 % en cada uno, en las dos escalas |
| V11 | Diafonía DAB→H: en S_conf se quita hematoxilina y se pone DAB en el 10-60 % de los núcleos, agrupados | ΔU ≤ 5 µm y ningún FC cambia de pasa/no pasa |
| V12 | **Cadena sobre pilas sintéticas** (S_conf, ×8 de P-CK19): **≥ 20 pilas** de 5 cortes (semillas + 13 … + 32).<br>· Cada salto con deformación conocida: similitud + campo, p90 20 µm por salto (vecindad), y una segunda serie con p90 60 µm (arquitectura).<br>· Descorrelación de la textura fina calibrada para que el NCC de un salto sea **0,4-0,5** (como el control real, E11).<br>· DAB sintético alternado, nuclear en una lámina y citoplásmico en la siguiente.<br>Además, V9 aplicado a los caminos compuestos de confirmación | orden recuperado en ≥ 18 de 20 pilas; cobertura de la cota (error del extremo ≤ U_camino) con **límite inferior de Clopper-Pearson ≥ 0,90**, en cada serie; en los caminos reales de confirmación, V9 no detecta optimismo |
| V4 | Acuerdo I-a / I-b | informativo |
| V8 | Fiabilidad de I-e | informativo: ≥ 30 ensayos y ≥ 27 aciertos para llamarla fiable |

**`valido` = V1 ∧ V3a ∧ V3b ∧ V5 ∧ V6 ∧ V7 ∧ V9 ∧ V10 ∧ V11 ∧ V12**, sellado en `anexo_validacion` junto con `anexo_codigo` (§6). Si es false, KI67 no se registra (§8, P1).

### 4.5 Puerta por FC (los criterios del plan se mantienen)
**Por salto** (y en el par directo), un peldaño propio pasa en un FC si cumple a la vez:
1. I-a suficiente (n ≥ n_min de su camino) y cota < umbral de la escala, o la regla de fragmento pequeño (rotulada);
2. **solo cuando se certifica a escala de vecindad:** si I-b (configuración de vecindad) es suficiente, su estadístico con censura (sobre e_final) < 50 µm (veto). Se aplica a los saltos de la cadena y al par directo.
   **A escala de arquitectura este criterio no se aplica, en ninguna de las dos rutas.** Solo rige el veto de los extremos con la configuración de arquitectura (criterio 7). Así la escala de arquitectura se puede alcanzar igual por el directo que por la cadena. Un directo con error de 50-128 µm ya no queda vetado por la configuración de vecindad, cuya búsqueda se censura en 128 µm;
3. todas las cotas de §2.4, en los 10 ajustes de pliegue y en el final;
4. contención ≥ 0,95;
5. la distancia a los landmarks (§4.3): la zona a > 300 µm de cualquier punto de I-a no cuenta como verificada si supera el 10 % de los núcleos del FC.

**Por par A↔B:**
6. U de la ruta (U_camino, o la U del directo) por debajo del umbral de la escala, acotada al 97,5 % nominal (sin corrección por la elección de peldaño; cobertura real medida por V12);
7. veto de I-b en los extremos con la configuración de su escala (`veto_ib_por_escala`);
8. cierre en el centroide **solo entre transformadas estimadas de forma independiente** (cadena frente a directo, o pares sin saltos en común): ≤ 3° y ≤ 50 µm (vecindad) o ≤ 3° y ≤ la cota del par (arquitectura).
   - Si falla, el FC sale del mapa en todas las parejas implicadas (conservador; se declara).
   - Entre composiciones que comparten saltos no es puerta, y se declara.
   - Donde no se puede evaluar, no es puerta y se dice.

**Dos escalas, fijadas antes de medir:**

| Escala | Umbral de la cota | Qué permite | Estado |
|---|---|---|---|
| **Vecindad** (la del plan) | < 50 µm | todo lo del plan: (a), (a-bis), denominador CK19, L por la regla sellada | **preespecificada**; solo esta cuenta para **(ii) ≥ 50 % del área** |
| **Arquitectura** | 50 ≤ cota < 200 µm | **lista cerrada:** solo el Spearman regional de (a) a L ∈ {300, 400} µm (L ≥ 2 × cota) y el mapa. Rótulo: «secondary analysis specified after the per-protocol registration failure and before any registered target measurement; relaxes the protocol TRE <50 µm gate for regional co-localization; never replaces (ii), (a), (a-bis) or the CK19 denominator» | **análisis secundario declarado; solo como último recurso** (§8.1), porque relaja la puerta del protocolo y choca con el mandato de {{TITULAR}} («no alternativas menos precisas»). Nunca sustituye a (ii), (a), (a-bis) ni al denominador CK19 |

**Por qué existe la escala de arquitectura:**
- entre cortes lejanos la correspondencia a escala celular no existe (E6, E12);
- la precisión alcanzable la pone la distancia entre cortes, no el método (inferencia, E12);
- su cota se mide con el mismo instrumento que la de vecindad, con su propio veto (`veto_ib_por_escala`), V3a y V9.

La propusieron el orquestador expuesto (§8.2) y el diagnóstico, con geometría y sin cifras de diana. El metodólogo la revisó en la segunda vuelta y pidió estas restricciones (§10).

**R4:** similitud global de 4 gdl, KI67→CK19; solo en la ruta directa P-KI67↔P-CK19. No se ajustó con nuestros pliegues, así que se mide con todos los puntos. La fuga se declara despreciable (inferencia: 4 parámetros frente a cientos de puntos), y le aplican las mismas cotas y la misma puerta.
**R5:** solo sensibilidad. Sus FC no entran en el consenso principal.
**(iii), H&E:** el mismo método. Las alternativas de su puerta (otra quiralidad, otro giro a > 15°) se calculan **con el mismo modelo** (similitud RANSAC sobre DISK+LightGlue). Se mantiene: inliers ≥ 30 y ≥ 2× la mejor alternativa.
**Suelo físico:** el error crece con la distancia entre cortes (Lotz). El orden se estima (§2.0) y el Δz real **no lo sé**. Para cada par se anotan la distancia en el orden estimado, la proximidad S_ij de cada salto y la escala alcanzada.

---

## 5. VALIS: enmienda de la «vuelta única» (una sola vez, declarada)
- **Estado.** Según el plan (estado ≈13:05 local), VALIS corrió y la vuelta cuenta como gastada, pero su salida nunca llegó a una puerta TRE (E4). No abrí `escalera.json`, que está en SESION: **sin verificar** por mí.
- **Enmienda.** La vuelta única pasa a significar «la salida de VALIS juzgada por un instrumento válido». Evaluar esa misma salida no es una segunda vuelta.
- **(i) Evaluar la similitud que VALIS ya devolvió**, después de `valido`:
  1. **Sin datos**, se comprueba el conversor `_px_proc_a_um` con un sintético de las formas reales (5120×3584 y 3584×4096 px a ×8), s = 1,05 y giro de 18°, frente a `Slide.warp_xy_from_to` de VALIS. Pasa con error de escala ≤ 0,002 y ≤ 2 µm en rejilla.
  2. Si pasa: la matriz guardada (`registro/P-KI67.json` → `resumen.valis.matriz_um`) se evalúa como R4, por FC, sin proyección euclídea, con las cotas y el instrumento v2. Su escala a nivel 0 se informa.
  3. Si no pasa, es un error de ejecución del conversor: se corrige y se reproduce LA MISMA corrida dos veces (PNG v1 regeneradas con el código v1 sellado, VALIS 1.2.0, mismos parámetros). Si los dos M crudos coinciden, es recuperación de la salida. Si difieren, «VALIS similarity not recoverable» y R4 cae.
  4. (i) se informa siempre, gane o no.
- **(ii) VALIS en modo serie (R5):** `align_to_reference=False`, con su ordenación por similitud, rígido y no rígido por defecto, sin micro. Una corrida sobre las IHQ de la cadena (PNG v1 de cada una), **solo como sensibilidad**. Su orden se compara con el estimado (§2.0).
- Después de (i) y (ii), ninguna adenda vuelve a usar VALIS en (ii) KI67↔CK19.

---

## 6. Mecanismo de sello
1. **Regla del hash** (la de `laminillas_sello.sha256_de`): sha256 del JSON canónico (claves ordenadas, separadores `,` y `:` sin espacios, UTF-8, sin NaN), **sin el campo `sha256`**.
2. **Al sellar, el implementador rellena solo:**
   - `sello_base.sha256`: los 64 hex, leídos con `Sello.sha256` dentro de la jaula; tienen que empezar por `d014ff7df6e4016e`;
   - `fecha_sellado_utc`;
   - `estado` = `"sellada"`;
   - `sha256`.

   Test: con esos cuatro campos devueltos a su valor de borrador, el hash del canónico es el **sha256 de borrador** del mensaje de commit. El JSON lleva `md_sha256`, el sha256 de este MD.
3. **Lista blanca:** `superpone` tiene exactamente la clave `"modulo_b.registro"`; cualquier otra → `SelloInvalido`. El sello de (0) no se edita; su sección `registro` (v1) queda como historia.
4. **Cotejo cerrado ante fallos:** con la adenda activa, el cotejo es contra `superpone["modulo_b.registro"]` y **una clave que falta o sobra es error**. Hoy `coteja` hace `if v is None: continue` (`laminillas_metricas.py:350-356`) y una clave omitida pasaría. Test: `set(PARAMS_V2) == claves de la adenda`.
5. **Activación por sesión:** la orden `adenda-activa` escribe `SESION/adenda_registro.activa.json` (sha256 de la adenda, sello base, fecha UTC). Sin activación no corre v2; con ella, v1 ya no corre (ii), (ii-bis) ni (iii).
6. **Cadena de anexos**, misma regla de hash; cada uno cita al anterior:

   | Anexo | Contenido |
   |---|---|
   | `anexo_entorno` | `pip freeze` con hashes; sha256 de los pesos DISK/LightGlue, también del fichero en caché de `torch.hub` |
   | `anexo_desarrollo` | τ, (w_p, w_b), orden de la cadena con su estabilidad, E11 |
   | `anexo_codigo` | commit y sha256 de cada `tools/laminillas_*.py` y de sus tests en el momento de `valida-registro` |
   | `anexo_validacion` | V1-V12 y `valido` |

   `registro-par P-KI67` se niega si algún hash del código difiere de `anexo_codigo`. Los cambios de código antes de la primera corrida de confirmación son desarrollo. Después, cualquier cambio obliga a repetir la confirmación y **cuenta como iteración de P1** (§8).
7. **Libro de corridas:** `SESION/registro_corridas.jsonl`, solo se añade, encadenado por hash (cada línea lleva el sha256 de la anterior). Cada corrida v2 (desarrollo, confirmación, KI67) apunta adenda, código, semillas y sha256 de sus productos.
8. **Guardas:**
   - **Versión de método en «hecho»:** `marca_hecho(..., metodo="registro-v2+<sha12 adenda>")` y `esta_hecho(..., metodo=)`. Un «hecho» v1 no vale para v2.
   - **Candado de congelación (P3):** `SESION/registro_congelado/<diana>.json`, solo se añade y en UTC. Lo escribe ANTES de calcular toda orden que lea positividad de esa diana (`metricas`, `geojson`, visor, `capas-piloto`, `exporta`, F3, tribunal). Con el candado puesto, el registro v2 de esa diana se niega salvo con `--desviacion "<causa>"`, que conserva las dos versiones y lo declara. Se prueba con un mutante. (`lecturas_diana.jsonl` no sirve de guarda: solo lo escribe `celulas_diana`, P-KI67 ya tiene entradas v1 y va en hora local.)
   - **Gancho de auditoría:** `sys.addaudithook` en los procesos v2 aborta todo `open` de `objetos_*.npz` de dianas, `escalera.json`, `metricas/`, `piloto-i.json`, `L.json`, `lecturas_diana.jsonl`, GeoJSON, visor, capas y `piloto*.log`. Se prueba con un mutante.
   - **Orden:** `registro-par P-KI67 P-CK19` exige `anexo_validacion` con `valido: true` que cite la adenda activa y `anexo_codigo` con hashes iguales.
9. **Efectos aguas abajo** (campo `efectos_aguas_abajo`, con test de que cada consumidor lee el valor v2 y el sha de la adenda):
   - L (≥ 2× el TRE exportado = U);
   - erosión de la máscara CK19 (1× U);
   - dilatación 1× TRE del control de fondo (F3);
   - puerta de L del mapa (a) (2× p90 ≤ L);
   - consenso FC (máscara provisional);
   - puerta (iii) (mismo modelo);
   - regla de fragmento pequeño (puntos);
   - vuelta de VALIS (§5);
   - comprobación de densidad (3);
   - puerta de p63 (registro);
   - GeoJSON y visor (inversas v2);
   - orden de los cortes (pasa de informativo a operativo: define la cadena);
   - escala de arquitectura (pares de 50-200 µm): solo el Spearman regional de (a) a L ∈ {300, 400} µm y el mapa, como último recurso; nunca sustituye a (ii), (a), (a-bis) ni al denominador CK19;
   - composición por el camino mínimo A…B, sin rodeo por CK19 (test a ±2 µm);
   - veto de I-b en un único campo, `veto_ib_por_escala`.

   Todo producto lleva `adenda_registro_sha256` y `anexo_validacion_sha256`, y la caché de `_valor_L` se invalida si cambian.
10. **Sello de tiempo externo** (OpenTimestamps o RFC 3161 de los hashes): es egress y **necesita el OK de {{TITULAR}}**. Sin él, Métodos dice «internal timestamps only».

---

## 7. Plan de implementación (en orden)

| # | Fichero · función | Qué | Test |
|---|---|---|---|
| 1 | `laminillas_stack.py`, `laminillas_ventanilla.py` (`PROCESADORES`), `laminillas_pesos.py` | **Venv** `~/.polaris-venvs/registro` (Python 3.12), versiones fijas:<br>· `torch==2.7.1` y `kornia==0.8.1` (los del venv `valis`, con los pesos sellados; rueda de kornia `5dcb00faa795dfb45a3630d771387290bc4f40473451352ca250e5bcc81af3d1`);<br>· `itk-elastix==0.25.4` (cp311-abi3 macOS 15 arm64, `dbd05b6afeee6b25b3988bffcf92330d679a0e32195df8c430f87e231ea6ce3b`);<br>· `itk==5.4.7` (`c7c254419071b178bdf3b50b5542aaef5de3b54bc524b1f6fea72c62f531e590`; sus `itk-*` los fija el anexo).<br>El resto (scikit-image, scipy, shapely, numpy, opencv) se resuelve en seco y se instala con `--require-hashes` dentro de `red-sin-zona.sb`.<br>**Procesador** `laminillas_registro2` (venv `registro`, perfil `analisis`, `mem` 6). **ANTs, fuera** | humo sin datos: B-spline de elastix con spacing en mm; DISK+LightGlue en CPU determinista; sha256 de los pesos en caché |
| 2 | nuevo `tools/laminillas_transformada.py` | clase `Transformada`: lineal 3×3, o A∘(Id+u) con campo denso, origen y paso. Métodos `aplica`, `inversa` (A analítica, u iterativa, control de consistencia), `compone`, `jacobiano`, `giro_local` (polar), `descompone` (s, σ₁/σ₂, cizalla, θ, escala transversal y longitudinal), `cumple_cotas`, `serializa`/`carga` (npz + json con sha256) | `tests/test_laminillas_transformada.py`: verdades conocidas; mutantes x↔y, directa↔inversa, signo del giro, det < 0, convenio de elastix |
| 3 | nuevo `tools/laminillas_adenda.py`; `laminillas_sello.py` (`exige(..., adenda=)`, `Sello.modulo_b`); `laminillas_metricas.coteja` | carga y verifica la adenda, lista blanca, superposición, cotejo cerrado ante fallos, cadena de anexos, libro de adendas `SESION/registro_adendas.jsonl` (solo se añade, encadenado por hash, tope 3), semilla maestra inmutable | `tests/test_laminillas_adenda.py`: hash de borrador; **paridad MD↔JSON** (sha256 del MD = `md_sha256`; el texto de Métodos del MD, normalizado en espacios, es igual a `metodos_en`; las cifras clave de la tabla de parámetros aparecen en los dos); clave extra o que falta → rechazo; sello base equivocado → rechazo; anexo que no cita la adenda → rechazo; cuarta adenda en el libro → rechazo; **todas las semillas derivadas son distintas** |
| 4 | `laminillas_comun.py` | `marca_hecho`/`esta_hecho` con `metodo`; candado `registro_congelado`; libro `registro_corridas.jsonl` encadenado | un «hecho» v1 no salta v2; mutante sin candado → falla; cadena rota → falla |
| 5 | nuevo `tools/laminillas_registro_v2.py` (v1 queda intacto) | `ImagenRegistroV2` (hematoxilina a 8 y 4 µm/px, suavizada), `mascara_provisional` (desde centroides de segmentación), `piezas` (partición geodésica), `rasgos_disk`, `empareja_lightglue`, `ransac_modelo`, `global_v2`, `empareja_piezas`, `fcs_v2`, `proximidad` (S_ij), `orden_cadena` (Held-Karp y bootstrap del orden), `camino`, `compone_camino`, `u_camino` (propagación con Bonferroni), `lipschitz` (Λᵢ sobre la transformada estimada), `rutas` (cadena y directo al 97,5 %; camino mínimo sin rodeo por CK19), `veto_extremos` (`veto_ib_por_escala`), `distancia_landmarks` (300 µm / 10 %), `detector_intermedias` (a ciegas, para el tribunal), `pliegues` (5 orígenes, franja), `r1_similitud`, `r2_afin`, `r3_bspline`, `r4_valis_guardada`, `r5_valis_serie`, `elige` (IC pareado), `registra_fc_v2`, `registra_par_v2` (cadena y directo), `registra_serie_v2`, `registra_he_v2`, `cierre` (centroide y rejilla) | `tests/test_laminillas_registro_v2.py` con tejido sintético fragmentado: escala, anisotropía, cizalla y campo conocidos; espejo; piezas que se mueven por separado; emparejado ambiguo; FC delgado; cotas que rechazan; jacobiano; **mutante de fuga** (un punto de medida dentro del ajuste o de la franja → falla); determinismo |
| 6 | nuevo `tools/laminillas_tre_v2.py` | `retenidos` (I-a, aclarado, e_final), `arquitectura` (I-b, censura, localización), `calibra_nulo`, `u95` (estadístico de orden y bootstrap), `sinteticos` (D1-D10, V11), `senuelos` (V3a, V3b, I-e), `estructuras` (V9), `negativos` (V10), `valida_instrumento` → anexos | `tests/test_laminillas_tre_v2.py`: k del estadístico de orden, también con Bonferroni por salto; censura; señuelos detectados; suficiencia; fragmento pequeño. **Centinela:** si más del 20 % de las ventanas de I-b dan desplazamiento exactamente 0, es error de ejecución y se para. Antes se busca la causa del «0 exacto» del mapa de densidad v1 (E13) con un sintético de desplazamiento conocido |
| 7 | `laminillas_proc.py` | `registro_par` → v2 con adenda activa. (ii-bis) y (iii) **leen** KI67→CK19 del disco con su sha, sin recalcularlo. `_p90_par` → U. `resultado_desde_disco` → `Transformada`. Órdenes nuevas: `adenda-activa`, `desarrollo-registro`, `valida-registro` y `valis-similitud-guardada`. Guardas de §6.8 y gancho de auditoría | `tests/test_laminillas_piloto_b.py` ampliado |
| 8 | `laminillas_geojson.py`, `laminillas_f3.py`, `laminillas_metricas.py` | inversas y regiones con `Transformada`; `consenso` con v2; candado antes de leer positividad; sha de la adenda en cada salida | los existentes, en verde; test de efectos aguas abajo |
| 9 | nuevo `tests/test_laminillas_sala_limpia_registro.py` + `tests/test_all.sh` | test estático: los módulos v2 no abren rutas vetadas; ninguna orden de registro imprime cifras de diana | en `test_all.sh` |
| 10 | corridas por la ventanilla, **las lanza un subagente no expuesto** | (a) sellar la adenda; (b) `anexo_entorno`; (c) segmentación (configuración sellada, sin positividad) de las IHQ que falten y `desarrollo-registro` (orden de la cadena, τ, pesos) → `anexo_desarrollo`; (d) `valida-registro` (confirmación, una vez, con P-P63↔P-CK19 sin inspeccionar) → `anexo_codigo` + `anexo_validacion`; (e) solo si `valido`: orden con las 11 (primer paso de `registro-par P-KI67`, informando si cambia el orden relativo); VALIS (i); (ii) KI67↔CK19 v2 por cadena y directo, cada ruta al 97,5 %; (ii-bis) con cierre; (iii) H&E; VALIS en modo serie como sensibilidad; (f) congelar los productos del registro; (g) comprobaciones ciegas preespecificadas: dentro y fuera de los FC (§8.2) y detector de intermedias (§2.0.7), que solo ve el tribunal; (h) métricas, sin cifras en el log | `bash tests/test_all.sh` una vez al final |

**Horas** (inferencia; trabajo de agente, sin colas):

| Bloque | Horas |
|---|---|
| Entorno y humo | 3 |
| `Transformada` y los ~31 sitios | 8 |
| Máscara provisional, piezas, emparejado y global | 8 |
| R1-R3 con 5 orígenes y franja | 7 |
| Cadena: proximidad, orden, camino mínimo, composición, Λ, dos rutas, veto en extremos, detector de intermedias y V12 (≥ 20 pilas) | 10 |
| Segmentación de las IHQ intermedias (lanzar y comprobar; el cómputo va en reloj) | 2 |
| Instrumento y validación (I-a, I-b, U, V1-V12) | 14 |
| Adenda, sello, anexos, candado, libro y gancho | 7 |
| VALIS (i) y (ii) | 3 |
| Corridas de desarrollo y confirmación | 6 |
| KI67, (ii-bis), (iii) | 3 |
| Tests completos y arreglos | 6 |
| **Total** | **≈ 77 h (67-92)** |

Tiempo de reloj de las corridas: **no lo sé**. Memoria prevista < 6 GB por par a 4-8 µm/px (inferencia; v1 llegó a 19,7 GB al cargar ×8 de tres láminas).

---

## 8. Regla de parada y exposición

### 8.1 Parada (tope duro: 3 adendas en el libro, esta incluida)
- **Roles obligatoriamente no expuestos:** implementador, decisor de P1/P2 y declarante del agotamiento. Lo certifica el agente de integridad contando en los transcripts, sin imprimir ninguna cifra, antes de cada decisión.
- **P1 · `valido` = false en confirmación:** KI67 no se toca. El decisor abre una adenda nueva solo con causa geométrica de desarrollo o confirmación, sellada antes de confirmar otra vez.
- **P2 · `valido` = true y (ii) < 50 %:** P2 **se abre siempre**; la escala de arquitectura no exime de abrirla.
  - Es **una sola** adenda motivada por el TRE de KI67.
  - La decide el agente no expuesto viendo **solo motivos agregados** (qué criterio falló en qué FC, sin imágenes ni cifras de diana).
  - Se desarrolla en controles y cuenta para el tope.
- **Agotamiento, criterio escrito ahora** (no es juicio de nadie): (a) el libro llega a 3 adendas, o (b) tras la adenda de P2, R1-R4 siguen dejando el área verificada a escala de vecindad < 50 %.
- **Con el agotamiento rige el último recurso declarado** (actualización 20), en este orden:
  1. el análisis secundario a escala de arquitectura (§4.5), solo en los pares que la certifiquen y solo su lista cerrada;
  2. para todo lo demás, la escalera sin registro, con su rótulo y la lista de todo lo intentado (cada peldaño × FC con su U, cotas y cierre; VALIS (i) y (ii); validez; adendas).

  Nada de esto se presenta como resultado equivalente.
- **P3 · Congelación:** con el candado de una diana, su registro no se toca más. Un cambio posterior es análisis post hoc: se informan las dos versiones y lo ve el tribunal.

### 8.2 Registro de exposición

| Quién | Qué | Cuándo (UTC) | Estado |
|---|---|---|---|
| Orquestador principal | % global de Ki67 de la escalera (cola de `piloto3.log`) | 2-oct 10:59:12 | notas de integridad (recuento por regex, valor nunca impreso) |
| Subagente «Diagnosticar SIFT del registro real» | ídem | 11:14:24 | ídem |
| Subagente «Orquestador del piloto por la ventanilla» | una cifra con ese formato, antes de la primera pasada real | 03:06:39 | que fuera sintética es inferencia |
| {{TITULAR}} | **no lo sé**: no se le comunicó en mensajes, pero pudo verla en la salida de una herramienta | — | lo cuenta el agente de integridad |
| Información espacial de P-KI67 (hotspot, heterogeneidad, GeoJSON, visor, capas) | quién la vio | — | **a completar** por el agente de integridad |
| `piloto4.log` y demás `piloto*.log` | cifras de diana | — | **a completar**: recuento por regex sin imprimir, en todos los `piloto*.log` y en los transcripts posteriores a las 07:54 |
| Autor de esta adenda | no vio el % de Ki67. Vio el reconocimiento DAB por píxel de 1-oct (con la fila de P-KI67) y agregados de CK19 y P-HER2; no usó ninguno | 2-oct | este documento |
| Críticos adversariales | instruidos para no abrir nada con cifras de diana | 2-oct | §10 |
| Agente del diagnóstico numérico (995fb22), fuente de E11-E13 | produjo la geometría que motiva la cadena | — | **casi seguro el mismo subagente que vio la cifra a las 11:14:24** (lo confirma el agente de integridad, según el orquestador). Sus datos son solo geometría; la decisión de diseño la tomó el autor no expuesto. Se declara |
| Orquestador expuesto, como proponente | propuso la cadena por vecinos, elegir el marco por geometría y «fijar la escala por la precisión medida» (plan, actualización 20; mensaje a esta sesión tras 995fb22) | ≈13:10 | declarado. El autor aceptó la cadena, rechazó el marco central (lista blanca) y aceptó la escala de arquitectura solo como análisis secundario de último recurso, con las restricciones del metodólogo |

**Medidas:**
1. Autor no expuesto; decisores e implementador no expuestos (§8.1).
2. Todo queda sellado antes de KI67.
3. La validez sale solo de controles y sintéticos, con desarrollo y confirmación separados.
4. El canal reduce el DAB y las piezas salen de una máscara que no depende de la tinción.
5. El código del registro no puede abrir productos con cifras de diana (gancho y test).
6. Las métricas no imprimen cifras en el log (49ceffa) y el orquestador no las lee antes del tribunal.
7. El % de la escalera no se borra ni se usa; va al tribunal como «unregistered fallback computed before the re-specification», con la misma prominencia que el resultado registrado.
8. **Comprobación ciega preespecificada**, tras congelar el registro: el % de Ki67 sin registro dentro de los FC verificados frente al de fuera. La calcula un proceso no expuesto, se sella y solo la ve el tribunal; sirve para detectar si la selección de FC se relaciona con la diana.

---

## 9. Frase de Métodos (EN)
> Per protocol, the pre-specified registration of the Ki67 slide directly to the CK19 reference (Euclidean, unit scale; accuracy check by nucleus-scale haematoxylin phase correlation requiring ≥15 valid peaks) verified <50% of the consensus area, and the pre-specified unregistered fallback was run; its slide-level Ki67 percentage was computed and seen by the orchestrating agent (2 Oct 2026, 10:59 UTC). Geometric diagnostics, run by an agent that had also seen that percentage, then showed that nucleus-scale texture is preserved only between near sections: the accuracy check passed on an adjacent control pair (the two HER2-labelled slides) and on self-registration, but failed for every non-adjacent pair under every candidate transform, including VALIS. At the sponsor's request (≈11:45 UTC), registration was re-specified ⟨k⟩ time(s) in sealed addenda (first draft 12:26 UTC; sha256 ⟨…⟩; internal timestamps only unless stated). The addenda were written by an author without access to that percentage (who had seen pixel-level DAB reconnaissance of all slides) and sealed before any registered target measurement. The chained design was proposed by the exposed orchestrating agent and adopted with restrictions after adversarial review. Slides are ordered by texture persistence, registered to their neighbours along the shortest path, and composed to the CK19 frame. Each hop fits per-fragment similarity, affine and constrained B-spline (elastix) models to DISK+LightGlue features of the smoothed haematoxylin channel, with VALIS as a fallback and, in serial mode, as a sensitivity analysis. Accuracy is an upper bound of the 90th-percentile error of the final transform, at a nominal 97.5% per candidate and route (chain or direct); the choice among model rungs within a route is not corrected for multiplicity, and the coverage of the whole procedure is measured on synthetic section stacks (Clopper-Pearson lower bound ≥ 0.90). It is estimated per hop by buffered spatial cross-validation on held-out feature landmarks, propagated along the chain with Lipschitz amplification and a union bound, and vetoed at the endpoints by nuclear-density architecture. Validity was established before use on control slide pairs (two inspected in the diagnostics and one randomly drawn, uninspected pair), negative controls, an independent structure-based reference, and synthetic deformations including at least 20 synthetic section stacks. Pairs whose bound is below 50 µm are reported at neighbourhood scale, as pre-specified. A secondary analysis at architecture scale (bound 50-200 µm; regional Spearman at L of 300 or 400 µm and the map only) was specified after the per-protocol registration failure and before any registered target measurement. It relaxes the protocol 50 µm gate, is used only as a declared last resort and never replaces the pre-specified analyses. Both the per-protocol (unregistered) and the re-specified (registered) results are reported.

---

## 10. Críticos adversariales: qué se incorporó y qué no
Dos críticos en Opus, con la regla de sala limpia en su prompt. Sus informes llegaron al orquestador, que los reenvió en resumen fiel y sin cifras de diana. **Veredictos:** metodólogo, apto con cambios (7 bloqueantes); experto en registro, no apto tal cual (6 bloqueantes corregibles).

### Metodólogo

| # | Corrección | Decisión |
|---|---|---|
| 1 | «una vez» era falso: tope 3, P2 ≤ 1 adenda y Métodos con k | **aceptada** (§8.1, §9) |
| 2 | bifurcaciones decididas por no expuestos; agotamiento por criterio escrito | **aceptada**. El recuento en transcripts lo hace el agente de integridad: el autor no puede abrir transcripts (sala limpia) |
| 3 | censura de I-b, ≥ 50 % de ventanas válidas, señuelos de 150 y 300 µm por la puerta entera | **aceptada** (§4.2, V3a) |
| 4 | separar desarrollo y confirmación | **aceptada** (§4.4) |
| 5 | sellar el código (`anexo_codigo`) | **aceptada con matiz**: los cambios anteriores a la primera confirmación son desarrollo y no cuentan como iteración; los posteriores, sí |
| 6 | candado de congelación real | **aceptada** (§6.8) |
| 7 | cotejo cerrado ante fallos; test de claves | **aceptada** (§6.4) |
| 8 | control negativo V10 | **aceptada** |
| 9 | máscara independiente de la tinción; «reduce», no «excluye»; comprobación ciega dentro/fuera | **aceptada** (§2.1, §3, §8.2) |
| 10 | registro de exposición completo | **aceptada**: filas añadidas; los recuentos, al agente de integridad |
| 11 | taxonomía, misma prominencia, cronología | **aceptada**: la etiqueta une «error de especificación» (el encargo) con «validez del instrumento» (el crítico) |
| 12 | medir v1 en HER2NEG↔HER2; reescribir E4 | E4 **aceptada**. La medida **aceptada con cambio de sitio**: va a `anexo_desarrollo`, porque meterla en la adenda tras el borrador rompería la lista de campos que se rellenan al sellar |
| 13 | erosión y L con la cota superior; R5 fuera del consenso | **aceptada**, sin salir de la lista blanca: el TRE que se exporta es la U, así L y la erosión no cambian de código |
| 14 | sello de tiempo externo | **aceptada condicionada**: es egress y necesita el OK de {{TITULAR}}; por defecto, «internal timestamps only» |
| 15 | libro de corridas encadenado; semilla maestra inmutable | **aceptada** |
| 16 | gancho de auditoría | **aceptada** |
| 17 | `efectos_aguas_abajo` con test | **aceptada** (§6.9) |
| 18 | IC solo con ≥ 10 bloques | **aceptada**: el bootstrap exige ≥ 10 celdas; por debajo, estadístico de orden |
| 19 | Métodos narra la secuencia | **aceptada** (§9) |

### Experto en registro

| # | Corrección | Decisión |
|---|---|---|
| 1 | MAGSAC mal llamado en OpenCV | **aceptada con cambio**: RANSAC de scikit-image para similitud **y** afín (una librería, determinista, ya probada), en vez de `cv2.UsacParams` para la afín |
| 2 | referencia independiente V9; señuelo antes de ajustar | **aceptada** (V9, V3b) |
| 3 | franja de exclusión, bloques de 800 µm y 5 orígenes | franja y 5 orígenes **aceptados**. **800 µm rechazado**: con FC de 1,4-3,0 mm² ninguno llegaría a 10 bloques y no habría bootstrap posible. Se usan 600 µm con franja de 100 µm (núcleo de medida de 400 µm) |
| 4 | TRE de la transformada final; SyN | e_final **aceptado**. SyN: se toma la opción «quitar R3b» |
| 5 | puerta sobre la cota superior; ≥ 10 bloques; erosión con la cota; ventanas sin solape | **aceptada** (§4.3) |
| 6 | censura, localización, nulo estratificado; I-b no certifica | **aceptada** |
| 7 | diafonía DAB→H: suavizado, núcleos no derivados de H, V11 | **aceptada** |
| 8 | pesos de elastix en mm y calibrados; optimizador explícito; un hilo; pirámide | **aceptada**: (w_p, w_b) en una rejilla 3×3 calibrada solo en desarrollo |
| 9 | cota absoluta de 100 µm | **aceptada** la alternativa de estiramientos y giro local |
| 10 | bordes sin correspondencia | **aceptada** |
| 11 | piezas en las dos láminas y emparejado robusto | **aceptada** |
| 12 | FC delgados | **aceptada** |
| 13 | det J > 0 al 100 %; ∫det J por celda | **aceptada** |
| 14 | inversa A∘(Id+u) | **aceptada**; se quita «o equivalente» |
| 15 | ANTsPy: semilla y ejes | **resuelta al quitar R3b** |
| 16 | elección por IC pareado | **aceptada**: sustituye a la tolerancia de 5 µm del borrador |
| 17 | cierre en rejilla | **aceptada** la opción «centroide como puerta (plan), rejilla informativa» |
| 18 | I-e no es puerta | **aceptada**, y R5 pasa a solo sensibilidad |
| 19 | suelo físico y DISK a 8 con refinado a 4 | **aceptada** |
| 20 | cita completa de ACROBAT; Métodos declara lo que vio el autor; hash de la caché de LightGlue; P2 ≤ 1 | **aceptada** |

### Cambios tras los datos de 995fb22 y segunda revisión del metodólogo
Los datos de E11-E13 llegaron después de las dos críticas. Con ellos entraron la cadena por vecinos, VALIS en modo serie, el centinela del «desplazamiento 0» y la escala de arquitectura; el marco central se rechazó (lista blanca).
**Segunda revisión del metodólogo** sobre 74acc82: **no apto**, 6 bloqueantes. El orquestador los llamó «de redacción», pero tres (1.3, 1.4 y 2.1) cambiaron la estadística. Respuesta:

| # | Corrección | Decisión |
|---|---|---|
| 1.1 | el orden en desarrollo incluía P-KI67 y contradecía la confirmación | **aceptada**: desarrollo sin P-KI67 (ni P-P63); las 11 juntas solo como primer paso de `registro-par P-KI67`, con el código sellado, informando si cambia el orden relativo (§2.0.2) |
| 1.2 | eran 11 IHQ, no 10 | **aceptada** (verificado: `IHQ_CON_TEJIDO`, `laminillas_congela.py:80`; `SERIE_IHQ`, `laminillas_f3.py:71`) |
| 1.3 | la propagación omitía la amplificación | **aceptada**: U_camino = Σ Λᵢ·Uᵢ, con Uᵢ solo en la imagen del FC del par (§4.3). Infraestimación sin Λ: 5 / 10,3 / 16 % con m = 2 / 3 / 4 (recalculado) |
| 1.4 | dos rutas, cada una al 95 % | **aceptada**: cada ruta al 97,5 %, la cadena a 1 − 0,025/m por salto. n mínimo recalculado: 36 / 86 / 142 con m = 1 / 2 / 3 (el crítico no daba cifra; yo había escrito 147 y era 142) |
| 2.1 | el veto de arquitectura no llegaba a 200 µm | **aceptada**: configuración de arquitectura con ventana de 768 µm, búsqueda ±400 µm y su propio τ; un único campo `veto_ib_por_escala`. Añado ≥ 2 ventanas sin solape en arquitectura, por la anchura de los cilindros (inferencia) |
| 2.2 | V3a, V9 y el cierre no cubrían 50-200 µm | **aceptada**: V3a por escala (250 y 400 µm no pasan; 150 µm detectado como ≥ 100 µm en ≥ 95 %), compuerta de V9 de 300 µm en arquitectura, cierre ≤ 3° y ≤ la cota del par. Añado a V12 una serie con p90 de 60 µm por salto |
| 1.5 | cierre tautológico entre composiciones | **aceptada** (§4.2 I-d, §4.5) |
| 1.6 | V12 débil | **aceptada**: ≥ 20 pilas (+13…+32), NCC del salto 0,4-0,5, DAB alterno nuclear y citoplásmico, Clopper-Pearson ≥ 0,90, orden en ≥ 18 de 20; sensibilidad con la métrica de orden del plan |
| 1.7a | adyacencias inestables | **aceptada** (máximo sobre los órdenes con frecuencia ≥ 10 %) |
| 1.7b | eje NE en las intermedias | **aceptada con cambio**:<br>· detector a ciegas aceptado, ampliado a toda diana intermedia;<br>· **no se excluyen solo las NE como intermedias**: rompería la simetría luminal/NE que pide {{TITULAR}} (el mismo riesgo valdría para una luminal si su DAB satura), y excluir por intensidad de DAB usaría datos de diana;<br>· la sensibilidad, redefinida en la tercera revisión por mecanismo: solo intermedias de `SUELO` |
| 2.3 | rótulo, lista cerrada y choque con el mandato | **aceptada**: rótulo literal, lista cerrada (Spearman de (a) a L ∈ {300, 400} y el mapa), solo como último recurso (§4.5, §8.1) |
| 2.4 | «o si no se abre» | **aceptada**: P2 se abre siempre |
| 3.1 | fila del orquestador como proponente | **aceptada** (§8.2) |
| 3.2 | pares de confirmación ya inspeccionados | **aceptada**: se declara en V6 y en Métodos. Se añade **P-P63↔P-CK19**, sorteado con la semilla **+33** (no +13, que choca con las pilas de V12); el resultado del sorteo queda escrito aquí |
| 4.2 | distancia al landmark | **aceptada** (> 300 µm en > 10 % de los núcleos → zona no verificada). Roberts 2017 y Milà 2022, abiertos en Crossref (resumen) |
| menores | R4 solo en la ruta directa; «máximo físico» → inferencia; agente de 995fb22 casi seguro el mismo; el implementador recoteja E11; camino mínimo con test a ±2 µm; paridad de Métodos normalizada; libro `SESION/registro_adendas.jsonl` | **aceptadas** |

### Revisión final del metodólogo (sobre a760cd9)
**No apto por un solo bloqueante**, de redacción. Confirma resueltas las seis correcciones anteriores. Respuesta:

| # | Corrección | Decisión |
|---|---|---|
| B1 | «97,5 % por ruta… cobertura conjunta ≥ 95 %» era falso: la elección de peldaño dentro de la ruta no está corregida | **aceptada** (redacción): «97,5 % nominal»; la elección de peldaño no se corrige por multiplicidad (falso pase de hasta K × 2,5 % en el peor caso); la cobertura del procedimiento entero la mide V12. Cambiado en el TL;DR, §2.0.6, §4.3, §4.5, Métodos y JSON (`rutas`, `u_certificada`). La alternativa estadística (1 − 0,025/(K·m); n mínimo 46/107/174, y 49 con R4) se declara y no se adopta, porque deja más FC sin certificar y V12 ya mide la cobertura real |
| I1 | semillas que se pisaban | **aceptada**: los orígenes pasan a +34…+38, el sorteo sigue en +33 y se añade un test de semillas distintas. **El sorteo con +13 no se ejecutó nunca**: dos intentos con +33, el primero falló al importar numpy sin dar resultado. No hay bifurcación |
| I2 | «RE, con DAB saturado» | **aceptada**. La frase venía del plan (reconocimiento del 1-oct: «P-RE … saturada»), así que era un dato cualitativo de diana. Pasa a «si su DAB satura». Además, la sensibilidad de intermedias se redefine **por mecanismo**: solo intermedias de `SUELO` (sin DAB de diana por diseño), saltando toda diana con DAB. Responde también al menor (a): el subconjunto {RE, SYN, CHGA, {{DIANA3}}} no era ni luminal completo ni mecanístico. Con esto, «ninguna decisión de esta adenda sale de ahí» vuelve a ser cierto |
| I3 | el veto de vecindad bloqueaba el directo a escala de arquitectura | **decidido y escrito** (§4.5, criterio 2): el veto de vecindad solo se aplica al certificar a escala de vecindad, en las dos rutas; a escala de arquitectura solo rige el veto de los extremos con su configuración. Las dos rutas pueden llegar a arquitectura |
| menor | `V9.referencia` decía «compuerta 100 um» | **corregido** en el JSON (compuerta por escala) |
| menor | «de redacción» se quedaba corto | **aceptado**: 1.3, 1.4 y 2.1 cambiaron la estadística; dicho así en §10 y en la cronología |
| menor | `n_min_por_m` solo hasta m = 3 | **aceptado**: fórmula y tabla hasta m = 10 (m = 4 → 201) en el MD y en el JSON |
| menor | arquitectura con n = 2 | **aceptado**: con < 10 ventanas válidas el estadístico del veto es el máximo; se informa la fracción de FC de arquitectura sin veto porque no caben 2 ventanas |
| menor | V3a: ¿compuesta o salto? | **aceptado**: dos variantes, V3a-c (compuesta: veto y cierre) y V3a-s (un salto: U) |
| menor | orden inestable | **aceptado**: el orden de Held-Karp siempre entra en el máximo, aunque aparezca en < 10 % de las réplicas |

## Anexo A · Fuentes y estado
- **Verificadas por el autor en esta sesión:**
  - Lotz et al. 2023, J Med Imaging, PMC10704256: texto completo por Europe PMC (`curl`).
  - Jones, Milthorpe, Howlett 1994, PMID 8168406: resumen por Europe PMC (`curl`); rata.
  - ACROBAT 2022 (arXiv 2305.18033), RegWSI (arXiv 2404.13108) y StainBridge (arXiv 2609.17090): grep sobre los textos descargados en `registro_inv/`.
  - Código de VALIS 1.2.0 (`registration.py:63, 71`; `serial_rigid.py:1669, 1843`).
  - Cuadernos de ITKElastix descargados: nombres de `CorrespondingPointsEuclideanDistanceMetric`, `TransformBendingEnergyPenalty`, `MultiMetricMultiResolutionRegistration` y `DisplacementMagnitudePenalty`.
  - En los venvs: OpenCV 5.0.0 y 4.9.0 tienen la constante `USAC_MAGSAC`; kornia 0.8.1 tiene `DISK` y `LightGlueMatcher`.
  - JSON de PyPI con los sha256 de las ruedas.
- **Del metodólogo, abiertas por mí en Crossref (solo resumen):** Roberts et al. 2017, Ecography, doi 10.1111/ecog.02881; Milà et al. 2022, Methods Ecol Evol, doi 10.1111/2041-210X.13851.
- **Del crítico de registro, no recotejado por mí:** que `estimateAffinePartial2D` rechaza USAC_MAGSAC (`ptsetreg.cpp` 4.9.0) y que ANTsPy 0.6.3 ignora `random_seed`.
- **Abiertas por los subagentes de investigación, no recotejadas por mí:** VALIS (PMC10372014), ANHIR (PMC7584382), CORE (arXiv 2511.03826), Mayo TMA (PMC13370634), Map3D (PMC11008751), README de DeeperHistReg.
- **No abiertas por nadie:** Fiorin WBIR 2024 y SWIFT-Reg (Springer 403). scite pide autorización y no estuvo disponible.
- **China:** Europe PMC con `AFF:"China"`, arXiv y WebSearch (SIAT-CAS, Beihang, Tsinghua SZ, Tiangong). CNKI y Wanfang no se buscaron.

## Anexo B · Procedencia (sha256 de las entradas, 2-oct 12:26 UTC)
- plan: `3be8d77d5c189f81f1315a9229fa301eb3c220e8f010e654cd780a0317ea00ab`
- `laminillas_registro.py` (con cambios sin commitear de otro agente): `a099384b1943698b0977e0377753407f262220eb5606da3e86188f0d33b7f742`
- `laminillas_proc.py` (ídem): `83a0d1e30dc8198f0d43b3cc589db45f9e4885cb5f53ed0b3de01a144ec85692`
- `laminillas_sello.py`: `3169cba809092076fd8326255b9c20e8ce782f2af9aff0448185542374cb6da4`
- `laminillas_congela.py`: `a03aea0b45263d271fc7fb2a43b41cfa3afebcb99fd40c6e71ee6f15c9afb67d`
- `laminillas_metricas.py`: `25e6d5e132451a86e94c8133322f5c93952952982ddec3933e024989fa192865`
- `diag_ki67.log`: `39e7ffb8ada42d9a9fb84ed601eac60eb231ae32721877d6329740baf6ddcb17`
- `diag_her2neg.log`: `8d45eea4258b03e6cf34a060eaf393c97a82640ece096d0f0bf4ed6a67052eb1`
- HEAD del worktree: `49ceffa40847d7a836e7f53a3809be213ce76207`
- **Revisado a las 13:12 UTC**, tras 995fb22:
  - HEAD `28c7d85f55d5b9e25b9556b5490edf01f9a549b4`;
  - plan `dddb619beb4d3b438ffc483a80f49a227273e33e6f78e5dffeeff1cfdd0246bb`;
  - `laminillas_proc.py`, que otro agente sigue cambiando sin commitear: `cfcc49fa72eac0c7471ae58720dea2ed540e5159edc90d41f075b68dc9847191`;
  - `laminillas_registro.py` sigue en `a099384b…f742`, ya commiteado en 995fb22.
- **Revisado a las 13:38 UTC**, tras la segunda revisión del metodólogo:
  - HEAD `74acc82d2fd2a3f2190c34fc4898cba99b06e455`;
  - `laminillas_proc.py` `ba5bd22f2b14c0b4ee08808f88265580b17ec28f04609790e3f1a8f0cdf9c3a3`, ya sin cambios sin commitear;
  - cotejado en el código: `IHQ_CON_TEJIDO` (`laminillas_congela.py:80`) y `SERIE_IHQ` (`laminillas_f3.py:71`), las dos con 11 láminas; `rejilla_anclada="P-CK19"` (`laminillas_congela.py:156`).
