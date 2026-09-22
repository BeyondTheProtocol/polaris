#!/usr/bin/env python3
"""test_avisos_nueva_tarea.py — hook «tarea nueva → aviso» (modelo unificado, {{TITULAR}} 2/7/26).

Cubre el choke-point de tools/seguimiento.py::add_hilo() (clasificador de urgencia DETERMINISTA +
buffer de coalescencia) y tools/avisos_flush.py (vacía el buffer en UN mensaje agrupado). Aislado
en un tmp (BTP_STATE_DIR); mockea salida.report_to_titular para no tocar Telegram de verdad.
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

_TMP = tempfile.mkdtemp(prefix="test_avisos_nueva_tarea_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import seguimiento as s   # noqa: E402
import salida             # noqa: E402
import avisos_flush as af  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


_sent = []


def _mock_report(text, *a, **k):
    _sent.append({"text": text, "kwargs": k})
    return {"delivered": True, "blocked": False, "reason": "entregado (mock)"}


def _reset():
    """Limpia buffer + seguimiento.json + captura de envíos entre grupos de test."""
    s.load_seguimiento()  # noop, solo por simetría
    for p in (s.SEG, s.AVISOS_PENDIENTES):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    _sent.clear()


def _iso(d):
    return d.strftime("%Y-%m-%d")


def main():
    salida.report_to_titular = _mock_report

    # 1) Tarea NED-alta NUEVA de origen autónomo → aviso INMEDIATO, no al buffer.
    _reset()
    s.crear_tarea("Confirmar biopsia", etiqueta="NED", prioridad="alta", origen="whatsapp")
    check("NED-alta nueva → aviso inmediato (1 envío)", len(_sent) == 1)
    check("NED-alta nueva → NO va al buffer", not os.path.exists(s.AVISOS_PENDIENTES))
    check("NED-alta nueva → texto nombra la tarea", "Confirmar biopsia" in _sent[0]["text"])
    check("NED-alta nueva → urgente=True", _sent[0]["kwargs"].get("urgente") is True)

    # 2) Tarea NORMAL nueva de origen autónomo → va al buffer, NO pinga.
    _reset()
    s.crear_tarea("Comprar pastillero", etiqueta="Gestión", prioridad="normal", origen="whatsapp")
    check("normal nueva → NO pinga", len(_sent) == 0)
    check("normal nueva → SÍ va al buffer", os.path.exists(s.AVISOS_PENDIENTES))
    with open(s.AVISOS_PENDIENTES, encoding="utf-8") as f:
        buf = [json.loads(x) for x in f if x.strip()]
    check("normal nueva → 1 línea en el buffer", len(buf) == 1 and buf[0]["titulo"] == "Comprar pastillero")

    # 3) Tarea que toca el cuello de botella (ref_cumbre == cumbre.aqui_estamos) → urgente aunque
    #    no sea NED/alta ni tenga plazo.
    _reset()
    cumbre_path = s.CUMBRE
    with open(cumbre_path, "w", encoding="utf-8") as f:
        json.dump({"aqui_estamos": "biopsia"}, f)
    s.crear_tarea("Logística de la muestra", etiqueta="Gestión", prioridad="normal",
                 origen="correo", ref_cumbre="biopsia")
    check("toca el cuello de botella → aviso inmediato", len(_sent) == 1)
    os.remove(cumbre_path)

    # 4) Tarea con plazo ≤2 días → urgente.
    _reset()
    s.crear_tarea("Enviar documento", etiqueta="Gestión", prioridad="normal", origen="dm",
                 vence=_iso(date.today() + timedelta(days=1)))
    check("plazo mañana → aviso inmediato", len(_sent) == 1)

    _reset()
    s.crear_tarea("Sin prisa", etiqueta="Gestión", prioridad="normal", origen="dm",
                 vence=_iso(date.today() + timedelta(days=10)))
    check("plazo lejano → NO urgente (buffer)", len(_sent) == 0 and os.path.exists(s.AVISOS_PENDIENTES))

    # 5) Flusher agrupa 3 no-urgentes en 1 mensaje.
    _reset()
    s.crear_tarea("A", etiqueta="Gestión", origen="whatsapp")
    s.crear_tarea("B", etiqueta="Gestión", origen="x-menciones")
    s.crear_tarea("C", etiqueta="Gestión", origen="prensa")
    check("3 normales → sin ping antes del flush", len(_sent) == 0)
    res = af.flush()
    check("flush agrupa 3 en 1 mensaje", len(_sent) == 1 and res["n"] == 3)
    check("flush menciona las 3 tareas", all(t in _sent[0]["text"] for t in ("A", "B", "C")))
    check("flush vacía el buffer", not os.path.exists(s.AVISOS_PENDIENTES))
    # Sella ultimo_aviso en las 3.
    seg = s.load_seguimiento()
    sellados = [h for h in seg["hilos"] if h.get("id") in ("a", "b", "c") and h.get("ultimo_aviso")]
    check("flush sella ultimo_aviso en las 3 tareas", len(sellados) == 3)

    # flush sin nada pendiente → no envía nada (silencio > ruido, nunca "0 cosas nuevas").
    _reset()
    res2 = af.flush()
    check("flush sin buffer → no envía", len(_sent) == 0 and res2["enviado"] is False)

    # 6) Update de tarea EXISTENTE (mismo id) → NO re-avisa aunque cambie a NED-alta.
    _reset()
    s.crear_tarea("Tarea repetida", etiqueta="Gestión", prioridad="normal", origen="whatsapp")
    check("creación inicial → va al buffer (no urgente)", len(_sent) == 0 and os.path.exists(s.AVISOS_PENDIENTES))
    os.remove(s.AVISOS_PENDIENTES)   # limpia el buffer para aislar el paso 2
    s.crear_tarea("Tarea repetida", etiqueta="NED", prioridad="alta", origen="whatsapp")  # mismo slug → update
    check("update de tarea existente → NO re-avisa", len(_sent) == 0)
    check("update de tarea existente → NO reaparece en el buffer", not os.path.exists(s.AVISOS_PENDIENTES))

    # 7) Hilo con `ultimo_aviso` YA sellado al crearse (un canal ya avisó por su cuenta) → NO re-avisa
    #    (dedup con correo-urgente/dm-inbox/etc., que sellan su propio aviso).
    _reset()
    hid = s.add_hilo({"titulo": "Correo urgente de {{CONTACTO}}", "origen": "correo",
                      "etiqueta": "NED", "prioridad": "alta",
                      "ultimo_aviso": "2026-07-01T10:00:00"})
    check("ultimo_aviso ya sellado → NO re-avisa aunque sea NED-alta", len(_sent) == 0)
    check("ultimo_aviso ya sellado → NO va al buffer tampoco", not os.path.exists(s.AVISOS_PENDIENTES))
    seg = s.load_seguimiento()
    h = next(x for x in seg["hilos"] if x["id"] == hid)
    check("ultimo_aviso preservado tal cual (no se pisa)", h["ultimo_aviso"] == "2026-07-01T10:00:00")

    # 8) Origen INTERACTIVO ("manual", una sesión conmigo) → NUNCA pinga ni va al buffer.
    _reset()
    s.crear_tarea("Tarea creada en sesión", etiqueta="NED", prioridad="alta", origen="manual")
    check("origen manual/interactivo → NO pinga", len(_sent) == 0)
    check("origen manual/interactivo → NO va al buffer", not os.path.exists(s.AVISOS_PENDIENTES))

    # Origen desconocido/no listado (fail-closed): tampoco dispara aviso (allowlist cerrada).
    _reset()
    s.crear_tarea("Tarea de origen raro", etiqueta="NED", prioridad="alta", origen="algo-nuevo-sin-registrar")
    check("origen fuera de la allowlist → NO pinga (fail-closed)", len(_sent) == 0)
    check("origen fuera de la allowlist → NO va al buffer", not os.path.exists(s.AVISOS_PENDIENTES))

    # 9) Silencio nocturno: report_to_titular devuelve "retenido" (ni delivered ni draft, sin HALT en
    #    reason) → el flusher lo trata como enviado-al-hold: vacía el buffer igualmente (el mensaje
    #    ya vive en el hold de salida.py, no se pierde) y sella ultimo_aviso.
    _reset()
    s.crear_tarea("D", etiqueta="Gestión", origen="whatsapp")

    def _mock_retenido(text, *a, **k):
        _sent.append({"text": text, "kwargs": k})
        return {"delivered": False, "blocked": False,
                "reason": "retenido (silencio nocturno) → resumen a las 08:00", "retenido": True}
    salida.report_to_titular = _mock_retenido
    res3 = af.flush()
    check("noche: flush retiene (no pierde) y vacía buffer local", res3["enviado"] is True
          and not os.path.exists(s.AVISOS_PENDIENTES))
    salida.report_to_titular = _mock_report   # restaura el mock normal

    # HALT real (reason SÍ trae "HALT") → NO vacía el buffer, se reintenta luego.
    _reset()
    s.crear_tarea("E", etiqueta="Gestión", origen="whatsapp")

    def _mock_halt(text, *a, **k):
        _sent.append({"text": text, "kwargs": k})
        return {"delivered": False, "blocked": True, "reason": "HALT activo: salida en pausa total"}
    salida.report_to_titular = _mock_halt
    res4 = af.flush()
    check("HALT real: flush NO vacía el buffer (se reintenta)", res4["enviado"] is False
          and os.path.exists(s.AVISOS_PENDIENTES))
    salida.report_to_titular = _mock_report

    print("RESULTADO avisos_nueva_tarea: %d OK, %d fallos" % (_pass, _fail))
    print("✅ AVISOS NUEVA TAREA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
