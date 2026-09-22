#!/usr/bin/env python3
"""Cliente de GLM 5.2 (Z.ai / Zhipu) por API — especializado en WEB DESIGN / frontend.

Por qué: GLM 5.2 es #1 en Design Arena (diseño web HTML), por delante de Claude
Fable 5/Opus 4.7, y mucho más barato. Se usa para generar/iterar UI y HTML; el
juicio de marca/diseño sigue siendo del comité web ({{CONTACTO}}/Diseño/Producto). API
compatible con formato OpenAI (chat completions). Sin pip (stdlib).

APOYO: no manda PII a la API (guardarraíl heredable). Para web pública no hay PII clínica.

Setup (1 vez):
  1. z.ai → API Keys (pago por uso, o GLM Coding Plan desde ~$10/mes). Guarda la clave:
       security add-generic-password -U -a "$USER" -s btp-glm-api -w
  2. (opcional) tools/.glm_secrets.json: {"api_key":"...", "model":"glm-5.2"}

Uso:
  python3 tools/glm.py "genera una landing de donación accesible, hero + CTA"
  python3 tools/glm.py --model glm-5.2 "..."
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import stream_chat

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".glm_secrets.json")
URL = "https://api.z.ai/api/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-5.2"


def main():
    args = sys.argv[1:]
    model = DEFAULT_MODEL
    if "--model" in args:
        i = args.index("--model"); model = args[i + 1]; args = args[:i] + args[i + 2:]
    elif os.path.exists(SECRETS):
        try: model = (json.load(open(SECRETS)) or {}).get("model", DEFAULT_MODEL)
        except Exception as _e:
            try:
                import errores as _err
                _err.registrar("glm", _e, _err.TRANSITORIO, job="secrets", escalar=True)
            except Exception:
                pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso
    q = " ".join(args).strip()
    if not q:
        print('Uso: python3 tools/glm.py "tu encargo de web design"'); return
    import borde  # 🔴 BORDE no-bypassable: GLM (Zhipu) = tercero NO confiable
    if not borde.guard_cli(q, "glm"):
        return
    api_key = get_secret("btp-glm-api", SECRETS, "api_key")
    if not api_key:
        print("Falta la clave de GLM: Llavero (btp-glm-api) o tools/.glm_secrets.json."); return
    body = {"model": model, "messages": [{"role": "user", "content": q}]}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        text, usage = stream_chat(URL, body, headers)   # streaming: aguanta respuestas largas
    except urllib.error.HTTPError as e:
        print(f"GLM API error {e.code}: {e.read().decode()[:500]}"); return
    except Exception as e:
        print("Error:", e); return
    try:                                   # ledger de gasto (best-effort, no rompe la respuesta)
        import gasto
        gasto.registrar("glm", model,
                        (usage or {}).get("prompt_tokens") or 0,
                        (usage or {}).get("completion_tokens") or 0)
    except Exception:
        pass
    print(text if text.strip() else "(respuesta vacía)")


if __name__ == "__main__":
    main()
