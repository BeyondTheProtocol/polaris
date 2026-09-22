#!/usr/bin/env python3
"""La Anatomía — el mapa vivo de Polaris.

Responde a tres preguntas con la misma pieza:
  1. ¿Qué tengo? (cuántos comités, tools, rutinas, skills, MCP, memoria)
  2. ¿Qué está haciendo ahora? (qué nodo late, qué corrió hoy, qué falló)
  3. ¿CÓMO está montado? (el camino de un encargo, la máquina, qué LLM hace qué)

NO es El Observatorio. El Observatorio es sala de control (todo a la vez, para
vigilar). Esto es un MAPA: una capa cada vez, para entender y para enseñar.

Solo lectura. Sin LLM (~0 tokens). Sin dependencias fuera de la stdlib.

TRES caras desde el mismo generador:
  privada  — nombres reales; solo local; para que {{TITULAR}} entienda su sistema.
  tecnica  — para enseñárselo a alguien que sabe de sistemas agénticos: TODO el
             detalle de arquitectura, máquina, modelos y gasto, pero sin los
             nombres que delatan su vida (el caso abierto, sus DMs, las cajas).
             Pasa por el mismo scrubber que la pública.
  publica  — todo texto pasa por _lexico_publico.revisar() ANTES de renderizar
             (fail-closed: lo que no pasa se sustituye por su categoría), y no
             sale ni un nombre de contacto ni nada clínico. Es la que se graba.

VOCABULARIO: de puertas afuera el panel habla en TÉRMINO ESTÁNDAR (LLM, router,
subagente, job queue, failover, hook, gate de egress) y la palabra de la casa va
al lado, glosada. Es la regla de {{TITULAR}} del 27/7/26 (commit c3befbc, glosario de
`ia.py`): ella valida lo técnico, así que el nombre de la industria manda. Las
FUNCIONES de este fichero conservan su nombre de casa (`cerebros()`): renombrar
en masa es churn y riesgo, y la palabra queda glosada donde se introduce.

Uso:
  python3 tools/anatomia.py inventario [--json]
  python3 tools/anatomia.py render [--cara publica|tecnica] [--vertical] [-o f.html]
  python3 tools/anatomia.py serve [--puerto 8791] [--cara privada]
  python3 tools/anatomia.py pulso
  python3 tools/anatomia.py sellar [--ver]   # el panel ya cuenta el sistema de hoy
"""

import datetime
import glob
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys

# Casa base SIEMPRE: la Anatomía retrata el sistema VIVO, no el worktree desde el
# que se lanza (los daemons, el estado y las trazas viven en casa base). Mismo
# criterio que tools/audit_comites.py.
ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(ROOT, "tools", "state")
AGENTS_DIR = os.path.join(ROOT, ".claude", "agents")
SKILLS_DIR = os.path.join(ROOT, ".claude", "skills")
MEMORY_DIR = os.path.expanduser(
    "~/.claude/projects/-Users-polaris-claudecode/memory")

sys.path.insert(0, os.path.join(ROOT, "tools"))

# Conectores MCP que viven en la CUENTA, no en un fichero del disco: no se
# pueden derivar leyendo el repo. Se declaran a mano y se marcan como tales en
# la vista (sello: "declarado", no "verificado"). Revisado: 2026-07-25.
CONECTORES_CUENTA = [
    # «literatura-medica», no «-ingeniera»: el léxico público veta «científic-»
    # (la norma de no presentar a {{TITULAR}} como ingeniera), y la etiqueta de un
    # conector de PubMed/PMC describe igual de bien llamándose médica.
    "notion", "gmail", "google-drive", "literatura-medica", "buscador-papers",
    "navegador", "chrome", "computer-use", "sesiones", "directorio",
    "registro-mcp", "tareas-programadas", "visualize", "simulador-ios", "borde",
]

# Comités cuyo NOMBRE no sale en la cara pública. El scrubber caza léxico y PII,
# pero no caza el contexto: "un comité" no dice nada prohibido y aun así
# cuenta que hay un asunto privado. Estos salen como "comité" a secas.
# La lista es una PROPUESTA: la última palabra sobre qué se enseña es de {{TITULAR}}.
PUBLICO_RESERVADO = frozenset({
    "investigador",       # OSINT sobre personas
    "dm-inbox",           # mina sus mensajes privados
    "x-inbox",            # idem
    "conserje-web",       # opera cuentas suyas
})

# Rutinas que en la cara TÉCNICA salen contadas, no nombradas. El nombre de un
# daemon es información de arquitectura y por eso la cara técnica los enseña —
# pero estos nombran un CANAL personal («wa-…», «dm-…»), y eso ya no cuenta cómo
# funciona el sistema, cuenta la vida de {{TITULAR}}. Mismo criterio que
# PUBLICO_RESERVADO y, como aquella, es una PROPUESTA: la última palabra es suya.
TECNICA_RESERVADO = ("dm-inbox", "x-dms", "x-inbox", "x-guardados", "wa-",
                     "whatsapp", "instagram", "conserje")

# ─── el vocabulario ──────────────────────────────────────────────────────────
# Palabra de la casa ↔ término estándar ↔ qué es, en una línea. Existe porque la
# metáfora sola le quita el enganche a quien ya sabe de sistemas agénticos: ve
# «cerebro» y no puede mapearlo a lo que conoce. Ambos nombres, siempre.
GLOSARIO = [
    ("orquestador", "router de intención",
     "lee lo que pides, decide el plan y reparte al especialista"),
    ("comité", "subagente",
     "un agente con su propio system prompt, sus permisos y su oficio"),
    ("centralita", "router de proveedores LLM (control-plane)",
     "elige QUÉ modelo atiende cada llamada; el modelo nunca se elige a sí mismo"),
    ("cerebro", "proveedor de inferencia (LLM)",
     "una entrada del registro de proveedores, no un proceso vivo"),
    ("relevo", "failover sobre una fallback chain",
     "si el proveedor de turno cae o topa límite, entra el siguiente por orden"),
    ("perfil de tarea", "intent routing determinista",
     "clasifica la petición por regex, sin gastar un token y sin poder alucinar"),
    ("el muro", "guardarraíles estructurales",
     "hooks que se meten ANTES de cada herramienta; no son un prompt, son código"),
    ("guarda", "hook",
     "se engancha a un evento del arnés (antes de la tool, al terminar el turno)"),
    ("el borde", "gate de egress",
     "decide qué dato puede salir de esta máquina y hacia dónde; fail-closed"),
    ("la cola", "job queue persistente",
     "un encargo es un fichero; su estado es la carpeta donde vive"),
    ("el lazo", "loop autónomo 24/7",
     "lo que corre solo, sin nadie delante de la pantalla"),
    ("caja", "sub-sistema por objetivo",
     "un mini-Polaris para un objetivo concreto, que hereda el muro entero"),
    ("carril gratis", "free tier",
     "degradación de COSTE, nunca de las garantías del muro"),
    ("el Tablero", "backlog único",
     "todo lo que queda colgando, exhaustivo; se filtra lo que se muestra, no lo que se guarda"),
    ("el historial", "archivo clínico canónico",
     "un documento, un sitio: la carpeta clasifica, el nombre identifica, el índice se genera"),
]

# ─── el camino de un encargo ─────────────────────────────────────────────────
# La arquitectura agéntica contada como un recorrido, con el fichero real de cada
# eslabón. Es texto FIJO a propósito: describe el diseño, no el estado. Lo que sí
# se cuenta solo (cuántos hooks, cuántas rutinas, qué proveedores) va aparte.
CAMINO = [
    ("entra", "tools/bot_telegram.py",
     "Telegram, chat o voz. Solo el chat de la allowlist. No interpreta nada: "
     "encola el texto como DATO delimitado, en perfil de cuarentena. Todo lo que "
     "llega de fuera es dato no confiable, nunca instrucciones."),
    ("quién responde", "tools/decide_peticion.py",
     "Una sola decisión por petición: la sesión sola, un LLM de fuera, el comité dueño o un "
     "panel de varias cabezas. Junta la centralita de comités y la de modelos, el muro primero. "
     "En el chat se ejecuta y el gate de salida lo exige; en el lazo de Telegram se fija en el "
     "encargo antes de lanzarlo: el comité dueño va como agente."),
    ("triaje", "tools/triage_route.py",
     "Código, no un modelo. Solo escala a trabajo real si el JSON viene marcado "
     "seguro y la acción está en una allowlist cerrada. Fail-closed: lo que no "
     "encaja no se ejecuta."),
    ("la cola", "tools/cola.py",
     "Un encargo = un fichero JSON. Su estado ES el directorio donde vive, y cada "
     "transición es un rename atómico. Carriles de prioridad, reintentos con "
     "dead-letter, y un tope de anidamiento que corta la bomba de fork."),
    ("el despachador", "tools/btp_dispatcher.sh",
     "Shell, no un agente: no razona, reparte. Un lock deja pasar UN encargo a la "
     "vez. Antes de cada ciclo mira los kill-switches y el presupuesto del día."),
    ("el subagente", "tools/run_agent.sh",
     "Arranca el arnés agéntico con el comité que toca, su fichero de permisos y "
     "un tope de turnos (25 de rutina, 60 si es crítico). Nunca se salta los "
     "permisos, y un subagente no puede anidar más de dos niveles."),
    ("los guardarraíles", ".claude/hooks/",
     "Antes de CADA herramienta corre un hook que puede denegarla: en el lazo, una "
     "allowlist fail-closed; en interactivo, el guarda de lo clínico. Y al cerrar "
     "el turno, otro hook revisa la respuesta contra las normas escritas: la mayoría "
     "solo avisan, pero dos frenan la respuesta aunque el gate esté en modo aviso — "
     "una cita que no existe y una afirmación fuerte sin sello, porque las dos, una "
     "vez leídas, ya no se pueden deshacer."),
    ("las herramientas", "tools/ · MCP",
     "Lo que el subagente puede tocar: las tools propias del repo y los conectores "
     "MCP. Lo que no está en la lista, no existe para él."),
    ("la boca", "tools/salida.py",
     "El ÚNICO punto del repo que habla hacia fuera. Un aviso al sistema sale solo; "
     "publicar, contactar o pagar jamás sale desatendido: queda en borrador y "
     "espera un OK humano con su nonce."),
    ("la traza", "tools/observabilidad.py · tools/panel.py",
     "Cada ejecución deja línea append-only con duración, tokens y coste, y un "
     "parte de qué hizo, qué decidió, qué espera OK y qué falló. Sin esto, "
     "supervisar sería mirar por encima del hombro."),
]

