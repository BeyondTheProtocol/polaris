#!/usr/bin/env python3
"""Cliente de OpenAI (ChatGPT / GPT) por API. Sin dependencias (stdlib).
La clave la pones TÚ en el Llavero (btp-openai-api) o en tools/.openai_secrets.json — yo nunca la veo.

⚠️  CARRIL: 2º par de ojos sobre razonamiento médico GENÉRICO + deep-research con citas.
    EL MURO MANDA: lo clínico/genómico CRUDO (VCF/HLA/PII/informes) = Claude en LOCAL, NUNCA aquí.
    A OpenAI solo terminología genérica (gen/variante/HGVS/fármaco), jamás el caso crudo.
    (La API de OpenAI no entrena con tus datos por defecto; para máxima estrictez, pide ZDR.)

Setup (1 vez):
  1. platform.openai.com → API keys → Create. Copia la clave (sk-...). Añade crédito (pago por uso).
  2. Guárdala en el Llavero (recomendado):
       security add-generic-password -a "$USER" -s btp-openai-api -w 'sk-...'
     o crea tools/.openai_secrets.json con:  {"api_key": "sk-...", "model": "gpt-5"}
     (confirma el nombre exacto del modelo en platform.openai.com → Models al poner la key.)

Uso:
  python3 chatgpt.py "tu pregunta"                       # modelo por defecto
  python3 chatgpt.py --model gpt-5-mini "..."            # elige modelo
  python3 chatgpt.py --system "Eres un revisor crítico" "..."
  python3 chatgpt.py --json "..."                        # JSON crudo de la API
  echo "texto" | python3 chatgpt.py "resume:"            # también lee de stdin
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries, stdin_canalizado

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".openai_secrets.json")
URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-5"


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__); return
    want_json = "--json" in args
    if want_json:
        args.remove("--json")
    model = None
    if "--model" in args:
        i = args.index("--model"); model = args[i + 1]; args = args[:i] + args[i + 2:]
    system = None
    if "--system" in args:
        i = args.index("--system"); system = args[i + 1]; args = args[:i] + args[i + 2:]

    api_key = get_secret("btp-openai-api", SECRETS, "api_key")
    if not api_key:
        print("Falta la clave de OpenAI: guárdala en el Llavero (btp-openai-api) o en tools/.openai_secrets.json. "
              "Sácala en platform.openai.com → API keys.")
        return
    if not model:
        model = DEFAULT_MODEL
        if os.path.exists(SECRETS):
            try: model = (json.load(open(SECRETS)) or {}).get("model", DEFAULT_MODEL)
            except Exception as _e:
                try:
                    import errores as _err
                    _err.registrar("chatgpt", _e, _err.TRANSITORIO, job="secrets", escalar=True)
                except Exception:
                    pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso

    q = " ".join(args).strip()
    piped = stdin_canalizado(1.0 if q else 10.0).strip()   # texto canalizado → se añade
    if piped:
        q = (q + "\n\n" + piped).strip() if q else piped
    if not q:
        print('Uso: python3 chatgpt.py "tu pregunta"   (--help para todo)'); return

    import borde  # 🔴 BORDE no-bypassable: OpenAI = tercero NO confiable
    if not borde.guard_cli(q, "chatgpt"):
        return

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": q})
    req = urllib.request.Request(URL, data=json.dumps({"model": model, "messages": messages}).encode(),
                                 headers={"Authorization": "Bearer " + api_key,
                                          "Content-Type": "application/json"})
    try:
        resp = json.load(with_retries(lambda: urllib.request.urlopen(req, timeout=180)))
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:600]
        hint = "  → revisa la clave (sk-...), el saldo, y el nombre del modelo en platform.openai.com." if e.code in (401, 404) else ""
        print(f"OpenAI API error {e.code}: {detail}{hint}"); return
    except Exception as e:
        print("Error de red:", e); return
    try:                                   # ledger de gasto (best-effort, no rompe la respuesta)
        import gasto
        _u = (resp or {}).get("usage") or {}
        gasto.registrar("chatgpt", model,
                        _u.get("prompt_tokens") or _u.get("input_tokens") or 0,
                        _u.get("completion_tokens") or _u.get("output_tokens") or 0)
    except Exception:
        pass
    if want_json:
        print(json.dumps(resp, ensure_ascii=False, indent=2)); return
    try:
        print(resp["choices"][0]["message"]["content"])
    except Exception:
        print(json.dumps(resp, ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
