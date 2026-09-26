// Lanza un Chrome headless que SIEMPRE se cierra: al acabar, con un error, con una señal o al
// pasar el tope de tiempo. Para cualquier script de captura (captura_visor.mjs y los que vengan).
//
// Por qué existe (26-sep-26, deuda `chrome_headless_huerfano`): captura_visor.mjs solo cerraba
// Chrome en los caminos felices. Cuando el script murió a medias, tres Chrome headless se quedaron
// huérfanos (PPID 1) con dos pestañas cada uno al 100 % de CPU durante ~27 h: unos 6 de los 10
// núcleos del Mac. Los 9 hooks de cada Bash pasaron de 0,7 s a 6,5 s y todo Polaris iba lento.
// Nadie avisó. Un `kill -9` al script sigue sin poder atraparse: para eso está la red de debajo,
// `tools/bucles_colgados.py`, que limpia headless huérfanos de más de 1 h.
//
// Uso:
//   import { lanzarChrome } from './_chrome_headless.mjs'
//   const { puerto, cerrar } = lanzarChrome(['--window-size=1400,1600'], { topeMs: 90000 })
//   ... CDP contra http://127.0.0.1:${puerto} ...
//   cerrar()          // opcional: también se cierra solo al salir
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync, writeSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export const CHROME = process.env.BTP_CHROME_BIN ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

export function lanzarChrome(args = [], { topeMs = 90000 } = {}) {
  const puerto = 9400 + Math.floor(Math.random() * 500)
  const perfil = mkdtempSync(join(tmpdir(), 'cap-'))
  // `detached`: Chrome encabeza su propio grupo de procesos, así que matar el grupo (-pid) se
  // lleva también sus pestañas (renderers), que eran las que quemaban CPU.
  const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${puerto}`, ...args,
    `--user-data-dir=${perfil}`, 'about:blank'], { stdio: 'ignore', detached: true })

  let cerrado = false
  const cerrar = () => {
    if (cerrado) return
    cerrado = true
    try { process.kill(-chrome.pid, 'SIGKILL') } catch {}
    try { chrome.kill('SIGKILL') } catch {}
    try { rmSync(perfil, { recursive: true, force: true }) } catch {}
  }
  process.on('exit', cerrar)
  for (const s of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
    process.on(s, () => { cerrar(); process.exit(130) })
  }
  // writeSync y no console.error: en una tubería console.error es asíncrono y process.exit lo
  // corta, y el motivo del fallo se perdía (visto al escribir el test, 26-sep-26).
  const decir = (t) => { try { writeSync(2, String(t) + '\n') } catch {} }
  const fallo = (e) => { decir(e && e.stack ? e.stack : e); cerrar(); process.exit(1) }
  process.on('uncaughtException', fallo)
  process.on('unhandledRejection', fallo)
  const tope = setTimeout(() => {
    decir(`tope de ${Math.round(topeMs / 1000)} s: cierro Chrome`)
    cerrar()
    process.exit(1)
  }, topeMs)
  tope.unref()
  return { puerto, perfil, pid: chrome.pid, cerrar }
}
