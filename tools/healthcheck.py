#!/usr/bin/env python3
"""tools/healthcheck.py — salud del lazo 24/7 + DEAD-MAN'S-SWITCH (P1, A3).

Corre cada ~30 min (plist). Comprueba: cola viva (+ reap de jobs atascados), heartbeat
del despachador fresco, disco libre, y que el canal de Telegram está configurado (en
SECO, sin abrir socket nuevo: usa salida.report_to_titular dry). Escribe last_check.json.

DEAD-MAN (A3): `state/healthcheck/last_seen.json` lo toca SOLO un acto INEQUÍVOCO de
{{TITULAR}} (un mensaje válido suyo por el bot, `btp_run seen`, o un prompt suyo en Claude Code
verificado contra el transcript: origin {"kind":"human"} y sin envoltorio de automatismo,
ver _procesar_presencia_cc), NUNCA la actividad del
lazo. Si pasan > N días (def 3) sin señal suya → crea `degraded.flag` (cost_guard baja
el tope) y ESCALA el aviso. Invariante: el dead-man NUNCA bloquea la salida — eso lo da
SIEMPRE el muro/.HALT; el dead-man solo baja gasto y sube la insistencia del aviso.

SERVICIOS Y CAMINOS VIVOS (añadido 26/6/26):
Distingue "el proceso existe" de "responde de verdad". Tres niveles:
  1. Web local (127.0.0.1:8787): ¿el Observatorio devuelve 200?
  2. Camino móvil (tailscale serve 9090 → 8787, por nombre MagicDNS): ¿llega desde el móvil?
     · Si el local va bien pero el móvil no → Tailscale caído o NordVPN bloqueándolo (no hay
       daemon propio que reiniciar: el reenvío lo hace la app de Tailscale).
     · Si el local también está caído → daemon caído (se intenta kickstart UNA vez).
  3. Autofix acotado: kickstart de daemons KeepAlive caídos (una sola vez, reversible).
     Si sigue caído tras el kickstart → aviso en llano, sin bucle.

Todas las alertas salen por salida.report_to_titular (cero sockets nuevos → se mantiene
el invariante del choke-point). Sin dependencias (stdlib).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cola as q
import salida
import cost_guard
import bucles_colgados
import estado_rutina   # al día / atrasada / rota: lo usa _frescura_rutina (25-sep-2026)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `BTP_STATE_DIR` manda, como en `_casa.state_dir()`, `_lock.py` y el resto del repo (20-sep-26).
# Aquí no se leía, así que los tests que la ponen para aislarse escribían en el estado VIVO de
# salud: `tests/test_healthcheck_deadman.py` monta BTP_STATE_DIR + BTP_TEST_BATTERY y su propio
# comentario avisa de que `salida.send` enmudece «solo si BTP_TEST_BATTERY=1 Y el STATE está
# aislado: hacen falta las dos» — y la segunda no se cumplía. Esa es la protección que nació de
# los 14 mensajes reales que le llegaron a {{TITULAR}} el 12-jul-26, apoyada en algo que no pasaba.
# Medido: una clave de un script de prueba con BTP_STATE_DIR a un tmpdir apareció igualmente en
# `tools/state/healthcheck/last_alert_state-operativo.json` del repo real.
# En producción no cambia nada: `com.btp.healthcheck.plist` no define la variable, y los plists
# que sí la definen la apuntan a `/Users/polaris/claudecode/tools/state`, que es este mismo valor.
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
HC = os.path.join(STATE, "healthcheck")
LAST_SEEN = os.path.join(HC, "last_seen.json")
LAST_CHECK = os.path.join(HC, "last_check.json")
DEGRADED = os.path.join(HC, "degraded.flag")
HEARTBEAT = os.path.join(STATE, "dispatcher", "heartbeat.json")

AUSENCIA_DIAS = 3
# Solo grita por encargos muertos en los últimos N días. Nadie vacía `failed/`, y sin esta ventana
# dos cadáveres del 30-jul tuvieron la batería roja 3 días con 73 re-detecciones de un problema que
# ya no ocurría (31-jul-26). Lo viejo sigue contado en `info`, pero deja de gritar.
VENTANA_CAIDOS_DIAS = 2
HEARTBEAT_STALE_SEG = 600        # 10 min: el dispatcher hace heartbeat cada ciclo
DISCO_MIN_GB = 2.0
STUCK_SEG = 3600
RECOVER_LEDGER = os.path.join(HC, "recover_ledger.json")  # rebotes del auto-recover B2 (guarda anti-bucle)
RECOVER_MAX = 20                 # tope de jobs re-encolados por ciclo (B2)
RECOVER_ESCALA = 3               # rebotes seguidos antes de escalar a {{TITULAR}} (no se auto-cura → falta fusionar)
PRESUP_MENSUAL_FRAC = float(os.environ.get("BTP_PRESUP_MENSUAL_FRAC", "0.80"))  # C: avisar al 80% del mensual
PRESUP_DIARIO_FRAC = float(os.environ.get("BTP_PRESUP_DIARIO_FRAC", "0.90"))    # C: avisar al 90% del diario
# Saldo PREPAGO de Anthropic (el susto del 17-jul: llegó a 0 en silencio, el tope local tenía margen).
# Avisamos al cruzar estas fracciones del importe recargado (heads-up y urgente).
PREPAGO_FRAC_AVISO = float(os.environ.get("BTP_PREPAGO_FRAC_AVISO", "0.75"))
PREPAGO_FRAC_URGENTE = float(os.environ.get("BTP_PREPAGO_FRAC_URGENTE", "0.90"))
VIGIA_STALE_MIN = int(os.environ.get("BTP_VIGIA_STALE_MIN", "20"))   # B: vigía corre cada 5 min → stale > 20
INACTIVO_FACTOR = int(os.environ.get("BTP_INACTIVO_FACTOR", "3"))    # A: inactivo si última actividad > K×cadencia
# Suelo del umbral A: en daemons MUY frecuentes (correo-imap = 120s → 3×120 = 6 min) un solo hipo
# del entorno (Mac dormido, coalescencia de timers de launchd bajo ahorro de energía) dispara la
# falsa alarma aunque el daemon esté sano y se auto-recupere al ciclo siguiente. La INTENCIÓN de
# este check es cazar "días sin disparar / horario mal puesto", no un hueco de minutos. Con el
# suelo, un daemon rápido debe callar ≥ INACTIVO_MIN antes de alertar; los lentos no cambian
# (su K×cadencia ya supera el suelo). Fix 3/7: correo-imap avisó por un hueco único de 12 min.
INACTIVO_MIN_SEG = int(os.environ.get("BTP_INACTIVO_MIN_MIN", "30")) * 60   # suelo: 30 min

# ── Servicios y caminos vivos ─────────────────────────────────────────────────────────
OBS_LOCAL_URL   = "http://127.0.0.1:8787/"
# Antes era el relay Python 100.114.113.73:8788 (com.btp.observatorio-remoto), retirado el
# 25-sep-2026 (S14): `tailscale serve` 9090 hace lo mismo sin un proceso nuestro escuchando fuera.
OBS_MOVIL_URL   = "http://polaris.taild7f51c.ts.net:9090/"
HTTP_TIMEOUT_S  = 5    # generoso: el relay puede tardar si Tailscale negocia
KICKSTART_WAIT  = 8    # segundos de espera tras kickstart antes de re-comprobar
# Daemons con latido propio (bot-telegram): tras el kickstart el proceso nuevo no late hasta que
# vuelve su PRIMER long-poll, que sin mensajes tarda 25 s. Con la espera fija de 8 s el autofix no
# podía salir bien nunca: 0 de 1.370 intentos en healthcheck.out (medido el 13-sep-26), y cada
# kickstart que sí lo revivía se contaba igual como «parado». Se sondea el latido hasta este tope.
LATIDO_TRAS_KICKSTART_MAX_S = 45     # long-poll (25 s) + margen de arranque
LATIDO_TRAS_KICKSTART_PASO_S = 1

# Daemons con KeepAlive=true que el healthcheck puede revivir con kickstart.
# Solo se lista el Observatorio y su relay (los dos que vigila con HTTP).
# El dispatcher tiene su propia comprobación (heartbeat + cola pendiente, más abajo). El
# bot-telegram tiene la SUYA por heartbeat propio en VEGA_DAEMONS/_autofix_daemon_por_heartbeat (2/7/26:
# antes decía tenerla y no era cierto para el caso "vivo pero con el poll en bucle de red" — el
# hueco real del incidente del 2/7. Ya está cubierto, no lo repitas aquí con HTTP).
KEEPALIVE_DAEMONS = {
    "observatorio":        "com.btp.observatorio",
    "borde-gateway":       "com.btp.borde-gateway",
}

# B1 (vigía de roster): daemons KeepAlive que NO se vigilan aquí porque ya tienen un check de
# RESPUESTA dedicado (3b/3c) — mejor que "¿está cargado?", verifica que de verdad funcionen.
_ROSTER_SKIP = {"com.btp.observatorio", "com.btp.borde-gateway"}
# Rutinas aparcadas a propósito: si faltan del roster, NO es un fallo (no alertar).
_PARKED_HC = {"com.btp.instagram", "com.btp.preview-web", "com.btp.observatorio-remoto"}
# (23/7: wa-tareas estuvo aquí mientras su gate de cosecha de WhatsApp estaba cerrado. {{TITULAR}} lo
#  encendió (.wa_cosecha_on) → ya hace trabajo real a diario → vuelve a monitorizarse como el resto.)

# La PUERTA de Polaris (gateway del borde: Vivir/Misión/Cuidar/Construir). Si el daemon se cae,
# {{TITULAR}} habla y no contesta nadie → hay que revivirlo solo. Loopback, mismo host/puerto que el daemon.
GW_HOST = os.environ.get("BTP_GATEWAY_HOST", "127.0.0.1")
GW_PORT = int(os.environ.get("BTP_GATEWAY_PORT", "8799"))

# ── Watchdog de Vega ───────────────────────────────────────────────────────────────
# Vigilamos por su HEARTBEAT propio (state/heartbeat/<agente>.json) a los daemons de Vega que dejan
# rastro fiable. correo-urgente NO escribe heartbeat → su salud se infiere del saldo (que es lo que
# de verdad lo silencia). El parseo es defensivo: nunca tira el healthcheck.
HB_DIR = os.path.join(STATE, "heartbeat")
# Tres «sin señal» simultáneos ya no son coincidencia: es el directorio que no se dejó leer.
UMBRAL_CASCADA_LATIDOS = 3
VEGA_DAEMONS = (
    {"agente": "asistente",     "label": "el barrido diario de Vega", "cadencia_h": 27, "agentico": True},   # diario 7:55
    {"agente": "calendar-sync", "label": "el sync de tu calendario",  "cadencia_h": 27, "agentico": False},  # diario 8:05 (no usa LLM)
    {"agente": "centinela-ned", "label": "el centinela (avisa al momento)", "cadencia_h": 0.5, "agentico": False},  # cada ~2-3 min, $0
    # 2/7/26: "vivo ≠ sano". El bot es KeepAlive (siempre debería estar vivo Y respondiendo); su
    # heartbeat lo escribe bot_telegram.poll_once() en cada pasada que HABLÓ con Telegram de verdad
    # (con o sin mensajes). cadencia_h corta (long-poll ~25s + sleep 1s por ciclo) con umbral de
    # 4 min: da margen a un blip de red puntual sin falsear alarma, pero caza un bucle sostenido de
    # resets (el incidente real: cientos de "Connection reset" seguidos, sin ningún poll OK). El
    # daemon_label activa el autofix de kickstart×1 en _autofix_daemon_por_heartbeat (ver más abajo) ANTES
    # de convertirse en alerta humana — los otros VEGA_DAEMONS no son KeepAlive, así que no aplica.
    {"agente": "bot-telegram", "label": "el bot de Telegram (recepción de tus mensajes)",
     "cadencia_h": 4 / 60.0, "agentico": False, "daemon_label": "com.btp.bot-telegram"},
)
# Estados de heartbeat que NO son problema: ok + el salto frugal de la centralita/gate + la pausa
# nocturna (todos normales, no gastan ni indican fallo).
ESTADOS_HB_SANOS = {"ok", "gate_sin_novedad", "ok_sin_novedad", "centralita", "reanudado",
                    "quiet_nocturno"}

# ── Watchdog de rutinas NED (C, 2/7/26) ───────────────────────────────────────────
# Tres rutinas que alimentan DIRECTAMENTE la ruta a NED (auto-mejora del sistema, barrido de git,
# radar médico). Si una lleva ≥2 días CLAVADA en un estado NO sano (nadie la arregló, ni siquiera
# se volvió a ejecutar con éxito desde entonces), nadie lo notaría hoy: cada rutina solo tiene su
# propio dueño mirándola de reojo (auto-mejora corre sola lun/mié/vie/dom; git ídem/mensual)
# y no hay un vigía que sume "esto lleva DEMASIADO tiempo en mal estado". _salud_daemons() (arriba)
# ya cubre Vega/asistente/calendar/centinela; esto es su hermano para las rutinas NED. A diferencia
# de VEGA_DAEMONS, aquí NO exigimos frescura (comite-medico es MENSUAL: su heartbeat de hace 20 días
# es normal) — lo que importa es si el ÚLTIMO estado conocido es malo y lleva ≥2 días sin corregirse
# (sin una pasada 'ok' más reciente que lo tape). 'centralita' cuenta aquí como NO sano a propósito:
# para el watchdog general es un salto frugal aceptable, pero que una rutina NED lleve días sin ser
# atendida por el agente real (solo por el cerebro de respaldo) SÍ es degradación sostenida.
# `periodo_h` + `margen_h` (25-sep-2026): lo más que puede pasar entre dos pasadas SANAS según su
# plist, más holgura para lo que tarda la pasada. Con eso `estado_rutina.clasificar` caza la rutina
# que deja de correr sin fallar (su último latido sigue en «ok» y el watchdog de abajo no la veía).
# tests/test_healthcheck.py comprueba que periodo_h no es menor que el hueco real de su plist.
# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
RUTINAS_NED = (
    {"agente": "auto-mejora",   "label": "la auto-mejora del sistema (lun/mié/vie/dom)",
     "plist": "com.btp.auto-mejora", "periodo_h": 48, "margen_h": 6},
    {"agente": "git",           "label": "el barrido diario de git",
     "plist": "com.btp.git-barrido", "periodo_h": 24, "margen_h": 6},
    # comite-medico: su latido lo escribe también radar_ned_dia.sh (diario), así que esto mide la
    # actividad del AGENTE; el mensual de radar-lit es el peor caso, no lo que se ve a diario.
    {"agente": "comite-medico", "label": "el radar del comité médico",
     "plist": "com.btp.radar-lit", "periodo_h": 31 * 24, "margen_h": 48},
)
ESTADOS_NED_MALOS = {
    "fallo", "critico_bloqueado", "centralita",
    "aplazado_sin_saldo", "aplazado_tope_local", "aplazado_limite", "credito_agotado", "tope_consola",
}
RUTINA_NED_DIAS_UMBRAL = 2

# Gemelos de criterio (consejeros espejo de una persona real). Dos umbrales a propósito:
# uno para «nadie lo está refrescando» y otro para «el carril ni se puede ejecutar».
GEMELO_RANCIO_DIAS = 21
GEMELO_CARRIL_MUERTO_DIAS = 14


# ── Funciones de comprobación HTTP (servicios vivos) ─────────────────────────────────

def _http_ok(url, timeout=HTTP_TIMEOUT_S):
    """Devuelve (True, código) si la URL responde 200; (False, motivo_str) si no.
    Fail-soft: nunca lanza, captura todo (timeout, error de red, URL malformada)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            code = r.getcode()
            return (code == 200, code)
    except urllib.error.HTTPError as e:
        return (False, "HTTP %d" % e.code)
    except Exception as e:
        return (False, type(e).__name__)


def _kickstart_daemon(label):
    """Revive un daemon KeepAlive. Dos escalones: `kickstart`, y si no, `bootstrap`.

    POR QUÉ EL SEGUNDO ESCALÓN (20-sep-26). `kickstart -k` exige que el job YA esté cargado en
    el dominio. Si se cae del todo (`bootout`), kickstart falla y el autofix se rendía: había que
    levantarlo a mano. Eso mantenía `daemon_roster_caido:com.btp.dispatcher` y `:bot-telegram` a
    **1.251 detecciones cada uno** — con el dispatcher abajo la cola no se vacía, así que los
    encargos que debían cerrar las alertas tampoco corrían, y el sistema se quedaba gritando algo
    que solo podía arreglar una persona. La causa la dejó diagnosticada otra sesión el 19-sep y
    seguía sin arreglarse; detectar no es arreglar.

    Y antes de `bootstrap`, `enable`: `launchctl disable` deja una marca PERSISTENTE que hace
    fallar el bootstrap con «Input/output error», que no dice nada y no es transitorio. Es la
    misma trampa que `activar_daemon.py` documenta tras pisarla con estos dos daemons.

    Esto NO enciende nada que estuviera apagado a conciencia: solo se llama desde la rama de los
    KeepAlive que deberían estar corriendo. Los interval/calendar descargados siguen siendo gate
    de {{TITULAR}}, como hasta ahora. Fail-soft: nunca lanza.
    """
    uid = os.getuid()
    dominio = "gui/%d" % uid
    try:
        r = subprocess.run(
            ["launchctl", "kickstart", "-k", "%s/%s" % (dominio, label)],
            capture_output=True, timeout=10
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass

    plist = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % label)
    if not os.path.isfile(plist):
        return False                    # sin plist no hay nada que levantar
    try:
        subprocess.run(["launchctl", "enable", "%s/%s" % (dominio, label)],
                       capture_output=True, timeout=10)
        r = subprocess.run(["launchctl", "bootstrap", dominio, plist],
                           capture_output=True, timeout=20)
        if r.returncode == 0:
            return True
        # «Input/output error» de launchd es a veces transitorio: un reintento, no más.
        time.sleep(1.5)
        r = subprocess.run(["launchctl", "bootstrap", dominio, plist],
                           capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def _marcar_acuse_autofix(clave, que_hizo):
    """Cuando el autofix acotado (kickstart) YA intentó algo, deja constancia en el acuse de esa
    clave para que {{TITULAR}} vea que el sistema ya se puso, aunque ella no esté mirando. Fail-soft:
    nunca lanza (el import de salud.py puede faltar en un entorno raro; no debe tirar el ciclo)."""
    try:
        import salud as _salud
        _salud.ack(clave, "autofix: %s" % que_hizo, por="autofix")
    except Exception:
        pass


def _clave_del_libro(clave):
    """La clave que el job de investigación tiene que dejar anotada en el libro de deuda.

    Casi siempre es la de la alerta, en su forma normalizada (`deuda.normalizar_clave`: sin
    backticks, que dentro de `"…"` en bash son sustitución de comandos). La excepción es la
    alerta RESUMEN `deuda_escalada`: no está en el libro a propósito (NO_AL_LIBRO, se comía a sí
    misma hasta 72x), así que exigirla como prueba tumbaba siempre el job aunque el agente
    anotara bien el hallazgo concreto (jobs 53696bb300 y b1a2bfe70b). Ahí la clave es la del
    hallazgo escalado con más detecciones, que es el que el propio aviso nombra como «el peor».
    None = no hay nada que anotar (el resumen ya no tiene hallazgos debajo)."""
    try:
        import deuda as _d
    except Exception:
        return clave
    if clave == "deuda_escalada":
        try:
            esc = _d.escaladas()
        except Exception:
            return None
        if not esc:
            return None
        return max(esc.items(), key=lambda kv: (kv[1].get("veces", 0), kv[0]))[0]
    return _d.normalizar_clave(clave)


def _encolar_investigacion(clave, texto):
    """Encola UNA investigación real para una alerta de salud nueva. Devuelve el id del job, o None
    si no se pudo encolar (y entonces el acuse lo dice en llano, sin prometer).

    Por qué existe (25-jul-26): el acuse automático decía "🔧 Detecté X, lo estoy mirando" y detrás
    no miraba nadie — el docstring del propio módulo lo admitía como TODO. Eso es peor que el grito
    seco: {{TITULAR}} lee que alguien se puso y deja de vigilarlo. Ahora el "me pongo" es literal.

    Guardas:
      · HALT activo → no se encola (el job se quedaría criando polvo y la promesa sería falsa).
      · `expira` a +2 días: si el lazo está parado, el job no se acumula para siempre. Cuando caduca
        cae a failed/, que desde el 25-jul SÍ tiene quien lo cuente (`_check_jobs_caidos`).
      · perfil `privileged` + agente `tecnico`: es fontanería del sistema, no algo hacia fuera. El
        muro y el gate de salida siguen mandando dentro del job, igual que en cualquier otro.
      · sin campos nuevos en el schema: `enqueue` tal cual, para no romper el consumer-first de la
        cola (un campo que casa base no conozca manda el job a failed/).
    Fail-soft: cualquier excepción → None. Esto nunca puede tumbar el aviso de salud."""
    try:
        import salida as _s
        if _s.halted():
            return None
    except Exception:
        pass
    libro = _clave_del_libro(clave)
    if not libro:
        return None
    # La MISIÓN tiene que caber dentro del muro (31-jul-26). Antes decía «arréglala, la fontanería
    # se ejecuta, no se apunta», y en modo autónomo eso es imposible: el guard bloquea `python -c`,
    # `awk`, `ToolSearch`, el binario `claude`, editar código y tocar el propio muro. El agente
    # chocaba con la pared en cada intento hasta salir con rc=1 — 3 veces por alerta, minutos de
    # LLM cada una, y el fallo aterrizaba en `failed/` alimentando la alerta de «jobs caídos», que
    # encolaba otro job igual. Pedirle lo imposible no es exigencia, es un bucle de gasto.
    # El muro NO se toca: se cambia el encargo. Diagnosticar y DEJARLO ESCRITO sí cabe.
    resumen = ""
    if clave == "deuda_escalada":
        resumen = ("Es una alerta RESUMEN: no está en el libro. El hallazgo que te toca es el peor "
                   "de los escalados, con clave «%s». Trabaja sobre ese.\n\n" % libro)
    intencion = (
        "Alerta de salud del sistema: «%s» (clave estable: %s).\n\n"
        "%s"
        "Tu trabajo aquí es DIAGNOSTICAR y DEJARLO ESCRITO, no arreglar el código. Corres en modo "
        "autónomo y el muro te bloquea (correctamente) `python -c`, `awk`, `ToolSearch`, el binario "
        "`claude`, editar cualquier ejecutable y tocar el propio muro. NO intentes rodearlo: si el "
        "arreglo exige tocar código, ese arreglo es de una sesión con {{TITULAR}}, no tuyo.\n\n"
        "Lo que SÍ puedes hacer: leer ficheros, y correr las tools del repo (`python3 tools/…`) "
        "y `git log`/`git diff`.\n\n"
        "1. Averigua la causa con eso.\n"
        "2. Apúntala SIEMPRE en el libro: `python3 tools/deuda.py abrir \"%s\" \"<la causa en una "
        "línea>\" --ned <alto|medio|bajo>` (si ya estaba abierta, `deuda.py nota \"%s\" \"<lo "
        "nuevo que has averiguado>\"`).\n"
        "3. Si la condición YA no se cumple (era un rastro viejo), remítela: "
        "`python3 tools/deuda.py remitir \"%s\" \"<por qué ya no ocurre>\"`.\n"
        "4. Cierra la alerta con `python3 tools/salud.py resuelto %s` SOLO si comprobaste por su "
        "EFECTO que ya no se da. Si sigue dándose, déjala abierta: una alerta cerrada en falso es "
        "peor que una alerta que grita.\n\n"
        "Termina siempre con un resumen de 2-3 líneas: causa, qué dejaste apuntado, y qué haría "
        "falta para cerrarlo. Que no puedas arreglarlo NO es un fallo tuyo: es el reparto."
    ) % (texto, clave, resumen, libro, libro, libro, clave)
    try:
        return q.enqueue(
            intencion, prioridad="alta", agente="tecnico", perfil="privileged",
            procedencia="healthcheck:%s" % clave, tipo="exec", criticidad="rutina",
            # Lo que tiene que quedar escrito para que el job se pueda cerrar (20-sep-26): la
            # anotación en el libro que el propio encargo pide en su paso 2. Sin esto, un job que
            # choca contra el muro y responde en prosa se cerraba como hecho y la alerta seguía.
            prueba={"tipo": "deuda", "clave": libro},
            # Datetime COMPLETO, no fecha suelta: hasta el 30-jul-26 aquí iba `%Y-%m-%d` y
            # `cola._expired` solo sabía leer el formato largo, así que TODOS estos jobs se
            # marcaban caducados al nacer. El consumidor ya acepta las dos formas; esto quita el
            # tropiezo de raíz en vez de dejarlo a la tolerancia del otro lado.
            expira=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception:
        return None


FRESCURA_FALLOS_UMBRAL = int(os.environ.get("BTP_FRESCURA_FALLOS_UMBRAL", "3"))   # avisa a {{TITULAR}} solo tras N ciclos seguidos
_FRESCURA_FALLOS = os.path.join(STATE, "healthcheck", "frescura_fallos.json")


def _frescura_fallos_inc():
    """Suma 1 al contador de fallos consecutivos del chequeo de frescura y devuelve el total.
    Persistente entre procesos (cada ciclo es un python nuevo). Fail-soft → si no puede leer,
    trata el fallo como el primero (conservador: no dispara antes de tiempo)."""
    try:
        os.makedirs(os.path.dirname(_FRESCURA_FALLOS), exist_ok=True)
        n = 0
        try:
            with open(_FRESCURA_FALLOS, encoding="utf-8") as f:
                n = int((json.load(f) or {}).get("consecutivos", 0))
        except Exception:
            n = 0
        n += 1
        tmp = _FRESCURA_FALLOS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"consecutivos": n}, f)
        os.replace(tmp, _FRESCURA_FALLOS)
        return n
    except Exception:
        return 1


def _frescura_fallos_reset():
    """El chequeo corrió bien → borra el contador (best-effort, nunca lanza)."""
    try:
        if os.path.exists(_FRESCURA_FALLOS):
            os.remove(_FRESCURA_FALLOS)
    except Exception:
        pass


def _dir_plists():
    """Directorio FUENTE DE VERDAD de "que daemons deberian estar vivos en ESTA maquina".

    Son los plists INSTALADOS (~/Library/LaunchAgents), no los del repo. Motivo (14/7/26): los
    plists de tools/launchd/ son la UNION Air+mini y llevan rutas absolutas al home de su dueno
    (/Users/titular); el mini corre copias corregidas a /Users/polaris instaladas aparte. Leer
    el repo hacia que el mini exigiera daemons del PORTATIL (living-context, org-wrap,
    sync-playbook-air, vigia-sesiones) -> alerta IMPOSIBLE de satisfacer, repetida cada 30 min
    durante dias. Lo que launchd puede arrancar aqui es lo que hay instalado aqui; y sus rutas de
    log son las de ESTA maquina, no las del portatil.
    Fallback al repo si no hay nada instalado (maquina recien clonada) para no quedarnos ciegos."""
    inst = os.path.expanduser("~/Library/LaunchAgents")
    import glob as _glob
    if _glob.glob(os.path.join(inst, "com.btp.*.plist")):
        return inst
    return os.path.join(REPO, "tools", "launchd")


def _daemons_keepalive():
    """Set de Labels con KeepAlive=true (bool) en tools/launchd/*.plist. Esos DEBEN estar
    siempre cargados; si faltan del roster es un fallo real (no un interval/calendar que se
    carga en su horario). Detección DINÁMICA: un KeepAlive nuevo se cubre solo, sin lista
    hardcoded que se desactualice. Fail-soft: nunca lanza."""
    import glob
    import plistlib
    out = set()
    for p in glob.glob(os.path.join(_dir_plists(), "com.btp.*.plist")):
        try:
            with open(p, "rb") as fh:
                d = plistlib.load(fh)
            if d.get("KeepAlive") is True and d.get("Label"):
                out.add(d["Label"])
        except Exception:
            continue
    return out


def _daemons_roster():
    """TODOS los Labels declarados en tools/launchd/*.plist (KeepAlive o interval). Es el roster que
    DEBERÍA estar vivo. Detección dinámica (sin lista hardcoded). Fail-soft → set()."""
    import glob
    import plistlib
    out = set()
    for p in glob.glob(os.path.join(_dir_plists(), "com.btp.*.plist")):
        try:
            with open(p, "rb") as fh:
                d = plistlib.load(fh)
            if d.get("Label"):
                out.add(d["Label"])
        except Exception:
            continue
    return out


