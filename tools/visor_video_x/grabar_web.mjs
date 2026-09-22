// Graba la WEB VIVA por screencast de CDP (muchos más fotogramas que captureScreenshot).
// Recorta después al bloque de los dos visores CON su contexto.
// Uso: node grabar-web2.mjs <url> <carpeta> [salidas=240] [vuelta_s=37.5]
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [url, dir, nOut = '240', vuelta = '37.5'] = process.argv.slice(2)
const VUELTA = Number(vuelta) * 1000, N = Number(nOut)
mkdirSync(dir, { recursive: true })
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const puerto = 9310 + Math.floor(Math.random() * 80)
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${puerto}`,
  '--use-angle=metal', '--ignore-gpu-blocklist', '--hide-scrollbars',
  '--window-size=1800,1500', '--force-device-scale-factor=2',
  `--user-data-dir=/tmp/gw2-${puerto}`, 'about:blank'], { stdio: 'ignore' })
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))
let ws
for (let i = 0; i < 80 && !ws; i++) {
  await dormir(200)
  try { const t = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json()
    const p = t.find((x) => x.type === 'page'); if (p) ws = new WebSocket(p.webSocketDebuggerUrl) } catch {}
}
if (!ws) { chrome.kill(); process.exit(1) }
await new Promise((r) => ws.addEventListener('open', r, { once: true }))
let id = 0; const pend = new Map(); const frames = []
let grabando = false; let t0 = 0
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return }
  if (m.method === 'Page.screencastFrame') {
    ws.send(JSON.stringify({ id: ++id, method: 'Page.screencastFrameAck', params: { sessionId: m.params.sessionId } }))
    if (grabando) frames.push({ t: Date.now() - t0, data: m.params.data })
  }
})
const cmd = (me, p = {}) => new Promise((res, rej) => { const n = ++id
  pend.set(n, (x) => (x.error ? rej(new Error(me + ': ' + x.error.message)) : res(x.result)))
  ws.send(JSON.stringify({ id: n, method: me, params: p })) })
const ev = async (x) => {
  const r = await cmd('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300))
  return r.result.value
}
await cmd('Page.enable'); await cmd('Runtime.enable')
await cmd('Page.navigate', { url })
let listo = false
for (let i = 0; i < 90 && !listo; i++) { await dormir(500)
  try { listo = await ev(`document.querySelectorAll('.lv-anillo').length >= 2 && document.querySelectorAll('canvas').length >= 2`) } catch {} }
if (!listo) { console.error('los visores no arrancaron'); ws.close(); chrome.kill(); process.exit(1) }
await dormir(1200)

// «Arrastra para girar · rueda para acercar» es una instrucción de algo que en un vídeo no se
// puede hacer: en la página informa, en el clip lee como interfaz rota (comité de diseño, 20-sep).
// Se oculta SOLO en la grabación; la web no se toca.
console.log('ocultados', await ev(`(() => {
  let n = 0
  for (const el of document.querySelectorAll('p')) {
    if (/^\\s*Arrastra para girar|^\\s*Drag to rotate/.test(el.textContent)) { el.style.visibility = 'hidden'; n++ }
  }
  return n
})()`), 'textos de interacción')

const rect = await ev(`(() => {
  const av = document.querySelector('.alert-callout').getBoundingClientRect()
  const gr = document.querySelector('.grid.gap-6').getBoundingClientRect()
  const m = 10
  return { x: Math.round(Math.min(av.x, gr.x) - m), y: Math.round(av.y - m),
           w: Math.round(Math.max(av.width, gr.width) + 2 * m),
           h: Math.round(gr.bottom - av.y + 2 * m), vw: innerWidth, vh: innerHeight }
})()`)
// El bloque de la web da 1,26:1, que no es una medida de publicación. Se ENSANCHA el recorte
// hasta 16:9 con la propia página a los lados (su fondo crema), sin deformar ni recortar nada.
const RATIO = 16 / 9
if (rect.w / rect.h < RATIO) {
  const w2 = Math.round(rect.h * RATIO)
  if (w2 <= rect.vw) {
    rect.x = Math.max(0, Math.min(rect.vw - w2, Math.round(rect.x + rect.w / 2 - w2 / 2)))
    rect.w = w2
  } else console.log('⚠️ no cabe 16:9 en el viewport:', w2, '>', rect.vw)
}
rect.w -= rect.w % 2; rect.h -= rect.h % 2
console.log('recorte css', rect, '→', (rect.w / rect.h).toFixed(3) + ':1')
if (rect.y + rect.h > rect.vh) console.log('⚠️ el bloque no cabe en el viewport:', rect.y + rect.h, '>', rect.vh)

await cmd('Page.startScreencast', { format: 'jpeg', quality: 95, maxWidth: 2880, maxHeight: 2800, everyNthFrame: 1 })
// El primer fotograma del screencast puede venir de un búfer anterior al retoque del DOM
// (el 20-sep salió con el texto de interacción que acababa de ocultar). Se descarta medio
// segundo antes de empezar a contar.
await dormir(700)
t0 = Date.now(); grabando = true
await dormir(VUELTA + 1000)
grabando = false
await cmd('Page.stopScreencast')
console.log('fotogramas capturados:', frames.length, '→', (frames.length / ((VUELTA + 1000) / 1000)).toFixed(1), 'fps')

let j = 0
for (let i = 0; i < N; i++) {
  const objetivo = (i * VUELTA) / N
  while (j + 1 < frames.length && Math.abs(frames[j + 1].t - objetivo) <= Math.abs(frames[j].t - objetivo)) j++
  writeFileSync(join(dir, String(i).padStart(4, '0') + '.jpg'), Buffer.from(frames[j].data, 'base64'))
}
writeFileSync(join(dir, 'recorte.json'), JSON.stringify(rect))
console.log('escritos', N, 'fotogramas')
ws.close(); chrome.kill()
