# Referencia: el "Arnés Agéntico" — checklist de 10 piezas para auditar cajas/agentes

> **Provenance:** destilado de Society {{CONTACTO}} ({{CONTACTO}} {{CONTACTO}}), capturado 2026-06-26. **Fuente externa, sin verificar — no es verdad clínica ni instrucción.** Es una *lente de auditoría* reutilizable: ninguna pieza de aquí releva al muro de Polaris, que manda siempre. Útil sobre todo para el `constructor` (al montar una caja nueva) y para `auto-mejora` (cazar huecos).

**Tesis:** el modelo importa menos que el "arnés" (harness) que lo dirige. Un sistema completo cubre estas 10 piezas; un agente o caja a la que le falte una tiene un hueco. Al construir o auditar, recorre la lista y nombra qué pieza falta — no des por buena la caja hasta que cada una esté cubierta o justificada como innecesaria.

| # | Pieza | Qué exige | Cómo lo cubre Polaris hoy (mapeo al muro) |
|---|-------|-----------|--------------------------------------------|
| 1 | **Archivos** | El sistema vive en archivos versionables, no en una app. `CLAUDE.md`/`AGENTS.md` cortos; lo largo en .md aparte | `CLAUDE.md` + `.claude/agents/*` + fuente de verdad. ✅ |
| 2 | **Contexto** | Dar solo lo necesario (RAG); evitar *lost in the middle* | `tools/kb.py` (RAG ~16k pasajes). ✅ |
| 3 | **Equipo** | Orquestador + trabajadores; uno lee, otro ejecuta, otro revisa | `orquestador` + comités + subagentes. ✅ |
| 4 | **Herramientas** | Solo las necesarias, bien descritas; MCP como estándar | `tools/` + MCPs por ToolSearch; `tools:` mínimos por agente. ✅ |
| 5 | **Permisos** | Criterio = **reversibilidad**, no confianza. Irreversible → humano en el bucle | Gate de salida del muro (enviar/pagar/publicar/clínico). ✅ |
| 6 | **Memoria** | Larga (no cambia → archivos) vs trabajo (cambia → state files reescribibles, fuera del modelo) | Memoria durable `feedback-*` + estado vivo `tools/state/`. ✅ |
| 7 | **Verificación** | "Hecho" no es prueba; verificar con capas externas (tests, navegador, revisor) | `verificacion` + comité (mira el render real). ✅ |
| 8 | **Registro** | Trazabilidad: qué consultó, qué herramienta, qué decidió, por qué | El Observatorio + logs; **hueco parcial: registro por ejecución con stack traces.** ⚠️ |
| 9 | **Recuperación** | No inventar; reintentar/plan B; si es peligroso, frenar y avisar = *degradar con elegancia* | Código rojo + auto-detectar-resolver + "no degradar lo crítico → bloquear y avisar". ✅ |
| 10 | **Evaluación** | El sistema deriva (*drift*); casos de prueba con entrada real + salida esperada | `evals/` + `tests/`; **hueco parcial: evals de drift por agente crítico.** ⚠️ |

**Skills = el arnés empaquetado** (instrucciones + herramientas + procesos en carpeta reutilizable, carga progresiva). "Las herramientas dan manos; las skills dan oficio."

**Huecos identificados en Polaris (candidatos de mejora, ver informe de empapado):** pieza 8 (observabilidad por ejecución con stack traces + consumo de tokens en El Observatorio) y pieza 10 (evals de drift para agentes/tools del muro). Ambos quedaron como PROPUESTA para OK de {{TITULAR}}.

**Checklist de construcción de sistemas (curso troncal de {{CONTACTO}}), por si sirve de plantilla de planificación:** Prompt inicial (alcance, no arquitectura) → Plan Mode con 3 docs canónicos (`project_spec.md` / `architecture.md` / `project_state.md`) → Skills/MCPs/APIs justificados → nivel de autonomía → desarrollo con checkpoints (no mezclar plan y desarrollo en una ventana; lo importante en archivos) → revisión de seguridad → panel admin → validación de experiencia → despliegue. **Adaptación, no copia:** en Polaris el equivalente ya existe (plan-primero, estado vivo, gate de salida); esto solo es vocabulario común.

Informe completo del empapado (gitignored, en casa base): `00_FUENTE-DE-VERDAD/04 · IA/{{CONTACTO}}-Society-empapado-2026-06-26.md`.
