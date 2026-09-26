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

# Los dos falsos positivos del replay de 4.000 comandos (25-sep-2026), al pasar el hook a `deny`.
check("heredoc a python con «until … do sleep … done» en una cadena: NO es bucle de shell",
      not bc.bucle_sin_tope("python3 - <<'EOF'\ns = '''until grep -q X /tmp/l; do sleep 5; done'''\n"
                            "print(s)\nEOF"))
check("…y escrito a un fichero con cat tampoco",
      not bc.bucle_sin_tope("cat > /tmp/w.sh <<'EOF'\nuntil [ -f /tmp/x ]; do sleep 5; done\nEOF"))
check("pero un heredoc a bash SÍ se ejecuta: se sigue cazando",
      bc.bucle_sin_tope("cd /tmp && bash <<'EOF'\nuntil [ -f /tmp/x ]; do sleep 5; done\nEOF"))
check("y el bucle de shell FUERA del heredoc se sigue viendo",
      bc.bucle_sin_tope("python3 - <<'EOF'\nprint(1)\nEOF\nuntil [ -f /tmp/x ]; do sleep 5; done"))
check("esperar a una HORA (`date +%H%M`) cuenta como tope",
      not bc.bucle_sin_tope('while [ "$(date +%H%M)" -lt 1357 ]; do sleep 60; done; date'))
check("el caso que motivó el deny sigue cazado: esperar a un pid sin reloj",
      bc.bucle_sin_tope("while kill -0 76684 2>/dev/null; do sleep 60; done; tail -45 /tmp/b.log"))

