#!/usr/bin/env python3
"""tools/migrar_secretos.py — saca las claves de los ficheros en claro y las mete en el Llavero.

POR QUÉ (31-jul-2026). `tests/test_normas_mecanizadas.py` cazó tres ficheros con claves en texto
plano en `tools/`. El alcance real, comprobado antes de asustarse:

  · Los tres están en `.gitignore` y **ninguno aparece en el historial de git** → no hay fuga
    al repo.
  · Los tres tienen permisos `-rw-------` → solo los lee el usuario de esta máquina.
  · `_secrets.get()` prueba el **Llavero primero** y el fichero es solo respaldo de
    retrocompatibilidad → en cuanto la clave esté en el Llavero, el fichero sobra.

O sea: no es una brecha, es una puerta que quedó abierta. Pero una clave en claro es una clave
en claro, y un backup, un `tar` o un `rsync` la sacan de la máquina sin que nadie lo note.

POR QUÉ ES UNA TOOL Y NO SE HIZO A MANO: el Llavero de macOS **no se deja tocar desde una sesión
no interactiva** («User interaction is not allowed», comprobado). Esto lo tiene que correr una
persona en su terminal, una vez. Aquí está todo hecho para que sea un comando y no un
procedimiento que se hace a medias.

NUNCA imprime el valor de una clave: solo su nombre y su longitud.

Uso:
  python3 tools/migrar_secretos.py             # revisar: qué hay, a dónde iría, qué sobra
  python3 tools/migrar_secretos.py migrar      # ENSAYO: dice qué haría, no toca nada
  python3 tools/migrar_secretos.py migrar --si # mete en el Llavero lo que falte
  python3 tools/migrar_secretos.py retirar --si# aparta los ficheros que YA sobran

RESULTADO REAL del 31-jul: no hacía falta migrar nada. Las tres claves ya estaban en el Llavero
desde antes y los ficheros eran respaldo muerto del 11-12 de julio. `retirar` es lo que hacía
falta, y esa NO necesita permiso de lectura del Llavero: basta con saber que el item EXISTE.
"""
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Los ficheros de claves están gitignorados: en un worktree NO existen. Si se resolviera desde
# `__file__`, esta tool diría «no queda ninguna clave en claro» desde cualquier rama — un falso
# verde en lo único que mira. Mismo criterio que `deuda.py`, `kpi_ned.py` y `audit_comites.py`.
CASA = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
TOOLS = os.path.join(CASA, "tools")

# fichero → {clave_en_el_json: servicio del Llavero}. Sale de leer las llamadas a
# `_secrets.get("btp-…", SECRETS, "…")` que hay en las tools.
MAPA = {
    ".telegram_secrets.json": {"token": "btp-telegram-token", "chat_id": "btp-telegram-chatid"},
    ".nvidia_secrets.json": {"api_key": "btp-nvidia-api"},
    ".openai_secrets.json": {"api_key": "btp-openai-api"},
    ".glm_secrets.json": {"api_key": "btp-glm-api"},
    ".grok_secrets.json": {"api_key": "btp-grok-api"},
    ".gemini_secrets.json": {"api_key": "btp-gemini-api"},
    ".perplexity_secrets.json": {"api_key": "btp-perplexity-api"},
    ".fugu_secrets.json": {"api_key": "btp-fugu-api"},
    ".umami_secrets.json": {"api_key": "btp-umami-api"},
    ".instagram_secrets.json": {"access_token": "btp-instagram-api"},
}
# Ficheros cuya clave NO la lee ninguna tool: no hay a dónde migrarla, sobra entera.
# `openrouter` quedó de un piloto de junio (`tools/pilotos/f3b-opencode/`) que no invoca nadie:
# ninguna tool menciona la palabra fuera de un comentario.
HUERFANOS = {".openrouter_secrets.json": "ninguna tool lo lee (piloto f3b-opencode, 25-jun)"}


# Códigos de salida de security(1), comprobados en esta máquina el 31-jul-26 preguntando por un
# servicio inventado y por uno que existe:
#   0  → lo encuentra y lo lee
#   44 → NO EXISTE (errSecItemNotFound)
#   36 → existe pero este proceso NO puede leerlo (errSecInteractionNotAllowed)
# La diferencia entre 36 y 44 lo es TODO: la primera versión de esto miraba el texto del stderr,
# que en 36 viene VACÍO, así que daba «falta en el Llavero» para claves que sí estaban. Habría
# hecho migrar (y pisar) tres claves que no hacía falta tocar. Tercera medición mentirosa del día.
_KC_NO_EXISTE = 44
_KC_SIN_PERMISO = 36