# Cadena de degradación de modelos del plano agéntico. Se declara aquí para poder
# CONTARLA, pero la verdad vive en el `case` de tools/run_agent.sh — y un test ata
# las dos: si alguien cambia el shell, el test se cae en vez de que el panel siga
# afirmando en silencio algo que ya no es cierto.
CADENA_MODELOS = (("fable", "opus", "sonnet"),
                  ("opus", "sonnet", "haiku"),
                  ("sonnet", "haiku"))
MODELO_POR_DEFECTO = "sonnet"

# Familias de herramientas: clave → nombre humano de la familia. Se evalúan EN
# ORDEN; la primera que casa manda. Una clave con "=" delante exige nombre
# exacto (para siglas cortas que si no se comen medio repo). Lo que no casa cae
# en "otras" — si "otras" crece mucho, es señal de que falta una familia.
FAMILIAS = [
    ("muro y seguridad", ("muro", "codigo_rojo", "seguridad", "_lexico", "deid",
                          "=clinico_guard", "cuarentena", "guard", "_secrets",
                          # la válvula del guard de salida: permiso de un solo uso que solo
                          # nace del mensaje de {{TITULAR}} (21-sep-26)
                          "ok_envio",
                          "decision_alto_riesgo",
                          "gate_etiqueta",     # etiqueta hallazgos del gate de salida (aviso→bloqueo)
                          "replay_gate",       # mide un check nuevo contra respuestas reales (22-sep-26)
                          # a Grok/Perplexity sale el TEMA de la búsqueda, no su frase (22-sep-26)
                          "reescribe_consulta", "eval_reescritura")),
    ("el caso clínico", ("cascada", "contexto_caso", "elegibilidad", "nct",
                         "estado_actual",   # fecha de §1 de ESTADO-ACTUAL: candado del perfil N1 del radar
                         "invariante", "dosier", "ocr_informes", "postdicom",
                         "sintomas", "biomarcador", "pipeline", "evidencia",
                         # `historial` es el archivo clínico entero (401 documentos); caía en
                         # «otras» solo porque su nombre no lleva la palabra «clinico».
                         "historial",
                         # mapa y vídeo 3D del hígado desde DICOM (19-sep-26)
                         "visor3d",
                         "clinico", "centinela_ned")),
    ("la boca (avisos)", ("salida", "avisos", "notif", "enviar", "telegram",
                          "bot_", "report", "=digest")),
    ("correo", ("correo", "gmail", "imap", "outbox", "email_")),
    # helptitular.com: el carril que la actualiza sola, su lint y su marcha atrás (20-sep-26)
    ("la web pública", ("web_novedad", "web_lint", "web_revertir", "web_")),
    ("redes y prensa", ("x_", "wa_", "ig_", "dm_", "yt_", "instagram",
                        "youtube", "prensa", "umami", "redes", "reel_")),
    ("buscar y verificar", ("grok", "perplexity", "nvidia", "consensus",
                            "scite", "pubmed", "radar", "kb", "elicit",
                            "verifica", "cotejo",
                            "cn_fetch",      # lee webs chinas geobloqueadas para investigar (13-sep)
                            "cn.py", "cn_", # cn.py: buscar CUALQUIER cosa en la web china (20-sep)
                            "cde_fetch",     # el registro chino de ensayos, sin navegador (20-sep)
                            "onco")),        # grafo abierto de oncología en local (19-sep)
    ("salud del sistema", ("healthcheck", "vigia", "salud", "errores",
                           # valida la forma de los ficheros de estado (20-sep-26)
                           "esquema_estado",
                           "observabilidad", "heartbeat", "diag", "ia_health",
                           "audit_", "frescura",
                           "ciclo_agentes",     # id, turno y motivo de salida de cada agente
                           "bucles_colgados",   # corta bucles de espera colgados (20-sep)
                           "sonda_silencio",    # lo que arrancó y no dejó obra (21-sep)
                           "inventario")),      # qué pieza está viva y cuál no (20-sep)
    ("memoria y saber", ("archivar", "memoria", "cosecha", "espejo", "living",
                         "cronica", "contexto_lazo", "continuity", "minador")),
    ("el día a día", ("seguimiento", "calendar", "cumbre", "tareas", "reservas",
                      "conserje", "bandeja", "pendientes", "ritmo", "rituales",
                      "anticipa", "triage", "vega_", "persecucion", "dedup",
                      "buzon", "reconcilia", "sync_playbook", "leer_contacto",
                      "responder_con_datos", "viajes", "asistente", "hoy_",
                      "org_wrap", "cuidado", "etiquetar_")),
    ("dinero", ("coste", "cost_guard", "finanzas", "presupuesto", "pago",
                "gasto")),
    ("git y ramas", ("git", "ramas", "deploy", "release", "worktree",
                     "rebuild", "ci_barrido",   # guarda lo que ENTRA por PR al repo público

                     # el espejo público: derivar el árbol publicable y mantenerlo al día
                     # (19-sep-26) — es fontanería de repos, no «otras»
                     "publicar", "pr_portar")),
    ("el lazo", ("cola", "queue", "dispatcher", "run_agent", "panel", "decide_peticion",
                 # comprueba que un encargo del lazo dejó huella en disco (20-sep-26)
                 "prueba_entregable",
                 "orquesta", "constelacion", "constructor", "evals",
                 "paso_consolidacion", "cerrar_sesion",
                 "caja",      # caja.py cosecha N subagentes y entrega UN dossier
                 "lentes")),  # lentes.py: panel de reserva por ángulos cuando la tabla no da comité
    ("modelos de IA", ("chatgpt", "gemini", "glm", "=ia", "capacidades",
                       "carril_gratis", "elevenlabs", "voz_", "transcribe",
                       "openrouter",
                       "enruta",     # enruta.py es quien ELIGE el modelo: aqui, no en «otras»
                       "local")),    # local.py es el LLM que corre en casa (ollama), egress 0
    ("documentos", ("md_to_pdf", "drive", "_pdf", "pdf_")),
    ("la casa (infra)", ("_lock", "_net", "_xurl", "_casa", "activar_daemon", "backup",
                         "borde", "preview_remoto", "staging", "mcp_server",
                         "rotar_logs", "fugu", "observatorio", "polaris_estado",
                         "anatomia", "aeo", "snowflake", "honestidad_lint",
                         "caffeinate",
                         "_oauth_refresh",      # renueva el token del carril de evidencia
                         "migrar_secretos")),   # claves del Llavero, no en claro
    ("mejora del sistema", ("auto_mejora", "coach", "kpi_", "deuda", "normas",
                            "reglas_repetidas",
                            "bench_determinista",   # mide acierto del triaje, set dorado
                            "bench_jev",            # mide un modelo externo contra el mismo set, tras el muro
                            "mutantes")),           # comprueba que los tests de verdad protegen
]


# ─── inventario: qué tengo ───────────────────────────────────────────────────

def _frontmatter(path):
    """Frontmatter YAML plano entre las dos primeras '---'. Igual que audit_comites."""
    fm = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return fm
    if not lines or lines[0].strip() != "---":
        return fm
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", ln)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def _oficio(desc):
    """La primera frase de la descripción de un agente, recortada."""
    if not desc:
        return ""
    txt = desc.strip().strip('"').strip("'")
    txt = re.split(r"(?<=[a-zá-úñ])\.\s|\s—\s|\s-\s", txt)[0]
    return txt[:80].strip()


def comites():
    """Los agentes del gabinete: nombre + oficio en una línea."""
    out = []
    for p in sorted(glob.glob(os.path.join(AGENTS_DIR, "*.md"))):
        fm = _frontmatter(p)
        nombre = fm.get("name") or os.path.basename(p)[:-3]
        out.append({"nombre": nombre, "oficio": _oficio(fm.get("description", ""))})
    return out


def _familia_de(nombre):
    for fam, claves in FAMILIAS:
        for k in claves:
            if (nombre == k[1:]) if k.startswith("=") else (k in nombre):
                return fam
    return "otras"


def herramientas():
    """Las tools .py agrupadas por familia, con el total de líneas."""
    fams, lineas = {}, 0
    for p in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py"))):
        nombre = os.path.basename(p)[:-3]
        fams.setdefault(_familia_de(nombre), []).append(nombre)
        try:
            with open(p, "rb") as f:
                lineas += sum(1 for _ in f)
        except OSError:
            pass
    return {"familias": dict(sorted(fams.items(), key=lambda kv: -len(kv[1]))),
            "total": sum(len(v) for v in fams.values()), "lineas": lineas}


def _labels_cargados():
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return set()
    return {ln.split("\t")[-1].strip() for ln in out.splitlines()[1:]
            if ln.split("\t")}


def rutinas():
    """Daemons launchd: el REGISTRO (fuente única) cruzado con lo cargado de verdad."""
    reg = {}
    try:
        with open(os.path.join(ROOT, "tools", "launchd", "REGISTRO.json"),
                  encoding="utf-8") as f:
            reg = json.load(f).get("daemons", {})
    except Exception:
        pass
    cargados = _labels_cargados()
    out = []
    for lab in sorted(set(reg) | {l for l in cargados if l.startswith("com.btp.")}):
        out.append({
            "label": lab,
            "nombre": lab.replace("com.btp.", ""),
            "concern": reg.get(lab, {}).get("concern", ""),
            "cargado": lab in cargados,
            "en_registro": lab in reg,
        })
    return out


def skills():
    """Skills del repo. Una carpeta SIN SKILL.md existe pero NO carga."""
    out = []
    if os.path.isdir(SKILLS_DIR):
        for n in sorted(os.listdir(SKILLS_DIR)):
            d = os.path.join(SKILLS_DIR, n)
            if not os.path.isdir(d):
                continue
            out.append({"nombre": n, "carga": os.path.isfile(os.path.join(d, "SKILL.md"))})
    return out


