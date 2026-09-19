#!/usr/bin/env python3
"""tools/decide_peticion.py — UNA decisión por petición: quién responde y con qué.

{{TITULAR}}, 11-sep-2026: *«que se analice quién es el mejor para responder… y que lo ejecute… esa
decisión no es mía»*. Había dos centralitas que no se hablaban y ninguna ejecutaba:
  · `enruta_comite.py` decide QUÉ COMITÉ (reglas, sin LLM).
  · `enruta.py` decide QUÉ LLM (muro, capacidad, salud, coste, panel).
Esto las junta en una sola decisión con cuatro niveles, y la escribe como ORDEN para la sesión:

  directo  → responde la sesión sola. Coste cero extra. Es lo normal en charla y seguimiento.
  llm      → un LLM de fuera es mejor que Claude para esto (Grok para X, Perplexity para citas…).
  comite   → el comité dueño del dominio (y `verificacion` si sostiene una decisión o es clínico).
  panel    → varias cabezas: comités + LLMs de casas distintas, y Claude sintetiza.

CÓMO SE HACE CUMPLIR (y por qué así): en el chat, un hook no puede lanzar un agente. Lo que sí
puede es BLOQUEAR la respuesta. Por eso la decisión se persiste por sesión
(`tools/state/plan_enrutado/<sesion>.json`) y el Stop (`gate_salida.py::enrutado_incumplido`)
devuelve la respuesta si el turno no muestra la ejecución. Válvula: una línea
`ENRUTADO-OMITIDO: <motivo>` en la respuesta, que queda registrada y se audita.

LO QUE NO SE NEGOCIA:
  · El muro va primero. Si la petición o la sesión son sensibles, jamás se ordena un LLM de fuera.
  · Solo se ORDENA un LLM externo para capacidades que mandan una CONSULTA, no su contenido
    (buscar en vivo, redes, citas, segunda opinión). Visión, documentos largos o volumen llevan su
    material dentro: eso se sugiere, nunca se ordena, porque una imagen «neutra» puede ser su biopsia.
  · Determinista y <1 s: sin LLM, sin red. El modelo local tarda 5,6 s y no respeta el formato
    (medido el 11-sep-26), así que no entra en la decisión.

Uso:
  python3 tools/decide_peticion.py "busca qué se dice en X de la vacuna de BioNTech"
  python3 tools/decide_peticion.py --json "..."
  python3 tools/decide_peticion.py --eval tests/fixtures/enrutado_eval.jsonl
"""
import json
import os
import re
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

DIRECTO, LLM, COMITE, PANEL = "directo", "llm", "comite", "panel"

# Capacidades para las que un tercero recibe una CONSULTA, no el material de {{TITULAR}}.
CAPS_ORDENABLES = ("buscar_vivo", "redes", "citas", "segunda_opinion")

# Lo que el harness mete como si fuera un mensaje suyo y no lo es. Sin este filtro, una
# notificación de fin de tarea con un «FGFR1» dentro pedía comité médico (visto el 11-sep-26).
PREFIJOS_SISTEMA = ("<task-notification", "<ci-monitor-event", "<local-command",
                    "<command-name", "<bash-input", "<bash-stdout", "[SYSTEM NOTIFICATION")

DIR_PLANES = os.path.join(os.environ.get("BTP_STATE_DIR") or os.path.join(AQUI, "state"),
                          "plan_enrutado")

VALVULA = "ENRUTADO-OMITIDO:"


def es_del_sistema(prompt):
    p = (prompt or "").lstrip()
    return any(p.startswith(x) for x in PREFIJOS_SISTEMA)


