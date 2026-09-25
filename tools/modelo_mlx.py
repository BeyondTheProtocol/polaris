#!/usr/bin/env python3
"""tools/modelo_mlx.py — un modelo local en MLX, con los frenos de memoria. Lo usan el bench y el presorteo.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

Extraído de `bench_modelos.py` (25-sep-26) para que el bench y la rutina de producción compartan
UN solo freno: dos frenos que miden distinto acaban diciendo cosas distintas.

Lo que hace:
  · `MLX.puntuar(contenido, opciones)` → (etiqueta, confianza, ms). Pide UNA letra (A, B, …) y lee
    el vector ENTERO de probabilidades: sin el techo top-20 de Ollama.
  · `freno()` antes de cargar (holgura de `score_local.HOLGURA_GB`) y `vigia()` durante el lote
    (memoria libre, crecimiento del swap y footprint de MLX).
Corre en su venv (`~/.venvs/mlx-bench`), no en el `.venv` de casa base.
"""
import math
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_DEFECTO = "mlx-community/Qwen3.5-9B-4bit"


class MLX:
    def __init__(self, repo):
        self.nombre = "mlx:" + repo
        self.repo = repo
        self._cargado = False

    def tamano_gb(self):
        """Tamaño en disco de los pesos del snapshot local. Para 4 bits es buen proxy de lo que
        ocupa en memoria unificada. None si no está descargado → el freno bloquea (fail-closed)."""
        base = os.path.expanduser("~/.cache/huggingface/hub/models--" + self.repo.replace("/", "--"))
        tot = 0
        for raiz, _d, fs in os.walk(os.path.join(base, "blobs")):
            tot += sum(os.path.getsize(os.path.join(raiz, f)) for f in fs)
        return tot / 1e9 if tot else None

    def cargar(self):
        import mlx.core as mx
        from mlx_lm import load
        self.mx = mx
        # 25-sep-26: sin esto, a los 90 min el proceso ocupaba 11 GB y el swap 13,4 GB con unos
        # pesos de 6 GB. MLX guarda en caché un búfer por cada longitud de prompt distinta y no
        # los suelta. Techo duro (pesos + 2 GB) y caché vaciada tras cada caso.
        tam = self.tamano_gb() or 6.0
        mx.set_memory_limit(int((tam + 2.0) * 1e9))
        mx.set_cache_limit(int(0.5e9))
        t0 = time.time()
        self.model, self.tok = load(self.repo)
        self.carga_s = time.time() - t0
        self._ids = {}
        self._cargado = True

    def _ids_letra(self, letra):
        if letra not in self._ids:
            ids = set()
            for v in (letra, " " + letra):
                enc = self.tok.encode(v, add_special_tokens=False)
                if len(enc) == 1:
                    ids.add(enc[0])
            self._ids[letra] = sorted(ids)
        return self._ids[letra]

    def puntuar(self, contenido, opciones):
        """`opciones` = {letra: (etiqueta, descripción)}. Devuelve (etiqueta, confianza, ms);
        (None, 0.0, ms) si ninguna letra tiene probabilidad (fail-closed, no inventa)."""
        mx = self.mx
        msgs = [{"role": "user", "content": contenido}]
        try:
            p = self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                             enable_thinking=False)
        except TypeError:
            p = self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        ids = self.tok.encode(p, add_special_tokens=False)
        t0 = time.perf_counter()
        logits = self.model(mx.array(ids)[None])[0, -1].astype(mx.float32)
        lp = logits - mx.logsumexp(logits)
        mx.eval(lp)
        ms = (time.perf_counter() - t0) * 1000
        del logits
        mx.clear_cache()
        probs = {}
        for letra in opciones:
            ii = self._ids_letra(letra)
            probs[letra] = sum(math.exp(lp[i].item()) for i in ii) if ii else 0.0
        s = sum(probs.values())
        if s <= 0:
            return None, 0.0, ms
        mejor = max(probs, key=probs.get)
        return opciones[mejor][0], probs[mejor] / s, ms

    def memoria_ahora_gb(self):
        return (self.mx.get_active_memory() + self.mx.get_cache_memory()) / 1e9

    def memoria_pico_gb(self):
        return self.mx.get_peak_memory() / 1e9


def freno(cand, forzar=False, esperar=0):
    """Con `esperar` (minutos) reintenta cada 2 min hasta que haya sitio. CON TOPE, siempre: un
    `until` sin límite fue el fallo del 13-sep-26 (14 h colgado esperando una marca)."""
    import score_local as sl
    if not isinstance(cand, MLX):
        return
    tam, libre = cand.tamano_gb(), sl._memoria_libre_gb()
    limite = time.time() + esperar * 60
    while (tam is not None and libre is not None and libre - tam < sl.HOLGURA_GB
           and time.time() < limite):
        print(f"   … {libre:.1f} GB libres, hacen falta {tam + sl.HOLGURA_GB:.1f}; "
              f"reintento en 120 s", flush=True)
        time.sleep(120)
        libre = sl._memoria_libre_gb()
    if tam is None or libre is None:
        msg = f"no puedo medir la memoria (libre={libre}, modelo={tam})"
        if not forzar:
            raise SystemExit(f"⛔ {msg}. ¿Pesos descargados? Con --forzar sigue bajo tu riesgo.")
        print(f"  ⚠️  {msg}; sigo por --forzar")
        return
    margen = libre - tam
    print(f"  memoria: {libre:.1f} GB libres · pesos {tam:.1f} GB · margen {margen:.1f} GB "
          f"(mínimo {sl.HOLGURA_GB})")
    if margen < sl.HOLGURA_GB and not forzar:
        raise SystemExit(f"⛔ NO se carga: dejaría {margen:.1f} GB al sistema. El 20-sep-26 un "
                         f"bench sin freno tumbó el mini.")


def _swap_gb():
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True,
                             timeout=5).stdout
        return float(re.search(r"used\s*=\s*([\d.]+)M", out).group(1)) / 1024.0
    except Exception:
        return None


_SWAP_INICIO = []


def vigia(cand, minimo_gb=1.5, swap_max_gb=1.5, proceso_max_gb=None):
    """El freno de arranque no ve lo que pasa DURANTE una hora de bench. Se para si:
      · el sistema baja de `minimo_gb` libres, o
      · el swap crece más de `swap_max_gb` desde el arranque, o
      · MLX (activa + caché) pasa de pesos + 2,5 GB.
    El 25-sep-26 solo miraba lo primero, y el porcentaje libre se sostenía a base de swap: el
    proceso llegó a 11 GB y el swap a 13,4 GB sin que saltara. Medir no vale un reinicio."""
    import score_local as sl
    if not isinstance(cand, MLX):
        return
    swap = _swap_gb()
    if swap is not None and not _SWAP_INICIO:
        _SWAP_INICIO.append(swap)
    motivo = None
    libre = sl._memoria_libre_gb()
    if libre is not None and libre < minimo_gb:
        motivo = f"quedan {libre:.1f} GB libres (mínimo {minimo_gb})"
    elif swap is not None and swap - _SWAP_INICIO[0] > swap_max_gb:
        motivo = f"el swap ha crecido {swap - _SWAP_INICIO[0]:.1f} GB desde el arranque"
    else:
        tope = proceso_max_gb or ((cand.tamano_gb() or 6.0) + 2.5)
        usado = cand.memoria_ahora_gb()
        if usado > tope:
            motivo = f"MLX ocupa {usado:.1f} GB (tope {tope:.1f})"
    if motivo:
        raise SystemExit(f"⛔ abortado a mitad: {motivo}. Nada se ha escrito a medias.")
