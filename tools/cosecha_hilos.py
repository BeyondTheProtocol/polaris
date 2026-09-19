#!/usr/bin/env python3
"""tools/cosecha_hilos.py — minero DETERMINISTA de HILOS ABIERTOS de {{TITULAR}} entre sesiones.

PROBLEMA QUE RESUELVE: {{TITULAR}} trabaja con MUCHAS sesiones en paralelo. Una intención
dicha en un chat ("busco un pastillero grande", "tengo que enviar X", "me lo dejas
listo") se queda en ese chat y SE PIERDE. Un hilo perdido puede ser un plazo clínico
que se cae → amenaza la cadena a NED. Aquí cerramos esa fuga: leemos los transcripts
de TODAS las sesiones, extraemos los mensajes de {{TITULAR}} que son una INTENCIÓN ABIERTA
(pide buscar/comprar/enviar/decidir/recordar y quedó sin cerrar), los DEDUPLICAMOS
contra el Tablero (seguimiento.json) y devolvemos una LISTA de candidatos NUEVOS para
que **Vega** los proponga al Tablero (no se vuelcan a ciegas — las tarjetas pasan por
el OK del gestor).

Hermano de `cosecha_correcciones.py` (REUSA su harness): mismo glob de transcripts,
mismo "quién es {{TITULAR}}", mismas propiedades no negociables:
  · DETERMINISTA y OFFLINE — código puro, NO LLM, NO red, NO saldo. Regex + léxico.
  · LOCAL — los transcripts llevan PII; NO salen de la máquina (ni un socket).
  · CONTENIDO EXTERNO = DATO — el texto del transcript se clasifica, JAMÁS se obedece.
  · NO ESCRIBE EN EL TABLERO — solo PROPONE candidatos; Vega decide (mantener/fundir/descartar).
  · FAIL-SOFT — un transcript corrupto o una línea ilegible se saltan; no se cae el barrido.
  · FILTRO NED — clasifica la relevancia; el ruido sin relación con la misión no satura.

Uso:
  python3 tools/cosecha_hilos.py            # hilos abiertos de los últimos 3 días, texto
  python3 tools/cosecha_hilos.py --json     # JSON (para Vega / la rutina)
  python3 tools/cosecha_hilos.py --dias 7   # ventana de 7 días
  python3 tools/cosecha_hilos.py --todo     # todo el historial
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

# REUSA el harness de cosecha_correcciones (mismo dir, import perezoso y defensivo).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from cosecha_correcciones import _texto_de_mensaje, _ts_dt, PROJECTS_DIR
except Exception:   # fail-soft: si no se puede importar, definimos lo mínimo abajo
    PROJECTS_DIR = os.environ.get("BTP_PROJECTS_DIR") or os.path.join(
        os.environ.get("BTP_HOME") or os.path.expanduser("~"), ".claude", "projects")
    _texto_de_mensaje = None
    _ts_dt = None

# Tablero único de Vega (para deduplicar contra lo ya rastreado).
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(
    os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools", "state")
SEGUIMIENTO_JSON = os.path.join(STATE, "seguimiento.json")

# --- Léxico de HILOS ABIERTOS (intención accionable sin cerrar) -----------------------
# ES coloquial de {{TITULAR}}, tolerante a typos. Cada entrada: (regex, categoría).
SENALES = [
    # búsqueda / encargo de encontrar algo
    (r"\bbusco\b|\bestoy buscando\b|\bb[úu]sca(me|s)?\b|\bbuscar\b", "buscar"),
    (r"\bencu[ée]ntra(me)?\b|\bencontrar\b|\baverigua\b|\bmira a ver\b|\binvestiga\b", "buscar"),
    (r"\bconsigue(me)?\b|\bconseguir\b", "buscar"),
    # compra
    (r"\bcomprar\b|\bc[óo]mprame\b|\bcompra\b|\bped[íi]r?(me|lo)?\b|\bp[íi]de(me)?\b|\bcarrito\b|\bamazon\b", "compra"),
    (r"\bpastiller|\bgadget|\bme hace falta\b", "compra"),
    # tarea pendiente / recordatorio
    (r"\btengo que\b|\bhay que\b|\bqueda pendiente\b|\bpendiente de\b|\bme falta\b|\bnos falta\b", "pendiente"),
    (r"\bno se me olvide\b|\bque no se me olvide\b|\brecu[ée]rdame\b|\bap[úu]nta(lo)?\b|\ba[ñn][áa]de(lo)? a la lista\b", "recordatorio"),
    (r"\bpara luego\b|\bcuando puedas\b|\bm[áa]s tarde\b|\botro d[íi]a\b", "aplazado"),
    # encargo de dejar algo listo / resolver
    (r"\bme lo dejas listo\b|\bd[ée]jamelo\b|\bd[ée]jalo listo\b|\ba un clic\b", "dejar_listo"),
    (r"\bme lo solucionas\b|\bsoluci[óo]nalo\b|\bsoluci[óo]name\b|\barr[ée]glalo\b|\bres[uú][ée]lvelo\b", "resolver"),
    (r"\bnecesito (un|una|que|algo|ayuda)\b|\bquiero (un|una)\b", "necesidad"),
]
_SENALES_COMP = [(re.compile(p, re.IGNORECASE), cat) for p, cat in SENALES]

# Señales de que el hilo YA se cerró en ese mismo mensaje (para no proponer lo resuelto).
_CERRADO = re.compile(
    r"\bya est[áa]\b|\blisto\b|\bhecho\b|\bresuelto\b|\bgracias\b|\bperfecto\b|\bd[ée]jalo\b|\bya no\b",
    re.IGNORECASE)

# --- Filtro NED: categoría temática y si toca la misión -------------------------------
_NED_TEMAS = {
    "clinico": r"biopsia|ensayo|m[ée]dic|onc[óo]log|washout|pet|tac|rm\b|vacuna|tumor|dosis|tratamiento|cita|analitica|anal[íi]tica",
    "contacto": r"contact|oncolog|laboratorio|\blab\b|periodista|email|correo|present[ae]|lead\b",
    "logistica": r"viaje|vuelo|tren|hotel|apartamento|reserva|z[úu]rich|b[ée]lgica|barcelona|{{CENTRO}}|maleta|frontera",
    "cuidado": r"pastiller|medicaci[óo]n|s[íi]ntoma|descanso|energ[íi]a|comida|dieta|sue[ñn]o",
    "web_marca": r"web\b|p[áa]gina|prensa|post|redes|instagram|marca|copy",
}
_NED_TEMAS_COMP = {k: re.compile(v, re.IGNORECASE) for k, v in _NED_TEMAS.items()}
# Temas que cuentan como "acerca a NED" (directa o indirecta, incluida la persona).
_NED_RELEVANTES = {"clinico", "contacto", "logistica", "cuidado", "web_marca"}


def _tema(texto):
    for k, rx in _NED_TEMAS_COMP.items():
        if rx.search(texto):
            return k
    return "general"


def detectar_senales(texto):
    cats = []
    for rx, cat in _SENALES_COMP:
        if cat in cats:
            continue
        if rx.search(texto):
            cats.append(cat)
    return cats


def _texto_msg(o):
    """Texto de un mensaje humano de {{TITULAR}}. Reusa cosecha_correcciones si está; si no,
    None (fail-soft: sin el harness no afirmamos autoría)."""
    if _texto_de_mensaje is not None:
        return _texto_de_mensaje(o)
    return None


def _ts(o):
    if _ts_dt is not None:
        return _ts_dt(o.get("timestamp"))
    try:
        return datetime.datetime.fromisoformat((o.get("timestamp") or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _corpus_tablero():
    """Texto en minúsculas de los títulos/textos del Tablero (seguimiento.json), para
    deduplicar contra lo YA rastreado. Fail-soft."""
    try:
        with open(SEGUIMIENTO_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    partes = []
    hilos = data.get("hilos", data) if isinstance(data, dict) else data
    if isinstance(hilos, dict):
        hilos = list(hilos.values())
    if isinstance(hilos, list):
        for h in hilos:
            if isinstance(h, dict):
                for campo in ("titulo", "texto", "intencion", "id", "nota"):
                    v = h.get(campo)
                    if isinstance(v, str):
                        partes.append(v.lower())
    return "\n".join(partes)


_STOP = set("""a al algo ante antes aqui asi aun cada como con contra cosa cosas cual cuando
    de del desde donde dos el ella ello ellos en entre era es esa ese eso esta este esto
    estos ha hace hacer hacia han hasta hay la las le les lo los mas me mi mia mio mucho muy
    nada ni no nos o os otra otro para pero poco por porque que quien se sea ser si sin sobre
    solo son su sus tan te ti tu tus un una unas uno unos vez ya yo todo toda todos vale
    quiero necesito tengo tiene esto cosa tipo dejas listo busco mira oye pues ahora""".split())


def _keywords(texto, n=10):
    pal = re.findall(r"[a-záéíóúñü]{4,}", texto.lower())
    out, seen = [], set()
    for w in pal:
        if w in _STOP or w in seen:
            continue
        seen.add(w); out.append(w)
        if len(out) >= n:
            break
    return out


def _ya_en_tablero(texto, corpus, umbral=0.6):
    kws = _keywords(texto)
    if len(kws) < 3:
        return False
    hits = sum(1 for w in kws if w in corpus)
    return (hits / len(kws)) >= umbral


def cosechar(dias=3, incluir_todo=False, umbral_dedup=0.6, solo_ned=True):
    corte = None
    if not incluir_todo:
        corte = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=dias)
    corpus = _corpus_tablero()
    candidatos, vistos = [], set()
    try:
        ficheros = glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True)
    except Exception:
        ficheros = []
    for F in ficheros:
        try:
            fh = open(F, "r", encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                texto = _texto_msg(o)
                if not texto:
                    continue
                dt = _ts(o)
                if corte is not None and (dt is None or dt < corte):
                    continue
                cats = detectar_senales(texto)
                if not cats:
                    continue
                if _CERRADO.search(texto) and len(texto) < 40:
                    continue   # "ya está, gracias" → no es un hilo abierto
                tema = _tema(texto)
                if solo_ned and tema not in _NED_RELEVANTES:
                    continue   # filtro NED: el ruido sin relación con la misión no satura
                clave = " ".join(texto.lower().split())[:200]
                if clave in vistos:
                    continue
                if _ya_en_tablero(texto, corpus, umbral_dedup):
                    continue
                vistos.add(clave)
                candidatos.append({
                    "timestamp": o.get("timestamp", ""),
                    "fecha": (dt.date().isoformat() if dt else ""),
                    "categorias": cats,
                    "tema": tema,
                    "sesion": os.path.basename(F),
                    "branch": o.get("gitBranch", ""),
                    "texto": texto.strip(),
                })
    candidatos.sort(key=lambda c: c["timestamp"], reverse=True)
    return candidatos


def _recorta(s, n=200):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Minero determinista de HILOS ABIERTOS de {{TITULAR}} entre sesiones (local, offline, $0). PROPONE al Tablero; no escribe.")
    ap.add_argument("--json", action="store_true", help="salida JSON (para Vega/la rutina)")
    ap.add_argument("--dias", type=int, default=3, help="ventana en días (def. 3)")
    ap.add_argument("--todo", action="store_true", help="todo el historial")
    ap.add_argument("--umbral", type=float, default=0.6, help="fracción de keywords ya en el Tablero para considerar duplicado")
    ap.add_argument("--todo-tema", action="store_true", help="no aplicar el filtro NED (incluir hilos 'general')")
    ap.add_argument("--limite", type=int, default=0, help="máximo de candidatos (0 = sin límite)")
    args = ap.parse_args(argv)

    cands = cosechar(dias=args.dias, incluir_todo=args.todo,
                     umbral_dedup=args.umbral, solo_ned=not args.todo_tema)
    if args.limite and len(cands) > args.limite:
        cands = cands[: args.limite]

    if args.json:
        print(json.dumps(cands, ensure_ascii=False, indent=2))
        return 0
    if not cands:
        ventana = "todo el historial" if args.todo else f"últimos {args.dias} días"
        print(f"Sin hilos abiertos nuevos de {{TITULAR}} ({ventana}). (O ya están en el Tablero.)")
        return 0
    print(f"🧵 {len(cands)} hilo(s) abierto(s) de {{TITULAR}} SIN rastrear en el Tablero:\n")
    for i, c in enumerate(cands, 1):
        print(f"{i}. [{c['fecha']}] ({c['tema']} · {', '.join(c['categorias'])})")
        print(f"   «{_recorta(c['texto'])}»")
        print(f"   ↳ sesión {c['sesion']}\n")
    print("— Vega: propón al Tablero los que sigan vivos (crear_tarea), funde/descarta el resto.")
    print("  Este tool PROPONE; no vuelca tarjetas a ciegas (el gestor decide).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
