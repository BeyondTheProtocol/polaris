#!/usr/bin/env python3
"""zonas_clinicas.py — FUENTE ÚNICA de qué es «zona clínica» en Polaris.

Antes esto vivía en cuatro listas independientes que ya habían divergido
(`muro_guard.CLINICAL_HINTS`, `clinico_guard.CLINICAL_HINTS`,
`lector_clinico._allowed_roots()` y el bloque clínico del `.gitignore`), y las carpetas
donde de verdad vive el N2 —`informes/`, `docu enviada a nova/`— no estaban en NINGUNA.
Cerrar eso sincronizando listas ya se intentó; esto lo cierra con una sola definición.

## Por qué DOS familias y no una lista

El error de las cuatro listas era tratar todo con la misma herramienta (substring sobre el
comando en crudo). Eso hace dos cosas malas a la vez: deniega comandos que solo MENCIONAN
la ruta (un `ls -d 00_FUENTE-DE-VERDAD/*/` o un `grep -rn "_PRIVADO_CLINICO" tools/` —
reproducido tres veces mientras se auditaba esto), y aun así no protege `informes/`, porque
meter «informes» como substring denegaría media palabra española del repo.

La separación es la misma que `.gitignore` ya hace con `**/_PRIVADO_*/` frente a `/informes/`:

  · Familia A — NOMBRE EN CUALQUIER NIVEL. El nombre es un token único que no colisiona con
    nada (`_PRIVADO_CLINICO`, `00_Salud`…), así que casa como SEGMENTO de ruta, esté donde esté.
  · Familia B — RAÍZ ANCLADA. El nombre es una palabra común (`informes`), así que solo casa
    como prefijo de una ruta ABSOLUTA concreta. `informes` nunca casa suelto.
  · Familia C — EXENTOS. `_PRIVADO_DMS` no es clínico; la razón está documentada desde que se
    quitó el `"_privado"` suelto de muro_guard.
  · Familia D — FICHEROS EXENTOS dentro de una de la A (`_PRIVADO_INSTAGRAM/reels/*.md`). El
    padre y el propio directorio siguen siendo zona clínica; solo esos ficheros públicos salen.

## Lo que este módulo NO hace, a propósito

`00_FUENTE-DE-VERDAD/` está gitignorada pero NO es zona clínica: el lazo lee `Gestion/HOY.md`
y `Mails/` sin parar, y marcarla entera pararía el sistema. Que esté en `.gitignore` y no aquí
es correcto — no lo «arregles» en la dirección equivocada.

Solo stdlib, sin estado, sin I/O más allá de `realpath` (y un `isdir` solo cuando aplica la
familia D). Lo importan hooks que corren en CADA
llamada a herramienta: si crece, mídelo.
"""
import glob as _glob
import io
import json
import os
import re as _re
import unicodedata

# `<<` o `<<-`, delimitador con o sin comillas. Excluye `<<<` (here-string: no tiene cuerpo).
_RE_HEREDOC = _re.compile(r"<<(-?)(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")

VERSION = 1

# ── Familia A: nombres que son zona clínica en cualquier nivel de la ruta ─────────────
NOMBRES_CUALQUIER_NIVEL = frozenset({
    "_privado_clinico", "_privado_expediente", "_privado_nucleo", "_privado_correo",
    "_privado_whatsapp", "_privado_x", "_privado_yt", "_privado_instagram",
    "_privado_sesiones",          # lo escribe minador_sesiones.py; no estaba en ninguna lista
    "clinico-privado",            # ~/Clinico-PRIVADO
    "00_salud", "00 - salud",     # la ruta iCloud del portátil
})
# Prefijos de segmento (el nombre lleva fecha o apellidos variables detrás).
PREFIJOS_CUALQUIER_NIVEL = (
    "historial clinico", "historial-clinico",
)
# ── Familia C: exentos, ganan sobre A y B ────────────────────────────────────────────
# `_PRIVADO_DMS` son DMs de redes, no clínico. Es la razón por la que el `"_privado"` suelto
# se quitó de muro_guard en su día; se conserva aquí para que no vuelva a colarse.
NOMBRES_EXENTOS = frozenset({"_privado_dms"})
# ── Familia D: ficheros exentos DENTRO de una carpeta de la familia A ─────────────────
# (padre, hija): el segmento `padre` deja de contar como clínico SOLO para un FICHERO `.md`
# que cuelga directamente de `hija` (`padre/hija/<x>.md`, y que no sea un directorio). El padre
# sigue protegido entero, y también el directorio `hija` en sí (ni `ls`, ni `cd`, ni `-C`), sus
# sub-carpetas y cualquier otra extensión. Cualquier otro segmento clínico de la ruta sigue
# mandando, y los symlinks se juzgan por su destino. Y SOLO para tools nativas (Read/Grep/
# Glob): en Bash (`rutas_clinicas_en_tokens`) la familia D no existe y reels/ sigue denegada.
#
# `_PRIVADO_INSTAGRAM/reels/` (11-sep-26, deuda privado-instagram-bloquea-mineria-buzon): la
# escribe SOLO tools/reel_digest.py con la transcripción, el texto en pantalla y la URL de un
# reel/vídeo de TERCEROS que la titular manda al buzón. Contenido público (N0, lo dice el propio
# tool); el drenaje automático no pasa caption. Lo que puede traer PII de terceros
# (comentarios y menciones de tools/instagram.py) cae en la RAÍZ de `_PRIVADO_INSTAGRAM/`,
# y esa sigue denegada.
SUBCARPETAS_EXENTAS = frozenset({("_privado_instagram", "reels")})

