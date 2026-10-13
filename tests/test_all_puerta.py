#!/usr/bin/env python3
"""test_all_puerta.py — `test_all.sh --puerta` FALLA CERRADA.

POR QUÉ EXISTE (10-oct-26, revisión de consejero-arquitectura sobre la puerta de fusión). La puerta
decidía «RAPIDA» por defecto: si `tools/tests_afectados.py` reventaba o imprimía vacío, test_all.sh
corría CERO baterías y escribía «PUERTA RAPIDA en verde». Una puerta que se abre cuando falla su
propio selector no es una puerta. Aquí se monta una suite de juguete (cabecera y final REALES de
test_all.sh, selector falso) y se fija que, ante cualquier cosa que no sea una respuesta limpia y no
vacía, la suite corre ENTERA y no dice «PUERTA RAPIDA en verde».

Se fija:
  1. selector que sale con rc≠0 (aunque haya impreso «RAPIDA»)          → COMPLETA, corre todo.
  2. selector que imprime vacío                                            → COMPLETA, corre todo.
  3. selector que imprime una primera línea desconocida                    → COMPLETA, corre todo.
  4. «RAPIDA» sin ninguna batería debajo                                   → COMPLETA, corre todo.
  5. selector que ni existe                                                → COMPLETA, corre todo.
  6. «RAPIDA» válida con 2 baterías                                        → corre SOLO esas 2.
  7. el meta-check de tests huérfanos corre TAMBIÉN en la rápida (un test nuevo sin registrar no
     puede colarse por la puerta).
  8. `--cambiados` tiene la misma salvaguarda (salida vacía o rc≠0 → suite completa).
  9. (13-oct-26) las campañas de mutantes: en la suite completa y en la puerta rápida corren TODAS;
     en `--cambiados` solo las que el selector nombra («mutantes:<campaña>.json»). Y ninguna campaña
     del runner real queda fuera del filtro `_mut_va` ni se repite.
"""
import os
import re
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUITE = os.environ.get("BTP_SUITE") or os.path.join(RAIZ, "tests", "test_all.sh")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


lineas = open(SUITE, encoding="utf-8").read().splitlines()
ini = next(i for i, l in enumerate(lineas) if re.match(r"^run\s+test_", l))
fin = next(i for i, l in enumerate(lineas) if l.startswith("_vacia_cola   #"))
CABECERA = "\n".join(lineas[:ini])
FINAL = "\n".join(lineas[fin:])
TOYS = {"test_a.py": "print('ok a')\n", "test_b.py": "print('ok b')\n", "test_c.py": "print('ok c')\n"}


_m_ini = next(i for i, l in enumerate(lineas) if l.startswith("if _mut_va "))
_m_fin = max(i for i, l in enumerate(lineas) if l.startswith("if _mut_va "))
_m_fin = next(i for i in range(_m_fin, len(lineas)) if lineas[i] == "fi")
MUTANTES = "\n".join(lineas[_m_ini:_m_fin + 1])          # el bloque REAL de campañas del runner
CAMPANAS = re.findall(r"^if _mut_va (\S+\.json); then$", MUTANTES, re.M)


def monta(selector, huerfano=False, mutantes=False):
    """Raíz de juguete. `selector`: cuerpo del tools/tests_afectados.py falso (None = no existe).
    `mutantes`: mete el bloque real de campañas, con un tools/mutantes.py falso que solo dice cuál corrió."""
    root = tempfile.mkdtemp(prefix="all_puerta_")
    os.makedirs(os.path.join(root, "tests"))
    os.makedirs(os.path.join(root, "tools"))
    for n, c in TOYS.items():
        open(os.path.join(root, "tests", n), "w").write(c)
    if huerfano:
        open(os.path.join(root, "tests", "test_huerfano.py"), "w").write("print('nadie me corre')\n")
    if selector is not None:
        open(os.path.join(root, "tools", "tests_afectados.py"), "w").write(selector)
    cab = re.sub(r'LARGAS="[^"]*"', 'LARGAS=""', CABECERA, count=1)
    toy = "\n".join("runpy " + n for n in sorted(TOYS))
    if mutantes:
        open(os.path.join(root, "tools", "mutantes.py"), "w").write(
            "import os, sys\nprint('CAMPAÑA ' + os.path.basename(sys.argv[1]))\n")
        toy += "\n" + MUTANTES
    open(os.path.join(root, "tests", "test_all.sh"), "w").write(cab + "\n" + toy + "\n" + FINAL + "\n")
    return root


