#!/usr/bin/env python3
"""test_flex_higado_celula.py — fase 2 del hígado célula a célula (tools/flex_analisis/higado_celula/).

Sin datos de la paciente:
 1. Estadística pura: Wilson k=0, Hanley-McNeil, Katz con ceros, redondeo determinista.
 2. Veredicto: NO RESUELTO si falla C1 o C2; FRÁGIL si falla C3, C4, C5 o una matriz alternativa válida; RESUELTO si no.
 3. La copia parametrizada de los brazos da lo mismo que _final.brazos_genes (sintético).
 4. dobletes.R detecta dobletes plantados (scDblFinder, sintético) y da lo mismo dos veces.
 5. Muro: los dos scripts no arrancan sin la ventanilla; los dos procesadores están registrados en lector_clinico.
 6. Reglas: JSON válido, la ENMIENDA cita el sha de las reglas, 8p11 retirado.
Post hoc 2 (POSTHOC_2_PLAN.json): el plan es el commiteado antes del código y cita las reglas; el reparto estratificado
de H1/H2 conserva el hold-out del aleatorio y parte cada tipo por la mitad (sintético); kappa, TVD y etiqueta puros;
dobletes_posthoc2.R corre las tres configuraciones en sintético y da lo mismo dos veces; posthoc2.py y el hijo de REFCON
no arrancan sin la ventanilla; procesador flex_hc_posthoc2 registrado; con salidas, MANIFIESTO_POSTHOC2 con dos
ejecuciones idénticas, red denegada dentro del sandbox, salidas canónicas intactas y sin barcodes.
Con cachés y salidas (esta máquina): el MANIFIESTO dice dos ejecuciones idénticas, cada salida en disco tiene el sha del
MANIFIESTO y ninguna salida lleva barcodes ni ids de bloque. Re-ejecución completa (lenta, ~1 h) solo con
FLEX_HC_LENTO=1. Sin entornos (otra máquina, CI) → SKIP 77, nunca PASS.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "tools", "flex_analisis", "higado_celula")
PY = os.path.expanduser("~/.claude-science/conda/envs/nescore/bin/python")
PYR = os.path.expanduser("~/.claude-science/conda/envs/refcon/bin/python")
RS = os.path.expanduser("~/.claude-science/conda/envs/dobletes/bin/Rscript")
BASE = os.path.expanduser("~/Clinico-PRIVADO/flex/higado_celula")
fallos = []


def ok(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fallos.append(msg)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


PURO = r"""
import sys, json
sys.path.insert(0, %r)
for d in ("ambiente", "cierre", "tribunal"):
    sys.path.append(%r + "/../" + d)
import numpy as np, scipy.sparse as sp
import _hc, _hc_final as HF, _final as F, _c
out = {}
out["wilson0"] = _hc.wilson(0, 490)[2]
out["hanley"] = _hc.hanley(0.9, 100, 100)
out["katz0"] = _hc.katz(0, 10, 5, 10)["razon"]
out["r6"] = _hc.r_(0.123456789)
ev = lambda c1, c2, c3, c5: {"C1": {"PASA": c1}, "C2": {"PASA": c2}, "C3": {"PASA": c3}, "C5": {"PASA": c5}}
out["v_no"] = HF.veredicto(ev(False, True, True, True), True, False)[0]
out["v_no2"] = HF.veredicto(ev(True, False, True, True), True, False)[0]
out["v_ok"] = HF.veredicto(ev(True, True, True, True), True, False)[0]
out["v_c4"] = HF.veredicto(ev(True, True, True, True), False, False)
out["v_c5"] = HF.veredicto(ev(True, True, True, False), True, False)[0]
out["v_alt"] = HF.veredicto(ev(True, True, True, True), True, True)[0]
rng = np.random.default_rng(3)
class T: pass
def hace(n, G):
    t = T()
    t.X = sp.csr_matrix(rng.poisson(rng.gamma(0.8, 2.0, G), size=(n, G)).astype(float))
    chs = np.array(["chr%%d" %% (1 + i %% 22) for i in range(G)])
    t.o = {"chrom": chs, "start": (np.arange(G) * 997_331) %% 240_000_000}
    t.n = n
    return t
