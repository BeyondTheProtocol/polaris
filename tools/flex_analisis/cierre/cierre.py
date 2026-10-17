"""cierre.py — cierre técnico del Flex (REGLAS_CIERRE_v3.json). Entrada única.

Uso: MURO_ALLOW_CLINICAL=1 <nescore>/python -I tools/flex_analisis/cierre/cierre.py <paso>
Pasos: prep (lectura pesada → cachés) | final (todo el cálculo desde cachés) | manifiesto | comprueba (77 si faltan cachés)
Salidas en ~/Clinico-PRIVADO/flex/cierre/ (o $CIERRE_OUT). stdout: solo agregados.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MPLBACKEND", "Agg")


def main(argv):
    import _c  # noqa: F401  (fija hilos a 1 antes de numpy)
    if argv and argv[0] == "comprueba":
        # 77 = SKIP para test_all.sh: sin cachés (otra máquina, CI) no hay con qué comprobar
        faltan = [f for f in ("X_higado.npz", "X_hueso.npz", "obs_higado.npz", "obs_hueso.npz") if not os.path.exists(os.path.join(_c.CACHE, f))]
        print("faltan cachés: %d" % len(faltan) if faltan else "cachés presentes")
        return 77 if faltan else 0
    if argv and argv[0] == "manifiesto_ruta":
        print(os.path.join(_c.CIERRE, "MANIFIESTO_CIERRE.json"))
        return 0
    if not argv or argv[0] not in ("prep", "final", "manifiesto"):
        print(__doc__)
        return 2
    if argv[0] == "prep":
        import _prep
        _prep.run()
    elif argv[0] == "final":
        import _final
        _final.run()
    else:
        import _final
        _final.manifiesto()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
