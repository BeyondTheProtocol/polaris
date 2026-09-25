# CHANGELOG — Polaris (Beyond the Protocol)

Registro de cada **salto** del sistema hacia NED. Esto **no es semver**: Polaris es un
sistema vivo, así que versionamos por **fecha** (`vAAAA.MM.DD`, con sufijo `-2`, `-3`… si
hay varios en un día). Una *release* = un merge a `master` que mejora el sistema (o su
ruta clínica) de forma observable.

Lo mantiene `tools/release.py` (determinista, local, $0): tras fusionar una mejora,
`python3 tools/release.py "qué mejoró" --items "punto;punto"` añade aquí la entrada y
crea el tag datado sobre el HEAD de `master`. `python3 tools/release.py --list` lista las
releases. El muro manda: nada hacia fuera, sin push.

---

## v2026.09.17 — la vigilancia hacia NED pasa de mensual a diaria
- el radar de literatura era MENSUAL y no corrió el 1-sep (HALT activo): 47 días sin barrer; el último digest real era del 1-ago
- nuevo `tools/radar_ned_diario.py`: barrido DIARIO determinista, sin LLM, $0 — Europe PMC (PubMed/PMC + preprints bioRxiv/medRxiv), colección de patentes de Europe PMC y ClinicalTrials.gov v2
- gate de gasto en `tools/radar_ned_dia.sh`: mirar es gratis; el triaje que piensa (~1-3 $) solo arranca si hay novedad en una diana de PRIORIDAD ALTA, y hoy queda opt-in (`RADAR_NED_TRIAJE=1`)
- daemon `com.btp.radar-ned-diario` a las 06:40, antes del parte de la mañana
- 10 dianas con prioridad explícita; entran por fin las DOS que el propio radar marcó como hueco en 14-jul y 01-ago y nunca se aplicaron: NE de mama ({{DIANA2}}/{{DIANA3}}/{{DIANA4}}) y FGFR4/FGF19 — añadidas también al radar mensual
- precisión antes que volumen: las queries de ClinicalTrials.gov van por `AREA[...]` con comillas (la búsqueda laxa devolvía migraña pediátrica al preguntar por ONA-255) y las patentes se recortan por antigüedad (el índice PAT de Europe PMC ignora `FIRST_PDATE`: verificado)
- un digest bueno ya no se machaca con una pasada vacía
- lo que este carril NO cubre queda escrito en cada digest: CTIS (ONA-255 solo vive ahí), congresos y notas de prensa necesitan navegador
- **la verificación deja de ser una promesa**: cada lead de diana ALTA entra en una COLA (`tools/state/radar_ned/cola_verificacion.json`) que solo se cierra con `cerrar --ref … --veredicto …`, es decir abriendo la fuente; sin veredicto no hay cierre
- la cola se inyecta en la brújula (`tools/contexto_lazo.py`) → sale en CADA sesión interactiva y en CADA job del lazo: los leads del 14-jul y del 1-ago pidieron OK para verificarse y se perdieron porque no había dónde estuvieran siempre delante
- el triaje autónomo tiene prohibido cerrar cola: no puede abrir fuentes (el muro le deniega WebFetch y los MCP a propósito), así que su único resultado legítimo es dejarla marcada
- **capa ABIERTA (corrección de {{TITULAR}} el 18-sep): «novedades en general que nos acerquen a NED, da igual de lo que sea»**. Una watchlist de 10 dianas, por construcción, nunca puede traer la diana que aún no tiene nombre. Cinco temas nuevos que barren sin saber qué buscan: agentes y ensayos recién REGISTRADOS en mama sea cual sea su mecanismo, vocabulario de erradicación (respuesta completa, curación, oligometastásico), enfermedad residual molecular y ctDNA, reversión de resistencia, y mecanismos trasladables desde otros tumores con su biología
- la capa abierta entra al digest y a la cola con **tope de 3 por tema y día** (informa sin inundar) y no dispara aviso por sí sola: el juicio de si algo acerca a NED es de la sesión
- primera pasada real: trajo un ensayo fase 2 para **HER2 ultra-low** y un bispecífico **{{DIANA2}} en neuroendocrino extrapulmonar** — dos cosas que ninguna de las 10 dianas habría encontrado
- **capa de MODALIDADES (segunda corrección suya el mismo día: «no solo ensayos, toda la literatura que a mí me puede afectar: CAR-T, vacunas»)**: cinco temas que barren por PLATAFORMA y no por diana ni por ensayo — terapia celular (CAR-T, CAR-NK, TIL, TCR-T, CAR in vivo), vacunas y virus oncolíticos, convertir un tumor frío en caliente, radioligandos y teranóstica, y reversión de fenotipo (epigenética, diferenciación, metabolismo, reposicionamiento). Incluye preprints
- **marcas de validez externa en el digest**: ⚠️ señala lo que probablemente no es su subtipo (triple negativo, HER2-positivo, microcítico…) o es preclínico (murino, 4T1, xenoinjerto, líneas celulares). No se descarta nada: se avisa, para que un resultado de 4T1 no se lea como si fuera de su luminal B
- el cupo de la capa abierta dejaba leads fuera EN SILENCIO por orden de fecha — el ensayo de HER2 ultra-low, el más relevante del día, se quedó sin encolar por ser el cuarto. El cupo se queda, pero ahora dice cuántos deja fuera
- **capa CHINA (peticion suya: «busca en China tambien, que para eso tenemos hueco en servidor alli»)**: cuatro temas que filtran Europe PMC por **afiliacion china** (`AFF:"China"`) y ClinicalTrials.gov por pais — ADC y biespecificos, terapia celular en solidos, vacunas y radioligandos, y sus dianas concretas. Funciona desde Espana, $0, sin proxy: captura lo que publican Hengrui, Kelun, SystImmune o BeiGene, que llega a Europa anios despues o nunca
- **verificado en vivo el estado del hueco chino**: el VPS de Hong Kong responde y el proxy con IP china esta vivo; **el CDE de la NMPA y ChinaXiv abren por proxy**, pero sus buscadores son SPA o rechazan POST, y **ChiCTR solo sirve la portada** (su busqueda responde 405). Esas tres fuentes necesitan navegador, igual que CTIS: queda dicho, no fingido
- 24 temas en cuatro capas: 10 dianas, 5 abiertos, 5 modalidades, 4 China
- **carril de navegador (`tools/radar_navegador.py`) para los tres registros sin API**: CTIS, ChiCTR y el CDE de la NMPA. No conduce el navegador (el lazo autonomo lo tiene denegado y asi sigue): hace la mitad determinista — recibir lo que el navegador extrajo, normalizarlo, deduplicarlo contra el mismo cache y volcarlo al digest y a la cola con prioridad ALTA, porque eso no lo trae ninguna otra fuente
- **los tres barridos, hechos hoy**: CTIS 20 ensayos (IEV407 en HR+/HER2- avanzado, BGB-43395 fase 3 de BeiGene reclutando en Espana, premedicacion con dexametasona contra la ILD del T-DXd), ChiCTR 10 (entinostat + CDK4/6i + endocrino), CDE 20 (SSGJ-612 con ADC anti-HER2 en HR+ **HER2 ultra-low**, SI-B036 biespecifico, SKB103)
- **cazado y revertido**: el buscador del CDE ignora `form_input` y devuelve los ultimos registros del pais entero; la primera ingesta metio 20 ensayos de linfoma y artritis en la cola. Revertidos, el digest lo dice, y el plan del tool avisa: hay que teclear y pulsar Return
- la brujula avisa cuando una de las tres fuentes lleva mas de 7 dias sin barrerse: automatizar el barrido es imposible, automatizar el recuerdo no
- `tests/test_radar_navegador.py`: 13 asserts
- `tests/test_radar_ned_diario.py`: 26 asserts — la cola no se duplica, un cerrado no reaparece, sin veredicto no se cierra, sale en la brújula, ninguna query lleva huella genómica

