#!/usr/bin/env python3
"""Capa ILUSTRATIVA del portal en `visor3d reservorio3d` sobre un FANTOMA (nada de la paciente).

El modelo de catálogo se superpone al metal medido, nunca lo sustituye. Qué se comprueba, con
geometría conocida de antemano:
  · un «portal» triangular con vástago, girado y trasladado a propósito, se recoloca con un
    movimiento rígido a escala 1:1 y el error de superficie queda por debajo de 1 mm;
  · el metal del fantoma NO lleva vástago (como en un TC, donde el umbral no lo ve): la
    orientación entre las tres casi equivalentes la decide la salida del catéter, y el
    vástago recolocado apunta hacia ella;
  · sin catéter, el ajuste lo dice («sin anclar») en vez de callarlo;
  · con un vástago CORTO (más cerca del centro que los vértices del triángulo, como en el STL
    real) el saliente más lejano es un vértice: sin declarar el vástago la info dice ESTIMADO,
    y declarándolo el modelo queda bien orientado (el 8-oct salió girado 180° por esto);
  · la escala no se toca aunque el modelo sea más grande que el metal;
  · un STL que no es el de procedencia conocida sale rotulado «sin verificar»;
  · un STL ASCII aborta;
  · el visor arranca enseñando las vistas medidas y el modelo solo entra con el botón.

Necesita numpy/scipy/scikit-image de `.venv-imagen`: se relanza con ella si hace falta.
"""
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                    ".venv-imagen", "bin", "python")

try:
    import numpy as np
    import scipy  # noqa: F401
    import skimage  # noqa: F401
except ImportError:
    if os.path.exists(VENV) and os.environ.get("_VISOR3D_REEXEC") != "1":
        env = dict(os.environ, _VISOR3D_REEXEC="1")
        sys.exit(subprocess.call([VENV, os.path.abspath(__file__)], env=env))
    if os.environ.get("BTP_PORTABLE"):
        print("SKIP: sin .venv-imagen (modo portátil)")
        sys.exit(77)
    print("ROJO: falta .venv-imagen en casa base; el test del modelo del portal no puede correr")
    sys.exit(1)

import importlib.util  # noqa: E402

from scipy.spatial.transform import Rotation  # noqa: E402
from skimage.measure import marching_cubes  # noqa: E402

spec = importlib.util.spec_from_file_location("visor3d", os.path.join(RAIZ, "tools", "visor3d.py"))
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


# ─── el «portal» de mentira, en su propio sistema (mm) ───────────────────────────────────
def cuerpo(p):
    """Prisma triangular redondeado: 3 semiplanos a 9 mm del eje, 10 mm de alto, con pozo."""
    dentro = np.abs(p[..., 2]) <= 5.0
    for k in range(3):
        a = np.radians(90.0 + 120.0 * k)
        dentro &= p[..., 0] * np.cos(a) + p[..., 1] * np.sin(a) <= 9.0
    pozo = (np.hypot(p[..., 0], p[..., 1]) <= 5.0) & (p[..., 2] > 1.0)
    return dentro & ~pozo


def vastago(p):
    """Cilindro de 2 mm de radio que sale 14 mm por una de las tres caras (la de -y): llega
    más lejos que las esquinas del triángulo (a 18 mm), que es como se reconoce el vástago."""
    return (np.hypot(p[..., 0], p[..., 2]) <= 2.0) & (p[..., 1] <= -8.0) & (p[..., 1] >= -23.0)


EJE_VASTAGO = np.array([0.0, -1.0, 0.0])


def vastago_corto(p):
    """El mismo vástago, pero solo hasta 14 mm: los vértices del triángulo (18 mm) quedan más lejos."""
    return vastago(p) & (p[..., 1] >= -14.0)


