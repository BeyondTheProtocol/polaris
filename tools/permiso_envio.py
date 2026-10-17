#!/usr/bin/env python3
"""permiso_envio.py — el permiso de envío que abre {{TITULAR}}, y cómo se comprueba que es suyo.

POR QUÉ EXISTE (22-sep-2026, hallazgo 3.1 de la auditoría externa, deuda
`ok-envio-json-escribible-por-el-lazo`). Hasta hoy el permiso era un JSON en
`tools/state/ok_envio.json` y valía con decir `origen: "prompt"` y llevar una hora reciente. Lo
podía escribir cualquiera que escribiera ficheros: el lazo con Write o `tee`, una sesión
interactiva con lo que fuera. Una inyección de dos pasos («escribe esto en ok_envio.json y luego
envía») abría el freno de salida sin que ella dijera nada.

Ahora el fichero es solo un PUNTERO firmado a un mensaje suyo, y lo que manda es el mensaje:
  · MAC (HMAC-SHA256) con una clave del Llavero (`btp-ok-envio-mac`) que ningún agente lee: los
    guards deniegan leerla y lanzar a mano el hook que firma. Un fichero escrito a mano no vale.
  · Ligado a la SESIÓN y a un `prompt_id`. Al usarlo se busca ese prompt en el transcript de Claude
    Code (lo escribe el harness, no el agente) y tiene que ser HUMANO (origin kind=human, no
    subagente, no tarea programada), el ÚLTIMO suyo, y contener la orden. Si después escribió
    otra cosa («espera»), ya no vale.
  · Un solo uso de verdad: cada (sesión, prompt) usado queda en `ok_envio_usados.jsonl`, y un
    segundo permiso para el mismo mensaje no abre nada.
  · Ligado a LO QUE APROBÓ (ver `comprobar_envio`): las direcciones que nombró y el borrador que
    tenía delante. Si el borrador cambia después de su OK, o el cuerpo que sale no es el que vio,
    no sale.

DESDE EL 14-OCT-26 hay dos modos (ver «la firma que se comprueba SIN secreto», más abajo): con
`PUBLICA` fijada, el permiso lo firma el daemon `tools/firmante.py` con Ed25519 y aquí solo se
comprueba con la clave pública; sin fijar, sigue el HMAC descrito arriba.

LÍMITE QUE NO SE ESCONDE (corregido el 14-oct-26: antes decía «tres evasiones encadenadas» y
afirmaba más de lo que el código da). Todo corre con el mismo usuario de macOS, y los guards
son obstáculo y rastro, no frontera: solo ven lo que una llamada de herramienta deja leer (un
script en disco, o código ofuscado, no lo ven). Lo que de verdad sostiene el permiso son DOS
cosas, y basta con que caiga la que toque:
  · QUE EL TRANSCRIPT NO SE PUEDA ESCRIBIR. Es la única barrera común a los dos modos: quien
    consiga añadir una línea «humana» al transcript de la sesión obtiene un permiso válido. En
    modo Ed25519 ni siquiera necesita nada más: el firmante firma lo que el transcript confirma.
  · EN MODO HMAC, además, QUE LA CLAVE NO SE PUEDA LEER. Escribir el fichero del permiso no es
    una barrera aparte (lo hace cualquier proceso que el guard no vea). Y hasta el 14-oct-26
    la lectura de la clave tenía una vía obvia abierta: `nice security …` pasaba el guard
    (confirmado con test; cerrado ese día junto con otros envoltorios).
Lo que SÍ está cerrado y con test: las vías obvias de escribir el permiso, su libro y los
transcripts; leer las claves de firma por `security`, también detrás de un envoltorio; lanzar a
mano el emisor o el firmante; hablarle a su socket; un transcript fuera de `~/.claude/projects`
o de otra sesión. No es una garantía criptográfica contra el propio usuario.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _casa  # noqa: E402

SERVICIO = "btp-ok-envio-mac"
# Ruta ABSOLUTA (13-oct-26): con el nombre desnudo, el `security` que corre lo elige el PATH de
# quien lanza el proceso, y uno falso delante devolvería la «clave» que quisiera (o recibiría la
# nueva en argv al crearla). Test: tests/test_permiso_security_ruta.py.
SECURITY = "/usr/bin/security"
VIDA_S = 600
ORIGENES = ("prompt",)
AUTOMATICOS = ("<task-notification", "<scheduled-task", "<ci-monitor-event")
# Un prompt que lleve CUALQUIERA de estas etiquetas, en cualquier sitio, no lo tecleó ella: lo
# lanzó una tarea programada, una notificación o el monitor de CI (24-sep-26, verificacion). Antes
# se miraba DESPUÉS de quitar los bloques y solo al principio, así que un cierre falso de la
# etiqueta dentro del cuerpo de una tarea dejaba el resto como «texto suyo» y abría un permiso de
# envío real (sonda e2e). Se mira el texto CRUDO.
_AUTOMATICO = re.compile(r"</?(?:task-notification|scheduled-task|ci-monitor-event)\b", re.I)


def es_automatico(texto):
    return bool(_AUTOMATICO.search(texto or ""))

# Órdenes de envío de {{TITULAR}}. Imperativo y en segunda persona: «envíalo», «mándaselo», «publica».
# NO entran el condicional, el subjuntivo ni el pasado («habría que enviar», «cuando lo envíe»,
# «lo envió ayer»), que son conversación sobre el envío, no la orden. La tercera persona del
# presente SÍ entra aunque no sea orden («Vega los envía»): en español es la misma forma que el
# imperativo y una regex no las distingue. Tampoco distingue una cita sin comillas («me dijo:
# envíalo»). Declarado, no escondido: deuda `orden-envio-falsos-amigos` (verificacion, 25-sep-26). (Vivía en ok_envio_prompt.py; aquí la usan el
# que emite el permiso y los que lo comprueban contra el transcript: una sola regex, no dos.)
# El PLURAL entra (25-sep-26): pidió «Publicalos» para cinco comentarios y el permiso no se abrió,
# porque aquí solo había singulares. No era esa palabra, era toda la clase (envíalos, mándaselos,
# respóndelas, súbelos a la web…). `_CLITICO` lleva los dos números en un solo sitio para que no
# vuelva a faltar en uno de los verbos. Medido antes de fusionar contra sus prompts reales.
_CLITICO = r"(?:selos|selas|selo|sela|melos|melas|melo|mela|los|las|les|lo|la|le)"
ORDEN = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"env[ií]a" + _CLITICO + r"?\b|env[ií]ame\b|"
    r"m[áa]nda" + _CLITICO + r"?\b|"
    r"publ[ií]ca(?:los|las|lo|la)?\b|"
    r"resp[óo]nde(?:les|los|las|le|lo|la)?\b|contesta(?:le|les)?\b|"
    r"dale\s+a\s+enviar\b|"
    r"ya\s+puedes\s+(?:enviar|mandar|publicar)\b|"
    r"adelante\s+con\s+el\s+(?:env[ií]o|correo|mensaje)\b|"
    # Publicar en su web es lo mismo que enviar: sale al mundo y lleva su nombre.
    # `a\s*la` y no `a\s+la` a propósito: ella escribió «Añade ala cronologia» (20-sep-26) y el
    # permiso no se abrió por un espacio. Un freno que exige escribir sin erratas es un freno que
    # acaba estorbando, y entonces se quita — que es peor que no tenerlo.
    r"(?:p[óo]n|s[úu]be|a[ñn][áa]de|mete|a[ñn][áa]d[ae]?)(?:los|las|lo|la|le)?\s+"
    r"(?:a|en)\s*la\s+(?:web|cronolog[íi]a|timeline|l[íi]nea\s+de\s+tiempo)\b|"
    r"que\s+salga\s+en\s+la\s+web\b"
    r")", re.I)

# Órdenes de PROGRAMAR (24-sep-26). Crear o lanzar una tarea programada es salida (salida_guard:
# es un agente que actuará solo más tarde), pero ninguna frase natural suya lo abría: pidió
# «haz la tarea programada» y «ya puedes cread la tarea peorramada» y el permiso solo se abrió con
# «ya puedes enviar», que no dice lo que aprueba. Esta orden abre un permiso de ALCANCE
# «programar»: vale para `*_scheduled_task` y para nada más (ni correo, ni web). «Programa» a secas
# no cuenta: es también un sustantivo («el programa»); hace falta el clítico o «la tarea».
# Imperativo al principio de la frase (o tras «vale», «dale», «venga»…): «el programa la revisión»
# o «Vega programa la tarea cada lunes» no son órdenes suyas, y «crear» en infinitivo va casi
# siempre en una subordinada («antes de crear la tarea…»). «Tarea» sola en este sistema es también
# una tarjeta del Tablero, así que «crea la tarea en el Tablero/Vega» no cuenta (revisión de
# verificacion, 24-sep-26: 9 frases así abrían el permiso).
_INICIO = r"(?:^|\n\s*|[,.;:¿?¡!]\s*|\b(?:vale|venga|dale|ok|pues|ahora|y|por\s+favor|porfa)\s+)"
ORDEN_PROGRAMAR = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"progr?[áa]ma(?:lo|la|los|las)\b|"
    r"ya\s+puedes\s+(?:programar|crea[rd]?\s+la\s+tarea)\b"
    r")|" + _INICIO + r"("
    r"progr?[áa]ma\s+(?:la|una|esa|esta)\s+(?:tarea|revisi[óo]n|comprobaci[óo]n)\b|"
    r"crea[d]?\s+(?:la|una|esa|esta)\s+tarea\b(?!\s+(?:en|a|para)\s+(?:el\s+)?(?:tablero|vega))|"
    r"haz\s+(?:la|una|esa|esta)\s+tarea\s+\w*ramada\b"
    r")", re.I)

# Órdenes de FUSIONAR un PR (25-sep-26). `gh pr merge` es salida (fusionar la web la publica),
# pero ninguna frase suya para ESO lo abría: escribió «fusiona» para el #224 y el freno no lo
# reconoció, porque aquí solo había envía/manda/publica. Abre un permiso de alcance «fusionar»:
# vale para `gh pr merge` del PR del que se hablaba y para nada más. Imperativo al principio de
# frase (como programar): «hay que fusionar», «¿fusionamos?», «la fusión de ayer» no son órdenes.
# «fusiona a casa base» tampoco: eso es local, no pasa por este freno y no debe abrir nada.
ORDEN_FUSIONAR = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"fusi[óo]na(?:lo|la|los|las)\b|m[ée]rgea(?:lo|la|los|las)\b|"
    r"dale\s+a\s+(?:fusionar|mergear)\b|"
    r"ya\s+puedes\s+(?:fusionar|mergear)\b"
    r")|" + _INICIO + r"("
    r"(?:fusiona|m[ée]rgea)\b(?!\s+(?:a|en|con)\s+(?:la\s+)?(?:casa\s+base|master|base)\b)|"
    r"haz\s+(?:el\s+)?merge\b"
    r")", re.I)

# «Merge» en inglés (26-sep, #235; deuda `ok-envio-no-reconoce-merge-en-ingles`), SOLO como
# mensaje entero: «merge conflict en la rama» o «merge the branch locally» no son órdenes
# (verificacion, 17-oct-26). Mismo candado que «fusiona»: el PR y su SHA tienen que estar delante.
MERGE_SOLO = re.compile(r"\s*merge(?:\s+it)?[\s.!]*", re.I)

# Órdenes de ABRIR un PR (27-sep-26, deuda `ok_envio_no_entiende_abre_pr`). Dentro de casa base
# `gh pr create/edit/comment/ready/review` es salida, pero su frase natural no abría nada: escribió
# «abre pr» y «fusiona y abre pr» (26-sep), antes «Haz pr y enséñame el preview» y «y haz tú el pr»
# (20-sep), y solo pasaba diciendo «publícalo», que lo abre TODO. Abre el alcance «pr»: vale para
# abrir o editar un PR y para nada más; fusionarlo sigue pidiendo su «fusiona» con el SHA delante.
# Imperativo al principio de frase (como fusionar): «¿abrimos un PR?», «el PR que abriste» o
# «abrir un PR sería…» no son órdenes.
ORDEN_PR = re.compile(
    r"(?:^|[\s,.;:¿?¡!])("
    r"[áa]bre(?:lo|la)\s+(?:como|en)\s+(?:un\s+)?(?:pr|pull\s+request)\b|"
    r"ya\s+puedes\s+(?:abrir|crear|hacer)\s+(?:el\s+|un\s+)?(?:pr|pull\s+request)\b"
    r")|" + _INICIO + r"("
    r"(?:abre|crea|haz)(?:\s+t[úu])?\s+(?:el\s+|un\s+|ese\s+|este\s+)?(?:pr|pull\s+request)\b"
    r")", re.I)
# Los `gh pr` que abre ese alcance (el merge NO: ese es «fusionar»).
_PR_ABRE = re.compile(r"\bgh\s+pr\s+(?:create|edit|comment|ready|review)\b")

# Si el mensaje habla de dejarlo en borrador, NO es una orden de envío aunque use el verbo.
# `(?:lo|la|los|las)`: «no la envíes, envíala mañana» abría un envío hoy (verificacion, 24-sep-26).
# Plural (25-sep-26): si la orden entiende «publícalos», el freno tiene que entender el plural y
# los clíticos que lo acompañan, o una frase que frena y ordena a la vez abre el permiso HOY:
# «déjalos en borrador, publícalos mañana», «solo los borradores, envíalos mañana», «no se los
# envíes todavía, envíaselos mañana» (verificacion, con las frases compiladas contra las dos
# versiones). Las formas con se/me ya se colaban en singular; se cierran a la vez.
FRENA = re.compile(r"(no\s+(?:(?:me|te|se)\s+)?(?:(?:lo|la|los|las|le|les)\s+)?"
                   r"(?:env[ií]es|mandes|publiques)|"
                   r"d[ée]ja(?:se|me)?(?:los|las|lo|la)\s+en\s+borrador|"
                   r"solo\s+(?:el\s+|los\s+)?borrador(?:es)?|sin\s+enviar|"
                   r"no\s+(?:(?:lo|la|los|las)\s+)?programes|sin\s+programar|"
                   r"no\s+(?:hay\s+que|hace\s+falta)\s+crear|no\s+(?:la\s+)?crees\b|sin\s+crear|"
                   r"antes\s+de\s+crear|"
                   r"no\s+(?:(?:lo|la|los|las)\s+)?(?:fusiones|mergees)|sin\s+fusionar|"
                   r"no\s+hagas\s+(?:el\s+)?merge)", re.I)

# Tools que un permiso de alcance «programar» deja pasar. Todo lo demás exige una orden de envío.
# `update_`: cambiar una tarea existente es cambiar lo que un agente hará solo (verificacion).
PROGRAMAN = re.compile(r"__(?:create|run|update)[_-]?scheduled[_-]?task$", re.I)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CAMPOS_DESTINO = ("to", "cc", "bcc", "recipient", "recipients")
_DRAFT = re.compile(r"__(create_draft|update_draft)$")


# Lo que el harness y los hooks PEGAN al mensaje: recordatorios de worktree, contexto del lazo,
# memorias recordadas, avisos de tareas. Llega dentro del mismo campo `prompt`, así que sin quitarlo
# el emisor estaría juzgando texto que ella no ha escrito (visto en vivo el 24-sep-26: el permiso
# que había abierto otra sesión llevaba de motivo un `<system-reminder>` del harness). Una frase de
# envío dentro de uno de estos bloques —o en una memoria que la cite— NO es una orden suya.
INYECTADO = re.compile(
    r"<(system-reminder|task-notification|scheduled-task|ci-monitor-event)\b.*?</\1>|"
    r"<(?:task-notification|scheduled-task|ci-monitor-event)\b[^>]*/?>", re.S | re.I)


def solo_suyo(texto):
    """El texto sin los bloques que pega el harness: lo que de verdad escribió ella."""
    return INYECTADO.sub(" ", texto or "").strip()


# `push` y `drive` (17-oct-26): subir commits que publican y compartir un fichero de Drive con
# enlace. Una orden explícita («publícalo», «envíalo») los cubre como cubría todo; un «sí» a una
# propuesta (ver ASENTIMIENTO) abre SOLO el de la propuesta.
TODO = frozenset({"envio", "programar", "fusionar", "pr", "push", "drive"})
# Lo que abre una orden explícita de envío. `drive` NO (verificacion, 17-oct-26): un «envíalo» para
# un correo no puede servir para hacer público un fichero de Drive que salió en pantalla. Hacer
# público algo con enlace pide su «sí» a una línea `compartir-drive` con ese FILE_ID.
EXPLICITO = TODO - {"drive"}


# ── ASENTIMIENTO A UNA PROPUESTA (17-oct-26) ─────────────────────────────────────────────────
# El vocabulario fijo dejaba fuera órdenes suyas clarísimas: «pues hazlo» (17-oct, compartir un
# vídeo de Drive), «sube» (push, 26-sep, 3 veces), «Merge», «sí». Cinco deudas de la misma clase.
# Ampliar la lista de verbos no lo arregla: «sí» o «hazlo» no dicen QUÉ aprueban. Lo dice lo que
# ella tenía delante. Así que:
#   · el asistente, cuando propone una salida, escribe UNA línea con forma fija
#       🔐 Propuesta de salida: <tipo> · <objeto>
#     (tipos: enviar, publicar, subir, fusionar, abrir-pr, programar, compartir-drive; el objeto
#     lleva lo que ata la salida: PR + SHA, FILE_ID, destinatario…);
#   · si su mensaje ENTERO es un asentimiento corto («sí», «pues hazlo», «dale», «compártelo»,
#     «Merge»…) y en mi último turno hay EXACTAMENTE una línea así, y está en mi último bloque de
#     texto, se abre el permiso SOLO para el tipo de esa línea (nunca un lote, nunca «todo»).
# Lo que no cambia: el permiso sigue naciendo solo de un prompt HUMANO de esta sesión (un texto
# de una herramienta, un subagente u otra sesión no es su prompt), un uso, 10 minutos, y atado al
# objeto (PR+SHA, borrador que vio, FILE_ID). Un asentimiento ENCOLADO a mitad de turno no cuenta:
# pudo escribirlo antes de que la propuesta apareciera.
# LÍMITE DECLARADO: la frontera de confianza es el texto del asistente que ella lee, igual que con
# el borrador `en_vista`. Si una inyección consiguiera que el asistente escribiera una propuesta
# mala y ella dijera «sí» sin leerla, saldría eso y solo eso.
PROPUESTA = re.compile(
    r" {0,3}(?:[-*][ \t]+)?(?:\*\*|__)?🔐\s*(?:\*\*|__)?\s*propuesta\s+de\s+salida\s*(?:\*\*|__)?\s*:"
    r"\s*(?:\*\*|__)?\s*(enviar|publicar|subir|fusionar|abrir-pr|programar|compartir-drive)\b\s*"
    r"(?:\*\*|__)?\s*[·:|—–-]\s*(\S.*?)\s*(?:\*\*|__)?\s*", re.I)
TIPOS_PROPUESTA = {"enviar": "envio", "publicar": "envio", "subir": "push", "fusionar": "fusionar",
                   "abrir-pr": "pr", "programar": "programar", "compartir-drive": "drive"}
# Lo que la vuelve ilegible a la vista y no a la regex (verificacion, 17-oct-26): selectores de
# variante, espacios de ancho cero, marcas de dirección. Se quitan ANTES de buscar, y cualquier
# línea que tras eso mencione «propuesta de salida» cuenta como candidata, sea válida o no.
_INVISIBLES = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f"
                         "\u2028-\u202f\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0"
                         "\U000e0000-\U000e0fff]")


def _limpia(t):
    """NFKC y sin invisibles: lo que se ve igual, se compara igual."""
    import unicodedata
    return _INVISIBLES.sub("", unicodedata.normalize("NFKC", t or ""))
_MENCION = re.compile(r"propuesta\s+de\s+salida", re.I)

# Las palabras de un asentimiento. El mensaje entero tiene que estar hecho SOLO de estas (máximo
# 6 palabras y 60 caracteres), y al menos una tiene que ser de `_SI`. Una palabra fuera de la
# lista («pero», «no», «mañana», «quieres») y ya no es un asentimiento: «sí, pero no lo envíes»,
# «si quieres», «no lo hagas», «hazlo mañana» no abren nada. Una PREGUNTA («¿sí?») tampoco, ni un
# emoji que no sea de los de decir que sí, ni «vale ya» (que en su boca es «basta»).
_SI = frozenset("""hazlo hazla adelante dale sí si vale ok okay okey venga perfecto confirmo
confirmado aprobado compártelo compartelo compártela compartela compártelos súbelo subelo súbela
subela súbelos sube publícalo publicalo publícala publicala mándalo mandalo envíalo envialo
fusiónalo fusionalo fusiona merge mergéalo mergealo mergea yes yep do go""".split())
# Sin «ya» (verificacion, 17-oct-26): «venga ya», «vale ya» en su boca son «ni hablar».
_RELLENO = frozenset("""pues claro genial bien guay de acuerdo por favor porfa tú tu it ahead
please eso así asi entonces y""".split())
_PALABRA = re.compile(r"[a-záéíóúüñ]+")
# Solo letras, espacios, `. , ; ! ¡` y los emojis de decir que sí. Cualquier otra cosa, fuera.
_PERMITIDO = re.compile(r"[a-záéíóúüñ\s.,;!¡]|💜|❤️|❤|👍|✅|🙏|👌|💪|🚀")


def es_asentimiento(texto):
    """¿Su mensaje ENTERO es un asentimiento corto? (No dice a qué: eso lo dice la propuesta.)"""
    if es_automatico(texto):
        return False
    t = solo_suyo(texto).lower().strip()
    if not t or len(t) > 60 or FRENA.search(t) or _PERMITIDO.sub("", t):
        return False
    palabras = _PALABRA.findall(t)
    if not palabras or len(palabras) > 6:
        return False
    if any(p not in _SI and p not in _RELLENO for p in palabras):
        return False
    if "vale" in palabras and "ya" in palabras:
        return False
    return any(p in _SI for p in palabras)


def _sin_cercas(texto):
    """El texto con los bloques ``` ... ``` vaciados (una línea dentro de código es una cita)."""
    return re.sub(r"```.*?(?:```|\Z)", lambda m: "\n" * m.group(0).count("\n"), texto, flags=re.S)


def propuesta_unica(textos):
    """La propuesta a la que contesta un «sí», o None (17-oct-26, endurecida tras `verificacion`).

    Vale SOLO si:
      · en todo el turno hay UNA sola línea que mencione «propuesta de salida» (contando citas,
        bloques de código y variantes con caracteres invisibles: si hay otra, aunque no sea
        válida, no vale ninguna);
      · esa línea es una propuesta bien formada, en su propia línea, sin cita (`>`) ni código;
      · es la ÚLTIMA línea no vacía del ÚLTIMO bloque de texto: lo último que ella leyó. Un
        «sí» a una pregunta escrita debajo de la línea no es un sí a la línea;
      · si es `enviar`, el objeto nombra al menos una dirección (la salida se ata a ella)."""
    textos = [_limpia(t) for t in (textos or [])]
    if not textos:
        return None
    menciones = [(i, l) for i, t in enumerate(textos) for l in t.split("\n")
                 for _m in _MENCION.finditer(l)]
    if len(menciones) != 1 or menciones[0][0] != len(textos) - 1:
        return None
    ultimo = _sin_cercas(textos[-1])
    lineas = [l for l in ultimo.split("\n") if l.strip()]
    if not lineas or lineas[-1] != menciones[0][1]:
        return None
    m = PROPUESTA.fullmatch(lineas[-1])
    if not m:
        return None
    tipo, objeto = m.group(1).lower(), re.sub(r"<[^>]*>", " ", m.group(2)).strip()
    if tipo == "enviar" and not EMAIL.search(objeto):
        return None
    return dict(tipo=tipo, objeto=objeto, alcance=TIPOS_PROPUESTA[tipo])


def alcance_por_propuesta(texto, visto):
    """(alcance, propuesta|None): lo que abre un asentimiento suyo a lo que tenía delante."""
    if not es_asentimiento(texto):
        return set(), None
    p = propuesta_unica(visto)
    if not p:
        return set(), None
    return {p["alcance"]}, p


def es_candidata(texto):
    """Para el hook: ¿merece la pena mirar? Orden explícita, o asentimiento (que solo abrirá si
    hay propuesta delante)."""
    return es_orden(texto) or es_asentimiento(texto)


def propuesta_previa(transcript_path, session_id, prompt_id=None):
    """La propuesta única del último turno del asistente ANTES de su mensaje, leída del transcript
    (solo lectura). La usa el hook para callar ante un «sí» que no contesta a ninguna: así no se
    molesta al firmante con cada «sí» de una conversación normal. No decide nada: quien firma
    vuelve a mirarlo todo en `contexto`."""
    ruta, _m = ruta_transcript(transcript_path, session_id)
    if not ruta:
        return None
    textos = []
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                try:
                    e = json.loads(linea)
                except Exception:
                    continue
                if not isinstance(e, dict):
                    continue
                if _es_humano(e) or _es_humano_encolado(e):
                    if prompt_id and e.get("promptId") == prompt_id:
                        break                    # su mensaje ya está: lo de antes es lo que vio
                    textos = []
                    continue
                if e.get("type") == "assistant" and not e.get("isSidechain"):
                    for b in (e.get("message") or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "text":
                            textos.append(b.get("text") or "")
    except Exception:
        return None
    return propuesta_unica(textos)


def alcance(texto):
    """Qué abre su mensaje: «envio», «programar», «fusionar», combinados o nada. Un envío vale
    también para programar y para fusionar (así se publicó el #220 con «publícalo»); programar y
    fusionar NO valen para enviar ni publicar por otra vía."""
    if es_automatico(texto):
        return set()
    texto = solo_suyo(texto)
    if not texto or FRENA.search(texto):
        return set()
    out = set()
    if ORDEN.search(texto):
        out |= EXPLICITO
    if ORDEN_PROGRAMAR.search(texto):
        out.add("programar")
    if ORDEN_FUSIONAR.search(texto) or MERGE_SOLO.fullmatch(texto):
        out.add("fusionar")
    if ORDEN_PR.search(texto):
        out.add("pr")
    return out


def es_orden(texto):
    return bool(alcance(texto))


def permite(ctx, que):
    """¿El permiso validado (su `ctx`) cubre `que` («envio» | «programar» | «fusionar»)? Un ctx sin
    alcance (formato anterior) se lee como envío, que lo cubría todo; un alcance VACÍO no cubre
    nada (antes `or` lo convertía en permiso total)."""
    a = (ctx or {}).get("alcance")
    return que in (EXPLICITO if a is None else a)


def dir_permisos():
    """Un permiso por SESIÓN (26-sep-26, deuda `ok_envio_fusionar_permiso_se_pierde`). Antes había
    un solo `ok_envio.json` para todas: si dos sesiones esperaban al CI con su «fusiona», la orden
    de una pisaba la de la otra y la primera se quedaba sin permiso. Ahora cada sesión tiene su
    fichero en este directorio, que el muro protege entero como zona de escritura."""
    return os.path.join(_casa.state_dir(), "ok_envio")


def _sid(sesion):
    """El nombre de fichero de una sesión: solo [A-Za-z0-9_-], máx. 64. Un `../` no sale del
    directorio. Vacío → `sin-sesion`."""
    return re.sub(r"[^A-Za-z0-9_-]", "", str(sesion or ""))[:64] or "sin-sesion"


def _ext():
    """`.ok2` para los permisos del firmante: el barrido de un worktree con el código de antes
    borra todo `.json` que no pase su HMAC."""
    return ".ok2" if modo() == ALG else ".json"


def token_path(sesion=""):
    return os.path.join(dir_permisos(), _sid(sesion) + _ext())


def _legado():
    """El hueco único de antes. Los hooks nuevos no lo leen; se borra solo cuando ya no vale."""
    return os.path.join(_casa.state_dir(), "ok_envio.json")


def _todos():
    try:
        return [os.path.join(dir_permisos(), n) for n in os.listdir(dir_permisos())
                if n.endswith(_ext())]
    except Exception:
        return []


def hay_alguno():
    """¿Hay algún permiso emitido, de cualquier sesión? Vía rápida para no tocar el Llavero."""
    return bool(_todos())


def usados_path():
    return os.path.join(_casa.state_dir(), "ok_envio_usados.jsonl")


# ── la clave ─────────────────────────────────────────────────────────────────────────────────
def _llavero_estado():
    """(valor|None, estado). estado: "ok" | "no-existe" | "ilegible".

    Distingue dos casos que antes eran el mismo `None` (9-oct-26): la clave NO EXISTE (código 44,
    `errSecItemNotFound`: hay que crearla) o EXISTE PERO NO SE PUEDE LEER (36, «User interaction is
    not allowed»: Llavero bloqueado, típico de una sesión por SSH; también tiempo agotado o
    cualquier otro fallo). En el segundo nunca se intenta crear: no arregla nada y, si el Llavero
    se desbloquea luego, habría dos claves."""
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", SERVICIO, "-w"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None, "ilegible"
    if r.returncode == 0:
        return (r.stdout.strip(), "ok") if r.stdout.strip() else (None, "ilegible")
    return None, ("no-existe" if r.returncode == 44 else "ilegible")


def _llavero_leer():
    return _llavero_estado()[0]


def _llavero_crear():
    import getpass
    valor = secrets.token_hex(32)
    cuenta = os.environ.get("USER") or getpass.getuser()
    try:
        subprocess.run([SECURITY, "add-generic-password", "-a", cuenta, "-s", SERVICIO,
                        "-w", valor], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return _llavero_leer()          # si otra sesión la creó a la vez, gana la que quedó


CLAVE_TEST = None   # los tests en proceso la fijan aquí; nunca por entorno (ver `clave`)


def _state_aislado():
    """True si el estado NO es el de casa base (tests): ahí no se crea nada en el Llavero real."""
    try:
        return os.path.realpath(_casa.state_dir()) != os.path.realpath(
            os.path.join(_casa.casa_base(), "tools", "state"))
    except Exception:
        return True


def clave_estado(permitir_env=False, crear=False):
    """(bytes|None, estado) con estado "ok" | "no-existe" | "ilegible". Como `clave`, pero dice POR
    QUÉ no hay clave. Solo se crea cuando NO existe; si existe y no se puede leer, no se toca."""
    if CLAVE_TEST:
        return CLAVE_TEST, "ok"
    if permitir_env and os.environ.get("BTP_OK_ENVIO_CLAVE"):
        return os.environ["BTP_OK_ENVIO_CLAVE"].encode(), "ok"
    if os.environ.get("BTP_OK_ENVIO_LLAVERO_ILEGIBLE"):      # solo para los tests
        return None, "ilegible"
    if os.environ.get("BTP_OK_ENVIO_SIN_LLAVERO"):
        return None, "no-existe"
    v, est = _llavero_estado()
    if v is None and est == "no-existe" and crear and not _state_aislado():
        v = _llavero_crear()
        est = "ok" if v else "ilegible"
    return (v.encode() if v else None), est


def clave(permitir_env=False, crear=False):
    """bytes o None. `permitir_env` SOLO para los hooks: su entorno lo pone el harness, y los
    tests lo usan para no tocar el Llavero real. Una tool que lanza el agente (web_novedad,
    ok_envio.py) NO lo permite: ese entorno lo elige quien la lanza, y bastaría con
    `BTP_OK_ENVIO_CLAVE=x python3 tools/web_novedad.py` para firmar lo que quisiera."""
    return clave_estado(permitir_env, crear)[0]


# ── la firma que se comprueba SIN secreto (14-oct-26, «pieza A») ──────────────────────────────
# EL PROBLEMA. {{TITULAR}} trabaja desde la app en otro dispositivo, que entra al Mac por SSH. Ahí el
# Llavero no se deja leer (`security` da el código 36), y tanto abrir el permiso como
# COMPROBARLO necesitaban la clave del HMAC: su «fusiona» no abría nada.
# EL ARREGLO. El permiso lo firma con Ed25519 un daemon (`tools/firmante.py`, launchd `gui/501`,
# que sí lee el Llavero) y aquí se comprueba con la clave PÚBLICA, que no es secreta y va en
# este fichero. La sesión ya no necesita el Llavero para nada.
#
# `PUBLICA` es None hasta que el firmante esté activado y su clave pública se FIJE AQUÍ POR
# COMMIT (64 hex; el paso exacto, en `tools/launchd/FIRMANTE.md`). Va en código versionado para
# que cambiarla deje rastro (un diff, un commit), no porque no se pueda: este fichero NO está
# entre las rutas que los guards deniegan escribir, y cada worktree lee SU copia. En casa base
# la regla de permisos solo cubre `.claude/hooks/`. Proteger `tools/permiso_envio.py`,
# `firmante.py`, `lote_envio.py` y `_casa.py` es una propuesta abierta (FIRMANTE.md, «Pendiente»).
#
# CONVIVENCIA, sin vía que dure para siempre sin decirlo:
#   · mientras `PUBLICA` es None manda el HMAC de antes, tal cual (y por SSH sigue sin abrir);
#   · el commit que fija `PUBLICA` RETIRA el HMAC en ese árbol: un permiso con MAC deja de
#     valer ahí, y para volver atrás hay que revertir ese commit;
#   · un worktree nacido antes de ese commit sigue con su código y su HMAC hasta que se cierra
#     o se pone al día. No se puede arreglar desde aquí; está en FIRMANTE.md cómo listarlos.
# Los permisos nuevos usan otra extensión (`.ok2`, `.lote2`) en el mismo directorio: el barrido
# de un worktree viejo borra todo `.json` que no pase su HMAC y se los cargaría.
PUBLICA = "823a32a7e3e990e1500f6e27ad4f32170a5dd529fea5fd087e46d9259a04c5a3"
SERVICIO_FIRMANTE = "btp-firmante-ed25519"
V_PERMISO = 2
ALG = "ed25519"
# Costuras de los tests, SOLO en proceso (nunca por entorno, ver `RAIZ_TRANSCRIPTS_TEST`):
# `PUBLICA_TEST` es la pública de un firmante de prueba; `MODO_TEST` fuerza «hmac» o «ed25519»
# para que las baterías de cada modo no dependan de si `PUBLICA` ya está fijada.
PUBLICA_TEST = None
MODO_TEST = None


def publica():
    """La clave pública contra la que se comprueba, en hex, o None."""
    return PUBLICA_TEST or PUBLICA


def modo():
    """«ed25519» si hay clave pública fijada; si no, «hmac» (lo de antes)."""
    if MODO_TEST in ("hmac", ALG):
        return MODO_TEST
    return ALG if publica() else "hmac"


def kid(pub_hex):
    """Huella corta de una clave pública: va en el permiso para poder decir «lo firmó otra»."""
    return hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:16]


def _canon2(d):
    return json.dumps({k: v for k, v in d.items() if k != "firma"}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def cripto():
    """(módulo ed25519 | None, motivo). La librería se importa AQUÍ y no arriba: el `python3`
    de /usr/bin no la trae y este módulo lo importan también cosas que no firman nada. Si
    falta, quien comprueba DENIEGA con este motivo; no hay plan B que dé por buena una firma."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        return ed25519, ""
    except Exception:
        return None, ("a este Python (%s) le falta la librería `cryptography`: no puedo comprobar "
                      "la firma del permiso. Los hooks tienen que correr con el python3 de "
                      "Homebrew (/opt/homebrew/bin/python3)" % sys.executable)


def relanzar_si_falta_cripto():
    """Para los HOOKS, antes de leer su entrada. Se lanzan por su shebang (`env python3`) y qué
    python3 es ese lo decide el PATH que les dé el harness, que no está comprobado en todas las
    formas de sesión. Si en modo Ed25519 a este intérprete le falta `cryptography` y el de
    Homebrew existe, el hook se vuelve a lanzar con ese en vez de denegarlo todo. Una sola vez
    (`BTP_SIN_RELANZAR` lo corta, y solo sirve para cortar: fail-closed)."""
    if modo() != ALG or cripto()[0] or os.environ.get("BTP_SIN_RELANZAR"):
        return
    py = "/opt/homebrew/bin/python3"
    if os.path.exists(py) and os.path.realpath(py) != os.path.realpath(sys.executable):
        os.environ["BTP_SIN_RELANZAR"] = "1"
        os.execv(py, [py] + sys.argv)


def verificar(d):
    """"" si `d` lleva una firma Ed25519 válida de la clave pública fijada; si no, el motivo."""
    pub = publica()
    if not pub:
        return "no hay clave pública del firmante fijada"
    if not isinstance(d, dict) or d.get("v") != V_PERMISO or d.get("alg") != ALG \
            or not isinstance(d.get("firma"), str):
        return "el permiso no es del formato que firma el firmante (¿de una versión anterior?)"
    ed, motivo = cripto()
    if not ed:
        return motivo
    try:
        if d.get("kid") != kid(pub):
            return "el permiso lo firmó otra clave, no la del firmante"
        ed.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(
            bytes.fromhex(d["firma"]), _canon2(d))
    except Exception:
        return "el permiso no lleva la firma del firmante (escrito a mano, retocado o de otra clave)"
    return ""


def firma_ok(d, k=None):
    """"" si la firma de `d` vale en el modo vigente (Ed25519 con la pública, o el HMAC de antes
    con `k`); si no, el motivo."""
    if modo() == ALG:
        return verificar(d)
    return "" if _mac_ok(d, k) else "el permiso no lleva la firma del hook que lee su mensaje"


def llave(permitir_env=False):
    """(lo que hace falta para COMPROBAR un permiso | None, motivo). En modo Ed25519 no hay
    secreto que leer: basta la pública y la librería. En modo HMAC es la clave del Llavero."""
    if modo() == ALG:
        if not publica():
            return None, "no hay clave pública del firmante fijada"
        ed, motivo = cripto()
        return (ALG, "") if ed else (None, motivo)
    k, estado = clave_estado(permitir_env)
    if k:
        return k, ""
    return None, ("no hay clave de firma en el Llavero" if estado == "no-existe" else
                  "la clave de firma existe pero esta sesión no puede leerla (Llavero bloqueado "
                  "o sesión remota por SSH)")


def ts_de_linea(valor):
    """La hora de una línea del transcript (`2026-10-13T10:00:00.123Z`, UTC), como la guarda un
    permiso: ISO con zona y con sus milisegundos (identifica la línea: dos mensajes suyos no
    comparten hora). None si no se entiende."""
    try:
        t = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except Exception:
        return None
    if t.tzinfo is None:
        return None
    from datetime import timezone
    return t.astimezone(timezone.utc).isoformat()


def edad(ts):
    """Segundos desde `ts`, o None si no se entiende. Los permisos del firmante llevan la hora
    con zona (UTC, la de la línea del transcript); los del HMAC, hora local sin zona."""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if t.tzinfo is None:
        return (datetime.now() - t).total_seconds()
    from datetime import timezone
    return (datetime.now(timezone.utc) - t).total_seconds()


def _canon(d):
    return json.dumps({k: v for k, v in d.items() if k != "mac"}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def firmar(d, k):
    d = dict(d)
    d["mac"] = hmac.new(k, _canon(d), hashlib.sha256).hexdigest()
    return d


def _mac_ok(d, k):
    return (isinstance(d.get("mac"), str) and k is not None
            and hmac.compare_digest(d["mac"], hmac.new(k, _canon(d), hashlib.sha256).hexdigest()))


def _escribir(d):
    ruta = token_path(d.get("session_id"))
    os.makedirs(os.path.dirname(ruta), mode=0o700, exist_ok=True)
    tmp = ruta + ".%d.tmp" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, ruta)


# ── emitir (solo ok_envio_prompt.py) ─────────────────────────────────────────────────────────
def emitir(prompt, session_id, prompt_id, transcript_path, k):
    d = {"ts": datetime.now().replace(microsecond=0).isoformat(),
         "origen": "prompt",
         "motivo": re.sub(r"\s+", " ", prompt).strip()[:200],
         "hash_prompt": hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest(),
         "session_id": session_id or "", "prompt_id": prompt_id or "",
         "transcript_path": transcript_path or "",
         "nonce": secrets.token_hex(8), "usos": 0}
    _escribir(firmar(d, k))
    _barrer_caducados(k)
    return d


def _barrer_caducados(k):
    """Al emitir, se van los de otras sesiones que ya caducaron, y el hueco único de antes SOLO si
    ya no vale. Mientras haya sesiones con los hooks de antes (su worktree nació antes del
    26-sep-26), su permiso vive en `ok_envio.json`; borrarlo a ciegas era repetir el fallo que esto
    arregla: la orden de una sesión borrando la de otra (visto el mismo 26-sep, 17:33)."""
    for ruta in [_legado()] + _todos():
        _leer_ruta(ruta, k)          # leer uno caducado o sin firma ya lo borra


# ── leer y comprobar ─────────────────────────────────────────────────────────────────────────
def borrar(sesion=""):
    _borrar_ruta(token_path(sesion))


# Consultar no destruye (17-oct-26, deuda `ok-envio-caduca-antes-de-usar`): `ok_envio.py --estado`
# llamaba a `validar` sin sesión, que toma el permiso más reciente de CUALQUIER sesión y lo borra si
# no confirma; una consulta podía llevarse el permiso de otra sesión. Con esto a True no se borra
# nada. Ponerlo no abre nada: un permiso caducado, gastado o falso sigue sin valer, solo se queda
# en disco hasta que lo barra quien lo use.
SOLO_LECTURA = False


def _borrar_ruta(ruta):
    if SOLO_LECTURA:
        return
    try:
        os.remove(ruta)
    except Exception:
        pass


def leer(k, sesion=None):
    """(dict|None, motivo). Con `sesion`, SOLO el fichero de esa sesión. Sin ella (una tool que no
    la conoce, como web_novedad), el más reciente de los válidos: lo que hacía el hueco único."""
    if sesion is not None:
        return _leer_ruta(token_path(sesion), k)
    mejor, motivo = None, "nadie lo ha pedido en un mensaje"
    for ruta in _todos():
        d, m = _leer_ruta(ruta, k)
        if d and (mejor is None or d.get("ts", "") > mejor.get("ts", "")):
            mejor = d
        elif not d and mejor is None:
            motivo = m
    return mejor, ("" if mejor else motivo)


def _leer_ruta(ruta, k):
    """(dict|None, motivo). Firma, origen y caducidad de UN fichero. No mira el transcript."""
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, "nadie lo ha pedido en un mensaje"
    except Exception:
        _borrar_ruta(ruta)
        return None, "permiso ilegible"
    if not isinstance(d, dict):
        _borrar_ruta(ruta)
        return None, "permiso ilegible"
    if modo() == ALG and not cripto()[0]:
        # Falta la librería en ESTE intérprete: no se puede comprobar, así que no vale; pero el
        # permiso no se borra, porque el fallo es del entorno y no del permiso.
        return None, cripto()[1]
    pega = firma_ok(d, k)
    if pega:
        # Escrito a mano, con otra clave o retocado: no vale y no se deja ahí para reintentar.
        _borrar_ruta(ruta)
        return None, pega
    if d.get("origen") not in ORIGENES:
        _borrar_ruta(ruta)
        return None, "el permiso no nació de un mensaje suyo (origen %r)" % d.get("origen")
    if modo() == ALG and d.get("tipo") != "uso":
        # El firmante firma SIEMPRE qué es lo que firma. Un `.lote2` copiado a `.ok2` llevaba una
        # firma buena y valía como permiso general de un uso (verificacion, 14-oct-26).
        _borrar_ruta(ruta)
        return None, "ese permiso no es de un solo uso (es de otro tipo: %r)" % d.get("tipo")
    seg = edad(d.get("ts"))
    if seg is None:
        _borrar_ruta(ruta)
        return None, "permiso ilegible"
    if seg > VIDA_S or seg < -60:
        _borrar_ruta(ruta)
        return None, "el permiso caducó"
    return d, ""


# ── el libro de usos ─────────────────────────────────────────────────────────────────────────
# TODO lo que se gasta de un permiso vive aquí (14-oct-26), no en el propio permiso:
#   · línea FINAL (sin `parcial`): ese mensaje suyo ya no abre nada más;
#   · línea `parcial`: un uso de los varios que admite (web_novedad) o un ítem de un lote.
# Todo se cuenta por (sesión, mensaje suyo), nunca por emisión del permiso: pedir otra vez la
# firma del mismo mensaje no devuelve ni un uso ni un ítem.
# Antes el contador iba DENTRO del permiso y había que refirmarlo en cada uso: quien lo usaba
# necesitaba la clave de firma, y desde una sesión remota no la hay. Además se miraba «¿usado?»
# y se apuntaba después, sin cerrojo y reescribiendo el fichero: dos usos a la vez pasaban los
# dos. Ahora mirar y apuntar es UNA operación bajo `flock`.
MAX_LIBRO = 500


def _linea(d):
    """La LÍNEA del transcript a la que apunta un permiso del firmante: su hora, que firma el
    daemon y que quien valida coteja con el transcript. None en los permisos HMAC (ahí `ts` es
    la hora de emisión)."""
    return d.get("ts") if (d.get("v") == V_PERMISO and d.get("ts")) else None


def _mia(u, d):
    """¿Ese apunte del libro es del mismo mensaje suyo que `d`? Por `prompt_id`, y además por
    LÍNEA (sesión + hora de la línea). Un mensaje ENCOLADO se localiza por el hash de su texto y
    el `prompt_id` lo pone quien llama: sin la línea, tres ids inventados para un solo «Fusiona»
    daban tres permisos y tres apuntes distintos (verificacion, 14-oct-26)."""
    if not isinstance(u, dict) or u.get("session_id") != d.get("session_id"):
        return False
    if u.get("prompt_id") == d.get("prompt_id"):
        return True
    return bool(_linea(d)) and u.get("linea") == _linea(d)


def _con_libro(fn, escribir=True):
    """Corre `fn(entradas)` con el libro bajo cerrojo. `fn` devuelve (resultado, nuevas|None);
    las nuevas se añaden antes de soltarlo. Se reescribe EN SITIO, sin renombrar: un `rename`
    dejaría a quien espera el cerrojo esperando sobre un fichero que ya no es el libro."""
    import fcntl
    ruta = usados_path()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    fd = os.open(ruta, (os.O_RDWR | os.O_CREAT) if escribir else os.O_RDONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if escribir else fcntl.LOCK_SH)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8", errors="replace") as f:
            lineas = f.read().splitlines()
        entradas = []
        for linea in lineas:
            try:
                entradas.append(json.loads(linea))
            except Exception:
                continue
        resultado, nuevas = fn(entradas)
        if nuevas and escribir:
            lineas = (lineas + [json.dumps(n, ensure_ascii=False) for n in nuevas])[-MAX_LIBRO:]
            datos = ("\n".join(lineas) + "\n").encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, datos)
            os.fsync(fd)
        return resultado
    finally:
        os.close(fd)            # cerrar suelta el cerrojo


