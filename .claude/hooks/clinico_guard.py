#!/usr/bin/env python3
"""clinico_guard.py — guard PreToolUse ESTRECHO para las sesiones INTERACTIVAS.

EL AGUJERO (auditoría 14-jul-26, verificado por ejecución):
  · `muro_guard.py` sí tiene una regla de lectura clínica (`check_read`), pero (a) solo
    está enganchado en `settings.autonomous.json` / `settings.quarantine.json` — o sea,
    únicamente en el lazo 24/7 —, y (b) sus marcadores (`00_salud`, `historial clinico`)
    NO coinciden con las carpetas reales del mini (`_PRIVADO_CLINICO`, `_PRIVADO_NUCLEO`,
    `_PRIVADO_CORREO`, `_PRIVADO_EXPEDIENTE`, `~/Clinico-PRIVADO`). Resultado: el guard
    clínico no denegaba NADA, ni siquiera donde estaba enganchado.
  · En interactivo no había ningún hook: cualquier agente (prensa, diseño, comunidad…)
    podía abrir el disco clínico, y no quedaba REGISTRO de un solo acceso.

QUÉ HACE (y qué NO):
  · SOLO vigila la LECTURA de rutas clínicas. No es una allowlist de binarios: no toca
    el resto del trabajo interactivo (por eso no se engancha aquí el muro_guard entero,
    que es fail-closed y rompería la sesión).
  · Read/Grep/Glob/LS/NotebookRead sobre una ruta clínica  -> DENY.
  · Bash que lea una ruta clínica (cat/head/less/grep/cp…) -> DENY.
  · El subcomando Bash que EJECUTE `tools/lector_clinico.py` -> PERMITIDO (esa es la
    ventanilla SANCIONADA: deja registro de cada acceso). Solo ese subcomando: el resto de
    la línea se juzga igual que sin ella.
  · `MURO_ALLOW_CLINICAL=1`                                -> bypass (comité clínico).
  · TODO intento (permitido o denegado) se registra en .claude/logs/clinico-access.log.

FAIL-OPEN A PROPÓSITO ante un error interno: en interactivo hay un humano delante, y un
bug en este hook no puede dejar a {{TITULAR}} sin poder trabajar. Los dientes fail-closed
viven en el lazo 24/7 (muro_guard). Aquí el objetivo es cerrar la lectura clínica y
crear la traza de auditoría que hoy no existe.
"""
import json
import os
import re
import shlex
import sys
from datetime import datetime

REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.expanduser("~/claudecode")


def _casa_base(repo):
    """El árbol donde vive el log de auditoría: SIEMPRE casa base, nunca un worktree.

    Un worktree de git tiene `.git` como FICHERO (un puntero al .git compartido); la casa base
    lo tiene como DIRECTORIO. Es la comprobación más barata que distingue los dos, y se hace
    una vez al importar.

    POR QUÉ (20-sep-2026). Esto resolvía el log contra el árbol donde corre el hook, así que
    cada sesión con worktree escribía SU propia traza. Medido al podar uno: 52.682 líneas allí,
    con accesos que no estaban en el log de casa base. Y `.claude/logs` está gitignored, así
    que `git status` decía «limpio» y la poda se los habría llevado sin avisar. La traza de
    quién tocó la zona clínica es justo lo que no puede fragmentarse ni perderse.

    Un `CLAUDE_PROJECT_DIR` que apunte a un directorio cualquiera (los tests lo mandan a un
    tmpdir) se respeta tal cual: allí no hay `.git`, así que no es un worktree.
    """
    return (os.path.expanduser("~/claudecode")
            if os.path.isfile(os.path.join(repo, ".git")) else repo)


LOG = os.path.join(_casa_base(REPO), ".claude", "logs", "clinico-access.log")

# La política vive en zonas_clinicas.py (fuente única, compartida con muro_guard y
# lector_clinico). El import es defensivo: este hook corre en CADA llamada a herramienta y
# un ImportError no puede dejar a {{TITULAR}} sin trabajar. Si falla, se degrada a la lista de
# antes — nunca a menos: `test_zonas_clinicas` asserta que el fallback sigue cubierto.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import zonas_clinicas as ZC
except Exception as _e:              # noqa: BLE001
    ZC, _ZC_ERR = None, repr(_e)

