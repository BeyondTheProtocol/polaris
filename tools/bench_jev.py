#!/usr/bin/env python3
"""tools/bench_jev.py — Jev (TypeSafe AI) contra el set dorado de triage, y contra lo que ya hay.

Por qué existe (21-sep-26): {{TITULAR}} consiguió acceso a Jev. La pregunta no es «¿qué tal va Jev?»
sino «¿decide mejor que `triage_tareas.clasificar`, que ya está en producción y cuesta 0 ms?».
Así que se mide lo mismo que en `bench_determinista.py`: no solo acierto a cobertura total, sino
CUÁNTO cubre y CUÁNTO acierta cuando se moja, porque un «dudosa» va a una cola humana y no se
pierde nada, mientras que una tarea real tirada como ruido con confianza sí se pierde.

EL MURO, antes que la métrica. Jev es un tercero alojado en EE. UU. con retención sin plazo
(política verificada el 21-sep-26: no entrenan con el input, pero lo guardan «as long as
reasonably necessary»). Por eso:

  · Solo lee el set DE-IDENTIFICADO (`triage_golden.jsonl`). No hay modo crudo, a propósito.
  · Cada caso pasa, uno a uno y ANTES de salir, por `borde.clasificar`, `borde.hay_canario` y
    un filtro extra (fechas, @handles, números largos, URLs). Cualquiera que salte → no se
    envía y se cuenta como bloqueado. Fail-closed: si el borde revienta, no sale nada.
  · El filtro extra existe porque el set «de-identificado» dejó pasar una fecha con pinta de
    nacimiento junto a un correo del hospital, y el borde lo daba por limpio (deuda
    `eval-triage-deja-fecha-nacimiento-y-handles`). Hasta cerrarla, el parche vive aquí.
  · No persiste respuestas ni textos: solo imprime el resumen.

Uso:
  python3 tools/bench_jev.py                 # set completo (tras el filtro)
  python3 tools/bench_jev.py --limite 20     # prueba corta
  python3 tools/bench_jev.py --seco          # cuenta qué saldría, sin llamar a Jev
  python3 tools/bench_jev.py --n1            # ¿juzga el encaje? Exige trust-cloud de {{TITULAR}}
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import borde
import score_local as sl
import triage_tareas as tt

URL = "https://api.typesafe.ai/v1/systemone"
MODELO = "jev-latest"
LLAVERO = "btp-typesafe-api"
HILOS = 4
UMBRALES = (0.80, 0.90, 0.95, 0.98)

# Lo que el borde todavía no caza en el set redactado (ver cabecera). Mejor pasarse.
_EXTRA = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|@\w|\d{5,}|https?://")

# Instrucción del radar NED (N0: solo título + tema). Validada en el piloto del 21-sep-26 sobre
# 47 leads cerrados (AUC 0,85). Solo mide si el lead es DE {{DIAGNOSTICO}} HR+; no juzga el
# encaje, y hunde a propósito los carriles cruzados, que por eso no se puntúan con ella.
INSTR_RADAR = ("This item is about metastatic hormone-receptor-positive breast cancer, neuroendocrine "
               "features of breast cancer, or the stated target or therapy in a form that could apply "
               "to metastatic breast cancer. Items about other diseases (other tumor types, non-cancer "
               "topics), or only about early-stage or only triple-negative breast cancer, do NOT count.")

INSTR = ("Esto debe entrar en la lista de tareas pendientes de una persona porque ELLA tiene que "
         "hacer algo. La publicidad, las newsletters, los acuses de recibo y la informacion sin "
         "accion NO entran, aunque pidan hacer clic.")


def puede_salir(texto):
    """(bool, motivo). Fail-closed: cualquier duda o excepción → no sale."""
    try:
        sens, motivo = borde.clasificar(texto)
        if sens:
            return False, motivo
        if borde.hay_canario(texto):
            return False, "canario"
        if _EXTRA.search(texto):
            return False, "fecha / @handle / número largo / URL"
        return True, "limpio"
    except Exception as e:
        return False, f"borde reventó ({type(e).__name__})"


def _clave():
    r = subprocess.run(["security", "find-generic-password", "-s", LLAVERO, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"⛔ falta la clave `{LLAVERO}` en el llavero")
    return r.stdout.strip()


def preguntar(texto, clave, timeout=30, instr=None):
    """(prob_sí, ms). Una pregunta Noul: Jev devuelve la probabilidad de «sí».
    `instr` por defecto es la del triage (`INSTR`); el radar pasa `INSTR_RADAR`."""
    cuerpo = json.dumps({"state": texto, "model": MODELO,
                         "questions": {"es_tarea": {"type": "noul",
                                                    "instructions": instr or INSTR}}}).encode()
    req = urllib.request.Request(URL, data=cuerpo, headers={
        "Authorization": f"Bearer {clave}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        d = json.loads(fh.read())
    return float(d["answers"]["es_tarea"]["noul"]), (time.time() - t0) * 1000


def _filas(limite=None):
    if not os.path.exists(sl.GOLDEN):
        raise SystemExit(f"⛔ falta el set dorado: {sl.GOLDEN}")
    filas = [json.loads(l) for l in open(sl.GOLDEN, encoding="utf-8") if l.strip()]
    filas = [f for f in filas if f.get("pregunta") == "es_tarea" and f["etiqueta"] in ("tarea", "no")]
    return filas[:limite] if limite else filas


def bench(limite=None, seco=False):
    todas = _filas(limite)
    salen, bloq = [], []
    for f in todas:
        ok, motivo = puede_salir(f["texto"])
        (salen if ok else bloq).append((f, motivo))
    print(f"\n  muro: {len(salen)} casos pueden salir · {len(bloq)} bloqueados (no se envían)")
    if seco:
        return {"salen": len(salen), "bloqueados": len(bloq)}

    salen = [f for f, _ in salen]
    clave = _clave()

    def uno(f):
        try:
            return f, *preguntar(f["texto"], clave), None
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            return f, None, None, f"{type(e).__name__}"

    with ThreadPoolExecutor(HILOS) as ex:
        res = list(ex.map(uno, salen))
    del clave

    errores = [e for *_, e in res if e]
    res = [(f, p, ms) for f, p, ms, e in res if e is None]
    n = len(res)
    if not n:
        raise SystemExit(f"⛔ ninguna respuesta válida ({errores[:3]})")

    pares, tiempos, fallos = [], [], []
    for f, p, ms in res:
        dice = "tarea" if p >= 0.5 else "no"
        conf = p if p >= 0.5 else 1 - p
        ok = dice == f["etiqueta"]
        pares.append((conf, ok))
        tiempos.append(ms)
        if not ok:
            fallos.append((conf, f["etiqueta"], f["texto"][:90]))
    acc = sum(ok for _, ok in pares) / n
    ece, bandas = sl._ece(pares)
    mayoria = max(sum(1 for f, *_ in res if f["etiqueta"] == e) for e in ("tarea", "no")) / n
    tiempos.sort()

    # El determinista, sobre EXACTAMENTE los mismos casos y el mismo texto redactado.
    decide = acierta = 0
    for f, *_ in res:
        v = tt.clasificar(f["texto"]).get("veredicto")
        if v != "dudosa":
            decide += 1
            acierta += v == f["etiqueta"]

    print(f"  {MODELO} · {n} casos respondidos" + (f" · {len(errores)} errores de red" if errores else ""))
    print(f"  acierto a cobertura total  {acc:.1%}   (baseline «di tarea a todo»: {mayoria:.1%})")
    print(f"  ECE                        {ece:.3f}")
    print(f"  latencia                   mediana {tiempos[n // 2]:.0f} ms · p90 {tiempos[int(n * .9)]:.0f} ms")

    print("\n  cobertura vs acierto (lo que importa en este carril):")
    print("   quién                      cubre    acierta al decidir")
    print(f"   determinista (producción)  {decide / n:>6.1%}   {acierta / decide if decide else 0:>6.1%}")
    for u in UMBRALES:
        sel = [ok for c, ok in pares if c >= u]
        if sel:
            print(f"   Jev con confianza ≥ {u:.2f}   {len(sel) / n:>6.1%}   {sum(sel) / len(sel):>6.1%}")

    print("\n  calibración, banda a banda:")
    print("   confianza      n   dice   acierta   error")
    for lo, hi, cnt, conf, real in bandas:
        print(f"   {lo:.1f}-{hi:.1f}  {cnt:>5}  {conf:.1%}   {real:>6.1%}  {abs(conf - real):>6.1%}")

    if fallos:
        print(f"\n  fallos más confiados, {len(fallos)} en total:")
        for conf, et, txt in sorted(fallos, reverse=True)[:6]:
            print(f"   {conf:.0%} seguro y era «{et}»: {txt}")
    return {"n": n, "acierto": acc, "ece": ece, "baseline": mayoria,
            "det_cobertura": decide / n, "det_acierto": acierta / decide if decide else 0}


# ── Modo --n1 (21-sep-26): ¿juzga Jev el ENCAJE con un perfil N1 mínimo? ──────────────────
# Medición, no integración. Exige que {{TITULAR}} haya confiado el destino a mano
# (`borde.py trust-cloud jev-typesafe --para n1-minimo-medicion`, confirmación tecleada) y se
# revoca al terminar. Lo que sale es SOLO este perfil + los criterios PÚBLICOS de cada ensayo.
# Fuera a propósito: diferenciación neuroendocrina, CCND1, ESR1, Ki67, SSTR, fechas, centros: son
# raros y, cruzados con su caso público, la identificarían. El test lo vigila.
DESTINO_N1 = "jev-typesafe"
PERFIL_N1 = ("Patient: woman with HR-positive metastatic breast cancer; HER2 IHC 0 on all biopsies "
             "(never HER2-low or HER2-positive); 3 prior endocrine-based lines in the metastatic "
             "setting including CDK4/6 inhibitors; never received chemotherapy; never received any "
             "antibody-drug conjugate or anti-HER2 therapy; bone and liver metastases.")
# Fecha en que se escribió y cotejó el perfil contra ESTADO-ACTUAL §1 (sesión del 21-sep-26).
# El radar (`encaje_n1`) NO usa el perfil si §1 es posterior: un cambio de tratamiento (empezar
# TB06, un ADC o quimio) lo dejaría diciendo algo falso. Al re-cotejar el texto, sube esta fecha.
PERFIL_N1_FECHA = (2026, 9, 21)
INSTR_N1 = ("Based strictly on the trial's eligibility criteria, this patient appears eligible to "
            "enroll (no inclusion criterion she clearly fails and no exclusion criterion she clearly meets).")
_VETADAS_N1 = ("neuroendocr", "ccnd1", "esr1", "ki67", "ki-67", "sstr", "{{DIANA2}}", "fgfr")
# Etiquetas puestas por la sesión del 21-sep a partir de los veredictos cerrados del radar.
# «Encaja» = el veredicto no encontró criterio que la excluya. Revisables.
POS_N1 = ("NCT06750484", "NCT06172478", "NCT07753226", "NCT07774624", "NCT07697443", "NCT07553390")
# Ambiguos o sin registro de ensayo: fuera del set para no medir contra una verdad dudosa.
SKIP_N1 = ("NCT07471776", "NCT07604571", "NCT07810088", "NCT07544654", "NCT07825753",
           "NCT06088472", "NCT05461768", "NCT05701709")


def _set_n1():
    """[(nct, titulo, encaja)] desde los leads cerrados del radar que son ensayos."""
    cola = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                        "tools", "state", "radar_ned", "cola_verificacion.json")
    vistos, fuera = set(), []
    for x in json.load(open(cola, encoding="utf-8")).get("cerrados", []):
        ref = x.get("ref") or ""
        m = re.search(r"NCT\d{8}", ref) or re.search(r"NCT\d{8}", x.get("veredicto") or "")
        if not m:
            continue
        nct = m.group(0)
        if nct in vistos or nct in SKIP_N1:
            continue
        vistos.add(nct)
        fuera.append((nct, x.get("titulo", ""), nct in POS_N1))
    return fuera


def _criterios(nct, timeout=25):
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "btp-bench"}),
                                timeout=timeout) as fh:
        d = json.loads(fh.read())["protocolSection"]
    return (d["identificationModule"].get("briefTitle", ""),
            d.get("eligibilityModule", {}).get("eligibilityCriteria", ""))


def bench_n1(limite=None):
    if any(v in PERFIL_N1.lower() for v in _VETADAS_N1):
        raise SystemExit("⛔ el perfil N1 lleva un término vetado: no sale")
    if not borde.es_trusted(DESTINO_N1):
        raise SystemExit("⛔ jev-typesafe no está confiado para N1. Es un acto de {{TITULAR}}:\n"
                         "   python3 tools/borde.py trust-cloud jev-typesafe --para n1-minimo-medicion")
    crudo, motivo = borde.identificador_directo(PERFIL_N1)
    if crudo:
        raise SystemExit(f"⛔ el perfil N1 parece identificable ({motivo}): no sale")
    casos = _set_n1()[:limite] if limite else _set_n1()
    clave = _clave()
    res = []
    for nct, titulo, encaja in casos:
        try:
            bt, crit = _criterios(nct)
        except Exception as e:
            print(f"   {nct}: criterios no abiertos ({type(e).__name__}), fuera")
            continue
        estado = f"{PERFIL_N1}\n\nTrial {nct}: {bt}\nEligibility criteria:\n{crit[:6000]}"
        if borde.hay_canario(estado):
            continue
        try:
            p, _ = preguntar(estado, clave, instr=INSTR_N1)
        except Exception as e:
            print(f"   {nct}: Jev falló ({type(e).__name__})")
            continue
        res.append((p, encaja, nct, bt))
    del clave
    pos = [p for p, e, *_ in res if e]
    neg = [p for p, e, *_ in res if not e]
    auc = (sum((a > b) + .5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))
           if pos and neg else None)
    print(f"\n  N1 · {len(res)} ensayos respondidos · {len(pos)} «encaja» · {len(neg)} «no»")
    print(f"  AUC {auc:.3f}" if auc is not None else "  AUC: sin positivos o negativos")
    print("\n  ranking (P de «elegible»):")
    for i, (p, e, nct, bt) in enumerate(sorted(res, reverse=True), 1):
        print(f"   {i:>2}. {p:.2f} {'ENCAJA' if e else '  no  '} {nct} {bt[:70]}")
    return {"n": len(res), "auc": auc}


if __name__ == "__main__":
    a = sys.argv[1:]
    lim = int(a[a.index("--limite") + 1]) if "--limite" in a else None
    if "--n1" in a:
        bench_n1(limite=lim)
    else:
        bench(limite=lim, seco="--seco" in a)
