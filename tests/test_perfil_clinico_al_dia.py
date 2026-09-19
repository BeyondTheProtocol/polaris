#!/usr/bin/env python3
"""test_perfil_clinico_al_dia.py — la memoria clínica maestra no puede quedarse por detrás del estado real.

POR QUÉ EXISTE (13-sep-2026, deuda `memoria-clinica-maestra-desactualizada`). La memoria
`reference-clinical-profile` es la que el recall inyecta en cada respuesta sobre el caso. Seguía
diciendo «bone-predominant, no visceral crisis» (foto de junio) y no nombraba el hígado ni una vez,
cuando había metástasis hepáticas (ya visibles en el PET del 10-jul) y son las que sacaron a {{TITULAR}} del ensayo el 9-sep.
Resultado: se analizó un paper de inmunoterapia solo en clave de hueso y {{TITULAR}} tuvo que corregirlo.
Es la MISMA clase que el 23-ago con el elacestrant: se corrige el documento donde salta el error y la
memoria maestra se queda vieja.

QUÉ EXIGE (sin copiar ni un dato clínico en el test):
  1. Que la memoria declare con qué fecha se cotejó: «Cotejo: DD-mmm-AAAA contra … ESTADO-ACTUAL».
  2. Que esa fecha NO sea anterior a la de la sección clínica de ESTADO-ACTUAL («## 1. Clínico (al …)»).
     Si ESTADO-ACTUAL se reescribe, la memoria se pone roja hasta que alguien la re-coteje.
  2b. Que tampoco sea anterior a la última «Novedades del DD-mmm» colgada dentro de §1. Hueco real
      (13-sep-26): se añadió «Novedades del 13-sep» bajo «## 1. Clínico (al 11-sep-2026)» sin tocar
      esa fecha, y el test solo miraba la cabecera.
  3. Que toda LOCALIZACIÓN que ESTADO-ACTUAL nombre en «### Enfermedad» (hígado, hueso, pulmón,
     cerebro, ganglios, piel, peritoneo) aparezca también en la memoria. Es justo el fallo del hígado.

Si alguno de los dos ficheros no existe en esta máquina (el portátil, un CI), no hay nada que comparar:
se dice y se sale en verde. Las fuentes viven fuera de git a propósito (datos clínicos).
"""
import os
import re
import sys
import tempfile

MEMORIA = os.path.join(
    os.environ.get("BTP_MEMORY_DIR")
    or os.path.expanduser("~/.claude/projects/-Users-polaris-claudecode/memory"),
    "reference-clinical-profile.md")
ESTADO = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                      "00_FUENTE-DE-VERDAD", "ESTADO-ACTUAL.md")

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "oct": 10, "nov": 11, "dic": 12}
# Localizaciones de metástasis que importan para las decisiones. Se buscan como FAMILIA de palabras:
# «hígado» y «hepáticas» son la misma localización.
SITIOS = {
    "hígado": r"h[íi]gado|hep[áa]tic",
    "hueso": r"\bhueso|\b[óo]se[oa]s?\b|v[ée]rtebra",
    "pulmón": r"pulm[óo]n|pulmonar",
    "cerebro": r"cerebr|enc[ée]fal",
    "ganglios": r"ganglio|ganglionar|adenopat",
    "piel": r"\bpiel\b|cut[áa]ne",
    "peritoneo": r"periton",
}

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _fecha(dia, mes, anio):
    m = MESES.get(mes.lower()[:3])
    return (int(anio), m, int(dia)) if m else None


def fecha_estado(texto):
    """Fecha de la sección clínica de ESTADO-ACTUAL: «## 1. Clínico (al 11-sep-2026)»."""
    m = re.search(r"^##\s*1\.\s*Cl[íi]nico\s*\(al\s+(\d{1,2})-([A-Za-zé]{3,})-(\d{4})\)", texto, re.M)
    return _fecha(*m.groups()) if m else None


def fecha_cotejo(texto):
    """Fecha de cotejo que declara la memoria: «Cotejo: 13-sep-2026 contra … ESTADO-ACTUAL»."""
    m = re.search(r"Cotejo:?\**\s*(\d{1,2})-([A-Za-zé]{3,})-(\d{4})[^\n]*ESTADO-ACTUAL", texto)
    return _fecha(*m.groups()) if m else None


def fecha_novedades(texto):
    """Última «Novedades del DD-mmm» dentro de §1; el año sale de la cabecera de §1. None si no hay."""
    fe = fecha_estado(texto)
    m = re.search(r"^##\s*1\.\s*Cl[íi]nico.*?(?=^##\s|\Z)", texto, re.M | re.S)
    if not fe or not m:
        return None
    fechas = [_fecha(d, mes, fe[0])
              for d, mes in re.findall(r"Novedades del (\d{1,2})-([A-Za-zé]{3,})", m.group(0))]
    fechas = [f for f in fechas if f]
    return max(fechas) if fechas else None


