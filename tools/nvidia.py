#!/usr/bin/env python3
"""Llama a la API GRATIS de NVIDIA NIM (modelos abiertos, OpenAI-compatible). Sin dependencias (stdlib).
La clave la pones TÚ en el Llavero (btp-nvidia-api) o en tools/.nvidia_secrets.json — yo nunca la veo.

⚠️  CARRIL NO-CLÍNICO. Son modelos ABIERTOS (Llama / DeepSeek / Qwen / Nemotron), más flojos que Opus.
    Úsalo para trabajo masivo / desechable / no crítico. EL MURO MANDA: lo clínico = Claude SIEMPRE.

Setup (1 vez):
  1. build.nvidia.com → inicia sesión → "Get API Key" (o tu cuenta → API Keys). Copia la clave (nvapi-...).
     Tier gratis: ~1.000 créditos (hasta 5.000 a petición) y ~40 peticiones/min. Para prototipar, no producción.
  2. Guárdala en el Llavero (recomendado):
       security add-generic-password -a "$USER" -s btp-nvidia-api -w 'nvapi-...'
     o crea tools/.nvidia_secrets.json con:  {"api_key": "nvapi-...", "model": "meta/llama-3.3-70b-instruct"}

Uso:
  python3 nvidia.py "tu pregunta"                          # modelo por defecto
  python3 nvidia.py --model deepseek-ai/deepseek-r1 "..."  # elige modelo
  python3 nvidia.py --system "Eres un editor conciso" "..."# system prompt
  python3 nvidia.py --models                               # lista EN VIVO los modelos disponibles
  python3 nvidia.py --json "..."                           # JSON crudo de la API
  echo "texto largo" | python3 nvidia.py "resume esto:"    # también lee de stdin (lo añade al final)

Modelos útiles del catálogo gratis (a 9/7/26 — confirma con --models):
  z-ai/glm-5.2                              MoE open MIT, 1M contexto, mejor español/razón (POR DEFECTO)
  deepseek-ai/deepseek-v4-pro                máxima calidad general (pero LENTO: mal para volumen)
  deepseek-ai/deepseek-v4-flash             rápido
  qwen/qwen3.5-397b-a17b                     nueva gen Qwen, muy capaz
  openai/gpt-oss-120b                        open-weight de OpenAI
  moonshotai/kimi-k2.6                       código + uso de herramientas
  nvidia/nemotron-3-super-120b-a12b         MoE potente y eficiente
  deepseek-ai/deepseek-coder-6.7b-instruct  código
💸 NOTA (28/6/26): DeepSeek V4 aquí = **GRATIS** (catálogo NIM, tier ~40 req/min). El precio
~$0.43/$0.87 que circula es el de la **API DIRECTA de DeepSeek** — NO lo que paga {{TITULAR}}; solo
importaría pasado el tier gratis de NIM (volumen/producción). Llama 4 = OBSOLETO, fuera.
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".nvidia_secrets.json")
BASE = "https://integrate.api.nvidia.com/v1"
CHAT_URL = BASE + "/chat/completions"
MODELS_URL = BASE + "/models"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"  # gratis (POR DEFECTO desde 2-sep-26, 2a correccion del MISMO dia). HISTORIA: z-ai/glm-5.2 fue RETIRADO del catalogo (410 Gone) y el carril llevaba caido sin que nadie lo supiera; se probo moonshotai/kimi-k3 (indice 60 en artificialanalysis), respondio a la primera y se puso por defecto -- ERROR: una sola llamada no mide estabilidad. Media hora despues kimi-k3 daba timeout de red repetido a los 180s mientras nemotron-3-ultra y gpt-oss-120b respondian al instante: esta SATURADO, no caido. nemotron-3-ultra-550b: rapido, español correcto, y al ser de la propia NVIDIA es menos probable que se sature. Alt via --model: openai/gpt-oss-120b (rapido, verificado 2-sep), moonshotai/kimi-k3 (mejor en teoria, inestable en la practica), nvidia/nemotron-3-super-120b-a12b. NO usar deepseek-ai/deepseek-v4-pro-0813: se cuelga. NUNCA clinico/PII (muro)


def load_key():
    """(api_key, model) o (None, None) con ayuda. Clave: Llavero (btp-nvidia-api) → fallback fichero."""
    key = (get_secret("btp-nvidia-api", SECRETS, "api_key") or "").strip()
    if not key or not key.startswith("nvapi-"):
        print("Falta la clave de NVIDIA (debe empezar por 'nvapi-'). "
              "Guárdala en el Llavero (btp-nvidia-api) o en tools/.nvidia_secrets.json. "
              "Sácala en build.nvidia.com → Get API Key.")
        return None, None
    model = DEFAULT_MODEL
    if os.path.exists(SECRETS):
        try:
            with open(SECRETS) as f:
                model = (json.load(f) or {}).get("model", DEFAULT_MODEL)
        except Exception:
            pass
    return key, model


def post(url, key, body):
    # 🔴 BORDE en el TRANSPORTE (defensa-en-profundidad, F1 17-jul-26): este es el CUELLO por el que
    # TODO egress de contenido a NVIDIA pasa. El CLI (guard_cli) y carril_gratis (borde.permitido) ya
    # gatean antes; esto lo hace NO-bypassable: un futuro llamador que importe nvidia.post y olvide el
    # borde NO puede filtrar clinico/PII. Se usa borde.clasificar (chequeo PURO, sin sellar el ledger
    # ni disparar canario — eso lo hace la capa de arriba; aqui evitamos doble-sellado). NVIDIA =
    # tercero NO confiable. Fail-closed: si borde no carga, NO se envia.
    msgs = body.get("messages") if isinstance(body, dict) else None
    if msgs:
        texto = "\n".join(str(m.get("content", "")) for m in msgs if isinstance(m, dict))
        try:
            import borde
            sensible, motivo = borde.clasificar(texto)
        except Exception:
            return None, "🛑 BORDE no disponible: no envío a NVIDIA por seguridad (fail-closed)."
        if sensible:
            return None, "🛑 BORDE: %s — contenido no enviado a NVIDIA (tercero no confiable)." % motivo
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    try:
        return json.load(with_retries(lambda: urllib.request.urlopen(req, timeout=180))), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:600]
        hint = ""
        if e.code == 401:
            hint = "  → clave inválida o sin créditos. Revisa la key (nvapi-...) y tu saldo en build.nvidia.com."
        elif e.code == 404 and "model" in detail.lower():
            hint = "  → modelo no encontrado. Mira los disponibles con: python3 nvidia.py --models"
        elif e.code == 429:
            hint = "  → rate limit (tier gratis ~40 req/min). Espera un momento y reintenta."
        return None, f"NVIDIA API error {e.code}: {detail}{hint}"
    except Exception as e:
        return None, f"Error de red: {e}"


def get_json(url, key):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        return json.load(with_retries(lambda: urllib.request.urlopen(req, timeout=60))), None
    except urllib.error.HTTPError as e:
        return None, f"NVIDIA API error {e.code}: {e.read().decode()[:600]}"
    except Exception as e:
        return None, f"Error de red: {e}"


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__); return
    want_json = "--json" in args
    if want_json:
        args.remove("--json")
    list_models = "--models" in args
    if list_models:
        args.remove("--models")
    model = None
    if "--model" in args:
        i = args.index("--model"); model = args[i + 1]; args = args[:i] + args[i + 2:]
    system = None
    if "--system" in args:
        i = args.index("--system"); system = args[i + 1]; args = args[:i] + args[i + 2:]

    key, default_model = load_key()
    if not key:
        return
    model = model or default_model

    if list_models:
        data, err = get_json(MODELS_URL, key)
        if err:
            print(err); return
        ids = sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
        print(f"{len(ids)} modelos disponibles en el catálogo NVIDIA NIM:")
        for mid in ids:
            print("  " + mid)
        return

    q = " ".join(args).strip()
    if not sys.stdin.isatty():                       # texto canalizado por stdin → lo añadimos
        piped = sys.stdin.read().strip()
        if piped:
            q = (q + "\n\n" + piped).strip() if q else piped
    if not q:
        print('Uso: python3 nvidia.py "tu pregunta"   (--help para todas las opciones)'); return

    import borde  # 🔴 BORDE no-bypassable: NVIDIA = tercero NO confiable (carril NO-clínico)
    if not borde.guard_cli(q, "nvidia"):
        return

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": q})

    data, err = post(CHAT_URL, key, {"model": model, "messages": messages})
    if err:
        print(err); return
    if want_json:
        print(json.dumps(data, ensure_ascii=False, indent=2)); return
    try:
        print(data["choices"][0]["message"]["content"])
    except Exception:
        print(json.dumps(data, ensure_ascii=False)[:1200])


if __name__ == "__main__":
    main()
