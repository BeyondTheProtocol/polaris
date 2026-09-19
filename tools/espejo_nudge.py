#!/usr/bin/env python3
"""tools/espejo_nudge.py — aviso SEMANAL (determinista, sin LLM) de que toca refrescar el espejo de
investigación → Notion.

POR QUÉ existe: el muro PROHÍBE todo MCP en el carril autónomo (`SAFE_MCP_TOOLS = frozenset()` en
muro_guard) → el daemon NO puede leer la base «Biblioteca de papers» ni escribir en Notion. El sync
real es SUPERVISADO (corre en una sesión). Esto NO sincroniza nada: solo RECUERDA, por el único canal
de salida permitido (tools/salida.py → Telegram), con cooldown anti-spam. Respeta HALT y el silencio
nocturno (los aplica salida.report_to_titular). Barato: 0 tokens, 0 red salvo el envío.

Uso:
  python3 tools/espejo_nudge.py            # envía el aviso si no está en cooldown
  python3 tools/espejo_nudge.py --dry      # no envía (prueba de cableo); ignora cooldown
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import salida  # única boca al exterior (Telegram a la propia {{TITULAR}})

# Estado VIVO en casa base (igual criterio que seguimiento.py): el marcador de cooldown no viaja a
# los worktrees. BTP_STATE_DIR aísla en tests.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
MARK = os.path.join(STATE, "espejo_nudge.json")
COOLDOWN_S = 6 * 24 * 3600        # no repetir en <6 días (anti doble-fire / re-run manual)

NUDGE = ("🔬 Toca refrescar el espejo de investigación en Notion (los papers que marcaste "
         "«Publicar en web»). El muro no me deja sincronizarlo sola en automático, así que cuando "
         "estés en una sesión dímelo y lo dejo al día en un minuto.")


def _en_cooldown():
    try:
        with open(MARK, encoding="utf-8") as f:
            return (time.time() - float(json.load(f).get("ts", 0))) < COOLDOWN_S
    except Exception:
        return False           # sin marcador / ilegible → no hay cooldown (avisa)


def _sella():
    try:
        os.makedirs(STATE, exist_ok=True)
        tmp = MARK + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time()}, f)
        os.replace(tmp, MARK)
    except Exception as e:
        print("aviso: no pude sellar el cooldown: %r" % e, file=sys.stderr)


def main(argv):
    dry = "--dry" in argv
    if not dry and _en_cooldown():
        print("nudge en cooldown (<6 días); no reenvío.")
        return 0
    res = salida.report_to_titular(NUDGE, dry=dry, voz="calida")
    if not dry and isinstance(res, dict) and res.get("delivered"):
        _sella()               # solo sella si se ENTREGÓ (si HALT/noche lo bloquea, reintenta)
    print(json.dumps(res, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
