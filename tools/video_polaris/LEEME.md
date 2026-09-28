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