# ── Familia B: raíces ancladas (relativas a una base; nunca casan sueltas) ────────────
RAICES_EN_REPO = (
    "informes",
    "docu enviada a nova",
)
RAICES_EN_HOME = (
    "Clinico-PRIVADO",
    "DICOM_Seattle",              # imagen médica; no estaba ni en .gitignore
)

# ── Overlay local: lo que NO puede estar versionado ──────────────────────────────────
# Algunas carpetas clínicas se llaman con el nombre completo y la fecha de nacimiento de la
# titular (así las nombra el hospital). Tenerlas aquí metía esos datos en git para siempre:
# el detector de PHI era la fuga. Viven en `zonas_clinicas.local.json` (gitignored) y se
# SUMAN a las listas de arriba. Formato: {"prefijos": [...], "raices_repo": [...],
# "raices_home": [...], "nombres": [...]}.
#
# FAIL-OPEN CONSCIENTE, y por qué es aceptable: si el overlay falta, se protege MENOS, no
# más. No se puede hacer fail-closed sin meter en el código lo que se quiere sacar de él. La
# mitigación es que los prefijos genéricos de arriba («historial clinico») ya cubren el caso
# habitual, y que `avisa_si_falta_overlay()` lo dice en voz alta en la casa base.
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zonas_clinicas.local.json")
# ...y el de CASA BASE. El overlay es gitignored, así que NO viaja a un worktree — y ahí el
# fail-open consciente de arriba dejaba de ser «se protege menos» para convertirse en «no se
# protege»: las raíces clínicas locales no se reconocían y sus rutas pasaban el guard. Justo
# en las ramas, que es donde se prueba el código nuevo. El fichero está al lado, en casa base:
# solo había que ir a buscarlo. Cuarto sitio con este mismo fallo (20-sep-26); los otros tres
# fueron seguimiento._cargar_nombres_deny, tests/_entorno._hay_overlay y subir_historial_drive.
LOCAL_BASE = os.path.join(
    os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
    ".claude", "hooks", "zonas_clinicas.local.json")


def _overlay():
    """El overlay de ESTE repo, y si no está, el de casa base. Se busca, no se copia."""
    for ruta in (LOCAL, LOCAL_BASE):
        try:
            with io.open(ruta, encoding="utf-8") as fh:
                d = json.load(fh)
            break
        except (OSError, ValueError):
            continue
    else:
        return {}
    return d if isinstance(d, dict) else {}


def _extender():
    """Suma el overlay local a las listas del módulo. Idempotente."""
    global PREFIJOS_CUALQUIER_NIVEL, RAICES_EN_REPO, RAICES_EN_HOME, NOMBRES_CUALQUIER_NIVEL
    d = _overlay()
    pref = tuple(str(x).lower() for x in d.get("prefijos", []) if str(x).strip())
    PREFIJOS_CUALQUIER_NIVEL = tuple(dict.fromkeys(PREFIJOS_CUALQUIER_NIVEL + pref))
    RAICES_EN_REPO = tuple(dict.fromkeys(RAICES_EN_REPO + tuple(d.get("raices_repo", []))))
    RAICES_EN_HOME = tuple(dict.fromkeys(RAICES_EN_HOME + tuple(d.get("raices_home", []))))
    NOMBRES_CUALQUIER_NIVEL = frozenset(NOMBRES_CUALQUIER_NIVEL) | {
        str(x).lower() for x in d.get("nombres", []) if str(x).strip()}


