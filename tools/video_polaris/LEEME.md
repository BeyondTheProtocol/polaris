# 🎬 video_polaris — «Polaris, so far» (EN)

Vídeo corto de Polaris y lo que ha hecho, para redes (X primero, 16:9). Plan aprobado el 27-sep-2026:
`00_FUENTE-DE-VERDAD/07 · Marca/plan-vídeo-polaris-so-far-en-x-16x9-2026-09-27.md`.

Todo local: Chrome headless por CDP + ffmpeg + Python de la stdlib (y PIL). Nada que instalar.
Hanken Grotesk llega de Google Fonts; el resto de tipografías, de la carpeta del piloto de /datos.

## Cómo se hace, de cero

```bash
B=<carpeta de trabajo>   # p. ej. el scratchpad de la sesión
node tools/video_polaris/capturar.mjs $B/tomas                 # biopsia, /data y repo (páginas públicas)
node tools/video_polaris/grabar_higado.mjs $B/higado-raw 8 30  # el visor del hígado, en vivo
python3 tools/video_polaris/preparar.py $B/tomas $B/higado-raw $B/build   # recortes + cifras del día
python3 tools/video_polaris/sonido.py $B/build/sonido.wav 26
node tools/video_polaris/grabar.mjs $B/build 30 0 26          # 780 fotogramas, ~45 s
bash tools/video_polaris/montar.sh $B/build "<salida>.mp4"
```

## Lo que NO puede salir (y quién lo frena)

| Qué | Por qué | Freno |
|---|---|---|
| Rótulos de medida y SUV del visor | En este vídeo no sale ninguna cifra clínica (comité de diseño) | `grabar_higado.mjs` oculta `.lv-rotulo` solo en la grabación |
| La mama | Su cifra está reservada a la segunda ola de la campaña de visores | se graba solo el lienzo «Liver» |
| La lista de commits del repo | Lleva la ✗ roja del CI | recorte a enlace + topics |
| La cifra grande de /data | Diseño: nada clínico legible | recorte al campo de puntos |
| {{CONTACTO}}, ingeniera/scientist, pecho | Muro de marca | `tests/test_video_polaris.py` |
| Cifras del sistema a mano | Tienen que ser las del día del render | salen de `anatomia.py inventario --json` |

Los recortes (`preparar.py`) están medidos sobre capturas a 1920×1080 del 27-sep-2026: si la web
cambia de maquetación, mira la hoja de contactos antes de dar el vídeo por bueno.

## El vídeo con historia (Remotion), en orden — lo aprendido de v15 a v18 (28-sep-2026)

La v15 tardó 15 versiones; esto es lo que hay que hacer a la primera la próxima vez.

| # | Paso | Comando | Lo que costó aprenderlo |
|---|---|---|---|
| 1 | Guion firmado por ella | `guion_voz.json` | No se toca una frase sin su OK |
| 2 | Voz **enlazada**, 3 tomas por frase | `~/claudecode/.venv/bin/python voz.py <build>` | Frase a frase y sin contexto suena a «leer una lista» ({{TITULAR}}: «sobre todo es el tono de hablar»). Se queda la toma más viva; `tomas` en el guion fija una a mano |
| 3 | Tiempos de palabra (Whisper local) | `~/claudecode/.venv/bin/python palabras.py <build>` | — |
| 4 | Montaje sobre la música + voz nivelada | `python3 montaje.py <build> <musica.mp3>` | ElevenLabs entrega cada frase a su volumen: de -16 a -28 LUFS. Se iguala cada una a -18 con ganancia fija |
| 5 | Efectos **después** del montaje | `python3 sfx.py <build>` | Si cambia la línea de tiempo y no se regeneran, los barridos caen fuera de sitio |
| 6 | Copiar `voz.wav`, `musica.wav`, `sfx.wav`, `timeline.json` a `remotion/public/` | — | — |
| 7 | Render + master | `bash remotion/render.sh vN` | Techo -4 dBTP: X recodifica al subir y el AAC sube ~0,5 dB |
| 8 | Mirar, no suponer | hoja de contactos de las costuras + `verify_promo.py` de onetake (ritmo y pico) | — |

Reglas de pantalla que {{TITULAR}} tuvo que corregir:
- **Nunca el mismo texto dos veces a la vez**: si está escrito en grande, no se subtitula (`ROTULADO` en `Polaris.tsx`, con test). Pasó en el cierre, en los golpes del principio y en NED.
- **Subtítulo legible sobre cualquier fondo**: sobre la página crema va oscuro.
- **Las escenas no se reemplazan, se transforman**: en cada costura algo que ya está en pantalla se convierte en lo siguiente (onetake). Solo el cierre y las ráfagas pueden cortar.
- Una palabra clave con peso (NED) merece su propia animación: cada inicial se despliega cuando ella dice la palabra.

## Vídeo NARRADO con su voz (clase / presentación), 28-sep-2026

Mezcla del estilo de {{CONTACTO}} / KAI con lo mejor de la v18 ({{TITULAR}}: «coge lo mejor de ambos mundos»).

1. Guion por capítulos en `guiones/<slug>.<idioma>.json`, con kicker, titular (`*acento*`), escena y frases. Recorrido: `voz-titular` → `verificacion` → **su firma**. El español se escribe desde cero, no se traduce.
2. `~/claudecode/.venv/bin/python narrado.py guiones/<slug>.json <build>` genera **toda la narración en UNA toma** (pausas entre capítulos con `<break>`; los tiempos de cada palabra salen de la propia toma), la música y los efectos, y lo deja en `remotion/public/narrado/`. Frase a frase sonaba «a trompicones» ({{TITULAR}}, 28-sep): nunca se pegan tomas sueltas en un narrado.
3. `bash remotion/render.sh vN Narrado <nombre>` saca el borrador de 16:9 y el móvil.
4. Hay que mirarlo: hoja de contactos de cada capítulo.

Escenas (`Narrado.tsx`):
- Estilo KAI: `imagen`, `dosCaras`, `contadores`, `estrella`, `diagrama`, `lista`, `cita`, `repo`.
- De la v18: `golpes`, `foto`, `anillo`, `visores` (con `en_zoom`), `ned`.
- `pasos` encadena varias escenas en un capítulo; cada paso entra con `desde`, la palabra que la dice.
- **A pantalla completa, sin marco de ventana** ({{TITULAR}}: «el otro era más visual que tenerlo todo metido en una pantalla de navegador»). Cada elemento entra al decir su palabra (`en`).

**Google Flow** ({{TITULAR}}: «úsalo, es lo que queremos»):
- Solo para planos de ambiente **sin datos, sin texto y sin biología inventada** (portada, cierre). Regla del 25-sep: en planos con cifras inventa y recorta.
- Se usa en su cuenta desde Chrome, con 16:9 y Omni 1.1 Flash. Cuesta 15 puntos el clip de 10 s a 720p, y cada generación se aprueba de una en una.
- Se descarga a `07 · Marca/Videos-Polaris/flow/`, se revisa fotograma a fotograma y se copia sin audio a `remotion/public/narrado/flow/`.
- En el guion va como `"video"` en la escena `portada` o `cierre`.
