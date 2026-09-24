#!/usr/bin/env python3
"""test_deid_procedencia.py — lo que viene del caso es sensible aunque el detector no vea nada.

POR QUÉ EXISTE (24-sep-2026, auditoría externa de Marcos Gorgojo, hallazgo 3.3). `deid.py` redacta
con unos patrones y VERIFICA con esos mismos patrones: un texto ficticio con nombre, domicilio,
diagnóstico y médico dio 0 redacciones y `limpio=True` (reproducido aquí). Eso por sí solo es un
detector con huecos; lo grave es DÓNDE se decide el egress. `ia.ask` decide si un prompt es sensible
con `borde.clasificar`, el mismo detector: un pasaje del caso que la regex no reconoce salía como
«no sensible» y podía ir a un cerebro de nube sin contrato (N2 fuera).

El arreglo NO es otro detector (el experto de verificación midió uno estructural sobre 6.000
pasajes reales: marca el 77 % de lo privado y el 75 % de lo interno, no discrimina). Es la
PROCEDENCIA: lo que sale de la KB del caso con acceso pleno es N2 por venir de donde viene, se
detecte algo o no, y entonces solo lo atiende un cerebro de confianza.

Lo que se fija:
  1. el texto del auditor, dentro del contexto de caso, NO llega a un cerebro de nube;
  2. con solo nube disponible → respuesta honesta de «no puedo», nunca la nube;
  3. el carril local egress-cero sigue recibiendo contexto (vetar por reflejo es fallo);
  4. el veredicto ya no se llama «limpio»: se llama lo que es, «sin identificadores detectados»;
  5. una pregunta sin contexto de caso sigue pudiendo ir a la nube (no es un muro de falsos positivos).
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="deid_proc_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_CANARIOS"] = "CANARIO-DEID-771"
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
os.makedirs(os.path.join(_TMP, "cost"), exist_ok=True)
json.dump({"diario_usd": 30.0, "job_usd": 3.0}, open(os.path.join(_TMP, "cost", "limits.json"), "w"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import responder_con_datos as rc   # noqa: E402
import contexto_caso               # noqa: E402
import deid                        # noqa: E402
import ia                          # noqa: E402

rc.HOY_PATH = os.path.join(_TMP, "HOY.md")
ia._salud_disponibles = lambda: {}          # mide routing/muro, no la salud real de los cerebros

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# El texto del auditor (ficticio). Ruta NEUTRA y etiqueta «internal» a propósito: el peor caso es
# un informe del caso fuera de `_PRIVADO_` y sin marcadores genómicos, que es justo lo que se escapa.
AUDITOR = ("Beatriz Salgado Ferrer vive en Calle del Olivar 27, 3B, Valladolid. Le diagnosticaron "
           "un sarcoma sinovial de partes blandas en el muslo izquierdo y empezo tratamiento con "
           "pazopanib el invierno pasado. Su medico es el doctor Ruben Alcantara.")
PATH = "00_FUENTE-DE-VERDAD/01 · Tratamiento/nota-consulta.md"


def _indice():
    toks = contexto_caso._cargar_kb().toks
    tk = toks(AUDITOR)
    df, post = {}, {}
    for w in set(tk):
        df[w] = 1
        post[w] = [[0, tk.count(w)]]
    idx = {"N": 1, "avgdl": float(len(tk)), "lengths": [len(tk)], "df": df, "postings": post,
           "chunks": [{"path": PATH, "title": "Nota de consulta", "text": AUDITOR,
                       "sensitivity": "internal"}]}
    p = os.path.join(_TMP, "kb_index.json")
    json.dump(idx, open(p, "w"), ensure_ascii=False)
    return p


def _registro(cerebros):
    json.dump({"cerebros": cerebros}, open(os.environ["BTP_PERIPHERIES"], "w"), ensure_ascii=False)


NUBE = {"name": "nvidia-free", "kind": "carril_gratis", "destino": "nvidia", "trusted": False,
        "free": True, "capability": 9, "orden": 1, "enabled": True}
CLAUDE = {"name": "claude", "kind": "claude", "destino": "cleared:claude", "trusted": True,
          "free": False, "capability": 9, "orden": 20, "enabled": True}
IDX = _indice()
PREGUNTA = "¿qué me dijeron del tratamiento con pazopanib en la consulta?"


def main():
    # ── 0. El hallazgo tal cual: el detector no ve nada (eso NO se arregla con otro detector) ────
    d, n, sin_ids, motivo = deid.de_identificar_verificado(AUDITOR)
    ok(n == 0 and sin_ids, "repro del auditor: 0 redacciones y el detector no ve nada (%d, %r)" % (n, motivo))

    # ── 1. Con nube y Claude disponibles, el contexto de caso va SOLO a Claude ──────────────────
    ctx = rc.reunir_contexto(PREGUNTA, index_path=IDX)
    ok("pazopanib" in ctx["texto"], "el pasaje del caso entra en el contexto (si no, el test no prueba nada)")
    ok(ctx.get("procedencia") == "N2", "el contexto con pasaje del caso lleva procedencia N2 (%r)"
       % ctx.get("procedencia"))
    _registro([NUBE, CLAUDE])
    os.environ["BTP_IA_FAKE"] = "RESPUESTA"
    r = rc.responder(PREGUNTA, index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r.get("brain") != "nvidia-free", "N2 no detectado NO llega a la nube (respondió %r)" % r.get("brain"))
    ok(r.get("brain") == "claude", "lo atiende el cerebro de confianza (respondió %r)" % r.get("brain"))

    # ── 2. Solo nube → «no puedo» honesto, nunca la nube ────────────────────────────────────────
    _registro([NUBE])
    os.environ["BTP_IA_FAKE"] = "NO-DEBE-SALIR"
    r = rc.responder(PREGUNTA, index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r.get("text") is None and "NO-DEBE-SALIR" not in (r.get("mensaje") or ""),
       "solo nube disponible → no se sirve (respondió %r)" % r.get("brain"))

    # ── 3. El carril local egress-cero no se queda sin contexto ────────────────────────────────
    loc = contexto_caso.construir_contexto(PREGUNTA, pre_bloqueo=False, index_path=IDX)
    ok("pazopanib" in (loc.get("contexto") or ""), "el carril local sigue recibiendo el contexto del caso")

    # ── 4. El lenguaje: «sin identificadores detectados», no «limpio» ──────────────────────────
    ok(hasattr(deid, "sin_identificadores_detectados"), "deid expone sin_identificadores_detectados()")
    ok("sin_identificadores_detectados" in loc, "construir_contexto informa «sin_identificadores_detectados»")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        deid.main(["--check", AUDITOR])
    ok("LIMPIO" not in buf.getvalue().upper().replace("SIN IDENTIFICADORES", ""),
       "la CLI no certifica «limpio»: %r" % buf.getvalue().splitlines()[:1])
    _d, _n, _ok, mot = deid.de_identificar_verificado(AUDITOR, procedencia="N2")
    ok("procedencia" in mot.lower(), "con procedencia N2 el motivo lo dice (%r)" % mot)

    # ── 5. Sin contexto de caso, la nube sigue sirviendo (no es un muro de falsos positivos) ─────
    _registro([NUBE])
    os.environ["BTP_IA_FAKE"] = "RESPUESTA-NUBE"
    # Sin palabras en común con el pasaje: en un índice de UN documento, BM25 devuelve el pasaje
    # por cualquier «de» o «la» compartido, y entonces sí sería contexto del caso.
    r = rc.responder("Portugal capital Lisboa", index_path=IDX)
    del os.environ["BTP_IA_FAKE"]
    ok(r.get("brain") == "nvidia-free", "sin pasaje del caso, la nube responde (respondió %r)" % r.get("brain"))

    print("RESULTADO deid procedencia: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