def _apunte(d, por, **mas):
    a = {"ts": datetime.now().replace(microsecond=0).isoformat(),
         "session_id": d.get("session_id"), "prompt_id": d.get("prompt_id"),
         "nonce": d.get("nonce"), "por": por}
    a.update(mas)
    if _linea(d):
        a["linea"] = _linea(d)
    return a


def _usado(d):
    """¿Ese mensaje suyo ya está gastado del todo?"""
    try:
        return _con_libro(lambda es: (any(_mia(u, d) and not u.get("parcial") for u in es), None),
                          escribir=False)
    except FileNotFoundError:
        return False


def consumir(d, por=""):
    """Gasta el permiso entero. True si lo gasta ESTA llamada; False si ya estaba gastado.
    Atómico: de dos llamadas a la vez, una y solo una recibe True."""
    def _f(es):
        if any(_mia(u, d) and not u.get("parcial") for u in es):
            return False, None
        return True, [_apunte(d, por)]
    hecho = _con_libro(_f)
    borrar(d.get("session_id"))
    return hecho


def marcar_usado(d, por=""):
    """Lo deja gastado, lo estuviera ya o no. Quien necesite saber si el uso es suyo (antes de
    dejar salir algo), que llame a `consumir`."""
    consumir(d, por)


def gastar_item(d, item, total, por="lote"):
    """Un ítem de un lote. True si lo gasta ESTA llamada. Con el último, el lote queda gastado
    del todo. Lo gastado se cuenta por (sesión, mensaje) y NO por emisión: si el firmante
    volviera a emitir el lote de ese mismo mensaje (otro `nonce`), lo ya enviado sigue enviado."""
    def _f(es):
        mias = [u for u in es if _mia(u, d)]
        if any(not u.get("parcial") for u in mias):
            return False, None
        gastados = {u.get("item") for u in mias if u.get("parcial") and u.get("item")}
        if item in gastados:
            return False, None
        nuevas = [_apunte(d, por, parcial=True, item=item)]
        if len(gastados) + 1 >= total:
            nuevas.append(_apunte(d, por))
        return True, nuevas
    return _con_libro(_f)


