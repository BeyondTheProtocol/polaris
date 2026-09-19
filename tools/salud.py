#!/usr/bin/env python3
"""tools/salud.py — el ACUSE de cada alerta de salud ("visto, en ello", 3/7/26).

Hueco que pregunta {{TITULAR}}: cuando salta una alerta de healthcheck, hoy no hay forma de que ella
sepa que alguien la vio y se puso — la alerta grita al vacío (healthcheck._emitir_si_cambia) y se
repite cada ~12h sin dueño ni acuse. Esto le da un ESTADO por alerta:

    {clave: {estado: "detectado"|"en_arreglo"|"resuelto", por, visto_ts, nota}}

en tools/state/healthcheck/acuses.json, con la MISMA clave estable que ya usa el dedup de
healthcheck._emitir_si_cambia (p.ej. "daemon_asistente_fallo", "frescura_agente_fallo:correo-triaje").

CLI:
    python3 tools/salud.py list                      # alertas abiertas ahora + su acuse
    python3 tools/salud.py ack <clave> "<qué hago>"   # marca en_arreglo (por=BTP_ACTOR o "Claude")
    python3 tools/salud.py resuelto <clave> ["nota"]  # marca resuelto

REFLEJO (CLAUDE.md, hermano del 🩺 auto-detectar-resolver): al ver una alerta de salud, LO PRIMERO
es `salud ack <clave> "<qué voy a hacer>"` ({{TITULAR}} ve "✋ Visto, en ello" en vez del grito) y, al
terminar, `salud resuelto <clave>`. El arreglo real sigue siendo obligatorio — esto NO lo sustituye,
solo evita que grite mientras se está atendiendo.

Determinista, stdlib, sin salida hacia fuera (esto no habla con Telegram; healthcheck._emitir_si_cambia
es quien decide qué VOZ usar según lo que aquí se guarda).
"""
import json
import os
import sys
import time
from datetime import datetime

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
HC = os.path.join(STATE, "healthcheck")
ACUSES = os.path.join(HC, "acuses.json")

ESTADOS_VALIDOS = ("detectado", "en_arreglo", "resuelto")


def _ensure():
    os.makedirs(HC, mode=0o700, exist_ok=True)


