#!/usr/bin/env python3
"""tools/visor3d.py — Del DICOM al mapa 3D de un órgano (hígado primero), 100 % local.

Por qué existe: el mapa óseo de junio se hizo a mano (Colab + un /tmp/render_pet.py que se
perdió) y la segmentación hepática del 17-sep tampoco dejó código ni traza. Cada órgano nuevo
repetía el coste. Esto lo convierte en un pipeline versionado, con etapas separadas:

  inventario  → qué series hay (modalidad, fecha, fase de contraste), sin volcar PII
  convierte   → serie DICOM → NIfTI (SimpleITK)
  segmenta    → TotalSegmentator (tareas del preset del órgano), con caché por serie
  mide        → lesiones: diámetro axial mayor, volumen, segmento, SUV; emparejado entre fechas
  assets      → volúmenes recortados al órgano, mallas PLY, JSON de lesiones, para la web

EL MURO:
  · Se lanza SOLO por la ventanilla: `python3 tools/lector_clinico.py procesa visor3d -- …`
    (el guard deniega llamarlo directo sobre rutas clínicas, y así queda registro).
  · Toda escritura va a zona clínica (`_PRIVADO_CLINICO/visor3d/`); cualquier otro destino
    aborta. Nada sale del Mac: la telemetría de TotalSegmentator debe estar apagada y se
    comprueba antes de segmentar.
  · Nunca imprime ni escribe nombre, IDs, fecha de nacimiento, institución ni UIDs en claro:
    la identidad del paciente se compara por huella (hash corto), no se muestra.
  · Este código se publica en el espejo: sin rutas con usuario ni nombres propios.

Intérprete: `.venv-imagen` (pydicom, SimpleITK, nibabel, scikit-image, TotalSegmentator).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time

# ─── techo del heap de Metal ───────────────────────────────────────────────────────────────
# Tiene que estar puesto ANTES de que nadie importe torch: el allocator de MPS lee esto al
# nacer y ya no lo suelta. Sin límite, PyTorch reserva hasta 1,7× la memoria que Metal
# «recomienda» (en un Mac de 16 GB, ~18 GB) y la retiene aunque no la use. Medido el 20-sep-26:
# con el trabajo real ocupando 3,2 GB de RSS, el footprint se quedaba clavado en 9,8 GB y el
# sistema empujaba 3 GB al swap para hacerle sitio a una reserva vacía.
_MPS_RATIO = float(os.environ.get("BTP_MPS_RATIO", "0.4"))
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "%g" % _MPS_RATIO)
# y el bajo TIENE que quedar por debajo del alto, o torch aborta al mover la red a la GPU
# («invalid low watermark ratio 1.4», que es el valor que trae de fábrica).
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "%g" % (_MPS_RATIO * 0.85))

_TOOLS = os.path.dirname(os.path.abspath(__file__))
# tools/queue.py sombrea la `queue` de la stdlib que usan torch/requests (mismo arreglo que
# postdicom.py): se quita tools/ del path antes de importar nada pesado.
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != _TOOLS]

REPO = os.environ.get("BTP_REPO") or os.path.join(os.path.expanduser("~"), "claudecode")
SALIDA_RAIZ = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "_PRIVADO_CLINICO",
                           "visor3d")

# Órgano activo. Las 16 rutas de trabajo colgaban de un literal "higado" repetido; ahora salen
# todas de `_dir()`, que es la única costura que hay que mover para un órgano nuevo. El valor por
# defecto deja las rutas del hígado EXACTAMENTE donde estaban (tests/test_visor3d.py lo comprueba).
ORGANO = "higado"


def _dir(*partes):
    """Carpeta de trabajo del órgano activo, bajo la zona clínica."""
    return os.path.join(SALIDA_RAIZ, ORGANO, *partes)


# Presets por órgano: qué tareas de TotalSegmentator y qué clases forman el mapa.
PRESETS = {
    "higado": {
        "organo": "liver",
        "tareas": ["total", "liver_segments", "liver_lesions", "liver_vessels"],
        "roi_total": ["liver", "inferior_vena_cava", "portal_vein_and_splenic_vein",
                      "gallbladder"],
        "margen_mm": 20,
        "modalidad": "TC",
    },
    # La mama no trae tareas de TotalSegmentator, y no es un olvido: no existe modelo de lesión
    # de mama en RM. El tumor sale de la resta del dinámico y la mama del Dixon (assets_mama).
    # El esqueleto sale del TC de cuerpo entero con kernel de hueso: las 117 clases de
    # TotalSegmentator traen las 25 vértebras una a una, las costillas y el resto del hueso.
    "esqueleto": {
        "organo": "skeleton",
        "tareas": ["total_completo"],
        "roi_total": [],
        "margen_mm": 0,
        "modalidad": "TC",
    },
    "mama": {
        "organo": "breast",
        "tareas": [],
        "roi_total": [],
        "margen_mm": 10,
        "modalidad": "RM",
    },
}


# ─── muro ────────────────────────────────────────────────────────────────────────────────

def _zonas():
    ruta = os.path.join(os.path.dirname(_TOOLS), ".claude", "hooks", "zonas_clinicas.py")
    spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def exige_zona_clinica(ruta):
    """Fail-closed: si el destino no es zona clínica, no se escribe nada."""
    if not _zonas().es_ruta_clinica(os.path.realpath(ruta)):
        raise SystemExit("ABORTA: %s no es zona clínica; el visor solo escribe en zona clínica."
                         % ruta)


def exige_telemetria_apagada():
    cfg = os.path.expanduser("~/.totalsegmentator/config.json")
    try:
        activa = json.load(open(cfg)).get("send_usage_stats", True)
    except (OSError, ValueError):
        activa = True
    if activa:
        raise SystemExit("ABORTA: TotalSegmentator tiene send_usage_stats activo (%s). "
                         "Apágalo antes de segmentar datos clínicos." % cfg)


def huella(*partes):
    """Hash corto y estable para comparar identidades sin mostrarlas."""
    h = hashlib.sha256("|".join(str(p or "") for p in partes).encode("utf-8"))
    return h.hexdigest()[:8]


# ─── inventario ──────────────────────────────────────────────────────────────────────────

def _lee_cabecera(ruta, forzar=False):
    """Cabecera DICOM o None. `forzar` lee ficheros sin preámbulo (CD con FILESET/DICOMDIR)."""
    import pydicom
    try:
        return pydicom.dcmread(ruta, stop_before_pixels=True, force=forzar)
    except Exception:
        return None


def ilegibles(raices):
    """Por carpeta de estudio: ficheros que NO se leen como DICOM estricto y cuántos de ellos sí
    se leen forzando. Sirve para saber si a un CD le «faltan» series o es que no se leyeron."""
    cuenta = {}
    for raiz in raices:
        for d, _, ficheros in os.walk(raiz):
            estudio = os.path.relpath(d, raiz).split(os.sep)[0]
            for f in ficheros:
                if f.startswith("."):
                    continue
                r = os.path.join(d, f)
                if _lee_cabecera(r) is not None:
                    continue
                c = cuenta.setdefault(estudio, {"ilegibles": 0, "legibles_forzando": 0,
                                                "modalidades_forzando": {}})
                c["ilegibles"] += 1
                ds = _lee_cabecera(r, forzar=True)
                if ds is not None and "SeriesInstanceUID" in ds:
                    c["legibles_forzando"] += 1
                    m = str(ds.get("Modality", "?"))
                    c["modalidades_forzando"][m] = c["modalidades_forzando"].get(m, 0) + 1
    return cuenta


def inventario(raices):
    """Agrupa por serie. Devuelve lista de dicts SIN PII (paciente = huella)."""
    series = {}
    for raiz in raices:
        for d, _, ficheros in os.walk(raiz):
            for f in ficheros:
                if f.startswith("."):
                    continue
                ds = _lee_cabecera(os.path.join(d, f))
                if ds is None or "SeriesInstanceUID" not in ds:
                    continue
                uid = str(ds.SeriesInstanceUID)
                s = series.get(uid)
                if s is None:
                    s = series[uid] = {
                        "serie": huella(uid),
                        "paciente": huella(ds.get("PatientName"), ds.get("PatientBirthDate")),
                        "modalidad": str(ds.get("Modality", "")),
                        "fecha": str(ds.get("StudyDate", "")),
                        "descripcion": str(ds.get("SeriesDescription", ""))[:60],
                        "contraste": str(ds.get("ContrastBolusAgent", ""))[:30],
                        "grosor_mm": str(ds.get("SliceThickness", "")),
                        "tipo": "\\".join(ds.get("ImageType", []))[:40],
                        # Horas (no identifican): ordenan las fases de un dinámico de RM.
                        "parte": str(ds.get("BodyPartExamined", ""))[:20],
                        # La POSTURA decide si dos estudios se pueden comparar punto a punto:
                        # HFS = boca arriba, HFP = boca abajo. Una mama en prono y la misma en
                        # supino no tienen la misma forma ni de lejos.
                        "postura": str(ds.get("PatientPosition", ""))[:6],
                        "protocolo": str(ds.get("ProtocolName", ""))[:40],
                        "desc_estudio": str(ds.get("StudyDescription", ""))[:40],
                        "hora_serie": str(ds.get("SeriesTime", ""))[:6],
                        "hora_adq": str(ds.get("AcquisitionTime", ""))[:6],
                        "hora_contraste": str(ds.get("ContrastBolusStartTime", ""))[:6],
                        "n": 0,
                        # Solo la carpeta de estudio (1er nivel): los niveles de debajo los
                        # nombra el hospital y llevan nombre y nº de historia.
                        "estudio": os.path.relpath(d, raiz).split(os.sep)[0],
                        "_ruta": d,
                    }
                s["n"] += 1
    return sorted(series.values(), key=lambda s: (s["fecha"], s["modalidad"], -s["n"]))


def dicomdir(raices):
    """Series que ANUNCIA cada DICOMDIR (índice del CD) y cuántas de sus imágenes están en disco.
    Distingue «el CD no lo traía» de «no se copió»."""
    import pydicom
    out = []
    for raiz in raices:
        for d, _, ficheros in os.walk(raiz):
            if "DICOMDIR" not in ficheros:
                continue
            try:
                dd = pydicom.dcmread(os.path.join(d, "DICOMDIR"))
            except Exception as e:
                out.append({"estudio": os.path.relpath(d, raiz).split(os.sep)[0], "error": repr(e)})
                continue
            estudio = os.path.relpath(d, raiz).split(os.sep)[0]
            fecha, actual = "", None
            for r in dd.get("DirectoryRecordSequence", []):
                tipo = str(r.get("DirectoryRecordType", "")).upper()
                if tipo == "STUDY":
                    fecha = str(r.get("StudyDate", ""))
                elif tipo == "SERIES":
                    actual = {"estudio": estudio, "fecha": fecha,
                              "modalidad": str(r.get("Modality", "")),
                              "descripcion": str(r.get("SeriesDescription", ""))[:50],
                              "imagenes": 0, "en_disco": 0}
                    out.append(actual)
                elif actual is not None and "ReferencedFileID" in r:
                    fid = r.ReferencedFileID
                    partes = [fid] if isinstance(fid, str) else list(fid)
                    actual["imagenes"] += 1
                    actual["en_disco"] += os.path.exists(os.path.join(d, *partes))
    return out


def _cmd_dicomdir(a):
    for f in dicomdir(a.raices):
        if "error" in f:
            print(f["estudio"], "ERROR", f["error"])
            continue
        print("%-40s %s %-3s %-45s anunciadas=%-4d en_disco=%d" % (
            f["estudio"][:40], f["fecha"], f["modalidad"], f["descripcion"], f["imagenes"],
            f["en_disco"]))
    return 0


def _cmd_inventario(a):
    filas = inventario(a.raices)
    if a.json:
        print(json.dumps([{k: v for k, v in f.items() if not k.startswith("_")} for f in filas],
                         ensure_ascii=False, indent=1))
        return 0
    pacientes = sorted({f["paciente"] for f in filas})
    print("series: %d · huellas de paciente distintas: %d (%s)"
          % (len(filas), len(pacientes), ", ".join(pacientes)))
    for f in filas:
        if f["n"] < a.min_n:
            continue
        print("%s  %-3s %-4s n=%-4d grosor=%-5s %-4s %-8s contraste=%-12s %-40s  %s"
              % (f["fecha"], f["modalidad"], f["serie"], f["n"], f["grosor_mm"],
                 f.get("postura") or "-", f["paciente"],
                 f["contraste"] or "-", f["descripcion"], f["estudio"]))
    return 0


# ─── conversión DICOM → NIfTI ────────────────────────────────────────────────────────────

def _cache(serie):
    d = os.path.join(SALIDA_RAIZ, "cache", serie)
    exige_zona_clinica(d)
    os.makedirs(d, exist_ok=True)
    return d


def _localiza_serie(raices, serie):
    """(directorio, SeriesInstanceUID real) de la serie con esa huella."""
    for f in inventario(raices):
        if f["serie"] == serie:
            import SimpleITK as sitk
            for uid in sitk.ImageSeriesReader.GetGDCMSeriesIDs(f["_ruta"]) or []:
                if huella(uid) == serie:
                    return f["_ruta"], uid
    raise SystemExit("No encuentro la serie %s bajo esas raíces." % serie)


def _hms(t):
    """'HHMMSS.frac' → segundos."""
    t = str(t or "").strip()
    if len(t) < 6:
        return None
    try:
        return int(t[0:2]) * 3600 + int(t[2:4]) * 60 + float(t[4:])
    except ValueError:
        return None


def convierte(raices, serie):
    """Serie DICOM → <cache>/<serie>/imagen.nii.gz + meta.json (sin PII). Idempotente."""
    import SimpleITK as sitk
    import pydicom
    d = _cache(serie)
    salida = os.path.join(d, "imagen.nii.gz")
    meta_p = os.path.join(d, "meta.json")
    if os.path.exists(salida) and os.path.exists(meta_p):
        return salida, json.load(open(meta_p))
    carpeta, uid = _localiza_serie(raices, serie)
    ficheros = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(carpeta, uid)
    lector = sitk.ImageSeriesReader()
    lector.SetFileNames(ficheros)
    img = lector.Execute()
    sitk.WriteImage(img, salida, useCompression=True)
    ds = pydicom.dcmread(ficheros[0], stop_before_pixels=True)
    ini, adq = _hms(ds.get("ContrastBolusStartTime")), _hms(ds.get("AcquisitionTime"))
    meta = {
        "serie": serie,
        "modalidad": str(ds.get("Modality", "")),
        "fecha": str(ds.get("StudyDate", "")),
        "descripcion": str(ds.get("SeriesDescription", ""))[:60],
        "contraste": str(ds.get("ContrastBolusAgent", ""))[:30],
        "retraso_contraste_s": round(adq - ini, 1) if ini is not None and adq is not None else None,
        "tamano": list(img.GetSize()),
        "espaciado_mm": [round(x, 3) for x in img.GetSpacing()],
        "n_ficheros": len(ficheros),
        # Marcas de texto quemado: estas series nunca van a la web (arquitectura, 19-sep).
        "texto_quemado": str(ds.get("BurnedInAnnotation", "")).upper() == "YES"
                         or "SECONDARY" in "\\".join(ds.get("ImageType", [])).upper(),
    }
    json.dump(meta, open(meta_p, "w"), ensure_ascii=False, indent=1)
    return salida, meta


def _cmd_cobertura(a):
    """Hasta dónde llega cada serie, en milímetros del mundo (RAS).

    Sirve para saber si un estudio cubre lo que se quiere dibujar ANTES de montar nada: una
    reconstrucción del esqueleto no vale si al paciente le falta media columna en la imagen.
    No imprime nada identificativo: solo extensión y espaciado."""
    import nibabel as nib
    import numpy as np
    print("%-10s %-30s %-22s %-22s %s" % ("serie", "descripción", "x (mm)", "z craneo-caudal",
                                          "espaciado"))
    for serie in a.series:
        ruta, meta = convierte(a.raices, serie)
        img = nib.load(ruta)
        af, forma = img.affine, img.shape
        esq = np.array([[0, 0, 0], [forma[0] - 1, 0, 0], [0, forma[1] - 1, 0],
                        [0, 0, forma[2] - 1], [forma[0] - 1, forma[1] - 1, forma[2] - 1]])
        m = np.array([(af @ np.append(e, 1.0))[:3] for e in esq])
        lo, hi = m.min(0), m.max(0)
        print("%-10s %-30s %7.0f → %-12.0f %7.0f → %-12.0f %s" % (
            serie, meta["descripcion"][:30], lo[0], hi[0], lo[2], hi[2],
            "×".join("%.2f" % v for v in meta["espaciado_mm"])))
    return 0


def _cmd_convierte(a):
    for serie in a.series:
        salida, meta = convierte(a.raices, serie)
        print(json.dumps(meta, ensure_ascii=False))
    return 0


# ─── segmentación ────────────────────────────────────────────────────────────────────────

ROI_FASE = ["aorta", "portal_vein_and_splenic_vein", "liver", "spleen"]
# Estructuras vecinas del hígado con captación fisiológica de FDG (excreción, intestino):
# un «foco» ahí no es hepático.
VECINOS_EXCRECION = {"kidney_right", "kidney_left", "gallbladder", "duodenum", "colon",
                     "small_bowel", "stomach", "adrenal_gland_right", "urinary_bladder"}


def segmenta(raices, serie, preset="higado", tareas=None, device="mps"):
    """TotalSegmentator por tarea → <cache>/<serie>/seg/<tarea>.nii.gz (multilabel)."""
    exige_telemetria_apagada()
    from totalsegmentator.python_api import totalsegmentator
    imagen, meta = convierte(raices, serie)
    cfg = PRESETS[preset]
    tareas = tareas or cfg["tareas"]
    if meta["modalidad"] == "MR":
        tareas = [t + "_mr" if t in ("liver_segments", "liver_lesions") else t for t in tareas
                  if t not in ("liver_vessels",)]
        tareas = ["total_mr" if t == "total" else t for t in tareas]
    segdir = os.path.join(_cache(serie), "seg")
    os.makedirs(segdir, exist_ok=True)
    hechas = {}
    for t in tareas:
        out = os.path.join(segdir, t + ".nii.gz")
        if not os.path.exists(out):
            kw = {}
            if t in ("total", "total_mr"):
                kw["roi_subset"] = sorted(set(cfg["roi_total"] + ROI_FASE))
            tarea = "total" if t == "total_completo" else t     # 117 clases, sin recorte
            totalsegmentator(input=imagen, output=out, task=tarea, ml=True, device=device,
                             quiet=True, **kw)
        hechas[t] = out
    return hechas


def _cmd_segmenta(a):
    for serie in a.series:
        hechas = segmenta(a.raices, serie, tareas=a.tareas, device=a.device)
        print(serie, json.dumps({k: os.path.basename(v) for k, v in hechas.items()}))
    return 0


# ─── fase de contraste (sin xgboost: cabecera + HU de aorta/porta/hígado) ────────────────

def fase(raices, serie):
    import nibabel as nib
    import numpy as np
    from totalsegmentator.map_to_binary import class_map
    imagen, meta = convierte(raices, serie)
    seg = segmenta(raices, serie, tareas=["total"])["total"]
    ct = np.asarray(nib.load(imagen).dataobj)
    lab = np.asarray(nib.load(seg).dataobj)
    cm = {v: k for k, v in class_map["total"].items()}
    hu = {}
    for nombre in ROI_FASE:
        m = lab == cm[nombre]
        if m.sum() > 50:
            hu[nombre] = float(np.median(ct[m]))
    ao, pv, lv = hu.get("aorta"), hu.get("portal_vein_and_splenic_vein"), hu.get("liver")
    if ao is None or pv is None:
        veredicto = "indeterminada"
    elif ao < 100 and pv < 100:
        veredicto = "sin contraste"
    elif ao > pv + 60:
        veredicto = "arterial"
    elif pv >= 130:
        veredicto = "portal"
    else:
        veredicto = "tardía / equilibrio"
    return {"serie": serie, "fecha": meta["fecha"], "descripcion": meta["descripcion"],
            "retraso_contraste_s": meta["retraso_contraste_s"],
            "hu_mediana": {k: round(v) for k, v in hu.items()}, "fase": veredicto}


def _cmd_fase(a):
    for serie in a.series:
        print(json.dumps(fase(a.raices, serie), ensure_ascii=False))
    return 0


# ─── medidas por lesión y emparejado entre fechas ────────────────────────────────────────

UMBRAL_PEQUENA_MM = 10     # por debajo, sensibilidad publicada ~10 % (dossier 19-sep)
MIN_VOXELES = 5            # componentes más pequeños = ruido de segmentación


def _carga(ruta):
    import nibabel as nib
    import numpy as np
    img = nib.load(ruta)
    return np.asarray(img.dataobj), img.affine


def _diametro_axial_mayor(mask, esp):
    """Máximo diámetro en cualquier corte axial (eje 2 en RAS de nibabel), en mm.

    Entre CENTROS de vóxel y con el espaciado real de cada eje. El Feret de skimage mide entre
    esquinas de píxel y sobreestimaba ~1 píxel (21,5 mm en una esfera de 20: test_visor3d).
    """
    import numpy as np
    from scipy.spatial import ConvexHull, QhullError
    from scipy.spatial.distance import pdist
    mejor = 0.0
    for k in np.unique(np.nonzero(mask)[2]):
        pts = np.argwhere(mask[:, :, k]) * np.asarray(esp[:2], dtype=float)
        if len(pts) < 2:
            continue
        try:
            pts = pts[ConvexHull(pts).vertices]
        except QhullError:      # puntos colineales: pdist sobre todos
            pass
        mejor = max(mejor, float(pdist(pts).max()))
    return mejor


def lesiones(raices, serie):
    """Lista de lesiones de una serie, desde liver_lesions, recortadas al hígado."""
    import numpy as np
    from scipy import ndimage
    from totalsegmentator.map_to_binary import class_map
    seg = segmenta(raices, serie)
    total, aff = _carga(seg["total"])
    cm = {v: k for k, v in class_map["total"].items()}
    higado = total == cm["liver"]
    les, _ = _carga(seg.get("liver_lesions") or seg.get("liver_lesions_mr"))
    segm = _carga(seg.get("liver_segments") or seg.get("liver_segments_mr"))[0]
    tumor_v = seg.get("liver_vessels")
    tumor_v = (_carga(tumor_v)[0] == 2) if tumor_v else None
    esp = np.abs(np.diag(aff)[:3])
    vox_ml = float(np.prod(esp)) / 1000.0
    dentro = ndimage.binary_dilation(higado, iterations=3)
    comp, n = ndimage.label((les > 0) & dentro)
    out = []
    for i in range(1, n + 1):
        m = comp == i
        nv = int(m.sum())
        if nv < MIN_VOXELES:
            continue
        idx = np.argwhere(m).mean(axis=0)
        centro = (aff @ np.append(idx, 1.0))[:3]
        seg_vals = segm[m]
        seg_vals = seg_vals[seg_vals > 0]
        segmento = int(np.bincount(seg_vals).argmax()) if seg_vals.size else None
        d = _diametro_axial_mayor(m, esp)
        out.append({
            "_comp": i,
            "diametro_mm": round(d, 1),
            "volumen_ml": round(nv * vox_ml, 2),
            "centro_mm": [round(float(c), 1) for c in centro],
            "segmento": segmento,
            "pequena": d < UMBRAL_PEQUENA_MM,
            # Acuerdo con el 2º modelo (liver_vessels/tumor): fracción de la lesión que él
            # también marca. Dos modelos que coinciden no validan nada, pero uno que discrepa
            # es una bandera útil para el radiólogo.
            "acuerdo_2o_modelo": (round(float(tumor_v[m].mean()), 2)
                                  if tumor_v is not None else None),
        })
    out.sort(key=lambda L: -L["volumen_ml"])
    ids = np.zeros(comp.shape, np.uint8)
    for k, L in enumerate(out, 1):
        L["id"] = k
        ids[comp == L.pop("_comp")] = k
    # Mapa de ids en el espacio del TC diagnóstico: lo necesita el PET (se lleva entero con el
    # registro deformable, no solo el centroide).
    import nibabel as nib
    nib.save(nifti_limpio(ids, aff, np.uint8), os.path.join(_cache(serie), "lesiones_id.nii.gz"))
    return {
        "serie": serie,
        "volumen_higado_ml": round(float(higado.sum()) * vox_ml, 1),
        "centro_higado_mm": [round(float(c), 1) for c in
                             (aff @ np.append(np.argwhere(higado).mean(axis=0), 1.0))[:3]],
        "volumen_tumoral_ml": round(sum(L["volumen_ml"] for L in out), 2),
        "lesiones": out,
    }


def empareja(a, b, tolerancia_mm=15.0, tolerancia_mismo_segmento_mm=25.0):
    """Empareja lesiones de a (antes) y b (después): asignación óptima (húngaro) sobre la
    distancia entre centroides tras alinear el centro del hígado, con el segmento de Couinaud
    como desempate. AYUDA, no verdad: el emparejado bueno sale del informe radiológico
    (arquitectura, 19-sep); aquí solo se propone, con la distancia a la vista.

    Con el mismo segmento se admite hasta 25 mm: el hígado se deforma entre estudios y con 15 mm
    fijos la diana s.IVb (17,4 mm) quedaba sin pareja en el caso real.
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    desplaz = np.array(b["centro_higado_mm"]) - np.array(a["centro_higado_mm"])
    A, B = a["lesiones"], b["lesiones"]
    GRANDE = 1e6
    coste = np.full((len(A), len(B)), GRANDE)
    for i, La in enumerate(A):
        pa = np.array(La["centro_mm"]) + desplaz
        for j, Lb in enumerate(B):
            dd = float(np.linalg.norm(np.array(Lb["centro_mm"]) - pa))
            mismo = La.get("segmento") is not None and La.get("segmento") == Lb.get("segmento")
            lim = max(tolerancia_mismo_segmento_mm if mismo else tolerancia_mm,
                      0.75 * max(La["diametro_mm"], Lb["diametro_mm"]))
            if dd <= lim:
                coste[i, j] = dd + (0.0 if mismo else 10.0)
    pares, usados_a, usados_b = [], set(), set()
    if len(A) and len(B):
        filas, cols = linear_sum_assignment(coste)
        for i, j in zip(filas, cols):
            if coste[i, j] >= GRANDE:
                continue
            La, Lb = A[i], B[j]
            usados_a.add(La["id"])
            usados_b.add(Lb["id"])
            dist = float(np.linalg.norm(np.array(Lb["centro_mm"]) - np.array(La["centro_mm"])
                                        - desplaz))
            pares.append({"antes": La["id"], "despues": Lb["id"], "distancia_mm": round(dist, 1),
                          "delta_diametro_pct": round((Lb["diametro_mm"] - La["diametro_mm"])
                                                      / max(La["diametro_mm"], 0.1) * 100, 1),
                          "delta_volumen_pct": round((Lb["volumen_ml"] - La["volumen_ml"])
                                                     / La["volumen_ml"] * 100, 1)})
    pares += [{"antes": L["id"], "despues": None} for L in A if L["id"] not in usados_a]
    pares += [{"antes": None, "despues": L["id"]} for L in B if L["id"] not in usados_b]
    pares.sort(key=lambda p: (p["antes"] is None, p["antes"] or 0, p["despues"] or 0))
    return pares


def compara(raices, serie_a, serie_b):
    a, b = lesiones(raices, serie_a), lesiones(raices, serie_b)
    res = {"antes": a, "despues": b, "pares": empareja(a, b)}
    d = _dir("comparacion")
    exige_zona_clinica(d)
    os.makedirs(d, exist_ok=True)
    ruta = os.path.join(d, "%s_%s.json" % (serie_a, serie_b))
    json.dump(res, open(ruta, "w"), ensure_ascii=False, indent=1)
    return res, ruta


def _cmd_compara(a):
    res, ruta = compara(a.raices, a.series[0], a.series[1])
    for lado in ("antes", "despues"):
        r = res[lado]
        print("%s %s: hígado %.0f ml · lesiones %d · volumen tumoral %.1f ml"
              % (lado, r["serie"], r["volumen_higado_ml"], len(r["lesiones"]),
                 r["volumen_tumoral_ml"]))
        for L in r["lesiones"]:
            print("   L%-2d  %5.1f mm  %7.2f ml  seg %-4s %s acuerdo2=%s"
                  % (L["id"], L["diametro_mm"], L["volumen_ml"], L["segmento"],
                     "<10mm" if L["pequena"] else "     ", L["acuerdo_2o_modelo"]))
    print("pares:")
    for p in res["pares"]:
        print("  ", json.dumps(p))
    print("→", os.path.basename(ruta))
    return 0


# ─── assets para el visor (web y local) ──────────────────────────────────────────────────

MARGEN_MASCARA_MM = 15     # fuera de hígado dilatado 15 mm, todo a aire (arquitectura, 19-sep)
AIRE_HU = -1024
VENTANA_ANCHO = 180        # HU; nivel = mediana del parénquima de cada estudio
ETIQUETAS = {"higado": 1, "vasos": 2, "porta": 3, "vci": 4}   # lesiones: 10 + id


