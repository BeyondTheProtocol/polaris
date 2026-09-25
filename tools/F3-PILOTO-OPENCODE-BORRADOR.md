# F3 · Fase B — Piloto OpenCode contra el gateway del borde (BORRADOR)

> Plan: `playful-crafting-pancake` (motor `typed-swinging-wand`). Fecha: 23/6/26.
> Rama: `claude/objective-bose-d6b8ab`. **Nada encendido como daemon — eso es gate aparte de {{TITULAR}}.**

## TL;DR
Instalé OpenCode (1.17.5) **enjaulado** y validé que el muro lo contiene: el orquestador abierto solo
ve el gateway local del borde, sin claves de nube, y físicamente no puede llegar a ningún modelo salvo
a través del borde (que clasifica, bloquea lo sensible y sella cada llamada). Las 3 pruebas de muro del
plan **pasan**. Descubrí y arreglé un hueco real (streaming SSE) sin tocar el muro. **Recomendación:
adoptar OpenCode como orquestador abierto piloto** — la jaula aguanta.

## Qué hice
1. **Instalé OpenCode** vía Homebrew (`opencode 1.17.5`, binario). El enjaulado es en tiempo de
   ejecución, no en la ubicación del binario.
2. **Lo enjaulé** en `tools/state/f3_piloto/` (gitignored, fácil de borrar):
   - HOME contenido + `env -i` (entorno fregado) → **cero claves de nube heredadas** (verificado: el
     entorno solo lleva PATH/HOME/XDG/TERM; ni `ANTHROPIC_*` ni `OPENAI_*`).
   - Config con **un único provider** `borde` → `base_url = http://127.0.0.1:8799/v1`, token del
     gateway como `apiKey`, modelos `local-ollama`/`nvidia-free`. Sin ningún otro provider.
3. **Corrí una tarea NO clínica end-to-end** ("¿por qué el cielo es azul?") → OpenCode → gateway (SSE)
   → borde → `ia.ask` → `local-ollama` → respuesta. (El texto es flojo porque local-ollama es un
   modelo pequeño; lo que valida el piloto es la **jaula**, no la calidad del modelito.)
4. **Arreglé un hueco real:** OpenCode pide `stream:true` (SSE) y el gateway solo devolvía un JSON
   único → OpenCode recibía vacío. Añadí streaming SSE OpenAI-compatible al gateway. **Muro-neutral:**
   el contenido sigue pasando entero por `ia.ask`/borde; solo cambia el envoltorio de la respuesta.
   2 tests nuevos. Batería: `test_borde_gateway` 18/18; `test_all.sh` **todo en verde**.

## Qué validó el muro (las 3 pruebas del plan)
| Prueba | Resultado |
|---|---|
| **(a) Toda llamada del orquestador queda trazada** | ✅ Cada petición de OpenCode aparece en el log de metadatos del gateway **y** sellada en el ledger hash-chained del borde (`tools/state/borde/ledger-*.jsonl`). |
| **(b) Entrada clínica/canario → el borde BLOQUEA** | ✅ Sembré un canario y pedí a OpenCode que lo repitiera. El borde lo **negó en TODOS los cerebros** (`local:ollama`, `nvidia`, `cleared:claude`: `permitido=False, alarma=True`), encadenado en el ledger (seq 40-45). OpenCode recibió 503 sin contenido y **el canario nunca apareció** en ninguna respuesta guardada. |
| **(c) Sin gateway, el orquestador NO tiene otra vía a un modelo** | ✅ Triple cerrojo: (1) maté el gateway → OpenCode no produjo respuesta de modelo (su único provider apunta al gateway, sin claves de nube); (2) bajo `sandbox-exec` solo-loopback con gateway vivo → funciona (solo alcanza el borde); (3) bajo `sandbox-exec` con la red TOTALMENTE denegada → **cero** peticiones al gateway (la jaula de SO muerde de verdad). |

Además: token malo → 401; ruta desconocida → 404; cadena agotada → 503 honesto.

## Recomendación
**Adoptar OpenCode como piloto del orquestador abierto/model-agnostic**, siempre detrás del gateway del
borde y dentro de la jaula (HOME contenido + `env -i` sin claves + sandbox solo-loopback). La elección
NO queda clavada: el gateway es agnóstico, así que Goose se puede probar contra el MISMO endpoint si
algún día conviene.

## Lo que te toca a ti (gates pendientes)
- **Encender el gateway como daemon launchd** (vida larga) = tu OK. Hoy se corre a mano para el piloto.
- **Permisos de OpenCode deny-by-default** (bash/edit/mcp en `ask`) y el wrapper de sandbox quedan
  listos para cuando se decida usarlo en serio; revisarlos contigo antes de darle herramientas.
- **Limpieza:** si no quieres seguir, se borra con `brew uninstall opencode` + `rm -rf tools/state/f3_piloto`.

## Ficheros
- `tools/borde_gateway.py` — +streaming SSE (muro-neutral).
- `tests/test_borde_gateway.py` — +2 tests (SSE OK / SSE+muro-rechaza).
- `tools/state/f3_piloto/` — jaula del piloto (config, sandbox profiles, HOME). **Gitignored.**
