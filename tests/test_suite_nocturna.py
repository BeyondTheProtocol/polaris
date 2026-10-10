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
import json
import os
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BTP_SUITE_NOCTURNA_TOOL: la campaña de mutantes (tests/mutantes/suite_nocturna.json) apunta aquí a una
# copia MUTADA de tools/suite_nocturna.py; sin la variable, la de verdad.
TOOL = os.environ.get("BTP_SUITE_NOCTURNA_TOOL") or os.path.join(RAIZ, "tools", "suite_nocturna.py")
fallos = []


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
  while IFS='|' read -r n l; do [ -n "$n" ] && printf '%s\n' "$l" > "$ROJOS/rojo-$n.log"; done < "$(dirname "$0")/../cfg/rojos.txt"
fi
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
               BTP_SUITE_NOCTURNA_AVISO_A=alerta, BTP_SUITE_NOCTURNA_PERMITE_WORKTREE="")
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
root = juguete(en_worktree=True)
r, al = corre(root)
check(r.returncode == 2 and not os.path.exists(os.path.join(root, "cfg", "corrio.marca")), "desde un worktree se niega (rc 2) y no corre nada")
root = juguete(dormir=20)
r, al = corre(root, tope=2)
check(r.returncode == 1 and latido(root)["estado"] == "fallo" and len(al) == 1 and "NO pudo completarse" in al[0]["texto"],
      "runner colgado → estado «fallo» y AVISO (una nocturna muda es peor que ninguna)")

print("7) --sembrar")
root = juguete()
r, al = corre(root, "--sembrar")
sem = json.load(open(os.path.join(root, "estado", "suite_nocturna", "sembrado.json")))
check(len(al) == 1 and al[0]["sembrado"] and "SEMBRADO A PROPÓSITO" in al[0]["texto"], "el aviso sembrado sale")
check(sem["avisado"] is True and sem["confirmado"] is None and not deuda(root), "queda esperando confirmación, sin deuda")
r2, _ = corre(root, "confirmar-sembrado", "sí, me llegó el aviso de prueba")
check(r2.returncode == 0 and json.load(open(os.path.join(root, "estado", "suite_nocturna", "sembrado.json")))["confirmado"], "confirmar-sembrado lo registra")
r3, _ = corre(root, "confirmar-sembrado", "ok")
check(r3.returncode == 2, "una confirmación vacía no vale")

print("8) condiciones")


def prepara_condiciones(noches, ultimo_latido_h=2, sembrado=None, puerta=True, lista=True, sin_veto=False):
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
            f.write(json.dumps({"fecha": d, "corrio": True, "estado": "ok"}) + "\n")
    ahora = datetime.datetime(2026, 10, 14, 12, 0, 0)
    ts = (ahora - datetime.timedelta(hours=ultimo_latido_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump({"agente": "suite-nocturna", "ts": ts, "estado": "ok"}, open(os.path.join(est, "heartbeat", "suite-nocturna.json"), "w"))
    if sembrado is not None:
        json.dump(sembrado, open(os.path.join(est, "suite_nocturna", "sembrado.json"), "w"))
    return root, ahora.isoformat()


BUENAS = ["2026-10-12", "2026-10-13", "2026-10-14"]
SEMB_OK = {"fecha": "2026-10-12", "avisado": True, "confirmado": {"cita": "me llegó"}}


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
        ("sembrado sin confirmar", dict(noches=BUENAS, sembrado={"fecha": "2026-10-12", "avisado": True, "confirmado": None}), "C2"),
        ("sembrado que NO se envió", dict(noches=BUENAS, sembrado={"fecha": "2026-10-12", "avisado": False, "confirmado": {"cita": "x"}}), "C2"),
        ("sin test de la puerta", dict(noches=BUENAS, sembrado=SEMB_OK, puerta=False), "A"),
        ("sin lista de rojos conocidos", dict(noches=BUENAS, sembrado=SEMB_OK, lista=False), "B"),
        ("lista de rojos conocidos que NO veta el muro", dict(noches=BUENAS, sembrado=SEMB_OK, sin_veto=True), "B")):
    root, ahora = prepara_condiciones(**kw)
    rc, out = veredicto(root, ahora)
    linea = [l for l in out.splitlines() if l.startswith("❌")]
    check(rc == 1 and any(l.startswith("❌ " + esperado) for l in linea), "%s → NO se cumple (%s)" % (desc, esperado))
noches_con_fallo = [{"fecha": d, "corrio": True, "estado": "fallo"} for d in BUENAS]
root, ahora = prepara_condiciones(BUENAS, sembrado=SEMB_OK)
with open(os.path.join(root, "estado", "suite_nocturna", "historial.jsonl"), "w") as f:
    for e in noches_con_fallo:
        f.write(json.dumps(e) + "\n")
check(veredicto(root, ahora)[0] == 1, "3 noches con estado «fallo» no cuentan")

if fallos:
    print("\n❌ %d fallo(s)" % len(fallos))
    sys.exit(1)
print("\nOK")
