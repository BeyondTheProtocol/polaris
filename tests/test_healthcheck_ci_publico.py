#!/usr/bin/env python3
"""test_healthcheck_ci_publico.py — el CI del repo público no puede estar rojo en silencio.

Nace del 22-sep-2026: el CI de BeyondTheProtocol/polaris falló 6 veces seguidas con el badge
«failing» en la portada del lanzamiento, y el único aviso era un correo de GitHub. Sin red:
`gh` se sustituye por un falso que devuelve la lista de ejecuciones que toque.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as hc   # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class _R:
    def __init__(self, out, rc=0, err=""):
        self.stdout = out.encode("utf-8")
        self.stderr = err.encode("utf-8")
        self.returncode = rc


def _gh(runs, rc=0, err=""):
    llamadas = []

    def run(cmd, **kw):
        llamadas.append(cmd)
        return _R(json.dumps(runs), rc, err)
    return run, llamadas


def _r(conclusion, status="completed", wf="contribuciones", rama="master", n=0):
    return {"workflowName": wf, "headBranch": rama, "status": status,
            "conclusion": conclusion, "url": "https://github.com/x/actions/runs/%d" % n,
            "createdAt": "2026-09-22T11:%02d:00Z" % n}


def _una(runs, **kw):
    d = tempfile.mkdtemp()
    run, llamadas = _gh(runs, **kw)
    alertas, info = hc._check_ci_publico(run=run, state_dir=d, gh="gh")
    return alertas, info, llamadas, d


# 1. El caso real del 22-sep: 6 rojas seguidas (de nueva a vieja) y una verde detrás.
alertas, info, llamadas, _ = _una([_r("failure", n=i) for i in range(6)] + [_r("success", n=9)])
check("rojo → alerta con clave estable", [a[0] for a in alertas] == ["ci_publico_rojo"])
check("cuenta las 6 seguidas", alertas and "6 seguidas" in alertas[0][1])
check("lleva el enlace a la ejecución", alertas and "actions/runs/0" in alertas[0][1])
check("pregunta al repo público", llamadas and "BeyondTheProtocol/polaris" in llamadas[0])

# 2. Verde → silencio.
alertas, info, _, _ = _una([_r("success", n=0), _r("failure", n=1)])
check("última verde → sin alerta aunque antes hubiera rojo", alertas == [])
check("info dice success", info.get("workflows") == {"contribuciones": "success"})

# 3. Una en curso no tapa la roja terminada, ni la pinta de verde.
alertas, _, _, _ = _una([_r(None, status="in_progress", n=0), _r("failure", n=1)])
check("en curso encima de una roja → sigue la alerta", [a[0] for a in alertas] == ["ci_publico_rojo"])

# 4. Ramas que no son la por defecto no cuentan.
alertas, _, _, _ = _una([_r("failure", rama="experimento", n=0), _r("success", n=1)])
check("rojo en otra rama → sin alerta", alertas == [])

# 5. Cancelado no es rojo (lo cancela quien relanza); timed_out sí.
alertas, _, _, _ = _una([_r("cancelled", n=0), _r("success", n=1)])
check("cancelled → sin alerta", alertas == [])
alertas, _, _, _ = _una([_r("timed_out", n=0)])
check("timed_out → alerta", [a[0] for a in alertas] == ["ci_publico_rojo"])

# 6. Varios workflows: se juzga cada uno por su última.
alertas, _, _, _ = _una([_r("success", wf="a", n=0), _r("failure", wf="b", n=1)])
check("solo el workflow rojo sale", alertas and "«b»" in alertas[0][1] and "«a»" not in alertas[0][1])

# 7. gh falla (sin red, sin sesión): no grita.
alertas, info, _, _ = _una([], rc=1, err="HTTP 401")
check("gh con error → sin alerta, error en info", alertas == [] and "401" in info.get("error", ""))

# 8. Throttle: dentro de la hora no se vuelve a llamar a gh y se repite la alerta guardada.
_, _, _, d = _una([_r("failure", n=0)])
run, llamadas = _gh([_r("success", n=0)])
alertas, info = hc._check_ci_publico(run=run, state_dir=d, gh="gh")
check("throttle → no llama a gh", llamadas == [] and info.get("throttled"))
check("throttle → repite la alerta guardada", [a[0] for a in alertas] == ["ci_publico_rojo"])

# 9. Está enchufado en run() (un check que no se llama no vigila nada).
fuente = open(os.path.join(ROOT, "tools", "healthcheck.py"), encoding="utf-8").read()
check("run() llama a _check_ci_publico", "ci_alertas, ci_info = _check_ci_publico()" in fuente)

print("RESULTADO healthcheck ci público: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
