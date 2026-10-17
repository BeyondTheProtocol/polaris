#!/usr/bin/env python3
"""web_al_dia.py — qué ha pasado en el caso que la web todavía no cuenta. Solo detecta.

POR QUÉ EXISTE (17-oct-2026, plan «web al día», `docs/plan-web-al-dia.md`, aprobado por {{TITULAR}}).
Ella: *«deberías detectar cuando hay info nueva que merece la pena meter o cambiar en la web»*.
Ese día llegaron los resultados del single-cell y /ciencia y /datos siguieron diciendo «en marcha»
hasta que alguien lo corrigió a mano. El guardián (`web_guardian.py`) ya veía la cronología parada
y /datos atrasado, pero como hallazgos sueltos: nadie sabía QUÉ hecho concreto faltaba.

QUÉ PRODUCE: HECHOS, no hallazgos. Cada uno tiene identidad estable (su `clave`) y un ciclo de
vida: `encolado` → `propuesto` → `publicado` / `recordado` / `descartado`. Un hecho sin evidencia
nace `por_confirmar` y no se propone.

QUÉ «MERECE LA PENA» (determinista y por orden, plan §4; el redactor no decide, solo redacta)
  1. La web afirma algo que ya es falso: un estado de /ciencia (mapa `web_afirmaciones.json`) que
     su hilo del Tablero contradice, EN LAS DOS DIRECCIONES (Vega: un hilo que se reabre también
     deja la web falsa). El Tablero solo no basta (el del scRNA seguía «esperando» con los
     resultados ya publicados): la evidencia es una nota de la fuente de verdad con la línea
     `afecta: <id del mapa>`. Sin ella → `por_confirmar`.
  2. /datos más viejo que sus fuentes (lo calcula el guardián: `datos-atrasados`).
  3. Un informe nuevo en la bóveda, o un PR `feat(` de la web (rama que no sea `auto/`), posterior
     a la última entrada de la cronología.
  No merece: PRs mecánicos, los `auto/` (sería un bucle), notas sin `afecta:`, nada externo.

DEDUP Y SOMBRA. `vistos` se guarda aparte por modo (`al_dia_vistos_sombra.json` /
`_activo.json`): lo visto en sombra no impide que se proponga al encender el modo activo. En sombra
solo se apunta lo NUEVO en `al_dia_sombra.jsonl`; no toca el buzón ni le escribe a nadie.

Uso:
  python3 tools/web_al_dia.py estado     # los hechos apuntados y su estado
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402

DIR = os.path.join(_casa.state_dir(), "web_guardian")
TONOS_ABIERTOS = ("pendiente", "curso")
HILO_ABIERTO = ("esperando", "en_curso", "por_confirmar", "pendiente")
RE_AFECTA = re.compile(r"^\s*afecta:\s*([\w-]+)", re.M | re.I)
DIAS_NOTAS = 60


def _h(*partes):
    return hashlib.sha256("|".join(str(p) for p in partes).encode()).hexdigest()[:12]


def hecho(clave, carril, prioridad, titulo, detalle, evidencia=None):
    """Un hecho. Sin evidencia nace `por_confirmar`: se apunta, no se propone."""
    return {"clave": clave, "carril": carril, "prioridad": int(prioridad), "titulo": titulo,
            "detalle": detalle, "evidencia": evidencia or "",
            "estado": "encolado" if evidencia else "por_confirmar"}


# ── carril A: estados de /ciencia que el Tablero contradice ─────────────────────────────────

def hechos_estados(mapa, hilos, notas_afecta):
    """`hilos` = {id: hilo de seguimiento.json}; `notas_afecta` = {id del mapa: ruta de la nota}."""
    out = []
    for a in (mapa or {}).get("afirmaciones") or []:
        f = a.get("fuente") or {}
        h = hilos.get(f.get("hilo")) if f.get("tipo") == "tablero" else None
        if not h or h.get("privado"):          # el mapa solo enlaza hilos no privados (Vega)
            continue
        tono, estado = a.get("tono"), h.get("estado")
        if tono in TONOS_ABIERTOS and estado == "hecho":
            sentido = "avanza"
        elif tono == "hecho" and estado in HILO_ABIERTO:
            sentido = "retrocede"
        else:
            continue
        valor = (a.get("valor") or {}).get("es")
        out.append(hecho(
            "estado:%s:%s:%s" % (a["id"], tono, estado), "A", 1,
            "/ciencia dice «%s» y su hilo está «%s»" % (valor, estado),
            "%s (%s). Si es así, la web afirma algo que ya no es verdad." % (a["id"], sentido),
            evidencia=notas_afecta.get(a["id"])))
    return out


# ── carril B: /datos atrasado (lo calcula el guardián) ──────────────────────────────────────

def hechos_datos(hallazgos_guardian):
    out = []
    for g in hallazgos_guardian or []:
        if g.get("clave") == "datos-atrasados":
            out.append(hecho("datos:" + _h(g.get("detalle")), "B", 2, g.get("titulo"),
                             g.get("detalle"), evidencia="guardián: datos-atrasados"))
    return out


# ── carril C: hitos sin entrada en la cronología ────────────────────────────────────────────

def hechos_informes(lineas, desde):
    """`lineas` de `vega_vision._informes()` («· AAAA-MM-DD · carpeta · desc», ya de-identificadas).
    Se guarda la carpeta (el tipo), no la descripción: el detalle lo lee el redactor en local."""
    out = []
    for linea in lineas or []:
        m = re.match(r"·\s*(\d{4}-\d{2}-\d{2})\s*·\s*([^·]+)", linea)
        if not m or not desde or datetime.date.fromisoformat(m.group(1)) <= desde:
            continue
        fecha, carpeta = m.group(1), m.group(2).strip()
        out.append(hecho("informe:" + _h(fecha, carpeta, linea), "C", 3,
                         "Informe nuevo (%s) sin entrada en la cronología" % carpeta,
                         "del %s" % fecha, evidencia="bóveda"))
    return out


def _ambito(titulo):
    m = re.match(r"feat\(([^)]+)\)", titulo or "")
    return m.group(1) if m else ""


def _toca_cronologia(p):
    return any(re.match(r"content/[a-z]{2}/timeline\.yml$", (f.get("path") if isinstance(f, dict) else f) or "")
               for f in p.get("files") or [])


def hechos_prs(prs, desde):
    """`prs` = [{number, title, headRefName, mergedAt, files}] de `gh pr list --state merged`.

    Un hecho por ÁMBITO (`feat(celula-a-celula)`), no por PR: una página nueva llega en varios PRs
    y son UNA novedad. Si algún PR de ese ámbito ya tocó `timeline.yml`, la novedad ya está contada
    (17-oct-26: el PR #282 metió la entrada de /celula-a-celula y sin esto salían 4 hechos falsos)."""
    contados = {_ambito(p.get("title")) for p in prs or [] if _toca_cronologia(p)}
    grupos = {}
    for p in prs or []:
        rama, titulo = p.get("headRefName") or "", p.get("title") or ""
        if rama.startswith("auto/") or not re.match(r"feat(\(|:)", titulo) or _toca_cronologia(p):
            continue
        fecha = (p.get("mergedAt") or "")[:10]
        if not fecha or not desde or datetime.date.fromisoformat(fecha) <= desde:
            continue
        amb = _ambito(titulo) or "pr-%s" % p.get("number")
        if amb in contados:
            continue
        grupos.setdefault(amb, []).append(p)
    out = []
    for amb, ps in sorted(grupos.items()):
        ps.sort(key=lambda x: x.get("number") or 0)
        out.append(hecho("pr:%s:%s" % (amb, ps[0].get("number")), "C", 3,
                         "Página o sección nueva sin entrada en la cronología (%s)" % amb,
                         "; ".join("PR #%s: %s" % (x.get("number"), (x.get("title") or "")[:80])
                                   for x in ps), evidencia="GitHub"))
    return out


def detectar(mapa, hilos, notas_afecta, hallazgos_guardian, informes, prs, desde):
    """Todos los hechos de hoy, de mayor a menor prioridad."""
    hs = (hechos_estados(mapa, hilos, notas_afecta) + hechos_datos(hallazgos_guardian)
          + hechos_informes(informes, desde) + hechos_prs(prs, desde))
    return sorted(hs, key=lambda x: (x["prioridad"], x["clave"]))


# ── estado: lo visto, por modo ──────────────────────────────────────────────────────────────

def _ruta_vistos(modo, dir_=None):
    return os.path.join(dir_ or DIR, "al_dia_vistos_%s.json" % modo)


def _lee(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def registrar(hechos, modo, hoy, dir_=None):
    """[hechos nuevos]. Lo ya visto no se vuelve a apuntar: así un hecho que espera no se encola
    en cada pasada. Un `por_confirmar` que gana evidencia pasa a `encolado` (eso sí es nuevo)."""
    dir_ = dir_ or DIR
    os.makedirs(dir_, exist_ok=True)
    ruta = _ruta_vistos(modo, dir_)
    vistos = _lee(ruta, {})
    nuevos = []
    for h in hechos:
        previo = vistos.get(h["clave"])
        if previo and not (previo.get("estado") == "por_confirmar" and h["estado"] == "encolado"):
            continue
        vistos[h["clave"]] = {"estado": h["estado"], "carril": h["carril"],
                              "prioridad": h["prioridad"], "titulo": h["titulo"],
                              "evidencia": h["evidencia"], "desde": hoy.isoformat()}
        nuevos.append(h)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vistos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)
    if nuevos and modo == "sombra":
        with open(os.path.join(dir_, "al_dia_sombra.jsonl"), "a", encoding="utf-8") as f:
            for h in nuevos:
                f.write(json.dumps(dict(h, ts=hoy.isoformat()), ensure_ascii=False) + "\n")
    return nuevos


# ── lectura de las fuentes (lo único que toca disco o red) ──────────────────────────────────

def leer_hilos():
    d = _lee(os.path.join(_casa.state_dir(), "seguimiento.json"), {})
    return {h.get("id"): h for h in d.get("hilos") or [] if h.get("id")}


def leer_notas_afecta(raiz=None, hoy=None, dias=DIAS_NOTAS):
    """{id del mapa: ruta} de las notas de la fuente de verdad que declaran `afecta: <id>`.
    Solo las de los últimos `dias`: una nota vieja no es evidencia de un cambio de hoy."""
    raiz = raiz or os.path.join(_casa.casa_base(), "00_FUENTE-DE-VERDAD")
    hoy = hoy or datetime.date.today()
    limite = (datetime.datetime.combine(hoy, datetime.time()) - datetime.timedelta(days=dias)).timestamp()
    out = {}
    for base, _dirs, ficheros in os.walk(raiz):
        if "_PRIVADO" in base:
            continue
        for fn in ficheros:
            if not fn.endswith(".md"):
                continue
            ruta = os.path.join(base, fn)
            try:
                if os.path.getmtime(ruta) < limite:
                    continue
                with open(ruta, encoding="utf-8", errors="ignore") as f:
                    for m in RE_AFECTA.finditer(f.read(20000)):
                        out.setdefault(m.group(1), os.path.relpath(ruta, raiz))
            except OSError:
                continue
    return out


def leer_prs(repo):
    r = subprocess.run(["gh", "pr", "list", "--state", "merged", "--limit", "30", "--json",
                        "number,title,headRefName,mergedAt,files"],
                       cwd=repo, capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout or "[]") if r.returncode == 0 else None


def pasada(hallazgos_guardian, informes, desde, modo, hoy, repo, seco=False):
    """La llama el guardián. Devuelve (hechos nuevos, avisos de fuentes que no se pudieron leer).
    Una fuente que falla no calla a las demás, y se dice: un detector mudo se lee como «todo bien»."""
    import web_mapa
    avisos = []
    try:
        mapa = web_mapa.cargar()
    except (OSError, ValueError) as e:
        mapa = {}
        avisos.append("mapa ilegible: %r" % (e,))
    prs = None
    try:
        prs = leer_prs(repo)
    except Exception as e:
        avisos.append("PRs: %r" % (e,))
    if prs is None:
        avisos.append("no pude listar los PRs fusionados de la web")
    hechos = detectar(mapa, leer_hilos(), leer_notas_afecta(hoy=hoy), hallazgos_guardian,
                      informes, prs or [], desde)
    if seco:                       # enseña lo que vería hoy, sin apuntar nada
        return hechos, avisos
    return registrar(hechos, modo, hoy), avisos


def main(argv):
    if argv[:1] == ["estado"]:
        for modo in ("sombra", "activo"):
            vistos = _lee(_ruta_vistos(modo), {})
            if not vistos:
                continue
            print("modo %s · %d hechos" % (modo, len(vistos)))
            for clave, v in sorted(vistos.items(), key=lambda kv: (kv[1].get("prioridad", 9), kv[0])):
                print("  [P%s %s · %s] %s" % (v.get("prioridad"), v.get("carril"), v.get("estado"),
                                             v.get("titulo")))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
