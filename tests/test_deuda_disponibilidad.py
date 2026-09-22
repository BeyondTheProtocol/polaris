#!/usr/bin/env python3
"""test_deuda_disponibilidad.py — R5: la caída de un TERCERO no es un bug nuestro.

POR QUÉ EXISTE (12-sep-2026). La suite llevaba semanas roja con 24 hallazgos escalados, y 8 eran
`ia_caida_*` (Claude, Grok, OpenAI, Gemini, GLM, NVIDIA, Perplexity, Undermind) con el mismo
detalle, «nodename nor servname»: UNA pasada del healthcheck sin red. Ese día `ia_health` daba
HTTP 200 en las nueve. Y R4 ya las había marcado `intermitente`, que es terminal, así que iban a
teñir la suite de rojo para siempre — y una suite que siempre escuece deja de escocer, que es
justo lo que el libro de deuda existe para evitar.

El error era de categoría, no de criterio. R4 acierta con un bug propio que va y viene: eso es un
bug con disfraz. Pero una API ajena que cae y vuelve no va disfrazada, y no la arregla ningún
commit nuestro. Lo único que sí podemos garantizar, y lo único que importa para NED, es que su
caída no nos pare ni nos abra: eso lo fija `tests/test_enruta.py`.

Este test protege las dos mitades: que la excepción exista, y que NO sea una puerta trasera para
callar bugs propios.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="deuda_disp_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = ROOT          # para que `cerrar --test` encuentre tests/ y test_all.sh
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


def main():
    TEST_REAL = "tests/test_enruta.py"   # existe y está en test_all.sh

    # --- 1. Un tercero que cae y vuelve NO asciende a intermitente ---
    deuda.abrir("ia_caida_ejemplo", "la IA Ejemplo no responde")
    for _ in range(5):
        deuda.visto("ia_caida_ejemplo")
        deuda.remitir("ia_caida_ejemplo", "volvió a responder")
    est = deuda._cargar()["ia_caida_ejemplo"]
    ok(est["estado"] == "remitido", "tras 5 idas y venidas sigue 'remitido', no 'intermitente'")
    ok(est.get("remisiones", 0) >= 5, "las remisiones se siguen contando (no se ocultan)")

    # --- 2. Un bug NUESTRO conserva R4 intacta: a la 3ª se queda intermitente para siempre ---
    deuda.abrir("bug-nuestro-que-va-y-viene", "algo nuestro falla a ratos")
    for _ in range(3):
        deuda.visto("bug-nuestro-que-va-y-viene")
        deuda.remitir("bug-nuestro-que-va-y-viene", "ya no se ve")
    est2 = deuda._cargar()["bug-nuestro-que-va-y-viene"]
    ok(est2["estado"] == "intermitente", "R4 sigue entera para un bug propio (3ª → intermitente)")
    okc, _ = deuda.remitir("bug-nuestro-que-va-y-viene", "otra vez")
    ok(okc is False, "y un bug propio intermitente YA NO se puede callar (sin puerta trasera)")

    # --- 3. R1 no se toca: cerrar sigue exigiendo un test que exista y esté en test_all.sh ---
    okc, msg = deuda.cerrar("ia_caida_ejemplo", "tests/test_que_no_existe.py")
    ok(okc is False, "cerrar con un test inexistente sigue fallando, también en disponibilidad")
    okc, msg = deuda.cerrar("ia_caida_ejemplo", TEST_REAL)
    ok(okc is True, "cerrar con el test de degradación sí vale (%s)" % msg)

    # --- 4. Cerrado el de disponibilidad, que el tercero vuelva a caer NO es regresión ---
    deuda.visto("ia_caida_ejemplo", "otra caída del proveedor")
    est3 = deuda._cargar()["ia_caida_ejemplo"]
    ok(est3["estado"] == "cerrado", "el proveedor cae otra vez y NO reabre (no es culpa nuestra)")
    ok(est3.get("test") == TEST_REAL, "y no se borra el test que garantiza la degradación")
    ok(est3.get("caidas_tras_cierre", 0) == 1, "pero la caída queda contada, no se pierde")
    ok(est3.get("regresion") is not True, "no se marca como regresión")

    # --- 5. Un bug NUESTRO cerrado que vuelve SÍ es regresión, y se reabre sin test ---
    deuda.abrir("bug-nuestro-cerrado", "x")
    deuda.cerrar("bug-nuestro-cerrado", TEST_REAL)
    deuda.visto("bug-nuestro-cerrado", "ha vuelto")
    est4 = deuda._cargar()["bug-nuestro-cerrado"]
    ok(est4["estado"] == "abierto" and est4.get("regresion") is True,
       "un bug propio que vuelve tras cerrarse SIGUE siendo regresión y se reabre")
    ok(est4.get("test") is None, "y pierde el test, como debe")

    # --- 6. La lista es cerrada y vive en el código: no hay flag que la amplíe ---
    ok(deuda._es_disponibilidad("ia_caida_loquesea") is True, "prefijo reconocido")
    ok(deuda._es_disponibilidad("red_sin_dns") is True,
       "la red del Mac sin DNS es disponibilidad ajena (router/operador, no nuestro código)")
    ok(deuda._es_disponibilidad("red_cualquier_otra_cosa") is False,
       "pero solo la clave exacta: `red_` no es un comodín para callar lo que sea")
    ok(deuda._es_disponibilidad("daemon_fallando:com.btp.x") is False,
       "un daemon NUESTRO no cuela como disponibilidad ajena")
    ok(deuda._es_disponibilidad("correo_smtp_inalcanzable") is False,
       "nuestro SMTP tampoco: la configuración es nuestra, no del proveedor")

    print("RESULTADO deuda_disponibilidad: %d OK, %d fallos" % (_pass, _fail))
    print("✅ DEUDA/DISPONIBILIDAD EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
