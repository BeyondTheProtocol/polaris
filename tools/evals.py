#!/usr/bin/env python3
"""tools/evals.py — F2b: el ARNÉS DE EVALS del motor (plan typed-swinging-wand, sección «Evals y testing»).

El motor mejora en cada ciclo: para eso necesita una vara que mida si una IA/router/borde sigue
haciendo lo correcto. Este arnés convierte esa vara en DATOS (golden sets en `evals/*.json`) que
CRECEN con las regresiones de producción — no en tests cableados que solo el autor recuerda tocar.

Filosofía (idéntica al resto del motor):
  · DETERMINISTA-PRIMERO y GRATIS por defecto. Las categorías muro/router/relevo/ciencia se miden con
    checks puros (sin LLM, $0, nunca bloqueadas por saldo). El borde sigue siendo REAL en cada caso.
  · APLAZO HONESTO. La categoría `calidad` (texto libre) se juzga con un LLM-juez; si no hay cerebro-juez
    disponible (apagón de crédito, como hoy) NO se inventa una nota: el caso queda `deferred`.
  · ANTI-GOODHART. El juez NUNCA es el mismo cerebro que respondió (juez ≠ sujeto); los criterios van
    ANCLADOS POR HUMANO en la `rubrica` del caso; hay casos `held_out` que solo miden, no afinan nada.
  · El juez consume saldo → es OPT-IN (`--judge`). Una corrida normal es gratis.

Tipos de caso (campo `tipo`):
  clasificar  → borde.clasificar(prompt)              espera {sensible, motivo_contiene?}
  egress      → borde.egress_check(prompt, destino)   espera {permitido, alarma?}   (canarios? opcional)
  ingeniero  → borde.egress_cientifico(prompt)        espera {ok}
  router      → ia.ask(prompt) con `registro` + `stub` espera {brain?, degradado?, deferred?, texto_nulo?, motivo_contiene?}
  calidad     → respuesta real de un cerebro, juzgada por OTRO    espera vía `rubrica`+`umbral`

CLI:
  python3 tools/evals.py run [--judge] [--tipo clasificar,router] [--no-held-out] [--json]
  python3 tools/evals.py drift           # compara la última corrida con la baseline (alerta >25%)
  python3 tools/evals.py baseline        # fija la última corrida como baseline
  python3 tools/evals.py add-regression '<json del caso>'   # promueve un fallo de prod al golden set
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde   # noqa: E402 — el borde real, en cada caso
import ia      # noqa: E402 — la centralita real

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS_DIR = os.environ.get("BTP_EVALS_DIR") or os.path.join(REPO, "evals")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
OUT_DIR = os.path.join(STATE, "evals")
REGRESIONES = os.path.join(EVALS_DIR, "regresiones.json")
UMBRAL_DERIVA = 0.25          # caída de pass-rate que dispara alerta (plan: recalibrar a >20-25%)
TIPOS = ("clasificar", "egress", "ingeniero", "router", "calidad", "sello", "citas")


# ── Carga del golden set ───────────────────────────────────────────────────────────────────
def cargar_casos(tipos=None, incluir_held_out=True):
    """Lee todos los `evals/*.json` (cada uno = lista de casos). Ignora claves `_*` (notas)."""
    casos = []
    for f in sorted(glob.glob(os.path.join(EVALS_DIR, "*.json"))):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            sys.stderr.write("evals: no pude leer %s (%r)\n" % (f, e))
            continue
        for c in (data if isinstance(data, list) else []):
            if not isinstance(c, dict) or not c.get("id"):
                continue
            if tipos and c.get("tipo") not in tipos:
                continue
            if not incluir_held_out and c.get("held_out"):
                continue
            casos.append(c)
    return casos


# ── Evaluadores deterministas (gratis; el borde es real) ────────────────────────────────────
def _r(paso, detalle, *, deferred=False, score=None, extra=None):
    d = {"paso": paso, "detalle": detalle, "deferred": deferred, "score": score}
    if extra:
        d.update(extra)
    return d


def _ev_clasificar(caso):
    esp = caso.get("espera", {})
    sensible, motivo = borde.clasificar(caso.get("prompt", ""))
    if sensible != esp.get("sensible"):
        return _r(False, "sensible=%s, esperaba %s (%s)" % (sensible, esp.get("sensible"), motivo))
    mc = esp.get("motivo_contiene")
    if mc and mc.lower() not in (motivo or "").lower():
        return _r(False, "motivo '%s' no contiene '%s'" % (motivo, mc))
    return _r(True, "sensible=%s (%s)" % (sensible, motivo))


def _ev_egress(caso):
    esp = caso.get("espera", {})
    prev = os.environ.get("BTP_CANARIOS")
    if caso.get("canarios"):
        os.environ["BTP_CANARIOS"] = ":".join(caso["canarios"])
    try:
        v = borde.egress_check(caso.get("prompt", ""), destino=caso.get("destino", "externo"),
                               escalar=False)
    finally:
        if prev is None:
            os.environ.pop("BTP_CANARIOS", None)
        else:
            os.environ["BTP_CANARIOS"] = prev
    if v.permitido != esp.get("permitido"):
        return _r(False, "permitido=%s, esperaba %s (%s)" % (v.permitido, esp.get("permitido"), v.motivo))
    if "alarma" in esp and bool(v.alarma) != bool(esp["alarma"]):
        return _r(False, "alarma=%s, esperaba %s" % (v.alarma, esp["alarma"]))
    return _r(True, "permitido=%s alarma=%s (%s)" % (v.permitido, v.alarma, v.motivo))


def _ev_cientifico(caso):
    esp = caso.get("espera", {})
    ok, motivo = borde.egress_cientifico(caso.get("prompt", ""))
    if ok != esp.get("ok"):
        return _r(False, "ok=%s, esperaba %s (%s)" % (ok, esp.get("ok"), motivo))
    return _r(True, "ok=%s (%s)" % (ok, motivo))


def _stub_invocar(stub):
    """Sustituye ia._invocar por un stub determinista keyed por nombre de cerebro (como test_ia).
    El ROUTER y el BORDE siguen reales; solo se finge la RESPUESTA del cerebro → caso gratis y estable."""
    def fake(cerebro, prompt, system, clinico, interactivo=False, critico=False):
        b = (stub or {}).get(cerebro.get("name"), ["fail", "no-config"])
        return (b[1], 0.0, None) if b[0] == "ok" else (None, 0.0, b[1])
    return fake


def _ev_router(caso):
    esp = caso.get("espera", {})
    # registro sintético del caso → fichero temporal; NUNCA tocamos el peripheries real.
    reg_path = os.path.join(OUT_DIR, "_router_reg.json")
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"cerebros": caso.get("registro", [])}, open(reg_path, "w"), ensure_ascii=False)
    reg_prev, inv_prev = ia.REGISTRO, ia._invocar
    ia.REGISTRO = reg_path
    ia._invocar = _stub_invocar(caso.get("stub"))
    try:
        r = ia.ask(caso.get("prompt", ""), clinico=bool(caso.get("clinico")))
    finally:
        ia.REGISTRO, ia._invocar = reg_prev, inv_prev
    fallos = []
    if "brain" in esp and r.get("brain") != esp["brain"]:
        fallos.append("brain=%s≠%s" % (r.get("brain"), esp["brain"]))
    for k in ("degradado", "deferred"):
        if k in esp and bool(r.get(k)) != bool(esp[k]):
            fallos.append("%s=%s≠%s" % (k, r.get(k), esp[k]))
    if esp.get("texto_nulo") and r.get("text") is not None:
        fallos.append("texto no nulo")
    mc = esp.get("motivo_contiene")
    if mc and mc.lower() not in (r.get("motivo") or "").lower():
        fallos.append("motivo '%s' sin '%s'" % (r.get("motivo"), mc))
    if fallos:
        return _r(False, "; ".join(fallos))
    return _r(True, "brain=%s deg=%s def=%s" % (r.get("brain"), r.get("degradado"), r.get("deferred")))


# ── Sello de evidencia: las dos varas que faltaban (25-jul-26) ───────────────────────────────
# El golden set medía el muro con 17 casos, el router con 7 y la calidad con 3, y el SELLO DE
# EVIDENCIA con cero. O sea: de las dos reglas que CLAUDE.md llama inquebrantables, una tenía
# diecisiete casos gratis y la otra ninguno — y la que no tenía vara es justo la que ya falló en
# producción (el «Baifo = caja negra» del 28-jun, relayado como hecho sin verificar).
#
# Los dos evaluadores son DETERMINISTAS, OFFLINE y $0, igual que el resto del carril gratis:
#   · `sello` → honestidad_lint.lint_text(texto): ¿cuántas afirmaciones fuertes sin sello?
#   · `citas` → verifica_citas.clasifica(cadena): ¿extrae el id de una cita real?
# `citas` NO sale a la red a propósito. Comprobar si un DOI existe depende de doi.org, y un caso que
# se pone rojo porque el wifi va mal enseña a ignorar el rojo. Lo que sí se puede medir sin red —y es
# donde estaba el agujero— es el PARSEO: un DOI o un NCT fabricados dentro de una bibliografía se
# devolvían como `no_resoluble`, que el agente `verificacion` tiene instrucción de NO acusar.
def _ev_sello(caso):
    """Cuenta las afirmaciones fuertes sin sello de un texto. `espera`: {flags: N} exacto, o
    {flags_min: N} / {flags_max: N} para casos donde el número exacto es frágil."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import honestidad_lint
    n = len(honestidad_lint.lint_text(caso.get("texto", "")))
    esp = caso.get("espera", {})
    if "flags" in esp and n != esp["flags"]:
        return _r(False, "%d flag(s), esperaba %d" % (n, esp["flags"]))
    if "flags_min" in esp and n < esp["flags_min"]:
        return _r(False, "%d flag(s), esperaba >= %d" % (n, esp["flags_min"]))
    if "flags_max" in esp and n > esp["flags_max"]:
        return _r(False, "%d flag(s), esperaba <= %d" % (n, esp["flags_max"]))
    return _r(True, "%d flag(s)" % n)


