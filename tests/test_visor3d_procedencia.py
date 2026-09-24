#!/usr/bin/env python3
"""Procedencia sellada de tools/visor3d.py con datos SINTÉTICOS (nada de la paciente).

Lo que se congela (auditoría de arquitectura, 24-sep-26):
  1. compara() ABORTA si los dos lados vienen de versiones de TotalSegmentator o recetas
     distintas, o si a un lado le falta el sello; y el JSON que escribe lleva el sello.
  2. La caché de segmentación no se reutiliza sin sello ni con otra versión del modelo.
  3. La transformación del registro PET se escribe a disco, se relee igual y lleva sha256.
  4. Un cruce PET hecho sobre otra segmentación del TC no se usa (los ids ya no casan).
  5. Lo que detecta el modelo sale como «lesión candidata».
  6. La exportación web no copia el sello (el SeriesInstanceUID vive solo en zona clínica),
     y no cruza un PET sin sello o de otra segmentación.
"""
import json
import os
import subprocess
import sys
import tempfile
import types

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")
try:
    import numpy as np
    import nibabel as nib
    import scipy  # noqa: F401
    import SimpleITK as sitk
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)],
                                 env=dict(os.environ, _VISOR3D_REEXEC="1")))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el sello de procedencia no puede probarse")
    sys.exit(1)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

TMP = tempfile.mkdtemp(prefix="visor3d_proc_")
V.SALIDA_RAIZ = TMP
V.exige_zona_clinica = lambda r: None
V.exige_telemetria_apagada = lambda: None
V.serie_uid = lambda raices, serie: "1.2.826.0.1.3680043.2.99999." + serie   # UID sintético
fallos = []


def ok(cond, msg):
    if not cond:
        fallos.append(msg)


def aborta(fn, *a):
    try:
        fn(*a)
    except SystemExit:
        return True
    return False


def sello(version="2.4.0", receta="r1", tareas=("total", "liver_lesions")):
    return {"totalsegmentator": version, "visor3d": "abcd1234", "serie": "s",
            "serie_uid": "x", "segmentaciones": {t: {"sha256": "0" * 16, "receta": receta}
                                                 for t in tareas}}


# 1) exige_procedencia_comparable ────────────────────────────────────────────────────────
C = V.exige_procedencia_comparable
ok(not aborta(C, sello(), sello()), "dos lados iguales deberían compararse")
ok(aborta(C, sello("2.4.0"), sello("2.5.0")), "versiones distintas de TotalSegmentator pasan")
ok(aborta(C, sello(receta="r1"), sello(receta="r2")), "recetas distintas pasan")
ok(aborta(C, None, sello()), "un lado sin sello pasa")
ok(aborta(C, sello(receta=None), sello(receta=None)), "segmentaciones sin sello pasan")
ok(aborta(C, sello(tareas=("total",)), sello()), "conjuntos de tareas distintos pasan")
ok(aborta(C, dict(sello(), totalsegmentator=None), dict(sello(), totalsegmentator=None)),
   "sin versión de TotalSegmentator pasa")

# 2) compara(): aborta antes de escribir, y lo que escribe va sellado ───────────────────
lados = {}


def lesiones_falsas(raices, serie):
    return {"serie": serie, "procedencia": lados[serie], "volumen_higado_ml": 1500.0,
            "centro_higado_mm": [0.0, 0.0, 0.0], "volumen_tumoral_ml": 1.0,
            "lesiones": [{"id": 1, "estado": V.ESTADO_CANDIDATA, "diametro_mm": 12.0,
                          "volumen_ml": 1.0, "centro_mm": [10.0, 0.0, 0.0], "segmento": 2,
                          "pequena": False, "acuerdo_2o_modelo": None}]}


V.lesiones = lesiones_falsas
lados.update(A=sello("2.4.0"), B=sello("2.5.0"))
ok(aborta(V.compara, [], "A", "B"), "compara() con versiones distintas no aborta")
ok(not os.path.exists(os.path.join(V._dir("comparacion"), "A_B.json")),
   "compara() escribió la comparación aunque las versiones no casan")
lados.update(A=sello(), B=sello())
res, ruta = V.compara([], "A", "B")
guardado = json.load(open(ruta))
pr = guardado.get("procedencia") or {}
ok(pr.get("antes", {}).get("totalsegmentator") == "2.4.0"
   and pr.get("despues", {}).get("totalsegmentator") == "2.4.0" and pr.get("visor3d"),
   "el JSON de compara() no lleva el sello de los dos lados: %s" % pr)

