// Render de nivel «ilustración médica» del hígado para vídeo (three.js, la librería del mapa óseo).
//  · Hígado con translucidez Fresnel: bordes opacos que dan forma, centro claro que deja ver dentro.
//  · Luz de estudio: entorno PBR + clave cálida + relleno frío + contraluz de silueta.
//  · Sin bloom (a 2× dejaba halo e inflaba las lesiones); tone mapping ACES, supermuestreo ×ss.
//  · Composición final en un lienzo 2D: viñeta + (opcional) leyenda y aviso.
// ?fecha=AAAAMMDD_hd&ss=2&texto=1  ·  window.__listo, window.__fotograma(t) → dataURL PNG.
import * as THREE from 'three'
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'

const q = new URLSearchParams(location.search)
const carpeta = './' + q.get('fecha') + '/'
const SS = Number(q.get('ss') || 2)
const TEXTO = q.get('texto') === '1'
// ?lesiones=dianas: solo las lesiones con medida del radiólogo (páginas que publican solo el informe)
const SOLO_DIANAS = q.get('lesiones') === 'dianas'
const W = window.innerWidth, H = window.innerHeight
const PW = W * SS, PH = H * SS

const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
renderer.setPixelRatio(SS)
renderer.setSize(W, H)
renderer.outputColorSpace = THREE.SRGBColorSpace
renderer.toneMapping = THREE.ACESFilmicToneMapping
renderer.toneMappingExposure = 1.0
document.body.appendChild(renderer.domElement)

const scene = new THREE.Scene()
{
  const c = document.createElement('canvas'); c.width = 540; c.height = 960
  const g = c.getContext('2d')
  // Berenjena canónica del design system (#2d1b3d), con un halo casi imperceptible (diseno, 19-sep)
  const r = g.createRadialGradient(270, 360, 30, 270, 380, 620)
  r.addColorStop(0, '#33253f'); r.addColorStop(1, '#2d1b3d')
  g.fillStyle = r; g.fillRect(0, 0, 540, 960)
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace
  scene.background = t
}
const pmrem = new THREE.PMREMGenerator(renderer)
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture
scene.environmentIntensity = 0.9
const luz = (color, int, x, y, z) => { const l = new THREE.DirectionalLight(color, int); l.position.set(x, y, z); scene.add(l) }
luz(0xfff0dc, 1.7, 2.5, 3, 4)      // clave cálida
luz(0xb8c8ff, 0.6, -4, 0.5, 2)     // relleno frío
luz(0xffd9f0, 2.2, -1, 2, -5)      // contraluz: silueta

function fresnel(mat, min, max, pot) {
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uMin = { value: min }; sh.uniforms.uMax = { value: max }; sh.uniforms.uPot = { value: pot }
    sh.fragmentShader = 'uniform float uMin; uniform float uMax; uniform float uPot;\n' +
      sh.fragmentShader.replace('#include <opaque_fragment>',
        'float fr = pow(1.0 - abs(dot(normal, normalize(vViewPosition))), uPot);\n' +
        'diffuseColor.a = mix(uMin, uMax, fr);\n#include <opaque_fragment>')
  }
  return mat
}
const higadoMat = (lado) => fresnel(new THREE.MeshPhysicalMaterial({
  color: 0x9a3f2c, roughness: 0.38, clearcoat: 0.8, clearcoatRoughness: 0.22,
  sheen: 0.5, sheenRoughness: 0.5, sheenColor: new THREE.Color(0xe39a86),
  transparent: true, depthWrite: false, side: lado }), 0.10, 0.92, 2.4)
const vaso = (c) => new THREE.MeshPhysicalMaterial({ color: c, roughness: 0.28, clearcoat: 0.9, clearcoatRoughness: 0.15 })
const MAT = {
  // RECIST 1.1: ≥ 10 mm = medible; < 10 mm = no medible (sigue siendo enfermedad)
  // Además del color, textura distinta (daltonismo azul-amarillo): medibles brillantes, pequeñas mates
  lesion: new THREE.MeshPhysicalMaterial({ color: 0xead3a0, roughness: 0.32, clearcoat: 0.8,
    clearcoatRoughness: 0.1, emissive: 0x6b4516, emissiveIntensity: 0.22 }),
  lesionPequena: new THREE.MeshPhysicalMaterial({ color: 0xb8accb, roughness: 0.85, clearcoat: 0,
    emissive: 0x2a2238, emissiveIntensity: 0.12 }),
  porta: vaso(0x5236b0), vasos: vaso(0x2d63d6), vci: vaso(0x1f45a8),
  vesicula: fresnel(new THREE.MeshPhysicalMaterial({ color: 0x6f9a3a, roughness: 0.25, clearcoat: 1,
    transparent: true, depthWrite: false }), 0.35, 0.95, 2.0),
}

