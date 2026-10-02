#!/usr/bin/env python3
"""tools/laminillas_stack.py — instala y sella los venvs de laminillas DENTRO de `red-sin-zona.sb`.

Plan «laminillas DFCI», F2. Los venvs viven fuera del repo (`~/.polaris-venvs/patologia` y
`~/.polaris-venvs/valis`) y se instalan con red pero SIN zona clínica ni Llavero: `sandbox-exec`
con el perfil `red-sin-zona` y entorno fregado (equivale a `env -i`). Si pip falla por TLS se
arregla la regla mach del perfil; nunca se instala fuera de la jaula.

El `pip freeze` de cada venv se sella (sha256) en `tools/laminillas_stack/<venv>.freeze`, versionado.

Uso:  python3 tools/laminillas_stack.py corre <venv> -- <comando…>   # cualquier orden, en la jaula
      python3 tools/laminillas_stack.py crea <venv>                  # python3.12 -m venv
      python3 tools/laminillas_stack.py instala <venv>               # el stack del plan
      python3 tools/laminillas_stack.py sella <venv>                 # freeze + sha256 al repo
"""
import hashlib
import os
import subprocess
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
import laminillas_jaulas as J  # noqa: E402

PYTHON_BASE = "/opt/homebrew/bin/python3.12"
VENV_NOMBRES = ("patologia", "valis")
SELLOS = os.path.join(os.path.dirname(_AQUI), "tools", "laminillas_stack")

# F2 «patologia». torch: el plan dice 2.14.1; si no hay wheel arm64, la que resuelva y se declara.
STACK = {
    "patologia": [
        ["install", "--only-binary=:all:", "--upgrade", "pip"],
        ["install", "--only-binary=:all:",
         "lazyslide==0.12.0", "lazyslide-models==0.0.4", "wsidata==0.11.1",
         "instanseg-torch==0.1.2", "wsinfer==0.6.1",
         "openslide-python", "openslide-bin",
         "torch==2.14.1", "transformers>=4.46,<5", "timm", "huggingface_hub",
         "scikit-image", "tifffile", "shapely"],
        # El código remoto de TITAN (dac6773) importa einops_exts; sin él no carga (humo 1-oct-26).
        ["install", "--only-binary=:all:", "einops-exts==0.0.4"],
    ],
    # Comando EXACTO del plan (choca con tiffslide 4: venv aparte, numpy<2).
    "valis": [
        ["install", "--upgrade", "pip"],
        ["install", "--uploaded-prior-to", "2025-07-31T00:00:00Z",
         "numpy<2", "valis-wsi==1.2.0", "pyvips[binary]"],
    ],
}


def venv_dir(nombre):
    if nombre not in VENV_NOMBRES:
        raise SystemExit("venv desconocido: %s (válidos: %s)" % (nombre, ", ".join(VENV_NOMBRES)))
    return os.path.join(J.VENVS, nombre)


def entorno(nombre, extra=None):
    """Entorno fregado: nada heredado (ni claves, ni HF_TOKEN, ni el PATH de la sesión)."""
    v = venv_dir(nombre)
    home = os.path.join(J.VENVS, "home")
    env = {
        "HOME": home,
        "PATH": "%s/bin:/usr/bin:/bin:/opt/homebrew/bin" % v,
        "TMPDIR": J.TMP_RED,
        "PIP_CACHE_DIR": os.path.join(J.CACHE, "pip"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "HF_HOME": os.path.join(J.CACHE, "hf"),
        "TORCH_HOME": os.path.join(J.CACHE, "torch"),
        "LANG": "en_US.UTF-8",
    }
    env.update(extra or {})
    return env


def _prepara():
    for d in (J.VENVS, J.CACHE, J.TMP_RED, os.path.join(J.VENVS, "home"),
              os.path.join(J.CACHE, "pip")):
        os.makedirs(d, mode=0o700, exist_ok=True)


# Herramientas de compilación (solo lectura). Con `--uploaded-prior-to 2025-07-31` pip no ve la
# rueda de fastcluster 1.3.0 y compila 1.2.6 desde fuente: clang necesita leer Xcode, que
# red-sin-zona no deja leer (`file system sandbox blocked open()`). Desviación mínima del plan,
# solo lectura: mejor que saltarse el pin del plan o instalar fuera de la jaula.
COMPILADOR = [p for p in ("/Applications/Xcode.app", "/Library/Developer/CommandLineTools")
              if os.path.isdir(p)]


def corre(nombre, comando, extra_env=None, cwd=None, captura=False):
    """Lanza `comando` dentro de red-sin-zona.sb. Devuelve el CompletedProcess."""
    _prepara()
    perfil = J.escribe("red-sin-zona", tmpdir=J.TMP_RED,
                       extra_lectura=COMPILADOR if nombre == "valis" else ())
    cwd = cwd or (venv_dir(nombre) if os.path.isdir(venv_dir(nombre)) else J.VENVS)
    return subprocess.run(["/usr/bin/sandbox-exec", "-f", perfil] + list(comando),
                          env=entorno(nombre, extra_env), cwd=cwd,
                          capture_output=captura, text=True)


def crea(nombre):
    v = venv_dir(nombre)
    if os.path.exists(os.path.join(v, "bin", "python")):
        print("ya existe %s" % v)
        return 0
    return corre(nombre, [PYTHON_BASE, "-m", "venv", v], cwd=J.VENVS).returncode


def instala(nombre):
    py = os.path.join(venv_dir(nombre), "bin", "python")
    for paso in STACK[nombre]:
        print("· pip " + " ".join(paso), flush=True)
        rc = corre(nombre, [py, "-m", "pip"] + paso).returncode
        if rc:
            print("FALLO pip (rc=%d) en: %s" % (rc, " ".join(paso)), file=sys.stderr)
            return rc
    return 0


def sella(nombre):
    py = os.path.join(venv_dir(nombre), "bin", "python")
    r = corre(nombre, [py, "-m", "pip", "freeze", "--all"], captura=True)
    if r.returncode:
        print(r.stderr, file=sys.stderr)
        return r.returncode
    v = corre(nombre, [py, "-c", "import sys; print(sys.version.split()[0])"], captura=True)
    texto = "# python %s · %s\n%s" % (v.stdout.strip(), venv_dir(nombre).replace(J.HOME, "~"),
                                      r.stdout)
    os.makedirs(SELLOS, exist_ok=True)
    ruta = os.path.join(SELLOS, nombre + ".freeze")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(texto)
    sha = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    with open(ruta + ".sha256", "w", encoding="utf-8") as f:
        f.write("%s  %s.freeze\n" % (sha, nombre))
    print("%s  %s (%d paquetes)" % (sha, ruta, texto.count("==")))
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] in ("crea", "instala", "sella"):
        return {"crea": crea, "instala": instala, "sella": sella}[argv[0]](argv[1])
    if len(argv) >= 4 and argv[0] == "corre" and argv[2] == "--":
        return corre(argv[1], argv[3:]).returncode
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
