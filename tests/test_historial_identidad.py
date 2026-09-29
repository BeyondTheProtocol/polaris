#!/usr/bin/env python3
"""test_historial_identidad.py — la identidad se comprueba al ENTRAR en el historial (29-sep-2026).

Antes solo se comprobaba al leer: un informe de otra paciente entraba en su historial y el RAG lo
servía como suyo. Con ficheros falsos y el veredicto simulado (no se toca ningún documento real):
  · otro_paciente → no se copia, queda en `rechazados_identidad` y el aviso lo dice
  · ambiguo       → entra, pero en `dudosos` para que lo confirme una persona
  · coincide      → entra y deja un evento en `_eventos.jsonl` (solo metadatos)
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="hist_id_")
os.environ["BTP_HISTORIAL"] = os.path.join(_TMP, "historial")
os.makedirs(os.environ["BTP_HISTORIAL"], exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import historial as h  # noqa: E402
import historial_sync as hs  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


origen = os.path.join(_TMP, "entrada")
os.makedirs(origen)
casos = {"ajeno.pdf": "otro_paciente", "dudoso.pdf": "ambiguo", "suyo.pdf": "coincide"}
rutas = []
for nombre in casos:
    r = os.path.join(origen, nombre)
    open(r, "wb").write(("contenido distinto %s" % nombre).encode())
    rutas.append(r)

h.RAIZ = os.environ["BTP_HISTORIAL"]
h._candidatos = lambda _rutas, _excl=None: (list(rutas), [])
h._archivados = lambda: {}
h._texto_de = lambda p: ("Informe 2026-09-01 texto de prueba", False)
h._identidad = lambda txt, p: casos[os.path.basename(p)]

res = h.ingerir([origen], apply=True, carpeta_forzada="analitica")
escritos = [d["origen"] for d in res["documentos"]]
ok(not any(p.endswith("ajeno.pdf") for p in escritos), "⭐ otro_paciente NO entra en el historial")
ok(any(r["origen"].endswith("ajeno.pdf") for r in res["rechazados_identidad"]),
   "otro_paciente queda en rechazados_identidad")
ok(any(d["origen"].endswith("dudoso.pdf") for d in res["dudosos"]
       if any("identidad" in m for m in d.get("motivos", []))), "ambiguo entra, marcado como dudoso")
ok(any(p.endswith("suyo.pdf") for p in escritos), "coincide entra")
ok(res["escritos"] == 2, "se copian 2 de 3 (%d)" % res["escritos"])
copiados = [f for _r, _d, fs in os.walk(h.RAIZ) for f in fs if f.endswith(".pdf")]
ok(len(copiados) == 2, "en disco hay 2 PDF, no el ajeno (%d)" % len(copiados))

ev = os.path.join(h.RAIZ, "_eventos.jsonl")
lineas = [json.loads(l) for l in open(ev)] if os.path.exists(ev) else []
ok(len(lineas) == 2 and all(e["tipo"] == "informe" for e in lineas),
   "⭐ cada informe nuevo deja un evento (%d)" % len(lineas))
ok(all(set(e) <= {"ts", "tipo", "carpeta", "fichero", "fecha", "centro", "sha", "identidad"}
       for e in lineas), "el evento lleva solo metadatos, no contenido")

# El aviso nombra los rechazados, también si no entró ninguno
enviados = []


class _S:
    @staticmethod
    def report_to_titular(texto, **_k):
        enviados.append(texto)
        return {"delivered": True}


sys.modules["salida"] = _S
hs.avisar({"ingerido": {"documentos": [], "dudosos": [], "rechazados_identidad": [{"origen": "x"}]},
           "drive": {}})
ok(enviados and "NO entran" in enviados[0], "⭐ el aviso dice que hay documentos que no entran")

if _fail:
    print("❌ test_historial_identidad: %d fallo(s)" % _fail)
    sys.exit(1)
print("✅ test_historial_identidad: lo ajeno no entra, lo ambiguo se marca, lo nuevo deja evento")
