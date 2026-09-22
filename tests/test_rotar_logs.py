#!/usr/bin/env python3
"""tests/test_rotar_logs.py — higiene de logs (tools/rotar_logs.py, Fase 3c).

Verifica:
  · dry-run no toca nada
  · trunca .out/.err que superan el umbral
  · no trunca .out/.err por debajo del umbral
  · archiva JSONL viejos (respeta MAX_DIAS)
  · nunca toca el fichero del día de hoy
  · borrar=True borra en vez de archivar
  · max_por_pasada limita el batch
Hermético: BTP_STATE_DIR y BTP_LOGS_DIR forzados a tmp.
"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

_TMP = tempfile.mkdtemp(prefix="test_rotar_logs_")
_LOGS = os.path.join(_TMP, "logs")
os.makedirs(_LOGS, exist_ok=True)
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_LOGS_DIR"] = _LOGS
os.environ.setdefault("BTP_REPO", ROOT)

sys.path.insert(0, TOOLS)
import rotar_logs as rl   # noqa: E402

_pass = _fail = 0


def ok(cond, nombre):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  [OK]   %s" % nombre)
    else:
        _fail += 1
        print("  [FAIL] %s" % nombre)


def _escribir(path, contenido="x" * 1024):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(contenido)
    return path


def _fecha(dias_atras):
    return (datetime.now() - timedelta(days=dias_atras)).date().isoformat()


print("TEST rotar_logs.py — higiene de logs y JSONL")

# ─── 1. dry-run: no toca nada ────────────────────────────────────────────────
print("\n[1] dry-run (por defecto): no toca nada")
log_viejo = _escribir(os.path.join(_LOGS, "daemon.out"), "x" * (20 * 1024 * 1024))  # 20MB
informe = rl.run(dry_run=True)
ok(any(i.get("accion") == "truncar" and i.get("dry") for i in informe),
   "dry-run informa que truncaría el log grande")
ok(os.path.getsize(log_viejo) == 20 * 1024 * 1024, "dry-run: fichero sin tocar")

# ─── 2. trunca logs grandes ────────────────────────────────────────────────────
print("\n[2] trunca .out/.err > MAX_LOG_MB")
rl.MAX_LOG_MB = 10  # 10 MB de umbral
informe = rl.run(dry_run=False)
acciones_trunc = [i for i in informe if i.get("accion") == "truncar"]
ok(len(acciones_trunc) >= 1, "hay al menos 1 acción de truncar")
ok(os.path.getsize(log_viejo) == 0, "fichero truncado a 0 bytes")
ok(os.path.exists(log_viejo), "fichero truncado aún existe (no borrado)")

# ─── 3. no trunca logs pequeños ────────────────────────────────────────────────
print("\n[3] no trunca .out/.err por debajo del umbral")
log_peq = _escribir(os.path.join(_LOGS, "small.out"), "abc")
informe2 = rl.run(dry_run=False)
trunc_peq = [i for i in informe2 if i.get("accion") == "truncar" and "small.out" in i.get("path", "")]
ok(len(trunc_peq) == 0, "small.out no truncado (por debajo del umbral)")
ok(os.path.getsize(log_peq) == 3, "small.out intacto")

# ─── 4. archiva JSONL viejos ──────────────────────────────────────────────────
print("\n[4] archiva JSONL de observabilidad viejos")
obs_dir = os.path.join(_TMP, "observabilidad")
os.makedirs(obs_dir, exist_ok=True)
# Fichero viejo (20 días atrás)
fecha_vieja = _fecha(20)
jsonl_viejo = _escribir(os.path.join(obs_dir, "observabilidad-%s.jsonl" % fecha_vieja), '{"test":1}\n')
# Fichero de hoy
fecha_hoy = datetime.now().date().isoformat()
jsonl_hoy = _escribir(os.path.join(obs_dir, "observabilidad-%s.jsonl" % fecha_hoy), '{"hoy":1}\n')

rl.MAX_DIAS_OBS = 14  # archiva los de más de 14 días
informe3 = rl.run(dry_run=False)
ok(not os.path.exists(jsonl_viejo), "JSONL viejo (20d) movido de observabilidad/")
archive = os.path.join(_TMP, "archive")
ok(os.path.exists(os.path.join(archive, "observabilidad-%s.jsonl" % fecha_vieja)),
   "JSONL viejo archivado en archive/")
ok(os.path.exists(jsonl_hoy), "JSONL de hoy intacto (nunca se toca)")

# ─── 5. nunca toca el día de hoy ──────────────────────────────────────────────
print("\n[5] el fichero del día de hoy no se toca nunca")
# Confirmado en el paso anterior; doble check
ok(os.path.exists(jsonl_hoy), "jsonl_hoy existe tras la pasada")
with open(jsonl_hoy, encoding="utf-8") as f:
    contenido_hoy = f.read()
ok('"hoy":1' in contenido_hoy, "jsonl_hoy tiene el contenido original intacto")

# ─── 6. borrar=True borra en vez de archivar ──────────────────────────────────
print("\n[6] borrar=True borra el JSONL en vez de archivarlo")
fecha_vieja2 = _fecha(25)
jsonl_borrar = _escribir(os.path.join(obs_dir, "observabilidad-%s.jsonl" % fecha_vieja2), '{"del":1}\n')
informe4 = rl.run(dry_run=False, borrar=True)
ok(not os.path.exists(jsonl_borrar), "JSONL viejo2 borrado con borrar=True")
ok(not os.path.exists(os.path.join(archive, "observabilidad-%s.jsonl" % fecha_vieja2)),
   "JSONL viejo2 NO está en archive (borrado, no archivado)")

# ─── 7. MAX_POR_PASADA limita el batch ─────────────────────────────────────────
print("\n[7] MAX_POR_PASADA limita el batch")
borde_dir = os.path.join(_TMP, "borde")
os.makedirs(borde_dir, exist_ok=True)
for i in range(10):
    fecha_b = _fecha(40 + i)
    _escribir(os.path.join(borde_dir, "ledger-%s.jsonl" % fecha_b), '{"i":%d}\n' % i)
rl.MAX_DIAS_BORDE = 30
rl.MAX_POR_PASADA = 3
informe5 = rl.run(dry_run=False)
acciones_borde = [i for i in informe5 if "borde" in i.get("path", "") and i.get("accion") in ("archivar", "borrar")]
ok(len(acciones_borde) <= 3, "MAX_POR_PASADA=3 limita el batch (got %d)" % len(acciones_borde))

print("\nRESULTADO: %d OK · %d FAIL" % (_pass, _fail))
print("TEST ROTAR_LOGS EN VERDE" if _fail == 0 else "TEST ROTAR_LOGS: %d fallo(s)" % _fail)
sys.exit(_fail)
