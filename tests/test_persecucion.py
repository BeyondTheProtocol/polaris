#!/usr/bin/env python3
"""test_persecucion.py — F4.1 drive-to-close. Vega delega el caso `entrega` (interno) al comité de
forma determinista; `aviso`/`desbloquea`/`codigo_rojo` ({{TITULAR}}/terceros/fuera) NO se auto-ejecutan;
idempotente con cooldown; intención saneada + recuerda el muro; cero egress (queue mock, sin red)."""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="persec_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))          # para importar cola (la cola del lazo) y hermanos
import persecucion as pc    # noqa: E402
import seguimiento          # noqa: E402
import cola as q           # noqa: E402  (= tools/cola.py por el path)

_enq = []
q.enqueue = lambda intencion, **k: (_enq.append((intencion, k)) or ("job%d" % len(_enq)))

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


ITEMS = [
    {"id": "h-entrega", "titulo": "Presupuesto biopsia\nNMI-TT", "siguiente_accion": "pedir cifra"},
    {"id": "h-aviso", "titulo": "Firmar MTA"},
    {"id": "h-desbloq", "titulo": "Esperando a {{CONTACTO}}"},
    {"id": "h-rojo", "titulo": "Cuello clínico"},
]
seguimiento.recopilar = lambda: {"items": ITEMS}


def set_salidas(salidas):
    seguimiento.perseguir = lambda ejecutar=False: {"salidas": salidas}


def main():
    # 1. SOLO `entrega` se delega; aviso/desbloquea/codigo_rojo NO se auto-ejecutan
    set_salidas([
        {"salida": "entrega", "hilo_id": "h-entrega", "destino": "herramientas-medicas"},
        {"salida": "aviso", "hilo_id": "h-aviso", "destino": "titular"},
        {"salida": "desbloquea", "hilo_id": "h-desbloq", "destino": "consejero-acceso"},
        {"salida": "codigo_rojo", "hilo_id": "h-rojo", "destino": "agente"},
    ])
    out = pc.evaluar(dry=False)
    encolados = [o for o in out if o[3] == "encolado"]
    ok(len(encolados) == 1 and encolados[0][0] == "h-entrega", "solo `entrega` se delega (%d)" % len(encolados))
    ok(len(_enq) == 1, "se encoló exactamente 1 job (no aviso/desbloquea/codigo_rojo)")
    ok(_enq and _enq[0][1].get("agente") == "herramientas-medicas", "encola AL COMITÉ dueño")
    ok(_enq and _enq[0][1].get("procedencia") == "vega-persecucion", "procedencia marcada")
    ok(_enq and _enq[0][1].get("criticidad") == "rutina", "criticidad rutina (no bloquea lo NED-crítico)")
    ok(_enq and "\n" not in _enq[0][0] and "el muro" in _enq[0][0],
       "intención SANEADA (sin saltos) + recuerda el muro (borrador, nada fuera)")

    # 2. idempotente: segunda pasada (cooldown) → NO re-encola
    out2 = pc.evaluar(dry=False)
    ok(len(_enq) == 1, "idempotente: no re-encola el mismo hilo (cooldown)")
    ok(any(o[3] == "cooldown" for o in out2), "el hilo queda en cooldown")

    # 3. check (dry) NO encola
    ITEMS.append({"id": "h-otro", "titulo": "Otra cosa", "siguiente_accion": "x"})
    set_salidas([{"salida": "entrega", "hilo_id": "h-otro", "destino": "tecnico"}])
    before = len(_enq)
    pc.evaluar(dry=True)
    ok(len(_enq) == before, "check (dry) NO encola")

    # 4. estado de persecución por hilo (ids/comité, no claims)
    st = json.load(open(os.path.join(_TMP, "persecucion", "h-entrega.json")))
    ok(st.get("job_id") == "job1" and st.get("comite") == "herramientas-medicas", "estado de persecución por hilo")

    print("RESULTADO persecución (F4.1 drive-to-close): %d OK, %d fallos" % (_pass, _fail))
    print("✅ PERSECUCIÓN EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
