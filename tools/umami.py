#!/usr/bin/env python3
"""Lee métricas de Umami Cloud (helptitular.com) sin navegador. Usa curl porque
api.umami.is está tras Cloudflare y bloquea a urllib (Error 1010). Sin pip.
La API key vive en el Llavero de macOS (servicio btp-umami-api); como red,
tools/.umami_secrets.json — yo nunca la imprimo.

Setup: Umami Cloud → perfil → Settings → API keys → Create. Guárdala en el
Llavero (security add-generic-password -s btp-umami-api -a <usuario> -w <key> -U)
o en tools/.umami_secrets.json:  {"api_key": "..."}

Forma de la API (api.umami.is/v1), verificada 2026-06-20:
  GET /websites/{id}/stats     -> dict PLANO: {"pageviews":N,"visitors":N,
       "visits":N,"bounces":N,"totaltime":N,"comparison":{...}}
  GET /websites/{id}/metrics   -> lista: [{"x":"<nombre>","y":<conteo>}, ...]
       (type=referrer | event | url ...)
  En error devuelve {"error":{"message":...,"code":...,"status":...}}.

Uso:
  python3 tools/umami.py            # últimos 7 días
  python3 tools/umami.py 1          # último día (parte del lanzamiento)
"""
import os, sys, json, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secrets import get as get_secret

SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".umami_secrets.json")
BASE = "https://api.umami.is/v1"
DEFAULT_WID = "30a40c53-5573-45c0-8ac9-8f0f94621ecf"  # helptitular.com

def api(path, key):
    """Llama a la API y devuelve el JSON parseado. Lanza RuntimeError (con un
    mensaje legible) ante fallo de red, respuesta no-JSON o error de la API."""
    p = subprocess.run(
        ["curl", "-s", "--max-time", "30", BASE + path,
         "-H", "accept: application/json", "-H", "x-umami-api-key: " + key],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("curl error: " + (p.stderr or "")[:120])
    raw = (p.stdout or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        raise RuntimeError("respuesta no-JSON: " + raw[:160])
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        err = data["error"]
        msg = err.get("message") or err.get("code") or "error desconocido"
        st = err.get("status")
        raise RuntimeError("API: %s%s" % (msg, " (%s)" % st if st else ""))
    return data

def stat_num(stats, key):
    """Valor numérico de stats, tolerando forma plana (515) o anidada ({"value":515})."""
    if not isinstance(stats, dict):
        return None
    x = stats.get(key)
    if isinstance(x, dict):
        x = x.get("value")
    return x

def fmt_metrics(rows, top=None):
    """rows: lista de {"x":nombre,"y":conteo}. -> 'a=1 · b=2' o '(sin datos)'."""
    if rows is None:
        return "(sin datos)"
    if not isinstance(rows, list):
        return "(forma inesperada: %s)" % type(rows).__name__
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("x")
        if name is None or name == "":
            name = "(directo)"
        out.append("%s=%s" % (name, r.get("y")))
        if top and len(out) >= top:
            break
    return " · ".join(out) if out else "(sin datos)"

def _wid_de_secrets(wid=None):
    if wid:
        return wid
    if os.path.exists(SECRETS):
        try:
            return (json.load(open(SECRETS)) or {}).get("website_id", DEFAULT_WID)
        except Exception as _e:
            # Mismo parse que main(): un secrets corrupto manda analytics al WID por defecto.
            # Se registra (no se traga) y se cae al fallback, sin cambiar el control de flujo.
            try:
                import errores as _err
                _err.registrar("umami", _e, _err.CONFIG, job="secrets", escalar=True)
            except Exception:
                pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso
    return DEFAULT_WID


def serie_diaria(days=28, key=None, wid=None):
    """Serie temporal DIARIA (para proyectar el ritmo, no solo el total).
    -> [{'fecha':'YYYY-MM-DD','vistas':N,'visitas':N}, ...]; [] si no hay clave/datos.
    Endpoint /pageviews con unit=day → {'pageviews':[{x,y}],'sessions':[{x,y}]}."""
    if key is None:
        key = get_secret("btp-umami-api", SECRETS, "api_key")
    if not key or str(key).startswith("PEGA"):
        return []
    wid = _wid_de_secrets(wid)
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    q = "startAt=%d&endAt=%d&unit=day&timezone=Europe/Madrid" % (start, end)
    try:
        data = api("/websites/%s/pageviews?%s" % (wid, q), key)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pv = {r.get("x"): r.get("y") for r in (data.get("pageviews") or []) if isinstance(r, dict)}
    sv = {r.get("x"): r.get("y") for r in (data.get("sessions") or []) if isinstance(r, dict)}
    out = []
    for x in sorted(set(pv) | set(sv)):
        out.append({"fecha": str(x)[:10], "vistas": pv.get(x) or 0, "visitas": sv.get(x) or 0})
    return out


def main():
    key = get_secret("btp-umami-api", SECRETS, "api_key")
    wid = DEFAULT_WID
    if os.path.exists(SECRETS):
        try: wid = (json.load(open(SECRETS)) or {}).get("website_id", DEFAULT_WID)
        except Exception as _e:
            try:
                import errores as _err
                _err.registrar("umami", _e, _err.CONFIG, job="secrets", escalar=True)
            except Exception:
                pass  # benigno: el bus de errores ya es fail-safe; este es el blindaje del propio aviso
    if not key or str(key).startswith("PEGA"):
        print('Falta la clave de Umami: Llavero (btp-umami-api) o tools/.umami_secrets.json.'); return
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    end = int(time.time() * 1000); start = end - days * 86400 * 1000
    q = "startAt=%d&endAt=%d" % (start, end)
    try:
        stats = api("/websites/%s/stats?%s" % (wid, q), key)
        refs = api("/websites/%s/metrics?%s&type=referrer&limit=8" % (wid, q), key)
        evs = api("/websites/%s/metrics?%s&type=event&limit=10" % (wid, q), key)
        pages = api("/websites/%s/metrics?%s&type=url&limit=8" % (wid, q), key)
    except Exception as e:
        print("Error:", e)
        if "API:" in str(e):
            print("  Pista: si dice 'Invalid API key', corrige la clave en el Llavero")
            print("  (servicio btp-umami-api) o en tools/.umami_secrets.json.")
        return
    show = lambda n: "—" if n is None else n
    print("=== Umami · helptitular.com · últimos %d días ===" % days)
    print("Visitantes %s · Visitas %s · Vistas %s · Rebotes %s" % (
        show(stat_num(stats, "visitors")), show(stat_num(stats, "visits")),
        show(stat_num(stats, "pageviews")), show(stat_num(stats, "bounces"))))
    print("Fuentes:", fmt_metrics(refs))
    print("Eventos:", fmt_metrics(evs))
    print("Páginas:", fmt_metrics(pages))

if __name__ == "__main__":
    main()
