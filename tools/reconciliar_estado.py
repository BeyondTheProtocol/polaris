#!/usr/bin/env python3
"""tools/reconciliar_estado.py — reconciliador determinista de estado del lazo.

Por qué existe: el estado de Polaris vive en varios almacenes (reservas.json, cumbre.json,
correo/buzon.json) y la FUENTE ÚNICA operativa (seguimiento.json) no los
cosía. Resultado: dos sesiones leían fotos distintas (caso 30/6: vuelo de {{CONTACTO}} RESERVADO_PAGADO
en reservas, pero su hilo seguía "en_curso" → otra sesión creía que estaba pendiente).

Qué hace: cruza cada almacén satélite contra seguimiento.json y deja UNA verdad:
  · reserva RESUELTA (reservado/pagado/cancelado) con hilo gemelo 1:1 abierto → CIERRA el hilo
    (determinista, reversible, registrado). Reflejar la realidad, no decidir.
  · reserva 'a_un_clic' sin hilo → PROPONE a Vega (cola de dudosas), NO crea tarjeta a discreción.
  · fase de cumbre ya 'hecho' con hilos abiertos enlazados → PROPONE revisión (clínico: nunca
    auto-cierra — el muro pesa sobre la comodidad).
  · correo NED-crítico sin hilo → PROPONE a Vega (backstop de correo.py).

Garantías: DETERMINISTA (sin LLM), SIN red, FAIL-SOFT (un JSON corrupto no lo tumba). Solo lee y
escribe estado LOCAL. Único egress = salida.report_to_titular (gated, anti-spam, respeta HALT);
jamás hacia fuera. Escribe SIEMPRE en casa base (BTP_REPO→~/claudecode), aunque corra desde un
worktree. Modos: `audit` (solo reporta) · `fix` (cierra resueltos + encola propuestas).
"""
import os
import sys
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento as sg  # noqa: E402

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
RESERVAS = os.path.join(STATE, "reservas.json")
BUZON = os.path.join(STATE, "correo", "buzon.json")
RECON_DIR = os.path.join(STATE, "reconciliador")
SEEN = os.path.join(RECON_DIR, "seen.json")

COOLDOWN_H = 12  # mismo aviso, máx 1×/12h (patrón vigia/healthcheck)

# Estados de reserva que cuentan como RESUELTA (case-insensitive; la data viva usa variantes en
# mayúsculas tipo RESERVADO_PAGADO que NO están en el enum de reservas.py — por eso normalizamos).
_RESUELTA_MARCAS = ("reservad", "pagad", "cancelad")
# Verbos/sustantivos que marcan que un hilo ES una tarea de reserva (no cualquier hilo).
_VERBO_RESERVA = ("reservar", "reserva", "vuelo", "billete", "tren", "hotel", "apartamento",
                  "alojamiento", "traslado", "taxi", "transfer", "seguro")
# Señales de que un hilo agrupa MÁS que la reserva → NO auto-cerrar (proponer).
_AMPLIO = (" y ", " + ", "coordinar", "checklist", "protocolo", "lab", "reunión", "reunion",
           "muestra", "varios", "organizar")
# Stopwords para el match difuso de títulos.
_STOP = {"de", "del", "la", "el", "los", "las", "a", "al", "en", "con", "para", "por", "y", "o",
         "un", "una", "the", "to", "of", "plan", "tarde", "noche", "ida", "vuelta", "directo",
         "media", "mañana", "manana", "economy", "su", "el/la"}


def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        # corrupto: fail-soft, pero lo dejamos saber al llamador con un centinela.
        return {"_error": "ilegible: %s" % os.path.basename(path)}


def _toks(s):
    """Tokens significativos de un título (para match difuso conservador)."""
    out = set()
    for raw in "".join(c if c.isalnum() else " " for c in (s or "").lower()).split():
        if len(raw) >= 3 and raw not in _STOP:
            out.add(raw)
        elif raw.isupper() and len(raw) == 3:  # códigos de aeropuerto (BOS/ZRH) ya en minúscula aquí
            out.add(raw)
    return out


_PREFIJOS_ASUNTO = ("re:", "aw:", "fwd:", "fw:", "rv:", "ext:", "[extern]")
_AUTORESP = ("resposta automàtica", "respuesta automática", "automatic reply",
             "out of office", "fuera de la oficina", "auto-reply", "delivery status")


def _norm_asunto(s):
    """Normaliza un asunto a su HILO: quita prefijos de respuesta/reenvío repetidos y baja a
    minúsculas, para deduplicar 'RE: X' / 'AW: X' como el mismo hilo."""
    t = (s or "").strip().lower()
    cambiado = True
    while cambiado:
        cambiado = False
        for p in _PREFIJOS_ASUNTO:
            if t.startswith(p):
                t = t[len(p):].strip(" :")
                cambiado = True
    return t


def _es_autorespuesta(asunto):
    a = (asunto or "").lower()
    return any(m in a for m in _AUTORESP)


def _resuelta(estado):
    e = (estado or "").lower()
    return any(m in e for m in _RESUELTA_MARCAS)


