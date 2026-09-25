#!/usr/bin/env python3
"""test_nonce_a4_blindado.py — un agente no puede fabricarse la aprobación de un borrador (A4).

POR QUÉ EXISTE (26-sep-2026, deuda `nonce-a4-legible-aprobacion-fabricable`). El nonce con el que
{{TITULAR}} aprueba un borrador del outbox iba EN CLARO en `outbox/pending/*.json`, ningún hook protegía
leerlo ni llamar a `salida.approve_and_deliver`, y sin lista blanca de Instagram el DM salía a
cualquier destinatario. Y a ella el nonce no le llegaba por ninguna vía. Lo cazó el comité de
arquitectura al revisar el plan F4 de P3 (idea de {{CONTACTO}}, https://contacto, con su
agente KAI, revisión del 25-sep-2026).

Lo que se fija:
  1. el borrador NO guarda el nonce en claro (ni el campo ni los bytes);
  2. el nonce va a {{TITULAR}} por su chat (report urgente) con la orden exacta; un REPORT retenido no avisa;
  3. con ese nonce se aprueba; un borrador viejo con nonce en claro ya no;
  4. 5 fallos bloquean el borrador, incluso para el nonce bueno;
  5. el hook de salida deniega el código que llama a la aprobación (y deja leerlo).
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

HOOK = os.path.join(ROOT, ".claude", "hooks", "salida_guard.py")
SELF = "999000111"
DEST = "17841400000000999"
fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _bomba(*a, **k):
    raise RuntimeError("un test ha llegado a la red de verdad")


def setup():
    tmp = tempfile.mkdtemp(prefix="nonce_a4_")
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    salida.STATE = tmp
    salida.NOTIF_CFG = os.path.join(tmp, "notif", "config.json")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    salida._self_chatid = lambda: SELF
    salida.IG_ALLOWLIST = os.path.join(tmp, "noallow.json")
    salida.urllib.request.urlopen = _bomba
    return tmp


def main():
    setup()
    avisos = []
    salida.report_to_titular = lambda texto, **k: (avisos.append((texto, k)) or {"delivered": True})
    enviados = []
    salida._DELIVERERS["instagram"] = lambda dest, text, **k: (enviados.append(dest) or (True, "mock"))

    print("── 1-2 · el nonce no queda en disco y le llega a ella ──")
    r = salida.send("instagram", "contact", DEST, "hola")
    name = r["draft"]
    crudo = open(os.path.join(salida.PENDING, name), encoding="utf-8").read()
    d = json.loads(crudo)
    check("nonce" not in d and "nonce_sello" in d, "el borrador guarda el sello, no el nonce")
    check(len(avisos) == 1 and avisos[0][1].get("urgente") is True, "se le avisa a {{TITULAR}}, urgente")
    orden = [l for l in avisos[0][0].splitlines() if l.startswith("aprobar ")]
    check(len(orden) == 1 and orden[0].split()[1] == name, "el aviso trae la orden exacta")
    nonce = orden[0].split()[2] if orden else ""
    check(len(nonce) == 16 and nonce not in crudo, "nonce de 64 bits y sus bytes no están en el fichero")
    check(nonce not in json.dumps(r), "el agente que creó el borrador no recibe el nonce")
    salida._draft("telegram", salida.REPORT, SELF, "retenido", "test")
    check(len(avisos) == 1, "un REPORT retenido (va a ella misma) no genera aviso")

    print("── 3 · se aprueba con su nonce; uno viejo en claro, no ──")
    res = salida.approve_and_deliver(name, nonce)
    check(res.get("delivered") and enviados == [DEST], "con el nonce que le llegó, sale")
    viejo = os.path.join(salida.PENDING, "viejo-instagram-contact-x.json")
    with open(viejo, "w", encoding="utf-8") as f:
        json.dump({"canal": "instagram", "accion": "contact", "dest": DEST, "texto": "x",
                   "estado": "pendiente_OK", "nonce": "abc123abc1"}, f)
    res = salida.approve_and_deliver(os.path.basename(viejo), "abc123abc1")
    check(not res.get("delivered") and len(enviados) == 1, "un borrador viejo con nonce en claro no sale")

    print("── 4 · cinco fallos bloquean ──")
    avisos.clear()
    name2 = salida.send("instagram", "contact", DEST, "otra")["draft"]
    bueno = [l for l in avisos[0][0].splitlines() if l.startswith("aprobar ")][0].split()[2]
    for i in range(5):
        salida.approve_and_deliver(name2, "%016x" % i)
    res = salida.approve_and_deliver(name2, bueno)
    check(not res.get("delivered") and "bloqueado" in res.get("reason", ""),
          "tras 5 fallos ni el nonce bueno entrega: %r" % res.get("reason"))

    print("── 5 · el hook deniega llamar a la aprobación ──")
    env = dict(os.environ, BTP_STATE_DIR=tempfile.mkdtemp(), BTP_OK_ENVIO_CLAVE="c" * 64)

    def decide(cmd):
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "s", "cwd": ROOT,
             "permission_mode": "bypassPermissions"}), capture_output=True, text=True, env=env, timeout=60)
        if p.returncode == 2:
            return "deny"
        try:
            return json.loads(p.stdout)["hookSpecificOutput"].get("permissionDecision")
        except Exception:
            return None
    for cmd in ("python3 -c \"import sys; sys.path.insert(0,'tools'); import salida; "
                "salida.approve_and_deliver('b.json', 'x')\"",
                "python3 - <<'PY'\nimport salida\nsalida.reconciliar('b.json', 'reintentar')\nPY",
                "python3 -c \"import bot_telegram as b; b.handle_message({'text': 'aprobar b x'})\""):
        check(decide(cmd) == "deny", "deniega: %s" % cmd.splitlines()[0][:60])
    for cmd in ("grep -n approve_and_deliver tools/salida.py", "python3 tests/test_instagram_dm.py",
                # nombrarla dentro de una cadena (regex, informe) no es llamarla: falso positivo real
                "python3 - <<'PY'\nimport re\nprint(re.findall(r'approve_and_deliver|reconciliar\\s*\\(', 'x'))\nPY",
                # un heredoc que es la ENTRADA de un script (una nota que se archiva) no se ejecuta
                "python3 tools/archivar_nota.py \"t\" <<'EOF'\n`approve_and_deliver` entregaba y luego movía\nEOF"):
        check(decide(cmd) != "deny", "deja: %s" % cmd)

    print("\ntest_nonce_a4_blindado: %d fallos" % len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
