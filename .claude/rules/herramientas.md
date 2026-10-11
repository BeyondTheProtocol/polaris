---
paths:
  - "tools/enruta*.py"
  - "tools/grok.py"
  - "tools/perplexity.py"
  - "tools/nvidia.py"
  - "tools/local.py"
  - "tools/deid.py"
  - "tools/kb.py"
  - "tools/ramas.py"
  - "tools/honestidad_lint.py"
  - "tools/cosecha_entregables.py"
  - "tools/cosecha_checklists.py"
  - "tools/evidencia.py"
  - "tools/verifica_citas.py"
  - "tools/audit_herramientas.py"
  - "tools/state/herramientas.json"
---

# Tools, modelos, buscadores y MCPs: el catálogo

Salió de `CLAUDE.md` el 11-sep-26 para bajar la carga fija. La **norma** (qué puede salir, a qué
modelo, y cómo se trata la evidencia) se queda en el muro de `CLAUDE.md`; aquí va el **cómo**.

## Tools de uso diario
Catálogo entero: `ls tools/`. Las de siempre: `kb.py` (RAG) · `salida.py` (Telegram, HALT + anti-spam) · `seguimiento.py` (Tablero) · `archivar_nota.py` · `codigo_rojo.py` · `salud.py` · `coste.py` · `ramas.py` · `deid.py` (de-identificar por patrones; «sin identificadores detectados» NO es anónimo: lo que sale del caso es N2 por procedencia, `ia.ask(sensible_forzado=True)`).
- `honestidad_lint.py`: apoyo del sello de evidencia (caza el relay sin verificar).
- `cosecha_entregables.py`: red de seguridad de «dónde quedó archivado» (encuentra entregables que se quedaron solo en el chat).
- `ramas.py list`: quién trabaja ahora en qué rama.
- `dependencias.py`: grafo del repo sin LLM. `quien <script>` = qué se rompe si lo cambias; `huerfanos` = scripts que nadie nombra; `centrales` = los más usados.
- **Grafo de evidencia del caso** (graphify, 26-sep-26): 1849 nodos de los 74 análisis de `01 · Tratamiento`, de-identificados. Consulta por relaciones (diana ↔ fármaco ↔ ensayo ↔ estudio): `~/.venvs/graphify/bin/graphify query "<pregunta>" --graph "00_FUENTE-DE-VERDAD/01 · Tratamiento/_grafo-evidencia/corpus-deid/graphify-out/graph.json" --budget 2000`. Son conexiones entre NUESTROS análisis, no evidencia nueva; el 15 % son inferidas: cotejar contra el documento original antes de sostener nada. Actualizar = de-identificar lo nuevo con `deid.py` y relanzar `graphify extract` (incremental; cuesta API: piloto y saldo antes, `feedback-lote-api-piloto-y-saldo-antes`). Nota: `04 · IA/Notas/grafo-de-evidencia-del-caso-con-graphify-2026-09-26.md`.
- `cosecha_checklists.py`: sube al Tablero los checklists que viven dentro de los `.md` (la fuente única lo contiene todo).
- Investigación profunda: la skill `deep-research` YA NO EXISTE (comprobado 30-jul-26); se hace con los MCP de literatura (`search_papers`, `create_systematic_review`, PubMed/PMC) o un `Workflow` de varios agentes.

## 🧭 Qué modelo uso para cada cosa: `tools/enruta.py`
La centralita (2-sep-26). Decide y explica por qué: el muro primero (la norma de `CLAUDE.md`), luego capacidad, salud medida, coste-o-calidad, y panel de casas distintas para lo crítico.
- `enruta.py "la tarea"` decide; `--ejecutar` además llama; `--critico` saca panel.

## 🪜 Escalar de modelo por ATASCO, no solo por categoría (20-sep-26)

La regla de siempre sigue: **muro / clínico / hacia fuera = máxima potencia desde el primer
intento**, sin discusión. Lo que se añade es el otro caso, el que se resolvía a ojo: una tarea
*no* crítica que se atranca.

- **Empieza barato** en volumen y cribado (el patrón coordinador-caro + actores-baratos que ya
  defiende `consejero-arneses`).
- **Sube de modelo cuando la tarea se ATASCA**, no cuando «parece difícil»: dos intentos fallidos
  del mismo paso, o una salida que no cumple el formato pedido. El atasco es observable; la
  dificultad percibida, no.
- **Agrupa las correcciones**: un mensaje con las cinco cosas a arreglar gasta mucho menos que
  cinco turnos de una. Vale para mí y para cualquier comité que itere sobre un borrador.
- **Antes de escalar, mira DÓNDE se atasca** (`python3 -c "import sys;sys.path.insert(0,'tools');
  import ciclo_agentes;print(ciclo_agentes.atascos(7))"`): si el fallo es *entender el encargo*,
  un modelo más caro no lo arregla — lo arregla un encargo mejor. Si es *retomar*, tampoco:
  eso es contexto. Solo el atasco de *ejecución* mejora subiendo de modelo.

