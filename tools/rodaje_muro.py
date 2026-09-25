#!/usr/bin/env python3
"""rodaje_muro.py — las 24 h de rodaje de un hook del muro, medidas en vez de prometidas.

POR QUÉ (20-sep-2026). La norma de {{TITULAR}} (`feedback-muro-soak-antes-de-fusionar`, 25-jul-26)
dice que un cambio en `clinico_guard.py` / `muro_guard.py` no se fusiona al pasar los tests:
se deja 24 h corriendo en la rama y solo entonces se fusiona. Hasta hoy eso era una promesa —
nadie guardaba desde cuándo, ni contra qué comparar al terminar. Esto lo convierte en dos
comandos y un veredicto.

QUÉ MIDE, Y QUÉ NO (importa, porque lo contrario es falsa tranquilidad):
  · `clinico_guard.py` está enganchado SOLO en `.claude/settings.json`, o sea en las sesiones
    INTERACTIVAS. El lazo 24/7 (`settings.autonomous.json`) no lo carga. Así que el rodaje de
    este hook lo hacen las sesiones que trabajan en un worktree de la rama, no el lazo.
  · Cada árbol usa SU hook (`${CLAUDE_PROJECT_DIR}/.claude/hooks/…`) y escribe SU log, así que
    una sesión en un worktree nacido de master sigue con el guard viejo: el rodaje solo cuenta
    lo que pasa por los árboles de la rama.
  · Lo que sí cubre a todas las sesiones es el replay: al revisar, los comandos Bash REALES
    que aparecieron durante la ventana se pasan por los dos hooks. Es a posteriori, pero es
    tráfico de verdad, no casos inventados.

USO
  python3 tools/rodaje_muro.py iniciar --nuevo <hook> --viejo <hook> [--horas 24] [--nota "…"]
  python3 tools/rodaje_muro.py revisar   [--json]
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from _casa import casa_base, state_dir  # noqa: E402

LOG_REL = os.path.join(".claude", "logs", "clinico-access.log")


def _rama_slug():
    try:
        r = subprocess.run(["git", "-C", os.path.dirname(AQUI), "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:            # noqa: BLE001
        r = ""
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in (r or "sin-rama"))


# El ESTADO vive en casa base, uno por rama (21-sep-2026). Antes era `<este árbol>/state/…`: un
# rodaje se lanza desde el worktree de la rama que se rueda, así que su estado moría con la poda
# y a las 24 h no quedaba contra qué comparar. Pasó con el guard de la ventanilla clínica: la
# deuda decía «está en rodaje» y el fichero no existía. Uno por rama para que dos rodajes a la
# vez no se pisen.
ESTADO = os.path.join(state_dir(), "rodaje_muro", "%s.json" % _rama_slug())


def _ahora():
    return dt.datetime.now().replace(microsecond=0)


def _ahora_utc():
    """El corte para el replay va en UTC, y no es un detalle.

    Los transcripts de Claude Code marcan la hora en UTC (`…T10:00:21.434Z`) y esta máquina va
    en CEST. Comparar el corte local contra esos `ts` descartaba DOS HORAS de tráfico: el
    rodaje decía «0 comandos nuevos» con el sistema a pleno rendimiento, que es la peor avería
    posible en una herramienta de vigilancia — la que no avisa de que no está mirando.
    """
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None)


def _arbol():
    return os.path.dirname(AQUI)


def _lineas_log():
    # El log de auditoría vive en CASA BASE desde el 20-sep-2026 (clinico_guard ya no lo parte
    # por worktree). Leerlo del árbol dejaba el rodaje ciego desde un worktree: o no existía, o
    # era una COPIA CONGELADA que el harness pone al reciclar el árbol — 57.473 líneas, 440 por
    # detrás de la real y sin crecer. «0 accesos nuevos» con apariencia de funcionar, que es la
    # peor avería de una herramienta de vigilancia.
    p = os.path.join(casa_base(), LOG_REL)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def _sha():
    try:
        return subprocess.run(["git", "-C", _arbol(), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "?"


def _rama():
    try:
        return subprocess.run(["git", "-C", _arbol(), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "?"


def _huella(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _hermanos(hook):
    """Los módulos del MISMO directorio que el hook importa (zonas_clinicas, _git_camino…).

    Sin ellos, la copia del hook cae a su lista de respaldo o no arranca: juzgaría otra cosa.
    """
    d = os.path.dirname(os.path.abspath(hook))
    with open(hook, encoding="utf-8", errors="replace") as fh:
        nombres = set(re.findall(r"(?:^|\s)(?:from|import)\s+([A-Za-z_]\w*)", fh.read()))
    return sorted(os.path.join(d, n + ".py") for n in nombres
                  if os.path.isfile(os.path.join(d, n + ".py"))
                  and os.path.join(d, n + ".py") != os.path.abspath(hook))


def _instantanea(hook, lado):
    """Copia FIJA del hook en casa base, fuera de cualquier worktree.

    25-sep-2026 (deuda rodaje-muro-worktree-podado-antes-de-tiempo, 2x). El estado apuntaba al
    hook DENTRO del worktree de la rama, y el árbol no aguanta 24 h: `silly-chatelet` se podó a
    media ventana (revisar habría dicho «no puede juzgar») y la app RECICLÓ `great-mahavira` para
    otra rama, así que su session_start.sh pasó a ser el de master y el rodaje comparaba el viejo
    consigo mismo sin avisar. Quién borra el árbol da igual; lo que se juzga no puede vivir en él.
    """
    destino = os.path.join(os.path.dirname(ESTADO), _rama_slug(), lado)
    shutil.rmtree(destino, ignore_errors=True)
    os.makedirs(destino)
    copia = os.path.join(destino, os.path.basename(hook))
    shutil.copy2(hook, copia)
    for h in _hermanos(hook):
        shutil.copy2(h, os.path.join(destino, os.path.basename(h)))
    return copia, _huella(copia)


def _no_puede_juzgar(e):
    """Motivo por el que las copias ya no son lo que se rodó, o '' si están bien."""
    for lado in ("viejo", "nuevo"):
        hook = e["hook_" + lado]
        huella = e.get("sha256_" + lado)
        if not os.path.exists(hook):
            if huella:
                return "falta la copia fija del hook %s (%s)" % (lado, hook)
            return ("el hook %s ya no existe (%s): su árbol se podó. Estado de antes de las copias "
                    "fijas: sácalo del commit con `git show %s:<ruta>` a un sitio estable, "
                    "o vuelve a `iniciar`." % (lado, hook, e.get("sha", "<sha>")))
        if huella and _huella(hook) != huella:
            return "la copia del hook %s cambió desde `iniciar` (%s)" % (lado, hook)
        if not huella and lado == "nuevo" and e.get("sha"):
            # Estado antiguo: la ruta vive en un árbol que pudo cambiar de rama.
            arbol = e.get("arbol", "")
            rel = os.path.relpath(hook, arbol) if arbol else ""
            r = subprocess.run(["git", "-C", casa_base(), "show", "%s:%s" % (e["sha"], rel)],
                               capture_output=True)
            if r.returncode == 0:
                with open(hook, "rb") as fh:
                    if fh.read() != r.stdout:
                        return ("el hook nuevo ya no es el de %s (%s): su árbol cambió de rama o de "
                                "commit, y juzgaría otra cosa." % (e["sha"], hook))
    return ""


def iniciar(a):
    for h in (a.nuevo, a.viejo):
        if not os.path.exists(h):
            print("⛔ no existe: %s" % h)
            return 2
        # Antes (20-sep) se vetaban los hooks en /tmp: el scratchpad con el viejo desapareció a
        # media ventana. Desde el 25-sep `_instantanea` copia los dos a casa base al iniciar, así
        # que dónde viva el original ya no importa; el veto solo estorbaba.
    inicio = _ahora()
    nuevo, sha_nuevo = _instantanea(a.nuevo, "nuevo")
    viejo, sha_viejo = _instantanea(a.viejo, "viejo")
    estado = {
        "inicio": inicio.isoformat(),
        "inicio_utc": _ahora_utc().isoformat(),   # el corte del replay: los ts vienen en UTC
        "hasta": (inicio + dt.timedelta(hours=a.horas)).isoformat(),
        "horas": a.horas,
        "arbol": _arbol(),
        "rama": _rama(),
        "sha": _sha(),
        "hook_nuevo": nuevo,
        "hook_viejo": viejo,
        "sha256_nuevo": sha_nuevo,
        "sha256_viejo": sha_viejo,
        "origen_nuevo": os.path.abspath(a.nuevo),
        "origen_viejo": os.path.abspath(a.viejo),
        "log_al_empezar": len(_lineas_log()),
        "nota": a.nota or "",
    }
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    with open(ESTADO, "w", encoding="utf-8") as fh:
        json.dump(estado, fh, ensure_ascii=False, indent=1)
    print("⏱️  rodaje iniciado — %s (%s @ %s)" % (inicio.isoformat(), estado["rama"], estado["sha"]))
    print("   termina:  %s" % estado["hasta"])
    print("   copias fijas: %s" % os.path.dirname(os.path.dirname(nuevo)))
    print("   log del árbol en la marca: %d líneas" % estado["log_al_empezar"])
    print("   revisa con: python3 tools/rodaje_muro.py revisar")
    return 0


def revisar(a):
    if not os.path.exists(ESTADO):
        print("⛔ no hay rodaje iniciado (falta %s)" % ESTADO)
        return 2
    with open(ESTADO, encoding="utf-8") as fh:
        e = json.load(fh)
    inicio = dt.datetime.fromisoformat(e["inicio"])
    hasta = dt.datetime.fromisoformat(e["hasta"])
    ahora = _ahora()
    horas = round((ahora - inicio).total_seconds() / 3600.0, 1)

    # 1) el log del árbol donde corre el hook nuevo, desde la marca
    nuevas = _lineas_log()[e["log_al_empezar"]:]
    deny = [l for l in nuevas if "\tDENY\t" in l]
    allow_v = [l for l in nuevas if "ALLOW (ventanilla)" in l]
    bypass = [l for l in nuevas if "BYPASS" in l]

    # 2) el tráfico real de TODAS las sesiones, reproducido contra los dos hooks
    sys.path.insert(0, AQUI)
    import replay_guard as RG                                          # noqa: E402
    motivo = _no_puede_juzgar(e)
    if motivo:
        print("⛔ EL RODAJE NO PUEDE JUZGAR: %s" % motivo)
        print("   No es un hallazgo, es la herramienta rota. Lo medido hasta ahora no vale.")
        return 2
    for h in (e["hook_viejo"], e["hook_nuevo"]):
        try:
            RG.comprueba(h)
        except RG.HookRoto as ex:
            print("⛔ EL RODAJE NO PUEDE JUZGAR: %s" % ex)
            print("   No es un hallazgo, es la herramienta rota. Arregla la ruta y vuelve a")
            print("   `iniciar`; lo medido hasta ahora no vale.")
            return 2
    # Los `ts` del transcript son UTC; el corte también, o se pierden las horas de desfase.
    corte = e.get("inicio_utc") or (inicio - (_ahora() - _ahora_utc())).isoformat()
    filas = [f for f in RG.comandos_reales() if f["ts"] and f["ts"][:19] >= corte[:19]]
    env = RG._entorno(os.path.join(os.path.sep, "tmp", "rodaje-muro"))
    os.makedirs(env["CLAUDE_PROJECT_DIR"], exist_ok=True)
    difs = []
    for f in filas:
        va, _ = RG.juzga(e["hook_viejo"], env, f["cmd"], cwd=f.get("cwd", ""), modo=f.get("modo", ""))
        vb, sb = RG.juzga(e["hook_nuevo"], env, f["cmd"], cwd=f.get("cwd", ""), modo=f.get("modo", ""))
        if va != vb:
            difs.append(dict(f, viejo=va, nuevo=vb, stderr=sb))
    relaja = [d for d in difs if d["viejo"] == "DENY"]
    endurece = [d for d in difs if d["nuevo"] == "DENY"]

    listo = (horas >= e["horas"]) and not relaja
    print("⏱️  rodaje de %s @ %s — %.1f h de %d" % (e["rama"], e["sha"], horas, e["horas"]))
    print("   ventana: %s → %s" % (inicio.isoformat(), hasta.isoformat()))
    print()
    print("   árbol con el hook nuevo (%s)" % e["arbol"])
    print("     · %d accesos nuevos en el log · %d DENY · %d por la ventanilla · %d bypass"
          % (len(nuevas), len(deny), len(allow_v), len(bypass)))
    for l in deny[-8:]:
        print("       DENY  " + l.split("\t", 2)[-1][:96])
    print()
    print("   tráfico real reproducido (todas las sesiones)")
    print("     · %d comandos nuevos · %d endurece (ALLOW→DENY) · %d RELAJA (DENY→ALLOW)"
          % (len(filas), len(endurece), len(relaja)))
    for d in relaja[:10]:
        print("       ⛔ " + d["cmd"].replace("\n", " ⏎ ")[:110])
    print()
    if relaja:
        print("🔴 NO FUSIONAR: el hook nuevo deja pasar algo que el viejo denegaba.")
    elif horas < e["horas"]:
        print("🟡 AÚN NO: faltan %.1f h de rodaje." % (e["horas"] - horas))
    else:
        print("🟢 RODAJE CUMPLIDO sin relajaciones. Fusionar sigue siendo gate de {{TITULAR}}.")
    if a.json:
        print(json.dumps({"horas": horas, "cumplido": horas >= e["horas"],
                          "relaja": len(relaja), "endurece": len(endurece),
                          "deny_en_log": len(deny), "listo": listo}, ensure_ascii=False))
    return 0 if not relaja else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("iniciar")
    i.add_argument("--nuevo", required=True)
    i.add_argument("--viejo", required=True)
    i.add_argument("--horas", type=float, default=24)
    i.add_argument("--nota", default="")
    i.set_defaults(fn=iniciar)
    r = sub.add_parser("revisar")
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=revisar)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
