// Vídeo vertical 1080×1920 del bloque de la mama de helptitular.com/lesiones, fotograma a
// fotograma. Mismo método que el del hígado: Chrome sin interfaz con Metal, y requestAnimationFrame
// sustituido por una cola que se avanza a mano — así el giro sale fluido y determinista aunque
// capturar cada fotograma tarde más que un fotograma real.
// Graba de la web PÚBLICA: lo que entra aquí ya está publicado, no sale nada del muro.
import { spawn } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
const [url, dir, segStr] = process.argv.slice(2)
const SEG = Number(segStr || 14), FPS = 30
mkdirSync(dir, { recursive: true })
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless=new', '--remote-debugging-port=9361', '--use-angle=metal', '--enable-gpu',
   '--hide-scrollbars', '--user-data-dir=/tmp/cdp-mama-video', 'about:blank'], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
let ws
for (let i = 0; i < 60 && !ws; i++) {
  try { const t = await (await fetch('http://127.0.0.1:9361/json')).json()
    const p = t.find((x) => x.type === 'page'); if (p) ws = p.webSocketDebuggerUrl } catch {}
  await sleep(200)
}
const s = new WebSocket(ws); await new Promise((r) => { s.onopen = r })
let id = 0; const pend = {}
s.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend[d.id]) { pend[d.id](d.result); delete pend[d.id] } }
const cdp = (method, params = {}) => new Promise((r) => { pend[++id] = r; s.send(JSON.stringify({ id, method, params })) })
const ev = async (e) => (await cdp('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result?.value

await cdp('Page.addScriptToEvaluateOnNewDocument', { source: `
  (()=>{ let q=[], n=0;
    window.requestAnimationFrame=(cb)=>{q.push([++n,cb]); return n};
    window.cancelAnimationFrame=(k)=>{q=q.filter(x=>x[0]!==k)};
    window.__tick=(k)=>{for(let i=0;i<k;i++){const c=q; q=[]; const t=performance.now(); c.forEach(([,cb])=>{try{cb(t)}catch(e){}})}};
    window.__manual=false; setInterval(()=>{ if(!window.__manual) window.__tick(1) },16);
  })()` })
// 405 × 720 a DPR 2,667 = 1080 × 1920 reales
await cdp('Emulation.setDeviceMetricsOverride', { width: 405, height: 720, deviceScaleFactor: 1080 / 405, mobile: true })
await cdp('Page.navigate', { url }); await sleep(12000)
// que el visor de la mama cargue de verdad antes de empezar
await ev("document.querySelector('.bv-caja')?.scrollIntoView({block:'center'})"); await sleep(10000)
const listo = await ev("!!document.querySelector('.bv-caja canvas')")
if (!listo) { console.error('el visor de la mama no ha cargado'); process.exit(1) }
// La cabecera pegajosa se come la parte de arriba del vídeo vertical, y en un Reel eso es
// espacio caro: fuera mientras se graba. Y un zoom para que el visor llene más cuadro: a
// tamaño normal ocupaba el 48 % del alto, que para redes es poco.
await ev(`(()=>{const h=document.querySelector('header'); if(h) h.style.display='none';
  document.body.style.zoom='1.35';})()`)
await sleep(2500)
// encuadre: la tarjeta entera, centrada en el alto del vídeo
const y0 = await ev(`(()=>{const c=document.querySelector('.bv-caja').getBoundingClientRect();
  return Math.max(0, scrollY + c.top - (innerHeight - c.height)/2)})()`)
await ev(`window.scrollTo(0, ${y0})`); await sleep(1500)
await ev('window.__manual=true')
let f = 0
const total = SEG * FPS
for (let i = 0; i < total; i++) {
  await ev('window.__tick(2)')                       // 2 ticks = 60 actualizaciones/s a 30 fps
  const r = await cdp('Page.captureScreenshot', { format: 'jpeg', quality: 92 })
  writeFileSync(`${dir}/f${String(f++).padStart(4, '0')}.jpg`, Buffer.from(r.data, 'base64'))
  if (i % 60 === 0) console.log(`  ${i}/${total}`)
}
console.log('fotogramas:', f)
chrome.kill(); process.exit(0)
