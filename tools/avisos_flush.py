#!/usr/bin/env python3
"""tools/avisos_flush.py — flusher de coalescencia de avisos "tarea nueva" (§3 del modelo unificado
de avisos, {{TITULAR}} 2/7/26). DETERMINISTA, sin LLM, sin red, $0.

PROBLEMA QUE RESUELVE: el hook de `tools/seguimiento.py::add_hilo()` avisa AL MOMENTO de una tarea
NUEVA urgente (NED-alta / toca el cuello de botella / plazo ≤2 días), pero las tareas NO-urgentes
(la mayoría: un mensaje de WhatsApp, una mención en X, un correo normal) se acumulan en un buffer
(`tools/state/avisos/pendientes.jsonl`) para no saturar a {{TITULAR}} con un ping por cada una. Este
script vacía ese buffer en UN solo mensaje agrupado por `salida.report_to_titular` — "1 mensaje, no
un ping por tarea" (la norma que ya vale para el parte de HOY).

Reglas duras:
  · Choke-point único: TODO el egress pasa por `salida.report_to_titular` (respeta HALT, silencio
    nocturno, anti-spam — nada de eso se reimplementa aquí).
  · Sin tareas pendientes → no hace nada, no manda "0 cosas nuevas" (silencio > ruido).
  · Vacía el buffer y sella `ultimo_aviso` en cada hilo avisado SOLO si el envío no fue bloqueado
    por HALT (si HALT está activo, `report_to_titular` no entrega y este script NO vacía el buffer,
    así nada se pierde: se reintenta en el próximo ciclo).
  · Fail-soft: una línea corrupta del buffer se salta; no tira el flush entero.

Uso:
  python3 tools/avisos_flush.py            # vacía el buffer y avisa si hay algo
  python3 tools/avisos_flush.py --dry      # no envía; solo imprime qué mandaría
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento  # noqa: E402 — reusa AVISOS_PENDIENTES/AVISOS_DIR/SEG/_write_atomic/load_seguimiento


def _leer_pendientes():
    """Lee el buffer línea a línea (fail-soft: una línea corrupta se salta). Devuelve la lista de
    registros y el conteo de líneas corruptas descartadas."""
    if not os.path.exists(seguimiento.AVISOS_PENDIENTES):
        return [], 0
    items, corruptas = [], 0
    with open(seguimiento.AVISOS_PENDIENTES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                corruptas += 1
    return items, corruptas


def _vaciar_buffer():
    try:
        os.remove(seguimiento.AVISOS_PENDIENTES)
    except FileNotFoundError:
        pass


def _sellar(ids, ahora):
    """Sella `ultimo_aviso` en cada hilo avisado, en un solo read-modify-write (evita N escrituras)."""
    ids = set(i for i in ids if i)
    if not ids:
        return
    seg = seguimiento.load_seguimiento()
    if seg.get("_error"):
        return
    for h in seg.get("hilos", []):
        if h.get("id") in ids:
            h["ultimo_aviso"] = ahora
    seguimiento._write_atomic(seguimiento.SEG, seg)


def construir_mensaje(items):
    n = len(items)
    cosa = "cosa" if n == 1 else "cosas"
    lineas = ["📋 Vega registró %d %s nuevas:" % (n, cosa)]
    for it in items[:20]:
        icono = it.get("icono") or "📌"
        lineas.append("· %s %s" % (icono, it.get("titulo", "?")))
    if n > 20:
        lineas.append("· …y %d más" % (n - 20))
    return "\n".join(lineas)


def flush(dry=False):
    items, corruptas = _leer_pendientes()
    if corruptas:
        sys.stderr.write("avisos_flush: aviso, %d línea(s) corrupta(s) descartada(s) del buffer\n" % corruptas)
    if not items:
        return {"enviado": False, "n": 0, "motivo": "nada pendiente"}

    texto = construir_mensaje(items)
    if dry:
        return {"enviado": False, "n": len(items), "motivo": "dry", "texto": texto}

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import salida
    res = salida.report_to_titular(texto, voz="sobria")
    # Silencio nocturno: `report_to_titular` retiene el texto (no lo pierde: pasa a acumularse en el
    # hold de salida.py y sale en el resumen de la mañana) pero el buffer local de avisos_flush debe
    # vaciarse igualmente — no hace falta guardarlo dos veces. Solo si HALT bloqueó de verdad (ni
    # entregó ni draft-eó ni retuvo) se deja el buffer intacto para reintentarlo en el próximo ciclo.
    bloqueado_halt = (not res.get("delivered") and not res.get("draft")
                       and "HALT" in (res.get("reason") or ""))
    if bloqueado_halt:
        return {"enviado": False, "n": len(items), "motivo": "HALT activo, buffer conservado"}

    ahora = datetime.now().isoformat(timespec="seconds")
    _sellar([it.get("id") for it in items], ahora)
    _vaciar_buffer()
    return {"enviado": True, "n": len(items), "motivo": res.get("reason", "")}


def main(argv):
    dry = "--dry" in argv
    res = flush(dry=dry)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
