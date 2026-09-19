#!/usr/bin/env bash
# Durabilidad del guardián de lectura clínica (Capa A).
# Aplica (idempotente) las reglas `permissions.deny` que impiden LEER (Read/Grep/Glob) el
# dato clínico N2 a la config GLOBAL de Claude Code (~/.claude/settings.json).
#
# POR QUÉ: la config global no está versionada; si se pierde ese fichero, la cerradura se cae.
# Este script ES el registro versionado y reproducible. Correr en cada máquina donde corran
# agentes (portátil + Polaris):   bash tools/aplicar-guardian-lectura.sh
#
# NO afecta al RAG (kb.py lee vía python open(), no por la herramienta Read).
set -euo pipefail
SETTINGS="${1:-$HOME/.claude/settings.json}"
python3 - "$SETTINGS" <<'PY'
import json, os, sys
f = sys.argv[1]
os.makedirs(os.path.dirname(f), exist_ok=True)
d = json.load(open(f)) if os.path.exists(f) else {}
perms = d.setdefault("permissions", {}); deny = perms.setdefault("deny", [])
# Familias de rutas clínicas (N2). Catch-all `**/_PRIVADO_*` cubre repo + copia iCloud en
# cualquier home (titular / polaris). Se cubren las 3 herramientas de lectura.
paths = [
    "//Users/titular/Clinico-PRIVADO/**",
    "//Users/polaris/Clinico-PRIVADO/**",
    "//**/_PRIVADO_CORREO/**", "//**/_PRIVADO_NUCLEO/**",
    "//**/_PRIVADO_WHATSAPP/**", "//**/_PRIVADO_X/**", "//**/_PRIVADO_CLINICO/**",
    "//Users/titular/Library/Mobile Documents/com~apple~CloudDocs/Documents/MI VIDA/00_Salud/informes/**",
    "//Users/titular/Library/Mobile Documents/com~apple~CloudDocs/Documents/MI VIDA/00_Salud/Historial clinico {{TITULAR}} a abril 2026/**",
]
added = 0
for t in ("Read", "Grep", "Glob"):
    for p in paths:
        r = f"{t}({p})"
        if r not in deny:
            deny.append(r); added += 1
json.dump(d, open(f, "w"), ensure_ascii=False, indent=1)
json.load(open(f))  # valida
print(f"guardián aplicado a {f}: +{added} reglas nuevas (deny total {len(deny)})")
PY
echo "OK. (En Polaris: ssh polaris 'cd ~/claudecode && bash tools/aplicar-guardian-lectura.sh')"