def _kc_leer(servicio):
    """(existe, legible). Distingue «no está» de «no puedo mirar» (Llavero bloqueado)."""
    r = subprocess.run(["security", "find-generic-password", "-s", servicio, "-w"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return True, True
    if r.returncode == _KC_SIN_PERMISO:
        return True, False          # está: si no estuviera, sería 44
    if r.returncode == _KC_NO_EXISTE:
        return False, True
    return None, False              # rc raro: no afirmar nada


def _kc_escribir(servicio, valor):
    r = subprocess.run(["security", "add-generic-password", "-U",
                        "-a", os.environ.get("USER", "polaris"), "-s", servicio, "-w", valor],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()


def _ficheros():
    return sorted(glob.glob(os.path.join(TOOLS, ".*_secrets.json")))


def revisar():
    fs = _ficheros()
    if not fs:
        print("✅ no queda ninguna clave en claro en tools/")
        return 0
    print("🔑 %d fichero(s) con claves en claro en tools/\n" % len(fs))
    for p in fs:
        nombre = os.path.basename(p)
        try:
            with open(p, encoding="utf-8") as f:
                datos = json.load(f)
        except Exception as e:
            print("  ⚠️  %-32s ilegible (%s)" % (nombre, e))
            continue
        perm = oct(os.stat(p).st_mode & 0o777)[2:]
        marca = "" if perm == "600" else "  ⚠️ permisos %s (deberían ser 600)" % perm
        if nombre in HUERFANOS:
            print("  🗑️  %-32s SOBRA: %s%s" % (nombre, HUERFANOS[nombre], marca))
            print("        claves: %s" % ", ".join("%s (%d chars)" % (k, len(str(v)))
                                                   for k, v in datos.items()))
            continue
        mapa = MAPA.get(nombre)
        if not mapa:
            print("  ❓ %-32s sin mapeo conocido — mírala a mano%s" % (nombre, marca))
            continue
        print("  📄 %s%s" % (nombre, marca))
        for clave, valor in datos.items():
            servicio = mapa.get(clave)
            if not servicio:
                print("      · %-14s (%d chars) → sin servicio conocido" % (clave, len(str(valor))))
                continue
            esta, legible = _kc_leer(servicio)
            if esta is None:
                estado = "no puedo saberlo (rc inesperado de security)"
            elif esta and legible:
                estado = "YA está en el Llavero → el fichero SOBRA"
            elif esta:
                estado = "YA está en el Llavero (no lo leo, pero existe) → el fichero SOBRA"
            else:
                estado = "FALTA en el Llavero → hay que migrarla"
            print("      · %-14s (%d chars) → %-22s  %s"
                  % (clave, len(str(valor)), servicio, estado))
    print("\nMigrar: python3 tools/migrar_secretos.py migrar --si"
          "\n(el Llavero solo se deja tocar desde TU terminal, no desde una sesión automática)")
    return 0


def migrar(escribir=False):
    fs = _ficheros()
    if not fs:
        print("✅ no queda nada que migrar")
        return 0
    _, legible = _kc_leer("btp-telegram-token")
    if not legible and escribir:
        print("❌ el Llavero está bloqueado para este proceso («User interaction is not "
              "allowed»).\n   Corre esto en TU Terminal, no desde una sesión automática.",
              file=sys.stderr)
        return 2

    hechos, pendientes = [], []
    for p in fs:
        nombre = os.path.basename(p)
        if nombre in HUERFANOS:
            pendientes.append("%s → SOBRA (%s): revísalo y bórralo tú, y revoca la clave en el "
                              "proveedor si sigue viva" % (nombre, HUERFANOS[nombre]))
            continue
        mapa = MAPA.get(nombre)
        if not mapa:
            pendientes.append("%s → sin mapeo conocido, no lo toco" % nombre)
            continue
        try:
            with open(p, encoding="utf-8") as f:
                datos = json.load(f)
        except Exception as e:
            pendientes.append("%s → ilegible (%s)" % (nombre, e))
            continue

        todas = True
        for clave, valor in datos.items():
            servicio = mapa.get(clave)
            if not servicio:
                todas = False
                pendientes.append("%s[%s] → sin servicio conocido" % (nombre, clave))
                continue
            if not escribir:
                print("  ENSAYO: %s[%s] → %s" % (nombre, clave, servicio))
                continue
            ok, err = _kc_escribir(servicio, str(valor))
            if not ok:
                todas = False
                pendientes.append("%s[%s] → NO se pudo guardar en %s (%s)"
                                  % (nombre, clave, servicio, err))
                continue
            # Verificar el EFECTO, no que el comando corrió: si no se relee, el fichero se queda.
            esta, _ = _kc_leer(servicio)
            if not esta:
                todas = False
                pendientes.append("%s[%s] → se guardó pero no se relee en %s: dejo el fichero"
                                  % (nombre, clave, servicio))
                continue
            hechos.append("%s[%s] → %s" % (nombre, clave, servicio))
        # El fichero solo se aparta si TODAS sus claves están verificadas en el Llavero. Y se
        # APARTA, no se borra: si algo se rompe, se vuelve moviéndolo de vuelta.
        if escribir and todas:
            bak = p + ".migrado.bak"
            os.rename(p, bak)
            hechos.append("%s → apartado a %s (bórralo tú cuando compruebes que todo va)"
                          % (nombre, os.path.basename(bak)))

    for h in hechos:
        print("  ✓ %s" % h)
    for x in pendientes:
        print("  ⏸️  %s" % x)
    if not escribir:
        print("\n(ensayo: no se tocó nada. Repite con --si)")
    return 1 if pendientes else 0


def retirar(escribir=False):
    """Aparta los ficheros cuyas claves YA están en el Llavero. No necesita permiso de lectura.

    Esta es la operación que resultó hacer falta (31-jul-26): las tres claves ya estaban
    guardadas desde antes, y los ficheros eran respaldo muerto del 11-12 de julio. Como
    `_secrets.get()` mira el Llavero PRIMERO, esos ficheros no se estaban usando ya: retirarlos
    no cambia el comportamiento, solo quita el texto plano del disco.

    Se APARTAN a `.sobra.bak`, no se borran: si algo se rompiera, se vuelve moviéndolos.
    Un fichero con una sola clave que NO esté en el Llavero no se toca, porque ahí sí haría falta.
    """
    fs = _ficheros()
    if not fs:
        print("✅ no queda ninguna clave en claro en tools/")
        return 0
    apartados, intactos = [], []
    for p in fs:
        nombre = os.path.basename(p)
        if nombre in HUERFANOS:
            razon = "no lo lee ninguna tool"
        else:
            mapa = MAPA.get(nombre)
            if not mapa:
                intactos.append("%s → sin mapeo conocido, no lo toco" % nombre)
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    datos = json.load(f)
            except Exception as e:
                intactos.append("%s → ilegible (%s), no lo toco" % (nombre, e))
                continue
            faltan = [c for c in datos
                      if not mapa.get(c) or _kc_leer(mapa[c])[0] is not True]
            if faltan:
                intactos.append("%s → %s NO está(n) en el Llavero: el fichero AÚN hace falta"
                                % (nombre, ", ".join(faltan)))
                continue
            razon = "todas sus claves están en el Llavero"
        if not escribir:
            print("  ENSAYO: apartaría %s (%s)" % (nombre, razon))
            continue
        os.rename(p, p + ".sobra.bak")
        apartados.append("%s → %s.sobra.bak (%s)" % (nombre, nombre, razon))
    for a in apartados:
        print("  ✓ %s" % a)
    for i in intactos:
        print("  ⏸️  %s" % i)
    if not escribir:
        print("\n(ensayo: no se tocó nada. Repite con --si)")
    elif apartados:
        print("\nComprueba que todo sigue yendo y borra los .sobra.bak cuando estés tranquila.")
    return 0


def main(argv):
    cmd = argv[0] if argv else "revisar"
    if cmd == "revisar":
        return revisar()
    if cmd == "migrar":
        return migrar(escribir="--si" in argv)
    if cmd == "retirar":
        return retirar(escribir="--si" in argv)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
