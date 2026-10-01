#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atascos.py — el resumen diario de atascos (plan «Vega al mando», Fase 5).

POR QUÉ (1-oct-26)
------------------
Los atascos del sistema se detectaban, pero cada uno por su lado (cola fallida, borradores sin
entregar, crédito, daemons en rojo, deuda), y nadie medía lo que espera por {{TITULAR}}. En el directo
del 28-sep {{CONTACTO}} lo dijo así: «si el cuello de botella eres tú, cambia el proceso». Esto los junta
en UN bloque que va dentro del parte de la mañana (enviar_hoy.py), sin mandar un mensaje más, y
propone un cambio de proceso cuando lo que espera por ella se repite.

QUÉ LEE (solo lectura, sin red, sin LLM)
-----------------------------------------
· Tablero: hilos abiertos cuyo siguiente paso es de {{TITULAR}} (`seguimiento._manos == "tu"`), con los
  días que llevan esperando.
· Buzón de propuestas de Vega sin resolver (aprobaciones.propuestas_abiertas).
· Promesas del caso vencidas (promesas_caso.abiertas).
· La foto de healthcheck (`state/healthcheck/last_check.json`): cola fallida y daemons en rojo.
· Incongruencias NUEVAS entre informes del caso (incongruencias_caso.nuevas), en N1.
· Crédito (cost_guard.credito_ok) y deuda escalada. Los borradores de outbox no: ya van en el parte.
Cada fuente falla por su cuenta: si una no se puede leer, el resumen lo dice y sigue.

Lo urgente NO pasa por aquí: sigue avisándose al momento por su camino de siempre.

Uso:
  python3 tools/atascos.py            # el bloque, tal cual iría en el parte
  python3 tools/atascos.py --json
Ganchos de test: BTP_STATE_DIR.
"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ESPERA_DIAS = 3          # desde cuántos días esperando por {{TITULAR}} entra en el resumen
REPETICION = 3           # cuántas esperas del mismo tipo hacen proponer un cambio de proceso
ABIERTOS = ("esperando", "bloqueado", "por_confirmar", "en_curso", "pendiente")
_TIPOS_CLINICOS = ("clinic", "médic", "medic", "salud", "tratamiento")


def _state():
    if os.environ.get("BTP_STATE_DIR"):
        return os.environ["BTP_STATE_DIR"]
    import seguimiento
    return seguimiento.STATE


def _dias_desde(s, hoy):
    try:
        return (hoy - date.fromisoformat(str(s)[:10])).days
    except ValueError:
        return None


def esperan_por_titular(hilos, hoy=None):
    """[(hilo, dias)] abiertos cuyo siguiente paso es de ella, de más viejo a más nuevo."""
    import seguimiento
    hoy = hoy or date.today()
    out = []
    for h in hilos:
        if h.get("estado") not in ABIERTOS or seguimiento._manos(h) != "tu":
            continue
        dias = _dias_desde(h.get("esperando_desde") or h.get("creado") or "", hoy)
        if dias is not None and dias >= ESPERA_DIAS:
            out.append((h, dias))
    return sorted(out, key=lambda x: -x[1])


def propuestas_de_proceso(esperas):
    """Si REPETICION o más esperas de un mismo tipo no clínico se acumulan en {{TITULAR}}, propone que
    las apruebe Vega (política A). Lo clínico nunca: eso es suyo por decisión del 29-sep."""
    por_tipo = {}
    for h, _d in esperas:
        et = (h.get("etiqueta") or "").strip()
        if et:                      # sin etiqueta no hay «tipo» que delegar
            por_tipo.setdefault(et, []).append(h)
    out = []
    for et, hs in sorted(por_tipo.items(), key=lambda kv: -len(kv[1])):
        if len(hs) >= REPETICION and not any(k in et.lower() for k in _TIPOS_CLINICOS):
            out.append("%d cosas de «%s» esperan por ti. Propuesta: que las apruebe Vega con registro "
                       "(nivel A) y tú veas solo el resumen." % (len(hs), et))
    return out