def decidir(prompt, *, contexto_clinico=False, sesion_sensible=None, estado=None):
    """→ dict con nivel, comites, llms, sensible, motivos y la orden en texto. Nunca lanza.

    `contexto_clinico`: la sesión abrió material clínico y el comité aún no lo ha tocado → se le
    exige. `sesion_sensible`: la sesión abrió material clínico, lo haya tocado o no el comité → el
    muro cierra los LLMs de fuera. Por defecto, igual que `contexto_clinico`."""
    if sesion_sensible is None:
        sesion_sensible = contexto_clinico
    texto = ("" if prompt is None else str(prompt)).strip()
    out = {"nivel": DIRECTO, "comites": [], "llms": [], "sugeridos": [], "sensible": False,
           "motivos": [], "orden": "", "consulta": texto[:300], "ts": time.time()}
    if not texto or es_del_sistema(texto):
        out["motivos"].append("mensaje del sistema, no de {{TITULAR}}" if texto else "vacío")
        return out

    import enruta_comite
    import enruta
    dc = enruta_comite.decidir(texto)
    de = enruta.elegir(texto, estado=estado if estado is not None else enruta.salud_rapida(),
                       critico=bool(dc.get("amplificado")))
    sensible = bool(de.sensible or sesion_sensible)
    out["sensible"] = sensible
    if sensible:
        out["motivos"].append("muro: %s" % (de.motivo_sensible if de.sensible
                                             else "la sesión ya ha abierto material clínico"))

    # ── comités ─────────────────────────────────────────────────────────────────────────
    comites = []
    if dc["nivel"] in (enruta_comite.CRITICO, enruta_comite.ALTO):
        comites = list(dc["comites"])
        out["motivos"].extend(dc["motivos"])
    elif dc["comites"]:
        out["sugeridos"].extend(dc["comites"])
    if contexto_clinico and not comites:
        comites = ["comite-medico", "verificacion"]
        out["motivos"].append("la sesión ya trabaja material clínico: lo que venga hereda el nivel")
    out["comites"] = comites

    # ── LLMs de fuera ───────────────────────────────────────────────────────────────────
    llms = []
    if not sensible:
        for nombre in de.elegidos:
            meta = enruta.PROVEEDORES.get(nombre) or {}
            if meta.get("confianza"):
                continue                      # claude y local: los hace la sesión, no se ordenan
            caps = [c for c in de.capacidades if c in meta.get("fuerzas", {})]
            if any(c in CAPS_ORDENABLES for c in caps):
                llms.append(nombre)
                out["motivos"].append("%s: %s" % (nombre, de.motivos.get(nombre, "")[:120]))
            elif caps:
                out["sugeridos"].append(nombre)
            if not de.panel:
                break
    out["llms"] = llms

    # La caja, si la petición cae en una: sus asientos entran en `comites`, y con eso el gate
    # de Stop los exige. No sustituye al enrutado por dominio — se suma a él.
    slug, asientos = caja_aplicable(texto)
    if slug and asientos:
        out["caja"] = slug
        out["_asientos_caja"] = asientos
        comites = list(dict.fromkeys(list(comites) + asientos))
        out["comites"] = comites

    if comites and llms or len(llms) > 1:
        out["nivel"] = PANEL
    elif comites:
        out["nivel"] = COMITE
    elif llms:
        out["nivel"] = LLM
    out["orden"] = orden(out)

    # ── fallback de la caja, en modo SUGERIDO (13-sep-2026, paso 4) ────────────────────
    # Si nadie sienta un comité (ni la tabla ni la herencia clínica), `lentes.panel` calcula qué
    # ángulos tendría un panel. Se deja en `sugerido_fallback` y NO entra en `orden`: una semana de
    # prompts reales (apuntados en `guardar`) dice si el umbral vale antes de ordenar nada.
    if not comites:
        try:
            import lentes
            out["sugerido_fallback"] = lentes.panel(texto)
        except Exception:
            out["sugerido_fallback"] = []
    return out


