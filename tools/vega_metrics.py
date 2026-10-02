#!/usr/bin/env python3
"""tools/vega_metrics.py — F4.4: observabilidad de Vega (determinista, read-only, $0).

Para que se vea de un vistazo si Vega está cumpliendo: ¿cuánto cierra?, ¿cuánto se le cae?, ¿cuánto
deja a un clic?, ¿cuántos hilos persigue?, ¿están sus daemons vivos? Lo lee El Observatorio (y {{TITULAR}}
a demanda). 0 LLM, 0 egress; deriva de `seguimiento` + el estado de `persecucion`/`centinela` + los
heartbeats. Resuelve a casa base.

Uso:
  python3 tools/vega_metrics.py            # métricas en JSON
  python3 tools/vega_metrics.py --texto    # resumen legible
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
PERS_DIR = os.path.join(STATE, "persecucion")
HB_DIR = os.path.join(STATE, "heartbeat")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Daemons de Vega y su cadencia (h) para el "vivo" por frescura de heartbeat.
_DAEMONS = (("centinela-ned", 0.5), ("asistente", 27), ("calendar-sync", 27))


def _fecha(v):
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def _edad_hb_h(agente):
    try:
        with open(os.path.join(HB_DIR, agente + ".json"), encoding="utf-8") as f:
            ts = (json.load(f) or {}).get("ts")
    except Exception:
        return None
    if not ts:
        return None
    ts = ts.strip()
    try:
        if ts.endswith("Z"):
            dt, ref = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"), datetime.utcnow()
        elif "T" in ts:
            dt, ref = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"), datetime.now()
        else:
            dt, ref = datetime.strptime(ts[:16], "%Y-%m-%d %H:%M"), datetime.now()
        return max(0.0, (ref - dt).total_seconds() / 3600.0)
    except Exception:
        return None


MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def proactividad(dias=7):
    """¿Se adelanta Vega sin hacer ruido? (2-oct-26, plan «Vega aprende y se adelanta», eslabón 5).
      · anticipacion_pct: de las acciones con fecha que derivó, cuántas se propusieron ≥48 h antes
        del plazo (registro de acciones_datadas, sombra o activo).
      · ya_pedidas: derivadas cuya fecha ya estaba en un hilo que NO es derivado (lo dijo ella antes).
      · ruido: hilos derivados que se cerraron como «abandonado» (no aplicaba).
      · avisos/día entregados y aplazados, reacciones positivas/negativas y hilos con fecha sin plazo.
    Determinista, solo lectura."""
    out = {}
    reg = []
    try:
        with open(os.path.join(STATE, "vega", "acciones_sombra.jsonl"), encoding="utf-8") as fh:
            reg = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        pass
    medibles = [r for r in reg if r.get("ts") and r.get("plazo")]
    antes = 0
    for r in medibles:
        try:
            h = (datetime.fromisoformat(str(r["plazo"])[:10]) - datetime.fromisoformat(r["ts"])).total_seconds() / 3600
        except ValueError:
            continue
        antes += 1 if h >= 48 else 0
    out["derivadas"] = len(reg)
    out["anticipacion_pct"] = round(100.0 * antes / len(medibles)) if medibles else None
    try:
        import seguimiento as sg
        hilos = sg.load_seguimiento().get("hilos", [])
    except Exception:
        hilos = []
    ajenos = " ".join((h.get("titulo") or "").lower() for h in hilos if h.get("origen") != "derivado")
    ya = 0
    for r in reg:
        f = _fecha(r.get("plazo"))
        if f and ("%d-%s" % (f.day, MESES_CORTOS[f.month - 1])) in ajenos:
            ya += 1
    out["ya_pedidas"] = ya
    out["ruido"] = sum(1 for h in hilos if h.get("origen") == "derivado" and h.get("estado") == "abandonado")
    try:
        import perfil_vega
        hoy = date.today()
        vals = [v for v in (perfil_vega._veredictos_salida(
            (hoy - timedelta(days=i)).isoformat()) for i in range(dias)) if v]
        out["avisos_dia"] = ({"entregados": round(sum(v[0] for v in vals) / len(vals), 1),
                              "aplazados": round(sum(v[1] for v in vals) / len(vals), 1)} if vals else None)
    except Exception:
        out["avisos_dia"] = None
    reac = {"positiva": 0, "negativa": 0, "otra": 0}
    try:
        corte = (date.today() - timedelta(days=dias)).isoformat()
        with open(os.path.join(STATE, "vega", "reacciones.jsonl"), encoding="utf-8") as fh:
            for l in fh:
                r = json.loads(l)
                if str(r.get("ts", ""))[:10] >= corte:
                    reac[r.get("senal", "otra")] = reac.get(r.get("senal", "otra"), 0) + 1
    except (OSError, ValueError):
        pass
    out["reacciones_%dd" % dias] = reac
    try:
        import acciones_datadas
        out["con_fecha_sin_plazo"] = len(acciones_datadas.sin_plazo_con_fecha())
    except Exception:
        out["con_fecha_sin_plazo"] = None
    return out


def metricas():
    out = {"ts": datetime.now().isoformat(timespec="seconds")}
    # Hilos (throughput de Vega)
    try:
        import seguimiento as sg
        data = sg.recopilar()
        items = data.get("items", [])
        hoy = date.today()
        cerrados = [i for i in items if str(i.get("estado", "")).lower() in ("hecho", "done", "cerrado")]
        try:
            cayendo = len(sg.perseguir(ejecutar=False).get("salidas", []))
        except Exception:
            cayendo = None
        out["hilos"] = {
            "total": len(items),
            "cayendo": cayendo,
            "cerrados_hoy": sum(1 for i in cerrados if _fecha(i.get("hecho_el")) == hoy),
            "cerrados_7d": sum(1 for i in cerrados
                               if _fecha(i.get("hecho_el")) and (hoy - _fecha(i.get("hecho_el"))).days <= 7),
            "borradores_a_un_clic": len(data.get("pendientes_ok", [])),
        }
    except Exception as e:
        out["hilos_error"] = "%r" % e
    # Persecución (drive-to-close en curso)
    try:
        out["persecucion_en_curso"] = len([f for f in os.listdir(PERS_DIR) if f.endswith(".json")])
    except OSError:
        out["persecucion_en_curso"] = 0
    # Daemons de Vega vivos (frescura de heartbeat)
    out["daemons"] = {}
    for ag, cad in _DAEMONS:
        edad = _edad_hb_h(ag)
        out["daemons"][ag] = {"edad_h": round(edad, 2) if edad is not None else None,
                              "vivo": bool(edad is not None and edad <= cad)}
    try:
        out["proactividad"] = proactividad()
    except Exception as e:  # noqa: BLE001
        out["proactividad_error"] = "%r" % e
    return out


def _texto(m):
    h = m.get("hilos", {})
    L = ["📊 Vega:",
         "· hilos: %s total · %s cayendo · %s cerrados hoy · %s en 7d" % (
             h.get("total", "?"), h.get("cayendo", "?"), h.get("cerrados_hoy", "?"), h.get("cerrados_7d", "?")),
         "· %s borrador(es) a un clic · %s en persecución" % (
             h.get("borradores_a_un_clic", "?"), m.get("persecucion_en_curso", "?"))]
    vivos = [a for a, d in m.get("daemons", {}).items() if d.get("vivo")]
    muertos = [a for a, d in m.get("daemons", {}).items() if not d.get("vivo")]
    L.append("· daemons vivos: %s%s" % (", ".join(vivos) or "ninguno",
                                        ("  ⚠️ caídos: " + ", ".join(muertos)) if muertos else ""))
    p = m.get("proactividad") or {}
    if p:
        a = p.get("avisos_dia") or {}
        r = p.get("reacciones_7d") or {}
        L.append("· se adelanta: %s derivadas · %s%% con ≥48 h · %s ya las había pedido ella · %s ruido"
                 % (p.get("derivadas", "?"), p.get("anticipacion_pct", "?"), p.get("ya_pedidas", "?"),
                    p.get("ruido", "?")))
        L.append("· avisos/día: %s entregados, %s aplazados · reacciones 7d: 👍 %s 👎 %s · %s hilos con fecha sin plazo"
                 % (a.get("entregados", "?"), a.get("aplazados", "?"), r.get("positiva", 0),
                    r.get("negativa", 0), p.get("con_fecha_sin_plazo", "?")))
    return "\n".join(L)


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    m = metricas()
    print(_texto(m) if "--texto" in argv else json.dumps(m, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
