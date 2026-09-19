#!/usr/bin/env python3
"""test_seguimiento_carrera.py — dos escritores concurrentes no se pisan.

`_write_atomic` es atómico para la ESCRITURA, no para leer→modificar→escribir. Sin candado,
el caso real es: {{TITULAR}} mueve una tarjeta en el Tablero (el handler HTTP del Observatorio llama
a `set_estado`) mientras el poller de correo registra un hilo con `add_hilo`. Los dos leen el
mismo JSON y el segundo `os.replace` machaca al primero. Sin excepción, sin log, sin reintento:
la tarjeta vuelve a su sitio sola y nadie se entera.

El test lanza PROCESOS de verdad (no hilos): el candado es entre procesos, que es donde vive
la carrera — daemons distintos, no threads del mismo intérprete.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


ESCRITOR = r'''
import os, sys
sys.path.insert(0, %(tools)r)
import seguimiento as s
i = int(sys.argv[1])
# Lectura lenta a propósito: ensancha la ventana entre load y write, que es donde vive
# la carrera. Sin candado, cada proceso escribe el JSON que leyó y borra a los demás.
s.add_hilo({"titulo": "hilo concurrente %%d" %% i, "categoria": "otros",
            "estado": "en_curso", "origen": "manual"})
'''


def main():
    tmp = tempfile.mkdtemp(prefix="carrera_")
    state = os.path.join(tmp, "state")
    os.makedirs(state, exist_ok=True)
    env = dict(os.environ)
    env["BTP_STATE_DIR"] = state
    env["BTP_REPO"] = tmp
    json.dump({"hilos": []}, open(os.path.join(state, "seguimiento.json"), "w"))

    script = os.path.join(tmp, "escritor.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(ESCRITOR % {"tools": os.path.join(ROOT, "tools")})

    N = 12
    procs = [subprocess.Popen([sys.executable, script, str(i)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             for i in range(N)]
    errs = []
    for p in procs:
        _, e = p.communicate(timeout=120)
        if e:
            errs.append(e.decode("utf-8", "ignore"))

    seg = json.load(open(os.path.join(state, "seguimiento.json"), encoding="utf-8"))
    hilos = seg.get("hilos", [])
    titulos = {h.get("titulo") for h in hilos}

    # Lo que se mide es SUPERVIVENCIA: sin candado, N procesos concurrentes dejan
    # típicamente 1-3 hilos de los N. Con candado tienen que sobrevivir los N.
    check("sobreviven los %d hilos concurrentes (hay %d)" % (N, len(hilos)), len(hilos) == N)
    faltan = [i for i in range(N) if ("hilo concurrente %d" % i) not in titulos]
    check("no falta ninguno (faltan: %s)" % faltan, not faltan)
    check("ningún escritor reventó", not [e for e in errs if "Traceback" in e])
    check("sin .tmp colgando", not os.path.exists(os.path.join(state, "seguimiento.json.tmp")))

    # Reentrancia: el candado es un mkdir, así que una función decorada que llame a otra
    # decorada se bloquearía contra sí misma si no llevara contador de profundidad.
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    os.environ["BTP_STATE_DIR"] = state
    os.environ["BTP_REPO"] = tmp
    import importlib
    import seguimiento as s
    importlib.reload(s)
    with s._seg_lock():
        with s._seg_lock():
            hid = s.add_hilo({"titulo": "anidado", "estado": "en_curso", "origen": "manual"})
    check("el candado es reentrante (no se bloquea contra sí mismo)", bool(hid))
    check("profundidad vuelve a 0", s._SEG_LOCK_DEPTH == 0)

    print("RESULTADO carrera seguimiento: %d OK, %d fallos" % (_pass, _fail))
    print("✅ ESCRITURA SERIALIZADA" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
