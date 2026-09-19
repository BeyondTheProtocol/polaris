#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regla_en_accion.py — PreToolUse: trae la regla JUSTO cuando se va a actuar.

EL PROBLEMA (25-jul-26)
-----------------------
Las reglas llegan al principio de la sesión (CLAUDE.md) o al escribir el mensaje
(`memoria_recall.sh`). Pero la acción arriesgada ocurre 40 turnos después, y encima
lo que inyecta un hook NO sobrevive a un `/compact`. Resultado medido: 22 memorias
registran correcciones que {{TITULAR}} ha tenido que repetir.

Este hook cierra ese hueco: mira lo que se va a EJECUTAR y, si toca una de las líneas
sensibles, recuerda la regla exacta en ese instante.

QUÉ NO ES
---------
No es el muro. **No bloquea** por defecto: quien bloquea es `muro_guard.py` (lazo 24/7)
y `clinico_guard.py` (lectura clínica). Duplicar el bloqueo aquí daría dos sitios que
mantener y una sesión rota cuando discrepen. Aquí se RECUERDA.

La excepción son cuatro acciones donde un recordatorio silencioso no basta y se pide
confirmación (`ask`): push del repo clínico, cargar launchd desde un worktree, desplegar
Air↔Polaris por fuera de `deploy_ff.sh`, y escribir en casa base desde un worktree. Se usa
`ask` porque su efecto está garantizado por la API de hooks; `additionalContext` es el
canal preferido para el resto, y si una versión de Claude Code lo ignorase, lo único que
se pierde es un recordatorio blando, no una protección.

LÍMITE DECLARADO de `escritura-en-casa-base` (25-jul-26)
--------------------------------------------------------
Cubre entera la vía Edit/Write/NotebookEdit, que es por donde pasa el 90% del riesgo. Por
Bash cubre solo las escrituras que sabe leer (`sed -i`, `tee`, `cp`, `mv`, redirección,
`python3 -c` con `open(`): el shell arbitrario no se puede cubrir del todo, y decirlo aquí
importa porque este freno nació justo de un fichero —`tools/normas.json`— que declaraba una
protección que no existía. Media verdad sobre una protección es peor que ninguna.

