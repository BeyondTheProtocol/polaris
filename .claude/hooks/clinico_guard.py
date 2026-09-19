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
  · Bash que invoque `tools/lector_clinico.py`             -> PERMITIDO (esa es la
    ventanilla SANCIONADA: deja registro de cada acceso).
  · `MURO_ALLOW_CLINICAL=1`                                -> bypass (comité clínico).
  · TODO intento (permitido o denegado) se registra en .claude/logs/clinico-access.log.

FAIL-OPEN A PROPÓSITO ante un error interno: en interactivo hay un humano delante, y un
bug en este hook no puede dejar a {{TITULAR}} sin poder trabajar. Los dientes fail-closed
viven en el lazo 24/7 (muro_guard). Aquí el objetivo es cerrar la lectura clínica y
crear la traza de auditoría que hoy no existe.
"""
import json
import os
import shlex
import sys
from datetime import datetime

REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.expanduser("~/claudecode")
LOG = os.path.join(REPO, ".claude", "logs", "clinico-access.log")

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

# La ventanilla auditada: si el comando la usa, se permite (ella misma registra).
VENTANILLA = "lector_clinico.py"

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


_SEPARADORES = {"|", "||", "&&", ";", "&", "|&"}


def _subcomandos(cmd):
    """Trocea un comando de shell en subcomandos (lista de tokens cada uno).

    Fail-CLOSED al tokenizar: si las comillas están mal cerradas no sabemos qué se está
    leyendo, así que devolvemos la línea entera como un solo subcomando y que la juzgue el
    predicado. Es el único punto de este hook que no es fail-open, y a propósito.
    """
    # El CUERPO de un heredoc es dato que entra por stdin, no una ruta que se lee: sin esto,
    # `cat > x.md <<'EOF' … informes … EOF` se denegaba solo (12-sep-2026). `sin_heredocs`
    # deja intacto el comando cuando el cuerpo sí puede ser peligroso (intérprete, sustitución
    # de comandos, terminador ausente). Este hook solo juzga LECTURA de rutas, así que aplicarlo
    # aquí es seguro; en muro_guard NO vale hacerlo antes de la allowlist ni del egress.
    cmd = ZC.sin_heredocs(cmd)
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return [cmd.split()]
    subs, cur = [], []
    for t in tokens:
        if t in _SEPARADORES:
            if cur:
                subs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        subs.append(cur)
    return subs


def deny(tool, ruta):
    _log("DENY", tool, ruta)
    sys.stderr.write(
        "MURO ⛔ lectura de datos clínicos fuera de la ventanilla auditada.\n"
        "  ruta: %s\n"
        "  Usa la vía sancionada, que deja registro:\n"
        "      python3 tools/lector_clinico.py <ruta>\n"
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
        if VENTANILLA in cmd:
            _log("ALLOW (ventanilla)", tool, cmd[:120])
            return
        if ZC is None:
            if _es_clinico_fallback(cmd):
                deny(tool, cmd[:160])
            return
        # Se juzgan los ARGUMENTOS resueltos, no la cadena: mencionar una ruta clínica en un
        # patrón de grep no es leerla, y denegarlo paralizaba el trabajo sobre el propio muro.
        for sub in _subcomandos(cmd):
            malas = ZC.rutas_clinicas_en_tokens(sub)
            if malas:
                deny(tool, malas[0][:160])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # fail-open, pero ruidoso
        sys.stderr.write("clinico_guard: error interno (%r) — dejo pasar, pero REVÍSAME\n" % e)
