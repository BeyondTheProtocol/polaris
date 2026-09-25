#!/usr/bin/env python3
"""Tests del protocolo de decisión de alto riesgo (tools/decision_alto_riesgo.py).

Local, sin red, sin LLM, sin estado vivo (BTP_STATE_DIR a un tmp). Demuestra que el
protocolo SE DISPARA con el criterio objetivo y que REGISTRA el debate (panel paralelo +
verificación obligatoria + discrepancias + acta auditable), fail-closed.
"""
import hashlib
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

# Stub del soporte cita→afirmación (sin red). Una cita con dígitos 11111111 «dice» un 14 % y nada más;
# el resto de citas no traen abstract (NO_EVALUABLE: no bloquea, como antes del 24-sep).
import soporte_cita as _sc  # noqa: E402


def _stub_soporte(afirmacion, cita):
    if "22222222" in cita:        # fase 2: las cifras están, pero el juez dice que se usan mal
        return {"estado": "CONTRADICE", "motivo": "juez local: contradice (eje poblacion)"}
    if "33333333" in cita:
        return {"estado": "PARCIAL", "motivo": "juez local: parcial (eje direccion)"}
    if "11111111" in cita:
        return _sc.soporte(afirmacion, cita, fetch=lambda c: (
            "pmid", "11111111", "Objective response rate was 14% in the HER2-low cohort."))
    return {"estado": "NO_EVALUABLE", "motivo": "stub"}


dar._SOPORTE = _stub_soporte
_ABS_14 = "Objective response rate was 14% in the HER2-low cohort of 120 patients."
dar._FRAGMENTO = lambda afirmacion, cita, fragmento: _sc.cotejar_fragmento(
    afirmacion, cita, fragmento, fetch=lambda c: ("pmid", "11111111", _ABS_14),
    texto_completo=lambda t, i: None)

# Bóveda clínica DE PEGA (auditoría Gorgojo 1.1): desde el 24-sep el panel abre la fuente y busca
# dentro el fragmento citado, así que las pruebas necesitan un informe que exista de verdad. Es
# sintético y vive en un tmp: la bóveda real no se toca desde los tests.
BOVEDA = os.path.join(tempfile.mkdtemp(prefix="dar_boveda_"), "_PRIVADO_CLINICO")
os.makedirs(BOVEDA)
os.environ["BTP_BOVEDA_CLINICA"] = BOVEDA
FRAGMENTO = "HER2 IHC 0 en la biopsia hepática, Ki-67 del 30 %"
_TEXTO_INFORME = "Informe sintético de prueba.\n\nResultado: " + FRAGMENTO + ".\nFin del informe.\n"
INFORME = "_PRIVADO_CLINICO/informe-prueba.md"
with open(os.path.join(BOVEDA, "informe-prueba.md"), "w", encoding="utf-8") as _f:
    _f.write(_TEXTO_INFORME)
HASH_INFORME = hashlib.sha256(_TEXTO_INFORME.encode("utf-8")).hexdigest()
ACCESOS = []  # cada lectura de la bóveda tiene que dejar su línea (aquí, en memoria)
try:
    import fuente_clinica as fc  # noqa: E402
    fc._IDENTIDAD = lambda texto, ruta: ("coincide", "stub de test")
    fc._LOG = lambda agente, resultado, ruta: ACCESOS.append((agente, resultado, ruta))
except ImportError:  # antes del arreglo el módulo no existe: los tests nuevos salen en rojo
    fc = None


def comprobado(por="verificacion", resultado="confirmado", contra=INFORME, frag=FRAGMENTO):
    """Bloque `comprobacion` REAL (veredicto de un comprobador distinto). El nuevo `verificado`
    NO es el booleano auto-declarado: lo deriva esto. `por` debe ser != del agente que afirma.
    Con un puntero clínico, `fragmento` es el texto que el comprobador dice haber visto en la
    fuente: el panel lo busca dentro del fichero (auditoría Gorgojo 1.1)."""
    return {"por": por, "resultado": resultado, "contra_fuente": contra, "fragmento": frag}


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
         # El veredicto red-team de `verificacion` lo comprueba `herramientas-medicas`: si lo
         # comprobara `comite-medico` (a quien `verificacion` comprueba), sería un círculo.
         "comprobacion": comprobado(por="herramientas-medicas"),
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



