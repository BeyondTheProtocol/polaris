#!/usr/bin/env python3
"""Tests del regenerador (tools/rebuild_agents.py). Sobre fixtures en /tmp; nunca toca agentes reales."""
import os
import sys
import tempfile

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import rebuild_agents as ra  # noqa: E402

fallos = 0


def check(nombre, cond):
    global fallos
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


def w(path, txt):
    open(path, "w", encoding="utf-8").write(txt)


def r(path):
    return open(path, encoding="utf-8").read()


tmp = tempfile.mkdtemp(prefix="rebuild_agents_")
ra.AGENTS_DIR = tmp
TPL = ra.template()

STALE = "<!-- BOILERPLATE:START viejo -->\n## Muro VIEJO\n- algo obsoleto\n<!-- BOILERPLATE:END -->"
PREFIX_A = "---\nname: a\n---\n\nEres el agente A. GUARDARRAIL PROPIO IMPORTANTE.\n\n"
PREFIX_C = "---\nname: comite-medico\n---\n\n5 lentes adversariales. NUNCA concluye. (bespoke clínico)\n\n"
BODY_B = "---\nname: b\n---\n\nEres B. Sin bloque. Cuerpo bespoke que no se debe tocar.\n"

w(os.path.join(tmp, "a.md"), PREFIX_A + STALE + "\n")
w(os.path.join(tmp, "comite-medico.md"), PREFIX_C + STALE + "\n")
w(os.path.join(tmp, "b.md"), BODY_B)

# 1) --check con bloques viejos → detecta drift (rc 1) en a y comite-medico, no en b
rc = ra.main(["--check"])
check("--check detecta drift (rc 1) con bloques viejos", rc == 1)

# guardar estado previo de los que NO deben cambiar
b_antes = r(os.path.join(tmp, "b.md"))

# 2) sync → regenera SOLO el bloque marcado
rc = ra.main([])
a_post = r(os.path.join(tmp, "a.md"))
c_post = r(os.path.join(tmp, "comite-medico.md"))
check("sync rc 0", rc == 0)
check("a: el bloque quedó igual al núcleo", a_post == PREFIX_A + TPL + "\n")
check("a: el cuerpo bespoke se conservó", "GUARDARRAIL PROPIO IMPORTANTE" in a_post)
check("comité-médico: bloque sincronizado y cuerpo clínico intacto",
      c_post == PREFIX_C + TPL + "\n" and "NUNCA concluye" in c_post)
check("b (sin marcar) NO se tocó, byte a byte", r(os.path.join(tmp, "b.md")) == b_antes)

# 3) idempotente: un segundo sync no cambia nada
a_tras1 = r(os.path.join(tmp, "a.md"))
ra.main([])
check("sync idempotente (sin cambios la 2ª vez)", r(os.path.join(tmp, "a.md")) == a_tras1)

# 4) --check tras sincronizar → limpio (rc 0)
check("--check limpio tras sync (rc 0)", ra.main(["--check"]) == 0)

# 5) --init añade el bloque a un agente sin marcar, conservando su cuerpo
ra.main(["--init", "b"])
b_post = r(os.path.join(tmp, "b.md"))
check("--init añade el bloque a b", ra.BLOCK_RE.search(b_post) is not None)
check("--init conserva el cuerpo de b", b_post.startswith(BODY_B.rstrip("\n")))

# 6) --init es idempotente (no duplica el bloque)
ra.main(["--init", "b"])
check("--init no duplica el bloque", r(os.path.join(tmp, "b.md")).count(ra.START) == 1)

print("RESULTADO rebuild_agents: %d OK, %d fallos" % (10 - fallos, fallos))
print("✅ REGENERADOR EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
