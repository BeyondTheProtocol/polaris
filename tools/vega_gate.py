#!/usr/bin/env python3
"""tools/vega_gate.py — gate DETERMINISTA (gratis, 0 tokens) del ciclo de vigilancia de Vega.

Por qué existe (frugalidad sin perder fiabilidad, 23/6/26):
  El monitor `com.btp.correo-urgente` arranca al agente `asistente` (Sonnet) ~cada 15 min
  HAYA O NO algo nuevo. Eso se come el presupuesto del día (≈$2,3/día solo de mirar el vacío).
  Pero MIRAR es gratis y PENSAR (LLM) cuesta. Este gate hace la mirada GRATIS: decide si hay
  NOVEDAD real desde la última vez. Si NO la hay → el ciclo termina sin gastar un céntimo. Si
  la hay → deja vía libre al LLM, pasándole SOLO el delta (qué cambió), no todo otra vez.

  El "pensar" se gatea; el "mirar" sigue cada 15 min (es gratis ahora). La pasada COMPLETA
  garantizada 1×/día (sección de HOY, `com.btp.asistente` 07:55) NO pasa por este gate: esa
  corre siempre.

Filosofía FAIL-SAFE de fiabilidad (el muro de la confiabilidad manda sobre el ahorro):
  · Ante CUALQUIER duda → "hay novedad" (rc 0): jamás dejar caer un hilo crítico para NED
    (plazo de biopsia, respuesta de médico, firma de MTA) por ahorrar dinero.
  · Si una fuente de señal falta, es ilegible o el marcador está corrupto → se trata como
    novedad (corre el LLM). El ahorro es OPORTUNISTA: solo cuando estamos SEGUROS de que no
    cambió nada.
  · Si NO existe NINGUNA fuente de señal fiable (p. ej. correo_imap/buzon.json aún no está en
    esta máquina) → por defecto NO se gatea (rc 0 siempre), salvo que se pida lo contrario.

Muro / privacidad:
  · 0 tokens, 0 red, 0 egress: solo mira mtime/tamaño/cuentas de ficheros locales y un hash
    de IDs (de buzon.json, si existe). NUNCA lee cuerpos de correo ni manda nada a ninguna IA.
  · No persiste PII: el marcador guarda hashes y contadores, no contenido.

Señales que mira (todas LOCALES y GRATIS), de más a menos directa:
  1. buzon.json (lo escribe tools/correo_imap.py, IMAP RO): nº y hash de los IDs de mensaje
     → detecta correo NUEVO sin abrir Gmail ni gastar. (Si el fichero no existe, se omite.)
  2. seguimiento.json: mtime + hash → un hilo/tarea nuevo o que cambió de estado.
  3. _PRIVADO_WHATSAPP/ : mtime de la carpeta + del fichero más reciente → notas/mensajes
     nuevos que wa_tracker volcó.
  4. BANDEJA.md / captura verbal: mtime → algo que {{TITULAR}} soltó a mano.
  5. (umbral de plazos) seguimiento.py revisar: si hay un ítem cuya fecha CRUZA hoy/mañana
     desde la última pasada → novedad aunque ningún fichero haya cambiado (un plazo que se
     calienta solo con el paso del tiempo).

Uso:
  python3 tools/vega_gate.py check        # rc 0 = hay novedad (corre LLM); rc 1 = nada nuevo
  python3 tools/vega_gate.py check --json # igual + explica en JSON por qué
  python3 tools/vega_gate.py delta        # texto del DELTA para inyectar al prompt del LLM
  python3 tools/vega_gate.py mark         # sella "último visto" (lo hace el LLM tras correr)
  python3 tools/vega_gate.py status       # qué fuentes ve, sin tocar el marcador

Flags:
  --require-source   si no hay NINGUNA fuente fiable, gatea igual (rc 1). Por defecto NO se
                     usa: sin fuentes, fail-safe = correr el LLM.

Sin dependencias (stdlib). Espejo del estilo de cost_guard/seguimiento (BTP_STATE_DIR aísla).
"""
import hashlib
import json
import os
import sys
from datetime import datetime, date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
FUENTE = os.environ.get("BTP_FUENTE_DIR") or os.path.join(REPO, "00_FUENTE-DE-VERDAD")

