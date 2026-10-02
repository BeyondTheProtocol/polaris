#!/usr/bin/env python3
"""tools/acciones_datadas.py — las acciones con fecha que Vega propone ANTES de que {{TITULAR}} las pida.

POR QUÉ (2-oct-2026, plan «Vega aprende y se adelanta», eslabón 3, aprobado por {{TITULAR}} el 1-oct)
-----------------------------------------------------------------------------------------------
Desde el 3-jul, `asistente.md` le pedía a Vega derivar de las reservas el check-in, la asistencia y
el plan del día. Era solo prompt. Medido el 1-oct: el cambio de apartamento del 2-oct estaba en el
Tablero con `plazo=None` (y `severidad()` solo escala con plazo ISO), y el fin de la cancelación
gratis (3-oct) y el segundo cambio (4-oct) no tenían hilo. Esto lo hace determinista.

QUÉ DERIVA (de tools/state/reservas.json, solo fechas futuras)
-------------------------------------------------------------
  · Estancias (checkin/checkout): cambio de alojamiento cuando una salida coincide con otra entrada;
    si no, la entrada. Plazo = ese día.
  · Fin de la cancelación gratis: campo `cancelacion_gratis_hasta` (ISO) o, si no está, la frase
    «cancelación gratis hasta D-mes» de la nota. Es una DECISIÓN de {{TITULAR}}: el hilo solo la pone delante.
  · Vuelos y trenes con `fecha_salida` (ISO): check-in online (T-1) y la pregunta de la asistencia
    (T-3; regla suya del 24-sep: preguntar en cada viaje, no darla por hecha ni descartarla).
Y lista (no toca) los hilos abiertos con fecha en el título y sin plazo: no escalan nunca.

NO deriva recordatorios de citas (ya los hace la pasada follonera) ni seguimientos de promesas del
caso (`promesas_caso.py` ya las deja en el buzón de Vega, y hablar con terceros del caso es nivel B).

GARANTÍAS
---------
  · Solo escribe en local, vía `seguimiento.add_hilo` con id `derivado-…`, `origen="derivado"`
    (fuera de ORIGENES_AUTONOMOS_AVISO: no avisa al nacer; sale en el parte y escala por plazo).
  · Nunca toca un hilo que ya exista, esté como esté: si {{TITULAR}} lo cerró o lo descartó, se queda así.
  · MODO SOMBRA por defecto (7 días, `feedback-vega-supervisar-y-pulir-mensajes`): apunta las
    candidatas en state/vega/acciones_sombra.jsonl sin crear hilos. Se activa con
    {"modo": "activo"} en state/vega/acciones_datadas.json.

Uso:
  python3 tools/acciones_datadas.py            # qué derivaría (seco)
  python3 tools/acciones_datadas.py --write    # sombra: apunta; activo: crea los hilos
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento  # noqa: E402

STATE = os.environ.get("BTP_STATE_DIR") or seguimiento.STATE
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sep": 9, "oct": 10, "nov": 11, "dic": 12}
RE_CANCEL = re.compile(r"cancelaci[oó]n gratis hasta (?:el )?(\d{1,2})[-/ ](?:de )?([a-záéíóú]{3})", re.I)
RE_FECHA_TITULO = re.compile(r"\b(\d{1,2})[-/ ](?:de )?(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\b"
                             r"|\b(\d{4}-\d{2}-\d{2})\b|\bmañana\b|\bhoy\b", re.I)
OBJ_VIAJE = ("Que {{TITULAR}} llegue entera y sin sobresaltos a las citas del ensayo; un cambio de "
             "alojamiento o un check-in que se cae le cuesta energía justo cuando más la necesita.")


def _ruta(*partes):
    return os.path.join(STATE, *partes)


def _iso(v):
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def _modo():
    try:
        with open(_ruta("vega", "acciones_datadas.json"), encoding="utf-8") as fh:
            return json.load(fh).get("modo", "sombra")
    except Exception:
        return "sombra"


def _reservas():
    try:
        with open(_ruta("reservas.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return []
    out = []

    def walk(x):
        if isinstance(x, dict):
            if "checkin" in x or "fecha_salida" in x:
                out.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    return out


def _nombre(r):
    for k in ("alojamiento", "nombre"):
        if r.get(k):
            return str(r[k])[:70]
    nota = str(r.get("nota") or r.get("ubicacion") or "el alojamiento")
    return nota.split(". ")[0].split(",")[0][:70]


def _cancelacion(r, ref):
    v = _iso(r.get("cancelacion_gratis_hasta"))
    if v:
        return v
    m = RE_CANCEL.search(str(r.get("nota") or "") + " " + str(r.get("tarifa") or ""))
    if not m or m.group(2).lower()[:3] not in MESES:
        return None
    try:
        return date(ref.year, MESES[m.group(2).lower()[:3]], int(m.group(1)))
    except ValueError:
        return None


def _cerrada(r):
    """Solo se persigue lo reservado de verdad: con localizador, confirmación o estado cerrado."""
    return bool(r.get("localizador") or r.get("confirmacion")
                or str(r.get("estado") or "").lower() in ("reservado", "confirmado", "comprado"))


def derivar(hoy=None):
    """[{id, titulo, plazo, por_que}] de lo que toca proponer. Solo futuro (plazo >= hoy)."""
    hoy = hoy or date.today()
    acciones, estancias = [], []
    for r in _reservas():
        if not _cerrada(r) or "cancel" in str(r.get("estado") or "").lower():
            continue
        if r.get("checkin"):
            ent = _iso(r.get("checkin"))
            if ent:
                estancias.append((ent, _iso(r.get("checkout")), _nombre(r), r))
        f = _iso(r.get("fecha_salida"))
        if f and f >= hoy:
            t = str(r.get("titulo") or "el viaje").split(" — ")[0][:70]
            clave = "%s-%s" % (f.isoformat(), seguimiento._slug(t)[:20])
            acciones.append({"id": "derivado-checkin-" + clave,
                             "titulo": "Check-in online de %s (sale el %s)" % (t, f.strftime("%d-%m")),
                             "plazo": (f - timedelta(days=1)).isoformat(),
                             "por_que": "el check-in online se abre ~24 h antes"})
            acciones.append({"id": "derivado-asistencia-" + clave,
                             "titulo": "¿Quieres asistencia especial en %s? (silla, peso, "
                                       "acompañamiento, lazo Sunflower)" % t,
                             "plazo": (f - timedelta(days=3)).isoformat(),
                             "por_que": "regla de {{TITULAR}} del 24-sep: preguntar en cada viaje"})
    estancias.sort(key=lambda e: e[0])
    por_salida = {e[1]: e for e in estancias if e[1]}
    for ent, sal, nombre, r in estancias:
        if ent >= hoy:
            previa = por_salida.get(ent)
            if previa and previa[2] != nombre:
                acciones.append({"id": "derivado-cambio-aloj-%s" % ent.isoformat(),
                                 "titulo": "Cambio de alojamiento el %s: dejar %s y entrar en %s"
                                           % (ent.strftime("%d-%m"), previa[2], nombre),
                                 "plazo": ent.isoformat(), "por_que": "salida y entrada el mismo día"})
            elif not previa:
                acciones.append({"id": "derivado-entrada-%s-%s" % (ent.isoformat(),
                                                                  seguimiento._slug(nombre)[:20]),
                                 "titulo": "Entrada en %s el %s" % (nombre, ent.strftime("%d-%m")),
                                 "plazo": ent.isoformat(), "por_que": "día de entrada"})
        canc = _cancelacion(r, ent)
        if canc and canc >= hoy:
            acciones.append({"id": "derivado-cancelacion-%s-%s" % (canc.isoformat(),
                                                                  seguimiento._slug(nombre)[:20]),
                             "titulo": "Decidir si mantienes la reserva de %s (%s→%s): la "
                                       "cancelación gratis acaba el %s"
                                       % (nombre, ent.strftime("%d-%m"),
                                          sal.strftime("%d-%m") if sal else "?", canc.strftime("%d-%m")),
                             "plazo": canc.isoformat(), "por_que": "después cuesta dinero; decide ella"})
    return acciones


def sin_plazo_con_fecha():
    """Hilos abiertos con una fecha en el título y sin plazo: no escalan nunca. Solo se listan."""
    out = []
    for h in seguimiento.load_seguimiento().get("hilos", []):
        if h.get("estado") in ("hecho", "abandonado") or h.get("plazo"):
            continue
        if RE_FECHA_TITULO.search(h.get("titulo") or ""):
            out.append({"id": h.get("id"), "titulo": (h.get("titulo") or "")[:100]})
    return out


def aplicar(write=False, hoy=None):
    modo = _modo()
    existentes = {h.get("id") for h in seguimiento.load_seguimiento().get("hilos", [])}
    nuevas = [a for a in derivar(hoy) if a["id"] not in existentes]
    res = {"modo": modo, "nuevas": nuevas, "sin_plazo": sin_plazo_con_fecha(), "creadas": []}
    if not write or not nuevas:
        return res
    # El registro se escribe SIEMPRE (también en activo): es lo que mide la anticipación
    # (cuántas horas antes del plazo se propuso) en vega_metrics.
    registro = _ruta("vega", "acciones_sombra.jsonl")
    os.makedirs(os.path.dirname(registro), exist_ok=True)
    ya = set()
    try:
        with open(registro, encoding="utf-8") as fh:
            ya = {json.loads(l).get("id") for l in fh if l.strip()}
    except OSError:
        pass
    with open(registro, "a", encoding="utf-8") as fh:
        for a in nuevas:
            if a["id"] not in ya:
                fh.write(json.dumps(dict(a, ts=datetime.now().isoformat(timespec="seconds"), modo=modo),
                                    ensure_ascii=False) + "\n")
    if modo != "activo":
        return res
    for a in nuevas:
        seguimiento.add_hilo({"id": a["id"], "titulo": a["titulo"], "estado": "esperando",
                              "plazo": a["plazo"], "origen": "derivado", "etiqueta": "Gestión",
                              "prioridad": "alta", "quien_espera": "tú", "categoria": "viaje",
                              "objetivo_ned": OBJ_VIAJE, "por_que": a["por_que"]})
        res["creadas"].append(a["id"])
    return res


def main(argv):
    res = aplicar(write="--write" in argv)
    print("modo: %s" % res["modo"])
    for a in res["nuevas"]:
        print("  %s  %s" % (a["plazo"], a["titulo"]))
    if res["creadas"]:
        print("creadas: %d" % len(res["creadas"]))
    if res["sin_plazo"]:
        print("hilos con fecha en el título y SIN plazo (no escalan):")
        for h in res["sin_plazo"]:
            print("  · %s" % h["titulo"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