def _es_tarea_reserva(titulo):
    t = (titulo or "").lower()
    return any(v in t for v in _VERBO_RESERVA)


def _es_amplio(titulo):
    t = (titulo or "").lower()
    return any(a in t for a in _AMPLIO)


def _abierto(hilo):
    return hilo.get("estado") not in ("hecho",)


def _gemelo(enc, hilos):
    """Encuentra el hilo gemelo de una reserva. Devuelve (confianza, hilo) o (None, None).
    confianza: 'link' (enlace explícito, seguro) | 'fuzzy' (match de títulos, requiere prudencia)."""
    eid = enc.get("id")
    # 1) Enlace explícito (lo más seguro).
    for h in hilos:
        if h.get("ref_reserva") and h["ref_reserva"] == eid:
            return ("link", h)
        if enc.get("ref_hilo") and enc["ref_hilo"] == h.get("id"):
            return ("link", h)
    # 2) Match difuso conservador: solapamiento de tokens significativos + el hilo es de reserva.
    et = _toks(enc.get("titulo"))
    mejor, mejor_score = None, 0
    for h in hilos:
        if not _abierto(h) or not _es_tarea_reserva(h.get("titulo")):
            continue
        score = len(et & _toks(h.get("titulo")))
        if score > mejor_score:
            mejor, mejor_score = h, score
    if mejor and mejor_score >= 2:   # ≥2 tokens fuertes compartidos (p. ej. nombre + destino)
        return ("fuzzy", mejor)
    return (None, None)


def _proponer(texto, origen, campos=None):
    """Encola una PROPUESTA en la cola de dudosas de Vega (no crea tarjeta). Dedup por texto."""
    try:
        import triage_tareas
        triage_tareas._encolar_dudosa(texto, origen, campos or {})
        return True
    except Exception:
        return False


def _enlazar(hid, eid):
    try:
        sg.enlazar_reserva(hid, eid)
        return True
    except Exception:
        return False


