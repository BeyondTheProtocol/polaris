#!/usr/bin/env python3
"""tests/test_autopoda_residuo.py — los worktrees fusionados se podan solos; lo dudoso, no.

EL FALLO (21 y 22-sep-2026). test_all dejaba 12 jobs falsos en el PANEL-LAZO.md (gitignored) del
worktree donde corría. El freno de poda (bien) lo tomaba por trabajo vivo y se negaba: los
worktrees ya fusionados se quedaban colgados hasta que alguien los podaba a mano con --force.

Congela:
  · el residuo de la batería (ráfaga de segundos, jobs que casa base no conoce, forma exacta)
    NO cuenta como trabajo vivo;
  · cualquier cosa que no encaje del todo SÍ cuenta (fail-closed): ráfaga larga, texto extra,
    un job que casa base conoce por su cola o por su panel;
  · `ramas.py autopoda` poda el worktree fusionado con residuo y deja (y avisa una sola vez)
    el que tiene un panel de verdad.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAMAS = os.path.join(ROOT, "tools", "ramas.py")
OK, FALLOS = [], []
REL = os.path.join("00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md")


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd] + list(a), capture_output=True, text=True)


def bloque(ts, job, did="agente orquestador ejecutado", cost="$0.050000"):
    return ("\n## %s  job %s\n- QUÉ HIZO: %s\n- QUÉ DECIDIÓ: -\n- ESPERA OK: —\n"
            "- FALLÓ: —\n- COSTE: %s\n" % (ts, job, did, cost))


RESIDUO = "".join(bloque("2026-09-21T09:06:%02d" % s, "aa%08d" % s) for s in range(30, 42))


def escribe(wt, txt):
    os.makedirs(os.path.join(wt, os.path.dirname(REL)), exist_ok=True)
    open(os.path.join(wt, REL), "w", encoding="utf-8").write(txt)


tmp = tempfile.mkdtemp(prefix="test-autopoda-")
base = os.path.join(tmp, "base")
state = os.path.join(tmp, "state")
os.makedirs(base)
git(base, "init", "-q", "-b", "master")
git(base, "config", "user.email", "t@t"); git(base, "config", "user.name", "t")
open(os.path.join(base, ".gitignore"), "w").write("00_FUENTE-DE-VERDAD/\n.claude/logs/\ntools/state/\n")
git(base, "add", "-A"); git(base, "commit", "-qm", "base")

# Casa base de mentira ANTES de importar ramas (resuelve ROOT al importar).
os.environ.update(BTP_REPO=base, BTP_STATE_DIR=state, BTP_TEST_BATTERY="1")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ramas  # noqa: E402

# ── Unidad: qué es residuo ────────────────────────────────────────────────────────────
u = os.path.join(tmp, "u")
escribe(u, RESIDUO)
ok("la ráfaga de la batería se reconoce como residuo", ramas.residuo_de_tests(u) == [REL],
   "-> %r" % ramas.residuo_de_tests(u))
ok("y no cuenta como trabajo vivo", ramas._trabajo_vivo(u) == [], "-> %r" % ramas._trabajo_vivo(u))

escribe(u, bloque("2026-09-21T09:00:00", "bb1") + bloque("2026-09-21T09:07:00", "bb2"))
ok("entradas separadas por minutos (ritmo del lazo real) SON trabajo vivo",
   REL in ramas._trabajo_vivo(u))

OTRA = "".join(bloque("2026-09-21T11:40:%02d" % s, "dd%08d" % s) for s in range(0, 12))
escribe(u, RESIDUO + OTRA)
ok("varias pasadas de la batería (una ráfaga cada una) siguen siendo residuo",
   ramas._trabajo_vivo(u) == [], "-> %r" % ramas._trabajo_vivo(u))

escribe(u, RESIDUO + bloque("2026-09-21T10:15:00", "ee1") + OTRA)
ok("una entrada suelta entre ráfagas (un job real) lo hace trabajo vivo",
   REL in ramas._trabajo_vivo(u))

LENTA = "".join(bloque("2026-09-21T12:%02d:%02d" % divmod(s * 20, 60), "ff%08d" % s) for s in range(12))
escribe(u, LENTA)
ok("una racha de entradas cada 20 s durante minutos NO es residuo (supera la duración de ráfaga)",
   REL in ramas._trabajo_vivo(u))

escribe(u, RESIDUO + "nota a mano de {{TITULAR}}\n")
ok("cualquier texto fuera de los bloques lo hace trabajo vivo", REL in ramas._trabajo_vivo(u))

escribe(u, "## 2026-09-20  job abc123\n")
ok("un panel con otra forma es trabajo vivo (fail-closed)", REL in ramas._trabajo_vivo(u))

os.makedirs(os.path.join(state, "queue", "done"))
open(os.path.join(state, "queue", "done", "1-20260921T090631000000-aa00000031.json"), "w").write("{}")
escribe(u, RESIDUO)
ok("un job que casa base conoce por su COLA lo hace trabajo vivo", REL in ramas._trabajo_vivo(u))
shutil.rmtree(os.path.join(state, "queue"))

escribe(base, bloque("2026-09-21T09:06:35", "aa00000035"))
ok("un job que casa base conoce por su PANEL lo hace trabajo vivo", REL in ramas._trabajo_vivo(u))
os.remove(os.path.join(base, REL))
ok("(sin nada conocido vuelve a ser residuo)", ramas._trabajo_vivo(u) == [])

# ── Copias de casa base (la app copia .claude/logs al crear cada worktree) ────────────
LOG = os.path.join(".claude", "logs", "clinico-access.log")
os.makedirs(os.path.join(base, ".claude", "logs"), exist_ok=True)
os.makedirs(os.path.join(u, ".claude", "logs"), exist_ok=True)
open(os.path.join(base, LOG), "w").write("l1\nl2\n")
open(os.path.join(u, LOG), "w").write("l1\nl2\n")
ok("un log idéntico al de casa base es copia, no trabajo", LOG not in ramas._trabajo_vivo(u))
open(os.path.join(base, LOG), "a").write("l3 (casa base siguió escribiendo)\n")
ok("y si casa base siguió añadiendo líneas, también (es un prefijo)", LOG not in ramas._trabajo_vivo(u))
open(os.path.join(u, LOG), "a").write("acceso escrito SOLO en el worktree\n")
ok("una línea que casa base no tiene lo hace trabajo vivo", LOG in ramas._trabajo_vivo(u))
os.remove(os.path.join(base, LOG))
open(os.path.join(u, LOG), "w").write("l1\n")
ok("sin el fichero en casa base, es trabajo vivo", LOG in ramas._trabajo_vivo(u))
shutil.rmtree(os.path.join(base, ".claude")); shutil.rmtree(os.path.join(u, ".claude"))

# ── Integración: autopoda sobre worktrees fusionados ──────────────────────────────────
wt_res = os.path.join(tmp, "wt-residuo")
wt_real = os.path.join(tmp, "wt-panel-real")
wt_sin = os.path.join(tmp, "wt-sin-fusionar")
for wt, rama in ((wt_res, "r-residuo"), (wt_real, "r-real"), (wt_sin, "r-sin")):
    git(base, "worktree", "add", "-q", "-b", rama, wt)
escribe(wt_res, RESIDUO)
escribe(wt_real, bloque("2026-09-21T09:00:00", "cc1") + bloque("2026-09-21T09:20:00", "cc2"))
open(os.path.join(wt_sin, "nuevo.txt"), "w").write("x\n")
git(wt_sin, "add", "-A"); git(wt_sin, "commit", "-qm", "sin fusionar")
open(os.path.join(wt_sin, "suelto.txt"), "w").write("y\n")   # y encima algo sin commitear

env = dict(os.environ)
r = subprocess.run([sys.executable, RAMAS, "autopoda"], cwd=base, capture_output=True,
                   text=True, env=env, timeout=120)
ok("autopoda termina bien", r.returncode == 0, "-> rc=%d %s" % (r.returncode, r.stderr[-300:]))
ok("poda el worktree fusionado que solo tenía residuo", not os.path.isdir(wt_res), r.stdout[-400:])
ok("la rama se conserva (podar un worktree no borra commits)",
   git(base, "rev-parse", "--verify", "r-residuo").returncode == 0)
ok("NO poda el que tiene un panel de verdad", os.path.isfile(os.path.join(wt_real, REL)))
ok("NO poda el que tiene commits sin fusionar", os.path.isdir(wt_sin))

avisados = os.path.join(state, "autopoda_avisados.json")
try:
    marcados = json.load(open(avisados))
except (OSError, ValueError):
    marcados = {}
ok("el dudoso queda anotado como avisado", any("wt-panel-real" in p for p in marcados),
   "-> %r" % marcados)
ok("el de commits sin fusionar no es «dudoso» (va por huerfanas)",
   not any("wt-sin-fusionar" in p for p in marcados))

sys.path.insert(0, os.path.join(ROOT, "tools"))
ok("el aviso no se repite mientras siga igual", ramas._avisar_dudosos(ramas.dudosos()) == [])

git(base, "worktree", "remove", "--force", wt_real)
git(base, "worktree", "remove", "--force", wt_sin)
shutil.rmtree(tmp, ignore_errors=True)
print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_autopoda_residuo: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
