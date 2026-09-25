#!/usr/bin/env python3
"""tests/test_poda_no_se_lleva_ignorados.py — que la poda no borre lo que git no ve.

EL FALLO (20-sep-2026, dos veces el mismo día). `cerrar_sesion.py` decidía podar mirando solo
`sin_commitear` y `fusionado`. Los dos salen de git, y git NO ve los ficheros ignorados — que es
justo donde vive lo que duele perder:

  · 52 entradas del panel del lazo en un worktree, y 12 en otro (`00_FUENTE-DE-VERDAD/` está
    gitignored), escritas allí por jobs que resolvían su ruta contra el árbol donde corrían;
  · 52.682 líneas del log de auditoría clínica en `.claude/logs/`, con accesos que no estaban
    en el log de casa base.

Las dos veces se salvaron porque alguien miró a mano antes de borrar. «Alguien se acuerda» no es
un freno: esto sí. Ahora `_trabajo_vivo` mira también esos dos árboles y la poda se niega,
diciendo qué hay y dónde, en vez de llevárselo en silencio.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ramas  # noqa: E402

OK, FALLOS = [], []


def ok(msg, cond, extra=""):
    (OK if cond else FALLOS).append(msg)
    print(("  ✅ " if cond else "  ❌ ") + msg + (("  " + extra) if extra and not cond else ""))


tmp = tempfile.mkdtemp(prefix="test-poda-")


def _crea(rel, contenido="x\n"):
    d = os.path.join(tmp, os.path.dirname(rel))
    os.makedirs(d, exist_ok=True)
    open(os.path.join(tmp, rel), "w").write(contenido)


# 1. Un worktree vacío de cosas ignoradas: no hay nada que rescatar.
ok("un worktree sin ficheros ignorados no reporta nada", ramas._trabajo_vivo(tmp) == [],
   "-> %r" % ramas._trabajo_vivo(tmp))

# 2. El caso real nº1: el panel del lazo dentro del worktree.
_crea("00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md", "## 2026-09-20  job abc123\n")
v = ramas._trabajo_vivo(tmp)
ok("detecta el panel del lazo escrito dentro del worktree",
   any("PANEL-LAZO.md" in f for f in v), "-> %r" % v)

# 3. El caso real nº2: la traza de auditoría clínica.
_crea(".claude/logs/clinico-access.log", "2026-09-20\tALLOW\tBash\n")
v = ramas._trabajo_vivo(tmp)
ok("detecta el log de auditoría clínica dentro del worktree",
   any("clinico-access.log" in f for f in v), "-> %r" % v)

# 4. Anidado hondo: no vale mirar solo el primer nivel.
_crea("00_FUENTE-DE-VERDAD/01 · Tratamiento/sub/otro/dato.md")
ok("mira en profundidad, no solo el primer nivel",
   any("dato.md" in f for f in ramas._trabajo_vivo(tmp)))

# 5. Lo de siempre sigue cubierto (borradores esperando el OK de {{TITULAR}}).
_crea("tools/state/outbox/pending/borrador.json", "{}")
ok("sigue detectando un borrador pendiente de su OK",
   any("borrador.json" in f for f in ramas._trabajo_vivo(tmp)))

# 6. Y la poda lo consulta: no basta con saberlo, hay que usarlo donde se borra.
src = open(os.path.join(ROOT, "tools", "cerrar_sesion.py"), encoding="utf-8").read()
ok("cerrar_sesion consulta _trabajo_vivo antes de podar", "_trabajo_vivo(wt)" in src,
   "-> la poda volvería a ser ciega a los ignorados")
ok("y un hallazgo IMPIDE la poda, no solo la comenta",
   "seguido" not in src and "seguro = False" in src)

# 7. UN SOLO rescate para el cierre y para la autopoda (24-sep-2026).
#    Pasó de verdad, dos veces en un día: dos sesiones en paralelo escribieron un `rescatar()`
#    distinto en `tools/ramas.py`. Git fusiona dos funciones con el mismo nombre SIN conflicto:
#    la segunda gana en silencio, y `ramas.limpia` petó con TypeError (la autopoda dejó de
#    rescatar y de podar). Luego, al quitar una, la otra rama ya llamaba a la quitada. Por eso
#    esto se comprueba: una sola definición, una sola firma, y los dos sitios llamándola igual.
import inspect  # noqa: E402
fuente_ramas = open(os.path.join(ROOT, "tools", "ramas.py"), encoding="utf-8").read()
ok("`rescatar` está definida UNA sola vez en ramas.py",
   fuente_ramas.count("\ndef rescatar(") == 1,
   "-> %d definiciones" % fuente_ramas.count("\ndef rescatar("))
ok("con la firma que usan los dos sitios",
   list(inspect.signature(ramas.rescatar).parameters) == ["wt", "base", "destino", "copiar"],
   "-> %s" % inspect.signature(ramas.rescatar))
ok("la autopoda la llama", "rescatar(w[\"path\"], _casa(), cajon)" in fuente_ramas)
ok("y el cierre de sesión también", "_ramas.rescatar(wt, BASE, cajon" in src)
ok("todo lo que ramas.py llama `_lineas_ya_en` existe",
   ("_lineas_ya_en(" not in fuente_ramas) or ("def _lineas_ya_en(" in fuente_ramas))

# 8. El rescate en sí: lo repetido se descarta, lo propio se copia, lo vivo bloquea.
import shutil  # noqa: E402
wt, base = tempfile.mkdtemp(prefix="rw-"), tempfile.mkdtemp(prefix="rb-")
cajon = os.path.join(base, "tools/state/rescate-poda/x")


def _en(raiz, rel, contenido):
    os.makedirs(os.path.dirname(os.path.join(raiz, rel)), exist_ok=True)
    open(os.path.join(raiz, rel), "w").write(contenido)


_en(base, ".claude/logs/clinico-access.log", "a\nb\nc\n")
_en(wt, ".claude/logs/clinico-access.log", "a\nb\n")                 # copia vieja de casa base
_en(base, "00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md", "viejo\n")
_en(wt, "00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md", "viejo\n## job nuevo\n")   # algo propio
r = ramas.rescatar(wt, base, cajon, copiar=False)
ok("dry-run: la copia vieja cuenta como repetida",
   r["repetidos"] == [".claude/logs/clinico-access.log"], "-> %r" % r)
ok("dry-run: no escribe nada", not os.path.exists(cajon))
r = ramas.rescatar(wt, base, cajon)
ok("lo propio se rescata al cajón",
   r["rescatados"] == ["00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md"], "-> %r" % r)
ok("y queda íntegro",
   open(os.path.join(cajon, "00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md")).read()
   == "viejo\n## job nuevo\n")
ok("el fichero de casa base NO se toca (restos de tests no ensucian el panel)",
   open(os.path.join(base, "00_FUENTE-DE-VERDAD/Gestion/PANEL-LAZO.md")).read() == "viejo\n")
_en(wt, "tools/state/outbox/pending/borrador.json", "{}")
ok("un borrador esperando su OK SIGUE bloqueando la poda",
   any("borrador.json" in f for f in ramas.rescatar(wt, base, cajon, copiar=False)["bloquean"]))
ok("fail-closed: si casa base no se puede leer, no se da por repetido",
   not ramas._lineas_ya_en(os.path.join(wt, ".claude/logs/clinico-access.log"), "/no/existe"))
shutil.rmtree(wt, ignore_errors=True)
shutil.rmtree(base, ignore_errors=True)

shutil.rmtree(tmp, ignore_errors=True)

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_poda_no_se_lleva_ignorados: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
