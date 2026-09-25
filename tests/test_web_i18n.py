#!/usr/bin/env python3
"""test_web_i18n.py — las dos lenguas de helptitular.com dicen lo mismo y no se rompen.

NORMA: `feedback-web-workflow` (clase BLOQUEO) — «rama → PR → preview de Netlify → {{TITULAR}}
revisa y fusiona». El registro proponía dos mecanismos: «hook PreToolUse bloquea push/merge a
main sin OK» (eso ya lo hace `.claude/hooks/egreso_guard.py`, de la norma
`feedback-modo-build-enruta-no-ejecuta`) y «test i18n», que es este fichero.

QUÉ VIGILA, Y POR QUÉ CADA COSA:
  1. Los dos locales tienen EXACTAMENTE las mismas claves. Una clave que existe solo en `es`
     sale en blanco —o cruda— para quien entra en inglés. La web pide dinero a gente de fuera:
     un hueco ahí se ve.
  2. Ningún valor vacío.
  3. Los PLACEHOLDERS (`{op}`, `{lead}`, `{age}`…) coinciden entre las dos lenguas. El
     componente pasa los slots por NOMBRE: si `es` dice `{neuroendocrino}` y `en` dice
     `{neuroendocrine}`, en inglés no hay slot que case y el hueco se ve en la página.

ES UN TRINQUETE, NO UNA FOTO LIMPIA. Hoy hay UN desparejado real y está listado abajo con su
arreglo. El test pasa con ese y solo ese; cualquier desparejado NUEVO lo pone rojo. Listarlo es
la forma honesta: borrarlo de la lista sin arreglarlo sería fingir que no está.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import re  # noqa: E402

WEB = _os.environ.get("BTP_WEB_REPO") or _os.path.expanduser("~/projects/titular-{{APELLIDO}}-case")
LOCALES = _os.path.join(WEB, "i18n", "locales")
SKIP = 77

# Desparejado REAL pendiente de su OK (18-sep-26). `app/pages/index.vue` pasa los slots
# `#luminal` y `#neuroendocrino`; `en.json` escribe `{neuroendocrine}`, que no casa con ningún
# slot → en la home EN ese hueco no se rellena. Arreglo de una palabra: en `en.json`, cambiar
# `{neuroendocrine}` por `{neuroendocrino}` (o añadir el slot `#neuroendocrine` en el .vue).
# No se arregla desde aquí: es copy publicado del repo web y eso lo firma ella
# (`feedback-no-tocar-copy-web-sin-ok`).
DESPAREJADOS_CONOCIDOS = {"home.s3_summary"}

if not _os.path.isdir(LOCALES):
    print("⏭️  saltado: no está el repo web (%s). Es otro repo, no viaja con éste." % WEB)
    raise SystemExit(SKIP)


def _aplana(o, pre=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_aplana(v, "%s.%s" % (pre, k) if pre else str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(_aplana(v, "%s[%d]" % (pre, i)))
    else:
        out[pre] = o
    return out


def _cargar(lang):
    with open(_os.path.join(LOCALES, "%s.json" % lang), encoding="utf-8") as fh:
        return _aplana(json.load(fh))


es, en = _cargar("es"), _cargar("en")
_RE_PH = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
fallos = 0
casos = []


def check(desc, ok, detalle=""):
    global fallos
    casos.append((desc, ok, detalle))
    if not ok:
        fallos += 1


solo_es = sorted(set(es) - set(en))
solo_en = sorted(set(en) - set(es))
check("los dos locales tienen las mismas claves (%d)" % len(es), not solo_es and not solo_en,
      ("faltan en EN: %s" % ", ".join(solo_es[:5])) if solo_es else
      (("faltan en ES: %s" % ", ".join(solo_en[:5])) if solo_en else ""))

vacias = sorted(k for d in (es, en) for k, v in d.items() if isinstance(v, str) and not v.strip())
check("ningún valor vacío", not vacias, ", ".join(vacias[:5]))

desparejados = set()
for k in set(es) & set(en):
    if not (isinstance(es[k], str) and isinstance(en[k], str)):
        continue
    if set(_RE_PH.findall(es[k])) != set(_RE_PH.findall(en[k])):
        desparejados.add(k)
nuevos = sorted(desparejados - DESPAREJADOS_CONOCIDOS)
check("sin placeholders desparejados NUEVOS", not nuevos, ", ".join(nuevos[:5]))

arreglados = sorted(DESPAREJADOS_CONOCIDOS - desparejados)
check("la lista de desparejados conocidos está al día", not arreglados,
      "ya arreglado(s), quítalo(s) de DESPAREJADOS_CONOCIDOS: %s" % ", ".join(arreglados))

for desc, ok, det in casos:
    print(("  ✅ " if ok else "  ❌ ") + desc + (("\n       " + det) if det and not ok else ""))
if desparejados & DESPAREJADOS_CONOCIDOS:
    print("\n  ⚠️  deuda conocida (no falla, pero está rota en producción):")
    for k in sorted(desparejados & DESPAREJADOS_CONOCIDOS):
        print("       %s — es=%s · en=%s" % (k, sorted(set(_RE_PH.findall(es[k]))),
                                             sorted(set(_RE_PH.findall(en[k])))))
print()
print("RESULTADO web_i18n: %d de %d OK (%d claves por idioma)" % (len(casos) - fallos, len(casos), len(es)))
print("✅ LAS DOS LENGUAS DICEN LO MISMO" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
