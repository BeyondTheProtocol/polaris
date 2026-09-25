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
    EXCEPCIÓN (25-sep-26): los checks que consultan RED (citas, tier, cifra) no callan si la red
    falla. Devuelven un hallazgo PENDIENTE: se avisa («sin verificar»), se apunta y no bloquea.
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
TIMEOUT_CITAS = 20       # segundos para todo el lote; si no llega, PENDIENTE (aviso), nunca «verificado»
# Checks que bloquean AUNQUE el gate global esté en `aviso`. Solo entra aquí lo que, una vez
# leído, ya no se puede deshacer: una cita fabricada en contexto clínico se recuerda aunque
# después se desmienta. El resto de checks respetan el modo global.
SIEMPRE_BLOQUEA = {"citas_fabricadas"}
# Un hallazgo que empieza así es una verificación que NO se pudo hacer, no una violación: se avisa
# y nunca bloquea (ver `_bloquean`). Existe para que un fallo de red no se convierta en un
# «verificado» por defecto (auditoría Gorgojo 1.4;
# desde el 25-sep-26 lo usan los tres checks que consultan red).
PENDIENTE = "⏳ PENDIENTE de comprobar:"

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
    # 22-sep-26: se probó a NO aceptar «verificado» como sello en turnos sin herramientas y se
    # revirtió con datos (replay): 5 disparos nuevos de `falsa_certeza` (que BLOQUEA), casi todos
    # legítimos — lo verificado en un turno anterior de la misma sesión, o una captura que ella pegó.
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


# Clase 1 de la auditoría del 22-sep-26 (8+5 correcciones en 9 días): «no puedo» o devolverle a
# {{TITULAR}} un trabajo que el sistema SÍ puede hacer. El check original (solo «no puedo» + correo) no
# saltó NI UNA vez en 2.032 turnos reales. Una versión ancha («tienes que…», «hazlo tú», «no puedo
# ver…») dio 44 disparos con ~25 % de acierto: la mayoría eran credenciales, logins, revisiones que
# SÍ le tocan a ella o frases citadas. Los aciertos compartían forma: incapacidad o delegación SOBRE
# UN OBJETO PARA EL QUE HAY HERRAMIENTA (borrador, Drive, ZIP, PDF…). Eso es lo que se mira.
_DELEGA = re.compile(
    r"\bno\s+(puedo|podemos|s[ée] c[óo]mo)\s+(\w+\s+){0,2}?(borrar|eliminar|descargar|bajar|subir|"
    r"adjuntar|enviar|mandar|abrir|leer|descomprimir|convertir|renderizar|mover|copiar|guardar|"
    r"responder)\w*|"
    r"\b(tendr[áa]s|tienes)\s+que\s+(borrar|eliminar|descargar|bajar|subir|adjuntar|enviar|mandar|"
    r"abrir|descomprimir|convertir|mover|copiar|guardar|pegar)\w*|"
    r"\b(b[óo]rralo|desc[áa]rgalo|b[áa]jalo|s[úu]belo|adj[úu]ntalo|[áa]brelo|m[áa]ndalo|"
    r"p[ée]gamelo|c[óo]pialo|mu[ée]velo)\s+tú\b|"
    r"\b(borra|descarga|baja|sube|adjunta|abre|manda|pega|copia|mueve)\s+tú\b|"
    r"\bnecesito\s+que\s+(t[úu]\s+)?(me\s+)?(pegues|subas|descargues|bajes|adjuntes|copies|abras|"
    r"mandes|borres)\b", re.I)
# Objetos para los que Polaris tiene herramienta (Gmail, Drive, ficheros locales, navegador).
_OBJETO_CON_TOOL = re.compile(
    r"correo|e-?mail|gmail|borrador|adjunt|drive|carpeta|fichero|archivo|\bzip\b|\bpdf\b|dicom|"
    r"informe|documento|imagen|captura|foto|\bPET\b|\bTAC\b|resonancia|laminilla|excel|\.xlsx|"
    r"\.docx|csv|enlace|link|p[áa]gina", re.I)
# Lo que SÍ es de un humano: credenciales, login, pagos, firma, gates de OK, su criterio.
_DELEGA_LEGITIMO = re.compile(
    r"\bOK\b|\bgate\b|firma|confirm|contrase[ñn]a|password|passkey|passcode|credencial|captcha|"
    r"\b2FA\b|c[óo]digo|cl@ve|certificado digital|sudo|llavero|login|inici(a|ar|o)( de)? sesi[óo]n|"
    r"entrar con|pag(ar|o)\b|tarjeta|prohibid|irreversible|tu decisi[óo]n|tu criterio|tu voz|"
    r"tu m[ée]dic|tu onc[óo]log|auto mode|permiso|\b40[13]\b|ca[íi]d[oa]|no responde", re.I)


# Un límite REAL dicho con su prueba (cifra, código de error, rechazo) no es incapacidad fingida:
# bloquearlo empuja a borrar el límite, que es mentir por omisión (25-sep-26, revisión P2 del
# comité verificacion). Idea de {{CONTACTO}} (https://contacto), con su agente KAI,
# revisión del 25-sep-2026.
_LIMITE_REAL = re.compile(
    r"\b(limita|l[íi]mite)\b[^.]{0,30}\d|\b\d+\s*[MG]B\b|\berror\s+\d{3}\b|\brechaz", re.I)