# ── AUDITORÍA GORGOJO 1.1 (22-sep-26): el acta no puede sellar una fuente que no existe ──────
# Reproducido el 24-sep antes de arreglarlo: dos lentes del MISMO agente, «comprobadas» por
# `verificacion` contra `_PRIVADO_CLINICO/informe-que-no-existe-2099.md`, daban entregable=True,
# confianza alta y 0 bloqueos. Nadie abría nada: se validaba la FORMA del puntero con una regex.
INEXISTENTE = "_PRIVADO_CLINICO/informe-que-no-existe-2099.md"
data_gorgojo = {"decision": "¿biopsiar L1 o L3?", "veredictos": [
    {"lente": "lente-alfa", "agente": "comite-medico", "postura": "a_favor", "confianza": "alta",
     "porque": "x", "fuente": INEXISTENTE,
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": INEXISTENTE}},
    {"lente": "lente-beta", "agente": "comite-medico", "postura": "a_favor", "confianza": "alta",
     "porque": "y", "fuente": INEXISTENTE,
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": INEXISTENTE}}]}
_, _, vers_g, _, bloq_g, conf_g, ent_g = dar.evaluar(data_gorgojo)
check("GORGOJO 1.1: el caso reproducido del auditor ya NO es entregable", not ent_g)
check("GORGOJO 1.1: un puntero a un fichero que NO existe no se sella como verificado",
      not any(v["verificado"] for v in vers_g))
check("GORGOJO 1.1: dos lentes del MISMO agente no cuentan como panel independiente",
      any("agente" in b and "INCOMPLETO" in b for b in bloq_g))
check("GORGOJO 1.1: la fuente propia inexistente de la lente también bloquea",
      any("no existe" in b for b in bloq_g))

# Con una fuente REAL, el fragmento citado tiene que estar DENTRO. Si no está, no verifica.
_, _, vers_f, _, bloq_f, _, ent_f = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": INFORME,
     "comprobacion": comprobado(frag="un texto que el informe de prueba no contiene en absoluto")},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": INFORME,
     "comprobacion": comprobado()}]})
check("GORGOJO 1.1: fuente real pero fragmento AUSENTE → no verificado",
      (not ent_f) and not vers_f[0]["verificado"] and vers_f[1]["verificado"])

# Un fragmento de 3 letras casa con cualquier informe: no es un cotejo.
_, _, vers_c, _, _, _, _ = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": INFORME,
     "comprobacion": comprobado(frag="HER2")},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()}]})
check("GORGOJO 1.1: un fragmento trivialmente corto no cuenta como cotejo", not vers_c[0]["verificado"])

# Un puntero clínico SIN fragmento: existe el fichero, pero nadie dice qué se comprobó en él.
_, _, vers_s, _, _, _, _ = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": INFORME,
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": INFORME}},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()}]})
check("GORGOJO 1.1: puntero clínico sin `fragmento` → no verificado", not vers_s[0]["verificado"])

# La misma clase por la otra puerta: una cita externa INVENTADA en `contra_fuente`.
_, _, vers_p, _, _, _, _ = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:39538331",
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": "PMID:00000000"}},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()}]})
check("GORGOJO 1.1: `contra_fuente` con un PMID que no existe → no verificado",
      not vers_p[0]["verificado"])

# SOPORTE (24-sep-26): una cita que EXISTE pero no dice la cifra que se le atribuye → no verificado.
_, _, vers_sop, _, _, _, _ = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:11111111",
     "porque": "La tasa de respuesta fue del 41 % en HER2-low",
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": "PMID:11111111"}},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "PMID:11111111",
     "porque": "La tasa de respuesta fue del 14 % en HER2-low",
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": "PMID:11111111",
                      "fragmento": "Objective response rate was 14% in the HER2-low cohort"}}]})
check("SOPORTE: cita real con cifra que NO dice (41 %) → no verificado",
      not vers_sop[0]["verificado"] and "NO respalda" in vers_sop[0]["verificado_motivo"])
check("SOPORTE: la misma cita con su cifra real (14 %) → verificado", vers_sop[1]["verificado"])
check("SOPORTE: el acta anota el estado del cotejo de soporte",
      vers_sop[1].get("cotejo", {}).get("soporte") == "RESPALDA_LITERAL")

# FRASE DE LA FUENTE (24-sep-26): con cifras y cita externa, el comprobador señala la frase literal.
def _con_frag(frag):
    c = {"por": "verificacion", "resultado": "confirmado", "contra_fuente": "PMID:11111111"}
    if frag is not None:
        c["fragmento"] = frag
    return dar.evaluar({"decision": "x", "veredictos": [
        {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:11111111",
         "porque": "La tasa de respuesta fue del 14 % en HER2-low", "comprobacion": c},
        {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()}]})


