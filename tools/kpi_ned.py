#!/usr/bin/env python3
"""tools/kpi_ned.py — los dos números que el bucle de auto-mejora tiene que MOVER.

POR QUÉ EXISTE (30-jul-2026). El bucle de auto-mejora sabe detectar y (desde el libro de deuda) sabe
cerrar, pero **no tiene ninguna medida de si el sistema está más cerca de NED que ayer**. Sin métrica
no converge: acumula. Lo que se midió el día que se escribió esto —

  · 35 agentes en `.claude/agents/`, 13 con CERO invocaciones en todo el histórico de transcripts.
  · `pipeline_vacuna.json` con 34 días de desfase: decía «0/6 etapas · etapa A» cuando la biopsia
    ya constaba como HECHA (8-jul) en `cumbre.json`. Dos estados del sistema contradiciéndose, y el
    ciego era el del camino crítico.

— no son fallos distintos: son el mismo bucle puntuando solo por AÑADIR.

QUÉ MIDE (los dos únicos números; todo lo demás —agentes, tokens, MCPs— es MEDIO, no fin):

  **KPI-1 · Latencia de estado.** Cuánto tarda Polaris en enterarse de algo que ya es verdad. Por
  cada fuente crítica, edad de su `actualizado` contra un presupuesto de frescura. Importa porque
  razonamiento brillante sobre estado viejo no da «no sé»: da una respuesta coherente y FALSA, que
  en una ruta clínica es peor que el silencio.

  **KPI-2 · Cobertura de vigilancia.** Qué parte de lo que está abierto tiene a alguien detrás.
  Se parte en dos a propósito —etiquetado y accionable— porque si fuera un solo número se sube
  haciendo trampa: etiquetando menos hilos, el porcentaje de «bien atendidos» sube solo.

Determinista, local, $0, sin LLM: nada de que un modelo se puntúe a sí mismo. No avisa a {{TITULAR}} por
su cuenta (el número viaja en el resumen diario del bucle), así que no necesita anti-spam.

Molde copiado de `tools/normas.py:latido_y_meta()`, que ya hace exactamente esto para la cobertura
de normas: si el número no sube en 30 días, eso ES un hallazgo y se abre solo en el libro de deuda.

Uso:
  python3 tools/kpi_ned.py estado [--json]   # los cuatro números, de una ojeada
  python3 tools/kpi_ned.py latido            # snapshot + ¿se movió? → deuda si lleva 30 días parado

Exit: `estado` siempre 0 (es un informe) · `latido` 1 si está estancado, 0 si no.
"""
import json
import os
import sys
import time
from datetime import datetime, date, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Casa base SIEMPRE: el estado vivo (`tools/state/`) NO está versionado, así que en un worktree no
# existe. Resolver desde __file__ daría cero hilos y un falso verde. Mismo criterio que
# `audit_comites.py`. `BTP_STATE_DIR` manda (los tests lo usan para aislarse).
CASA = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
DIAS_ESTANCADO = 30

# KPI-1: qué fuentes vigilo y cuánto puede envejecer cada una antes de que sea un problema.
# Los presupuestos son una PROPUESTA de partida (30-jul-26), no un dato medido: se ajustan con la
# primera semana de números reales.
FUENTES = (
    {"fichero": "pipeline_vacuna.json", "presupuesto": 7,
     "que": "el flujo biopsia→vacuna, lo más NED que hay"},
    {"fichero": "cumbre.json", "presupuesto": 7,
     "que": "los hilos clínicos y dónde estamos"},
    {"fichero": "seguimiento.json", "presupuesto": 2,
     "que": "el Tablero: todo lo que queda colgando"},
    {"fichero": "deuda.json", "presupuesto": 2,
     "que": "el libro de hallazgos"},
)

# Un hilo cuenta como CERRADO (y por tanto no se vigila) si su estado es uno de estos.
CERRADOS = ("hecho", "cerrado", "descartado")
# Un hilo tiene RELOJ si al menos uno de estos campos dice cuándo o de quién depende.
RELOJ = ("plazo", "gate", "quien_espera")


def _state_dir():
    return os.environ.get("BTP_STATE_DIR") or os.path.join(CASA, "tools", "state")


def _snap_path():
    return os.path.join(_state_dir(), "kpi_ned_snapshot.json")


