#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reglas_repetidas.py — detecta la norma que {{TITULAR}} ha tenido que repetir.

LA SEÑAL QUE NADIE MIRABA
-------------------------
`cosecha_correcciones.py` busca lecciones NUEVAS y descarta las correcciones que ya
tienen memoria escrita. Pero esas descartadas son la señal más valiosa del sistema:
si {{TITULAR}} repite una corrección que YA está en memoria, la memoria no está fallando
por contenido, está fallando de SITIO. La lección existe y aun así no llega.

De las 286 memorias, 22 llevan escrito en el propio cuerpo que hubo que repetirlas
(«me lo ha tenido que repetir 3 veces», «ha pasado varias veces»). Eso se detectaba
leyéndolas a mano, o sea: no se detectaba.

QUÉ HACE
  1. Relee los transcritos buscando correcciones que SÍ casan con una memoria existente.
  2. Las agrupa por memoria y cuenta en cuántos DÍAS DISTINTOS se repitió.
  3. Suma las memorias que ya se autodeclaran repetidas.
  4. PROPONE subirlas de capa: memoria → regla `paths:` → constitución.

QUÉ NO HACE
  · No escribe ni una memoria, ni toca CLAUDE.md. Solo propone, y decide el agente de
    auto-mejora (o {{TITULAR}}). «Memoria = superficie de ataque»: nada durable se escribe
    solo desde un texto leído.
  · No abre red. Determinista, local, $0.

Uso:
  python3 tools/reglas_repetidas.py                 # informe
  python3 tools/reglas_repetidas.py --dias 30       # ventana (def. 45)
  python3 tools/reglas_repetidas.py --json          # para la rutina de auto-mejora
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

import cosecha_correcciones as cc   # noqa: E402 — reusamos su lectura de transcritos
import memoria_radar as mr          # noqa: E402 — y su ranking para atribuir la memoria

# Marcas con las que una memoria confiesa que hubo que repetirla. Deliberadamente
# ESTRECHAS: la primera versión casaba cualquier «repetir» y sacaba 39 memorias, entre
# ellas «no repetir disclaimers», que habla de otra cosa. Una lista de 39 no se lee;
# una de 8 se actúa. Aquí solo entran expresiones donde el sujeto de la repetición es
# {{TITULAR}} corrigiéndome.
_AUTODECLARA = re.compile(
    r"((me|se) lo (ha |han |tuvo que |ha tenido que )?repetid\w*"
    r"|ha tenido que (repetir|dec[ií]r)\w*"
    r"|repetid\w+ (\d+|dos|tres|varias|muchas) ve(ces|z)"
    r"|(\d+|dos|tres|varias|muchas) ve(ces|z) (me|que me|se me|lo)"
    r"|ya te lo (dije|hab[ií]a dicho)"
    r"|te lo he (dicho|repetido)"
    r"|ha pasado (varias veces|ya varias|más de una vez|mas de una vez)"
    r"|(volviste|volv[ií]) a (hacer|caer)"
    r"|vuelve a pasar"
    r"|se me olvid\w+ (otra vez|de nuevo|varias)"
    r"|regla repetida"
    r"|\(repetida?\b)", re.I)

# Dónde tiene sitio natural cada tema como regla `paths:` (para proponer destino).
_DESTINO_REGLA = [
    (re.compile(r"cola|dispatcher|schema", re.I), ".claude/rules/cola.md"),
    (re.compile(r"daemon|launchd|plist|salud|healthcheck", re.I), ".claude/rules/launchd-daemons.md"),
    (re.compile(r"deploy|fast-forward|air|polaris.*push", re.I), ".claude/rules/deploy-ff.md"),
    (re.compile(r"clinic|privado|deid|paciente|informe", re.I), ".claude/rules/clinico.md"),
    (re.compile(r"web|copy|hero|marca|dise|preview|netlify", re.I), ".claude/rules/marca-copy.md"),
    (re.compile(r"agente|comit|constelaci|dossier|contacto", re.I), ".claude/rules/agentes-comites.md"),
    (re.compile(r"tool|test|python|coste|modelo", re.I), ".claude/rules/tools-python.md"),
    (re.compile(r"memoria|regla|indice|recall", re.I), ".claude/rules/memoria-sistema.md"),
]


def _destino(slug, texto):
    """Dónde tendría sitio la regla. El SLUG manda sobre el cuerpo: el cuerpo de una
    memoria menciona medio sistema y proponía destinos absurdos (una regla sobre pedir
    ayuda al equipo acababa en `clinico.md` por la palabra «informe»)."""
    for patron, destino in _DESTINO_REGLA:
        if patron.search(slug):
            return destino
    for patron, destino in _DESTINO_REGLA:
        if len(patron.findall(texto)) >= 3:   # el cuerpo solo decide si insiste mucho
            return destino
    return "CLAUDE.md (constitución) o su rule: sin `paths:` evidente, decídelo a mano"


# `detectar_senales` es LAXO a propósito (para cazar lecciones nuevas marca cualquier
# «mejor», «quiero que», «no,»). Aquí eso no vale: con ventana de 60 días producía 37
# falsos positivos, mensajes de trabajo normales atribuidos a una memoria de proyecto.
# Un detector que grita así se ignora, y entonces no detecta nada. Filtro:
#   · la señal tiene que ser de RECHAZO o de REGLA (una preferencia no es una repetición),
#   · y además una regla explícita, o dos señales distintas.
_SENALES_FUERTES = {"rechazo", "regla"}
_REGLA_EXPLICITA = re.compile(
    r"\b(a partir de ahora|de ahora en adelante|regla|recu[eé]rda|siempre que|nunca)\b", re.I)


