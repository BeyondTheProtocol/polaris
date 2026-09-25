#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Una sola forma de clave en el libro de deuda, y la alerta resumen apunta a su hallazgo.

BUG REAL (24-sep-26), cinco «jobs caídos sin-entregable» del panel, 0,80 a 1,70 USD cada uno:
  a) job 8052991d8b: la alerta traía `Python` entre backticks y su texto era la clave. El
     encargo decía `deuda.py abrir "<clave>"`; dentro de comillas dobles un backtick ejecuta
     un comando, así que el agente los quitó y anotó otra clave. El gate comparaba letra a
     letra, no encontró su anotación y tumbó un trabajo bien hecho. En el libro quedaron DOS
     entradas del mismo hallazgo.
  b) jobs 53696bb300 y b1a2bfe70b: la alerta resumen `deuda_escalada` exigía como prueba la
     clave `deuda_escalada`, que NO está en el libro a propósito. Ningún trabajo podía pasar.

Lo que fija:
  1. deuda.normalizar_clave quita backticks y espacios de más; todos los verbos la usan.
  2. al cargar, dos entradas que solo difieren en eso se funden en una (veces = máximo).
  3. el gate (prueba_entregable) encuentra la anotación aunque la clave del job traiga backticks.
  4. la investigación de `deuda_escalada` exige como prueba la clave del peor escalado.
  5. y la de una alerta normal con backticks, la clave normalizada."""
import json
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
sys.path.insert(0, os.path.join(RAIZ, "tests"))
_STATE = tempfile.mkdtemp(prefix="btp_clave_deuda_")
os.environ["BTP_STATE_DIR"] = _STATE
os.environ["BTP_TEST_BATTERY"] = "1"      # healthcheck importa salida: ni un mensaje real
import deuda              # noqa: E402
import prueba_entregable  # noqa: E402
import healthcheck as hc  # noqa: E402
import cola as q          # noqa: E402
import salida             # noqa: E402
from _entorno import exige_cola_aislada  # noqa: E402

fallos = []
CON = "`Python` (pid 27648) pide 12 GB  en una máquina de 16."
SIN = "Python (pid 27648) pide 12 GB en una máquina de 16."


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _libro(d):
    with open(deuda.LIBRO, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def main():
    check(deuda.LIBRO.startswith(_STATE), "el libro del test está aislado en tmp")

    print("── 1. una sola forma ──")
    check(deuda.normalizar_clave(CON) == SIN, "sin backticks ni dobles espacios")
    check(deuda.normalizar_clave(SIN) == SIN, "idempotente")

    print("── 2. las dos entradas del pid 27648 se funden ──")
    _libro({
        CON: {"que": CON, "estado": "remitido", "veces": 1, "visto_ts": 100.0, "nota": "de healthcheck"},
        SIN: {"que": "la causa de verdad", "estado": "remitido", "veces": 2, "visto_ts": 200.0,
              "nota": "del tecnico"},
    })
    d = deuda._cargar()
    check(list(d) == [SIN], "queda UNA entrada, con la clave normalizada")
    check(d[SIN]["que"] == "la causa de verdad", "manda la entrada tocada más tarde")
    check(d[SIN]["veces"] == 2, "veces = máximo, no suma (no se inventan detecciones)")
    check("de healthcheck" in d[SIN]["nota"], "la nota de la otra no se pierde")
    deuda.anotar(CON, "nota nueva con la clave con backticks")
    d = json.load(open(deuda.LIBRO, encoding="utf-8"))
    check(list(d) == [SIN] and "nota nueva" in d[SIN]["nota"],
          "anotar con backticks escribe en la entrada única, y el disco queda migrado")

    print("── 3. el gate encuentra la anotación ──")
    antes = time.time() - 1
    _libro({CON: {"que": CON, "estado": "abierto", "veces": 1, "visto_ts": antes - 3600}})
    deuda.anotar(SIN, "el agente anota sin backticks")
    ok, motivo = prueba_entregable.cumple({"tipo": "deuda", "clave": CON}, antes)
    check(ok, "prueba con backticks + anotación sin ellos → cumple (%s)" % motivo)
    _libro({CON: {"que": CON, "estado": "abierto", "veces": 1, "visto_ts": antes - 3600}})
    ok, motivo = prueba_entregable.cumple({"tipo": "deuda", "clave": SIN}, antes)
    check(not ok and "no se toco" in motivo, "sin anotación nueva sigue sin cumplir (fail-closed)")

    print("── 4 y 5. la prueba que exige la investigación ──")
    _libro({
        "hallazgo-leve": {"que": "x", "estado": "escalado", "veces": 3},
        "hallazgo-peor": {"que": "y", "estado": "escalado", "veces": 9},
        "hallazgo-cerrado": {"que": "z", "estado": "cerrado", "veces": 40},
    })
    check(hc._clave_del_libro("deuda_escalada") == "hallazgo-peor",
          "deuda_escalada → el escalado con más detecciones")
    check(hc._clave_del_libro(CON) == SIN, "alerta normal → su clave normalizada")

    exige_cola_aislada_ok = True
    with tempfile.TemporaryDirectory() as tmp:
        q.QUEUE = os.path.join(tmp, "queue")
        try:
            exige_cola_aislada()
        except SystemExit:
            exige_cola_aislada_ok = False
        q._ensure_dirs()
        orig = salida.halted
        salida.halted = lambda: False
        try:
            hc._encolar_investigacion("deuda_escalada", "🔴 2 hallazgos escalados")
            hc._encolar_investigacion(CON, CON)
        finally:
            salida.halted = orig
        pend = sorted(os.listdir(os.path.join(q.QUEUE, "pending")))
        jobs = [q._load(os.path.join(q.QUEUE, "pending", p)) for p in pend]
    check(exige_cola_aislada_ok, "la cola del test está aislada")
    pruebas = sorted(j["prueba"]["clave"] for j in jobs)
    check(pruebas == sorted(["hallazgo-peor", SIN]),
          "los jobs exigen el hallazgo concreto y la clave normalizada, no la prosa (%s)" % pruebas)
    resumen = [j for j in jobs if j["procedencia"] == "healthcheck:deuda_escalada"]
    check(resumen and "hallazgo-peor" in resumen[0]["intencion"]
          and "salud.py resuelto deuda_escalada" in resumen[0]["intencion"],
          "el encargo del resumen nombra el hallazgo y sigue cerrando la alerta resumen")
    normal = [j for j in jobs if j is not (resumen[0] if resumen else None)]
    check(normal and "alerta RESUMEN" not in normal[0]["intencion"],
          "una alerta normal no se presenta como resumen")

    print()
    if fallos:
        print("❌ %d fallo(s)" % len(fallos))
        return 1
    print("✅ clave de deuda: una sola forma, y cada job exige una prueba que se puede cumplir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