def mcps():
    """MCP del repo (verificado, se lee del disco) + conectores de cuenta (declarados)."""
    locales = []
    try:
        with open(os.path.join(ROOT, ".mcp.json"), encoding="utf-8") as f:
            locales = sorted(json.load(f).get("mcpServers", {}).keys())
    except Exception:
        pass
    return {"locales": locales, "cuenta": sorted(CONECTORES_CUENTA)}


def cerebros():
    """Los LLM (proveedores de inferencia) que puede usar el sistema, del registro
    de `ia.py` (fuente única, `tools/peripheries.json`).

    Aquí se ve de un vistazo lo que de verdad importa de cada uno: si es GRATIS o
    de pago, si es DE CONFIANZA para el muro (a un proveedor no-trusted no le
    llega nada sensible, por muy bueno que sea), qué MODELO exacto sirve y para
    qué CARRIL se le llama.
    """
    try:
        import ia
        registro = ia.cargar_registro()
    except Exception:
        return []
    out = []
    for c in registro:
        # `model` (singular) es lo que declaran los locales; `models` (lista), los
        # de nube. Sin unificar aquí, el local salía "sin modelos declarados"
        # cuando sí lo tiene, y era justo el dato que se venía a ver.
        modelos = list(c.get("models") or [])
        if not modelos and c.get("model"):
            modelos = [c["model"]]
        out.append({
            "nombre": c.get("name", "?"),
            "encendido": bool(c.get("enabled")),
            "gratis": bool(c.get("free")),
            "confianza": bool(c.get("trusted")),
            "capacidad": c.get("capability"),
            "modelos": modelos,
            "modelos_clinico": list(c.get("models_clinico") or []),
            "carriles": list(c.get("para") or []),
            "solo_carril": bool(c.get("solo_perfil")),
            "local": str(c.get("destino", "")).startswith("local:"),
        })
    # Primero los encendidos, y dentro, el más capaz arriba.
    return sorted(out, key=lambda c: (not c["encendido"], -(c["capacidad"] or 0)))


# Qué es cada carril, dicho para quien no vive aquí dentro. Las CLAVES tienen que
# ser las de `ia.PERFILES`; un test lo comprueba, para que añadir un perfil nuevo
# al router no deje el panel explicando ocho de nueve.
CARRILES = {
    "clinico": "lo sensible del caso — solo proveedor de confianza, nunca una nube ajena",
    "evidencia": "papers de verdad, con PMID/DOI — herramienta especializada, no un buscador LLM",
    "descubrir": "lo último que ha salido (preprints, congresos) — radar, no oráculo: se coteja",
    "osint-x": "lo que se dice en X",
    "web-frontend": "maquetar web y componentes",
    "codigo": "escribir y arreglar código",
    "segunda-opinion": "otro par de ojos a propósito, para contrastar",
    "volumen": "trabajo a destajo y barato: resumir, clasificar, extraer, traducir",
    "general": "lo que no cae en ningún carril: la cadena de siempre, por capacidad",
}


def ruteo():
    """Qué proveedor atiende cada carril, y en qué orden. NO se escribe a mano: se
    deriva del campo `para` del registro, así que no puede desincronizarse del
    router de verdad. Es la respuesta a «¿para qué usas cada modelo?»."""
    try:
        import ia
        perfiles = list(ia.PERFILES)
    except Exception:
        return []
    ce = [c for c in cerebros() if c["encendido"]]
    out = []
    for p in perfiles:
        # El especialista del carril primero; detrás, la cadena general (que es la
        # que recoge si el especialista está caído o bloqueado). Los proveedores
        # `solo_perfil` no entran en la general a propósito.
        suyos = [c["nombre"] for c in ce if p in c["carriles"]]
        resto = [c["nombre"] for c in ce
                 if p not in c["carriles"] and not c["solo_carril"]]
        if p == "clinico":
            # El carril clínico no se reordena: exige confianza, y punto.
            suyos = [c["nombre"] for c in ce if c["confianza"] and (c["capacidad"] or 0) >= 8]
            resto = []
        out.append({"carril": p, "que_es": CARRILES.get(p, ""),
                    "especialistas": suyos, "luego": resto[:3]})
    return out


def arquitectos():
    """Los ARNESES agénticos registrados (`tools/orquestadores.json`): quién arranca
    y razona el gabinete. Es una caja intercambiable — y el muro no depende de ella,
    porque vive en hooks y en el gate de egress, no en el prompt del arnés."""
    try:
        with open(os.path.join(ROOT, "tools", "orquestadores.json"),
                  encoding="utf-8") as f:
            reg = json.load(f).get("orquestadores", [])
    except Exception:
        return []
    return [{"nombre": o.get("name", "?"),
             "arnes": o.get("kind", ""),
             "encendido": bool(o.get("enabled")),
             "confianza": bool(o.get("trusted")),
             "medido": bool(o.get("medido")),
             "nivel_min": o.get("nivel_min", "")} for o in reg]


def maquina():
    """La casa: chip, núcleos, RAM, disco, cuánto lleva encendida, y si el LLM local
    está de verdad levantado.

    Por `sysctl` y no por `system_profiler`: aquél tarda ~2 s y devuelve el número
    de serie, que no queremos ni cerca de un HTML que se publica. Fail-soft entero:
    si algo no se puede saber, no sale — nunca se inventa.
    """
    m = {}
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string", "hw.ncpu",
             "hw.perflevel0.logicalcpu", "hw.perflevel1.logicalcpu", "hw.memsize"],
            capture_output=True, text=True, timeout=5).stdout.split("\n")
        m["chip"] = out[0].strip()
        m["nucleos"] = int(out[1])
        m["nucleos_potencia"] = int(out[2])
        m["nucleos_eficiencia"] = int(out[3])
        m["ram_gb"] = round(int(out[4]) / 1024 ** 3)
    except Exception:
        pass
    try:
        u = shutil.disk_usage(ROOT)
        m["disco_gb"] = round(u.total / 1024 ** 3)
        m["disco_libre_gb"] = round(u.free / 1024 ** 3)
    except Exception:
        pass
    try:                       # el único que sabe de uptime en todo el repo
        import vigia
        seg = vigia._uptime_seg()
        if seg:
            m["encendida_dias"] = int(seg / 86400)
    except Exception:
        pass
    m["llm_local_vivo"] = _puerto_vivo("127.0.0.1", 11434)
    return m


def _puerto_vivo(host, puerto, timeout=1.0):
    """¿Hay alguien escuchando? Cuatro líneas de stdlib a propósito: importar el
    healthcheck entero para esto costaría lo que cuesta y tiene efectos."""
    try:
        with socket.create_connection((host, puerto), timeout):
            return True
    except Exception:
        return False


def gasto():
    """Lo que cuesta operar esto. Dos fuentes, las dos ya existentes:
    `cost_guard` (barato, lee el estado del día) para el euro de hoy y el tope, y
    `coste` (~2 s, escanea las trazas) para el VOLUMEN de tokens por modelo.

    El volumen importa más que el euro y hay que decirlo: en suscripción el coste
    marginal de una llamada es ≈0, así que un € pequeño no significa poco trabajo.
    """
    g = {}
    # `except BaseException`: cost_guard._panic() levanta SystemExit ante un estado
    # corrupto, y hacer caer el panel entero por no poder pintar una cifra de gasto
    # sería cambiar un mapa por un número.
    try:
        import cost_guard
        g["hoy_usd"] = round(cost_guard.today_spent(), 2)
        g["mes_usd"] = round(cost_guard.month_spent(), 2)
        dia, job, mes = cost_guard._limits()
        g["tope_dia_usd"], g["tope_job_usd"], g["tope_mes_usd"] = dia, job, mes
    except BaseException:
        pass
    try:
        import coste
        ficheros = []
        for proy in coste.proyectos_del_repo():
            ficheros += coste.transcripts(proy)   # también subagentes y workflows
        por_modelo, por_dia, _ = coste.scan(ficheros)
        tabla = coste.precios()
        modelos = []
        for modelo, u in por_modelo.items():
            modelos.append({
                "modelo": modelo,
                "tokens": coste.tokens_total(u),
                "usd": coste.usd(u, coste.price_for(modelo, tabla)),
                "con_precio": coste.price_for(modelo, tabla) is not None,
            })
        g["modelos"] = sorted(modelos, key=lambda m: -m["tokens"])[:6]
        g["tokens_total"] = sum(coste.tokens_total(u) for u in por_modelo.values())
        hoy = datetime.date.today().isoformat()
        if hoy in por_dia:
            g["tokens_hoy"] = coste.tokens_total(por_dia[hoy])
    except BaseException:
        pass
    return g


def guardas():
    """Los dientes del muro: los hooks que se meten en medio de cada herramienta."""
    d = os.path.join(ROOT, ".claude", "hooks")
    if not os.path.isdir(d):
        return []
    # Por NOMBRE, sin extensión y sin repetir: `muro_guard` vive en .py y en .sh
    # (el .sh es el arranque del .py), y contarlo dos veces diría que hay un
    # guarda más de los que hay.
    return sorted({n.rsplit(".", 1)[0] for n in os.listdir(d)
                   if n.endswith((".py", ".sh")) and not n.startswith("_")})


def cola():
    """Cuántos encargos hay esperando, corriendo o caídos."""
    # `cola`, NO `queue`: `import queue` se trae el de la STDLIB, que no tiene
    # get_status, así que la primera versión caía al except y devolvía todo ceros
    # — una cola vacía de mentira, que es peor que no enseñarla.
    try:
        import cola as _c
        est = _c.get_status() or {}
    except Exception:
        return {}
    return {k: est.get(k, 0) for k in ("pending", "processing", "failed", "done")}


def cajas():
    d = os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "04 · IA", "Constelacion")
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d)
                  if os.path.isdir(os.path.join(d, n)))


def memoria():
    # Solo memorias de verdad: fuera el índice (MEMORY.md) y los auxiliares que
    # escribe la rutina de memoria (_indice-*.md), que si no inflan la cuenta.
    mem = len([p for p in glob.glob(os.path.join(MEMORY_DIR, "*.md"))
               if not os.path.basename(p).startswith(("_", "MEMORY"))])
    est = len(glob.glob(os.path.join(STATE, "*.json")))
    tests = len(glob.glob(os.path.join(ROOT, "tests", "*.py")))
    return {"memorias": mem, "estado": est, "tests": tests}


