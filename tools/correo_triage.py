#!/usr/bin/env python3
"""tools/correo_triage.py — correo "fino + monitorizado + organizado" (2/7/26).

Cierra el hueco pedido por {{TITULAR}}: Vega hoy solo tiene el gestor de correo EN SESIÓN
(prompt de agente en asistente.md); no había una capa DETERMINISTA que:
  (a) triara AMBAS cuentas de correo entrante y produjera una sección lista para el HOY
      diario + avisos de lo urgente / lo que espera su firma o respuesta de terceros;
  (b) propusiera una categoría por remitente/dominio/keywords (correo.categoria_de),
      en modo --dry (SIMULA);
  (c) propusiera qué archivar (ruido claro: Promos/newsletters) en DRY-RUN;
  (d) — 2/7/26, con el OK explícito de {{TITULAR}} ("sí aplica") — APLICARA de verdad esas
      etiquetas/archivado en Gmail vía IMAP (modo `apply`, ver más abajo).

`barrer`/`categorizar`/`archivar-propuesta`/`seccion`/`avisos` son SOLO LECTURA de principio
a fin (reusan correo_imap.py IMAP-readonly y correo.py para las reglas duras, nunca las
reimplementan). El modo `apply` es la ÚNICA excepción — abre una conexión IMAP de ESCRITURA,
documentada aparte más abajo. Cero SMTP en todo el fichero (ni siquiera importado):
estructuralmente no puede enviar correo, solo etiquetar/archivar dentro de la propia cuenta.

Anti-inyección: el asunto/remitente de cada correo es DATO NO CONFIABLE — igual que en
correo_imap._entry, el asunto pasa por correo.detecta_inyeccion antes de mostrarse (si
detecta patrón, se retiene). Nada de lo leído aquí se persiste a memoria durable a ciegas;
solo escribe tools/state/correo/triage.json (estado regenerable, no fuente de verdad).

Uso:
  python3 tools/correo_triage.py barrer [--dry]     # IMAP RO en AMBAS cuentas, guarda triage.json
                                                       # (uso manual/emergencia; el poller normal
                                                       # es correo-imap.py cada 120s, ver refrescar)
  python3 tools/correo_triage.py refrescar          # SIN RED: relee buzon.json ya persistido por
                                                       # correo-imap.py y solo refresca triage.json
                                                       # (item #14, 15-jul: evita abrir una SEGUNDA
                                                       # conexión IMAP contra la misma cuenta —
                                                       # probable causa del imaplib EOF del 10-jul).
                                                       # Es lo que corre correo-refrescar cada 30min.
  python3 tools/correo_triage.py seccion            # imprime la sección para el parte de HOY
  python3 tools/correo_triage.py avisos             # imprime lo urgente / lo que espera firma
  python3 tools/correo_triage.py digest [--dry]     # correo NUEVO no-urgente que merece un aviso
                                                       # breve intradía; '' si nada nuevo (calla).
                                                       # --dry no marca como visto (para probar).
  python3 tools/correo_triage.py digest-enviar [--dry]  # digest + salida.report_to_titular SI
                                                       # hay algo (si no, no manda nada — silencio
                                                       # real). NO barre (el lector correo-imap,
                                                       # cada 2 min, ya mantiene el buzón fresco).
                                                       # Determinista, sin LLM, $0. --dry no envía.
  python3 tools/correo_triage.py categorizar [--dry]  # propone categoria_de() por correo (SIMULA)
  python3 tools/correo_triage.py archivar-propuesta   # lista de "qué archivaría" (ruido); SIEMPRE
                                                       # dry — aplicar en vivo es acto separado + OK
  python3 tools/correo_triage.py apply [--dry] [--cuentas a b]  # APLICA etiquetas Gmail + archiva
                                                       # el ruido claro (newsletters). --dry SIMULA
                                                       # (no toca IMAP en escritura). Requiere el OK
                                                       # explícito de {{TITULAR}} (2/7/26, dado) — ver
                                                       # apply_log.jsonl para deshacer.

MODO APPLY — única parte de este fichero que ESCRIBE en Gmail (todo lo demás es de solo lectura):
  · Etiquetar: SIEMPRE pasa por correo.etiqueta_permitida() antes de crear/aplicar (fail-closed;
    TRASH/SPAM nunca se aplican). Idempotente (no duplica si el mensaje ya lleva la etiqueta,
    comprobado vía X-GM-LABELS). Nombres con acentos ("NED/Médico") van codificados a Modified
    UTF-7 (RFC 3501) — imaplib NO lo hace solo, así que este módulo trae su propio codec
    (_mutf7_encode/_decode, ver tests de round-trip en tests/test_correo_triage.py).
  · Archivar: SOLO el ruido claro que ya propone archivar_propuesta() Y cuya categoria_ruido()
    sea 'newsletters' (nunca recibos/spam en esta primera pasada — el recibo de Umami se queda).
    "Archivar" = quitar SOLO la etiqueta INBOX (UID STORE +FLAGS \\Deleted + EXPUNGE, ejecutado
    con INBOX seleccionado — en Gmail eso NUNCA borra el mensaje, solo lo saca de la bandeja;
    sigue vivo en 'Todos los mensajes'). Este módulo NUNCA selecciona ni expunge sobre 'Todos'/
    'All Mail' — sería borrado real, y eso el muro lo prohíbe.
  · Log reversible: cada acción (cuenta, uid, etiqueta aplicada / archivado) se apendea a
    tools/state/correo/apply_log.jsonl. --dry no escribe nada (ni IMAP ni log).
"""
import base64
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import correo               # noqa: E402  (reglas duras)
import correo_imap as cimap  # noqa: E402  (lector IMAP, solo lectura + _login/_secret_for/_assert_host)