def _obs_agente_de_label():
    """{label_plist: BTP_AGENT} de TODOS los plists que declaran BTP_AGENT en su EnvironmentVariables
    (p.ej. 'com.btp.correo' → 'asistente', ver tools/launchd/com.btp.correo.plist). Es el mapeo que
    necesita el supersede de daemon_fallando (3): el chequeo por CÓDIGO DE SALIDA vigila el LABEL del
    daemon, pero observabilidad.registrar() traza por AGENTE real (BTP_AGENT), no por label — varios
    labels (correo, correo-urgente) comparten el mismo agente ('asistente'). Detección DINÁMICA (sin
    lista hardcoded que se desactualice), mismo patrón que _roster_meta()/_daemons_roster(). Un label
    sin BTP_AGENT declarado no entra (ese daemon no corre agéntico, no tiene traza que comparar).
    Fail-soft: nunca lanza."""
    import glob
    import plistlib
    out = {}
    for p in glob.glob(os.path.join(_dir_plists(), "com.btp.*.plist")):
        try:
            with open(p, "rb") as fh:
                d = plistlib.load(fh)
        except Exception:
            continue
        label = d.get("Label")
        agente = (d.get("EnvironmentVariables") or {}).get("BTP_AGENT")
        if label and agente:
            out[label] = agente
    return out


