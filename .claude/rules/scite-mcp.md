---
description: Reglas de uso del MCP de Scite. Se cargan al tocar scite, evidencia con citas o el conector MCP.
paths:
  - "tools/evidencia.py"
  - "tools/verifica_citas.py"
  - ".claude/agents/comite-medico.md"
  - ".claude/agents/verificacion.md"
---

# Scite por MCP: qué puede salir y qué no

Auditoría del 2-sep-2026 (`acceso-herramientas`), con el mismo listón que dejó a Elicit en
solo-local. **Veredicto: ÁMBAR.** Scite pasa donde Elicit falla, y lo que queda ámbar no es
Scite: es que sus *prompts* aceptan texto libre y un agente podría pegar ahí media historia
clínica.

## Por qué se admite

Cita literal de sus Términos, sección 1, verificada descargando la página:

> "the Company does not use Customer Data (…including without limitation **inputs, outputs,
> queries**…) to train, fine-tune, or improve such AI systems."

La definición de *Customer Data* nombra explícitamente lo que viaja por el MCP. Está repetido
en la Política de Privacidad (v. 26-mar-2026) y en los términos del propio MCP. Elicit dice lo
contrario en su artículo 7.2, y por eso Elicit no entra.

Y lo decisivo: **por el MCP solo viajan argumentos tipados**. 25 herramientas con parámetros
concretos (`term`, `dois`, filtros), `resources/list` devuelve lista vacía, no hay ingesta de
ficheros ni canal de contexto de conversación. No puede llevarse el hilo aunque quisiera.

## Qué puede salir

Terminología y nada más: genes, variantes, fármacos, histologías, dianas, DOIs, identificadores
NCT, años, términos MeSH.

## Qué no sale nunca

Nombre, fecha de nacimiento, NHC, hospital, nombre de sus médicas, fechas de cita, y **nada
copiado de `_PRIVADO_*`**.

## La regla de las tres señas

Nunca combinar en un mismo argumento **tres o más** de estos: edad exacta, histología, estado de
receptores, variante somática, línea de tratamiento.

Por separado son literatura. Juntas son su ficha. Con un perfil tan raro como el suyo, tres
señas bastan para reidentificarla aunque no aparezca su nombre por ningún lado.

## Los dos prompts vetados

`fact-check-claim` y `systematic-review-screen` aceptan texto libre que se envía al servidor.
Ahí cabe entera una frase de un informe. **No se usan desde un agente autónomo.** Si hacen
falta, la frase la reformula un humano, o pasa antes por `tools/deid.py`.

Los otros dos (`literature-review`, `verify-bibliography`) son de riesgo bajo.

## Collections: solo lectura para agentes

Las 7 herramientas de colecciones (`create/update/delete_collection`, `add/remove_dois`) quedan
fuera para agentes autónomos. Una colección persistente de DOIs sobre su histología concreta,
guardada bajo su cuenta nominal en la nube de Scite, es un perfil clínico inferible que
sobrevive a la sesión. Leer sí; escribir, no.

## Lo que ya está cubierto sin hacer nada

`.claude/hooks/muro_guard.py` es allowlist **fail-closed**: en perfil `privileged` (el de las
rutinas de las 5 de la mañana) toda herramienta que no esté explícitamente permitida cae al DENY
genérico, y **hoy no hay ningún MCP allowlistado**. Así que Scite queda fuera del alcance de los
agentes desatendidos por diseño, no por confianza.

Si alguien lo añade a `SAFE_MCP_TOOLS`, esa protección desaparece y estas reglas pasan a ser lo
único que queda. Que sea una decisión consciente de {{TITULAR}}, no un efecto colateral.

## Incógnitas que la auditoría dejó abiertas

- **Qué LLM hay debajo de su Assistant: no se sabe.** Cero menciones de proveedor en Términos,
  Política, T&C y documentación. *No sé* no es *no hay*.
- **Los logs de consultas MCP no tienen plazo de retención publicado**, y su documentación
  confirma que se registran para analítica de sesión.
- **`scite.ai/apiterms` devuelve 404**: no hay términos de API publicados, se aplican los de
  Servicio.
- Sin HIPAA, sin BAA. Servidores en EE. UU., con cláusulas contractuales tipo para la UE.

Ninguna cambia el veredicto, porque el compromiso de no entrenar es contractual y explícito.
Pero si algún día se quiere subir el nivel de lo que se le manda, hay que resolverlas antes.

## Cómo se conecta

```bash
claude mcp add --transport http --scope user scite https://api.scite.ai/mcp
```

Requiere OAuth con su cuenta, así que lo ejecuta ella. Relacionado:
[[project-consensus-scite-must]], [[reference-elicit-veredicto-solo-local]],
[[feedback-herramientas-externas-auditoria-primero]].
