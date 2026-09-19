#!/usr/bin/env python3
"""tools/dedup_hilos.py — pasada de dedup DETERMINISTA de hilos en seguimiento.json.

Por que existe: la capa de informacion (triaje de correo, agenda HOY, planes) generaba
inteligencia pero no la persistia con dedup-en-escritura: la misma reunion de Bernardo
Cordovez terminaba como 4+ hilos contradictorios. Este modulo DETECTA esos duplicados
y los funde (o los propone a Vega cuando la confianza es baja).

Algoritmo (conservador, fail-closed):
    1. Agrupa hilos abiertos por (entidad_canonizada, fecha_iso).
       - "entidad" = el token de persona/org mas largo y frecuente que aparece en los
         titulos del grupo (extraccion superficial: NO LLM, NO heuristicas de NLP pesadas).
       - "fecha_iso" = primera fecha YYYY-MM-DD o DD/MM extraida del titulo/plazo.
    2. Alta confianza (misma entidad + misma fecha + solapamiento de tokens >= SCORE_MIN):
       → FUSION: conserva el hilo MAS COMPLETO (mas campos no vacios + estado mas avanzado),
         marca los otros como 'hecho' con nota de fusion + ref al superviviente.
         Auditado en tools/state/dedup/<fecha>.jsonl.
    3. Baja confianza (misma entidad + fecha similar pero score bajo, o fecha incierta):
       → PROPUESTA a la cola de dudosas de Vega (triage_tareas._encolar_dudosa).
         Nunca funde a ciegas (regla: gestor-OK primero).
    4. Sin match → no toca nada.

Modos:
    audit  — solo reporta (nunca escribe). Default.
    fix    — aplica las fusiones de alta confianza + encola propuestas de baja confianza.

Garantias:
    · DETERMINISTA y sin LLM.
    · FAIL-SOFT: un JSON corrupto no tumba el modulo.
    · NO toca hilos de eventos distintos (la particion es conservadora).
    · NO toca hilos de cumbre (id 'cumbre:...').
    · Solo escribe en casa base via BTP_REPO/BTP_STATE_DIR.
    · Egress cero: ningun dato sale del sistema (la propuesta a Vega es local).

USO:
    python3 tools/dedup_hilos.py [audit|fix]
    python3 tools/dedup_hilos.py audit --verbose   # imprime grupos candidatos
"""
import os
import sys
import json
import re
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento as sg  # noqa: E402

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
DEDUP_DIR = os.path.join(STATE, "dedup")

# Puntuacion minima de solapamiento de tokens para considerar fusion de alta confianza.
# Subido a 3 el 30/6 tras un falso positivo en vivo: a score 2, dos items de viaje del MISMO
# dia con un token generico compartido (p.ej. "Canjear billete tren {{CONTACTO}}" + "Rock {{CONTACTO}}
# festival", o un vuelo + un tren del 1-jul) se fundian como si fueran el mismo evento. A 3 solo
# funde lo fuerte (persona + reunion, como los 4 hilos de Bernardo); lo de score 2 se PROPONE a Vega.
SCORE_MIN = 3

# Palabras que NO identifican entidad (ruido frecuente en titulos de este dominio).
_STOP_ENT = {
    "llamada", "reunion", "call", "cita", "meeting", "video", "google", "meet",
    "zoom", "teams", "titular", "contacto", "contacto", "zurch", "{{CIUDAD}}", "barcelona",
    "madrid", "biopsia", "tejido", "muestra", "vacuna", "tratamiento", "protocolo",
    "coordinar", "revisar", "confirmar", "enviar", "borrador", "correo", "email",
    "linkedin", "telegram", "whatsapp", "jueves", "viernes", "lunes", "martes",
    "mie", "mied", "mier", "jul", "jun", "may", "abr", "mar", "ene", "feb",
    "manana", "manana", "tarde", "noche", "mañana", "mediodía",
    "hoy", "mañ", "prox", "proxima", "siguiente", "pendiente", "urgente",
    "y", "de", "del", "la", "el", "los", "las", "a", "al", "en", "con", "para",
    "por", "un", "una", "the", "to", "of", "and", "or", "con", "para",
}


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return {"_error": "ilegible: %s" % os.path.basename(path)}


