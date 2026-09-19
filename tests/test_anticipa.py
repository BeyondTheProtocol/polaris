#!/usr/bin/env python3
"""test_anticipa.py — F4.3 anticipación. Proyecta hoy..+7d (citas/plazos NED) + el gate actual de
cumbre con su checklist PRE-GENERADO (del bloqueo). Derivado, read-only, $0. Aislado en tmp."""
import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="anticipa_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import anticipa as an       # noqa: E402
import seguimiento          # noqa: E402
import cumbre               # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    hoy = datetime.date.today()
    dentro = (hoy + datetime.timedelta(days=3)).isoformat()
    fuera = (hoy + datetime.timedelta(days=30)).isoformat()
    seguimiento.recopilar = lambda: {"items": [
        {"id": "cita", "titulo": "Cita biopsia Zúrich", "estado": "en_curso", "categoria": "NED",
         "vence": dentro, "siguiente_accion": "checklist a {{CONTACTO}}"},
        {"id": "lejos", "titulo": "Algo lejano", "estado": "en_curso", "vence": fuera},
        {"id": "hecho", "titulo": "Ya hecho", "estado": "hecho", "vence": hoy.isoformat()},
    ]}
    cumbre.foco = lambda: {"id": "biopsia", "titulo": "Re-biopsia L1 Zúrich", "estado": "en_curso",
                           "bloqueo": "que los cores recojan TODO: 13-core, HLA-LOH, PBMC, RNA-seq",
                           "siguiente_accion": "confirmar fecha"}

    b = an.esta_semana(7)
    ok("Cita biopsia Zúrich" in b, "incluye la cita DENTRO de la ventana")
    ok("Algo lejano" not in b, "excluye lo fuera de ventana")
    ok("Ya hecho" not in b, "excluye lo cerrado")
    ok("AQUÍ ESTAMOS" in b and "Re-biopsia" in b, "muestra el gate actual de la ruta")
    ok("Prepara YA" in b and "13-core" in b, "PRE-GENERA lo que destraba el gate (checklist del bloqueo)")

    # sin nada datado → no peta, lo dice
    seguimiento.recopilar = lambda: {"items": []}
    b2 = an.esta_semana(7)
    ok("nada datado" in b2 and "Re-biopsia" in b2, "sin citas → lo dice + sigue mostrando el foco")

    print("RESULTADO anticipación (F4.3): %d OK, %d fallos" % (_pass, _fail))
    print("✅ ANTICIPA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