_v = _con_frag(None)[2][0]
check("FRASE: cita externa con cifras SIN fragmento → no verificado",
      not _v["verificado"] and "fragmento" in _v["verificado_motivo"])
_v = _con_frag("Objective response rate was 14% in the HER2-low cohort")[2][0]
check("FRASE: fragmento literal con la cifra → verificado", _v["verificado"])
check("FRASE: el acta enseña la frase pública cotejada",
      "Objective response rate was 14%" in dar.render(*_con_frag("Objective response rate was 14% in the HER2-low cohort")))
_v = _con_frag("Objective response rate was 41% in the HER2-low cohort")[2][0]
check("FRASE: fragmento que NO está en la fuente → no verificado",
      not _v["verificado"] and "FRAGMENTO" in _v["verificado_motivo"])
_v = _con_frag("in the HER2-low cohort of 120 patients enrolled")[2][0]
check("FRASE: fragmento real pero SIN la cifra de la afirmación → no verificado",
      not _v["verificado"] and "SIN_CIFRAS" in _v["verificado_motivo"])

# FASE 2 (24-sep-26): el juez semántico dice CONTRADICE → no verificado; PARCIAL → verificado con aviso.
_, _, vers_j, _, _, _, _ = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": "PMID:22222222",
     "porque": "En la cohorte HR+ la SLP fue de 9,9 meses",
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": "PMID:22222222"}},
    {"lente": "oncologo-virtual", "postura": "a_favor", "fuente": "PMID:33333333",
     "porque": "Mejoró la SLP y la SG",
     "comprobacion": {"por": "verificacion", "resultado": "confirmado", "contra_fuente": "PMID:33333333"}}]})
check("FASE 2: el juez dice CONTRADICE → no verificado",
      not vers_j[0]["verificado"] and "juez" in vers_j[0]["verificado_motivo"])
check("FASE 2: PARCIAL → verificado, con el aviso en el acta",
      vers_j[1]["verificado"] and "PARCIAL" in vers_j[1].get("cotejo", {}).get("aviso", ""))

# Un nombre de agente inventado no suma independencia.
_, _, _, _, bloq_i, _, ent_i = dar.evaluar({"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()},
    {"lente": "experto-que-no-existe", "postura": "a_favor", "fuente": INFORME,
     "comprobacion": comprobado()}]})
check("GORGOJO 1.1: un agente que no está en el roster no cuenta como voz independiente",
      (not ent_i) and any("INCOMPLETO" in b for b in bloq_i))

# El acta enseña la huella de la fuente, NUNCA el fragmento (es N2 y la leen sus médicas).
txt_ok = dar.render(*dar.evaluar(data_ok))
check("GORGOJO 1.1: el acta muestra el sha256 de la fuente cotejada", HASH_INFORME[:12] in txt_ok)
check("GORGOJO 1.1: el acta NO reproduce el fragmento clínico", FRAGMENTO not in txt_ok)
check("GORGOJO 1.1: cada lectura de la bóveda deja su línea en el registro de accesos",
      any("COTEJO" in a[1] for a in ACCESOS))

# Las actas se guardan en CASA BASE, no en el árbol efímero de un worktree (se perdían al podar).
import subprocess as _sp  # noqa: E402
_env = {k: v for k, v in os.environ.items() if k != "BTP_STATE_DIR"}
_out = _sp.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import decision_alto_riesgo "
                "as d, _casa; print(d.ACTAS.startswith(_casa.state_dir()))" % os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")],
               capture_output=True, text=True, env=_env).stdout.strip()
check("GORGOJO 1.1: el registro de actas resuelve a casa base (no al worktree)", _out == "True")


