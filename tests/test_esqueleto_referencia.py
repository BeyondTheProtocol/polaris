#!/usr/bin/env python3
"""El esqueleto de referencia (tools/esqueleto_referencia.py): los frenos que no se ven mirando.

Lo que se prueba es lo que rompería la web en silencio, no el render bonito:
  · el cruce de nombres BodyParts3D → TotalSegmentator, que es por donde cada foco encuentra
    su hueso (un «left third rib» mal traducido deja un foco sin sitio o en el lado contrario);
  · el filtro de piezas: que entren las piezas que CIERRAN la figura (cartílago costal, tibia,
    huesos del pie) y no se cuelen arterias o tractos con nombre de hueso («ulnar artery»);
  · `frenos`, que tiene que ABORTAR con la columna desordenada o la vista en espejo.
Solo biblioteca estándar: corre con el python del sistema, como toda la batería.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import esqueleto_referencia as er  # noqa: E402

fallos = 0


def ok(cond, msg):
    global fallos
    print("  %s %s" % ("✅" if cond else "❌", msg))
    if not cond:
        fallos += 1


# 1 · nombres
casos = {
    "atlas": "vertebrae_C1", "axis": "vertebrae_C2",
    "third cervical vertebra": "vertebrae_C3", "eleventh thoracic vertebra": "vertebrae_T11",
    "fifth lumbar vertebra": "vertebrae_L5", "sacrum": "sacrum",
    "right third rib": "rib_right_3", "left twelfth rib": "rib_left_12",
    "right hip bone": "hip_right", "left femur": "femur_left", "right scapula": "scapula_right",
    "right clavicle": "clavicula_right", "manubrium": "sternum", "body of sternum": "sternum",
    "right tibia": None, "right third costal cartilage": None,
}
for bp, ts in casos.items():
    ok(er.nombre_totalseg(bp) == ts, "%s → %s" % (bp, ts))

# 2 · filtro de piezas
for n in ["right tibia", "left fibula", "left patella", "right calcaneus", "left talus",
          "left ulna", "right radius", "distal phalanx of left big toe",
          "left first metatarsal bone", "right fifth costal cartilage", "xiphoid process"]:
    ok(er.es_pieza(n), "entra: %s" % n)
for n in ["left ulnar artery", "right iliotibial tract", "right anterior tibial artery",
          "intervertebral disk of axis", "left sclera", "patellar part of left knee"]:
    ok(not er.es_pieza(n), "fuera: %s" % n)


# 3 · frenos
def huesos(espejo=False, desorden=False):
    h = {n: {"u": 0.5, "v": 0.1 + 0.01 * i} for i, n in enumerate(er.ORDEN_COLUMNA)}
    h["femur_left"] = {"u": 0.4 if espejo else 0.6, "v": 0.5}
    h["femur_right"] = {"u": 0.6 if espejo else 0.4, "v": 0.5}
    if desorden:
        h["vertebrae_T11"]["v"], h["vertebrae_T2"]["v"] = h["vertebrae_T2"]["v"], h["vertebrae_T11"]["v"]
    return h


def aborta(h):
    try:
        er.frenos(h)
    except SystemExit:
        return True
    return False


ok(not aborta(huesos()), "un esqueleto bien puesto pasa los frenos")
ok(aborta(huesos(espejo=True)), "la vista en espejo ABORTA")
ok(aborta(huesos(desorden=True)), "la columna desordenada ABORTA")
sin_t5 = huesos()
del sin_t5["vertebrae_T5"]
ok(aborta(sin_t5), "una vértebra que falta ABORTA")

print("\n%s test_esqueleto_referencia: %d fallo(s)" % ("❌" if fallos else "✅", fallos))
sys.exit(1 if fallos else 0)
