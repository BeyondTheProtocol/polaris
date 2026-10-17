#!/usr/bin/env python3
"""test_ccd_huerfanos.py — sesiones ccd-cli de versiones viejas (bucles_colgados.run_ccd) y aviso
de pesados juntos (healthcheck._check_recursos).

POR QUÉ (16-oct-26): tras actualizarse la app de Claude, 17 sesiones remotas de la versión
anterior seguían vivas de 2 a 6 días; con CellBender, la barrida de laminillas y Chrome a la vez
el swap llegó a 11,5 de 12 GB y las sesiones nuevas no arrancaban.

`ps` va INYECTADO (no depende de qué corra en la máquina). El cierre es REAL: el pid de la
sesión vieja falsa es un `sleep` de verdad que el test lanza, y se comprueba que muere. Los
demás pids del ps inyectado son inventados y altos; ninguno debe recibir señal.

Si tocas la lógica de detección o de cierre, añade el caso aquí.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="ccd_huerfanos_test_")
os.environ["BTP_STATE_DIR"] = _TMP          # antes de importar: nunca el estado de casa base
os.environ.pop("BTP_CCD_SOLO_AVISO", None)
os.environ.pop("BTP_BUCLE_SOLO_AVISO", None)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bucles_colgados as bc  # noqa: E402
import healthcheck as hc  # noqa: E402

_pass = _fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % nombre)


UID = os.getuid()
HOME = "/Users/x"
SRV = "%s/.claude/remote/srv/c8e2f083/server --serve --socket x" % HOME


def ccd(ver):
    return "%s/.claude/remote/ccd-cli/%s-597e87956861 --output-format stream-json" % (HOME, ver)


def linea(pid, ppid, etime, cmd, pcpu="0.0", cputime="9:39.29", uid=UID):
    return "%d %d %s %d %s %s %s" % (pid, ppid, etime, uid, pcpu, cputime, cmd)


def ps(*lineas):
    return lambda: "\n".join(lineas) + "\n"


SRV_L = linea(900001, 1, "06-08:28:57", SRV)
NUEVA = linea(900002, 900001, "09:18:16", ccd("2.1.299"), pcpu="0.1", cputime="4:52.51")


def correr(*lineas, prev_cpu=None, umbral=86400, dry_run=False, edad=1800):
    """run_ccd con un estado previo sembrado `edad` segundos antes."""
    st = os.path.join(_TMP, "ccd_%d.json" % time.time_ns())
    if prev_cpu is not None:
        with open(st, "w") as fh:
            json.dump({"cpu": prev_cpu, "ts": time.time() - edad}, fh)
    return bc.run_ccd(dry_run=dry_run, umbral_seg=umbral, ps_runner=ps(*lineas), state_path=st)


# ── Parseos ────────────────────────────────────────────────────────────────────────────────
check("version numérica: 2.1.1000 > 2.1.299", bc._version_tupla("2.1.1000") > bc._version_tupla("2.1.299"))
check("cputime m:ss.cc", abs(bc._cputime_a_seg("9:39.29") - 579.29) < 0.01)
check("cputime h:mm:ss", bc._cputime_a_seg("1:02:03") == 3723)
check("cputime ilegible → None", bc._cputime_a_seg("abc") is None)

# ── Cierre REAL de una sesión vieja, quieta y sin trabajo debajo ──────────────────────────
victima = subprocess.Popen(["/bin/sleep", "300"])
try:
    vieja = linea(victima.pid, 900001, "01-04:46:26", ccd("2.1.295"))
    al, inf = correr(SRV_L, NUEVA, vieja, prev_cpu={str(victima.pid): 579.29})
    victima.wait(timeout=5)
    check("cierre real: el proceso de la versión vieja muere", victima.poll() is not None)
    check("cierre real: 1 candidato", inf.get("candidatos") == 1)
    check("cierre real: alerta ccd_huerfano_parado",
          any(a[0] == "ccd_huerfano_parado" and "2.1.295" in a[1] and "2.1.299" in a[1] for a in al))
    log = open(bc.LOG).read() if os.path.exists(bc.LOG) else ""
    check("cierre real: queda en el registro auditable",
          '"ccd_cli_huerfano"' in log and str(victima.pid) in log)
finally:
    if victima.poll() is None:
        victima.kill()

# ── Lo que NUNCA se toca ──────────────────────────────────────────────────────────────────
vieja = linea(900003, 900001, "01-04:46:26", ccd("2.1.295"))
PREV = {"900003": 579.29, "900002": 292.51}

al, inf = correr(SRV_L, NUEVA, vieja, prev_cpu=PREV, dry_run=True)
check("dry-run: detecta y no mata", inf.get("candidatos") == 1
      and inf["resultado"][0]["parado"] is False
      and any(a[0] == "ccd_huerfano_detectado" for a in al))

al, inf = correr(SRV_L, NUEVA, prev_cpu=PREV)
check("la versión actual nunca es candidata", inf.get("candidatos") == 0 and not al)

solo_vieja = linea(900004, 900001, "05-00:00:00", ccd("2.1.295"))
al, inf = correr(SRV_L, solo_vieja, prev_cpu={"900004": 579.29}, dry_run=True)
check("si solo corre una versión, no hay candidatos", inf.get("candidatos") == 0)

nueva_mil = linea(900005, 900001, "01:00:00", ccd("2.1.1000"))
v299 = linea(900006, 900001, "02-00:00:00", ccd("2.1.299"), cputime="4:52.51")
al, inf = correr(SRV_L, nueva_mil, v299, prev_cpu={"900006": 292.51}, dry_run=True)
check("orden numérico: con 2.1.1000 corriendo, la 2.1.299 es la vieja",
      inf.get("actual") == "2.1.1000" and inf.get("candidatos") == 1)

joven = linea(900007, 900001, "23:59:00", ccd("2.1.295"))
al, inf = correr(SRV_L, NUEVA, joven, prev_cpu={"900007": 579.29}, dry_run=True)
check("menos de 24 h: no se toca", inf.get("candidatos") == 0)

al, inf = correr(SRV_L, NUEVA, vieja, prev_cpu=None, dry_run=True)
check("sin vuelta anterior (una sola muestra): no se toca", inf.get("candidatos") == 0)

al, inf = correr(SRV_L, NUEVA, vieja, prev_cpu={"900003": 570.0}, dry_run=True)
check("CPU acumulada que se movió entre vueltas: no se toca", inf.get("candidatos") == 0)

al, inf = correr(SRV_L, NUEVA, vieja, prev_cpu=PREV, dry_run=True, edad=60)
check("muestra previa de hace 1 min (pasada manual): no compara", inf.get("candidatos") == 0)

otro_padre = linea(900008, 1, "03-00:00:00", ccd("2.1.295"))
al, inf = correr(SRV_L, NUEVA, otro_padre, prev_cpu={"900008": 579.29}, dry_run=True)
check("padre que no es el servidor remoto: no se toca", inf.get("candidatos") == 0)

ajeno = linea(900009, 900001, "03-00:00:00", ccd("2.1.295"), uid=UID + 1)
al, inf = correr(SRV_L, NUEVA, ajeno, prev_cpu={"900009": 579.29}, dry_run=True)
check("proceso de otro usuario: no se toca", inf.get("candidatos") == 0)

local = linea(900010, 900001, "03-00:00:00",
              "/Users/x/Library/Application Support/Claude/claude-code/2.1.295/9d64/claude.app/"
              "Contents/MacOS/claude --output-format stream-json")
al, inf = correr(SRV_L, NUEVA, local, prev_cpu={"900010": 579.29}, dry_run=True)
check("el claude local de la app (no ccd-cli) queda fuera", inf.get("candidatos") == 0)

ruta_en_arg = linea(900011, 900001, "03-00:00:00",
                    "/bin/zsh -c echo /Users/x/.claude/remote/ccd-cli/2.1.295-597e87956861")
al, inf = correr(SRV_L, NUEVA, ruta_en_arg, prev_cpu={"900011": 579.29}, dry_run=True)
check("ruta ccd-cli como argumento (no ejecutable): no casa", inf.get("candidatos") == 0)

# Trabajo debajo: solo aviso.
clinico = linea(900012, 900003, "02:00:00",
                "/Users/x/.claude-science/conda/envs/cellbender/bin/python cellbender remove-background",
                pcpu="0.0")
al, inf = correr(SRV_L, NUEVA, vieja, clinico, prev_cpu=PREV)
check("análisis clínico debajo: no se cierra, se avisa",
      inf.get("candidatos") == 0 and inf.get("protegidos") == 1
      and any(a[0] == "ccd_viejo_protegido" and "clínico" in a[1] for a in al))

shell = linea(900013, 900003, "02:00:00", "/bin/zsh -c make", pcpu="0.0")
nieto = linea(900014, 900013, "02:00:00", "/usr/bin/cc foo.c", pcpu="95.0")
al, inf = correr(SRV_L, NUEVA, vieja, shell, nieto, prev_cpu=PREV)
check("nieto con CPU: no se cierra, se avisa", inf.get("candidatos") == 0
      and inf.get("protegidos") == 1)

al, inf = bc.run_ccd(dry_run=False, ps_runner=lambda: (_ for _ in ()).throw(OSError("ps roto")),
                     state_path=os.path.join(_TMP, "roto.json"))
check("ps roto: fail-safe, no revienta", inf.get("ps_error") is True and al == [])

al, inf = bc.run_ccd(dry_run=False, ps_runner=ps("basura sin columnas"),
                     state_path=os.path.join(_TMP, "basura.json"))
check("ps ilegible: fail-safe", inf.get("ps_error") is True and al == [])

# Estado: una pasada guarda la muestra y la siguiente, pasados 20+ min, puede comparar.
st = os.path.join(_TMP, "dos_vueltas.json")
bc.run_ccd(dry_run=True, umbral_seg=86400, ps_runner=ps(SRV_L, NUEVA, vieja), state_path=st)
guardado = json.load(open(st))
check("primera vuelta guarda la CPU acumulada", guardado["cpu"].get("900003") == 579.29)
_, inf = bc.run_ccd(dry_run=True, umbral_seg=86400, ps_runner=ps(SRV_L, NUEVA, vieja),
                    state_path=st, ahora=guardado["ts"] + 1800)
check("segunda vuelta a los 30 min: candidata", inf.get("candidatos") == 1)

# ── healthcheck: pesados juntos ───────────────────────────────────────────────────────────
_orig = hc._recursos_ahora
_orig_ram = hc._ram_fisica_gb
_orig_et = hc._etiqueta_proceso
try:
    hc._ram_fisica_gb = lambda: 16.0
    hc._etiqueta_proceso = lambda pid, n: n
    estado = os.path.join(hc.HC, "recursos.json")

    def ronda(swap, gordos):
        hc._recursos_ahora = lambda: (swap, 30.0, gordos)
        return hc._check_recursos()

    if os.path.exists(estado):
        os.remove(estado)
    PES = [(5.9, 9223, "cellbender"), (5.4, 97209, "llama-server"), (2.3, 94459, "Python")]
    al1, _ = ronda(8.3, PES)
    al2, inf2 = ronda(8.3, PES)
    check("pesados juntos: la 1ª vuelta calla", al1 == [])
    check("pesados juntos: la 2ª vuelta avisa y nombra a los dos",
          len(al2) == 1 and "cellbender" in al2[0] and "llama-server" in al2[0]
          and "Python" not in al2[0])
    al3, inf3 = ronda(8.3, PES)
    check("pesados juntos: throttle de 1 h", al3 == [])

    os.remove(estado)
    ronda(2.0, PES)
    al, _ = ronda(2.0, PES)
    check("pesados juntos con poco swap: uso sano, calla", al == [])

    os.remove(estado)
    UNO = [(5.9, 9223, "cellbender"), (2.3, 94459, "Python")]
    ronda(8.3, UNO)
    al, _ = ronda(8.3, UNO)
    check("un solo pesado: no es «juntos»", al == [])
finally:
    hc._recursos_ahora = _orig
    hc._ram_fisica_gb = _orig_ram
    hc._etiqueta_proceso = _orig_et

check("_etiqueta_proceso de un pid inexistente devuelve el nombre de top",
      hc._etiqueta_proceso(999999, "Python") == "Python")

print("test_ccd_huerfanos: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
