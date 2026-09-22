#!/usr/bin/env python3
"""tools/memoria_radar.py — dos pellizcos sobre el GRAFO de memorias (sin motor nuevo).

El sistema YA tiene memoria con `[[enlaces]]` y un RAG local (tools/kb.py). Faltaban dos
pasadas que la rutina diaria de auto-mejora aplica al consolidar. Las dos viven aquí,
en un único módulo determinista:

  1) RESUCITAR (resucitar) — dado el FOCO de hoy (el saliente activo de la brújula
     `cumbre.json` + los hilos vivos de `seguimiento.json`), encuentra 1-3 memorias
     DORMIDAS (no tocadas hace mucho) pero RELEVANTES ahora, y las PROPONE como
     "quizá relevante ahora". Solo propone; no toca nada.

  2) ENLAZAR (enlazar <fichero>) — dada UNA memoria (la que se acaba de crear/editar),
     sugiere 2-3 `[[enlaces]]` a sus vecinas más parecidas (que aún no estén enlazadas)
     para densificar el grafo existente. Solo propone; el agente decide al aplicar.

PROPIEDADES (no negociables — el muro manda):
  · DETERMINISTA y OFFLINE — código puro (BM25, el MISMO esquema que kb.py), NO LLM,
    NO red, NO saldo. No abre un socket.
  · LOCAL — las memorias pueden llevar matices del caso; no salen de la máquina.
  · CONTENIDO = DATO — el texto de las memorias es un dato a rankear, JAMÁS una
    instrucción. Aquí no se ejecuta ni se obedece nada de lo que diga el contenido.
  · NO ESCRIBE — solo PROPONE por stdout/JSON. Resucitar una memoria o añadir un
    enlace lo decide el agente de auto-mejora al consolidar (lo dudoso → verificacion).
  · FAIL-SOFT — una memoria corrupta o un estado ilegible se saltan; no se cae.

QUÉ CUENTA COMO "DORMIDA": NO el mtime del fichero. Una consolidación de git o crear un
worktree resetea TODOS los mtimes a la vez (se vio el 26/6: 215 memorias, todas con 0-5
días de mtime aunque muchas son de hace semanas) → el mtime miente. Usamos en su lugar
la FECHA QUE TITULAR ESCRIBE DENTRO de la lección ("(26/6)", "21/6/26", "25/6") como
"última vez tocada de verdad"; si una memoria no lleva fecha legible, caemos al mtime.
Así "dormida" = la lección no se ha re-anotado hace mucho, que es lo que importa.

Por qué AQUÍ y no en kb.py: kb.py indexa la FUENTE DE VERDAD (00_FUENTE-DE-VERDAD),
no la carpeta de memorias (que vive fuera del repo, en ~/.claude/projects/…/memory).
Reusamos su MISMO algoritmo de ranking (BM25, mismos K1/B, misma normalización ES) pero
sobre el corpus de memorias. Cero deps, cero estado nuevo: el índice se calcula al vuelo
(126+47+38 ≈ 200 ficheros pequeños → milisegundos), así nunca queda rancio.

Uso:
  python3 tools/memoria_radar.py resucitar              # 1-3 dormidas relevantes al foco
  python3 tools/memoria_radar.py resucitar --json       # lo mismo en JSON (para la rutina)
  python3 tools/memoria_radar.py resucitar --dias 21    # "dormida" = sin tocar ≥21 días (def. 30)
  python3 tools/memoria_radar.py resucitar -n 3         # cuántas proponer (def. 3)
  python3 tools/memoria_radar.py enlazar feedback-x.md  # 2-3 [[enlaces]] sugeridos para esa memoria
  python3 tools/memoria_radar.py enlazar feedback-x.md --json
"""
import argparse
import glob
import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict

# --- Casa base / rutas (mismo criterio que el resto del gabinete) --------------------
HOME = os.environ.get("BTP_HOME") or os.path.expanduser("~")
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
# Carpeta de memorias del proyecto. Override por entorno SOLO para los tests.
MEMORY_DIR = os.environ.get("BTP_MEMORY_DIR") or os.path.join(
    HOME, ".claude", "projects", "-Users-polaris-claudecode", "memory"
)
CUMBRE = os.path.join(STATE, "cumbre.json")
SEGUIMIENTO = os.path.join(STATE, "seguimiento.json")

