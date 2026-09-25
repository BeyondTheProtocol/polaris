---
paths:
  - ".claude/rules/**"
  - ".claude/hooks/memoria_recall.sh"
  - ".claude/hooks/regla_en_accion.py"
  - "tools/memoria_radar.py"
  - "tools/salud_memoria.py"
  - "CLAUDE.md"
---

# El sistema de memoria: cómo se mantiene (25-jul-26)

Cuatro capas. Cada norma vive en **una** de ellas, no en varias.

| Capa | Fichero | Cuándo se carga | Qué va aquí |
|---|---|---|---|
| **1. Constitución** | `CLAUDE.md` | siempre, y **sobrevive a `/compact`** | lo que aplica a CUALQUIER tarea: estrella polar, muro, plan-primero, formato de entrega |
| **2. Reglas por contexto** | `.claude/rules/*.md` con `paths:` | solo al tocar ficheros que casan | reglas de un subsistema concreto |
| **3. Recall en el momento** | hooks + `memoria_radar.py` | al escribir el prompt y antes de una acción de riesgo | las memorias `feedback-*` |
| **4. Bucle cerrado** | `tests/test_recall_memoria.py`, auto-mejora | en `test_all.sh` y en la rutina | mide si el recall trae lo que debía |

## Los límites son reales, no estéticos
- **`MEMORY.md`: solo se cargan las primeras 200 líneas O 25KB**, lo que llegue antes. Lo que sobra **se pierde en silencio**. Objetivo: **< 15KB**.
- **`CLAUDE.md` se carga entero**, pero más largo = **menos adherencia** (la doc oficial recomienda **< 200 líneas**). Umbral aquí: **aviso a 14KB, techo 15KB** (equivalente en volumen a esas 200 líneas; el 25-jul quedó en **14.0KB / 86 líneas**, bajando desde 33.9KB; el 11-sep, en **12.5KB / 68 líneas**, con `tests/test_constitucion_sin_perdida.py` vigilando que no vuelva a engordar ni pierda una norma al recortar). Si lo pasa, **algo tiene que salir** a una regla `paths:` o a una memoria, no se sube el techo.
- Guardián: `python3 tools/salud_memoria.py` (en `test_all.sh`). Avisa **antes** de rozar el corte.

## Dónde meter una norma nueva
1. ¿Aplica a **cualquier** tarea? → constitución (`CLAUDE.md`), en una línea.
2. ¿Aplica solo al tocar **ciertos ficheros**? → `.claude/rules/<dominio>.md` con `paths:`.
3. ¿Es una lección de una corrección de {{TITULAR}}? → **memoria `feedback-*`** con *Why* + *How to apply*, y una línea en `MEMORY.md`.

Regla de oro: **nada se duplica**. Si una norma está en una regla, en la constitución va como puntero, no repetida. Dos copias divergen y entonces «Claude puede elegir una arbitrariamente» (doc oficial).

## Lo que NO se puede arreglar con memoria
`CLAUDE.md` y las memorias son **contexto, no configuración forzada**. Lo que tiene que cumplirse SIEMPRE va en un **hook** (`muro_guard.py`, `clinico_guard.py`), que se ejecuta pase lo que pase. Si alguien propone «pongo la regla en CLAUDE.md y ya», y la regla es crítica, la respuesta es: **hook**.

## Las herramientas de esta capa

| Para | Comando |
|---|---|
| Regenerar el índice tras escribir una memoria | `python3 tools/indice_memoria.py` |
| Ver si algo se ha pasado de los límites | `python3 tools/salud_memoria.py` |
| Ver qué normas ha tenido que repetir {{TITULAR}} | `python3 tools/reglas_repetidas.py` |
| Medir si el recall sigue trayendo lo que debe | `python3 tests/test_recall_memoria.py` |
| Recalcular los vectores (si se activa el brazo semántico) | `.venv-embed/bin/python tools/memoria_radar.py vectores` |

## Cuando una corrección se repite
Es señal de que la norma está en la capa equivocada: **súbela de capa** (memoria → regla `paths:` → constitución), no la reescribas más fuerte en el mismo sitio. El detector de la rutina de auto-mejora lo **propone**; nunca lo escribe solo, porque **memoria = superficie de ataque** ([[reference-claude-code-capacidades-2026-07]]).
