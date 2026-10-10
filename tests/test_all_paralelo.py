#!/usr/bin/env python3
"""test_all_paralelo.py — correr la suite en paralelo no cambia lo que la suite DICE.

POR QUÉ EXISTE (10-oct-26, plan de aceleración de test_all). `test_all.sh` corre en serie y se
le añadió `BTP_JOBS=N` / `--jobs N` para correr N baterías a la vez. Una suite que protege el
muro no puede acelerarse a costa de decir otra cosa. Este test monta una suite de JUGUETE con
la cabecera y el final REALES de test_all.sh (solo las baterías son de mentira) y fija:

  1. Serie y paralelo dicen lo mismo: mismas líneas en el mismo orden, mismo código de salida,
     mismo contenido del log de cada rojo, misma cuenta de saltadas.
  2. El paralelo es paralelo de verdad (6 baterías de 1 s con 6 trabajos no tardan 6 s).
  3. FAIL-CLOSED: un trabajo que muere sin dejar código cuenta ROJO, no verde ni ausente.
  4. Una batería de SOLO_SERIE no se solapa con ninguna otra.
  5. El cronómetro vive: «las 10 más lentas» nombra baterías (llevaba vacío desde una fusión).
  6. `--jobs`/`BTP_JOBS` basura (0, «x») caen a serie, no rompen la suite.
"""
import os
import re
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BTP_SUITE: la campaña de mutantes (tests/mutantes/test_all_paralelo.json) apunta aquí a una copia
# MUTADA de test_all.sh; sin la variable, la suite de verdad.
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

TESTS = {
    "test_a_verde.py": "print('ok A')\n",
    "test_b_rojo.py": "import sys\nprint('fallo B en stdout')\nsys.stderr.write('BOOM-B stderr\\n')\nsys.exit(1)\n",
    "test_c_skip.py": "import sys\nprint('SKIP: falta X')\nsys.exit(77)\n",
    "test_d_verde.sh": "echo 'ok D'\nexit 0\n",
    "test_e_rojo.sh": "echo 'fallo E'\necho 'BOOM-E stderr' >&2\nexit 3\n",
    "test_f1.py": "import time\ntime.sleep(1)\nprint('f1')\n",
    "test_f2.py": "import time\ntime.sleep(1)\nprint('f2')\n",
    "test_f3.py": "import time\ntime.sleep(1)\nprint('f3')\n",
    "test_f4.py": "import time\ntime.sleep(1)\nprint('f4')\n",
    "test_f5.py": "import time\ntime.sleep(1)\nprint('f5')\n",
    "test_f6.py": "import time\ntime.sleep(1)\nprint('f6')\n",
}
ORDEN = ["test_a_verde.py", "test_f1.py", "test_b_rojo.py", "test_f2.py", "test_c_skip.py", "test_d_verde.sh",
         "test_f3.py", "test_e_rojo.sh", "test_f4.py", "test_f5.py"]
# Para el caso «el trabajo muere»: mata a su padre (el trabajador). En serie mataría la suite entera.
MUERE = ("import os, signal\nos.kill(os.getppid(), signal.SIGKILL)\nimport time\ntime.sleep(5)\n")
# Para el caso SOLO_SERIE: apuntan cuándo empiezan y acaban (reloj monotónico común al sistema).
INTERVALO = ("import sys, time\nt0 = time.time()\ntime.sleep(%s)\n"
             "open(%r, 'a').write('%s %%.3f %%.3f\\n' %% (t0, time.time()))\n")


def monta(extra=None, solo_serie="", orden=None):
    root = tempfile.mkdtemp(prefix="all_paralelo_")
    os.makedirs(os.path.join(root, "tests"))
    todo = dict(TESTS)
    todo.update(extra or {})
    nombres = orden or ORDEN
    for n in nombres:          # solo las que la suite de juguete lista: el meta-check caza huérfanos
        with open(os.path.join(root, "tests", n), "w") as f:
            f.write(todo[n])
    toy = []
    for n in nombres:
        toy.append(("runpy " if n.endswith(".py") else "run   ") + n)
    cab = re.sub(r'SOLO_SERIE="[^"]*"', lambda m: 'SOLO_SERIE="%s"' % solo_serie, CABECERA, count=1)
    cab = re.sub(r'LARGAS="[^"]*"', 'LARGAS=""', cab, count=1)
    script = cab + "\n" + "\n".join(toy) + "\n" + FINAL + "\n"
    with open(os.path.join(root, "tests", "test_all.sh"), "w") as f:
        f.write(script)
    return root


