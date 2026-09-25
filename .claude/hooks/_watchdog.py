"""_watchdog.py — un guard que BLOQUEA no puede agotar su timeout en silencio.

POR QUÉ EXISTE (25-sep-2026). Idea de {{CONTACTO}} (https://contacto), con su agente KAI,
revisión del 25-sep-2026. La doc oficial de Claude Code lo dice sin matices
(https://code.claude.com/docs/en/hooks.md, sección «Timeouts», abierta el 25-sep): *«A timed-out
`command` [...] hook doesn't block the tool call. The call continues through the normal permission
flow»*. O sea: un guard que tarda más que su `timeout` de settings DEJA PASAR. Con el Mac cargado
(el reinicio por memoria del 20-sep) eso es realista, y en el lazo 24/7 el muro es la única puerta.

QUÉ HACE. `armar(timeout_hook)` se llama como PRIMERA cosa del `__main__` de cada guard:
  · hace `fork()`. El HIJO sigue siendo el guard y hace su trabajo normal (stdin, stdout, exit).
  · el PADRE solo vigila: espera al hijo hasta `timeout_hook - MARGEN` segundos y devuelve su
    código de salida tal cual. Si el hijo no ha terminado, lo mata (SIGKILL) y DENIEGA (exit 2
    con el motivo en stderr), antes de que Claude Code llegue a cancelar el hook.
  · el padre no toca stdin ni stdout: lo que decide el guard le llega a Claude Code intacto.

POR QUÉ UN PROCESO Y NO UN HILO O SIGALRM. Un hilo o una señal solo actúan entre instrucciones de
Python: una regex con backtracking o una llamada C que no suelta el GIL los deja mudos justo
cuando más falta hacen. Un proceso padre aparte no depende de lo que haga el hijo.

LÍMITE CONOCIDO (dicho, no escondido). El reloj arranca al armar, no al lanzar el proceso: el
arranque del intérprete y los imports de cabecera del guard quedan fuera. Por eso el margen es
2 s y por eso existen las reglas `permissions.deny` de respaldo en `.claude/settings.json`, que
Claude Code aplica aunque el hook no llegue a responder. `muro_guard.sh` añade además su propio
reloj en bash, que arranca en milisegundos.
"""
import os
import signal
import sys
import time

MARGEN = 2.0        # segundos antes del timeout de settings en los que el padre corta
_PASO = 0.02        # sondeo del hijo; 50 veces por segundo, coste despreciable


def _codigo(status):
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 2        # muerto por señal: no llegó a decidir → no se deja pasar


def armar(timeout_hook, nombre, al_vencer=None, margen=MARGEN):
    """Vigila el resto del proceso. Vuelve SOLO en el hijo; el padre sale con el código del hijo.

    `al_vencer(presupuesto)` (opcional) decide qué hacer si se agota el plazo y devuelve el código
    de salida; por defecto, deny (exit 2). Solo el gate de salida (hook Stop) lo cambia.
    """
    presupuesto = max(0.5, float(timeout_hook) - margen)
    # Solo para los tests: BTP_WATCHDOG_S puede ACORTAR el plazo, nunca alargarlo (alargar es
    # justo volver al agujero; acortar solo deniega antes).
    try:
        presupuesto = min(presupuesto, float(os.environ.get("BTP_WATCHDOG_S") or presupuesto))
    except ValueError:
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
    except OSError as e:
        # Sin fork no hay vigilante. No se puede saber si el guard acabaría a tiempo: se sigue
        # sin él, avisando, y quedan las reglas deny de respaldo. Denegar aquí paralizaría toda
        # la sesión por un fallo del sistema operativo, no del guard.
        sys.stderr.write("%s: watchdog sin fork (%r); sigo sin vigilante\n" % (nombre, e))
        return
    if pid == 0:
        return                                      # hijo: es el guard, que siga
    limite = time.monotonic() + presupuesto
    while True:
        try:
            r, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            os._exit(2)
        if r == pid:
            os._exit(_codigo(status))
        if time.monotonic() >= limite:
            break
        time.sleep(_PASO)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except Exception:
        pass
    if al_vencer is not None:
        try:
            codigo = int(al_vencer(presupuesto))
            sys.stdout.flush()                      # os._exit no vacía los búferes
            sys.stderr.flush()
            os._exit(codigo)
        except Exception:
            pass
    sys.stderr.write("MURO ⛔ [%s] el guard no terminó en %.1f s (watchdog): deniego antes de que "
                     "el timeout de Claude Code lo convierta en un permitir.\n" % (nombre, presupuesto))
    sys.stderr.flush()
    os._exit(2)
