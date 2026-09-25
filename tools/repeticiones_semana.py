#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""repeticiones_semana.py — la cifra semanal de correcciones repetidas.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

POR QUÉ EXISTE
  Su propuesta P8 dice: antes de inyectar contexto a los subagentes, MEDIR qué se repite, y
  dar por hecho el arreglo solo cuando «la cifra semanal de correcciones repetidas existe y
  baja». `reglas_repetidas.py` ya encontraba normas repetidas, pero no dejaba una cifra por
  semana ni decía si ese turno lo hizo un subagente. Sin esas dos cosas no se puede comprobar
  si un hook SubagentStart sirve.

TRES SEÑALES, POR SEMANA ISO (no se suman a ciegas: cada una mide otra cosa)
  1. `explicita`  → {{TITULAR}} dice que se repite («ya te lo dije», «cuántas veces», «se te
                    olvida»). Regex estrecha a propósito: «otra vez» a secas es casi siempre
                    «hazlo otra vez» (medido: 6 de 6 falsos en 60 días).
  2. `ya_escrita` → corrección seria de {{TITULAR}} que casa con una memoria `feedback-*` que YA
                    existía. Reusa el filtro y la atribución de `reglas_repetidas.py`.
  → CIFRA = mensajes distintos con 1 o 2. Es la que tiene que bajar.
  3. `gate`       → pares (sesión, norma) que el gate de salida pilló en
                    `tools/state/gate_salida.jsonl`. Es la máquina cazándome a mí, no {{TITULAR}}
                    repitiendo: sirve de indicador adelantado, no entra en la cifra.

LA HIPÓTESIS DE CONTACTO, EN UN NÚMERO
  Para cada evento de 1 y 2 mira si, desde el mensaje anterior de {{TITULAR}} en esa sesión, se
  lanzó algún `Agent`/`Task`. `tras_subagente / cifra` es la parte de las repeticiones donde
  pudo trabajar alguien que nunca vio la corrección. Es una correlación, no una causa: dice
  «hubo subagente en ese turno», no «el subagente causó el fallo».

QUÉ NO HACE
  · No escribe memoria ni reglas. Con `--guardar` deja la serie en
    `tools/state/repeticiones_semana.json` de casa base; sin él, no escribe nada.
  · No abre red. Local, determinista, $0. Los transcritos llevan PII: no salen de aquí y la
    salida solo lleva 100 caracteres de ejemplo por evento (y ninguno con `--json --sin-texto`).

Uso:
  python3 tools/repeticiones_semana.py                 # 8 semanas, tabla
  python3 tools/repeticiones_semana.py --semanas 12 --json --sin-texto
  python3 tools/repeticiones_semana.py --guardar       # la rutina semanal
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

import cosecha_correcciones as cc   # noqa: E402 — lectura de transcritos y filtro «es {{TITULAR}}»
import reglas_repetidas as rr       # noqa: E402 — qué es una corrección seria
import memoria_radar as mr          # noqa: E402 — atribución a memoria

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
GATE_LOG = os.environ.get("BTP_GATE_LOG") or os.path.join(REPO, "tools", "state", "gate_salida.jsonl")
SALIDA = os.path.join(REPO, "tools", "state", "repeticiones_semana.json")

_EXPLICITA = re.compile(
    r"\bya te lo (he |hab[ií]a )?(dije|dicho|ped[ií]do|ped[ií]|expliqu[eé]|explicado)\b"
    r"|\bte lo he (dicho|repetido|pedido|explicado)\b"
    r"|\bcu[aá]ntas veces\b"
    r"|\bcomo ya te (dije|he dicho)\b"
    r"|\bte lo repito\b"
    r"|\b(vuelves|volviste|has vuelto) a (hacer|decir|poner|caer|olvidar|equivocar)\w*"
    r"|\bse te (ha |vuelve a |sigue )?olvid\w+"
    r"|\bno es la primera vez\b"
    r"|\botra vez lo mismo\b|\bsiempre lo mismo\b|\bmil veces\b"
    r"|\bno aprendes\b|\blo guardo,? no volver[aá] a pasar\b",
    re.I)

_AGENTE = ("Agent", "Task")


def _semana(dt):
    return dt.strftime("%G-W%V")


def _atribuye(texto):
    """Slug de la memoria feedback con la que casa, o None. Mismo listón que reglas_repetidas."""
    try:
        top = mr.resucitar(dias_dormida=0, n=3, foco_texto=texto)
    except Exception:
        return None
    top = [t for t in top if t["slug"].startswith("feedback-")]
    if not top:
        return None
    slug = top[0]["slug"]
    cuerpo = next((d["body"] for d in mr._corpus() if d["slug"] == slug), "").lower()
    claves = cc._keywords(texto)
    if claves and sum(1 for k in claves if k in cuerpo) / len(claves) < 0.4:
        return None
    return slug


def _usa_agente(o):
    if o.get("type") != "assistant":
        return False
    c = (o.get("message") or {}).get("content")
    if not isinstance(c, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in _AGENTE
               for b in c)