# BM25 — los MISMOS parámetros que kb.py (consistencia de ranking en todo el sistema).
K1, B = 1.5, 0.75


# --- Normalización / tokenización (idéntica a kb.py: acentos + minúsculas ES) --------
def _norm(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


_WORD = re.compile(r"[a-z0-9]+")


def _toks(s):
    return _WORD.findall(_norm(s))


# Stopwords ES (alineadas con cosecha_correcciones._STOP) para que la QUERY del foco no
# arrastre relleno. El BM25 ya penaliza términos frecuentes vía IDF, pero limpiar la
# query da mejores vecinos con corpus pequeño.
_STOP = set(
    """a al algo alguna algunas alguno algunos ante antes aqui asi aun cada como con
    contra cosa cosas cual cuando de del desde donde dos el ella ellas ello ellos en
    entonces entre era eran eras eres es esa esas ese eso esos esta estan estar este
    esto estos este ha hace hacer hacia han hasta hay la las le les lo los mas me mi mia
    mias mio mios mucho muchos muy nada ni no nos nosotros o os otra otras otro otros
    para pero poco por porque que quien se sea ser si siempre sin sobre solo son su sus
    tan te ti tu tus un una unas uno unos vamos van vez ya yo todo toda todos todas eh
    vale bueno creo hago haga puede puedo poder mira oye ahora luego tambien pues quiero
    necesito tengo tiene tienes tipo memoria feedback project reference""".split()
)


def _query_toks(s):
    out, seen = [], set()
    for w in _toks(s):
        if len(w) < 3 or w in _STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


# --- Expansión de vocabulario (25-jul-26) --------------------------------------------
# BM25 casa PALABRAS, no significados. Medido el 25-jul: la consulta «voy a mandar un
# correo a la oncóloga» no traía NINGUNA de las memorias de correo ni del muro, porque
# {{TITULAR}} escribe «correo/oncóloga» y las memorias dicen «mail/gmail/clínico/{{CONTACTO}}».
# Esta tabla es el puente, y es DETERMINISTA (sin modelo, sin red): cada término de la
# query añade sus vecinos de dominio al ranking. No sustituye al brazo vectorial de
# abajo; funciona aunque el modelo no esté disponible, que es el caso por defecto.
_ALIAS = {
    "correo": ["mail", "gmail", "imap", "outbox", "enviar", "borrador"],
    "correos": ["mail", "gmail", "enviar", "borrador"],
    "mail": ["correo", "gmail", "borrador"],
    "email": ["correo", "gmail", "mail"],
    "oncologa": ["medico", "medicos", "clinico", "contacto", "contacto", "doctora"],
    "oncologo": ["medico", "clinico", "contacto", "doctor"],
    "medica": ["clinico", "medico", "consejo"],
    "escribir": ["redactar", "borrador", "voz", "copy"],
    "mandar": ["enviar", "salida", "telegram", "correo"],
    "publicar": ["web", "copy", "redes", "post", "gate"],
    "contactar": ["contacto", "dossier", "presentacion", "gate"],
    "pagar": ["pago", "dinero", "tarjeta", "compra", "gate"],
    "comprar": ["compra", "compras", "amazon", "coste", "carrito"],
    "viaje": ["orbita", "vuelo", "tren", "{{CIUDAD}}", "logistica", "reserva"],
    "vuelo": ["viaje", "tren", "avion", "panico"],
    "reunion": ["agenda", "calendario", "cita"],
    "biopsia": ["muestra", "tumor", "{{CIUDAD}}", "{{CENTRO}}", "core"],
    "paper": ["articulo", "evidencia", "cita", "citas", "pubmed", "verificacion"],
    "papers": ["articulo", "evidencia", "citas", "pubmed"],
    "verificar": ["verificacion", "cotejar", "fuente", "evidencia", "sello"],
    "modelo": ["coste", "opus", "sonnet", "haiku", "tier"],
    "barato": ["coste", "gasto", "tokens", "carril"],
    "caro": ["coste", "gasto", "presupuesto"],
    "rama": ["git", "worktree", "paralelo", "fusion"],
    "commit": ["git", "rama", "historial"],
    "daemon": ["launchd", "plist", "activar", "salud"],
    "web": ["copy", "hero", "netlify", "preview", "diseno", "marca"],
    "diseno": ["marca", "design", "system", "ceci", "accesibilidad"],
    "tarea": ["tablero", "seguimiento", "vega", "tarjeta", "hilo"],
    "tareas": ["tablero", "seguimiento", "vega", "hilos"],
    "recordar": ["memoria", "leccion", "regla"],
    "olvidar": ["memoria", "leccion", "regla", "recall"],
    "whatsapp": ["wa", "tracker", "mensaje", "audio"],
    "telegram": ["salida", "lazo", "mensaje", "aviso"],
    "prensa": ["periodista", "medios", "epk", "contacto"],
    "redes": ["instagram", "linkedin", "twitter", "post", "dm"],
    "foto": ["imagen", "ocr", "captura"],
    "coste": ["gasto", "tokens", "presupuesto", "tier"],
    "seguridad": ["muro", "egress", "privacidad", "secreto"],
    "instalar": ["herramienta", "auditoria", "dependencia"],
}


def _expandir(terminos):
    """Añade los vecinos de dominio de cada término. Conserva el orden y no duplica."""
    if os.environ.get("BTP_SIN_ALIAS"):   # para medir el antes/después en el eval
        return list(terminos)
    out, vistos = [], set()
    for w in terminos:
        if w not in vistos:
            vistos.add(w)
            out.append(w)
    for w in list(out):
        for vecino in _ALIAS.get(w, ()):
            if vecino not in vistos:
                vistos.add(vecino)
                out.append(vecino)
    return out


# --- Lectura de una memoria (cuerpo sin frontmatter + tipo + enlaces existentes) -----
_FRONT = re.compile(r"^---\s*\n.*?\n---\s*\n", re.S)
_LINK = re.compile(r"\[\[([^\]]+)\]\]")


def _slug(name):
    """nombre de fichero (con o sin .md) → slug del [[enlace]]."""
    return name[:-3] if name.lower().endswith(".md") else name


# Fecha que {{TITULAR}} escribe en la lección: DD/MM o DD/MM/YY (año 2026 si no lo pone).
# Es la señal REAL de "última vez tocada" (el mtime miente tras una consolidación/worktree).
_FECHA = re.compile(r"\b([0-3]?\d)/([01]?\d)(?:/(\d{2,4}))?\b")


def _edad_por_fecha(body, hoy):
    """Días desde la fecha MÁS RECIENTE escrita en el cuerpo (no futura). None si no hay
    ninguna fecha válida → el llamador cae al mtime. Conservador: ignora 56/56, 20/20…"""
    import datetime as _dt
    mejor = None  # la fecha más reciente (= menos días dormida)
    for d, m, y in _FECHA.findall(body):
        d, m = int(d), int(m)
        if not (1 <= d <= 31 and 1 <= m <= 12):
            continue
        if y:
            y = int(y)
            y = 2000 + y if y < 100 else y
        else:
            y = hoy.year  # sin año → el año en curso (así escribe ella: "25/6")
        try:
            f = _dt.date(y, m, d)
        except ValueError:
            continue
        if f > hoy:          # fecha en el futuro = ruido (o año mal) → ignórala
            continue
        if mejor is None or f > mejor:
            mejor = f
    if mejor is None:
        return None
    return (hoy - mejor).days


def _cargar_memoria(path, hoy=None):
    """Devuelve dict {slug, fname, body, tipo, edad_dias, enlaces:set} o None (fail-soft).

    edad_dias = días desde la fecha escrita DENTRO de la lección (señal real); si no hay
    fecha legible, se cae al mtime del fichero. hoy se pasa para tests deterministas."""
    import datetime as _dt
    if hoy is None:
        hoy = _dt.date.today()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return None
    body = _FRONT.sub("", raw, count=1)  # quita el bloque YAML inicial si lo hay
    fname = os.path.basename(path)
    slug = _slug(fname)
    tipo = slug.split("-", 1)[0] if "-" in slug else "otro"
    enlaces = {_slug(m) for m in _LINK.findall(raw)}
    edad = _edad_por_fecha(body, hoy)
    if edad is None:  # sin fecha en el cuerpo → cae al mtime del fichero
        try:
            import time as _t
            edad = int((_t.time() - os.path.getmtime(path)) // 86400)
        except Exception:
            edad = 0
    return {"slug": slug, "fname": fname, "body": body, "tipo": tipo,
            "edad_dias": max(edad, 0), "enlaces": enlaces}


def _corpus(excluir=None):
    """Carga todas las memorias (menos MEMORY.md, que es el índice, y la excluida)."""
    out = []
    try:
        ficheros = glob.glob(os.path.join(MEMORY_DIR, "*.md"))
    except Exception:
        ficheros = []
    for p in sorted(ficheros):
        base = os.path.basename(p)
        # MEMORY.md y los `_indice-*.md` son ÍNDICES generados, no lecciones: si entran al
        # corpus, el recall acaba proponiendo un índice en vez de la memoria que hace falta.
        if base == "MEMORY.md" or base.startswith("_indice-") or base == excluir:
            continue
        m = _cargar_memoria(p)
        if m and len(m["body"].strip()) > 30:
            out.append(m)
    return out


# --- BM25 sobre el corpus de memorias (mismo esquema que kb.py) ----------------------
def _indexar(docs):
    """docs: lista de strings ya tokenizables. Devuelve (df, postings, lengths, N, avgdl)."""
    df, postings, lengths = defaultdict(int), defaultdict(list), []
    for i, txt in enumerate(docs):
        t = _toks(txt)
        lengths.append(len(t))
        tf = defaultdict(int)
        for w in t:
            tf[w] += 1
        for w, f in tf.items():
            df[w] += 1
            postings[w].append((i, f))
    N = len(docs)
    avgdl = sum(lengths) / max(N, 1)
    return df, postings, lengths, N, avgdl


def _bm25_scores(query_terms, df, postings, lengths, N, avgdl):
    scores = defaultdict(float)
    for w in set(query_terms):
        if w not in postings:
            continue
        idf = math.log((N - df[w] + 0.5) / (df[w] + 0.5) + 1)
        for ci, f in postings[w]:
            d = lengths[ci]
            scores[ci] += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * d / avgdl))
    return scores


