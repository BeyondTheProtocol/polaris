#!/usr/bin/env python3
"""test_web_citas_futuras.py — puente al test de privacidad de la web: nada publicado junta una
cita FUTURA con día y un lugar.

POR QUÉ EXISTE (25-sep-2026). El panel /datos estuvo a punto de publicar «pruebas la semana del
28-sep» y «1-oct» junto a {{CENTRO}}, con acoso activo. La norma feedback-no-publicar-citas-futuras-con-lugar
dependía solo de criterio. El freno real vive en el repo web (scripts/test-citas-futuras.ts, en la
CI E2E detrás de `nuxt generate`, PR 226); este puente lo corre también desde test_all.

Toma el script de origin/main del repo web (el checkout local puede estar en otra rama) y lo corre
con el tsx del repo web, sobre los ficheros del checkout local. Sin repo web o sin tsx: salta (77).
"""
import os
import subprocess
import sys
import tempfile

WEB = os.environ.get("BTP_WEB_REPO") or os.path.expanduser("~/projects/titular-{{APELLIDO}}-case")
TSX = os.path.join(WEB, "node_modules", ".bin", "tsx")
SKIP = 77


def main():
    if not os.path.isdir(os.path.join(WEB, ".git")) or not os.path.exists(TSX):
        print("test_web_citas_futuras: saltado (no está el repo web o su tsx)")
        return SKIP
    try:
        src = subprocess.run(["git", "-C", WEB, "show", "origin/main:scripts/test-citas-futuras.ts"],
                             capture_output=True, text=True, check=True, timeout=30).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("test_web_citas_futuras: saltado (origin/main no tiene scripts/test-citas-futuras.ts)")
        return SKIP
    with tempfile.TemporaryDirectory(prefix="citas_futuras_") as d:
        ruta = os.path.join(d, "test-citas-futuras.ts")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(src)
        r = subprocess.run([TSX, ruta], cwd=WEB, capture_output=True, text=True, timeout=180)
    fallos = [l for l in r.stdout.splitlines() if "❌" in l]
    for l in fallos[:10]:
        print(l[:240])
    print(f"test_web_citas_futuras: {'0 fallos' if r.returncode == 0 else f'{len(fallos)} fallo(s)'}")
    return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
