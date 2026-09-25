#!/usr/bin/env python3
"""tools/observatorio.py — El Observatorio: sala de control viva de Polaris.

Una sola vista, viva y SOLO-LECTURA, de cómo está Polaris por dentro:
  · cuánto falta por ajustar (termómetro polaris_estado)
  · cuánto gasta (cost_guard + coste: tokens/€)
  · qué rutinas 24/7 están vivas (launchctl + plists)
  · qué cajas de la constelación funcionan (audit_constelacion)
  · qué agentes/comités hay y si están registrados (audit_comites)
  · qué hilos se están cayendo (seguimiento, impacto-NED)
  · salud del lazo (healthcheck) y actividad reciente (PANEL-LAZO)
  · borradores esperando tu OK (outbox/pending)

Filosofía (la constelación): es DATOS, no un proceso nuevo con poderes. No edita ninguna tool:
REUSA sus interfaces (importa funciones puras de solo-lectura o lee sus ficheros de estado).
Sin dependencias (stdlib). Sin LLM → ~0 tokens (no quema el recurso que vigila).

Muro:
  · Solo-lectura para TODO lo del sistema: NO envía, NO paga, NO publica, NO contacta.
  · EXCEPCIÓN acotada (El Tablero): crear/mover una tarea SOLO edita la lista LOCAL única de Vega
    (tools/seguimiento.py → state/seguimiento.json, vía crear_tarea/set_estado). NUNCA dispara nada
    hacia fuera: mover una tarjeta a "hecho" no envía/publica/contacta — el envío real sigue pasando
    por el gate de salida (outbox + firma de {{TITULAR}}). Los POST exigen un token de sesión incrustado
    en la página (defensa anti-escritura-fortuita desde otra web abierta en su navegador).
  · EXCEPCIÓN acotada (Síntomas, /sintomas): apuntar un síntoma SOLO escribe en el diario LOCAL
    (tools/sintomas.py → _PRIVADO_CLINICO/, gitignored). Registro de APOYO, no consejo médico; no
    interpreta ni avisa hacia fuera. Mismo guardia de token que el Tablero.
  · Lo único que sale es el `parte` diario al PROPIO Telegram de {{TITULAR}} (REPORT a sí misma; respeta HALT).
  · Privacidad por bind: escucha SOLO en 127.0.0.1 (jamás 0.0.0.0). Al móvil se llega por el relay
    privado de Tailscale: `tailscale serve` 9090 → 127.0.0.1:8787 (http://polaris.taild7f51c.ts.net:9090).
  · Muestra meta/títulos; nunca datos clínicos/genómicos CRUDOS ni PII (encargos privados → enmascarados).

Uso:
  python3 tools/observatorio.py                 # arranca el servidor (127.0.0.1:8787)
  python3 tools/observatorio.py once            # imprime el JSON de estado y sale (debug)
  python3 tools/observatorio.py parte [--send] [--dry]   # parte diario a Telegram (sustituye el ping 8:18)
  python3 tools/observatorio.py abrir           # abre Safari a pantalla completa (kiosko en Polaris)
"""
import glob
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")  # casa base SIEMPRE: el estado vivo y la fuente de verdad viven ahí, nunca en un worktree
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)  # para importar las tools hermanas sin depender del cwd
import portguard  # noqa: E402 — arranque limpio de puertos (libera huérfanos propios)

# Token de sesión (por proceso): los POST del Tablero lo exigen → ninguna web ajena puede
# escribir en localhost sin leerlo primero (y la same-origin policy se lo impide).
TOKEN = secrets.token_urlsafe(24)
# Id de versión (por proceso): cambia en cada arranque del servidor. Las pantallas abiertas
# (kiosko 4K, móvil, portátil) lo comparan y se RECARGAN solas si difiere → "enciéndelo y
# olvídate": al actualizar el código y recargar el servidor, todas se refrescan sin tocar nada.
BUILD = secrets.token_hex(4)

# --- red: SIEMPRE privado. El host es loopback fijo; jamás se expone a la red local/internet. ---
HOST = "127.0.0.1"
# Puerto fijo 8787 (kiosko 24/7). BTP_OBS_PORT permite arrancar una instancia EFÍMERA en otro
# puerto (preview/test) sin pisar el servidor vivo; sigue siendo loopback (el muro de _host_es_privado
# manda igual). No cambia el despliegue real.
PORT = int(os.environ.get("BTP_OBS_PORT") or 8787)
TS_IP = "100.114.113.73"   # Polaris en Tailscale (se sigue aceptando como Origin propio)
# Al móvil se llega por `tailscale serve --http=9090 http://127.0.0.1:8787` (tailnet only, sin
# Funnel). Enruta por NOMBRE: por IP a :9090 da 404, así que el enlace lleva el nombre MagicDNS.
# Sustituye al relay Python 8788 (S14): un proceso menos escuchando fuera de loopback.
# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026
TS_HOST = "polaris.taild7f51c.ts.net"
TS_HOST_CORTO = "polaris"
MOVIL_PORT = 9090

STATE = os.path.join(TOOLS, "state")
PANEL = os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "Gestion", "PANEL-LAZO.md")
AGENTS_DIR = os.path.join(ROOT, ".claude", "agents")


def _host_es_privado(h=None):
    """Guardia del muro: solo loopback. Bindear a 0.0.0.0 expondría datos → prohibido."""
    return (h or HOST) in ("127.0.0.1", "::1", "localhost")