# --- Brazo vectorial LOCAL (opcional) + fusión RRF (25-jul-26) -----------------------
# Segundo puente contra el fallo de paráfrasis, para lo que la tabla de alias no cubre.
# Reusa tools/kb_embed.py (multilingual-e5-small ONNX, CPU, egress-0: el modelo ya está
# en disco y en consulta no se abre ni un socket).
#
# CLAVE DE RENDIMIENTO: este código lo llama un hook en CADA mensaje de {{TITULAR}}, con 12s
# de timeout. Embeber las ~286 memorias al vuelo tardaría segundos, así que los vectores
# del corpus se PRECALCULAN (`memoria_radar.py vectores`) y aquí solo se embebe la query
# (~50ms). Si la caché no existe o el corpus ha cambiado, se degrada a BM25 sin ruido:
# el recall sigue funcionando, solo con un brazo menos.
VECTORES = os.path.join(STATE, "memoria_vectores.npz")
# Peso del brazo semántico en la fusión. APAGADO (0) por defecto: medido el 25-jul con
# `tests/test_recall_memoria.py` sobre 25 casos reales y 287 memorias, e5-small NO aporta
# recall (0.80 con y sin él) y con peso alto tumbaba casos que BM25 sí acertaba. El código
# y la caché se quedan porque el coste de mantenerlos es cero y el veredicto puede cambiar
# con otro modelo: se reactiva con BTP_PESO_SEMANTICO=0.35 y se vuelve a medir.
# La mejora que SÍ se midió (0.76 → 0.80, y de «nada» a «3 aciertos» en el caso del correo)
# viene de la tabla de alias de arriba.
PESO_SEMANTICO = float(os.environ.get("BTP_PESO_SEMANTICO", "0"))


