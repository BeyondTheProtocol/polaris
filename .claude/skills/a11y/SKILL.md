---
name: a11y
description: >-
  Estándares de accesibilidad web (WCAG 2.2 AA, WAI-ARIA APG) con más de 38
  anti-patrones clasificados por severidad, contexto legal (EAA, ADA Title II) y
  arreglos concretos por framework. Úsala al construir o revisar cualquier
  superficie visible —web, dashboards, paneles, PDFs con HTML— y siempre que se
  toque color, foco, movimiento, semántica, formularios o áreas táctiles. La
  auditoría la firma el comité `ceci`; esto es la vara contra la que audita.
---

# Accesibilidad — la vara

El detalle completo vive en [a11y.instructions.md](a11y.instructions.md): tabla
WCAG 2.2 AA, los anti-patrones con su severidad y su detección, los patrones
ARIA y los arreglos por framework. Léelo antes de dar por buena una pieza.

## Lo que no se negocia aquí

- **Contraste WCAG por encima del mockup.** Si el diseño y la norma chocan, gana
  la norma (regla del comité de calidad de {{TITULAR}}).
- **Nada que tape el texto.** Ningún elemento flotante se come contenido.
- **Teclado siempre.** Todo lo que se pulsa con ratón se alcanza con `Tab` y se
  activa con `Enter`/`Espacio`, y el foco se ve.
- **Táctil ≥ 44 px.**
- **Alternativa en texto** para cualquier gráfico o diagrama que sostenga una
  decisión (`role="img"` con `<title>` y `<desc>`, o la fuente de verdad al lado).
- **Nunca solo el color** para distinguir estados: acompáñalo de forma, texto o
  trazo.

## Cómo se usa

1. Antes de construir: mira los anti-patrones de la familia que vas a tocar.
2. Al terminar: calcula los contrastes **sobre el código real**, no de memoria.
3. Pásale la pieza al comité `ceci`, que audita y devuelve aprobado / con matiz /
   con cambios. `ceci` se consulta y su criterio pesa, pero no decide.
