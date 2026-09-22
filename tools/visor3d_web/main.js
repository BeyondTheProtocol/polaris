// Visor hepático local (offline) — NiiVue. Lee ./<fecha>/estudio.json + volúmenes y mallas de
// esa carpeta, más ./comparacion.json y ./referencia.json (medidas del radiólogo, opcional).
// Todo relativo: se sirve desde la carpeta de salida de `visor3d.py visor` y no llama a nada
// fuera de ella (CSP default-src 'self' en index.html).
import { Niivue, SLICE_TYPE, MULTIPLANAR_TYPE, SHOW_RENDER } from '@niivue/niivue'
import { fraccionHepaticaPct } from './carga.js'

const BACK = [0.110, 0.067, 0.149, 1]            // berenjena-950, el negativoscopio de la marca
const CORAL = [255, 107, 71]
const SELECCION = [255, 214, 102]
const NEUTRO = [230, 220, 205]
const PORTA = [110, 150, 200]
const VASOS = [150, 170, 200]

const $ = (s) => document.querySelector(s)
const fmt = (x, d = 1) => (x == null ? '—' : Number(x).toFixed(d).replace('.', ','))

async function json(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(url + ': ' + r.status)
  return r.json()
}

const estado = { fechas: [], fecha: null, estudio: null, comparacion: null, referencia: {}, sel: null, pet: null }
const fechaPET = (f) => (f && f.length === 8 ? f.slice(6) + '-' + f.slice(4, 6) + '-' + f.slice(0, 4) : f)

const nv2 = new Niivue({
  backColor: BACK, crosshairColor: [1, 0.42, 0.28, 1], crosshairWidth: 1,
  isRadiologicalConvention: true, isOrientationTextVisible: true,
  isColorbar: false, isRuler: true, rulerColor: [0.98, 0.96, 0.94, 0.9],
  multiplanarShowRender: SHOW_RENDER.NEVER, atlasOutline: 1, dragMode: 'contrast',
})
const nv3 = new Niivue({ backColor: BACK, show3Dcrosshair: true, isOrientCube: true,
  crosshairColor: [1, 0.42, 0.28, 1] })
window.__visor = { nv2, nv3 }   // inspección local (consola del navegador)

function lutEtiquetas(lesiones, selId) {
  // 1 hígado (invisible en cortes: el TC ya lo enseña), 2 vasos, 3 porta, 4 VCI, 10+id lesiones
  const I = [0, 1, 2, 3, 4], R = [0, 0, ...VASOS.slice(0, 1), PORTA[0], PORTA[0]]
  const G = [0, 0, VASOS[1], PORTA[1], PORTA[1]], B = [0, 0, VASOS[2], PORTA[2], PORTA[2]]
  const A = [0, 0, 90, 120, 90], labels = ['', 'hígado', 'vasos', 'porta', 'cava']
  for (const L of lesiones) {
    const c = L.id === selId ? SELECCION : CORAL
    I.push(10 + L.id); R.push(c[0]); G.push(c[1]); B.push(c[2]); A.push(255); labels.push('L' + L.id)
  }
  return { R, G, B, A, I, labels }
}

async function cargaFecha(fecha) {
  estado.fecha = fecha
  estado.sel = null
  const base = './' + fecha + '/'
  const est = await json(base + 'estudio.json')
  estado.estudio = est
  try { estado.pet = await json(base + 'pet.json') } catch { estado.pet = null }
  for (const nv of [nv2, nv3]) {
    while (nv.volumes.length) nv.removeVolume(nv.volumes[0])
    while (nv.meshes.length) nv.removeMesh(nv.meshes[0])
  }
  const [w0, w1] = est.ventana_hu
  await nv2.loadVolumes([
    { url: base + 'ct.nii.gz', colormap: 'gray', cal_min: w0, cal_max: w1 },
    { url: base + 'etiquetas.nii.gz', opacity: 0.55 },
  ])
  nv2.volumes[1].setColormapLabel(lutEtiquetas(est.lesiones, null))
  nv2.updateGLVolume()
  // 3D: solo mallas (el volumen del TC taparía el hígado) + etiquetas invisibles como espacio común
  await nv3.loadVolumes([{ url: base + 'etiquetas.nii.gz', opacity: 0 }])
  const mallas = []
  for (const L of est.lesiones) if (L.malla) mallas.push({ url: base + L.malla, rgba255: [...CORAL, 255], name: 'L' + L.id + '.ply' })
  if (est.mallas.porta) mallas.push({ url: base + est.mallas.porta, rgba255: [...PORTA, 255] })
  if (est.mallas.vasos) mallas.push({ url: base + est.mallas.vasos, rgba255: [...VASOS, 200] })
  // El hígado va el ÚLTIMO: NiiVue pinta la transparencia en el orden del array (arquitectura).
  if (est.mallas.higado) mallas.push({ url: base + est.mallas.higado, rgba255: [...NEUTRO, 255] })
  await nv3.loadMeshes(mallas)
  for (const m of nv3.meshes) nv3.setMeshShader(m.id, 'Phong')
  const hig = nv3.meshes[nv3.meshes.length - 1]
  if (est.mallas.higado) nv3.setMeshProperty(hig.id, 'opacity', 0.22)
  nv3.setSliceType(SLICE_TYPE.RENDER)
  nv3.setRenderAzimuthElevation(210, 20)
  pintaResumen()
  pintaTabla()
  console.info('visor: cargado ' + fecha + ' · mallas ' + nv3.meshes.length + '/' + mallas.length)
}

