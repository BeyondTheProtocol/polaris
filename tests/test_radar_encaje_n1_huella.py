#!/usr/bin/env python3
"""test_radar_encaje_n1_huella.py — el candado N1 del radar mira el CONTENIDO de §1, no solo la fecha.

POR QUÉ EXISTE (26-sep-2026). `candados_n1` solo comparaba la fecha de ESTADO-ACTUAL §1 (cabecera o
«Novedades del …») con `bench_jev.PERFIL_N1_FECHA`. Las novedades entran también como filas «(NUEVO)»
o bloques citados sin esa fórmula: el 26-sep §1 tenía un cambio posterior al perfil, la fecha de §1
seguía en el 13-sep y el radar mandó 3 ensayos a Jev con el perfil sin re-cotejar. Mismo punto ciego
que tuvo `test_perfil_clinico_al_dia` (arreglado ese día con `estado_actual.huella_s1`).

Qué blinda:
  1. fecha al día pero huella de §1 distinta a la sellada → no sale nada y el motivo lo dice;
  2. perfil sin huella sellada (None o sin el atributo) → fail-closed;
  3. ESTADO-ACTUAL ilegible o sin §1 → fail-closed;
  4. huella igual y fecha al día → sale (el candado no bloquea el camino bueno);
  5. la huella cambia con una fila «(NUEVO)» sin «Novedades del …», y la fecha no;
  6. mutante: quitando la comparación de huella, este test se pone rojo.
Offline y sin datos clínicos: huellas falsas y un §1 de juguete.
"""
import inspect
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estado_actual  # noqa: E402
import radar_ned_diario as r  # noqa: E402
from test_radar_encaje_n1 import Borde, Jev, _bench, _cola, get_ok  # noqa: E402

_pass = _fail = 0
HOY = (2026, 9, 13)  # anterior al perfil (21-sep): el candado de fecha deja pasar


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def comprobar(mod):
    fallos = []

    def f(cond, name):
        if not cond:
            fallos.append(name)

    def corre(bench, huella):
        c, jev = _cola(), Jev()
        n, motivo = mod.encaje_n1(c["pendientes"], preguntar=jev, get=get_ok, bench=bench,
                                  borde=Borde(), fecha_clinica=HOY, huella_s1=huella)
        return n, motivo, jev

    # 1. §1 cambió sin mover la fecha
    n, motivo, jev = corre(_bench(huella="aaaaaaaaaaaaaaaa"), "bbbbbbbbbbbbbbbb")
    f(n == 0 and not jev.llamadas, "huella distinta: no se llama a Jev")
    f("§1" in motivo and "cambió" in motivo, "huella distinta: el motivo lo dice")

    # 2. perfil sin huella sellada
    n, motivo, jev = corre(_bench(huella=None), "bbbbbbbbbbbbbbbb")
    f(n == 0 and not jev.llamadas, "perfil sin huella: fail-closed")
    f("huella" in motivo, "perfil sin huella: el motivo lo dice")
    b = _bench()
    del b.PERFIL_N1_HUELLA_S1
    n, _, jev = corre(b, "bbbbbbbbbbbbbbbb")
    f(n == 0 and not jev.llamadas, "bench sin el atributo: fail-closed")

    # 4. camino bueno
    n, motivo, jev = corre(_bench(huella="cccccccccccccccc"), "cccccccccccccccc")
    f(n == 2 and motivo == "ok", "huella igual y fecha al día: puntúa")
    return fallos


def main():
    fallos = comprobar(r)
    for x in fallos:
        ok(False, x)
    ok(not fallos, "el módulo real pasa los casos de huella")

    # 3. ESTADO-ACTUAL ilegible → huella None → candado cerrado (con fecha al día pasada a mano)
    real, estado_actual.ESTADO = estado_actual.ESTADO, "/no/existe/ESTADO-ACTUAL.md"
    try:
        ok(estado_actual.lee_huella_s1()[0] is None, "ESTADO-ACTUAL ausente → huella None")
        ok(not r.candados_n1(bench=_bench(), borde=Borde(), fecha_clinica=HOY)[0],
           "sin ESTADO-ACTUAL legible, el candado de huella queda cerrado")
    finally:
        estado_actual.ESTADO = real

    # 5. el punto ciego exacto: fila «(NUEVO)» sin «Novedades del …»
    antes = "## 1. Clínico (al 11-sep-2026)\n| a | b |\n## 2. Otro\n"
    despues = "## 1. Clínico (al 11-sep-2026)\n| a | b |\n| x (NUEVO) | y |\n## 2. Otro\n"
    ok(estado_actual.fecha_clinica(antes) == estado_actual.fecha_clinica(despues),
       "la fila (NUEVO) no mueve la fecha (por eso hacía falta la huella)")
    ok(estado_actual.huella_s1(antes) != estado_actual.huella_s1(despues), "la fila (NUEVO) sí mueve la huella")
    ok(estado_actual.huella_s1("sin sección") is None, "sin §1 no hay huella")

    # 6. mutante: sin la comparación de huella, el test lo caza
    fuente = inspect.getsource(r)
    viejo = "if huella_s1 != sellada:"
    ok(viejo in fuente, "mutante: la línea existe")
    mut = types.ModuleType("radar_mutante")
    mut.__file__ = r.__file__
    exec(compile(fuente.replace(viejo, "if False:", 1), "radar_mutante", "exec"), mut.__dict__)
    ok(bool(comprobar(mut)), "mutante «sin comparar huella»: el test lo caza")

    print(f"test_radar_encaje_n1_huella: {_pass} ok, {_fail} fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