def eventos_transcritos(corte):
    """Eventos de {{TITULAR}} repitiendo, con la marca de si hubo subagente en ese turno."""
    corpus = cc._corpus_memorias()
    fuera = []
    vistos = set()
    try:
        ficheros = glob.glob(os.path.join(cc.PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    except Exception:
        ficheros = []
    for ruta in ficheros:
        if os.sep + "subagents" + os.sep in ruta:   # el transcript del subagente no es {{TITULAR}}
            continue
        try:
            fh = open(ruta, "r", encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hubo_agente = False   # desde el último mensaje de {{TITULAR}} en ESTA sesión
        with fh:
            for linea in fh:
                try:
                    o = json.loads(linea)
                except Exception:
                    continue
                if _usa_agente(o):
                    hubo_agente = True
                    continue
                texto = cc._texto_de_mensaje(o)
                if not texto:
                    continue
                agente_previo, hubo_agente = hubo_agente, False
                dt = cc._ts_dt(o.get("timestamp"))
                if dt is None or dt < corte:
                    continue
                clave = " ".join(texto.lower().split())[:160]
                if clave in vistos:
                    continue
                tipos, slug = [], None
                if _EXPLICITA.search(texto):
                    tipos.append("explicita")
                senales = cc.detectar_senales(texto)
                if rr._es_correccion_seria(texto, senales) and cc._ya_en_memoria(texto, corpus):
                    slug = _atribuye(texto)
                    if slug:
                        tipos.append("ya_escrita")
                if not tipos:
                    continue
                vistos.add(clave)
                fuera.append({"fecha": dt.date().isoformat(), "semana": _semana(dt),
                              "tipos": tipos, "slug": slug, "tras_subagente": agente_previo,
                              "texto": texto.strip()[:100]})
    return fuera


def eventos_gate(corte):
    """(semana, sesión, norma) distintos que el gate de salida registró."""
    fuera, vistos = [], set()
    try:
        fh = open(GATE_LOG, "r", encoding="utf-8", errors="ignore")
    except Exception:
        return fuera
    with fh:
        for linea in fh:
            try:
                o = json.loads(linea)
                dt = datetime.datetime.fromtimestamp(float(o["ts"]), datetime.timezone.utc)
            except Exception:
                continue
            if dt < corte:
                continue
            clave = (_semana(dt), o.get("session_hash"), o.get("slug") or o.get("check"))
            if clave in vistos:
                continue
            vistos.add(clave)
            fuera.append({"semana": clave[0], "slug": clave[2]})
    return fuera


def serie(semanas=8, ahora=None):
    ahora = ahora or datetime.datetime.now(datetime.timezone.utc)
    lunes = (ahora - datetime.timedelta(days=ahora.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    corte = lunes - datetime.timedelta(weeks=semanas - 1)
    ev = eventos_transcritos(corte)
    gate = eventos_gate(corte)
    filas = {}
    for i in range(semanas):
        s = _semana(corte + datetime.timedelta(weeks=i))
        filas[s] = {"semana": s, "cifra": 0, "explicita": 0, "ya_escrita": 0,
                    "tras_subagente": 0, "gate": 0}
    for e in ev:
        f = filas.get(e["semana"])
        if not f:
            continue
        f["cifra"] += 1
        for t in e["tipos"]:
            f[t] += 1
        if e["tras_subagente"]:
            f["tras_subagente"] += 1
    for g in gate:
        if g["semana"] in filas:
            filas[g["semana"]]["gate"] += 1
    return {"generado": ahora.isoformat(timespec="seconds"), "semanas": list(filas.values()),
            "eventos": ev}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cifra semanal de correcciones repetidas.")
    ap.add_argument("--semanas", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sin-texto", action="store_true", help="no sacar el ejemplo de cada evento")
    ap.add_argument("--guardar", action="store_true", help="escribe la serie en tools/state/ de casa base")
    a = ap.parse_args(argv)

    res = serie(a.semanas)
    if a.sin_texto or a.guardar:
        for e in res["eventos"]:
            e.pop("texto", None)
    if a.guardar:
        os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
        tmp = SALIDA + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, SALIDA)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    print("Semana     cifra  explícita  ya_escrita  tras_subagente  gate")
    for f in res["semanas"]:
        print("%-9s  %5d  %9d  %10d  %14d  %4d" % (
            f["semana"], f["cifra"], f["explicita"], f["ya_escrita"], f["tras_subagente"], f["gate"]))
    if not (a.sin_texto or a.guardar) and res["eventos"]:
        print("\nEventos (cifra):")
        for e in res["eventos"]:
            print("  %s %-22s %s%s  «%s»" % (e["fecha"], "+".join(e["tipos"]), e["slug"] or "",
                                            "  [tras subagente]" if e["tras_subagente"] else "",
                                            e["texto"].replace("\n", " ")))
    print("\ncifra = mensajes de {{TITULAR}} repitiendo (explícita o norma ya escrita). gate = aviso "
          "adelantado, no entra en la cifra.")
    if a.guardar:
        print("Guardado: %s" % SALIDA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