STATE = correo.CORREO_DIR
TRIAGE = os.path.join(STATE, "triage.json")
APPLY_LOG = os.path.join(STATE, "apply_log.jsonl")

# Cuentas por defecto del triaje "fino" (las dos que {{TITULAR}} usa a diario). Se leen de
# correo_imap.CUENTAS por nombre, así que un cambio de clave de Llavero no hay que duplicarlo.
CUENTAS_DEFECTO = ("titular.mgp@gmail.com", "titular@gmail.com")


def _cuentas_objetivo(nombres=None):
    nombres = nombres or CUENTAS_DEFECTO
    disponibles = dict(cimap.CUENTAS)
    return [u for u in nombres if u in disponibles]


def barrer(dry=False, cuentas=None, alertar=None):
    """Un pulso de triaje sobre AMBAS cuentas (IMAP RO, incremental por cuenta). Reusa
    correo_imap.once(user=...) — no reimplementa el parseo IMAP. dry=True no persiste ni
    avisa (igual semántica que correo_imap). Devuelve {cuenta: resumen}."""
    out = {}
    for user in _cuentas_objetivo(cuentas):
        try:
            out[user] = cimap.once(dry=dry, user=user, alertar=alertar)
        except Exception as e:
            out[user] = {"error": "%r" % e}
    if not dry:
        correo._ensure()
        correo._write_atomic(TRIAGE, {
            "actualizado": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "cuentas": list(out.keys()),
        })
    return out


def refrescar_desde_buzon(cuentas=None):
    """Item #14 (saneamiento 15-jul): pulso de correo-refrescar SIN RED. correo-imap.py YA
    poll-ea IMAP cada 120s y deja buzon-<cuenta>.json fresco; abrir AQUI una segunda conexion
    IMAP contra las MISMAS cuentas cada 30 min era trabajo duplicado y probable causa del
    'imaplib EOF' del 10-jul (dos conexiones concurrentes a Gmail IMAP). Esta funcion relee
    lo que correo-imap ya persistio (cimap.cargar_buzon, sin red) y solo refresca triage.json;
    NUNCA llama a cimap.once()/cimap._login(). barrer() (real, con IMAP) se deja intacta para
    uso manual si algun dia hace falta un barrido de verdad fuera del poller."""
    out = {}
    for user in _cuentas_objetivo(cuentas):
        try:
            out[user] = {"mensajes": len(_mensajes_de(user))}
        except Exception as e:
            out[user] = {"error": "%r" % e}
    correo._ensure()
    correo._write_atomic(TRIAGE, {
        "actualizado": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "cuentas": list(out.keys()),
    })
    return out

