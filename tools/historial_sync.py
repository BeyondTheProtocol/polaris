#!/usr/bin/env python3
"""tools/historial_sync.py — que el historial clínico esté al día SOLO, sin que nadie lo empuje.

EL PROBLEMA QUE RESUELVE. El 25-ago-2026 el archivo estaba impecable —404 documentos ordenados,
indexados, con su índice generado— y lo estaba porque alguien lo había hecho a mano esa tarde.
No había nada que lo mantuviera así: si {{TITULAR}} colgaba un informe en Drive, o si el hospital se
lo mandaba por correo, el documento no llegaba al archivo ni, por tanto, al conocimiento de
Polaris. La regla que ella dio el 23-ago era *«que Polaris tenga SIEMPRE conocimiento de todo mi
historial a la última»*, y el «siempre» no lo cumplía nadie.

LAS CUATRO PATAS, en orden:
  1. **Drive → local.** Drive es la fuente de verdad ([[project-historial-drive-es-la-verdad]]).
     Se lista la carpeta «Historial médico», se compara contra lo archivado y se baja lo que falte.
  2. **Correo → local.** `adjuntos_clinicos.py` baja los informes que llegan como adjunto.
  3. **Ingerir.** Lo bajado pasa por `historial.ingerir`: clasifica, fecha, nombra y deduplica.
  4. **Reindexar.** `kb.py index`, y solo si de verdad entró algo (reindexar cuesta minutos).

Y avisa, en llano, SOLO cuando entra documento nuevo. Un daemon que dice «no ha pasado nada»
cada tres horas se convierte en ruido y se deja de leer.

🔒 MURO. No sube NADA: es de una sola dirección, de Drive hacia aquí. La subida sigue siendo un
   acto aparte y explícito de {{TITULAR}} (`historial.py subir`), y ahora mismo está parada por el
   código rojo del 6-ago (las carpetas de destino en Drive están compartidas con siete personas).

⛔ HALT. `drive.py` se bloquea con el kill-switch, y eso está bien: es el muro funcionando. Esta
   rutina NO lo rodea. Lo que hace es DEGRADAR con honestidad: se salta la pata de Drive, hace
   las otras tres y lo dice en el informe. El día que se levante el HALT, la pata 1 arranca sola
   sin tocar una línea.

Disciplina, como el resto: DRY-RUN por defecto; sin `--apply` no escribe un byte.

Uso:
  python3 tools/historial_sync.py estado             # qué hay, qué falta, si el índice va al día
  python3 tools/historial_sync.py sync [--apply]     # las cuatro patas
  python3 tools/historial_sync.py sync --apply --con-correo   # incluye la pata del correo
"""
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
import historial as H  # noqa: E402

REPO = H.REPO
ESTADO = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
DIARIO = os.path.join(ESTADO, "historial_sync.json")
# Lo que se baja aterriza aquí antes de clasificarse. Va DENTRO de la zona clínica: un informe
# suyo no se queda en /tmp ni un minuto.
BANDEJA = os.path.join(os.path.dirname(H.RAIZ), "_bandeja_sync")
# La carpeta «Historial médico» de Drive. El id se fija porque dos carpetas pueden llamarse
# igual y el id no miente (mismo criterio que `CARPETAS_DRIVE` en historial.py).
DRIVE_HISTORIAL = "12OEQm7m1hu39cIeJk9iFhv5qCaGnlCMZ"
VENV_PY = os.path.join(REPO, ".venv", "bin", "python3")

# Lo que NO es un informe médico y por tanto no baja: el índice viejo que sustituyó el CSV
# generado, y los resúmenes que escribimos nosotros.
NO_BAJAR = ("00_INDICE_historial_clinico.xlsx", "01_CASE_SUMMARY.md")


# ─────────────────────────── diario ───────────────────────────

def _leer_diario():
    try:
        with open(DIARIO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ultima": None, "vistos_drive": [], "corridas": []}


def _escribir_diario(d):
    os.makedirs(os.path.dirname(DIARIO), exist_ok=True)
    tmp = DIARIO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DIARIO)


# ─────────────────────────── estado ───────────────────────────

