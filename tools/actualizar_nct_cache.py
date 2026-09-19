#!/usr/bin/env python3
"""tools/actualizar_nct_cache.py — actualiza el caché local de overallStatus de los NCTs vigilados.

Corre desde Claude (o manualmente) para interrogar ClinicalTrials.gov vía BioMCP y guardar el
resultado en state/centinela/nct_cache.json. El centinela_ned.py lo lee offline ($0, sin red).

Flujo:
  1. Lee los NCTs de seguimiento.json y cumbre.json (misma lógica que centinela_ned._nct_ids_vigilados).
  2. Para cada NCT llama a la API de ClinicalTrials.gov o usa la herramienta BioMCP que esté
     disponible en el entorno. Si no hay acceso a BioMCP, usa la API pública REST v2 de
     clinicaltrials.gov directamente (sin clave, sin LLM, sin coste).
  3. Extrae SOLO el campo overallStatus (anti-inyección: no procesa nada más del JSON externo).
  4. Guarda el caché en state/centinela/nct_cache.json con timestamp de la última consulta.
  5. Compara con el caché previo y reporta cambios (sin avisar → eso lo hace centinela_ned.run).

El caché tiene TTL configurable: si la última consulta tiene menos de MIN_HOURS_ENTRE_CONSULTAS
horas, no se re-consulta (ahorra llamadas; el estado de ensayos no cambia por horas).

Uso:
  python3 tools/actualizar_nct_cache.py [--forzar]   # --forzar ignora el TTL
  python3 tools/actualizar_nct_cache.py --status      # muestra el caché actual sin actualizar

Muro / privacidad: SOLO llama a clinicaltrials.gov (dominio público de los NIH). No envía PII.
Los NCT son identificadores públicos. El JSON de respuesta se trata como DATO EXTERNO (solo
extrae overallStatus; ignora el resto). No usa LLM.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
NCT_CACHE = os.path.join(STATE, "centinela", "nct_cache.json")
SEGUIMIENTO_JSON = os.path.join(STATE, "seguimiento.json")

MIN_HOURS_ENTRE_CONSULTAS = 6   # no re-consultar si el caché tiene menos de 6h

# API pública de ClinicalTrials.gov v2 (sin clave, HTTPS)
CT_API_URL = "https://clinicaltrials.gov/api/v2/studies/{nct_id}?fields=OverallStatus&format=json"


def _nct_ids_vigilados():
    """Misma lógica que centinela_ned._nct_ids_vigilados. Sin importar el módulo (evita
    dependencias cruzadas entre herramientas)."""
    ids = set()
    _re_nct = re.compile(r'\bNCT\d{6,}\b', re.IGNORECASE)
    for path in [SEGUIMIENTO_JSON, os.path.join(STATE, "cumbre.json")]:
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            ids.update(m.upper() for m in _re_nct.findall(raw))
        except Exception:
            pass
    return ids


def _cargar_cache():
    try:
        with open(NCT_CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _guardar_cache(d):
    os.makedirs(os.path.dirname(NCT_CACHE), mode=0o700, exist_ok=True)
    tmp = NCT_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NCT_CACHE)


def _consultar_nct(nct_id):
    """Consulta clinicaltrials.gov y devuelve el overallStatus (str) o None si falla.
    DATO EXTERNO: solo extrae el campo overallStatus del JSON de respuesta; ignora todo lo demás.
    """
    url = CT_API_URL.format(nct_id=nct_id)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "BeyondTheProtocol-WatchdogBot/1.0 (research; contact titular.mgp@gmail.com)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        print("  [WARN] %s: no se pudo consultar (%s)" % (nct_id, e), file=sys.stderr)
        return None

    # Anti-inyección: SOLO extraemos overallStatus, ignoramos cualquier otro campo.
    # La API v2 de ClinicalTrials.gov devuelve el estudio directamente cuando se
    # consulta por NCT ID (GET /studies/{id}), no como lista sino como objeto raíz.
    try:
        # Forma 1: objeto directo {"protocolSection": {"statusModule": {"overallStatus": ...}}}
        status_module = raw.get("protocolSection", {}).get("statusModule", {})
        status = status_module.get("overallStatus")
        if status and isinstance(status, str):
            return status.strip()[:64]
        # Forma 2: lista {"studies": [{...}]} (por si la API cambia en el futuro)
        studies = raw.get("studies") or []
        if studies and isinstance(studies[0], dict):
            status_module = (
                studies[0]
                .get("protocolSection", {})
                .get("statusModule", {})
            )
            status = status_module.get("overallStatus")
            if status and isinstance(status, str):
                return status.strip()[:64]
    except Exception:
        pass
    return None


def _cache_necesita_actualizacion(cache, ncts):
    """True si algún NCT vigilado falta en el caché o el caché es más viejo que el TTL."""
    ts_str = cache.get("_ts_consulta")
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ahora = datetime.now(tz=timezone.utc)
            horas = (ahora - ts).total_seconds() / 3600
            if horas < MIN_HOURS_ENTRE_CONSULTAS:
                faltantes = [n for n in ncts if n not in cache]
                if not faltantes:
                    return False
        except Exception:
            pass
    return True


def actualizar(forzar=False):
    """Actualiza el caché para todos los NCTs vigilados. Devuelve {nct_id: overallStatus} nuevo."""
    ncts = _nct_ids_vigilados()
    if not ncts:
        print("No hay NCTs vigilados en seguimiento/cumbre.")
        return {}

    cache = _cargar_cache()
    if not forzar and not _cache_necesita_actualizacion(cache, ncts):
        print("Cache actualizado (menos de %dh). Usa --forzar para re-consultar." % MIN_HOURS_ENTRE_CONSULTAS)
        return {k: v for k, v in cache.items() if k in ncts}

    print("Consultando ClinicalTrials.gov para: %s" % ", ".join(sorted(ncts)))
    cambios = []
    for nct_id in sorted(ncts):
        prev = cache.get(nct_id, {})
        prev_status = prev.get("overallStatus") if isinstance(prev, dict) else prev
        nuevo_status = _consultar_nct(nct_id)
        if nuevo_status is None:
            print("  %s: sin respuesta (se conserva el estado previo: %s)" % (nct_id, prev_status or "desconocido"))
            continue
        cache[nct_id] = {"overallStatus": nuevo_status}
        if prev_status and prev_status != nuevo_status:
            cambios.append((nct_id, prev_status, nuevo_status))
            print("  %s: CAMBIO %s -> %s" % (nct_id, prev_status, nuevo_status))
        else:
            print("  %s: %s (sin cambio)" % (nct_id, nuevo_status))

    cache["_ts_consulta"] = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    _guardar_cache(cache)
    print("Cache guardado en %s" % NCT_CACHE)
    if cambios:
        print("\nCambios detectados: %d ensayo(s). El centinela avisara en la proxima pasada." % len(cambios))
    return {k: cache[k]["overallStatus"] for k in ncts if k in cache and isinstance(cache.get(k), dict)}


def status():
    """Muestra el caché actual sin actualizar."""
    cache = _cargar_cache()
    ncts = _nct_ids_vigilados()
    ts = cache.get("_ts_consulta", "nunca")
    print("Ultima consulta: %s" % ts)
    print("NCTs vigilados: %s" % (", ".join(sorted(ncts)) or "(ninguno)"))
    for nct_id in sorted(ncts):
        info = cache.get(nct_id, "(sin datos)")
        if isinstance(info, dict):
            info = info.get("overallStatus", "(sin overallStatus)")
        print("  %s: %s" % (nct_id, info))


def main(argv):
    forzar = "--forzar" in argv or "--force" in argv
    if "--status" in argv:
        status()
        return 0
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    actualizar(forzar=forzar)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
