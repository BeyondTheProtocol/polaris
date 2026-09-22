#!/usr/bin/env python3
"""tools/anticipa.py — F4.3: anticipación. El bloque "🔭 ESTA SEMANA" (determinista, $0, read-only).

Una asistente buena recuerda la cita; la MEJOR trae, dos pasos antes, lo que hará falta. Este tool
PROYECTA la ventana hoy..+7d sobre `seguimiento.json` (citas/plazos NED datados) + `cumbre.json` (el
gate actual de la ruta) y PRE-GENERA el checklist/preparación que destraba ese gate (ej.: el checklist
molecular para la biopsia ya vive en `cumbre.json:biopsia.bloqueo` → lo surface ANTES de la cita).

Es DERIVADA pura, no una 4ª fuente de verdad (como `seguimiento.render_tracks`): solo lee y proyecta.
Pre-TRABAJO, no pre-DECISIÓN (la decisión clínica es de {{TITULAR}} y su equipo). 0 LLM, 0 egress. Resuelve
a casa base. El bloque lo incluye Vega en el parte de HOY (lo lee el barrido diario).

Uso:
  python3 tools/anticipa.py                 # imprime el bloque "🔭 ESTA SEMANA"
  python3 tools/anticipa.py --dias 14
"""
import os
import sys
from datetime import date, datetime, timedelta

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fecha(it):
    for campo in ("vence", "fecha", "cuando", "deadline", "due", "plazo"):
        v = it.get(campo)
        if v:
            try:
                return datetime.fromisoformat(str(v)[:10]).date()
            except Exception:
                return None
    return None


def esta_semana(dias=7):
    """Bloque '🔭 ESTA SEMANA' (str). Nunca lanza: ante un fallo, lo dice en texto."""
    lineas = ["🔭 ESTA SEMANA (lo que viene + lo que conviene preparar YA):"]
    # 1. citas/plazos NED datados en la ventana hoy..+dias
    try:
        import seguimiento as sg
        items = sg.recopilar().get("items", [])
    except Exception:
        items = []
    hoy = date.today()
    fin = hoy + timedelta(days=max(1, int(dias)))
    proximos = []
    for it in items:
        if str(it.get("estado", "")).lower() in ("hecho", "done", "cerrado"):
            continue
        fv = _fecha(it)
        if fv and hoy <= fv <= fin:
            proximos.append((fv, it))
    proximos.sort(key=lambda x: x[0])
    if proximos:
        for fv, it in proximos[:8]:
            sig = (it.get("siguiente_accion") or "").strip()
            lineas.append("· %s — %s%s" % (fv.isoformat(), (it.get("titulo") or "")[:70],
                                           (" → " + sig[:90]) if sig else ""))
    else:
        lineas.append("· (nada datado en los próximos %d días)" % dias)
    # 2. el gate actual de la ruta + lo que lo destraba (pre-generado)
    foco = None
    try:
        import cumbre
        foco = cumbre.foco()
    except Exception:
        foco = None
    if isinstance(foco, dict):
        lineas += ["", "🎯 AQUÍ ESTAMOS hacia NED: %s (%s)" % (str(foco.get("titulo"))[:80], foco.get("estado"))]
        sig = (foco.get("siguiente_accion") or "").strip()
        bloqueo = (foco.get("bloqueo") or "").strip()
        if sig:
            lineas.append("· Siguiente: " + sig[:200])
        if bloqueo:
            lineas.append("· 📋 Prepara YA (lo que destraba el gate): " + bloqueo[:400])
    return "\n".join(lineas)


def main(argv):
    dias = 7
    if "--dias" in argv:
        try:
            dias = int(argv[argv.index("--dias") + 1])
        except (ValueError, IndexError):
            pass
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    print(esta_semana(dias))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
