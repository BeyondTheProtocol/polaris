#!/usr/bin/env python3
"""tools/bench_modelos.py — ¿qué modelo local, para qué tarea, en 16 GB? Contra lo determinista.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
Diseño y cifras cotejadas: `00_FUENTE-DE-VERDAD/04 · IA/Notas/p5-modelo-local-por-tarea-…`.

La pregunta no es «¿qué tal va el modelo?» sino «¿mejora lo que ya decide gratis y en 0 ms?».
Por eso, como `bench_determinista.py`, se mide COBERTURA y ACIERTO AL DECIDIR por separado: un
«no lo sé» va a una cola humana y no se pierde nada; un fallo con confianza sí se pierde.

Tareas (casos cerrados, todos locales, texto en memoria, nunca a disco ni fuera):
  triaje   ¿entra en su lista?            set dorado de `eval_triage` (texto crudo)
  dudosas  lo mismo, SOLO lo que el determinista deja en «dudosa»: el uso realista del modelo
  tipo     carpeta del historial           PDFs archivados; la verdad es la carpeta
  centro   centro emisor                   PDFs archivados; la verdad es el nombre del fichero
  router   nivel de `decide_peticion`      tests/fixtures/enrutado_eval.jsonl (sin PII)

⚠️  tipo y centro son CIRCULARES: buena parte del archivo lo colocó `historial.py` con las mismas
reglas que el candidato determinista. Su cifra es acuerdo con el archivo, no acierto real, y un
modelo que discrepa puede tener razón. El set independiente etiquetado a ciegas sigue pendiente.

Método, y por qué:
  · Letras A-H de UN token, como `score_local.puntuar`: leer la etiqueta larga («laboratorio»)
    confunde opciones que comparten primer token.
  · MLX lee el vector ENTERO de probabilidades (Ollama se queda en el top-20: PRs #18580 y
    #18591 cerrados sin fusionar, verificado 25-sep-26).
  · Umbral de abstención elegido en la mitad de CALIBRACIÓN y medido en la de TEST (partición
    estratificada, semilla fija). Elegirlo y medirlo en los mismos casos da cifras optimistas.
  · Un modelo en memoria cada vez, con el freno de memoria de `score_local` (3 GB de holgura).

Uso (MLX vive en su venv, no en el `.venv` de casa base):
  ~/.venvs/mlx-bench/bin/python tools/bench_modelos.py --modelo mlx:mlx-community/Qwen3.5-9B-4bit
  python3 tools/bench_modelos.py --modelo det                      # solo la línea base
  python3 tools/bench_modelos.py --modelo ollama:qwen3:8b --tareas triaje,dudosas
  … --esperar 60  (espera hasta 60 min a que haya memoria)   … --limite 10   (prueba corta)   … --fallos   (lista en consola cada fallo, para revisarlo)   … --forzar   (saltarse el freno de memoria, bajo tu riesgo)
"""
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import modelo_mlx  # noqa: E402
REPO_TOOLS = os.path.dirname(AQUI)
ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
SALIDA = os.path.join(ROOT, "tools", "state", "eval")
SEMILLA = 25092026
OBJETIVO = 0.97          # acierto mínimo al decidir para fijar el umbral en calibración
CONFIADO = 0.90          # un fallo por encima de esto es un «fallo confiado»
MAX_CHARS = 2000         # la cabecera clasifica (ver historial.clasificar); el resto es latencia
LETRAS = "ABCDEFGHIJKLMNOPQRST"
VER_FALLOS = False


# ─────────────────────────── casos cerrados ───────────────────────────

def casos_triaje():
    import score_local as sl
    return [{"texto": f["texto"], "gold": f["etiqueta"]} for f in sl._casos_crudos("es_tarea")]


def casos_dudosas():
    import triage_tareas as tt
    return [c for c in casos_triaje() if tt.clasificar(c["texto"]).get("veredicto") == "dudosa"]


