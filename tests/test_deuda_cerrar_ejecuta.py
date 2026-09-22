#!/usr/bin/env python3
"""test_deuda_cerrar_ejecuta.py — «cerrado» exige que el test PASE, no solo que exista.

POR QUÉ EXISTE (13-sep-2026, hallazgo `deuda-cerrar-no-verifica-test-pasa`, detectado 3 veces).
`_test_valido()` comprobaba dos cosas —que el fichero existiera y que su nombre apareciera en
`test_all.sh`— y **nunca lo ejecutaba**. Así que se podía cerrar un hallazgo con un test en ROJO.

Eso no es un detalle: «cerrado» es la palabra con la que este libro afirma que un fallo **no puede
volver sin que nos enteremos**. Si esa palabra se puede poner sin ejecutar nada, R1 es decorativa y
el libro entero deja de significar lo que dice. Y encima en silencio, que es como fallan las cosas
que más duelen aquí.

Fail-closed en los tres modos de no-saber: test que no existe, test que existe pero está rojo, y
test que no termina a tiempo. Ninguno cierra.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="deuda_cierra_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = ROOT
import deuda  # noqa: E402

deuda.STATE = _TMP
deuda.LIBRO = os.path.join(_TMP, "deuda.json")

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _escribe_test(nombre, cuerpo):
    """Crea un test de mentira en tests/ y lo engancha a test_all.sh; devuelve (ruta_rel, limpiar)."""
    ruta = os.path.join(ROOT, "tests", nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(cuerpo)
    ta = os.path.join(ROOT, "tests", "test_all.sh")
    original = open(ta, encoding="utf-8").read()
    with open(ta, "w", encoding="utf-8") as f:
        f.write(original + "\n# temporal de test_deuda_cerrar_ejecuta\nrunpy %s\n" % nombre)

    def limpiar():
        try:
            os.remove(ruta)
        except OSError:
            pass
        with open(ta, "w", encoding="utf-8") as f2:
            f2.write(original)
    return "tests/" + nombre, limpiar


def main():
    # --- 1. Un test que EXISTE, está enganchado, y está en ROJO: NO cierra ---
    rel, limpiar = _escribe_test("test_zz_falso_rojo_BORRAR.py",
                                 "import sys\nprint('fallo a proposito')\nsys.exit(1)\n")
    try:
        deuda.abrir("prueba-rojo", "x")
        okc, motivo = deuda.cerrar("prueba-rojo", rel)
        ok(okc is False, "un test en ROJO no cierra la deuda")
        ok("ROJO" in (motivo or ""), "y el motivo lo dice claro (%s)" % (motivo or "")[:60])
        ok(deuda._cargar()["prueba-rojo"]["estado"] != "cerrado", "la deuda sigue sin cerrar")
    finally:
        limpiar()

    # --- 2. El mismo test, pero en VERDE: sí cierra ---
    rel, limpiar = _escribe_test("test_zz_falso_verde_BORRAR.py",
                                 "print('todo bien')\n")
    try:
        deuda.abrir("prueba-verde", "x")
        okc, _ = deuda.cerrar("prueba-verde", rel)
        ok(okc is True, "un test en VERDE sí cierra")
        it = deuda._cargar()["prueba-verde"]
        ok(it["estado"] == "cerrado" and it.get("test") == rel, "queda cerrado y con su test")
    finally:
        limpiar()

    # --- 3. Un test que NO TERMINA no cierra: no se puede cerrar lo que no se comprobó ---
    rel, limpiar = _escribe_test("test_zz_falso_colgado_BORRAR.py",
                                 "import time\ntime.sleep(30)\n")
    try:
        os.environ["BTP_DEUDA_TEST_TIMEOUT"] = "2"
        import importlib
        importlib.reload(deuda)
        deuda.STATE, deuda.LIBRO = _TMP, os.path.join(_TMP, "deuda.json")
        deuda.abrir("prueba-colgado", "x")
        okc, motivo = deuda.cerrar("prueba-colgado", rel)
        ok(okc is False, "un test que no termina no cierra")
        ok("no terminó" in (motivo or ""), "y lo dice (%s)" % (motivo or "")[:60])
    finally:
        os.environ.pop("BTP_DEUDA_TEST_TIMEOUT", None)
        limpiar()
        import importlib
        importlib.reload(deuda)
        deuda.STATE, deuda.LIBRO = _TMP, os.path.join(_TMP, "deuda.json")

    # --- 4. R1 sigue entera: sin test, o con uno que nadie corre, tampoco ---
    deuda.abrir("prueba-sin-test", "x")
    ok(deuda.cerrar("prueba-sin-test", None)[0] is False, "sin test no cierra (R1)")
    ok(deuda.cerrar("prueba-sin-test", "tests/test_que_no_existe.py")[0] is False,
       "con un test inexistente tampoco")

    rel2, limpiar2 = _escribe_test("test_zz_huerfano_BORRAR.py", "print('ok')\n")
    try:
        # lo desenganchamos de test_all.sh: existe y pasa, pero nadie lo corre
        ta = os.path.join(ROOT, "tests", "test_all.sh")
        cuerpo = open(ta, encoding="utf-8").read().replace("runpy test_zz_huerfano_BORRAR.py\n", "")
        open(ta, "w", encoding="utf-8").write(cuerpo)
        ok(deuda.cerrar("prueba-sin-test", rel2)[0] is False,
           "un test que pasa pero que NADIE corre tampoco cierra")
    finally:
        limpiar2()

    # --- 5. Un test que se cierra A SÍ MISMO termina en vez de multiplicarse (13-sep-2026) ---
    # Es el fallo que agotó los procesos del mini: verificar el test lo ejecutaba, él llamaba a
    # `cerrar()` consigo mismo, y así sin fondo. Con el freno, el segundo nivel se niega y todo acaba.
    # El plazo corto es la red por si el freno se rompe: entonces el caso 6 mata el grupo entero.
    nombre = "test_zz_bucle_BORRAR.py"
    rel, limpiar = _escribe_test(nombre, (
        "import os, sys\n"
        "sys.path.insert(0, %r)\n"
        "import deuda\n"
        "deuda.abrir('bucle', 'x')\n"
        "ok, _ = deuda.cerrar('bucle', 'tests/%s')\n"
        "sys.exit(0 if ok else 1)\n") % (os.path.join(ROOT, "tools"), nombre))
    try:
        os.environ["BTP_DEUDA_TEST_TIMEOUT"] = "20"
        import importlib
        importlib.reload(deuda)
        deuda.STATE, deuda.LIBRO = _TMP, os.path.join(_TMP, "deuda.json")
        import time as _t
        t0 = _t.time()
        deuda.abrir("prueba-bucle", "x")
        okc, motivo = deuda.cerrar("prueba-bucle", rel)
        ok(okc is False, "un test que se cierra a sí mismo NO cierra (%s)" % (motivo or "")[:60])
        ok(_t.time() - t0 < 15, "y termina solo, sin llegar al plazo (%.1fs)" % (_t.time() - t0))
    finally:
        os.environ.pop("BTP_DEUDA_TEST_TIMEOUT", None)
        limpiar()

    # --- 6. Al vencer el plazo muere el GRUPO entero, no solo el hijo (13-sep-2026) ---
    # `subprocess.run(timeout=…)` solo mata al hijo directo: los nietos quedaban huérfanos y vivos.
    pidfile = os.path.join(_TMP, "nieto.pid")
    rel, limpiar = _escribe_test("test_zz_con_nieto_BORRAR.py", (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(%r, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n") % pidfile)
    try:
        os.environ["BTP_DEUDA_TEST_TIMEOUT"] = "3"
        import importlib
        importlib.reload(deuda)
        deuda.STATE, deuda.LIBRO = _TMP, os.path.join(_TMP, "deuda.json")
        deuda.abrir("prueba-nieto", "x")
        okc, _ = deuda.cerrar("prueba-nieto", rel)
        ok(okc is False, "un test colgado con un nieto no cierra")
        vivo = True
        try:
            pid = int(open(pidfile).read().strip())
            import time as _t
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    vivo = False
                    break
                _t.sleep(0.1)
        except (OSError, ValueError):
            vivo = True                  # sin pid no se puede afirmar que murió
        ok(not vivo, "el NIETO también muere al vencer el plazo (no queda huérfano)")
    finally:
        os.environ.pop("BTP_DEUDA_TEST_TIMEOUT", None)
        limpiar()
        import importlib
        importlib.reload(deuda)
        deuda.STATE, deuda.LIBRO = _TMP, os.path.join(_TMP, "deuda.json")

    print("RESULTADO deuda_cerrar_ejecuta: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CERRAR EJECUTA EL TEST — EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
