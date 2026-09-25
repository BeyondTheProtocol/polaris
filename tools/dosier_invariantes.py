#!/usr/bin/env python3
"""tools/dosier_invariantes.py — L1: invariantes deterministas del Dosier-JSONL.

El SUELO DETERMINISTA del Guardián. Sobre el Dosier-JSONL (una pieza por línea),
comprueba con CÓDIGO (no juicio de modelo) que el dosier cumple sus reglas, y lo
combina con la frescura (frescura_dosier) en un solo veredicto:

  · SCHEMA con ALLOWLIST CERRADA — un campo desconocido RECHAZA la pieza (fail-closed,
    anti-inyección; mismo patrón que cola.py: un campo nuevo puede ser un freno).
  · Campos REQUERIDOS presentes y ENUMS válidos (status, confianza).
  · `id` ÚNICO — dos piezas VIVAS con el mismo id = CONTRADICCIÓN (alguien actualizó un
    hecho sin retirar el viejo: la clase «sin cerrar vs confirmada» que cazamos a mano).
  · COHERENCIA confianza↔gate — no «alta» con un gate humano abierto sin confirmar.
  · «alta» exige FUENTE.
  · Invariantes de DOMINIO configurables (marcador presente ⇒ su ensayo no huérfano).

Principio #0: lo load-bearing aquí es CÓDIGO. Fail-closed: cualquier violación → NO
entregable. El Guardián no concluye clínica; verifica la FORMA y la CONSISTENCIA.

Códigos de salida: 0 = entregable · 1 = violaciones/no-fresco · 2 = formato inválido.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frescura_dosier as fd  # reusa cargar_jsonl + evaluar_frescura
import elegibilidad_ensayos as el  # L3 — cableado al veredicto único
import cotejo_invariante as cot  # L-cotejo — cortafuegos de cotejo obligatorio (error 27/6/26)

# ── SCHEMA (allowlist CERRADA) ───────────────────────────────────────────────
# `cotejo` es el bloque que el cortafuegos de cotejo obligatorio (L-cotejo) consume:
#   {"fuente": "_PRIVADO_CLINICO/...", "fecha": "AAAA-MM-DD", "plataforma": "...",
#    "tipo_resultado": "presencia|ausencia|otro"}. Allowlist cerrada: un sub-campo
#   desconocido dentro de `cotejo` también rechaza (fail-closed) — ver cotejo_invariante.
CAMPOS = {"id", "bloque", "afirmacion", "fuentes", "confianza",
          "status", "gate_humano", "confirmado_en", "ttl_dias", "version_canonico",
          "cotejo"}
REQUERIDOS = {"id", "bloque", "afirmacion", "status", "confianza"}
STATUS = {"vivo", "superado", "obsoleto"}
CONFIANZA = {"alta", "media", "baja", "por-confirmar"}

# Invariantes de DOMINIO: si una pieza VIVA afirma <marcador>, debe existir otra pieza
# VIVA que cubra <ensayo>. Extensible (lista de (marcador, ensayo, motivo)).
#
# ── EN CUARENTENA (clínico, pendiente {{CONTACTO}}) ────────────────────────────────
# El MECANISMO (marcador presente ⇒ su ensayo no huérfano) es TÉCNICO y lo valida {{TITULAR}}.
# El CONTENIDO (qué marcador liga con qué ensayo/diana) es una AFIRMACIÓN CLÍNICA: el
# Guardián NO la enforca hasta que la firme {{CONTACTO}} {{CONTACTO}} con el protocolo (regla 27/6/26 —
# memoria feedback-titular-valida-tecnico-contacto-clinico; muro «no consejo médico»). Estas
# reglas viven INERTES; activar una = moverla a INVARIANTES_DOMINIO, acto que exige la
# firma clínica de {{CONTACTO}}. Lista de revisión para ella: docs Guardian-Bisturi.
INVARIANTES_DOMINIO_PENDIENTE_CONTACTO = [
    ("{{DIANA}}", "{{TRAZADOR}}", "marcador {{DIANA}}+ presente ⇒ debe pedirse su ensayo/imagen {{TRAZADOR}}"),
    ("RB1", "CDK", "pérdida de RB1 ⇒ debe contemplarse la diana CDK (resistencia/rescate)"),
]
# ACTIVAS: vacío hasta firma clínica. El MECANISMO se sigue probando con una regla
# SINTÉTICA en L9 (guardian_evals), así que queda verificado aunque no haya reglas activas.
INVARIANTES_DOMINIO = []


def _schema_pieza(p):
    """Violaciones de SCHEMA de una pieza. Allowlist cerrada = fail-closed."""
    v = []
    if not isinstance(p, dict):
        return ["la pieza no es un objeto JSON"]
    desconocidos = set(p) - CAMPOS
    if desconocidos:
        v.append("campo(s) DESCONOCIDO(s) %s → rechazado (allowlist cerrada, fail-closed)"
                 % sorted(desconocidos))
    faltan = REQUERIDOS - set(p)
    if faltan:
        v.append("falta(n) campo(s) requerido(s) %s" % sorted(faltan))
    if p.get("status") not in STATUS:
        v.append("status inválido: %r (válidos: %s)" % (p.get("status"), sorted(STATUS)))
    if p.get("confianza") not in CONFIANZA:
        v.append("confianza inválida: %r (válidas: %s)" % (p.get("confianza"), sorted(CONFIANZA)))
    if "fuentes" in p and not isinstance(p["fuentes"], list):
        v.append("`fuentes` debe ser una lista")
    if "ttl_dias" in p and (isinstance(p["ttl_dias"], bool) or not isinstance(p["ttl_dias"], int)):
        # bool es subclase de int en Python (True==1): se excluye explícitamente, si no
        # `ttl_dias: true` colaría como entero 1 → fail-OPEN silencioso.
        v.append("`ttl_dias` debe ser entero (no booleano)")
    return v


def _invariantes(piezas, invariantes_dominio=None):
    """Violaciones CRUZADAS entre piezas (deterministas). Devuelve lista de strings.

    `invariantes_dominio` (lista de (marcador, ensayo, motivo)) inyectable; por defecto usa
    la lista ACTIVA del módulo (vacía: las clínicas están en cuarentena pendiente de {{CONTACTO}}).
    """
    inv_dom = INVARIANTES_DOMINIO if invariantes_dominio is None else invariantes_dominio
    bloqueos = []
    validas = [p for p in piezas if isinstance(p, dict)]

    # fail-closed CENTRAL: un Dosier SIN ninguna pieza VIVA (vacío, o todo superado/obsoleto)
    # tiene TODO lo requerido faltante → no puede ser entregable (regla 27/6/26, validada).
    if not any(p.get("status") == "vivo" for p in validas):
        bloqueos.append("Dosier SIN ninguna pieza VIVA (vacío o todo superado/obsoleto) → no "
                        "entregable: nada vivo que entregar, todo lo requerido faltante (fail-closed)")

    # id único; dos VIVAS con el mismo id = contradicción (hecho actualizado sin retirar el viejo)
    por_id = {}
    for p in validas:
        por_id.setdefault(p.get("id"), []).append(p)
    for pid, grupo in por_id.items():
        vivos = [p for p in grupo if p.get("status") == "vivo"]
        if len(vivos) > 1:
            bloqueos.append("CONTRADICCIÓN: %d piezas VIVAS con el mismo id «%s» (un hecho con "
                            "dos versiones vivas; retira la vieja a `superado`)" % (len(vivos), pid))
        elif len(grupo) > 1 and len(vivos) <= 1:
            pass  # histórico (superado/obsoleto) conviviendo con la viva: correcto (versionado)

    # coherencia por pieza
    for p in validas:
        pid = p.get("id", "(sin id)")
        gate = p.get("gate_humano")
        conf = p.get("confianza")
        if conf == "alta" and gate and not p.get("confirmado_en"):
            bloqueos.append("INCOHERENTE: «%s» dice confianza ALTA con el gate humano %s ABIERTO "
                            "sin confirmar → no puede ser alta hasta la firma" % (pid, gate))
        if conf == "alta" and not p.get("fuentes"):
            bloqueos.append("«%s» dice confianza ALTA sin FUENTE (alta exige fuente trazable)" % pid)

    # invariantes de dominio (marcador ⇒ ensayo no huérfano), solo entre piezas VIVAS
    afirm_vivas = " || ".join((p.get("afirmacion") or "").lower() for p in validas
                              if p.get("status") == "vivo")
    for marcador, ensayo, motivo in inv_dom:
        if marcador.lower() in afirm_vivas and ensayo.lower() not in afirm_vivas:
            bloqueos.append("INVARIANTE de dominio: %s (falta una pieza viva que cubra «%s»)"
                            % (motivo, ensayo))
    return bloqueos


def evaluar(piezas, ahora=None, comprobador_ct=None, invariantes_dominio=None):
    """Schema + invariantes + frescura + elegibilidad → (resultados_schema, entregable, bloqueos).

    Fail-closed: cualquier violación de schema, invariante o frescura → NO entregable.
    `invariantes_dominio` inyectable (por defecto la lista activa, vacía: clínicas en cuarentena).
    """
    bloqueos = []
    resultados = []
    for i, p in enumerate(piezas, 1):
        vs = _schema_pieza(p)
        pid = (p.get("id") if isinstance(p, dict) else None) or "(línea %d)" % i
        resultados.append({"id": pid, "schema_ok": not vs, "violaciones": vs})
        for v in vs:
            bloqueos.append("pieza «%s»: %s" % (pid, v))

    bloqueos += _invariantes(piezas, invariantes_dominio)

    # frescura (reusa L3/frescura_dosier) — su veredicto se suma fail-closed
    _, fresco_ok, bloq_fresco = fd.evaluar_frescura(
        [p for p in piezas if isinstance(p, dict)], ahora)
    bloqueos += bloq_fresco

    # A: cablear L3 al veredicto único — CT.gov nunca confirma elegibilidad (regla PNV21)
    bloqueos += el.evaluar_elegibilidad(
        [p for p in piezas if isinstance(p, dict)], ahora, comprobador_ct=comprobador_ct)

    # L-cotejo: cortafuegos de cotejo OBLIGATORIO — una afirmación de presencia/ausencia de una
    # alteración sin fuente primaria + fecha + plataforma NO es entregable, y la ausencia nunca es
    # «no existe» (el error del 27/6/26). Se suma al veredicto único, fail-closed.
    bloqueos += cot.evaluar_cotejo([p for p in piezas if isinstance(p, dict)])

    entregable = not bloqueos
    return resultados, entregable, bloqueos


def main(argv):
    if not argv:
        print("uso: dosier_invariantes.py <dosier.jsonl>")
        return 2
    piezas, errores = fd.cargar_jsonl(argv[0])
    if errores:
        print("=== L1 · invariantes del Dosier ===")
        for e in errores:
            print("  ✗ %s" % e)
        print("FORMATO INVÁLIDO → fail-closed (no se puede evaluar)")
        return 2
    resultados, entregable, bloqueos = evaluar(piezas)
    print("=== L1 · invariantes del Dosier (determinista, sin modelo) ===")
    for r in resultados:
        print("  %s [%s] %s" % ("✓" if r["schema_ok"] else "✗",
                                "schema OK" if r["schema_ok"] else "schema FALLA", r["id"]))
    if entregable:
        print("✅ ENTREGABLE: schema + invariantes + frescura en verde")
        return 0
    print("🛑 NO entregable (fail-closed):")
    for b in bloqueos:
        print("   - %s" % b)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
