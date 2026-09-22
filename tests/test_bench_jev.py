#!/usr/bin/env python3
"""test_bench_jev.py — el filtro del muro de `bench_jev.py` no deja salir lo que el borde no caza.

21-sep-26: el set dorado «de-identificado» conservaba una fecha con pinta de nacimiento junto a
un correo del hospital y @handles de terceros, y `borde.clasificar` lo daba por limpio. Jev es un
tercero en EE. UU. con retención sin plazo: lo que salga, se queda. Este test fija que el filtro
extra bloquea esas clases, que el borde sigue mandando, y que si revienta no sale nada. Offline.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bench_jev as b  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    sale = lambda t: b.puede_salir(t)[0]
    check("fecha dd/mm/aaaa no sale", not sale("Informe de [NOMBRE] (03/04/1985) listo"))
    check("fecha con guiones no sale", not sale("cita el 3-4-85 en consulta"))
    check("@handle no sale", not sale("señal en X (@alguien_real) sobre inferencia"))
    check("URL no sale", not sale("mira https://ejemplo.org/x"))
    check("número largo no sale", not sale("expediente 1234567"))
    check("email no sale (borde)", not sale("escribe a fulano@ejemplo.com"))
    check("texto neutro sí sale", sale("Recordatorio: llamar al fontanero el martes"))
    orig = b.borde.clasificar
    b.borde.clasificar = lambda t: (_ for _ in ()).throw(RuntimeError("x"))
    try:
        check("si el borde revienta, no sale", not sale("texto neutro"))
    finally:
        b.borde.clasificar = orig
    # Modo --n1: el perfil que sale no lleva nada raro que la identifique, y sin trust no sale nada.
    perfil = b.PERFIL_N1.lower()
    check("perfil N1 sin términos vetados", not any(v in perfil for v in b._VETADAS_N1))
    check("perfil N1 sin identificador directo", not b.borde.identificador_directo(b.PERFIL_N1)[0])
    llamado = []
    orig_t, orig_p = b.borde.es_trusted, b.preguntar
    b.borde.es_trusted = lambda d: False
    b.preguntar = lambda *a, **k: llamado.append(1)
    try:
        try:
            b.bench_n1(limite=1)
            aborta = False
        except SystemExit:
            aborta = True
        check("sin trust-cloud, --n1 aborta sin llamar a Jev", aborta and not llamado)
    finally:
        b.borde.es_trusted, b.preguntar = orig_t, orig_p
    check("no hay modo crudo", not hasattr(b, "_casos_crudos") and "--crudo" not in open(b.__file__).read())
    print("test_bench_jev: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