def escribe_stl(ruta, escala=1.0, corto=False):
    g = np.mgrid[-24:24.01:0.5, -24:24.01:0.5, -10:10.01:0.5]
    p = np.moveaxis(g, 0, -1)
    solido = cuerpo(p) | (vastago_corto(p) if corto else vastago(p))
    v, f, _n, _val = marching_cubes(solido.astype(np.float32), 0.5, spacing=(0.5, 0.5, 0.5))
    tri = ((v + np.array([-24.0, -24.0, -10.0])) * escala)[f].astype("<f4")
    if corto:
        tri[..., :2] *= -1.0      # media vuelta en z: el vástago queda en +y, donde se puede declarar
    reg = np.zeros(len(tri), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    reg["v"] = tri
    with open(ruta, "wb") as o:
        o.write(b"\0" * 80)
        o.write(np.array([len(tri)], "<u4").tobytes())
        o.write(reg.tobytes())


# ─── el «TC»: rejilla anisótropa, el portal girado y trasladado, SIN vástago ──────────────
ESP = np.array([0.8, 0.8, 1.0])
FORMA = (90, 90, 70)
AFIN = np.diag([ESP[0], ESP[1], ESP[2], 1.0])
AFIN[:3, 3] = [-30.0, 12.0, 100.0]
R_REAL = Rotation.from_euler("zyx", [70.0, 25.0, -35.0], degrees=True).as_matrix()
T_REAL = np.array([6.0, 48.0, 135.0])
ijk = np.moveaxis(np.mgrid[0:FORMA[0], 0:FORMA[1], 0:FORMA[2]], 0, -1).astype(float)
mundo = ijk * ESP + AFIN[:3, 3]
portal = cuerpo((mundo - T_REAL) @ R_REAL)          # R_REALᵀ·(x − t): del mundo al modelo
salida_mm = T_REAL + R_REAL @ (EJE_VASTAGO * 26.0)
salida_ijk = (salida_mm - AFIN[:3, 3]) / ESP


def angulo_vastago(tri_mm):
    """Grados entre el vástago recolocado y el de verdad."""
    pts = tri_mm.reshape(-1, 3)
    c = pts.mean(0)
    d = np.linalg.norm(pts - c, axis=1)
    v = pts[d >= np.percentile(d, 99.5)].mean(0) - c
    cos = float(v @ (R_REAL @ EJE_VASTAGO) / np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


with tempfile.TemporaryDirectory() as tmp:
    stl = os.path.join(tmp, "portal.stl")
    escribe_stl(stl)

    print("\n[1] recolocación rígida, anclada a la salida del catéter")
    cascara, tri_mm, info = V.modelo_ilustrativo(portal, AFIN, ESP, stl, salida_ijk)
    check(info["escala"] == 1.0, "escala 1:1 declarada")
    check(info["rms_mm"] < 1.5, "RMS de superficie < 1,5 mm con el vástago sin pareja (sale %.2f)" % info["rms_mm"])
    check(angulo_vastago(tri_mm) < 15.0, "el vástago apunta a la salida real (%.0f°)" % angulo_vastago(tri_mm))
    check(info["orientacion"].startswith("anclada"), "la orientación se declara anclada al catéter")
    check(info["vastago_vs_cateter_grados"] is not None and info["vastago_vs_cateter_grados"] < 15.0,
          "ángulo vástago-catéter informado y pequeño")
    lado = np.linalg.norm(tri_mm[:, 0] - tri_mm[:, 1], axis=1)
    check(abs(float(np.median(lado)) / 0.5 - 1.0) < 0.5 and
          abs(float(np.ptp(tri_mm.reshape(-1, 3), axis=0).max()) - 34.0) < 6.0,
          "el modelo conserva su tamaño (no se estira ni se encoge)")
    check(cascara.shape == portal.shape and cascara.any(), "cáscara en la rejilla de la escena")
    from scipy import ndimage
    dist = ndimage.distance_transform_edt(~cascara, sampling=ESP)
    check(float(np.percentile(dist[portal & ~ndimage.binary_erosion(portal)], 95)) < 2.0,
          "la cáscara envuelve el metal (p95 < 2 mm)")
    check("sin verificar" in info["procedencia"]["origen"], "STL desconocido → procedencia «sin verificar»")
    check("NO MEDIDO" in info["aviso"], "el aviso de «no medido» viaja en la escena")

    print("\n[2] sin catéter: lo dice")
    _c, _t, info2 = V.modelo_ilustrativo(portal, AFIN, ESP, stl, None)
    check(info2["orientacion"].startswith("sin anclar") and info2["vastago_vs_cateter_grados"] is None,
          "sin salida de catéter la orientación sale como «sin anclar»")

    print("\n[2b] vástago corto: declarado se orienta bien; sin declarar, lo avisa")
    corto = os.path.join(tmp, "corto.stl")
    escribe_stl(corto, corto=True)
    tri_c = V._stl_triangulos(corto)
    es_vastago = tri_c[..., 1].min(axis=1) >= 11.0

    def angulo_declarado(tri_mm_):
        v = tri_mm_[es_vastago].reshape(-1, 3).mean(0) - tri_mm_.reshape(-1, 3).mean(0)
        cos = float(v @ (R_REAL @ EJE_VASTAGO) / np.linalg.norm(v))
        return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))

    _c, tri_d, info_d = V.modelo_ilustrativo(portal, AFIN, ESP, corto, salida_ijk, vastago={"eje": 1, "desde_mm": 11.0})
    check(angulo_declarado(tri_d) < 15.0, "vástago declarado → apunta a la salida real (%.0f°)" % angulo_declarado(tri_d))
    check(info_d["vastago"].startswith("declarado") and info_d["vastago_vs_cateter_grados"] < 15.0,
          "y la info dice que el vástago estaba declarado")
    _c, _t, info_e = V.modelo_ilustrativo(portal, AFIN, ESP, corto, salida_ijk)
    check(info_e["vastago"].startswith("ESTIMADO"), "sin declarar, la info dice que el vástago es ESTIMADO")
    try:
        V.modelo_ilustrativo(portal, AFIN, ESP, corto, salida_ijk, vastago={"eje": 1, "desde_mm": 99.0})
        check(False, "un vástago declarado que no existe debería abortar")
    except SystemExit as e:
        check("vástago declarado no existe" in str(e), "un vástago declarado que no existe → ABORTA")

    print("\n[3] un modelo más grande que el metal NO se escala para que encaje")
    grande = os.path.join(tmp, "grande.stl")
    escribe_stl(grande, escala=1.3)
    _c, tri_g, info3 = V.modelo_ilustrativo(portal, AFIN, ESP, grande, salida_ijk)
    check(info3["escala"] == 1.0 and info3["rms_mm"] > info["rms_mm"] + 0.5,
          "el desajuste de tamaño se queda en el RMS (%.2f frente a %.2f mm)" % (info3["rms_mm"], info["rms_mm"]))
    check(abs(float(np.ptp(tri_g.reshape(-1, 3), axis=0).max()) / float(np.ptp(tri_mm.reshape(-1, 3), axis=0).max()) - 1.3) < 0.15,
          "y el modelo sigue midiendo un 30 % más")

    print("\n[4] un STL ASCII aborta")
    ascii_ = os.path.join(tmp, "ascii.stl")
    with open(ascii_, "w") as o:
        o.write("solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid x\n" * 3)
    try:
        V._stl_triangulos(ascii_)
        check(False, "un STL ASCII debería abortar")
    except SystemExit as e:
        check("no es un STL binario" in str(e), "STL ASCII → ABORTA con mensaje")

