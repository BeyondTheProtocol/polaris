#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del portero de ruido de Vega (27-jul-26).

EL PROBLEMA QUE ARREGLA: {{TITULAR}} dijo «Vega habla tanto que ya no le hago caso», y el
propio outbox le daba la razón: 33 mensajes/día de media, pico de 71. Con ese volumen
dejar de mirar es lo racional — y entonces la señal se pierde. El 26-jul saltó un
CÓDIGO ROJO, se entregaron 10 alertas críticas, y el lazo estuvo 37 HORAS parado sin
que ella se enterara.

Lo que estos tests protegen es el equilibrio exacto:
  · lo rutinario se agrupa (deja de ser ruido),
  · lo urgente pasa SIEMPRE (si esto se rompe, el portero se convierte en una mordaza),
  · y nada se pierde por el camino.
"""
import importlib
import json
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _salida_aislada(tmp, tope="3"):
    """Importa salida.py con su estado apuntando a un tmpdir y sin tocar la red."""
    os.environ["BTP_STATE_DIR"] = tmp
    os.environ["BTP_TOPE_AVISOS"] = tope
    os.environ["BTP_TEST_BATTERY"] = "1"     # ni un mensaje real a {{TITULAR}}
    import salida
    importlib.reload(salida)
    salida.STATE = tmp
    salida.OUTBOX = os.path.join(tmp, "outbox")
    os.makedirs(salida.OUTBOX, exist_ok=True)
    return salida


def _finge_entregados(salida, n):
    """Escribe n entregas en el audit de hoy, como si ya hubieran salido."""
    import time
    path = os.path.join(salida.OUTBOX, "audit-%s.jsonl" % time.strftime("%Y-%m-%d"))
    with open(path, "a", encoding="utf-8") as fh:
        for _ in range(n):
            fh.write(json.dumps({"ts": "x", "canal": "telegram", "accion": "report",
                                 "veredicto": "entregado"}) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="btp_portero_")
    try:
        salida = _salida_aislada(tmp, tope="3")

        print("── el contador lee lo que DE VERDAD salió ──")
        check(salida._entregados_hoy() == 0, "empieza a cero")
        _finge_entregados(salida, 2)
        check(salida._entregados_hoy() == 2, "cuenta las entregas del audit")
        check(not salida._presupuesto_agotado(), "con 2 de 3, aún queda margen")
        _finge_entregados(salida, 1)
        check(salida._presupuesto_agotado(), "al llegar a 3, se agota")

        print("── lo rutinario se agrupa, no se pierde ──")
        r = salida.report_to_titular("cuarto aviso rutinario del día", categoria="humano")
        # `_result` aplana el extra en la raíz del dict, no lo anida bajo "extra".
        check(isinstance(r, dict) and r.get("aplazado") and not r.get("delivered"),
              "el 4º aviso se aplaza en vez de entregarse")
        apl = salida.aplazados_de_hoy()
        check(len(apl) >= 1 and "cuarto aviso" in json.dumps(apl, ensure_ascii=False),
              "y queda guardado entero para el parte (no se tira)")

        print("── lo urgente SIEMPRE pasa ──")
        # Si esto falla, el portero es una mordaza y hay que quitarlo entero.
        r = salida.report_to_titular("esto no puede esperar", urgente=True)
        check(not (isinstance(r, dict) and r.get("aplazado")),
              "un aviso urgente NO se aplaza aunque el presupuesto esté agotado")

        print("── el código rojo va por su propio carril ──")
        fuente = open(os.path.join(RAIZ, "tools", "salida.py"), encoding="utf-8").read()
        pos_critica = fuente.find("def alerta_critica")
        pos_portero = fuente.find("_presupuesto_agotado()")
        check(pos_portero < pos_critica,
              "alerta_critica() no pasa por el portero (es otra función, sin presupuesto)")

        print("── ante la duda, el mensaje sale ──")
        salida2 = _salida_aislada(os.path.join(tmp, "roto"), tope="3")
        salida2.OUTBOX = "/ruta/que/no/existe"
        check(salida2._entregados_hoy() == 0 and not salida2._presupuesto_agotado(),
              "si no puede leer el audit, NO bloquea (fail-open)")
    finally:
        for k in ("BTP_STATE_DIR", "BTP_TOPE_AVISOS", "BTP_TEST_BATTERY"):
            os.environ.pop(k, None)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ portero de ruido OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