const RAS_A_THREE = new THREE.Matrix4().set(-1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1)
const grupo = new THREE.Group()
scene.add(grupo)
const camara = new THREE.PerspectiveCamera(26, W / H, 1, 5000)
const composer = new EffectComposer(renderer)
composer.addPass(new RenderPass(scene, camara))
composer.addPass(new OutputPass())

async function geo(url) {
  const g = await new PLYLoader().loadAsync(url)
  g.applyMatrix4(RAS_A_THREE); g.computeVertexNormals(); return g
}
function malla(g, mat, orden) { const m = new THREE.Mesh(g, mat); m.renderOrder = orden; grupo.add(m); return m }

// Lienzo final (composición + textos)
const final = document.createElement('canvas'); final.width = PW; final.height = PH
const fx = final.getContext('2d')
const LEYENDA = [['#ead3a0', 'medibles'], ['#b8accb', 'pequenas'], ['#5236b0', 'Vena porta'],
  ['#2d63d6', 'Vasos hepáticos'], ['#1f45a8', 'Vena cava inferior'], ['#6f9a3a', 'Vesícula']]
const DIANAS = []          // { malla, texto }
const ESCALA = { pxPorMm: 0, medibles: '', pequenas: '', rozan: '', anchoHigado: '' }
const cm = (mm) => (mm / 10).toFixed(1).replace('.', ',')
// Zonas seguras Reels/TikTok 2026 (diseno, 19-sep): arriba ~270 px, abajo ~672 px, lateral dcho
// ~140 px (iconos), izdo ~65 px. Todo el texto vive entre y=300 y y=1248 (en base 1080×1920);
// el hígado, en la franja 500-970, sin tocar rótulos (440) ni leyenda (desde 1022).
const ZONA = { arriba: 300, abajo: 1248, izq: 90, der: 140 }
const F_TIT = '"Fraunces", Georgia, serif', F_TXT = '"JetBrains Mono", Menlo, monospace'
const TINTA = (a) => `rgba(245,239,230,${a})`   // #F5EFE6, token crema del design system
function compone(fecha) {
  fx.drawImage(renderer.domElement, 0, 0, PW, PH)
  if (!TEXTO) return
  const u = PW / 1080
  const X0 = ZONA.izq * u, XD = PW - ZONA.der * u
  fx.textBaseline = 'alphabetic'
  fx.fillStyle = TINTA(0.97); fx.font = `600 ${50 * u}px ${F_TIT}`
  fx.fillText('Mi hígado en 3D', X0, 340 * u)
  fx.fillStyle = TINTA(0.82); fx.font = `400 ${24 * u}px ${F_TXT}`
  fx.fillText('Reconstruido desde mi TAC del ' + fecha, X0, 384 * u)
  // Dianas del radiólogo: anillo + línea guía + rótulo fijo por lado en la franja 430-480
  const p3 = new THREE.Vector3()
  for (const D of DIANAS) {
    D.malla.geometry.boundingSphere || D.malla.geometry.computeBoundingSphere()
    const bs = D.malla.geometry.boundingSphere
    p3.copy(bs.center).applyMatrix4(D.malla.matrixWorld).project(camara)
    const px = (p3.x + 1) / 2 * PW, py = (1 - p3.y) / 2 * PH
    const r = Math.max(14 * u, bs.radius * ESCALA.pxPorMm * 1.35)
    const izq = D.izq, lx = izq ? X0 : XD, ly = 440 * u
    fx.strokeStyle = TINTA(0.92); fx.lineWidth = 2.2 * u
    fx.beginPath(); fx.arc(px, py, r, 0, 2 * Math.PI); fx.stroke()
    const codo = lx + (izq ? 250 : -250) * u
    const ang = Math.atan2(ly - py, codo - px)
    fx.beginPath(); fx.moveTo(px + r * Math.cos(ang), py + r * Math.sin(ang))
    fx.lineTo(codo, ly); fx.lineTo(lx, ly); fx.stroke()
    fx.textAlign = izq ? 'left' : 'right'
    fx.fillStyle = TINTA(0.97); fx.font = `600 ${24 * u}px ${F_TXT}`
    fx.fillText(D.texto, lx, ly - 12 * u)
    fx.fillStyle = TINTA(0.8); fx.font = `400 ${19 * u}px ${F_TXT}`
    fx.fillText('medida del radiólogo', lx, ly + 26 * u)
    fx.textAlign = 'left'
  }
  // Leyenda compacta: 2 filas de lesiones a todo el ancho + anatomía en 2×2
  const punto = (c, x, y) => { fx.fillStyle = c; fx.beginPath(); fx.arc(x + 9 * u, y - 8 * u, 9 * u, 0, 2 * Math.PI); fx.fill() }
  let y = 1022 * u
  fx.font = `400 ${22 * u}px ${F_TXT}`
  for (const [c, t] of [['#ead3a0', ESCALA.medibles], ['#b8accb', ESCALA.pequenas]]) {
    punto(c, X0, y); fx.fillStyle = TINTA(0.92); fx.fillText(t, X0 + 30 * u, y); y += 36 * u
  }
  const ana = [['#5236b0', 'Vena porta'], ['#2d63d6', 'Vasos hepáticos'], ['#1f45a8', 'Vena cava'], ['#6f9a3a', 'Vesícula']]
  ana.forEach(([c, t], i) => {
    const x = X0 + (i % 2) * 420 * u, yy = y + Math.floor(i / 2) * 36 * u
    punto(c, x, yy); fx.fillStyle = TINTA(0.9); fx.fillText(t, x + 30 * u, yy)
  })
  y += 2 * 36 * u + 10 * u        // notas en 1176 y 1204; aviso en 1236 (límite seguro 1248)
  fx.fillStyle = TINTA(0.82); fx.font = `400 ${19 * u}px ${F_TXT}`
  fx.fillText('RECIST 1.1 solo mide lesiones de 10 mm o más · ' + ESCALA.rozan, X0, y)
  fx.fillText('Hígado de ' + ESCALA.anchoHigado + ' de ancho · medidas automáticas ±1 mm', X0, y + 28 * u)
  fx.fillStyle = TINTA(0.78)
  fx.fillText('Segmentación automática (IA, 100 % local) · sin validación radiológica', X0, y + 60 * u)
  // Barra de 1 cm calibrada en el plano del centro del hígado, fuera de la columna de iconos
  const bx = XD, by = y - 4 * u, largo = 10 * ESCALA.pxPorMm
  fx.strokeStyle = TINTA(0.92); fx.lineWidth = 3 * u
  fx.beginPath(); fx.moveTo(bx - largo, by); fx.lineTo(bx, by)
  fx.moveTo(bx - largo, by - 9 * u); fx.lineTo(bx - largo, by + 9 * u)
  fx.moveTo(bx, by - 9 * u); fx.lineTo(bx, by + 9 * u); fx.stroke()
  fx.fillStyle = TINTA(0.92); fx.textAlign = 'center'; fx.font = `600 ${20 * u}px ${F_TXT}`
  fx.fillText('1 cm', bx - largo / 2, by - 16 * u); fx.textAlign = 'left'
  if (q.get('zonas') === '1') {       // solo revisión: franjas que tapa la UI de Reels/TikTok
    fx.fillStyle = 'rgba(255,0,0,0.28)'
    fx.fillRect(0, 0, PW, 270 * u); fx.fillRect(0, PH - 672 * u, PW, 672 * u)
    fx.fillRect(PW - 120 * u, 0, 120 * u, PH); fx.fillRect(0, 0, 65 * u, PH)
  }
}

