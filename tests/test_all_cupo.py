#!/usr/bin/env python3
"""test_all_cupo.py — la suite va en paralelo por defecto y las pasadas de la máquina comparten un cupo.

POR QUÉ EXISTE (13-oct-26, nota «Cuello de botella de test_all.sh»). El modo paralelo existía desde el
10-oct pero apagado: 49 de 54 lanzamientos en 7 días fueron en serie (47-76 min frente a 19-23). Y con
varias sesiones a la vez nadie repartía los núcleos: la misma batería tardó 429 s una noche y 2160 s
otra. Ahora `bash tests/test_all.sh` sin más va en paralelo, y cada trabajador coge antes una de las N
fichas (`lockf`) que comparten TODAS las pasadas de la máquina. Suite de juguete con la cabecera y el
final REALES de test_all.sh.

Se fija:
  1. Sin `BTP_JOBS` ni `--jobs` la suite va en paralelo; con `BTP_JOBS=1`, anidada, en CI o con un
     valor basura va en serie como siempre. Paralelo y serie dicen lo mismo.
  2. Dos pasadas a la vez con el mismo cupo de 2 fichas nunca corren más de 2 baterías entre las dos
     (y sí corren 2: el cupo reparte, no serializa).
  3. Una ficha cuyo dueño muere se suelta sola: la pasada acaba y las fichas quedan libres.
  4. Un `lockf` roto no deja la suite sin correr: corre sin ficha, lo dice, y el resultado es el mismo.
  5. Con todas las fichas ocupadas por otro, espera; pasado el tope corre sin ficha (no se cuelga).
  6. Las variables del cupo no bajan a las baterías, que sí se ven anidadas.
  7. Una suite anidada no toca el cupo de la máquina aunque se le pida paralelo.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BTP_SUITE: la campaña de mutantes (tests/mutantes/test_all_cupo.json) apunta aquí a una copia MUTADA.
SUITE = os.environ.get("BTP_SUITE") or os.path.join(RAIZ, "tests", "test_all.sh")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


if subprocess.run(["sh", "-c", "command -v lockf"], capture_output=True).returncode != 0:
    print("SKIP: aquí no hay `lockf` (el cupo solo existe donde lo hay)")
    sys.exit(77)

lineas = open(SUITE, encoding="utf-8").read().splitlines()
ini = next(i for i, l in enumerate(lineas) if re.match(r"^run\s+test_", l))
fin = next(i for i, l in enumerate(lineas) if l.startswith("_vacia_cola   #"))
CABECERA = re.sub(r'LARGAS="[^"]*"', 'LARGAS=""', "\n".join(lineas[:ini]), count=1)
CABECERA = re.sub(r'SOLO_SERIE="[^"]*"', 'SOLO_SERIE=""', CABECERA, count=1)
FINAL = "\n".join(lineas[fin:])

# Cada batería apunta cuándo empieza y cuándo acaba. Con `time.time()`: el `time.monotonic()` del python 3.9
# del sistema cuenta desde que arranca CADA proceso, así que entre baterías no se puede comparar.
DUERME = ("import os, sys, time\nd = os.environ['CUPO_TEST_MARCAS']\na = time.time()\ntime.sleep(%s)\n"
          "open(os.path.join(d, '%%s.%%d' %% (os.path.basename(sys.argv[0]), os.getpid())), 'w')"
          ".write('%%r %%r' %% (a, time.time()))\nprint('ok')\n")
MUERE = "import os, signal\nos.kill(os.getppid(), signal.SIGKILL)\nimport time\ntime.sleep(3)\n"
ENTORNO = ("import os, sys\nmalas = sorted(k for k in os.environ if k.startswith('BTP_CUPO'))\n"
           "print('CUPO=%s ANIDADA=[%s]' % (malas, os.environ.get('BTP_DENTRO_DE_SUITE', '')))\n"
           "sys.exit(1 if malas or os.environ.get('BTP_DENTRO_DE_SUITE') != '1' else 0)\n")


def monta(tests):
    root = tempfile.mkdtemp(prefix="all_cupo_")
    os.makedirs(os.path.join(root, "tests"))
    os.makedirs(os.path.join(root, "marcas"))
    for n, c in tests.items():
        open(os.path.join(root, "tests", n), "w").write(c)
    cuerpo = "\n".join(("runpy " if n.endswith(".py") else "run   ") + n for n in tests)
    open(os.path.join(root, "tests", "test_all.sh"), "w").write(CABECERA + "\n" + cuerpo + "\n" + FINAL + "\n")
    return root


def entorno(root, cupo, extra=None):
    """Como una pasada de verdad: sin la marca de anidada. El cupo SIEMPRE es uno propio del test."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CI", "BTP_ROJO_DIR", "BTP_JOBS", "BTP_PORTABLE", "BTP_DENTRO_DE_SUITE", "BTP_CUPO_N",
                        "BTP_TEST_ALL_ROOT", "BTP_TEST_ALL_COPIA", "BTP_TEST_ALL_DUENA")}
    env.update(BTP_ROJO_DIR=os.path.join(root, "rojos_%d" % time.time_ns()), BTP_CUPO_DIR=cupo,
               CUPO_TEST_MARCAS=os.path.join(root, "marcas"))
    env.update(extra or {})
    return env