def _mensajes_de(user):
    """Mensajes recientes de una cuenta, desde su buzón persistido (sin tocar red)."""
    _, buzon_path = cimap.path_por_cuenta(user)
    return cimap.cargar_buzon(buzon_path).get("mensajes", [])


def _todos_los_mensajes(cuentas=None):
    """{email: [mensajes]} de todas las cuentas objetivo, cada mensaje con su 'cuenta'."""
    out = []
    for user in _cuentas_objetivo(cuentas):
        for m in _mensajes_de(user):
            m2 = dict(m)
            m2["cuenta"] = user
            out.append(m2)
    return out


def _es_ruido_msg(m):
    return correo.es_ruido(m.get("remitente_email", ""), m.get("asunto", ""))


def _espera_firma(m):
    """Heurística conservadora: NED-crítico + señal de cita/plazo (correo.es_urgente ya
    combina ambas) Y de persona real → probablemente espera respuesta/firma suya. No
    inventa: si no hay señal dura, no entra en esta lista (mejor listar de menos)."""
    return m.get("urgente") and correo.es_persona_real(m.get("remitente_email", ""))


def resumen(cuentas=None):
    """Triaje sobre lo YA barrido (sin tocar red): urgentes, esperando-firma, ruido, resto.
    Prioriza por impacto-NED (NED-crítico primero), determinista. No incluye lo marcado
    con posible inyección (queda fuera de listas legibles, tal como ya hace correo_imap)."""
    msgs = _todos_los_mensajes(cuentas)
    limpios = [m for m in msgs if not m.get("inyeccion")]
    urgentes = [m for m in limpios if m.get("urgente")]
    ned = [m for m in urgentes if m.get("ned_critico")]
    otros_urgentes = [m for m in urgentes if not m.get("ned_critico")
                      and correo.es_persona_real(m.get("remitente_email", ""))
                      and not _es_ruido_msg(m)]
    esperando_firma = [m for m in limpios if _espera_firma(m)]
    ruido = [m for m in limpios if _es_ruido_msg(m)]
    personas = [m for m in limpios if correo.es_persona_real(m.get("remitente_email", ""))
                and not m.get("urgente") and m not in ruido]
    inyeccion = [m for m in msgs if m.get("inyeccion")]
    return {
        "ned_critico": ned,
        "otros_urgentes": otros_urgentes,
        "esperando_firma": esperando_firma,
        "ruido": ruido,
        "personas_no_urgentes": personas,
        "inyeccion_retenida": len(inyeccion),
        "total": len(msgs),
    }


def _nombre_corto(m):
    return m.get("remitente") or m.get("remitente_email") or "alguien"


def seccion_hoy(cuentas=None, max_items=5):
    """Sección para el parte de HOY diario: DETERMINISTA, sin IA. Formato compacto (regla
    de tabla/lista escaneable), pensado para fundirse con seguimiento.construir_hoy.
    Devuelve '' si no hay nada que decir (no ensucia el HOY con un bloque vacío)."""
    r = resumen(cuentas)
    if not (r["ned_critico"] or r["otros_urgentes"] or r["esperando_firma"]):
        return ""
    L = ["📬 CORREO"]
    for m in r["ned_critico"][:max_items]:
        L.append("   🔴 %s: «%s»" % (_nombre_corto(m), m.get("asunto", "?")))
    for m in r["esperando_firma"][:max_items]:
        if m in r["ned_critico"]:
            continue
        L.append("   ✍️ %s espera tu respuesta: «%s»" % (_nombre_corto(m), m.get("asunto", "?")))
    resto = [m for m in r["otros_urgentes"] if m not in r["esperando_firma"]]
    for m in resto[:max_items]:
        L.append("   • %s: «%s»" % (_nombre_corto(m), m.get("asunto", "?")))
    return "\n".join(L)


