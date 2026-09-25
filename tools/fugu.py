#!/usr/bin/env python3
"""tools/fugu.py — consulta PUNTUAL y AISLADA a Sakana Fugu (IA externa de pago).

Fugu es un modelo orquestador de terceros (API comercial, OpenAI-compatible) que por
dentro reparte la petición a un pool OPACO de modelos. Por eso aquí va FUERTEMENTE
encajonada:

  · ON-DEMAND: se llama a mano ("consúltale a Fugu X"). NO es agente, NO es MCP, NO es
    rutina, NO está en la constelación ni en run_agent.sh. Cada consulta es estanca: solo
    ve el texto que le pasas, nunca el RAG, la fuente de verdad ni ficheros.
  · 🔴 CORTAFUEGOS DE EGRESS (fail-closed): antes de enviar NADA, escanea el prompt y lo
    BLOQUEA si huele a clínico/genómico/PII o a término vetado ({{CONTACTO}}/vacuna). Ante la
    duda, NO sale. Reutiliza los deny-lists de seguimiento.py.
  · COSTE controlado: pasa por cost_guard (tope por consulta + tope diario) y se registra.
  · Su respuesta es DATO NO CONFIABLE (IA externa): no se persiste a memoria a ciegas.

Setup (1 vez, manos de {{TITULAR}}): saca y PAGA la clave en Sakana → guárdala en el Llavero
con `bash tools/setup_keychain.sh` (btp-fugu-api). El sistema nunca ve la clave.
La URL base de la API la da la doc de Sakana; ponla en tools/.fugu_secrets.json
{"base_url": "https://api.sakana.ai/v1"} si difiere del valor por defecto.

Uso:
  python3 tools/fugu.py "explica el patrón X de código"     # consulta normal (modelo fugu)
  python3 tools/fugu.py --ultra "problema difícil multipaso" # Fugu Ultra (más caro)
  python3 tools/fugu.py --dry "..."                          # solo prueba el cortafuegos
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret          # noqa: E402
from _net import stream_chat                     # noqa: E402
import cost_guard                                # noqa: E402
import borde                                     # noqa: E402  — la ÚNICA pared del egress

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".fugu_secrets.json")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
LOGDIR = os.path.join(STATE, "fugu")

DEFAULT_BASE = "https://api.sakana.ai/v1"   # confirmar con la doc de Sakana al sacar la key
TOPE_JOB_USD = 1.00      # tope modesto por consulta (subido 0.50→1.00 el 23/6 para holgura de auditoría; sigue muy por debajo del techo $3/job y $30/día de cost_guard)
MAX_TOKENS = 4000        # cabe una generación larga; el dinero lo topa cost_guard
PRECIO_POR_1K = 0.01     # estimación conservadora (pricing real de Fugu desconocido aún)


# ── 🔴 Cortafuegos de egress (FAIL-CLOSED) — delega en el BORDE no-bypassable ───────
# Antes fugu tenía su propia copia de la pared; ahora hay UNA sola fuente (tools/borde.py),
# que además sella la decisión en la traza encadenada. Fugu = destino NO confiable (API de
# pago en la nube), así que lo sensible NUNCA pasa.
def egress_check(text):
    """(ok, motivo). FAIL-CLOSED vía borde.egress_check (destino no confiable 'fugu')."""
    v = borde.egress_check(text, destino="fugu", intencion="consulta-fugu")
    return v.permitido, v.motivo


# ── Registro (sin guardar el contenido del prompt) ─────────────────────────────────
def _log(estado, motivo, usd, ultra):
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        rec = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
               "modelo": "fugu-ultra" if ultra else "fugu",
               "estado": estado, "motivo": motivo, "usd": round(float(usd or 0), 6)}
        with open(os.path.join(LOGDIR, "log-%s.jsonl" % datetime.now().strftime("%Y-%m-%d")),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _base_url():
    if os.path.exists(SECRETS):
        try:
            b = (json.load(open(SECRETS)) or {}).get("base_url")
            if b:
                return b.rstrip("/")
        except Exception:
            pass
    return os.environ.get("FUGU_BASE_URL", DEFAULT_BASE).rstrip("/")


def _estimar_usd(usage):
    """USD aprox. a partir del bloque `usage` (si la API lo manda al final del stream)."""
    try:
        tok = (usage or {}).get("total_tokens")
        if isinstance(tok, (int, float)):
            return round(tok / 1000.0 * PRECIO_POR_1K, 6)
    except Exception:
        pass
    return None


def consultar(prompt, *, ultra=False, dry=False):
    ok, motivo = egress_check(prompt)
    if not ok:
        _log("bloqueado", motivo, 0, ultra)
        return {"blocked": True, "motivo": motivo}
    if dry:
        return {"dry": True, "ok": True, "motivo": "pasaría el cortafuegos (no se envió)"}
    api_key = get_secret("btp-fugu-api", SECRETS, "api_key")
    if not api_key:
        return {"error": "falta la clave 'btp-fugu-api' en el Llavero. Págala en Sakana y "
                         "guárdala con: bash tools/setup_keychain.sh"}
    okb, motivob, _ = cost_guard.check_before_job(tope_job_usd=TOPE_JOB_USD)
    if not okb:
        _log("sin_presupuesto", motivob, 0, ultra)
        return {"error": "sin presupuesto hoy: " + motivob}
    body = {"model": "fugu-ultra" if ultra else "fugu",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        # streaming: la conexión viva evita el corte por inactividad en respuestas largas
        text, usage = stream_chat(_base_url() + "/chat/completions", body, headers)
    except urllib.error.HTTPError as e:
        _log("error", "HTTP %s" % e.code, 0, ultra)
        return {"error": "Fugu API %d: %s" % (e.code, e.read().decode()[:400])}
    except Exception as e:
        _log("error", repr(e), 0, ultra)
        return {"error": "red: %r" % e}
    usd = _estimar_usd(usage)
    if usd is None:
        # el stream no trajo bloque `usage` → estima por longitud (≈4 chars/token), NUNCA
        # el tope por llamada (contarlo entero agotaba el presupuesto del día por las buenas).
        usd = round((len(prompt) + len(text)) / 4.0 / 1000.0 * PRECIO_POR_1K, 6)
    cost_guard.add_cost(usd, job_id="fugu", tope_job_usd=TOPE_JOB_USD)
    _log("ok", "enviado", usd, ultra)
    return {"text": text, "coste_usd": usd}


def main(argv):
    ultra = "--ultra" in argv
    dry = "--dry" in argv
    args = [a for a in argv if a not in ("--ultra", "--dry")]
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    prompt = " ".join(args).strip()
    if not prompt:
        print('Uso: python3 tools/fugu.py "tu consulta"  [--ultra] [--dry]')
        return 2
    r = consultar(prompt, ultra=ultra, dry=dry)
    if r.get("blocked"):
        print("🛑 BLOQUEADO por el cortafuegos: %s. No se envió nada a Fugu." % r["motivo"])
        return 3
    if r.get("dry"):
        print("✅ %s" % r["motivo"])
        return 0
    if r.get("error"):
        print("error: %s" % r["error"], file=sys.stderr)
        return 1
    print(r["text"])
    if r.get("coste_usd") is not None:
        print("\n— coste aprox: $%.4f" % r["coste_usd"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
