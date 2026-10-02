#!/usr/bin/env python3
"""test_backup_latido.py — la copia de seguridad deja latido y healthcheck lo vigila.

2-oct-2026 (propuesta del buzón de Vega): el daemon de las 04:30 no tenía latido y ya pasó 13 días
sin copia sin que nadie lo viera. Se fija:
  1. la función `hb` de backup.sh escribe heartbeat/backup.json con estado y ts (JSON válido);
  2. backup.sh late «ok» al final y «error_rc_N» si restic falla, y NO late al saltar sin disco;
  3. healthcheck vigila al agente «backup» en VEGA_DAEMONS.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.path.join(ROOT, "tools", "backup.sh")
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


src = open(BACKUP, encoding="utf-8").read()
m = re.search(r"^hb\(\)\{.*?^\}", src, re.S | re.M)
check(m is not None, "backup.sh define hb()")
if m:
    d = tempfile.mkdtemp(prefix="hb_backup_")
    r = subprocess.run(["bash", "-c", m.group(0) + "\nhb ok"], env=dict(os.environ, BTP_HB_DIR=d,
                                                                       SRC_REPO="/nada"),
                       capture_output=True, text=True, timeout=20)
    f = os.path.join(d, "backup.json")
    j = json.load(open(f)) if os.path.exists(f) else {}
    check(r.returncode == 0 and j.get("agente") == "backup" and j.get("estado") == "ok"
          and j.get("ts", "").endswith("Z"), "hb ok escribe un latido válido (%r)" % j)

fin = src.rsplit('log "=== fin', 1)[-1]
check("hb ok" in fin, "late «ok» al terminar la copia")
check('hb "error_rc_$rc"' in src, "late el error si restic falla")
salto = src.split("no montado", 1)[-1].split("\n", 1)[0]
check("hb" not in salto, "sin disco no late (así el latido envejece y avisa)")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck  # noqa: E402
check(any(d.get("agente") == "backup" for d in healthcheck.VEGA_DAEMONS),
      "healthcheck vigila el latido del backup")

print("\n" + ("✅ BACKUP LATIDO EN VERDE" if not fallos else "❌ %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
