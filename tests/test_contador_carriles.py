#!/usr/bin/env python3
"""test_contador_carriles.py — cada respuesta de NVIDIA y de Jev deja una línea en el ledger de
gasto, y los pings de salud llegan marcados. Offline: red falsa, ledger en un directorio temporal.

POR QUÉ (25-sep-2026). La revisión de {{CONTACTO}}+KAI dio «NVIDIA, 10.065 llamadas»: eran filas del
ledger del borde (bloqueos por HALT, pings y tests); respuestas de verdad, 134. NVIDIA y Jev no
se apuntaban en el ledger de gasto, que es donde Grok, Gemini, ChatGPT y GLM ya cuentan.
Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="contador_carriles_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_GASTO_LEDGER"] = os.path.join(_TMP, "gasto.jsonl")   # ledger propio y vacío
os.environ.pop("BTP_ORIGEN", None)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gasto  # noqa: E402
import nvidia  # noqa: E402
import bench_jev  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


LEDGER_REAL = os.path.join(os.path.expanduser("~/claudecode"), "tools", ".gasto_ledger.jsonl")


def lineas():
    ruta = gasto._ledger()
    if not os.path.exists(ruta):
        return []
    return [json.loads(l) for l in open(ruta, encoding="utf-8") if l.strip()]


class _Resp:
    def __init__(self, cuerpo):
        self.cuerpo = cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.cuerpo

    def readline(self):
        return b""


def main():
    ruta = gasto._ledger()
    check("el ledger del test NO es el real (%s)" % ruta, os.path.realpath(ruta) !=
          os.path.realpath(LEDGER_REAL))
    real_antes = os.path.getsize(LEDGER_REAL) if os.path.exists(LEDGER_REAL) else None

    orig_url, orig_veto = nvidia.urllib.request.urlopen, nvidia._veto_borde
    orig_jev_url, orig_sellar = bench_jev.urllib.request.urlopen, bench_jev.borde._sellar
    nvidia._veto_borde = lambda body: None
    bench_jev.borde._sellar = lambda ev: None
    try:
        # 1) NVIDIA, carril automático (post): una línea, usd 0, tokens del usage
        cuerpo = json.dumps({"choices": [{"message": {"content": "OK"}}],
                             "usage": {"prompt_tokens": 7, "completion_tokens": 2}}).encode()
        nvidia.urllib.request.urlopen = lambda *a, **k: _Resp(cuerpo)
        data, err = nvidia.post(nvidia.CHAT_URL, "k", {"model": "m-prueba", "messages": []})
        ls = lineas()
        check("nvidia.post responde", err is None and data)
        check("nvidia.post apunta una línea", len(ls) == 1 and ls[-1]["tool"] == "nvidia")
        check("nvidia: usd 0 (gratis) y tokens del usage",
              ls and ls[-1]["usd"] == 0 and ls[-1]["input_tokens"] == 7
              and ls[-1]["output_tokens"] == 2 and ls[-1]["model"] == "m-prueba")
        check("sin BTP_ORIGEN no hay campo origen", ls and "origen" not in ls[-1])

        # 2) error de red → ninguna línea
        def _rota(*a, **k):
            raise OSError("red caída")
        nvidia.urllib.request.urlopen = _rota
        data, err = nvidia.post(nvidia.CHAT_URL, "k", {"model": "m-prueba", "messages": []})
        check("error de red: no apunta nada", err and len(lineas()) == 1)

        # 3) el ping llega marcado
        nvidia.urllib.request.urlopen = lambda *a, **k: _Resp(cuerpo)
        os.environ["BTP_ORIGEN"] = "ping"
        try:
            nvidia.post(nvidia.CHAT_URL, "k", {"model": "m-prueba", "messages": []})
        finally:
            os.environ.pop("BTP_ORIGEN", None)
        check("con BTP_ORIGEN=ping la línea lleva origen ping", lineas()[-1].get("origen") == "ping")

        # 4) Jev: una línea sin tarifa (créditos, no hay precio que inventar)
        bench_jev.urllib.request.urlopen = lambda *a, **k: _Resp(
            b'{"answers": {"es_tarea": {"noul": 0.4}}}')
        bench_jev.preguntar("texto de prueba", "k")
        u = lineas()[-1]
        check("jev apunta una línea sin tarifa", u["tool"] == "jev" and u.get("sin_tarifa") is True
              and u["usd"] is None)
        bench_jev.urllib.request.urlopen = _rota
        n = len(lineas())
        try:
            bench_jev.preguntar("texto", "k")
        except OSError:
            pass
        check("jev sin respuesta: no apunta", len(lineas()) == n)
    finally:
        nvidia.urllib.request.urlopen, nvidia._veto_borde = orig_url, orig_veto
        bench_jev.urllib.request.urlopen, bench_jev.borde._sellar = orig_jev_url, orig_sellar

    # 5) enruta lanza el ping con BTP_ORIGEN=ping (lo mira en el código, sin gastar una llamada)
    fuente = open(os.path.join(ROOT, "tools", "enruta.py"), encoding="utf-8").read()
    check("enruta.probar marca sus pings", 'BTP_ORIGEN="ping"' in fuente)

    real_despues = os.path.getsize(LEDGER_REAL) if os.path.exists(LEDGER_REAL) else None
    check("el ledger real no se ha tocado", real_antes == real_despues)
    print("test_contador_carriles: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