def _pdfs_historial():
    import historial as h
    inv = {v: k for k, v in h.CARPETAS}
    for carpeta in sorted(os.listdir(h.RAIZ)):
        k = inv.get(carpeta)
        if k in (None, "indice", "paquetes"):
            continue
        for f in sorted(os.listdir(os.path.join(h.RAIZ, carpeta))):
            if not f.lower().endswith(".pdf"):
                continue
            p = os.path.join(h.RAIZ, carpeta, f)
            txt = subprocess.run(["pdftotext", "-l", "3", p, "-"], capture_output=True,
                                 text=True).stdout
            side = p[:-4] + ".txt"
            if len(txt.strip()) < 50 and os.path.exists(side):
                txt = open(side, errors="ignore").read()
            if len(txt.strip()) < 50:
                continue            # sin capa de texto: eso es OCR/visión, otra tarea
            partes = f[:-4].split(" - ")
            yield {"texto": txt, "carpeta": k, "centro": partes[1] if len(partes) >= 3 else None}


def casos_tipo():
    return [{"texto": d["texto"], "gold": d["carpeta"]} for d in _pdfs_historial()]


def casos_centro():
    return [{"texto": d["texto"], "gold": d["centro"]} for d in _pdfs_historial()
            if d["centro"] and d["centro"] != "sin-centro"]