function pintaResumen() {
  const e = estado.estudio
  $('#resumen').innerHTML =
    `<div><b>${fmt(e.volumen_higado_ml, 0)} ml</b><span>volumen hepático</span></div>` +
    `<div><b>${fmt(e.volumen_tumoral_ml, 1)} ml</b><span>volumen tumoral detectado</span></div>` +
    `<div><b>${fmt(fraccionHepaticaPct(e.volumen_tumoral_ml, e.volumen_higado_ml), 1)} %</b><span>del hígado ocupado por lo detectado<br>(exploratorio, no pronóstico)</span></div>` +
    `<div><b>${e.lesiones.length}</b><span>lesiones detectadas<br>(${e.lesiones.filter((l) => l.pequena).length} &lt; 10 mm, fiabilidad baja)</span></div>` +
    `<div><b>${e.modalidad} · ${e.fecha}</b><span>fase portal · ventana W${e.ventana_hu[1] - e.ventana_hu[0]}/L${(e.ventana_hu[0] + e.ventana_hu[1]) / 2}</span></div>`
  const p = estado.pet
  if (p && p.fondo_higado) {
    $('#resumen').innerHTML +=
      `<div><b>${fmt(p.fondo_higado.suvmean, 2)} ± ${fmt(p.fondo_higado.suvsd, 2)}</b><span>fondo hepático SUVmean · PET-FDG ${fechaPET(p.pet.fecha)} · umbral PERCIST ${fmt(p.fondo_higado.umbral_percist, 2)}</span></div>`
  }
  pintaFocos()
}

function pintaFocos() {
  const p = estado.pet
  const caja = $('#focos')
  if (!p) { caja.innerHTML = ''; return }
  const pegado = (f) => f.lesion_cercana && f.distancia_mm <= 10
  // Un foco pegado a una lesión es de esa lesión aunque caiga justo fuera de la máscara hepática
  const hep = (p.focos_pet || []).filter((f) => f.organo === 'liver' || pegado(f))
  const sueltos = hep.filter((f) => !pegado(f))
  const otros = (p.focos_pet || []).filter((f) => !hep.includes(f))
  const nombre = (o) => {
    if (o === 'fuera de órganos segmentados') return 'fuera de los órganos que segmenta el modelo'
    const fijo = { liver: 'hígado', heart: 'corazón', stomach: 'estómago', gallbladder: 'vesícula', spleen: 'bazo',
      duodenum: 'duodeno', colon: 'colon', small_bowel: 'intestino delgado', pancreas: 'páncreas', esophagus: 'esófago',
      kidney_right: 'riñón derecho', kidney_left: 'riñón izquierdo', adrenal_gland_right: 'suprarrenal derecha',
      lung_lower_lobe_right: 'pulmón derecho (lóbulo inferior)', lung_lower_lobe_left: 'pulmón izquierdo (lóbulo inferior)' }
    if (fijo[o]) return fijo[o]
    let m = o.match(/^rib_(right|left)_(\d+)$/)
    if (m) return 'costilla ' + m[2] + ' ' + (m[1] === 'right' ? 'derecha' : 'izquierda')
    m = o.match(/^vertebrae_(\w+)$/)
    if (m) return 'vértebra ' + m[1]
    return o.replace(/_/g, ' ')
  }
  caja.innerHTML =
    `<h2>PET-FDG ${fechaPET(p.pet.fecha)}</h2>` +
    `<p>SUV calculado desde el PET original (${fmt(p.pet.min_desde_inyeccion, 0)} min tras la inyección), coincide con el del fabricante (cociente ${fmt(p.pet.ratio_vs_fabricante, 4)}). ` +
    `Registro del hígado del TC diagnóstico sobre el del PET: Dice ${fmt(p.dice_registro_higado, 3)}.</p>` +
    (sueltos.length ? `<p><b>Focos hepáticos del PET sin lesión del TC a menos de 10 mm</b> (a revisar por el radiólogo: lesión que el modelo del TC no detectó, o desalineación residual, sobre todo en la cúpula):</p><ul>` +
      sueltos.map((f) => `<li>SUVmax <b>${fmt(f.suvmax, 2)}</b> · segmento ${f.segmento ?? '—'} · ${f.voxeles} vóxel(es) · lesión TC más cercana L${f.lesion_cercana ?? '—'} a ${fmt(f.distancia_mm, 1)} mm</li>`).join('') + '</ul>' : '') +
    (otros.length ? `<p><b>Captación junto al hígado pero fuera de él</b> (sobre el umbral; la del riñón es excreción fisiológica):</p><ul>` +
      otros.map((f) => `<li>${nombre(f.organo)} · SUVmax ${fmt(f.suvmax, 2)} · ${f.voxeles} vóxel(es)${f.segmento ? ' · junto al segmento ' + f.segmento : ''}</li>`).join('') + '</ul>' : '')
}

