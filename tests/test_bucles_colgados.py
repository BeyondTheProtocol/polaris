#!/usr/bin/env python3
"""test_bucles_colgados.py — batería del barrido de bucles de espera colgados
(tools/bucles_colgados.py). Ver feedback-bucles-espera-con-tope.

Lanza procesos REALES efímeros (zsh/sleep) y comprueba, con un umbral bajo INYECTADO (segundos,
no las 2h reales) y una cadena de padres INYECTADA (así el test no depende de correr dentro de
una sesión de Claude Code de verdad):

  · un `zsh -c 'until … sleep …; done'` cuya cadena de padres SÍ pasa por claude → se para;
  · uno igual cuya cadena de padres NO pasa por claude → no se toca;
  · un `sleep` suelto (sin shell de bucle) → no se toca;
  · un bucle por debajo del umbral → no se toca;
  · `dry_run=True` → no mata (solo detecta);
  · `ps` roto → no mata (fail-safe) y no revienta;
  · un `zsh -c` que ejecuta un script con SUS PROPIAS palabras "while"/"sleep" (p. ej. un
    heredoc de Python con un `while … time.sleep(...)` interno, con timeout propio) pero SIN la
    gramática de shell "do … done" → no se confunde con un bucle de shell sin tope (caso real
    visto en esta máquina el 14-sep-26: otra sesión de Claude probando el MCP de X con un
    `zsh -c "eval 'python3 <<EOF ... while ...: time.sleep(30) ... EOF'"`).

Si tocas la lógica de detección/parada → añade el caso aquí (regresión permanente).
"""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Aislado ANTES de importar (STATE/LOG se fijan al importar el módulo): esta rama vive en un
# worktree y este test para procesos reales — bajo NINGÚN concepto toca tools/state/ de casa
# base. Ver feedback-estado-vivo-resuelve-casa-base.
_TMP = tempfile.mkdtemp(prefix="bucles_colgados_test_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bucles_colgados as bc  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _existe(pid):
    return bc._existe(pid)


def _spawn_bucle():
    """zsh en bucle de espera sin tope real (lo para el test, o se limpia en el finally)."""
    p = subprocess.Popen(
        ["/bin/zsh", "-c", "until false; do sleep 1; done"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _esperar_visible(p.pid)
    return p


def _spawn_sleep():
    p = subprocess.Popen(["sleep", "100"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _esperar_visible(p.pid)
    return p


def _spawn_falso_bucle_script():
    """zsh cuyo comando lleva, en TEXTO, sus propias palabras "while"/"sleep" (como un heredoc
    de Python con un bucle acotado propio) pero SIN la gramática de shell "do … done". El `:`
    (no-op) ignora la cadena; el `sleep 3` real solo mantiene vivo al zsh lo justo para que
    `ps` lo vea."""
    cmd = ": 'while True: time.sleep(30)  # bucle interno del script, no del shell' ; sleep 3"
    p = subprocess.Popen(["/bin/zsh", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _esperar_visible(p.pid)
    return p


def _esperar_visible(pid, tope_seg=2.0):
    """Espera (con tope) a que `ps` real ya vea el pid — arranque asíncrono de Popen."""
    deadline = time.time() + tope_seg
    while time.time() < deadline:
        lista = bc._listar_ps()
        if lista is not None and any(p["pid"] == pid for p in lista):
            return True
        time.sleep(0.05)
    return False


def _matar_si_vive(p):
    if p.poll() is None:
        try:
            p.kill()
        except Exception:
            pass
        try:
            p.wait(timeout=5)
        except Exception:
            pass


# ── 1) Bucle real, bajo_claude_fn=True, umbral bajo → se para ────────────────────────────────
proc = _spawn_bucle()
try:
    alertas, info = bc.run(dry_run=False, umbral_seg=0, bajo_claude_fn=lambda pid: pid == proc.pid)
    time.sleep(0.3)
    check("bajo_claude=True: el bucle se para", not _existe(proc.pid))
    check("bajo_claude=True: hay alerta de parada", any(a[0] == "bucle_colgado_parado" for a in alertas))
    check("bajo_claude=True: info cuenta 1 candidato", info.get("candidatos") == 1)
finally:
    _matar_si_vive(proc)

# ── 2) Bucle real, bajo_claude_fn=False (padre NO es claude) → no se toca ────────────────────
proc = _spawn_bucle()
try:
    alertas, info = bc.run(dry_run=False, umbral_seg=0, bajo_claude_fn=lambda pid: False)
    time.sleep(0.3)
    check("bajo_claude=False: el proceso ajeno a claude sigue vivo", _existe(proc.pid))
    check("bajo_claude=False: cero candidatos", info.get("candidatos", 0) == 0)
finally:
    _matar_si_vive(proc)

# ── 3) `sleep` suelto (sin shell de bucle) → no se toca aunque bajo_claude_fn diga que sí ────
# bajo_claude_fn se restringe al PID del propio target (no un blanket True): con umbral_seg=0
# sobre TODOS los procesos de la máquina real, un True incondicional también "aprobaría" otros
# shells en bucle legítimos que ya estén corriendo (daemons de usuario, otra sesión) y el test
# dejaría de probar lo que dice probar. Aquí basta con que "sleep 100" no case con el patrón
# shell+bucle: bajo_claude_fn ni siquiera debería llegar a evaluarse para él.
proc = _spawn_sleep()
try:
    alertas, info = bc.run(dry_run=False, umbral_seg=0, bajo_claude_fn=lambda pid: pid == proc.pid)
    time.sleep(0.3)
    check("sleep suelto: sigue vivo (no es un shell en bucle)", _existe(proc.pid))
    check("sleep suelto: no aparece entre los candidatos",
          not any(r.get("pid") == proc.pid for r in info.get("resultado", [])))
finally:
    _matar_si_vive(proc)

# ── 4) Bucle por debajo del umbral → no se toca ──────────────────────────────────────────────
proc = _spawn_bucle()
try:
    alertas, info = bc.run(dry_run=False, umbral_seg=99999, bajo_claude_fn=lambda pid: pid == proc.pid)
    time.sleep(0.3)
    check("bajo el umbral: sigue vivo", _existe(proc.pid))
    check("bajo el umbral: cero candidatos", info.get("candidatos", 0) == 0)
finally:
    _matar_si_vive(proc)

# ── 5) dry_run=True → detecta pero no mata ───────────────────────────────────────────────────
proc = _spawn_bucle()
try:
    alertas, info = bc.run(dry_run=True, umbral_seg=0, bajo_claude_fn=lambda pid: pid == proc.pid)
    time.sleep(0.3)
    check("dry-run: sigue vivo", _existe(proc.pid))
    check("dry-run: sí detecta el candidato", info.get("candidatos") == 1)
    check("dry-run: alerta de detección, no de parada",
          any(a[0] == "bucle_colgado_detectado" for a in alertas)
          and not any(a[0] == "bucle_colgado_parado" for a in alertas))
finally:
    _matar_si_vive(proc)

# ── 6) `ps` roto → fail-safe: no mata nada y no revienta ─────────────────────────────────────
proc = _spawn_bucle()
try:
    def _ps_roto():
        raise RuntimeError("ps no disponible (simulado)")

    alertas, info = bc.run(dry_run=False, umbral_seg=0, ps_runner=_ps_roto,
                            bajo_claude_fn=lambda pid: True)
    time.sleep(0.2)
    check("ps roto: no revienta (devuelve alertas/info)", isinstance(alertas, list) and isinstance(info, dict))
    check("ps roto: se marca el fallo", info.get("ps_error") is True)
    check("ps roto: el proceso real sigue vivo (fail-safe, no se mata a ciegas)", _existe(proc.pid))
    check("ps roto: cero candidatos reportados", "candidatos" not in info)
finally:
    _matar_si_vive(proc)

# ── 7) "while"/"sleep" en el TEXTO del comando, sin gramática de shell do/done → no se toca ──
# Caso real visto en esta máquina el 14-sep-26: otro `zsh -c` corriendo un heredoc de Python con
# su propio `while … time.sleep(30)` (timeout acotado del SCRIPT, no un bucle de shell sin tope).
proc = _spawn_falso_bucle_script()
try:
    alertas, info = bc.run(dry_run=False, umbral_seg=0, bajo_claude_fn=lambda pid: pid == proc.pid)
    time.sleep(0.3)
    check("while/sleep propios del script (sin do/done): sigue vivo", _existe(proc.pid))
    check("while/sleep propios del script (sin do/done): no es candidato",
          not any(r.get("pid") == proc.pid for r in info.get("resultado", [])))
finally:
    _matar_si_vive(proc)

# ── 8) Un residuo nacido ANTES del cierre de la deuda no la reabre ──────────────────────────
# 20-sep-26: la deuda se cerró con el aviso al escribir el bucle (20:46) y a las 2,6 h estaba
# ESCALADA como «regresión». Los 7 bucles matados ese día habían empezado antes de las 20:46.
# Matarlos está bien; contarlos como recaída hacía parecer roto un arreglo que funciona.
import deuda as _deu  # noqa: E402
_cargar_real = _deu._cargar
try:
    CIERRE = 1_000_000.0
    _deu._cargar = lambda: {"bucle_espera_sin_tope": {"estado": "cerrado", "cerrado_ts": CIERRE}}
    ahora = CIERRE + 3600                                   # una hora después del cierre
    viejo = {"pid": 1, "etime": "02:00:00", "cmd": "x"}      # empezó 1 h ANTES del cierre
    nuevo = {"pid": 2, "etime": "30:00", "cmd": "y"}         # empezó 30 min DESPUÉS
    n = bc._nacidos_tras_cierre([viejo, nuevo], "bucle_espera_sin_tope", ahora=ahora)
    check("un bucle nacido ANTES del cierre no cuenta como recaída",
          all(p["pid"] != 1 for p in n))
    check("uno nacido DESPUÉS del cierre sí cuenta (no se silencia la regresión real)",
          any(p["pid"] == 2 for p in n))
    _deu._cargar = lambda: {"bucle_espera_sin_tope": {"estado": "escalado", "veces": 3}}
    check("si la deuda NO está cerrada, todo cuenta (ante la duda se reporta)",
          len(bc._nacidos_tras_cierre([viejo, nuevo], "bucle_espera_sin_tope", ahora=ahora)) == 2)
    def _revienta():
        raise RuntimeError("libro ilegible")
    _deu._cargar = _revienta
    check("si no se puede leer el libro, todo cuenta (fail-closed)",
          len(bc._nacidos_tras_cierre([viejo, nuevo], "bucle_espera_sin_tope", ahora=ahora)) == 2)
finally:
    _deu._cargar = _cargar_real

# El extracto del registro es el BUCLE, no el preámbulo del shell de Claude Code (25-sep-2026: las
# cinco paradas registradas decían «source …shell-snapshots…» y ninguna qué bucle era).
_envuelto = (r"/bin/zsh -c source /Users/x/.claude/shell-snapshots/snapshot-zsh-1-a.sh 2>/dev/null "
             r"|| true && setopt NO_EXTENDED_GLOB NO_BARE_GLOB_QUAL 2>/dev/null || true && "
             r"{ \builtin unalias -- 'unsetenv'; } >/dev/null 2>&1 || true && eval 'until grep -q "
             r"LISTO /tmp/x.log; do sleep 5; done; echo '\''fin'\''' < /dev/null && pwd -P >| "
             r"/tmp/claude-ab12-cwd")
_ex = bc._redactar_truncar(_envuelto)
check("el extracto es el bucle, no el preámbulo (%s)" % _ex,
      _ex.startswith("until grep -q LISTO") and "shell-snapshots" not in _ex and "echo 'fin'" in _ex)
check("sin envoltorio, el comando tal cual",
      bc._redactar_truncar("while true; do sleep 1; done") == "while true; do sleep 1; done")
check("lo _PRIVADO se sigue tachando dentro del eval",
      "[REDACTADO]" in bc._redactar_truncar("eval 'until [ -f /a/_PRIVADO/b ]; do sleep 5; done' < /dev/null"))

print("test_bucles_colgados: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
