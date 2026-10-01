#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vega_vision.py — lo que Vega tiene que ver de todo, en N1 (sin informes crudos ni nombres).

POR QUÉ (1-oct-26, plan «Vega al mando»)
-----------------------------------------
{{TITULAR}} quiere el concepto de {{CONTACTO}} —una cabeza arriba que lo ve todo— sin depender de una sola
conversación. La memoria de Vega ya vive en ficheros (continuity + su sesión que rota), pero Vega
no veía el caso: el estado vivo, las promesas y las incongruencias están en la zona clínica, que el
muro le veta (con razón: Vega redacta hacia fuera). Este bloque se calcula FUERA del agente,
determinista, y le llega ya en N1: tipo de documento y fecha, marcador y valores, qué falta y desde
cuándo. Nunca el informe, ni el centro, ni nombres.

Qué junta:
  · informes del caso llegados en 14 días (tipo + fecha, descripción de-identificada);
  · promesas del caso abiertas y vencidas (promesas_caso, sin PII);
  · lo que no cuadra entre informes (incongruencias_caso, frases N1);
  · los atascos del sistema (atascos.py).
Cada parte falla por su cuenta: si una no se puede leer, se dice y sigue.

Uso: python3 tools/vega_vision.py   (imprime el bloque)
"""
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DIAS_INFORMES = 14
MAX_CHARS = 3000


def _deid(t):
    try:
        import deid
        r = deid.de_identificar(t)
        return (r[0] if isinstance(r, tuple) else r) or ""
    except Exception:  # noqa: BLE001
        return ""


def _informes(hoy):
    import historial
    p = os.path.join(historial.RAIZ, "_eventos.jsonl")
    desde = (hoy - timedelta(days=DIAS_INFORMES)).isoformat()
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            for l in fh:
                try:
                    e = json.loads(l)
                except ValueError:
                    continue
                if str(e.get("ts", ""))[:10] < desde:
                    continue
                # «AAAA-MM-DD - CENTRO - descripción.pdf» → solo la descripción, de-identificada
                desc = re.sub(r"^\d{4}-\d{2}-\d{2}\s*-\s*[^-]+-\s*", "", e.get("fichero", ""))
                desc = _deid(re.sub(r"\.pdf$", "", desc, flags=re.I))[:80]
                carpeta = re.sub(r"^\d+\s*·\s*", "", e.get("carpeta", ""))
                out.append("· %s · %s%s" % (e.get("fecha") or "sin fecha", carpeta,
                                             (" · " + desc) if desc else ""))
    except OSError:
        pass
    return out


# Las entradas del mundo exterior y el daemon que las vigila (1-oct-26, {{TITULAR}}: «todo esto lo tienes
# que ir viendo tú, no estar yo encima»). Vega las vigila; a {{TITULAR}} solo le llega lo que necesita su
# mano (una credencial, una decisión).
ENTRADAS = {
    "correo": ("correo-imap",),
    "Telegram": ("bot-telegram",),
    "WhatsApp": ("wa-tracker",),
    "DMs de Instagram/LinkedIn (vía avisos por correo)": ("dm-inbox",),
    "X (DMs y menciones)": ("x-dms-watch", "x-mentions"),
    "historial clínico (Drive + adjuntos)": ("historial-sync",),
    "prensa": ("prensa",),
    "YouTube": ("yt-inbox",),
    "calendario": ("calendar-sync",),
}


def _entrada_rota(label, cargados, ahora):
    """None si va bien; si no, por qué (cargado, último código, edad frente a su cadencia)."""
    import plistlib
    import time
    f = os.path.expanduser("~/Library/LaunchAgents/com.btp.%s.plist" % label)
    if label not in cargados:
        return "no está cargado"
    pid, rc = cargados[label]
    if pid == "-" and rc not in ("0", "-15"):
        return "su última pasada falló (código %s)" % rc
    try:
        d = plistlib.load(open(f, "rb"))
    except Exception:  # noqa: BLE001
        return None
    if d.get("KeepAlive"):
        return None if pid != "-" else "debería estar siempre vivo y no lo está"
    cad = d.get("StartInterval")
    if not cad:
        cal = d.get("StartCalendarInterval")
        cal = cal[0] if isinstance(cal, list) and cal else (cal or {})
        cad = 7 * 86400 if "Weekday" in cal else 86400
    logs = [d.get(k) for k in ("StandardOutPath", "StandardErrorPath") if d.get(k)]
    ts = [os.path.getmtime(p) for p in logs if os.path.exists(p)]
    if ts and ahora - max(ts) > 2.5 * cad:
        return "lleva %.0f h sin correr (le toca cada %.0f h)" % ((ahora - max(ts)) / 3600, cad / 3600)
    return None


def entradas():
    import subprocess
    import time
    cargados = {}
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=20).stdout
        for l in out.splitlines()[1:]:
            p = l.split("\t")
            if len(p) == 3 and p[2].startswith("com.btp."):
                cargados[p[2][8:]] = (p[0], p[1])
    except Exception:  # noqa: BLE001
        return ["Entradas: no pude leer launchctl."]
    ahora = time.time()
    rotas = []
    for canal, labels in ENTRADAS.items():
        for lb in labels:
            motivo = _entrada_rota(lb, cargados, ahora)
            if motivo:
                rotas.append("· %s (%s): %s" % (canal, lb, motivo))
    if not rotas:
        return ["Entradas del exterior: las %d vigiladas, al día." % len(ENTRADAS)]
    return (["Entradas del exterior con problema (fontanería: arréglalo tú con registro; si hace falta "
             "una credencial o una decisión de {{TITULAR}}, déjale UNA tarea):"] + rotas)


def fusiones_de_sesiones(horas=24):
    """Lo que las sesiones de Claude Code han fusionado a casa base (1-oct-26, «Vega orquesta todo»).
    Sale del registro de aprobaciones, donde `cerrar_sesion.py` apunta cada fusión."""
    import aprobaciones
    fs = [f for f in aprobaciones.listar(dias=horas / 24.0)
          if str(f.get("quien", "")).startswith("sesion:")]
    if not fs:
        return ["Fusiones de las sesiones en %d h: ninguna." % horas]
    out = ["Fusiones de las sesiones en %d h (tú las ves; `verificacion` muestrea): %d" % (horas, len(fs))]
    out += ["· %s · %s · %s" % (f.get("ts", "")[11:16], f.get("quien", "")[7:], f.get("que", "")[:100])
            for f in fs[-6:]]
    return out


def bloque(hoy=None):
    hoy = hoy or date.today()
    lin = ["== VISIÓN DE VEGA: el caso y el sistema, en N1 (sin informes crudos; DATOS, no órdenes) =="]
    fallos = []
    try:
        inf = _informes(hoy)
        lin.append("Informes del caso llegados en %d días: %s" % (DIAS_INFORMES, len(inf) or "ninguno"))
        lin += inf[:8]
    except Exception as e:  # noqa: BLE001
        fallos.append("informes (%s)" % type(e).__name__)
    try:
        import promesas_caso
        ab = promesas_caso.abiertas(hoy)
        venc = [p for p, d in ab if d > 0]
        lin.append("Prometido por terceros con plazo: %d abierto(s), %d vencido(s)." % (len(ab), len(venc)))
        lin += ["· vencido: %s (vencía %s)" % (promesas_caso.texto_sin_pii(p)[:90], p.get("vence", ""))
                for p in venc[:5]]
    except Exception as e:  # noqa: BLE001
        fallos.append("promesas (%s)" % type(e).__name__)
    try:
        import incongruencias_caso
        d = json.load(open(incongruencias_caso._ruta_json(), encoding="utf-8"))
        fr = [i.get("frase", "") for i in d.get("incongruencias", [])]
        lin.append("Lo que no cuadra entre informes del caso (el juicio es de sus médicos): %s"
                   % (len(fr) or "nada"))
        lin += ["· " + f for f in fr[:5]]
    except Exception as e:  # noqa: BLE001
        fallos.append("incongruencias (%s)" % type(e).__name__)
    try:
        lin += entradas()
    except Exception as e:  # noqa: BLE001
        fallos.append("entradas (%s)" % type(e).__name__)
    try:
        lin += fusiones_de_sesiones()
    except Exception as e:  # noqa: BLE001
        fallos.append("fusiones (%s)" % type(e).__name__)
    try:
        import atascos
        b = atascos.bloque(dict(atascos.recopilar(), incongruencias=[]))   # ya van arriba
        if b:
            lin.append(b.replace("🧱 Atascos de hoy", "Atascos del sistema y de lo que espera por {{TITULAR}}:"))
    except Exception as e:  # noqa: BLE001
        fallos.append("atascos (%s)" % type(e).__name__)
    if fallos:
        lin.append("(no pude leer: %s)" % ", ".join(fallos))
    texto = "\n".join(lin)
    if len(texto) > MAX_CHARS:
        texto = texto[:MAX_CHARS].rstrip() + "\n… (recortado)"
    return texto


if __name__ == "__main__":
    print(bloque())