# Marcador "último visto" del gate (hashes + contadores, NUNCA contenido/PII).
MARK = os.path.join(STATE, "vega_gate", "last_seen.json")

# Fuentes de señal (todas locales). Las que no existan se OMITEN (fail-soft).
BUZON = os.path.join(STATE, "correo", "buzon.json")            # tools/correo_imap.py (IMAP RO)
SEGUI = os.path.join(STATE, "seguimiento.json")
WHATSAPP_DIR = os.path.join(FUENTE, "_PRIVADO_WHATSAPP")
BANDEJA = os.environ.get("BTP_BANDEJA") or os.path.join(REPO, "BANDEJA.md")

# Punto ciego tapado (11-jul-2026): BUZON de arriba solo es la cuenta por defecto (titular.mgp@).
# Desde que correo_imap.py también vigila titular@ (once_todas/`todas`), su buzón vive en un
# fichero PROPIO (path_por_cuenta, para no pisar el de la cuenta por defecto) — sin sumarlo aquí,
# un correo NUEVO solo en titular@ no cambiaba NINGUNA firma y el gate seguía diciendo "sin
# novedad". Reusa la MISMA función de slug que correo_imap.py (DRY: un solo sitio calcula el
# nombre de fichero por cuenta). Fail-soft TOTAL: si correo_imap no está importable por lo que
# sea, cae a la ruta calculada a mano (mismo slug) — nunca rompe el gate.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import correo_imap as _ci
    BUZON_TITULAR = _ci.path_por_cuenta("titular@gmail.com")[1]
except Exception:
    BUZON_TITULAR = os.path.join(STATE, "correo", "buzon-titular-gmail-com.json")


# ── utilidades deterministas ────────────────────────────────────────────────────
def _sha(s):
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:16]


def _stat_sig(path):
    """Firma barata de un fichero: (mtime_ns, size). None si no existe.
    Si existe pero no se puede leer su stat → 'ilegible' (= tratar como novedad)."""
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except FileNotFoundError:
        return None
    except Exception:
        return "ilegible"


def _dir_sig(path):
    """Firma de un directorio: (mtime del dir, mtime del fichero más nuevo, nº de ficheros).
    Captura tanto 'llegó un fichero' como 'cambió uno existente'. None si no existe."""
    if not os.path.isdir(path):
        return None
    try:
        newest = 0
        n = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                n += 1
                try:
                    m = os.stat(os.path.join(root, f)).st_mtime_ns
                    if m > newest:
                        newest = m
                except Exception:
                    return "ilegible"
        return [os.stat(path).st_mtime_ns, newest, n]
    except Exception:
        return "ilegible"


def _buzon_sig(path):
    """Firma del buzón IMAP: nº de mensajes + hash de sus IDs (orden-independiente).
    Así un correo NUEVO cambia la firma sin que tengamos que leer su cuerpo.
    Tolerante al esquema: busca una lista de mensajes y un id por mensaje con varios nombres."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return "ilegible"          # existe pero corrupto → novedad (fail-safe)
    # Localiza la lista de mensajes bajo claves plausibles; si no, usa el propio doc.
    msgs = None
    if isinstance(d, list):
        msgs = d
    elif isinstance(d, dict):
        for k in ("mensajes", "messages", "items", "correos", "buzon", "threads"):
            if isinstance(d.get(k), list):
                msgs = d[k]
                break
    if msgs is None:
        # esquema desconocido pero legible → firma del documento entero (cae al lado seguro:
        # cualquier cambio dispara, ningún cambio no dispara).
        return ["raw", _sha(json.dumps(d, sort_keys=True, ensure_ascii=False))]
    ids = []
    for m in msgs:
        if isinstance(m, dict):
            mid = (m.get("id") or m.get("uid") or m.get("message_id")
                   or m.get("messageId") or m.get("thread_id") or m.get("msgid"))
            ids.append(str(mid) if mid is not None else _sha(json.dumps(m, sort_keys=True)))
        else:
            ids.append(str(m))
    return [len(ids), _sha("\n".join(sorted(ids)))]


def _plazos_umbral():
    """Marca de tiempo de plazos calientes: ¿hay algún ítem datado para HOY o MAÑANA?
    Determinista (reusa seguimiento.recopilar, gratis). Si cambia respecto a la última
    pasada (un plazo entró en la ventana hoy/mañana) → novedad, aunque ningún fichero
    se haya tocado. Fail-soft: si no se puede calcular, devuelve None (no gatea por aquí)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import seguimiento as sg
        data = sg.recopilar()
    except Exception:
        return None
    hoy = date.today()
    manana = hoy + timedelta(days=1)
    calientes = []
    for it in data.get("items", []):
        if str(it.get("estado", "")).lower() == "hecho":
            continue
        for campo in ("vence", "fecha", "cuando", "deadline", "due"):
            v = it.get(campo)
            if not v:
                continue
            try:
                fv = sg._parse_iso(str(v)[:10]) if hasattr(sg, "_parse_iso") else None
            except Exception:
                fv = None
            if fv is None:
                try:
                    fv = datetime.fromisoformat(str(v)[:10]).date()
                except Exception:
                    fv = None
            if fv is not None and fv <= manana:
                key = str(it.get("id") or it.get("titulo") or "")[:40]
                calientes.append(key + "@" + fv.isoformat())
            break
    return [len(calientes), _sha("\n".join(sorted(calientes)))]


