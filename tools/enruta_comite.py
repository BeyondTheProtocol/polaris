#!/usr/bin/env python3
"""tools/enruta_comite.py — la centralita de COMITÉS. Decide QUIÉN analiza, no con qué modelo.

EL AGUJERO QUE TAPA (5-sep-2026, y lo encontró {{TITULAR}}, no el sistema):
  Una sesión entró por «ayúdame a interpretar esta imagen», creció hasta ser el análisis exhaustivo
  del FoundationOne de la metástasis ósea (el nodo ⭐NED del Tablero) y lo hizo un solo LLM a pelo:
  sin `comite-medico`, sin `verificacion`, sin cotejar una cita. Nada saltó. Su pregunta fue:
  *«¿por qué no toca el comité médico esto, si principalmente he hecho Polaris para esto?»*.

  La causa NO fue el olvido. Fue que **nadie decide quién analiza**:
    · `enruta.py` decide MODELO (Claude / local / Grok). Cero menciones a comités en sus 561 líneas.
    · `orquestadores.json` decide qué BINARIO arranca el gabinete.
    · `audit_comites.py` AUDITA los comités a posteriori (y ya midió el 30-jul que 13 de 35 tenían
      cero invocaciones), pero no enruta nada.
  Faltaba la pieza de en medio. Esto es esa pieza.

QUÉ HACE:
  Dada una intención en texto, devuelve qué comité(s) deberían tocarla, con qué nivel y por qué.
  DETERMINISTA: sin LLM, sin red, sin estado. La misma frase da siempre la misma decisión, para
  que se pueda testear y para que no cueste un token.

QUÉ NO HACE (a propósito):
  · NO invoca a nadie. Devuelve una RECOMENDACIÓN. Quien invoca es la sesión, y el coste es una
    decisión consciente (regla de {{TITULAR}} 21/6/26: gastar se elige, no es un reflejo).
  · NO sustituye al muro. `clinico_guard` y `muro_guard` siguen mandando sobre esto.
  · NO adivina intenciones sutiles. Cubre las señales gruesas, que son las que se escapaban.

Uso:
  python3 tools/enruta_comite.py "analiza el informe del panel molecular de hueso"
  python3 tools/enruta_comite.py "..." --json
  python3 tools/enruta_comite.py "..." --exigir     # exit 2 si hay un comité OBLIGATORIO

Exit: 0 normal · 2 solo con --exigir y comité obligatorio · nunca revienta una sesión.
"""
import json
import os
import re
import sys

REPO = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_AGENTES = os.path.join(REPO, ".claude", "agents")

# ── Niveles ───────────────────────────────────────────────────────────────────────────────────
# critico  → el comité es OBLIGATORIO; hacerlo a pelo es el fallo del 5-sep.
# alto     → recomendado fuerte; saltárselo se justifica en una línea.
# rutina   → sugerencia; lo normal es que lo haga la sesión sola.
CRITICO, ALTO, RUTINA = "critico", "alto", "rutina"