def _texto_vectorizable(doc):
    return re.sub(r"\s+", " ", doc["body"]).strip()[:2000]


def _huella(doc):
    """Huella de UNA memoria. La caché es por memoria, no del corpus entero: si fuese
    global, escribir una sola memoria nueva apagaría el brazo semántico hasta la próxima
    reindexación (pasó el 25-jul en la primera versión). Así, lo que ya está vale."""
    import hashlib
    return hashlib.sha1(_texto_vectorizable(doc).encode("utf-8")).hexdigest()[:16]


def _kb_embed():
    """Importa la capa vectorial. None si no está (venv sin onnxruntime, modelo ausente)."""
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import kb_embed  # noqa: E402 — opcional por diseño
        return kb_embed if kb_embed.disponible() else None
    except Exception:
        return None


def _cache_vectores():
    """{slug: (huella, vector)} de la caché en disco. {} si no hay o no se puede leer."""
    if not os.path.exists(VECTORES):
        return {}
    try:
        import numpy as np
        z = np.load(VECTORES, allow_pickle=False)
        return {str(s): (str(h), z["mat"][i])
                for i, (s, h) in enumerate(zip(z["slugs"], z["huellas"]))}
    except Exception:
        return {}


def indexar_vectores(forzar=False):
    """Calcula los vectores que falten (incremental). Devuelve (ok, mensaje)."""
    ke = _kb_embed()
    if ke is None:
        return False, "capa vectorial no disponible (falta onnxruntime o el modelo)"
    docs = _corpus()
    if not docs:
        return False, "corpus vacío"
    try:
        import numpy as np
        cache = {} if forzar else _cache_vectores()
        pendientes = [d for d in docs if cache.get(d["slug"], (None,))[0] != _huella(d)]
        if pendientes:
            nuevos = ke.embed_texts([_texto_vectorizable(d) for d in pendientes],
                                    prefix="passage: ")
            if nuevos is None:
                return False, "embed_texts devolvió None"
            for d, v in zip(pendientes, nuevos):
                cache[d["slug"]] = (_huella(d), v)
        vigentes = {d["slug"] for d in docs}
        cache = {s: v for s, v in cache.items() if s in vigentes}  # olvida las borradas
        slugs = sorted(cache)
        os.makedirs(os.path.dirname(VECTORES), exist_ok=True)
        np.savez(VECTORES,
                 mat=np.array([cache[s][1] for s in slugs], dtype="float32"),
                 slugs=np.array(slugs),
                 huellas=np.array([cache[s][0] for s in slugs]))
        return True, "%d memorias vectorizadas (%d nuevas o cambiadas)" % (len(slugs), len(pendientes))
    except Exception as e:  # fail-soft: nunca rompe al llamador
        return False, "fallo indexando vectores: %s" % e