# ── estado del gate (marcador "último visto") ───────────────────────────────────
def _signals():
    """Reúne la firma actual de TODAS las fuentes. Devuelve (firmas, fuentes_vivas)."""
    sig = {
        "buzon": _buzon_sig(BUZON),
        "buzon_titular": _buzon_sig(BUZON_TITULAR),
        "seguimiento": _stat_sig(SEGUI),
        "whatsapp": _dir_sig(WHATSAPP_DIR),
        "bandeja": _stat_sig(BANDEJA),
        "plazos": _plazos_umbral(),
    }
    # Acotar por consumidor (BTP_GATE_SOURCES): un daemon solo debe "ver novedad" en SUS fuentes.
    # Ej.: com.btp.correo-urgente → "buzon" (correo nuevo real), para que el RUIDO de seguimiento.json
    # —que reescriben otros daemons cada pocos minutos— NO lo encienda 24/7. Lista separada por contacto;
    # vacío/ausente = todas (comportamiento de siempre). Claves desconocidas se ignoran (no rompe).
    # Coherente check↔mark: el mismo daemon usa la misma lista, así que compara y sella el mismo set.
    # OJO (gate de {{TITULAR}}, TODO pendiente de encendido): com.btp.correo-urgente fija hoy
    # BTP_GATE_SOURCES=buzon a secas — para que TAMBIÉN despierte con correo nuevo en
    # titular@, ese plist necesita sumar buzon_titular a la lista (edición de plist +
    # `launchctl load`, fuera del alcance de este cambio: no se toca sin su OK). Mientras tanto,
    # la pasada COMPLETA diaria (sin BTP_GATE_SOURCES, ve TODAS las fuentes) ya sí lo detecta.
    only = os.environ.get("BTP_GATE_SOURCES", "").strip()
    if only:
        keep = {s.strip() for s in only.split(",") if s.strip()}
        sig = {k: v for k, v in sig.items() if k in keep}
    # Una fuente está VIVA si aporta señal real (no None). 'ilegible' cuenta como viva-y-cambió.
    vivas = [k for k, v in sig.items() if v is not None]
    return sig, vivas


def _load_mark():
    try:
        with open(MARK, encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return None
    except Exception:
        return "corrupto"


def _write_mark(sig):
    os.makedirs(os.path.dirname(MARK), mode=0o700, exist_ok=True)
    payload = {"ts": datetime.now().isoformat(timespec="seconds"), "sig": sig}
    tmp = MARK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MARK)


