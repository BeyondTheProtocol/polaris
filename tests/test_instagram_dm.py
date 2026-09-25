#!/usr/bin/env python3
"""test_instagram_dm.py — batería del canal Instagram DM (envío con gate del muro).

Verifica, SIN tocar la red, que el envío de DMs de Instagram respeta el muro:
  · send(instagram, contact, ...) → SIEMPRE borrador, NUNCA entrega autónoma.
  · approve_and_deliver con nonce correcto → invoca el deliverer (mock); éxito → mueve a sent.
  · entrega que falla (hoy: sin permiso de mensajes) → el borrador SE QUEDA en pending.
  · nonce incorrecto → rechazo, no entrega.
  · _ig_dest_ok: vacío deniega; allowlist presente EXIGE pertenencia; allowlist ilegible deniega.
  · HALT corta la aprobación.
  · _deliver_instagram_dm real sin token → (False, motivo) fail-closed, no revienta.
  · instagram_dm.py: agrupa hilos, resuelve @handle e id directo, y --dry no draftea.

Cada bypass nuevo del canal IG → añade un caso aquí (regresión permanente, AM4).
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida          # noqa: E402
import instagram_dm    # noqa: E402

SELF = "999000111"
DEST = "17841400000000999"   # IGSID de un contacto

_pass = 0
_fail = 0
_ig_calls = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def fake_ig_ok(dest, text):
    _ig_calls.append((dest, text))
    return True, "HTTP 200 (mock)"


def fake_ig_fail(dest, text):
    _ig_calls.append((dest, text))
    return False, "falta instagram_manage_messages (App Review pendiente)"


def setup(tmp):
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    salida.STATE = tmp
    salida.NOTIF_CFG = os.path.join(tmp, "notif", "config.json")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    salida._self_chatid = lambda: SELF
    # allowlist apuntando a un fichero inexistente → sin allowlist = gate por nonce.
    salida.IG_ALLOWLIST = os.path.join(tmp, "noallow.json")


# El nonce ya no está en el borrador (26-sep-26): se captura lo que se le mandaría a {{TITULAR}}.
_NONCES = {}
salida._avisar_nonce = lambda d, n, c: _NONCES.__setitem__(n, c)


def _draft_payload(name):
    return json.load(open(os.path.join(salida.PENDING, name), encoding="utf-8"))


def n_drafts():
    return len(os.listdir(salida.PENDING)) if os.path.isdir(salida.PENDING) else 0


def main():
    tmp = tempfile.mkdtemp(prefix="test_ig_dm_")
    setup(tmp)

    # 1. send instagram/contact → borrador, no entrega.
    salida._DELIVERERS["instagram"] = fake_ig_ok
    _ig_calls.clear()
    r = salida.send("instagram", "contact", DEST, "Hola, te respondo por aquí 💜")
    check("ig contact draftea", "draft" in r and r["blocked"] and not r["delivered"])
    check("ig contact no entrega autónomo", len(_ig_calls) == 0)
    draft = r.get("draft")
    check("ig 'instagram' es canal válido", draft is not None)

    # 2. approve con nonce correcto → invoca deliverer (mock OK) → mueve a sent.
    _ig_calls.clear()
    nonce = _NONCES[draft]
    res = salida.approve_and_deliver(draft, nonce)
    check("ig approve entrega con nonce", res["delivered"] and not res["blocked"])
    check("ig approve invoca deliverer 1 vez al dest", len(_ig_calls) == 1 and _ig_calls[0][0] == DEST)
    check("ig borrador entregado sale de pending", not os.path.exists(os.path.join(salida.PENDING, draft)))
    check("ig borrador entregado va a sent", os.path.exists(os.path.join(salida.OUTBOX, "sent", draft)))

    # 3. entrega que FALLA (sin permiso) → el borrador SE QUEDA en pending.
    salida._DELIVERERS["instagram"] = fake_ig_fail
    _ig_calls.clear()
    r2 = salida.send("instagram", "contact", DEST, "otro intento")
    d2 = r2["draft"]
    res2 = salida.approve_and_deliver(d2, _NONCES[d2])
    check("ig entrega fallida no marca delivered", not res2["delivered"] and res2["blocked"])
    check("ig entrega fallida deja el borrador en pending", os.path.exists(os.path.join(salida.PENDING, d2)))

    # 4. nonce incorrecto → rechazo, no entrega.
    salida._DELIVERERS["instagram"] = fake_ig_ok
    _ig_calls.clear()
    r3 = salida.send("instagram", "contact", DEST, "tercer intento")
    res3 = salida.approve_and_deliver(r3["draft"], "deadbeef00")
    check("ig nonce malo no entrega", not res3["delivered"] and len(_ig_calls) == 0)
    check("ig nonce malo deja borrador", os.path.exists(os.path.join(salida.PENDING, r3["draft"])))

    # 5. _ig_dest_ok: forma + allowlist.
    check("dest vacío deniega", not salida._ig_dest_ok(""))
    check("dest None deniega", not salida._ig_dest_ok(None))
    check("sin allowlist (gate por nonce) permite", salida._ig_dest_ok(DEST))
    # allowlist presente → EXIGE pertenencia.
    json.dump({"ids": ["111", DEST]}, open(salida.IG_ALLOWLIST, "w"))
    check("allowlist permite a los de la lista", salida._ig_dest_ok(DEST))
    check("allowlist deniega a los de fuera", not salida._ig_dest_ok("777"))
    # allowlist ilegible → fail-closed.
    open(salida.IG_ALLOWLIST, "w").write("{rota")
    check("allowlist ilegible deniega (fail-closed)", not salida._ig_dest_ok(DEST))
    os.remove(salida.IG_ALLOWLIST)

    # 5b. approve a un dest fuera de la allowlist → bloqueado, no toca deliverer.
    json.dump({"ids": ["solo-este"]}, open(salida.IG_ALLOWLIST, "w"))
    salida._DELIVERERS["instagram"] = fake_ig_ok
    _ig_calls.clear()
    r5 = salida.send("instagram", "contact", DEST, "fuera de lista")
    res5 = salida.approve_and_deliver(r5["draft"], _NONCES[r5["draft"]])
    check("ig dest no-allowlistado bloquea entrega", res5["blocked"] and len(_ig_calls) == 0)
    check("ig dest no-allowlistado deja borrador", os.path.exists(os.path.join(salida.PENDING, r5["draft"])))
    os.remove(salida.IG_ALLOWLIST)

    # 6. HALT corta la aprobación IG.
    salida._DELIVERERS["instagram"] = fake_ig_ok
    _ig_calls.clear()
    r6 = salida.send("instagram", "contact", DEST, "antes del HALT")
    open(salida.HALT_FILES[1], "w").close()
    res6 = salida.approve_and_deliver(r6["draft"], _NONCES[r6["draft"]])
    check("HALT bloquea approve IG", res6["blocked"] and len(_ig_calls) == 0)
    os.remove(salida.HALT_FILES[1])

    # 7. _deliver_instagram_dm real sin token → fail-closed (no red, no excepción).
    salida._ig_token = lambda: None
    ok, info = salida._deliver_instagram_dm(DEST, "hola")
    check("deliverer real sin token devuelve False", ok is False and "token" in info.lower())

    # 8. instagram_dm.py — agrupar hilos y resolver destinatarios.
    msgs = [
        {"from_id": "1", "conv": "c1", "handle": "contacto", "name": "{{CONTACTO}}", "text": "hey", "ts": 100, "inbox": "principal"},
        {"from_id": "1", "conv": "c1", "handle": "contacto", "name": "{{CONTACTO}}", "text": "último", "ts": 200, "inbox": "principal"},
        {"from_id": "2", "conv": "c2", "handle": "otro", "name": "Otro", "text": "hola", "ts": 50, "inbox": "solicitudes"},
    ]
    threads = instagram_dm._threads(msgs)
    check("agrupa por contacto (2 hilos)", len(threads) == 2)
    contacto = next((t for t in threads if t["handle"] == "contacto"), None)
    check("conserva el último mensaje del contacto", contacto and contacto["text"] == "último")
    dest_h, label_h = instagram_dm._resolve_dest("@contacto", threads)
    check("resuelve @handle a su id", dest_h == "1" and label_h == "@contacto")
    dest_id, _ = instagram_dm._resolve_dest("12345", threads)
    check("acepta id directo", dest_id == "12345")
    dest_no, err = instagram_dm._resolve_dest("@desconocido", threads)
    check("@handle desconocido devuelve error", dest_no is None and "encuentro" in err.lower())

    # 9. cmd_responder --dry → no draftea (cuenta de borradores no cambia).
    before = n_drafts()
    rc = instagram_dm.main(["responder", "--to", DEST, "--texto", "prueba dry", "--dry"])
    check("responder --dry rc=0", rc == 0)
    check("responder --dry no crea borrador", n_drafts() == before)

    # 10. cmd_responder real → crea un borrador por la única boca (salida.send).
    rc2 = instagram_dm.main(["responder", "--to", DEST, "--texto", "respuesta real en mi voz"])
    check("responder real rc=0", rc2 == 0)
    check("responder real crea 1 borrador", n_drafts() == before + 1)

    print("RESULTADO instagram_dm: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CANAL INSTAGRAM EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
