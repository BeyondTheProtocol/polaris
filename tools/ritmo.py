#!/usr/bin/env python3
"""tools/ritmo.py — Pulso de la campaña de un vistazo: tráfico web y fondos hacia la meta.

Para qué: la campaña y los fondos son el combustible del acceso a la vacuna (eje
"capacidad del sistema"). Esto le da a {{TITULAR}} el RITMO de un vistazo: "vas a ~N
visitas/día, tendencia ↑/↓" y "a este paso llegas a la meta el ~DD/MM".

Aritmética SIMPLE a propósito (media móvil 7d + tendencia + proyección lineal), NO un
foundation model: para series cortas y ruidosas (semanas) una media móvil acierta igual
y cuesta cero — evaluado 25/6/26, TimesFM = matar moscas a cañonazos hoy. Cuando la web
tenga ~3 meses de histórico, reabrir TimesFM como piloto (disparador objetivo).

Egress-cero: lee Umami (ya conectado, tools/umami.py) y un registro LOCAL opcional de
donaciones (tools/state/donaciones.json). No envía, no paga, no publica. Sin LLM (~0 tokens).
Lo consume El Observatorio (tarjeta "Ritmo de la campaña"); también se puede correr a mano.

Registro de fondos (opcional, manual mientras GoFundMe/Stripe no tengan API):
  tools/state/donaciones.json
  {"meta_eur": 50000, "registro": [{"fecha":"2026-06-01","acumulado_eur": 1200}, ...]}

Uso:
  python3 tools/ritmo.py              # pulso legible
  python3 tools/ritmo.py --json       # JSON (lo usa el Observatorio)
  python3 tools/ritmo.py 14           # ventana de 14 días para el tráfico
"""
import os, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
DONA = os.path.join(STATE, "donaciones.json")


def _media(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else 0.0


def _calc_web(serie):
    """Función PURA (testeable): serie [{fecha,vistas,visitas}] -> dict de pulso.
    Compara media diaria de los últimos 7 días vs los 7 anteriores."""
    if not serie:
        return {"estado": "sin_datos", "motivo": "sin clave de Umami o sin tráfico todavía"}
    vistas = [d.get("vistas") or 0 for d in serie]
    n = len(vistas)
    ult7 = vistas[-7:]
    prev7 = vistas[-14:-7] if n >= 8 else []
    avg7 = _media(ult7)
    avgp = _media(prev7)
    if avgp > 0:
        pct = round((avg7 - avgp) / avgp * 100)
    else:
        pct = None
    if pct is None:
        tend = "—"
    elif pct >= 8:
        tend = "sube"
    elif pct <= -8:
        tend = "baja"
    else:
        tend = "plano"
    return {
        "estado": "ok",
        "dias": n,
        "vistas_dia": round(avg7, 1),
        "vistas_7d": int(sum(ult7)),
        "tendencia": tend,
        "tendencia_pct": pct,
        "proy_7d": int(round(avg7 * 7)),
        "spark": [int(v) for v in vistas[-14:]],
        "fiable": n >= 14,   # con <2 semanas la tendencia es orientativa
    }


def pulso_web(days=28):
    try:
        import umami
        serie = umami.serie_diaria(days=days)
    except Exception as e:
        return {"estado": "sin_datos", "motivo": "Umami no disponible (%s)" % str(e)[:60]}
    return _calc_web(serie)


def _parse_fecha(s):
    try:
        return time.strptime(str(s)[:10], "%Y-%m-%d")
    except Exception:
        return None


def _calc_fondos(cfg):
    """Función PURA (testeable): registro acumulado -> ritmo €/día y ETA a la meta."""
    if not cfg:
        return {"estado": "sin_registro",
                "motivo": "registro manual pendiente (tools/state/donaciones.json)"}
    reg = [r for r in (cfg.get("registro") or []) if _parse_fecha(r.get("fecha"))]
    reg.sort(key=lambda r: r["fecha"])
    if not reg:
        return {"estado": "sin_registro", "motivo": "registro vacío"}
    acum = reg[-1].get("acumulado_eur") or 0
    meta = cfg.get("meta_eur")
    out = {"estado": "ok", "acumulado_eur": round(acum), "meta_eur": meta, "n_puntos": len(reg)}
    # ritmo €/día entre el primer y el último punto (si hay span de días)
    if len(reg) >= 2:
        t0 = time.mktime(_parse_fecha(reg[0]["fecha"]))
        t1 = time.mktime(_parse_fecha(reg[-1]["fecha"]))
        dias = max(1, round((t1 - t0) / 86400))
        delta = acum - (reg[0].get("acumulado_eur") or 0)
        ritmo = delta / dias
        out["ritmo_dia_eur"] = round(ritmo)
        if meta and ritmo > 0 and acum < meta:
            faltan_dias = (meta - acum) / ritmo
            eta = time.localtime(t1 + faltan_dias * 86400)
            out["eta"] = time.strftime("%d/%m", eta)
        elif meta and acum >= meta:
            out["eta"] = "meta alcanzada 🎉"
    return out


def pulso_fondos():
    if not os.path.exists(DONA):
        return _calc_fondos(None)
    try:
        cfg = json.load(open(DONA))
    except Exception as e:
        return {"estado": "sin_registro", "motivo": "donaciones.json ilegible (%s)" % str(e)[:50]}
    return _calc_fondos(cfg)


def estado(days=28):
    """Lo que consume el Observatorio (estado_ritmo)."""
    return {"generado": time.strftime("%Y-%m-%d %H:%M:%S"),
            "web": pulso_web(days), "fondos": pulso_fondos()}


def main():
    argv = sys.argv[1:]
    as_json = False
    if argv and argv[0] in ("--json", "-j"):
        as_json, argv = True, argv[1:]
    days = int(argv[0]) if argv and argv[0].isdigit() else 28
    st = estado(days)
    if as_json:
        print(json.dumps(st, ensure_ascii=False, indent=2)); return
    w, f = st["web"], st["fondos"]
    print("=== Ritmo de la campaña ===")
    if w["estado"] == "ok":
        arrow = {"sube": "↑", "baja": "↓", "plano": "→", "—": "·"}[w["tendencia"]]
        extra = "" if w["tendencia_pct"] is None else " (%+d%%)" % w["tendencia_pct"]
        print("Web · %.1f vistas/día  %s %s%s%s" % (
            w["vistas_dia"], arrow, w["tendencia"], extra,
            "" if w["fiable"] else "  [orientativo: <2 semanas de datos]"))
        print("     últimos 7 días: %d vistas · proyección próx. 7 días: ~%d" % (w["vistas_7d"], w["proy_7d"]))
    else:
        print("Web · sin datos (%s)" % w.get("motivo", ""))
    if f["estado"] == "ok":
        line = "Fondos · %s €" % f["acumulado_eur"]
        if f.get("meta_eur"):
            line += " de %s €" % f["meta_eur"]
        if f.get("ritmo_dia_eur") is not None:
            line += " · ritmo %s €/día" % f["ritmo_dia_eur"]
        if f.get("eta"):
            line += " · a este paso: %s" % f["eta"]
        print(line)
    else:
        print("Fondos · sin registro (%s)" % f.get("motivo", ""))


if __name__ == "__main__":
    main()
