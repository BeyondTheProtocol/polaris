#!/usr/bin/env python3
"""test_saldo_aprende.py — el aviso de prepago llega ANTES del corte, no después.

QUÉ PASÓ (26-sep-26). El prepago de Anthropic se agotó 5 veces en septiembre y el único aviso fue
el de «ya se acabó». Tres fallos juntos:
  · healthcheck ponía la baseline a HOY en cada pasada con la API viva (189 marcadores en
    recargas.jsonl): el estimador se reiniciaba cada 30 min y nunca llegaba al 75 %.
  · run_agent.sh no escribía la señal de crédito: a las 00:21 decía «hay crédito» con la API ya
    rechazando.
  · el gasto de Grok/Perplexity (jobs `api-*`) se contaba como prepago de Anthropic.
Aquí se prueba que cada uno queda corregido, sobre un estado de juguete.
"""
import json
import os
import sys
import tempfile
import time

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


def _dia(d, fecha, eventos):
    with open(os.path.join(d, "%s.json" % fecha), "w") as f:
        json.dump({"fecha": fecha, "gastado_usd": sum(e["usd"] for e in eventos),
                   "eventos": eventos}, f)


def _lineas(d):
    p = os.path.join(d, "recargas.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def main():
    with tempfile.TemporaryDirectory() as tmp:
        cg.COST, cg.STATE = os.path.join(tmp, "cost"), tmp
        os.makedirs(cg.COST)
        hoy = time.strftime("%Y-%m-%d")
        base = time.time() - 10 * 3600          # recarga hace 10 h
        with open(os.path.join(cg.COST, "recargas.jsonl"), "w") as f:
            f.write(json.dumps({"ts": base, "fecha": hoy, "monto_usd": 20.0}) + "\n")
        _dia(cg.COST, hoy, [
            {"job": "3f46335cf0", "usd": 10.0, "via": "api"},       # Anthropic por el lazo
            {"job": "api-grok", "usd": 7.0, "via": "api"},          # otro proveedor
            {"job": "asistente-x", "usd": 30.0, "via": "suscripcion"},
        ])

        # ── fallo 3: otros proveedores fuera del prepago ──
        det = cg.spent_since_detallado(hoy)
        check("api-grok no cuenta como prepago de Anthropic", det["api"] == 10.0)
        check("…pero sigue en el total (el tope diario no cambia)", det["total"] == 47.0)
        check("…y se ve aparte", det["otros_proveedores"] == 7.0)

        sp = cg.saldo_prepago()
        check("frac_firme = solo Anthropic (10 de 20)", abs(sp["frac_firme"] - 0.5) < 1e-9)
        check("previsión: 1 $/h durante 10 h → quedan ~10 h", 9.5 <= sp["horas_restantes"] <= 10.5)

        # ── fallo 1: el resync ya no reinicia en bucle ──
        check("con gasto < importe no se sube nada", cg.resync_baseline_auto() is None)
        n = len(_lineas(cg.COST))
        _dia(cg.COST, hoy, [{"job": "j", "usd": 25.0, "via": "api"}])
        r = cg.resync_baseline_auto()
        check("gastado 25 > 20 con API viva → importe sube a 50", r and r["monto_usd"] == 50.0)
        check("…sin mover la baseline (misma hora)", r and r["ts"] == base)
        check("…y una segunda pasada no añade otra línea (sin bucle)",
              cg.resync_baseline_auto() is None and len(_lineas(cg.COST)) == n + 1)

        # ── aprendizaje: corte → recarga ──
        with open(os.path.join(cg.COST, "recargas.jsonl"), "w") as f:
            f.write(json.dumps({"ts": base, "fecha": hoy, "monto_usd": 20.0}) + "\n")
        _dia(cg.COST, hoy, [{"job": "j", "usd": 33.0, "via": "api"}])
        check("con crédito y sin corte previo: nada que anotar", cg.observar_credito(True) is None)
        a = cg.observar_credito(False)
        check("corte: se anota lo gastado desde la recarga (33 $)", a and a["gastado"] == 33.0)
        check("…una sola vez por corte", cg.observar_credito(False) is None)
        r = cg.observar_credito(True)
        check("vuelve el crédito: recarga detectada con el importe aprendido (33 $)",
              r and r["monto_usd"] == 33.0 and r["auto"] == "recarga_detectada")
        check("…y es la nueva baseline", cg.ultima_recarga()["monto_usd"] == 33.0)
        check("señal None no hace nada", cg.observar_credito(None) is None)

        # ── fallo 2: la señal la puede escribir run_agent ──
        check("cost_guard.py credito agotado", cg.main(["credito", "agotado"]) == 0
              and cg.credito_ok() is False)
        check("cost_guard.py credito ok", cg.main(["credito", "ok"]) == 0
              and cg.credito_ok() is True)

    run_agent = open(os.path.join(ROOT, "tools", "run_agent.sh")).read()
    check("run_agent marca «agotado» al ver el 400 de saldo", "cost_guard.py\" credito agotado" in run_agent)
    check("run_agent marca «ok» tras un éxito por la API medida", "cost_guard.py\" credito ok" in run_agent)

    # ── tope de gasto de la consola (26-sep-26): no es saldo ni rate, y avisa con fecha y enlace ──
    import re
    import subprocess
    m = re.search(r"^is_tope_consola\(\) \{.*?^\}", run_agent, re.S | re.M)
    check("run_agent define is_tope_consola", m is not None)
    if m:
        real = ('{"is_error":true,"result":"API Error: 400 {\\"type\\":\\"invalid_request_error\\",'
                '\\"message\\":\\"You have reached your specified API usage limits. You will regain '
                'access on 2026-10-01 at 00:00 UTC.\\"}"}')
        casos = {
            "respuesta real del 26-sep": (real, 0),
            "saldo agotado (otra cosa)": ('{"is_error":true,"result":"Credit balance is too low"}', 1),
            "rate limit pasajero": ('{"is_error":true,"api_error_status":429,"result":"rate limit"}', 1),
            "respuesta buena que cita el texto": ('{"is_error":false,"result":"usage limits … regain access on"}', 1),
        }
        for nombre, (out, rc) in casos.items():
            r = subprocess.run(["bash", "-c", m.group(0) + '\nis_tope_consola "$1"', "_", out],
                               capture_output=True, stdin=subprocess.DEVNULL)
            check("is_tope_consola: " + nombre, r.returncode == rc)
    check("el tope corta la cadena de modelos (misma cuenta)",
          'is_credit_out "$OUT" || is_tope_consola "$OUT"; then break' in run_agent)
    check("el aviso lleva el enlace para subir el límite", "platform.claude.com/settings/limits" in run_agent)
    check("latido propio «tope_consola»", 'heartbeat "tope_consola"' in run_agent)

    print("test_saldo_aprende: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