def lanza(root, cupo, extra=None, args=()):
    return subprocess.Popen(["bash", os.path.join(root, "tests", "test_all.sh")] + list(args), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=entorno(root, cupo, extra), stdin=subprocess.DEVNULL)


def corre(root, cupo, extra=None, args=()):
    t0 = time.monotonic()
    p = lanza(root, cupo, extra, args)
    out, err = p.communicate(timeout=120)
    return p.returncode, out, err, time.monotonic() - t0


def intervalos(*roots):
    iv = []
    for root in roots:
        d = os.path.join(root, "marcas")
        for f in os.listdir(d):
            a, b = open(os.path.join(d, f)).read().split()
            iv.append((float(a), float(b)))
    return iv


def maximo_a_la_vez(iv):
    puntos = sorted([(a, 1) for a, b in iv] + [(b, -1) for a, b in iv], key=lambda x: (x[0], x[1]))
    n = mx = 0
    for _t, d in puntos:
        n += d
        mx = max(mx, n)
    return mx


def fichas_libres(cupo, n):
    return all(subprocess.run(["lockf", "-k", "-s", "-t", "0", os.path.join(cupo, "ficha.%d" % i), "true"]).returncode == 0
               for i in range(1, n + 1))


def orden(out):
    return re.findall(r"^── (test_\S+) ──$", out, re.M)


SEIS = {"test_d%d.py" % i: DUERME % "0.6" for i in range(1, 7)}

print("1) por defecto va en paralelo; BTP_JOBS=1, anidada, CI o basura → serie")
root = monta(SEIS)
cupo = tempfile.mkdtemp(prefix="cupo_")
rc, out, err, t_par = corre(root, cupo)
check(rc == 0 and "⚙️  paralelo:" in out, "sin BTP_JOBS ni --jobs → paralelo (rc=%d)" % rc)
check("en paralelo:" in err and "cupo de máquina:" in err and cupo in err, "y avisa por stderr del cupo que usa")
rc1, out1, _e, t_ser = corre(root, cupo, {"BTP_JOBS": "1"})
check(rc1 == 0 and "⚙️" not in out1, "BTP_JOBS=1 → la serie de siempre")
check(orden(out) == orden(out1) == sorted(SEIS), "paralelo y serie nombran las mismas baterías en el mismo orden")
check(t_par < t_ser - 1.0, "y el paralelo tarda menos (%.1f s frente a %.1f s)" % (t_par, t_ser))
for desc, extra, args in (("anidada (BTP_DENTRO_DE_SUITE)", {"BTP_DENTRO_DE_SUITE": "1"}, ()), ("CI", {"CI": "true"}, ()),
                          ("BTP_PORTABLE", {"BTP_PORTABLE": "1"}, ()), ("BTP_JOBS=abc", {"BTP_JOBS": "abc"}, ()),
                          ("--jobs 0", {}, ("--jobs", "0")), ("--jobs 1", {}, ("--jobs", "1"))):
    rcx, outx, _e, _t = corre(root, cupo, extra, args)
    check(rcx == 0 and "⚙️" not in outx and orden(outx) == sorted(SEIS), "%s → serie" % desc)
rc2, out2, _e, _t = corre(root, cupo, {}, ("--jobs", "3"))
check("⚙️  paralelo: 3 trabajos" in out2, "--jobs 3 manda sobre el valor por defecto")
rc3, out3, _e, _t = corre(root, cupo, {"BTP_CUPO_N": "2"})
check("⚙️  paralelo: 2 trabajos" in out3, "el valor por defecto es el nº de fichas del cupo (BTP_CUPO_N=2 → 2 trabajos)")

print("2) dos pasadas a la vez comparten las fichas")
ra, rb = monta(SEIS), monta(SEIS)
cupo = tempfile.mkdtemp(prefix="cupo_")
pa = lanza(ra, cupo, {"BTP_JOBS": "4", "BTP_CUPO_N": "2"})
pb = lanza(rb, cupo, {"BTP_JOBS": "4", "BTP_CUPO_N": "2"})
oa, ob = pa.communicate(timeout=120), pb.communicate(timeout=120)
iv = intervalos(ra, rb)
check(pa.returncode == 0 and pb.returncode == 0 and len(iv) == 12, "las 12 baterías corrieron en verde (%d)" % len(iv))
check(maximo_a_la_vez(iv) <= 2, "nunca más de 2 a la vez entre las dos pasadas (máximo visto: %d)" % maximo_a_la_vez(iv))
check(maximo_a_la_vez(iv) == 2, "y sí 2 a la vez: el cupo reparte, no serializa")
check(sorted(os.listdir(cupo)) == ["ficha.1", "ficha.2"], "con 2 fichas solo se usan 2 (%s)" % sorted(os.listdir(cupo)))
rs = monta(SEIS)
subprocess.run(["bash", os.path.join(rs, "tests", "test_all.sh")], capture_output=True, text=True, timeout=120,
               env=entorno(rs, tempfile.mkdtemp(prefix="cupo_"), {"BTP_JOBS": "4"}), stdin=subprocess.DEVNULL)
