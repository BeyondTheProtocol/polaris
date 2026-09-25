#!/usr/bin/env python3
"""tools/gate_escalera.py — la ESCALERA del gate de salida: por check, cuántos casos hay
etiquetados, cuántos falsos positivos, qué cota superior de FP garantiza eso, y qué escalón PROPONE.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
Plan: `00_FUENTE-DE-VERDAD/04 · IA/Notas/p2-escalera-del-gate-verificación-y-plan-2026-09-25.md`.

POR QUÉ. El criterio viejo (`gate_etiqueta.py`) subía checks a bloqueo con 1, 7 o 20 casos: con
0 FP en 1 caso, la cota superior al 95 % de avisos innecesarios es 95 %. Y el 25-sep los 4 checks
que bloquean tenían 0 etiquetas. Esta tool pone el número delante antes de cada subida.

ESCALONES: sombra (solo apunta) → aviso → bloqueo (una reescritura por turno).
LISTÓN para subir a bloqueo, según lo que cuesta un falso positivo (comité verificacion, 25-sep):
  · estilo          → cota ≤ 15 %  (p.ej. 30 etiquetados con ≤1 FP)
  · suprime_info    → cota ≤ 6,5 % (59 limpios o 100 con ≤2 FP) + recall medido. Por defecto:
                      un FP de estos empuja a borrar algo verdadero, o sea a mentir por omisión.
MARCHA ATRÁS: 3 FP seguidos entre los últimos etiquetados, o cota > 30 % con ≥20 etiquetados, en
un check que bloquea → propone bajar a aviso.

MIDE Y PROPONE, NUNCA ESCRIBE. Cada subida es una línea en `tools/normas.json` que firma {{TITULAR}}.
Cota: Clopper-Pearson a una cola 95 %, stdlib (bisección sobre la binomial).

CLI:
  python3 tools/gate_escalera.py [--check X] [--json]
Recall: casos malos sembrados en `tests/gate_sembrados.json` (offline; los checks con red no).
"""
import json
import os
import sys
from math import comb

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gate_etiqueta as ge  # noqa: E402

NORMAS = os.path.join(HERE, "normas.json")
ALFA = 0.05
UMBRAL = {"estilo": 0.15, "suprime_info": 0.065}
BAJAR_COTA, BAJAR_MIN_N, BAJAR_SEGUIDOS = 0.30, 20, 3
# Clase por defecto de los checks de estilo; el resto cuenta como `suprime_info` (el listón
# estricto) salvo que su norma diga otra cosa en `escalera.clase`.
ESTILO = {"tells_ia", "secuencia_sin_tabla"}
# Fijos a bloqueo por diseño (`gate_salida.SIEMPRE_BLOQUEA`): la escalera no propone bajarlos;
# si acumulan FP, lo que toca es arreglar el check. Caso real: los 4 FP de citas_fabricadas
# (25-sep-26) eran bugs de extracción/registro ya arreglados (1dfe573, filtro de plantilla P2).
FIJOS = {"citas_fabricadas"}


def cota_superior(fp, n, alfa=ALFA):
    """Cota superior Clopper-Pearson (una cola) de la tasa de FP con `fp` fallos en `n`."""
    if n <= 0:
        return 1.0
    if fp >= n:
        return 1.0

    def cdf(p):
        return sum(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(fp + 1))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = (lo + hi) / 2
        if cdf(m) > alfa:
            lo = m
        else:
            hi = m
    return lo


SEMBRADOS = os.path.join(REPO, "tests", "gate_sembrados.json")
GATE = os.path.join(REPO, ".claude", "hooks", "gate_salida.py")
RECALL_MIN = 0.90          # cota INFERIOR exigida: 30/30 sembrados la da (90,5 %); 27/30, solo 76 %


def recall(ruta=SEMBRADOS, gate=GATE):
    """{check: (cazados, total)} corriendo cada check sobre sus casos malos sembrados. Offline."""
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        import importlib.util
        spec = importlib.util.spec_from_file_location("gate_escalera_gate", gate)
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
    except Exception:
        return {}
    out = {}
    for c, casos in d.items():
        if c.startswith("_") or c not in g.CHECKS:
            continue
        # Si hay lote APARTADO, el recall sale de él: el de entrenamiento ya se usó para ajustar
        # el check y daría una cifra inflada.
        casos = d.get(c + "_holdout", casos)
        out[c] = (sum(1 for t in casos if g.CHECKS[c](t, [])), len(casos))
    return out


def _normas_por_check(ruta=NORMAS):
    """{check: {"modo": ..., "clase": ...}} leído de normas.json (mecanismo `gate_salida.py::X`)."""
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    lista = d if isinstance(d, list) else d.get("normas", [])
    out = {}
    for n in lista:
        mec = n.get("mecanismo") or ""
        if "gate_salida.py::" not in mec:
            continue
        check = mec.split("::", 1)[1].split()[0]
        r = out.setdefault(check, {})
        desde = (n.get("escalera") or {}).get("desde")
        if desde:
            r["desde"] = desde
        if n.get("modo") and r.get("modo") != "bloqueo":     # el más estricto manda
            r["modo"] = n["modo"]
        clase = (n.get("escalera") or {}).get("clase")
        if clase:
            r["clase"] = clase
    return out


