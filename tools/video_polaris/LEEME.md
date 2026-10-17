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
| 2b | **Revisar las tomas** (texto exacto y, en EN, acento americano) y fijar las buenas en `tomas` | `~/claudecode/.venv/bin/python revisa_tomas.py <build>` | v19 (29-sep): con Eleven v4 la etiqueta de acento no pega en todas las tomas y una dijo «Ned» en vez de «N-E-D». La más viva no basta |
| 3 | Tiempos de palabra (Whisper local) | `~/claudecode/.venv/bin/python palabras.py <build>` | — |
| 4 | Montaje sobre la música + voz nivelada | `python3 montaje.py <build> <musica.mp3>` | ElevenLabs entrega cada frase a su volumen: de -16 a -28 LUFS. Se iguala cada una a -18 con ganancia fija. Música de la v18/v19: `musica-elevenlabs-tech-65s.mp3`. Si su voz llega a «Polaris» después del drop del tema, la música entra más tarde (`pieza_intro`) |
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

## Vídeo de Orellana: volver a montar el taller (4-oct-2026)

`remotion/public`, `node_modules` y `out/` no están en git. El worktree del rodaje se podó entre el 3 y el 4 de octubre (ya estaba fusionado y limpio) y con él se fueron los recursos, los clips procesados y las tomas de voz. Desde entonces las fuentes viven fuera del worktree, en `_data/orellana-video-2026-10-02-fuera-de-rag/produccion/`, y el taller se rehace con una orden:

```bash
python3 tools/video_polaris/taller_orellana.py      # recursos, fotos, clips de ambiente, tiempos de la v8, música, efectos, npm ci
```

| Qué cambia | Cómo se renderiza | Voz |
|---|---|---|
| Solo la imagen (una escena, un rótulo, un fondo) | editar `guiones/orellana-circulo.es.json` o `Narrado.tsx`, `taller_orellana.py` y `AUDIO_DE=<máster v8> DESTINO=… bash remotion/render.sh vN Narrado orellana-circulo` | la pista del máster, copiada sin recodificar |
| Pausas, música o efectos | `narrado.py guiones/orellana-circulo.es.json <produccion>/build-orellana-v4` y `render.sh` sin `AUDIO_DE` | las mismas tomas: van firmadas y no se vuelven a pedir |
| Lo que dice | lo mismo, con su OK al texto | se piden a ElevenLabs el capítulo que cambia y sus vecinos (el anterior y los dos siguientes: la firma de cada toma incluye el texto de al lado), 3 tomas de cada uno. El resto no se toca |

Comprobado el 4-oct: con los tiempos de la v8 y `AUDIO_DE`, el render sale con el audio idéntico al de la v8 (mismo MD5) y la imagen igual fotograma a fotograma (SSIM medio 0,9996; el peor, 0,995).

**Las tomas perdidas se recuperan del historial de ElevenLabs** (solo lectura, no gasta): `produccion/recupera_tomas.py` las baja y `produccion/instala_tomas.py` las coloca con su firma. En el historial, las de Eleven v4 salen **sin `voice_id` y sin texto**: si se filtra por voz no aparecen. Van en orden de petición, capítulo a capítulo, a, b, c. Con las 33 recuperadas, `narrado.py` volvió a dar la línea de tiempo de la v8 palabra por palabra.

Para mirar un render: `hoja.sh` (hoja de contactos), `huecos.py` (tramos con el escenario vacío) y `vertical.py` (máster vertical con el aviso de voz).

### v9 (4-oct-2026): su voz en una sola toma y sus propios vídeos

{{TITULAR}} pidió «que toda la voz tenga continuidad en todo el tiempo que se habla». Lo que se aprendió:

| Qué | Cómo queda |
|---|---|
| Voz | `"toma_unica": "entera"`: toda la narración en UNA petición a Eleven v4 (2.400 caracteres caben). Las pausas se estiran solo dentro del silencio y el volumen se iguala por capítulo (la toma arranca alta y va bajando 4,5 dB). `continuidad.py <build>` mide tono, volumen y ritmo por capítulo |
| Ritmo | La toma entera corre un 9 % más que las tomas por capítulo, y la v4 **no admite `speed`** (lo acepta y lo ignora). No se usa `atempo`: se da aire entre frases (`pausa_entre_frases`) |
| Palabras mal acentuadas | La v4 dice «neuroendócrina» 2 de cada 3 veces, «cáncer de mamá» 1 de cada 2 y «Billón de Protocol» 6 de cada 10. Whisper lo delata en la grafía: `evitar` y `exigir` en el guion. Una tilde donde va el acento (`decir`: «neuroendocrína») lo corrigió en todas las tomas |
| Ninguna toma limpia | De diez, ninguna. `mezcla_tomas.py` compone la toma base con los capítulos de otra toma ENTERA, cortando en el silencio; en el guion, `"tomas": {"entera": "<letra>"}` |
| Exactitud | `difflib` con `autojunk` da cifras absurdas en textos de más de 200 caracteres: siempre `autojunk=False` |
| Sus vídeos (cámara DJI) | 300 clips en Drive («Vídeos DJI — brutos para presentación»); la cuenta de servicio no los ve, hay que entrar con el OAuth de usuario. Barrido por dentro (un fotograma cada 4 s) antes de elegir: la miniatura engaña. Los elegidos, en `produccion/dji-hoy/clips` |
| Privacidad de los planos | **Mirar a resolución completa antes de usar un plano.** La consola de radioterapia enseñaba nombres y un número de historia de otros pacientes; el portátil, su nombre y fecha de nacimiento; el congreso, a terceros. `foco_lesion` desenfoca todo menos la lesión |

Escenas nuevas de `Narrado.tsx`: `fondos` (varios planos por capítulo, anclados a palabras), `clip`, `visores2`, `alcance`, `carrera`, `marca`.

### v10 (5-oct-2026): los planos los elige ella, viéndolos

{{TITULAR}}: «vamos a elegir los clips primero y luego ponemos bien los vídeos». Misma voz y mismo texto que la v9; cambian las escenas.

| Qué | Cómo queda |
|---|---|
| Elegir | Una página de miniaturas no le sirvió: quiere los tramos **reproducibles en el chat**, numerados por posición, y contesta con una captura del que comenta. Proxies de 960×540 sin sonido; los finalistas y quién decidió cada uno, en `produccion/dji-hoy/para-elegir/finalistas/finalistas.json` |
| El inventario mentía | El del 4-oct se hizo mientras aún subían ficheros: 300 clips de 467. Faltaban los de más de 1 GB (mesa redonda, sala y consola de la SBRT) y toda la tanda del 23-jun al 4-jul. Antes de decir «no hay clip de X», rehacer el inventario |
| Planos limpios | Escena `clips`: varios planos **a corte** en una ventana, anclados a palabras (`desde`), sin velo ni palabras grandes; `rotulo` es una píldora pequeña. Palabras grandes solo en cuatro frases |
| Encuadre que sigue | `planos_seguidos.py`: en un plano a pulso de una pantalla, el recorte sigue a lo que importa fotograma a fotograma (`pantalla_tac` por un rótulo verde del programa; `tarjeta` por el bloque oscuro) |
| Tapar sin que se note | `tapa()` desenfoca rectángulos con el borde difuminado (caras al fondo, un monitor de pared); `acerca()` va de plano entero a un encuadre cerrado. En `zoompan` hay que poner `fps=30` antes y `setpts=N/(30*TB)` después, o repite cada fotograma mil veces |
| Lo que se coló esta vez | A 1080p se leía un terminal con el principio de una clave de API (plano de su monitor: ahora desenfocado). A 540p no se veía: **la comprobación se hace al tamaño final** |
| «IA» dicho en inglés | La v4 lee a veces «IA» como en inglés (3 tomas de 14) y Whisper escribe «IA» igual, así que `evitar`/`exigir` no lo ven; lo oyó {{TITULAR}}. `ia_es.py <build> [letras]` lo mide: en español la palabra acaba en /a/ y dura 0,22 s; en inglés acaba en /i/ y dura 0,33 s. Pasarlo a toda toma antes de elegirla |
| El giro de los visores 3D a trompicones | Tres causas, las tres medidas (`mide_giro.py` sobre la secuencia; cambio entre fotogramas seguidos sobre el vídeo): (1) la captura va a 20 fps dentro de 30, con un fotograma repetido de cada tres: `giros_fluidos()` del taller la reparte a paso parejo; (2) la secuencia no cierra el bucle (del último al primero hay un salto): `Secuencia` con `desde` va y vuelve; (3) **todo el escenario saltaba 1 px cada medio segundo** porque la flotación iba en `top` y el navegador la redondea al píxel: ahora va en `transform`. Nunca animar con `top`/`left` algo que se mueve despacio |
| Sin planos borrosos | No le gustan los planos desenfocados enteros. Antes de tapar, buscar un encuadre que deje fuera lo que no puede verse (`lesion_cerca` en `planos_seguidos.py`) u otro plano |
| Variante con subtítulos en todas las frases (7-oct) | Los subtítulos quitan solos lo que ya está escrito en pantalla; para la variante que los saca siempre, `REMOTION_SUBS_SIEMPRE=1` delante del render y `AUDIO_DE=<máster>` para copiar la misma pista de audio: `REMOTION_SUBS_SIEMPRE=1 DESTINO=… AUDIO_DE=…v10-1920x1080-BORRADOR.mp4 bash render.sh v10-subtitulos Narrado orellana-circulo`. En la v10 son 39 frases más; comprobado fotograma a fotograma que no pisan nada |

