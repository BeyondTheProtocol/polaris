#!/usr/bin/env python3
"""test_historial_trazador.py — el nombre de un informe de imagen no puede afirmar un trazador
que su texto no menciona (29-sep-2026).

Origen: «2024-03-18 · PET-CT de cuerpo completo con 68Ga - {{TRAZADOR}}» era una copia del PET-FDG de
ese día con el título del portal cambiado. El índice lo repetía y tres comités y la web llegaron a
hablar de un «{{TRAZADOR}} de 2024» que no existió. Deuda: historial-fichero-{{TRAZADOR}}-2024-mal-etiquetado.

  · casos sintéticos de trazador_incoherente (ningún dato real)
  · barrido real de `03 · Imagen` si el historial existe en esta máquina; si no, se salta
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import historial as h  # noqa: E402

_fail = 0


def check(cond, msg):
    global _fail
    if cond:
        print("  ✓", msg)
    else:
        _fail += 1
        print("  ✗", msg)


RELLENO = " Estudio de cuerpo completo desde base de cráneo hasta raíz de muslos." * 3
FDG = "PET-CT con 18F-FDG. Dosis administrada de 18F-FDG: 183.8 MBq." + RELLENO
GALIO = "PET-CT con 68Ga-{{TRAZADOR}}. Sobreexpresión de receptores de somatostatina." + RELLENO

check(h.trazador_incoherente("2024-03-18 - {{CENTRO}} - PET-CT de cuerpo completo con 68Ga - {{TRAZADOR}}.pdf", FDG)
      == "68Ga-{{TRAZADOR}}/DOTATATE", "nombre {{TRAZADOR}} con texto de FDG → incoherente")
check(h.trazador_incoherente("2026-05-26 - {{CENTRO}} - pet-galio68.pdf", GALIO) is None,
      "nombre de galio con texto de galio → coherente")
check(h.trazador_incoherente("2026-03-24 - {{CENTRO}} - PET-CT selectiva con 18F - FDG.pdf", FDG) is None,
      "nombre FDG con texto FDG → coherente")
check(h.trazador_incoherente("2026-03-24 - {{CENTRO}} - PET-CT con 18F - FDG.pdf", GALIO) == "18F-FDG",
      "nombre FDG con texto de galio → incoherente")
check(h.trazador_incoherente("2024-03-18 - {{CENTRO}} - PET-CT FDG (copia con título erróneo en el portal).pdf", FDG) is None,
      "el nombre corregido no se acusa a sí mismo")
check(h.trazador_incoherente("2026-05-26 - {{CENTRO}} - pet-galio68.pdf", "   ") is None,
      "sin texto no se juzga")

carpeta = os.path.join(h.RAIZ, h.CLAVE_A_CARPETA["imagen"])
if os.path.isdir(carpeta):
    malos = h.trazadores()
    check(not malos, "03 · Imagen sin nombres que mientan sobre el trazador%s"
          % ("" if not malos else ": " + "; ".join("%s (%s)" % m for m in malos)))
else:
    print("  · historial real no disponible en esta máquina: barrido omitido")

if _fail:
    print("✗ test_historial_trazador: %d fallo(s)" % _fail)
    sys.exit(1)
print("✓ test_historial_trazador")
