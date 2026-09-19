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