def hay_overlay():
    """¿Hay overlay, aquí o en casa base? Lo usan los tests y el healthcheck.

    Mira los DOS sitios, igual que `_overlay()`. Si solo mirara el local, en un worktree diría
    «falta el overlay» mientras el módulo lo está usando desde casa base: un aviso que miente
    es peor que no avisar, porque el test declara un hueco que ya no existe."""
    return os.path.isfile(LOCAL) or os.path.isfile(LOCAL_BASE)


_extender()      # al importar: las listas de arriba ya están definidas


# Binarios cuyo PRIMER operando no-flag es un PATRÓN, no una ruta. Sin esto, un
# `grep -rn "_PRIVADO_CLINICO" tools/` se deniega por el patrón de búsqueda — que es
# exactamente el falso positivo que paralizaba el trabajo sobre el propio muro.
BINARIOS_CON_PATRON = frozenset({
    "grep", "egrep", "fgrep", "rg", "ack", "ag", "sed", "awk", "perl",
})


def normaliza(seg):
    """Minúsculas, sin tildes y sin separadores raros, para comparar segmentos de ruta."""
    s = unicodedata.normalize("NFD", str(seg or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().lower()


def _bases():
    home = os.path.expanduser("~")
    repo = os.environ.get("BTP_REPO") or os.environ.get("CLAUDE_PROJECT_DIR") \
        or os.path.join(home, "claudecode")
    # /Users/polaris es la casa base real aunque el HOME del proceso sea otro (worktrees,
    # daemons con HOME distinto). Barato y evita un agujero por entorno.
    return (repo, home, "/Users/polaris", "/Users/titular")


def raices():
    """Familia B ya resuelta a rutas absolutas (sin exigir que existan: una ruta de otra
    máquina o aún no creada tiene que denegarse igual)."""
    out = []
    for base in _bases():
        for r in RAICES_EN_REPO:
            out.append(os.path.join(base, r))
        for r in RAICES_EN_HOME:
            out.append(os.path.join(base, r))
    # ~/claudecode/informes vale, pero también /Users/polaris/informes si el repo se moviera.
    return tuple(dict.fromkeys(os.path.normpath(p) for p in out))


_RAICES = None


def _raices_cache():
    global _RAICES
    if _RAICES is None:
        _RAICES = tuple(normaliza(r) for r in raices())
    return _RAICES


def _segmentos(ruta):
    return [normaliza(s) for s in str(ruta or "").replace("\\", "/").split("/") if s]


_MARCA_WORKTREE = "/.claude/worktrees/"


def _sin_worktree(ruta):
    """`<repo>/.claude/worktrees/<rama>/informes/x` → `<repo>/informes/x`.

    Un worktree es una copia del repo, así que sus raíces ancladas son las mismas. El clínico
    no viaja al worktree (está gitignored), pero si alguien copia ahí un informe el guard
    tiene que verlo igual: la protección no puede depender de en qué copia estás trabajando.
    """
    r = str(ruta or "")
    i = r.find(_MARCA_WORKTREE)
    if i < 0:
        return None
    resto = r[i + len(_MARCA_WORKTREE):]
    partes = resto.split("/", 1)
    if len(partes) < 2 or not partes[1]:
        return None
    return os.path.normpath(os.path.join(r[:i], partes[1]))


def _casa_en_raiz(ruta):
    rn = normaliza(ruta)
    return any(rn == raiz or rn.startswith(raiz + "/") for raiz in _raices_cache())


def es_segmento_clinico(seg):
    """¿Este SEGMENTO de ruta (no substring) es zona clínica?"""
    s = normaliza(seg)
    if not s or s in NOMBRES_EXENTOS:
        return False
    if s in NOMBRES_CUALQUIER_NIVEL:
        return True
    return any(s.startswith(p) for p in PREFIJOS_CUALQUIER_NIVEL)


def _es_hoja_exenta(segs, i):
    """¿`segs[i]` (padre de la familia A) va seguido EXACTAMENTE de `hija/<fichero>.md` y nada más?

    Solo el FICHERO se exime, nunca el directorio: si `cd …/reels` pasara, un `cat ../x.json`
    posterior se resolvería contra el cwd del hook (no el del shell) y leería la raíz protegida
    sin nombrarla (lo cazó `verificacion`, 11-sep-26). Por lo mismo tampoco valen `-C …/reels`,
    sub-carpetas dentro de reels/, llaves de glob ni otra extensión."""
    if i + 3 != len(segs) or (segs[i], segs[i + 1]) not in SUBCARPETAS_EXENTAS:
        return False
    hoja = segs[i + 2]
    return (hoja.endswith(".md") and len(hoja) > 3 and not hoja.startswith(".")
            and not any(c in hoja for c in "{}"))


def _hay_segmento_clinico(segs, ruta_disco=None, exencion=True):
    """¿Algún segmento de la lista es clínico, descontando las hojas exentas (familia D)?
    `segs` ya viene normalizado. Si hay `ruta_disco` y resulta ser un DIRECTORIO (un `x.md/`
    creado a propósito), la exención no aplica. El stat solo se hace si la exención iba a aplicar."""
    for i, s in enumerate(segs):
        if not es_segmento_clinico(s):
            continue
        if exencion and _es_hoja_exenta(segs, i) \
                and (ruta_disco is None or _no_es_dir(ruta_disco)):
            continue
        return True
    return False


def _no_es_dir(ruta):
    try:
        return not os.path.isdir(ruta)
    except Exception:
        return False


def es_ruta_clinica(ruta, cwd=None, exencion=True):
    """¿Esta ruta apunta a zona clínica? Familia A por segmento + familia B por prefijo.

    NO exige que exista: una ruta de otra máquina, o un fichero que aún no está creado,
    tiene que denegarse igual (si no, el guard se abriría solo al borrar el fichero).
    """
    if not ruta:
        return False
    r = os.path.expanduser(str(ruta))
    if not os.path.isabs(r):
        r = os.path.join(cwd or os.getcwd(), r)
    # normpath (léxico) y no realpath a secas: realpath sobre una ruta inexistente pierde
    # los `..`, y además queremos evaluar el camino tal y como lo escribió quien llama.
    r = os.path.normpath(r)
    if _hay_segmento_clinico(_segmentos(r), ruta_disco=r, exencion=exencion):
        return True
    if _casa_en_raiz(r):
        return True
    equiv = _sin_worktree(r)
    if equiv and _casa_en_raiz(equiv):
        return True
    # Y otra vez tras resolver symlinks: un enlace a la carpeta clínica es la carpeta clínica.
    try:
        real = os.path.normpath(os.path.realpath(r))
    except Exception:
        return False
    if real == r:
        return False
    if _hay_segmento_clinico(_segmentos(real), ruta_disco=real, exencion=exencion):
        return True
    if _casa_en_raiz(real):
        return True
    equiv = _sin_worktree(real)
    return bool(equiv and _casa_en_raiz(equiv))


def es_patron_clinico(patron):
    """Para el `pattern` de Glob/Grep, donde no hay ruta que resolver. SOLO familia A: un
    patrón que nombra `_PRIVADO_CLINICO` está pescando en la zona clínica. `informes` no
    entra aquí — es una palabra normal y colgaría cualquier búsqueda del repo."""
    # normpath antes de trocear: sin él, `_PRIVADO_INSTAGRAM/reels/../*.md` usaría la exención
    # de `reels/` para pescar en la raíz de la carpeta, que sigue protegida.
    p = str(patron or "").replace("\\", "/")
    return _hay_segmento_clinico(_segmentos(os.path.normpath(p) if p else p))


def rutas_clinicas_en_tokens(tokens, cwd=None):
    """Los tokens de UN subcomando que apuntan a zona clínica.

    Se salta el binario, los flags, y —en grep/rg/sed/awk— el primer operando no-flag, que
    es el patrón de búsqueda. Ese salto es lo que permite volver a trabajar sobre el propio
    muro sin que el muro te lo impida.
    """
    toks = [str(t) for t in (tokens or []) if str(t)]
    if not toks:
        return []
    binname = os.path.basename(toks[0]).lower()
    # La familia D NO aplica en Bash: desde un fichero exento se deriva su directorio
    # (`find x.md -execdir`, `dirname | xargs`, `$(dirname …)`, subshell con cd) y de ahí se sube
    # a la raíz protegida sin nombrarla. Lo cazó `verificacion` (11-sep-26, 2ª ronda). Una lista
    # negra de binarios siempre deja un hueco, así que la exención vive SOLO en las tools nativas
    # (Read/Grep/Glob → es_ruta_clinica / es_patron_clinico), que es lo que auto-mejora necesita.
    exencion = False
    saltar_patron = binname in BINARIOS_CON_PATRON
    fuera = []
    for t in toks[1:]:
        if t.startswith("-"):
            # -e PATRON / -f FICHERO ya traen el patrón explícito: deja de ser posicional.
            if t in ("-e", "--regexp", "-f", "--file"):
                saltar_patron = False
            continue
        if saltar_patron:
            saltar_patron = False      # este era el patrón; los siguientes sí son rutas
            continue
        candidatos = [t]
        if any(c in t for c in "*?["):
            try:
                candidatos = _glob.glob(os.path.expanduser(t)) or [t]
            except Exception:
                candidatos = [t]
        for c in candidatos:
            if es_ruta_clinica(c, cwd=cwd, exencion=exencion):
                fuera.append(c)
                break
    return fuera


def motivo(ruta):
    return ("lectura de datos clínicos fuera de la ventanilla auditada: %s" % ruta)


# Intérpretes: si el heredoc alimenta a uno de éstos, su cuerpo es CÓDIGO que sí puede leer
# rutas clínicas, así que no se despoja y se juzga entero.
_INTERPRETES = {"bash", "sh", "zsh", "ksh", "dash", "python", "python3", "perl", "ruby",
                "node", "eval", "source", "."}


def sin_heredocs(cmd):
    """Devuelve `cmd` sin los CUERPOS de sus heredocs, para juzgar solo las rutas que se leen.

    POR QUÉ (12-sep-2026): `cat > x.md <<'EOF' … informes … EOF` se denegaba. El cuerpo se
    tokenizaba como operandos del mismo subcomando, y la palabra suelta «informes» se resolvía
    contra el cwd → `<repo>/informes` → zona clínica → DENY. Nadie leía nada: era texto entrando
    por stdin. El falso positivo empujaba a exportar MURO_ALLOW_CLINICAL=1, que sí debilita el muro.

    Qué NO se despoja, porque ahí el cuerpo sí puede ser peligroso:
      · el heredoc alimenta a un intérprete → su cuerpo es código que puede leer rutas;
      · el delimitador va SIN comillas y el cuerpo trae `$(`, backtick o `${` → hay sustitución;
      · no aparece el terminador → no sabemos dónde acaba, se devuelve intacto (fail-closed,
        el mismo criterio que la rama `ValueError` de `_subcomandos`).

    Ojo al alcance: esto es para el eje «qué rutas se LEEN». No debe aplicarse antes de la
    allowlist ni del control de egress, donde el cuerpo de un heredoc sí es la carga que sale
    (`curl -d @- <<EOF …`).
    """
    texto = str(cmd or "")
    if "<<" not in texto:
        return texto
    lineas = texto.split("\n")
    fuera, i = [], 0
    while i < len(lineas):
        linea = lineas[i]
        m = _RE_HEREDOC.search(linea)
        if not m:
            fuera.append(linea)
            i += 1
            continue
        cabeza = linea[:m.start()]
        if os.path.basename((cabeza.strip().split() or [""])[0]).lower() in _INTERPRETES:
            return texto                      # código, no dato: que lo juzgue entero
        guion, comilla, delim = m.group(1), m.group(2), m.group(3)
        # buscar el terminador
        fin = None
        for j in range(i + 1, len(lineas)):
            cand = lineas[j].lstrip("\t") if guion else lineas[j]
            if cand.strip() == delim:
                fin = j
                break
        if fin is None:
            return texto                      # sin terminador: no tocamos nada
        cuerpo = "\n".join(lineas[i + 1:fin])
        if not comilla and ("$(" in cuerpo or "`" in cuerpo or "${" in cuerpo):
            return texto                      # sustitución de comandos dentro del cuerpo
        fuera.append(cabeza.rstrip())          # la cabeza sí se juzga (ahí va el `>` y su destino)
        i = fin + 1
    return "\n".join(fuera)
