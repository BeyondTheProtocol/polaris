#!/usr/bin/env python3
"""test_centralita_blacklist.py — regresión del FIX 3 de A1 (10-jul-26): exclusión ESTRUCTURAL
(por código, no por convención de plist) de los roles-con-shell del respaldo de la centralita.

Origen: a fin de junio, un bug real dejó a la centralita (cerebro SIN herramientas) narrando en
TEXTO comandos de `git` como si los hubiera ejecutado, con heartbeat "sano" — indistinguible de
un éxito real salvo cruzando con el log a mano. Hoy está mitigado porque NINGÚN .plist de un rol
con shell activa BTP_FREE_OK=1 (verificado: `tools/launchd/*.plist`) — pero eso es "seguro por
AUSENCIA de una variable en cada plist", no una regla dura. `run_agent.sh` ahora incluye una
lista negra explícita (`_en_blacklist_centralita`, ver `intentar_centralita()`) que excluye
`git`/`tecnico`/`constructor` del respaldo AUNQUE su plist ponga `BTP_FREE_OK=1` por error.

NO incluye `orquestador`: su respaldo discreto vía centralita ya es un camino PROBADO y en
producción (ver `test_run_agent_f2.py`, caso 2) — este test lo re-confirma como control (la
blacklist no debe sobre-bloquear lo que ya funciona).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="centralita_bl_")
_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# claude falso: 429 (límite) en todos los modelos -> cadena agotada -> intentar_centralita() decide.
_BIN_429 = os.path.join(_TMP, "claude_429.sh")
open(_BIN_429, "w").write('#!/bin/bash\necho \'{"api_error_status": 429, "is_error": true}\'\n')
os.chmod(_BIN_429, 0o755)
_REG = os.path.join(_TMP, "peripheries.json")
json.dump({"cerebros": [{"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia",
                         "trusted": False, "free": True, "orden": 10, "enabled": True}]},
          open(_REG, "w"))


def _siembra_salud(tmp):
    """Caché de salud PRE-cocinado: nvidia-free vivo, sin preguntar por su clave.

    `ia.ask` filtra por `_salud_disponibles()` ANTES de invocar, y para un
    `carril_gratis` eso acaba en `nvidia.load_key()` → `tools/.nvidia_secrets.json`,
    fichero gitignored que solo existe en casa base. En un worktree el cerebro salía
    "no disponible", ia.ask lo saltaba y `BTP_IA_FAKE` no llegaba a aplicarse nunca:
    la centralita callaba por FALTA DE CLAVE y el test lo leía como si fuera la
    blacklist. Los tres casos negativos pasaban por el motivo equivocado (falso verde)
    y el control del orquestador fallaba. Sembrando el caché, lo único que decide es
    `_en_blacklist_centralita` — que es lo que este test dice medir."""
    d = os.path.join(tmp, "ia")
    os.makedirs(d, exist_ok=True)
    json.dump({"cerebros": [{"name": "nvidia-free", "kind": "carril_gratis",
                             "trusted": False, "free": True, "enabled": True,
                             "disponible": True}]},
              open(os.path.join(d, "health.json"), "w"))


def run_agent(agent, free_ok=True):
    tmp = tempfile.mkdtemp(prefix="centralita_bl_run_")
    _siembra_salud(tmp)
    env = dict(os.environ, BTP_CLAUDE_BIN=_BIN_429, BTP_API_KEY_OVERRIDE="x",
               BTP_COST_GUARDED="1", BTP_STATE_DIR=tmp, BTP_PERIPHERIES=_REG,
               BTP_IA_FAKE="DESDE-CENTRALITA", BTP_AGENT=agent, BTP_MODEL="sonnet", BTP_REPO=ROOT,
               BTP_HALT_FILES=os.path.join(tmp, "nh_a") + ":" + os.path.join(tmp, "nh_b"))
    if free_ok:
        env["BTP_FREE_OK"] = "1"
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "resume esto"],
                       capture_output=True, text=True, env=env)
    hb = {}
    try:
        hb = json.load(open(os.path.join(tmp, "heartbeat", agent + ".json")))
    except Exception:
        pass
    return p.stdout, hb.get("estado"), p.returncode


def main():
    # --- Roles-con-shell en la blacklist: NUNCA centralita, ni con BTP_FREE_OK=1 ---
    for agente in ("git", "tecnico", "constructor"):
        out, hb, rc = run_agent(agente, free_ok=True)
        ok("DESDE-CENTRALITA" not in out,
           "%s + BTP_FREE_OK=1 + Claude agotado -> NUNCA responde la centralita" % agente)
        ok(rc == 75 and hb == "aplazado_limite",
           "%s -> aplaza limpio (exit 75, heartbeat aplazado_limite), no finge" % agente)

    # --- Control: SIN el blacklist, un plist mal editado (BTP_FREE_OK=1 en un rol con shell)
    # habría fingido -- confirmamos que la exclusión es del propio run_agent.sh, no del plist:
    # el mismo agente CON free_ok=False también aplaza (ya lo hacía; no es lo que prueba el fix).
    out, hb, rc = run_agent("git", free_ok=False)
    ok("DESDE-CENTRALITA" not in out and rc == 75,
       "git SIN BTP_FREE_OK tampoco cae a la centralita (comportamiento ya esperado)")

    # --- Control: orquestador SIGUE pudiendo usar el respaldo (camino probado, no lo rompemos) ---
    out, hb, rc = run_agent("orquestador", free_ok=True)
    ok("DESDE-CENTRALITA" in out and rc == 0 and hb == "centralita",
       "orquestador + BTP_FREE_OK=1 sigue con respaldo (la blacklist no sobre-bloquea)")

    print("RESULTADO centralita blacklist (A1 fix 3): %d OK, %d fallos" % (_pass, _fail))
    print("✅ EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
