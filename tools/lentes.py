#!/usr/bin/env python3
"""tools/lentes.py — el enrutador de reserva de la caja: cuando la tabla no sabe de qué tema es, qué ÁNGULOS tiene.

POR QUÉ EXISTE (13-sep-2026, paso 4 del plan de la caja). `enruta_comite.py` tiene 16 reglas que
contestan «¿de qué tema es esto?». Cuando ninguna casa, no sale ningún comité, y la petición la hace
la sesión sola aunque sea una decisión con peso («quiero mudarme antes de noviembre, cuesta 900 €
más y me pilla lejos del hospital»). El mundo de temas es abierto; el de ÁNGULOS de una decisión es
cerrado. Por eso el fallback no adivina el tema: sienta lentes fijas.

LAS LENTES (3 a 5 asientos; `contra` nunca es opcional, porque el fallo del 5-sep no fue falta de
análisis, fue que nadie intentó tumbarlo):
  · contra       → `verificacion`            siempre
  · impacto-ned  → `asistente` (Vega)        siempre: prioriza por impacto en NED
  · coste-plazo  → `finanzas-transparencia`  solo si hay dinero en juego
  · acceso       → `consejero-acceso`        solo si hay que conseguir algo o a alguien, o como
                                              tercer asiento mínimo
  · dominio      → NO se sienta (medido: 16 de 17 veces era `voz-titular`, por léxico). Se calcula
                   con `capacidades.buscar` sobre el umbral de reuso y solo se APUNTA como candidato.
                   Sin umbral, `capacidades` sentaba a `git` para planificar una mudanza.

CAPA DE SILENCIO: si la petición no es un encargo con peso (verbo de decisión o encargo Y además
plazo o consecuencia), no pasa nada. Un fallback que salta con cada «vale, gracias» se ignora.

MODO SUGERIDO: esto CALCULA el panel pero NO lo ordena. `decide_peticion.guardar` lo apunta en
`tools/state/enrutado/fallback.jsonl` para medir una semana de prompts reales y decidir si el umbral
vale. Es lo único que lo calibra. Determinista, sin LLM, sin red.

Uso:
  python3 tools/lentes.py "quiero mudarme antes de noviembre y cuesta 900 € más al mes"
  python3 tools/lentes.py --replay 7      # prompts REALES de 7 días: cuántos caerían aquí (no escribe)
"""
import json
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

# ── capa de silencio ─────────────────────────────────────────────────────────────────────────
ENCARGO = re.compile(
    r"\b(decid\w*|decisi[oó]n|eleg\w*|elij\w*|escog\w*|compar\w*|valor\w*|organiz\w*|prepar\w*|"
    r"planific\w*|conviene|deber[íi]a|vale la pena|merece la pena|qu[ée] hago|qu[ée] har[íi]as|"
    r"ay[úu]dame a|necesito|quiero|busca(me)?|consigue|mont\w*|contrat\w*|compr\w*|cambi\w*|"
    r"mud\w*|pedir|solicit\w*|apunt\w*|inscrib\w*)\b", re.I)
PLAZO = re.compile(
    r"\b(ma[ñn]ana|hoy|esta semana|este mes|antes de|para el|plazo|fecha l[íi]mite|urgente|"
    r"cuanto antes|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|enero|febrero|marzo|"
    r"abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b|\b\d{1,2}[/-]\d{1,2}\b",
    re.I)
CONSECUENCIA = re.compile(
    r"\b(cuesta\w*|coste|precio|pag\w*|dinero|euros?|riesgo|arriesg\w*|pierdo|perder|afecta\w*|"
    r"impacto|consecuencia\w*|lejos|cerca|salud|energ[íi]a|me pilla|compromet\w*|firmar)\b|€|\$",
    re.I)
DINERO = re.compile(r"\b(cuesta\w*|coste|precio|pag\w*|dinero|euros?|presupuesto|factura|"
                    r"alquiler|hipoteca|salario|ahorro\w*)\b|€|\$", re.I)
ACCESO = re.compile(r"\b(conseguir|consigue|contactar|acceso|entrar en|pedir|solicit\w*|"
                    r"quien|qui[ée]n|contacto|cita con|plaza|admisi[oó]n|recomendaci[oó]n)\b", re.I)

UMBRAL_DOMINIO = None      # se lee de capacidades.UMBRAL_REUSA al vuelo
# Agentes que no son dueños de un DOMINIO de decisión, aunque el léxico los acerque.
NO_DOMINIO = frozenset({"git", "tecnico", "auto-mejora", "orquestador", "constructor",
                        "monitor-lanzamiento", "coach-colaboracion", "verificacion", "asistente"})


