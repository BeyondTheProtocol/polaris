"""ambiente.py — descontaminación de ARN ambiental del Flex (REGLAS_AMBIENTE_v1.json). Entrada única.

Uso: MURO_ALLOW_CLINICAL=1 <nescore>/python -I tools/flex_analisis/ambiente/ambiente.py <paso>
Pasos: orienta | prep | final | comprueba (77 si faltan cachés)
Salidas en ~/Clinico-PRIVADO/flex/ambiente/ (o $AMBIENTE_OUT). stdout: solo agregados.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "Agg")

PASOS = {"orienta": "_amb_orienta", "orienta2": "_amb_orienta2", "sintetico": "_amb_sintetico", "cellbender": "_amb_cellbender", "prep": "_amb_prep", "final": "_amb_final", "externa": "_amb_externa",
         "comprueba": "_amb_comprueba", "vias": "_amb_vias"}


def main(argv):
    import _a  # noqa: F401  (fija hilos a 1 antes de numpy)
    if not argv or argv[0] not in PASOS:
        print(__doc__)
        return 2
    m = importlib.import_module(PASOS[argv[0]])
    r = m.run(*argv[1:])
    return int(r or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
