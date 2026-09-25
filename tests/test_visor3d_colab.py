#!/usr/bin/env python3
"""Vía Colab de tools/visor3d.py con datos SINTÉTICOS (nada de la paciente, sin red, sin GPU).

Lo que se congela (24-sep-2026):
  1. Sin decisión sobre la cabeza (--corta-z-mm o --con-cabeza) no sale nada.
  2. Lo que sale es un NIfTI sin texto ni extensiones, y ningún valor identificativo de la serie
     aparece en su cabecera ni en el manifiesto; si aparece, se borra y aborta.
  3. Serie con texto quemado en la imagen: no sale.
  4. El recorte quita lo de ENCIMA del corte y el manifiesto guarda dónde empezaba.
  5. Las celdas REALES del cuaderno, con Colab/pip/torch/TotalSegmentator de mentira, producen un
     zip que importa-colab acepta, y lo devuelve a la geometría original.
  6. importa-colab rechaza otra versión, otro volumen, ficheros de más, otra forma u otra geometría.
  7. segmenta() reutiliza lo importado SIN llamar a TotalSegmentator (mismos parámetros).
  8. El cuaderno se publica: sin rutas de usuario ni nombres.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")
try:
    import numpy as np
    import nibabel as nib
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)],
                                 env=dict(os.environ, _VISOR3D_REEXEC="1")))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; la vía Colab no puede probarse")
    sys.exit(1)

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

TMP = os.path.realpath(tempfile.mkdtemp(prefix="visor3d_colab_"))
V.SALIDA_RAIZ = os.path.join(TMP, "visor3d")
V.exige_zona_clinica = lambda r: None
V.exige_telemetria_apagada = lambda: None
V.serie_uid = lambda raices, serie: "1.2.826.0.1.3680043.2.99999." + serie
PII = V._trozos_pii(["PRUEBA^PACIENTE", "19700101", "HOSPITAL FICTICIO", "1.2.3.4.5.6.7"])
V._pii_de_serie = lambda raices, serie: PII
VERSION = V._version_ts()
fallos = []


def ok(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        fallos.append(msg)


def aborta(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SystemExit as e:
        return str(e) or True
    return False


def volumen(nombre, paso_z, origen_z, forma=(12, 10, 40)):
    datos = (np.arange(np.prod(forma)).reshape(forma) % 300).astype(np.int16)
    afin = np.diag([2.0, 2.0, paso_z, 1.0])
    afin[2, 3] = origen_z
    ruta = os.path.join(TMP, nombre)
    nib.save(nib.Nifti1Image(datos, afin), ruta)
    return ruta


# Volumen «de pies a cabeza»: z = 17 - 3k (k=0 es lo más craneal, como suele salir el TC).
SRC = volumen("src.nii.gz", -3.0, 17.0)
META = {"modalidad": "CT", "texto_quemado": False}
V.convierte = lambda raices, serie: (SRC, dict(META))
RAICES = ["/no/importa"]

# 1) sin decisión sobre la cabeza no sale nada
ok(aborta(V.exporta_colab, RAICES, "s1"), "sin --corta-z-mm ni --con-cabeza: aborta")
ok(not os.path.exists(os.path.join(V.SALIDA_RAIZ, "colab", "s1", "volumen.nii.gz")),
   "  y no deja fichero")

# 3) texto quemado
V.convierte = lambda raices, serie: (SRC, dict(META, texto_quemado=True))
ok(aborta(V.exporta_colab, RAICES, "s1", corta_z_mm=0.0), "serie con texto quemado: aborta")
V.convierte = lambda raices, serie: (SRC, dict(META))

# 4) recorte + 2) cabecera limpia
out, man = V.exporta_colab(RAICES, "s1", corta_z_mm=0.0, preset="higado",
                           tareas=["total", "liver_segments"])
img = nib.load(out)
zs = [(img.affine @ np.array([0, 0, k, 1.0]))[2] for k in range(img.shape[2])]
ok(max(zs) <= 0.0 + 1e-6, "el recorte quita todo lo que está por encima de z=0 (máx %.1f)" % max(zs))
ok(man["k0"] == 6 and img.shape[2] == 34, "  y guarda dónde empezaba (k0=%s, %s planos)"
   % (man["k0"], img.shape[2]))
ok(man["forma_original"] == [12, 10, 40] and not man["con_cabeza"], "  manifiesto con la forma original")
ok(np.array_equal(np.asanyarray(img.dataobj), np.asanyarray(nib.load(SRC).dataobj)[:, :, 6:]),
   "  los vóxeles que salen son los del original")
ok(V.verifica_sin_pii(out, PII) is True, "la cabecera exportada está limpia")
txt = open(os.path.join(os.path.dirname(out), "manifiesto.json")).read()
ok(not any(v.lower() in txt.lower() for v in PII), "el manifiesto no lleva nada identificativo")
ok(man["totalsegmentator"] == VERSION and man["sha256_volumen"] == V._sha256_completo(out),
   "el manifiesto fija versión y sha256")

# 2b) mutantes de PII: texto en descrip, una extensión, y la exportación que lo plantaría
sucio = nib.load(out)
sucio.header["descrip"] = b"paciente prueba"
p_sucio = os.path.join(TMP, "sucio.nii.gz")
nib.save(sucio, p_sucio)
ok(aborta(V.verifica_sin_pii, p_sucio, PII), "texto en descrip: la comprobación aborta")
ext = nib.Nifti1Image(np.zeros((2, 2, 2), np.int16), np.eye(4))
ext.header.extensions.append(nib.nifti1.Nifti1Extension(6, b"PRUEBA^PACIENTE"))
p_ext = os.path.join(TMP, "ext.nii.gz")
nib.save(ext, p_ext)
ok(aborta(V.verifica_sin_pii, p_ext, PII), "una extensión NIfTI: la comprobación aborta")
limpio_orig = V.nifti_limpio


def limpio_con_fuga(datos, afin, dtype):
    i = limpio_orig(datos, afin, dtype)
    i.header["aux_file"] = b"HOSPITAL"
    return i


V.nifti_limpio = limpio_con_fuga
ok(aborta(V.exporta_colab, RAICES, "s2", corta_z_mm=0.0), "si lo exportado lleva PII: aborta")
ok(not os.path.exists(os.path.join(V.SALIDA_RAIZ, "colab", "s2", "volumen.nii.gz")),
   "  y borra el fichero")
V.nifti_limpio = limpio_orig

# 5) las celdas REALES del cuaderno, con dobles de Colab, pip, torch y TotalSegmentator
NB = os.path.join(RAIZ, "tools", "colab", "visor3d_totalseg.ipynb")
nb = json.load(open(NB))
celdas = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
CONTENT = os.path.join(TMP, "content")
os.makedirs(CONTENT)
llamadas = {"ts": [], "descarga": [], "unassign": 0, "pip": []}


def fake_upload():
    d = os.path.dirname(out)
    for n in ("volumen.nii.gz", "manifiesto.json"):
        shutil.copy(os.path.join(d, n), n)
    return {"volumen.nii.gz": b"", "manifiesto.json": b""}


def fake_ts(input, output, task, device, quiet, **kw):
    llamadas["ts"].append((task, device, kw))
    im = nib.load(input)
    mask = (np.asanyarray(im.dataobj) % 7).astype(np.uint8)
    nib.save(nib.Nifti1Image(mask, im.affine), output)


cfg = {}
colab = types.ModuleType("google.colab")
colab.files = types.SimpleNamespace(upload=fake_upload,
                                    download=lambda n: llamadas["descarga"].append(n))
colab.runtime = types.SimpleNamespace(unassign=lambda: llamadas.__setitem__("unassign", 1))
google = types.ModuleType("google")
google.colab = colab
tsp = types.ModuleType("totalsegmentator")
tsc = types.ModuleType("totalsegmentator.config")
tsc.setup_totalseg = lambda: cfg.setdefault("send_usage_stats", True)
tsc.set_config_key = lambda k, v: cfg.__setitem__(k, v)
tsc.get_config_key = lambda k: cfg.get(k)
tsa = types.ModuleType("totalsegmentator.python_api")
tsa.totalsegmentator = fake_ts
torch_f = types.ModuleType("torch")
torch_f.cuda = types.SimpleNamespace(is_available=lambda: True)
dobles = {"google": google, "google.colab": colab, "totalsegmentator": tsp,
          "totalsegmentator.config": tsc, "totalsegmentator.python_api": tsa, "torch": torch_f}
guardados = {k: sys.modules.get(k) for k in dobles}
run_orig, cwd_orig = subprocess.run, os.getcwd()


def fake_run(cmd, *a, **kw):
    llamadas["pip"].append(cmd)
    return types.SimpleNamespace(returncode=0)


sys.modules.update(dobles)
subprocess.run = fake_run
ns = {}
try:
    for i, c in enumerate(celdas):
        c = c.replace("MODO_ENSAYO = True", "MODO_ENSAYO = False").replace("/content", CONTENT)
        if i == len(celdas) - 1:
            zip_hecho = os.path.join(TMP, "mascaras.zip")
            shutil.copy(os.path.join(CONTENT, "trabajo", ns["nombre_zip"]), zip_hecho)
        exec(compile(c, "celda%d" % i, "exec"), ns)
finally:
    subprocess.run = run_orig
    os.chdir(cwd_orig)
    for k, v in guardados.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
ok(llamadas["pip"] and ("TotalSegmentator==" + VERSION) in llamadas["pip"][0],
   "el cuaderno instala EXACTAMENTE la versión del Mac (%s)" % VERSION)
ok(cfg.get("send_usage_stats") is False, "el cuaderno apaga la telemetría")
ok([t for t, _d, _k in llamadas["ts"]] == ["total", "liver_segments"]
   and all(d == "gpu" for _t, d, _k in llamadas["ts"]), "segmenta cada tarea en la GPU")
ok(llamadas["ts"][0][2].get("roi_subset") and llamadas["ts"][0][2].get("ml") is True,
   "  con los parámetros del manifiesto (roi_subset, ml)")
ok(llamadas["descarga"] == ["mascaras_s1.zip"], "descarga el zip")
ok(llamadas["unassign"] == 1 and not os.path.exists(os.path.join(CONTENT, "trabajo")),
   "la última celda borra todo y suelta la máquina")

# 6) importa: lo bueno entra, devuelto a la geometría original y sellado
hechas = V.importa_colab(RAICES, "s1", zip_hecho)
m = nib.load(hechas["total"])
orig = nib.load(SRC)
ok(m.shape == orig.shape and np.allclose(m.affine, orig.affine), "importa devuelve la geometría original")
dm = np.asanyarray(m.dataobj)
ok(not dm[:, :, :6].any() and np.array_equal(dm[:, :, 6:],
   (np.asanyarray(img.dataobj) % 7).astype(np.uint8)), "  con la máscara en su sitio y ceros arriba")
sello = json.load(open(hechas["total"] + ".sello.json"))
ok(sello["receta"]["origen"] == "colab" and sello["receta"]["totalsegmentator"] == VERSION,
   "  sellada como origen colab con la versión")


def zip_mod(nombre, cambia=None, quita=None, extra=None):
    ruta = os.path.join(TMP, nombre)
    with zipfile.ZipFile(zip_hecho) as zi, zipfile.ZipFile(ruta, "w") as zo:
        for n in zi.namelist():
            if n == quita:
                continue
            dato = zi.read(n)
            if cambia and n in cambia:
                dato = cambia[n](dato)
            zo.writestr(n, dato)
        for n, dato in (extra or {}).items():
            zo.writestr(n, dato)
    return ruta


def version_json(**campos):
    return lambda b: json.dumps(dict(json.loads(b), **campos)).encode()


def mascara_rara(forma=None, afin=None):
    def f(_b):
        p = os.path.join(TMP, "rara.nii.gz")
        base = nib.load(out)
        sh = forma or base.shape
        nib.save(nib.Nifti1Image(np.zeros(sh, np.uint8),
                                 base.affine if afin is None else afin), p)
        return open(p, "rb").read()
    return f


ok(aborta(V.importa_colab, RAICES, "s1", zip_mod("v.zip", {"version.json": version_json(totalsegmentator="0.0.1")})),
   "importa rechaza otra versión de TotalSegmentator")
ok(aborta(V.importa_colab, RAICES, "s1", zip_mod("sha.zip", {"version.json": version_json(sha256_volumen="x" * 64)})),
   "importa rechaza máscaras de OTRO volumen")
ok(aborta(V.importa_colab, RAICES, "s1", zip_mod("extra.zip", extra={"../fuera.nii.gz": b"x"})),
   "importa rechaza ficheros que no se pidieron")
ok(aborta(V.importa_colab, RAICES, "s1", zip_mod("falta.zip", quita="liver_segments.nii.gz")),
   "importa rechaza un zip incompleto")
ok(aborta(V.importa_colab, RAICES, "s1", zip_mod("forma.zip", {"total.nii.gz": mascara_rara(forma=(12, 10, 5))})),
   "importa rechaza otra forma")
ok(aborta(V.importa_colab, RAICES, "s1", zip_mod("afin.zip", {"total.nii.gz": mascara_rara(afin=np.eye(4))})),
   "importa rechaza otra geometría")
hechas = V.importa_colab(RAICES, "s1", zip_hecho)          # deja la buena otra vez

# 7) segmenta reutiliza lo de Colab sin llamar a TotalSegmentator
prohibido = types.ModuleType("totalsegmentator.python_api")


def no_me_llames(**kw):
    raise AssertionError("segmenta llamó a TotalSegmentator: no reutilizó lo de Colab")


prohibido.totalsegmentator = no_me_llames
guard = {k: sys.modules.get(k) for k in ("totalsegmentator", "totalsegmentator.python_api")}
sys.modules["totalsegmentator"] = tsp
sys.modules["totalsegmentator.python_api"] = prohibido
try:
    try:
        r = V.segmenta(RAICES, "s1", preset="higado", tareas=["total", "liver_segments"])
        ok(set(r) == {"total", "liver_segments"}, "segmenta reutiliza lo importado de Colab sin recalcular")
    except AssertionError as e:
        ok(False, str(e))
finally:
    for k, v in guard.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
ok(not V.cache_vale(hechas["total"], dict(sello["receta"], origen=None, totalsegmentator="9.9")),
   "cache_vale no acepta lo de Colab para otra versión")

# 8) el cuaderno se publica
texto_nb = open(NB, encoding="utf-8").read()
ok(not any(x in texto_nb for x in ("/Users/", "claudecode", "_PRIVADO", "@")),
   "el cuaderno no lleva rutas de usuario, zona clínica ni correos")

shutil.rmtree(TMP, ignore_errors=True)
print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