# «No puedo X, así que lo paso por OCR»: el rodeo lo hago yo, no se lo paso a ella.
_RODEO_PROPIO = re.compile(
    r"\b(as[íi] que|pero|y)\s+(lo|la|los|las)\s+(subo|paso|convierto|proceso|hago|mando|leo|"
    r"descargo|abro|comprimo|parto)\b", re.I)


def no_puedo_falso(t, tools=None):
    """feedback-si-puedo-adjuntar-correos + feedback-modo-maxima-autonomia — «no puedo» o «hazlo
    tú» sobre algo para lo que hay herramienta (correo, Drive, ficheros, navegador)."""
    # Fuera lo citado entre «» o comillas: leer una frase ajena no es decirla.
    limpio = re.sub(r"«[^»]*»|\"[^\"]*\"|“[^”]*”", " ", _sin_bloques(t))
    for f in re.split(r"(?<=[.\n])\s+", limpio):
        if f.lstrip().startswith(">"):
            continue
        m = _DELEGA.search(f)
        if not m:
            continue
        if re.match(r"\s*(si|¿|cuando)\b", f, re.I) or f.rstrip().endswith("?"):
            continue
        # Hablar DEL error («para no volver a decir que no puedo…») no es cometerlo.
        if re.search(r"(decir|dije|dec[íi]a|diga|digo)\s+que\s*$", f[:m.start()], re.I):
            continue
        if not _OBJETO_CON_TOOL.search(f) or _DELEGA_LEGITIMO.search(f):
            continue
        pasa_a_ella = re.search(r"\bt[úu]\b|te toca|tendr[áa]s que|tienes que|para que (lo|la)", f, re.I)
        if not pasa_a_ella and (_LIMITE_REAL.search(f) or _RODEO_PROPIO.search(f[m.end():])):
            continue
        return ("«%s…»: dices que no puedes, o le pasas a {{TITULAR}}, algo para lo que hay herramienta "
                "(Gmail, Drive, ficheros, navegador). Hazlo. Si de verdad es un gate de su OK o una "
                "credencial suya, dilo así, no como incapacidad. Si es un límite REAL de la "
                "herramienta, cítalo con su cifra o su error: no lo borres." % f.strip()[:120])
    return None


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
                "cuando se usa de muletilla. Reescribe con puntos, contacto o paréntesis." % (n, ej))
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
    si la respuesta trae algún id: sin ids no hay coste ni red.

    Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
    Fallo de verificación ≠ vía libre (25-sep-26, su punto de rotura 07). Antes, red
    caída, timeout, verificador ausente o `no_resoluble` devolvían None: la cita salía como si se
    hubiera comprobado y no quedaba ni rastro en el log. Reproducido con la red cortada: un PMID
    inventado pasaba con cero hallazgos. Ahora eso devuelve un hallazgo PENDIENTE: se le enseña a
    {{TITULAR}} como «cita SIN VERIFICAR», se apunta en el log y NO bloquea (la cita puede existir; la
    culpa es de la red, no de la respuesta). Solo `no_existe` confirmado bloquea.
    """
    ids = _ids_cita(t)
    if not ids:
        return None
    verificador = os.path.join(REPO, "tools", "verifica_citas.py")
    if not os.path.exists(verificador):
        return _cita_sin_verificar(ids, "no encuentro `tools/verifica_citas.py`")
    try:
        r = subprocess.run([sys.executable, verificador, "--json"] + ids,
                           capture_output=True, text=True, timeout=TIMEOUT_CITAS)
        datos = json.loads(r.stdout or "null")
    except subprocess.TimeoutExpired:
        return _cita_sin_verificar(ids, "el registro no contestó en %d s" % TIMEOUT_CITAS)
    except Exception as e:
        return _cita_sin_verificar(ids, "falló la verificación (%s)" % type(e).__name__)
    if not isinstance(datos, list) or not datos:
        return _cita_sin_verificar(ids, "el verificador no devolvió resultados legibles")
    datos = [d for d in datos if isinstance(d, dict)]
    malas = [d.get("id") for d in datos if d.get("estado") == "no_existe"]
    # Todo lo que no sea `existe` ni `no_existe` (no_resoluble, no_parseable, estado raro) y los ids
    # que el verificador no devolvió cuentan como NO comprobados, nunca como buenos.
    dudosas = [d.get("entrada") or d.get("id") for d in datos
               if d.get("estado") not in ("existe", "no_existe")]
    if len(datos) < len(ids):
        vistos = {str(d.get("entrada")) for d in datos}
        dudosas += [x for x in ids if x not in vistos]
    if malas:
        extra = (" Además, sin verificar (red o registro mudo): %s." % ", ".join(dudosas)
                 if dudosas else "")
        return ("Cita(s) que NO existen en su registro público: %s. Verificado con "
                "`tools/verifica_citas.py` (Crossref/NCBI/ClinicalTrials/arXiv). Quita la "
                "referencia o sustitúyela por una real ANTES de entregar.%s"
                % (", ".join(malas), extra))
    if dudosas:
        return _cita_sin_verificar(dudosas, "el registro no respondió o no la resolvió")
    return None


def _cita_sin_verificar(ids, porque):
    """Hallazgo PENDIENTE de `citas_fabricadas`: avisa, se apunta, no bloquea."""
    return (PENDIENTE + " CITA SIN VERIFICAR: %s (%s). NO está comprobado que exista: trátala "
            "como «sin verificar» hasta cotejarla con `tools/verifica_citas.py` o abrirla."
            % (", ".join(ids), porque))


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

# Fin de frase que NO es fin de frase: «Smith et al. mostraron…», «p. ej. en ratones».
_ABREVIATURA = re.compile(r"(?:\bet al|\bp\. ej|\bvs|\betc|\bfig|\baprox|\bdra?|\bsra?|\b\w)\.$", re.I)


def _parrafos(t):
    """Sin bloques, sin código en línea y sin lo que va entre comillas: un `PMID: X` entre
    backticks o «el inhibidor funciona (PMID: X)» entre comillas es un EJEMPLO o una cita (hablar
    del propio gate), no una afirmación mía. Lo cazó el replay del 24-sep: dos respuestas que
    explicaban el check quedaban BLOQUEADAS por la frase de su propio test.
    Límite que se acepta: una promesa escrita entre comillas con su PMID dentro se escapa. Es más
    raro que hablar del gate, y este check bloquea: un freno que para de más acaba apagado."""
    t = re.sub(r"\"[^\"\n]{0,400}\"|«[^»\n]{0,400}»|“[^”\n]{0,400}”", " ", _sin_bloques(t))
    return [p for p in re.split(r"\n\s*\n", t) if p.strip()]


def _frases(parrafo):
    """Frases: fin de oración o salto de línea (una fila de tabla o un bullet es SU unidad, con su
    columna de tier), sin partir en una abreviatura."""
    out = []
    for trozo in re.split(r"(?<=[.!?])\s+|\s*\n\s*", parrafo):
        if out and _ABREVIATURA.search(out[-1].rstrip()):
            out[-1] += " " + trozo
        else:
            out.append(trozo)
    return [f for f in out if f.strip()]


def _etiquetada(frase):
    b = frase.lower()
    return any(k in b for k in _YA_ETIQUETADO)


def _pmids(t):
    return [x for x in _ids_cita(t) if x.upper().startswith("PMID")]


def preclinico_aplanado(t, tools=None):
    """Un resultado en ratones o en células no puede salir con cara de que «funciona».

    Es la regla 17 del protocolo mecanizada: hasta hoy (17-sep-26) vivía SOLO en el prompt,
    o sea que se cumplía cuando el modelo se acordaba. El daño de incumplirla es asimétrico
    y difícil de deshacer: una cita FABRICADA se desmiente enseñando el registro; una
    esperanza construida sobre un xenoinjerto ya se ha leído.

    Tres booleanos, tejidos por el CÓDIGO (mismo patrón que `tools/tier_evidencia.py`, que es
    quien resuelve el primero contra PubMed, sin LLM):
      A. ¿alguna cita de la respuesta es preclínica según el REGISTRO?
      B. ¿la respuesta afirma eficacia clínica?
      C. ¿la respuesta YA dice que es preclínico?
    Acusa solo con A y B y no C. Si la frase ya dice «en ratones», la norma se cumple y calla.

    POR UNIDAD (auditoría Gorgojo 1.4, 24-sep-26). Antes los tres booleanos se miraban sobre el
    texto ENTERO, y un «en ratones» sobre el fármaco B eximía una promesa de supervivencia sobre
    el A: lo reproduje, y el registro ni se consultaba. Ahora la ETIQUETA cubre solo SU frase, y
    la CITA de una afirmación es la de su frase o, si no trae, las de su párrafo que no estén ya
    dichas como preclínicas en otra frase. («A reduce el tumor. Fuente: PMID X.» es una unidad.)

    Fallo de verificación ≠ vía libre. Red caída, clasificador ausente o tier `desconocido` ya no
    devuelven None en silencio (eso era dar la promesa por comprobada): devuelven un hallazgo que
    empieza por PENDIENTE, que se AVISA y nunca bloquea (`_bloquean` lo excluye). Bloquear por la
    red rompería conversaciones por algo que no es culpa de la respuesta, y un paper recién
    indexado todavía no tiene MeSH; callar, en cambio, es sellar lo que nadie miró.
    """
    candidatos = []                                # [(frase, [PMID…])]
    for parrafo in _parrafos(t):
        frases = _frases(parrafo)
        pm_etiquetados = {x for f in frases if _etiquetada(f) for x in _pmids(f)}
        pm_parrafo = _pmids(parrafo)
        for f in frases:
            if not any(k in f.lower() for k in _AFIRMA_EFICACIA):
                continue                           # B falso en esta frase
            if _etiquetada(f):
                continue                           # C cierto EN ESTA frase: la regla se cumple
            usa = _pmids(f) or [x for x in pm_parrafo if x not in pm_etiquetados]
            if usa:
                candidatos.append((f.strip(), usa))
    if not candidatos:
        return None                                # sin afirmación citada no hay registro que mirar
    pmids = []
    for _f, usa in candidatos:
        pmids += [x for x in usa if x not in pmids]
    pmids = pmids[:MAX_IDS_CITA]
    clasificador = os.path.join(REPO, "tools", "tier_evidencia.py")
    if not os.path.exists(clasificador):
        return (PENDIENTE + " no encuentro `tools/tier_evidencia.py` para saber si %s es "
                "preclínico. Afirmas eficacia apoyándote en esa cita: nómbrale el tier en la misma "
                "frase o dilo como no comprobado." % ", ".join(pmids))
    try:
        r = subprocess.run([sys.executable, clasificador, "--json"] + pmids,
                           capture_output=True, text=True, timeout=TIMEOUT_CITAS)
        datos = json.loads(r.stdout or "[]")
    except Exception:
        datos = None
    if not isinstance(datos, list):
        return (PENDIENTE + " el registro (PubMed vía `tools/tier_evidencia.py`) no respondió, así "
                "que NO sé si %s es preclínico. Afirmas eficacia apoyándote en esa cita: nómbrale el "
                "tier en la misma frase o dilo como no comprobado." % ", ".join(pmids))
    por_id = {re.sub(r"\D", "", str(d.get("id"))): d for d in datos if isinstance(d, dict)}
    malas, dudosas = [], []
    for frase, usa in candidatos:
        for x in usa:
            d = por_id.get(re.sub(r"\D", "", x))
            if d is None or d.get("tier") == "desconocido":
                dudosas.append(x)
            elif d.get("preclinico"):
                malas.append((x, d.get("etiqueta"), frase))
    if malas:
        detalle = "; ".join("%s (%s) bajo «%s»" % (i, e, f[:90]) for i, e, f in malas)
        return ("Estás afirmando eficacia apoyándote en evidencia PRECLÍNICA sin decirlo: %s. "
                "Verificado contra PubMed con `tools/tier_evidencia.py` (PublicationType + MeSH, "
                "sin LLM). Regla 17: nombra el tier en la MISMA frase del hallazgo — «en ratones», "
                "«en líneas celulares» — o quita la afirmación de eficacia. Una etiqueta en otra "
                "frase no cubre esta." % detalle)
    if dudosas:
        return (PENDIENTE + " el registro no sabe el tier de %s (sin MeSH o no encontrado). "
                "Afirmas eficacia apoyándote en esa cita: si no sabes si es clínico, dilo en la "
                "frase." % ", ".join(dict.fromkeys(dudosas)))
    return None


def _ids_de(t):
    """IDs de literatura de un trozo, en el formato que entiende `tools/soporte_cita.py`."""
    return [x for x in _ids_cita(t) if not x.lower().startswith("arxiv")]


def cita_no_respalda(t, tools=None):
    """Una cita que EXISTE no basta: la cifra que le atribuyo tiene que estar en su abstract.

    Nace el 24-sep-26 (deuda `cita-afirmacion-sin-soporte`, comparación con CureWise): «41 % de
    respuesta (PMID X)» pasaba `citas_fabricadas` si X existía, aunque X dijera 14 %. Coteja cada
    FRASE con cifras contra el abstract de su cita (`tools/soporte_cita.py`, determinista, sin LLM;
    al registro solo sale el ID). La cita de una frase es la suya o, si no trae, la ÚNICA de su
    párrafo; con varias en el párrafo no se adivina a cuál se refiere.

    Nace en modo AVISO (normas.json): los falsos positivos esperables son cifras que están en el
    texto completo y no en el abstract. Solo acusa NO_RESPALDA; PENDIENTE (red, HALT), DUDOSO y
    NO_EVALUABLE callan aquí para no convertir cada respuesta con HALT en ruido — el que necesita el
    detalle lo tiene en el CLI y en el acta de `decision_alto_riesgo`.
    """
    pares = []
    for parrafo in _parrafos(t):
        ids_p = _ids_de(parrafo)
        for f in _frases(parrafo):
            if not re.search(r"\d", _CITA_EN_FRASE.sub(" ", f)):
                continue                           # sin cifras no hay nada literal que cotejar
            sin_enlaces = re.sub(r"\]\([^)]*\)|\S*[/\\]\S*|https?://\S+", " ", f)
            if not _CIFRA_DE_RESULTADO.search(sin_enlaces) or _SUS_DATOS.search(f):
                continue                           # un NCT de etiqueta o un dato suyo, no un resultado
            usa = _ids_de(f) or (ids_p if len(ids_p) == 1 else [])
            for x in usa:
                if len(pares) < MAX_IDS_CITA:
                    pares.append({"afirmacion": f.strip(), "cita": x})
    if not pares:
        return None
    herramienta = os.path.join(REPO, "tools", "soporte_cita.py")
    citas = list(dict.fromkeys(p["cita"] for p in pares))
    if not os.path.exists(herramienta):
        return _cifra_sin_cotejar(citas, "no encuentro `tools/soporte_cita.py`")
    try:
        r = subprocess.run([sys.executable, herramienta, "--lote"], input=json.dumps(pares),
                           capture_output=True, text=True, timeout=TIMEOUT_CITAS)
        datos = json.loads(r.stdout or "null")
    except Exception as e:                         # red o proceso caído: aviso, no acusación
        return _cifra_sin_cotejar(citas, "falló el cotejo (%s)" % type(e).__name__)
    if not isinstance(datos, list):
        return _cifra_sin_cotejar(citas, "el cotejo no devolvió resultados legibles")
    datos = [d for d in datos if isinstance(d, dict)]
    malas = [d for d in datos if d.get("estado") == "NO_RESPALDA"]
    if not malas:
        # 25-sep-26: PENDIENTE por RED ya no calla (callar era dar la cifra por cotejada). El de
        # HALT sí: es una parada deliberada de {{TITULAR}}, no un fallo, y avisar en cada respuesta con
        # HALT puesto sería el ruido que este check quiso evitar al nacer.
        red = [d.get("id") for d in datos if d.get("estado") == "PENDIENTE"
               and "HALT" not in str(d.get("motivo") or "")]
        if len(datos) < len(pares):
            red += citas[len(datos):]
        if red:
            return _cifra_sin_cotejar(list(dict.fromkeys(red)), "el registro no respondió")
        return None
    detalle = "; ".join("%s no contiene %s (bajo «%s»)" % (
        d.get("id"), ", ".join(d.get("faltan_numeros") or []), (d.get("afirmacion") or "")[:80])
        for d in malas)
    return ("La cita existe pero su abstract NO trae la cifra que le atribuyes: %s. Verificado con "
            "`tools/soporte_cita.py` (abstract de PubMed/CT.gov, sin LLM). Corrige la cifra, cita "
            "la fuente que sí la dice, o marca que sale del texto completo." % detalle)


def _cifra_sin_cotejar(citas, porque):
    """Hallazgo PENDIENTE de `cita_no_respalda`: avisa, se apunta, no bloquea."""
    return (PENDIENTE + " CIFRA SIN COTEJAR contra %s (%s). No sé si el abstract dice la cifra "
            "que le atribuyes: dila como «sin verificar» o cotéjala con `tools/soporte_cita.py`."
            % (", ".join(citas), porque))


# Solo se coteja una frase que atribuye un RESULTADO al estudio. El replay del 24-sep (2338 turnos,
# 108 disparos) enseñó que casi todo lo demás era un NCT usado de etiqueta en texto operativo
# («Moffitt (NCT…): borrador con el PDF de 75 págs») o un dato de ella junto a una cita.
_CIFRA_DE_RESULTADO = re.compile(
    r"%|\bmedian[ae]?\b|\bsupervivencia|\bsurvival|\bSLP\b|\bSG\b|\bPFS\b|\bOS\b|\bORR\b|"
    r"\bTRO\b|\btasa\b|\brespuesta\b|\bresponse\b|\bHR\b|\bhazard|\bIC\s*95|\bCI\b|"
    r"\bp\s*[<=]|\bn\s*=|\bpacientes\b|\bpatients\b|\briesgo relativo|\bodds", re.I)
# Un dato de ELLA junto a una cita («tu Ki-67 del 30 %, como en PMID X») no es la cifra del estudio.
_SUS_DATOS = re.compile(r"\btus?\s+(?:ki-?67|tumor|lesi[oó]n|lesiones|biopsia|informe|anal[ií]tica|"
                        r"marcador|marcadores|RM|TAC|PET|VAF|h[ií]gado|perfil|muestra)", re.I)
# Lo que es la cita misma (PMID, DOI, NCT) no cuenta como cifra de la frase.
_CITA_EN_FRASE = re.compile(r"10\.\d{4,9}/\S+|NCT\d{8}|PMID\s*[:#]?\s*\d{1,9}", re.I)


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


# La respuesta suena a que se buscaron novedades o soluciones (no a interpretar un dato fijo
# suyo): «último avance», «nuevos tratamientos», «ensayo clínico», «papers recientes», etc.
_INVESTIGA_NOVEDADES = re.compile(
    r"[uú]ltim\w+ (novedad|avance|estudio|publicaci|investigaci)\w*|nuevos? tratamientos?|"
    r"ensayos? cl[íi]nicos?|literatura (reciente|publicada)|papers? (recientes|nuevos)|"
    r"seg[uú]n (la|lo) (evidencia|publicado)|he (revisado|buscado|encontrado) (la )?"
    r"(literatura|evidencia|papers?)", re.I)

# Cualquier rastro de haber tocado una fuente CHINA, o de haberlo DECLARADO explícitamente (la
# declaración vale como cumplimiento: es justo lo que `feedback-investigar-incluye-china` pide).
_FUENTE_CHINA = re.compile(
    r"cn_fetch|radar_cn_vps|--tema cn-|AFF:\s*[\"']?China|\bCDE\b|\bChiCTR\b|\bICTRP\b|\bNMPA\b|"
    r"china\s*:\s*\S|fuente china", re.I)


def china_omitida(t, tools=None):
    """Investigar novedades clínicas sin mirar China (20-sep-26, Bloque A).

    {{TITULAR}}, 18-sep-26: *«busca en China también, que para eso tenemos hueco en servidor allí»*.
    El carril AUTOMÁTICO (`radar_ned_diario.TEMAS_CN`) ya mira China; el carril EN SESIÓN no
    tenía ningún freno — la petición vivía solo como comentario en ese fichero. Este check no
    exige que TODA investigación toque una fuente china de verdad (hay temas donde no aplica):
    exige que lo DIGA. Solo se evalúa si el turno de verdad invocó a `comite-medico` — si no
    investigó nada, no hay nada que acusar (evita el falso positivo obvio).
    """
    if tools is None:
        return None
    if not any(re.search(r"comite-medico", x, re.I) for x in tools):
        return None
    if not _INVESTIGA_NOVEDADES.search(t):
        return None
    corpus = t + "\n" + "\n".join(tools)
    if _FUENTE_CHINA.search(corpus):
        return None
    return ("Investigaste novedades o tratamientos con comité médico y no se ve ninguna fuente "
            "china tocada (Europe PMC con AFF:\"China\", CDE, ChiCTR/ICTRP) ni la capa CN del "
            "radar (tools/radar_ned_diario.py --tema cn-*, tools/cn_fetch.py, "
            "tools/radar_cn_vps.py). Declara qué fuente china se abrió y cuál no, o por qué no "
            "aplicaba aquí.")


# Léxico de `geografia_como_filtro`. Fuera de la función para poder testearlo suelto.
# A. Un país/región lejos, dicho como ubicación (no como dato de sede entre otras).
_GEO_LUGAR = re.compile(
    r"\ben\s+(china|jap[óo]n|corea(\s+del\s+sur)?|singapur|asia|estados unidos|eeuu)\b", re.I)
# A. Frases de distancia que ya cargan el descarte en la propia expresión.
_GEO_DISTANCIA = re.compile(
    r"queda(n)?\s+(muy\s+)?lejos|habr[íi]a que viajar|solo\s+\S+\s+recluta\s+en\s+\w+|"
    r"no\s+hay\s+sedes?\s+en\s+(espa[ñn]a|europa)|sin\s+sede\s+en\s+(espa[ñn]a|europa)|"
    r"fuera\s+de\s+(espa[ñn]a|europa)|no\s+tiene\s+sede\s+en\s+\w+", re.I)
# A. La señal de que la distancia se está usando como MOTIVO de no seguir, no como dato.
_GEO_DESCARTE_CUE = re.compile(
    r"\bpero\b|\bas[íi] que\b|\bpor (lo que|eso)\b|\blo descart\w*|\blo quito\b|\blo aparto\b|"
    r"\bbaja(mos|n)?\s+de\s+prioridad\b|\bmenor\s+prioridad\b|\bprioridad\s+menor\b|"
    r"\bno\s+lo\s+(recomiendo|contemplo|priorizo)\b|\bno\s+es\s+(viable|realista|factible)\b|"
    r"\bqueda(r[íi]a)?\s+fuera\b|\bmejor\s+(no|centrarnos|centrarme)\b|"
    r"\bno\s+merece\s+la\s+pena\b|\bcomplica(r[íi]a)?\b|\bdificulta(r[íi]a)?\b|"
    r"\bdescarta(mos|do|da)?\b", re.I)
# A. Excepciones: la regla SÍ quiere esto (logística tras seleccionar) o es otra regla (egress).
_GEO_EXCEPCION = re.compile(
    r"si\s+(entra|entras|entramos|acept\w+|se selecciona|eleg\w+)|"
    r"servidor|egress|modelo\s+(chino|de\s+IA)|infraestructura|nacionalidad\s+del\s+modelo|"
    r"open-weight|\bLLM\b|\bno\s+importa\b|independientemente\s+de|sin\s+que\s+(nos\s+)?importe",
    re.I)
# B. La cercanía presentada como ventaja de selección.
_GEO_MARCA_POSITIVA = re.compile(
    r"\blo bueno es\b|\bes una ventaja\b|\bhallazgo estrat[ée]gico\b|\bpunto a favor\b|"
    r"\bopci[óo]n m[áa]s (accesible|realista|c[óo]moda)\b|\bm[áa]s accesible por\b|"
    r"\bm[áa]s realista por\b", re.I)
_GEO_CERCA = re.compile(
    r"espa[ñn]a|europa|cercan[íi]a|proximidad|cerca de (casa|aqu[íi])|sin salir del pa[íi]s", re.I)
# Contexto: solo se evalúa si la respuesta de verdad habla de opciones clínicas o de triaje.
# «hospital»/«clínico» a secas se dejan fuera a propósito: son demasiado genéricos (cualquier
# mención médica los dispara) y no bastan para saber que hay una OPCIÓN sobre la mesa.
_GEO_CONTEXTO_CLINICO = re.compile(
    r"ensayo|tratamiento|opci[óo]n|candidat|laboratorio|protocolo|\btrial\b|reclut|vacuna|terapi",
    re.I)


def geografia_como_filtro(t, tools=None):
    """feedback-geografia-no-es-filtro — {{TITULAR}}, 20-sep-26, regla inquebrantable: «no me importa
    viajar a cualquier parte del mundo si es necesario porque es buena la solución, eso grábalo
    a fuego». Nace del incidente real de presentar «solo BeOne recluta en España» como hallazgo
    estratégico. Nace en modo AVISO midiendo falsos positivos, igual que `falsa_certeza` y
    `china_omitida` al estrenarse.

    Caza dos formas, las sutiles, no el primer ejemplo:
      A. Descartar o despriorizar por distancia: «está en China», «solo recluta en Japón»,
         «queda muy lejos», «no hay sedes en España», «habría que viajar» — usados como PERO o
         como motivo de no seguir (exige lugar/distancia + una señal de descarte en la MISMA
         frase; el dato solo, sin esa señal, no dispara nada).
      B. Presentar la cercanía como ventaja de selección: «lo bueno es que…», «la opción más
         accesible es…», «lo más realista por cercanía», cuando además nombra España/Europa/
         cercanía en la misma frase.

    NO caza (a propósito, para no ser un estorbo): mencionar dónde está un ensayo como dato
    informativo sin valorar («fase 3, 162 sedes, 4 en España»); logística de viaje DESPUÉS de
    haber seleccionado («si entras, habría que ir a Shanghái tres veces» — es justo lo que la
    norma SÍ quiere); egress de datos, servidores o nacionalidad de modelos de IA (esa es
    `feedback-muro-egress-no-nacionalidad`, otra norma); ni nada fuera de un contexto de
    opciones clínicas o triaje.

    Limitación honesta, declarada y no forzada: NO audita si una LISTA de opciones clínicas está
    ordenada poniendo lo español o europeo delante sin motivo de evidencia. Ese patrón es real
    (es la tercera forma que pidió el encargo), pero exige leer el orden completo de la lista y
    el porqué de cada puesto — no sale un detector determinista con pocos falsos positivos sin
    eso, así que no se fuerza. Queda como criterio para `verificacion` y para auditoría humana.
    """
    if not _GEO_CONTEXTO_CLINICO.search(t):
        return None
    for f in re.split(r"(?<=[.\n])\s+", _sin_bloques(t)):
        if len(f) < 15 or _GEO_EXCEPCION.search(f):
            continue
        if (_GEO_LUGAR.search(f) or _GEO_DISTANCIA.search(f)) and _GEO_DESCARTE_CUE.search(f):
            return ("Usas la distancia como motivo para descartar o despriorizar una opción: "
                    "«%s…». La geografía NO es filtro (regla inquebrantable de {{TITULAR}}, 20-sep-26): "
                    "se filtra por evidencia y por NED, no por dónde queda." % f.strip()[:150])
        if _GEO_MARCA_POSITIVA.search(f) and _GEO_CERCA.search(f):
            return ("Presentas la cercanía/accesibilidad geográfica como VENTAJA de selección: "
                    "«%s…». La geografía no es filtro ni a favor ni en contra." % f.strip()[:150])
    return None


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


# Clase 2 de la auditoría del 22-sep-26 («hecho» sin comprobar): se probó un check
# `hecho_sin_herramienta` (afirmar enviado/publicado/verificado con `tools == []`) y se DESCARTÓ con
# datos: en 2.032 turnos reales, la rama de prosa dio 2 disparos, los dos borradores de mensajes; la
# del «Hecho:» de cierre, ~25, casi todos resúmenes legítimos de turnos anteriores. El fallo real
# ocurre CON herramientas (se corrió algo, pero no lo que comprueba el efecto), y eso no lo ve una
# regex sobre el texto.


# Clase 3 (dato clínico sin sello) y clase 5 (investigación sin China) de la misma auditoría:
# probados y DESCARTADOS con `tools/replay_gate.py` sobre 2.032 turnos reales (22-sep-26).
#   · `clinico_sin_sello`: 166 disparos con ~30 % de acierto; afinado, 44 con ~23 %. Salta en
#     borradores con la voz de {{TITULAR}}, citas, datos de producto y frases que ya nombran su informe.
#   · `china_omitida` con disparador ampliado a cualquier tool de investigación: 4 disparos, 1 útil.
# Ninguna de las dos clases se deja cazar por una regex sobre el texto final; necesitan otro sitio.



def gestion_pide_ok(t, tools=None):
    """feedback-gestion-de-sesion-la-hago-yo — a fuego (22-sep-26): la fusión a casa base, la poda
    del worktree y el cierre de sesión los hago yo. Canta si le pido OK para eso.
    Afinado con replay_gate (14 días, 2.187 turnos): «fusionar» a secas NO basta, porque también
    es el PR de la web, y eso sí sale fuera. El objeto tiene que ser casa base, worktree o poda."""
    pide = (r"(espera tu OK|cuando (me )?des el OK|con tu OK|te pido (el )?OK|necesito tu OK|"
            r"¿\s*(fusiono|podo|borro|cierro|elimino)\b)")
    gestion = r"(casa base|worktree|\bpod(ar|a|o)\b|cerrar_sesion|cierre de sesi[óo]n)"
    # Lo que SÍ sigue siendo gate suyo, o no es una petición: la web y lo que sale fuera, el muro
    # (fusionar un hook del muro lo firma ella), el código rojo, y lo descrito en pasado.
    fuera = (r"\bnada\b|ya no|sin pedir|sin preguntar|#\d+|\bPR\b|pull|deploy|preview|vista previa|"
             r"netlify|web|producci[óo]n|publica|muro|hook|ci_barrido|guard|c[óo]digo rojo|"
             r"fusion[ée]\b|fusionad[oa]|se hizo|no he comprobado|cada una con tu OK|"
             r"con tu OK y verificad|^[-*\s]*\**hecho\b|commit `?[0-9a-f]{7}`? con tu OK")
    lineas = [l.strip() for l in _sin_fences(t).splitlines()]
    for n, l in enumerate(lineas):
        if not re.search(pide, l, re.I):
            continue
        # «**Qué espera tu OK**» como encabezado: el objeto va en la línea de debajo.
        f = l if len(l) > 30 or n + 1 >= len(lineas) else l + " " + lineas[n + 1]
        if re.search(gestion, f, re.I) and not re.search(fuera, f, re.I):
            return ("Le pides OK para gestión de sesión («%s…»). Fusionar a casa base, rescatar "
                    "ignorados y podar el worktree lo haces tú (`BTP_GIT_BASE_OK=1 "
                    "cerrar_sesion.py --apply`). Solo se para por trabajo vivo de otro o por el muro."
                    % f[:140])
    return None


# Checks que corren aunque la respuesta sea corta: «hecho» sin haber lanzado al comité es
# justo el incumplimiento, y el mínimo de caracteres lo dejaba pasar.
SIN_MINIMO = {"enrutado_incumplido"}


CHECKS = {
    "citas_fabricadas": citas_fabricadas,
    "preclinico_aplanado": preclinico_aplanado,
    "cita_no_respalda": cita_no_respalda,
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
    "china_omitida": china_omitida,
    "geografia_como_filtro": geografia_como_filtro,
    "gestion_pide_ok": gestion_pide_ok,
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
    """Checks de estos hallazgos que exigen bloquear: por modo propio de su norma, o por el global.
    Un hallazgo PENDIENTE (no se pudo comprobar) nunca bloquea: se avisa."""
    activas, _ = _reglas_activas()
    propio = {c: m for c, _s, m in activas}
    return [c for c, _s, m in hallazgos
            if not str(m or "").startswith(PENDIENTE)
            and ((propio.get(c) or modo_global) == "bloqueo" or c in SIEMPRE_BLOQUEA)]


def _prioriza(hallazgos, modo_global):
    """Lo que bloquea primero, luego lo PENDIENTE, luego el resto; estable dentro de cada grupo.
    25-sep-26: el recorte a MAX_HALLAZGOS se hacía en el orden del registro, donde
    `citas_fabricadas` va el 15.º. Con tres avisos de estilo delante, una cita INVENTADA se caía
    del recorte y la respuesta salía sin bloquear (y una «sin verificar», sin aviso).
    Los de modo `sombra` van al final: no se enseñan, y no pueden quitarle sitio a lo que sí."""
    bloq = set(_bloquean(hallazgos, modo_global))
    activas, _ = _reglas_activas()
    sombra = {c for c, _s, m in activas if m == "sombra" and c not in SIEMPRE_BLOQUEA}

    def peso(h):
        if h[0] in bloq:
            return 0
        if h[0] in sombra:
            return 3
        return 1 if str(h[2] or "").startswith(PENDIENTE) else 2
    return sorted(hallazgos, key=peso)


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


def _apunta(hallazgos, modo, reintento=False):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        # `bloqueo_real` corrige el hallazgo colateral del 13-sep-26: `modo` en la fila es el
        # GLOBAL en el momento de escribir, no si ESTE hallazgo bloqueó de verdad (un check con
        # `modo` propio, como `falsa_certeza`, puede bloquear con el global en "aviso"). Se
        # calcula igual que `_bloquean()`.
        # En la reescritura (`reintento`) no se bloquea nunca: se apunta para que el log no cuente
        # de menos (revisión P2, 25-sep-26: la reescritura salía sin auditar ni apuntar).
        bloqueados = set() if reintento else set(_bloquean(hallazgos, modo))
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
                    **({"reintento": True} if reintento else {}),
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
    texto = data.get("last_assistant_message") or ""
    global SESION
    SESION = str(data.get("session_id") or data.get("transcript_path") or "") or None
    if data.get("stop_hook_active"):
        # Ya frenó una vez este turno: no hacemos bucle, pero la reescritura SÍ se apunta
        # (`reintento: true`), o el log vivo cuenta de menos y las cotas de la escalera mienten.
        try:
            h = revisar(texto, _tools_del_turno(data.get("transcript_path")))
            if h:
                _apunta(h[:MAX_HALLAZGOS], _reglas_activas()[1], reintento=True)
        except Exception:
            pass                                  # fail-open: apuntar nunca frena
        return 0
    tools = _tools_del_turno(data.get("transcript_path"))
    hallazgos = revisar(texto, tools)
    if not hallazgos:
        return 0
    activas, modo = _reglas_activas()
    hallazgos = _prioriza(hallazgos, modo)[:MAX_HALLAZGOS]
    _apunta(hallazgos, modo)
    # `sombra` (escalera P2, 25-sep-26): el check se apunta para medirlo, pero no se enseña. Para
    # los ruidosos, que en aviso solo hacían ruido. Nunca aplica a SIEMPRE_BLOQUEA.
    sombra = {c for c, _s, m in activas if m == "sombra" and c not in SIEMPRE_BLOQUEA}
    hallazgos = [h for h in hallazgos if h[0] not in sombra]
    if not hallazgos:
        return 0
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
        ("gestion_pide_ok", "Qué espera tu OK: fusionar la rama a casa base con cerrar_sesion.py "
                            "y podar el worktree cuando lo veas, que ya está todo en verde."),
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
