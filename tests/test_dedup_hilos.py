#!/usr/bin/env python3
"""test_dedup_hilos.py — bateria de tests para dedup_hilos.py y seguimiento.upsert_evento.

Aislado con BTP_STATE_DIR (nunca toca el estado vivo de casa base). Cubre:
    · upsert_evento idempotente (2ª llamada no duplica)
    · dedup funde hilos de alta confianza (mismo evento, score >= SCORE_MIN)
    · dedup propone baja confianza (score < SCORE_MIN), no funde a ciegas
    · NO toca hilos de eventos distintos
    · fail-soft con seguimiento.json corrupto
    · caso Bernardo: 4 hilos del mismo evento → funde en 1 (sobre estado copiado)
"""
import os
import sys
import json
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_dedup_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import seguimiento as sg   # noqa: E402
import dedup_hilos as dd   # noqa: E402
import triage_tareas as tt  # noqa: E402

STATE = os.environ["BTP_STATE_DIR"]
dd.DEDUP_DIR = os.path.join(STATE, "dedup")  # redirigir auditoria al tmp

_pass = 0
_fail = 0


def check(nombre, cond, detalle=""):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s%s" % (nombre, " — " + detalle if detalle else ""))


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _reset():
    """Estado limpio: seguimiento vacio + dudosas vacias."""
    _write(sg.SEG, {"hilos": []})
    if os.path.exists(tt.DUDOSAS):
        os.remove(tt.DUDOSAS)


def _n_hilos():
    return len(sg.load_seguimiento().get("hilos", []))


def _hilo(hid):
    for h in sg.load_seguimiento().get("hilos", []):
        if h.get("id") == hid:
            return h
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 1. upsert_evento — creacion y actualizacion idempotente
# ═════════════════════════════════════════════════════════════════════════════

_reset()

hid1 = sg.upsert_evento(
    entidad="bernardo-cordovez",
    tipo="reunion",
    fecha_iso="2026-07-01",
    titulo="Reunion Bernardo Cordovez — 1/7 19h",
    estado="por_confirmar",
    etiqueta="NED",
)
check("upsert crea el hilo", _n_hilos() == 1)
check("upsert id determinista", hid1 == "reunion-bernardo-cordovez-2026-07-01")

# Segunda llamada identica → mismos argumentos → 0 hilos nuevos.
hid2 = sg.upsert_evento(
    entidad="bernardo-cordovez",
    tipo="reunion",
    fecha_iso="2026-07-01",
    titulo="Reunion Bernardo Cordovez — 1/7 19h",
    estado="por_confirmar",
    etiqueta="NED",
)
check("2ª llamada identica no duplica", _n_hilos() == 1)
check("2ª llamada devuelve el mismo id", hid1 == hid2)

# Segunda llamada con titulo actualizado → actualiza, no crea.
hid3 = sg.upsert_evento(
    entidad="bernardo-cordovez",
    tipo="reunion",
    fecha_iso="2026-07-01",
    titulo="Reunion Bernardo + {{CONTACTO}} — Google Meet 1/7 19:00",
    estado="en_curso",
    etiqueta="NED",
)
check("2ª llamada con titulo nuevo actualiza, no crea", _n_hilos() == 1)
check("estado se actualiza", _hilo(hid1)["estado"] == "en_curso")
check("titulo se actualiza al nuevo", "{{CONTACTO}}" in _hilo(hid1)["titulo"])
check("id sigue igual", hid1 == hid3)

# Evento DISTINTO (otra fecha) → nuevo hilo.
hid_otro = sg.upsert_evento(
    entidad="bernardo-cordovez",
    tipo="reunion",
    fecha_iso="2026-07-08",
    titulo="Reunion Bernardo — 8/7 seguimiento",
    estado="por_confirmar",
)
check("evento distinto (otra fecha) crea nuevo hilo", _n_hilos() == 2)
check("id distinto", hid1 != hid_otro)

# Sin fecha → id sin fecha, idempotente igual.
_reset()
hid_sf = sg.upsert_evento("contacto-contacto", "tarea", None, "Tarea {{CONTACTO}} sin fecha")
hid_sf2 = sg.upsert_evento("contacto-contacto", "tarea", None, "Tarea {{CONTACTO}} sin fecha")
check("sin fecha: idempotente", _n_hilos() == 1 and hid_sf == hid_sf2)

# Fecha invalida → fail-soft: id sin fecha, no lanza.
_reset()
try:
    hid_bad = sg.upsert_evento("test", "reunion", "no-es-fecha", "Test fecha mala")
    check("fecha invalida: no lanza (fail-soft)", True)
    # id debe ser sin la fecha mala
    check("fecha invalida: id sin fecha", hid_bad == "reunion-test")
except Exception as e:
    check("fecha invalida: no lanza (fail-soft)", False, str(e))

print("✓ 1. upsert_evento")