FAIL-OPEN: ante cualquier error interno, sale 0 y no estorba. Un bug aquí no puede
dejar a {{TITULAR}} sin trabajar.
"""
import hashlib
import json
import os
import re
import sys
import time

REPO = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.expanduser("~/claudecode")
LOG = os.path.join(REPO, ".claude", "logs", "regla-en-accion.log")
# Anti-ruido: cada regla se recuerda UNA vez por sesión. Repetirla en cada llamada
# la convertiría en ruido de fondo, que es justo como se pierden las reglas.
STAMP_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "btp_regla_en_accion")


# (clave, ¿aplica?, texto de la regla, ¿pedir confirmación?)
def _cmd(entrada):
    ti = entrada.get("tool_input") or {}
    return str(ti.get("command", "") or "")


def _rutas(entrada):
    ti = entrada.get("tool_input") or {}
    campos = [ti.get("file_path"), ti.get("path"), ti.get("notebook_path")]
    return " ".join(str(c) for c in campos if c)


def _en_worktree():
    return "/.claude/worktrees/" in os.path.abspath(REPO)


MARCA_WT = "/.claude/worktrees/"


def _casa_base():
    """Raíz de casa base. Si la sesión vive en un worktree, se DERIVA de su propia ruta
    (`<casa base>/.claude/worktrees/<rama>`) en vez de dar por hecho `~/claudecode`: así el
    freno sigue valiendo si el repo se mueve de sitio."""
    raiz = os.path.abspath(REPO)
    if MARCA_WT in raiz:
        return raiz.split(MARCA_WT)[0]
    return os.path.expanduser("~/claudecode")


def _versionado_en_casa_base(ruta):
    """True si `ruta` apunta a un fichero de casa base que git VERSIONA (o versionaría).

    El discriminador no es una lista de exclusiones a mano —esas se quedan viejas— sino lo que
    DEFINE el problema: bajo casa base, lo que git ignora existe SOLO ahí (el estado vivo de
    `tools/state/`, la fuente de verdad, lo clínico) y escribirlo desde un worktree es correcto y
    a menudo obligatorio; lo que git versiona tiene una COPIA en el worktree, y editar la de casa
    base es exactamente lo que anula el aislamiento.

    Se usa `check-ignore` y no `ls-files` porque acierta también con un fichero que todavía no
    existe: pregunta por la intención del `.gitignore`, no por el índice.

    Fail-OPEN (False ante cualquier duda): este hook nunca puede dejar a {{TITULAR}} sin trabajar."""
    try:
        if not ruta or not os.path.isabs(ruta):
            return False          # una ruta relativa resuelve DENTRO del worktree: esa es la buena
        base = _casa_base().rstrip(os.sep)
        real = os.path.realpath(ruta)   # realpath: que un symlink o un `..` no lo esquiven
        if not real.startswith(base + os.sep):
            return False
        # El marcador se busca en lo que va DESPUÉS de casa base, no en la ruta entera: la propia
        # casa base puede colgar de una carpeta que lleve `worktrees` en el nombre (pasa en la
        # batería, que corre desde un worktree) y entonces se rechazaba todo en silencio.
        if MARCA_WT.strip(os.sep) in real[len(base):].split(os.sep):
            return False
        import subprocess
        r = subprocess.run(["git", "-C", base, "check-ignore", "-q", real],
                           capture_output=True, timeout=4)
        return r.returncode != 0      # rc 0 = ignorado (vive solo aquí) → dejar pasar
    except Exception:
        return False


def _rutas_lista(entrada):
    ti = entrada.get("tool_input") or {}
    return [str(c) for c in (ti.get("file_path"), ti.get("path"),
                             ti.get("notebook_path")) if c]


# Escrituras por shell que sí se saben leer. NO es todo el shell posible y el docstring del
# módulo lo dice: prometer cobertura total sería repetir el error que originó este freno.
_ESCRITURA_SHELL = re.compile(
    r"(sed\s+-i|tee\b|\bcp\b|\bmv\b|>>?\s*/|python3?\s+-c\b.*open\()", re.I)


def _escritura_shell_a_casa_base(entrada):
    """Rutas de casa base versionadas que un comando de shell parece ir a ESCRIBIR."""
    cmd = _cmd(entrada)
    if not cmd or not _ESCRITURA_SHELL.search(cmd):
        return []
    base = _casa_base()
    # Rutas absolutas de casa base que aparezcan en el comando, con o sin comillas.
    candidatas = re.findall(re.escape(base) + r"[^\s\"';|&)]*", cmd)
    return [c for c in dict.fromkeys(candidatas) if _versionado_en_casa_base(c)]


def _texto_casa_base(entrada, _tool):
    """Mensaje CONCRETO: nombra el fichero y da la ruta buena, para que la salida no sea un «no»
    a secas sino el camino correcto."""
    malas = [r for r in _rutas_lista(entrada) if _versionado_en_casa_base(r)] \
        or _escritura_shell_a_casa_base(entrada)
    base, aqui = _casa_base(), os.path.abspath(REPO)
    detalle = ""
    if malas:
        buena = malas[0].replace(base.rstrip(os.sep), aqui.rstrip(os.sep), 1)
        detalle = "\n  vas a escribir: %s\n  deberías escribir: %s" % (malas[0], buena)
    return (
        "🛑 Estás en un worktree y esto escribe en CASA BASE, sobre un fichero que git versiona "
        "(o sea: existe una copia aquí, en tu rama). Eso anula el aislamiento — tus cambios se "
        "quedan sin commitear en casa base, donde otra sesión paralela los puede pisar." + detalle +
        "\n  Leer fuera del worktree está bien; el `cd` para leer NO autoriza a escribir ahí. "
        "El estado vivo (`tools/state/`), la fuente de verdad y lo clínico SÍ son de casa base y "
        "no se frenan. Ver `.claude/rules/tools-python.md`.")


REGLAS = [
    (
        "push-repo-clinico",
        lambda e, t: t == "Bash" and re.search(r"\bgit\s+push\b", _cmd(e)),
        "🛑 El repo NO se sube a GitHub: su historial lleva datos clínicos. "
        "Para mover código entre Air y Polaris la única vía es `tools/deploy_ff.sh` "
        "(fast-forward). Ver `.claude/rules/deploy-ff.md`.",
        True,
    ),
    (
        "launchd-desde-worktree",
        lambda e, t: (t == "Bash"
                      and re.search(r"launchctl\s+(load|bootstrap|enable|kickstart)", _cmd(e))
                      and _en_worktree()),
        "🛑 Estás en un worktree y esto enciende un daemon. Los daemons se activan SOLO "
        "desde casa base (`~/claudecode`) y SOLO con `tools/activar_daemon.py <label>`, "
        "que valida label único, no-worktree y concern único. Encenderlo es gate de {{TITULAR}}.",
        True,
    ),
    (
        "deploy-a-mano",
        lambda e, t: (t == "Bash"
                      and re.search(r"(rsync|scp).*(polaris|air)", _cmd(e), re.I)),
        "🛑 Desplegar copiando ficheros fue justo lo que forkeó Air↔Polaris el 12-jul y "
        "estuvo a punto de borrar trabajo. La única vía es `tools/deploy_ff.sh "
        "{to-polaris|from-polaris}`, que rehúsa si las dos historias divergen.",
        True,
    ),
    (
        # Rota TRES veces en este repo (26-jun, 28-jun, 25-jul), siempre igual: el fichero que
        # hace falta leer vive fuera del worktree (00_FUENTE-DE-VERDAD está gitignored), un
        # `cd ~/claudecode` para encontrarlo, y de ahí ya no se vuelve. El 25-jul fueron 13 tools
        # editadas en casa base; el 28-jun otra sesión paralela committeó lo mismo y el trabajo
        # sin commitear se perdió. Se pide confirmación UNA vez por sesión (_ya_dicho): pilla el
        # primer Edit, que es donde empieza el error, sin dar la lata trece veces.
        # DENY y no `ask` (corregido el 25-jul-26 tras probarlo en vivo): se implementó con `ask`
        # y la escritura pasó IGUAL. El log del hook y el sello anti-repetición prueban que la
        # regla disparó y emitió su `ask`; simplemente, con el modo de permisos de una sesión
        # normal ese `ask` se resuelve solo y nadie ve nada. Un freno que solo frena a veces es
        # el mismo problema que vine a arreglar. `deny` sí está garantizado — es lo que usa
        # `clinico_guard.py`. Se puede saltar a propósito con BTP_ALLOW_CASA_BASE=1, igual que
        # el comité clínico salta su guard con MURO_ALLOW_CLINICAL=1.
        "escritura-en-casa-base",
        lambda e, t: (_en_worktree()
                      and os.environ.get("BTP_ALLOW_CASA_BASE") != "1"
                      and ((t in ("Edit", "Write", "NotebookEdit")
                            and any(_versionado_en_casa_base(r) for r in _rutas_lista(e)))
                           or (t == "Bash" and _escritura_shell_a_casa_base(e)))),
        _texto_casa_base,
        "deny",
    ),
    (
        "hacia-fuera-sin-ok",
        lambda e, t: (re.search(r"(send|reply|publish|post|tweet)", t or "", re.I)
                      and not re.search(r"draft", t or "", re.I)),
        "🛑 Gate de salida: nada sale hacia fuera sin el OK explícito de {{TITULAR}}. "
        "Déjalo en BORRADOR y que firme ella.",
        False,
    ),
    (
        # Cazada por `tools/reglas_repetidas.py`: a {{TITULAR}} se le repitió 3 veces.
        "correo-formato",
        lambda e, t: re.search(r"draft|correo|email|mail", t or "", re.I),
        "✉️ El correo va en HTML (negritas en lo clave, enlaces clicables, listas), pasa "
        "por `voz-titular`, y se manda desde la cuenta del hilo. Queda en BORRADOR: firma "
        "{{TITULAR}}. Ver `.claude/rules/correo.md`.",
        False,
    ),
    (
        "copy-publicado",
        lambda e, t: (t in ("Edit", "Write", "NotebookEdit")
                      and re.search(r"(hero|landing|index\.(vue|html)|tagline)", _rutas(e), re.I)),
        "⚠️ Copy ya publicado: editarlo no es zona autónoma. Un «dale» rápido no basta, "
        "lo mergea {{TITULAR}}. Páginas o secciones NUEVAS sí son autónomas. "
        "Ver `.claude/rules/marca-copy.md`.",
        False,
    ),
    (
        "clinico",
        lambda e, t: re.search(r"_PRIVADO_|Clinico-PRIVADO", _rutas(e) + " " + _cmd(e)),
        "⚠️ Dato clínico: la lectura va por `tools/lector_clinico.py` (ventanilla auditada) "
        "y nada N2 (relato o informe crudo con nombre) sale a un SaaS sin contrato. "
        "De-identifica con `deid.py`. Ver `.claude/rules/clinico.md`.",
        False,
    ),
    (
        "constitucion",
        lambda e, t: (t in ("Edit", "Write")
                      and re.search(r"(CLAUDE\.md|MEMORY\.md|\.claude/rules/)", _rutas(e))),
        "📏 Estás tocando las reglas del sistema. Los límites son reales: MEMORY.md se corta "
        "a 200 líneas o 25KB (en silencio), y CLAUDE.md pierde adherencia por encima de 15KB. "
        "Después: `python3 tools/salud_memoria.py`. Ver `.claude/rules/memoria-sistema.md`.",
        False,
    ),
    (
        "borrado",
        lambda e, t: t == "Bash" and re.search(r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b", _cmd(e)),
        "⚠️ Borrado recursivo. Mira antes qué hay dentro: lo irreversible es gate de {{TITULAR}}, "
        "y lo clínico y el estado vivo no se recuperan de un `rm`.",
        False,
    ),
    (
        "dinero",
        lambda e, t: re.search(r"(pay|payment|checkout|purchase|transfer)", t or "", re.I),
        "🛑 Dinero: no se paga ni se mueve dinero. Se deja «a un clic» y firma {{TITULAR}}.",
        True,
    ),
]


def _ya_dicho(session, clave):
    """True si esta regla ya se recordó en esta sesión (y la marca si no)."""
    try:
        os.makedirs(STAMP_DIR, exist_ok=True)
        h = hashlib.sha1(("%s|%s" % (session, clave)).encode()).hexdigest()[:16]
        marca = os.path.join(STAMP_DIR, h)
        if os.path.exists(marca) and (time.time() - os.path.getmtime(marca)) < 6 * 3600:
            return True
        open(marca, "w").close()
        return False
    except Exception:
        return False   # ante la duda, mejor recordarla otra vez que callarla


def _log(clave, tool):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), clave, tool))
    except Exception:
        pass


def main():
    try:
        entrada = json.load(sys.stdin)
    except Exception:
        return 0
    tool = entrada.get("tool_name") or ""
    session = str(entrada.get("session_id") or "sin-sesion")

    disparadas = []
    for clave, aplica, texto, confirmar in REGLAS:
        try:
            if aplica(entrada, tool):
                disparadas.append((clave, texto, confirmar))
        except Exception:
            continue
    if not disparadas:
        return 0

    # El texto puede ser una función (entrada, tool) → str, para poder nombrar el fichero
    # concreto y la ruta buena en vez de soltar una regla genérica. Si esa función peta, se
    # cae a un texto neutro: fail-open, un mensaje feo antes que una sesión rota.
    def _render(texto):
        if not callable(texto):
            return texto
        try:
            return texto(entrada, tool)
        except Exception:
            return "🛑 Esta acción toca una regla del sistema. Revísala antes de seguir."

    disparadas = [(c, _render(t), k) for c, t, k in disparadas]
    for clave, _t, _k in disparadas:
        _log(clave, tool)

    # DENIEGAN. Va ANTES del filtro anti-repetición a propósito: un recordatorio se dice una vez,
    # pero un freno que solo frena la primera vez de la sesión no es un freno.
    #
    # Se usa la forma JSON `permissionDecision: "deny"` + exit 0, y NO `exit 2`.
    #
    # ⚠️ SIN CONFIRMAR EN VIVO (25-jul-26). Se probó escribiendo de verdad en casa base desde una
    # sesión en worktree y el resultado NO permite afirmar que frene:
    #   · con `ask`, la escritura pasó. El log del hook y el sello anti-repetición prueban que la
    #     regla disparó, así que el `ask` se resolvió solo sin que nadie viera nada.
    #   · con `exit 2`, también pasó. Ese rc SÍ frena un `Bash` (clinico_guard me paró ese mismo
    #     día con él), pero no frenó la tool `Write`.
    #   · a partir de ahí el hook dejó de CONSULTARSE para `Write` en esa carpeta —ni una línea
    #     más en su log—, que es justo lo que se esperaría si el harness cacheó el permiso que
    #     concedió aquel primer `ask`. O sea que la sesión quedó contaminada y no se pudo probar
    #     la versión `deny` en condiciones limpias.
    # Lo que SÍ está probado: la lógica de la regla (14 casos en tests/test_regla_en_accion.py) y
    # que invocado con la configuración de producción emite el `deny` correcto.
    # Queda por verificar en una sesión NUEVA. Hasta entonces `tools/normas.json` mantiene esta
    # norma en clase `contexto`, NO `bloqueo`, y la deuda `worktree_escritura_sin_freno` sigue
    # ABIERTA — que es el motivo entero por el que existe este freno: el fichero que lo originó
    # declaraba una protección que no existía, y repetir eso aquí sería peor que no hacer nada.
    deniegan = [(c, t) for c, t, k in disparadas if k == "deny"]
    if deniegan:
        motivo = "\n\n".join(t for _c, t in deniegan)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": motivo,
            "additionalContext": motivo,
        }}, ensure_ascii=False))
        return 0

    nuevas = [(c, t, k) for c, t, k in disparadas if not _ya_dicho(session, c)]
    if not nuevas:
        return 0

    texto = "\n".join(t for _c, t, _k in nuevas)
    pedir = any(k is True for _c, _t, k in nuevas)

    salida = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "REGLA QUE APLICA A ESTA ACCIÓN (de {{TITULAR}}, no es una sugerencia):\n" + texto,
    }}
    if pedir:
        salida["hookSpecificOutput"]["permissionDecision"] = "ask"
        salida["hookSpecificOutput"]["permissionDecisionReason"] = texto
    print(json.dumps(salida, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)   # fail-open, siempre
