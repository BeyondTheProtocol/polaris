#!/usr/bin/env python3
"""test_cosecha_whatsapp.py — la bandeja de WhatsApp muestra 300, pero no pierde ninguno.

25-sep-26: `estacionar` recortaba a los 300 más recientes y avanzaba el watermark, así que lo
recortado no volvía nunca (150-824 candidatos al día; deuda `wa-bandeja-tope-300-tira-sin-juzgar`).
Fija: lo que no cabe va a `wa_desbordados.jsonl`; prioridad yo > directo > grupo; dedup entre
corridas; si la escritura falla, el watermark no avanza; HALT y gate no escriben. Offline.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cosecha_whatsapp as cw  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def cand(i, chat="Grupo", who="Ana", dia=24):
    return {"titulo": "t%d" % i, "slug": "s%d" % i, "veredicto": "dudosa", "chat": chat,
            "who": who, "fuente": "whatsapp:%s @ %02d/09/26 %02d:%02d" % (chat, dia, i // 60 % 24, i % 60)}


def desbordados():
    try:
        return [json.loads(ln) for ln in open(cw.DESBORDADOS, encoding="utf-8")]
    except OSError:
        return []


def main():
    orig = (cw.BANDEJA, cw.DESBORDADOS, cw.WATERMARK, cw.MAX_BANDEJA)
    with tempfile.TemporaryDirectory() as d:
        cw.BANDEJA = os.path.join(d, "tareas", "wa_candidatos.json")
        cw.DESBORDADOS = os.path.join(d, "tareas", "wa_desbordados.jsonl")
        cw.WATERMARK = os.path.join(d, "wa_cosecha.json")
        cw.MAX_BANDEJA = 300
        try:
            # Grupo: dos voces además de ella. Directo: una sola. Y lo que escribe ella.
            lote = ([cand(i, "Grupo", "Ana" if i % 2 else "Luis") for i in range(320)]
                    + [cand(1000 + i, "Directo", "Bea") for i in range(20)]
                    + [cand(2000 + i, "Grupo", "yo") for i in range(10)])
            n = cw.estacionar(lote, {"Grupo": "x"})
            banj = json.load(open(cw.BANDEJA))
            dentro = {c["slug"] for c in banj["candidatos"]}
            fuera = {c["slug"] for c in desbordados()}
            check("350 estacionados", n == 350)
            check("300 en la bandeja", len(dentro) == 300)
            check("50 desbordados, guardados enteros", len(fuera) == 50 and banj["desbordados_hoy"] == 50)
            check("0 perdidos", dentro | fuera == {c["slug"] for c in lote} and not dentro & fuera)
            check("lo suyo siempre dentro", all("s%d" % (2000 + i) in dentro for i in range(10)))
            check("los directos dentro antes que el grupo", all("s%d" % (1000 + i) in dentro for i in range(20)))
            check("los desbordados son del grupo", all(s.startswith("s") and int(s[1:]) < 1000 for s in fuera))
            check("el watermark avanza tras escribir", json.load(open(cw.WATERMARK)) == {"Grupo": "x"})

            # Segunda corrida: lo ya desbordado o ya en la bandeja no se duplica.
            n2 = cw.estacionar([cand(5), cand(1001, "Directo", "Bea"), cand(3000, "Otro", "Eva")], {"Grupo": "y"})
            check("dedup contra bandeja y desbordados", n2 == 1)
            check("nada duplicado en desbordados", len(desbordados()) == len({c["slug"] for c in desbordados()}))
            check("el total acumula", json.load(open(cw.BANDEJA))["desbordados_total"] >= 50)

            # Si la escritura del desbordamiento falla, el watermark NO avanza.
            orig_open = open
            wm_antes = open(cw.WATERMARK).read()

            def roto(ruta, *a, **k):
                if ruta == cw.DESBORDADOS and a and a[0] == "a":
                    raise OSError("disco lleno")
                return orig_open(ruta, *a, **k)
            import builtins
            builtins.open = roto
            try:
                try:
                    cw.estacionar([cand(4000 + i) for i in range(400)], {"Grupo": "z"})
                    rompio = False
                except OSError:
                    rompio = True
            finally:
                builtins.open = orig_open
            check("fallo al desbordar → excepción visible", rompio)
            check("fallo al desbordar → watermark intacto", open(cw.WATERMARK).read() == wm_antes)

            # HALT y gate: no escriben.
            antes = open(cw.BANDEJA).read()
            orig_h, orig_g, orig_c = cw._halted, cw._gate_abierto, cw.cosechar
            cw.cosechar = lambda v=2: ([cand(9000)], {"Grupo": "h"})
            cw._halted = lambda: True
            cw.main(["--stage"])
            check("HALT no escribe", open(cw.BANDEJA).read() == antes)
            cw._halted, cw._gate_abierto = (lambda: False), (lambda: False)
            cw.main(["--stage"])
            check("gate cerrado no escribe", open(cw.BANDEJA).read() == antes)
            cw._halted, cw._gate_abierto, cw.cosechar = orig_h, orig_g, orig_c
        finally:
            cw.BANDEJA, cw.DESBORDADOS, cw.WATERMARK, cw.MAX_BANDEJA = orig

    print("test_cosecha_whatsapp: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
