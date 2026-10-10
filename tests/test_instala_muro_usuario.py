#!/usr/bin/env python3
"""test_instala_muro_usuario.py — el muro mínimo llega a las carpetas de fuera de claudecode.

10-oct-2026, paso 7 del plan «Claude a punto para Polaris». Fija que `tools/instala_muro_usuario.py`:
  · copia los cuatro guards con la cadena del comando IDÉNTICA a la del proyecto (si difiere en un
    espacio, Claude Code los ejecuta dos veces dentro de claudecode);
  · instala en un directorio de usuario vacío, conserva las claves que ya hubiera y es idempotente;
  · deja un lanzador ejecutable por cada hook del árbol;
  · con el comando instalado y el proyecto en una carpeta ajena, `clinico_guard` deniega (rc 2);
  · en ESTE Mac, los ajustes reales de usuario no derivan de casa base (se salta si aún no se instaló).
"""
import json
import os
import subprocess
import sys
import tempfile

ARBOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ARBOL, "tools"))
import instala_muro_usuario as imu  # noqa: E402

_pass = _fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ " + nombre)


def main():
    proyecto = json.load(open(os.path.join(ARBOL, ".claude", "settings.json"), encoding="utf-8"))
    b = imu.bloque(proyecto)
    cmds_b = [h["command"] for g in b.values() for m in g for h in m["hooks"]]
    cmds_p = {h["command"]: (m.get("matcher"), h.get("timeout"))
              for ev in imu.EVENTOS for m in proyecto["hooks"].get(ev, []) for h in m["hooks"]}
    check("el bloque lleva los cuatro guards y nada más",
          sorted(os.path.basename(c) for c in cmds_b) == sorted(imu.GUARDS))
    check("cada comando es byte a byte el del proyecto", all(c in cmds_p for c in cmds_b))
    check("matcher y timeout copiados tal cual", all(
        (m.get("matcher"), h.get("timeout")) == cmds_p[h["command"]]
        for g in b.values() for m in g for h in m["hooks"]))

    with tempfile.TemporaryDirectory() as tmp:
        usuario = os.path.join(tmp, "dot-claude")
        os.makedirs(usuario)
        json.dump({"model": "x", "permissions": {"deny": ["Read(~/a/**)"]}},
                  open(os.path.join(usuario, "settings.json"), "w"))
        check("antes de instalar hay deriva", bool(imu.deriva(ARBOL, usuario)))
        imu.aplicar(ARBOL, usuario)
        check("tras instalar no hay deriva", imu.deriva(ARBOL, usuario) == [])
        s = json.load(open(os.path.join(usuario, "settings.json")))
        check("conserva las claves que ya había",
              s.get("model") == "x" and s["permissions"]["deny"] == ["Read(~/a/**)"])
        check("deja copia previa", bool(os.listdir(os.path.join(usuario, "backups"))))
        imu.aplicar(ARBOL, usuario)
        check("idempotente", imu.deriva(ARBOL, usuario) == [])
        hooks = imu.hooks_de_casa_base(ARBOL)
        check("un lanzador ejecutable por hook (%d)" % len(hooks), all(
            os.access(os.path.join(usuario, "hooks", f), os.X_OK) for f in hooks))
        os.remove(os.path.join(usuario, "hooks", "clinico_guard.py"))
        check("un lanzador borrado se nota", any("clinico_guard" in t for t in imu.deriva(ARBOL, usuario)))

        # El efecto: el comando instalado, lanzado como lo lanza Claude Code desde una carpeta ajena
        ajeno = os.path.join(tmp, "otro-proyecto")
        os.makedirs(ajeno)
        cmd = [h["command"] for m in s["hooks"]["PreToolUse"] for h in m["hooks"]
               if h["command"].endswith("clinico_guard.py")][0]
        casa = os.path.join(tmp, "home")
        os.makedirs(casa)
        os.symlink(ARBOL, os.path.join(casa, "claudecode"))
        env = dict(os.environ, CLAUDE_PROJECT_DIR=ajeno, HOME=casa,
                   BTP_STATE_DIR=os.path.join(tmp, "estado"))
        env.pop("MURO_ALLOW_CLINICAL", None)

        def lanza(orden):
            return subprocess.run(["sh", "-c", cmd], input=json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": orden}, "cwd": ajeno,
                 "session_id": "test-muro-usuario"}), capture_output=True, text=True, timeout=60,
                env=env, cwd=ajeno)
        p = lanza("ls ~/Clinico-PRIVADO/")
        check("desde una carpeta ajena, leer zona clínica → deny (rc %d)" % p.returncode,
              p.returncode == 2 and "MURO" in p.stderr)
        check("desde una carpeta ajena, una orden inocua pasa", lanza("ls").returncode == 0)

    real = imu.dir_usuario()
    try:
        instalado = bool(json.load(open(os.path.join(real, "settings.json"))).get("hooks"))
    except (OSError, ValueError):
        instalado = False
    if instalado and os.path.isdir(os.path.join(imu.casa_base(), ".claude", "hooks")):
        falta = imu.deriva(imu.casa_base(), real)
        check("los ajustes reales de usuario están al día con casa base (%s) — arreglo: "
              "python3 tools/instala_muro_usuario.py --apply" % (falta or "ok"), not falta)
    else:
        print("  (muro de usuario sin instalar en este equipo: no comparo los ajustes reales)")

    print("test_instala_muro_usuario: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
