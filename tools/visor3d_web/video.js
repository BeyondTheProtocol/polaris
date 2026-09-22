// Modo vídeo: solo el 3D, a pantalla completa, sin cursor ni cubo, para grabar fotograma a
// fotograma (grabar.mjs). ?fecha=AAAAMMDD. Expone window.__listo y window.__fotograma(t).
import { Niivue, SLICE_TYPE } from '@niivue/niivue'

const q = new URLSearchParams(location.search)
const fecha = q.get('fecha')
const BACK = [0.110, 0.067, 0.149, 1]
const CORAL = [255, 107, 71]
const PORTA = [110, 150, 200]
const VASOS = [150, 170, 200]
const NEUTRO = [235, 225, 212]

const nv = new Niivue({ backColor: BACK, show3Dcrosshair: false, isOrientCube: false,
  crosshairWidth: 0, isColorbar: false })

async function main() {
  await nv.attachTo('gl')
  const base = './' + fecha + '/'
  const est = await (await fetch(base + 'estudio.json')).json()
  await nv.loadVolumes([{ url: base + 'etiquetas.nii.gz', opacity: 0 }])
  const mallas = []
  for (const L of est.lesiones) if (L.malla) mallas.push({ url: base + L.malla, rgba255: [...CORAL, 255], name: 'L' + L.id + '.ply' })
  if (est.mallas.porta) mallas.push({ url: base + est.mallas.porta, rgba255: [...PORTA, 255], name: 'porta.ply' })
  if (est.mallas.vasos) mallas.push({ url: base + est.mallas.vasos, rgba255: [...VASOS, 220], name: 'vasos.ply' })
  if (est.mallas.higado) mallas.push({ url: base + est.mallas.higado, rgba255: [...NEUTRO, 255], name: 'higado.ply' })
  await nv.loadMeshes(mallas)
  for (const m of nv.meshes) nv.setMeshShader(m.id, 'Phong')
  nv.setMeshProperty(nv.meshes[nv.meshes.length - 1].id, 'opacity', 0.26)
  nv.setSliceType(SLICE_TYPE.RENDER)
  nv.scene.crosshairPos = [2, 2, 2]          // fuera del volumen: sin cruz en el 3D
  nv.volScaleMultiplier = Number(q.get('zoom') || 1.15)
  window.__fotograma = (t) => {
    // t ∈ [0,1): una vuelta completa, con un balanceo suave de elevación
    nv.setRenderAzimuthElevation(180 + 360 * t, 15 + 8 * Math.sin(2 * Math.PI * t))
    nv.drawScene()
    return nv.canvas.toDataURL('image/png')   // mismo turno que el dibujo: el búfer aún vale
  }
  window.__fotograma(0)
  window.__listo = true
}
main().catch((e) => { window.__error = String(e); console.error(e) })
