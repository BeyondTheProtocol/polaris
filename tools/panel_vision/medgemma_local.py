#!/usr/bin/env python3
"""tools/panel_vision/medgemma_local.py — MedGemma 4B (`google/medgemma-4b-it`) en LOCAL para el
panel de visión de laminillas DFCI (plan, actualizaciones 3 y 15: «MedGemma, pendiente de que
{{TITULAR}} acepte sus condiciones»). Revisa capas; no puntúa biomarcadores. NADA sale del Mac: no pasa
por `vision_n1` porque no hay proveedor externo.

CUÁNDO CORRE: solo si `tools/laminillas_stack/pesos.json` (verificado contra su `.sha256`) tiene la
entrada `medgemma` con el repo, el commit de 40 hex, estado «OK» y la lista de ficheros con bytes y
sha256, y esos ficheros están en la caché de Hugging Face EN ESE COMMIT
(`$HF_HOME/hub/models--google--medgemma-4b-it/snapshots/<commit>/`). Antes de cargar se comprueban
bytes y sha256 de cada uno. Se carga con transformers SIN RED (`HF_HUB_OFFLINE=1`,
`TRANSFORMERS_OFFLINE=1`, `local_files_only=True`), en MPS y bfloat16; con decodificación voraz
(temperatura 0 de hecho) y `max_new_tokens` fijo. Una imagen por petición, sin historial.

PESOS (2-oct-26): el repo es «gated»; {{TITULAR}} aceptó las condiciones de Health AI Developer
Foundations y la ventanilla de pesos (F1.0: `lector_clinico.py procesa laminillas_pesos --
medgemma`, jaula red-sin-zona, HF_TOKEN del Llavero leído por el padre en gui/501) bajó y selló el
`main` de ese día: commit 290cda5eeccbee130f987c4ad74a59ae6f196408, 14 ficheros (~8,6 GB, dos
safetensors bf16), sha256 en pesos.json; el sha256 de los 4 ficheros LFS casa con el que declara
el servidor (nombre del blob en la caché). Desde aquí no se descarga nada ni se llama a Hugging
Face. Sin la entrada sellada, sin los ficheros en la caché o con un byte distinto, `estado()` da
«pendiente de acceso» y `comprueba()` lanza `PendienteDeAcceso`; el panel (`calibra.py modelos`)
lo marca así.

HUMO REAL (2-oct-26, sandbox sin red —ni DNS—, MPS, bfloat16, transformers 4.57.6, torch 2.14.1):
`comprueba()` 3,8 s; carga + 1.ª respuesta 51,6 s, las siguientes ~11,5 s; misma salida dos veces
seguidas; responde el JSON envuelto en ```json, que `calibra.interpreta` acepta. Con el modelo
cargado ocupa 8,65 GB de MPS y el Mac de 16 GB queda al 12 % libre: en lotes, se descarga antes de
medir la memoria de cada uno. Los tests sustituyen la carga por un cargador falso (`cargador=`).
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calibra import AdaptadorPendiente  # noqa: E402 — calibra es solo stdlib

REPO = "google/medgemma-4b-it"
ENTRADA = "medgemma"
PENDIENTE = "pendiente de acceso"
LISTO = "listo"
_AQUI = os.path.dirname(os.path.abspath(__file__))
PESOS = os.path.join(os.path.dirname(_AQUI), "laminillas_stack", "pesos.json")
HF_HOME_DEFECTO = os.path.expanduser("~/.polaris-venvs/cache/hf")
MAX_NUEVOS = 256
RE_COMMIT = re.compile(r"^[0-9a-f]{40}$")
MODELOS_VALIDOS = ("", REPO, "medgemma-4b-it")


class PendienteDeAcceso(AdaptadorPendiente):
    """MedGemma no se puede usar todavía (gated, sin pesos sellados o con pesos que no casan). No
    se ha cargado ni enviado nada."""


def _hf_home(hf_home=None):
    return hf_home or os.environ.get("HF_HOME") or HF_HOME_DEFECTO


def _entrada(pesos):
    """La entrada `medgemma` de pesos.json, o (None, motivo)."""
    try:
        with open(pesos, "rb") as fh:
            crudo = fh.read()
        with open(pesos + ".sha256", encoding="utf-8") as fh:
            sellado = fh.read().split()[0]
    except (OSError, IndexError):
        return None, "pesos.json o su .sha256 no se pueden leer"
    if hashlib.sha256(crudo).hexdigest() != sellado:
        return None, "pesos.json no casa con su .sha256 (¿editado?)"
    try:
        ent = (json.loads(crudo.decode("utf-8")).get("modelos") or {}).get(ENTRADA)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, "pesos.json ilegible"
    if not isinstance(ent, dict):
        return None, ("%s es gated en Hugging Face y no hay pesos sellados (sin entrada «%s» en "
                      "pesos.json): falta que {{TITULAR}} acepte sus condiciones y bajarlos con commit y "
                      "sha256" % (REPO, ENTRADA))
    if ent.get("repo") != REPO or not RE_COMMIT.match(str(ent.get("commit"))) or ent.get("estado") != "OK" \
            or not isinstance(ent.get("ficheros"), dict) or not ent["ficheros"]:
        return None, "la entrada «%s» de pesos.json no está sellada (repo, commit, estado OK, ficheros)" % ENTRADA
    return ent, None


def _carpeta(ent, hf_home):
    return os.path.join(_hf_home(hf_home), "hub", "models--" + REPO.replace("/", "--"), "snapshots", ent["commit"])


def estado(pesos=PESOS, hf_home=None):
    """(«pendiente de acceso» | «listo», motivo). Barato: no calcula sha256 (eso, `comprueba`)."""
    ent, motivo = _entrada(pesos)
    if ent is None:
        return PENDIENTE, motivo
    faltan = [f for f in ent["ficheros"] if not os.path.isfile(os.path.join(_carpeta(ent, hf_home), f))]
    if faltan:
        return PENDIENTE, "faltan %d fichero(s) del commit sellado en la caché" % len(faltan)
    return LISTO, "commit %s, %d ficheros" % (ent["commit"][:12], len(ent["ficheros"]))


def verifica_pesos(pesos=PESOS, hf_home=None):
    """(carpeta del snapshot, entrada) con bytes y sha256 de cada fichero comprobados."""
    ent, motivo = _entrada(pesos)
    if ent is None:
        raise PendienteDeAcceso("MedGemma (%s): %s. %s." % (REPO, motivo, PENDIENTE))
    carpeta = _carpeta(ent, hf_home)
    for nombre, meta in sorted(ent["ficheros"].items()):
        ruta = os.path.join(carpeta, nombre)
        if not os.path.isfile(ruta):
            raise PendienteDeAcceso("MedGemma: %s no está en la caché en el commit sellado. %s." % (nombre, PENDIENTE))
        h, n = hashlib.sha256(), 0
        with open(ruta, "rb") as fh:
            for trozo in iter(lambda: fh.read(1 << 20), b""):
                h.update(trozo)
                n += len(trozo)
        if n != int((meta or {}).get("bytes", -1)) or h.hexdigest() != (meta or {}).get("sha256"):
            raise PendienteDeAcceso("MedGemma: %s no son los bytes sellados: no cargo nada" % nombre)
    return carpeta, ent


def cargador_transformers(carpeta, dispositivo="mps"):
    """Devuelve `genera(prompt, imagen_pil) -> texto`, con el modelo cargado SIN RED desde `carpeta`."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    if dispositivo == "mps" and not torch.backends.mps.is_available():
        raise PendienteDeAcceso("MedGemma: MPS no disponible en este proceso; no lo bajo a CPU en silencio")
    proc = AutoProcessor.from_pretrained(carpeta, local_files_only=True)
    modelo = AutoModelForImageTextToText.from_pretrained(carpeta, local_files_only=True,
                                                         torch_dtype=torch.bfloat16).to(dispositivo)
    modelo.eval()

    def genera(prompt, imagen):
        mensajes = [{"role": "user", "content": [{"type": "image", "image": imagen},
                                                 {"type": "text", "text": prompt}]}]
        ent = proc.apply_chat_template(mensajes, add_generation_prompt=True, tokenize=True,
                                       return_dict=True, return_tensors="pt").to(dispositivo, dtype=torch.bfloat16)
        n = ent["input_ids"].shape[-1]
        with torch.inference_mode():
            sal = modelo.generate(**ent, max_new_tokens=MAX_NUEVOS, do_sample=False)
        return proc.decode(sal[0][n:], skip_special_tokens=True)

    return genera


