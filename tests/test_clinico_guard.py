#!/usr/bin/env python3
"""Prueba el guard clínico INTERACTIVO (.claude/hooks/clinico_guard.py)."""
import atexit
import io
import json
import os
import subprocess

# El hook del ÁRBOL EN EL QUE CORRE este test, no el de casa base. Apuntaba a
# ~/claudecode fijo, así que desde un worktree probaba el guard viejo: todo lo que
# verificaras antes de fusionar era teatro. (Encontrado el 25-jul-26 auditando el muro.)
ARBOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# La ventanilla REAL de este árbol. Nunca a fuego: ver el guardián de más abajo.
_VENTANILLA = os.path.join(ARBOL, "tools", "lector_clinico.py")
H = os.path.join(ARBOL, ".claude", "hooks", "clinico_guard.py")


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
    # BYPASS POR SUBSTRING (19-sep-26, deuda clinico-guard-ventanilla-por-substring): bastaba
    # con que «lector_clinico.py» apareciera en el texto. Ahora solo cuenta quien la EJECUTA.
    ("Bash: cat clínico con la ventanilla en un COMENTARIO (bypass)",
     {"tool_name": "Bash", "tool_input": {"command": "cat '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf' # lector_clinico.py"}}, True, None),
    ("Bash: cat clínico encadenado DESPUÉS de una llamada legítima a la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'; cp ~/Clinico-PRIVADO/informe.pdf /tmp/x.pdf"}}, True, None),
    ("Bash: echo que MENCIONA la ventanilla y luego lee el clínico",
     {"tool_name": "Bash", "tool_input": {"command": "echo lector_clinico.py && head ~/Clinico-PRIVADO/informe.pdf"}}, True, None),
    # La ruta sale de ARBOL, no a fuego (20-sep-26). Estaba escrita como
    # `/Users/polaris/claudecode/tools/lector_clinico.py`, la de la cajita: el guard reconoce
    # la ventanilla por el SHA-256 del fichero contra `<árbol>/tools/lector_clinico.py`, así
    # que en cualquier otra máquina esa ruta no existe, el hash no casa y DENIEGA — haciendo
    # exactamente lo que debe. Era el único rojo que le quedaba al CI del repo público, y no
    # era del guard: era de este caso. Absoluta sigue siendo, que es la gracia del caso.
    ("Bash: la ventanilla con cd previo y variable de entorno",
     {"tool_name": "Bash", "tool_input": {"command": "cd /tmp && BTP_AGENT=x python3 %s '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md' | head -40" % os.path.join(ARBOL, "tools", "lector_clinico.py")}}, False, None),
    ("Bash: la ventanilla en modo procesa sobre DICOM clínico",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py procesa visor3d -- inventario '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/Imagen'"}}, False, None),
    ("Bash: el procesador llamado DIRECTO, sin ventanilla (sin traza)",
     {"tool_name": "Bash", "tool_input": {"command": ".venv-imagen/bin/python tools/visor3d.py inventario '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/Imagen'"}}, True, None),
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
    # ── REPRODUCCIÓN DE 14.378 COMANDOS REALES (19/20-sep-26) ────────────────────────
    # Quitar el bypass por substring denegó de golpe 16 comandos de trabajo diario que solo
    # pasaban porque el texto mencionaba la ventanilla. Tres clases, y su contrapartida: lo
    # que NO puede pasar a pesar del arreglo. Sin estos casos, el arreglo del muro rompe la
    # mano que lo usa y empuja a MURO_ALLOW_CLINICAL=1, que sí abre el muro entero.
    #
    # Clase 1 — la cabecera de un bucle REPARTE, no lee.
    ("Bash: bucle cuyo cuerpo lee SOLO por la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md' '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/b.md'; do python3 tools/lector_clinico.py \"$f\"; done"}}, False, None),
    ("Bash: el mismo bucle imprimiendo el NOMBRE de cada fichero",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'; do echo \"== $f\"; python3 tools/lector_clinico.py \"$f\"; done"}}, False, None),
    ("Bash: bucle sobre rutas clínicas consumido por cat (el agujero que NO se abre)",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'; do cat \"$f\"; done"}}, True, None),
    ("Bash: bucle sobre un GLOB clínico consumido por cat",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO'/*.md; do cat \"$f\"; done"}}, True, None),
    ("Bash: bucle que pasa por la ventanilla Y ADEMÁS hace cat del mismo fichero",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'; do python3 tools/lector_clinico.py \"$f\"; cat \"$f\"; done"}}, True, None),
    # La variable atada fuera del bucle es lo mismo: atar no es leer, usar sí.
    ("Bash: variable con la carpeta clínica, leída por la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "H='00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO'; python3 tools/lector_clinico.py \"$H/a.md\""}}, False, None),
    # AGUJERO CONOCIDO Y **PREEXISTENTE**, no lo abre este arreglo: el guard nunca ha resuelto
    # variables, así que `H=<ruta>; cat "$H/a.md"` pasa igual en casa base. Se probó a
    # resolverlas y la reproducción del 20-sep-26 lo tumbó: rompía 49 comandos reales de
    # trabajo diario (todos los `H=<carpeta clínica>; ls "$H/…"` del historial). Queda en el
    # libro de deuda como `clinico-guard-no-resuelve-variables`. Este caso fija el
    # comportamiento de HOY: si alguien lo cierra, el test avisa de que hay deuda que cerrar.
    ("Bash: variable resuelta y leída con cat (agujero preexistente, ver deuda)",
     {"tool_name": "Bash", "tool_input": {"command": "H='00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO'; cat \"$H/a.md\""}}, False, None),
    # `echo` imprime el nombre; no abre el fichero. Pero solo para lo que salió de la variable:
    ("Bash: echo de una ruta clínica ESCRITA A PELO (sigue denegado)",
     {"tool_name": "Bash", "tool_input": {"command": "echo '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'; python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/b.md'"}}, True, None),
    ("Bash: echo de la variable pero POR TUBERÍA a xargs cat (eso sí lee)",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO'/*.md; do echo \"$f\" | xargs cat; python3 tools/lector_clinico.py \"$f\"; done"}}, True, None),
    #
    # Clase 2 — el salto de línea separa subcomandos (shlex lo trataba como un espacio).
    ("Bash: script de tres líneas que acaba llamando a la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "cd ~/claudecode\necho 'leyendo el informe'\npython3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'"}}, False, None),
    ("Bash: continuación de línea — el cat y su ruta clínica son UN comando",
     {"tool_name": "Bash", "tool_input": {"command": "cat \\\n'00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'"}}, True, None),
    ("Bash: comentario al final de una línea, ruta clínica en la siguiente",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py a.md # la ventanilla\ncat '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'"}}, True, None),
    #
    # Clase 3 — palabras que preceden al comando sin serlo.
    ("Bash: `do` delante de la ventanilla (bucle en una línea)",
     {"tool_name": "Bash", "tool_input": {"command": "for n in a b; do python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/'$n'.md'; done"}}, False, None),
    ("Bash: `timeout 900` delante de la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "timeout 900 python3 tools/lector_clinico.py procesa visor3d -- inventario '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/Imagen'"}}, False, None),
    ("Bash: la ventanilla dentro de una sustitución de comandos",
     {"tool_name": "Bash", "tool_input": {"command": "out=$(python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'); echo ok"}}, False, None),
    ("Bash: `cat` con la ventanilla COMO ARGUMENTO y una ruta clínica detrás",
     {"tool_name": "Bash", "tool_input": {"command": "cat tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'"}}, True, None),
    # Y lo que el replay confirmó que debe SEGUIR denegado aunque el comando use la ventanilla:
    ("Bash: ls de la carpeta clínica junto a una llamada legítima a la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'; ls -t '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO' | head -40"}}, True, None),
    # ── LO QUE CAZÓ EL RED-TEAM (comité `verificacion`, 20-sep-26) ───────────────────
    # Una versión intermedia de este arreglo resolvía variables de shell y partía CUALQUIER
    # script por líneas. Abría 8 lecturas que casa base sí denegaba, todas por la misma
    # causa: `rutas_clinicas_en_tokens` se salta el primer token porque es «el binario», y
    # trocear de más convierte una ruta en primer token. Estos casos fijan el cierre.
    ("Bash: heredoc a python que abre la ruta con open()",
     {"tool_name": "Bash", "tool_input": {"command": "python3 - <<EOF\nprint(open('00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md').read())\nEOF"}}, True, None),
    ("Bash: array de bash con la ruta entre paréntesis",
     {"tool_name": "Bash", "tool_input": {"command": "a=('00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'); cat \"${a[0]}\""}}, True, None),
    ("Bash: comentario terminado en «\\» y la lectura en la línea siguiente",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py /dev/null # nota \\\ncat '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md'"}}, True, None),
    ("Bash: echo con una sustitución de comandos que sí lee",
     {"tool_name": "Bash", "tool_input": {"command": "for f in '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO'/*.md; do echo \"$(cat \"$f\")\"; done; python3 tools/lector_clinico.py /dev/null"}}, True, None),
    ("Bash: la ventanilla y detrás una comilla sin cerrar (tokenizar falló → no se exime nada)",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py /dev/null; cat '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/a.md"}}, True, None),
    # ── LA VENTANILLA CON BANDERAS (96d9e4a / 77745cf, 20-sep-26) ────────────────────
    # Desde que sirve binarios, la llamada lleva `--binario`, `--texto` o `--a <destino>`.
    # `_es_llamada_ventanilla` mira quién EJECUTA, no cuántos argumentos vienen detrás, así que
    # las banderas no la despistan. Y el caso que `verificacion` re-detectó como 3ª aparición
    # del bypass —copiar con `--a` y leer la copia a pelo— queda cerrado.
    ("Bash: la ventanilla con --texto sobre un PDF clínico",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py --texto '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, False, None),
    ("Bash: la ventanilla con --a <destino> dentro de zona clínica",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py --a '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/copia.pdf' '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, False, None),
    ("Bash: copiar con --a y luego leer la copia a pelo (bypass, 3ª detección)",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py --a '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/copia.pdf' '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf' && cat '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/copia.pdf'"}}, True, None),
    ("Bash: la ventanilla con --binario tubeada a pdftoppm",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py --binario '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf' | pdftoppm -png - /tmp/pag"}}, False, None),
    ("Bash: la ventanilla en modo procesa con ocr_informes",
     {"tool_name": "Bash", "tool_input": {"command": "cd ~/claudecode && python3 tools/lector_clinico.py procesa ocr_informes -- --raiz '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/_historial' --si"}}, False, None),
    ("Bash: el procesador OCR llamado DIRECTO, saltándose la ventanilla",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/ocr_informes.py --raiz '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/_historial' --si"}}, True, None),
    ("Bash: cat de ruta clínica REAL junto a un heredoc inocente (sigue denegado)",
     {"tool_name": "Bash", "tool_input": {"command": "cat > x.md <<'EOF'\nhola\nEOF\ncat '00_FUENTE-DE-VERDAD/_PRIVADO_NUCLEO/notas.md'"}}, True, None),
    # ── La ventanilla es UN script concreto, no un nombre de fichero (20-sep-26) ──────────────
    # Deuda `clinico-guard-ventanilla-por-basename`, cazada por el comite de verificacion: el
    # guard eximia por `os.path.basename(tok) == "lector_clinico.py"`, asi que bastaba con dejar
    # un fichero con ese nombre en CUALQUIER sitio para leer el archivo clinico sin pasar por la
    # ventanilla y sin dejar traza. El agujero era PREEXISTENTE, tambien en casa base.
    ("Bash: IMPOSTOR con el nombre de la ventanilla en /tmp",
     {"tool_name": "Bash", "tool_input": {"command": "python3 /tmp/impostor/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, True, None),
    ("Bash: IMPOSTOR en el home del usuario",
     {"tool_name": "Bash", "tool_input": {"command": "python3 ~/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, True, None),
    ("Bash: IMPOSTOR ejecutado directo, sin interprete",
     {"tool_name": "Bash", "tool_input": {"command": "/tmp/impostor/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, True, None),
    ("Bash: IMPOSTOR por ruta relativa que sale del repo",
     {"tool_name": "Bash", "tool_input": {"command": "python3 ../../tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, True, None),
    ("Bash: el nombre SUELTO, sin tools/ delante",
     {"tool_name": "Bash", "tool_input": {"command": "python3 lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, True, None),
    # Y las formas BUENAS siguen pasando: un arreglo que rompa el trabajo diario es peor que el
    # agujero. Esto ya paso el 19-sep con el bypass por substring, que hubo que revertir fusionado.
    ("Bash: la ventanilla de verdad, relativa",
     {"tool_name": "Bash", "tool_input": {"command": "python3 tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, False, None),
    ("Bash: la ventanilla de verdad, absoluta a casa base",
     {"tool_name": "Bash", "tool_input": {"command": "python3 %s '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'" % _VENTANILLA}}, False, None),
    ("Bash: la ventanilla de verdad, con ./ delante",
     {"tool_name": "Bash", "tool_input": {"command": "python3 ./tools/lector_clinico.py '00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf'"}}, False, None),
]


# ── LA VENTANILLA SE RECONOCE POR IDENTIDAD, NO POR NOMBRE (20-sep-26) ──────────────
# Deuda clinico-guard-ventanilla-por-basename: bastaba con que el script se LLAMARA
# `lector_clinico.py` para eximirlo, así que `python3 /tmp/x/lector_clinico.py <clínico>`
# leía lo que quisiera. Estos casos necesitan ficheros de verdad, así que se construyen
# aquí en vez de ir en la lista de arriba. El tercero es el que evita el arreglo perezoso:
# una copia FIEL en otro árbol (un worktree) tiene que seguir pasando, o el muro rompe el
# trabajo en paralelo, que es como {{TITULAR}} trabaja.
def _casos_identidad_ventanilla():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="test-ventanilla-")
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    CLINICO = "00_FUENTE-DE-VERDAD/01 · Tratamiento/_PRIVADO_CLINICO/informe.pdf"

    # 1. impostor pelado: el nombre correcto, nada más
    io.open(os.path.join(tmp, "lector_clinico.py"), "w").write("print('impostor')\n")
    # 2. impostor que IMITA la estructura del árbol (tools/ + el hook al lado)
    os.makedirs(os.path.join(tmp, "falso", "tools"))
    os.makedirs(os.path.join(tmp, "falso", ".claude", "hooks"))
    io.open(os.path.join(tmp, "falso", "tools", "lector_clinico.py"), "w").write("print('impostor')\n")
    io.open(os.path.join(tmp, "falso", ".claude", "hooks", "clinico_guard.py"), "w").write("")
    # 3. copia FIEL en otro árbol: un worktree legítimo
    os.makedirs(os.path.join(tmp, "wt", "tools"))
    os.makedirs(os.path.join(tmp, "wt", ".claude", "hooks"))
    shutil.copy(os.path.join(ARBOL, "tools", "lector_clinico.py"),
                os.path.join(tmp, "wt", "tools", "lector_clinico.py"))
    io.open(os.path.join(tmp, "wt", ".claude", "hooks", "clinico_guard.py"), "w").write("")

    def bash(cmd):
        return {"tool_name": "Bash", "tool_input": {"command": cmd}}
    return [
        ("Bash: impostor que solo se LLAMA lector_clinico.py",
         bash("python3 %s/lector_clinico.py '%s'" % (tmp, CLINICO)), True, None),
        ("Bash: impostor que además imita la estructura del árbol",
         bash("python3 %s/falso/tools/lector_clinico.py '%s'" % (tmp, CLINICO)), True, None),
        ("Bash: copia FIEL de la ventanilla en otro worktree (legítima)",
         bash("python3 %s/wt/tools/lector_clinico.py '%s'" % (tmp, CLINICO)), False, None),
    ]


CASOS += _casos_identidad_ventanilla()

fallos = 0
# ── GUARDIÁN DE LA CLASE (20-sep-26) ────────────────────────────────────────────────
# Esta ruta ya se arregló una vez, en «la ventanilla con cd previo y variable de entorno», y
# volvió a entrar en otro caso dos commits después. Arreglar el ejemplo no sirve: el guard
# reconoce la ventanilla por el SHA-256 del fichero, así que una ruta de la cajita escrita a
# fuego no existe en ninguna otra máquina, no se exime y DENIEGA — y el CI del repo público se
# pone rojo por un fallo que no es del muro. Esto lo caza en el sitio, no tres horas después
# en un runner de Linux.
_a_fuego = [d for d, payload, _, _ in CASOS
            if "/Users/" in str(payload.get("tool_input", {}).get("command", ""))
            and _VENTANILLA not in str(payload.get("tool_input", {}).get("command", ""))]
if _a_fuego:
    print("\n❌ CASOS CON UNA RUTA ABSOLUTA A FUEGO (usa `_VENTANILLA`, que sale de ARBOL):")
    for d in _a_fuego:
        print("   ·", d)
    raise SystemExit(1)   # `SystemExit`, no `sys.exit`: aquí arriba `sys` aún no se ha importado

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
