#!/usr/bin/env python3
"""test_claude_lazo.py — la versión de Claude Code del lazo solo sube con el canario del muro en verde.

POR QUÉ EXISTE (P9, 25-sep-2026). Idea de {{CONTACTO}} (https://contacto), con su agente KAI,
revisión del 25-sep-2026. Fijar la versión del lazo sin cortar los parches de seguridad: la fijada
sube sola, pero solo a una versión que carga los hooks del muro en TODOS los settings del lazo.

Qué se fija aquí (`tools/claude_lazo.py`), con versiones falsas en una carpeta de usar y tirar:
  1. sin versión fijada, `ruta` devuelve `claude` (el canario de run_agent.sh sigue guardando);
  2. `revisar` fija la más nueva si pasa el canario, y `ruta` devuelve su binario enlazado;
  3. `revisar` no repite antes de 20 h salvo --forzar;
  4. una versión nueva que NO carga hooks no se fija, la anterior se queda, aviso 1 sola vez;
  5. si la fijada lleva más de 14 días sin poder subir, aviso de desfase;
  6. si alguien borra el binario fijado, `ruta` cae a `claude`;
  7. la poda deja solo la fijada y la anterior.
Nunca toca el ~/.local/share real ni manda avisos de verdad (BTP_SALIDA apunta a un espía).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="claude_lazo_")
VERS = os.path.join(TMP, "versions")
BINS = os.path.join(TMP, "bins")
ST = os.path.join(TMP, "state")
AVISOS = os.path.join(TMP, "avisos.log")
os.makedirs(VERS)
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def version(v, hooks=True):
    """Un `claude` falso: dice su versión y, si `hooks`, ejecuta el hook canario de verdad."""
    p = os.path.join(VERS, v)
    open(p, "w").write("#!/bin/bash\n"
                       "[ \"${1:-}\" = --version ] && { echo '%s (Claude Code)'; exit 0; }\n"
                       "%s\nexit 1\n" % (v, ('echo "{}" | "%s/.claude/hooks/canario_muro.sh"' % ROOT) if hooks else ":"))
    os.chmod(p, 0o755)


ESPIA = os.path.join(TMP, "salida_espia.py")
open(ESPIA, "w").write("import sys\nopen(%r,'a').write(' | '.join(sys.argv[1:])+'\\n')\n" % AVISOS)


def lazo(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_")}
    env.update(BTP_REPO=ROOT, BTP_STATE_DIR=ST, BTP_CLAUDE_VERSIONS_DIR=VERS, BTP_LAZO_BIN_DIR=BINS,
               BTP_SALIDA=ESPIA, BTP_CANARIO_ESPERA="10")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "claude_lazo.py")] + list(args),
                       capture_output=True, text=True, env=env, timeout=300)
    return p.stdout.strip()


def avisos():
    return open(AVISOS).read().splitlines() if os.path.exists(AVISOS) else []


def pin():
    return json.load(open(os.path.join(ST, "claude_lazo", "pin.json")))


print("1. sin versión fijada → `claude`")
check(lazo("ruta") == "claude", "ruta = claude")

print("2. revisar fija la más nueva si pasa el canario")
version("1.0.0"); version("1.0.2"); version("1.0.10")
r = lazo("revisar")
check(r.startswith("fijada 1.0.10"), "fija 1.0.10, orden numérico y no alfabético (%s)" % r)
check(lazo("ruta") == os.path.join(BINS, "1.0.10"), "ruta = binario enlazado")
check(os.path.samefile(os.path.join(BINS, "1.0.10"), os.path.join(VERS, "1.0.10")), "enlace duro, no copia")

print("3. no repite antes de 20 h")
check(lazo("revisar").startswith("revisado hace"), "revisión reciente → no hace nada")
check(lazo("revisar", "--forzar") == "al día: 1.0.10", "con --forzar → al día")

print("4. versión nueva sin hooks (como --bare por defecto) → no se fija")
version("1.1.0", hooks=False)
r = lazo("revisar", "--forzar")
check(r.startswith("rojo: 1.1.0"), "rojo (%s)" % r)
check(pin()["version"] == "1.0.10", "sigue fijada 1.0.10")
check(lazo("ruta") == os.path.join(BINS, "1.0.10"), "ruta sigue en 1.0.10")
check(not os.path.exists(os.path.join(BINS, "1.1.0")), "el binario rojo no se queda en la carpeta del lazo")
check(len(avisos()) == 1 and "1.1.0" in avisos()[0] and avisos()[0].startswith("report-urgente"), "un aviso urgente")
lazo("revisar", "--forzar")
check(len(avisos()) == 1, "no repite el aviso de la misma versión")

print("5. más de 14 días sin poder subir → aviso de desfase")
p = pin(); p["fijada"] = "2026-01-01T00:00:00+00:00"
json.dump(p, open(os.path.join(ST, "claude_lazo", "pin.json"), "w"))
lazo("revisar", "--forzar")
check(len(avisos()) == 2 and "días" in avisos()[-1], "aviso de desfase")

print("6. versión nueva buena → sube; poda deja la fijada y la anterior")
version("1.2.0")
r = lazo("revisar", "--forzar")
check(r.startswith("fijada 1.2.0"), "fija 1.2.0 (%s)" % r)
check(pin().get("anterior") == "1.0.10", "recuerda la anterior")
check(sorted(os.listdir(BINS)) == ["1.0.10", "1.2.0"], "poda (%s)" % sorted(os.listdir(BINS)))

print("7. si falta el binario fijado → `claude`")
os.remove(os.path.join(BINS, "1.2.0"))
check(lazo("ruta") == "claude", "cae a claude")

print("8. fijar a mano una versión rota → no se fija")
check(lazo("fijar", "1.1.0").startswith("rojo"), "fijar 1.1.0 → rojo")
check(pin()["version"] == "1.2.0", "sigue 1.2.0")

print("\n%s" % ("🔴 %d fallo(s)" % len(fallos) if fallos else "✅ versión fijada del lazo: todo en verde"))
sys.exit(1 if fallos else 0)