def inventario():
    """Todo lo que hay, en un solo dict."""
    cs, hs, rs, sk = comites(), herramientas(), rutinas(), skills()
    mc, cj, me = mcps(), cajas(), memoria()
    ce, gu, co = cerebros(), guardas(), cola()
    return {
        "fecha": datetime.date.today().isoformat(),
        "comites": cs,
        "cerebros": ce,
        "herramientas": hs,
        "rutinas": rs,
        "skills": sk,
        "mcp": mc,
        "guardas": gu,
        "cola": co,
        "cajas": cj,
        "memoria": me,
        "numeros": {
            "comites": len(cs),
            "cerebros": len(ce),
            "cerebros_on": sum(1 for c in ce if c["encendido"]),
            "tools": hs["total"],
            "lineas": hs["lineas"],
            "rutinas_cargadas": sum(1 for r in rs if r["cargado"]),
            "rutinas_total": len(rs),
            "skills": len(sk),
            "skills_rotas": sum(1 for s in sk if not s["carga"]),
            "mcp": len(mc["locales"]) + len(mc["cuenta"]),
            "guardas": len(gu),
            "cajas": len(cj),
            "memorias": me["memorias"],
            "estado": me["estado"],
        },
    }


# ─── la huella: que un cambio en Polaris no deje el panel contando el de ayer ─
# El panel se empuja solo cada 5 min, pero solo sabe DERIVAR lo que puede leer del
# disco. Lo declarado aquí a mano (el camino de un encargo, el glosario, los
# conectores de cuenta, quién va reservado en la cara pública) no se entera de
# nada: si nace un comité nuevo y nadie lo mira, el panel sigue afirmando en
# silencio el sistema de la semana pasada — y en el peor caso saca por su nombre
# a un comité que delata un asunto privado.
#
# La huella es la superficie del sistema reducida a comparable. Si la de hoy no
# cuadra con la sellada, `tests/test_anatomia_al_dia.py` se pone rojo y obliga a
# mirar el panel antes de cerrar. Se cierra con `anatomia.py sellar`.
#
# La huella VA A GIT: por eso no lleva ni un nombre que salga de
# `00_FUENTE-DE-VERDAD/` (las cajas se cuentan, no se nombran — «viaje-{{CIUDAD}}-
# biopsia» en el historial es exactamente lo que el .gitignore evita). Todo lo
# demás que guarda (comités, tools, hooks, skills, rutinas) ya vive versionado.
HUELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "anatomia_huella.json")


def superficie(inv=None):
    """Qué RETRATA el panel, sin lo que cambia solo (trazas, gasto, cola)."""
    inv = inv or inventario()
    return {
        "comites": sorted(c["nombre"] for c in inv["comites"]),
        "familias": {f: len(v) for f, v in
                     sorted(inv["herramientas"]["familias"].items())},
        "rutinas": sorted(r["label"] for r in inv["rutinas"]),
        "skills": sorted(s["nombre"] for s in inv["skills"]),
        "guardas": sorted(inv["guardas"]),
        "cerebros": sorted(c["nombre"] for c in inv["cerebros"]),
        "mcp_locales": sorted(inv["mcp"]["locales"]),
        "mcp_cuenta": sorted(CONECTORES_CUENTA),
        "carriles": sorted(CARRILES),
        "camino": [fichero for _, fichero, _ in CAMINO],
        "glosario": sorted(casa for casa, _, _ in GLOSARIO),
        "reservado_publico": sorted(PUBLICO_RESERVADO),
        "reservado_tecnica": sorted(TECNICA_RESERVADO),
        "cadena_modelos": [list(c) for c in CADENA_MODELOS],
        "modelo_defecto": MODELO_POR_DEFECTO,
        "cajas": len(inv["cajas"]),  # contadas, NUNCA nombradas: van a git
    }


def huella_sellada():
    try:
        with open(HUELLA, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def diff_superficie(vieja, nueva):
    """Qué se movió, en líneas legibles. Vacío = el panel está al día."""
    if not vieja:
        return ["no hay huella sellada todavía"]
    fuera = []
    for clave in sorted(set(vieja) | set(nueva)):
        a, b = vieja.get(clave), nueva.get(clave)
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list) and \
                all(isinstance(x, str) for x in a + b):
            nuevos, idos = sorted(set(b) - set(a)), sorted(set(a) - set(b))
            if nuevos:
                fuera.append("%s · nuevo: %s" % (clave, ", ".join(nuevos)))
            if idos:
                fuera.append("%s · ya no está: %s" % (clave, ", ".join(idos)))
            if not nuevos and not idos:
                fuera.append("%s · cambió el orden" % clave)
        else:
            fuera.append("%s · %r → %r" % (clave, a, b))
    return fuera


# ─── pulso: qué está pasando ahora ───────────────────────────────────────────

def _trazas(dias=2):
    try:
        import observabilidad
        return observabilidad._leer_dias(dias)
    except Exception:
        return []


def _heartbeats():
    """Último latido conocido de cada daemon, en minutos."""
    d = os.path.join(STATE, "heartbeat")
    out = {}
    if not os.path.isdir(d):
        return out
    ahora = datetime.datetime.now().timestamp()
    for n in os.listdir(d):
        if not n.endswith(".json"):
            continue
        p = os.path.join(d, n)
        try:
            out[n[:-5]] = int((ahora - os.path.getmtime(p)) / 60)
        except OSError:
            pass
    return out


def pulso():
    """Qué late AHORA. Devuelve, por comité y por rutina, su estado reciente.

    encendido = actividad en los últimos 5 min · hoy = corrió hoy ·
    fallo = su última traza de hoy falló · dormido = ni rastro hoy.
    """
    ahora = datetime.datetime.now()
    hoy = ahora.date().isoformat()
    trazas = _trazas(2)

    por_agente = {}
    for r in trazas:
        ts = r.get("ts_ini") or ""
        ag = r.get("agente") or "?"
        d = por_agente.setdefault(ag, {"hoy": 0, "fallos": 0, "ultimo": "", "min": None})
        if ts[:10] == hoy:
            d["hoy"] += 1
            if r.get("resultado") == "fail":
                d["fallos"] += 1
        if ts > d["ultimo"]:
            d["ultimo"] = ts

    for ag, d in por_agente.items():
        try:
            t = datetime.datetime.fromisoformat(d["ultimo"])
            d["min"] = int((ahora - t).total_seconds() / 60)
        except (ValueError, TypeError):
            d["min"] = None
        if d["min"] is not None and d["min"] <= 5:
            d["estado"] = "encendido"
        elif d["fallos"]:
            d["estado"] = "fallo"
        elif d["hoy"]:
            d["estado"] = "hoy"
        else:
            d["estado"] = "dormido"

    hb = _heartbeats()
    ultimas = sorted([r for r in trazas if r.get("ts_ini")],
                     key=lambda r: r["ts_ini"], reverse=True)[:8]

    inv_com = {c["nombre"] for c in comites()}
    # Ojo: en las trazas también aparecen tools de fontanería (vigia, healthcheck)
    # que NO son comités. Se cuentan aparte para no inflar el "despiertos".
    despiertos = sorted(a for a, d in por_agente.items()
                        if d["estado"] == "encendido" and a in inv_com)
    fontaneria = sorted(a for a, d in por_agente.items()
                        if d["estado"] == "encendido" and a not in inv_com)
    return {
        "ahora": ahora.strftime("%H:%M"),
        "agentes": por_agente,
        "heartbeats": hb,
        "despiertos": despiertos,
        "fontaneria": fontaneria,
        "con_traza": sorted(inv_com & set(por_agente)),
        "sin_traza": sorted(inv_com - set(por_agente)),
        "ultimas": [{
            "ts": (r.get("ts_ini") or "")[11:16],
            "agente": r.get("agente", "?"),
            "job": r.get("job", "?"),
            "ok": r.get("resultado") == "ok",
        } for r in ultimas],
        "hoy_total": sum(d["hoy"] for d in por_agente.values()),
        "hoy_fallos": sum(d["fallos"] for d in por_agente.values()),
    }


# ─── la cara pública: fail-closed ────────────────────────────────────────────

CARAS = ("privada", "tecnica", "publica")
# Las caras que NO son de andar por casa: todo su texto pasa por el scrubber.
CARAS_FILTRADAS = ("publica", "tecnica")


def limpiar(texto, cara, reserva="(reservado)"):
    """Fuera de la cara privada, todo texto pasa por el mismo scrubber que la boca
    del sistema. Si trae léxico vetado, PII o algo clínico, NO se emite: se
    sustituye por su categoría. Fail-closed a propósito — si el scrubber no se
    puede importar, se corta igual (mejor una pieza sosa que una fuga).
    """
    if cara not in CARAS_FILTRADAS or not texto:
        return texto
    try:
        import _lexico_publico
        avisos = _lexico_publico.revisar(texto)
        terceros = _nombres_de_terceros()
    except Exception:
        return reserva
    if avisos:
        return reserva
    # El léxico no sabe quién es quién: «{{CONTACTO}}: se le CONSULTA…» pasaba entero,
    # cuando el repo público ya lo tacha (22-sep-26). Misma deny-list que publicar.py,
    # para que el panel no enseñe lo que el repo esconde.
    for n in terceros:
        texto = re.sub(r"(?<!\w)%s(?!\w)" % re.escape(n), "[contacto]", texto,
                       flags=re.I)
    return texto


def _nombres_de_terceros():
    """Los contactos que `publicar.py` pasa a {{CONTACTO}} en el repo público."""
    import publicar
    return publicar._nombres_de_terceros()


def _e(s):
    return html.escape(str(s) if s is not None else "")


def _num(n):
    """Miles con punto, como se escriben en español: 45599 → 45.599."""
    return "{:,}".format(int(n)).replace(",", ".")


def _pl(n, singular, plural=None):
    """Concordancia. _pl(1,'despierto') → '1 despierto'; _pl(3,…) → '3 despiertos'."""
    palabra = singular if abs(n) == 1 else (plural or singular + "s")
    return "%s %s" % (_num(n), palabra)


