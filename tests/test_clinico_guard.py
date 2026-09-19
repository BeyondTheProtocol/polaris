#!/usr/bin/env python3
"""Prueba el guard clínico INTERACTIVO (.claude/hooks/clinico_guard.py)."""
import json
import os
import subprocess

# El hook del ÁRBOL EN EL QUE CORRE este test, no el de casa base. Apuntaba a
# ~/claudecode fijo, así que desde un worktree probaba el guard viejo: todo lo que
# verificaras antes de fusionar era teatro. (Encontrado el 25-jul-26 auditando el muro.)
H = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 ".claude", "hooks", "clinico_guard.py")


def run(payload, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(["python3", H], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=20, env=e)
    return p.returncode != 0, (p.stderr or "").strip()


CASOS = [
    # (descripcion, payload, debe_denegar, env)
    ("Read del informe de biopsia",
     {"tool_name": "Read", "tool_input": {"file_path": "00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/biopsia.pdf"}}, True, None),
    ("Read del núcleo privado",
     {"tool_name": "Read", "tool_input": {"file_path": "00_FUENTE-DE-VERDAD/_PRIVADO_NUCLEO/notas.md"}}, True, None),
    ("Grep sobre el expediente legal",
     {"tool_name": "Grep", "tool_input": {"path": "00_FUENTE-DE-VERDAD/05 · Legal-Finanzas/_PRIVADO_EXPEDIENTE"}}, True, None),
    ("Bash: cat del informe clínico",
     {"tool_name": "Bash", "tool_input": {"command": "cat '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, True, None),
    ("Bash: cp del clínico a /tmp (exfiltración)",
     {"tool_name": "Bash", "tool_input": {"command": "cp ~/Clinico-PRIVADO/informe.pdf /tmp/x.pdf"}}, True, None),
    # Familia D (11-sep-26): el digest de un reel público se lee con Read; nada más de la carpeta.
    ("Read de un digest de reel (familia D)",
     {"tool_name": "Read", "tool_input": {"file_path": "00_FUENTE-DE-VERDAD/_PRIVADO_INSTAGRAM/reels/DaCGXkHNE3M.md"}}, False, None),
    ("Read de la raíz de Instagram (comentarios, PII de terceros)",
     {"tool_name": "Read", "tool_input": {"file_path": "00_FUENTE-DE-VERDAD/_PRIVADO_INSTAGRAM/digest.md"}}, True, None),
    ("Glob del directorio reels sin .md",
     {"tool_name": "Glob", "tool_input": {"pattern": "00_FUENTE-DE-VERDAD/_PRIVADO_INSTAGRAM/reels/*"}}, True, None),
    ("Bash: cat del digest (en Bash la familia D no existe)",
     {"tool_name": "Bash", "tool_input": {"command": "cat 00_FUENTE-DE-VERDAD/_PRIVADO_INSTAGRAM/reels/DaCGXkHNE3M.md"}}, True, None),
    ("Read de código normal del repo",
     {"tool_name": "Read", "tool_input": {"file_path": "tools/borde.py"}}, False, None),
    ("Bash: git status (normal)",
     {"tool_name": "Bash", "tool_input": {"command": "git status --short"}}, False, None),
    ("Bash: la VENTANILLA auditada (lector_clinico.py)",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, False, None),
    ("Read clínico CON MURO_ALLOW_CLINICAL=1 (comité clínico)",
     {"tool_name": "Read", "tool_input": {"file_path": "00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/biopsia.pdf"}}, False, {"MURO_ALLOW_CLINICAL": "1"}),
    # HEREDOCS (12-sep-26). El cuerpo es dato que entra por stdin, no una ruta que se lee: la
    # palabra suelta «informes» se resolvía contra el cwd y el guard se denegaba a sí mismo al
    # archivar una nota. El falso positivo empujaba a MURO_ALLOW_CLINICAL=1, que sí abre el muro.
    ("Bash: heredoc cuyo CUERPO menciona «informes» (dato, no lectura)",
     {"tool_name": "Bash", "tool_input": {"command": "cat > nota.md <<'EOF'\nRevisar los informes de la semana\nEOF"}}, False, None),
    ("Bash: heredoc que menciona una ruta clínica en el texto (mencionar no es leer)",
     {"tool_name": "Bash", "tool_input": {"command": "cat > x.md <<'EOF'\nver 00_FUENTE-DE-VERDAD/_PRIVADO_NUCLEO/notas.md\nEOF"}}, False, None),
    ("Bash: heredoc a un INTÉRPRETE que sí lee el clínico (sigue denegado)",
     {"tool_name": "Bash", "tool_input": {"command": "bash <<'EOF'\ncat ~/Clinico-PRIVADO/informe.pdf\nEOF"}}, True, None),
    ("Bash: heredoc SIN terminador (no sabemos dónde acaba → se juzga entero)",
     {"tool_name": "Bash", "tool_input": {"command": "cat > x.md <<'EOF'\ncat ~/Clinico-PRIVADO/informe.pdf"}}, True, None),
    ("Bash: heredoc sin comillas con sustitución de comandos (se juzga entero)",
     {"tool_name": "Bash", "tool_input": {"command": "cat > x.md <<EOF\n$(cat ~/Clinico-PRIVADO/informe.pdf)\nEOF"}}, True, None),
    ("Bash: cat de ruta clínica REAL junto a un heredoc inocente (sigue denegado)",
     {"tool_name": "Bash", "tool_input": {"command": "cat > x.md <<'EOF'\nhola\nEOF\ncat '00_FUENTE-DE-VERDAD/_PRIVADO_NUCLEO/notas.md'"}}, True, None),
]

fallos = 0
for desc, payload, debe, env in CASOS:
    den, msg = run(payload, env)
    ok = (den == debe)
    if not ok:
        fallos += 1
    marca = "✅" if ok else "❌ MAL"
    verbo = "DENEGADO" if den else "pasa"
    print("  %s %-46s -> %s" % (marca, desc, verbo))

print()
print("VEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % fallos))
raise SystemExit(1 if fallos else 0)

# el log de auditoria
LOG = os.path.expanduser("~/claudecode/.claude/logs/clinico-access.log")
if os.path.exists(LOG):
    lineas = open(LOG, encoding="utf-8").read().strip().splitlines()
    print("\n=== traza de auditoría (que antes NO existía) — últimas 4 ===")
    for l in lineas[-4:]:
        print("  " + l[:96])
    print("  total de accesos registrados: %d" % len(lineas))
