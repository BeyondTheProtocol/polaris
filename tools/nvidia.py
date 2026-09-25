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
     o crea tools/.nvidia_secrets.json con:  {"api_key": "nvapi-...", "model": "nvidia/nemotron-3-ultra-550b-a55b"}

Uso:
  python3 nvidia.py "tu pregunta"                          # modelo por defecto
  python3 nvidia.py --model z-ai/glm-5.3 "..."             # elige modelo
  python3 nvidia.py --system "Eres un editor conciso" "..."# system prompt
  python3 nvidia.py --models                               # lista EN VIVO los modelos disponibles
  python3 nvidia.py --json "..."                           # JSON crudo de la API
  echo "texto largo" | python3 nvidia.py "resume esto:"    # también lee de stdin (lo añade al final)

Modelos útiles del catálogo gratis (a 22/9/26, cotejado con --models; cambia a menudo: confírmalo):
  nvidia/nemotron-3-ultra-550b-a55b         POR DEFECTO: rápido, buen español, no se satura
  nvidia/nemotron-3-super-120b-a12b         MoE potente y eficiente
  z-ai/glm-5.3  ·  z-ai/glm-5.3-flash       Zhipu (China), buen español y razonamiento
  moonshotai/kimi-k3  ·  kimi-k2.6          Moonshot (China); k3 mejor en teoría, inestable en la práctica
  deepseek-ai/deepseek-v4.1-flash           DeepSeek (China), rápido
  01-ai/yi-large                            01.AI (China)
  mistralai/mistral-large-2-instruct        Mistral (Francia)
  openai/gpt-oss-20b                        open-weight de OpenAI (el de 120b ya no está)
  deepseek-ai/deepseek-coder-6.7b-instruct  código
  Retirados del catálogo desde el 9/7 (NO usar): z-ai/glm-5.2, deepseek-ai/deepseek-v4-pro(-0813),
  deepseek-ai/deepseek-r1, qwen/qwen3.5-397b-a17b, openai/gpt-oss-120b, meta/llama-3.3-70b-instruct.