## v2026.07.25 — auditoría de Polaris: brújula, fuga clínica y las redes que no existían
- la brújula ya no retrocede al llegar los resultados de la biopsia (CERRADOS como definición única)
- la etiqueta NED deja de pesar cero: 5 hilos suben al parte, entre ellos el courier a {{CENTRO}}, sin un solo Telegram nuevo
- el caché de estado de los 4 ensayos llevaba 29 días congelado y nadie lo sabía — daemon com.btp.nct-cache encendido y frescura fail-loud
- fuga clínica cerrada: las carpetas de informes eran invisibles para los dos guards, ahora hay UNA fuente de zonas clínicas y gate en drive.py
- 25 tests escritos que nadie corría (4 del muro) enganchados + meta-check para que el desfase no se reabra
- seguimiento.json deja de perder escrituras concurrentes (12 de 12 sobreviven, antes 9)
- la cadena clínica envejece: el parte dice cuántos días llevas esperando
- honestidad_lint barría CERO documentos y perdonaba «según X», el patrón de relay que originó el tool
- un DOI fabricado dentro de una bibliografía pasaba limpio: ahora sale FABRICADA
- la poda de ramas dejaba de llevarse borradores del gate y worktrees con sesión viva
- _merge_ `f874d83`

## v2026.06.28 — El chat recuerda y el sistema se cura solos
- Recall activo de memoria en el chat (SessionStart brújula NED + memorias relevantes por prompt)
- Vigía de roster: caza el daemon KeepAlive caído sin avisar (autofix kickstart, escala si no revive)
- Auto-recover de jobs caídos por schema-desconocido, con freno anti-bucle
- Agente contacto (consejero de arneses locales) + dossier + skills de {{CONTACTO}} destiladas (humanizer a voz, calidad de código Python, AGENTS.md)
- _merge_ `06ffde5f`