def casos_router():
    ruta = os.path.join(REPO_TOOLS, "tests", "fixtures", "enrutado_eval.jsonl")
    fuera = []
    for ln in open(ruta, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            c = json.loads(ln)
            fuera.append({"texto": c["prompt"], "gold": c["nivel"]})   # admite «a|b»
    return fuera


# ─────────────────────────── preguntas (letras) ───────────────────────────

def _pregunta(tarea, etiquetas_gold):
    """(instrucción, {letra: (etiqueta, descripción)})."""
    if tarea in ("triaje", "dudosas"):
        return ("Eres el filtro de la lista de tareas de una persona. Decide si esto debe entrar "
                "en su lista de pendientes porque ELLA tiene que hacer algo. La publicidad y las "
                "newsletters nunca entran, aunque te pidan que hagas clic.",
                {"A": ("tarea", "sí, entra en su lista de tareas"),
                 "B": ("no", "no: publicidad, newsletter, acuse de recibo o información sin acción")})
    if tarea == "tipo":
        import historial as h
        desc = {"molecular": "informe molecular o genómico (NGS, biopsia líquida, panel de genes)",
                "patologia": "anatomía patológica o biopsia",
                "imagen": "prueba de imagen (TAC, PET, RM, ecografía, gammagrafía, radiografía)",
                "laboratorio": "análisis de laboratorio (sangre, orina, marcadores)",
                "consulta": "consulta médica o evolutivo",
                "ingreso": "hospitalización, alta o urgencias",
                "soporte": "enfermería, nutrición o soporte",
                "tramite": "petición, consentimiento o trámite administrativo"}
        claves = [k for k, _ in h.CARPETAS if k in desc]
        return ("Clasifica este documento médico por su tipo. Fíjate en el membrete y el servicio "
                "que lo emite, no en lo que cita por dentro.",
                {LETRAS[i]: (k, desc[k]) for i, k in enumerate(claves)})
    if tarea == "centro":
        import historial as h
        claves = sorted(set(etiquetas_gold))
        return ("¿Qué centro EMITE este documento médico? Si no nombra hospital y solo consta el "
                "Servicio Murciano de Salud, elige SMS.",
                {LETRAS[i]: (k, h.CENTROS.get(k, k)) for i, k in enumerate(claves)})
    if tarea == "router":
        return ("Eres la centralita de un sistema de asistentes. Decide quién responde a este "
                "mensaje de la usuaria.",
                {"A": ("directo", "la sesión responde sola: charla, seguimiento, órdenes cortas"),
                 "B": ("llm", "un buscador o LLM externo: buscar en vivo, redes, citas"),
                 "C": ("comite", "el comité experto del dominio"),
                 "D": ("panel", "varias cabezas: decisión importante o estratégica")})
    raise ValueError(tarea)


def _prompt(instr, opciones, texto):
    ops = "\n".join(f"{k} = {d}" for k, (_, d) in opciones.items())
    return (f"{instr}\n\nOpciones:\n{ops}\n\nTexto:\n{texto[:MAX_CHARS]}\n\n"
            f"Responde SOLO con la letra.")


# ─────────────────────────── candidatos ───────────────────────────

class Determinista:
    nombre = "det"

    def decidir(self, tarea, texto, opciones):
        t0 = time.perf_counter()
        if tarea in ("triaje", "dudosas"):
            import triage_tareas as tt
            v = tt.clasificar(texto).get("veredicto")
            et = None if v == "dudosa" else v
        elif tarea == "tipo":
            import historial as h
            et = h.clasificar(texto)
        elif tarea == "centro":
            import historial as h
            c = h.detecta_centro(texto)
            et = None if not c or c == "?" else c
        else:
            import decide_peticion as dp
            import enruta
            est = {n: {"ok": True, "detalle": "eval"} for n in enruta.PROVEEDORES}
            et = dp.decidir(texto, estado=est)["nivel"]
        ms = (time.perf_counter() - t0) * 1000
        return et, (1.0 if et is not None else 0.0), ms   # sin probabilidad: 1 o abstención


class MLX(modelo_mlx.MLX):
    def decidir(self, tarea, texto, opciones):
        return self.puntuar(_prompt(*_INSTR_OPC[tarea], texto), opciones)


class Ollama:
    def __init__(self, modelo):
        self.nombre = "ollama:" + modelo
        self.modelo = modelo

    def decidir(self, tarea, texto, opciones):
        import score_local as sl
        instr, _ = _INSTR_OPC[tarea]
        ops = {k: d for k, (_, d) in opciones.items()}
        el, probs, ms = sl.puntuar(texto[:MAX_CHARS], instr, ops, modelo=self.modelo)
        if el is None:
            return None, 0.0, ms
        return opciones[el][0], probs[el], ms


_INSTR_OPC = {}


# ─────────────────────────── métrica ───────────────────────────

def _ok(pred, gold):
    return pred is not None and pred in str(gold).split("|")


def _partir(casos):
    """50/50 estratificado por etiqueta, semilla fija."""
    rnd = random.Random(SEMILLA)
    por = defaultdict(list)
    for i, c in enumerate(casos):
        por[str(c["gold"])].append(i)
    cal, test = [], []
    for _g, idx in sorted(por.items()):
        rnd.shuffle(idx)
        m = len(idx) // 2
        cal += idx[:m]
        test += idx[m:]
    return sorted(cal), sorted(test)


def _umbral(res):
    """Menor confianza con la que, en calibración, el acierto al decidir llega a OBJETIVO."""
    decididos = sorted([r for r in res if r["pred"] is not None], key=lambda r: -r["conf"])
    mejor, ok = None, 0
    for n, r in enumerate(decididos, 1):
        ok += r["ok"]
        if ok / n >= OBJETIVO:
            mejor = r["conf"]
    return mejor


UMBRAL_NO = 0.95   # tirar un correo es el fallo caro: el modelo necesita más seguridad para «no»


def _cascada(casos, res, idx, umbral_tarea, umbral_no=None):
    """El uso realista: decide el determinista; el modelo solo entra donde él se abstiene, y con
    umbral ASIMÉTRICO. Motivo (25-sep-26): el único fallo confiado de Qwen3.5-9B por encima de
    0,84 fue tirar como «no» un correo de un centro oncológico (91 %)."""
    umbral_no = umbral_no or max(umbral_tarea, UMBRAL_NO)
    det = Determinista()
    dec = ok = fc = fm = 0
    for i in idx:
        v = det.decidir("triaje", casos[i]["texto"], None)[0]
        if v is None:
            r = res[i]
            lim = umbral_no if r["pred"] == "no" else umbral_tarea
            if r["pred"] is None or r["conf"] < lim:
                continue
            v, conf = r["pred"], r["conf"]
            del_modelo = True
        else:
            conf, del_modelo = 1.0, False
        dec += 1
        bien = _ok(v, casos[i]["gold"])
        ok += bien
        fc += (not bien and conf >= CONFIADO)
        fm += (not bien and del_modelo)
    n = len(idx) or 1
    return {"n": len(idx), "cobertura": round(dec / n, 3),
            "acierto_al_decidir": round(ok / dec, 3) if dec else None, "fallos_confiados": fc, "fallos_del_modelo": fm}


def _resumen(res, umbral=None):
    n = len(res) or 1
    if umbral is None:
        dec = [r for r in res if r["pred"] is not None]
    else:
        dec = [r for r in res if r["pred"] is not None and r["conf"] >= umbral]
    ok = sum(r["ok"] for r in dec)
    return {"n": len(res), "cobertura": len(dec) / n,
            "acierto_al_decidir": ok / len(dec) if dec else None,
            "acierto_total": sum(r["ok"] for r in res) / n,
            "fallos_confiados": sum(1 for r in dec if not r["ok"] and r["conf"] >= CONFIADO)}


def _ece(res, bins=10):
    pares = [(r["conf"], r["ok"]) for r in res if r["pred"] is not None]
    cubos = defaultdict(list)
    for c, o in pares:
        cubos[min(int(c * bins), bins - 1)].append((c, o))
    n = len(pares) or 1
    return sum(len(g) / n * abs(sum(c for c, _ in g) / len(g) - sum(o for _, o in g) / len(g))
               for g in cubos.values())


_freno, _vigia = modelo_mlx.freno, modelo_mlx.vigia


CASOS = {"triaje": casos_triaje, "dudosas": casos_dudosas, "tipo": casos_tipo,
         "centro": casos_centro, "router": casos_router}


def correr(cand, tareas, limite=None, forzar=False, esperar=0):
    _freno(cand, forzar, esperar)
    if isinstance(cand, MLX):
        cand.cargar()
        print(f"  cargado {cand.repo} en {cand.carga_s:.1f} s")
    filas, memo = {}, {}      # memo vive en memoria y muere con el proceso: nada a disco
    if "dudosas" in tareas and "triaje" in tareas:
        tareas = ["triaje", "dudosas"] + [t for t in tareas if t not in ("triaje", "dudosas")]
    for tarea in tareas:
        casos = CASOS[tarea]()
        if limite:
            casos = casos[:limite]
        if not casos:
            print(f"  {tarea}: sin casos")
            continue
        _INSTR_OPC[tarea] = _pregunta(tarea, [c["gold"] for c in casos])
        opciones = _INSTR_OPC[tarea][1]
        res = []
        familia = "triaje" if tarea == "dudosas" else tarea   # misma pregunta, mismos textos
        for i, c in enumerate(casos, 1):
            clave = (familia, c["texto"])
            if clave not in memo:
                memo[clave] = cand.decidir(tarea, c["texto"], opciones)
            pred, conf, ms = memo[clave]
            res.append({"pred": pred, "conf": conf, "ms": ms, "ok": _ok(pred, c["gold"])})
            if i % 5 == 0:
                _vigia(cand)
            if i % 25 == 0:
                print(f"   … {tarea} {i}/{len(casos)}", file=sys.stderr, flush=True)
        cal, test = _partir(casos)
        rc, rt = [res[i] for i in cal], [res[i] for i in test]
        u = None if isinstance(cand, Determinista) else _umbral(rc)
        tiempos = sorted(r["ms"] for r in res)
        fila = {"test_sin_umbral": _resumen(rt), "test_con_umbral": _resumen(rt, u) if u else None,
                "umbral": u, "ece": None if isinstance(cand, Determinista) else _ece(rt),
                "lat_mediana_ms": tiempos[len(tiempos) // 2],
                "lat_p90_ms": tiempos[int(len(tiempos) * .9)],
                "confusiones": Counter(f"{casos[i]['gold']}→{res[i]['pred']}" for i in range(len(res))
                                       if not res[i]["ok"] and res[i]["pred"] is not None).most_common(5)}
        filas[tarea] = fila
        _imprimir(tarea, fila)
        if tarea == "triaje" and u and not isinstance(cand, Determinista):
            fila["cascada"] = _cascada(casos, res, test, u)
            fila["cascada_todos"] = _cascada(casos, res, range(len(casos)), u)
            print("   cascada test (det primero; modelo solo en «dudosa»; «no» exige ≥ %.2f): %s"
                  % (UMBRAL_NO, fila["cascada"]))
            print("   cascada, los %d casos: %s" % (len(casos), fila["cascada_todos"]))
        if VER_FALLOS:
            # Solo consola, en local: es para que un humano (o la sesión) mire QUÉ falla.
            print("   fallos (todos los casos, más confiados primero; «det» = lo que dijo el determinista):")
            det = Determinista()
            for i in sorted(range(len(res)), key=lambda i: -res[i]["conf"]):
                r = res[i]
                if r["ok"] or r["pred"] is None:
                    continue
                d = det.decidir(tarea, casos[i]["texto"], opciones)[0]
                t = " ".join(casos[i]["texto"].split())[:160]
                print(f"    {r['conf']:.0%} dijo {r['pred']} · era {casos[i]['gold']} · det {d} · {t}")
    out = {"candidato": cand.nombre, "fecha": time.strftime("%Y-%m-%d %H:%M"), "tareas": filas}
    if isinstance(cand, MLX):
        out["memoria_pico_gb"] = cand.memoria_pico_gb()
        out["carga_s"] = cand.carga_s
        print(f"\n  memoria pico MLX {out['memoria_pico_gb']:.2f} GB")
    os.makedirs(SALIDA, exist_ok=True)
    ruta = os.path.join(SALIDA, "bench_modelos_%s_%s.json" % (
        time.strftime("%Y%m%d-%H%M"), re.sub(r"[^\w.-]", "_", cand.nombre)))
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)   # solo agregados: ningún texto
    print(f"  → {ruta}")
    return out


def _pct(x):
    return "—" if x is None else f"{x:.1%}"


def _imprimir(tarea, f):
    s = f["test_sin_umbral"]
    print(f"\n  {tarea} · test n={s['n']}", flush=True)
    print(f"   sin umbral   cobertura {_pct(s['cobertura'])} · acierto al decidir "
          f"{_pct(s['acierto_al_decidir'])} · fallos confiados {s['fallos_confiados']}")
    if f["test_con_umbral"]:
        c = f["test_con_umbral"]
        print(f"   umbral {f['umbral']:.2f}  cobertura {_pct(c['cobertura'])} · acierto al decidir "
              f"{_pct(c['acierto_al_decidir'])} · fallos confiados {c['fallos_confiados']}")
    if f["ece"] is not None:
        print(f"   ECE {f['ece']:.3f}")
    print(f"   latencia mediana {f['lat_mediana_ms']:.1f} ms · p90 {f['lat_p90_ms']:.1f} ms")
    if f["confusiones"]:
        print(f"   confusiones: {f['confusiones']}")
    sys.stdout.flush()


def candidato(spec):
    if spec == "det":
        return Determinista()
    if spec.startswith("mlx:"):
        return MLX(spec[4:])
    if spec.startswith("ollama:"):
        return Ollama(spec[7:])
    raise SystemExit(f"⛔ candidato desconocido {spec!r}: det | mlx:<repo> | ollama:<modelo>")


if __name__ == "__main__":
    a = sys.argv[1:]

    def _opt(flag, defecto=None):
        if flag in a:
            i = a.index(flag)
            v = a[i + 1]
            del a[i:i + 2]
            return v
        return defecto
    spec = _opt("--modelo", "det")
    tareas = _opt("--tareas", "triaje,dudosas,tipo,centro,router").split(",")
    lim = _opt("--limite")
    esp = int(_opt("--esperar", "0"))
    forzar = "--forzar" in a
    VER_FALLOS = "--fallos" in a
    correr(candidato(spec), tareas, limite=int(lim) if lim else None, forzar=forzar, esperar=esp)