def bloque_enfermedad(texto):
    """El bloque «### Enfermedad» de la sección clínica, hasta el siguiente encabezado."""
    m = re.search(r"^###\s*Enfermedad\s*$(.*?)(?=^#{2,3}\s)", texto, re.M | re.S)
    return m.group(1) if m else ""


def sitios(texto):
    return {s for s, pat in SITIOS.items() if re.search(pat, texto, re.I)}


def problemas(memoria, estado):
    """Lista de motivos por los que la memoria está por detrás de ESTADO-ACTUAL (vacía = al día)."""
    out = []
    fe, fc = fecha_estado(estado), fecha_cotejo(memoria)
    if fc is None:
        out.append("la memoria no declara «Cotejo: DD-mmm-AAAA contra … ESTADO-ACTUAL»")
    if fe is None:
        out.append("no encuentro la fecha de «## 1. Clínico (al …)» en ESTADO-ACTUAL")
    if fe and fc and fc < fe:
        out.append("la memoria se cotejó el %s y ESTADO-ACTUAL §1 es del %s: re-cotejar" % (fc, fe))
    fn = fecha_novedades(estado)
    if fn and fc and fc < fn:
        out.append("ESTADO-ACTUAL §1 trae novedades del %s y la memoria se cotejó el %s: re-cotejar" % (fn, fc))
    faltan = sorted(sitios(bloque_enfermedad(estado)) - sitios(memoria))
    if faltan:
        out.append("ESTADO-ACTUAL nombra %s y la memoria no" % ", ".join(faltan))
    return out


ESTADO_FIX = """# ESTADO
## 1. Clínico (al 11-sep-2026)
### Lo que manda ahora
- algo
### Enfermedad
- Hígado: múltiples metástasis en aumento.
- Hueso estable; sin afectación pulmonar.
## 2. Tejido
"""


def main():
    # --- 1. El mecanismo, sobre textos de juguete ---
    ok(fecha_estado(ESTADO_FIX) == (2026, 9, 11), "lee la fecha de «## 1. Clínico (al …)»")
    buena = "**Cotejo: 13-sep-2026 contra `ESTADO-ACTUAL.md`**\nHígado, hueso, sin afectación pulmonar."
    ok(problemas(buena, ESTADO_FIX) == [], "memoria cotejada después y con todas las localizaciones: al día")
    vieja = "**Cotejo: 30-jun-2026 contra ESTADO-ACTUAL**\nHígado, hueso, pulmonar."
    ok(any("re-cotejar" in p for p in problemas(vieja, ESTADO_FIX)), "cotejo anterior a ESTADO-ACTUAL: rojo")
    sin_higado = "**Cotejo: 13-sep-2026 contra ESTADO-ACTUAL**\nBone-predominant, no visceral crisis; pulmonar."
    ok(any("hígado" in p for p in problemas(sin_higado, ESTADO_FIX)),
       "EL CASO REAL: la memoria no nombra el hígado que ESTADO-ACTUAL sí nombra → rojo")
    ok(any("no declara" in p for p in problemas("Hígado, hueso, pulmonar.", ESTADO_FIX)),
       "sin fecha de cotejo: rojo")
    ok(sitios("metástasis hepáticas") == {"hígado"}, "«hepáticas» cuenta como hígado")
    ok(fecha_novedades(ESTADO_FIX) is None, "sin «Novedades del …» en §1: no hay fecha de novedades")
    con_nov = ESTADO_FIX.replace("- algo", "- algo\n- **🔄 Novedades del 20-sep (panel):** algo nuevo")
    ok(fecha_novedades(con_nov) == (2026, 9, 20), "lee «Novedades del 20-sep» con el año de la cabecera")
    ok(any("novedades" in p for p in problemas(buena, con_nov)),
       "EL HUECO: novedades del 20-sep bajo una cabecera del 11-sep y memoria cotejada el 13 → rojo")
    ok(problemas(buena.replace("13-sep-2026", "20-sep-2026"), con_nov) == [],
       "memoria cotejada el mismo día de las novedades: al día")
    fuera = ESTADO_FIX + "- **Novedades del 30-sep:** esto es de la sección 2\n"
    ok(fecha_novedades(fuera) is None, "unas novedades fuera de §1 no cuentan")

    # --- 2. Contra los ficheros reales ---
    if not (os.path.isfile(MEMORIA) and os.path.isfile(ESTADO)):
        print("  · sin memoria o sin ESTADO-ACTUAL en esta máquina: nada que comparar")
    else:
        with open(MEMORIA, encoding="utf-8") as f:
            mem = f.read()
        with open(ESTADO, encoding="utf-8") as f:
            est = f.read()
        reales = problemas(mem, est)
        ok(not reales, "reference-clinical-profile al día con ESTADO-ACTUAL: %s" % "; ".join(reales))

    print("RESULTADO perfil_clinico_al_dia: %d OK, %d fallos" % (_pass, _fail))
    print("✅ PERFIL CLÍNICO AL DÍA" if _fail == 0 else "❌ la memoria clínica va por detrás del estado real")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