function parDe(id) {
  const c = estado.comparacion
  if (!c) return null
  const lado = estado.fecha === estado.fechas[0] ? 'antes' : 'despues'
  return c.pares.find((p) => p[lado] === id) || null
}

function pintaTabla() {
  const e = estado.estudio
  const ref = estado.referencia[estado.fecha] || {}
  const filas = e.lesiones.map((L) => {
    const p = parDe(L.id)
    const esDespues = estado.fecha !== estado.fechas[0]
    let delta = '—'
    if (p && p.antes && p.despues) delta = (p.delta_diametro_pct > 0 ? '+' : '') + fmt(p.delta_diametro_pct, 0) + ' %'
    else if (p && esDespues && !p.antes) delta = '<span title="Puede ser una lesión que el modelo no vio antes, un falso positivo o una nueva. Lo decide el radiólogo.">sin pareja automática en el estudio previo</span>'
    else if (p && !esDespues && !p.despues) delta = '<span title="Puede haber desaparecido, confluido con otra o no haber sido detectada después. Lo decide el radiólogo.">sin pareja automática en el estudio siguiente</span>'
    const r = ref[String(L.id)]
    const bandera = L.pequena ? '<span class="aviso">&lt;10 mm</span>' : ''
    const acuerdo = L.acuerdo_2o_modelo == null ? '—' : fmt(L.acuerdo_2o_modelo * 100, 0) + ' %'
    const pl = estado.pet && (estado.pet.lesiones || []).find((x) => x.id === L.id)
    const suv = pl && pl.suvmax != null ? fmt(pl.suvmax, 2) : '—'
    return `<tr data-id="${L.id}" tabindex="0"><th scope="row">L${L.id}</th><td>${L.segmento ?? '—'}</td>` +
      `<td>${fmt(L.diametro_mm)} ${bandera}</td><td>${r ? '<b>' + r.mm + '</b> <small>' + r.etiqueta + '</small>' : '—'}</td>` +
      `<td>${fmt(L.volumen_ml, 2)}</td><td>${suv}</td><td>${delta}</td><td>${acuerdo}</td></tr>`
  })
  $('#tabla tbody').innerHTML = filas.join('')
  for (const tr of document.querySelectorAll('#tabla tbody tr')) {
    const ir = () => selecciona(Number(tr.dataset.id))
    tr.addEventListener('click', ir)
    tr.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ir() } })
  }
}

function selecciona(id) {
  estado.sel = id
  const L = estado.estudio.lesiones.find((x) => x.id === id)
  if (!L) return
  const vol = nv2.volumes[0]
  const vox = vol.mm2vox(L.centro_mm)
  nv2.scene.crosshairPos = nv2.vox2frac(vox)
  nv2.volumes[1].setColormapLabel(lutEtiquetas(estado.estudio.lesiones, id))
  nv2.updateGLVolume()
  for (const m of nv3.meshes) {
    if (m.name && m.name.startsWith('L')) nv3.setMeshProperty(m.id, 'rgba255', m.name === 'L' + id + '.ply' ? [...SELECCION, 255] : [...CORAL, 255])
  }
  nv2.drawScene(); nv3.drawScene()
  for (const tr of document.querySelectorAll('#tabla tbody tr')) tr.classList.toggle('sel', Number(tr.dataset.id) === id)
  $('#estado').textContent = `L${id} · segmento ${L.segmento ?? '—'} · ${fmt(L.diametro_mm)} mm · ${fmt(L.volumen_ml, 2)} ml`
}

async function main() {
  await nv2.attachTo('gl2')
  await nv3.attachTo('gl3')
  nv2.setSliceType(SLICE_TYPE.MULTIPLANAR)
  nv2.opts.multiplanarLayout = MULTIPLANAR_TYPE.AUTO
  nv2.broadcastTo(nv3, { '2d': true, '3d': false })
  nv3.broadcastTo(nv2, { '2d': true, '3d': false })
  nv2.onLocationChange = (d) => { $('#cursor').textContent = d.string }
  const indice = await json('./indice.json')
  estado.fechas = indice.fechas
  try { estado.comparacion = await json('./comparacion.json') } catch { estado.comparacion = null }
  try { estado.referencia = await json('./referencia.json') } catch { estado.referencia = {} }
  const sel = $('#fecha')
  sel.innerHTML = indice.fechas.map((f) => `<option value="${f}">${f}</option>`).join('')
  sel.value = indice.fechas[indice.fechas.length - 1]
  sel.addEventListener('change', () => cargaFecha(sel.value))
  $('#vista').addEventListener('change', (ev) => {
    const v = ev.target.value
    nv2.setSliceType(v === 'mpr' ? SLICE_TYPE.MULTIPLANAR : SLICE_TYPE[v.toUpperCase()])
  })
  await cargaFecha(sel.value)
}

main().catch((err) => { $('#estado').textContent = 'Error: ' + err.message; console.error(err) })
