#!/usr/bin/env python3
"""tools/reservas.py — El Conserje: motor DETERMINISTA de reservas ("que se reserven solas,
con cabeza y con tu firma").

Recibe ENCARGOS de reserva ya pensados por un comité (un viaje de La Órbita, una compra del
asistente de Amazon, una cita, una suscripción), decide el RIESGO con una regla fija, y los
deja en uno de dos carriles:

  · 🟢 VERDE  — bajo riesgo, reembolsable, decidido por {{TITULAR}} y dentro del "sobre" que ella
                pre-aprueba → carril auto (lo reservaría solo). EN F1 ESTÁ DESACTIVADO
                (AUTO_PAGO_ACTIVO=False): hasta F3 incluso lo verde se deja "a un clic".
  · 🟠 ÁMBAR / 🔴 ROJO — caro, no reembolsable, irreversible o decisión nueva → "a un clic":
                se deja todo listo y se avisa a {{TITULAR}} para que dé su Touch ID. SU GATE.

Reglas duras (el muro):
  · El RIESGO sale SOLO de números y enums (importe, reembolsable, categoría, decisión-tomada),
    NUNCA de texto libre. Una orden plantada en la prosa de un encargo ("URGENTE, reserva ya")
    no puede bajar la guardia: cierra el vector de inyección. El título/razones son SOLO display.
  · FAIL-CLOSED: si algo no es DEMOSTRABLEMENTE verde, NO es verde. Ante la duda → ámbar (a un
    clic). Importe desconocido o irreversible → rojo. Nunca se reserva a ciegas.
  · Este motor NO paga, NO contacta a nadie, NO rellena tarjetas. Solo clasifica, guarda estado
    y AVISA a {{TITULAR}} (salida.report_to_titular, que respeta HALT/anti-spam/silencio nocturno).
    El rellenado real en el navegador (con la extensión de 1Password) es F2, en sesión, no aquí.
  · La tarjeta NUNCA pasa por el agente: la rellena la extensión de 1Password en la web. Aquí
    no se guardan credenciales ni datos de pago (solo metadatos del encargo).

Sin dependencias (stdlib). Patrón de seguimiento.py / cola.py.
"""
import json
import os
import sys
import time

# El estado VIVO vive SOLO en casa base (tools/state/ está gitignored, NO viaja a los worktrees).
# Resolvemos casa base (BTP_REPO o ~/claudecode), igual que seguimiento.py/cola.py: un encargo
# creado desde un worktree debe aterrizar en la ÚNICA libreta viva, no en una copia desechable.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
RESERVAS = os.path.join(STATE, "reservas.json")
SOBRE = os.path.join(STATE, "reservas_sobre.json")

# ─────────────────────────────────────────────────────────────────────────────
# 🔒 EL INTERRUPTOR DEL CARRIL AUTO. En F1 está APAGADO: aunque un encargo salga VERDE, se deja
# "a un clic" (cero auto-pago). Encenderlo es un acto de F3 + gate de {{TITULAR}} (sobre acotado +
# tarjeta virtual con tope), no un flip a la ligera. Se deja como constante para que la auditoría
# (audit_*) y El Observatorio lo vean explícito.
AUTO_PAGO_ACTIVO = False
# ─────────────────────────────────────────────────────────────────────────────

# Categorías conocidas (allowlist consumer-first: una categoría desconocida NO se acepta a ciegas).
CATEGORIAS = ("viaje", "compra", "cita", "suscripcion", "otros")
RIESGOS = ("verde", "ambar", "rojo")
ESTADOS = ("pendiente", "a_un_clic", "auto_listo", "reservado", "cancelado")

