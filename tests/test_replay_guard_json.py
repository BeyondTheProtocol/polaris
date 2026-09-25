#!/usr/bin/env python3
"""test_replay_guard_json.py — el replay de hooks ve los deny por JSON y decide con el cwd real.

POR QUÉ (22-sep-2026, deuda `replay-guard-ciego-a-json`). `replay_guard.py` juzgaba DENY solo por
rc≠0 y mandaba el payload sin `cwd`. `salida_guard.py` deniega con JSON `permissionDecision` y
rc=0, y decide el `git push` por el repo donde corre: el replay dio 0 deny sobre 21.518 comandos
reales, un verde falso justo sobre un hook del muro. Aquí se fija con hooks de juguete de los dos
estilos (rc y JSON) y con transcripts sintéticos: no depende del historial real.
"""
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import replay_guard as RG      # noqa: E402

fallos = 0


def ok(cond, desc, detalle=""):
    global fallos
    if not cond:
        fallos += 1
    print("  %s %s%s" % ("✅" if cond else "❌ MAL", desc, ("  — " + detalle) if not cond else ""))


tmp = tempfile.mkdtemp(prefix="replay-json-")


def hook(nombre, cuerpo):
    p = os.path.join(tmp, nombre)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("import json, sys\nd = json.load(sys.stdin)\n" + cuerpo)
    return p


# Deniega por JSON (rc=0) solo si el push corre DENTRO de /casa; pregunta en modo default.
json_hook = hook("salida_like.py", (
    "cmd = d['tool_input'].get('command', '')\n"
    "cwd = d.get('cwd', '')\n"
    "if 'git push' in cmd and cwd.startswith('/casa'):\n"
    "    print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}}))\n"
    "elif 'clic' in cmd and d.get('permission_mode') == 'default':\n"
    "    print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'ask'}}))\n"))
rc_hook = hook("muro_like.py", "sys.exit(2 if 'rm -rf' in d['tool_input'].get('command', '') else 0)\n")
env = RG._entorno(tempfile.mkdtemp(prefix="replay-json-log-"))

print("── juzga: los dos estilos de hook ──")
ok(RG.juzga(json_hook, env, "git push", cwd="/casa/wt")[0] == "DENY",
   "deny por JSON con rc=0 se lee como DENY")
ok(RG.juzga(json_hook, env, "git push", cwd="/otro")[0] == "ALLOW",
   "el mismo comando en otro repo es ALLOW (decide el cwd)")
ok(RG.juzga(json_hook, env, "clic", modo="default")[0] == "ASK", "ask por JSON se lee como ASK")
ok(RG.juzga(rc_hook, env, "rm -rf x")[0] == "DENY", "el estilo rc≠0 sigue siendo DENY")
ok(RG.juzga(rc_hook, env, "ls")[0] == "ALLOW", "rc=0 sin JSON sigue siendo ALLOW")

print("── comandos_reales: cwd y modo del transcript ──")
tr = os.path.join(tmp, "proj")
os.makedirs(tr)
with open(os.path.join(tr, "s.jsonl"), "w", encoding="utf-8") as fh:
    for cwd in ("/casa/wt", "/otro"):
        fh.write(json.dumps({"cwd": cwd, "permissionMode": "bypassPermissions",
                             "timestamp": "2026-09-22T10:00:00Z",
                             "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                      "input": {"command": "git push"}}]}}) + "\n")
filas = RG.comandos_reales(patron=os.path.join(tmp, "*", "*.jsonl"))
ok(sorted(f.get("cwd", "") for f in filas) == ["/casa/wt", "/otro"],
   "el mismo comando en dos cwd son DOS filas, cada una con su cwd", repr(filas))
ok(all(f.get("modo") == "bypassPermissions" for f in filas), "lleva el modo de permisos")
veredictos = sorted(RG.juzga(json_hook, env, f["cmd"], f["tool"], f.get("cwd", ""), f.get("modo", ""))[0]
                    for f in filas)
ok(veredictos == ["ALLOW", "DENY"], "replay de punta a punta: un DENY y un ALLOW", repr(veredictos))

# El replay no escribe en el estado VIVO (25-sep-26). Antes `salida_guard` volcaba cada veredicto
# del replay al `salida_guard.jsonl` real y leía el `ok_envio.json` real (podía gastar el permiso de
# un solo uso de {{TITULAR}}). Primero se mira el entorno: si no está aislado, NO se lanza el hook de
# verdad, para que el propio test no ensucie el log que protege.
import _casa                    # noqa: E402
logdir = tempfile.mkdtemp(prefix="replay-state-")
env_r = RG._entorno(logdir)
aislado = env_r.get("BTP_STATE_DIR", "").startswith(logdir + os.sep)
ok(aislado, "el replay corre con BTP_STATE_DIR dentro de su carpeta temporal",
   repr(env_r.get("BTP_STATE_DIR")))
if aislado:
    vivo = os.path.join(_casa.state_dir(), "salida_guard.jsonl")
    antes = os.path.getsize(vivo) if os.path.exists(vivo) else -1
    real = os.path.join(RAIZ, ".claude", "hooks", "salida_guard.py")
    v = RG.juzga(real, env_r, "gh pr merge 1 --repo prueba/replay", "Bash", "/tmp", "default")[0]
    ok(v == "DENY", "el salida_guard real deniega el merge en el replay", v)
    despues = os.path.getsize(vivo) if os.path.exists(vivo) else -1
    ok(antes == despues, "el log vivo de salida_guard no crece con el replay", "%s → %s" % (antes, despues))
    ok(os.path.exists(os.path.join(env_r["BTP_STATE_DIR"], "salida_guard.jsonl")),
       "el veredicto queda en el log aislado")

print("\ntest_replay_guard_json: %d fallos" % fallos)
sys.exit(1 if fallos else 0)
