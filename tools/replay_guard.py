#!/usr/bin/env python3
"""replay_guard.py — reproduce los comandos Bash REALES contra dos versiones de un hook.

POR QUÉ EXISTE (20-sep-2026). Tocar `clinico_guard.py` / `muro_guard.py` a ciegas sale caro:
el 19-sep el arreglo del bypass por substring pasó todos los tests y aun así rompía 16
comandos de trabajo diario, y hubo que revertirlo ya fusionado (6be09d9). Lo único que lo
cazó fue reproducir el historial real de comandos contra el guard viejo y el nuevo. Ese
script vivió en el scratchpad de la sesión y hubo que reescribirlo entero al día siguiente,
así que ahora vive aquí. Es el «rodaje» barato que la norma `feedback-muro-soak-antes-de-
fusionar` pide ANTES de fusionar, y no sustituye a las 24 h en rama: las complementa.

QUÉ HACE
  1. extrae los `tool_use` de Bash de ~/.claude/projects/*/*.jsonl (deduplicados);
  2. pasa cada comando como payload PreToolUse a los DOS hooks, con `CLAUDE_PROJECT_DIR`
     en un directorio temporal (para no ensuciar el log de auditoría real) y `BTP_REPO`
     apuntando a casa base (para que las raíces clínicas sean las de verdad);
  3. imprime y guarda las diferencias, separando las dos direcciones, que no son iguales:
     ALLOW→DENY es un falso positivo posible, DENY→ALLOW es un AGUJERO.

USO
  python3 tools/replay_guard.py <hook_viejo.py> <hook_nuevo.py> [--salida x.json] [--limite N]
                                [--escrituras] [--solo-lazo]

Para `muro_guard` (fail-closed, y juzga escrituras) lo suyo es `--escrituras --solo-lazo`: si un
falso positivo nuevo deja mudo el lazo 24/7, no da error visible, solo silencio.

El hook importa `zonas_clinicas` desde SU directorio, así que cada versión debe estar en una
carpeta con su copia (o un enlace) de `zonas_clinicas.py` y de `zonas_clinicas.local.json`.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

TRANSCRIPTS = os.path.expanduser("~/.claude/projects/*/*.jsonl")
CASA_BASE = os.path.expanduser("~/claudecode")


# `muro_guard` juzga también lo que se ESCRIBE, no solo lo que se lee: un replay que solo mire
# Bash no vale para él. Viene del replay del 11-sep-26 que vivía suelto en una nota archivada.
TOOLS_ESCRITURA = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def comandos_reales(patron=TRANSCRIPTS, tools=("Bash",), solo_lazo=False):
    """Las llamadas únicas del historial, de la más reciente a la más vieja.

    `tools` elige qué herramientas se replican (Bash por defecto; añade TOOLS_ESCRITURA para
    `muro_guard`). `solo_lazo` se queda con las del lazo 24/7 (`entrypoint: sdk-cli`), que es lo
    que importa cuando el hook que cambias es fail-closed y puede dejarlo mudo horas.
    """
    vistos = {}
    for fp in sorted(glob.glob(patron)):
        try:
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for linea in fh:
                    # filtro barato: 870 MB no caben en un json.loads
                    if not any(('"%s"' % t) in linea for t in tools):
                        continue
                    try:
                        d = json.loads(linea)
                    except Exception:
                        continue
                    if solo_lazo and d.get("entrypoint") != "sdk-cli":
                        continue
                    cont = (d.get("message") or {}).get("content")
                    if not isinstance(cont, list):
                        continue
                    ts = d.get("timestamp") or ""
                    for b in cont:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        if b.get("name") not in tools:
                            continue
                        ent = b.get("input") or {}
                        cmd = ent.get("command") if b["name"] == "Bash" else ent.get("file_path")
                        if not isinstance(cmd, str) or not cmd.strip():
                            continue
                        # cwd y modo cuentan (22-sep-26): `salida_guard` decide por el repo
                        # donde corre la orden y pregunta o no según el modo de permisos.
                        cmd = (b["name"], cmd, d.get("cwd") or "", d.get("permissionMode") or "")
                        e = vistos.get(cmd)
                        if e is None:
                            vistos[cmd] = [1, ts]
                        else:
                            e[0] += 1
                            e[1] = max(e[1], ts)
        except Exception as ex:                                   # noqa: BLE001
            print("!! %s: %r" % (fp, ex), file=sys.stderr)
    filas = [{"tool": k[0], "cmd": k[1], "cwd": k[2], "modo": k[3], "n": v[0], "ts": v[1]}
             for k, v in vistos.items()]
    filas.sort(key=lambda r: r["ts"], reverse=True)
    return filas


def _entorno(logdir):
    e = dict(os.environ)
    e["CLAUDE_PROJECT_DIR"] = logdir
    e["BTP_REPO"] = CASA_BASE
    # Estado aislado (25-sep-26). Sin esto `salida_guard` escribía cada veredicto del replay en el
    # `salida_guard.jsonl` REAL (977 «denegados» falsos en 3,5 días frente a 17 reales: el log dejó
    # de valer como medida) y leía el `ok_envio.json` real, así que un replay podía GASTAR el
    # permiso de un solo uso que {{TITULAR}} acababa de abrir. Medido al planear P3 (firmas por niveles).
    e["BTP_STATE_DIR"] = os.path.join(logdir, "state")
    os.makedirs(e["BTP_STATE_DIR"], exist_ok=True)
    e.pop("MURO_ALLOW_CLINICAL", None)     # el bypass falsearía todo el replay
    return e


# Señales de que el intérprete no llegó a EJECUTAR el hook. Sin esto, un hook que falta se lee
# como un DENY —Python sale con rc≠0 igual que el hook al denegar— y el replay entero se llena
# de «relajaciones» inventadas. Pasó el 20-sep-26: el scratchpad con la copia del hook viejo se
# borró al reiniciar la sesión y el rodaje se puso ROJO con 5 falsos hallazgos. Un vigía que
# convierte su propia rotura en alarma es tan inútil como el que se calla; y al revés —si el
# que falta es el hook NUEVO— diría «deniega todo» y nadie sospecharía nada.
_NO_ARRANCÓ = ("can't open file", "No such file or directory", "SyntaxError",
               "ModuleNotFoundError: No module named 'json'", "IndentationError")


class HookRoto(RuntimeError):
    pass


def _decision_json(stdout):
    """`permissionDecision` del JSON que imprime el hook, o "". Los hooks nuevos (salida_guard)
    deniegan así, con rc=0; leer solo el rc los daba por ALLOW siempre (22-sep-26: 0 deny sobre
    21.518 comandos, un verde falso)."""
    for linea in reversed((stdout or "").strip().splitlines()):
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        if isinstance(d, dict):
            return str((d.get("hookSpecificOutput") or {}).get("permissionDecision") or "")
    return ""


def juzga(hook, env, cmd, tool="Bash", cwd="", modo=""):
    """"DENY" / "ASK" / "ALLOW". Vale para los dos estilos de hook: rc≠0 (muro_guard,
    clinico_guard) y JSON `permissionDecision` con rc=0 (salida_guard)."""
    campo = "command" if tool == "Bash" else "file_path"
    datos = {"tool_name": tool, "tool_input": {campo: cmd}}
    if cwd:
        datos["cwd"] = cwd
    if modo:
        datos["permission_mode"] = modo
    payload = json.dumps(datos)
    try:
        p = subprocess.run(["python3", hook], input=payload, capture_output=True,
                           text=True, timeout=30, env=env)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    err = (p.stderr or "").strip()
    if p.returncode and any(m in err for m in _NO_ARRANCÓ):
        raise HookRoto("%s no se pudo ejecutar: %s" % (hook, err.splitlines()[-1][:200]))
    if p.returncode:
        return "DENY", err[:300]
    dec = _decision_json(p.stdout)
    return {"deny": "DENY", "ask": "ASK"}.get(dec, "ALLOW"), err[:300]


def comprueba(hook):
    """Que el hook existe y RESPONDE antes de juzgar 15.000 comandos con él."""
    if not os.path.exists(hook):
        raise HookRoto("no existe: %s" % hook)
    zc = os.path.join(os.path.dirname(os.path.abspath(hook)), "zonas_clinicas.py")
    with open(hook, encoding="utf-8", errors="replace") as fh:
        usa_zonas = "zonas_clinicas" in fh.read()
    # Solo si el hook la usa (25-sep): salida_guard no la importa y el aviso salía en cada
    # revisión del rodaje. Un aviso del muro que siempre sale enseña a no leerlo.
    if usa_zonas and not os.path.exists(zc):
        print("⚠️  %s no tiene zonas_clinicas.py al lado: el hook caerá a su lista de respaldo "
              "y protegerá MENOS que en su sitio." % os.path.basename(os.path.dirname(hook)),
              file=sys.stderr)
    env = _entorno(tempfile.mkdtemp(prefix="replay-check-"))
    juzga(hook, env, "git status --short")      # inofensivo: si esto revienta, el hook no sirve
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("viejo")
    ap.add_argument("nuevo")
    ap.add_argument("--salida", default="")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--escrituras", action="store_true",
                    help="juzga también Write/Edit/MultiEdit (para muro_guard)")
    ap.add_argument("--solo-lazo", action="store_true",
                    help="solo las llamadas del lazo 24/7 (entrypoint sdk-cli)")
    a = ap.parse_args()

    for h in (a.viejo, a.nuevo):
        try:
            comprueba(h)
        except HookRoto as ex:
            print("⛔ %s" % ex, file=sys.stderr)
            print("   Sin los dos hooks en pie no hay comparación posible: el que falta saldría "
                  "como «deniega todo» y llenaría el informe de hallazgos falsos.", file=sys.stderr)
            return 2
    filas = comandos_reales(tools=("Bash",) + (TOOLS_ESCRITURA if a.escrituras else ()),
                            solo_lazo=a.solo_lazo)
    if a.limite:
        filas = filas[:a.limite]
    logdir = tempfile.mkdtemp(prefix="replay-guard-")
    env = _entorno(logdir)

    def una(f):
        t = f.get("tool", "Bash")
        va, _ = juzga(a.viejo, env, f["cmd"], t, f.get("cwd", ""), f.get("modo", ""))
        vb, eb = juzga(a.nuevo, env, f["cmd"], t, f.get("cwd", ""), f.get("modo", ""))
        return dict(f, viejo=va, nuevo=vb, stderr=eb)

    with ThreadPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(una, filas))

    difs = [r for r in res if r["viejo"] != r["nuevo"]]
    agujeros = [r for r in difs if r["viejo"] == "DENY"]
    resumen = {"comandos": len(res),
               "deniega_viejo": sum(1 for r in res if r["viejo"] == "DENY"),
               "deniega_nuevo": sum(1 for r in res if r["nuevo"] == "DENY"),
               "diferencias": len(difs),
               "pregunta_viejo": sum(1 for r in res if r["viejo"] == "ASK"),
               "pregunta_nuevo": sum(1 for r in res if r["nuevo"] == "ASK"),
               "endurece (→DENY)": sum(1 for r in difs if r["nuevo"] == "DENY"),
               "otros cambios (ALLOW↔ASK)": sum(1 for r in difs
                                                if "DENY" not in (r["viejo"], r["nuevo"])),
               "RELAJA (DENY→ALLOW)": len(agujeros)}
    print(json.dumps(resumen, indent=1, ensure_ascii=False))
    if agujeros:
        print("\n⛔ el hook nuevo DEJA PASAR lo que el viejo denegaba:")
        for r in agujeros[:20]:
            print("   · " + r["cmd"].replace("\n", " ⏎ ")[:160])
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as fh:
            json.dump({"resumen": resumen, "diferencias": difs}, fh,
                      ensure_ascii=False, indent=1)
        print("\ndetalle: %s" % a.salida)
    return 1 if agujeros else 0


if __name__ == "__main__":
    raise SystemExit(main())
