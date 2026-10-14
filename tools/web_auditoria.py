#!/usr/bin/env python3
"""web_auditoria.py — la auditoría semanal de la web con Umami. Mide; no decide ni cambia nada.

POR QUÉ EXISTE (14-oct-2026). {{TITULAR}}: *«una vez a la semana deberías hacer una auditoría con
umami de la web y ver qué cambios harías… e ir mejorándola»*. El comité `diseno` lo aprobó con
matiz, y ella aceptó su dictamen:

  · con ~300 visitantes al día casi nada de Umami es firme, así que cada señal lleva un MÍNIMO
    por debajo del cual no hay propuesta, solo «visto, sin señal»;
  · «ir mejorándola» sin umbral hace que el sistema se invente cambios: por eso una semana puede
    terminar en «sin cambios», y eso es un buen resultado;
  · como mucho 3 propuestas por semana las elige la pasada semanal (modo guardián de `tecnico`);
    esta herramienta solo le da las señales medidas.

QUÉ MIDE (7 días contra la media semanal de las 4 semanas previas)
  · Error404: ≥5 en la semana → hay rutas rotas que alguien visita.
  · Donar ÷ visitantes: cae >30 % y la base previa es de ≥20 clics por semana.
  · Origen del tráfico: una fuente nueva con ≥5 % de las visitas, o una conocida a >3× su media.
  · Contacto y Bio-link: a cero dos semanas seguidas → probable rotura.

QUÉ NO MIDE TODAVÍA, Y LO DICE: los embudos por página (de dónde a dónde se navega), la salida
en las páginas 3D y el rebote por dispositivo. La API, tal como la usa `umami.py`, no los da
sin filtros que aún no se han comprobado. Se listan en `no_medido` para que nadie lea su
ausencia como «todo bien».

DÓNDE CORRE. Umami solo se puede leer desde la terminal de {{TITULAR}} o desde launchd (las sesiones
de Claude Code no leen el Llavero). La corre el guardián (`com.btp.web-guardian`) los lunes y la
guarda en `tools/state/web_guardian/auditoria-AAAA-Wss.json`.

Uso:
  python3 tools/web_auditoria.py           # audita la última semana y la guarda
  python3 tools/web_auditoria.py --seco    # audita e imprime, sin guardar
"""
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402

DIR = os.path.join(_casa.state_dir(), "web_guardian")
MIN_404 = 5
CAIDA_DONAR = 0.30
BASE_DONAR = 20
CUOTA_FUENTE_NUEVA = 0.05
PICO_FUENTE = 3.0
EVENTOS_VIGILADOS = ("Contacto", "Bio-link")
NO_MEDIDO = [
    "embudo de la navegación por página (de qué página a cuál se pasa)",
    "salida en las páginas 3D (/mapa-metastasis, /reservorio) en móvil",
    "rebote y tiempo por dispositivo (móvil frente a escritorio)",
]


def _api_real(ruta, dias):
    """Lee Umami. `ruta` es la parte fija («/stats», «/metrics?type=event…»); aquí se añade el
    rango. Devuelve None si no hay clave o falla: «sin datos», nunca «todo bien»."""
    import umami
    key = umami.get_secret("btp-umami-api", umami.SECRETS, "api_key")
    if not key or str(key).startswith("PEGA"):
        return None
    fin = int(time.time() * 1000) - (0 if dias == 7 else 7 * 86400 * 1000)
    inicio = fin - dias * 86400 * 1000
    sep = "&" if "?" in ruta else "?"
    try:
        return umami.api("/websites/%s%s%sstartAt=%d&endAt=%d" % (
            umami._wid_de_secrets(None), ruta, sep, inicio, fin), key)
    except Exception:
        return None


def _metricas(api, tipo, dias):
    filas = api("/metrics?type=%s&limit=50" % tipo, dias)
    if not isinstance(filas, list):
        return None
    return {str(r.get("x") or "(directo)"): int(r.get("y") or 0) for r in filas if isinstance(r, dict)}


def _visitantes(api, dias):
    s = api("/stats", dias)
    if not isinstance(s, dict):
        return None
    v = s.get("visitors")
    if isinstance(v, dict):
        v = v.get("value")
    return int(v or 0)


def _senal(clave, titulo, detalle):
    return {"clave": clave, "titulo": titulo, "detalle": detalle}


