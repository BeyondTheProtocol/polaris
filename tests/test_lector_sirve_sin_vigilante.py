#!/usr/bin/env python3
"""`visor3d sirve` no corre bajo el vigilante de cuelgues de la ventanilla.

El vigilante mata un visor3d que pasa 10 min al 0 % de CPU (interbloqueo de nnU-Net, 24-sep-2026).
Un servidor local que espera visitas está al 0 % por diseño: el 8-oct-2026 murió dos veces con
código 98 mientras nadie giraba la escena. Todo lo demás de visor3d sigue vigilado.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import lector_clinico as L  # noqa: E402

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


check(L._vigila("visor3d", ["sirve"]) is False, "sirve → sin vigilante")
check(L._vigila("visor3d", ["sirve", "--puerto", "8794"]) is False, "sirve con puerto → sin vigilante")
check(L._vigila("visor3d", ["--organo", "higado", "sirve"]) is False, "sirve tras una opción global → sin vigilante")
check(L._vigila("visor3d", ["reservorio3d", "60691016", "--punta", "31", "1198"]) is True, "reservorio3d sigue vigilado")
check(L._vigila("visor3d", ["segmenta", "x"]) is True, "segmenta sigue vigilado")
check(L._vigila("visor3d", []) is True, "sin argumentos sigue vigilado")
check(L._vigila("ocr_informes", ["sirve"]) is False, "lo que nunca estuvo vigilado sigue sin estarlo")
check("visor3d" in L.VIGILA_CUELGUE, "visor3d sigue en la lista de vigilados")

print("\n%s" % ("TODO VERDE" if not fallos else "ROJO: %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