# Batería adversarial (25-sep-2026): los payloads que la verificación ejecutó contra el detector
# al pasar el hook a `deny`. True = bucle de espera SIN tope (debe cazarse); False = legítimo.
_B = "until [ -f /tmp/x ]; do sleep 5; done"
_ADVERSARIAL = [
    ("H1 cat|bash", "cat <<EOF | bash\n%s\nEOF" % _B, True),
    ("H2 bash -c $(cat)", "bash -c \"$(cat <<'EOF'\n%s\nEOF\n)\"" % _B, True),
    ("H3 sh -s", "sh -s <<EOF\n%s\nEOF" % _B, True),
    ("H5 sudo bash", "sudo bash <<EOF\n%s\nEOF" % _B, True),
    ("H6 nohup bash &", "nohup bash <<'EOF' &\n%s\nEOF" % _B, True),
    ("H7 VAR=1 bash", "FOO=1 bash <<EOF\n%s\nEOF" % _B, True),
    ("H8 ksh", "ksh <<EOF\n%s\nEOF" % _B, True),
    ("H9 dash", "dash <<EOF\n%s\nEOF" % _B, True),
    ("H11 $((1<<3))", "x=$((1<<3)); echo $x\n%s" % _B, True),
    ("H12 here-string", "grep -q foo <<< abc\n%s" % _B, True),
    ("H13 <<-EOF", "bash <<-EOF\n\t%s\n\tEOF" % _B, True),
    ("H16 bucle tras heredoc", "cat <<EOF\nhola\nEOF\n%s" % _B, True),
    ("H18 <<< \"hola\"", "read -r v <<< \"hola\"\n%s" % _B, True),
    ("H19 env bash", "/usr/bin/env bash <<EOF\n%s\nEOF" % _B, True),
    ("H20 docker exec bash", "docker exec -i c1 bash <<EOF\n%s\nEOF" % _B, True),
    ("H21 ssh host bash", "ssh host bash <<EOF\n%s\nEOF" % _B, True),
    ("H27 cat|zsh", "cat <<'EOF' | zsh\n%s\nEOF" % _B, True),
    ("H28 cat|ssh", "cat <<'EOF' | ssh host\n%s\nEOF" % _B, True),
    ("H29 tee && bash", "tee /tmp/w.sh <<'EOF' >/dev/null && bash /tmp/w.sh\n%s\nEOF" % _B, True),
    ("L1 multilínea", "until [ -f /tmp/x ]\ndo\n  sleep 5\ndone", True),
    ("L3 while :", "while :; do sleep 5; done", True),
    ("L4 /bin/sleep", "while true; do /bin/sleep 5; done", True),
    ("L6 continuación \\", "while true; do \\\n  sleep 5; done", True),
    ("L15 forma típica", "until [ -f /tmp/x ]; do\n  sleep 5\ndone", True),
    ("L16 14-sep en 3 líneas", "while ! grep -q EXIT /tmp/log; do\n  sleep 5\ndone\necho listo", True),
    ("T1 # timeout", _B + " # timeout ", True),
    ("T2 echo date +%s", "echo \"date +%s\"; " + _B, True),
    ("T3 date +%H en log", "echo \"$(date +%H:%M) esperando\"; " + _B, True),
    ("T4 --connect-timeout", "until curl -s --connect-timeout 5 localhost:8080; do sleep 5; done", True),
    ("T6 $SECONDS fuera", _B + "; echo $SECONDS", True),
    ("T7 date +%s en log", "log=/tmp/run_$(date +%s).log; " + _B, True),
    ("F1 contador", "n=0; until [ -f /tmp/x ] || [ $n -ge 60 ]; do n=$((n+1)); sleep 5; done", False),
    ("F2 tries++", "while (( tries++ < 30 )); do sleep 2; done", False),
    ("F3 date '+%s'", "fin=$(( $(date '+%s') + 600 )); until [ -f /tmp/x ]; do [ $(date '+%s') -lt $fin ] "
                      "|| break; sleep 5; done", False),
    ("F4 date -u +%s", "fin=$(( $(date -u +%s) + 600 )); until [ -f /tmp/x ]; do [ $(date -u +%s) -lt $fin ] "
                       "|| break; sleep 5; done", False),
    ("F5 SECONDS <", "SECONDS=0; while (( SECONDS < 60 )); do sleep 1; done", False),
    ("F6 while read", "while read -r l; do echo $l; sleep 0.2; done < /tmp/lista", False),
    ("F7 python con cadena", "python3 - <<'EOF'\ns = 'until grep -q X f; do sleep 5; done'\nprint(s)\nEOF", False),
    ("F8 cat a notas", "cat <<'EOF' > /tmp/notas.md\nejemplo: %s\nEOF" % _B, False),
    ("F9 bash con contador", "bash <<'EOF'\nn=0; until [ -f /tmp/x ] || [ $n -ge 60 ]; do n=$((n+1)); sleep 5; "
                             "done\nEOF", False),
    ("F10 date +%H%M", "while [ \"$(date +%H%M)\" -lt 1357 ]; do sleep 60; done", False),
    ("F11 plantilla", "fin=$(( $(date +%s) + 600 )); until [ -f /tmp/x ]; do [ $(date +%s) -lt $fin ] "
                      "|| break; sleep 5; done", False),
    ("F13 i<10", "i=0; while [ $i -lt 10 ]; do i=$((i+1)); sleep 1; done", False),
    ("F14 ++n", "until [ -f /tmp/x ] || [ $(( ++n )) -gt 20 ]; do sleep 3; done", False),
    # Heredoc SIN cierre: ante la duda se LEE (segunda vuelta). Tragárselo hasta el final dejaba
    # pasar `git commit -m "usa <<EOF"` ↵ bucle real. Falso positivo raro a cambio de un hueco fácil.
    ("F15 cierre indentado (se lee)", "cat <<EOF\n%s\n  EOF\nuntil x; do sleep 1; done" % _B, True),
    ("F16 heredoc sin cierre (se lee)", "cat <<EOF > /tmp/n.md\nhola\n%s" % _B, True),
    ("F17 $SECONDS en cond", "until [ -f /tmp/x ] || [ $SECONDS -gt 600 ]; do sleep 5; done", False),
    ("F18 health con contador", "n=0; while [ $n -lt 60 ] && ! curl -sf localhost:8080/h; do n=$((n+1)); "
                                "sleep 5; done", False),
    ("git commit -F - con texto", "git commit -q -F - <<'EOF'\nfix: antes un `%s` sin tope\nEOF" % _B, False),
    ("timeout delante", "timeout 600 bash -c '%s'" % _B, False),
    # Del replay de 31.298 comandos reales: comparar un ESTADO EXTERNO con un número no es tope.
    ("estado externo -gt N", "until [ \"$(python3 -c 'print(1)')\" -gt 5000 ]; do sleep 5; done", True),
    ("pgrep|wc -eq 0", "until [ \"$(pgrep -f 'kb.py index' | wc -l)\" -eq 0 ]; do sleep 5; done", True),
    ("contador que se reinicia", "z=0; until [ $z -ge 2 ]; do n=$(lsof -i :8765 | wc -l); if [ \"$n\" = 0 ]; "
                                 "then z=$((z+1)); else z=0; fi; sleep 2; done", True),
    ("date +%H solo en el echo final", "while pgrep -f run_agent.sh >/dev/null; do sleep 30; done; "
                                       "echo \"terminó $(date +%H:%M:%S)\"", True),
    ("git commit -m con el texto", "git add x && git commit -q -m \"feat: avisar de %s\"" % _B, False),
    # Segunda vuelta adversarial (25-sep-2026), lo realista.
    ("D1 'done' en un echo del cuerpo", "until [ -f /tmp/x ]; do echo \"not done yet\"; sleep 5; done", True),
    ("F15b 'until' en una cadena + for acotado", "echo 'esperando until que arranque'; for i in 1 2 3; "
                                                 "do curl -s x && break; sleep 2; done", False),
    ("F16b 'until' en un fichero + for", "cp /tmp/until.txt /tmp/y; for i in 1 2 3; do sleep 1; done", False),
    ("A12 tail -f | while read", "tail -f /tmp/log | while read -r l; do echo $l; sleep 1; done", True),
    ("A13 while read < <(tail -f)", "while read -r l; do sleep 1; done < <(tail -f /tmp/log)", True),
    ("A2 heredoc sin comillas con $( )", "cat <<EOF > /tmp/n.txt\n$(%s)\nEOF" % _B, True),
    ("A10 '<<EOF' dentro de un -m", "git commit -m \"usa <<EOF para heredocs\"\n%s" % _B, True),
    ("bash -c 'while …'", "bash -c 'while true; do sleep 5; done'", True),
    ("R1 contador contra $MAX", "n=0; until [ -f /tmp/x ] || [ $n -ge $MAX ]; do n=$((n+1)); sleep 5; done",
     False),
    ("R2 (( n < MAX ))", "n=0; while (( n < MAX )); do n=$((n+1)); sleep 1; done", False),
    ("R3 let i=i+1", "i=0; while [ $i -lt 10 ]; do let i=i+1; sleep 1; done", False),
    ("R4 ((i=i+1))", "i=0; while [ $i -lt 10 ]; do ((i=i+1)); sleep 1; done", False),
    ("R5 test $i -lt 10", "i=0; while test $i -lt 10; do i=$((i+1)); sleep 1; done", False),
    ("R6 reloj en variable", "fin=$(( $(date +%s) + 600 )); until [ -f /tmp/x ]; do now=$(date +%s); "
                             "[ $now -lt $fin ] || break; sleep 5; done", False),
    ("R7 date +'%s'", "fin=$(( $(date +'%s') + 600 )); until [ -f /tmp/x ]; do [ $(date +'%s') -lt $fin ] "
                      "|| break; sleep 5; done", False),
    ("R8 n+0 no es contador", "n=0; until [ -f /tmp/x ] || [ $n -ge 60 ]; do n=$((n+0)); sleep 5; done", True),
    ("R9 # timeout en comentario", "# timeout 5 min de margen\n%s" % _B, True),
]
for _nombre, _payload, _esperado in _ADVERSARIAL:
    check("adversarial %s → %s" % (_nombre, _esperado), bc.bucle_sin_tope(_payload) is _esperado)


