"""flex_a.py — analista A del Flex (fase 5). Entrada única con subcomandos.

Uso:  NUMBA_NUM_THREADS=1 MURO_ALLOW_CLINICAL=1 <nescore>/python -I tools/flex_analisis/a/flex_a.py <paso>
Pasos: inventario | qc | celulas | malignas | tablas | parálogos(paralogos) | secundarias | congela
Todo lo que escribe va a ~/Clinico-PRIVADO/flex/analisis_A/. stdout: solo agregados.
"""
import os
import sys

os.environ.setdefault("NUMBA_NUM_THREADS", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    paso = argv[0]
    mod = {"inventario": "_inventario", "qc": "_qc", "celulas": "_celulas",
           "malignas": "_malignas", "tablas": "_tablas", "paralogos": "_paralogos",
           "secundarias": "_secundarias", "congela": "_congela", "tejido": "_tejido", "diag": "_diag", "diag2": "_diag2", "diag3": "_diag3", "purificada": "_purificada", "informe": "_informe", "control_ref": "_control_ref"}.get(paso)
    if mod is None:
        print("paso desconocido:", paso)
        return 2
    m = __import__(mod)
    m.run(*argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
