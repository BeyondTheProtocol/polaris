#!/usr/bin/env python3
"""El banco de `liver_lesions` contra las marcas del radiólogo (`visor3d banco`), con datos
SINTÉTICOS. Nació el 26-sep-26 con la opción 1 del dossier «¿puede Polaris descubrir las 35
lesiones que se le escaparon?».

Lo que frena:
  1. un blob conocido sin marca cuenta como «candidato sin marca» (nunca como falso positivo);
  2. dos marcas sobre una misma detección NUNCA suman 2 (uno a uno, y el ambiguo se lista);
  3. una marca sin posición sale del denominador y queda declarada;
  4. banco.json fuera de zona clínica ABORTA antes de tocar nada;
  5. la cota inferior es la misma Clopper-Pearson que tools/gate_escalera.py (copiada, no
     importada: tools/ está fuera del sys.path de la venv de imagen);
  6. la geometría del .npz de nnU-Net (LPS de SimpleITK) vuelve al afín RAS de nibabel;
  7. las 20 marcas ya emparejadas toman el centro de su lesión automática y lo declaran;
  8. la CLI expone `banco`;
  9. la corrida con TTA (espejos de nnU-Net) va en receta, caché y banco-tta.json aparte.

Necesita numpy/scipy/nibabel/SimpleITK (`.venv-imagen`): si faltan se relanza con la venv; sin
venv, skip (77) solo en modo portátil, en casa base ROJO (igual que test_visor3d_mascara_union.py).
"""
import inspect
import json
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

