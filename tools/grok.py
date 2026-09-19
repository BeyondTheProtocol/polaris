#!/usr/bin/env python3
"""Pregunta a Grok (xAI) con búsqueda EN VIVO de web y de X. Sin dependencias (stdlib).
La clave la pones TÚ en tools/.grok_secrets.json — yo nunca la veo.

Setup (1 vez):
  1. console.x.ai → inicia sesión → API Keys → Create. Copia la clave (xai-...). Añade crédito (API de pago por uso).
  2. Crea tools/.grok_secrets.json con:  {"api_key": "xai-...", "model": "grok-4.3"}

Uso:
  python3 grok.py "tu pregunta"                          # busca en web + X en vivo
  python3 grok.py --handles contactoinpublic "lo último"    # solo posts de esos handles en X (máx 20)
  python3 grok.py --nolive "tu pregunta"                 # sin búsqueda (solo conocimiento del modelo)
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".grok_secrets.json")
URL = "https://api.x.ai/v1/responses"

def main():
    args = sys.argv[1:]
    live, handles = True, None
    if "--nolive" in args:
        live = False; args.remove("--nolive")
    if "--handles" in args:
        i = args.index("--handles"); handles = [h.strip() for h in args[i + 1].split(",")]; args = args[:i] + args[i + 2:]
    q = " ".join(args).strip()
    if not q:
        print('Uso: python3 grok.py "tu pregunta"'); return 0
    import borde  # 🔴 BORDE no-bypassable: x.ai = tercero NO confiable
    if not borde.guard_cli(q, "grok"):
        return 1   # stderr ya avisó; stdout vacío → el consumidor ve "sin respuesta"
    api_key = get_secret("btp-grok-api", SECRETS, "api_key")
    if not api_key:
        # A stderr: así un consumidor (x_radar/x_mentions/x_centinela) ve stdout vacío
        # y dispara su rama "Grok no devolvió nada" en vez de archivar el error como contenido.
        print('Falta la clave de Grok: guárdala en el Llavero (btp-grok-api) o en tools/.grok_secrets.json.', file=sys.stderr)
        return 1
    model = "grok-4.3"
    if os.path.exists(SECRETS):
        try: model = (json.load(open(SECRETS)) or {}).get("model", model)
        except Exception as _e:
            try:
                import errores as _err
                _err.registrar("grok", _e, _err.TRANSITORIO, job="secrets", escalar=True)
            except Exception:
                pass
    body = {"model": model, "input": [{"role": "user", "content": q}]}
    if live:
        xsearch = {"type": "x_search"}
        if handles:
            xsearch["allowed_x_handles"] = handles
        body["tools"] = [{"type": "web_search"}, xsearch]
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + api_key,
                                          "Content-Type": "application/json"})
    try:
        resp = json.load(with_retries(lambda: urllib.request.urlopen(req, timeout=120)))
    except urllib.error.HTTPError as e:
        # Errores SIEMPRE a stderr (nunca a stdout) para que un consumidor no los
        # archive como si fueran resultados. Un 410 = endpoint retirado por xAI:
        # avisamos explícito para que se note y no se caiga en silencio.
        detail = ""
        try: detail = e.read().decode()[:600]
        except Exception as _e:
            try:
                import errores as _err
                _err.registrar("grok", _e, _err.TRANSITORIO, job="http_detail", escalar=True)
            except Exception:
                pass
        if e.code == 410:
            print("Grok API 410 (Gone): el endpoint/API que usa grok.py fue RETIRADO por xAI. "
                  "Hay que migrar al formato vigente de Agent Tools (https://docs.x.ai/developers/tools/x-search). "
                  f"Detalle: {detail}", file=sys.stderr)
        else:
            print(f"Grok API error {e.code}: {detail}", file=sys.stderr)
        return 1
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        return 1
    # medidor de gasto (best-effort, nunca rompe): apunta los tokens de esta llamada de pago
    try:
        import gasto
        _u = resp.get("usage") or {}
        gasto.registrar("grok", model,
                        _u.get("input_tokens") or _u.get("prompt_tokens") or 0,
                        _u.get("output_tokens") or _u.get("completion_tokens") or 0)
    except Exception as _e:
        # Mismo carril FAIL-SAFE que el resto del fichero: el medidor es best-effort y
        # NUNCA debe romper la llamada de pago, pero el fallo se REGISTRA por el bus de
        # errores (Fase 1: ningún error de estos carriles se pierde) en vez de tragarse
        # en silencio. El except-pass interno solo blinda que registrar() no rompa.
        try:
            import errores as _err
            _err.registrar("grok", _e, _err.TRANSITORIO, job="gasto", escalar=False)
        except Exception:
            pass
    text = resp.get("output_text")
    if not text:
        chunks = []
        for item in resp.get("output", []):
            for c in (item.get("content", []) if isinstance(item, dict) else []):
                if isinstance(c, dict) and c.get("text"):
                    chunks.append(c["text"])
        text = "\n".join(chunks) if chunks else json.dumps(resp)[:1200]
    print(text)
    return 0

if __name__ == "__main__":
    sys.exit(main())
