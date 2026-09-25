#!/usr/bin/env python3
"""test_guard_timeout.py — un guard que BLOQUEA no puede dejar pasar por lento.

POR QUÉ EXISTE (25-sep-2026). Idea de {{CONTACTO}} (https://contacto), con su agente KAI,
revisión del 25-sep-2026. La doc oficial (https://code.claude.com/docs/en/hooks.md, «Timeouts»):
un hook que agota su `timeout` no bloquea, la llamada sigue por el flujo normal de permisos. Con
el Mac cargado, un guard lento era un guard que dice que sí. Deuda `guard-timeout-no-deniega`.

Qué fija:
  1. `_watchdog.armar`: un guard lento se DENIEGA (exit 2) antes de su timeout, también si está
     atascado en C sin soltar el GIL; uno rápido pasa intacto (código y stdout); el acortador de
     tests solo acorta.
  2. `muro_guard.sh`: su reloj de bash deniega un guard lento y no le roba el stdin al rápido.
  3. Cada hook PreToolUse/Stop de cada `.claude/settings*.json` que puede bloquear: timeout
     explícito en settings, `_watchdog.armar(N)` con N igual a ese timeout, y el hook REAL
     deniega cuando el plazo vence (se simula con BTP_WATCHDOG_S).
  4. `gate_salida` (Stop): sin tiempo y con citas, frena una vez; sin citas, avisa y deja pasar.
  5. Las reglas `permissions.deny` de respaldo están, no casan con rutas de worktree, no usan
     reglas de ruta que Claude Code ignora (Write/Glob/Grep…) y no llevan nombres del overlay.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, ".claude", "hooks")
PY = sys.executable

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def corre(cmd, entrada="{}", env_extra=None, timeout=30):
    env = dict(os.environ)
    env.pop("BTP_WATCHDOG_S", None)
    env.pop("MURO_WATCHDOG_S", None)
    env.update(env_extra or {})
    t0 = time.monotonic()
    p = subprocess.run(cmd, input=entrada, capture_output=True, text=True, env=env,
                       timeout=timeout, cwd=ROOT)
    return p.returncode, p.stdout, p.stderr, time.monotonic() - t0


# ── 1. el módulo ─────────────────────────────────────────────────────────────────────
def guard_falso(tmp, cuerpo, timeout_hook=3):
    ruta = os.path.join(tmp, "falso_%d.py" % abs(hash(cuerpo)))
    with open(ruta, "w") as f:
        f.write("import sys, time, re, json\nsys.path.insert(0, %r)\nimport _watchdog\n"
                "_watchdog.armar(%d, 'falso')\n%s\n" % (HOOKS, timeout_hook, cuerpo))
    return ruta


def prueba_modulo(tmp):
    print("── _watchdog.armar")
    lento = guard_falso(tmp, "time.sleep(30)\nsys.exit(0)")
    rc, _o, err, dt = corre([PY, lento], env_extra={"BTP_WATCHDOG_S": "0.5"})
    check(rc == 2 and "watchdog" in err and dt < 5, "guard dormido → deny en %.1f s" % dt)

    # Backtracking catastrófico: el motor de regex no suelta el GIL; un hilo o SIGALRM no
    # podrían cortar aquí. El proceso padre sí.
    atascado = guard_falso(tmp, "re.match(r'(a+)+$', 'a' * 40 + 'b')\nsys.exit(0)")
    rc, _o, err, dt = corre([PY, atascado], env_extra={"BTP_WATCHDOG_S": "0.5"})
    check(rc == 2 and dt < 5, "guard atascado en C (regex) → deny en %.1f s" % dt)

    permite = guard_falso(tmp, "d = json.loads(sys.stdin.read())\n"
                               "print(json.dumps({'visto': d['x']}))\nsys.exit(0)")
    rc, out, _e, _dt = corre([PY, permite], entrada='{"x": 7}')
    check(rc == 0 and json.loads(out or "{}").get("visto") == 7,
          "guard rápido: exit 0, stdin y stdout intactos")

    niega = guard_falso(tmp, "sys.stderr.write('no'); sys.exit(2)")
    rc, _o, err, _dt = corre([PY, niega])
    check(rc == 2 and err.strip() == "no", "guard rápido que niega: exit 2 y su stderr")

    uno = guard_falso(tmp, "sys.exit(1)")
    check(corre([PY, uno])[0] == 1, "exit 1 del guard se devuelve tal cual")

    # El acortador no puede alargar: armar(3) → presupuesto 1 s aunque pidan 99.
    rc, _o, _e, dt = corre([PY, lento], env_extra={"BTP_WATCHDOG_S": "99"})
    check(rc == 2 and dt < 4, "BTP_WATCHDOG_S=99 no alarga el plazo (%.1f s)" % dt)


# ── 2. el envoltorio de bash del muro ────────────────────────────────────────────────
def prueba_sh(tmp):
    print("── muro_guard.sh")
    d = os.path.join(tmp, "sh")
    os.makedirs(d)
    sh = os.path.join(d, "muro_guard.sh")
    shutil.copy(os.path.join(HOOKS, "muro_guard.sh"), sh)
    falso = os.path.join(d, "muro_guard.py")

    open(falso, "w").write("import time\ntime.sleep(60)\n")
    rc, _o, err, dt = corre(["bash", sh], env_extra={"MURO_WATCHDOG_S": "1"})
    check(rc == 2 and "watchdog" in err and dt < 5, "guard lento → deny en %.1f s" % dt)

    open(falso, "w").write("import sys, json\nd = json.loads(sys.stdin.read())\n"
                           "print('eco', d['x'])\nsys.exit(0 if d['x'] == 'ok' else 3)\n")
    rc, out, _e, _dt = corre(["bash", sh], entrada='{"x": "ok"}')
    check(rc == 0 and "eco ok" in out, "guard rápido: recibe el stdin y su exit 0 permite")
    rc, _o, _e, _dt = corre(["bash", sh], entrada='{"x": "no"}')
    check(rc == 2, "cualquier exit distinto de 0 sigue siendo deny (fail-closed)")

    txt = open(os.path.join(HOOKS, "muro_guard.sh")).read()
    m = re.search(r"^LIMITE=(\d+)$", txt, re.M)
    return int(m.group(1)) if m else None


# ── 3. los hooks reales ──────────────────────────────────────────────────────────────
BLOQUEA = re.compile(r"sys\.exit\(2\)|return 2\b|\"deny\"")


def hooks_registrados():
    """{fichero .py del guard: [(settings, evento, timeout)]} de PreToolUse y Stop."""
    out = {}
    for f in sorted(glob.glob(os.path.join(ROOT, ".claude", "settings*.json"))):
        s = json.load(open(f, encoding="utf-8"))
        for evento in ("PreToolUse", "Stop"):
            for m in (s.get("hooks") or {}).get(evento) or []:
                for h in m.get("hooks") or []:
                    cmd = h.get("command") or ""
                    nombre = os.path.basename(cmd.split()[-1])
                    out.setdefault(nombre, []).append((os.path.basename(f), evento,
                                                       h.get("timeout")))
    return out


# Un hook real con una llamada trivial acaba en menos de un milisegundo, así que acortar el plazo
# no basta para verlo vencer: hay que hacerlo LENTO, que es el caso real (Mac cargado). Este
# sitecustomize, solo en el PYTHONPATH del test, duerme al hijo justo después del fork: es decir,
# con el watchdog ya armado y el guard sin haber empezado. No toca el código de producción.
SITE = """import os, time
_fork = os.fork
def _fork_lento():
    pid = _fork()
    if pid == 0:
        time.sleep(float(os.environ.get("BTP_TEST_HIJO_LENTO") or 0))
    return pid