def _usd(x):
    """Dólares como se escriben en español: 4.85 → «4,85 $». Sin decimales cuando
    no los tiene (un tope de 60 se lee peor como «60,00»)."""
    try:
        centavos = int(round(float(x) * 100))
    except (TypeError, ValueError):
        return "?"
    entero, resto = divmod(centavos, 100)
    return ("%s $" % _num(entero)) if not resto \
        else ("%s,%02d $" % (_num(entero), resto))


_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _fecha(iso):
    try:
        d = datetime.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return "%d de %s de %d" % (d.day, _MESES[d.month - 1], d.year)


# ─── render ──────────────────────────────────────────────────────────────────

CAPAS = [
    ("entrada", "tú", "por donde entra todo"),
    ("orquestador", "orquestador", "lee la intención y reparte"),
    ("comites", "comités", "cada uno con su oficio"),
    ("saber", "lo que usan", "herramientas, conexiones, skills"),
    ("memoria", "lo que recuerdan", "memoria y estado"),
    ("rutinas", "lo que corre solo", "rutinas encendidas 24/7"),
]


def _svg(inv, pul, cara):
    """El mapa. Un nodo por capa, con su número real y su latido."""
    n = inv["numeros"]
    enc = len(pul["despiertos"])
    co = inv.get("cola") or {}
    # El mapa dejó de ser un censo por capas para ser EL CAMINO: cada ficha es un
    # eslabón por el que pasa un encargo, en el orden en que pasa. Cada `cid` tiene
    # que existir abajo como sección — un ancla a ninguna parte es una promesa rota.
    fichas = [
        ("camino", "entra una petición", "chat · Telegram · voz · correo",
         "como dato, nunca como orden", "quieto"),
        ("cola", "la cola", "un encargo = un fichero",
         (_pl(co.get("pending", 0), "esperando", "esperando")) if co else "",
         "encendido" if co.get("processing") else "quieto"),
        ("arquitecto", "el arnés agéntico", "arranca y razona el gabinete",
         "modelo por defecto: " + MODELO_POR_DEFECTO, "quieto"),
        ("comites", _pl(n["comites"], "subagente"), "cada uno con su oficio",
         (_pl(enc, "despierto") + " ahora") if enc else "",
         "encendido" if enc else "quieto"),
        ("llm", _pl(n["cerebros_on"], "LLM", "LLM") + " encendidos",
         "proveedores de inferencia",
         "%s de confianza"
         % _num(sum(1 for c in inv["cerebros"] if c["confianza"] and c["encendido"])),
         "quieto"),
        ("saber", _pl(n["tools"], "herramienta"),
         "%s · %s" % (_pl(n["mcp"], "conexión", "conexiones"),
                      _pl(n["skills"], "skill")),
         _pl(n["lineas"], "línea") + " de código", "quieto"),
        ("guardas", _pl(n["guardas"], "guardarraíl", "guardarraíles"),
         "antes de cada herramienta", "pueden denegarla", "quieto"),
        ("rutinas", _pl(n["rutinas_cargadas"], "rutina") + " encendidas",
         "trabajan sin que las llames",
         _pl(pul["hoy_total"], "ejecución", "ejecuciones") + " hoy", "encendido"),
        ("maquina", "una sola máquina", "un Mac mini, siempre encendido",
         "nadie entra: esto empuja", "quieto"),
    ]

    y, alto, hueco, partes = 96, 76, 30, []
    partes.append(
        '<rect x="24" y="52" width="852" height="%d" rx="16" class="muro"/>'
        % (len(fichas) * (alto + hueco) + 42))
    partes.append('<text x="44" y="80" class="muro-lab">el muro · nada sale de '
                  'aquí sin tu OK</text>')

    for i, (cid, titulo, sub, extra, estado) in enumerate(fichas):
        if i:
            partes.append('<line x1="450" y1="%d" x2="450" y2="%d" class="hilo" '
                          'marker-end="url(#p)"/>' % (y - hueco + 4, y - 8))
        # Enlace de verdad, no un onclick: funciona sin JavaScript, lo alcanza el
        # teclado solo y deja poner una CSP estricta (el panel no necesita script).
        partes.append(
            '<a href="#d-%s" aria-label="%s. %s">'
            '<g class="nodo %s" id="n-%s">'
            '<rect x="120" y="%d" width="660" height="%d" rx="12"/>'
            '<text x="152" y="%d" class="n-tit">%s</text>'
            '<text x="152" y="%d" class="n-sub">%s</text>'
            '%s</g></a>'
            % (cid, _e(titulo), _e(sub), estado, cid, y, alto, y + 33, _e(titulo),
               y + 56, _e(sub),
               ('<text x="748" y="%d" class="n-ext" text-anchor="end">%s</text>'
                % (y + 45, _e(extra))) if extra else ""))
        y += alto + hueco

    return ('<svg viewBox="0 0 900 %d" role="img" aria-labelledby="svg-t">'
            '<title id="svg-t">Mapa de Polaris por capas</title>'
            '<defs><marker id="p" viewBox="0 0 10 10" refX="8" refY="5" '
            'markerWidth="6" markerHeight="6" orient="auto">'
            '<path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
            'stroke-width="1.6" stroke-linecap="round"/></marker></defs>'
            '%s</svg>' % (y + 20, "".join(partes)))


def _reservada_en_tecnica(nombre):
    """¿Este daemon nombra un canal personal en vez de una pieza del sistema?"""
    return any(k in nombre for k in TECNICA_RESERVADO)


