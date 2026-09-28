// Captura el núcleo animado del Mapa de la Anatomía (cara PÚBLICA) como bucle de 4 s, fotograma a fotograma.
// Uso: python3 tools/anatomia_mapa.py render --cara publica > mapa.html ; node capturar_mapa.mjs mapa.html <carpeta> [fps=30]
// Solo sale el círculo (anillos, candados del muro, orquestador): el texto del mapa está en español y lleva
// datos internos (caídas, la cadena clínica), así que se deja fuera del recorte. La etiqueta central la tapa
// historia.html con la suya en inglés.
import { abrir, dormir } from './cdp.mjs'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const [html, dir, fps = '30'] = process.argv.slice(2)
mkdirSync(dir, { recursive: true })
const b = await abrir({ w: 1400, h: 1320 })
await b.ir('file://' + resolve(html), 3000)
await b.ev(`(() => { const s = document.createElement('style'); s.textContent = '*{animation-play-state:paused!important}'; document.head.appendChild(s) })()`)
const N = 4 * Number(fps)
for (let i = 0; i < N; i++) {
  await b.ev(`document.documentElement.style.setProperty('--t', '${(i / Number(fps)).toFixed(4)}s')`)
  await dormir(15)
  // solo el círculo del sistema (x 310-1090, y 230-1010 en el mapa de 1400×1320)
  writeFileSync(join(dir, String(i + 1).padStart(4, '0') + '.jpg'), await b.foto('jpeg', { x: 310, y: 230, width: 780, height: 780 }))
}
console.log('✓', N, 'fotogramas del mapa en', dir)
b.cerrar()
