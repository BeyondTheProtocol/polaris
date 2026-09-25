#!/usr/bin/env python3
"""test_borrador_unico.py — un correo, UN borrador.

NORMA: `feedback-borrador-mejorado-borra-el-anterior` (clase BLOQUEO, repetida) — «al rehacer/
mejorar un borrador de correo, BORRAR el anterior — nunca dejar dos versiones del mismo correo
en Borradores».

MEDIDO sobre los transcripts reales antes de escribir el guard: 47 `create_draft`, 8 pares
(destinatario, asunto) con más de un borrador, uno de ellos SEIS veces → 14 borradores
redundantes de 47. Y `update_draft`, que es la salida correcta, ya se usaba 19 veces.

EL FILO ESTÁ EN EL ASUNTO. «Sample at {{CENTRO}}», «Re: Sample at {{CENTRO}}» y «RE: [Extern] RE: Sample at
{{CENTRO}}» son el MISMO correo, y un guard que no normalice los prefijos deja pasar justo los casos
reales que se midieron. Por eso hay tantos casos de normalización aquí.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402

ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
GUARD = _os.path.join(ROOT, ".claude", "hooks", "borrador_unico_guard.py")
_TMP = tempfile.mkdtemp(prefix="borrador_")
LIBRETA = _os.path.join(_TMP, "borradores.json")
ENV = dict(_os.environ, BTP_BORRADORES_JSON=LIBRETA)
ENV.pop("BTP_BORRADOR_OK", None)
CREATE = "mcp__gmail__create_draft"
UPDATE = "mcp__gmail__update_draft"


def _rc(tool, ti, env=None):
    p = subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=20,
                       env=env or ENV,
                       input=json.dumps({"tool_name": tool, "tool_input": ti}))
    return p.returncode, (p.stderr or "")


def _reset():
    if _os.path.exists(LIBRETA):
        _os.remove(LIBRETA)


fallos = 0
casos = []


def check(desc, ok):
    global fallos
    casos.append((desc, ok))
    if not ok:
        fallos += 1


# ── el caso medido: dos veces el mismo correo ─────────────────────────────────────────────
_reset()
A = {"to": "onco@{{CENTRO}}.net", "subject": "Sample at {{CENTRO}}", "body": "v1"}
check("primer borrador → PASA", _rc(CREATE, A)[0] == 0)
rc, msg = _rc(CREATE, dict(A, body="v2 mejorada"))
check("segundo borrador del mismo correo → DENIEGA", rc == 2)
check("   …y el mensaje ofrece las dos salidas (update_draft / borrar)",
      "update_draft" in msg and "borra el anterior" in msg)

# ── el filo: los prefijos de respuesta son el MISMO correo ────────────────────────────────
for asunto in ("Re: Sample at {{CENTRO}}", "RE: [Extern] RE: Sample at {{CENTRO}}", "  re:  sample at {{CENTRO}}",
               "Fwd: Sample at {{CENTRO}}", "RV: Sample at {{CENTRO}}"):
    rc, _ = _rc(CREATE, {"to": "onco@{{CENTRO}}.net", "subject": asunto})
    check("«%s» es el mismo correo → DENIEGA" % asunto.strip()[:34], rc == 2)

# ── update_draft es la salida correcta, nunca se bloquea ──────────────────────────────────
check("update_draft sobre el mismo correo → PASA (es lo que pide la norma)",
      _rc(UPDATE, dict(A, body="v3"))[0] == 0)
check("   …y sigue bloqueando un create_draft posterior", _rc(CREATE, A)[0] == 2)

# ── lo que NO se puede romper ─────────────────────────────────────────────────────────────
check("otro asunto al mismo destinatario → PASA",
      _rc(CREATE, {"to": "onco@{{CENTRO}}.net", "subject": "Biopsia hepática — logística"})[0] == 0)
check("mismo asunto a OTRO destinatario → PASA",
      _rc(CREATE, {"to": "otra@hospital.example", "subject": "Sample at {{CENTRO}}"})[0] == 0)
check("el hilo manda sobre el asunto: threadId nuevo → PASA",
      _rc(CREATE, {"to": "onco@{{CENTRO}}.net", "subject": "Sample at {{CENTRO}}", "threadId": "t-999"})[0] == 0)
check("   …y repetir ese threadId → DENIEGA",
      _rc(CREATE, {"to": "otra@x.example", "subject": "distinto", "threadId": "t-999"})[0] == 2)
check("destinatario con nombre («Ana <a@x.example>») casa por la dirección",
      _rc(CREATE, {"to": "Ana Pérez <a@x.example>", "subject": "Informe"})[0] == 0
      and _rc(CREATE, {"to": "a@x.example", "subject": "Re: Informe"})[0] == 2)

_reset()
check("BTP_BORRADOR_OK=1 → PASA aunque repita",
      _rc(CREATE, A)[0] == 0
      and _rc(CREATE, A, env=dict(ENV, BTP_BORRADOR_OK="1"))[0] == 0)
check("BTP_BORRADOR_OK=0 NO cuenta como escotilla",
      _rc(CREATE, A, env=dict(ENV, BTP_BORRADOR_OK="0"))[0] == 2)
check("una tool que no es de borradores → PASA",
      _rc("mcp__gmail__send_message", A)[0] == 0)
check("Bash no se toca", _rc("Bash", {"command": "ls"})[0] == 0)
p = subprocess.run([_sys.executable, GUARD], input="{no es json", capture_output=True,
                   text=True, timeout=20, env=ENV)
check("payload ilegible → PASA (fail-OPEN deliberado)", p.returncode == 0)

# ── caduca ────────────────────────────────────────────────────────────────────────────────
_reset()
_rc(CREATE, A)
with open(LIBRETA, encoding="utf-8") as fh:
    libro = json.load(fh)
for k in libro:
    libro[k]["ts"] = 0          # hace mucho
with open(LIBRETA, "w", encoding="utf-8") as fh:
    json.dump(libro, fh)
check("un borrador de hace meses ya no bloquea (caduca a 30 días)", _rc(CREATE, A)[0] == 0)

# ── borrar libera (24-sep-26: la respuesta a Penguin se quedó bloqueada tras borrar) ────────
DELETE = "mcp__gmail__delete_draft"


def _post(tool, ti, resp=None):
    return subprocess.run([_sys.executable, GUARD], capture_output=True, text=True, timeout=20,
                          env=ENV, input=json.dumps({"hook_event_name": "PostToolUse",
                                                     "tool_name": tool, "tool_input": ti,
                                                     "tool_response": resp})).returncode


_reset()
P = {"to": ["contacto@editorial.example"], "cc": ["agente@x.example"],
     "subject": "Re: Propuesta editorial", "replyToMessageId": "m1"}
check("crear el borrador de la respuesta → PASA", _rc(CREATE, P)[0] == 0)
# Gmail MCP devuelve bloques de texto con JSON dentro: el caso real
_post(CREATE, P, [{"type": "text", "text": json.dumps({"id": "r111", "threadId": "t1"})}])
check("   …PostToolUse apunta el id del borrador",
      any(v.get("draft") == "r111" for v in json.load(open(LIBRETA)).values()))
check("repetirlo sin borrar → DENIEGA", _rc(CREATE, P)[0] == 2)
rc, msg = _rc(CREATE, P)
check("   …y el aviso avisa de que update_draft saca del hilo",
      "replyToMessageId" in msg and "saca el borrador del hilo" in msg)
check("borrar OTRO borrador no libera este",
      _post(DELETE, {"draftId": "r999"}) == 0 and _rc(CREATE, P)[0] == 2)
check("delete_draft de ESE id (PostToolUse) → libera",
      _post(DELETE, {"draftId": "r111"}) == 0 and _rc(CREATE, P)[0] == 0)
check("respuesta como dict plano también sirve",
      _post(CREATE, P, {"id": "r222"}) == 0
      and any(v.get("draft") == "r222" for v in json.load(open(LIBRETA)).values()))
check("delete_draft en PreToolUse no bloquea nunca",
      _rc(DELETE, {"draftId": "r222"})[0] == 0)
check("update_draft con solo draftId refresca SU entrada, sin clave vacía",
      _rc(UPDATE, {"draftId": "r222", "body": "v2"})[0] == 0
      and not any(k.startswith("asunto:|") for k in json.load(open(LIBRETA))))
check("respuesta ilegible en PostToolUse → PASA y no apunta nada",
      _post(CREATE, {"to": "z@x.example", "subject": "otro"}, "basura") == 0)

for desc, ok in casos:
    print(("  ✅ " if ok else "  ❌ ") + desc)
shutil.rmtree(_TMP, ignore_errors=True)
print()
print("RESULTADO borrador_unico: %d de %d OK" % (len(casos) - fallos, len(casos)))
print("✅ UN CORREO, UN BORRADOR" if not fallos else "❌ revisar")
raise SystemExit(1 if fallos else 0)
