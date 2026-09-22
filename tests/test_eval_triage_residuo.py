#!/usr/bin/env python3
"""test_eval_triage_residuo.py — el set dorado no sale con fechas, URLs, @handles ni números largos.

21-sep-26: el set «de-identificado» llevaba una fecha con pinta de nacimiento junto a un correo
del hospital y @handles de terceros, y `borde.clasificar` lo daba por limpio (deuda
`eval-triage-deja-fecha-nacimiento-y-handles`). Este test fija que `_redactar` los tapa, que los
marcadores no se re-tapan como nombre, y que `endurecer` conserva ids y etiquetas. Offline: el
set se escribe en un temporal, nunca en el estado vivo.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import eval_triage as ev  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    red = lambda t: ev._redactar(t, [])[0]
    r = red("informe del hospital (03/04/1985) listo")
    check("fecha dd/mm/aaaa se tapa", "1985" not in r and "[FECHA]" in r)
    check("fecha ISO se tapa", "2026-07-13" not in red("del evento (2026-07-13): recoge"))
    check("fecha con guiones se tapa", "3-4-85" not in red("cita el 3-4-85"))
    r = red("señal en X (@alguien_real, 28-ago)")
    check("@handle se tapa", "alguien_real" not in r and "[HANDLE]" in r)
    r = red("mira https://ejemplo.xyz/ruta?q=1 ya")
    check("URL de dominio raro se tapa", "ejemplo" not in r and "[DOMINIO]" in r)
    check("número largo se tapa", "1234567" not in red("expediente 1234567"))
    check("marcadores no se re-tapan como nombre",
          "[[" not in red("el 03/04/1985 en https://a.xyz con @b y 123456"))
    check("texto normal intacto", red("llamar al fontanero el martes") == "llamar al fontanero el martes")

    fd, tmp = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    filas = [{"id": "t000", "texto": "Correo ({{FECHA_NAC}}) de @x", "etiqueta": "tarea", "redacciones": 2},
             {"id": "t001", "texto": "nada que tapar", "etiqueta": "no", "redacciones": 0}]
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in filas) + "\n")
    orig = ev.SALIDA
    ev.SALIDA = tmp
    try:
        ev.endurecer()
        out = [json.loads(l) for l in open(tmp, encoding="utf-8") if l.strip()]
        check("endurecer tapa la fecha", "1990" not in out[0]["texto"])
        check("endurecer conserva ids y etiquetas",
              [(x["id"], x["etiqueta"]) for x in out] == [("t000", "tarea"), ("t001", "no")])
        check("endurecer es idempotente", ev.endurecer() == 0)
    finally:
        ev.SALIDA = orig
        os.remove(tmp)
    print("test_eval_triage_residuo: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
