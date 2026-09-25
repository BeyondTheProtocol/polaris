#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vigila que las reglas del sistema SIGAN CARGÁNDOSE en cada sesión.

POR QUÉ EXISTE (25-jul-26)
--------------------------
Las normas de {{TITULAR}} se olvidaban por saturación, no por falta de memoria:
`CLAUDE.md` había llegado a 33.9KB y `MEMORY.md` a 21.9KB de los 25KB que el
cargador admite. Pasado ese punto, **lo que sobra deja de cargarse y NADIE avisa**:
la regla sigue escrita en el fichero, pero ya no llega a la sesión.

Este chequeo es el guardián de esa frontera. Corre en `tests/test_all.sh` y puede
avisar a {{TITULAR}} si algo se ha pasado de la raya.

Uso:
  python3 tools/salud_memoria.py             # informe legible
  python3 tools/salud_memoria.py --check     # exit 1 si algo supera el TECHO
  python3 tools/salud_memoria.py --avisar    # además, avisa por salida.py (anti-spam)

Determinista, local, sin red. Fail-open: si algo no se puede medir, no rompe.
"""
import argparse
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(AQUI)
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

MEM_DIR = os.path.expanduser(
    os.environ.get("BTP_MEMORY_DIR", "~/.claude/projects/-Users-polaris-claudecode/memory")
)

# Límites. Los de MEMORY.md son los REALES del cargador (doc oficial de Claude Code:
# primeras 200 líneas o 25KB). Los de CLAUDE.md son nuestros: se carga entero, pero
# cuanto más largo, menos adherencia; 15KB es el volumen equivalente a las ~200
# líneas que recomienda la doc.
LIMITES = {
    "CLAUDE.md":  {"aviso": 14 * 1024, "techo": 15 * 1024, "lineas": None},
    "MEMORY.md":  {"aviso": 12 * 1024, "techo": 25 * 1024, "lineas": 200},
    "regla":      {"aviso": 6 * 1024,  "techo": 8 * 1024,  "lineas": None},
}


def _medir(ruta):
    with open(ruta, encoding="utf-8") as fh:
        texto = fh.read()
    return len(texto.encode("utf-8")), len(texto.splitlines())


def _tiene_paths(ruta):
    """Una regla SIN `paths:` se carga en TODAS las sesiones: pesa como CLAUDE.md."""
    try:
        with open(ruta, encoding="utf-8") as fh:
            cabecera = fh.read(400)
    except OSError:
        return False
    return cabecera.startswith("---") and "paths:" in cabecera


def chequear():
    """Devuelve (alertas, info). Cada alerta: (clave_estable, nivel, texto).

    `clave_estable` es apta para el anti-flapping de healthcheck._emitir_si_cambia
    y para `tools/salud.py ack <clave>`.
    """
    alertas, info = [], []

    # 1. La constitución.
    ruta = os.path.join(REPO, "CLAUDE.md")
    if os.path.exists(ruta):
        nbytes, nlineas = _medir(ruta)
        lim = LIMITES["CLAUDE.md"]
        info.append("CLAUDE.md: %d B / %d líneas" % (nbytes, nlineas))
        if nbytes > lim["techo"]:
            alertas.append(("memoria-claude-md-grande", "alerta",
                            "CLAUDE.md %d B supera el techo de %d B: cada regla extra resta "
                            "adherencia a las demás. Saca algo a .claude/rules/ con paths:."
                            % (nbytes, lim["techo"])))
        elif nbytes > lim["aviso"]:
            alertas.append(("memoria-claude-md-grande", "info",
                            "CLAUDE.md %d B roza el techo (%d B)." % (nbytes, lim["techo"])))

    # 2. El índice de la memoria: el único con corte SILENCIOSO.
    ruta = os.path.join(MEM_DIR, "MEMORY.md")
    if os.path.exists(ruta):
        nbytes, nlineas = _medir(ruta)
        lim = LIMITES["MEMORY.md"]
        info.append("MEMORY.md: %d B / %d líneas" % (nbytes, nlineas))
        if nbytes > lim["techo"] or nlineas > lim["lineas"]:
            alertas.append(("memoria-indice-cortado", "alerta",
                            "MEMORY.md %d B / %d líneas pasa del corte (%d B / %d líneas): lo que "
                            "sobra NO se carga y no avisa nadie. Corre `python3 tools/indice_memoria.py`."
                            % (nbytes, nlineas, lim["techo"], lim["lineas"])))
        elif nbytes > lim["aviso"]:
            alertas.append(("memoria-indice-cortado", "info",
                            "MEMORY.md %d B se acerca al corte de %d B." % (nbytes, lim["techo"])))

    # 3. Las reglas por contexto.
    dir_reglas = os.path.join(REPO, ".claude", "rules")
    if os.path.isdir(dir_reglas):
        siempre = []
        for nombre in sorted(os.listdir(dir_reglas)):
            if not nombre.endswith(".md"):
                continue
            ruta = os.path.join(dir_reglas, nombre)
            nbytes, _ = _medir(ruta)
            con_paths = _tiene_paths(ruta)
            if not con_paths:
                siempre.append((nombre, nbytes))
            lim = LIMITES["regla"]
            if nbytes > lim["techo"]:
                alertas.append(("memoria-regla-grande-%s" % nombre[:-3], "info",
                                "La regla %s pesa %d B: pártela o muévela a una memoria."
                                % (nombre, nbytes)))
        info.append("reglas: %d ficheros, %d sin paths (se cargan siempre)"
                    % (len([n for n in os.listdir(dir_reglas) if n.endswith('.md')]), len(siempre)))
        # El presupuesto que importa no es el de cada fichero: es la SUMA de todo lo que
        # entra en cada sesión. Desde el 25-jul hay dos fuentes (la constitución y las
        # normas sin `paths:`), y si cada una se vigila por su lado, entre las dos se
        # cuela justo lo que este guardián existe para evitar.
        peso_siempre = sum(b for _n, b in siempre)
        ruta_c = os.path.join(REPO, "CLAUDE.md")
        total = peso_siempre + (_medir(ruta_c)[0] if os.path.exists(ruta_c) else 0)
        info.append("carga fija por sesión: %d B (CLAUDE.md + reglas sin paths)" % total)
        if total > 20 * 1024:
            alertas.append(("memoria-carga-fija", "alerta",
                            "%d B entran en CADA sesión (techo 20480 B): a partir de aquí "
                            "cada regla nueva le resta adherencia a las que ya hay. Saca algo "
                            "a una regla con `paths:`." % total))
        elif total > 18 * 1024:
            alertas.append(("memoria-carga-fija", "info",
                            "%d B de carga fija por sesión: queda poco margen." % total))

    return alertas, info


def main(argv=None):
    ap = argparse.ArgumentParser(description="Salud del sistema de memoria y reglas.")
    ap.add_argument("--check", action="store_true", help="exit 1 si algo supera el techo")
    ap.add_argument("--avisar", action="store_true", help="avisar a {{TITULAR}} si hay alerta roja")
    args = ap.parse_args(argv)

    alertas, info = chequear()
    for linea in info:
        print("  " + linea)

    rojas = [a for a in alertas if a[1] == "alerta"]
    for clave, nivel, texto in alertas:
        print(("❌ " if nivel == "alerta" else "⚠️  ") + texto + "   [clave: %s]" % clave)
    if not alertas:
        print("✅ Memoria y reglas dentro de límites: todo lo escrito sigue llegando a la sesión.")

    if args.avisar and rojas:
        try:
            import salida  # noqa: E402 — opcional: fuera del repo vivo puede no estar
            salida.report_to_titular(
                "🧠 Salud de la memoria:\n- " + "\n- ".join(t for _c, _n, t in rojas))
        except Exception as exc:  # fail-open: un aviso que falla no rompe el chequeo
            print("ℹ️  No se pudo avisar (%s)" % exc)

    if args.check:
        return 1 if rojas else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
