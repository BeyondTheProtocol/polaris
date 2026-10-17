"""tribunal.py — miembro computacional del tribunal (fase 6) del Flex. Entrada única.

Uso: NUMBA_NUM_THREADS=1 MURO_ALLOW_CLINICAL=1 <nescore>/python -I tools/flex_analisis/tribunal/tribunal.py <paso> [args]
Pasos: orienta | fuga | adn | calibra | clon | suelo | fase | tejido | hashes
Salidas en ~/Clinico-PRIVADO/flex/tribunal/. stdout: solo agregados (ni barcodes ni ids de bloque).
"""
import os
import sys

os.environ.setdefault("NUMBA_NUM_THREADS", "1")
os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASOS = {"orienta": "_orienta", "fuga": "_fuga", "adn": "_adn", "calibra": "_calibra",
         "suelo": "_suelo", "clon": "_clon", "fase": "_fase", "tejido": "_tejido", "hashes": "_hashes"}


def main(argv):
    if not argv or argv[0] not in PASOS:
        print(__doc__)
        return 2
    __import__(PASOS[argv[0]]).run(*argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