os.fork = _fork_lento
"""


def env_lento(tmp):
    d = os.path.join(tmp, "site")
    if not os.path.isdir(d):
        os.makedirs(d)
        open(os.path.join(d, "sitecustomize.py"), "w").write(SITE)
    return {"PYTHONPATH": d, "BTP_TEST_HIJO_LENTO": "20", "BTP_WATCHDOG_S": "1"}


PAYLOAD_PRE = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"},
                          "cwd": ROOT, "session_id": "test-watchdog"})


def prueba_hooks(limite_sh, tmp):
    print("── hooks registrados")
    reg = hooks_registrados()
    vistos = 0
    for nombre, usos in sorted(reg.items()):
        py = nombre[:-3] + ".py" if nombre.endswith(".sh") else nombre
        ruta = os.path.join(HOOKS, py)
        if not os.path.isfile(ruta):
            continue
        fuente = open(ruta, encoding="utf-8").read()
        if not BLOQUEA.search(fuente):
            continue
        vistos += 1
        tiempos = [t for _s, _e, t in usos]
        check(all(isinstance(t, (int, float)) for t in tiempos),
              "%s: timeout explícito en todos sus settings %s" % (nombre, [s for s, _e, _t in usos]))
        m = re.search(r"_watchdog\.armar\((\d+)", fuente)
        t_min = min([t for t in tiempos if isinstance(t, (int, float))] or [0])
        check(bool(m) and int(m.group(1)) == t_min,
              "%s: _watchdog.armar(%s) = timeout de settings (%s)"
              % (py, m.group(1) if m else "—", t_min))
        if nombre.endswith(".sh"):
            check(limite_sh is not None and limite_sh < t_min,
                  "%s: reloj de bash (%s s) por debajo del timeout (%s s)"
                  % (nombre, limite_sh, t_min))
        evento = usos[0][1]
        if evento == "PreToolUse":
            cmd = ["bash", os.path.join(HOOKS, nombre)] if nombre.endswith(".sh") else [PY, ruta]
            rc, _o, err, dt = corre(cmd, entrada=PAYLOAD_PRE, env_extra=env_lento(tmp))
            check(rc == 2 and "watchdog" in err and dt < 8,
                  "%s REAL y lento → deny en %.1f s" % (nombre, dt))
            rc, _o, _e, _dt = corre(cmd, entrada=PAYLOAD_PRE)
            check(rc == 0, "%s REAL y rápido → sigue permitiendo una llamada inocua" % nombre)
    check(vistos >= 10, "hay guards que bloquean y se han revisado (%d)" % vistos)


def prueba_gate(tmp):
    print("── gate_salida (Stop)")
    gate = os.path.join(HOOKS, "gate_salida.py")
    con = json.dumps({"last_assistant_message": "Lo apoya PMID: 12345678, sin más.",
                      "stop_hook_active": False})
    rc, _o, err, _dt = corre([PY, gate], entrada=con, env_extra=env_lento(tmp))
    check(rc == 2 and "no me dio tiempo" in err, "sin tiempo y con citas → frena (una vez)")
    sin = json.dumps({"last_assistant_message": "Hecho, suite en verde.",
                      "stop_hook_active": False})
    rc, out, _e, _dt = corre([PY, gate], entrada=sin, env_extra=env_lento(tmp))
    check(rc == 0 and "SIN revisar" in out, "sin tiempo y sin citas → deja pasar, pero avisa")
    ya = json.dumps({"last_assistant_message": "PMID: 12345678", "stop_hook_active": True})
    rc, _o, _e, _dt = corre([PY, gate], entrada=ya, env_extra=env_lento(tmp))
    check(rc == 0, "con stop_hook_active no hay bucle")


# ── 5. las reglas deny de respaldo ───────────────────────────────────────────────────
DENY_OBLIGADAS = {
    "Edit(~/claudecode/.claude/hooks/**)",
    "Edit(~/claudecode/.claude/settings*.json)",
    "Read(//**/_PRIVADO_CLINICO/**)",
    "Read(//**/_PRIVADO_EXPEDIENTE/**)",
    "Read(//**/_PRIVADO_NUCLEO/**)",
    "Read(//**/_PRIVADO_CORREO/**)",
    "Read(~/Clinico-PRIVADO/**)",
}
# Claude Code acepta estas reglas de ruta pero NUNCA las consulta (docs/en/permissions.md).
IGNORADAS = re.compile(r"^(Write|MultiEdit|NotebookEdit|Glob|Grep)\(")


def prueba_deny():
    print("── permissions.deny de respaldo")
    ruta = os.path.join(ROOT, ".claude", "settings.json")
    crudo = open(ruta, encoding="utf-8").read()
    deny = (json.loads(crudo).get("permissions") or {}).get("deny") or []
    faltan = sorted(DENY_OBLIGADAS - set(deny))
    check(not faltan, "settings.json trae las reglas obligadas (faltan: %s)" % (faltan or "—"))
    check(not [r for r in deny if IGNORADAS.match(r)], "ninguna regla de ruta que se ignore")
    # Las de código del muro: ancladas a casa base. Un worktree cuelga de .claude/worktrees/,
    # así que `~/claudecode/.claude/hooks/**` no puede casar con él.
    muro = [r for r in deny if r.startswith("Edit(") and (".claude/hooks" in r or "settings" in r)]
    check(all(r.startswith("Edit(~/claudecode/.claude/") for r in muro),
          "las reglas del código del muro van ancladas a casa base (worktrees libres)")
    check(not [r for r in deny if "worktrees" in r], "ninguna regla nombra worktrees")
    overlay = os.path.join(HOOKS, "zonas_clinicas.local.json")
    base = os.path.expanduser("~/claudecode/.claude/hooks/zonas_clinicas.local.json")
    for f in (overlay, base):
        if os.path.isfile(f):
            datos = json.load(open(f, encoding="utf-8"))
            nombres = [x for k, v in datos.items() if isinstance(v, list) for x in v]
            check(not [n for n in nombres if n and n.lower() in crudo.lower()],
                  "settings.json no lleva nombres del overlay local (%d revisados)" % len(nombres))
            break


def main():
    tmp = tempfile.mkdtemp(prefix="test-watchdog-")
    try:
        prueba_modulo(tmp)
        limite = prueba_sh(tmp)
        prueba_hooks(limite, tmp)
        prueba_gate(tmp)
        prueba_deny()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fallos:
        print("❌ %d fallo(s) en el watchdog de guards" % len(fallos))
        return 1
    print("✅ WATCHDOG DE GUARDS EN VERDE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