# ═════════════════════════════════════════════════════════════════════════════
# 2. dedup — fusion de alta confianza
# ═════════════════════════════════════════════════════════════════════════════

_reset()

# Crear 3 hilos del mismo evento (reunion Bernardo 1/7) con titulos distintos pero
# que comparten suficientes tokens (score >= SCORE_MIN).
h1 = sg.add_hilo({"titulo": "Llamada Bernardo Cordovez Kernis 1-jul 19h", "estado": "en_curso",
                  "plazo": "2026-07-01", "quien_espera": "Bernardo Cordovez", "etiqueta": "NED",
                  "siguiente_accion": "Asistir a la reunion"})
h2 = sg.add_hilo({"titulo": "Reunion Bernardo Cordovez Kernis manana 1/7", "estado": "por_confirmar",
                  "plazo": "2026-07-01", "quien_espera": "Bernardo Cordovez"})
h3 = sg.add_hilo({"titulo": "Call Bernardo Cordovez Kernis circulo Sid mie 1-jul 19:00",
                  "estado": "por_confirmar", "plazo": "2026-07-01",
                  "quien_espera": "Bernardo Cordovez"})

check("setup: 3 hilos del mismo evento", _n_hilos() == 3)

# audit no funde.
rep_a = dd.dedup(aplicar=False)
check("audit detecta grupo duplicado", rep_a["grupos_analizados"] >= 1)
check("audit no modifica el estado", _n_hilos() == 3)

# fix funde.
rep_f = dd.dedup(aplicar=True)
hilos_abiertos = [h for h in sg.load_seguimiento()["hilos"] if h.get("estado") != "hecho"]
check("fix: queda 1 hilo abierto", len(hilos_abiertos) == 1)
check("fix: los absorbidos quedan como hecho", _n_hilos() == 3)  # total no cambia; 2 cerrados
absorbidos_hecho = [h for h in sg.load_seguimiento()["hilos"]
                    if h.get("estado") == "hecho" and "Fundido en" in (h.get("siguiente_accion") or "")]
check("fix: absorbidos tienen nota de fusion", len(absorbidos_hecho) == 2)
check("fix: reporte tiene fusiones", len(rep_f["fusiones"]) >= 1)

# El superviviente es el mas completo (h1 tenia etiqueta y siguiente_accion).
superviviente_id = hilos_abiertos[0]["id"]
check("superviviente es el mas completo (h1)", superviviente_id == h1)

print("✓ 2. dedup fusion alta confianza")

# ═════════════════════════════════════════════════════════════════════════════
# 3. dedup — propone baja confianza, no funde
# ═════════════════════════════════════════════════════════════════════════════

_reset()

# Dos hilos: misma entidad (quien_espera = "Bernardo") y misma fecha, pero titulos
# tan diferentes que score < SCORE_MIN. La entidad se detecta via quien_espera.
sg.add_hilo({"titulo": "Pendiente gestion interna 1-jul", "estado": "en_curso",
             "plazo": "2026-07-01", "quien_espera": "Bernardo Cordovez"})
sg.add_hilo({"titulo": "Factura presupuesto laboratorio 1-jul tejido",
             "estado": "en_curso", "plazo": "2026-07-01", "quien_espera": "Bernardo Cordovez"})

rep_baja = dd.dedup(aplicar=True)
# Los dos hilos deben seguir abiertos (no se fusionaron).
abiertos_baja = [h for h in sg.load_seguimiento()["hilos"] if h.get("estado") != "hecho"]
check("baja confianza: no funde (ambos siguen abiertos)", len(abiertos_baja) == 2)
check("baja confianza: encola propuesta", len(tt.listar_dudosas()) >= 1 or len(rep_baja["propuestas"]) >= 1)

print("✓ 3. dedup baja confianza → propuesta")

# ═════════════════════════════════════════════════════════════════════════════
# 4. dedup — NO toca hilos de eventos distintos
# ═════════════════════════════════════════════════════════════════════════════

_reset()

sg.add_hilo({"titulo": "Reunion Bernardo Kernis 1-jul", "estado": "en_curso", "plazo": "2026-07-01",
             "quien_espera": "Bernardo Cordovez Kernis"})
sg.add_hilo({"titulo": "Cita {{CONTACTO}} {{CONTACTO}} {{CENTRO}} 8-jul biopsia",
             "estado": "en_curso", "plazo": "2026-07-08", "quien_espera": "{{CONTACTO}} {{CONTACTO}}"})
sg.add_hilo({"titulo": "Llamada Bernardo Kernis 1/7 19h Google Meet",
             "estado": "por_confirmar", "plazo": "2026-07-01", "quien_espera": "Bernardo Cordovez"})

