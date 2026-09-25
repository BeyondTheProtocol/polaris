# Vídeo cuadrado de los visores para X

Hígado y mama girando a la vez, 1080×1080, bucle sin costura, con los materiales, los colores y
los rótulos del visor publicado en `helptitular.com/lesiones`.

**Por qué cuadrado:** es lo que ocupa más alto en el timeline de X y es el formato que ya usaba la
pieza anterior del hígado. 8 s a 30 fps = una vuelta completa, así que el bucle encaja solo.

**Lo que se ve, y por qué así:** las dos lesiones diana llevan anillo y la medida del radiólogo;
el resto va sin anillo y la franja de abajo dice que son detección automática sin validar. Ningún
número se presenta como recuento de nadie. Es la misma regla que la imagen de respaldo de la web.

## Cómo se usa

Los datos son los **públicos** de helptitular.com, no hay nada clínico crudo aquí.

```sh
mkdir -p vx && cd vx
# 1. mallas y escenas públicas
for org in higado mama; do
  mkdir -p $org
  curl -s -o $org/escena.json https://helptitular.com/lesiones/$org/escena.json
  # y cada .ply que nombre ese escena.json, desde https://helptitular.com/lesiones/$org/<malla>
done
# 2. three.js (del repo de la web): build/three.module.js, build/three.core.js,
#    examples/jsm/loaders/PLYLoader.js y examples/jsm/environments/RoomEnvironment.js
#    en ./three/ respetando esa estructura
cp .../x.html .../x.js .../grabar-x.mjs .
python3 -m http.server 8731 &
node grabar-x.mjs "http://127.0.0.1:8731/x.html" frames 240
ffmpeg -y -framerate 30 -i frames/%04d.png -c:v libx264 -profile:v high -pix_fmt yuv420p \
       -crf 19 -movflags +faststart -r 30 higado-mama-x-1080.mp4
```

`x.js` tiene arriba las tres constantes que se tocan: `CORTE` (dónde parte el marco), y los
factores de `dH` y `dM` (cuánto llena cada órgano su panel).

⚠️ `three.module.js` necesita `three.core.js` al lado o la página carga en blanco sin decir nada.
