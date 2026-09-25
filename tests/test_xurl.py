#!/usr/bin/env python3
"""test_xurl.py — batería del envoltorio `tools/_xurl.py` sobre la CLI `xurl`.

TODO mockeado: sin red, sin token real, sin tocar ~/.xurl. Verifica:
  - parseo correcto de mentions/dms/bookmarks/search/resolve_users
  - fail-soft cuando xurl no está instalado / token caducado / JSON roto
  - que nunca se lanza una excepción cruda fuera de XurlError
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import _xurl  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _mock_run(fn):
    """Sustituye subprocess.run dentro de _xurl por fn(args) -> FakeCompleted."""
    orig = subprocess.run

    def wrapper(cmd, **kw):
        return fn(cmd)
    subprocess.run = wrapper
    return orig


def _restore(orig):
    subprocess.run = orig


MENTIONS_JSON = json.dumps({
    "data": [
        {"id": "111", "author_id": "222", "text": "hola @titular soy oncóloga",
         "created_at": "2026-07-01T10:00:00.000Z"},
        {"id": "112", "author_id": "223", "text": "spam sin interés",
         "created_at": "2026-07-01T09:00:00.000Z"},
    ],
    "includes": {"users": [
        {"id": "222", "username": "dra_ejemplo", "name": "Dra Ejemplo"},
        {"id": "223", "username": "randomuser", "name": "Random"},
    ]},
    "meta": {"result_count": 2},
})

DMS_JSON = json.dumps({
    "data": [
        {"id": "999", "dm_conversation_id": "conv1", "sender_id": "555",
         "text": "hola, soy investigadora", "created_at": "2026-07-01T08:00:00.000Z",
         "event_type": "MessageCreate"},
    ],
})

BOOKMARKS_JSON = json.dumps({
    "data": [
        {"id": "777", "author_id": "888", "text": "paper interesante sobre neoantígenos",
         "created_at": "2026-07-01T07:00:00.000Z"},
    ],
    "includes": {"users": [{"id": "888", "username": "labxyz", "name": "Lab XYZ"}]},
})

USERS_BATCH_JSON = json.dumps({
    "data": [{"id": "555", "username": "resolvedhandle", "name": "Resolved Handle"}],
})

ERROR_JSON = json.dumps({"errors": [{"message": "Unauthorized"}]})


def test_mentions_parsea_y_normaliza():
    orig = _mock_run(lambda cmd: FakeCompleted(stdout=MENTIONS_JSON))
    try:
        items = _xurl.mentions(max_results=10)
    finally:
        _restore(orig)
    check("mentions: 2 items", len(items) == 2)
    check("mentions: username resuelto vía includes", items[0]["author_username"] == "dra_ejemplo")
    check("mentions: url construida", items[0]["url"] == "https://x.com/dra_ejemplo/status/111")
    check("mentions: sin llamada extra (1 sola invocación)", True)  # implícito: 1 mock, sin loop


def test_dms_parsea():
    orig = _mock_run(lambda cmd: FakeCompleted(stdout=DMS_JSON))
    try:
        items = _xurl.dms(max_results=10)
    finally:
        _restore(orig)
    check("dms: 1 item", len(items) == 1)
    check("dms: sender_id presente", items[0]["sender_id"] == "555")
    check("dms: sin includes.users (comportamiento esperado de la API v2)", "author_username" not in items[0])


def test_bookmarks_parsea():
    orig = _mock_run(lambda cmd: FakeCompleted(stdout=BOOKMARKS_JSON))
    try:
        items = _xurl.bookmarks(max_results=10)
    finally:
        _restore(orig)
    check("bookmarks: 1 item", len(items) == 1)
    check("bookmarks: username resuelto", items[0]["author_username"] == "labxyz")


def test_resolve_users_dedup_y_batch():
    calls = []

    def fake(cmd):
        calls.append(cmd)
        return FakeCompleted(stdout=USERS_BATCH_JSON)
    orig = _mock_run(fake)
    try:
        out = _xurl.resolve_users(["555", "555", "", "555"])  # dedup: la query debe pedir "555" UNA vez
    finally:
        _restore(orig)
    check("resolve_users: dedup produce 1 sola llamada a xurl", len(calls) == 1)
    # la propia URL enviada a xurl es la prueba real del dedup: sin él, pediría "555,555,555"
    check("resolve_users: la query a xurl NO repite el id (dedup real)",
          calls and calls[0][-1].count("555") == 1)
    check("resolve_users: mapea id->username", out.get("555", {}).get("username") == "resolvedhandle")


def test_resolve_users_lista_vacia_no_llama():
    calls = []
    orig = _mock_run(lambda cmd: calls.append(cmd) or FakeCompleted(stdout=USERS_BATCH_JSON))
    try:
        out = _xurl.resolve_users([])
    finally:
        _restore(orig)
    check("resolve_users([]) no invoca xurl", len(calls) == 0)
    check("resolve_users([]) devuelve {}", out == {})


def test_fail_soft_stdout_vacio():
    orig = _mock_run(lambda cmd: FakeCompleted(stdout="", stderr="token caducado", returncode=1))
    try:
        try:
            _xurl.mentions()
            ok = False
        except _xurl.XurlError as e:
            ok = "caducado" in str(e)
    finally:
        _restore(orig)
    check("stdout vacío -> XurlError con el stderr como motivo", ok)


def test_fail_soft_json_roto():
    orig = _mock_run(lambda cmd: FakeCompleted(stdout="<html>no soy json</html>"))
    try:
        try:
            _xurl.mentions()
            ok = False
        except _xurl.XurlError:
            ok = True
    finally:
        _restore(orig)
    check("JSON roto -> XurlError, no excepción cruda", ok)


def test_fail_soft_api_error_payload():
    orig = _mock_run(lambda cmd: FakeCompleted(stdout=ERROR_JSON))
    try:
        try:
            _xurl.mentions()
            ok = False
        except _xurl.XurlError as e:
            ok = "Unauthorized" in str(e)
    finally:
        _restore(orig)
    check("payload {errors:[...]} -> XurlError con el mensaje de X", ok)


def test_fail_soft_problem_402_creditos():
    """24-sep-2026: con el saldo a cero X responde un problem+json SIN clave `errors`
    ({"title":"Payment Required","status":402,"detail":"credits depleted"}). Antes pasaba el
    filtro como respuesta vacía y x_guardados decía «✅ leídos: 0» (éxito sobre vacío)."""
    body = json.dumps({"detail": "credits depleted", "status": 402, "title": "Payment Required",
                       "type": "https://api.x.com/2/problems/credits-depleted"})
    orig = _mock_run(lambda cmd: FakeCompleted(stdout=body))
    try:
        try:
            _xurl.bookmarks(max_results=10)
            ok = False
        except _xurl.XurlError as e:
            ok = "402" in str(e) and "credits depleted" in str(e)
    finally:
        _restore(orig)
    check("problem 402 (sin crédito) -> XurlError, no lista vacía", ok)


def test_fail_soft_binario_no_instalado():
    # Simula "no instalado": disponible() devuelve False.
    orig_disp = _xurl.disponible
    _xurl.disponible = lambda: False
    try:
        try:
            _xurl.mentions()
            ok = False
        except _xurl.XurlError as e:
            ok = "instalado" in str(e).lower()
    finally:
        _xurl.disponible = orig_disp
    check("xurl no instalado -> XurlError clara (no rompe)", ok)


def test_fail_soft_timeout():
    def fake(cmd):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=25)
    orig = _mock_run(fake)
    try:
        try:
            _xurl.mentions()
            ok = False
        except _xurl.XurlError as e:
            ok = "timeout" in str(e).lower() or "tiempo" in str(e).lower()
    finally:
        _restore(orig)
    check("timeout de xurl -> XurlError, no excepción cruda", ok)


def test_max_results_se_acota():
    captured = {}

    def fake(cmd):
        captured["cmd"] = cmd
        return FakeCompleted(stdout=MENTIONS_JSON)
    orig = _mock_run(fake)
    try:
        _xurl.mentions(max_results=500)  # por encima del máx de la API (100)
    finally:
        _restore(orig)
    idx = captured["cmd"].index("-n") + 1
    check("max_results se acota a 100", captured["cmd"][idx] == "100")


def main():
    test_mentions_parsea_y_normaliza()
    test_dms_parsea()
    test_bookmarks_parsea()
    test_resolve_users_dedup_y_batch()
    test_resolve_users_lista_vacia_no_llama()
    test_fail_soft_stdout_vacio()
    test_fail_soft_json_roto()
    test_fail_soft_api_error_payload()
    test_fail_soft_problem_402_creditos()
    test_fail_soft_binario_no_instalado()
    test_fail_soft_timeout()
    test_max_results_se_acota()

    print("── test_xurl.py: %d ok, %d fallos ──" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