def _leer_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _fecha_del_contenido(d):
    """La fecha que el propio fichero declara. Devuelve `date` o None."""
    if not isinstance(d, dict):
        return None
    crudo = d.get("actualizado") or d.get("updated")
    if not isinstance(crudo, str) or not crudo.strip():
        return None
    try:
        return date.fromisoformat(crudo.strip()[:10])
    except ValueError:
        return None


def latencia(hoy=None):
    """KPI-1: ¿cuánto hace que Polaris no se entera de nada en cada fuente crítica?"""
    hoy = hoy or date.today()
    fuentes, desfasadas, peor = [], 0, 0
    for f in FUENTES:
        path = os.path.join(_state_dir(), f["fichero"])
        d = _leer_json(path)
        fecha = _fecha_del_contenido(d)
        base = "campo"
        if fecha is None:
            # Sin campo `actualizado` (p. ej. `deuda.json`, que es un dict de hallazgos): cae al
            # mtime. Se dice cuál se usó, porque un mtime que toca un daemon cada noche es un
            # aprobado gratis y hay que poder verlo.
            base = "mtime"
            try:
                fecha = date.fromtimestamp(os.path.getmtime(path))
            except OSError:
                fecha = None
        if fecha is None:
            edad, fuera = None, True   # no existe = no se sabe nada = cuenta como desfasada
        else:
            edad = (hoy - fecha).days
            fuera = edad > f["presupuesto"]
            peor = max(peor, edad)
        desfasadas += 1 if fuera else 0
        fuentes.append({"fichero": f["fichero"], "que": f["que"], "presupuesto": f["presupuesto"],
                        "edad_dias": edad, "base": base, "fuera_de_presupuesto": fuera})
    return {"fuentes": fuentes, "desfasadas": desfasadas, "peor_edad_dias": peor}


def cobertura():
    """KPI-2: de lo que está abierto, ¿cuánto declara su NED y cuánto tiene a alguien detrás?"""
    d = _leer_json(os.path.join(_state_dir(), "seguimiento.json")) or {}
    hilos = d.get("hilos") or []
    abiertos = [h for h in hilos if (h.get("estado") or "") not in CERRADOS]
    etiquetados = [h for h in abiertos if h.get("objetivo_ned")]
    accionables = [h for h in etiquetados
                   if h.get("dueno") and h.get("siguiente_accion")
                   and any(h.get(k) for k in RELOJ)]
    return {
        "abiertos": len(abiertos),
        "etiquetados": len(etiquetados),
        "accionables": len(accionables),
        "pct_etiquetado": _pct(len(etiquetados), len(abiertos)),
        "pct_accionable": _pct(len(accionables), len(etiquetados)),
    }


def _pct(parte, total):
    return 0 if not total else round(100.0 * parte / total)


def estado():
    lat, cob = latencia(), cobertura()
    return {"latencia": lat, "cobertura": cob,
            "linea": ("KPI-1 fuentes desfasadas: %d (peor: %d d) · "
                      "KPI-2 etiquetado %d %% · accionable %d %%"
                      % (lat["desfasadas"], lat["peor_edad_dias"],
                         cob["pct_etiquetado"], cob["pct_accionable"]))}


def _mejor(actual, previo):
    """¿Alguno de los tres números va mejor que la mejor marca anterior?

    Uno solo basta: exigir que suban los tres a la vez haría el listón inalcanzable y el latido
    gritaría siempre, que es como muere una alarma. Se compara contra la MEJOR marca histórica
    (trinquete), no contra la pasada anterior, para que un bajón puntual no reinicie el reloj ni
    cuente como avance cuando se recupere.
    """
    return (actual["desfasadas"] < previo["desfasadas"]
            or actual["pct_etiquetado"] > previo["pct_etiquetado"]
            or actual["pct_accionable"] > previo["pct_accionable"])


def _resumen_numerico(e):
    return {"desfasadas": e["latencia"]["desfasadas"],
            "pct_etiquetado": e["cobertura"]["pct_etiquetado"],
            "pct_accionable": e["cobertura"]["pct_accionable"]}


