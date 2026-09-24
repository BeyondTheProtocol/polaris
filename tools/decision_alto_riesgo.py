#!/usr/bin/env python3
"""tools/decision_alto_riesgo.py — Protocolo de DECISIÓN DE ALTO RIESGO (panel de élite).

Las decisiones clínicas difíciles del caso de {{TITULAR}} (BC-NED) no deben salir de UNA sola
voz en serie. Este módulo cablea el método de élite que YA está en los prompts (5 lentes de
comite-medico, watchdog de verificacion, validador≠constructor de herramientas-medicas) en
un PROTOCOLO que se dispara solo y deja el debate POR ESCRITO:

  1. DISPARADOR objetivo — `disparar`: decide si una decisión exige el protocolo
     (criterio: CLÍNICA + ALTO RIESGO/IRREVERSIBLE, o una ACCIÓN clínica de alto riesgo por sí
     misma —dosis, suspensión, cambio de línea, procedimiento— que se está decidiendo).
     Ante la duda → exige protocolo.
  2. PANEL PARALELO — `acta`: ≥2-3 veredictos INDEPENDIENTES (agentes distintos del gabinete,
     no nombres de lente), en vez de uno en serie. Registra quién opinó qué, dónde discreparon.
  3. VERIFICACIÓN OBLIGATORIA — (a) la EXISTENCIA de cada cita externa (DOI/PMID/NCT/arXiv)
     la confirma el REGISTRO vía `verifica_citas` (sin LLM): una cita inexistente = FABRICADA
     → se BLOQUEA; si no se puede confirmar (red/registro mudo) → fail-closed. (b) además,
     cada veredicto debe venir con una `comprobacion` de otro agente, y el panel la COMPRUEBA:
     un puntero a la bóveda se abre y el `fragmento` citado tiene que estar dentro
     (`fuente_clinica`); una cita externa tiene que existir. El gate NO se fía del booleano que
     un agente se auto-declara — principio #0: el modelo no certifica; lo certifican el CÓDIGO,
     el REGISTRO o el HUMANO. Si algo falla → se BLOQUEA (no llega a {{TITULAR}}).
     Límite: esto acredita que la fuente existe y contiene lo citado, no que lo citado sostenga
     la afirmación. Eso sigue siendo revisión clínica.
  4. ACTA AUDITABLE — veredicto final con su CONFIANZA y las discrepancias explícitas, para
     que {{TITULAR}} y sus médicas vean el RAZONAMIENTO, no solo la conclusión.

Es DETERMINISTA a propósito (patrón de paso_consolidacion.py): NO razona ni concluye nada
clínico — toma lo que los agentes-lente ya dictaminaron (JSON) y garantiza la FORMA, la
SEGURIDAD (fail-closed) y la TRAZABILIDAD del acta. La prosa fina (TL;DR en la voz de
{{TITULAR}}) la pone el agente encima.

MURO (hereda del núcleo, no se re-discute):
  · APOYO A LA DECISIÓN, **NO consejo médico**. El acta describe y equipa, NO concluye:
    deciden sus médicas. Banner no-suprimible en cada acta.
  · Local y privado: solo transforma texto que ya tienes; nada hacia fuera. Términos
    genéricos; no metas PII/HLA/mutaciones crudas en el acta (cita la fuente por ID).

Salida: el acta en markdown.  Códigos de salida:
  0 = acta limpia y entregable
  1 = el protocolo APLICA pero el acta NO es entregable (panel incompleto / sin verificar /
      discrepancia abierta) → para, completa el panel o eleva a {{TITULAR}} con la discrepancia.
"""
import json
import os
import re
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402
import fuente_clinica  # noqa: E402

REPO = os.environ.get("BTP_REPO") or os.path.dirname(HERE)
# Registro vivo de actas: ESTADO, así que resuelve a casa base (`_casa.state_dir()`), no al árbol
# donde corra el código. Hasta el 24-sep resolvía con `dirname(__file__)`: desde un worktree —el
# caso normal— las actas se escribían en el árbol efímero, se perdían al podarlo, y la CLI decía
# «acta guardada en…». Misma clase que `feedback-estado-vivo-resuelve-casa-base`. Tests: BTP_STATE_DIR.
STATE = os.environ.get("BTP_STATE_DIR") or _casa.state_dir()
ACTAS = os.path.join(STATE, "decisiones_alto_riesgo")
# Los agentes son CÓDIGO versionado: se leen del árbol en el que se trabaja.
AGENTES_DIR = os.path.join(os.path.dirname(HERE), ".claude", "agents")

BANNER = ("Apoyo a la decisión. **NO es consejo médico.** El acta equipa y describe el "
          "debate; **no concluye**. Deciden {{TITULAR}} y sus médicas.")

# Posturas que un veredicto puede tomar sobre la pregunta de decisión.
POSTURAS = ("a_favor", "en_contra", "matiz", "abstiene")
POSTURA_TXT = {
    "a_favor": "✅ a favor (go)",
    "en_contra": "⛔ en contra (no-go)",
    "matiz": "🟡 a favor con matiz / condicionado",
    "abstiene": "⚪ se abstiene (fuera de su lente / sin evidencia)",
}

# ── 1. DISPARADOR ────────────────────────────────────────────────────────────
# Criterio OBJETIVO (no "¿parece importante?"). Dos ejes; el protocolo aplica si AMBOS.
# Eje A — ¿es una decisión CLÍNICA del caso?  Eje B — ¿es de ALTO RIESGO / irreversible?
# Determinista por léxico (ES). Ante la duda, se dispara (fail-safe hacia el protocolo).