# 3) caché de segmentación: sin sello o con otra versión, se re-segmenta ────────────────
llamadas = []


def ts_falso(input, output, task, **kw):
    llamadas.append(task)
    nib.save(nib.Nifti1Image(np.full((4, 4, 4), len(llamadas), np.uint8), np.eye(4)), output)


pa = types.ModuleType("totalsegmentator.python_api")
pa.totalsegmentator = ts_falso
import totalsegmentator  # noqa: E402,F401  (el paquete real; solo se sustituye la API que segmenta)
sys.modules["totalsegmentator.python_api"] = pa
V.convierte = lambda raices, serie: ("/no/importa.nii.gz", {"modalidad": "CT"})
version = {"v": "2.4.0"}
V._version_ts = lambda: version["v"]
seg = V.segmenta([], "S1", tareas=["liver_lesions"], device="cpu")
ok(llamadas == ["liver_lesions"], "primera vez no segmentó: %s" % llamadas)
ok(os.path.exists(seg["liver_lesions"] + ".sello.json"), "la segmentación no dejó sello")
V.segmenta([], "S1", tareas=["liver_lesions"], device="cpu")
ok(len(llamadas) == 1, "con el mismo sello debería reutilizar la caché: %s" % llamadas)
version["v"] = "2.5.0"
V.segmenta([], "S1", tareas=["liver_lesions"], device="cpu")
ok(len(llamadas) == 2, "cambió la versión del modelo y reutilizó la segmentación vieja")
os.remove(seg["liver_lesions"] + ".sello.json")
V.segmenta([], "S1", tareas=["liver_lesions"], device="cpu")
ok(len(llamadas) == 3, "una caché sin sello se reutilizó")
with open(seg["liver_lesions"], "ab") as f:
    f.write(b"\0")
V.segmenta([], "S1", tareas=["liver_lesions"], device="cpu")
ok(len(llamadas) == 4, "una caché cuyo contenido no es el sellado se reutilizó")
p = V.procedencia([], "S1", seg)
ok(p["totalsegmentator"] == "2.5.0" and p["segmentaciones"]["liver_lesions"]["receta"]
   and len(p["segmentaciones"]["liver_lesions"]["sha256"]) == 16 and p["serie_uid"],
   "procedencia() incompleta: %s" % p)
version["v"] = "2.4.0"

# 4) la transformación del PET se escribe, se relee igual y lleva sha256 ────────────────
im = sitk.Image([16, 16, 16], sitk.sitkFloat32)
rig = sitk.Euler3DTransform()
rig.SetTranslation([1.0, 2.0, 3.0])
campo = sitk.Image([16, 16, 16], sitk.sitkVectorFloat64)
campo.CopyInformation(im)
comp = sitk.CompositeTransform([rig, sitk.DisplacementFieldTransform(campo)])
t = V.guarda_transformada(comp, os.path.join(V._dir("pet"), "P_D.tfm.h5"))
ruta_t = os.path.join(V._dir("pet"), t["fichero"])
ok(os.path.exists(ruta_t) and len(t["sha256"]) == 16 and t["sha256"] == V._sha256(ruta_t),
   "la transformada no se escribió o su sha256 no casa: %s" % t)
releida = sitk.ReadTransform(ruta_t)
ok(np.allclose(releida.TransformPoint([5.0, 5.0, 5.0]), comp.TransformPoint([5.0, 5.0, 5.0])),
   "la transformada releída no mueve los puntos igual")

# 5) un cruce PET de otra segmentación del TC no se usa ─────────────────────────────────
info = {"serie": "D", "procedencia": sello(receta="r1")}
ok(aborta(V.pet_vigente, {"lesiones": []}, info), "un PET sin sello se usó")
ok(aborta(V.pet_vigente, {"procedencia": {"ct_diag": sello(receta="r2")}}, info),
   "un PET hecho sobre otra segmentación del TC se usó")
ok(not aborta(V.pet_vigente, {"procedencia": {"ct_diag": sello(receta="r1")}}, info),
   "un PET de la misma segmentación se rechazó")
ok(V.pet_vigente(None, info) is None, "sin PET debería devolver None")

