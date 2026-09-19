#!/usr/bin/env python3
"""Tests del protocolo de decisión de alto riesgo (tools/decision_alto_riesgo.py).

Local, sin red, sin LLM, sin estado vivo (BTP_STATE_DIR a un tmp). Demuestra que el
protocolo SE DISPARA con el criterio objetivo y que REGISTRA el debate (panel paralelo +
verificación obligatoria + discrepancias + acta auditable), fail-closed.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="dar_test_")
import decision_alto_riesgo as dar  # noqa: E402


# Stub determinista del verificador de EXISTENCIA (sin red): el cable #0 se inyecta aquí.
# Una cita cuyos dígitos sean 00000000 → inexistente (FABRICADA); 99999999 → no_resoluble;
# cualquier otra → existe. Producción usa verifica_citas real.
def _stub_existencia(ids):
    out = []
    for i in ids:
        d = "".join(c for c in i if c.isdigit())
        est = "no_existe" if d == "00000000" else ("no_resoluble" if d == "99999999" else "existe")
        out.append({"entrada": i, "id": i, "estado": est, "detalle": "stub", "fuente": "stub"})
    return out


dar._COMPROBADOR = _stub_existencia


def comprobado(por="verificacion", resultado="confirmado", contra="_PRIVADO_CLINICO/informe"):
    """Bloque `comprobacion` REAL (veredicto de un comprobador distinto). El nuevo `verificado`
    NO es el booleano auto-declarado: lo deriva esto. `por` debe ser != del agente que afirma."""
    return {"por": por, "resultado": resultado, "contra_fuente": contra}


fallos = 0
total = 0


def check(nombre, cond):
    global fallos, total
    total += 1
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


# ── 1. DISPARADOR ────────────────────────────────────────────────────────────
# a) clínica + riesgo (léxico) → dispara
ap, mot, _ = dar.disparar("¿qué lesión biopsiar? es una ventana única, no se puede repetir")
check("dispara con CLÍNICA + ALTO RIESGO (léxico)", ap)

# b) clínica pero sin riesgo → no dispara solo, pero avisa de forzar
ap, mot, _ = dar.disparar("preparar la lista de preguntas sobre el ensayo para la médica")
check("clínica sin riesgo → no dispara solo (avisa de --forzar)", (not ap) and "forzar" in mot)

# c) info/organización pura → no dispara
ap, _, _ = dar.disparar("organiza la agenda de la semana y un resumen de los correos")
check("organización/info pura → no dispara", not ap)

# d) override explícito del agente (sabe que es clínico e irreversible) → dispara
ap, _, _ = dar.disparar("decidir entre opción A y B", clinica=True, riesgo=True)
check("override clinica+riesgo del agente → dispara", ap)

# e) --forzar dispara siempre (duda razonable)
ap, mot, _ = dar.disparar("no sé si esto es delicado", forzar=True)
check("--forzar dispara siempre", ap and "forzado" in mot)

# f) el disparador NUNCA puede apagarse en silencio para algo clínico-irreversible explícito:
#    aunque el texto no traiga léxico, si el agente marca ambos ejes, dispara.
ap, _, _ = dar.disparar("xyz", clinica=True, riesgo=True)
check("clínico+irreversible marcado → no se apaga en silencio", ap)


# ── 2+3. PANEL PARALELO + VERIFICACIÓN OBLIGATORIA ──────────────────────────
# g) panel completo, verificado, consenso → ENTREGABLE (rc 0)
data_ok = {
    "decision": "¿incluir RNA-seq + immunopeptidomics en los cores de la re-biopsia?",
    "contexto": "ventana única; el pipeline de neoantígenos lo necesita",
    "veredictos": [
        {"lente": "comite-medico", "postura": "a_favor", "confianza": "alta",
         "porque": "TMB baja → neoantígenos centrados en ARN (fusiones/splicing)",
         "fuente": "reference-clinical-profile; PMID:39538331", "comprobacion": comprobado()},
        {"lente": "oncologo-virtual", "postura": "a_favor", "confianza": "media",
         "porque": "coherente con el protocolo de Zúrich; no añade riesgo a la paciente",
         "fuente": "Protocolo_Biopsia_Vacuna_Personalizada", "comprobacion": comprobado()},
        {"lente": "verificacion (abogado del diablo)", "postura": "matiz", "confianza": "media",
         "porque": "viable solo si el laboratorio confirma rendimiento de ARN del core",
         "fuente": "checklist {{CENTRO}} 13-core",
         "comprobacion": comprobado(por="comite-medico"),
         "discrepa_en": "condiciona a confirmación de rendimiento del tejido"},
    ],
}
*_, entregable = dar.evaluar(data_ok)
# matiz + a_favor cuenta como discrepancia REGISTRADA (no bloqueante: no hay a_favor vs en_contra)
dec, ctx, vers, disc, bloq, conf, entregable = dar.evaluar(data_ok)
check("panel completo+verificado+sin oposición → entregable", entregable)
check("registra las 3 lentes del panel", len(vers) == 3)
check("registra la discrepancia de matiz aunque sea entregable", any("matiz" in d for d in disc))
check("confianza = eslabón más flojo (media)", conf == "media")

# h) panel en SERIE (1 sola lente) → NO entregable
data_serie = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "confianza": "alta",
     "fuente": "PMID:1", "verificado": True}]}
*_, entregable = dar.evaluar(data_serie)
check("1 sola lente (panel en serie) → bloqueado", not entregable)

# i) veredicto SIN verificación real (sin bloque comprobacion) → NO entregable
data_sinver = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:1", "comprobacion": comprobado()},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "ficha"}]}  # sin comprobacion
_, _, _, _, bloq, _, entregable = dar.evaluar(data_sinver)
check("un veredicto sin verificación REAL → bloqueado",
      (not entregable) and any("SIN verificación REAL" in b for b in bloq))

# j) DISCREPANCIA ABIERTA (a favor vs en contra) → no se da como consenso (rc 1)
data_disc = {"decision": "¿biopsiar L1 o L3?", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "confianza": "alta",
     "porque": "L1 capta el clon dominante", "fuente": "PET-TAC", "comprobacion": comprobado()},
    {"lente": "oncologo-virtual", "postura": "en_contra", "confianza": "media",
     "porque": "L3 es más accesible y menos riesgo para la paciente", "fuente": "MTB",
     "comprobacion": comprobado()}]}
dec, ctx, vers, disc, bloq, conf, entregable = dar.evaluar(data_disc)
check("a favor vs en contra → NO entregable como consenso", not entregable)
check("la discrepancia abierta queda registrada en el debate", len(disc) == 2)
check("confianza marca discrepancia abierta → a {{TITULAR}} y médicas", "discrepancia abierta" in conf)

# k) fuente ausente → avisa (trazabilidad)
data_nofuente = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "comprobacion": comprobado()},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "ok", "comprobacion": comprobado()}]}
_, _, _, _, bloq, _, _ = dar.evaluar(data_nofuente)
check("veredicto sin fuente → avisa", any("no cita FUENTE" in b for b in bloq))


# ── 4. ACTA AUDITABLE ────────────────────────────────────────────────────────
# l) el render trae el banner no-suprimible y el debate
txt = dar.render(*dar.evaluar(data_ok))
check("acta con banner no-consejo-médico", "NO es consejo médico" in txt and txt.count("consejo médico") >= 2)
check("acta registra quién opinó qué (lentes)", "comite-medico" in txt and "oncologo-virtual" in txt)
check("acta muestra el veredicto final con confianza", "Confianza agregada" in txt)

# m) guardar_acta deja un fichero auditable y datado (0600), en el state aislado
ruta = dar.guardar_acta(txt, etiqueta="test-biopsia")
check("guarda acta auditable en disco", os.path.exists(ruta) and ruta.endswith(".md"))
check("permisos 0600 del acta", (os.stat(ruta).st_mode & 0o777) == 0o600)


# ── 2b. EL CABLE #0 — EXISTENCIA DE LA CITA (no la palabra del modelo) ───────
def _boom(ids):
    raise RuntimeError("offline")


# n) cita FABRICADA aunque el agente se AUTO-DECLARE verificado=True → BLOQUEADO.
#    (Es el agujero exacto que cazó el red-team: el gate no se fía del booleano.)
data_fabri = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "PMID:00000000", "verificado": True}]}
_, _, _, _, bloq, _, entregable = dar.evaluar(data_fabri)
check("cita FABRICADA con verificado=True auto-declarado → BLOQUEADO", not entregable)
check("el bloqueo nombra lo FABRICADO (no se fía del booleano)", any("FABRICADO" in b for b in bloq))

# o) existencia NO resoluble (registro mudo) → fail-closed, no entregable
data_nores = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "PMID:99999999", "verificado": True}]}
_, _, _, _, bloq, _, entregable = dar.evaluar(data_nores)
check("existencia no resoluble → fail-closed", (not entregable) and any("EXISTENCIA" in b for b in bloq))

# p) refs INTERNAS (sin ID externo) → existencia n/a, no sobre-bloquea
data_interna = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "reference-clinical-profile",
     "comprobacion": comprobado()},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "checklist {{CENTRO}} 13-core",
     "comprobacion": comprobado()}]}
_, _, vers_i, _, _, _, entregable = dar.evaluar(data_interna)
check("ref interna sin ID externo → existencia n/a (no sobre-bloquea)",
      entregable and all(v["cita_existencia"] == "n/a" for v in vers_i))

# q) comprobador CAÍDO (excepción) y hay cita externa → fail-closed
_, _, _, _, _, _, entregable_off = dar.evaluar(
    {"decision": "x", "veredictos": [
        {"lente": "a", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True},
        {"lente": "b", "postura": "a_favor", "fuente": "NCT07112053", "verificado": True}]},
    comprobador=_boom)
check("comprobador caído + cita externa → fail-closed (no entregable)", not entregable_off)

# r) el acta RENDER muestra el estado de existencia (trazable)
check("el render expone la existencia de la cita", "Existencia de la cita" in dar.render(*dar.evaluar(data_ok)))


# ── 2c. REGRESIÓN del endurecimiento del cable (esta tanda) ──────────────────
# Allow-list estricta: 'confirmada' SOLO si todo es 'existe' y cada token tiene su resultado.
# El código viejo (any(no_resoluble) or len<len) fail-OPENeaba con estas respuestas hostiles.
data_cable = {"decision": "x", "veredictos": [
    {"lente": "a", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True},
    {"lente": "b", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True}]}

# s) el registro responde 'existe' pero con una `entrada` que NO cuadra con el token (id cruzado)
def _comp_cruzado(ids):
    return [{"id": t, "entrada": "OTRA-COSA-DISTINTA", "estado": "existe"} for t in ids]
_, _, _, _, _, _, entregable_cruz = dar.evaluar(data_cable, comprobador=_comp_cruzado)
check("REGRESIÓN cable: 'existe' con entrada que no cuadra → fail-closed (el viejo fail-OPENeaba)",
      not entregable_cruz)

# t) el registro devuelve un estado INESPERADO ('quizas') → no puede dar 'confirmada'
def _comp_raro(ids):
    return [{"id": t, "entrada": t, "estado": "quizas"} for t in ids]
_, _, _, _, _, _, entregable_raro = dar.evaluar(data_cable, comprobador=_comp_raro)
check("REGRESIÓN cable: estado inesperado del registro → fail-closed (no 'confirmada')",
      not entregable_raro)


# ── 2d. EL CABLE DEL `verificado`: veredicto REAL, no la palabra del modelo (error 27/6/26) ──
# u) `verificado: True` AUTO-DECLARADO, sin bloque `comprobacion` → NO cuenta (no entregable).
data_autodeclara = {"decision": "x", "veredictos": [
    {"lente": "a", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True},
    {"lente": "b", "postura": "a_favor", "fuente": "PMID:39538331", "verificado": True}]}
_, _, _, _, bloq_ad, _, entregable_ad = dar.evaluar(data_autodeclara)
check("`verificado:True` auto-declarado sin comprobacion → NO cuenta (bloqueado)",
      (not entregable_ad) and any("SIN verificación REAL" in b for b in bloq_ad))

# v) AUTO-comprobación (el comprobador es el propio agente) = sello de goma → no cuenta.
data_autocomp = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "agente": "comite-medico", "postura": "a_favor",
     "fuente": "PMID:39538331", "comprobacion": comprobado(por="comite-medico")},
    {"lente": "b", "postura": "a_favor", "fuente": "PMID:39538331", "comprobacion": comprobado()}]}
_, _, _, _, bloq_ac, _, entregable_ac = dar.evaluar(data_autocomp)
check("auto-comprobación (comprobador == agente) → no cuenta (sello de goma)",
      (not entregable_ac) and any("auto-comprob" in b.lower() for b in bloq_ac))

# w) comprobación con resultado != 'confirmado' (refutado/no_concluyente) → no cuenta.
data_refutado = {"decision": "x", "veredictos": [
    {"lente": "a", "postura": "a_favor", "fuente": "PMID:39538331",
     "comprobacion": comprobado(resultado="no_concluyente")},
    {"lente": "b", "postura": "a_favor", "fuente": "PMID:39538331", "comprobacion": comprobado()}]}
_, _, _, _, _, _, entregable_ref = dar.evaluar(data_refutado)
check("comprobación 'no_concluyente' → no verificado (solo 'confirmado' cuenta)", not entregable_ref)

# x) comprobación sin `contra_fuente` (no hubo cotejo contra nada real) → no cuenta.
data_sincontra = {"decision": "x", "veredictos": [
    {"lente": "a", "postura": "a_favor", "fuente": "PMID:39538331",
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": ""}},
    {"lente": "b", "postura": "a_favor", "fuente": "PMID:39538331", "comprobacion": comprobado()}]}
_, _, _, _, _, _, entregable_sc = dar.evaluar(data_sincontra)
check("comprobación sin contra_fuente → no verificado (no hubo cotejo real)", not entregable_sc)


print("RESULTADO decision_alto_riesgo: %d OK, %d fallos" % (total - fallos, fallos))
print("✅ PROTOCOLO DE DECISIÓN DE ÉLITE EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
