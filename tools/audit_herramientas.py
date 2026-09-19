#!/usr/bin/env python3
"""tools/audit_herramientas.py — Auditoría autónoma de USO + SALUD de herramientas/IAs.

Tapar el agujero de que «instalado» ≠ «vivo» ≠ «usado». Ejemplo concreto: Consensus/scite
marcados MUST en el stack pero sin operar ni usarse en semanas — y nadie avisaba.

PRINCIPIOS:
  · Determinista, $0 tokens, sin LLM.
  · Lee tools/state/herramientas.json (inventario vivo).
  · Cruza inventario × observabilidad × gasto_ledger × call-graph (grep de logs).
  · Clasifica:
      OK            — sonda pasa Y señal de uso dentro de la ventana
      HUERFANA      — sonda pasa PERO sin uso en N días
      MUST_SIN_OPERAR — es_must=true Y (sonda falla O sin uso en N días)
      ROTA          — sonda falla
      NO_VERIFICABLE — la sonda no puede confirmar NI desmentir localmente
                       (conector claude.ai inyectado en sesión, o web tras login).
                       Informativo: se lista pero NUNCA alarma. Si hay uso reciente
                       de un conjunto no verificable → OK (el uso lo demuestra vivo).
  · Avisa por salida.py (anti-spam, HALT-aware) SOLO si hay MUST_SIN_OPERAR o ROTA.
    NO_VERIFICABLE nunca avisa (regla avisos-no-mientan: no afirmar "roto" sin
    haberlo comprobado). En tests/DRY: nunca envía nada de verdad.

CLI:
  python3 tools/audit_herramientas.py           # informe legible
  python3 tools/audit_herramientas.py --json    # JSON crudo
  python3 tools/audit_herramientas.py --dry     # fuerza DRY (no avisa)
  python3 tools/audit_herramientas.py --must    # solo las MUST
  python3 tools/audit_herramientas.py --roto    # solo las que no pasan la sonda

Exit codes:
  0 — todo OK
  1 — hay MUST_SIN_OPERAR o ROTA (útil para enganche a CI / auto-mejora)
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# ─── Resolución de casa base (patrón canónico del repo) ──────────────────────
# BTP_REPO o ~/claudecode. BTP_STATE_DIR permite aislamiento en tests.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")

INVENTARIO_PATH = os.environ.get("BTP_HERRAMIENTAS_JSON") or os.path.join(
    STATE, "herramientas.json"
)
OBS_DIR = os.path.join(STATE, "observabilidad")
LEDGER = os.path.join(REPO, "tools", ".gasto_ledger.jsonl")
LOGS_DIR = os.path.join(REPO, "tools", "launchd", "logs")

# Ventana por defecto para considerar "usada recientemente"
VENTANA_USO_DIAS = int(os.environ.get("BTP_AUDIT_VENTANA_DIAS", "14"))

# Anti-spam: solo re-avisamos de MUST_SIN_OPERAR/ROTA cada N horas
ALERTA_COOLDOWN_H = int(os.environ.get("BTP_AUDIT_COOLDOWN_H", "12"))
ALERTA_STATE = os.path.join(STATE, "audit_herramientas_last_alert.json")

# DRY: nunca enviar aunque haya alertas. Se activa:
#   1. si BTP_AUDIT_DRY=1 en env
#   2. si se pasa --dry en CLI
#   3. si hay HALT (salida.py lo gestiona, pero lo advertimos también aquí)
DRY_ENV = os.environ.get("BTP_AUDIT_DRY", "0") == "1"

# Clasificaciones
OK = "OK"
HUERFANA = "HUERFANA"
MUST_SIN_OPERAR = "MUST_SIN_OPERAR"
ROTA = "ROTA"
# NO_VERIFICABLE — la sonda no puede confirmar NI desmentir localmente (conector
# claude.ai inyectado en la sesión, o web tras login con anti-bot). Informativo:
# se lista en el informe pero NUNCA dispara alerta (regla avisos-no-mientan: no
# afirmamos "roto" lo que no hemos comprobado).
NO_VERIFICABLE = "NO_VERIFICABLE"

# Rutas por defecto donde Claude Code declara servidores MCP locales (además de
# las que traiga cada entrada del inventario). El bug de origen: el inventario
# apuntaba a ~/.claude/claude_desktop_config.json (inexistente); los servidores
# de proyecto viven en ~/claudecode/.mcp.json.
MCP_CONFIG_PATHS_DEFECTO = [
    os.path.join(REPO, ".mcp.json"),
    "~/.claude.json",
    "~/Library/Application Support/Claude/claude_desktop_config.json",
]
# Raíz de transcripciones de Claude Code (evidencia de uso real de un conector).
CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


# ─── Carga del inventario ─────────────────────────────────────────────────────

def cargar_inventario() -> list[dict]:
    try:
        with open(INVENTARIO_PATH, encoding="utf-8") as f:
            datos = json.load(f)
        return datos.get("herramientas", [])
    except FileNotFoundError:
        return []
    except Exception as exc:
        sys.stderr.write(f"audit_herramientas: no pude leer inventario: {exc}\n")
        return []


# ─── Señal de uso ─────────────────────────────────────────────────────────────

def _leer_obs_dias(n_dias: int) -> list[dict]:
    """Lee trazas de observabilidad de los últimos n_dias."""
    hoy = datetime.date.today()
    registros = []
    for i in range(n_dias):
        dia = hoy - datetime.timedelta(days=i)
        path = os.path.join(OBS_DIR, f"observabilidad-{dia.isoformat()}.jsonl")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            registros.append(json.loads(ln))
                        except Exception:
                            pass
        except Exception:
            pass
    return registros


def _leer_ledger_dias(n_dias: int) -> list[dict]:
    """Lee el ledger de gasto de APIs (gasto_ledger.jsonl)."""
    if not os.path.exists(LEDGER):
        return []
    corte = datetime.datetime.now() - datetime.timedelta(days=n_dias)
    registros = []
    try:
        with open(LEDGER, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                    ts_str = r.get("ts", "")
                    # Parseo defensivo: acepta ISO con o sin Z/offset
                    ts_str_norm = ts_str.replace("Z", "+00:00")
                    try:
                        dt = datetime.datetime.fromisoformat(ts_str_norm)
                        if dt.tzinfo is not None:
                            dt = dt.astimezone().replace(tzinfo=None)
                    except Exception:
                        dt = datetime.datetime.min
                    if dt >= corte:
                        registros.append(r)
                except Exception:
                    pass
    except Exception:
        pass
    return registros


def _grep_logs(patron: str, ventana_dias: int) -> bool:
    """Busca el patrón (regex) en los logs de launchd de los últimos N días."""
    if not os.path.isdir(LOGS_DIR):
        return False
    corte = time.time() - ventana_dias * 86400
    encontrado = False
    for ruta in glob.glob(os.path.join(LOGS_DIR, "*.out")) + glob.glob(
        os.path.join(LOGS_DIR, "*.err")
    ):
        try:
            if os.path.getmtime(ruta) < corte:
                continue
            with open(ruta, encoding="utf-8", errors="replace") as f:
                texto = f.read()
            if re.search(patron, texto, re.IGNORECASE):
                encontrado = True
                break
        except Exception:
            pass
    return encontrado


def tiene_uso_reciente(herramienta: dict, ventana_dias: int) -> bool:
    """True si hay al menos una señal de uso en la ventana dada."""
    su = herramienta.get("senal_uso", {})
    fuente = su.get("fuente", "")
    v = su.get("ventana_dias", ventana_dias)

    if fuente == "gasto_ledger":
        filtro_tool = su.get("filtro_tool", "")
        registros = _leer_ledger_dias(v)
        for r in registros:
            if r.get("tool", "") == filtro_tool:
                return True
        return False

    if fuente == "observabilidad":
        filtro_agente = su.get("filtro_agente", "")
        registros = _leer_obs_dias(v)
        for r in registros:
            if r.get("agente", "") == filtro_agente:
                return True
        return False

    if fuente == "grep_invocacion":
        patron = su.get("patron", "")
        return _grep_logs(patron, v) if patron else False

    return False  # sin señal definida → sin uso conocido


# ─── Probes de salud ──────────────────────────────────────────────────────────

def _probe_keychain(clave: str) -> tuple[bool, str]:
    """Comprueba que existe una entrada en el Llavero de macOS. $0 tokens."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", clave],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            return True, "clave en Llavero"
        return False, f"clave '{clave}' no encontrada en Llavero (rc={r.returncode})"
    except FileNotFoundError:
        # No estamos en macOS o security no disponible
        return False, "security(1) no disponible — ¿no es macOS?"
    except subprocess.TimeoutExpired:
        return False, "timeout comprobando Llavero"
    except Exception as exc:
        return False, f"error Llavero: {exc}"


