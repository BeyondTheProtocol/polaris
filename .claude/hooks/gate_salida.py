#!/usr/bin/env python3
"""gate_salida.py — hook `Stop`: revisa MI respuesta antes de que {{TITULAR}} la lea.

POR QUÉ EXISTE (25-jul-2026). {{TITULAR}}: *«me dices vale, lo guardo, no volverá a pasar, pero en
realidad sí»*. El defecto era estructural, no de voluntad: sus normas viven como memorias que se
inyectan por relevancia (BM25 top-3/5) **cuando ella escribe**, mientras el incumplimiento ocurre
**cuando yo respondo**. Nadie miraba mi respuesta. Este hook es ese ojo.

No sustituye a las memorias: es la capa de ENFORCEMENT que la doc oficial de Claude Code pide
explícitamente («CLAUDE.md y la auto-memoria son *context, not enforced configuration*; si una regla
debe cumplirse siempre, hazla un hook»). Marco de fondo: `arXiv:2605.10481` — una restricción sirve
si se MANTIENE (fresca, heredada, aplicable, auditable), no si solo se afirma.

CÓMO SE COMPORTA (por diseño, para no volverse un estorbo):
  · **Modo** en `tools/normas.json` (`_modo_gate`): `aviso` avisa a {{TITULAR}} por `systemMessage` y deja
    pasar; `bloqueo` devuelve la respuesta a Claude con el motivo para que la corrija ANTES de
    entregarla. Se estrena en `aviso` (decisión de {{TITULAR}}, 25-jul: una semana midiendo falsos
    positivos y luego bloquear).
  · **Determinista y sin LLM** (coste 0, <1s). Caza CLASES de patrón, no toda falsedad.
  · **FAIL-OPEN**: cualquier excepción, timeout o duda → deja pasar. Un guardián de estilo no puede
    romper una conversación. (Lo contrario que el muro, que es fail-closed: ahí sí se bloquea.)
  · **Nunca frena una urgencia**: si la respuesta lleva 🔴 / CÓDIGO ROJO / HALT, pasa sin mirar.
  · **Sin bucles**: si `stop_hook_active` viene puesto (ya bloqueó una vez este turno), pasa.
  · **Bypass**: `BTP_GATE_OFF=1`.
  · **Auditable**: cada hallazgo se apunta en `tools/state/gate_salida.jsonl` para poder MEDIR los
    falsos positivos antes de pasar a bloquear (y para que {{TITULAR}} pueda auditarme). Por fila: `ts`,
    `session_hash` (nunca el id en claro), `check`/`slug`/`modo`/`motivo`, `bloqueo_real` (si ESE
    hallazgo bloqueó de verdad — distinto del modo global) y `extracto` (la frase citada en el
    motivo, de-identificada con `borde.de_identificar()` antes de escribirse). Se etiqueta con
    `tools/gate_etiqueta.py` (13-sep-26, Arreglo B) para poder promover check a check con datos.

Uso directo (para tests y para medir):
  echo '{"last_assistant_message":"..."}' | python3 .claude/hooks/gate_salida.py
  python3 .claude/hooks/gate_salida.py --check "texto"        # imprime hallazgos, exit 1 si hay
  python3 .claude/hooks/gate_salida.py --selftest
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tools"))
import _casa  # noqa: E402

MIN_CHARS = 80           # un «ok» o un «hecho» de una línea no se audita
MAX_HALLAZGOS = 3        # no abrumar: los 3 más graves
# El log vivo es ESTADO, no código: resuelve a CASA BASE (`_casa.state_dir()`), nunca al worktree
# donde corra esta sesión — mismo bug de clase que `feedback-estado-vivo-resuelve-casa-base` ya
# arregló en `deuda.py` (13-sep-26). `REPO`, en cambio, NO cambia: `normas.json` es código
# versionado y tiene que leerse del árbol en el que se está trabajando.
LOG = os.path.join(_casa.state_dir(), "gate_salida.jsonl")
URGENTE = ("🔴", "CÓDIGO ROJO", "CODIGO ROJO", ".HALT")
MAX_IDS_CITA = 6         # tope de citas a verificar por respuesta (coste y latencia)
TIMEOUT_CITAS = 20       # segundos para todo el lote; si no llega, fail-open
# Checks que bloquean AUNQUE el gate global esté en `aviso`. Solo entra aquí lo que, una vez
# leído, ya no se puede deshacer: una cita fabricada en contexto clínico se recuerda aunque
# después se desmienta. El resto de checks respetan el modo global.
SIEMPRE_BLOQUEA = {"citas_fabricadas"}

# ── utilidades ────────────────────────────────────────────────────────────────────────────────
def _sin_fences(t):
    """Fuera SOLO los bloques ```…```: dentro no aplican las normas de prosa."""
    return re.sub(r"```.*?```", " ", t, flags=re.S)


def _sin_bloques(t):
    """Fuera bloques y código en línea. OJO: no usar cuando el `código en línea` cuente como CITA
    (fue un bug real: se quitaban los backticks y luego se buscaba un backtick como fuente)."""
    return re.sub(r"`[^`]*`", " ", _sin_fences(t))


def _tiene_marca_de_apertura(t):
    """¿Hay señal de que las fuentes se ABRIERON (no solo citadas de oídas)?"""
    pats = (r"\babr[íi]\b", r"\babiert", r"\ble[íi]d[oa]", r"\bcotejad", r"\bverificad",
            r"HTTP 2\d\d", r"\bcontenido cotejado", r"\bfuente primaria", r"\bconsta en\b")
    return any(re.search(p, t, re.I) for p in pats)


def _sello(t):
    return re.search(r"\[(verificado|inferido|sin verificar|supuesto|fuente)[^\]]*\]|"
                     r"\bsello\b|\bverificad|\binferid|\bsin verificar\b|\bsupuesto\b", t, re.I)


# ── checks de TEXTO (cada uno = una norma del registro) ───────────────────────────────────────
def convergencia(t, tools=None):
    """feedback-convergencia-solo-si-abri-cada-fuente — el fallo del 25-jul."""
    pat = (r"(varias|m[úu]ltiples|dos|tres|cuatro)\s+fuentes|convergen(cia|te)|"
           r"coinciden\s+(varias|en|todas)|fuentes\s+independientes")
    if not re.search(pat, t, re.I):
        return None
    if _tiene_marca_de_apertura(t):
        return None
    # No cantar cuando estoy CORRIGIENDO o NEGANDO una convergencia (contarlo es lo correcto).
    for f in re.split(r"(?<=[.\n])\s+", _sin_bloques(t)):
        if re.search(pat, f, re.I) and not re.search(
                r"\bno\b|falso|era una broma|correcci[óo]n|resulta que|no cuenta|titular repetido|"
                r"inflad|hype", f, re.I):
            # La frase real va CITADA PRIMERO (13-sep-26): es lo que `_apunta()` guarda como
            # `extracto` para poder etiquetar de verdad — sin esto, las 132 filas de este check
            # en el log eran indistinguibles (mismo texto fijo siempre), y no había forma de medir
            # cuánto era charla de bajo riesgo frente a entregable con peso.
            return ("Dices que varias fuentes coinciden («%s…») y no hay ni una señal de que las "
                    "ABRIERAS. Si no las abriste todas, pon «titular repetido en N sitios, x "
                    "abiertas» y descuenta las que se citan entre sí." % f.strip()[:150])
    return None


def coste_no_bloquea(t, tools=None):
    """feedback-credito-no-bloquea-degrada + feedback-coste-nunca-corta-ned-pide-aprobacion."""
    m = re.search(r"no\s+(puedo|se puede|lo hago|lo he hecho)[^.\n]{0,60}"
                  r"(saldo|cr[ée]dito|tope|presupuesto|l[íi]mite de gasto)", t, re.I)
    if not m:
        m = re.search(r"(sin saldo|sin cr[ée]dito|se agot[óo] el (saldo|cr[ée]dito))"
                      r"[^.\n]{0,40}(no|imposible)", t, re.I)
    if not m:
        return None
    if re.search(r"(carril gratis|degrad|bajo de marcha|alternativa|pido tu aprobaci[óo]n|"
                 r"te lo pido|NVIDIA|gratis)", t, re.I):
        return None
    return ("Estás poniendo el saldo/coste como muro. La norma: se baja de marcha (carril gratis) "
            "o se pide aprobación; el coste NUNCA corta el camino a NED.")


def no_puedo_falso(t, tools=None):
    """feedback-si-puedo-adjuntar-correos — «no puedo» sobre cosas que sí puedo."""
    m = re.search(r"no\s+puedo\s+\w{0,12}\s*(adjuntar|enviar|mandar|borrar|responder)"
                  r"[^.\n]{0,50}(correo|email|e-mail|mail|gmail)", t, re.I)
    return ("Dices que no puedes hacer algo con el correo que SÍ puedes (adjuntar, enviar, borrar). "
            "Si es un gate de OK, dilo así; no como incapacidad.") if m else None


def falsa_certeza(t, tools=None):
    """feedback-honestidad-limites-avisar-no-inventar — la clase peligrosa: afirmar/negar sin sello."""
    # `_sin_fences` y no `_sin_bloques`: el `código en línea` cuenta como CITA.
    frases = re.split(r"(?<=[.\n])\s+", _sin_fences(t))
    for f in frases:
        if len(f) < 25:
            continue
        # Hipótesis, preguntas y condicionales NO son afirmaciones: «si se colaran, X no existe».
        if re.match(r"\s*(si|¿|cuando|imagina|supongamos|y si)\b", f, re.I) or f.rstrip().endswith("?"):
            continue
        if re.search(r"\b(si |ser[íi]a|habr[íi]a|podr[íi]a|puede que|quiz[áa]|tal vez)\b", f, re.I):
            continue
        fuerte = re.search(r"\b(es un hecho|est[áa] (confirmad|verificad)|"
                           r"no existe|no hay ninguna?|es imposible|nunca (ha|se) )", f, re.I)
        if not fuerte:
            continue
        if _sello(f) or re.search(r"https?://|\bNCT\d|\bPMID|10\.\d{4}/|`[^`]+`", f):
            continue
        return ("Afirmación fuerte sin sello ni fuente: «%s…». Va con [verificado]/[inferido]/"
                "[sin verificar] o con la fuente al lado." % f.strip()[:90])
    return None


def tells_ia(t, tools=None):
    """feedback-no-em-dash-tell-ia — que no suene a IA."""
    limpio = _sin_bloques(t)
    n = limpio.count("—")
    por_mil = (n * 1000.0 / max(len(limpio), 1))
    if n >= 3 and por_mil >= 2.0:
        # Un ejemplo REAL citado (13-sep-26), no solo el conteo: sin esto el `extracto` del log
        # era el mismo texto fijo en las 68 filas de este check, indistinguibles para etiquetar.
        i = limpio.find("—")
        ej = limpio[max(0, i - 25):i + 30].strip()
        return ("%d guiones largos (—) en la respuesta, p.ej. «%s»: en español es un *tell* de IA "
                "cuando se usa de muletilla. Reescribe con puntos, comas o paréntesis." % (n, ej))
    m = re.search(r"\bno es\b[^.\n]{3,40}\bes\b", limpio, re.I)
    if m and len(re.findall(r"\bno es\b[^.\n]{3,40}\bes\b", limpio, re.I)) >= 3:
        return ("Antítesis «%s» tres veces o más: suena a eslogan. Rompe el ritmo."
                % m.group(0).strip())
    return None


def secuencia_sin_tabla(t, tools=None):
    """feedback-formato-tabla-pasos-itinerarios — las secuencias van en tabla."""
    if "|" in t and re.search(r"\|[^\n]*\|", t):
        return None
    horas = len(set(re.findall(r"\b([01]?\d|2[0-3]):[0-5]\d\b(?!\s*(?:h?\s*)?→?\s*\d{2}:\d{2}\s*\))",
                               t)))
    # Solo es una SECUENCIA si además hay contexto de itinerario/agenda; «tardó de 18:00 a 18:13»
    # no es un plan de viaje.
    if not re.search(r"(itinerario|agenda|plan del d[íi]a|tren|vuelo|salida|llegada|cita|"
                     r"recogida|check-?in|hotel|traslado)", t, re.I):
        return None
    pasos = len(re.findall(r"^\s*(paso\s*\d|[1-9]\)|\d\.\s)", t, re.M | re.I))
    if horas >= 3 or (pasos >= 4 and horas >= 1):
        return ("Hay una secuencia con horas/pasos en prosa. La norma es TABLA por defecto: "
                "ancla emoji + salida→llegada en negrita + paso corto.")
    return None


def disclaimers(t, tools=None):
    """feedback-no-repetir-disclaimers-muro — ya se sabe el muro, no se lo repitas."""
    for f in re.split(r"(?<=[.\n])\s+", _sin_bloques(t)):
        if not re.search(r"(no es consejo m[ée]dico|apoyo a la decisi[óo]n,? no consejo|"
                         r"deciden (sus|tus) m[ée]dicos|ninguna IA dise[ñn]a la vacuna)", f, re.I):
            continue
        # Legítimo cuando CALIFICA algo ajeno («el informe de un tercero no es consejo médico»)
        # o cuando el entregable sale de casa. Solo canta si me lo estoy recitando a ella.
        if re.search(r"(tercero|ajen[oa]|LLM|desconocid|informe de|salida de|borrador|"
                     r"para (tus|sus) m[ée]dic|entregable)", f, re.I):
            continue
        if re.search(r"(te recuerdo|recuerda que|como siempre|como sabes|insisto)", f, re.I) or \
                len(f.strip()) < 140:
            return ("Le estás recitando un disclaimer del muro que ya se sabe: «%s». Quítalo salvo "
                    "que califique algo ajeno o salga de casa." % f.strip()[:80])
    return None


def taller_descartado(t, tools=None):
    """feedback-mejorar-taller-en-paralelo-no-descartar."""
    m = re.search(r"(no (es )?urgente|no toca (ahora )?(la )?biopsia|no es prioritario|"
                  r"puede esperar)[^.\n]{0,60}(polaris|sistema|taller|mejora|herramienta)", t, re.I)
    if not m:
        m = re.search(r"(polaris|el taller|el sistema)[^.\n]{0,40}"
                      r"(no (es )?urgente|no es prioritario|puede esperar)", t, re.I)
    return ("Estás descartando una mejora del taller por «no urgente». El taller va EN PARALELO a "
            "lo clínico: priorizar no es descartar.") if m else None


def verborrea(t, tools=None):
    """feedback-ahorrar-tokens-no-sobreexplicar — umbral generoso a propósito."""
    if len(t) < 9000:
        return None
    if "|" in t or "```" in t:
        return None
    return ("Muro de texto de %d caracteres sin tabla ni código. Corta: terso por defecto."
            % len(t))


def clinico_asumido(t, tools=None):
    """feedback-no-asumir-medicacion-clinica — no dar por hecho lo clínico."""
    m = re.search(r"\b(lleva|toma|est[áa] (con|en)|le (dan|administran))\b[^.\n]{0,40}"
                  r"(opi[áa]ceos?|fentanilo|morfina|quimio|radioterapia|corticoides|"
                  r"\d+\s?mg)", t, re.I)
    if not m or _sello(m.group(0)):
        return None
    return ("Estás afirmando un detalle clínico/medicación como hecho: «%s…». Va como supuesto y "
            "se coteja con la fuente." % m.group(0).strip()[:70])


# ── checks que miran las HERRAMIENTAS usadas en el turno ──────────────────────────────────────
def whatsapp_sin_leer(t, tools=None):
    """feedback-wp-leer-ultimo-mensaje-antes-de-redactar."""
    if tools is None:
        return None
    if not re.search(r"whatsapp|\bWA\b", t, re.I):
        return None
    if not re.search(r"borrador|te propongo|le escribo|mensaje para", t, re.I):
        return None
    if any(re.search(r"wa_tracker|_PRIVADO_WHATSAPP|wa_tareas", x, re.I) for x in tools):
        return None
    return ("Redactas un WhatsApp sin haber leído lo ÚLTIMO de ese chat en este turno "
            "(`wa_tracker`). Puede que ya le hayas dicho eso, o que no sea inicio de conversación.")


def pendientes_sin_verificar(t, tools=None):
    """feedback-comprobar-si-lo-hizo-solo — nunca listar pendientes de memoria."""
    if tools is None:
        return None
    if not re.search(r"(lo que (te )?queda|qué espera tu OK|lo tuyo ahora|sigue pendiente|"
                     r"te queda por)", t, re.I):
        return None
    if any(re.search(r"seguimiento|Read|Grep|Glob|git |ls |cat |python3", x, re.I) for x in tools):
        return None
    return ("Listas pendientes sin haber comprobado el estado real en este turno. "
            "Comprobar > suponer: mira `seguimiento.py`, los ficheros o lo enviado.")


def no_se_sin_mirar(t, tools=None):
    """feedback-cuando-no-sepas-mira-mails."""
    if tools is None:
        return None
    if not re.search(r"(no s[ée]\b|no tengo ese dato|no me consta|no lo encuentro)", t, re.I):
        return None
    if any(re.search(r"correo|imap|_PRIVADO_CORREO|gmail|mail", x, re.I) for x in tools):
        return None
    return ("Dices que no sabes algo sin haber mirado los correos en este turno. La norma: "
            "cuando no sepas, mira los mails (grep en `_PRIVADO_CORREO` o IMAP).")


def _ids_cita(t):
    """IDs de literatura que aparecen en MI respuesta. Solo formatos verificables sin LLM."""
    ids = []
    ids += ["PMID:" + m for m in re.findall(r"\bPMID:?\s*(\d{6,9})\b", t, re.I)]
    ids += re.findall(r"\b(10\.\d{4,9}/[^\s\"'<>,;)\]}]+)", t)
    ids += re.findall(r"\b(NCT\d{8})\b", t, re.I)
    ids += ["arXiv:" + m for m in re.findall(r"\barXiv:\s*(\d{4}\.\d{4,5})\b", t, re.I)]
    fuera = []
    for x in ids:                                  # sin duplicados, orden estable
        if x not in fuera:
            fuera.append(x)
    return fuera[:MAX_IDS_CITA]


def citas_fabricadas(t, tools=None):
    """Una cita que NO existe no puede llegar a {{TITULAR}}. Gate determinista, sin LLM.

    Se apoya en tools/verifica_citas.py (Crossref / NCBI / ClinicalTrials / arXiv). Solo corre
    si la respuesta trae algún id: sin ids no hay coste ni red. FAIL-OPEN en todo lo demás —
    red caída o API muda devuelven `no_resoluble` y NO se acusa, porque la cita puede existir.
    """
    ids = _ids_cita(t)
    if not ids:
        return None
    verificador = os.path.join(REPO, "tools", "verifica_citas.py")
    if not os.path.exists(verificador):
        return None
    try:
        r = subprocess.run([sys.executable, verificador, "--json"] + ids,
                           capture_output=True, text=True, timeout=TIMEOUT_CITAS)
        datos = json.loads(r.stdout or "[]")
    except Exception:
        return None                                # fail-open: nunca frenar por la red
    malas = [d["id"] for d in datos if d.get("estado") == "no_existe"]
    if not malas:
        return None
    return ("Cita(s) que NO existen en su registro público: %s. Verificado con "
            "`tools/verifica_citas.py` (Crossref/NCBI/ClinicalTrials/arXiv). Quita la "
            "referencia o sustitúyela por una real ANTES de entregar." % ", ".join(malas))


# Léxico del check `preclinico_aplanado`. Fuera de la función para poder testearlo suelto.
# Afirmación de eficacia CLÍNICA: la frase que convierte un resultado en una promesa.
_AFIRMA_EFICACIA = (
    "funciona", "es eficaz", "eficaz en", "es efectivo", "resulta efectivo",
    "reduce el tumor", "reduce la enfermedad", "frena el tumor", "detiene el tumor",
    "elimina el tumor", "revierte", "cura", "curan", "mejora la supervivencia",
    "alarga la supervivencia", "consigue respuesta", "logra respuesta", "responde al",
    "responden al", "sirve para tratar", "es una opción de tratamiento", "hay que pedirlo",
)
# Etiquetas que demuestran que la regla 17 YA se está cumpliendo en la propia frase.
_YA_ETIQUETADO = (
    "in vitro", "preclínic", "preclinic", "en ratones", "en ratón", "en raton",
    "modelo animal", "modelos animales", "línea celular", "linea celular", "líneas celulares",
    "lineas celulares", "en células", "en celulas", "xenoinjerto", "xenograft", "murino",
    "organoide", "aún no en personas", "aun no en personas", "no en personas",
    "no se ha probado en personas", "todavía no en humanos", "todavia no en humanos",
)


def preclinico_aplanado(t, tools=None):
    """Un resultado en ratones o en células no puede salir con cara de que «funciona».

    Es la regla 17 del protocolo mecanizada: hasta hoy (17-sep-26) vivía SOLO en el prompt,
    o sea que se cumplía cuando el modelo se acordaba. El daño de incumplirla es asimétrico
    y difícil de deshacer: una cita FABRICADA se desmiente enseñando el registro; una
    contacto construida sobre un xenoinjerto ya se ha leído.

    Tres booleanos, tejidos por el CÓDIGO (mismo patrón que `tools/tier_evidencia.py`, que es
    quien resuelve el primero contra PubMed, sin LLM):
      A. ¿alguna cita de la respuesta es preclínica según el REGISTRO?
      B. ¿la respuesta afirma eficacia clínica?
      C. ¿la respuesta YA dice que es preclínico?
    Acusa solo con A y B y no C. Si la frase ya dice «en ratones», la norma se cumple y calla.

    FAIL-OPEN ante la red y ante `desconocido`, igual que `citas_fabricadas`.
    Porqué-no: bloquear ante `desconocido` frenaría cualquier paper recién indexado todavía
    sin MeSH — sería ruido constante y acabaría con el gate desactivado, que es peor que el
    hueco que tapa. El `desconocido` se ve en la herramienta, no frena la salida.
    """
    bajo = t.lower()
    if not any(k in bajo for k in _AFIRMA_EFICACIA):
        return None                                # B falso: no hay promesa que aplanar
    if any(k in bajo for k in _YA_ETIQUETADO):
        return None                                # C cierto: la regla 17 ya se cumple
    pmids = [x for x in _ids_cita(t) if x.upper().startswith("PMID")]
    if not pmids:
        return None                                # sin PMID no hay registro que consultar
    clasificador = os.path.join(REPO, "tools", "tier_evidencia.py")
    if not os.path.exists(clasificador):
        return None
    try:
        r = subprocess.run([sys.executable, clasificador, "--json"] + pmids,
                           capture_output=True, text=True, timeout=TIMEOUT_CITAS)
        datos = json.loads(r.stdout or "[]")
    except Exception:
        return None                                # fail-open: nunca frenar por la red
    malas = [(d.get("id"), d.get("etiqueta")) for d in datos if d.get("preclinico")]
    if not malas:
        return None
    detalle = ", ".join("%s (%s)" % (i, e) for i, e in malas)
    return ("Estás afirmando eficacia apoyándote en evidencia PRECLÍNICA sin decirlo: %s. "
            "Verificado contra PubMed con `tools/tier_evidencia.py` (PublicationType + MeSH, "
            "sin LLM). Regla 17: nombra el tier en la MISMA frase del hallazgo — «en ratones», "
            "«en líneas celulares» — o quita la afirmación de eficacia." % detalle)


def clinico_sin_comite(t, tools=None):
    """Ninguna cifra clinica sale de aqui sin que un comite la haya tocado (5-sep-26).

    El fallo: una sesion analizo el panel molecular del nodo NED entera a pelo y entrego cifras de
    ensayo, hazard ratios y dianas sin que `comite-medico` ni `verificacion` hubieran corrido. El
    watchdog, lanzado despues, encontro dos transcripciones falsas y una direccion invertida.
    Hermano de `enrutado_guard`: aquel avisa al ENTRAR, este al SALIR.
    """
    if tools is None:
        return None
    duro = re.search(r"\bHR\s*=\s*0?[.,]\d|\bmPFS\b|\bORR\b|\bmediana de (SG|SLP)\b|"
                     r"\d+[.,]?\d*\s*(meses|months) de (SLP|PFS)|\bMuts?/Mb\b|\bVAF\b|"
                     r"\bn[uú]mero de copias\b|\bamplificaci[oó]n\b|\bHRDsig\b", t)
    if not duro:
        return None
    if not re.search(r"\b(diana|tratamiento|terapia|f[áa]rmac|ensayo|inhibidor|pron[oó]stico|"
                     r"opci[oó]n|indicad|candidat)\w*", t, re.I):
        return None
    if any(re.search(r"comite-medico|verificacion", x, re.I) for x in tools):
        return None
    return ("Entregas cifras clinicas o moleculares que sostienen una lectura terapeutica sin que "
            "`comite-medico` ni `verificacion` hayan tocado esto en el turno. Lanzalos, o di en "
            "una linea por que no hace falta. Consulta: python3 tools/enruta_comite.py \"<tarea>\"")


SESION = None            # la pone main(): el plan de enrutado es por sesión
LOG_OMITIDOS = os.path.join(os.path.dirname(LOG), "enrutado_omitido.jsonl")


def enrutado_incumplido(t, tools=None):
    """La decisión de quién responde se EJECUTA, no se sugiere ({{TITULAR}}, 11-sep-26).

    *«esa decisión no es mía, se toma a través del análisis de quién debe responder mejor… y lo
    tiene que hacer Polaris sin que yo le diga nada»*. `enrutado_guard` decide al entrar y guarda
    el plan; aquí se mira si el turno lo ejecutó. Si falta un comité o el LLM ordenado, la
    respuesta vuelve. Válvula: «ENRUTADO-OMITIDO: <motivo>», que se apunta para auditarla.
    """
    if tools is None or not SESION:
        return None
    try:
        import decide_peticion as dp
    except Exception:
        return None
    plan = dp.cargar(SESION)
    if not plan or plan.get("nivel") == dp.DIRECTO:
        return None
    if dp.VALVULA in t:
        try:
            motivo = t.split(dp.VALVULA, 1)[1].strip().splitlines()[0][:200]
            with open(LOG_OMITIDOS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"sesion": SESION, "nivel": plan.get("nivel"),
                                    "comites": plan.get("comites"), "llms": plan.get("llms"),
                                    "motivo": motivo}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return None
    faltan = dp.falta_por_ejecutar(plan, tools)
    if not faltan:
        return None
    return ("El enrutado de esta petición (%s) exigía %s y el turno no lo muestra. Ejecútalo y "
            "sintetiza, o escribe «%s <motivo>» si de verdad no procede."
            % (plan.get("nivel"), ", ".join(faltan), dp.VALVULA))


# Checks que corren aunque la respuesta sea corta: «hecho» sin haber lanzado al comité es
# justo el incumplimiento, y el mínimo de caracteres lo dejaba pasar.
SIN_MINIMO = {"enrutado_incumplido"}


CHECKS = {
    "citas_fabricadas": citas_fabricadas,
    "preclinico_aplanado": preclinico_aplanado,
    "convergencia": convergencia,
    "coste_no_bloquea": coste_no_bloquea,
    "no_puedo_falso": no_puedo_falso,
    "falsa_certeza": falsa_certeza,
    "tells_ia": tells_ia,
    "secuencia_sin_tabla": secuencia_sin_tabla,
    "disclaimers": disclaimers,
    "taller_descartado": taller_descartado,
    "verborrea": verborrea,
    "clinico_asumido": clinico_asumido,
    "whatsapp_sin_leer": whatsapp_sin_leer,
    "pendientes_sin_verificar": pendientes_sin_verificar,
    "no_se_sin_mirar": no_se_sin_mirar,
    "clinico_sin_comite": clinico_sin_comite,
    "enrutado_incumplido": enrutado_incumplido,
}


def _reglas_activas():
    """Del registro: qué checks están declarados como mecanismo de una norma. Fail-open.

    Se DEDUPLICA por check (31-jul-26): dos normas suyas legítimamente distintas pueden compartir
    mecanismo — `feedback-credito-no-bloquea-degrada` y `feedback-coste-nunca-corta-ned-pide-
    aprobacion` apuntan las dos a `coste_no_bloquea`. Sin dedup, el check corría dos veces y el
    mismo hallazgo se apuntaba y se le mostraba por duplicado.

    Devuelve (activas, modo_global) donde cada activa es (check, slug, modo_propio|None).
    """
    try:
        import normas
        activas, vistos = [], set()
        for r in normas.reglas_salida():
            c = r["check"]
            if c not in CHECKS or c in vistos:
                continue
            vistos.add(c)
            activas.append((c, r["slug"], r.get("modo")))
        return activas, normas.modo()
    except Exception:
        # Sin registro legible no se pierde el freno duro: los de SIEMPRE_BLOQUEA siguen bloqueando.
        return [(c, "", "bloqueo" if c in SIEMPRE_BLOQUEA else None) for c in CHECKS], "aviso"


def revisar(texto, tools=None):
    """[(check, slug, motivo)] de lo que incumple. Nunca lanza."""
    if not texto:
        return []
    if any(u in texto for u in URGENTE):
        return []
    corta = len(texto) < MIN_CHARS
    activas, _ = _reglas_activas()
    out = []
    for check, slug, _modo in activas:
        if corta and check not in SIN_MINIMO:
            continue
        try:
            motivo = CHECKS[check](texto, tools)
        except Exception:
            motivo = None
        if motivo:
            out.append((check, slug, motivo))
    return out


def _bloquean(hallazgos, modo_global):
    """Checks de estos hallazgos que exigen bloquear: por modo propio de su norma, o por el global."""
    activas, _ = _reglas_activas()
    propio = {c: m for c, _s, m in activas}
    return [c for c, _s, _m in hallazgos
            if (propio.get(c) or modo_global) == "bloqueo" or c in SIEMPRE_BLOQUEA]


def _es_mensaje_de_titular(d):
    """¿Esta línea del transcript es un mensaje suyo? Los tool_result también llevan role=user, y
    tomarlos por mensaje suyo borraba del turno los Agent lanzados antes (11-sep-26)."""
    if d.get("type") != "user" or d.get("isMeta"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        texto = c
    elif isinstance(c, list):
        tipos = {x.get("type") for x in c if isinstance(x, dict)}
        if "tool_result" in tipos or not tipos & {"text", "image"}:
            return False
        texto = next((x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text"), "")
    else:
        return False
    return not texto.lstrip().startswith(("<task-notification", "[SYSTEM NOTIFICATION"))


def _tools_del_turno(transcript_path):
    """Nombres+entradas de las tools usadas después del último mensaje de {{TITULAR}}. None si no se
    puede saber (el transcript se escribe async y puede ir por detrás) → nunca se acusa a ciegas."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    try:
        lineas = open(transcript_path, encoding="utf-8", errors="replace").read().splitlines()
    except Exception:
        return None
    parsed = []
    for ln in lineas:
        try:
            parsed.append(json.loads(ln))
        except Exception:
            continue
    ini = 0
    for i, d in enumerate(parsed):
        if _es_mensaje_de_titular(d):
            ini = i
    usos = []
    for d in parsed[ini:]:
        c = (d.get("message") or {}).get("content")
        if d.get("type") != "assistant" or not isinstance(c, list):
            continue
        for x in c:
            if not isinstance(x, dict) or x.get("type") != "tool_use":
                continue
            usos.append(x.get("name") or "")
            entrada = x.get("input") or {}
            for campo in ("command", "file_path", "pattern", "subagent_type"):
                v = entrada.get(campo)
                if isinstance(v, str):
                    usos.append(v[:400])
    # [] = se pudo leer y no se usó nada (que es justo lo que hay que poder acusar);
    # None = no se sabe. Antes las dos cosas eran None, y «no ejecutó nada» pasaba siempre.
    return usos if parsed else None


