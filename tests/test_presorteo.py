#!/usr/bin/env python3
"""test_presorteo.py — el presorteo ordena, nunca decide, y si algo falla no escribe nada.

25-sep-26 (P5, idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del
25-sep-2026). Fija: HALT, candado ocupado y Ollama cargado → no escribe; «no» al 91 % queda en
gris, nunca como ruido (umbral asimétrico); la bandeja de Vega queda intacta byte a byte; a
disco no va ni un título. Offline: modelo falso, estado en un directorio temporal.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class Falso:
    repo = "falso"

    def __init__(self, respuestas):
        self.r = respuestas

    def puntuar(self, contenido, opciones):
        for clave, v in self.r.items():
            if clave in contenido:
                return v[0], v[1], 1.0
        return None, 0.0, 1.0


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["BTP_STATE_DIR"] = d
        import importlib
        import _lock
        importlib.reload(_lock)
        import presorteo as p
        importlib.reload(p)
        os.makedirs(os.path.join(d, "tareas"))
        bandeja = {"candidatos": [
            {"titulo": "CANARIO-uno reservar tren", "veredicto": "dudosa"},
            {"titulo": "CANARIO-dos correo del hospital", "veredicto": "dudosa"},
            {"titulo": "CANARIO-tres jaja vale", "veredicto": "dudosa"},
            {"titulo": "CANARIO-cuatro ya decidido", "veredicto": "tarea"},
            {"titulo": "CANARIO-cinco misterio resuelto, llevo un power port", "veredicto": "dudosa"},
            {"titulo": "CANARIO-seis predisposición a pólipos en el colon", "veredicto": "dudosa"}]}
        with open(p.BANDEJA, "w") as fh:
            json.dump(bandeja, fh)
        antes = open(p.BANDEJA, "rb").read()
        falso = Falso({"CANARIO-uno": ("tarea", 0.90), "CANARIO-dos": ("no", 0.91),
                       "CANARIO-tres": ("no", 0.97), "CANARIO-cinco": ("no", 0.99),
                       "CANARIO-seis": ("no", 0.99)})
        p._ollama_cargado = lambda: False

        orig_h = p.borde.halted
        p.borde.halted = lambda: True
        check("HALT → no escribe", p.anotar(falso) is None and not os.path.exists(p.SALIDA))
        p.borde.halted = orig_h

        with p._lock.lock(p.CANDADO, timeout=5):
            check("candado tomado → no escribe", p.anotar(falso) is None and not os.path.exists(p.SALIDA))

        p._ollama_cargado = lambda: True
        check("Ollama cargado → no escribe", p.anotar(falso) is None and not os.path.exists(p.SALIDA))
        p._ollama_cargado = lambda: False

        check("--seco no escribe", p.anotar(falso, seco=True) is None and not os.path.exists(p.SALIDA))

        anot = p.anotar(falso)
        fr = {k: v["franja"] for k, v in anot["candidatos"].items()}
        check("solo los dudosos", len(fr) == 5)
        check("clínico al 99 % → gris, nunca ruido (port)",
              fr[p.huella("CANARIO-cinco misterio resuelto, llevo un power port")] == "gris")
        check("clínico al 99 % → gris, nunca ruido (colon)",
              fr[p.huella("CANARIO-seis predisposición a pólipos en el colon")] == "gris")
        check("«tarea» al 90 % → propuesta", fr[p.huella("CANARIO-uno reservar tren")] == "propuesta_tarea")
        check("«no» al 91 % → gris, nunca ruido", fr[p.huella("CANARIO-dos correo del hospital")] == "gris")
        check("«no» al 97 % → probable ruido", fr[p.huella("CANARIO-tres jaja vale")] == "probable_ruido")
        check("bandeja de Vega intacta", open(p.BANDEJA, "rb").read() == antes)
        check("ni un título a disco", "CANARIO" not in open(p.SALIDA).read())

        os.remove(p.SALIDA)

        class Revienta(Falso):
            def puntuar(self, *a):
                raise SystemExit("vigía")
        try:
            p.anotar(Revienta({}))
        except SystemExit:
            pass
        check("si revienta a mitad, no escribe", not os.path.exists(p.SALIDA))
        check("y suelta el candado", not p._lock.held(p.CANDADO))

    print("test_presorteo: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
