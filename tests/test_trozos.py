#!/usr/bin/env python3
"""test_trozos.py — repartir una batería gorda en trozos no pierde ni repite un solo test.

POR QUÉ EXISTE (13-oct-26). Las tres baterías gordas de laminillas (46 % del CPU de la suite) corren
ahora por trozos en paralelo (`tests/_trozos.py`). Son baterías que protegen la puerta de N1 y las
jaulas: ganar tiempo no puede costar cobertura. Aquí se fija que el reparto es EXACTO y que quien
decide qué corre (`test_all.sh`, `tools/tests_afectados.py`) habla de trozos y no del original.

Se fija:
  1. Cada original existe, tiene un único trozo con RESTO, y cada trozo es un fichero de paso (sin tests).
  2. Entre todos los trozos de un original sale cada (clase, test) EXACTAMENTE una vez.
  3. Lo que `_trozos` ve con `ast` es lo que `unittest` carga de verdad (en las clases partidas por tests).
  4. `test_all.sh` lanza cada trozo una vez y ya no lanza el original; LARGAS tampoco lo nombra.
  5. El mecanismo real, con una batería de juguete: cada test corre una vez, un fallo pone rojo su trozo
     y solo ese, y un SKIP (rc 77) del original llega tal cual.
  6. Una tabla mal hecha REVIENTA en vez de correr de menos.
  7. El selector cambia el original por sus trozos, y nada de lo que puede seleccionar se queda sin
     línea en el runner.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
# BTP_TROZOS: la campaña de mutantes (tests/mutantes/trozos.json) apunta aquí a una copia MUTADA.
RUTA = os.environ.get("BTP_TROZOS") or os.path.join(AQUI, "_trozos.py")
_spec = importlib.util.spec_from_file_location("_trozos_bajo_prueba", RUTA)
Z = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Z)
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def pares(original, tabla=None, carpeta=AQUI):
    """{trozo: [(clase, test)]}: lo que cada trozo corre, desplegado test a test."""
    clases = Z.clases_de(os.path.join(carpeta, original))
    out = {}
    for trozo in Z.trozos_de(original, tabla):
        mios = []
        for n in Z.nombres(trozo, tabla, carpeta):
            c, _, t = n.partition(".")
            mios += [(c, t)] if t else ([(c, x) for x in clases[c]] or [(c, "")])
        out[trozo] = mios
    return out


runner = open(os.path.join(AQUI, "test_all.sh"), encoding="utf-8").read()
# una línea puede lanzar varias («runpy a.py; runpy b.py»)
lanzadas = re.findall(r"(?:^|;[ \t]*)(?:runpy|run)[ \t]+(test_[A-Za-z0-9_]+\.(?:py|sh))", runner, re.M)
largas = re.search(r'^LARGAS="(.*?)"', runner, re.M | re.S).group(1).split()

print("1) la tabla y los ficheros de paso")
for original, partes in Z.TROZOS.items():
    check(os.path.isfile(os.path.join(AQUI, original)), "%s existe" % original)
    check(sum(sel.count(Z.RESTO) for _t, sel in partes) == 1, "%s tiene exactamente un trozo con RESTO" % original)
    for trozo, _sel in partes:
        ruta = os.path.join(AQUI, trozo)
        texto = open(ruta, encoding="utf-8").read() if os.path.isfile(ruta) else ""
        check("_trozos.corre(__file__)" in texto and "def test" not in texto and "class " not in texto,
              "%s es un fichero de paso, sin tests propios" % trozo)
sueltos = [f for f in os.listdir(AQUI) if re.fullmatch(r"test_.*_t\d+\.py", f) and Z.original_de(f) is None
           and "_trozos.corre" in open(os.path.join(AQUI, f), encoding="utf-8").read()]
check(not sueltos, "ningún fichero de paso fuera de la tabla (%s)" % sueltos)

print("2) cada test, exactamente una vez")
for original in Z.TROZOS:
    clases = Z.clases_de(os.path.join(AQUI, original))
    todos = sorted((c, t) for c, ts in clases.items() for t in (ts or [""]))
    reparto = pares(original)
    juntos = sorted(p for mios in reparto.values() for p in mios)
    n_tests = sum(1 for _c, t in todos if t)
    check(n_tests > 50, "%s: se ven %d tests en %d clases (no una tabla vacía)" % (original, n_tests, len(clases)))
    check(len(juntos) == len(set(juntos)), "%s: ningún test repetido entre trozos" % original)
    check(juntos == todos, "%s: los trozos suman los %d tests, ni uno menos (faltan %s, sobran %s)"
          % (original, n_tests, sorted(set(todos) - set(juntos))[:3], sorted(set(juntos) - set(todos))[:3]))
    check(all(mios for mios in reparto.values()), "%s: ningún trozo vacío" % original)

print("3) lo que ve `ast` es lo que carga `unittest`")
_CARGA = ("import importlib.util, json, sys, unittest\nsys.argv = [sys.argv[1]]\n"
          "s = importlib.util.spec_from_file_location('orig', sys.argv[0]); m = importlib.util.module_from_spec(s)\n"
          "sys.modules['orig'] = m; s.loader.exec_module(m)\n"
          "print('CARGA ' + json.dumps({k: sorted(unittest.defaultTestLoader.getTestCaseNames(v)) for k, v in vars(m).items()"
          " if isinstance(v, type) and issubclass(v, unittest.TestCase) and v.__module__ == 'orig'}))\n")
for original, partes in Z.TROZOS.items():
    if not any("%" in s for _t, sel in partes for s in sel):
        continue
    r = subprocess.run(["/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable, "-c", _CARGA,
                        os.path.join(AQUI, original)], capture_output=True, text=True, timeout=300, cwd=RAIZ,
                       stdin=subprocess.DEVNULL)
    m = re.search(r"^CARGA (.*)$", r.stdout, re.M)
    if not m:
        check(False, "%s se pudo cargar con unittest (rc=%d: %s)" % (original, r.returncode, r.stderr.strip()[-200:]))
        continue
    import json
    reales = json.loads(m.group(1))
    check(reales == Z.clases_de(os.path.join(AQUI, original)),
          "%s: mismas %d clases y mismos tests con `ast` que con `unittest`" % (original, len(reales)))

print("4) el runner lanza los trozos, no el original")
for original, partes in Z.TROZOS.items():
    check(lanzadas.count(original) == 0, "%s ya no tiene línea run propia" % original)
    check(original in runner, "pero sigue nombrado en el runner (comentario): no es un test huérfano")
    check(all(lanzadas.count(t) == 1 for t, _s in partes), "cada trozo de %s se lanza exactamente una vez" % original)
    check(original not in largas, "%s no está en LARGAS (estarían sus trozos)" % original)

print("5) el mecanismo real, con una batería de juguete")
JUGUETE = '''import os, sys, unittest
if os.environ.get("JUGUETE_SKIP"):
    print("SKIP: a propósito"); sys.exit(77)
def apunta(self):
    open(os.environ["JUGUETE_MARCAS"], "a").write(self.id().split(".", 1)[1] + "\\n")
class Madre(unittest.TestCase):
    def test_heredado(self): apunta(self)
class A(Madre):
    def test_a1(self): apunta(self)
    def test_a2(self): apunta(self)
    def test_a3(self): apunta(self)
    def test_a4(self): apunta(self)
class B(unittest.TestCase):
    def test_b1(self): apunta(self)
    def test_rompe(self):
        apunta(self); self.assertFalse(os.environ.get("JUGUETE_ROMPE"))
class C(unittest.TestCase):
    def test_c1(self): apunta(self)
class SinTests(unittest.TestCase):
    pass
if __name__ == "__main__":
    unittest.main()
'''
TABLA = {"test_juguete.py": [("test_juguete_t1.py", ["A%1/2"]), ("test_juguete_t2.py", ["A%2/2"]),
                            ("test_juguete_t3.py", ["B"]), ("test_juguete_t4.py", [Z.RESTO])]}
tmp = tempfile.mkdtemp(prefix="trozos_")
shutil.copy(RUTA, os.path.join(tmp, "_trozos.py"))
open(os.path.join(tmp, "test_juguete.py"), "w").write(JUGUETE)
for t, _s in TABLA["test_juguete.py"]:
    open(os.path.join(tmp, t), "w").write("import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                                          "import _trozos\n_trozos.corre(__file__, %r)\n" % (TABLA,))


def lanza(trozo, **extra):
    marcas = os.path.join(tmp, "marcas_%s_%s" % (trozo, "_".join(sorted(extra)) or "normal"))
    env = dict(os.environ, JUGUETE_MARCAS=marcas, **extra)
    r = subprocess.run([sys.executable, os.path.join(tmp, trozo)], capture_output=True, text=True, env=env,
                       timeout=60, stdin=subprocess.DEVNULL)
    return r.returncode, (open(marcas).read().split() if os.path.exists(marcas) else [])


corridos, rcs = [], []
for t, _s in TABLA["test_juguete.py"]:
    rc, m = lanza(t)
    rcs.append(rc)
    corridos += m
esperados = sorted(["A.test_a1", "A.test_a2", "A.test_a3", "A.test_a4", "A.test_heredado", "B.test_b1", "B.test_rompe",
                    "C.test_c1", "Madre.test_heredado"])
check(rcs == [0, 0, 0, 0], "los 4 trozos del juguete acaban en verde (%s)" % rcs)
check(sorted(corridos) == esperados, "entre los 4 corre cada test UNA vez, heredados incluidos (%d de %d)" % (len(corridos), len(esperados)))
rojos = [lanza(t, JUGUETE_ROMPE="1")[0] for t, _s in TABLA["test_juguete.py"]]
check([rc != 0 for rc in rojos] == [False, False, True, False], "un test roto pone ROJO su trozo y solo ese (%s)" % rojos)
check(lanza("test_juguete_t1.py", JUGUETE_SKIP="1")[0] == 77, "un SKIP (rc 77) del original llega tal cual")
check(sorted(p for mios in pares("test_juguete.py", TABLA, tmp).values() for p in mios)
      == sorted([tuple(e.split(".")) for e in esperados] + [("SinTests", "")]),
      "y el despliegue con `ast` del punto 2 dice lo mismo que lo que corrió")

print("6) una tabla mal hecha revienta")
for desc, tabla, trozo, motivo in (
        ("sin RESTO", {"test_juguete.py": [("t1.py", ["A"]), ("t2.py", ["B"])]}, "t1.py", "exactamente un trozo con RESTO"),
        ("con dos RESTO", {"test_juguete.py": [("t1.py", [Z.RESTO]), ("t2.py", [Z.RESTO])]}, "t1.py", "exactamente un trozo con RESTO"),
        # junto a una clase buena: si la mala se ignorase, el trozo correría «algo» y nadie lo vería
        ("una clase que no existe", {"test_juguete.py": [("t1.py", ["A", "NoExiste"]), ("t2.py", [Z.RESTO])]}, "t1.py", "no existe"),
        ("una parte imposible (3/2)", {"test_juguete.py": [("t1.py", ["B", "A%3/2"]), ("t2.py", [Z.RESTO])]}, "t1.py", "parte imposible"),
        ("una parte sin tests (C%2/2)", {"test_juguete.py": [("t1.py", ["B", "C%2/2"]), ("t2.py", [Z.RESTO])]}, "t1.py", "sin tests"),
        ("un trozo que no está en la tabla", {"test_juguete.py": [("t1.py", [Z.RESTO])]}, "otro.py", "no está en la tabla")):
    try:
        Z.nombres(trozo, tabla, tmp)
        check(False, "%s → revienta" % desc)
    except SystemExit as e:
        check(motivo in str(e), "%s → revienta y dice por qué (%s)" % (desc, e))
try:
    Z.nombres("t2.py", {"test_juguete.py": [("t1.py", ["A", "B", "C", "Madre", "SinTests"]), ("t2.py", [Z.RESTO])]}, tmp)
    check(False, "un RESTO al que no le queda nada → revienta")
except SystemExit as e:
    check("nada que correr" in str(e), "un RESTO al que no le queda nada → revienta (%s)" % e)

print("7) el selector habla de trozos")
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import tests_afectados as T  # noqa: E402
disponibles = T.baterias()
for original, partes in Z.TROZOS.items():
    _todo, sel = T.afectados(["tests/" + original], disponibles)
    check(sel == {t for t, _s in partes}, "tocar %s selecciona sus %d trozos y no el original" % (original, len(partes)))
# Fuera de la cuenta: el propio runner y los tests que el meta-check exime A PROPÓSITO (mandarían mensajes de verdad).
_exentos = set(re.search(r'case "\$_b" in ([^)]*)\) continue', runner).group(1).split("|")) | {"test_all.sh"}
sin_linea = set()
for b in sorted(disponibles - _exentos):
    _todo, sel = T.afectados(["tests/" + b], disponibles)
    sin_linea |= {x for x in sel if x not in lanzadas}
check(not sin_linea, "nada de lo que el selector puede elegir se queda sin línea run (%s)" % sorted(sin_linea))

shutil.rmtree(tmp, ignore_errors=True)
if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