def _probe_fichero(ruta: str) -> tuple[bool, str]:
    """Comprueba que un fichero existe (y no está vacío)."""
    ruta_abs = os.path.join(REPO, ruta) if not os.path.isabs(ruta) else ruta
    if not os.path.exists(ruta_abs):
        return False, f"fichero no encontrado: {ruta}"
    if os.path.getsize(ruta_abs) == 0:
        return False, f"fichero vacío: {ruta}"
    return True, f"fichero OK ({ruta})"


def _probe_url(url: str, bot_wall_indica_rota: bool = True,
               login_gated: bool = False) -> tuple[bool | None, str]:
    """Comprueba que una URL responde con HTTP 2xx o 3xx.

    Usa urllib (sin librerías externas). Timeout corto: es un ping, no un scrape.

    login_gated=True (herramienta tras login, p. ej. Consensus/scite): un ping
    ANÓNIMO nunca puede confirmar que TU suscripción funcione — ni un 200 en la
    home lo prueba, ni un anti-bot lo desmiente. Así que SIEMPRE → None
    (NO_VERIFICABLE). La única evidencia honesta es el uso reciente. Se reporta lo
    observado (up / anti-bot / caída) en el detalle, pero nunca se afirma viva/rota.

    Para tools públicas (login_gated=False):
      · bot_wall_indica_rota=True  → anti-bot = False (ROTA): la web pública debe cargar.
      · bot_wall_indica_rota=False → anti-bot = None (no verificable).
    """
    BOT_WALL = (
        "cloudflare", "security check", "are you a robot", "are you human",
        "verificación de seguridad", "checking your browser", "403 error",
        "access denied", "request blocked", "ray id", "bots maliciosos",
    )
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = resp.status
            body = resp.read(2048).decode("utf-8", errors="replace").lower()
            anti_bot = any(h in body for h in BOT_WALL)
            if login_gated:
                obs = "anti-bot" if anti_bot else f"HTTP {status}"
                return None, f"{obs} en {url} (login-gated: la home responde pero no confirma tu sesión)"
            if anti_bot:
                if bot_wall_indica_rota:
                    return False, f"anti-bot/Cloudflare detectado en {url}"
                return None, f"anti-bot en {url} (tras login; no verificable anónimamente)"
            if 200 <= status < 400:
                return True, f"HTTP {status}"
            return False, f"HTTP {status} (inesperado)"
    except urllib.error.HTTPError as exc:
        if login_gated:
            return None, f"HTTP {exc.code} en {url} (login-gated; no verificable anónimamente)"
        # 403/503 comunes en anti-bot
        if exc.code in (403, 503):
            if bot_wall_indica_rota:
                return False, f"HTTP {exc.code} (probable anti-bot) en {url}"
            return None, f"HTTP {exc.code} anti-bot en {url} (tras login; no verificable)"
        return False, f"HTTP {exc.code} en {url}"
    except urllib.error.URLError as exc:
        if login_gated:
            return None, f"URL inaccesible ({exc.reason}) en {url} (login-gated; no verificable)"
        return False, f"URL inaccesible ({exc.reason}): {url}"
    except socket.timeout:
        if login_gated:
            return None, f"timeout en {url} (login-gated; no verificable)"
        return False, f"timeout conectando a {url}"
    except Exception as exc:
        if login_gated:
            return None, f"error HTTP en {url} (login-gated; no verificable): {exc}"
        return False, f"error HTTP: {exc}"


