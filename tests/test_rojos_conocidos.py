#!/usr/bin/env python3
"""test_rojos_conocidos.py — «sin rojos NUEVOS» como semáforo, sin esconder ni uno.

Fija las condiciones de consejero-arquitectura (10-oct-26) sobre `tests/_rojos_conocidos.py`:
  1. Un rojo conocido (deuda abierta, dueño, caducidad ≤ 14 d, misma firma) no cuenta para el rc,
     pero el resumen lo nombra y NO dice «TODO EN VERDE».
  2. Es NUEVO, y la suite sigue roja: sin entrada · firma distinta (falla por otra cosa) · entrada
     caducada · caducidad > 14 días · deuda no abierta · fichero ilegible · clasificador roto.
  3. Las baterías del núcleo del muro están VETADAS sin `ok_titular` {fecha, cita}: se prueba con
     CADA prefijo del muro y con test_fuga*.
  4. El fichero REAL cumple todo lo anterior (ninguna entrada inválida, ninguna del muro sin OK).
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tests"))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
# BTP_ROJOS_MODULO / BTP_SUITE: las campañas de mutantes (tests/mutantes/rojos_conocidos*.json)
# apuntan aquí a copias MUTADAS del clasificador o de test_all.sh; sin ellas, lo de verdad.
MODULO = os.environ.get("BTP_ROJOS_MODULO") or os.path.join(RAIZ, "tests", "_rojos_conocidos.py")
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("_rojos_conocidos", MODULO)
RC = importlib.util.module_from_spec(_spec)
sys.modules["_rojos_conocidos"] = RC
_spec.loader.exec_module(RC)

SUITE = os.environ.get("BTP_SUITE") or os.path.join(RAIZ, "tests", "test_all.sh")
fallos = []


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


HOY = datetime.date.today()
iso = lambda d: d.isoformat()
VALIDA = {"bateria": "test_rojo_conocido.py", "deuda": "suite-nocturna-test_rojo_conocido", "dueno": "tecnico",
          "desde": iso(HOY), "caduca": iso(HOY + datetime.timedelta(days=10)),
          "firma": ["❌ falla algo CONOCIDO"]}

print("1) firma: normaliza lo volátil y conserva el caso que falla")
f = RC.firma_de_log("FAIL: test_x (A.B)\nAssertionError: 29 hallazgo(s) en /var/folders/ab/cd/T/rojo.X/y\n"
                    "   🔴 detalle sangrado que cambia cada día\n  ❌ otro fallo 12\nOK 3\n")
check(f == sorted(["FAIL: test_x (A.B)", "AssertionError: N hallazgo(s) en <TMP>", "❌ otro fallo N"]), "firma = %s" % f)
check(RC.firma_de_log("AssertionError: 28 hallazgo(s)") == RC.firma_de_log("AssertionError: 29 hallazgo(s)"),
      "el número de hallazgos no cambia la firma")
check(RC.firma_de_log("FAIL: test_a (X)") != RC.firma_de_log("FAIL: test_b (X)"), "otro caso que falla = otra firma")

print("2) validación de entradas")
baterias = {"test_rojo_conocido.py", "test_fuga.sh"}
deudas = {"suite-nocturna-test_rojo_conocido"}


def probs(**cambios):
    e = dict(VALIDA)
    e.update(cambios)
    return RC.problemas_de_entrada(e, HOY, deudas, baterias)


check(probs() == [], "la entrada buena no tiene problemas")
check(any("CADUCADA" in p for p in probs(caduca=iso(HOY - datetime.timedelta(days=1)), desde=iso(HOY - datetime.timedelta(days=5)))), "caducada → problema")
check(any("máximo 14" in p for p in probs(caduca=iso(HOY + datetime.timedelta(days=15)))), "caduca a 15 días → problema")
check(probs(caduca=iso(HOY + datetime.timedelta(days=14))) == [], "14 días exactos sí")
check(any("deuda" in p for p in probs(deuda="otra-que-no-existe")), "deuda no abierta → problema")
check(any("no existe en test_all.sh" in p for p in probs(bateria="test_fantasma.py")), "batería inexistente → problema")
check(any("falta" in p for p in probs(dueno="")), "sin dueño → problema")
check(any("falta" in p for p in probs(firma=[])), "sin firma → problema")

print("3) el núcleo del muro está VETADO sin el OK de {{TITULAR}}")
muro = dict(VALIDA, bateria="test_fuga.sh")
check(any("VETADA" in p for p in RC.problemas_de_entrada(muro, HOY, deudas, baterias)), "test_fuga.sh sin ok_titular → VETADA")
check(any("VETADA" in p for p in RC.problemas_de_entrada(dict(muro, ok_titular={"fecha": iso(HOY)}), HOY, deudas, baterias)),
      "ok_titular sin cita → VETADA")
check(any("VETADA" in p for p in RC.problemas_de_entrada(dict(muro, ok_titular={"cita": "dale, aprobado del todo"}), HOY, deudas, baterias)),
      "ok_titular sin fecha → VETADA")
check(RC.problemas_de_entrada(dict(muro, ok_titular={"fecha": iso(HOY), "cita": "vale, entra en la lista ({{TITULAR}})"}), HOY, deudas, baterias) == [],
      "con fecha y cita literal → admitida")
prefijos = RC.prefijos_muro()
check(len(prefijos) >= 15, "se ven los prefijos del muro (%d)" % len(prefijos))
for pref in prefijos:
    e = dict(VALIDA, bateria=pref + "_x.py")
    pr = RC.problemas_de_entrada(e, HOY, deudas, {e["bateria"]})
    check(any("VETADA" in p for p in pr), "prefijo %s vetado" % pref)

print("4) integración con test_all.sh (suite de juguete, cabecera y final REALES)")
lineas = open(SUITE, encoding="utf-8").read().splitlines()
ini = next(i for i, l in enumerate(lineas) if re.match(r"^run\s+test_", l))
fin = next(i for i, l in enumerate(lineas) if l.startswith("_vacia_cola   #"))
CAB, FIN = "\n".join(lineas[:ini]), "\n".join(lineas[fin:])
ROJO = "import sys\nprint('❌ falla algo %s')\nsys.exit(1)\n"


def juguete(entradas, bateria="test_rojo_conocido.py", mensaje="CONOCIDO", deudas_abiertas=("suite-nocturna-test_rojo_conocido",),
            fichero_raw=None, clasificador_roto=False, jobs=None):
    root = tempfile.mkdtemp(prefix="rojos_conocidos_")
    os.makedirs(os.path.join(root, "tests"))
    estado = os.path.join(root, "estado")
    os.makedirs(estado)
    json.dump({k: {"estado": "abierto"} for k in deudas_abiertas}, open(os.path.join(estado, "deuda.json"), "w"))
    open(os.path.join(root, "tests", bateria), "w").write(ROJO % mensaje)
    open(os.path.join(root, "tests", "test_verde.py"), "w").write("print('ok')\n")
    shutil.copy(MODULO, os.path.join(root, "tests", "_rojos_conocidos.py"))
    if clasificador_roto:
        open(os.path.join(root, "tests", "_rojos_conocidos.py"), "w").write("esto no es python (\n")
    fich = os.path.join(root, "tests", "rojos_conocidos.json")
    if fichero_raw is not None:
        open(fich, "w").write(fichero_raw)
    else:
        json.dump({"version": 1, "entradas": entradas}, open(fich, "w"))
    cab = re.sub(r'LARGAS="[^"]*"', 'LARGAS=""', CAB, count=1)
    toy = "runpy %s\nrunpy test_verde.py" % bateria
    open(os.path.join(root, "tests", "test_all.sh"), "w").write(cab + "\n" + toy + "\n" + FIN + "\n")
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_ROJO_DIR", "BTP_JOBS", "BTP_PORTABLE")}
    env.update(BTP_ROJO_DIR=os.path.join(root, "rojos"), BTP_STATE_DIR=estado, BTP_ROJOS_CONOCIDOS=fich)
    if jobs:
        env["BTP_JOBS"] = str(jobs)
    r = subprocess.run(["bash", os.path.join(root, "tests", "test_all.sh")], capture_output=True, text=True,
                       env=env, timeout=120, stdin=subprocess.DEVNULL)
    return r


r = juguete([VALIDA])
check(r.returncode == 0, "conocido válido → rc 0 (rc=%d)" % r.returncode)
check("CONOCIDO: test_rojo_conocido.py" in r.stdout, "y se NOMBRA igual")
check("SIN ROJOS NUEVOS" in r.stdout and "✅✅ TODO EN VERDE" not in r.stdout, "dice «SIN ROJOS NUEVOS» y no el «✅✅ TODO EN VERDE»")
r = juguete([VALIDA], jobs=4)
check(r.returncode == 0 and "SIN ROJOS NUEVOS" in r.stdout, "lo mismo en paralelo (rc=%d)" % r.returncode)
r = juguete([])
check(r.returncode == 1 and "NUEVO: test_rojo_conocido.py" in r.stdout, "sin entrada → rojo NUEVO, rc 1")
r = juguete([VALIDA], mensaje="OTRA COSA DISTINTA")
check(r.returncode == 1 and "OTRA cosa" in r.stdout, "misma batería, falla por otra cosa → NUEVO")
r = juguete([dict(VALIDA, desde=iso(HOY - datetime.timedelta(days=20)), caduca=iso(HOY - datetime.timedelta(days=6)))])
check(r.returncode == 1 and "CADUCADA" in r.stdout, "entrada caducada → NUEVO")
r = juguete([dict(VALIDA, caduca=iso(HOY + datetime.timedelta(days=40)))])
check(r.returncode == 1 and "máximo 14" in r.stdout, "caducidad a 40 días → NUEVO")
r = juguete([VALIDA], deudas_abiertas=())
check(r.returncode == 1 and "no está abierta" in r.stdout, "sin deuda abierta → NUEVO")
r = juguete([VALIDA], fichero_raw="{esto no es json")
check(r.returncode == 1 and "NUEVO" in r.stdout and "ilegible" in r.stdout, "fichero ilegible → todo NUEVO y lo dice")
r = juguete([VALIDA], clasificador_roto=True)
check(r.returncode == 1 and "SIN ROJOS NUEVOS" not in r.stdout, "clasificador roto → todo sigue rojo")
muro_e = dict(VALIDA, bateria="test_fuga_toy.py")
r = juguete([muro_e], bateria="test_fuga_toy.py")
check(r.returncode == 1 and "VETADA" in r.stdout, "test_fuga* sin el OK de {{TITULAR}} → VETADA, rc 1")
r = juguete([dict(muro_e, ok_titular={"fecha": iso(HOY), "cita": "vale, entra en la lista ({{TITULAR}})"})], bateria="test_fuga_toy.py")
check(r.returncode == 0 and "CONOCIDO: test_fuga_toy.py" in r.stdout, "con su OK (fecha y cita) → conocido")

print("5) el fichero REAL")
ent, err = RC.carga_entradas(os.path.join(RAIZ, "tests", "rojos_conocidos.json"))
check(err is None, "se lee bien (%s)" % err)
real_bat = RC.baterias_de_la_suite()
check(len(real_bat) > 300, "se ven las baterías reales (%d): el test no pasa en vacío" % len(real_bat))
for e in ent:
    # contra el libro REAL de deuda (casa base)
    pr = RC.problemas_de_entrada(e, HOY, RC.deudas_abiertas(), real_bat)
    check(not pr, "entrada %s válida (%s)" % (e.get("bateria") if isinstance(e, dict) else e, pr))
    if isinstance(e, dict) and RC.es_muro(e.get("bateria", "")):
        check(isinstance(e.get("ok_titular"), dict) and e["ok_titular"].get("cita"), "%s (muro) lleva el OK de {{TITULAR}}" % e["bateria"])
fijos = open(os.path.join(RAIZ, "tools", "tests_afectados.py"), encoding="utf-8").read()
check('"tests/rojos_conocidos.json"' in fijos, "editar el fichero exige la suite COMPLETA (está en los fijos de la puerta)")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
