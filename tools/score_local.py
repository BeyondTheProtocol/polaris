#!/usr/bin/env python3
"""tools/score_local.py — decisiones tipadas en local, con probabilidad. Egress cero.

El mecanismo de Jev (TypeSafe AI) sin Jev: en vez de generar texto, se pide UN token y se leen
los logprobs de los candidatos que nos interesan. Una pasada, sin decode loop. Corre sobre el
Ollama que ya está instalado (backend de `tools/local.py`), así que no instala nada nuevo ni
manda nada fuera — por eso puede ver dato crudo, cosa que Jev no puede.

Por qué existe (20-sep-26): {{TITULAR}} pidió asegurarse de que Jev no nos da mejoras. Sin medir,
eso no se puede responder. Esto es el medidor.

DOS TRAMPAS, las dos pisadas y las dos caras:
  1. `think: false` obligatorio, como en `local.py`: qwen3 razona en voz alta y arruina el
     primer token.
  2. **Las variantes del mismo label hay que SUMARLAS.** El tokenizador devuelve 'A', ' A' y 'a'
     por separado; quedarse con la última en vez de sumar daba 0/3 aciertos con 100% de
     confianza, y parecía culpa del modelo. No lo era. Si algún día esto vuelve a dar resultados
     absurdos, sospecha del lector de logprobs antes que del modelo.

Qué mide `bench`, y por qué esas dos cosas y no una:
  · ACIERTO — contra el baseline de la clase mayoritaria. Un set desequilibrado hace que
    "di siempre tarea" parezca bueno; si no se bate ese número, no hay nada.
  · CALIBRACIÓN (ECE) — si dice 90%, ¿acierta el 90% de las veces? Es EL eje donde Jev dice ser
    superior (lo entrena con RLCD). Un softmax restringido no está calibrado por construcción.
    El ECE de aquí es el listón concreto que Jev tendría que batir para valer la pena.

Uso:
  python3 tools/score_local.py preguntar "¿es una tarea accionable?" "texto a juzgar"
  python3 tools/score_local.py bench                 # contra el set dorado del triage
  python3 tools/score_local.py bench --modelo llama3.2:3b
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
GOLDEN = os.path.join(ROOT, "tools", "state", "eval", "triage_golden.jsonl")
URL = "http://localhost:11434/api/generate"
MODELO = "qwen3:8b"


# ── Freno de memoria ──────────────────────────────────────────────────────────────────────
# El 20-sep-26 esta tool tumbó el Mac mini: 298 inferencias seguidas con qwen3:8b (5,3 GB en
# GPU) sobre 16 GB compartidos con el sistema 24/7. macOS disparó Jetsam (el matador por
# presión de memoria) y la máquina se reinició, matando de paso el propio benchmark. La lección
# no vale como nota: va como freno, porque el que corre esto la próxima vez seré yo y no me
# acordaré. Casa base sostiene daemons, correo y colas — tumbarla no es un contratiempo, es
# dejar a {{TITULAR}} sin sistema.
HOLGURA_GB = 3.0  # lo que hay que dejarle al sistema DESPUÉS de cargar el modelo


def _memoria_libre_gb():
    """GB realmente disponibles. Delega en `healthcheck._recursos_ahora()`, que es el ÚNICO
    medidor de memoria del sistema.

    Antes esto miraba `vm_stat` (libre + inactiva) por su cuenta y era peor de dos maneras:
    no veía el swap, y sobre todo no veía el footprint real de los procesos. El 20-sep-26
    diagnostiqué con `ps` que el visor 3D consumía 1,28 GB cuando pedía unos 16: un proceso que
    se ha ido al swap tiene un RSS ridículo, así que `ps` mira justo donde el problema no está.
    Eso ya lo resolvió la sesión del visor con `top -stats mem` y `vm.swapusage`; duplicar su
    criterio, y encima peor, era pedir que los dos frenos dijeran cosas distintas.
    """
    try:
        import healthcheck as hc
        datos = hc._recursos_ahora()
    except Exception:
        return None
    if not datos:
        return None
    _swap, libre_pct, _gordos = datos
    if libre_pct != libre_pct:  # NaN: memory_pressure no respondió
        return None
    total = _ram_fisica_gb()
    return total * libre_pct / 100.0 if total else None


def _ram_fisica_gb():
    try:
        import healthcheck as hc
        return hc._ram_fisica_gb()
    except Exception:
        return None


def _tamano_modelo_gb(modelo):
    """GB que ocupa el modelo según `ollama list`. None si no aparece."""
    import subprocess
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for linea in out.splitlines():
        campos = linea.split()
        if campos and campos[0] == modelo:
            for i, c in enumerate(campos):
                if c.upper() in ("GB", "MB"):
                    v = float(campos[i - 1])
                    return v if c.upper() == "GB" else v / 1024
    return None


def esperar_memoria(modelo, minutos, cada=120):
    """Espera hasta `minutos` a que haya sitio para el modelo. Devuelve True si lo hay.

    CON TOPE, siempre. Un `until` sin límite es el fallo del 13-sep-26: un subagente se quedó
    14 h 34 min esperando una marca que nunca llegó, bloqueó el worktree y acabó lanzando un
    aviso obsoleto. Aquí, si se agota el plazo, se dice y se sale: no haber podido medir es un
    resultado, quedarse colgado no.
    """
    tam = _tamano_modelo_gb(modelo)
    if tam is None:
        return False
    limite = time.time() + minutos * 60
    intento = 0
    while True:
        libre = _memoria_libre_gb()
        if libre is not None and libre - tam >= HOLGURA_GB:
            print(f"  ✓ hay sitio ({libre:.1f} GB disponibles) tras {intento} comprobación(es)")
            return True
        restante = limite - time.time()
        if restante <= 0:
            print(f"⛔ se agotó la espera de {minutos} min: siguen faltando "
                  f"{tam + HOLGURA_GB - (libre or 0):.1f} GB. No se midió nada.")
            return False
        intento += 1
        print(f"   … {libre:.1f} GB libres, hacen falta {tam + HOLGURA_GB:.1f}; "
              f"reintento en {cada}s (quedan {restante/60:.0f} min)", flush=True)
        time.sleep(min(cada, restante))


def comprobar_memoria(modelo, forzar=False):
    """Aborta si cargar el modelo dejaría la máquina sin margen. Fail-closed: si no puedo
    medir, no sigo, porque la alternativa es un reinicio."""
    libre = _memoria_libre_gb()
    tam = _tamano_modelo_gb(modelo)
    if libre is None or tam is None:
        aviso = f"no puedo medir la memoria (libre={libre}, modelo={tam})"
        if not forzar:
            raise SystemExit(f"⛔ {aviso}. Con --forzar sigue bajo tu responsabilidad.")
        print(f"  ⚠️  {aviso}, sigo porque me lo has pedido con --forzar")
        return
    margen = libre - tam
    print(f"  memoria: {libre:.1f} GB disponibles · {modelo} ocupa {tam:.1f} GB · "
          f"margen {margen:.1f} GB (mínimo {HOLGURA_GB} GB)")
    if margen < HOLGURA_GB and not forzar:
        menor = "llama3.2:3b" if modelo != "llama3.2:3b" else None
        raise SystemExit(
            f"⛔ NO se lanza: dejaría {margen:.1f} GB al sistema y hacen falta {HOLGURA_GB}.\n"
            f"   El 20-sep-26 esto tumbó el mini (Jetsam) y se llevó por delante el benchmark.\n"
            f"   Opciones: cerrar apps y reintentar"
            + (f", usar --modelo {menor}" if menor else "")
            + ", o --forzar si de verdad quieres arriesgarte.")
    if margen < HOLGURA_GB:
        print(f"  ⚠️  margen por debajo del mínimo y --forzar puesto: puede reiniciarse la máquina")


def puntuar(estado, instruccion, opciones, modelo=MODELO, timeout=120):
    """(elegida, {label: prob}, ms). `opciones` es {LETRA: descripción}.

    Devuelve SIEMPRE una de las opciones dadas: es la misma garantía de tipo que vende Jev, y
    sale gratis por construcción — solo se miran los logprobs de esas letras. Ojo: garantiza el
    TIPO, no el acierto.
    """
    ops = "  ".join(f"{k}={v}" for k, v in opciones.items())
    prompt = (f"{instruccion}\nOpciones: {ops}.\n"
              f"Texto: {estado}\nResponde SOLO con la letra.\nRespuesta:")
    cuerpo = json.dumps({
        "model": modelo, "prompt": prompt, "stream": False, "think": False,
        "options": {"temperature": 0, "num_predict": 1},
        "logprobs": True, "top_logprobs": 20,
    }).encode()
    req = urllib.request.Request(URL, data=cuerpo, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        d = json.loads(fh.read())
    ms = (time.time() - t0) * 1000

    acc = defaultdict(float)
    for t in d.get("logprobs", [{}])[0].get("top_logprobs", []):
        lab = t["token"].strip().upper()
        if lab in opciones:
            acc[lab] += math.exp(t["logprob"])  # SUMAR variantes, no pisarlas (ver cabecera)
    if not acc:
        # Ninguna opción entró en el top-k: fail-closed, no se inventa una respuesta.
        return None, {}, ms
    s = sum(acc.values())
    probs = {k: v / s for k, v in sorted(acc.items(), key=lambda x: -x[1])}
    return next(iter(probs)), probs, ms


def _ece(pares, bins=10):
    """Expected Calibration Error + las bandas, sobre (confianza, acertó).

    Reparte las predicciones por su confianza y compara, banda a banda, la confianza media con
    el acierto real. Si dice 0.9 y acierta 0.6, esa banda aporta 0.3 ponderado por su tamaño.
    """
    cubos = defaultdict(list)
    for conf, ok in pares:
        i = min(int(conf * bins), bins - 1)
        cubos[i].append((conf, ok))
    n = len(pares) or 1
    ece, bandas = 0.0, []
    for i in sorted(cubos):
        grupo = cubos[i]
        conf_media = sum(c for c, _ in grupo) / len(grupo)
        acierto = sum(1 for _, ok in grupo if ok) / len(grupo)
        ece += len(grupo) / n * abs(conf_media - acierto)
        bandas.append((i / bins, (i + 1) / bins, len(grupo), conf_media, acierto))
    return ece, bandas


def _casos_crudos(pregunta):
    """El MISMO set pero con el texto SIN redactar, en memoria y sin escribir nada a disco.

    Por qué hace falta: medir el modelo local contra el corpus de-identificado es hacer trampa
    en su contra. La redacción existe para poder enseñarle el texto a un TERCERO; local corre en
    esta máquina con egress cero, así que su condición real de trabajo es ver el texto entero.
    Comparar Jev-redactado contra local-redactado castiga a local por una restricción que no le
    aplica. Esto no se persiste nunca: se calcula al vuelo y muere con el proceso.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import eval_triage as ev
    fuera = []
    for texto, etiqueta, _fuente in ev._casos():
        if etiqueta not in ("tarea", "no") or not (texto or "").strip():
            continue
        if ev._tipo_pregunta(texto) != pregunta:
            continue
        fuera.append({"texto": texto.strip(), "etiqueta": etiqueta, "pregunta": pregunta})
    return fuera


