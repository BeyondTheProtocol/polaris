#!/usr/bin/env python3
"""Cliente de Gemini (Google) por API — razonamiento ingeniero de apoyo. Sin pip.

APOYO A LA DECISIÓN, NO consejo médico. Pensado para razonar sobre EVIDENCIA y
TERMINOLOGÍA MOLECULAR GENÉRICA (gen/variante/fármaco/lesión descrita), NUNCA con
PII (nombre, DNI, fechas, email, teléfono, imágenes/PET crudos). El guardarraíl de
abajo BLOQUEA el envío si detecta PII (defensa en profundidad del muro).

Deep Think (gemini-*-deep-think) hoy es early-access por formulario; por defecto se
usa el Pro accesible por API. Cambia el modelo con --model o en el secrets.

Setup (1 vez):
  1. aistudio.google.com → Get API key (de pago por uso). Guárdala en el Llavero:
       security add-generic-password -U -a "$USER" -s btp-gemini-api -w
  2. (opcional) tools/.gemini_secrets.json: {"api_key":"...", "model":"gemini-3-pro"}

Uso:
  python3 tools/gemini.py "pregunta de razonamiento (terminología genérica)"
  python3 tools/gemini.py --model gemini-3-pro "..."
  python3 tools/gemini.py --models           # lista los modelos disponibles para tu key
"""
import json, os, re, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret
from _net import with_retries

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gemini_secrets.json")
BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-pro"
# Bloqueo opcional de términos extra (su nombre, "{{CONTACTO}}", etc.) — gitignored, no versionado.
PII_BLOCKLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pii_blocklist.json")

# Patrones de PII que NUNCA deben salir a una API pública.
PII_PATTERNS = [
    (r"\b\d{8}[A-Za-z]\b", "DNI"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "email"),
    (r"\b(?:\+34|0034)?[6-9]\d{8}\b", "teléfono"),
    (r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "fecha"),
    (r"\b\d{4}-\d{2}-\d{2}\b", "fecha ISO"),
]


def assert_no_pii(text):
    """Lanza ValueError si el texto contiene PII (fail-closed: no enviar)."""
    for pat, label in PII_PATTERNS:
        if re.search(pat, text):
            raise ValueError(f"MURO: el texto contiene posible {label} → no lo envío a la API pública. "
                             f"Usa solo terminología genérica (gen/variante/lesión), sin PII.")
    extra = []
    if os.path.exists(PII_BLOCKLIST):
        try:
            extra = json.load(open(PII_BLOCKLIST)).get("terms", [])
        except Exception as _e:
            # MURO: si la blocklist no carga, los términos extra NO se comprueban (los
            # PII_PATTERNS sí siguen). Esa degradación silenciosa hay que registrarla, no tragarla.
            try:
                import errores as _err
                _err.registrar("gemini", _e, _err.CONFIG, job="pii_blocklist", escalar=True)
            except Exception:
                pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso
    low = text.lower()
    for t in extra:
        if t and t.lower() in low:
            raise ValueError("MURO: el texto contiene un término protegido → no lo envío. Anonimízalo.")


def key():
    k = get_secret("btp-gemini-api", SECRETS, "api_key")
    if not k:
        print("Falta la clave de Gemini: Llavero (btp-gemini-api) o tools/.gemini_secrets.json.")
        sys.exit(2)
    return k


def req(path, data=None):
    url = f"{BASE}/{path}"
    headers = {"x-goog-api-key": key(), "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers, method="POST" if data else "GET")
    return json.load(with_retries(lambda: urllib.request.urlopen(r, timeout=180)))


def main():
    args = sys.argv[1:]
    if "--models" in args:
        try:
            data = req("models")
        except urllib.error.HTTPError as e:
            print(f"Gemini API error {e.code}: {e.read().decode()[:400]}"); return
        for m in data.get("models", []):
            print(" ", m.get("name", "").replace("models/", ""))
        return
    model = DEFAULT_MODEL
    if "--model" in args:
        i = args.index("--model"); model = args[i + 1]; args = args[:i] + args[i + 2:]
    elif os.path.exists(SECRETS):
        try: model = (json.load(open(SECRETS)) or {}).get("model", DEFAULT_MODEL)
        except Exception as _e:
            try:
                import errores as _err
                _err.registrar("gemini", _e, _err.TRANSITORIO, job="secrets", escalar=True)
            except Exception:
                pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso
    q = " ".join(args).strip()
    if not q:
        print('Uso: python3 tools/gemini.py "tu pregunta (terminología genérica, sin PII)"'); return
    # 🔴 BORDE no-bypassable (fuente única del muro). Gemini = API pública NO confiable —
    # "{{CONTACTO}}" es el relay HUMANO cleared, no este endpoint. Sustituye al assert_no_pii local.
    import borde
    if not borde.guard_cli(q, "gemini"):
        sys.exit(2)
    body = {"contents": [{"parts": [{"text": q}]}]}
    try:
        resp = req(f"models/{model}:generateContent", body)
    except urllib.error.HTTPError as e:
        print(f"Gemini API error {e.code}: {e.read().decode()[:500]}"); return
    except Exception as e:
        print("Error:", e); return
    try:                                   # ledger de gasto (best-effort)
        import gasto
        _u = (resp or {}).get("usageMetadata") or {}
        gasto.registrar("gemini", model,
                        _u.get("promptTokenCount") or 0,
                        _u.get("candidatesTokenCount") or 0)
    except Exception:
        pass
    try:
        print(resp["candidates"][0]["content"]["parts"][0]["text"])
    except (KeyError, IndexError, TypeError):
        print(json.dumps(resp, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
