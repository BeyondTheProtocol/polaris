#!/usr/bin/env python3
"""tools/publicar.py — genera el ÁRBOL PUBLICABLE de Polaris, sin tocar la casa base.

POR QUÉ UN GENERADOR Y NO UNA EDICIÓN A MANO: el arnés está en producción con un solo
usuario y sus agentes están escritos en su voz. Editar 90 ficheros de `.claude/` para
despersonalizarlos rompería el sistema vivo y crearía dos copias divergentes. Esto en
cambio DERIVA el repo público desde el privado cada vez que haga falta: reproducible,
auditable y testeable, que es como funciona el resto del repo.

QUÉ HACE, en orden:
  1. Copia solo lo que entra (INCLUIR), nunca lo que no (EXCLUIR gana siempre).
  2. Sustituye el nombre propio por `{{TITULAR}}` en contenido y en nombres de fichero.
  3. VERIFICA el resultado contra los términos vetados y ABORTA si queda alguno.

FAIL-CLOSED: si el barrido final encuentra un solo término vetado, no se escribe un repo
a medias — se borra el destino y se sale con rc=1. Un árbol "casi limpio" publicado es
peor que ninguno, porque parece revisado.

Uso:
  python3 tools/publicar.py <destino> [--forzar]
  python3 tools/publicar.py --selftest
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rutas_overlay(nombre):
    """Las rutas del overlay gitignored `nombre`: la de este árbol y la de casa base.

    Los overlays no viajan a un worktree —son gitignored—, así que desde una rama se leían
    vacíos. Eso NO abría un agujero, porque `_overlay_ok()` se planta y no publica; pero
    dejaba `publicar.py` inservible justo donde se trabaja siempre, y obligaba a generar el
    árbol público desde casa base. Se va a buscarlos donde están, que es el mismo arreglo que
    ya hicieron `seguimiento._cargar_nombres_deny` y `subir_historial_drive._ruta_identidad`
    el 20-sep-26. Devuelve las dos sin repetir: quien une (la deny-list) las recorre, y quien
    elige (el perfil, que es un objeto y no una lista) se queda con la primera que exista.
    """
    rutas, vistos = [], set()
    for base in (ROOT, _casa_base_si_soy_worktree()):
        if not base:
            continue
        r = os.path.join(base, "tools", nombre)
        real = os.path.realpath(r)
        if real not in vistos:
            vistos.add(real)
            rutas.append(r)
    return tuple(rutas)


def _casa_base_si_soy_worktree():
    """Casa base, PERO solo si este árbol es un worktree suyo. Si no, None.

    La condición no es cosmética, es la que mantiene el muro donde estaba. `_overlay_ok()`
    existe para que un árbol sin overlay NO publique en crudo, y heredar el overlay de
    ~/claudecode a cualquier directorio convertiría ese fail-closed en un fail-open en
    cualquier máquina que tenga el repo en casa (lo caza
    tests/test_publicar_fuga.py::test_sin_overlay_aborta_en_vez_de_publicar_en_crudo).
    Un worktree es otra cosa: es el MISMO repo, con los mismos permisos, al que los ficheros
    gitignored no llegan solo por cómo funciona git. Ahí ir a buscarlos no relaja nada.

    Se reconoce por lo que dice git, no por la ruta: en un worktree, `.git` es un FICHERO con
    `gitdir: <casa base>/.git/worktrees/<nombre>`.
    """
    punto_git = os.path.join(ROOT, ".git")
    if not os.path.isfile(punto_git):        # casa base (es un dir) o un árbol cualquiera
        return None
    try:
        with io.open(punto_git, encoding="utf-8") as fh:
            gitdir = fh.read().strip()
    except OSError:
        return None
    if not gitdir.startswith("gitdir:"):
        return None
    p = gitdir.split(":", 1)[1].strip()
    marca = os.path.join(".git", "worktrees")
    if marca not in p:
        return None
    casa = os.path.dirname(p[:p.index(marca)].rstrip(os.sep) + os.sep) or None
    return casa if casa and os.path.isdir(os.path.join(casa, "tools")) else None


def _overlay(nombre):
    """El primer overlay `nombre` que exista (este árbol manda sobre casa base)."""
    for r in _rutas_overlay(nombre):
        if os.path.isfile(r):
            return r
    return os.path.join(ROOT, "tools", nombre)

# ── Qué entra. Lo que no está aquí, no se publica. ───────────────────────────────────────
INCLUIR = (
    "tools", "tests", ".claude", "pipeline", "evals", "docs",
    ".github",                  # el CI que revisa lo que llega por PR (19-sep-26)
    "requirements.txt", ".gitignore", ".gitleaks.toml", ".gitleaksignore",
    "README.md", "CONTRIBUTING.md", "LICENSE", "AGENTS.md", "CHANGELOG.md",
    # Blindaje legal (19-sep-26): sin estos, la AGPL protege a medias — un repo sin línea de
    # copyright obliga a demostrar autoría, y sin acuerdo de contribución el titular pierde la
    # capacidad de relicenciar (y por tanto de vender una licencia comercial a quien no quiera
    # abrir su código, que es justo el freno al «me lo cogen y lo monetizan cerrado»).
    "NOTICE", "SECURITY.md", "CODE_OF_CONDUCT.md", "CITATION.cff",
    "ACUERDO-CONTRIBUCION.md",
    "CLAUDE.md",                # la constitución: el muro que el README describe
    ".mcp.json",                # qué conectores MCP usa el arnés (sin secretos)
)

# ── Qué NO entra jamás, aunque caiga dentro de INCLUIR. EXCLUIR gana. ────────────────────
EXCLUIR_DIR = {
    "00_FUENTE-DE-VERDAD",      # el contenido: informes, correo, mensajería
    "helptitular-site",          # la web de la campaña: es el caso, no el arnés
    "G0DM0D3",                  # ya tiene su propio repo público
    "state",                    # tools/state: estado vivo del lazo 24/7
    "worktrees", "__pycache__", ".git", ".venv", "node_modules",
    "_cajita", "_PRIVADO_CORREO", "_PRIVADO_DMS", "_PRIVADO_NUCLEO",
}
# Detectores de PHI: su contenido ES el diccionario de datos personales que buscan (nombre
# completo, las variantes de la fecha de nacimiento, la lista de médicos). Sustituirlos los
# deja inútiles y dejarlos es publicar la ficha. Se excluyen, y el README dice que esa lista
# se configura en local.
# Vacía desde el 16-sep-2026: los detectores de PHI ya no llevan dentro los datos que
# buscan — viven en overlays `*.local.json` gitignored. Se publican enteros y funcionan;
# lo que falta es la lista, que cada cual escribe en local.
# 19-sep-2026: estas baterías NO se publican porque dependen de lo que aquí se sustituye o
# de estado vivo que allí no existe — y publicadas salían ROJAS en el CI del repo público
# («{{CONTACTO}} ({{CENTRO}}) sigue siendo persona» falla justo porque la despersonalización hizo
# su trabajo). `_coser_runner` las comenta en `tests/test_all.sh` para que el runner público
# no llame a ficheros ausentes. Un CI que nace en rojo no lo mira nadie.
EXCLUIR_FICHERO = {
    os.path.join("tests", n) for n in (
        "test_correo.py",               # el triaje de correo lleva dentro nombres reales
        "test_correo_responder.py",
        "test_adjuntos_clinicos.py",    # adjuntos del historial clínico
        "test_historial.py",            # el archivo clínico: centros, pruebas, fechas
        "test_historial_indexado.py",
        "test_deuda_escalada.py",       # exige el libro de deuda del lazo (estado vivo)
        "test_fugu_egress.py",          # deny-list de la caja aislada, local
    )
}

# Ficheros que el overlay local declara privados (`no_publicar` en perfil.local.json). La
# lista vive allí y no aquí: nombrarlos en el código publicado ya contaría que existen.
def _no_publicar():
    try:
        with io.open(_overlay("perfil.local.json"), encoding="utf-8") as fh:
            return frozenset(os.path.normpath(r) for r in (json.load(fh).get("no_publicar") or []))
    except (OSError, ValueError):
        return frozenset()

EXCLUIR_PAT = (
    re.compile(r"^_PRIVADO"),
    re.compile(r"^\.venv"),
    re.compile(r"\.pdf$", re.I),
    re.compile(r"\.(db|npy|sqlite3?)$", re.I),
    re.compile(r"^BANDEJA\.md$"),
    # Notas internas de junio. Fuera por tres razones, no solo por el rastro clínico: usan
    # las cajas reales como ejemplo extenso (18 menciones a una ciudad y 13 a una prueba en
    # el de diseño), están desactualizadas —el REPLANTEO diagnostica un fail-open ya
    # arreglado— y el README explica el concepto sin ellas. Despersonalizarlas a fondo
    # dejaría un texto degradado que explica peor que el README.
    re.compile(r"^(REPLANTEO|DISENO|MACBOOK)-.*\.md$"),
    re.compile(r"\.bak(-|\.|$)"),
    re.compile(r"^\.DS_Store$"),
)

# ── La despersonalización. Orden importa: lo más largo primero. ──────────────────────────
def _titular(clave):
    """Datos del titular desde el overlay (`nombre`, `apellidos`, `nacimiento`, `contactos`,
    `telefonos`).

    No pueden vivir en este fichero: `publicar.py` se publica a sí mismo, así que llevarlos
    aquí lo convierte en la ficha que pretende borrar — el mismo fallo que tenían los
    detectores de PHI antes de sacarlos a sus overlays.
    """
    try:
        with io.open(_overlay("perfil.local.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return []
    v = (d.get("titular") or {}).get(clave)
    if isinstance(v, str):
        return [v]
    return list(v or [])


def _nombre_titular():
    n = _titular("nombre")
    return re.escape(n[0]) if n else r"(?!)"      # sin overlay, no casa nada


def _perfil():
    """El perfil clínico del caso, desde `tools/perfil.local.json` (gitignored).

    POR QUÉ NO BASTA CON VETAR GENES: `RB1`, `TP53` o `FGFR1` viven en el árbol como
    VOCABULARIO técnico — `_GEN_SHAPE_RE` los usa de ejemplo y `_lexico_publico.GENES` es
    la lista anti-fuga genérica. Sustituirlos rompería el detector, igual que habría pasado
    con «metastatic breast cancer». Lo privado no es el símbolo del gen: es el gen PEGADO a
    un hallazgo («RB1 + Ki-67 {{N}}% + NE {{N}}%») y la sección que los lista como suyos.

    Por eso el overlay trae dos cosas: `sustituciones` quirúrgicas (cifra, variante,
    subtipo) y `bloques` enteros (la sección «Dianas a vigilar») que se reemplazan por una
    nota. Así el árbol público conserva la ESTRUCTURA —un comité que vigila dianas y un
    radar que deriva sus consultas— sin el CONTENIDO, que es la ficha de una persona.
    """
    try:
        with io.open(_overlay("perfil.local.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {"sustituciones": [], "bloques": []}
    return {"sustituciones": d.get("sustituciones") or [],
            "bloques": d.get("bloques") or []}


def _despersonalizar_perfil(texto):
    """Aplica el perfil: primero los bloques enteros, luego las sustituciones sueltas."""
    for b in _perfil()["bloques"]:
        desde, hasta, por = b.get("desde"), b.get("hasta"), b.get("por", "")
        if not desde or desde not in texto:
            continue
        i = texto.index(desde)
        j = texto.find(hasta, i + len(desde)) if hasta else -1
        fin = j if j != -1 else len(texto)
        texto = texto[:i] + por + texto[fin:]
    for par in _perfil()["sustituciones"]:
        if len(par) == 2:
            texto = re.sub(par[0], par[1], texto, flags=re.I)
    return texto


def _nombres_de_terceros():
    """Los nombres que `seguimiento.py` redacta, leídos de su overlay local. Van a
    `{{CONTACTO}}` en el árbol público: son personas que no consintieron aparecer."""
    # Se UNEN los dos overlays en vez de elegir uno: en una deny-list, de más es seguro y de
    # menos es una fuga. Mismo criterio que `seguimiento._cargar_nombres_deny`.
    crudos = set()
    for ruta in _rutas_overlay("nombres.local.json"):
        try:
            with io.open(ruta, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        crudos |= {str(x) for x in d.get("nombres", [])}
    if not crudos:
        return ()
    # solo los de ≥4 letras: los cortos colisionan con palabras corrientes
    nombres = {x for x in crudos if len(x) >= 4}
    nombres |= {str(x) for x in _titular("contactos") if len(str(x)) >= 4}
    return tuple(sorted(nombres, key=len, reverse=True))


# ── Fichas de persona: cada una declara su anonimato, y el espejo lo cumple solo ─────────
# 25-sep-2026, FUGA REAL: una ficha de `tools/config/personas/` que pide no nombrar a esa
# persona salió al espejo con el nombre de pila tapado y el APELLIDO en claro (en `persona`,
# en el handle y en la URL de su perfil). La deny-list solo tapaba lo que alguien había
# copiado a mano en `nombres.local.json`, y el apellido nunca se copió. El arreglo es de
# CLASE: la ficha es la fuente de verdad de esa persona, así que la ficha aporta sus nombres.
#   · `anonimato: "alto"`   → nombre completo, cada palabra de `persona`, handles y URL de
#                              perfil van a `{{CONTACTO}}` y entran en el barrido final.
#   · `anonimato: "publico"`→ figura pública; su ficha puede salir tal cual.
#   · sin campo o con otro valor → NO se publica nada (rc=3). Una ficha nueva sin nivel es
#     justo cómo se cuela la siguiente fuga.
# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
PERSONAS_DIR = os.path.join("tools", "config", "personas")
NIVELES_ANONIMATO = ("alto", "publico")
_CLAVES_IDENTIDAD = ("handle", "url", "email", "telefono")
_TILDES = {"a": "aáàäâ", "e": "eéèëê", "i": "iíìïî", "o": "oóòöô", "u": "uúùüû", "n": "nñ", "c": "cç"}


def _fichas_personas():
    """[(ruta relativa, dict o None si no se puede leer)] de las fichas versionadas o no."""
    base = os.path.join(ROOT, PERSONAS_DIR)
    try:
        nombres = sorted(f for f in os.listdir(base) if f.endswith(".json"))
    except OSError:
        return []
    out = []
    for f in nombres:
        try:
            with io.open(os.path.join(base, f), encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            d = None
        out.append((os.path.join(PERSONAS_DIR, f), d if isinstance(d, dict) else None))
    return out


def _personas_sin_cubrir():
    """Las fichas que el espejo no sabe tratar: ilegibles, sin `anonimato` válido, o `alto`
    sin `persona`. Cualquiera de ellas bloquea la publicación entera."""
    malas = []
    for rel, d in _fichas_personas():
        if d is None:
            malas.append("%s → no es un JSON legible" % rel)
        elif d.get("anonimato") not in NIVELES_ANONIMATO:
            malas.append("%s → falta `anonimato` (%s)" % (rel, " | ".join(NIVELES_ANONIMATO)))
        elif d["anonimato"] == "alto" and not str(d.get("persona") or "").strip():
            malas.append("%s → `anonimato: alto` sin `persona`" % rel)
    return malas


def _valores_identidad(x):
    """Handles, URLs, correos y teléfonos, estén al nivel que estén de la ficha."""
    if isinstance(x, dict):
        for k, v in x.items():
            if k in _CLAVES_IDENTIDAD and isinstance(v, str) and v.strip():
                yield v.strip()
            else:
                yield from _valores_identidad(v)
    elif isinstance(x, list):
        for v in x:
            yield from _valores_identidad(v)


def _terminos_de_personas():
    """Lo que las fichas `alto` mandan tapar, del más largo al más corto."""
    terminos = set()
    for _rel, d in _fichas_personas():
        if not d or d.get("anonimato") != "alto":
            continue
        persona = " ".join(str(d.get("persona") or "").split())
        if persona:
            terminos.add(persona)
            terminos |= {t for t in persona.split() if len(t) >= 2}
        for v in _valores_identidad(d):
            if "://" in v:
                # de la URL de perfil se veta el último tramo (el handle), no el dominio
                v = v.rstrip("/").rsplit("/", 1)[-1]
            v = v.lstrip("@")
            if len(v) >= 3:
                terminos.add(v)
    return tuple(sorted(terminos, key=lambda t: (-len(t), t)))


def _sin_tildes(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _rx_termino(t):
    """El patrón de un término de ficha. Largos (≥4): sin distinguir mayúsculas ni tildes
    (la misma persona sale como «{{CONTACTO}}» y «{{CONTACTO}}»), también justo tras `\\b` o `_`,
    y pegados por delante de una Mayúscula, un `_` o un dígito (`ApellidoBot`, `apellido_ok`)
    pero NO de una minúscula: así «Madrid» no cae por contener un nombre de pila. Cortos
    (<4): colisionan con código (`zipfile.ZipFile`), así que solo casan con la grafía exacta
    de la ficha y como palabra suelta."""
    if len(t) < 4:
        return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(t))
    cuerpo = "".join(
        "[%s]" % _TILDES[c.lower()] if c.lower() in _TILDES else (r"\s+" if c.isspace() else re.escape(c))
        for c in _sin_tildes(t))
    return re.compile(r"(?:(?<=\\b)|(?<![A-Za-z0-9áéíóúñÁÉÍÓÚÑ]))%s(?-i:(?![a-záéíóúñ]))" % cuerpo, re.I)


def _contacto_pegado(m):
    """Como `_contacto`, pero sin llaves si el término va pegado a un identificador: dentro
    de `ApellidoBot` o `apellido_ok`, `{{CONTACTO}}` deja de ser código válido."""
    t = m.group(0)
    s, j = m.string, m.end()
    if t.isupper():
        return "CONTACTO"
    if t.islower():
        return "contacto"
    return "Contacto" if (j < len(s) and (s[j].isalnum() or s[j] == "_")) else "{{CONTACTO}}"


def _contacto(m):
    """Los nombres viven también DENTRO de identificadores (`contacto_ok`,
    `INVARIANTES_..._CONTACTO`): ahí `{{CONTACTO}}` no es Python válido. Se conserva la forma
    del original, que es lo que decide si el resultado sigue siendo código."""
    t = m.group(0)
    if t.isupper():
        return "CONTACTO"
    if t.islower():
        return "contacto"
    return "{{CONTACTO}}"


def _por_caso(m):
    """Sustituye el nombre y CUALQUIER handle derivado (titular, titular…)
    preservando el caso, porque viven en constantes, emails, rutas y plists por igual.

    19-sep-2026: el nombre vivía además PEGADO dentro de palabras más largas —etiquetas de
    correo (`HelpNombre/Prensa`), dominios (`helpnombre.com`), handles (`RealNombreBot`) y
    hasta identificadores de test—, y ahí el nombre seguía leyéndose entero. Se sustituye
    también dentro del compuesto, pero SIN llaves: `Help{{TITULAR}}` dentro de un
    identificador Python no compila, y el árbol público tiene que seguir corriendo."""
    t = m.group(0)
    s, i, j = m.string, m.start(), m.end()
    pegado = (i > 0 and (s[i - 1].isalnum() or s[i - 1] == "_")) or (j < len(s) and s[j].isalnum())
    if t.isupper():
        return "TITULAR"
    if t[0].isupper():
        return "Titular" if pegado else "{{TITULAR}}"
    return "titular"


# Nombres propios que en español son también palabras corrientes. Solo para estos manda la
# mayúscula al sustituir; el resto se sustituye sin distinguir caso.
_NOMBRES_QUE_SON_PALABRA = {"esperanza", "rosario", "pilar", "consuelo", "dolores", "paz",
                            "soledad", "cruz", "alba", "aurora", "gloria", "mar", "sol",
                            "angeles", "ángeles", "milagros", "amparo", "remedios"}

_NOMBRE = _nombre_titular()

# 19-sep-2026, FUGA REAL: el nombre escrito DENTRO de un patrón (`re.compile(r"\bNombre\b")`)
# sobrevivía entero, y el barrido final tampoco lo veía — así salió publicado. La causa es el
# lookbehind: el carácter anterior es la `b` de `\b`, una letra, así que `(?<![a-z0-9])` no
# dejaba casar. Un fichero que VETA un término lo escribe por definición, y ese es justo el
# sitio donde más caro sale. `_TRAS` añade la excepción: vale también justo después de `\b`.
_TRAS = r"(?:(?<=\\b)|(?<![a-z0-9]))"

SUSTITUCIONES = (
    (re.compile(r"voz-%s" % _NOMBRE, re.I), "voz-titular"),   # slug de agente + referencias
    (re.compile(r"%s[a-z0-9]*" % _NOMBRE, re.I), _por_caso),  # handle, compuesto o pegado
) + tuple(
    (re.compile(r"%s%s,?\s*" % (_TRAS, re.escape(a)), re.I), "") for a in _titular("apellidos")
) + tuple(
    (re.compile(p_, re.I), "{{FECHA_NAC}}") for p_ in _titular("nacimiento")
) + tuple(
    # Las fichas de persona con `anonimato: alto` (ver `_terminos_de_personas`). Van ANTES
    # que la deny-list: el nombre completo tiene que caer entero, no a trozos.
    (_rx_termino(t), _contacto_pegado) for t in _terminos_de_personas()
) + tuple(
    # Los nombres de terceros (contactos, colaboradores, médicos), del overlay.
    # Por defecto van SIN distinguir mayúsculas: un nombre corto metido dentro de un detector
    # (`…|contacto|…` en una regex) tiene que caer igual que en prosa — es el mismo fallo que
    # publicó un término vetado el 17-sep.
    (re.compile(r"(?:(?<=\\b)|\b|(?<=_))%s(?=[A-Z_]|\b)" % re.escape(n), re.I), _contacto)
    for n in _nombres_de_terceros() if n.lower() not in _NOMBRES_QUE_SON_PALABRA
) + tuple(
    # Excepción para los que TAMBIÉN son palabra corriente ({{CONTACTO}}, Rosario, Pilar…): ahí
    # sí manda la mayúscula, o se destroza texto legítimo. El caso que lo destapó: el NOTICE
    # decía «se distribuye con la esperanza de que sea útil» —la fórmula literal de la GPL— y
    # salía «con la contacto de que sea útil».
    (re.compile(r"(?:(?<=\\b)|\b|(?<=_))%s(?=[A-Z_]|\b)" % re.escape(n.capitalize())), _contacto)
    for n in _nombres_de_terceros() if n.lower() in _NOMBRES_QUE_SON_PALABRA
) + tuple(
    (re.compile(r"(?:(?<=_)%s\b|\b%s(?=_))" % (re.escape(n.lower()), re.escape(n.lower()))), _contacto)
    for n in _nombres_de_terceros() if n.lower() in _NOMBRES_QUE_SON_PALABRA
) + tuple(
    (re.compile(r"(?<![A-Z0-9])%s(?![A-Z0-9])" % re.escape(n.upper())), _contacto)
    for n in _nombres_de_terceros() if n.lower() in _NOMBRES_QUE_SON_PALABRA
) + (
    (re.compile(r"\bECOG\s*[0-4]\b", re.I), "ECOG {{N}}"),
)
RENOMBRAR = {"voz-titular.md": "voz-titular.md"}
MARCA_ESPEJO = ".espejo-publico"   # la lee tests/_entorno.es_espejo()

# ── El barrido final. Si algo de esto sobrevive, no se publica. ──────────────────────────
# Nombre propio, y el vocabulario clínico que identifica a UNA persona (no el genérico:
# "biopsia" u "oncología" en abstracto describen el dominio, no a nadie).
VETADOS = (
    re.compile(r"\bECOG\s*[0-4]\b", re.I),            # estado funcional: dato de UNA persona
)
# El resto del veto se DERIVA del overlay (nombre, apellidos, fecha de nacimiento, contactos
# y perfil): si el overlay dice que algo es privado, el barrido comprueba que desapareció.
# Tenerlo aquí en claro convertía a este fichero en la ficha que pretende borrar.

BINARIO = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
           ".woff", ".woff2", ".ttf", ".otf", ".db", ".npy", ".sqlite", ".sqlite3")


def es_texto(ruta):
    """¿Se puede leer como UTF-8? Decidirlo por el SUFIJO dejaba fuera los ficheros sin
    extensión — y el `.gitignore` llevaba nombre completo y fecha de nacimiento, porque
    para ignorar una carpeta hay que nombrarla. Se mira el contenido."""
    if ruta.lower().endswith(BINARIO):
        return False
    try:
        with io.open(ruta, encoding="utf-8") as fh:
            fh.read()
        return True
    except (UnicodeDecodeError, OSError):
        return False


def _excluido(nombre):
    if nombre in EXCLUIR_DIR:
        return True
    return any(p.search(nombre) for p in EXCLUIR_PAT)


def despersonalizar(texto, con_perfil=True):
    for patron, reemplazo in SUSTITUCIONES:
        texto = patron.sub(reemplazo, texto)
    # El perfil clínico va aparte: sus reglas viven en `perfil.local.json`, no en el código,
    # y solo se aplican a CONTENIDO (no a nombres de fichero, que no llevan diagnóstico).
    return _despersonalizar_perfil(texto) if con_perfil else texto


def _vetados_del_titular():
    """Nombre, apellidos y fecha de nacimiento, derivados del overlay."""
    out = []
    n = _titular("nombre")
    if n:
        out.append(re.compile(r"%s[a-z0-9]*" % re.escape(n[0]), re.I))
    for a in _titular("apellidos"):
        out.append(re.compile(r"%s%s\b" % (_TRAS, re.escape(a)), re.I))
    for p_ in _titular("nacimiento"):
        try:
            out.append(re.compile(p_, re.I))
        except re.error:
            pass
    for c in _titular("contactos"):
        out.append(re.compile(r"%s%s\b" % (_TRAS, re.escape(c)), re.I))
    # Sus teléfonos (25-sep-2026: su móvil salió en un fixture de test, publicado desde el
    # primer commit del espejo). Solo se vetan: si aparece uno, se cambia por uno de pega a
    # mano. Se toleran espacios, puntos y guiones entre cifras, y el +34 delante.
    for t in _titular("telefonos"):
        cifras = re.sub(r"\D", "", str(t))[-9:]
        if len(cifras) == 9:
            out.append(re.compile(r"(?<!\d)%s(?!\d)" % r"[\s.\-]?".join(cifras)))
    # Y lo que mandan tapar las fichas de persona: si sobrevive uno, no se publica.
    out.extend(_rx_termino(t) for t in _terminos_de_personas())
    return tuple(out)


def _vetados_del_perfil():
    """El veto del perfil ES el lado izquierdo de sus sustituciones: si el overlay dice que
    algo es privado, el barrido comprueba que desapareció. Derivarlo en vez de duplicarlo
    evita vetar de más — un `Ki-67 70%` inventado en un fixture de deid no es de nadie."""
    out = []
    for par in _perfil()["sustituciones"]:
        if len(par) == 2:
            try:
                out.append(re.compile(par[0], re.I))
            except re.error:
                pass
    return tuple(out)


# 19-sep-2026 — CASO EXPLÍCITO. {{TITULAR}} pide ayuda pública sobre qué modelos usar para SU caso,
# y para eso quien lea tiene que entender el caso: «tan genérico no sirve». Un fichero puede
# declarar este marcador en su cabecera y entonces el perfil clínico NO se sustituye ahí.
# Lo que NO cambia nunca: nombre, apellidos, fecha de nacimiento y contactos siguen fuera. La
# identidad no se negocia; el diagnóstico es suyo y ella decide dónde se cuenta.
MARCA_CASO_EXPLICITO = "publicar: caso-explicito"


def caso_explicito(texto):
    return MARCA_CASO_EXPLICITO in (texto[:4000] or "")


def vetados_en(texto, con_perfil=True):
    """Los términos vetados que sobreviven. Lista vacía = limpio."""
    patrones = VETADOS + _vetados_del_titular()
    if con_perfil:
        patrones += _vetados_del_perfil()
    return sorted({m.group(0) for p in patrones for m in p.finditer(texto)})


def versionados():
    """Lo que git conoce, que es LA fuente de verdad de qué existe en el repo.

    Recorrer el disco metía artefactos locales (informes sueltos, `.radar_seen.json`,
    caches) que nunca estuvieron versionados. Si git no lo conoce, no se publica.
    """
    salida = subprocess.check_output(["git", "-C", ROOT, "ls-files", "-z"])
    return [r for r in salida.decode("utf-8").split("\0") if r]


def _entra(rel):
    """¿Este fichero versionado entra en el árbol público? EXCLUIR gana siempre."""
    if rel in EXCLUIR_FICHERO or os.path.normpath(rel) in _no_publicar():
        return False
    partes = rel.split(os.sep)
    if any(_excluido(p) for p in partes):
        return False
    if rel in _fichas_alto():
        # La ficha `alto` ES el dossier de alguien que pidió no salir (sus canales, sus rutas
        # de digest, lo que se mina de él): tapar el nombre no la hace publicable.
        return False
    return partes[0] in INCLUIR


def _fichas_alto():
    return frozenset(rel for rel, d in _fichas_personas() if d and d.get("anonimato") == "alto")


def _colisiones(entran):
    """Rutas de origen distintas que acaban en el MISMO fichero público. 25-sep-2026: varias
    fichas de persona se renombraban todas a `contacto.json` y la última pisaba a las demás
    en silencio. Publicar algo distinto de lo que se barrió no es publicar: se aborta."""
    destinos = {}
    for rel in entran:
        destinos.setdefault(despersonalizar(rel, con_perfil=False), []).append(rel)
    return sorted((d, len(o)) for d, o in destinos.items() if len(o) > 1)


def _coser_runner(texto):
    """`tests/test_all.sh` invoca por nombre los tests que EXCLUIR_FICHERO deja fuera.
    Publicar un runner que llama a ficheros ausentes es publicar algo roto. Se derivan de
    la misma lista, para que no haya dos verdades que mantener."""
    fuera = {os.path.basename(r) for r in EXCLUIR_FICHERO if r.startswith("tests/")}
    salida = []
    for ln in texto.splitlines(True):
        if any(t in ln for t in fuera) and ln.lstrip().startswith("runpy"):
            salida.append("# (no publicado: cubre un detector de PHI que vive solo en local)\n")
            continue
        salida.append(ln)
    return "".join(salida)


def _copiar_uno(rel, destino):
    origen = os.path.join(ROOT, rel)
    salida = os.path.join(destino, despersonalizar(rel, con_perfil=False))
    base = os.path.basename(salida)
    if base in RENOMBRAR:
        salida = os.path.join(os.path.dirname(salida), RENOMBRAR[base])
    os.makedirs(os.path.dirname(salida) or destino, exist_ok=True)
    if es_texto(origen):
        with io.open(origen, encoding="utf-8", errors="replace") as fh:
            contenido = fh.read()
        contenido = despersonalizar(contenido, con_perfil=not caso_explicito(contenido))
        if rel == os.path.join("tests", "test_all.sh"):
            contenido = _coser_runner(contenido)
        with io.open(salida, "w", encoding="utf-8") as fh:
            fh.write(contenido)
        # Escribir el fichero pierde el modo del original: los hooks y los .sh dejaban de
        # ser ejecutables y el árbol público fallaba con «el hook tiene que ser ejecutable».
        shutil.copymode(origen, salida)
    else:
        shutil.copy2(origen, salida)


def _barrer(destino):
    """El gate: qué ficheros publicables conservan un término vetado."""
    sucios = []
    for base, dirs, ficheros in os.walk(destino):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in ficheros:
            ruta = os.path.join(base, f)
            rel = os.path.relpath(ruta, destino)
            if rel == os.path.join("tools", "publicar.py"):
                continue          # este fichero ES la lista de vetados; eximirlo no es esquivar
            hits = vetados_en(rel)
            if es_texto(ruta):
                with io.open(ruta, encoding="utf-8", errors="replace") as fh:
                    contenido = fh.read()
                # Un fichero con el marcador cuenta el caso a propósito: el barrido sigue
                # exigiendo que no haya identidad, y deja pasar solo el perfil clínico.
                hits += vetados_en(contenido, con_perfil=not caso_explicito(contenido))
            if hits:
                sucios.append((rel, sorted(set(hits))))
    return sucios


def _overlay_ok():
    """Sin overlay no se publica. 19-sep-2026: si `perfil.local.json` falta o no trae
    titular, `SUSTITUCIONES` se queda sin patrones Y `vetados_en()` también — el árbol sale
    crudo y el barrido lo aprueba, porque no le queda nada que buscar. Un fail-closed que
    depende de un fichero opcional es un fail-open con otro nombre. Pasa, por ejemplo, al
    generar desde un worktree, que no hereda los ficheros gitignored de casa base."""
    faltan = []
    if not _titular("nombre"):
        faltan.append("tools/perfil.local.json → titular.nombre")
    if not _perfil()["sustituciones"]:
        faltan.append("tools/perfil.local.json → sustituciones")
    if not _nombres_de_terceros():
        faltan.append("tools/nombres.local.json → nombres")
    faltan.extend(_personas_sin_cubrir())
    return faltan


def _vaciar(destino):
    """Borra el árbol derivado y conserva `.git`: el árbol se deriva; el repo que lo aloja, no."""
    for hijo in os.listdir(destino):
        if hijo == ".git":
            continue
        ruta = os.path.join(destino, hijo)
        shutil.rmtree(ruta) if os.path.isdir(ruta) and not os.path.islink(ruta) else os.remove(ruta)


def publicar(destino, forzar=False):
    faltan = _overlay_ok()
    if faltan:
        print("🔴 NO se publica: falta el overlay de despersonalización en %s" % ROOT,
              file=sys.stderr)
        for f in faltan:
            print("   · %s" % f, file=sys.stderr)
        return 3
    if os.path.exists(destino):
        if not forzar:
            print("El destino ya existe: %s (usa --forzar)" % destino, file=sys.stderr)
            return 2
        # Borrar el destino entero se llevaba por delante su `.git`: el repo publicado
        # perdía historial y remote en cada regeneración, y había que rehacerlo y empujar
        # con --force. El árbol se deriva; el repo que lo aloja, no.
        _vaciar(destino)
    os.makedirs(destino, exist_ok=True)

    entran = [r for r in versionados() if _entra(r)]
    choques = _colisiones(entran)
    if choques:
        # Los orígenes no se imprimen: el nombre de fichero original es justo lo que se tapa.
        print("🔴 NO se publica: %d ruta(s) pública(s) con varios orígenes" % len(choques),
              file=sys.stderr)
        for destino_rel, n in choques:
            print("   %s ← %d ficheros" % (destino_rel, n), file=sys.stderr)
        return 1
    for rel in entran:
        _copiar_uno(rel, destino)
    n = len(entran)
    # La MARCA del espejo (24-sep-26). Los tests que dependen de los nombres reales o del
    # léxico vetado no pueden correr aquí: este árbol los reescribe («ingeniera» llega como
    # «ingeniera»). Antes lo adivinaban buscando `{{` en el texto, que solo deja la sustitución
    # de nombres; un test sin nombre propio no se enteraba y el CI público se ponía rojo (21-22
    # y 24-sep). Con la marca lo SABEN: `tests/_entorno.es_espejo()`.
    with open(os.path.join(destino, MARCA_ESPEJO), "w", encoding="utf-8") as f:
        f.write("Árbol derivado por tools/publicar.py: nombres sustituidos y léxico reescrito.\n"
                "Los tests que necesitan los datos reales se saltan aquí con su motivo.\n")

    sucios = _barrer(destino)
    if sucios:
        print("\n🔴 NO se publica: %d fichero(s) conservan términos vetados" % len(sucios),
              file=sys.stderr)
        for rel, hits in sucios[:25]:
            print("   %s → %s" % (rel, ", ".join(hits)), file=sys.stderr)
        if len(sucios) > 25:
            print("   … y %d más" % (len(sucios) - 25), file=sys.stderr)
        # fail-closed: nada a medias. Pero el `.git` del destino se queda: es el clon del
        # espejo, con su historial y su remoto (25-sep-2026: un veto borraba el repo entero).
        _vaciar(destino)
        return 1

    print("\n✅ %d ficheros en %s · barrido limpio (%d patrones vetados)"
          % (n, destino, len(VETADOS) + len(_vetados_del_titular())
             + len(_vetados_del_perfil())))
    return 0


def selftest():
    casos = [
        ("El OK de {{TITULAR}} manda", "El OK de {{TITULAR}} manda"),
        ("usa voz-titular para el tono", "usa voz-titular para el tono"),
        ("escribe a titular@gmail.com", "escribe a titular@gmail.com"),
        ("la cuenta TITULAR_CHAT_ID", "la cuenta TITULAR_CHAT_ID"),
    ]
    fallos = 0
    for entrada, esperado in casos:
        got = despersonalizar(entrada)
        if got != esperado:
            print("FALLO: %r → %r (esperaba %r)" % (entrada, got, esperado)); fallos += 1
    hits = vetados_en("%s es ECOG {{N}}" % (_titular("nombre") or ["zzz"])[0])
    if "ECOG {{N}}" not in hits:
        print("FALLO: el barrido no caza ECOG"); fallos += 1
    if _titular("nombre") and len(hits) < 2:
        print("FALLO: con overlay, el barrido no caza el nombre"); fallos += 1
    if vetados_en("una biopsia ósea cualquiera"):
        print("FALLO: 'biopsia' genérica no debe estar vetada"); fallos += 1
    print("selftest: %d fallos" % fallos)
    return 1 if fallos else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Genera el árbol publicable de Polaris.")
    p.add_argument("destino", nargs="?")
    p.add_argument("--forzar", action="store_true", help="borra el destino si existe")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.destino:
        p.error("falta el destino")
    return publicar(os.path.abspath(a.destino), a.forzar)


if __name__ == "__main__":
    sys.exit(main())
