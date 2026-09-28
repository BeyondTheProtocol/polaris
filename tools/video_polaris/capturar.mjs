// Fotos fijas de páginas PÚBLICAS para los cortes rápidos del vídeo (bloque 0:17→0:23).
// Uso: node capturar.mjs <carpeta_salida>
// Cada toma: página, y del ancla (texto de un encabezado) y margen por encima.
import { abrir, dormir } from './cdp.mjs'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const [dir] = process.argv.slice(2)
mkdirSync(dir, { recursive: true })
const TOMAS = [
  { n: 'biopsia', url: 'https://helptitular.com/en/bone-biopsy', ancla: 'What can be measured', margen: 40 },
  { n: 'datos-cielo', url: 'https://helptitular.com/en/data', ancla: 'The case, seen another way', margen: 40 },
  { n: 'repo', url: 'https://github.com/BeyondTheProtocol/polaris', ancla: null, margen: 0 },
  // «two sides» ({{TITULAR}}, 28-sep): el esquema del tumor de /science, TAL CUAL (captura, nunca redibujado: sus cifras son las de la web)
  { n: 'dos-caras', url: 'https://helptitular.com/en/science', svgCon: 'HR+', escala: 2 },
  // vídeo con historia (28-sep)
  { n: 'science', url: 'https://helptitular.com/en/science', ancla: null, margen: 0 },  // sin scroll: la cabecera fija tapa el título
  { n: 'home', url: 'https://helptitular.com/en', ancla: null, margen: 0 },
  { n: 'esqueleto', url: 'https://helptitular.com/en/mapa-metastasis', ancla: 'The map, lesion by lesion', margen: 20 },
]
const b = await abrir({ w: 1920, h: 1080 })
for (const t of TOMAS.filter((x) => x.svgCon)) {
  const b2 = await abrir({ w: 1920, h: 1080, escala: t.escala || 1 })
  await b2.ir(t.url, 8000)
  const r = await b2.ev(`(() => { const s = [...document.querySelectorAll('svg')].find(s => s.textContent.includes(${JSON.stringify(t.svgCon)}))
    s.scrollIntoView({ block: 'center', behavior: 'instant' }); const q = s.getBoundingClientRect(); return { x: q.x - 10, y: q.y - 10, width: q.width + 20, height: q.height + 20 } })()`)
  await dormir(1500)
  const q = await b2.ev(`(() => { const s = [...document.querySelectorAll('svg')].find(s => s.textContent.includes(${JSON.stringify(t.svgCon)})); const q = s.getBoundingClientRect(); return { x: q.x - 10, y: q.y + scrollY - 10, width: q.width + 20, height: q.height + 20 } })()`)  // clip va en coordenadas de documento
  const img = Buffer.from((await b2.cmd('Page.captureScreenshot', { format: 'png', clip: { ...q, scale: 1 } })).data, 'base64')
  writeFileSync(join(dir, t.n + '.png'), img); console.log('✓', t.n, 'svg', Math.round(q.width), '×', Math.round(q.height))
  b2.cerrar()
}
for (const t of TOMAS.filter((x) => !x.svgCon)) {
  await b.ir(t.url, 7000)
  const y = t.ancla === null ? 0 : await b.ev(`(() => { const h = [...document.querySelectorAll('h1,h2')].find(h => h.textContent.includes(${JSON.stringify(t.ancla)}));
    if (!h) return -1; const y = h.getBoundingClientRect().top + scrollY - ${t.margen}; scrollTo(0, y); return Math.round(y) })()`)
  if (y < 0) { console.error('⚠️ sin ancla', t.n); continue }
  await dormir(2500)
  writeFileSync(join(dir, t.n + '.png'), await b.foto())
  console.log('✓', t.n, 'y=', y)
}
b.cerrar()
