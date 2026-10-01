#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""estado_caso.py — estado vivo del caso: qué ha llegado, qué se espera y qué va con retraso.

POR QUÉ (29-sep-26, plan «Vega al mando», Fase 2)
--------------------------------------------------
{{TITULAR}}: Vega «necesita saber absolutamente todo lo que pasa respecto a la enfermedad y estar muy al
día». Las piezas existían sueltas: el historial archiva los informes (desde hoy con un evento por
informe nuevo), el Tablero guarda lo que se espera de terceros con su plazo, y centinela_ned avisa
de los plazos vencidos. Nada las cruzaba: llegaba el informe que se estaba esperando y la tarjeta
seguía abierta, y el estado del caso solo existía en un fichero que se mantiene a mano.

QUÉ HACE (determinista, sin LLM, sin salir de la máquina)
----------------------------------------------------------
1. Lee los informes nuevos desde la última pasada (`_eventos.jsonl` del historial).
2. Por cada uno, busca esperas abiertas del Tablero cuyo título comparta palabras distintivas con
   el informe y PROPONE cerrarlas (aviso a {{TITULAR}} por salida.py). No cierra nada: un informe puede
   parecerse a una espera sin cumplirla.
3. Regenera `ESTADO-VIVO-DEL-CASO.md` junto al historial, en la zona clínica: informes de los
   últimos 14 días, esperas abiertas con quién, plazo y días de retraso, y próximos plazos.
   Describe; no interpreta ni aconseja.
4. (1-oct-26) Lista lo prometido con plazo en los chats del caso (promesas_caso.py) y avisa UNA vez
   de cada promesa vencida: es el «informe que falta» de la Fase 2.

Ganchos de test: BTP_HISTORIAL (raíz del historial), BTP_STATE_DIR.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import historial  # noqa: E402
import seguimiento  # noqa: E402

DIAS_RECIENTES = 14
ABIERTOS = ("esperando", "bloqueado", "por_confirmar", "en_curso")
# Palabras que aparecen en casi todos los nombres de informe y no distinguen nada.
_GENERICAS = {"informe", "informes", "resultado", "resultados", "analitica", "analiticas",
              "prueba", "pruebas", "documento", "hospital", "clinica", "consulta", "pdf",
              "sin", "fecha", "del", "los", "las", "para", "con", "por", "una"}


def _eventos_path():
    return os.path.join(historial.RAIZ, "_eventos.jsonl")


def _estado_path():
    return os.path.join(os.path.dirname(historial.RAIZ.rstrip(os.sep)), "ESTADO-VIVO-DEL-CASO.md")


def _marca_path():
    return os.path.join(seguimiento.STATE, "caso", "estado_caso.json")


