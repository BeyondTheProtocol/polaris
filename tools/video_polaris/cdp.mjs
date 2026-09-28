// Chrome headless por CDP, lo mínimo que usan capturar.mjs y grabar.mjs.
// Mismo patrón que tools/visor_video_x/grabar_web.mjs, sin dependencias.
import { spawn } from 'node:child_process'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
export const dormir = (ms) => new Promise((r) => setTimeout(r, ms))

export async function abrir({ w = 1920, h = 1080, escala = 1 } = {}) {
  const puerto = 9400 + Math.floor(Math.random() * 80)
  const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${puerto}`,
    '--use-angle=metal', '--ignore-gpu-blocklist', '--hide-scrollbars', '--allow-file-access-from-files',
    `--window-size=${w},${h}`, `--force-device-scale-factor=${escala}`,
    `--user-data-dir=/tmp/vp-${puerto}`, 'about:blank'], { stdio: 'ignore' })
  let ws
  for (let i = 0; i < 80 && !ws; i++) {
    await dormir(200)
    try {
      const t = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json()
      const p = t.find((x) => x.type === 'page'); if (p) ws = new WebSocket(p.webSocketDebuggerUrl)
    } catch {}
  }
  if (!ws) { chrome.kill(); throw new Error('Chrome no arrancó') }
  await new Promise((r) => ws.addEventListener('open', r, { once: true }))
  let id = 0; const pend = new Map(); let alFotograma = null
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return }
    if (m.method === 'Page.screencastFrame') {
      ws.send(JSON.stringify({ id: ++id, method: 'Page.screencastFrameAck', params: { sessionId: m.params.sessionId } }))
      if (alFotograma) alFotograma(m.params)
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
  await cmd('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: escala, mobile: false })
  const ir = async (url, espera = 3000) => { await cmd('Page.navigate', { url }); await dormir(espera) }
  const foto = async (fmt = 'png', clip = null) => Buffer.from((await cmd('Page.captureScreenshot', { format: fmt, ...(fmt === 'jpeg' ? { quality: 92 } : {}), ...(clip ? { clip: { ...clip, scale: 1 } } : {}) })).data, 'base64')
  const cerrar = () => { try { ws.close() } catch {} chrome.kill() }
  return { cmd, ev, ir, foto, cerrar, alFotograma: (f) => { alFotograma = f } }
}
