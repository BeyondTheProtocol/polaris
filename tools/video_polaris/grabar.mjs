// Renderiza polaris.html fotograma a fotograma (determinista: pinta(t), espera a las imágenes, foto).
// Uso: node grabar.mjs <build> [fps=30] [desde=0] [hasta=auto] [pagina=polaris.html]
//   hasta=auto: la duración la da la página (window.TIEMPOS.total en historia.html; 26 s en polaris.html)
// Deja <build>/frames/%05d.jpg. El montaje (audio + mp4) es montar.sh.
import { abrir } from './cdp.mjs'
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const [build, fps = '30', desde = '0', hasta = 'auto', pagina = 'polaris.html'] = process.argv.slice(2)
const aqui = dirname(fileURLToPath(import.meta.url))
copyFileSync(join(aqui, pagina), join(build, pagina))
const out = join(build, 'frames'); mkdirSync(out, { recursive: true })
const b = await abrir({ w: 1920, h: 1080 })
await b.ir('file://' + join(build, pagina), 2500)
await b.ev('window.listo')
const fin = hasta === 'auto' ? await b.ev('window.TIEMPOS ? window.TIEMPOS.total : DUR') : Number(hasta)
const i0 = Math.round(Number(desde) * Number(fps)), i1 = Math.round(fin * Number(fps))
for (let i = i0; i < i1; i++) {
  await b.ev(`(async () => { window.pinta(${i / Number(fps)}); await Promise.all([...document.images].filter(x => x.src).map(x => x.decode().catch(() => {}))) })()`)
  writeFileSync(join(out, String(i).padStart(5, '0') + '.jpg'), await b.foto('jpeg'))
  if (i % 150 === 0) console.log('fotograma', i, '/', i1)
}
console.log('✓', i1 - i0, 'fotogramas en', out)
b.cerrar()