def _ranking_vectorial(query, docs):
    """[(indice_en_docs, similitud)] ordenado, o None si el brazo no está disponible.

    Las memorias sin vector al día (recién escritas, editadas) simplemente no participan;
    BM25 las sigue cubriendo. Degradar por una es peor que puntuar con las 285 que valen.
    """
    cache = _cache_vectores()
    if not cache:
        return None
    ke = _kb_embed()
    if ke is None:
        return None
    try:
        qv = ke.embed_texts([query], prefix="query: ")
        if qv is None:
            return None
        salida = []
        for ci, d in enumerate(docs):
            entrada = cache.get(d["slug"])
            if entrada and entrada[0] == _huella(d):
                salida.append((ci, float(entrada[1] @ qv[0])))
        if not salida:
            return None
        salida.sort(key=lambda x: -x[1])
        return salida
    except Exception:
        return None


def _rrf(rankings, k=60):
    """Reciprocal Rank Fusion PONDERADA: combina rankings sin calibrar escalas.
    rankings: lista de (orden, peso), con el orden de mejor a peor.

    El peso del brazo semántico es bajo A PROPÓSITO. Medido el 25-jul sobre las 287
    memorias: e5-small apelotona las similitudes (rango 0.78-0.88) y deja la memoria
    correcta en posiciones tan dispares como la 3 o la 136. A igual peso, ese ruido
    tumbaba casos que BM25 acertaba. Con peso bajo desempata sin mandar, que es lo
    único que sabe hacer bien aquí.
    """
    puntos = defaultdict(float)
    for orden, peso in rankings:
        for posicion, ci in enumerate(orden):
            puntos[ci] += peso / (k + posicion + 1)
    return puntos


