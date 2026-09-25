#!/usr/bin/env python3
"""Prueba tools/visor3d.py con datos SINTÉTICOS (nada de la paciente).

Freno de muro: el inventario no puede sacar PII (se siembra un canario en un DICOM falso), y
ninguna escritura puede ir fuera de zona clínica. Además, la geometría de las medidas.

Necesita numpy/pydicom/scikit-image, que viven en `.venv-imagen`, no en el /usr/bin/python3
de test_all.sh: si no están, se relanza con la venv. Sin venv: skip (rc 77) solo en modo
portátil; en casa base es ROJO, porque un test de muro que se salta en silencio es otro agujero.
"""
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

try:
    import numpy as np  # noqa: F401
    import pydicom  # noqa: F401
    import skimage  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        env = dict(os.environ, _VISOR3D_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el freno de PII del visor no puede correr")
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


# 1) Destino fuera de zona clínica → aborta (fail-closed)
try:
    V.exige_zona_clinica(tempfile.gettempdir())
    check(False, "escribir en /tmp aborta")
except SystemExit:
    check(True, "escribir en /tmp aborta")

# 2) Inventario sin PII: DICOM sintético con canario en todos los tags identificativos
from pydicom.dataset import Dataset, FileMetaDataset  # noqa: E402
from pydicom.uid import ExplicitVRLittleEndian, generate_uid  # noqa: E402

CANARIO = "ZZCANARIO^PRUEBA"
with tempfile.TemporaryDirectory() as tmp:
    sub = os.path.join(tmp, "PET-FDG-2099-01-01", "ZZCANARIO_PRUEBA", "serie1")
    os.makedirs(sub)
    uid = generate_uid()
    for i in range(3):
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = Dataset()
        ds.file_meta = meta
        ds.PatientName = CANARIO
        ds.PatientID = "ZZCANARIO-ID-999"
        ds.PatientBirthDate = "19010101"
        ds.AccessionNumber = "ZZCANARIO-ACC"
        ds.InstitutionName = "ZZCANARIO HOSPITAL"
        ds.ReferringPhysicianName = "ZZCANARIO^DOCTOR"
        ds.Modality = "CT"
        ds.StudyDate = "20990101"
        ds.SeriesDescription = "abdomen"
        ds.SeriesInstanceUID = uid
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = meta.MediaStorageSOPClassUID
        ds.save_as(os.path.join(sub, "img%d.dcm" % i), enforce_file_format=True)
    import contextlib  # noqa: E402
    import io  # noqa: E402
    for flag in ([], ["--json"]):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            V.main(["inventario", tmp, "--min-n", "1"] + flag)
        texto = buf.getvalue().upper()
        fugas = [c for c in ("ZZCANARIO", "19010101", uid.upper()) if c in texto]
        check(not fugas and "PET-FDG-2099-01-01" in texto,
              "inventario%s sin PII (fugas: %s)" % (" --json" if flag else "", fugas))

# 3) Diámetro axial mayor de una esfera de radio 10 mm a 1 mm/vóxel ≈ 20 mm
z, y, x = np.mgrid[0:40, 0:40, 0:40]
esfera = (x - 20) ** 2 + (y - 20) ** 2 + (z - 20) ** 2 <= 10 ** 2
d = V._diametro_axial_mayor(esfera, np.array([1.0, 1.0, 1.0]))
check(19.5 <= d <= 20.5, "diámetro de esfera r=10 → %.1f mm (esperado ~20)" % d)

# 4) Emparejado: hígado desplazado 30 mm; L1 crece, L2 desaparece, aparece una nueva
a = {"centro_higado_mm": [0, 0, 0], "lesiones": [
    {"id": 1, "centro_mm": [10, 0, 0], "diametro_mm": 20, "volumen_ml": 4.0},
    {"id": 2, "centro_mm": [-40, 20, 0], "diametro_mm": 8, "volumen_ml": 0.3}]}
b = {"centro_higado_mm": [30, 0, 0], "lesiones": [
    {"id": 1, "centro_mm": [42, 1, 0], "diametro_mm": 26, "volumen_ml": 8.0},
    {"id": 2, "centro_mm": [70, -50, 30], "diametro_mm": 6, "volumen_ml": 0.1}]}
pares = V.empareja(a, b)
por_antes = {p["antes"]: p for p in pares}
check(por_antes[1]["despues"] == 1 and por_antes[1]["delta_volumen_pct"] == 100.0,
      "L1 emparejada tras compensar el desplazamiento del hígado (+100 % volumen)")
check(por_antes[2]["despues"] is None, "L2 sin pareja (desaparecida)")
check({"antes": None, "despues": 2} in pares, "lesión nueva marcada como nueva")
# Caso real (s.IVb, 19-sep): mismo segmento a 17,4 mm se empareja; otro segmento a 17,4 no.
c = {"centro_higado_mm": [0, 0, 0], "lesiones": [
    {"id": 1, "centro_mm": [0, 0, 0], "diametro_mm": 11, "volumen_ml": 0.8, "segmento": 4}]}
d_ = {"centro_higado_mm": [0, 0, 0], "lesiones": [
    {"id": 1, "centro_mm": [17.4, 0, 0], "diametro_mm": 14, "volumen_ml": 1.2, "segmento": 4}]}
check(V.empareja(c, d_)[0]["despues"] == 1, "mismo segmento a 17,4 mm → emparejada")
d_["lesiones"][0]["segmento"] = 7
check(V.empareja(c, d_)[0]["despues"] is None, "otro segmento a 17,4 mm → sin pareja")

# 5) Assets: NIfTI sin nada en la cabecera salvo imagen; PLY sin comentarios
import gzip  # noqa: E402

import nibabel as nib  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    img = V.nifti_limpio(np.zeros((4, 4, 4)), np.eye(4), np.int16)
    ruta = os.path.join(tmp, "x.nii.gz")
    nib.save(img, ruta)
    h = nib.load(ruta).header
    check(bytes(h["descrip"]).strip(b"\x00") == b"" and bytes(h["aux_file"]).strip(b"\x00") == b""
          and len(h.extensions) == 0, "cabecera NIfTI limpia (descrip/aux_file/extensiones)")
    crudo = gzip.open(ruta).read()
    check(b"ZZCANARIO" not in crudo and b"TotalSegmentator" not in crudo, "NIfTI sin texto ajeno")
    v, f = V._malla(esfera, np.eye(4))
    V.ply_binario(os.path.join(tmp, "m.ply"), v, f)
    cab = open(os.path.join(tmp, "m.ply"), "rb").read(400).split(b"end_header")[0]
    check(b"comment" not in cab and b"obj_info" not in cab, "PLY sin comentarios ni obj_info")
    check(abs(float(np.ptp(v[:, 0])) - 20) < 2, "malla de la esfera con ~20 mm de ancho")

# 6) Proporción (19-sep, «hazlas en proporción»): con el suavizado proporcional al tamaño,
#    una lesión de 4 a 16 mm sale en la malla a ±1,2 mm de su diámetro real (sin inflar ni encoger)
for diam in (4, 6, 10, 16):
    r = diam / 2.0
    zz, yy, xx = np.mgrid[0:30, 0:30, 0:30]
    esf = (xx - 15) ** 2 + (yy - 15) ** 2 + (zz - 15) ** 2 <= r ** 2
    sigma = min(1.2, max(0.3, diam / 10.0))
    v, f = V._malla(esf, np.eye(4), sigma, sobremuestreo=2)
    ancho = float(np.ptp(v[:, 0]))
    check(abs(ancho - diam) <= 1.2, "lesión de %d mm → malla de %.1f mm (±1,2)" % (diam, ancho))

# 7) Lesiones < 8 mm: elipsoide ajustado (sin cubos ni cruces), del tamaño real ±1 mm
for diam in (3, 5, 7):
    r = diam / 2.0
    zz, yy, xx = np.mgrid[0:20, 0:20, 0:20]
    esf = (xx - 10) ** 2 + (yy - 10) ** 2 + (zz - 10) ** 2 <= r ** 2
    v, f = V._elipsoide(esf, np.eye(4), [1.0, 1.0, 1.0])
    ancho = float(np.ptp(v[:, 0]))
    check(abs(ancho - diam) <= 1.0 and len(f) == 1280,
          "lesión de %d mm → elipsoide de %.1f mm (±1, liso)" % (diam, ancho))
    v2, _ = V._elipsoide(esf, np.eye(4), [1.0, 1.0, 1.0], diametro_mm=diam)
    check(abs(float(np.ptp(v2[:, 0])) - diam) <= 0.15,
          "  calibrado a la medida: %.2f mm" % float(np.ptp(v2[:, 0])))

# 8) web (19-sep): la simplificación para la web reduce la malla, no deja caras degeneradas
#    ni mueve la forma más que la rejilla; y el PLY vuelve a leerse igual.
zz, yy, xx = np.mgrid[0:40, 0:40, 0:40]
esf = (xx - 20) ** 2 + (yy - 20) ** 2 + (zz - 20) ** 2 <= 15 ** 2
v, f = V._malla(esf, np.eye(4), 1.0)
v2, f2 = V._agrupa_vertices(v, f, 1.8)
check(len(v2) < len(v) / 2, "simplificar a 1,8 mm reduce la malla (%d → %d vértices)" % (len(v), len(v2)))
check(bool(((f2[:, 0] != f2[:, 1]) & (f2[:, 1] != f2[:, 2]) & (f2[:, 0] != f2[:, 2])).all()),
      "  sin caras degeneradas")
check(abs(float(np.ptp(v2[:, 0])) - float(np.ptp(v[:, 0]))) <= 1.8, "  la forma no se mueve más que la rejilla")
with tempfile.TemporaryDirectory() as tmp:
    V.ply_binario(os.path.join(tmp, "w.ply"), v2, f2)
    v3, f3 = V._lee_ply_caras(os.path.join(tmp, "w.ply"))
    check(len(v3) == len(v2) and (f3 == f2).all(), "  el PLY de la web se relee igual")

# 9) Venas troceadas (19-sep, «las venas no están como discontinuas?»): el cierre une una
#    rama de 4 mm cortada por un hueco de 3 mm, NO une un hueco de 5 mm (no se inventa
#    conexiones largas), y con solo_conectados quita la isla que no toca ningún tronco.
from scipy import ndimage  # noqa: E402


def _rama(hueco):
    lab = np.zeros((40, 40, 60), np.uint8)
    lab[5:35, 5:35, 5:55] = V.ETIQUETAS["higado"]
    lab[17:23, 17:23, 5:12] = V.ETIQUETAS["vci"]             # tronco
    lab[18:22, 18:22, 12:25] = V.ETIQUETAS["vasos"]          # rama de 4 mm pegada al tronco
    lab[18:22, 18:22, 25 + hueco:40] = V.ETIQUETAS["vasos"]  # la misma rama tras el hueco
    lab[8:12, 28:32, 45:50] = V.ETIQUETAS["vasos"]           # isla lejos de todo
    return lab


lab = _rama(3)
check(ndimage.label((lab == V.ETIQUETAS["vasos"])[14:26, 14:26, 12:40])[1] == 2,
      "sin cierre, la rama sale en 2 trozos")
con = V._cierra_vasos(lab, 1.0, 4)
check(ndimage.label(con[14:26, 14:26, 12:40])[1] == 1, "  cierre de 4 mm: hueco de 3 mm unido")
check(ndimage.label(V._cierra_vasos(_rama(5), 1.0, 4)[14:26, 14:26, 12:40])[1] == 2,
      "  hueco de 5 mm: NO se une (no inventa conexiones largas)")
check(bool(con[8:12, 28:32, 45:50].any()), "  sin solo_conectados, la isla se queda")
con2 = V._cierra_vasos(lab, 1.0, 4, solo_conectados=True)
check(not con2[8:12, 28:32, 45:50].any() and bool(con2[18:22, 18:22, 30].any()),
      "  con solo_conectados, la isla se va y la rama se queda")


# ── mama: la piel no puede llegar a la malla, y sin receta no hay malla ──────────────────
# El comité de diseño levantó su veto a publicar la mama con UNA condición: que «sin piel» sea
# comprobable, no una promesa de estilo. Esto es esa comprobación.

# 12) Las rutas por defecto siguen siendo las del hígado tras meter --organo
check(V.ORGANO == "higado", "el órgano por defecto sigue siendo el hígado")
check(V._dir("assets", "x") == os.path.join(V.SALIDA_RAIZ, "higado", "assets", "x")
      and V._dir() == os.path.join(V.SALIDA_RAIZ, "higado"),
      "  _dir() resuelve donde resolvían los 16 literales")

# 13) sin_piel() quita milímetros, no vóxeles: con espaciado anisótropo también
esp = (1.0, 1.0, 2.0)
cuerpo_m = np.zeros((60, 60, 40), bool)
cuerpo_m[10:50, 10:50, 5:35] = True
dentro = V.sin_piel(cuerpo_m, esp, 4.0)
borde = ndimage.distance_transform_edt(cuerpo_m, sampling=esp)
check(dentro.sum() > 0, "sin_piel deja cuerpo")
check(float(borde[dentro].min()) >= 4.0 - 1e-6,
      "  ningún vóxel publicado queda a menos de 4 mm de la piel")
check(float(borde[dentro].min()) >= 4.0 - 1e-6 and not dentro[:, :, 5:7].any(),
      "  la erosión es en mm, no en vóxeles (eje de 2 mm también se recorta)")

# 13b) La apertura del pezón: quita el saliente y NO se come la cúpula
# La envoltura de la mama sale del cuerpo SIN erosionar (si no, no habría superficie), así que
# el pezón entra con ella. Una apertura morfológica con una bola de r mm borra, por definición,
# los salientes más finos que la bola. Esto lo comprueba sobre una cúpula sintética con pezón.
forma = (90, 90, 90)
g3 = np.indices(forma).astype(float)
c3 = np.array([45.0, 45.0, 45.0])
cupula = np.sqrt(((g3[0] - c3[0])) ** 2 + ((g3[1] - c3[1])) ** 2 + ((g3[2] - c3[2])) ** 2) <= 28
# pezón: cilindro de 8 mm de ancho que sobresale 9 mm del polo (+x)
rad = np.sqrt((g3[1] - c3[1]) ** 2 + (g3[2] - c3[2]) ** 2)
pezon = (rad <= 4) & (g3[0] >= c3[0] + 26) & (g3[0] <= c3[0] + 37)
con_pezon = cupula | pezon
bola9 = V._bola_estructura((1.0, 1.0, 1.0), 9.0)
abierto = ndimage.binary_opening(con_pezon, structure=bola9)
check(int(con_pezon[:, 45, 45].nonzero()[0].max()) > int(cupula[:, 45, 45].nonzero()[0].max()),
      "la cúpula sintética SÍ tenía pezón (sobresale del polo)")
check(int(abierto[:, 45, 45].nonzero()[0].max()) <= int(cupula[:, 45, 45].nonzero()[0].max()),
      "  tras la apertura de 9 mm, el saliente ya no está")
conserva = (abierto & cupula).sum() / max(1, cupula.sum())
check(conserva > 0.85, "  y la cúpula se conserva (%.0f %% del volumen)" % (conserva * 100))

# 14) crece_tumor() recupera el diámetro de una esfera que realza
afin = np.diag([0.8, 0.8, 1.0, 1.0])
forma = (80, 80, 60)
g = np.indices(forma).astype(float)
centro = np.array([40, 40, 30])
d = np.sqrt(((g[0] - centro[0]) * 0.8) ** 2 + ((g[1] - centro[1]) * 0.8) ** 2
            + ((g[2] - centro[2]) * 1.0) ** 2)
real = np.where(d <= 7.5, 100.0, 5.0).astype(np.float32)     # esfera de 15 mm
semilla = (afin @ np.append(centro, 1.0))[:3]
mask, _ = V.crece_tumor(real, afin, semilla, 0.5, 25)
dm = V._diametro_axial_mayor(mask, [0.8, 0.8, 1.0])
check(abs(dm - 15.0) <= 1.0, "crece_tumor recupera 15 mm de una esfera (%.1f)" % dm)
dm30 = V._diametro_axial_mayor(V.crece_tumor(real, afin, semilla, 0.3, 25)[0], [0.8, 0.8, 1.0])
check(abs(dm30 - 15.0) <= 1.5, "  y con f=0,30 también (%.1f), no depende del radio de la bola"
      % dm30)
try:
    V.crece_tumor(real, afin, semilla, 60, 25)
    check(False, "una fracción de 60 (percentil por error) aborta")
except SystemExit:
    check(True, "una fracción de 60 (percentil por error) aborta")

# 15) Receta incompleta → aborta antes de escribir nada
try:
    V.assets_mama(["/no/existe"], "a", "b", "c", {"semilla_mm": [0, 0, 0]}, V._dir("assets", "x"))
    check(False, "receta sin _fuente aborta")
except SystemExit as e:
    check("_fuente" in str(e), "receta sin _fuente aborta, y dice qué falta")

# 16) La semilla fuera del volumen aborta en vez de recortar en silencio
try:
    V.crece_tumor(real, afin, [9999, 0, 0], 0.5, 25)
    check(False, "semilla fuera del volumen aborta")
except SystemExit:
    check(True, "semilla fuera del volumen aborta")

print("\nVEREDICTO: %s" % ("TODO CORRECTO" if not fallos else "%d FALLOS" % len(fallos)))
sys.exit(1 if fallos else 0)
