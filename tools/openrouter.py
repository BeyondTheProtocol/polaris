#!/usr/bin/env python3
"""Cliente de OpenRouter — una sola clave, cientos de modelos de muchas casas.

Por qué existe: el registro de la centralita tenía una entrada por proveedor, y cada
proveedor nuevo costaba un alta, una clave y un cliente. OpenRouter es una pasarela
compatible con el formato OpenAI: con UNA clave se alcanzan modelos de OpenAI,
Anthropic, Google, Meta, Mistral, DeepSeek, Qwen, Moonshot y demás, y aparecer un
modelo nuevo no obliga a tocar nada aquí.

Qué NO resuelve: es un TERCERO que ve el prompt entero, así que el borde lo trata como
no confiable igual que a los demás. Y no sustituye a los clientes directos — cuando ya
hay cuenta propia con un proveedor (Claude, Gemini, GLM…), el directo sale más barato y
tiene menos saltos; esto es para alcanzar lo que NO tiene cuenta propia.

Setup (1 vez):
  1. openrouter.ai → Keys → crear clave. Guardarla:
       security add-generic-password -U -a "$USER" -s btp-openrouter-api -w
  2. (opcional) tools/.openrouter_secrets.json: {"api_key":"...", "model":"..."}
  3. Poner "enabled": true en la entrada `openrouter` de tools/peripheries.json.

Uso:
  python3 tools/openrouter.py "tu pregunta"
  python3 tools/openrouter.py --model deepseek/deepseek-chat "..."
  python3 tools/openrouter.py --listar          # qué modelos hay hoy (no gasta tokens)
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import stream_chat

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".openrouter_secrets.json")
URL = "https://openrouter.ai/api/v1/chat/completions"
URL_MODELOS = "https://openrouter.ai/api/v1/models"
DEFAULT_MODEL = "deepseek/deepseek-chat"


def _clave():
    return get_secret("btp-openrouter-api", SECRETS, "api_key")


def listar():
    """El catálogo de hoy. Va sin clave a propósito: sirve para decidir ANTES de dar de alta."""
    try:
        d = json.load(urllib.request.urlopen(URL_MODELOS, timeout=30))
    except Exception as e:
        print("No se pudo leer el catálogo de OpenRouter:", e); return
    filas = d.get("data") or []
    print("%d modelos en OpenRouter hoy" % len(filas))
    for m in sorted(filas, key=lambda x: x.get("id", ""))[:80]:
        pr = (m.get("pricing") or {}).get("prompt")
        print("  %-52s %s" % (m.get("id", "?"), ("$%s/tok entrada" % pr) if pr else ""))


def main():
    args = sys.argv[1:]
    if "--listar" in args:
        listar(); return
    model = DEFAULT_MODEL
    if "--model" in args:
        i = args.index("--model"); model = args[i + 1]; args = args[:i] + args[i + 2:]
    elif os.path.exists(SECRETS):
        try: model = (json.load(open(SECRETS)) or {}).get("model", DEFAULT_MODEL)
        except Exception as _e:
            try:
                import errores as _err
                _err.registrar("openrouter", _e, _err.TRANSITORIO, job="secrets", escalar=True)
            except Exception:
                pass  # el bus de errores ya es fail-safe; esto blinda el propio aviso
    q = " ".join(args).strip()
    if not q:
        print('Uso: python3 tools/openrouter.py "tu pregunta"  ·  --listar para ver el catálogo')
        return
    import borde  # 🔴 BORDE no-bypassable: pasarela de terceros = NO confiable
    if not borde.guard_cli(q, "openrouter"):
        return
    api_key = _clave()
    if not api_key:
        print("Falta la clave de OpenRouter: Llavero (btp-openrouter-api) o "
              "tools/.openrouter_secrets.json."); return
    body = {"model": model, "messages": [{"role": "user", "content": q}]}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        text, usage = stream_chat(URL, body, headers)
    except urllib.error.HTTPError as e:
        print(f"OpenRouter API error {e.code}: {e.read().decode()[:500]}"); return
    except Exception as e:
        print("Error:", e); return
    try:
        import gasto
        gasto.registrar("openrouter", model,
                        (usage or {}).get("prompt_tokens") or 0,
                        (usage or {}).get("completion_tokens") or 0)
    except Exception:
        pass
    print(text if text.strip() else "(respuesta vacía)")


if __name__ == "__main__":
    main()