# ─────────────────────────────────────────────────────────────────────────────
# 🛒 UN CARRITO NO ES UNA DECISIÓN (norma `feedback-carrito-no-es-decidido`, clase BLOQUEO).
# {{TITULAR}}: «el carrito/lista de deseos de Amazon (o cualquier "guardado") NO significa decidido —
# no reservar/comprar algo solo porque esté ahí».
#
# `clasificar()` ya exigía `decision_tomada` para el carril verde, así que el freno EXISTÍA…
# pero se fiaba del booleano que le pasaran. Y el estado vivo del 18-sep-26 enseña que ese
# booleano no era de fiar: de 24 encargos, **14 declaraban `decision_tomada=True` desde un
# origen que no es una decisión suya** —
#     1 · `amazon-carrito`        ← la norma, literal: el carrito marcado como decidido
#     6 · `propuesta-conserje`    ← una propuesta MÍA contada como decisión SUYA
#     7 · `estudio-viaje-julio`   ← un estudio contado como decisión
# frente a 9 de `agencia-viajes`, que sí es una confirmación suya (así lo manda su charter).
#
# Ninguno llegó a comprarse porque en F1 `AUTO_PAGO_ACTIVO=False` apaga hasta lo verde: el daño
# era LATENTE, esperando a F3 — que es exactamente el momento en que ese booleano decidiría si
# algo se paga solo. Por eso el arreglo no es "avisar": es que la PROCEDENCIA mande sobre el
# booleano. Un origen que es un guardado, una propuesta o un estudio NO puede sostener
# `decision_tomada`, lo pase quien lo pase.
ORIGENES_NO_DECIDEN = (
    "carrito", "cart", "wishlist", "lista-deseos", "lista_deseos", "deseos",
    "guardado", "guardados", "saved", "favorito", "favoritos",
    "propuesta", "sugerencia", "estudio", "radar", "borrador", "idea", "candidato",
)


def decision_no_vale(origen):
    """(True, motivo) si ese ORIGEN no puede sostener `decision_tomada`. Fail-closed."""
    o = (origen or "").strip().lower()
    for marca in ORIGENES_NO_DECIDEN:
        if marca in o:
            return True, ("origen '%s' es un guardado/propuesta, no una decisión suya" % origen)
    return False, None

# Sobre 🟢 por defecto: CONSERVADOR a propósito. Editarlo es el gate de {{TITULAR}} (`sobre set`).
SOBRE_DEFAULT = {
    "tope_verde_eur": 60.0,        # auto-candidato solo por debajo de esto
    "tope_rojo_eur": 400.0,        # por encima: SIEMPRE rojo (a un clic, sin excepción)
    "solo_reembolsable": True,     # el carril verde exige reembolsable/cancelable
    "categorias_verde": ["viaje", "cita", "suscripcion"],  # compras grandes fuera por defecto
    # Pista (NO secreto) de QUÉ tarjeta usar en el checkout: el nombre del ítem en 1Password.
    # La extensión de 1Password rellena el número; aquí solo guardamos la etiqueta para que
    # conserje-web elija la tarjeta correcta sin dudar. La tarjeta y su tope viven en Revolut.
    "metodo_pago": "",
}


# ── E/S de estado ────────────────────────────────────────────────────────────
def _write_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def cargar_sobre():
    """Lee el sobre 🟢. Si no existe o está corrupto, devuelve el DEFAULT conservador (fail-safe:
    ante un sobre ilegible nunca se ensancha el carril auto)."""
    try:
        with open(SOBRE, encoding="utf-8") as f:
            data = json.load(f)
        return {**SOBRE_DEFAULT, **{k: data[k] for k in SOBRE_DEFAULT if k in data}}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(SOBRE_DEFAULT)


def guardar_sobre(cambios):
    """Aplica cambios al sobre 🟢 (gate de {{TITULAR}}). Valida tipos/coherencia fail-closed."""
    sobre = cargar_sobre()
    for k, v in cambios.items():
        if k not in SOBRE_DEFAULT:
            raise ValueError("campo de sobre desconocido: %r" % k)
        sobre[k] = v
    if not (0 <= sobre["tope_verde_eur"] <= sobre["tope_rojo_eur"]):
        raise ValueError("incoherente: 0 ≤ tope_verde ≤ tope_rojo")
    cats = [c for c in sobre["categorias_verde"] if c in CATEGORIAS]
    sobre["categorias_verde"] = cats
    _write_atomic(SOBRE, sobre)
    return sobre


