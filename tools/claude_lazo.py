#!/usr/bin/env python3
"""claude_lazo.py — la versión de Claude Code que usa el lazo va FIJADA y solo sube con el canario verde.

POR QUÉ (P9, 25-sep-2026). Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión
del 25-sep-2026. La instalación nativa se actualiza sola, y una versión nueva puede cambiar cómo
arranca `claude -p` (la doc oficial anuncia `--bare` por defecto, que no carga hooks). El canario de
run_agent.sh ya para el lazo si pasa eso; esto evita llegar ahí: el lazo corre una versión concreta y
solo cambia a otra después de probarla. Las sesiones interactivas siguen con ~/.local/bin/claude y
su actualización automática: esto solo afecta al lazo.

CÓMO
  · El binario fijado vive en ~/.local/share/btp/claude-lazo/<versión> (enlace duro al de
    ~/.local/share/claude/versions/, o copia si no se puede): la poda del actualizador no lo borra.
  · `ruta` le da a run_agent.sh ese binario. Sin versión fijada, o si falta el fichero, devuelve
    `claude` (el que se actualiza solo) y el canario de run_agent.sh sigue guardando: falla abierta
    en disponibilidad, nunca en el muro.
  · `revisar` (run_agent.sh lo lanza de fondo, como mucho 1 vez cada 20 h): si hay una versión
    instalada más nueva que la fijada, le pasa tools/canario_preflight.sh con TODOS los settings del
    lazo. Verde → se fija. Rojo → se queda la anterior y aviso urgente (1 por versión). Si la fijada
    lleva más de 14 días sin poder subir, aviso urgente (1 al día): quedarse atrás también es riesgo,
    porque las correcciones de seguridad llegan en versiones nuevas.
  Los parches llegan con el retraso del canal `stable` más, como mucho, un día.

USO
  python3 tools/claude_lazo.py ruta                 # binario que debe usar el lazo
  python3 tools/claude_lazo.py estado               # versión fijada, instaladas, desfase
  python3 tools/claude_lazo.py probar <binario>     # canario contra todos los settings del lazo
  python3 tools/claude_lazo.py revisar [--forzar]   # sube la fijada si la nueva pasa el canario
  python3 tools/claude_lazo.py fijar <versión>      # fija a mano (también pasa el canario)
"""
import datetime as dt
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
DIR = os.path.join(STATE, "claude_lazo")
PIN = os.path.join(DIR, "pin.json")
VERSIONES = os.environ.get("BTP_CLAUDE_VERSIONS_DIR") or os.path.expanduser("~/.local/share/claude/versions")
BINS = os.environ.get("BTP_LAZO_BIN_DIR") or os.path.expanduser("~/.local/share/btp/claude-lazo")
SALIDA = os.environ.get("BTP_SALIDA") or os.path.join(REPO, "tools", "salida.py")
PREFLIGHT = os.path.join(REPO, "tools", "canario_preflight.sh")
DESFASE_DIAS = int(os.environ.get("BTP_LAZO_DESFASE_DIAS", "14"))
CADA_H = 20


def _v(s):
    """'2.1.236' → (2, 1, 236). Lo que no es versión → None (se ignora)."""
    return tuple(int(x) for x in s.split(".")) if re.fullmatch(r"\d+(\.\d+)+", s or "") else None


def _ahora():
    return dt.datetime.now(dt.timezone.utc)