# ───────────────────────── recolección (cada fuente AISLADA) ─────────────────────────
def _safe(fn):
    """Llama a fn() y nunca propaga: si falla, devuelve {'_error': ...} y el resto sigue vivo."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — a propósito: una fuente caída no tumba el panel
        return {"_error": "%s: %s" % (type(e).__name__, e)}


# Pendientes técnicos → frase humana (regla: lo que ve {{TITULAR}}, en español llano, sin jerga).
_PEND_HUMANO = {
    "claves_rotadas": "rotar las claves expuestas",
    "api_nvidia": "sacar la clave de NVIDIA (carril gratis)",
    "api_perplexity": "sacar la clave de Perplexity",
    "api_youtube": "sacar la clave de YouTube",
    "acceso_x_privado": "guardar el acceso a X",
    "acceso_ig": "guardar el acceso a Instagram",
    "google_service_account": "la cuenta de servicio de Google",
    "backup_usb": "enchufar/preparar el backup en USB",
    "energia_sudo": "dar permiso de energía (sudo)",
    "backup_clave_externa": "guardar la clave del backup FUERA de Polaris",
    "stitch_mcp_ok": "arreglar el MCP de Stitch",
    "muro_parche_commit": "commitear el parche del muro",
    "cajas_commit": "commitear las cajas",
}

# Rutinas técnicas → nombre humano (lo que ve {{TITULAR}}). Las que no estén aquí se muestran prettificadas.
_RUTINA_HUMANO = {
    "com.btp.hoy-compose": "Tu HOY (compone el resumen)",
    "com.btp.enviar-hoy": "Tu HOY (te lo envía, 8:12)",
    "com.btp.polaris-estado": "Termómetro de Polaris (8:18)",
    "com.btp.observatorio": "El Observatorio (servidor)",
    "com.btp.observatorio-kiosk": "El Observatorio (pantalla completa)",
    "com.btp.observatorio-parte": "El Observatorio (parte diario, 8:18)",
    "com.btp.auto-mejora": "Auto-mejora diaria",
    "com.btp.git-barrido": "Barrido de git (higiene del repo, 5:40)",
    "com.btp.prensa": "Monitor de prensa",
    "com.btp.wa-tracker": "WhatsApp (lectura local)",
    "com.btp.radar-lit": "Radar de literatura",
    "com.btp.radar-rutas": "Radar de rutas a NED",
    "com.btp.dispatcher": "El lazo (recibe lo que sueltas)",
    "com.btp.bot-telegram": "Telegram (escucha)",
    "com.btp.healthcheck": "Vigía de salud",
    "com.btp.caffeinate": "Anti-dormir (24/7)",
    "com.btp.backup": "Copia de seguridad",
    "com.btp.kb-reindex": "Reindexa la base de conocimiento",
    "com.btp.notif-flush": "Avisos en silencio nocturno",
    "com.btp.calendar-sync": "Calendario",
    "com.btp.correo": "Correo (lectura)",
    "com.btp.correo-urgente": "Correo urgente",
    "com.btp.asistente": "Vega (asistente proactiva)",
    "com.btp.instagram": "Instagram (lectura)",
    "com.btp.x-centinela": "X (centinela)",
    "com.btp.x-dms": "X (mensajes directos)",
    "com.btp.x-dms-watch": "X (vigila contacto clave)",
    "com.btp.x-mentions": "X (menciones)",
    "com.btp.x-radar": "X (radar)",
    "com.btp.preview-web": "Preview web (helptitular)",
}


def _nombre_rutina(label):
    if label in _RUTINA_HUMANO:
        return _RUTINA_HUMANO[label]
    base = label.replace("com.btp.", "").replace("-", " ").replace("_", " ")
    return base[:1].upper() + base[1:]


def estado_config():
    """Termómetro 0-100 + categorías + lo que falta (reusa polaris_estado.compute())."""
    import polaris_estado as pe
    overall, rows, (ld_loaded, ld_total), pend = pe.compute()
    return {
        "overall": overall,
        "categorias": [{"label": l, "pct": p, "peso": w} for (l, p, w) in rows],
        "launchd": {"cargadas": ld_loaded, "total": ld_total},
        "pendientes": [_PEND_HUMANO.get(k, k) for k in pend],
    }


def _toktot(t):
    return (t.get("input", 0) + t.get("output", 0) + t.get("cache_read", 0) + t.get("cache_write", 0))


# Caché del escaneo de transcripts (lo pesado de coste): se calcula en segundo plano, TTL 60 s.
_coste_cache = {"ts": 0.0, "data": None, "computing": False, "lock": threading.Lock()}


def _compute_coste():
    # TODO el repo, no solo casa base: casa base + sus worktrees (`coste.proyectos_del_repo`). El
    # panel medía un directorio de 95 y CLAUDE.md manda aislar cada sesión en un worktree, así que
    # lo que quedaba invisible era precisamente el trabajo de construcción.
    import coste
    proyectos = coste.proyectos_del_repo()
    files, files_base = [], []
    for p in proyectos:
        f = coste.transcripts(p)   # también subagentes y workflows, no solo la sesión principal
        files.extend(f)
        if coste.es_casa_base(p):
            files_base = f
    por_modelo, por_dia, _por_sesion = coste.scan(files)
    tabla = coste.precios()
    dias = sorted(d for d in por_dia if d != "????-??-??")
    # HOY es hoy, no «el último día con datos»: si el lazo lleva dos días callado, `dias[-1]` seguía
    # enseñando el consumo de anteayer bajo el rótulo «hoy». Un cero real dice más que un dato viejo.
    hoy = time.strftime("%Y-%m-%d")
    ventana = dias[-7:]
    modelos = []
    for m, t in sorted(por_modelo.items(), key=lambda kv: -(kv[1]["input"] + kv[1]["output"])):
        pr = coste.price_for(m, tabla)
        modelos.append({
            "modelo": m,
            "tok": _toktot(t),
            "usd": round(coste.usd(t, pr), 2) if pr else None,
        })
    serie = [{"dia": d, "tok": _toktot(por_dia[d])} for d in ventana]
    ledger = coste.read_ledger()
    today = time.strftime("%Y-%m-%d")
    ledger_hoy = sum(float(r.get("usd", 0) or 0) for r in ledger if (r.get("ts") or "")[:10] == today)
    # Desglose casa base / worktrees: sin él, «hoy_tok» sube y no se sabe si fue el lazo o una
    # sesión de construcción. Se calcula sobre los MISMOS ficheros, no re-escaneando.
    _pm_base, pd_base, _ps = coste.scan(files_base) if files_base else ({}, {}, {})
    hoy_base = _toktot(pd_base[hoy]) if hoy in pd_base else 0
    hoy_total = _toktot(por_dia[hoy]) if hoy in por_dia else 0
    return {
        "hoy_tok": hoy_total,
        "hoy_tok_casa_base": hoy_base,
        "hoy_tok_worktrees": hoy_total - hoy_base,
        "proyectos_medidos": len(proyectos),
        "por_modelo": modelos[:8],
        "serie_7d": serie,
        "apis_pago_usd_hoy": round(ledger_hoy, 2),
        "apis_pago_n": len(ledger),
    }


def _coste_async():
    """Devuelve el último cálculo cacheado; si está viejo, dispara uno en segundo plano."""
    now = time.time()
    with _coste_cache["lock"]:
        fresco = _coste_cache["data"] is not None and (now - _coste_cache["ts"] < 60)
        if fresco or _coste_cache["computing"]:
            return _coste_cache["data"] or {"_status": "calculando…"}
        _coste_cache["computing"] = True

    def worker():
        d = _safe(_compute_coste)
        with _coste_cache["lock"]:
            _coste_cache["data"] = d
            _coste_cache["ts"] = time.time()
            _coste_cache["computing"] = False

    threading.Thread(target=worker, daemon=True).start()
    return _coste_cache["data"] or {"_status": "calculando…"}


def estado_gasto(coste_sync=False):
    """Gasto de hoy vs tope + tokens. coste_sync=True calcula los tokens al momento (para el parte)."""
    today = time.strftime("%Y-%m-%d")
    limits = {}
    try:
        with open(os.path.join(STATE, "cost", "limits.json"), encoding="utf-8") as f:
            limits = json.load(f)
    except Exception:
        pass
    hoy = {}
    try:
        with open(os.path.join(STATE, "cost", today + ".json"), encoding="utf-8") as f:
            hoy = json.load(f)
    except Exception:
        pass
    degradado = os.path.exists(os.path.join(STATE, "healthcheck", "degraded.flag"))
    tokens = _compute_coste() if coste_sync else _coste_async()
    # Serie de 7 días + acumulado del mes (SOLO $ real del daemon, de cost_guard).
    import datetime as _dt
    serie7, mes_usd, base = [], 0.0, _dt.date.today()
    for i in range(6, -1, -1):
        d = (base - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
        u = 0.0
        try:
            with open(os.path.join(STATE, "cost", d + ".json"), encoding="utf-8") as f:
                u = float(json.load(f).get("gastado_usd", 0.0))
        except Exception:
            pass
        serie7.append({"dia": d[5:], "usd": round(u, 2)})
    mespre = base.strftime("%Y-%m")
    try:
        for fn in os.listdir(os.path.join(STATE, "cost")):
            if fn.startswith(mespre) and fn.endswith(".json"):
                with open(os.path.join(STATE, "cost", fn), encoding="utf-8") as f:
                    mes_usd += float(json.load(f).get("gastado_usd", 0.0))
    except Exception:
        pass
    return {
        "hoy_usd": round(float(hoy.get("gastado_usd", 0.0)), 2),
        "tope_usd": float(limits.get("tope_diario_usd", 30.0)),
        "n_jobs": hoy.get("n_jobs", 0),
        "conservador": degradado,
        "tokens": tokens,
        "serie7": serie7,
        "mes_usd": round(mes_usd, 2),
        "tope_mes_usd": float(limits.get("tope_mensual_usd", 1500.0)),
    }


def estado_rutinas():
    """Las plists disponibles y cuáles están cargadas/corriendo (launchctl)."""
    plists = sorted(glob.glob(os.path.join(TOOLS, "launchd", "*.plist")))
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        out = ""
    cargadas = {}
    for ln in out.splitlines():
        parts = ln.split("\t")
        label = parts[-1].strip()
        if label.startswith("com.btp."):
            pid = parts[0].strip()
            cargadas[label] = pid not in ("-", "")
    rutinas = []
    for p in plists:
        label = os.path.basename(p)[:-6]  # quita ".plist"
        rutinas.append({
            "nombre": _nombre_rutina(label),
            "label": label,
            "cargada": label in cargadas,
            "corriendo": bool(cargadas.get(label)),
        })
    rutinas.sort(key=lambda r: (not r["cargada"], r["nombre"].lower()))
    return {"total": len(plists), "cargadas": sum(1 for r in rutinas if r["cargada"]), "rutinas": rutinas}


def estado_cajas():
    """Estado de cada caja de la constelación + nº de checks FAIL/WARN (audit_constelacion)."""
    import audit_constelacion as ac
    cajas, hallazgos = ac.auditar()
    cuenta = {}
    for h in hallazgos:
        d = h.as_dict()
        c = cuenta.setdefault(d["caja"], {"FAIL": 0, "WARN": 0})
        c[d["nivel"]] = c.get(d["nivel"], 0) + 1
    out = []
    for c in cajas:
        estado, vis = "?", "?"
        try:
            with open(c["md"], encoding="utf-8") as f:
                fm, _cuerpo, _err = ac._parse_frontmatter(f.read())
            if fm:
                estado = (fm.get("estado") or "?").lower()
                vis = (fm.get("visibilidad") or "?").lower()
        except Exception:
            pass
        cc = cuenta.get(c["slug"], {})
        out.append({
            "slug": c["slug"], "estado": estado, "visibilidad": vis,
            "fail": cc.get("FAIL", 0), "warn": cc.get("WARN", 0),
        })
    return {"n": len(cajas), "cajas": out}


def estado_agentes():
    """Agentes en disco vs registrados en el catálogo de comités (audit_comites)."""
    import audit_comites as acom
    reg = acom.find_registry()
    reg_text = ""
    if reg:
        with open(reg, encoding="utf-8") as f:
            reg_text = f.read()
    agents = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(AGENTS_DIR, "*.md"))} - acom.BUILTIN
    reg_slugs = set(re.findall(r"`([a-z][a-z0-9-]+)`", reg_text))
    sin = sorted(a for a in agents if a not in reg_slugs)
    return {"en_disco": len(agents), "registrados": len(agents) - len(sin), "sin_registrar": sin}


def estado_hilos():
    """Hilos que se caen (seguimiento), ordenados por urgencia + cuello de botella."""
    import seguimiento as sg
    data = sg.recopilar()
    items = sorted(
        data.get("items", []),
        key=lambda i: (sg.SEV_ORDEN.get(i.get("_sev", "info"), 3), not i.get("es_cuello")),
    )
    top = [{
        "titulo": i.get("titulo", ""),
        "sev": i.get("_sev", "info"),
        "emoji": sg.SEV_EMOJI.get(i.get("_sev", "info"), ""),
        "cuello": bool(i.get("es_cuello")),
        "espera": i.get("quien_espera", ""),
        "accion": i.get("siguiente_accion", ""),
    } for i in items[:12]]
    return {
        "n": len(items),
        "items": top,
        "pendientes_ok": len(data.get("pendientes_ok", [])),
        "fallos": len(data.get("fallos", [])),
        "avisos": data.get("avisos", []),
    }


def estado_salud():
    """Salud del lazo (lee el estado de healthcheck; NO ejecuta run() para no tener efectos)."""
    with open(os.path.join(STATE, "healthcheck", "last_check.json"), encoding="utf-8") as f:
        d = json.load(f)
    d["_degradado_flag"] = os.path.exists(os.path.join(STATE, "healthcheck", "degraded.flag"))
    return d


def estado_biomarcadores():
    """Timeline de biomarcadores de sangre (lo construye tools/biomarcadores.py).
    Solo lectura; apoyo a la decisión, NO consejo médico. Devuelve los puntos en
    formato compacto [fecha, valor, fuera] para no inflar cada /api/estado."""
    base = os.path.join(STATE, "biomarcadores")
    with open(os.path.join(base, "timeline.json"), encoding="utf-8") as f:
        d = json.load(f)
    grupos = {}
    for gk, g in (d.get("grupos") or {}).items():
        analitos = []
        for a in g.get("analitos", []):
            analitos.append({
                "key": a["key"], "nombre": a["nombre"], "unidad": a["unidad"],
                "ref": a.get("ref"),
                "pts": [[p["fecha"], p["valor"], 1 if p.get("fuera") else 0] for p in a["puntos"]],
            })
        grupos[gk] = {"nombre": g["nombre"], "analitos": analitos}
    out = {
        "generado": d.get("generado"),
        "n_analiticas": d.get("n_analiticas"),
        "rango_fechas": d.get("rango_fechas"),
        "por_confirmar_n": d.get("por_confirmar_n"),
        "grupos": grupos,
        "intervenciones": [],
    }
    try:
        with open(os.path.join(base, "intervenciones.json"), encoding="utf-8") as f:
            out["intervenciones"] = json.load(f).get("intervenciones", [])
    except FileNotFoundError:
        pass
    return out


def estado_halt():
    import salida
    return {"halt": bool(salida.halted())}


def estado_actividad():
    """Últimas entradas del PANEL-LAZO (qué hizo / decidió / falló / coste)."""
    try:
        with open(PANEL, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return {"entradas": []}
    bloques = re.split(r"\n## ", txt)
    entradas = []
    for b in bloques[-6:]:
        b = b.strip()
        if not b or "QUÉ HIZO" not in b:
            continue
        cab = b.splitlines()[0]
        def campo(nombre):
            m = re.search(r"%s: (.*)" % re.escape(nombre), b)
            return (m.group(1).strip() if m else "")
        entradas.append({
            "cabecera": cab.replace("## ", ""),
            "hizo": campo("QUÉ HIZO"),
            "decidio": campo("QUÉ DECIDIÓ"),
            "espera": campo("ESPERA OK"),
            "fallo": campo("FALLÓ"),
            "coste": campo("COSTE"),
            "origen": campo("ORIGEN"),      # lazo | test (desde 24-sep-2026; antes, vacío)
        })
    entradas.reverse()
    return {"entradas": entradas}


def estado_borradores():
    """Borradores esperando tu OK (outbox/pending). Solo nº + meta; nunca el cuerpo."""
    d = os.path.join(STATE, "outbox", "pending")
    files = sorted(glob.glob(os.path.join(d, "*.json")))
    items = []
    for p in files[-10:]:
        name = os.path.basename(p)[:-5]
        partes = name.split("-")
        items.append({"cuando": partes[0] if partes else name, "tipo": " ".join(partes[1:3]) if len(partes) > 2 else name})
    # Lo reclamado y sin cerrar (entregándose o INCIERTO, 24-sep-26, 3.5): si no se enseña aquí,
    # nadie lo ve y se queda para siempre en sending/.
    n_inc = len(glob.glob(os.path.join(STATE, "outbox", "sending", "*.json")))
    return {"n": len(files), "items": items, "inciertos": n_inc}


def estado_sesiones():
    """Sesiones de Claude vivas y en qué rama (worktree) trabaja cada una (reusa ramas.resumen())."""
    import ramas
    r = ramas.resumen()
    porrama = {}
    for x in r["sesiones"]:
        porrama[x["rama"]] = porrama.get(x["rama"], 0) + 1
    return {
        "n": r["n_sesiones"],
        "en_base": r["n_base"],
        "n_ramas": r["n_ramas"],
        "por_rama": [{"rama": k, "n": v} for k, v in sorted(porrama.items(), key=lambda kv: -kv[1])],
        "huerfanas": r.get("huerfanas", []),    # ramas paradas (sin sesión + sin fusionar) = cabos sueltos
        "n_huerfanas": r.get("n_huerfanas", 0),
        # Colisiones: ficheros que tocan DOS ramas a la vez (idea copiada de Fleet Deck, 25-jul).
        "conflictos": r.get("conflictos", []),
        "n_conflictos": r.get("n_conflictos", 0),
    }


def estado_deuda():
    """El número de una ojeada: ¿el sistema CIERRA o acumula? (plan «que quede arreglado», 25-jul-26).

    Sin este número, «va bien» es una opinión. `escaladas` son hallazgos detectados varias veces y sin
    cerrar: mientras haya alguno, `test_all.sh` está en rojo a propósito.
    """
    try:
        import deuda
        ab = deuda.abiertas()
        esc = deuda.escaladas()
        import time as _t
        dias = 0
        if ab:
            dias = int((_t.time() - min(v.get("abierto_ts", _t.time()) for v in ab.values())) / 86400)
        return {"abiertas": len(ab), "escaladas": len(esc), "dias_mas_vieja": dias,
                "linea": deuda.numero(),
                "items": [{"clave": k, "que": deuda.texto(v)[:120], "veces": v.get("veces", 1),
                           "estado": v.get("estado"), "ned": v.get("impacto_ned"),
                           "muro": bool(v.get("muro"))}
                          for k, v in sorted(ab.items(),
                                             key=lambda kv: (kv[1].get("estado") != "escalado",
                                                             kv[1].get("abierto_ts", 0)))[:12]]}
    except Exception as e:
        return {"error": str(e)[:120]}


# ───────────────────────── El Tablero (kanban de TAREAS GENERALES, no internals técnicos) ─────────────────────────
# REGLA ({{TITULAR}}, 22/6/26): el tablero es de TUS tareas humanas hacia NED (encargos, hilos que
# seguimos, lo que espera tu firma), NUNCA de la fontanería del sistema (rutinas/daemons/sesiones/
# cola). Eso se queda en las DEMÁS tarjetas del Observatorio, debajo. Ver memoria
# feedback-tablero-tareas-generales-no-tecnico.
COLS_TABLERO = [
    ("por_hacer", "Por hacer"),
    ("en_curso", "En curso"),
    ("esperando_ok", "Esperan tu OK"),
    ("hecho", "Hecho"),
    ("pausa", "En pausa / bloqueadas"),
]
ESTADOS_FLUJO = ["por_hacer", "en_curso", "esperando_ok", "hecho"]  # destinos válidos de un encargo movible
_TOPE_COL = 20  # tope de tarjetas por columna (privados ya fuera; protege de columnas runaway)

# Categoría de un hilo de seguimiento → etiqueta de grupo del tablero.
#   clínico = NED (la misión) · infra = Polaris (el sistema) · el resto = Gestión (trámites/vida).
_CAT_ETQ = {
    "clinico": "NED", "clínico": "NED",
    "infra": "Polaris", "sistema": "Polaris", "polaris": "Polaris",
    "legal": "Gestión", "prensa": "Gestión", "finanzas": "Gestión",
    "voz": "Gestión", "personal": "Gestión", "otros": "Gestión",
}


def _tcard(titulo, sub, sev, mov=False, tid=None, etiqueta="", rank=5, nivel="", tipo=None, fase="gestion", dia="mas", rama=""):
    """Tarjeta de tarea. `tipo`: "encargo" (tuyo) o "seguido" (hilo de Vega). `mov`=se puede mover/
    cerrar. `etiqueta`=grupo; `rank`=orden; `nivel`=indicativo (urgente/importante);
    `fase`=fila en la vista Fase×Día; `dia`=columna (hoy/manana/semana/mas); `rama`=worktree enlazado."""
    c = {"titulo": titulo, "sub": sub, "tipo": tipo or ("encargo" if mov else "seguido"),
         "mov": mov, "sev": sev, "rank": rank, "fase": fase, "dia": dia}
    if tid:
        c["id"] = tid
    if etiqueta:
        c["etiqueta"] = etiqueta
    if nivel:
        c["nivel"] = nivel
    if rama:
        c["rama"] = rama
    return c


def _etiqueta_hilo(categoria):
    return _CAT_ETQ.get((categoria or "").lower(), "NED")


def _fdmy(iso):
    """'2026-06-25' → '25/06' (fecha corta para la tarjeta)."""
    p = (iso or "").split("-")
    return ("%s/%s" % (p[2], p[1])) if len(p) == 3 else (iso or "")


def _clave_tit(s):
    """Clave normalizada de un título (para no duplicar: si ya hay un encargo con este título,
    su hilo gemelo de seguimiento se oculta)."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Fases de la cadena a NED (vista Fase×Día). Icono por fase (la página usa emoji, no es widget).
FASE_ICONO = {"biopsia": "🔬", "dianas": "🎯", "ensayo": "🚪", "acceso": "✈️", "medible": "⚠️"}


def _dia(iso, hoy):
    """Fecha ISO → columna de día del tablero: hoy / manana / semana / mas (sin fecha = mas)."""
    if not iso:
        return "mas"
    try:
        y, m, d = [int(x) for x in iso.split("-")]
        delta = (date(y, m, d) - hoy).days
    except Exception:
        return "mas"
    if delta <= 0:
        return "hoy"
    if delta == 1:
        return "manana"
    if delta <= 7:
        return "semana"
    return "mas"


# "Quién espera" → ¿es algo en TU tejado (tú/tu equipo) o de un tercero/gate?
def _espera_tuya(quien):
    q = (quien or "").lower()
    if not q:
        return True  # sin destinatario claro: trátalo como tuyo (por hacer), no lo entierres
    return any(k in q for k in ("tú", "tu ", "ti", "titular", "ella", "contacto", "yo"))


# Hilo de seguimiento → columna, según su estado y quién está esperando.
def _col_hilo(h):
    est = (h.get("estado") or "").lower()
    if est == "hecho":
        return "hecho"
    if est == "en_curso":
        return "en_curso"
    if est == "por_confirmar":
        return "esperando_ok"            # espera que TÚ confirmes
    if est in ("esperando", "bloqueado", "riesgo"):
        return "por_hacer" if _espera_tuya(h.get("quien_espera")) else "pausa"
    return "por_hacer"                    # pendiente / sin empezar