print("\n[5] el visor: lo medido por defecto, el modelo solo con el botón")
h = V._RESERVORIO_HTML
check("let mod=q.get('modelo')==='1'" in h, "el modelo arranca apagado salvo que la URL pida ?modelo=1")
check("esc.vueltas_modelo" in h and "NO medido" in h, "el botón existe y dice que no es medido")
check("(mod&&hay)?esc.vueltas_modelo:esc.vueltas" in h, "sin modelo en la escena se pintan las vistas medidas")
check(all(v["origen"].startswith("dibujado a partir de fotos") for v in V.MODELO_PORT_CONOCIDO.values()),
      "la procedencia conocida declara que el modelo sale de fotos")
check(all("vastago" in v for v in V.MODELO_PORT_CONOCIDO.values()),
      "todo modelo de procedencia conocida lleva su vástago declarado")

print("\n[6] la mezcla translúcida no toca los píxeles sin modelo")
from PIL import Image  # noqa: E402
base = Image.fromarray(np.full((4, 4, 3), 100, np.uint8))
capa = np.zeros((4, 4, 3), np.uint8)
capa[0, 0] = (255, 176, 0)
m = np.asarray(V._mezcla_capa(base, Image.fromarray(capa)))
check((m[1:, 1:] == 100).all() and tuple(m[0, 0]) != (100, 100, 100), "solo cambia donde hay modelo")
check(m[0, 0, 2] > 0, "y debajo se sigue viendo lo medido (translúcido, no opaco)")

print("\n[7] los rótulos pintados en las imágenes llevan tildes")
fuente, con_tildes = V._fuente()
if not con_tildes and os.environ.get("BTP_PORTABLE"):
    print("  SKIP: esta máquina no tiene ninguna de las fuentes de rótulo")
else:
    check(con_tildes, "hay una fuente con tildes para los rótulos")
    lienzo = Image.new("RGB", (200, 20))
    d = V._lienzo(lienzo)
    check(d.font is not None and bytes(d.font.getmask("é")) != bytes(d.font.getmask("\U0010ffff")),
          "«é» no se pinta como el cuadrado de glifo ausente")
    d.text((2, 2), "catéter tráquea ámbar diagnóstico", fill=(255, 255, 255))
    check(np.asarray(lienzo).any(), "el texto con tildes se pinta")
fuente_visor = open(os.path.join(RAIZ, "tools", "visor3d.py"), encoding="utf-8").read()
check(fuente_visor.count("ImageDraw.Draw(") == 2,
      "ningún rótulo se pinta con un lienzo sin fuente (solo quedan _lienzo y una línea sin texto)")

print("\n%s" % ("TODO VERDE" if not fallos else "ROJO: %d fallo(s)" % len(fallos)))
sys.exit(1 if fallos else 0)
