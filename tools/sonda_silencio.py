#!/usr/bin/env python3
"""Sonda del SILENCIO: lo que debía pasar y no pasó, y nadie dijo nada.

Nace del 21-sep-2026. La tarea programada `rodaje-guard-clinico` arrancó a las 13:50, murió a
los 7 segundos porque buscaba un worktree ya borrado, y cinco horas después seguía marcada como
«corriendo». Su encargo incluía avisar a {{TITULAR}} por Telegram; no la avisó. Lo descubrimos porque
ELLA preguntó, no porque el sistema lo dijera.

El resto de vigías de Polaris miran lo que SÍ pasa (un job que falla, un daemon que cae, una
cifra que se dispara). Esta mira el hueco: el trabajo que arrancó y no dejó obra. Es determinista,
local y sin LLM.

Qué mira, hoy:
  1. Tareas programadas que murieron mudas: la sesión que abre la tarea vive menos de N segundos.
  2. Jobs del lazo atascados en `processing` más de N horas (arrancaron y nadie los cerró).
  3. Jobs caídos en `failed` que llevan días ahí sin que nadie los mire.

Lo que NO mira todavía, y se dice en voz alta en la salida: si una tarea programada CORRIÓ del
todo pero su encargo no se cumplió (p.ej. dijo que avisaría y no avisó). Eso exige leer lo que
hizo, no cuánto vivió.

Uso:
    python3 tools/sonda_silencio.py            # informe humano; rc=1 si hay hallazgos
    python3 tools/sonda_silencio.py --json     # para el panel o el healthcheck
"""
import argparse
import glob
import json
import os
import sys
import time

# Sesiones que abre la app de escritorio, una por fichero. La que nace de una tarea programada
# lleva `scheduledTaskId`, y sus dos marcas de tiempo dicen cuánto vivió.
SESIONES = os.path.expanduser(
    "~/Library/Application Support/Claude/claude-code-sessions/*/*/local_*.json")
_REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
COLA = os.path.join(os.environ.get("BTP_STATE_DIR") or os.path.join(_REPO, "tools", "state"),
                    "queue")

# Una tarea que vive menos de esto no llegó a trabajar: arranque, primer turno y muerte.
UMBRAL_MUDA_S = 180
# Un job en `processing` más de esto es un job que nadie cerró.
UMBRAL_ATASCO_H = 6
# Un fallo que lleva más de esto sin tocar ya no es «reciente».
UMBRAL_OLVIDO_D = 3


def _ms(x):
    """Las marcas de la app van en milisegundos; toleramos segundos por si eso cambia."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v / 1000.0 if v > 1e11 else v


def tareas_mudas(ahora=None, patron=SESIONES, umbral_s=UMBRAL_MUDA_S):
    ahora = ahora or time.time()
    out = []
    for f in glob.glob(patron):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        tarea = d.get("scheduledTaskId")
        if not tarea:
            continue
        ini, fin = _ms(d.get("createdAt")), _ms(d.get("lastActivityAt"))
        if ini is None or fin is None:
            continue
        vivio = fin - ini
        if vivio < umbral_s:
            out.append({"tarea": tarea, "titulo": d.get("title") or "",
                        "sesion": d.get("sessionId") or os.path.basename(f)[:-5],
                        "vivio_s": round(vivio, 1),
                        "hace_h": round((ahora - fin) / 3600.0, 1)})
    return sorted(out, key=lambda x: -x["hace_h"])


def jobs_atascados(ahora=None, cola=None, umbral_h=UMBRAL_ATASCO_H):
    ahora, cola = ahora or time.time(), cola or COLA
    out = []
    for f in glob.glob(os.path.join(cola, "processing", "*.json")):
        h = (ahora - os.path.getmtime(f)) / 3600.0
        if h > umbral_h:
            out.append({"job": os.path.basename(f), "horas": round(h, 1)})
    return sorted(out, key=lambda x: -x["horas"])


def fallos_olvidados(ahora=None, cola=None, umbral_d=UMBRAL_OLVIDO_D):
    ahora, cola = ahora or time.time(), cola or COLA
    fs = glob.glob(os.path.join(cola, "failed", "*.json"))
    viejos = [f for f in fs if (ahora - os.path.getmtime(f)) / 86400.0 > umbral_d]
    if not fs:
        return None
    mas_viejo = min(fs, key=os.path.getmtime)
    return {"total": len(fs), "olvidados": len(viejos),
            "dias_del_mas_viejo": round((ahora - os.path.getmtime(mas_viejo)) / 86400.0, 1)}


def revisa(ahora=None):
    return {"tareas_mudas": tareas_mudas(ahora),
            "jobs_atascados": jobs_atascados(ahora),
            "fallos": fallos_olvidados(ahora)}


def _informe(r):
    ls = []
    for t in r["tareas_mudas"]:
        ls.append("🔇 tarea «%s» arrancó y murió en %.0f s (hace %.0f h) · %s"
                  % (t["tarea"], t["vivio_s"], t["hace_h"], t["titulo"] or "sin título"))
    for j in r["jobs_atascados"]:
        ls.append("🧊 job atascado en processing %.0f h · %s" % (j["horas"], j["job"]))
    f = r["fallos"]
    if f and f["olvidados"]:
        ls.append("🛠️ %d job(s) caídos, %d sin tocar en más de %d días (el más viejo, %.0f días)"
                  % (f["total"], f["olvidados"], UMBRAL_OLVIDO_D, f["dias_del_mas_viejo"]))
    return ls


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = revisa()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        ls = _informe(r)
        if not ls:
            print("🔊 sin silencios: ninguna tarea muerta, ningún job atascado, nada olvidado.")
        else:
            print("SONDA DEL SILENCIO — lo que debía pasar y no pasó:")
            for l in ls:
                print("  " + l)
            print("\nNo cubre (dicho a propósito): una tarea que corrió entera pero no cumplió su "
                  "encargo. Eso exige leer lo que hizo, no cuánto vivió.")
    return 1 if _informe(r) else 0


if __name__ == "__main__":
    sys.exit(main())
