#!/usr/bin/env python3
"""test_bench_modelos.py — las piezas de `bench_modelos.py` que, si fallan, dan cifras falsas.

25-sep-26 (P5, idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del
25-sep-2026). Fija: partición estratificada y reproducible, umbral elegido solo con calibración,
abstención que no cuenta como acierto, alternativas «a|b» del router, freno de memoria
fail-closed para MLX y que a disco no va ningún texto. Offline: no carga modelos.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bench_modelos as b  # noqa: E402
import modelo_mlx as mm  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    casos = [{"gold": "tarea"}] * 10 + [{"gold": "no"}] * 4
    cal, test = b._partir(casos)
    check("partición reproducible", (cal, test) == b._partir(casos))
    check("partición sin solape y completa", not set(cal) & set(test) and len(cal) + len(test) == 14)
    check("partición estratificada", sum(1 for i in cal if casos[i]["gold"] == "no") == 2)

    check("abstención no es acierto", not b._ok(None, "no"))
    check("router admite alternativas", b._ok("panel", "comite|panel") and not b._ok("llm", "comite|panel"))

    res = [{"pred": "a", "conf": c, "ok": ok} for c, ok in
           [(0.99, True), (0.95, True), (0.9, True), (0.8, False), (0.7, False)]]
    u = b._umbral(res)
    check("umbral deja fuera la zona de fallos", u == 0.9)
    r = b._resumen(res, u)
    check("resumen con umbral: cobertura y acierto", abs(r["cobertura"] - 0.6) < 1e-9 and r["acierto_al_decidir"] == 1.0)
    check("fallo confiado se cuenta", b._resumen([{"pred": "a", "conf": 0.95, "ok": False}])["fallos_confiados"] == 1)

    m = b.MLX("no-existe/modelo-de-prueba")
    check("MLX sin pesos → tamaño None", m.tamano_gb() is None)
    try:
        b._freno(m, forzar=False)
        bloquea = False
    except SystemExit:
        bloquea = True
    check("freno MLX fail-closed sin pesos", bloquea)

    # Cascada: el modelo solo entra donde el determinista se abstiene, y «no» exige ≥ 0,95.
    # 25-sep-26: el único fallo confiado de Qwen3.5-9B fue tirar un correo oncológico al 91 %.
    import triage_tareas as tt
    orig_cl = tt.clasificar
    tt.clasificar = lambda t, origen="chat": {"veredicto": {"d1": "tarea"}.get(t, "dudosa")}
    try:
        cs = [{"texto": "d1", "gold": "tarea"}, {"texto": "m1", "gold": "tarea"},
              {"texto": "m2", "gold": "tarea"}, {"texto": "m3", "gold": "no"}]
        rs = [{"pred": "no", "conf": 0.99, "ok": False},      # det manda: el modelo no pisa
              {"pred": "no", "conf": 0.91, "ok": False},      # «no» al 91 %: se abstiene
              {"pred": "tarea", "conf": 0.90, "ok": True},    # «tarea» al 90 %: decide
              {"pred": "tarea", "conf": 0.50, "ok": False}]   # bajo umbral: se abstiene
        c = b._cascada(cs, rs, range(4), 0.84)
        check("cascada: el determinista manda donde decide", c["acierto_al_decidir"] == 1.0)
        check("cascada: «no» al 91 % se abstiene (umbral asimétrico)", c["cobertura"] == 0.5)
        check("cascada: 0 fallos del modelo", c["fallos_del_modelo"] == 0)
    finally:
        tt.clasificar = orig_cl

    # 25-sep-26: el vigía solo miraba % libre y el proceso llegó a 11 GB con 13,4 GB de swap.
    class MLXFalso(b.MLX):
        def __init__(self, ahora):
            b.MLX.__init__(self, "falso/x")
            self._ahora = ahora

        def tamano_gb(self):
            return 6.0

        def memoria_ahora_gb(self):
            return self._ahora
    orig_swap = mm._swap_gb
    import score_local as sl
    orig_ml = sl._memoria_libre_gb
    sl._memoria_libre_gb = lambda: 8.0      # el % libre «parece» sano, como aquel día

    def aborta(cand, swaps):
        seq = list(swaps)
        mm._swap_gb = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
        del mm._SWAP_INICIO[:]
        try:
            for _ in swaps:
                b._vigia(cand)
            return False
        except SystemExit:
            return True
    try:
        check("vigía para si el swap crece aunque haya % libre", aborta(MLXFalso(6.5), [5.0, 5.2, 7.0]))
        check("vigía para si MLX pasa de pesos + 2,5 GB", aborta(MLXFalso(11.0), [5.0, 5.0]))
        check("vigía deja seguir si todo está en su sitio", not aborta(MLXFalso(6.5), [5.0, 5.3, 5.4]))
    finally:
        mm._swap_gb, sl._memoria_libre_gb = orig_swap, orig_ml
        del mm._SWAP_INICIO[:]

    # A disco solo agregados: un candidato falso con texto marcado no deja rastro en el JSON.
    class Falso:
        nombre = "falso"

        def decidir(self, tarea, texto, opciones):
            return "directo", 0.9, 1.0
    orig_c, orig_s = dict(b.CASOS), b.SALIDA
    with tempfile.TemporaryDirectory() as d:
        b.SALIDA = d
        b.CASOS["router"] = lambda: [{"texto": "CANARIO-%d-XYZ" % i, "gold": "directo"} for i in range(6)]
        try:
            b.correr(Falso(), ["router"])
            vuelcos = "".join(open(os.path.join(d, f)).read() for f in os.listdir(d))
        finally:
            b.CASOS.clear()
            b.CASOS.update(orig_c)
            b.SALIDA = orig_s
    check("el JSON existe", bool(vuelcos))
    check("ningún texto a disco", "CANARIO" not in vuelcos)

    print("test_bench_modelos: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
