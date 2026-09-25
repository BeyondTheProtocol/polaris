#!/usr/bin/env python3
"""test_copy_web_guard.py — el hero publicado no cambia sin su OK; una sección nueva sí es mía.

NORMA: `feedback-no-tocar-copy-web-sin-ok` (clase BLOQUEO) — «cambiar copy EXISTENTE de la web
(sobre todo el hero/H1) NO es zona autónoma — requiere OK explícito y bien entendido de {{TITULAR}};
páginas/secciones NUEVAS sí son autónomas».

POR QUÉ EL GATE VA EN EL `git commit` Y NO EN Write/Edit: medido sobre todos los transcripts,
hay 0 llamadas a Write/Edit contra `i18n/locales` o `content/`, y 23 comandos Bash que las tocan
con `python3 - <<EOF` y `sed`. Vigilar la tool escritora sería vigilar una puerta por la que no
pasa nadie. Sobre el diff de git da igual cómo se escribió el fichero.

TRES NIVELES, y el test los separa porque la diferencia es el diseño entero:
  FRENA  → hero/H1 cambiado, o clave borrada.        (27 de 86 commits reales del repo web)
  AVISA  → otro copy existente cambiado. NO bloquea. (32 de 86)
  PASA   → claves nuevas, páginas nuevas, adiciones. (27 de 86)
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "copy_web_guard.py")

_TMP = tempfile.mkdtemp(prefix="copyweb_")
WEB = _os.path.join(_TMP, "web")
OTRO = _os.path.join(_TMP, "otro-repo")
BASE = {"hero": {"title": "Un cáncer raro, {op} real.", "subtitle": "{{TITULAR}} tiene un tumor…"},
        "team": {"subtitle": "Una ingeniera"},
        "nav": {"title": "Inicio", "map": "Mapa"},
        "press": {"quote_1": "cita uno"}}


def _sh(*a, **kw):
    return subprocess.run(list(a), capture_output=True, text=True, check=True, **kw)


def _escribe(d, rel, obj):
    p = _os.path.join(d, rel)
    _os.makedirs(_os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2) if isinstance(obj, (dict, list)) else fh.write(obj)


for d, es_web in ((WEB, True), (OTRO, False)):
    _os.makedirs(d)
    _sh("git", "init", "-q", "-b", "main", cwd=d)
    if es_web:
        _escribe(d, "nuxt.config.ts", "export default {}\n")
        _escribe(d, "i18n/locales/es.json", BASE)
        _escribe(d, "content/es/historia/01.md", "# Historia\n\nprimera línea\n")
    else:
        _escribe(d, "i18n/locales/es.json", BASE)   # mismos ficheros, pero sin nuxt.config.ts
    _sh("git", "add", "-A", cwd=d)
    _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base", cwd=d)

ENV = dict(_os.environ)
ENV.pop("BTP_COPY_OK", None)


def _veredicto(cwd=WEB, cmd="git commit -m x", env=None):
    p = subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=25,
                       env=env or ENV,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}))
    if p.returncode == 2:
        return "FRENA", p.stderr
    if "additionalContext" in (p.stdout or ""):
        return "AVISA", p.stdout
    return "PASA", p.stdout


def _prepara(d, rel, obj):
    """Deja un cambio EN EL ÍNDICE, que es lo que mira el guard.

    `git checkout -- .` restaura desde el ÍNDICE, no desde HEAD: dejaba colado el cambio del
    caso anterior y un test daba AVISA por culpa del de arriba. Se limpia contra HEAD.
    """
    _sh("git", "reset", "-q", "--hard", "HEAD", cwd=d)
    _sh("git", "clean", "-qfd", cwd=d)
    _escribe(d, rel, obj)
    _sh("git", "add", rel, cwd=d)


import copy  # noqa: E402
fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


# ── FRENA ────────────────────────────────────────────────────────────────────────────────
d = copy.deepcopy(BASE); d["hero"]["title"] = "{{DIAGNOSTICO}}."
_prepara(WEB, "i18n/locales/es.json", d)
v, msg = _veredicto()
check("cambiar hero.title → FRENA", v == "FRENA")
check("el mensaje nombra el hero y la clave", "HERO" in msg and "hero.title" in msg)
check("el mensaje cita el precedente del revert", "02ccb94" in msg)

d = copy.deepcopy(BASE); d["hero"]["subtitle"] = "otro subtítulo"
_prepara(WEB, "i18n/locales/es.json", d)
check("cambiar hero.subtitle → FRENA", _veredicto()[0] == "FRENA")

d = copy.deepcopy(BASE); del d["nav"]["map"]
_prepara(WEB, "i18n/locales/es.json", d)
v, msg = _veredicto()
check("BORRAR una clave publicada → FRENA", v == "FRENA")
check("el mensaje dice que borra", "BORRA" in msg)

# ── AVISA (no bloquea) ───────────────────────────────────────────────────────────────────
d = copy.deepcopy(BASE); d["team"]["subtitle"] = "Una paciente"
_prepara(WEB, "i18n/locales/es.json", d)
v, msg = _veredicto()
check("cambiar team.subtitle (no hero) → AVISA, no frena", v == "AVISA")
check("el aviso nombra la clave y dice que no es bloqueo",
      "team.subtitle" in msg and "no es un bloqueo" in msg.lower().replace("aviso, no un bloqueo", "no es un bloqueo"))

d = copy.deepcopy(BASE); d["nav"]["title"] = "Portada"
_prepara(WEB, "i18n/locales/es.json", d)
check("`nav.title` NO se confunde con el hero (hero estricto) → AVISA", _veredicto()[0] == "AVISA")

# ── PASA en silencio ─────────────────────────────────────────────────────────────────────
d = copy.deepcopy(BASE); d["press"]["quote_2"] = "cita nueva"
_prepara(WEB, "i18n/locales/es.json", d)
check("añadir una clave NUEVA → PASA (zona autónoma)", _veredicto()[0] == "PASA")

d = copy.deepcopy(BASE); d["seccion_nueva"] = {"title": "Sección nueva", "body": "texto"}
_prepara(WEB, "i18n/locales/es.json", d)
check("añadir una SECCIÓN entera nueva → PASA", _veredicto()[0] == "PASA")

_sh("git", "reset", "-q", "--hard", "HEAD", cwd=WEB)
_sh("git", "clean", "-qfd", cwd=WEB)
_escribe(WEB, "content/es/historia/02.md", "# Capítulo nuevo\n\ntexto\n")
_sh("git", "add", "-A", cwd=WEB)
check("página de contenido NUEVA → PASA", _veredicto()[0] == "PASA")

_sh("git", "reset", "-q", "--hard", "HEAD", cwd=WEB)
_sh("git", "clean", "-qfd", cwd=WEB)
_escribe(WEB, "content/es/historia/01.md", "# Historia\n\nprimera línea\nlínea añadida al final\n")
_sh("git", "add", "-A", cwd=WEB)
check("añadir líneas a una página existente → PASA", _veredicto()[0] == "PASA")

_sh("git", "reset", "-q", "--hard", "HEAD", cwd=WEB)
_sh("git", "clean", "-qfd", cwd=WEB)
_escribe(WEB, "content/es/historia/01.md", "# Historia\n\nlínea REESCRITA\n")
_sh("git", "add", "-A", cwd=WEB)
check("reescribir texto de una página existente → AVISA", _veredicto()[0] == "AVISA")

# ── lo demás ─────────────────────────────────────────────────────────────────────────────
d = copy.deepcopy(BASE); d["hero"]["title"] = "otro"
_prepara(WEB, "i18n/locales/es.json", d)
check("BTP_COPY_OK=1 → PASA (escotilla explícita)",
      _veredicto(env=dict(ENV, BTP_COPY_OK="1"))[0] == "PASA")
check("BTP_COPY_OK=0 NO cuenta → FRENA", _veredicto(env=dict(ENV, BTP_COPY_OK="0"))[0] == "FRENA")
check("`git status` no dispara nada", _veredicto(cmd="git status --short")[0] == "PASA")
check("`git add` no dispara nada (el gate es el commit)", _veredicto(cmd="git add -A")[0] == "PASA")
check("`git push` no dispara este gate (de eso va egreso_guard)",
      _veredicto(cmd="git push origin main")[0] == "PASA")

d2 = copy.deepcopy(BASE); d2["hero"]["title"] = "otro"
_prepara(OTRO, "i18n/locales/es.json", d2)
check("mismo cambio en un repo que NO es la web → PASA", _veredicto(cwd=OTRO)[0] == "PASA")

_prepara(WEB, "i18n/locales/es.json", copy.deepcopy(BASE))
_escribe(WEB, "i18n/locales/es.json", "{ esto no es json")
_sh("git", "add", "i18n/locales/es.json", cwd=WEB)
check("locale que no parsea → PASA (fail-OPEN: lo que no se puede juzgar, no se juzga)",
      _veredicto()[0] == "PASA")

p = subprocess.run([_sys.executable, GUARD], input="{no es json", capture_output=True,
                   text=True, timeout=20, env=ENV)
check("payload ilegible → PASA (fail-OPEN deliberado)", p.returncode == 0)
check("tool que no es Bash → PASA",
      subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=20, env=ENV,
                     input=json.dumps({"tool_name": "Write", "tool_input": {"command": "git commit -m x"},
                                       "cwd": WEB})).returncode == 0)

for desc, ok in casos:
    print(("  ✅ " if ok else "  ❌ ") + desc)
shutil.rmtree(_TMP, ignore_errors=True)
print()
print("RESULTADO copy_web: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ EL COPY PUBLICADO NO SE TOCA SOLO" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
