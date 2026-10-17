# Plan v1 (sustituido por docs/plan-web-al-dia.md): la cronología propone sus entradas sola

17-oct-2026 · BORRADOR para el OK de {{TITULAR}} · rama `claude/keen-haslett-82bd5f`

## TL;DR

- **Hoy la web no se actualiza sola.** [verificado: lectura de `tools/web_novedad.py` + `git show origin/main:content/es/timeline.yml`]. La última entrada pública es del **7 oct** (TROPION-Breast06). Los PR #279–#281 del single-cell (16-17 oct) crearon la página `/celula-a-celula`, pero en la cronología no hay ninguna entrada.
- **La mitad del circuito ya existe y funciona:** PR → Telegram con el texto sellado → su «sí» citando el aviso → fusión y despliegue comprobado (`web_lote.pedir_ok` → `salida._draft(sellado)` → `bot_telegram` → `web_publicar.fusionar_aprobado`). **No se toca.**
- **Lo que falta son dos piezas:** (1) un **detector** de hitos, determinista y sin IA, que apunta cada hito una sola vez; (2) un **redactor único diario** que agrupa hasta 3 hitos, escribe las entradas en su voz, abre **un** PR **sin fusionar**, espera al CI y encarga el `pedir-ok`.
- **Garantías:** solo se AÑADE · `highlight: false` · `web_lint` antes del Telegram (lo que no pasa no se le enseña) · **nunca se publica sin su «sí»** · **1 PR de cronología abierto a la vez** · 1 propuesta por hito y 1 recordatorio como mucho.
- **Arranca en sombra:** durante unos días apunta lo que habría propuesto y no le escribe. El modo activo lo enciende ella.

## Qué hay hoy (verificado leyendo el código en esta sesión)

| Pieza | Qué hace | ¿Sirve? |
|---|---|---|
| `web_novedad.publicar(texto)` | worktree desde `origin/main`, inserta la entrada ordenada por fecha, pasa `web_lint`, abre el PR `[auto] cronología: …`; **sin permiso de su sesión no fusiona** | Sí. Es el que abre el PR |
| `web_guardian.py` (launchd 7:35, **en sombra**: no existe `modo.json`) | detecta una cronología parada >14 d y **cuenta** los informes llegados después de la última entrada (`informes-sin-reflejar`, peldaño 3); atiende los encargos `pedir_ok/` | Sí. Es el proceso que puede escribirle (lee el Llavero) |
| `web_lote.pedir_ok(PR)` | si el CI está en verde y el PR cambia texto (`content/` está en `TEXTO_RUTAS`), le manda el antes/después sellado con PR, sha y huella | Sí |
| `bot_telegram` | «sí» citando el aviso → publica; otra respuesta citando el aviso → `encargar_cambios` (se apunta en el buzón de Vega; hoy nadie lo rehace solo) | Sí. El bucle de cambios está a medias |
| `web_lint.revisar` | para clínico/HGVS/marcadores/dosis/terceros/marca/voz | **Con un hueco:** deja pasar «NY-BR-1 en el 58 % de las células tumorales» [verificado: llamada a `revisar()`]. Abierta la deuda `web-lint-no-caza-diana-con-porcentaje` |

## Diseño

### Pieza 1: detector `tools/cronologia_hitos.py` (determinista, $0)

Corre dentro de la pasada diaria del guardián, como una señal más. Por cada fuente produce hitos con **identidad estable**:

| Fuente | Identidad del hito | Qué guarda (N1, nunca crudo) |
|---|---|---|
| Informes nuevos en la bóveda (`historial/_eventos.jsonl`) | sha del fichero | fecha + carpeta (tipo de informe), sin la descripción |
| Análisis archivados (notas en `00_FUENTE-DE-VERDAD` con `--hito-web`) | ruta de la nota | título + fecha |
| Ciclos y cambios de línea | fecha + tipo | ver UNKNOWN 2 (todavía no hay fuente fiable) |
| PR web fusionados (`gh pr list --state merged`) | nº de PR | título. **Excluye los `[auto]`** (si no, bucle) y los `fix/chore/a11y` sin página nueva |