def _extracto(motivo):
    """La frase citada entre «» dentro del motivo (falsa_certeza, disclaimers, clinico_asumido ya
    citan así el fragmento que disparó el check); si no hay cita, el motivo entero. Pasada por
    `borde.de_identificar()` antes de guardarse — el jsonl vive fuera de la ventanilla clínica, y
    el fragmento citado puede traer una cifra o un nombre en claro. Fail-open: sin `borde`, se
    guarda sin de-identificar antes que perder el hallazgo entero."""
    m = re.search(r"«([^»]{1,200})»", motivo or "")
    frag = (m.group(1) if m else (motivo or ""))[:200]
    try:
        import borde
        frag, _n = borde.de_identificar(frag)
    except Exception:
        pass
    return frag


def _apunta(hallazgos, modo):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        # `bloqueo_real` corrige el hallazgo colateral del 13-sep-26: `modo` en la fila es el
        # GLOBAL en el momento de escribir, no si ESTE hallazgo bloqueó de verdad (un check con
        # `modo` propio, como `falsa_certeza`, puede bloquear con el global en "aviso"). Se
        # calcula igual que `_bloquean()`.
        bloqueados = set(_bloquean(hallazgos, modo))
        sesion_hash = (hashlib.sha256(SESION.encode("utf-8")).hexdigest()[:16]
                      if SESION else None)
        with open(LOG, "a", encoding="utf-8") as f:
            for check, slug, motivo in hallazgos:
                f.write(json.dumps({
                    "ts": time.time(),
                    "session_hash": sesion_hash,
                    "check": check, "slug": slug, "modo": modo,
                    "bloqueo_real": check in bloqueados,
                    "motivo": motivo[:200],
                    "extracto": _extracto(motivo),
                }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    if os.environ.get("BTP_GATE_OFF") == "1":
        return 0
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        return _selftest()
    if argv and argv[0] == "--check":
        h = revisar(argv[1] if len(argv) > 1 else "")
        for check, slug, motivo in h:
            print("⚠️  [%s] %s" % (check, motivo))
        return 1 if h else 0

    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0                                  # fail-open
    if data.get("stop_hook_active"):
        return 0                                  # ya frenó una vez este turno: no hacemos bucle
    texto = data.get("last_assistant_message") or ""
    global SESION
    SESION = str(data.get("session_id") or data.get("transcript_path") or "") or None
    tools = _tools_del_turno(data.get("transcript_path"))
    hallazgos = revisar(texto, tools)
    if not hallazgos:
        return 0
    _, modo = _reglas_activas()
    hallazgos = hallazgos[:MAX_HALLAZGOS]
    _apunta(hallazgos, modo)
    cuerpo = "\n".join("· [%s] %s" % (c, m) for c, _s, m in hallazgos)
    if _bloquean(hallazgos, modo):
        sys.stderr.write("GATE DE SALIDA — corrige esto ANTES de entregar la respuesta:\n"
                         + cuerpo + "\n")
        return 2                                  # Stop: bloquea y me devuelve el motivo
    print(json.dumps({"systemMessage": "⚠️ Gate de salida (modo aviso) — %d norma(s):\n%s"
                                       % (len(hallazgos), cuerpo)}, ensure_ascii=False))
    return 0


def _selftest():
    casos = [
        ("convergencia", "Tres fuentes coinciden en que esto es el futuro del trabajo con agentes, "
                         "y por eso lo doy por bueno sin más comprobaciones por mi parte."),
        ("coste_no_bloquea", "No puedo hacer la investigación profunda porque se agotó el saldo de "
                             "la API, así que lo dejo aquí y no seguimos con ese hilo hoy."),
        ("no_puedo_falso", "No puedo adjuntar el PDF al correo, tendrás que hacerlo tú desde Gmail "
                           "cuando tengas un momento tranquilo esta tarde."),
        ("falsa_certeza", "Está confirmado que ese ensayo no acepta pacientes desde Europa, así que "
                          "lo descarto del mapa y no lo volvemos a mirar más adelante."),
        ("disclaimers", "Te recuerdo que esto no es consejo médico y que deciden tus médicos, como "
                        "siempre, antes de seguir con el resumen de lo que he encontrado hoy."),
        ("taller_descartado", "Lo de mejorar Polaris no es urgente ahora mismo, así que lo dejo "
                              "aparcado y seguimos con lo demás que teníamos entre manos."),
    ]
    fallos = []
    for check, texto in casos:
        h = [c for c, _s, _m in revisar(texto)]
        if check not in h:
            fallos.append("%s NO cazado (cazó: %s)" % (check, h or "nada"))
    limpio = ("He fusionado la rama y la suite está en verde: 211 comprobaciones del muro sin "
              "fallos. Te dejo el enlace del informe en la fuente de verdad para que lo mires "
              "cuando quieras, y el resto sigue como estaba esta mañana.")
    if revisar(limpio):
        fallos.append("FALSO POSITIVO en texto limpio: %s" % revisar(limpio))
    if fallos:
        print("❌ selftest del gate: %d fallo(s)" % len(fallos))
        for f in fallos:
            print("   ·", f)
        return 1
    print("✅ selftest del gate en verde (%d casos + 1 canario limpio)" % len(casos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
