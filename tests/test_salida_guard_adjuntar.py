#!/usr/bin/env python3
"""test_salida_guard_adjuntar.py — adjuntar a un borrador de Gmail no es enviar, y tras adjuntar no se pulsa.

POR QUÉ EXISTE (17-oct-2026). Al adjuntar 3 imágenes a la respuesta al HuffPost, `salida_guard`
denegó `file_upload` sobre el borrador con cualquier permiso («subir» = commits; «enviar a X» no
casa porque el destinatario no va en la llamada): adjuntar volvía a {{TITULAR}}, contra su regla del
14-oct ([[feedback-adjuntar-sin-que-guie]]). Y de paso: un `file_upload` DENTRO de un
`browser_batch` pasaba libre, porque el batch solo miraba `input.action`.

Lo que se fija, contra el hook de verdad (subprocess, estado en un directorio temporal):
  1. file_upload a una pestaña navegada a un compose de Gmail en ESTA sesión → pasa;
  2. y cierra el CANDADO: el clic siguiente se DENIEGA, también en bypass;
  3. sin navegar antes, en otra pestaña, en otra sesión, sin tabId, a otro host, a un Gmail que
     no es compose, o con un clic de por medio → se deniega como siempre;
  4. dentro de un browser_batch, file_upload / form_input / JS que pulsa ya no pasan libres;
  5. el registro no se puede escribir a mano (Write/Bash), igual que el permiso;
  6. sin candado, un clic en bypass sigue siendo solo aviso (no rompe el navegador).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "salida_guard.py")
CH = "mcp__claude-in-chrome__"
COMPOSE = ("https://mail.google.com/mail/?authuser=0#all?compose="
           "thread-f:1879314062594552625%2Bmsg-a:r7109129845999787243")

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class Hook:
    def __init__(self):
        self.state = tempfile.mkdtemp(prefix="sg-adj-")

    def __call__(self, tool, inp, sesion="ses-A", modo="bypassPermissions"):
        payload = {"tool_name": tool, "tool_input": inp, "session_id": sesion, "cwd": self.state,
                   "permission_mode": modo, "transcript_path": "/nonexistent"}
        env = dict(os.environ, BTP_STATE_DIR=self.state)
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=env, timeout=30)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])["hookSpecificOutput"]
        except Exception:
            return {} if p.returncode == 0 else {"permissionDecision": "deny", "rc": p.returncode}


def deny(dec):
    return dec.get("permissionDecision") == "deny"


def nav(h, tab, url=COMPOSE, **k):
    return h(CH + "navigate", {"url": url, "tabId": tab}, **k)


def sube(h, tab=7, **k):
    inp = {"paths": ["/tmp/a.png"], "ref": "ref_3"}
    if tab is not None:
        inp["tabId"] = tab
    return h(CH + "file_upload", inp, **k)


def clic(h, tab=7, **k):
    return h(CH + "computer", {"action": "left_click", "coordinate": [10, 10], "tabId": tab}, **k)


def main():
    # 1 + 2: el camino bueno y su candado
    h = Hook()
    ok(not deny(nav(h, 7)), "navegar al compose pasa")
    d = sube(h)
    ok(not deny(d), "file_upload al compose navegado pasa")
    ok("Adjunto a un BORRADOR" in d.get("additionalContext", ""), "y dice qué ha pasado")
    ok(deny(clic(h)), "tras adjuntar, el clic se DENIEGA en bypass (candado)")
    ok(deny(h(CH + "form_input", {"ref": "r", "value": "x", "tabId": 7})), "y form_input también")
    ok(deny(h(CH + "javascript_tool", {"text": "document.querySelector('b').click()", "tabId": 7})),
       "y el JS que pulsa también")
    ok(deny(h("mcp__computer-use__app_click", {"x": 1})), "y el clic de escritorio también")
    ok(not deny(h(CH + "computer", {"action": "screenshot", "tabId": 7})), "leer sigue libre en el candado")
    ok(not deny(clic(h, sesion="ses-B")), "el candado es de ESA sesión, no de las demás")
    ok(deny(sube(h)), "un segundo adjunto exige volver a navegar")
    nav(h, 7)
    ok(not deny(sube(h)), "…y con la navegación nueva pasa")

    # 3: lo que no es un compose suyo recién navegado
    h = Hook()
    ok(deny(sube(h)), "sin navegar antes: denegado")
    h = Hook(); nav(h, 7)
    ok(deny(sube(h, tab=8)), "otra pestaña: denegado")
    h = Hook(); nav(h, 7)
    ok(deny(sube(h, sesion="ses-B")), "otra sesión: denegado")
    h = Hook(); nav(h, 7)
    ok(deny(sube(h, tab=None)), "sin tabId: denegado")
    for url in ("https://evil.example.net/mail/#x?compose=1",
                "https://mail.google.com.evil.net/mail/#all?compose=1",
                "https://mail.google.com/mail/u/0/#inbox",
                "https://www.google.com/url?q=https://mail.google.com/mail/#a?compose=1",
                "http://mail.google.com/mail/#all?compose=1"):
        h = Hook(); nav(h, 7, url=url)
        ok(deny(sube(h)), "navegado a %s: denegado" % url[:50])
    h = Hook(); nav(h, 7); clic(h)
    ok(deny(sube(h)), "con un clic entre navegar y subir: denegado")
    h = Hook(); nav(h, 7); h(CH + "navigate", {"url": "https://evil.example.net"})
    ok(deny(sube(h)), "un navigate sin tabId borra lo apuntado: denegado")
    h = Hook(); nav(h, 7); nav(h, 7, url="https://evil.example.net/")
    ok(deny(sube(h)), "la misma pestaña navegada fuera después: denegado")
    h = Hook()
    h(CH + "browser_batch", {"actions": [{"name": "navigate", "input": {"url": COMPOSE, "tabId": 7}},
                                         {"name": "computer", "input": {"action": "left_click",
                                                                        "coordinate": [1, 1], "tabId": 7}}]})
    ok(deny(sube(h)), "batch navegar + clic: el clic invalida, denegado")
    h = Hook()
    h(CH + "browser_batch", {"actions": [{"name": "navigate", "input": {"url": COMPOSE, "tabId": 7}}]})
    ok(not deny(sube(h)), "batch que solo navega al compose apunta la pestaña")
    ok(deny(h(CH + "upload_image", {"imageId": "x", "tabId": 7})), "upload_image sigue denegado")

    # 4: el hueco del batch
    h = Hook(); nav(h, 7)
    ok(deny(h(CH + "browser_batch", {"actions": [{"name": "file_upload",
                                                  "input": {"paths": ["/etc/hosts"], "ref": "r", "tabId": 7}}]})),
       "file_upload dentro de un batch: denegado (antes pasaba libre)")
    spec = importlib.util.spec_from_file_location("sg_adj", HOOK)
    sg = importlib.util.module_from_spec(spec)
    os.environ["BTP_STATE_DIR"] = tempfile.mkdtemp(prefix="sg-adj-mod-")
    spec.loader.exec_module(sg)
    b = lambda *its: sg._sale_fuera(CH + "browser_batch", {"actions": list(its)})  # noqa: E731
    ok(b({"name": "form_input", "input": {"ref": "r", "value": "x"}}) == "clic", "form_input en batch = clic")
    ok(b({"name": "javascript_tool", "input": {"text": "x.click()"}}) == "clic", "JS que pulsa en batch = clic")
    ok(b({"name": "find", "input": {"query": "x"}},
         {"name": "computer", "input": {"action": "screenshot"}}) is None, "leer en batch sigue libre")
    ok(b({"name": "computer", "input": {"action": "left_click"}}) == "clic", "clic en batch sigue siendo clic")
    ok(b({"name": "computer"}) == "clic", "ítem sin input: ante la duda, clic")

    # 5: el registro es tan intocable como el permiso
    ruta = os.path.join("tools", "state", "ok_envio_adjuntar.json")
    ok(sg._escribe_protegido("Write", {"file_path": "/Users/x/claudecode/" + ruta, "content": "{}"}),
       "Write sobre el registro: denegado")
    ok(sg._escribe_protegido("Bash", {"command": "echo '{}' > %s" % ruta}), "echo > registro: denegado")

    for c in ("ln -sf /tmp/x " + ruta, "mv /tmp/x " + ruta, "cp /tmp/x " + ruta,
              "ln -sf /tmp/x tools/state/ok_envio_adjuntar.tmp.json"):
        ok(sg._escribe_protegido("Bash", {"command": c}), "%s: denegado" % c.split()[0])
    # Un registro que es un enlace no vale aunque diga lo que el agente quiera.
    h = Hook()
    falso = os.path.join(h.state, "falso.json")
    with open(falso, "w") as f:
        json.dump({"ses-A": {"pestanas": {"7": 9e12}}}, f)
    os.symlink(falso, os.path.join(h.state, "ok_envio_adjuntar.json"))
    ok(deny(sube(h)), "registro enlazado a un fichero editable: denegado")
    h = Hook()
    destino = os.path.join(h.state, "destino.json")
    os.symlink(destino, os.path.join(h.state, "ok_envio_adjuntar.tmp.json"))
    nav(h, 7)
    ok(not os.path.lexists(destino) and not os.path.islink(os.path.join(h.state, "ok_envio_adjuntar.json")),
       "un symlink plantado en el temporal no se sigue ni acaba siendo el registro")
    ok(not deny(sube(h)), "…y el registro bueno sigue funcionando")

    # 6: sin candado, el clic en bypass sigue siendo aviso
    h = Hook()
    ok(not deny(clic(h)), "sin adjunto previo, el clic en bypass solo avisa")

    print("RESULTADO salida_guard adjuntar: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
