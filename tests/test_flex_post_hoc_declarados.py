#!/usr/bin/env python3
"""tests/test_flex_post_hoc_declarados.py — los post hoc que el red-team del 17-oct-2026 encontró sin
declarar en la vía ambiental del Flex quedan declarados en el código y en una adenda versionada.
Sin datos clínicos: solo lee código y la adenda del repo."""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AMB = os.path.join(RAIZ, "tools", "flex_analisis", "ambiente")
fallos = 0


def ok(c, m):
    global fallos
    print(("  ✓ " if c else "  ✗ ") + m)
    fallos += 0 if c else 1


fin = open(os.path.join(AMB, "_amb_final.py"), encoding="utf-8").read()
i_ph = fin.find('POST_HOC.append("VEREDICTO_V4_EXCLUYE_CRUDA')
i_val = fin.find('validos = [m for m in ms if m != "cruda"')
ok(i_ph != -1, "_amb_final.py declara VEREDICTO_V4_EXCLUYE_CRUDA en POST_HOC")
ok(-1 < i_ph < i_val, "la declaración va antes de excluir la cruda del veredicto")

ad = json.load(open(os.path.join(AMB, "ADENDA_POST_HOC_2026-10-17.json"), encoding="utf-8"))
claves = {p["clave"] for p in ad["post_hoc"]}
ok({"VEREDICTO_V4_EXCLUYE_CRUDA", "CELLBENDER_SIN_SEED_Y_PRUEBA_EPOCAS"} <= claves, "la adenda declara los dos post hoc")
ok(all(p.get("efecto_en_conclusion") for p in ad["post_hoc"]), "cada post hoc dice su efecto en la conclusión")

cb = open(os.path.join(AMB, "_amb_cellbender.py"), encoding="utf-8").read()
ok("--seed" not in cb.split("def _cmd")[1].split("return")[1].split("]")[0], "el comando no finge pasar --seed")
ok("CELLBENDER_SIN_SEED_Y_PRUEBA_EPOCAS" in cb, "_amb_cellbender.py remite a la adenda")

print("RESULTADO flex post hoc: %s" % ("EN VERDE" if not fallos else "%d fallos" % fallos))
sys.exit(1 if fallos else 0)
