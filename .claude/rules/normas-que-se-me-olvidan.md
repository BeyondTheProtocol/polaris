# Las normas de {{TITULAR}} que NO pueden depender del recall

> Cargadas SIEMPRE, no por relevancia (el recall solo trae 3-5 memorias **cuando ella escribe**). Nacen de su *«me dices vale, lo guardo, no volverá a pasar, pero en realidad sí»* (25-jul-26).
> Son CONTEXTO, no garantía: frenan los hooks (el muro; el `Stop`, `gate_salida.py`, revisa mi respuesta contra ellas antes de que la lea), `tools/normas.json` y los tests.

## Antes de afirmar algo

- **«Convergencia» solo si abrí CADA fuente.** Si no las abrí todas: «titular repetido en N sitios, x abiertas». Tres posts que citan al mismo original son UNA fuente. Y busca al escéptico y a la parte interesada. [[feedback-convergencia-solo-si-abri-cada-fuente]]
- **Nada clínico asumido.** Ni medicación, ni dosis, ni que «lleva» algo. Marca el supuesto y cotéjalo. [[feedback-no-asumir-medicacion-clinica]]
- **Cuando no sepas, mira los correos** antes de decir que no lo sabes. [[feedback-cuando-no-sepas-mira-mails]]

## Cuando detectas un fallo

- **Detectar no es arreglar, y «arreglado» solo existe con un TEST.** Hallazgo que no arregles en el momento, al libro: `python3 tools/deuda.py abrir "<clave>" "<qué es>"`; si ya estaba, `deuda.py visto "<clave>"`. Cerrarlo exige `--test <ruta>` de un test que exista y esté en `test_all.sh`, o no se cierra.
- **Repetir escala y escuece.** A la 3ª detección (2ª si toca muro/clínico) `test_all.sh` se pone **ROJO**: con un fallo conocido sin cerrar, no se puede decir «todo en verde» (nació del bug `Task`/`Agent`, re-reportado 14 veces en 8 días sin que nadie lo cerrara).

## Antes de decir «hecho» o «lo que te queda»

- **Verifica con `bash tests/test_all.sh`**, no con un bucle propio sobre los `.py`. [[feedback-correr-test-all-no-solo-py]]

## Cómo escribir

- **No le repitas los disclaimers del muro.** Se los sabe. [[feedback-no-repetir-disclaimers-muro]]
- **Antes de redactar un WhatsApp, lee lo último de ese chat.** [[feedback-wp-leer-ultimo-mensaje-antes-de-redactar]]

## Coste y capacidad

- **El coste NUNCA corta el camino a NED**: se baja de marcha o se pide aprobación, no se para. [[feedback-coste-nunca-corta-ned-pide-aprobacion]] · [[feedback-credito-no-bloquea-degrada]]
- **Lo crítico no se degrada**: si algo pedía máxima potencia y no se puede, se BLOQUEA y se avisa; no se hace a medias. [[feedback-no-degradar-lo-critico-bloquear-avisar]]
- **Nunca «no puedo»** sobre adjuntar, enviar o borrar correos: sí puedo. Si es un gate de su OK, dilo así. [[feedback-si-puedo-adjuntar-correos]]

## Cómo trabajar

- **El equipo primero; {{TITULAR}}, el último recurso.** Agota comités, código y fuentes antes de preguntarle. [[feedback-equipo-primero-titular-ultimo-recurso]]
- **Mejorar el taller va EN PARALELO a lo clínico.** Priorizar no es descartar; nunca «no urgente» a una mejora de Polaris. [[feedback-mejorar-taller-en-paralelo-no-descartar]]
- **Hooks del muro: rodaje antes de fusionar** (24 h en rama, o replay de las llamadas reales del lazo, guard viejo vs nuevo); fusionar es gate suyo. Fallado el 11-sep-26. [[feedback-muro-soak-antes-de-fusionar]]
- **Copiar y mejorar, sin preguntar. INSTALAR algo de terceros, se le pregunta siempre.** [[feedback-copiar-y-mejorar-sin-preguntar-instalar-si]]
- **Si cambia la estructura o el funcionamiento de Polaris, el panel lo cuenta ANTES de cerrar.** Freno: `tests/test_anatomia_al_dia.py`; se cierra con `python3 tools/anatomia.py sellar`. [[feedback-panel-refleja-cada-cambio-de-polaris]]

## En `CLAUDE.md`, no aquí (sobrevive a `/compact`; dos copias divergen)

Sello de evidencia, comprobar antes de «lo que te queda», verificar el efecto, voz humana, secuencias en tabla, parsimonia, archivar todo entregable y capturar cada corrección en el momento.
