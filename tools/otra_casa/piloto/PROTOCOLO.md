# Piloto de aceptación de Grok como revisor de otra casa · PROTOCOLO

**Escrito y fechado el 10-oct-2026, antes de correr nada.** No se ha enviado ningún texto a xAI ni se ha hecho ningún pase cuando esto se escribe. Lo que sigue (definiciones, umbral, orden) no se cambia después de ver resultados. Si hay que cambiarlo, es un protocolo nuevo con fecha nueva y una corrida nueva, y se dice.

**Revisado el 10-oct-2026 antes de correr nada**, tras la segunda y la tercera ronda de revisión de la boca. En el estado de casa base no había ninguna corrida abierta ni ningún manifiesto. El umbral, las definiciones y el orden de los pasos son los mismos. Lo que cambió está en el apartado 11.

Plan del que sale: «Vía de Grok como revisor de otra casa» (10-oct-2026), fase 1.

## 1. Objetivo

Decidir con un criterio fijado de antemano si merece la pena pasar un texto abstracto a Grok después de que Claude lo haya revisado. La pregunta es una sola: ¿Grok caza errores de razonamiento estadístico que Claude no caza, sin inventarse errores donde no los hay?

## 2. Qué mide y qué no

| Mide | No mide |
|---|---|
| Si Grok detecta y clasifica un error sembrado en un análisis estadístico corto | Qué modelo razona mejor en general |
| Cuántas veces ve un error en un análisis limpio | Cómo se porta con un texto largo, con varios errores o con errores sutiles |
| Cuánto añade a lo que ya caza Claude, y al revés | Nada del caso: el corpus es de dominio neutro |
| Si la boca y la API funcionan de verdad (la sonda) | Si lo que xAI declara sobre retención y uso de datos es cierto |

Validez externa, dicha sin rodeos: son 30 problemas cortos (unos 1.200 caracteres), de dominio neutro, con errores de manual y una sola instrucción. El primer uso real será un texto más largo y con fallos más finos. Por eso **pasar el piloto es condición necesaria y no suficiente**: aunque salga USAR, lo que diga Grok sigue siendo una pista sin cotejar, y nada suyo entra en un documento sin comprobarlo.

Con 20 problemas con error y 10 limpios las cifras son pequeñas. Una diferencia de dos o tres aciertos entra dentro del azar. El umbral lo asume: es una regla de decisión acordada antes, no una medida fina.

## 3. El corpus

| Pieza | Qué es | Quién la hizo |
|---|---|---|
| `problemas/p01.txt` a `p30.txt` | 30 análisis estadísticos sintéticos: 20 con un error sembrado de ocho clases y 10 limpios | Un agente que no evalúa |
| `instruccion.txt` | La instrucción, igual para los dos modelos. Pide solo un JSON con `hay_error`, `clase` y `explicacion` | El mismo agente |
| `_clave/clave.json` | La clave. No se envía y no la ve quien evalúa | El mismo agente |
| `sonda.txt` | Texto desechable para la primera llamada real. No es del corpus y no puntúa | El constructor del piloto |

Un verificador independiente resolvió los 30 a ciegas, coincidió con la clave en 30 de 30 y recalculó las cifras (dato del orquestador; quien escribe este protocolo no lo ha repetido).

La instrucción que reciben los dos modelos es el contenido de `instruccion.txt` sin sus saltos de línea finales. La entrada exacta de cada petición es instrucción + `\n\n---\n\n` + el fichero del problema, tal cual. La imprime `corre_piloto.py entrada --problema pNN`.

## 4. Condiciones previas, que son de {{TITULAR}}

Las exige la auditoría de la boca (`tools/otra_casa/auditorias.json`, la de casa base). Confirmar una condición no es poner un booleano: cada una lleva su constancia, con la fecha (el día de la auditoría o después) y lo que {{TITULAR}} vio o decidió, en el vocabulario cerrado que dice el texto de la condición. Si a una constancia le falta algo, la boca no envía. El texto completo de cada condición está en la auditoría; esto es el resumen.