def estado_tablero():
    """FUENTE ÚNICA: toda tarea es un hilo de Vega (seguimiento). El tablero es una ventana a esa
    lista; cualquier canal crea con seguimiento.crear_tarea. NADA técnico (rutinas/sesiones/cola
    viven en las otras tarjetas del Observatorio)."""
    buckets = {k: [] for k, _ in COLS_TABLERO}
    hoy = []                                   # tareas que vencen hoy o están vencidas
    today = time.strftime("%Y-%m-%d")
    today_date = date.today()

    # --- LA LISTA ÚNICA DE TAREAS = hilos de Vega (seguimiento + cadena de cumbre) ---
    def _hilos():
        import seguimiento
        return seguimiento.recopilar()
    seg = _safe(_hilos)
    for h in (seg.get("items") or []):
        if h.get("privado"):
            continue  # privados de terceros: no al 4K (quedan en local), como en el digest
        hid = h.get("id", "")
        clinico = str(hid).startswith("cumbre:")   # representa una FASE clínica → cabecera, no se mueve
        col = _col_hilo(h)
        cuello = bool(h.get("es_cuello"))
        pr = (h.get("prioridad") or "normal")
        titulo = ("⭐ " if cuello else "") + (h.get("titulo") or "")
        sub = h.get("siguiente_accion") or (("espera: " + h["quien_espera"]) if h.get("quien_espera") else (h.get("categoria") or ""))
        sev = {"roja": "bad", "ambar": "gold", "amarilla": "gold"}.get(h.get("_sev"), "info")
        # indicativo + rango: 🔴 urgente o ⭐ cuello o prioridad alta = arriba; 🟠/🟡 = importante; resto normal.
        nivel = "urgente" if sev == "bad" else ("importante" if (cuello or sev == "gold" or pr == "alta") else "")
        rank = 0 if (cuello or sev == "bad" or pr == "alta") else (1 if sev == "gold" else (3 if pr == "baja" else 2))
        etq = h.get("etiqueta") or _etiqueta_hilo(h.get("categoria"))   # etiqueta del hilo, o derivada de su categoría
        fase = (hid.split(":", 1)[1] if clinico else (h.get("ref_cumbre") or "gestion"))
        pl = h.get("plazo")
        if pl:   # añade el vencimiento al subtítulo
            sub = (sub + " · " if sub else "") + ("vencido" if pl < today else "vence hoy" if pl == today else "vence " + _fdmy(pl))
        card = _tcard(titulo, sub, sev, etiqueta=etq, rank=rank, nivel=nivel,
                      mov=(not clinico), tid=(None if clinico else hid), tipo="seguido",
                      fase=fase, dia=_dia(pl, today_date), rama=h.get("rama", ""))
        if clinico:
            card["clinico"] = True   # representa una fase → en Fase×Día es la cabecera, no una tarjeta de celda
        buckets[col].append(card)
        if pl and pl <= today and (h.get("estado") or "").lower() not in ("hecho", "abandonado"):   # vence hoy o ya pasó → "Para hoy" (excluye hechas/abandonadas)
            hoy.append({"titulo": titulo, "nivel": nivel, "etiqueta": etq, "sev": sev,
                        "plazo": pl, "vencido": pl < today, "rank": rank})

    # --- BORRADORES que esperan tu firma (humano, no técnico) ---
    br = _safe(estado_borradores)
    if not br.get("_error") and br.get("n"):
        buckets["esperando_ok"].append(_tcard("Borradores por revisar", "%d esperan tu OK" % br["n"],
                                              "gold", etiqueta="NED", rank=1, fase="gestion", dia="hoy"))
    if not br.get("_error") and br.get("inciertos"):
        buckets["esperando_ok"].append(_tcard("Envío incierto", "%d pueden haberte llegado: reconcilia"
                                              % br["inciertos"], "bad", etiqueta="NED", rank=0,
                                              fase="gestion", dia="hoy"))

    # Ensamblar: dentro de cada columna, lo más URGENTE/IMPORTANTE arriba (rank). El sort es estable:
    # a igual rango, se respeta el orden de inserción (tus encargos antes que los hilos).
    columnas = []
    presentes = set()
    for key, nombre in COLS_TABLERO:
        cards = sorted(buckets[key], key=lambda c: c.get("rank", 5))
        mostradas = cards[:_TOPE_COL]
        if len(cards) > _TOPE_COL:
            mostradas.append(_tcard("+%d más" % (len(cards) - _TOPE_COL), "", "grey", rank=99))
        for c in mostradas:
            if c.get("etiqueta"):
                presentes.add(c["etiqueta"])
        columnas.append({"key": key, "nombre": nombre, "n": len(cards), "cards": mostradas})
    # Orden de las etiquetas para el filtro: NED y Polaris primero, luego el resto alfabético.
    orden = {"NED": 0, "Polaris": 1}
    etiquetas = sorted(presentes, key=lambda e: (orden.get(e, 2), e.lower()))
    # "Para hoy": lo vencido primero, luego por urgencia.
    hoy.sort(key=lambda x: (0 if x["vencido"] else 1, x.get("rank", 5), x.get("plazo", "")))

    # Filas de la vista Fase×Día: las fases de la cadena a NED (cumbre) + Gestión. La activa, resaltada.
    cumbre = seg.get("cumbre", {}) if isinstance(seg, dict) else {}
    aqui = cumbre.get("aqui_estamos") or ""
    fases = []
    for s in cumbre.get("salientes", []):
        k = s.get("id", "")
        fases.append({"key": k, "titulo": s.get("titulo", ""), "icono": FASE_ICONO.get(k, "•"),
                      "estado": s.get("estado", ""), "sig": s.get("siguiente_accion", ""), "activa": (k == aqui)})
    for s in cumbre.get("transversal", []):
        k = s.get("id", "")
        fases.append({"key": k, "titulo": s.get("titulo", ""), "icono": FASE_ICONO.get(k, "⚠️"),
                      "estado": s.get("estado", ""), "sig": s.get("siguiente_accion", ""), "activa": False, "transversal": True})
    fases.append({"key": "gestion", "titulo": "Gestión · día a día", "icono": "🗂️", "estado": "", "activa": False})

    return {"columnas": columnas, "flujo": ESTADOS_FLUJO, "etiquetas": etiquetas, "hoy": hoy,
            "fases": fases, "dias": [["hoy", "Hoy"], ["manana", "Mañana"], ["semana", "Esta semana"], ["mas", "Más adelante"]]}


def estado_ritmo():
    """Pulso de la campaña: ritmo de tráfico web (Umami) y de fondos hacia la meta.
    Aritmética simple (media móvil + proyección); si no hay clave de Umami o aún no hay
    datos, degrada a 'sin datos' sin tumbar el panel. Solo-lectura, sin LLM."""
    import ritmo
    return ritmo.estado()


def estado_pipeline():
    """Tablero del flujo biopsia → vacuna (etapas + gates de calidad). Solo-lectura: lee el
    resumen de pipeline_vacuna (estado del flujo y umbrales GENÉRICOS; nunca valores crudos/PII)."""
    import pipeline_vacuna as pv
    return pv.resumen()


def estado_trazas():
    """Últimas ejecuciones y fallos recientes (pieza 8: observabilidad por ejecución).
    Solo lectura; reutiliza observabilidad.estado_observabilidad()."""
    import observabilidad
    return observabilidad.estado_observabilidad()


def estado_errores():
    """Tablero de Errores (Fase 2): estado de la cola, heartbeats, anomalías del vigía,
    saldo y últimos fallos con severidad. Responde a '¿intervengo o el sistema ya lo maneja?'.
    Solo lectura; sin LLM. Si una sub-fuente falla, devuelve _error en ese campo."""

    # 1. Cola (pending / processing / failed)
    cola = _safe(lambda: __import__("queue").get_status())

    # 2. Heartbeats de daemons (state/heartbeat/*.json)
    def _heartbeats():
        hb_dir = os.path.join(STATE, "heartbeat")
        if not os.path.isdir(hb_dir):
            return []
        out = []
        for fname in sorted(os.listdir(hb_dir)):
            if not fname.endswith(".json"):
                continue
            agente = fname[:-5]
            p = os.path.join(hb_dir, fname)
            try:
                with open(p, encoding="utf-8") as f:
                    d = _json.load(f)
                ts = d.get("ts") or ""
                estado = d.get("estado") or "?"
                # edad en minutos
                try:
                    from datetime import datetime as _dt
                    if ts.endswith("Z"):
                        ref = _dt.utcnow()
                        t0 = _dt.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
                    else:
                        ref = _dt.now()
                        t0 = _dt.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
                    edad_min = int((ref - t0).total_seconds() / 60)
                except Exception:
                    edad_min = None
                out.append({"agente": agente, "estado": estado, "edad_min": edad_min, "ts": ts[:16]})
            except Exception:
                out.append({"agente": agente, "estado": "ilegible", "edad_min": None, "ts": ""})
        return out

    heartbeats = _safe(_heartbeats)

    # 3. Anomalías vivas del vigía (state/vigia/anomalias.jsonl) — últimas N líneas.
    def _anomalias_vigia():
        p = os.path.join(STATE, "vigia", "anomalias.jsonl")
        if not os.path.exists(p):
            return []
        out = []
        try:
            with open(p, encoding="utf-8") as f:
                lineas = f.readlines()[-20:]
            for ln in reversed(lineas):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = _json.loads(ln)
                    out.append({
                        "tipo": d.get("tipo", "?"),
                        "clase": d.get("clase", "?"),
                        "ts": (d.get("ts") or "")[:16],
                        "detalle": (d.get("detalle") or "")[:120],
                        "auto_resuelta": bool(d.get("auto_resuelta")),
                    })
                except Exception:
                    pass
                if len(out) >= 8:
                    break
        except Exception:
            pass
        return out

    anomalias = _safe(_anomalias_vigia)

    # 4. Últimos FALLOS con severidad (vía observabilidad, misma fuente que la tarjeta de trazas
    #    pero filtrado a resultado=fail y con el campo severidad destacado).
    def _fallos_severos():
        import observabilidad
        regs = [r for r in observabilidad._leer_dias(2) if r.get("resultado") == "fail"]
        regs.sort(key=lambda r: r.get("ts_ini", ""), reverse=True)
        out = []
        for r in regs[:10]:
            sev = r.get("severidad") or "?"
            out.append({
                "ts": (r.get("ts_ini") or "")[:16],
                "agente": r.get("agente", "?"),
                "job": r.get("job", "?"),
                "severidad": sev,
                "error_type": r.get("error_type"),
                # ¿ya auto-resuelto? Si hay trazas 'ok' del mismo agente DESPUÉS de este fallo,
                # se asume que el sistema se recuperó solo (heurística rápida, sin LLM).
                "auto_ok": any(
                    x.get("resultado") == "ok"
                    and x.get("agente") == r.get("agente")
                    and x.get("ts_ini", "") > r.get("ts_ini", "")
                    for x in regs[:30]  # busca en la ventana reciente
                ),
            })
        return out

    fallos = _safe(_fallos_severos)

    # 5. Saldo (cost_guard — mismo dato que la tarjeta de gasto pero centrado en el semáforo).
    def _saldo():
        import cost_guard
        ok_s, motivo, nivel = cost_guard.check_before_job(esencial=False)
        return {"ok": bool(ok_s), "motivo": motivo, "nivel": nivel}

    saldo = _safe(_saldo)

    # 6. Acuses de alertas de salud (tools/salud.py, 3/7/26): "visto, en ello" — quién se puso y
    #    cuándo. Solo lectura; el propio healthcheck decide la voz, esto es para que {{TITULAR}} (o
    #    cualquier sesión) vea el estado sin esperar al próximo aviso por Telegram.
    def _acuses_salud():
        import salud
        return salud.listar_abiertas()

    acuses = _safe(_acuses_salud)

    # 7. Procesos del usuario frente al límite del sistema (13-sep-26). El «fork failed» que dejó
    #    la shell de Claude muerta durante horas se anticipa viendo cuántos procesos lleva el
    #    usuario y cuántos Chrome sin ventana quedaron colgados. Solo lectura: ps, sysctl, pgrep.
    def _procesos():
        import getpass
        import subprocess

        def _run(cmd):
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout

        usados = len(_run(["ps", "-U", getpass.getuser(), "-o", "pid="]).split())
        limite = int((_run(["sysctl", "-n", "kern.maxprocperuid"]).strip() or "0"))
        headless = len(_run(["pgrep", "-f", "headless=new"]).split())
        return clasificar_procesos(usados, limite, headless)

    procesos = _safe(_procesos)

    return {
        "cola": cola,
        "heartbeats": heartbeats,
        "anomalias_vigia": anomalias,
        "fallos_recientes": fallos,
        "saldo": saldo,
        "acuses_salud": acuses,
        "procesos": procesos,
    }


# Umbrales de la señal de procesos. Pura y sin E/S para poder probarla (tests/test_observatorio.py).
_PROC_AVISO_PCT = 60
_PROC_ALERTA_PCT = 85
_PROC_AVISO_HEADLESS = 4


def clasificar_procesos(usados, limite, headless):
    """Nivel de riesgo de quedarse sin procesos (lo que rompe la shell con «fork failed»)."""
    pct = round(100.0 * usados / limite, 1) if limite else None
    if pct is not None and pct >= _PROC_ALERTA_PCT:
        nivel = "alerta"
        consejo = ("Cerrar procesos colgados y reiniciar la app de Claude: la shell puede "
                   "empezar a fallar con «fork failed».")
    elif (pct is not None and pct >= _PROC_AVISO_PCT) or headless >= _PROC_AVISO_HEADLESS:
        nivel = "aviso"
        consejo = "Se están acumulando procesos (p. ej. Chrome sin ventana): conviene cerrarlos."
    else:
        nivel = "ok"
        consejo = ""
    return {"usados": usados, "limite": limite, "pct": pct,
            "chrome_headless": headless, "nivel": nivel, "consejo": consejo}


# Alias del módulo json para usarlo dentro de funciones nested (no importar desde dentro de lambdas)
import json as _json  # noqa: E402 — se usa en estado_errores


def recopilar_todo():
    """Ensambla TODO el estado. Cada fuente aislada: una caída no tumba el resto."""
    return {
        "generado": time.strftime("%Y-%m-%d %H:%M:%S"),
        "build": BUILD,
        "tablero": _safe(estado_tablero),
        "pipeline": _safe(estado_pipeline),
        "config": _safe(estado_config),
        "sesiones": _safe(estado_sesiones),
        "deuda": _safe(estado_deuda),          # ¿el sistema cierra o acumula? (25-jul-26)
        "gasto": _safe(estado_gasto),
        "rutinas": _safe(estado_rutinas),
        "cajas": _safe(estado_cajas),
        "agentes": _safe(estado_agentes),
        "hilos": _safe(estado_hilos),
        "salud": _safe(estado_salud),
        "biomarcadores": _safe(estado_biomarcadores),
        "ritmo": _safe(estado_ritmo),
        "halt": _safe(estado_halt),
        "actividad": _safe(estado_actividad),
        "borradores": _safe(estado_borradores),
        "trazas": _safe(estado_trazas),
        "errores": _safe(estado_errores),
        "urls": {"local": "http://%s:%d" % (HOST, PORT), "movil": "http://%s:%d" % (TS_HOST, MOVIL_PORT)},
    }