_CLINICO = (
    "tratamiento", "terapia", "fármaco", "farmaco", "dosis", "quimio", "quimioterapia",
    "radio", "radioterapia", "cirugía", "cirugia", "biopsia", "re-biopsia", "rebiopsia",
    "ensayo", "elegibilidad", "candidatura", "criterio", "recist", "medible",
    "vacuna", "neoantígeno", "neoantigeno", "diana", "mutación", "mutacion",
    "secuencia de tratamiento", "secuenciar el tratamiento", "washout", "interrumpir",
    "suspender", "cambiar de línea", "cambiar de linea", "línea de tratamiento",
    "lesión a biopsiar", "lesion a biopsiar", "qué lesión", "que lesion", "protocolo clínico",
    "protocolo clinico", "consentir", "consentimiento clínico", "prrt", "teranóstica",
    "teranostica", "lutecio", "platino", "serd", "elacestrant",
)
_RIESGO = (
    "irreversible", "no tiene vuelta", "sin vuelta atrás", "sin vuelta atras",
    "alto riesgo", "vida o muerte", "tóxico", "toxico", "toxicidad", "ventana única",
    "ventana unica", "una sola oportunidad", "no se puede repetir", "decisión definitiva",
    "decision definitiva", "compromete", "descarta otras", "cierra la puerta", "quema",
    "única muestra", "unica muestra", "un solo intento",
)
# Señales de que NO es una decisión, solo info/organización (degrada hacia "no protocolo").
_NO_DECISION = ("resumen", "organiza", "agenda", "recordatorio", "archivar", "formatear")

# ── Eje ACCIÓN (auditoría Gorgojo 1.2, 24-sep-26) ────────────────────────────────────────────
# Reproducido antes de arreglarlo: «¿debo duplicar la dosis?», «¿debo suspender el tratamiento?»,
# «¿qué lesión conviene biopsiar?» y «¿es seguro continuar la medicación?» NO disparaban: el panel
# exigía la palabra «riesgo» (o sinónimos), y su ausencia no prueba que la acción sea inocua.
# Hay acciones clínicas que son de alto riesgo POR SÍ MISMAS: tocar la dosis, parar o seguir un
# tratamiento, cambiar de línea, empezar uno, y abrir el cuerpo (biopsia, cirugía, ablación,
# radioterapia). Esas disparan el panel cuando se están DECIDIENDO (marco deliberativo); si solo se
# agendan, se archivan o se buscan papers sobre ellas, no. Se buscan sobre texto canónico (sin
# tildes, minúsculas, k→c para «kambiar»/«kimio»), con tallos de verbo que aguantan conjugaciones.
_TTO = (r"tratamiento|terapia|medicacion|farmaco|quimio\w*|hormonoterapia|letrozol|pastillas?|"
        r"pauta")
_ACCION = tuple(re.compile(p) for p in (
    r"(?:duplic|dobl|subi|suba|baj|aument|reduc|escal|desescal|ajust|cambi|modific)\w*"
    r"(?:\s+\w+){0,2}\s+(?:la\s+|de\s+)?do[sc]is",
    r"do[sc]is\s+(?:doble|mas alta|mas baja|de carga|maxima)",
    # «para» a secas es preposición («opciones para el tratamiento»): solo las formas de verbo.
    r"(?:suspend\w*|interrump\w*|\bpar(?:ar|amos|o|e|emos|en|as|arlo|arla)\b|dej\w*|abandon\w*|"
    r"retir\w*|discontinu\w*|descans\w*)(?:\s+\w+){0,3}?\s+(?:" + _TTO + r")",
    # «seguimiento del tratamiento» no es seguir con él.
    r"(?:continu\w*|segui(?!miento)\w*|\bsigo\b|mant[ei]n\w*)(?:\s+\w+){0,3}?\s+(?:" + _TTO + r")",
    r"cambi\w*\s+de\s+linea", r"(?:siguiente|nueva|proxima|otra)\s+linea",
    r"(?:empez|inici|comenz|arranc|entrar)\w*(?:\s+\w+){0,3}?\s+(?:" + _TTO + r"|ensayo)",
    r"\bre-?biopsi\w*", r"\bbiopsi\w*", r"(?:que|cual)\s+lesion",
    r"\bopera(?:r|rme|rla)\b", r"\bcirugia\b", r"\breseca\w*|\breseccion\b", r"\bablacion\b",
    r"\bradiofrecuencia\b", r"\bradioterapia\b|\birradia\w*|\bsbrt\b|\bprrt\b",
))
# Marco DELIBERATIVO: se está decidiendo, no informando ni agendando. El FUERTE decide solo; el
# DÉBIL (una interrogación, «¿podemos…?») no, si la frase es de logística: «¿podemos mover la
# biopsia al jueves?» es agenda, no una decisión de alto riesgo.
_DELIBERA_FUERTE = re.compile(
    r"\bdebo\b|\bdeberi\w+|\bdebemos\b|\bme planteo\b|\bnos planteamos\b|\bconviene\b|"
    r"\bmerece la pena\b|\bvale la pena\b|\bhay que decidir\b|\btengo que decidir\b|\bdecidir si\b|"
    r"\bes seguro\b|\bo esperar\b|\bo seguir\b|\bque hago\b|\bque hacemos\b|\bpros y contras\b|"
    r"\bvalorar si\b|\bvaloramos\b|\bes razonable\b|\by si\b")
