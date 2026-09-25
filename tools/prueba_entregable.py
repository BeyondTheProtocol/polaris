#!/usr/bin/env python3
"""prueba_entregable.py — ¿el job dejó lo que prometió? (20-sep-2026)

POR QUÉ EXISTE (deuda `cola-marca-done-sin-verificar-cambio-real`, 3 detecciones):
`btp_dispatcher.sh` cerraba un job mirando SOLO el código de salida. Un job que chocó contra el
muro, respondió en prosa lo que «haría» y salió limpio se marcaba HECHO. Medido: 6,03 USD por un
arreglo de `tools/seguimiento.py` que nunca existió, y 0,24 USD de un radar sin commit, rama ni
nota. Dinero gastado, panel diciendo que se hizo, y un KPI que parecía atendido.

QUÉ HACE: evalúa la `prueba` declarada en el job contra el disco. Dos formas, vocabulario cerrado:
  · {"tipo": "ruta",  "ruta":  "ruta/relativa"}  → existe y se tocó DESPUÉS de lanzar al agente
  · {"tipo": "deuda", "clave": "<clave>"}        → la entrada existe y se anotó después

QUÉ **NO** DEMUESTRA: `mtime` no prueba autoría. Un `git checkout`, otro proceso o un `touch` del
propio agente dan verde. Es un falso positivo aceptable porque el gate es fail-closed en la
dirección que importa —sin cambio no se cierra— pero no es una garantía criptográfica.

DISEÑO: solo stdlib, solo lectura. Sin subprocess, sin git, sin red, sin importar `cola`. Corre en
el único punto del lazo que no se puede permitir colgarse, así que no hay nada que pueda tardar.
Todo lo que no se entiende o no se puede leer → False (fail-closed).

CLI (lo usa el dispatcher):
  prueba_entregable.py --prueba '<json>' --desde <epoch>   → rc 0 cumple, rc 1 no; motivo a stdout
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deuda import normalizar_clave  # noqa: E402  una sola forma de clave (24-sep-26); solo stdlib

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
TIPOS = ("ruta", "deuda")


def _mas_reciente(path):
    """mtime del fichero, o el del hijo más reciente si es carpeta. None si no hay nada."""
    try:
        if os.path.isdir(path):
            tiempos = []
            with os.scandir(path) as it:
                for e in it:
                    try:
                        tiempos.append(e.stat().st_mtime)
                    except OSError:
                        continue
            return max(tiempos) if tiempos else None
        return os.stat(path).st_mtime
    except OSError:
        return None


def _ts_deuda(clave):
    """El momento en que esa deuda se tocó por última vez. None si no existe o no se puede leer."""
    try:
        with open(os.path.join(STATE, "deuda.json"), encoding="utf-8") as f:
            libro = json.load(f)
    except Exception:
        return None
    if not isinstance(libro, dict):
        return None
    # Se compara la forma normalizada de las dos partes: el libro puede traer aún claves viejas
    # con backticks y el job, la clave tal cual venía en la prosa de la alerta.
    buscada = normalizar_clave(clave)
    tocadas = [v for k, v in libro.items() if isinstance(v, dict) and normalizar_clave(k) == buscada]
    if not tocadas:
        return None
    sellos = [it.get(k) for it in tocadas
              for k in ("anotado_ts", "abierto_ts", "visto_ts", "cerrado_ts", "remitido_ts")]
    sellos = [s for s in sellos if isinstance(s, (int, float))]
    return max(sellos) if sellos else None


def cumple(prueba, desde_epoch):
    """(ok, motivo). `desde_epoch` es el instante ANTES de lanzar al agente, no el de creación del
    job: uno reencolado aceptaría como prueba algo que tocó otro proceso hace dos días."""
    if not isinstance(prueba, dict):
        return False, "prueba ilegible"
    tipo = prueba.get("tipo")
    if tipo not in TIPOS:
        return False, "tipo de prueba desconocido: %r" % (tipo,)
    try:
        desde = float(desde_epoch)
    except (TypeError, ValueError):
        return False, "instante de referencia ilegible"

    if tipo == "ruta":
        rel = prueba.get("ruta")
        if not isinstance(rel, str) or not rel.strip():
            return False, "ruta vacia"
        destino = os.path.join(REPO, rel)
        ts = _mas_reciente(destino)
        if ts is None:
            return False, "no existe o esta vacio: %s" % rel
        if ts <= desde:
            return False, "existia pero no se toco: %s" % rel
        return True, "tocado: %s" % rel

    clave = prueba.get("clave")
    if not isinstance(clave, str) or not clave.strip():
        return False, "clave de deuda vacia"
    ts = _ts_deuda(clave)
    if ts is None:
        return False, "sin entrada en el libro: %s" % clave
    if ts <= desde:
        return False, "la entrada no se toco: %s" % clave
    return True, "anotado en el libro: %s" % clave


def main(argv):
    prueba, desde = None, None
    if "--prueba" in argv:
        crudo = argv[argv.index("--prueba") + 1] if argv.index("--prueba") + 1 < len(argv) else ""
        try:
            prueba = json.loads(crudo)
        except Exception:
            print("prueba ilegible")
            return 1
    if "--desde" in argv:
        desde = argv[argv.index("--desde") + 1] if argv.index("--desde") + 1 < len(argv) else None
    if prueba is None or desde is None:
        print("uso: prueba_entregable.py --prueba '<json>' --desde <epoch>")
        return 2
    ok, motivo = cumple(prueba, desde)
    print(motivo)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
