#!/usr/bin/env python
"""posthoc2.py — post hoc 2 DECLARADOS (POSTHOC_2_PLAN.json) de la fase 2 del hígado célula a célula. Entorno nescore.

NO cambian ningún veredicto: controles que pidió el red-team del 17-oct (sensibilidad de C3, reparto de H1/H2, dobletes
en configuración Flex, clasificador propio de REFCON y llamadas con HEP5).
Solo por la ventanilla:
    python3 tools/lector_clinico.py procesa flex_hc_posthoc2 -- <paso>
Pasos (en este orden):
    p3_dobletes   scDblFinder dbr.sd = 1, sin clusters y el cruce (dobletes_posthoc2.R, entorno dobletes); una vez, en caché
    p4_refcon     clasificador propio de REFCON en 3 submuestras (hijo con el python de refcon); una vez, en caché
    final         P1-P5 desde cachés; salida en posthoc2/ (o $HC_POSTHOC2_OUT, p. ej. _posthoc2_rep2/ para la 2.ª ejecución)
    manifiesto    MANIFIESTO_POSTHOC2.json (salidas, dos ejecuciones, cachés, plan, scripts, salidas canónicas intactas)
    comprueba     77 si faltan cachés (para el test)
Muro: -I, BTP_VENTANILLA=1, zona clínica y sandbox sin red (_hc.arranque); los hijos heredan el sandbox. stdout: agregados.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _hc  # noqa: E402

PASOS = ("p3_dobletes", "p4_refcon", "final", "manifiesto")


def out2():
    return os.environ.get("HC_POSTHOC2_OUT") or os.path.join(_hc.BASE, "posthoc2")


def main(argv):
    if argv and argv[0] == "comprueba":
        faltan = [f for f in ("X_higado.npz", "X_hueso.npz", "obs_higado.npz", "obs_hueso.npz")
                  if not os.path.exists(os.path.join(_hc.CACHE_CIERRE, f))]
        faltan += [f for f in ("dobletes/scdbl_dbr006.csv", "posthoc2/scdbl_dbrsd1.csv") if not os.path.exists(os.path.join(_hc.CACHE, f))]
        print("faltan cachés: %d" % len(faltan) if faltan else "cachés presentes")
        return 77 if faltan else 0
    # Todo lo que el código reutilizado escriba con _hc.outp cae en la carpeta de ESTOS post hoc, nunca en las salidas
    # canónicas de la fase 2 (se fija antes de arranque: el re-exec hereda el entorno).
    os.environ["HIGADO_CELULA_OUT"] = out2()
    _hc.arranque(__file__, argv, "flex_hc_posthoc2")
    if not argv or argv[0] not in PASOS:
        print(__doc__)
        return 2
    import higado_celula as HC
    HC._rutas_reutilizadas()
    import _hc_posthoc2 as P
    return int(getattr(P, argv[0])() or 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