def _archivados():
    """sha256 y nombres de lo que ya está en el historial."""
    shas, nombres = set(), set()
    for _clave, carpeta in H.CARPETAS:
        d = os.path.join(H.RAIZ, carpeta)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith(".pdf"):
                shas.add(H.sha256(os.path.join(d, f)))
                nombres.add(f)
    return shas, nombres


def _halt_activo():
    return any(os.path.exists(p) for p in
               (os.path.expanduser("~/.btp.HALT"), os.path.join(REPO, ".HALT")))


def _indice_al_dia():
    """(al_dia, mtime_indice, mtime_doc_mas_nuevo). El índice del RAG vive en casa base."""
    db = os.path.join(REPO, "00_FUENTE-DE-VERDAD", ".kb_index.db")
    docs = []
    for _clave, carpeta in H.CARPETAS:
        d = os.path.join(H.RAIZ, carpeta)
        if os.path.isdir(d):
            docs += [os.path.join(d, f) for f in os.listdir(d) if not f.startswith(".")]
    if not docs or not os.path.exists(db):
        return True, None, None
    nuevo = max(os.path.getmtime(p) for p in docs)
    idx = os.path.getmtime(db)
    return idx >= nuevo - 60, idx, nuevo


def estado():
    shas, nombres = _archivados()
    al_dia, idx, nuevo = _indice_al_dia()
    return {
        "documentos": len(shas),
        "halt": _halt_activo(),
        "indice_al_dia": al_dia,
        "indice_ts": idx,
        "doc_mas_nuevo_ts": nuevo,
        "ultima_sync": _leer_diario().get("ultima"),
    }


# ─────────────────────────── pata 1 · Drive ───────────────────────────

def _palabras(texto):
    """Palabras significativas, sin acentos ni puntuación."""
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return {w for w in re.split(r"[^a-z0-9]+", t) if len(w) > 2}


def _fecha_y_desc(nombre):
    """(fecha, palabras) de un nombre de fichero, sea del esquema viejo de Drive
    (`AAAA-MM-DD - Categoría - Descripción`) o del nuevo (`AAAA-MM-DD - CENTRO - Descripción`)."""
    raiz = os.path.splitext(nombre)[0]
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s*-\s*[^-]{1,22}?\s*-\s*(.+)$", raiz)
    if m:
        return m.group(1), _palabras(m.group(2))
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", raiz)
    return (m.group(1) if m else ""), _palabras(raiz)


def _indice_archivado():
    """fecha → [palabras de la descripción] de todo lo que ya está en el historial."""
    idx = {}
    for _clave, carpeta in H.CARPETAS:
        d = os.path.join(H.RAIZ, carpeta)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.lower().endswith(".pdf"):
                continue
            fecha, pal = _fecha_y_desc(f)
            idx.setdefault(fecha, []).append(pal)
    return idx


# Umbral de parecido. Bajo a propósito: la descripción se recorta a 120 caracteres al archivar,
# así que el nombre de Drive casi siempre tiene MÁS palabras que su pareja ya archivada.
PARECIDO_MIN = 0.34


def ya_archivado(nombre, idx):
    """¿Este fichero de Drive ya está en el historial, aunque se llame distinto?

    Comparar por nombre a secas no vale: Drive conserva la taxonomía vieja
    (`2024-01-16 - Imagen - Ecografía de mama`) y el archivo usa la nueva
    (`2024-01-16 - HMM - ECOGRAFÍA DE MAMA`). Sin esto la rutina daba 288 pendientes de 292,
    es decir, se ofrecía a bajar entero un archivo que ya estaba entero.
    """
    fecha, pal = _fecha_y_desc(nombre)
    if not fecha:
        return False                       # sin fecha no se puede afirmar; que lo mire un humano
    for otra in idx.get(fecha, []):
        if pal and otra and len(pal & otra) / float(len(pal | otra)) >= PARECIDO_MIN:
            return True
    return False


def pendientes_drive():
    """Qué hay en la carpeta de Drive que NO esté ya en el historial."""
    import drive
    idx = _indice_archivado()
    vistos = set(_leer_diario().get("vistos_drive", []))
    fuera = []
    for f in drive.list_files(folder_id=DRIVE_HISTORIAL, max_results=500):
        if f.get("mimeType", "").endswith(".folder"):
            continue
        n = f["name"]
        if n in NO_BAJAR or n in vistos or ya_archivado(n, idx):
            continue
        fuera.append(f)
    return fuera