# ───────────────────────── la página (HTML+CSS+JS embebidos, stdlib) ─────────────────────────
# Los valores dinámicos se pintan con textContent (no innerHTML) → a prueba de inyección por construcción.
PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="tablero-token" content="__CSRF_TOKEN__">
<meta name="tablero-build" content="__BUILD__">
<!-- "Añadir a pantalla de inicio" → abre a pantalla completa como app (móvil/iPad), sin barra del navegador. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="El Tablero">
<meta name="theme-color" content="#080b16">
<title>El Observatorio · Polaris</title>
<style>
  :root{
    --bg:#080b16; --bg2:#0d1224; --card:#121a30; --line:#1f2a47;
    --tx:#e8ecf7; --mut:#8a94b0; --gold:#ffd479; --star:#aa7bff;
    --ok:#3fd17a; --warn:#ffb454; --bad:#ff5d6c; --grey:#4a536e;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:radial-gradient(1200px 700px at 80% -10%, #15224a 0%, var(--bg) 55%) fixed, var(--bg);
    color:var(--tx); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    padding:18px; max-width:1180px; margin:0 auto; -webkit-text-size-adjust:100%;
  }
  header{display:flex; align-items:center; gap:14px; flex-wrap:wrap; margin-bottom:6px}
  h1{font-size:20px; margin:0; letter-spacing:.3px}
  h1 .star{color:var(--gold)}
  .sub{color:var(--mut); font-size:12.5px}
  .bar-top{margin-left:auto; display:flex; gap:8px; align-items:center; flex-wrap:wrap}
  button{
    background:var(--card); color:var(--tx); border:1px solid var(--line); border-radius:10px;
    padding:8px 12px; font-size:13px; cursor:pointer; transition:.15s;
  }
  button:hover{border-color:var(--star)}
  .pill{font-size:12px; padding:4px 10px; border-radius:999px; border:1px solid var(--line)}
  .pill.ok{color:var(--ok); border-color:#1c5} .pill.bad{color:var(--bad); border-color:#a33}
  .tabs{display:flex; gap:8px; margin-top:14px; border-bottom:1px solid var(--line); padding-bottom:0}
  .tabs .tab{background:transparent; border:1px solid var(--line); border-bottom:none;
             border-radius:11px 11px 0 0; padding:10px 18px; font-size:14px; color:var(--mut);
             min-height:44px; margin-bottom:-1px}
  .tabs .tab.sel{color:var(--gold); border-color:var(--line); background:linear-gradient(180deg,var(--card),var(--bg2))}
  .grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px; margin-top:14px}
  .card{background:linear-gradient(180deg,var(--card),var(--bg2)); border:1px solid var(--line);
        border-radius:16px; padding:15px 16px; overflow:hidden}
  .card h2{font-size:13px; text-transform:uppercase; letter-spacing:.8px; color:var(--mut);
           margin:0 0 10px; display:flex; align-items:center; gap:8px}
  .card.full{grid-column:1/-1}
  .biogrp{display:flex; gap:6px; flex-wrap:wrap; margin:8px 0}
  .biogrp .tab{border:1px solid var(--line); border-radius:8px; padding:6px 11px; min-height:34px;
               font-size:12.5px; color:var(--mut); background:var(--bg2); cursor:pointer}
  .biogrp .tab.sel{color:var(--gold); border-color:var(--star)}
  .bioleg{display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; margin:4px 0 12px}
  .biolegitem{display:inline-flex; align-items:center; gap:5px}
  .biodot{width:9px; height:9px; border-radius:2px; display:inline-block}
  .biocharts{display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px}
  .biochart{background:var(--bg2); border:1px solid var(--line); border-radius:10px; padding:8px 9px}
  .biosvg{width:100%; height:118px; display:block; margin-top:3px}
  .biohead{display:flex; justify-content:space-between; align-items:baseline; gap:6px}
  .bioname{font-size:12px; color:var(--mut); line-height:1.25}
  .bioval{font-size:12.5px; color:var(--tx); font-variant-numeric:tabular-nums; white-space:nowrap}
  .bioval.bad{color:var(--bad)}
  .gauge{display:flex; align-items:center; gap:18px}
  .ring{--p:0; width:104px; height:104px; border-radius:50%; flex:0 0 auto;
        background:conic-gradient(var(--gold) calc(var(--p)*1%), #20294a 0);
        display:flex; align-items:center; justify-content:center}
  .ring i{width:80px; height:80px; border-radius:50%; background:var(--bg2);
          display:flex; align-items:center; justify-content:center; font-size:24px; font-weight:700; font-style:normal}
  .cats{flex:1; min-width:0}
  .row{display:flex; align-items:center; gap:8px; margin:5px 0; font-size:13px}
  .row .lab{width:115px; color:var(--mut); flex:0 0 auto; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
  .meter{flex:1; height:8px; background:#1a2342; border-radius:6px; overflow:hidden}
  .meter > span{display:block; height:100%; background:var(--star)}
  .row .v{width:38px; text-align:right; color:var(--tx)}
  ul.clean{list-style:none; margin:0; padding:0}
  ul.clean li{padding:6px 0; border-bottom:1px dashed var(--line); display:flex; gap:8px; align-items:baseline}
  ul.clean li:last-child{border-bottom:0}
  .dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto;margin-top:6px}
  .dot.on{background:var(--ok)} .dot.off{background:var(--grey)}
  .muted{color:var(--mut)} .small{font-size:12.5px} .right{margin-left:auto}
  .big{font-size:26px; font-weight:700}
  .tag{font-size:11px; padding:2px 8px; border-radius:999px; background:#1a2342; color:var(--mut)}
  .tag.activa{color:var(--ok)} .tag.propuesta{color:var(--gold)} .tag.bad{color:var(--bad)}
  /* Pipeline biopsia→vacuna (tablero del flujo) */
  .pl-rail{display:flex; flex-direction:column; gap:10px; margin-top:4px}
  .pl-etapa{display:flex; gap:11px; align-items:flex-start; padding:11px 12px; border-radius:13px;
            background:#0e1426; border:1px solid var(--line)}
  .pl-etapa.act{border-color:var(--gold); background:linear-gradient(180deg,#171b30,#0e1426)}
  .pl-etapa.done{opacity:.72}
  .pl-badge{flex:0 0 auto; width:30px; height:30px; border-radius:9px; display:flex; align-items:center;
            justify-content:center; font-size:14px; font-weight:700; background:#1a2342; color:var(--mut)}
  .pl-badge.done{background:rgba(63,209,122,.16); color:var(--ok)}
  .pl-badge.act{background:rgba(255,212,121,.18); color:var(--gold)}
  .pl-badge.block{background:rgba(255,93,108,.16); color:var(--bad)}
  .pl-body{flex:1; min-width:0}
  .pl-nom{font-size:13.5px; color:var(--tx); font-weight:600}
  .pl-que{font-size:12px; color:var(--mut); margin:2px 0 7px}
  .pl-gates{display:flex; flex-wrap:wrap; gap:6px}
  .pl-gate{display:inline-flex; align-items:center; gap:5px; font-size:11.5px; padding:3px 9px;
           border-radius:999px; background:#141b30; border:1px solid var(--line); color:var(--mut)}
  .pl-gate .gi{width:7px; height:7px; border-radius:50%; flex:0 0 auto; background:var(--grey)}
  .pl-gate.ok .gi{background:var(--ok)} .pl-gate.fallo{border-color:#a33}
  .pl-gate.fallo .gi{background:var(--bad)} .pl-gate.na{opacity:.55}
  .pl-banner{margin-top:13px; font-size:12px; color:var(--mut); border-top:1px dashed var(--line);
             padding-top:10px; line-height:1.5}
  .err{color:var(--bad); font-size:12.5px}
  .filtros{display:flex; gap:6px; margin-bottom:8px}
  .filtros button{padding:4px 9px; font-size:12px}
  .filtros button.sel{border-color:var(--star); color:var(--gold)}
  details summary{cursor:pointer; color:var(--mut); font-size:12.5px; margin-top:6px}
  .foot{color:var(--mut); font-size:12px; text-align:center; margin:22px 0 8px}
  a{color:var(--star)}
  /* El Tablero (kanban) */
  .addinp{background:var(--bg2); color:var(--tx); border:1px solid var(--line); border-radius:9px;
          padding:11px 12px; font-size:12.5px; width:170px; min-height:44px}
  .addinp::placeholder{color:var(--mut)}
  .kb{display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:11px; margin-top:4px}
  .kcol{background:#0e1426; border:1px solid var(--line); border-radius:13px; padding:10px; min-width:0}
  .kh{display:flex; align-items:center; gap:8px; margin-bottom:9px}
  .kh .knom{font-size:11px; text-transform:uppercase; letter-spacing:.6px; color:var(--mut);
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
  .kh .kn{margin-left:auto; font-size:11px; color:var(--mut); flex:0 0 auto}
  .kcard{background:#141c34; border:1px solid var(--line); border-radius:10px; padding:9px 10px; margin-bottom:8px}
  .kcard:last-child{margin-bottom:0}
  .kcard.enc{border-color:#2c3a64; background:#16213f; border-left:3px solid var(--star)}
  .kcard .kt{font-size:13px; color:var(--tx); line-height:1.35}
  .kcard .ks{font-size:11px; color:var(--mut); margin-top:3px}
  .kcard.ok .ks{color:var(--ok)} .kcard.gold .ks{color:var(--gold)}
  .kcard.bad .ks{color:var(--bad)} .kcard.grey .ks{color:var(--mut)}
  .knav{display:flex; gap:8px; margin-top:9px}
  .kmove{display:inline-flex; align-items:center; justify-content:center; padding:0 12px;
         min-width:44px; min-height:44px; border-radius:9px; background:#1c2647; border-color:#3a4a7a; color:var(--tx)}
  .kmove svg{width:15px; height:15px}
  .ketq{display:inline-block; font-size:10px; line-height:1; padding:3px 8px; border-radius:999px;
        border:1px solid currentColor; margin-bottom:6px; letter-spacing:.3px}
  .kflag{display:inline-flex; align-items:center; gap:5px; font-size:10px; line-height:1; padding:3px 8px;
         border-radius:999px; border:1px solid currentColor; margin:0 0 6px 6px; letter-spacing:.3px}
  .kflag .fd{width:6px; height:6px; border-radius:50%; background:currentColor; flex:0 0 auto}
  .kflag.urg{color:var(--bad)} .kflag.imp{color:var(--gold)}
  .khoy{background:#16213f; border:1px solid #2c3a64; border-radius:13px; padding:11px 13px; margin:2px 0 13px}
  .khoy-h{font-size:11px; text-transform:uppercase; letter-spacing:.6px; color:var(--gold); margin-bottom:9px;
          display:flex; align-items:center; gap:8px}
  .khoy ul{list-style:none; margin:0; padding:0}
  .khoy li{padding:7px 0; border-bottom:1px dashed var(--line); display:flex; gap:9px; align-items:center; font-size:13px}
  .khoy li:last-child{border-bottom:0}
  .khoy .cuando{margin-left:auto; font-size:11px; flex:0 0 auto}
  /* vista Fase × Día */
  .kfd{display:grid; gap:7px; margin-top:4px; overflow-x:auto}
  .kfd-h{font-size:10.5px; text-transform:uppercase; letter-spacing:.5px; color:var(--mut); padding:0 4px 2px; align-self:end}
  .kfd-fase{background:#0e1426; border:1px solid var(--line); border-radius:11px; padding:9px; min-width:0}
  .kfd-fase.activa{border-color:var(--gold); background:#191c12}
  .kfd-fnom{font-size:12px; color:var(--tx); line-height:1.3}
  .kfd-fest{font-size:10px; color:var(--mut); margin-top:3px}
  .kfd-fact{font-size:10px; color:var(--gold); margin-top:3px}
  .kfd-fsig{font-size:10px; color:var(--mut); margin-top:4px; line-height:1.3}
  .kfd-cell{background:rgba(255,255,255,.015); border:1px solid #161f3a; border-radius:11px; padding:7px;
            min-width:0; display:flex; flex-direction:column; gap:7px}
  .addetq{background:var(--bg2); color:var(--tx); border:1px solid var(--line); border-radius:9px;
          padding:0 8px; min-height:44px; font-size:12.5px; margin-right:6px}
  .addfec{background:var(--bg2); color:var(--tx); border:1px solid var(--line); border-radius:9px;
          padding:0 8px; min-height:44px; font-size:12.5px; margin-right:6px; color-scheme:dark}
  .kempty{color:var(--mut); font-size:12px; text-align:center; padding:8px 0}
  @media (max-width:560px){ body{padding:12px} .ring{width:90px;height:90px} .ring i{width:68px;height:68px}
    .kb{grid-template-columns:1fr} .addinp{width:130px} }
</style>
</head>
<body>
<header>
  <h1><span class="star">✦</span> El Observatorio</h1>
  <span class="sub" id="sub">cargando…</span>
  <div class="bar-top">
    <span class="pill" id="halt">·</span>
    <a href="/sintomas" style="text-decoration:none"><button type="button">✦ Síntomas</button></a>
    <button id="btn-copy">Copiar estado</button>
    <button id="btn-refresh">↻ Refrescar</button>
  </div>
</header>
<div class="tabs" id="tabs"></div>
<div class="grid" id="grid"></div>
<div class="foot" id="foot"></div>

<script>
const $ = (t, c, txt) => { const e=document.createElement(t); if(c)e.className=c; if(txt!=null)e.textContent=txt; return e; };
const svgEl = (t, attrs) => { const e=document.createElementNS('http://www.w3.org/2000/svg', t); for(const k in attrs) e.setAttribute(k, attrs[k]); return e; };
const card = (titulo, full) => { const c=$('div','card'+(full?' full':'')); const h=$('h2',null,titulo); c.appendChild(h); return c; };
const errBox = (src) => { const d=$('div','err','No pude leer esto: '+(src&&src._error||'desconocido')); return d; };
const nf = new Intl.NumberFormat('es-ES');
const eur = (n)=> n==null? '—' : ('$'+n.toFixed(2));
const ktok = (n)=> n==null? '—' : (n>=1000? nf.format(Math.round(n/1000))+'k' : nf.format(n));
let LAST=null;
const TOKEN=(document.querySelector('meta[name=tablero-token]')||{}).content||'';
const BUILD=(document.querySelector('meta[name=tablero-build]')||{}).content||'';
const SVG_NS='http://www.w3.org/2000/svg';
function chevron(dir){ // SVG (no glifo Unicode): centrado óptico de gratis, sin riesgo de tofu
  const s=document.createElementNS(SVG_NS,'svg'); s.setAttribute('viewBox','0 0 24 24'); s.setAttribute('fill','none');
  s.setAttribute('stroke','currentColor'); s.setAttribute('stroke-width','2.5');
  s.setAttribute('stroke-linecap','round'); s.setAttribute('stroke-linejoin','round');
  const p=document.createElementNS(SVG_NS,'polyline');
  p.setAttribute('points', dir<0? '15 18 9 12 15 6':'9 18 15 12 9 6'); s.appendChild(p); return s;
}
function btnMove(dir, label, fn){ const b=$('button','kmove'); b.appendChild(chevron(dir)); b.setAttribute('aria-label',label); b.onclick=fn; return b; }
function checkIcon(){ const s=document.createElementNS(SVG_NS,'svg'); s.setAttribute('viewBox','0 0 24 24'); s.setAttribute('fill','none'); s.setAttribute('stroke','currentColor'); s.setAttribute('stroke-width','2.5'); s.setAttribute('stroke-linecap','round'); s.setAttribute('stroke-linejoin','round'); const p=document.createElementNS(SVG_NS,'polyline'); p.setAttribute('points','20 6 9 17 4 12'); s.appendChild(p); return s; }
function btnHecho(fn){ const b=$('button','kmove'); b.appendChild(checkIcon()); b.setAttribute('aria-label','Marcar hecha'); b.onclick=fn; return b; }

async function tareaPost(ruta, cuerpo){
  try{
    const r=await fetch(ruta,{method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json','X-Tablero-Token':TOKEN}, body:JSON.stringify(cuerpo)});
    if(r.ok){ tick(); return true; }
    const e=await r.json().catch(()=>({})); alert('No pude: '+(e.error||r.status)); return false;
  }catch(e){ alert('Sin conexión con el servidor.'); return false; }
}
const moverTarea=(id, estado)=> tareaPost('/api/tarea/mover', {id, estado});
const moverHilo=(id, estado)=> tareaPost('/api/hilo/mover', {id, estado});   // write-back a Vega
const moverCard=(cd, estado)=> (cd.tipo==='seguido'? moverHilo : moverTarea)(cd.id, estado);
const crearTarea=(titulo, etiqueta, vence)=>{ if(titulo && titulo.trim()) tareaPost('/api/tarea/crear', {titulo:titulo.trim(), etiqueta:etiqueta||'NED', vence:vence||'', origen:'tablero'}); };
let lente='estado';   // lente del tablero: 'estado' o 'fase'; persiste entre refrescos
// Color del chip por etiqueta: NED=dorado (meta), Polaris=violeta (sistema), resto=azul.
const ETQ_COLOR={'NED':'var(--gold)','Polaris':'var(--star)'};
const etqColor=(e)=> ETQ_COLOR[e] || '#6fa8dc';
let filtroEtq='Todas';  // persiste entre refrescos
function aplicarFiltroEtq(){
  document.querySelectorAll('#grid .kcard').forEach(el=>{
    const e=el.dataset.etq||'';
    el.style.display=(filtroEtq==='Todas' || e===filtroEtq) ? '' : 'none';
  });
}
// indicativo visible de urgencia/importancia: punto de color + palabra
function flagBadge(nivel){
  const b=$('span','kflag '+(nivel==='urgente'?'urg':'imp'));
  b.appendChild($('span','fd')); b.appendChild($('span',null, nivel==='urgente'?'Urgente':'Importante'));
  return b;
}

function gauge(cfg){
  const c=card('Estado general · cuánto falta', false);
  if(cfg._error){ c.appendChild(errBox(cfg)); return c; }
  const g=$('div','gauge');
  const ring=$('div','ring'); ring.style.setProperty('--p', cfg.overall);
  ring.appendChild($('i',null, cfg.overall)); g.appendChild(ring);
  const cats=$('div','cats');
  (cfg.categorias||[]).forEach(k=>{
    const r=$('div','row'); r.appendChild($('span','lab',k.label));
    const m=$('div','meter'); const s=$('span'); s.style.width=k.pct+'%'; m.appendChild(s); r.appendChild(m);
    r.appendChild($('span','v', k.pct)); cats.appendChild(r);
  });
  g.appendChild(cats); c.appendChild(g);
  if(cfg.pendientes && cfg.pendientes.length){
    const d=document.createElement('details'); d.appendChild($('summary',null,'Falta por ajustar ('+cfg.pendientes.length+')'));
    const ul=$('ul','clean'); cfg.pendientes.forEach(p=>{ const li=$('li'); li.appendChild($('span',null,'• ')); li.appendChild($('span',null,p)); ul.appendChild(li); });
    d.appendChild(ul); c.appendChild(d);
  }
  return c;
}

function gasto(g){
  const c=card('Gasto', false);
  if(g._error){ c.appendChild(errBox(g)); return c; }
  const top=$('div','row');
  top.appendChild($('span','big', eur(g.hoy_usd)));
  top.appendChild($('span','muted small', ' de '+eur(g.tope_usd)+(g.conservador?' (modo ahorro)':'')));
  c.appendChild(top);
  const m=$('div','meter'); const s=$('span'); const pct=g.tope_usd? Math.min(100, 100*g.hoy_usd/g.tope_usd):0;
  s.style.width=pct+'%'; s.style.background = pct>85?'var(--bad)':(pct>60?'var(--warn)':'var(--ok)'); m.appendChild(s); c.appendChild(m);
  c.appendChild($('div','small muted', g.n_jobs+' trabajos hoy')) ;
  // Mes en curso ($ real del daemon)
  if(g.mes_usd!=null){ c.appendChild($('div','small muted', 'Mes: '+eur(g.mes_usd)+(g.tope_mes_usd?' / '+eur(g.tope_mes_usd):''))); }
  // Serie de 7 días (barras compactas de $ real)
  const s7=g.serie7||[];
  if(s7.length){
    const mx=Math.max(0.01, ...s7.map(x=>x.usd));
    const wrap=$('div'); wrap.style.cssText='display:flex;align-items:flex-end;gap:3px;height:34px;margin:6px 0';
    s7.forEach(x=>{ const b=$('div'); const h=Math.round(4+30*x.usd/mx);
      b.style.cssText='flex:1;height:'+h+'px;background:var(--star);border-radius:2px;opacity:.85'; b.title=x.dia+': '+eur(x.usd); wrap.appendChild(b); });
    c.appendChild(wrap);
    const lab=$('div','small muted'); lab.textContent=s7[0].dia+' → '+s7[s7.length-1].dia+' ($ real/día)'; c.appendChild(lab);
  }
  c.appendChild($('div','small muted','💡 $ real = daemon (prepago). El volumen de Opus va en tu cuota Max (€≈0).'));
  const tk=g.tokens||{};
  if(tk._status){ c.appendChild($('div','small muted', tk._status)); }
  else if(!tk._error){
    c.appendChild($('div','small muted', 'Tokens hoy: '+ktok(tk.hoy_tok)+(tk.apis_pago_usd_hoy? ' · APIs de pago hoy: '+eur(tk.apis_pago_usd_hoy):'')));
    if(tk.hoy_tok_worktrees!=null){ c.appendChild($('div','small muted', '↳ casa base '+ktok(tk.hoy_tok_casa_base)+' · ramas en curso '+ktok(tk.hoy_tok_worktrees)+' ('+tk.proyectos_medidos+' proyectos)')); }
    const d=document.createElement('details'); d.appendChild($('summary',null,'Por modelo'));
    const ul=$('ul','clean'); (tk.por_modelo||[]).forEach(x=>{ const li=$('li'); li.appendChild($('span',null,x.modelo)); const r=$('span','right small muted', ktok(x.tok)+(x.usd!=null?' · '+eur(x.usd):'')); li.appendChild(r); ul.appendChild(li); });
    d.appendChild(ul); c.appendChild(d);
  }
  return c;
}

function rutinas(rt){
  const c=card('Rutinas 24/7', false);
  if(rt._error){ c.appendChild(errBox(rt)); return c; }
  c.querySelector('h2').appendChild($('span','right small muted', rt.cargadas+'/'+rt.total+' vivas'));
  const f=$('div','filtros');
  ['todas','vivas','paradas'].forEach((k,i)=>{ const b=$('button',(i==0?'sel':''),k); b.dataset.f=k; b.onclick=()=>{ f.querySelectorAll('button').forEach(x=>x.classList.remove('sel')); b.classList.add('sel'); paint(k); }; f.appendChild(b); });
  c.appendChild(f);
  const ul=$('ul','clean'); c.appendChild(ul);
  function paint(filtro){
    ul.innerHTML='';
    (rt.rutinas||[]).filter(r=> filtro=='todas' || (filtro=='vivas'&&r.cargada) || (filtro=='paradas'&&!r.cargada)).forEach(r=>{
      const li=$('li'); li.appendChild($('span','dot '+(r.cargada?'on':'off')));
      li.appendChild($('span',null,r.nombre));
      li.appendChild($('span','right small muted', r.corriendo?'corriendo':(r.cargada?'en espera':'parada')));
      ul.appendChild(li);
    });
  }
  paint('todas');
  return c;
}

function cajas(cj){
  const c=card('Constelación · cajas', false);
  if(cj._error){ c.appendChild(errBox(cj)); return c; }
  c.querySelector('h2').appendChild($('span','right small muted', (cj.n||0)+' caja'+(cj.n==1?'':'s')));
  if(!cj.cajas || !cj.cajas.length){ c.appendChild($('div','small muted','Aún no hay cajas montadas.')); return c; }
  const ul=$('ul','clean');
  cj.cajas.forEach(x=>{ const li=$('li'); li.appendChild($('span',null,x.slug));
    const r=$('span','right'); const t=$('span','tag '+(x.estado||''), x.estado||'?'); r.appendChild(t);
    if(x.fail){ r.appendChild($('span','tag bad',' '+x.fail+' fallo'+(x.fail==1?'':'s'))); }
    li.appendChild(r); ul.appendChild(li);
  });
  c.appendChild(ul);
  return c;
}

function hilos(h){
  const c=card('Hilos que se están cayendo', true);
  if(h._error){ c.appendChild(errBox(h)); return c; }
  c.querySelector('h2').appendChild($('span','right small muted', (h.pendientes_ok||0)+' esperan tu OK · '+(h.n||0)+' hilos'));
  if(!h.items || !h.items.length){ c.appendChild($('div','small muted','Nada urgente cayéndose. 💜')); }
  else{
    const ul=$('ul','clean');
    h.items.forEach(i=>{ const li=$('li'); li.appendChild($('span',null,i.emoji||'•'));
      const box=$('span'); box.appendChild($('span',null,(i.cuello?'⭐ ':'')+i.titulo));
      if(i.accion){ box.appendChild($('div','small muted','→ '+i.accion)); }
      li.appendChild(box);
      if(i.espera){ li.appendChild($('span','right small muted','espera: '+i.espera)); }
      ul.appendChild(li);
    });
    c.appendChild(ul);
  }
  if(h.avisos && h.avisos.length){ c.appendChild($('div','small err','Avisos: '+h.avisos.join(' · '))); }
  return c;
}

function salud(s){
  const c=card('Salud del lazo', false);
  if(s._error){ c.appendChild(errBox(s)); return c; }
  const ul=$('ul','clean');
  const cola=s.cola||{};
  const li=(k,v)=>{ const x=$('li'); x.appendChild($('span',null,k)); x.appendChild($('span','right small muted', v)); ul.appendChild(x); };
  li('Cola', (cola.pending||0)+' en espera · '+(cola.failed||0)+' fallidos');
  li('Disco libre', (s.disco_libre_gb!=null? s.disco_libre_gb+' GB':'—'));
  li('Telegram', s.telegram_ok? 'ok':'sin conexión');
  li('Días sin señal tuya', (s.dias_sin_senal!=null? s.dias_sin_senal:'—'));
  li('Modo', s.degradado||s._degradado_flag? 'conservador':'normal');
  c.appendChild(ul);
  return c;
}

function ritmo(r){
  const c=card('Ritmo de la campaña', false);
  if(r._error){ c.appendChild(errBox(r)); return c; }
  const w=r.web||{};
  if(w.estado==='ok'){
    const arrow = w.tendencia==='sube'?'↑':(w.tendencia==='baja'?'↓':'→');
    const top=$('div','row');
    top.appendChild($('span','big', w.vistas_dia));
    let sub=' vistas/día · '+arrow+' '+w.tendencia+(w.tendencia_pct!=null?' ('+(w.tendencia_pct>0?'+':'')+w.tendencia_pct+'%)':'');
    top.appendChild($('span','muted small', sub));
    c.appendChild(top);
    c.appendChild($('div','small muted', 'Últimos 7 días: '+w.vistas_7d+' vistas · proyección próx. 7 días: ~'+w.proy_7d));
    if(!w.fiable){ c.appendChild($('div','small muted', 'Tendencia orientativa: menos de 2 semanas de datos.')); }
  } else {
    c.appendChild($('div','small muted', 'Web: '+(w.motivo||'sin datos')));
  }
  const f=r.fondos||{};
  if(f.estado==='ok'){
    let line='Fondos: '+f.acumulado_eur+' €'+(f.meta_eur?(' de '+f.meta_eur+' €'):'');
    if(f.ritmo_dia_eur!=null){ line+=' · '+f.ritmo_dia_eur+' €/día'; }
    if(f.eta){ line+=' · a este paso: '+f.eta; }
    c.appendChild($('div','small', line));
  } else {
    c.appendChild($('div','small muted', 'Fondos: '+(f.motivo||'sin registro aún')));
  }
  return c;
}

function agentes(a){
  const c=card('Agentes y comités', false);
  if(a._error){ c.appendChild(errBox(a)); return c; }
  c.appendChild($('div','row')).appendChild($('span','big', (a.registrados||0)+'/'+(a.en_disco||0)));
  c.appendChild($('div','small muted','agentes registrados en el catálogo'));
  if(a.sin_registrar && a.sin_registrar.length){ c.appendChild($('div','small err','Sin registrar: '+a.sin_registrar.join(', '))); }
  return c;
}

function sesiones(s){
  const c=card('Sesiones de Claude en marcha', false);
  if(s._error){ c.appendChild(errBox(s)); return c; }
  c.appendChild($('div','row')).appendChild($('span','big', s.n));
  c.appendChild($('div','small muted','procesos vivos · '+(s.n_ramas||0)+' rama(s) de trabajo'));
  const ul=$('ul','clean');
  (s.por_rama||[]).forEach(x=>{ const li=$('li'); li.appendChild($('span',null,x.rama)); li.appendChild($('span','right small muted', x.n)); ul.appendChild(li); });
  c.appendChild(ul);
  if(s.en_base>2){ c.appendChild($('div','small err', s.en_base+' a la vez en casa base — cada hilo debería ir en su rama')); }
  if((s.huerfanas||[]).length){
    c.appendChild($('div','small muted', 'Ramas paradas (sin sesión + cambios sin fusionar):'));
    const up=$('ul','clean');
    s.huerfanas.forEach(h=>{
      const li=$('li'); const edad=(h.edad_dias>=0?h.edad_dias+'d':'?');
      li.appendChild($('span',null,'⚠️ '+h.rama));
      li.appendChild($('span','right small muted', h.ahead+' sin fusionar · '+edad));
      up.appendChild(li);
    });
    c.appendChild(up);
  }
  return c;
}

function actividad(ac){
  const c=card('Actividad reciente del lazo', true);
  if(ac._error){ c.appendChild(errBox(ac)); return c; }
  if(!ac.entradas || !ac.entradas.length){ c.appendChild($('div','small muted','Sin actividad registrada.')); return c; }
  const ul=$('ul','clean');
  ac.entradas.forEach(e=>{ const li=$('li'); const box=$('span');
    box.appendChild($('div','small muted', e.cabecera));
    box.appendChild($('div',null, e.hizo||'—'));
    if(e.fallo && e.fallo!=='—'){ box.appendChild($('div','small err','falló: '+e.fallo)); }
    li.appendChild(box);
    if(e.coste && e.coste!=='-'){ li.appendChild($('span','right small muted', e.coste)); }
    ul.appendChild(li);
  });
  c.appendChild(ul);
  return c;
}

// Una tarjeta (reutilizada por ambas lentes). opts.flujo+opts.colKey → botones ◀▶ (vista estado);
// sin flujo → botón ✓ "marcar hecha" (vista fase×día). El movimiento se enruta por tipo (encargo→tareas, hilo→Vega).
function cardEl(cd, opts){
  opts=opts||{};
  const el=$('div','kcard '+(cd.sev||'info')+(cd.tipo==='encargo'?' enc':''));
  if(cd.etiqueta){ el.dataset.etq=cd.etiqueta; const ch=$('span','ketq', cd.etiqueta); ch.style.color=etqColor(cd.etiqueta); el.appendChild(ch); }
  if(cd.nivel){ el.appendChild(flagBadge(cd.nivel)); }
  el.appendChild($('div','kt', cd.titulo));
  if(cd.sub){ el.appendChild($('div','ks', cd.sub)); }
  if(cd.rama){ el.appendChild($('div','ks small muted', '🌿 rama: '+cd.rama)); }
  if(cd.mov && cd.id){
    const nav=$('div','knav');
    if(opts.flujo){
      const i=opts.flujo.indexOf(opts.colKey);
      if(i>0){ nav.appendChild(btnMove(-1,'Mover atrás', ()=>moverCard(cd, opts.flujo[i-1]))); }
      if(i>=0 && i<opts.flujo.length-1){ nav.appendChild(btnMove(1,'Avanzar', ()=>moverCard(cd, opts.flujo[i+1]))); }
    } else if(opts.colKey!=='hecho'){
      nav.appendChild(btnHecho(()=>moverCard(cd,'hecho')));
    }
    if(nav.childNodes.length) el.appendChild(nav);
  }
  return el;
}

// Lente POR ESTADO: columnas por_hacer/en_curso/esperando_ok/hecho/pausa.
function vistaEstado(tb, c){
  const flujo=tb.flujo||['por_hacer','en_curso','esperando_ok','hecho'];
  const grid=$('div','kb');
  (tb.columnas||[]).forEach(col=>{
    const k=$('div','kcol');
    const kh=$('div','kh'); kh.appendChild($('span','knom',col.nombre)); kh.appendChild($('span','kn',col.n)); k.appendChild(kh);
    if(!col.cards || !col.cards.length){ k.appendChild($('div','kempty','—')); }
    (col.cards||[]).forEach(cd=> k.appendChild(cardEl(cd, {flujo:flujo, colKey:col.key})));
    grid.appendChild(k);
  });
  c.appendChild(grid);
}

// Lente FASE × DÍA: filas = fases de la cadena a NED (+ Gestión); columnas = Hoy/Mañana/Semana/Más adelante.
function vistaFaseDia(tb, c){
  const dias=tb.dias||[['hoy','Hoy'],['manana','Mañana'],['semana','Esta semana'],['mas','Más adelante']];
  // las fases clínicas son cabecera de fila → no se repiten como tarjeta en las celdas
  const all=[]; (tb.columnas||[]).forEach(col=> (col.cards||[]).forEach(cd=>{ if(!cd.clinico && !(cd.titulo||'').startsWith('+')) all.push(cd); }));
  const grid=$('div','kfd'); grid.style.gridTemplateColumns='160px repeat('+dias.length+',minmax(150px,1fr))';
  grid.appendChild($('div','kfd-h',''));
  dias.forEach(d=> grid.appendChild($('div','kfd-h', d[1])));
  (tb.fases||[]).forEach(f=>{
    const lab=$('div','kfd-fase'+(f.activa?' activa':''));
    lab.appendChild($('div','kfd-fnom', (f.icono?f.icono+' ':'')+(f.titulo||f.key)));
    if(f.activa){ lab.appendChild($('div','kfd-fact','fase activa')); }
    else if(f.estado){ lab.appendChild($('div','kfd-fest', f.estado)); }
    if(f.sig){ lab.appendChild($('div','kfd-fsig', '→ '+f.sig)); }
    grid.appendChild(lab);
    dias.forEach(d=>{
      const cell=$('div','kfd-cell');
      const cards=all.filter(cd=> (cd.fase||'gestion')===f.key && (cd.dia||'mas')===d[0]).sort((a,b)=>(a.rank||5)-(b.rank||5));
      if(!cards.length){ cell.appendChild($('div','kempty','·')); }
      cards.forEach(cd=> cell.appendChild(cardEl(cd, {colKey:d[0]})));
      grid.appendChild(cell);
    });
  });
  c.appendChild(grid);
}

function tablero(tb){
  const c=card('El Tablero · tus tareas', true);
  if(!tb || tb._error){ c.appendChild(errBox(tb||{})); return c; }
  // añadir encargo: etiqueta + fecha + texto
  const add=$('span','right');
  const sel=document.createElement('select'); sel.className='addetq'; sel.setAttribute('aria-label','Etiqueta del encargo');
  ['NED','Polaris','Gestión'].forEach(e=>{ const o=document.createElement('option'); o.value=e; o.textContent=e; sel.appendChild(o); });
  const fec=document.createElement('input'); fec.type='date'; fec.className='addfec'; fec.title='Fecha límite (opcional)'; fec.setAttribute('aria-label','Fecha límite del encargo (opcional)');
  const inp=document.createElement('input'); inp.type='text'; inp.className='addinp';
  inp.placeholder='+ añadir encargo'; inp.setAttribute('aria-label','Añadir un encargo nuevo');
  inp.onkeydown=(e)=>{ if(e.key==='Enter'){ crearTarea(inp.value, sel.value, fec.value); inp.value=''; fec.value=''; } };
  add.appendChild(sel); add.appendChild(fec); add.appendChild(inp); c.querySelector('h2').appendChild(add);
  // toggle de LENTE: Estado | Fase × Día
  const lt=$('div','filtros');
  [['estado','Estado'],['fase','Fase × Día']].forEach(p=>{ const b=$('button',(p[0]===lente?'sel':''), p[1]); b.onclick=()=>{ lente=p[0]; if(LAST) render(LAST); }; lt.appendChild(b); });
  c.appendChild(lt);
  // filtro por etiqueta
  const fil=$('div','filtros');
  ['Todas'].concat(tb.etiquetas||[]).forEach(e=>{
    const b=$('button',(e===filtroEtq?'sel':''), e); b.dataset.e=e;
    if(e!=='Todas'){ b.style.color=etqColor(e); }
    b.onclick=()=>{ filtroEtq=e; fil.querySelectorAll('button').forEach(x=>x.classList.toggle('sel', x.dataset.e===e)); aplicarFiltroEtq(); };
    fil.appendChild(b);
  });
  c.appendChild(fil);
  // apartado "Para hoy"
  if(tb.hoy && tb.hoy.length){
    const hb=$('div','khoy'); hb.appendChild($('div','khoy-h','Para hoy · '+tb.hoy.length));
    const ul=document.createElement('ul');
    tb.hoy.forEach(it=>{ const li=document.createElement('li');
      if(it.nivel){ li.appendChild(flagBadge(it.nivel)); }
      if(it.etiqueta){ const ch=$('span','ketq', it.etiqueta); ch.style.color=etqColor(it.etiqueta); ch.style.marginBottom='0'; li.appendChild(ch); }
      li.appendChild($('span',null, it.titulo));
      const cu=$('span','cuando', it.vencido?'vencido':'hoy'); cu.style.color=it.vencido?'var(--bad)':'var(--gold)';
      li.appendChild(cu); ul.appendChild(li);
    });
    hb.appendChild(ul); c.appendChild(hb);
  }
  // cuerpo según la lente
  if(lente==='fase'){ vistaFaseDia(tb, c); } else { vistaEstado(tb, c); }
  return c;
}

let pestana='tablero';   // pestaña activa: 'tablero' (tus tareas) o 'estado' (monitor del sistema); persiste
let bioGrupo='hematologia';   // grupo de biomarcadores seleccionado; persiste entre refrescos

function pipeline(p){
  // Tablero del flujo biopsia → vacuna: etapas + gates de calidad. SOLO-LECTURA: no hay botones,
  // no se mueve nada con un clic (un gate clínico lo pasa un humano cualificado, no el tablero).
  const c=card('Pipeline biopsia → vacuna · estado del flujo', true);
  if(!p || p._error){ c.appendChild(errBox(p||{})); return c; }
  if(!p.etapas || !p.etapas.length){
    c.appendChild($('div','small muted','Tablero sin sembrar. Arranca: python3 tools/pipeline_vacuna.py init'));
    return c;
  }
  const sub = p.etapas_hechas+'/'+p.n_etapas+' etapas · gates ✓'+p.gates_ok
            + (p.gates_fallo? ' ✗'+p.gates_fallo : '') + ' ·'+p.gates_pendiente+' pendientes';
  c.querySelector('h2').appendChild($('span','right small muted', sub));
  const rail=$('div','pl-rail');
  const EST={pendiente:'·', en_curso:'▸', hecho:'✓', bloqueado:'■'};
  p.etapas.forEach(e=>{
    const esAct = (e.id===p.etapa_actual);
    const cls = 'pl-etapa'+(e.estado==='hecho'?' done':'')+(esAct?' act':'');
    const row=$('div',cls);
    const bcls='pl-badge'+(e.estado==='hecho'?' done':(e.estado==='bloqueado'?' block':(esAct?' act':'')));
    row.appendChild($('div',bcls, e.id));
    const body=$('div','pl-body');
    const nom=$('div','pl-nom', e.nombre);
    nom.appendChild($('span','right small muted', EST[e.estado]||'·'));
    body.appendChild(nom);
    if(e.que) body.appendChild($('div','pl-que', e.que));
    const gs=$('div','pl-gates');
    (e.gates||[]).forEach(g=>{
      const gc=$('span','pl-gate '+(g.estado||'pendiente'));
      gc.appendChild($('span','gi'));
      gc.appendChild($('span',null,g.nombre));
      // el umbral GENÉRICO va en el title (tooltip), sin saturar; nunca un valor del paciente
      if(g.umbral) gc.title = g.umbral + (g.nota? ' — '+g.nota : '');
      gs.appendChild(gc);
    });
    body.appendChild(gs);
    if(e.nota) body.appendChild($('div','small muted', e.nota));
    row.appendChild(body);
    rail.appendChild(row);
  });
  c.appendChild(rail);
  // caveat canónico (una sola vez): qué es y qué NO es este tablero.
  const ban=$('div','pl-banner');
  ban.appendChild($('span',null, p.encuadre || 'Apoyo a la decisión, no consejo médico.'));
  ban.appendChild($('span','muted',
    '  ·  Guarda el estado del flujo y umbrales genéricos; los valores clínicos crudos viven fuera de git.'));
  c.appendChild(ban);
  return c;
}

function erroresCard(er){
  const c=card('Errores del sistema · cola + vigía + fallos', false);
  if(!er||er._error){ c.appendChild(errBox(er||{})); return c; }
  const ul=$('ul','clean');
  // Cola
  const cola=er.cola||{};
  if(!cola._error){
    const li=$('li');
    li.appendChild($('span',null,'Cola'));
    const txt=[(cola.pending||0)+' en espera', (cola.processing||0)+' procesando',
               (cola.failed||0)+' fallidos'].join(' · ');
    li.appendChild($('span','right small '+(cola.failed>0?'err':'muted'), txt));
    ul.appendChild(li);
  }
  // Saldo
  const sal=er.saldo||{};
  if(!sal._error){
    const li=$('li');
    li.appendChild($('span',null,'Saldo'));
    li.appendChild($('span','right small '+(sal.ok?'muted':'err'), sal.ok?'disponible':(sal.motivo||'bloqueado')));
    ul.appendChild(li);
  }
  // Procesos frente al límite (13-sep-26): anticipa el «fork failed» que deja la shell muerta.
  const pr=er.procesos||{};
  if(!pr._error && pr.nivel){
    const li=$('li');
    li.appendChild($('span',null,'Procesos'));
    const PR_CLS={ok:'muted', aviso:'warn', alerta:'err'};
    const pct=(pr.pct===null||pr.pct===undefined)?'?':pr.pct+' %';
    let txt=pr.usados+' de '+pr.limite+' ('+pct+')';
    if(pr.chrome_headless){ txt+=' · '+pr.chrome_headless+' Chrome sin ventana'; }
    li.appendChild($('span','right small '+(PR_CLS[pr.nivel]||'muted'), txt));
    if(pr.consejo){ li.title=pr.consejo; }
    ul.appendChild(li);
    if(pr.nivel!=='ok' && pr.consejo){
      const liC=$('li'); liC.appendChild($('span','small '+(PR_CLS[pr.nivel]||'muted'), pr.consejo)); ul.appendChild(liC);
    }
  }
  c.appendChild(ul);
  // Fallos recientes con severidad y marca AUTO/ESPERA
  const fallos=(er.fallos_recientes||[]);
  if(fallos.length){
    const d=document.createElement('details'); d.appendChild($('summary',null,'Fallos recientes ('+fallos.length+')'));
    const fu=$('ul','clean');
    const SEV_CLS={goal:'bad',operativo:'err',degradado:'warn',config:'muted',transitorio:'muted'};
    fallos.forEach(f=>{
      const li=$('li');
      li.appendChild($('span','dot '+(f.auto_ok?'on':'off')));
      const box=$('span');
      const sev=f.severidad||'?';
      box.appendChild($('span','small '+(SEV_CLS[sev]||'muted'), '['+sev+'] '));
      box.appendChild($('span',null,(f.agente||'?')+'/'+f.job));
      if(f.error_type){ box.appendChild($('div','small err', f.error_type)); }
      li.appendChild(box);
      const lbl=$('span','right small muted', f.auto_ok?'auto-ok':'espera');
      lbl.title=f.auto_ok?'El sistema se recuperó solo':'Puede requerir intervención';
      li.appendChild(lbl);
      fu.appendChild(li);
    });
    d.appendChild(fu); c.appendChild(d);
  }
  // Anomalías vivas del vigía
  const anoms=(er.anomalias_vigia||[]);
  const vivas=anoms.filter(a=>!a.auto_resuelta);
  if(vivas.length){
    const d2=document.createElement('details'); d2.appendChild($('summary',null,'Anomalías vigía ('+vivas.length+' vivas)'));
    const au=$('ul','clean');
    vivas.forEach(a=>{
      const li=$('li'); li.appendChild($('span','dot off'));
      const box=$('span');
      box.appendChild($('span',null,a.tipo));
      box.appendChild($('div','small muted',a.detalle));
      li.appendChild(box);
      if(a.ts){ li.appendChild($('span','right small muted',a.ts)); }
      au.appendChild(li);
    });
    d2.appendChild(au); c.appendChild(d2);
  } else {
    c.appendChild($('div','small muted','Vigía: sin anomalías vivas.'));
  }
  // Acuses de salud ("visto, en ello" — tools/salud.py, 3/7/26): quién se puso y cuándo.
  const acuses=(er.acuses_salud||[]);
  if(acuses.length){
    const d3=document.createElement('details'); d3.appendChild($('summary',null,'Acuses de salud ('+acuses.length+')'));
    const ac=$('ul','clean');
    const AC_CLS={en_arreglo:'warn', resuelto:'muted', detectado:'err'};
    acuses.forEach(a=>{
      const li=$('li');
      li.appendChild($('span','dot '+(a.estado==='resuelto'?'on':'off')));
      const box=$('span');
      box.appendChild($('span','small '+(AC_CLS[a.estado]||'muted'), '['+a.estado+'] '));
      box.appendChild($('span',null, a.clave));
      if(a.nota){ box.appendChild($('div','small muted', a.nota)); }
      li.appendChild(box);
      const edad = a.edad_min!=null ? (a.edad_min<60 ? Math.round(a.edad_min)+'m' : Math.round(a.edad_min/60)+'h') : '?';
      const lbl=$('span','right small muted', (a.por||'?')+' · hace '+edad);
      li.appendChild(lbl);
      ac.appendChild(li);
    });
    d3.appendChild(ac); c.appendChild(d3);
  }
  return c;
}

function trazas(tr){
  const c=card('Trazas de ejecución (observabilidad)', false);
  if(tr._error){ c.appendChild(errBox(tr)); return c; }
  const h=tr.hoy||{};
  const top=$('div','row');
  top.appendChild($('span','big', h.total||0));
  const tasa = (h.fallos&&h.total)? ' · '+h.tasa_fallo_pct+'% fallo' : '';
  top.appendChild($('span','muted small', ' ejecuciones hoy · '+( h.ok||0)+' ok · '+(h.fallos||0)+' fallos'+tasa));
  c.appendChild(top);
  if(h.dur_media_ms!=null){ c.appendChild($('div','small muted', 'media '+h.dur_media_ms+' ms · máx '+h.dur_max_ms+' ms')); }
  const fallos=tr.fallos_recientes||[];
  if(fallos.length){
    const d=document.createElement('details'); d.appendChild($('summary',null,'Fallos recientes ('+fallos.length+')'));
    const ul=$('ul','clean');
    fallos.forEach(f=>{
      const li=$('li');
      li.appendChild($('span','dot off'));
      const box=$('span');
      box.appendChild($('span',null,f.agente+'/'+f.job));
      if(f.error_type){ box.appendChild($('div','small err', f.error_type)); }
      li.appendChild(box);
      if(f.dur_ms!=null){ li.appendChild($('span','right small muted', f.dur_ms+'ms')); }
      ul.appendChild(li);
    });
    d.appendChild(ul); c.appendChild(d);
  } else if(h.total>0){
    c.appendChild($('div','small muted','Sin fallos hoy.'));
  }
  const ults=tr.ultimas||[];
  if(ults.length){
    const d2=document.createElement('details'); d2.appendChild($('summary',null,'Últimas '+ults.length+' ejecuciones'));
    const ul=$('ul','clean');
    ults.forEach(u=>{
      const li=$('li');
      li.appendChild($('span','dot '+(u.resultado==='ok'?'on':'off')));
      const box=$('span');
      const ts=(u.ts||'').slice(11,19)||'?';
      box.appendChild($('span','small muted', ts+' '));
      box.appendChild($('span',null, u.agente+'/'+u.job));
      li.appendChild(box);
      const meta=[(u.dur_ms!=null?u.dur_ms+'ms':''),(u.eur!=null?'$'+u.eur:'')].filter(Boolean).join(' · ');
      if(meta){ li.appendChild($('span','right small muted', meta)); }
      ul.appendChild(li);
    });
    d2.appendChild(ul); c.appendChild(d2);
  }
  if(!h.total){ c.appendChild($('div','small muted','Sin trazas aún. Instrumenta una tool con traza(agente, job).')); }
  return c;
}

function intColor(t){ return t==='integrativo'?'var(--star)': t==='radioterapia'?'var(--gold)': t==='sistemico'?'#5aa9ff':'var(--mut)'; }

function bioChart(a, interv, dom){
  const W=300, H=120, padL=8, padR=8, padT=6, padB=14;
  const box=$('div','biochart');
  const head=$('div','biohead');
  head.appendChild($('span','bioname', a.nombre));
  const pts=a.pts||[];
  const last=pts.length? pts[pts.length-1] : null;
  if(last){ head.appendChild($('span','bioval'+(last[2]?' bad':''), last[1]+(a.unidad?(' '+a.unidad):''))); }
  box.appendChild(head);
  let lo=Infinity, hi=-Infinity;
  pts.forEach(p=>{ lo=Math.min(lo,p[1]); hi=Math.max(hi,p[1]); });
  if(a.ref){ lo=Math.min(lo,a.ref.low); hi=Math.max(hi,a.ref.high); }
  if(!isFinite(lo)){ lo=0; hi=1; }
  if(lo===hi){ hi=lo+1; }
  const m=(hi-lo)*0.12; lo-=m; hi+=m;
  const X=t=> padL + (W-padL-padR)*((t-dom[0])/(dom[1]-dom[0]||1));
  const Y=v=> padT + (H-padT-padB)*(1-(v-lo)/(hi-lo));
  const svg=svgEl('svg',{viewBox:'0 0 '+W+' '+H, class:'biosvg', preserveAspectRatio:'none'});
  if(a.ref){
    const yh=Y(a.ref.high), yl=Y(a.ref.low);
    svg.appendChild(svgEl('rect',{x:padL, y:yh, width:W-padL-padR, height:Math.max(1,yl-yh), fill:'var(--ok)', 'fill-opacity':0.08}));
    [a.ref.low,a.ref.high].forEach(v=> svg.appendChild(svgEl('line',{x1:padL, y1:Y(v), x2:W-padR, y2:Y(v), stroke:'var(--ok)', 'stroke-opacity':0.28, 'stroke-dasharray':'2 3'})));
  }
  (interv||[]).forEach(iv=>{ const t=Date.parse(iv.fecha); if(isNaN(t)||t<dom[0]||t>dom[1]) return;
    svg.appendChild(svgEl('line',{x1:X(t), y1:padT, x2:X(t), y2:H-padB, stroke:intColor(iv.tipo), 'stroke-opacity':0.55, 'stroke-width':1, 'stroke-dasharray':'3 3'})); });
  if(pts.length){
    // rompe la línea en huecos largos (>120 días): no implicar continuidad de datos que no hay
    const GAP=120*864e5; let dpath=''; let prevT=null;
    pts.forEach(p=>{ const t=Date.parse(p[0]); const cmd=(prevT===null || (t-prevT)>GAP)?'M':'L';
      dpath += cmd+X(t).toFixed(1)+' '+Y(p[1]).toFixed(1)+' '; prevT=t; });
    svg.appendChild(svgEl('path',{d:dpath, fill:'none', stroke:'var(--tx)', 'stroke-width':1.3, 'stroke-opacity':0.85}));
    pts.forEach(p=> svg.appendChild(svgEl('circle',{cx:X(Date.parse(p[0])), cy:Y(p[1]), r:p[2]?2.4:1.5, fill:p[2]?'var(--bad)':'var(--star)'})));
  }
  box.appendChild(svg);
  return box;
}

function biomarcadores(d){
  const c=card('🩸 Biomarcadores · evolución', true);
  if(!d || d._error){ c.appendChild(errBox(d||{})); return c; }
  c.appendChild($('div','ks small muted',
    (d.n_analiticas||0)+' analíticas'+(d.rango_fechas? ' · '+d.rango_fechas[0]+' → '+d.rango_fechas[1]:'')+
    (d.por_confirmar_n? ' · '+d.por_confirmar_n+' por confirmar':'')+' · banda = rango orientativo (manda el flag del laboratorio) · apoyo a la decisión, no consejo médico'));
  const groups=d.grupos||{}; const keys=Object.keys(groups);
  if(!keys.length){ c.appendChild($('div','small muted','Sin datos. Corre tools/biomarcadores.py build.')); return c; }
  if(!keys.includes(bioGrupo)) bioGrupo=keys[0];
  const sel=$('div','biogrp');
  keys.forEach(k=>{ const b=$('button','tab'+(k===bioGrupo?' sel':''), groups[k].nombre);
    b.onclick=()=>{ bioGrupo=k; if(LAST) render(LAST); }; sel.appendChild(b); });
  c.appendChild(sel);
  const interv=d.intervenciones||[];
  if(interv.length){
    const leg=$('div','bioleg small muted'); leg.appendChild($('span',null,'Marcas: '));
    interv.forEach(iv=>{ const s=$('span','biolegitem'); const dot=$('span','biodot'); dot.style.background=intColor(iv.tipo);
      s.appendChild(dot); s.appendChild($('span',null, iv.nombre+(iv.por_confirmar?' (?)':''))); leg.appendChild(s); });
    c.appendChild(leg);
  }
  const r=d.rango_fechas; const dom=[ r?Date.parse(r[0]):0, r?Date.parse(r[1]):1 ];
  if(!(dom[1]>dom[0])) dom[1]=dom[0]+1;
  const wrap=$('div','biocharts');
  (groups[bioGrupo].analitos||[]).forEach(a=> wrap.appendChild(bioChart(a, interv, dom)));
  c.appendChild(wrap);
  return c;
}

function render(d){
  LAST=d;
  document.getElementById('sub').textContent = 'al '+d.generado+'  ·  móvil: '+ (d.urls&&d.urls.movil||'');
  const halt = d.halt && d.halt.halt;
  const hp=document.getElementById('halt'); hp.textContent = halt? '🔴 PARADO (HALT)':'🟢 vivo'; hp.className='pill '+(halt?'bad':'ok');
  // pestañas: separan TUS TAREAS del monitor del sistema ({{TITULAR}}, 22/6: el estado general estorba con las tareas)
  const tabs=document.getElementById('tabs'); tabs.innerHTML='';
  [['tablero','📋 Tareas'],['estado','🩺 Estado del sistema']].forEach(p=>{
    const b=$('button','tab'+(p[0]===pestana?' sel':''), p[1]);
    b.onclick=()=>{ pestana=p[0]; if(LAST) render(LAST); };
    tabs.appendChild(b);
  });
  const g=document.getElementById('grid'); g.innerHTML='';
  if(pestana==='tablero'){
    g.appendChild(tablero(d.tablero));
    aplicarFiltroEtq();   // respeta el filtro de etiqueta tras cada refresco
  } else {
    g.appendChild(pipeline(d.pipeline));   // misión nº1: el camino biopsia→vacuna, lo primero
    g.appendChild(gauge(d.config));
    g.appendChild(gasto(d.gasto));
    g.appendChild(salud(d.salud));
    g.appendChild(biomarcadores(d.biomarcadores));
    g.appendChild(ritmo(d.ritmo));
    g.appendChild(erroresCard(d.errores||{}));
    g.appendChild(trazas(d.trazas||{}));
    g.appendChild(rutinas(d.rutinas));
    g.appendChild(cajas(d.cajas));
    g.appendChild(agentes(d.agentes));
    g.appendChild(sesiones(d.sesiones));
    g.appendChild(hilos(d.hilos));
    g.appendChild(actividad(d.actividad));
  }
  document.getElementById('foot').textContent='El Observatorio · solo-lectura · '+(d.urls&&d.urls.local||'');
}

async function tick(){
  try{ const r=await fetch('/api/estado',{cache:'no-store'}); const d=await r.json();
    if(d.build && BUILD && d.build!==BUILD){ location.reload(); return; }  // el servidor se actualizó → recarga sola
    render(d); }
  catch(e){ document.getElementById('sub').textContent='sin conexión con el servidor…'; }
}
document.getElementById('btn-refresh').onclick=tick;
document.getElementById('btn-copy').onclick=()=>{
  if(!LAST) return;
  const c=LAST.config||{}, g=LAST.gasto||{}, rt=LAST.rutinas||{}, h=LAST.hilos||{};
  const t='Observatorio de Polaris ('+LAST.generado+')\n'
    +'Estado: '+(c.overall!=null?c.overall+'/100':'?')+'\n'
    +'Gasto hoy: '+eur(g.hoy_usd)+' de '+eur(g.tope_usd)+'\n'
    +'Rutinas vivas: '+(rt.cargadas||0)+'/'+(rt.total||0)+'\n'
    +'Esperan tu OK: '+(h.pendientes_ok||0)+' · hilos cayéndose: '+(h.n||0)+'\n'
    +'Falta: '+((c.pendientes||[]).join(', ')||'nada')+'\n';
  navigator.clipboard&&navigator.clipboard.writeText(t);
  const b=document.getElementById('btn-copy'); const old=b.textContent; b.textContent='✓ copiado'; setTimeout(()=>b.textContent=old,1400);
};
tick(); setInterval(tick, 15000);
</script>
</body>
</html>
"""


# ── El sitio de síntomas ─────────────────────────────────────────────────────
# Página propia, sencilla y accesible, para que {{TITULAR}} apunte cómo se encuentra desde el
# móvil (vía Tailscale) en segundos. Reusa la paleta del Observatorio (design system).
# Registro de APOYO, no consejo médico: apunta y ordena, no interpreta. Escribe en el
# diario LOCAL (tools/sintomas.py → _PRIVADO_CLINICO, gitignored); nada sale hacia fuera.
PAGE_SINTOMAS = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="tablero-token" content="__CSRF_TOKEN__">
<title>Síntomas</title>
<style>
  :root{
    --bg:#080b16; --bg2:#0d1224; --card:#121a30; --line:#1f2a47;
    --tx:#e8ecf7; --mut:#8a94b0; --gold:#ffd479; --star:#aa7bff;
    --ok:#3fd17a; --warn:#ffb454; --bad:#ff5d6c; --grey:#4a536e;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:radial-gradient(1200px 700px at 80% -10%, #15224a 0%, var(--bg) 55%) fixed, var(--bg);
    color:var(--tx); font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    padding:18px; max-width:640px; margin:0 auto; -webkit-text-size-adjust:100%; overflow-x:hidden;
  }
  header{display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:4px}
  h1{font-size:21px; margin:0; letter-spacing:.3px}
  h1 .ic{color:var(--gold)}
  .sub{color:var(--mut); font-size:12.5px}
  a.volver{margin-left:auto; color:var(--mut); text-decoration:none; font-size:13px; border:1px solid var(--line); border-radius:9px; padding:7px 11px}
  .card{background:linear-gradient(180deg,var(--card),var(--bg2)); border:1px solid var(--line); border-radius:14px; padding:16px; margin-top:14px}
  label{display:block; font-size:13px; color:var(--mut); margin:2px 0 6px}
  input[type=text], textarea{
    width:100%; background:var(--bg2); color:var(--tx); border:1px solid var(--line);
    border-radius:11px; padding:13px 13px; font-size:17px; font-family:inherit;
  }
  textarea{min-height:64px; resize:vertical}
  input::placeholder, textarea::placeholder{color:#566085}
  .ints{display:grid; grid-template-columns:repeat(6,1fr); gap:7px; margin-top:2px}
  .ints button{
    min-height:46px; width:100%; border-radius:11px; border:1px solid var(--line);
    background:var(--bg2); color:var(--tx); font-size:16px; cursor:pointer;
  }
  .ints button[aria-pressed=true]{background:var(--gold); color:#1a1300; border-color:var(--gold); font-weight:700}
  .hint{font-size:12px; color:var(--mut); margin-top:7px}
  .row{margin-top:14px}
  details{margin-top:12px}
  summary{cursor:pointer; color:var(--gold); font-size:14px; list-style:none}
  summary::-webkit-details-marker{display:none}
  .det-grid{margin-top:10px}
  .guardar{
    width:100%; margin-top:16px; min-height:52px; border:none; border-radius:13px;
    background:var(--ok); color:#04210f; font-size:18px; font-weight:700; cursor:pointer;
  }
  .guardar:disabled{opacity:.5; cursor:default}
  .toast{margin-top:12px; padding:12px 14px; border-radius:11px; font-size:14px; display:none}
  .toast.ok{display:block; background:rgba(63,209,122,.12); border:1px solid #1c5; color:var(--ok)}
  .toast.flag{display:block; background:rgba(255,180,84,.12); border:1px solid #a76; color:var(--warn)}
  h2{font-size:15px; margin:22px 0 4px; color:var(--mut); font-weight:600}
  .dia{font-size:12.5px; color:var(--gold); margin:14px 0 6px}
  .item{display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-top:1px solid var(--line)}
  .item .h{color:var(--mut); font-size:13px; min-width:42px; flex:none}
  .item .s{flex:1; min-width:0; overflow-wrap:anywhere}
  .item .i{color:var(--mut); font-size:13px; white-space:nowrap; display:flex; align-items:center; gap:7px; flex:none}
  .item .i .bar{display:inline-block; width:48px; height:7px; border-radius:4px; background:var(--line); overflow:hidden}
  .item .i .bar>span{display:block; height:100%}
  .item .extra{color:var(--mut); font-size:13px; margin-top:3px}
  .flag{color:var(--warn)}
  .vacio{color:var(--mut); font-size:14px; padding:14px 0}
  .pie{color:var(--mut); font-size:11.5px; margin-top:26px; line-height:1.6}
</style>
</head>
<body>
<header>
  <h1><span class="ic">✦</span> Síntomas</h1>
  <span class="sub">cómo te encuentras, datado</span>
  <a class="volver" href="/">← Volver</a>
</header>

<div class="card">
  <label for="sintoma">¿Qué notas?</label>
  <input type="text" id="sintoma" placeholder="p. ej. dolor de cabeza, cansancio, náuseas…" autocomplete="off" autofocus>

  <div class="row">
    <label>Intensidad <span style="color:var(--grey)">(opcional, 0–10)</span></label>
    <div class="ints" id="ints" role="group" aria-label="Intensidad de 0 a 10"></div>
  </div>

  <details id="mas">
    <summary>+ Añadir detalle (opcional)</summary>
    <div class="det-grid">
      <div class="row"><label for="zona">¿Dónde?</label>
        <input type="text" id="zona" placeholder="zona del cuerpo" autocomplete="off"></div>
      <div class="row"><label for="desde">¿Desde cuándo?</label>
        <input type="text" id="desde" placeholder="p. ej. esta mañana, 2 días" autocomplete="off"></div>
      <div class="row"><label for="notas">Notas</label>
        <textarea id="notas" placeholder="lo que quieras añadir"></textarea></div>
    </div>
  </details>

  <button class="guardar" id="guardar">Guardar</button>
  <div class="toast" id="toast"></div>
</div>

<h2>Lo apuntado</h2>
<div id="lista"><div class="vacio">cargando…</div></div>

<div class="pie">
  Registro personal de apoyo, no consejo médico. Es para que lo lleves a tus consultas.
  Privado y en local. Si algo es urgente o te asusta, llama a tu equipo o a urgencias.
</div>

<script>
const TOKEN=(document.querySelector('meta[name=tablero-token]')||{}).content||'';
const $=id=>document.getElementById(id);
let intensidad=null;

// Botonera de intensidad 0–10
const cont=$('ints');
for(let n=0;n<=10;n++){
  const b=document.createElement('button');
  b.type='button'; b.textContent=n; b.setAttribute('aria-pressed','false');
  b.onclick=()=>{
    if(intensidad===n){ intensidad=null; b.setAttribute('aria-pressed','false'); return; }
    intensidad=n;
    [...cont.children].forEach(x=>x.setAttribute('aria-pressed','false'));
    b.setAttribute('aria-pressed','true');
  };
  cont.appendChild(b);
}

function toast(msg, cls){ const t=$('toast'); t.className='toast '+cls; t.textContent=msg; }

async function guardar(){
  const sintoma=$('sintoma').value.trim();
  if(!sintoma){ $('sintoma').focus(); return; }
  const cuerpo={sintoma, intensidad,
    zona:$('zona').value.trim(), desde:$('desde').value.trim(), notas:$('notas').value.trim()};
  const btn=$('guardar'); btn.disabled=true;
  try{
    const r=await fetch('/api/sintoma/crear',{method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json','X-Tablero-Token':TOKEN}, body:JSON.stringify(cuerpo)});
    const d=await r.json();
    if(!r.ok){ toast(d.error||'no se pudo guardar', 'flag'); btn.disabled=false; return; }
    if(d.entrada && d.entrada.alarma){
      toast('Apuntado. Esto conviene que tu equipo lo sepa pronto.', 'flag');
    }else{
      toast('Apuntado ✓', 'ok');
    }
    $('sintoma').value=''; $('zona').value=''; $('desde').value=''; $('notas').value='';
    intensidad=null; [...cont.children].forEach(x=>x.setAttribute('aria-pressed','false'));
    $('mas').open=false; $('sintoma').focus();
    cargar();
  }catch(e){ toast('sin conexión con el sitio', 'flag'); }
  btn.disabled=false;
}
$('guardar').onclick=guardar;
$('sintoma').addEventListener('keydown', e=>{ if(e.key==='Enter') guardar(); });

async function cargar(){
  try{
    const r=await fetch('/api/sintomas?dias=14',{cache:'no-store'});
    const ent=await r.json();
    const L=$('lista'); L.innerHTML='';
    if(!ent.length){ L.innerHTML='<div class="vacio">Aún no has apuntado nada.</div>'; return; }
    let dia=null;
    ent.forEach(e=>{
      if(e.fecha!==dia){ dia=e.fecha; const h=document.createElement('div'); h.className='dia'; h.textContent=e.fecha; L.appendChild(h); }
      const it=document.createElement('div'); it.className='item';
      const h=document.createElement('div'); h.className='h'; h.textContent=e.hora;
      const s=document.createElement('div'); s.className='s';
      s.innerHTML=(e.alarma?'<span class="flag">🚩 </span>':'')+escapar(e.sintoma);
      const extra=[]; if(e.zona)extra.push(e.zona); if(e.desde)extra.push('desde '+e.desde); if(e.notas)extra.push(e.notas);
      if(extra.length){ const ex=document.createElement('div'); ex.className='extra'; ex.textContent=extra.join(' · '); s.appendChild(ex); }
      const i=document.createElement('div'); i.className='i';
      if(e.intensidad!=null){
        const col=e.intensidad<=3?'var(--ok)':(e.intensidad<=6?'var(--gold)':'var(--bad)');
        i.innerHTML='<span class="bar"><span style="width:'+(e.intensidad*10)+'%;background:'+col+'"></span></span>'+e.intensidad+'/10';
      }
      it.appendChild(h); it.appendChild(s); it.appendChild(i); L.appendChild(it);
    });
  }catch(e){ $('lista').innerHTML='<div class="vacio">no se pudo cargar</div>'; }
}
function escapar(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
cargar();
</script>
</body>
</html>
"""


# ── La vista Calma ───────────────────────────────────────────────────────────
# Vista ADITIVA y opt-in (/calma). Misma paleta y mismos datos que el índice
# (reusa /api/estado), pero enseña SOLO lo poco que quita agobio de un vistazo:
# cuántas sesiones hay abiertas (lo que dispara el gasto y el lío), cuánto se ha
# gastado hoy, qué espera tu OK y qué hilos se caen. No toca la vista completa.
PAGE_CALMA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#080b16">
<title>Calma · Polaris</title>
<style>
  :root{
    --bg:#080b16; --bg2:#0d1224; --card:#121a30; --line:#1f2a47;
    --tx:#e8ecf7; --mut:#9aa4c0; --star:#aa7bff; --gold:#ffd479;
    --ok:#3fd17a; --warn:#ffb454; --bad:#ff5d6c; --grey:#4a536e;
  }
  *{box-sizing:border-box}
  body{margin:0; min-height:100vh;
    background:radial-gradient(1200px 700px at 80% -10%, #15224a 0%, var(--bg) 55%) fixed, var(--bg);
    color:var(--tx); font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    display:flex; flex-direction:column; align-items:center; padding:32px 20px 48px}
  header{width:100%; max-width:620px; margin-bottom:26px}
  h1{font-size:22px; margin:0 0 2px; letter-spacing:.3px; font-weight:600}
  .sub{color:var(--mut); font-size:13px}
  main{width:100%; max-width:620px; display:grid; gap:22px}
  section.card{background:linear-gradient(180deg,var(--card),var(--bg2)); border:1px solid var(--line);
    border-radius:16px; padding:20px 22px}
  section.card > h2{margin:0 0 14px; font-size:12px; text-transform:uppercase; letter-spacing:1px;
    color:var(--mut); font-weight:600}
  ul{list-style:none; margin:0; padding:0}
  .task{display:flex; gap:12px; padding:12px 0; border-top:1px solid var(--line)}
  .task:first-child{border-top:none; padding-top:0}
  .dot{width:8px; height:8px; border-radius:50%; margin-top:7px; flex:0 0 auto; background:var(--grey)}
  .dot.bad{background:var(--bad)} .dot.gold{background:var(--gold)} .dot.info{background:var(--star)}
  .task .body{flex:1; min-width:0}
  .task .t{color:var(--tx); font-size:15px; line-height:1.4}
  .task .s{color:var(--mut); font-size:13px; margin-top:2px;
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden}
  .tag{display:inline-block; font-size:11px; color:var(--mut); border:1px solid var(--line);
    border-radius:999px; padding:1px 8px; margin-left:8px; vertical-align:middle}
  .rama{display:flex; justify-content:space-between; align-items:baseline; gap:12px;
    padding:11px 0; border-top:1px solid var(--line)}
  .rama:first-child{border-top:none; padding-top:0}
  .rama code{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--gold); word-break:break-all}
  .rama .meta{color:var(--mut); font-size:12px; white-space:nowrap; flex:0 0 auto}
  .vacio{color:var(--ok); font-size:14px}
  .foot{max-width:620px; width:100%; margin-top:24px; text-align:center}
  .foot a{color:var(--star); text-decoration:none; font-size:14px; border:1px solid var(--line);
    padding:9px 18px; border-radius:999px; display:inline-block; transition:.15s}
  .foot a:hover{border-color:var(--star)}
  .stamp{color:var(--mut); font-size:12px; text-align:center; margin-top:14px}
</style>
</head>
<body>
<header>
  <h1>Calma</h1>
  <div class="sub" id="sub">cargando…</div>
</header>
<main id="app">
  <section class="card">
    <h2>Lo que tienes que hacer</h2>
    <ul id="tareas"><li class="vacio">cargando…</li></ul>
  </section>
  <section class="card">
    <h2>Ramas huérfanas por cerrar</h2>
    <ul id="huerfanas"><li class="vacio">cargando…</li></ul>
  </section>
</main>
<div class="foot"><a href="/">Ver el Observatorio completo →</a></div>
<div class="stamp" id="stamp"></div>
<script>
const $=id=>document.getElementById(id);
function esc(s){ const d=document.createElement('div'); d.textContent=(s==null?'':s); return d.innerHTML; }
function render(d){
  // --- Tus tareas: columnas "Por hacer" + "En curso" del tablero de Vega (nada técnico) ---
  const tb=(d.tablero&&d.tablero.columnas)||[];
  const quiero={por_hacer:1, en_curso:1};
  let cards=[];
  tb.forEach(col=>{ if(quiero[col.key]){ (col.cards||[]).forEach(c=>{ if(c.rank!==99) cards.push(c); }); } });
  cards.sort((a,b)=>(a.rank||5)-(b.rank||5));
  const TOPE=10, extra=cards.length-TOPE; cards=cards.slice(0,TOPE);
  const ul=$('tareas');
  if(!cards.length){ ul.innerHTML='<li class="vacio">Nada pendiente ahora mismo ✨</li>'; }
  else{
    ul.innerHTML = cards.map(c=>{
      const dot=(c.sev==='bad'?'bad':c.sev==='gold'?'gold':'info');
      const tag=c.etiqueta?('<span class="tag">'+esc(c.etiqueta)+'</span>'):'';
      const sub=c.sub?('<div class="s">'+esc(c.sub)+'</div>'):'';
      return '<li class="task"><span class="dot '+dot+'"></span><div class="body"><div class="t">'+esc(c.titulo)+tag+'</div>'+sub+'</div></li>';
    }).join('') + (extra>0?('<li class="task"><span class="dot"></span><div class="body"><div class="s">y '+extra+' más en el Observatorio completo</div></div></li>'):'');
  }
  // --- Ramas huérfanas: worktrees parados con commits sin fusionar, sin sesión viva ---
  const hu=(d.sesiones&&d.sesiones.huerfanas)||[];
  const hl=$('huerfanas');
  if(!hu.length){ hl.innerHTML='<li class="vacio">Ninguna, todo cerrado ✨</li>'; }
  else{
    hl.innerHTML = hu.map(r=>{
      const meta=[];
      if(r.ahead) meta.push(r.ahead+' commit'+(r.ahead>1?'s':'')+' sin fusionar');
      if(r.edad_dias!=null) meta.push(r.edad_dias+'d parada');
      if(r.sin_commitear) meta.push('cambios sueltos');
      return '<li class="rama"><code>'+esc(r.rama)+'</code><span class="meta">'+esc(meta.join(' · '))+'</span></li>';
    }).join('');
  }
  $('sub').textContent='al '+(d.generado||'');
  $('stamp').textContent='se actualiza sola cada 20 s';
}
let BUILD=null;
async function tick(){
  try{ const r=await fetch('/api/estado',{cache:'no-store'}); const d=await r.json();
    if(d.build){ if(BUILD && d.build!==BUILD){ location.reload(); return; } BUILD=d.build; }
    render(d);
  }catch(e){ $('sub').textContent='sin conexión con el servidor…'; }
}
tick(); setInterval(tick, 20000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body):
        # Item #24 (saneamiento 15-jul): el navegador puede cerrar/refrescar la pestaña
        # mientras escribimos la respuesta -> BrokenPipeError/ConnectionResetError. No es
        # una avería del Observatorio (nada que reintentar ni que loguear); sin capturarlo
        # aqui, el do_GET/do_POST de mas abajo reintentaba mandar un 500 por la MISMA
        # conexion ya muerta, y esa segunda escritura fallida es la que subia sin capturar
        # y llenaba observatorio.err de tracebacks (el unico log donde se veria un error real).
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):  # noqa: N802 (API de http.server)
        path = self.path.split("?")[0]
        try:
            if path == "/" or path == "/index.html":
                page = PAGE.replace("__CSRF_TOKEN__", TOKEN).replace("__BUILD__", BUILD)
                self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            elif path == "/sintomas":
                page = PAGE_SINTOMAS.replace("__CSRF_TOKEN__", TOKEN)
                self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            elif path == "/calma":
                self._send(200, "text/html; charset=utf-8", PAGE_CALMA.encode("utf-8"))
            elif path == "/api/estado":
                body = json.dumps(recopilar_todo(), ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            elif path == "/api/sintomas":
                import sintomas
                dias = 14
                q = urlparse(self.path).query
                for kv in q.split("&"):
                    if kv.startswith("dias="):
                        try:
                            dias = int(kv[5:])
                        except ValueError:
                            dias = 14
                body = json.dumps(sintomas.listar(dias=dias), ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            else:
                self._send(404, "text/plain; charset=utf-8", b"no existe")
        except Exception as e:  # noqa: BLE001 — el servidor nunca debe caerse por una petición
            self._send(500, "text/plain; charset=utf-8", ("error: %s" % e).encode("utf-8"))

    def _post_guard(self):
        """Solo deja escribir si trae el token de sesión y (si viene) un Origin/Referer propio.
        El token basta como defensa CSRF: una web ajena no puede leerlo (same-origin) ni mandar
        la cabecera personalizada sin un preflight CORS que aquí no se aprueba."""
        if self.headers.get("X-Tablero-Token", "") != TOKEN:
            return False
        ref = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not ref:
            return True  # herramientas locales (curl con token) — sin navegador de por medio
        host = urlparse(ref).hostname or ""
        return host in ("127.0.0.1", "::1", "localhost", TS_IP, TS_HOST, TS_HOST_CORTO)

    def do_POST(self):  # noqa: N802 (API de http.server)
        path = self.path.split("?")[0]
        try:
            if not self._post_guard():
                return self._send(403, "application/json; charset=utf-8",
                                  '{"error":"guardia: token u origen no válido"}'.encode("utf-8"))
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 8192:
                return self._send(400, "application/json; charset=utf-8",
                                  '{"error":"cuerpo vacío o demasiado grande"}'.encode("utf-8"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/sintoma/crear":
                import sintomas  # efecto LOCAL: apunta en el diario de síntomas (_PRIVADO_CLINICO). Nunca hacia fuera.
                inten = data.get("intensidad", None)
                e = sintomas.add(str(data.get("sintoma", "")),
                                 intensidad=(None if inten in (None, "") else inten),
                                 zona=str(data.get("zona", "")),
                                 desde=str(data.get("desde", "")),
                                 notas=str(data.get("notas", "")),
                                 via="web")
                return self._send(200, "application/json; charset=utf-8",
                                  json.dumps({"ok": True, "entrada": e}, ensure_ascii=False).encode("utf-8"))
            import seguimiento  # ÚNICO efecto de los POST: editar la lista LOCAL de Vega. Nunca salida hacia fuera.
            if path == "/api/tarea/crear" or path == "/api/hilo/crear":
                t = seguimiento.crear_tarea(str(data.get("titulo", "")),
                                            etiqueta=str(data.get("etiqueta", "NED")),
                                            vence=str(data.get("vence", "")),
                                            origen=str(data.get("origen", "tablero")),
                                            hecho_cuando=str(data.get("hecho_cuando", "")))
            elif path == "/api/hilo/mover" or path == "/api/tarea/mover":
                t = seguimiento.set_estado(str(data["id"]), str(data["estado"]))
            else:
                return self._send(404, "text/plain; charset=utf-8", b"no existe")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"ok": True, "tarea": t}, ensure_ascii=False).encode("utf-8"))
        except (KeyError, ValueError) as e:
            self._send(400, "application/json; charset=utf-8",
                       json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
        except Exception as e:  # noqa: BLE001 — el servidor nunca debe caerse por una petición
            self._send(500, "application/json; charset=utf-8",
                       json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))

    def log_message(self, *a):  # silencio (no ensuciar los logs de launchd)
        return


def serve():
    if not _host_es_privado():
        sys.exit("MURO: me niego a escuchar en %s — solo loopback (127.0.0.1)." % HOST)
    # Arranque LIMPIO: si un Observatorio huérfano sigue ocupando el puerto (kickstart -k), lo
    # libera y reintenta el bind, en vez de reventar con OSError 48 y entrar en bucle de reinicio.
    try:
        httpd = portguard.http_server(ThreadingHTTPServer, HOST, PORT, Handler,
                                      markers=("observatorio.py",), log=lambda m: print(m, flush=True))
    except OSError as e:
        sys.exit("El Observatorio no pudo escuchar en %s:%d (%s). ¿Hay otra instancia ajena ahí?" % (HOST, PORT, e))
    print("El Observatorio en http://%s:%d  (móvil vía Tailscale: http://%s:%d)" % (HOST, PORT, TS_HOST, MOVIL_PORT), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


# ───────────────────────── parte diario a Telegram (sustituye el ping de las 8:18) ─────────────────────────
def construir_parte():
    cfg = _safe(estado_config)
    g = _safe(lambda: estado_gasto(coste_sync=True))
    rt = _safe(estado_rutinas)
    cj = _safe(estado_cajas)
    h = _safe(estado_hilos)
    overall = cfg.get("overall", "?")
    lineas = ["✦ El Observatorio de Polaris — %s" % time.strftime("%-d/%-m"), ""]
    lineas.append("Estado general: %s/100." % overall)
    if cfg.get("pendientes"):
        falta = cfg["pendientes"][:3]
        lineas.append("Aún falta: " + ", ".join(falta) + ("…" if len(cfg["pendientes"]) > 3 else "") + ".")
    if not g.get("_error"):
        lineas.append("Gasto hoy: %.2f$ de %.0f$%s." % (g.get("hoy_usd", 0), g.get("tope_usd", 0), " (modo ahorro)" if g.get("conservador") else ""))
    if not rt.get("_error"):
        lineas.append("Rutinas vivas: %d de %d." % (rt.get("cargadas", 0), rt.get("total", 0)))
    if not cj.get("_error"):
        nombres = ", ".join("%s (%s)" % (c["slug"], c["estado"]) for c in cj.get("cajas", [])) or "ninguna aún"
        lineas.append("Cajas: %s." % nombres)
    if not h.get("_error"):
        urgentes = [i for i in h.get("items", []) if i.get("sev") in ("roja", "ambar")]
        if urgentes:
            lineas.append("Cayéndose (%d): lo primero, %s." % (h.get("n", 0), urgentes[0]["titulo"]))
        if h.get("pendientes_ok"):
            lineas.append("Esperan tu OK: %d." % h["pendientes_ok"])
    lineas += ["", "Míralo entero: http://%s:%d" % (TS_HOST, MOVIL_PORT)]
    return "\n".join(lineas)


def enviar_parte(dry=False):
    import salida
    cfg = _safe(estado_config)
    overall = cfg.get("overall", 0) or 0
    texto = construir_parte()
    if not dry and isinstance(overall, int) and overall >= 100:
        # Una celebración NO es un código rojo. Antes iba por alerta_critica, que atraviesa el HALT,
        # el silencio nocturno y la casa de estilo: si el sistema estaba PARADO, Polaris igual te
        # despertaba de madrugada con un 🎉. El canal de socorro se gasta si se usa para fiestas.
        return salida.report_to_titular("🎉 ¡Polaris al 100%! Configuración completa.\n\n" + texto,
                                       fuente="observatorio-parte")
    return salida.report_to_titular(texto, dry=dry, fuente="observatorio-parte")


# ───────────────────────── kiosko: abrir Safari a pantalla completa en Polaris ─────────────────────────
def abrir():
    url = "http://%s:%d" % (HOST, PORT)
    subprocess.run(["open", "-a", "Safari", url])
    time.sleep(2.0)
    # Fuerza la (re)carga del documento al URL: si Safari ya tenía la página abierta, `open` solo
    # la enfoca sin refrescar → reasignar el URL del documento la recarga (trae el JS nuevo).
    osa = (
        'tell application "Safari"\n'
        '  activate\n'
        '  if (count of documents) > 0 then set URL of front document to "%s"\n'
        'end tell\n'
        'delay 0.8\n'
        'tell application "System Events" to tell process "Safari"\n'
        '  try\n'
        '    set value of attribute "AXFullScreen" of window 1 to true\n'
        '  end try\n'
        'end tell\n'
    ) % url
    subprocess.run(["osascript", "-e", osa])
    print("Safari abierto en %s (pantalla completa si hay permiso de Automatización)." % url)


def main(argv):
    cmd = argv[0] if argv else "serve"
    if cmd in ("serve", "server", "run"):
        serve()
    elif cmd == "once":
        print(json.dumps(recopilar_todo(), ensure_ascii=False, indent=2))
    elif cmd == "parte":
        res = enviar_parte(dry=("--dry" in argv or "--send" not in argv))
        print(json.dumps(res, ensure_ascii=False, indent=2) if isinstance(res, dict) else res)
    elif cmd == "abrir":
        abrir()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
