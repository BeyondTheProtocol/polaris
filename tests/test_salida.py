#!/usr/bin/env python3
"""test_salida.py — batería del choke-point único de salida (B3 del muro).

Verifica, sin tocar la red, que tools/salida.py cumple la política del muro:
  · HALT (cualquiera de los dos kill-switches) → bloquea TODO; NO entrega.
  · OUTWARD (publish/contact/pay) → SIEMPRE borrador, NUNCA entrega autónoma.
  · REPORT a destino que no es {{TITULAR}} → borrador (salida disfrazada de report).
  · REPORT a {{TITULAR}} → entrega por el canal (mock); dry no toca el canal.
  · Forma inválida (canal/acción/texto) → bloqueo.
  · El deliverer (la boca hacia fuera) SOLO se invoca en el caso REPORT-a-{{TITULAR}} real.
  · Cada intento queda auditado.

Cada bypass nuevo del choke-point → añade un caso aquí (regresión permanente).
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

SELF = "999000111"        # chat_id de "{{TITULAR}}" para el test
OTHER = "555000222"       # cualquier tercero

_pass = 0
_fail = 0
_delivers = []            # registro de llamadas al deliverer (la boca hacia fuera)


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def fake_deliver(chat_id, text, reply_to=None):
    _delivers.append((chat_id, text, reply_to))
    return True, "mock"


def setup_module(tmp):
    """Aísla el estado del módulo en un tmp y mockea credenciales/entrega."""
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    salida.STATE = tmp
    # NOTIF_CFG es constante de módulo (se fija al importar con el STATE real). Hay que
    # aislarla TAMBIÉN, o el test lee la config de avisos REAL y, de noche, el silencio
    # nocturno retiene los REPORT → los checks de "entrega" fallarían (falso negativo).
    salida.NOTIF_CFG = os.path.join(tmp, "notif", "config.json")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    salida._self_chatid = lambda: SELF
    salida._DELIVERERS = {"telegram": fake_deliver}


def n_drafts():
    return len(os.listdir(salida.PENDING)) if os.path.isdir(salida.PENDING) else 0


def n_audit_lines():
    if not os.path.isdir(salida.OUTBOX):
        return 0
    total = 0
    for f in os.listdir(salida.OUTBOX):
        if f.startswith("audit-") and f.endswith(".jsonl"):
            total += sum(1 for _ in open(os.path.join(salida.OUTBOX, f)))
    return total


def main():
    tmp = tempfile.mkdtemp(prefix="test_salida_")
    setup_module(tmp)
    halt_inner = salida.HALT_FILES[1]

    # 1. REPORT a {{TITULAR}} (real) → entrega por el canal mock.
    _delivers.clear()
    r = salida.send("telegram", "report", None, "hola {{TITULAR}}")
    check("report-self entrega", r["delivered"] and not r["blocked"])
    check("report-self invoca deliverer 1 vez", len(_delivers) == 1 and _delivers[0][0] == SELF)

    # 2. dry: NO entrega, NO bloquea, NO toca el canal.
    _delivers.clear()
    r = salida.send("telegram", "report", None, "seco", dry=True)
    check("dry no entrega", not r["delivered"] and r.get("dry"))
    check("dry no toca deliverer", len(_delivers) == 0)

    # 3. OUTWARD (las tres acciones) → borrador, NUNCA entrega, NUNCA toca el canal.
    for act in ("publish", "contact", "pay"):
        _delivers.clear()
        before = n_drafts()
        r = salida.send("telegram", act, OTHER, "texto hacia fuera")
        check("outward %s no entrega" % act, not r["delivered"] and r["blocked"])
        check("outward %s draftea" % act, "draft" in r and n_drafts() == before + 1)
        check("outward %s no toca deliverer" % act, len(_delivers) == 0)

    # 4. OUTWARD aunque el destino fuese el de {{TITULAR}} → sigue siendo gate.
    _delivers.clear()
    r = salida.send("telegram", "contact", SELF, "contactar")
    check("outward-a-self sigue gate", r["blocked"] and len(_delivers) == 0)

    # 5. REPORT a un tercero (no {{TITULAR}}) → borrador, no entrega.
    _delivers.clear()
    r = salida.send("telegram", "report", OTHER, "report a otro")
    check("report-a-otro draftea", r["blocked"] and "draft" in r and len(_delivers) == 0)

    # 6. HALT (kill-switch interno) → bloquea TODO, ni siquiera draftea, no toca canal.
    _delivers.clear()
    open(halt_inner, "w").close()
    r = salida.send("telegram", "report", None, "con HALT")
    check("HALT bloquea report", r["blocked"] and not r["delivered"])
    check("HALT no toca deliverer", len(_delivers) == 0)
    r2 = salida.send("telegram", "publish", OTHER, "publish con HALT")
    check("HALT bloquea outward", r2["blocked"] and len(_delivers) == 0)
    os.remove(halt_inner)

    # 6b. HALT externo (~/.btp.HALT homólogo) también corta.
    _delivers.clear()
    open(salida.HALT_FILES[0], "w").close()
    r = salida.send("telegram", "report", None, "con HALT externo")
    check("HALT externo bloquea", r["blocked"] and len(_delivers) == 0)
    os.remove(salida.HALT_FILES[0])

    # 7. Forma inválida → bloqueo, sin entrega.
    _delivers.clear()
    check("canal desconocido bloquea", salida.send("sms", "report", None, "x")["blocked"])
    check("accion desconocida bloquea", salida.send("telegram", "spam", None, "x")["blocked"])
    check("texto vacio bloquea", salida.send("telegram", "report", None, "  ")["blocked"])
    check("forma invalida no toca deliverer", len(_delivers) == 0)

    # 7b. text no-str: se coacciona, no revienta (fail-closed limpio).
    _delivers.clear()
    r = salida.send("telegram", "report", None, 12345)
    check("text no-str se coacciona y entrega", r["delivered"] and len(_delivers) == 1)
    check("text None bloquea como vacio", salida.send("telegram", "report", None, None)["blocked"])

    # 8. report_to_titular (atajo) entrega.
    _delivers.clear()
    check("report_to_titular entrega", salida.report_to_titular("hi")["delivered"])

    # 9. Auditoría: hubo registros (no exigimos número exacto, sí que registra).
    check("auditoria registra intentos", n_audit_lines() >= 10)

    # 10. CATEGORÍA OPERATIVO: NO llega a Telegram; SÍ al log; default es humano (fail-safe).
    _delivers.clear()
    r_op = salida.report_to_titular("revise de salud interna", categoria="operativo")
    check("operativo no entrega a Telegram", not r_op["delivered"] and not r_op.get("blocked"))
    check("operativo devuelve operativo=True", r_op.get("operativo") is True)
    check("operativo no toca deliverer", len(_delivers) == 0)

    # El log operativo se creó en el tmp aislado.
    op_dir = os.path.join(tmp, "operativo")
    import time as _time
    op_log = os.path.join(op_dir, "operativo-%s.jsonl" % _time.strftime("%Y-%m-%d"))
    check("operativo escribe al log", os.path.isfile(op_log))
    # Verifica que el log contiene la línea con el texto.
    import json as _json
    log_lines = []
    if os.path.isfile(op_log):
        with open(op_log) as fh:
            log_lines = [_json.loads(ln) for ln in fh if ln.strip()]
    check("operativo log contiene el texto", any("revise de salud interna" in (e.get("texto") or "") for e in log_lines))

    # La auditoría del operativo lleva veredicto "operativo-log".
    audit_lines = []
    if os.path.isdir(salida.OUTBOX):
        for fn in os.listdir(salida.OUTBOX):
            if fn.startswith("audit-") and fn.endswith(".jsonl"):
                with open(os.path.join(salida.OUTBOX, fn)) as fh:
                    for ln in fh:
                        try:
                            audit_lines.append(_json.loads(ln))
                        except Exception:
                            pass
    check("operativo audita con veredicto operativo-log",
          any(a.get("veredicto") == "operativo-log" for a in audit_lines))

    # El portero de ruido (27-jul) aplaza a partir de TOPE_AVISOS_DIA entregas al día, y a estas
    # alturas esta misma batería ya ha gastado el cupo en su outbox aislado: por eso la del MURO
    # salía ROJA por su propio ruido. Aquí se comprueba el fail-safe de CATEGORÍA, no el presupuesto
    # — el portero tiene su batería en test_portero_ruido.py.
    salida.TOPE_AVISOS_DIA = 10 ** 6

    # Default "humano" = fail-safe: si no se marca, NO se pierde. Con el tope neutralizado arriba
    # esto entrega; el "o aplaza" deja la afirmación cierta también si el portero cambia de forma,
    # y el "nunca bloqueado" es el invariante que de verdad protege el muro.
    _delivers.clear()
    r_hum = salida.report_to_titular("mensaje normal sin categoria")
    check("humano (default) no se pierde (entrega o aplaza al parte)",
          bool(r_hum["delivered"]) or bool(r_hum.get("aplazado")))
    check("humano (default) NUNCA queda bloqueado", not r_hum.get("blocked"))
    check("humano (default) toca deliverer si de verdad entrega",
          len(_delivers) == (1 if r_hum["delivered"] else 0))

    # urgente=True + categoria="operativo" → prevalece humano (regla de oro: nunca esconder urgente).
    _delivers.clear()
    r_urg = salida.report_to_titular("urgente operativo", urgente=True, categoria="operativo")
    check("urgente+operativo prevale humano (entrega)", r_urg["delivered"])
    check("urgente+operativo toca deliverer", len(_delivers) == 1)

    print("RESULTADO salida.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CHOKE-POINT EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
