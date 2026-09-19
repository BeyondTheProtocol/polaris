#!/usr/bin/env python3
"""test_reconciliar_estado.py — reconciliador determinista de estado.
Aísla TODO en un tmp (BTP_STATE_DIR); no toca el repo real. Cubre: cierre seguro de hilos
gemelos de reservas resueltas (enlace y difuso estrecho), propuesta (no creación) de huérfanos,
NO-auto-cierre de hilos de alcance amplio, idempotencia y fail-soft con JSON corrupto."""
import os
import sys
import json
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_reconciliar_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import seguimiento as sg          # noqa: E402
import reconciliar_estado as rc   # noqa: E402
import triage_tareas as tt        # noqa: E402

STATE = os.environ["BTP_STATE_DIR"]


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _reset(reservas):
    """Estado limpio: reservas dadas + seguimiento vacío + sin dudosas/cumbre."""
    _write(rc.RESERVAS, {"encargos": reservas})
    _write(sg.SEG, {"hilos": []})
    for p in (tt.DUDOSAS, os.path.join(STATE, "reconciliador", "seen.json")):
        if os.path.exists(p):
            os.remove(p)


def _estado_hilo(hid):
    for h in sg.load_seguimiento().get("hilos", []):
        if h.get("id") == hid:
            return h.get("estado")
    return None


# ── 1a) Reserva resuelta + hilo gemelo ENLAZADO abierto → fix lo cierra ────────
_reset([{"id": "vuelo-contacto", "titulo": "SWISS BOS ZRH {{CONTACTO}}", "estado": "RESERVADO_PAGADO"}])
hid = sg.add_hilo({"titulo": "Reservar vuelo de {{CONTACTO}} BOS ZRH", "estado": "en_curso",
                   "ref_reserva": "vuelo-contacto"})
rep = rc.reconciliar(aplicar=False)
assert any(i["hilo"] == hid for i in rep["inconsistentes"]), "audit debe marcar la inconsistencia"
assert _estado_hilo(hid) == "en_curso", "audit NO debe escribir"
rep = rc.reconciliar(aplicar=True)
assert _estado_hilo(hid) == "hecho", "fix debe cerrar el hilo gemelo enlazado"
assert any(c["hilo"] == hid for c in rep["cerrados"])
print("✓ 1a cierre por enlace explícito")

# ── 1b) Reserva resuelta + gemelo por MATCH DIFUSO estrecho → fix lo cierra ────
_reset([{"id": "tren-titular", "titulo": "Tren Bruselas {{CIUDAD}} {{TITULAR}}", "estado": "reservado"}])
hid = sg.add_hilo({"titulo": "Reservar tren Bruselas {{CIUDAD}} para {{TITULAR}}", "estado": "esperando"})
rep = rc.reconciliar(aplicar=True)
assert _estado_hilo(hid) == "hecho", "fix debe cerrar por match difuso estrecho"
# y debe haber dejado el enlace sembrado
assert any(h.get("ref_reserva") == "tren-titular" for h in sg.load_seguimiento()["hilos"])
print("✓ 1b cierre por match difuso + siembra de enlace")

# ── 2) Reserva 'a_un_clic' sin hilo → PROPONE, NO crea tarjeta ─────────────────
_reset([{"id": "apto-{{CIUDAD}}", "titulo": "Apartamento Wolframplatz {{CIUDAD}}",
         "estado": "a_un_clic", "categoria": "viaje"},
        {"id": "antifaz", "titulo": "Antifaz para dormir",  # COMPRA (gadget del carrito)
         "estado": "a_un_clic", "categoria": "compra"}])
n_antes = len(sg.load_seguimiento()["hilos"])
rep = rc.reconciliar(aplicar=True)
assert len(sg.load_seguimiento()["hilos"]) == n_antes, "NO debe crear hilos (solo propone)"
assert tt.listar_dudosas(), "debe encolar una propuesta para Vega"
nuevos = [p for p in rep["propuestas"] if p["tipo"] == "nuevo_hilo"]
assert any(p["reserva"] == "apto-{{CIUDAD}}" for p in nuevos), "el viaje a_un_clic se propone"
assert not any(p["reserva"] == "antifaz" for p in nuevos), "la compra (carrito) NO se propone"
print("✓ 2 huérfano de viaje → propuesta; compra del carrito ignorada")

# ── 3) Hilo de ALCANCE AMPLIO con reserva resuelta → NO auto-cierra ────────────
_reset([{"id": "vuelo-x", "titulo": "Vuelo {{CONTACTO}} BOS ZRH", "estado": "RESERVADO_PAGADO"}])
hid = sg.add_hilo({"titulo": "Reservar vuelo {{CONTACTO}} BOS ZRH y coordinar el lab fresco",
                   "estado": "en_curso"})
rep = rc.reconciliar(aplicar=True)
assert _estado_hilo(hid) == "en_curso", "un hilo que agrupa más trabajo NO se auto-cierra"
assert any(p["tipo"] == "revisar_cierre" for p in rep["propuestas"]), "debe proponer revisión"
print("✓ 3 alcance amplio → no se auto-cierra, se propone")

# ── 4) Idempotencia: un segundo fix no vuelve a cerrar nada ────────────────────
_reset([{"id": "v2", "titulo": "Vuelo Ceci AGP ZRH", "estado": "reservado"}])
hid = sg.add_hilo({"titulo": "Reservar vuelo Ceci AGP ZRH", "estado": "en_curso",
                   "ref_reserva": "v2"})
rc.reconciliar(aplicar=True)
rep2 = rc.reconciliar(aplicar=True)
assert not rep2["cerrados"], "segundo fix no debe cerrar nada (idempotente)"
print("✓ 4 idempotencia")

# ── 5) Fail-soft: reservas.json corrupto no tumba el reconciliador ─────────────
_write(sg.SEG, {"hilos": []})
with open(rc.RESERVAS, "w", encoding="utf-8") as f:
    f.write("{ esto no es json valido ")
rep = rc.reconciliar(aplicar=True)
assert isinstance(rep, dict) and rep.get("errores"), "debe reportar el error, no lanzar"
print("✓ 5 fail-soft con JSON corrupto")

# ── 6) Smoke: contexto_lazo inyecta la sección Tablero cuando hay un hilo abierto ──
_write(sg.SEG, {"hilos": []})
sg.add_hilo({"titulo": "Algo urgente con plazo", "estado": "en_curso", "plazo": "2020-01-01"})
import contexto_lazo  # noqa: E402
blo = contexto_lazo.bloque(con_estilo_telegram=False)
assert "Tablero" in blo, "el arranque de sesión debe mostrar el Tablero"
print("✓ 6 contexto_lazo muestra el Tablero")

print("OK test_reconciliar_estado")
