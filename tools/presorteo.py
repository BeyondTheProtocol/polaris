#!/usr/bin/env python3
"""tools/presorteo.py — anota y ORDENA la bandeja de WhatsApp antes de que la juzgue Vega. No decide.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.
Plan aprobado por {{TITULAR}} el 25-sep-26 (P5, fase A). Medición en la nota P5 de `04 · IA/Notas`.

Qué hace: para cada candidato en «dudosa» de `state/tareas/wa_candidatos.json`, pregunta a un
modelo local (Qwen3.5-9B en MLX, egress cero) si entra en su lista y lo pone en una FRANJA:

  propuesta_tarea   dice «tarea» con ≥ 0,84
  probable_ruido    dice «no» con ≥ 0,95   (asimétrico: tirar es el fallo caro; el único fallo
                                            confiado del bench fue un correo oncológico al 91 %)
  gris              lo demás

Lo que NO hace, a propósito (el patrón de Jev que {{CONTACTO}} señaló: ordena, nunca archiva):
  · no crea, no descarta, no toca `wa_candidatos.json`: escribe su anotación APARTE, en
    `state/tareas/presorteo.json`, con la huella del título y sin ningún texto.
  · si algo falla (HALT, candado ocupado, Ollama con un modelo cargado, memoria, el vigía), no
    escribe nada: Vega trabaja exactamente como antes. Fail-closed.

Uso (en el venv de MLX):
  ~/.venvs/mlx-bench/bin/python tools/presorteo.py --seco     # cuenta qué haría, sin modelo
  ~/.venvs/mlx-bench/bin/python tools/presorteo.py            # anota
  ~/.venvs/mlx-bench/bin/python tools/presorteo.py --muestra 20   # tabla para revisión humana
  … --swap-max 3   (afloja el vigía; solo con OK de {{TITULAR}} para esa pasada)
"""
import datetime
import hashlib
import json
import os
import random
import re
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import borde  # noqa: E402
import _lock  # noqa: E402
import modelo_mlx  # noqa: E402

STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(
    os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"), "tools", "state")
BANDEJA = os.path.join(STATE, "tareas", "wa_candidatos.json")
SALIDA = os.path.join(STATE, "tareas", "presorteo.json")
CANDADO = "modelo-local"
UMBRAL_TAREA = 0.84   # fijado en calibración (bench_modelos, 25-sep-26)
UMBRAL_NO = 0.95

INSTR = ("Eres el filtro de la lista de tareas de una persona. Decide si esto debe entrar en su "
         "lista de pendientes porque ELLA tiene que hacer algo. La publicidad y las newsletters "
         "nunca entran, aunque te pidan que hagas clic.")
OPCIONES = {"A": ("tarea", "sí, entra en su lista de tareas"),
            "B": ("no", "no: charla, acuse de recibo o información sin acción")}


def huella(texto):
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()[:16]


# 25-sep-26, muestra A3: 3 de 20 «probable ruido» eran clínicos (pólipos, «escrito por un
# médico», «llevo un power port»). Lo clínico no se manda al fondo de la cola aunque el modelo
# esté seguro: va a gris y lo mira Vega. `borde` caza parte; lo demás, esta lista.
_RE_CLINICO_EXTRA = re.compile(
    r"\bport\b|reservorio|cat[eé]ter|contraste|anal[ií]tic|p[oó]lip|informe|resonancia|\brm\b|"
    r"\btac\b|\bpet\b|esc[aá]ner|scanner|ecograf|mamograf|sangre|pastill|dosis|pinchaz|"
    r"inyecci|enfermer|urgenci|ingres|cita\b|consulta|receta|s[ií]ntoma|dolor|fiebre|marcador|"
    # 2ª pasada sobre las 73 del 25-sep: operación, neumo, mutaciones, recuperación, costillas…
    r"operaci|cirug|neumo|mutaci|gen[eé]tic|hered|recuperad|costill|respir|funcional|"
    # …y su equipo clínico: un contacto de su oncología nunca es «ruido» (regla del muro).
    r"contacto|contacto", re.I)


def es_clinico(texto):
    t = texto or ""
    return bool(borde._RE_MEDICA.search(t) or borde._RE_CLINICO_NUEVO.search(t)
                or _RE_CLINICO_EXTRA.search(t))


def franja(pred, conf, texto=""):
    if pred == "tarea" and conf >= UMBRAL_TAREA:
        return "propuesta_tarea"
    if pred == "no" and conf >= UMBRAL_NO and not es_clinico(texto):
        return "probable_ruido"
    return "gris"


def dudosos():
    try:
        with open(BANDEJA, encoding="utf-8") as fh:
            cands = json.load(fh).get("candidatos", [])
    except (OSError, ValueError):
        return []
    return [c for c in cands if c.get("veredicto") == "dudosa" and (c.get("titulo") or "").strip()]


def _prompt(texto):
    ops = "\n".join(f"{k} = {d}" for k, (_, d) in OPCIONES.items())
    return f"{INSTR}\n\nOpciones:\n{ops}\n\nTexto:\n{texto}\n\nResponde SOLO con la letra."


def _ollama_cargado():
    """True si Ollama tiene un modelo en memoria: dos modelos a la vez es lo que tumba el mini."""
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return len([ln for ln in out.splitlines() if ln.strip()]) > 1


def _guardar(anot):
    tmp = SALIDA + ".tmp"
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(anot, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, SALIDA)


def anotar(modelo=None, seco=False, esperar=0, swap_max_gb=1.5):
    """Devuelve el dict escrito, o None si no escribió nada (y por qué, impreso)."""
    if borde.halted():
        print("⏸  HALT activo: no se anota nada")
        return None
    casos = dudosos()
    print(f"  {len(casos)} candidatos en «dudosa» en la bandeja")
    if seco or not casos:
        return None
    if _lock.held(CANDADO):
        print(f"⛔ el candado «{CANDADO}» está tomado: otro proceso usa el modelo local")
        return None
    import time
    limite = time.time() + esperar * 60
    while _ollama_cargado() and time.time() < limite:   # Ollama suelta el modelo a los ~5 min
        print("   … Ollama tiene un modelo cargado; reintento en 60 s", flush=True)
        time.sleep(60)
    if _ollama_cargado():
        print("⛔ Ollama tiene un modelo cargado: no cargo otro encima")
        return None
    with _lock.lock(CANDADO, timeout=1800):
        cand = modelo or modelo_mlx.MLX(modelo_mlx.REPO_DEFECTO)
        modelo_mlx.freno(cand, esperar=esperar)
        if modelo is None:
            cand.cargar()
        filas = {}
        for i, c in enumerate(casos, 1):
            pred, conf, _ms = cand.puntuar(_prompt(c["titulo"]), OPCIONES)
            filas[huella(c["titulo"])] = {"pred": pred, "conf": round(conf, 3),
                                          "franja": franja(pred, conf, c["titulo"])}
            if i % 5 == 0:
                modelo_mlx.vigia(cand, swap_max_gb=swap_max_gb)
    anot = {"fecha": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "modelo": getattr(cand, "repo", "?"), "umbral_tarea": UMBRAL_TAREA,
            "umbral_no": UMBRAL_NO, "candidatos": filas}
    _guardar(anot)
    cuenta = {}
    for f in filas.values():
        cuenta[f["franja"]] = cuenta.get(f["franja"], 0) + 1
    print(f"  anotados {len(filas)} · {cuenta} → {SALIDA}")
    return anot


def refranjar():
    """Recalcula las franjas de un presorteo ya hecho (cambió la regla, no el modelo). Sin modelo."""
    anot = json.load(open(SALIDA, encoding="utf-8"))
    por_huella = {huella(c["titulo"]): c["titulo"] for c in dudosos()}
    cuenta = {}
    for h, f in anot["candidatos"].items():
        f["franja"] = franja(f["pred"], f["conf"], por_huella.get(h, ""))
        cuenta[f["franja"]] = cuenta.get(f["franja"], 0) + 1
    _guardar(anot)
    print(f"  refranjados {len(anot['candidatos'])} · {cuenta}")


def muestra(n=20, semilla=25092026):
    """Tabla para que un humano marque: n de cada franja firme. Solo consola."""
    try:
        anot = json.load(open(SALIDA, encoding="utf-8"))["candidatos"]
    except (OSError, ValueError, KeyError):
        raise SystemExit("⛔ no hay presorteo.json: corre primero sin --muestra")
    rnd = random.Random(semilla)
    for fr in ("propuesta_tarea", "probable_ruido"):
        fila = [(c, anot[huella(c["titulo"])]) for c in dudosos()
                if anot.get(huella(c["titulo"]), {}).get("franja") == fr]
        rnd.shuffle(fila)
        print(f"\n### {fr} ({len(fila)} en total, muestra {min(n, len(fila))})")
        print("| # | conf | título | ¿es tarea? |\n|---|---|---|---|")
        for k, (c, a) in enumerate(fila[:n], 1):
            print(f"| {k} | {a['conf']:.2f} | {' '.join(c['titulo'].split())[:110]} | |")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--refranjar" in a:
        refranjar()
    elif "--muestra" in a:
        i = a.index("--muestra")
        muestra(int(a[i + 1]) if len(a) > i + 1 else 20)
    else:
        esp = int(a[a.index("--esperar") + 1]) if "--esperar" in a else 0
        # --swap-max: aflojar el vigía es decisión de {{TITULAR}}, pasada a pasada (25-sep-26: OK a 3 GB
        # para una pasada de ~5 min). Por defecto sigue en 1,5.
        sw = float(a[a.index("--swap-max") + 1]) if "--swap-max" in a else 1.5
        anotar(seco="--seco" in a, esperar=esp, swap_max_gb=sw)
