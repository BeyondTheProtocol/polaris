#!/usr/bin/env python3
"""tools/radar_navegador.py — el carril de los registros que NO tienen API (18-sep-2026).

Por qué existe: tres registros que importan para su caso NO se pueden barrer con `urllib`, y
fingir que sí sería peor que no mirarlos:

  · **CTIS** (registro de ensayos de la UE) — SPA Angular. Es donde vive ONA-255 y donde se ven
    los ensayos europeos ANTES de que aparezcan (o sin que aparezcan nunca) en ClinicalTrials.gov.
  · **ChiCTR** (registro de ensayos chino) — su buscador responde 405 sin navegador; la portada sí.
  · **CDE / NMPA** (chinadrugtrials.org.cn, registro obligatorio de ensayos de fármacos en China)
    — abre por el proxy chino del VPS de Hong Kong, pero su buscador rechaza POST (403) y el
    índice se pinta por JavaScript.

Este módulo NO conduce el navegador: eso lo hace Claude en sesión con el MCP de Chrome (el lazo
autónomo tiene el navegador denegado a propósito, y así debe seguir). Lo que hace es la mitad
determinista y reutilizable: **recibir lo que el navegador ya ha extraído, normalizarlo,
deduplicarlo contra el mismo caché del radar diario y volcarlo al digest y a la cola de
verificación**. Así el trabajo del navegador no se pierde en el chat.

Uso:
  python3 tools/radar_navegador.py plan                    # qué abrir y qué buscar en cada registro
  python3 tools/radar_navegador.py ingerir --fuente ctis --fichero /tmp/ctis.json
  python3 tools/radar_navegador.py ingerir --fuente chictr --fichero /tmp/chictr.json --dry
  python3 tools/radar_navegador.py estado                  # cuándo se barrió por última vez

Formato de entrada (JSON, lista de objetos). Campos aceptados, todos opcionales salvo `id`:
  {"id": "2025-522707-26-00", "fecha": "15/09/2026", "titulo": "...", "cond": "...", "loc": "..."}

El estado del último barrido se guarda en tools/state/radar_ned/navegador.json, y la brújula
avisa cuando pasa de PERIODO_DIAS sin mirarse (eso sí es automatizable: recordar, no navegar).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
sys.path.insert(0, os.path.join(REPO, "tools"))

DIR_ESTADO = os.path.join(os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state"),
                          "radar_ned")
MARCA = os.path.join(DIR_ESTADO, "navegador.json")
PERIODO_DIAS = 7   # cada cuánto toca mirar estos registros a mano

FUENTES = {
    "ctis": {
        "nombre": "CTIS · registro de ensayos clínicos de la Unión Europea",
        "url": "https://euclinicaltrials.eu/ctis-public/search",
        "pasos": [
            "Abrir la URL y esperar a que cargue la SPA (tarda unos segundos).",
            "Escribir el término en el campo 'Contain all of these terms'.",
            "Pulsar el botón Search del final del formulario (no el Enter del campo).",
            "Ordenar por 'Decision date' DESC y leer la primera página.",
            "Términos de una pasada completa: 'breast cancer', 'FGFR4', '{{DIANA2}}', "
            "'neuroendocrine carcinoma', 'antibody drug conjugate'.",
        ],
        "ficha": "https://euclinicaltrials.eu/ctis-public/view/{id}",
    },
    "chictr": {
        "nombre": "ChiCTR · registro de ensayos clínicos de China",
        "url": "https://www.chictr.org.cn/searchproj.html",
        "pasos": [
            "Abrir la URL (carga desde España; el buscador NO responde a GET/POST desde el VPS).",
            "Rellenar el campo de enfermedad o título y pulsar buscar en la propia página.",
            "Términos: 'breast cancer', '乳腺癌', 'neuroendocrine', 'FGFR4', '{{DIANA2}}'.",
            "Anotar el número ChiCTR (ChiCTRxxxxxxxxxx), la fecha de registro y el estado.",
        ],
        "ficha": "https://www.chictr.org.cn/showprojEN.html?proj={id}",
    },
    "cde": {
        "nombre": "CDE / NMPA · registro obligatorio de ensayos de fármacos en China",
        "url": "http://www.chinadrugtrials.org.cn/clinicaltrials.prosearch.dhtml",
        "pasos": [
            "Carga desde España con el navegador (verificado el 18-sep-2026); no hace falta proxy.",
            "OJO (cazado el 18-sep-2026): el buscador NO reacciona a form_input — hay que hacer "
            "click en el campo, TECLEAR el término y pulsar Return. Si no, devuelve los últimos "
            "registros del país entero (linfoma, artritis...) y parece que ha buscado.",
            "Buscar por indicación: '乳腺癌' ({{DIAGNOSTICO}}), y por diana: 'FGFR4', '{{DIANA2}}', 'TROP2'.",
            "Anotar el registro CTRxxxxxxxx, el fármaco, el promotor y la fase.",
        ],
        "ficha": "http://www.chinadrugtrials.org.cn/clinicaltrials.searchlistdetail.dhtml?id={id}",
    },
}

MAX_TITULO = 200


def _limpia(t):
    if not isinstance(t, str):
        return ""
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r"[`*_\[\]<>|]", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()[:MAX_TITULO]


def _hoy():
    return datetime.now().date().isoformat()


def lee_marca():
    try:
        with open(MARCA) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def guarda_marca(d):
    os.makedirs(DIR_ESTADO, exist_ok=True)
    tmp = MARCA + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MARCA)


def dias_desde(fuente):
    """Días desde el último barrido de esa fuente. None si nunca se hizo."""
    m = lee_marca().get(fuente, {})
    if not m.get("fecha"):
        return None
    try:
        d = datetime.fromisoformat(m["fecha"]).date()
    except ValueError:
        return None
    return (datetime.now().date() - d).days


def pendientes():
    """Fuentes que llevan sin mirarse más de PERIODO_DIAS (o nunca). Lo usa la brújula."""
    out = []
    for f in FUENTES:
        d = dias_desde(f)
        if d is None or d >= PERIODO_DIAS:
            out.append((f, d))
    return out


def cmd_plan(a):
    print("CARRIL DE NAVEGADOR — registros sin API. Lo conduce Claude en sesión con Chrome MCP;\n"
          "el lazo autónomo tiene el navegador denegado a propósito y así debe seguir.\n")
    for clave, f in FUENTES.items():
        d = dias_desde(clave)
        estado = "NUNCA barrido" if d is None else f"hace {d} día(s)"
        print(f"== {clave.upper()} — {f['nombre']}  [{estado}]")
        print(f"   {f['url']}")
        for i, p in enumerate(f["pasos"], 1):
            print(f"   {i}. {p}")
        print()
    print("Al terminar cada fuente, volcar lo extraído:")
    print("   python3 tools/radar_navegador.py ingerir --fuente <ctis|chictr|cde> --fichero x.json")
    return 0


def cmd_estado(a):
    m = lee_marca()
    if not m:
        print("el carril de navegador no se ha corrido nunca")
        return 1
    for clave in FUENTES:
        d = dias_desde(clave)
        info = m.get(clave, {})
        print(f"{clave:7} · {'nunca' if d is None else f'hace {d} d'} · "
              f"último {info.get('fecha', '—')} · {info.get('n', 0)} ítems · "
              f"{info.get('nuevos', 0)} nuevos · términos: {', '.join(info.get('terminos') or []) or '—'}")
    pend = pendientes()
    if pend:
        print("PENDIENTE de barrer: " + ", ".join(c for c, _ in pend))
    return 0


def cmd_ingerir(a):
    if a.fuente not in FUENTES:
        print(f"fuente desconocida: {a.fuente}")
        return 2
    try:
        with open(a.fichero) as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"no puedo leer {a.fichero}: {e.__class__.__name__}")
        return 2
    if isinstance(datos, dict):
        datos = datos.get("items") or datos.get("resultados") or []
    if not isinstance(datos, list):
        print("el JSON debe ser una lista de objetos (o {items: [...]})")
        return 2

    import radar_ned_diario as rn
    f = FUENTES[a.fuente]
    visto = rn.carga_visto()
    hits, nuevos_uids = [], set()
    for it in datos:
        if not isinstance(it, dict):
            continue
        rid = _limpia(str(it.get("id") or it.get("ref") or ""))
        if not rid:
            continue
        uid = f"{a.fuente}:{rid}"
        if uid in visto or uid in nuevos_uids:
            continue
        nuevos_uids.add(uid)
        cond = _limpia(it.get("cond") or it.get("condicion") or "")
        loc = _limpia(it.get("loc") or it.get("paises") or "")
        titulo = _limpia(it.get("titulo") or it.get("title") or rid)
        if cond:
            titulo = f"{titulo} — {cond}"
        if loc:
            titulo = f"{titulo} [{loc[:120]}]"
        hits.append({
            "uid": uid,
            "tipo": "ensayo",
            "titulo": titulo[:MAX_TITULO],
            "fecha": _limpia(it.get("fecha") or it.get("dec") or ""),
            "ref": rid,
            "url": f["ficha"].format(id=rid),
            "fuente": f["nombre"],
            "flags": rn._flags(titulo),
        })

    print(f"{f['nombre']}: {len(datos)} leídos · {len(hits)} nuevos (el resto ya estaba visto)")
    for h in hits[:40]:
        print(f"  - {h['fecha'] or 's/f'} · {h['titulo'][:110]}")
    if a.dry:
        print("--dry: no escribo nada.")
        return 0

    # se vuelca como un tema mas del radar del dia, con prioridad ALTA: lo que sale de estos
    # registros no lo trae ninguna otra fuente, asi que no se puede quedar fuera de la cola.
    res = {
        "fecha": _hoy(),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ventana": {"desde": _hoy(), "hasta": _hoy()},
        "nuevos": len(hits),
        "alta_con_novedad": [a.fuente] if hits else [],
        "triado": False,
        "temas": [{"clave": a.fuente, "titulo": f"NAVEGADOR · {f['nombre']}", "prio": "alta",
                   "cn": a.fuente in ("chictr", "cde"), "hits": hits, "errores": []}],
    }
    ruta = rn.escribe_digest(res)
    rn.guarda_visto(visto | nuevos_uids)
    n_cola, total_cola, _ = rn.encola(res)
    m = lee_marca()
    m[a.fuente] = {"fecha": _hoy(), "n": len(datos), "nuevos": len(hits),
                   "terminos": a.terminos.split(",") if a.terminos else []}
    guarda_marca(m)
    print(f"digest → {ruta}")
    print(f"cola de verificación: +{n_cola} · {total_cola} pendientes")
    return 0


def main():
    p = argparse.ArgumentParser(description="Carril de registros sin API (CTIS, ChiCTR, CDE)")
    p.add_argument("cmd", choices=["plan", "ingerir", "estado"])
    p.add_argument("--fuente", choices=sorted(FUENTES))
    p.add_argument("--fichero", help="JSON extraído del navegador")
    p.add_argument("--terminos", help="términos buscados, separados por coma (para el registro)")
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    return {"plan": cmd_plan, "ingerir": cmd_ingerir, "estado": cmd_estado}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