L, B = hace(300, 2500), hace(120, 2500)
L.X = L.X[:, :]
m = rng.random(120) < 0.4; rB = ~m; cand = rng.random(300) < 0.5; nulo = ~cand & (rng.random(300) < 0.5); hep = ~cand & ~nulo
g0, b0, v0, _ = F.brazos_genes(L, B, m, rB, cand, nulo, hep)
g1, b1, v1 = HF.brazos(L, B, m, rB, cand, nulo, hep)
out["brazos_iguales"] = bool((g0 == g1).all() and v0 == v1 and (b0[g0] == b1[g1]).all() and len(v0) > 3)
# post hoc 2
import _hc_posthoc2 as P2
out["plan_sha_ok"] = _hc.sha256(P2.PLAN) == P2.PLAN_SHA
n = 3000
tipos = np.array(["hepatocito", "NK", "endotelio", "mieloide", "fibroblasto_estelar", "epitelial", "no_asignado"])
tl = tipos[rng.integers(0, len(tipos), n)]
sym = np.array(["CYP2E1", "CYP3A4", "HAL", "SDS", "ARG1", "KRT7", "SOX9", "ALB", "APOA1", "SERPINA1", "HP", "TF", "G1"])
L = F.T.__new__(F.T)
L.X = sp.csr_matrix(rng.poisson(1.0, size=(n, len(sym))).astype(float)); L.sym = sym; L.tipo = tl; L.n = n; L.umi = np.asarray(L.X.sum(axis=1)).ravel()
qc = {"ngen": rng.integers(300, 3000, n), "scrublet": rng.random(n) * 0.3, "decontx_cont": rng.random(n)}
exc = rng.random(n) < 0.08
oks = []
for sem in (11, 12, 13):
    ga = HF.grupos_semilla(L, qc, sem, excluir=exc)
    ge = P2.grupos_estratificado(L, qc, sem, excluir=exc)
    ge2 = P2.grupos_estratificado(L, qc, sem, excluir=exc)
    mitad = all(int((ge["h1"] & (tl == t)).sum()) == int(((ga["h1"] | ga["h2"]) & (tl == t)).sum()) // 2 for t in P2.TIPOS_H)
    oks.append(bool(all((ga[k] == ge[k]).all() for k in ("keep", "ref", "pool", "no_ref", "hep", "pp"))
                    and ((ga["h1"] | ga["h2"]) == (ge["h1"] | ge["h2"])).all() and not (ge["h1"] & ge["h2"]).any()
                    and mitad and all((ge[k] == ge2[k]).all() for k in ge)))
out["estratificado_ok"] = all(oks)
out["kappa1"] = P2.kappa(np.array([1, 0, 1, 0], bool), np.array([1, 0, 1, 0], bool))["kappa"]
out["kappa0"] = P2.kappa(np.array([1, 1, 0, 0], bool), np.array([1, 0, 1, 0], bool))["kappa"]
out["tvd"] = P2.tvd({"NK": 2, "endotelio": 2, "mieloide": 0, "fibroblasto_estelar": 0}, {"NK": 0, "endotelio": 2, "mieloide": 2, "fibroblasto_estelar": 0})
out["etiquetas"] = [P2.etiqueta(False, True, True), P2.etiqueta(True, False, True), P2.etiqueta(True, True, False), P2.etiqueta(True, True, True)]
out["serie"] = P2.serie([0.1, 0.2, 0.4, 0.5])["frac_mayor_0_3"]
print(json.dumps(out))
"""

SINT_DOBLETES = r"""
import numpy as np, os, sys
d = sys.argv[1]
rng = np.random.default_rng(1)
G = 800; n = [600, 500, 300]
mu = [rng.gamma(0.5, 1.0, G) for _ in n]
for k in range(3):
    s = rng.choice(G, 80, replace=False); mu[k][s] *= 25
cells = np.vstack([rng.poisson(mu[k] * 0.6, size=(n[k], G)) for k in range(3)])
lab = np.repeat(["epitelial", "hepatocito", "mieloide"], n)
i = rng.choice(np.where(lab == "epitelial")[0], 60, replace=False); j = rng.choice(np.where(lab == "hepatocito")[0], 60, replace=False)
X = np.vstack([cells, cells[i] + cells[j]]).astype(np.int32)
lab = np.concatenate([lab, np.repeat("epitelial", 60)])
import scipy.sparse as sp
M = sp.csr_matrix(X); M.sort_indices()
M.indptr.astype("<i4").tofile(os.path.join(d, "higado_p.bin")); M.indices.astype("<i4").tofile(os.path.join(d, "higado_i.bin"))
M.data.astype("<i4").tofile(os.path.join(d, "higado_x.bin"))
open(os.path.join(d, "higado_dims.txt"), "w").write(f"{G}\n{X.shape[0]}\n{M.nnz}\n")
open(os.path.join(d, "higado_genes.txt"), "w").write("\n".join(f"g{k}" for k in range(G)) + "\n")
open(os.path.join(d, "higado_tipo.txt"), "w").write("\n".join(lab) + "\n")
"""


def main():
    if not (os.path.exists(PY) and os.path.exists(RS)):
        print("SKIP: faltan los entornos nescore o dobletes")
        return 77
    env = dict(os.environ, NUMBA_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONHASHSEED="0")
    env.pop("BTP_VENTANILLA", None)
    # 1-3
    r = subprocess.run([PY, "-I", "-c", PURO % (DIR, DIR)], capture_output=True, text=True, env=env, timeout=600)
    v = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {}
    ok(r.returncode == 0, "funciones puras importan y corren" + ("" if r.returncode == 0 else ": " + r.stderr[-400:]))
    ok(v.get("wilson0") is not None and 0 < v["wilson0"] < 0.01, "Wilson k=0 de 490 da cota superior > 0 y < 1 %")
    ok(v.get("hanley") is not None and v["hanley"][0] < 0.9 < v["hanley"][1], "Hanley-McNeil contiene el AUC")
    ok(v.get("katz0") is not None and v["katz0"] > 0, "Katz con ceros no rompe (+0,5)")
    ok(v.get("r6") == 0.123457, "redondeo determinista a 6 cifras")
    ok(v.get("v_no") == "NO RESUELTO" and v.get("v_no2") == "NO RESUELTO", "falla C1 o C2 → NO RESUELTO")
    ok(v.get("v_ok") == "RESUELTO", "todo pasa → RESUELTO")
    ok(v.get("v_c4") == ["FRÁGIL", ["C4"]] and v.get("v_c5") == "FRÁGIL" and v.get("v_alt") == "FRÁGIL",
       "falla C4, C5 o una matriz alternativa válida → FRÁGIL")
    ok(v.get("brazos_iguales") is True, "brazos parametrizados == _final.brazos_genes (sintético)")
    ok(v.get("plan_sha_ok") is True, "posthoc2: POSTHOC_2_PLAN.json es el commiteado antes del código (sha del módulo)")
    ok(v.get("estratificado_ok") is True, "posthoc2: H1/H2 estratificado conserva el hold-out del aleatorio, parte cada tipo por la mitad y es determinista")
    ok(v.get("kappa1") == 1.0 and v.get("kappa0") == 0.0, "posthoc2: kappa de Cohen 1 con acuerdo total y 0 con acuerdo de azar")
    ok(v.get("tvd") == 0.5, "posthoc2: distancia de variación total")
    ok(v.get("etiquetas") == ["NO RESUELTO", "NO RESUELTO", "FRÁGIL", "RESUELTO posible (C3 y C4 sin calcular)"], "posthoc2: etiqueta informativa por semilla")
    ok(v.get("serie") == 0.5, "posthoc2: fracción de r > 0,3")
    # 4
    d = tempfile.mkdtemp(prefix="hc_dobletes_")
    try:
        subprocess.run([PY, "-I", "-c", SINT_DOBLETES, d], check=True, env=env, timeout=120)
        renv = dict(env, PATH=os.path.dirname(RS) + os.pathsep + env.get("PATH", ""))
        res = []
        for _ in (1, 2):
            p = subprocess.run([RS, "--vanilla", os.path.join(DIR, "dobletes.R"), d, "scdbl"], capture_output=True, text=True, env=renv, timeout=900)
            ok(p.returncode == 0, "dobletes.R scdbl corre en sintético" + ("" if p.returncode == 0 else ": " + p.stderr[-300:]))
            res.append(open(os.path.join(d, "scdbl_dbr006.csv")).read())
        filas = [x.split(",") for x in res[0].strip().splitlines()[1:]]
        plantados = [f for f in filas if int(f[0]) >= 1400]
        det = sum(f[1].strip('"') == "doublet" for f in plantados)
        falsos = sum(f[1].strip('"') == "doublet" for f in filas if int(f[0]) < 1400)
        ok(det >= 54, f"scDblFinder detecta >= 90 % de 60 dobletes plantados ({det})")
        ok(falsos <= 0.06 * 1400 + 10, f"falsos dobletes acotados ({falsos} de 1.400)")
        orig = sum("epitelial" in f[3] and "hepatocito" in f[3] for f in plantados if f[1].strip('"') == "doublet")
        ok(orig >= 0.8 * max(det, 1), f"origen epitelial+hepatocito en los plantados detectados ({orig}/{det})")
        ok(res[0] == res[1], "scDblFinder: dos ejecuciones idénticas")
        # post hoc 2 (P3): tres configuraciones, dos veces
        res2 = []
        for _ in (1, 2):
            p = subprocess.run([RS, "--vanilla", os.path.join(DIR, "dobletes_posthoc2.R"), d, d], capture_output=True, text=True, env=renv, timeout=900)
            ok(p.returncode == 0, "dobletes_posthoc2.R corre en sintético" + ("" if p.returncode == 0 else ": " + p.stderr[-300:]))
            res2.append({m: open(os.path.join(d, f"scdbl_{m}.csv")).read() for m in ("dbrsd1", "sinclusters", "sinclusters_dbrsd1")})
        for m in ("dbrsd1", "sinclusters", "sinclusters_dbrsd1"):
            filas = [x.split(",") for x in res2[0][m].strip().splitlines()[1:]]
            det = sum(f[1].strip('"') == "doublet" for f in filas if int(f[0]) >= 1400)
            falsos = sum(f[1].strip('"') == "doublet" for f in filas if int(f[0]) < 1400)
            ok(det >= 54 and falsos <= 0.06 * 1400 + 10, f"posthoc2 {m}: detecta >= 90 % de 60 plantados ({det}) con falsos acotados ({falsos})")
        ok(res2[0] == res2[1], "dobletes_posthoc2.R: dos ejecuciones idénticas en las tres configuraciones")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # 5
    for script in ("higado_celula.py", "refcon_run.py"):
        py = PY if script == "higado_celula.py" else (PYR if os.path.exists(PYR) else PY)
        p = subprocess.run([py, "-I", os.path.join(DIR, script), "final" if script == "higado_celula.py" else "hueso"],
                           capture_output=True, text=True, env=env, timeout=120)
        ok(p.returncode == 2 and "ventanilla" in p.stderr, f"{script} no arranca sin la ventanilla (rc={p.returncode})")
    p = subprocess.run([PY, "-I", os.path.join(DIR, "posthoc2.py"), "final"], capture_output=True, text=True, env=env, timeout=120)
    ok(p.returncode == 2 and "ventanilla" in p.stderr, f"posthoc2.py no arranca sin la ventanilla (rc={p.returncode})")
    p = subprocess.run([PYR if os.path.exists(PYR) else PY, "-I", os.path.join(DIR, "_hc_p4_refcon.py"), tempfile.gettempdir()],
                       capture_output=True, text=True, env=env, timeout=120)
    ok(p.returncode == 2 and "ventanilla" in p.stderr, f"_hc_p4_refcon.py no arranca fuera de la ventanilla y del sandbox (rc={p.returncode})")
    src = open(os.path.join(ROOT, "tools", "lector_clinico.py")).read()
    ok('"flex_hc_posthoc2": ("tools/flex_analisis/higado_celula/posthoc2.py"' in src, "procesador flex_hc_posthoc2 registrado")
    ok('"flex_higado_celula": ("tools/flex_analisis/higado_celula/higado_celula.py"' in src, "procesador flex_higado_celula registrado")
    ok('"flex_refcon": ("tools/flex_analisis/higado_celula/refcon_run.py"' in src, "procesador flex_refcon registrado")
    hc = open(os.path.join(DIR, "_hc.py")).read()
    ok("deny network*" in hc and "sandbox-exec" in hc, "los procesos se re-ejecutan dentro de sandbox-exec sin red")
    # 6
    reglas = os.path.join(DIR, "REGLAS_HIGADO_CELULA_v1.json")
    R = json.load(open(reglas))
    E = json.load(open(os.path.join(DIR, "ENMIENDA_1_HIGADO_CELULA.json")))
    ok(sha(reglas) in E["estado"], "la ENMIENDA_1 cita el sha256 exacto de las reglas (reglas sin tocar)")
    ok("RETIRADO" in R["criterios_D3"]["C6_8p11"], "8p11 retirado por potencia desde el principio")
    ok(all(k in R["criterios_D3"] for k in ("C1_AUC", "C2_controles", "C3_clon_hueso", "C4_estabilidad_dianas", "C5_poblacional")),
       "criterios C1-C5 congelados")
    PL = json.load(open(os.path.join(DIR, "POSTHOC_2_PLAN.json")))
    ok(PL["base"]["reglas_sha256"] == sha(reglas) and "NO cambian ningún veredicto" in PL["estado"],
       "POSTHOC_2_PLAN cita el sha de las reglas y declara que no cambia ningún veredicto")
    ok(all(k in PL for k in ("P1_sensibilidad_C3", "P2_particion_H1_H2", "P3_dobletes_configuracion_Flex", "P4_clasificador_REFCON", "P5_llamadas_con_HEP5")),
       "POSTHOC_2_PLAN declara P1-P5")
    # con salidas
    man = os.path.join(BASE, "MANIFIESTO_HIGADO_CELULA.json")
    if not os.path.exists(man):
        print("SKIP parcial: sin MANIFIESTO en esta máquina (sin datos)")
        return 1 if fallos else 77
    M = json.load(open(man))
    ok(M.get("dos_ejecuciones") is not None and M["dos_ejecuciones"]["identicas"], "MANIFIESTO: dos ejecuciones con sha256 idénticos")
    ok(M.get("reglas_sha256") == sha(reglas), "MANIFIESTO generado con estas reglas")
    malos = [f for f, h in M["salidas"].items() if not os.path.exists(os.path.join(BASE, f)) or sha(os.path.join(BASE, f)) != h]
    ok(not malos, "cada salida en disco tiene el sha del MANIFIESTO" + (f" (difieren {malos[:3]})" if malos else ""))
    texto = ""
    for f in M["salidas"]:
        if f.endswith((".json", ".csv")):
            texto += open(os.path.join(BASE, f), errors="ignore").read()
    ok(not re.search(r"[ACGT]{16,}", texto), "ninguna salida lleva barcodes")
    ok(not re.search(r"B?\d{2}-\d{5}_M\d", texto), "ninguna salida lleva ids de bloque")
    man2 = os.path.join(BASE, "posthoc2", "MANIFIESTO_POSTHOC2.json")
    if os.path.exists(man2):
        M2 = json.load(open(man2))
        ok(M2.get("dos_ejecuciones") is not None and M2["dos_ejecuciones"]["identicas"], "posthoc2: dos ejecuciones con sha256 idénticos")
        ok(M2.get("plan_sha256") == sha(os.path.join(DIR, "POSTHOC_2_PLAN.json")), "posthoc2: MANIFIESTO con el plan commiteado")
        ok(M2["salidas_canonicas_fase2"]["intactas"], "posthoc2: salidas canónicas de la fase 2 intactas")
        malos2 = [f for f, h in M2["salidas"].items() if sha(os.path.join(BASE, "posthoc2", f)) != h]
        ok(not malos2, "posthoc2: cada salida en disco tiene el sha del MANIFIESTO" + (f" (difieren {malos2[:3]})" if malos2 else ""))
        texto2 = "".join(open(os.path.join(BASE, "posthoc2", f), errors="ignore").read() for f in M2["salidas"] if f.endswith((".json", ".csv")))
        ok(not re.search(r"[ACGT]{16,}", texto2) and not re.search(r"B?\d{2}-\d{5}_M\d", texto2), "posthoc2: ninguna salida lleva barcodes ni ids de bloque")
        J2 = json.load(open(os.path.join(BASE, "posthoc2", "posthoc2.json")))
        ok(J2.get("NO_CAMBIA_NINGUN_VEREDICTO") is True and J2["muro"]["red_denegada"] is True, "posthoc2: declara que no cambia veredictos y la red estaba denegada (EPERM)")
        ok(J2["P1"]["estandar"]["reproduce_base_canonica"]["IDENTICO"] and J2["P1"]["estricto"]["reproduce_base_canonica"]["IDENTICO"]
           and J2["P2"]["reproduce_posthoc_semillas_canonico"]["TODAS"], "posthoc2: reproduce la base canónica y el barrido de semillas canónico")
    else:
        print("aviso: sin salidas de posthoc2 en esta máquina")
    if os.environ.get("FLEX_HC_LENTO") == "1":
        hashes = []
        for i in (1, 2):
            out = os.path.join(BASE, f"_test_rep{i}")
            shutil.rmtree(out, ignore_errors=True)
            p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "lector_clinico.py"), "procesa", "flex_higado_celula", "--", "final"],
                               capture_output=True, text=True, env=dict(os.environ, HIGADO_CELULA_OUT=out), timeout=7200)
            ok(p.returncode == 0, f"re-ejecución {i} sin error")
            ok(not re.search(r"[ACGT]{16,}", p.stdout + p.stderr), f"re-ejecución {i}: stdout sin barcodes")
            hashes.append({os.path.relpath(os.path.join(r_, f), out): sha(os.path.join(r_, f)) for r_, _, fs in os.walk(out) for f in fs})
            shutil.rmtree(out, ignore_errors=True)
        ok(hashes[0] == hashes[1], "re-ejecución lenta: sha256 idénticos")
        ok(all(M["salidas"].get(k) == h for k, h in hashes[0].items()), "re-ejecución lenta = MANIFIESTO")
    else:
        print("aviso: re-ejecución completa omitida (FLEX_HC_LENTO=1 para hacerla; ~1 h)")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