def avisos_pendientes(cuentas=None):
    """Lista de avisos individuales (para Vega, no para enviar directo): lo urgente y lo
    que espera firma/respuesta. Formato de texto corto, en su nombre propio (nunca la
    dirección cruda), como ya hace correo_imap.procesar(). No avisa por sí mismo — deja
    los textos listos; el envío real por salida.py lo hace quien invoque esto (Vega)."""
    r = resumen(cuentas)
    out = []
    for m in r["ned_critico"]:
        out.append({"texto": "🔴 %s (NED-crítico): «%s»" % (_nombre_corto(m), m.get("asunto", "?")),
                    "ned_critico": True, "cuenta": m.get("cuenta")})
    for m in r["esperando_firma"]:
        if m.get("ned_critico"):
            continue
        out.append({"texto": "✍️ %s espera tu respuesta: «%s»" % (_nombre_corto(m), m.get("asunto", "?")),
                    "ned_critico": False, "cuenta": m.get("cuenta")})
    return out


DIGEST_VISTO = os.path.join(correo.CORREO_DIR, "digest_visto.json")


def _digest_id(m):
    """Identificador único y estable de un mensaje para el ledger del digest intradía:
    message_id si lo hay (cabecera del propio correo, estable entre pasadas), si no
    cuenta+uid (uid es solo estable DENTRO de una cuenta, así que va con su prefijo)."""
    mid = (m.get("message_id") or "").strip()
    if mid:
        return mid
    return "%s#%s" % (m.get("cuenta", "?"), m.get("uid", "?"))