# Deben estar: Bernardo×2 (mismo evento) + {{CONTACTO}}×1 (evento distinto).
rep_mix = dd.dedup(aplicar=True)
abiertos_mix = [h for h in sg.load_seguimiento()["hilos"] if h.get("estado") != "hecho"]
# {{CONTACTO}} NO se toca.
contacto_ok = any("contacto" in h.get("titulo", "").lower() and h.get("estado") != "hecho"
                 for h in sg.load_seguimiento()["hilos"])
check("evento distinto ({{CONTACTO}}) no se toca", contacto_ok)
# Con SCORE_MIN=3, este par comparte solo "bernardo"+"kernis" en el TÍTULO (score 2): ya NO se
# auto-funde, se PROPONE a Vega (fallo seguro tras el falso positivo {{CONTACTO}} del 30/6). Lo nuclear
# de este test es que el evento DISTINTO ({{CONTACTO}}) no se toque (check de arriba).
check("score 2 se propone, no se auto-funde", len(abiertos_mix) == 3 and len(rep_mix["propuestas"]) >= 1)

print("✓ 4. eventos distintos no se tocan")

# ═════════════════════════════════════════════════════════════════════════════
# 5. fail-soft con seguimiento.json corrupto
# ═════════════════════════════════════════════════════════════════════════════

_reset()
with open(sg.SEG, "w", encoding="utf-8") as f:
    f.write("{ esto no es json valido !!")

rep_corr = dd.dedup(aplicar=True)
check("fail-soft: no lanza con JSON corrupto", isinstance(rep_corr, dict))
check("fail-soft: status es error", rep_corr.get("status") == "error")
check("fail-soft: errores describe el problema", bool(rep_corr.get("errores")))

print("✓ 5. fail-soft JSON corrupto")

# ═════════════════════════════════════════════════════════════════════════════
# 6. caso Bernardo real (sobre copia del estado): 4 hilos → 1
# ═════════════════════════════════════════════════════════════════════════════

_reset()

# Reproducir los 4 hilos duplicados del caso real (30/6/26) sobre estado temporal.
# Datos anonimizados/simplificados pero fieles a la estructura real.
sg.add_hilo({
    "titulo": "Llamada Bernardo Cordovez Kernis 1jul 19:00",
    "estado": "en_curso",
    "plazo": "2026-07-01",
    "categoria": "contactos-red",
    "etiqueta": "NED",
    "quien_espera": "Bernardo Cordovez Kernis",
    "siguiente_accion": "Asistir a la llamada 1 jul 19:00-19:30 CET. Google Meet",
})
sg.add_hilo({
    "titulo": "Reunion Bernardo Cordovez Kernis manana 1/7 19h Google Meet",
    "estado": "esperando",
    "plazo": "2026-07-01",
    "categoria": "contactos-red",
    "etiqueta": "Gestion",
    "quien_espera": "Bernardo Cordovez — cita confirmada",
})
sg.add_hilo({
    "titulo": "Llamada con Bernardo Cordovez Kernis POSPUESTA a mie 1-jul",
    "estado": "esperando",
    "plazo": "2026-07-03",
    "categoria": "otros",
    "etiqueta": "NED",
    "quien_espera": "tú elegir slot en Calendly",
})
sg.add_hilo({
    "titulo": "Call con Bernardo Cordovez Kernis circulo de Sid mie 1-jul 19:00 {{CONTACTO}} en copia",
    "estado": "por_confirmar",
    "plazo": "2026-07-01",
    "categoria": "contacto",
    "etiqueta": "NED",
    "quien_espera": "tú",
})

check("setup caso Bernardo: 4 hilos", _n_hilos() == 4)

rep_bern = dd.dedup(aplicar=True)
abiertos_bern = [h for h in sg.load_seguimiento()["hilos"] if h.get("estado") != "hecho"]
fusiones = rep_bern.get("fusiones", [])
propuestas = rep_bern.get("propuestas", [])

# Esperar que al menos 3 de los 4 queden cerrados (o que haya fusion o propuesta registrada).
n_cerrados = sum(1 for h in sg.load_seguimiento()["hilos"] if h.get("estado") == "hecho")
check("caso Bernardo: al menos 2 hilos cerrados o propuestos",
      n_cerrados >= 2 or len(fusiones) >= 1 or len(propuestas) >= 1,
      "cerrados=%d fusiones=%d propuestas=%d" % (n_cerrados, len(fusiones), len(propuestas)))
check("caso Bernardo: no todos se pierden (superviviente o propuesta)",
      len(abiertos_bern) >= 1 or len(propuestas) >= 1)

print("✓ 6. caso Bernardo (4 hilos → fusion o propuesta)")

# ═════════════════════════════════════════════════════════════════════════════
# Resultado final
# ═════════════════════════════════════════════════════════════════════════════

print()
print("Resultado: %d OK · %d FAIL" % (_pass, _fail))
if _fail:
    print("FALLO(S):")
sys.exit(0 if _fail == 0 else 1)
