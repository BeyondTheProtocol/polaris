#!/usr/bin/env python3
"""tools/prefiltro_juez.py — prefiltro DETERMINISTA del juez de las normas de salida (capa 3).

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026: punto
de rotura 08 y deuda `capa3_juez_pendiente`. Paso 1 del plan aprobado por {{TITULAR}} el 26-sep-2026
(`04 · IA/Notas/plan-el-juez-de-las-normas-de-salida-capa-3-2026-09-26.md`).

QUÉ HACE. Marca, sin LLM, los turnos CANDIDATOS a que un juez los mire, para las 3 normas de la
fase 1. No decide si hubo incumplimiento: sobre-captura a propósito (recall alto) y el juez pone la
precisión. Mide cuántos candidatos por día daría sobre los turnos reales, reutilizando el partido
de turnos de `replay_gate.py` (`turnos_ricos`) y los borradores de `gate_salida._borradores`.

  · inventarle_frases  (feedback-no-inventarle-frases-en-primera-persona): un borrador en su voz
    lleva una frase entrecomillada de ≥3 palabras. Subcuenta: cuántos quedan SIN respaldo tras el
    cotejo de `cotejo_frases.py` (paso 2).
  · cotejar_fuente     (feedback-cotejar-siempre-fuente-clinica): la respuesta da una cifra
    clínica de ELLA (término clínico + número con unidad, cerca de «tu/tus/su» o de un informe).
    Subcuenta: cuántos sin haber leído una fuente en el turno (lector_clinico, kb.py, informe).
  · calibrar           (feedback-calibrar-no-derrumbarse-ni-rotundo): su mensaje cuestiona lo que
    dije en el turno anterior («¿seguro?», «eso no es así», «¿de dónde sacas…?»).

Local, sin red, sin escribir nada. Los ejemplos se de-identifican con `borde`.
Uso:
  python3 tools/prefiltro_juez.py [--dias 30] [--ejemplos 0] [--norma X] [--sin-wa] [--json]
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cotejo_frases  # noqa: E402
import replay_gate as rg  # noqa: E402

NORMAS = {
    "inventarle_frases": "feedback-no-inventarle-frases-en-primera-persona",
    "cotejar_fuente": "feedback-cotejar-siempre-fuente-clinica",
    "calibrar": "feedback-calibrar-no-derrumbarse-ni-rotundo",
}

# ── cotejar_fuente ─────────────────────────────────────────────────────────────────────────────
_TERMINO_CLINICO = re.compile(
    r"\bki-?67\b|\bSUV(?:max)?\b|\bCA\s?15[.-]?3\b|\bCEA\b|\bHER2\b|\bRE\b|\bRP\b|\breceptor\w*|"
    r"\bVAF\b|\bTMB\b|\blesi[oó]n\w*|\bmet[aá]stasis\b|\bh[ií]gado\b|\bhep[aá]tic\w*|\bganglio\w*|"
    r"\bPET\b|\bTAC\b|\bRM\b|\bresonancia\b|\bdosis\b|\bneutr[oó]filos\b|\bhemoglobina\b|"
    r"\bplaquetas\b|\bleucocitos\b|\bbilirrubina\b|\btransaminasas\b|\bGOT\b|\bGPT\b|"
    r"\bmarcador\w*|\btumor\w*|\bn[oó]dulo\w*|\bbiopsia\b|\bFEVI\b|\bcreatinina\b", re.I)
_CIFRA = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|mm\b|cm\b|mg\b|U/mL|ng/mL|g/dL|/mm3|mL\b|x\s?10)|"
                    r"\bSUV(?:max)?\s*(?:de\s*)?\d|\b(?:3|2|1)\+", re.I)
_DE_ELLA = re.compile(r"\btus?\b|\btu caso\b|\bTitular\b|\bsu (?:informe|PET|TAC|RM|biopsia|anal[ií]tica)|"
                      r"\binforme\b|\banal[ií]tica\b", re.I)
_FUENTE_LEIDA = re.compile(r"lector_clinico|kb\.py|00_FUENTE-DE-VERDAD|informe|oncologo-virtual|"
                           r"verificacion|comite-medico", re.I)


def _frases(t):
    return [f for f in re.split(r"(?<=[.!?])\s+|\n+", t) if f.strip()]


def cotejar_fuente(d):
    for f in _frases(d["respuesta"]):
        f = f[:600]
        if _TERMINO_CLINICO.search(f) and _CIFRA.search(f) and _DE_ELLA.search(f):
            return f
    return None


# ── calibrar ───────────────────────────────────────────────────────────────────────────────────
# Se mira sobre su mensaje en minúsculas y SIN tildes: escribe rápido, sin acentos y a veces sin
# «¿». Sacado de sus mensajes reales (replay de 30 días, 26-sep-26): «Seguro que no entra?», «eso
# esta mal no se de donde lo has sacado», «no inventes», «esta bien o mal», «O me lo he inventado».
_CUESTIONA = re.compile(
    r"(^|[¿?.!,]\s*)seguro que\b|\bseguro\s*\?|\bestas segur|\bde verdad\s*\?|\ben serio\s*\?|"
    r"\bde donde (lo )?(has sacado|sacas|sale)|\bcomo lo sabes|\blo has (comprobado|verificado|mirado)|"
    r"\bno es (asi|verdad|cierto|correcto|eso)\b|\bno era (asi|eso)\b|\beso (no|esta mal|es mentira)|"
    r"\b(esta|estan|es) mal\b|\bbien o mal\b|\bno cuadra|\bno tiene sentido|\bes falso|\bfalso\b|"
    r"\bmentira\b|\bte equivocas|\bequivocad[oa]|\bincorrect[oa]|\bno estoy de acuerdo|"
    r"\bno (me lo )?creo\b|\bno te (lo )?inventes|\bno inventes|\bte lo (has )?inventado|"
    r"\bpor que (dices|has dicho|pones|afirmas|das por)|\bpero no (dijo|dijiste|era|es)\b")


def _sin_tildes(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def calibrar(d):
    if not d["previa"]:
        return None
    m = _CUESTIONA.search(_sin_tildes(d["pregunta"])[:1500])
    return m.group(0).strip() if m else None


# ── inventarle_frases ──────────────────────────────────────────────────────────────────────────
def inventarle_frases(d, g):
    """Frases en su voz de la respuesta y de lo escrito en el turno, también por sus sub-agentes
    (marcas de `gate._marcas` y `gate._frases_de_subagentes`, vía `replay_gate.turnos_ricos`)."""
    fr = cotejo_frases.frases_en_su_voz(g._borradores(d["respuesta"]))
    marca = getattr(g, "FRASE_SUYA", None)
    if marca:
        fr += [x[len(marca):] for x in d["tools"] if x.startswith(marca)]
    return list(dict.fromkeys(fr)) or None


def _dia(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
    except Exception:
        return None


def medir(dias=30, solo=None, n_ejemplos=0, usar_wa=True):
    g = rg.cargar_gate()
    base = os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")
    corte = time.time() - dias * 86400
    hoy = datetime.date.today()
    primer = hoy - datetime.timedelta(days=dias - 1)
    wa = cotejo_frases.corpus_whatsapp() if usar_wa else []
    por_dia = collections.defaultdict(collections.Counter)
    turnos_dia = collections.Counter()
    sub = collections.Counter()
    ejemplos = collections.defaultdict(list)
    for ruta in sorted(glob.glob(os.path.join(base, "*", "*.jsonl"))):
        try:
            if os.path.getmtime(ruta) < corte:
                continue
        except OSError:
            continue
        for d in rg.turnos_ricos(ruta, getattr(g, "_marcas", None)):
            dia = _dia(d["ts"])
            if not dia or dia < primer or dia > hoy:
                continue
            turnos_dia[dia] += 1
            if any(u in d["respuesta"] for u in g.URGENTE):
                continue
            for nombre in NORMAS:
                if solo and nombre not in solo:
                    continue
                if nombre == "inventarle_frases":
                    hit = inventarle_frases(d, g)
                    mot = None
                    # Versión bruta (cualquier comilla en un bloque suyo), para ver qué quita el
                    # filtro de atribución.
                    if any(cotejo_frases.frases_citadas(b) for b in g._borradores(d["respuesta"])
                           if cotejo_frases.es_su_voz(b)):
                        sub["inventarle_frases.bruto_turnos"] += 1
                    if hit:
                        faltan = [f for f in hit if cotejo_frases.respaldo(f, d["suyos"], wa) is None]
                        sub["inventarle_frases.frases"] += len(hit)
                        sub["inventarle_frases.frases_sin_respaldo"] += len(faltan)
                        if faltan:
                            sub["inventarle_frases.turnos_sin_respaldo"] += 1
                        mot = ("SIN respaldo: " if faltan else "respaldada: ") + " | ".join(
                            faltan or hit)
                elif nombre == "cotejar_fuente":
                    hit = cotejar_fuente(d)
                    if hit and not any(_FUENTE_LEIDA.search(x) for x in d["tools"]):
                        sub["cotejar_fuente.sin_fuente_en_turno"] += 1
                    mot = hit
                else:
                    hit = calibrar(d)
                    mot = hit
                if hit:
                    por_dia[dia][nombre] += 1
                    if len(ejemplos[nombre]) < n_ejemplos:
                        ejemplos[nombre].append({"sesion": os.path.basename(ruta)[:8],
                                                 "dia": str(dia), "motivo": rg._deid(str(mot)[:220]),
                                                 "respuesta": rg._deid(d["respuesta"][:300])})
    dias_lista = [primer + datetime.timedelta(days=i) for i in range(dias)]
    activos = [x for x in dias_lista if turnos_dia[x]]
    res = {"dias": dias, "desde": str(primer), "hasta": str(hoy),
           "turnos": sum(turnos_dia.values()), "dias_con_turnos": len(activos),
           "normas": {}, "sub": dict(sub), "ejemplos": dict(ejemplos), "wa_mensajes": len(wa)}
    for nombre, slug in NORMAS.items():
        serie = [por_dia[x][nombre] for x in dias_lista]
        ords = sorted([por_dia[x][nombre] for x in activos] or [0])
        total = sum(serie)
        res["normas"][nombre] = {
            "slug": slug, "total": total,
            "media_dia_natural": round(total / dias, 1),
            "media_dia_activo": round(total / max(1, len(activos)), 1),
            "mediana_dia_activo": ords[len(ords) // 2],
            "max_dia": max(serie) if serie else 0,
        }
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dias", type=int, default=30)
    ap.add_argument("--norma", action="append", choices=list(NORMAS))
    ap.add_argument("--ejemplos", type=int, default=0)
    ap.add_argument("--sin-wa", action="store_true", help="no cargar WhatsApp en el cotejo")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = medir(a.dias, a.norma, a.ejemplos, not a.sin_wa)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0
    print("Turnos: %d en %d días (%s → %s), %d días con actividad · WhatsApp: %d mensajes suyos\n"
          % (r["turnos"], r["dias"], r["desde"], r["hasta"], r["dias_con_turnos"], r["wa_mensajes"]))
    print("%-18s %6s %9s %9s %8s %6s" % ("norma", "total", "/día nat", "/día act", "mediana", "máx"))
    for n, x in r["normas"].items():
        print("%-18s %6d %9.1f %9.1f %8d %6d" % (n, x["total"], x["media_dia_natural"],
                                                 x["media_dia_activo"], x["mediana_dia_activo"],
                                                 x["max_dia"]))
    if r["sub"]:
        print("\n" + "\n".join("  %s: %d" % kv for kv in sorted(r["sub"].items())))
    for n, ej in r["ejemplos"].items():
        for e in ej:
            print("\n[%s %s %s] %s" % (n, e["dia"], e["sesion"], e["motivo"].replace("\n", " ")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