def _ev_citas(caso):
    """Clasifica una cadena de cita. `espera`: {clase: 'doi'|'pmid'|'nct'|'arxiv'|'desconocido',
    id?: '…', estado?: 'no_parseable'}.

    `estado` solo se comprueba cuando la clase esperada es 'desconocido', y ahí `verifica()` NO sale
    a la red (corta antes de llamar a ningún checker). Es la distinción que sostiene todo esto: «no
    encontré ninguna cita» tiene que llegar como `no_parseable`, porque el agente `verificacion`
    tiene instrucción de NO acusar ante `no_resoluble` — si las dos cosas llegan con la misma
    etiqueta, una cita inventada se cuela con el mismo silencio que un corte de wifi."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import verifica_citas
    clase, ident = verifica_citas.clasifica(caso.get("cadena", ""))
    esp = caso.get("espera", {})
    if clase != esp.get("clase"):
        return _r(False, "clase=%r, esperaba %r (id=%r)" % (clase, esp.get("clase"), ident))
    if "id" in esp and ident != esp["id"]:
        return _r(False, "id=%r, esperaba %r" % (ident, esp["id"]))
    if "estado" in esp:
        if clase != "desconocido":
            return _r(False, "el caso pide `estado` con clase %r: eso saldría a la red y esta "
                             "categoría es offline. Quítalo o usa una cadena sin id." % clase)
        est = verifica_citas.verifica([caso.get("cadena", "")])[0]["estado"]
        if est != esp["estado"]:
            return _r(False, "estado=%r, esperaba %r" % (est, esp["estado"]))
        return _r(True, "clase=%s estado=%s" % (clase, est))
    return _r(True, "clase=%s id=%s" % (clase, ident))


# ── Categoría calidad: LLM-juez (opt-in, aplazo honesto, anti-Goodhart) ──────────────────────
def _parse_score(texto):
    """Extrae {score, motivo, critica_rubrica} del JSON que devuelve el juez (tolerante a texto
    alrededor). `critica_rubrica` (delta minado de skill-creator/grader de Alby, 28/6): el juez
    señala criterios que NO discriminan —los cumpliría también una respuesta mala— para que la
    rúbrica se endurezca. Optativo: si el juez no lo da, queda vacío."""
    if not texto:
        return None, "", ""
    try:
        i, j = texto.index("{"), texto.rindex("}") + 1
        d = json.loads(texto[i:j])
        s = max(0.0, min(1.0, float(d.get("score"))))   # clamp a [0,1]
        return s, str(d.get("motivo", "")), str(d.get("critica_rubrica", "") or "")
    except Exception:
        return None, "", ""


def _otro_cerebro(subject_brain):
    """Nombre de un cerebro del registro REAL distinto del sujeto (para forzar juez ≠ sujeto)."""
    for c in ia.cargar_registro():
        if c.get("enabled") and c.get("name") != subject_brain:
            return c.get("name")
    return None


def _ev_calidad(caso, con_juez):
    if not con_juez:
        return _r(None, "juez desactivado (usa `run --judge` para puntuar)", deferred=True)
    # 1) respuesta real del SUJETO (registro real; pasa por el borde dentro de ia.ask)
    r = ia.ask(caso.get("prompt", ""), clinico=bool(caso.get("clinico")))
    if not r.get("text"):
        return _r(None, "sin cerebro para responder (%s)" % r.get("motivo"), deferred=True)
    subject = r.get("brain")
    # 2) ANTI-GOODHART: el juez debe ser OTRO cerebro
    otro = _otro_cerebro(subject)
    if not otro:
        return _r(None, "solo hay 1 cerebro; el juez sería el sujeto (anti-Goodhart) → aplazo",
                  deferred=True, extra={"subject_brain": subject})
    rub = caso.get("rubrica", "Evalúa la calidad y fidelidad de la respuesta.")
    jprompt = ("Eres un JUEZ imparcial (no eres quien respondió). Evalúa la RESPUESTA contra los "
               "CRITERIOS. Devuelve SOLO un JSON: {\"score\": <0.0-1.0>, \"motivo\": \"<breve>\", "
               "\"critica_rubrica\": \"<vacío si los criterios discriminan bien; si NO, señala qué "
               "criterio lo cumpliría también una respuesta claramente mala>\"}.\n\n"
               "PREGUNTA:\n%s\n\nCRITERIOS:\n%s\n\nRESPUESTA A EVALUAR:\n%s\n" %
               (caso.get("prompt", ""), rub, r["text"]))
    jr = ia.ask(jprompt, prefer=otro)
    if not jr.get("text"):
        return _r(None, "sin cerebro-juez disponible (%s)" % jr.get("motivo"), deferred=True,
                  extra={"subject_brain": subject})
    judge = jr.get("brain")
    if judge == subject:
        return _r(None, "el juez cayó en el mismo cerebro que el sujeto (%s) → aplazo anti-Goodhart"
                  % subject, deferred=True, extra={"subject_brain": subject, "judge_brain": judge})
    score, motivo, critica = _parse_score(jr["text"])
    if score is None:
        return _r(None, "el juez no devolvió un score parseable", deferred=True,
                  extra={"subject_brain": subject, "judge_brain": judge})
    umbral = float(caso.get("umbral", 0.7))
    detalle = "score=%.2f vs umbral=%.2f — %s" % (score, umbral, motivo)
    extra = {"subject_brain": subject, "judge_brain": judge}
    if critica:
        detalle += " | ⚠️ rúbrica poco discriminante: %s" % critica
        extra["critica_rubrica"] = critica
    return _r(score >= umbral, detalle, score=score, extra=extra)


_EVALUADORES = {"clasificar": _ev_clasificar, "egress": _ev_egress, "ingeniero": _ev_cientifico,
                "router": _ev_router, "sello": _ev_sello, "citas": _ev_citas}


def correr_caso(caso, con_juez=False):
    tipo = caso.get("tipo")
    try:
        if tipo == "calidad":
            res = _ev_calidad(caso, con_juez)
        elif tipo in _EVALUADORES:
            res = _EVALUADORES[tipo](caso)
        else:
            res = _r(False, "tipo desconocido: %s" % tipo)
    except Exception as e:
        res = _r(False, "excepción: %r" % e)
    res.update({"id": caso.get("id"), "tipo": tipo, "held_out": bool(caso.get("held_out")),
                "origen": caso.get("origen", "?")})
    return res


# ── Scorecard + deriva ──────────────────────────────────────────────────────────────────────
def _resumir(resultados):
    by = {}
    for r in resultados:
        b = by.setdefault(r["tipo"], {"pasa": 0, "falla": 0, "deferred": 0})
        if r["deferred"]:
            b["deferred"] += 1
        elif r["paso"]:
            b["pasa"] += 1
        else:
            b["falla"] += 1
    for t, b in by.items():
        medidos = b["pasa"] + b["falla"]
        b["pass_rate"] = round(b["pasa"] / medidos, 4) if medidos else None
    tot = {"pasa": sum(b["pasa"] for b in by.values()),
           "falla": sum(b["falla"] for b in by.values()),
           "deferred": sum(b["deferred"] for b in by.values())}
    return {"por_tipo": by, "total": tot}


def guardar_scorecard(resumen, resultados):
    os.makedirs(OUT_DIR, exist_ok=True)
    sc = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
          "resumen": resumen, "resultados": resultados}
    fecha = datetime.now().strftime("%Y-%m-%d")
    json.dump(sc, open(os.path.join(OUT_DIR, "scorecard-%s.json" % fecha), "w"),
              ensure_ascii=False, indent=2)
    tmp = os.path.join(OUT_DIR, "scorecard.json.tmp")
    json.dump(sc, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(OUT_DIR, "scorecard.json"))
    return sc


def fijar_baseline():
    p = os.path.join(OUT_DIR, "scorecard.json")
    if not os.path.exists(p):
        return False, "no hay scorecard; corre `evals.py run` primero"
    sc = json.load(open(p, encoding="utf-8"))
    json.dump(sc, open(os.path.join(OUT_DIR, "baseline.json"), "w"), ensure_ascii=False, indent=2)
    return True, "baseline fijada (%s)" % sc.get("ts")


def calcular_deriva():
    """Compara la última corrida con la baseline; alerta si algún tipo cae > UMBRAL_DERIVA."""
    bp = os.path.join(OUT_DIR, "baseline.json")
    sp = os.path.join(OUT_DIR, "scorecard.json")
    if not (os.path.exists(bp) and os.path.exists(sp)):
        return {"ok": False, "motivo": "falta baseline o scorecard"}
    base = json.load(open(bp))["resumen"]["por_tipo"]
    ult = json.load(open(sp))["resumen"]["por_tipo"]
    derivas, alertas = [], []
    for t in sorted(set(base) | set(ult)):
        b = (base.get(t) or {}).get("pass_rate")
        u = (ult.get(t) or {}).get("pass_rate")
        if b is None or u is None:
            continue
        d = round(b - u, 4)            # positivo = empeoró
        item = {"tipo": t, "baseline": b, "ultimo": u, "caida": d}
        derivas.append(item)
        if d > UMBRAL_DERIVA:
            alertas.append(item)
    return {"ok": True, "derivas": derivas, "alertas": alertas}


# ── Regresiones: promover un fallo de producción al golden set ──────────────────────────────
def add_regresion(caso):
    if isinstance(caso, str):
        caso = json.loads(caso)
    if not isinstance(caso, dict) or not caso.get("id") or caso.get("tipo") not in TIPOS:
        return False, "caso inválido (necesita id y tipo válido)"
    caso.setdefault("origen", "regresion-prod")
    try:
        lista = json.load(open(REGRESIONES, encoding="utf-8")) if os.path.exists(REGRESIONES) else []
    except Exception:
        lista = []
    if any(c.get("id") == caso["id"] for c in lista):
        return False, "ya existe una regresión con id '%s'" % caso["id"]
    lista.append(caso)
    os.makedirs(EVALS_DIR, exist_ok=True)
    json.dump(lista, open(REGRESIONES, "w"), ensure_ascii=False, indent=2)
    return True, "regresión '%s' añadida al golden set (%d en total)" % (caso["id"], len(lista))


# ── Orquestación + render ───────────────────────────────────────────────────────────────────
def correr(tipos=None, con_juez=False, incluir_held_out=True):
    casos = cargar_casos(tipos=tipos, incluir_held_out=incluir_held_out)
    resultados = [correr_caso(c, con_juez=con_juez) for c in casos]
    resumen = _resumir(resultados)
    guardar_scorecard(resumen, resultados)
    return resumen, resultados


def _render(resumen, resultados, verbose=False):
    L = ["🧪 EVALS — %s" % datetime.now().strftime("%Y-%m-%d %H:%M")]
    for t in TIPOS:
        b = resumen["por_tipo"].get(t)
        if not b:
            continue
        pr = "n/a" if b["pass_rate"] is None else "%.0f%%" % (100 * b["pass_rate"])
        defs = " · %d aplazados" % b["deferred"] if b["deferred"] else ""
        L.append("  %-11s %d ✅ / %d ❌  (pass %s)%s" % (t, b["pasa"], b["falla"], pr, defs))
    tot = resumen["total"]
    L.append("  TOTAL      %d ✅ / %d ❌ / %d ⏸️" % (tot["pasa"], tot["falla"], tot["deferred"]))
    fallos = [r for r in resultados if r["paso"] is False]
    if fallos or verbose:
        L.append("")
        for r in (resultados if verbose else fallos):
            ic = "✅" if r["paso"] else ("⏸️" if r["deferred"] else "❌")
            L.append("  %s [%s] %s — %s" % (ic, r["tipo"], r["id"], r["detalle"]))
    if tot["deferred"]:
        L.append("\n  ⏸️ %d caso(s) aplazado(s) (juez/saldo). Corre con `--judge` cuando haya saldo; "
                 "nunca se inventa una nota." % tot["deferred"])
    return "\n".join(L)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd = argv[0]
    if cmd == "run":
        con_juez = "--judge" in argv
        incluir_ho = "--no-held-out" not in argv
        tipos = None
        if "--tipo" in argv:
            tipos = tuple(argv[argv.index("--tipo") + 1].split(","))
        resumen, resultados = correr(tipos=tipos, con_juez=con_juez, incluir_held_out=incluir_ho)
        if "--json" in argv:
            print(json.dumps({"resumen": resumen, "resultados": resultados}, ensure_ascii=False, indent=2))
        else:
            print(_render(resumen, resultados, verbose="--verbose" in argv))
        # exit = nº de fallos REALES (los aplazados NO cuentan: no son rojos, son honestos)
        return resumen["total"]["falla"]
    if cmd == "drift":
        d = calcular_deriva()
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 1 if d.get("alertas") else 0
    if cmd == "baseline":
        ok, msg = fijar_baseline()
        print(msg); return 0 if ok else 1
    if cmd == "add-regression":
        if len(argv) < 2:
            print("uso: evals.py add-regression '<json del caso>'"); return 2
        ok, msg = add_regresion(argv[1])
        print(msg); return 0 if ok else 1
    print("uso: evals.py [run [--judge] [--tipo ...] [--no-held-out] [--json] | drift | baseline | "
          "add-regression '<json>']", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