def _es_correccion_seria(texto, senales):
    fuertes = [s for s in senales if s in _SENALES_FUERTES]
    if not fuertes:
        return False
    return bool(_REGLA_EXPLICITA.search(texto)) or len(set(senales)) >= 2


def _correcciones_ya_cubiertas(dias):
    """Correcciones de {{TITULAR}} que YA casan con una memoria existente = repeticiones."""
    corte = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)
    corpus = cc._corpus_memorias()
    salida, vistos = [], set()
    try:
        ficheros = glob.glob(os.path.join(cc.PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    except Exception:
        ficheros = []
    for ruta in ficheros:
        try:
            fh = open(ruta, "r", encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with fh:
            for linea in fh:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    o = json.loads(linea)
                except Exception:
                    continue
                texto = cc._texto_de_mensaje(o)
                if not texto:
                    continue
                senales = cc.detectar_senales(texto)
                if not _es_correccion_seria(texto, senales):
                    continue
                dt = cc._ts_dt(o.get("timestamp"))
                if dt is None or dt < corte:
                    continue
                # La clave del asunto: NOS QUEDAMOS con lo que la cosecha descarta.
                if not cc._ya_en_memoria(texto, corpus):
                    continue
                clave = " ".join(texto.lower().split())[:160]
                if clave in vistos:
                    continue
                vistos.add(clave)
                salida.append({"fecha": dt.date().isoformat(), "texto": texto.strip()})
    return salida


def analizar(dias=45, minimo=2):
    """Devuelve {repetidas:[…], autodeclaradas:[…]} sin escribir nada."""
    # 1) Repeticiones vistas en los transcritos, atribuidas a su memoria.
    porc = {}
    for corr in _correcciones_ya_cubiertas(dias):
        try:
            top = mr.resucitar(dias_dormida=0, n=3, foco_texto=corr["texto"])
        except Exception:
            top = []
        # Una NORMA es una memoria `feedback`. Un `project-*` describe un asunto (el
        # viaje, el Observatorio), y ser el mejor resultado del ranking para un mensaje
        # de ese asunto no significa que {{TITULAR}} esté repitiendo una norma.
        top = [t for t in top if t["slug"].startswith("feedback-")]
        if not top:
            continue
        # Y la atribución tiene que ser sólida: que compartan vocabulario de verdad,
        # no solo ser lo mejor de una lista mala.
        slug = top[0]["slug"]
        cuerpo = next((d["body"] for d in mr._corpus() if d["slug"] == slug), "")
        claves = cc._keywords(corr["texto"])
        if claves:
            solape = sum(1 for k in claves if k in cuerpo.lower()) / len(claves)
            if solape < 0.4:
                continue
        entrada = porc.setdefault(slug, {"slug": slug, "dias": set(), "ejemplos": []})
        entrada["dias"].add(corr["fecha"])
        if len(entrada["ejemplos"]) < 3:
            entrada["ejemplos"].append(corr["texto"][:160])

    repetidas = []
    for slug, e in porc.items():
        if len(e["dias"]) < minimo:
            continue
        repetidas.append({
            "slug": slug,
            "veces": len(e["dias"]),
            "fechas": sorted(e["dias"]),
            "ejemplos": e["ejemplos"],
            "propuesta": _destino(slug, " ".join(e["ejemplos"])),
        })
    repetidas.sort(key=lambda x: -x["veces"])

    # 2) Memorias que ya llevan escrito que hubo que repetirlas.
    autodeclaradas = []
    for doc in mr._corpus():
        m = _AUTODECLARA.search(doc["body"])
        if not m:
            continue
        autodeclaradas.append({
            "slug": doc["slug"],
            "marca": m.group(0),
            "propuesta": _destino(doc["slug"], doc["body"][:400]),
        })
    autodeclaradas.sort(key=lambda x: x["slug"])
    return {"repetidas": repetidas, "autodeclaradas": autodeclaradas}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normas que {{TITULAR}} ha tenido que repetir.")
    ap.add_argument("--dias", type=int, default=45, help="ventana de transcritos (def. 45)")
    ap.add_argument("--minimo", type=int, default=2, help="días distintos para contar como repetida")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    res = analizar(dias=args.dias, minimo=args.minimo)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    rep, auto = res["repetidas"], res["autodeclaradas"]
    if not rep:
        print("✅ Ninguna norma repetida en los últimos %d días." % args.dias)
    else:
        print("🔁 Normas que {{TITULAR}} ha tenido que repetir (%d):\n" % len(rep))
        for r in rep:
            print("  [[%s]] — %d días distintos (%s)" % (r["slug"], r["veces"], ", ".join(r["fechas"])))
            print("      subirla a: %s" % r["propuesta"])
            for ej in r["ejemplos"]:
                print("      · «%s»" % ej)
            print()

    if auto:
        print("📌 Memorias que ya declaran en su cuerpo que hubo que repetirlas (%d):" % len(auto))
        for a in auto[:20]:
            print("  [[%s]] («%s») → %s" % (a["slug"], a["marca"], a["propuesta"]))
        if len(auto) > 20:
            print("  … y %d más" % (len(auto) - 20))
    print("\nEsto PROPONE, no escribe. Subir de capa lo decide auto-mejora o {{TITULAR}}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