def latido_y_meta(hoy_ts=None):
    """Deja latido y comprueba que los números SE MUEVEN. Devuelve (estancado, dias, actual).

    Si en `DIAS_ESTANCADO` días no mejora ninguno de los tres, eso ES un hallazgo y se abre solo en
    el libro de deuda: un contador parado significa que el bucle está trabajando en cosas que no
    mueven NED, que es exactamente el fallo que esta tool existe para cazar.
    """
    ahora = hoy_ts or time.time()
    act = _resumen_numerico(estado())
    prev = _leer_json(_snap_path()) or {}
    base = prev.get("mejor") or dict(act)
    base_ts = float(prev.get("ts") or ahora)
    mejoro = _mejor(act, base)
    # `dias` es «desde la última mejora»: si la mejora es ESTA pasada, son 0. Sin esto la línea del
    # resumen diría «40 días sin mejorar» justo el día que mejoró.
    dias = 0 if mejoro else int((ahora - base_ts) / 86400)
    estancado = dias >= DIAS_ESTANCADO

    mejor = {"desfasadas": min(act["desfasadas"], base["desfasadas"]),
             "pct_etiquetado": max(act["pct_etiquetado"], base["pct_etiquetado"]),
             "pct_accionable": max(act["pct_accionable"], base["pct_accionable"])}
    nuevo = {"ts": ahora if (mejoro or not prev) else base_ts,
             "mejor": mejor, "ultimo_visto": ahora, "ultimo": act}
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        with open(_snap_path(), "w", encoding="utf-8") as f:
            json.dump(nuevo, f, ensure_ascii=False)
        hb = os.path.join(_state_dir(), "heartbeat", "kpi-ned.json")
        os.makedirs(os.path.dirname(hb), exist_ok=True)
        with open(hb, "w", encoding="utf-8") as f:
            json.dump({"agente": "kpi-ned",
                       "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "estado": "estancado" if estancado else "ok", **act}, f, ensure_ascii=False)
    except Exception:
        pass

    if estancado:
        try:
            sys.path.insert(0, HERE)
            import deuda
            deuda.abrir("kpi-ned-estancado",
                        "Los KPIs de NED llevan %d días sin mejorar (desfasadas=%d · etiquetado=%d %% "
                        "· accionable=%d %%): el bucle de auto-mejora está trabajando en cosas que no "
                        "mueven NED." % (dias, act["desfasadas"], act["pct_etiquetado"],
                                         act["pct_accionable"]), ned="alto")
        except Exception:
            pass
    return estancado, dias, act


def _print_estado(e):
    lat, cob = e["latencia"], e["cobertura"]
    print("📉 KPI-1 · Latencia de estado — cuánto tarda Polaris en enterarse")
    for f in lat["fuentes"]:
        edad = "?" if f["edad_dias"] is None else "%d d" % f["edad_dias"]
        marca = "❌" if f["fuera_de_presupuesto"] else "✅"
        nota = "" if f["base"] == "campo" else "  (por mtime, no por campo)"
        print("   %s %-24s %6s  / presupuesto %d d · %s%s"
              % (marca, f["fichero"], edad, f["presupuesto"], f["que"], nota))
    print("   → fuera de presupuesto: %d de %d · peor: %d días"
          % (lat["desfasadas"], len(lat["fuentes"]), lat["peor_edad_dias"]))
    print()
    print("🎯 KPI-2 · Cobertura de vigilancia — de lo abierto, qué tiene a alguien detrás")
    print("   · etiquetado (declara su objetivo_ned):   %3d %%   (%d de %d abiertos)"
          % (cob["pct_etiquetado"], cob["etiquetados"], cob["abiertos"]))
    print("   · accionable (dueño + acción + reloj):    %3d %%   (%d de %d etiquetados)"
          % (cob["pct_accionable"], cob["accionables"], cob["etiquetados"]))
    print()
    print("   " + e["linea"])


def main(argv):
    cmd = argv[0] if argv else "estado"
    if cmd == "estado":
        e = estado()
        if "--json" in argv:
            print(json.dumps(e, ensure_ascii=False, indent=2))
        else:
            _print_estado(e)
        return 0
    if cmd == "latido":
        estancado, dias, act = latido_y_meta()
        print("latido escrito · desfasadas %d · etiquetado %d %% · accionable %d %% · "
              "%d día(s) desde la última mejora%s"
              % (act["desfasadas"], act["pct_etiquetado"], act["pct_accionable"], dias,
                 " → ⚠️ ESTANCADO, abierto en el libro de deuda" if estancado else ""))
        return 1 if estancado else 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
