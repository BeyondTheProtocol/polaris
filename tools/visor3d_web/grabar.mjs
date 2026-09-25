// Graba video.html fotograma a fotograma con Chrome sin interfaz (CDP, WebSocket nativo de
// Node): node grabar.mjs <url> <carpeta_png> [fotogramas=360] [ancho=1080] [alto=1920]
// Sin dependencias: solo Chrome del sistema. No sale nada del Mac (la URL es 127.0.0.1).
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [url, dir, nF = '360', ancho = '1080', alto = '1920'] = process.argv.slice(2)
if (!/^http:\/\/127\.0\.0\.1:\d+\//.test(url || '')) { console.error('solo URLs de 127.0.0.1'); process.exit(2) }
mkdirSync(dir, { recursive: true })
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const puerto = 9300 + Math.floor(Math.random() * 500)
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${puerto}`, '--use-angle=metal',
  '--ignore-gpu-blocklist', '--hide-scrollbars', `--window-size=${ancho},${alto}`,
  `--user-data-dir=/tmp/grabar-${puerto}`, 'about:blank'], { stdio: 'ignore' })
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))

let ws
for (let i = 0; i < 50 && !ws; i++) {
  await dormir(200)
  try {
    const t = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json()
    const pag = t.find((x) => x.type === 'page')
    if (pag) ws = new WebSocket(pag.webSocketDebuggerUrl)
  } catch {}
}
if (!ws) { chrome.kill(); console.error('Chrome no arrancó'); process.exit(1) }
await new Promise((r) => ws.addEventListener('open', r, { once: true }))
let id = 0
const pend = new Map()
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
})
const cdp = (method, params = {}) => new Promise((r) => { const k = ++id; pend.set(k, r); ws.send(JSON.stringify({ id: k, method, params })) })
const evalua = async (expr) => (await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value

await cdp('Emulation.setDeviceMetricsOverride', { width: +ancho, height: +alto, deviceScaleFactor: 1, mobile: false })
await cdp('Page.navigate', { url })
for (let i = 0; i < 150; i++) {
  await dormir(200)
  if (await evalua('window.__listo === true')) break
  const err = await evalua('window.__error || null')
  if (err) { console.error('error en la página:', err); chrome.kill(); process.exit(1) }
}
if (!(await evalua('window.__listo === true'))) { console.error('la página no cargó'); chrome.kill(); process.exit(1) }
await dormir(800)
const N = +nF
for (let f = 0; f < N; f++) {
  // El fotograma se lee del propio lienzo (toDataURL tras dibujar): la captura de pantalla
  // del compositor sin interfaz devolvía siempre el primer fotograma (vídeo que no giraba, 19-sep).
  const png = await evalua(`window.__fotograma(${f / N})`)
  if (typeof png !== 'string' || !png.startsWith('data:image/png;base64,')) {
    console.error('la página no devolvió el fotograma ' + f); chrome.kill(); process.exit(1)
  }
  writeFileSync(join(dir, `f${String(f).padStart(4, '0')}.png`), Buffer.from(png.split(',')[1], 'base64'))
}
ws.close()
chrome.kill()
console.log(`grabados ${N} fotogramas`)