# ── Chrome headless huérfano (26-sep-2026, deuda chrome_headless_huerfano) ───────────────────
# Tres Chrome de un script de captura muerto pasaron ~27 h al 100 % de CPU. La DETECCIÓN se
# prueba con un `ps` inyectado (no se puede fingir un Chrome real sin lanzarlo); la PARADA, con
# procesos reales efímeros (un `sleep` como «Chrome» y otro como su pestaña).
_UID = os.getuid()
_CH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_HELPER = ("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/"
           "Versions/154.0.8037.57/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/"
           "Google Chrome Helper (Renderer) --type=renderer --user-data-dir=/tmp/cap-9561")


def _ps(*filas):
    return lambda: "\n".join("%d %d %s %d %s" % f for f in filas) + "\n"


_HUERFANO = (101, 1, "1-03:12:00", _UID, _CH + " --headless=new --remote-debugging-port=9561 "
             "--user-data-dir=/tmp/cap-9561 about:blank")
_PESTANA = (102, 101, "1-03:12:00", _UID, _HELPER)
casos_chrome = [
    ("huérfano de 27 h con perfil en /tmp → candidato", [_HUERFANO, _PESTANA], [101]),
    ("perfil en /var/folders (TMPDIR) → candidato",
     [(103, 1, "02:00:00", _UID, _CH + " --headless=new --user-data-dir=/var/folders/_l/x/T/cap-ab "
       "about:blank")], [103]),
    ("padre vivo → no se toca (alguien lo usa)",
     [(104, 555, "05:00:00", _UID, _CH + " --headless=new --user-data-dir=/tmp/cap-1")], []),
    ("el Chrome de {{TITULAR}} (sin --headless) → nunca",
     [(105, 1, "3-00:00:00", _UID, _CH + " --user-data-dir=/tmp/raro")], []),
    ("perfil fuera de /tmp → no se toca",
     [(106, 1, "05:00:00", _UID, _CH + " --headless=new --user-data-dir=/Users/x/perfil")], []),
    ("menos de 1 h → aún no", [(107, 1, "40:00", _UID, _CH + " --headless=new "
                                                         "--user-data-dir=/tmp/cap-2")], []),
    ("de otro usuario → nunca", [(108, 1, "05:00:00", _UID + 1, _CH + " --headless=new "
                                                                    "--user-data-dir=/tmp/cap-3")], []),
    ("una pestaña (Helper) no es el proceso principal", [(109, 1, "05:00:00", _UID, _HELPER)], []),
]
for _nombre, _filas, _esperado in casos_chrome:
    _c, _ = bc.detectar_chrome(ps_runner=_ps(*_filas))
    check("chrome: %s" % _nombre, [x["pid"] for x in (_c or [])] == _esperado)