def _log(accion):
    try:
        os.makedirs(RECON_DIR, exist_ok=True)
        with open(os.path.join(RECON_DIR, "auditoria-%s.jsonl" % datetime.date.today().isoformat()),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now_iso(), **accion}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def reconciliar(aplicar=False):
    """Cruza los satélites contra seguimiento.json. aplicar=False → solo reporta (audit).
    Devuelve un reporte estructurado. NUNCA lanza por datos sucios (fail-soft)."""
    rep = {"status": "ok", "modo": "fix" if aplicar else "audit",
           "inconsistentes": [], "huerfanos": [], "propuestas": [],
           "cerrados": [], "enlazados": [], "errores": []}

    seg = sg.load_seguimiento()
    if seg.get("_error"):
        rep["status"] = "error"
        rep["errores"].append(seg["_error"])
        return rep
    hilos = seg.get("hilos", [])

    # ── 1) reservas.json ↔ seguimiento.json ──────────────────────────────────
    res = _load_json(RESERVAS, {"encargos": []})
    if isinstance(res, dict) and res.get("_error"):
        rep["errores"].append("reservas.json " + res["_error"])
        encargos = []
    else:
        encargos = res.get("encargos", []) if isinstance(res, dict) else []

    for enc in encargos:
        if not isinstance(enc, dict):
            continue
        eid, titulo, estado = enc.get("id"), enc.get("titulo", ""), enc.get("estado", "")
        conf, h = _gemelo(enc, hilos)
        if _resuelta(estado):
            # Reserva resuelta. ¿Su hilo sigue abierto? → inconsistencia a cerrar.
            if h and _abierto(h):
                # Solo auto-cierra si es seguro: enlace explícito, o difuso de alcance ESTRECHO.
                seguro = (conf == "link") or (conf == "fuzzy" and not _es_amplio(h.get("titulo")))
                if not seguro:
                    rep["propuestas"].append({"tipo": "revisar_cierre", "reserva": eid,
                                              "hilo": h.get("id"), "titulo": h.get("titulo"),
                                              "motivo": "reserva resuelta pero el hilo agrupa más trabajo"})
                    if aplicar:
                        _proponer("Revisar si cerrar el hilo «%s» (la reserva ya está %s, pero el hilo "
                                  "parece cubrir más)." % (h.get("titulo"), estado),
                                  "reconciliador", {"hilo": h.get("id"), "reserva": eid})
                    continue
                rep["inconsistentes"].append({"reserva": eid, "estado_reserva": estado,
                                              "hilo": h.get("id"), "hilo_estado": h.get("estado"),
                                              "confianza": conf})
                if aplicar:
                    if conf != "link":
                        if _enlazar(h.get("id"), eid):
                            rep["enlazados"].append({"hilo": h.get("id"), "reserva": eid})
                    try:
                        sg.set_estado(h.get("id"), "hecho")
                        rep["cerrados"].append({"hilo": h.get("id"), "titulo": h.get("titulo"),
                                                "por": "reserva %s (%s)" % (eid, estado)})
                        _log({"accion": "cerrar_hilo", "hilo": h.get("id"), "reserva": eid,
                              "estado_reserva": estado, "confianza": conf})
                    except Exception as e:
                        rep["errores"].append("cerrar %s: %r" % (h.get("id"), e))
        elif estado == "a_un_clic":
            # Encargo a la espera de la firma de {{TITULAR}}, sin hilo que lo siga → proponer.
            # SOLO logística/compromiso (viaje/cita): las compras del carrito (gadgets) NO son
            # tareas de seguimiento (las posee el Conserje); proponerlas spamearía a Vega.
            if enc.get("categoria") not in ("viaje", "cita"):
                continue
            if not h:
                rep["huerfanos"].append({"reserva": eid, "titulo": titulo})
                if aplicar:
                    if _proponer("¿Seguir como tarea la reserva pendiente de tu firma: «%s»?" % titulo,
                                 "reconciliador", {"reserva": eid, "etiqueta": "Gestión"}):
                        rep["propuestas"].append({"tipo": "nuevo_hilo", "reserva": eid, "titulo": titulo})
                        _log({"accion": "proponer_hilo", "reserva": eid})

    # ── 2) cumbre.json: fases resueltas con hilos enlazados abiertos → PROPONER ───
    cumbre = sg.load_cumbre()
    fases_hechas = {s.get("id") for s in cumbre.get("salientes", []) if s.get("estado") == "hecho"}
    for h in hilos:
        rc = h.get("ref_cumbre")
        if rc and rc in fases_hechas and _abierto(h):
            rep["propuestas"].append({"tipo": "revisar_cierre_clinico", "hilo": h.get("id"),
                                      "titulo": h.get("titulo"), "fase": rc})
            if aplicar:
                _proponer("Revisar el hilo «%s»: la fase clínica enlazada (%s) figura ya resuelta." %
                          (h.get("titulo"), rc), "reconciliador", {"hilo": h.get("id")})

    # ── 3) correo/buzon.json: NED-crítico sin hilo gemelo → PROPONER (backstop) ───
    buz = _load_json(BUZON, {})
    msgs = []
    if isinstance(buz, dict) and not buz.get("_error"):
        for k, v in buz.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "asunto" in v[0]:
                msgs = v
                break
    vistos_asunto = set()
    for m in msgs:
        if not (m.get("ned_critico") and not m.get("inyeccion")):
            continue
        asunto = m.get("asunto", "")
        norm = _norm_asunto(asunto)
        if not norm or _es_autorespuesta(asunto):
            continue
        if norm in vistos_asunto:
            continue  # mismo HILO (RE:/AW: del mismo asunto) → una sola propuesta
        vistos_asunto.add(norm)
        at = _toks(norm)
        if any(len(at & _toks(h.get("titulo"))) >= 2 for h in hilos):
            continue  # ya hay un hilo relacionado
        rep["propuestas"].append({"tipo": "correo_sin_hilo", "asunto": asunto[:70],
                                  "de": m.get("remitente", "")})
        if aplicar:
            _proponer("Correo NED sin hilo: «%s» (de %s) — ¿lo seguimos?" %
                      (asunto[:70], m.get("remitente", "")), "reconciliador", {"etiqueta": "NED"})

    # ── 4) tareas.json legacy: RETIRADO el 14-jul-26 ──
    # Este bloque denunciaba cada dia que el almacen obsoleto tenia entradas vivas... y
    # nadie hacia nada con la denuncia. Sus 7 tareas se rescataron a seguimiento.json y el
    # almacen se borro. Ya no hay legacy que reconciliar.

    return rep


# ── Aviso anti-spam ──────────────────────────────────────────────────────────
def _firma(rep):
    import hashlib
    base = json.dumps({k: rep[k] for k in ("inconsistentes", "huerfanos")},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _debe_avisar(firma):
    seen = _load_json(SEEN, {})
    if not isinstance(seen, dict):
        seen = {}
    last = seen.get(firma)
    if last:
        try:
            t = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%S")
            if (datetime.datetime.now() - t).total_seconds() < COOLDOWN_H * 3600:
                return False
        except Exception:
            pass
    seen[firma] = _now_iso()
    try:
        os.makedirs(RECON_DIR, exist_ok=True)
        tmp = SEEN + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SEEN)
    except Exception:
        pass
    return True


def _avisar(rep):
    n_cerr = len(rep["cerrados"])
    n_prop = len(rep["propuestas"])
    if not (n_cerr or n_prop):
        return
    if not _debe_avisar(_firma(rep)):
        return
    partes = []
    if n_cerr:
        partes.append("cerré %d cosa(s) que ya estaban hechas (lo tenía apuntado como pendiente "
                      "sin estarlo)" % n_cerr)
    if n_prop:
        partes.append("dejé %d cosa(s) para que las revisemos" % n_prop)
    txt = "Puse al día el seguimiento del viaje y las reservas: " + " y ".join(partes) + "."
    try:
        import salida
        salida.report_to_titular(txt)
    except Exception:
        pass


def main(argv):
    modo = argv[0] if argv else "audit"
    if modo not in ("audit", "fix"):
        print("uso: reconciliar_estado.py [audit|fix]")
        return 2
    rep = reconciliar(aplicar=(modo == "fix"))
    if modo == "fix":
        _avisar(rep)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
