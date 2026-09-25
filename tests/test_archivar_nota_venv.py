#!/usr/bin/env python3
"""tests/test_archivar_nota_venv.py — que archivar_nota reindexe con el intérprete QUE PUEDE.

El fallo que lo motiva (20-sep-2026, nota «campaña visores 3D hígado y mama»): archivar_nota.py
importaba kb y llamaba a `kb.build()` EN PROCESO, así que heredaba el intérprete de quien lo
invocó. A este tool se le llama con el `python3` del PATH (Homebrew), que no tiene pypdf; pypdf
vive en el `.venv` del repo. kb.build() hacía lo correcto —negarse antes que dejar el índice sin
los PDFs del historial clínico— y la nota se quedaba fuera del RAG hasta reindexar a mano.

Cubre las tres ramas de `_build`, sin red y sin tocar el índice real:
  · este intérprete SÍ lee PDFs -> construye en proceso (nada de subprocesos);
  · NO lee PDFs y hay venv      -> delega en el venv y devuelve SU código de salida;
  · NO lee PDFs y NO hay venv   -> construye en proceso, para que kb se niegue y lo explique él.
"""
import os
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "tools"))

import archivar_nota as an  # noqa: E402

FALLOS, OK = [], []


def ok(caso, cond, detalle=""):
    (OK if cond else FALLOS).append(caso)
    print(("  ok  " if cond else "  FALLO  ") + caso + ("" if cond else f" {detalle}"))


class KbFalso:
    """Doble de kb: dice si puede leer PDFs y cuenta si le construyeron en proceso."""

    def __init__(self, pypdf, rc=0):
        self._pypdf, self._rc, self.builds = pypdf, rc, 0

    def hay_pypdf(self):
        return self._pypdf

    def build(self):
        self.builds += 1
        return self._rc


tmp = tempfile.mkdtemp(prefix="archnota-venv-")
an.ROOT = tmp
os.makedirs(os.path.join(tmp, "tools"))
MARCA = os.path.join(tmp, "corrio-el-venv")
# kb.py falso: deja rastro de que lo llamaron y sale con el código que le digamos.
with open(os.path.join(tmp, "tools", "kb.py"), "w", encoding="utf-8") as f:
    f.write("import os, sys\n"
            "open(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "sys.exit(int(os.environ.get('RC_FALSO', '0')))\n" % MARCA)

an.VENV_PY = sys.executable  # el «venv» del test es este mismo python, que sí existe

# --- 1. Con pypdf en este proceso: se construye aquí, sin lanzar nada fuera -------------------
kb1 = KbFalso(pypdf=True, rc=0)
rc1 = an._build(kb1)
ok("con pypdf en este intérprete construye EN PROCESO", kb1.builds == 1 and rc1 == 0,
   f"-> builds={kb1.builds} rc={rc1}")
ok("...y no llama al venv", not os.path.exists(MARCA))

# --- 2. Sin pypdf y con venv: delega, no construye aquí ---------------------------------------
kb2 = KbFalso(pypdf=False, rc=1)
rc2 = an._build(kb2)
ok("sin pypdf delega en el venv en vez de construir en proceso", kb2.builds == 0,
   f"-> builds={kb2.builds}")
ok("llama al venv con `kb.py index`",
   os.path.exists(MARCA) and open(MARCA).read().strip() == "index",
   f"-> {open(MARCA).read()!r}" if os.path.exists(MARCA) else "-> no se llamó")
ok("devuelve el código de salida del subproceso, no el de kb.build()", rc2 == 0, f"-> {rc2}")

# --- 3. El subproceso falla: el fallo se propaga, no se traga -----------------------------------
os.environ["RC_FALSO"] = "3"
kb3 = KbFalso(pypdf=False, rc=0)
rc3 = an._build(kb3)
ok("si el venv falla, `_build` devuelve su rc (la nota NO se declarará reindexada)", rc3 == 3,
   f"-> {rc3}")
os.environ.pop("RC_FALSO")

# --- 4. Sin venv donde delegar: construye aquí y que kb se niegue y lo explique ----------------
an.VENV_PY = os.path.join(tmp, "no-existe", "python3")
kb4 = KbFalso(pypdf=False, rc=1)
rc4 = an._build(kb4)
ok("sin venv al que delegar construye en proceso (kb se negará y lo dirá)",
   kb4.builds == 1 and rc4 == 1, f"-> builds={kb4.builds} rc={rc4}")

# --- 5. Un doble viejo sin `hay_pypdf` sigue funcionando como antes ---------------------------
class KbViejo:
    def __init__(self):
        self.builds = 0

    def build(self):
        self.builds += 1
        return 0


kb5 = KbViejo()
rc5 = an._build(kb5)
ok("un kb que no sabe decir si lee PDFs se construye en proceso (no rompe lo ya probado)",
   kb5.builds == 1 and rc5 == 0, f"-> builds={kb5.builds} rc={rc5}")


# --- 6. La CLASE entera: ningún tool lanza `kb.py index` con el `python3` del PATH -------------
# No basta con arreglar archivar_nota.py: el mismo fallo estaba en cerrar_sesion.py, que copiaba
# los docs nuevos a casa base y reindexaba con un "python3" literal. Este caso es el freno de
# clase: cualquier tool que vuelva a lanzar el reindexado tiene que hacerlo con un intérprete que
# lea PDFs (el venv), no con el que le tocara.
import glob
import re

TOOLS = os.path.join(AQUI, "..", "tools")
PELADO = re.compile(r'\[\s*["\']python3?["\']\s*,[^\]]*kb\.py', re.S)
culpables = []
for f in sorted(glob.glob(os.path.join(TOOLS, "*.py"))):
    txt = open(f, encoding="utf-8").read()
    if PELADO.search(txt):
        culpables.append(os.path.basename(f))
ok("ningún tool lanza `kb.py index` con un «python3» literal (usa el venv)",
   not culpables, f"-> {culpables}")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_archivar_nota_venv: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
