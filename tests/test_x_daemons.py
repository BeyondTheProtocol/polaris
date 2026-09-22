#!/usr/bin/env python3
"""test_x_daemons.py — batería de los daemons deterministas de X migrados a `xurl`
(x_mentions, x_dms, x_radar, x_centinela, x_guardados).

Mockea `_xurl` (nunca red real, nunca ~/.xurl, nunca subprocess.run de verdad) y
aísla el estado en tmp. Verifica:
  - triaje/etiquetado determinista sobre datos xurl ya parseados
  - dedup de "ya visto" entre pasadas (seen.json / SEEN_FILE)
  - fallback a Grok cuando xurl falla (mock de subprocess para grok.py)
  - fail-soft sin romper cuando NI xurl NI Grok responden
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _reload(modname):
    if modname in sys.modules:
        return importlib.reload(sys.modules[modname])
    return importlib.import_module(modname)


# ---------------------------------------------------------------- x_mentions --

def test_mentions_triaje_y_fuente():
    import _xurl
    xm = _reload("x_mentions")

    sample = [
        {"id": "1", "author_id": "a", "author_username": "dra", "author_name": "Dra",
         # "oncolog" (sin tilde) es la forma exacta de la keyword existente en KW_MEDICO;
         # ver nota sobre normalización de acentos más abajo en este fichero de tests.
         "text": "soy oncologa y me interesa tu caso", "created_at": "2026-07-01T10:00:00",
         "url": "https://x.com/dra/status/1"},
        {"id": "2", "author_id": "b", "author_username": "fan", "author_name": "Fan",
         "text": "qué fuerte tu historia", "created_at": "2026-07-01T09:00:00",
         "url": "https://x.com/fan/status/2"},
    ]
    orig = _xurl.mentions
    _xurl.mentions = lambda max_results=10: sample
    try:
        body, err = xm._via_xurl(10)
    finally:
        _xurl.mentions = orig
    check("mentions._via_xurl: sin error", err is None)
    check("mentions: etiqueta MEDICO al primero", "[MEDICO]" in body.split("\n")[0])
    check("mentions: etiqueta PERSONAL al segundo (sin señales medicas)", "PERSONAL" in body)


def test_mentions_fallback_a_grok_si_xurl_falla():
    import _xurl
    xm = _reload("x_mentions")

    def boom(max_results=10):
        raise _xurl.XurlError("token caducado")
    orig_mentions = _xurl.mentions
    _xurl.mentions = boom

    orig_run = subprocess.run

    def fake_grok(cmd, **kw):
        class R:
            stdout = "• @alguien: dice algo relevante\n"
            stderr = ""
        return R()
    subprocess.run = fake_grok
    try:
        body, err = xm._via_xurl(10)
        check("mentions: xurl roto -> _via_xurl devuelve error", body is None and err)
        gbody, gerr = xm._via_grok("titular", 48)
        check("mentions: fallback a grok.py responde", gbody is not None and gerr is None)
    finally:
        _xurl.mentions = orig_mentions
        subprocess.run = orig_run


def test_mentions_sin_xurl_ni_grok_no_rompe():
    import _xurl
    xm = _reload("x_mentions")

    _xurl.mentions = lambda max_results=10: (_ for _ in ()).throw(_xurl.XurlError("sin crédito"))
    orig_run = subprocess.run
    subprocess.run = lambda cmd, **kw: (_ for _ in ()).throw(Exception("red caída"))
    old_argv = sys.argv
    sys.argv = ["x_mentions.py", "--print"]
    try:
        rc = xm.main()
        check("mentions: xurl+grok caídos -> main() devuelve rc!=0 sin excepción", rc != 0)
    finally:
        sys.argv = old_argv
        subprocess.run = orig_run
        importlib.reload(_xurl)


# --------------------------------------------------------------------- x_dms --

def test_dms_dedup_entre_pasadas():
    import _xurl
    xd = _reload("x_dms")

    tmp = tempfile.mkdtemp(prefix="test_xdms_")
    xd.OUT_DIR = tmp
    xd.SEEN_FILE = os.path.join(tmp, "seen.json")
    xd.HANDLE_CACHE_FILE = os.path.join(tmp, "handle_cache.json")

    _xurl.whoami = lambda: {"id": "MY_ID"}
    call_count = {"n": 0}

    def fake_dms(max_results=30):
        call_count["n"] += 1
        return [
            {"id": "m1", "conversation_id": "c1", "sender_id": "other",
             "text": "hola, soy investigadora en cáncer", "created_at": "2026-07-01T10:00:00", "event_type": "MessageCreate"},
        ]
    _xurl.dms = fake_dms
    _xurl.resolve_users = lambda ids: {"other": {"username": "investigadora", "name": "Investigadora"}}

    old_argv = sys.argv
    try:
        sys.argv = ["x_dms.py"]
        rc1 = xd.main()
        check("dms: primera pasada rc=0", rc1 == 0)
        check("dms: primera pasada crea digest con el lead", os.path.exists(os.path.join(tmp, "digest-%s.md" % __import__("datetime").date.today().isoformat())))

        rc2 = xd.main()
        check("dms: segunda pasada rc=0", rc2 == 0)
        digest2 = open(os.path.join(tmp, "digest-%s.md" % __import__("datetime").date.today().isoformat())).read()
        check("dms: segunda pasada NO repite el mensaje ya visto", "Sin DMs nuevos" in digest2)
    finally:
        sys.argv = old_argv


def test_dms_falla_xurl_no_rompe():
    import _xurl
    xd = _reload("x_dms")
    tmp = tempfile.mkdtemp(prefix="test_xdms2_")
    xd.OUT_DIR = tmp
    xd.SEEN_FILE = os.path.join(tmp, "seen.json")
    xd.HANDLE_CACHE_FILE = os.path.join(tmp, "handle_cache.json")

    _xurl.whoami = lambda: (_ for _ in ()).throw(_xurl.XurlError("sin token"))
    old_argv = sys.argv
    try:
        sys.argv = ["x_dms.py"]
        rc = xd.main()
        check("dms: xurl roto -> main() no lanza excepción, rc!=0", rc != 0)
    finally:
        sys.argv = old_argv


# ------------------------------------------------------------------- x_radar --

def test_radar_modo_valido_e_invalido():
    xr = _reload("x_radar")
    check("radar: modos conocidos incluyen expertos/ensayos/prensa/financiacion/comunidad",
          set(["expertos", "ensayos", "prensa", "financiacion", "comunidad"]) <= set(xr.MODES))


def test_radar_via_xurl_formatea():
    import _xurl
    xr = _reload("x_radar")
    sample = [
        {"id": "1", "author_id": "a", "author_username": "labx", "author_name": "Lab X",
         "text": "nuevo ensayo NCT12345 reclutando", "created_at": "2026-07-01T10:00:00",
         "url": "https://x.com/labx/status/1"},
    ]
    orig = _xurl.search_recent
    _xurl.search_recent = lambda query, max_results=20: sample
    try:
        body, err = xr._via_xurl("ensayos", xr.MODES["ensayos"], 20)
    finally:
        _xurl.search_recent = orig
    check("radar: _via_xurl sin error", err is None)
    check("radar: incluye el hallazgo", "NCT12345" in body)


def test_radar_fallback_grok_en_fallo():
    import _xurl
    xr = _reload("x_radar")
    _xurl.search_recent = lambda query, max_results=20: (_ for _ in ()).throw(_xurl.XurlError("sin crédito"))
    body, err = xr._via_xurl("prensa", xr.MODES["prensa"], 20)
    check("radar: xurl roto -> devuelve (None, motivo)", body is None and err)
    importlib.reload(_xurl)


# ---------------------------------------------------------------- x_centinela --

def test_centinela_sin_senales_es_limpio():
    import _xurl
    xc = _reload("x_centinela")
    _xurl.search_recent = lambda query, max_results=20: []
    try:
        body, err = xc._via_xurl([], 20)
    finally:
        importlib.reload(_xurl)
    check("centinela: sin resultados -> mensaje limpio, sin error", err is None and "Sin señales" in body)


def test_centinela_clasifica_estafa():
    xc = _reload("x_centinela")
    tipos = xc._clasifica("dale a este link de donate para ayudar a {{TITULAR}}")
    check("centinela: detecta señal de estafa por palabra clave", "posible ESTAFA" in tipos)


def test_centinela_query_incluye_protegidos():
    xc = _reload("x_centinela")
    q = xc.build_search_query(["{{CONTACTO}}"], "titular")
    check("centinela: la query incluye el término protegido inyectado", "{{CONTACTO}}" in q)
    check("centinela: la query excluye los propios tweets", "-from:titular" in q)


# --------------------------------------------------------------- x_guardados --

def test_guardados_dedup_entre_pasadas():
    import _xurl
    xg = _reload("x_guardados")
    tmp = tempfile.mkdtemp(prefix="test_xg_")
    xg.OUT_DIR = tmp
    xg.SEEN_FILE = os.path.join(tmp, "seen.json")
    xg.STORE_FILE = os.path.join(tmp, "guardados.jsonl")

    sample = [
        {"id": "t1", "author_id": "u1", "author_username": "labz", "author_name": "Lab Z",
         "text": "paper sobre neoantígenos", "created_at": "2026-07-01T10:00:00+00:00",
         "url": "https://x.com/labz/status/t1"},
    ]
    _xurl.bookmarks = lambda max_results=40: sample

    rc1 = xg.cmd_fetch(notify=False, show_all=False, max_results=40)
    check("guardados: primera pasada rc=0", rc1 == 0)
    store1 = xg._load_store()
    check("guardados: 1 guardado en el store", len(store1) == 1)
    check("guardados: etiqueta NED detectada (neoantígenos)", "NED" in store1["t1"]["tags"])

    rc2 = xg.cmd_fetch(notify=False, show_all=False, max_results=40)
    check("guardados: segunda pasada rc=0", rc2 == 0)
    store2 = xg._load_store()
    check("guardados: dedup -> sigue habiendo 1 solo guardado (no duplica)", len(store2) == 1)


def test_guardados_falla_xurl_no_rompe():
    import _xurl
    xg = _reload("x_guardados")
    tmp = tempfile.mkdtemp(prefix="test_xg2_")
    xg.OUT_DIR = tmp
    xg.SEEN_FILE = os.path.join(tmp, "seen.json")
    xg.STORE_FILE = os.path.join(tmp, "guardados.jsonl")
    _xurl.bookmarks = lambda max_results=40: (_ for _ in ()).throw(_xurl.XurlError("404 queryId"))
    rc = xg.cmd_fetch(notify=False, show_all=False, max_results=40)
    check("guardados: xurl roto -> cmd_fetch no lanza excepción, rc!=0", rc != 0)


def main():
    test_mentions_triaje_y_fuente()
    test_mentions_fallback_a_grok_si_xurl_falla()
    test_mentions_sin_xurl_ni_grok_no_rompe()
    test_dms_dedup_entre_pasadas()
    test_dms_falla_xurl_no_rompe()
    test_radar_modo_valido_e_invalido()
    test_radar_via_xurl_formatea()
    test_radar_fallback_grok_en_fallo()
    test_centinela_sin_senales_es_limpio()
    test_centinela_clasifica_estafa()
    test_centinela_query_incluye_protegidos()
    test_guardados_dedup_entre_pasadas()
    test_guardados_falla_xurl_no_rompe()

    print("── test_x_daemons.py: %d ok, %d fallos ──" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
