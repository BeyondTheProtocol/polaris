#!/bin/bash
# _ejecuta.sh — PLANTILLA del trabajo CellViT++ que corre EN LA MÁQUINA GPU de la nube (no en el Mac).
# La rellena tools/nube_n1.py (`lanzar`): cada marcador entre dobles arrobas se sustituye por un
# valor ya validado y citado con shlex.quote; si queda alguno, nube_n1 no lanza. Va dentro del cloud-init versionado
# (cellvit.cloud-init.yaml), en base64.
#
# Qué hace, en orden (cualquier fallo escribe el objeto FAILED con el paso, el código y la última
# línea del log, saneada, y sale):
#   gpu          nvidia-smi
#   docker       docker pull de la imagen por DIGEST (nunca por etiqueta), con tope de tiempo
#   entradas     baja cada copia N1 con su URL prefirmada y comprueba su sha256
#   checkpoints  baja los pesos de CellViT++ y comprueba el sha256 sellado (sin sha, no corre)
#   instala      pip install de la lista EXACTA de config.json (`pip`, todo ==): solo ruedas, salvo los
#                paquetes que en PyPI no tienen rueda (`pip_solo_fuente`), que se construyen con el
#                setuptools/wheel de la imagen (--no-build-isolation: nada sin fijar baja de la red).
#                Con tope de tiempo: un pip colgado es FAILED, no seis horas de máquina
#   comprueba    SIN RED, en el contenedor congelado: cada pin instalado con su versión exacta, y
#                torch, openslide (su librería de openslide-bin) y cellvit importan
#   cellvit      inferencia por clasificador en un contenedor SIN RED (--network none): con los
#                pesos ya en la caché, CellViT++ no puede bajar nada sin comprobar
#   enganche     cells.json → P-HE.<clasificador>.csv + .json (formato de laminillas_he)
#   resultados   sube cada fichero con su URL prefirmada de PUT
#   fin          escribe el objeto DONE
# La máquina NO se apaga ni se borra sola (no lleva ninguna clave del proveedor): la borra
# nube_n1 `lanzar` desde el Mac al ver DONE o FAILED (una máquina apagada sigue facturando).
set -Eeuo pipefail
umask 077
PASO=inicio
R=${BTP_RAIZ:-/root/btp}      # donde el cloud-init deja los ficheros; los tests lo apuntan a un temporal
URL_FAILED=@@URL_FAILED@@
falla() {
  local rc=$? ultimo=''
  trap - ERR
  set +e
  # La última línea con texto del log (pip, docker, curl… nunca imprimen las URLs prefirmadas),
  # saneada: al ver FAILED la máquina se borra y el log se va con ella.
  ultimo=$(grep -v '^[[:space:]]*$' "$R/ejecuta.log" 2>/dev/null | tail -n 1 |
           tr -c 'A-Za-z0-9=_.:-' ' ' | tr -s ' ' | tail -c 120)
  printf 'FAILED trabajo=%s paso=%s rc=%s ultimo=%s\n' @@TRABAJO@@ "$PASO" "$rc" "$ultimo" > "$R/failed.txt"
  curl -fsS --retry 3 -X PUT --upload-file "$R/failed.txt" "$URL_FAILED"
  exit 1
}
trap falla ERR

W=$R/trabajo
if [ -d /scratch ] && [ -w /scratch ]; then W=/scratch/btp; fi
mkdir -p "$W/in" "$W/out" "$W/res" "$W/cache"
cp "$R/a_enganche.py" "$R/mapa.json" "$W/"

baja() {   # baja <url> <fichero> <sha256>
  curl -fsSL --retry 3 -o "$2" "$1"
  echo "$3  $2" | sha256sum -c --status || { echo "sha256 no casa: ${2##*/}"; return 1; }
}
sube() {   # sube <fichero> <url>
  curl -fsS --retry 3 -X PUT --upload-file "$1" "$2"
}

# Corre DENTRO del contenedor congelado (python de la imagen, 3.10): argv = los pins exactos.
COMPRUEBA='
import json, re, subprocess, sys
from pip._vendor.packaging.version import Version
c = lambda s: re.sub(r"[-_.]+", "-", s).lower()
pl = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
                    capture_output=True, text=True, check=True).stdout
hay = {c(d["name"]): d["version"] for d in json.loads(pl)}
mal = [p for p in sys.argv[1:]
       if c(p.split("==")[0]) not in hay or Version(hay[c(p.split("==")[0])]) != Version(p.split("==")[1])]
if mal:
    sys.exit("comprueba: pins sin instalar o con otra version: " + " ".join(mal))
import torch, openslide, cellvit.inference.inference
print("comprueba ok: python", sys.version.split()[0], "torch", torch.__version__,
      "openslide", openslide.__library_version__, "pins", len(sys.argv) - 1)
'

PASO=gpu
nvidia-smi -L

PASO=docker
timeout 1800 docker pull @@IMAGEN@@

PASO=entradas
@@BAJA_ENTRADAS@@

PASO=checkpoints
@@BAJA_CHECKPOINTS@@
if [ -f "$W/cache/classifier.zip" ]; then
  (cd "$W/cache" && python3 -m zipfile -e classifier.zip . && rm -f classifier.zip)
fi

PASO=instala
timeout 2700 docker run --name btp-instala @@IMAGEN@@ pip install --no-cache-dir --disable-pip-version-check \
  --only-binary=:all: --no-binary=@@SOLO_FUENTE@@ --no-build-isolation @@PINS@@
docker commit btp-instala btp-cellvit:local
docker rm btp-instala

PASO=comprueba
docker run --rm --network none btp-cellvit:local python -c "$COMPRUEBA" @@PINS@@

PASO=cellvit
for C in @@CLASIFICADORES@@; do
  docker run --rm --gpus all --network none -e CELLVIT_CACHE=/w/cache -v "$W":/w btp-cellvit:local \
    cellvit-inference --model @@MODELO@@ --nuclei_taxonomy "$C" --outdir "/w/out/$C" \
    process_wsi --wsi_path /w/in/@@ENTRADA@@ --wsi_mpp @@MPP@@ --wsi_magnification @@MAGNIF@@
  PASO="enganche-$C"
  docker run --rm --network none -v "$W":/w btp-cellvit:local \
    python /w/a_enganche.py --cells "/w/out/$C/"@@STEM@@"/cells.json" --clasificador "$C" \
    --mpp @@MPP@@ --mapa /w/mapa.json --modelo @@NOMBRE_MODELO@@ --version @@VERSION@@ --salida /w/res
  PASO=cellvit
done

PASO=resultados
@@SUBE_RESULTADOS@@

PASO=fin
printf 'DONE trabajo=%s\n' @@TRABAJO@@ > "$R/done.txt"
curl -fsS --retry 3 -X PUT --upload-file "$R/done.txt" @@URL_DONE@@