Origen: destilado de los 201 vídeos de {{CONTACTO}} (20-sep-26), cruzado con nuestros propios
números. **No anula el carve-out:** si algo pedía máxima potencia y no se puede, se BLOQUEA y se
avisa — nunca se degrada en silencio.

## Buscar
- `grok.py`: web+X en vivo.
- `perplexity.py`: citas, por el **Agent API** (`/v1/agent`; el `/v1/sonar` viejo se retira el 27-sep-26). Da acceso a **46 modelos** de varias casas con la misma clave: `python3 tools/perplexity.py --modelos`. La búsqueda hay que pedirla (`tools: web_search`) o contesta de memoria.
- `nvidia.py`: gratis, NO clínico.
- `local.py`: LLM en casa vía ollama, **egress 0**; `--deid` para de-identificar.
- **CLIs a demanda** (no las llama ningún daemon; se usan a mano y por eso NO son código muerto): `md_to_pdf_pro.py` (Markdown→PDF legible: hizo el paquete de elegibilidad de Moffitt), `elevenlabs_voz.py` (su voz hablada en español).
- `audit_agentes.py`: qué agente y qué skill se usan DE VERDAD, y con qué modelo corren (lo dispara `auto-mejora`). Gemelo de `audit_herramientas.py`; `inventario.py` responde otra pregunta (quién NOMBRA a quién).
- `inventario.py`: qué pieza está viva y cuál no llama nadie (`--huerfanas`, `--agentes`). Clasificar no es borrar.
- `onco.py`: OnCo (onco.cc), grafo abierto de oncología **en local** (`sync` · `buscar` · `ficha` · `novedades`); también es fuente del radar NED. Es mapa, no evidencia. **No usar `npx onco`/`onco-mcp`**: el 19-sep-26 `onco` en npm era otro paquete y `onco-mcp` no existía.
- `otra_casa.py` (10-oct-26): la boca por la que un texto ABSTRACTO entero sale a una IA de otra casa para que lo revise a ciegas (hoy Grok, destino `razona:grok`, fase 1). **No es `grok.py`** (ese es el buscador). **CERRADA desde el 11-oct-26 por decisión de {{TITULAR}}: no se usa.** El piloto 1 dio NO USAR (Claude y Grok acertaron todo: techo) y el piloto 2 no se corrió porque su corpus también tocaba techo; la auditoría de casa base está con `condiciones_cumplidas: false` aunque las constancias de {{TITULAR}} siguen ahí. Reabrirla no es «confirmar»: es un plan nuevo y decisión suya (el piloto 2, sin revisar, está en la rama `archivo/piloto2-otra-casa-2026-10-11`, sin fusionar). Lo que sigue describe cómo funciona si se reabriera. No envía sin `tools/otra_casa/auditorias.json` de casa base con cada constancia de {{TITULAR}} COMPLETA (fecha y lo que vio o decidió, no solo «confirmada»). Texto por fichero (nunca de zona clínica), sin herramientas, una llamada, sellos antes y después; la salida es «pista sin cotejar». `comprobar` valida un corpus sin enviar nada (`--instruccion` o `--instruccion-fichero`); `estado` enseña las guardas. **Hasta que el piloto de aceptación dé USAR, `revisar` solo se usa para el piloto** (plan, pasos 4 y 5; protocolo en `tools/otra_casa/piloto/PROTOCOLO.md`). **`revisar` se lanza en segundo plano desde una sesión principal que sigue abierta y espera, o en la terminal de {{TITULAR}}; nunca desde un subagente ni un flujo** (su proceso muere al responder). Hasta 15 min por texto; un Bash en primer plano corta a los 10. Cualquiera de esos cortes deja un envío sin desenlace. Una parada (también un 429) o un envío sin desenlace solo los levanta {{TITULAR}} tecleando `reanudar` en su terminal. La boca real solo corre en la casa base de verdad y con SU código (sin `BTP_REPO`, `BTP_HALT_FILES` ni `PYTHONPATH`; desde un worktree no envía). El primer envío con una auditoría va de día: su aviso sale urgente y, en silencio nocturno, la boca cierra. Un canario en el texto escala como en los carriles del borde. Contexto clínico, aunque vaya sin identificadores, es la fase 2 y cierra. Otra casa (GPT, Gemini) = adaptador + auditoría + sus pruebas, no un parámetro. Tras tocar la boca: `python3 tests/test_otra_casa.py --sellar-completa` (campaña completa de mutantes, varios minutos).
- Evidencia médica con citas: **scite está conectado por MCP** (`mcp__scite__*`, reglas en `.claude/rules/scite-mcp.md`).

## MCPs (vía ToolSearch)
Notion · Drive · Gmail (borradores) · PubMed/PMC · **BioMCP** · **cBioPortal** · Chrome · computer-use.
