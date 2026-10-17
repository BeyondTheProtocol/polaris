#!/usr/bin/env python
"""higado_celula.py — fase 2 del hígado célula a célula (REGLAS_HIGADO_CELULA_v1.json). Entrada única (entorno nescore).

Solo por la ventanilla:
    python3 tools/lector_clinico.py procesa flex_higado_celula -- <paso>
Pasos:
    dobletes      exporta las cuentas crudas y corre dobletes.R (scDblFinder + scCDC)
    gate_refcon   control positivo de REFCON en el hueso (antes de inferir el hígado)
    final         todo el cálculo desde cachés (criterios estándar y estricto, residuo, scCDC, REFCON, dianas)
    manifiesto    MANIFIESTO_HIGADO_CELULA.json (salidas, scripts, cachés, reglas, locks, commit)
    comprueba     77 si faltan cachés (para test_all.sh)
    manifiesto_ruta
Muro: -I, BTP_VENTANILLA=1, zona clínica y sandbox sin red (ver _hc.arranque). stdout: solo agregados.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _hc  # noqa: E402

PASOS = ("dobletes", "gate_refcon", "final", "manifiesto")


def _rutas_reutilizadas():
    for d in ("ambiente", "cierre", "tribunal"):
        p = os.path.join(AQUI, "..", d)
        if p not in sys.path:
            sys.path.append(p)
    # Lo que el código reutilizado escriba (dianas por F.tablas, etc.) cae en la carpeta de ESTA fase, nunca en las
    # salidas selladas del cierre o del ambiente.
    aux = os.path.join(_hc.out_dir(), "aux")
    os.makedirs(aux, exist_ok=True)
    os.environ["AMBIENTE_OUT"] = aux
    os.environ["CIERRE_OUT"] = aux


def main(argv):
    if argv and argv[0] == "manifiesto_ruta":
        print(os.path.join(_hc.BASE, "MANIFIESTO_HIGADO_CELULA.json"))
        return 0
    if argv and argv[0] == "comprueba":
        faltan = [f for f in ("X_higado.npz", "X_hueso.npz", "obs_higado.npz", "obs_hueso.npz")
                  if not os.path.exists(os.path.join(_hc.CACHE_CIERRE, f))]
        faltan += [f for f in ("scdbl_dbr006.csv",) if not os.path.exists(os.path.join(_hc.CACHE, "dobletes", f))]
        print("faltan cachés: %d" % len(faltan) if faltan else "cachés presentes")
        return 77 if faltan else 0
    _hc.arranque(__file__, argv, "flex_higado_celula")
    if not argv or argv[0] not in PASOS:
        print(__doc__)
        return 2
    _rutas_reutilizadas()
    if argv[0] == "dobletes":
        import _hc_dobletes
        return int(_hc_dobletes.run(*argv[1:]) or 0)
    import _hc_final
    if argv[0] == "gate_refcon":
        return int(_hc_final.gate_refcon() or 0)
    if argv[0] == "manifiesto":
        return int(_hc_final.manifiesto(*argv[1:]) or 0)
    return int(_hc_final.run(*argv[1:]) or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
