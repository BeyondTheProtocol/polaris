#!/usr/bin/env python3
"""rama_vista_guard.py — commiteas en la rama que VISTE, no en la que otra sesión dejó.

NORMA (registro: tools/normas.json) `feedback-web-repo-checkout-compartido`, clase BLOQUEO y
`repetida: true`: «cuidado — el repo web (~/projects/titular-{{APELLIDO}}-case) NO está
worktree-aislado; sesiones paralelas cambian la rama bajo tus pies. Verifica la rama justo
antes de cada commit/push».

ES UN TOCTOU, no un descuido. Entre que miro la rama y commiteo hay una ventana, y en esa
ventana otra sesión hace `checkout`. No se arregla acordándose: se arregla comprobando en el
instante de commitear que la rama sigue siendo la que esta sesión vio la última vez.

POR QUÉ NO SOLO EL REPO WEB. La norma nombra el repo web porque es donde le mordió, pero medido
(18-sep-26): el repo web tuvo 2 sesiones a la vez 1 día de 10, mientras que en CASA BASE la
concurrencia es constante — en la sesión que escribió esto, `git branch --show-current` dijo
`master` y dos comandos después el árbol estaba en `muro-saldo-agotado-y-falso-positivo-llavero`,
sin que esta sesión tocara nada. El mecanismo es el mismo en los dos sitios, así que vigila
CUALQUIER repo. Nombrar solo el web habría dejado fuera el caso más frecuente.

CÓMO FUNCIONA (detección de DERIVA, no verificación obligatoria):
  · cualquier `git` en un repo R desde la sesión S → apunta la rama actual como «la última que
    S vio en R». Es gratis: el comando anterior ya establece la línea base.
  · `git commit` / `git push` en R → si la rama actual NO es la apuntada, alguien la cambió en
    la ventana → DENY, diciendo cuál era y cuál es.
  · `git checkout` / `git switch` → esta sesión SÍ está cambiando a propósito: se apunta la
    rama destino leída del comando. Si el destino no se puede leer sin ambigüedad (un
    `checkout -- fichero`, un commit suelto, un `--detach`), se BORRA el apunte y la siguiente
    orden de git vuelve a establecer la línea base. Preferimos perder una detección a inventar.

Sin apunte previo (primer contacto de la sesión con ese repo) → se apunta y se PERMITE. Esto
detecta que la rama CAMBIÓ, no exige haber mirado: exigirlo denegaría el primer commit de cada
sesión y sería un estorbo sin cazar nada que no cace esto.

El apunte vive en `<casa base>/tools/state/rama_vista/<session_id>.json`. Por sesión, así que
dos sesiones en paralelo no se pisan el apunte (sería el mismo bug otra vez).

FAIL-OPEN, como sus hermanos (worktree_guard, singleton_guard, egreso_guard): atrapa un despiste
en interactivo; si el guard revienta, dejarla sin poder commitear sería peor que el despiste.
Escotilla explícita: `BTP_RAMA_OK=1`.

LÍMITE: si algún día abre una sesión con el repo web COMO proyecto (hoy no ocurre: las 10
sesiones que lo tocan son de claudecode y entran por `cd`), este hook no se cargaría, porque
~/projects/titular-{{APELLIDO}}-case no tiene su propio .claude/settings.json.

Contrato de hooks (code.claude.com/docs/hooks): exit 0 permite · exit 2 deniega.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from singleton_guard import subcomandos, _git_sub  # noqa: E402  (mismo tokenizador)

VERIFICAN = {"commit", "push"}
CAMBIAN = {"checkout", "switch"}
CADUCIDAD = 7 * 24 * 3600     # apuntes viejos: se barren solos


def _casa_base():
    ov = os.environ.get("BTP_WT_RAIZ_OVERRIDE")
    raiz = os.path.abspath(ov) if ov else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return raiz.split(os.sep + os.path.join(".claude", "worktrees") + os.sep)[0]


def _apuntes(session_id):
    base = os.environ.get("BTP_RAMA_VISTA_DIR") or os.path.join(
        _casa_base(), "tools", "state", "rama_vista")
    return os.path.join(base, "%s.json" % (session_id or "sin-sesion"))


def _leer(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _escribir(path, datos):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(datos, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def _rama_y_raiz(d):
    """(rama, raíz del repo) o (None, None) si ahí no hay repo o git no contesta."""
    try:
        p = subprocess.run(["git", "-C", d, "rev-parse", "--abbrev-ref", "HEAD", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        if p.returncode != 0:
            return None, None
        lineas = [x.strip() for x in p.stdout.splitlines() if x.strip()]
        return (lineas[0], os.path.abspath(lineas[1])) if len(lineas) >= 2 else (None, None)
    except Exception:
        return None, None


def _destino_de_cambio(sg, resto):
    """Rama a la que va un checkout/switch, o None si no se puede leer sin ambigüedad."""
    if "--" in resto or "--detach" in resto:
        return None                      # restaurar ficheros / HEAD suelto: no es cambiar de rama
    for flag in ("-b", "-B", "-c", "-C"):
        if flag in resto:
            i = resto.index(flag)
            if i + 1 < len(resto):
                return resto[i + 1]
            return None
    libres = [a for a in resto if not a.startswith("-")]
    if len(libres) == 1:
        return libres[0]
    return None


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return 0
    if os.environ.get("BTP_RAMA_OK") == "1":
        return 0
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        return 0
    path = _apuntes(data.get("session_id"))
    apuntes = _leer(path)
    ahora = time.time()
    for k, v in list(apuntes.items()):    # barrido de apuntes caducados
        if ahora - float(v.get("ts") or 0) > CADUCIDAD:
            apuntes.pop(k, None)

    aqui = data.get("cwd")
    tocado = False
    for sub in subcomandos(command):
        binario = os.path.basename(sub[0])
        if binario == "cd":
            d = next((a for a in sub[1:] if not a.startswith("-")), None)
            if d and "$" not in d:
                d = os.path.expanduser(d)
                aqui = d if os.path.isabs(d) else os.path.join(aqui or "", d)
            continue
        if binario != "git":
            continue
        args = sub[1:]
        destino = aqui
        for i, a in enumerate(args):
            if a == "-C" and i + 1 < len(args):
                destino = os.path.expanduser(args[i + 1])
        if not destino:
            continue
        sg, resto = _git_sub(args)
        rama, raiz = _rama_y_raiz(destino)
        if not raiz:
            continue                      # ahí no hay repo: nada que vigilar

        if sg in CAMBIAN:
            nueva = _destino_de_cambio(sg, resto)
            if nueva:
                apuntes[raiz] = {"rama": nueva, "ts": ahora}
            else:
                apuntes.pop(raiz, None)
            tocado = True
            continue

        if sg in VERIFICAN:
            visto = (apuntes.get(raiz) or {}).get("rama")
            if visto and rama and visto != rama:
                _escribir(path, apuntes)
                sys.stderr.write(
                    "RAMA ⛔ `git %s` sobre una rama que esta sesión NO vio.\n"
                    "   repo:     %s\n"
                    "   viste:    %s\n"
                    "   está en:  %s\n"
                    "   Otra sesión la cambió bajo tus pies. Mira qué hay ahí antes de seguir;\n"
                    "   si el árbol es de otra sesión, vuelve a la tuya en vez de commitear aquí.\n"
                    "   Si ya lo has comprobado: BTP_RAMA_OK=1.\n" % (sg, raiz, visto, rama))
                return 2

        if rama:
            apuntes[raiz] = {"rama": rama, "ts": ahora}
            tocado = True

    if tocado:
        _escribir(path, apuntes)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)   # FAIL-OPEN deliberado (ver cabecera)
