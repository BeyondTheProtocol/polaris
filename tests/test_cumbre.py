#!/usr/bin/env python3
"""test_cumbre.py — la brújula a NED: foco, trinquete (avanzar con evidencia), set, radar."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_cumbre_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cumbre  # noqa: E402

cumbre.BRUJULA_MD = os.path.join(_TMP, "BRUJULA-NED.md")

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # seed + foco: el cuello real de hoy = la biopsia.
    cumbre.ensure()
    d = cumbre.load()
    check("meta = NED", "NED" in d["meta"])
    check("aqui_estamos = biopsia", d["aqui_estamos"] == "biopsia")
    f = cumbre.foco()
    check("foco = re-biopsia Zúrich", f and f["id"] == "biopsia" and "Zúrich" in f["titulo"])
    check("el foco cita su fuente (no inventa)", f and "reference-clinical-profile" in f["fuente"])

    # set_saliente: actualiza; estado inválido revienta.
    check("set_saliente ok", cumbre.set_saliente("biopsia", estado="en_curso", siguiente_accion="confirmar fecha"))
    bad = False
    try:
        cumbre.set_saliente("biopsia", estado="INVENTADO")
    except ValueError:
        bad = True
    check("estado inválido → ValueError", bad)
    # hueco cerrado: set_saliente NO puede marcar 'resuelto' (eso exige avanzar+evidencia).
    no_resuelto = False
    try:
        cumbre.set_saliente("biopsia", estado="resuelto")
    except ValueError:
        no_resuelto = True
    check("set_saliente resuelto → ValueError (sin puentear el trinquete)", no_resuelto)

    # trinquete: avanzar EXIGE evidencia; sin ella revienta.
    sin_ev = False
    try:
        cumbre.avanzar("biopsia", "")
    except ValueError:
        sin_ev = True
    check("avanzar sin evidencia → ValueError (trinquete)", sin_ev)
    check("aqui_estamos sigue en biopsia", cumbre.load()["aqui_estamos"] == "biopsia")

    # avanzar con evidencia → marcador sube a 'dianas'.
    nf = cumbre.avanzar("biopsia", "biopsia realizada y cores enviados (evidencia verificada)")
    check("avanzar con evidencia → foco = dianas", nf and nf["id"] == "dianas")
    check("biopsia queda resuelto con evidencia", any(s["id"] == "biopsia" and s["estado"] == "resuelto" and s.get("evidencia") for s in cumbre.load()["salientes"]))

    # radar de rutas: registrar candidata; veto inválido revienta.
    check("add_ruta_candidata", cumbre.add_ruta_candidata("Nueva modalidad X a NED", fuente="radar-lit", veto="pendiente"))
    veto_bad = False
    try:
        cumbre.add_ruta_candidata("y", veto="MEJORÍSIMA")
    except ValueError:
        veto_bad = True
    check("veto inválido → ValueError", veto_bad)
    check("la candidata quedó registrada", len(cumbre.load()["rutas_candidatas"]) == 1)

    # render: BRUJULA-NED.md existe y refleja el estado; sin .tmp colgando.
    cumbre.render_brujula()
    check("BRUJULA-NED.md renderizado", os.path.exists(cumbre.BRUJULA_MD))
    md = open(cumbre.BRUJULA_MD, encoding="utf-8").read()
    check("el md dice NO consejo clínico", "NO consejo clínico" in md)
    check("el md marca AQUÍ ESTAMOS", "AQUÍ ESTAMOS" in md)
    check("sin .tmp colgando", not os.path.exists(cumbre.CUMBRE + ".tmp"))

    _regresion_retroceso()

    print("RESULTADO cumbre.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ BRÚJULA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


def _sembrar(salientes, aqui):
    """Reescribe la cadena con una forma concreta (sin pasar por el trinquete: es fixture)."""
    d = cumbre.load()
    d["salientes"] = [dict(s) for s in salientes]
    d["aqui_estamos"] = aqui
    cumbre._write_atomic(cumbre.CUMBRE, d)


def _ids():
    return [s["id"] for s in cumbre.load()["salientes"]]


def _regresion_retroceso():
    """Regresión del bug del 25-jul: con la cadena viva (biopsia=hecho, screening=hecho) el
    marcador RETROCEDÍA al primer eslabón ya cerrado en cuanto se avanzaba el actual."""
    # La forma REAL del cumbre.json vivo el 25-jul-2026.
    real = [
        {"id": "biopsia", "titulo": "Re-biopsia", "estado": "hecho"},
        {"id": "screening-{{CENTRO}}", "titulo": "Screening {{CENTRO}}", "estado": "hecho"},
        {"id": "esperando-resultados", "titulo": "Esperando resultados", "estado": "en_curso"},
        {"id": "dianas", "titulo": "Dianas", "estado": "pendiente"},
        {"id": "ensayo", "titulo": "Ensayo", "estado": "pendiente"},
    ]
    _sembrar(real, "esperando-resultados")
    orden = _ids()
    viejo = cumbre.load()["aqui_estamos"]
    cumbre.avanzar("esperando-resultados", "anatomía patológica recibida (verificada)")
    nuevo = cumbre.load()["aqui_estamos"]
    check("avanzar sobre cadena con 'hecho' → foco = dianas", nuevo == "dianas")
    # Monotonía: caza CUALQUIER retroceso futuro, no solo este caso.
    check("el marcador nunca retrocede (monotonía)", orden.index(nuevo) > orden.index(viejo))

    # El trinquete cubre TODOS los estados que cierran, no solo 'resuelto'.
    for est in ("hecho", "aparcado", "fallido"):
        _sembrar(real, "esperando-resultados")
        msg = ""
        try:
            cumbre.set_saliente("dianas", estado=est)
        except ValueError as e:
            msg = str(e)
        # Debe fallar por el TRINQUETE, no por enum inválido: si no, pasa por el motivo equivocado.
        check("set_saliente %s → ValueError del trinquete" % est, "trinquete" in msg)

    # 'hecho' es un estado legítimo del enum (el JSON vivo lo usa): avanzar lo acepta.
    _sembrar(real, "esperando-resultados")
    cumbre.avanzar("esperando-resultados", "biopsia hecha", estado="hecho")
    check("avanzar --estado hecho lo marca hecho",
          any(s["id"] == "esperando-resultados" and s["estado"] == "hecho" for s in cumbre.load()["salientes"]))
    est_malo = False
    try:
        cumbre.avanzar("dianas", "ev", estado="riesgo")
    except ValueError:
        est_malo = True
    check("avanzar con estado no-cerrante → ValueError", est_malo)

    # Un eslabón fallido no clava la cadena (pero se marca ⚠️ para que no pase en silencio).
    con_fallido = [dict(s) for s in real]
    con_fallido[3]["estado"] = "fallido"
    _sembrar(con_fallido, "esperando-resultados")
    cumbre.avanzar("esperando-resultados", "resultados recibidos")
    check("un 'fallido' en medio no clava el marcador", cumbre.load()["aqui_estamos"] == "ensayo")
    check("el fallido sale marcado ⚠️ en el resumen", "⚠️" in cumbre.estado())

    # foco() con el marcador sobre un nodo YA CERRADO degrada al primer accionable.
    _sembrar(real, "biopsia")  # marcador sobre un 'hecho', como pasó por edición a mano
    f = cumbre.foco()
    check("foco() sobre nodo cerrado degrada al accionable", f and f["id"] == "esperando-resultados")

    # La cadena ENVEJECE: el nodo que pasa a ser el foco sella desde cuándo se espera.
    # Sin esto, «esperando resultados» pesa y se lee igual el día 1 que el día 17.
    _sembrar(real, "esperando-resultados")
    cumbre.avanzar("esperando-resultados", "resultados recibidos")
    nuevo = [s for s in cumbre.load()["salientes"] if s["id"] == "dianas"][0]
    check("el nuevo foco sella esperando_desde", bool(nuevo.get("esperando_desde")))
    check("no pisa una fecha ya puesta",
          cumbre.esperando_desde("dianas", "2026-01-05")
          and [s for s in cumbre.load()["salientes"] if s["id"] == "dianas"][0]["esperando_desde"] == "2026-01-05")
    cumbre.avanzar("dianas", "dianas identificadas")
    check("avanzar respeta la fecha ya sellada",
          [s for s in cumbre.load()["salientes"] if s["id"] == "dianas"][0]["esperando_desde"] == "2026-01-05")
    mala = False
    try:
        cumbre.esperando_desde("ensayo", "5 de enero")
    except ValueError:
        mala = True
    check("fecha no-ISO → ValueError (nada de prosa)", mala)

    # avanzar sobre un id que no existe no debe recalcular el marcador en silencio.
    inexistente = False
    try:
        cumbre.avanzar("no-existe", "ev")
    except ValueError:
        inexistente = True
    check("avanzar sobre id inexistente → ValueError", inexistente)


if __name__ == "__main__":
    sys.exit(main())
