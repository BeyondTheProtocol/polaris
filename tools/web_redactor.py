#!/usr/bin/env python3
"""web_redactor.py — de «la web aún no cuenta esto» a UN cambio esperando el «sí» de {{TITULAR}}.

POR QUÉ EXISTE (17-oct-2026, plan «web al día», `docs/plan-web-al-dia.md`, aprobado por {{TITULAR}}:
«hazlo todo»). `web_al_dia.py` detecta los hechos; esto los convierte en texto, en un PR y en el
Telegram que ella contesta. Su regla: *«yo solo quiero repasar textos»*, *«me llega un telegram
y me dice apruebas este texto y chimpún»*.

QUÉ ESCRIBE (y nada más: lista cerrada `PERMITIDOS`, comprobada contra el diff antes de commitear)
  · A, /ciencia — el `status` y `statusTone` de una prueba, sacados de las plantillas del mapa
    (`web_afirmaciones.json` → `transiciones`), que aprobaron `voz-titular` y `diseno`. Sin
    plantilla para ese destino no se redacta a ciegas: se apunta y se espera.
  · B, /datos — se regenera con `caso_publico.py build`, que es fail-closed y tiene su propio
    freno (`web_lint.revisar_caso`).
  · C, cronología — una entrada NUEVA (solo se añade) por página o sección nueva de la web. La
    escribe Claude (`ia.ask`, cerebro de confianza) con la voz de las entradas vecinas, en ES y en
    EN escritos aparte, `highlight: false` y tag de los que ya existen. Los informes clínicos
    nuevos NO se redactan solos: contarlos exige leerlos y que `comite-medico` lo mire; van al parte.
  Todo texto nuevo pasa `web_lint` (con el cotejo de cifras de dianas) ANTES de enseñárselo; si
  falla, ese cambio no sale.

UN SOLO PR VIVO, UN SOLO TELEGRAM AL DÍA. Mientras hay un PR del carril abierto no se abre otro:
dos PRs sobre el mismo fichero invalidan su «sí» sellado al rebasar. El PR vivo se apunta en
`al_dia_pr_vivo_<modo>.json`, no se pregunta a GitHub. Hasta 3 cambios por PR (`TOPE`).
Si no contesta: un recordatorio a los 3 días; si algo de lo que lleva es prioridad 1 (la web dice
algo falso), otro a los 7. Luego, silencio: queda en el parte.

SUS CAMBIOS. Si responde al Telegram con correcciones («quita el 2», «dilo más corto»), el bot
las apunta (`web_guardian.encargar_cambios`). En la pasada siguiente el redactor las aplica con
Claude sobre las piezas del PR vivo, valida lo que vuelve (mismas piezas o menos, tags existentes,
lint), rehace ESE PR en su rama `auto/`, retira el Telegram viejo y le manda el nuevo. Máximo 3
vueltas; a la cuarta quedan en el buzón de Vega.

LA FIRMA. Este fichero NUNCA fusiona ni mira el permiso de sesión: abre el PR, espera al CI y
encarga `pedir_ok`, que sella PR + commit + huella del texto. Publica su «sí» y solo su «sí».

SOMBRA. Por defecto (modo del guardián). Prepara los textos y apunta en
`al_dia_propuestas_sombra.jsonl` el antes→después que le habría mandado, sin PR ni Telegram.

Uso:
  python3 tools/web_redactor.py estado
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import web_al_dia as AD  # noqa: E402
import web_lint  # noqa: E402

DIR = AD.DIR
PERMITIDOS = ("content/es/science.yml", "content/en/science.yml", "content/es/timeline.yml",
              "content/en/timeline.yml", "app/data/caso.json", "public/datos/caso.json")
TOPE = 3
RECORDAR_DIAS = 3
SEGUNDO_P1_DIAS = 7
MESES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
            "septiembre", "octubre", "noviembre", "diciembre")
MESES_EN = ("January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December")
MESES_EN_CORTO = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _lee(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def _escribe(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)


def _apunta(ruta, linea):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")


def lint_texto(texto):
    """`web_lint` sin la regla de longitud mínima (un estado de /ciencia es corto por diseño)."""
    return [(c, m) for c, m in web_lint.revisar(texto) if c != "formato"]


# ── A: estados de /ciencia ──────────────────────────────────────────────────────────────────

def texto_transicion(afirmacion, destino, hoy):
    """{es, en, tono} para ese destino, con la fecha puesta, o None si no hay plantilla aprobada."""
    t = (afirmacion.get("transiciones") or {}).get(destino)
    if not t or not t.get("es") or not t.get("en") or not t.get("tono"):
        return None
    es = t["es"].replace("{mes}", MESES_ES[hoy.month - 1]).replace("{año}", str(hoy.year))
    en = t["en"].replace("{month}", MESES_EN[hoy.month - 1]).replace("{year}", str(hoy.year))
    return {"es": es, "en": en, "tono": t["tono"]}


def aplicar_estado(texto, componente, status, tono):
    """Cambia `status` y `statusTone` SOLO dentro del bloque de `componente`. Lanza si no lo
    encuentra: tocar a ciegas otro bloque sería publicar un estado en la prueba equivocada."""
    lineas, dentro, cambiadas = texto.split("\n"), False, set()
    for i, l in enumerate(lineas):
        m = re.match(r"(\s*)- component:\s*(.+)", l)
        if m:
            dentro = (m.group(2).strip().strip("'\"") == componente)
            continue
        if not dentro:
            continue
        m = re.match(r"(\s+)(status|statusTone):\s*.*", l)
        if m:
            valor = status if m.group(2) == "status" else tono
            lineas[i] = "%s%s: '%s'" % (m.group(1), m.group(2), valor.replace("'", "''"))
            cambiadas.add(m.group(2))
    if cambiadas != {"status", "statusTone"}:
        raise ValueError("no encuentro status y statusTone de «%s»" % componente)
    return "\n".join(lineas)


# ── C: entradas de cronología ───────────────────────────────────────────────────────────────

def ultimas_entradas(texto, n=3):
    partes = re.split(r"\n(?=  - date:)", texto or "")
    return "\n\n".join(p.strip("\n") for p in partes[1:][-n:])


def prompt_entrada(h, vecinas_es, vecinas_en, tags_es, tags_en, fecha):
    prs = "; ".join("PR #%s «%s» (%s)" % (p["number"], p["title"], p["fecha"])
                    for p in (h.get("datos") or {}).get("prs") or [])
    return (
        "Escribe UNA entrada nueva para la cronología pública de helptitular.com, la web del caso "
        "de {{TITULAR}}, en su voz. El hecho: en la web se ha publicado una página o sección nueva. "
        "Lo que se sabe: %s.\n\n"
        "Reglas: título corto y narrativo como los de las vecinas; descripción de 1 a 3 frases; "
        "primera persona si las vecinas la usan; nada de cifras clínicas, dosis, marcadores, "
        "nombres de médicos ni de instituciones; nunca «ingeniera» ni «{{CONTACTO}}»; sin guion largo, "
        "sin «no es X, es Y», sin tricolon. Si no sabes algo, no lo inventes: cuenta solo que la "
        "página existe y para qué sirve según su título.\n"
        "El inglés se ESCRIBE aparte, natural, no traducido palabra a palabra.\n"
        "Tag: uno de %s en español y uno de %s en inglés (el equivalente).\n\n"
        "Entradas vecinas (ES):\n%s\n\nEntradas vecinas (EN):\n%s\n\n"
        "Devuelve SOLO un JSON: {\"tag_es\": \"\", \"titulo_es\": \"\", \"descripcion_es\": \"\", "
        "\"tag_en\": \"\", \"titulo_en\": \"\", \"descripcion_en\": \"\"}. Fecha del hecho: %s."
        % (prs or h.get("detalle"), tags_es, tags_en, vecinas_es, vecinas_en, fecha))


def _json_de(texto):
    m = re.search(r"\{.*\}", texto or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def _ask_claude(prompt):
    import ia
    r = ia.ask(prompt, clinico=True, prefer="claude")
    return (r or {}).get("text")


def fecha_en(d):
    return "%s %d, %d" % (MESES_EN_CORTO[d.month - 1], d.day, d.year)


def bloque(fecha, tag, titulo, descripcion):
    """Una entrada con el formato exacto del fichero (el mismo que `web_novedad`)."""
    import web_novedad as N
    cuerpo = "\n".join("      " + x.strip() for x in N._envolver(descripcion, 92))
    return ("  - date: '%s'\n    tag: %s\n    title: %s\n    description: >\n%s\n"
            "    highlight: false\n" % (fecha, tag, titulo, cuerpo))


def redactar_entrada(h, textos, _ask=None):
    """(entrada, motivo). La pide a Claude y la pasa por el lint; un reintento si falla."""
    import web_novedad as N
    datos = h.get("datos") or {}
    if datos.get("tipo") != "pr":
        return None, ("un informe clínico nuevo no se cuenta solo: hay que leerlo y que lo mire "
                      "comite-medico. Va al parte")
    fechas = sorted(p["fecha"] for p in datos.get("prs") or [] if p.get("fecha"))
    if not fechas:
        return None, "sin fecha del hecho"
    d = datetime.date.fromisoformat(fechas[0])
    es, en = textos.get("content/es/timeline.yml"), textos.get("content/en/timeline.yml")
    if es is None or en is None:
        return None, "no pude leer la cronología publicada"
    tags_es, tags_en = N.tags_existentes(es), N.tags_existentes(en)
    prompt = prompt_entrada(h, ultimas_entradas(es), ultimas_entradas(en), tags_es, tags_en,
                            N.fecha_humana(d))
    motivo = "Claude no respondió"
    for _intento in range(2):
        e = _json_de((_ask or _ask_claude)(prompt))
        if not e or not all(e.get(k) for k in ("tag_es", "titulo_es", "descripcion_es",
                                                "tag_en", "titulo_en", "descripcion_en")):
            motivo = "respuesta sin el JSON pedido"
            continue
        if e["tag_es"] not in tags_es or e["tag_en"] not in tags_en:
            motivo = "tag inventado (%s / %s)" % (e["tag_es"], e["tag_en"])
            prompt += "\nOJO: el tag tiene que ser EXACTAMENTE uno de la lista."
            continue
        fallos = (lint_texto(e["titulo_es"] + ". " + e["descripcion_es"])
                  + lint_texto(e["titulo_en"] + ". " + e["descripcion_en"]))
        if fallos:
            motivo = "web_lint: " + "; ".join("%s: %s" % f for f in fallos)
            prompt += "\nEl intento anterior no pasó el freno (%s). Quita eso." % motivo
            continue
        return dict(e, fecha_es=N.fecha_humana(d), fecha_en=fecha_en(d)), ""
    return None, motivo


def insertar_entradas(textos, entradas):
    """Añade (nunca modifica) las entradas. ES en su sitio por fecha; EN al final, porque su
    formato de fecha ('Oct 7, 2026') no lo entiende el ordenador de `web_novedad` y un hito
    nuevo es casi siempre el último."""
    import web_novedad as N
    es, en = textos["content/es/timeline.yml"], textos["content/en/timeline.yml"]
    for e in entradas:
        es = N._insertar_ordenado(es, bloque(e["fecha_es"], e["tag_es"], e["titulo_es"],
                                             e["descripcion_es"]), e["fecha_es"])
        en = en.rstrip("\n") + "\n\n" + bloque(e["fecha_en"], e["tag_en"], e["titulo_en"],
                                               e["descripcion_en"])
    return {"content/es/timeline.yml": es, "content/en/timeline.yml": en}


# ── preparar: hechos → ficheros nuevos ──────────────────────────────────────────────────────

def preparar(hechos, mapa, textos, hoy, _ask=None):
    """{"ficheros": {rel: contenido nuevo}, "datos": bool, "hechos": [claves que van],
        "cambios": [{clave, que, antes, despues}], "fuera": [(clave, estado, motivo)]}"""
    out = {"ficheros": {}, "datos": False, "hechos": [], "cambios": [], "fuera": [], "piezas": []}
    actual = dict(textos)
    filas = {a["id"]: a for a in (mapa or {}).get("afirmaciones") or []}
    entradas = []
    for h in hechos[:TOPE]:
        datos, clave = h.get("datos") or {}, h["clave"]
        try:
            if h["carril"] == "A":
                a = filas.get(datos.get("id"))
                t = texto_transicion(a or {}, datos.get("destino"), hoy)
                if not a or not t:
                    out["fuera"].append((clave, "sin_plantilla", "no hay frase aprobada para «%s»"
                                         % datos.get("destino")))
                    continue
                fallos = lint_texto(t["es"]) + lint_texto(t["en"])
                if fallos:
                    out["fuera"].append((clave, "lint", "; ".join("%s: %s" % f for f in fallos)))
                    continue
                for idioma in ("es", "en"):
                    rel = "content/%s/science.yml" % idioma
                    actual[rel] = aplicar_estado(actual[rel], a["componente"][idioma], t[idioma], t["tono"])
                out["cambios"].append({"clave": clave, "que": "/ciencia · %s" % a["componente"]["es"],
                                       "antes": a["valor"]["es"], "despues": t["es"]})
                out["piezas"].append({"clave": clave, "carril": "A", "id": a["id"], "es": t["es"],
                                      "en": t["en"], "tono": t["tono"], "antes": a["valor"]["es"]})
            elif h["carril"] == "B":
                out["datos"] = True
                m = re.search(r"\d{4}-\d{2}-\d{2}", h.get("detalle") or "")
                out["cambios"].append({"clave": clave, "que": "/datos",
                                       "antes": "panel generado el %s" % (m.group(0) if m else "?"),
                                       "despues": "regenerado con lo de hoy"})
                out["piezas"].append({"clave": clave, "carril": "B",
                                      "antes": out["cambios"][-1]["antes"]})
            elif h["carril"] == "C":
                e, motivo = redactar_entrada(h, actual, _ask)
                if not e:
                    out["fuera"].append((clave, "necesita_humano" if "informe" in motivo else
                                         "sin_redactar", motivo))
                    continue
                entradas.append(e)
                out["cambios"].append({"clave": clave, "que": "cronología (nueva)", "antes": "",
                                       "despues": "%s · %s" % (e["titulo_es"], e["descripcion_es"])})
                out["piezas"].append(dict(e, clave=clave, carril="C"))
            else:
                continue
            out["hechos"].append(clave)
        except (ValueError, KeyError) as ex:
            out["fuera"].append((clave, "error", str(ex)[:200]))
    if entradas:
        actual.update(insertar_entradas(actual, entradas))
    out["ficheros"] = {k: v for k, v in actual.items() if v != textos.get(k)}
    return out


# ── sus cambios: «responde con el cambio» (bucle, plan §7.4) ─────────────────────────────────

VUELTAS_MAX = 3
CAMPOS_C = ("tag_es", "titulo_es", "descripcion_es", "tag_en", "titulo_en", "descripcion_en")


def plan_desde_piezas(piezas, mapa, textos):
    """Rehace ficheros y resumen a partir de piezas ya escritas (sin volver a redactar)."""
    filas = {a["id"]: a for a in (mapa or {}).get("afirmaciones") or []}
    actual, cambios, entradas, datos = dict(textos), [], [], False
    for p in piezas:
        if p["carril"] == "A":
            a = filas[p["id"]]
            for idioma in ("es", "en"):
                rel = "content/%s/science.yml" % idioma
                actual[rel] = aplicar_estado(actual[rel], a["componente"][idioma], p[idioma], p["tono"])
            cambios.append({"clave": p["clave"], "que": "/ciencia · %s" % a["componente"]["es"],
                            "antes": p.get("antes", ""), "despues": p["es"]})
        elif p["carril"] == "B":
            datos = True
            cambios.append({"clave": p["clave"], "que": "/datos", "antes": p.get("antes", ""),
                            "despues": "regenerado con lo de hoy"})
        elif p["carril"] == "C":
            entradas.append(p)
            cambios.append({"clave": p["clave"], "que": "cronología (nueva)", "antes": "",
                            "despues": "%s · %s" % (p["titulo_es"], p["descripcion_es"])})
    if entradas:
        actual.update(insertar_entradas(actual, entradas))
    return {"ficheros": {k: v for k, v in actual.items() if v != textos.get(k)}, "datos": datos,
            "cambios": cambios, "hechos": [p["clave"] for p in piezas], "piezas": piezas, "fuera": []}


def aplicar_sus_cambios(piezas, pedido, textos, _ask=None):
    """(piezas nuevas, motivo). Su respuesta al Telegram es SUYA (viene de su chat), pero se trata
    como instrucciones de CONTENIDO para estas piezas, nunca como órdenes de sistema: lo que vuelve
    se valida campo a campo (mismas claves o menos, tags existentes, tono válido) y pasa el lint."""
    import web_novedad as N
    tags_es = N.tags_existentes(textos.get("content/es/timeline.yml") or "")
    tags_en = N.tags_existentes(textos.get("content/en/timeline.yml") or "")
    numeradas = [dict(p, n=i + 1) for i, p in enumerate(piezas)]
    prompt = (
        "{{TITULAR}} ha revisado estos cambios para su web y ha contestado con lo que quiere cambiar. "
        "Aplica SOLO lo que pide.\n\nCambios propuestos (JSON, numerados):\n%s\n\n"
        "Su respuesta, literal: «%s»\n\n"
        "Reglas: devuelve la MISMA lista con sus cambios aplicados; si pide quitar uno, quítalo de "
        "la lista; no toques `clave`, `carril`, `id` ni `antes`. Los de carril B solo se quedan o "
        "se quitan. En A, `es` y `en` son el estado corto y `tono` uno de pendiente/curso/hecho/no. "
        "En C, tags de %s (es) y %s (en). Si cambia el español, adapta el inglés (no lo traduzcas "
        "palabra a palabra). Nada de cifras clínicas, dosis, marcadores ni nombres de médicos o "
        "instituciones; nunca «ingeniera» ni «{{CONTACTO}}». Devuelve SOLO un JSON: {\"piezas\": [...]}."
        % (json.dumps(numeradas, ensure_ascii=False), str(pedido)[:2000], tags_es, tags_en))
    r = _json_de((_ask or _ask_claude)(prompt))
    nuevas = (r or {}).get("piezas") if isinstance(r, dict) else None
    if not isinstance(nuevas, list):
        return None, "Claude no devolvió las piezas"
    originales = {p["clave"]: p for p in piezas}
    limpias = []
    for p in nuevas:
        o = originales.get((p or {}).get("clave"))
        if not o or p.get("carril") != o["carril"]:
            return None, "la respuesta trae un cambio que no estaba"
        q = dict(o)
        if o["carril"] == "A":
            q.update(es=str(p.get("es") or ""), en=str(p.get("en") or ""), tono=p.get("tono"))
            if not q["es"] or not q["en"] or q["tono"] not in ("pendiente", "curso", "hecho", "no"):
                return None, "estado de /ciencia incompleto"
            fallos = lint_texto(q["es"]) + lint_texto(q["en"])
        elif o["carril"] == "C":
            q.update({k: str(p.get(k) or "") for k in CAMPOS_C})
            if not all(q[k] for k in CAMPOS_C) or q["tag_es"] not in tags_es or q["tag_en"] not in tags_en:
                return None, "entrada incompleta o con un tag que no existe"
            fallos = (lint_texto(q["titulo_es"] + ". " + q["descripcion_es"])
                      + lint_texto(q["titulo_en"] + ". " + q["descripcion_en"]))
        else:
            fallos = []
        if fallos:
            return None, "web_lint: " + "; ".join("%s: %s" % f for f in fallos)
        limpias.append(q)
    return limpias, ""


def retirar_borradores_web(numero, _pending=None):
    """Retira los Telegram de la web que esperan su «sí» para el PR `numero` (los mueve a
    `outbox/retirado/`). Dirección segura: solo impide que algo salga, nunca hace salir nada.
    Sin esto, un «sí» al texto viejo lo rechazaría el commit, pero ella lo vería como un fallo."""
    import salida
    pend = _pending or salida._sub("pending")
    destino = os.path.join(os.path.dirname(pend), "retirado")
    n = 0
    try:
        nombres = [x for x in os.listdir(pend) if "-web-publish-" in x and x.endswith(".json")]
    except OSError:
        return 0
    for x in nombres:
        ruta = os.path.join(pend, x)
        d = _lee(ruta, {})
        if (d.get("sellado") or {}).get("pr") == numero:
            os.makedirs(destino, exist_ok=True)
            os.replace(ruta, os.path.join(destino, x))
            n += 1
    return n


def cambios_suyos(numero, _dir=None):
    """[(ruta, texto)] de lo que ella respondió al Telegram del PR `numero` (lo apunta el bot vía
    `web_guardian.encargar_cambios`)."""
    import web_guardian
    d = _dir or web_guardian.CAMBIOS
    try:
        nombres = sorted(x for x in os.listdir(d) if x.startswith("%d-" % numero) and x.endswith(".json"))
    except OSError:
        return []
    return [(os.path.join(d, x), (_lee(os.path.join(d, x), {}) or {}).get("texto") or "") for x in nombres]


def _archiva_cambio(ruta, sub):
    destino = os.path.join(os.path.dirname(ruta), sub)
    os.makedirs(destino, exist_ok=True)
    os.replace(ruta, os.path.join(destino, os.path.basename(ruta)))


# ── el PR ───────────────────────────────────────────────────────────────────────────────────

def textos_publicados(repo):
    import web_mapa
    rels = ("content/es/science.yml", "content/en/science.yml",
            "content/es/timeline.yml", "content/en/timeline.yml")
    return {r: web_mapa.publicado(r, repo) for r in rels}


def fuera_de_lista(rutas):
    """Lo que un cambio del carril NO puede tocar (hero, navegación, equipo, prensa… todo lo que
    no está en `PERMITIDOS`). Si sale algo, no hay PR."""
    return [r for r in rutas if r not in PERMITIDOS]


def crear_pr(plan, hoy, repo, rama=None, numero=None):
    """Nº del PR, o lanza. Worktree propio desde origin/main; nunca el árbol de {{TITULAR}}.
    Con `rama` y `numero` rehace ESE PR (sus cambios): misma rama desde origin/main, push forzado
    solo a esa rama `auto/`, sin abrir otro PR."""
    import web_novedad as N

    def git(*a, cwd=None):
        return N._git(*a, cwd=cwd or repo)

    rehacer = bool(rama and numero)
    rama = rama or "auto/web-al-dia-%s" % hoy.isoformat()
    if not rama.startswith("auto/"):
        raise RuntimeError("el redactor solo escribe en ramas auto/ (%s)" % rama)
    wt = tempfile.mkdtemp(prefix="webaldia-")
    try:
        git("fetch", "origin", "main")
        git("worktree", "add", "-B" if rehacer else "-b", rama, wt, "origin/main")
        for rel, contenido in plan["ficheros"].items():
            with open(os.path.join(wt, rel), "w", encoding="utf-8") as f:
                f.write(contenido)
        if plan["datos"]:
            r = subprocess.run([sys.executable, os.path.join(HERE, "caso_publico.py"), "build",
                                "--web", wt], capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError("caso_publico build falló: %s" % (r.stdout + r.stderr)[-300:])
        # rutas, no `status --porcelain`: `_git` hace strip() y se comía el primer carácter de la
        # primera ruta («ontent/…»), y el freno de la lista lo paraba todo (lo cazó el test).
        tocados = sorted(set(git("diff", "--name-only", cwd=wt).split())
                         | set(git("ls-files", "--others", "--exclude-standard", cwd=wt).split()))
        fuera = fuera_de_lista(tocados)
        if fuera:
            raise RuntimeError("el cambio toca ficheros fuera de la lista: %s" % fuera)
        if not tocados:
            raise RuntimeError("no hay nada que cambiar")
        git("add", *tocados, cwd=wt)
        asunto = "[auto] web al día: %s" % "; ".join(c["que"] for c in plan["cambios"])[:90]
        cuerpo = ("Preparado por tools/web_redactor.py (plan «web al día»). No se fusiona sin el "
                  "«sí» de {{TITULAR}} por Telegram.\n\n" + "\n".join(
                      "- %s: «%s» → «%s»" % (c["que"], c["antes"], c["despues"]) for c in plan["cambios"]))
        git("commit", "-q", "-m", asunto, "-m", cuerpo, cwd=wt)
        if rehacer:
            git("push", "-q", "--force", "origin", rama, cwd=wt)
            return numero
        git("push", "-q", "origin", rama, cwd=wt)
        r = subprocess.run(["gh", "pr", "create", "--base", "main", "--head", rama, "--title",
                            asunto, "--body", cuerpo], cwd=wt, capture_output=True, text=True,
                           timeout=180)
        m = re.search(r"/pull/(\d+)", r.stdout or "")
        if not m:
            raise RuntimeError("gh pr create: %s" % (r.stderr or r.stdout)[-300:])
        return int(m.group(1))
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt],
                       capture_output=True, timeout=60)


# ── el ciclo ────────────────────────────────────────────────────────────────────────────────

def _ruta_vivo(modo, dir_):
    return os.path.join(dir_, "al_dia_pr_vivo_%s.json" % modo)


def _marca(vistos, claves, estado, **extra):
    for c in claves:
        if c in vistos:
            vistos[c].update(estado=estado, **extra)


def seguir_pr(vivo, vistos, hoy, _estado_pr, _encargar):
    """(sigue_vivo, nota). Lo que pasó con el PR que espera su «sí»."""
    est = _estado_pr(vivo["pr"])
    if est == "MERGED":
        _marca(vistos, vivo["claves"], "publicado", pr=vivo["pr"])
        return False, "PR #%s publicado" % vivo["pr"]
    if est == "CLOSED":
        _marca(vistos, vivo["claves"], "descartado", pr=vivo["pr"])
        return False, "PR #%s cerrado sin publicar" % vivo["pr"]
    dias = (hoy - datetime.date.fromisoformat(vivo["desde"])).days
    p1 = any((vistos.get(c) or {}).get("prioridad") == 1 for c in vivo["claves"])
    n = vivo.get("recordatorios", 0)
    if (n == 0 and dias >= RECORDAR_DIAS) or (n == 1 and p1 and dias >= SEGUNDO_P1_DIAS):
        _encargar(vivo["pr"])
        vivo["recordatorios"] = n + 1
        _marca(vistos, vivo["claves"], "recordado", pr=vivo["pr"])
        return True, "recordatorio %d del PR #%s" % (n + 1, vivo["pr"])
    return True, "PR #%s esperando su «sí» (%d d)" % (vivo["pr"], dias)


def _rehacer(vivo, vistos, pedido, mapa, textos, hoy, crear, ci, encargar, retirar, ask):
    """Una vuelta del bucle de cambios sobre el PR vivo. Devuelve la nota."""
    piezas, motivo = aplicar_sus_cambios(vivo.get("piezas") or [], pedido, textos, ask)
    if piezas is None:
        return False, "sus cambios al PR #%s no se pudieron aplicar solos (%s): quedan en el buzón de Vega" % (vivo["pr"], motivo)
    quitadas = [c for c in vivo["claves"] if c not in {p["clave"] for p in piezas}]
    _marca(vistos, quitadas, "descartado", motivo="lo quitó ella")
    if not piezas:
        return True, "quitó todos los cambios del PR #%s" % vivo["pr"]
    plan = plan_desde_piezas(piezas, mapa, textos)
    crear(plan, vivo["rama"], vivo["pr"])
    retirar(vivo["pr"])
    estado_ci, _ = ci(vivo["pr"])
    vivo.update(claves=plan["hechos"], piezas=piezas, vueltas=vivo.get("vueltas", 0) + 1,
                desde=hoy.isoformat(), recordatorios=0, ci=estado_ci)
    if estado_ci == "verde":
        encargar(vivo["pr"])
        return True, "PR #%s rehecho con sus cambios (vuelta %d); Telegram nuevo encargado" % (vivo["pr"], vivo["vueltas"])
    return True, "PR #%s rehecho con sus cambios; CI «%s»" % (vivo["pr"], estado_ci)


def redactar(modo, hoy, repo, dir_=None, _ask=None, _textos=None, _crear=None, _ci=None,
             _encargar=None, _estado_pr=None, _mapa=None, _cambios=None, _retirar=None):
    """Una vuelta del redactor. Devuelve una lista de líneas de lo que hizo. Nunca fusiona."""
    import _lock
    dir_ = dir_ or DIR
    notas = []
    with _lock.lock("web-al-dia-redactor", timeout=60.0):
        vistos = _lee(os.path.join(dir_, "al_dia_vistos_%s.json" % modo), {})
        ruta_vivo = _ruta_vivo(modo, dir_)
        vivo = _lee(ruta_vivo, None)
        if modo == "activo" and vivo:
            import web_guardian
            import web_publicar
            encargar = _encargar or web_guardian.encargar_pedir_ok
            ci = _ci or (lambda n: web_publicar.esperar_ci(str(n)))
            estado = (_estado_pr or web_publicar.estado_pr)(vivo["pr"])
            pendientes = (_cambios or cambios_suyos)(vivo["pr"]) if estado == "OPEN" else []
            if pendientes:
                ruta, pedido = pendientes[-1]          # la última respuesta suya manda
                if vivo.get("vueltas", 0) >= VUELTAS_MAX:
                    notas.append("PR #%s: %d vueltas ya; sus cambios quedan en el buzón de Vega"
                                 % (vivo["pr"], VUELTAS_MAX))
                    sub = "sin_aplicar"
                else:
                    if _mapa is None:
                        import web_mapa
                        _mapa = web_mapa.cargar()
                    textos = _textos if _textos is not None else textos_publicados(repo)
                    ok, nota = _rehacer(vivo, vistos, pedido, _mapa, textos, hoy,
                                        _crear or (lambda p, r, n: crear_pr(p, hoy, repo, r, n)),
                                        ci, encargar, _retirar or retirar_borradores_web, _ask)
                    notas.append(nota)
                    sub = "aplicados" if ok else "sin_aplicar"
                for r, _t in pendientes:
                    if os.path.exists(r):
                        _archiva_cambio(r, sub)
                _escribe(ruta_vivo, vivo)
                _escribe(os.path.join(dir_, "al_dia_vistos_%s.json" % modo), vistos)
                return notas
            if estado == "OPEN" and vivo.get("ci") != "verde":
                # el CI no estaba en verde al crearlo: sin esto, no se le pedía nunca el «sí»
                vivo["ci"], _ = ci(vivo["pr"])
                if vivo["ci"] == "verde":
                    encargar(vivo["pr"])
                    notas.append("PR #%s: CI ya en verde; Telegram encargado" % vivo["pr"])
            sigue, nota = seguir_pr(vivo, vistos, hoy, lambda _n: estado, encargar)
            notas.append(nota)
            if sigue:
                _escribe(ruta_vivo, vivo)
                _escribe(os.path.join(dir_, "al_dia_vistos_%s.json" % modo), vistos)
                return notas
            os.remove(ruta_vivo)
        cola = sorted(((k, v) for k, v in vistos.items() if v.get("estado") == "encolado"),
                      key=lambda kv: (kv[1].get("prioridad", 9), kv[0]))
        if not cola:
            _escribe(os.path.join(dir_, "al_dia_vistos_%s.json" % modo), vistos)
            return notas or ["nada que redactar"]
        hechos = [dict(v, clave=k) for k, v in cola]
        if _mapa is None:
            import web_mapa
            _mapa = web_mapa.cargar()
        textos = _textos if _textos is not None else textos_publicados(repo)
        plan = preparar(hechos, _mapa, textos, hoy, _ask)
        for clave, estado, motivo in plan["fuera"]:
            _marca(vistos, [clave], estado, motivo=motivo)
            notas.append("fuera: %s (%s)" % (clave, motivo))
        if not plan["hechos"]:
            _escribe(os.path.join(dir_, "al_dia_vistos_%s.json" % modo), vistos)
            return notas
        if modo != "activo":
            _apunta(os.path.join(dir_, "al_dia_propuestas_sombra.jsonl"),
                    {"ts": hoy.isoformat(), "cambios": plan["cambios"],
                     "ficheros": sorted(plan["ficheros"]) + (["app/data/caso.json"] if plan["datos"] else [])})
            _marca(vistos, plan["hechos"], "propuesto_sombra")
            notas.append("sombra: habría propuesto %d cambio(s)" % len(plan["cambios"]))
        else:
            numero = (_crear or (lambda p, r=None, n=None: crear_pr(p, hoy, repo, r, n)))(plan)
            import web_publicar
            estado_ci, _info = (_ci or (lambda n: web_publicar.esperar_ci(str(n))))(numero)
            _escribe(ruta_vivo, {"pr": numero, "claves": plan["hechos"], "desde": hoy.isoformat(),
                                 "recordatorios": 0, "ci": estado_ci, "vueltas": 0,
                                 "rama": "auto/web-al-dia-%s" % hoy.isoformat(),
                                 "piezas": plan["piezas"]})
            _marca(vistos, plan["hechos"], "propuesto", pr=numero)
            if estado_ci == "verde":
                import web_guardian
                (_encargar or web_guardian.encargar_pedir_ok)(numero)
                notas.append("PR #%s listo; Telegram encargado" % numero)
            else:
                notas.append("PR #%s con CI «%s»: no se le pide nada hasta que esté en verde"
                             % (numero, estado_ci))
        _escribe(os.path.join(dir_, "al_dia_vistos_%s.json" % modo), vistos)
    return notas


def main(argv):
    if argv[:1] == ["estado"]:
        for modo in ("sombra", "activo"):
            vivo = _lee(_ruta_vivo(modo, DIR), None)
            if vivo:
                print("modo %s · PR #%s vivo desde %s (%d recordatorio/s)"
                      % (modo, vivo["pr"], vivo["desde"], vivo.get("recordatorios", 0)))
        ruta = os.path.join(DIR, "al_dia_propuestas_sombra.jsonl")
        try:
            with open(ruta, encoding="utf-8") as f:
                ultimas = [json.loads(x) for x in f if x.strip()][-3:]
        except OSError:
            ultimas = []
        for p in ultimas:
            print("sombra %s:" % p["ts"])
            for c in p["cambios"]:
                print("  · %s: «%s» → «%s»" % (c["que"], c["antes"], c["despues"]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