def corre(root, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_ROJO_DIR", "BTP_JOBS", "BTP_PORTABLE")}
    env["BTP_ROJO_DIR"] = os.path.join(root, "rojos")
    env["BTP_DENTRO_DE_SUITE"] = "1"      # suite de juguete = anidada: sin BTP_JOBS va en serie (13-oct-26)
    r = subprocess.run(["bash", os.path.join(root, "tests", "test_all.sh")] + list(args),
                       capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
    corridas = re.findall(r"^── (test_\S+) ──", r.stdout, re.M)
    return r, corridas


def debe_ir_completa(desc, selector, *args):
    root = monta(selector)
    r, corridas = corre(root, *(args or ("--puerta",)))
    check(sorted(corridas) == sorted(TOYS), "%s → corre las 3 baterías (corrió %s)" % (desc, corridas))
    check("PUERTA RAPIDA en verde" not in r.stdout, "%s → no dice «PUERTA RAPIDA en verde»" % desc)
    check(("PUERTA: COMPLETA" in r.stdout) if "--cambiados" not in (args or ()) else ("va la suite completa" in r.stdout),
          "%s → dice que va completa" % desc)


print("1-5) la puerta que no concluye va COMPLETA")
debe_ir_completa("rc≠0 tras imprimir RAPIDA", "import sys\nprint('RAPIDA')\nprint('test_a.py')\nsys.exit(3)\n")
debe_ir_completa("salida vacía", "pass\n")
debe_ir_completa("primera línea desconocida", "print('QUIZAS')\nprint('test_a.py')\n")
debe_ir_completa("RAPIDA sin baterías", "print('RAPIDA')\n")
debe_ir_completa("el selector no existe", None)

print("6) una RAPIDA válida corre SOLO lo que dice")
root = monta("print('RAPIDA')\nprint('test_a.py')\nprint('test_c.py')\n")
r, corridas = corre(root, "--puerta")
check(sorted(corridas) == ["test_a.py", "test_c.py"], "corre solo test_a y test_c (corrió %s)" % corridas)
check("PUERTA RAPIDA en verde" in r.stdout and "TODO EN VERDE" not in r.stdout, "dice «PUERTA RAPIDA en verde», nunca «TODO EN VERDE»")

print("7) el meta-check de huérfanos corre también en la rápida")
root = monta("print('RAPIDA')\nprint('test_a.py')\n", huerfano=True)
r, corridas = corre(root, "--puerta")
check(r.returncode != 0 and "NADIE corre" in r.stdout and "test_huerfano.py" in r.stdout,
      "un test sin registrar pone ROJA la rápida (rc=%d)" % r.returncode)
check("PUERTA RAPIDA en verde" not in r.stdout, "y no dice «en verde»")

print("8) --cambiados: salida vacía o rc≠0 → suite completa")
for desc, sel in (("vacía", "pass\n"), ("rc≠0", "import sys\nprint('test_a.py')\nsys.exit(2)\n")):
    root = monta(sel)
    r, corridas = corre(root, "--cambiados")
    check(sorted(corridas) == sorted(TOYS), "--cambiados con selector %s → corre las 3 (corrió %s)" % (desc, corridas))
    check("va la suite completa" in r.stdout, "--cambiados con selector %s → lo dice" % desc)

print("9) campañas de mutantes: todas en la completa y en la puerta; en --cambiados, solo las nombradas")
check(len(CAMPANAS) >= 10 and len(set(CAMPANAS)) == len(CAMPANAS), "el runner real tiene %d campañas, sin repetir" % len(CAMPANAS))
_todo = "\n".join(lineas)
_invocadas = re.findall(r'tools/mutantes\.py" tests/mutantes/(\S+\.json)', _todo)
check(sorted(_invocadas) == sorted(CAMPANAS) and sorted(re.findall(r'tools/mutantes\.py" tests/mutantes/(\S+\.json)', MUTANTES)) == sorted(CAMPANAS),
      "cada campaña que el runner invoca va dentro de su `if _mut_va <la misma>`")
for _bloque in re.findall(r"^if _mut_va (\S+\.json); then\n(.*?)^fi$", MUTANTES, re.M | re.S):
    if _bloque[1].count("tools/mutantes.py") != 1 or ("tests/mutantes/%s " % _bloque[0]) not in _bloque[1]:
        check(False, "el bloque de %s corre exactamente su campaña" % _bloque[0])
corrio = lambda r: re.findall(r"^CAMPAÑA (\S+)$", r.stdout, re.M)
root = monta("print('TODO')\n", mutantes=True)
r, _ = corre(root)
check(sorted(corrio(r)) == sorted(CAMPANAS), "suite completa → corren las %d (corrieron %d)" % (len(CAMPANAS), len(corrio(r))))
r, _ = corre(root, "--cambiados")
check(sorted(corrio(r)) == sorted(CAMPANAS), "--cambiados que cae a TODO → corren todas (corrieron %d)" % len(corrio(r)))
root = monta("print('RAPIDA')\nprint('test_a.py')\n", mutantes=True)
r, _ = corre(root, "--puerta")
check(sorted(corrio(r)) == sorted(CAMPANAS), "puerta RAPIDA → corren todas (corrieron %d)" % len(corrio(r)))
root = monta("print('mutantes:%s')\nprint('test_a.py')\n" % CAMPANAS[1], mutantes=True)
r, corridas = corre(root, "--cambiados")
check(corrio(r) == [CAMPANAS[1]] and corridas == ["test_a.py"],
      "--cambiados con una campaña nombrada → corre solo esa y su batería (campañas %s, baterías %s)" % (corrio(r), corridas))
check("1 batería(s) afectada(s) y 1 campaña(s) de mutantes" in r.stdout, "y dice cuántas de cada")
check("PARCIAL en verde" in r.stdout and "TODO EN VERDE" not in r.stdout, "el resumen sigue siendo PARCIAL, nunca «TODO EN VERDE»")
root = monta("print('test_a.py')\n", mutantes=True)
r, _ = corre(root, "--cambiados")
check(corrio(r) == [], "--cambiados sin campañas nombradas → no corre ninguna (corrieron %s)" % corrio(r))
root = monta("print('NINGUNA')\n", mutantes=True)
r, corridas = corre(root, "--cambiados")
check(corrio(r) == [] and corridas == [], "--cambiados «NINGUNA» → ni baterías ni campañas")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