def _iter_mcp_servers(cfg: dict):
    """Devuelve todos los nombres de servidor MCP declarados en un config, sea
    formato Claude Desktop (mcpServers al nivel raíz), .mcp.json (idem) o
    ~/.claude.json (mcpServers raíz + projects[*].mcpServers)."""
    nombres = set(cfg.get("mcpServers", {}).keys())
    for proj in cfg.get("projects", {}).values():
        if isinstance(proj, dict):
            nombres.update(proj.get("mcpServers", {}).keys())
    return nombres


def _probe_mcp_config(servidor: str, config_paths: list[str]) -> tuple[bool, str]:
    """Comprueba que un servidor MCP LOCAL está declarado en la config de Claude
    Code. Mira las rutas de la entrada MÁS las rutas por defecto conocidas (el bug
    de origen: el inventario apuntaba solo a ficheros inexistentes)."""
    rutas = list(dict.fromkeys(  # de-dup preservando orden
        [os.path.expanduser(p) for p in config_paths]
        + [os.path.expanduser(p) for p in MCP_CONFIG_PATHS_DEFECTO]
    ))
    revisadas = []
    for ruta in rutas:
        if not os.path.exists(ruta):
            continue
        revisadas.append(ruta)
        try:
            with open(ruta, encoding="utf-8") as f:
                cfg = json.load(f)
            servidores = _iter_mcp_servers(cfg)
            if servidor in servidores:
                return True, f"MCP declarado en {ruta}"
            for k in servidores:  # substring (para UUIDs)
                if servidor in k or k in servidor:
                    return True, f"MCP declarado como '{k}' en {ruta}"
        except Exception:
            pass
    donde = f" (revisadas: {', '.join(revisadas)})" if revisadas else " (sin config local)"
    return False, f"servidor MCP '{servidor}' no encontrado en config{donde}"