- **Dedup:** `state/cronologia_hitos/vistos.json` guarda la identidad → estado (`encolado` → `propuesto` → `publicado` / `recordado` / `descartado`). El detector escribe `encolado` **al detectar**, con la identidad como clave idempotente. Así un hito que espera no se vuelve a encolar en cada pasada.
- **Identidad robusta:** para los informes, fecha del informe + carpeta + tipo, no solo el sha (una re-exportación o un re-OCR cambia el sha). Los PR automáticos se excluyen **por la rama `auto/`**, no por el título.
- **Antispam** (`feedback-todo-aviso-a-titular-necesita-anti-spam`): un hito se propone 1 vez. Si no responde, 1 recordatorio a los 3 días y luego `descartado` en silencio (queda en el parte semanal, no en un Telegram). **Tope: 1 Telegram de cronología al día.**
- La señal `informes-sin-reflejar` del guardián pasa a leer `vistos.json`: un informe ya propuesto no se vuelve a contar. Así no hay dos avisos del mismo hecho.

### Pieza 2: redactor único diario (cola, cerebro de confianza)

Cambio tras la revisión de arquitectura: en vez de un trabajo por hito, hay **un solo consumidor** que corre una vez al día, justo después de la pasada del guardián. Con un solo consumidor no hace falta comprobar si hay un PR abierto antes de crear otro (esa comprobación tenía una carrera), y el dedup vive en un único fichero.

1. Coge los hitos en estado `encolado` de `vistos.json`, **3 como máximo** (`USOS_MAX`). Lo que no cabe espera al día siguiente sin avisar.
2. Si `vistos.json` apunta a un PR de cronología vivo, no abre otro: añade al mismo PR o espera. Va con `_lock.lock("cronologia-pr")`, sin preguntar a GitHub.
3. Escribe cada entrada en su voz, usando solo **lo ya publicado o el hito en N1**. Lee en local o con Claude (`enruta.py`, fail-closed); nada a un modelo externo. Título corto, 2-3 líneas, `#tag` existente, `@fecha` del hecho.
4. **Filtro clínico (matiz de Vega):** una entrada que cite un informe o una diana **no lleva cifras ni marcadores**, solo el hecho («llegó el informe de AP del hígado», «primeros resultados célula a célula, en su página»). La corrección clínica la firma {{CONTACTO}}, no el «sí» de {{TITULAR}}. Si la entrada necesita la cifra, pasa por `comite-medico` antes del Telegram.
5. Pasa `web_lint`. Si falla, reescribe una vez sin la cifra o el nombre. Si vuelve a fallar, no se le enseña: queda una línea en el parte, sin el texto.
6. `web_novedad.proponer()` es una función nueva que **por código** nunca llama a `lo_pide_titular` ni a `fusionar`. No es un parámetro que se pueda olvidar. La rama es `auto/cronologia-…`.
7. **Espera al CI** con `WP.esperar_ci` y solo después hace `encargar_pedir_ok(PR)` + `--kick`. Si no, el Telegram se iba a la pasada del día siguiente: el CI tarda 2-3,5 min y el daemon solo corre a las 7:35.

### Avisos: cómo encaja con los de Vega

- Cuenta dentro del **tope general de 3 avisos no urgentes al día** de `salida.py`. Si no cabe, va al parte.
- Respeta el **silencio de 23 a 8 h**: nunca es 🔴, así que espera a las 8:00.
- Las vueltas de cambios **no cuentan** contra el tope, porque responden a algo que inició ella.
- `origen: "cronologia-hitos"` propio, para que sus 👍/👎 se midan y el aviso se baje si acumula 👎.
- Cada propuesta lleva una línea de **qué acerca a NED** (visibilidad, contactos). Prioridad baja: no compite con lo datado.

### Lo que le llega a {{TITULAR}} (un Telegram)

> 🌐 Propuesta para la cronología (17 oct 2026 · Molecular)
> **«Primeros resultados del análisis célula a célula»**
> <descripción tal cual>
> Responde «sí» a este mensaje y se publica. Si quieres cambiar algo, responde con el cambio.

Es el render que ya hace `pedir_ok`, con una cabecera de una línea. **Su «sí» es la firma**: la huella sellada impide que salga un texto distinto al que leyó.

### Bucle de cambios (cierra lo que hoy está a medias)

Si responde con cambios, `encargar_cambios` ya los apunta. El trabajo nuevo los recoge, rehace la entrada **en el mismo PR** (nuevo commit, nueva huella) y vuelve a pedir el `pedir-ok`. Máximo 3 vueltas; a la cuarta, pasa al parte. Al empujar el commit nuevo **se retira el borrador de Telegram anterior**: un «sí» al texto viejo lo rechazaría el sha, pero ella lo vería como un fallo sin explicación. Si un PR humano toca `timeline.yml` y deja inválida la huella, se le dice en una línea («la cronología cambió por otro lado, te reenvío el texto»).

