#!/usr/bin/env python3
"""test_deid.py — GATE DURO adversarial del NÚCLEO F3b (de-id + cableado de contexto).

SOLO DATOS SINTÉTICOS. No abre ni procesa NINGÚN clínico real (ni _PRIVADO_CLINICO ni la fuente de
verdad): construye un índice kb sintético en un tmp aislado con identificadores PLANTADOS, y verifica:

  1. De-id directa: tras de-identificar, NINGÚN identificador plantado sobrevive — se reconfirma con
     las MISMAS regex/deny-lists del borde (0 hits) Y con el juez del muro `borde.clasificar` (limpio).
  2. Robustez de evasión: homoglifos / acentos falsos / zero-width / espaciado tampoco escapan
     (el de-id normaliza igual que el borde antes de enmascarar).
  3. Cableado de contexto: el contexto que se construye para el cerebro GRATIS/LOCAL es el
     DE-IDENTIFICADO, no el crudo — el crudo NUNCA aparece en lo que llega al cerebro.
  4. Fail-closed: si un pasaje no queda limpio, se DESCARTA (no se cuela a medio limpiar).

Estilo igual que test_borde / test_muro_fase0: cada caso suma OK/fallo; exit = nº de fallos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("identidad", "nombres")
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

# Estado aislado + sin HALT antes de importar borde (lee STATE/HALT al importar).
_TMP = tempfile.mkdtemp(prefix="deid_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "no_a") + ":" + os.path.join(_TMP, "no_b")

import borde      # noqa: E402
import deid       # noqa: E402
import contexto_caso as cc  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── Identificadores SINTÉTICOS plantados (NADA real) ─────────────────────────────────────
# Un nombre conocido por la deny-list ({{CONTACTO}}) + el nombre de {{TITULAR}} + un tercero, PII, y huella
# clínica/genómica específica + un término vetado del muro + una fecha.
PLANTADOS = {
    "nombre {{TITULAR}}": "{{TITULAR}} {{APELLIDO}}",
    "tercero deny-list": "{{CONTACTO}}",
    "email": "contacto.sintetico@ejemplo.org",
    "telefono": "+34 600 11 22 33",
    "dni": "12345678Z",
    "variante HGVS": "c.524G>A",
    "variante corta": "p.R175H",
    "HLA": "HLA-A*02:01",
    "genotipo VCF": "0/1",
    "cifra Ki-67": "Ki-67 70%",
    "marcador": "TP53",
    "rsID": "rs28934578",
    "citobanda": "del 17p",
    "termino vetado": "vacuna",
    "fecha ISO": "2026-07-08",
}

CASO_SINTETICO = (
    "Informe SINTÉTICO de prueba. La paciente {{TITULAR}} {{APELLIDO}} (DNI 12345678Z, "
    "contacto.sintetico@ejemplo.org, tel +34 600 11 22 33) habló con {{CONTACTO}}. "
    "El panel muestra variante c.524G>A (p.R175H) en TP53, rs28934578, del 17p, "
    "genotipo 0/1, HLA-A*02:01 y Ki-67 70%. Plan de vacuna personalizada el 2026-07-08."
)

# detectores del borde con los que RE-comprobamos que nada sobrevive (cazan por CLASE, no por
# el literal plantado — más estricto que un find del string).
_DETECTORES = (borde._RE_EMAIL, borde._RE_DNI, borde._RE_PHONE, borde._RE_HLA, borde._RE_RS,
               borde._RE_CHR, borde._RE_CHR_W, borde._RE_CITO, borde._RE_EXON, borde._RE_VAR,
               borde._RE_VAR_CORTO, borde._RE_GT, borde._RE_CLIN, borde._RE_CLIN_PCT,
               borde._NOMBRE_TITULAR)


def _ningun_literal_sobrevive(salida):
    """Ningún VALOR plantado aparece textualmente en la salida (normalizando como el borde, para
    que '{{TITULAR}}'→'titular' cuente)."""
    _, low = borde._normalizar(salida)
    fugados = []
    for etq, val in PLANTADOS.items():
        _, vlow = borde._normalizar(val)
        # comparamos el núcleo del valor (sin el '*02:01' residual de HLA, que el borde deja limpio)
        if vlow in low:
            fugados.append(etq)
    return fugados


def _ninguna_clase_sobrevive(salida):
    """Ningún DETECTOR del borde encuentra nada en la salida (caza por clase)."""
    norm, _ = borde._normalizar(salida)
    return [rx.pattern[:24] for rx in _DETECTORES if rx.search(norm)]


def _construir_indice_sintetico(dirpath):
    """Crea un .kb_index.json mínimo (formato de kb.build) con UN pasaje sintético marcado
    'private'. No toca la fuente de verdad real."""
    import math
    from collections import defaultdict
    import kb
    text = CASO_SINTETICO
    toks = kb.toks(text)
    tf = defaultdict(int)
    for w in toks:
        tf[w] += 1
    df = {w: 1 for w in tf}
    postings = {w: [[0, f]] for w, f in tf.items()}
    idx = {"chunks": [{"path": "SINTETICO/caso.md", "title": "Caso de prueba",
                       "text": text, "sensitivity": "private"}],
           "lengths": [len(toks)], "avgdl": float(len(toks)),
           "N": 1, "df": df, "postings": postings}
    path = os.path.join(dirpath, ".kb_index.json")
    with open(path, "w") as f:
        json.dump(idx, f)
    return path


def main():
    print("── F3b: de-id + cableado de contexto (SINTÉTICO) ──")

    # 1. DE-ID DIRECTA: el caso entero → de-id → nada sobrevive
    deid_txt, n, ok_juez, motivo = deid.de_identificar_verificado(CASO_SINTETICO)
    ok(deid_txt is not None, "1.0 de-id devuelve salida (no fail-closed sobre caso normal)")
    ok(n >= len(PLANTADOS) - 2, "1.1 hubo redacciones suficientes (n=%d)" % n)
    fug_lit = _ningun_literal_sobrevive(deid_txt or "")
    ok(not fug_lit, "1.2 NINGÚN literal plantado sobrevive (fugados: %s)" % fug_lit)
    fug_cls = _ninguna_clase_sobrevive(deid_txt or "")
    ok(not fug_cls, "1.3 NINGUNA clase del borde sobrevive (detectó: %s)" % fug_cls)
    ok(ok_juez, "1.4 el juez del muro declara LIMPIO (%s)" % motivo)
    ok(not deid._RE_FECHA.search(deid_txt or ""), "1.5 ninguna fecha sobrevive")

    # 2. EVASIÓN: homoglifos / zero-width / acento falso / espaciado tampoco escapan
    evasiones = [
        "vacüna",                         # acento falso → término vetado
        "v​a​c​u​na",  # zero-width entre letras
        "{{TITULAR}} {{APELLIDO}}",           # homoglifo (i sin punto) en el nombre
    ]
    for ev in evasiones:
        d, _, okj, mot = deid.de_identificar_verificado("texto con %s aquí" % ev)
        # tras normalizar+enmascarar, el juez del muro debe declararlo limpio
        ok(okj and d is not None, "2.x evasión neutralizada (%r → %s)" % (ev, mot))

    # 3. CABLEADO: el contexto para el cerebro GRATIS/LOCAL es el DE-IDENTIFICADO, no el crudo
    idx_path = _construir_indice_sintetico(_TMP)
    ctx = cc.construir_contexto("panel molecular del caso", k=6, scope="private",
                                index_path=idx_path)
    ok(ctx["limpio"], "3.1 el contexto ensamblado es LIMPIO (%s)" % ctx["motivo"])
    ok(bool(ctx["contexto"]), "3.2 se recuperó algún contexto del índice sintético")
    fug_ctx = _ningun_literal_sobrevive(ctx["contexto"])
    ok(not fug_ctx, "3.3 ningún literal plantado en el contexto que iría al cerebro (%s)" % fug_ctx)
    ok(not _ninguna_clase_sobrevive(ctx["contexto"]),
       "3.4 ninguna clase del borde en el contexto")
    ok("{{TITULAR}}" not in ctx["contexto"] and "vacuna" not in ctx["contexto"].lower(),
       "3.5 ni el nombre ni el término vetado aparecen en crudo en el contexto")

    # 3.b el crudo NO se envía: el prompt al cerebro gratis se arma con el contexto de-id.
    #    Capturamos lo que llegaría a ia.ask vía BTP_IA_FAKE (gancho sin tocar el borde).
    import ia
    capturado = {}
    _orig = ia.ask

    def _spy(prompt, **kw):
        capturado["prompt"] = prompt
        capturado["solo_gratis"] = kw.get("solo_gratis")
        capturado["clinico"] = kw.get("clinico")
        return {"text": "respuesta-falsa", "brain": "local-fake", "motivo": "ok"}
    ia.ask = _spy
    try:
        r = cc.preguntar_local("panel molecular del caso", index_path=idx_path)
    finally:
        ia.ask = _orig
    ok(r["enviado_limpio"], "3.6 preguntar_local marca enviado_limpio")
    ok(capturado.get("solo_gratis") is True, "3.7 va al carril GRATIS (solo_gratis=True)")
    ok(capturado.get("clinico") is False, "3.8 NO va por el carril clínico")
    prompt = capturado.get("prompt", "")
    ok(not _ningun_literal_sobrevive(prompt),
       "3.9 el prompt enviado al cerebro gratis NO contiene ningún identificador crudo")
    ok("[REDACTADO]" in prompt, "3.10 el prompt contiene marcadores de redacción (es el de-id)")

    # 4. FAIL-CLOSED: un pasaje imposible de limpiar se DESCARTA (no se cuela).
    #    Forzamos `deid.de_identificar` a devolver algo todavía sucio para este caso.
    _orig_deid = deid.de_identificar
    deid.de_identificar = lambda t: ("Queda {{TITULAR}} {{APELLIDO}} sin limpiar", 0)
    try:
        ctx2 = cc.construir_contexto("panel molecular del caso", k=6, scope="private",
                                     index_path=idx_path)
    finally:
        deid.de_identificar = _orig_deid
    ok(ctx2["contexto"] == "", "4.1 contexto vacío cuando nada queda limpio (fail-closed)")
    ok(len(ctx2["descartados"]) >= 1, "4.2 el pasaje sucio se registró como DESCARTADO")

    print("\n%s  (%d OK / %d fallos)" %
          ("✅ F3b EN VERDE" if _fail == 0 else "❌ F3b CON FALLOS", _pass, _fail))
    return _fail


if __name__ == "__main__":
    sys.exit(main())
