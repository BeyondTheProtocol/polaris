// Graba una herramienta de helptitular.com como un plano de cámara: fotograma a fotograma (no en tiempo real), así el
// movimiento sale parejo aunque Chrome vaya lento. Vídeo EN v3 para Lexie (17-oct-2026).
// Uso: node grabar_herramienta.mjs planos/<vídeo>.json <id> <carpeta>
// Plan por herramienta:
//   url, seg, fps                      la página y la duración
//   ocultar: [css]                     se ocultan en la grabación (la web no se toca): cifras clínicas, fechas de informes…
//   antes: [{click|eval, espera}]      lo que se hace antes de grabar
//   camara: [{t, x, y, w}]             encuadre en px CSS de la página (16:9; h = w·9/16), interpolado con suavizado
//   arrastres: [{t0, t1, x, y, dx, dy}] arrastre del ratón (girar un visor 3D) en px CSS de la página
//   clics: [{t, txt}]                  pulsa el botón con ese texto en el segundo t
//   mostrar: "<js>"                    expresión que devuelve los elementos que se ven; el resto de la página se vacía
//   sin_cifras (por defecto true)      oculta todo texto con un dígito (DOM; lo que va pintado en un canvas, no: hoja de contactos)
// Siempre se ocultan la barra «Latest», la cabecera, el banner de cookies y los rótulos con medidas de los visores.
import { abrir, dormir } from './cdp.mjs'
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const [planF, id, dir] = process.argv.slice(2)
const P = JSON.parse(readFileSync(planF, 'utf8'))[id]
if (!P) { console.error('no hay plan para', id); process.exit(1) }
mkdirSync(dir, { recursive: true })
const OCULTAR_SIEMPRE = ['.latest-bar', 'header', '[class*="cookie"]', '[id*="cookie"]', '[class*="consent"]',
  '[class$="-rotulo"]', '[class*="-rotulo "]', '[class$="-probe"]', '[class*="-probe "]', '[class*="-probe-"]']
const fps = P.fps || 30, N = Math.round(P.seg * fps), DPR = P.dpr || 2  // dpr alto para encuadres muy cerrados
const b = await abrir({ w: 1920, h: 1080, escala: DPR })
await b.ir(P.url, 6000)
await b.ev(`(() => { const st = document.createElement('style');
  st.textContent = ${JSON.stringify([...OCULTAR_SIEMPRE, ...(P.ocultar || [])].join(',') + '{visibility:hidden!important}')}
    + '.latest-bar,header{display:none!important} html{scroll-behavior:auto!important}';
  document.head.appendChild(st) })()`)
if (P.mostrar) await b.ev(`(() => { const keep = (${P.mostrar}).filter(Boolean); if (!keep.length) throw new Error('mostrar: nada');
  const st = document.createElement('style'); st.textContent = 'body *{visibility:hidden!important} .vp-ve, .vp-ve *{visibility:visible!important}'
  document.head.appendChild(st); keep.forEach(e => e.classList.add('vp-ve')) })()`)
if (P.sin_cifras !== false) await b.ev(`(() => { const tapa = () => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n; (n = w.nextNode());) if (/\\d/.test(n.nodeValue) && n.parentElement && !n.parentElement.closest('svg text, script, style'))
      n.parentElement.style.setProperty('visibility', 'hidden', 'important')
    document.querySelectorAll('svg text').forEach(t => { if (/\\d/.test(t.textContent)) t.style.setProperty('visibility', 'hidden', 'important') }) }
  tapa(); new MutationObserver(tapa).observe(document.body, { subtree: true, childList: true, characterData: true }) })()`)
await dormir(1500)
for (const a of P.antes || []) {
  if (a.eval) await b.ev(a.eval)
  if (a.click) await b.ev(`(() => { const e = [...document.querySelectorAll('button,a,[role=button]')].find(e => e.textContent.trim() === ${JSON.stringify(a.click)}); if (!e) throw new Error('no está: ${a.click}'); e.click() })()`)
  await dormir(a.espera || 800)
}
const suave = (u) => u * u * (3 - 2 * u)
const cam = (t) => {
  const k = P.camara; if (t <= k[0].t) return k[0]; if (t >= k.at(-1).t) return k.at(-1)
  let i = 0; while (t > k[i + 1].t) i++
  const u = suave((t - k[i].t) / (k[i + 1].t - k[i].t)), m = (a, c) => a + (c - a) * u
  return { x: m(k[i].x, k[i + 1].x), y: m(k[i].y, k[i + 1].y), w: m(k[i].w, k[i + 1].w) }
}
let pulsado = null; const hechos = new Set()
for (let f = 0; f < N; f++) {
  const t = f / fps, c = cam(t), h = c.w * 9 / 16
  for (const k of P.clics || []) if (t >= k.t && !hechos.has(k)) { hechos.add(k)
    await b.ev(`(() => { const e = [...document.querySelectorAll('button,a,[role=button]')].find(e => e.textContent.trim() === ${JSON.stringify(k.txt)}); if (!e) throw new Error('no está: ' + ${JSON.stringify(k.txt)}); e.click() })()`) }
  // desplaza la página para que el encuadre quede en la ventana (el WebGL solo pinta lo visible); el recorte va en
  // coordenadas del DOCUMENTO (CDP), los eventos de ratón en las de la ventana
  const sy = Math.max(0, Math.round(c.y - (1080 - h) / 2))
  const real = await b.ev(`(scrollTo({ top: ${sy}, behavior: 'instant' }), scrollY)`)
  for (const a of P.arrastres || []) {
    const dentro = t >= a.t0 && t <= a.t1, u = dentro ? suave((t - a.t0) / (a.t1 - a.t0)) : 0
    const mx = a.x + (a.dx || 0) * u, my = a.y - real + (a.dy || 0) * u
    if (dentro && pulsado !== a) { await b.cmd('Input.dispatchMouseEvent', { type: 'mousePressed', x: a.x, y: a.y - real, button: 'left', buttons: 1, clickCount: 1 }); pulsado = a }
    if (dentro) await b.cmd('Input.dispatchMouseEvent', { type: 'mouseMoved', x: mx, y: my, button: 'left', buttons: 1 })
    if (!dentro && pulsado === a) { await b.cmd('Input.dispatchMouseEvent', { type: 'mouseReleased', x: a.x + (a.dx || 0), y: a.y - real + (a.dy || 0), button: 'left', clickCount: 1 }); pulsado = null }
  }
  await b.ev('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
  const img = Buffer.from((await b.cmd('Page.captureScreenshot', { format: 'jpeg', quality: 93,
    clip: { x: c.x, y: c.y, width: c.w, height: h, scale: 1920 / (c.w * DPR) } })).data, 'base64')
  writeFileSync(join(dir, String(f + 1).padStart(4, '0') + '.jpg'), img)
}
console.log(id, 'escritos', N, 'fotogramas →', dir)
b.cerrar()