## Lo que NO cambia

- Ninguna entrada existente se modifica (lo vigila `test_web_novedad`).
- `science.yml`, `press.yml` y `team.yml` siguen sin ruta de escritura.
- El inglés no se traduce (ver UNKNOWN 3).
- Ningún tercero ni dato clínico sale sin su «sí». El lint es un freno previo y el «sí» es el definitivo.

## Tests (antes de cerrar)

1. Dedup: el mismo hito en dos pasadas seguidas y con un PR abierto → 1 trabajo, 1 PR y 1 propuesta. Tras el recordatorio, silencio.
2. Exclusión de los PR `[auto]` y de los mecánicos.
3. Con un PR de cronología vivo en `vistos.json`, el segundo hito no abre otro PR.
3b. `proponer()` espera al CI antes de encargar; con el CI en rojo no hay encargo.
3c. En el bucle de cambios, el commit nuevo retira el borrador viejo.
4. Si el lint falla dos veces, no hay encargo `pedir_ok` y queda una línea en el parte sin el texto.
5. `proponer()` nunca llama a `fusionar` aunque haya permiso abierto.
6. Sombra: no escribe en `pedir_ok/` ni en el buzón.
7. `highlight: false` y tag existente en todo lo que sale del redactor.
8. Test de regresión de la deuda del lint: «NY-BR-1 … 58 %» → `clinico`.

Todo entra en `tests/test_all.sh`.

## UNKNOWNS (lo que decide ella, o lo que no sé)

1. **¿Cuenta los datos de dianas con cifra en la cronología?** El lint hoy los deja pasar (hueco). Si lo cierro, el hito del single-cell solo podrá decir «han salido dianas candidatas, todo en la página», sin porcentajes. **Recomendación: cerrar el hueco** y que la cifra viva en `/celula-a-celula`, que ya la publicó con su OK.
2. **Ciclos de tratamiento: no encuentro una fuente fiable** [verificado: `seguimiento.json` tiene 2 menciones de «ciclo»; `calendar_sync.config.json`, ninguna]. Propuesta: una entrada por **inicio o fin de línea o ensayo**, no por ciclo, y con origen en el calendario o en una marca a mano. Por ciclo, la cronología se llenaría de ruido.
3. **El inglés:** ¿un segundo PR por `voz-titular` con su propio «sí», o se acepta el desfase? (El guardián ya marca la falta de paridad ES/EN.) Recomendación: segundo PR en el mismo Telegram, «sí» a los dos.
4. **Análisis archivados:** `archivar_nota.py` no emite eventos [verificado: lectura]. Propuesta: una bandera `--hito-web` que la sesión pone a conciencia. Detectar «todo lo archivado» propondría notas internas.
5. **Modo activo:** ¿cuántos días en sombra? Recomendación: 7, y enseñarle **3 propuestas reales** de la sombra antes de encenderlo. **Condición previa (Vega): cerrar el hueco del lint** (UNKNOWN 1) antes de pasar a activo.
6. **No lo sé:** si un trabajo de `cola.py` puede leer el Llavero, y cómo se retira hoy un borrador de `salida` ya enviado. No abrí `cola.py` ni la retirada de borradores; es lo primero que se mira al implementar.

## Revisión de comités (17-oct)

| Quién | Veredicto | Qué cambió en el plan |
|---|---|---|
| Vega (`asistente`) | aprobado con matiz | tope general de 3/día, silencio 23-8, las vueltas de cambios fuera del tope, `origen` propio para 👍/👎, cerrar el lint antes de activo, filtro clínico (firma {{CONTACTO}}) |
| `consejero-arquitectura` | aprobado con matiz | un redactor único diario en vez de un trabajo por hito, estado `encolado`, esperar al CI antes del empujón, cerrojo, excluir por la rama `auto/`, retirar el borrador viejo |

Nadie discrepa del fondo. No se consultó a `comite-medico` ni a `diseno`: este plan no publica nada. `diseno` entra al revisar el formato del Telegram y las 3 propuestas de la sombra.

## Esfuerzo

Unas 4-6 h de implementación con tests si el OK llega con las 5 respuestas. Si el UNKNOWN 1 implica cerrar el lint, 1 h más.