# 6) lesiones() marca lo del modelo como «lesión candidata» y lleva sello ────────────────
spec2 = importlib.util.spec_from_file_location("visor3d_b", os.path.join(RAIZ, "tools",
                                                                          "visor3d.py"))
W = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(W)
W.SALIDA_RAIZ = TMP
W.exige_zona_clinica = lambda r: None
W.serie_uid = V.serie_uid
from totalsegmentator.map_to_binary import class_map  # noqa: E402
hig = {v: k for k, v in class_map["total"].items()}["liver"]
tot = np.zeros((40, 40, 40), np.uint8)
tot[5:35, 5:35, 5:35] = hig
les = np.zeros_like(tot)
les[15:25, 15:25, 15:25] = 1
sg = np.where(tot > 0, 2, 0).astype(np.uint8)
rutas = {}
for nombre, arr in (("total", tot), ("liver_lesions", les), ("liver_segments", sg)):
    rutas[nombre] = os.path.join(TMP, "L_" + nombre + ".nii.gz")
    nib.save(nib.Nifti1Image(arr, np.eye(4)), rutas[nombre])
    V.sella_cache(rutas[nombre], {"totalsegmentator": "2.4.0", "tarea": nombre})
W.segmenta = lambda raices, serie, **kw: dict(rutas)
r = W.lesiones([], "L")
ok(r["lesiones"] and all(L["estado"] == "lesión candidata" for L in r["lesiones"]),
   "las lesiones del modelo no salen como «lesión candidata»: %s" % r["lesiones"])
ok(set(r["procedencia"]["segmentaciones"]) == set(rutas),
   "lesiones() no sella todas sus segmentaciones")

# 7) la exportación web no copia el sello (el UID no sale de zona clínica) ──────────────
import inspect  # noqa: E402
fuente_web = inspect.getsource(V._cmd_web)
ok("procedencia" not in fuente_web and "serie_uid" not in fuente_web,
   "_cmd_web toca el sello: el SeriesInstanceUID podría acabar en escena.json")

# 7b) _pet_para (lo que alimenta la web): mismo fail-closed que ficha(), nada de «continue»
def escribe(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    json.dump(datos, open(ruta, "w"))


for f in os.listdir(V._dir("pet")):
    os.remove(os.path.join(V._dir("pet"), f))
est_ruta = os.path.join(V._dir("assets", "C"), "estudio.json")
pet_ruta = os.path.join(V._dir("pet"), "P_C.json")
filas_pet = [{"id": 1, "diametro_mm": 12.0, "suvmax": 5.0}]
escribe(est_ruta, {"lesiones": [{"id": 1, "diametro_mm": 12.0}]})
escribe(pet_ruta, {"lesiones": filas_pet, "procedencia": {"ct_diag": sello(receta="r1")}})
ok(aborta(V._pet_para, "C"), "_pet_para usó un estudio.json sin sello")
escribe(est_ruta, {"lesiones": [{"id": 1, "diametro_mm": 12.0}], "procedencia": sello(receta="r1")})
escribe(pet_ruta, {"lesiones": filas_pet, "procedencia": {"ct_diag": sello(receta="r2")}})
ok(aborta(V._pet_para, "C"), "_pet_para usó un PET de otra segmentación del TC")
escribe(pet_ruta, {"lesiones": filas_pet})
ok(aborta(V._pet_para, "C"), "_pet_para usó un PET sin sello")
escribe(pet_ruta, {"lesiones": filas_pet, "procedencia": {"ct_diag": sello(receta="r1")}})
ok(not aborta(V._pet_para, "C") and V._pet_para("C")[0].get(1, {}).get("suvmax") == 5.0,
   "_pet_para rechazó un PET de la misma segmentación")

# 8) el cruce PET usa todo lo anterior. Freno ESTÁTICO (montar un PET sintético entero no
#    compensa): si alguien quita la escritura de la transformada o el sello, esto salta.
fuente_suv = inspect.getsource(V.suv_lesiones)
ok("guarda_transformada(" in fuente_suv and '"procedencia": sello' in fuente_suv
   and '"transformada": tfm' in fuente_suv,
   "suv_lesiones() ya no guarda la transformada o no sella el cruce PET")
ok("pet_vigente(" in inspect.getsource(V.ficha), "ficha() usa PET sin comprobar su sello")

if fallos:
    print("FALLOS:\n  " + "\n  ".join(fallos))
    sys.exit(1)
print("OK: procedencia sellada en compara, caché, PET y lesiones candidatas")
