#!/usr/bin/env python3
"""tools/laminillas_pesos.py — F1.0: baja los pesos públicos por commit SELLADO (red sin datos).

Lo lanza SOLO la ventanilla (`lector_clinico.py procesa laminillas_pesos [-- <modelo>…]`) dentro de
`red-sin-zona.sb`: con red, sin zona clínica, sin Llavero; `HF_TOKEN` lo lee el padre del Llavero y
llega por entorno. No instala nada. Escribe solo en la caché propia (`~/.polaris-venvs/cache`).

Por modelo:
  1. `refs/main` remoto. Si el plan sella un commit y `main` no empieza por él → ABORTA ESE modelo
     (los wrappers descargan sin `revision`: bajar otro commit sería medir con otro modelo).
     Sin commit en el plan: se sella el `main` de hoy y se declara («sellado-hoy»).
  2. `snapshot_download(revision=<sha completo>, allow_patterns=…)` y `refs/main` LOCAL apuntando a
     ese sha: así el wrapper, offline y sin `revision`, carga exactamente lo sellado.
  3. sha256 de cada fichero → `cache/manifiesto-pesos.json` (lo versiona quien lo lanzó).

Modo `kornia` (venv valis): DISK('depth') y LightGlue('disk'), los que carga VALIS 1.2.0, a
`TORCH_HOME/hub/checkpoints`, con sha256.
Modo `instanseg`: `brightfield_nuclei` v0.1.1 a `INSTANSEG_BIOIMAGEIO_PATH`, con sha256.
"""
import hashlib
import json
import os
import sys
import time

CACHE = os.path.dirname(os.environ.get("HF_HOME", "")) or os.path.expanduser("~/.polaris-venvs/cache")
MANIFIESTO = os.path.join(CACHE, "manifiesto-pesos.json")

MODELOS = {
    "uni2h": dict(repo="MahmoodLab/UNI2-h", commit="d517a8d",
                  patrones=["config.json", "pytorch_model.bin", "README.md"]),
    "titan": dict(repo="MahmoodLab/TITAN", commit="dac6773",
                  patrones=["*.py", "*.json", "model.safetensors", "conch_v1_5_pytorch_model.bin",
                            "README.md"]),
    "hoptimus1": dict(repo="bioptimus/H-optimus-1", commit="3592cb2",
                      patrones=["config.json", "model.safetensors", "README.md"]),
    # Repo que carga lazyslide_models (cellvit_family/histoplus.py); variante 40x (plan).
    "histoplus": dict(repo="Owkin-Bioptimus/histoplus", commit=None,
                      patrones=["config.json", "histoplus_cellvit_segmentor_40x.pt", "README.md"]),
    "wsinfer_brca": dict(repo="kaczmarj/breast-tumor-resnet34.tcga-brca", commit=None,
                         patrones=["config.json", "torchscript_model.pt", "README.md"]),
    "lazyslide_models": dict(repo="RendeiroLab/LazySlide-models", commit=None,
                             patrones=["instanseg/instanseg_v0_1_0.pt", "NuLite/*_exported.pt2",
                                       "GrandQC/*"]),
    # Panel de visión (panel_vision/medgemma_local.py): gated (condiciones HAI-DEF aceptadas por
    # {{TITULAR}} el 2-oct-26). Todo el repo salvo .gitattributes (~8,6 GB: 2 safetensors bf16).
    "medgemma": dict(repo="google/medgemma-4b-it", commit=None,
                     patrones=["*.json", "*.safetensors", "chat_template.jinja", "tokenizer.model",
                               "README.md"]),
}


