#!/usr/bin/env python3
"""tools/buzon_drive.py — buzón de subidas a Google Drive (17-oct-26).

Las sesiones de Claude Code no leen el Llavero (rc=36), así que `drive.py upload` falla y
{{TITULAR}} tenía que pegar comandos en su Terminal. Aquí la sesión ENCOLA la subida y el bot de
Telegram (launchd, con el Llavero abierto) la hace en su siguiente vuelta y le manda el enlace.

  python3 tools/buzon_drive.py subir <ruta> [FOLDER_ID]   # encola (no sube nada)
  python3 tools/buzon_drive.py estado                      # pendientes / subidos / fallidos

Frenos (además de los de drive.py, que siguen mandando: HALT, zona clínica solo con TTY humano,
borde): rutas bajo la fuente de verdad o ~/tmp_subida, ≤ 200 MB, una subida por vuelta y a los 3
fallos a fallidos/. La zona clínica NUNCA sale por aquí: drive.py exige TTY y el bot no tiene.
"""
import hashlib
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import salida  # noqa: E402  (HALT, auditoría, aviso a {{TITULAR}})

RAICES = (os.path.expanduser("~/claudecode/00_FUENTE-DE-VERDAD"),
          os.path.join(REPO, "00_FUENTE-DE-VERDAD"),
          os.path.expanduser("~/tmp_subida"))
MAX_BYTES = 200 * 1024 * 1024
MAX_INTENTOS = 3


def _dir(sub="pendientes"):
    return os.path.join(salida.STATE, "envios_drive", sub)


def _permitido(path):
    real = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isfile(real):
        return False, "no existe el fichero"
    raices = [os.path.realpath(r) for r in RAICES]
    if not any(real == r or real.startswith(r + os.sep) for r in raices):
        return False, "ruta fuera del buzón permitido"
    if os.path.getsize(real) > MAX_BYTES:
        return False, "pasa de 200 MB"
    return True, real


def encolar(path, folder_id=None):
    """Deja la subida en el buzón. No toca Drive."""
    ok, info = _permitido(path)
    if not ok:
        salida._audit("drive", salida.REPORT, None, "bloqueado", "buzón drive: %s" % info, 0)
        return {"encolado": False, "reason": info}
    d = _dir()
    os.makedirs(d, exist_ok=True)
    nombre = "%s-%s-%s.json" % (time.strftime("%Y%m%dT%H%M%S"),
                                hashlib.sha1(info.encode()).hexdigest()[:8], os.urandom(3).hex())
    tmp = os.path.join(d, "." + nombre)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ruta": info, "carpeta": folder_id, "ts": salida._now_iso(), "intentos": 0},
                  f, ensure_ascii=False)
    os.replace(tmp, os.path.join(d, nombre))
    salida._audit("drive", salida.REPORT, None, "encolado", "buzón drive: %s" % os.path.basename(info),
                  os.path.getsize(info))
    return {"encolado": True, "fichero": os.path.basename(info)}


def _subir(ruta, carpeta):
    import drive  # perezoso: las librerías de Google solo se cargan si hay algo que subir
    return drive.upload_file(ruta, carpeta)


def vaciar(subir=None, avisar=None):
    """Lo llama el bot en cada vuelta: UNA subida como mucho (no bloquear el long-poll)."""
    subir = subir or _subir
    avisar = avisar or (lambda texto: salida.report_to_titular(texto, categoria="respuesta"))
    d = _dir()
    if salida.halted() or not os.path.isdir(d):
        return 0
    pend = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))
    if not pend:
        return 0
    nombre = pend[0]
    p = os.path.join(d, nombre)
    try:
        with open(p, encoding="utf-8") as f:
            item = json.load(f)
    except (OSError, ValueError):
        os.makedirs(_dir("fallidos"), exist_ok=True)
        os.replace(p, os.path.join(_dir("fallidos"), nombre))
        return 0
    ok, info = _permitido(item.get("ruta", ""))
    error = info if not ok else ""
    if ok:
        try:
            meta = subir(info, item.get("carpeta"))
            item["drive"] = {k: meta.get(k) for k in ("id", "name", "webViewLink")}
            os.makedirs(_dir("subidos"), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(item, f, ensure_ascii=False)
            os.replace(p, os.path.join(_dir("subidos"), nombre))
            salida._audit("drive", salida.REPORT, None, "subido", "buzón drive: %s" % os.path.basename(info),
                          os.path.getsize(info))
            try:
                avisar("Subido a tu Drive: %s\n%s" % (meta.get("name", ""), meta.get("webViewLink", "")))
            except Exception:
                pass
            return 1
        except Exception as e:  # noqa: BLE001 — cualquier fallo de Drive cuenta como intento
            error = repr(e)[:300]
    item["intentos"] = int(item.get("intentos", 0)) + 1
    item["ultimo_error"] = error
    with open(p, "w", encoding="utf-8") as f:
        json.dump(item, f, ensure_ascii=False)
    if not ok or item["intentos"] >= MAX_INTENTOS:
        os.makedirs(_dir("fallidos"), exist_ok=True)
        os.replace(p, os.path.join(_dir("fallidos"), nombre))
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] == "subir" and len(argv) <= 3:
        r = encolar(argv[1], argv[2] if len(argv) > 2 else None)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("encolado") else 1
    if argv == ["estado"]:
        for sub in ("pendientes", "subidos", "fallidos"):
            d = _dir(sub)
            print("%s: %d" % (sub, len([n for n in os.listdir(d) if n.endswith(".json")]) if os.path.isdir(d) else 0))
        return 0
    print("uso: buzon_drive.py subir <ruta> [FOLDER_ID] | estado")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
