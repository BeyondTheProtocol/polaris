#!/usr/bin/env bash
# Capa B del guardián: quita la herramienta Bash a los agentes PÚBLICOS que NO la necesitan,
# cerrando el bypass por shell (`python3 -c open(clinico)`) que la Capa A (deny de Read/Grep/Glob)
# no puede cubrir. Usa `disallowedTools: Bash` (doc oficial: quita SOLO Bash, conserva
# Read/Write/Edit/Grep/Glob/WebSearch/WebFetch y MCP). Idempotente.
#
# NO se tocan dm-inbox / x-inbox / monitor-lanzamiento: SÍ usan Bash para sus scripts.
# Correr en cada máquina:  bash tools/aplicar-guardian-agentes.sh [ruta .claude/agents]
set -euo pipefail
DIR="${1:-.claude/agents}"
PUBLICOS="prensa redes-contenido comunidad"
for a in $PUBLICOS; do
  f="$DIR/$a.md"
  [ -f "$f" ] || { echo "  (no existe: $f)"; continue; }
  python3 - "$f" <<'PY'
import sys, re
f = sys.argv[1]
s = open(f).read()
if re.search(r'(?m)^disallowedTools:', s):
    print(f"  = {f} (ya tenía disallowedTools)"); raise SystemExit
# Insertar dentro del frontmatter (antes del segundo '---'). Si por lo que sea hay 'tools:',
# se añade en su propia línea; disallowedTools se aplica primero igualmente.
m = re.match(r'^(---\n.*?\n)(---\n)', s, re.S)
if not m:
    print(f"  ! {f} sin frontmatter reconocible — NO tocado"); raise SystemExit
nuevo = m.group(1) + "disallowedTools: Bash\n" + m.group(2) + s[m.end():]
open(f, "w").write(nuevo)
print(f"  + {f} (disallowedTools: Bash)")
PY
done
echo "OK — Capa B aplicada a los públicos sin-Bash."