def auditar(hoy=None, _api=_api_real, semana_anterior=None):
    """La auditoría de la semana que acaba en `hoy`. `semana_anterior` = {evento: n} de la
    semana previa, para la regla de «a cero dos semanas» (si no se pasa, se lee de lo guardado)."""
    hoy = hoy or datetime.date.today()
    anio, num, _ = hoy.isocalendar()
    res = {"semana": "%d-W%02d" % (anio, num), "fecha": hoy.isoformat(), "con_datos": False,
           "senales": [], "medido": {}, "no_medido": list(NO_MEDIDO)}
    vis, vis_prev = _visitantes(_api, 7), _visitantes(_api, 28)
    ev, ev_prev = _metricas(_api, "event", 7), _metricas(_api, "event", 28)
    ref, ref_prev = _metricas(_api, "referrer", 7), _metricas(_api, "referrer", 28)
    if vis is None or ev is None or not vis:
        res["resumen"] = "sin datos de Umami esta semana: no se puede auditar (no es «todo bien»)"
        return res
    res["con_datos"] = True
    ev_prev, ref_prev = ev_prev or {}, ref_prev or {}
    res["medido"] = {"visitantes": vis, "visitantes_media_previa": round((vis_prev or 0) / 4),
                     "eventos": ev, "referrers": dict(sorted((ref or {}).items(), key=lambda x: -x[1])[:10])}

    if ev.get("Error404", 0) >= MIN_404:
        res["senales"].append(_senal("error-404", "Hay páginas rotas que la gente visita",
                                     "%d visitas a páginas que no existen esta semana." % ev["Error404"]))

    base_donar = ev_prev.get("Donar", 0) / 4.0
    if base_donar >= BASE_DONAR and vis_prev:
        tasa = ev.get("Donar", 0) / float(vis)
        tasa_prev = ev_prev.get("Donar", 0) / float(vis_prev)
        if tasa_prev and (tasa_prev - tasa) / tasa_prev > CAIDA_DONAR:
            res["senales"].append(_senal(
                "donar-cae", "Menos gente pulsa «Donar»",
                "%.1f %% de los visitantes esta semana frente a %.1f %% de media (-%d %%)." % (
                    100 * tasa, 100 * tasa_prev, round(100 * (tasa_prev - tasa) / tasa_prev))))

    total_ref = sum((ref or {}).values()) or 1
    for fuente, n in (ref or {}).items():
        media = ref_prev.get(fuente, 0) / 4.0
        if fuente not in ref_prev and n / float(total_ref) >= CUOTA_FUENTE_NUEVA:
            res["senales"].append(_senal("fuente-nueva", "Llega tráfico de un sitio nuevo",
                                         "%s: %d visitas (%d %% del tráfico con origen)." % (
                                             fuente, n, round(100 * n / float(total_ref)))))
        elif media and n > PICO_FUENTE * media:
            res["senales"].append(_senal("fuente-pico", "Un origen de tráfico se ha disparado",
                                         "%s: %d visitas frente a %d de media semanal." % (
                                             fuente, n, round(media))))

    if semana_anterior is None:
        semana_anterior = _eventos_guardados(hoy - datetime.timedelta(days=7))
    for e in EVENTOS_VIGILADOS:
        if ev.get(e, 0) == 0 and semana_anterior is not None and semana_anterior.get(e, 0) == 0:
            res["senales"].append(_senal("evento-a-cero", "«%s» lleva dos semanas a cero" % e,
                                         "Puede ser que el botón o el formulario se haya roto."))

    res["resumen"] = ("%d señal(es) para la pasada semanal" % len(res["senales"]) if res["senales"]
                      else "visto, sin señal: sin cambios que proponer esta semana")
    return res


def _ruta(hoy):
    anio, num, _ = hoy.isocalendar()
    return os.path.join(DIR, "auditoria-%d-W%02d.json" % (anio, num))


def _eventos_guardados(dia):
    try:
        with open(_ruta(dia), encoding="utf-8") as f:
            return (json.load(f).get("medido") or {}).get("eventos")
    except (OSError, ValueError):
        return None


def hecha_esta_semana(hoy=None):
    return os.path.exists(_ruta(hoy or datetime.date.today()))


def guardar(res):
    os.makedirs(DIR, exist_ok=True)
    ruta = _ruta(datetime.date.fromisoformat(res["fecha"]))
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ruta)
    return ruta


def main(argv):
    res = auditar()
    print("Semana %s · %s" % (res["semana"], res["resumen"]))
    for s in res["senales"]:
        print("  · %s: %s" % (s["titulo"], s["detalle"]))
    print("  No medido aún: %s" % "; ".join(res["no_medido"]))
    if "--seco" not in argv and res["con_datos"]:
        print("  guardado en %s" % guardar(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
