#!/usr/bin/env python3
"""tests/test_normas_gracia.py — una memoria nueva no rompe la batería de otro, pero no se olvida.

EL FALLO (20-21 sep 2026, cuatro veces en dos días). Cada memoria `feedback` nueva ponía
test_normas_registro en ROJO al instante, hasta que alguien la clasificaba a mano en
tools/normas.json. Las cuatro veces eran memorias de OTRAS sesiones: el coste caía en quien corría
la batería después, no en quien la escribió.

El arreglo evidente —que `indice_memoria.py` la escriba sola en normas.json— se descartó: ese
fichero está versionado, y hacerlo dejaría casa base sin commitear cada vez que alguien guarda una
memoria. En su lugar: el aviso le llega a quien la escribe, y la recién escrita tiene un margen
(72 h) en el que se LISTA pero no rompe. Pasado el margen, rojo como siempre: no se relaja la
obligación, se pone donde toca.
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


mem = tempfile.mkdtemp(prefix="test-normas-gracia-")
os.environ["BTP_MEM_DIR"] = mem
sys.path.insert(0, os.path.join(ROOT, "tools"))
import normas  # noqa: E402

REG = {"normas": [{"slug": "feedback-ya-registrada"}]}


def memoria(slug, horas):
    p = os.path.join(mem, slug + ".md")
    open(p, "w").write("---\nname: %s\n---\n" % slug)
    t = time.time() - horas * 3600
    os.utime(p, (t, t))


memoria("feedback-ya-registrada", 500)
memoria("feedback-recien-escrita", 1)
memoria("feedback-olvidada", 100)

pend, venc = normas.sin_clasificar(REG)
ok("la recién escrita (1 h) queda PENDIENTE, no rompe", pend == ["feedback-recien-escrita"],
   "-> pendientes %r" % pend)
ok("la olvidada (100 h, > 72) queda VENCIDA y sí rompe", venc == ["feedback-olvidada"],
   "-> vencidas %r" % venc)
ok("la registrada no aparece en ninguna", "feedback-ya-registrada" not in pend + venc)

# Justo en el borde: 71 h pendiente, 73 h vencida.
memoria("feedback-borde-71", 71)
memoria("feedback-borde-73", 73)
pend, venc = normas.sin_clasificar(REG)
ok("a las 71 h sigue en margen", "feedback-borde-71" in pend)
ok("a las 73 h ya no", "feedback-borde-73" in venc)

# El aviso le llega a QUIEN ESCRIBE: indice_memoria.py lo canta al terminar.
src = open(os.path.join(ROOT, "tools", "indice_memoria.py"), encoding="utf-8").read()
ok("indice_memoria.py avisa de las memorias sin clasificar", "sin_clasificar()" in src)
ok("y NO escribe en normas.json (está versionado)",
   "normas.json\", \"w\"" not in src and "_write_atomic" not in src.split("sin_clasificar")[-1][:600])

# Y el test del registro usa la gracia, no una comparación ciega.
t = open(os.path.join(ROOT, "tests", "test_normas_registro.py"), encoding="utf-8").read()
ok("test_normas_registro falla solo con las vencidas", "normas.sin_clasificar(self.d)" in t)

subprocess.run(["rm", "-rf", mem], capture_output=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_normas_gracia: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