# ── decisión ────────────────────────────────────────────────────────────────────
def evaluar(require_source=False):
    """Decide si hay NOVEDAD. Devuelve dict con la decisión y el porqué.
       novedad=True  → corre el LLM (rc 0)
       novedad=False → nada nuevo, no gastes (rc 1)
    """
    sig, vivas = _signals()
    mark = _load_mark()

    # 1. Sin marcador previo (primera vez) o marcador corrupto → novedad (fail-safe).
    if mark is None:
        return {"novedad": True, "motivo": "primera_pasada",
                "detalle": "no hay marcador previo → corro para fijar la línea base", "sig": sig}
    if mark == "corrupto":
        return {"novedad": True, "motivo": "marcador_corrupto",
                "detalle": "el marcador 'último visto' no se pudo leer → corro por seguridad",
                "sig": sig}

    # 2. ¿Hay alguna fuente fiable? Si NO → por defecto correr (no gateamos a ciegas).
    if not vivas:
        if require_source:
            return {"novedad": False, "motivo": "sin_fuentes_require",
                    "detalle": "ninguna fuente de señal disponible y --require-source activo → no gasto",
                    "sig": sig}
        return {"novedad": True, "motivo": "sin_fuentes",
                "detalle": "ninguna fuente de señal local disponible (¿falta correo_imap/buzon.json?) "
                           "→ fail-safe: corro el LLM", "sig": sig}

    prev = mark.get("sig", {})
    cambios = []
    for k, v in sig.items():
        pv = prev.get(k)
        if v == "ilegible":
            cambios.append(k + "(ilegible→corro)")
            continue
        if v is None:
            # fuente desapareció: si antes había señal y ahora no, lo tratamos como cambio
            # (algo se movió). Si nunca la hubo, no cuenta.
            if pv not in (None,):
                cambios.append(k + "(desapareció)")
            continue
        if pv != v:
            cambios.append(k)

    if cambios:
        return {"novedad": True, "motivo": "cambio", "detalle": "cambió: " + ", ".join(cambios),
                "fuentes_vivas": vivas, "sig": sig}
    return {"novedad": False, "motivo": "sin_cambios",
            "detalle": "ninguna fuente cambió desde la última pasada", "fuentes_vivas": vivas,
            "sig": sig}


def construir_delta():
    """Texto corto del DELTA para inyectar al prompt del LLM: SOLO qué cambió, no todo otra
    vez. Sin PII (nombres de fuente y contadores, no contenido)."""
    res = evaluar()
    if not res["novedad"]:
        return "(sin novedad)"
    lineas = ["DELTA desde tu última pasada (mira SOLO esto, no repases todo):", "· " + res["detalle"]]
    sig = res.get("sig", {})
    b = sig.get("buzon")
    if isinstance(b, list) and len(b) == 2 and isinstance(b[0], int):
        lineas.append("· buzón: %d mensajes ahora mismo." % b[0])
    p = sig.get("plazos")
    if isinstance(p, list) and p and isinstance(p[0], int) and p[0]:
        lineas.append("· %d plazo(s) con fecha hoy/mañana sin cerrar → revísalos primero." % p[0])
    return "\n".join(lineas)


# ── CLI ──────────────────────────────────────────────────────────────────────────
def main(argv):
    cmd = argv[0] if argv else "check"
    as_json = "--json" in argv
    require_source = "--require-source" in argv

    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0

    if cmd == "check":
        res = evaluar(require_source=require_source)
        if as_json:
            # no volcamos 'sig' crudo en --json salvo petición (ruido); resumen claro.
            out = {k: v for k, v in res.items() if k != "sig"}
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(("NOVEDAD: " if res["novedad"] else "sin novedad: ") + res["detalle"])
        # rc 0 = corre el LLM ; rc 1 = no hay nada nuevo (ahorro). Convención apta para `&&`.
        return 0 if res["novedad"] else 1

    if cmd == "delta":
        print(construir_delta())
        return 0

    if cmd == "mark":
        sig, _vivas = _signals()
        _write_mark(sig)
        if as_json:
            print(json.dumps({"ok": True, "ts": datetime.now().isoformat(timespec="seconds")}))
        else:
            print("marcador 'último visto' actualizado")
        return 0

    if cmd == "status":
        sig, vivas = _signals()
        mark = _load_mark()
        info = {
            "fuentes_vivas": vivas,
            "marcador": ("ausente" if mark is None else "corrupto" if mark == "corrupto"
                         else mark.get("ts")),
            "firmas": sig,
        }
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print("uso: vega_gate.py [check [--json] [--require-source] | delta | mark | status]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
