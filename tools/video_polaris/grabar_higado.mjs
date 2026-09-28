// Graba SOLO el lienzo del visor del hígado de helptitular.com/en/lesiones, girando solo.
// Uso: node grabar_higado.mjs <carpeta> [segundos=8] [fps=30] [tarjeta=Liver]   (tarjeta: «Liver» o «Right breast», el título del visor en /en/lesiones)
// En la grabación (la web no se toca) se ocultan:
//   .lv-rotulo  rótulos con medidas y SUV: en este vídeo no sale ninguna cifra clínica (diseño, 27-sep)
//   botones     el de girar, que en un vídeo lee como interfaz rota
// La mama NO sale: su cifra está reservada para la segunda ola de la campaña de visores.
import { abrir, dormir } from './cdp.mjs'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [dir, seg = '8', fps = '30', TARJETA = 'Liver', URL = 'https://helptitular.com/en/lesiones'] = process.argv.slice(2)
// TARJETA «#N» = el N-ésimo lienzo grande (>600 px) de la página, para visores sin título de tarjeta (la vértebra de /mapa-metastasis)
mkdirSync(dir, { recursive: true })
const b = await abrir({ w: 1920, h: 1080, escala: 2 })
await b.ir(URL, 4000)
let listo = false
for (let i = 0; i < 60 && !listo; i++) { await dormir(500)
  try { listo = await b.ev(TARJETA.startsWith('#') ? `[...document.querySelectorAll('canvas')].filter(c => c.getBoundingClientRect().width > 600).length > ${Number(TARJETA.slice(1))}` : `document.querySelectorAll('.lv-anillo').length >= 2`) } catch {} }
if (!listo) { console.error('el visor no arrancó'); b.cerrar(); process.exit(1) }
await b.ev(`(() => {
  const c = ${JSON.stringify(TARJETA)}.startsWith('#') ? [...document.querySelectorAll('canvas')].filter(c => c.getBoundingClientRect().width > 600)[${Number(TARJETA.slice(1)) || 0}]
    : [...document.querySelectorAll('canvas')].find(c => c.closest('section,article,div[class*=card]')?.querySelector('h2,h3')?.textContent?.trim() === ${JSON.stringify(TARJETA)})
  window.__lienzo = c
  scrollTo({ top: c.getBoundingClientRect().top + scrollY - 200, behavior: 'instant' })
})()`)
await dormir(1500)
const r = await b.ev(`(() => {
  const q = window.__lienzo.getBoundingClientRect()
  document.querySelectorAll('[class$="-rotulo"], [class*="-rotulo "]').forEach(e => e.style.visibility = 'hidden')
  // regla CSS (no un barrido único): también oculta lo que aparece DESPUÉS, como la sonda con SUV al pasar el ratón (btv-probe)
  const st = document.createElement('style')
  st.textContent = '[class$="-rotulo"],[class*="-rotulo "],[class$="-probe"],[class*="-probe "],[class*="-probe-"]{visibility:hidden!important}'
  document.head.appendChild(st)
  for (const e of document.querySelectorAll('button')) {
    const k = e.getBoundingClientRect()
    if (k.x >= q.x && k.right <= q.right && k.y >= q.y && k.bottom <= q.bottom) e.style.visibility = 'hidden'
  }
  return { x: q.x, y: q.y, w: q.width, h: q.height }
})()`)
await dormir(800)
const frames = []; let grabando = false, t0 = 0
b.alFotograma((m) => { if (grabando) frames.push({ t: Date.now() - t0, data: m.data }) })
await b.cmd('Page.startScreencast', { format: 'jpeg', quality: 95, maxWidth: 3840, maxHeight: 2160, everyNthFrame: 1 })
await dormir(700); t0 = Date.now(); grabando = true
if (process.env.ARRASTRE) {
  // «gíralo manualmente» ({{TITULAR}}, 28-sep): arrastre del ratón sobre el primer tercio del lienzo mientras se graba
  const cx = r.x + r.w / 6, cy = r.y + r.h / 2, pasos = Math.round(Number(seg) * 30), dx = Number(process.env.ARRASTRE)
  await b.cmd('Input.dispatchMouseEvent', {type: 'mousePressed', x: cx, y: cy, button: 'left', clickCount: 1})
  for (let k = 1; k <= pasos; k++) {
    const u = k / pasos, x = cx + dx * Math.sin(u * Math.PI) * (u < 0.5 ? 1 : 1), y = cy + 30 * Math.sin(u * Math.PI * 2)
    await b.cmd('Input.dispatchMouseEvent', {type: 'mouseMoved', x, y, button: 'left', buttons: 1})
    await dormir(1000 / 30)
  }
  await b.cmd('Input.dispatchMouseEvent', {type: 'mouseReleased', x: cx, y: cy, button: 'left', clickCount: 1})
  await dormir(500)
} else await dormir(Number(seg) * 1000 + 500)
grabando = false
await b.cmd('Page.stopScreencast')
const N = Number(seg) * Number(fps); let j = 0
for (let i = 0; i < N; i++) {
  const obj = (i * 1000) / Number(fps)
  while (j + 1 < frames.length && Math.abs(frames[j + 1].t - obj) <= Math.abs(frames[j].t - obj)) j++
  writeFileSync(join(dir, String(i + 1).padStart(4, '0') + '.jpg'), Buffer.from(frames[j].data, 'base64'))
}
writeFileSync(join(dir, 'recorte.json'), JSON.stringify(r))
console.log('capturados', frames.length, '→ escritos', N, 'recorte css', r)
b.cerrar()