def _load():
    try:
        with open(RESERVAS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"encargos": []}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _slug(s):
    out = "".join(c if c.isalnum() else "-" for c in (s or "").lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "encargo"


# ── El cerebro: clasificador de riesgo (determinista, fail-closed) ───────────
def clasificar(enc, sobre=None):
    """Devuelve (riesgo, razones). SOLO mira números/bool/enum, jamás texto libre.

    🔴 ROJO   si: irreversible, o importe desconocido, o importe > tope_rojo.
    🟢 VERDE  si TODO se cumple: categoría en el sobre, importe ≤ tope_verde, (reembolsable si el
              sobre lo exige) y la decisión la tomó {{TITULAR}}.
    🟠 ÁMBAR  todo lo demás (el cajón seguro: a un clic)."""
    sobre = sobre or cargar_sobre()
    importe = enc.get("importe_eur")
    reemb = enc.get("reembolsable")          # True / False / None
    decidida = bool(enc.get("decision_tomada"))
    cat = enc.get("categoria")
    # La procedencia manda sobre el booleano. Va aquí y no solo en `crear()` a propósito: así
    # los encargos que YA están guardados con la contradicción tampoco pueden colarse al verde,
    # sin tener que reescribir el estado vivo por debajo.
    mal_origen, motivo_origen = decision_no_vale(enc.get("origen"))
    if mal_origen:
        decidida = False

    # 🔴 ROJO — irreversibles y desconocidos NO se tocan sin firma.
    if enc.get("irreversible") is True:
        return "rojo", ["marcado como irreversible"]
    if not isinstance(importe, (int, float)):
        return "rojo", ["importe desconocido — no se reserva a ciegas"]
    if importe > sobre["tope_rojo_eur"]:
        return "rojo", ["%.2f€ supera el tope rojo (%.2f€)" % (importe, sobre["tope_rojo_eur"])]

    # 🟢 VERDE — exige TODO (cualquier fallo lo expulsa del carril auto).
    falla = []
    if cat not in sobre["categorias_verde"]:
        falla.append("categoría '%s' no está en tu sobre verde" % cat)
    if importe > sobre["tope_verde_eur"]:
        falla.append("%.2f€ supera el tope verde (%.2f€)" % (importe, sobre["tope_verde_eur"]))
    if sobre["solo_reembolsable"] and reemb is not True:
        falla.append("no consta reembolsable/cancelable")
    if not decidida:
        falla.append(motivo_origen if mal_origen else "la decisión no la has confirmado tú aún")
    if not falla:
        return "verde", ["dentro del sobre (%.2f€, %s, reembolsable)" % (importe, cat)]

    # 🟠 ÁMBAR — el resto: a un clic.
    return "ambar", falla


# ── Ciclo de vida del encargo ────────────────────────────────────────────────
def crear(titulo, categoria, *, importe_eur=None, reembolsable=None, decision_tomada=False,
          irreversible=False, url="", origen="manual", ned="", datos=None):
    """Registra un encargo de reserva (lo emite un comité o un humano). NO reserva nada: solo lo
    encola para clasificar. `datos` = metadatos del encargo (fechas, nº pasajeros…), NUNCA tarjeta."""
    titulo = (titulo or "").strip()
    if not titulo:
        raise ValueError("el encargo necesita un título")
    if categoria not in CATEGORIAS:
        raise ValueError("categoría desconocida: %r (válidas: %s)" % (categoria, ", ".join(CATEGORIAS)))
    data = _load()
    base = _slug(titulo)
    ids = {e["id"] for e in data["encargos"]}
    eid, n = base, 2
    while eid in ids:
        eid, n = "%s-%d" % (base, n), n + 1
    # Un guardado/propuesta no sostiene una decisión: se REBAJA al registrarlo, y queda dicho
    # por qué (si no, el encargo mentiría en el estado vivo y nadie lo vería nunca).
    mal_origen, motivo_origen = decision_no_vale(origen)
    if mal_origen and decision_tomada:
        decision_tomada = False
    enc = {
        "id": eid, "titulo": titulo, "categoria": categoria,
        "importe_eur": (float(importe_eur) if isinstance(importe_eur, (int, float)) else None),
        "reembolsable": (bool(reembolsable) if reembolsable is not None else None),
        "decision_tomada": bool(decision_tomada),
        "decision_rebajada": (motivo_origen if mal_origen else None),
        "irreversible": bool(irreversible),
        "url": (url or "").strip(), "origen": (origen or "manual").strip(),
        "ned": (ned or "").strip(), "datos": (datos or {}),
        "estado": "pendiente", "riesgo": None, "razones": [],
        "ref_hilo": None,   # hilo de seguimiento.json enlazado (puente reservas↔tareas)
        "avisado": False, "creado": _now(), "actualizado": _now(),
    }
    data["encargos"].append(enc)
    _write_atomic(RESERVAS, data)
    return enc


def procesar():
    """Clasifica todo encargo PENDIENTE y lo enruta. Devuelve la lista de los que cambiaron.
    En F1 (AUTO_PAGO_ACTIVO=False) hasta lo verde acaba en 'a_un_clic'. Idempotente."""
    data = _load()
    sobre = cargar_sobre()
    cambiados = []
    for enc in data["encargos"]:
        if enc.get("estado") != "pendiente":
            continue
        riesgo, razones = clasificar(enc, sobre)
        enc["riesgo"], enc["razones"] = riesgo, razones
        if riesgo == "verde" and AUTO_PAGO_ACTIVO:
            enc["estado"] = "auto_listo"     # carril auto (F3+); hoy inalcanzable por el flag
        else:
            enc["estado"] = "a_un_clic"
        enc["actualizado"] = _now()
        cambiados.append(enc)
    if cambiados:
        _write_atomic(RESERVAS, data)
    return cambiados


def set_estado(eid, estado):
    """Mueve un encargo (p.ej. 'reservado' tras la firma de {{TITULAR}}, o 'cancelado'). LOCAL."""
    if estado not in ESTADOS:
        raise ValueError("estado inválido: %r" % estado)
    data = _load()
    for enc in data["encargos"]:
        if enc["id"] == eid:
            enc["estado"] = estado
            enc["actualizado"] = _now()
            _write_atomic(RESERVAS, data)
            return enc
    raise KeyError("no existe el encargo: %s" % eid)


_ICONO = {"verde": "🟢", "ambar": "🟠", "rojo": "🔴"}


def avisar(dry=False):
    """Compone UN aviso en lenguaje llano con los encargos 'a un clic' aún no avisados y se lo
    manda a {{TITULAR}} (REPORT autónomo; respeta HALT/silencio nocturno/anti-spam de salida.py).
    Marca 'avisado' para no repetir. Devuelve (texto, n_avisados)."""
    data = _load()
    pendientes = [e for e in data["encargos"]
                  if e.get("estado") == "a_un_clic" and not e.get("avisado")]
    if not pendientes:
        return ("", 0)
    lineas = ["Tengo reservas listas para tu OK (yo no pago, solo dejo todo a un clic):", ""]
    for e in pendientes:
        imp = "%.2f€" % e["importe_eur"] if isinstance(e.get("importe_eur"), (int, float)) else "importe por ver"
        lineas.append("%s %s — %s" % (_ICONO.get(e.get("riesgo"), "•"), e["titulo"], imp))
        if e.get("razones"):
            lineas.append("   (%s)" % "; ".join(e["razones"][:2]))
        if e.get("url"):
            lineas.append("   %s" % e["url"])
    texto = "\n".join(lineas)
    if not dry:
        try:
            sys.path.insert(0, os.path.join(REPO, "tools"))
            import salida
            salida.report_to_titular(texto)
        except Exception:
            pass  # best-effort: el aviso nunca rompe el flujo (igual criterio que el resto del motor)
        for e in pendientes:
            e["avisado"] = True
        _write_atomic(RESERVAS, data)
    return (texto, len(pendientes))


def listar(estado=None):
    data = _load()
    return [e for e in data["encargos"] if estado is None or e.get("estado") == estado]


def auditar():
    """Encargos que DECLARAN decisión suya desde un origen que no puede sostenerla.

    Existe porque el freno nuevo no reescribe el estado vivo: los encargos ya guardados con la
    contradicción siguen ahí, y `clasificar()` ya no se los cree, pero ella tiene derecho a
    VERLOS. Sin esto, la norma se aplicaría en silencio sobre 14 registros que dicen otra cosa.
    """
    out = []
    for e in _load()["encargos"]:
        mal, motivo = decision_no_vale(e.get("origen"))
        if mal and e.get("decision_tomada"):
            out.append({"id": e["id"], "titulo": e["titulo"], "origen": e.get("origen"),
                        "estado": e.get("estado"), "riesgo": e.get("riesgo"), "motivo": motivo})
    return out


# ── CLI (inspección / operación manual) ──────────────────────────────────────
def _main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("uso: reservas.py <listar|procesar|avisar|sobre|crear|clasificar> ...")
        print("  listar [estado]            — encargos (opcional: filtra por estado)")
        print("  procesar                   — clasifica los pendientes y los enruta")
        print("  avisar [--dry]             — manda a {{TITULAR}} los 'a un clic' nuevos")
        print("  sobre [show]               — muestra el sobre verde")
        print("  sobre set campo=valor ...  — edita el sobre (gate de {{TITULAR}})")
        print("  clasificar <importe> <categoria> [reembolsable] [decidida]  — prueba la regla")
        print("  auditar                    — encargos que dicen 'decidido' desde un carrito/propuesta")
        print("  estado <id>      AUTO_PAGO_ACTIVO=%s" % AUTO_PAGO_ACTIVO)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "listar":
        estado = rest[0] if rest else None
        for e in listar(estado):
            print("%-6s %s %-9s %s" % (e.get("estado"), _ICONO.get(e.get("riesgo"), " "),
                                       e.get("riesgo") or "-", e["titulo"]))
        return 0
    if cmd == "auditar":
        malos = auditar()
        if not malos:
            print("✅ ningún encargo declara decisión desde un guardado/propuesta")
            return 0
        print("🛒 %d encargo(s) dicen 'decidido' desde un origen que no lo sostiene:" % len(malos))
        for m in malos:
            print("  %-34s origen=%-22s %s" % (m["titulo"][:34], m["origen"], m["estado"]))
        print("\nNinguno puede entrar al carril verde (clasificar() ya no se cree el booleano).")
        print("Si alguno SÍ lo decidiste tú, vuelve a emitirlo con el origen real.")
        return 0
    if cmd == "procesar":
        ch = procesar()
        print("procesados: %d" % len(ch))
        for e in ch:
            print("  %s %s → %s (%s)" % (_ICONO.get(e["riesgo"]), e["titulo"], e["estado"],
                                          "; ".join(e["razones"][:2])))
        return 0
    if cmd == "avisar":
        texto, n = avisar(dry=("--dry" in rest))
        print("avisados: %d" % n)
        if texto:
            print("---\n%s" % texto)
        return 0
    if cmd == "sobre":
        if rest and rest[0] == "set":
            cambios = {}
            for kv in rest[1:]:
                k, _, v = kv.partition("=")
                if k in ("tope_verde_eur", "tope_rojo_eur"):
                    cambios[k] = float(v)
                elif k == "solo_reembolsable":
                    cambios[k] = v.lower() in ("1", "true", "si", "sí")
                elif k == "categorias_verde":
                    cambios[k] = [c.strip() for c in v.split(",") if c.strip()]
                elif k == "metodo_pago":
                    cambios[k] = v
                else:
                    print("campo desconocido: %s" % k); return 2
            print(json.dumps(guardar_sobre(cambios), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(cargar_sobre(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "clasificar":
        importe = float(rest[0]) if rest and rest[0] not in ("-", "none") else None
        cat = rest[1] if len(rest) > 1 else "otros"
        reemb = (rest[2].lower() in ("1", "true", "si", "sí")) if len(rest) > 2 else None
        dec = (rest[3].lower() in ("1", "true", "si", "sí")) if len(rest) > 3 else False
        r, razones = clasificar({"importe_eur": importe, "categoria": cat,
                                 "reembolsable": reemb, "decision_tomada": dec})
        print("%s %s — %s" % (_ICONO.get(r), r, "; ".join(razones)))
        return 0
    print("comando desconocido: %s" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
