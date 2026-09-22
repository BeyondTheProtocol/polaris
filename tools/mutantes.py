#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/mutantes.py — comprueba que un test DE VERDAD protege lo que dice proteger.

POR QUÉ EXISTE (20-sep-2026). Escribiendo los frenos de la ventanilla clínica escribí dos
checks que pasaban EN VACÍO, el mismo día:

  · uno buscaba el patrón «secreto» y comprobaba que un symlink no salía en el listado. El
    symlink se llamaba `atajo.txt`, así que el patrón no casaba nunca: el check pasaba por no
    encontrar nada, y quitar la defensa lo dejaba igual de verde.
  · otro hacía `"RETIRADO" in log`. El log escribe rutas, y la ruta de destino es
    `…/_RETIRADOS/…`, así que la subcadena casaba SIEMPRE, incluso borrando la línea del log.

Ninguna de las dos se veía leyendo el test. Las dos aparecieron al romper a propósito lo que
decían proteger. Eso es lo que hace esto, y por eso deja de ser una costumbre que depende de
que a alguien se le ocurra: **una defensa sin mutante que la mate es una defensa sin cobertura,
y un test que sigue verde cuando le quitas el candado es peor que no tener test** — da la
cobertura por hecha y nadie vuelve a mirar.

CÓMO FUNCIONA. Se le da una «campaña»: el fichero objetivo, el test que lo prueba, la variable
de entorno con la que el test apunta al código bajo prueba, y la lista de mutaciones (trozo de
código que se quita o se cambia). Por cada una: escribe el objetivo mutado, corre el test y
EXIGE que se ponga ROJO. Si alguno sobrevive, sale con rc=1 y lo dice por su nombre.

  python3 tools/mutantes.py tests/mutantes/<campaña>.json [--solo <etiqueta>] [-v]

Formato de la campaña (JSON):
  {"objetivo": "tools/x.py",
   "test": ["python3", "tests/test_x.py"],
   "env": "BTP_X",                      # el test carga lo que diga esta variable
   "mutaciones": [{"etiqueta": "sin el candado", "viejo": "if guard:", "nuevo": "if False:"},
                  {"etiqueta": "sin el log", "viejo": "    log(...)\\n", "nuevo": ""}]}

TRES COSAS QUE SE HACEN A PROPÓSITO, y cada una nace de un fallo real:

  1. **El mutante se escribe AL LADO del original, no en /tmp.** Un módulo suele resolver sus
     vecinos desde su propia ruta (`lector_clinico` carga `zonas_clinicas` así). Copiado a
     /tmp no los encuentra, falla por el motivo equivocado y la campaña entera miente: todos
     los mutantes «mueren», pero por no arrancar.
  2. **Primero se corre el test SIN mutar, y tiene que estar VERDE.** Con un test ya rojo
     todos los mutantes «mueren» igual y la campaña no mide nada.
  3. **`viejo` tiene que aparecer EXACTAMENTE una vez.** Si aparece cero veces la mutación es
     un no-op silencioso (y el mutante «sobrevive» sin haber existido); si aparece varias, no
     se sabe cuál se tocó.

Determinista, stdlib, local. No habla con nadie.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile

# La raíz contra la que se resuelven las rutas de la campaña. Se puede fijar por entorno
# para que el test de esta herramienta monte un repo de juguete en un tmp: una herramienta
# que solo sabe correr sobre el repo de verdad no se puede probar sin tocarlo.
RAIZ = os.environ.get("BTP_MUTANTES_RAIZ") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))