# ── AUDITORÍA GORGOJO 1.2 (22-sep-26): el disparador clasifica por ACCIÓN, no por «riesgo» ──
# Reproducido el 24-sep: las 4 preguntas del auditor daban aplica=False. La ausencia de la
# palabra «riesgo» no prueba que la acción sea inocua.
DEBEN_DISPARAR = [
    "¿debo duplicar la dosis?",
    "¿debo suspender el tratamiento?",
    "¿qué lesión conviene biopsiar?",
    "¿es seguro continuar la medicación?",
    "me planteo dejar el letrozol una temporada",                       # paráfrasis
    "kambiar de linea de tratamiento o seguir?",                        # errata
    "no continuar con la medicacion, ¿es razonable?",                   # negación
    "doblar la dosis de capecitabina, ¿pros y contras?",
    "¿qué lesion biopsiamos, la hepatica o la del nodulo?",
    "valorar si empezar el ensayo ahora o esperar",
    "¿merece la pena la radioterapia sobre la L3?",
    "¿deberia bajar la docis por la toxicidad?",                        # errata
    "¿y si paramos la quimio un ciclo?",
]
NO_DEBEN_DISPARAR = [
    "organiza la agenda de la semana y un resumen de los correos",
    "archiva la nota de la biopsia del 18-ago en la fuente de verdad",
    "recuérdame la cita de la biopsia del martes",
    "resume el informe de anatomía patológica cuando llegue",
    "formatea la tabla de los tratamientos que ya ha hecho",
    "manda el borrador a {{CONTACTO}}",
    "¿qué hora es la cita de mañana?",
    "añade a la cronología que empezó el tratamiento en marzo",
    "cuántos ensayos de vacuna hay abiertos en España",
    "busca papers sobre biopsia líquida y neoantígenos",
    "¿podemos mover la biopsia al jueves?",                             # logística con «?»
    "¿cómo va el seguimiento del tratamiento?",                         # «seguimiento» ≠ seguir
    "¿qué opciones hay para el tratamiento que ya conoces?",            # «para» preposición
]
for q in DEBEN_DISPARAR:
    check("GORGOJO 1.2: dispara → %s" % q, dar.disparar(q)[0])
for q in NO_DEBEN_DISPARAR:
    check("GORGOJO 1.2: NO dispara → %s" % q, not dar.disparar(q)[0])

# Referencia a un turno anterior: «¿lo hacemos?» solo no dice qué; con el contexto, sí.
check("GORGOJO 1.2: «¿lo hacemos?» sin contexto no se inventa una decisión",
      not dar.disparar("¿lo hacemos entonces?")[0])
check("GORGOJO 1.2: «¿lo hacemos?» con una acción clínica en el turno anterior → dispara",
      dar.disparar("¿lo hacemos entonces?",
                   contexto="la oncóloga propone suspender el tratamiento dos semanas")[0])
# Una acción clínica detectada no se apaga con --no-riesgo: el protocolo nunca se desactiva en
# silencio para algo clínico (regla ya escrita en el docstring del disparador).
check("GORGOJO 1.2: --no-riesgo no apaga una acción clínica detectada",
      dar.disparar("¿debo duplicar la dosis?", riesgo=False)[0])

# ── JUEZ Y PARTE (24-sep-26, decisión de {{TITULAR}}): dos agentes no se comprueban el uno al otro ──
# Hueco que vio `consejero-arquitectura`, no la auditoría: `verificacion` comprueba a
# `comite-medico` y `comite-medico` comprueba el veredicto red-team de `verificacion` → cada uno
# sella al otro. `verificacion` sigue pudiendo ser gate Y abogado del diablo; lo que no puede es
# que su propio veredicto lo selle alguien a quien ella acaba de sellar.
data_circulo = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()},
    {"lente": "verificacion (abogado del diablo)", "postura": "matiz", "fuente": INFORME,
     "comprobacion": comprobado(por="comite-medico")}]}
_, _, vers_ci, _, bloq_ci, _, ent_ci = dar.evaluar(data_circulo)
check("JUEZ Y PARTE: comprobación mutua (A comprueba a B y B a A) → no entregable",
      (not ent_ci) and any("mutua" in b for b in bloq_ci))
check("JUEZ Y PARTE: el bloqueo dice quién comprueba a quién", any("comite-medico" in b and "verificacion" in b
                                                                   and "mutua" in b for b in bloq_ci))
data_sin_circulo = {"decision": "x", "veredictos": [
    {"lente": "comite-medico", "postura": "a_favor", "fuente": INFORME, "comprobacion": comprobado()},
    {"lente": "verificacion (abogado del diablo)", "postura": "matiz", "fuente": INFORME,
     "comprobacion": comprobado(por="herramientas-medicas")}]}
*_, ent_sc = dar.evaluar(data_sin_circulo)
check("JUEZ Y PARTE: el red-team de verificacion comprobado por herramientas-medicas → entregable", ent_sc)

print("RESULTADO decision_alto_riesgo: %d OK, %d fallos" % (total - fallos, fallos))
print("✅ PROTOCOLO DE DECISIÓN DE ÉLITE EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
