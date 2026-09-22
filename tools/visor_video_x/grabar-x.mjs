// Graba x.html fotograma a fotograma con Chrome sin interfaz. Solo 127.0.0.1.
// Uso: node grabar-x.mjs <url> <carpeta> [fotogramas=240]
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
const [url, dir, nF = '240'] = process.argv.slice(2)
if (!/^http:\/\/127\.0\.0\.1:\d+\//.test(url || '')) { console.error('solo 127.0.0.1'); process.exit(2) }
mkdirSync(dir, { recursive: true })
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const puerto = 9600 + Math.floor(Math.random() * 300)
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${puerto}`,
  '--use-angle=metal', '--ignore-gpu-blocklist', '--hide-scrollbars',
  '--window-size=1120,1160', '--force-device-scale-factor=1',
  `--user-data-dir=/tmp/gx-${puerto}`, 'about:blank'], { stdio: 'ignore' })
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))
let ws
for (let i = 0; i < 60 && !ws; i++) {
  await dormir(200)
  try {
    const t = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json()
    const p = t.find((x) => x.type === 'page'); if (p) ws = new WebSocket(p.webSocketDebuggerUrl)
  } catch {}
}
if (!ws) { chrome.kill(); console.error('Chrome no arrancó'); process.exit(1) }
await new Promise((r) => ws.addEventListener('open', r, { once: true }))
let id = 0; const pend = new Map()
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) } })
const cmd = (method, params = {}) => new Promise((res, rej) => {
  const n = ++id; pend.set(n, (m) => (m.error ? rej(new Error(method + ': ' + m.error.message)) : res(m.result)))
  ws.send(JSON.stringify({ id: n, method, params }))
})
const ev = async (expression) => {
  const r = await cmd('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400))
  return r.result.value
}
await cmd('Page.enable'); await cmd('Runtime.enable')
await cmd('Page.navigate', { url })
let listo = false
for (let i = 0; i < 120 && !listo; i++) { await dormir(500); try { listo = await ev('!!window.__listo') } catch {} }
if (!listo) { console.error('la escena no cargó'); ws.close(); chrome.kill(); process.exit(1) }
await dormir(1200)
const rect = await ev(`(() => { const r = document.getElementById('marco').getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height } })()`)
const N = Number(nF)
for (let i = 0; i < N; i++) {
  await ev(`window.__fotograma(${i / N})`)
  const s = await cmd('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 1 } })
  writeFileSync(join(dir, String(i).padStart(4, '0') + '.png'), Buffer.from(s.data, 'base64'))
  if (i % 40 === 0) process.stdout.write(i + ' ')
}
console.log('\nlisto', N, 'fotogramas', Math.round(rect.w) + 'x' + Math.round(rect.h))
ws.close(); chrome.kill()