def tiene_peso(texto):
    """¿Es un encargo con peso? Verbo de decisión/encargo Y (plazo o consecuencia)."""
    t = str(texto or "")
    return bool(ENCARGO.search(t) and (PLAZO.search(t) or CONSECUENCIA.search(t)))


def panel(texto):
    """[{lente, comite, motivo}] entre 3 y 5 asientos, o [] si no hay peso."""
    t = str(texto or "").strip()
    if not t or not tiene_peso(t):
        return []
    asientos = [
        {"lente": "contra", "comite": "verificacion",
         "motivo": "alguien tiene que intentar tumbar la decisión antes de tomarla"},
        {"lente": "impacto-ned", "comite": "asistente",
         "motivo": "cómo acerca o aleja esto de NED (energía, citas, acceso al hospital)"},
    ]
    if DINERO.search(t):
        asientos.append({"lente": "coste-plazo", "comite": "finanzas-transparencia",
                         "motivo": "hay dinero en juego: cuánto cuesta de verdad y a qué plazo"})
    if ACCESO.search(t):
        asientos.append({"lente": "acceso", "comite": "consejero-acceso",
                         "motivo": "hay que conseguir algo o a alguien: cuál es el cuello de botella"})
    # SIN asiento de dominio (13-sep-2026). Medido con --replay sobre 940 prompts reales de 7 días:
    # de 17 dominios sentados, 16 eran `voz-titular`, por léxico y no por oficio (lo mismo que midió el
    # paso 0.1 con los agentes forjados). Un asiento que casi siempre es el mismo comité equivocado es
    # ruido con cara de experto. `dominio()` se sigue calculando, pero solo se APUNTA como candidato
    # en el registro (decide_peticion.guardar) hasta que exista un comparador por oficio.
    if len(asientos) < 3:
        # Sin dinero, sin acceso y sin dueño claro: el tercer asiento es acceso, porque en este
        # sistema casi toda decisión con plazo acaba en «a quién hay que pedírselo».
        asientos.append({"lente": "acceso", "comite": "consejero-acceso",
                         "motivo": "tercer asiento mínimo: quién o qué desbloquea esto"})
    return asientos[:5]


def dominio(texto):
    """El comité dueño del tema según capacidades, solo si pasa el umbral de reuso. None si no."""
    try:
        import capacidades
        umbral = capacidades.UMBRAL_REUSA
        for score, slug, _desc in capacidades.buscar(texto, 5):
            if slug in NO_DOMINIO:
                continue
            return {"comite": slug, "score": score} if score >= umbral else None
    except Exception:
        return None
    return None


# ── replay sobre prompts reales ──────────────────────────────────────────────────────────────
def _prompts_reales(dias):
    import time
    import coste
    corte = time.time() - dias * 86400
    prefijos = ("<task-notification", "<ci-monitor-event", "<local-command", "<command-name",
                "<bash-input", "<bash-stdout", "[SYSTEM NOTIFICATION", "<system-reminder")
    for proy in coste.proyectos_del_repo():
        for f in coste.transcripts(proy):
            if "/subagents/" in f:
                continue
            try:
                if os.path.getmtime(f) < corte:
                    continue
                fh = open(f, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for linea in fh:
                    if '"type":"user"' not in linea:
                        continue
                    try:
                        r = json.loads(linea)
                    except Exception:
                        continue
                    c = (r.get("message") or {}).get("content")
                    if isinstance(c, list):
                        c = " ".join(b.get("text", "") for b in c
                                     if isinstance(b, dict) and b.get("type") == "text")
                    if not isinstance(c, str) or not c.strip() or r.get("isMeta"):
                        continue
                    if c.lstrip().startswith(prefijos):
                        continue
                    yield c.strip()


def replay(dias=7):
    import collections
    import enruta_comite
    total = sin_tabla = con_peso = 0
    por_lente = collections.Counter()
    dominios = collections.Counter()
    for p in _prompts_reales(dias):
        total += 1
        if enruta_comite.decidir(p)["comites"]:
            continue
        sin_tabla += 1
        asientos = panel(p)
        if not asientos:
            continue
        con_peso += 1
        for a in asientos:
            por_lente[a["lente"]] += 1
            if a["lente"] == "dominio":
                dominios[a["comite"]] += 1
    print("prompts reales en %d días: %d · sin comité por la tabla: %d · con peso (darían panel): %d"
          % (dias, total, sin_tabla, con_peso))
    print("asientos por lente:", por_lente.most_common())
    print("dominios sentados:", dominios.most_common(10))
    return 0


def main(argv):
    if "--replay" in argv:
        i = argv.index("--replay")
        dias = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 7
        return replay(dias)
    texto = " ".join(a for a in argv if not a.startswith("--"))
    print(json.dumps({"peso": tiene_peso(texto), "panel": panel(texto)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        sys.stderr.write("lentes: %r\n" % e)
        sys.exit(0)