def _probe_mcp_conector(servidor: str, ventana_dias: int) -> tuple[bool | None, str]:
    """Conectores de claude.ai (Gmail/Drive/Notion…): NO viven en ningún fichero
    de config local — se inyectan en la sesión autenticada de claude.ai. Por eso
    _probe_mcp_config SIEMPRE los da por rotos (falso rojo). La evidencia honesta
    de que el conector funciona es su USO reciente en las transcripciones de Claude
    Code. Encontrado → True (usado). No encontrado → None (no verificable), NUNCA
    False: no afirmamos "roto" lo que sencillamente no podemos sondear."""
    if not os.path.isdir(CLAUDE_PROJECTS_DIR):
        return None, f"conector claude.ai '{servidor}': sin transcripciones locales (no verificable)"
    corte = time.time() - ventana_dias * 86400
    aguja = f"mcp__{servidor}__"  # así aparece una llamada al conector en el transcript
    for ruta in glob.iglob(os.path.join(CLAUDE_PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(ruta) < corte:
                continue
            with open(ruta, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    if aguja in ln or servidor in ln:
                        return True, f"conector usado en sesión reciente ({os.path.basename(ruta)})"
        except Exception:
            pass
    return None, f"conector claude.ai '{servidor}': sin uso en {ventana_dias}d (no verificable por fichero)"


def ejecutar_probe(herramienta: dict, ventana_dias: int = VENTANA_USO_DIAS) -> tuple[bool | None, str]:
    """Ejecuta la sonda definida para una herramienta. Devuelve (vive, detalle)
    donde vive ∈ {True, False, None}. None = no verificable localmente."""
    probe = herramienta.get("probe", {})
    metodo = probe.get("metodo", "")

    if metodo == "keychain":
        return _probe_keychain(probe.get("clave_llavero", ""))

    if metodo == "fichero":
        return _probe_fichero(probe.get("ruta", ""))

    if metodo == "url_accesible":
        marca = probe.get("bot_wall_indica")
        # "ROTA" = web pública (anti-bot = rota). "INCONCLUSO" = herramienta tras
        # login (nunca verificable por ping anónimo → NO_VERIFICABLE).
        return _probe_url(
            probe.get("url", ""),
            bot_wall_indica_rota=(marca == "ROTA"),
            login_gated=(marca == "INCONCLUSO"),
        )

    if metodo == "mcp_config":
        return _probe_mcp_config(
            probe.get("servidor", ""),
            probe.get("config_paths", []),
        )

    if metodo == "mcp_conector":
        return _probe_mcp_conector(probe.get("servidor", ""), ventana_dias)

    # Método desconocido → conservador: NO verificable (no afirmamos "roto")
    return None, f"método de sonda desconocido: '{metodo}'"


# ─── Clasificación ───────────────────────────────────────────────────────────

def clasificar(herramienta: dict, ventana_dias: int) -> dict[str, Any]:
    """Clasifica una herramienta y devuelve un dict con el resultado."""
    hid = herramienta.get("id", "?")
    es_must = herramienta.get("es_must", False)

    vive, detalle_probe = ejecutar_probe(herramienta, ventana_dias)
    usada = tiene_uso_reciente(herramienta, ventana_dias)

    if vive is None:
        # No sondeable localmente (conector claude.ai / web tras login). El uso
        # reciente es la única evidencia honesta: si se usó, está viva; si no,
        # NO_VERIFICABLE (informativo, nunca alarma — ni siquiera si es MUST).
        estado = OK if usada else NO_VERIFICABLE
    elif not vive:
        estado = MUST_SIN_OPERAR if es_must else ROTA
    elif not usada:
        estado = MUST_SIN_OPERAR if es_must else HUERFANA
    else:
        estado = OK

    return {
        "id": hid,
        "nombre": herramienta.get("nombre", hid),
        "tipo": herramienta.get("tipo", "?"),
        "es_must": es_must,
        "estado": estado,
        "vive": vive,
        "usada": usada,
        "detalle_probe": detalle_probe,
        "dueno": herramienta.get("dueno", "?"),
        "notas": herramienta.get("notas", ""),
    }


# ─── Anti-spam de alertas ─────────────────────────────────────────────────────

def _debe_alertar(clave: str, cooldown_h: int) -> bool:
    """True si han pasado más de cooldown_h horas desde la última alerta con esta clave."""
    try:
        if os.path.exists(ALERTA_STATE):
            with open(ALERTA_STATE, encoding="utf-8") as f:
                estado = json.load(f)
        else:
            estado = {}
        ultima = estado.get(clave)
        if ultima:
            dt = datetime.datetime.fromisoformat(ultima)
            if (datetime.datetime.now() - dt).total_seconds() < cooldown_h * 3600:
                return False
    except Exception:
        pass
    return True


def _marcar_alertada(clave: str):
    """Registra que se acaba de enviar la alerta con esta clave."""
    try:
        if os.path.exists(ALERTA_STATE):
            with open(ALERTA_STATE, encoding="utf-8") as f:
                estado = json.load(f)
        else:
            estado = {}
        estado[clave] = datetime.datetime.now().isoformat()
        tmp = ALERTA_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(estado, f)
        os.replace(tmp, ALERTA_STATE)
    except Exception:
        pass


# ─── Aviso vía salida.py ─────────────────────────────────────────────────────

def _enviar_alerta(resultados: list[dict], dry: bool):
    """Avisa a {{TITULAR}} si hay MUST_SIN_OPERAR o ROTA. Respeta anti-spam y HALT."""
    criticos = [r for r in resultados if r["estado"] in (MUST_SIN_OPERAR, ROTA)]
    if not criticos:
        return

    clave_spam = "must_sin_operar:" + ",".join(sorted(r["id"] for r in criticos))

    if dry:
        sys.stderr.write(
            f"[DRY] alerta suprimida ({len(criticos)} herramientas en alerta): "
            + ", ".join(r["id"] for r in criticos) + "\n"
        )
        return

    if not _debe_alertar(clave_spam, ALERTA_COOLDOWN_H):
        sys.stderr.write(
            f"[anti-spam] alerta ya enviada hace menos de {ALERTA_COOLDOWN_H}h — suprimida\n"
        )
        return

    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import salida

        lineas = []
        for r in criticos:
            if r["estado"] == MUST_SIN_OPERAR:
                lineas.append(
                    f"• {r['nombre']} — MUST sin operar. Sonda: {r['detalle_probe']}. "
                    f"Uso reciente: {'si' if r['usada'] else 'no'}."
                )
            else:
                lineas.append(
                    f"• {r['nombre']} — ROTA. Sonda: {r['detalle_probe']}."
                )

        texto = (
            "Alerta de herramientas/IAs\n\n"
            f"Se detectaron {len(criticos)} herramienta(s) con problemas:\n"
            + "\n".join(lineas)
            + "\n\nRevisa con: python3 tools/audit_herramientas.py"
        )

        result = salida.report_to_titular(texto, fuente="audit_herramientas")
        if result:
            _marcar_alertada(clave_spam)
    except Exception as exc:
        sys.stderr.write(f"audit_herramientas: no pude enviar alerta: {exc}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    want_json = "--json" in argv
    only_must = "--must" in argv
    only_roto = "--roto" in argv
    dry = DRY_ENV or "--dry" in argv
    ventana = VENTANA_USO_DIAS

    if "--ventana" in argv:
        i = argv.index("--ventana")
        try:
            ventana = int(argv[i + 1])
        except (IndexError, ValueError):
            pass

    herramientas = cargar_inventario()
    if not herramientas:
        msg = (
            f"No hay inventario en {INVENTARIO_PATH}. "
            "Crea o restaura tools/state/herramientas.json."
        )
        if want_json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(msg)
        return 1

    resultados = [clasificar(h, ventana) for h in herramientas]

    # Filtros de vista
    vista = resultados
    if only_must:
        vista = [r for r in resultados if r["es_must"]]
    elif only_roto:
        vista = [r for r in resultados if r["estado"] in (ROTA, MUST_SIN_OPERAR)]

    if want_json:
        resumen = {
            "ok": sum(1 for r in resultados if r["estado"] == OK),
            "huerfana": sum(1 for r in resultados if r["estado"] == HUERFANA),
            "must_sin_operar": sum(1 for r in resultados if r["estado"] == MUST_SIN_OPERAR),
            "rota": sum(1 for r in resultados if r["estado"] == ROTA),
            "no_verificable": sum(1 for r in resultados if r["estado"] == NO_VERIFICABLE),
            "total": len(resultados),
            "ventana_dias": ventana,
        }
        print(json.dumps({"resumen": resumen, "herramientas": vista}, ensure_ascii=False, indent=2))
    else:
        _imprimir_informe(vista, resultados, ventana, dry)

    # Aviso autónomo
    _enviar_alerta(resultados, dry)

    # Exit 1 si hay alertas
    hay_alerta = any(r["estado"] in (MUST_SIN_OPERAR, ROTA) for r in resultados)
    return 1 if hay_alerta else 0


ICONOS = {OK: "OK", HUERFANA: "HUERFANA", MUST_SIN_OPERAR: "MUST_SIN_OPERAR", ROTA: "ROTA",
          NO_VERIFICABLE: "NO_VERIFICABLE"}
ORDEN_GRAVEDAD = [MUST_SIN_OPERAR, ROTA, HUERFANA, NO_VERIFICABLE, OK]


def _imprimir_informe(vista: list[dict], todos: list[dict], ventana: int, dry: bool):
    por_estado = {e: [] for e in ORDEN_GRAVEDAD}
    for r in todos:
        por_estado[r["estado"]].append(r)

    total = len(todos)
    ok_n = len(por_estado[OK])
    must_n = len(por_estado[MUST_SIN_OPERAR])
    rota_n = len(por_estado[ROTA])
    huer_n = len(por_estado[HUERFANA])
    nv_n = len(por_estado[NO_VERIFICABLE])

    print(f"\nAUDITORIA DE HERRAMIENTAS · ventana {ventana} dias · {datetime.date.today().isoformat()}")
    print(f"  Total: {total}  |  OK: {ok_n}  |  HUERFANA: {huer_n}  |  ROTA: {rota_n}  |  MUST_SIN_OPERAR: {must_n}  |  NO_VERIFICABLE: {nv_n}")
    if dry:
        print("  [DRY: avisos suprimidos]")
    print()

    for estado in ORDEN_GRAVEDAD:
        items = [r for r in vista if r["estado"] == estado]
        if not items:
            continue
        print(f"── {ICONOS[estado]} ({len(items)}) ──────────────────────────────────")
        for r in items:
            must_tag = " [MUST]" if r["es_must"] else ""
            print(f"  {r['id']}{must_tag}")
            print(f"    {r['nombre']}  ({r['tipo']})")
            sonda_str = {True: "si", False: "NO", None: "no verificable"}[r["vive"]]
            print(f"    Sonda:  {sonda_str}  — {r['detalle_probe']}")
            uso_str = f"si (<{ventana}d)" if r["usada"] else f"NO (>{ventana}d sin senal)"
            print(f"    Uso:    {uso_str}")
            if r.get("notas"):
                print(f"    Notas:  {r['notas']}")
            print()


if __name__ == "__main__":
    sys.exit(main())