async function fuentes() {
  // Fuentes de marca servidas junto al visor (las copia visor3d.py desde el build de la web)
  const ff = [['Fraunces', 'fonts/Fraunces-600-normal.woff', '600'],
    ['JetBrains Mono', 'fonts/JetBrains_Mono-400-normal.woff', '400'],
    ['JetBrains Mono', 'fonts/JetBrains_Mono-600-normal.woff', '600']]
  for (const [n, url, w] of ff) {
    try { const f = new FontFace(n, `url(${url})`, { weight: w }); await f.load(); document.fonts.add(f) } catch {}
  }
}

async function main() {
  await fuentes()
  const est = await (await fetch(carpeta + 'estudio.json')).json()
  const fecha = est.fecha.split('-').reverse().join('·')
  const tareas = []
  let ref = {}
  try { ref = (await (await fetch('./referencia.json')).json())[q.get('fecha').split('_')[0]] || {} } catch {}
  const altura = [0.232, 0.232, 0.29]
  for (const L of est.lesiones) {
    if (!L.malla) continue
    if (SOLO_DIANAS && !ref[String(L.id)]) continue
    const mat = L.diametro_mm >= 10 ? MAT.lesion : MAT.lesionPequena
    tareas.push(geo(carpeta + L.malla).then((g) => {
      const m = malla(g, mat, 1)
      const r = ref[String(L.id)]
      if (r) DIANAS.push({ malla: m, texto: r.etiqueta.replace('diana', 'Diana') + ' · ' + r.mm + ' mm',
        y: altura[DIANAS.length % altura.length], izq: DIANAS.length % 2 === 0 })
    }))
  }
  for (const k of ['porta', 'vasos', 'vci']) if (est.mallas[k]) tareas.push(geo(carpeta + est.mallas[k]).then((g) => malla(g, MAT[k], 1)))
  if (est.mallas.vesicula) tareas.push(geo(carpeta + est.mallas.vesicula).then((g) => malla(g, MAT.vesicula, 2)))
  let higado = null
  if (est.mallas.higado) {
    const g = await geo(carpeta + est.mallas.higado)
    malla(g, higadoMat(THREE.BackSide), 3)          // caras de detrás primero…
    higado = malla(g, higadoMat(THREE.FrontSide), 4) // …y las de delante encima
  }
  await Promise.all(tareas)
  const caja = new THREE.Box3().setFromObject(higado || grupo)
  const centro = caja.getCenter(new THREE.Vector3())
  grupo.children.forEach((m) => m.position.sub(centro))
  const radio = caja.getBoundingSphere(new THREE.Sphere()).radius
  // Encuadre por las zonas seguras: el hígado cabe en 520 px de alto (franja 480-1000) y, al
  // girar, en 860 px de ancho (su diagonal en planta). Escala en px/mm sobre la base 1080×1920.
  const tam = caja.getSize(new THREE.Vector3())
  // Con texto: franja 500-970 del vertical 1080×1920. Sin texto: llena el lienzo (fotos de la web).
  const pxmm = (TEXTO
    ? Math.min(450 / (tam.y * 1.12), 840 / Math.hypot(tam.x, tam.z))
    : Math.min(0.78 * H / (tam.y * 1.12), 0.9 * W / Math.hypot(tam.x, tam.z))) * Number(q.get('zoom') || 1)
  const tanV = Math.tan(THREE.MathUtils.degToRad(camara.fov / 2))
  const dist = H / (2 * tanV * pxmm)
  camara.position.set(0, 0, dist)
  camara.lookAt(0, 0, 0)
  grupo.position.y = TEXTO ? (960 - 735) / pxmm : 0   // con texto: centro a y≈735 (franja 500-970)
  // Escala y cifras para que nadie lea «superlesiones»: medidas automáticas del estudio
  ESCALA.pxPorMm = pxmm * SS                      // en px del lienzo supermuestreado
  const d = est.lesiones.map((L) => L.diametro_mm).filter((x) => x > 0)
  const med = d.filter((x) => x >= 10), peq = d.filter((x) => x < 10)
  const rango = (a) => a.length ? Math.round(Math.min(...a)) + '–' + Math.round(Math.max(...a)) + ' mm' : ''
  ESCALA.medibles = med.length + ' lesiones medibles (≥ 10 mm) · ' + rango(med)
  ESCALA.pequenas = peq.length + ' lesiones pequeñas (< 10 mm) · ' + rango(peq)
  const roz = d.filter((x) => x >= 9 && x < 11).length
  ESCALA.rozan = roz ? roz + ' rozan el umbral' : ''
  const s = caja.getSize(new THREE.Vector3())
  ESCALA.anchoHigado = cm(Math.max(s.x, s.z)) + ' cm'
  window.__fotograma = (t) => {
    grupo.rotation.y = 2 * Math.PI * t
    grupo.rotation.x = THREE.MathUtils.degToRad(9 + 4 * Math.sin(2 * Math.PI * t))
    composer.render()
    compone(fecha)
    return final.toDataURL('image/png')
  }
  window.__fotograma(0)
  window.__listo = true
}
main().catch((e) => { window.__error = String(e); console.error(e) })
