#!/usr/bin/env python3
"""test_centinela_ned.py — el centinela NED (surface-en-el-momento, F4.0).

Verifica: primera pasada = línea base (NO avisa, evita burst de correos viejos); correo NED-crítico
NUEVO → avisa nombrando al remitente; idempotente; cambio de FOCO de cumbre → avisa; plazo NED que
entra a hoy → avisa; el marcador NO guarda PII. Aislado en tmp; salida/correo/cumbre/seguimiento
mockeados de forma determinista (cero red, cero egress real)."""
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="centinela_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP                      # que NO toque casa base
sys.path.insert(0, os.path.join(ROOT, "tools"))
import centinela_ned as cn          # noqa: E402
import correo                       # noqa: E402
import cumbre                       # noqa: E402
import seguimiento                  # noqa: E402
import salida                       # noqa: E402

_sent = []
salida.report_to_titular = lambda text, **k: _sent.append(text)

# Controlables deterministas (el centinela los llama vía `import` → mismo objeto módulo).
correo.es_ned_critico = lambda sender="", subject="": "BIOPSIA" in (sender + subject).upper()
_FOCO = {"id": "biopsia", "titulo": "Re-biopsia L1 Zúrich", "estado": "en_curso", "bloqueo": "cita por confirmar"}
cumbre.foco = lambda: _FOCO
seguimiento.recopilar = lambda: {"items": []}

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def buzon(msgs):
    os.makedirs(os.path.join(_TMP, "correo"), exist_ok=True)
    json.dump(msgs, open(os.path.join(_TMP, "correo", "buzon.json"), "w"))


