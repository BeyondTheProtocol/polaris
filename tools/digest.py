#!/usr/bin/env python3
"""tools/digest.py — compila el DIGEST sanitizado del proyecto para sincronizar el RAG de
{{CONTACTO}} (@RealTitular) POR CHAT, sin fuga.

Principio (el muro): por el chat del bot va SOLO estado/estrategia (coordinación). El bot
es un canal PRIVADO de confianza ({{CONTACTO}}, hecho para {{TITULAR}}) → los términos estratégicos
('vacuna', 'neoantígenos', nombres de ensayo, el médico de la biopsia) SÍ pueden ir (ella
ya aprobó un digest con ellos el 21/6, y {{CONTACTO}} ya alimenta al bot con el caso). Lo que NUNCA
puede salir por el chat (nube de Telegram + caja de {{CONTACTO}} SIN vetar): **PII clínica CRUDA** —
genes/biomarcadores nombrados, variantes genómicas (HGVS/VCF/HLA), teléfonos, y el contenido
de `_PRIVADO_*`. Por eso este módulo:

  1) construye el digest desde campos CURADOS de cumbre.py (meta/ruta/foco/cadena), NO
     volcando el `bloqueo` crudo (que lleva mutaciones/cifras), sino un resumen sanitizado;
  2) pasa TODO por un scrub duro (genes/variantes/teléfonos → omitidos) y verifica con
     `_lexico_publico.revisar`: si tras el scrub queda una marca de PII clínica, FALLA (es
     un bug, no se emite). El léxico 'público' (vacuna/neoantígeno/oncolog…) se PERMITE aquí
     (canal privado) y solo se informa.

El ENVÍO al bot NO lo hace este módulo: es flujo HACIA FUERA → pasa por `salida.py` OUTWARD
con el OK explícito de {{TITULAR}} (a un clic). Aquí solo se COMPILA y se compara (build/diff).
Stdlib puro, sin red.
"""
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cumbre
import _lexico_publico as lx

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_DIR = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "04 · IA", "Sync-RAG")

# Resumen SANITIZADO del bloqueo por saliente (coordinación, sin mutaciones/cifras crudas).
# Si aparece un id no contemplado → genérico (no se filtra el bloqueo crudo de cumbre).
BLOQUEO_SANITIZADO = {
    "biopsia": "confirmar la fecha de la cita y asegurar que los cores recojan TODO lo "
               "necesario para neoantígenos (preservación inmediata sin descalcificación: "
               "congelación rápida/OCT/RNAlater; WES tumor+normal; RNA-seq profundo; tipado "
               "HLA alta resolución; immunopeptidomics; PBMC)",
    "dianas": "depende de la biopsia; hay una experta dispuesta a venir a procesar el "
              "tumor→neoantígenos si se organiza (palanca de acceso a blindar)",
    "ensayo": "gate de enfermedad medible; mapear qué puertas de ensayo/fabricante siguen "
              "abiertas y cuáles no exigen enfermedad medible",
    "acceso": "viaje / self-pay / marco legal transfronterizo / fondos (campaña en marcha)",
    "medible": "la enfermedad puede no ser medible por imagen estándar → confirmar con los "
               "coordinadores qué aceptan (no-medible / evaluable / marcadores en sangre)",
}

# Scrub DURO: patrones de PII clínica cruda que NUNCA salen por el chat (reuso de _lexico_publico).
_OMIT = "[dato clínico omitido]"


def _scrub(texto):
    """Quita PII clínica cruda (genes/variantes/teléfonos) dejando el resto. Belt-and-suspenders:
    el digest se construye sin esos campos, pero si algo se cuela, aquí se omite."""
    if not texto:
        return texto
    t = lx.RE_GEN_PEGADO.sub(_OMIT, texto)
    t = re.sub(lx.RE_GEN.pattern, _OMIT, t, flags=re.I)   # genes/biomarcadores (case-insensitive)
    t = lx.RE_HGVS.sub(_OMIT, t)                          # HGVS c./p. con código de 3 letras
    t = lx.RE_AA.sub(_OMIT, t)                            # variante aminoácido 1 letra (R175H)
    t = lx.RE_TEL_SEP.sub(_OMIT, t)                       # teléfono con separadores
    t = lx.RE_TEL_PLANO.sub(_OMIT, t)                     # teléfono plano de 9 dígitos
    return t


def _saliente_linea(s):
    bl = BLOQUEO_SANITIZADO.get(s.get("id"), "(detalle en la fuente de verdad privada)")
    return "- **%s** [%s] — %s" % (s.get("titulo", "?"), s.get("estado", "?"), bl)


