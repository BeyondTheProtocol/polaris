#!/usr/bin/env python3
"""tools/subir_historial_drive.py — pone al día el Drive del historial con lo que hay en local.

POR QUÉ EXISTE (11-sep-2026). {{TITULAR}}: *«A este drive solo subo yo cosas, tienes todo mi
consentimiento a tener libertad de subida»* ([[feedback-drive-historial-subida-libre]]). El gate
de `drive.py` pide teclear SUBIR-CLINICO en un TTY por cada fichero: correcto para cualquier
subida, pero poner al día el historial eran 649 confirmaciones y el Drive se quedaba atrás. Esta
es la vía ACOTADA para ese único caso, y es la única que cubre la regla de permisos que {{TITULAR}}
añadió a mano en `.claude/settings.local.json`.

Qué la hace estrecha (y lo que prueba `tests/test_subir_historial_drive.py`):
  · ORIGEN: solo ficheros que cuelgan de las carpetas del historial local (`historial.RAIZ`).
  · DESTINO: solo las carpetas fijas de `historial.CARPETAS_DRIVE`. No acepta un id de carpeta
    por argumento: no se puede apuntar a otro sitio.
  · IDENTIDAD: cada informe tiene que llevar su fecha de nacimiento, DNI o número de historia
    ([[feedback-verificar-identidad-paciente-en-informe]]). Las transcripciones heredan de su PDF.
    Lo que no se puede verificar NO sube, salvo `--sin-verificar`, que exige que {{TITULAR}} lo haya
    confirmado y lo lista antes.
  · Sin duplicar (nombre NFC en la carpeta destino) y cotejando el sha256 que devuelve Drive.
  · DRY-RUN por defecto; HALT manda; log en `.claude/logs/subida-drive-<fecha>.json`.

Uso:
  python3 tools/subir_historial_drive.py                  # qué subiría y qué retiene
  python3 tools/subir_historial_drive.py --apply          # sube lo verificado
  python3 tools/subir_historial_drive.py --apply --sin-verificar   # + lo que {{TITULAR}} confirmó
"""
import hashlib
import json
import io
import mimetypes
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import historial as H   # noqa: E402

# Marcas de que el informe es de la titular: su fecha de nacimiento en los formatos que se han
# visto en su archivo (ES, CA, DE y el americano de Guardant/MD Anderson), su DNI, su NHC y su
# nº de paciente del hospital.
#
# ESTO NO PUEDE ESTAR VERSIONADO. Hasta el 16-sep-2026 vivía aquí en claro: el detector que
# impide subir la ficha de otra persona ERA la ficha de esta. Vive en `identidad.local.json`
# (gitignored, junto a este fichero).
#
# FAIL-CLOSED, y aquí sale natural: sin overlay no hay con qué reconocer un informe, `es_suyo`
# devuelve False y no sube nada. Es exactamente la regla que ya declara el docstring —
# «lo que no se puede verificar NO sube».
IDENTIDAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "identidad.local.json")