# --- FOCO de hoy (brújula + hilos vivos) ---------------------------------------------
_ESTADOS_VIVOS = {"pendiente", "en_curso", "en curso", "esperando", "riesgo"}


def foco_actual():
    """Texto del foco de hoy para la query: el saliente activo de la brújula (cumbre) +
    los hilos vivos de seguimiento. Fail-soft: si un estado falta, devuelve lo que haya."""
    partes = []
    # Brújula: la ruta + el saliente "aquí estamos" (y su bloqueo/acción) — el cuello real.
    try:
        with open(CUMBRE, "r", encoding="utf-8") as f:
            c = json.load(f)
        partes.append(c.get("ruta_actual", ""))
        aqui = c.get("aqui_estamos")
        for s in c.get("salientes", []):
            if not isinstance(s, dict):
                continue
            # el saliente activo pesa más; también los demás en curso/riesgo
            if s.get("id") == aqui or s.get("estado") in ("en_curso", "riesgo"):
                partes += [s.get("titulo", ""), s.get("bloqueo", ""),
                           s.get("siguiente_accion", "")]
    except Exception:
        pass
    # Hilos vivos de la asistente (Vega) — lo que está abierto AHORA.
    try:
        with open(SEGUIMIENTO, "r", encoding="utf-8") as f:
            s = json.load(f)
        for h in (s.get("hilos") or []):
            if not isinstance(h, dict):
                continue
            if str(h.get("estado", "")).lower() in _ESTADOS_VIVOS:
                partes += [h.get("titulo", ""), h.get("texto", ""), h.get("nota", "")]
    except Exception:
        pass
    return " ".join(p for p in partes if p)


# --- 1) RESUCITAR dormidas relevantes ------------------------------------------------
def resucitar(dias_dormida=30, n=3, foco_texto=None):
    """Memorias DORMIDAS (sin tocar ≥ dias_dormida) pero RELEVANTES al foco de hoy.

    Devuelve lista de dicts: {slug, fname, dias_sin_tocar, score, snippet}.
    Conservador: solo propone las que de verdad casan con el foco (score > 0).
    """
    foco = foco_texto if foco_texto is not None else foco_actual()
    # La query se EXPANDE con los vecinos de dominio: {{TITULAR}} dice «correo a la oncóloga»
    # y las memorias dicen «mail/gmail/clínico». Sin este puente, BM25 no las encuentra.
    q = _expandir(_query_toks(foco))
    docs = _corpus()
    if not q or not docs:
        return []
    df, postings, lengths, N, avgdl = _indexar([d["body"] for d in docs])
    scores = _bm25_scores(q, df, postings, lengths, N, avgdl)
    orden_bm25 = [ci for ci, sc in sorted(scores.items(), key=lambda x: -x[1]) if sc > 0]

    # Segundo brazo: similitud semántica local, si hay caché de vectores al día.
    vectorial = _ranking_vectorial(foco, docs)
    if vectorial:
        orden_vec = [ci for ci, _s in vectorial[:10]]
        puntos = _rrf([(orden_bm25[:50], 1.0), (orden_vec, PESO_SEMANTICO)])
        orden_final = sorted(puntos, key=lambda ci: -puntos[ci])
        puntuar = lambda ci: round(puntos[ci], 4)          # noqa: E731 — score = RRF
    else:
        orden_final = orden_bm25
        puntuar = lambda ci: round(scores[ci], 2)          # noqa: E731 — score = BM25

    cand = []
    for ci in orden_final:
        d = docs[ci]
        if d["edad_dias"] < dias_dormida:   # re-anotada hace poco → NO está dormida
            continue
        cand.append({
            "slug": d["slug"],
            "fname": d["fname"],
            "dias_sin_tocar": d["edad_dias"],
            "score": puntuar(ci),
            "snippet": re.sub(r"\s+", " ", d["body"]).strip()[:200],
        })
        if len(cand) >= n:
            break
    return cand