def _listas(inv, pul, cara):
    """El detalle debajo del mapa: qué hay exactamente dentro de cada capa, y —para
    quien no vive aquí dentro— cómo está montado."""
    b = []

    def bloque(cid, titulo, chips, nota=""):
        b.append('<section class="det" id="d-%s"><h2>%s</h2>%s<div class="chips">%s'
                 '</div></section>'
                 % (cid, _e(titulo),
                    ('<p class="nota">%s</p>' % _e(nota)) if nota else "",
                    "".join(chips)))

    def bloque_libre(cid, titulo, cuerpo, nota=""):
        """Igual, para lo que no son chips (el camino, el glosario, las tablas)."""
        b.append('<section class="det" id="d-%s"><h2>%s</h2>%s%s</section>'
                 % (cid, _e(titulo),
                    ('<p class="nota">%s</p>' % _e(nota)) if nota else "", cuerpo))

    est = {"encendido": "on", "hoy": "hoy", "fallo": "mal", "dormido": ""}
    n = inv["numeros"]

    # ── el camino de un encargo ──────────────────────────────────────────────
    # Va primero a propósito: es la única sección que contesta «cómo funciona»
    # en vez de «cuánto hay». Todo lo demás de la página es el censo.
    pasos = []
    for i, (nombre, fichero, que_hace) in enumerate(CAMINO, 1):
        pasos.append('<li class="paso"><span class="paso-n">%d</span>'
                     '<div><b>%s</b> <code>%s</code><p>%s</p></div></li>'
                     % (i, _e(nombre), _e(fichero), _e(limpiar(que_hace, cara))))
    bloque_libre("camino", "el camino de un encargo",
                 '<ol class="camino">%s</ol>' % "".join(pasos),
                 "de dónde sale una petición y por dónde pasa antes de que algo "
                 "ocurra. Cada eslabón puede parar el siguiente")

    # ── el arnés agéntico ────────────────────────────────────────────────────
    arq = arquitectos()
    if not arq:
        # La sección sale igual: el mapa de arriba enlaza aquí, y un ancla que no
        # lleva a ninguna parte es peor que una sección que dice que no sabe.
        bloque("arquitecto", "el arnés agéntico",
               ['<span class="chip mal">no se pudo leer el registro</span>'])
    else:
        chips = []
        for a in arq:
            etiq = [a["arnes"]]
            etiq.append("de confianza" if a["confianza"] else "fuera del muro")
            etiq.append("API medida" if a["medido"] else "por suscripción")
            if a["nivel_min"]:
                etiq.append("desde %s" % a["nivel_min"])
            if not a["encendido"]:
                etiq.append("apagado")
            chips.append('<span class="chip %s">%s <b>%s</b></span>'
                         % ("on" if a["encendido"] else "apagada",
                            _e(a["nombre"]), _e(" · ".join(etiq))))
        cadena = " → ".join(CADENA_MODELOS[1])
        bloque("arquitecto", "el arnés agéntico", chips,
               "quién arranca y razona el gabinete: es una caja intercambiable. "
               "El modelo por defecto del lazo es %s y degrada en cadena (%s) si "
               "topa un límite. Los guardarraíles NO dependen de quién orqueste: "
               "viven en hooks y en el gate de egress, no en el prompt"
               % (MODELO_POR_DEFECTO, cadena))

    chips, reservados = [], 0
    for c in inv["comites"]:
        if cara in CARAS_FILTRADAS and (c["nombre"] in PUBLICO_RESERVADO
                                        or limpiar(c["nombre"], cara, "") == ""):
            reservados += 1
            continue
        e = pul["agentes"].get(c["nombre"], {}).get("estado", "dormido")
        chips.append('<span class="chip %s" data-ag="%s" title="%s">%s</span>'
                     % (est[e], _e(c["nombre"]),
                        _e(limpiar(c["oficio"], cara, "")), _e(c["nombre"])))
    if reservados:
        # Un solo chip honesto en vez de N chips iguales que parecen un bug.
        chips.append('<span class="chip declarado">y %s que no enseño</span>'
                     % _pl(reservados, "comité", "comités"))
    # La cobertura, dicha en alto: si de 35 solo han dejado rastro 6, el mapa no
    # es que esté apagado, es que la mayoría trabaja sin que quede constancia.
    vistos = len(pul["con_traza"])
    nota = "%s de %s han dejado rastro estos dos días" % (
        _num(vistos), _num(n["comites"]))
    if cara == "privada" and pul["sin_traza"]:
        nota += " · los otros %s trabajan sin registro" % _num(len(pul["sin_traza"]))
    bloque("comites", _pl(n["comites"], "comité", "comités") + " · subagentes",
           chips, nota)

    # ── los LLM, uno a uno ───────────────────────────────────────────────────
    # Antes era un chip con dos etiquetas. Aquí sale el MODELO exacto y el carril
    # para el que se le llama, que es lo que de verdad se viene a mirar.
    filas = []
    for c in inv["cerebros"]:
        # El carril clínico puede pedir modelos MÁS potentes que el normal (Claude
        # sirve sonnet de diario y opus para lo del caso). Juntarlos en una lista
        # borraba justo esa diferencia, y repetía nombres.
        modelos = ", ".join(c["modelos"]) or "—"
        extra = [m for m in c["modelos_clinico"] if m not in c["modelos"]]
        if extra:
            modelos += " · en lo sensible %s" % ", ".join(extra)
        sellos = ["gratis" if c["gratis"] else "de pago",
                  "de confianza" if c["confianza"] else "fuera del muro"]
        if c["local"]:
            sellos.insert(0, "en esta máquina")
        if c["solo_carril"]:
            sellos.append("fuera del relevo general")
        if not c["encendido"]:
            sellos.append("APAGADO")
        carriles = ", ".join(c["carriles"]) or "la cadena general"
        filas.append(
            '<tr class="%s"><td><b>%s</b></td><td><code>%s</code></td>'
            '<td>%s</td><td>%s</td><td class="num">%s</td></tr>'
            % ("" if c["encendido"] else "apagada", _e(c["nombre"]), _e(modelos),
               _e(carriles), _e(" · ".join(sellos)),
               _e(c["capacidad"] if c["capacidad"] is not None else "—")))
    tabla = ('<div class="tabla"><table><thead><tr><th>proveedor</th>'
             '<th>modelo</th><th>para qué</th><th>condiciones</th>'
             '<th class="num">capacidad</th></tr></thead><tbody>%s</tbody>'
             '</table></div>' % "".join(filas))
    bloque_libre(
        "llm", "los LLM · %s de %s encendidos"
        % (_num(n["cerebros_on"]), _pl(n["cerebros"], "proveedor", "proveedores")),
        tabla,
        "en la casa se les llama «cerebros»; son proveedores de inferencia "
        "registrados, no procesos vivos. La capacidad (1-9) es lo que ordena el "
        "failover. Lo sensible SOLO va a los de confianza, por bueno que sea el "
        "otro — y eso no lo decide el modelo, lo decide el gate de egress")

    # ── qué carril atiende quién ─────────────────────────────────────────────
    rt = ruteo()
    if rt:
        filas = []
        for r in rt:
            luego = (" <span class='luego'>luego %s</span>" % _e(", ".join(r["luego"]))) \
                if r["luego"] else ""
            filas.append('<tr><td><b>%s</b></td><td>%s</td>'
                         '<td>%s%s</td></tr>'
                         % (_e(r["carril"]), _e(limpiar(r["que_es"], cara)),
                            _e(", ".join(r["especialistas"]) or "—"), luego))
        bloque_libre(
            "ruteo", "qué carril atiende quién",
            '<div class="tabla"><table><thead><tr><th>carril</th><th>qué es</th>'
            '<th>quién lo coge</th></tr></thead><tbody>%s</tbody></table></div>'
            % "".join(filas),
            "el carril se decide con expresiones regulares antes de llamar a nadie: "
            "es determinista, no gasta un token y no puede alucinar su propia "
            "decisión. Esta tabla no está escrita a mano, se deriva del registro")

    chips = ['<span class="chip">%s <b>%s</b></span>' % (_e(f), _num(len(v)))
             for f, v in inv["herramientas"]["familias"].items()]
    bloque("saber", _pl(n["tools"], "herramienta") + ", por familia", chips)

    if cara == "publica":
        chips = ['<span class="chip">bases de datos de biología <b>%s</b></span>'
                 % _num(len(inv["mcp"]["locales"])),
                 '<span class="chip declarado">del resto de mi vida digital <b>%s'
                 '</b></span>' % _num(len(inv["mcp"]["cuenta"]))]
    else:
        # Por el scrubber también aquí: un conector se llama como quien lo puso, y
        # basta con que alguien registre uno con una palabra vetada para que se
        # publique sola. Fail-closed por diseño, no porque yo lo haya mirado hoy.
        chips = ['<span class="chip">%s</span>' % _e(limpiar(m, cara))
                 for m in inv["mcp"]["locales"]]
        chips += ['<span class="chip declarado">%s</span>' % _e(limpiar(m, cara))
                  for m in inv["mcp"]["cuenta"]]
    bloque("mcp", _pl(n["mcp"], "conexión", "conexiones"), chips,
           "lo verificado se lee del disco; lo punteado son conectores que viven "
           "en la cuenta y no se pueden derivar leyendo el repo — van declarados")

    chips = ['<span class="chip %s">%s%s</span>'
             % ("" if s["carga"] else "mal", _e(s["nombre"]),
                "" if s["carga"] else " · no carga") for s in inv["skills"]]
    bloque("skills", _pl(n["skills"], "skill") + " nuestras", chips,
           "procedimientos escritos que el agente carga solo cuando la tarea los "
           "pide, en vez de llevarlos siempre encima")

    bloque("memoria", "lo que recuerda",
           ['<span class="chip">%s</span>' % _pl(n["memorias"], "memoria"),
            '<span class="chip">%s de estado</span>'
            % _pl(n["estado"], "fichero"),
            '<span class="chip">%s</span>'
            % _pl(inv["memoria"]["tests"], "test")],
           "una memoria = un fichero = un hecho, con su índice. Se inyectan por "
           "relevancia al empezar; las que no pueden depender de eso van siempre")

    titulo_rut = "%s de %s rutinas cargadas" % (_num(n["rutinas_cargadas"]),
                                                _num(n["rutinas_total"]))
    if cara == "publica":
        # En público las rutinas van por familia, NO por nombre: el nombre de un
        # daemon no le dice nada a quien mira y sí cuenta de más sobre la vida
        # digital de {{TITULAR}}.
        fams = {}
        for r in inv["rutinas"]:
            if r["cargado"]:
                fams[_familia_de(r["nombre"].replace("-", "_"))] = \
                    fams.get(_familia_de(r["nombre"].replace("-", "_")), 0) + 1
        chips = ['<span class="chip">%s <b>%s</b></span>' % (_e(f), _num(c))
                 for f, c in sorted(fams.items(), key=lambda kv: -kv[1])]
    else:
        chips, calladas = [], 0
        for r in inv["rutinas"]:
            # En técnica el nombre del daemon SÍ se enseña —es arquitectura— salvo
            # el puñado que nombra un canal personal en vez de una pieza.
            if cara == "tecnica" and _reservada_en_tecnica(r["nombre"]):
                calladas += 1
                continue
            hb = pul["heartbeats"].get(r["nombre"])
            t = ("último latido hace %s" % _pl(hb, "minuto")) if hb is not None \
                else ("cargada, sin latido propio" if r["cargado"]
                      else "en el registro pero apagada")
            chips.append('<span class="chip %s" title="%s">%s</span>'
                         % ("on" if r["cargado"] else "apagada", _e(t),
                            _e(r["nombre"])))
        if calladas:
            chips.append('<span class="chip declarado">y %s de canales míos</span>'
                         % _pl(calladas, "rutina"))
    bloque("rutinas", titulo_rut, chips)

    chips = ['<span class="chip">%s</span>' % _e(g) for g in inv["guardas"]]
    bloque("guardas", _pl(n["guardas"], "guarda") + " del muro · hooks", chips,
           "se enganchan a un evento del arnés y se meten en medio de cada "
           "herramienta, antes de que pase nada. No son un prompt que pide "
           "portarse bien: son código que devuelve «denegado» y corta")

    if inv["cajas"]:
        # En público, SOLO el número. Una caja se llama como el trozo de vida al
        # que sirve («viaje-{{CIUDAD}}-prueba»), así que ahí no hay nada seguro por
        # defecto. Y el scrubber no basta: no veta «biopsia» a secas, solo junto
        # a un patrón de variante. Se cierra por diseño, no por confiar en él.
        if cara in CARAS_FILTRADAS:
            chips = ['<span class="chip">por objetivo <b>%s</b></span>'
                     % _num(n["cajas"])]
        else:
            chips = ['<span class="chip">%s</span>' % _e(c) for c in inv["cajas"]]
        bloque("cajas", _pl(n["cajas"], "caja"), chips,
               "mini-sistemas por objetivo, heredan el muro entero")

    co = inv["cola"]
    chips = ['<span class="chip %s">%s <b>%s</b></span>'
             % ("mal" if k == "failed" and v else "", _e(nom), _num(v))
             for k, nom in (("pending", "esperando"), ("processing", "en curso"),
                            ("failed", "caídos"), ("done", "hechos"))
             for v in [co.get(k, 0)]] \
        if co else ['<span class="chip mal">no se pudo leer la cola</span>']
    bloque("cola", "la cola de encargos", chips,
           "un encargo es un fichero y su estado es la carpeta donde vive; cada "
           "cambio de estado es un rename atómico. Se despacha de uno en uno")

    # ── la máquina ───────────────────────────────────────────────────────────
    mq = maquina()
    if mq:
        chips = []
        if mq.get("chip"):
            chips.append('<span class="chip">%s <b>%s</b></span>'
                         % (_e(mq["chip"]),
                            _e("%d núcleos (%d + %d)" % (mq["nucleos"],
                                                         mq["nucleos_potencia"],
                                                         mq["nucleos_eficiencia"]))
                            if mq.get("nucleos_potencia") else
                            _e("%d núcleos" % mq.get("nucleos", 0))))
        if mq.get("ram_gb"):
            chips.append('<span class="chip">memoria <b>%d GB</b></span>' % mq["ram_gb"])
        if mq.get("disco_gb"):
            chips.append('<span class="chip">disco <b>%d de %d GB libres</b></span>'
                         % (mq["disco_libre_gb"], mq["disco_gb"]))
        if mq.get("encendida_dias") is not None:
            chips.append('<span class="chip">lleva encendida <b>%s</b></span>'
                         % _pl(mq["encendida_dias"], "día"))
        chips.append('<span class="chip %s">LLM local <b>%s</b></span>'
                     % ("on" if mq["llm_local_vivo"] else "apagada",
                        "levantado" if mq["llm_local_vivo"] else "parado"))
        bloque("maquina", "la máquina", chips,
               "un Mac mini de sobremesa encendido todo el día. Aquí vive TODO: la "
               "fuente de verdad, las claves, el lazo y el modelo local. El portátil "
               "es una cabina que manda y consulta pero no almacena, y el móvil entra "
               "por Telegram")
        bloque("red", "y cómo se llega hasta aquí",
               ['<span class="chip">nada escucha hacia fuera</span>',
                '<span class="chip">el panel se EMPUJA, cifrado</span>',
                '<span class="chip">la clave no está en el servidor</span>'],
               "no hay ningún puerto ni túnel abierto hacia esta máquina. Lo que se "
               "publica sale de aquí ya cerrado: quien lo sirve no sabe abrirlo, y "
               "quien lo abre es el navegador de quien tiene la frase")

    # ── lo que cuesta ────────────────────────────────────────────────────────
    if cara in ("privada", "tecnica"):
        gs = gasto()
        if gs:
            chips = []
            if gs.get("hoy_usd") is not None:
                chips.append('<span class="chip">hoy <b>%s de %s</b></span>'
                             % (_usd(gs["hoy_usd"]), _usd(gs.get("tope_dia_usd"))))
            if gs.get("mes_usd") is not None:
                chips.append('<span class="chip">este mes <b>%s de %s</b></span>'
                             % (_usd(gs["mes_usd"]), _usd(gs.get("tope_mes_usd"))))
            if gs.get("tope_job_usd") is not None:
                chips.append('<span class="chip">tope de un encargo <b>%s</b></span>'
                             % _usd(gs["tope_job_usd"]))
            if gs.get("tokens_hoy"):
                chips.append('<span class="chip">tokens hoy <b>%s M</b></span>'
                             % _num(round(gs["tokens_hoy"] / 1e6)))
            if gs.get("tokens_total"):
                chips.append('<span class="chip">tokens desde el principio '
                             '<b>%s M</b></span>'
                             % _num(round(gs["tokens_total"] / 1e6)))
            for m in gs.get("modelos", []):
                chips.append('<span class="chip">%s <b>%s M</b></span>'
                             % (_e(m["modelo"]), _num(round(m["tokens"] / 1e6))))
            bloque("gasto", "lo que cuesta tenerlo en pie", chips,
                   "el dólar dice poco: casi todo el trabajo va por suscripción, "
                   "donde el coste marginal de una llamada es ~0, así que lo "
                   "honesto es mirar el VOLUMEN (millones de tokens, contando "
                   "también las ramas de trabajo, que es donde se construye). El "
                   "tope no es decorativo: cuando se toca, el lazo aplaza el "
                   "encargo en vez de servirlo a medias")

    # ── el glosario, al final, como referencia ───────────────────────────────
    filas = ['<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>'
             % (_e(casa), _e(estandar), _e(limpiar(que_es, cara)))
             for casa, estandar, que_es in GLOSARIO]
    bloque_libre("glosario", "cómo se llama cada cosa",
                 '<div class="tabla"><table><thead><tr><th>en esta casa</th>'
                 '<th>término estándar</th><th>qué es</th></tr></thead><tbody>%s'
                 '</tbody></table></div>' % "".join(filas),
                 "el sistema se construyó con metáforas para que {{TITULAR}} lo pudiera "
                 "pensar mientras estaba en tratamiento. Los dos nombres valen; el "
                 "de la derecha es el que se usa fuera")

    return "".join(b)


