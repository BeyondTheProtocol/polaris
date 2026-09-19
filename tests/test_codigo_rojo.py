#!/usr/bin/env python3
"""test_codigo_rojo.py — el cortafuegos para todas las máquinas, avisa, explica (regla
inquebrantable). Aísla HALT/informe y stubbea la red. Verifica el orden y el gate de clear."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import codigo_rojo as cr  # noqa: E402

_pass = 0
_fail = 0
_alertas = []


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    tmp = tempfile.mkdtemp(prefix="test_rojo_")
    cr.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    cr.ROJO_MD = os.path.join(tmp, "CODIGO-ROJO.md")
    cr.salida.alerta_critica = lambda t: (_alertas.append(t) or {"delivered": True, "reason": "stub"})
    cr._stop_launchd = lambda: None     # no tocar launchd en el test

    # trigger: para todo + explica + avisa
    cr.trigger("la biopsia se borró por un fallo mío", "detalle técnico del incidente")
    check("crea AMBOS kill-switches", all(os.path.exists(h) for h in cr.HALT_FILES))
    check("escribe el informe CODIGO-ROJO.md", os.path.exists(cr.ROJO_MD))
    txt = open(cr.ROJO_MD, encoding="utf-8").read()
    check("el informe lleva el motivo y qué paró", "biopsia se borró" in txt and "HALT activado" in txt)
    check("avisó fuerte (atraviesa HALT)", len(_alertas) == 1 and "CÓDIGO ROJO" in _alertas[0])
    check("la alerta nombra el goal (NED)", "NED" in _alertas[0])

    # status refleja el código rojo activo
    # clear sin presencia humana → NO levanta
    os.environ.pop("BTP_PRESENCE_OK", None)
    check("clear sin presencia → no levanta", cr.clear() is False)
    check("HALT sigue puesto tras clear bloqueado", all(os.path.exists(h) for h in cr.HALT_FILES))

    # clear con presencia humana → levanta
    os.environ["BTP_PRESENCE_OK"] = "1"
    check("clear con presencia → levanta", cr.clear() is True)
    check("HALT retirado tras clear humano", not any(os.path.exists(h) for h in cr.HALT_FILES))
    os.environ.pop("BTP_PRESENCE_OK", None)

    print("RESULTADO código rojo: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CÓDIGO ROJO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