# ── Señales ───────────────────────────────────────────────────────────────────────────────────
# Cada entrada: (regex, comités, nivel, motivo). El orden importa: gana la primera CRÍTICA que
# case, y luego se acumulan las demás sin repetir.
#
# Las señales clínicas van con dos comités a la vez y no por capricho: `comite-medico` aporta la
# literatura graduada y `verificacion` es el watchdog adversarial. Uno sin el otro es justo lo que
# falló: análisis plausible, sin nadie que intentara tumbarlo.
SEÑALES = [
    (r"\b(informe|panel|report)\b[^.\n]{0,40}\b(molecular|gen[oó]mic\w+|ngs|secuenciaci[oó]n)\b"
     r"|\bfoundation ?one\b|\bf1cdx\b|\btrusight\b|\bdipcan\b|\bvigex\b|\bexoma\b|\bwes\b|\bwgs\b"
     r"|\bscrna|\brna-?seq\b|\binmunopeptid[oó]m",
     ["comite-medico", "verificacion"], CRITICO,
     "material molecular o genómico: literatura graduada + watchdog adversarial"),

    (r"\b(neoantigen\w*|neoant[ií]gen\w*|vacuna personalizada|hla\b|epitopo\w*|ep[ií]topo\w*)\b",
     ["comite-medico", "verificacion"], CRITICO,
     "toca el diseño de la ruta a NED: nada aquí se sostiene con una sola cabeza"),

    (r"\b(biopsia|met[áa]stasis|met[áa]stasi\w*|tumor(al)?|histolog|anatom[íi]a patol|"
     r"inmunohistoqu[íi]mic|ihq|lesi[oó]n|cresta il[íi]aca|il[íi]ac\w+|[óo]se[ao]|hep[áa]tic\w+|"
     r"ganglio\w*|primario)\b|\bpet[- ]?(tc|ct)\b|\bsuvmax\b",
     ["comite-medico", "verificacion"], CRITICO,
     "dato clínico primario: se coteja contra la fuente antes de sostener nada"),

    # Biomarcadores y jerga molecular. Esta señal es la que faltaba el 5-sep: la sesión llevaba
    # cuatro turnos hablando de HRD, TMB, VAF y número de copias y no había disparado nada.
    (r"\b(hrd(sig)?|tmb|msi|mss?\b|ms-?stable|vaf|variant allele|n[uú]mero de copias|copy ?number|"
     r"amplificaci[oó]n|amplificad\w+|mutaci[oó]n|mutad\w+|wildtype|germinal|som[áa]tic\w+|"
     r"al[ée]lic\w+|clonal|subclonal|exon|ex[oó]n|cromosom\w+|ploid[íi]a|cariotipo|"
     r"gen\b|genes\b|diana\w*|driver|v[íi]a de se[ñn]alizaci[oó]n|autocrin\w+|expresi[oó]n g[ée]nica)\b"
     # El símbolo de gen, SOLO en mayúsculas (11-sep-26). Con re.I casaba «sep26», «mp3» o el id
     # de una tarea, y el enrutador pedía comité médico para una notificación del sistema.
     # Y sin las siglas de consumo que tienen la misma forma (11-sep-26, `verificacion`): «MP3»,
     # «PS5» o «GPT5» mandaban un mensaje de Telegram a comité médico en modo crítico.
     r"|(?-i:\b(?!(?:MP|PS|GPT|USB|IOS|HDMI|COVID|SARS|WIN|WIFI|LTE|DDR|RTX|GTX|HTTP|IPHONE|"
     r"XBOX|MACOS|ANDROID)\d)[A-Z]{2,6}[0-9]{1,2}\b)",
     ["comite-medico", "verificacion"], CRITICO,
     "biomarcador o alteración molecular: ninguna cifra de estas se sostiene sin cotejo"),

    (r"\b(ensayo|trial)s?\b[^.\n]{0,30}\b(cl[íi]nic\w+|fase [123i]|nct\d)|\bnct\d{6,}\b"
     r"|\belegibilidad\b|\bcriterios de (inclusi[oó]n|exclusi[oó]n)\b",
     ["comite-medico", "consejero-acceso"], CRITICO,
     "elegibilidad a ensayo: mezcla evidencia y acceso real"),

    (r"\b(f[áa]rmac\w+|tratamiento|terapia|dosis|inhibidor\w*|quimio\w*|radioterapia|"
     r"pronóstico|pron[oó]stico|superviv\w+|mpfs\b|\bhr\s*=|\bors?\b|\borr\b)\b",
     ["comite-medico", "verificacion"], CRITICO,
     "afirmación terapéutica o pronóstica: cifra que no se cotea, cifra que engaña"),

    (r"\b(paper|art[íi]culo|literatura|pubmed|doi\b|pmid\b|evidencia|meta-?an[áa]lisis|"
     r"revisi[oó]n sistem[áa]tica)\b",
     ["verificacion"], ALTO,
     "toda cita se abre y se verifica antes de salir"),

    (r"\b(acceso|contactar|laboratorio|financiaci[oó]n|fondos|beca|grant|colaboraci[oó]n)\b",
     ["consejero-acceso"], ALTO,
     "motor de acceso: cuál es el cuello de botella real"),

    # «web» a secas ya no basta (11-sep-26): «alguien experto en web» en una petición sobre cómo
    # enruta Polaris pedía el comité de marca. Tiene que ser la web COMO superficie que se ve.
    (r"\b(la web|p[áa]gina web|helptitular|landing|copy|hero|marca|dise[ñn]o visual|logo|"
     r"tipograf\w*|paleta|accesibilidad|wcag)\b",
     ["diseno"], ALTO,
     "todo lo que se ve pasa por el comité de marca antes de publicarse"),

    # Ojo: aquí NO va un `\bx\b` suelto para la red social. Casaría con cualquier «x» del texto y
    # convertiría el enrutador en ruido, que es la forma más rápida de que se ignore.
    (r"\b(instagram|linkedin|twitter|tiktok|reel|story|carrusel|caption)\b|\b(post|hilo) en x\b",
     ["redes-contenido", "voz-titular"], ALTO,
     "contenido en su voz: pase de voz antes de que salga"),

    (r"\b(prensa|periodista|entrevista|medio|nota de prensa|reportaje)\b",
     ["prensa", "voz-titular"], ALTO,
     "la atención de los medios se convierte en contactos útiles, no en ruido"),

    (r"\b(gdpr|consentimiento|mta|contrato|legal|asociaci[oó]n|estatutos|notar)\b",
     ["legal-burocracia"], ALTO,
     "trámite o transferencia de datos: hay forma correcta y hay forma cara"),

    # Sin «coste» (11-sep-26): casi siempre es coste de tokens o de modelos, que es de
    # `tools/coste.py` y de la rúbrica de tier, no del comité de dinero de la asociación.
    (r"\b(presupuesto|gastos?|donaci[oó]n|donante|factura|impuesto|irpf|transparencia econ[oó]mica)\b",
     ["finanzas-transparencia"], ALTO,
     "dinero: se reporta, no se improvisa"),

    (r"\b(vuelo|hotel|viaje|desplazamiento|cita en|logística|log[íi]stica)\b",
     ["agencia-viajes"], RUTINA,
     "que llegue entera a la cita"),

    (r"\b(plazo|pendiente|tablero|seguimiento|agenda|tarea)\b",
     ["asistente"], RUTINA,
     "Vega mantiene la fuente única, no la memoria"),

    # Preguntas sobre Polaris mismo (11-sep-26). RUTINA a propósito: «¿cuál es el flujo?» lo
    # contesta la sesión leyendo el código; el comité entra cuando se va a CAMBIAR la máquina,
    # y eso ya lo sube el plan-primero, no este carril.
    (r"\b(polaris|hooks?|comit[eé]s|subagentes?|enrutad\w*|orquestad\w*|arquitectura|arn[eé]s|"
     r"llms?)\b",
     ["consejero-arquitectura"], RUTINA,
     "arquitectura de Polaris: se consulta al cambiarla, no para describirla"),
]