try:
    import numpy as np
    import nibabel as nib
    import SimpleITK as sitk
    from scipy import ndimage  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_BANCO_REEXEC") != "1":
        env = dict(os.environ, _BANCO_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el banco no se puede probar")
    sys.exit(1)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


def bola(forma, centro, radio):
    ejes = np.ogrid[tuple(slice(0, n) for n in forma)]
    return sum((e - c) ** 2 for e, c in zip(ejes, centro)) <= radio ** 2


# ── volumen sintético: hígado esférico, 1 mm isótropo (mm = vóxeles) ─────────────────────
FORMA = (60, 60, 60)
AFIN = np.eye(4)
HIGADO = bola(FORMA, (30, 30, 30), 18)          # 12..48 en cada eje; dilatado 5 mm: 7..53
PROB = np.zeros(FORMA, np.float32)
PROB[bola(FORMA, (30, 30, 30), 4)] = 0.9     # A: lesión con marca (M01) … y una segunda marca encima (M02)
PROB[bola(FORMA, (45, 30, 30), 3)] = 0.9     # B: blob SIN marca (el «FP conocido»)
PROB[bola(FORMA, (30, 45, 30), 3)] = 0.25    # C: lesión floja con marca (M04): sale a 0,2, no a 0,5
PROB[bola(FORMA, (30, 30, 2), 2)] = 0.9      # D: blob FUERA del hígado dilatado (z 0..4 vs 7): no cuenta
PROB[31, 12, 30] = 0.9                       # E: 1 vóxel (1 mm³ < 12 mm³): ruido, no candidato
MARCAS = [
    {"id": "M01", "mm": 8.0, "centro_mm": [30.0, 30.0, 30.0]},
    {"id": "M02", "mm": 4.0, "centro_mm": [32.0, 30.0, 30.0]},          # a 2 mm de M01, misma detección
    {"id": "M03", "mm": 5.0, "centro_mm": None, "posicion_de": "sin posición: prueba"},
    {"id": "M04", "mm": 5.0, "centro_mm": [30.0, 45.0, 30.0]},
    {"id": "M05", "mm": 3.5, "centro_mm": [30.0, 12.0, 30.0]},          # sin nada debajo: no detectada
]

print("== 1-3. núcleo: candidato sin marca, uno a uno, sin posición ==")
r = V._banco_core(PROB, AFIN, HIGADO, MARCAS, umbrales=(0.5, 0.2))
r05, r02 = r["resultados"]
check(r05["denominador"] == 4 and r02["denominador"] == 4, "denominador = 4 (M03 sin posición fuera)")
check([s["id"] for s in r["sin_posicion"]] == ["M03"] and "prueba" in r["sin_posicion"][0]["motivo"],
      "M03 queda declarada como sin posición, con su motivo")
check(r05["detectadas"] == 1, "a 0,5 se detecta UNA (M01+M02 sobre la misma detección no suman 2): %d"
      % r05["detectadas"])
check(r05["candidatos_sin_marca"]["total"] == 1, "el blob B cuenta como candidato sin marca (no FP): %d"
      % r05["candidatos_sin_marca"]["total"])
check(r05["candidatos_sin_marca"]["por_diametro_equivalente"]["6-10"] == 1,
      "…y va en el tramo de diámetro equivalente 6-10 (esfera de r=3 → 6,7 mm eq.)")
check(r05["componentes"] == 2, "D (fuera del hígado) y E (1 mm³) no son componentes: %d" % r05["componentes"])
amb = [a for a in r05["ambiguos"] if "deteccion" in a]
check(len(amb) == 1 and sorted(amb[0]["marcas"]) == ["M01", "M02"] and amb[0]["elegida"] == ["M01"],
      "el ambiguo (una detección, dos marcas) se LISTA y dice cuál eligió: %s" % amb)
check(r02["detectadas"] == 2 and r02["candidatos_sin_marca"]["total"] == 1,
      "a 0,2 aparece C (M04) y B sigue siendo candidato sin marca: %d/%d"
      % (r02["detectadas"], r02["candidatos_sin_marca"]["total"]))
t = r05["por_tramo"]
check(t["6-10"]["detectadas"] == 1 and t["3-6"]["no_detectadas"] == 3
      and sorted(t["3-6"]["no_detectadas_ids"]) == ["M02", "M04", "M05"],
      "tramos a 0,5: 6-10 1/0 · 3-6 0/3 con ids, RECUENTOS y no porcentajes")
pm = {m["id"]: m for m in r["por_marca"]}
check(pm["M04"]["detectada_en"] == [0.2] and pm["M01"]["detectada_en"] == [0.5, 0.2]
      and pm["M05"]["detectada_en"] == [], "por marca: en qué umbrales se detecta")
check(all(m["en_mascara"] for m in r["por_marca"]), "las 4 marcas con posición caen en la máscara dilatada")
r_fuera = V._banco_core(PROB, AFIN, HIGADO, [{"id": "M09", "mm": 5.0, "centro_mm": [30.0, 30.0, 2.0]}],
                        umbrales=(0.5,))
check(not r_fuera["por_marca"][0]["en_mascara"] and r_fuera["resultados"][0]["detectadas"] == 0,
      "una marca fuera de la máscara dilatada se declara y no puede detectarse")
# tolerancia: centro a ≤ max(5 mm, radio); a 6 mm de un blob de 3 mm de radio (marca de 4 mm) no empareja
r_tol = V._banco_core(PROB, AFIN, HIGADO, [{"id": "M10", "mm": 4.0, "centro_mm": [54.0, 30.0, 30.0]}],
                      umbrales=(0.5,))
check(r_tol["resultados"][0]["detectadas"] == 0, "a 6 mm del borde de B (tolerancia 5 mm) NO empareja")
r_tol2 = V._banco_core(PROB, AFIN, HIGADO, [{"id": "M10", "mm": 4.0, "centro_mm": [52.0, 30.0, 30.0]}],
                       umbrales=(0.5,))
check(r_tol2["resultados"][0]["detectadas"] == 1
      and abs(r_tol2["resultados"][0]["emparejadas"]["M10"]["dist_mm"] - 4.0) < 0.01,
      "a 4 mm del borde sí empareja y guarda la distancia")

check(isinstance(json.loads(json.dumps(r, default=V._json_numpy)), dict),
      "el resultado del núcleo es serializable a JSON con _json_numpy (int64 de numpy incluidos)")
try:
    json.dumps({"x": np.int64(3), "y": np.array([1, 2]), "z": np.bool_(True)}, default=V._json_numpy)
    check(True, "_json_numpy convierte int64, ndarray y bool_")
except TypeError as e:
    check(False, "_json_numpy falla: %s" % e)

print("== 4. banco.json fuera de zona clínica aborta ==")
tmp = tempfile.mkdtemp()
try:
    V._guarda_banco(os.path.join(tmp, "banco", "x"), {"a": 1})
    check(False, "_guarda_banco escribió fuera de zona clínica")
except SystemExit as e:
    check("ABORTA" in str(e) and not os.path.exists(os.path.join(tmp, "banco")),
          "_guarda_banco aborta fuera de zona clínica sin crear nada")
_dir_real = V._dir
V._dir = lambda *p: os.path.join(tmp, *p)
try:
    V.banco("s", "f")
    check(False, "banco() siguió con la salida fuera de zona clínica")
except SystemExit as e:
    check("ABORTA" in str(e) and "zona clínica" in str(e), "banco() aborta ANTES de leer nada: %s" % str(e)[:70])
finally:
    V._dir = _dir_real

print("== 5. la cota inferior es la Clopper-Pearson de gate_escalera ==")
spec_g = importlib.util.spec_from_file_location("gate_escalera", os.path.join(RAIZ, "tools", "gate_escalera.py"))
G = importlib.util.module_from_spec(spec_g)
try:
    sys.path.insert(0, os.path.join(RAIZ, "tools"))
    spec_g.loader.exec_module(G)
finally:
    sys.path.pop(0)
iguales = all(abs(V._cota_superior_cp(fp, n) - G.cota_superior(fp, n)) < 1e-9
              for fp, n in ((0, 1), (0, 30), (1, 30), (2, 100), (20, 55), (35, 55), (55, 55), (0, 0)))
check(iguales, "_cota_superior_cp == gate_escalera.cota_superior en 8 casos")
check(abs(V._cota_inferior_cp(55, 55) - 0.05 ** (1 / 55)) < 1e-6, "55/55 → cota inferior alfa^(1/55) = %.3f"
      % V._cota_inferior_cp(55, 55))
check(abs(V._cota_inferior_cp(20, 55) - (1 - G.cota_superior(35, 55))) < 1e-9 and V._cota_inferior_cp(0, 0) == 0.0,
      "20/55 → 1 − cota superior de 35 fallos en 55 (%.3f); 0/0 → 0" % V._cota_inferior_cp(20, 55))
check(abs(r05["cota_inferior_95"] - round(V._cota_inferior_cp(1, 4), 3)) < 1e-9,
      "el núcleo usa esa cota (1/4 → %.3f)" % r05["cota_inferior_95"])

print("== 6. geometría del .npz: LPS de SimpleITK → afín RAS de nibabel ==")
aff = np.array([[-0.75, 0, 0, 100.5], [0, -0.75, 0, -80.25], [0, 0, 1.0, -300.0], [0, 0, 0, 1]])
arr = np.random.RandomState(0).rand(7, 9, 11).astype(np.float32)
f = os.path.join(tmp, "a.nii.gz")
nib.save(nib.Nifti1Image(arr, aff), f)
im = sitk.ReadImage(f)
st = {"spacing": im.GetSpacing(), "origin": im.GetOrigin(), "direction": im.GetDirection()}
check(np.allclose(V._afin_de_sitk(st), aff, atol=1e-4), "_afin_de_sitk recupera el afín de nibabel")
import pickle  # noqa: E402
npz = os.path.join(tmp, "s01.npz")
zyx = sitk.GetArrayFromImage(im)                      # (z, y, x), como lo guarda nnU-Net
np.savez_compressed(npz, probabilities=np.stack([1 - zyx, zyx]))
pickle.dump({"sitk_stuff": st}, open(os.path.join(tmp, "s01.pkl"), "wb"))
p, a = V._prob_lesion(npz, os.path.join(tmp, "s01.pkl"))
check(p.shape == arr.shape and np.allclose(p, arr) and np.allclose(a, aff, atol=1e-4),
      "_prob_lesion devuelve la clase 1 en orden (x, y, z) con su afín")
# el lector de la tarea 591 (NibabelIOWithReorient) guarda nibabel_stuff, no sitk_stuff
pickle.dump({"nibabel_stuff": {"original_affine": aff, "reoriented_affine": aff}, "spacing": [1.0, 0.75, 0.75]},
            open(os.path.join(tmp, "s02.pkl"), "wb"))
p2, a2 = V._prob_lesion(npz, os.path.join(tmp, "s02.pkl"))
check(np.allclose(p2, arr) and np.allclose(a2, aff), "_prob_lesion también lee nibabel_stuff (lector de la tarea 591)")
pickle.dump({"spacing": [1.0]}, open(os.path.join(tmp, "s03.pkl"), "wb"))
try:
    V._prob_lesion(npz, os.path.join(tmp, "s03.pkl"))
    check(False, "sin geometría no abortó")
except SystemExit as e:
    check("ABORTA" in str(e), "sin geometría en el pkl, aborta")

print("== 7. las marcas ya emparejadas toman el centro de su lesión automática ==")
d = os.path.join(tmp, "assets", "f")
os.makedirs(d)
json.dump({"marcas": [{"id": 4, "mm": 15.29, "centro_mm": [1.0, 2.0, 3.0]}],
           "automaticas": {"2": {"marca": 1, "radiologo_mm": 20.35, "corte": 155},
                           "9": {"marca": 7, "radiologo_mm": 6.0, "corte": 100}},
           "sin_posicion": [{"id": 5, "mm": 3.0, "motivo": "no se localizó"}],
           "recuento": {"total": 4}}, open(os.path.join(d, "marcas.json"), "w"))
json.dump({"lesiones": [{"id": 2, "centro_mm": [10.0, 20.0, 30.0]}]}, open(os.path.join(d, "estudio.json"), "w"))
ms, rec = V._marcas_para_banco(d)
por = {m["id"]: m for m in ms}
check([m["id"] for m in ms] == ["M01", "M04", "M05", "M07"] and rec == {"total": 4}, "una entrada por marca, ordenadas")
check(por["M01"]["centro_mm"] == [10.0, 20.0, 30.0] and "L2" in por["M01"]["posicion_de"]
      and "por construcción" in por["M01"]["posicion_de"], "M01 toma el centro de L2 y lo declara")
check(por["M04"]["centro_mm"] == [1.0, 2.0, 3.0] and por["M04"]["posicion_de"] == "marca del radiólogo",
      "M04 conserva su posición de radiólogo")
check(por["M05"]["centro_mm"] is None and "no se localizó" in por["M05"]["posicion_de"], "M05 sin posición, con motivo")
check(por["M07"]["centro_mm"] is None and "L9" in por["M07"]["posicion_de"],
      "una automática que no está en estudio.json queda sin posición y lo dice")

print("== 8. CLI y constantes ==")
src_main = inspect.getsource(V.main)
check('add_parser("banco"' in src_main and "--umbrales" in src_main and "--rejillas" in src_main,
      "la CLI expone banco con --umbrales y --rejillas")
check(V.BANCO_UMBRALES == (0.5, 0.4, 0.3, 0.2, 0.1) and V.BANCO_CROP_ADDON_MM == 20,
      "umbrales del encargo y margen de recorte de 20 mm (python_api.py:769)")
check("exige_zona_clinica(salida_dir)" in inspect.getsource(V.banco).split("_marcas_para_banco")[0],
      "banco() exige zona clínica antes de leer marcas.json")

print("== 9. TTA (espejos de nnU-Net): receta, caché y salida aparte ==")
# 26-sep-26: {{TITULAR}} pidió probar el mirroring de nnU-Net como última opción barata. Lo que frena:
# la corrida con TTA NUNCA reutiliza la caché de la corrida sin TTA (ni al revés) y no pisa su banco.json.
check(V._nombre_banco(False) == "banco.json" and V._nombre_banco(True) == "banco-tta.json",
      "banco.json sin TTA, banco-tta.json con TTA")
r_sin = V._receta_seg("liver_lesions", "mps", codigo="x", ml=True, folds=[0], tta=False)
r_con = V._receta_seg("liver_lesions", "mps", codigo="x", ml=True, folds=[0], tta=True)
check(r_sin != r_con and r_sin["kw"]["tta"] is False and r_con["kw"]["tta"] is True
      and V.huella(json.dumps(r_sin, sort_keys=True)) != V.huella(json.dumps(r_con, sort_keys=True)),
      "la receta lleva tta en kw y su hash cambia")
f_tta = os.path.join(tmp, "prob.npz")
open(f_tta, "wb").write(b"npz")
V.sella_cache(f_tta, r_sin)
check(V.cache_vale(f_tta, r_sin) and not V.cache_vale(f_tta, r_con),
      "un .npz sellado sin TTA vale para su receta y NO para la receta con TTA")
colab = dict(r_sin, origen="colab")
check(V._receta_colab_casa(colab, r_sin) and not V._receta_colab_casa(colab, r_con),
      "la equivalencia Colab↔casa también distingue tta (va en kw)")
src_prob = inspect.getsource(V.probabilidades_lesiones)
check("tta=False" in src_prob.split("\n")[0] and 'save_probabilities=True, tta=tta)' in src_prob
      and 'trainer="nnUNetTrainer", tta=tta' in src_prob and '"_tta" if tta else ""' in src_prob,
      "probabilidades_lesiones: tta va a la receta, al nnUNet_predict_image de la 591 y al nombre del .npz")
check('trainer="nnUNetTrainer_4000epochs_NoMirroring",\n' in src_prob and "tta=False, multilabel_image=True, resample=3.0" in src_prob,
      "la máscara de recorte de 3 mm (tarea 297) sigue sin TTA: no depende del flag")
src_banco = inspect.getsource(V.banco)
check("tta=False" in src_banco.split('"""')[0] and "probabilidades_lesiones(serie, mascara, device, tta=tta)" in src_banco
      and '_guarda_banco(salida_dir, salida, _nombre_banco(P["tta"]))' in src_banco and '"tta": P["tta"]' in src_banco,
      "banco(): pasa tta al detector, lo escribe en el JSON y elige el nombre de salida por él")
check('"--tta"' in src_main and 'tta=a.tta' in inspect.getsource(V._cmd_banco), "la CLI expone banco --tta")

print("\n%s" % ("VERDE" if not fallos else "ROJO: %d fallos" % len(fallos)))
sys.exit(1 if fallos else 0)
