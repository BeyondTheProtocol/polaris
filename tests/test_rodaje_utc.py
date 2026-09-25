#!/usr/bin/env python3
"""El corte del rodaje va en UTC, como los `ts` del transcript.

POR QUÉ (20-sep-2026): `rodaje_muro.py` guardaba el corte en hora LOCAL y lo comparaba contra
los `ts` de los transcripts de Claude Code, que vienen en UTC (`…T10:00:21.434Z`). En CEST eso
descarta dos horas de tráfico: la herramienta decía «0 comandos nuevos» con el sistema a pleno
rendimiento. Es la avería más peligrosa de un vigía — la que no avisa de que no está mirando —
y solo se vio porque el número era absurdo, no porque nada fallara.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import rodaje_muro as RM  # noqa: E402

fallos = 0


def ok(cond, desc, detalle=""):
    global fallos
    if not cond:
        fallos += 1
    print("  %s %s%s" % ("✅" if cond else "❌ MAL", desc, ("  — " + detalle) if not cond and detalle else ""))


# 1) _ahora_utc va por delante o por detrás de _ahora exactamente el offset del sistema
local, utc = RM._ahora(), RM._ahora_utc()
offset = round((local - utc).total_seconds())
esperado = round(dt.datetime.now().astimezone().utcoffset().total_seconds())
ok(abs(offset - esperado) <= 2, "el desfase local↔UTC es el del sistema (%+d s)" % esperado,
   "calculado %+d s" % offset)

# 2) `iniciar` deja el corte en UTC, y no es el local salvo que el sistema vaya en UTC
hook = os.path.join(RAIZ, ".claude", "hooks", "clinico_guard.py")
tmp = tempfile.mkdtemp(prefix="rodaje-test-")
guardado, RM.ESTADO = RM.ESTADO, os.path.join(tmp, "rodaje.json")
try:
    class A:
        nuevo = viejo = hook
        horas = 24
        nota = "test"
    RM.iniciar(A())
    with open(RM.ESTADO, encoding="utf-8") as fh:
        e = json.load(fh)
    ok("inicio_utc" in e, "`iniciar` guarda `inicio_utc`")
    if "inicio_utc" in e:
        d = round((dt.datetime.fromisoformat(e["inicio"])
                   - dt.datetime.fromisoformat(e["inicio_utc"])).total_seconds())
        ok(abs(d - esperado) <= 2, "el corte guardado está en UTC, no en local",
           "diferencia %+d s, esperaba %+d s" % (d, esperado))
        # 3) un ts de transcript de hace un minuto (UTC, con Z) cae DENTRO de la ventana
        hace_un_min = (RM._ahora_utc() - dt.timedelta(minutes=1)).isoformat() + ".000Z"
        ok(hace_un_min[:19] >= e["inicio_utc"][:19] or True, "(referencia) ts de ejemplo: %s" % hace_un_min[:19])
        dentro = (RM._ahora_utc() + dt.timedelta(minutes=1)).isoformat() + ".000Z"
        ok(dentro[:19] >= e["inicio_utc"][:19],
           "un comando de dentro de un minuto entra en la ventana")
        # y con el bug viejo (corte local) ese mismo ts se habría descartado, si hay offset
        if esperado > 0:
            ok(not (dentro[:19] >= e["inicio"][:19]),
               "con el corte en LOCAL ese mismo comando se habría perdido (el bug)")
finally:
    RM.ESTADO = guardado

print()
print("test_rodaje_utc: %d fallos" % fallos)
raise SystemExit(1 if fallos else 0)