def _log(entrada):
    """Escribe en el diario de auditoria diario (append)."""
    try:
        os.makedirs(DEDUP_DIR, exist_ok=True)
        hoy = datetime.date.today().isoformat()
        with open(os.path.join(DEDUP_DIR, "auditoria-%s.jsonl" % hoy), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now_iso(), **entrada}, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Extraccion de fecha y entidad de un titulo ──────────────────────────────

_RE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_RE_DMY = re.compile(r"\b(\d{1,2})[-/](\d{1,2})(?:[-/](\d{4}))?\b")
_MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}
_RE_MES_TEXTO = re.compile(r"\b(\d{1,2})[-\s]+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\w*\b",
                            re.IGNORECASE)


def _fecha_de(hilo):
    """Extrae la fecha ISO mas relevante del hilo (plazo > titulo > None)."""
    # 1) plazo ISO directo
    plazo = hilo.get("plazo")
    if plazo and sg._parse_iso(plazo):
        return str(plazo)[:10]
    # 2) buscar ISO en el titulo
    titulo = hilo.get("titulo", "")
    m = _RE_ISO.search(titulo)
    if m:
        return m.group(1)
    # 3) "2/7" o "1-jul" en titulo
    m2 = _RE_MES_TEXTO.search(titulo)
    if m2:
        dd, mes_abr = int(m2.group(1)), m2.group(2).lower()[:3]
        mes = _MESES_ES.get(mes_abr)
        if mes:
            anio = datetime.date.today().year
            try:
                return datetime.date(anio, mes, dd).isoformat()
            except Exception:
                pass
    m3 = _RE_DMY.search(titulo)
    if m3:
        dd, mm = int(m3.group(1)), int(m3.group(2))
        anio = int(m3.group(3)) if m3.group(3) else datetime.date.today().year
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            try:
                return datetime.date(anio, mm, dd).isoformat()
            except Exception:
                pass
    return None


def _toks(texto):
    """Tokens significativos (3+ chars, no stopwords) del texto."""
    out = set()
    for raw in re.split(r"[^\w]+", (texto or "").lower()):
        if len(raw) >= 3 and raw not in _STOP_ENT:
            out.add(raw)
    return out


_STOP_ENT_EXTRA = {
    # Palabras de estado/accion que aparecen en quien_espera pero no son nombres.
    "confirmada", "confirmado", "confirmados", "cita", "listo", "listos",
    "borrador", "borradores", "pendiente", "pendientes",
    "bio", "mandas", "decisión", "decision", "elegir", "slot", "calendly",
    "presentarte", "quiere", "también", "tambien", "grupo", "circulo",
    "centuryofbio", "centuryof", "quiere", "también",
    # Verbos/sustantivos de accion que no identifican a una persona.
    "contacto", "contactos", "respuesta", "respuestas",
    "mensaje", "mensajes", "reunirse", "reunion",
}


def _entidad_de(hilo):
    """Extrae la entidad canonica: el primer nombre propio mas corto (mas especifico)
    del campo quien_espera, siempre que no empiece por 'tu'/'titular' y que no sea
    una palabra de accion/estado. Si quien_espera no aplica, usa el titulo.

    La heuristica prefiere la primera palabra de 5-10 chars antes que la mas larga,
    para evitar que terminos de estado (confirmada=11) superen nombres (cordovez=8).
    Conservador: devuelve "" si no hay candidato claro."""
    _combined_stop = _STOP_ENT | _STOP_ENT_EXTRA

    espera = (hilo.get("quien_espera") or "").strip().lower()
    # Ignorar si quien_espera es del propio lado ('tú', 'titular', 'ella', 'yo'...).
    if not espera or espera.startswith(("tú", "tu", "titular", "ella", "yo")):
        pass  # no usar quien_espera
    else:
        espera_toks = [t for t in re.split(r"[^\w]+", espera)
                       if len(t) >= 4 and t not in _combined_stop]
        if espera_toks:
            # Preferir el primer token de 5-10 chars (evitar palabras de estado largas).
            candidatos_nombre = [t for t in espera_toks if 5 <= len(t) <= 10]
            if candidatos_nombre:
                return candidatos_nombre[0]
            return espera_toks[0]

    # Fallback al titulo.
    titulo_toks = [t for t in _toks(hilo.get("titulo", ""))
                   if t not in _combined_stop]
    if titulo_toks:
        # En el titulo preferimos el token mas largo (nombres en titulos suelen ser descriptivos).
        return max(titulo_toks, key=len)
    return ""