def main():
    # 1. PRIMERA pasada: hay correo crítico, pero NO avisa (fija la línea base)
    buzon([{"id": "m1", "remitente": "{{CONTACTO}} {{CONTACTO}}", "asunto": "biopsia confirmada"},
           {"id": "m2", "remitente": "spam", "asunto": "oferta"}])
    cn.run()
    ok(len(_sent) == 0, "primera pasada = línea base, NO avisa (%d)" % len(_sent))

    # 2. sin cambios → silencio
    cn.run()
    ok(len(_sent) == 0, "sin cambios → silencio")

    # 3. correo NED-crítico NUEVO → avisa nombrando al remitente
    buzon([{"id": "m1", "remitente": "{{CONTACTO}} {{CONTACTO}}", "asunto": "biopsia confirmada"},
           {"id": "m3", "remitente": "{{CONTACTO}} {{CONTACTO}}", "asunto": "re: biopsia neoantígenos"}])
    cn.run()
    ok(len(_sent) == 1, "correo NED-crítico nuevo → 1 aviso (%d)" % len(_sent))
    ok(_sent and "{{CONTACTO}} {{CONTACTO}}" in _sent[-1], "el aviso NOMBRA al remitente")
    ok(_sent and "📩" in _sent[-1], "marca de correo")

    # 4. idempotente: mismo buzón → no re-avisa
    cn.run()
    ok(len(_sent) == 1, "idempotente: no re-avisa el mismo correo")

    # 5. cambio de FOCO de cumbre → avisa
    cumbre.foco = lambda: {"id": "dianas", "titulo": "Identificar dianas", "estado": "en_curso", "bloqueo": "depende de biopsia"}
    cn.run()
    ok(len(_sent) == 2 and "cambio" in _sent[-1].lower(), "cambio de foco de cumbre → avisa")

    # 6. plazo NED que entra a HOY → avisa; y es idempotente
    # El centinela lee seguimiento.json del disco (no el módulo seguimiento) → hay que escribirlo.
    hoy = datetime.date.today().isoformat()
    seg_data = {"items": [
        {"id": "cita-contacto", "titulo": "Cita biopsia Zúrich", "estado": "en_curso", "categoria": "NED", "vence": hoy}]}
    json.dump(seg_data, open(os.path.join(_TMP, "seguimiento.json"), "w"))
    cn.run()
    ok(len(_sent) == 3 and "se acerca" in _sent[-1].lower(), "plazo NED que entra a hoy → avisa")
    cn.run()
    ok(len(_sent) == 3, "plazo idempotente: no re-avisa")

    # 7. el marcador NO guarda PII (solo hashes/ids)
    mark = json.load(open(os.path.join(_TMP, "centinela", "last_seen.json")))
    blob = json.dumps(mark, ensure_ascii=False)
    ok("{{CONTACTO}}" not in blob and "{{CONTACTO}}" not in blob and "biopsia confirmada" not in blob,
       "el marcador NO guarda PII")

    # 8. fail-soft: sin buzón (gate App Password) no peta ni inventa
    os.remove(os.path.join(_TMP, "correo", "buzon.json"))
    seguimiento.recopilar = lambda: {"items": []}
    cumbre.foco = lambda: {"id": "dianas", "titulo": "Identificar dianas", "estado": "en_curso", "bloqueo": "depende de biopsia"}
    antes = len(_sent)
    cn.run()
    ok(len(_sent) == antes, "sin buzón → señal de correo dormida, no peta")

    # 9. anti-duplicado ENTRE daemons: si el poller (correo_imap) ya reclamó el aviso de un correo
    #    vía correo.reclamar_aviso, el centinela NO lo repite (bug 3/7: «📬 Correo nuevo…» +
    #    «📩 Oye, te ha escrito…» del mismo correo). Requiere que el mensaje traiga remitente_email
    #    (la clave del ledger, tal como lo escribe correo_imap en el buzón).
    base = len(_sent)
    ya = {"id": "m9", "remitente": "Elizabeth Vega", "remitente_email": "e.vega@{{CENTRO}}.org",
          "asunto": "re: biopsia BIO121619 {{CENTRO}}"}
    buzon([ya])
    correo.reclamar_aviso("e.vega@{{CENTRO}}.org", "re: biopsia BIO121619 {{CENTRO}}")  # poller avisa 1º
    cn.run()
    ok(len(_sent) == base, "correo ya reclamado por el poller → centinela NO duplica (%d)" % (len(_sent) - base))

    # 9b. el mismo mecanismo NO calla un NED-crítico que nadie reclamó antes
    nuevo = {"id": "m10", "remitente": "Nuevo Lab", "remitente_email": "hola@lab-nuevo.org",
             "asunto": "re: biopsia — hueco de cita"}
    buzon([ya, nuevo])
    cn.run()
    ok(len(_sent) == base + 1 and "Nuevo Lab" in _sent[-1],
       "correo no reclamado por nadie → centinela SÍ avisa (%d)" % (len(_sent) - base))

    _frescura_del_cache()

    print("RESULTADO centinela NED: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CENTINELA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


def _escribir_cache(estados, edad_h):
    """Caché de NCTs con una antigüedad concreta (None = sin _ts_consulta)."""
    os.makedirs(os.path.join(_TMP, "centinela"), exist_ok=True)
    raw = {k: {"overallStatus": v} for k, v in estados.items()}
    if edad_h is not None:
        raw["_ts_consulta"] = (datetime.datetime.now()
                               - datetime.timedelta(hours=edad_h)).isoformat(timespec="seconds")
    json.dump(raw, open(cn.NCT_CACHE, "w", encoding="utf-8"))


def _frescura_del_cache():
    """Regresión: el caché llevaba 29 días congelado y el centinela comparaba fósil contra
    fósil sin enterarse, porque `_ts_consulta` la descartaba el filtro de claves y nadie la leía."""
    NCT = "NCT05098210"
    cn._nct_ids_vigilados = lambda: {NCT}          # determinista, sin leer los JSON vivos
    mark = os.path.join(_TMP, "centinela", "last_seen.json")

    def _pasada(estados, edad_h, prev, dia_avisado=None):
        _escribir_cache(estados, edad_h)
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        json.dump({"ts": "2026-01-01T00:00:00", "nct_estados": prev,
                   "nct_rancio_avisado": dia_avisado}, open(mark, "w"))
        return cn.detectar()

    # Caché fresco + cambio real → avisa del cambio, y NO de rancidez.
    av, est = _pasada({NCT: "SUSPENDED"}, 3, {NCT: "RECRUITING"})
    tipos = [t for t, _ in av]
    ok("nct" in tipos and "nct-rancio" not in tipos, "caché fresco + cambio → avisa del cambio")
    ok(est["nct_estados"].get(NCT) == "SUSPENDED", "caché fresco → sella el estado nuevo")

    # Caché rancio → avisa de que NO puede mirarlo, y NO compara (el «sin cambios» mentiría).
    av, est = _pasada({NCT: "RECRUITING"}, 24 * 29, {NCT: "SUSPENDED"})
    tipos = [t for t, _ in av]
    ok("nct-rancio" in tipos, "caché rancio → avisa 'no puedo vigilarlo'")
    ok("nct" not in tipos, "caché rancio → NO compara estados (no finge 'sin cambios')")
    # Y no sella: sellar un fósil enterraría el cambio real bajo nuestra propia línea base.
    ok(est["nct_estados"].get(NCT) == "SUSPENDED", "caché rancio → NO sella el fósil como visto")

    # Dedup: corre cada 150 s, así que el aviso es UNO al día, no 576.
    av, _ = _pasada({NCT: "RECRUITING"}, 24 * 29, {}, dia_avisado=cn._hoy_iso())
    ok("nct-rancio" not in [t for t, _ in av], "rancio ya avisado hoy → no repite")

    # Sin _ts_consulta → se trata como rancio (fail-closed): no sabemos si el dato sirve.
    av, _ = _pasada({NCT: "RECRUITING"}, None, {NCT: "SUSPENDED"})
    ok("nct-rancio" in [t for t, _ in av], "caché sin _ts_consulta → rancio (fail-closed)")

    # `status` deja de decir la verdad tranquilizadora («existe») y dice si el dato SIRVE.
    _escribir_cache({NCT: "RECRUITING"}, 24 * 29)
    _, edad = cn._nct_estados_desde_cache(con_edad=True)
    ok(edad is not None and edad > cn.NCT_CACHE_STALE_H, "la edad del caché se lee de verdad")


if __name__ == "__main__":
    sys.exit(main())