# ─── enrutado a caja: la petición entra en su caja, si tiene una (16-sep-2026) ───────────
# Hasta hoy la caja se SUGERÍA: `CIERRE_DOSSIER` era una cadena que recordaba cerrar con el
# dossier, y los asientos se enumeraban a mano desde la tabla de dominio. El charter, que ya
# declara quién debe opinar sobre ese goal, no participaba en la decisión.
#
# Ahora, si la petición cae en una caja ACTIVA, sus asientos entran en `comites` — y con eso
# el gate de Stop los EXIGE sin tocar el gate, porque `falta_por_ejecutar` ya comprueba esa
# lista. Soldar aquí en vez de en `gate_salida.py` es lo que evita un segundo enforcement.
#
# DETERMINISTA y CONSERVADOR: solapamiento de términos con el slug y el `## Goal`, sin LLM y
# sin red. Ante dos cajas empatadas NO se ordena ninguna: convocar la caja equivocada gasta
# presupuesto y produce un dossier que contesta a otra pregunta. La ambigüedad se resuelve
# no actuando, que es la misma regla que el resto del muro.
_VACIAS = frozenset("""
para pero como cuando donde porque sobre desde hasta entre todo toda todos todas este esta
estos estas mismo misma otro otra cual cuales quien quienes algo nada mucho poco muy más
menos bien mal hacer haces hacemos tiene tienes tener puede puedes pueden decir dice dicen
""".split())


def _terminos(texto):
    """Palabras con carga: ≥5 letras y fuera de la lista de vacías."""
    return {w for w in re.findall(r"[a-záéíóúñü]{5,}", (texto or "").lower())
            if w not in _VACIAS}


def caja_aplicable(texto):
    """(slug, asientos) de la caja ACTIVA que cubre esta petición, o (None, []).

    Se compara contra el slug y el `## Goal`, que es donde el charter dice qué persigue.
    Umbral de 2 términos: con 1 basta un sustantivo común para convocar de más.
    """
    try:
        import caja as CJ                    # tardío: `caja` importa este módulo (contrato)
    except Exception:
        return None, []
    pedidos = _terminos(texto)
    if len(pedidos) < 2:
        return None, []
    mejor, empate = None, False
    try:
        base = CJ.glob.glob(os.path.join(CJ.ROOT, "00_FUENTE-DE-VERDAD", "04*IA",
                                         "Constelacion", "*", "CAJA.md"))
    except Exception:
        return None, []
    for ruta in sorted(base):
        slug = os.path.basename(os.path.dirname(ruta))
        try:
            fm, goal = CJ.charter(slug)
        except Exception:
            continue                          # un charter roto lo caza el auditor, no esto
        if (fm.get("estado") or "").strip() != "activa":
            continue
        n = len(pedidos & _terminos(slug.replace("-", " ") + " " + goal))
        if n < 2:
            continue
        if mejor and n == mejor[1]:
            empate = True
        elif not mejor or n > mejor[1]:
            mejor, empate = (slug, n), False
    if not mejor or empate:
        return None, []
    slug = mejor[0]
    try:
        fm, _ = CJ.charter(slug)
    except Exception:
        return None, []
    asientos = [a.strip() for a in ([fm.get("dueno") or ""] + (fm.get("expertos") or []))
                if a and a.strip()]
    return slug, list(dict.fromkeys(asientos))


# El contrato que cada asiento tiene que cerrar para que su trabajo entre en el dossier.
# Va en la ORDEN, no en la ficha de cada agente: así vale igual para los 33 de disco y para
# un experto compuesto al vuelo, y cambiarlo no obliga a tocar 33 ficheros.
# El último renglón es el que lo hace cumplir: explica el incentivo en vez de dar una orden.
CONTRATO = (
    "  📋 Cada uno cierra su respuesta con un bloque ```json (máx. 3 decisiones):\n"
    "     {\"decisiones\":[{\"titulo\":\"\",\"impacto\":\"critica|alta|media\","
    "\"recomendacion\":\"\",\"porque\":\"\",\"fuente\":\"\"}],\n"
    "      \"acciones\":[{\"texto\":\"\",\"limite\":\"\",\"fuente\":\"\"}]}\n"
    "     `fuente` = de dónde lo sabe: fichero, URL abierta, PMID/NCT, o «mi criterio».\n"
    "     Sin ese bloque su trabajo NO entra en el dossier y se queda en su respuesta suelta."
)
CIERRE_DOSSIER = ("  🧺 Cierra con: python3 tools/caja.py dossier — {{TITULAR}} recibe UN dossier, "
                  "no %d volcados sueltos.")
