#!/usr/bin/env python3
"""tools/laminillas_ventanilla.py — lo que hace `lector_clinico.py procesa laminillas_*`.

Plan «laminillas DFCI», F1-infra. `lector_clinico.procesa` delega aquí los procesadores de
laminillas; no se invoca a mano (el `main` es solo para inspeccionar las puertas).

Cada arranque, en este orden (cualquier fallo aborta ANTES de tocar un píxel):
  1. argumentos opacos: sin rutas (`/`, `~`), sin zona clínica, sin accesiones
     (`caso_publico._RE_CODIGO_AP`) — código 2;
  2. intérprete del venv por ruta absoluta (`~/.polaris-venvs/<venv>/bin/python`);
  3. puerta de disco: <15 GiB libres, o menos de lo declarado + 10 GiB → código 96;
  4. sha256 re-verificado de las láminas del manifiesto de SESION (si existe) → 94 si difiere;
  5. cerrojo `fcntl.flock` entre pesados (todos los de datos lo toman: uno a la vez);
  6. puerta de memoria: `ollama ps` vacío, libre = % de `memory_pressure` × RAM ≥ lo que pide el
     procesador, swap usado < 4 GB. Espera; a los 15 min, UN aviso por `salida.py` con los 5
     procesos más gordos (paso 1-ter) y sigue esperando, hasta 6 h → código 95;
  7. jaula generada en el momento (`laminillas_jaulas`), entorno en lista blanca exacta;
  8. `guarda_memoria` con TOPE_GB 10 (6 con colima arriba) → código 99 al pasarlo; 98 colgado.
     El visor, exento del vigilante.
El «hecho» por lámina/tesela lo escriben los procesadores (`laminillas_comun`): tras 98/99 se
relanza y reanudan.

TRAZA DE MEMORIA (plan F1-infra: «umbral real por procesador = pico + 2 GB con la `traza` de
`guarda_memoria` en la primera corrida sobre P-HER2NEG, sellado en (0)»): la primera corrida
vigilada de cada procesador que acaba con rc 0 deja `SESION/trazas/<procesador>.tsv` y ya no se
pisa. Si la corrida nombra láminas, solo cuenta la que incluye P-HER2NEG; sin láminas en los
argumentos (ingesta, precongela), cuenta la primera. Una corrida fallida deja
`<procesador>.fallida.tsv` (diagnóstico) y la siguiente vuelve a medir.
`laminillas_congela congela|recongela --traza a,b` recibe NOMBRES de procesador (los argumentos no
llevan rutas) y la ventanilla los traduce a `a=trazas/a.tsv,…` (relativas a SESION, el cwd), solo
si esas trazas existen.
"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
import laminillas_jaulas as J  # noqa: E402

GiB = 1024 ** 3
DISCO_MIN_GB = 15
DISCO_MARGEN_GB = 10
SWAP_MAX_GB = 4.0
SWAP_CRECE_MAX_MB = 50                 # con swap ≥ SWAP_MAX_GB, lo tolerado en la ventana de 10 s
ESPERA_AVISO_S = 15 * 60
ESPERA_MAX_S = 6 * 3600
SONDEO_S = 30
TOPE_GB = 10
TOPE_GB_COLIMA = 6
AVISO_CADA_S = 6 * 3600                 # anti-spam del aviso de memoria
CERROJO = os.path.join(J.VENVS, ".cerrojo-pesados")
AVISO_SELLO = os.path.join(J.VENVS, ".aviso-memoria")

COD_ARGS, COD_SHA, COD_MEMORIA_ESPERA, COD_DISCO = 2, 94, 95, 96

# Procesador → configuración. `script` relativo al repo (se resuelve junto a ESTE fichero:
# lo que se prueba en una rama es el código de esa rama), `modo` se antepone a los argumentos
# (no lo controla quien llama), `venv`, `perfil`, memoria que pide (GB), disco que declara (GB),
# si toma el cerrojo y si lo vigila guarda_memoria.
CONF = {
    "laminillas_ingesta":  dict(script="tools/laminillas_proc.py", modo="ingesta", venv="patologia",
                                perfil="analisis-ingesta", mem=5, disco=10, datos=True),
    # Puerta del piloto tras (0), puntos 4-6: la orden es el primer argumento de quien llama
    # (lista cerrada = `laminillas_proc.ORDENES`; la ventanilla antepone «analisis»). Memoria del
    # plan para InstanSeg, registro y QC (5 GB); `--valis` es pesado (9); `tribunal-listo` solo
    # lee JSON (sin cerrojo ni vigilante).
    "laminillas":          dict(script="tools/laminillas_proc.py", modo="analisis", venv="patologia",
                                perfil="analisis", mem=5, disco=10, datos=True,
                                ordenes=("piloto-i", "piloto-ibis", "registro-par",
                                         "lista-postcongelacion", "metricas", "geojson",
                                         "tribunal-listo",
                                         # F3 parte A (`laminillas_f3`, `ORDENES_F3`)
                                         "consenso", "puerta-p63", "regiones-pobres",
                                         "lectura-digital"),
                                mem_orden={"tribunal-listo": 0}, mem_bandera={"--valis": 9}),
    # F3 parte A, ROI de Carlos: lee el pptx de ORIGEN, así que va en la jaula de la ingesta (la
    # única que lee ORIGEN); escribe solo en SESION/f3/roi/. Lista cerrada =
    # `laminillas_proc.ORDENES_ORIGEN`. SIFT por teselas del ×4 (5 GB, como el registro).
    "laminillas_roi":      dict(script="tools/laminillas_proc.py", modo="analisis", venv="patologia",
                                perfil="analisis-ingesta", mem=5, disco=2, datos=True,
                                ordenes=("roi-carlos",)),
    "laminillas_registro": dict(script="tools/laminillas_proc.py", modo="registro", venv="valis",
                                perfil="analisis", mem=5, disco=10, datos=True),
    "laminillas_qc":       dict(script="tools/laminillas_proc.py", modo="qc", venv="patologia",
                                perfil="analisis", mem=5, disco=5, datos=True),
    # Puerta del piloto, puntos 2-3: pre-congelación, congelación (0), recongelación y
    # verificación del sello. El primer argumento es la orden (lista cerrada `ordenes`); `verifica`
    # solo lee JSON (sin cerrojo ni vigilante). `--traza` lleva nombres de procesador.
    "laminillas_congela":  dict(script="tools/laminillas_congela.py", modo=None,
                                venv="patologia", perfil="analisis", mem=9, disco=10, datos=True,
                                ordenes=("precongela", "congela", "recongela", "verifica"),
                                mem_orden={"verifica": 0}, trazas_arg=True),
    # Punto 4 (i)/(i-bis): InstanSeg con la configuración sellada, por lámina (`segmenta P-KI67`).
    "laminillas_segmenta": dict(script="tools/laminillas_proc.py", modo="segmenta",
                                venv="patologia", perfil="analisis", mem=5, disco=10, datos=True),
    # F3 parte B (`laminillas_he`): H&E del primario (HistoPLUS + NuLite sobre InstanSeg, WSInfer),
    # hueso (QC y composición de tejido, sin células) y exploratorio (UNI2-h, H-optimus-1, TITAN).
    # Orden = primer argumento (lista cerrada = `laminillas_he.ORDENES`). `primario` y `explora`
    # cargan modelos: pesados (9 GB); `hueso`, 5; `cellvitpp` solo escribe el formato del enganche
    # de la nube (sin cerrojo ni vigilante).
    "laminillas_he":       dict(script="tools/laminillas_he.py", modo=None, venv="patologia",
                                perfil="analisis", mem=9, disco=10, datos=True,
                                ordenes=("primario", "hueso", "explora", "cellvitpp"),
                                mem_orden={"hueso": 5, "cellvitpp": 0}),
    # F1.4: la ÚNICA boca a ~/Laminillas-N1/, dentro de exporta.sb (lee SESION, escribe solo N1).
    "laminillas_exporta":  dict(script="tools/laminillas_exporta.py", modo=None, venv="patologia",
                                perfil="exporta", mem=3, disco=2, datos=True),
    "laminillas_visor":    dict(script="tools/laminillas_proc.py", modo="visor", venv="patologia",
                                perfil="visor-clinico", mem=0, disco=0, datos=True, vigila=False),
    # Humo SIN datos (F2): TIFF sintético dentro de la jaula de análisis real.
    "laminillas_humo":     dict(script="tools/laminillas_humo.py", modo=None, venv="patologia",
                                perfil="analisis", mem=9, disco=5, datos=False),
    "laminillas_humo_valis": dict(script="tools/laminillas_humo.py", modo="valis", venv="valis",
                                  perfil="analisis", mem=5, disco=5, datos=False),
    # F1.0: red sin datos; único con HF_TOKEN (del Llavero). No instala.
    "laminillas_pesos":    dict(script="tools/laminillas_pesos.py", modo=None, venv="patologia",
                                perfil="red-sin-zona", mem=0, disco=30, datos=False, red=True,
                                hf=True),
    # F1.0, venv valis: los pesos de kornia que carga VALIS 1.2.0 (DISK, LightGlue). Sin token.
    "laminillas_pesos_valis": dict(script="tools/laminillas_pesos.py", modo="kornia", venv="valis",
                                   perfil="red-sin-zona", mem=0, disco=1, datos=False, red=True),
}


def interprete(venv):
    return os.path.join(J.VENVS, venv, "bin", "python")


# ── 1. argumentos opacos ──────────────────────────────────────────────────────────────────────
_RE_ARG = re.compile(r"^[A-Za-z0-9_.,=:+-]{1,64}$")


def valida_args(args):
    """None si valen; si no, el motivo. Opaco = sin rutas, sin zona clínica, sin accesiones.
    Los procesadores saben dónde está SESION: ningún argumento necesita una ruta."""
    try:
        import caso_publico
        re_ap = caso_publico._RE_CODIGO_AP
    except Exception as e:                  # fail-closed: sin detector de accesiones, nada
        return "no pude cargar el detector de accesiones (%r)" % e
    zc = J._zc().ZC
    for a in args:
        if "/" in a or "~" in a or "\\" in a:
            return "argumento con ruta: los procesadores de laminillas solo aceptan nombres opacos"
        if zc.es_ruta_clinica(a) or zc.es_segmento_clinico(a):
            return "argumento que nombra zona clínica"
        if re_ap.search(a) or re.search(r"\d{2}[A-Za-z]\d{6,}", a):
            return "argumento con pinta de accesión de anatomía patológica"
        if not _RE_ARG.match(a):
            return "argumento con caracteres fuera de [A-Za-z0-9_.,=:+-] o >64"
    return None


# ── 3. disco ──────────────────────────────────────────────────────────────────────────────────
def puerta_disco(necesita_gb, libre_bytes=None):
    libre = shutil.disk_usage(J.HOME).free if libre_bytes is None else libre_bytes
    libre_gb = libre / GiB
    minimo = max(DISCO_MIN_GB, necesita_gb + DISCO_MARGEN_GB)
    if libre_gb < minimo:
        return False, "%.1f GiB libres; hacen falta %d" % (libre_gb, minimo)
    return True, "%.1f GiB libres" % libre_gb


# ── 4. sha256 ─────────────────────────────────────────────────────────────────────────────────
def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def verifica_manifiesto(sesion, args):
    """Re-verifica el sha256 de las láminas del manifiesto (las nombradas en args, o todas)."""
    m = os.path.join(sesion, "manifiesto.json")
    if not os.path.isfile(m):
        return True, "sin manifiesto todavía"
    with open(m, encoding="utf-8") as f:
        man = json.load(f)
    laminas = man.get("laminas", {})
    pedidas = [a for a in args if a in laminas] or list(laminas)
    for n in pedidas:
        ent = laminas[n]
        ruta = os.path.join(sesion, ent["fichero"])
        if "/" in ent["fichero"] or not os.path.isfile(ruta):
            return False, "%s: fichero ausente o con ruta" % n
        if sha256(ruta) != ent["sha256"]:
            return False, "%s: sha256 distinto del manifiesto" % n
    return True, "%d lámina(s) con sha256 igual" % len(pedidas)


# ── órdenes y trazas de memoria ───────────────────────────────────────────────────────────────
DIR_TRAZAS = "trazas"
LAMINA_TRAZA = "P-HER2NEG"
_RE_LAMINA = re.compile(r"^[PB]-[A-Z0-9]+(?:-[0-9]+)?$")       # P-HE, P-KI67, B-HE-1…


def ruta_traza(sesion, nombre):
    return os.path.join(sesion, DIR_TRAZAS, nombre + ".tsv")


def mem_de(conf, args):
    """Memoria que pide ESTA corrida: la de la orden si la declara (`mem_orden`), si no la del
    procesador; una bandera pesada (`mem_bandera`, p. ej. `--valis`) la sube."""
    mem = conf.get("mem_orden", {}).get(args[0] if args else None, conf["mem"])
    if mem:
        for b, gb in conf.get("mem_bandera", {}).items():
            if b in args:
                mem = max(mem, gb)
    return mem


def mem_sellada(sesion, nombre):
    """Umbral de memoria del procesador SELLADO en (0) (`memoria.umbral_gb` = pico de su traza de
    guarda_memoria + 2 GB; plan F1-infra), o None. El sello se verifica (sha256); si no vale, None
    (rige el de partida; el código de medida ya se niega a medir con un sello inválido)."""
    try:
        import laminillas_sello as SL
        s = SL.carga(os.path.join(sesion, SL.FICHERO))
    except Exception:                                              # noqa: BLE001
        return None
    v = ((s.d.get("memoria") or {}).get("umbral_gb") or {}).get(nombre)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def valida_orden(conf, args):
    """None si la orden vale (o el procesador no tiene lista de órdenes); si no, el motivo."""
    ordenes = conf.get("ordenes")
    if ordenes and (not args or args[0] not in ordenes):
        return "orden %r fuera de la lista cerrada (%s)" % (args[0] if args else None,
                                                           ", ".join(ordenes))
    return None


def toca_traza(sesion, nombre, args):
    """¿Esta corrida es la que deja la traza del procesador? Solo si aún no hay una y, si nombra
    láminas, solo si una de ellas es P-HER2NEG."""
    if os.path.isfile(ruta_traza(sesion, nombre)):
        return False
    laminas = [a for a in args if _RE_LAMINA.match(a)]
    return not laminas or LAMINA_TRAZA in laminas


def traduce_trazas(args, sesion):
    """`--traza a,b` (o `--traza=a,b`), con NOMBRES de procesador → `--traza a=trazas/a.tsv,…`.
    Devuelve (args, None) o (None, motivo). Lo hace la ventanilla DESPUÉS de validar los
    argumentos opacos: quien llama nunca escribe una ruta."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--traza" or a.startswith("--traza="):
            if a == "--traza":
                if i + 1 >= len(args):
                    return None, "--traza sin valor"
                valor, i = args[i + 1], i + 2
            else:
                valor, i = a.split("=", 1)[1], i + 1
            pares = []
            for proc in [p for p in valor.split(",") if p]:
                if proc not in CONF:
                    return None, "--traza: %r no es un procesador de la ventanilla" % proc
                r = ruta_traza(sesion, proc)
                if not os.path.isfile(r) or os.path.getsize(r) == 0:
                    return None, ("--traza: no hay traza de %s (corre antes su primera corrida "
                                  "vigilada sobre %s)" % (proc, LAMINA_TRAZA))
                pares.append("%s=%s/%s.tsv" % (proc, DIR_TRAZAS, proc))
            if not pares:
                return None, "--traza vacía"
            out += ["--traza", ",".join(pares)]
            continue
        out.append(a)
        i += 1
    return out, None