def _write_atomic(path, payload):
    _ensure()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load():
    try:
        with open(ACUSES, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _now_ts():
    return time.time()


def _actor(por=None):
    if por:
        return str(por)
    return os.environ.get("BTP_ACTOR") or "Claude"


def ack(clave, nota="", por=None):
    """Marca `clave` como en_arreglo. Devuelve el registro guardado. Fail-soft: nunca lanza
    (si no puede escribir, devuelve el registro igualmente para que el llamador sepa el intento)."""
    d = _load()
    rec = {"estado": "en_arreglo", "por": _actor(por), "visto_ts": _now_ts(), "nota": str(nota or "")}
    d[str(clave)] = rec
    try:
        _write_atomic(ACUSES, d)
    except Exception:
        pass
    return rec


def resuelto(clave, nota=""):
    """Marca `clave` como resuelto (cierre explícito; también se limpia solo cuando la condición
    ya no está en el próximo ciclo de healthcheck — esto es el cierre EXPLÍCITO de quien arregló)."""
    d = _load()
    rec = {"estado": "resuelto", "por": _actor(), "visto_ts": _now_ts(), "nota": str(nota or "")}
    d[str(clave)] = rec
    try:
        _write_atomic(ACUSES, d)
    except Exception:
        pass
    return rec


def get(clave):
    """El acuse de `clave`, o None si nunca se acusó."""
    return _load().get(str(clave))


def purgar(clave):
    """Quita el acuse de `clave` (se llama cuando la condición se despeja del todo, para que un
    futuro fallo NUEVO de la misma clave empiece sin acuse pegajoso). Fail-soft."""
    d = _load()
    if str(clave) in d:
        del d[str(clave)]
        try:
            _write_atomic(ACUSES, d)
        except Exception:
            pass


_RE_JOB = None


def reconciliar_acuses():
    """Un acuse que promete un encargo que ya no existe es una MENTIRA. Lo devuelve a 'detectado'.

    POR QUÉ (31-jul-2026). El acuse nació para que {{TITULAR}} leyera «✋ Visto, en ello» en vez del
    grito, y para que la alerta no re-gritara mientras alguien la atiende. Pero nadie comprobaba
    que ese alguien siguiera existiendo: cuando el encargo encolado moría (caducado, rc=1,
    dead-letter), el acuse se quedaba en `en_arreglo` PARA SIEMPRE y la alerta no volvía a
    llamar. Encontrados 5 acuses así, uno de ellos con 89 horas — «lo estoy mirando» durante casi
    cuatro días con el encargo muerto desde el primer minuto.

    Es exactamente el fallo que el acuse vino a arreglar (*«decía 'me pongo' y detrás no miraba
    nadie»*), reaparecido una capa más arriba. Un silencio que se sostiene solo es peor que un
    grito: el grito al menos se oye.

    Solo toca acuses `en_arreglo` cuya nota nombra un `job <id>`. Los de `autofix` (que no
    encolan nada) y los `resuelto` se quedan como están. Devuelve las claves liberadas.
    """
    global _RE_JOB
    if _RE_JOB is None:
        import re
        _RE_JOB = re.compile(r"job ([0-9a-f]{6,})")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import cola
    except Exception:
        return []                                  # sin cola no se puede juzgar: no tocar nada
    d = _load()
    liberadas = []
    for clave, rec in list(d.items()):
        if rec.get("estado") != "en_arreglo":
            continue
        m = _RE_JOB.search(str(rec.get("nota") or ""))
        if not m:
            continue                               # no prometía un encargo: no es asunto de aquí
        jid = m.group(1)
        vivo = any(cola._find_by_id(sub, jid) for sub in ("pending", "processing"))
        if vivo:
            continue
        rec["estado"] = "detectado"
        rec["nota"] = ("%s — el encargo %s ya no está en la cola (murió o terminó sin cerrarla): "
                       "el acuse se libera para que la alerta vuelva a llamar."
                       % (rec.get("nota") or "", jid))[:400]
        rec["visto_ts"] = _now_ts()
        liberadas.append(clave)
    if liberadas:
        try:
            _write_atomic(ACUSES, d)
        except Exception:
            return []
    return liberadas


def _edad_min(visto_ts):
    try:
        return max(0.0, (_now_ts() - float(visto_ts)) / 60.0)
    except Exception:
        return None


def listar_abiertas():
    """[{clave, estado, por, visto_ts, nota, edad_min}] — todo lo que hay en acuses.json ahora
    mismo (no filtra por si la condición sigue viva: eso lo decide healthcheck comparando contra
    `alertas` del ciclo actual; aquí solo se expone el estado guardado)."""
    d = _load()
    out = []
    for clave, rec in d.items():
        out.append({
            "clave": clave,
            "estado": rec.get("estado"),
            "por": rec.get("por"),
            "visto_ts": rec.get("visto_ts"),
            "nota": rec.get("nota", ""),
            "edad_min": _edad_min(rec.get("visto_ts")),
        })
    out.sort(key=lambda x: x.get("visto_ts") or 0, reverse=True)
    return out


def _fmt_edad(edad_min):
    if edad_min is None:
        return "?"
    if edad_min < 60:
        return "%dm" % int(edad_min)
    return "%dh" % int(edad_min / 60)


def listar_alertas_abiertas():
    """[{clave, texto, edad_min}] — las alertas de categoría 'humano' que healthcheck AVISÓ la
    última vez y que, hasta donde sabemos, siguen abiertas (siguen en last_alert_state-humano.json;
    ese fichero solo se reescribe cuando _emitir_si_cambia vuelve a enviar, así que su contenido es
    'lo último que se le mostró a {{TITULAR}}'). Complementa a listar_abiertas(): antes, `salud list`
    solo mostraba lo que TENÍA acuse — si una alerta nunca se acusó (o el acuse se purgó) parecía que
    'no había nada', aunque siguiera viva. No relee el estado en vivo (no vuelve a correr los
    chequeos): es una foto de la última vez que se avisó, coherente con lo que {{TITULAR}} ya vio.
    Fail-soft: nunca lanza, [] si no hay estado."""
    path = os.path.join(HC, "last_alert_state-humano.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return []
    except Exception:
        return []
    claves = d.get("claves", d.get("alertas", []))
    textos = d.get("textos", {}) if isinstance(d.get("textos"), dict) else {}
    ts = d.get("ts")
    edad_min = _edad_min(ts) if isinstance(ts, (int, float)) else None   # _edad_min espera segundos
    out = []
    for clave in claves:
        out.append({"clave": clave, "texto": textos.get(clave, clave), "edad_min": edad_min})
    return out


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = argv[0]
    if cmd == "reconciliar":
        libres = reconciliar_acuses()
        if not libres:
            print("todos los acuses 'en_arreglo' tienen su encargo vivo (o no prometían ninguno)")
            return 0
        print("liberados %d acuse(s) cuyo encargo ya no existe → la alerta vuelve a llamar:"
              % len(libres))
        for c in libres:
            print("   · %s" % c)
        return 0
    if cmd == "list":
        # Antes esto solo miraba acuses.json — si una alerta viva nunca se acusó (o su acuse ya se
        # purgó tras un ciclo de "✋ Visto" viejo) decía "Sin acuses registrados" aunque hubiera
        # alertas abiertas de verdad, lo cual confundía. Ahora combina ambas fuentes: acuses con
        # estado (como antes) + las alertas abiertas SIN acuse asociado (listar_alertas_abiertas).
        abiertas = listar_abiertas()
        con_acuse = {a["clave"] for a in abiertas}
        vivas_sin_acuse = [a for a in listar_alertas_abiertas() if a["clave"] not in con_acuse]
        if not abiertas and not vivas_sin_acuse:
            print("Sin alertas abiertas ni acuses registrados.")
            return 0
        if abiertas:
            print("── Con acuse ──")
            for a in abiertas:
                print("%-45s %-11s por=%-10s hace %-5s %s" % (
                    a["clave"], a["estado"], a["por"] or "?", _fmt_edad(a["edad_min"]), a["nota"]))
        if vivas_sin_acuse:
            print("── Alertas abiertas SIN acuse ──")
            for a in vivas_sin_acuse:
                print("%-45s hace %-5s %s" % (a["clave"], _fmt_edad(a["edad_min"]), a["texto"]))
        return 0

    if cmd == "ack":
        if len(argv) < 2:
            print("uso: salud.py ack <clave> [\"nota\"]")
            return 1
        clave = argv[1]
        nota = argv[2] if len(argv) > 2 else ""
        rec = ack(clave, nota)
        print("✋ %s → en_arreglo (por=%s): %s" % (clave, rec["por"], nota or "(sin nota)"))
        return 0

    if cmd == "resuelto":
        if len(argv) < 2:
            print("uso: salud.py resuelto <clave> [\"nota\"]")
            return 1
        clave = argv[1]
        nota = argv[2] if len(argv) > 2 else ""
        resuelto(clave, nota)
        print("✅ %s → resuelto" % clave)
        return 0

    print("comando desconocido: %s (usa list/ack/resuelto)" % cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
