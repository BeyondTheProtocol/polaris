#!/usr/bin/env python3
"""Un hook que no arranca NO es un «deny»: es la herramienta rota, y se dice.

POR QUÉ (20-sep-2026). El rodaje del guard clínico guardaba la copia del hook viejo en el
scratchpad de la sesión. Al reiniciarse la sesión el scratchpad se vació, Python salió con
rc≠0 al no encontrar el fichero, y `juzga()` leyó ese rc como DENY. Resultado: el rodaje se
puso 🔴 con cinco «relajaciones» que no existían, y la revisión automática del día siguiente
habría vetado la fusión por nada.

El reverso es peor: si el que falta es el hook NUEVO, todo sale «DENY» — o sea «endurece», que
es la dirección que nadie mira con lupa — y el veredicto sería un 🟢 falso. Un vigía tiene que
saber cuándo no está mirando.
"""
import json
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import replay_guard as RG      # noqa: E402
import rodaje_muro as RM       # noqa: E402

HOOK = os.path.join(RAIZ, ".claude", "hooks", "clinico_guard.py")
fallos = 0


def ok(cond, desc, detalle=""):
    global fallos
    if not cond:
        fallos += 1
    print("  %s %s%s" % ("✅" if cond else "❌ MAL", desc, ("  — " + detalle) if not cond else ""))


env = RG._entorno(tempfile.mkdtemp(prefix="replay-test-"))

# 1) juzgar con un hook que no existe NO puede devolver un veredicto
try:
    v = RG.juzga(os.path.join(tempfile.mkdtemp(), "no_existe.py"), env, "git status")
    ok(False, "juzgar con un hook ausente avisa en vez de devolver veredicto",
       "devolvió %r (el bug: se leía como DENY)" % (v,))
except RG.HookRoto:
    ok(True, "juzgar con un hook ausente avisa en vez de devolver veredicto")

# 2) un hook que existe pero no compila tampoco cuela
roto = os.path.join(tempfile.mkdtemp(), "clinico_guard.py")
with open(roto, "w", encoding="utf-8") as fh:
    fh.write("def (:\n")
try:
    RG.juzga(roto, env, "git status")
    ok(False, "un hook con la sintaxis rota avisa", "devolvió un veredicto")
except RG.HookRoto:
    ok(True, "un hook con la sintaxis rota avisa")

# 3) `comprueba` acepta el hook de verdad y rechaza el que falta
try:
    RG.comprueba(HOOK)
    ok(True, "`comprueba` da por bueno el hook real")
except RG.HookRoto as ex:
    ok(False, "`comprueba` da por bueno el hook real", repr(ex))
try:
    RG.comprueba("/no/existe/clinico_guard.py")
    ok(False, "`comprueba` rechaza el que falta", "lo dio por bueno")
except RG.HookRoto:
    ok(True, "`comprueba` rechaza el que falta")

# 4) un hook en un temporal ya vale: `iniciar` lo COPIA a un sitio fijo (25-sep), y la copia
#    sobrevive a que borren el temporal. Antes se vetaba porque el rodaje leía el original.
efimero = os.path.join(tempfile.mkdtemp(dir="/tmp"), "clinico_guard.py")
os.makedirs(os.path.dirname(efimero), exist_ok=True)
with open(efimero, "w", encoding="utf-8") as fh:
    fh.write("import sys\n")


class A:
    nuevo = HOOK
    viejo = efimero
    horas = 24
    nota = "test"


guardado, RM.ESTADO = RM.ESTADO, os.path.join(tempfile.mkdtemp(), "rodaje.json")
try:
    rc = RM.iniciar(A())
    ok(rc == 0, "el rodaje acepta un hook en un temporal (lo copia)", "devolvió rc=%s" % rc)
    with open(RM.ESTADO, encoding="utf-8") as fh:
        est = json.load(fh)
    ok(not est["hook_viejo"].startswith(os.path.dirname(efimero)),
       "y juzga con la copia, no con el temporal", est["hook_viejo"])
    shutil.rmtree(os.path.dirname(efimero), ignore_errors=True)
    ok(RM._no_puede_juzgar(est) == "", "borrado el temporal, la copia sigue sirviendo",
       RM._no_puede_juzgar(est))
finally:
    RM.ESTADO = guardado

print()
print("test_replay_hook_roto: %d fallos" % fallos)
raise SystemExit(1 if fallos else 0)