_DELIBERA_DEBIL = re.compile(r"[?¿]|\bpuedo\b|\bpodemos\b|\bmejor\b")
_LOGISTICA = re.compile(
    r"\bcita\b|\bhora\b|\bagend\w*|\bmover\b|\bmuev\w*|\breprogram\w*|\bfecha\b|\brecuerd\w*|"
    r"\brecordatorio\b|\barchiv\w*|\bresum\w*|\bbusca\w*|\bpapers?\b|\bcronologia\b|"
    r"\bformate\w*|\bborrador\b|\bcorreo\b|\bmanda\w*")


def _delibera(c):
    return bool(_DELIBERA_FUERTE.search(c) or (_DELIBERA_DEBIL.search(c) and not _LOGISTICA.search(c)))
# «¿lo hacemos?» se refiere a algo dicho antes: solo cuenta si el turno anterior trae la acción.
_ANAFORA = re.compile(r"\blo hacemos\b|\blo hago\b|\bla hacemos\b|\badelante\b|\bentonces\b|"
                      r"\beso\b|\besto\b|\blo que (?:dijimos|hablamos|propone)\b|\bseguimos\b")


def _norm(s):
    return (s or "").lower()


def _canon_txt(s):
    """Minúsculas, sin tildes y k→c: lo que aguanta erratas sin inventar palabras."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().replace("k", "c")


def _acciones(t):
    return [p.pattern[:40] for p in _ACCION if p.search(t)]


def disparar(texto, clinica=None, riesgo=None, forzar=False, contexto=""):
    """Devuelve (aplica:bool, motivo:str, ejes:dict).

    `clinica`/`riesgo` (bool|None): override explícito del que invoca (un agente que YA sabe
    que es clínico lo marca; None = autodetecta por léxico). `forzar`=True dispara siempre
    (p. ej. el orquestador ante una duda real). El léxico es una RED, no la verdad: cualquier
    agente puede forzar el protocolo, nunca puede desactivarlo en silencio para algo clínico.
    `contexto`: el turno anterior, para las referencias del tipo «¿lo hacemos?».
    Una ACCIÓN clínica en marco deliberativo dispara aunque se pase `riesgo=False`.
    """
    t = _norm(texto)
    hit_clin = sorted({k for k in _CLINICO if k in t})
    hit_riesgo = sorted({k for k in _RIESGO if k in t})
    es_clinica = bool(hit_clin) if clinica is None else bool(clinica)
    es_riesgo = bool(hit_riesgo) if riesgo is None else bool(riesgo)
    c = _canon_txt(texto)
    delibera = _delibera(c)
    hit_accion = _acciones(c)
    accion_previa = []
    if delibera and not hit_accion and contexto and _ANAFORA.search(c):
        accion_previa = _acciones(_canon_txt(contexto))
    solo_info = (any(k in t for k in _NO_DECISION) and not hit_riesgo and clinica is None
                 and riesgo is None and not (delibera and hit_accion))
    ejes = {"clinica": es_clinica, "riesgo": es_riesgo,
            "lex_clinica": hit_clin, "lex_riesgo": hit_riesgo,
            "accion": bool(hit_accion or accion_previa), "delibera": delibera}
    if forzar:
        return True, "forzado por el agente (duda razonable → protocolo)", ejes
    if delibera and hit_accion:
        return True, ("se está DECIDIENDO una acción clínica de alto riesgo por sí misma (dosis, "
                      "suspensión, cambio de línea, procedimiento) → panel de élite obligatorio"), ejes
    if accion_previa:
        return True, ("la pregunta remite a una acción clínica del turno anterior → panel de élite "
                      "obligatorio"), ejes
    if solo_info:
        return False, "parece organización/info, no una decisión clínica", ejes
    if es_clinica and es_riesgo:
        return True, "decisión CLÍNICA + ALTO RIESGO/irreversible → panel de élite obligatorio", ejes
    if es_clinica and not es_riesgo:
        # Clínica pero sin señal de riesgo: NO se descarta sin más; se avisa para que un
        # humano (o el agente con contexto) confirme tier. Default-careful en lo clínico.
        return False, ("clínica pero sin señal explícita de alto riesgo → si es irreversible, "
                       "fuerza el protocolo (--forzar)"), ejes
    return False, "no es una decisión clínica del caso", ejes


# ── EXISTENCIA DETERMINISTA DE LA CITA (el cable del principio #0) ───────────
# El gate NO se fía del booleano `verificado` que un agente se auto-declara: la
# EXISTENCIA de toda cita externa (DOI/PMID/NCT/arXiv) la confirma el REGISTRO,
# vía `verifica_citas` (sin LLM). Cita inexistente = FABRICADA = se bloquea; si no
# se puede confirmar (red/registro mudo) = fail-closed. Las refs internas (sin ID
# externo) no se existence-checkean aquí: las cubren el humano y otras vías.
_EXT_RE = re.compile(
    r'10\.\d{4,9}/\S+'                          # DOI
    r'|NCT\d{8}'                                # ensayo
    r'|PMID\s*[:#]?\s*\d{1,8}'                  # PubMed
    r'|arXiv\s*:?\s*\d{4}\.\d{4,5}(?:v\d+)?',   # arXiv
    re.I)

# Inyectable en tests (monkeypatch del comprobador). None → `verifica_citas` real;
# si no se puede importar, se degrada FAIL-CLOSED (nunca fail-open).
_COMPROBADOR = None

# Soporte cita→afirmación (24-sep-26): que la cita EXISTA no basta; sus cifras tienen que estar en
# el abstract (`tools/soporte_cita.py`). Inyectable en tests: `_SOPORTE(afirmacion, cita) -> dict`.
# Si no se puede importar o el registro no responde, se comporta como antes (solo existencia): el
# hallazgo que bloquea es NO_RESPALDA, que exige haber leído el abstract y no encontrar la cifra.
_SOPORTE = None
# El comprobador señala la frase literal de la fuente externa (`soporte_cita.cotejar_fragmento`).
# Inyectable: `_FRAGMENTO(afirmacion, cita, fragmento) -> dict`.
_FRAGMENTO = None


def _resolver_soporte():
    if _SOPORTE is not None:
        return _SOPORTE
    try:
        from soporte_cita import soporte, JUEZ_ACTIVO
        # Fase 2: el juez semántico local solo entra cuando pasó su benchmark (JUEZ_ACTIVO).
        return lambda afirmacion, cita: soporte(afirmacion, cita, con_juez=JUEZ_ACTIVO)
    except Exception:
        return None


def _resolver_comprobador(comprobador):
    if comprobador is not None:
        return comprobador
    if _COMPROBADOR is not None:
        return _COMPROBADOR
    try:
        from verifica_citas import verifica
        return verifica
    except Exception:
        return None


def _existencia_citas(fuente, comp):
    """(estado, detalle). estado ∈ {confirmada, FABRICADA, no_resoluble, n/a}.

    El estado NO es opinión de un modelo: sale del registro público (Crossref/PubMed/
    ClinicalTrials/arXiv) vía `verifica_citas`. n/a = la fuente no trae un ID externo.
    """
    tokens = _EXT_RE.findall(fuente or "")
    if not tokens:
        return "n/a", "sin ID externo (ref interna; la cubren el humano y otras vías)"
    if comp is None:
        return "no_resoluble", "verificador de existencia no disponible → fail-closed"
    try:
        res = comp(tokens) or []
    except Exception as e:
        return "no_resoluble", "fallo verificando existencia (%s) → fail-closed" % (str(e)[:60])
    malas = [r.get("id") for r in res if r.get("estado") == "no_existe"]
    if malas:
        return "FABRICADA", "cita(s) inexistente(s) en el registro: %s" % ", ".join(str(m) for m in malas)
    # confirmada SOLO si TODO es 'existe' y CADA token tiene su resultado (allow-list,
    # anti fail-open: un estado inesperado o ids que no cuadran → no_resoluble, bloquea).
    entradas_ok = {r.get("entrada") for r in res if r.get("estado") == "existe"}
    if (len(res) == len(tokens)
            and all(r.get("estado") == "existe" for r in res)
            and set(tokens) <= entradas_ok):
        return "confirmada", "%d cita(s) externa(s) confirmada(s) por el registro" % len(tokens)
    return "no_resoluble", "el registro no confirmó (estado inesperado o ids que no cuadran) → fail-closed"


# ── EL CABLE DEL `verificado`: un VEREDICTO de comprobación REAL, no la palabra del modelo ───
# El error de CLASE del 27/6/26 (afirmar por ausencia sin abrir la fuente) enseña que un booleano
# auto-declarado por el agente NO para nada: el modelo puede poner `verificado: true` sin haber
# cotejado. Por eso `verificado` SOLO es True si la lente trae un bloque `comprobacion` con un
# VEREDICTO real de un comprobador DISTINTO del que afirma:
#   "comprobacion": {"por": "verificacion", "resultado": "confirmado|refutado|no_concluyente",
#                    "contra_fuente": "_PRIVADO_CLINICO/... | PMID/NCT/...", "nota": "opcional"}
# Reglas (fail-closed): sin `comprobacion` → NO verificado (da igual `verificado: true`); el
# comprobador no puede ser el propio agente (auto-firma = sello de goma); resultado != confirmado
# → no verificado; sin `contra_fuente` → no verificado (no hubo cotejo real contra nada).
_COMP_CAMPOS = {"por", "resultado", "contra_fuente", "fragmento", "nota"}
_COMP_RESULTADO = {"confirmado", "refutado", "no_concluyente"}

# Allowlist CERRADA de comprobadores válidos: el comprobador es un ROL del gabinete cuyo oficio es
# contrastar, no «cualquier nombre ≠ del agente» (eso permitía inventar un comprobador o acortar el
# propio nombre para evadir el chequeo de auto-firma — red-team 27/6/26).
_COMPROBADORES_VALIDOS = {"verificacion", "verificación", "comite-medico", "comité-médico",
                          "herramientas-medicas", "herramientas-médicas", "consejero-arquitectura"}
# Una `contra_fuente` REAL: una cita externa (DOI/PMID/NCT/arXiv) o un puntero a la bóveda clínica.
# Reusa el patrón de cita externa del propio módulo (_EXT_RE) + el segmento de bóveda.
_CONTRA_CLINICA_RE = re.compile(r"(^|[\\/])_PRIVADO_CLINICO([\\/])")


def _canon(nombre):
    """Canonicaliza un nombre de rol/agente para comparar (minúsculas, sin tildes ni separadores)."""
    s = (nombre or "").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(a, b)
    return re.sub(r"[\s_\-]+", "", s)


def _agente_base(nombre):
    """«verificacion (abogado del diablo)» → «verificacion»: el agente, sin el papel que hace."""
    return _canon(re.split(r"[(\[]", nombre or "", maxsplit=1)[0])


def roster():
    """Agentes REALES del gabinete (`.claude/agents/*.md`), canonicalizados. Un nombre inventado
    no es una voz: dos «lentes» con nombres distintos no hacen un panel si no hay dos agentes."""
    try:
        return {_canon(f[:-3]) for f in os.listdir(AGENTES_DIR) if f.endswith(".md")}
    except OSError:
        return set()


def _resolver_verificado(v, comprobador=None):
    """(verificado:bool, motivo:str). verificado=True SOLO si hay un veredicto de comprobación REAL
    de un comprobador VÁLIDO y DISTINTO del agente, con resultado confirmado contra una fuente real.
    Determinista; ignora cualquier `verificado` auto-declarado.

    «Fuente real» se COMPRUEBA, no se lee en la forma (auditoría Gorgojo 1.1, 24-sep-26): un
    puntero a la bóveda tiene que existir y contener el `fragmento` que el comprobador dice haber
    visto (`fuente_clinica.cotejar`); una cita externa tiene que existir en su registro. Deja el
    resultado del cotejo en `v["cotejo"]` (hashes y estado, nunca el fragmento)."""
    comp = v.get("comprobacion")
    if not isinstance(comp, dict):
        return False, ("sin bloque `comprobacion`: el `verificado` auto-declarado NO cuenta "
                       "(principio #0; el error del 27/6/26 — afirmar sin cotejar la fuente)")
    desconocidos = set(comp) - _COMP_CAMPOS
    if desconocidos:
        return False, "`comprobacion` con sub-campo(s) desconocido(s) %s → fail-closed" % sorted(desconocidos)
    por = (comp.get("por") or "").strip()
    agente = v.get("agente") or v.get("lente") or ""
    if not por:
        return False, "`comprobacion.por` vacío (no consta QUIÉN comprobó)"
    # Allowlist de comprobadores: no vale un nombre inventado.
    if _canon(por) not in {_canon(x) for x in _COMPROBADORES_VALIDOS}:
        return False, ("comprobador «%s» NO está en la allowlist de roles de verificación %s → no "
                       "cuenta (un nombre inventado no certifica)" % (por, sorted(_COMPROBADORES_VALIDOS)))
    # Auto-firma por nombre canónico: mata el alias («comite-medico» vs «comite», espacios, tildes).
    if _canon(por) == _canon(agente) or _agente_base(por) == _agente_base(agente):
        return False, ("auto-comprobación: «%s» se comprobó a sí mismo (comprobador == agente, por "
                       "nombre canónico — sello de goma)" % por)
    res = comp.get("resultado")
    if res not in _COMP_RESULTADO:
        return False, "`comprobacion.resultado` inválido: %r (válidos: %s)" % (res, sorted(_COMP_RESULTADO))
    if res != "confirmado":
        return False, "comprobación con resultado «%s» (solo «confirmado» cuenta como verificado)" % res
    contra = (comp.get("contra_fuente") or "").strip()
    if not contra:
        return False, "`comprobacion.contra_fuente` vacío (no hubo cotejo contra una fuente real)"
    # contra_fuente debe ser una fuente REAL: cita externa (PMID/NCT/DOI/arXiv) o puntero clínico.
    if not (_EXT_RE.search(contra) or _CONTRA_CLINICA_RE.search(contra)):
        return False, ("`comprobacion.contra_fuente` no parece una fuente real (ni cita externa "
                       "PMID/NCT/DOI ni puntero a _PRIVADO_CLINICO): «%s» → no cuenta como cotejo" % contra)
    # …y se COMPRUEBA. Puntero a la bóveda: que exista y que el fragmento citado esté dentro.
    if _CONTRA_CLINICA_RE.search(contra):
        frag = comp.get("fragmento")
        if not isinstance(frag, str) or not frag.strip():
            return False, ("puntero clínico sin `comprobacion.fragmento`: no consta QUÉ se vio en «%s». "
                           "Copia el texto literal del informe que sostiene la afirmación" % contra)
        r = fuente_clinica.cotejar(contra, frag, quien=por)
        v["cotejo"] = {k: r.get(k) for k in ("estado", "sha256", "sha256_fragmento", "identidad")}
        if not r["ok"]:
            return False, "cotejo contra la fuente fallido (%s): %s" % (r["estado"], r["motivo"])
        return True, "confirmado por «%s»: fragmento encontrado en %s (sha256 %s…)" % (
            por, contra, (r["sha256"] or "")[:12])
    # Cita externa: la existencia la da el registro, no la palabra del comprobador.
    est, det = _existencia_citas(contra, _resolver_comprobador(comprobador))
    v["cotejo"] = {"estado": est}
    if est != "confirmada":
        return False, "`contra_fuente` sin confirmar en su registro (%s): %s" % (est, det)
    # …y que DIGA lo que se le atribuye: las cifras del `porque` tienen que estar en su abstract.
    sop_fn = _resolver_soporte()
    afirma = v.get("porque") or ""
    if sop_fn is not None and afirma.strip():
        try:
            sop = sop_fn(afirma, contra) or {}
        except Exception as e:
            sop = {"estado": "PENDIENTE", "motivo": "fallo del cotejo de soporte (%s)" % str(e)[:60]}
        v["cotejo"]["soporte"] = sop.get("estado")
        if sop.get("estado") == "NO_RESPALDA":
            return False, ("%s existe, pero NO respalda la afirmación: %s (tools/soporte_cita.py)"
                           % (contra, sop.get("motivo", "")))
        if sop.get("estado") == "CONTRADICE":
            return False, ("%s existe y trae las cifras, pero el juez dice que NO dicen eso: %s "
                           "(tools/soporte_cita.py, fase 2)" % (contra, sop.get("motivo", "")))
        if sop.get("estado") == "PARCIAL":
            v["cotejo"]["aviso"] = "respaldo PARCIAL: " + (sop.get("motivo") or "")[:160]
    # …y el comprobador SEÑALA la frase de la fuente que trae esas cifras (24-sep-26). Sin frase no
    # hay cotejo de cohorte/endpoint/brazo que nadie pueda revisar: solo «existe y las cifras salen».
    try:
        import soporte_cita as _sc
        cifras = _sc.numeros(afirma, "es") if afirma.strip() else []
        frag_fn = _FRAGMENTO or _sc.cotejar_fragmento
    except Exception:
        cifras, frag_fn = [], None
    if cifras and frag_fn is not None:
        frag = comp.get("fragmento")
        if not isinstance(frag, str) or not frag.strip():
            return False, ("cita externa con cifras (%s) sin `comprobacion.fragmento`: copia la frase "
                           "LITERAL de %s que las trae, para que se vea a qué cohorte, endpoint y brazo "
                           "corresponden" % (", ".join(cifras), contra))
        try:
            rf = frag_fn(afirma, contra, frag) or {}
        except Exception as e:
            rf = {"estado": "PENDIENTE", "motivo": "fallo del cotejo del fragmento (%s)" % str(e)[:60]}
        v["cotejo"]["fragmento"] = rf.get("estado")
        if rf.get("estado") == "FRAGMENTO_OK":
            v["cotejo"]["fragmento_fuente"] = frag.strip()[:400]      # fuente PÚBLICA: va al acta
        elif rf.get("estado") != "PENDIENTE":
            return False, "fragmento de %s no válido (%s): %s" % (contra, rf.get("estado"), rf.get("motivo", ""))
    return True, "confirmado por «%s» contra %s (existe en su registro)" % (por, contra)


# ── 2+3. PANEL PARALELO + VERIFICACIÓN OBLIGATORIA ──────────────────────────
def evaluar(data, comprobador=None):
    """Toma los veredictos del panel y devuelve el acta estructurada + los bloqueos.

    Entrada (dict):
      {
        "decision": "¿biopsiar L1 o L3 para el pipeline de neoantígenos?",
        "contexto": "ventana única de biopsia; los cores deben recoger todo (opcional)",
        "veredictos": [
          {"lente": "comite-medico", "agente": "comite-medico",
           "postura": "a_favor|en_contra|matiz|abstiene", "confianza": "alta|media|baja",
           "porque": "…", "fuente": "PMID/NCT/ficha",
           "comprobacion": {"por": "verificacion", "resultado": "confirmado",
                            "contra_fuente": "_PRIVADO_CLINICO/... | PMID/NCT",
                            "fragmento": "texto LITERAL del informe (obligatorio si es bóveda)",
                            "nota": "opcional"},
           "discrepa_en": "opcional: con quién/en qué discrepa"},
          ...
        ]
      }
      NOTA: `verificado` ya NO es un campo de entrada que el agente se auto-declare. La
      verificación se DERIVA del bloque `comprobacion` (veredicto real de un comprobador
      DISTINTO); cualquier `verificado` que venga en el JSON se IGNORA (principio #0).

    Reglas fail-closed (un acta NO es entregable si):
      · menos de 2 lentes DISTINTAS (panel en serie disfrazado),
      · algún veredicto sin VERIFICACIÓN REAL (sin `comprobacion` confirmada por un 3º distinto),
      · hay discrepancia de postura SIN resolver (a {{TITULAR}} + médicas con el debate completo).
    Devuelve: (decision, contexto, veredictos, discrepancias, bloqueos, confianza_final,
               entregable:bool)
    """
    decision = data.get("decision") or "(sin enunciar la decisión)"
    contexto = data.get("contexto") or ""
    vers, bloqueos = [], []
    comp = _resolver_comprobador(comprobador)

    for v in data.get("veredictos", []) or []:
        v = dict(v)
        v["lente"] = v.get("lente") or v.get("agente") or "?"
        v["agente"] = v.get("agente") or v["lente"]
        if v.get("postura") not in POSTURAS:
            bloqueos.append("postura inválida en la lente «%s»: %r (válidas: %s)"
                            % (v["lente"], v.get("postura"), ", ".join(POSTURAS)))
            v["postura"] = "matiz"
        v["confianza"] = (v.get("confianza") or "media").lower()
        if v["confianza"] not in ("alta", "media", "baja"):
            v["confianza"] = "media"
        # `verificado` NO es la palabra del modelo: lo deriva un veredicto de comprobación REAL.
        v["verificado"], v["verificado_motivo"] = _resolver_verificado(v, comprobador)
        if not v.get("fuente"):
            bloqueos.append("la lente «%s» no cita FUENTE (verifica antes de fiarte)" % v["lente"])
        # Los punteros a la bóveda de la propia `fuente` tienen que EXISTIR (auditoría 1.1): una
        # lente no puede apoyarse en un informe que no está.
        for p in fuente_clinica.punteros(v.get("fuente")):
            r = fuente_clinica.cotejar(p, quien=v["agente"])
            if not r["ok"]:
                bloqueos.append("la fuente de «%s» no existe o no vale (%s): %s"
                                % (v["lente"], r["estado"], r["motivo"]))
        # EXISTENCIA determinista de la cita (no la palabra del modelo) — el cable #0.
        est, det = _existencia_citas(v.get("fuente"), comp)
        v["cita_existencia"], v["cita_detalle"] = est, det
        if est == "FABRICADA":
            bloqueos.append("la lente «%s» cita algo FABRICADO (%s). Cita inexistente = se "
                            "BLOQUEA: la desmiente el registro, no la palabra del modelo "
                            "(principio #0)." % (v["lente"], det))
        elif est == "no_resoluble":
            bloqueos.append("no se pudo verificar la EXISTENCIA de la cita de «%s» (%s) → "
                            "fail-closed: no entregable hasta confirmarla." % (v["lente"], det))
        vers.append(v)

    # a) panel paralelo real: ≥2 AGENTES distintos del gabinete, no ≥2 nombres de lente. Hasta el
    #    24-sep se contaban lentes: dos «lentes» del mismo agente eran un panel (auditoría 1.1).
    #    Límite honesto: `agente` lo escribe el mismo que rellena el JSON. Esto quita el fallo
    #    obvio y los nombres inventados; no ACREDITA que corrieran dos procesos distintos.
    reales = roster()
    agentes = {_agente_base(v["agente"]) for v in vers}
    validos = agentes & reales
    fuera = sorted(agentes - reales)
    if len(validos) < 2:
        bloqueos.append("panel INCOMPLETO: solo %d agente(s) real(es) distinto(s) (%s)%s. El "
                        "protocolo exige ≥2 veredictos INDEPENDIENTES de agentes distintos del "
                        "gabinete; dos lentes del mismo agente son una sola voz"
                        % (len(validos), ", ".join(sorted(validos)) or "ninguno",
                           ("; no son agentes del gabinete: " + ", ".join(fuera)) if fuera else ""))

    # a2) nadie es juez y parte: si A comprueba a B, B no puede comprobar a A (24-sep-26, decisión
    #    de {{TITULAR}}; hueco que vio consejero-arquitectura). `verificacion` puede ser gate Y abogado
    #    del diablo, pero su propio veredicto lo sella alguien a quien ella no ha sellado (por
    #    diseño, `herramientas-medicas`). El veredicto del círculo deja de contar como verificado.
    comprueba = {}                                       # agente → agentes cuyos veredictos sella
    for v in vers:
        por = _agente_base((v.get("comprobacion") or {}).get("por") if isinstance(
            v.get("comprobacion"), dict) else "")
        if por:
            comprueba.setdefault(por, set()).add(_agente_base(v["agente"]))
    for v in vers:
        c = v.get("comprobacion") if isinstance(v.get("comprobacion"), dict) else {}
        por, yo = _agente_base(c.get("por")), _agente_base(v["agente"])
        if por and v["verificado"] and por in comprueba.get(yo, set()):
            v["verificado"] = False
            v["verificado_motivo"] = ("comprobación mutua: «%s» comprueba a «%s» y «%s» comprueba a "
                                      "«%s» (juez y parte). El veredicto de «%s» lo tiene que sellar un "
                                      "agente al que no haya sellado" % (por, yo, yo, por, yo))

    # b) verificación obligatoria: TODOS con un VEREDICTO de comprobación REAL (no el booleano
    #    auto-declarado). El motivo concreto (por qué no cuenta) va en el bloqueo y en el acta.
    for v in vers:
        if not v["verificado"]:
            bloqueos.append("veredicto de «%s» SIN verificación REAL: %s → no es entregable hasta "
                            "que un comprobador DISTINTO contraste contra fuente (principio #0)"
                            % (v["lente"], v.get("verificado_motivo", "")))

    # c) discrepancias: posturas opuestas a_favor vs en_contra = discrepancia material
    posturas = {v["postura"] for v in vers if v["postura"] != "abstiene"}
    discrepancias = []
    hay_opuestos = "a_favor" in posturas and "en_contra" in posturas
    if hay_opuestos or ("matiz" in posturas and len(posturas) > 1):
        for v in vers:
            if v["postura"] != "abstiene":
                discrepancias.append("%s → %s%s"
                    % (v["lente"], POSTURA_TXT[v["postura"]],
                       (" · " + v["discrepa_en"]) if v.get("discrepa_en") else ""))
    if hay_opuestos:
        bloqueos.append("DISCREPANCIA ABIERTA entre lentes (a favor vs en contra): NO se "
                        "presenta como consenso. Va a {{TITULAR}} + sus médicas CON el debate, "
                        "marcada como decisión no resuelta por el panel")

    confianza_final = _confianza(vers, hay_opuestos)
    entregable = not bloqueos
    return decision, contexto, vers, discrepancias, bloqueos, confianza_final, entregable


def _confianza(vers, hay_opuestos):
    """Confianza AGREGADA, conservadora. No es una probabilidad: es una señal honesta."""
    if hay_opuestos:
        return "discrepancia abierta — la deciden {{TITULAR}} y sus médicas"
    activos = [v for v in vers if v["postura"] != "abstiene"]
    if not activos:
        return "sin postura — el panel se abstiene"
    orden = {"alta": 3, "media": 2, "baja": 1}
    minc = min(orden.get(v["confianza"], 2) for v in activos)  # la cadena vale lo que su eslabón flojo
    return {3: "alta", 2: "media", 1: "baja"}[minc]


# ── 4. ACTA AUDITABLE ────────────────────────────────────────────────────────
def render(decision, contexto, vers, discrepancias, bloqueos, confianza_final, entregable):
    out = ["# Acta de decisión de alto riesgo — panel de élite",
           "\n> " + BANNER,
           "\n## Decisión sometida al panel\n%s" % decision]
    if contexto:
        out.append("\n**Contexto:** %s" % contexto)

    out.append("\n## Panel paralelo — veredictos independientes")
    if not vers:
        out.append("_(ningún veredicto: el panel no llegó a constituirse)_")
    for v in vers:
        out.append("\n### Lente: %s  (`%s`)" % (v["lente"], v["agente"]))
        out.append("- Postura: %s" % POSTURA_TXT.get(v["postura"], v["postura"]))
        out.append("- Confianza: %s%s" % (v["confianza"],
                   "" if v["verificado"] else "  ⚠️ SIN verificar"))
        if v.get("porque"):
            out.append("- Razón: %s" % v["porque"])
        out.append("- Fuente: %s" % (v.get("fuente") or "⚠️ sin fuente"))
        out.append("- Verificado (comprobación REAL, no auto-declarada): %s — %s"
                   % ("sí" if v["verificado"] else "**NO**", v.get("verificado_motivo", "")))
        out.append("- Existencia de la cita (registro, determinista): %s — %s"
                   % (v.get("cita_existencia", "?"), v.get("cita_detalle", "")))
        # Huella del cotejo, NUNCA el fragmento: es dato clínico y el acta la leen sus médicas.
        cot = v.get("cotejo")
        if cot:
            out.append("- Cotejo contra la fuente (abierta por el panel): %s%s%s"
                       % (cot.get("estado"),
                          " · sha256 fuente %s…" % cot["sha256"][:12] if cot.get("sha256") else "",
                          " · huella del fragmento %s…" % cot["sha256_fragmento"][:12]
                          if cot.get("sha256_fragmento") else ""))
            if cot.get("fragmento_fuente"):
                # Solo con fuente PÚBLICA (cita externa): la frase se enseña para que el cotejo de
                # cohorte, endpoint y brazo lo pueda revisar quien lea el acta.
                out.append("- Frase de la fuente que lo sostiene (pública, cotejada): «%s»"
                           % cot["fragmento_fuente"])

    out.append("\n## Dónde discrepan (el debate, no solo la conclusión)")
    if discrepancias:
        for d in discrepancias:
            out.append("- %s" % d)
    else:
        out.append("_(las lentes coinciden; sin discrepancia material registrada)_")

    out.append("\n## Veredicto final del panel")
    out.append("- Confianza agregada: **%s**" % confianza_final)
    if entregable:
        out.append("- Estado: ✅ acta entregable a {{TITULAR}} (con sus médicas, para decidir)")
    else:
        out.append("- Estado: 🛑 **NO entregable como consenso** — ver bloqueos abajo")

    if bloqueos:
        out.append("\n## 🛑 Bloqueos (fail-closed: el protocolo avisa, no oculta)")
        for b in bloqueos:
            out.append("- %s" % b)

    out.append("\n---\n_%s_" % BANNER)
    return "\n".join(out)


def guardar_acta(texto, etiqueta="acta"):
    """Escribe el acta a un registro auditable y datado. Local, 0600. Devuelve la ruta."""
    os.makedirs(ACTAS, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in etiqueta)[:48] or "acta"
    ruta = os.path.join(ACTAS, "%s_%s.md" % (ts, safe))
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, texto.encode("utf-8"))
    finally:
        os.close(fd)
    return ruta


# ── CLI ──────────────────────────────────────────────────────────────────────
def _flag(a, name):
    return name in a


def _arg(a, name, default=""):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def _cmd_disparar(a):
    texto = _arg(a, "--texto") or (a[0] if a and not a[0].startswith("--") else "")
    clinica = True if _flag(a, "--clinica") else (False if _flag(a, "--no-clinica") else None)
    riesgo = True if _flag(a, "--riesgo") else (False if _flag(a, "--no-riesgo") else None)
    aplica, motivo, ejes = disparar(texto, clinica=clinica, riesgo=riesgo, forzar=_flag(a, "--forzar"),
                                    contexto=_arg(a, "--contexto"))
    if _flag(a, "--json"):
        print(json.dumps({"aplica": aplica, "motivo": motivo, "ejes": ejes}, ensure_ascii=False))
    else:
        print(("APLICA el protocolo de alto riesgo" if aplica else "no aplica") + " — " + motivo)
    return 0 if aplica else 3  # 3 = no aplica (distinto de 0/1 para scripting)


def _cmd_acta(a):
    raw_path = _arg(a, "--in") or (a[0] if a and not a[0].startswith("--") else "")
    raw = open(raw_path, encoding="utf-8").read() if raw_path else sys.stdin.read()
    data = json.loads(raw)
    res = evaluar(data)
    texto = render(*res)
    print(texto)
    if _flag(a, "--guardar"):
        ruta = guardar_acta(texto, etiqueta=_arg(a, "--etiqueta", "acta"))
        sys.stderr.write("\n[acta guardada en %s]\n" % ruta)
    entregable = res[-1]
    return 0 if entregable else 1


def main(argv):
    if not argv:
        print("uso: decision_alto_riesgo.py disparar \"<texto>\" [--clinica|--no-clinica] "
              "[--riesgo|--no-riesgo] [--forzar] [--contexto \"<turno anterior>\"] [--json]")
        print("     decision_alto_riesgo.py acta [--in fichero.json] [--guardar [--etiqueta X]]")
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "disparar":
        return _cmd_disparar(rest)
    if cmd == "acta":
        return _cmd_acta(rest)
    print("comando desconocido: %s" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