_c, _ = bc.detectar_chrome(ps_runner=_ps(_HUERFANO, _PESTANA))
check("chrome: las pestañas van con el huérfano", _c and _c[0]["hijos"] == [102])
_c, _ = bc.detectar_chrome(ps_runner=lambda: (_ for _ in ()).throw(RuntimeError("ps roto")))
check("chrome: ps roto → None (no se mata nada)", _c is None)

# Parada real: un `sleep` hace de Chrome y otro de su pestaña.
_falso = subprocess.Popen(["sleep", "100"])
_pestana = subprocess.Popen(["sleep", "100"])
_esperar_visible(_falso.pid)
_esperar_visible(_pestana.pid)
try:
    _cand = {"pid": _falso.pid, "hijos": [_pestana.pid], "etime": "1-00:00:00", "etime_seg": 86400,
             "cmd": _CH + " --headless=new --user-data-dir=/tmp/cap-test"}
    _r = bc._parar_chrome(_cand, dry_run=True)
    check("chrome: dry-run no mata", _falso.poll() is None and _pestana.poll() is None
          and _r["parado"] is False)
    _r = bc._parar_chrome(_cand, wait=2.0)
    _falso.wait(timeout=5)
    _pestana.wait(timeout=5)
    check("chrome: para el proceso y su pestaña", _r["parado"] and _falso.poll() is not None
          and _pestana.poll() is not None)
    check("chrome: el registro dice el perfil", _r.get("perfil") == "/tmp/cap-test")
finally:
    for _p in (_falso, _pestana):
        if _p.poll() is None:
            _p.kill()

# run_chrome con ps inyectado y dry-run: avisa sin tocar.
_al, _inf = bc.run_chrome(dry_run=True, ps_runner=_ps(_HUERFANO, _PESTANA))
check("chrome: run_chrome dry-run avisa", _inf.get("candidatos") == 1 and _al
      and _al[0][0] == "chrome_huerfano_detectado")
_al, _inf = bc.run_chrome(dry_run=True, ps_runner=_ps(_PESTANA))
check("chrome: sin huérfanos no hay aviso", _al == [] and _inf.get("candidatos") == 0)

print("test_bucles_colgados: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