def _sistema():
    """Atascos del sistema: lista de frases, cada fuente por su cuenta."""
    st = _state()
    out, fallos = [], []
    try:
        hc = json.load(open(os.path.join(st, "healthcheck", "last_check.json"), encoding="utf-8"))
        jc = hc.get("jobs_caidos") or {}
        if jc.get("failed_total"):
            out.append("Cola: %d trabajos fallidos (%d de más de %d días)."
                       % (jc["failed_total"], jc.get("failed_viejos", 0), jc.get("failed_ventana_dias", 0)))
        rojo = (hc.get("roster_daemons") or {}).get("fallando") or {}
        if rojo:
            out.append("Rutinas en rojo: %s." % ", ".join(sorted(rojo)[:6]))
    except Exception as e:  # noqa: BLE001
        fallos.append("healthcheck (%s)" % type(e).__name__)
    # outbox/pending no va aquí: el parte ya lo cuenta («N borradores más listos»).
    try:
        import cost_guard
        if cost_guard.credito_ok() is False:
            # 1-oct-26: todo el trabajo de Claude va por la cuenta Max; la API de pago solo es la
            # reserva de lo crítico cuando Max llega al límite.
            out.append("Crédito de la API de pago agotado: no hay reserva si Max llega al límite "
                       "en una tarea crítica (lo demás va por Max).")
    except Exception as e:  # noqa: BLE001
        fallos.append("crédito (%s)" % type(e).__name__)
    try:
        import deuda
        esc = deuda.escaladas()
        if esc:
            out.append("Deuda escalada: %d (%s)." % (len(esc), ", ".join(sorted(esc)[:3])))
    except Exception as e:  # noqa: BLE001
        fallos.append("deuda (%s)" % type(e).__name__)
    return out, fallos


def recopilar(hoy=None):
    hoy = hoy or date.today()
    fallos = []
    try:
        import seguimiento
        esperas = esperan_por_titular(seguimiento.load_seguimiento().get("hilos", []), hoy)
    except Exception as e:  # noqa: BLE001
        esperas, fallos = [], fallos + ["tablero (%s)" % type(e).__name__]
    try:
        import aprobaciones
        propuestas = len(aprobaciones.propuestas_abiertas())
    except Exception as e:  # noqa: BLE001
        propuestas, fallos = 0, fallos + ["propuestas (%s)" % type(e).__name__]
    try:
        import promesas_caso
        vencidas = [p for p, d in promesas_caso.abiertas(hoy) if d > 0]
    except Exception as e:  # noqa: BLE001
        vencidas, fallos = [], fallos + ["promesas (%s)" % type(e).__name__]
    try:
        import incongruencias_caso
        from datetime import timedelta
        incong = incongruencias_caso.nuevas((hoy - timedelta(days=1)).isoformat())
    except Exception as e:  # noqa: BLE001
        incong, fallos = [], fallos + ["incongruencias (%s)" % type(e).__name__]
    sistema, f2 = _sistema()
    return {"incongruencias": incong, "esperas": esperas, "proceso": propuestas_de_proceso(esperas), "propuestas": propuestas,
            "promesas_vencidas": vencidas, "sistema": sistema, "fallos": fallos + f2}


def bloque(datos=None):
    """Texto para el parte. Vacío si no hay ningún atasco (sin novedad no se dice nada)."""
    d = datos or recopilar()
    lin = []
    # Nivel B (decisión del 29-sep): lo que no cuadra entre fuentes del caso es de {{TITULAR}}. Solo lo
    # NUEVO (visto desde ayer); el listado completo vive en INCONGRUENCIAS-DEL-CASO.md.
    if d.get("incongruencias"):
        lin.append("🔀 Datos de tu caso que no cuadran entre informes (nuevo; el juicio es de tus médicos):")
        lin += ["   · " + f for f in d["incongruencias"][:5]]
    if d["esperas"]:
        lin.append("⏳ Esperan por ti (%d, de más de %d días):" % (len(d["esperas"]), ESPERA_DIAS))
        for h, dias in d["esperas"][:5]:
            lin.append("   · %s · %d días" % (h.get("titulo", "")[:70], dias))
        if len(d["esperas"]) > 5:
            lin.append("   · … y %d más en el Tablero" % (len(d["esperas"]) - 5))
    for p in d["proceso"]:
        lin.append("🔁 " + p)
    if d["promesas_vencidas"]:
        lin.append("📭 Prometido y sin llegar: %d (detalle en el estado vivo del caso)." % len(d["promesas_vencidas"]))
    if d["propuestas"]:
        lin.append("📮 Propuestas sin resolver en el buzón de Vega: %d." % d["propuestas"])
    if d["sistema"]:
        lin.append("⚙️ Sistema:")
        lin += ["   · " + s for s in d["sistema"]]
    if d["fallos"]:
        lin.append("(no pude leer: %s)" % ", ".join(d["fallos"]))
    if not lin:
        return ""
    return "\n".join(["🧱 Atascos de hoy"] + lin)


def main(argv):
    d = recopilar()
    if "--json" in argv:
        d = dict(d, esperas=[{"titulo": h.get("titulo"), "dias": n} for h, n in d["esperas"]],
                 promesas_vencidas=len(d["promesas_vencidas"]))
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(bloque(d) or "Sin atascos.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