class MedGemmaLocal:
    """Adaptador del arnés (`calibra.correr` / `revisa_capas`): `comprueba()` y
    `responder(prompt, ruta_png, esquema)`. Local: la imagen se lee del disco y no sale."""
    local = True

    def __init__(self, modelo="", pesos=PESOS, hf_home=None, dispositivo="mps", cargador=None):
        if modelo not in MODELOS_VALIDOS:
            raise ValueError("medgemma: el modelo es %s (llega %r)" % (REPO, modelo))
        self.pesos, self.hf_home, self.dispositivo = pesos, hf_home, dispositivo
        self._cargador = cargador or cargador_transformers
        self._genera = None
        self.nombre = "medgemma:" + REPO
        self.destino = None
        self.config = {"adaptador": "medgemma-local", "modelo": REPO, "dispositivo": dispositivo,
                       "decodificacion": "voraz", "max_new_tokens": MAX_NUEVOS, "dtype": "bfloat16"}

    def comprueba(self):
        carpeta, ent = verifica_pesos(self.pesos, self.hf_home)
        self.config["commit"] = ent["commit"]
        self._carpeta = carpeta
        return dict(self.config)

    def responder(self, prompt, ruta_png, esquema):
        if self._genera is None:
            if "commit" not in self.config:
                self.comprueba()
            self._genera = self._cargador(self._carpeta, self.dispositivo)
        from PIL import Image
        with Image.open(ruta_png) as im:
            imagen = im.convert("RGB")
        return self._genera(prompt, imagen)