def bajar_de_drive(apply=False):
    # drive.py llama sys.exit() en sus funciones de auth/lectura cuando faltan las
    # dependencias de Google (el hint de "pip install ..."), no solo en su CLI. Esa
    # decisión es de drive.py, pero el SystemExit que produce viaja como cualquier
    # excepción y esta pata promete degradar con honestidad, no morirse en silencio:
    # por eso el except de aquí abajo pesca también SystemExit, no solo Exception
    # (SystemExit cuelga de BaseException, "except Exception" no lo atrapa).
    if _halt_activo():
        return {"saltado": "HALT activo — drive.py bloqueado; las otras patas siguen", "bajados": []}
    try:
        import drive
    except (Exception, SystemExit) as e:                    # noqa: BLE001
        return {"saltado": "drive.py no importable (%r)" % e, "bajados": []}
    try:
        faltan = pendientes_drive()
    except (Exception, SystemExit) as e:                    # noqa: BLE001
        return {"saltado": "no pude listar Drive (%r)" % e, "bajados": []}
    bajados = []
    for f in faltan:
        destino = os.path.join(BANDEJA, H._seguro(f["name"]))
        if apply:
            try:
                drive.download(f["id"], destino)
            except Exception as e:                          # noqa: BLE001
                bajados.append({"nombre": f["name"], "error": repr(e)})
                continue
        bajados.append({"nombre": f["name"], "id": f["id"], "destino": destino})
    return {"saltado": None, "bajados": bajados}


# ─────────────────────────── pata 2 · correo ───────────────────────────

def bajar_de_correo(apply=False):
    """`adjuntos_clinicos.py` ya sabe hacer esto; aquí solo se le llama y se recoge el resultado."""
    script = os.path.join(_AQUI, "adjuntos_clinicos.py")
    if not os.path.exists(script):
        return {"saltado": "adjuntos_clinicos.py no está en esta copia", "n": 0}
    cmd = [sys.executable, script, "--dir", BANDEJA] + (["--apply"] if apply else [])
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        return {"saltado": None, "n": None, "salida": r.stdout.decode("utf-8", "replace")[-400:]}
    except Exception as e:                                  # noqa: BLE001
        return {"saltado": "falló (%r)" % e, "n": 0}


# ─────────────────────────── patas 3 y 4 ───────────────────────────

def ingerir_bandeja(apply=False):
    if not os.path.isdir(BANDEJA):
        return {"documentos": [], "escritos": 0, "dudosos": [], "vistos": 0,
                "duplicados": [], "no_documento": [], "ocr": 0}
    return H.ingerir([BANDEJA], apply=apply)


def reindexar():
    """Reindexa con el intérprete del .venv A PROPÓSITO: el del sistema no tiene pypdf y kb.py
    se planta (bien) antes que dejar el índice sin los PDFs."""
    py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    r = subprocess.run([py, os.path.join(_AQUI, "kb.py"), "index"],
                       capture_output=True, timeout=3600)
    return r.returncode == 0, r.stdout.decode("utf-8", "replace")[-300:]


# ─────────────────────────── orquestación ───────────────────────────