def _ts(fecha):
    """«2026-09-25» o «2026-09-25T18:30» → epoch local."""
    import datetime
    try:
        return datetime.datetime.fromisoformat(fecha).timestamp()
    except Exception:
        return 0


def medir(normas=None, rec=None):
    normas = _normas_por_check() if normas is None else normas
    rec = recall() if rec is None else rec
    etiquetas = ge._cargar_etiquetas()
    por_check = {}
    for id_, fila in ge._leer_log():
        # Con `ts` y sin sesión = la escribió un test o un `--check` a mano sobre el log vivo,
        # no una respuesta real (fila 1114, «El inhibidor A…», 25-sep-26). No cuenta.
        if fila.get("ts") and not fila.get("session_hash"):
            continue
        c = fila.get("check")
        # `escalera.desde`: el check se reescribió; lo medido con la versión vieja no la juzga.
        desde = normas.get(c, {}).get("desde")
        if desde and (fila.get("ts") or 0) < _ts(desde):
            continue
        r = por_check.setdefault(c, {"disparos": 0, "veredictos": []})
        r["disparos"] += 1
        et = etiquetas.get(id_)
        if et and et.get("veredicto") in ge.VEREDICTOS:
            r["veredictos"].append((int(id_), et["veredicto"]))
    for c in normas:
        por_check.setdefault(c, {"disparos": 0, "veredictos": []})

    out = {}
    for c, r in por_check.items():
        vs = [v for _i, v in sorted(r["veredictos"])]
        n, fp = len(vs), vs.count("falso_positivo")
        nr = normas.get(c, {})
        modo = nr.get("modo", "aviso")
        clase = nr.get("clase") or ("estilo" if c in ESTILO else "suprime_info")
        cota = cota_superior(fp, n)
        seguidos = len(vs) >= BAJAR_SEGUIDOS and all(
            v == "falso_positivo" for v in vs[-BAJAR_SEGUIDOS:])
        propone, porque = modo, ""
        if modo == "bloqueo" and c in FIJOS:
            if fp:
                porque = "fijo a bloqueo: %d FP → arreglar el check, no bajarlo" % fp
            elif n == 0:
                porque = "fijo a bloqueo, sin etiquetas"
        elif modo == "bloqueo":
            if seguidos:
                propone, porque = "aviso", "%d FP seguidos" % BAJAR_SEGUIDOS
            elif n >= BAJAR_MIN_N and cota > BAJAR_COTA:
                propone, porque = "aviso", "cota %.0f %% > %.0f %%" % (cota * 100, BAJAR_COTA * 100)
            elif n == 0:
                porque = "bloquea SIN ninguna etiqueta: bloqueo no respaldado"
        cazados, total = rec.get(c, (0, 0))
        rec_inf = 1 - cota_superior(total - cazados, total) if total else None
        if modo in ("aviso", "sombra") and n and cota <= UMBRAL[clase]:
            if clase == "suprime_info" and (rec_inf is None or rec_inf < RECALL_MIN):
                porque = ("listón de FP cumplido; recall %s" % (
                    "sin medir (faltan casos sembrados)" if rec_inf is None else
                    "%d/%d, cota inf. %.0f %% < %.0f %%" % (cazados, total, rec_inf * 100,
                                                           RECALL_MIN * 100)))
            else:
                propone, porque = "bloqueo", "cota %.1f %% ≤ %.0f %%" % (cota * 100,
                                                                      UMBRAL[clase] * 100)
        out[c] = {"modo": modo, "clase": clase, "disparos": r["disparos"], "etiquetados": n,
                  "fp": fp, "cota_fp": round(cota, 4), "propone": propone, "porque": porque,
                  "recall": "%d/%d" % (cazados, total) if total else None,
                  "recall_inf": round(rec_inf, 4) if rec_inf is not None else None}
    return out


def main(argv):
    solo = argv[argv.index("--check") + 1] if "--check" in argv[:-1] else None
    m = medir()
    if solo:
        m = {k: v for k, v in m.items() if k == solo}
    if "--json" in argv:
        print(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    print("%-24s %-7s %-12s %8s %5s %4s %8s %7s  %s" % (
        "check", "modo", "clase", "disparos", "etiq", "FP", "cota FP", "recall", "propone"))
    for c, v in sorted(m.items(), key=lambda kv: (kv[1]["modo"] != "bloqueo", kv[0] or "")):
        cambio = "→ %s" % v["propone"] if v["propone"] != v["modo"] else "="
        print("%-24s %-7s %-12s %8d %5d %4d %7.1f%% %7s  %s %s" % (
            c, v["modo"], v["clase"], v["disparos"], v["etiquetados"], v["fp"],
            v["cota_fp"] * 100, v["recall"] or "—", cambio, v["porque"]))
    print("— mide y propone; cada subida la firma {{TITULAR}} en tools/normas.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
