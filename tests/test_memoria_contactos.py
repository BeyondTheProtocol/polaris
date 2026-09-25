#!/usr/bin/env python3
"""test_memoria_contactos.py — F4.2 ficha viva de contacto. Va a la FUENTE (dossier), deduce el CANAL
del nombre del fichero, agrega + cita la fuente, marca PII y cachea en local. Aislado en tmp."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres")
import glob
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="memcon_")
_FUENTE = os.path.join(_TMP, "FUENTE")
os.makedirs(os.path.join(_FUENTE, "Seguimiento-Contactos"), exist_ok=True)
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_FUENTE_DIR"] = _FUENTE
sys.path.insert(0, os.path.join(ROOT, "tools"))
import memoria_contactos as mc   # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def dossier(fname, contenido):
    open(os.path.join(_FUENTE, "Seguimiento-Contactos", fname), "w").write(contenido)


def main():
    dossier("{{CONTACTO}}-{{CONTACTO}}-X-DM-2026-06-18.md",
            "# Chat X con Dr. {{CONTACTO}} {{CONTACTO}}\nBiopsia 8-jul confirmada. Cores para neoantígenos.")
    dossier("{{CONTACTO}}-{{CONTACTO}}-1a1-digest.md", "{{CONTACTO}} ({{CENTRO}}). Gate de neoantígenos.")

    f = mc.ficha("{{CONTACTO}}", con_whatsapp=False)
    ok("DM de X" in f, "deduce el CANAL del nombre del dossier ({{CONTACTO}} = DM de X)")
    ok("{{CONTACTO}}-{{CONTACTO}}-X-DM" in f, "cita el dossier (la fuente)")
    ok("Biopsia 8-jul" in f, "incluye el contenido curado")
    ok("solo cerebro de confianza" in f, "marca PII (muro)")

    cache = glob.glob(os.path.join(_FUENTE, "_PRIVADO_NUCLEO", "asistente", "contactos", "*.md"))
    ok(len(cache) == 1, "cachea la ficha en LOCAL (gitignored)")

    fp = mc.ficha("{{CONTACTO}}", con_whatsapp=False)
    ok("WhatsApp" in fp and "{{CENTRO}}" in fp, "{{CONTACTO}} (1a1 = WhatsApp) + su dossier")

    fx = mc.ficha("Desconocido", con_whatsapp=False)
    ok("sin dossier" in fx, "contacto sin dossier → aviso claro, no peta")

    print("RESULTADO memoria de contactos (F4.2): %d OK, %d fallos" % (_pass, _fail))
    print("✅ MEMORIA-CONTACTOS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
