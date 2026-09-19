#!/usr/bin/env python3
"""tools/onco.py — OnCo (onco.cc), el grafo abierto de oncología, consultado EN LOCAL.

POR QUÉ EXISTE (19-sep-2026). Guardado de X de @judegomila: OnCo es un grafo público de oncología
(12.360 registros: cánceres, fármacos, dianas, ensayos, ideas, cuellos de botella, papers clave),
cada registro con fuente primaria y fecha de verificación, y «no data» en vez de inventar. Sirve
de mapa para el radar y el comité, NO de evidencia: todo lo que se cite se abre en su fuente.

CÓMO (y por qué así). OnCo publica su API como ficheros estáticos. Su README sugiere `npx onco` y
`npx -y onco-mcp`, pero el 19-sep `onco-mcp` NO existía en npm y `onco` en npm es OTRO paquete
(github.com/wagerfield/onno): instalar por npx habría ejecutado código de un tercero ajeno. Aquí no
se instala nada: se baja el volcado `all.ndjson` y se busca en local, así las consultas no salen
de la máquina (egress cero de lo que buscamos; solo sale el GET del volcado público).

Licencia de los datos: CC BY-NC 4.0, uso individual/educativo con atribución «Data from OnCo
(https://onco.cc)». Todo lo que se derive de aquí y se publique lleva esa atribución.

Uso:
  python3 tools/onco.py sync                      # baja el volcado si hay build nuevo (guarda el anterior)
  python3 tools/onco.py buscar "fgfr1 ccnd1" [--tipo trial] [-n 15]
  python3 tools/onco.py ficha <id>                # un registro: tldr, fecha, fuentes
  python3 tools/onco.py novedades [--filtro "breast,neoantigen"] [--todo]
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request

REPO_VIVO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
DIR = os.environ.get("BTP_ONCO_DIR") or os.path.join(REPO_VIVO, "_cajita", "onco")
BASE = "https://onco.cc/api/v1/"
ATRIBUCION = "Data from OnCo (https://onco.cc), CC BY-NC 4.0"
TIMEOUT = 120
# Filtro por defecto de `novedades`: lo que toca la ruta de {{TITULAR}} (mama HR+, dianas, vacuna).
FILTRO_NED = ("breast", "hr+", "er+", "hr-positive", "estrogen", "oestrogen", "cdk4", "fgfr",
              "ccnd1", "neoantigen", "cancer vaccine", "personalized vaccine", "mrna vaccine",
              "trop2", "her2-low", "serd", "esr1", "pik3ca", "akt", "radioligand")


def _ruta(nombre):
    return os.path.join(DIR, nombre)


def _get(url, destino=None):
    req = urllib.request.Request(url, headers={"User-Agent": "polaris-onco/1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        if destino is None:
            return r.read()
        with open(destino, "wb") as f:
            shutil.copyfileobj(r, f)


def _meta_local(nombre="meta.json"):
    try:
        with open(_ruta(nombre)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cargar(nombre="all.ndjson"):
    """Registros del volcado local (lista de dicts). Vacío si no hay volcado."""
    ruta = _ruta(nombre)
    if not os.path.exists(ruta):
        return []
    with open(ruta) as f:
        return [json.loads(l) for l in f if l.strip()]


def sync(forzar=False):
    """Baja el volcado si el build remoto es distinto del local. Rota el actual a *.prev."""
    os.makedirs(DIR, exist_ok=True)
    meta = json.loads(_get(BASE + "meta.json"))
    local = _meta_local()
    if not forzar and local.get("built") == meta.get("built") and os.path.exists(_ruta("all.ndjson")):
        return {"cambio": False, "built": meta.get("built")}
    tmp = _ruta("all.ndjson.tmp")
    _get(BASE + "all.ndjson", tmp)
    n = sum(1 for l in open(tmp) if l.strip())
    if n == 0:
        os.remove(tmp)
        raise RuntimeError("volcado vacío: no se sustituye el local")
    if os.path.exists(_ruta("all.ndjson")):
        os.replace(_ruta("all.ndjson"), _ruta("all.prev.ndjson"))
        if os.path.exists(_ruta("meta.json")):
            os.replace(_ruta("meta.json"), _ruta("meta.prev.json"))
    os.replace(tmp, _ruta("all.ndjson"))
    with open(_ruta("meta.json"), "w") as f:
        json.dump(meta, f)
    return {"cambio": True, "built": meta.get("built"), "registros": n}


def _texto(r):
    partes = [r.get("id", ""), r.get("name", ""), r.get("tldr", ""), " ".join(r.get("aka") or []),
              " ".join(r.get("tags") or []), r.get("nct") or "", r.get("summary") or ""]
    return " ".join(p for p in partes if isinstance(p, str)).lower()


def buscar(registros, consulta, tipo=None, n=15):
    """Todos los términos deben aparecer. Puntúa más si están en nombre/aka que en el resumen."""
    terminos = [t for t in consulta.lower().split() if t]
    out = []
    for r in registros:
        if tipo and r.get("kind") != tipo:
            continue
        txt = _texto(r)
        if not all(t in txt for t in terminos):
            continue
        cabeza = (r.get("name", "") + " " + " ".join(r.get("aka") or [])).lower()
        out.append((sum(3 if t in cabeza else 1 for t in terminos), r))
    out.sort(key=lambda x: (-x[0], x[1].get("name", "")))
    return [r for _, r in out[:n]]


def novedades(actual, previo, filtro=FILTRO_NED):
    """Registros nuevos o con `asOf` cambiado respecto al volcado previo, filtrados por palabras."""
    antes = {r["id"]: r.get("asOf") for r in previo}
    out = []
    for r in actual:
        if r["id"] in antes and antes[r["id"]] == r.get("asOf"):
            continue
        if filtro and not any(k in _texto(r) for k in filtro):
            continue
        out.append(dict(r, _novedad="nuevo" if r["id"] not in antes else "actualizado"))
    return out


def _linea(r):
    extra = " · ".join(x for x in (r.get("nct"), r.get("phase") and "fase " + str(r["phase"]),
                                   r.get("status")) if x)
    return "[%s] %s — %s%s\n    %s" % (r.get("kind"), r.get("id"), r.get("name"),
                                       (" (%s)" % extra) if extra else "", (r.get("tldr") or "")[:220])


def main(argv=None):
    p = argparse.ArgumentParser(description="OnCo en local (onco.cc)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--forzar", action="store_true")
    b = sub.add_parser("buscar"); b.add_argument("consulta"); b.add_argument("--tipo")
    b.add_argument("-n", type=int, default=15)
    f = sub.add_parser("ficha"); f.add_argument("id")
    nv = sub.add_parser("novedades"); nv.add_argument("--filtro"); nv.add_argument("--todo", action="store_true")
    a = p.parse_args(argv)

    if a.cmd == "sync":
        try:
            res = sync(a.forzar)
        except Exception as e:  # red caída, JSON roto: se dice, no se inventa
            print("⚠️ OnCo sync falló: %s (se conserva el volcado local)" % e)
            return 1
        print(("✅ volcado nuevo: %(registros)s registros, build %(built)s" if res["cambio"]
               else "sin cambios (build %(built)s)") % res)
        return 0

    registros = cargar()
    if not registros:
        print("No hay volcado local: corre `python3 tools/onco.py sync`")
        return 1
    if a.cmd == "buscar":
        res = buscar(registros, a.consulta, a.tipo, a.n)
        for r in res:
            print(_linea(r))
        print("\n%d resultado(s) · %s" % (len(res), ATRIBUCION))
    elif a.cmd == "ficha":
        r = next((x for x in registros if x["id"] == a.id), None)
        if not r:
            print("No existe el id %r" % a.id)
            return 1
        print(_linea(r))
        print("    verificado por OnCo: %s · https://onco.cc%s" % (r.get("asOf") or "sin fecha", r.get("route", "")))
        for l in r.get("links") or []:
            print("    fuente: %s — %s" % (l.get("label"), l.get("url")))
        print(ATRIBUCION)
    elif a.cmd == "novedades":
        previo = cargar("all.prev.ndjson")
        if not previo:
            print("Sin volcado previo con el que comparar (hace falta un segundo sync con build nuevo).")
            return 0
        filtro = None if a.todo else (tuple(k.strip().lower() for k in a.filtro.split(",")) if a.filtro else FILTRO_NED)
        res = novedades(registros, previo, filtro)
        for r in res:
            print("(%s) %s" % (r["_novedad"], _linea(r)))
        print("\n%d novedad(es) · %s" % (len(res), ATRIBUCION))
    return 0


if __name__ == "__main__":
    sys.exit(main())
