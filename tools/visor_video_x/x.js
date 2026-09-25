// Vídeo cuadrado para X: hígado y mama girando a la vez, con los materiales, los colores y los
// rótulos del visor publicado. Los .ply y los escena.json son los PÚBLICOS de helptitular.com,
// descargados aquí al lado; no hay nada clínico crudo en este render.
import * as THREE from 'three'
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'

const W = 1080, H = 1080
const WEB = new URLSearchParams(location.search).get('web') === '1'
const BARRA = WEB ? 74 : 0   // barra de navegador: solo en la variante «web»
const PIE = 176              // alto de la franja de leyenda
const PANEL_H = H - PIE - BARRA
const CORTE = Math.round(W * 0.60)   // hígado a la izquierda, mama a la derecha

const marco = document.getElementById('marco')
if (WEB) document.body.classList.add('web')
const overlay = document.getElementById('overlay')

/* ── materiales, calcados del visor publicado ──────────────────────────────────────── */
function fresnel(mat, min, max, pot) {
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uMin = { value: min }; sh.uniforms.uMax = { value: max }; sh.uniforms.uPot = { value: pot }
    sh.fragmentShader = 'uniform float uMin; uniform float uMax; uniform float uPot;\n'
      + sh.fragmentShader.replace('#include <opaque_fragment>',
        'float fr = pow(1.0 - abs(dot(normal, normalize(vViewPosition))), uPot);\n'
        + 'diffuseColor.a = mix(uMin, uMax, fr);\n#include <opaque_fragment>')
  }
  return mat
}
const higadoMat = (lado) => fresnel(new THREE.MeshPhysicalMaterial({
  color: 0x9a3f2c, roughness: 0.38, clearcoat: 0.8, clearcoatRoughness: 0.22,
  sheen: 0.5, sheenRoughness: 0.5, sheenColor: new THREE.Color(0xe39a86),
  transparent: true, depthWrite: false, side: lado }), 0.10, 0.92, 2.4)
const vaso = (c) => new THREE.MeshPhysicalMaterial({ color: c, roughness: 0.28, clearcoat: 0.9, clearcoatRoughness: 0.15 })
const lesionMat = () => new THREE.MeshPhysicalMaterial({ color: 0xf2b23c, roughness: 0.3,
  clearcoat: 0.85, clearcoatRoughness: 0.1, emissive: 0x7a4a08, emissiveIntensity: 0.35 })
const lesionPequenaMat = () => new THREE.MeshPhysicalMaterial({ color: 0x7c5cf0, roughness: 0.5,
  clearcoat: 0.25, emissive: 0x5b3ce0, emissiveIntensity: 0.85 })
const MAT_H = { porta: () => vaso(0x5236b0), vasos: () => vaso(0x2d63d6), vci: () => vaso(0x1f45a8),
  vesicula: () => fresnel(new THREE.MeshPhysicalMaterial({ color: 0x6f9a3a, roughness: 0.25,
    clearcoat: 1, transparent: true, depthWrite: false }), 0.35, 0.95, 2.0) }
const tejidoMat = (lado) => fresnel(new THREE.MeshPhysicalMaterial({
  color: 0xe8dcc8, roughness: 0.45, clearcoat: 0.5, clearcoatRoughness: 0.3,
  sheen: 0.25, sheenRoughness: 0.7, sheenColor: new THREE.Color(0xfff6e4),
  transparent: true, depthWrite: false, side: lado }), 0.05, 0.38, 2.4)
const envolturaMat = (lado) => fresnel(new THREE.MeshPhysicalMaterial({
  color: 0xdccfc0, roughness: 0.78, clearcoat: 0.1, clearcoatRoughness: 0.7,
  transparent: true, depthWrite: false, side: lado }), 0.11, 0.62, 2.0)

const RAS_A_THREE = new THREE.Matrix4().set(-1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1)
const loader = new PLYLoader()
async function geo(url) {
  const g = await loader.loadAsync(url)
  g.applyMatrix4(RAS_A_THREE); g.computeVertexNormals(); g.computeBoundingSphere(); return g
}