def build():
    """Devuelve (texto_digest, avisos). Construye desde cumbre (campos curados) y sanitiza."""
    d = cumbre.load()
    hoy = datetime.now().strftime("%Y-%m-%d")
    foco = cumbre.foco() or {}
    L = []
    L.append("# Digest del proyecto — estado a %s" % hoy)
    L.append("*(Para mantener tu RAG al día. Coordinación/estado, NO consejo médico — deciden "
             "los médicos de {{TITULAR}}. Sin datos genómicos crudos ni PII.)*")
    L.append("")
    L.append("## Meta y ruta")
    L.append("- **Meta (estrella polar):** %s" % d.get("meta", ""))
    L.append("- **Ruta de hoy:** %s" % d.get("ruta_actual", ""))
    L.append("")
    L.append("## Dónde estamos ahora")
    if foco:
        L.append("**AQUÍ ESTAMOS → %s** [%s]" % (foco.get("titulo", "?"), foco.get("estado", "?")))
        L.append("- Qué falta: %s" % BLOQUEO_SANITIZADO.get(foco.get("id"), "(ver fuente de verdad)"))
    L.append("")
    L.append("## Cadena hasta NED")
    for s in d.get("salientes", []):
        L.append(_saliente_linea(s))
    tr = d.get("transversal", [])
    if tr:
        L.append("")
        L.append("## Gates transversales")
        for s in tr:
            L.append(_saliente_linea(s))
    rc = [r for r in d.get("rutas_candidatas", []) if r.get("veto") != "descartada"]
    if rc:
        L.append("")
        L.append("## Radar de rutas (candidatas, sin re-apuntar)")
        for r in rc:
            L.append("- [%s] %s" % (r.get("veto", "?"), r.get("titulo", "")))
    L.append("")
    L.append("## Notas")
    L.append("- El detalle clínico/genómico crudo vive en la fuente de verdad privada, no aquí.")
    L.append("- Privado por defecto: nada se publica ni se contacta a nadie sin el OK explícito de {{TITULAR}}.")
    texto = _scrub("\n".join(L)) + "\n"

    # Red de seguridad: tras el scrub NO debe quedar PII clínica. El léxico público
    # (vacuna/neoantígeno/oncolog…) se PERMITE en este canal privado y solo se informa.
    avisos = lx.revisar(texto)
    pii = [a for a in avisos if "gen/biomarcador" in a or "variante" in a or "teléfono" in a]
    if pii:
        raise SystemExit("digest: FUGA de PII clínica tras el scrub (BUG): %s" % pii)
    return texto, avisos


def _ultimo_digest():
    """Ruta del digest más reciente en SYNC_DIR (para diff), o None."""
    if not os.path.isdir(SYNC_DIR):
        return None
    cands = sorted(f for f in os.listdir(SYNC_DIR) if f.startswith("digest-proyecto-") and f.endswith(".md"))
    return os.path.join(SYNC_DIR, cands[-1]) if cands else None


def _cuerpo(texto):
    """Quita la línea de fecha para comparar cambio MATERIAL (no por el sello del día)."""
    return "\n".join(l for l in texto.splitlines() if not l.startswith("# Digest del proyecto"))


def write():
    texto, avisos = build()
    os.makedirs(SYNC_DIR, exist_ok=True)
    path = os.path.join(SYNC_DIR, "digest-proyecto-%s.md" % datetime.now().strftime("%Y-%m-%d"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(texto)
    return path, avisos


def diff():
    """(hay_cambio_material, detalle). Compara el build actual con el último digest en disco."""
    texto, _ = build()
    ult = _ultimo_digest()
    if not ult:
        return True, "no hay digest previo (primer envío)"
    with open(ult, encoding="utf-8") as f:
        prev = f.read()
    if _cuerpo(texto) != _cuerpo(prev):
        return True, "cambio material vs %s" % os.path.basename(ult)
    return False, "sin cambio material vs %s" % os.path.basename(ult)


def main(argv):
    cmd = argv[0] if argv else "build"
    if cmd == "build":
        texto, avisos = build()
        sys.stdout.write(texto)
        if avisos:
            sys.stderr.write("\n[avisos léxico — PERMITIDOS en canal privado, revisa antes de enviar]:\n  - "
                             + "\n  - ".join(avisos) + "\n")
        return 0
    if cmd == "write":
        path, avisos = write()
        print("digest escrito:", path)
        if avisos:
            print("avisos léxico (permitidos, canal privado):", "; ".join(avisos))
        return 0
    if cmd == "diff":
        cambio, detalle = diff()
        print(("CAMBIO MATERIAL: " if cambio else "sin cambio: ") + detalle)
        return 0 if cambio else 1
    print("uso: digest.py [build | write | diff]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
