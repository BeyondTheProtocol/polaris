---
paths:
  - "tools/correo*.py"
  - "tools/email_archive.py"
  - "tools/outbox*.py"
  - "00_FUENTE-DE-VERDAD/_PRIVADO_CORREO/**"
---

# Correo: cómo se prepara y quién lo manda

Esta regla existe porque `tools/reglas_repetidas.py` la cazó el 25-jul: el formato HTML
se le había repetido a {{TITULAR}} **3 veces** y la lección no tenía sitio en ninguna capa,
solo una memoria suelta. Estaba escrita y aun así no llegaba.

## Formato
Todo correo que preparo para enviar (a médicos, prensa, contactos) va **en HTML**, no en
texto plano: **negritas** en lo clave, **enlaces clicables** (`<a href>`, nunca una URL
pegada en crudo), listas (`<ol>`/`<ul>`) para enumerar, y estructura limpia con firma
formateada. Detalle: [[feedback-correos-formato-html]].

## Voz y redacción
Lo redacta el agente de correo y pasa por `voz-titular` como última capa: tiene que sonar
a ella, no a IA ([[feedback-delegar-correos-al-agente]], [[feedback-no-em-dash-tell-ia]]).
Antes de redactar una respuesta, **lee el último mensaje del hilo**: contestar a lo de
hace tres correos se nota ([[feedback-wp-leer-ultimo-mensaje-antes-de-redactar]]).

## Cuenta correcta
Cada hilo tiene su cuenta. El conector `titular` no es el mismo buzón que
`titular.mgp`: responder desde la equivocada rompe el hilo ([[feedback-correo-cuenta-correcta-del-hilo]]).

## Quién manda
🛑 **Nada sale sin el OK de {{TITULAR}}.** Yo dejo el BORRADOR listo y firma ella. Un borrador
nuevo **sustituye** al anterior, no se acumulan versiones ([[feedback-borrador-mejorado-borra-el-anterior]]).
El gestor de correo hace; Vega consulta ([[feedback-gestor-correo-hace-vega-consulta]]).

Sí puedo adjuntar, archivar y preparar envíos: no digas «no puedo»
([[feedback-si-puedo-adjuntar-correos]]).

## Adjuntar a una respuesta, de punta a punta (17-oct-26)
Sin devolverle el paso a ella ([[feedback-adjuntar-sin-que-guie]]). Primero `python3 tools/correo_outbox.py status`:
- **✓** → `correo_outbox.py draft … --attach f1 f2 --reply-to-message-id "<Message-ID del padre>"` (la cabecera `<…@…>` de `get_message`, no el id del hilo; asunto `Re: …`).
- **🔒 bloqueado** (rc=36: la clave está, el Llavero no se abre) → vía Chrome: `create_draft` con `replyToMessageId` → `navigate` **con `tabId` explícito** a su `viewUrl` (compose) → `find` el input de fichero → `file_upload` **suelto, sin clics antes** y con todos los ficheros en una llamada. `salida_guard` lo deja pasar y cierra 15 min los clics de la sesión: se comprueba leyendo y queda a un clic. Ojo con los dos ids: `create_draft.replyToMessageId` lleva el id de mensaje de la API de Gmail; `--reply-to-message-id` de `correo_outbox`, la cabecera RFC `Message-ID`. Desbloquear el Llavero en su Terminal **no** sirve: las sesiones entran por SSH y no heredan el login gráfico; el arreglo de raíz es el broker de secretos (rama `claude/awesome-babbage-cee7dd`).