💸 NOTA (28/6/26): DeepSeek V4 aquí = **GRATIS** (catálogo NIM, tier ~40 req/min). El precio
~$0.43/$0.87 que circula es el de la **API DIRECTA de DeepSeek** — NO lo que paga {{TITULAR}}; solo
importaría pasado el tier gratis de NIM (volumen/producción). Llama 4 = OBSOLETO, fuera.
"""
import json, os, sys, threading, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries, stdin_canalizado, stream_chat

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".nvidia_secrets.json")
BASE = "https://integrate.api.nvidia.com/v1"
CHAT_URL = BASE + "/chat/completions"
MODELS_URL = BASE + "/models"
# Tope TOTAL de una llamada de chat, sumando reintentos y esperas. 90 s y no menos: un resumen
# largo del carril gratis tarda más que un «ok». Ajustable sin tocar código.
TOPE_TOTAL_S = int(os.environ.get("BTP_NVIDIA_TOPE_S", "90"))
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"  # gratis (POR DEFECTO desde 2-sep-26, 2a correccion del MISMO dia). HISTORIA: z-ai/glm-5.2 fue RETIRADO del catalogo (410 Gone) y el carril llevaba caido sin que nadie lo supiera; se probo moonshotai/kimi-k3 (indice 60 en artificialanalysis), respondio a la primera y se puso por defecto -- ERROR: una sola llamada no mide estabilidad. Media hora despues kimi-k3 daba timeout de red repetido a los 180s mientras nemotron-3-ultra y gpt-oss-120b respondian al instante: esta SATURADO, no caido. nemotron-3-ultra-550b: rapido, español correcto, y al ser de la propia NVIDIA es menos probable que se sature. Alt via --model: z-ai/glm-5.3 (vivo 22-sep; gpt-oss-120b ya retirado), moonshotai/kimi-k3 (mejor en teoria, inestable en la practica), nvidia/nemotron-3-super-120b-a12b. NO usar deepseek-ai/deepseek-v4-pro-0813: se cuelga. NUNCA clinico/PII (muro)


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


def _veto_borde(body):
    """Motivo para NO enviar (str) o None. El mismo borde para post() y chat_stream(): cualquier
    camino hacia NVIDIA pasa por aquí. Fail-closed: si borde no carga, no se envía."""
    msgs = body.get("messages") if isinstance(body, dict) else None
    if not msgs:
        return None
    texto = "\n".join(str(m.get("content", "")) for m in msgs if isinstance(m, dict))
    try:
        import borde
        sensible, motivo = borde.clasificar(texto)
    except Exception:
        return "🛑 BORDE no disponible: no envío a NVIDIA por seguridad (fail-closed)."
    if sensible:
        return "🛑 BORDE: %s — contenido no enviado a NVIDIA (tercero no confiable)." % motivo
    return None


def chat_stream(key, body, espera_s=120):
    """Chat recibiendo la respuesta POR TROZOS (stream). (texto, error).

    POR QUÉ (22-sep-26): sin stream, el servidor no manda nada hasta acabar de generar, y el corte
    por inactividad del socket (180 s) mataba cualquier respuesta larga: GLM 5.3 y DeepSeek no
    terminaban una tabla de 10 filas, y no se podía saber si era el modelo o la herramienta. Con
    stream, mientras lleguen trozos la llamada está viva; `espera_s` es el máximo SIN recibir nada.
    Lo usa el CLI. `post()` (sin stream, con tope total) sigue siendo lo del carril automático."""
    veto = _veto_borde(body)
    if veto:
        return None, veto
    try:
        texto, _uso = stream_chat(CHAT_URL, body, {"Authorization": "Bearer " + key,
                                                   "Content-Type": "application/json"},
                                  timeout=espera_s)
        return texto, None
    except urllib.error.HTTPError as e:
        return None, f"NVIDIA API error {e.code}: {e.read().decode()[:600]}"
    except Exception as e:
        return None, f"Error de red: {e}"


def post(url, key, body):
    # 🔴 BORDE en el TRANSPORTE (defensa-en-profundidad, F1 17-jul-26): este es el CUELLO por el que
    # TODO egress de contenido a NVIDIA pasa. El CLI (guard_cli) y carril_gratis (borde.permitido) ya
    # gatean antes; esto lo hace NO-bypassable: un futuro llamador que importe nvidia.post y olvide el
    # borde NO puede filtrar clinico/PII. Se usa borde.clasificar (chequeo PURO, sin sellar el ledger
    # ni disparar canario — eso lo hace la capa de arriba; aqui evitamos doble-sellado). NVIDIA =
    # tercero NO confiable. Fail-closed: si borde no carga, NO se envia.
    veto = _veto_borde(body)
    if veto:
        return None, veto
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    try:
        fin = time.monotonic() + TOPE_TOTAL_S

        def _intento():
            # Cada intento solo puede gastar lo que QUEDA del tope total: sin esto, un modelo
            # saturado colgaba el carril gratis (automático) hasta ~15 min. 22-sep-26.
            queda = fin - time.monotonic()
            if queda < 5:
                raise TimeoutError("tope total de %d s agotado" % TOPE_TOTAL_S)
            return urllib.request.urlopen(req, timeout=min(180, queda))

        # El timeout de urlopen es POR OPERACIÓN de socket: si llegan las cabeceras y el cuerpo
        # gotea, cada lectura reinicia el reloj y la llamada podría colgarse igual. Así el tope
        # cubre también la lectura: la llamada entera va en un hilo y se espera como mucho
        # TOPE_TOTAL_S. (Ojo, 22-sep-26: los «cuelgues» de >5 min que se vieron ese día NO eran
        # esto; eran el CLI leyendo un stdin que no se cerraba. Ver _net.stdin_canalizado.)
        res = {}

        def _todo():
            try:
                res["data"] = json.load(with_retries(_intento, deadline=fin))
            except Exception as e:          # se relanza fuera, para los manejadores de abajo
                res["err"] = e
        hilo = threading.Thread(target=_todo, daemon=True)
        hilo.start()
        hilo.join(max(0.0, fin - time.monotonic()) + 1)
        if hilo.is_alive():
            return None, ("Error de red: tope total de %d s agotado; NVIDIA no terminó de "
                          "responder" % TOPE_TOTAL_S)
        if "err" in res:
            raise res["err"]
        return res["data"], None
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
    piped = stdin_canalizado(1.0 if q else 10.0).strip()   # texto canalizado → se añade
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

    if not want_json:
        texto, err = chat_stream(key, {"model": model, "messages": messages})
        if err:
            print(err); return
        print(texto if texto.strip() else "(respuesta vacía)")
        return
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
