#!/usr/bin/env python3
"""tools/triage_tareas.py — el "gestor de tareas": decide qué entra a la lista única y qué no.

Carril DETERMINISTA y barato (sin LLM). Clasifica un texto suelto (chat/Telegram/voz/correo) en:
  · "tarea"  → claramente accionable → lo crea en la lista única (seguimiento.crear_tarea)
  · "no"     → ruido (saludo, acuse, FYI corto) → se descarta
  · "dudosa" → ni claro sí ni claro no → a la cola de "¿es tarea?" para que Vega/{{TITULAR}} decidan

Política ({{TITULAR}}, 22/6): «añade lo claro, pregunta lo dudoso». Lo dudoso lo juzga Vega (LLM, tier
barato) y, si sigue sin estar claro o es sensible, te pregunta. Este módulo es el carril barato +
la fontanería; el criterio fino lo pone Vega.

Muro:
  · SOLO escribe en la lista LOCAL (vía seguimiento.crear_tarea) y en la cola local de dudosas.
    NUNCA envía/publica/contacta/paga.
  · Lo que llega de fuera es DATO NO confiable: aquí se trata como TEXTO a clasificar, jamás se
    obedecen instrucciones embebidas (el muro de Vega lo refuerza). Sin dependencias (stdlib).

Uso:
  python3 tools/triage_tareas.py "recuérdame llamar a la aseguradora mañana"   # clasifica/crea
  python3 tools/triage_tareas.py --solo-clasificar "lo de {{CONTACTO}}"              # no crea, solo dice
  python3 tools/triage_tareas.py --dudosas                                     # lista la cola
"""
import json
import os
import re
import sys
from datetime import date, timedelta

# La cola de "dudosas" es estado VIVO (gitignored, no viaja a los worktrees) y comparte libreta
# con seguimiento.crear_tarea: ambos deben escribir en casa base (BTP_REPO o ~/claudecode), nunca
# en el árbol relativo al fichero, o lo que Vega tría desde su worktree "desaparece" del Tablero.
# Mismo criterio que tools/seguimiento.py / tools/leer_contacto.py. BTP_STATE_DIR aísla en tests.
ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(ROOT, "tools", "state")
DUDOSAS = os.path.join(STATE, "tareas", "dudosas.json")