## Voz en Eleven v4, inglés americano (v19, 29-sep-2026)

{{TITULAR}} oyó la v4 frente a la v2 a ciegas y dijo «me parece espectacular de verdad». Lo que hay que saber:
- **Su clon en v4 sale británico sin etiqueta** (clasificador local: england 0,72). `[American accent]` no basta en todas las frases; `[General American accent]` con stability 0.45 sí. Va en `prefijo` del guion, con `language_code: "en"`.
- **v4 no admite style** (la API: `can_use_style=False`) **ni entrenamiento** (`can_be_finetuned=False`): no es cosa de su plan, es del modelo. Lo que suena es ya lo mejor que da la v4 con su clon.
- Sale ~7 % más lenta que la v2 (v19: 83,8 s frente a 78,2 s). El montaje se adapta solo.
- Remotion: `npm ci` en `remotion/` (el `node_modules` no viaja entre worktrees) y `public/narrado/timeline.json` tiene que existir aunque solo se renderice «Polaris» (lo importa `Root.tsx`).
- Para volver a la v18: `model: eleven_multilingual_v2`, sin `prefijo` ni `language_code`.
- **Español (ES v2):** `language_code: "es"` y sin etiqueta. La v4 decía «Convertió» y «mapas 3»: el campo `decir` de la frase lleva la grafía para la voz («Convirtio… tres D»). En pantalla y en los tiempos va el texto firmado.
- Si «Polaris» cae después del drop del tema (ES v2: +3,43 s), `montaje.piezas_intro` repite los primeros compases de la intro, cortando en compás entero: hay música desde el segundo 0.

## Versión ES con sus capturas (ES v3, 29-sep-2026)

- Capturas de la web en español: `node capturar.mjs <dir> --idioma es` y `python3 preparar.py --web <dir> remotion/public/es --idioma es`. Remotion las coge de `public/<textos.web.dir>` con `captura()`. Los nombres de fichero no cambian: la escena web compara por nombre.
- `RECORTES_ES` en `preparar.py`: en `/datos` el campo de puntos sube 20 px y justo encima está «731». No subas más la caja.
- La geometría de la cadena va por idioma (`textos.cadena`): en ES, «Pruebas y mensajes» se salía de la caja. El test mide el texto contra la caja.
- `semilla` por frase en el guion: fija la toma cuando solo una suena bien. En ES 3d, el tono subía al final de «convirtió»; se mide con Whisper, palabra a palabra, contra la ES v1.
- `cifras.json`, del día del render (`anatomia.py inventario`).
