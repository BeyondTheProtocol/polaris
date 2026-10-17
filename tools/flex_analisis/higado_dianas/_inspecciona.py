"""Lectura de agregados de higado_dianas.json para la revisión (no calcula nada nuevo salvo el diagnóstico de
especificidad dentro del hígado, POST HOC declarado). stdout: agregados."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _h  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

d = json.load(open(_h.outp("higado_dianas.json")))
df = pd.read_csv(_h.outp("tabla_dianas_higado.csv"))
pd.set_option("display.width", 320)
pd.set_option("display.max_rows", 200)
dia = df[df["clase"] == "diana"]
print(dia[["gen", "via1_ref_hepatocito", "via1_ref_NK", "via1_ref_mieloide", "via1_ref_endotelio", "via1_ref_fibroblasto_estelar",
           "via1_doblete_pos", "via1_doblete_neg", "via2_enriq_vs_max_no_tumor", "via2_T_sin_restar_amb", "via2_T_rho_tumoral",
           "via1_cp10k", "via1_cp10k_ref_R1", "hueso68_cp10k", "hueso68_wilson_lo", "hueso68_suelo", "hueso87_positiva",
           "higadoAC_det_predicha_2000", "hueso68_det_predicha_2000"]].round(3).to_string(index=False))
print("via2 meta:", json.dumps(d["via2"]["meta"]["primaria_rho_no_tumoral"]["pesos_ic95"]))
print("via3 meta:", d["via3"]["meta"])
print("curva 5%:", d["via1"]["info"]["curva_5pct"])

if len(sys.argv) > 1 and sys.argv[1] == "especificidad":
    import dianas as D
    L = D.Muestra("higado")
    ref1 = np.isin(L.tipo, _h.REF1)
    alto = D.fgfr1_alto(L, ref1)
    todas = np.ones(L.n, bool)
    c = D.conjunto(L, todas, 4, 1000, True, 2, _h.PANEL, alto, _h.INMUNE)
    for t in sorted(set(L.tipo)):
        m = L.tipo == t
        print(f"  regla primaria sin exclusión por tipo: {t}: {int((c & m).sum())} de {int(m.sum())}")
