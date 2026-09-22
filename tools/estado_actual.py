#!/usr/bin/env python3
"""tools/estado_actual.py — la fecha clínica de ESTADO-ACTUAL, en un solo sitio.

Nació dentro de `tests/test_perfil_clinico_al_dia.py` (13-sep-26) y se movió aquí el 22-sep-26
porque el radar la necesita para el candado del perfil N1 (`radar_ned_diario.encaje_n1`): si
ESTADO-ACTUAL §1 se actualiza después de la fecha del perfil, el perfil puede estar diciendo algo
que ya no es verdad (p. ej. «sin ADC previo» tras empezar TROPION-B06) y el filtro se para.
Un test no se importa desde `tools/`; al revés sí.

Solo lee fechas. No copia ni devuelve ningún dato clínico.
"""
import os
import re

ESTADO = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                      "00_FUENTE-DE-VERDAD", "ESTADO-ACTUAL.md")

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def _fecha(dia, mes, anio):
    m = MESES.get(mes.lower()[:3])
    return (int(anio), m, int(dia)) if m else None


def fecha_estado(texto):
    """Fecha de la sección clínica de ESTADO-ACTUAL: «## 1. Clínico (al 11-sep-2026)»."""
    m = re.search(r"^##\s*1\.\s*Cl[íi]nico\s*\(al\s+(\d{1,2})-([A-Za-zé]{3,})-(\d{4})\)", texto, re.M)
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


def fecha_clinica(texto):
    """La fecha más reciente de §1: cabecera o última «Novedades del …». None si no hay cabecera.
    Un cambio de tratamiento puede entrar como novedad sin tocar la cabecera (pasó el 13-sep)."""
    fe = fecha_estado(texto)
    if fe is None:
        return None
    fn = fecha_novedades(texto)
    return max(fe, fn) if fn else fe


def lee_fecha_clinica(ruta=None):
    """(fecha, motivo). Fecha None si el fichero no está o no se entiende."""
    ruta = ruta or ESTADO
    try:
        texto = open(ruta, encoding="utf-8").read()
    except OSError as e:
        return None, f"ESTADO-ACTUAL ilegible ({type(e).__name__})"
    f = fecha_clinica(texto)
    return (f, "ok") if f else (None, "ESTADO-ACTUAL sin «## 1. Clínico (al …)»")