def _launchctl_estado():
    """{label: (pid, status)} de los com.btp.* en `launchctl list`. None si no se puede leer.
    pid '-' = no corriendo ahora; status = código de salida de la ÚLTIMA pasada (0 = ok)."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    estado = {}
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        label = parts[-1].strip()
        if label.startswith("com.btp."):
            estado[label] = (parts[0].strip(), parts[1].strip())
    return estado


DISPATCHER_LABEL = "com.btp.dispatcher"


def _dispatcher_apagado_a_proposito():
    """True si el latido rancio del dispatcher tiene una explicación DELIBERADA y no hay que gritar:
    HALT activo (el kill-switch está haciendo su trabajo) o el daemon sin cargar (lazo apagado a
    conciencia — de eso ya avisa el roster de daemons, no hace falta un segundo grito por lo mismo).

    Fail-CLOSED a propósito: si no se puede leer `launchctl list` (None), NO se exime. Un motor que
    puede estar muerto y una consulta que falla no son lo mismo que un apagado deliberado, y aquí el
    error caro es callarse."""
    try:
        import salida as _s
        if _s.halted():
            return True
    except Exception:
        pass
    estado = _launchctl_estado()
    if estado is None:
        return False
    return DISPATCHER_LABEL not in estado


def _parked():
    """Rutinas aparcadas a propósito (no alertar si faltan). Une la lista local (_PARKED_HC) con la
    FUENTE ÚNICA de polaris_estado.PARKED_ROUTINES (la que edita {{TITULAR}}, 22/6) — import perezoso +
    fallback. Así, cuando {{TITULAR}} aparque/encienda algo en paso 2, B1 lo respeta sin tocar dos sitios."""
    parked = set(_PARKED_HC)
    try:
        import polaris_estado
        parked |= set(getattr(polaris_estado, "PARKED_ROUTINES", ()) or ())
    except Exception:
        pass
    return parked


def _roster_meta():
    """{label: (cadencia_seg|None, log_path|None)} de TODOS los plists, en UNA pasada (eficiente).
    cadencia: StartInterval (seg) o StartCalendarInterval (Weekday→semanal, Day→mensual, si no→diaria).
    log: StandardOutPath/StandardErrorPath (la señal de que el daemon CORRIÓ de verdad). Fail-soft → {}."""
    import glob
    import plistlib
    meta = {}
    for p in glob.glob(os.path.join(_dir_plists(), "com.btp.*.plist")):
        try:
            with open(p, "rb") as fh:
                d = plistlib.load(fh)
        except Exception:
            continue
        label = d.get("Label")
        if not label:
            continue
        cad = None
        if isinstance(d.get("StartInterval"), int):
            cad = d["StartInterval"]
        elif "StartCalendarInterval" in d:
            cad = _cadencia_calendario(d["StartCalendarInterval"])
        meta[label] = (cad, d.get("StandardOutPath") or d.get("StandardErrorPath"))
    return meta


def _cadencia_calendario(sci):
    """Segundos del MAYOR hueco entre dos disparos de un StartCalendarInterval (lo más que puede
    pasar sin correr estando sano), o None si no se puede leer.

    25-sep-2026: antes, cualquier entrada con `Weekday` contaba como SEMANAL. La auto-mejora dispara
    lun/mié/vie/dom (hueco máximo: 2 días) y salía con cadencia de 7, así que el aviso de inactividad
    (INACTIVO_FACTOR × cadencia) llegaba a los 21 días en vez de a los 6. Ahora se cuentan los
    disparos reales de una semana tipo (una clave ausente es comodín, como en launchd) y se toma el
    hueco circular más grande. Con `Day`/`Month` se queda en 30 días: los meses no son iguales y
    ahí el conservador es el umbral largo.
    Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026."""
    entradas = sci if isinstance(sci, list) else [sci]
    entradas = [e for e in entradas if isinstance(e, dict)]
    if not entradas:
        return None
    if any("Day" in e or "Month" in e for e in entradas):
        return 30 * 86400
    orden = _disparos_semana(entradas)
    if not orden:
        return None
    semana = 7 * 1440
    huecos = [b - a for a, b in zip(orden, orden[1:])] + [orden[0] + semana - orden[-1]]
    return max(huecos) * 60


def _disparos_semana(entradas):
    """Minutos de la semana (0 = domingo 00:00) en que disparan estas entradas SIN Day/Month, en
    orden. Una clave ausente es comodín, como en launchd. None si alguna es ilegible."""
    minutos = set()
    try:
        for e in entradas:
            dias = [int(e["Weekday"]) % 7] if "Weekday" in e else range(7)   # 0 y 7 = domingo
            horas = [int(e["Hour"])] if "Hour" in e else range(24)
            mins = [int(e["Minute"])] if "Minute" in e else range(60)
            for wd in dias:
                for h in horas:
                    for m in mins:
                        minutos.add(wd * 1440 + h * 60 + m)
    except (TypeError, ValueError):
        return None
    return sorted(minutos)


def _minutos_hasta_disparo(sci, ahora):
    """Minutos hasta el próximo disparo de un StartCalendarInterval desde `ahora` (datetime local),
    o None si no se sabe. Con Day/Month solo se mira el día de hoy (si no toca hoy, queda lejos)."""
    entradas = sci if isinstance(sci, list) else [sci]
    entradas = [e for e in entradas if isinstance(e, dict)]
    if not entradas:
        return None
    try:
        if any("Day" in e or "Month" in e for e in entradas):
            hoy = [e for e in entradas if int(e.get("Day", ahora.day)) == ahora.day
                   and int(e.get("Month", ahora.month)) == ahora.month]
            quedan = [int(e.get("Hour", 0)) * 60 + int(e.get("Minute", 0)) - (ahora.hour * 60 + ahora.minute)
                      for e in hoy]
            quedan = [q for q in quedan if q >= 0]
            return min(quedan) if quedan else None
    except (TypeError, ValueError):
        return None
    orden = _disparos_semana(entradas)
    if not orden:
        return None
    semana = 7 * 1440
    ya = ((ahora.weekday() + 1) % 7) * 1440 + ahora.hour * 60 + ahora.minute   # lunes=0 → domingo=0
    return min((m - ya) % semana for m in orden)


def _check_boca_muda():
    """Canarios de la MORDAZA. Devuelve (alertas, info). Escala por alerta_critica (salta send()).

    Ver capa 3 del plan del muro (14-jul-2026). Un guardian que avisara por la via normal quedaria
    amordazado por la propia mordaza que vigila — por eso usa el canal del codigo rojo."""
    import datetime as _dt
    import glob as _glob
    info, alertas = {}, []
    sal_dir = os.path.join(STATE, "salida")
    hoy = _dt.datetime.now()
    f_hoy = os.path.join(sal_dir, "enviados-%s.jsonl" % hoy.strftime("%Y-%m-%d"))

    def _lineas(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return sum(1 for ln in fh if ln.strip())
        except Exception:
            return 0

    # ── A. el mutis de la bateria NO puede existir en el estado REAL ──
    aud = os.path.join(STATE, "outbox", "audit-%s.jsonl" % hoy.strftime("%Y-%m-%d"))
    try:
        with open(aud, encoding="utf-8") as fh:
            n_mutis = sum(1 for ln in fh if "mutis-test-battery" in ln)
    except Exception:
        n_mutis = 0
    info["mutis_en_estado_real"] = n_mutis
    if n_mutis:
        alertas.append(("mordaza_en_produccion",
                        "El mutis de la bateria de tests ha actuado en el estado REAL (%d veces). "
                        "Eso no deberia poder pasar: Vega puede estar callandose avisos de verdad."
                        % n_mutis))

    # ── B. boca muda: hoy 0 mensajes pasadas las 14:00, y ayer hablaba ──
    n_hoy = _lineas(f_hoy)
    info["enviados_hoy"] = n_hoy
    # El HALT es una mordaza DELIBERADA y conocida: durante una pausa total no sale ni un
    # mensaje, y eso es el kill-switch funcionando, no que me haya quedado mudo. Este canario
    # existe para el silencio que NADIE pidió. Sin esta exención gritaba cada día de HALT
    # (hueco medido: 15 y 16-sep sin un solo envío, con HALT activo hasta el 17), y es la misma
    # exención que el dead-man del dispatcher ya reconoce como legítima.
    try:
        import salida as _s
        if _s.halted():
            info["boca_muda_suprimido_por_halt"] = True
            return alertas, info
    except Exception:
        pass  # fail-soft: ante la duda, que el canario cante
    if hoy.hour >= 14 and n_hoy == 0:
        ayer = (hoy - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        n_ayer = _lineas(os.path.join(sal_dir, "enviados-%s.jsonl" % ayer))
        info["enviados_ayer"] = n_ayer
        if n_ayer >= 3:      # el sistema SI habla normalmente -> el silencio de hoy es anomalo
            alertas.append(("boca_muda",
                            "Hoy no te ha llegado NI UN mensaje mio (ayer salieron %d). "
                            "Puede que me haya quedado mudo y estes perdiendote avisos." % n_ayer))
    return alertas, info


# Servicios que se sondean para saber si el Llavero RESPONDE. No se lee el valor: solo el
# código de salida de `security`. Dos, y de dominios distintos, para poder separar «falta ESE
# ítem» de «el Llavero entero está mudo».
_LLAVERO_SONDAS = ("btp-anthropic-api", "btp-telegram-token")
# errSecInteractionNotAllowed: el ítem existe pero el Llavero no lo entrega sin interacción
# (típico: login.keychain bloqueado tras un reinicio sin sesión gráfica).
_SEC_INTERACCION = 36
_SEC_NO_EXISTE = 44          # errSecItemNotFound: ese ítem concreto no está


# Frase EXACTA con la que la API de Anthropic contesta 400 cuando la cuenta se queda a cero.
# Se busca en los logs de los daemons porque es la única evidencia que no miente: el estimador
# de prepago (`cost_guard.saldo_prepago`) se declaró `fiable: False` el 18-sep-26 mientras
# afirmaba 7,43 USD restantes y siete daemons recibían este 400 desde el día anterior.
_SIN_SALDO = "Credit balance is too low"
_SALDO_VENTANA_H = 24        # filtro barato: un log sin tocar en un día no se abre siquiera
_SALDO_COLA = 65536          # tope de bytes a leer por log y pasada (un delta enorme no se traga entero)
# Offsets ya inspeccionados por log. Sin esto el check no se apaga nunca: estos logs son
# append-only, así que la frase de las 23:00 sigue en la cola a las 03:00 con el saldo ya
# recargado. Visto en vivo el 18-sep-26 — el parte ya se componía y el check seguía en SIN SALDO.
_SALDO_OFFSETS = "saldo_api_offsets.json"


def _check_saldo_api():
    """La cuenta de API sin saldo se dice con ese nombre (18-sep-26).

    EL FALLO QUE LO TRAJO: el parte HOY llevaba cinco días sin componerse. En el libro de deuda
    había cuatro hallazgos —parte viejo, agente parado, boca muda, daemon sin señal— y ninguno
    decía «no hay saldo». Se diagnosticó como Llavero bloqueado (un rc 36 leído desde una sesión
    SSH, donde ese código no significa eso) y se fue a arreglar lo que no estaba roto. La API
    llevaba desde el 17-sep contestando exactamente qué pasaba, en siete logs distintos, y nadie
    lo leía.

    Determinista y barato: ni red, ni LLM, ni estimación. Se lee SOLO lo que el log ha crecido
    desde la pasada anterior, así que la alerta dice «está pasando ahora» y no «pasó alguna vez»:
    con la cola fija, un 400 de hace horas seguía alertando con el saldo ya recargado.

    Arranque en frío (sin offsets guardados): se anotan los tamaños y NO se alerta. Cuesta un
    ciclo de healthcheck. Porqué-no alertar en esa primera pasada: sería leer el pasado del log
    como si fuera el presente, que es justo el fallo que este delta viene a cerrar.

    Devuelve (alertas, info) como el resto de _check_*.
    """
    info, alertas = {}, []
    logs_dir = os.path.join(REPO, "tools", "launchd", "logs")
    afectados, ahora = [], time.time()
    try:
        nombres = sorted(os.listdir(logs_dir))
    except OSError:
        info["estado"] = "sin directorio de logs"
        return alertas, info

    estado_path = os.path.join(HC, _SALDO_OFFSETS)
    try:
        with open(estado_path, encoding="utf-8") as f:
            offsets = json.load(f) or {}
    except Exception:
        offsets = {}
    primera = not offsets
    info["arranque_en_frio"] = primera
    nuevos = {}

    for nombre in nombres:
        if not nombre.endswith(".out"):
            continue
        ruta = os.path.join(logs_dir, nombre)
        try:
            tam = os.path.getsize(ruta)
            nuevos[nombre] = tam
            if (ahora - os.path.getmtime(ruta)) > _SALDO_VENTANA_H * 3600:
                continue
            prev = offsets.get(nombre)
            if prev is None:
                continue                            # primera vez que se ve este log: solo anotar
            if tam < prev:
                prev = 0                            # el log rotó o se truncó: empezar de nuevo
            if tam <= prev:
                continue                            # no ha crecido: no hay nada nuevo que leer
            with open(ruta, "rb") as f:
                f.seek(max(prev, tam - _SALDO_COLA))
                delta = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        if _SIN_SALDO in delta:
            afectados.append(nombre[:-4])

    try:
        os.makedirs(HC, exist_ok=True)
        with open(estado_path, "w", encoding="utf-8") as f:
            json.dump(nuevos, f)
    except OSError:
        pass                                        # fail-soft: esto no puede tumbar el aviso

    info["daemons_sin_saldo"] = afectados
    if not afectados:
        info["estado"] = "sin señal de saldo agotado"
        return alertas, info
    info["estado"] = "SIN SALDO"
    alertas.append((
        "api_sin_saldo",
        "💳 La cuenta de API está a cero: %d daemon(s) (%s) recibieron «%s» en las últimas %d h. "
        "Todo lo que necesita un modelo está parado —el parte de HOY, los agentes, el triaje— y "
        "los daemons pueden seguir saliendo en verde. No es cosa de esta máquina: hay que "
        "recargar el prepago de Anthropic." % (
            len(afectados), ", ".join(afectados[:5]), _SIN_SALDO, _SALDO_VENTANA_H)))
    return alertas, info


def _check_llavero():
    """El Llavero responde. Si no, el sistema parece vivo y no lo está (17-sep-26).

    EL FALLO QUE LO TRAJO: el Mac se reinició el 13-sep 13:26 y el login.keychain se quedó
    bloqueado. Desde ese momento NINGÚN proceso pudo leer una clave. El lazo no se murió de
    golpe, se fue apagando por trozos y cada trozo abrió su propio hallazgo: el parte HOY
    congelado, «correo-triaje parado», «boca muda», wa-tareas sin señal. Cuatro síntomas, cero
    causas, cuatro días. El libro de deuda tenía siete entradas y ninguna decía lo único que
    había que hacer: desbloquear el Llavero.

    Sonda barata y sin secretos: se pide una clave conocida y se mira SOLO el código de salida.
    - rc 36 en las dos sondas -> el Llavero está mudo entero. Alerta, con el comando que lo arregla.
    - rc 44 en una -> falta ese ítem; no es el Llavero. No es asunto de este check (lo ve quien
      la use), y avisar aquí sería un falso positivo cada vez que una integración no esté montada.
    - `security` ausente o cualquier otra cosa -> silencio. Porqué-no alertar: este check existe
      para nombrar una causa raíz conocida, no para inventar alarmas sobre lo que no entiende.

    SOLO CONCLUYE EN LA SESIÓN GRÁFICA (corregido el 18-sep-26, el mismo día que se estrenó).
    El estado desbloqueado del Llavero vive en la sesión de seguridad, y un proceso fuera de
    `Aqua` —una terminal SSH, un job de fondo— recibe rc 36 aunque el Llavero esté perfectamente
    abierto para los LaunchAgents, que son quienes trabajan. La primera versión de este check no
    lo sabía: dijo BLOQUEADO desde una sesión SSH, se dio por causa raíz de que el parte HOY
    llevaba días sin componerse, y la causa era otra (saldo de API agotado). Un detector que
    confunde «no puedo verlo desde aquí» con «está roto» manda a arreglar lo que no estaba roto.

    Devuelve (alertas, info) como el resto de _check_*.
    """
    info, alertas = {}, []
    # ¿Estoy donde se puede concluir? `launchctl managername` dice "Aqua" en la sesión gráfica
    # (donde corren los LaunchAgents com.btp.*) y "Background"/"StandardIO" fuera de ella.
    try:
        mn = subprocess.run(["launchctl", "managername"], capture_output=True, text=True,
                            timeout=5).stdout.strip()
    except Exception:
        mn = ""
    info["contexto"] = mn or "desconocido"
    rcs = {}
    for servicio in _LLAVERO_SONDAS:
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", servicio, "-w"],
                               capture_output=True, text=True, timeout=10)
            rcs[servicio] = r.returncode
        except FileNotFoundError:
            info["estado"] = "sin binario `security` (no es macOS)"
            return alertas, info
        except Exception as e:
            info["estado"] = "sonda no concluyente: %r" % e
            return alertas, info
    info["rc"] = rcs
    if all(v == 0 for v in rcs.values()):
        info["estado"] = "legible"
        return alertas, info
    if all(v == _SEC_INTERACCION for v in rcs.values()):
        if mn != "Aqua":
            # Fuera de la sesión gráfica esto NO distingue «bloqueado» de «no visible desde aquí».
            info["estado"] = "no concluyente (fuera de la sesión gráfica)"
            return alertas, info
        info["estado"] = "BLOQUEADO"
        alertas.append((
            "llavero_bloqueado",
            "🔑 El Llavero está bloqueado: ninguna clave se puede leer, así que TODO lo que "
            "necesita una API (componer el parte HOY, los agentes, los avisos) está parado "
            "aunque los daemons salgan en verde. Se arregla desbloqueándolo una vez: "
            "`security unlock-keychain ~/Library/Keychains/login.keychain-db` (pide tu "
            "contraseña). Pasa tras un reinicio sin sesión gráfica."))
        return alertas, info
    faltan = [s for s, v in rcs.items() if v == _SEC_NO_EXISTE]
    info["estado"] = ("faltan ítems: %s" % ", ".join(faltan)) if faltan else "mixto"
    return alertas, info


RECURSOS_INTERVAL_H = 1     # anti-spam del aviso; la MEDIDA se toma en cada vuelta
RECURSOS_SWAP_GB = 10.0     # swap usado a partir del cual esto ya no es uso normal
RECURSOS_SWAP_TIBIO_GB = 6.0
RECURSOS_LIBRE_PCT = 12.0   # ...si además queda poca memoria libre, es ahogo de verdad
RECURSOS_VORAZ_FRAC = 0.75  # un proceso que pide 3/4 de la RAM de la máquina ya va al swap


def _ram_fisica_gb():
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                  timeout=5).stdout.strip()) / 1073741824.0
    except Exception:
        return None


def _recursos_ahora():
    """(swap_usado_gb, libre_pct, [(footprint_gb, pid, comando), ...]) o None si no se puede.

    El footprint sale de `top`, no de `ps`: un proceso que se ha ido entero al swap tiene un RSS
    ridículo (el que tumbó la máquina el 20-sep marcaba 42 MB de RSS con 16 GB de footprint), así
    que buscarlo por RSS es mirar justo donde no está."""
    import re as _re
    try:
        swap = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True,
                              timeout=5).stdout
        usado = float(_re.search(r"used\s*=\s*([\d.]+)M", swap).group(1)) / 1024.0
    except Exception:
        return None
    libre_pct = float("nan")
    try:
        mp = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10).stdout
        libre_pct = float(_re.search(r"free percentage:\s*(\d+)", mp).group(1))
    except Exception:
        pass
    gordos = []
    try:
        top = subprocess.run(["top", "-l", "1", "-o", "mem", "-n", "5", "-stats", "pid,command,mem"],
                             capture_output=True, text=True, timeout=30).stdout
        for ln in top.splitlines():
            m = _re.match(r"\s*(\d+)\s+(.+?)\s+([\d.]+)([KMGT])\s*$", ln)
            if m:
                gb = float(m.group(3)) * {"K": 1 / 1048576.0, "M": 1 / 1024.0, "G": 1.0,
                                          "T": 1024.0}[m.group(4)]
                gordos.append((round(gb, 1), int(m.group(1)), m.group(2).strip()))
    except Exception:
        pass
    return usado, libre_pct, sorted(gordos, reverse=True)[:5]


def _check_recursos():
    """La casa base se está ahogando en memoria. Salud OPERATIVA, nunca código rojo.

    EL FALLO QUE LO TRAJO (20-sep-26): `visor3d.py --organo mama tumor` pidió 16 GB en un Mac
    mini de 16 porque sobremuestreaba el volumen entero para dibujar un tumor de 2 cm. El swap
    llegó a 21,4 de 22,5 GB, la máquina se arrastró durante media hora y hubo que reiniciarla.
    NADIE avisó: lo notó {{TITULAR}}. La causa concreta ya está arreglada (el recorte a la caja en
    `_malla`, tests/test_visor3d_malla_recorte.py), pero lo que este check cubre es la clase
    entera: cualquier proceso que se coma la máquina tiene que decirlo él, no ella.

    Por qué NO dispara a la primera: un Mac de 16 GB con el Claude de escritorio, Chrome y diez
    sesiones abiertas usa swap de forma perfectamente sana, y un pico de un minuto durante un
    build no es un problema. Alerta solo cuando la vuelta ANTERIOR también estaba mal, que es lo
    que distingue «está apretado» de «se está ahogando» (misma lección que _check_llavero: un
    detector que confunde carga con avería manda a arreglar lo que no está roto).

    Mide en cada vuelta y guarda la medida; el AVISO va con throttle de ~1h. No mata nada:
    matar un proceso es irreversible y puede ser media hora de cómputo clínico de {{TITULAR}}.

    Devuelve (alertas, info) como el resto de _check_*.
    """
    import json as _json
    state_p = os.path.join(HC, "recursos.json")
    prev = {}
    try:
        prev = _json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pass
    if not isinstance(prev, dict):      # un state corrupto no puede tirar el check
        prev = {}

    ahora = _recursos_ahora()
    if ahora is None:
        return [], {"error": "no se pudo leer vm.swapusage"}
    swap_gb, libre_pct, gordos = ahora
    # Señal TEMPRANA: un solo proceso que ya pide más memoria de la que la máquina tiene va a
    # acabar en el swap sí o sí, aunque de momento el sistema respire. El 20-sep esto habría
    # avisado a los dos minutos; el swap desbordado no llegó hasta la media hora.
    ram_gb = _ram_fisica_gb()
    voraz = gordos[0] if (gordos and ram_gb and gordos[0][0] >= RECURSOS_VORAZ_FRAC * ram_gb) \
        else None
    ahogo = bool(voraz) or swap_gb >= RECURSOS_SWAP_GB or (
        swap_gb >= RECURSOS_SWAP_TIBIO_GB and libre_pct == libre_pct
        and libre_pct <= RECURSOS_LIBRE_PCT)

    info = {"swap_usado_gb": round(swap_gb, 1), "libre_pct": libre_pct, "ahogo": ahogo,
            "voraz": voraz, "ram_gb": ram_gb, "gordos": gordos[:3], "ts": time.time()}
    try:
        _ensure()
        _write_atomic(state_p, dict(info, aviso_ts=prev.get("aviso_ts", 0)))
    except Exception:
        pass

    if not (ahogo and prev.get("ahogo")):       # hace falta la segunda vuelta seguida
        return [], info
    if (time.time() - prev.get("aviso_ts", 0)) / 3600.0 < RECURSOS_INTERVAL_H:
        return [], dict(info, throttled=True)
    try:
        _write_atomic(state_p, dict(info, aviso_ts=time.time()))
    except Exception:
        pass
    libre_txt = int(libre_pct) if libre_pct == libre_pct else "?"
    if voraz:
        return (["`%s` (pid %d) pide %.0f GB en una máquina de %.0f: va a acabar en el swap y a "
                 "dejar la casa base arrastrándose. Swap %.1f GB, %s%% libre. No lo he tocado."
                 % (voraz[2], voraz[1], voraz[0], ram_gb, swap_gb, libre_txt)], info)
    quien = ("; el mayor: %s (pid %d, %.1f GB)" % (gordos[0][2], gordos[0][1], gordos[0][0])
             if gordos else "")
    return (["La casa base se está ahogando en memoria: %.1f GB de swap, %s%% libre%s. "
             "Sigue viva, pero va a ir lenta hasta que eso baje."
             % (swap_gb, libre_txt, quien)], info)


CPU_CARGA_FACTOR = float(os.environ.get("BTP_CPU_CARGA_FACTOR") or 2.0)


def _cpu_ahora():
    """(carga media de 5 min, núcleos, [(%cpu, pid, nombre)] de los 3 que más gastan) o None."""
    try:
        carga = os.getloadavg()[1]
        nucleos = os.cpu_count() or 1
    except Exception:
        return None
    top = []
    try:
        out = subprocess.run(["/bin/ps", "-Aro", "pcpu=,pid=,comm="], capture_output=True,
                             text=True, timeout=10).stdout
        for linea in out.splitlines()[:3]:
            pc, pid, comm = linea.strip().split(None, 2)
            top.append((float(pc), int(pid), os.path.basename(comm)))
    except Exception:
        pass
    return carga, nucleos, top


def _check_cpu(ahora_fn=_cpu_ahora):
    """La casa base lleva rato con la CPU saturada. Salud OPERATIVA, nunca código rojo.

    EL FALLO QUE LO TRAJO (26-sep-26): tres Chrome headless huérfanos de un script de captura
    muerto pasaron ~27 h con dos pestañas cada uno al 100 %. La carga del Mac estuvo en 46-82
    con 10 núcleos, los 9 hooks de cada Bash pasaron de 0,7 s a 6,5 s y todo Polaris iba lento.
    Lo notó {{TITULAR}} («cada tarea tarda muchísimo»), no el sistema: `_check_recursos` mira memoria
    y swap, y la memoria estaba bien. La causa concreta ya la limpia `bucles_colgados.run_chrome`;
    esto cubre la clase entera: cualquier cosa que se coma la CPU tiene que decirlo ella.

    Mismo criterio que _check_recursos: carga media de 5 min por encima de CPU_CARGA_FACTOR ×
    núcleos DOS vueltas seguidas (un build o una suite de tests no es un ahogo), aviso con
    throttle de ~1 h, y no mata nada. Dice quién gasta para que se pueda actuar.
    """
    import json as _json
    state_p = os.path.join(HC, "cpu.json")
    prev = {}
    try:
        prev = _json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pass
    if not isinstance(prev, dict):
        prev = {}
    ahora = ahora_fn()
    if ahora is None:
        return [], {"error": "no se pudo leer la carga"}
    carga, nucleos, top = ahora
    ahogo = carga >= CPU_CARGA_FACTOR * nucleos
    info = {"carga_5m": round(carga, 1), "nucleos": nucleos, "ahogo": ahogo, "top": top,
            "ts": time.time()}
    try:
        _ensure()
        _write_atomic(state_p, dict(info, aviso_ts=prev.get("aviso_ts", 0)))
    except Exception:
        pass
    if not (ahogo and prev.get("ahogo")):
        return [], info
    if (time.time() - prev.get("aviso_ts", 0)) / 3600.0 < RECURSOS_INTERVAL_H:
        return [], dict(info, throttled=True)
    try:
        _write_atomic(state_p, dict(info, aviso_ts=time.time()))
    except Exception:
        pass
    quien = "; ".join("%s (pid %d, %.0f %%)" % (n, pid, pc) for pc, pid, n in top) or "?"
    return (["La casa base lleva rato con la CPU saturada: carga %.0f con %d núcleos. Todo va a ir "
             "lento. Lo que más gasta: %s. No he tocado nada." % (carga, nucleos, quien)], info)


# Sonda del cerebro (24-sep-26): primer intento corto; si falla por algo TRANSITORIO (timeout,
# no se puede crear el proceso), espera y reintenta más largo antes de avisar.
CEREBRO_TIMEOUT_S = 30
CEREBRO_REINTENTO_TIMEOUT_S = 60
CEREBRO_ESPERA_S = float(os.environ.get("BTP_CEREBRO_ESPERA_S", "15"))


def _check_cerebro_alcanzable():
    """Canario del CEREBRO: ¿puede el LAZO ejecutar de verdad un cerebro? Devuelve (alertas, info).

    Hueco real (14-jul-2026): `ia.health()` NUNCA ejecuta el binario — para él "disponible" = hay
    clave y no hay bloqueo de prepago. Si un mal deploy, un PATH roto en un plist o un update de
    Homebrew se llevan /opt/homebrew/bin/claude por delante, la caché de salud sigue diciendo
    "Claude vivo" y NADIE se entera: lo único que se ve es que las tareas clínicas empiezan a
    pararse, una a una, sin explicación. Aquí se ejecuta DE VERDAD (`--version`, no gasta tokens).

    Solo afirma cuando corre DENTRO del lazo (launchd). Por ssh el binario no está en el PATH, y un
    "no lo encuentro" desde ahí no dice NADA del lazo — sería exactamente el falso rojo que causó el
    incidente que motivó todo esto."""
    info, alertas = {}, []
    try:
        _, es_lazo = salida._origen()
    except Exception:
        es_lazo = False
    info["es_lazo"] = es_lazo
    if not es_lazo:
        info["saltado"] = "fuera de launchd: un binario ausente aquí no dice nada del lazo"
        return alertas, info

    binario = os.environ.get("BTP_CLAUDE_BIN", "claude")
    ruta = shutil.which(binario)
    info["binario"], info["ruta"] = binario, (ruta or "")
    if not ruta:
        alertas.append(("cerebro_inalcanzable",
                        "El LAZO no encuentra el binario del cerebro (%s) en su PATH. Las tareas "
                        "clínicas van a PARARSE y la caché de salud no lo detecta (nunca ejecuta el "
                        "binario). Revisa el PATH de los plists o reinstala Claude Code." % binario))
        return alertas, info

    # ROTO vs AHOGADO (24-sep-2026, deuda cerebro_inalcanzable 4x). Medido en healthcheck.out:
    # 8 fallos en 3.293 lecturas y los 8 TRANSITORIOS (5 TimeoutExpired, 3 BlockingIOError = no
    # se podía ni crear un proceso), ninguno de binario roto. El de hoy coincidió con 21,8 GB de
    # swap. Un solo intento los contaba como «no puede ejecutarlo», abría deuda con cada uno y
    # dejaba test_all rojo para todas las sesiones, mientras la causa (memoria) ya la avisa
    # _check_recursos. Ahora: lo transitorio se reintenta una vez, y si sigue es `cerebro_lento`;
    # `cerebro_inalcanzable` queda solo para lo roto, y siempre dice POR QUÉ.
    def _probar(timeout):
        try:
            r = subprocess.run([ruta, "--version"], capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, BlockingIOError) as e:
            return "transitorio", repr(e)[:120]
        except Exception as e:
            return "roto", repr(e)[:120]
        if r.returncode != 0:
            return "roto", "rc=%d %s" % (r.returncode, (r.stderr or r.stdout or "").strip()[:120])
        info["version"] = (r.stdout or "").strip()[:60]
        return "ok", ""

    estado, motivo = _probar(CEREBRO_TIMEOUT_S)
    if estado == "transitorio":
        info["transitorio"] = motivo
        time.sleep(CEREBRO_ESPERA_S)
        estado, motivo2 = _probar(CEREBRO_REINTENTO_TIMEOUT_S)
        motivo = "%s; reintento: %s" % (motivo, motivo2 or "ok")
    if estado == "ok":
        return alertas, info
    info["error"] = motivo
    if estado == "transitorio":
        alertas.append(("cerebro_lento",
                        "`claude --version` no responde en %d+%d s (%s). Suele ser la máquina "
                        "ahogada: mira el aviso de memoria. El binario está; si persiste, las "
                        "tareas del lazo se retrasan."
                        % (CEREBRO_TIMEOUT_S, CEREBRO_REINTENTO_TIMEOUT_S, motivo)))
    else:
        alertas.append(("cerebro_inalcanzable",
                        "El LAZO encuentra el binario del cerebro (%s) pero NO puede ejecutarlo "
                        "(%s). Las tareas clínicas van a PARARSE." % (ruta, motivo)))
    return alertas, info
    try:
        r = subprocess.run([ruta, "--version"], capture_output=True, text=True, timeout=30)
        info["version"] = (r.stdout or "").strip()[:60]
        ok = (r.returncode == 0)
    except Exception as e:
        ok, info["error"] = False, repr(e)[:120]
    if not ok:
        # AUTO-REPARACIÓN (24-sep-2026, deuda `cerebro_inalcanzable`, 4 detecciones desde el
        # 15-sep). `~/.local/bin/claude` es un ENLACE a un binario CON VERSIÓN
        # (…/versions/2.1.236): cuando Claude Code se actualiza y esa versión desaparece, el
        # enlace queda colgando, `which` lo sigue encontrando y el lazo se queda sin cerebro. Aquí
        # se repunta al binario más nuevo que SÍ exista. Acotado: solo si el destino actual no
        # existe, solo dentro de `versions/`, y solo si el candidato ejecuta de verdad.
        reparado = _repara_enlace_cerebro(ruta)
        if reparado:
            info["autofix"] = reparado
            alertas.append(("cerebro_enlace_repuntado",
                            "El enlace del cerebro apuntaba a una versión que ya no existe (una "
                            "actualización de Claude Code) y el lazo se había quedado sin cerebro. "
                            "Lo he repuntado a %s y responde. Queda dicho por si vuelve a pasar."
                            % reparado))
        else:
            alertas.append(("cerebro_inalcanzable",
                            "El LAZO encuentra el binario del cerebro (%s) pero NO puede "
                            "ejecutarlo, y no he podido repararlo solo. Las tareas clínicas van a "
                            "PARARSE." % ruta))
    return alertas, info


def _repara_enlace_cerebro(ruta):
    """Repunta `ruta` (un enlace colgante) al binario más nuevo de `versions/`. Devuelve la versión
    nueva si quedó ejecutable, o None. No toca nada si el destino actual existe: entonces el fallo
    es otro y hay que mirarlo, no taparlo."""
    try:
        if not os.path.islink(ruta) or os.path.exists(os.path.realpath(ruta)):
            return None
        versiones = os.path.join(os.path.dirname(os.path.realpath(ruta)))
        if os.path.basename(versiones) != "versions":
            return None
        cands = [os.path.join(versiones, f) for f in os.listdir(versiones)]
        cands = [c for c in cands if os.path.isfile(c) and os.access(c, os.X_OK)]
        if not cands:
            return None
        nuevo = max(cands, key=os.path.getmtime)
        tmp = ruta + ".nuevo"
        os.symlink(nuevo, tmp)
        os.replace(tmp, ruta)          # atómico: nadie ve el enlace a medias
        r = subprocess.run([ruta, "--version"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return os.path.basename(nuevo)
    except Exception:
        return None


def _check_roster_daemons():
    """B1 — VIGÍA DE ROSTER: caza daemons que se cayeron del arranque o fallan, sin que nadie lo note.
    CUATRO clases (las tres últimas cubren lo que el primer corte no veía — los monitores de redes, que
    son interval, no KeepAlive):
      · KeepAlive CAÍDO (debe estar siempre vivo): kickstart × 1 → autofix silencioso o alerta humana.
      · Interval/calendar DESCARGADO del todo (bootout): no se ejecutará nunca más → alerta. NO se
        auto-carga (encender una rutina = gate de {{TITULAR}}; y kickstart no levanta un job descargado).
      · Cualquiera del roster CARGADO pero con salida ≠ 0 en su última pasada (PID '-'): falló → alerta.
      · Interval CARGADO pero SIN TRABAJAR (A): su log lleva > INACTIVO_FACTOR×cadencia sin tocarse →
        no está disparando aunque esté cargado. Conservador (solo si cadencia y log son determinables).
    Excluye _ROSTER_SKIP (tienen check de RESPUESTA dedicado en 3b/3c) y lo aparcado (_parked()).
    Devuelve (alertas, info). Tuplas (clave, texto) → respeta el anti-flapping. Fail-soft."""
    alertas, info = [], {}
    estado = _launchctl_estado()
    if estado is None:
        info["error"] = "launchctl ilegible"
        return alertas, info
    cargados = set(estado)
    parked = _parked()
    keepalive = _daemons_keepalive() - _ROSTER_SKIP - parked
    roster = _daemons_roster() - _ROSTER_SKIP - parked
    interval = roster - keepalive          # los que se cargan en su horario (x-*, dm-inbox, prensa…)

    # 1) KeepAlive caídos → kickstart × 1 (autofix acotado; launchd los gestiona).
    ka_caidos = sorted(keepalive - cargados)
    info["keepalive_caidos"] = ka_caidos
    for label in ka_caidos:
        revivio = False
        if _kickstart_daemon(label):
            time.sleep(KICKSTART_WAIT)
            try:
                r = subprocess.run(["launchctl", "list", label], capture_output=True, timeout=5)
                revivio = (r.returncode == 0)
            except Exception:
                revivio = False
        if revivio:
            alertas.append(("daemon_roster_autofix:%s" % label,
                            "Reviví solo el daemon %s (se había caído del arranque)." % label))
            _marcar_acuse_autofix("daemon_roster_caido:%s" % label, "kickstart")
        else:
            alertas.append(("daemon_roster_caido:%s" % label,
                            "El daemon %s se ha caído del arranque y no he podido revivirlo solo; "
                            "hay que mirarlo." % label))

    # 2) Interval/calendar DESCARGADOS del todo → no se relanzan solos (encender = gate). Solo aviso.
    iv_descargados = sorted(interval - cargados)
    info["interval_descargados"] = iv_descargados
    for label in iv_descargados:
        corto = label.replace("com.btp.", "")
        alertas.append(("daemon_descargado:%s" % label,
                        "El daemon «%s» está descargado del arranque: no se ejecutará. "
                        "Si debe vigilar, hay que encenderlo." % corto))

    # 3) Cualquier daemon del roster CARGADO pero que falló su última pasada (exit > 0, sin PID vivo).
    #    'isdigit' solo casa enteros positivos → ignora '-' (sin status) y los negativos (señales).
    #    PERO si hay bloqueo por DINERO (tope local / crédito), un exit≠0 casi siempre es "aplazado por
    #    saldo", no un fallo de código → ya lo explica el aviso de saldo de _salud_daemons; no lo
    #    dupliques aquí (parsimonia del lector). Mismo criterio que _salud_daemons (line 442).
    bloqueado_por_dinero = False
    try:
        ok_s, _, _ = cost_guard.check_before_job(esencial=False)
        bloqueado_por_dinero = not ok_s
    except Exception:
        pass
    info["fallando_suprimido_por_saldo"] = bloqueado_por_dinero
    #    Y el 75 (EX_TEMPFAIL) NO es un fallo: es como `run_agent.sh` dice «aplazo esto a propósito»
    #    (Claude no disponible, cadena de modelos agotada por límite de capacidad, tope local de
    #    gasto). El 22-sep-26 tres daemons —asistente, correo, git-barrido— llevaban 5, 5 y 9
    #    detecciones en el libro de deudas con `.err` lleno de «cadena de modelos agotada por límite
    #    → aplazo», y `launchctl list` dándolos en 0. Un aplazo declarado no es un daemon roto:
    #    contarlo como fallo tapa los fallos de verdad con ruido y pone la suite en rojo por nada.
    APLAZO_DELIBERADO = 75
    fallando_bruto = [] if bloqueado_por_dinero else sorted(
        lbl for lbl in (roster & cargados)
        if estado[lbl][0] == "-" and estado[lbl][1].isdigit()
        and int(estado[lbl][1]) > 0 and int(estado[lbl][1]) != APLAZO_DELIBERADO)
    info["fallando"] = {lbl: estado[lbl][1] for lbl in fallando_bruto}
    # SUPERSEDE (3/7/26, hermano del que ya usan _salud_daemons/seguimiento._heartbeats_problema):
    # el exit-code de `launchctl list` es la última pasada CONOCIDA, pero puede quedarse "fallando"
    # aunque el daemon ya haya vuelto a correr bien DESPUÉS (launchd no siempre refresca el status
    # hasta el próximo disparo, y un interval con cadencia larga puede tardar en pisar el exit viejo).
    # A diferencia del supersede por heartbeat, el exit-code NO trae un ts del momento del fallo que
    # comparar — así que el criterio es más simple: ¿la traza MÁS RECIENTE de observabilidad para el
    # BTP_AGENT real de este daemon (ver _obs_agente_de_label) es 'ok'? Si sí, se tapa (una pasada
    # posterior ya fue bien). Si la más reciente es 'fail' (o no hay ninguna traza) → sigue alertando,
    # el supersede no enmascara un fallo que se repite. Un label sin BTP_AGENT declarado (no corre
    # agéntico) no tiene traza que comparar → se alerta igual (conservador, sin cambio de comportamiento).
    agente_de_label = _obs_agente_de_label()
    fallando, supersedidos = [], {}
    for label in fallando_bruto:
        agente = agente_de_label.get(label)
        tapado = False
        if agente:
            try:
                import observabilidad as _obs
                tapado = _obs.ultimo_resultado(agente) == "ok"
            except Exception:
                tapado = False
        if tapado:
            supersedidos[label] = agente
        else:
            fallando.append(label)
    if supersedidos:
        info["fallando_supersedido_por_ok_posterior"] = supersedidos
    # AUTOFIX ACOTADO (3/7/26, acuse automático — extiende el kickstart×1 que ya usan
    # KeepAlive-caído/heartbeat-rancio a esta clase): un daemon cargado con exit≠0 puede haberse
    # atascado en un estado transitorio; `launchctl kickstart -k` fuerza una pasada limpia YA, igual
    # que hace el resto del fichero. Máx. 1 intento por ciclo (sin bucle). El intento se marca YA en
    # el acuse (_marcar_acuse_autofix, por="autofix") para que _emitir_si_cambia (más abajo) sepa que
    # esta clave "ya se puso sola" y hable con la voz "🔧 me pongo a arreglarlo" en vez de tratarla
    # como una clave nueva sin autofix ("🔧 lo estoy mirando"). El resultado del kickstart NO decide
    # si se alerta (ese status no se refleja al instante en `launchctl list`; la próxima pasada real
    # ya lo dirá) — solo deja constancia de que el sistema ya actuó.
    # SIN RED (13-sep-26): la noche del 11 al 12-sep com.btp.correo salió con exit≠0 porque el Mac no
    # resolvía DNS, y entró al libro como daemon_fallando:com.btp.correo. Sin DNS, un exit≠0 no prueba
    # nada del daemon: se avisa de la red y no se kickstartea (relanzarlo sin red no arregla nada). El
    # exit se queda en launchctl, así que si el fallo era de verdad lo ve la siguiente pasada con red.
    # `fallando` se conserva para que más abajo no se evalúen como «inactivos».
    sin_red = bool(fallando) and not _hay_dns()
    if sin_red:
        info["fallando_sin_dns"] = list(fallando)
        alertas.append(("red_sin_dns", RED_SIN_DNS_TEXTO))
    a_alertar = [] if sin_red else fallando
    for label in a_alertar:
        _kickstart_daemon(label)
        _marcar_acuse_autofix("daemon_fallando:%s" % label, "kickstart")
    for label in a_alertar:
        corto = label.replace("com.btp.", "")
        alertas.append(("daemon_fallando:%s" % label,
                        "El daemon «%s» está cargado pero falló su última pasada (código %s)."
                        % (corto, estado[label][1])))

    # 4) "CARGADO PERO SIN TRABAJAR" (A): un interval cargado y exit 0 puede llevar días sin DISPARAR
    #    (horario mal puesto, o no hace nada). Señal: su log lleva > INACTIVO_FACTOR × cadencia esperada
    #    sin tocarse. Conservador (solo si cadencia Y log son determinables; si no, no se alerta). Mismo
    #    money-gate que 'fallando' (sin saldo → inactividad esperada → no alertar). No aplica a KeepAlive.
    inactivos = {}
    # El HALT suprime esta alerta ENTERA, igual que el money-gate de arriba. Durante un HALT los
    # daemons SÍ arrancan, pero se paran en seco y escriben en stderr («MURO: HALT activo → no
    # estaciono»), así que su .out no se toca y este check los canta como muertos. Medido el
    # 20-sep-26: `daemon_inactivo:com.btp.wa-tareas` llevaba **1.177 detecciones en 42 días** de un
    # daemon que funciona — corrió esa misma mañana y estacionó 191 mensajes. Eso no es vigilancia,
    # es fatiga de alarma: entrena a todo el mundo a ignorar el canal donde algún día habrá algo
    # de verdad. Y avisar de inactividad mientras el sistema está en pausa TOTAL no informa de
    # nada: que no trabajen es justo lo que el HALT pide.
    try:
        import salida as _s
        if _s.halted():
            info["inactivos_h"] = {}
            info["inactivos_suprimidos_por_halt"] = True
            return alertas, info
    except Exception:
        pass  # fail-soft, como los otros dos usos del HALT en este fichero
    if not bloqueado_por_dinero:
        meta = _roster_meta()
        ahora = time.time()
        for label in sorted((interval & cargados) - set(fallando)):
            cad, log = meta.get(label, (None, None))
            if not cad or not log:
                continue                            # cadencia/log indeterminables → no alertamos (conservador)
            umbral = max(INACTIVO_FACTOR * cad, INACTIVO_MIN_SEG)
            # El HEARTBEAT manda sobre el mtime del log: un daemon puede correr con
            # ÉXITO sin escribir nada en stdout, dejando el log viejo (falso rojo de
            # seguridad-sweep 17-jul: corría a diario 06:00 con exit 0 pero el .out
            # no se tocaba desde días atrás → «77h sin señal»). Si su latido propio
            # (state/heartbeat/<corto>.json) está fresco, HAY actividad real → no
            # es «sin señal». Solo suprime; si no hay latido, cae al chequeo de log.
            corto = label.replace("com.btp.", "")
            hb_edad_h, _hb_est, hb_corrupto = _leer_heartbeat(corto)
            if not hb_corrupto and hb_edad_h is not None and hb_edad_h * 3600 <= umbral:
                continue
            try:
                edad = ahora - os.path.getmtime(log)
            except OSError:
                continue                            # sin log aún → no hay baseline de actividad
            if edad > umbral:
                inactivos[label] = round(edad / 3600.0, 1)
    info["inactivos_h"] = inactivos
    for label, h_ in inactivos.items():
        corto = label.replace("com.btp.", "")
        alertas.append(("daemon_inactivo:%s" % label,
                        "El daemon «%s» está cargado pero lleva ~%.0f h sin dar señal de actividad "
                        "(debería correr mucho más a menudo). Conviene mirar si su horario está bien." % (corto, h_)))
    return alertas, info


def _auto_recover_schema():
    """B2: re-encola jobs caídos a failed/ por `schema-desconocido` (skew worktree→casa base: el
    consumidor aún no conocía un campo nuevo). Recuperable: re-encolar basta una vez que el consumidor
    se fusiona y recarga. NUNCA toca schema-invalido (plantado permanente; recover() ya lo excluye).

    Freno ANTI-BUCLE (el riesgo real): si un job rebota RECOVER_ESCALA veces seguidas y sigue cayendo,
    el consumidor SIGUE sin conocer el campo (falta fusionar) → se ESCALA a {{TITULAR}} en vez de re-encolar
    en silencio para siempre. El ledger de rebotes se purga solo cuando el job sale de failed/ (se
    recuperó de verdad). Determinista, fail-soft. Devuelve (alertas, info)."""
    alertas, info = [], {}
    try:
        ledger = json.load(open(RECOVER_LEDGER, encoding="utf-8"))
        if not isinstance(ledger, dict):
            ledger = {}
    except Exception:
        ledger = {}
    try:
        cand = [str(x) for x in q.recover(patron="schema-desconocido", dry_run=True).get("reencolados", [])]
    except Exception as e:
        info["error"] = "%r" % e
        return alertas, info
    info["candidatos"] = cand
    if not cand:
        try:
            os.remove(RECOVER_LEDGER)   # nada pendiente → resetea el ledger
        except OSError:
            pass
        return alertas, info

    # +1 a los candidatos que SIGUEN en failed/ este ciclo; lo que ya no está, se purga (count resetea).
    nuevo = {jid: ledger.get(jid, 0) + 1 for jid in cand}
    persistentes = sorted(jid for jid, c in nuevo.items() if c >= RECOVER_ESCALA)
    try:
        with open(RECOVER_LEDGER, "w", encoding="utf-8") as f:
            json.dump(nuevo, f, ensure_ascii=False)
    except Exception:
        pass

    reencolados = []
    if not persistentes:                      # solo re-encola si no hay un atasco que escalar
        try:
            reencolados = [str(x) for x in q.recover(patron="schema-desconocido", max_jobs=RECOVER_MAX).get("reencolados", [])]
        except Exception as e:
            info["error"] = "%r" % e
    info["reencolados"] = reencolados
    info["persistentes"] = persistentes

    if persistentes:
        alertas.append(("cola_recover_atascado",
                        "%d tarea(s) caen una y otra vez por un cambio de formato que el sistema aún no "
                        "reconoce. Hay que fusionar y recargar; luego se recuperan solas." % len(persistentes)))
    elif reencolados:
        alertas.append(("cola_recover_schema",
                        "Re-encolé %d tarea(s) que se habían caído por un cambio de formato (ya recuperadas)." % len(reencolados)))
    return alertas, info


def _puerto_vivo(host, port, timeout=HTTP_TIMEOUT_S):
    """True si el puerto acepta conexión TCP (el daemon está levantado). No necesita token: que el
    gateway responda 401 ya prueba que está vivo, así que basta con que el TCP conecte. Fail-soft."""
    import socket
    try:
        s = socket.create_connection((host, port), timeout)
        s.close()
        return True
    except Exception:
        return False


def _clave_frescura(aviso):
    """Clave ESTABLE para un aviso de frescura: sin los contadores, que cambian cada vuelta."""
    return "frescura_otro:" + re.sub(r"\d+", "N", aviso)[:40]


def _hay_dns(host="api.telegram.org", timeout=3):
    """¿Resuelve nombres el Mac? Solo DNS: `getaddrinfo`, sin abrir conexión ni mandar un byte.

    Existe por la noche del 11 al 12-sep-2026: el Mac se quedó sin DNS 11 h y el healthcheck marcó
    `daemon_bot-telegram_parado` 21 pasadas seguidas. El bot estaba sano; su .err tenía 11.645
    `gaierror(8, 'nodename nor servname')` y cada kickstart lo relanzaba igual de ciego. Sin red,
    un latido rancio no prueba nada del daemon. `getaddrinfo` no acepta timeout y un resolver
    colgado puede tardar decenas de segundos, así que corre en un hilo con tope. Colgado o fallo
    → False. Nunca lanza."""
    import socket
    import threading
    res = {}

    def _resolver():
        try:
            res["ok"] = bool(socket.getaddrinfo(host, 443))
        except Exception:
            res["ok"] = False

    t = threading.Thread(target=_resolver, daemon=True)
    t.start()
    t.join(timeout)
    return res.get("ok", False)


RED_SIN_DNS_TEXTO = ("El Mac no tiene red: el bot no puede leer Telegram ni entra el correo. Vuelve solo "
                     "cuando vuelva la conexión (no reinicio nada: sin red no serviría).")


def _alertas_frescura(fav):
    """Avisos de seguimiento.frescura → tuplas (clave_estable, texto) para el aviso de salud.

    La clave se deriva del prefijo del texto (sin números) para que sea estable. SIN RED (13-sep-26):
    la noche del 11 al 12-sep, `correo-imap` agotó reintentos sin DNS, escribió su latido en 'fallo'
    y entró al libro como `frescura_agente_fallo:correo-imap`, que ya iba por 2 remisiones (a la 3ª
    queda intermitente para siempre). Sin DNS, ni un 'fallo' ni un silencio de agente prueban nada
    del agente: esos avisos se funden en un único `red_sin_dns`. El de HOY.md no se toca. El DNS se
    sondea solo si hay algún aviso de agente. Nunca lanza."""
    alertas, de_agente = [], []
    for aviso in fav:
        if "HOY.md" in aviso:
            clave = "frescura_hoy_desactualizado"
        elif "última ejecución" in aviso or "falló" in aviso.lower():
            # Extraer el nombre del agente del texto para diferenciar claves
            clave = "frescura_agente_fallo:" + aviso.split("'")[1] if "'" in aviso else "frescura_agente_fallo"
        elif "parado" in aviso or "sin señales" in aviso:
            clave = "frescura_agente_parado:" + aviso.split("'")[1] if "'" in aviso else "frescura_agente_parado"
        else:
            # SIN NÚMEROS EN LA CLAVE (19-sep-2026). Aquí iba `aviso[:40]` tal cual, y esos 40
            # caracteres incluyen los contadores: «🌿 2 rama(s) … (7 commit[s])». Cada vez que
            # cambiaba el número nacía una clave NUEVA: alerta nueva, acuse nuevo, encargo nuevo
            # y entrada nueva en el libro de deuda, por la MISMA condición. Había seis variantes
            # vivas del mismo aviso. La regla ya estaba escrita en este módulo —la clave es
            # estable, el texto lleva el detalle—; el código no la cumplía.
            clave = _clave_frescura(aviso)
        (de_agente if clave.startswith("frescura_agente_") else alertas).append((clave, aviso))
    if de_agente and not _hay_dns():
        alertas.append(("red_sin_dns", RED_SIN_DNS_TEXTO))
    else:
        alertas.extend(de_agente)
    return alertas


def _una_red_sin_dns(alertas):
    """`red_sin_dns` puede salir de frescura y de _salud_daemons en la misma pasada: se deja la primera."""
    visto = False
    out = []
    for a in alertas:
        if (a[0] if isinstance(a, tuple) else str(a)) == "red_sin_dns":
            if visto:
                continue
            visto = True
        out.append(a)
    return out


def _check_gateway_vivo():
    """Vigila que la PUERTA de Polaris (gateway del borde) esté viva. Si el puerto no responde →
    kickstart UNA vez (autofix acotado, reversible); si sigue caída → aviso en llano. Esto cierra el
    hueco de que un fallo de la puerta (la que sirve Vivir) tenga que notarlo {{TITULAR}}. Devuelve
    (alertas, info). Cadena de alerta ESTABLE para el anti-flapping de _emitir_si_cambia."""
    alertas, info = [], {}
    vivo = _puerto_vivo(GW_HOST, GW_PORT)
    info["gateway"] = {"ok": vivo}
    if vivo:
        return alertas, info
    kicked = _kickstart_daemon(KEEPALIVE_DAEMONS["borde-gateway"])
    if kicked:
        time.sleep(KICKSTART_WAIT)
        vivo2 = _puerto_vivo(GW_HOST, GW_PORT)
        info["gateway_tras_kickstart"] = {"ok": vivo2}
        if vivo2:
            return alertas, info                      # revivió sola → registrado, sin alertar
    alertas.append(
        "La puerta de Polaris (Vivir) está caída y no revivió sola. "
        "Revísalo: launchctl kickstart -k gui/$(id -u)/com.btp.borde-gateway")
    return alertas, info


def _check_servicios_vivos():
    """Comprueba que el Observatorio y su camino móvil responden de verdad, no solo que el
    proceso exista. Si algo falla, intenta autofix (kickstart) UNA vez antes de avisar.

    Lógica de distinción VPN vs daemon caído:
      - Si el local (8787) responde pero el móvil (tailscale serve 9090) no → Tailscale caído o
        NordVPN bloqueándolo. Aviso en llano; NO hay kickstart (no hay daemon nuestro en medio).
      - Si el local (8787) no responde → daemon del Observatorio caído. Se intenta
        kickstart; si luego el móvil tampoco → aviso separado.

    Devuelve (alertas_list, info_dict). Cadenas de alerta ESTABLES (sin números volátiles)
    para que el anti-flapping de _emitir_si_cambia funcione correctamente."""
    alertas, info = [], {}

    local_ok, local_det = _http_ok(OBS_LOCAL_URL)
    relay_ok, relay_det = _http_ok(OBS_MOVIL_URL)
    info["obs_local"]  = {"ok": local_ok, "detalle": str(local_det)}
    info["obs_relay"]  = {"ok": relay_ok, "detalle": str(relay_det)}

    if local_ok and relay_ok:
        # Todo correcto: no hay alertas.
        return alertas, info

    if local_ok and not relay_ok:
        # El servidor local responde y el camino por Tailscale no. Sin daemon nuestro en medio
        # no hay nada que reiniciar: o Tailscale está caído, o NordVPN lo bloquea.
        alertas.append(
            "La web de síntomas no se alcanza desde fuera de casa; "
            "el servidor local sí responde, así que parece que Tailscale está caído o NordVPN lo bloquea. "
            "Pausa NordVPN un momento y vuelve a intentarlo."
        )
        return alertas, info

    if not local_ok:
        # El Observatorio local no responde → daemon caído. Kickstart.
        kicked = _kickstart_daemon(KEEPALIVE_DAEMONS["observatorio"])
        if kicked:
            time.sleep(KICKSTART_WAIT)
            local_ok2, _ = _http_ok(OBS_LOCAL_URL)
            info["obs_local_tras_kickstart"] = {"ok": local_ok2}
            if local_ok2:
                info["obs_local_autofix"] = True
                # Tras revivir el local, intentar también el relay
                relay_ok2, _ = _http_ok(OBS_MOVIL_URL)
                info["obs_relay_tras_local_fix"] = {"ok": relay_ok2}
                if not relay_ok2:
                    alertas.append(
                        "La web de síntomas estaba caída; la he revivido, "
                        "pero el acceso desde el móvil todavía no llega. "
                        "Es posible que NordVPN esté bloqueando Tailscale."
                    )
                return alertas, info
        # Kickstart no funcionó (o no era posible): avisar
        alertas.append(
            "La web de síntomas (el Observatorio) no responde. "
            "Intenté reiniciarla automáticamente pero sigue sin contestar. "
            "Puede que necesite que abras Terminal y ejecutes: "
            "launchctl kickstart -k gui/$(id -u)/com.btp.observatorio"
        )
        return alertas, info

    return alertas, info


def _ensure():
    os.makedirs(HC, mode=0o700, exist_ok=True)


def _write_atomic(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def mark_seen(fuente="titular", cuando=None):
    """Señal de presencia de {{TITULAR}} (acto humano). Refresca last_seen y, si procede,
    levanta el modo degradado. NUNCA la llama el propio lazo por su actividad.

    `cuando` (datetime local) = hora real del acto, si no es ahora (p. ej. un prompt de Claude
    Code verificado 30 min después). `ts`/`fuente` de primer nivel = la señal MÁS RECIENTE de
    cualquier fuente (lo que lee _dias_sin_senal); `fuentes` guarda la última de cada una."""
    _ensure()
    ts = (cuando or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        prev = json.load(open(LAST_SEEN, encoding="utf-8"))
        if not isinstance(prev, dict):
            prev = {}
    except Exception:
        prev = {}
    fuentes = prev.get("fuentes") if isinstance(prev.get("fuentes"), dict) else {}
    if isinstance(prev.get("ts"), str) and isinstance(prev.get("fuente"), str):
        fuentes.setdefault(prev["fuente"], prev["ts"])   # last_seen de antes de haber historial
    fuentes[fuente] = max(ts, fuentes.get(fuente, ""))
    top_fuente, top_ts = max(fuentes.items(), key=lambda kv: kv[1])
    _write_atomic(LAST_SEEN, {"ts": top_ts, "fuente": top_fuente, "fuentes": fuentes})
    if os.path.exists(DEGRADED):
        try:
            os.remove(DEGRADED)
        except OSError:
            pass


def _dias_sin_senal():
    try:
        ts = json.load(open(LAST_SEEN, encoding="utf-8")).get("ts")
        d = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        return (datetime.now() - d).total_seconds() / 86400.0
    except Exception:
        return None   # sin señal previa: no disparamos el dead-man hasta que haya una


# ── PRESENCIA POR CLAUDE CODE (11-sep-26) ──────────────────────────────────────────────────────
# El hook .claude/hooks/presencia_cc.py apunta cada UserPromptSubmit en esta cola; aquí se decide.
# El entorno del proceso no prueba nada (un `claude -p` hijo de una sesión de escritorio hereda
# CLAUDE_CODE_ENTRYPOINT=claude-desktop). La prueba está en el transcript: el harness guarda cada
# prompt con `origin`. Censo del 11-sep-26 sobre 574 transcripts: tecleado → {"kind":"human"};
# aviso de subagente → task-notification; mensaje entre sesiones → peer; `claude -p` → sin origin.
# OJO, verificado con sonda: una TAREA PROGRAMADA del escritorio también sale {"kind":"human"};
# solo la delata el texto, que empieza por <scheduled-task …>. Por eso se miran las dos cosas.
PRESENCIA_PEND = os.path.join(HC, "presencia_pendiente.jsonl")
PROYECTOS_CC = os.path.expanduser("~/.claude/projects")
PRESENCIA_ENTRYPOINTS = ("claude-desktop", "cli")
PRESENCIA_AUTOMATICOS = ("<task-notification", "<scheduled-task", "<ci-monitor-event")
PRESENCIA_VENTANA_H = 24   # más viejo que esto no se cuenta ni se sigue esperando


def _texto_prompt(entrada):
    """Texto del mensaje de usuario, sin los <system-reminder> que el harness antepone."""
    import re
    c = (entrada.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None
    return re.sub(r"^(\s*<system-reminder>.*?</system-reminder>)+", "", c, flags=re.S).lstrip()


def _veredicto_presencia(entrada, ahora):
    """(True, datetime_local) si la entrada del transcript es un prompt TECLEADO por una persona;
    si no, (False, motivo). Fail-closed: cualquier campo raro o ausente = no."""
    if entrada.get("type") != "user":
        return False, "no_es_user"
    if entrada.get("origin") != {"kind": "human"}:
        return False, "origin_no_humano"
    if entrada.get("isSidechain") is not False:
        return False, "sidechain"
    if entrada.get("entrypoint") not in PRESENCIA_ENTRYPOINTS:
        return False, "entrypoint"
    texto = _texto_prompt(entrada)
    if texto is None:
        return False, "sin_texto"
    if texto.startswith(PRESENCIA_AUTOMATICOS):
        return False, "automatismo"
    try:   # el transcript va en UTC ("…Z"); last_seen va en hora local sin zona
        local = datetime.fromisoformat(entrada["timestamp"].replace("Z", "+00:00"))
        local = local.astimezone().replace(tzinfo=None)
    except Exception:
        return False, "sin_timestamp"
    if not (-timedelta(minutes=5) <= ahora - local <= timedelta(hours=PRESENCIA_VENTANA_H)):
        return False, "fuera_de_ventana"
    return True, local


def _buscar_entrada(transcript, prompt_id):
    """Entrada `type=user` con ese promptId, o None. Busca la cadena antes de parsear."""
    with open(transcript, encoding="utf-8") as f:
        for linea in f:
            if prompt_id not in linea:
                continue
            try:
                d = json.loads(linea)
            except Exception:
                continue
            if d.get("type") == "user" and d.get("promptId") == prompt_id:
                return d
    return None


def _procesar_presencia_cc(ahora=None):
    """Vacía la cola del hook presencia_cc y llama a mark_seen("claude-code") con el prompt humano
    más reciente que pase el veredicto. Lo no encontrado todavía se reintenta hasta 24 h.
    Nunca guarda ni devuelve el texto de un prompt: solo cuentas y motivos."""
    ahora = ahora or datetime.now()
    info = {"marcadas": 0, "rechazos": {}, "reintentos": 0}
    if not os.path.exists(PRESENCIA_PEND):
        return info
    trabajo = PRESENCIA_PEND + ".procesando"
    os.replace(PRESENCIA_PEND, trabajo)   # el hook sigue apuntando en un fichero nuevo
    raiz = os.path.realpath(PROYECTOS_CC) + os.sep
    quedan, mejor = [], None
    try:
        lineas = open(trabajo, encoding="utf-8").read().splitlines()
    except Exception:
        lineas = []
    for linea in lineas:
        try:
            p = json.loads(linea)
            pid, tr, ts = p["prompt_id"], p["transcript_path"], float(p["ts"])
        except Exception:
            info["rechazos"]["cola_ilegible"] = info["rechazos"].get("cola_ilegible", 0) + 1
            continue
        motivo = None
        real = os.path.realpath(tr) if isinstance(tr, str) else ""
        # Solo <proyecto>/<sesión>.jsonl: ni memory/ (el lazo escribe ahí) ni subcarpetas.
        if not (real.startswith(raiz) and real.endswith(".jsonl")
                and len(real[len(raiz):].split(os.sep)) == 2):
            motivo = "transcript_fuera_de_proyectos"
        else:
            try:
                entrada = _buscar_entrada(real, pid) if os.path.isfile(real) else None
            except Exception:
                entrada, motivo = None, "transcript_ilegible"
            if motivo is None and entrada is None:
                if (ahora - datetime.fromtimestamp(ts)) < timedelta(hours=PRESENCIA_VENTANA_H):
                    quedan.append(linea)
                    info["reintentos"] += 1
                    continue
                motivo = "no_aparece"
            elif motivo is None:
                ok, res = _veredicto_presencia(entrada, ahora)
                if ok:
                    info["marcadas"] += 1
                    mejor = res if mejor is None or res > mejor else mejor
                    continue
                motivo = res
        info["rechazos"][motivo] = info["rechazos"].get(motivo, 0) + 1
    if quedan:
        with open(PRESENCIA_PEND, "a", encoding="utf-8") as f:
            f.write("\n".join(quedan) + "\n")
    try:
        os.remove(trabajo)
    except OSError:
        pass
    if mejor is not None:
        mark_seen("claude-code", cuando=mejor)
    return info


# Módulos de tools/ que al importarse BORRAN tools/ del sys.path del proceso (correo_smtp, drive,
# postdicom, wa_tracker). Importados a pelo desde run(), todo import perezoso posterior fallaba:
# en vivo el 14-sep-26, frescura_error «No module named 'seguimiento'» y ciclo_agentes_error.
# 20-sep-2026: eran cinco. `correo_smtp`, `drive` y `wa_tracker` dejaron de purgar (la sombra que
# lo justificaba, `tools/queue.py`, se renombró el 11-jul y ya no existe). Quedan los dos que solo
# se ejecutan como proceso propio, donde la purga no afecta a ningún llamador: `postdicom` (CLI) y
# `visor3d` (se lanza con su venv desde lector_clinico.py). El helper de abajo se queda como red.
_BORRAN_TOOLS_DEL_PATH = ("postdicom", "visor3d")


def _importar_sin_tocar_path(nombre):
    """import de un hermano de tools/ que deja sys.path exactamente como estaba."""
    import importlib
    antes = list(sys.path)
    try:
        return importlib.import_module(nombre)
    finally:
        sys.path[:] = antes


def _aparcado(nombre):
    """¿Ese proveedor está apagado A PROPÓSITO? (campo `aparcado` de enruta.PROVEEDORES)

    20-sep-26: `llm_sin_saldo:glm` llevaba 5 detecciones e `intermitente` —terminal— con la
    batería roja, y no había forma de cerrarlo: el prepago de GLM está a cero por DECISIÓN del
    19-sep (su relevo es perplexity/glm-5.3), así que la condición es cierta en cada vuelta y
    cerrar la deuda solo la reabría como regresión. Una decisión no es una avería: no se avisa.
    Fail-soft: si no se puede leer el catálogo, se avisa como siempre."""
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import enruta as _enruta
        return (_enruta.PROVEEDORES.get(nombre) or {}).get("aparcado")
    except Exception:
        return None


def _ciclo_agentes():
    """Apunta el final de los subagentes (tools/ciclo_agentes.py, 14-sep-26). El hook traza_subagente
    solo ve el lanzamiento; sin esta pasada el registro acumulaba lanzamientos y ningún motivo de
    salida. No avisa a nadie: deja cifras en last_check. En la batería no toca ~/.claude/projects
    real salvo que el test fije BTP_PROJECTS_DIR."""
    if os.environ.get("BTP_TEST_BATTERY") and not os.environ.get("BTP_PROJECTS_DIR"):
        return {"omitido": "bateria"}
    import ciclo_agentes
    resueltos = ciclo_agentes.cerrar(dias=7)
    por_estado = {}
    for st in resueltos.values():
        por_estado[st] = por_estado.get(st, 0) + 1
    # Dónde se atascan (destilado de {{CONTACTO}}, 20-sep-26): «entender el encargo / ejecutarlo /
    # retomar» son tres fallos con tres arreglos distintos. Sin esto, «5 agentes fallaron»
    # no dice qué arreglar. Fail-soft: si no se puede clasificar, no se inventa.
    try:
        atascos = ciclo_agentes.atascos(dias=7)
    except Exception:
        atascos = None
    salida = {"resueltos": len(resueltos), "por_estado": por_estado,
              "huerfanos_24h": len(ciclo_agentes.huerfanos(horas=24))}
    if atascos:
        salida["atascos"] = atascos
    return salida


ALERTA_COOLDOWN_H = 12   # ventana de recordatorio: un problema persistente se re-avisa como mucho cada 12h
# Dead-man del LIBRO DE DEUDA (tools/deuda.py, 25-jul-26): si nadie registra un hallazgo en este
# plazo, no es que no haya fallos — es que los detectores han dejado de anotar (así murieron los
# intentos anteriores de arreglar esto: en silencio).
DEUDA_LATIDO_DIAS = 8


ACUSE_VENTANA_H = ALERTA_COOLDOWN_H   # un acuse 'en_arreglo' deja de silenciar tras esta ventana sin
                                       # resolverse (misma ventana que el recordatorio normal — no
                                       # se inventa un plazo nuevo: si algo lleva "en ello" 12h sin
                                       # cerrarse, vuelve a escalar como si nadie lo hubiera visto).

def _emitir_si_cambia(alertas, categoria="humano"):
    """Envía el aviso de salud por el choke-point SOLO si hay un problema NUEVO o si ya pasó la
    ventana de recordatorio (ALERTA_COOLDOWN_H). Un problema persistente avisa UNA vez y se recuerda
    cada ~12h. Cuando el conjunto de fallos cambia (nuevo o resuelto), avisa de nuevo.

    `alertas` puede ser:
      · lista de str  — el string es a la vez clave de dedup Y texto visible. Solo usar si el
                        mensaje NO lleva números volátiles (contadores, edades, GBs...).
      · lista de (clave_estable, texto_visible)  — PREFERIDO: la clave es estable (qué condición
                        falla, sin números), el texto puede llevar el detalle con números para {{TITULAR}}.

    `categoria`: "humano" (defecto) → entrega al chat de {{TITULAR}} (fail-safe).
                 "operativo" → va al log de fontanería, no al chat.
    REGLA DE ORO: si el mensaje requiere acción de {{TITULAR}}, usar "humano". Ante la duda → "humano".

    ACUSE (3/7/26, tools/salud.py): una clave con acuse 'en_arreglo' FRESCO (< ACUSE_VENTANA_H) no
    grita el texto normal — se sustituye por "✋ Visto — en ello (<por>, hace <N>): <nota>" y no
    re-nag mientras el acuse siga fresco. Si el acuse ENVEJECE sin resolverse, vuelve a escalar
    (deja de tratarse como acusado). Cuando una clave que SÍ se llegó a mostrar a {{TITULAR}} desaparece
    del conjunto, se manda "✅ Resuelto: <label>" una vez y se purga su acuse — las 'operativo' que
    nunca se le mostraron no generan cierre (no la interrumpen por algo que no vio). El autofix
    (_autofix_daemon_por_heartbeat) marca su propio acuse aparte.

    ACUSE AUTOMÁTICO EN EL PROPIO AVISO (3/7/26 — pide {{TITULAR}}: que el aviso YA lleve el "me pongo",
    sin depender de que una sesión llame a `salud ack` a mano): para cada clave NUEVA (recién
    aparecida, sin ningún acuse previo) de categoría "humano", ANTES de decidir la voz:
      · si YA se intentó un autofix acotado para ella (el acuse quedó marcado por="autofix" — lo deja
        _marcar_acuse_autofix, llamado por el chequeo que corresponda ANTES de llegar aquí, p.ej.
        _check_roster_daemons con el kickstart de daemon_fallando) → la voz es
        "🔧 Detecté <X> y me pongo a arreglarlo (reinicio automático). Te confirmo si no se cierra."
      · si NO hay autofix (nadie marcó el acuse todavía) → se registra un acuse `en_arreglo` genérico
        (nota "investigando", por="healthcheck") y la voz es "🔧 Detecté <X>, lo estoy mirando."
        (La investigación de fondo es a día de hoy un TODO acotado — lo que este acuse garantiza es
        que el PRIMER mensaje que le llega a {{TITULAR}} ya dice que alguien se puso, no un grito seco.)
    Esto NO sustituye el acuse manual (`salud ack`): si una sesión humana ya acusó la clave con su
    propia nota, esa nota manda (no se pisa). Solo actúa cuando NO había ACUSE NINGUNO todavía.

    El cierre respeta el MISMO anti-flapping que ya protege las alertas nuevas: una clave que
    desaparece de golpe queda "candidata a resuelta" pero solo se ANUNCIA cuando sigue ausente en
    la SIGUIENTE llamada donde de verdad hay algo que persistir (evita anunciar "✅ Resuelto" por un
    parpadeo de un ciclo que luego vuelve — igual que una alerta nueva que oscila no re-spamea).

    Clave anti-FLAPPING: el estado se guarda SOLO al ENVIAR, así el conjunto de referencia es el
    último AVISADO. Un agente que oscila dentro/fuera de ese conjunto NO vuelve a disparar el digest.
    Devuelve True si envió. Determinista y testeable.

    (Bug 24/6: firmaba el texto completo → números volátiles cambiaban la firma cada ciclo → spam.
     Bug 26/6: mismo problema con dead-man días + dispatcher minutos + disco GB + seguimiento días.)"""
    import json as _json


    try:
        import salud as _salud
    except Exception:
        _salud = None

    # Antes de decidir la voz: soltar los acuses que prometen un encargo MUERTO (31-jul-26). Si no,
    # una alerta cuyo job de investigación murió se queda diciendo «lo estoy mirando» para siempre y
    # no vuelve a llamar nunca. Se encontraron 5 así, uno con 89 horas — el mismo fallo que el acuse
    # vino a arreglar, una capa más arriba. Fail-soft: esto jamás puede tumbar el aviso de salud.
    if _salud:
        try:
            _salud.reconciliar_acuses()
        except Exception:
            pass

    # UNA CADENA NO ES UNA LISTA DE ALERTAS (19-sep-2026). Si alguien llama con un str suelto,
    # el `for` de abajo lo recorre LETRA A LETRA: cada carácter se vuelve una clave de alerta y
    # el sistema encola un diagnóstico por cada una. En `queue/failed/` había encargos reales
    # pidiendo investigar la alerta «a» y la alerta «c». Se normaliza aquí, en la puerta.
    if isinstance(alertas, (str, bytes)):
        alertas = [alertas if isinstance(alertas, str) else alertas.decode("utf-8", "replace")]

    # Normalizar: extraer claves (para dedup) y textos (para el mensaje)
    claves, textos_por_clave = [], {}
    for a in alertas:
        if isinstance(a, tuple) and len(a) == 2:
            clave, texto = str(a[0]), str(a[1])
        else:
            clave, texto = str(a), str(a)
        claves.append(clave)
        textos_por_clave[clave] = texto

    # Fichero de estado separado por categoría: los grupos operativo y humano no se pisan.
    state_path = os.path.join(HC, "last_alert_state-%s.json" % categoria)
    try:
        prev = _json.load(open(state_path, encoding="utf-8"))
    except Exception:
        prev = {}
    # Compatibilidad con el formato viejo (clave "alertas") → migración transparente
    prev_claves = set(prev.get("claves", prev.get("alertas", [])))
    prev_textos = prev.get("textos", {}) if isinstance(prev.get("textos"), dict) else {}

    # Tracking de "pendientes de cierre" EN SU PROPIO fichero, actualizado en TODA llamada (no solo
    # cuando se envía voz) — así el reloj de "¿lleva 2 lecturas ausente?" no depende del cooldown de
    # ALERTA_COOLDOWN_H, que es un reloj DISTINTO (cuándo se avisa, no cuándo se confirma un cierre).
    pend_path = os.path.join(HC, "pendientes_resueltas-%s.json" % categoria)
    try:
        prev_pendientes = set(_json.load(open(pend_path, encoding="utf-8")).get("claves", []))
    except Exception:
        prev_pendientes = set()

    ausentes_ahora = prev_claves - set(claves)
    # CONFIRMADAS: llevaban ausentes desde la llamada anterior Y siguen ausentes ahora (2 lecturas
    # seguidas sin volver) → se anuncian. Las que acaban de desaparecer este ciclo se guardan como
    # "pendientes" y se confirman (o se desmienten, si vuelven) en la SIGUIENTE llamada.
    cerradas = sorted(prev_pendientes & ausentes_ahora)
    try:
        if ausentes_ahora:
            _write_atomic(pend_path, {"claves": sorted(ausentes_ahora)})
        elif os.path.exists(pend_path):
            os.remove(pend_path)
    except Exception:
        pass
    if cerradas and categoria == "humano":
        etiquetas = [prev_textos.get(c, c) for c in cerradas]
        salida.report_to_titular("✅ Resuelto: " + "; ".join(etiquetas),
                                categoria=categoria, fuente="healthcheck")

    # La PURGA va fuera del `== "humano"`, por el mismo motivo que la remisión de deuda de abajo:
    # ese filtro decide si se manda un Telegram, no si la condición dejó de cumplirse. Atada al
    # mensaje, una alerta `operativo` resuelta no se cerraba NUNCA y se quedaba en el libro para
    # siempre. Medido el 13-sep-26: 9 alertas `daemon_fallando:*` llevaban 26 h en `en_arreglo`
    # con los nueve daemons en exit=0 y healthcheck diciendo «ok» de ellos en el mismo ciclo.
    # Una lista de alertas que ya no son ciertas es ruido, y el ruido enseña a no mirarla.
    if cerradas and _salud:
        for c in cerradas:
            try:
                _salud.purgar(c)
            except Exception:
                pass

    # R4 (29-jul-26): la condición se FUE → el hallazgo deja de gritar en el libro, sin cerrarse.
    # `cerradas` ya exige dos lecturas seguidas sin la condición, así que el anti-rebote sale gratis
    # y no hay que inventar otro umbral. Va fuera del `== "humano"` de arriba a propósito: ese filtra
    # el mensaje de Telegram, no el hecho de que la condición dejó de cumplirse. Fail-soft como la
    # escritura de más abajo: esto NO puede romper el aviso de salud.
    if cerradas:
        try:
            import deuda as _deuda
            for c in cerradas:
                _deuda.remitir(c, "healthcheck: la condición dejó de detectarse en 2 lecturas")
        except Exception:
            pass

    if not claves:
        # Solo se resetea state_path del todo cuando YA NO quedan ausencias pendientes de confirmar
        # (si algo desapareció ESTE mismo ciclo, hay que conservar la referencia un ciclo más para
        # poder anunciar su "✅ Resuelto" la próxima vez que se compruebe y siga sin volver).
        if not (ausentes_ahora - set(cerradas)):
            try:
                os.remove(state_path)
            except (FileNotFoundError, OSError):
                pass
        return bool(cerradas and categoria == "humano")

    now = time.time()
    nuevas = set(claves) - prev_claves         # condiciones que NO estaban en el último enviado
    elapsed_h = (now - prev.get("ts", 0)) / 3600.0

    # LIBRO DE DEUDA (25-jul-26, plan «que quede arreglado»): cada condición que se detecta cae SOLA
    # en `tools/deuda.py`, y si se repite ESCALA hasta poner `test_all.sh` en ROJO. Aquí muere el
    # «reconfirmación 14ª»: el bug Task/Agent del muro se re-reportó 14 veces en 8 días por el log y
    # nadie lo cerró. Detectar sin cerrar deja de ser gratis. Fail-soft: si algo falla, healthcheck
    # sigue avisando como siempre (esto NO puede romper el aviso de salud).
    # `deuda_escalada` NO entra en el libro: es el propio libro reportándose a sí mismo. Llegó a 68x
    # alimentándose sola — mientras hubiera algo escalado, la condición «hay algo escalado» se cumplía
    # y generaba otra detección. Un detector no puede anotar «hay deuda» como deuda.
    #
    # SOLO `nuevas`, NUNCA `claves` (11-sep-26, hallazgo escalado en triaje): `visto()` significa
    # «alguien lo detectó DE NUEVO» (R2, docstring de deuda.py), no «sigue pasando ESTE ciclo». Con
    # `claves` (el conjunto CRUDO de esta pasada) una condición persistente e ininterrumpida —el
    # código rojo del 6-ago que paró el lazo 27 días, o el propio dead-man mientras {{TITULAR}} no señala—
    # llamaba a `visto()` una vez por CADA pasada de healthcheck (cada 30 min) mientras durase, así
    # que un ÚNICO incidente inflaba el contador cientos de veces (dead_man_ausencia llegó a 1241x,
    # frescura_agente_parado:auto-mejora a 747x) y cruzaba en horas tanto el umbral de escalada (3x)
    # como el de REMISIONES_MAX (3): el hallazgo quedaba 'intermitente' PARA SIEMPRE aunque la
    # condición llevara días resuelta, porque `intermitente` ya no se puede volver a callar (R4) —
    # solo cerrar con test. `nuevas` (ya calculado arriba: claves - prev_claves, el último conjunto
    # REALMENTE avisado) es exactamente «esta condición no estaba en la última vez que se avisó»:
    # una incidencia continua solo entra aquí en el ciclo en que aparece; ciclos posteriores mientras
    # sigue activa NO vuelven a tocar el libro. Si desaparece y reaparece (repetición real), vuelve a
    # estar en `nuevas` y SÍ escala — eso es justo lo que R2 quiere medir.
    NO_AL_LIBRO = {"deuda_escalada"}
    try:
        import deuda as _deuda
        for clave in nuevas:
            if clave in NO_AL_LIBRO:
                continue
            it = _deuda._cargar().get(_deuda.normalizar_clave(clave))
            if it is None or it.get("estado") == "cerrado":
                _deuda.abrir(clave, str(textos_por_clave.get(clave, clave))[:200],
                             ned="medio", dueno="healthcheck")
            else:
                _deuda.visto(clave)
    except Exception:
        pass

    # ACUSE AUTOMÁTICO EN EL PROPIO AVISO (3/7/26): para cada clave NUEVA de "humano" que todavía no
    # tiene NINGÚN acuse (ni manual ni de un autofix ya corrido antes de llegar aquí), el propio aviso
    # autónomo deja constancia de que "alguien se puso" — sin depender de que una sesión llame a
    # `salud ack` a mano. Si ya había acuse (autofix o manual), se respeta tal cual (no se pisa).
    #
    # …y desde el 25-jul-26 el acuse NO es solo una frase: encola la investigación DE VERDAD
    # (`_encolar_investigacion`). El «lo estoy mirando» sin nadie mirando es peor que el grito seco,
    # porque {{TITULAR}} lee que alguien se puso y deja de vigilarlo: en `acuses.json` había claves
    # en_arreglo desde hacía 8 h y 32 h con cero trabajo detrás. Si el encolado no sale (HALT, cola
    # ilegible), la nota lo dice en llano en vez de prometer lo que no va a pasar.
    if categoria == "humano" and _salud:
        for clave in nuevas:
            if _salud.get(clave) is None:
                job_id = _encolar_investigacion(clave, textos_por_clave.get(clave, clave))
                nota = ("investigando (job %s encolado)" % job_id) if job_id else \
                       "anotado; sin ejecutor ahora mismo, lo retomo en cuanto el lazo vuelva"
                try:
                    _salud.ack(clave, nota, por="healthcheck")
                except Exception:
                    pass

    # ACUSE: separa las claves con 'en_arreglo' FRESCO (silenciadas, voz de "visto") del resto
    # (voz normal). Solo mira acuses para 'humano' — lo operativo no tiene dueño humano que acusar.
    # Dentro de "fresco" hay DOS voces distintas según quién dejó el acuse: "autofix" (ya se intentó
    # un arreglo automático, kickstart u otro) habla de "me pongo a arreglarlo"; cualquier otro acuse
    # SIN autofix explícito (el genérico "healthcheck" de arriba, o un `salud ack` manual/otro sistema)
    # habla de "lo estoy mirando"/repite la nota tal cual la dejó quien acusó.
    textos_finales = []
    hay_sin_acusar_nuevas_o_vencidas = False
    for clave in claves:
        acuse = _salud.get(clave) if _salud else None
        fresco = False
        if acuse and acuse.get("estado") == "en_arreglo":
            edad_h = (now - float(acuse.get("visto_ts", 0))) / 3600.0
            fresco = edad_h < ACUSE_VENTANA_H
        if categoria == "humano" and fresco:
            edad_min = int((now - float(acuse.get("visto_ts", 0))) / 60.0)
            cuando = ("%dm" % edad_min) if edad_min < 60 else ("%dh" % (edad_min // 60))
            if acuse.get("por") == "autofix":
                textos_finales.append("🔧 Detecté %s y me pongo a arreglarlo (reinicio automático). "
                                       "Te confirmo si no se cierra." % textos_por_clave[clave])
            elif (clave in nuevas and acuse.get("por") == "healthcheck"
                  and (acuse.get("nota") or "").startswith("investigando")):
                # `startswith`, no igualdad: desde el 25-jul la nota lleva pegado el id del job de
                # investigación que se encoló («investigando (job 3f2a… encolado)»), y ese id es lo
                # que hace comprobable la promesa. La voz sigue siendo la misma para {{TITULAR}}.
                textos_finales.append("🔧 Detecté %s, lo estoy mirando." % textos_por_clave[clave])
            elif (clave in nuevas and acuse.get("por") == "healthcheck"
                  and (acuse.get("nota") or "").startswith("anotado")):
                # No se pudo encolar (HALT, cola ilegible). Aquí NO se dice «lo estoy mirando»: sería
                # exactamente la promesa vacía que este arreglo vino a quitar. Se dice la verdad.
                textos_finales.append("🔧 Detecté %s. Ahora mismo no tengo ejecutor (el lazo está "
                                      "parado), así que queda anotado y lo retomo en cuanto vuelva."
                                      % textos_por_clave[clave])
            else:
                textos_finales.append("✋ Visto — en ello (%s, hace %s): %s" % (
                    acuse.get("por", "?"), cuando, acuse.get("nota") or textos_por_clave[clave]))
            if clave in nuevas:
                hay_sin_acusar_nuevas_o_vencidas = True   # una alerta NUEVA siempre merece una entrega, aunque venga ya acusada
        else:
            textos_finales.append(textos_por_clave[clave])
            if clave in nuevas:
                hay_sin_acusar_nuevas_o_vencidas = True

    if not hay_sin_acusar_nuevas_o_vencidas and elapsed_h < ALERTA_COOLDOWN_H and not cerradas:
        return False                           # nada nuevo y dentro de la ventana → silencio (anti-flapping)
    if not textos_finales:
        return bool(cerradas and categoria == "humano")
    salida.report_to_titular("🩺 Revisión de salud del lazo:\n- " + "\n- ".join(textos_finales),
                            categoria=categoria, fuente="healthcheck")
    try:                                       # guarda SOLO al enviar: referencia = último avisado
        with open(state_path, "w", encoding="utf-8") as f:
            _json.dump({"ts": now, "claves": sorted(claves), "textos": textos_por_clave},
                       f, ensure_ascii=False)
    except Exception:
        pass
    return True


IA_PROBE_INTERVAL_H = 2  # no martillear los endpoints: sondea las IAs cada ~2h, no cada 30min


def _check_ias():
    """Sonda de salud de las IAs externas (ia_health.probe). Avisa SOLO si una IA CAE (DOWN);
    el bot-wall de las de evidencia es DEGRADED = esperado, NO alerta. Throttle ~2h para no
    martillear endpoints. Clave estable por IA → respeta el anti-flapping. Fail-soft."""
    import json as _json
    state_p = os.path.join(HC, "ia_health.json")
    prev = {}
    try:
        prev = _json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pass
    if (time.time() - prev.get("ts", 0)) / 3600.0 < IA_PROBE_INTERVAL_H:
        return [], {"throttled": True, "caidas": prev.get("caidas", []), "degradadas": prev.get("degradadas", [])}
    try:
        from ia_health import probe as _ia_probe
        res = _ia_probe()
    except Exception as e:
        return [], {"error": "%r" % e}
    alertas = []
    for x in res.get("ias", []):
        if x.get("code") == "DOWN":
            crit = " (CRÍTICA — clínico)" if str(x.get("carril", "")).startswith("🔴") else ""
            clave = "ia_caida_" + x["ia"].lower().replace(" ", "").replace(".", "")
            alertas.append((clave, "La IA %s no responde%s: %s." % (x["ia"], crit, x.get("detalle", ""))))
    try:
        with open(state_p, "w", encoding="utf-8") as f:
            _json.dump({"ts": time.time(), "caidas": res.get("caidas", []),
                        "degradadas": res.get("degradadas", [])}, f, ensure_ascii=False)
    except Exception:
        pass
    return alertas, {"caidas": res.get("caidas", []), "degradadas": res.get("degradadas", [])}


DRIVE_PROBE_INTERVAL_H = 6
DRIVE_MARCA_CADUCADO = "OAuth de usuario (escritura): CADUCADO"


LLM_PROBE_INTERVAL_H = 6
# Lo que dicen los proveedores cuando lo que falta es DINERO, no servicio. Se separa del resto
# porque la acción es distinta: un caído se espera, un sin-saldo hay que recargarlo.
# OJO con el nombre: `_SIN_SALDO` (una cadena) ya existe más arriba, para el log de Anthropic.
# Definir aquí otro con el mismo nombre lo PISABA y `_check_saldo_api` reventaba con
# «'in <string>' requires string as left operand, not re.Pattern». Lo cazó test_saldo_api.
_SIN_SALDO_LLM = re.compile(r"insufficient\s+balance|no resource package|credit balance is too low|"
                        r"quota|billing|recharge|payment required|exceeded your current quota|"
                        r"saldo", re.I)


def _check_llms(salud=None, ahora=None):
    """¿Responde cada LLM del enrutador, y a cuál se le acabó el saldo? (19-sep-2026)

    El agujero que tapa: `_check_ias` sonda los endpoints con un GET, y un GET responde 200 con
    el saldo a CERO —lo dice el propio comentario de `ia_health.refrescar_credito_claude`—. Por
    eso se montó una sonda de crédito REAL… solo para Claude. Del resto nadie sabía nada: GLM
    llevaba el 19-sep devolviendo «Insufficient balance» en cada llamada del enrutador y ninguna
    alerta saltó, porque el único que hace la llamada de verdad es `enruta.salud()`, y a ese
    nadie le preguntaba.

    Dos claves distintas a propósito, porque la acción no es la misma:
      · `llm_sin_saldo:<proveedor>` — hay que recargar. Es de {{TITULAR}}, y por eso es «humano».
      · `llm_caido:<proveedor>`     — no responde. Se espera, se reintenta, y degrada solo.
    """
    ahora = ahora or time.time()
    estado_p = os.path.join(HC, "llms.json")
    prev = {}
    try:
        with open(estado_p, encoding="utf-8") as fh:
            prev = json.load(fh)
    except Exception:
        pass
    if (ahora - prev.get("ts", 0)) / 3600.0 < LLM_PROBE_INTERVAL_H:
        return [], {"throttled": True, "sin_saldo": prev.get("sin_saldo", []),
                    "caidos": prev.get("caidos", [])}
    if salud is None:
        try:
            sys.path.insert(0, os.path.join(REPO, "tools"))
            import enruta as _enruta
            salud = _enruta.salud(refrescar=True)
        except Exception as e:  # noqa: BLE001
            return [], {"error": "%r" % e}

    alertas, sin_saldo, caidos, aparcados = [], [], [], []
    for nombre, dato in sorted((salud or {}).items()):
        if (dato or {}).get("ok"):
            continue
        if _aparcado(nombre):
            aparcados.append(nombre)   # apagado a propósito: su silencio no es una avería
            continue
        detalle = str((dato or {}).get("detalle") or "")[:160]
        if _SIN_SALDO_LLM.search(detalle):
            sin_saldo.append(nombre)
            alertas.append(("llm_sin_saldo:%s" % nombre,
                            "💳 %s se quedó SIN SALDO: hay que recargar para volver a usarlo. (%s)"
                            % (nombre, detalle)))
        else:
            caidos.append(nombre)
            # DOS PASADAS PARA GRITAR (19-sep-2026): la primera medición real dio `grok` caído y
            # la sonda directa, medio minuto después, decía OK — un timeout de red. Un aviso por
            # cada hipo enseña a ignorar los avisos. Sin saldo NO espera: eso no se arregla solo.
            if nombre in (prev.get("caidos") or []):
                alertas.append(("llm_caido:%s" % nombre,
                                "🔌 %s no responde (dos pasadas seguidas): %s" % (nombre, detalle)))
    try:
        os.makedirs(HC, exist_ok=True)
        with open(estado_p, "w", encoding="utf-8") as fh:
            json.dump({"ts": ahora, "sin_saldo": sin_saldo, "caidos": caidos}, fh, ensure_ascii=False)
    except Exception:
        pass
    return alertas, {"sin_saldo": sin_saldo, "caidos": caidos, "aparcados": aparcados}


def _check_drive_oauth(run=subprocess.run, state_dir=None):
    """¿Sigue vivo el permiso de ESCRITURA de Drive? (11-sep-2026)

    Ese día se descubrió a mitad de tarea: `drive.py` dio `invalid_grant` al mover el
    historial y hubo que tirar del conector MCP. Nadie lo había visto antes porque la lectura
    va por la cuenta de servicio, que no caduca: todo parecía sano hasta el primer write.
    Google emite refresh tokens de 7 días a las apps con la pantalla de consentimiento en
    «Testing», así que esto vuelve cada semana hasta pasarla a «En producción».

    Delega en `drive.py doctor` (subproceso: healthcheck se queda en stdlib). Solo avisa si el
    doctor dice CADUCADO; cualquier otro fallo (HALT, sin red) se registra y no grita.
    """
    import json as _json
    state_p = os.path.join(state_dir or HC, "drive_oauth.json")
    prev = {}
    try:
        prev = _json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pass
    if (time.time() - prev.get("ts", 0)) / 3600.0 < DRIVE_PROBE_INTERVAL_H:
        return ([("drive_oauth_caducado", prev["mensaje"])] if prev.get("caducado") else []), \
            {"throttled": True, "caducado": prev.get("caducado", False)}
    drive = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drive.py")
    try:
        r = run([sys.executable, drive, "doctor"], capture_output=True, timeout=120)
        salida_txt = (r.stdout or b"").decode("utf-8", "replace")
    except Exception as e:
        return [], {"error": "%r" % e}
    caducado = DRIVE_MARCA_CADUCADO in salida_txt
    mensaje = ("El permiso de escritura de Google Drive ha caducado: Polaris no puede mover, "
               "renombrar ni subir al historial. Arreglo: en tu terminal, "
               "`python3 tools/drive.py auth`. Para que no vuelva cada 7 días: Google Cloud "
               "(proyecto quiet-antler-500122-p9) → pantalla de consentimiento → «En producción».")
    try:
        with open(state_p, "w", encoding="utf-8") as f:
            _json.dump({"ts": time.time(), "caducado": caducado, "mensaje": mensaje}, f,
                       ensure_ascii=False)
    except Exception:
        pass
    return ([("drive_oauth_caducado", mensaje)] if caducado else []), {"caducado": caducado}


CI_PUBLICO_REPO = "BeyondTheProtocol/polaris"
CI_PUBLICO_INTERVAL_H = 1
CI_PUBLICO_ROJO = ("failure", "timed_out", "startup_failure")


CI_PUBLICO_MAX_BATERIAS = 5
_RE_CI_TS = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")
_RE_CI_ROJO = re.compile(r"🔴 ROJO: (.+?) \((?:rc=|log:)")
_RE_CI_NUMERADA = re.compile(r"^\d+:\s*(\S.*)$")


def _ci_rojos_del_log(texto):
    """[(batería, primera línea de fallo)] sacado del log de un job de `tests/test_all.sh`.

    POR QUÉ (24-sep-2026): la alerta decía «CI en ROJO» con el enlace y nada más. El tecnico que
    la investigó lo achacó a un choque de arreglos de poda cuando eran tres baterías concretas
    (test_regla_en_accion, test_singleton_guard, test_traspaso_compact). Con el nombre y la
    primera línea del fallo, quien lo mire empieza por el sitio correcto.
    Formato del log (visto en el run 36003140839): `🔴 ROJO: <batería> (rc=… · log: …)` en la
    corrida, y después el paso de detalle abre `##[group]rojo-<batería>.log` con las líneas de
    fallo numeradas (`2:  ❌ …`). Si ese paso no está, vale la línea anterior al ROJO (el tail -1
    de la batería)."""
    lineas = [_RE_CI_TS.sub("", l).rstrip() for l in str(texto or "").splitlines()]
    out = []
    for i, l in enumerate(lineas):
        m = _RE_CI_ROJO.search(l)
        if not m or m.group(1) in [b for b, _ in out]:
            continue
        bateria = m.group(1).strip()
        previa = lineas[i - 1].strip() if i else ""
        out.append((bateria, "" if previa.startswith("──") else previa))
    for n, (bateria, primera) in enumerate(out):
        try:
            k = lineas.index("##[group]rojo-%s.log" % bateria)
        except ValueError:
            continue
        for l in lineas[k + 1:]:
            if l.startswith("##[endgroup]"):
                break
            m = _RE_CI_NUMERADA.match(l)
            if m:
                out[n] = (bateria, m.group(1).strip())
                break
    return [(b, (p if len(p) <= 160 else p[:159] + "…")) for b, p in out]


def _ci_rojos_de_run(run_id, run, gh):
    """Baterías rojas de una ejecución: jobs fallidos → su log → `_ci_rojos_del_log`.
    Fail-soft: si algo no se puede leer, [] y la alerta sale igual, sin el detalle."""
    import json as _json
    try:
        r = run([gh, "run", "view", str(run_id), "-R", CI_PUBLICO_REPO, "--json", "jobs"],
                capture_output=True, timeout=60)
        if r.returncode != 0:
            return []
        jobs = _json.loads((r.stdout or b"{}").decode("utf-8", "replace")).get("jobs") or []
    except Exception:
        return []
    out = []
    for j in jobs:
        if not isinstance(j, dict) or j.get("conclusion") not in CI_PUBLICO_ROJO:
            continue
        try:
            r = run([gh, "api", "repos/%s/actions/jobs/%s/logs" % (CI_PUBLICO_REPO, j.get("databaseId"))],
                    capture_output=True, timeout=60)
            if r.returncode == 0:
                out.extend(_ci_rojos_del_log((r.stdout or b"").decode("utf-8", "replace")))
        except Exception:
            continue
    return out


def _check_ci_publico(run=subprocess.run, state_dir=None, gh=None):
    """¿Está en verde el CI del repo público? (22-sep-2026)

    `publicar_sync.py` empuja el espejo y da por bueno lo que dispara: el CI de
    BeyondTheProtocol/polaris estuvo rojo 6 veces seguidas (badge «failing» en la
    portada del lanzamiento) y el único aviso era un correo de GitHub que no lee nadie.
    Mira la ÚLTIMA ejecución TERMINADA de cada workflow en la rama por defecto: si está
    roja, avisa con el enlace. Una en curso no cuenta ni para bien ni para mal.
    Si `gh` no está o falla (sin red, sin sesión), se registra y no grita, como Drive.
    """
    import json as _json
    state_p = os.path.join(state_dir or HC, "ci_publico.json")
    prev = {}
    try:
        prev = _json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pass
    if (time.time() - prev.get("ts", 0)) / 3600.0 < CI_PUBLICO_INTERVAL_H:
        return list(map(tuple, prev.get("alertas", []))), {"throttled": True}
    gh = gh or shutil.which("gh") or "/opt/homebrew/bin/gh"
    try:
        r = run([gh, "run", "list", "-R", CI_PUBLICO_REPO, "--limit", "30", "--json",
                 "workflowName,headBranch,status,conclusion,url,createdAt,databaseId"],
                capture_output=True, timeout=60)
        if r.returncode != 0:
            return [], {"error": (r.stderr or b"").decode("utf-8", "replace")[:200]}
        runs = _json.loads((r.stdout or b"[]").decode("utf-8", "replace"))
    except Exception as e:
        return [], {"error": "%r" % e}
    ultima = {}   # workflow -> su ejecución terminada más reciente (gh las da de nueva a vieja)
    for x in runs:
        if x.get("status") == "completed" and x.get("headBranch") in ("master", "main"):
            ultima.setdefault(x.get("workflowName") or "?", x)
    rojos = {w: x for w, x in ultima.items() if x.get("conclusion") in CI_PUBLICO_ROJO}
    alertas = []
    if rojos:
        # Cuántas seguidas: el «6 veces rojo sin que nadie lo viera» es lo que se quiere contar.
        partes = []
        for w, x in sorted(rojos.items()):
            seguidas = 0
            for y in runs:
                if y.get("workflowName") != w or y.get("status") != "completed":
                    continue
                if y.get("conclusion") not in CI_PUBLICO_ROJO:
                    break
                seguidas += 1
            parte = "«%s» (%s seguida%s): %s" % (w, seguidas, "" if seguidas == 1 else "s",
                                                x.get("url", ""))
            baterias = _ci_rojos_de_run(x.get("databaseId"), run, gh) if x.get("databaseId") else []
            if baterias:
                parte += ". En rojo: " + "; ".join(
                    ("%s («%s»)" % (b, p)) if p else b for b, p in baterias[:CI_PUBLICO_MAX_BATERIAS])
                if len(baterias) > CI_PUBLICO_MAX_BATERIAS:
                    parte += "; y %d más" % (len(baterias) - CI_PUBLICO_MAX_BATERIAS)
            partes.append(parte)
        alertas.append(("ci_publico_rojo",
                        "El CI del repo público %s está en ROJO y la portada enseña el badge "
                        "«failing». %s" % (CI_PUBLICO_REPO, "; ".join(partes))))
    info = {"workflows": {w: x.get("conclusion") for w, x in ultima.items()}}
    try:
        with open(state_p, "w", encoding="utf-8") as f:
            _json.dump({"ts": time.time(), "alertas": alertas}, f, ensure_ascii=False)
    except Exception:
        pass
    return alertas, info


CORREO_PROBE_INTERVAL_H = 1   # correo importa más que las IAs genéricas: throttle más corto
CORREO_CUENTAS_MONITOR = ("titular.mgp@gmail.com", "titular@gmail.com")


def _check_correo_salud():
    """MONITOR DE CORREO (pieza 4 del correo "fino + monitorizado + organizado", 2/7/26):
    cierra el hueco de que Vega hoy no vigila la FONTANERÍA del correo (solo lee contenido).
    Comprueba, por cuenta: SMTP alcanzable (STARTTLS, sin enviar), IMAP alcanzable, y que la
    App Password de cada cuenta autentica de verdad (login test). Además, detecta REBOTES
    (mailer-daemon/undeliverable) recientes en INBOX — si Vega dejó un borrador o alguien
    intentó escribir y rebotó, esto lo nota sin que {{TITULAR}} tenga que darse cuenta ella.

    Throttle ~1h (no martillear Gmail). Fail-soft total: cualquier excepción de un check
    individual no tira el resto ni el ciclo de healthcheck. Es el hermano OPERATIVO del
    código rojo: fontanería, no amenaza al goal — nunca dispara codigo_rojo.py.

    NUNCA envía nada: smtp_alcanzable/login_ok(SMTP) solo hacen EHLO+STARTTLS(+LOGIN), nunca
    send_message; el IMAP es readonly de por sí (correo_imap._login/rebotes_recientes)."""
    import json as _json
    state_p = os.path.join(HC, "correo_salud.json")
    prev = {}
    try:
        prev = _json.load(open(state_p, encoding="utf-8"))
    except Exception:
        pass
    if (time.time() - prev.get("ts", 0)) / 3600.0 < CORREO_PROBE_INTERVAL_H:
        return [], {"throttled": True, "detalle": prev.get("detalle", {})}

    alertas, detalle = [], {}
    try:
        _cimap = _importar_sin_tocar_path("correo_imap")
    except Exception as e:
        return [], {"error": "no se pudo importar correo_imap: %r" % e}
    try:
        _csmtp = _importar_sin_tocar_path("correo_smtp")
    except Exception as e:
        return [], {"error": "no se pudo importar correo_smtp: %r" % e}

    # 1. SMTP alcanzable (una sola vez, no depende de cuenta — mismo host:puerto para todas).
    smtp_ok, smtp_motivo = _csmtp.smtp_alcanzable()
    detalle["smtp_alcanzable"] = {"ok": smtp_ok, "motivo": smtp_motivo}
    if not smtp_ok:
        alertas.append(("correo_smtp_inalcanzable",
                        "El servidor de envío de correo (smtp.gmail.com) no responde: %s. "
                        "Si necesitas enviar/dejar un borrador ahora, puede fallar." % smtp_motivo))

    # 2. IMAP + credenciales por cuenta.
    por_cuenta = {}
    for user in CORREO_CUENTAS_MONITOR:
        imap_ok, imap_motivo = _cimap.login_ok(user=user)
        por_cuenta[user] = {"imap_ok": imap_ok, "imap_motivo": imap_motivo}
        if not imap_ok:
            clave = "correo_imap_login:" + user.split("@")[0].replace(".", "_")
            alertas.append((clave,
                            "No puedo leer el correo de %s: %s (revisa la App Password en el "
                            "Llavero — puede haberse revocado o caducado)." % (user, imap_motivo)))
            continue   # sin IMAP no tiene sentido buscar rebotes en esta cuenta
        # 3. Rebotes recientes (solo si el login fue bien — reusa la misma credencial).
        try:
            rebotes = _cimap.rebotes_recientes(user=user, limit=40)
        except Exception as e:
            rebotes = []
            por_cuenta[user]["rebotes_error"] = "%r" % e
        por_cuenta[user]["rebotes"] = len(rebotes)
        if rebotes:
            clave = "correo_rebotes:" + user.split("@")[0].replace(".", "_")
            ejemplos = "; ".join("«%s»" % r.get("asunto", "?") for r in rebotes[:2])
            alertas.append((clave,
                            "Tienes %d correo(s) de REBOTE reciente en %s (%s). Puede que un "
                            "envío/borrador no haya llegado a su destino." % (len(rebotes), user, ejemplos)))

    # 3b. SMTP también autentica en al menos una cuenta (si IMAP falló para todas, probamos
    #     igual — puede que solo esté mal UNA de las dos claves, IMAP vs SMTP no comparten bug).
    smtp_login_falla = []
    if smtp_ok:
        for user in CORREO_CUENTAS_MONITOR:
            ok_l, motivo_l = _csmtp.login_ok(account=user)
            por_cuenta.setdefault(user, {})["smtp_login_ok"] = ok_l
            if not ok_l:
                smtp_login_falla.append((user, motivo_l))
        for user, motivo_l in smtp_login_falla:
            clave = "correo_smtp_login:" + user.split("@")[0].replace(".", "_")
            alertas.append((clave,
                            "No puedo autenticar para ENVIAR desde %s: %s (App Password del "
                            "Llavero, revísala)." % (user, motivo_l)))

    # SIN RED (13-sep-26): la noche del 11 al 12-sep, sin DNS, este monitor apuntó
    # correo_smtp_inalcanzable y correo_imap_login:* con el correo sano (verificado en vivo el 12-sep),
    # y correo_imap_login:titular ya va por 2 remisiones: a la 3ª queda intermitente. Sin DNS no se
    # distingue «Gmail no responde» o «App Password revocada» de «no hay red», así que esos fallos se
    # funden en red_sin_dns. Los rebotes no: solo se miran con login correcto. Solo se sondea si hay fallo.
    de_red = [a for a in alertas if a[0] == "correo_smtp_inalcanzable"
              or a[0].startswith(("correo_imap_login:", "correo_smtp_login:"))]
    if de_red and not _hay_dns():
        detalle["sin_dns"] = True
        alertas = [a for a in alertas if a not in de_red] + [("red_sin_dns", RED_SIN_DNS_TEXTO)]

    detalle["cuentas"] = por_cuenta
    try:
        with open(state_p, "w", encoding="utf-8") as f:
            _json.dump({"ts": time.time(), "detalle": detalle}, f, ensure_ascii=False)
    except Exception:
        pass
    return alertas, detalle


def _edad_horas_ts(ts):
    """Edad en horas de un ts de heartbeat. Soporta ISO-Z (UTC, run_agent: '2026-06-24T22:32:52Z') y
    'YYYY-MM-DD HH:MM' local (calendar_sync). Devuelve None si no se puede interpretar (no crashea)."""
    if not ts:
        return None
    ts = ts.strip()
    try:
        if ts.endswith("Z"):
            dt, ref = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"), datetime.utcnow()
        elif "T" in ts:
            dt, ref = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"), datetime.now()
        else:
            dt, ref = datetime.strptime(ts[:16], "%Y-%m-%d %H:%M"), datetime.now()
        return max(0.0, (ref - dt).total_seconds() / 3600.0)
    except Exception:
        return None


def _leer_heartbeat(agente):
    """(edad_h, estado, corrupto). corrupto=True si el fichero existe pero NO es JSON legible
    (lo auto-curamos). Sin fichero → (None, None, False)."""
    p = os.path.join(HB_DIR, agente + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return (None, None, False)
    except Exception:
        return (None, None, True)
    if not isinstance(d, dict):
        return (None, None, True)
    return (_edad_horas_ts(d.get("ts")), d.get("estado"), False)


def _ts_heartbeat_raw(agente):
    """El `ts` CRUDO (string, sin convertir a edad) del heartbeat de `agente`, o None si no hay
    fichero/es ilegible. Hermano de _leer_heartbeat (que solo da la edad en horas) — hace falta el
    ts crudo para comparar contra observabilidad.hubo_ok_desde(), que parsea sus propios timestamps.
    Fail-soft: nunca lanza."""
    p = os.path.join(HB_DIR, agente + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("ts") if isinstance(d, dict) else None
    except Exception:
        return None


def _cuarentena_heartbeat(agente):
    """Aparta un heartbeat ilegible para que el daemon lo regenere limpio. Auto-heal SEGURO: el
    heartbeat es estado regenerable, no fuente de verdad (nada irreversible)."""
    p = os.path.join(HB_DIR, agente + ".json")
    try:
        os.replace(p, p + ".corrupto-" + datetime.now().strftime("%Y%m%dT%H%M%S"))
        return True
    except OSError:
        return False


def _credito_cuenta_bajo():
    """¿La cuenta de Claude se quedó sin crédito PREPAGO? Señal FRESCA = el heartbeat del daemon
    agéntico tras su última pasada: run_agent escribe estado 'credito_agotado' cuando la API devolvió
    'Credit balance is too low'. Eso lo DISTINGUE de 'aplazado_tope_local' (el tope LOCAL de {{TITULAR}}, que
    es OTRO arreglo) y de un 'ok' posterior. Antes leíamos el .out, que se quedaba viejo y gritaba 'sin
    crédito' cuando el bloqueo real ya había pasado al tope local (bug 25/6). Tolerante: nunca lanza.

    MATIZ (3/7/26, "señal FRESCA de crédito"): un 'credito_agotado' fresco puede ser un BLIP momentáneo
    que ya se recuperó solo (el caso real del 1/7: caída a las 14:00, pasadas reales gastando de nuevo
    a las 14:31 — el auto-reload de cost_guard ya lo resolvió). Antes de avisar "recarga el crédito",
    mismo criterio de supersede que el resto del fichero: si observabilidad tiene una pasada 'ok'
    ESTRICTAMENTE posterior al heartbeat 'credito_agotado' para ese mismo agente, el blip ya se
    recuperó → NO se considera bajo. Un agotamiento SOSTENIDO (sin ok posterior) sigue avisando claro,
    tal cual antes."""
    for d in VEGA_DAEMONS:
        if not d.get("agentico"):
            continue
        edad, est, _corrupto = _leer_heartbeat(d["agente"])
        # Guarda de FRESCURA: un 'credito_agotado' solo cuenta si el latido es reciente (≤ la
        # cadencia del daemon). El estado es pegajoso —se queda hasta que el daemon vuelve a correr
        # y lo sobreescribe—, así que sin esta guarda un crédito agotado de hace días seguiría
        # gritando "sin crédito" después de recargar, hasta el próximo run. Si el latido envejece,
        # deja de ser señal de crédito; pasa a ser "lleva demasiado tiempo sin correr" (más abajo).
        if est == "credito_agotado" and edad is not None and edad <= d["cadencia_h"]:
            ts_hb = _ts_heartbeat_raw(d["agente"])
            try:
                import observabilidad as _obs
                recuperado = _obs.hubo_ok_desde(d["agente"], ts_hb)
            except Exception:
                recuperado = False
            if recuperado:
                continue        # blip ya recuperado (pasada OK posterior) → no cuenta como agotado
            return True
    return False


def _autofix_daemon_por_heartbeat(agente, daemon_label, cadencia_h):
    """Autofix ACOTADO (2/7/26, primer uso: bot-telegram) para un daemon VIVO pero cuyo heartbeat
    propio quedó rancio (p.ej. long-poll atascado en un bucle de resets de red). Un kickstart fuerza
    a launchd a relanzar el proceso, que reabre la conexión desde cero. MÁXIMO 1 kickstart por ciclo
    de healthcheck (no hay bucle de reinicios); si tras el kickstart el heartbeat sigue sin
    refrescarse (o vuelve a quedarse rancio en el SIGUIENTE ciclo), se deja de reintentar y se avisa
    en llano — igual que _check_servicios_vivos/_check_gateway_vivo. Devuelve True si tras el
    kickstart el heartbeat vuelve a estar fresco antes de LATIDO_TRAS_KICKSTART_MAX_S (autofix silencioso, sin alertar). Genérica
    para cualquier entrada de VEGA_DAEMONS con `daemon_label` (hoy solo bot-telegram lo declara).
    Fail-soft: nunca lanza."""
    if not _kickstart_daemon(daemon_label):
        return False
    limite = time.time() + LATIDO_TRAS_KICKSTART_MAX_S
    while True:
        edad_h2, _est2, corrupto2 = _leer_heartbeat(agente)
        if (not corrupto2) and edad_h2 is not None and edad_h2 <= cadencia_h:
            return True
        if time.time() >= limite:
            return False
        time.sleep(LATIDO_TRAS_KICKSTART_PASO_S)


def _puedo_juzgar_latidos(hb_dir=None, daemons=None):
    """(puedo, motivo). Un vigía que no puede LEER los latidos no ve un sistema muerto: ve su
    propia ceguera, y decirlo es la diferencia entre un aviso útil y una cascada de falsas alarmas.

    Nació el 22-sep-2026: un aviso dio por caídos a seis daemons (y el libro de deuda «999 días»)
    cuando todos habían latido hacía minutos. Dos causas, la misma consecuencia: (1) el healthcheck
    corría desde un WORKTREE, donde `tools/state/heartbeat/` está vacío porque los latidos no se
    versionan; (2) un hipo de lectura del directorio en una sola pasada."""
    hb_dir = HB_DIR if hb_dir is None else hb_dir
    daemons = VEGA_DAEMONS if daemons is None else daemons
    if not daemons:
        return True, None
    if not os.path.isdir(hb_dir):
        return False, ("no existe el directorio de latidos (%s): desde aquí no se puede juzgar "
                       "la salud de nada" % hb_dir)
    try:
        hay = [f for f in os.listdir(hb_dir) if f.endswith(".json")]
    except Exception as e:
        return False, "no se puede leer el directorio de latidos (%r)" % e
    if not hay:
        return False, ("el directorio de latidos está vacío (%s). Si esto corre desde un worktree, "
                       "es lo esperado: los latidos viven solo en casa base" % hb_dir)
    return True, None


def _salud_daemons():
    """Salud REAL de los daemons de Vega: separa el GATE de {{TITULAR}} (sin dinero) del error de código,
    auto-cura el estado corrupto y devuelve (alertas, info).

    Las alertas son tuplas (clave_estable, texto_visible): la clave NO lleva números volátiles
    (identificador del daemon + tipo de fallo), el texto puede tener detalle para {{TITULAR}}.
    Esto respeta el anti-flapping de _emitir_si_cambia; el detalle (edades, motivo) va ademas
    en `info` para last_check.json y El Observatorio. Nunca lanza."""
    alertas, info = [], {}
    # 1. ¿Vega puede usar la IA? Dos límites DISTINTOS, con arreglos distintos:
    #    · cost_guard ve los topes LOCALES de gasto que puso {{TITULAR}} (diario/mensual) → "espera al reset".
    #    · el saldo PREPAGO de la cuenta Anthropic NO lo ve cost_guard; solo asoma como 'Credit balance
    #      is too low' en el resultado del agente → "recarga el crédito". Es el caso real del 25/6.
    hay_saldo = True
    try:
        ok_s, motivo, _ = cost_guard.check_before_job(esencial=True)
        hay_saldo = bool(ok_s)
        info["saldo"] = {"ok": hay_saldo, "motivo": motivo}
    except Exception as e:
        info["saldo_error"] = "%r" % e
    sin_credito = _credito_cuenta_bajo()
    info["credito_cuenta_ok"] = not sin_credito
    bloqueado_por_dinero = (not hay_saldo) or sin_credito
    if sin_credito:
        alertas.append(("vega_sin_credito_cuenta",
                        "Vega no puede trabajar: se ha agotado el CRÉDITO de la cuenta de Claude (no es "
                        "tu tope local, que aún tiene margen). Recárgalo y vuelve sola; tu parte de HOY, "
                        "que no gasta, sigue saliendo igual."))
    elif not hay_saldo:
        alertas.append(("vega_sin_saldo_local",
                        "Vega está en pausa: ha llegado al tope de gasto del día/mes que tienes puesto. "
                        "Sube el tope o espera al reset; tu parte de HOY, que no gasta, sigue saliendo igual."))
    # 2. daemons con heartbeat propio: estado + frescura, con auto-heal del estado corrupto.
    # Antes de juzgar a nadie: ¿puedo LEER los latidos? Si no, se dice ESO y se para. Un vigía
    # ciego que declara muerto al sistema entero quema la confianza en todos sus avisos.
    puedo, motivo_ciego = _puedo_juzgar_latidos()
    if not puedo:
        info["no_puedo_juzgar"] = motivo_ciego
        alertas.append(("vigia_sin_latidos",
                        "NO PUEDO JUZGAR la salud de los daemons: %s. Esto NO significa que estén "
                        "caídos." % motivo_ciego))
        return alertas, info
    sin_dns = None                         # se sondea como mucho UNA vez por pasada, y solo si hace falta
    for d in VEGA_DAEMONS:
        agente = d["agente"]
        edad_h, est, corrupto = _leer_heartbeat(agente)
        info[agente] = {"edad_h": round(edad_h, 2) if edad_h is not None else None,
                        "estado": est, "corrupto": corrupto}
        if corrupto:
            _cuarentena_heartbeat(agente)
            alertas.append(("daemon_%s_corrupto" % agente,
                            "Reseteé un fichero de estado ilegible de %s (se regenera solo)." % d["label"]))
            continue
        sano = est in ESTADOS_HB_SANOS
        fresco = edad_h is not None and edad_h <= d["cadencia_h"]
        if sano and fresco:
            continue
        if bloqueado_por_dinero:
            continue                       # un fallo/atraso por falta de dinero ya lo explica el aviso de arriba
        # SIN RED (13-sep-26): un daemon cuyo latido depende de hablar con internet (los que
        # declaran daemon_label: hoy bot-telegram) no puede latir si el Mac no resuelve nombres. Ahí
        # el latido rancio es de la red y no del daemon: se avisa de la red, y no se kickstartea
        # porque relanzarlo sin DNS no arregla nada (lo probó el .err del 12-sep, tras cada
        # «daemon arriba»). Va ANTES del autofix. La clave `red_sin_dns` está en R5 de deuda.py.
        if d.get("daemon_label") and est != "critico_bloqueado" and edad_h is not None and not fresco:
            if sin_dns is None:
                sin_dns = not _hay_dns()
            if sin_dns:
                info[agente]["sin_dns"] = True
                if not any(a[0] == "red_sin_dns" for a in alertas):
                    alertas.append(("red_sin_dns", RED_SIN_DNS_TEXTO))
                continue
        # AUTOFIX (2/7/26): solo para daemons KeepAlive con heartbeat propio (daemon_label) y solo
        # cuando ya hubo señal previa que se quedó RANCIA (edad_h no None) — "nunca corrió" (edad_h
        # None) puede ser un despliegue recién hecho, no un atasco; ahí no forzamos kickstart. Máximo
        # 1 kickstart por ciclo (sin bucle). Si revive → traza operativa, sin alertar a {{TITULAR}}.
        if d.get("daemon_label") and est != "critico_bloqueado" and edad_h is not None and not fresco:
            if _autofix_daemon_por_heartbeat(agente, d["daemon_label"], d["cadencia_h"]):
                info[agente]["autofix"] = True
                alertas.append(("daemon_%s_autofix" % agente,
                                "Reviví solo %s (su latido se había quedado atascado, probablemente un "
                                "bucle de resets de red)." % d["label"]))
                _marcar_acuse_autofix("daemon_%s_fallo" % agente, "kickstart")
                _marcar_acuse_autofix("daemon_%s_parado" % agente, "kickstart")
                continue
            info[agente]["autofix"] = False
        # SUPERSEDE (3/7/26, mismo espíritu que RUTINAS_NED): un heartbeat clavado en mal estado
        # queda TAPADO si hay una pasada OK ESTRICTAMENTE más reciente en la traza de observabilidad
        # (bug real: el heartbeat de una pasada suelta que falló no se sobreescribe hasta la
        # SIGUIENTE corrida — si esa siguiente ya salió bien, gritar por la vieja es falso positivo).
        # Solo aplica con un ts que comparar (edad_h no None) y NUNCA para 'critico_bloqueado' (un
        # bloqueo de garantías se avisa siempre, no se tapa con un ok posterior de otra pasada).
        if edad_h is not None and est != "critico_bloqueado":
            ts_hb = _ts_heartbeat_raw(agente)
            try:
                import observabilidad as _obs
                tapado = _obs.hubo_ok_desde(agente, ts_hb)
            except Exception:
                tapado = False
            if tapado:
                info[agente]["supersedido_por_ok_posterior"] = True
                continue        # una corrida posterior ya salió bien → no se alerta (no enmascara: solo tapa lo VIEJO)
        if est == "critico_bloqueado":
            alertas.append(("daemon_%s_critico" % agente,
                            "%s se ha bloqueado: una tarea crítica no se pudo atender con garantías. Conviene mirarlo." % d["label"]))
        elif edad_h is None:
            alertas.append(("daemon_%s_sin_senal" % agente,
                            "No tengo señal reciente de %s." % d["label"]))
        elif not fresco:
            alertas.append(("daemon_%s_parado" % agente,
                            "%s lleva demasiado tiempo sin correr." % d["label"]))
        else:
            alertas.append(("daemon_%s_fallo" % agente,
                            "%s falló en su última pasada." % d["label"]))
    # Varios «sin señal» en la MISMA pasada no son varios daemons muertos: es el directorio de
    # latidos que no se dejó leer en ese instante (22-sep-2026: seis a la vez, todos vivos). Se
    # colapsan en un aviso de lectura, que es lo que de verdad pasó.
    mudos = [a for a in alertas if a[0].endswith("_sin_senal")]
    if len(mudos) >= UMBRAL_CASCADA_LATIDOS:
        alertas = [a for a in alertas if not a[0].endswith("_sin_senal")]
        info["cascada_sin_senal"] = [a[0] for a in mudos]
        alertas.append(("vigia_lectura_latidos",
                        "%d daemons han salido «sin señal» a la vez: eso es un fallo de LECTURA de "
                        "los latidos, no %d daemons caídos. Reviso en la próxima pasada."
                        % (len(mudos), len(mudos))))
    return alertas, info


def _frescura_rutina(d, edad_h):
    """Estado de `estado_rutina` para una rutina NED cuyo último latido es SANO y tiene `edad_h`
    horas: el éxito y la ejecución son el mismo instante. Con periodo P = periodo_h + margen_h:
    hasta P «al-dia», pasado P «atrasada», desde 2P «rota». Sin periodo declarado, o con el .HALT
    puesto (las rutinas no corren a propósito, como en `daemon_inactivo`), devuelve None: no alarma.
    Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026."""
    if not d.get("periodo_h"):
        return None
    try:
        if salida.halted():
            return None
    except Exception:
        pass
    ahora = datetime.now()
    ultima = ahora - timedelta(hours=edad_h)
    try:
        return estado_rutina.clasificar(
            ultima, ultima, timedelta(hours=d["periodo_h"] + d.get("margen_h", 0)), ahora)
    except ValueError:
        return None


# ── Recuperar al arrancar (F1) y latido externo (F2), 25-sep-2026 ─────────────────────────────
# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
# F1. launchd repite al despertar un StartCalendarInterval perdido durante el REPOSO, pero no uno
# perdido con el Mac APAGADO (corte de luz, FileVault esperando). En las 2 primeras horas tras
# arrancar, una rutina NED «atrasada» se relanza una vez con `kickstart` (sin -k: nunca mata una
# pasada viva). Corre en la primera pasada normal del vigía (StartInterval 30 min), NO con
# RunAtLoad: al cargar, el vigía entero haría kickstart -k de daemons que launchd aún levanta
# (verificación, 25-sep). Tres frenos más: solo si launchd ya la tiene cargada (encender algo
# descargado es gate de {{TITULAR}}), solo con red (si no, la pasada fallaría y la marca impediría
# reintentar) y nunca si su propio disparo cae en menos de 2 h (run_agent.sh no tiene candado:
# serían dos pasadas a la vez).
RECUPERAR_VENTANA_SEG = 2 * 3600
RECUPERAR_MARGEN_DISPARO_MIN = 120


def _arranque_ts():
    """Epoch del último arranque (sysctl kern.boottime), o None."""
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=5).stdout
        m = re.search(r"sec\s*=\s*(\d+)", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _kickstart_sin_matar(label):
    """`launchctl kickstart` SIN -k: arranca el job cargado; si ya corre, no hace nada."""
    try:
        r = subprocess.run(["launchctl", "kickstart", "gui/%d/%s" % (os.getuid(), label)],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _sci_de(label):
    """StartCalendarInterval del plist instalado de `label`, o None."""
    import plistlib
    try:
        with open(os.path.join(_dir_plists(), label + ".plist"), "rb") as fh:
            return plistlib.load(fh).get("StartCalendarInterval")
    except Exception:
        return None


def _recuperar_tras_arranque(ahora=None):
    """Relanza, una vez por arranque, las rutinas NED que el apagado dejó atrás. Devuelve info.
    Nunca lanza."""
    info = {}
    ahora = time.time() if ahora is None else ahora
    boot = _arranque_ts()
    if boot is None or ahora - boot > RECUPERAR_VENTANA_SEG:
        return {"fuera_de_ventana": True}
    try:
        if salida.halted():
            return {"halt": True}
    except Exception:
        pass
    marca = os.path.join(HC, "recuperado_arranque.json")
    try:
        with open(marca, encoding="utf-8") as f:
            if json.load(f).get("boot") == boot:
                return {"ya_hecho_en_este_arranque": True}
    except Exception:
        pass
    if not _hay_dns():
        return {"sin_red": True}           # sin marca: la siguiente pasada de la ventana reintenta
    estado = _launchctl_estado() or {}
    relanzadas = []
    ahora_dt = datetime.fromtimestamp(ahora)
    for d in RUTINAS_NED:
        edad_h, est, corrupto = _leer_heartbeat(d["agente"])
        if corrupto or edad_h is None or est not in ESTADOS_HB_SANOS or est in ESTADOS_NED_MALOS:
            continue
        if _frescura_rutina(d, edad_h) not in ("atrasada", "rota"):
            continue
        label = d.get("plist")
        pid = estado.get(label, (None, None))[0]
        if label not in estado or pid not in ("-", ""):
            info[d["agente"]] = "no cargada o ya corriendo: no se toca"
            continue
        falta = _minutos_hasta_disparo(_sci_de(label), ahora_dt)
        if falta is not None and falta < RECUPERAR_MARGEN_DISPARO_MIN:
            info[d["agente"]] = "le toca sola en %d min: no se duplica" % falta
            continue
        if _kickstart_sin_matar(label):
            relanzadas.append(d["agente"])
            _marcar_acuse_autofix("rutina_ned_%s_sin_correr" % d["agente"], "relanzada al arrancar")
    info["relanzadas"] = relanzadas
    _write_atomic(marca, {"boot": boot, "relanzadas": relanzadas,
                          "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")})
    return info


# F2. Latido hacia fuera: el único vigía que ve un Mac apagado es uno que no vive en él. Cada
# pasada completa hace un GET a Healthchecks.io con un UUID opaco y NADA MÁS: ni cuerpo, ni
# nombres, ni alertas. Si deja de llegar, Healthchecks avisa a {{TITULAR}} por email. El UUID vive en el
# Llavero; sin él, esto no hace nada. Con el .HALT puesto no sale (el HALT para todo lo que sale).
LATIDO_SECRETO = "btp-latido-externo"
LATIDO_BASE = "https://hc-ping.com/"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


LLAVERO_INACCESIBLE = "llavero-inaccesible"


def _uuid_latido():
    """El UUID del Llavero, None si no existe (rc 44), o LLAVERO_INACCESIBLE si el Llavero no se
    deja leer (p. ej. rc 36 bajo launchd). Confundirlos haría que Healthchecks avisara «down» sin
    que nada en el Mac explicara por qué (la misma lección que tools/ia.py)."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", LATIDO_SECRETO, "-w"],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return LLAVERO_INACCESIBLE
    if r.returncode == 44:
        return None
    if r.returncode != 0:
        return LLAVERO_INACCESIBLE
    out = r.stdout.strip().lower()
    return out if _UUID_RE.match(out) else None


