#!/usr/bin/env python3
"""tools/radar_navegador.py — el carril de los registros que NO tienen API (18-sep-2026).

Por qué existe: tres registros que importan para su caso NO se pueden barrer con `urllib`, y
fingir que sí sería peor que no mirarlos:

  · **CTIS** (registro de ensayos de la UE) — SPA Angular. Es donde vive ONA-255 y donde se ven
    los ensayos europeos ANTES de que aparezcan (o sin que aparezcan nunca) en ClinicalTrials.gov.
  · **ChiCTR** (registro de ensayos chino) — su WAF bloquea las IPs de datacenter (405, con proxy
    y sin él). Desde el 20-sep-2026 lo cubre **ICTRP** (trialsearch.who.int, portal de la OMS que
    importa el fichero de ChiCTR): ASP.NET con postback, se conduce con el Playwright del VPS
    (`radar_cn_vps.py ictrp perfil`) y `radar_ned_dia.sh` lo ingiere a diario con `--fuente ictrp`.
  · **CDE / NMPA** (chinadrugtrials.org.cn, registro obligatorio de ensayos de fármacos en China)
    — CORREGIDO 20-sep-2026: esto decía que hacía falta navegador porque el buscador ignora
    `form_input` y el índice se pinta por JavaScript, y ya no es la vía a usar. Verificado hoy,
    en sesión, con Python puro (sin navegador): dos peticiones (GET cookies + POST del
    formulario) bastan — `tools/cde_fetch.py` ya lo implementa y trae test. Sigue este carril
    de navegador SÓLO de respaldo si `cde_fetch.py` falla. Términos por PERFIL:
    `radar_cn_vps.TERMINOS_CDE`.

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

# Los términos del CDE viven en radar_cn_vps.py (fuente única); importarlos no exige Playwright
# porque ese módulo lo importa dentro de main(). Fail-soft: sin el módulo, lista vacía y se dice.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from radar_cn_vps import TERMINOS_CDE
except Exception:  # noqa: BLE001
    TERMINOS_CDE = ["(radar_cn_vps.TERMINOS_CDE no importable)"]

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
    "ictrp": {
        # AUTOMATIZADO desde el 20-sep-2026: radar_ned_dia.sh lo corre a diario por el VPS de
        # Hong Kong (radar_cn_vps.py ictrp perfil). Aquí queda el plan manual por si el VPS cae.
        "nombre": "ICTRP · portal OMS, espejo de ChiCTR (importó su fichero el 14-sep-2026)",
        "url": "https://trialsearch.who.int/AdvSearch.aspx",
        "pasos": [
            "Abre desde España y desde el VPS (HTTP 200 medido el 20-sep-2026). ASP.NET con postback.",
            "Condition: 'breast cancer' · Countries: China (seleccionar y pulsar >>) · "
            "Date of registration: últimos 14 días (dd/mm/yyyy) · Search.",
            "OJO (medido el 20-sep): el campo Intervention NO casa con los registros ChiCTR "
            "('breast cancer' x 'vaccine' x China = 0 aunque existan). Filtra por título tú.",
            "Segunda pasada, sin fecha, por intervención: " + ", ".join(
                ["neoantigen", "personalized vaccine", "mRNA vaccine", "tumor infiltrating lymphocyte",
                 "CDK2", "FGFR", "{{DIANA2}}", "TROP2", "antibody-drug conjugate"]) + ".",
            "Anotar Main ID (ChiCTR.........., NCT........), estado, fecha de registro y si hay resultados.",
        ],
        "ficha": "https://trialsearch.who.int/Trial2.aspx?TrialID={id}",
    },
    "chictr": {
        "nombre": "ChiCTR · registro de ensayos clínicos de China (BLOQUEADO a datacenter; ver ictrp)",
        "url": "https://www.chictr.org.cn/searchproj.html",
        "pasos": [
            "Su WAF devuelve 405 a las IPs de datacenter, con proxy y sin él (18 y 20-sep-2026). "
            "Desde un navegador de casa carga. El barrido diario lo cubre ICTRP (fuente 'ictrp').",
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
            "CORREGIDO 20-sep-2026: úsese primero `tools/cde_fetch.py` (POST directo, sin "
            "navegador, verificado hoy y con test) — este carril manual es sólo respaldo si "
            "esa tool falla.",
            "Si toca a mano: carga desde España con el navegador (verificado el 18-sep-2026); "
            "no hace falta proxy. Desde el VPS NO abre (curl 000 el 20-sep).",
            "OJO (cazado el 18-sep-2026, sólo aplica al navegador): el buscador NO reacciona a "
            "form_input — hay que hacer click en el campo, TECLEAR el término y pulsar Return. "
            "Si no, devuelve los últimos registros del país entero (linfoma, artritis...) y "
            "parece que ha buscado.",
            "Buscar por PERFIL, no por 乳腺癌 a secas (así entró un ensayo ALK de pulmón el 19-sep). "
            "Términos (radar_cn_vps.TERMINOS_CDE): " + " · ".join(TERMINOS_CDE) + ".",
            "Anotar el registro CTRxxxxxxxx, el fármaco, el promotor y la fase. La FICHA se pide "
            "por el id interno de 32 hex del listado, no por el código CTR (ver cde_fetch.py).",
        ],
        "ficha": "http://www.chinadrugtrials.org.cn/clinicaltrials.searchlistdetail.dhtml?id={id}",
    },
}
FUENTES_CN = ("ictrp", "chictr", "cde")

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
    tema = {"clave": a.fuente, "cn": a.fuente in FUENTES_CN}
    visto = rn.carga_visto()
    hits, nuevos_uids, errores = [], set(), []
    for it in datos:
        if not isinstance(it, dict):
            continue
        if it.get("error") and not it.get("id"):
            # el VPS reporta lo que NO pudo leer como un item {"error": ...}: se dice, no se calla
            errores.append(_limpia(str(it.get("error")))[:160])
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
        estado = _limpia(it.get("estado") or "")
        titulo = _limpia(it.get("titulo") or it.get("title") or rid)
        if estado:
            titulo = f"[{estado}] {titulo}"
        if cond:
            titulo = f"{titulo} — {cond}"
        if loc:
            titulo = f"{titulo} [{loc[:120]}]"
        h = {
            "uid": uid,
            "tipo": "ensayo",
            "titulo": titulo[:MAX_TITULO],
            "fecha": _limpia(it.get("fecha") or it.get("dec") or ""),
            "ref": rid,
            "url": f["ficha"].format(id=rid),
            "fuente": f["nombre"],
            "flags": rn._flags(titulo),
            "resultados": bool(it.get("resultados")),
        }
        h["calidad"] = rn.etiquetas_calidad(h, tema)
        hits.append(h)

    print(f"{f['nombre']}: {len(datos)} leídos · {len(hits)} nuevos (el resto ya estaba visto)")
    for e in errores:
        print(f"  ⚠️ la fuente avisa: {e}")
    for h in hits[:40]:
        print(f"  - {h['fecha'] or 's/f'} · {h['titulo'][:110]} {' '.join(h['calidad'])}")
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
                   "cn": a.fuente in FUENTES_CN, "hits": hits, "errores": errores}],
    }
    for h in hits:
        h["modalidad"] = rn.modalidad(h["titulo"])
    hist = rn.registra_historial(res)
    res["recuento30"], res["cobertura"] = rn.recuento_30(hist), rn.cobertura(hist)
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
