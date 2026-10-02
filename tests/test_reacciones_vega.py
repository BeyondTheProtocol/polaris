#!/usr/bin/env python3
"""test_reacciones_vega.py — las reacciones de {{TITULAR}} en Telegram son señal para Vega (2-oct-2026).

Con un getUpdates falso:
  1. se pide message_reaction (sin pedirlo, Telegram no lo manda) y siguen pidiéndose los mensajes
  2. una reacción de su chat se apunta (emoji, id, señal) y NO llega al bot como mensaje
  3. una reacción de otro chat no se apunta (allowlist)
  4. quitar una reacción no es señal
  5. el mensaje normal sigue llegando igual
  6. perfil_vega cuenta las reacciones en las señales de la semana
"""
import io
import json
import os
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="reac_vega_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_TEST_BATTERY"] = "1"   # mutis de salida.py: este test no puede escribirle a {{TITULAR}}
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


YO = "111"
salida.get_secret = lambda *a, **k: "token-falso"
salida._self_chatid = lambda: YO
pedidas = []
RESPUESTA = {"ok": True, "result": [
    {"update_id": 1, "message_reaction": {"chat": {"id": 111}, "message_id": 50, "date": 1,
                                          "old_reaction": [], "new_reaction": [{"type": "emoji", "emoji": "👎"}]}},
    {"update_id": 2, "message_reaction": {"chat": {"id": 999}, "message_id": 51, "date": 1,
                                          "old_reaction": [], "new_reaction": [{"type": "emoji", "emoji": "👍"}]}},
    {"update_id": 3, "message_reaction": {"chat": {"id": 111}, "message_id": 52, "date": 1,
                                          "old_reaction": [{"type": "emoji", "emoji": "👍"}], "new_reaction": []}},
    {"update_id": 4, "message": {"chat": {"id": 111}, "message_id": 53, "date": 1, "text": "hecho 2"}},
]}


class _Resp(io.BytesIO):
    pass


def falso_urlopen(req, timeout=None):
    pedidas.append(req.full_url if hasattr(req, "full_url") else str(req))
    return _Resp(json.dumps(RESPUESTA).encode("utf-8"))


salida.urllib.request.urlopen = falso_urlopen
msgs = salida.poll_updates(timeout=0)

# 1
q = urllib.parse.parse_qs(urllib.parse.urlparse(pedidas[0]).query)
pedido = json.loads(q.get("allowed_updates", ["[]"])[0])
ok("message_reaction" in pedido and "message" in pedido, "1: ⭐ se pide message_reaction y se siguen pidiendo los mensajes")

# 2-4
ruta = os.path.join(_TMP, "vega", "reacciones.jsonl")
reac = [json.loads(l) for l in open(ruta)] if os.path.exists(ruta) else []
ok(len(reac) == 1 and reac[0]["emoji"] == "👎" and reac[0]["senal"] == "negativa"
   and reac[0]["message_id"] == 50, "2: ⭐ la reacción de {{TITULAR}} se apunta (%s)" % reac)
ok(not any(m.get("message_id") in (50, 51, 52) for m in msgs), "2: y no llega al bot como mensaje")
ok(not any(r["message_id"] == 51 for r in reac), "3: ⭐ la de otro chat no (allowlist)")
ok(not any(r["message_id"] == 52 for r in reac), "4: quitar una reacción no es señal")

# 5
ok([m.get("text") for m in msgs] == ["hecho 2"], "5: el mensaje normal llega igual")

# 6
import perfil_vega  # noqa: E402
lineas = perfil_vega.senales()
ok(any("negativa 1" in l for l in lineas), "6: ⭐ el perfil cuenta las reacciones (%s)" % lineas)

if _fail:
    print("❌ test_reacciones_vega: %d fallo(s)" % _fail)
    sys.exit(1)
print("✅ test_reacciones_vega: tus reacciones llegan al perfil de Vega, solo de tu chat y sin tocar los mensajes")
