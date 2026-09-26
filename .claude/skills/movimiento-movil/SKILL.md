---
name: movimiento-movil
description: >-
  Vara de movimiento, interacción y sensación nativa en el móvil para helptitular.com y
  cualquier superficie web de Polaris: cuándo animar y cuándo no, curvas y duraciones con
  los tokens del design system, retroalimentación al tocar, interrupción, y los arreglos de
  plataforma móvil (hover pegado, destello al tocar, 100vh, zoom en inputs, notch). Úsala al
  construir o revisar cualquier cosa que se mueva, se toque o se arrastre, y al buscar dónde
  añadir interactividad. La auditoría la firma el comité `diseno`; esto es la vara.
---

# Movimiento y móvil: la vara

Minado y adaptado (26-sep-2026) de las skills de Emil Kowalski (`emilkowalski/skills`, commit
`d16ebe6`, licencia MIT: ver `LICENSE-emilkowalski.txt`): `mobile-native`, `review-animations`,
`find-animation-opportunities` y partes de `emil-design-eng` y `apple-design`. Las cifras y la
tabla de síntomas son suyas; la adaptación a nuestros tokens, a la marca y a qué es
«interactivo» aquí, nuestra. El detalle técnico está en [estandares.md](estandares.md).

## La idea: interacción sí, decoración no

{{TITULAR}} quiere más interactividad porque engancha, da impacto y trae ayuda. Emil pide
contención («a menudo la mejor animación es ninguna»). **Las dos cosas son compatibles si se
separan:**

| Es | Qué hacer | Ejemplo nuestro |
|---|---|---|
| **Interacción que deja explorar** (la gente toca, arrastra, compara, reproduce) | **Buscarla y darle prioridad.** Es lo que la gente comparte y lo que explica el caso | Visor 3D del hígado, «reproducir la evolución» de /datos, fijar una fecha en todos los gráficos |
| **Retroalimentación** (la interfaz confirma que te ha oído) | Siempre, rápida y sutil | Pastilla de pestaña al pulsar, botón que cede al tocar |
| **Movimiento decorativo** en algo que se ve a menudo o que se lee | Quitarlo o reducirlo | Datos que se animan «para quedar bien», entradas lentas en cada visita |
| **Deleite** en momentos raros y emocionales | Aquí sí hay presupuesto | Primera visita, gracias tras donar, un hito de la campaña |

Regla práctica: **la interacción la pone quien mira; la animación decorativa se la impone la
página.** Lo primero suma; lo segundo, pasado el primer vistazo, resta.

## Antes de animar algo: las cuatro preguntas (en orden)

1. **¿Con qué frecuencia lo ve una persona?** Muy a menudo (navegación, pestañas, atajos de
   teclado) → nada o casi imperceptible. De vez en cuando (hojas, avisos, un desplegable) →
   animación estándar. Raro o primera vez → hay sitio para el deleite.
2. **¿Para qué?** Tiene que ser una de estas: retroalimentación, continuidad espacial (de dónde
   viene, adónde va), indicar un estado, evitar un salto brusco, explicar algo, o deleite (solo
   en lo raro). «Queda bonito» no vale.
3. **¿Cabe en el presupuesto de tiempo?** Interfaz por debajo de 300 ms (`--dur-transicion`).
   Si solo funciona lenta y vistosa, no pasa.
4. **¿Ayuda o estorba?** Un dato que alguien está leyendo no se mueve por estilo. En /datos, el
   gráfico se anima si lo pide quien mira (reproducir), no solo porque entra en pantalla.

## Lo que no se negocia

- **Tokens del design system, no curvas nuevas.** `--dur-micro` (150 ms), `--dur-transicion`
  (300 ms), `--curva-entrada`, `--curva-salida` en
  `app/assets/css/design-system-v2/btp-tokens.css`. Si hace falta una curva nueva, entra como
  token con la aprobación de `diseno`, nunca suelta en un componente.
- **Nunca `ease-in` en interfaz,** tampoco en salidas: empieza lento justo en el instante que
  la persona mira. Entrar y salir → curva de salida (ease-out fuerte).
- **Solo `transform` y `opacity`.** Animar alto, ancho, márgenes o posición recalcula todo el
  diseño en cada fotograma.
- **Nunca desde `scale(0)`.** Desde `scale(0.95–0.97)` + `opacity: 0`.
- **Responder al posar el dedo, no al levantarlo:** `:active` o `pointerdown`, 100–160 ms.
- **Todo movimiento se puede interrumpir.** Transiciones CSS (se re-apuntan) mejor que
  `@keyframes` (reinician) en lo que se dispara a menudo; nunca bloquear la entrada mientras
  anima.
- **`prefers-reduced-motion`:** menos y más suave, no cero. Se quita el desplazamiento, se
  queda el cambio de opacidad o color que ayuda a entender.
- **Hover solo donde hay hover:** `@media (hover: hover) and (pointer: fine)`. En el móvil,
  el primer toque deja el `:hover` pegado.
- **Nunca desactivar el zoom** (`user-scalable=no`, `maximum-scale=1`). El zoom en inputs se
  arregla con 16 px.
- **La emulación del navegador no es un móvil.** Hover pegado, retardo al tocar, 100vh, zoom en
  inputs y notch solo se ven en un teléfono de verdad. Lo que solo se ha visto emulado se
  reporta como «sin verificar en dispositivo».

## Para auditar

Formato de hallazgos (obligatorio), de más grave a menos:

| # | Dónde (`fichero:línea`) | Hoy | Problema (regla de esta vara) | Arreglo con valores exactos |
|---|---|---|---|---|

Además, **siempre**:
- **Descartados (2-5):** sitios que se miraron y NO deben animarse, con la pregunta que los
  tumbó. Esto es lo que separa una auditoría de una lista de deseos.
- **Oportunidades de interacción (máx. 5):** dónde dejar explorar más, ordenadas por impacto
  en ayuda y difusión, cada una con su propósito de la lista de arriba.
- **Qué necesita un teléfono real** para confirmarse.

Preferencia al proponer arreglos: primero borrar la animación que sobra, luego reducirla, luego
corregir la curva, el origen, la interrupción, pasarla a GPU, y el pulido al final.

Lo publicado **no se toca** desde una auditoría: salen hallazgos, y cambiar copy o diseño ya
publicado pide el OK de {{TITULAR}} (`.claude/rules/marca-copy.md`).
