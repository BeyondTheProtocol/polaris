"""tools/laminillas_comun.py — utilidades de los procesadores de laminillas (corren en la jaula).

Solo stdlib: lo importan scripts que corren con el intérprete de `patologia` o de `valis`.

«Hecho» por lámina y tesela (plan F1-infra): tras un 98 (colgado) o 99 (techo de memoria) la
ventanilla se relanza y el procesador salta lo que ya está sellado. El sello guarda el sha256 de lo
producido: un «hecho» cuyo producto ya no casa con su sha256 no vale (se rehace).
"""
import hashlib
import json
import os
import re
import time

_RE_CLAVE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def sesion():
    """SESION la fija la ventanilla con el cwd; los procesadores no reciben rutas."""
    if os.environ.get("BTP_VENTANILLA") != "1":
        raise SystemExit("este procesador solo corre por la ventanilla (lector_clinico procesa)")
    return os.getcwd()


def _clave(*partes):
    for p in partes:
        if not _RE_CLAVE.match(str(p)):
            raise ValueError("clave de «hecho» no opaca: %r" % (p,))
    return "__".join(str(p) for p in partes)


def ruta_hecho(base, paso, lamina, tesela=None):
    partes = [paso, lamina] + ([str(tesela)] if tesela is not None else [])
    return os.path.join(base, "hecho", _clave(*partes) + ".json")


def sha256_fichero(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def esta_hecho(base, paso, lamina, tesela=None):
    r = ruta_hecho(base, paso, lamina, tesela)
    try:
        with open(r, encoding="utf-8") as f:
            sello = json.load(f)
    except (OSError, ValueError):
        return False
    for rel, sha in sello.get("productos", {}).items():
        p = os.path.join(base, rel)
        if not os.path.isfile(p) or sha256_fichero(p) != sha:
            return False
    return True


def marca_hecho(base, paso, lamina, tesela=None, productos=()):
    """Sella el «hecho» con el sha256 de cada producto (rutas relativas a `base`)."""
    r = ruta_hecho(base, paso, lamina, tesela)
    os.makedirs(os.path.dirname(r), mode=0o700, exist_ok=True)
    sello = {"paso": paso, "lamina": lamina, "tesela": tesela,
             "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "productos": {p: sha256_fichero(os.path.join(base, p)) for p in productos}}
    tmp = r + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sello, f, ensure_ascii=False, indent=1)
    os.replace(tmp, r)
    return r


# ── manifiesto de SESION (F1.2) ─────────────────────────────────────────────────────────────
# Lo escriben varios procesadores (ingesta, QC, identidad) en jaulas distintas: cerrojo y
# escritura atómica. Nunca lleva nombre, accesión ni fecha: es candidato a N1 (por la Puerta).
def ruta_manifiesto(base):
    return os.path.join(base, "manifiesto.json")


def lee_manifiesto(base):
    try:
        with open(ruta_manifiesto(base), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"version": 1, "laminas": {}}


def escribe_json(ruta, obj, modo=0o600):
    """JSON atómico, creado con `modo` (0600 por defecto: SESION y ORIGEN son zona clínica)."""
    tmp = ruta + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modo)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, ruta)
    os.chmod(ruta, modo)
    return ruta


def actualiza_manifiesto(base, fn):
    """fn(manifiesto) lo modifica en sitio; bajo flock y con escritura atómica."""
    import fcntl
    with open(os.path.join(base, ".manifiesto.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        man = lee_manifiesto(base)
        fn(man)
        man["actualizado"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        escribe_json(ruta_manifiesto(base), man)
    return man
