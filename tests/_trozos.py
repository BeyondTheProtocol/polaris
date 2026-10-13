#!/usr/bin/env python3
"""tests/_trozos.py — una batería gorda, repartida en varias baterías que corren A LA VEZ.

POR QUÉ EXISTE (13-oct-26, nota «Cuello de botella de test_all.sh»). `test_laminillas_n1.py`,
`test_laminillas_piloto_b.py` y `test_laminillas.py` son el 46 % del CPU de la suite y cada una era UN
proceso: por muchos trabajos que tenga el paralelo, la pasada no bajaba de lo que tardase la mayor
(835 s). Aquí NO se parte ningún fichero ni se toca un solo test: el original sigue entero y se puede
correr a mano como siempre (`python3 tests/test_laminillas_n1.py`). Lo que cambia es que `test_all.sh`
ya no lo lanza de una vez sino por TROZOS, cada uno un fichero de tres líneas que ejecuta el original
pidiéndole solo sus clases (`python3 original.py ClaseA ClaseB`, lo que `unittest.main` ya entiende).

Cada trozo dice qué le toca con una lista de selectores:
  · "Clase"        esa clase entera.
  · "Clase%i/n"    la parte i de n de los tests de esa clase (por orden alfabético, uno de cada n).
                   Un test nuevo cae solo en una parte: no hay lista de nombres que mantener.
  · RESTO          TODAS las clases que ningún otro trozo del mismo original nombra. Cada original
                   tiene exactamente un trozo con RESTO: una clase nueva nunca se queda sin correr.

Que entre todos los trozos corren cada test UNA vez, ni uno menos ni uno repetido, lo fija
`tests/test_trozos.py` (y que lo que ve este fichero con `ast` es lo que `unittest` carga de verdad).
"""
import ast
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
RESTO = "<resto>"

# original → [(trozo, selectores)]. Reparto con los tiempos de CPU medidos el 13-oct-26 (entre paréntesis).
TROZOS = {
    "test_laminillas_n1.py": [                                                   # 588 s
        ("test_laminillas_n1_t1.py", ["Vision%1/3"]),                            # ~100
        ("test_laminillas_n1_t2.py", ["Vision%2/3"]),                            # ~100
        ("test_laminillas_n1_t3.py", ["Vision%3/3"]),                            # ~100
        ("test_laminillas_n1_t4.py", ["Nube", "PuertaImagen", "TiffN1"]),        # 140
        ("test_laminillas_n1_t5.py", [RESTO]),                                   # 150
    ],
    "test_laminillas_piloto_b.py": [                                             # 451 s
        # Registro no se parte: su setUpClass son 128 s y cada parte lo pagaría entero.
        ("test_laminillas_piloto_b_t1.py", ["Registro"]),                        # 375
        ("test_laminillas_piloto_b_t2.py", [RESTO]),                             # 76
    ],
    "test_laminillas.py": [                                                      # 280 s
        ("test_laminillas_t1.py", ["CristalR4YRetirada"]),                       # 109
        ("test_laminillas_t2.py", ["ProcRegistroPar", "ProcPilotoPixeles"]),     # 122
        ("test_laminillas_t3.py", [RESTO]),                                      # 49
    ],
}


def original_de(trozo, tabla=None):
    for orig, partes in (tabla or TROZOS).items():
        if any(t == trozo for t, _s in partes):
            return orig
    return None


def trozos_de(original, tabla=None):
    return [t for t, _s in (tabla or TROZOS).get(original, [])]


def clases_de(ruta):
    """{clase: [tests]} de las clases TestCase del fichero, EN SU ORDEN, con los tests heredados de
    las clases del propio fichero. Con `ast`: importar el original puede re-ejecutarlo en otro python."""
    with open(ruta, encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    defs = {n.name: n for n in arbol.body if isinstance(n, ast.ClassDef)}

    def padres(c):
        return [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in defs[c].bases]

    def es_caso(c, visto=()):
        return any(p == "TestCase" or (p in defs and p not in visto and es_caso(p, visto + (c,))) for p in padres(c))

    def tests(c, visto=()):
        propios = {m.name for m in defs[c].body
                   if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test")}
        for p in padres(c):
            if p in defs and p not in visto:
                propios |= tests(p, visto + (c,))
        return propios

    return {c: sorted(tests(c)) for c in defs if es_caso(c)}


def nombres(trozo, tabla=None, carpeta=AQUI):
    """Lo que el trozo le pide al original: ['Clase', 'Clase.test_x', …]. Lo que no cuadra REVIENTA
    (un trozo que no sabe qué correr no puede acabar en verde por no correr nada)."""
    tabla = tabla or TROZOS
    orig = original_de(trozo, tabla)
    if orig is None:
        raise SystemExit("_trozos: %s no está en la tabla" % trozo)
    clases = clases_de(os.path.join(carpeta, orig))
    partes = dict(tabla[orig])
    nombradas = {s.split("%")[0] for sel in partes.values() for s in sel if s != RESTO}
    if sum(sel.count(RESTO) for sel in partes.values()) != 1:
        raise SystemExit("_trozos: %s necesita exactamente un trozo con RESTO" % orig)
    out = []
    for s in partes[trozo]:
        if s == RESTO:
            out += [c for c in clases if c not in nombradas]
            continue
        clase, _, parte = s.partition("%")
        if clase not in clases:
            raise SystemExit("_trozos: la clase %s no existe en %s" % (clase, orig))
        if not parte:
            out.append(clase)
            continue
        i, n = (int(x) for x in parte.split("/"))
        if not 1 <= i <= n:
            raise SystemExit("_trozos: parte imposible %s" % s)
        mios = clases[clase][i - 1::n]
        if not mios:
            raise SystemExit("_trozos: %s se queda sin tests" % s)
        out += ["%s.%s" % (clase, t) for t in mios]
    if not out:
        raise SystemExit("_trozos: %s no tiene nada que correr" % trozo)
    return out


def corre(yo, tabla=None):
    """Lo llama cada trozo con su `__file__`: se convierte en el original con sus nombres. Es un
    `exec`: mismo proceso, mismo código de salida (el de un SKIP incluido), misma salida."""
    trozo = os.path.basename(yo)
    carpeta = os.path.dirname(os.path.abspath(yo))
    args = [sys.executable, os.path.join(carpeta, original_de(trozo, tabla) or "")] + nombres(trozo, tabla, carpeta)
    sys.stdout.flush()
    os.execv(sys.executable, args)
