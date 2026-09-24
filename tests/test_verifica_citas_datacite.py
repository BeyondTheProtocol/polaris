#!/usr/bin/env python3
"""test_verifica_citas_datacite.py — un 404 de Crossref no es una cita fabricada.

POR QUÉ EXISTE (24-sep-2026, deuda `verifica_citas_no_mira_datacite`).
El gate de salida marcó como «citas fabricadas» tres DOI de Zenodo que existen
(10.5281/zenodo.20005708, .20020977, .20005707; api.datacite.org devolvía 200). Zenodo
registra en DataCite, no en Crossref, y `check_doi` solo miraba Crossref. Uno de ellos es
el del preprint que va en la firma de correo de {{TITULAR}}: el freno habría bloqueado cualquier
respuesta que lo citara. Además el extractor pegaba el backtick del markdown al DOI.

HERMÉTICO: `_curl` se sustituye por respuestas fijas por URL. Sin red.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import verifica_citas as v  # noqa: E402

_pass = _fail = 0

CROSSREF = "https://api.crossref.org/works/"
DATACITE = "https://api.datacite.org/dois/"
HANDLE = "https://doi.org/api/handles/"


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _fake(tabla):
    """tabla: {prefijo_url: (codigo, cuerpo)}. Lo no listado = red caída."""
    llamadas = []

    def curl(url, accept="application/json", timeout=None):
        llamadas.append(url)
        for pref, resp in tabla.items():
            if url.startswith(pref):
                return resp
        return None, "curl: (28) timeout"
    return curl, llamadas


def _con(tabla, doi):
    orig = v._curl
    curl, llamadas = _fake(tabla)
    v._curl = curl
    try:
        return v.check_doi(doi), llamadas
    finally:
        v._curl = orig


DC_OK = (200, json.dumps({"data": {"attributes": {"titles": [{"title": "Preprint en Zenodo"}]}}}))
NO = (404, '{"status":"404"}')
H_NO = (404, json.dumps({"responseCode": 100}))
H_SI = (200, json.dumps({"responseCode": 1}))


def main():
    zen = "10.5281/zenodo.20005708"

    # ── la cascada Crossref → DataCite → doi.org ──────────────────────────────────
    (est, det, fue), ll = _con({CROSSREF: NO, DATACITE: DC_OK}, zen)
    check("DOI de Zenodo (404 Crossref, 200 DataCite) → existe", est == v.EXISTE)
    check("y la fuente es DataCite con su título", fue == "DataCite" and "Zenodo" in det)
    check("no pregunta a doi.org si DataCite ya lo tiene", not any(u.startswith(HANDLE) for u in ll))

    (est, det, fue), _ = _con({CROSSREF: NO, DATACITE: NO, HANDLE: H_NO}, "10.5281/zenodo.99999999999")
    check("DOI inventado (404 en las tres) → sigue FABRICADA", est == v.FABRICADA)

    (est, _, fue), _ = _con({CROSSREF: NO, DATACITE: NO, HANDLE: H_SI}, "10.1234/medra.x1")
    check("DOI de otra agencia (solo doi.org lo conoce) → existe", est == v.EXISTE and fue == "doi.org")

    (est, _, _), _ = _con({CROSSREF: NO}, zen)
    check("Crossref 404 + DataCite mudo → no_resoluble, NO se acusa", est == v.NO_RES)

    (est, _, _), _ = _con({CROSSREF: NO, DATACITE: NO}, zen)
    check("Crossref 404 + DataCite 404 + doi.org mudo → no_resoluble", est == v.NO_RES)

    (est, _, _), ll = _con({CROSSREF: (200, json.dumps({"message": {"title": ["X"]}}))}, zen)
    check("Crossref 200 → existe sin más llamadas", est == v.EXISTE and len(ll) == 1)

    (est, _, _), ll = _con({CROSSREF: (503, "")}, zen)
    check("Crossref 503 → no_resoluble (como antes)", est == v.NO_RES and len(ll) == 1)

    # ── limpieza del DOI extraído ─────────────────────────────────────────────────
    casos = [
        ("el preprint (`10.5281/zenodo.20005708`)", zen),
        ("10.5281/zenodo.20005708`", zen),
        ("**10.5281/zenodo.20005708**", zen),
        ("_10.5281/zenodo.20005708_", zen),
        ("doi:10.5281/zenodo.20005708.", zen),
        ("(ver 10.5281/zenodo.20005708),", zen),
        ("[preprint](https://doi.org/10.5281/zenodo.20005708)", zen),
        ("Lancet Oncol. doi:10.1016/S1470-2045(25)00001-9.", "10.1016/S1470-2045(25)00001-9"),
    ]
    for cadena, esperado in casos:
        t, i = v.clasifica(cadena)
        check("limpia «%s» → %s (salió %s)" % (cadena, esperado, i), t == "doi" and i == esperado)

    # ── integración: lo que le pasa el gate de salida, crudo ──────────────────────
    orig = v._curl
    v._curl, _ = _fake({CROSSREF: NO, DATACITE: DC_OK})
    try:
        r = v.verifica([zen + "`"])[0]
    finally:
        v._curl = orig
    check("verifica() con backtick pegado → id limpio", r["id"] == zen)
    check("verifica() con backtick pegado → existe", r["estado"] == v.EXISTE)

    print("RESULTADO verifica_citas DataCite: %d OK, %d fallos" % (_pass, _fail))
    print("✅ DATACITE Y LIMPIEZA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
