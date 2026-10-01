#!/usr/bin/env python3
"""test_incongruencias_caso.py — incongruencias entre fuentes del caso (1-oct-2026, Vega al mando F2b).

Informes FALSOS en texto (ningún dato real):
  · extracción ES/EN de RE, RP, Ki-67, HER2 (null/ultralow), cromogranina, sinaptofisina
  · «Score RP: 25» + salto + «% de núcleos RP positivos: 20%» → 20 %, no 25
  · «Sinaptofisina (+) · Ki 67 aprox. 15 %» → sinaptofisina positiva, Ki-67 15 %
  · misma muestra, valores distintos → incongruencia; redondeo (≤5 puntos) → no
  · HER2 «0» a secas no choca con «0 null»; «0 ultralow» frente a «0 null» sí, aunque haya un «0» antes
  · muestras distintas → heterogeneidad, sin incongruencia
  · copia con cifra que no está en ningún original de su muestra → «copia que no cuadra»
  · «positiva» no contradice un 80 %
  · fecha imposible en el historial
  · la frase del parte no lleva nombre de documento ni centro
  · actualizar() escribe md + json y `nuevas()` solo da lo visto desde la fecha pedida
"""
import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="incong_")
os.environ["BTP_HISTORIAL"] = os.path.join(_TMP, "clinico", "_historial")
os.makedirs(os.environ["BTP_HISTORIAL"])
sys.path.insert(0, os.path.join(ROOT, "tools"))
import incongruencias_caso as ic  # noqa: E402

_fail = 0


def ok(cond, name):
    global _fail
    if not cond:
        _fail += 1
        print("  ✗ %s" % name)


A = """Diagnóstico: mama derecha, BAG: carcinoma infiltrante.
- Receptores de estrógenos: Positivos (95% de células neoplásicas)
- Receptores de progesterona: Positivos (5% de células neoplásicas)
- Actividad proliferativa (Ki67): 60%
- Her2neu (Pathway-Roche): Negativo (0)
- Cromogranina: Positiva (80% de células neoplásicas)
- Sinaptofisina: Positiva (80% de células neoplásicas)"""
B = """Revisión de material, mama.
Score RE : 225
% de núcleos RE positivos : 93%
Score RP : 25
% de núcleos RP positivos : 20%
HER2: NEGATIVO (0), AUSENCIA DE TINCIÓN DE MEMBRANA
Ki67: 40%"""
C = """Breast review. - ER: 97%
- HER2: Negativo (0). Tinción de membrana incompleta y casi imperceptible en menos del 10%"""
D = """Biopsias (hueso, ilion): metástasis ósea.
Inmunohistoquímica: Sinaptofisina (+) · Ki 67 aprox. 15 %"""
E = "Resumen: breast carcinoma, ER 95%, PR 50%, Ki-67 {{N}}%, chromogranin positive"

ok(ic.extraer(A) == {"RE": "95%", "RP": "5%", "Ki-67": "60%", "HER2": "0",
                     "cromogranina": "80%", "sinaptofisina": "80%"}, "extrae el informe en castellano: %r" % ic.extraer(A))
eb = ic.extraer(B)
ok(eb.get("RP") == "20%" and eb.get("RE") == "93%", "Score sin %% no se pega al %% de la línea siguiente: %r" % eb)
ok(eb.get("HER2") == "0 (null)" and ic.extraer(C).get("HER2") == "0 (ultralow)", "HER2 null / ultralow")
ed = ic.extraer(D)
ok(ed.get("sinaptofisina") == "positiva" and ed.get("Ki-67") == "15%", "no cruza al marcador vecino: %r" % ed)

docs = [("2024-01-24 - X - BAG mama.pdf", "2024-01-24", False, A),
        ("2024-09-20 - Y - Patologia mama.pdf", "2024-09-20", False, C),
        ("2026-06-10 - Z - revisión mama.pdf", "2026-06-10", False, B),
        ("2026-07-17 - W - metástasis ósea.pdf", "2026-07-17", False, D),
        ("2025-04-28 - X - consult bilingual.pdf", "2025-04-28", True, E),
        ("2007-12-31 - Q - NGS.pdf", "2007-12-31", True, "")]
r = ic.analizar(docs)
inc = {i["marcador"]: i for i in r["incongruencias"]}
ok("RE" not in inc, "RE 95/93/97 es redondeo: no se avisa")
ok("RP" in inc and "Ki-67" in inc, "RP 5/20 y Ki-67 60/40 chocan")
ok("HER2" in inc and {l["valor"] for l in inc["HER2"]["lecturas"]} >= {"0 (ultralow)", "0 (null)"},
   "HER2 ultralow frente a null aunque haya un «0» a secas antes")
ok(all(i["muestra"] == "mama" for i in r["incongruencias"]), "las incongruencias son de la misma muestra")
ok(any(h["marcador"] == "Ki-67" and "hueso" in h["por_muestra"] for h in r["heterogeneidad"]),
   "hueso frente a mama = heterogeneidad")
cop = {(c["marcador"], c["valor"]) for c in r["copias"]}
ok(("RP", "50%") in cop and ("cromogranina", "positiva") not in cop,
   "copia: RP 50 %% no está en ningún informe; «positiva» casa con 80 %%: %r" % cop)
ok([f["fecha"] for f in r["fechas_raras"]] == ["2007-12-31"], "fecha imposible")
f = ic.frase_n1(inc["HER2"])
ok("pdf" not in f and " X " not in f and "jun-2026" in f, "la frase del parte va sin documento ni centro: %s" % f)

ic.actualizar(docs, hoy=date(2026, 10, 1))
ok(os.path.exists(ic._ruta_md()) and "Ki-67" in open(ic._ruta_md(), encoding="utf-8").read(), "escribe el md")
ok(len(ic.nuevas("2026-10-01")) == len(r["incongruencias"]) and ic.nuevas("2026-10-02") == [],
   "nuevas(): solo lo visto desde la fecha pedida")
ic.actualizar(docs, hoy=date(2026, 10, 5))
ok(ic.nuevas("2026-10-05") == [], "la segunda pasada no las vuelve a dar por nuevas")

print("test_incongruencias_caso: %s" % ("OK" if not _fail else "%d FALLOS" % _fail))
sys.exit(1 if _fail else 0)
