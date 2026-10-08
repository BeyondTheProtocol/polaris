#!/usr/bin/env python3
"""Cliente de NaN Community (nan.builders) — GPUs compartidas con modelos abiertos, cuota plana.

Por qué existe: el carril de volumen colgaba de un solo proveedor (el tier gratis de NVIDIA NIM).
NaN es una comunidad de pago (membresía mensual, sin coste por token) que sirve modelos abiertos
en GPUs propias en la UE, con API compatible con el formato OpenAI. Da un segundo proveedor para
el trabajo masivo o desechable cuando NVIDIA topa o se cae.

Qué NO resuelve: es un TERCERO que ve el prompt entero, así que el borde lo trata como no
confiable igual que a los demás. Su web declara «Zero logs»; es su afirmación, no una garantía
comprobada. NUNCA clínico ni PII (lo niega el borde).

Setup (1 vez):
  1. cloud.nan.builders → ajustes → API Keys → crear clave. Guardarla:
       security add-generic-password -U -a "$USER" -s btp-nan-api -w
  2. Poner "enabled": true en la entrada `nan` de tools/peripheries.json.

Uso:
  python3 tools/nan.py "tu pregunta"
  python3 tools/nan.py --model glm5.3-flash "..."
  python3 tools/nan.py --listar          # qué modelos sirve hoy (pide clave, no gasta tokens)
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import stream_chat

BASE = "https://api.nan.builders/v1"
URL = BASE + "/chat/completions"
URL_MODELOS = BASE + "/models"
DEFAULT_MODEL = "deepseek-v4-flash"   # el catálogo real del 8-oct-26 no trae `glm5.3`, solo el flash
# Sin esto la API devuelve 403 «error code: 1010»: su Cloudflare rechaza el User-Agent por
# defecto de urllib (comprobado el 8-oct-26 con la clave ya válida).
UA = "polaris-nan/1.0"


def _clave():
    return get_secret("btp-nan-api")


def listar():
    api_key = _clave()
    if not api_key:
        print("Falta la clave de NaN: Llavero (btp-nan-api)."); return
    req = urllib.request.Request(URL_MODELOS, headers={"Authorization": "Bearer " + api_key,
                                                       "User-Agent": UA})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:
        print("No se pudo leer el catálogo de NaN:", e); return
    filas = d.get("data") or []
    print("%d modelos en NaN hoy" % len(filas))
    for m in sorted(filas, key=lambda x: x.get("id", "")):
        print("  %s" % m.get("id", "?"))


def main():
    args = sys.argv[1:]
    if "--listar" in args:
        listar(); return
    model = DEFAULT_MODEL
    if "--model" in args:
        i = args.index("--model"); model = args[i + 1]; args = args[:i] + args[i + 2:]
    q = " ".join(args).strip()
    if not q:
        print('Uso: python3 tools/nan.py "tu pregunta"  ·  --listar para ver el catálogo')
        return
    import borde  # 🔴 BORDE no-bypassable: GPUs de terceros = NO confiable
    if not borde.guard_cli(q, "nan"):
        return
    api_key = _clave()
    if not api_key:
        print("Falta la clave de NaN: Llavero (btp-nan-api)."); return
    body = {"model": model, "messages": [{"role": "user", "content": q}], "max_tokens": 8192}
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "User-Agent": UA}
    try:
        text, usage = stream_chat(URL, body, headers)
    except urllib.error.HTTPError as e:
        print(f"NaN API error {e.code}: {e.read().decode()[:500]}"); return
    except Exception as e:
        print("Error:", e); return
    try:
        # Cuota plana: se apuntan los tokens; sin tarifa por token, el ledger deja `usd: null`.
        import gasto
        gasto.registrar("nan", model,
                        (usage or {}).get("prompt_tokens") or 0,
                        (usage or {}).get("completion_tokens") or 0)
    except Exception:
        pass
    print(text if text.strip() else "(respuesta vacía)")


if __name__ == "__main__":
    main()