CIERRE_DOSSIER_CAJA = ("  🧺 Cierra con: python3 tools/caja.py dossier --caja %s — el dossier "
                       "sale titulado con el goal de la caja.")


def orden(d):
    """La decisión escrita para la sesión: qué ejecutar, en qué orden y cómo cerrarlo."""
    if d["nivel"] == DIRECTO:
        return ""
    pasos = []
    # Con caja, la convocatoria manda: la orden sale del charter (goal, arquetipo, gate,
    # contrato) en vez de enumerarse aquí. Enumerar los mismos asientos a mano dejaría el goal
    # fuera, que es justo lo que la caja existe para evitar.
    if d.get("caja"):
        pasos.append("Bash: python3 tools/caja.py convocar --caja %s  → y ejecuta la "
                     "convocatoria que imprime (trae el goal, el límite y el contrato)"
                     % d["caja"])
        pendientes = [c for c in d["comites"] if c not in (d.get("_asientos_caja") or [])]
    else:
        pendientes = list(d["comites"])
    for c in pendientes:
        pasos.append("Agent(subagent_type=\"%s\") con la petición y el contexto necesario" % c)
    if d["llms"]:
        pasos.append("Bash: python3 tools/enruta.py --ejecutar%s \"<la consulta, sin datos suyos>\""
                     % (" --todos --critico" if len(d["llms"]) > 1 else ""))
    lineas = ["ENRUTADO (%s) — decisión de Polaris, no de {{TITULAR}}: ejecútala ANTES de responder."
              % d["nivel"].upper()]
    for i, p in enumerate(pasos, 1):
        lineas.append("  %d. %s" % (i, p))
    if len(pasos) > 1:
        lineas.append("  → lanza en paralelo lo independiente; luego sintetiza tú (Claude) y di "
                      "qué dijo cada uno y dónde discrepan.")
    if d.get("caja"):
        # La convocatoria ya imprime el contrato: repetirlo aquí sería decir dos veces lo
        # mismo con el riesgo de que diverjan. Solo se recuerda el cierre, ya con la caja.
        lineas.append(CIERRE_DOSSIER_CAJA % d["caja"])
    elif len(d["comites"]) >= 2:
        # Con un solo asiento no hay nada que consolidar: pedir contrato y dossier ahí sería
        # burocracia. El dossier existe para que N voces lleguen como una.
        lineas.append(CONTRATO)
        lineas.append(CIERRE_DOSSIER % len(d["comites"]))
    lineas.append("  Al pie de la respuesta, una línea: «Respondió: …».")
    if d["motivos"]:
        lineas.append("  Por qué: " + " · ".join(d["motivos"][:4]))
    if d["sensible"]:
        lineas.append("  🔒 Sensible: ningún LLM de fuera recibe esto.")
    lineas.append("  Si de verdad no procede, escribe una línea «%s <motivo>» (queda registrada)."
                  % VALVULA)
    return "\n".join(lineas)


# ── persistencia por sesión (la lee el Stop) ────────────────────────────────────────────────
def _ruta_plan(sesion):
    seguro = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sesion or "sin-id"))[:120]
    return os.path.join(DIR_PLANES, seguro + ".json")


def guardar(sesion, d):
    try:
        os.makedirs(DIR_PLANES, exist_ok=True)
        tmp = _ruta_plan(sesion) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _ruta_plan(sesion))
        _purgar()
    except Exception:
        pass
    _apuntar_fallback(sesion, d)