def nifti_limpio(datos, afin, dtype):
    """NIfTI sin nada que no sea imagen: descrip/aux_file/intent vacíos y sin extensiones."""
    import nibabel as nib
    import numpy as np
    img = nib.Nifti1Image(np.asarray(datos).astype(dtype), afin)
    h = img.header
    h["descrip"] = b""
    h["aux_file"] = b""
    h["intent_name"] = b""
    h["db_name"] = b""
    img.header.extensions.clear()
    img.set_qform(afin, code=1)
    img.set_sform(afin, code=1)
    return img


def ply_binario(ruta, verts, caras):
    """PLY binario little-endian: solo x,y,z y caras (lo que NiiVue lee; sin comentarios)."""
    import numpy as np
    v = np.asarray(verts, dtype="<f4")
    f = np.asarray(caras, dtype="<i4")
    cab = ("ply\nformat binary_little_endian 1.0\nelement vertex %d\n"
           "property float x\nproperty float y\nproperty float z\n"
           "element face %d\nproperty list uchar int vertex_indices\nend_header\n"
           % (len(v), len(f))).encode("ascii")
    caras_b = np.empty(len(f), dtype=[("n", "u1"), ("i", "<i4", (3,))])
    caras_b["n"] = 3
    caras_b["i"] = f
    with open(ruta, "wb") as o:
        o.write(cab)
        o.write(v.tobytes())
        o.write(caras_b.tobytes())


def _caja_de(mask, margen):
    """Caja mínima que contiene la máscara, ensanchada `margen` vóxeles y recortada al array.

    Devuelve (origen, caja): `origen` es el vértice de la caja en índices del array original
    (para devolver a su sitio los vértices de una malla calculada sobre el recorte) y `caja`
    es la tupla de slices. Proyecta con `any` eje a eje, así que no materializa nada del
    tamaño del volumen (un `argwhere` sobre un hígado serían decenas de MB)."""
    import numpy as np
    origen, caja = [], []
    for eje in range(mask.ndim):
        idx = np.nonzero(mask.any(axis=tuple(e for e in range(mask.ndim) if e != eje)))[0]
        lo = max(int(idx[0]) - margen, 0)
        caja.append(slice(lo, min(int(idx[-1]) + 1 + margen, mask.shape[eje])))
        origen.append(lo)
    return np.asarray(origen, dtype=float), tuple(caja)


def _malla(mask, afin, suavizado=1.0, sobremuestreo=1, min_componente=0, taubin=12):
    """Máscara → malla (vértices en mm del mundo, caras).

    sobremuestreo: interpola el campo suavizado ×N antes de marching cubes (lesiones pequeñas
    redondas en vez de facetadas). min_componente: quita trozos sueltos de menos de N vóxeles
    (el árbol vascular automático deja fragmentos flotando que ensucian el render)."""
    import numpy as np
    from scipy import ndimage
    from skimage.measure import marching_cubes
    if mask.sum() < 4:
        return None
    # Recorte a la caja de la máscara. Un tumor de 2 cm ocupa una millonésima de un TC de
    # tórax-abdomen-pelvis, pero aquí se suavizaba y se sobremuestreaba el volumen ENTERO:
    # el 20-sep-26 eso pidió 16 GB en una máquina de 16 y dejó el Mac mini paginando a
    # muerte. Con margen >= 4·sigma el gaussiano ya vale ~0 en el borde, así que la malla
    # sale idéntica a la del volumen completo (lo fija tests/test_visor3d_malla_recorte.py).
    origen, caja = _caja_de(mask, margen=int(np.ceil(4 * max(np.atleast_1d(suavizado)))) + 3)
    mask = mask[caja]
    if min_componente:
        comp, n = ndimage.label(mask)
        if n:
            tam = ndimage.sum(mask, comp, index=np.arange(1, n + 1))
            mask = np.isin(comp, 1 + np.nonzero(tam >= min_componente)[0])
            if not mask.any():
                return None
    campo = ndimage.gaussian_filter(np.pad(mask, 2).astype(np.float32), suavizado)
    if campo.max() <= 0.5:      # estructura tan fina que el suavizado la borra: sin malla
        return None
    forma = campo.shape
    if sobremuestreo > 1:
        campo = ndimage.zoom(campo, sobremuestreo, order=3)
    v, f, _, _ = marching_cubes(campo, 0.5)
    if sobremuestreo > 1:
        # `zoom` estira el eje entero sobre el eje entero, así que la escala real es
        # (N-1)/(N'-1) y DEPENDE del tamaño del array: dividir por `sobremuestreo` a secas
        # hacía que la malla dependiera de cuánto volumen vacío sobrara alrededor, y con el
        # recorte de arriba eso sí se nota. Con la escala exacta el recorte es invariante
        # (frente al volumen completo cambia 5·10⁻³ vóxel, cinco micras a 1 mm de vóxel).
        v = v * np.array([(m - 1) / (z - 1) for m, z in zip(forma, campo.shape)])
    v = _taubin(v - 2, f, iteraciones=taubin)
    v = v + origen              # deshacer el recorte: Taubin conmuta con la traslación
    v = (afin @ np.c_[v, np.ones(len(v))].T).T[:, :3]
    return v, f


