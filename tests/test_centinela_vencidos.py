#!/usr/bin/env python3
"""test_centinela_vencidos.py — el centinela deja de callarse cuando un plazo vence.

Hallazgo medio nº14 de la auditoría del 25-jul-26: `_plazos_seguimiento` cortaba con
`if dias < 0 or dias > 7: continue`, o sea que el segundo reloj —el que interrumpe al momento y no
depende del saldo— dejaba de mirar un plazo EXACTAMENTE cuando vencía. Sobre el seguimiento.json
vivo había 21 hilos vencidos abiertos, entre ellos «pasar el informe de radioterapia a Gemma Comas»
a −9 días. Un plazo vencido aprieta más, no menos.

Y los vencidos van en UN aviso agrupado, no uno por hilo: el día que esto se encendió había 15
esperando, y quince Telegram seguidos no son quince avisos (se leen como ruido y se deja de mirar el
canal). La lista va COMPLETA, sin top-N: un recorte silencioso se leería como «eso es todo».
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import centinela_ned as c  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _seg(tmp, hilos):
    """Escribe un seguimiento.json aislado y apunta el centinela a él."""
    p = os.path.join(tmp, "seguimiento.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"hilos": hilos}, f)
    c.SEGUIMIENTO_JSON = p
    return p


def _hilo(titulo, dias, estado="pendiente"):
    return {"id": titulo, "titulo": titulo, "estado": estado,
            "vence": (date.today() + timedelta(days=dias)).isoformat()}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        _seg(tmp, [
            _hilo("vencio ayer", -1),
            _hilo("vencio hace 5", -5),
            _hilo("vencio hace 9", -9),
            _hilo("vencio hace 40", -40),     # por debajo del suelo: ya no es un plazo
            _hilo("vence hoy", 0),
            _hilo("vence en 3", 3),
            _hilo("vence en 20", 20),         # fuera de ventana por arriba, como siempre
            _hilo("cerrado y vencido", -6, estado="hecho"),
        ])
        p = c._plazos_seguimiento()
        titulos = {v[0]: v for v in p.values()}

        check("un plazo vencido AYER entra (antes se caía)", "vencio ayer" in titulos)
        check("uno de hace 5 días entra", "vencio hace 5" in titulos)
        check("uno de hace 9 días entra", "vencio hace 9" in titulos)
        check("el suelo de 30 días corta: hace 40 NO entra", "vencio hace 40" not in titulos)
        check("lo de hoy sigue entrando", "vence hoy" in titulos)
        check("lo de dentro de 3 sigue entrando", "vence en 3" in titulos)
        check("lo de dentro de 20 sigue fuera", "vence en 20" not in titulos)
        check("un hilo CERRADO no se persigue aunque esté vencido",
              "cerrado y vencido" not in titulos)

        # Hitos: cada uno con su clave estable → 3 avisos por hilo a lo largo del mes, no uno por
        # pasada. Sin esto el arreglo sería un generador de spam.
        check("hito T+1 para el de ayer", titulos["vencio ayer"][2] == "T+1")
        check("hito T+3 para el de hace 5", titulos["vencio hace 5"][2] == "T+3")
        check("hito T+7 para el de hace 9", titulos["vencio hace 9"][2] == "T+7")
        check("hito T-0 para el de hoy", titulos["vence hoy"][2] == "T-0")
        check("los días son negativos en los vencidos", titulos["vencio ayer"][3] == -1)

        claves = list(p.keys())
        check("las claves de dedup son únicas por hilo+fecha+hito",
              len(claves) == len(set(claves)))

        # Dos pasadas seguidas dan las MISMAS claves: un vencido ya avisado no re-spamea.
        check("la clave es estable entre pasadas",
              set(c._plazos_seguimiento().keys()) == set(claves))

    # ── Agrupación: los vencidos salen en UN aviso, no en N ────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        _seg(tmp, [_hilo("uno", -2), _hilo("dos", -3), _hilo("tres", -4), _hilo("y ademas hoy", 0)])
        orig = c._cargar_mark
        # `ts` presente = NO es la primera pasada (en la primera, detectar() calla a propósito para
        # no vaciar el buzón entero encima de {{TITULAR}}). Sin plazos vistos: todo es nuevo.
        c._cargar_mark = lambda: {"ts": "2026-07-24T00:00:00", "plazos_avisados": []}
        try:
            avisos, _estado = c.detectar()
        finally:
            c._cargar_mark = orig
        vencidos = [t for k, t in avisos if k == "plazo-vencido"]
        sueltos = [t for k, t in avisos if k == "plazo"]
        check("los 3 vencidos van en UN solo aviso", len(vencidos) == 1)
        check("y el que vence hoy sigue yendo aparte", len(sueltos) == 1)
        if vencidos:
            txt = vencidos[0]
            check("el aviso lleva la lista COMPLETA, sin recortar",
                  all(t in txt for t in ("uno", "dos", "tres")))
            check("dice cuántos son", txt.startswith("3 plazo"))
            check("no dice «se acerca» sobre algo que ya pasó", "se acerca" not in txt)

    print("test_centinela_vencidos: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
