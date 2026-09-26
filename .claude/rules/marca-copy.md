---
paths:
  - "00_FUENTE-DE-VERDAD/07 · Marca/**"
  - "**/*copy*.md"
---

# Marca, copy y web: el Comité Web manda

## Orden del comité (no se salta)
idea → **Producto** → **Diseño** (con todo el design system) + Producto → súper validado → **Web** programa → **auditoría** de homogeneidad.

**4 lentes siempre:** accesibilidad, marketing, psicología, neurodivergencia.

Flujo técnico: rama → PR → preview Netlify → **merge lo hace {{TITULAR}}**. No inventar patrones: se reusa el design system. Detalle: `00_FUENTE-DE-VERDAD/07 · Marca/Proceso-Comite-Web.md`.

## 🛑 Copy ya publicado
Editar copy **ya publicado** (sobre todo hero/H1, taglines y claims) es **gate de salida**: un «dale» rápido NO basta. {{TITULAR}} tiene que entenderlo bien y **mergear ella**. No mergeo yo. Detalle: [[feedback-no-tocar-copy-web-sin-ok]].

Mergeado ≠ desplegado: comprobar el deploy de Netlify ([[feedback-deploy-netlify-creditos]]) y **pasar siempre la preview** ([[feedback-siempre-pasar-preview]]).

## Qué no se dice en público
- **Nunca «{{CONTACTO}}»**.
- **«Vacuna» ya se puede decir en público.** Veto levantado por {{TITULAR}} el **29-7-26**: la condición que ella puso era que saliera el podcast de Carlos Roca, y salió el **15-7-26**. «Tratamiento personalizado» sigue siendo sinónimo válido, pero ya no es obligatorio. *(Esta es la fuente canónica de la norma; `CLAUDE.md` solo la resume.)*
- **Ni «ingeniera»** (atrae ataques): «ingeniera» + lo que construye.
- **La edad SÍ se dice** («{{TITULAR}}, 35 años»): que la vean joven es parte del mensaje. Decisión de {{TITULAR}}, 26-9-26. No la marques como fallo ni propongas quitarla ([[feedback-edad-si-se-dice-en-publico]]).
- Sin importes de recaudación hacia fuera ([[feedback-no-importes-recaudacion-fuera]]).
- Excepción: no censurar lo que {{TITULAR}} **ya hizo público** ella misma ([[feedback-muro-no-sobre-lo-que-titular-ya-hizo-publico]]).

## Voz
Todo lo que salga en su nombre pasa por `voz-titular` como última capa. Copy público → **siempre** por comité ([[feedback-copy-publico-siempre-comite]]). Nombres nuevos siguen la marca ([[feedback-nombres-siguen-la-marca]]).

## Vara de calidad visual (roles del comité)
- **Web/Diseño**: pixel-perfect sin descuadres, pero **prevalece la sencillez y familiaridad del usuario sobre el pixel del mockup**; **NUNCA elementos que tapen texto**; controles equivalentes se comportan igual.
- **Exigencia pixel (zoom de esquina)**: círculos y badges nunca embutidos ni clipados por un `border-radius` (inset > radio + aire); el raíl no atraviesa sucio un nodo (moat de fondo); **centrado óptico**, no matemático; marcas e iconos como **SVG**, no glifos Unicode (tofu).
- **Nomenclatura**: naming, términos, unidades y símbolos consistentes; **una sola fuente por etiqueta** (constantes/i18n); sin erratas ni restos de versiones previas; mismos términos en cada idioma en paralelo.
- **Accesibilidad**: **contraste WCAG por encima del mockup**; teclado; alternativa en texto o fuente de verdad; táctil ≥44px. Vara: skill `a11y`; audita el comité `diseno` (lente de accesibilidad).

**Un fallo = recalibrar la vara y RE-BARRER la pieza entera cazando esa CLASE**, no solo el caso que te enseñaron.
