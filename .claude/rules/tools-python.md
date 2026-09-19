---
paths:
  - "tools/**/*.py"
  - "tests/**/*.py"
---

# Tocar una tool de Polaris

## Antes de escribir código
- **¿Ya existe?** Busca en `tools/` y **fuera del repo** antes de decir «no existe» ([[feedback-buscar-fuera-del-repo-antes-de-decir-no-existe]]).
- **¿Otra rama ya lo toca?** `python3 tools/ramas.py en-vuelo <patron>`.
- **No sobre-ingenierizar:** filtra por *«¿esto acerca a NED?»* ([[feedback-no-sobreingenierizar-filtrar-por-ned]]).
- Estándar de código: `00_FUENTE-DE-VERDAD/04 · IA/Calidad-Codigo-Python-Polaris.md`.

## Al terminar
- **Corre `tests/test_all.sh`, no solo tu test** ([[feedback-correr-test-all-no-solo-py]]).
- **Verifica el EFECTO, no que el script corrió sin error** ([[feedback-verificar-efecto-no-que-corrio]]).
- Si la tool avisa a {{TITULAR}}, el aviso sale por **`salida.py`** (respeta HALT y anti-spam). Todo aviso necesita anti-spam ([[feedback-todo-aviso-a-titular-necesita-anti-spam]]).
- **Envío opt-in en tests:** ningún test manda mensajes de verdad a {{TITULAR}} ([[feedback-envio-opt-in-para-no-spamear-en-tests]]).

## Estado vivo y ficheros compartidos
- El **estado vivo** (`tools/state/`, `seguimiento.json`) es de **casa base**: los conflictos se resuelven ahí, no en el worktree ([[feedback-estado-vivo-resuelve-casa-base]]).
- Los ficheros *hot* compartidos se **serializan** con lock ([[feedback-serializar-ficheros-hot-compartidos]]).
- En un worktree, **edita las rutas del worktree**, no las de casa base ([[feedback-worktree-editar-rutas-del-worktree]]).

## La Anatomía (`tools/anatomia.py`)
El empuje cifrado refresca solo lo derivable; el camino de un encargo, el glosario y los conectores de cuenta se editan a mano en `tools/anatomia.py`. La norma (el panel cuenta cada cambio de Polaris antes de cerrar) vive en `normas-que-se-me-olvidan.md`.

## Coste
Al empezar la tarea **decide el tier** (modelo + precisión): no vayas a tope por reflejo, pero **muro / clínico / hacia fuera = máxima potencia + verificación siempre**. Mídelo con `python3 tools/coste.py`. Detalle: [[feedback-estrategia-coste-precision]].

⛔ **Carve-out:** si algo pedía máxima potencia y **no se puede** a ese nivel (saldo agotado, herramienta caída), **NO lo degrades ni lo medio-hagas: BLOQUÉALO, APLÁZALO y AVISA**. Media calidad en lo crítico engaña ([[feedback-no-degradar-lo-critico-bloquear-avisar]]).