def bench(modelo=MODELO, pregunta="es_tarea", limite=None, crudo=False, forzar=False, esperar=0):
    if crudo:
        filas = _casos_crudos(pregunta)
        print("  ⚠️  modo CRUDO: texto sin redactar, en memoria. NADA de esto sale de la máquina.")
    else:
        if not os.path.exists(GOLDEN):
            raise SystemExit(f"⛔ falta el set dorado: {GOLDEN}\n   correlo: python3 tools/eval_triage.py extraer")
        filas = [json.loads(l) for l in open(GOLDEN, encoding="utf-8") if l.strip()]
    filas = [f for f in filas if f.get("pregunta") == pregunta and f["etiqueta"] in ("tarea", "no")]
    if limite:
        filas = filas[:limite]
    if not filas:
        raise SystemExit(f"⛔ no hay casos de la pregunta {pregunta!r}")

    # La pregunta importa tanto como el modelo. La v1 era «¿pide una acción concreta?» y metía
    # TODO el spam comercial como tarea con 100% de confianza — claro: un correo de rebajas pide
    # que actúes. La decisión real no es esa, es si entra en SU lista.
    if esperar and not esperar_memoria(modelo, esperar):
        raise SystemExit(2)
    comprobar_memoria(modelo, forzar=forzar)

    OPC = {"A": "si, entra en su lista de tareas",
           "B": "no: publicidad, newsletter, acuse de recibo, o informacion sin accion"}
    INSTR = ("Eres el filtro de la lista de tareas de una persona. Decide si esto debe entrar en "
             "su lista de pendientes porque ELLA tiene que hacer algo. La publicidad y las "
             "newsletters nunca entran, aunque te pidan que hagas clic.")
    ESPERADO = {"tarea": "A", "no": "B"}

    aciertos, pares, tiempos, fallos = 0, [], [], []
    mayoria = max(sum(1 for f in filas if f["etiqueta"] == e) for e in ("tarea", "no")) / len(filas)

    for i, f in enumerate(filas, 1):
        try:
            elegida, probs, ms = puntuar(f["texto"], INSTR, OPC, modelo=modelo)
        except (urllib.error.URLError, OSError, KeyError, IndexError) as e:
            raise SystemExit(f"⛔ Ollama no responde ({type(e).__name__}: {e}).\n"
                             f"   ¿está levantado? `ollama serve` / `ollama list`")
        if elegida is None:
            continue
        ok = elegida == ESPERADO[f["etiqueta"]]
        aciertos += ok
        pares.append((probs[elegida], ok))
        tiempos.append(ms)
        if not ok:
            fallos.append((f["texto"][:90], f["etiqueta"], probs[elegida]))
        if i % 25 == 0:
            print(f"   … {i}/{len(filas)}", file=sys.stderr)

    n = len(pares)
    acc = aciertos / n if n else 0
    ece, bandas = _ece(pares)
    tiempos.sort()

    print(f"\n  modelo   {modelo}   ·   pregunta «{pregunta}»   ·   {n} casos")
    print(f"  acierto  {acc:.1%}      (baseline «di siempre la clase mayoritaria»: {mayoria:.1%})")
    print(f"  ECE      {ece:.3f}      (0 = perfectamente calibrado)")
    print(f"  latencia mediana {tiempos[len(tiempos)//2]:.0f} ms · p90 {tiempos[int(len(tiempos)*.9)]:.0f} ms")

    print("\n  calibración, banda a banda:")
    print("   confianza      n   dice   acierta   error")
    for lo, hi, cnt, conf, real in bandas:
        print(f"   {lo:.1f}-{hi:.1f}  {cnt:>5}  {conf:.1%}   {real:>6.1%}  {abs(conf-real):>6.1%}")

    if fallos:
        print(f"\n  fallos más confiados (los que más duelen), {len(fallos)} en total:")
        for txt, et, conf in sorted(fallos, key=lambda x: -x[2])[:5]:
            print(f"   {conf:.0%} seguro y era «{et}»: {txt}")
    return {"acierto": acc, "baseline": mayoria, "ece": ece, "n": n}


if __name__ == "__main__":
    args = sys.argv[1:]
    modelo = MODELO
    if "--modelo" in args:
        i = args.index("--modelo")
        modelo = args[i + 1]
        del args[i:i + 2]
    cmd = args[0] if args else "bench"
    if cmd == "bench":
        crudo = "--crudo" in args
        if crudo:
            args.remove("--crudo")
        forzar = "--forzar" in args
        if forzar:
            args.remove("--forzar")
        esperar = 0
        if "--esperar" in args:
            i = args.index("--esperar")
            esperar = int(args[i + 1])
            del args[i:i + 2]
        lim = None
        if "--limite" in args:
            i = args.index("--limite")
            lim = int(args[i + 1])
            del args[i:i + 2]
        preg = args[1] if len(args) > 1 else "es_tarea"
        bench(modelo=modelo, pregunta=preg, limite=lim, crudo=crudo, forzar=forzar, esperar=esperar)
    elif cmd == "preguntar":
        instruccion, estado = args[1], args[2]
        el, probs, ms = puntuar(estado, instruccion, {"A": "si", "B": "no"}, modelo=modelo)
        print(f"{el}  {probs}  ({ms:.0f} ms)")
    else:
        raise SystemExit(__doc__)