CSS = """
:root{--bg:#FAF6F0;--card:#F5EFE6;--tx:#2D1B3D;--tx2:#3A3340;--mi:#A855B5;
--mis:#E8D4ED;--cta:#FF6B47;--bd:#DCD2C6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;
line-height:1.6}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px 64px}
h1{font-family:Georgia,serif;font-size:34px;font-weight:400;margin:0 0 4px}
.sub{color:var(--tx2);margin:0 0 28px;font-size:15px}
.pulso{display:inline-flex;align-items:center;gap:8px;background:var(--card);
border:1px solid var(--bd);border-radius:9999px;padding:6px 14px;font-size:14px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--mi)}
.dot.late{animation:lat 1.6s ease-in-out infinite}
@keyframes lat{0%,100%{opacity:1}50%{opacity:.25}}
svg{width:100%;height:auto;margin:8px 0 24px}
.muro{fill:none;stroke:var(--bd);stroke-width:1.5;stroke-dasharray:7 6}
.muro-lab{fill:var(--tx2);font-size:14px}
.hilo{stroke:var(--bd);stroke-width:1.5}
.nodo rect{fill:var(--card);stroke:var(--bd);stroke-width:1}
svg a{cursor:pointer}
svg a:hover .nodo rect{stroke:var(--mi);stroke-width:2}
svg a:focus-visible{outline:2px solid var(--mi);outline-offset:2px}
.nodo.encendido rect{fill:var(--mis);stroke:var(--mi)}
.n-tit{fill:var(--tx);font-size:19px}
.n-sub{fill:var(--tx2);font-size:14px}
.n-ext{fill:var(--mi);font-size:14px}
.det{margin:0 0 28px;scroll-margin-top:16px}
.det h2{font-size:17px;font-weight:500;margin:0 0 10px;
border-bottom:1px solid var(--bd);padding-bottom:6px}
.nota{color:var(--tx2);font-size:13px;margin:-2px 0 10px}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{background:var(--card);border:1px solid var(--bd);border-radius:9999px;
padding:5px 12px;font-size:14px;min-height:32px;display:inline-flex;
align-items:center;color:var(--tx)}
.chip.on{background:var(--mis);border-color:var(--mi);color:#3D1147}
.chip.hoy{border-color:var(--mi)}
.chip.mal{border-color:var(--cta);color:#7A2A16}
.chip.apagada{border-style:dashed;color:var(--tx2)}
.chip.declarado{border-style:dashed}
.chip b{font-weight:500;margin-left:6px;color:var(--mi)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;
color:var(--tx2);background:var(--card);border-radius:5px;padding:1px 5px}
/* El camino de un encargo: es lo primero que lee quien viene de fuera, así que
   se numera y respira. Sin JS: el número es un span, no un contador de lista. */
ol.camino{list-style:none;margin:0;padding:0;counter-reset:none}
li.paso{display:flex;gap:14px;padding:11px 0;border-bottom:1px solid var(--bd)}
li.paso:last-child{border-bottom:0}
li.paso>div{flex:1;min-width:0}
li.paso p{margin:3px 0 0;color:var(--tx2);font-size:14px}
.paso-n{flex:0 0 26px;height:26px;border-radius:50%;background:var(--mis);
color:#3D1147;font-size:13px;display:flex;align-items:center;
justify-content:center;margin-top:2px}
/* Las tablas scrollean DENTRO de su caja: en el móvil, que es donde se mira el
   panel, una tabla ancha arrastraba la página entera de lado. */
.tabla{overflow-x:auto;border:1px solid var(--bd);border-radius:12px;
background:var(--card)}
.tabla table{border-collapse:collapse;width:100%;font-size:14px}
.tabla th,.tabla td{text-align:left;padding:8px 12px;vertical-align:top;
border-bottom:1px solid var(--bd)}
.tabla th{font-weight:500;color:var(--tx2);font-size:13px;white-space:nowrap}
.tabla tbody tr:last-child td{border-bottom:0}
.tabla tr.apagada td{color:var(--tx2);opacity:.65}
.tabla .num{text-align:right;font-variant-numeric:tabular-nums}
.luego{color:var(--tx2);font-size:13px}
.ticker{background:var(--card);border:1px solid var(--bd);border-radius:12px;
padding:14px 18px;font-size:14px}
.ticker li{list-style:none;display:flex;gap:12px;padding:3px 0}
.ticker ul{margin:8px 0 0;padding:0}
.ticker .t{color:var(--tx2);font-variant-numeric:tabular-nums}
/* Lo que acaba de pasar tiene que VERSE pasar: si el color final es el mismo
   antes y después, en un vídeo no se percibe nada. Estas dos animaciones son
   toda la diferencia entre un mapa vivo y una captura de pantalla. */
@keyframes fogonazo{0%{background:var(--mi);color:#fff;transform:scale(1.06)}
100%{background:var(--card);color:var(--tx);transform:scale(1)}}
.chip.acaba{animation:fogonazo 2.6s ease-out}
@keyframes entra{0%{opacity:0;transform:translateX(-10px)}
100%{opacity:1;transform:translateX(0)}}
.ticker li.fresco{animation:entra .5s ease-out}
.ticker li.fresco .t{color:var(--mi)}
@media (prefers-reduced-motion:reduce){
.chip.acaba,.ticker li.fresco{animation:none}
.chip.acaba{outline:2px solid var(--mi)}}
.pie{color:var(--tx2);font-size:13px;margin-top:32px;
border-top:1px solid var(--bd);padding-top:14px}
.alerta{color:#7A2A16}
.vert .wrap{max-width:1080px;padding:56px 48px}
.vert h1{font-size:56px}
.vert .n-tit{font-size:30px}
.vert .n-sub,.vert .n-ext{font-size:22px}
.vert .chip{font-size:20px;padding:9px 18px}
.vert .tabla table,.vert li.paso p{font-size:19px}
.intro{background:var(--card);border:1px solid var(--bd);border-left:3px solid
var(--mi);border-radius:12px;padding:16px 20px;margin:0 0 26px;font-size:15px}
.intro p{margin:0 0 8px}
.intro p:last-child{margin:0}
"""