def sync(apply=False, con_correo=False):
    os.makedirs(BANDEJA, exist_ok=True) if apply else None
    res = {"drive": bajar_de_drive(apply), "correo": None, "ingerido": None,
           "reindexado": None, "aviso": None}
    if con_correo:
        res["correo"] = bajar_de_correo(apply)
    res["ingerido"] = ingerir_bandeja(apply)

    nuevos = res["ingerido"]["escritos"] if apply else len(res["ingerido"]["documentos"])
    if apply and nuevos:
        # El índice solo se rehace si de verdad entró algo: cuesta minutos y toca un fichero
        # de 400 MB que otras sesiones consultan.
        ok, salida_txt = reindexar()
        res["reindexado"] = {"ok": ok, "salida": salida_txt}
        # La bandeja se vacía: lo ingerido ya vive en su carpeta con su nombre bueno.
        for f in os.listdir(BANDEJA):
            try:
                os.remove(os.path.join(BANDEJA, f))
            except OSError:
                pass
        res["aviso"] = avisar(res)

    if apply:
        d = _leer_diario()
        d["ultima"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        d["vistos_drive"] = sorted(set(d.get("vistos_drive", []))
                                   | {b["nombre"] for b in res["drive"]["bajados"]
                                      if not b.get("error")})
        d["corridas"] = (d.get("corridas", []) + [{"cuando": d["ultima"], "nuevos": nuevos}])[-40:]
        _escribir_diario(d)
    return res


def avisar(res):
    """Solo cuando entra documento nuevo, y en llano. Sin novedad no se dice nada."""
    docs = res["ingerido"]["documentos"]
    if not docs:
        return None
    lineas = ["📄 Han entrado %d documento(s) nuevo(s) en tu historial:" % len(docs)]
    for d in docs[:6]:
        lineas.append("   · %s" % d["fichero"][:78])
    if len(docs) > 6:
        lineas.append("   · … y %d más" % (len(docs) - 6))
    dudosos = res["ingerido"].get("dudosos") or []
    if dudosos:
        lineas.append("")
        lineas.append("⚠️ %d sin fecha o sin centro: los he archivado igual, pero convendría "
                      "mirarlos." % len(dudosos))
    if res["drive"].get("saltado"):
        lineas.append("")
        lineas.append("(Drive no consultado: %s)" % res["drive"]["saltado"])
    texto = "\n".join(lineas)
    try:
        import salida
        r = salida.report_to_titular(texto, voz="sobria")
        return {"enviado": bool(r.get("delivered")), "motivo": r.get("reason", "")}
    except Exception as e:                                  # noqa: BLE001
        return {"enviado": False, "motivo": repr(e), "texto": texto}


# ─────────────────────────── CLI ───────────────────────────

def main(argv):
    cmd = (argv[0] if argv else "estado").lower()
    apply = "--apply" in argv

    if cmd == "estado":
        e = estado()
        print("documentos en el historial: %d" % e["documentos"])
        print("índice del RAG al día:      %s" % ("sí" if e["indice_al_dia"] else "NO"))
        if e["indice_ts"]:
            print("  índice:          %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(e["indice_ts"])))
            print("  doc más nuevo:   %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(e["doc_mas_nuevo_ts"])))
        print("última sincronización:      %s" % (e["ultima_sync"] or "nunca"))
        if e["halt"]:
            print("\n⛔ HALT activo: la pata de Drive no corre. Las otras tres sí.")
        else:
            try:
                faltan = pendientes_drive()
                print("\npendientes de bajar de Drive: %d" % len(faltan))
                for f in faltan[:10]:
                    print("   · %s" % f["name"][:78])
            except (Exception, SystemExit) as e2:           # noqa: BLE001 — ver bajar_de_drive()
                print("\nno pude consultar Drive: %r" % e2)
        return 0

    if cmd == "sync":
        r = sync(apply=apply, con_correo="--con-correo" in argv)
        if r["drive"]["saltado"]:
            print("Drive: saltado — %s" % r["drive"]["saltado"])
        else:
            print("Drive: %d fichero(s) por bajar" % len(r["drive"]["bajados"]))
            for b in r["drive"]["bajados"][:10]:
                print("   %s %s" % ("✗" if b.get("error") else "·", b["nombre"][:74]))
        if r["correo"]:
            print("Correo: %s" % (r["correo"]["saltado"] or "revisado"))
        ing = r["ingerido"]
        print("Ingerir: %d PDF mirados · %d nuevos · %d duplicados"
              % (ing["vistos"], len(ing["documentos"]), len(ing["duplicados"])))
        for d in ing["documentos"][:10]:
            print("   → %s / %s" % (d["carpeta"][:5], d["fichero"][:70]))
        if ing.get("dudosos"):
            print("⚠️  %d dudosos" % len(ing["dudosos"]))
        if r["reindexado"]:
            print("Reindexado: %s" % ("ok" if r["reindexado"]["ok"] else "FALLÓ"))
        if r["aviso"]:
            print("Aviso a {{TITULAR}}: %s" % ("enviado" if r["aviso"]["enviado"] else r["aviso"]["motivo"]))
        if not apply:
            print("\nDRY-RUN: no se ha escrito un byte. Repite con --apply.")
        return 0

    print(__doc__.split("Uso:")[-1].strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