check(maximo_a_la_vez(intervalos(rs)) >= 3, "sin apretar el cupo, 4 trabajos sí corren 3 o más a la vez (control: %d)" % maximo_a_la_vez(intervalos(rs)))

print("3) una ficha cuyo dueño muere se suelta sola")
root = monta(dict(SEIS, **{"test_g_muere.py": MUERE}))
cupo = tempfile.mkdtemp(prefix="cupo_")
rc, out, err, _t = corre(root, cupo, {"BTP_JOBS": "3", "BTP_CUPO_N": "3"})
check(rc == 1 and "sin resultado" in out and "test_g_muere.py" in out, "el trabajo muerto cuenta 1 ROJO por su nombre (rc=%d)" % rc)
check(len(intervalos(root)) == 6, "las otras 6 corrieron UNA vez cada una (%d marcas)" % len(intervalos(root)))
check(fichas_libres(cupo, 3), "y las 3 fichas quedan libres")

print("4) un lockf roto no deja la suite sin correr")
root = monta(SEIS)
falso = tempfile.mkdtemp(prefix="lockf_falso_")
open(os.path.join(falso, "lockf"), "w").write("#!/bin/sh\necho x >> \"$LOCKF_FALSO_LOG\"\nexit 1\n")
os.chmod(os.path.join(falso, "lockf"), 0o755)
log = os.path.join(falso, "llamadas")
rc, out, err, _t = corre(root, tempfile.mkdtemp(prefix="cupo_"),
                         {"BTP_JOBS": "3", "PATH": falso + os.pathsep + os.environ["PATH"], "LOCKF_FALSO_LOG": log})
check(rc == 0 and orden(out) == sorted(SEIS) and len(intervalos(root)) == 6, "las 6 corren, una vez cada una (rc=%d)" % rc)
check(err.count("SIN ficha") == 6, "y cada una dice que corrió sin ficha (%d avisos)" % err.count("SIN ficha"))

print("5) todas las fichas ocupadas: espera y, pasado el tope, corre sin ficha")
root = monta({"test_d1.py": DUERME % "0.1", "test_d2.py": DUERME % "0.1"})
cupo = tempfile.mkdtemp(prefix="cupo_")
ocupa = [subprocess.Popen(["lockf", "-k", "-s", os.path.join(cupo, "ficha.%d" % i), "sleep", "30"]) for i in (1, 2)]
time.sleep(0.5)
try:
    rc, out, err, t = corre(root, cupo, {"BTP_JOBS": "2", "BTP_CUPO_N": "2", "BTP_CUPO_ESPERA_S": "3"})
finally:
    for p in ocupa:
        p.kill()
check(rc == 0 and len(intervalos(root)) == 2, "acaba en verde con las 2 baterías (rc=%d)" % rc)
check(t >= 2.5, "pero antes ESPERÓ a las fichas (%.1f s; el tope era 3 s)" % t)
check(err.count("SIN ficha") == 2, "y dice que corrieron sin ficha por espera agotada")

print("6) las variables del cupo no bajan a las baterías, que se ven anidadas")
root = monta({"test_env.py": ENTORNO, "test_env2.py": ENTORNO})
for desc, extra in (("paralelo", {"BTP_JOBS": "2", "BTP_CUPO_N": "2"}), ("serie", {"BTP_JOBS": "1", "BTP_CUPO_N": "2"})):
    rc, out, err, _t = corre(root, tempfile.mkdtemp(prefix="cupo_"), extra)
    check(rc == 0 and out.count("CUPO=[] ANIDADA=[1]") == 2, "%s: la batería ve CUPO=[] y ANIDADA=[1] (rc=%d)" % (desc, rc))

print("7) una suite anidada no toca el cupo de la máquina")
root = monta(SEIS)
env = entorno(root, "", {"BTP_JOBS": "3", "BTP_DENTRO_DE_SUITE": "1", "LOCKF_FALSO_LOG": log + "7",
                         "PATH": falso + os.pathsep + os.environ["PATH"]})
del env["BTP_CUPO_DIR"]
r = subprocess.run(["bash", os.path.join(root, "tests", "test_all.sh")], capture_output=True, text=True, env=env,
                   timeout=120, stdin=subprocess.DEVNULL)
check(r.returncode == 0 and "⚙️  paralelo: 3 trabajos" in r.stdout, "anidada con BTP_JOBS=3 → paralelo, en verde")
check(not os.path.exists(log + "7") and "SIN ficha" not in r.stderr, "y no llamó a lockf ni una vez")
check("en paralelo:" not in r.stderr and "· [" not in r.stderr, "ni ensucia stderr con el progreso")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