def cierra_traza(parcial, final, codigo, log=lambda m: None):
    """Tras la corrida: con rc 0, la traza parcial pasa a ser LA del procesador; si no, queda como
    `<procesador>.fallida.tsv` y la siguiente corrida vuelve a medir. Devuelve la ruta que quedó."""
    if not os.path.isfile(parcial):
        log("traza de memoria: guarda_memoria no dejó fichero")
        return None
    if codigo == 0 and os.path.getsize(parcial) > 0:
        destino = final
    else:
        destino = final[:-len(".tsv")] + ".fallida.tsv"
    os.replace(parcial, destino)
    os.chmod(destino, 0o600)
    log("traza de memoria: %s/%s%s" % (DIR_TRAZAS, os.path.basename(destino),
                                       "" if destino == final else " (rc %d: no vale para el "
                                       "umbral)" % codigo))
    return destino


# ── 6. memoria ────────────────────────────────────────────────────────────────────────────────
def _ollama_vacio():
    exe = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
    if not os.path.exists(exe):
        return True
    try:
        out = subprocess.run([exe, "ps"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return False                         # no sé si hay modelo cargado: no paso
    return len([ln for ln in out.splitlines() if ln.strip()]) <= 1


def _swapouts():
    """Páginas enviadas a swap desde el arranque (vm_stat) y tamaño de página, o None."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
        pag = int(re.search(r"page size of (\d+) bytes", out).group(1))
        return int(re.search(r"Swapouts:\s+(\d+)", out).group(1)), pag
    except Exception:
        return None


def estado_memoria(ventana_s=10):
    """Memoria libre, swap usado y si la máquina está EMPUJANDO a swap ahora (en `ventana_s`).

    Desviación del plan (1-oct-26): el plan pide «swap usado < 4 GB» a secas. En macOS el swap
    usado no baja aunque la presión pase (medido hoy: 7,7 GB de swap con 75 % libre), así que esa
    puerta no abriría hasta reiniciar. Lo que ahoga es el swap que CRECE: con swap ≥ 4 GB solo se
    pasa si en la ventana no salen páginas a swap (`vm_stat` Swapouts). Por debajo de 4 GB, como
    el plan."""
    import healthcheck
    s0 = _swapouts()
    time.sleep(ventana_s)
    s1 = _swapouts()
    creciendo = None
    if s0 and s1:
        creciendo = (s1[0] - s0[0]) * s1[1] / 1e6           # MB empujados a swap en la ventana
    r = healthcheck._recursos_ahora()
    ram = healthcheck._ram_fisica_gb()
    if r is None or ram is None:
        return None
    swap, libre_pct, gordos = r
    libre_gb = (libre_pct / 100.0) * ram if libre_pct == libre_pct else 0.0
    return dict(swap_gb=swap, libre_gb=libre_gb, libre_pct=libre_pct, ram_gb=ram,
                gordos=gordos, ollama_vacio=_ollama_vacio(), swap_creciendo_mb=creciendo)


def memoria_basta(e, necesita_gb):
    if e is None:
        return False, "no pude leer memoria"
    if not e["ollama_vacio"]:
        return False, "ollama tiene un modelo cargado (ollama stop)"
    if e["swap_gb"] >= SWAP_MAX_GB:
        crece = e.get("swap_creciendo_mb")
        if crece is None or crece > SWAP_CRECE_MAX_MB:
            return False, "swap usado %.1f GB (máx. %.0f) y creciendo (%s MB en la ventana)" % (
                e["swap_gb"], SWAP_MAX_GB, "?" if crece is None else "%.0f" % crece)
    if e["libre_gb"] < necesita_gb:
        return False, "libres %.1f GB, pide %d" % (e["libre_gb"], necesita_gb)
    nota = " (rancio: no crece)" if e["swap_gb"] >= SWAP_MAX_GB else ""
    return True, "libres %.1f GB, swap %.1f GB%s" % (e["libre_gb"], e["swap_gb"], nota)


def _aviso_memoria(nombre, motivo, gordos, dry=False):
    """UN aviso por `salida.py` (anti-spam: uno cada 6 h). Nombres de proceso, nada clínico."""
    try:
        if time.time() - os.path.getmtime(AVISO_SELLO) < AVISO_CADA_S:
            return "silenciado (anti-spam)"
    except OSError:
        pass
    lista = "; ".join("%s %.1f GB" % (c[2][:30], c[0]) for c in (gordos or [])[:5])
    texto = ("Las laminillas esperan memoria para «%s» desde hace 15 min (%s). Lo que más ocupa: "
             "%s. Si puedes, cierra Chrome o WhatsApp; sigo esperando." % (nombre, motivo, lista))
    import salida
    r = salida.report_to_titular(texto, dry=dry, voz="sobria", fuente="laminillas_ventanilla")
    with open(AVISO_SELLO, "w") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    return r


def puerta_memoria(nombre, necesita_gb, log=print, medir=estado_memoria, dormir=time.sleep,
                   reloj=time.time, aviso=_aviso_memoria, espera_aviso=ESPERA_AVISO_S,
                   espera_max=ESPERA_MAX_S):
    t0, avisado = reloj(), False
    while True:
        e = medir()
        ok, motivo = memoria_basta(e, necesita_gb)
        if ok:
            return True, motivo
        dt = reloj() - t0
        if dt >= espera_max:
            return False, "tras %.0f min sigue sin memoria: %s" % (dt / 60, motivo)
        if dt >= espera_aviso and not avisado:
            aviso(nombre, motivo, (e or {}).get("gordos"))
            avisado = True
        log("esperando memoria (%s)" % motivo)
        dormir(SONDEO_S)


def colima_arriba():
    exe = shutil.which("colima") or "/opt/homebrew/bin/colima"
    if not os.path.exists(exe):
        return False
    try:
        return subprocess.run([exe, "status"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return True                          # en la duda, el techo bajo


# ── 7. entorno en lista blanca ────────────────────────────────────────────────────────────────
def entorno(conf, sesion, token=None):
    """Lista blanca EXACTA del plan. Nada de os.environ."""
    venv = os.path.join(J.VENVS, conf["venv"])
    if conf.get("red"):
        tmp = J.TMP_RED
        caches = J.CACHE
    else:
        tmp = os.path.join(sesion, "tmp")
        caches = os.path.join(sesion, "cache")
    env = {
        "HOME": J.HOME,
        "PATH": "%s/bin:/usr/bin:/bin:/opt/homebrew/bin" % venv,
        "TMPDIR": tmp,
        "HF_HOME": os.path.join(J.CACHE, "hf"),
        "TORCH_HOME": os.path.join(J.CACHE, "torch"),
        "MPLCONFIGDIR": os.path.join(caches, "mpl"),
        "PYTORCH_ENABLE_MPS_FALLBACK": "1",
        "BTP_VENTANILLA": "1",
        "INSTANSEG_BIOIMAGEIO_PATH": os.path.join(J.CACHE, "instanseg"),
        "HF_MODULES_CACHE": os.path.join(caches, "hf_modules"),
        "NUMBA_CACHE_DIR": os.path.join(caches, "numba"),
        "XDG_CACHE_HOME": os.path.join(caches, "xdg"),
    }
    if conf.get("red"):
        if token:
            env["HF_TOKEN"] = token
    else:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def token_hf():
    """HF_TOKEN del Llavero (servicio btp-hf-token). Solo para laminillas_pesos, en el padre."""
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "btp-hf-token",
                            "-w"], capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _prepara_dirs(conf, sesion):
    for d in (J.VENVS, J.CACHE, J.TMP_RED, os.path.join(J.CACHE, "hf"),
              os.path.join(J.CACHE, "torch"), os.path.join(J.CACHE, "instanseg")):
        os.makedirs(d, mode=0o700, exist_ok=True)
    if not conf.get("red"):
        dirs = [sesion, os.path.join(sesion, "tmp"), os.path.join(sesion, "cache")]
        if conf["perfil"] == "analisis-ingesta":
            dirs.append(J.ORIGEN)
        for d in dirs:
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)


def _mod():
    """El propio módulo, para que los tests puedan sustituir puertas y techos sin tocar código."""
    return sys.modules[__name__]


def ejecuta(agent, nombre, args, log=None, sesion=None, jaulas_dir=None):
    """Lanza el procesador `nombre`. Devuelve el código de salida."""
    log = log or (lambda m: print("[laminillas] " + m, file=sys.stderr, flush=True))
    conf = CONF[nombre]
    sesion = sesion or J.SESION
    args = list(args)
    mal = valida_args(args) or valida_orden(conf, args)
    if mal:
        log("RECHAZADO: %s" % mal)
        return COD_ARGS
    mem = mem_de(conf, args)
    if mem:                                     # tras (0): pico sellado + 2 GB, si es mayor
        sellada = mem_sellada(sesion, nombre)
        if sellada:
            mem = max(mem, int(math.ceil(sellada)))
    pedidos = args                              # los de quien llama (antes de traducir trazas)
    if conf.get("trazas_arg"):
        args, mal = traduce_trazas(args, sesion)
        if mal:
            log("RECHAZADO: %s" % mal)
            return COD_ARGS
    raiz = os.path.dirname(_AQUI)
    script = os.path.join(raiz, conf["script"])
    py = interprete(conf["venv"])
    if not os.path.isfile(script) or not os.path.isfile(py):
        log("RECHAZADO: falta %s o su intérprete %s" % (script, py))
        return 1
    ok, motivo = puerta_disco(conf["disco"])
    if not ok:
        log("ABORTO (disco): %s" % motivo)
        return COD_DISCO
    log("disco: %s" % motivo)
    _prepara_dirs(conf, sesion)
    if conf["datos"]:
        ok, motivo = verifica_manifiesto(sesion, args)
        if not ok:
            log("ABORTO (sha256): %s" % motivo)
            return COD_SHA
        log("sha256: %s" % motivo)
    cerrojo = None
    previo = os.getcwd()
    try:
        if mem:
            import fcntl
            cerrojo = open(_mod().CERROJO, "w")
            try:
                fcntl.flock(cerrojo, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                log("otro pesado tiene el cerrojo; espero")
                fcntl.flock(cerrojo, fcntl.LOCK_EX)
            ok, motivo = _mod().puerta_memoria(nombre, mem, log=log)
            if not ok:
                log("ABORTO (memoria): %s" % motivo)
                return COD_MEMORIA_ESPERA
            log("memoria: %s" % motivo)
        token = None
        if conf.get("hf"):
            token = token_hf()
            if not token:
                log("ABORTO: no pude leer HF_TOKEN del Llavero (btp-hf-token). Desde una sesión "
                    "Background el Llavero no se ve (rc 36): lánzalo en gui/501.")
                return 1
        tmp = J.TMP_RED if conf.get("red") else os.path.join(sesion, "tmp")
        # Con red, de `tools/` solo el CÓDIGO VERSIONADO (los `*.py` del primer nivel que da
        # `git ls-files`, uno a uno; sin `__pycache__/`), no la carpeta entera: el 2-oct-26
        # `extra_lectura=[tools]` dejaba leer, en una jaula con internet, tokens de tools/state,
        # overlays de PII y el estado vivo, y después la regex `*.py` dejaba leer un borrador sin
        # versionar de la casa base.
        perfil = J.escribe(conf["perfil"], destino_dir=jaulas_dir, sesion=sesion, tmpdir=tmp,
                           extra_codigo=[os.path.join(raiz, "tools")] if conf.get("red") else ())
        env = entorno(conf, sesion, token)
        comando = ["/usr/bin/sandbox-exec", "-f", perfil, py, script]
        if conf["modo"]:
            comando.append(conf["modo"])
        comando += list(args)
        # cwd = SESION (o el venv, en red): los procesadores no reciben rutas.
        os.chdir(os.path.join(J.VENVS, conf["venv"]) if conf.get("red") else sesion)
        log("jaula %s · %s %s" % (conf["perfil"], os.path.basename(script), " ".join(args)))
        if conf.get("vigila", True) and mem:
            import guarda_memoria
            tope = _mod().TOPE_GB_COLIMA if colima_arriba() else _mod().TOPE_GB
            traza_final = parcial = None
            if toca_traza(sesion, nombre, pedidos):   # primera corrida: se mide (plan F1-infra)
                traza_final = ruta_traza(sesion, nombre)
                os.makedirs(os.path.dirname(traza_final), mode=0o700, exist_ok=True)
                parcial = traza_final + ".parcial"
            codigo, pico = guarda_memoria.corre(comando, tope, env=env, inactivo_s=30 * 60,
                                                traza=parcial)
            log("fin rc=%d · pico %.1f GB sin Metal (techo %g)" % (codigo, pico, tope))
            if parcial:
                cierra_traza(parcial, traza_final, codigo, log)
            return codigo
        return subprocess.call(comando, env=env)
    finally:
        os.chdir(previo)
        if cerrojo is not None:
            cerrojo.close()             # suelta el flock

if __name__ == "__main__":
    # Inspección de puertas, sin lanzar nada.
    print(json.dumps({"disco": puerta_disco(0),
                      "memoria_9": memoria_basta(estado_memoria(), 9),
                      "memoria_5": memoria_basta(estado_memoria(), 5),
                      "colima": colima_arriba()}, ensure_ascii=False, indent=1))