def _sha(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for t in iter(lambda: f.read(1 << 20), b""):
            h.update(t)
    return h.hexdigest()


def _carga():
    try:
        with open(MANIFIESTO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"modelos": {}}


def _guarda(man):
    man["actualizado"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = MANIFIESTO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, MANIFIESTO)


def baja_hf(clave):
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE
    m = MODELOS[clave]
    api = HfApi()
    main = api.model_info(m["repo"], revision="main").sha
    if m["commit"] and not main.startswith(m["commit"]):
        return {"estado": "ABORTADO", "motivo": "refs/main=%s no es el sellado %s"
                % (main, m["commit"]), "repo": m["repo"]}
    ruta = snapshot_download(m["repo"], revision=main, allow_patterns=m["patrones"])
    # refs/main local → el sha sellado (los wrappers piden «main» offline).
    carpeta = os.path.join(HF_HUB_CACHE, "models--" + m["repo"].replace("/", "--"))
    os.makedirs(os.path.join(carpeta, "refs"), exist_ok=True)
    with open(os.path.join(carpeta, "refs", "main"), "w") as f:
        f.write(main)
    ficheros = {}
    for base, _, fs in os.walk(ruta):
        for n in sorted(fs):
            p = os.path.join(base, n)
            ficheros[os.path.relpath(p, ruta)] = {"sha256": _sha(p), "bytes": os.path.getsize(p)}
    return {"estado": "OK", "repo": m["repo"], "commit": main,
            "sello": "plan" if m["commit"] else "sellado-hoy",
            "patrones": m["patrones"], "ficheros": ficheros}


def baja_wsinfer_zoo(nombre="breast-tumor-resnet34.tcga-brca"):
    """El registro del zoo (a ~/.wsinfer-zoo, como hace su import) y el modelo en la revisión
    que fija ESE registro, que es la que carga `wsinfer run`."""
    from huggingface_hub import HfApi
    from wsinfer_zoo.client import WSINFER_ZOO_REGISTRY_DEFAULT_PATH, load_registry
    reg = load_registry()
    m = reg.get_model_by_name(nombre)
    hf = m.load_model_torchscript()
    main = HfApi().model_info(m.hf_repo_id, revision="main").sha
    ficheros = {}
    from huggingface_hub import hf_hub_download
    config = hf_hub_download(m.hf_repo_id, "config.json", revision=m.hf_revision)
    for p in (WSINFER_ZOO_REGISTRY_DEFAULT_PATH, hf.model_path, config):
        p = str(p)
        ficheros[os.path.basename(p)] = {"sha256": _sha(p), "bytes": os.path.getsize(p)}
    return {"estado": "OK", "repo": m.hf_repo_id, "commit": m.hf_revision,
            "sello": "registro-wsinfer-zoo", "main_hoy": main, "ficheros": ficheros}


def baja_kornia():
    import torch
    import kornia
    from kornia.feature import DISK, LightGlueMatcher
    DISK.from_pretrained("depth")
    LightGlueMatcher("disk")
    d = os.path.join(torch.hub.get_dir(), "checkpoints")
    ficheros = {n: {"sha256": _sha(os.path.join(d, n)), "bytes": os.path.getsize(os.path.join(d, n))}
                for n in sorted(os.listdir(d))}
    return {"estado": "OK", "kornia": kornia.__version__, "torch": torch.__version__,
            "carpeta": d.replace(os.path.expanduser("~"), "~"), "ficheros": ficheros}


def baja_instanseg():
    from instanseg.utils.utils import download_model
    download_model("brightfield_nuclei", version="0.1.1", verbose=False)
    d = os.environ["INSTANSEG_BIOIMAGEIO_PATH"]
    ficheros = {}
    for base, _, fs in os.walk(d):
        for n in sorted(fs):
            p = os.path.join(base, n)
            ficheros[os.path.relpath(p, d)] = {"sha256": _sha(p), "bytes": os.path.getsize(p)}
    return {"estado": "OK", "version": "0.1.1", "ficheros": ficheros}


def main(argv):
    if os.environ.get("BTP_VENTANILLA") != "1":
        print("solo por la ventanilla (lector_clinico procesa laminillas_pesos)", file=sys.stderr)
        return 2
    pedidos = argv or list(MODELOS) + ["instanseg", "wsinfer_zoo"]
    man = _carga()
    fallos = 0
    for clave in pedidos:
        print("· %s" % clave, flush=True)
        try:
            if clave == "kornia":
                r = baja_kornia()
            elif clave == "instanseg":
                r = baja_instanseg()
            elif clave == "wsinfer_zoo":
                r = baja_wsinfer_zoo()
            elif clave in MODELOS:
                r = baja_hf(clave)
            else:
                r = {"estado": "ERROR", "motivo": "modelo desconocido"}
        except Exception as e:                     # noqa: BLE001 — se registra y se sigue
            r = {"estado": "ERROR", "motivo": "%s: %s" % (type(e).__name__, str(e)[:300])}
        r["fecha"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        man["modelos"][clave] = r
        _guarda(man)
        fallos += r["estado"] != "OK"
        print("  %s %s %s" % (r["estado"], r.get("commit", ""), r.get("motivo", "")), flush=True)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