# --- 1bis) RECALL para el hook: la ficha ENTERA, no un recorte -----------------------
# Hasta el 25-jul el hook inyectaba 200 caracteres del cuerpo, cortados a mitad de frase,
# bajo el rótulo «orientación, no órdenes». Con eso llegaba el titular de la lección pero
# no el CÓMO aplicarla, y el rótulo invitaba a saltársela. Aquí va la ficha útil: la
# lección y, si están, sus líneas *Why* y *How to apply*.
_LINEA_UTIL = re.compile(r"^\s*\*{0,2}(Why|Por qu[eé]|How to apply|C[oó]mo aplicar)\*{0,2}\s*:",
                         re.I)


def ficha(doc, largo=520):
    """Texto de la memoria pensado para inyectar: lo esencial, sin el relleno."""
    lineas = [ln.strip() for ln in doc["body"].splitlines() if ln.strip()]
    utiles = []
    for i, ln in enumerate(lineas):
        if not _LINEA_UTIL.match(ln):
            continue
        utiles.append(ln)
        # «**How to apply:**» a veces es solo el rótulo y el contenido va debajo:
        # sin esto, la ficha inyecta un encabezado vacío, que es peor que no ponerlo.
        if ln.rstrip().endswith((":", ":**")) and i + 1 < len(lineas):
            utiles.append(lineas[i + 1])
    # Las dos primeras líneas ya suelen traer el *Why* (la lección + su porqué), así que
    # concatenar `utiles` a pelo lo inyectaba DOS VECES en cada prompt: medido el 30-jul-26
    # en feedback-ahorrar-tokens-no-sobreexplicar, ~150 caracteres repetidos por ficha.
    cabeza = lineas[:2]
    utiles = [ln for ln in utiles if ln not in cabeza]
    cuerpo = " ".join(cabeza + utiles) if utiles else " ".join(lineas[:4])
    cuerpo = re.sub(r"\s+", " ", cuerpo).strip()
    return cuerpo[:largo] + ("…" if len(cuerpo) > largo else "")


def bloque_recall(consulta, n_fichas=2, n_extra=3):
    """Bloque de contexto listo para el hook. Cadena vacía si no hay nada que decir."""
    items = resucitar(dias_dormida=0, n=n_fichas + n_extra, foco_texto=consulta)
    if not items:
        return ""
    por_slug = {d["slug"]: d for d in _corpus()}
    fuera = []
    fuera.append("REGLAS DE TITULAR QUE APLICAN A ESTE MENSAJE.")
    fuera.append("Son suyas y ya las aprobó: aplícalas, no las repitas de vuelta ni las trates")
    fuera.append("como sugerencias. Si alguna contradijera el muro, manda el muro.")
    for it in items[:n_fichas]:
        d = por_slug.get(it["slug"])
        if not d:
            continue
        fuera.append("")
        fuera.append("▸ [[%s]]" % it["slug"])
        fuera.append("  " + ficha(d))
    resto = [it["slug"] for it in items[n_fichas:]]
    if resto:
        fuera.append("")
        fuera.append("También cerca (ábrelas si tocan): " + ", ".join("[[%s]]" % s for s in resto))
    return "\n".join(fuera)


# --- 2) ENLAZAR: sugerir [[enlaces]] para una memoria --------------------------------
def enlazar(fname, n=3):
    """Para la memoria `fname`, devuelve hasta n vecinas más parecidas que NO estén ya
    enlazadas. Devuelve dict {ok, sugerencias:[{slug, score, motivo}], ya_enlazadas}.
    """
    base = os.path.basename(fname)
    path = os.path.join(MEMORY_DIR, base)
    me = _cargar_memoria(path)
    if me is None:
        return {"ok": False, "error": "no se pudo leer %s" % base, "sugerencias": []}
    q = _query_toks(me["body"])
    if not q:
        return {"ok": True, "sugerencias": [], "ya_enlazadas": sorted(me["enlaces"]),
                "nota": "memoria muy corta o sin términos distintivos"}
    docs = _corpus(excluir=base)           # vecinas = todas menos ella misma
    if not docs:
        return {"ok": True, "sugerencias": [], "ya_enlazadas": sorted(me["enlaces"])}
    df, postings, lengths, N, avgdl = _indexar([d["body"] for d in docs])
    scores = _bm25_scores(q, df, postings, lengths, N, avgdl)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    sug = []
    for ci, sc in ranked:
        if sc <= 0:
            continue
        d = docs[ci]
        if d["slug"] in me["enlaces"]:     # ya está enlazada → no la repitas
            continue
        sug.append({
            "slug": d["slug"],
            "score": round(sc, 2),
            "snippet": re.sub(r"\s+", " ", d["body"]).strip()[:140],
        })
        if len(sug) >= n:
            break
    return {"ok": True, "sugerencias": sug, "ya_enlazadas": sorted(me["enlaces"])}


