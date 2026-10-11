#!/usr/bin/env python3
"""test_suite_nocturna.py — la suite de noche sobre casa base hace lo que dice, y no calla.

Se monta un casa base de JUGUETE (un `tests/test_all.sh` falso que escribe los rojos que se le
piden, baterías falsas para la repetición en solitario, deuda y estado aislados) y se corre
`tools/suite_nocturna.py` de verdad como subproceso. Se fija:
  1. todo verde → rc 0, estado «ok», latido, una línea de historial, ni deuda ni aviso;
  2. rojo NUEVO no-muro → deuda `suite-nocturna-<bat>`, estado «rojo_nuevo», SIN aviso;
  3. rojo del MURO → aviso a {{TITULAR}} esa misma noche (no espera a escalar) y deuda marcada muro;
  4. rojo que pasa corrido solo → FLAKY: deuda `…-flaky-…` (y aviso si es del muro);
  5. un rojo CONOCIDO (con su entrada válida) no avisa ni cuenta;
  6. HALT → no corre, lo apunta; worktree → se niega; runner colgado → estado «fallo» + aviso;
  7. `--sembrar` → el aviso sale de punta a punta, sin deuda, y queda esperando la confirmación;
  8. `condiciones`: A (puerta cerrada), B (lista de rojos con vetos), C1 (3 noches SEGUIDAS con
     latido: un hueco, dos noches o un último latido viejo NO valen) y C2 (sembrado recibido Y
     confirmado) — sale 0 solo si las cuatro.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BTP_SUITE_NOCTURNA_TOOL: la campaña de mutantes (tests/mutantes/suite_nocturna.json) apunta aquí a una
# copia MUTADA de tools/suite_nocturna.py; sin la variable, la de verdad.
TOOL = os.environ.get("BTP_SUITE_NOCTURNA_TOOL") or os.path.join(RAIZ, "tools", "suite_nocturna.py")
fallos = []


def hash_codigo(codigo, sal):
    return hashlib.pbkdf2_hmac("sha256", str(codigo).encode(), bytes.fromhex(sal), 300000).hex()


def check(cond, desc):
    print("  %s %s" % ("✅" if cond else "❌", desc))
    if not cond:
        fallos.append(desc)


TOY_SUITE = r'''#!/bin/bash
# casa base de juguete: escribe los rojos de cfg/rojos.txt (nombre|línea de fallo) y sale con cfg/rc
ROJOS="${BTP_ROJO_DIR:?}"; mkdir -p "$ROJOS"
echo corrio > "$(dirname "$0")/../cfg/corrio.marca"
[ -f "$(dirname "$0")/../cfg/dormir" ] && sleep "$(cat "$(dirname "$0")/../cfg/dormir")"
if [ -f "$(dirname "$0")/../cfg/rojos.txt" ]; then
  while IFS='|' read -r n l; do [ -n "$n" ] && printf '%b\n' "$l" > "$ROJOS/rojo-$n.log"; done < "$(dirname "$0")/../cfg/rojos.txt"
fi
C="$(dirname "$0")/../cfg"
[ -f "$C/halt_al_final" ] && { touch "$C/../.HALT"; sleep 30; }     # un HALT que aparece con la suite en marcha
[ -f "$C/halt_y_sale" ] && touch "$C/../.HALT"                      # un HALT puesto justo al acabar
exit "$(cat "$(dirname "$0")/../cfg/rc" 2>/dev/null || echo 0)"
'''


def juguete(rojos=(), rc=0, ssobreviven=(), dormir=None, halt=False, en_worktree=False, conocidos=None, deudas=()):
    base = tempfile.mkdtemp(prefix="nocturna_")
    root = os.path.join(base, ".claude", "worktrees", "x", "casa") if en_worktree else os.path.join(base, "casa")
    for d in ("tests", "tools", "cfg", "estado"):
        os.makedirs(os.path.join(root, d))
    open(os.path.join(root, "tests", "test_all.sh"), "w").write(TOY_SUITE + "# test_rojos_conocidos.py\n")
    os.chmod(os.path.join(root, "tests", "test_all.sh"), 0o755)
    for n in ("test_roja.py", "test_fuga_toy.py"):
        open(os.path.join(root, "tests", n), "w").write("import sys\nsys.exit(1)\n")
    for n in ("test_flaky.py", "test_fuga_flaky.py", "test_conocida.py"):
        open(os.path.join(root, "tests", n), "w").write("import sys\nsys.exit(0 if %r else 1)\n" % (n != "test_conocida.py"))
    shutil.copy(os.path.join(RAIZ, "tests", "_rojos_conocidos.py"), os.path.join(root, "tests", "_rojos_conocidos.py"))
    json.dump({"version": 1, "entradas": conocidos or []}, open(os.path.join(root, "tests", "rojos_conocidos.json"), "w"))
    open(os.path.join(root, "cfg", "rojos.txt"), "w").write("".join("%s|%s\n" % r for r in rojos))
    open(os.path.join(root, "cfg", "rc"), "w").write(str(rc))
    if dormir:
        open(os.path.join(root, "cfg", "dormir"), "w").write(str(dormir))
    if halt:
        open(os.path.join(root, ".HALT"), "w").write("x")
    json.dump({k: {"estado": "abierto"} for k in deudas}, open(os.path.join(root, "estado", "deuda.json"), "w"))
    return root


def corre(root, *args, hoy="2026-10-12", ahora=None, extra=None, tope=None):
    alerta = os.path.join(root, "alertas.jsonl")
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "BTP_JOBS", "BTP_ROJO_DIR", "BTP_REPO", "BTP_STATE_DIR")}
    env.update(BTP_REPO=root, BTP_STATE_DIR=os.path.join(root, "estado"), BTP_SUITE_NOCTURNA_HOY=hoy,
               BTP_SUITE_NOCTURNA_AVISO_A=alerta, BTP_SUITE_NOCTURNA_PERMITE_WORKTREE="",
               BTP_TRANSCRIPTS_DIR=os.path.join(root, "transcripts"),
               # el HALT del juguete, no el ~/.btp.HALT real: con un código rojo puesto este test salía rojo
               BTP_HALT_FILES=os.path.join(root, ".HALT") + ":" + os.path.join(root, "ext.HALT"))
    if ahora:
        env["BTP_SUITE_NOCTURNA_AHORA"] = ahora
    if tope:
        env["BTP_SUITE_NOCTURNA_TOPE_S"] = str(tope)
    env.update(extra or {})
    r = subprocess.run([sys.executable, TOOL] + list(args), capture_output=True, text=True, env=env, timeout=120,
                       stdin=subprocess.DEVNULL, cwd=root)
    al = [json.loads(l) for l in open(alerta)] if os.path.exists(alerta) else []
    return r, al


def deuda(root):
    try:
        return json.load(open(os.path.join(root, "estado", "deuda.json")))
    except Exception:
        return {}


def hist(root):
    p = os.path.join(root, "estado", "suite_nocturna", "historial.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def latido(root):
    try:
        return json.load(open(os.path.join(root, "estado", "heartbeat", "suite-nocturna.json")))
    except Exception:
        return None


print("1) todo verde")
root = juguete()
r, al = corre(root)
check(r.returncode == 0 and latido(root) and latido(root)["estado"] == "ok", "rc 0 y latido «ok» (rc=%d)" % r.returncode)
check(len(hist(root)) == 1 and hist(root)[0]["corrio"], "una línea de historial")
check(not deuda(root) and not al, "ni deuda ni aviso")

print("2) rojo nuevo, no es del muro")
root = juguete(rojos=[("test_roja.py", "❌ falla")], rc=1)
r, al = corre(root)
check(r.returncode == 1 and latido(root)["estado"] == "rojo_nuevo", "estado «rojo_nuevo»")
check("suite-nocturna-test_roja.py" in deuda(root), "deuda suite-nocturna-test_roja.py abierta (%s)" % list(deuda(root)))
check(not al, "no avisa (escala por repetición)")

print("3) rojo del MURO: avisa la primera noche")
root = juguete(rojos=[("test_fuga_toy.py", "❌ el muro")], rc=1)
r, al = corre(root)
check(len(al) == 1 and al[0]["muro"] and "test_fuga_toy.py" in al[0]["texto"], "aviso a {{TITULAR}} con el nombre (%s)" % [a["texto"][:60] for a in al])
check(latido(root)["estado"] == "rojo_muro", "estado «rojo_muro»")
d = deuda(root).get("suite-nocturna-test_fuga_toy.py", {})
check(d.get("muro") is True, "deuda marcada muro")

root = juguete(rojos=[("test_fuga_toy.py", "❌ el muro")], rc=1)
r, al = corre(root, extra={"BTP_SUITE_NOCTURNA_AVISO_ESTADO": "retenido"})
h_ = hist(root)[0]
check(h_["avisado_muro"] is False and h_["entrega_muro"] == "retenido", "el historial guarda el estado REAL del aviso del muro (retenido por el silencio nocturno), no «avisado»")
print("4) flaky: roja en la suite, verde sola")
root = juguete(rojos=[("test_flaky.py", "❌ intermitente")], rc=1)
r, al = corre(root)
check("suite-nocturna-flaky-test_flaky.py" in deuda(root) and "suite-nocturna-test_flaky.py" not in deuda(root), "deuda FLAKY, no la de rojo persistente")
check(not al, "no avisa si no es del muro")
root = juguete(rojos=[("test_fuga_flaky.py", "❌ intermitente del muro")], rc=1)
r, al = corre(root)
check(len(al) == 1 and "intermitente" in al[0]["texto"], "un flaky del MURO sí avisa")

print("5) rojo conocido: ni deuda nueva ni aviso")
hoy = datetime.date(2026, 10, 12)
ent = {"bateria": "test_conocida.py", "deuda": "deuda-previa", "dueno": "tecnico", "desde": hoy.isoformat(),
       "caduca": (hoy + datetime.timedelta(days=10)).isoformat(), "firma": ["❌ conocido"]}
root = juguete(rojos=[("test_conocida.py", "❌ conocido")], rc=0, conocidos=[ent], deudas=("deuda-previa",))
open(os.path.join(root, "tests", "test_all.sh"), "a").write("# test_conocida.py\n")
r, al = corre(root)
h = hist(root)[0]
check(h["conocidos"] == ["test_conocida.py"] and h["estado"] == "ok" and not al and r.returncode == 0, "conocido: estado ok, sin aviso (%s)" % h["conocidos"])
check(list(deuda(root)) == ["deuda-previa"], "y no abre deuda suya")

print("6) HALT, worktree y runner colgado")
root = juguete(halt=True)
r, al = corre(root)
check(r.returncode == 0 and not os.path.exists(os.path.join(root, "cfg", "corrio.marca")) and not hist(root)[0]["corrio"], "con HALT no corre y lo apunta")
root = juguete()
home_halt = tempfile.mkdtemp(prefix="homehalt_")
open(os.path.join(home_halt, ".btp.HALT"), "w").write("x")
r, al = corre(root, extra={"HOME": home_halt})
check(r.returncode == 0 and os.path.exists(os.path.join(root, "cfg", "corrio.marca")) and hist(root)[0]["corrio"],
      "BTP_HALT_FILES aísla: un ~/.btp.HALT real (aquí, en un HOME falso) no para la pasada del juguete (11-oct: el test salía rojo con el código rojo puesto)")
root = juguete(en_worktree=True)
r, al = corre(root)
check(r.returncode == 2 and not os.path.exists(os.path.join(root, "cfg", "corrio.marca")), "desde un worktree se niega (rc 2) y no corre nada")
root = juguete(dormir=20)
r, al = corre(root, tope=2)
check(r.returncode == 1 and latido(root)["estado"] == "fallo" and len(al) == 1 and "NO pudo completarse" in al[0]["texto"],
      "runner colgado → estado «fallo» y AVISO (una nocturna muda es peor que ninguna)")

print("7) --sembrar, --solo-aviso y la confirmación (solo vale un prompt HUMANO)")
ahora_utc = datetime.datetime.utcnow()
iso_ = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def escribe_transcript(root, entradas, nombre="sesion.jsonl"):
    d = os.path.join(root, "transcripts", "proyecto")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, nombre), "a") as f:
        for e in entradas:
            f.write(json.dumps(e) + "\n")


def humano(texto, dt, **kw):
    e = {"type": "user", "origin": {"kind": "human"}, "isSidechain": False, "message": {"content": texto}, "timestamp": iso_(dt)}
    e.update(kw)
    return e


root = juguete()
r, al = corre(root, "--sembrar", "--solo-aviso")
sem_p = os.path.join(root, "estado", "suite_nocturna", "sembrado.json")
sem = json.load(open(sem_p))
check(r.returncode == 0 and not os.path.exists(os.path.join(root, "cfg", "corrio.marca")), "--solo-aviso NO corre la suite")
cod = re.search(r"código: ([0-9a-f]+)", al[0]["texto"]).group(1) if al else ""
check(len(al) == 1 and al[0]["sembrado"] and "SEMBRADO A PROPÓSITO" in al[0]["texto"] and cod,
      "el aviso sembrado sale y lleva el código (%s)" % cod)
check(sem["avisado"] is True and sem["entrega"] == "entregado" and sem["confirmado"] is None and not deuda(root) and not hist(root),
      "queda esperando; ni deuda ni noche en el historial")
check("nonce" not in sem and cod not in open(sem_p).read() and sem.get("nonce_hash") == hash_codigo(cod, sem["sal"]),
      "el código NO está en claro en sembrado.json (solo su hash salado): un agente no lo puede leer del estado")
r1, _ = corre(root, "confirmar-sembrado", "000000")
check(r1.returncode == 2, "un código que no es el del aviso no vale")
r2, _ = corre(root, "confirmar-sembrado", cod)
check(r2.returncode == 2 and "HUMANO" in r2.stdout and json.load(open(sem_p))["confirmado"] is None,
      "con el código bueno pero SIN prompt humano que lo contenga, un agente NO puede confirmar")
despues = ahora_utc + datetime.timedelta(minutes=5)
escribe_transcript(root, [
    humano("el código es %s" % cod, despues, origin={"kind": "peer"}),                       # otro agente
    humano("el código es %s" % cod, despues, isSidechain=True),                              # subagente
    {"type": "assistant", "message": {"content": "el código es %s" % cod}, "timestamp": iso_(despues)},   # el propio modelo
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "código %s" % cod}]}, "timestamp": iso_(despues)},
    humano("antes del aviso: %s" % cod, ahora_utc - datetime.timedelta(hours=3)),            # anterior al sembrado
])
r3, _ = corre(root, "confirmar-sembrado", cod)
check(r3.returncode == 2, "ni un peer, ni un subagente, ni el modelo, ni un tool_result, ni un prompt anterior al aviso lo confirman")
escribe_transcript(root, [humano("sí, me ha llegado, el código es %s" % cod, despues)])
r4, _ = corre(root, "confirmar-sembrado", cod)
conf = json.load(open(sem_p))["confirmado"]
check(r4.returncode == 0 and conf and conf["transcript"] == "sesion.jsonl", "con un prompt HUMANO posterior que contiene el código, confirma")
r5, _ = corre(root, "confirmar-sembrado", "")
check(r5.returncode == 2, "una confirmación vacía no vale")
escribe_transcript(root, [humano("un texto cualquiera con deadbeef dentro", despues)])
r6 = corre(juguete(), "confirmar-sembrado", "deadbeef")[0]
r6b, _ = corre(root, "confirmar-sembrado", "deadbeef")
check(r6b.returncode == 2, "un código que SÍ aparece en un prompt humano pero NO es el del aviso no confirma (hash)")
root = juguete()
r, al = corre(root, "--sembrar")
check(os.path.exists(os.path.join(root, "cfg", "corrio.marca")) and len(al) == 1 and al[0]["sembrado"], "--sembrar a secas sí corre la pasada y siembra")
# cada siembra genera un código NUEVO (un sembrado visto o filtrado queda invalidado)
root = juguete()
r, al1 = corre(root, "--sembrar", "--solo-aviso")
c1 = json.load(open(os.path.join(root, "estado", "suite_nocturna", "sembrado.json")))
r, al2 = corre(root, "--sembrar", "--solo-aviso")
c2 = json.load(open(os.path.join(root, "estado", "suite_nocturna", "sembrado.json")))
check(c1["nonce_hash"] != c2["nonce_hash"] and re.search(r"código: ([0-9a-f]+)", al1[0]["texto"]).group(1) != re.search(r"código: ([0-9a-f]+)", al2[-1]["texto"]).group(1),
      "un segundo --sembrar genera un código y un hash NUEVOS")
# un sembrado antiguo con el código en claro queda invalidado
root = juguete()
corre(root, "--sembrar", "--solo-aviso")
sp = os.path.join(root, "estado", "suite_nocturna", "sembrado.json")
viejo = {"fecha": "2026-10-10", "ts": "2026-10-10T15:00:00Z", "avisado": True, "nonce": "abc123", "confirmado": None}
json.dump(viejo, open(sp, "w"))
r_old, _ = corre(root, "confirmar-sembrado", "abc123")
check(r_old.returncode == 2 and "INVALIDADO" in r_old.stdout, "un sembrado con el código en claro (versión anterior) queda INVALIDADO")
# el aviso APLAZADO no cuenta como entregado
root = juguete()
r, al = corre(root, "--sembrar", "--solo-aviso", extra={"BTP_SUITE_NOCTURNA_AVISO_ESTADO": "aplazado"})
sem_a = json.load(open(os.path.join(root, "estado", "suite_nocturna", "sembrado.json")))
check(r.returncode == 1 and sem_a["avisado"] is False and sem_a["entrega"] == "aplazado", "aviso aplazado → avisado False, entrega «aplazado», rc 1")
check("APLAZADO al parte, NO entregado" in r.stderr and json.loads(r.stdout)["entrega"] == "aplazado", "y lo dice claramente: %r" % r.stderr[-110:])
cod_a = re.search(r"código: ([0-9a-f]+)", al[0]["texto"]).group(1)
escribe_transcript(root, [humano("el código es %s" % cod_a, ahora_utc + datetime.timedelta(minutes=5))])
r_a, _ = corre(root, "confirmar-sembrado", cod_a)
check(r_a.returncode == 2 and "NO llegó" in r_a.stdout, "un aviso aplazado no se puede confirmar aunque haya prompt humano con el código")

print("8) condiciones")


def prepara_condiciones(noches, ultimo_latido_h=2, sembrado=None, puerta=True, lista=True, sin_veto=False, humano_ok=True):
    root = juguete()
    if sin_veto:   # una lista de rojos conocidos que NO veta el núcleo del muro
        open(os.path.join(root, 'tests', '_rojos_conocidos.py'), 'w').write(
            'def problemas_de_entrada(e, hoy, deudas, baterias):\n    return []\n')
    shutil.copy(os.path.join(RAIZ, "tools", "tests_afectados.py"), os.path.join(root, "tools", "tests_afectados.py"))
    if puerta:
        open(os.path.join(root, "tests", "test_all_puerta.py"), "w").write("")
    if not lista:
        os.remove(os.path.join(root, "tests", "rojos_conocidos.json"))
    est = os.path.join(root, "estado")
    os.makedirs(os.path.join(est, "suite_nocturna"), exist_ok=True)
    os.makedirs(os.path.join(est, "heartbeat"), exist_ok=True)
    with open(os.path.join(est, "suite_nocturna", "historial.jsonl"), "w") as f:
        for d in noches:
            f.write(json.dumps(d if isinstance(d, dict) else {"fecha": d, "corrio": True, "estado": "ok"}) + "\n")
    ahora = datetime.datetime(2026, 10, 14, 12, 0, 0)
    ts = (ahora - datetime.timedelta(hours=ultimo_latido_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump({"agente": "suite-nocturna", "ts": ts, "estado": "ok"}, open(os.path.join(est, "heartbeat", "suite-nocturna.json"), "w"))
    if sembrado is not None:
        json.dump(sembrado, open(os.path.join(est, "suite_nocturna", "sembrado.json"), "w"))
        cod_ = (sembrado.get("confirmado") or {}).get("codigo")
        if humano_ok and cod_:
            escribe_transcript(root, [humano("recibido, el código es %s" % cod_, datetime.datetime.utcnow() - datetime.timedelta(hours=1))])
    return root, ahora.isoformat()


BUENAS = ["2026-10-12", "2026-10-13", "2026-10-14"]
SEMB_TS = (datetime.datetime.utcnow() - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
SAL = "00112233445566778899aabbccddeeff"
SEMB_OK = {"fecha": "2026-10-12", "ts": SEMB_TS, "avisado": True, "entrega": "entregado", "sal": SAL,
           "nonce_hash": hash_codigo("abc12345", SAL), "confirmado": {"codigo": "abc12345"}}


def veredicto(root, ahora):
    r, _ = corre(root, "condiciones", ahora=ahora)
    return r.returncode, r.stdout


root, ahora = prepara_condiciones(BUENAS, sembrado=SEMB_OK)
rc, out = veredicto(root, ahora)
check(rc == 0 and "SE CUMPLEN LAS TRES" in out, "las tres cumplidas → rc 0 (%s)" % out.strip().splitlines()[-1][:70])
for desc, kw, esperado in (
        ("solo 2 noches", dict(noches=BUENAS[1:], sembrado=SEMB_OK), "C1"),
        ("3 noches con un HUECO", dict(noches=["2026-10-10", "2026-10-13", "2026-10-14"], sembrado=SEMB_OK), "C1"),
        ("las 3 noches son de hace una semana", dict(noches=["2026-10-05", "2026-10-06", "2026-10-07"], sembrado=SEMB_OK), "C1"),
        ("último latido de hace 40 h", dict(noches=BUENAS, ultimo_latido_h=40, sembrado=SEMB_OK), "C1"),
        ("sin rojo sembrado", dict(noches=BUENAS, sembrado=None), "C2"),
        ("sembrado sin confirmar", dict(noches=BUENAS, sembrado=dict(SEMB_OK, confirmado=None)), "C2"),
        ("sembrado que NO se envió", dict(noches=BUENAS, sembrado=dict(SEMB_OK, avisado=False, entrega="error")), "C2"),
        ("sembrado APLAZADO al parte (avisado=True escrito a mano, entrega «aplazado»)", dict(noches=BUENAS, sembrado=dict(SEMB_OK, entrega="aplazado")), "C2"),
        ("sembrado antiguo con el código en claro", dict(noches=BUENAS, sembrado={k: v for k, v in dict(SEMB_OK, nonce="abc12345").items() if k not in ("sal", "nonce_hash")}), "C2"),
        ("el código confirmado no coincide con el hash del aviso", dict(noches=BUENAS, sembrado=dict(SEMB_OK, confirmado={"codigo": "ffffffff"})), "C2"),
        ("«confirmado» escrito A MANO en sembrado.json, sin prompt humano que lo respalde", dict(noches=BUENAS, sembrado=SEMB_OK, humano_ok=False), "C2"),
        ("sin test de la puerta", dict(noches=BUENAS, sembrado=SEMB_OK, puerta=False), "A"),
        ("sin lista de rojos conocidos", dict(noches=BUENAS, sembrado=SEMB_OK, lista=False), "B"),
        ("lista de rojos conocidos que NO veta el muro", dict(noches=BUENAS, sembrado=SEMB_OK, sin_veto=True), "B"),
        ("una de las 3 noches con un rojo NUEVO del muro (test_gate_*)",
         dict(noches=[BUENAS[0], {"fecha": BUENAS[1], "corrio": True, "estado": "ok", "nuevos": ["test_gate_toy.py"]}, BUENAS[2]],
              sembrado=SEMB_OK), "C1"),
        ("una noche con un rojo INTERMITENTE del muro",
         dict(noches=[{"fecha": BUENAS[0], "corrio": True, "estado": "ok", "flaky": ["test_fuga.sh"]}, BUENAS[1], BUENAS[2]],
              sembrado=SEMB_OK), "C1"),
        ("una noche con un rojo CONOCIDO del muro (permanente, con OK)",
         dict(noches=[BUENAS[0], BUENAS[1], {"fecha": BUENAS[2], "corrio": True, "estado": "ok", "conocidos": ["test_muro_hook.py"]}],
              sembrado=SEMB_OK), "C1"),
        ("una noche en estado rojo_muro (p. ej. una campaña de mutantes de la puerta)",
         dict(noches=[BUENAS[0], {"fecha": BUENAS[1], "corrio": True, "estado": "rojo_muro", "nuevos": ["mutantes-test_all_puerta"]}, BUENAS[2]],
              sembrado=SEMB_OK), "C1")):
    root, ahora = prepara_condiciones(**kw)
    rc, out = veredicto(root, ahora)
    linea = [l for l in out.splitlines() if l.startswith("❌")]
    check(rc == 1 and any(l.startswith("❌ " + esperado) for l in linea), "%s → NO se cumple (%s)" % (desc, esperado))
root, ahora = prepara_condiciones([BUENAS[0], {"fecha": BUENAS[1], "corrio": True, "estado": "rojo_nuevo", "nuevos": ["test_roja.py"]}, BUENAS[2]], sembrado=SEMB_OK)
rc_nm, out_nm = veredicto(root, ahora)
check(rc_nm == 0, "un rojo que NO es del muro no descarta la noche (rc=%d)" % rc_nm)
noches_con_fallo = [{"fecha": d, "corrio": True, "estado": "fallo"} for d in BUENAS]
root, ahora = prepara_condiciones(BUENAS, sembrado=SEMB_OK)
with open(os.path.join(root, "estado", "suite_nocturna", "historial.jsonl"), "w") as f:
    for e in noches_con_fallo:
        f.write(json.dumps(e) + "\n")
check(veredicto(root, ahora)[0] == 1, "3 noches con estado «fallo» no cuentan")

print("9) la entrega REAL (con una `salida` falsa): el sembrado va por «respuesta», el muro por el portero normal")
import importlib.util
import types
_env_prev = {k: os.environ.get(k) for k in ("BTP_REPO", "BTP_STATE_DIR", "BTP_SUITE_NOCTURNA_AVISO_A", "BTP_SUITE_NOCTURNA_AVISO_ESTADO")}
_root9 = tempfile.mkdtemp(prefix="nocturna_entrega_")
os.makedirs(os.path.join(_root9, "estado"))
for _k in ("BTP_SUITE_NOCTURNA_AVISO_A", "BTP_SUITE_NOCTURNA_AVISO_ESTADO"):
    os.environ.pop(_k, None)
os.environ["BTP_REPO"], os.environ["BTP_STATE_DIR"] = _root9, os.path.join(_root9, "estado")
_llam = []
_res = {"v": None}


def _fake_salida():
    m = types.ModuleType("salida")

    def report_to_titular(texto, **kw):
        _llam.append((texto, kw))
        if isinstance(_res["v"], Exception):
            raise _res["v"]
        return _res["v"]
    m.report_to_titular = report_to_titular
    m.alerta_critica = lambda texto: {"delivered": True, "blocked": False, "reason": "x"}
    return m


_salida_prev = sys.modules.get("salida")
_errores_prev = sys.modules.pop("errores", None)
sys.modules["salida"] = _fake_salida()
sys.path.insert(0, os.path.join(RAIZ, "tools"))
try:
    _spec = importlib.util.spec_from_file_location("sn9", TOOL)
    SN = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(SN)
    APLAZADO = {"delivered": False, "blocked": False, "reason": "aplazado al parte diario (presupuesto de avisos agotado)", "aplazado": True}
    ENTREGADO = {"delivered": True, "blocked": False, "reason": "entregado (x)"}
    _res["v"] = APLAZADO
    r_s = SN.avisar("sembrado", muro=True, sembrado=True)
    kw = _llam[-1][1]
    check(kw.get("categoria") == "respuesta" and not kw.get("urgente"), "el SEMBRADO usa la categoría «respuesta» (exenta solo del cupo) y NO es urgente: %s" % kw)
    check(r_s["avisado"] is False and r_s["estado"] == "aplazado", "y aplazado → avisado False, estado «aplazado»")
    _res["v"] = ENTREGADO
    check(SN.avisar("sembrado", muro=True, sembrado=True)["avisado"] is True, "entregado → avisado True")
    _res["v"] = APLAZADO
    r_m = SN.avisar("muro en rojo", muro=True)
    kw_m = _llam[-1][1]
    check(not kw_m.get("urgente") and kw_m.get("categoria") in (None, "humano"), "el aviso del MURO va por el portero normal: ni urgente ni «respuesta» (%s)" % kw_m)
    check(r_m["avisado"] is False and r_m["estado"] == "aplazado", "con el cupo agotado el aviso del muro NO se da por entregado")
    _res["v"] = RuntimeError("telegram caído")
    check(SN.avisar("x", muro=True, sembrado=True)["estado"] == "error", "si salida lanza, estado «error», no entregado")
finally:
    sys.modules.pop("errores", None)
    if _errores_prev is not None:
        sys.modules["errores"] = _errores_prev
    if _salida_prev is not None:
        sys.modules["salida"] = _salida_prev
    else:
        sys.modules.pop("salida", None)
    for _k, _v in _env_prev.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

print("10) el semáforo de la deuda escalada es un indicador; la deuda escalada del muro sigue contando")
SEMAFORO = "FAIL: test_no_hay_deuda_escalada_sin_cerrar (__main__.DeudaReal)"
root = juguete(rojos=[("test_deuda_escalada.py", SEMAFORO)], rc=1)
r, al = corre(root)
h = hist(root)[0]
check(h["indicadores"] == ["test_deuda_escalada.py"] and h["nuevos"] == [] and h["estado"] == "ok" and r.returncode == 0,
      "solo el semáforo en rojo → indicador, estado «ok», no cuenta como rojo (estado=%s, nuevos=%s)" % (h["estado"], h["nuevos"]))
check("suite-nocturna-test_deuda_escalada.py" not in deuda(root) and not al, "…y no abre deuda propia (se escalaría a sí mismo) ni avisa")
check(h["sin_log"] == 0, "…y no lo cuenta como «rojo sin log» (sin_log=%s)" % h["sin_log"])
root = juguete(rojos=[("test_deuda_escalada.py", "FAIL: test_cerrar_exige_un_test (__main__.Reglas)")], rc=1)
r, al = corre(root)
h = hist(root)[0]
check(h["nuevos"] == ["test_deuda_escalada.py"] and h["indicadores"] == [] and h["estado"] == "rojo_nuevo",
      "si falla OTRA comprobación del fichero (las reglas de cierre) es un rojo normal")
root = juguete(rojos=[("test_deuda_escalada.py", SEMAFORO + "\\nFAIL: test_cerrar_exige_un_test (__main__.Reglas)")], rc=1)
r, al = corre(root)
check(hist(root)[0]["nuevos"] == ["test_deuda_escalada.py"], "semáforo + otra comprobación rota → rojo normal (no se esconde la rotura tras el semáforo)")
root = juguete(rojos=[("test_otra_cosa.py", SEMAFORO)], rc=1)
r, al = corre(root)
check(hist(root)[0]["nuevos"] == ["test_otra_cosa.py"], "el nombre del semáforo en OTRA batería no la exime")
root = juguete(rojos=[("test_deuda_escalada.py", "Traceback (most recent call last):\\nboom")], rc=1)
r, al = corre(root)
check(hist(root)[0]["nuevos"] == ["test_deuda_escalada.py"], "un log que no se reconoce (se rompió, no es el semáforo) es un rojo normal")

root = juguete()
json.dump({"deuda-del-muro": {"estado": "escalado", "muro": True, "veces": 2},
           "deuda-normal": {"estado": "escalado", "muro": False, "veces": 5},
           "muro-sin-escalar": {"estado": "abierto", "muro": True},
           "suite-nocturna-test_x.py": {"estado": "escalado", "muro": True}},
          open(os.path.join(root, "estado", "deuda.json"), "w"))
r, al = corre(root)
check(hist(root)[0]["deuda_muro_escalada"] == ["deuda-del-muro"],
      "se apunta SOLO la deuda escalada y del muro (no la normal, no la abierta, no el espejo suite-nocturna-*): %s" % hist(root)[0]["deuda_muro_escalada"])
root = juguete()
r, al = corre(root)
check(hist(root)[0]["deuda_muro_escalada"] == [], "sin deuda del muro, lista vacía")

DEBUG = {"fecha": BUENAS[1], "corrio": True, "estado": "ok"}
root, ahora = prepara_condiciones([BUENAS[0], dict(DEBUG, deuda_muro_escalada=["subagente-ejecuta-lo-que-el-muro-me-deniega"]), BUENAS[2]], sembrado=SEMB_OK)
rc_dm, out_dm = veredicto(root, ahora)
check(rc_dm == 1 and "❌ C1" in out_dm, "una noche con deuda escalada del MURO sin cerrar no entra en la racha")
root, ahora = prepara_condiciones([BUENAS[0], dict(DEBUG, indicadores=["test_deuda_escalada.py"], deuda_muro_escalada=[]), BUENAS[2]], sembrado=SEMB_OK)
rc_in, out_in = veredicto(root, ahora)
check(rc_in == 0, "una noche con solo el semáforo en rojo SÍ entra en la racha (rc=%d)" % rc_in)

print("11) HALT a mitad de la pasada, una sola pasada a la vez, y un día con varias entradas")
import time as _time

RAPIDO = {"BTP_SUITE_NOCTURNA_SONDEO_S": "0.3"}
# a) el HALT aparece con la suite en marcha (y ya había escrito rojos): se para, no cuenta ni como rojo ni como noche buena
root = juguete(rojos=[("test_roja.py", "❌ falla"), ("test_fuga_toy.py", "❌ el muro")], rc=1)
open(os.path.join(root, "cfg", "halt_al_final"), "w").write("x")
t0_ = _time.time()
r, al = corre(root, extra=RAPIDO)
seg_ = _time.time() - t0_
h = hist(root)[0]
check(r.returncode == 0 and h.get("interrumpida_por_halt") is True and h["estado"] == "interrumpida_halt",
      "HALT con la suite en marcha → pasada «interrumpida_halt», rc 0 (rc=%d, estado=%s)" % (r.returncode, h.get("estado")))
check(seg_ < 25 and h["nuevos"] == [] and h["flaky"] == [], "…se PARA la suite (no espera a los 30 s del juguete: %.1f s) y no clasifica sus rojos" % seg_)
check(not deuda(root) and not al, "…sin deuda ni aviso (los rojos de una pasada con HALT no son del código)")
check(latido(root)["estado"] == "ok_sin_novedad" and latido(root).get("interrumpida") is True, "…y el latido lo dice")
check(not os.path.exists(os.path.join(root, "estado", "suite_nocturna", "pasada.lock")), "…y suelta el cerrojo")
# b) el HALT se pone justo al acabar: tampoco es de fiar
root = juguete(rojos=[("test_roja.py", "❌ falla")], rc=1)
open(os.path.join(root, "cfg", "halt_y_sale"), "w").write("x")
r, al = corre(root, extra=RAPIDO)
h = hist(root)[0]
check(h.get("interrumpida_por_halt") is True and h["nuevos"] == [] and not deuda(root),
      "HALT puesto justo al acabar → también interrumpida, sin rojos ni deuda")
# c) HALT en una campaña de mutantes del domingo (11-oct-26 es domingo): la campaña cortada no dice nada de la defensa
root = juguete()
os.makedirs(os.path.join(root, "tests", "mutantes"))
open(os.path.join(root, "tests", "mutantes", "test_all_paralelo.json"), "w").write("{}")
open(os.path.join(root, "tools", "mutantes.py"), "w").write(
    "import os, time\nopen(os.path.join(%r, '.HALT'), 'w').write('x')\ntime.sleep(30)\n" % root)
t0_ = _time.time()
r, al = corre(root, hoy="2026-10-11", extra=RAPIDO)
h = hist(root)[0]
check(h.get("interrumpida_por_halt") is True and h["nuevos"] == [] and not deuda(root) and (_time.time() - t0_) < 25,
      "HALT durante una campaña de mutantes → interrumpida, la campaña se corta y no abre deuda")
# d) una campaña que sobrevive dice CUÁL (la última línea de mutantes.py es siempre la misma frase genérica)
root = juguete()
os.makedirs(os.path.join(root, "tests", "mutantes"))
open(os.path.join(root, "tests", "mutantes", "test_all_paralelo.json"), "w").write("{}")
open(os.path.join(root, "tools", "mutantes.py"), "w").write(
    "print('  🔴 SOBREVIVE el mutante X OK')\nprint('❌ 1 mutante(s) SOBREVIVEN: el mutante X')\nprint('   Esa defensa no está cubierta')\nraise SystemExit(1)\n")
r, al = corre(root, hoy="2026-10-11")
q_ = deuda(root).get("suite-nocturna-mutantes-test_all_paralelo", {}).get("que", "")
check("el mutante X" in q_ and "Esa defensa no está cubierta" not in q_, "la deuda de una campaña roja nombra el mutante que sobrevive (%s)" % q_[-90:])

# e) una sola pasada a la vez
root = juguete()
lock_ = os.path.join(root, "estado", "suite_nocturna", "pasada.lock")
os.makedirs(os.path.dirname(lock_))
json.dump({"pid": os.getpid(), "inicio": "2026-10-12T03:00:00+02:00"}, open(lock_, "w"))
r, al = corre(root)
h = hist(root)[0]
check(r.returncode == 0 and not os.path.exists(os.path.join(root, "cfg", "corrio.marca")) and not h["corrio"] and "otra pasada en curso" in h["nota"],
      "con el cerrojo de una pasada VIVA no corre y lo apunta (%s)" % h.get("nota"))
check(os.path.exists(lock_), "…y no le quita el cerrojo a la que corre")
root = juguete()
lock_ = os.path.join(root, "estado", "suite_nocturna", "pasada.lock")
os.makedirs(os.path.dirname(lock_))
json.dump({"pid": 999999, "inicio": "2026-10-11T03:00:00+02:00"}, open(lock_, "w"))
r, al = corre(root)
check(r.returncode == 0 and os.path.exists(os.path.join(root, "cfg", "corrio.marca")) and hist(root)[0]["corrio"] and not os.path.exists(lock_),
      "con el cerrojo de una pasada MUERTA corre y lo suelta al acabar")
h = hist(root)[0]
check(h.get("inicio") and h.get("fin") and h.get("pid"), "cada entrada lleva inicio, fin y pid (antes no llevaban hora)")

# f) lo que dejó una pasada anterior del mismo día no se cuela en esta
root = juguete()
vieja_ = os.path.join(root, "estado", "suite_nocturna", "2026-10-12")
os.makedirs(os.path.join(vieja_, "rojos"))
open(os.path.join(vieja_, "rojos", "rojo-test_vieja.py.log"), "w").write("❌ de una pasada anterior\n")
open(os.path.join(vieja_, "salida.log"), "w").write("log de la pasada anterior\n")
r, al = corre(root)
h = hist(root)[0]
check(h["nuevos"] == [] and h["estado"] == "ok", "un rojo viejo de otra pasada del mismo día no se clasifica como de esta (nuevos=%s)" % h["nuevos"])
check(any(n.startswith("salida-") for n in os.listdir(vieja_)), "…y el log anterior se aparta, no se pierde")

# g) varias entradas el mismo día: decide la ÚLTIMA concluyente; las interrumpidas por HALT ni suman ni restan
OK_ = lambda d, **k: dict({"fecha": d, "corrio": True, "estado": "ok"}, **k)
MURO_ = lambda d: {"fecha": d, "corrio": True, "estado": "rojo_muro", "nuevos": ["mutantes-x"]}
HALT_ = lambda d: {"fecha": d, "corrio": True, "estado": "interrumpida_halt", "interrumpida_por_halt": True}
for desc, noches, cumple in (
        ("día con pasada en muro rojo Y una posterior verde → cuenta (decide la última)", [OK_(BUENAS[0]), MURO_(BUENAS[1]), OK_(BUENAS[1]), OK_(BUENAS[2])], True),
        ("día con pasada verde Y una posterior en muro rojo → NO cuenta (decide la última)", [OK_(BUENAS[0]), OK_(BUENAS[1]), MURO_(BUENAS[1]), OK_(BUENAS[2])], False),
        ("una pasada interrumpida por HALT tras la verde del día no la deshace", [OK_(BUENAS[0]), OK_(BUENAS[1]), HALT_(BUENAS[1]), OK_(BUENAS[2])], True),
        ("un día con SOLO una pasada interrumpida por HALT no cuenta (rompe la racha)", [OK_(BUENAS[0]), HALT_(BUENAS[1]), OK_(BUENAS[2])], False),
        ("una pasada interrumpida con estado de rojo no descarta un día bueno", [OK_(BUENAS[0]), OK_(BUENAS[1]), dict(MURO_(BUENAS[1]), interrumpida_por_halt=True), OK_(BUENAS[2])], True)):
    root, ahora = prepara_condiciones(noches, sembrado=SEMB_OK)
    rc_, out_ = veredicto(root, ahora)
    check((rc_ == 0) == cumple, "%s (rc=%d)" % (desc, rc_))

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