def _leer_eventos():
    try:
        with open(_eventos_path(), encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except Exception:
        return []


def _palabras(texto):
    return {p for p in seguimiento._norm(texto or "").split()
            if len(p) >= 3 and p not in _GENERICAS and p not in seguimiento._CIERRE_STOP
            and not p.isdigit()}


def _esperas(hilos):
    return [h for h in hilos if h.get("estado") in ABIERTOS and h.get("estado") != "hecho"
            and (h.get("quien_espera") or h.get("estado") == "esperando")]


def _plazo(h):
    try:
        return date.fromisoformat(str(h.get("plazo") or "")[:10])
    except ValueError:
        return None


def casar(evento, esperas):
    """Esperas que el informe podría cumplir: comparten al menos una palabra distintiva."""
    pal = _palabras(evento.get("fichero", ""))
    out = []
    for h in esperas:
        comunes = pal & _palabras(h.get("titulo", "") + " " + str(h.get("siguiente_accion") or ""))
        if comunes:
            out.append((len(comunes), h))
    out.sort(key=lambda x: -x[0])
    return [h for _n, h in out[:2]]


def render(eventos, hilos, hoy=None):
    hoy = hoy or date.today()
    desde = (hoy - timedelta(days=DIAS_RECIENTES)).isoformat()
    lin = ["# Estado vivo del caso",
           "",
           "> Se regenera solo (tools/estado_caso.py). Describe qué ha llegado y qué se espera; no "
           "interpreta ni aconseja. Última actualización: %s." % datetime.now().strftime("%Y-%m-%d %H:%M"),
           "", "## Informes llegados en los últimos %d días" % DIAS_RECIENTES, ""]
    recientes = [e for e in eventos if str(e.get("ts", ""))[:10] >= desde]
    if recientes:
        lin += ["| Entró | Fecha del informe | Centro | Documento | Identidad |", "|---|---|---|---|---|"]
        for e in sorted(recientes, key=lambda x: x.get("ts", ""), reverse=True):
            lin.append("| %s | %s | %s | %s | %s |" % (str(e.get("ts", ""))[:10], e.get("fecha") or "?",
                                                     e.get("centro") or "?", e.get("fichero", ""),
                                                     e.get("identidad") or "?"))
    else:
        lin.append("Ninguno.")
    lin += ["", "## Esperas abiertas", ""]
    esperas = _esperas(hilos)
    if esperas:
        lin += ["| Qué se espera | De quién | Plazo | Retraso |", "|---|---|---|---|"]
        for h in sorted(esperas, key=lambda x: (_plazo(x) or date.max)):
            p = _plazo(h)
            retraso = ("%d días" % (hoy - p).days) if p and p < hoy else ("vence hoy" if p == hoy else "—")
            lin.append("| %s | %s | %s | %s |" % (h.get("titulo", ""), h.get("quien_espera") or "—",
                                                  p.isoformat() if p else "sin plazo", retraso))
    else:
        lin.append("Ninguna.")
    proximos = sorted(((p, h) for h in esperas for p in [_plazo(h)] if p and hoy <= p <= hoy + timedelta(days=7)),
                      key=lambda x: x[0])   # dos esperas con el mismo plazo no se comparan entre sí
    lin += ["", "## Plazos de los próximos 7 días", ""]
    lin += ["- %s · %s" % (p.isoformat(), h.get("titulo", "")) for p, h in proximos] or ["Ninguno."]
    lin += ["", "## Prometido en los chats del caso", ""]
    prom = _promesas(hoy)
    if prom:
        lin += ["| Qué | Quién | Dicho | Vence | Retraso |", "|---|---|---|---|---|"]
        for p, dias in prom:
            retraso = "%d días" % dias if dias > 0 else ("vence hoy" if dias == 0 else "—")
            lin.append("| %s | %s | %s · «%s» | %s | %s |" % (p.get("que", ""), p.get("quien", ""),
                                                             p.get("dicho", "")[:10], p.get("expresion", ""),
                                                             p.get("vence", ""), retraso))
    else:
        lin.append("Nada abierto.")
    lin += ["", "## Lo que no cuadra entre informes", "",
            "Ver INCONGRUENCIAS-DEL-CASO.md (se regenera solo con cada informe nuevo)."]
    return "\n".join(lin) + "\n"


def _promesas(hoy):
    """Promesas abiertas de promesas_caso.py. Fail-soft: sin ellas, el estado sigue saliendo."""
    try:
        import promesas_caso
        return promesas_caso.abiertas(hoy)
    except Exception:  # noqa: BLE001
        return []


def actualizar(avisar=False):
    eventos = _leer_eventos()
    hilos = seguimiento.load_seguimiento().get("hilos", [])
    try:
        marca = json.load(open(_marca_path(), encoding="utf-8"))
    except Exception:
        marca = {}
    ultimo = marca.get("ultimo_ts", "")
    nuevos = [e for e in eventos if str(e.get("ts", "")) > ultimo]
    propuestas = []
    for e in nuevos:
        for h in casar(e, _esperas(hilos)):
            propuestas.append({"informe": e.get("fichero", ""), "hilo": h.get("id"),
                               "titulo": h.get("titulo", "")})
    os.makedirs(os.path.dirname(_estado_path()), exist_ok=True)
    with open(_estado_path(), "w", encoding="utf-8") as fh:
        fh.write(render(eventos, hilos))
    if avisar and propuestas:
        lineas = ["📥 Ha llegado algo que se estaba esperando. ¿Cierro la espera?"]
        for p in propuestas[:5]:
            lineas.append("   · «%s» → espera «%s»" % (p["informe"][:70], p["titulo"][:70]))
        try:
            import salida
            salida.report_to_titular("\n".join(lineas), voz="sobria")
        except Exception:
            pass
    # Promesa vencida sin cumplir = «informe que falta» (Fase 2). Un aviso por promesa, nunca más.
    avisadas = set(marca.get("promesas_avisadas", []))
    vencidas = [p for p, dias in _promesas(date.today()) if dias > 0 and not p.get("sin_aviso")
                and p.get("id") not in avisadas]
    if avisar and vencidas:
        lineas = ["⏰ Prometido y no ha llegado (chats del caso):"]
        for p in vencidas[:5]:
            import promesas_caso
            lineas.append("   · %s, vencía el %s" % (promesas_caso.texto_sin_pii(p)[:90], p.get("vence", "")))
        lineas.append("Si ya llegó: python3 tools/promesas_caso.py --cumplida <id>")
        try:
            import salida
            salida.report_to_titular("\n".join(lineas), voz="sobria")
            marca["promesas_avisadas"] = sorted(avisadas | {p["id"] for p in vencidas})
        except Exception:
            pass
    if nuevos or marca.get("promesas_avisadas", []) != sorted(avisadas):
        if nuevos:
            marca["ultimo_ts"] = max(str(e.get("ts", "")) for e in nuevos)
        os.makedirs(os.path.dirname(_marca_path()), exist_ok=True)
        with open(_marca_path(), "w", encoding="utf-8") as fh:
            json.dump(marca, fh)
    return {"nuevos": len(nuevos), "propuestas": propuestas, "estado": _estado_path()}


def main(argv):
    r = actualizar(avisar="--avisar" in argv)
    print("informes nuevos: %d · propuestas de cierre: %d · estado: %s"
          % (r["nuevos"], len(r["propuestas"]), os.path.basename(r["estado"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