| Condición | Qué comprueba o decide | Qué anota en su constancia |
|---|---|---|
| c1 | En console.x.ai, que la clave `btp-grok-api` es de un equipo suyo y que ese equipo paga por uso (créditos prepago) | `cuando`, `que_ve`, `titular_cuenta` (`personal` o `asociacion`), `captura` |
| c2 | Que no hay ningún ajuste de compartir datos encendido ni ningún consentimiento de uso de sus datos para mejorar el producto | `compartir_datos` (`apagado` o `no-visible`), `que_ve`, `captura`, `cuando`; con `no-visible`, además `acepta_si_no_visible` |
| c3 | La retención: acepta los 30 días que declara la FAQ de xAI, o activa ella la retención cero en el equipo | `decision` (`acepta-30-dias` o `retencion-cero`), `cuando` |
| c4 | Aceptación expresa de tres cosas que el sistema no puede cambiar o no ha podido leer: que el texto sale por el punto de acceso global (sin región garantizada), que la contraparte figura como «SpaceXAI», y que nadie del sistema ha leído los términos ni el contrato de tratamiento de datos | `acepta_punto_global`, `acepta_contraparte` y `acepta_terminos_sin_leer` a true, `cuando` |

c4 no estaba en el plan que {{TITULAR}} aprobó. La añadió la revisión legal de la primera ronda para que la decisión sea informada. Si no la acepta, la boca no abre; quitar esa compuerta también es decisión suya, y es un cambio de código con auditoría nueva.

Además, antes del paso 1:

1. La rama de la boca, fusionada a casa base, con el OK de {{TITULAR}}. Va lo primero: hasta entonces la auditoría ni existe en casa base, y la boca real solo envía si el código que corre es el de casa base.
2. {{TITULAR}} rellena sus constancias y pone `condiciones_cumplidas` a true en esa auditoría, la de casa base. La confirma ella, nunca el código. Después casa base queda commiteada.
3. Saldo prepago en el equipo de al menos la cota del apartado 8 (25 dólares). Con el saldo agotado xAI rechaza las peticiones.
4. Su OK a correr el piloto. Es la primera salida real a xAI.

### Qué sale hacia xAI y qué queda allí

| | |
|---|---|
| Qué sale | 31 textos sintéticos: la sonda y los 30 problemas, de dominio neutro y sin nada del caso, cada uno con la instrucción. Nada más: sin herramientas, sin ficheros y sin estado |
| Qué queda en xAI | Esos 31 textos y sus respuestas, 30 días según su FAQ de seguridad («for auditing purposes in the event of suspected abuse or misuse»). Si {{TITULAR}} activa la retención cero en el equipo, lo que diga esa modalidad; la boca exige entonces que la cabecera de cada respuesta lo confirme. La petición lleva `store: false` |
| Quién lo ve allí | La FAQ no lo dice |
| Por dónde | `api.x.ai`, el punto de acceso global. xAI no garantiza en qué región se procesa y no lista ninguno de la UE |
| A quién va ligado | A la cuenta dueña de la clave: toda petición identifica a su titular |
| Lo que nadie del sistema ha leído | El contrato de tratamiento de datos y los términos (dan error 403 a las herramientas) y el programa de compartir datos a cambio de crédito |

Fuente: las páginas de docs.x.ai que cita la auditoría, cada una con el sello de quién la abrió. Se abrieron el 10-oct-2026 con una herramienta que resume: no es copia literal.

## 5. Los pasos

La corrida lleva un identificador que elige quien la abre (letras, cifras, punto, guion; 40 como máximo), por ejemplo `piloto-20261012`. Es también la tanda de todos los envíos. Las órdenes van desde la raíz del repo; `CP` es `python3 tools/otra_casa/piloto/corre_piloto.py`.

