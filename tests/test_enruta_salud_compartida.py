#!/usr/bin/env python3
"""test_enruta_salud_compartida.py — UNA caché de salud de `enruta` para todos los árboles.

POR QUÉ EXISTE (25-sep-2026). La caché de salud (`enruta_salud.json`) y su candado colgaban del
`tools/` de cada árbol, así que cada worktree sondeaba por su cuenta a grok, perplexity, chatgpt y
gemini al caducar SU caché de 1 h. Con ~20 worktrees vivos salieron 91 rondas en 8,5 h (15-18 por
hora en punta), casi todo el dinero real de API de ese día según el registro completo de
cost_guard. Y la sonda de grok buscaba en vivo para contestar «OK» (~2.000 tokens por ronda).

Lo que vigila:
  1. Desde cualquier árbol, la caché y el candado son los de CASA BASE.
  2. `BTP_STATE_DIR` los aísla; un proceso que es un test va a un tmp (nunca a la caché real).
  3. Un test no lanza sondas de pago reales de fondo.
  4. Dos refrescos seguidos → un solo lanzamiento (el candado compartido hace su trabajo).
  5. La sonda de grok va con `--nolive`; las demás, sin flags nuevos.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _rutas(entorno):
    env = {k: v for k, v in os.environ.items() if k not in ("BTP_STATE_DIR", "BTP_REPO")}
    env.update(entorno)
    r = subprocess.run([sys.executable, "-c", "import sys;sys.path.insert(0,%r);import enruta;"
                        "print(enruta.CACHE_SALUD);print(enruta.LOCK_REFRESCO)" % TOOLS],
                       capture_output=True, text=True, env=env)
    return r.stdout.split()


def main():
    import _casa
    casa_state = os.path.join(_casa.casa_base(), "tools", "state")

    # 1. Una tool normal, lanzada desde este árbol (que puede ser un worktree): casa base.
    r = _rutas({})
    check("la caché de salud es la de casa base (%s)" % (r[:1],),
          r[:1] == [os.path.join(casa_state, "enruta_salud.json")])
    check("…y el candado de refresco también", r[1:2] == [os.path.join(casa_state, "enruta_salud.refrescando")])
    check("la caché NO cuelga del árbol desde el que se llama",
          not r[0].startswith(os.path.join(TOOLS, "state")) or os.path.join(TOOLS, "state") == casa_state)

    # 2. BTP_STATE_DIR aísla.
    tmp = tempfile.mkdtemp(prefix="enruta_salud_")
    r = _rutas({"BTP_STATE_DIR": tmp})
    check("con BTP_STATE_DIR, la caché va al tmp", r[:1] == [os.path.join(tmp, "enruta_salud.json")])

    # 3. Este proceso ES un test: sin BTP_STATE_DIR, jamás la caché real.
    os.environ.pop("BTP_STATE_DIR", None)
    import enruta
    check("desde un tests/test_*.py, la caché no es la real",
          enruta.CACHE_SALUD != os.path.join(casa_state, "enruta_salud.json")
          and "btp-test-enruta" in enruta.CACHE_SALUD)
    lanzados = []
    popen_real = subprocess.Popen
    enruta.subprocess.Popen = lambda *a, **k: lanzados.append(a)
    try:
        enruta._refrescar_de_fondo()
        check("un test no lanza sondas de pago reales de fondo", lanzados == [])

        # 4. Con estado aislado (no es «el real»), el candado deja un solo refresco en vuelo.
        os.environ["BTP_STATE_DIR"] = tmp
        enruta.LOCK_REFRESCO = os.path.join(tmp, "enruta_salud.refrescando")
        if os.path.exists(enruta.LOCK_REFRESCO):
            os.remove(enruta.LOCK_REFRESCO)
        enruta._refrescar_de_fondo()
        enruta._refrescar_de_fondo()
        check("dos refrescos seguidos → UN solo lanzamiento (%d)" % len(lanzados), len(lanzados) == 1)
    finally:
        enruta.subprocess.Popen = popen_real
        os.environ.pop("BTP_STATE_DIR", None)

    # 5. La sonda de grok, sin búsqueda en vivo; las demás, como estaban.
    vistos = {}
    run_real = enruta.subprocess.run

    def falso_run(args, **kw):
        vistos[os.path.basename(args[1])] = list(args[2:])
        return subprocess.CompletedProcess(args, 0, stdout="OK\n", stderr="")

    enruta.subprocess.run = falso_run
    try:
        for n in ("grok", "perplexity"):
            if (enruta.PROVEEDORES.get(n) or {}).get("tool"):
                enruta.probar(n)
    finally:
        enruta.subprocess.run = run_real
    check("la sonda de grok lleva --nolive (%s)" % vistos.get("grok.py"),
          vistos.get("grok.py") == ["--nolive", "Responde solo: OK"])
    check("la de perplexity no recibe flags nuevos (%s)" % vistos.get("perplexity.py"),
          vistos.get("perplexity.py") == ["Responde solo: OK"])

    print("test_enruta_salud_compartida: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