# --- CLI -----------------------------------------------------------------------------
def _print_resucitar(items, foco):
    if not items:
        print("(sin dormidas relevantes al foco de hoy)")
        return
    print("🔔 Quizá relevante ahora (memorias dormidas que casan con el foco):")
    for it in items:
        print("\n  [[%s]]   (rel %.1f · sin tocar %d días)"
              % (it["slug"], it["score"], it["dias_sin_tocar"]))
        print("     %s" % it["snippet"])


def _print_enlazar(res, fname):
    if not res.get("ok"):
        print("✗ %s" % res.get("error", "error"))
        return
    sug = res["sugerencias"]
    if not sug:
        print("(sin vecinas nuevas que sugerir para %s)" % fname)
        if res.get("nota"):
            print("  nota: %s" % res["nota"])
        return
    print("🔗 Enlaces sugeridos para %s (vecinas por similitud, aún no enlazadas):" % fname)
    for s in sug:
        print("  [[%s]]   (rel %.1f)" % (s["slug"], s["score"]))
        print("     %s" % s["snippet"])
    if res.get("ya_enlazadas"):
        print("  (ya enlaza: %s)" % ", ".join(res["ya_enlazadas"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    pr = sub.add_parser("resucitar", help="dormidas relevantes al foco de hoy")
    pr.add_argument("--dias", type=int, default=30, help="umbral de 'dormida' (def. 30); 0 = no filtrar por antigüedad")
    pr.add_argument("-n", type=int, default=3, help="cuántas proponer (def. 3)")
    pr.add_argument("--query", default=None, help="texto libre como query BM25 (ignora el foco de hoy si se pasa; para recall en el chat)")
    pr.add_argument("--json", action="store_true")

    sub.add_parser("vectores", help="precalcula los vectores del corpus (brazo semántico)")

    prc = sub.add_parser("recall", help="bloque de reglas para inyectar (lo usa el hook)")
    prc.add_argument("--query", required=True, help="el mensaje de {{TITULAR}}")
    prc.add_argument("--fichas", type=int, default=2, help="cuántas van con ficha entera")
    prc.add_argument("--extra", type=int, default=3, help="cuántas más se citan por slug")

    pe = sub.add_parser("enlazar", help="[[enlaces]] sugeridos para una memoria")
    pe.add_argument("fichero", help="nombre del .md (en la carpeta de memorias)")
    pe.add_argument("-n", type=int, default=3, help="cuántos sugerir (def. 3)")
    pe.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "resucitar":
        foco = args.query if args.query is not None else foco_actual()
        items = resucitar(dias_dormida=args.dias, n=args.n, foco_texto=foco)
        if args.json:
            print(json.dumps({"foco_terminos": _query_toks(foco)[:20],
                              "candidatos": items}, ensure_ascii=False, indent=2))
        else:
            _print_resucitar(items, foco)
        return 0

    if args.cmd == "recall":
        texto = bloque_recall(args.query, n_fichas=args.fichas, n_extra=args.extra)
        if texto:
            print(texto)
        return 0

    if args.cmd == "vectores":
        ok, msg = indexar_vectores()
        print(("✅ " if ok else "ℹ️  ") + msg)
        return 0 if ok else 1

    if args.cmd == "enlazar":
        res = enlazar(args.fichero, n=args.n)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            _print_enlazar(res, args.fichero)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
