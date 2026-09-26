# Estándares de movimiento y móvil

Cifras y reglas de `emilkowalski/skills` (commit `d16ebe6`, MIT, ver `LICENSE-emilkowalski.txt`),
traducidas y llevadas a los tokens de helptitular.com. Se citan en los hallazgos; no se aproximan.

## 1. Curvas (con nuestros tokens)

| Caso | Curva | Token |
|---|---|---|
| Entrar o salir de pantalla | ease-out fuerte | `--curva-entrada` / `--curva-salida` |
| Moverse o transformarse en pantalla | ease-in-out fuerte, `cubic-bezier(0.77, 0, 0.175, 1)` | no existe: pedir token a `diseno` antes de usarla |
| Hover, cambio de color | `ease` | — |
| Movimiento constante (progreso, cinta) | `linear` | — |
| Por defecto | ease-out | `--curva-entrada` |

**Nunca `ease-in` en interfaz.** Las curvas de CSS de serie (`ease-out` a secas) son flojas para
una animación deliberada: usar las del token.

## 2. Duraciones

| Elemento | Duración | Token |
|---|---|---|
| Retroalimentación al pulsar | 100–160 ms | `--dur-micro` (150 ms) |
| Tooltips, popovers pequeños | 125–200 ms | `--dur-micro` |
| Desplegables | 150–250 ms | — |
| Hojas, diálogos | 200–500 ms | `--dur-transicion` (300 ms) |
| Explicativo o de campaña (reproducir la evolución, historia) | puede ser más largo | lo decide `diseno` |

Interfaz por debajo de 300 ms. Un desplegable a 180 ms se siente más rápido que a 400 ms.

## 3. Físico

- Nunca `scale(0)`: desde `scale(0.95–0.97)` + `opacity: 0`.
- Popovers, menús y tooltips crecen desde su disparador (`transform-origin` en el botón que los
  abre). Los diálogos centrados, desde el centro.
- Pulsar: `:active { transform: scale(0.97) }`, `transition: transform var(--dur-micro) var(--curva-entrada)`.
  Sutil: 0,95–0,98.
- Entradas en grupo (tarjetas, listas que se ven de vez en cuando): escalonado de 30–80 ms entre
  elementos. Nunca bloquea la interacción.
- Muelles (springs) para lo que se arrastra o se puede soltar a medias. Rebote bajo (0,1–0,3), y
  solo en gestos o momentos de deleite.

## 4. Interrupción y respuesta

- Transición CSS (se re-apunta a mitad) mejor que `@keyframes` (reinicia desde cero) para lo que
  se dispara a menudo: avisos, interruptores, pestañas.
- Nunca bloquear la entrada mientras algo anima.
- Lo que se arrastra sigue al dedo 1:1 y respeta desde dónde se agarró (`setPointerCapture`).
- Al soltar un gesto, decidir por velocidad y no solo por distancia (un golpe rápido basta:
  `|distancia| / ms > ~0,11`).
- Entrada sin JavaScript: `@starting-style`.

## 5. Rendimiento

- Solo `transform` y `opacity`.
- No mover hijos cambiando una variable CSS del padre: recalcula estilos de todos los hijos. Se
  pone el `transform` en el propio elemento.
- Movimiento predeterminado en CSS (va fuera del hilo principal); JavaScript solo para lo
  dinámico o interrumpible (o WAAPI, `element.animate`, que da ambas cosas).

## 6. Accesibilidad

- `prefers-reduced-motion: reduce` → quitar desplazamientos y escalas, dejar los cambios de
  opacidad o color que ayudan a entender. Menos, no cero. Los tokens de /mapa-metastasis ya lo
  hacen poniendo la duración a 0: vale como mínimo.
- Hover con movimiento, siempre dentro de `@media (hover: hover) and (pointer: fine)`.

## 7. Alertas: señalar en cuanto se vean

- `transition: all`
- Entradas desde `scale(0)` o solo con fundido, sin transformación inicial
- `ease-in` en cualquier interacción; curva de serie floja en una animación deliberada
- Animación en algo que se usa a todas horas o que se lanza con el teclado
- Más de 300 ms en interfaz sin motivo escrito
- `transform-origin: center` en un popover que sale de un botón
- `@keyframes` en avisos, interruptores o lo que se dispara seguido
- Animar alto, ancho, márgenes, `top` o `left`
- Variable CSS en el padre para mover hijos
- Movimiento sin tratamiento de `prefers-reduced-motion`
- `:hover` con movimiento sin la media query de hover
- Entrada de un grupo todo a la vez donde tocaba escalonar

## 8. Móvil: tabla de síntomas

| Síntoma | Arreglo | Por qué |
|---|---|---|
| El hover se queda pegado tras tocar | `@media (hover: hover) and (pointer: fine)` alrededor de todo `:hover` | El móvil finge un hover en el primer toque y no lo suelta |
| Destello gris o azul al tocar | `html { -webkit-tap-highlight-color: transparent }` + un `:active` propio en todo lo tocable | Es la señal más clara de «esto es una web» |
| La altura está mal | `100svh` en portadas, `100dvh` en lo que ocupa la pantalla y se ancla abajo | `100vh` en el móvil es la altura con la barra del navegador escondida |
| La página hace zoom al entrar en un input | `font-size: 16px` mínimo en `input`, `textarea`, `select` | iOS amplía con menos de 16 px y no vuelve |
| El toque va con retraso | `touch-action: manipulation` en botones y enlaces, y respuesta en `:active` | El navegador espera por si es doble toque |
| Arrastrar hacia abajo recarga la página | `overscroll-behavior: contain` en los contenedores con scroll propio | **No** poner `none` en `html`: helptitular es un documento, recargar al tirar es lo normal |
| El contenido choca con el notch | `viewport-fit=cover` + `env(safe-area-inset-*)` en barras fijas | Sin la meta, `env()` vale 0 |
| Mantener pulsado selecciona el texto del botón | `user-select: none` **solo en controles** | En el texto de lectura es un defecto |
| El carrusel mueve la página en vertical | `touch-action: pan-y` en la superficie del carrusel, o `scroll-snap` nativo | El navegador no sabe qué eje es de quién |
| La barra de estado no casa con la página | un `theme-color` por esquema de color | Hoy hay uno solo (`#faf6f0`, `nuxt.config.ts`); solo importa si hay modo oscuro |
| Bien en Chrome, mal en el teléfono | Probar en un teléfono de verdad (Safari → Desarrollo; `chrome://inspect`) | Nada de esta tabla se reproduce emulado |

Teclado del móvil: `inputmode="numeric"` o `"decimal"` para cifras, `type="email"`/`"tel"`,
`autocapitalize="none"` en códigos, `enterkeyhint` para que la tecla de intro diga lo que hace.