| Paso | Qué | Quién | Orden |
|---|---|---|---|
| 0 | Comprobación previa: guardas de destino, la sonda y los 30 contra las guardas de contenido. Sin red, sin clave, sin sellar | Cualquiera | `CP previa` |
| 1 | **Sonda desechable**: la primera llamada real, con `sonda.txt`. Abre la corrida. Se mira el uso que devuelve (tokens) antes de seguir. Se lanza como dice «Cómo se lanza `grok`» | {{TITULAR}}, o el orquestador con su OK | `CP grok --corrida ID --enviar-de-verdad --solo-sonda` |
| 2 | **Pase de Claude, a ciegas y antes que el de Grok.** Un agente nuevo, que no ha visto `_clave/` ni respuestas de Grok a los problemas. Un problema por petición, sin herramientas, sin más contexto que la entrada exacta, un solo intento. La respuesta se guarda tal cual | Agente nuevo de Claude | `CP entrada --problema pNN` y `CP apunta-claude --corrida ID --problema pNN --modelo <id> respuesta.txt` |
| 3 | **Pase de Grok**: los 30, uno por petición, misma tanda. Para en la primera parada. Se lanza como dice «Cómo se lanza `grok`» | {{TITULAR}}, o el orquestador con su OK | `CP grok --corrida ID --enviar-de-verdad` |
| 4 | **Puntuación, una sola pasada** | Cualquiera | `python3 tools/otra_casa/piloto/puntua.py --corrida ID` |
| 5 | Archivo del resultado: la tabla, el veredicto y `puntuacion.json` | El orquestador | `python3 tools/archivar_nota.py …` |

### Cómo se lanza `grok` (pasos 1 y 3)

- **Desde casa base, con la rama fusionada.** Desde un worktree la boca cierra antes de enviar. El estado y la auditoría son siempre los de casa base. Y sin `PYTHONPATH`, `PYTHONHOME`, `PYTHONUSERBASE` ni `PYTHONSTARTUP` en el entorno: con cualquiera de ellas la boca también cierra. Lo que cerraría un envío desde donde se está lo enseña `python3 tools/otra_casa.py estado --destino razona:grok`, sin enviar nada.
- **Quién lo lanza: {{TITULAR}} en su terminal, o una sesión PRINCIPAL de Claude Code que lo deja en segundo plano, sigue abierta y espera a que termine antes de dar su respuesta. Nunca un subagente ni un flujo de agentes.** El proceso en segundo plano de un subagente muere cuando el subagente entrega su respuesta; el de una sesión principal vive mientras la sesión siga abierta, y muere si se cierra. Cada petición espera hasta 900 segundos y son 31 (peor caso, casi 8 horas): la sonda cabe en una sesión principal; los 30, mejor en la terminal de {{TITULAR}}. Un Bash de agente en primer plano corta a los 600 segundos. En cualquiera de esos cortes el proceso muere a media llamada y deja un envío sin desenlace.
- **De día, fuera del silencio nocturno.** El primer envío con una auditoría (aquí, la sonda) espera a que a {{TITULAR}} le haya llegado su aviso. La boca lo manda como urgente (se salta el cupo diario de avisos) y solo sigue si consta entregado entero: sale una vez por auditoría mientras el registro de avisos lo guarde; si no lo guarda, la boca cierra y cada lanzamiento manda otro. Dentro del silencio nocturno no avisa y cierra: se relanza de día. Si el canal de avisos falla, cierra igual y se relanza.
- **Ese aviso llega con una etiqueta que invita a descartarlo, y es real.** El canal de avisos le antepone a todo lo que no sale del lazo una línea que dice «esto NO viene del lazo; probablemente un diagnóstico manual». La boca se lanza desde una terminal o una sesión, así que su aviso la lleva. Si {{TITULAR}} no confirmó ella la auditoría, ese aviso es la señal para parar con HALT.
- **La sonda va sola** (`--solo-sonda`) y su uso se mira antes de seguir.
- **Tras cualquier parada, reanudar lo teclea {{TITULAR}}** en su terminal: `python3 tools/otra_casa.py reanudar --destino razona:grok --motivo "…"`. Ni el lanzador ni un agente reanudan.

Reglas que el código impone y no dependen de que alguien se acuerde:

- `grok` no envía ningún problema si en la corrida no están ya la sonda guardada y las 30 respuestas de Claude. La sonda sola sí puede ir antes: es el paso 1.
- `apunta-claude` se niega si en la corrida ya hay algo de Grok sobre el corpus (la sonda no cuenta), no sobrescribe una respuesta apuntada y se niega si el modelo no es el de las respuestas ya apuntadas. El puntuador se niega si el pase de Claude mezcla modelos.
- Al abrir la corrida se guarda la huella de este protocolo, de la instrucción y de los 30 problemas. Si alguno cambia después, ni se envía ni se puntúa. La sonda queda fuera a propósito: si se quema, se cambia su texto sin tocar la corrida.
- El lanzador no lee `_clave/`. El puntuador sí, y solo él.
- El lanzador no reanuda ni reintenta. Al relanzar no se reenvía lo que ya tiene respuesta guardada.
- La puntuación se hace una vez. Con `puntuacion.json` escrito, el puntuador se niega.

Lo que el código no puede imponer y depende de seguir este protocolo: que el pase de Claude sea de verdad a ciegas; que alguien mire el uso de la sonda antes de lanzar los 30; y que la sonda vaya antes que el pase de Claude (al revés no se contamina el resultado, porque el manifiesto fija la instrucción, pero si la sonda obliga a tocarla el pase de Claude ya hecho se pierde).

El modelo de Claude es el de mayor capacidad disponible ese día, el mismo para los 30, y su identificador queda en cada registro. Grok va con lo que fija la boca: `grok-4.7`, esfuerzo `xhigh`, sin herramientas. Las condiciones de los dos pases no son idénticas (no hay forma de igualar el esfuerzo entre casas) y se declara.

### Por qué hay sonda

La boca bloquea para siempre el texto que reciba una respuesta inaceptable: un HTTP 400, 413 o 422, o un 200 fuera de la lista blanca. La forma real de la API no se ha probado nunca: si el cuerpo de la petición tuviera algo que xAI no acepta, el primer problema del corpus quedaría quemado por un fallo nuestro, no de Grok. Por eso la primera llamada va con un texto que no importa perder. Y va antes que el pase de Claude: si la sonda obligara a tocar la instrucción, las 30 respuestas de Claude ya hechas con la instrucción vieja no valdrían.

| Qué le pasa a la sonda | Qué significa | Qué se hace |
|---|---|---|
| Respuesta buena | La boca y la API se entienden | Se mira el uso y se sigue con el paso 2 |
| Parada que bloquea el texto (400, 413, 422, respuesta fuera de la lista blanca) | Fallo de forma de la boca, o negativa a un texto inocuo | No se envía ningún problema. No puntúa. Se arregla la boca y hace falta una sonda con texto nuevo |
| Parada que no bloquea y no dice nada del texto (red, tiempo agotado, 401, 402, 403, 404, 408, 429, 5xx o cualquier otro status) | Clave, permisos, saldo, límite de peticiones o servicio caído | No se envía ningún problema. Se arregla la causa, {{TITULAR}} reanuda y se relanza |
| Respuesta buena que contradice una condición auditada: `store` distinto de false, o retención cero anotada y la cabecera sin «true» | La configuración que {{TITULAR}} confirmó no es la que xAI aplica. Ese texto ya salió | Se para el piloto. Reanudar y relanzar sin más reenvía el mismo texto bajo la misma condición rota, y se paga otra vez. Primero se corrige la configuración en la consola o se vuelve a auditar (la auditoría cambia y avisa de nuevo); después {{TITULAR}} reanuda. La respuesta queda guardada como pista, no como registro de la corrida |
| La boca cierra antes de enviar (silencio nocturno, canal de avisos caído, auditoría sin confirmar, HALT) | No salió nada | Se arregla la causa y se relanza. No hace falta reanudar |
| Envío sin desenlace (el proceso murió a media llamada) | La petición pudo salir y cobrarse, y no hay respuesta | La boca no envía nada más hasta que {{TITULAR}} reanude. Tras reanudar, ese texto se envía otra vez |

Las cuatro últimas filas valen igual para cualquiera de los 30 problemas. La segunda no: una parada que bloquea un problema del corpus termina el pase (apartado 7). Antes de puntuar tras una parada así se lee su motivo en el registro de la parada: si habla del saldo o de la cuenta y no del texto (xAI no documenta con qué status rechaza por saldo agotado), se dice en el archivo del resultado. El veredicto pre-registrado no cambia por eso.

## 6. Definiciones cerradas

Se puntúa la respuesta de cada modelo a cada problema.