def corre(root, args=(), env_extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_ROJO_DIR", "BTP_JOBS", "BTP_PORTABLE")}
    env["BTP_ROJO_DIR"] = os.path.join(root, "rojos_%d" % time.time_ns())
    env.update(env_extra or {})
    t0 = time.monotonic()
    r = subprocess.run(["bash", os.path.join(root, "tests", "test_all.sh")] + list(args),
                       capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
    return r, time.monotonic() - t0, env["BTP_ROJO_DIR"]


def normaliza(salida, rojo_dir):
    """Lo que la suite DICE, sin lo que cambia por naturaleza (tiempos, carpeta, línea de paralelo)."""
    out = []
    for l in salida.splitlines():
        if l.startswith("⚙️") or l.startswith("🔴 (en vivo)"):
            continue
        out.append(l.replace(rojo_dir, "<ROJOS>"))
    s = "\n".join(out)
    # el bloque de tiempos (valores) y el total no son comparables; los NOMBRES sí.
    s = re.sub(r"total \d+s", "total Ns", s)
    s = re.sub(r"^\s+\d+ (test_\S+)$", r"   N \1", s, flags=re.M)
    # el orden de las «más lentas» depende de segundos sueltos: se compara el CONJUNTO de nombres
    bloque = sorted(re.findall(r"^   N test_\S+$", s, re.M))
    s = re.sub(r"^   N test_\S+\n", "", s, flags=re.M)
    return s + "\n" + "\n".join(bloque)


def logs(rojo_dir):
    d = {}
    if os.path.isdir(rojo_dir):
        for n in sorted(os.listdir(rojo_dir)):
            d[n] = open(os.path.join(rojo_dir, n), encoding="utf-8", errors="replace").read()
    return d


print("1) serie y paralelo dicen lo mismo")
root = monta()
rs, ts, ds = corre(root)
rp, tp, dp = corre(root, env_extra={"BTP_JOBS": "4"})
check(rs.returncode == 2, "serie: 2 rojos → rc=2 (rc=%d)" % rs.returncode)
check(rp.returncode == rs.returncode, "paralelo: mismo código de salida (rc=%d)" % rp.returncode)
check(normaliza(rs.stdout, ds) == normaliza(rp.stdout, dp), "misma salida, mismo orden")
if normaliza(rs.stdout, ds) != normaliza(rp.stdout, dp):
    import difflib
    print("\n".join(list(difflib.unified_diff(normaliza(rs.stdout, ds).splitlines(),
                                              normaliza(rp.stdout, dp).splitlines(), "serie", "paralelo", lineterm=""))[:30]))
check(logs(ds) == logs(dp) and len(logs(ds)) == 2, "mismos logs de rojo (%s)" % sorted(logs(dp)))
check("BOOM-B stderr" in logs(dp).get("rojo-test_b_rojo.py.log", "") and "BOOM-E stderr" in logs(dp).get("rojo-test_e_rojo.sh.log", ""),
      "el log del rojo lleva el stderr (py: unittest escribe ahí; sh: mezclado)")
check("1 batería(s) saltada(s)" in rp.stdout and "1 batería(s) saltada(s)" in rs.stdout, "misma cuenta de saltadas")
check("2 batería(s) con fallos" in rp.stdout, "el resumen cuenta los rojos del paralelo")

print("2) el paralelo es paralelo de verdad")
solo_f = ["test_f1.py", "test_f2.py", "test_f3.py", "test_f4.py", "test_f5.py", "test_f6.py"]
root2 = monta(orden=solo_f)
rs2, ts2, _ = corre(root2)
rp2, tp2, _ = corre(root2, env_extra={"BTP_JOBS": "6"})
check(rs2.returncode == 0 and rp2.returncode == 0, "ambas en verde")
check(ts2 > 5.5, "serie: 6 s de sueño suman ≥ 5,5 s (%.1f s)" % ts2)
check(tp2 < ts2 - 2.5, "paralelo con 6 trabajos: bastante menos que la serie (%.1f s frente a %.1f s)" % (tp2, ts2))
check("⚙️  paralelo: 6 trabajos" in rp2.stdout, "la salida dice que fue en paralelo")

print("3) fail-closed: un trabajo que muere cuenta ROJO")
root3 = monta(extra={"test_g_muere.py": MUERE}, orden=["test_a_verde.py", "test_g_muere.py", "test_d_verde.sh"])
rp3, _, _ = corre(root3, env_extra={"BTP_JOBS": "3"})
check(rp3.returncode == 1, "rc=1: la batería muerta es 1 rojo (rc=%d)" % rp3.returncode)
check("sin resultado" in rp3.stdout and "test_g_muere.py" in rp3.stdout, "lo dice por su nombre")
check("1 batería(s) con fallos" in rp3.stdout and "TODO EN VERDE" not in rp3.stdout, "no hay «TODO EN VERDE»")

print("4) SOLO_SERIE no se solapa con nada")
marca = os.path.join(tempfile.mkdtemp(prefix="all_paralelo_m_"), "intervalos.txt")
extra4 = {"test_s_solo.py": INTERVALO % ("1.2", marca, "solo"),
          "test_s_solo2.py": INTERVALO % ("1.2", marca, "solo2"),
          "test_p1.py": INTERVALO % ("1.0", marca, "p1"),
          "test_p2.py": INTERVALO % ("1.0", marca, "p2"),
          "test_p3.py": INTERVALO % ("1.0", marca, "p3")}
root4 = monta(extra=extra4, solo_serie="test_s_solo.py test_s_solo2.py",
              orden=["test_p1.py", "test_s_solo.py", "test_p2.py", "test_s_solo2.py", "test_p3.py"])
rp4, _, _ = corre(root4, env_extra={"BTP_JOBS": "4"})
iv = {}
if os.path.exists(marca):
    for l in open(marca):
        n, a, b = l.split()
        iv[n] = (float(a), float(b))
check(rp4.returncode == 0 and len(iv) == 5, "las 5 corrieron en verde (%s)" % sorted(iv))
if len(iv) == 5:
    pool = {n: v for n, v in iv.items() if n.startswith("p")}
    solos = sorted(v for n, v in iv.items() if n.startswith("solo"))
    check(all(b <= solos[0][0] + 0.05 for a, b in pool.values()), "las de SOLO_SERIE empiezan cuando el pool ya acabó")
    check(solos[0][1] <= solos[1][0] + 0.05, "y entre ellas van una a una, sin solaparse")
    check(max(a for a, b in pool.values()) - min(a for a, b in pool.values()) < 0.8, "las del pool sí arrancaron juntas")
check(re.findall(r"^── (test_\S+) ──$", rp4.stdout, re.M) == ["test_p1.py", "test_s_solo.py", "test_p2.py", "test_s_solo2.py", "test_p3.py"],
      "aun así la salida respeta el orden de siempre")

print("5) el cronómetro vive")
check(re.search(r"las 10 baterías más lentas.*\n(\s+\d+ test_\S+\n)+", rs2.stdout) is not None,
      "serie: «las 10 más lentas» nombra baterías")
check(re.search(r"las 10 baterías más lentas.*\n(\s+\d+ test_\S+\n)+", rp2.stdout) is not None,
      "paralelo: «las 10 más lentas» nombra baterías")

print("6) --jobs/BTP_JOBS basura cae a serie")
for args, env in ((["--jobs", "0"], {}), (["--jobs=x"], {}), ([], {"BTP_JOBS": "-3"}), ([], {"BTP_JOBS": "abc"})):
    r, _, _ = corre(root, args=args, env_extra=env)
    check(r.returncode == 2 and "⚙️" not in r.stdout, "%s %s → serie y mismo rc" % (args, env))

print("7) --jobs N equivale a BTP_JOBS=N")
r7, _, _ = corre(root2, args=["--jobs", "3"])
check("⚙️  paralelo: 3 trabajos" in r7.stdout, "--jobs 3 activa el paralelo")

print("8) las gordas (LARGAS) arrancan primero aunque estén declaradas las últimas")
marca8 = os.path.join(tempfile.mkdtemp(prefix="all_paralelo_m8_"), "intervalos.txt")
extra8 = {"test_big.py": INTERVALO % ("1.2", marca8, "big"),
          "test_q1.py": INTERVALO % ("1.0", marca8, "q1"),
          "test_q2.py": INTERVALO % ("1.0", marca8, "q2"),
          "test_q3.py": INTERVALO % ("1.0", marca8, "q3")}
root8 = monta(extra=extra8, orden=["test_q1.py", "test_q2.py", "test_q3.py", "test_big.py"])
ruta8 = os.path.join(root8, "tests", "test_all.sh")
txt8 = open(ruta8, encoding="utf-8").read()
txt8 = re.sub(r'LARGAS="[^"]*"', 'LARGAS="test_big.py"', txt8, count=1)
open(ruta8, "w", encoding="utf-8").write(txt8)
rp8, _, _ = corre(root8, env_extra={"BTP_JOBS": "2"})
iv8 = {}
if os.path.exists(marca8):
    for l in open(marca8):
        n, a, b = l.split()
        iv8[n] = (float(a), float(b))
check(rp8.returncode == 0 and len(iv8) == 4, "las 4 corrieron (%s)" % sorted(iv8))
if len(iv8) == 4:
    ini8 = sorted(a for a, b in iv8.values())
    check(iv8["big"][0] <= ini8[1] + 0.05, "la gorda es de las 2 primeras en arrancar (2 trabajos)")

print("9) las listas de SOLO_SERIE y LARGAS no tienen nombres muertos")
vivas = set(re.findall(r"^(?:run|runpy)\s+(test_\S+)", "\n".join(lineas), re.M))
vivas |= set(re.findall(r";\s*runpy\s+(test_\S+?)(?=;|\s|$)", "\n".join(lineas)))
for var in ("SOLO_SERIE", "LARGAS"):
    m = re.search(var + r'="([^"]*)"', "\n".join(lineas))
    nombres = m.group(1).split() if m else []
    muertos = [n for n in nombres if n not in vivas]
    check(not muertos, "%s: todos los nombres existen en la suite (muertos: %s)" % (var, muertos))

print("10) BTP_JOBS no baja a las baterías")
root10 = monta(extra={"test_env.py": "import os\nprint('JOBS=[%s]' % os.environ.get('BTP_JOBS', ''))\nimport sys\nsys.exit(0 if not os.environ.get('BTP_JOBS') else 1)\n"},
               orden=["test_env.py"])
r10, _, _ = corre(root10, env_extra={"BTP_JOBS": "4"})
check(r10.returncode == 0 and "JOBS=[]" in r10.stdout, "la batería ve BTP_JOBS vacío (rc=%d)" % r10.returncode)

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
