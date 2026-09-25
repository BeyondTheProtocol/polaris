#!/usr/bin/env python3
"""tools/enruta.py — decide QUÉ LLM (o cuáles) usar para cada tarea.

{{TITULAR}}, 27-jul-2026: «necesito que Polaris tenga todos los LLMs que nos lleven a NED y ya
elija cuándo necesita el que sea». Los tenía todos cableados desde hacía tiempo; lo que no
había era nadie que eligiera. Cada llamada estaba escrita a mano en el fichero donde tocaba,
así que un cambio de criterio obligaba a editar decenas de sitios y ninguno fallaba ruidoso.

Esto es esa pieza. No llama a nadie: DECIDE y devuelve a quién llamar y por qué. Quien ejecuta
sigue siendo la tool de siempre (grok.py, gemini.py, …), así que se puede adoptar poco a poco
sin reescribir nada, y se puede razonar sobre la decisión sin gastar un token.

ORDEN DE CRITERIOS, y el primero no se negocia:

  1. EL MURO. Si el contenido es sensible (clínico, genómico, PII, término vetado) según
     `borde.clasificar`, solo quedan destinos de confianza. Fail-closed: ante la duda, no sale.
     Esto no es una preferencia, es la regla inquebrantable, y por eso va antes que nada.
  2. CAPACIDAD. Cada proveedor tiene aquello para lo que es mejor, con su motivo escrito.
  3. SALUD. Un proveedor que no responde no se propone. Se mide de verdad, no se supone.
  4. COSTE o CALIDAD, segun lo que pida la tarea. Si pide volumen o barato, gana el gratis;
     en todo lo demas gana el mejor. Ordenar siempre por precio colaba un modelo mas flojo por
     delante del bueno en decisiones que importan, y ahorrar un dolar ahi es mal negocio
     (`feedback-coste-nunca-corta-ned-pide-aprobacion`, `feedback-estrategia-coste-precision`).
  5. PANEL. Para lo crítico no se elige uno: se piden varios y se contrastan. Modelos de casas
     distintas, que dos modelos de la misma familia se equivocan igual.

DESACOPLADO A PROPÓSITO (`feedback-desacoplar-siempre-de-claude-code`): stdlib pura, sin MCP,
sin depender del runtime. Si mañana no hay Claude Code, esto sigue decidiendo igual.

Uso:
  python3 enruta.py "buscar qué se ha publicado esta semana sobre FGFR4"
  python3 enruta.py --json "resumir estas 200 notas"           # para consumo programático
  python3 enruta.py --critico "¿esta diana es real?"           # panel de varios, no uno
  python3 enruta.py --contenido-de fichero.txt "clasifica"     # clasifica el contenido real
  python3 enruta.py --salud                                    # prueba viva de cada proveedor
  python3 enruta.py --ejecutar "busca X"                       # decide Y llama (la decision va a stderr)
  python3 enruta.py --ejecutar --todos --critico "..."         # llama a todo el panel

  (como módulo)  elegir(tarea, contenido=None, critico=False) -> Decision
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)



def _dir_estado():
    """Dónde vive la caché de salud: el estado de CASA BASE, compartido por todos los árboles.

    Hasta el 25-sep-2026 colgaba de `AQUI` (el `tools/` de cada árbol): cada worktree sondeaba
    por su cuenta a los proveedores de pago al caducar SU caché. Con ~20 worktrees vivos salían
    15-18 rondas por hora, casi todo el dinero real de API de ese día (medido en el registro
    completo de cost_guard). `BTP_STATE_DIR` aísla; un proceso que es un test, a un tmp."""
    try:
        from _casa import state_dir, es_proceso_de_test
        if not os.environ.get("BTP_STATE_DIR") and es_proceso_de_test():
            import tempfile
            return os.path.join(tempfile.gettempdir(), "btp-test-enruta-%d" % os.getuid())
        return state_dir()
    except Exception:
        return os.path.join(AQUI, "state")


CACHE_SALUD = os.path.join(_dir_estado(), "enruta_salud.json")
CACHE_TTL_S = 3600          # una hora: suficiente para no repetir la prueba en cada decisión

# ── Catálogo ────────────────────────────────────────────────────────────────────────────────
# `confianza`: si puede recibir contenido SENSIBLE. Solo lo que el borde considera trusted.
#   - claude: es el cerebro del sistema, el único de nube al que el muro deja lo clínico.
#   - local:  no sale de la máquina, así que puede con todo.
# El resto son terceros: NUNCA contenido sensible, pase lo que pase con el resto de criterios.
#
# `fuerzas`: para qué es el mejor, con el motivo. Es lo que hace auditable la decisión.
# `usd_mtok`: coste orientativo por millón de tokens (artificialanalysis, 2-sep-2026). Sirve
#   para ordenar, no para facturar.
PROVEEDORES = {
    "claude": {
        "tool": None,                       # es el propio runtime, no una tool de tools/
        "destino": "cleared:claude",
        "confianza": True,
        "calidad": 63,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": 2.34,
        "fuerzas": {
            "razonar": "el más alto del ranking hoy (índice 63; Fable 5.1 llega a 66)",
            "clinico": "único de nube al que el muro deja contenido clínico",
            "codigo": "lidera la arena de código",
            "escribir": "el que mantiene la voz sin sonar a IA",
            "orquestar": "es quien reparte el trabajo",
        },
    },
    "local": {
        "tool": "local.py",                 # ollama, via tools/local.py
        "destino": "local:ollama",
        "confianza": True,
        "calidad": 30,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": 0.0,
        "fuerzas": {
            "sin_egress": "no sale de la máquina: el único destino para dato crudo identificable",
            "deidentificar": "quitar PII antes de que nada salga, sin que salga para quitarla",
        },
        "aviso": "qwen3:8b — de-identifica, extrae y clasifica bien (8s); para razonar, no",
    },
    "grok": {
        "tool": "grok.py",
        "destino": "cloud:xai",
        "confianza": False,
        "calidad": 61,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": 0.94,
        "fuerzas": {
            "buscar_vivo": "búsqueda en vivo de web Y de X en la misma llamada",
            # Su papel, en palabras de {{TITULAR}} (19-sep-26): «el investigador macarra que busca
            # petróleo donde los demás no llegan». Va a por lo que no está en los índices
            # limpios —X en vivo, foros, lo que se dice antes de publicarse—. Por eso se queda
            # aunque Perplexity sirva `xai/grok-4.6`: eso da el modelo, no el acceso.
            "rastrear": "lo que no está en los índices limpios: X en vivo, foros, fuentes marginales",
            "redes": "el único con acceso real a X",
        },
        "aviso": "en citas es el menos fiable del grupo: cotejar siempre (Tow Center/CJR 2025)",
    },
    "perplexity": {
        "tool": "perplexity.py",
        "destino": "cloud:perplexity",
        "confianza": False,
        "calidad": 55,          # no figura en el ranking general (es buscador, no LLM de propósito
                                # general): valor conservador para que no gane a los de razonamiento
        "usd_mtok": None,
        "fuerzas": {
            "citas": "devuelve fuentes citadas, y es el mejor de los buscadores en alucinación de cita",
            "buscar_vivo": "búsqueda web reciente con síntesis",
            # 19-sep-2026: su Agent API sirve 46 modelos de VARIAS casas con la misma clave
            # (`perplexity.py --modelos`). Eso lo convierte además en carril barato y en
            # suplente cuando a otro proveedor se le acaba el saldo — que es justo lo que
            # pasó con GLM ese día: `perplexity/glm-5.3` respondió sin recargar nada.
            "barato": "46 modelos de varias casas con una sola clave (--modelo <id>)",
        },
        "aviso": "para evidencia médica, Consensus/scite van antes; toda cita se coteja. "
                 "El agente solo BUSCA si se le pide: sin eso responde de memoria",
    },
    "gemini": {
        "tool": "gemini.py",
        "destino": "cloud:google",
        "confianza": False,
        "calidad": 60,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": None,
        "fuerzas": {
            "vision": "multimodal nativo: imágenes, PDF escaneado, vídeo",
            "contexto_largo": "ventana muy grande, para tragar documentos enteros",
        },
    },
    "chatgpt": {
        "tool": "chatgpt.py",
        "destino": "cloud:openai",
        "confianza": False,
        "calidad": 61,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": 0.95,
        "fuerzas": {
            "segunda_opinion": "casa distinta a Claude: sirve para contrastar de verdad",
            "razonar": "índice 61, justo por debajo de la cabeza",
        },
    },
    "nvidia": {
        "tool": "nvidia.py",
        "destino": "cloud:nvidia",
        "confianza": False,
        "calidad": 58,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": 0.0,
        "fuerzas": {
            "volumen": "gratis: para trabajo masivo o desechable",
            "barato": "cuando la calidad no manda, esto no cuesta nada",
        },
        "aviso": "modelos abiertos, más flojos; NUNCA clínico ni PII",
    },
    "glm": {
        "tool": "glm.py",
        "destino": "cloud:zai",
        "confianza": False,
        "calidad": 60,          # índice artificialanalysis 2-sep-2026
        "usd_mtok": 0.68,
        "fuerzas": {
            "barato": "alternativa de bajo coste",
        },
        # 19-sep-2026: prepago a CERO y decisión de NO recargarlo — el mismo modelo se sirve
        # por Perplexity (`perplexity/glm-5.3`) con una clave que ya se paga. La sonda de
        # salud lo dejará fuera solo mientras siga sin saldo; no hace falta borrarlo.
        "aviso": "sin saldo desde el 19-sep-26; su relevo es perplexity --modelo perplexity/glm-5.3",
        # `aparcado` es para las MÁQUINAS lo que el aviso de arriba dice en prosa: apagado a
        # propósito, no averiado. healthcheck._check_llms no grita por un proveedor aparcado —
        # si no, el aviso `llm_sin_saldo:glm` renace en cada vuelta y nadie puede cerrarlo
        # (20-sep-26: llevaba 5 detecciones e `intermitente`, con la batería roja por ello).
        "aparcado": "19-sep-26: prepago a cero por decisión; relevo perplexity/glm-5.3",
    },
}

# ── Tareas ──────────────────────────────────────────────────────────────────────────────────
# Palabra clave → capacidad. Se busca sobre la descripción de la tarea, en minúsculas y sin
# tildes. Deliberadamente simple y legible: quien lea esto tiene que poder predecir la salida.
PISTAS = {
    "buscar_vivo": ("busca", "buscar", "ultimo", "ultima", "reciente", "novedad", "que se ha "
                    "publicado", "esta semana", "hoy", "noticia", "actualidad", "en vivo"),
    "redes": ("x.com", "twitter", " en x ", "tuit", "post de", "perfil", "redes"),
    # Sin «cita»/«citas» sueltas (11-sep-26): en su día a día casi siempre es la cita MÉDICA
    # («hotel cerca del hospital para la cita del jueves» acababa en Perplexity).
    "citas": ("con citas", "citas bibliograficas", "fuente", "fuentes", "referencia", "papers", "paper",
              "bibliografia", "evidencia", "literatura", "pubmed"),
    "vision": ("imagen", "imagenes", "foto", "captura", "pdf escaneado", "escaneado", "video",
               "grafico", "radiografia", "tac", "pet", "histologia", "mirar", "ver "),
    "contexto_largo": ("documento entero", "todo el informe", "muchas paginas", "libro",
                       "transcripcion completa", "contexto largo"),
    "codigo": ("codigo", "script", "funcion", "bug", "test", "refactor", "python", "bash"),
    "escribir": ("redacta", "redactar", "escribe", "borrador", "correo", "texto", "copy",
                 "articulo", "narra"),
    # Lo que Grok hace mejor que nadie aquí: ir a por lo que no está indexado limpio.
    "rastrear": ("quien dice", "se rumorea", "foro", "reddit", "en x ", "twitter", "hilo",
                 "antes de que se publique", "nadie ha publicado", "rastrea", "husmea"),
    "volumen": ("masivo", "en lote", "todas las", "cientos", "miles", "clasifica cada",
                "resumir cada", "por cada"),
    "razonar": ("analiza", "razona", "decide", "compara", "evalua", "por que", "explica",
                "estrategia", "plan"),
    "deidentificar": ("deidentifica", "de-identifica", "anonimiza", "quita el nombre",
                      "quitar pii", "seudonimiza"),
    "segunda_opinion": ("contrasta", "segunda opinion", "revisa lo que dije", "estoy seguro",
                        "verifica mi"),
}

# Capacidades que exigen panel aunque no se pida --critico: si algo va a cerrar o abrir una
# puerta clínica, una sola voz no basta.
FUERZA_PANEL = ("clinico",)


def _sin_tildes(s):
    tabla = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return s.translate(tabla)


def _norm(s):
    return _sin_tildes((s or "").lower())


# Capacidades en las que lo que sale es una CONSULTA de búsqueda, no material suyo.
# `rastrear` entra aquí (19-sep-26): al añadirla sin apuntarla en esta lista, «busca en X qué se
# dice de la vacuna» dejó de contar como búsqueda y el embargo de palabras públicas volvió a
# bloquearla — el test del muro lo cazó. Una capacidad nueva de BUSCAR se declara en los dos
# sitios o el borde cambia de criterio sin que nadie lo decida.
CAPS_CONSULTA = ("buscar_vivo", "redes", "citas", "rastrear")


def capacidades_de(tarea):
    """Capacidades que pide la tarea, por orden de aparición de la pista. Nunca vacío:
    si no reconozco nada, es 'razonar' (el caso general, y el que manda al mejor modelo)."""
    low = _norm(tarea)
    encontradas = []
    for cap, pistas in PISTAS.items():
        for p in pistas:
            if p in low:
                encontradas.append(cap)
                break
    return encontradas or ["razonar"]


# ── Salud ───────────────────────────────────────────────────────────────────────────────────
def _cache_leer(permitir_vencida=False):
    """Estado cacheado, o None. Con `permitir_vencida` devuelve (estado, fresca) aunque haya
    pasado la hora: para DECIDIR vale la última foto conocida; medir es otro trabajo."""
    try:
        d = json.load(open(CACHE_SALUD, encoding="utf-8"))
        fresca = time.time() - d.get("ts", 0) < CACHE_TTL_S
        if permitir_vencida:
            return (d.get("estado") or {}), fresca
        if fresca:
            return d.get("estado") or {}
    except Exception:
        pass
    return None


def _cache_escribir(estado):
    try:
        os.makedirs(os.path.dirname(CACHE_SALUD), exist_ok=True)
        tmp = CACHE_SALUD + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "estado": estado}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_SALUD)
    except Exception:
        pass                                 # la caché es una comodidad, no puede romper nada


# El carril gratis es lento por definición (cola compartida): con 60 s daba timeout y se
# descartaba un proveedor que estaba perfectamente vivo. Medido el 2-sep-2026.
TIMEOUT_S = {"nvidia": 150, "perplexity": 90}
TIMEOUT_DEFECTO = 60
SONDA_FLAGS = {"grok": ["--nolive"]}     # la sonda de salud no necesita búsqueda en vivo

# Algunas capacidades no se piden con lenguaje natural: se piden con el flag de la tool, que
# lleva el prompt ya afinado. Sin esto, `enruta --ejecutar "de-identifica esto"` mandaba la
# frase suelta a local.py y el modelo DEVOLVIA EL TEXTO CON LA PII DENTRO, aparentando que
# habia funcionado. Un de-identificador que no de-identifica y no falla es peor que no tenerlo.
FLAGS_POR_CAPACIDAD = {
    ("local", "deidentificar"): ["--deid"],
}


# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
# La sonda del local tiene que INFERIR, no solo ver que ollama contesta (25-sep-26, feedback de
# {{CONTACTO}}+KAI). Antes miraba `ollama list`: con el modelo corrupto, sin memoria o colgado, el
# carril salía «vivo» y el enrutado mandaba ahí la de-identificación, que luego fallaba. Una
# suma con respuesta única es la inferencia más barata que distingue «modelo carga y genera»
# de «el servidor lista ficheros». Va por HTTP directo, sin prompt clínico: nada sensible viaja.
_SONDA_LOCAL = ("Responde SOLO con el número, sin texto: ¿cuánto es 2+3?", re.compile(r"(?<!\d)5(?!\d)"))


def _probar_local(timeout):
    import local
    instalados = local.modelos()
    if not instalados:
        return False, "ollama no responde o no tiene modelos"
    if local.MODELO not in instalados:
        return False, "falta el modelo del carril (%s); hay: %s" % (
            local.MODELO, ", ".join(instalados[:3]))
    prompt, esperado = _SONDA_LOCAL
    t0 = time.time()
    try:
        salida = local._pedir(prompt, timeout=timeout)
    except Exception as e:
        return False, "%s no infiere: %r" % (local.MODELO, e)
    if not esperado.search(salida or "") or len(salida) > 40:
        return False, "%s responde mal a la sonda: %r" % (local.MODELO, (salida or "")[:40])
    return True, "%s infiere (%.1f s)" % (local.MODELO, time.time() - t0)


def probar(nombre, *, timeout=None):
    """¿Responde este proveedor? Prueba viva, sin suponer. (ok: bool, detalle: str)."""
    meta = PROVEEDORES.get(nombre) or {}
    tool = meta.get("tool")
    timeout = timeout or TIMEOUT_S.get(nombre, TIMEOUT_DEFECTO)
    if nombre == "claude":
        return True, "es el runtime de esta sesión"
    if nombre == "local":
        return _probar_local(timeout)
    if not tool:
        return False, "sin tool asociada"
    ruta = os.path.join(AQUI, tool)
    if not os.path.exists(ruta):
        return False, "falta %s" % tool
    try:
        # grok busca en vivo (web + X) por defecto: para contestar «OK» se pagaban ~2.000 tokens
        # de resultados de búsqueda (25-sep-2026). Sigue siendo una llamada de pago REAL, que es
        # lo que detecta un saldo a cero; solo sin búsqueda.
        r = subprocess.run([sys.executable, ruta] + SONDA_FLAGS.get(nombre, []) + ["Responde solo: OK"],
                           capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, BTP_ORIGEN="ping"))   # el contador lo separa
        salida = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode == 0 and salida and "error" not in salida.lower()[:40]:
            return True, salida.splitlines()[0][:60]
        return False, (salida or err or "sin salida")[:120]
    except subprocess.TimeoutExpired:
        return False, "timeout a los %ds" % timeout
    except Exception as e:
        return False, repr(e)[:120]


def salud(*, refrescar=False, solo=None):
    """Estado de cada proveedor. Usa caché de una hora salvo que se pida refrescar.

    Las pruebas van EN PARALELO (11-sep-26): en serie, ocho proveedores con timeouts de 60-150 s
    tardaban más de dos minutos y el enrutador era inservible en un hook. `solo` limita qué se
    prueba: si el muro ya ha descartado a los terceros, medirlos es tiempo tirado."""
    if not refrescar:
        cacheado = _cache_leer()
        if cacheado is not None:
            return cacheado
    from concurrent.futures import ThreadPoolExecutor
    nombres = [n for n in PROVEEDORES if solo is None or n in solo]
    with ThreadPoolExecutor(max_workers=max(1, len(nombres))) as pool:
        resultados = dict(zip(nombres, pool.map(probar, nombres)))
    previo = (_cache_leer(permitir_vencida=True) or ({}, False))[0]
    estado = dict(previo)
    for nombre, (ok, detalle) in resultados.items():
        estado[nombre] = {"ok": ok, "detalle": detalle}
    _cache_escribir(estado)
    return estado


LOCK_REFRESCO = os.path.join(_dir_estado(), "enruta_salud.refrescando")   # compartido, como la caché
LOCK_REFRESCO_S = 600       # un refresco en vuelo cada 10 min como mucho: nada de tormentas


def _refrescar_de_fondo():
    """Lanza `enruta.py --salud` desacoplado y vuelve al instante. Fail-open."""
    if os.environ.get("BTP_ENRUTA_SIN_REFRESCO") == "1":
        return
    try:
        from _casa import es_proceso_de_test
        if es_proceso_de_test() and not os.environ.get("BTP_STATE_DIR"):
            return                  # un test no lanza sondas de PAGO reales de fondo
    except Exception:
        pass
    try:
        if os.path.exists(LOCK_REFRESCO) and time.time() - os.path.getmtime(LOCK_REFRESCO) < LOCK_REFRESCO_S:
            return
        os.makedirs(os.path.dirname(LOCK_REFRESCO), exist_ok=True)
        open(LOCK_REFRESCO, "w").close()
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--salud"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def salud_rapida():
    """Para DECIDIR, nunca para medir: <1 s siempre. Última foto conocida aunque esté vencida
    (y si lo está, se refresca de fondo). Sin foto ninguna, se supone vivo con el detalle «sin
    medir»: el que ejecuta lo descubrirá, y el refresco de fondo corrige la siguiente decisión."""
    leido = _cache_leer(permitir_vencida=True)
    if leido is None:
        _refrescar_de_fondo()
        return {n: {"ok": True, "detalle": "sin medir aún"} for n in PROVEEDORES}
    estado, fresca = leido
    if not fresca:
        _refrescar_de_fondo()
    for n in PROVEEDORES:
        estado.setdefault(n, {"ok": True, "detalle": "sin medir aún"})
    return estado


# ── Decisión ────────────────────────────────────────────────────────────────────────────────
class Decision(object):
    """Resultado del enrutado. `elegidos` va ordenado: el primero es la recomendación."""

    def __init__(self, elegidos, motivos, capacidades, sensible, motivo_sensible,
                 panel, descartados, crudo=False, motivo_crudo=None):
        self.elegidos = elegidos
        self.motivos = motivos
        self.capacidades = capacidades
        self.sensible = sensible
        self.motivo_sensible = motivo_sensible
        self.panel = panel
        self.descartados = descartados
        # Identificador DIRECTO (N2, PII: nombre/DNI/email/teléfono/NHC). Distinto de
        # `sensible` (que también marca N1, clínico/genómico aislado): con `crudo=True`,
        # `claude` nunca es destino válido, solo `local` (lectura estricta, 13-sep-26).
        self.crudo = crudo
        self.motivo_crudo = motivo_crudo

    @property
    def primero(self):
        return self.elegidos[0] if self.elegidos else None

    def como_dict(self):
        return {"elegidos": self.elegidos, "motivos": self.motivos,
                "capacidades": self.capacidades, "sensible": self.sensible,
                "motivo_sensible": self.motivo_sensible, "panel": self.panel,
                "descartados": self.descartados, "crudo": self.crudo,
                "motivo_crudo": self.motivo_crudo}


def _clasificar_muro(texto):
    """(sensible, motivo) con el borde. FAIL-CLOSED de verdad: si el borde no se puede
    importar o revienta, se ASUME sensible. Un enrutador que abre la puerta cuando su propio
    guardia no contesta es peor que no tener enrutador."""
    if not texto:
        return False, "sin contenido que clasificar"
    try:
        import borde
        return borde.clasificar(texto)
    except Exception as e:
        return True, "el borde no pudo clasificar (%r): asumo sensible" % e


def _clasificar_consulta(texto):
    """(sensible, motivo) para una CONSULTA de búsqueda que sale tal cual. El mismo borde
    estricto, sin el embargo de palabras públicas («vacuna», neoantígenos, nombres-ruta), que
    rige lo que se publica y no una búsqueda. «{{CONTACTO}}», su nombre, PII, marcadores y huella
    genómica siguen bloqueando. Fail-closed igual que `_clasificar_muro`."""
    if not texto:
        return False, "sin contenido que clasificar"
    try:
        import borde
        return borde.clasificar_consulta(texto)
    except Exception as e:
        return True, "el borde no pudo clasificar la consulta (%r): asumo sensible" % e


def _crudo_identificador(texto):
    """(crudo, motivo) con el borde. Detecta identificador DIRECTO (nombre, DNI, email,
    teléfono, NHC) — NO lo clínico/genómico aislado (eso es N1, ver `.claude/rules/clinico.md`).
    Fail-closed igual que `_clasificar_muro`: si el borde revienta, se asume crudo (la rama más
    conservadora — un `claude` de más frente a un `local` de menos es la fuga real,
    `plan-enrutado-crudo-solo-local-y-gate-por-check`, 13-sep-26)."""
    if not texto:
        return False, "sin contenido que clasificar"
    try:
        import borde
        return borde.identificador_directo(texto)
    except Exception as e:
        return True, "el borde no pudo clasificar crudo (%r): asumo crudo" % e


def elegir(tarea, *, contenido=None, critico=False, refrescar_salud=False, estado=None):
    """Decide a quién llamar para `tarea`. Si hay `contenido`, se clasifica de verdad con el
    borde; si no, se clasifica la descripción de la tarea, que es lo único que hay.

    Una tarea SIN contenido y cuyas capacidades son todas de consulta (buscar en vivo, redes,
    citas) es una búsqueda: lo que sale es la propia frase, así que se mide sin el embargo de
    palabras públicas (11-sep-26: «busca en X qué se dice de la vacuna de
    BioNTech» se quedaba sin Grok por la palabra «vacuna», que ella levantó el 29-7-26).
    Segunda opinión y todo lo demás siguen con el borde estricto: ahí suele ir su caso."""
    caps = capacidades_de(tarea)
    if contenido is None and all(c in CAPS_CONSULTA for c in caps):
        texto_muro = tarea
        sensible, motivo_sens = _clasificar_consulta(texto_muro)
    else:
        texto_muro = contenido if contenido is not None else tarea
        sensible, motivo_sens = _clasificar_muro(texto_muro)
    # Identificador directo (N2), sobre el MISMO texto que se acaba de clasificar por sensible.
    # Solo importa si además es sensible; se calcula siempre para que quede auditado en `Decision`.
    crudo, motivo_crudo = _crudo_identificador(texto_muro)
    if estado is None:
        if refrescar_salud:
            # El muro va antes que la salud: si es sensible, medir a los terceros es tirar tiempo.
            confiables = [n for n, m in PROVEEDORES.items() if m["confianza"]]
            estado = salud(refrescar=True, solo=confiables if sensible else None)
        else:
            estado = salud_rapida()

    # Solo cuando la tarea PIDE explicitamente volumen o barato manda el precio. En lo demas
    # manda la calidad: en una decision que importa, ahorrar 1$ y perder al mejor modelo es un
    # mal negocio (`feedback-coste-nunca-corta-ned-pide-aprobacion`).
    manda_el_coste = bool({"volumen", "barato"} & set(caps))

    descartados = []
    candidatos = []
    for nombre, meta in PROVEEDORES.items():
        # 1. EL MURO, primero y sin excepciones. Con contenido crudo identificable (N2),
        # `claude` deja de contar como destino de confianza PARA ESTA DECISIÓN: solo `local`
        # vale (lectura estricta de {{TITULAR}}, 13-sep-26 — `.claude/rules/clinico.md`).
        confia = meta["confianza"] and not (crudo and nombre == "claude")
        if sensible and not confia:
            if crudo and nombre == "claude":
                motivo_desc = ("contenido crudo identificable (%s): solo local, nunca claude"
                               % motivo_crudo)
            else:
                motivo_desc = ("contenido sensible (%s): no es destino de confianza"
                               % motivo_sens)
            descartados.append((nombre, motivo_desc))
            continue
        # 3. SALUD (el orden del catálogo no importa; el criterio 2 puntúa más abajo).
        st = estado.get(nombre) or {}
        if not st.get("ok", False):
            descartados.append((nombre, "no responde: %s" % st.get("detalle", "sin detalle")))
            continue
        # 2. CAPACIDAD.
        puntos, porques = 0, []
        for cap in caps:
            if cap in meta["fuerzas"]:
                puntos += 1
                porques.append("%s → %s" % (cap, meta["fuerzas"][cap]))
        if puntos == 0:
            descartados.append((nombre, "no es el mejor para %s" % ", ".join(caps)))
            continue
        # 4. COSTE o CALIDAD, segun lo que pida la tarea. Ordenar SIEMPRE por coste metia a
        # un modelo mas flojo por delante del mejor en una decision critica solo por ser mas
        # barato: exactamente el fallo que no queremos. Asi que el coste manda solo cuando la
        # tarea pide volumen o barato; en todo lo demas manda la calidad.
        coste = meta.get("usd_mtok")
        coste = 999.0 if coste is None else coste
        if manda_el_coste:
            desempate = -coste
        else:
            desempate = meta.get("calidad", 0)
        candidatos.append((puntos, desempate, nombre, porques, meta.get("aviso")))

    candidatos.sort(reverse=True)
    elegidos = [c[2] for c in candidatos]
    motivos = {}
    for _, _, nombre, porques, aviso in candidatos:
        txt = "; ".join(porques)
        if aviso:
            txt += " · ojo: %s" % aviso
        motivos[nombre] = txt

    # 5. PANEL. Para lo crítico no se elige uno, se contrastan varios, y de casas distintas.
    panel = bool(critico) or any(c in FUERZA_PANEL for c in caps)
    if panel:
        vistos, diversos = set(), []
        for nombre in elegidos:
            casa = (PROVEEDORES[nombre]["destino"].split(":")[-1])
            if casa in vistos:
                continue
            vistos.add(casa)
            diversos.append(nombre)
            if len(diversos) == 3:
                break
        elegidos = diversos or elegidos[:1]

    if not elegidos and sensible and not crudo and (estado.get("claude") or {}).get("ok"):
        # CAÍDA SEGURA (11-sep-26). Antes, «qué se ha publicado sobre FGFR1 en mama HR+» daba
        # NADIE: el muro veta a los buscadores de terceros y ni claude ni local tienen la fuerza
        # `buscar_vivo`. Pero Claude, que es el mejor del ranking y el destino de confianza, busca
        # con WebSearch y con los MCP de literatura (PubMed, BioMCP, scite). Eso no es degradar:
        # es el carril que el muro deja abierto. Solo si claude tampoco está, se bloquea.
        # NUNCA si `crudo`: con identificador directo la letra es "solo local", y esta caída
        # segura mandaba precisamente a claude — el bug que reprodujo @MrHydeDev en X.
        elegidos = ["claude"]
        motivos["claude"] = ("caída segura: el muro veta a los terceros; lo hace el runtime de "
                             "confianza con WebSearch y los MCP de literatura (PubMed, BioMCP, scite)")
        descartados = [(n, p) for n, p in descartados if n != "claude"]

    if not elegidos and sensible:
        # Nadie de confianza está vivo (o capaz) y el contenido es sensible: se BLOQUEA, no se
        # degrada. `feedback-no-degradar-lo-critico-bloquear-avisar`.
        if crudo:
            motivos["__bloqueado__"] = (
                "contenido crudo identificable (%s) y local no disponible: se bloquea, nunca "
                "a claude. Opciones (.claude/rules/clinico.md): de-identificar (tools/deid.py), "
                "exigir contrato real, o que {{TITULAR}} lo acepte consciente para este uso "
                "concreto." % motivo_crudo)
        else:
            motivos["__bloqueado__"] = ("contenido sensible y ningún destino de confianza "
                                        "disponible: se bloquea, no se degrada")

    return Decision(elegidos, motivos, caps, sensible, motivo_sens, panel, descartados,
                    crudo=crudo, motivo_crudo=motivo_crudo)


def ejecutar(decision, tarea, *, todos=False, contenido=None):
    """Llama de verdad al proveedor elegido. Devuelve [(nombre, ok, salida)].

    Existe para que adoptar el enrutador sea cambiar UNA linea donde hoy pone
    `python3 tools/grok.py "..."`, en vez de reescribir los cientos de sitios que llaman a
    una tool concreta a mano. Sin esto, el enrutador decide y no lo usa nadie.

    Dos cosas que NO hace, a proposito:
      · Si el contenido es sensible, aqui no sale nada hacia un tercero. La decision ya los
        descarto; esto solo ejecuta lo que sobrevivio, y vuelve a comprobarlo por si acaso.
      · claude no se ejecuta desde aqui: es el runtime que ya esta corriendo, y llamarse a si
        mismo por subprocess seria absurdo y caro. Se devuelve como indicacion para quien
        llame. `local` SI se ejecuta desde el 2-sep-2026, via tools/local.py.

    `contenido` va por STDIN al subproceso, que es como lo esperan las tools que aceptan datos
    por pipe. Sin esto se le pasaba solo la instruccion y el modelo respondia «pasame el
    texto»: la tarea dice QUE hacer, el contenido dice SOBRE QUE.
    """
    if not decision.elegidos:
        return [("__nadie__", False, decision.motivos.get("__bloqueado__",
                                                          "ningun proveedor disponible"))]
    objetivos = decision.elegidos if todos else decision.elegidos[:1]
    resultados = []
    for nombre in objetivos:
        meta = PROVEEDORES.get(nombre) or {}
        # Cinturon y tirantes: la decision ya filtro por sensibilidad, pero el que ejecuta
        # vuelve a mirar. Una comprobacion de mas cuesta microsegundos; una de menos, una fuga.
        if decision.sensible and not meta.get("confianza"):
            resultados.append((nombre, False,
                               "BLOQUEADO: contenido sensible hacia destino no confiable"))
            continue
        if getattr(decision, "crudo", False) and nombre == "claude":
            # Cinturón y tirantes, igual que arriba: `elegir()` ya excluye a claude cuando hay
            # identificador directo, pero por si alguien construye una Decision a mano.
            resultados.append((nombre, False,
                               "BLOQUEADO: contenido crudo identificable — solo local, "
                               "nunca claude"))
            continue
        tool = meta.get("tool")
        if not tool:
            resultados.append((nombre, True,
                               "[no se ejecuta desde aqui: es el runtime en curso]"))
            continue
        ruta = os.path.join(AQUI, tool)
        extra = []
        for cap in decision.capacidades:
            extra = FLAGS_POR_CAPACIDAD.get((nombre, cap)) or extra
        try:
            # stdin=DEVNULL, no heredado: varias tools leen stdin cuando no es un tty
            # (para aceptar datos por pipe). Heredando el stdin del padre se quedaban
            # esperando un EOF que no llegaba nunca y el subproceso moria por timeout.
            # Si la decisión se tomó como BÚSQUEDA, la tool lo sabe y su borde mide igual que
            # aquí. Sin esto, enruta elegía Grok y grok.py volvía a bloquear «vacuna» (11-sep-26).
            import borde
            env = dict(os.environ)
            env.pop(borde.ENV_CONSULTA, None)
            if contenido is None and all(c in CAPS_CONSULTA for c in decision.capacidades):
                env[borde.ENV_CONSULTA] = "1"
            r = subprocess.run([sys.executable, ruta] + extra + ([] if extra else [tarea]),
                               capture_output=True, text=True, env=env,
                               input=(contenido or ""),
                               timeout=TIMEOUT_S.get(nombre, TIMEOUT_DEFECTO) * 4)
            salida = (r.stdout or "").strip() or (r.stderr or "").strip()
            resultados.append((nombre, r.returncode == 0, salida))
        except subprocess.TimeoutExpired:
            resultados.append((nombre, False, "timeout"))
        except Exception as e:
            resultados.append((nombre, False, repr(e)[:200]))
    return resultados


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────
def _render(d, tarea):
    out = []
    out.append("🧭 Enrutado de: «%s»" % tarea)
    out.append("   capacidades detectadas: %s" % ", ".join(d.capacidades))
    if d.sensible:
        out.append("   🔒 MURO: contenido sensible (%s) → solo destinos de confianza"
                   % d.motivo_sensible)
    if getattr(d, "crudo", False):
        out.append("   🚫 CRUDO IDENTIFICABLE (%s) → solo local, nunca claude" % d.motivo_crudo)
    if d.panel:
        out.append("   👥 PANEL: se piden varios y se contrastan (casas distintas)")
    out.append("")
    if not d.elegidos:
        out.append("   ⛔ NADIE. %s" % (d.motivos.get("__bloqueado__")
                                        or "ningún proveedor encaja o está vivo."))
    for i, nombre in enumerate(d.elegidos, 1):
        marca = "→" if i == 1 else " "
        out.append("   %s %d. %-11s %s" % (marca, i, nombre, d.motivos.get(nombre, "")))
    if d.descartados:
        out.append("")
        out.append("   descartados:")
        for nombre, porque in d.descartados:
            out.append("      · %-11s %s" % (nombre, porque))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[0])
    p.add_argument("tarea", nargs="*", help="qué hay que hacer, en lenguaje normal")
    p.add_argument("--json", action="store_true", help="salida para consumo programático")
    p.add_argument("--critico", action="store_true",
                   help="panel de varios modelos de casas distintas, no uno solo")
    p.add_argument("--contenido-de", metavar="FICHERO",
                   help="clasificar el contenido REAL de este fichero, no la descripción")
    p.add_argument("--salud", action="store_true", help="probar cada proveedor y salir")
    p.add_argument("--refrescar", action="store_true", help="ignorar la caché de salud")
    p.add_argument("--ejecutar", action="store_true",
                   help="además de decidir, LLAMAR al elegido y devolver su respuesta")
    p.add_argument("--todos", action="store_true",
                   help="con --ejecutar: llamar a todo el panel, no solo al primero")
    a = p.parse_args(argv)

    if a.salud:
        try:
            estado = salud(refrescar=True)
        finally:
            try:
                os.remove(LOCK_REFRESCO)        # lo puso _refrescar_de_fondo, si fue él
            except OSError:
                pass
        for nombre, st in estado.items():
            print("%-11s %s  %s" % (nombre, "✅" if st["ok"] else "❌", st["detalle"]))
        return 0 if all(s["ok"] for s in estado.values()) else 1

    tarea = " ".join(a.tarea).strip()
    if not tarea:
        p.print_help()
        return 2

    contenido = None
    if a.contenido_de:
        try:
            contenido = open(a.contenido_de, encoding="utf-8", errors="replace").read()
        except Exception as e:
            sys.stderr.write("no pude leer %s: %r\n" % (a.contenido_de, e))
            return 2

    d = elegir(tarea, contenido=contenido, critico=a.critico, refrescar_salud=a.refrescar)

    if a.ejecutar:
        res = ejecutar(d, tarea, todos=a.todos, contenido=contenido)
        if a.json:
            print(json.dumps({"decision": d.como_dict(),
                              "respuestas": [{"proveedor": n, "ok": ok, "salida": out}
                                             for n, ok, out in res]},
                             ensure_ascii=False, indent=2))
        else:
            sys.stderr.write(_render(d, tarea) + "\n\n")   # la decisión al log, no a stdout
            for nombre, ok, out in res:
                if len(res) > 1:
                    print("── %s %s ──" % (nombre, "✅" if ok else "❌"))
                print(out)
        return 0 if any(ok for _, ok, _ in res) else 1

    if a.json:
        print(json.dumps(d.como_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(d, tarea))
    return 0 if d.elegidos else 1


if __name__ == "__main__":
    sys.exit(main())
