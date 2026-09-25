#!/usr/bin/env python3
"""test_cosecha_hilos.py — el minero de hilos abiertos entre sesiones.

Hermético: FUERZA BTP_PROJECTS_DIR (transcripts falsos) y BTP_STATE_DIR (Tablero) a un
tmp. Verifica: detección de intención abierta, filtro NED, dedup contra el Tablero, y
que un cierre corto ("ya está, gracias") no se propone.
"""
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Aislamiento ANTES de importar (los módulos leen estas env al importarse).
_TMP = tempfile.mkdtemp(prefix="test_hilos_")
_PROJ = os.path.join(_TMP, "projects", "sesion-X")
os.makedirs(_PROJ, exist_ok=True)
_STATE = os.path.join(_TMP, "state")
os.makedirs(_STATE, exist_ok=True)
os.environ["BTP_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ["BTP_STATE_DIR"] = _STATE
os.environ["BTP_HOME"] = _TMP
os.environ.setdefault("BTP_REPO", ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _ev(texto):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return json.dumps({"type": "user", "entrypoint": "claude-desktop",
                       "timestamp": ts, "message": {"content": texto}}, ensure_ascii=False)


def _escribir_transcript(mensajes):
    with open(os.path.join(_PROJ, "t.jsonl"), "w", encoding="utf-8") as f:
        for m in mensajes:
            f.write(_ev(m) + "\n")


def _tablero(*titulos):
    with open(os.path.join(_STATE, "seguimiento.json"), "w", encoding="utf-8") as f:
        json.dump({"hilos": [{"id": "h%d" % i, "titulo": t} for i, t in enumerate(titulos)]},
                  f, ensure_ascii=False)


def main():
    import cosecha_hilos as ch

    # 1. detecta un hilo abierto NED (pastillero = cuidado/compra)
    _tablero()  # tablero vacío
    _escribir_transcript([
        "busco un pastillero grande para el viaje, no pequeño",
        "qué buen día hace hoy",                       # ruido: sin intención
    ])
    cands = ch.cosechar(dias=3)
    textos = " || ".join(c["texto"] for c in cands)
    check("detecta el pastillero como hilo abierto", any("pastillero" in c["texto"] for c in cands))
    check("el ruido sin intención NO se propone", "buen día" not in textos)
    if cands:
        c0 = next((c for c in cands if "pastillero" in c["texto"]), None)
        check("clasifica con un tema NED-relevante", c0 and c0["tema"] in ch._NED_RELEVANTES)
        check("marca categoría de intención", c0 and len(c0["categorias"]) >= 1)

    # 2. filtro NED: una intención 'general' sin relación con la misión se descarta
    _tablero()
    _escribir_transcript(["busco una serie nueva para ver el finde"])
    cands = ch.cosechar(dias=3, solo_ned=True)
    check("intención general (no-NED) filtrada por defecto", len(cands) == 0)
    cands_todo = ch.cosechar(dias=3, solo_ned=False)
    check("con --todo-tema sí aparece la general", len(cands_todo) >= 1)

    # 3. dedup contra el Tablero: si ya está rastreado, no se re-propone
    _tablero("Comprar pastillero grande para el viaje a Zúrich con medicación")
    _escribir_transcript(["busco un pastillero grande para el viaje, no pequeño"])
    cands = ch.cosechar(dias=3)
    check("dedup: hilo ya en el Tablero NO se re-propone", not any("pastillero" in c["texto"] for c in cands))

    # 4. cierre corto no se propone
    _tablero()
    _escribir_transcript(["ya está, gracias"])
    cands = ch.cosechar(dias=3)
    check("cierre corto ('ya está, gracias') no se propone", len(cands) == 0)

    # 5. ventana temporal: --todo no rompe
    cands_all = ch.cosechar(incluir_todo=True)
    check("incluir_todo no rompe", isinstance(cands_all, list))

    print("RESULTADO cosecha_hilos.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ COSECHA_HILOS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
