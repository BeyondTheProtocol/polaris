#!/usr/bin/env python3
"""test_radar_archivo_cerrados.py — pasar de 200 cierres no borra veredictos ni re-encola leads.

21-sep-26 (deuda `radar-cola-recorta-cerrados-a-200`): `guarda_cola` recortaba los cerrados a 200
en silencio. Se perdía el rastro de por qué se descartó cada ensayo y `encola` dejaba de verlos
como vistos. El test fija que lo que sobra va al archivo jsonl, que nada se pierde, y que un lead
archivado no vuelve a la cola. Todo en un directorio temporal.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_ned_diario as r  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    d = tempfile.mkdtemp()
    orig = (r.COLA, r.DIR_ESTADO, r.ARCHIVO_CERRADOS)
    r.COLA, r.DIR_ESTADO = os.path.join(d, "cola.json"), d
    r.ARCHIVO_CERRADOS = os.path.join(d, "cerrados_archivo.jsonl")
    try:
        cer = [{"uid": f"u{i}", "veredicto": f"v{i}"} for i in range(r.CERRADOS_TOPE + 5)]
        r.guarda_cola({"pendientes": [], "cerrados": cer})
        c = r.lee_cola()
        arch = [json.loads(l) for l in open(r.ARCHIVO_CERRADOS, encoding="utf-8")]
        check("en la cola quedan los últimos 200", len(c["cerrados"]) == r.CERRADOS_TOPE
              and c["cerrados"][0]["uid"] == "u5")
        check("los 5 más viejos van al archivo con su veredicto",
              [x["uid"] for x in arch] == [f"u{i}" for i in range(5)] and arch[0]["veredicto"] == "v0")
        check("no se pierde ninguno", len(arch) + len(c["cerrados"]) == len(cer))
        check("uids_archivados los ve", r.uids_archivados() == {f"u{i}" for i in range(5)})

        res = {"fecha": "2026-09-21", "temas": [{"prio": "alta", "clave": "adc-hr", "hits": [
            {"uid": "u0", "tipo": "ensayo", "titulo": "t", "ref": "NCT0", "url": "x", "fecha": "f"},
            {"uid": "nuevo", "tipo": "ensayo", "titulo": "t2", "ref": "NCT1", "url": "y", "fecha": "f"}]}]}
        r.orden_jev = lambda pend, **k: (0, "desactivado en el test")
        nuevos, _, _ = r.encola(res)
        uids = [p["uid"] for p in r.lee_cola()["pendientes"]]
        check("un lead archivado no vuelve a la cola", "u0" not in uids and nuevos == 1)

        r.guarda_cola(r.lee_cola())
        arch2 = [l for l in open(r.ARCHIVO_CERRADOS, encoding="utf-8")]
        check("guardar sin pasar del tope no duplica el archivo", len(arch2) == 5)
    finally:
        r.COLA, r.DIR_ESTADO, r.ARCHIVO_CERRADOS = orig
    print("test_radar_archivo_cerrados: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