# Fallback histórico (solo si el import falla). Marcadores de las carpetas REALES de datos
# sensibles: la convención de {{TITULAR}} es `_PRIVADO_*`, más la ruta iCloud del portátil.
_CLINICAL_HINTS_FALLBACK = (
    "_privado_clinico", "_privado_nucleo", "_privado_correo", "_privado_expediente",
    "clinico-privado",               # ~/Clinico-PRIVADO
    "00_salud", "00 - salud",        # la ruta iCloud del portátil
    "historial clinico", "historial clínico", "historial-clinico",
)

# La ventanilla auditada: el SUBCOMANDO que la ejecuta se permite (ella misma registra).
# Antes bastaba con que el texto apareciera en cualquier parte del comando, así que
# `cat <clínico> # lector_clinico.py` pasaba sin traza (deuda clinico-guard-ventanilla-por-
# substring, 19-sep-26). Ahora solo cuenta el subcomando que la invoca; el resto se juzga.
VENTANILLA = "lector_clinico.py"
# ...y su IDENTIDAD, no solo su nombre. Reconocerla por basename dejaba pasar
# `python3 /tmp/x/lector_clinico.py <ruta clínica>`: cualquier script con ese nombre se
# eximía, que es tanto como dejar la llave puesta (deuda clinico-guard-ventanilla-por-
# basename, 20-sep-26). Ahora un candidato solo es LA ventanilla si es el fichero de este
# árbol, o uno idéntico byte a byte colgando de un árbol Polaris (los worktrees tienen su
# propia copia y son igual de legítimos). Si no se puede PROBAR, no se exime: fail-closed.
_ARBOL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENTANILLA_CANON = os.path.join(_ARBOL, "tools", VENTANILLA)
_VENTANILLA_MAX = 2 * 1024 * 1024    # un script; si abulta más, no es el nuestro


def _huella(ruta):
    """SHA-256 del fichero, o None si no se puede leer o abulta demasiado."""
    try:
        if os.path.getsize(ruta) > _VENTANILLA_MAX:
            return None
        import hashlib
        with open(ruta, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception:                # noqa: BLE001
        return None


def _es_la_ventanilla_real(tok):
    """¿El token apunta al fichero de la ventanilla, y no a otro que se llama igual?"""
    if os.path.basename(tok) != VENTANILLA:
        return False
    canon = os.path.realpath(_VENTANILLA_CANON)
    candidatos = [tok] if os.path.isabs(tok) else [
        os.path.join(_ARBOL, tok), os.path.join(os.getcwd(), tok), tok]
    mia = None
    for cand in candidatos:
        try:
            rp = os.path.realpath(cand)
        except Exception:            # noqa: BLE001
            continue
        if not os.path.isfile(rp):
            continue
        if rp == canon:              # la de este árbol: sin leer nada
            return True
        # Otro árbol Polaris (un worktree): tiene que colgar de <x>/tools con el hook al
        # lado, Y ser idéntica a la nuestra. Replicar la estructura no basta.
        d = os.path.dirname(rp)
        if os.path.basename(d) != "tools":
            continue
        if not os.path.isfile(os.path.join(os.path.dirname(d), ".claude", "hooks",
                                            "clinico_guard.py")):
            continue
        if mia is None:
            mia = _huella(canon)
        if mia and _huella(rp) == mia:
            return True
    return False
_ASIGNACION = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
_PYTHON = re.compile(r"^python(3(\.\d+)?)?$")

# Palabras que preceden a un comando sin ser el comando. Sin esto, el `do` de un bucle o el
# `timeout` de una llamada larga escondían la ventanilla y el subcomando entero se juzgaba
# como si leyera a pelo (reproducción de 14.378 comandos reales, 19-sep-26).
_PREFIJOS = frozenset({"do", "then", "else", "elif", "time", "exec", "nohup", "command",
                       "builtin", "!", "(", ")", "{", "}"})
_PREFIJOS_CON_ARG = frozenset({"timeout", "nice", "ionice", "stdbuf"})
_DURACION = re.compile(r"^\d+(\.\d+)?[smhd]?$")

# `$f`, `${f}` — una referencia a variable, no una ruta. Para resolverla hace falta saber
# qué se le asignó unas palabras antes.
def _ref_var(nombre):
    return re.compile(r"\$\{?%s\}?(?![A-Za-z0-9_])" % re.escape(nombre))


# Imprimir el NOMBRE de un fichero no es abrirlo. Solo se usa para los tokens que salen de
# resolver una variable: una ruta clínica escrita a pelo en un `echo` sigue denegada igual
# que antes de este arreglo.
_NO_LEEN = frozenset({"echo", "printf"})

LECTURAS = {"Read", "Grep", "Glob", "LS", "NotebookRead"}


def _log(veredicto, tool, ruta):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\t%s\n" % (
                datetime.now().isoformat(timespec="seconds"), veredicto, tool, ruta))
    except Exception:
        pass  # el log nunca rompe la sesión