def _latido_url(fallo=False):
    """La URL entera del ping, o None. Solo base fija + UUID validado + «/fail» opcional."""
    uuid = _uuid_latido()
    if not uuid or not _UUID_RE.match(uuid):
        return None
    return LATIDO_BASE + uuid + ("/fail" if fallo else "")


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    """Un 3xx no se sigue: el ping solo va a hc-ping.com."""
    def redirect_request(self, *a, **k):
        return None


def _abrir_latido(req, timeout=10):
    """Opener propio: sin proxy del sistema y sin redirecciones."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       _SinRedirecciones()).open(req, timeout=timeout)


def _latido_externo(fallo=False, abrir=None):
    """→ 'sin-configurar' · 'llavero-inaccesible' · 'halt' · 'ok' · 'error'. Nunca lanza.
    Con el .HALT no sale: tras la gracia del check, Healthchecks mandará un «down». Es a propósito
    (el HALT para todo lo que sale) y está escrito en la nota del plan."""
    try:
        if salida.halted():
            return "halt"
    except Exception:
        pass
    if _uuid_latido() == LLAVERO_INACCESIBLE:
        return LLAVERO_INACCESIBLE
    url = _latido_url(fallo)
    if not url:
        return "sin-configurar"
    abrir = abrir or _abrir_latido
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "btp"})
    for _ in range(2):
        try:
            with abrir(req, timeout=10) as r:
                if 200 <= getattr(r, "status", 200) < 300:
                    return "ok"
        except Exception:
            time.sleep(2)
    return "error"


def _check_rutinas_ned():
    """WATCHDOG de rutinas NED (C, 2/7/26): auto-mejora / git / comite-medico llevan ≥2 días
    CLAVADAS en un estado NO sano (fallo/aplazado/critico/centralita) sin que nadie lo note.
    A diferencia de _salud_daemons (que exige FRESCURA porque Vega corre a diario), aquí NO se
    exige — comite-medico es MENSUAL, un heartbeat de hace 20 días es normal si su ESTADO es
    sano. Lo que se vigila es la EDAD del último estado MALO: si el heartbeat más reciente que
    tenemos ya lleva ≥ RUTINA_NED_DIAS_UMBRAL días y ese estado es malo (nadie volvió a correrla
    con éxito desde entonces), se avisa. Heartbeat corrupto → auto-heal (igual que _salud_daemons,
    es estado regenerable). Fail-soft: nunca lanza. Devuelve (alertas, info)."""
    alertas, info = [], {}
    for d in RUTINAS_NED:
        agente = d["agente"]
        edad_h, est, corrupto = _leer_heartbeat(agente)
        info[agente] = {"edad_h": round(edad_h, 2) if edad_h is not None else None, "estado": est}
        if corrupto:
            _cuarentena_heartbeat(agente)
            alertas.append(("rutina_ned_%s_corrupto" % agente,
                            "Reseteé un fichero de estado ilegible de %s (se regenera solo)." % d["label"]))
            continue
        if edad_h is None or est is None:
            continue                             # nunca corrió aún → sin baseline, no se alarma (igual que el dead-man)
        if est == "arranca":
            # run_agent escribe «arranca» al empezar y lo pisa al acabar. Si sigue ahí pasado el
            # periodo + margen, la pasada se colgó o murió sin dejar estado (25-sep-2026, cazado por
            # verificación: 5 días en «arranca» no avisaban).
            if _frescura_rutina(d, edad_h) in ("atrasada", "rota"):
                info[agente]["frescura"] = "colgada"
                alertas.append(("rutina_ned_%s_sin_correr" % agente,
                                "%s arrancó hace %d día(s) y no ha terminado: la pasada se colgó "
                                "o murió sin dejar estado." % (d["label"], int(edad_h / 24.0))))
            continue
        if est in ESTADOS_HB_SANOS and est not in ESTADOS_NED_MALOS:
            # Última pasada sana. Lo que falta ver es si ha DEJADO de correr (25-sep-2026).
            frescura = _frescura_rutina(d, edad_h)
            info[agente]["frescura"] = frescura
            if frescura in ("atrasada", "rota"):
                alertas.append(("rutina_ned_%s_sin_correr" % agente,
                                "%s no corre desde hace %d día(s) y le tocaba cada %d h: su última "
                                "pasada salió bien, pero no ha vuelto a arrancar (%s)."
                                % (d["label"], int(edad_h / 24.0), d["periodo_h"], frescura)))
            continue
        if est not in ESTADOS_NED_MALOS:
            continue                             # estado desconocido/no clasificado → conservador, no alarma
        dias = edad_h / 24.0
        if dias < RUTINA_NED_DIAS_UMBRAL:
            continue                             # malo pero reciente → puede autocorregirse en su próxima pasada normal
        alertas.append(("rutina_ned_%s_atascada" % agente,
                        "%s lleva %d día(s) clavada en estado «%s» sin corregirse (parte del camino a NED "
                        "— conviene mirarla)." % (d["label"], int(dias), est)))
    return alertas, info


def _check_gemelos_rancios():
    """WATCHDOG de los GEMELOS DE CRITERIO (20-sep-26): consejeros espejo de una persona real
    ({{CONTACTO}}, Alby, {{CONTACTO}}, Sid, {{CONTACTO}}) que dejaron de aprender sin que nadie lo notara.

    POR QUÉ EXISTE: el radar de {{CONTACTO}} pasó **86 días sin un solo pase** (`Radar-{{CONTACTO}}.md`
    intacto desde el 26-jun) y la rutina seguía cerrando en verde, porque «sin novedad» y
    «carril bloqueado» se reportaban igual. Aquí se separan, que es lo único que evita que
    vuelva a pasar:
      - `gemelo_<slug>_rancio`        → ≥21 días sin un pase EJECUTABLE (nadie lo mira).
      - `gemelo_<slug>_carril_muerto` → una fuente ≥14 días bloqueada (no es que no haya
        novedad: es que ese carril no se puede ni ejecutar).

    Un gemelo que NUNCA pasó no alarma (sin baseline, igual que `_check_rutinas_ned`).
    Fail-soft: nunca lanza. Devuelve (alertas, info)."""
    alertas, info = [], {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import radar_personas
        salud = radar_personas.salud(umbral_rancio_dias=GEMELO_RANCIO_DIAS,
                                     umbral_carril_dias=GEMELO_CARRIL_MUERTO_DIAS)
    except Exception as e:
        return [], {"error": "%r" % e}
    for slug, d in sorted(salud.items()):
        info[slug] = d
        if d.get("nunca_paso"):
            continue                              # sin baseline → no se alarma
        if d.get("rancio"):
            alertas.append(("gemelo_%s_rancio" % slug,
                            "El gemelo de %s (%s) lleva %d días sin refrescarse: su criterio se "
                            "está quedando viejo." % (d.get("persona", slug), d.get("agente", "?"),
                                                      d.get("dias_sin_pase_ejecutable") or 0)))
        for c in d.get("carriles_muertos") or []:
            alertas.append(("gemelo_%s_carril_muerto_%s" % (slug, c["fuente"]),
                            "El gemelo de %s lleva %d días con la fuente «%s» bloqueada — no es "
                            "que no haya novedad, es que no se puede ni mirar (%s)."
                            % (d.get("persona", slug), c["dias"], c["fuente"], c.get("motivo", ""))))
    return alertas, info


def _check_jobs_caidos():
    """Jobs que murieron en el dequeue y que NADIE reportaba.

    `cola.py` manda a failed/ por json-ilegible, schema-invalido, caducado, profundidad y
    dead-letter, y de todos ellos solo `schema-desconocido` tenía red (el auto-recover B2).
    Los demás se quedaban ahí para siempre en silencio. Importa sobre todo por la CADUCIDAD:
    `bot_telegram` es el ÚNICO productor que pone `expira` — o sea que el job perecedero es
    justo el que {{TITULAR}} manda por Telegram. Un encargo suyo hecho con el lazo parado caduca y
    se evapora sin que nadie lo diga.

    Clave estable por MOTIVO (no por número: el conteo es volátil y rompería el anti-flapping).
    Categoría humano: un encargo perdido lo tiene que ver ella, no un autofix.

    VENTANA DE FRESCURA (31-jul-2026). Hasta hoy esto alertaba por CADA fichero que hubiera en
    `failed/`, sin mirar su fecha. Como nadie vacía esa carpeta, dos cadáveres del 30-jul
    mantuvieron el hallazgo `jobs_caidos:rc=1` escalado **73 veces** y la batería roja tres días
    seguidos — mientras el problema real ya no ocurría (el `tecnico` volvía a ejecutarse bien).
    Una alarma que confunde «hay un montón de cadáveres» con «se está muriendo gente» deja de
    leerse, y entonces la número 74, la de verdad, tampoco se lee.

    Ahora solo alerta de lo que murió en los últimos `VENTANA_CAIDOS_DIAS` días. Lo viejo NO se
    borra ni se esconde: sigue contado en `info` (`failed_viejos`) para que se vea que está ahí,
    pero deja de gritar. Mismo principio que la des-escalada de `deuda.py`: lo que se va, se calla.
    """
    alertas, info = [], {}
    d = os.path.join(q.QUEUE, "failed")
    try:
        ficheros = [f for f in os.listdir(d) if f.endswith(".json")]
    except Exception:
        return alertas, info                      # sin cola aún: nada que decir
    corte = time.time() - VENTANA_CAIDOS_DIAS * 86400
    por_motivo, ejemplos, viejos = {}, {}, 0
    for f in ficheros:
        ruta = os.path.join(d, f)
        motivo, j = "desconocido", None
        try:
            with open(ruta, encoding="utf-8") as fh:
                j = json.load(fh)
            motivo = str(j.get("ultimo_error") or "desconocido")[:40]
        except Exception:
            pass
        # Cuándo murió: `terminado` si está, y si no la fecha del fichero (fail-open a "reciente"
        # solo si no hay ninguna de las dos: mejor un aviso de más que perder una muerte nueva).
        muerto_ts = None
        if isinstance(j, dict) and j.get("terminado"):
            try:
                muerto_ts = datetime.fromisoformat(str(j["terminado"])).timestamp()
            except (ValueError, TypeError):
                muerto_ts = None
        if muerto_ts is None:
            try:
                muerto_ts = os.path.getmtime(ruta)
            except OSError:
                muerto_ts = None
        if muerto_ts is not None and muerto_ts < corte:
            viejos += 1
            continue
        ejemplos.setdefault(motivo, str((j or {}).get("intencion") or (j or {}).get("id") or f)[:60])
        por_motivo[motivo] = por_motivo.get(motivo, 0) + 1
    info["failed_total"] = len(ficheros)
    info["failed_viejos"] = viejos                # están ahí, pero ya no gritan
    info["failed_ventana_dias"] = VENTANA_CAIDOS_DIAS
    info["failed_por_motivo"] = por_motivo
    for motivo, n in sorted(por_motivo.items()):
        # `schema-desconocido` ya tiene su propio camino (se re-encola solo tras fusionar).
        if motivo.startswith("schema-desconocido"):
            continue
        que = "caducó antes de ejecutarse" if "caduc" in motivo else motivo
        alertas.append((
            "jobs_caidos:%s" % motivo,
            "🗃️ %d encargo(s) no llegaron a ejecutarse (%s) en los últimos %d días. El primero: "
            "«%s». Están en tools/state/queue/failed/ y NO se reintentan solos."
            % (n, que, VENTANA_CAIDOS_DIAS, ejemplos.get(motivo, "?"))))
    return alertas, info


def _check_presupuesto():
    """C — BURN-RATE del presupuesto: avisa ANTES de topar, no cuando ya topaste (el hueco del 28/6:
    el mensual llegó a $1.392/$1.500 en silencio). Reusa los accesores que YA existen en cost_guard
    (today_spent / month_spent / _limits). El MENSUAL es el crítico: su techo está fijado en código y
    si toca, se pausa TODO hasta el reset del mes. Tuplas (clave_estable, texto) → anti-flapping (las
    claves NO llevan los % volátiles; el detalle con números va en el texto). Categoría humano (decide
    {{TITULAR}}: subir tope / regular). Fail-soft: nunca lanza."""
    alertas, info = [], {}
    try:
        d, _, m = cost_guard._limits()
        gd = cost_guard.today_spent()
        gm = cost_guard.month_spent()
    except Exception as e:
        info["error"] = "%r" % e
        return alertas, info
    import calendar as _cal
    hoy = datetime.now()
    dim = _cal.monthrange(hoy.year, hoy.month)[1]
    dias_rest = dim - hoy.day
    info.update({"gasto_dia": round(gd, 2), "tope_dia": d, "gasto_mes": round(gm, 2), "tope_mes": m})

    # Mensual ≥ umbral → el peligroso (techo duro; si topa, se pausa todo hasta el día 1).
    if m > 0 and gm / m >= PRESUP_MENSUAL_FRAC:
        alertas.append(("presupuesto_mensual_alto",
                        "Vas por el %d%% del tope MENSUAL de gasto ($%.0f de $%.0f; quedan ~$%.0f para %d día(s)). "
                        "Si toca el techo se pausa TODO hasta el día 1 — súbelo o regula el ritmo."
                        % (round(100 * gm / m), gm, m, max(0.0, m - gm), dias_rest)))

    # Proyección mensual: a este ritmo, ¿topa antes de fin de mes? Solo con datos suficientes (día ≥ 3).
    if m > 0 and gm > 0 and hoy.day >= 3:
        ritmo = gm / hoy.day                       # $/día medio en lo que va de mes
        proyectado = ritmo * dim
        info["proyeccion_fin_mes"] = round(proyectado, 2)
        if proyectado > m and ritmo > 0:
            import math as _math
            dia_topa = _math.ceil(m / ritmo)
            if dia_topa <= dim:
                alertas.append(("presupuesto_mensual_proyeccion",
                                "A este ritmo toparás el tope MENSUAL hacia el día %d (proyección $%.0f > $%.0f). "
                                "Conviene regular o subir el techo antes." % (dia_topa, proyectado, m)))

    # Diario ≥ umbral → más suave (resetea cada día, se sube con «sube» en 1 clic).
    if d > 0 and gd / d >= PRESUP_DIARIO_FRAC:
        alertas.append(("presupuesto_diario_alto",
                        "Vas por el %d%% del tope de gasto de HOY ($%.0f de $%.0f). Si lo topas, lo no-esencial "
                        "se pausa hasta mañana (o súbelo con «sube»)." % (round(100 * gd / d), gd, d)))

    # SALDO PREPAGO de Anthropic (≠ tope local): avisa ANTES de que llegue a 0 (susto 17-jul, que el
    # tope local NO cazó porque tenía margen). Solo si {{TITULAR}} registró una recarga (cost_guard.py
    # recarga N); si no, silencio (sin baseline no inventamos). Dos escalones: 90% urgente, 75% aviso.
    # Claves anti-flapping sin el % volátil (el número va en el texto).
    try:
        sp = cost_guard.saldo_prepago()
    except Exception:
        sp = None
    if sp:
        info["saldo_prepago"] = sp
        # 26-sep-26: la alarma mira el gasto de la API de ANTHROPIC (frac_firme), no el bruto con la
        # suscripción y otros proveedores. La señal real se lee SIEMPRE (es un fichero, no una
        # llamada) y alimenta `observar_credito`, que aprende cuánto recargó {{TITULAR}} entre dos cortes.
        # Antes: «API con crédito» = «el estimador está mal» → baseline a hoy en cada pasada (189
        # veces) y el aviso previo nunca llegaba. Ahora solo se sube el importe si el gasto ya lo
        # PASÓ con la API aún viva; entre el 75 % y el 100 % se avisa, que es para lo que existe.
        frac = sp.get("frac_firme", sp["frac"]) if sp.get("fiable", True) else sp["frac"]
        baja = frac >= PREPAGO_FRAC_AVISO
        cok = cost_guard.credito_ok()
        info["credito_probe"] = cok
        try:
            cost_guard.observar_credito(cok)
        except Exception:
            pass
        if cok is True and frac >= 1.0:
            # Gastado más de lo supuesto y la API sigue viva → el importe era mayor. Se sube, sin alarma.
            try:
                cost_guard.resync_baseline_auto()
            except Exception:
                pass
        elif cok is False:
            # SEÑAL REAL: la API rechaza por falta de crédito (400 'Credit balance too low'). Esta sí.
            alertas.append(("saldo_prepago_urgente",
                            "🔴 La API de Anthropic responde SIN CRÉDITO: el prepago se agotó y el núcleo "
                            "se para hasta que recargues (https://platform.claude.com/settings/billing). Lo detecté yo "
                            "solo, no por una cuenta a mano."))
        elif baja and sp.get("fiable", True):
            # Con la API viva (cok True) o muda (None): el estimador es fiable y dice que queda poco.
            # cok is None → la sonda real no confirma. Se avisa SOLO si el estimador es fiable, o
            # sea si la mayor parte del gasto está etiquetada como API medida. Antes se avisaba
            # siempre, y como el prepago solo lo consume la API pero el estimador sumaba también
            # la cuota de la suscripción, la cifra se volvía imposible (restante −29,72 $ el
            # 25-jul) y el mismo aviso salía en cada pasada hasta escalar la deuda a rojo.
            horas = sp.get("horas_restantes")
            alertas.append(("saldo_prepago_aviso",
                            "Queda poco prepago de la API de Anthropic: gastado el %.0f%% de la última "
                            "recarga%s. Cuando se acabe, el lazo se para. Recarga en "
                            "https://platform.claude.com/settings/billing"
                            % (100 * frac, (", a este ritmo se acaba en ~%.0f h" % horas)
                               if isinstance(horas, (int, float)) else "")))
        elif baja and cok is None:
            # No es que el saldo esté bajo: es que NO SE PUEDE SABER, y decir «bajo» sería afirmar
            # lo que no se ha verificado. Clave propia para que no se confunda con la alarma real.
            info["saldo_sin_señal"] = True
            alertas.append(("saldo_prepago_sin_señal",
                            "No puedo saber cuánto prepago de la API queda. La cuenta estimada dice "
                            "%.2f $ gastados de %.2f $, pero %.2f $ de esos son de canal sin "
                            "identificar (parte va por la cuota del plan, que NO sale del prepago), "
                            "así que el número no vale. La comprobación real contra la API tampoco "
                            "responde. Si quieres el dato firme está en console.anthropic.com; esto "
                            "no es una alarma de saldo, es que me falta la señal."
                            % (sp["gastado"], sp["monto"], sp["sin_etiquetar"])))
    return alertas, info


def _check_vigilantes():
    """B — WATCH-THE-WATCHER: ¿sigue corriendo el OTRO vigilante? healthcheck comprueba que el vigía
    dejó un latido reciente (state/vigia/last_run.json, que ahora escribe al final de su ciclo). Si el
    vigía lleva > VIGIA_STALE_MIN sin correr, nadie estaría cazando crash-loops del bot → alerta humana.
    (El vigía hace el chequeo SIMÉTRICO de healthcheck por su lado.) Sin baseline aún (fichero ausente)
    → no se dispara, igual que el dead-man. Caveat: si AMBOS mueren a la vez, ninguno avisa — lo mitiga
    launchd (los relanza) y El Observatorio (muestra el last_check). Reusa _edad_horas_ts. Fail-soft."""
    alertas, info = [], {}
    p = os.path.join(STATE, "vigia", "last_run.json")
    try:
        ts = json.load(open(p, encoding="utf-8")).get("ts")
    except FileNotFoundError:
        info["vigia"] = "sin baseline aún"
        return alertas, info                       # sin señal previa → no disparamos (igual que el dead-man)
    except Exception as e:
        info["error"] = "%r" % e
        return alertas, info
    edad_h = _edad_horas_ts(ts)
    edad_min = edad_h * 60 if edad_h is not None else None
    info["vigia_edad_min"] = round(edad_min, 1) if edad_min is not None else None
    if edad_min is None or edad_min > VIGIA_STALE_MIN:
        cuando = "no puedo leer su última señal" if edad_min is None else "su última señal fue hace %d min" % int(edad_min)
        alertas.append(("vigia_parado",
                        "El vigía (el que caza fallos del bot y bucles) parece parado (%s). Sin él, un bot "
                        "caído podría pasar inadvertido. Revísalo: launchctl kickstart -k gui/$(id -u)/com.btp.vigia"
                        % cuando))
    return alertas, info


# ── Exposición de contenedores Docker/Colima (seguridad) ────────────────────────────
DOCKER_TIMEOUT_S = 8
# IPs de host SEGURAS para publicar un puerto: loopback y la tailnet privada (100.x = Tailscale CGNAT).
# Cualquier otra (0.0.0.0, ::, o un IP de LAN) = el puerto queda expuesto más allá del Mac/tailnet.
_HOST_IP_SEGURO = ("127.0.0.1", "::1", "localhost", "100.")


def _docker_arriba():
    """¿Hay un daemon Docker/Colima respondiendo? Fail-soft con timeout: si el binario no está, o
    Colima está apagado, o el comando tarda, devolvemos False — eso NO es un fallo, es lo normal
    (el Mac mini suele tener Colima parado). Nunca lanza."""
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, timeout=DOCKER_TIMEOUT_S)
        return r.returncode == 0
    except Exception:
        return False


def _host_ip_expuesta(ip):
    """True si el IP de host de un binding PUBLICADO queda expuesto más allá de loopback/tailnet.
    0.0.0.0 y :: (wildcard) → sí; 127.0.0.1/::1/localhost → no; 100.x (Tailscale) → no."""
    ip = ip.strip().strip("[]")     # IPv6 puede venir entre corchetes: [::]
    if not ip:
        return False                # sin IP de host explícito → no lo tratamos como exposición
    for seguro in _HOST_IP_SEGURO:
        if ip == seguro or ip.startswith(seguro):
            return False
    return True                     # 0.0.0.0, ::, o cualquier IP no listado


def _bindings_expuestos():
    """Parsea `docker ps` y devuelve [(contenedor, "binding"), …] de puertos publicados a un IP de
    host expuesto. Devuelve None si Docker está arriba pero `ps` falla (señal de error, NO de "todo
    bien"). El formato de la columna Ports es, por entrada: 'IP:HOSTPORT->CONTPORT/proto' (publicado)
    o 'CONTPORT/proto' (solo expuesto, sin binding de host → no es exposición de red). Fail-soft."""
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
                           capture_output=True, text=True, timeout=DOCKER_TIMEOUT_S)
        if r.returncode != 0:
            return None
    except Exception:
        return None
    expuestos = []
    for ln in r.stdout.splitlines():
        if "\t" not in ln:
            continue
        nombre, ports = ln.split("\t", 1)
        for entry in ports.split(","):
            entry = entry.strip()
            if "->" not in entry:
                continue            # puerto expuesto pero NO publicado (sin host binding) → no es red
            host = entry.split("->", 1)[0]   # 'IP:PUERTO' (la IP puede ser IPv4, [::] o ::)
            if ":" not in host:
                continue
            ip = host.rsplit(":", 1)[0]      # todo menos el último :PUERTO
            if _host_ip_expuesta(ip):
                expuestos.append((nombre.strip(), entry))
    return expuestos


def _check_docker_expuesto():
    """SEGURIDAD — caza contenedores Docker/Colima que publican un puerto a 0.0.0.0 (o a cualquier
    interfaz que no sea loopback ni la tailnet 100.x). Docker puede ABRIR agujeros en el firewall al
    bindear a 0.0.0.0; los servicios de Polaris deben quedarse en 127.0.0.1 o en la IP de Tailscale
    (privada). Si Colima/Docker está apagado → no hace nada (no es un fallo). Clave estable POR
    contenedor → respeta el anti-flapping. Hermano operativo de seguridad>privacidad: aviso normal,
    NO código rojo (no amenaza el goal). Devuelve (alertas, info). Fail-soft: nunca lanza."""
    alertas, info = [], {}
    if not _docker_arriba():
        info["docker"] = "apagado"
        return alertas, info
    expuestos = _bindings_expuestos()
    if expuestos is None:
        info["docker"] = "arriba, ps ilegible"
        return alertas, info
    info["docker"] = "arriba"
    info["expuestos"] = ["%s %s" % (n, p) for n, p in expuestos]
    por_cont = {}
    for nombre, entry in expuestos:
        por_cont.setdefault(nombre, []).append(entry)
    for nombre, entries in sorted(por_cont.items()):
        puertos = ", ".join(entries)
        alertas.append(("docker_expuesto:%s" % nombre,
                        "El contenedor Docker «%s» publica un puerto a una interfaz pública (%s), no "
                        "solo a tu Mac ni a la red privada de Tailscale. Docker puede saltarse el "
                        "firewall así. Conviene atarlo a 127.0.0.1 o a la IP 100.x de Tailscale."
                        % (nombre, puertos)))
    return alertas, info


# ---------------------------------------------------------------------------
# DRIFT DE DAEMONS (item #2): compara las TRES capas de cada daemon —DEFINIDO (plist en el repo),
# INSTALADO (~/Library/LaunchAgents) y CARGADO (launchctl list)— cruzadas con REGISTRO.json (la
# fuente única declarada). DETERMINISTA y NO auto-arregla: solo SURFACEA. Cubre justo lo que la
# vigía de roster (B1) no puede ver: su roster SALE de los plists instalados, así que un daemon
# CARGADO-sin-instalar (fantasma) o un plist instalado sin fuente en el repo le son invisibles.
# El estado normal "definido en el repo pero no desplegado aquí" (rutinas gated con RunAtLoad=false,
# o daemons Air-only del repo unión) NO alerta.
# ---------------------------------------------------------------------------
def _labels_de_plists(directorio):
    """Set de Labels leídos de <directorio>/com.btp.*.plist. Fail-soft: set() si no se puede leer."""
    import glob
    import plistlib
    out = set()
    try:
        rutas = glob.glob(os.path.join(directorio, "com.btp.*.plist"))
    except Exception:
        return out
    for p in rutas:
        try:
            with open(p, "rb") as fh:
                lbl = plistlib.load(fh).get("Label")
            if lbl:
                out.add(lbl)
        except Exception:
            # plist ilegible → último recurso: el nombre de fichero (com.btp.X.plist → com.btp.X)
            base = os.path.basename(p)
            if base.endswith(".plist"):
                out.add(base[:-6])
    return out


def _registro_labels():
    """Set de Labels declarados en tools/launchd/REGISTRO.json (fuente única). None si ilegible."""
    ruta = os.path.join(REPO, "tools", "launchd", "REGISTRO.json")
    try:
        with open(ruta, encoding="utf-8") as fh:
            data = json.load(fh)
        return set((data.get("daemons") or {}).keys())
    except Exception:
        return None


def _drift_rutas_rotas(inst_dir):
    # item #2: escanea los plist INSTALADOS en <inst_dir> y devuelve (alertas, rotas, ilegibles).
    # rutas rotas = WorkingDirectory / interprete+script de ProgramArguments / DIR de los logs que NO
    # existen en ESTA maquina (tipico: plist del repo-union instalado sin reescribir el home -> exit 78
    # al disparar). Chequeo ESTATICO: caza el fallo ANTES del primer disparo (aun no hay exit!=0 que lo
    # delate). ilegible = plist instalado pero con XML que plistlib no parsea (inauditable). Solo
    # com.btp.*, respeta _ROSTER_SKIP y _parked(). Fail-soft por plist. Nunca lee el repo: si inst_dir
    # no tiene nada instalado, no devuelve nada (sin el bucle de rutas del portatil de maquina clonada).
    import glob as _glob
    import plistlib as _plist
    alertas, rotas, ilegibles = [], {}, []
    parked = _parked()
    for p in sorted(_glob.glob(os.path.join(inst_dir, "com.btp.*.plist"))):
        base = os.path.basename(p)
        lbl = base[:-6] if base.endswith(".plist") else base
        if lbl in _ROSTER_SKIP or lbl in parked:
            continue
        try:
            with open(p, "rb") as fh:
                d = _plist.load(fh)
        except Exception as e:
            ilegibles.append(lbl)
            alertas.append(("drift_ilegible_%s" % lbl,
                            "Plist ilegible: %s esta instalado en ~/Library/LaunchAgents pero su XML no se "
                            "puede parsear (%s). Esta cargado pero es INAUDITABLE - reinstalalo limpio desde "
                            "el repo: tools/activar_daemon.py %s --reemplaza." % (lbl, type(e).__name__, lbl)))
            continue
        faltan = []
        wd = d.get("WorkingDirectory")
        if isinstance(wd, str) and wd.startswith("/") and not os.path.isdir(wd):
            faltan.append("WorkingDirectory=%s" % wd)
        for arg in (d.get("ProgramArguments") or []):
            if isinstance(arg, str) and arg.startswith("/") and not os.path.exists(arg):
                faltan.append(arg)
        for k in ("StandardOutPath", "StandardErrorPath"):
            v = d.get(k)
            if isinstance(v, str) and v.startswith("/") and not os.path.isdir(os.path.dirname(v)):
                faltan.append("%s->%s/" % (k, os.path.dirname(v)))
        if faltan:
            rotas[lbl] = faltan
            alertas.append(("drift_rutas_%s" % lbl,
                            "Daemon con rutas rotas: %s esta instalado pero apunta a rutas que NO existen en "
                            "esta maquina (%s). Arrancara en error (exit 78) al dispararse - suele ser un plist "
                            "del repo-union instalado sin reescribir el home. Reinstalalo: "
                            "tools/activar_daemon.py %s --reemplaza." % (lbl, "; ".join(faltan), lbl)))
    return alertas, rotas, ilegibles


def _drift_contenido_plists(inst_dir, repo_dir=None):
    """¿El plist INSTALADO dice lo mismo que el del repo? (20-sep-2026)

    EL AGUJERO QUE TAPA: el drift que había comparaba NOMBRES (labels) entre cuatro capas, y
    `_drift_rutas_rotas` abre el plist instalado pero solo pregunta «¿existe esta ruta?».
    `EnvironmentVariables` no lo miraba NADIE. Así vivió tres meses, sin un solo aviso, que el
    plist activo de `com.btp.correo-imap` exportara `BTP_GMAIL_USER=<la cuenta secundaria>` y
    corriera `once`: el correo de la cuenta PRINCIPAL no llegaba a `buzon.json`, que es el fichero
    que leen diez consumidores, y tres de ellos no miran ningún fichero por cuenta.

    Compara solo dos campos, los que deciden QUÉ se ejecuta y CON QUÉ entorno:
    `ProgramArguments` y `EnvironmentVariables`. `PATH` se ignora a propósito (ruido de máquina).
    Una clave ESTABLE para todos (`plist_drift`), no una por label: con números o nombres dentro
    la clave cambia sola y el libro de deudas se llena de entradas gemelas (lección del 19-sep).
    Fail-soft por fichero: un plist ilegible ya lo cuenta `_drift_rutas_rotas`."""
    import glob as _glob
    import plistlib as _plist
    repo_dir = repo_dir or os.path.join(REPO, "tools", "launchd")
    parked = _parked()
    difs = {}
    for p in sorted(_glob.glob(os.path.join(repo_dir, "com.btp.*.plist"))):
        base = os.path.basename(p)
        lbl = base[:-6]
        if lbl in _ROSTER_SKIP or lbl in parked:
            continue
        instalado = os.path.join(inst_dir, base)
        if not os.path.exists(instalado):
            continue                      # que falte es otro chequeo (drift por labels)
        try:
            with open(instalado, "rb") as fh:
                act = _plist.load(fh)
            with open(p, "rb") as fh:
                rep = _plist.load(fh)
        except Exception:
            continue                      # ilegible: ya tiene su propia alerta
        campos = []
        if (act.get("ProgramArguments") or []) != (rep.get("ProgramArguments") or []):
            campos.append("ProgramArguments")
        ea = {k: v for k, v in (act.get("EnvironmentVariables") or {}).items() if k != "PATH"}
        er = {k: v for k, v in (rep.get("EnvironmentVariables") or {}).items() if k != "PATH"}
        if ea != er:
            campos.append("EnvironmentVariables")
        if campos:
            difs[lbl] = campos
    if not difs:
        return [], {}
    detalle = ", ".join("%s (%s)" % (l, "+".join(c)) for l, c in sorted(difs.items()))
    return [("plist_drift",
             "🔀 %d daemon(s) corren algo distinto de lo que dice el repo: %s. Lo que manda hoy es "
             "el fichero instalado; si alguien reinstala desde el repo, cambia el comportamiento "
             "sin que nadie lo pida. Compara y decide cuál es el bueno." % (len(difs), detalle))], difs


def _check_drift_daemons():
    """Chequeo DETERMINISTA de drift entre lo DECLARADO (REGISTRO.json + plist del repo) y lo
    DESPLEGADO (instalado + cargado) en ESTA máquina. SURFACEA, no arregla. Respeta HALT por la vía
    normal (todo sale por salida.report_to_titular aguas abajo). Tres clases de alerta, inequívocas y
    ciegas al ruido de las rutinas gated:
      · FANTASMA: cargado en launchctl pero SIN plist instalado aquí (invisible para B1).
      · NO REGISTRADO: instalado o cargado pero ausente de REGISTRO.json (se escapó del registro).
      · INSTALADO HUÉRFANO: instalado pero sin plist fuente en el repo (no se puede auditar/regenerar).
    NO alerta el estado normal 'definido en el repo pero no desplegado aquí' (RunAtLoad=false a la
    espera del gate, o daemons Air-only del repo unión). Devuelve (alertas, info). Fail-soft: si no
    puede leer alguna capa, informa y NO inventa drift."""
    alertas, info = [], {}
    cargado_map = _launchctl_estado()
    if cargado_map is None:
        info["error"] = "launchctl ilegible"
        return alertas, info
    registrados = _registro_labels()
    if registrados is None:
        info["error"] = "REGISTRO.json ilegible"
        return alertas, info

    # _ROSTER_SKIP fuera de las CUATRO capas por igual (tienen check dedicado en 3b/3c): si no, saldrían
    # como 'registrados_sin_rastro' espurios al quitarlos solo de definidos/instalados/cargados.
    registrados = registrados - _ROSTER_SKIP
    definidos = _labels_de_plists(os.path.join(REPO, "tools", "launchd")) - _ROSTER_SKIP
    instalados = _labels_de_plists(os.path.expanduser("~/Library/LaunchAgents")) - _ROSTER_SKIP
    cargados = set(cargado_map) - _ROSTER_SKIP

    fantasmas = sorted(cargados - instalados)
    no_registrados = sorted((instalados | cargados) - registrados)
    huerfanos = sorted(instalados - definidos)
    # registrado pero sin rastro físico ni cargado → info, NO alerta (puede ser placeholder legítimo).
    dangling = sorted(registrados - definidos - instalados - cargados)

    info.update({
        "n_registrados": len(registrados), "n_definidos": len(definidos),
        "n_instalados": len(instalados), "n_cargados": len(cargados),
        "fantasmas": fantasmas, "no_registrados": no_registrados,
        "instalados_huerfanos": huerfanos, "registrados_sin_rastro": dangling,
    })

    for lbl in fantasmas:
        alertas.append(("drift_fantasma_%s" % lbl,
                        "Daemon fantasma: %s está CARGADO pero no tiene plist instalado en "
                        "~/Library/LaunchAgents. Corre desde algo que ya no está en su sitio — "
                        "revísalo (launchctl list %s) y decide descargarlo o reinstalar su plist." % (lbl, lbl)))
    for lbl in no_registrados:
        alertas.append(("drift_norotu_%s" % lbl,
                        "Daemon fuera del registro: %s está instalado/cargado pero NO figura en "
                        "REGISTRO.json. Se escapó del inventario — regístralo (o retíralo si sobra)." % lbl))
    for lbl in huerfanos:
        alertas.append(("drift_huerfano_%s" % lbl,
                        "Plist instalado sin fuente: %s está en ~/Library/LaunchAgents pero no hay "
                        "com.btp.*.plist que lo defina en el repo. No se puede auditar ni regenerar — "
                        "recupera su fuente o desinstálalo." % lbl))
    # RUTAS ROTAS / ILEGIBLE (item #2): un plist instalado puede apuntar a un home inexistente en ESTA
    # maquina (repo-union sin reescribir -> exit 78) o estar malformado. Las capas de arriba no lo ven:
    # el plist ESTA instalado y registrado, solo arrancara roto. Chequeo estatico -> caza antes del 1er
    # disparo. Solo lo INSTALADO aqui (nunca el repo). Fail-soft: nunca rompe el check.
    try:
        rr_alertas, rr_rotas, rr_ileg = _drift_rutas_rotas(os.path.expanduser("~/Library/LaunchAgents"))
        alertas.extend(rr_alertas)
        info["rutas_rotas"] = rr_rotas
        info["ilegibles"] = rr_ileg
    except Exception as e:
        info["rutas_rotas_error"] = "%r" % e
    return alertas, info


def run():
    # Observabilidad (pieza 8): captura ts_ini para calcular duración al final.
    import time as _time
    _obs_ts_ini = _time.time()

    _ensure()
    alertas = []
    chk = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}

    # 1. cola viva + reap de atascados
    reaped = q.reap_stuck(STUCK_SEG)
    st = q.get_status()
    chk["cola"] = st
    chk["reaped"] = reaped
    if reaped:
        # clave estable: "reap" (la cantidad varía pero el hecho de que haya reaped es la condición)
        alertas.append(("cola_reap",
                        "Rescaté %d tarea(s) del lazo que se habían quedado colgadas (ya reencauzadas)." % reaped))

    # 1b. AUTO-RECOVER (B2): re-encola jobs caídos a failed/ por schema-desconocido (skew de versión
    #     tras fusionar/recargar; NO toca schema-invalido, plantados de seguridad permanentes). Con
    #     GUARDA ANTI-BUCLE: si un job rebota RECOVER_ESCALA veces, escala a {{TITULAR}} en vez de re-encolar
    #     en silencio para siempre. cola_recover_schema = operativo (auto-curado); cola_recover_atascado
    #     = humano (necesita fusionar el consumidor).
    try:
        rec_alertas, rec_info = _auto_recover_schema()
        chk["recover_schema"] = rec_info
        alertas.extend(rec_alertas)
    except Exception as e:
        chk["recover_schema_error"] = "%r" % e

    # 2. heartbeat del dispatcher. El latido rancio alerta A SECAS: antes exigía `pending>0`, y esa
    #    condición abría la ventana ciega que más duele — cola VACÍA + dispatcher en ciclo
    #    arranque-y-salir (los `exit 0` tempranos de btp_dispatcher.sh, con KeepAlive relanzando cada
    #    10 s) = launchd con status 0, cero pending, cero alerta. El primer mensaje de {{TITULAR}} por
    #    Telegram entraba a una cola sin ejecutor durante horas. El latido es la señal de VIDA del
    #    motor; no depende de que haya trabajo (el bucle late en cada vuelta, esté vacía o no).
    #    Dos exenciones legítimas, para no gritar por algo deliberado:
    #      · HALT activo → el dispatcher rompe el bucle a propósito (es el kill-switch funcionando).
    #      · el daemon NO está cargado → el lazo está apagado a conciencia (de eso ya avisa el roster).
    hb_edad = None
    try:
        hb_edad = time.time() - os.path.getmtime(HEARTBEAT)
    except OSError:
        pass
    chk["heartbeat_edad_seg"] = hb_edad
    hb_rancio = hb_edad is None or hb_edad > HEARTBEAT_STALE_SEG
    hb_exento = _dispatcher_apagado_a_proposito()
    chk["heartbeat_exento"] = hb_exento
    if hb_rancio and not hb_exento:
        # clave estable: "dispatcher_parado" (los minutos exactos son volátiles, no van en la clave)
        estado_txt = "no da señal" if hb_edad is None else "su última señal fue hace %d min" % int(hb_edad / 60)
        pend = st.get("pending", 0)
        cola_txt = ("y hay %d tarea(s) esperando" % pend) if pend else \
                   "y la cola está vacía, así que lo que le mandes ahora no lo ejecutaría nadie"
        alertas.append(("dispatcher_parado",
                        "El motor que ejecuta las tareas del lazo parece parado (%s) %s."
                        % (estado_txt, cola_txt)))

    # 3. disco
    try:
        libre_gb = shutil.disk_usage(REPO).free / (1024 ** 3)
        chk["disco_libre_gb"] = round(libre_gb, 2)
        if libre_gb < DISCO_MIN_GB:
            # clave estable: "disco_bajo" (los GB exactos son volátiles, no van en la clave)
            alertas.append(("disco_bajo",
                            "Disco bajo: %.1f GB libres. Conviene liberar espacio." % libre_gb))
    except Exception:
        pass

    # 3b. SERVICIOS Y CAMINOS VIVOS: ¿la web responde de verdad? ¿el móvil llega?
    #     Autofix acotado (kickstart × 1) antes de avisar. Distingue VPN de daemon caído.
    try:
        sv_alertas, sv_info = _check_servicios_vivos()
        chk["servicios_vivos"] = sv_info
        alertas.extend(sv_alertas)
    except Exception as e:
        chk["servicios_vivos_error"] = "%r" % e   # log técnico, nunca para {{TITULAR}}

    # 3c. LA PUERTA (gateway del borde, la que sirve Vivir): si el daemon se cae, {{TITULAR}} habla y no
    #     contesta nadie. Autofix kickstart × 1; si no revive → aviso. Cierra el hueco de que un
    #     fallo de la puerta tenga que notarlo ella.
    try:
        gw_alertas, gw_info = _check_gateway_vivo()
        chk["gateway"] = gw_info
        alertas.extend(gw_alertas)
    except Exception as e:
        chk["gateway_error"] = "%r" % e           # log técnico, nunca para {{TITULAR}}

    # 3d. ROSTER DE DAEMONS (B1): caza un daemon KeepAlive que se cayó del arranque sin avisar.
    #     Autofix kickstart × 1; revive → traza operativa; no revive → alerta humana. Los x-* y
    #     demás interval/calendar no entran (no son KeepAlive). Cierra la clase del fallo silencioso.
    try:
        rd_alertas, rd_info = _check_roster_daemons()
        chk["roster_daemons"] = rd_info
        alertas.extend(rd_alertas)
    except Exception as e:
        chk["roster_daemons_error"] = "%r" % e

    # 3e. BUCLES DE ESPERA COLGADOS (14-sep-26): un `until`/`while … sleep` sin tope, colgado de
    #     una sesión de Claude Code, se queda dando vueltas horas y bloquea el worktree (ver
    #     tools/bucles_colgados.py y feedback-bucles-espera-con-tope). Activo por defecto (para
    #     los que superen el umbral); BTP_BUCLE_SOLO_AVISO=1 lo vuelve a solo-aviso.
    try:
        bc_alertas, bc_info = bucles_colgados.run()
        chk["bucles_colgados"] = bc_info
        alertas.extend(bc_alertas)
    except Exception as e:
        chk["bucles_colgados_error"] = "%r" % e

    # 3e-bis. CHROME HEADLESS HUÉRFANO (26-sep-26): tres Chrome de un script de captura muerto
    #     pasaron ~27 h al 100 % de CPU y todo Polaris iba lento sin que nadie avisara. Mismo
    #     esquema que los bucles: activo por defecto, BTP_BUCLE_SOLO_AVISO=1 lo deja en aviso.
    try:
        ch_alertas, ch_info = bucles_colgados.run_chrome()
        chk["chrome_huerfano"] = ch_info
        alertas.extend(ch_alertas)
    except Exception as e:
        chk["chrome_huerfano_error"] = "%r" % e

    # 3d-quater. DRIFT DE DAEMONS (item #2): las tres capas DEFINIDO/INSTALADO/CARGADO cruzadas con
    #     REGISTRO.json. Determinista, SURFACEA y NO auto-arregla. Cubre lo que B1 no ve (roster salido
    #     de los plists instalados): fantasmas cargados-sin-instalar, escapados del registro, y plists
    #     instalados sin fuente en el repo. Fail-soft: nunca rompe el ciclo.
    try:
        dr_alertas, dr_info = _check_drift_daemons()
        chk["drift_daemons"] = dr_info
        alertas.extend(dr_alertas)
    except Exception as e:
        chk["drift_daemons_error"] = "%r" % e

    # 3d-ter. DRIFT POR CONTENIDO (20-sep-26): el de arriba compara NOMBRES. Este compara lo que
    #         de verdad se ejecuta —argumentos y entorno— entre el plist instalado y el del repo.
    #         Nació del correo: el plist activo exportaba otra cuenta y nadie lo vio en 3 meses.
    try:
        pd_alertas, pd_info = _drift_contenido_plists(os.path.expanduser("~/Library/LaunchAgents"))
        chk["plist_drift"] = pd_info
        alertas.extend(pd_alertas)
    except Exception as e:
        chk["plist_drift_error"] = "%r" % e

    # 3d-bis. GUARDIAN ANTI-MORDAZA (capa 3, 14-jul-2026). Si la boca esta rota, avisar por la via
    #         normal seria inutil (la mordaza tambien se lo tragaria) -> se escala por
    #         alerta_critica(), que NO pasa por send(). Sello diario: no repetir el grito.
    try:
        bm_alertas, bm_info = _check_boca_muda()
        chk["boca_muda"] = bm_info
        if bm_alertas:
            sello = os.path.join(STATE, "hc", "boca_muda-%s.flag"
                                 % __import__("datetime").datetime.now().strftime("%Y-%m-%d"))
            if not os.path.exists(sello):
                try:
                    import salida as _sal
                    for _k, _txt in bm_alertas:
                        _sal.alerta_critica("\U0001f6a8 " + _txt)
                    os.makedirs(os.path.dirname(sello), exist_ok=True)
                    open(sello, "w").close()
                except Exception as e2:
                    chk["boca_muda_escalada_error"] = "%r" % e2
        alertas.extend(bm_alertas)
    except Exception as e:
        chk["boca_muda_error"] = "%r" % e

    # 3d-quater. SONDA DEL SILENCIO (21-sep-2026). Lo que arranco y no dejo obra: una tarea
    #            programada que murio a los segundos, o un job parado en processing. El resto de
    #            chequeos miran lo que SI pasa; este mira el hueco. Solo avisa de lo RECIENTE
    #            (48 h): los caidos viejos ya cuelgan del Tablero y repetirlos seria ruido.
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import sonda_silencio as _ss
        _sil = _ss.revisa()
        chk["silencio"] = {"tareas_mudas": len(_sil["tareas_mudas"]),
                           "jobs_atascados": len(_sil["jobs_atascados"])}
        for _t in _sil["tareas_mudas"]:
            if _t["hace_h"] <= 48:
                alertas.append(("silencio_tarea_muda",
                                "tarea programada «%s» arranco y murio en %.0f s hace %.0f h: "
                                "su encargo NO se hizo y nadie lo dijo"
                                % (_t["tarea"], _t["vivio_s"], _t["hace_h"])))
        for _j in _sil["jobs_atascados"]:
            alertas.append(("silencio_job_atascado",
                            "job parado en processing %.0f h: %s" % (_j["horas"], _j["job"])))
    except Exception as e:
        chk["silencio_error"] = "%r" % e

    # 3d-ter. CANARIO DEL CEREBRO (14-jul-2026). El unico sitio del sistema que EJECUTA de verdad el
    #         binario: si el lazo lo ha perdido, las tareas clinicas se paran y la cache de salud no
    #         se entera. Va como chequeo propio a proposito, para NO caer bajo la supresion por saldo
    #         (`fallando_suprimido_por_saldo`), que si no lo enmascararia justo cuando mas duele.
    try:
        ce_alertas, ce_info = _check_cerebro_alcanzable()
        chk["cerebro_alcanzable"] = ce_info
        alertas.extend(ce_alertas)
    except Exception as e:
        chk["cerebro_alcanzable_error"] = "%r" % e

    # 3e-bis. LLAVERO (17-sep-26): un Llavero bloqueado apaga el lazo por trozos y cada trozo
    #         abre su propio hallazgo, sin que ninguno nombre la causa. Sonda barata, sin leer
    #         ningún secreto: solo el código de salida de `security`.
    try:
        lv_alertas, lv_info = _check_llavero()
        chk["llavero"] = lv_info
        alertas.extend(lv_alertas)
    except Exception as e:
        chk["llavero_error"] = "%r" % e

    # 3e-quater. RECURSOS (20-sep-26): un proceso se comió los 16 GB del mini y la casa base se
    #         arrastró media hora hasta que hubo que reiniciarla. Lo notó {{TITULAR}}, no el sistema.
    try:
        rc_alertas, rc_info = _check_recursos()
        chk["recursos"] = rc_info
        alertas.extend(rc_alertas)
    except Exception as e:
        chk["recursos_error"] = "%r" % e       # log técnico, nunca para {{TITULAR}}

    # 3e-quinquies. CPU (26-sep-26): un día entero con la carga a 46-82 por Chrome huérfanos y
    #         nadie avisó; _check_recursos solo mira memoria.
    try:
        cpu_alertas, cpu_info = _check_cpu()
        chk["cpu"] = cpu_info
        alertas.extend(cpu_alertas)
    except Exception as e:
        chk["cpu_error"] = "%r" % e

    # 3e-ter. SALDO DE API (18-sep-26): sin saldo, el lazo se para entero y los daemons siguen
    #         saliendo en verde. La API lo dice con todas las letras en sus logs; esto lo lee.
    try:
        sa_alertas, sa_info = _check_saldo_api()
        chk["saldo_api"] = sa_info
        alertas.extend(sa_alertas)
    except Exception as e:
        chk["saldo_api_error"] = "%r" % e

    # 3e. PRESUPUESTO (C): burn-rate proactivo — avisa al acercarse al tope mensual/diario y proyecta
    #     si el ritmo lo va a topar antes de fin de mes. Cierra el hueco "nadie avisó hasta agotarlo".
    try:
        pr_alertas, pr_info = _check_presupuesto()
        chk["presupuesto"] = pr_info
        alertas.extend(pr_alertas)
    except Exception as e:
        chk["presupuesto_error"] = "%r" % e        # log técnico, nunca para {{TITULAR}}

    # 3e-bis. JOBS CAÍDOS: los descartes del dequeue iban a failed/ sin que nadie lo dijera.
    try:
        jc_alertas, jc_info = _check_jobs_caidos()
        chk["jobs_caidos"] = jc_info
        alertas.extend(jc_alertas)
    except Exception as e:
        chk["jobs_caidos_error"] = "%r" % e

    # 3f. WATCH-THE-WATCHER (B): ¿el vigía sigue vivo? Si lleva rato sin correr, nadie caza los
    #     crash-loops del bot. (El vigía hace el chequeo simétrico de healthcheck por su lado.)
    try:
        vg_alertas, vg_info = _check_vigilantes()
        chk["vigilantes"] = vg_info
        alertas.extend(vg_alertas)
    except Exception as e:
        chk["vigilantes_error"] = "%r" % e         # log técnico, nunca para {{TITULAR}}

    # 3g. SALUD DE LAS IAs: ¿responde cada IA del gabinete? Si una CAE (DOWN) avisa a {{TITULAR}}
    #     (las de evidencia tras Cloudflare = DEGRADED esperado, no alertan). Throttle ~2h.
    #     Cierra el "saber si una IA se cae" que pidió {{TITULAR}} (hermano operativo del código rojo).
    try:
        ia_alertas, ia_info = _check_ias()
        chk["ias"] = ia_info
        alertas.extend(ia_alertas)
    except Exception as e:
        chk["ias_error"] = "%r" % e                # log técnico, nunca para {{TITULAR}}

    # 3g-bis. ¿Responde cada LLM del enrutador, y a cuál se le acabó el SALDO? (19-sep-2026)
    # El GET de la sonda de arriba responde 200 con el saldo a cero; esto hace la llamada real.
    try:
        llm_alertas, llm_info = _check_llms()
        chk["llms"] = llm_info
        alertas.extend(llm_alertas)
    except Exception as e:
        chk["llms_error"] = "%r" % e

    # 3h. EXPOSICIÓN DOCKER/COLIMA (seguridad): ¿algún contenedor publica un puerto a 0.0.0.0 (más
    #     allá de loopback / la tailnet 100.x)? Docker puede abrir agujeros en el firewall. Si Colima
    #     está apagado, no hace nada. Hermano operativo de seguridad>privacidad → aviso humano normal.
    try:
        dk_alertas, dk_info = _check_docker_expuesto()
        chk["docker_expuesto"] = dk_info
        alertas.extend(dk_alertas)
    except Exception as e:
        chk["docker_expuesto_error"] = "%r" % e    # log técnico, nunca para {{TITULAR}}

    # 3i. MONITOR DE CORREO (pieza 4, 2/7/26): SMTP/IMAP alcanzables, App Passwords válidas
    #     (login test por cuenta) y rebotes recientes (mailer-daemon). Throttle ~1h. Cierra
    #     el hueco de que la fontanería del correo no estaba vigilada (solo su contenido).
    try:
        co_alertas, co_info = _check_correo_salud()
        chk["correo_salud"] = co_info
        alertas.extend(co_alertas)
    except Exception as e:
        chk["correo_salud_error"] = "%r" % e       # log técnico, nunca para {{TITULAR}}

    # 3j. PERMISO DE ESCRITURA DE DRIVE (11-sep-2026): la lectura no caduca y lo tapaba; el
    #     token de escritura sí, y solo se notaba al primer write. Throttle ~6h.
    try:
        dr_alertas, dr_info = _check_drive_oauth()
        chk["drive_oauth"] = dr_info
        alertas.extend(dr_alertas)
    except Exception as e:
        chk["drive_oauth_error"] = "%r" % e        # log técnico, nunca para {{TITULAR}}

    # 3k. CI DEL REPO PÚBLICO (22-sep-2026): estuvo 6 veces rojo sin que nadie lo viera; el
    #     aviso solo llegaba por correo de GitHub. Throttle ~1h.
    try:
        ci_alertas, ci_info = _check_ci_publico()
        chk["ci_publico"] = ci_info
        alertas.extend(ci_alertas)
    except Exception as e:
        chk["ci_publico_error"] = "%r" % e         # log técnico, nunca para {{TITULAR}}

    # 4. Telegram configurado (en SECO, sin enviar nada)
    seco = salida.report_to_titular("healthcheck", dry=True)
    chk["telegram_ok"] = not seco.get("blocked", True)
    if seco.get("blocked"):
        alertas.append(("telegram_no_disponible",
                        "El canal de Telegram no está disponible: %s" % seco.get("reason")))

    # 4b. FRESCURA DE FUENTES (dead-man de contenido): HOY.md por su fecha DECLARADA (no
    #     mtime) + heartbeats de los agentes diarios. Reutiliza el vigía seguimiento.py.
    #     La ausencia de señal NUNCA se reporta como buena noticia.
    try:
        import seguimiento
        fav = seguimiento.frescura(seguimiento.load_cumbre())
        chk["frescura_avisos"] = fav
        _frescura_fallos_reset()            # el chequeo corrió → ceramos el contador de fallos
        # Las alertas de seguimiento son strings con fechas/días → envolverlas con clave estable.
        # La clave se deriva del prefijo del texto (sin los números) para que sea estable.
        alertas.extend(_alertas_frescura(fav))   # sin DNS, los de agente van a red_sin_dns
    except Exception as e:
        # "No pude comprobar" es fontanería, NO un problema confirmado, y este import se hace en un
        # proceso nuevo cada ciclo → un fallo TRANSITORIO (p.ej. el import coincide con una operación
        # de git que reescribe tools/seguimiento.py con rename atómico → ModuleNotFoundError puntual)
        # no debe llegar a {{TITULAR}}. Solo la avisamos si PERSISTE (≥ N ciclos ≈ 1,5 h); un hipo se queda
        # en el log y el ciclo siguiente lo recupera. Fix 3/7 (ModuleNotFoundError('seguimiento')).
        chk["frescura_error"] = "%r" % e   # detalle técnico SIEMPRE al log
        n_fallos = _frescura_fallos_inc()
        chk["frescura_fallos_consecutivos"] = n_fallos
        if n_fallos >= FRESCURA_FALLOS_UMBRAL:
            alertas.append(("frescura_error",
                            "No pude comprobar si tus fuentes (el parte de HOY y los agentes diarios) "
                            "están al día (lleva %d ciclos fallando)." % n_fallos))

    # 4c. SALUD DE VEGA (+ agenda): separa el gate de saldo del error de código y auto-cura estado
    #     corrupto. Tuplas (clave, texto) → respeta el anti-flapping; detalle en chk["daemons"].
    try:
        da_alertas, da_info = _salud_daemons()
        chk["daemons"] = da_info
        alertas.extend(da_alertas)
    except Exception as e:
        chk["daemons_error"] = "%r" % e

    # 4d. WATCHDOG DE RUTINAS NED (C, 2/7/26): auto-mejora / git / comite-medico ≥2 días clavadas
    #     en estado NO sano (fallo/aplazado/critico/centralita) → nadie lo notaría hoy. Hermano de
    #     4c pero SIN exigir frescura (comite-medico es mensual). Aviso humano claro.
    try:
        chk["recuperar_arranque"] = _recuperar_tras_arranque()
    except Exception as e:
        chk["recuperar_arranque_error"] = "%r" % e
    try:
        rn_alertas, rn_info = _check_rutinas_ned()
        chk["rutinas_ned"] = rn_info
        alertas.extend(rn_alertas)
    except Exception as e:
        chk["rutinas_ned_error"] = "%r" % e

    # 4d-bis. GEMELOS DE CRITERIO (20-sep-26): los consejeros espejo de una persona real que
    #     dejaron de aprender. Separa «rancio» (nadie lo mira) de «carril muerto» (no se puede
    #     mirar) — la confusión entre ambos dejó el radar de {{CONTACTO}} 86 días parado en verde.
    try:
        gm_alertas, gm_info = _check_gemelos_rancios()
        chk["gemelos"] = gm_info
        alertas.extend(gm_alertas)
    except Exception as e:
        chk["gemelos_error"] = "%r" % e

    # 4e. CICLO DE AGENTES (14-sep-26): motivo de salida de cada subagente. Sin alertas.
    try:
        chk["ciclo_agentes"] = _ciclo_agentes()
    except Exception as e:
        chk["ciclo_agentes_error"] = "%r" % e

    # 5. DEAD-MAN — antes, la presencia por Claude Code (cola del hook presencia_cc, 11-sep-26)
    try:
        chk["presencia_cc"] = _procesar_presencia_cc()
    except Exception as e:
        chk["presencia_cc_error"] = "%r" % e
    dias = _dias_sin_senal()
    chk["dias_sin_senal"] = round(dias, 2) if dias is not None else None
    if dias is not None and dias > AUSENCIA_DIAS:
        if not os.path.exists(DEGRADED):
            open(DEGRADED, "w").close()
        chk["degradado"] = True
        # clave estable: "dead_man_ausencia" (los días exactos son volátiles, no van en la clave)
        alertas.append(("dead_man_ausencia",
                        "Llevas %d días sin dar señal → modo conservador de gasto activado. "
                        "La salida sigue bloqueada por el muro; el lazo sigue trabajando hacia dentro." % int(dias)))
    else:
        chk["degradado"] = os.path.exists(DEGRADED)

    _write_atomic(LAST_CHECK, chk)

    # 5b. LIBRO DE DEUDA — R3 del plan «que quede arreglado» (25-jul-26): quien vigila también se
    # vigila. Dos cosas, las dos con clave estable para el anti-spam de _emitir_si_cambia:
    #   · si hay deuda ESCALADA (un hallazgo repetido y sin cerrar), {{TITULAR}} se entera — no se queda
    #     solo en el rojo de la suite;
    #   · si el propio libro deja de latir (nadie lo toca en DEUDA_LATIDO_DIAS), eso es un dead-man:
    #     significa que los detectores han dejado de registrar, que es como se murieron los intentos
    #     anteriores (en silencio).
    try:
        import deuda as _deuda_mod
        _esc = _deuda_mod.escaladas()
        if _esc:
            _peor = max(_esc.values(), key=lambda v: v.get("veces", 0))
            alertas.append(("deuda_escalada",
                            "🔴 %d hallazgo(s) detectados varias veces y sin cerrar (el peor: %s, "
                            "%dx). La suite está en rojo por esto: `python3 tools/deuda.py list`"
                            % (len(_esc), (_peor.get("que") or "?")[:60], _peor.get("veces", 0))))
        _hb_deuda = os.path.join(STATE, "heartbeat", "deuda.json")
        _edad_d = (time.time() - os.path.getmtime(_hb_deuda)) / 86400.0 \
            if os.path.exists(_hb_deuda) else 999
        if _edad_d > DEUDA_LATIDO_DIAS:
            alertas.append(("deuda_libro_sin_latido",
                            "El libro de deuda no late desde hace %d día(s): los detectores han "
                            "dejado de registrar hallazgos (así se murieron los intentos "
                            "anteriores, en silencio)." % int(_edad_d)))
    except Exception:
        pass

    # 5c. CANARIOS del muro (24-sep-2026): el borde bloquea y escala si aparece un canario sembrado,
    # pero durante meses `canarios.json` no existía — un tripwire sin munición, y en silencio. Si se
    # queda a cero otra vez, se avisa: esto es exactamente lo que nadie vio.
    try:
        _canarios = os.path.join(STATE, "borde", "canarios.json")
        _n_can = 0
        if os.path.exists(_canarios):
            with open(_canarios, encoding="utf-8") as _f:
                _n_can = len([x for x in json.load(_f) if isinstance(x, str) and x.strip()])
        if _n_can == 0:
            alertas.append(("canarios_sin_sembrar",
                            "El muro no tiene NINGÚN canario sembrado: si algo exfiltrara datos, el "
                            "tripwire no podría dispararse. Se siembra con "
                            "`python3 tools/canarios.py sembrar`."))
    except Exception:
        pass

    # 6. Separar alertas en dos categorías:
    #    · operativo: fontanería que el sistema resolvió solo (cola_reap, heartbeat corrupto auto-curado).
    #      → va al log de operativo, no al chat de {{TITULAR}}.
    #    · humano: requiere atención o acción de {{TITULAR}} (disco, dispatcher, servicios, saldo, dead-man…).
    #      → entrega al chat de {{TITULAR}} con el anti-spam habitual.
    # REGLA DE ORO: ante la duda → humano. Default fail-safe.
    # `jobs_caidos:sin-entregable` (20-sep-26): lo escribe el gate de entregable del dispatcher
    # cuando un job terminó limpio sin dejar lo que prometió. Es OPERATIVO a propósito: toda clave
    # humana NUEVA encola una investigación de pago (~1 USD, ver _encolar_investigacion), así que
    # un gate hecho para dejar de tirar dinero acabaría facturando por cada job que caza. Sigue
    # viéndose en el panel, en el log de operativo y en el libro de deudas.
    CLAVES_OPERATIVAS = frozenset(["cola_reap", "cola_recover_schema",
                                   "jobs_caidos:sin-entregable",
                                   "jobs_caidos:verificacion-indisponible",
                                   # Divergencia repo↔instalado: fontanería que se arregla mirando
                                   # dos ficheros. Humana encolaría una investigación de pago.
                                   "plist_drift"])
    def _clave_de(a):
        return a[0] if isinstance(a, tuple) else str(a)
    alertas = _una_red_sin_dns(alertas)

    alertas_op = [a for a in alertas if _clave_de(a) in CLAVES_OPERATIVAS
                  or (_clave_de(a).startswith("daemon_") and _clave_de(a).endswith("_corrupto"))
                  or (_clave_de(a).startswith("rutina_ned_") and _clave_de(a).endswith("_corrupto"))
                  or (_clave_de(a).startswith("daemon_") and _clave_de(a).endswith("_autofix"))
                  or _clave_de(a).startswith("daemon_roster_autofix:")]
    alertas_hum = [a for a in alertas if a not in alertas_op]

    if alertas_op:
        _emitir_si_cambia(alertas_op, categoria="operativo")
    _emitir_si_cambia(alertas_hum)

    # Bus de errores (Fase 1): feed silencioso al punto único de errores para cada alerta nueva.
    # escalar=False: healthcheck ya gestiona su propio aviso arriba; aquí solo dejamos traza.
    if alertas:
        try:
            import errores as _err
            for a in alertas:
                clave = a[0] if isinstance(a, tuple) else str(a)[:80]
                texto = a[1] if isinstance(a, tuple) else str(a)
                _err.registrar(
                    origen="healthcheck",
                    error=texto[:300],
                    severidad=_err.OPERATIVO,
                    job=clave,
                    escalar=False,
                )
        except Exception:
            pass   # fail-soft: nunca rompemos el ciclo de healthcheck

    # Observabilidad (pieza 8): registra traza del ciclo completo. Silencioso.
    try:
        import observabilidad as _obs
        _obs.registrar(
            agente="healthcheck",
            job="ciclo",
            ts_ini=datetime.utcfromtimestamp(_obs_ts_ini).strftime("%Y-%m-%dT%H:%M:%SZ"),
            resultado="ok" if not alertas else "ok",  # el ciclo siempre "ok" si no lanzó
        )
    except Exception:
        pass

    # Higiene de sesiones (ahorro de gasto Max): avisa suave a {{TITULAR}} si hoy hay
    # demasiadas sesiones reales abiertas o una muy larga. Anti-spam 1/día propio,
    # respeta HALT/silencio. Fail-soft: nunca rompe el ciclo de healthcheck.
    try:
        import vigia_sesiones as _vs
        _vs.run(send=True)
    except Exception:
        pass

    return chk, alertas


def main(argv):
    if argv and argv[0] == "seen":
        # Anti-falsificación (A3/H2): el lazo NO puede fingir la presencia de {{TITULAR}}. El
        # subcomando exige BTP_PRESENCE_OK=1, que run_agent.sh NO exporta al agente; lo
        # ponen btp_run.sh (acto manual de {{TITULAR}}) y el bot. El bot real marca presencia
        # en-proceso (mark_seen), no por esta CLI.
        if os.environ.get("BTP_PRESENCE_OK") != "1":
            print("seen: señal humana — requiere BTP_PRESENCE_OK=1 (no la fija el lazo)")
            return 1
        mark_seen(argv[1] if len(argv) > 1 else "manual")
        print("last_seen actualizado")
        return 0
    try:
        chk, alertas = run()
    except Exception:
        _latido_externo(fallo=True)       # el vigía se rompió: que lo sepa alguien de fuera
        raise
    chk["latido_externo"] = _latido_externo()
    print(json.dumps(chk, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