| Término | Definición |
|---|---|
| Respuesta válida | El texto, quitados los espacios de los extremos, es un objeto JSON, o es un único bloque de código (tres acentos graves, con o sin la palabra `json`) que contiene solo ese objeto. El objeto tiene exactamente las claves `hay_error`, `clase` y `explicacion`, sin repetir ninguna; `hay_error` es `true` o `false`; `clase` es una cadena o `null`; `explicacion` es una cadena |
| JSON roto | Cualquier otra cosa: texto antes o después, más de un bloque, claves de más o de menos, claves repetidas, `hay_error` que no sea un booleano (un 1 o un "true" no valen) |
| Acierto ESTRICTO | En un problema con error: respuesta válida, `hay_error` `true` y `clase` igual a la sembrada |
| Acierto LAXO | En un problema con error: respuesta válida y `hay_error` `true`. Se informa, no decide |
| Falsa alarma | En un problema limpio: `hay_error` `true`, o JSON roto |
| Incremental de Grok | Número de problemas con error en los que Grok tiene acierto estricto y Claude no tiene acierto estricto |
| Incremental de Claude | Lo mismo al revés. Se informa, no decide |

Casos de borde, fijados ya:

- Un JSON roto cuenta como fallo del ítem: en un problema con error no es acierto, ni estricto ni laxo; en un problema limpio es falsa alarma. Además se informa aparte.
- `hay_error` es lo que manda. Con `hay_error` `false` y una `clase` puesta, la respuesta cuenta como «sin error» y se anota la incoherencia.
- Una `clase` que no es uno de los ocho identificadores no rompe el JSON, pero nunca da acierto estricto.
- La longitud de `explicacion` no se juzga, y su contenido tampoco: nadie lee las explicaciones para decidir el veredicto.
- «Que Claude no caza» se lee en estricto en los dos lados. El incremental en laxo se imprime también, como dato.
- Una negativa escrita como texto normal no se puede distinguir de una respuesta cualquiera: sale como JSON roto.

## 7. Umbral pre-registrado

Propuesta de la lente escéptica del plan. Los cortes, en una línea que el test del piloto compara con el código:

`UMBRAL estricto>=12 falsas<=2 incremental>=3 | NO-USAR incremental<=1 falsas>=4 | INVALIDO rotos>3`

El veredicto se decide en este orden, y gana el primero que se cumple:

| Orden | Condición | Veredicto |
|---|---|---|
| 1 | Hay una negativa o un corte del proveedor en algún problema del corpus: una parada que bloquea el texto | **NO USAR** |
| 2 | Algún modelo tiene más de 3 JSON rotos de 30 (4 o más): su pase no vale y no hay comparación | **INVÁLIDO** |
| 3 | Incremental de Grok de 1 o menos, o 4 o más falsas alarmas de Grok de 10 | **NO USAR** |
| 4 | Grok acierta en estricto 12 o más de 20, y da 2 o menos falsas alarmas de 10, y su incremental es 3 o más | **USAR** |
| 5 | Todo lo demás | **ZONA GRIS** |

- **USAR**: Grok entra como revisor de otra casa de textos abstractos, siempre después de Claude y siempre como pista sin cotejar.
- **NO USAR**: no se adopta.
- **ZONA GRIS**: no se adopta, y decide {{TITULAR}} con la tabla delante.
- **INVÁLIDO**: no se adopta. Repetirlo pide un protocolo nuevo, fechado.

Una parada que bloquea el texto es un HTTP 400, 413 o 422, o un 200 fuera de la lista blanca de la respuesta buena. Con una de esas en un problema del corpus, el pase de Grok se da por terminado ahí y el puntuador da NO USAR con lo que haya.

Lo que interrumpe el pase sin puntuar: un fallo de red, un tiempo agotado, cualquier otro HTTP (401, 402, 403, 404, 405, 408, 415, 429, 5xx…), una respuesta buena que contradice una condición auditada, un envío sin desenlace, o que la boca cierre antes de enviar. Nada de eso bloquea el texto ni es un veredicto: no hay veredicto hasta que el pase esté completo. En particular, una parada por facturación (el saldo agotado) no es un veredicto.

Una parada que bloquea a la **sonda** no es un veredicto: el corpus no llegó a salir.

