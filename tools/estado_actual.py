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


# ── Huella de §1 (26-sep-26) ───────────────────────────────────────────────────────────────────────
# Las fechas no bastan: §1 se actualiza también con bloques citados («Lo dice {{TITULAR}} el 20-sep…») o
# filas «(NUEVO)» sin la fórmula «Novedades del …», y el test del perfil siguió en verde con tres
# novedades posteriores al cotejo (verificacion, 26-sep). Y buscar cualquier fecha no vale: §1 lleva
# fechas FUTURAS (citas, caducidades). Así que se compara el CONTENIDO: al re-cotejar la memoria
# clínica se guarda la huella de §1, y si §1 cambia después, el perfil vuelve a estar por re-cotejar.
import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

REGISTRO = os.path.join(os.environ.get("BTP_STATE_DIR") or os.path.join(
    os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools", "state"),
    "perfil_clinico_cotejo.json")


def huella_s1(texto):
    """16 hex del texto de §1, normalizado en espacios. None si no hay §1. No devuelve contenido."""
    m = re.search(r"^##\s*1\.\s*Cl[íi]nico.*?(?=^##\s|\Z)", texto or "", re.M | re.S)
    if not m:
        return None
    return hashlib.sha256(re.sub(r"\s+", " ", m.group(0)).strip().encode("utf-8")).hexdigest()[:16]


def lee_huella_s1(ruta=None):
    """(huella, motivo). Huella None si ESTADO-ACTUAL no se lee o no tiene §1. Lo usa el candado N1."""
    try:
        texto = open(ruta or ESTADO, encoding="utf-8").read()
    except OSError as e:
        return None, f"ESTADO-ACTUAL ilegible ({type(e).__name__})"
    h = huella_s1(texto)
    return (h, "ok") if h else (None, "ESTADO-ACTUAL sin «## 1. Clínico»")


def lee_registro(ruta=None):
    try:
        with open(ruta or REGISTRO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def registrar_cotejo(fecha_cotejo, texto_estado, ruta=None):
    """Lo llama quien ACABA de re-cotejar la memoria clínica contra ESTADO-ACTUAL (no antes)."""
    d = {"fecha_cotejo": "%04d-%02d-%02d" % fecha_cotejo, "huella_s1": huella_s1(texto_estado)}
    ruta = ruta or REGISTRO
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(ruta + ".tmp", ruta)
    return d


if __name__ == "__main__" and sys.argv[1:2] == ["huella"]:
    # Uso: python3 tools/estado_actual.py huella  — la huella de §1 de hoy, para sellar el perfil N1
    # (`bench_jev.PERFIL_N1_HUELLA_S1`) DESPUÉS de re-cotejar su texto. Solo imprime el hash.
    h, por_que = lee_huella_s1()
    sys.exit(por_que) if h is None else print(h)

if __name__ == "__main__" and sys.argv[1:2] == ["cotejado"]:
    # Uso: python3 tools/estado_actual.py cotejado  — tras re-cotejar `reference-clinical-profile`.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
    mem = os.path.join(os.environ.get("BTP_MEMORY_DIR") or os.path.expanduser(
        "~/.claude/projects/-Users-polaris-claudecode/memory"), "reference-clinical-profile.md")
    from test_perfil_clinico_al_dia import fecha_cotejo  # noqa: E402
    fc = fecha_cotejo(open(mem, encoding="utf-8").read())
    if not fc:
        sys.exit("la memoria no declara «Cotejo: DD-mmm-AAAA contra … ESTADO-ACTUAL»: no registro nada")
    print(registrar_cotejo(fc, open(ESTADO, encoding="utf-8").read()))
