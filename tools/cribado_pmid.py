#!/usr/bin/env python3
"""tools/cribado_pmid.py — PRIMERA PASADA barata sobre literatura publicada, por PMID.

Por qué existe: la primera lectura de un resumen de PubMed (¿de qué va?, ¿cuántos pacientes?,
¿qué diana?) la hacía Claude, que es el modelo caro, porque el borde no deja salir a un modelo
abierto ningún texto con un marcador clínico. Un resumen PUBLICADO no es un dato de {{TITULAR}}, pero
por contenido no se distingue. Esta herramienta entra por la puerta del borde que decide por
PROCEDENCIA (`borde.egress_literatura`): aquí solo se aceptan PMIDs y una tarea de una lista
fija; el resumen lo descarga el borde del registro de NLM. No hay por dónde pasar texto libre.

Qué devuelve: por cada PMID, la ficha que saca el modelo abierto MÁS el nivel de evidencia que da
`tier_evidencia.py` desde el registro (determinista, sin modelo). El nivel de evidencia que vale
es el del registro; el del modelo va al lado solo para ver si discrepan.

⚠️ SIN VERIFICAR: la ficha es la lectura de un modelo abierto de gama «flash». Sirve para
DESCARTAR y ORDENAR qué leer, nunca para afirmar nada del artículo ni para decidir nada clínico.
Lo que sobreviva al cribado lo lee y lo verifica Claude contra la fuente.

Uso:
  python3 tools/cribado_pmid.py 35665782 33882206
  python3 tools/cribado_pmid.py --model glm5.3-flash --json 35665782
  python3 tools/cribado_pmid.py --tareas            # la lista fija de tareas
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde            # noqa: E402 — la única puerta de egress
import nan              # noqa: E402
import tier_evidencia   # noqa: E402

DESTINO = "nan"
AVISO = "primera pasada de un modelo abierto, SIN VERIFICAR: sirve para ordenar qué leer, no para afirmar"

# La lista FIJA de tareas y sus instrucciones viven en el borde (`borde.TAREAS_LITERATURA`): aquí
# solo se elige una clave. El prompt que sale lo compone el borde, no esta herramienta.
TAREAS = borde.TAREAS_LITERATURA


def _json_de(txt):
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def cribar(pmid, *, tarea="ficha", model=None):
    """Una ficha por PMID. Nunca lanza: lo que falle queda dicho en `error`."""
    out = {"pmid": str(pmid), "tarea": tarea, "modelo": model or nan.DEFAULT_MODEL,
           "estado": AVISO, "ficha": None, "tier_registro": None, "error": None}
    if tarea not in TAREAS:
        out["error"] = "tarea desconocida (las válidas: %s)" % ", ".join(sorted(TAREAS))
        return out
    # Una sola llamada: la puerta del borde y el envío van juntos dentro del cliente de NaN, así
    # que esta herramienta nunca tiene en la mano un prompt que pueda mandar por su cuenta.
    r = nan.enviar_literatura(out["modelo"], pmid, tarea)
    if r["error"] is nan.BLOQUEADO:
        out["error"] = "el borde no lo deja salir: %s" % r["motivo"]
        return out
    out["titulo"] = r["titulo"]
    try:
        t = tier_evidencia.clasifica_pmid(str(pmid), fetch=lambda _p: r["xml"])
        out["tier_registro"] = t.get("tier") if isinstance(t, dict) else None
    except Exception as e:
        out["tier_registro"] = "error: %r" % e
    if r["error"]:
        out["error"] = r["error"]
        return out
    texto = r["texto"]
    out["ficha"] = _json_de(texto)
    if out["ficha"] is None:
        out["error"] = "el modelo no devolvió JSON"
        out["crudo"] = (texto or "")[:400]
    return out


def main(argv):
    if "--tareas" in argv:
        print("\n".join(sorted(TAREAS))); return 0
    en_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    model, tarea = None, "ficha"
    for flag in ("--model", "--tarea"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                print("falta el valor de %s" % flag); return 2
            if flag == "--model":
                model = argv[i + 1]
            else:
                tarea = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__.split("Uso:")[1].strip()); return 2
    malos = [a for a in argv if not re.match(r"^[1-9]\d{0,8}$", a)]
    if malos:
        print("Solo se aceptan PMIDs (dígitos). No válido: %s" % ", ".join(m[:20] for m in malos))
        return 2
    res = [cribar(p, tarea=tarea, model=model) for p in argv]
    if en_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("⚠️ " + AVISO)
        for r in res:
            if r["error"]:
                print("  %s  ✗ %s" % (r["pmid"], r["error"]))
            else:
                f = r["ficha"]
                print("  %s  registro=%s · modelo=%s · n=%s · mama-NE=%s · diana=%s\n      %s"
                      % (r["pmid"], r["tier_registro"], f.get("tier"), f.get("n_patients"),
                         f.get("ne_breast"), f.get("main_target"), (r.get("titulo") or "")[:90]))
    return 1 if any(r["error"] for r in res) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