const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById('gl'), antialias: true })
renderer.setPixelRatio(2)
renderer.setSize(W, H, false)
renderer.outputColorSpace = THREE.SRGBColorSpace
renderer.toneMapping = THREE.ACESFilmicToneMapping
renderer.toneMappingExposure = 1.0
renderer.setScissorTest(true)
const pmrem = new THREE.PMREMGenerator(renderer)
const ENV = pmrem.fromScene(new RoomEnvironment(), 0.04).texture

function nuevaEscena(w, h) {
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x1c1126)
  scene.environment = ENV; scene.environmentIntensity = 0.9
  const camera = new THREE.PerspectiveCamera(26, w / h, 0.5, 5000)
  const luz = (c, i, x, y, z) => { const l = new THREE.DirectionalLight(c, i); l.position.set(x, y, z); camera.add(l) }
  luz(0xfff0dc, 1.7, 2.5, 3, 4); luz(0xb8c8ff, 0.6, -4, 0.5, 2); luz(0xffd9f0, 2.2, -1, 2, -5)
  scene.add(camera)
  return { scene, camera, dianas: [] }
}
const AH = CORTE, AM = W - CORTE
const Hi = nuevaEscena(AH, PANEL_H)
const Ma = nuevaEscena(AM, PANEL_H)
const add = (S, g, mat, orden) => { const m = new THREE.Mesh(g, mat); m.renderOrder = orden; S.scene.add(m); return m }

/* ── hígado ────────────────────────────────────────────────────────────────────────── */
const escH = await (await fetch('./higado/escena.json')).json()
for (const k of ['porta', 'vasos', 'vci']) if (escH.mallas[k]) add(Hi, await geo('./higado/' + escH.mallas[k]), MAT_H[k](), 1)
if (escH.mallas.vesicula) add(Hi, await geo('./higado/' + escH.mallas.vesicula), MAT_H.vesicula(), 2)
for (const les of escH.lesiones) {
  const medible = (les.mm_informe ?? les.diametro_auto_mm) >= 10
  const m = add(Hi, await geo('./higado/' + les.malla), (medible ? lesionMat : lesionPequenaMat)(), 1)
  if (les.diana) Hi.dianas.push({ malla: m, texto: les.diana.replace('diana', 'Diana') + ' · ' + les.mm_informe + ' mm' })
}
const gh = await geo('./higado/' + escH.mallas.higado)
add(Hi, gh, higadoMat(THREE.BackSide), 3); add(Hi, gh, higadoMat(THREE.FrontSide), 4)
Hi.radio = gh.boundingSphere.radius; Hi.centro = gh.boundingSphere.center.clone()
const cuenta = {
  dianas: escH.lesiones.filter((x) => x.diana).length,
  medibles: escH.lesiones.filter((x) => !x.diana && x.diametro_auto_mm >= 10).length,
  pequenas: escH.lesiones.filter((x) => !x.diana && x.diametro_auto_mm < 10).length,
}

/* ── mama ──────────────────────────────────────────────────────────────────────────── */
const escM = await (await fetch('./mama/escena.json')).json()
let haciaLesion = null
for (const les of escM.lesiones) {
  const g = await geo('./mama/' + les.malla)
  const m = add(Ma, g, lesionMat(), 1)
  haciaLesion = g.boundingSphere.center.clone()
  if (les.mm_informe) Ma.dianas.push({ malla: m, texto: 'Informe · ' + les.mm_informe + ' mm' })
}
const gt = await geo('./mama/' + escM.mallas.fgt)
add(Ma, gt, tejidoMat(THREE.BackSide), 3); add(Ma, gt, tejidoMat(THREE.FrontSide), 4)
let ge = null
if (escM.mallas.mama) {
  ge = await geo('./mama/' + escM.mallas.mama)
  add(Ma, ge, envolturaMat(THREE.BackSide), 5); add(Ma, ge, envolturaMat(THREE.FrontSide), 6)
}
Ma.radio = (ge ?? gt).boundingSphere.radius; Ma.centro = (ge ?? gt).boundingSphere.center.clone()

/* ── cámaras: una vuelta completa por bucle ────────────────────────────────────────── */
function distancia(S, w, h) {
  const fov = THREE.MathUtils.degToRad(S.camera.fov / 2)
  return (S.radio * 1.08) / Math.sin(fov) / Math.min(1, w / h)
}
const dH = distancia(Hi, AH, PANEL_H) * 0.88
const dM = distancia(Ma, AM, PANEL_H) * 0.80
// La mama arranca desde el lado de su lesión, igual que el visor: de frente es un óvalo que no
// se reconoce; de perfil aparece la forma.
const fase0M = Math.atan2(Math.sign(haciaLesion?.x ?? -1) * 0.74, 0.66)