def _cargar_digest_visto():
    try:
        with open(DIGEST_VISTO, encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def _guardar_digest_visto(ids):
    correo._ensure()
    # Tope de tamaño (no crece sin fin): se queda con los últimos 500, más que de sobra
    # para no repetir dentro de un mismo día de pasadas cada pocas horas.
    correo._write_atomic(DIGEST_VISTO, sorted(ids)[-500:])


def digest_intradia(cuentas=None, max_items=6, marcar=True):
    """Pasada corta (pensada para 2-3 veces/día): correo NUEVO no-urgente que merece un
    aviso breve — personas reales, ya barridas, que NO están en ned_critico/otros_urgentes/
    esperando_firma (esos ya avisan por su propia vía) y que el digest AÚN no mostró.
    DETERMINISTA, sin IA. Devuelve '' si no hay nada que merezca (el gestor debe CALLAR,
    no mandar 'sin novedades' — regla de anti-spam de {{TITULAR}}).

    `marcar=True`: persiste los ids devueltos en el ledger para no repetirlos en la
    siguiente pasada (idempotente: llamar dos veces seguidas sin barrer entremedias da
    vacío la segunda vez). `marcar=False` es para --dry (mirar sin consumir)."""
    r = resumen(cuentas)
    ya_avisados = {_digest_id(m) for m in
                  (r["ned_critico"] + r["otros_urgentes"] + r["esperando_firma"])}
    vistos = _cargar_digest_visto()
    candidatos = [m for m in r["personas_no_urgentes"]
                 if _digest_id(m) not in ya_avisados and _digest_id(m) not in vistos]
    if not candidatos:
        return ""
    elegidos = candidatos[:max_items]
    L = ["📬 Correo nuevo (no urgente):"]
    for m in elegidos:
        L.append("   • %s: «%s»" % (_nombre_corto(m), m.get("asunto", "?")))
    resto = len(candidatos) - len(elegidos)
    if resto > 0:
        L.append("   …y %d más." % resto)
    if marcar:
        _guardar_digest_visto(vistos | {_digest_id(m) for m in candidatos})
    return "\n".join(L)


def enviar_digest_intradia(cuentas=None, dry=False):
    """Pasada CORTA para el cron (digest + avisar si hay algo), SIN pasar por un agente
    LLM (determinista, $0). NO barre red: el lector `correo-imap` (cada 2 min) ya mantiene
    el buzón fresco — barrer aquí otra vez duplicaría trabajo y pisaría su dominio (mismo
    principio que seccion_hoy()/avisos_pendientes(), que tampoco barren). Si hace falta un
    barrido más fresco antes de esto, es el cron el que debe ordenar 'barrer' antes en su
    propia pasada, no esta función.
    `salida.report_to_titular` YA respeta HALT + silencio nocturno (23-8) + anti-spam de
    verdad: aquí el 'no molestar de más' es que, si no hay nada NUEVO que merezca, esta
    función NO LLAMA a report_to_titular en absoluto (silencio real, no un mensaje vacío).
    `dry=True`: calcula el digest SIN marcar como visto y SIN enviar — para probar el
    cableado sin dejar huella. Devuelve (enviado:bool, texto:str)."""
    texto = digest_intradia(cuentas=cuentas, marcar=not dry)
    if not texto:
        return False, ""
    if dry:
        return False, texto   # no se envía en dry, pero se ve qué habría dicho
    import salida
    salida.report_to_titular(texto, urgente=False)
    return True, texto


def categorizar(cuentas=None, dry=True):
    """Propone una categoría (correo.categoria_de) por cada correo reciente. SIEMPRE
    informativo: no aplica nada por sí misma (aplicar de verdad es apply(), más abajo, o
    el conector Gmail en sesión — ambos exigen su OK). dry=False solo cambia si el resultado
    se PERSISTE a triage.json para que Vega lo consulte sin recalcular; esta función en
    concreto nunca toca Gmail en escritura."""
    msgs = [m for m in _todos_los_mensajes(cuentas) if not m.get("inyeccion")]
    props = []
    for m in msgs:
        cat = correo.categoria_de(m.get("remitente_email", ""), m.get("asunto", ""))
        if cat and correo.etiqueta_permitida(cat):
            props.append({"cuenta": m.get("cuenta"), "remitente": _nombre_corto(m),
                          "asunto": m.get("asunto", ""), "categoria_propuesta": cat})
    if not dry:
        correo._ensure()
        p = os.path.join(STATE, "categorizacion_propuesta.json")
        correo._write_atomic(p, {"actualizado": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                 "propuestas": props})
    return props


def archivar_propuesta(cuentas=None):
    """Propuesta de ARCHIVADO (quitar INBOX, reversible, NUNCA borrar) — solo ruido claro
    (Promos/newsletters/recibos vía correo.es_ruido) y NUNCA NED-crítico (correo.puede_archivar
    es la última palabra). SIEMPRE dry-run: esta función solo devuelve la lista; aplicar de
    verdad es apply() (más abajo), que además restringe el archivado solo a 'newsletters'
    (más conservador que esta propuesta, que también lista recibos/spam informativamente)."""
    msgs = [m for m in _todos_los_mensajes(cuentas) if not m.get("inyeccion")]
    out = []
    for m in msgs:
        sender, subj = m.get("remitente_email", ""), m.get("asunto", "")
        if _es_ruido_msg(m) and correo.puede_archivar(sender, subj):
            out.append({"cuenta": m.get("cuenta"), "remitente": _nombre_corto(m),
                        "asunto": subj, "motivo": correo.categoria_ruido(sender) or "ruido"})
    return out


# ── Modified UTF-7 (RFC 3501 §5.1.3) — nombres de buzón IMAP con no-ASCII ────────
# imaplib NO trae este codec (el 'utf-7' de Python estándar NO es el mismo: Gmail/IMAP usan
# ',' en vez de '/' en el base64 y delimitan con '&...-'). Sin esto, "NED/Médico" se manda
# mal codificado y Gmail crea una etiqueta con mojibake — inaceptable (regla del encargo).
# Implementación acotada, sin dependencias, con test de round-trip en test_correo_triage.py.
def _mutf7_encode(s):
    """str -> Modified UTF-7 (bytes ASCII, como str). Solo caracteres imprimibles 0x20-0x7e
    van literales (excepto '&', que se escapa como '&-'); el resto se agrupa y se manda como
    UTF-16BE en base64 estándar con '/' -> ',' y sin padding '=', entre '&' y '-'."""
    out = []
    buf = []

    def flush():
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + b64 + "-")
        buf.clear()

    for ch in s or "":
        o = ord(ch)
        if 0x20 <= o <= 0x7E and ch != "&":
            flush()
            out.append(ch)
        elif ch == "&":
            flush()
            out.append("&-")
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def _mutf7_decode(s):
    """Inverso de _mutf7_encode (para el test de round-trip; no se usa en el flujo normal)."""
    out = []
    i, n = 0, len(s or "")
    while i < n:
        ch = s[i]
        if ch == "&":
            j = s.find("-", i + 1)
            if j == -1:
                j = n
            chunk = s[i + 1:j]
            if chunk == "":
                out.append("&")
            else:
                b64 = chunk.replace(",", "/")
                b64 += "=" * ((-len(b64)) % 4)
                out.append(base64.b64decode(b64).decode("utf-16-be"))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _mbox_literal(nombre_mutf7):
    """Envuelve el nombre de buzón (ya en Modified UTF-7, todo ASCII) en comillas IMAP.
    Los nombres de este esquema no llevan comillas ni backslash, así que basta citar."""
    return '"%s"' % nombre_mutf7