def _es_clinico_fallback(texto):
    t = str(texto or "").lower()
    return any(h in t for h in _CLINICAL_HINTS_FALLBACK)


def _ruta_clinica(v):
    """¿Este valor de tool_input apunta a zona clínica?"""
    if ZC is None:
        return _es_clinico_fallback(v)
    return ZC.es_ruta_clinica(v)


def _patron_clinico(v):
    """El `pattern` de Glob/Grep no es una ruta: se juzga solo por nombre de segmento."""
    if ZC is None:
        return _es_clinico_fallback(v)
    return ZC.es_patron_clinico(v)


# `(` y `)` NO separan: se probó y abrió 15 lecturas que casa base sí denegaba — la ruta que
# sigue a `open(` o a `a=(` se convertía en el primer token de su subcomando, y el primer token
# se salta por ser «el binario». La sustitución de comandos se reconoce en `_salta_prefijos`.
_SEPARADORES = {"|", "||", "&&", ";", "&", "|&"}


def _lineas_logicas(texto):
    """Parte un script en LÍNEAS, respetando comillas y continuaciones `\\`+salto.

    POR QUÉ: `shlex` trata el salto de línea como un espacio más, así que un script de tres
    líneas que acaba llamando a la ventanilla se veía como UN subcomando que empieza por `cd`
    — la ventanilla dejaba de reconocerse y el guard denegaba su propio trabajo. Se parte
    aquí, y no sustituyendo el salto por `;`, porque entonces un `#` de comentario se comería
    el resto del script: así el comentario muere con su línea, como en el shell.
    """
    lineas, act, comilla, i, n = [], [], "", 0, len(texto)
    comentario = False
    while i < n:
        c = texto[i]
        if comentario:
            # dentro de un comentario NADA continúa la línea: `# nota \\` + salto NO pega la
            # siguiente línea, y sin esto el `cat` que venía detrás se perdía entero.
            if c == "\n":
                comentario = False
                lineas.append("".join(act))
                act = []
            i += 1
            continue
        if comilla:
            if c == "\\" and comilla == '"' and i + 1 < n:
                act.append(c)
                act.append(texto[i + 1])
                i += 2
                continue
            if c == comilla:
                comilla = ""
            act.append(c)
            i += 1
            continue
        if c in "'\"":
            comilla = c
            act.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if texto[i + 1] == "\n":
                act.append(" ")          # continuación de línea: sigue el mismo comando
                i += 2
                continue
            act.append(c)
            act.append(texto[i + 1])
            i += 2
            continue
        if c == "#" and (not act or act[-1] in (" ", "\t")):
            comentario = True
            i += 1
            continue
        if c == "\n":
            lineas.append("".join(act))
            act = []
            i += 1
            continue
        act.append(c)
        i += 1
    lineas.append("".join(act))
    return [l for l in lineas if l.strip()]