def _completitud(hilo):
    """Puntuacion de completitud: cuenta campos no vacios y no None.
    El hilo mas completo sera el superviviente en una fusion."""
    campos = ("titulo", "estado", "siguiente_accion", "quien_espera", "plazo",
              "esperando_desde", "categoria", "etiqueta", "origen", "dueno",
              "objetivo_ned", "ref_cumbre", "por_que")
    score = sum(1 for c in campos if hilo.get(c))
    # Estado mas avanzado da bonus (hecho > esperando > en_curso > por_confirmar).
    ORDEN = {"hecho": 10, "esperando": 3, "en_curso": 2, "por_confirmar": 1, "bloqueado": 2}
    score += ORDEN.get(hilo.get("estado", ""), 0)
    return score


def _abierto(h):
    return h.get("estado") not in ("hecho",) and not str(h.get("id", "")).startswith("cumbre:")


def _proponer(texto, origen, campos=None):
    try:
        import triage_tareas
        triage_tareas._encolar_dudosa(texto, origen, campos or {})
        return True
    except Exception:
        return False


# ── Nucleo del dedup ──────────────────────────────────────────────────────────

def _tokens_nombre(hilo):
    """Conjunto de tokens de nombre (4+ chars, no stopwords combinadas) que identifican
    a la(s) entidad(es) protagonistas del hilo. Se busca en titulo + quien_espera."""
    combined_stop = _STOP_ENT | _STOP_ENT_EXTRA
    toks = set()
    for campo in ("titulo", "quien_espera"):
        texto = (hilo.get(campo) or "").lower()
        for t in re.split(r"[^\w]+", texto):
            if len(t) >= 4 and t not in combined_stop:
                toks.add(t)
    return toks


def detectar_grupos(hilos):
    """Agrupa hilos abiertos por fecha ISO y solapamiento de tokens de nombre.

    Algoritmo en dos fases:
    1. Particiona por fecha ISO (extraida de plazo/titulo). Hilos sin fecha se descartan
       (demasiado ambiguos).
    2. Dentro de cada fecha, agrupa hilos que comparten al menos 1 token de nombre
       en comun (union-find conservador: hilos con >=1 token comun van al mismo grupo).

    Devuelve lista de grupos [lista_de_hilos] con len >= 2 (candidatos a dedup).
    """
    por_fecha = defaultdict(list)
    for h in hilos:
        if not _abierto(h):
            continue
        fecha = _fecha_de(h)
        if not fecha:
            continue  # sin fecha: demasiado ambiguo
        toks = _tokens_nombre(h)
        if not toks:
            continue  # sin tokens de nombre: no se puede identificar entidad
        por_fecha[fecha].append((h, toks))

    grupos_out = []
    for fecha, items in por_fecha.items():
        if len(items) < 2:
            continue
        # Union-find: conectar hilos que comparten >=1 token de nombre.
        n = len(items)
        padre = list(range(n))

        def find(x):
            while padre[x] != x:
                padre[x] = padre[padre[x]]
                x = padre[x]
            return x

        def union(x, y):
            padre[find(x)] = find(y)

        for i in range(n):
            for j in range(i + 1, n):
                if items[i][1] & items[j][1]:  # >=1 token comun
                    union(i, j)

        componentes = defaultdict(list)
        for i in range(n):
            componentes[find(i)].append(items[i][0])
        for comp in componentes.values():
            if len(comp) >= 2:
                grupos_out.append(comp)

    return grupos_out


def _score_solapamiento(grupo):
    """Solapamiento de tokens entre todos los pares del grupo. Devuelve el minimo
    (el par con menor solapamiento marca el nivel de confianza del grupo entero)."""
    titulos = [_toks(h.get("titulo", "")) for h in grupo]
    if len(titulos) < 2:
        return 0
    min_score = float("inf")
    for i in range(len(titulos)):
        for j in range(i + 1, len(titulos)):
            s = len(titulos[i] & titulos[j])
            if s < min_score:
                min_score = s
    return min_score if min_score != float("inf") else 0