## 8. Coste

Precios de la página de modelos de xAI (docs.x.ai/developers/models, abierta el 10-oct-2026 con una herramienta que resume, no es copia literal): `grok-4.7` por debajo de 200.000 tokens de entrada, 2 dólares por millón de tokens de entrada y 6 por millón de salida. Por encima de 200.000 el precio se dobla; aquí no aplica, cada petición ronda los 3.000 caracteres.

| Parte | Cálculo | Cota |
|---|---|---|
| Entrada | Bytes de las 31 entradas exactas, contando un token por byte, a 2 dólares por millón | Menos de 0,25 dólares |
| Salida | 31 peticiones por 128.000 tokens (el tope de salida por defecto según la referencia de la API) a 6 dólares por millón | 23,81 dólares |
| **Total** | | **Menos de 25 dólares** |

`CP previa` imprime la cota calculada con los bytes reales.

**Esta cifra no es la del plan.** El plan que {{TITULAR}} aprobó decía «menos de 5 dólares (inferido)»: era una estimación del gasto probable. Esto es una cota del peor caso (31 peticiones agotando el tope de salida) y es cinco veces mayor. El gasto probable sigue sin medir.

Esta cota vale solo si los tokens de razonamiento se cobran como salida y caben dentro de los 128.000. La página de modelos no dice ninguna de las dos cosas, así que **no está verificado**. Y el **coste real con esfuerzo `xhigh` no está medido**: lo razonable es que quede muy por debajo de la cota, pero es una suposición. Para eso sirve mirar el uso de la sonda antes de lanzar los 30.

Antes del paso 1 hace falta saldo prepago por esa cota. La página de facturación de xAI dice que, sin facturación mensual (desactivada por defecto), las peticiones se rechazan cuando se agota el saldo. No dice con qué código HTTP, y su página de errores tampoco. La boca trata cualquier status que no sea 400, 413 o 422 como una parada que no bloquea el texto: si el saldo se agota a media tanda, el pase se interrumpe, se recarga, {{TITULAR}} reanuda y se sigue.

Tres formas de pagar de más que el código no evita: si la conexión se corta por tiempo (15 minutos por petición) después de que xAI haya procesado la petición, se cobra y no hay respuesta; si la respuesta llega pero no se puede guardar en disco, el lanzador para, y al relanzar ese problema se envía otra vez; y tras una parada por condición auditada incumplida, ese texto se envía otra vez cuando la condición esté arreglada.

El tiempo tampoco está medido. Con 15 minutos de tope por petición, el peor caso son casi 8 horas.

## 9. Lo que este piloto no decide

- Qué modelo razona mejor en general.
- Si Grok sirve para un texto con contexto clínico. Eso es la fase 2, que no está auditada ni construida.
- Si la retención y el uso de datos que declara xAI son los reales.
- Si conviene usar otro esfuerzo u otro modelo de xAI.

## 10. Dónde queda cada cosa

En el estado de casa base, `otra_casa/piloto/<corrida>/`:

| Fichero | Contenido |
|---|---|
| `manifiesto.json` | Huellas del protocolo, la instrucción y los problemas al abrir la corrida |
| `claude/pNN.json` | Respuesta de Claude al problema NN |
| `grok/sonda.json` | Respuesta de Grok a la sonda |
| `grok/pNN.json` | Respuesta de Grok al problema NN |
| `grok/pNN.parada-*.json` | Uno por cada envío de ese problema (o de la sonda) que no acabó en respuesta guardada: el tipo (`parada`, `condicion` si la respuesta contradijo una condición auditada, `puerta` si la boca cerró antes de enviar), el motivo, el status, si bloquea el texto y, si la respuesta llegó, dónde quedó su pista |
| `puntuacion.json` | La puntuación y el veredicto. Se escribe una vez |

Cada registro de respuesta lleva: corrida, problema, modelo, el texto de la respuesta tal cual, la huella de la entrada exacta, la huella de la petición (solo Grok), la ruta de la pista guardada por la boca (solo Grok), el modelo que respondió, el uso y la hora.

Fuera de la carpeta de la corrida, también en el estado de casa base:

| Dónde | Qué |
|---|---|
| `otra_casa/respuestas/*.md` | La pista que guarda la boca por cada respuesta recibida, encabezada como «pista sin cotejar». También la de una respuesta que acabó en parada por condición incumplida |
| La cadena del borde | Un sello por envío, por respuesta, por parada y por el aviso del primer envío: hashes, uso, modelo devuelto, el `store` y la cabecera de retención cero que devuelve xAI. Nunca el texto ni la clave |
| El registro de gasto | Una línea por petición, con el coste que declara xAI en la respuesta |
| `salida/enviados-*` | El aviso del primer envío, tal como le llegó a {{TITULAR}} |

La respuesta de Grok es un dato no confiable. El lanzador la guarda y no hace nada más con ella; el puntuador solo la lee como JSON de tres claves.

## 11. Qué cambió en la revisión del 10-oct-2026 (antes de correr nada)

No cambian el umbral (apartado 7), las definiciones (apartado 6) ni el orden de los pasos (apartado 5).

| Apartado | Qué cambió | Por qué |
|---|---|---|
| 4 | Las condiciones de {{TITULAR}} son c1 a c4, con lo que anota en cada constancia; c4 marcada como añadida por la revisión. Saldo prepago, rama fusionada. Tabla nueva de qué sale y qué queda en xAI | La versión anterior listaba tres y la boca exige cuatro, y no juntaba en un sitio lo que hace falta saber para dar el OK |
| 5 | Apartado nuevo «Cómo se lanza `grok`»: desde casa base, en segundo plano, de día, la sonda sola. El lanzador ya no envía problemas sin la sonda guardada, y el pase de Claude tiene que ser de un solo modelo | Seguido al pie de la letra, el protocolo anterior dejaba un envío sin desenlace y permitía lanzar la sonda y los 30 de un tirón |
| 5 | La tabla de la sonda tiene cuatro filas nuevas: la parada por condición auditada incumplida, la boca que cierra antes de enviar, el envío sin desenlace y los status que no dicen nada del texto | La boca ya paraba por ellas y el protocolo mandaba «reanudar y relanzar» en todas |
| 5 y 7 | Qué bloquea un texto: solo un HTTP 400, 413 o 422 y un 200 fuera de la lista blanca. Antes bloqueaba cualquier status por debajo de 500 que no fuera 401, 403, 404, 405, 415 o 429 | Un 402 (saldo agotado) o un 408 habría quemado un problema del corpus y dado NO USAR sin que Grok dijera nada del texto. La fila 1 del veredicto se lee igual; lo que cambia es que un status que no habla del contenido ya no cuenta como corte |
| 6 | El texto no cambia. Cambia el puntuador: el bloque de código «con o sin la palabra `json`» vale ahora con `json` en mayúsculas o minúsculas, con espacios alrededor y con saltos de línea LF o CRLF; otra palabra en su lugar sigue siendo un JSON roto | El puntuador era más estricto que la definición escrita: un bloque abierto con `JSON` o con un espacio antes de `json` salía como JSON roto |
| 8 | Se dice que la cota (25 dólares) no es la cifra del plan (5 dólares) y que hace falta saldo previo | El salto no estaba avisado |
| 10 | Se nombran las pistas, los sellos, el gasto y el aviso, que quedan fuera de la carpeta de la corrida | Faltaban |
| 4 (tercera ronda) | El orden de «antes del paso 1»: primero la fusión a casa base, después confirmar la auditoría y commitear casa base | La auditoría no existe en casa base hasta fusionar: confirmar antes no era posible |
| 5 (tercera ronda) | Quién lanza `grok`: {{TITULAR}} en su terminal o una sesión principal que espera; nunca un subagente ni un flujo. Y la salvedad de que el aviso urgente se repite por lanzamiento si el registro de avisos no lo guarda | «En segundo plano» a secas incluía a un subagente, cuyo proceso muere al responder y deja un envío sin desenlace |
| 5 (tercera ronda) | Antes de puntuar tras una parada que bloquea, se lee su motivo y se anota si era de saldo o de cuenta | xAI no documenta con qué status rechaza por saldo agotado; si fuera un 400, el veredicto saldría igual y hay que poder leerlo con ese dato delante |