def items_gastados(d):
    """Las huellas ya gastadas de ese lote."""
    try:
        return _con_libro(lambda es: ({u.get("item") for u in es if _mia(u, d) and u.get("parcial")
                                       and u.get("item")},
                                      None), escribir=False)
    except FileNotFoundError:
        return set()


def _crudo(entrada):
    c = (entrada.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c if isinstance(c, str) else None


def texto_prompt(entrada):
    """Texto del mensaje de usuario, sin los <system-reminder> que el harness antepone."""
    c = _crudo(entrada)
    if c is None:
        return None
    # Se quitan TODOS los bloques inyectados, no solo los del principio (24-sep-26): una orden
    # pegada al FINAL por un hook colaba igual, y el test la reprodujo.
    return solo_suyo(c)


def _es_humano(e):
    """Prompt TECLEADO por una persona (mismo criterio que healthcheck, censo del 11-sep-26).
    El prompt de una tarea programada llega con origin human: se descarta por su texto CRUDO."""
    if e.get("type") != "user" or e.get("origin") != {"kind": "human"} or e.get("isSidechain") is not False:
        return False
    crudo = _crudo(e)
    return crudo is not None and not es_automatico(crudo)


def _es_humano_encolado(e):
    """Mensaje que ELLA escribió mientras el agente trabajaba (27-sep-26).

    Claude Code no lo guarda como prompt (`type: user`) sino como `attachment` de tipo
    `queued_command`, sin `promptId`. Sin esto, su «Fusiona» a mitad de turno no contaba y tenía
    que repetirlo (#247, #248). Solo vale el que Claude Code marca como humano: los mensajes de
    otros agentes llegan con `origin: {kind: peer}` y los avisos de tareas con `commandMode:
    task-notification`, y ninguno de los dos cuenta. Un JSON que imite esto dentro de un resultado
    de herramienta no es una línea `attachment` del transcript: tampoco cuenta."""
    a = e.get("attachment")
    if (e.get("type") != "attachment" or not isinstance(a, dict) or a.get("type") != "queued_command"
            or a.get("origin") != {"kind": "human"} or a.get("humanTurn") is not True
            or a.get("commandMode") != "prompt" or e.get("isSidechain") is not False):
        return False
    p = a.get("prompt")
    return isinstance(p, str) and not es_automatico(p)


def _hash_texto(t):
    return hashlib.sha256((t or "").encode("utf-8", "replace")).hexdigest()


def _cuerpo(e):
    b = e.get("body")
    if not isinstance(b, str):
        return None
    return hashlib.sha256("\n".join(l.rstrip() for l in b.replace("\r\n", "\n").strip().split("\n"))
                          .encode("utf-8")).hexdigest()


def _emails(valor):
    fuera = set()
    if isinstance(valor, str):
        fuera.update(m.lower() for m in EMAIL.findall(valor))
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            fuera |= _emails(v)
    elif isinstance(valor, dict):
        for v in valor.values():
            fuera |= _emails(v)
    return fuera


def destinatarios(entrada):
    """Direcciones a las que va una llamada (to/cc/bcc/recipient…), en minúsculas."""
    fuera = set()
    for c in _CAMPOS_DESTINO:
        if c in (entrada or {}):
            fuera |= _emails(entrada[c])
    return fuera


M_SIN_TRANSCRIPT = "no encuentro el transcript de la sesión"
M_NO_ESTA = "su mensaje no está en el transcript como prompt humano"

# Costura de los tests, SOLO en proceso (como `CLAVE_TEST`): nunca por entorno. Las tools que
# comprueban el permiso las lanza el agente con el entorno que él elija; si la raíz saliera de
# una variable (o de `HOME`, que es lo que mira `expanduser`), bastaría con apuntarla a una
# carpeta suya para que un transcript escrito a mano contase.
RAIZ_TRANSCRIPTS_TEST = None


def raiz_transcripts():
    """Donde Claude Code guarda los transcripts, en ruta REAL. La casa del usuario sale de la
    base de usuarios del sistema (`pwd`), no de `HOME`."""
    if RAIZ_TRANSCRIPTS_TEST:
        return os.path.realpath(RAIZ_TRANSCRIPTS_TEST)
    import pwd
    return os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude", "projects"))


def ruta_transcript(ruta, session_id):
    """(ruta real | None, motivo). El transcript que vale es el de ESA sesión y vive donde los
    escribe el harness (13-oct-26). Antes se abría la ruta que trajera el permiso, fuera la que
    fuera: con un emisor lanzado a mano (o cualquier vía que firmase un puntero), un fichero
    escrito en /tmp con una línea «humana» inventada servía de prueba, y /tmp no lo protege
    ningún guard. Ahora tiene que:
      · llamarse `<session_id>.jsonl` (el de otra sesión, o el de un subagente, no vale);
      · no ser un enlace simbólico, ni llegar por uno a un fichero con otro nombre;
      · estar, ya resuelto, dentro de `~/.claude/projects`."""
    sid = str(session_id or "")
    if not sid or _sid(sid) != sid:
        return None, "el permiso no dice de qué sesión es"
    nombre = sid + ".jsonl"
    ruta = str(ruta or "")
    if not os.path.isabs(ruta) or os.path.basename(ruta) != nombre:
        return None, "el transcript del permiso no es el de su sesión"
    if os.path.islink(ruta):
        return None, "el transcript del permiso es un enlace, no el fichero de la sesión"
    real = os.path.realpath(ruta)
    if os.path.basename(real) != nombre:
        return None, "el transcript del permiso no es el de su sesión"
    if not real.startswith(raiz_transcripts() + os.sep):
        return None, "el transcript del permiso no está donde Claude Code guarda los suyos"
    return real, ""


def transcripts_de(sesion):
    """Las rutas REALES de los transcripts con el nombre de esa sesión (`<raíz>/<proyecto>/
    <sesión>.jsonl`), cada una pasada por `ruta_transcript`. Puede haber más de una: el mismo id
    de sesión aparece a veces en dos carpetas de proyecto (26 casos medidos el 14-oct-26)."""
    raiz = raiz_transcripts()
    halladas = []
    try:
        proyectos = sorted(os.listdir(raiz))
    except Exception:
        return []
    for p in [""] + proyectos:
        cand = os.path.join(raiz, p, str(sesion) + ".jsonl") if p else os.path.join(raiz, str(sesion) + ".jsonl")
        if os.path.isfile(cand):
            real, _motivo = ruta_transcript(cand, sesion)
            if real:
                halladas.append(real)
    return sorted(set(halladas))


def confirma(d, transcript=None):
    """(ok, motivo, ctx): `contexto(d)` y además lo que hace falta para que aguante AUNQUE LA
    FIRMA NO PROTEGIERA (clave privada filtrada, daemon equivocado):
      · en modo Ed25519, la hora del permiso es la de su línea en el transcript;
      · si quien llama sabe cuál es el transcript de la sesión (`transcript`, el que da el
        harness al guard), la orden tiene que valer TAMBIÉN en ese, y ser la misma línea;
      · CUALQUIER otra copia `<sesión>.jsonl` que contenga la orden y la rechace, la ANULA. Sin
        esto, su «espera» dejaba de revocar si existía otra copia del transcript que no la
        tuviera: bastaba con firmar contra la copia vieja (verificacion, 14-oct-26)."""
    ok, motivo, ctx = contexto(d)
    if not ok:
        return False, motivo, dict()
    if modo() == ALG and d.get("ts") != ctx.get("ts_linea"):
        return False, "la hora del permiso no es la de su mensaje en el transcript", dict()
    sesion = str(d.get("session_id") or "")
    suyo = os.path.realpath(str(d.get("transcript_path") or ""))
    if transcript and os.path.basename(str(transcript)) == sesion + ".jsonl":
        # El daemon no sabe cuál de las copias es la que usa el harness; quien valida sí. La
        # orden tiene que valer TAMBIÉN ahí, y ser la misma línea (misma hora).
        real, _motivo = ruta_transcript(str(transcript), sesion)
        if real != suyo:
            ok3, m3, c3 = contexto(dict(d, transcript_path=real or str(transcript)))
            if not ok3:
                return False, "en el transcript de esta sesión la orden no vale: " + m3, dict()
            if c3.get("ts_linea") != ctx.get("ts_linea"):
                return False, "el permiso es de otra línea que la del transcript de esta sesión", dict()
            ctx = c3
    for otra in transcripts_de(sesion):
        if otra == suyo:
            continue
        ok2, m2, _c = contexto(dict(d, transcript_path=otra))
        if not ok2 and m2 not in (M_NO_ESTA, M_SIN_TRANSCRIPT):
            return False, "otra copia del transcript de la sesión anula la orden: " + m2, dict()
    return True, "", ctx


def contexto(d):
    """Lee el transcript al que apunta el permiso y devuelve (ok, motivo, ctx).

    ctx = {texto, emails (los que nombró), en_vista (borrador que tenía delante), hilos, drafts,
    cambios (borradores tocados DESPUÉS de su orden)}."""
    ruta = d.get("transcript_path") or ""
    if not ruta or not d.get("prompt_id"):
        return False, "el permiso no apunta a ningún mensaje suyo", {}
    ruta, motivo = ruta_transcript(ruta, d.get("session_id"))
    if not ruta:
        return False, motivo, {}
    try:
        f = open(ruta, encoding="utf-8")
    except Exception:
        return False, M_SIN_TRANSCRIPT, {}
    # Primero se recoge TODO en orden; después se elige cuál es su orden. Con los mensajes
    # encolados (sin promptId) la coincidencia se hace por el hash de su texto y puede aparecer
    # después de otro candidato: decidir sobre la marcha, como antes, se quedaba con el primero.
    eventos = []                    # ("h", entrada, texto) · ("t", texto mío) · ("d", borrador)
    with f:
        for linea in f:
            try:
                e = json.loads(linea)
            except Exception:
                continue
            if not isinstance(e, dict):
                continue
            if _es_humano(e):
                eventos.append(("h", e, texto_prompt(e) or ""))
                continue
            if _es_humano_encolado(e):
                eventos.append(("h", e, solo_suyo(e["attachment"]["prompt"]) or ""))
                continue
            # El TEXTO de una línea lateral (isSidechain) no es lo que ella tenía delante (9-oct-26),
            # pero un BORRADOR sí cuenta siempre: filtrarlo relajaría `cambios` (un update_draft
            # de un subagente tras su OK tiene que seguir frenando el envío).
            if e.get("type") == "assistant":
                lateral = bool(e.get("isSidechain"))
                for b in (e.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and not lateral:
                        eventos.append(("t", b.get("text") or ""))
                    if (isinstance(b, dict) and b.get("type") == "tool_use"
                            and _DRAFT.search(b.get("name", "")) and isinstance(b.get("input"), dict)):
                        eventos.append(("d", b["input"]))
    hash_suyo = d.get("hash_prompt") or ""

    def _es_el_suyo(ev):
        e = ev[1]
        if e.get("type") == "user":
            return e.get("promptId") == d["prompt_id"]
        crudo = e["attachment"]["prompt"]
        return bool(hash_suyo) and hash_suyo in (_hash_texto(crudo), _hash_texto(crudo.strip()))

    humanos = [i for i, ev in enumerate(eventos) if ev[0] == "h"]
    candidatos = [i for i in humanos if _es_el_suyo(eventos[i])]
    if not candidatos:
        return False, M_NO_ESTA, {}
    i_suyo = candidatos[-1]
    texto = eventos[i_suyo][2]
    if humanos[-1] != i_suyo:
        return False, "después de esa orden escribió otra cosa", {}
    encolado = eventos[i_suyo][1].get("type") == "attachment"
    antes = [i for i in humanos if i < i_suyo]
    previo = antes[-1] if antes else -1
    alc, propuesta = alcance(texto), None
    if not alc and not encolado:
        # Un «sí» suyo vale solo para la propuesta ÚNICA de mi último turno (ver ASENTIMIENTO).
        # Encolado no: pudo escribirlo antes de que la propuesta apareciera.
        alc, propuesta = alcance_por_propuesta(
            texto, [ev[1] for ev in eventos[previo + 1:i_suyo] if ev[0] == "t"])
    if not alc:
        return False, "su mensaje no contiene una orden de envío", {}
    if encolado and len(antes) >= 1:
        # Encolado a mitad de turno: el turno lo abrió OTRO mensaje suyo, y lo que ella tenía
        # delante al escribir la orden es también mi respuesta ANTERIOR a ese mensaje (ahí estaban
        # el PR y su SHA en el caso real, #247 y #248). Se amplía una vuelta, no más.
        previo = antes[-2] if len(antes) >= 2 else -1
    visto = [ev[1] for ev in eventos[previo + 1:i_suyo] if ev[0] == "t"]   # lo que tenía delante
    drafts_antes, cambios, nh = [], [], 0
    for i, ev in enumerate(eventos):
        if ev[0] == "h":
            nh += 1
        elif ev[0] == "d":
            (cambios if i > i_suyo else drafts_antes).append((nh, ev[1]))
    n_suyo = len(humanos)            # su orden es la última: su índice (1-based) entre los humanos
    hilos, drafts, en_vista = {}, {}, None
    for n, e in drafts_antes:
        info = {"cuerpo": _cuerpo(e), "destinos": destinatarios(e)}
        if e.get("replyToMessageId"):
            hilos[e["replyToMessageId"]] = info
        if e.get("draftId"):
            drafts[e["draftId"]] = info
        if n == n_suyo - 1:          # hecho en la vuelta a la que ella contesta: lo tenía delante
            en_vista = info
    e_suyo = eventos[i_suyo][1]
    crudo = (_crudo(e_suyo) if e_suyo.get("type") == "user" else e_suyo["attachment"]["prompt"]) or ""
    if propuesta:
        # Un «sí» aprueba LA PROPUESTA, no todo lo que hubiera en pantalla: el PR, el SHA, las
        # direcciones y el FILE_ID salen solo de su línea.
        obj = propuesta["objeto"]
        return True, "", {"texto": texto, "emails": {m.lower() for m in EMAIL.findall(obj)},
                          "ts_linea": ts_de_linea(e_suyo.get("timestamp")), "crudo": crudo,
                          "en_vista": en_vista, "hilos": hilos, "drafts": drafts,
                          "cambios": [e for _, e in cambios], "alcance": alc, "propuesta": propuesta,
                          "visto_textos": [obj], "prs_suyos": set(), "prs_vistos": _prs(obj),
                          "shas_vistos": _shas(obj)}
    return True, "", {"texto": texto, "emails": {m.lower() for m in EMAIL.findall(texto)},
                      # La hora de SU línea (la pone el harness) y el texto tal cual llegó: lo que
                      # usa el firmante para fechar el permiso y decidir si la orden es de lote.
                      "ts_linea": ts_de_linea(e_suyo.get("timestamp")), "crudo": crudo,
                      "en_vista": en_vista, "hilos": hilos, "drafts": drafts,
                      "cambios": [e for _, e in cambios], "alcance": alc, "propuesta": propuesta,
                      "visto_textos": visto,
                      "prs_suyos": _prs(texto), "prs_vistos": _prs("\n".join(visto)),
                      "shas_vistos": _shas(texto + "\n" + "\n".join(visto))}


# Números de PR en un texto: «#224», «PR 224», «el 224», «.../pull/224».
_PR_EN_TEXTO = re.compile(r"(?:#|\bPRs?\s*#?\s*|\bel\s+|/pull/)(\d{1,6})\b", re.I)
# El PR de un `gh pr merge`: el primer argumento numérico o la URL …/pull/N.
_MERGE = re.compile(r"\bgh\s+pr\s+merge\b(?P<resto>[^;&|\n]*)")


def _prs(texto):
    return {int(n) for n in _PR_EN_TEXTO.findall(texto or "")}


# Commits en un texto: 7 a 40 hex, como git los abrevia. En minúsculas para comparar.
_SHA_EN_TEXTO = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{7,40}(?![0-9A-Za-z])")
# `--match-head-commit X` o `--match-head-commit=X` dentro del `gh pr merge`.
_MATCH_HEAD = re.compile(r"--match-head-commit(?:\s+|=)['\"]?([0-9a-fA-F]+)")


def _shas(texto):
    return {x.lower() for x in _SHA_EN_TEXTO.findall(texto or "")}


def sha_de_merge(comando):
    """El SHA de `--match-head-commit` de un `gh pr merge`, en minúsculas, o None."""
    m = _MERGE.search(comando or "")
    if not m:
        return None
    h = _MATCH_HEAD.search(m.group("resto"))
    return h.group(1).lower() if h else None


def pr_de_merge(comando):
    """(es_merge, número|None) de una orden Bash."""
    m = _MERGE.search(comando or "")
    if not m:
        return False, None
    for tok in m.group("resto").split():
        u = re.search(r"/pull/(\d+)", tok)
        if u:
            return True, int(u.group(1))
        if tok.isdigit():
            return True, int(tok)
    return True, None


def comprobar_fusion(ctx, numero, sha=None):
    """"" si el merge de `numero` es el PR del que ella hablaba; si no, el motivo. Candado: si su
    mensaje nombra PRs, tiene que ser uno de esos; si no nombra ninguno, el que yo le había puesto
    delante (mi último mensaje antes del suyo), igual que `en_vista` en los borradores. Y además
    tiene que ser el CONTENIDO que vio (`_comprobar_contenido`)."""
    if numero is None:
        return "el merge no dice qué PR: no puedo comprobar que sea el que ella aprobó"
    suyos = ctx.get("prs_suyos") or set()
    if suyos:
        if numero not in suyos:
            return "ella dijo el PR %s, no el %s" % (", ".join(str(n) for n in sorted(suyos)), numero)
    elif numero not in (ctx.get("prs_vistos") or set()):
        return "el PR %s no estaba en lo que ella tenía delante al decirlo" % numero
    return _comprobar_contenido(ctx, numero, sha)


def comprobar_push(ctx, shas):
    """P3 · F2b (25-sep-26): un `git push` que publica sube EXACTAMENTE commits que ella vio.

    Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
    `shas` los resuelve `salida_guard` en el repo del push (vacío = no se pudo saber qué sube:
    --all, --tags, borrar una rama, un ref que no existe). Cada uno tiene que estar, con 7+
    caracteres, en lo que ella tenía delante o en su mensaje. Si después de su OK se hace otro
    commit, la cabeza cambia y el push ya no pasa: tiene que ver el nuevo."""
    como = "enséñale el commit que sube (`git log -1 --format='%h %s' <rama>`) y pide su OK sobre ese"
    if not shas:
        return "no sé qué commit sube este push, así que no puedo atarlo a lo que ella vio; " + como
    vistos = ctx.get("shas_vistos") or set()
    for sha in shas:
        if not any(len(v) >= 7 and sha.startswith(v) for v in vistos):
            return "el push sube %s, que no estaba en lo que ella tenía delante al decirlo; %s" % (
                sha[:12], como)
    return ""


def _comprobar_contenido(ctx, numero, sha):
    """P3 · F2 (25-sep-26): la firma va atada al CONTENIDO, no solo al número del PR.

    Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
    Antes, un «fusiona» sobre el #N dejaba fusionar el #N aunque alguien le hubiera empujado
    commits DESPUÉS de que ella lo viera. Ahora el merge tiene que llevar `--match-head-commit`
    con el SHA completo de la cabeza, y ese SHA (7+ caracteres bastan) tiene que estar en lo que
    ella tenía delante o en su mensaje. GitHub hace el resto en el servidor: si la cabeza ya no es
    esa, rechaza el merge. Así «lo firmo yo» sigue significando «firmo ESTO que he visto»."""
    como = ("enséñale el SHA de la cabeza (`gh pr view %s --json headRefOid`) y usa "
            "`gh pr merge %s --match-head-commit <SHA>`" % (numero, numero))
    if not sha:
        return "falta `--match-head-commit`: la firma va atada al contenido que ella vio; " + como
    if len(sha) != 40:
        return "`--match-head-commit` necesita el SHA completo (40 caracteres), no %d" % len(sha)
    vistos = ctx.get("shas_vistos") or set()
    if not any(len(v) >= 7 and sha.startswith(v) for v in vistos):
        return "el commit %s no estaba en lo que ella tenía delante al decirlo; %s" % (sha[:12], como)
    return ""


def comprobar_envio(ctx, entrada, tool=""):
    """"" si la llamada casa con lo que ella aprobó; si no, el motivo. Con `tool`, además, un
    permiso que solo abrió «programar» no deja pasar nada que no sea una tarea programada, y uno que
    solo abrió «fusionar», nada que no sea el `gh pr merge` del PR del que se hablaba."""
    entrada = entrada or {}
    es_merge, numero = (pr_de_merge(entrada.get("command"))
                        if (tool or "") == "Bash" else (False, None))
    es_pr = (tool or "") == "Bash" and not es_merge and bool(_PR_ABRE.search(entrada.get("command") or ""))
    que = ("programar" if PROGRAMAN.search(tool or "")
           else "fusionar" if es_merge else "pr" if es_pr
           else "push" if "_push_shas" in entrada else "envio")
    if tool and not permite(ctx, que):
        a = (ctx or {}).get("alcance") or set()
        if que == "envio":
            pidio = " y ".join({"programar": "programar", "fusionar": "fusionar", "pr": "abrir un PR",
                                "push": "subir commits", "drive": "compartir en Drive"}[x]
                               for x in ("programar", "fusionar", "pr", "push", "drive") if x in a
                               ) or "otra cosa"
            return "ella pidió %s, no enviar ni publicar" % pidio
        return ("su orden no cubre programar una tarea" if que == "programar"
                else "su orden no cubre abrir ni editar un PR" if que == "pr"
                else "su orden no cubre subir commits (push)" if que == "push"
                else "su orden no cubre fusionar un PR")
    if que == "fusionar":
        return comprobar_fusion(ctx, numero, sha_de_merge(entrada.get("command")))
    if que == "pr":
        return ""        # abrir o editar un PR no publica: el merge sigue con su propio candado
    if "_push_shas" in entrada:
        return comprobar_push(ctx, entrada.get("_push_shas"))
    destinos = destinatarios(entrada)
    prop = (ctx or {}).get("propuesta")
    if prop:
        # Un «sí» a una propuesta ata la salida a SU objeto (verificacion, 17-oct-26): `enviar`
        # solo a las direcciones de la línea; `publicar` no manda nada a nadie por correo.
        if prop["tipo"] == "publicar":
            # Publicar en su web va por `web_novedad` / el merge del PR, que tienen su candado.
            # Por aquí (un correo, un POST, un MCP que escribe fuera) un «sí» a «publicar» no
            # abre nada: no hay objeto que ate a qué host o a quién (verificacion, 17-oct-26).
            return ("ella aprobó publicar, no esta salida (%s): publicar va por web_novedad o por "
                    "el merge del PR" % (tool or "?"))
        if prop["tipo"] == "enviar" and (not destinos or destinos - ctx.get("emails", set())):
            return "ella aprobó enviar a %s, no a %s" % (
                ", ".join(sorted(ctx.get("emails", set()))) or "nadie",
                ", ".join(sorted(destinos)) or "un destino que no se ve en la llamada")
    if ctx.get("emails") and destinos - ctx["emails"]:
        return "va a %s y ella nombró %s" % (", ".join(sorted(destinos - ctx["emails"])),
                                             ", ".join(sorted(ctx["emails"])))
    cuerpo = _cuerpo(entrada)
    did = entrada.get("draftId")
    hilo = entrada.get("messageId") or entrada.get("replyToMessageId") or entrada.get("threadId")
    for c in ctx.get("cambios", []):
        if (did and c.get("draftId") == did) or (hilo and c.get("replyToMessageId") == hilo):
            return "el borrador cambió después de su OK: tiene que ver la versión nueva"
    aprobado = (ctx.get("drafts", {}).get(did) if did else None) or \
               (ctx.get("hilos", {}).get(hilo) if hilo else None) or ctx.get("en_vista")
    if aprobado:
        if cuerpo and aprobado["cuerpo"] and cuerpo != aprobado["cuerpo"]:
            return "el texto que sale no es el del borrador que ella vio"
        if destinos and aprobado["destinos"] and destinos - aprobado["destinos"] - ctx.get("emails", set()):
            return "va a %s, que no estaba en el borrador que ella vio" % ", ".join(
                sorted(destinos - aprobado["destinos"]))
    return ""


# Un FILE_ID de Drive: 25+ caracteres de [A-Za-z0-9_-] (los de hoy tienen 33).
_ID_DRIVE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{25,}(?![A-Za-z0-9_-])")


def comprobar_drive(ctx, file_id):
    """"" si compartir `file_id` con enlace es lo que ella aprobó; si no, el motivo (17-oct-26).
    El alcance tiene que cubrir `drive` y el FILE_ID tiene que estar, ENTERO, en lo que ella
    aprobó: la línea de propuesta si contestó con un «sí», o su texto y lo que tenía delante si
    dio la orden con palabras."""
    if not permite(ctx, "drive"):
        return "su orden no cubre compartir un fichero de Drive"
    fid = str(file_id or "")
    if not _ID_DRIVE.fullmatch(fid):
        return "eso no parece el FILE_ID de un fichero de Drive"
    donde = [(ctx or {}).get("texto") or ""] + list((ctx or {}).get("visto_textos") or [])
    if not any(fid in _ID_DRIVE.findall(t) for t in donde):
        return ("el fichero %s no estaba en lo que ella aprobó; escribe la línea "
                "«🔐 Propuesta de salida: compartir-drive · %s · cualquiera con el enlace, lector» "
                "y pídele un sí" % (fid, fid))
    return ""


def validar(k, sesion=None, transcript=None):
    """(dict|None, motivo, ctx). Todo menos lo específico de la llamada. `sesion` = la del
    PreToolUse; None cuando lo comprueba una tool (web_novedad), que no la conoce.
    `transcript` = el `transcript_path` que da el harness al guard (ver `confirma`)."""
    d, motivo = leer(k, sesion)
    if not d:
        if sesion is not None and motivo == "nadie lo ha pedido en un mensaje":
            # Su mensaje AQUÍ no abrió ninguno (si otra sesión tiene el suyo, ese no se toca):
            # hay que decirle qué frase lo abre.
            motivo = ("su mensaje en esta sesión no abrió permiso. "
                      "Lo abren: «publícalo», «envíalo», «fusiónalo», «prográmalo», «abre el PR», o un «sí» "
                      "a una línea «🔐 Propuesta de salida: <tipo> · <objeto>» de tu último mensaje")
        return None, motivo, {}
    if sesion is not None and sesion != d.get("session_id"):
        # Segunda red: el fichero de MI sesión con el session_id de otra (copiado o renombrado a
        # mano) no vale. La firma cubre session_id, así que renombrar no hereda su permiso.
        borrar(sesion)
        return None, ("su mensaje en esta sesión no abrió permiso (el que hay es de otra sesión). "
                      "Lo abren: «publícalo», «envíalo», «fusiónalo», «prográmalo», «abre el PR», o un «sí» "
                      "a una línea «🔐 Propuesta de salida: <tipo> · <objeto>» de tu último mensaje"), {}
    if _usado(d):
        borrar(d.get("session_id"))
        return None, "ese mensaje suyo ya abrió un envío", {}
    ok, motivo, ctx = confirma(d, transcript)
    if not ok:
        borrar(d.get("session_id"))
        return None, motivo, {}
    return d, "", ctx


def gastar_uno(d, k, maximo):
    """Para web_novedad: su petición puede traer hasta `maximo` entradas. El contador vive en el
    libro (ver «el libro de usos»): retocar el permiso no devuelve usos, y no hace falta la clave
    para apuntarlos (`k` se conserva por compatibilidad y no se usa). Lanza `PermissionError` si
    ya estaba gastado: quien llama no deja salir nada."""
    def _f(es):
        mias = [u for u in es if _mia(u, d)]
        if any(not u.get("parcial") for u in mias):
            return 0, None
        n = 1 + sum(1 for u in mias if u.get("parcial") and not u.get("item")
                   )
        return n, [_apunte(d, "web_novedad", parcial=n < maximo, uso=n)]
    n = _con_libro(_f)
    if not n:
        borrar(d.get("session_id"))
        raise PermissionError("ese mensaje suyo ya abrió todo lo que podía abrir")
    if n >= maximo:
        borrar(d.get("session_id"))
    d = dict(d)
    d["usos"] = n
    return d
