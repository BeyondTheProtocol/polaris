#!/usr/bin/env python3
"""tests/_entorno.py — un test que NO PUEDE correr lo dice, en vez de fallar.

POR QUÉ EXISTE: el árbol publicable (`tools/publicar.py`) deja fuera tres cosas por
diseño — el contenido (`00_FUENTE-DE-VERDAD`), el estado vivo (`tools/state`) y los
overlays `*.local.json` con los datos personales que los detectores buscan. Sin ellas,
16 baterías fallaban en rojo, y quien clonara el repo veía 40 fallos sin forma de saber
que 24 son deliberados y el resto es «te falta el entorno».

Un rojo dice «esto está roto». Un SKIP con su motivo dice «esto necesita X», que es la
verdad. La diferencia importa cuando la suite es la carta de presentación del repo.

CONVENCIÓN: rc=77 = saltado (la de autotools). `tests/test_all.sh` lo muestra aparte y
no lo cuenta como fallo. En la casa base ninguna de estas guardas salta nunca: todo está,
así que allí los tests corren exactamente igual que antes.
"""
import os
import sys

RAIZ = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = 77


def _hay_contenido():
    return os.path.isdir(os.path.join(RAIZ, "00_FUENTE-DE-VERDAD"))


def _hay_estado():
    """El estado VIVO del lazo, no un directorio con ese nombre.

    Mirar solo si existe `tools/state/` daba un efecto de ORDEN: un test anterior de la
    batería lo crea al ejecutarse, y para cuando le tocaba a `test_healthcheck` el
    predicado ya decía que sí — el test no saltaba y se ponía rojo. El estado vivo del
    lazo 24/7 solo existe donde corre el lazo, así que va atado a la casa base.
    """
    if not _es_casa_base():
        return False
    return os.path.isdir(os.environ.get("BTP_STATE_DIR")
                         or os.path.join(RAIZ, "tools", "state"))


# Casa base, para los overlays. Son ficheros gitignored (contienen nombres de terceros, marcas
# del titular y cifras del caso) y por eso NO viajan a un worktree. Consecuencia medida el
# 20-sep-2026: en cualquier rama se saltaban 24 baterías enteras, las del muro incluidas, sin
# que nadie lo notara — y una rama es justo donde se prueba el código nuevo. Mirar también en
# casa base no republica nada y devuelve la cobertura donde hace falta.
_CASA_BASE = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")


def _hay_overlay(rel):
    """El overlay existe si está en ESTE repo o en casa base. No lo copia: solo lo encuentra.

    Los módulos que los leen resuelven su ruta por su cuenta; si alguno solo mira su propio
    repo, el test correrá y fallará, que es exactamente lo que queremos saber."""
    return (os.path.isfile(os.path.join(RAIZ, rel))
            or os.path.isfile(os.path.join(_CASA_BASE, rel)))


def _sin_halt():
    """¿El lazo está en marcha? El HALT es pausa TOTAL: nada sale, todo se aplaza.

    13 baterías comprobaban entrega, egress o reintentos y se ponían ROJAS solo porque el
    sistema estaba parado — medido el 17-sep-2026 redirigiendo `BTP_HALT_FILES`: 24 rojos
    con HALT, 11 sin él. Ese rojo no era deuda: enmascaraba la deuda real y hacía imposible
    saber, de un vistazo, si la batería estaba sana.
    """
    ficheros = (os.environ["BTP_HALT_FILES"].split(":")
                if os.environ.get("BTP_HALT_FILES")
                else [os.path.expanduser("~/.btp.HALT"), os.path.join(RAIZ, ".HALT")])
    return not any(os.path.exists(f) for f in ficheros)


def _es_casa_base():
    """El muro mira SIEMPRE `~/claudecode` (muro_guard.py), nunca un repo arbitrario."""
    return os.path.realpath(RAIZ) == os.path.realpath(os.path.expanduser("~/claudecode"))


REQUISITOS = {
    "sin-halt": (_sin_halt, "el lazo en marcha: hay un HALT activo y el sistema está en pausa total"),
    "contenido": (_hay_contenido, "la fuente de verdad (`00_FUENTE-DE-VERDAD/`), que no se publica"),
    "estado": (_hay_estado, "el estado vivo del lazo (`tools/state/`), que no se publica"),
    "casa-base": (_es_casa_base, "estar en `~/claudecode`: el muro mira esa ruta, no un repo cualquiera"),
    "identidad": (lambda: _hay_overlay("tools/identidad.local.json"),
                  "`tools/identidad.local.json`: la lista de marcas del titular que el detector busca"),
    "nombres": (lambda: _hay_overlay("tools/nombres.local.json"),
                "`tools/nombres.local.json`: los nombres de terceros que se redactan"),
    "perfil": (lambda: _hay_overlay("tools/perfil.local.json"),
               "`tools/perfil.local.json`: las dianas y cifras del caso"),
    "zonas": (lambda: _hay_overlay(".claude/hooks/zonas_clinicas.local.json"),
              "`.claude/hooks/zonas_clinicas.local.json`: las carpetas clínicas del caso"),
}


def exige_cola_aislada():
    """ABORTA si la cola que va a tocar el test es la VIVA de casa base.

    20-sep-2026, destrozo real: un banco de mutantes importó `healthcheck` ANTES de que el test
    fijara `BTP_STATE_DIR`, así que `cola` quedó resuelto contra el estado vivo y el fixture
    `9-x-jobB2.json` aterrizó en `tools/state/queue/failed/` de casa base. El healthcheck lo tomó
    por un job real: alerta, investigación encolada (de pago) y un hallazgo escalado en el libro.

    No basta con que el test fije la variable: lo que importa es DÓNDE apunta `cola.QUEUE` cuando
    se escribe, ya se aísle por entorno o parcheando la ruta. Esto lo comprueba al final, que es
    lo único que no se puede falsear. Sale con 1 (rojo), no con SKIP: escribir en la cola viva no
    es «me falta entorno», es un test que ensucia el sistema de verdad."""
    import cola
    viva = os.path.realpath(os.path.join(os.path.expanduser("~/claudecode"), "tools", "state", "queue"))
    if os.path.realpath(cola.QUEUE) == viva:
        print("❌ TEST ABORTADO: cola.QUEUE apunta a la cola VIVA de casa base (%s).\n"
              "   Aísla el estado ANTES de importar nada (BTP_STATE_DIR=<tmp>) o parchea cola.QUEUE."
              % cola.QUEUE, file=sys.stderr)
        sys.exit(1)


def exige(*requisitos):
    """Sale con 77 (SKIP) nombrando lo que falta. No salta si está todo."""
    faltan = []
    for r in requisitos:
        comprueba, porque = REQUISITOS[r]
        if not comprueba():
            faltan.append(porque)
    if not faltan:
        return
    print("⏭️  SKIP: necesita %s" % faltan[0])
    for f in faltan[1:]:
        print("           y %s" % f)
    sys.exit(SKIP)