class Campana(object):
    def __init__(self, spec, base=RAIZ):
        self.base = base
        self.objetivo = os.path.join(base, spec["objetivo"])
        self.test = list(spec["test"])
        self.env = spec["env"]
        self.mutaciones = list(spec["mutaciones"])
        if not os.path.isfile(self.objetivo):
            raise ValueError("no existe el objetivo %s" % self.objetivo)
        if not self.mutaciones:
            raise ValueError("la campaña no tiene ni una mutación")

    def _corre(self, ruta_codigo):
        env = dict(os.environ)
        env[self.env] = ruta_codigo
        p = subprocess.run(self.test, cwd=self.base, capture_output=True, text=True,
                           env=env, timeout=900)
        ultima = ""
        for linea in (p.stdout or "").strip().split("\n"):
            if linea.strip():
                ultima = linea.strip()
        return p.returncode, ultima

    def corre(self, solo=None, verboso=False):
        """(supervivientes, problemas, corridas). rc=0 solo si las dos listas están vacías."""
        fuente = io.open(self.objetivo, encoding="utf-8").read()

        rc, ultima = self._corre(self.objetivo)
        if rc != 0:
            return None, ["el test ya está ROJO sin mutar (%s): con un test roto la campaña "
                          "no mide nada" % ultima], 0

        supervivientes, problemas, corridas = [], [], 0
        for m in self.mutaciones:
            etiqueta = m.get("etiqueta") or m.get("viejo", "")[:40]
            if solo and solo not in etiqueta:
                continue
            viejo, nuevo = m["viejo"], m.get("nuevo", "")
            veces = fuente.count(viejo)
            if veces != 1:
                problemas.append("«%s»: el trozo aparece %d veces en %s (tiene que aparecer "
                                 "exactamente 1)" % (etiqueta, veces, m and spec_nombre(self)))
                continue
            mutante = self._escribe(fuente.replace(viejo, nuevo, 1))
            try:
                rc, ultima = self._corre(mutante)
            finally:
                try:
                    os.unlink(mutante)
                except OSError:
                    pass
            corridas += 1
            vivo = (rc == 0)
            if vivo:
                supervivientes.append(etiqueta)
            if verboso or vivo:
                print("  %s %-46s %s" % ("🔴 SOBREVIVE" if vivo else "✅ cazado",
                                         etiqueta, ultima))
        return supervivientes, problemas, corridas

    def _escribe(self, codigo):
        # Al lado del original (ver punto 1 del docstring), con un nombre que nadie importe.
        d = os.path.dirname(self.objetivo)
        fd, ruta = tempfile.mkstemp(prefix="_mutante_", suffix=".py", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(codigo)
        return ruta


def spec_nombre(c):
    return os.path.relpath(c.objetivo, c.base)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Corre una campaña de mutantes contra un test.")
    ap.add_argument("campana", help="JSON de la campaña (p. ej. tests/mutantes/x.json)")
    ap.add_argument("--solo", help="solo las mutaciones cuya etiqueta contenga esto")
    ap.add_argument("-v", "--verboso", action="store_true", help="también las que sí se cazan")
    a = ap.parse_args(argv)

    ruta = a.campana if os.path.isabs(a.campana) else os.path.join(RAIZ, a.campana)
    with io.open(ruta, encoding="utf-8") as f:
        spec = json.load(f)
    c = Campana(spec)

    print("🧬 campaña sobre %s (%d mutaciones)" % (spec_nombre(c), len(c.mutaciones)))
    supervivientes, problemas, corridas = c.corre(solo=a.solo, verboso=a.verboso)

    if supervivientes is None:
        print("❌ %s" % problemas[0])
        return 1
    for p in problemas:
        print("  ⚠️  %s" % p)
    if supervivientes:
        print("\n❌ %d mutante(s) SOBREVIVEN: %s" % (len(supervivientes),
                                                    ", ".join(supervivientes)))
        print("   Esa defensa no está cubierta: o le falta un caso al test, o la capa es "
              "redundante — y entonces se dice en el código, no se finge.")
        return 1
    if problemas:
        print("\n❌ %d mutación(es) mal escritas; la campaña no vale." % len(problemas))
        return 1
    print("\n✅ %d/%d mutantes cazados: cada defensa pone el test en rojo al quitarla."
          % (corridas, corridas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
