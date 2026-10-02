#!/usr/bin/env python3
"""test_codigo_rojo_excepciones.py — una decisión deliberada de {{TITULAR}} no vuelve a parar el sistema.

2-oct-2026: un job del comité médico leyó en WhatsApp que {{TITULAR}} va a empezar antiparasitarios
«sin decírselo a nadie» y disparó el código rojo, aunque el 25-sep ella ya había pedido esa
excepción (quedó solo en una memoria que el lazo no ve). Paró todo, también el parte. Se fija:
  1. el motivo real del 2-oct cae en la excepción «pauta-metabolica-lola»;
  2. una señal de daño real (transaminasas, grado 3, dejar la medicación…) NO cae: dispara;
  3. «sin dejar la medicación» (su propia frase) no cuenta como dejarla;
  4. un motivo ajeno (p. ej. no elegible para la vacuna) no cae;
  5. trigger() con excepción no para, no avisa y no apaga daemons, pero lo apunta en el buzón de
     Vega; sin excepción hace lo de siempre.
Probar el muro sin dispararlo (regla del 22-sep): todo lo que escala se sustituye antes.
"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="cr_exc_")
os.environ["BTP_STATE_DIR"] = tmp
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import codigo_rojo  # noqa: E402

llamadas = []
codigo_rojo._halt_all = lambda *a, **k: llamadas.append("halt")
codigo_rojo._write_report = lambda *a, **k: llamadas.append("informe")
codigo_rojo._stop_launchd = lambda *a, **k: llamadas.append("stop")
codigo_rojo._start_launchd = lambda *a, **k: llamadas.append("start")
codigo_rojo._debe_avisar = lambda *a, **k: (True, "test")
codigo_rojo.salida.alerta_critica = lambda *a, **k: llamadas.append("alerta") or {"reason": "espia"}

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def exc(m, d=""):
    e = codigo_rojo.excepcion(m, d)
    return e and e["id"]


REAL = "Antiparasitarios en secreto antes de la dosis 1 del ensayo (7-oct)"
DET = "WhatsApp 30-sep: «voy a empezar yo también, sin decírselo a nadie… como mucho me sube el valor del hígado y lo tengo que dejar»"
check(exc(REAL, DET) == "pauta-metabolica-lola", "el motivo real del 2-oct cae en la excepción")
check(exc("Ivermectina y Lomper en la ventana previa a TB06") == "pauta-metabolica-lola", "pauta de Lola del 25-sep")
check(exc("Antiparasitarios", "sin dejar la medicación con evidencia") == "pauta-metabolica-lola",
      "su frase «sin dejar la medicación» no cuenta como dejarla")
check(exc("Antiparasitarios y transaminasas grado 3") is None, "con daño hepático real: dispara")
check(exc("Ivermectina", "GOT 240, GPT 310") is None, "con GOT/GPT en el detalle: dispara")
check(exc("Metformina y acidosis láctica") is None, "acidosis láctica: dispara")
check(exc("Quiere dejar el tratamiento del ensayo por la pauta metabólica") is None,
      "dejar el tratamiento con evidencia: dispara")
check(exc("Fred Hutch declara a {{TITULAR}} no elegible para la vacuna") is None, "motivo ajeno: dispara")

print("── trigger con excepción ──")
r = codigo_rojo.trigger(REAL, DET)
check(llamadas == [], "no para, no informa, no avisa, no apaga (%r)" % llamadas)
check(r.get("excepcion") == "pauta-metabolica-lola", "devuelve la excepción aplicada")
buzon = os.path.join(tmp, "vega", "propuestas_hilos.jsonl")
filas = [json.loads(l) for l in open(buzon)] if os.path.exists(buzon) else []
check(len(filas) == 1 and filas[0]["origen"] == "codigo_rojo", "lo apunta en el buzón de Vega")

print("── trigger sin excepción ──")
codigo_rojo.trigger("Fred Hutch declara a {{TITULAR}} no elegible para la vacuna", "")
check(llamadas == ["halt", "informe", "alerta", "stop"], "hace lo de siempre (%r)" % llamadas)

print("── fichero de excepciones roto → fail-closed ──")
os.environ["BTP_CR_EXCEPCIONES"] = os.path.join(tmp, "no_existe.json")
check(codigo_rojo.excepcion(REAL, DET) is None, "sin fichero, no hay excepción: dispara")

print("\n" + ("✅ CODIGO ROJO EXCEPCIONES EN VERDE" if not fallos else "❌ %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