function coloca(S, d, t, incl, fase0) {
  const a = fase0 + 2 * Math.PI * t
  const e = THREE.MathUtils.degToRad(incl)
  const c = S.centro
  S.camera.position.set(c.x + Math.sin(a) * Math.cos(e) * d, c.y + Math.sin(e) * d,
                        c.z + Math.cos(a) * Math.cos(e) * d)
  S.camera.lookAt(c)
  S.camera.updateMatrixWorld()
}

/* ── rótulos ───────────────────────────────────────────────────────────────────────── */
const p3 = new THREE.Vector3()
function rotulos(S, ox, w, h, escala) {
  return S.dianas.map((D) => {
    const bs = D.malla.geometry.boundingSphere
    p3.copy(bs.center).project(S.camera)
    const x = (p3.x + 1) / 2 * w, y = (1 - p3.y) / 2 * h
    const dist = S.camera.position.distanceTo(bs.center)
    const r = Math.max(26, (bs.radius / (dist * Math.tan(THREE.MathUtils.degToRad(S.camera.fov / 2)))) * h / 2 * escala)
    const medio = (D.texto.length * 12.6 + 22) / 2 + 8
    const tx = Math.min(Math.max(x, medio), w - medio)
    const dentro = x > w * 0.03 && x < w * 0.97 && y > h * 0.02 && y < h * 0.98
    return { texto: D.texto, x: ox + x, tx: ox + tx, y: BARRA + y, r, visible: p3.z < 1 && dentro }
  })
}
function pinta(lista) {
  overlay.innerHTML = lista.filter((r) => r.visible).map((r) =>
    `<span class="anillo" style="left:${r.x}px;top:${r.y}px;width:${2 * r.r}px;height:${2 * r.r}px"></span>` +
    `<span class="rot" style="left:${r.tx}px;top:${Math.max(r.y - r.r - 10, BARRA + 62)}px">${r.texto}</span>`).join('')
}

/* ── textos fijos ──────────────────────────────────────────────────────────────────── */
document.getElementById('cap-h').textContent = 'Hígado'
document.getElementById('cap-h').style.cssText += `left:30px;top:${BARRA + 24}px`
document.getElementById('cap-m').textContent = 'Mama derecha'
document.getElementById('cap-m').style.cssText += `left:${CORTE + 26}px;top:${BARRA + 24}px`
const fila = (c, borde, txt) => `<div class="l"><span class="p" style="background:${c}${borde ? ';box-shadow:0 0 0 3px #f5efe6' : ''}"></span><span>${txt}</span></div>`
document.getElementById('pie').innerHTML =
  fila('#f2b23c', true, `<b>${cuenta.dianas} lesiones diana, con anillo: medida del radiólogo</b>`) +
  fila('#f2b23c', false, `Otras ${cuenta.medibles} de 10 mm o más, y ${cuenta.pequenas} de menos de 10 mm: detección automática sin validar`) +
  `<div class="url">${WEB ? '' : 'helptitular.com/lesiones · '}TC y RM del 8 de septiembre de 2026</div>`
const div = document.createElement('div'); div.className = 'div'; div.style.left = CORTE + 'px'
div.style.bottom = PIE + 'px'; div.style.top = BARRA + 'px'; marco.insertBefore(div, overlay)

/* ── fotograma ─────────────────────────────────────────────────────────────────────── */
window.__fotograma = (t) => {
  coloca(Hi, dH, t, 9, 0)
  coloca(Ma, dM, t, 8, fase0M)
  renderer.setViewport(0, PIE, AH, PANEL_H); renderer.setScissor(0, PIE, AH, PANEL_H)
  renderer.render(Hi.scene, Hi.camera)
  renderer.setViewport(CORTE, PIE, AM, PANEL_H); renderer.setScissor(CORTE, PIE, AM, PANEL_H)
  renderer.render(Ma.scene, Ma.camera)
  pinta([...rotulos(Hi, 0, AH, PANEL_H, 1.35), ...rotulos(Ma, CORTE, AM, PANEL_H, 1.25)])
}
window.__fotograma(0)
window.__listo = true
