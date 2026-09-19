#!/usr/bin/env python3
"""test_tablero.py — El Tablero sobre la FUENTE ÚNICA de tareas (Vega/seguimiento).

Toda tarea es un hilo de seguimiento: cualquier canal la crea con `seguimiento.crear_tarea`,
el tablero (`observatorio.estado_tablero`) es una ventana a esa misma lista, y mover/cerrar
escribe de vuelta (`seguimiento.set_estado`). Una sola lista, igual en el kanban y en el parte.
+ EL MURO (nada del tablero envía/publica/contacta). Aísla todo en un tmp; no toca el repo real.

Ejecuta:  python3 tests/test_tablero.py
"""
import importlib
import json
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="test_tablero_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

import seguimiento  # noqa: E402
importlib.reload(seguimiento)          # toma BTP_STATE_DIR del entorno del test
import observatorio  # noqa: E402
importlib.reload(observatorio)

HOY = time.strftime("%Y-%m-%d")
_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print("  ok ·", name)
    else:
        _fail += 1
        print("  FALLA ·", name)


def _lanza(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:
        return False


def _seed(hilos, cumbre=None):
    """Reinicia la lista única (seguimiento.json) y, opcional, la cadena clínica (cumbre.json)."""
    json.dump({"hilos": hilos}, open(seguimiento.SEG, "w", encoding="utf-8"))
    if cumbre is not None:
        json.dump(cumbre, open(seguimiento.CUMBRE, "w", encoding="utf-8"))


def _cards(tb):
    return [c for col in tb["columnas"] for c in col["cards"]]


def main():
    # ───── la fuente única: crear_tarea = un hilo (lo crea cualquier canal) ─────
    _seed([])
    tid = seguimiento.crear_tarea("Contactar oncólogo", etiqueta="NED", prioridad="alta")
    items = seguimiento.recopilar().get("items", [])
    mio = [i for i in items if i.get("id") == tid]
    check("crear_tarea crea un hilo en la lista única", bool(mio))
    check("crear_tarea guarda etiqueta + prioridad", bool(mio) and mio[0].get("etiqueta") == "NED" and mio[0].get("prioridad") == "alta")
    check("crear_tarea exige título", _lanza(lambda: seguimiento.crear_tarea("  "), ValueError))

    # ───── el tablero es una ventana a esa misma lista ─────
    tb = observatorio.estado_tablero()
    cards = _cards(tb)
    mine = [c for c in cards if c["titulo"] == "Contactar oncólogo"]
    check("la tarea creada aparece en el tablero", bool(mine))
    check("la tarea es movible (write-back) y lleva su id", bool(mine) and mine[0]["mov"] and mine[0].get("id") == tid)
    check("conserva la etiqueta NED", bool(mine) and mine[0].get("etiqueta") == "NED")
    check("prioridad alta → indicativo 'importante'", bool(mine) and mine[0].get("nivel") == "importante")
    check("cada tarjeta lleva fase y día", all(("fase" in c and "dia" in c) for c in cards))
    check("5 columnas de estado", [c["key"] for c in tb["columnas"]] == ["por_hacer", "en_curso", "esperando_ok", "hecho", "pausa"])
    check("expone fases (con Gestión) y 4 días",
          isinstance(tb.get("fases"), list) and any(f["key"] == "gestion" for f in tb["fases"]) and len(tb.get("dias", [])) == 4)
    check("sin tarjetas técnicas (ningún tipo 'sistema')", not any(c.get("tipo") == "sistema" for c in cards))

    # ───── privacidad: un hilo privado de terceros NO sale al tablero (el 4K se ve en la sala) ─────
    _seed([{"id": "priv", "titulo": "Secreto de tercero", "estado": "esperando", "privado": True}])
    check("hilo privado NO aparece en el tablero",
          "Secreto de tercero" not in [c["titulo"] for c in _cards(observatorio.estado_tablero())])

    # ───── vence hoy → urgente + apartado "Para hoy"; orden por urgencia ─────
    _seed([])
    seguimiento.crear_tarea("Vence hoy", vence=HOY)
    seguimiento.crear_tarea("Vence lejos", vence="2099-01-01")
    seguimiento.crear_tarea("Sin fecha", prioridad="baja")
    tb2 = observatorio.estado_tablero()
    titulos_hoy = [x["titulo"] for x in tb2.get("hoy", [])]
    check("lo que vence hoy aparece en 'Para hoy'", "Vence hoy" in titulos_hoy)
    check("lo que vence lejos NO está en 'Para hoy'", "Vence lejos" not in titulos_hoy)
    vh = [c for c in _cards(tb2) if c["titulo"] == "Vence hoy"]
    check("vence hoy → urgente", bool(vh) and vh[0].get("nivel") == "urgente")
    check("cada columna ordenada por urgencia (rank no decreciente)",
          all([c.get("rank", 5) for c in col["cards"]] == sorted(c.get("rank", 5) for c in col["cards"]) for col in tb2["columnas"]))
    check("fecha inválida lanza ValueError", _lanza(lambda: seguimiento.crear_tarea("x", vence="32-13-2026"), ValueError))

    # ───── fases clínicas: cabecera de fila, NO tarjeta movible ─────
    _seed([], cumbre={"aqui_estamos": "biopsia", "transversal": [],
                      "salientes": [{"id": "biopsia", "titulo": "Re-biopsia", "estado": "en_curso", "siguiente_accion": "confirmar fecha"}]})
    tbc = observatorio.estado_tablero()
    check("la fase activa se marca", any(f.get("activa") for f in tbc["fases"]))
    clin = [c for c in _cards(tbc) if c.get("clinico")]
    check("la fase clínica aparece pero NO es movible", bool(clin) and all(not c["mov"] for c in clin))

    # ───── write-back board → Vega (set_estado) ─────
    _seed([{"id": "op", "titulo": "Hilo operativo", "estado": "esperando", "categoria": "prensa", "quien_espera": "tú"}])
    h = seguimiento.set_estado("op", "hecho")
    check("set_estado mueve el hilo (board→Vega)", h["estado"] == "hecho")
    check("set_estado PERSISTE en seguimiento.json",
          [x for x in seguimiento.load_seguimiento()["hilos"] if x["id"] == "op"][0]["estado"] == "hecho")
    check("set_estado NO toca fases clínicas (cumbre:)", _lanza(lambda: seguimiento.set_estado("cumbre:biopsia", "hecho"), ValueError))
    check("set_estado rechaza columna inválida", _lanza(lambda: seguimiento.set_estado("op", "xxx"), ValueError))
    check("set_estado en hilo inexistente lanza KeyError", _lanza(lambda: seguimiento.set_estado("fantasma", "hecho"), KeyError))

    # ───── EL MURO ─────
    VIAS = ["import requests", "import socket", "import smtplib", "from urllib", "urllib.request", "http.client"]
    src_sg = open(os.path.join(TOOLS, "seguimiento.py"), encoding="utf-8").read()
    for fn_name in ("crear_tarea", "set_estado"):
        blo = src_sg[src_sg.index("def " + fn_name):src_sg.index("\n\ndef ", src_sg.index("def " + fn_name))]
        check("%s no llama a salida/red" % fn_name, ("salida" not in blo) and not any(p in blo for p in VIAS))

    src_o = open(os.path.join(TOOLS, "observatorio.py"), encoding="utf-8").read()
    blo = src_o[src_o.index("def do_POST"):src_o.index("def log_message", src_o.index("def do_POST"))]
    check("do_POST solo toca la lista local (sin salida/envío)",
          ("seguimiento." in blo) and not any(p in blo for p in ("salida.", "import salida", "report_to_titular(", "alerta_critica(", "requests")))

    # comportamiento: crear/mover NO dispara nada de salida.py
    import salida
    trampa = []
    orig = {}
    for fn_name in ("report_to_titular", "report_file_to_titular", "alerta_critica", "react_to_titular"):
        if hasattr(salida, fn_name):
            orig[fn_name] = getattr(salida, fn_name)
            setattr(salida, fn_name, (lambda nm: (lambda *a, **k: trampa.append(nm)))(fn_name))
    try:
        x = seguimiento.crear_tarea("sin efectos hacia fuera")
        seguimiento.set_estado(x, "en_curso")
        seguimiento.set_estado(x, "hecho")
    finally:
        for fn_name, f in orig.items():
            setattr(salida, fn_name, f)
    check("crear/mover una tarea NO dispara salida (%s)" % (trampa or "ninguna"), trampa == [])

    print("\nRESULTADO tablero (fuente única): %d OK, %d fallos" % (_pass, _fail))
    print("✅ TABLERO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
