---
paths:
  - "00_FUENTE-DE-VERDAD/07 · Marca/**"
  - "**/*copy*.md"
---

# Marca, copy y web: el Comité Web manda

## Orden del comité (no se salta)
idea → **Producto** → **Diseño** (con todo el design system) + Producto → súper validado → **Web** programa → **auditoría** de homogeneidad.

**4 lentes siempre:** accesibilidad, marketing, psicología, neurodivergencia.

Flujo técnico: rama → PR → preview Netlify → fusión. No inventar patrones: se reusa el design system. Detalle: `00_FUENTE-DE-VERDAD/07 · Marca/Proceso-Comite-Web.md`.

**Quién fusiona (frase única, 13-oct-26, OK de {{TITULAR}}):** *fusiona Polaris con el permiso firmado de {{TITULAR}}, atado al commit que vio.* Su «fusiona» es la firma; la mecánica es mía. Vale para un PR suelto (`gh pr merge <N> --match-head-commit <SHA>`) y para el lote semanal (`tools/web_lote.py`). Lo único que fusiona desde Python es `tools/web_publicar.py`.

## 🛑 Copy ya publicado
Editar copy **ya publicado** (sobre todo hero/H1, taglines y claims) es **gate de salida**: un «dale» rápido NO basta. {{TITULAR}} tiene que entenderlo bien, y ese PR solo sale si **lo nombra por su número** en su mensaje: un «fusiona» genérico no lo arrastra. Detalle: [[feedback-no-tocar-copy-web-sin-ok]].

## 🪜 La escalera del guardián de la web (13-oct-26)
- **Peldaño 1, mecánico** (código sin texto visible, enlaces rotos, sitemap, accesibilidad, eventos, regenerar /datos ya sellado): entra en el lote y sale con su «fusiona».
- **Peldaño 2, míralo** (texto visible que cambia sin cambiar de voz, erratas, secciones nuevas, inglés, prensa literal, y **todo lo que nazca de una fuente externa**): va con diff y preview.
- **Peldaño 3, lo entiende y firma** (hero, claims, navegación principal, dato clínico nuevo, tercero nombrado, borrar copy): solo si nombra el PR.

Plan y motivos: `00_FUENTE-DE-VERDAD/04 · IA/Notas/plan-guardián-de-la-web-v2-aprobado-13-oct-2026-2026-10-13.md`.

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