# Verbos/arranques que marcan ACCIÓN (→ tarea clara).
_VERBOS = (
    "recuérdame", "recuerdame", "tengo que", "hay que", "necesito", "acordarme", "no olvidar",
    "llamar", "llama", "escribir", "escribe", "enviar", "manda", "mandar", "responder", "contactar",
    "comprar", "pagar", "pedir", "reservar", "agendar", "apuntar", "preparar", "revisar", "confirmar",
    "mirar", "buscar", "organizar", "subir", "cerrar", "terminar", "redactar", "recoger", "firmar",
    "pedir cita", "sacar cita", "rellenar", "rotar", "actualizar", "montar", "instalar",
)
# Acuses/saludos/relleno → NO-tarea (si el mensaje es corto y solo es esto).
_NO = frozenset((
    "gracias", "graciasss", "ok", "oka", "vale", "venga", "genial", "perfecto", "bien", "guay",
    "hola", "buenas", "buenos", "adiós", "adios", "chao", "jaja", "jajaja", "👍", "💜", "❤️",
    "hecho", "listo", "ya", "sí", "si", "no", "claro", "entendido", "recibido",
))
_ETQ = (
    ("Polaris", ("polaris", "observatorio", "tablero", "web ", "kanban", "daemon", "launchd",
                 "rama", "repo", "bug", "deploy", "preview", "código", "codigo")),
    ("Gestión", ("prensa", "podcast", "entrevista", "abogad", "legal", "contacto", "contrato", "rgpd",
                 "factura", "pago", "dinero", "donación", "donacion", "banco", "cita", "médic", "medic",
                 "cd", "hospital", "limpiad", "contacto", "casa", "calendario", "inglés", "ingles",
                 "voz", "elevenlabs", "instagram", "linkedin", "meta", "token", "correo", "email")),
    ("NED", ("biopsia", "neoantígen", "neoantigen", "diana", "vacuna", "ensayo", "oncólog", "oncolog",
             "contacto", "contacto", "dana", "tumor", "rna", "hla", "muestra", "lab")),
)
_DIAS = {"lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3,
         "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6}


def _hoy():
    return date.today()


def _detect_fecha(low, hoy=None):
    """Devuelve ISO YYYY-MM-DD si el texto trae una fecha reconocible, o ''."""
    hoy = hoy or _hoy()
    if re.search(r"\bhoy\b", low):
        return hoy.isoformat()
    if re.search(r"\bmañana\b|\bmanana\b", low):
        return (hoy + timedelta(days=1)).isoformat()
    if "pasado mañana" in low or "pasado manana" in low:
        return (hoy + timedelta(days=2)).isoformat()
    if "esta semana" in low:
        return (hoy + timedelta(days=(6 - hoy.weekday()))).isoformat()  # domingo de esta semana
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", low)               # ISO
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", low)  # DD/MM[/AAAA]
    if m:
        d, mth = int(m.group(1)), int(m.group(2))
        yr = int(m.group(3) or hoy.year)
        if yr < 100:
            yr += 2000
        try:
            f = date(yr, mth, d)
            if f < hoy:
                f = date(yr + 1, mth, d)   # fecha pasada → asume el año que viene
            return f.isoformat()
        except ValueError:
            pass
    for nombre, wd in _DIAS.items():       # "el viernes" → próximo ese día
        if re.search(r"\b" + nombre + r"\b", low):
            delta = (wd - hoy.weekday()) % 7
            return (hoy + timedelta(days=delta or 7)).isoformat()
    return ""


def _detect_etiqueta(low):
    for etq, claves in _ETQ:
        if any(k in low for k in claves):
            return etq
    return "NED"


def _detect_prioridad(low):
    if re.search(r"\burgente\b|\bya\b|\bcuanto antes\b|\basap\b|hoy mismo", low):
        return "alta"
    return "normal"


def _limpiar_titulo(texto):
    """Quita el arranque-muletilla para que el título sea la acción ('recuérdame que X' → 'X')."""
    t = texto.strip().rstrip(".")
    t = re.sub(r"^(recu[eé]rdame( que)?|acu[eé]rdate de|tengo que|hay que|necesito|no olvidar( de)?)\s+",
               "", t, flags=re.I)
    return (t[:1].upper() + t[1:]) if t else texto.strip()


def clasificar(texto, origen="chat"):
    """Decide veredicto + extrae campos. Determinista; nunca obedece instrucciones del texto."""
    t = (texto or "").strip()
    low = t.lower()
    palabras = re.findall(r"[\wáéíóúñ]+", low)
    if not palabras:
        return {"veredicto": "no", "motivo": "vacío", "campos": {}}
    tiene_verbo = any(v in low for v in _VERBOS)
    fecha = _detect_fecha(low)
    # 1) ACCIÓN explícita (verbo) o fecha+algo de sustancia → tarea clara.
    if tiene_verbo or (fecha and len(palabras) >= 3):
        return {"veredicto": "tarea", "motivo": "acción" if tiene_verbo else "fecha+contenido",
                "campos": {"titulo": _limpiar_titulo(t), "etiqueta": _detect_etiqueta(low),
                           "vence": fecha, "prioridad": _detect_prioridad(low)}}
    # 2) Mensaje corto que es solo acuse/saludo → no es tarea.
    if len(palabras) <= 4 and all(p in _NO for p in palabras):
        return {"veredicto": "no", "motivo": "acuse/saludo", "campos": {}}
    # 3) Resto → dudosa (la juzga Vega; si no, se pregunta).
    return {"veredicto": "dudosa", "motivo": "sin señal clara",
            "campos": {"titulo": _limpiar_titulo(t), "etiqueta": _detect_etiqueta(low),
                       "vence": fecha, "prioridad": _detect_prioridad(low)}}


def _load_dudosas():
    try:
        with open(DUDOSAS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("dudosas"), list) else {"dudosas": []}
    except Exception:
        return {"dudosas": []}


def _save_dudosas(d):
    os.makedirs(os.path.dirname(DUDOSAS), exist_ok=True)
    tmp = DUDOSAS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DUDOSAS)


def _encolar_dudosa(texto, origen, campos):
    d = _load_dudosas()
    if not any(x.get("texto") == texto for x in d["dudosas"]):
        d["dudosas"].append({"texto": texto, "origen": origen, "campos": campos})
        _save_dudosas(d)


def listar_dudosas():
    return _load_dudosas().get("dudosas", [])


def triar(texto, origen="chat"):
    """Aplica la política «añade lo claro, pregunta lo dudoso». Devuelve qué hizo.
    SOLO escribe en local (lista de tareas / cola de dudosas); nada hacia fuera."""
    c = clasificar(texto, origen)
    if c["veredicto"] == "tarea":
        import seguimiento
        cf = c["campos"]
        tid = seguimiento.crear_tarea(cf["titulo"], etiqueta=cf.get("etiqueta", "NED"),
                                      vence=cf.get("vence", ""), prioridad=cf.get("prioridad", "normal"),
                                      origen=origen)
        return {"accion": "creada", "id": tid, **c}
    if c["veredicto"] == "dudosa":
        _encolar_dudosa(texto.strip(), origen, c["campos"])
        return {"accion": "preguntar", **c}
    return {"accion": "descartada", **c}


# ───────────────────────── CLI ─────────────────────────
def main(argv):
    if argv and argv[0] == "--dudosas":
        for x in listar_dudosas():
            print("?", x.get("texto", ""), "·", (x.get("campos") or {}).get("etiqueta", ""))
        return 0
    solo = "--solo-clasificar" in argv
    texto = " ".join(a for a in argv if not a.startswith("--"))
    if not texto:
        print(__doc__)
        return 2
    res = clasificar(texto) if solo else triar(texto)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