## v2026.06.27-4 — Regla inquebrantable: proteger el goal incluso de {{TITULAR}} (muro)
- código rojo se dispara también ante una decisión de {{TITULAR}} que amenace NED
- decide consciente e informada, no en silencio
- crisis→ayuda humana
- _merge_ `56f6f212`

## v2026.06.27-3 — Sistema de control de errores completo (Tablero + recuperación + rotación)
- Tablero de Errores en el Observatorio
- queue recover + reintentos por severidad
- rotar_logs
- vigia/healthcheck conectados al bus
- guardia anti-regresión de fugas
- _merge_ `93df09f9`

## v2026.06.27-2 — Control de errores (espina) + tope €500 con cortacircuitos
- bus único de errores con taxonomía de severidad y escalado graduado
- cerradas 6 fugas except:pass
- cost_guard a €500/día con freno de gasto-de-golpe→código rojo
- _merge_ `d2f60ec9`

## v2026.06.27 — El arnés agéntico a 10/10
El sistema ahora **se ve a sí mismo ejecutar** y **se autovigila contra regresiones**.
- **Observabilidad por ejecución**: traza estructurada por job/agente (duración, tokens/€, fallos con stack trace) en `tools/state/observabilidad/`, cableada a los daemons 24/7 (run_agent, dispatcher, vigía, healthcheck) + tarjeta en el Observatorio.
- **Evals de drift**: casos-oro deterministas que cazan regresión del muro y lo clínico (agente público que toque lo clínico, clínico fuera de Opus, borde que deje pasar PII/canario), corriendo en cada `test_all.sh`.
- **Jerarquía de evidencia del comité médico**: Consensus/scite (puente manual) antes que el buscador-LLM; toda cita contra fuente primaria.
- _merge_ `b6245ab8`

---

## Historial reciente (pre-ritual, sin tag retroactivo)
Mejoras fusionadas antes de existir este registro, para no perder la memoria:
- **2026-06-27** · la puerta se auto-vigila (auto-reporte 503 + healthcheck del gateway) — `d8fdf3bb`
- **2026-06-27** · Vivir nunca enmudece (relevo de cortesía al local + reserva de gasto interactiva) — `c335efc8`
- **2026-06-27** · Guardián-bisturí F1 (cable de existencia de citas + frescura del dosier) — `f953f8c7`
- **2026-06-26** · el vigía avisa a {{TITULAR}} con anti-spam 12h + fix ROOT/REPO en worktree — `a0ec93fa`
- **2026-06-26** · 4 modos + auto-detección, agente `contacto` + radar {{CONTACTO}} — `b5e0c055`
- **2026-06-26** · El Conserje (reservas + firma) integrado a casa base — `33280703`