def _leer(p, defecto=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return defecto


def _escribir(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def instaladas():
    if not os.path.isdir(VERSIONES):
        return []
    return sorted((n for n in os.listdir(VERSIONES) if _v(n)), key=_v)


def pin():
    return _leer(PIN) or {}


def ruta():
    p = pin()
    b = os.path.join(BINS, p.get("version", ""))
    if p.get("version") and os.path.isfile(b) and os.access(b, os.X_OK):
        return b
    if p.get("version"):
        sys.stderr.write("claude_lazo: falta el binario fijado %s → uso `claude`; el canario sigue guardando\n" % b)
    return "claude"


def perfiles():
    """Todos los settings.<perfil>.json: run_agent.sh admite cualquiera por BTP_SETTINGS."""
    return sorted(p for p in glob.glob(os.path.join(REPO, ".claude", "settings.*.json"))
                  if os.path.basename(p) != "settings.local.json")


def probar(binario):
    """Canario contra cada perfil del lazo. Devuelve la lista de perfiles que NO cargaron hooks."""
    malos = []
    for s in perfiles():
        r = subprocess.run(["bash", PREFLIGHT, binario, s, os.path.join(DIR, "testigos")],
                           cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            malos.append(os.path.basename(s))
    return malos


def _enlazar(version):
    """Pone el binario de `version` en BINS (enlace duro; si no se puede, copia). Devuelve la ruta."""
    src = os.path.join(VERSIONES, version)
    dst = os.path.join(BINS, version)
    os.makedirs(BINS, exist_ok=True)
    if not os.path.isfile(dst):
        tmp = dst + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    return dst


def _podar(queda):
    """Deja en BINS solo la versión fijada y la anterior (para volver atrás a mano)."""
    vs = sorted((n for n in os.listdir(BINS) if _v(n)), key=_v) if os.path.isdir(BINS) else []
    guardar = set(queda)
    for n in vs:
        if n not in guardar:
            try:
                os.remove(os.path.join(BINS, n))
            except OSError:
                pass


def _avisar(clave, texto):
    """Aviso urgente por salida.py, 1 vez por clave (anti-spam: el lazo llama a esto muchas veces)."""
    flag = os.path.join(DIR, "avisos", re.sub(r"[^\w.-]", "_", clave))
    if os.path.exists(flag):
        return False
    os.makedirs(os.path.dirname(flag), exist_ok=True)
    open(flag, "w").write(_ahora().isoformat())
    subprocess.run([sys.executable, SALIDA, "report-urgente", texto],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def fijar(version, probada=True, motivo="manual"):
    b = _enlazar(version)
    if probada:
        malos = probar(b)
        if malos:
            if version != pin().get("version"):
                try:
                    os.remove(b)
                except OSError:
                    pass
            return False, malos
    anterior = pin().get("version")
    _escribir(PIN, {"version": version, "fijada": _ahora().isoformat(), "motivo": motivo,
                    "anterior": anterior})
    _podar([version] + ([anterior] if anterior else []))
    return True, []


def revisar(forzar=False):
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, ".lock"), "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "otro revisar en marcha"
        marca = os.path.join(DIR, "ultima_revision")
        if not forzar and os.path.exists(marca):
            edad_h = (_ahora().timestamp() - os.path.getmtime(marca)) / 3600
            if edad_h < CADA_H:
                return "revisado hace %.1f h" % edad_h
        open(marca, "w").write(_ahora().isoformat())
        vs = instaladas()
        if not vs:
            return "sin versiones instaladas en %s" % VERSIONES
        nueva, actual = vs[-1], pin().get("version")
        if actual and _v(nueva) <= _v(actual):
            return "al día: %s" % actual
        ok, malos = fijar(nueva, motivo="revisar: canario verde")
        if ok:
            return "fijada %s (antes %s)" % (nueva, actual or "ninguna")
        _avisar("rojo-" + nueva,
                "⚠️ Claude Code %s NO pasa el canario del muro (no carga los hooks en: %s). El lazo sigue "
                "con la versión %s, que sí lo pasa. Hay que revisar qué cambió antes de subir."
                % (nueva, ", ".join(malos), actual or "de siempre (sin fijar)"))
        if actual:
            dias = (_ahora() - dt.datetime.fromisoformat(pin()["fijada"])).days
            if dias > DESFASE_DIAS:
                _avisar("desfase-%s-%s" % (actual, _ahora().date()),
                        "⚠️ El lazo lleva %d días en Claude Code %s sin poder subir: las versiones nuevas "
                        "no pasan el canario del muro. Quedarse atrás también es riesgo (parches de "
                        "seguridad)." % (dias, actual))
        return "rojo: %s no pasa el canario (%s); se queda %s" % (nueva, ", ".join(malos), actual)


def estado():
    p, vs = pin(), instaladas()
    return {"fijada": p.get("version"), "desde": p.get("fijada"), "anterior": p.get("anterior"),
            "ruta": ruta(), "instaladas": vs, "mas_nueva": vs[-1] if vs else None}


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "ruta":
        print(ruta())
    elif cmd == "estado":
        print(json.dumps(estado(), ensure_ascii=False, indent=1))
    elif cmd == "probar" and len(argv) > 1:
        malos = probar(argv[1])
        print("verde" if not malos else "rojo: " + ", ".join(malos))
        return 1 if malos else 0
    elif cmd == "revisar":
        print(revisar(forzar="--forzar" in argv))
    elif cmd == "fijar" and len(argv) > 1:
        if not os.path.isfile(os.path.join(VERSIONES, argv[1])):
            print("no está instalada: %s" % argv[1])
            return 1
        ok, malos = fijar(argv[1])
        print("fijada %s" % argv[1] if ok else "rojo, no se fija: " + ", ".join(malos))
        return 0 if ok else 1
    else:
        print(__doc__.split("USO")[1])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
