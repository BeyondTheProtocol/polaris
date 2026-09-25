#!/usr/bin/env python3
"""vigia_sesiones — aviso suave de higiene de sesiones (ahorro de gasto).

Por qué: el grueso del gasto de Claude Max es relectura de contexto, y cada sesión
relee TODA su historia en cada turno. Muchas sesiones abiertas a la vez, o una muy
larga, disparan el volumen (ver tools/coste.py). Esto NO cierra nada: solo AVISA a
{{TITULAR}} con cariño para que cierre lo que no usa. Hábito asistido, no vigilancia.

Señal (reutiliza el escáner de coste.py sobre ~/.claude/projects, hoy):
  - nº de sesiones DISTINTAS con actividad hoy (muchas a la vez = caro), y
  - la sesión más pesada del día (una larga que relee mucho).

Salida: salida.report_to_titular(categoria="humano") — respeta HALT, silencio nocturno
(urgente=False) y muro. ANTI-SPAM: como mucho 1 aviso/día (estado en tools/state).
Por defecto NO envía (dry); el daemon lo llama con --send.
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coste  # noqa: E402  (reusa scan/empty: una sola fuente de verdad de tokens)
import salida  # noqa: E402

PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "vigia_sesiones.json")

# Umbrales (tokens CRUDOS; calibrados con datos reales jun-2026). Tunables.
# Un día normal (~7-8 sesiones reales, top ~200M) NO debe saltar; solo días excesivos
# (los picos del 21-22/6 fueron ~2,8B/día con muchas más sesiones y alguna >800M).
UMBRAL_SESION_REAL = 30_000_000     # por debajo = ruido de daemons, no cuenta como sesión
UMBRAL_N_SESIONES = 12              # nº de sesiones REALES a la vez hoy
UMBRAL_SESION_PESADA = 400_000_000  # tokens de una sola sesión (≈ sesión muy larga)


def _vol(t):
    return (t.get("input", 0) + t.get("output", 0)
            + t.get("cache_read", 0) + t.get("cache_write", 0))


def medir(hoy=None):
    """Devuelve (n_sesiones, top_tokens) de HOY, a través de todos los proyectos."""
    hoy = hoy or time.strftime("%Y-%m-%d")
    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
    _, _, por_sesion = coste.scan(files)
    vols = [_vol(v) for k, v in por_sesion.items() if k.startswith(hoy)]
    n = sum(1 for v in vols if v > UMBRAL_SESION_REAL)  # solo sesiones reales (no daemons)
    top = max(vols, default=0)
    return n, top


def _ya_avisado_hoy(hoy):
    try:
        return json.load(open(STATE, encoding="utf-8")).get("ultimo_aviso") == hoy
    except Exception:
        return False


def _marcar(hoy):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ultimo_aviso": hoy}, f)
    os.replace(tmp, STATE)


def construir_aviso(n, top):
    motivos = []
    if n >= UMBRAL_N_SESIONES:
        motivos.append("tienes %d sesiones abiertas hoy" % n)
    if top >= UMBRAL_SESION_PESADA:
        motivos.append("una de ellas va muy larga (relee mucho en cada turno)")
    if not motivos:
        return None
    return ("🧹 Apunte de gasto: %s. Cada sesión relee toda su historia en cada turno, "
            "así que cerrar las que no uses y abrir una nueva cuando se alargue ahorra "
            "bastante del tope de Max. Sin prisa, cuando puedas." % " y ".join(motivos))


def run(*, send=False, hoy=None):
    hoy = hoy or time.strftime("%Y-%m-%d")
    n, top = medir(hoy)
    aviso = construir_aviso(n, top)
    if not aviso:
        return {"n": n, "top": top, "aviso": None, "enviado": False}
    if _ya_avisado_hoy(hoy):
        return {"n": n, "top": top, "aviso": aviso, "enviado": False, "motivo": "ya avisado hoy"}
    if not send:
        return {"n": n, "top": top, "aviso": aviso, "enviado": False, "motivo": "dry"}
    r = salida.report_to_titular(aviso, categoria="humano", urgente=False, voz="calida")
    if r.get("delivered"):
        _marcar(hoy)
    return {"n": n, "top": top, "aviso": aviso, "enviado": bool(r.get("delivered")), "salida": r}


if __name__ == "__main__":
    res = run(send=("--send" in sys.argv[1:]))
    print(json.dumps(res, ensure_ascii=False, indent=2))