def _icosfera(n=3):
    """Esfera unidad (icosaedro subdividido n veces): vértices, caras."""
    import numpy as np
    t = (1 + 5 ** 0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
         (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
         (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
         (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    v = [np.array(x, float) / np.linalg.norm(x) for x in v]
    for _ in range(n):
        cache, nf = {}, []

        def medio(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = v[a] + v[b]
                v.append(m / np.linalg.norm(m))
                cache[k] = len(v) - 1
            return cache[k]
        for a, b, c in f:
            ab, bc, ca = medio(a, b), medio(b, c), medio(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return np.array(v), np.array(f, dtype=np.int32)


def _elipsoide(mask, afin, esp, diametro_mm=None):
    """Elipsoide ajustado a los vóxeles (media + covarianza en mm): misma posición, orientación y
    tamaño. Para lesiones de < 8 mm, donde marching cubes dibuja el vóxel (cubos, cruces) y la
    forma fina no es información real. Para un elipsoide sólido, var = semieje² / 5; se suma la
    del vóxel (esp²/12) que la discretización quita."""
    import numpy as np
    idx = np.argwhere(mask).astype(float)
    if len(idx) < 1:
        return None
    mm = (afin @ np.c_[idx, np.ones(len(idx))].T).T[:, :3]
    c = mm.mean(axis=0)
    cov = np.cov(mm.T) if len(mm) > 1 else np.zeros((3, 3))
    cov = cov + np.diag(np.asarray(esp, float) ** 2 / 12.0)
    val, vec = np.linalg.eigh(cov)
    semi = np.sqrt(5.0 * np.clip(val, 1e-6, None))
    u, f = _icosfera(3)
    v = c + (u * semi) @ vec.T
    if diametro_mm:
        # Calibrado a la medida: el ajuste por covarianza sobrestima en objetos de pocos vóxeles
        # (2,2 mm salía en 3,7). Se escala para que su diámetro axial mayor sea el medido, que
        # es la cifra que enseña la leyenda.
        from scipy.spatial.distance import pdist
        actual = float(pdist(v[:, :2]).max())
        if actual > 0:
            v = c + (v - c) * (diametro_mm / actual)
    return v, f


def _taubin(v, f, iteraciones=12, lam=0.5, mu=-0.53):
    """Suavizado de Taubin: quita el escalonado del vóxel sin encoger la malla (el laplaciano
    solo la encoge). Vecindad por aristas de las caras."""
    import numpy as np
    from scipy.sparse import coo_matrix
    n = len(v)
    a = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    a = np.concatenate([a, a[:, ::-1]])
    m = coo_matrix((np.ones(len(a)), (a[:, 0], a[:, 1])), shape=(n, n)).tocsr()
    m.data[:] = 1.0
    grado = np.asarray(m.sum(axis=1)).ravel()
    grado[grado == 0] = 1
    v = v.astype(np.float64)
    for _ in range(iteraciones):
        for k in (lam, mu):
            v = v + k * (m @ v / grado[:, None] - v)
    return v


def assets(raices, serie, destino, voxel_mm=2.0):
    """Volúmenes enmascarados + etiquetas + mallas + lesiones.json de UNA serie."""
    import nibabel as nib
    import numpy as np
    from nibabel.processing import resample_from_to, resample_to_output
    from scipy import ndimage
    from totalsegmentator.map_to_binary import class_map
    exige_zona_clinica(destino)
    os.makedirs(destino, exist_ok=True)
    imagen, meta = convierte(raices, serie)
    if meta.get("texto_quemado"):
        raise SystemExit("ABORTA: la serie %s lleva texto quemado; no genera assets." % serie)
    seg = segmenta(raices, serie)
    info = lesiones(raices, serie)
    ct = resample_to_output(nib.load(imagen), voxel_sizes=voxel_mm, order=1)
    afin = ct.affine

    def a_ct(ruta):
        return np.asarray(resample_from_to(nib.load(ruta), ct, order=0).dataobj).astype(np.int16)

    cm = {v: k for k, v in class_map["total"].items()}
    total = a_ct(seg["total"])
    higado = total == cm["liver"]
    lab = np.zeros(higado.shape, np.uint8)
    lab[higado] = ETIQUETAS["higado"]
    if "liver_vessels" in seg:
        lab[(a_ct(seg["liver_vessels"]) == 1) & higado] = ETIQUETAS["vasos"]
    lab[total == cm["portal_vein_and_splenic_vein"]] = ETIQUETAS["porta"]
    lab[total == cm["inferior_vena_cava"]] = ETIQUETAS["vci"]
    # Lesiones con el MISMO id que lesiones(): se re-etiqueta por centroide más cercano.
    les = a_ct(seg.get("liver_lesions") or seg.get("liver_lesions_mr")) > 0
    comp, n = ndimage.label(les & ndimage.binary_dilation(higado, iterations=2))
    inv = np.linalg.inv(afin)
    for i in range(1, n + 1):
        m = comp == i
        c = (afin @ np.append(np.argwhere(m).mean(axis=0), 1.0))[:3]
        cand = [(np.linalg.norm(np.array(L["centro_mm"]) - c), L["id"]) for L in info["lesiones"]]
        if cand and min(cand)[0] < 10:
            lab[m] = 10 + min(cand)[1]
    # Máscara de publicación: hígado dilatado MARGEN mm; fuera, aire.
    it = max(1, int(round(MARGEN_MASCARA_MM / voxel_mm)))
    mascara = ndimage.binary_dilation(higado, iterations=it)
    datos = np.asarray(ct.dataobj)
    # Ventana hepática centrada en el parénquima de ESTE estudio (el realce portal cambia entre
    # fechas: 131 vs 149 HU en el caso real, y una ventana fija saturaba el 8-sep).
    sano = higado & (lab == ETIQUETAS["higado"])
    nivel = int(round(float(np.median(datos[sano])))) if sano.any() else 60
    ventana = [nivel - VENTANA_ANCHO // 2, nivel + VENTANA_ANCHO // 2]
    datos = np.where(mascara, datos, AIRE_HU)
    lab[~mascara] = 0
    # Recorte a la caja de la máscara (+2 vóxeles)
    idx = np.argwhere(mascara)
    lo, hi = np.maximum(idx.min(0) - 2, 0), np.minimum(idx.max(0) + 3, mascara.shape)
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    afin_c = afin.copy()
    afin_c[:3, 3] = (afin @ np.append(lo, 1.0))[:3]
    nib.save(nifti_limpio(datos[sl], afin_c, np.int16), os.path.join(destino, "ct.nii.gz"))
    nib.save(nifti_limpio(lab[sl], afin_c, np.uint8), os.path.join(destino, "etiquetas.nii.gz"))
    if "liver_segments" in seg or "liver_segments_mr" in seg:
        sg = a_ct(seg.get("liver_segments") or seg.get("liver_segments_mr")).astype(np.uint8)
        sg[~higado] = 0
        nib.save(nifti_limpio(sg[sl], afin_c, np.uint8), os.path.join(destino, "segmentos.nii.gz"))
    mallas = {}
    vesicula = total == cm["gallbladder"]
    vs = voxel_mm / 2.0          # el suavizado se da en vóxeles: a 1 mm, el doble de vóxeles
    for nombre, mask, suav in (("higado", higado, 1.2 / vs),
                               ("porta", lab == ETIQUETAS["porta"], 0.8 / vs),
                               ("vci", (total == cm["inferior_vena_cava"]) & mascara, 0.8 / vs),
                               ("vesicula", vesicula & mascara, 1.0 / vs),
                               ("vasos", lab == ETIQUETAS["vasos"], 0.6 / vs)):
        if nombre == "higado":
            # Los cortes del TC (2,5 mm) dejaban escalones horizontales en la superficie:
            # más suavizado en el eje cráneo-caudal (eje 2 en RAS) y más pasadas de Taubin.
            r = _malla(mask[sl], afin_c, (suav, suav, suav * 1.6), taubin=30)
        else:
            r = _malla(mask[sl], afin_c, suav,
                       min_componente=int(300 / voxel_mm ** 3) if nombre == "vasos" else 0)
        if r:
            ply_binario(os.path.join(destino, nombre + ".ply"), *r)
            mallas[nombre] = nombre + ".ply"
    for L in info["lesiones"]:
        # Suavizado proporcional al tamaño: con sigma fijo las lesiones de 4-6 mm salían a la
        # mitad (proporcion, 19-sep). sigma = diámetro/10, entre 0,3 y 1,2 mm.
        sigma_mm = min(1.2, max(0.3, L["diametro_mm"] / 10.0))
        if L["diametro_mm"] < 8:
            r = _elipsoide(lab[sl] == 10 + L["id"], afin_c, [voxel_mm] * 3,
                           diametro_mm=L["diametro_mm"])
        else:
            r = _malla(lab[sl] == 10 + L["id"], afin_c, sigma_mm / voxel_mm,
                       sobremuestreo=2 if voxel_mm <= 1 else 1)
        if r:
            ply_binario(os.path.join(destino, "lesion%02d.ply" % L["id"]), *r)
            L["malla"] = "lesion%02d.ply" % L["id"]
    fecha = meta["fecha"]
    salida = {"fecha": "%s-%s-%s" % (fecha[:4], fecha[4:6], fecha[6:]), "modalidad": meta["modalidad"],
              "voxel_mm": voxel_mm, "ventana_hu": ventana, "etiquetas": ETIQUETAS,
              "volumen_higado_ml": info["volumen_higado_ml"],
              "volumen_tumoral_ml": info["volumen_tumoral_ml"],
              "lesiones": info["lesiones"], "mallas": mallas}
    json.dump(salida, open(os.path.join(destino, "estudio.json"), "w"), ensure_ascii=False,
              indent=1)
    return salida


def _cmd_assets(a):
    base = _dir("assets")
    for serie in a.series:
        _, meta = convierte(a.raices, serie)
        # 2 mm para la web; 1 mm (sufijo _hd) para el render de vídeo
        destino = os.path.join(base, meta["fecha"] + ("" if a.voxel >= 2 else "_hd"))
        r = assets(a.raices, serie, destino, voxel_mm=a.voxel)
        print("%s: %d lesiones, %d mallas → %s" % (r["fecha"], len(r["lesiones"]),
                                                  len(r["mallas"]), os.path.basename(destino)))
    return 0


# ─── mama: sin modelo de lesión, el tumor sale de restar las fases del dinámico ──────────
#
# Por qué no se parece al hígado: TotalSegmentator no tiene lesión de mama en RM (su tarea
# `breasts` es de TC y segmenta la mama entera, no el tumor). Así que aquí:
#   · el TUMOR sale de la resta post−pre del VIBRANT dinámico, con la semilla puesta donde dice
#     el informe y crecimiento de región por percentil del realce;
#   · la MAMA sale del Dixon (series WATER y FAT de la misma adquisición): el tejido
#     fibroglandular, que es lo que el radiólogo llama la mama y lo que la literatura cuantifica
#     como densidad mamaria. No es la piel.
# La PIEL, la areola y el pezón se erosionan ANTES de mallar: quedan fuera del DATO, no ocultos
# por opacidad (condición del comité de diseño, 20-sep; lo comprueba tests/test_visor3d.py).
# Y como la semilla la pone un humano leyendo un informe, esto NO es determinista como el
# hígado: cada malla sale con su `receta.json` y sin receta completa no se genera nada.

FF_FIBROGLANDULAR = 0.45   # por debajo de esta fracción de grasa, el vóxel es tejido glandular
PIEL_MM = 4.0              # cuánto se erosiona el cuerpo antes de mallar
RECETA_OBLIGATORIA = ("_fuente", "semilla_mm", "umbral_frac", "radio_mm", "contexto_mm")


def _sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()[:16]


def estudio_de(raices, serie):
    """Huella del StudyInstanceUID de una serie."""
    import SimpleITK as sitk
    import pydicom
    carpeta, uid = _localiza_serie(raices, serie)
    fich = sitk.ImageSeriesReader.GetGDCMSeriesFileNames(carpeta, uid)[0]
    ds = pydicom.dcmread(fich, stop_before_pixels=True)
    return huella(ds.get("StudyInstanceUID"))


def _dice(a, b):
    import numpy as np
    s = int(a.sum()) + int(b.sum())
    return round(float(2.0 * np.logical_and(a, b).sum() / s), 4) if s else 0.0


def cuerpo(vol):
    """Máscara del cuerpo en una RM: Otsu sobre la magnitud, agujeros rellenos, trozo mayor."""
    import numpy as np
    from scipy import ndimage
    from skimage.filters import threshold_otsu
    v = np.asarray(vol, dtype=np.float32)
    u = threshold_otsu(v[v > 0]) if (v > 0).any() else 0.0
    m = v > u
    m = ndimage.binary_closing(m, iterations=2)
    m = ndimage.binary_fill_holes(m)
    comp, n = ndimage.label(m)
    if n > 1:
        tam = ndimage.sum(m, comp, index=np.arange(1, n + 1))
        m = comp == (1 + int(np.argmax(tam)))
    return m


def _bola_estructura(esp, mm):
    """Elemento estructurante esférico de `mm` de radio, con el espaciado real de cada eje."""
    import numpy as np
    r = np.maximum(1, np.round(mm / np.asarray(esp, dtype=float)).astype(int))
    ejes = np.indices(tuple(2 * r + 1)).astype(float)
    for k in range(3):
        ejes[k] = (ejes[k] - r[k]) * esp[k]
    return np.linalg.norm(ejes, axis=0) <= mm


def sin_piel(mascara_cuerpo, esp, mm=PIEL_MM):
    """El cuerpo erosionado `mm`: piel, areola y pezón dejan de estar en el dato.

    Se erosiona en MILÍMETROS, no en vóxeles: el mismo número de iteraciones sobre otro
    espaciado dejaría la piel dentro sin que nadie se enterara."""
    import numpy as np
    from scipy import ndimage
    return ndimage.binary_erosion(mascara_cuerpo, structure=_bola_estructura(esp, mm),
                                  border_value=0)


def fibroglandular(agua, grasa, dentro, umbral=FF_FIBROGLANDULAR):
    """Tejido fibroglandular: vóxeles dominados por agua dentro del cuerpo ya sin piel."""
    import numpy as np
    a = np.asarray(agua, dtype=np.float32)
    g = np.asarray(grasa, dtype=np.float32)
    ff = np.divide(g, a + g, out=np.full(a.shape, 0.5, np.float32), where=(a + g) > 1e-6)
    return dentro & (ff < umbral)


def _bola_mm(forma, centro_ijk, radio_mm, esp):
    import numpy as np
    ejes = [np.abs(np.arange(forma[k]) - centro_ijk[k]) * esp[k] for k in range(3)]
    d2 = (ejes[0][:, None, None] ** 2 + ejes[1][None, :, None] ** 2
          + ejes[2][None, None, :] ** 2)
    return d2 <= radio_mm ** 2


def crece_tumor(realce, afin, semilla_mm, fraccion, radio_mm):
    """Crecimiento de región semillado, acotado a una bola alrededor de lo que dice el informe.

    El umbral NO es un valor absoluto (la RM no tiene unidades: no sobrevive a otro estudio ni a
    otra máquina) ni un percentil de la bola (depende de lo grande que se haga la bola, que es
    arbitrario). Es un PORCENTAJE DEL REALCE de la propia lesión: fondo + f·(pico − fondo), con
    f entre 0,30 y 0,50. Con 0,30 la medida iguala a la manual en la serie publicada; por encima
    de 0,50 infraestima (Cancer Imaging 2020, 10.1186/s40644-020-00307-0)."""
    import numpy as np
    from scipy import ndimage
    if not 0.05 <= fraccion <= 0.95:
        raise SystemExit("ABORTA: la fracción de realce (%s) está fuera de 0,05–0,95; se espera "
                         "algo entre 0,30 y 0,50." % fraccion)
    esp = np.sqrt((np.asarray(afin)[:3, :3] ** 2).sum(axis=0))
    ijk = np.round((np.linalg.inv(afin) @ np.append(semilla_mm, 1.0))[:3]).astype(int)
    if (ijk < 0).any() or (ijk >= np.array(realce.shape)).any():
        raise SystemExit("ABORTA: la semilla %s cae fuera del volumen." % (list(semilla_mm),))
    bola = _bola_mm(realce.shape, ijk, radio_mm, esp)
    nucleo = _bola_mm(realce.shape, ijk, max(3.0, min(5.0, radio_mm / 3.0)), esp)
    pico = float(np.percentile(realce[nucleo], 95))
    fondo = float(np.median(realce[bola]))
    if pico <= fondo:
        raise SystemExit("ABORTA: la semilla no realza por encima de su entorno (pico %.1f, "
                         "fondo %.1f). Revisa la semilla, no el umbral." % (pico, fondo))
    umbral = fondo + fraccion * (pico - fondo)
    comp, n = ndimage.label(bola & (realce >= umbral))
    et = int(comp[tuple(ijk)])
    if not et:
        raise SystemExit("ABORTA: la semilla queda por debajo de su propio umbral (%.1f)."
                         % umbral)
    return comp == et, umbral


def realce(raices, pre, post, registro="rigido"):
    """post − pre del MISMO estudio dinámico. Devuelve (ruta del NIfTI, info).

    El registro rígido corrige el movimiento entre fases, que es el artefacto que más rompe la
    resta; `info` lleva el Dice del cuerpo antes y después para saber si ayudó o estropeó."""
    import nibabel as nib
    import numpy as np
    import SimpleITK as sitk
    e_pre, e_post = estudio_de(raices, pre), estudio_de(raices, post)
    if e_pre != e_post:
        raise SystemExit("ABORTA: %s y %s no son del mismo estudio (%s vs %s). Restar fases de "
                         "estudios distintos produce una lesión que no existe." % (pre, post,
                                                                                   e_pre, e_post))
    r_pre, m_pre = convierte(raices, pre)
    r_post, m_post = convierte(raices, post)
    for s, m in ((pre, m_pre), (post, m_post)):
        if m.get("texto_quemado"):
            raise SystemExit("ABORTA: la serie %s lleva texto quemado." % s)
    destino = os.path.join(_cache(post), "realce_%s_%s.nii.gz" % (pre, registro))
    info_p = destino.replace(".nii.gz", ".json")
    if os.path.exists(destino) and os.path.exists(info_p):
        return destino, json.load(open(info_p))
    i_pre, i_post = sitk.ReadImage(r_pre, sitk.sitkFloat32), sitk.ReadImage(r_post,
                                                                           sitk.sitkFloat32)
    a_pre = sitk.GetArrayFromImage(i_pre)
    a_post = sitk.GetArrayFromImage(i_post)
    antes = _dice(cuerpo(a_pre), cuerpo(a_post))
    if registro == "rigido":
        tx = sitk.CenteredTransformInitializer(i_post, i_pre, sitk.Euler3DTransform(),
                                               sitk.CenteredTransformInitializerFilter.GEOMETRY)
        reg = sitk.ImageRegistrationMethod()
        reg.SetMetricAsMattesMutualInformation(numberOfHistogramBins=32)
        reg.SetMetricSamplingStrategy(reg.RANDOM)
        reg.SetMetricSamplingPercentage(0.05, seed=20260920)   # semilla fija: reproducible
        reg.SetInterpolator(sitk.sitkLinear)
        reg.SetOptimizerAsRegularStepGradientDescent(2.0, 1e-4, 200)
        reg.SetOptimizerScalesFromPhysicalShift()
        reg.SetShrinkFactorsPerLevel([4, 2, 1])
        reg.SetSmoothingSigmasPerLevel([2, 1, 0])
        reg.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
        reg.SetInitialTransform(tx, inPlace=False)
        tx = reg.Execute(i_post, i_pre)
        i_pre = sitk.Resample(i_pre, i_post, tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
        a_pre = sitk.GetArrayFromImage(i_pre)
    elif i_pre.GetSize() != i_post.GetSize():
        raise SystemExit("ABORTA: pre y post no comparten rejilla y pediste --registro ninguno.")
    despues = _dice(cuerpo(a_pre), cuerpo(a_post))
    # Solo el realce POSITIVO: un post menor que el pre es ruido o movimiento, no captación.
    sitk.WriteImage(_copia_geom(i_post, np.maximum(a_post - a_pre, 0.0)), destino, True)
    info = {"pre": pre, "post": post, "estudio": e_post, "registro": registro,
            "dice_cuerpo_antes": antes, "dice_cuerpo_despues": despues,
            "retraso_contraste_s": m_post.get("retraso_contraste_s"),
            "espaciado_mm": m_post["espaciado_mm"]}
    json.dump(info, open(info_p, "w"), ensure_ascii=False, indent=1)
    return destino, info


def _copia_geom(referencia, datos):
    """Array → imagen SimpleITK con la geometría de `referencia`."""
    import SimpleITK as sitk
    img = sitk.GetImageFromArray(datos)
    img.CopyInformation(referencia)
    return img


def assets_mama(raices, pre, post, grasa, receta, destino):
    """Mallas de la mama: tejido fibroglandular + tumor, y la receta que las reproduce.

    Se escribe APARTE de assets(): aquel recorta por HU y por clases de TotalSegmentator, y
    en RM no hay ni HU ni clases de mama. Lo genérico (mallado, PLY, NIfTI limpio) se reutiliza."""
    import nibabel as nib
    import numpy as np
    exige_zona_clinica(destino)
    obligatorias = ("_fuente", "contexto_mm") if receta.get("serie_modelo") \
        else RECETA_OBLIGATORIA
    faltan = [k for k in obligatorias if receta.get(k) in (None, "")]
    if faltan:
        raise SystemExit("ABORTA: la receta no trae %s. Sin receta completa no hay malla: esto "
                         "tiene que quedar reproducible." % ", ".join(faltan))
    os.makedirs(destino, exist_ok=True)
    ruta_realce, info = realce(raices, pre, post, receta.get("registro", "rigido"))
    r_agua, m_agua = convierte(raices, pre)
    r_grasa, m_grasa = convierte(raices, grasa)
    if estudio_de(raices, grasa) != info["estudio"]:
        raise SystemExit("ABORTA: la serie de grasa no es del mismo estudio que el dinámico.")
    im = nib.load(ruta_realce)
    afin = im.affine
    real = np.asarray(im.dataobj, dtype=np.float32)
    agua = np.asarray(nib.load(r_agua).dataobj, dtype=np.float32)
    gra = np.asarray(nib.load(r_grasa).dataobj, dtype=np.float32)
    if gra.shape != agua.shape:
        raise SystemExit("ABORTA: WATER y FAT no comparten rejilla; no son el mismo Dixon.")
    esp = np.sqrt((afin[:3, :3] ** 2).sum(axis=0))
    piel_mm = float(receta.get("piel_mm", PIEL_MM))
    cuerpo_m = cuerpo(agua + gra)
    dentro = sin_piel(cuerpo_m, esp, piel_mm)
    if receta.get("serie_modelo"):
        # El modelo de MAMA-MIA encuentra la lesión sin semilla. Se queda solo con el
        # componente que el informe respalda (el que se cotejó y quedó escrito en la receta):
        # publicar «todo lo que el modelo marcó» sería publicar sus falsos positivos.
        from scipy import ndimage as _nd
        ruta_m = tumor_modelo(raices, receta["serie_modelo"],
                              folds=tuple(receta.get("folds", [0])),
                              device=receta.get("device", "auto"))
        bruta = np.asarray(nib.load(ruta_m).dataobj) > 0
        if bruta.shape != real.shape:
            raise SystemExit("ABORTA: la máscara del modelo no casa con la rejilla del realce.")
        comp, n = _nd.label(bruta)
        if not n:
            raise SystemExit("ABORTA: el modelo no marcó nada en %s" % receta["serie_modelo"])
        if receta.get("centro_mm"):
            ijk = np.round((np.linalg.inv(afin)
                            @ np.append(receta["centro_mm"], 1.0))[:3]).astype(int)
            et = int(comp[tuple(np.clip(ijk, 0, np.array(comp.shape) - 1))])
            if not et:      # el centro cotejado cae fuera: coge el componente más cercano a él
                cents = _nd.center_of_mass(bruta, comp, np.arange(1, n + 1))
                et = 1 + int(np.argmin([np.linalg.norm(np.array(c) - ijk) for c in cents]))
        else:
            tam = _nd.sum(bruta, comp, index=np.arange(1, n + 1))
            et = 1 + int(np.argmax(tam))
        tumor, umbral = comp == et, None
    else:
        tumor, umbral = crece_tumor(real, afin, receta["semilla_mm"], receta["umbral_frac"],
                                    receta["radio_mm"])
    if not (tumor & dentro).any():
        raise SystemExit("ABORTA: el tumor cae entero en los %g mm de piel que se erosionan; "
                         "revisa la semilla." % piel_mm)
    # La ENVOLTURA de la mama: la superficie del pecho, si la receta la pide. Es una pieza
    # aparte y deliberada, no un descuido — el tejido fibroglandular sigue saliendo erosionado.
    # Se recorta a una bola centrada en el pezón (el punto más anterior de esa mama) para que
    # entre el pecho y no medio tórax.
    envoltura, pezon_mm = None, None
    if receta.get("envoltura_mm") or receta.get("envoltura_torso_mm"):
        lado_dcha = str(receta.get("lado", "derecha")).startswith("d")
        M = (afin @ np.vstack([np.indices(real.shape).reshape(3, -1),
                               np.ones(real.size)]))[:3].reshape(3, *real.shape)
        centro_x = float(M[0][cuerpo_m].mean())
        media = cuerpo_m & ((M[0] > centro_x) if lado_dcha else (M[0] <= centro_x))
        j = int(np.argmax(np.where(media, M[1], -1e9)))
        pezon_ijk = np.array(np.unravel_index(j, real.shape))
        # La POSICIÓN del pezón se guarda aunque su relieve se borre: es la referencia con la
        # que se orienta cualquiera en una mama, y un punto en un JSON no es un pezón dibujado.
        pezon_mm = [round(float(M[k].flat[j]), 2) for k in range(3)]
        # La COLA DE MAMA (prolongación axilar): extremo superoexterno de la mama. Se calcula
        # de su propia anatomía, no se coloca a ojo, y viaja como REGIÓN, no como lesión: el
        # PET de galio que la describe es en supino y esta mama es en prono, así que dibujar
        # ahí un contorno sería inventárselo. La cifra la pone el informe, no este cálculo.
        if receta.get("envoltura_torso_mm"):
            # LA MAMA DE VERDAD, no una bola recortada: una mama es, literalmente, lo que
            # SOBRESALE del tórax. Se calcula la apertura morfológica del cuerpo con una bola
            # grande (más ancha que una mama, más estrecha que un tórax): eso deja el tronco y
            # se come los pechos. Lo que falta entre el cuerpo y ese tronco ES la mama, con su
            # surco submamario y su unión real a la pared, sin cortes inventados.
            # La apertura va por transformada de distancia (erosión = distancia al borde ≥ R,
            # dilatación = distancia al erosionado ≤ R): con una bola de 4 cm, hacerlo con un
            # elemento estructurante explícito no cabe en el mini.
            from scipy import ndimage as _ndt
            R = float(receta["envoltura_torso_mm"])
            nucleo = _ndt.distance_transform_edt(cuerpo_m, sampling=esp) >= R
            torso = _ndt.distance_transform_edt(~nucleo, sampling=esp) <= R
            envoltura = media & cuerpo_m & ~torso
        else:
            envoltura = media & _bola_mm(real.shape, pezon_ijk, float(receta["envoltura_mm"]),
                                         esp)
        # Corte plano por detrás, a `envoltura_base_mm` del pezón: una mama real acaba contra la
        # pared torácica, no en una esfera. Sin esto la pieza sale con forma de almendra y no se
        # reconoce; con esto tiene base plana y perfil de mama.
        base = 0 if receta.get("envoltura_torso_mm") else float(receta.get("envoltura_base_mm") or 0)
        if base:
            envoltura = envoltura & (M[1] >= float(M[1].flat[j]) - base)
        # El PEZÓN. La envoltura sale del cuerpo SIN erosionar (si no, no habría superficie),
        # así que el pezón entra con ella. Se le pasa una apertura morfológica local —erosión y
        # dilatación con una bola de `pezon_suaviza_mm`— solo en su entorno: eso borra los
        # salientes más finos que la bola y deja la cúpula continua, sin abrir un agujero ni
        # cambiar el resto de la mama. Queda escrito en la receta, así que es una decisión
        # documentada y reproducible, no un retoque a mano.
        sua = float(receta.get("pezon_suaviza_mm") or 0)
        if sua:
            from scipy import ndimage as _ndp
            zona = _bola_mm(real.shape, pezon_ijk, max(22.0, 2.5 * sua), esp)
            abierto = _ndp.binary_opening(envoltura, structure=_bola_estructura(esp, sua))
            envoltura = np.where(zona, abierto, envoltura)
        # Solo el trozo pegado al pezón: si el corte deja islas de pared torácica, fuera.
        from scipy import ndimage as _ndc
        _c, _n = _ndc.label(envoltura)
        if _n:
            # El trozo que toca el PEZÓN, no el más grande: separando la mama del tórax, el
            # trozo más grande es el tórax. Si la apertura se ha comido el vóxel exacto del
            # pezón, se coge el componente cuyo centro esté más cerca de él.
            _et = int(_c[tuple(pezon_ijk)])
            if not _et:
                _cen = _ndc.center_of_mass(envoltura, _c, np.arange(1, _n + 1))
                _et = 1 + int(np.argmin([np.linalg.norm((np.array(q) - pezon_ijk) * esp)
                                         for q in _cen]))
            envoltura = _c == _et
    ancla = receta.get("semilla_mm") or np.argwhere(tumor).mean(axis=0).tolist()
    ijk = (np.round((np.linalg.inv(afin) @ np.append(ancla, 1.0))[:3]).astype(int)
           if receta.get("semilla_mm") else np.round(ancla).astype(int))
    contexto = _bola_mm(real.shape, ijk, float(receta["contexto_mm"]), esp)
    fgt = fibroglandular(agua, gra, dentro & contexto,
                         float(receta.get("ff_fibroglandular", FF_FIBROGLANDULAR)))
    fgt = fgt | (tumor & dentro)        # el tumor es parte del bloque, no un agujero
    diam = _diametro_axial_mayor(tumor, esp)
    # El suavizado va en MILÍMETROS y por eje, no en vóxeles: con vóxeles de 0,625 mm el mismo
    # número de vóxeles que en el hígado (2,5 mm) disolvía las ramas del árbol glandular y dejaba
    # bolas sueltas flotando, que no se leen como un órgano. 0,7 mm conserva las ramas.
    # LOS VASOS de la mama. Están en el mismo realce que el tumor, y son justo lo que estorbaba
    # al buscarlo: estructuras finas y muy brillantes. Aquí dejan de ser ruido y pasan a ser una
    # pieza. Se sacan con un top-hat blanco (el realce menos su apertura en escala de grises con
    # una bola de `vasos_mm`), que por construcción se queda con lo MÁS FINO que la bola y borra
    # lo ancho: una masa de 15 mm no sobrevive, un vaso de 2 mm sí. Es el filtro contrario al
    # desenfoque que usé para encontrar el tumor.
    vasos = None
    if receta.get("vasos_mm") and envoltura is not None:
        from scipy import ndimage as _ndv
        idxv = np.argwhere(envoltura)
        lo_v, hi_v = idxv.min(0), idxv.max(0) + 1
        sl_v = tuple(slice(a, b) for a, b in zip(lo_v, hi_v))
        rec = real[sl_v]
        th = rec - _ndv.grey_opening(rec, footprint=_bola_estructura(esp, float(receta["vasos_mm"])))
        env_v = envoltura[sl_v] & ~_ndv.binary_dilation(tumor[sl_v], iterations=2)
        if env_v.any():
            umbral_v = float(np.percentile(th[env_v], float(receta.get("vasos_pct", 99.0))))
            m_v = np.zeros_like(envoltura)
            m_v[sl_v] = env_v & (th >= umbral_v)
            # fuera el polvo: un vaso es un hilo continuo, no motas sueltas
            comp_v, n_v = _ndv.label(m_v)
            if n_v:
                tam_v = _ndv.sum(m_v, comp_v, index=np.arange(1, n_v + 1))
                minimo = float(receta.get("vasos_min_mm3", 60)) / float(np.prod(esp))
                m_v = np.isin(comp_v, 1 + np.nonzero(tam_v >= minimo)[0])
            vasos = m_v if m_v.any() else None
    mallas, lesiones_ = {}, []
    piezas = [("fgt", fgt, 0.7), ("tumor", tumor & dentro, 0.6)]
    if vasos is not None:
        piezas.insert(0, ("vasos", vasos, 0.5))
    if envoltura is not None:
        piezas.insert(0, ("mama", envoltura, 1.6))   # la piel, lisa: es una superficie, no un árbol
    for nombre, mask, suav_mm in piezas:
        suav = tuple(float(suav_mm) / float(e) for e in esp)
        r = _malla(mask, afin, suav, sobremuestreo=2 if nombre == "tumor" else 1,
                   min_componente=int(400 / float(np.prod(esp))) if nombre == "fgt" else 0,
                   taubin=30 if nombre == "mama" else (20 if nombre == "fgt" else 12))
        if not r:
            raise SystemExit("ABORTA: no sale malla de %s." % nombre)
        ply_binario(os.path.join(destino, nombre + ".ply"), *r)
        mallas[nombre] = nombre + ".ply"
    nib.save(nifti_limpio((fgt.astype(np.uint8) + tumor.astype(np.uint8)), afin, np.uint8),
             os.path.join(destino, "etiquetas.nii.gz"))
    fecha = m_agua["fecha"]
    salida = {"fecha": "%s-%s-%s" % (fecha[:4], fecha[4:6], fecha[6:]),
              "modalidad": m_agua["modalidad"], "organo": "mama",
              "lado": receta.get("lado", "derecha"),
              "espaciado_mm": [round(float(x), 3) for x in esp],
              "diametro_auto_mm": round(float(diam), 1),
              "mm_informe": receta.get("mm_informe"),
              "volumen_tumoral_ml": round(float(tumor.sum() * np.prod(esp) / 1000.0), 2),
              "pezon_mm": pezon_mm,
              "mallas": mallas, "registro": info}
    json.dump(salida, open(os.path.join(destino, "estudio.json"), "w"), ensure_ascii=False,
              indent=1)
    sellada = dict(receta)
    sellada.update({
        "serie_pre": pre, "serie_post": post, "serie_grasa": grasa, "estudio": info["estudio"],
        "piel_mm": piel_mm, "umbral_realce": round(umbral, 4) if umbral is not None else None,
        "origen_tumor": "modelo:MAMA-MIA" if receta.get("serie_modelo") else "semilla",
        "ff_fibroglandular": float(receta.get("ff_fibroglandular", FF_FIBROGLANDULAR)),
        "registro": info["registro"], "dice_cuerpo_despues": info["dice_cuerpo_despues"],
        "procedencia": ("modelo-publico-cotejado" if receta.get("serie_modelo")
                        else "semiautomatica-con-semilla"),
        "totalsegmentator": _version_ts(), "visor3d": huella(open(__file__, "rb").read()),
        "sha256_realce": _sha256(ruta_realce),
        "sha256_mallas": {k: _sha256(os.path.join(destino, v)) for k, v in mallas.items()},
    })
    json.dump(sellada, open(os.path.join(destino, "receta.json"), "w"), ensure_ascii=False,
              indent=1)
    return salida


def _version_ts():
    try:
        import importlib.metadata as md
        return md.version("totalsegmentator")
    except Exception:
        return None


def _cmd_mama(a):
    globals()["ORGANO"] = "mama"      # olvidarse de --organo escribiría bajo higado/
    # `--receta -` la lee de la entrada estándar: así el texto del informe que justifica la
    # localización no tiene que pasar por un fichero fuera de la zona clínica. La copia sellada
    # se escribe al lado de las mallas, que ya están dentro.
    receta = json.load(sys.stdin) if a.receta == "-" else json.load(open(a.receta,
                                                                       encoding="utf-8"))
    _, m = convierte(a.raices, a.pre)
    destino = _dir("assets", m["fecha"])
    r = assets_mama(a.raices, a.pre, a.post, a.grasa, receta, destino)
    print("%s · tumor %.1f mm (informe: %s mm) · %s" % (
        r["fecha"], r["diametro_auto_mm"], r.get("mm_informe"), os.path.basename(destino)))
    print("registro: dice cuerpo %s → %s" % (r["registro"]["dice_cuerpo_antes"],
                                             r["registro"]["dice_cuerpo_despues"]))
    return 0


def _cmd_focos(a):
    """Los focos de realce mayores de la resta, con su centro en mm: de aquí sale la semilla,
    que luego se COTEJA contra el informe antes de escribirla en la receta."""
    import nibabel as nib
    import numpy as np
    from scipy import ndimage
    ruta, info = realce(a.raices, a.pre, a.post, a.registro)
    im = nib.load(ruta)
    afin, vol = im.affine, np.asarray(im.dataobj, dtype=np.float32)
    esp = np.sqrt((afin[:3, :3] ** 2).sum(axis=0))
    r_agua, _ = convierte(a.raices, a.pre)
    r_grasa, _ = convierte(a.raices, a.grasa) if a.grasa else (None, None)
    dentro = sin_piel(cuerpo(np.asarray(nib.load(r_agua).dataobj, dtype=np.float32)
                             + (np.asarray(nib.load(r_grasa).dataobj, dtype=np.float32)
                                if r_grasa else 0)), esp)
    mundo = (afin @ np.vstack([np.indices(vol.shape).reshape(3, -1), np.ones(vol.size)]))
    if a.caja:
        # Caja explícita en mm (x0,x1,y0,y1,z0,z1). El corte «anterior» por porcentaje se comía
        # justo el cuadrante EXTERNO, que es donde está esta lesión: la mama se ensancha hacia
        # atrás según se va a lateral. Una caja mirada en la anatomía es honesta y reproducible.
        c = [float(t) for t in a.caja.split(",")]
        M = mundo[:3].reshape(3, *vol.shape)
        dentro = (dentro & (M[0] >= c[0]) & (M[0] <= c[1]) & (M[1] >= c[2]) & (M[1] <= c[3])
                  & (M[2] >= c[4]) & (M[2] <= c[5]))
    if a.adc:
        # Difusión restringida: un carcinoma invasivo tiene ADC bajo; el realce de fondo y los
        # vasos, no. Es el discriminador estándar y aquí sale gratis, el mapa ADC ya está en el
        # estudio. Se remuestrea al dinámico (es más grueso: 4,9 mm).
        from nibabel.processing import resample_from_to
        adc = np.asarray(resample_from_to(nib.load(convierte(a.raices, a.adc)[0]), im,
                                          order=1).dataobj, dtype=np.float32)
        dentro = dentro & (adc > 50) & (adc < a.adc_max)
    if a.suave:
        from scipy import ndimage as _nd
        vol = _nd.gaussian_filter(vol, [a.suave / e for e in esp])
    if a.tardio:
        # Cinética: el tumor capta PRONTO y luego se estanca o se lava; el realce de fondo de la
        # mama (BPE) sigue subiendo hasta el final. Quedarse con lo que NO sigue subiendo es el
        # filtro estándar para separar masa de fondo, y aquí sale gratis: las 6 fases ya están.
        a_pre = np.asarray(nib.load(convierte(a.raices, a.pre)[0]).dataobj, dtype=np.float32)
        a_tar = np.asarray(nib.load(convierte(a.raices, a.tardio)[0]).dataobj, dtype=np.float32)
        dentro = dentro & ((a_tar - a_pre) <= 1.05 * vol)
    if a.anterior:
        # La mama es lo ANTERIOR del cuerpo. Sin este corte, los focos que más realzan son el
        # corazón, los grandes vasos y las arterias de los brazos, no la mama.
        idx0 = np.argwhere(dentro)
        ya = np.array([(afin @ np.append(v, 1.0))[1] for v in
                       (idx0.min(0), idx0.max(0))])
        y = (afin @ np.vstack([np.indices(vol.shape).reshape(3, -1),
                               np.ones(vol.size)]))[1].reshape(vol.shape)
        corte = ya.min() + (a.anterior / 100.0) * (ya.max() - ya.min())
        dentro = dentro & (y >= corte)
    m = dentro & (vol >= np.percentile(vol[dentro], a.percentil))
    comp, n = ndimage.label(m)
    if not n:
        print("sin focos por encima del percentil %s" % a.percentil)
        return 1
    tam = ndimage.sum(m, comp, index=np.arange(1, n + 1))
    orden = np.argsort(tam)[::-1][:a.n]
    print("registro: dice cuerpo %s → %s" % (info["dice_cuerpo_antes"], info["dice_cuerpo_despues"]))
    # La posición se da RELATIVA al cuerpo, no en coordenadas del escáner: así se puede casar
    # con lo que dice el informe («cuadrante superoexterno de la mama derecha») sin abrir el
    # visor, y sin que salga una coordenada que identifique nada.
    idx = np.argwhere(dentro)
    lo, hi = idx.min(0), idx.max(0)
    cen = (afin @ np.append(idx.mean(0), 1.0))[:3]
    e0 = (afin @ np.append(lo, 1.0))[:3]
    e1 = (afin @ np.append(hi, 1.0))[:3]
    print("cuerpo: centro [%.0f, %.0f, %.0f] · caja x %.0f→%.0f  y %.0f→%.0f  z %.0f→%.0f (mm)"
          % (tuple(cen) + (e0[0], e1[0], e0[1], e1[1], e0[2], e1[2])))
    # «compacidad» = volumen real / volumen de la esfera de su diámetro mayor. Una MASA de 15 mm
    # está cerca de 1; el realce de fondo (BPE) y los vasos salen alargados y dan cifras bajas.
    # Es lo que separa un tumor de la maraña de realce normal sin mirar la imagen.
    print("%-3s %8s %7s %6s  %-24s %-14s %s" % ("#", "mm3", "diam", "compac",
                                                "centro mm (RAS)", "lado/altura", "realce"))
    filas = []
    for i in orden:
        mask = comp == i + 1
        c = (afin @ np.append(np.argwhere(mask).mean(axis=0), 1.0))[:3]
        lado = "dcha" if c[0] > cen[0] else "izda"
        if a.lado and lado != a.lado:
            continue
        d = _diametro_axial_mayor(mask, esp)
        vmm = tam[i] * np.prod(esp)
        compac = vmm / (np.pi / 6.0 * d ** 3) if d > 0 else 0.0
        prof = 100.0 * (c[1] - min(e0[1], e1[1])) / max(1e-6, abs(e1[1] - e0[1]))
        alt = 100.0 * (c[2] - min(e0[2], e1[2])) / max(1e-6, abs(e1[2] - e0[2]))
        filas.append((vmm, d, compac, c, lado, prof, alt, float(vol[mask].mean())))
    for k, (vmm, d, compac, c, lado, prof, alt, med) in enumerate(filas, 1):
        print("%-3d %8.0f %7.1f %6.2f  %-24s %-14s %.0f" % (
            k, vmm, d, compac, "[%.0f, %.0f, %.0f]" % tuple(c),
            "%s y%.0f%% z%.0f%%" % (lado, prof, alt), med))
    return 0


def _diagnostico(a):
    """Dónde caen las imágenes de diagnóstico de `cortes`.

    Por defecto, DENTRO de la zona clínica: son proyecciones de su resonancia y, aunque salgan
    en gris y sin cabeceras, sacarlas del muro por costumbre es como empiezan estas cosas.
    `--a-marca` las manda a borradores de marca, que es lo único que un humano puede abrir para
    mirarlas: es una salida deliberada, se pide a mano y queda en el log de la ventanilla.
    """
    dst = _marca("_diagnostico") if getattr(a, "a_marca", False) else _dir("_diagnostico")
    if not getattr(a, "a_marca", False):
        exige_zona_clinica(dst)
    os.makedirs(dst, exist_ok=True)
    return dst


def _cmd_cortes(a):
    """Proyecciones (MIP) del realce para MIRAR qué se está segmentando antes de mallar.

    Sale a borradores de marca, no a la web: imagen derivada en gris, sin cabeceras ni texto
    quemado. Existe para cotejar lado y cuadrante contra el informe en vez de fiarse de una
    coordenada — la semilla de la mama la pone un humano y equivocarse de mama es barato."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import nibabel as nib
    import numpy as np
    ruta, info = realce(a.raices, a.pre, a.post, a.registro)
    im = nib.load(ruta)
    afin, vol = im.affine, np.asarray(im.dataobj, dtype=np.float32)
    esp = np.sqrt((afin[:3, :3] ** 2).sum(axis=0))
    r_agua, _ = convierte(a.raices, a.pre)
    r_grasa, _ = convierte(a.raices, a.grasa)
    agua = np.asarray(nib.load(r_agua).dataobj, dtype=np.float32)
    gra = np.asarray(nib.load(r_grasa).dataobj, dtype=np.float32)
    dentro = sin_piel(cuerpo(agua + gra), esp, a.piel)
    ejes = "".join(nib.aff2axcodes(afin))
    if ejes != "RAS":
        raise SystemExit("ABORTA: esperaba ejes RAS y son %s; el lado saldría cambiado." % ejes)
    mundo = (afin @ np.vstack([np.indices(vol.shape).reshape(3, -1), np.ones(vol.size)]))
    Y = mundo[1].reshape(vol.shape)
    if a.anterior:
        ya = Y[dentro]
        dentro = dentro & (Y >= ya.min() + (a.anterior / 100.0) * (ya.max() - ya.min()))
    if a.caja:
        c = [float(t) for t in a.caja.split(",")]
        M = mundo[:3].reshape(3, *vol.shape)
        dentro = (dentro & (M[0] >= c[0]) & (M[0] <= c[1]) & (M[1] >= c[2]) & (M[1] <= c[3])
                  & (M[2] >= c[4]) & (M[2] <= c[5]))
    if a.anatomia:
        # La anatomía (agua+grasa), no el realce: para comprobar el LADO contra algo que no se
        # discute. Equivocarse de mama es el error caro aquí.
        vol = agua + gra
    if a.fgt:
        # El tejido fibroglandular tal y como iría a la malla: con la piel YA erosionada. Un MIP
        # coronal de esto es, de hecho, la silueta que se vería de frente en el visor.
        vol = fibroglandular(agua, gra, dentro, a.ff).astype(np.float32)
    if a.imagen:
        # Cualquier otra serie del estudio (p. ej. el mapa ADC), remuestreada al dinámico.
        from nibabel.processing import resample_from_to
        vol = np.asarray(resample_from_to(nib.load(convierte(a.raices, a.imagen)[0]), im,
                                          order=1).dataobj, dtype=np.float32)
    vol = np.where(dentro, vol, 0.0)
    if a.suave:
        # Un tumor es una MASA: sobrevive a un desenfoque de varios milímetros. Los vasos y el
        # realce parenquimatoso son finos y se apagan. Es un detector de masa de una línea.
        from scipy import ndimage
        vol = ndimage.gaussian_filter(vol, [a.suave / e for e in esp])
        vol = np.where(dentro, vol, 0.0)
    X = mundo[0].reshape(vol.shape)
    cen_x = float(X[dentro].mean())
    idx = np.argwhere(dentro)
    lo, hi = idx.min(0), idx.max(0)
    sl = tuple(slice(l, h + 1) for l, h in zip(lo, hi))
    v = vol[sl]
    x0 = float((afin @ np.append(lo, 1.0))[0])
    z0 = float((afin @ np.append(lo, 1.0))[2])
    if a.axiales:
        # Montaje de cortes AXIALES (el plano nativo de la RM de mama): una masa se ve redonda
        # y compacta ahí, mientras que en el MIP coronal se funde con el resto del realce.
        z0_, z1_, paso = [float(t) for t in a.axiales.split(":")]
        Z = mundo[2].reshape(vol.shape)
        zs = np.arange(z0_, z1_ + 1e-6, paso)
        n = len(zs)
        cols = min(4, n)
        filas_ = int(np.ceil(n / cols))
        ix = np.argwhere(dentro.any(axis=(1, 2)))[[0, -1], 0]
        iy = np.argwhere(dentro.any(axis=(0, 2)))[[0, -1], 0]
        if a.lado:
            mitad = int(np.argmin(np.abs(X[:, 0, 0] - X[dentro].mean())))
            ix = (mitad, ix[1]) if a.lado == "dcha" else (ix[0], mitad)
        xs = X[ix[0]:ix[1] + 1, 0, 0]
        ys = Y[0, iy[0]:iy[1] + 1, 0]
        fig, axs = plt.subplots(filas_, cols, figsize=(5.2 * cols, 5.2 * filas_ * len(ys)
                                                       / max(1, len(xs))),
                                facecolor="black", squeeze=False)
        for j, zz in enumerate(zs):
            k = int(np.argmin(np.abs(Z[0, 0, :] - zz)))
            corte = vol[ix[0]:ix[1] + 1, iy[0]:iy[1] + 1, k]
            eje = axs[j // cols][j % cols]
            eje.imshow(np.rot90(corte), cmap="gray",
                       vmax=float(np.percentile(corte[corte > 0], 99)) if (corte > 0).any() else 1,
                       extent=[xs[0], xs[-1], ys[0], ys[-1]], aspect="equal")
            eje.set_title("z = %.0f mm" % Z[0, 0, k], color="white", fontsize=9)
            eje.tick_params(colors="#777", labelsize=6)
            for marca in (a.semilla or []):
                mx, my, mz = [float(t) for t in marca.split(",")]
                if abs(mz - Z[0, 0, k]) <= paso / 2:
                    eje.plot([mx], [my], "o", mfc="none", mec="#ff5d73", ms=22, mew=1.6)
        for j in range(n, filas_ * cols):
            axs[j // cols][j % cols].axis("off")
        fig.suptitle("realce post−pre · cortes axiales · x+ = DERECHA de ella",
                     color="white", fontsize=11)
        ruta_png = os.path.join(_diagnostico(a), "%s-axiales-BORRADOR.png" % a.nombre)
        fig.savefig(ruta_png, dpi=110, facecolor="black", bbox_inches="tight")
        plt.close(fig)
        print("axiales →", os.path.relpath(ruta_png, REPO))
        return 0
    mip = np.rot90(v.max(axis=1))            # coronal: columnas = R, filas = S (arriba, craneal)
    alto, ancho = mip.shape
    fig, ax = plt.subplots(figsize=(ancho / 55.0, alto / 55.0), facecolor="black")
    ax.imshow(mip, cmap="gray", vmax=float(np.percentile(mip[mip > 0], 99.5)))
    ax.axvline((cen_x - x0) / esp[0], color="#4ea3ff", lw=0.8, alpha=0.6)
    ax.text(0.02, 0.97, "IZQUIERDA de ella", color="#ffd166", fontsize=9, va="top",
            transform=ax.transAxes)
    ax.text(0.98, 0.97, "DERECHA de ella", color="#ffd166", fontsize=9, va="top", ha="right",
            transform=ax.transAxes)
    for marca in (a.semilla or []):
        mx, my, mz = [float(t) for t in marca.split(",")]
        ax.plot([(mx - x0) / esp[0]], [alto - 1 - (mz - z0) / esp[2]], "o", mfc="none",
                mec="#ff5d73", ms=18, mew=1.6)
    ax.set_xticks(np.arange(0, ancho, 20.0 / esp[0]))
    ax.set_xticklabels(["%.0f" % (x0 + t * esp[0]) for t in ax.get_xticks()], fontsize=6)
    ax.set_yticks(np.arange(0, alto, 20.0 / esp[2]))
    ax.set_yticklabels(["%.0f" % (z0 + (alto - 1 - t) * esp[2]) for t in ax.get_yticks()],
                       fontsize=6)
    ax.tick_params(colors="#888")
    ax.set_title("realce post−pre · MIP coronal · mm RAS", color="white", fontsize=10)
    ruta_png = os.path.join(_diagnostico(a), "%s-realce-coronal-BORRADOR.png" % a.nombre)
    fig.savefig(ruta_png, dpi=130, facecolor="black", bbox_inches="tight")
    plt.close(fig)
    print("MIP coronal →", os.path.relpath(ruta_png, REPO))
    print("caja del recorte: x %.0f→%.0f  z %.0f→%.0f  · midline x=%.0f"
          % (x0, x0 + (ancho - 1) * esp[0], z0, z0 + (alto - 1) * esp[2], cen_x))
    return 0


# ─── modelo público de lesión de mama (MAMA-MIA nnU-Net) ────────────────────────────────
#
# Lo que NO hay en TotalSegmentator sí existe publicado: el nnU-Net de referencia del reto
# MAMA-MIA (Garrucho et al., Sci Data 2025, 10.1038/s41597-025-04707-4), pesos en Synapse
# syn61247992, licencia CC BY-NC 4.0 — uso sin ánimo de lucro, que es el caso.
# Corre LOCAL, como todo lo demás: lo único que sale del Mac es la descarga de los pesos.
# Synapse no deja bajar como anónimo (403: «Anonymous users have only READ access permission»)
# pero el fichero NO tiene requisitos de acceso (accessRequirement: 0 resultados), así que
# basta una cuenta gratuita y un token personal. El token vive en el Llavero, nunca en el repo.

MODELOS = os.path.join(os.path.expanduser("~"), ".cache", "btp", "modelos")
SYNAPSE_API = "https://repo-prod.prod.sagebase.org/repo/v1"
MAMA_MIA = {
    "entidad": "syn61906708",       # full_image_dce_mri_tumor_segmentation.zip
    "carpeta": "mama-mia",
    "llavero": "btp-synapse-token",
    "licencia": "CC BY-NC 4.0",
    "cita": "Garrucho et al., Sci Data 2025, 10.1038/s41597-025-04707-4",
}


def _token_synapse():
    import importlib.util
    ruta = os.path.join(_TOOLS, "_secrets.py")
    spec = importlib.util.spec_from_file_location("_secrets", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tok = mod.get(MAMA_MIA["llavero"])
    if not tok:
        raise SystemExit(
            "ABORTA: no hay token de Synapse en el Llavero (%s).\n"
            "  1) cuenta gratuita en https://www.synapse.org/register\n"
            "  2) token con permisos View y Download en "
            "https://www.synapse.org/PersonalAccessTokens\n"
            "  3) guardarlo SIN que quede en el historial:\n"
            "       security add-generic-password -U -a \"$USER\" -s %s -w"
            % (MAMA_MIA["llavero"], MAMA_MIA["llavero"]))
    return tok


def descarga_modelo_mama(destino=None):
    """Baja los pesos de MAMA-MIA de Synapse y los descomprime. Idempotente."""
    import urllib.request
    import zipfile
    destino = destino or os.path.join(MODELOS, MAMA_MIA["carpeta"])
    marca = os.path.join(destino, ".completo")
    if os.path.exists(marca):
        return destino
    os.makedirs(destino, exist_ok=True)
    cab = {"Authorization": "Bearer " + _token_synapse()}
    url = "%s/entity/%s/file?redirect=false" % (SYNAPSE_API, MAMA_MIA["entidad"])
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=60) as r:
            presignada = r.read().decode("utf-8").strip()
    except Exception as e:
        raise SystemExit("ABORTA: Synapse no da la URL de descarga (%r). Revisa que el token "
                         "tenga permiso de Download y no haya caducado." % (e,))
    zip_ruta = os.path.join(destino, "pesos.zip")
    print("bajando pesos de MAMA-MIA…", flush=True)
    urllib.request.urlretrieve(presignada, zip_ruta)
    sha = _sha256(zip_ruta)
    with zipfile.ZipFile(zip_ruta) as z:
        for m in z.namelist():
            if m.startswith("/") or ".." in m.split("/"):
                raise SystemExit("ABORTA: el zip trae una ruta fuera de destino (%s)." % m)
        z.extractall(destino)
    os.remove(zip_ruta)
    json.dump({"entidad": MAMA_MIA["entidad"], "sha256_zip": sha,
               "licencia": MAMA_MIA["licencia"], "cita": MAMA_MIA["cita"],
               "bajado": time.strftime("%Y-%m-%d")},
              open(marca, "w"), ensure_ascii=False, indent=1)
    return destino


def _cmd_modelo(a):
    d = os.path.join(MODELOS, MAMA_MIA["carpeta"])
    if a.descarga:
        d = descarga_modelo_mama()
    marca = os.path.join(d, ".completo")
    if not os.path.exists(marca):
        print("MAMA-MIA: no instalado. `modelo --descarga` lo baja (hace falta el token del "
              "Llavero %s)." % MAMA_MIA["llavero"])
        return 1
    print("MAMA-MIA instalado en %s" % d)
    print(json.dumps(json.load(open(marca)), ensure_ascii=False))
    for raiz, _, ficheros in os.walk(d):
        for f in sorted(ficheros):
            if f.endswith((".json", ".pth")) and not f.startswith("."):
                print("  %s" % os.path.relpath(os.path.join(raiz, f), d))
    return 0


def _dispositivo(pedido=None):
    """GPU del Mac si la hay. NO es una preferencia de velocidad: es lo que hace que quepa.

    Medido el 20-sep-26 en el mini de 16 GB: UN solo parche de 128³ por esta red en CPU llegó a
    15 GB de footprint (la conv3d de PyTorch en CPU materializa el im2col: 32 canales × 27 del
    núcleo × 128³ × 4 B ya son 7 GB) y tardó ~136 s. La misma red en MPS: ~1 GB y 0,2 s.
    Con CPU esto tumbó la máquina dos veces, con swap a 10,5 de 11,2 GB.

    El riesgo conocido de MPS es que devuelva NaN en silencio, así que se comprobó en vez de
    suponerlo: mismo parche y misma entrada en CPU y en MPS dan una diferencia máxima de 3e-5 en
    los logits, 0 en las probabilidades y el 100 % de vóxeles con la misma etiqueta
    (tests/test_visor3d_mps.py). Si no hay MPS, se cae a CPU y se avisa de lo que va a costar."""
    import torch
    if pedido and pedido != "auto":
        return pedido
    if torch.backends.mps.is_available():
        return "mps"
    print("AVISO: sin GPU (MPS); en CPU esta red pide ~15 GB por parche y tarda ~2 min cada uno.",
          file=sys.stderr)
    return "cpu"


def tumor_modelo(raices, serie, folds=(0,), device="auto"):
    """Máscara del tumor de mama con el nnU-Net de MAMA-MIA. 100 % local, sin red.

    El modelo espera UN canal: la imagen T1 post-contraste (en un Dixon, la serie WATER, que
    es la que va con la grasa suprimida, como el T1 con saturación grasa con el que se entrenó).
    Todo lo demás —1 mm isótropo, z-score— lo hace nnU-Net con su `plans.json`."""
    import shutil
    import torch
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
    modelo = os.path.join(MODELOS, MAMA_MIA["carpeta"], "full_image_dce_mri_tumor_segmentation")
    if not os.path.isdir(modelo):
        raise SystemExit("ABORTA: no están los pesos de MAMA-MIA. Corre `modelo --descarga`.")
    imagen, meta = convierte(raices, serie)
    if meta.get("texto_quemado"):
        raise SystemExit("ABORTA: la serie %s lleva texto quemado." % serie)
    base = os.path.join(_cache(serie), "mama_mia")
    salida = os.path.join(base, "salida")
    marca = os.path.join(salida, "caso.nii.gz")
    if os.path.exists(marca):
        return marca
    entrada = os.path.join(base, "entrada")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(entrada, exist_ok=True)
    os.makedirs(salida, exist_ok=True)
    exige_zona_clinica(base)
    shutil.copy(imagen, os.path.join(entrada, "caso_0000.nii.gz"))
    device = _dispositivo(device)
    p = nnUNetPredictor(tile_step_size=0.5, use_gaussian=True, use_mirroring=False,
                        device=torch.device(device), verbose=False, allow_tqdm=True)
    p.initialize_from_trained_model_folder(modelo, use_folds=tuple(folds),
                                           checkpoint_name="checkpoint_final.pth")
    p.predict_from_files(entrada, salida, save_probabilities=False, overwrite=True,
                         num_processes_preprocessing=2, num_processes_segmentation_export=2)
    shutil.rmtree(entrada, ignore_errors=True)
    if not os.path.exists(marca):
        raise SystemExit("ABORTA: el modelo no dejó máscara en %s" % salida)
    return marca


def _cmd_tumor(a):
    """Lo que el modelo encuentra, con su sitio en el cuerpo, para poder COTEJARLO contra el
    informe antes de mallar nada. El modelo propone; el informe manda."""
    import nibabel as nib
    import numpy as np
    from scipy import ndimage
    globals()["ORGANO"] = "mama"
    ruta = tumor_modelo(a.raices, a.serie, folds=a.folds, device=a.device)
    im = nib.load(ruta)
    afin = im.affine
    mask = np.asarray(im.dataobj) > 0
    esp = np.sqrt((afin[:3, :3] ** 2).sum(axis=0))
    r_agua, _ = convierte(a.raices, a.serie)
    cuerpo_m = cuerpo(np.asarray(nib.load(r_agua).dataobj, dtype=np.float32))
    idx = np.argwhere(cuerpo_m)
    cen = (afin @ np.append(idx.mean(0), 1.0))[:3]
    e0 = (afin @ np.append(idx.min(0), 1.0))[:3]
    e1 = (afin @ np.append(idx.max(0), 1.0))[:3]
    comp, n = ndimage.label(mask)
    if not n:
        print("el modelo no encuentra ninguna lesión en esta serie")
        return 1
    tam = ndimage.sum(mask, comp, index=np.arange(1, n + 1))
    # El informe habla en CUADRANTES, y un cuadrante se define respecto al PEZÓN, no respecto a
    # la caja del estudio: superior/inferior es z contra el pezón, externo/interno es alejarse o
    # acercarse a la línea media desde él. Sin esto, «CSE» y una coordenada no se pueden cotejar.
    pezon = {}
    Xc = (afin @ np.vstack([np.indices(mask.shape).reshape(3, -1),
                            np.ones(mask.size)]))[:3].reshape(3, *mask.shape)
    for lado, sel in (("dcha", Xc[0] > cen[0]), ("izda", Xc[0] <= cen[0])):
        m = cuerpo_m & sel
        if m.any():
            j = np.argmax(np.where(m, Xc[1], -1e9))       # el punto más ANTERIOR de esa mama
            pezon[lado] = np.array([Xc[k].flat[j] for k in range(3)])
            print("pezón %s (punto más anterior de esa mama): [%.0f, %.0f, %.0f]"
                  % ((lado,) + tuple(pezon[lado])))
    print("máscara: %s" % os.path.relpath(ruta, SALIDA_RAIZ))
    print("%-3s %9s %7s %6s  %-24s %s" % ("#", "mm3", "diam", "compac", "centro mm (RAS)",
                                          "dónde cae"))
    for k, i in enumerate(np.argsort(tam)[::-1][:a.n], 1):
        m = comp == i + 1
        c = (afin @ np.append(np.argwhere(m).mean(axis=0), 1.0))[:3]
        d = _diametro_axial_mayor(m, esp)
        vmm = tam[i] * np.prod(esp)
        lado = "dcha" if c[0] > cen[0] else "izda"
        if not cuerpo_m[tuple(np.clip(np.round(
                (np.linalg.inv(afin) @ np.append(c, 1.0))[:3]).astype(int),
                0, np.array(mask.shape) - 1))]:
            sitio = "FUERA DEL CUERPO (artefacto)"
        elif lado in pezon:
            pz = pezon[lado]
            vert = "superior" if c[2] > pz[2] else "inferior"
            fuera = abs(c[0] - cen[0]) > abs(pz[0] - cen[0])
            sitio = "%s · cuadrante %s%s · %.0f mm del pezón" % (
                lado, vert[:5], "externo" if fuera else "interno",
                float(np.linalg.norm(c - pz)))
        else:
            sitio = lado
        print("%-3d %9.0f %7.1f %6.2f  %-24s %s" % (
            k, vmm, d, vmm / (np.pi / 6.0 * d ** 3) if d else 0,
            "[%.0f, %.0f, %.0f]" % tuple(c), sitio))
    return 0


# ─── el esqueleto de verdad, no un dibujo ────────────────────────────────────────────────
#
# El esquema del esqueleto de /lesiones y /mapa-metastasis son trazados SVG hechos a mano: una
# mancha por cráneo, rectángulos por vértebras, arcos por costillas. Es lo único de esas páginas
# que no sale de los datos de {{TITULAR}}. Esto lo sustituye por SU esqueleto, segmentado de su TC de
# cuerpo entero con kernel de hueso, y —lo que importa más— saca el CENTROIDE de cada hueso con
# su nombre, de modo que la posición de cada foco deja de estar puesta a ojo.

CIERRE_MM = 3.0         # radio del cierre que vuelve a unir las costillas partidas
SIMETRIA_MIN_MM3 = 400  # por debajo de esto, un hueso par se da por ausente y se copia del otro lado


def _une_fragmentos(mask, lab, nombres, esp, radio_mm=9.0):
    """Une consigo mismos los trozos de un hueso largo que la segmentación dejó partido.

    Medido en este TC: sus costillas salen con 54 a 10.000 mm³ cuando una costilla entera
    ronda los 20.000, y la clavícula derecha con 621 de unos 8.000. No es que falte el hueso:
    es que sale a cachos, y una parrilla a cachos se lee como avería del sistema.

    El cierre se hace POR ETIQUETA, no sobre la máscara entera: así los trozos de la misma
    costilla se juntan entre ellos y no se pega una costilla con su vecina, que es lo que
    pasaría con un cierre global del mismo radio.
    """
    import numpy as np
    from scipy import ndimage
    ids = {v: k for k, v in nombres.items()}
    largos = [n for n in nombres.values()
              if "rib_" in n or "clavicula" in n or "humerus" in n]
    bola = _bola_estructura(esp, radio_mm)
    unidos = 0
    for n in largos:
        i = ids.get(n)
        if i is None:
            continue
        m = lab == i
        if not m.any():
            continue
        _, trozos = ndimage.label(m)
        if trozos < 2:
            continue
        # se trabaja en la caja del hueso, no en el volumen entero: un cierre con bola de 9 mm
        # sobre todo el TC no cabe en el mini y además no hace falta
        caja = ndimage.find_objects(m.astype(np.uint8))[0]
        caja = tuple(slice(max(0, s.start - 12), min(d, s.stop + 12))
                     for s, d in zip(caja, m.shape))
        mask[caja] |= ndimage.binary_closing(m[caja], bola)
        unidos += 1
    if unidos:
        print("fragmentos: %d hueso(s) largos unidos consigo mismos (radio %g mm)"
              % (unidos, radio_mm))
    return mask


def _puentea_columna(mask, lab, nombres):
    """Cierra los tramos de columna que el modelo no segmentó, interpolando entre los bordes.

    En este TC faltan T11 y T12 enteras (medido: el mayor trozo de T11 tenía 28 vóxeles), y eso
    deja un VACÍO entre el tórax y la lumbar — justo donde está el foco más intenso de la serie.
    Un agujero ahí es peor que una reconstrucción: parece que no hay nada donde sí hay hueso.

    Una columna es una cadena continua, así que un hueco interior no es anatomía, es que falta
    el dato. Se rellena interpolando la SECCIÓN entre el corte lleno de arriba y el de abajo,
    con distancias con signo (el morphing estándar de formas: la forma intermedia es el nivel
    cero de la mezcla de las dos distancias). No inventa un hueso donde no hay columna: solo
    une dos trozos de la misma columna.
    """
    import numpy as np
    from scipy import ndimage
    ids = {v: k for k, v in nombres.items()}
    vert = np.isin(lab, [ids[n] for n in CADENA if n in ids])
    if not vert.any():
        return mask
    lleno = vert.any(axis=(0, 1))
    zz = np.nonzero(lleno)[0]
    lo, hi = int(zz.min()), int(zz.max())
    huecos, z = [], lo
    while z <= hi:
        if not lleno[z]:
            ini = z
            while z <= hi and not lleno[z]:
                z += 1
            huecos.append((ini, z - 1))
        else:
            z += 1
    if not huecos:
        return mask
    puestos = 0
    for ini, fin in huecos:
        a, b = vert[:, :, ini - 1], vert[:, :, fin + 1]
        if not a.any() or not b.any():
            continue
        # distancia con signo: negativa dentro, positiva fuera
        da = ndimage.distance_transform_edt(~a) - ndimage.distance_transform_edt(a)
        db = ndimage.distance_transform_edt(~b) - ndimage.distance_transform_edt(b)
        for k in range(ini, fin + 1):
            t = (k - ini + 1) / (fin - ini + 2)
            mask[:, :, k] |= ((1 - t) * da + t * db) < 0
            puestos += 1
    if puestos:
        print("columna: %d corte(s) puenteados en %d hueco(s) — RECONSTRUIDOS, no medidos"
              % (puestos, len(huecos)))
    return mask


def _simetriza_costillas(mask, lab, nombres, esp):
    """Recupera del otro lado las costillas que faltan. Solo costillas, y solo si faltan.

    Medido en este TC: salen 9 costillas derechas y 10 izquierdas de 12, y no las mismas. Una
    parrilla con huecos asimétricos se lee como fallo, no como anatomía. Un tórax es simétrico
    dentro de lo que este render puede distinguir, así que la que falta se copia de su pareja.

    Se limita a las costillas a propósito: espejar el esqueleto entero también duplicaría lo
    que de verdad es asimétrico y taparía cosas que conviene ver tal cual.
    """
    import numpy as np
    ids = {v: k for k, v in nombres.items()}
    vol = float(np.prod(esp))
    eje_x = float(np.argwhere(mask)[:, 0].mean()) if mask.any() else mask.shape[0] / 2
    copiadas = []
    for k in range(1, 13):
        par = {}
        for lado in ("left", "right"):
            i = ids.get("rib_%s_%d" % (lado, k))
            par[lado] = (lab == i) if i is not None else np.zeros_like(mask)
        v_i, v_d = par["left"].sum() * vol, par["right"].sum() * vol
        if v_i < SIMETRIA_MIN_MM3 and v_d >= SIMETRIA_MIN_MM3:
            falta, tiene, nom = "left", "right", "izquierda"
        elif v_d < SIMETRIA_MIN_MM3 and v_i >= SIMETRIA_MIN_MM3:
            falta, tiene, nom = "right", "left", "derecha"
        else:
            continue
        # espejo respecto al eje medio del cuerpo, redondeando al vóxel
        idx = np.argwhere(par[tiene])
        idx[:, 0] = np.clip(np.round(2 * eje_x - idx[:, 0]).astype(int), 0, mask.shape[0] - 1)
        mask[idx[:, 0], idx[:, 1], idx[:, 2]] = True
        copiadas.append("%d%s" % (k, nom[0]))
    if copiadas:
        print("simetría: %d costilla(s) copiadas del otro lado (%s) — RECONSTRUIDAS, no medidas"
              % (len(copiadas), " ".join(copiadas)))
    return mask


def _reconstruye(mask, esp):
    """Cierra los agujeros que la segmentación deja y recupera por simetría lo que falta.

    Aquí el esqueleto deja de ser SOLO medida y pasa a ser RECONSTRUCCIÓN, y eso hay que
    decirlo donde se publique. La razón para hacerlo igualmente: este esqueleto es un fondo
    para situar los focos, y lo que sustituye es un dibujo hecho a mano entero. Una parrilla
    costal con mordiscos se lee como error del sistema, no como anatomía, y distrae de lo único
    que aquí es dato de verdad, que son los focos.

    Lo que NO se toca: la posición de los focos, que sale de los centroides, no de esta máscara.
    """
    import numpy as np
    from scipy import ndimage
    # 1) agujeros interiores: hueso rodeado de hueso que la segmentación se dejó
    lleno = ndimage.binary_fill_holes(mask)
    # y también por cortes, que pilla los que en 3D tienen salida por algún lado
    for eje in (0, 2):
        lleno |= np.moveaxis(ndimage.binary_fill_holes(
            np.moveaxis(mask, eje, 0).reshape(mask.shape[eje], -1)).reshape(
                np.moveaxis(mask, eje, 0).shape), 0, eje)
    return lleno
CADENA = (["vertebrae_C%d" % i for i in range(1, 8)]
          + ["vertebrae_T%d" % i for i in range(1, 13)]
          + ["vertebrae_L%d" % i for i in range(1, 6)] + ["vertebrae_S1"])


def vertebras_finas(red, lab_grueso, nombres, destino, device="auto", margen_mm=40.0):
    """Centroides de cada vértebra POR SU NOMBRE, con el modelo fino, sobre un recorte.

    Tres caminos probados y descartados antes de este, todos el 20-sep-26:

    · el modelo de 3 mm pierde niveles enteros en este TC (faltaban T11, T12 y C5-C7, y T9 salía
      con 58 mm³), así que no sirve para decir en qué vértebra está un foco;
    · el fino sobre el cuerpo entero corre CINCO redes y ahogó la máquina (7,5 GB de swap);
    · `roi_subset`, que habría corrido solo la red de vértebras, RENUMERA las etiquetas: con
      `ml=False` hasta los ficheros por nombre traían otra estructura (la columna salía
      C7→S1→C6→C5→…). Está en [[polaris-totalsegmentator-roi-subset-renumera]].

    Lo que sí funciona: recortar la imagen a la columna y correr la tarea COMPLETA ahí. El
    recorte estrecha mucho en x-y (la columna es estrecha) pero **conserva todo el rango
    cráneo-caudal**, porque el modelo nombra cada vértebra por su sitio en la cadena y un
    recorte que la parta la renumera igual que trocear.
    """
    import nibabel as nib
    import numpy as np
    from scipy import ndimage
    from totalsegmentator.python_api import totalsegmentator
    # la versión va en el nombre: cada vez que cambia CÓMO se recorta, el fichero de antes deja
    # de valer, y reutilizarlo me hizo dar por probado un arreglo que ni se había ejecutado.
    out = os.path.join(destino, "columna_v2_total.nii.gz")
    if not os.path.exists(out):
        os.makedirs(destino, exist_ok=True)
        # canónica, porque la caja se calcula sobre `lab_grueso`, que YA está en canónica.
        # Recortar la imagen cruda con índices de la canónica mezcla dos espacios: la caja
        # cae donde no es y el modelo nombra las vértebras con lo que ve, que es otra cosa.
        src = nib.as_closest_canonical(nib.load(red))
        esp = np.sqrt((src.affine[:3, :3] ** 2).sum(axis=0))
        ids = {v: k for k, v in nombres.items()}
        col = np.isin(lab_grueso, [ids[n] for n in CADENA if n in ids])
        if not col.any():
            raise SystemExit("ABORTA: la pasada gruesa no encontró columna donde recortar")
        xs, ys, _ = np.nonzero(col)
        def _caja(v, n, e):
            return (max(0, int(v.min() - margen_mm / e)), min(n, int(v.max() + margen_mm / e) + 1))
        x0, x1 = _caja(xs, col.shape[0], esp[0])
        y0, y1 = _caja(ys, col.shape[1], esp[1])
        if src.shape != lab_grueso.shape:
            raise SystemExit("ABORTA: la imagen (%s) y la máscara gruesa (%s) no comparten "
                             "rejilla; la caja del recorte caería donde no es"
                             % (src.shape, lab_grueso.shape))
        datos = np.asarray(src.dataobj, dtype=np.int16)[x0:x1, y0:y1, :]   # z entero a propósito
        af = src.affine.copy()
        af[:3, 3] = (src.affine @ np.array([x0, y0, 0, 1.0]))[:3]
        ent = os.path.join(destino, "columna_in.nii.gz")
        nib.save(nifti_limpio(datos, af, np.int16), ent)
        print("  recorte de columna: %s de %s vóxeles (%.0f%% del volumen)"
              % (datos.shape, col.shape, 100.0 * datos.size / col.size), flush=True)
        del datos
        tmp = out + ".parcial.nii.gz"
        totalsegmentator(input=ent, output=tmp, task="total", ml=True, fast=False,
                         device=_dispositivo(device), quiet=True)
        os.replace(tmp, out)
        os.remove(ent)
    img = nib.as_closest_canonical(nib.load(out))
    lab = np.asarray(img.dataobj)
    ids = {v: k for k, v in nombres.items()}
    centros, vacias = {}, []
    for n in CADENA:
        i = ids.get(n)
        m = (lab == i) if i is not None else None
        if m is None or not m.any():
            vacias.append(n)
            continue
        centros[n] = [round(float(v), 1)
                      for v in (img.affine @ np.append(ndimage.center_of_mass(m), 1.0))[:3]]
    print("vértebras finas: %d con centroide%s"
          % (len(centros), "" if not vacias else " · sin encontrar: " + ", ".join(vacias)))
    if os.environ.get("BTP_DIAG"):
        for n in CADENA:
            i = ids.get(n)
            if n in centros and i is not None:
                m = lab == i
                tr, nc = ndimage.label(m)
                vols = sorted(np.bincount(tr.ravel())[1:], reverse=True)
                print("  DIAG %-15s z=%8.1f mm · %d trozo(s) · mayores %s"
                      % (n, centros[n][2], nc, vols[:3]), flush=True)
    _orden_columna(centros)
    return centros


def niveles_por_conteo(lab, nombres, afin, cráneo_z=None):
    """Nombra cada vértebra por su SITIO en la cadena, no por lo que diga el modelo.

    El hallazgo que desbloquea esto: TotalSegmentator segmenta bien los cuerpos vertebrales y
    los nombra MAL (medido: la columna salía C7→S1→C6→C5…, y con cada etiqueta partida en
    trozos, dos de ellos del mismo tamaño en vértebras distintas). Pero el orden de una columna
    no es opinable: de arriba abajo son C1…C7, T1…T12, L1…L5. Si cuento los cuerpos, los
    nombres salen del orden, sin depender de que el modelo acierte.

    Cómo se cuenta: se proyecta el hueso vertebral corte a corte; los discos dejan un mínimo en
    ese perfil porque ahí hay menos hueso. Cada tramo entre dos mínimos es un cuerpo.

    El freno: entre la base del cráneo y el sacro hay 24 cuerpos, ni uno más ni uno menos. Si
    no salen 24, esto NO devuelve nada — prefiero no dar niveles a dar niveles corridos, que es
    el error que pone un foco en la vértebra de al lado sin que se note.
    """
    import numpy as np
    from scipy import ndimage
    ids = {v: k for k, v in nombres.items()}
    vert = np.isin(lab, [ids[n] for n in CADENA if n in ids])
    if not vert.any():
        return {}
    perfil = vert.sum(axis=(0, 1)).astype(float)
    zz = np.nonzero(perfil)[0]
    z0, z1 = int(zz.min()), int(zz.max())
    suave = ndimage.gaussian_filter1d(perfil[z0:z1 + 1], 1.5)
    # mínimos locales del perfil = discos. Se exige que el mínimo sea REAL (baja al menos un
    # 8 % respecto a los máximos vecinos), o el ruido inventa discos donde no los hay.
    minimos = []
    for i in range(2, len(suave) - 2):
        if suave[i] <= suave[i - 1] and suave[i] <= suave[i + 1]:
            v_izq, v_der = suave[max(0, i - 6):i], suave[i + 1:i + 7]
            izq = float(v_izq.max()) if v_izq.size else 0.0
            der = float(v_der.max()) if v_der.size else 0.0
            if min(izq, der) > 0 and suave[i] < 0.92 * min(izq, der):
                if not minimos or i - minimos[-1] > 3:
                    minimos.append(i)
    cuerpos = len(minimos) + 1
    print("conteo de la columna: %d cuerpos entre los cortes %d y %d" % (cuerpos, z0, z1))
    if cuerpos != len(CADENA) - 1:      # 24: de C1 a L5; S1 ya es sacro
        print("AVISO: esperaba %d cuerpos y conté %d — no se nombran niveles"
              % (len(CADENA) - 1, cuerpos), file=sys.stderr)
        return {}
    bordes = [0] + minimos + [len(suave) - 1]
    centros = {}
    for k, n in enumerate(CADENA[:-1]):
        a, b = bordes[k], bordes[k + 1]
        tramo = vert[:, :, z0 + a:z0 + b + 1]
        if not tramo.any():
            continue
        com = np.asarray(ndimage.center_of_mass(tramo)) + np.array([0, 0, z0 + a])
        centros[n] = [round(float(v), 1) for v in (afin @ np.append(com, 1.0))[:3]]
    return centros


def niveles_por_reparto(lab, nombres, afin):
    """Los 24 niveles repartidos a lo largo de SU columna, entre el cráneo y el sacro.

    Último recurso, y se dice lo que es: una ESTIMACIÓN. Antes se intentó, y se midió por qué
    no vale, todo el 20-sep-26:
      · el modelo de 3 mm pierde niveles enteros (faltaban T11, T12, C5-C7);
      · el fino entero no cabe en 16 GB y troceado entra en thrashing;
      · `roi_subset` renumera y hasta los ficheros por nombre traen otra estructura;
      · sobre un recorte estrecho las vértebras salen partidas y una etiqueta aparece en dos;
      · contar cuerpos por los discos da 21 de 24 (C1 no tiene cuerpo, arriba se funden);
      · las costillas, que serían el ancla natural de cada dorsal, salen desordenadas y faltan 5.

    Lo que SÍ está bien segmentado es el cráneo, el sacro y la forma de la columna. Entre la
    base del cráneo y el techo del sacro hay 24 cuerpos, siempre. Repartirlos a lo largo del
    eje real de su columna no inventa anatomía: usa la suya, con sus curvas. Lo que no hace es
    medir cada nivel, y por eso sale etiquetado como estimado y nunca como dato de informe.

    El freno: cada nivel tiene que caer DENTRO del hueso vertebral. Si alguno cae en el aire,
    el eje está mal y no se devuelve nada.
    """
    import numpy as np
    from scipy import ndimage
    ids = {v: k for k, v in nombres.items()}
    vert = np.isin(lab, [ids[n] for n in CADENA if n in ids])
    craneo = ids.get("skull")
    sacro = ids.get("sacrum")
    if not vert.any() or craneo is None or sacro is None:
        return {}
    m_cr, m_sa = lab == craneo, lab == sacro
    if not m_cr.any() or not m_sa.any():
        print("AVISO: sin cráneo o sin sacro, no hay entre qué repartir", file=sys.stderr)
        return {}
    # Los extremos salen de la PROPIA columna, no del cráneo: el cráneo baja más que la
    # cervical alta (mandíbula, base), y repartir desde ahí dejaba seis niveles en el aire.
    # El corte más alto con hueso vertebral ES C1, y el más bajo, L5 sobre el sacro.
    zv = np.nonzero(vert.any(axis=(0, 1)))[0]
    lo, hi = int(zv.min()), int(zv.max())
    z_sa = int(np.nonzero(m_sa.any(axis=(0, 1)))[0].max())
    if not (lo <= z_sa <= hi or abs(z_sa - lo) < abs(z_sa - hi)):
        pass    # el sacro solo sirve de comprobación de sentido, no de extremo
    if hi - lo < len(CADENA):
        print("AVISO: cráneo y sacro salen pegados, el reparto no tiene sentido", file=sys.stderr)
        return {}
    # eje real de la columna: centro del hueso vertebral en cada corte, suavizado
    n = len(CADENA) - 1
    centros, fuera = {}, []
    for k, nom in enumerate(CADENA[:-1]):
        # el centro de cada nivel, no su borde: por eso k + 0.5
        z = int(round(lo + (hi - lo) * (k + 0.5) / n))
        corte = vert[:, :, max(0, z - 2):z + 3]
        if not corte.any():
            fuera.append(nom)
            continue
        com = np.asarray(ndimage.center_of_mass(corte))
        com[2] = z
        centros[nom] = [round(float(v), 1) for v in (afin @ np.append(com, 1.0))[:3]]
    if fuera:
        print("AVISO: %d nivel(es) caen fuera del hueso (%s) — no se reparten"
              % (len(fuera), ", ".join(fuera[:4])), file=sys.stderr)
        return {}
    print("niveles repartidos: %d, todos dentro del hueso vertebral (ESTIMADOS)" % len(centros))
    return centros


def _orden_columna(centros):
    """La columna tiene un orden físico innegociable: si no sale monótona, los nombres están mal.

    Es el único freno que caza una renumeración, porque el render se ve perfecto igualmente.
    """
    alturas = [(n, centros[n][2]) for n in CADENA if n in centros]
    z = [a for _, a in alturas]
    if len(z) > 2 and not (all(x > y for x, y in zip(z, z[1:]))
                           or all(x < y for x, y in zip(z, z[1:]))):
        fuera = [n for (n, a), (_, b) in zip(alturas, alturas[1:]) if (a - b) * (z[0] - z[1]) < 0]
        raise SystemExit("ABORTA: la columna no sale en orden (rompen: %s). Los nombres están "
                         "barajados y cada foco iría a la vértebra de al lado." % fuera[:6])
    print("orden de la columna: ok (%d niveles monótonos)" % len(z))


def _revisa_cadena(nombres, lab, afin):
    """La columna tiene que salir entera y en orden, o los focos van a la vértebra que no es.

    Dos formas de equivocarse que NO se ven mirando el render: que falte un nivel en medio (el
    foco se queda sin hueso) y que el orden se rompa (el modelo ha renumerado, y entonces un
    foco de D11 acaba en la vértebra de al lado). Medido el 20-sep-26: el modelo de 3 mm perdía
    T11, T12 y C5-C7 en este TC, y T9 salía con 58 mm³.
    """
    import numpy as np
    from scipy import ndimage
    ids = {v: k for k, v in nombres.items()}
    vol0 = float(np.prod(np.sqrt((np.asarray(afin)[:3, :3] ** 2).sum(axis=0))))
    alturas, ausentes, chicas = [], [], []
    for n in CADENA:
        i = ids.get(n)
        m = (lab == i) if i is not None else None
        if m is None or not m.any():
            ausentes.append(n)
            continue
        v = float(m.sum()) * vol0
        if v < VERTEBRA_MIN_MM3:
            chicas.append("%s (%d mm³)" % (n, v))
        alturas.append((n, float(ndimage.center_of_mass(m)[2])))
    # el orden: de C1 a S1 la altura tiene que bajar siempre (o subir siempre, según la rejilla)
    z = [a for _, a in alturas]
    monotona = all(x > y for x, y in zip(z, z[1:])) or all(x < y for x, y in zip(z, z[1:]))
    huecos = [n for n in ausentes if CADENA.index(n) > CADENA.index(alturas[0][0])
              and CADENA.index(n) < CADENA.index(alturas[-1][0])] if alturas else ausentes
    print("cadena vertebral: %d de %d niveles%s"
          % (len(alturas), len(CADENA), "" if not chicas else " · pequeñas: " + ", ".join(chicas)))
    if huecos or not monotona:
        raise SystemExit(
            "ABORTA: la columna no sale entera o está desordenada (huecos: %s · en orden: %s). "
            "Colocar un foco con esto lo pondría en la vértebra de al lado."
            % (huecos or "ninguno", monotona))


VERTEBRA_MIN_MM3 = 2000  # por debajo de esto el nivel está reconocido a medias, no sirve de ancla
MOTA_MIN_MM3 = 40       # por debajo de esto, un trozo suelto es un recorte del campo.
# Bajado de 120 a 40 el 20-sep: a 120 se llevaba los restos de T11-T12, que en este TC salen
# muy fragmentadas, y dejaba un VACÍO entre el tórax y la lumbar — justo donde está el foco
# más intenso de la serie. Un hueco ahí es peor que un par de motas.
# Empezó en 1500 y se comió costillas enteras: a 3 mm una costilla sale partida en trozos de
# menos de 1500 mm³, así que el umbral borraba hueso de verdad y dejaba el tórax asimétrico.


def _huesos_del_mapa():
    """Nombres de clase de TotalSegmentator que son hueso, en el orden en que se dibujan."""
    from totalsegmentator.map_to_binary import class_map
    nombres = list(class_map["total"].values())
    # CON húmeros. Los quité cuando el fondo era un dibujo y los brazos levantados del TC
    # desentonaban. Con el esqueleto real desentona lo contrario: un tronco sin brazos se lee
    # como que falta medio cuerpo, y {{TITULAR}} lo dijo —«le faltan trozos, debería ser un
    # esqueleto completo». Están enteros en su TC (81.000 y 83.000 mm³ medidos), levantados,
    # que es como se hace un TC de cuerpo entero.
    def es_hueso(n):
        return (n.startswith("vertebrae_") or "rib_" in n
                or n in ("sacrum", "sternum", "skull", "hip_left", "hip_right",
                         "femur_left", "femur_right", "humerus_left", "humerus_right",
                         "scapula_left", "scapula_right", "clavicula_left", "clavicula_right"))
    return [n for n in nombres if es_hueso(n)]


def esqueleto(raices, serie, device="auto", voxel=1.5, trozos=3, solapa=16, suaviza_mm=1.0,
              fino=False, vertebras=False, niveles=True, solo=None):
    """Máscara del esqueleto + centroide de cada hueso, desde el TC. Devuelve (mask, afín, centros)."""
    import nibabel as nib
    import numpy as np
    from scipy import ndimage
    from totalsegmentator.map_to_binary import class_map
    from totalsegmentator.python_api import totalsegmentator
    imagen, meta = convierte(raices, serie)
    if meta["modalidad"] != "CT":
        raise SystemExit("ABORTA: el esqueleto sale del TC; %s es %s." % (serie, meta["modalidad"]))
    # REMUESTREAR ANTES de segmentar, y no es un atajo: TotalSegmentator trabaja por dentro a
    # 1,5 mm de todos modos, pero DEVUELVE el mapa de etiquetas a la resolución original. Sobre
    # un TC de cuerpo entero a 0,78 mm eso son 245 millones de vóxeles, y el 20-sep-26 pedir eso
    # llegó a 20 GB en un mini de 16 y hubo que matarlo a mano. A 1,5 mm son ~8 veces menos, y
    # para una silueta de esqueleto sobra.
    exige_telemetria_apagada()
    red = os.path.join(_cache(serie), "esqueleto_%gmm.nii.gz" % voxel)
    if not os.path.exists(red):
        # Se decima por PASO ENTERO, no con `resample_to_output`: esa interpola por splines en
        # float64 y con un TC de cuerpo entero se fue a 11,2 GB (la guarda lo mató, 20-sep-26).
        # Un paso entero se lee en rodajas con `dataobj`, así que nibabel no carga el volumen
        # entero y el pico se queda en megas. Para una silueta de esqueleto, un vóxel de cada n
        # sobra; la geometría se corrige en el afín.
        src = nib.load(imagen)
        esp0 = np.sqrt((src.affine[:3, :3] ** 2).sum(axis=0))
        paso = [max(1, int(round(voxel / e))) for e in esp0]
        datos = np.asarray(src.dataobj[::paso[0], ::paso[1], ::paso[2]], dtype=np.int16)
        af = src.affine.copy()
        af[:3, :3] = src.affine[:3, :3] * np.array(paso)
        nib.save(nifti_limpio(datos, af, np.int16), red)
        del datos
    # el nº de trozos va en el nombre: el cosido de 3 trozos y el de una pieza no son el mismo
    # fichero, y reutilizar uno por el otro fue lo que me escondió un cosido en espejo.
    out = os.path.join(_cache(serie), "seg",
                       "esqueleto_%gmm_x%d%s_total.nii.gz" % (voxel, max(1, trozos),
                                                             "_fino" if fino else ""))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if not os.path.exists(out) and trozos > 1:
        # POR TROZOS, y no es una manía: el pico de nnU-Net es proporcional al volumen, y este TC
        # de cuerpo entero pide ~10,5 GB de una pieza (medido cuatro veces: 11,2 · 11,3 · 10,4 ·
        # 10,2 GB, las cuatro cortadas por la guarda). En un mini de 16 GB con el escritorio
        # abierto, eso es dejar la máquina inservible. Cada trozo pide su parte y cabe.
        # Se solapan `solapa` cortes y en la zona común manda el trozo que tiene el vóxel más
        # LEJOS de su propio borde, que es donde la red ve con más contexto.
        # canónica, porque la caja se calcula sobre `lab_grueso`, que YA está en canónica.
        # Recortar la imagen cruda con índices de la canónica mezcla dos espacios: la caja
        # cae donde no es y el modelo nombra las vértebras con lo que ve, que es otra cosa.
        src = nib.as_closest_canonical(nib.load(red))
        datos = np.asarray(src.dataobj, dtype=np.int16)
        nz = datos.shape[2]
        etq = np.zeros(datos.shape, np.uint8)
        dist = np.zeros(datos.shape, np.int32)
        cortes = np.linspace(0, nz, trozos + 1).round().astype(int)
        for k in range(trozos):
            z0 = max(0, cortes[k] - solapa)
            z1 = min(nz, cortes[k + 1] + solapa)
            # el nombre lleva TODO lo que cambia el contenido (nº de trozos, solape, modelo):
            # reutilizar una pieza de otra configuración hacía que las formas no casaran.
            pieza = os.path.join(os.path.dirname(out), "trozo%d_de%d_s%d_%gmm%s.nii.gz"
                                 % (k, trozos, solapa, voxel, "_fino" if fino else ""))
            # el afín del recorte se calcula SIEMPRE, no solo al crearlo: con la pieza ya en
            # caché hacía falta igual para devolverla a esta rejilla al coserla.
            af = src.affine.copy()
            af[:3, 3] = (src.affine @ np.array([0, 0, z0, 1.0]))[:3]
            if not os.path.exists(pieza):
                ent = pieza.replace(".nii.gz", "_in.nii.gz")
                nib.save(nifti_limpio(datos[:, :, z0:z1], af, np.int16), ent)
                tmp = pieza + ".parcial.nii.gz"
                print("  trozo %d/%d · cortes %d–%d" % (k + 1, trozos, z0, z1), flush=True)
                totalsegmentator(input=ent, output=tmp, task="total", ml=True, fast=not fino,
                                 device=_dispositivo(device), quiet=True)
                os.replace(tmp, pieza)
                os.remove(ent)
            # OJO: TotalSegmentator devuelve la pieza en SU orientación, no necesariamente en
            # la del recorte que le di. Coserla con el afín de la imagen original salía en
            # espejo, y en un esqueleto simétrico eso no se ve: lo cazó el ancla del hígado.
            # Así que la pieza se lleva a la rejilla del recorte antes de pegarla.
            pimg = nib.load(pieza)
            if os.environ.get("BTP_DIAG"):
                print("  DIAG trozo %d: recorte %s · pieza %s · forma %s vs %s"
                      % (k, nib.orientations.aff2axcodes(af),
                         nib.orientations.aff2axcodes(pimg.affine),
                         pimg.shape, datos[:, :, z0:z1].shape), flush=True)
            orig = nib.orientations.io_orientation(af)
            suya = nib.orientations.io_orientation(pimg.affine)
            lp = np.asarray(pimg.dataobj)
            if not np.allclose(orig, suya):
                lp = nib.orientations.apply_orientation(
                    lp, nib.orientations.ornt_transform(suya, orig))
            lp = lp.astype(np.uint8)
            if lp.shape != datos[:, :, z0:z1].shape:
                raise SystemExit("ABORTA: el trozo %d vuelve con forma %s y esperaba %s"
                                 % (k, lp.shape, datos[:, :, z0:z1].shape))
            # +1 para que el primer y el último corte del trozo también entren (d=0 no gana a dist=0)
            d = np.minimum(np.arange(z1 - z0), (z1 - z0 - 1) - np.arange(z1 - z0)) + 1
            mejor = d[None, None, :] > dist[:, :, z0:z1]
            sub = etq[:, :, z0:z1]
            sub[mejor] = lp[mejor]
            etq[:, :, z0:z1] = sub
            dist[:, :, z0:z1] = np.maximum(dist[:, :, z0:z1], d[None, None, :])
            del lp
        nib.save(nifti_limpio(etq, src.affine, np.uint8), out + ".parcial.nii.gz")
        os.replace(out + ".parcial.nii.gz", out)
        del datos, etq, dist
    if not os.path.exists(out):
        # a un temporal y luego rename: si la guarda mata a mitad, no queda un fichero a medias
        # que la vuelta siguiente dé por bueno
        tmp = out + ".parcial.nii.gz"
        # `fast=True` NO es «peor calidad aceptable»: es lo único que cabe, y está medido.
        # nnU-Net materializa el mapa de probabilidades de las 118 clases en float32, así que el
        # pico es nº de vóxeles × 118 × 4 bytes:
        #     a 1,56 mm → 30,6 M vóxeles = 14,5 GB   (medido: la guarda cortó a 11,3 GB)
        #     a 3,00 mm →  5,5 M vóxeles =  2,6 GB   ← con fast
        # Por eso los dos intentos anteriores, con remuestreos completamente distintos, pararon
        # en el MISMO sitio: ninguno tocaba la causa. Para una silueta de esqueleto, 3 mm sobra.
        # SIN `roi_subset`: renumera las etiquetas de 1 a n y deja de casar con `class_map`,
        # que es lo que reventó el primer intento que llegó al final (KeyError 25). Y no hacía
        # falta: lo que hace que quepa es el tamaño del volumen, no el número de clases pedidas.
        # `fast` NO es «un poco peor»: es OTRO modelo, el de 3 mm. Y remuestrea a 3 mm pase lo
        # que pase, así que darle la imagen a 1,5 mm con fast gasta memoria sin ganar nada.
        # A 3 mm este TC pierde niveles vertebrales enteros: faltaban T11, T12 y C5-C7, y T9
        # salía con 58 mm³ (medido). Para colocar un foco en SU vértebra eso no vale.
        totalsegmentator(input=red, output=tmp, task="total", ml=True, fast=not fino,
                         device=_dispositivo(device), quiet=True)
        os.replace(tmp, out)
    # A orientación canónica SIEMPRE, venga de una pieza o de tres cosidas. `proyecta_anterior`
    # da por hecho que el eje 0 es x del paciente y el 2 es z; eso solo es cierto en canónica.
    # Los dos caminos traían orientaciones distintas (TotalSegmentator reorienta su salida, el
    # cosido conservaba la del DICOM) y por eso uno salía derecho y el otro en espejo.
    _bruto = nib.load(out)
    img = nib.as_closest_canonical(_bruto)
    if os.environ.get("BTP_DIAG"):
        print("DIAG cosido: %s → canónica %s · forma %s"
              % (nib.orientations.aff2axcodes(_bruto.affine),
                 nib.orientations.aff2axcodes(img.affine), img.shape), flush=True)
    lab = np.asarray(img.dataobj).astype(np.int16)
    nombres = class_map["total"]                 # número → nombre
    ids = {v: k for k, v in nombres.items()}     # nombre → número
    if os.environ.get("BTP_INVENTARIO"):
        vol0 = float(np.prod(np.sqrt((img.affine[:3, :3] ** 2).sum(axis=0))))
        hay = {nombres.get(int(i)): int(c) * vol0
               for i, c in zip(*np.unique(lab, return_counts=True)) if i}
        for grupo, pats in (("columna", ("vertebrae_",)), ("costillas", ("rib_",)),
                            ("cintura", ("scapula", "clavicula", "sternum")),
                            ("brazos", ("humerus", "radius", "ulna", "carpal", "hand")),
                            ("pelvis", ("hip", "sacrum")),
                            ("piernas", ("femur", "patella", "tibia", "fibula", "foot")),
                            ("cabeza", ("skull",))):
            hay_g = {k: v for k, v in hay.items() if k and any(p in k for p in pats)}
            print("INV %-10s %2d pieza(s) · %s" % (grupo, len(hay_g),
                  ", ".join("%s %.0f" % (k.replace("vertebrae_", "").replace("rib_", ""), v)
                            for k, v in sorted(hay_g.items())[:14]) or "NADA"), flush=True)
    if os.environ.get("BTP_DIAG"):
        hay_lab, cuenta = np.unique(lab, return_counts=True)
        vol0 = float(np.prod(np.sqrt((img.affine[:3, :3] ** 2).sum(axis=0))))
        faltan = [n for i, n in sorted(nombres.items())
                  if (n.startswith("vertebrae_") or n in ("sacrum", "sternum"))
                  and i not in set(hay_lab.tolist())]
        print("DIAG etiquetas presentes: %d · vértebras/sacro/esternón AUSENTES: %s"
              % (len(hay_lab) - 1, faltan or "ninguna"), flush=True)
        chicas = [(nombres.get(int(i)), int(c * vol0)) for i, c in zip(hay_lab, cuenta)
                  if i and c * vol0 < 3000]
        print("DIAG etiquetas con menos de 3000 mm³:", chicas[:12], flush=True)
    if vertebras:
        # los niveles vienen de su propia pasada, que es la única que los nombra bien
        finas = vertebras_finas(red, lab, nombres,
                                os.path.join(_cache(serie), "seg", "columna_%gmm" % voxel),
                                device=device)
    elif niveles:
        # los nombres salen del ORDEN de la cadena, no de lo que diga el modelo
        finas = niveles_por_conteo(lab, nombres, img.affine)
        if not finas:
            finas = niveles_por_reparto(lab, nombres, img.affine)
        if finas:
            _orden_columna(finas)
    else:
        # Sin niveles vertebrales publicados. No es un apaño para saltarse el freno: es que si
        # no sale NINGÚN nombre de vértebra, ningún foco puede acabar en la vértebra de al lado.
        # La silueta sigue siendo la real y los huesos que no son columna (escápula, ilíaco,
        # sacro, fémur, costilla) siguen teniendo su centroide.
        finas = {}
    huesos = _huesos_del_mapa()
    if solo:
        # Solo los huesos QUE TIENEN LESIÓN. Idea de {{TITULAR}}: en vez de pelearse por completar
        # un esqueleto que el TC nunca escaneó entero, enseñar las piezas que importan. Cada
        # una es suya, está en su sitio real, y no hay nada que reconstruir ni que disimular.
        huesos = [n for n in huesos if n in solo]
        print("solo lesionados: %d hueso(s) de %s" % (len(huesos), ", ".join(sorted(solo))))
    quiero = [ids[n] for n in huesos if n in ids]
    mask = np.isin(lab, quiero)
    if not mask.any():
        raise SystemExit("ABORTA: la segmentación no trae hueso; ¿es un TC de cuerpo entero?")
    # fuera las motas sueltas: trozos que el borde del campo corta y quedan flotando en el aire.
    # El umbral va en mm³ para que no dependa de a cuántos milímetros se haya remuestreado.
    esp_mm = np.sqrt((img.affine[:3, :3] ** 2).sum(axis=0))
    esp_v = float(np.prod(esp_mm))
    trozo, n = ndimage.label(mask)
    if n > 1:
        vol = np.bincount(trozo.ravel())[1:] * esp_v
        mask = np.isin(trozo, np.nonzero(vol >= MOTA_MIN_MM3)[0] + 1)
        fuera = int((vol < MOTA_MIN_MM3).sum())
        if fuera:
            print("limpieza: %d trozo(s) sueltos por debajo de %d mm³" % (fuera, MOTA_MIN_MM3))
    del trozo
    # Ancla de lateralidad. Un esqueleto es casi simétrico, así que si la vista sale en espejo
    # NO se nota mirándola: los focos de la derecha aparecerían en la izquierda y nadie lo vería
    # hasta que fuese tarde. El hígado y el bazo no son simétricos y no admiten discusión, así
    # que se sacan sus centroides SOLO para comprobar el sentido, y no se dibujan.
    ancla = {}
    for organo in ("liver", "spleen"):
        i = ids.get(organo)
        if i is not None and (lab == i).any():
            com = ndimage.center_of_mass((lab == i).astype(np.uint8))
            ancla[organo] = [float(v) for v in (img.affine @ np.append(np.asarray(com), 1.0))[:3]]
    mask = _une_fragmentos(mask, lab, nombres, esp_mm)
    mask = _simetriza_costillas(mask, lab, nombres, esp_mm)
    mask = _puentea_columna(mask, lab, nombres)
    # el centroide se calcula sobre lo que QUEDA tras la limpieza, no sobre la etiqueta original:
    # si a una costilla le quitamos su único trozo, `center_of_mass` devolvía NaN en silencio.
    lab = np.where(mask, lab, 0)
    centros = {}
    presentes = [i for i in quiero if (lab == i).any()]
    # centroide de CADA hueso por su etiqueta: es lo que sustituye a las posiciones a ojo
    coms = ndimage.center_of_mass(mask, lab, presentes)
    for i, com in zip(presentes, coms):
        nombre = nombres.get(int(i))
        if not nombre:          # etiqueta que no está en class_map: se dice y se sigue
            print("AVISO: etiqueta %s sin nombre en class_map; la salto" % i, file=sys.stderr)
            continue
        p = (img.affine @ np.append(np.asarray(com), 1.0))[:3]
        centros[nombre] = [round(float(v), 1) for v in p]
    centros.update(finas)      # el nivel vertebral fino manda sobre el del mapa grueso
    if not niveles and not vertebras:
        centros = {n: c for n, c in centros.items() if n not in set(CADENA)}
    # El borde escalonado no se arregla segmentando más fino: a 1,5 mm el volumen es 8 veces
    # mayor y la máquina empujó 6,3 GB al swap (medido). Y no haría falta aunque cupiera: el
    # detalle anatómico que aporta es nulo para una SILUETA. Lo que se ve feo es el escalón del
    # vóxel, y eso se quita remuestreando la máscara YA hecha, que cuesta unos cientos de MB.
    if suaviza_mm and suaviza_mm < min(esp_mm):
        factor = [e / suaviza_mm for e in esp_mm]
        mask = ndimage.zoom(mask.astype(np.float32), factor, order=1) > 0.5
        # y un cierre: a 3 mm las costillas salen cortadas a trozos y una parrilla costal
        # discontinua se lee como error, no como anatomía. El cierre las vuelve a unir sin
        # engordar el hueso más que el radio de la bola.
        mask = ndimage.binary_closing(mask, _bola_estructura([suaviza_mm] * 3, CIERRE_MM))
        mask = _reconstruye(mask, [suaviza_mm] * 3)
        afin_s = img.affine.copy()
        afin_s[:3, :3] = img.affine[:3, :3] / np.array(factor)
    else:
        afin_s = img.affine
    return mask, afin_s, centros, ancla


def proyecta_anterior(mask, afin, ancho=880):
    """Vista de frente del esqueleto, sombreada por profundidad.

    No es un render 3D: para cada punto de la pantalla se busca el vóxel de hueso MÁS ANTERIOR y
    se ilumina según la inclinación de esa superficie. Sale una figura con volumen y cuesta un
    segundo, que para un SELECTOR es lo que hace falta — girarlo no aporta nada aquí.

    Devuelve (rgba, extension) con la extensión en mm del mundo, que es lo que permite colocar
    después cada foco por su centroide en vez de a ojo. Vista anterior: la derecha del cuerpo
    queda a la IZQUIERDA de quien mira, como dice el rótulo de la página.
    """
    import numpy as np
    from scipy import ndimage
    esp = np.sqrt((np.asarray(afin)[:3, :3] ** 2).sum(axis=0))
    # índice del vóxel más anterior (mayor y) con hueso, por cada (x, z)
    hay = mask.any(axis=1)
    idx = mask.shape[1] - 1 - np.argmax(mask[:, ::-1, :], axis=1)
    prof = np.where(hay, idx.astype(np.float32) * esp[1], np.nan)
    # sombreado: normal aproximada por el gradiente del mapa de profundidad.
    # El suavizado NO es cosmético: la profundidad viene cuantizada al vóxel, y sin suavizar
    # el sombreado dibuja las terrazas del remuestreo como si fueran curvas de nivel.
    p = np.where(hay, prof, np.nanmax(prof[hay]) if hay.any() else 0.0)
    p = ndimage.gaussian_filter(p, 2.2)
    gx, gz = np.gradient(p)
    # El 2.2 de la componente Z es lo que aplana o levanta la figura: cuanto más bajo, más
    # inclinada se ve la normal y más marcado sale el volumen. A 1.35 el hueso deja de parecer
    # una silueta recortada y se le ven los relieves (crestas ilíacas, apófisis, arcos).
    n = np.stack([-gx, -gz, np.ones_like(p) * 1.35], axis=-1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-9
    luz = np.array([-0.45, 0.45, 0.78])
    luz /= np.linalg.norm(luz)
    lam = np.clip((n * luz).sum(axis=-1), 0, 1)
    # la profundidad también atenúa: lo de detrás se apaga
    if hay.any():
        z0, z1 = np.nanmin(prof[hay]), np.nanmax(prof[hay])
        cerca = np.clip((p - z0) / max(1e-6, z1 - z0), 0, 1)
    else:
        cerca = np.zeros_like(p)
    # Relieve, en tres capas que se suman:
    #  · difusa, con el exponente más bajo para que el medio tono no se queme;
    #  · un brillo especular estrecho, que es lo que hace que un hueso parezca hueso;
    #  · oclusión de andar por casa: donde el mapa de profundidad es CÓNCAVO se oscurece, y eso
    #    mete las sombras de los huecos (entre costillas, el agujero obturador, las órbitas).
    espec = np.clip(lam, 0, 1) ** 22
    hueco = ndimage.gaussian_filter(p, 6.0) - p
    ocl = np.clip(hueco / (np.abs(hueco).max() + 1e-6), 0, 1)
    val = np.where(hay,
                   0.20 + 0.62 * lam ** 0.9 + 0.26 * espec
                   + 0.16 * np.nan_to_num(cerca) - 0.16 * ocl,
                   0.0)
    val = np.clip(val, 0.0, 1.0)
    # A pantalla. Las tres operaciones van juntas en una función y se aplican TAMBIÉN a los
    # índices, porque deducir a mano dónde acaba cada eje después de un rot90 y dos flips es
    # justo donde me equivoqué: la vista salía en espejo y, en un esqueleto simétrico, mirándola
    # no se nota. Así el mapa de mm a píxel sale de las mismas operaciones que la imagen.
    def a_pantalla(m):
        # La máscara llega en canónica RAS: el eje 0 crece hacia la DERECHA del cuerpo. La vista
        # anterior la pone a la izquierda de quien mira, que es lo que dice el rótulo de la
        # página, así que ese eje se invierte. El ancla del hígado lo comprueba después.
        return np.flipud(np.fliplr(np.rot90(m, k=1)))
    val = a_pantalla(val)
    alfa = a_pantalla(hay.astype(np.float32))
    rgba = np.zeros(val.shape + (4,), np.uint8)
    # hueso gris-azulado pálido, como el resto del negativoscopio
    for k, base in enumerate((0xD7, 0xDB, 0xE2)):
        rgba[..., k] = np.clip(val * base, 0, 255).astype(np.uint8)
    rgba[..., 3] = (np.clip(alfa, 0, 1) * 255).astype(np.uint8)
    # extensión en mm: qué punto del mundo cae en el borde de la imagen, leído de los índices
    nx, nz = mask.shape[0], mask.shape[2]
    ii = a_pantalla(np.repeat(np.arange(nx)[:, None], nz, axis=1))
    kk = a_pantalla(np.repeat(np.arange(nz)[None, :], nx, axis=0))

    def _mm(i, k):
        return (np.asarray(afin) @ np.append([i, 0, k], 1.0))[:3]

    ext = {"x": [float(_mm(ii[0, 0], 0)[0]), float(_mm(ii[0, -1], 0)[0])],
           "z": [float(_mm(0, kk[0, 0])[2]), float(_mm(0, kk[-1, 0])[2])]}
    return rgba, ext


def _uv(mm, extremos):
    """De milímetros del mundo a fracción 0-1 de la imagen, en el eje que diga `extremos`."""
    return (mm - extremos[0]) / (extremos[1] - extremos[0])


def _cmd_esqueleto(a):
    import numpy as np
    from PIL import Image
    globals()["ORGANO"] = "esqueleto"
    mask, afin, centros, ancla = esqueleto(a.raices, a.serie, device=a.device, voxel=a.voxel,
                                           trozos=a.trozos, solapa=a.solapa,
                                           suaviza_mm=a.suaviza_mm, fino=a.fino,
                                           vertebras=a.vertebras, niveles=not a.sin_niveles,
                                           solo=set(a.solo.split(",")) if a.solo else None)
    rgba, ext = proyecta_anterior(mask, afin)
    dst = _dir("render")
    exige_zona_clinica(dst)
    os.makedirs(dst, exist_ok=True)
    im = Image.fromarray(rgba, "RGBA")
    if a.ancho and im.width != a.ancho:
        im = im.resize((a.ancho, max(1, round(im.height * a.ancho / im.width))), Image.LANCZOS)
    ruta = os.path.join(dst, "esqueleto-anterior.png")
    im.save(ruta, "PNG", optimize=True)
    # coordenadas de cada hueso en la MISMA imagen, normalizadas: es lo que la web necesita
    uv = {}
    for nombre, (x, y, z) in centros.items():
        uv[nombre] = {"u": round(_uv(x, ext["x"]), 5), "v": round(_uv(z, ext["z"]), 5)}
    # el hígado tiene que caer a la IZQUIERDA de quien mira, que es lo que dice el rótulo de
    # la página. Si no, la proyección está en espejo y todos los focos saldrían del lado que no es.
    if {"liver", "spleen"} <= set(ancla):
        def _u(p):
            return _uv(p[0], ext["x"])
        if not _u(ancla["liver"]) < _u(ancla["spleen"]):
            raise SystemExit("ABORTA: la vista sale en espejo (el hígado cae en el lado del bazo). "
                             "Un esqueleto es simétrico y esto NO se ve mirando la imagen.")
        print("lateralidad: ok (hígado u=%.3f < bazo u=%.3f)"
              % (_u(ancla["liver"]), _u(ancla["spleen"])))
    else:
        print("AVISO: sin hígado ni bazo en la segmentación, la lateralidad va SIN COMPROBAR",
              file=sys.stderr)
    # y el otro eje: la cabeza arriba. Con el cuerpo del revés los focos cervicales saldrían en
    # la pelvis, y tampoco se ve mirando una silueta.
    arriba = next((n for n in ("skull", "vertebrae_C1", "vertebrae_C3") if n in uv), None)
    abajo = next((n for n in ("femur_left", "femur_right", "hip_left") if n in uv), None)
    if arriba and abajo:
        if not uv[arriba]["v"] < uv[abajo]["v"]:
            raise SystemExit("ABORTA: la vista sale del revés (%s por debajo de %s)."
                             % (arriba, abajo))
        print("vertical: ok (%s v=%.3f por encima de %s v=%.3f)"
              % (arriba, uv[arriba]["v"], abajo, uv[abajo]["v"]))
    else:
        print("AVISO: sin cráneo ni fémur, el sentido vertical va SIN COMPROBAR", file=sys.stderr)
    json.dump({"imagen": os.path.basename(ruta), "tamano": [im.width, im.height],
               "serie": a.serie, "huesos": uv},
              open(os.path.join(dst, "esqueleto.json"), "w"), ensure_ascii=False, indent=1)
    print("esqueleto: %d×%d px · %d huesos con centroide → %s"
          % (im.width, im.height, len(uv), os.path.relpath(dst, SALIDA_RAIZ)))
    for n in sorted(uv)[:6]:
        print("  %-18s u=%.3f v=%.3f" % (n, uv[n]["u"], uv[n]["v"]))
    return 0


# ─── visor local (offline) ───────────────────────────────────────────────────────────────

WEB = os.path.join(_TOOLS, "visor3d_web")


def visor(comparacion=None):
    """Monta <SALIDA>/higado/visor/: index.html + bundle.js + una carpeta por fecha."""
    import shutil
    import subprocess
    base = _dir()
    dst = os.path.join(base, "visor")
    exige_zona_clinica(dst)
    os.makedirs(dst, exist_ok=True)
    esbuild = os.path.join(WEB, "node_modules", ".bin", "esbuild")
    if not os.path.exists(esbuild):
        raise SystemExit("Falta el build del visor: cd tools/visor3d_web && npm install")
    subprocess.run([esbuild, os.path.join(WEB, "main.js"), "--bundle", "--format=esm",
                    "--minify", "--log-level=warning",
                    "--outfile=" + os.path.join(dst, "bundle.js")], check=True, cwd=WEB)
    shutil.copy(os.path.join(WEB, "index.html"), os.path.join(dst, "index.html"))
    subprocess.run([esbuild, os.path.join(WEB, "video.js"), "--bundle", "--format=esm",
                    "--minify", "--log-level=warning",
                    "--outfile=" + os.path.join(dst, "video.bundle.js")], check=True, cwd=WEB)
    shutil.copy(os.path.join(WEB, "video.html"), os.path.join(dst, "video.html"))
    subprocess.run([esbuild, os.path.join(WEB, "render.js"), "--bundle", "--format=esm",
                    "--minify", "--log-level=warning",
                    "--outfile=" + os.path.join(dst, "render.bundle.js")], check=True, cwd=WEB)
    shutil.copy(os.path.join(WEB, "render.html"), os.path.join(dst, "render.html"))
    # Fuentes de marca (Fraunces, JetBrains Mono) desde el build de la web: no se versionan
    # binarios aquí. Si no están, el render cae a fuentes del sistema.
    fuentes = os.environ.get("BTP_FUENTES_MARCA") or os.path.expanduser(
        "~/projects/titular-{{APELLIDO}}-case/.output/public/_og-static-fonts")
    if os.path.isdir(fuentes):
        os.makedirs(os.path.join(dst, "fonts"), exist_ok=True)
        for f in os.listdir(fuentes):
            if f.endswith(".woff") and (f.startswith("Fraunces") or f.startswith("JetBrains")):
                shutil.copy(os.path.join(fuentes, f), os.path.join(dst, "fonts", f))
    fechas = []
    origen = os.path.join(base, "assets")
    for f in sorted(os.listdir(origen)):
        if os.path.exists(os.path.join(origen, f, "estudio.json")):
            shutil.copytree(os.path.join(origen, f), os.path.join(dst, f), dirs_exist_ok=True)
            if f.isdigit():
                fechas.append(f)
    # PET por fecha del TC diagnóstico: higado/pet/<pet>_<ctdiag>.json → <fecha>/pet.json
    dpet = os.path.join(base, "pet")
    if os.path.isdir(dpet):
        for f in sorted(os.listdir(dpet)):
            if not f.endswith(".json") or "_" not in f:
                continue
            ctdiag = f[:-5].split("_", 1)[1]
            meta_p = os.path.join(SALIDA_RAIZ, "cache", ctdiag, "meta.json")
            if not os.path.exists(meta_p):
                continue
            fecha = json.load(open(meta_p))["fecha"]
            if fecha in fechas:
                shutil.copy(os.path.join(dpet, f), os.path.join(dst, fecha, "pet.json"))
    json.dump({"fechas": fechas}, open(os.path.join(dst, "indice.json"), "w"))
    if comparacion:
        shutil.copy(comparacion, os.path.join(dst, "comparacion.json"))
    ref = os.path.join(base, "referencia.json")
    if os.path.exists(ref):
        shutil.copy(ref, os.path.join(dst, "referencia.json"))
    return dst, fechas


def _cmd_visor(a):
    comp = None
    if a.comparacion:
        comp = _dir("comparacion", a.comparacion + ".json")
    dst, fechas = visor(comp)
    print("visor montado: %d fechas (%s) en %s" % (len(fechas), ", ".join(fechas),
                                                   os.path.relpath(dst, SALIDA_RAIZ)))
    return 0


def _cmd_referencia(a):
    """Medidas del radiólogo por lesión (JSON por stdin) → <SALIDA>/higado/referencia.json."""
    datos = json.load(sys.stdin)
    if not isinstance(datos, dict) or not datos.get("_fuente"):
        raise SystemExit("ABORTA: la referencia necesita '_fuente' (de dónde salen las cifras).")
    ruta = _dir("referencia.json")
    exige_zona_clinica(ruta)
    json.dump(datos, open(ruta, "w"), ensure_ascii=False, indent=1)
    print("referencia guardada:", ", ".join(k for k in datos if not k.startswith("_")))
    return 0


# ─── marcas de un radiólogo sobre el mismo TC ────────────────────────────────────────────
#
# Revisión informal (22-sep-26, sin informe firmado): 55 medidas, una por lesión según él. Las que
# caen sobre una lesión automática se quedan con su malla y ganan la procedencia «revisada por el
# radiólogo»; el resto se pinta como ESFERA del diámetro que él midió, centrada en su marca. La
# esfera es una marca puntual, no una segmentación: no tiene forma ni volumen real, y el visor lo
# dice en la leyenda. Lo que no tiene posición fiable va listado fuera del 3D, nunca colocado.

ORIGEN_POLARIS = "Detectada por Polaris y revisada por el radiólogo"
ORIGEN_RADIOLOGO = "Detectada por el radiólogo"
FRASE_REVISION = ("Un radiólogo, en lectura informal y sin informe firmado, marcó 55 medidas y "
                  "las considera 55 lesiones distintas, muy sugestivas de metástasis; las 20 de "
                  "Polaris están entre ellas; el informe oficial dice M1 múltiples.")


def marcas_radiologo(revision, serie, fecha, empareja=None, fuente_emparejado=None):
    """lesiones_55.json (x, y en píxeles del corte, z = índice de corte, del volumen de la caché)
    → marcas.json + una esfera PLY por marca sin lesión automática.

    Reglas que no dependen de mirar a ojo (y por eso se pueden auditar):
      · `empareja` {marca: lesión} fuerza un emparejado verificado por otra vía (a ojo, sobre
        las superposiciones); queda escrito con su fuente en el JSON.
      · dos marcas emparejadas con la MISMA lesión automática: se la queda la de diámetro más
        cercano al automático; la otra, si comparte coordenadas exactas, se queda sin posición
        (el extractor le copió la del centroide: esa no es su sitio).
      · dos marcas sin lesión automática con coordenadas idénticas: no se sabe cuál es cuál,
        las dos van sin posición.
    """
    import numpy as np
    rev = json.load(open(revision))
    aff = _carga(os.path.join(_cache(serie), "imagen.nii.gz"))[1]
    destino = _dir("assets", fecha)
    exige_zona_clinica(destino)
    est = json.load(open(os.path.join(destino, "estudio.json")))
    auto = {L["id"]: L for L in est["lesiones"]}
    marcas = {m["id"]: dict(m) for m in rev["lesiones"]}
    for mid, lid in (empareja or {}).items():
        marcas[mid]["lesion_polaris"] = lid
        marcas[mid]["_forzado"] = True
    motivo = {}
    # una lesión automática, una marca
    por_lesion = {}
    for m in marcas.values():
        if m["lesion_polaris"]:
            por_lesion.setdefault(m["lesion_polaris"], []).append(m)
    for lid, ms in por_lesion.items():
        if len(ms) < 2:
            continue
        ms.sort(key=lambda m: (not m.get("_forzado"), abs(m["mm"] - auto[lid]["diametro_mm"])))
        for m in ms[1:]:
            m["lesion_polaris"] = None
            if (m["x"], m["y"], m["z"]) == (ms[0]["x"], ms[0]["y"], ms[0]["z"]):
                motivo[m["id"]] = ("el extractor le dio la misma posición que la marca %d "
                                   "(la de la lesión automática L%d)" % (ms[0]["id"], lid))
    # coordenadas idénticas entre marcas del radiólogo
    sueltas = [m for m in marcas.values() if not m["lesion_polaris"] and m["x"] is not None]
    for m in sueltas:
        gemelas = [o["id"] for o in sueltas if o is not m
                   and (o["x"], o["y"], o["z"]) == (m["x"], m["y"], m["z"])]
        if gemelas:
            motivo[m["id"]] = ("el extractor le dio la misma posición que la marca %s; "
                               "no se sabe cuál de las dos está ahí" % ", ".join(map(str, gemelas)))
    for m in marcas.values():
        if m["x"] is None:
            motivo[m["id"]] = "la marca no se pudo localizar en el volumen"
    automaticas, en_3d, sin_pos = {}, [], []
    u, f = _icosfera(2)
    for mid in sorted(marcas):
        m = marcas[mid]
        if m["lesion_polaris"]:
            automaticas[str(m["lesion_polaris"])] = {"origen": ORIGEN_POLARIS, "marca": mid,
                                                    "radiologo_mm": m["mm"], "corte": m["corte"]}
            continue
        if mid in motivo:
            sin_pos.append({"id": mid, "corte": m["corte"], "mm": m["mm"],
                            "origen": ORIGEN_RADIOLOGO, "motivo": motivo[mid]})
            continue
        c = (aff @ np.array([m["x"], m["y"], m["z"], 1.0]))[:3]
        malla = "marca%02d.ply" % mid
        ply_binario(os.path.join(destino, malla), c + u * (m["mm"] / 2.0), f)
        en_3d.append({"id": mid, "corte": m["corte"], "mm": m["mm"], "segmento": m["segmento"],
                      "centro_mm": [round(float(x), 1) for x in c], "origen": ORIGEN_RADIOLOGO,
                      "malla": malla})
    sin_marca = sorted(set(auto) - {int(k) for k in automaticas})
    salida = {
        "_fuente": "revisión informal de un radiólogo (22-sep-26, sin informe firmado): %s · %s"
                   % (os.path.basename(revision), rev.get("metodo", "")),
        "_emparejado_forzado": {"marcas": {str(k): v for k, v in (empareja or {}).items()},
                                "fuente": fuente_emparejado},
        "frase": FRASE_REVISION,
        "tc": rev.get("tc"),
        "automaticas": automaticas,
        "automaticas_sin_marca": sin_marca,
        "marcas": en_3d,
        "sin_posicion": sin_pos,
        "recuento": {"total": len(marcas), "polaris_revisadas": len(automaticas),
                     "solo_radiologo": len(en_3d) + len(sin_pos), "en_3d": len(en_3d),
                     "sin_posicion": len(sin_pos)},
    }
    json.dump(salida, open(os.path.join(destino, "marcas.json"), "w"), ensure_ascii=False,
              indent=1)
    return salida


def _cmd_marcas(a):
    empareja = {}
    for par in a.empareja or []:
        mid, lid = par.split(":")
        empareja[int(mid)] = int(lid)
    if empareja and not a.fuente:
        raise SystemExit("ABORTA: un emparejado forzado necesita --fuente (quién lo verificó).")
    r = marcas_radiologo(a.revision, a.serie, a.fecha, empareja, a.fuente)
    n = r["recuento"]
    print("marcas: %d · Polaris+radiólogo %d · solo radiólogo %d (en 3D %d, sin posición %d)"
          % (n["total"], n["polaris_revisadas"], n["solo_radiologo"], n["en_3d"],
             n["sin_posicion"]))
    if r["automaticas_sin_marca"]:
        print("lesiones automáticas SIN marca:", r["automaticas_sin_marca"])
    for s in r["sin_posicion"]:
        print("  sin posición: marca %d (img %d, %.2f mm): %s" % (s["id"], s["corte"], s["mm"],
                                                              s["motivo"]))
    return 0


def _cmd_video(a):
    """Vídeo vertical del 3D (Reels/TikTok): graba video.html con Chrome sin interfaz y monta
    el MP4 con ffmpeg. Necesita `sirve` corriendo. Salida en zona clínica: es imagen suya y no
    sale de ahí hasta que ELLA decida publicarla."""
    import shutil
    import subprocess
    base = _dir("video")
    exige_zona_clinica(base)
    fotos = os.path.join(base, "_fotogramas_%s" % a.fecha)
    shutil.rmtree(fotos, ignore_errors=True)
    n = int(a.segundos * a.fps)
    pagina = "render.html" if a.estilo == "realista" else "video.html"
    url = "http://127.0.0.1:%d/%s?fecha=%s&zoom=%s&ss=%d&texto=%d" % (
        a.puerto, pagina, a.fecha, a.zoom, a.ss, 1 if a.texto else 0)
    subprocess.run(["node", os.path.join(WEB, "grabar.mjs"), url, fotos, str(n),
                    str(a.ancho), str(a.alto)], check=True)
    salida = os.path.join(base, "higado-3d-%s-%s%s-%dx%d.mp4" % (
        a.fecha, a.estilo, "-texto" if a.texto else "", a.ancho, a.alto))
    # Supermuestreo: los fotogramas vienen a ss× y se reducen con lanczos (bordes limpios)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps),
                    "-i", os.path.join(fotos, "f%04d.png"),
                    "-vf", "scale=%d:%d:flags=lanczos" % (a.ancho, a.alto),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-map_metadata", "-1",
                    salida], check=True)
    shutil.rmtree(fotos, ignore_errors=True)
    # Copia dentro del visor para verlo en http://127.0.0.1:<puerto>/video/ (misma zona clínica)
    vdir = _dir("visor", "video")
    os.makedirs(vdir, exist_ok=True)
    shutil.copy(salida, os.path.join(vdir, os.path.basename(salida)))
    # Hoja de contactos (6 momentos de la vuelta) para revisar sin reproducir: si no gira,
    # salen 6 fotogramas iguales (así se cazó el vídeo quieto del 19-sep).
    hoja = os.path.join(vdir, os.path.basename(salida)[:-4] + "-hoja.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", salida, "-vf",
                    "fps=%f,scale=270:-1,tile=6x1" % (6.0 / a.segundos), "-frames:v", "1", hoja],
                   check=True)
    print("vídeo:", os.path.relpath(salida, SALIDA_RAIZ), "· %d fotogramas a %d fps" % (n, a.fps))
    print("hoja de contactos: http://127.0.0.1:%d/video/%s" % (a.puerto, os.path.basename(hoja)))
    print("verlo: http://127.0.0.1:%d/video/%s" % (a.puerto, os.path.basename(salida)))
    return 0


def _cmd_hoja(a):
    import subprocess
    vdir = _dir("visor", "video")
    exige_zona_clinica(vdir)
    mp4 = os.path.join(vdir, a.nombre)
    if a.t is not None:            # un fotograma a tamaño real, para mirar detalle
        hoja = mp4[:-4] + "-t%.1f.png" % a.t
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(a.t), "-i", mp4,
                        "-frames:v", "1", hoja], check=True)
    else:
        hoja = mp4[:-4] + "-hoja.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-vf",
                        "fps=0.5,scale=270:-1,tile=6x1", "-frames:v", "1", hoja], check=True)
    print("hoja: http://127.0.0.1:%d/video/%s" % (a.puerto, os.path.basename(hoja)))
    return 0


CARPETA_MARCA = {"higado": "Videos-Higado-3D", "mama": "Videos-Mama-3D",
                 "esqueleto": "Esqueleto-3D"}


def _marca(*partes):
    """Borradores de marca del órgano activo. NO es zona clínica: aquí solo salen imágenes
    derivadas (renders, MIPs), nunca DICOM ni cabeceras."""
    return os.path.join(REPO, "00_FUENTE-DE-VERDAD", "07 · Marca",
                        CARPETA_MARCA.get(ORGANO, "Videos-Higado-3D"), *partes)


def _cmd_exporta(a):
    """Saca un vídeo YA RENDERIZADO de la zona clínica a borradores de marca, para que {{TITULAR}} lo
    revise y decida si publica. Solo sale un render (mallas, sin TC ni cabeceras) y solo si el
    MP4 no lleva metadatos: ni título, ni fechas de estudio, ni nada que identifique."""
    import json as _j
    import shutil
    import subprocess
    # el vídeo sale del visor; el render del esqueleto, de su carpeta. Las dos son zona clínica
    # y las dos salen por esta misma ventanilla, que es la única que revisa lo que se lleva.
    candidatos = [os.path.join(_dir("visor", "video"), os.path.basename(a.nombre)),
                  os.path.join(_dir("render"), os.path.basename(a.nombre))]
    mp4 = next((c for c in candidatos if os.path.exists(c)), None)
    if not mp4:
        raise SystemExit("No existe %s" % a.nombre)
    if mp4.endswith(".png"):
        # Fotograma/hoja renderizados: sin trozos de texto (tEXt/iTXt/zTXt/eXIf) que lleven nada
        datos = open(mp4, "rb").read()
        raros = [c for c in (b"tEXt", b"iTXt", b"zTXt", b"eXIf") if c in datos]
        if raros:
            raise SystemExit("ABORTA: el PNG lleva metadatos %s" % raros)
        destino = _marca()          # depende del órgano ACTIVO, no de una constante fija:
        os.makedirs(destino, exist_ok=True)   # con la constante, el esqueleto caía en la del hígado
        dst = os.path.join(destino, os.path.basename(mp4).replace(".png", "-BORRADOR.png"))
        shutil.copy(mp4, dst)
        print("exportado (borrador):", os.path.relpath(dst, REPO))
        return 0
    if mp4.endswith(".json"):
        # El mapa de huesos son fracciones 0-1 de la imagen y nombres de hueso: nada que
        # identifique. Pero se revisa igual, porque el fichero de origen sí lleva la serie.
        datos = _j.loads(open(mp4, encoding="utf-8").read())
        permitido = {"imagen", "tamano", "huesos"}
        fuera = sorted(set(datos) - permitido)
        if fuera:
            print("exporta: se dejan fuera las claves %s" % fuera)
        limpio = {k: datos[k] for k in permitido if k in datos}
        for nombre, c in limpio.get("huesos", {}).items():
            if not (isinstance(c, dict) and set(c) <= {"u", "v"}
                    and all(isinstance(x, (int, float)) for x in c.values())):
                raise SystemExit("ABORTA: %s no es un par u/v numérico" % nombre)
        destino = _marca()
        os.makedirs(destino, exist_ok=True)
        dst = os.path.join(destino, os.path.basename(mp4).replace(".json", "-BORRADOR.json"))
        _j.dump(limpio, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("exportado (borrador):", os.path.relpath(dst, REPO))
        return 0
    if not mp4.endswith(".mp4"):
        raise SystemExit("Solo se exportan .mp4, .png o .json renderizados")
    info = _j.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                    "-of", "json", mp4], capture_output=True, text=True,
                                   check=True).stdout)
    permitidas = {"major_brand", "minor_version", "compatible_brands", "encoder",
                  "handler_name", "vendor_id", "language"}
    etiquetas = dict(info["format"].get("tags", {}))
    for st in info["streams"]:
        etiquetas.update(st.get("tags", {}))
    raras = sorted(set(etiquetas) - permitidas)
    if raras:
        raise SystemExit("ABORTA: el MP4 lleva metadatos no esperados: %s" % raras)
    destino = _marca()
    os.makedirs(destino, exist_ok=True)
    dst = os.path.join(destino, os.path.basename(mp4).replace(".mp4", "-BORRADOR.mp4"))
    shutil.copy(mp4, dst)
    print("exportado (borrador, NO publicado):", os.path.relpath(dst, REPO))
    return 0


def _lee_ply(ruta):
    import numpy as np
    with open(ruta, "rb") as f:
        cab = b""
        while not cab.endswith(b"end_header\n"):
            cab += f.readline()
        nv = int([l for l in cab.split(b"\n") if l.startswith(b"element vertex")][0].split()[-1])
        return np.frombuffer(f.read(nv * 12), dtype="<f4").reshape(nv, 3)


def _cmd_proporcion(a):
    """¿Las lesiones del render están a escala? Diámetro axial máximo de cada MALLA (plano
    x-y del RAS, entre vértices) frente al diámetro medido en la segmentación. Un cociente
    ≈1 = proporción real; >1,15 = la malla la agranda."""
    import numpy as np
    from scipy.spatial.distance import pdist
    d = _dir("assets", a.carpeta)
    est = json.load(open(os.path.join(d, "estudio.json")))
    peor = 0.0
    for L in est["lesiones"]:
        if not L.get("malla"):
            continue
        v = _lee_ply(os.path.join(d, L["malla"]))
        if len(v) > 3000:
            v = v[np.random.default_rng(0).choice(len(v), 3000, replace=False)]
        # por cortes axiales de 1 mm: máximo diámetro en el plano, como la medida
        z = np.round(v[:, 2])
        dmax = max((pdist(v[z == k][:, :2]).max() for k in np.unique(z) if (z == k).sum() > 2),
                   default=0.0)
        r = dmax / L["diametro_mm"] if L["diametro_mm"] else float("nan")
        peor = max(peor, r)
        print("L%-2d medida %5.1f mm · malla %5.1f mm · cociente %.2f" % (L["id"], L["diametro_mm"],
                                                                     dmax, r))
    print("cociente máximo: %.2f" % peor)
    return 0


def _cmd_foto(a):
    """Un fotograma del render (sin texto) para páginas de la web: se graba con el mismo
    Chrome sin interfaz y queda en visor/video/ listo para `exporta`."""
    import shutil
    import subprocess
    vdir = _dir("visor", "video")
    exige_zona_clinica(vdir)
    tmp = _dir("video", "_foto")
    shutil.rmtree(tmp, ignore_errors=True)
    url = "http://127.0.0.1:%d/render.html?fecha=%s&ss=%d&texto=0&lesiones=%s&t=%s" % (
        a.puerto, a.fecha, a.ss, a.lesiones, a.t)
    subprocess.run(["node", os.path.join(WEB, "grabar.mjs"), url, tmp, "1", str(a.ancho),
                    str(a.alto)], check=True)
    os.makedirs(vdir, exist_ok=True)
    dst = os.path.join(vdir, a.nombre + ".png")
    shutil.copy(os.path.join(tmp, "f0000.png"), dst)
    shutil.rmtree(tmp, ignore_errors=True)
    print("foto:", os.path.relpath(dst, SALIDA_RAIZ))
    return 0


def _agrupa_vertices(v, f, rejilla_mm):
    """Simplifica una malla juntando los vértices que caen en la misma celda de `rejilla_mm`
    (vertex clustering). Sin dependencias; quita las caras que quedan degeneradas."""
    import numpy as np
    if not rejilla_mm:
        return v, f
    celda = np.floor(v / rejilla_mm).astype(np.int64)
    _, inv, cuenta = np.unique(celda, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    nv = np.zeros((len(cuenta), 3))
    np.add.at(nv, inv, v)
    nv /= cuenta[:, None]
    nf = inv[f]
    ok = (nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])
    return nv, nf[ok]


def _lee_ply_caras(ruta):
    """(vértices, caras) de un PLY escrito por ply_binario."""
    import numpy as np
    with open(ruta, "rb") as fh:
        cab = b""
        while not cab.endswith(b"end_header\n"):
            cab += fh.readline()
        lin = cab.split(b"\n")
        nv = int([l for l in lin if l.startswith(b"element vertex")][0].split()[-1])
        nf = int([l for l in lin if l.startswith(b"element face")][0].split()[-1])
        v = np.frombuffer(fh.read(nv * 12), dtype="<f4").reshape(nv, 3).astype(float)
        c = np.frombuffer(fh.read(nf * 13), dtype=[("n", "u1"), ("i", "<i4", (3,))])
    return v, c["i"].copy()


def _cierra_vasos(lab, vox, mm, solo_conectados=False):
    """Máscara de las venas hepáticas con los huecos pequeños cerrados.

    La segmentación automática sale troceada (cortes de 2,5 mm: 30 trozos, el mayor con el
    38 %, en el caso real). Cierre morfológico con una bola de `mm`, anclado en porta y cava
    para que las ramas lleguen a sus troncos: salva huecos de unos milímetros (con 4 mm, un hueco
    de 3 mm sí y uno de 5 mm no; test_visor3d), no dibuja ramas nuevas. `solo_conectados` quita las islas que no tocan ningún tronco."""
    import numpy as np
    from scipy import ndimage
    r = max(1, int(round(mm / vox)))
    bola = np.linalg.norm(np.indices((2 * r + 1,) * 3) - r, axis=0) <= r
    troncos = (lab == ETIQUETAS["porta"]) | (lab == ETIQUETAS["vci"])
    vasos = lab == ETIQUETAS["vasos"]
    cerrado = ndimage.binary_closing(np.pad(vasos | troncos, r + 1), structure=bola)[
        (slice(r + 1, -(r + 1)),) * 3]
    dentro = ndimage.binary_dilation(lab > 0, iterations=2)
    nuevo = (cerrado | vasos) & ~troncos & dentro
    if solo_conectados:
        comp, _ = ndimage.label(nuevo | troncos)
        ok = np.unique(comp[troncos])
        nuevo &= np.isin(comp, ok[ok > 0])
    return nuevo


def _web_mama(a):
    """Mallas de la mama para el visor 3D de la web: tejido fibroglandular + tumor.

    Mismas reglas que el hígado: sale geometría y nada más —centrada, sin coordenadas del
    escáner, PLY sin comentarios— y la medida que viaja es la DEL INFORME. La automática va
    aparte y etiquetada como automática. Lo que se malla del cuerpo es el tejido
    fibroglandular ya erosionado: sin piel, sin areola, sin pezón."""
    import numpy as np
    d = _dir("assets", a.carpeta)
    est = json.load(open(os.path.join(d, "estudio.json")))
    receta = json.load(open(os.path.join(d, "receta.json")))
    for k in ("fgt", "tumor"):
        if not est.get("mallas", {}).get(k):
            raise SystemExit("ABORTA: falta la malla «%s» en %s" % (k, a.carpeta))
    if not est.get("mm_informe"):
        raise SystemExit("ABORTA: el estudio no trae la medida del informe. La que se enseña "
                         "es siempre la del radiólogo; sin ella no se publica.")
    if not receta.get("_fuente"):
        raise SystemExit("ABORTA: la receta no dice de qué informe sale la localización.")
    partes = [("fgt", a.rejilla), ("tumor", 0.0)]
    if est["mallas"].get("mama"):
        partes.insert(0, ("mama", max(a.rejilla, 1.2)))
    if est["mallas"].get("vasos"):
        partes.insert(0, ("vasos", a.rejilla_vasos))
    geos = {k: _lee_ply_caras(os.path.join(d, est["mallas"][k])) + (rej,) for k, rej in partes}
    vf = geos["fgt"][0]
    centro = (vf.min(0) + vf.max(0)) / 2
    dst = _marca("web-lesiones", a.carpeta)
    os.makedirs(dst, exist_ok=True)
    total = 0
    for k, (v, f, rej) in geos.items():
        v, f = _agrupa_vertices(v - centro, f, rej)
        ruta = os.path.join(dst, k + ".ply")
        ply_binario(ruta, np.round(v, 2), f)
        cab = open(ruta, "rb").read(400).split(b"end_header")[0]
        if b"comment" in cab or b"obj_info" in cab:
            raise SystemExit("ABORTA: %s lleva comentarios en la cabecera" % k)
        tam = os.path.getsize(ruta)
        total += tam
        print("%-6s %7d caras · %6.0f KB" % (k, len(f), tam / 1024))
    referencias = {}
    if est.get("pezon_mm"):
        # Centrado igual que las mallas: sale una posición relativa a la pieza, no una
        # coordenada del escáner.
        referencias["pezon"] = [round(float(v), 2) for v in (np.asarray(est["pezon_mm"]) - centro)]
    escena = {
        "mallas": {k: k + ".ply" for k in geos if k != "tumor"},
        "referencias": referencias,
        "lesiones": [{"malla": "tumor.ply",
                      "diametro_auto_mm": est.get("diametro_auto_mm"),
                      "diana": "tumor primario",
                      "mm_informe": est["mm_informe"]}],
        "fuente": ("tejido fibroglandular: fracción agua/grasa de la RM Dixon, con la piel "
                   "erosionada 4 mm; tumor: %s, sin validación radiológica. La medida es la "
                   "del informe." % receta.get("origen_tumor", "segmentación automática local")),
    }
    json.dump(escena, open(os.path.join(dst, "escena.json"), "w"), ensure_ascii=False, indent=1)
    print("total %.1f MB → %s" % (total / 1048576, os.path.relpath(dst, REPO)))
    return 0


# ¿Qué dice el PET de cada lesión? Tres estados, y ninguno es «PET negativo»: con vóxel de 4 mm
# el volumen parcial hunde en el fondo hepático cualquier lesión pequeña, así que poca captación
# NO es ausencia de lesión. Medido el 20-sep en su propio PET: de 20 lesiones, 1 sobre el umbral
# PERCIST, 14 indistinguibles del fondo y 3 sin vóxel propio en el PET.
def _pet_para(carpeta):
    """(dict id→{suvmax,estado}, meta de la escena) del PET cruzado con este TC diagnóstico."""
    d = _dir("pet")
    if not os.path.isdir(d):
        return {}, None
    est = json.load(open(os.path.join(_dir("assets", carpeta), "estudio.json")))
    # estudio.json no guarda de qué serie salió, así que el PET correcto NO se puede elegir por
    # el nombre del fichero: se ata por contenido. Cada lesión del PET tiene que casar con la
    # del estudio en id Y diámetro. Si no casa, no es este TC y no se usa. Fail-closed: un
    # cruce equivocado pondría el SUV de una lesión sobre otra, en una página pública.
    suyo = {L["id"]: round(L["diametro_mm"], 1) for L in est.get("lesiones", []) if "id" in L}
    r = None
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        c = json.load(open(os.path.join(d, f)))
        filas = c.get("lesiones") or []
        if filas and all(suyo.get(L["id"]) == round(L["diametro_mm"], 1) for L in filas):
            if r is not None:
                raise SystemExit("ABORTA: dos PET casan con %s; no se puede elegir" % carpeta)
            r = c
    if r is None:
        return {}, None
    fondo = r.get("fondo_higado") or {}
    umbral = fondo.get("umbral_percist")
    techo_fondo = (fondo.get("suvmean", 0) + 2 * fondo.get("suvsd", 0)) if fondo else None
    por_id = {}
    for L in r.get("lesiones", []):
        v = L.get("suvmax")
        if v is None:
            estado = "no_evaluable"
        elif umbral is not None and v > umbral:
            estado = "sobre_umbral"
        elif techo_fondo is not None and v > techo_fondo:
            estado = "sobre_fondo"
        else:
            estado = "en_fondo"
        por_id[L["id"]] = {"suvmax": v, "estado": estado}
    # Los FOCOS activos del PET dentro del hígado, con y sin lesión segmentada debajo. Sin esto
    # la página dice «1 capta» y se lee como «hay un solo punto activo», que contradice el
    # informe («mayor número de lesiones con actividad metabólica»). Lo que el cruce mide es
    # cuántos focos COINCIDEN con la segmentación, no cuántos hay. ({{TITULAR}} lo cazó, 20-sep.)
    hig = [f for f in r.get("focos_pet", []) if f.get("organo") == "liver"]
    con = [f for f in hig if f.get("lesion_cercana") and (f.get("distancia_mm") or 99) <= 8]
    sueltos = [{"suvmax": f["suvmax"], "segmento": f.get("segmento"),
                "distancia_mm": f.get("distancia_mm"), "ras": f.get("centro_mm")}
               for f in hig if f not in con and f.get("centro_mm")]
    meta = {"fecha": (r.get("pet") or {}).get("fecha"),
            "focos_higado": len(hig), "focos_sobre_lesion": len(con),
            "focos_sin_lesion": len(hig) - len(con), "_sueltos": sueltos,
            "fondo_suvmean": fondo.get("suvmean"), "fondo_suvsd": fondo.get("suvsd"),
            "umbral_percist": umbral,
            "dice_registro": r.get("dice_registro_higado"),
            "ratio_vs_fabricante": (r.get("pet") or {}).get("ratio_vs_fabricante"),
            "fuente": "SUVmax por lesión del PET-TC FDG del mismo día, cruzado con el TC por "
                      "registro deformable del hígado. Poca captación NO descarta lesión: con "
                      "vóxel de 4 mm el volumen parcial hunde en el fondo a las pequeñas."}
    return por_id, meta


def _cmd_web(a):
    """Mallas para el visor 3D de la web (/lesiones): hígado, vasos, vesícula y las lesiones
    (todas, o solo las dianas con --lesiones dianas). Sale geometría y nada más: centrada en el hígado (sin
    coordenadas del escáner), PLY sin comentarios y un escena.json con las etiquetas del
    informe o el diámetro automático. Va a borradores de marca; a la web la sube un humano con su OK."""
    import numpy as np
    if ORGANO == "mama":
        return _web_mama(a)
    d = _dir("assets", a.carpeta)
    est = json.load(open(os.path.join(d, "estudio.json")))
    ref = json.load(open(_dir("referencia.json")))
    ref = ref.get(a.carpeta.split("_")[0], {})
    if not ref:
        raise SystemExit("ABORTA: no hay medidas del radiólogo para %s" % a.carpeta)
    piezas = [(k, est["mallas"][k], a.rejilla if k == "higado" else a.rejilla_vasos)
              for k in ("higado", "porta", "vasos", "vci", "vesicula") if est["mallas"].get(k)]
    pet_por_id, pet_meta = _pet_para(a.carpeta)
    # Todas las lesiones ({{TITULAR}}, 19-sep): las dianas con la medida del radiólogo; el resto,
    # con su diámetro automático y marcadas como detección automática sin validar.
    lesiones, nd = [], 0
    for L in sorted(est["lesiones"], key=lambda x: -x["diametro_mm"]):
        if not L.get("malla"):
            continue
        r = ref.get(str(L["id"]))
        if a.lesiones == "dianas" and not r:
            continue
        nombre = "lesion%02d" % (len(lesiones) + 1)
        piezas.append((nombre, L["malla"], 0))
        pieza = {"malla": nombre + ".ply", "diametro_auto_mm": round(L["diametro_mm"], 1),
                 "diana": r["etiqueta"] if r else None, "mm_informe": r["mm"] if r else None}
        # PET: se une por el id ORIGINAL de la lesión. La web las renumera por DIÁMETRO y el
        # PET las numera por VOLUMEN, así que casarlas por posición pone el SUV en la lesión
        # equivocada (comprobado el 20-sep: lesion01 de la web es la L2 del PET).
        if pet_por_id:
            q = pet_por_id.get(L["id"])
            if q:
                pieza["suvmax"] = q["suvmax"]
                pieza["pet"] = q["estado"]
        lesiones.append(pieza)
        nd += bool(r)
    if not nd:
        raise SystemExit("ABORTA: ninguna lesión del estudio casa con las dianas del informe")
    if "higado" not in dict((k, 1) for k, _, _ in piezas):
        raise SystemExit("ABORTA: el estudio no tiene malla del hígado")
    geos = {k: _lee_ply_caras(os.path.join(d, f)) + (rej,) for k, f, rej in piezas}
    if a.cierre_vasos and "vasos" in geos:
        import nibabel as nib
        img = nib.load(os.path.join(d, "etiquetas.nii.gz"))
        vox = float(est.get("voxel_mm") or 1.0)
        nuevo = _cierra_vasos(np.asarray(img.dataobj), vox, a.cierre_vasos, a.solo_conectados)
        res = _malla(nuevo, img.affine, 0.6 / (vox / 2.0), min_componente=int(300 / vox ** 3))
        if res:
            geos["vasos"] = (res[0], res[1], geos["vasos"][2])
    vh = geos["higado"][0]
    centro = (vh.min(0) + vh.max(0)) / 2
    dst = _marca("web-lesiones", a.carpeta)
    os.makedirs(dst, exist_ok=True)
    total = 0
    for k, (v, f, rej) in geos.items():
        v, f = _agrupa_vertices(v - centro, f, rej)
        ruta = os.path.join(dst, k + ".ply")
        ply_binario(ruta, np.round(v, 2), f)
        cab = open(ruta, "rb").read(400).split(b"end_header")[0]
        if b"comment" in cab or b"obj_info" in cab:
            raise SystemExit("ABORTA: %s lleva comentarios en la cabecera" % k)
        tam = os.path.getsize(ruta)
        total += tam
        print("%-9s %7d caras · %6.0f KB" % (k, len(f), tam / 1024))
    escena = {"mallas": {k: k + ".ply" for k in geos if not k.startswith("lesion")},
              "lesiones": lesiones,
              "fuente": "dianas: informe radiológico (RECIST); resto: segmentación automática "
                        "local, sin validación radiológica"}
    if pet_meta:
        sueltos = pet_meta.pop("_sueltos", [])
        escena["pet"] = pet_meta
        # Los focos, al MISMO marco que las mallas: RAS → ejes de three, menos el centro del
        # hígado. Si se hace a ojo o se salta un paso, el marcador aparece en otro sitio del
        # órgano, que en una página pública es peor que no ponerlo.
        if sueltos:
            escena["focos"] = []
            for f in sueltos:
                # OJO al orden: las mallas se centran en RAS (v - centro) y los ejes se
                # rotan DESPUÉS, ya en el navegador. Rotar primero y restar después pone el
                # anillo en otro punto del hígado.
                v4 = np.append(np.array(f["ras"], float) - centro, 1.0)
                xyz = (RAS_A_THREE @ v4)[:3]
                escena["focos"].append({"suvmax": f["suvmax"], "segmento": f["segmento"],
                                        "distancia_mm": f["distancia_mm"],
                                        "centro": [round(float(c), 2) for c in xyz]})
    json.dump(escena, open(os.path.join(dst, "escena.json"), "w"), ensure_ascii=False, indent=1)
    print("total %.1f MB → %s" % (total / 1048576, os.path.relpath(dst, REPO)))
    return 0


def _cmd_reservorio(a):
    """Vista del reservorio venoso en un TC: el metal de la pared torácica, en tres MIPs.

    MIRA, no concluye. Aísla lo que tiene densidad de metal en la mitad anterior del tórax, se
    queda con el objeto más grande y guarda una lámina con tres proyecciones (axial, coronal,
    sagital) A ESCALA REAL en mm —los cortes miden más que el píxel, y sin reescalar la forma
    sale deformada, que es justo lo que se quiere mirar—. La lámina va a la carpeta del visor:
    es la única que se puede ver sin sacarla de la zona clínica (`sirve`, solo 127.0.0.1).

    Identificar el MODELO no lo hace esto. Sin la referencia del fabricante la forma orienta,
    no prueba. Nació el 21-sep-2026 para ver si el reservorio de la titular es un PowerPort
    antes de un TC con contraste, porque ningún informe lo dice.
    """
    import numpy as np
    import nibabel as nib
    from scipy import ndimage
    from PIL import Image, ImageDraw
    ruta = os.path.join(_cache(a.serie), "imagen.nii.gz")
    if not os.path.exists(ruta):
        print("sin volumen de %s: corre antes `convierte`" % a.serie, file=sys.stderr)
        return 2
    img = nib.as_closest_canonical(nib.load(ruta))          # RAS: +x derecha, +y anterior, +z arriba
    ct = np.asarray(img.dataobj).astype(np.float32)
    esp = np.array(img.header.get_zooms()[:3], dtype=float)
    metal = ct > a.umbral
    metal[:, : ct.shape[1] // 2, :] = False                 # solo la mitad anterior
    lab, n = ndimage.label(metal)
    if not n:
        print("nada con densidad > %.0f HU en la mitad anterior" % a.umbral, file=sys.stderr)
        return 1
    tam = ndimage.sum(metal, lab, index=range(1, n + 1))
    obj = lab == int(np.argmax(tam)) + 1
    idx = np.argwhere(obj)
    lo, hi = idx.min(0), idx.max(0)
    dims = (hi - lo + 1) * esp
    cen = idx.mean(0)
    vals = ct[obj]
    lado = "derecha" if cen[0] > ct.shape[0] / 2 else "izquierda"
    print("objeto metálico más grande (mitad anterior): lado %s del paciente" % lado)
    print("  caja: %.0f x %.0f x %.0f mm (izq-dcha x ant-post x cráneo-caudal)" % tuple(dims))
    print("  volumen del metal: %.0f mm3 · HU máx %.0f · mediana %.0f · objetos por encima del umbral: %d"
          % (obj.sum() * esp.prod(), vals.max(), np.median(vals), n))

    m = np.ceil(a.margen / esp).astype(int)                 # margen alrededor, en mm
    a0, b0 = np.maximum(lo - m, 0), np.minimum(hi + m + 1, ct.shape)
    sub = ct[a0[0]:b0[0], a0[1]:b0[1], a0[2]:b0[2]]
    PX = a.px                                               # px por mm

    def panel(mip, ex, ey, titulo):
        """mip[i,j] con i en eje de espaciado ex y j en ey → imagen a escala, j hacia arriba."""
        v = np.clip((mip - a.vmin) / (a.vmax - a.vmin), 0, 1)
        im = Image.fromarray((v.T[::-1] * 255).astype(np.uint8))   # j arriba
        im = im.resize((max(1, int(mip.shape[0] * ex * PX)), max(1, int(mip.shape[1] * ey * PX))),
                       Image.LANCZOS).convert("RGB")
        d = ImageDraw.Draw(im)
        d.text((6, 4), titulo, fill=(255, 220, 0))
        y = im.height - 12
        d.line([(6, y), (6 + int(10 * PX), y)], fill=(255, 220, 0), width=2)
        d.text((6, y - 12), "10 mm", fill=(255, 220, 0))
        return im

    # Convención radiológica: la derecha del paciente, a la izquierda de la pantalla.
    ax = panel(sub.max(axis=2)[::-1, :], esp[0], esp[1], "AXIAL (desde arriba, anterior arriba)")
    co = panel(sub.max(axis=1)[::-1, :], esp[0], esp[2], "CORONAL (de frente)")
    sa = panel(sub.max(axis=0), esp[1], esp[2], "SAGITAL (de lado, anterior a la dcha)")
    ancho = ax.width + co.width + sa.width + 40
    alto = max(ax.height, co.height, sa.height) + 20
    lam = Image.new("RGB", (ancho, alto), (0, 0, 0))
    x = 10
    for p_ in (ax, co, sa):
        lam.paste(p_, (x, 10)); x += p_.width + 10
    dst = _dir("visor")
    exige_zona_clinica(dst)
    os.makedirs(dst, exist_ok=True)
    salida = os.path.join(dst, "reservorio_%s.png" % a.serie)
    lam.save(salida)
    print("lámina: %s  (se ve con `sirve`)" % os.path.basename(salida))
    return 0


def _cmd_sirve(a):
    """Sirve el visor SOLO en 127.0.0.1 (nunca 0.0.0.0): verlo no es publicarlo."""
    import functools
    import http.server
    dst = _dir("visor")
    exige_zona_clinica(dst)
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=dst)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.puerto), h)
    print("visor en http://127.0.0.1:%d/ (Ctrl-C para parar)" % a.puerto, flush=True)
    srv.serve_forever()


# ─── PET: SUV por lesión (PET original + su TC de baja dosis + registro del hígado) ──────

def _pet_suv(raices, serie_pet):
    """PET ORIGINAL → (volumen SUVbw en RAS, afín RAS, meta). Rescale por corte y SUV calculado
    desde la cabecera (peso, dosis, decaimiento). No se usa la serie 'SUV' del fabricante: es
    DERIVED/SECONDARY; queda solo como contraste."""
    import math
    import numpy as np
    import pydicom
    carpeta, uid = _localiza_serie(raices, serie_pet)
    cortes = []
    for f in os.listdir(carpeta):
        try:
            ds = pydicom.dcmread(os.path.join(carpeta, f))
        except Exception:
            continue
        if str(ds.get("SeriesInstanceUID", "")) == uid:
            cortes.append(ds)
    if not cortes:
        raise SystemExit("PET %s sin cortes legibles" % serie_pet)
    d0 = cortes[0]
    iop = np.array(d0.ImageOrientationPatient, float)
    fila, col = iop[:3], iop[3:]
    normal = np.cross(fila, col)
    cortes.sort(key=lambda d: float(np.dot(np.array(d.ImagePositionPatient, float), normal)))
    unidades = str(d0.get("Units", ""))

    def _priv(d, elem):
        try:
            return float(d[0x7053, elem].value)
        except (KeyError, TypeError, ValueError):
            return None
    # Philips guarda cuentas (CNTS) y el factor a Bq/ml en (7053,1009); el SUV del fabricante,
    # en (7053,1000). El primero entra en el cálculo; el segundo solo se usa para contrastar.
    if unidades == "CNTS" and all(_priv(d, 0x1009) for d in cortes):
        escala = [_priv(d, 0x1009) for d in cortes]
        unidades = "BQML"
    else:
        escala = [1.0] * len(cortes)
    vol = np.stack([(d.pixel_array.astype(np.float32) * float(d.get("RescaleSlope", 1))
                     + float(d.get("RescaleIntercept", 0))) * e for d, e in zip(cortes, escala)],
                   axis=-1)  # [fila,col,k]
    suv_fabricante = _priv(d0, 0x1000)
    vol = np.transpose(vol, (1, 0, 2))                                                # [i=col,j=fila,k]
    ps = [float(x) for x in d0.PixelSpacing]          # [entre filas, entre columnas]
    p0 = np.array(cortes[0].ImagePositionPatient, float)
    pk = (np.array(cortes[-1].ImagePositionPatient, float) - p0) / max(len(cortes) - 1, 1)
    lps = np.eye(4)
    lps[:3, 0], lps[:3, 1], lps[:3, 2], lps[:3, 3] = fila * ps[1], col * ps[0], pk, p0
    afin = np.diag([-1.0, -1.0, 1.0, 1.0]) @ lps
    if unidades != "BQML":
        raise SystemExit("PET %s en unidades %r, no BQML: no calculo SUV." % (serie_pet, unidades))
    rf = d0.RadiopharmaceuticalInformationSequence[0]
    dosis = float(rf.RadionuclideTotalDose)
    semivida = float(rf.RadionuclideHalfLife)
    t_iny = _hms(rf.get("RadiopharmaceuticalStartTime"))
    t_ref = _hms(d0.get("SeriesTime")) if str(d0.get("DecayCorrection", "")) == "START" \
        else _hms(d0.get("AcquisitionTime"))
    peso = float(d0.PatientWeight)
    dt = (t_ref - t_iny) if (t_ref is not None and t_iny is not None) else None
    if dt is None or dt < 0:
        raise SystemExit("PET %s: no puedo fechar la inyección frente a la adquisición." % serie_pet)
    dosis_corr = dosis * math.exp(-math.log(2) * dt / semivida)
    factor = peso * 1000.0 / dosis_corr
    # Contraste con el SUV del fabricante: su factor lleva de cuentas a SUV; el nuestro, de Bq/ml.
    ratio = None
    if suv_fabricante and escala[0]:
        ratio = round(factor / (suv_fabricante / escala[0]), 4)
    meta = {"serie": serie_pet, "fecha": str(d0.get("StudyDate", "")),
            "ratio_vs_fabricante": ratio,
            "descripcion": str(d0.get("SeriesDescription", ""))[:60],
            "min_desde_inyeccion": round(dt / 60.0, 1), "correccion": str(d0.get("DecayCorrection", "")),
            "voxel_mm": [round(abs(ps[1]), 2), round(abs(ps[0]), 2),
                         round(float(np.linalg.norm(pk)), 2)]}
    return vol * factor, afin, meta


def _registra_higado(mask_fijo_ruta, mask_movil_ruta, label_fijo, label_movil):
    """Hígado del TC diagnóstico (móvil) → hígado del TC del PET (fijo): rígido (Euler 3D por
    momentos) y luego deformable (Demons difeomórfico sobre mapas de distancia con signo).
    Solo rígido dejaba los focos a 16-24 mm de su lesión en el caso real (Dice 0,85): la
    respiración y la postura del PET-TC no son las del TC diagnóstico.
    Devuelve (transformada fijo→móvil para sitk.Resample, Dice rígido, Dice final, imagen fija)."""
    import numpy as np
    import SimpleITK as sitk
    fijo_img = sitk.ReadImage(mask_fijo_ruta)

    def reducida(im, label, mm=2.5):
        # Alinear dos contornos de hígado no necesita 0,8 mm: a resolución nativa el Demons
        # tardaba >18 min de CPU sin acabar (19-sep). A 2,5 mm, segundos.
        b = sitk.Cast(im == label, sitk.sitkFloat32)
        tam = [int(round(n * e / mm)) for n, e in zip(b.GetSize(), b.GetSpacing())]
        return sitk.Resample(b, tam, sitk.Transform(), sitk.sitkLinear, b.GetOrigin(),
                             [mm] * 3, b.GetDirection(), 0.0, sitk.sitkFloat32)
    fijo = reducida(fijo_img, label_fijo)
    movil = reducida(sitk.ReadImage(mask_movil_ruta), label_movil)
    fijo_s = sitk.SmoothingRecursiveGaussian(fijo, 3.0)
    movil_s = sitk.SmoothingRecursiveGaussian(movil, 3.0)
    ini = sitk.CenteredTransformInitializer(fijo_s, movil_s, sitk.Euler3DTransform(),
                                            sitk.CenteredTransformInitializerFilter.MOMENTS)
    reg = sitk.ImageRegistrationMethod()
    reg.SetMetricAsMeanSquares()
    reg.SetOptimizerAsRegularStepGradientDescent(2.0, 1e-3, 300)
    reg.SetOptimizerScalesFromPhysicalShift()
    reg.SetShrinkFactorsPerLevel([4, 2, 1])
    reg.SetSmoothingSigmasPerLevel([2, 1, 0])
    reg.SetInterpolator(sitk.sitkLinear)
    reg.SetInitialTransform(ini, inPlace=False)
    rigido = reg.Execute(fijo_s, movil_s)

    def dice(t):
        r = sitk.Resample(movil, fijo, t, sitk.sitkNearestNeighbor, 0.0)
        a = sitk.GetArrayFromImage(fijo) > 0.5
        b = sitk.GetArrayFromImage(r) > 0.5
        return float(2 * (a & b).sum() / max(a.sum() + b.sum(), 1))
    d_rig = dice(rigido)
    movil_r = sitk.Resample(movil, fijo, rigido, sitk.sitkLinear, 0.0)
    sdm = lambda im: sitk.SignedMaurerDistanceMap(im > 0.5, insideIsPositive=False,  # noqa: E731
                                                  squaredDistance=False, useImageSpacing=True)
    demons = sitk.DiffeomorphicDemonsRegistrationFilter()
    demons.SetNumberOfIterations(150)
    demons.SetStandardDeviations(2.0)
    campo = demons.Execute(sdm(fijo), sdm(movil_r))
    desplaz = sitk.DisplacementFieldTransform(sitk.Cast(campo, sitk.sitkVectorFloat64))
    comp = sitk.CompositeTransform([rigido, desplaz])   # se aplica: desplazamiento, luego rígido
    return comp, d_rig, dice(comp), fijo_img


def _ids_en_pet(ids_ruta, transformada, ref_img):
    """Mapa de ids de lesiones del TC diagnóstico → rejilla del TC del PET (vecino más cercano)."""
    import SimpleITK as sitk
    return sitk.GetArrayFromImage(sitk.Resample(sitk.ReadImage(ids_ruta), ref_img, transformada,
                                                sitk.sitkNearestNeighbor, 0))


def suv_lesiones(raices, serie_pet, serie_ctpet, serie_ctdiag):
    import numpy as np
    import nibabel as nib
    import SimpleITK as sitk
    from nibabel.processing import resample_from_to
    from scipy import ndimage
    from totalsegmentator.map_to_binary import class_map
    suv, afin_pet, meta = _pet_suv(raices, serie_pet)
    cm = {v: k for k, v in class_map["total"].items()}
    seg_pet = segmenta(raices, serie_ctpet, tareas=["total"])["total"]
    # Todas las estructuras del TC del PET: para saber DÓNDE cae cada foco (hígado, riñón,
    # intestino…). Con solo 6 órganos, el riñón no existía y su excreción pasaba por «foco».
    seg_pet_completo = segmenta(raices, serie_ctpet, tareas=["total_completo"])["total_completo"]
    seg_diag = segmenta(raices, serie_ctdiag, tareas=["total"])["total"]
    info = lesiones(raices, serie_ctdiag)       # también escribe lesiones_id.nii.gz
    t, d_rig, d_fin, _ = _registra_higado(seg_pet, seg_diag, cm["liver"], cm["liver"])
    ref = sitk.ReadImage(seg_pet)   # rejilla nativa del TC del PET para llevar los ids
    ids_ct = _ids_en_pet(os.path.join(_cache(serie_ctdiag), "lesiones_id.nii.gz"), t, ref)
    # sitk devuelve [z,y,x] en LPS; se reescribe como NIfTI para llevarlo con nibabel al PET
    tmp = os.path.join(_cache(serie_ctpet), "_ids_desde_%s.nii.gz" % serie_ctdiag)
    im = sitk.GetImageFromArray(ids_ct)
    im.CopyInformation(ref)
    sitk.WriteImage(im, tmp)
    pet_img = nib.Nifti1Image(suv, afin_pet)
    ids = np.asarray(resample_from_to(nib.load(tmp), pet_img, order=0).dataobj).astype(np.int16)
    # Segmentos de Couinaud llevados igual (misma transformada): el segmento de cada foco.
    segs_pet = None
    seg_diag_segm = segmenta(raices, serie_ctdiag).get("liver_segments")
    if seg_diag_segm:
        arr = _ids_en_pet(seg_diag_segm, t, ref)
        im2 = sitk.GetImageFromArray(arr)
        im2.CopyInformation(ref)
        tmp2 = os.path.join(_cache(serie_ctpet), "_segmentos_desde_%s.nii.gz" % serie_ctdiag)
        sitk.WriteImage(im2, tmp2)
        segs_pet = np.asarray(resample_from_to(nib.load(tmp2), pet_img, order=0).dataobj)
    tot = np.asarray(resample_from_to(nib.load(seg_pet_completo), pet_img, order=0).dataobj)
    hig_pet = tot == cm["liver"]
    nombre_de = class_map["total"]
    vox = min(meta["voxel_mm"])
    # Riñones y vesícula fuera (la excreción urinaria daba un «foco» de SUV 28 pegado al hígado)
    excl = np.isin(tot, [v for k, v in cm.items() if k in VECINOS_EXCRECION])
    excl = ndimage.binary_dilation(excl, iterations=2)
    hig_ero = ndimage.binary_erosion(hig_pet, iterations=max(1, int(10 / vox)))
    filas = []
    for L in info["lesiones"]:
        m = ids == L["id"]
        # 1 vóxel de PET (4 mm) de margen: efecto de volumen parcial y error residual de registro
        m_d = ndimage.binary_dilation(m, iterations=1) & ~excl if m.any() else m
        filas.append({"id": L["id"], "segmento": L["segmento"], "diametro_mm": L["diametro_mm"],
                      "en_pet": bool(m.any()),
                      "suvmax": round(float(suv[m_d].max()), 2) if m_d.any() else None})
    fondo = suv[hig_ero & (ids == 0) & ~excl]
    focos = []
    if fondo.size:
        umbral = 1.5 * fondo.mean() + 2 * fondo.std()
        # Sin excluir vecinos aquí: cada foco sale con su órgano y se ve, no se esconde.
        region = ndimage.binary_dilation(hig_pet, iterations=max(1, int(10 / vox)))
        comp, n = ndimage.label((suv > umbral) & region)
        dist, (ii, jj, kk) = ndimage.distance_transform_edt(ids == 0, sampling=meta["voxel_mm"],
                                                              return_indices=True)
        for i in range(1, n + 1):
            m = comp == i
            k = np.unravel_index(np.argmax(np.where(m, suv, -1)), suv.shape)
            lid = int(ids[ii[k], jj[k], kk[k]])
            segmento = None
            if segs_pet is not None:
                zona = segs_pet[m]
                zona = zona[zona > 0]
                segmento = int(np.bincount(zona.astype(int)).argmax()) if zona.size else None
            # Posición del foco EN EL ESPACIO DEL TC DIAGNÓSTICO, que es donde viven las
            # mallas del visor. PET(vóxel) → RAS mm → LPS → t → LPS del TC diag → RAS.
            # `t` es «fijo→móvil» (ctpet→ctdiag), justo el sentido que hace falta.
            ras = (afin_pet @ np.append(np.array(k, float), 1.0))[:3]
            lps = np.array([-ras[0], -ras[1], ras[2]])
            d_lps = np.array(t.TransformPoint([float(x) for x in lps]))
            centro_diag = [round(float(-d_lps[0]), 1), round(float(-d_lps[1]), 1),
                           round(float(d_lps[2]), 1)]
            focos.append({"suvmax": round(float(suv[k]), 2), "voxeles": int(m.sum()),
                          "centro_mm": centro_diag,
                          "segmento": segmento,
                          "organo": nombre_de.get(int(tot[k]), "fuera de órganos segmentados"),
                          "lesion_cercana": lid or None, "distancia_mm": round(float(dist[k]), 1)})
        focos.sort(key=lambda f: -f["suvmax"])
    return {"pet": meta, "dice_registro_higado": round(d_fin, 3),
            "dice_registro_rigido": round(d_rig, 3), "focos_pet": focos,
            "fondo_higado": {"suvmean": round(float(fondo.mean()), 2),
                             "suvsd": round(float(fondo.std()), 2),
                             "umbral_percist": round(float(1.5 * fondo.mean() + 2 * fondo.std()), 2)}
            if fondo.size else None,
            "lesiones": filas}


def _cmd_suv(a):
    r = suv_lesiones(a.raices, a.pet, a.ctpet, a.ctdiag)
    d = _dir("pet")
    exige_zona_clinica(d)
    os.makedirs(d, exist_ok=True)
    json.dump(r, open(os.path.join(d, "%s_%s.json" % (a.pet, a.ctdiag)), "w"), ensure_ascii=False,
              indent=1)
    p = r["pet"]
    print("PET %s %s · %s min p.i. · corrección %s · vóxel %s mm" % (
        p["fecha"], p["descripcion"][:30], p["min_desde_inyeccion"], p["correccion"], p["voxel_mm"]))
    print("SUV propio / SUV del fabricante: %s (1,0 = coinciden)" % p["ratio_vs_fabricante"])
    print("registro hígado TC diag → TC del PET: Dice rígido %.3f → deformable %.3f" % (
        r["dice_registro_rigido"], r["dice_registro_higado"]))
    if r["fondo_higado"]:
        f = r["fondo_higado"]
        print("fondo hepático SUVmean %.2f ± %.2f · umbral PERCIST %.2f" % (f["suvmean"], f["suvsd"],
                                                                         f["umbral_percist"]))
    for L in r["lesiones"]:
        print("  L%-2d seg %-4s %5.1f mm  SUVmax %s" % (L["id"], L["segmento"], L["diametro_mm"],
                                                    L["suvmax"]))
    print("focos calientes en el PET (sobre umbral PERCIST, dentro del hígado):")
    seg_de = {L["id"]: L["segmento"] for L in r["lesiones"]}
    for f in r["focos_pet"][:12]:
        print("  SUVmax %5.2f  (%3d vóx)  en %-18s seg %-4s → lesión más cercana L%s (seg %s) a %s mm" % (
            f["suvmax"], f["voxeles"], f["organo"], f["segmento"], f["lesion_cercana"],
            seg_de.get(f["lesion_cercana"]), f["distancia_mm"]))
    return 0


# ─── ficha por lesión: densidad + evolución entre TCs + PET de cada fecha ────────────────
# Nace de una crítica pública (21-sep): «¿por qué no determinas si son benignas o malignas?».
# La respuesta honesta es que eso es del radiólogo, pero sí hay tres señales objetivas por
# lesión que un modelo puede medir y que un radiólogo revisa en minutos: densidad (HU),
# si ya estaba en el TC anterior y cómo ha cambiado, y si capta en el PET de cada fecha.
# NADA de esto es un diagnóstico: la ficha lo dice en cada fila.

HU_LIQUIDO = 20        # por debajo, densidad de líquido en portal (quiste simple ~0-20 HU)


def densidad_lesiones(ct, ids, higado, margen_vox=1, excluir=None):
    """HU por lesión (id>0 en `ids`) y del parénquima sano. Pura, sin I/O (se testea).

    El núcleo de cada lesión se erosiona `margen_vox` para no promediar el borde con el
    parénquima (volumen parcial); si la erosión la vacía (lesión pequeña), se usa entera y
    se marca, porque esa cifra está contaminada por el borde.
    """
    import numpy as np
    from scipy import ndimage
    lesion_any = ids > 0
    sano = higado & ~ndimage.binary_dilation(lesion_any, iterations=3)
    if excluir is not None:
        # Vasos portales (150-200 HU en portal) inflaban la referencia y el contraste salía
        # más negativo de lo real (red-team `verificacion`, 21-sep).
        sano &= ~excluir
    par = float(np.median(ct[sano])) if sano.any() else None
    out = {}
    for i in np.unique(ids[lesion_any]):
        m = ids == i
        nucleo = ndimage.binary_erosion(m, iterations=margen_vox) if margen_vox else m
        contaminada = not nucleo.any()
        v = ct[m if contaminada else nucleo]
        med = float(np.median(v))
        out[int(i)] = {"hu_mediana": round(med),
                       "hu_p10_p90": [round(float(np.percentile(v, 10))),
                                      round(float(np.percentile(v, 90)))],
                       "contraste_vs_higado_hu": round(med - par) if par is not None else None,
                       "borde_contamina": contaminada,
                       "densidad_liquido": med < HU_LIQUIDO}
    return out, (round(par) if par is not None else None)


def evolucion(pares, lesiones_despues):
    """Estado de cada lesión del estudio posterior respecto al anterior. Pura (se testea).

    «no vista antes» NO es «nueva»: el modelo pudo no detectarla en el TC previo, sobre todo
    si es pequeña. Se escribe como lo que es.
    """
    por_despues = {p["despues"]: p for p in pares if p.get("despues") is not None}
    out = {}
    for L in lesiones_despues:
        p = por_despues.get(L["id"])
        if p is None or p.get("antes") is None:
            out[L["id"]] = {"estado": "no vista en el TC previo", "id_previo": None}
            continue
        dd = p.get("delta_diametro_pct")
        estado = ("crece" if dd is not None and dd >= 20 else
                  "se reduce" if dd is not None and dd <= -30 else "estable")
        out[L["id"]] = {"estado": estado, "id_previo": p["antes"],
                        "delta_diametro_pct": dd, "delta_volumen_pct": p.get("delta_volumen_pct"),
                        "distancia_emparejado_mm": p.get("distancia_mm")}
    return out


def _pet_de(serie_ct):
    """JSON de PET ya calculado (`suv`) cuyo TC diagnóstico es esta serie, o None."""
    d = _dir("pet")
    if not os.path.isdir(d):
        return None
    for f in sorted(os.listdir(d)):
        if f.endswith("_%s.json" % serie_ct):
            return json.load(open(os.path.join(d, f)))
    return None


FOCO_SIN_LESION_MM = 10


def focos_sin_lesion(focos, max_mm=FOCO_SIN_LESION_MM):
    """Focos del PET dentro del hígado cuya lesión CT más cercana está a > max_mm. Pura."""
    return [f for f in focos if f.get("organo") == "liver"
            and (f.get("distancia_mm") is None or f["distancia_mm"] > max_mm)]


def _estado_pet(suvmax, fondo):
    if suvmax is None or not fondo:
        return "no evaluable"
    # «tipo» PERCIST: aquí es 1,5·media + 2·DE de SUV (peso) sobre el hígado entero; PERCIST
    # de verdad usa SUL y una ROI de 3 cm en el lóbulo derecho. El nombre no puede prometer más.
    if suvmax >= fondo["umbral_percist"]:
        return "sobre umbral tipo PERCIST"
    if suvmax >= fondo["suvmean"] + 2 * fondo["suvsd"]:
        return "sobre fondo"
    return "en fondo"


def ficha(raices, serie, previo=None):
    import numpy as np
    from totalsegmentator.map_to_binary import class_map
    imagen, meta = convierte(raices, serie)
    info = lesiones(raices, serie)
    ct, _ = _carga(imagen)
    ids, _ = _carga(os.path.join(_cache(serie), "lesiones_id.nii.gz"))
    total, _ = _carga(segmenta(raices, serie)["total"])
    cm = {v: k for k, v in class_map["total"].items()}
    vasos = segmenta(raices, serie).get("liver_vessels")
    vasos = (_carga(vasos)[0] == 1) if vasos else None
    dens, par = densidad_lesiones(ct, ids, total == cm["liver"], excluir=vasos)
    evo, no_reencontradas = {}, []
    if previo:
        res, _ = compara(raices, previo, serie)
        evo = evolucion(res["pares"], info["lesiones"])
        prev_de = {L["id"]: L for L in res["antes"]["lesiones"]}
        no_reencontradas = [{"id_previo": p["antes"], "segmento": prev_de[p["antes"]]["segmento"],
                             "diametro_mm": prev_de[p["antes"]]["diametro_mm"]}
                            for p in res["pares"] if p.get("despues") is None]
    pet_now, pet_prev = _pet_de(serie), (_pet_de(previo) if previo else None)
    suv_now = {L["id"]: L["suvmax"] for L in (pet_now or {}).get("lesiones", [])}
    suv_prev = {L["id"]: L["suvmax"] for L in (pet_prev or {}).get("lesiones", [])}
    filas = []
    for L in info["lesiones"]:
        e = evo.get(L["id"], {})
        ip = e.get("id_previo")
        filas.append({
            "id": L["id"], "segmento": L["segmento"], "diametro_mm": L["diametro_mm"],
            "volumen_ml": L["volumen_ml"], "pequena": L["pequena"],
            "acuerdo_2o_modelo": L["acuerdo_2o_modelo"],
            "densidad": dens.get(L["id"]),
            "evolucion": e or None,
            "pet": {"suvmax": suv_now.get(L["id"]),
                    "estado": _estado_pet(suv_now.get(L["id"]),
                                          (pet_now or {}).get("fondo_higado"))} if pet_now else None,
            "pet_previo": {"suvmax": suv_prev.get(ip),
                           "estado": _estado_pet(suv_prev.get(ip),
                                                 (pet_prev or {}).get("fondo_higado"))}
            if (pet_prev and ip) else None,
        })
    r = {"aviso": "Medidas de un modelo, sin validar por radiología. No es un diagnóstico.",
         "serie": serie, "fecha": meta["fecha"], "previo": previo,
         "fecha_previo": convierte(raices, previo)[1]["fecha"] if previo else None,
         "hu_parenquima": par,
         "pet": {k: (v or {}).get("pet", {}).get("fecha") for k, v in
                 (("actual", pet_now), ("previo", pet_prev))},
         "lesiones": filas,
         # Lo que la ficha NO cubre, a la vista: lesiones del TC previo sin pareja, y focos
         # hepáticos del PET sin lesión del modelo cerca (p.ej. el sVIII del informe, 21-sep).
         "previas_no_reencontradas": no_reencontradas,
         "focos_pet_sin_lesion": focos_sin_lesion((pet_now or {}).get("focos_pet", []))}
    d = _dir("ficha")
    exige_zona_clinica(d)
    os.makedirs(d, exist_ok=True)
    ruta = os.path.join(d, "%s%s.json" % (serie, "_desde_%s" % previo if previo else ""))
    json.dump(r, open(ruta, "w"), ensure_ascii=False, indent=1)
    return r, ruta


def _cmd_ficha(a):
    r, ruta = ficha(a.raices, a.series[0], a.previo)
    print("FICHA %s (TC %s) · previo %s (%s) · parénquima %s HU · %s"
          % (r["serie"], r["fecha"], r["previo"], r["fecha_previo"], r["hu_parenquima"], r["aviso"]))
    print(" id seg  diám  vol ml  HU(p10-p90)  Δhígado  líq  evolución                 Δdiám  "
          "PET ahora              PET antes")
    for f in r["lesiones"]:
        d, e, p, q = f["densidad"] or {}, f["evolucion"] or {}, f["pet"] or {}, f["pet_previo"] or {}
        print("L%-2d %-4s %5.1f %6.2f  %4s(%s..%s)%s %6s  %-3s  %-24s %6s  %-5s %-16s %-5s %s" % (
            f["id"], f["segmento"], f["diametro_mm"], f["volumen_ml"], d.get("hu_mediana"),
            *(d.get("hu_p10_p90") or [None, None]), "*" if d.get("borde_contamina") else " ",
            d.get("contraste_vs_higado_hu"), "sí" if d.get("densidad_liquido") else "no",
            e.get("estado", "-"), e.get("delta_diametro_pct", ""), p.get("suvmax"),
            p.get("estado", "-"), q.get("suvmax"), q.get("estado", "-")))
    print("* núcleo vacío tras erosión: la HU incluye borde (volumen parcial).")
    for q in r["previas_no_reencontradas"]:
        print("previa NO reencontrada: L%s del TC previo, seg %s, %s mm"
              % (q["id_previo"], q["segmento"], q["diametro_mm"]))
    for fo in r["focos_pet_sin_lesion"]:
        print("foco PET SIN lesión del modelo: SUVmax %s seg %s, lesión más cercana a %s mm"
              % (fo["suvmax"], fo["segmento"], fo["distancia_mm"]))
    print("→", os.path.relpath(ruta, REPO))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--organo", choices=sorted(PRESETS), default="higado",
                   help="de qué órgano son las carpetas de trabajo (por defecto, higado)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("inventario", help="series DICOM bajo las raíces, sin PII")
    pi.add_argument("raices", nargs="+")
    pi.add_argument("--min-n", type=int, default=20, help="oculta series con menos imágenes")
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(fn=_cmd_inventario)
    for nombre, fn, ayuda in (("convierte", _cmd_convierte, "serie DICOM → NIfTI en caché"),
                              ("segmenta", _cmd_segmenta, "TotalSegmentator por tarea"),
                              ("fase", _cmd_fase, "fase de contraste (cabecera + HU)"),
                              ("compara", _cmd_compara, "lesiones de 2 series y su emparejado"),
                              ("assets", _cmd_assets, "volúmenes/mallas/JSON para el visor")):
        ps = sub.add_parser(nombre, help=ayuda)
        ps.add_argument("--raiz", dest="raices", action="append", required=True)
        ps.add_argument("series", nargs="+", help="huellas de serie (del inventario)")
        if nombre == "assets":
            ps.add_argument("--voxel", type=float, default=2.0)
        if nombre == "segmenta":
            ps.add_argument("--tareas", nargs="*")
            ps.add_argument("--device", default="mps")
        ps.set_defaults(fn=fn)
    pcb = sub.add_parser("cobertura", help="hasta dónde llega cada serie, en mm del mundo")
    pcb.add_argument("--raiz", dest="raices", action="append", required=True)
    pcb.add_argument("series", nargs="+")
    pcb.set_defaults(fn=_cmd_cobertura)
    pv = sub.add_parser("visor", help="monta el visor local con los assets")
    pv.add_argument("--comparacion", help="nombre del JSON de comparación (serieA_serieB)")
    pv.set_defaults(fn=_cmd_visor)
    ps_ = sub.add_parser("suv", help="SUVmax por lesión desde el PET original")
    ps_.add_argument("--raiz", dest="raices", action="append", required=True)
    ps_.add_argument("--pet", required=True)
    ps_.add_argument("--ctpet", required=True, help="TC de baja dosis del mismo PET-TC")
    ps_.add_argument("--ctdiag", required=True, help="TC diagnóstico con las lesiones")
    ps_.set_defaults(fn=_cmd_suv)

    pfi = sub.add_parser("ficha", help="por lesión: HU, evolución vs TC previo y PET de cada fecha")
    pfi.add_argument("--raiz", dest="raices", action="append", required=True)
    pfi.add_argument("series", nargs=1, help="TC diagnóstico actual")
    pfi.add_argument("--previo", help="TC diagnóstico anterior (para la evolución)")
    pfi.set_defaults(fn=_cmd_ficha)
    pdd = sub.add_parser("dicomdir", help="series que anuncia el índice del CD vs en disco")
    pdd.add_argument("raices", nargs="+")
    pdd.set_defaults(fn=_cmd_dicomdir)
    pil = sub.add_parser("ilegibles", help="ficheros que el inventario se salta, por estudio")
    pil.add_argument("raices", nargs="+")
    pil.set_defaults(fn=lambda a: print(json.dumps(ilegibles(a.raices), ensure_ascii=False,
                                                   indent=1)) or 0)
    pr = sub.add_parser("referencia", help="medidas del radiólogo (JSON por stdin)")
    pr.set_defaults(fn=_cmd_referencia)
    pvid = sub.add_parser("video", help="vídeo vertical del 3D para redes (borrador)")
    pvid.add_argument("--fecha", required=True)
    pvid.add_argument("--segundos", type=float, default=12)
    pvid.add_argument("--fps", type=int, default=30)
    pvid.add_argument("--ancho", type=int, default=1080)
    pvid.add_argument("--alto", type=int, default=1920)
    pvid.add_argument("--zoom", type=float, default=1.15)
    pvid.add_argument("--puerto", type=int, default=8794)
    pvid.add_argument("--estilo", choices=["realista", "clinico"], default="realista")
    pvid.add_argument("--ss", type=int, default=2, help="supermuestreo del render")
    pvid.add_argument("--texto", action="store_true", help="leyenda y aviso sobreimpresos")
    pvid.set_defaults(fn=_cmd_video)
    ph = sub.add_parser("hoja", help="hoja de contactos de un vídeo ya grabado")
    ph.add_argument("nombre")
    ph.add_argument("--t", type=float, help="segundo del fotograma suelto a extraer")
    ph.add_argument("--puerto", type=int, default=8794)
    ph.set_defaults(fn=_cmd_hoja)
    pe = sub.add_parser("exporta", help="saca un vídeo renderizado a borradores de marca")
    pe.add_argument("nombre")
    pe.set_defaults(fn=_cmd_exporta)
    pp = sub.add_parser("proporcion", help="¿mallas de lesiones a escala real?")
    pp.add_argument("carpeta", help="p.ej. 20260908_hd")
    pp.set_defaults(fn=_cmd_proporcion)
    pf = sub.add_parser("foto", help="un fotograma del render sin texto (para la web)")
    pf.add_argument("--fecha", required=True)
    pf.add_argument("--nombre", required=True)
    pf.add_argument("--lesiones", choices=["todas", "dianas"], default="dianas")
    pf.add_argument("--ancho", type=int, default=1200)
    pf.add_argument("--alto", type=int, default=1200)
    pf.add_argument("--ss", type=int, default=2)
    pf.add_argument("--t", type=float, default=0.0)
    pf.add_argument("--puerto", type=int, default=8794)
    pf.set_defaults(fn=_cmd_foto)
    pwe = sub.add_parser("web", help="mallas del hígado + dianas para el visor 3D de la web")
    pwe.add_argument("carpeta", help="p.ej. 20260908_hd")
    pwe.add_argument("--rejilla", type=float, default=0.0, help="mm; simplifica el hígado")
    pwe.add_argument("--rejilla-vasos", type=float, default=0.0, help="mm; simplifica los vasos")
    pwe.add_argument("--lesiones", choices=["todas", "dianas"], default="todas")
    pwe.add_argument("--cierre-vasos", type=float, default=0.0,
                     help="mm; cierra huecos de la segmentación de las venas hepáticas")
    pwe.add_argument("--solo-conectados", action="store_true",
                     help="con --cierre-vasos: quita las venas que no llegan a porta ni cava")
    pwe.set_defaults(fn=_cmd_web)
    pfo = sub.add_parser("focos", help="focos de realce de la resta: de ahí sale la semilla")
    pfo.add_argument("--raiz", dest="raices", action="append", required=True)
    pfo.add_argument("--pre", required=True, help="WATER pre-contraste del VIBRANT")
    pfo.add_argument("--post", required=True, help="WATER post-contraste (la primera fase)")
    pfo.add_argument("--grasa", help="FAT de la misma adquisición Dixon (afina el cuerpo)")
    pfo.add_argument("--registro", choices=["rigido", "ninguno"], default="rigido")
    pfo.add_argument("--percentil", type=float, default=99.5)
    pfo.add_argument("-n", type=int, default=10, help="cuántos focos listar")
    pfo.add_argument("--lado", choices=["dcha", "izda"], help="solo focos de ese lado")
    pfo.add_argument("--tardio", help="última fase: filtra por cinética (meseta o lavado)")
    pfo.add_argument("--suave", type=float, default=0, help="mm de desenfoque: deja solo masas")
    pfo.add_argument("--caja", help="x0,x1,y0,y1,z0,z1 en mm: acota la búsqueda a una mama")
    pfo.add_argument("--adc", help="serie del mapa ADC: exige difusión restringida")
    pfo.add_argument("--adc-max", type=float, default=1300,
                     help="ADC máximo en 10⁻⁶ mm²/s (por defecto 1300)")
    pfo.add_argument("--anterior", type=float, default=0,
                     help="%% del grosor del cuerpo, desde atrás: 60 deja solo la mama")
    pfo.set_defaults(fn=_cmd_focos)
    pco = sub.add_parser("cortes", help="MIP del realce, para mirar lado y cuadrante")
    pco.add_argument("--raiz", dest="raices", action="append", required=True)
    pco.add_argument("--pre", required=True)
    pco.add_argument("--post", required=True)
    pco.add_argument("--grasa", required=True)
    pco.add_argument("--registro", choices=["rigido", "ninguno"], default="ninguno")
    pco.add_argument("--piel", type=float, default=PIEL_MM)
    pco.add_argument("--nombre", default="mama")
    pco.add_argument("--a-marca", action="store_true",
                     help="saca la imagen fuera del muro, a borradores de marca, para poder verla")
    pco.add_argument("--anterior", type=float, default=0,
                     help="%% del grosor del cuerpo, desde atrás: 60 deja solo la mama")
    pco.add_argument("--semilla", action="append", help="x,y,z en mm: marca un círculo")
    pco.add_argument("--axiales", help="z0:z1:paso en mm — montaje de cortes axiales")
    pco.add_argument("--lado", choices=["dcha", "izda"], help="recorta a esa mama")
    pco.add_argument("--suave", type=float, default=0, help="mm de desenfoque: deja solo masas")
    pco.add_argument("--anatomia", action="store_true", help="enseña agua+grasa, no el realce")
    pco.add_argument("--caja", help="x0,x1,y0,y1,z0,z1 en mm: recorta a una mama")
    pco.add_argument("--imagen", help="otra serie (p. ej. el ADC) en vez del realce")
    pco.add_argument("--fgt", action="store_true", help="el tejido fibroglandular ya sin piel")
    pco.add_argument("--ff", type=float, default=FF_FIBROGLANDULAR, help="umbral de fracción grasa")
    pco.set_defaults(fn=_cmd_cortes)
    pma = sub.add_parser("mama", help="mallas de la mama (fibroglandular + tumor) desde la receta")
    pma.add_argument("--raiz", dest="raices", action="append", required=True)
    pma.add_argument("--pre", required=True, help="WATER pre-contraste del VIBRANT")
    pma.add_argument("--post", required=True, help="WATER post-contraste (la primera fase)")
    pma.add_argument("--grasa", required=True, help="FAT de la misma adquisición Dixon")
    pma.add_argument("--receta", required=True, help="JSON con semilla, percentil y radios")
    pma.set_defaults(fn=_cmd_mama)
    pmo = sub.add_parser("modelo", help="pesos públicos de lesión de mama (MAMA-MIA)")
    pmo.add_argument("--descarga", action="store_true", help="bájalos de Synapse")
    pmo.set_defaults(fn=_cmd_modelo)
    ptu = sub.add_parser("tumor", help="lesión de mama con el nnU-Net de MAMA-MIA (local)")
    ptu.add_argument("--raiz", dest="raices", action="append", required=True)
    ptu.add_argument("--serie", required=True, help="T1 post-contraste (en Dixon, la WATER)")
    ptu.add_argument("--folds", nargs="+", type=int, default=[0])
    ptu.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    ptu.add_argument("-n", type=int, default=8)
    ptu.set_defaults(fn=_cmd_tumor)
    pes = sub.add_parser("esqueleto", help="su esqueleto del TC + centroide de cada hueso")
    pes.add_argument("--raiz", dest="raices", action="append", required=True)
    pes.add_argument("--serie", required=True, help="TC de cuerpo entero (mejor kernel de hueso)")
    pes.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    pes.add_argument("--ancho", type=int, default=880)
    pes.add_argument("--solo", help="lista de huesos separados por coma: solo esos se dibujan")
    pes.add_argument("--sin-niveles", action="store_true",
                     help="no publicar centroides de vértebra (la silueta sí, los niveles no)")
    pes.add_argument("--vertebras", action="store_true",
                     help="pasada aparte, con el modelo fino, solo para nombrar cada vértebra")
    pes.add_argument("--fino", action="store_true",
                     help="modelo de 1,5 mm en vez del de 3 mm (el de 3 pierde niveles vertebrales)")
    pes.add_argument("--suaviza-mm", type=float, default=1.0,
                     help="mm a los que se remuestrea la MÁSCARA para quitar el escalón (0 = no)")
    pes.add_argument("--trozos", type=int, default=3,
                     help="en cuántas rebanadas se parte el cuerpo (el entero no cabe)")
    pes.add_argument("--solapa", type=int, default=16, help="cortes de solape entre trozos")
    pes.add_argument("--voxel", type=float, default=1.5,
                     help="mm a los que se remuestrea ANTES de segmentar (el TC entero no cabe)")
    pes.set_defaults(fn=_cmd_esqueleto)
    pre_ = sub.add_parser("reservorio", help="vista del reservorio venoso en un TC (3 MIPs del metal)")
    pre_.add_argument("serie")
    pre_.add_argument("--umbral", type=float, default=2000.0,
                      help="HU desde las que se considera metal (por defecto 2000)")
    pre_.add_argument("--margen", type=float, default=30.0, help="mm alrededor del objeto")
    pre_.add_argument("--px", type=float, default=5.0, help="px por mm en la lámina")
    pre_.add_argument("--vmin", type=float, default=-200.0, help="HU que salen negro")
    pre_.add_argument("--vmax", type=float, default=3000.0, help="HU que salen blanco")
    pre_.set_defaults(fn=_cmd_reservorio)
    pmr = sub.add_parser("marcas", help="marcas de un radiólogo sobre un TC → esferas + procedencia")
    pmr.add_argument("revision", help="lesiones_55.json de la revisión")
    pmr.add_argument("--serie", required=True, help="huella de la serie (caché de imagen.nii.gz)")
    pmr.add_argument("--fecha", required=True, help="carpeta de assets (AAAAMMDD)")
    pmr.add_argument("--empareja", action="append", help="MARCA:LESION verificado por otra vía")
    pmr.add_argument("--fuente", help="de dónde sale el emparejado forzado")
    pmr.set_defaults(fn=_cmd_marcas)
    pw = sub.add_parser("sirve", help="sirve el visor en 127.0.0.1")
    pw.add_argument("--puerto", type=int, default=8794)
    pw.set_defaults(fn=_cmd_sirve)
    a = p.parse_args(argv)
    if getattr(a, "organo", None):
        globals()["ORGANO"] = a.organo
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
