// Captura un visor 3D de helptitular.com con sus anillos y le QUEMA la leyenda dentro de la
// propia caja, para que la imagen de respaldo no viaje desnuda.
//
// Por qué existe: la imagen de respaldo del hígado (la que se ve si el visor no arranca, la que
// sale en una captura compartida o en la vista previa de un enlace) enseñaba una veintena de
// bultos iguales, sin anillo ni leyenda; y cuando el visor falla, la leyenda de debajo TAMPOCO
// se pinta. Dos lesiones tienen medida de radiólogo y el resto es detección automática: la
// imagen tiene que decirlo sola. Rehacerlo a mano en cada estudio nuevo era el coste que esto
// evita (misma lección que el pipeline de visor3d.py).
//
// No saca nada del Mac: carga una página YA PÚBLICA y captura lo que pinta el navegador.
//
// Uso:  node tools/captura_visor.mjs <url> <salida.png>
// Luego, a 1000x1000 y webp:  (Pillow) redimensionar + cwebp -q 86
import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'

const [url, salida] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const puerto = 9400 + Math.floor(Math.random() * 500)
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${puerto}`,
  '--use-angle=metal', '--ignore-gpu-blocklist', '--hide-scrollbars',
  '--window-size=1400,1600', '--force-device-scale-factor=2',
  `--user-data-dir=/tmp/cap-${puerto}`, 'about:blank'], { stdio: 'ignore' })
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))

let ws
for (let i = 0; i < 60 && !ws; i++) {
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
ws.addEventListener('message', (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
})
const cmd = (method, params = {}) => new Promise((res, rej) => {
  const n = ++id
  pend.set(n, (m) => (m.error ? rej(new Error(method + ': ' + m.error.message)) : res(m.result)))
  ws.send(JSON.stringify({ id: n, method, params }))
})
const ev = async (expression) => {
  const r = await cmd('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text)
  return r.result.value
}

await cmd('Page.enable')
await cmd('Runtime.enable')
await cmd('Page.navigate', { url })
await dormir(6000)

// Esperar a que el visor del hígado tenga sus dos anillos pintados
let listo = false
for (let i = 0; i < 60 && !listo; i++) {
  listo = await ev(`document.querySelectorAll('.lv-anillo').length >= 2`)
  if (!listo) await dormir(1000)
}
if (!listo) { console.error('los anillos no aparecieron'); ws.close(); chrome.kill(); process.exit(1) }
await dormir(2500)

// Quemar la leyenda DENTRO de la caja, con la tipografía del propio sitio
const rect = await ev(`(() => {
  const caja = document.querySelector('.lv-caja');
  const raiz = caja.parentElement;
  const btn = raiz.querySelector('.lv-reencuadre'); if (btn) btn.style.display = 'none';
  const lis = [...raiz.querySelectorAll('ul li')].map(li => li.textContent.trim())
    .filter(t => /diana|automática/i.test(t));
  const dianas = lis.find(t => /diana/i.test(t)) || '';
  const resto  = lis.filter(t => /automática/i.test(t));
  const pie = document.createElement('div');
  pie.style.cssText = 'position:absolute;left:0;right:0;bottom:0;padding:16px 18px 18px;' +
    'background:linear-gradient(to top, rgba(18,10,25,.94), rgba(18,10,25,.78) 62%, rgba(18,10,25,0));' +
    'color:#f5efe6;font-size:15px;line-height:1.45;pointer-events:none;' +
    'font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;';
  const fila = (color, borde, texto) =>
    '<div style="display:flex;align-items:flex-start;gap:8px;margin-top:3px">' +
    '<span style="width:11px;height:11px;margin-top:5px;flex:0 0 auto;border-radius:50%;background:' + color +
    (borde ? ';box-shadow:0 0 0 2px #f5efe6' : '') + '"></span><span>' + texto + '</span></div>';
  pie.innerHTML =
    fila('#f2b23c', true, '<b>' + dianas + '</b>') +
    resto.map(t => fila(/menos de 10/.test(t) ? '#7c5cf0' : '#f2b23c', false, t)).join('') +
    '<div style="margin-top:7px;opacity:.85;font-size:13px">helptitular.com/lesiones · TC del 8 de septiembre de 2026</div>';
  caja.appendChild(pie);
  const r = caja.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
})()`)

await dormir(600)
const shot = await cmd('Page.captureScreenshot', {
  format: 'png', captureBeyondViewport: true,
  clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 2 },
})
writeFileSync(salida, Buffer.from(shot.data, 'base64'))
console.log('ok', salida, Math.round(rect.w) + 'x' + Math.round(rect.h), '(x2)')
ws.close(); chrome.kill()