def _subcomandos(cmd):
    """Trocea un comando de shell en pares `(tokens, separador_que_le_sigue)`.

    Fail-CLOSED al tokenizar: si las comillas están mal cerradas no sabemos qué se está
    leyendo, así que devolvemos la línea entera como un solo subcomando y que la juzgue el
    predicado. Es el único punto de este hook que no es fail-open, y a propósito.
    """
    # El CUERPO de un heredoc es dato que entra por stdin, no una ruta que se lee: sin esto,
    # `cat > x.md <<'EOF' … informes … EOF` se denegaba solo (12-sep-2026). `sin_heredocs`
    # deja intacto el comando cuando el cuerpo sí puede ser peligroso (intérprete, sustitución
    # de comandos, terminador ausente). Este hook solo juzga LECTURA de rutas, así que aplicarlo
    # aquí es seguro; en muro_guard NO vale hacerlo antes de la allowlist ni del egress.
    crudo = cmd
    if ZC is not None:
        cmd = ZC.sin_heredocs(cmd)
    # Heredoc que NO se pudo despojar = cuerpo peligroso (intérprete, `$(…)`, sin terminador).
    # Ahí el salto de línea NO separa nada: partir por líneas convertiría la primera palabra de
    # cada línea del cuerpo en un «binario», y `rutas_clinicas_en_tokens` se salta el binario.
    # Un `python3 - <<PY` con rutas clínicas dentro se colaba entero (visto en la reproducción
    # del 20-sep-26: 8 comandos pasaron de DENY a ALLOW). Se juzga junto, como antes.
    peligroso = "<<" in crudo and cmd == crudo
    # Y solo se trocea por líneas si la VENTANILLA aparece en el comando. Fuera de ahí, el
    # troceo queda idéntico al de casa base, así que este arreglo solo puede denegar MÁS, nunca
    # menos: la reproducción del 20-sep-26 cazó 15 lecturas que se colaban al partir cualquier
    # script en líneas (la primera palabra de cada línea se salta por ser «el binario»).
    if peligroso or VENTANILLA not in crudo:
        lineas = [cmd]
    else:
        lineas = _lineas_logicas(cmd)
    subs = []
    for linea in lineas:
        try:
            lex = shlex.shlex(linea, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            subs.append((linea.split(), "?"))     # «?» = no sabemos qué es: no se exime
            continue
        cur = []
        for t in tokens:
            if t in _SEPARADORES:
                if cur:
                    subs.append((cur, t))
                cur = []
            else:
                cur.append(t)
        if cur:
            subs.append((cur, ";"))
    return subs


def _salta_prefijos(toks):
    """Índice del primer token que de verdad es el COMANDO.

    `do python3 … lector_clinico.py`, `time …`, `timeout 900 …`: palabras que preceden al
    comando sin serlo. La lista es corta y cerrada a propósito — `cat` no está, así que
    `cat tools/lector_clinico.py <ruta clínica>` se sigue juzgando entero.
    """
    i = 0
    while i < len(toks):
        t = toks[i]
        if _ASIGNACION.match(t) or t == "env" or t in _PREFIJOS:
            i += 1
            continue
        if t in _PREFIJOS_CON_ARG:
            i += 1
            while i < len(toks) and (toks[i].startswith("-") or _DURACION.match(toks[i])):
                i += 1
            continue
        break
    return i


def _es_llamada_ventanilla(sub):
    """¿Este subcomando EJECUTA la ventanilla? (`[prefijos] [VAR=x] [python3] …/lector_clinico.py`)

    Un comentario, un echo o un argumento que la mencione no la ejecuta: `#` corta la línea en
    shlex, y el nombre tiene que ser el binario o el script que recibe el intérprete. Y no
    basta con que se LLAME así: `_es_la_ventanilla_real` comprueba que sea el fichero.
    """
    toks = [str(t) for t in sub]
    i = _salta_prefijos(toks)
    if i >= len(toks):
        return False
    if _es_la_ventanilla_real(toks[i]):
        return True
    return (bool(_PYTHON.match(os.path.basename(toks[i])))
            and i + 1 < len(toks) and _es_la_ventanilla_real(toks[i + 1]))


def _cabecera_for(toks):
    """`(var, primer_token_del_cuerpo)` si el subcomando es la cabecera de un bucle."""
    i = 0
    while i < len(toks) and str(toks[i]) in _PREFIJOS:
        i += 1
    if i + 3 > len(toks) or str(toks[i]) not in ("for", "select") or str(toks[i + 2]) != "in":
        return None
    return str(toks[i + 1])


def _usa(toks, var):
    ref = _ref_var(var)
    return any(ref.search(str(t)) for t in toks)


def _solo_imprime(toks, sep):
    """`echo "== $f"` imprime el NOMBRE; no abre el fichero.

    Con tubería sí lo abriría (`echo "$f" | xargs cat`), y con una sustitución de comandos
    dentro también (`echo "$(cat "$f")"`): las dos cosas descalifican al `echo`.
    """
    if sep == "|":
        return False
    if any(("$(" in str(t) or "`" in str(t)) for t in toks):
        return False
    i = _salta_prefijos([str(t) for t in toks])
    return i < len(toks) and os.path.basename(str(toks[i])) in _NO_LEEN


def _bucles_eximidos(subs):
    """Índices de las cabeceras `for` cuya LISTA no se juzga.

    `for f in <rutas clínicas>` no lee: reparte. Lo que lee es el cuerpo, y ahí sí se ve quién
    consume la variable. Se exime la lista SOLO si todo lo que usa `$f` pasa por la ventanilla
    (o se limita a imprimir el nombre), así que `for f in <clínico>; do cat "$f"; done` sigue
    denegado. Sin esta regla, iterar el historial por la ventanilla —el uso más común— moría.
    """
    fuera = set()
    for i, (toks, _sep) in enumerate(subs):
        var = _cabecera_for(toks)
        if not var:
            continue
        usos, limpio = 0, True
        for toks2, sep2 in subs[i + 1:]:
            if _cabecera_for(toks2):
                break                      # otro bucle: el cuerpo de éste se acabó
            if not _usa(toks2, var):
                if toks2 and str(toks2[0]) == "done":
                    break
                continue
            usos += 1
            if not (_es_llamada_ventanilla(toks2) or _solo_imprime(toks2, sep2)):
                limpio = False
                break
        if usos and limpio:
            fuera.add(i)
    return fuera


def _juzgables(cmd):
    """Los subcomandos que de verdad LEEN.

    Quedan fuera dos cosas que no son lectura: la ventanilla (que registra ella misma) y la
    cabecera de un bucle cuyo cuerpo solo lee por la ventanilla.
    """
    subs = _subcomandos(cmd)
    eximidos = _bucles_eximidos(subs)
    fuera = [toks for i, (toks, sep) in enumerate(subs)
             if sep == "?" or (i not in eximidos and not _es_llamada_ventanilla(toks))]
    return subs, fuera


def deny(tool, ruta):
    _log("DENY", tool, ruta)
    sys.stderr.write(
        "MURO ⛔ lectura de datos clínicos fuera de la ventanilla auditada.\n"
        "  ruta: %s\n"
        "  Usa la vía sancionada, que deja registro:\n"
        "      python3 tools/lector_clinico.py <ruta>              # texto\n"
        "      python3 tools/lector_clinico.py --texto <ruta.pdf>  # capa de texto de un PDF\n"
        "      python3 tools/lector_clinico.py --a <destino> <ruta>  # copia dentro de zona clínica\n"
        "      python3 tools/lector_clinico.py --binario <ruta>    # bytes crudos, para tubear\n"
        "      python3 tools/lector_clinico.py procesa <proc> -- <args>   # visor3d, ocr_informes\n"
        "  (o exporta MURO_ALLOW_CLINICAL=1 si eres el comité clínico y sabes lo que haces)\n"
        % ruta)
    sys.exit(2)


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return  # fail-open: un payload raro no puede bloquear a {{TITULAR}}

    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}

    if os.environ.get("MURO_ALLOW_CLINICAL") == "1":
        # El bypass del comité clínico SÍ deja traza. Antes hacía `return` antes de loguear,
        # o sea que el único acceso que de verdad interesa auditar era el único invisible.
        _log("BYPASS (MURO_ALLOW_CLINICAL)", tool, str(ti.get("file_path")
                                                       or ti.get("path")
                                                       or ti.get("command") or "")[:160])
        return

    # 1) lecturas con tool nativa
    if tool in LECTURAS:
        for k in ("file_path", "notebook_path", "path"):
            v = ti.get(k)
            if v and _ruta_clinica(v):
                deny(tool, str(v))
        pat = ti.get("pattern")
        if pat and _patron_clinico(pat):
            deny(tool, str(pat))
        return

    # 2) Bash: leer el clínico a mano (cat/head/grep/cp/rsync…)
    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        subs, resto = _juzgables(cmd)
        if len(resto) < len(subs):
            _log("ALLOW (ventanilla)", tool, cmd[:120])
        if ZC is None:
            if any(_es_clinico_fallback(" ".join(s)) for s in resto):
                deny(tool, cmd[:160])
            return
        # Se juzgan los ARGUMENTOS resueltos, no la cadena: mencionar una ruta clínica en un
        # patrón de grep no es leerla, y denegarlo paralizaba el trabajo sobre el propio muro.
        for sub in resto:
            malas = ZC.rutas_clinicas_en_tokens(sub)
            if malas:
                deny(tool, malas[0][:160])


if __name__ == "__main__":
    # Watchdog (25-sep-26): si tardo más que el timeout de settings (8 s), deniego yo antes de
    # que Claude Code me cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("clinico_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(8, 'clinico_guard')
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # fail-open, pero ruidoso
        sys.stderr.write("clinico_guard: error interno (%r) — dejo pasar, pero REVÍSAME\n" % e)