def _apuntar_fallback(sesion, d):
    """Paso 4 de la caja: apunta el panel SUGERIDO de las peticiones reales para calibrarlo.

    Solo desde `guardar`, que solo llama el hook con prompts de verdad: `decidir` a secas (tests,
    --eval) no ensucia la medición. Guarda el arranque de la consulta porque sin él no se puede
    juzgar si el panel acertaba; vive en tools/state (local, fuera de git), como el plan del turno."""
    panel = (d or {}).get("sugerido_fallback")
    if not panel:
        return
    try:
        import lentes
        cand = lentes.dominio(d.get("consulta") or "")
        fila = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "sesion": str(sesion or "sin-id")[:8],
                "nivel": d.get("nivel"), "panel": panel,
                "dominio_candidato": cand, "consulta": (d.get("consulta") or "")[:200]}
        ruta = os.path.join(os.path.dirname(DIR_PLANES), "enrutado", "fallback.jsonl")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except Exception:
        pass


def cargar(sesion):
    try:
        return json.load(open(_ruta_plan(sesion), encoding="utf-8"))
    except Exception:
        return None


def _purgar(max_dias=3):
    """Los planes son de un turno: nada de archivo histórico que crezca sin freno."""
    try:
        limite = time.time() - max_dias * 86400
        for f in os.listdir(DIR_PLANES):
            p = os.path.join(DIR_PLANES, f)
            if os.path.getmtime(p) < limite:
                os.remove(p)
    except Exception:
        pass


# ── ¿se ejecutó? (lo usa el Stop) ───────────────────────────────────────────────────────────
def falta_por_ejecutar(d, usos):
    """Qué de la decisión `d` no aparece en `usos` (lista de strings del turno). [] si todo."""
    if not d or d.get("nivel") == DIRECTO:
        return []
    usos = usos or []
    faltan = []
    for c in d.get("comites", []):
        if c not in usos:
            faltan.append("comité `%s`" % c)
    if d.get("llms"):
        import enruta
        tools = {"enruta.py"} | {(enruta.PROVEEDORES.get(n) or {}).get("tool") or "" for n in d["llms"]}
        tools.discard("")
        if not any(any(t in u for t in tools) for u in usos):
            faltan.append("LLM %s (vía `tools/enruta.py --ejecutar`)" % " + ".join(d["llms"]))
    return faltan


# ── evaluación ──────────────────────────────────────────────────────────────────────────────
def evaluar(ruta):
    """Precisión contra un set etiquetado. Cada línea: {prompt, nivel, incluye?, excluye?}."""
    import enruta
    estado = {n: {"ok": True, "detalle": "eval"} for n in enruta.PROVEEDORES}
    casos, fallos = 0, []
    for ln in open(ruta, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        c = json.loads(ln)
        casos += 1
        d = decidir(c["prompt"], contexto_clinico=c.get("contexto_clinico", False), estado=estado)
        quien = set(d["comites"]) | set(d["llms"])
        mal = []
        if d["nivel"] not in c["nivel"].split("|"):
            mal.append("nivel %s (esperado %s)" % (d["nivel"], c["nivel"]))
        for x in c.get("incluye", []):
            if x not in quien:
                mal.append("falta %s" % x)
        for x in c.get("excluye", []):
            if x in quien:
                mal.append("sobra %s" % x)
        if mal:
            fallos.append((c["prompt"][:70], mal, sorted(quien)))
    return casos, fallos


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--eval"]:
        casos, fallos = evaluar(argv[1])
        for p, mal, quien in fallos:
            print("✗ %-70s  %s  → %s" % (p, "; ".join(mal), quien))
        precision = 1 - len(fallos) / max(1, casos)
        print("precisión %.0f%% (%d/%d)" % (precision * 100, casos - len(fallos), casos))
        return 0
    como_json = "--json" in argv
    texto = " ".join(a for a in argv if not a.startswith("--"))
    d = decidir(texto)
    if como_json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print("nivel: %s" % d["nivel"])
        print(d["orden"] or "responde la sesión sola")
        if d["sugeridos"]:
            print("sugeridos (no obligatorios): %s" % ", ".join(d["sugeridos"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write("decide_peticion: %r\n" % e)
        sys.exit(0)