def _fundir(superviviente, absorbidos, aplicar=False):
    """Marca los absorbidos como 'hecho' con nota de fusion. Si aplicar=False, solo
    describe la accion (audit). Devuelve lista de acciones realizadas."""
    acciones = []
    sid = superviviente.get("id")
    for h in absorbidos:
        hid = h.get("id")
        nota = "Fundido en %s (dedup_hilos: mismo evento, alta confianza). Titulo original: %s" % (
            sid, h.get("titulo", ""))
        accion = {"tipo": "fusion", "superviviente": sid, "absorbido": hid,
                  "titulo_absorbido": h.get("titulo", ""), "titulo_superviviente": superviviente.get("titulo", "")}
        if aplicar:
            try:
                # Actualizar el absorbido con estado hecho + nota de fusion.
                sg.add_hilo({
                    "id": hid,
                    "titulo": h.get("titulo", ""),
                    "estado": "hecho",
                    "siguiente_accion": nota,
                    "origen": h.get("origen", "dedup"),
                })
                accion["aplicado"] = True
                _log({"accion": "fusion", **accion})
            except Exception as e:
                accion["error"] = str(e)
                accion["aplicado"] = False
        else:
            accion["aplicado"] = False
        acciones.append(accion)
    return acciones


def dedup(aplicar=False, verbose=False):
    """Detecta y (si aplicar=True) funde duplicados de alta confianza; propone los de
    baja confianza a Vega. Devuelve un reporte estructurado. NUNCA lanza por datos sucios."""
    rep = {
        "status": "ok",
        "modo": "fix" if aplicar else "audit",
        "grupos_analizados": 0,
        "fusiones": [],         # alta confianza, aplicadas (o pendientes en audit)
        "propuestas": [],       # baja confianza, encoladas a Vega (o pendientes en audit)
        "errores": [],
    }

    seg = sg.load_seguimiento()
    if seg.get("_error"):
        rep["status"] = "error"
        rep["errores"].append(seg["_error"])
        return rep

    hilos = seg.get("hilos", [])
    grupos = detectar_grupos(hilos)
    rep["grupos_analizados"] = len(grupos)

    for grupo in grupos:
        score = _score_solapamiento(grupo)
        ent = _entidad_de(grupo[0])
        fecha = _fecha_de(grupo[0])

        if verbose:
            print("  GRUPO [ent=%s fecha=%s score=%d]:" % (ent, fecha, score))
            for h in grupo:
                print("    · [%s] %s (%s)" % (h.get("id"), h.get("titulo"), h.get("estado")))

        if score >= SCORE_MIN:
            # Alta confianza: fundir.
            # Superviviente = el mas completo; en empate, el primero en la lista.
            grupo_ord = sorted(grupo, key=_completitud, reverse=True)
            superviviente = grupo_ord[0]
            absorbidos = grupo_ord[1:]
            acciones = _fundir(superviviente, absorbidos, aplicar=aplicar)
            rep["fusiones"].append({
                "entidad": ent, "fecha": fecha, "score": score,
                "superviviente": superviviente.get("id"),
                "superviviente_titulo": superviviente.get("titulo"),
                "absorbidos": [a["absorbido"] for a in acciones],
                "aplicado": aplicar,
            })
        else:
            # Baja confianza: proponer a Vega.
            ids = [h.get("id") for h in grupo]
            titulos = [h.get("titulo", "") for h in grupo]
            texto = ("Posibles duplicados del evento '%s' (%s): %s — ¿los fundimos en uno?" %
                     (ent, fecha, " | ".join(titulos)))
            rep["propuestas"].append({
                "entidad": ent, "fecha": fecha, "score": score,
                "ids": ids, "texto": texto,
            })
            if aplicar:
                _proponer(texto, "dedup_hilos", {"ids": ids, "entidad": ent, "fecha": fecha})

    return rep


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv):
    modo = "audit"
    verbose = False
    for a in argv:
        if a in ("audit", "fix"):
            modo = a
        elif a in ("--verbose", "-v"):
            verbose = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0

    rep = dedup(aplicar=(modo == "fix"), verbose=verbose)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
