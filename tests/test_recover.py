#!/usr/bin/env python3
"""tests/test_recover.py — subcomando recover de cola.py (Fase 3a).

Verifica:
  · dry-run lista candidatos sin tocarlos
  · los schema-invalido NUNCA se re-encolan
  · los schema-desconocido sí se re-encolan
  · filtro por patrón y por fecha funcionan
  · max_jobs limita el batch
  · idempotente: re-recuperar la misma cola no duplica jobs
  · Fase 3b: mark_failed con error CONFIG va directo a dead-letter
Hermético: BTP_STATE_DIR forzado a tmp, sin salida real.
"""
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

# Aislamiento hermético
_TMP = tempfile.mkdtemp(prefix="test_recover_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ.setdefault("BTP_REPO", ROOT)

# Stub para errores.py (para que mark_failed no intente importar salida/codigo_rojo real)
_fake_salida = types.ModuleType("salida")
_fake_salida.report_to_titular = lambda *a, **k: None
_fake_salida.alerta_critica = lambda *a, **k: None
_fake_salida.halted = lambda: False
sys.modules["salida"] = _fake_salida
_fake_cr = types.ModuleType("codigo_rojo")
_fake_cr.trigger = lambda *a, **k: None
sys.modules["codigo_rojo"] = _fake_cr

sys.path.insert(0, TOOLS)
import cola as q   # noqa: E402

_pass = _fail = 0


def ok(cond, nombre):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  [OK]   %s" % nombre)
    else:
        _fail += 1
        print("  [FAIL] %s" % nombre)


def _job_base(job_id, ultimo_error=None, creado="2026-06-20T10:00:00"):
    return {
        "id": job_id, "prioridad": "normal", "intencion": "test recover",
        "agente": None, "modelo": None, "perfil": "privileged",
        "intentos": 3, "max_intentos": 3,
        "creado": creado, "expira": None, "procedencia": "test",
        "tope_job_usd": None, "ultimo_error": ultimo_error,
        "terminado": "2026-06-20T11:00:00", "coste_usd": None,
        "tipo": "exec", "criticidad": "rutina",
        "caja_id": None, "linaje": None, "profundidad": 0,
    }


def _escribir_failed(job_id, ultimo_error=None, creado="2026-06-20T10:00:00"):
    q._ensure_dirs()
    job = _job_base(job_id, ultimo_error=ultimo_error, creado=creado)
    path = os.path.join(q.QUEUE, "failed", "1-20260620T100000-%s.json" % job_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    return path


print("TEST recover (cola.py Fase 3a + Fase 3b)")
q._ensure_dirs()

# ─── 1. dry-run: no mueve nada ────────────────────────────────────────────────
print("\n[1] dry-run: lista sin mover")
_escribir_failed("dry001", ultimo_error="atascado en processing")
res = q.recover(dry_run=True)
ok(len(res["reencolados"]) >= 1, "dry-run: al menos 1 recuperable detectado")
ok(res["dry_run"] is True, "dry-run devuelve dry_run=True")
# el fichero sigue en failed/
ok(os.path.exists(os.path.join(q.QUEUE, "failed", "1-20260620T100000-dry001.json")),
   "dry-run: fichero permanece en failed/")

# ─── 2. schema-invalido NUNCA se re-encola ────────────────────────────────────
print("\n[2] schema-invalido → ignorado siempre")
_escribir_failed("inv001", ultimo_error="schema-invalido: prioridad invalida")
q._ensure_dirs()
n_failed_antes = sum(1 for f in os.listdir(os.path.join(q.QUEUE, "failed")) if f.endswith(".json"))
res = q.recover(patron=None)
# inv001 debe haber sido ignorado, dry001 reencolado
ok("inv001" not in res["reencolados"], "schema-invalido ignorado en re-encolado")

# ─── 3. schema-desconocido SÍ se re-encola ────────────────────────────────────
print("\n[3] schema-desconocido → recuperable")
_escribir_failed("unk001", ultimo_error="schema-desconocido: campo_nuevo (¿casa base sin fusionar?)")
res_dry = q.recover(patron="schema-desconocido", dry_run=True)
ok("unk001" in res_dry["reencolados"], "schema-desconocido aparece como recuperable")

# ─── 4. filtro por patrón ─────────────────────────────────────────────────────
print("\n[4] filtro por patrón")
_escribir_failed("pat001", ultimo_error="timeout en llamada a API")
_escribir_failed("pat002", ultimo_error="disco lleno, imposible continuar")
res_t = q.recover(patron="timeout", dry_run=True)
ok("pat001" in res_t["reencolados"], "filtro 'timeout': captura pat001")
ok("pat002" not in res_t["reencolados"], "filtro 'timeout': excluye pat002")

# ─── 5. filtro por fecha ──────────────────────────────────────────────────────
print("\n[5] filtro por fecha")
_escribir_failed("date001", ultimo_error="error generico", creado="2026-06-25T10:00:00")
_escribir_failed("date002", ultimo_error="error generico", creado="2026-06-01T10:00:00")
res_d = q.recover(desde="2026-06-20", dry_run=True)
ok("date001" in res_d["reencolados"], "filtro fecha: date001 (25/6) incluido")
ok("date002" not in res_d["reencolados"], "filtro fecha: date002 (1/6) excluido")

# ─── 6. max_jobs limita el batch ──────────────────────────────────────────────
print("\n[6] max_jobs")
for i in range(5):
    _escribir_failed("max%03d" % i, ultimo_error="error generico", creado="2026-06-27T10:00:00")
res_m = q.recover(max_jobs=2, dry_run=True)
ok(len(res_m["reencolados"]) <= 2, "max_jobs=2 limita a máximo 2 candidatos")

# ─── 7. re-encolar real + idempotencia ────────────────────────────────────────
print("\n[7] re-encolar real + idempotencia")
_escribir_failed("idem001", ultimo_error="error transitorio")
res_real = q.recover(patron="transitorio", max_jobs=10)
ok("idem001" in res_real["reencolados"], "idem001 reencolado")
ok(os.path.exists(os.path.join(q.QUEUE, "pending",
                               "1-20260620T100000-idem001.json")),
   "idem001 en pending/ tras recover real")
# Segunda pasada: ya no está en failed/ → ignorado (idempotente)
res_idem = q.recover(patron="transitorio", max_jobs=10)
ok("idem001" not in res_idem["reencolados"], "2ª pasada: idem001 ya no está en failed/ → no duplica")

# ─── 8. Fase 3b: mark_failed con error CONFIG → dead-letter directo ───────────
print("\n[8] Fase 3b: mark_failed con error CONFIG va directo a dead-letter")
jid = q.enqueue("job de prueba 3b", procedencia="test_recover", max_intentos=5)
# Buscamos directamente el job por id (la cola puede tener otros jobs de pruebas anteriores)
path_pending = q._find_by_id("pending", jid)
ok(path_pending is not None, "job encolado y encontrado en pending/ por id")
if path_pending:
    job = q._load(path_pending)
    job["_path"] = path_pending
    # Mover a processing/ (simula dequeue manual)
    import shutil as _shutil
    dest_proc = os.path.join(q.QUEUE, "processing", os.path.basename(path_pending))
    os.replace(path_pending, dest_proc)
    job["_path"] = dest_proc
    resultado = q.mark_failed(job, "schema-invalido: campo_raro (CONFIG)")
    ok(resultado == "dead-letter", "mark_failed CONFIG → dead-letter (no re-encola)")
    # Debe estar en failed/, no de vuelta en pending/
    pending_files = os.listdir(os.path.join(q.QUEUE, "pending"))
    ok(not any(jid in f for f in pending_files), "job CONFIG no re-encolado en pending/")
else:
    ok(False, "mark_failed CONFIG → dead-letter (no re-encola) [skip: job no encontrado]")
    ok(False, "job CONFIG no re-encolado en pending/ [skip]")

print("\nRESULTADO: %d OK · %d FAIL" % (_pass, _fail))
print("TEST RECOVER EN VERDE" if _fail == 0 else "TEST RECOVER: %d fallo(s)" % _fail)
sys.exit(_fail)