# Señales que ELEVAN a crítico algo que ya iba por otro carril: el tamaño de la apuesta, no el tema.
AMPLIFICADORES = re.compile(
    r"\bexhaustiv\w+|\ba fondo\b|\ben profundidad\b|\btodo lo que\b|\bcompleto\b|\bned\b"
    r"|\bdecisi[oó]n\b|\bdecidir\b|\bmejor forma\b", re.I)


def _agentes_activos():
    """Comités que existen y NO están archivados. Fail-open: si no se puede leer, no se filtra."""
    activos = {}
    try:
        for f in sorted(os.listdir(DIR_AGENTES)):
            if not f.endswith(".md"):
                continue
            slug = f[:-3]
            try:
                cab = open(os.path.join(DIR_AGENTES, f), encoding="utf-8",
                           errors="replace").read(1200)
            except Exception:
                activos[slug] = ""
                continue
            if re.search(r"^estado:\s*archivado\s*$", cab, re.M):
                continue
            m = re.search(r"^description:\s*(.+)$", cab, re.M)
            activos[slug] = (m.group(1).strip() if m else "")
    except Exception:
        return None
    return activos or None


def decidir(intencion):
    """→ dict con comités, nivel, motivos y obligatoriedad. Nunca lanza."""
    # str() y no `or ""`: al hook le puede llegar cualquier cosa desde el JSON del harness, y una
    # centralita que revienta con un entero deja la sesión sin freno y sin decir nada (fail-open).
    texto = ("" if intencion is None else str(intencion)).strip()
    salida = {"intencion": texto[:200], "comites": [], "nivel": RUTINA,
              "motivos": [], "obligatorio": False, "amplificado": False}
    if not texto:
        return salida

    activos = _agentes_activos()
    vistos = []
    for regex, comites, nivel, motivo in SEÑALES:
        if not re.search(regex, texto, re.I):
            continue
        for c in comites:
            # Si no se pudo leer el directorio, no se descarta a nadie (fail-open).
            if activos is not None and c not in activos:
                continue
            if c not in vistos:
                vistos.append(c)
        if motivo not in salida["motivos"]:
            salida["motivos"].append(motivo)
        if nivel == CRITICO:
            salida["nivel"] = CRITICO
        elif nivel == ALTO and salida["nivel"] != CRITICO:
            salida["nivel"] = ALTO

    if vistos and salida["nivel"] == ALTO and AMPLIFICADORES.search(texto):
        # «analiza esto exhaustivamente» sobre algo que ya pedía comité deja de ser opcional.
        salida["nivel"] = CRITICO
        salida["amplificado"] = True
        salida["motivos"].append("la propia petición pide profundidad o sostiene una decisión")

    salida["comites"] = vistos
    salida["obligatorio"] = bool(vistos) and salida["nivel"] == CRITICO
    return salida


def explicar(d):
    """Texto corto para humanos y para inyectar en un hook."""
    if not d["comites"]:
        return "Sin señal de comité: lo normal es que lo haga la sesión sola."
    marca = "OBLIGATORIO" if d["obligatorio"] else d["nivel"].upper()
    lineas = ["[%s] %s" % (marca, " + ".join("`%s`" % c for c in d["comites"]))]
    for m in d["motivos"]:
        lineas.append("  · " + m)
    return "\n".join(lineas)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    como_json = "--json" in argv
    exigir = "--exigir" in argv
    resto = [a for a in argv if not a.startswith("--")]
    intencion = " ".join(resto) or sys.stdin.read() if not sys.stdin.isatty() else " ".join(resto)

    d = decidir(intencion)
    print(json.dumps(d, ensure_ascii=False, indent=1) if como_json else explicar(d))
    return 2 if (exigir and d["obligatorio"]) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Una centralita que revienta es peor que una que calla.
        sys.exit(0)