JS = """
function pl(n,s,p){return n+' '+(n===1?s:(p||s+'s'));}
var visto={}, arranque=true;
function refrescar(){
  fetch('/api/pulso').then(function(r){return r.json()}).then(function(p){
    var e=p.despiertos.length;
    var b=document.getElementById('pulso');
    if(b)b.textContent=p.ahora+' · '+pl(e,'comité despierto','comités despiertos')
      +' · '+pl(p.hoy_total,'ejecución','ejecuciones')+' hoy';
    var d=document.querySelector('.dot');
    if(d)d.className='dot'+(e?' late':'');

    var mapa={encendido:'on',hoy:'hoy',fallo:'mal',dormido:''};
    document.querySelectorAll('.chip[data-ag]').forEach(function(c){
      var a=p.agentes[c.dataset.ag], nuevo=a?mapa[a.estado]:'';
      // Fogonazo SOLO cuando de verdad cambia de estado. Sin esto no se ve
      // nada pasar: el color final es idéntico antes y después.
      if(!arranque && visto[c.dataset.ag]!==undefined && visto[c.dataset.ag]!==nuevo){
        c.classList.add('acaba');
        setTimeout(function(){c.classList.remove('acaba');},2600);
      }
      visto[c.dataset.ag]=nuevo;
      c.className='chip '+nuevo+(c.classList.contains('acaba')?' acaba':'');
    });

    var nodo=document.getElementById('n-comites');
    if(nodo)nodo.setAttribute('class','nodo '+(e?'encendido':'quieto'));

    // El registro de lo último: es la parte que se lee como una película, así
    // que se repinta entero y lo nuevo entra marcado.
    var ul=document.getElementById('ultimas');
    if(ul&&p.ultimas){
      var antes={};
      ul.querySelectorAll('li').forEach(function(li){antes[li.dataset.k]=1;});
      ul.innerHTML=p.ultimas.map(function(u){
        var k=u.ts+'|'+u.agente+'|'+u.job;
        var fresco=(!arranque&&!antes[k])?' class="fresco"':'';
        return '<li data-k="'+k+'"'+fresco+'><span class="t">'+u.ts+'</span>'
          +'<span>'+u.agente+' · '+u.job+'</span><span>'
          +(u.ok?'ok':'falló')+'</span></li>';
      }).join('');
    }
    arranque=false;
  }).catch(function(){});
}
refrescar();
setInterval(refrescar,5000);
"""


def _intro(cara, inv):
    """Cuatro líneas de encuadre. Solo en la cara técnica: quien la abre no vive
    aquí dentro y necesita saber qué está mirando antes del primer número."""
    if cara != "tecnica":
        return ""
    n = inv["numeros"]
    parrafos = [
        # Sin el diagnóstico: el scrubber lo vetaría (y con razón), y para "cómo "
        # está montado" no hace ninguna falta. Se dice el ENCARGO, no la historia.
        "Polaris es el sistema personal con el que una ingeniera lleva su propio "
        "caso médico: buscar la mejor ruta de tratamiento, sostener la evidencia "
        "que la respalda, gestionar el día a día y no perder nada por el camino. "
        "No es un producto ni una demo — lleva %s encendido y trabaja de noche."
        % _pl(maquina().get("encendida_dias", 0), "día"),
        "Lo que hay debajo: %s con su propio oficio, %s propias, %s que corren "
        "solas y %s de inferencia entre los que el sistema elige según la "
        "tarea. Todo en una máquina de sobremesa, sin nube propia y sin un solo "
        "puerto abierto hacia dentro."
        % (_pl(n["comites"], "subagente"), _pl(n["tools"], "herramienta"),
           _pl(n["rutinas_cargadas"], "rutina"),
           _pl(n["cerebros_on"], "proveedor", "proveedores")),
        "Esta página se cuenta sola: no hay nada escrito a mano en los números, "
        "los lee del sistema vivo cada vez que se abre. Y está recortada a "
        "propósito — el caso clínico, los contactos y los canales privados no "
        "salen de la máquina.",
    ]
    return '<div class="intro">%s</div>' % "".join(
        '<p>%s</p>' % _e(limpiar(p, cara)) for p in parrafos)


def render(cara="privada", vertical=False, vivo=False, inv=None, pul=None):
    """El HTML entero, autocontenido. Sin red, sin CDN, sin fuentes externas."""
    inv = inv if inv is not None else inventario()
    pul = pul if pul is not None else pulso()
    n = inv["numeros"]
    enc = len(pul["despiertos"])

    ult = ""
    if cara == "privada" and pul["ultimas"]:
        # `data-k` identifica cada línea para que el refresco sepa cuál es NUEVA
        # y la marque; sin eso el registro cambia sin que se note.
        filas = "".join(
            '<li data-k="%s"><span class="t">%s</span><span>%s · %s</span>'
            '<span>%s</span></li>'
            % (_e("%s|%s|%s" % (u["ts"], u["agente"], u["job"])),
               _e(u["ts"]), _e(u["agente"]), _e(u["job"]),
               "ok" if u["ok"] else "falló") for u in pul["ultimas"])
        ult = ('<section class="det"><h2>lo último que ha pasado</h2>'
               '<div class="ticker"><ul id="ultimas">%s</ul></div></section>'
               % filas)

    aviso = ""
    if n["skills_rotas"]:
        aviso = (' · <span class="alerta">%s sin cargar</span>'
                 % _pl(n["skills_rotas"], "skill"))

    return (
        '<!doctype html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>La Anatomía · Polaris</title><style>%s</style></head>'
        '<body class="%s"><div class="wrap">'
        '<h1>La Anatomía</h1>'
        '<p class="sub">cómo funciona esto por dentro, a %s%s</p>'
        '<p class="pulso"><span class="dot%s"></span>'
        '<span id="pulso">%s · %s · %s hoy</span></p>'
        '%s%s%s%s'
        '<p class="pie">Se cuenta solo, leyendo el sistema. Cara: %s.</p>'
        '</div>%s</body></html>'
        % (CSS, "vert" if vertical else "", _e(_fecha(inv["fecha"])), aviso,
           " late" if enc else "", _e(pul["ahora"]),
           _pl(enc, "comité despierto", "comités despiertos"),
           _pl(pul["hoy_total"], "ejecución", "ejecuciones"),
           _intro(cara, inv),
           _svg(inv, pul, cara), _listas(inv, pul, cara), ult,
           cara,
           # El script SOLO cuando hay servidor detrás que sirva /api/pulso. En
           # el fichero suelto y en el panel no pinta nada, y así la página se
           # sirve con una CSP estricta que no permite scripts.
           ("<script>%s</script>" % JS) if vivo else ""))


# ─── servidor ────────────────────────────────────────────────────────────────

def serve(puerto=8791, cara="privada"):
    """Solo 127.0.0.1. Nunca 0.0.0.0 (mismo criterio que El Observatorio)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def _envia(self, cuerpo, tipo="text/html; charset=utf-8"):
            b = cuerpo.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            ruta = self.path.split("?")[0]
            if ruta == "/api/pulso":
                return self._envia(json.dumps(pulso(), ensure_ascii=False),
                                   "application/json; charset=utf-8")
            if ruta == "/api/inventario":
                return self._envia(json.dumps(inventario(), ensure_ascii=False),
                                   "application/json; charset=utf-8")
            c = cara
            for posible in CARAS:
                if "cara=" + posible in self.path:
                    c = posible
            self._envia(render(cara=c, vertical="vertical" in self.path, vivo=True))

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", puerto), H)
    print("La Anatomía en http://127.0.0.1:%d  (cara: %s)" % (puerto, cara))
    srv.serve_forever()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv):
    cmd = argv[0] if argv else "inventario"
    op = argv[1:]

    def val(flag, defecto=None):
        return op[op.index(flag) + 1] if flag in op and op.index(flag) + 1 < len(op) else defecto

    if cmd == "inventario":
        inv = inventario()
        if "--json" in op:
            print(json.dumps(inv, ensure_ascii=False, indent=2))
            return 0
        n = inv["numeros"]
        print("La Anatomía de Polaris —", inv["fecha"])
        print("  %-24s %d" % ("comités", n["comites"]))
        print("  %-24s %d de %d encendidos" % ("cerebros (LLM)", n["cerebros_on"],
                                               n["cerebros"]))
        print("  %-24s %d  (%s líneas)" % ("herramientas", n["tools"], n["lineas"]))
        print("  %-24s %d de %d cargadas" % ("rutinas 24/7", n["rutinas_cargadas"],
                                             n["rutinas_total"]))
        print("  %-24s %d  (%d no cargan)" % ("skills nuestras", n["skills"],
                                              n["skills_rotas"]))
        print("  %-24s %d" % ("conexiones MCP", n["mcp"]))
        print("  %-24s %d" % ("guardas del muro", n["guardas"]))
        print("  %-24s %d" % ("memorias", n["memorias"]))
        print("  %-24s %d" % ("ficheros de estado", n["estado"]))
        print("  %-24s %d" % ("cajas", n["cajas"]))
        return 0

    if cmd == "pulso":
        p = pulso()
        print("Pulso a las %s — %d ejecuciones hoy, %d fallos"
              % (p["ahora"], p["hoy_total"], p["hoy_fallos"]))
        for ag, d in sorted(p["agentes"].items(), key=lambda kv: -kv[1]["hoy"]):
            print("  %-22s %-10s hoy:%-4d hace %s min"
                  % (ag, d["estado"], d["hoy"],
                     d["min"] if d["min"] is not None else "?"))
        print("  comités CON traza: %d · SIN traza: %d"
              % (len(p["con_traza"]), len(p["sin_traza"])))
        return 0

    if cmd == "sellar":
        # Sellar es decir "he MIRADO el panel y cuenta el sistema de hoy". Por eso
        # imprime primero qué se movió: firmar sin leer no vale de nada.
        nueva = superficie()
        movido = diff_superficie(huella_sellada(), nueva)
        for ln in movido:
            print("  " + ln)
        if "--ver" in op:
            return 0 if not movido else 1
        if not movido:
            print("nada que sellar: el panel ya está al día")
            return 0
        with open(HUELLA, "w", encoding="utf-8") as f:
            json.dump(nueva, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print("sellado: %s" % HUELLA)
        return 0

    if cmd == "render":
        c = val("--cara", "privada")
        if c not in CARAS:
            print("cara desconocida: %s (hay %s)" % (c, ", ".join(CARAS)),
                  file=sys.stderr)
            return 2
        h = render(cara=c, vertical="--vertical" in op)
        destino = val("-o")
        if destino:
            with open(destino, "w", encoding="utf-8") as f:
                f.write(h)
            print(destino)
        else:
            sys.stdout.write(h)
        return 0

    if cmd == "serve":
        return serve(int(val("--puerto", 8791)), val("--cara", "privada"))

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
