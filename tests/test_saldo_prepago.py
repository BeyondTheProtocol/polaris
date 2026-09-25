#!/usr/bin/env python3
"""test_saldo_prepago.py — el estimador del prepago deja de afirmar lo que no puede saber.

QUÉ PASÓ (25-jul-26). El libro de deuda escaló `saldo_prepago_aviso` a la tercera detección y puso
`test_all.sh` en ROJO, que es exactamente para lo que se montó. Al mirarlo, el aviso no era cierto:

  · El prepago de Anthropic solo lo consume la API MEDIDA, pero `spent_since` sumaba TODO el gasto
    del CLI, incluida la cuota del plan Max, que no sale de ahí. Sobre el estado vivo: recarga de
    20 $, «gastado» 49,72 $, restante −29,72 $. Un número imposible, y con él el estimador cruzaba
    el umbral del 75% a los dos días de cualquier recarga, para siempre.
  · La comprobación REAL (`cost_guard.credito_ok`, que lee `state/ia/credito.json`) llevaba 50,9 h
    sin refrescarse con un TTL de 6 h, porque la escribe `ia.ask` y el lazo ejecuta por
    `run_agent.sh`. O sea que devolvía None en cada pasada.
  · Con las dos cosas rotas, healthcheck caía siempre en la rama del aviso suave y repetía
    «Saldo de la API estimado bajo» — una afirmación que ninguna de las dos fuentes sostenía.

El arreglo no es callar la alerta, es que diga la verdad: el gasto se cuenta POR CANAL (el campo
`via` existe desde ese mismo día), y mientras la mayor parte no esté etiquetada el sistema dice
«no puedo saberlo» en vez de «está bajo».
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cost_guard as cg  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _dia(tmp, fecha, total, eventos=None):
    """Escribe un fichero-día de gasto como los reales."""
    d = {"fecha": fecha, "gastado_usd": total, "n_jobs": len(eventos or [])}
    if eventos is not None:
        d["eventos"] = eventos
    with open(os.path.join(tmp, "%s.json" % fecha), "w", encoding="utf-8") as f:
        json.dump(d, f)


def _ev(usd, via=None):
    e = {"ts": "2026-07-25T10:00:00", "job": "x", "usd": usd}
    if via:
        e["via"] = via
    return e


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cg.COST = tmp

        _dia(tmp, "2026-07-23", 10.0, [_ev(6.0, "api"), _ev(4.0, "suscripcion")])
        _dia(tmp, "2026-07-24", 5.0, [_ev(5.0)])                    # evento sin `via`
        _dia(tmp, "2026-07-25", 8.0)                                # día sin desglose
        _dia(tmp, "2026-07-22", 99.0, [_ev(99.0, "api")])           # anterior al corte: no cuenta

        det = cg.spent_since_detallado("2026-07-23")
        check("el total no cambia (compatibilidad con spent_since)", det["total"] == 23.0)
        check("separa lo que salió de la API medida", det["api"] == 6.0)
        check("separa lo que fue por la cuota del plan (NO toca el prepago)",
              det["suscripcion"] == 4.0)
        check("un evento sin `via` no se adivina: va a sin_etiquetar", det["sin_etiquetar"] == 13.0)
        check("un día sin desglose cae ENTERO a sin_etiquetar (5 del evento + 8 del día)",
              det["sin_etiquetar"] == 13.0)
        check("lo anterior a la recarga sigue fuera", det["total"] < 99.0)
        check("spent_since sigue devolviendo el total de siempre",
              cg.spent_since("2026-07-23") == 23.0)

        # El agregado del día puede superar la suma de sus eventos (podados, o anotados antes de
        # que el desglose existiera). Ese resto tampoco se sabe de qué canal es.
        with tempfile.TemporaryDirectory() as t2:
            cg.COST = t2
            _dia(t2, "2026-07-23", 10.0, [_ev(3.0, "api")])
            d2 = cg.spent_since_detallado("2026-07-23")
            check("el resto entre el total del día y sus eventos va a sin_etiquetar",
                  d2["api"] == 3.0 and d2["sin_etiquetar"] == 7.0)

        # ── saldo_prepago: fiable / no fiable ──────────────────────────────────────
        cg.COST = tmp
        cg.ultima_recarga = lambda: {"fecha": "2026-07-23", "monto_usd": 20.0}
        sp = cg.saldo_prepago()
        check("marca el estimador como NO fiable (13 de 23 sin etiquetar)", sp["fiable"] is False)
        check("frac_firme cuenta solo lo confirmado de API", abs(sp["frac_firme"] - 6.0 / 20) < 1e-9)
        check("frac sigue siendo el bruto de siempre", abs(sp["frac"] - 23.0 / 20) < 1e-9)

        with tempfile.TemporaryDirectory() as t3:
            cg.COST = t3
            _dia(t3, "2026-07-23", 18.0, [_ev(17.0, "api"), _ev(1.0)])
            sp2 = cg.saldo_prepago()
            check("con casi todo etiquetado, el estimador SÍ es fiable", sp2["fiable"] is True)

    # ── healthcheck: qué se le dice a {{TITULAR}} en cada caso ──────────────────────────
    import healthcheck as hc

    def alertas_con(sp, credito):
        orig_sp, orig_ck, orig_obs = cg.saldo_prepago, cg.credito_ok, cg.observar_credito
        cg.saldo_prepago, cg.credito_ok = (lambda: sp), (lambda: credito)
        cg.observar_credito = lambda ok: None
        try:
            al, _info = hc._check_presupuesto()
        finally:
            cg.saldo_prepago, cg.credito_ok, cg.observar_credito = orig_sp, orig_ck, orig_obs
        return {c: t for c, t in al}

    no_fiable = {"monto": 20.0, "gastado": 49.72, "restante": -29.72, "frac": 2.48,
                 "frac_firme": 0.0, "sin_etiquetar": 49.72, "fiable": False,
                 "fecha_recarga": "2026-07-23"}
    a = alertas_con(no_fiable, None)
    check("estimador NO fiable + sonda muda → NO se dice «bajo»",
          "saldo_prepago_aviso" not in a)
    check("…se dice que no se puede saber", "saldo_prepago_sin_señal" in a)
    if "saldo_prepago_sin_señal" in a:
        t = a["saldo_prepago_sin_señal"]
        check("…el mensaje lo dice en llano", "No puedo saber" in t)
        check("…y explica POR QUÉ el número no vale (canal sin identificar)",
              "sin identificar" in t)
        check("…y aclara que no es una alarma de saldo", "no es una alarma de saldo" in t)

    fiable = dict(no_fiable, gastado=18.0, restante=2.0, frac=0.9,
                  frac_firme=0.85, sin_etiquetar=1.0, fiable=True)
    a = alertas_con(fiable, None)
    check("estimador fiable + sonda muda → sí se avisa de saldo bajo",
          "saldo_prepago_aviso" in a and "saldo_prepago_sin_señal" not in a)

    a = alertas_con(no_fiable, False)
    check("la API dice SIN CRÉDITO → urgente, pase lo que pase con el estimador",
          "saldo_prepago_urgente" in a)
    check("…y entonces no se emite el «no puedo saberlo» (hay señal real)",
          "saldo_prepago_sin_señal" not in a)


    # ── La SONDA que devuelve la capacidad de saberlo (25-jul-26) ──────────────────
    # Que el aviso no mienta arreglaba media cosa: el sistema seguia sin PODER saber su saldo.
    # `credito.json` lo escribia solo `ia.ask`, y el lazo va por run_agent.sh (cuota del plan,
    # que no toca el prepago). Ahora hay una sonda minima que lo refresca, y la dispara la de
    # salud, que ya corre sola cada ~2 h. Va por el binario `claude`, el MISMO camino que usa
    # `_claude`: un cliente HTTP propio habria sido una segunda boca hacia Anthropic y el test
    # de fuga del muro lo casco al primer intento (bien cascado).
    import ia as _ia
    import subprocess as _sp

    class _P:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    escrito, corridos = {}, []
    orig_run, orig_marcar, orig_key = _sp.run, _ia._marcar_credito, _ia.get_secret
    _ia._marcar_credito = lambda ok: escrito.update(ok=ok)
    _ia.get_secret = lambda *a, **k: "clave-de-prueba"

    def _responde(rc, out):
        def fake(args, **k):
            corridos.append(args)
            return _P(rc, out)
        _ia.subprocess.run = fake

    try:
        _responde(0, '{"result":"hi"}')
        escrito.clear()
        check("respuesta normal de Claude -> hay credito", _ia.sonda_credito() is True)
        check("...y queda escrito para cost_guard", escrito.get("ok") is True)
        check("...preguntando lo minimo (un `hi` al modelo mas barato)",
              corridos and "hi" in corridos[-1] and "haiku" in " ".join(corridos[-1]))

        escrito.clear()
        _responde(1, '{"is_error":true,"result":"API Error: 400 credit balance is too low"}')
        check("400 «credit balance is too low» -> NO hay credito", _ia.sonda_credito() is False)
        check("...y tambien se escribe (es LA señal que importa)", escrito.get("ok") is False)

        escrito.clear()
        _responde(1, '{"is_error":true,"result":"api_error_status: 429 rate limit"}')
        check("un 429 no dice nada del saldo -> None", _ia.sonda_credito() is None)
        check("...y NO se escribe nada (una señal fresca que miente es peor que una vieja)",
              "ok" not in escrito)

        escrito.clear()
        _responde(1, "")
        check("salida vacia -> None, sin escribir",
              _ia.sonda_credito() is None and "ok" not in escrito)

        _ia.get_secret = lambda *a, **k: None
        os.environ.pop("ANTHROPIC_API_KEY", None)
        check("sin clave en el Llavero -> None (no inventa)", _ia.sonda_credito() is None)
    finally:
        _ia.subprocess.run, _ia._marcar_credito, _ia.get_secret = orig_run, orig_marcar, orig_key

    print("test_saldo_prepago: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