# ── apply(): la ÚNICA función de este fichero que escribe en Gmail ──────────────
def _log_apply(evento):
    correo._ensure()
    with open(APPLY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")


def _etiquetas_actuales(M, uid):
    """Lee X-GM-LABELS del mensaje (idempotencia: no reetiquetar lo que ya la tiene)."""
    try:
        typ, data = M.uid("fetch", str(uid), "(X-GM-LABELS)")
    except Exception:
        return set()
    if typ != "OK" or not data:
        return set()
    labels = set()
    for part in data:
        if isinstance(part, (bytes, bytearray)):
            raw = part
        elif isinstance(part, tuple) and len(part) >= 2:
            raw = part[1] or b""   # part[0] es la línea de estado ("N FETCH ..."), no el dato
        else:
            raw = b""
        if not raw:
            continue
        txt = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        m = re.search(r"X-GM-LABELS \(([^)]*)\)", txt)
        if not m:
            continue
        # Gmail devuelve las labels como átomos/strings IMAP separados por espacio; los que
        # llevan espacios propios van entre comillas. Split simple respetando comillas.
        for tok in re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', m.group(1)):
            val = tok[0] if tok[0] else tok[1]
            if val:
                labels.add(val)
    return labels


def _asegura_etiqueta(M, etiqueta_mutf7, cache_creadas):
    """CREATE de la carpeta-etiqueta si no existe aún (idempotente: ignora 'ya existe').
    `cache_creadas` evita repetir CREATE para la misma etiqueta dentro de la misma corrida."""
    if etiqueta_mutf7 in cache_creadas:
        return
    try:
        M.create(_mbox_literal(etiqueta_mutf7))
    except Exception:
        pass  # ya existe (o falla y COPY lo revelará) — no es fatal
    cache_creadas.add(etiqueta_mutf7)


def apply(cuentas=None, dry=False):
    """Aplica etiquetas Gmail (categoria_de) y archiva el ruido claro (newsletters) sobre
    los UIDs recientes de INBOX, en cada cuenta. dry=True SIMULA: calcula y muestra qué haría,
    NO abre conexión de escritura ni toca IMAP ni el log. Devuelve un resumen por cuenta.

    Gates duros (en este orden, ninguno se salta):
      1. etiqueta_permitida() antes de crear/aplicar cualquier etiqueta (fail-closed).
      2. Archivar solo si es_ruido() Y categoria_ruido()=='newsletters' Y puede_archivar()
         (NED-crítico nunca se archiva — puede_archivar ya lo garantiza).
      3. Archivar = quitar SOLO INBOX (STORE \\Deleted + EXPUNGE con INBOX seleccionado);
         jamás se selecciona 'Todos'/'All Mail' en este módulo.
    """
    resumen_out = {}
    for user in _cuentas_objetivo(cuentas):
        secret = cimap._secret_for(user)
        cuenta_res = {"etiquetadas": 0, "por_categoria": {}, "archivados": 0,
                      "omitidas_no_permitida": 0, "errores": []}
        try:
            _, buzon_path = cimap.path_por_cuenta(user)
            mensajes = cimap.cargar_buzon(buzon_path).get("mensajes", [])
        except Exception as e:
            cuenta_res["errores"].append("no se pudo leer buzón local: %r" % e)
            resumen_out[user] = cuenta_res
            continue

        # Plan (puro, sin red): qué etiqueta y qué archivar, por UID.
        plan_etiqueta = {}   # uid -> categoria
        plan_archivar = []   # uids
        for m in mensajes:
            if m.get("inyeccion"):
                continue  # dato retenido por revisión: no se actúa sobre él
            uid = m.get("uid")
            sender, subj = m.get("remitente_email", ""), m.get("asunto", "")
            cat = correo.categoria_de(sender, subj)
            if cat and correo.etiqueta_permitida(cat):
                plan_etiqueta[uid] = cat
            elif cat:
                cuenta_res["omitidas_no_permitida"] += 1
            if (correo.es_ruido(sender, subj) and correo.categoria_ruido(sender) == "newsletters"
                    and correo.puede_archivar(sender, subj)):
                plan_archivar.append(uid)

        if dry:
            for cat in plan_etiqueta.values():
                cuenta_res["por_categoria"][cat] = cuenta_res["por_categoria"].get(cat, 0) + 1
            cuenta_res["etiquetadas"] = len(plan_etiqueta)
            cuenta_res["archivados"] = len(plan_archivar)
            resumen_out[user] = cuenta_res
            continue

        # Ejecución real: conexión de ESCRITURA (no readonly), solo INBOX.
        try:
            M = cimap._login(user=user, secret=secret)
            M.select("INBOX")  # escritura: sin readonly=True
        except cimap.FaltaClave as e:
            cuenta_res["errores"].append(str(e))
            resumen_out[user] = cuenta_res
            continue
        except Exception as e:
            cuenta_res["errores"].append("login/select falló: %r" % e)
            resumen_out[user] = cuenta_res
            continue

        cache_creadas = set()
        try:
            for uid, cat in plan_etiqueta.items():
                try:
                    ya = _etiquetas_actuales(M, uid)
                    et_mutf7 = _mutf7_encode(cat)
                    if cat in ya or et_mutf7 in ya:
                        continue  # idempotente: ya la tiene
                    _asegura_etiqueta(M, et_mutf7, cache_creadas)
                    typ, _resp = M.uid("copy", str(uid), _mbox_literal(et_mutf7))
                    if typ == "OK":
                        cuenta_res["etiquetadas"] += 1
                        cuenta_res["por_categoria"][cat] = cuenta_res["por_categoria"].get(cat, 0) + 1
                        _log_apply({"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                    "accion": "etiquetar", "cuenta": user, "uid": uid,
                                    "etiqueta": cat})
                    else:
                        cuenta_res["errores"].append("copy uid=%s -> %s: %s" % (uid, cat, typ))
                except Exception as e:
                    cuenta_res["errores"].append("uid=%s etiqueta=%s: %r" % (uid, cat, e))

            for uid in plan_archivar:
                try:
                    typ, _resp = M.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
                    if typ == "OK":
                        cuenta_res["archivados"] += 1
                        _log_apply({"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                    "accion": "archivar-inbox", "cuenta": user, "uid": uid})
                    else:
                        cuenta_res["errores"].append("store uid=%s: %s" % (uid, typ))
                except Exception as e:
                    cuenta_res["errores"].append("uid=%s archivar: %r" % (uid, e))
            if cuenta_res["archivados"]:
                try:
                    M.expunge()  # SOLO con INBOX seleccionado: en Gmail esto quita INBOX, no borra
                except Exception as e:
                    cuenta_res["errores"].append("expunge: %r" % e)
        finally:
            try:
                M.close()
            except Exception:
                pass
            try:
                M.logout()
            except Exception:
                pass

        resumen_out[user] = cuenta_res
    return resumen_out


def _fmt_lista(items, campo_extra=None):
    if not items:
        return "  (nada)"
    L = []
    for it in items:
        base = "  - [%s] %s: «%s»" % (it.get("cuenta", "?"), it.get("remitente", "?"), it.get("asunto", "?"))
        if campo_extra and it.get(campo_extra):
            base += "  (%s)" % it[campo_extra]
        L.append(base)
    return "\n".join(L)


def main(argv):
    cmd = argv[0] if argv else "seccion"
    dry = "--dry" in argv
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "barrer":
        silencioso = "--silencioso" in argv
        r = barrer(dry=dry, alertar=(False if silencioso else None))
        for user, res in r.items():
            if res.get("error"):
                print("⚠️  %s: %s" % (user, res["error"]))
            else:
                print("%s: nuevos=%d avisos=%d baseline=%s mensajes=%d%s"
                      % (user, res.get("nuevos", 0), res.get("avisos", 0), res.get("baseline"),
                         len(res.get("buzon", {}).get("mensajes", [])),
                         " (DRY)" if dry else ""))
        return 0
    if cmd == "refrescar":
        r = refrescar_desde_buzon()
        for user, res in r.items():
            if res.get("error"):
                print("⚠️  %s: %s" % (user, res["error"]))
            else:
                print("%s: mensajes=%d (sin red, releido de buzon.json)" % (user, res["mensajes"]))
        return 0
    if cmd == "seccion":
        s = seccion_hoy()
        print(s if s else "(sin correo urgente)")
        return 0
    if cmd == "avisos":
        for a in avisos_pendientes():
            print(a["texto"])
        return 0
    if cmd == "digest":
        s = digest_intradia(marcar=not dry)
        print(s if s else "(nada nuevo que merezca digest — calla)")
        return 0
    if cmd == "digest-enviar":
        enviado, texto = enviar_digest_intradia(dry=dry)
        if enviado:
            print("✅ digest enviado:\n%s" % texto)
        elif texto:
            print("(DRY, no enviado) habría dicho:\n%s" % texto)
        else:
            print("(nada nuevo que merezca digest — no se manda nada)")
        return 0
    if cmd == "resumen":
        r = resumen()
        print(json.dumps({k: (v if isinstance(v, int) else len(v)) for k, v in r.items()},
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "categorizar":
        props = categorizar(dry=dry)
        print("Propuestas de categoría (%d) — SIMULACIÓN, nada aplicado:" % len(props))
        print(_fmt_lista(props, campo_extra="categoria_propuesta"))
        return 0
    if cmd == "archivar-propuesta":
        props = archivar_propuesta()
        print("Propuesta de ARCHIVADO (%d) — quitar INBOX, reversible, NUNCA borrar. "
              "DRY-RUN: nada se aplica sin tu OK." % len(props))
        print(_fmt_lista(props, campo_extra="motivo"))
        return 0
    if cmd == "apply":
        cuentas = None
        if "--cuentas" in argv:
            i = argv.index("--cuentas")
            cuentas = [a for a in argv[i + 1:] if not a.startswith("--")] or None
        r = apply(cuentas=cuentas, dry=dry)
        total_et, total_arch = 0, 0
        for user, res in r.items():
            print("%s%s" % (user, " (DRY)" if dry else ""))
            print("  etiquetadas: %d %s" % (res["etiquetadas"], res["por_categoria"] or ""))
            print("  archivadas (quitado INBOX): %d" % res["archivados"])
            if res.get("omitidas_no_permitida"):
                print("  omitidas (etiqueta no permitida): %d" % res["omitidas_no_permitida"])
            for e in res.get("errores", []):
                print("  ⚠️  %s" % e)
            total_et += res["etiquetadas"]
            total_arch += res["archivados"]
        print("TOTAL: %d etiquetadas, %d archivadas%s" % (total_et, total_arch,
              " — SIMULACIÓN, nada aplicado" if dry else " — aplicado en vivo"))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