def _identidad():
    try:
        with io.open(IDENTIDAD, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _compilar(clave):
    """Un patrón que NUNCA casa si el overlay falta: `(?!)` falla siempre."""
    alternativas = [a for a in _identidad().get(clave, []) if str(a).strip()]
    return re.compile("|".join(alternativas) if alternativas else r"(?!)", re.I)


DOB = _compilar("dob_patrones")
IDS = _compilar("ids")


def hay_identidad():
    """¿Existe el overlay? Sin él, este tool no puede verificar nada y no sube."""
    return bool(_identidad().get("dob_patrones") or _identidad().get("ids"))
SIDECAR = re.compile(r"(_ES\.md|_EN\.md|\.ocr\.txt)$")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _nfc(s):
    return unicodedata.normalize("NFC", s)


def es_suyo(texto):
    return bool(DOB.search(texto or "") or IDS.search(texto or ""))


def _texto(ruta):
    if ruta.lower().endswith(".pdf"):
        t = subprocess.run(["pdftotext", "-l", "4", ruta, "-"], capture_output=True,
                           text=True, errors="ignore").stdout
        ocr = ruta[:-4] + ".ocr.txt"
        if len(t.strip()) < 120 and os.path.exists(ocr):
            t += open(ocr, errors="ignore").read()
        return t
    if ruta.endswith((".md", ".txt", ".csv")):
        return open(ruta, errors="ignore").read()
    return ""


def clasificar(locales, en_drive, texto=_texto):
    """locales: [(ruta, carpeta)]; en_drive: {carpeta: set(nombres)}.
    Devuelve (subir, retener) como listas de (ruta, carpeta). Función pura salvo `texto`."""
    pend = [(r, c) for r, c in locales
            if _nfc(os.path.basename(r)) not in {_nfc(n) for n in en_drive.get(c, ())}]
    suyo = {}
    for r, c in locales:                       # el PDF manda sobre sus transcripciones
        if r.lower().endswith(".pdf"):
            suyo[r[:-4]] = es_suyo(texto(r))
    subir, retener = [], []
    for r, c in pend:
        ok = es_suyo(texto(r))
        if not ok and not r.lower().endswith(".pdf"):
            ok = suyo.get(SIDECAR.sub("", r), False)
        (subir if ok else retener).append((r, c))
    return subir, retener


def _locales():
    out = []
    for carpeta in H.CARPETAS_DRIVE:
        d = os.path.join(H.RAIZ, carpeta)
        if os.path.isdir(d):
            out += [(os.path.join(d, f), carpeta) for f in sorted(os.listdir(d))
                    if not f.startswith(".") and os.path.isfile(os.path.join(d, f))]
    return out


def destino(carpeta):
    """El id de Drive SOLO sale de la tabla fija. Una carpeta fuera de ella es un error."""
    if carpeta not in H.CARPETAS_DRIVE:
        raise ValueError("carpeta fuera del historial: %r" % carpeta)
    return H.CARPETAS_DRIVE[carpeta]


def main(argv):
    apply = "--apply" in argv
    sin_verificar = "--sin-verificar" in argv
    for halt in (os.path.expanduser("~/.btp.HALT"), os.path.join(REPO, ".HALT")):
        if os.path.exists(halt):
            print("🛑 HALT activo (%s): no se sube nada." % halt)
            return 99
    import drive                                # tras el HALT: sin red si está parado
    en_drive = {c: {x["name"] for x in drive.list_folder(fid)}
                for c, fid in H.CARPETAS_DRIVE.items()}
    subir, retener = clasificar(_locales(), en_drive)
    print("Pendientes: %d verificados · %d sin verificar" % (len(subir), len(retener)))
    for r, c in retener:
        print("   retenido  %s / %s" % (c[:3], os.path.basename(r)[:90]))
    if sin_verificar:
        subir, retener = subir + retener, []
    if not apply:
        print("\nDRY-RUN. Repite con --apply%s." % ("" if sin_verificar else
                                                   " (y --sin-verificar si {{TITULAR}} los confirmó)"))
        return 0
    from googleapiclient.http import MediaFileUpload
    svc = drive._service_write()
    log, fallos = [], 0
    for ruta, carpeta in subir:
        nombre = os.path.basename(ruta)
        mime = (mimetypes.guess_type(nombre)[0] or
                ("text/markdown" if nombre.endswith(".md") else "application/octet-stream"))
        estado, fid_nuevo = "ERROR", ""
        for intento in range(3):
            try:
                r = svc.files().create(
                    body={"name": nombre, "parents": [destino(carpeta)]},
                    media_body=MediaFileUpload(ruta, mimetype=mime, resumable=True),
                    fields="id,sha256Checksum").execute()
                sha = hashlib.sha256(open(ruta, "rb").read()).hexdigest()
                estado = "OK" if r.get("sha256Checksum") == sha else "SHA_DISTINTO"
                fid_nuevo = r["id"]
                break
            except Exception as e:
                estado = "ERROR %r" % e
                time.sleep(3 * (intento + 1))
        fallos += estado != "OK"
        log.append({"fichero": nombre, "carpeta": carpeta, "estado": estado, "id": fid_nuevo})
        print("   %-12s %s" % (estado[:12], nombre[:90]))
    salida = os.path.join(REPO, ".claude", "logs",
                          "subida-drive-%s.json" % datetime.now().strftime("%Y-%m-%d-%H%M"))
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    json.dump(log, open(salida, "w"), ensure_ascii=False, indent=1)
    print("\nsubidos %d · fallos %d · log %s" % (len(log) - fallos, fallos, salida))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
