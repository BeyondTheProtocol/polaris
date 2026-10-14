#!/usr/bin/env python3
"""test_permiso_guard_llavero_socket.py — la clave que firma el permiso no se saca con un envoltorio
delante, y al socket del firmante no se le habla a mano (13/14-oct-26).

LOS HUECOS. Dos comités los infirieron leyendo `salida_guard`; aquí quedaron CONFIRMADOS
ejecutando la función del guard sobre la versión anterior (42a42d10), sin lanzar ninguna orden:
  · `security find-generic-password -s btp-ok-envio-mac -w` se denegaba, pero bastaba ponerle
    delante `nice`, `timeout`, `caffeinate`, `arch`, `xargs`, `launchctl asuser`, `stdbuf`,
    `script`, `taskpolicy`, `find -exec` o meterlo en `sh -c 'nice …'` para que pasara: el
    guard solo miraba `security` como PRIMER programa tras `sudo/env/nohup/time/command/exec`;
  · partir el nombre con comillas (`"btp-ok-envio-""mac"`) también pasaba;
  · `nc -U`, `socat`, `curl --unix-socket` o un `AF_UNIX` de Python contra el socket del
    firmante pasaban (el socket es nuevo: nadie lo cerraba).

Este test NO ejecuta nada de eso ni toca ninguna clave: llama a `_escribe_bash` en proceso (y al
hook entero por subproceso, que solo decide) con las órdenes como texto.

LÍMITE DECLARADO (el mismo de la cabecera de trust-cloud en `salida_guard`): esto cierra las vías
LEGIBLES. No ve un script en disco que llame a `security`, ni el nombre de la clave o la palabra
`security` armados a trozos o codificados (`$(printf 'secu%s' rity)`, base64), ni un programa
corriente que lance otro por dentro (`awk 'BEGIN{system(…)}'`, `git -c alias.x='!…'`). Es
obstáculo y rastro, no frontera: por eso la clave del firmante no la necesita ninguna sesión.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, ".claude", "hooks", "salida_guard.py")
CASA = os.path.expanduser("~/claudecode")
os.environ.setdefault("BTP_STATE_DIR", tempfile.mkdtemp(prefix="guard-llavero-"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
_spec = importlib.util.spec_from_file_location("salida_guard_bajo_prueba", HOOK)
SG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SG)

HMAC = "btp-ok-envio-" + "mac"
ED = "btp-firmante-" + "ed25519"
SOCK = CASA + "/tools/state/ok_envio/firmante.sock"


def _leer(servicio):
    return "find-generic-password -s " + servicio + " -w"


def _envueltas(servicio):
    f = _leer(servicio)
    return [
        "security " + f,
        "/usr/bin/security " + f,
        "nice security " + f,
        "nice -n 5 security " + f,
        "timeout 5 security " + f,
        "gtimeout 5 /usr/bin/security " + f,
        "caffeinate -i security " + f,
        "arch -arm64 security " + f,
        "launchctl asuser 501 security " + f,
        "stdbuf -o0 security " + f,
        "script -q /dev/null security " + f,
        "taskpolicy -b security " + f,
        "nohup nice security " + f,
        "time nice security " + f,
        "env X=1 nice security " + f,
        "echo " + servicio + " | xargs security find-generic-password -w -s",
        "echo " + servicio + " | xargs -I X security find-generic-password -s X -w",
        "find . -maxdepth 0 -exec security " + f + " ';'",
        "sh -c 'nice security " + f + "'",
        "S=" + servicio + "; nice security find-generic-password -s \"$S\" -w",
        "S=" + servicio + "; sh -c 'nice security find-generic-password -s \"$S\" -w'",
        "if true; then nice security " + f + "; fi",
        "security find-generic-password -s \"" + servicio[:8] + "\"\"" + servicio[8:] + "\" -w",
        "nice security delete-generic-password -s " + servicio,
        # La misma clase por otras puertas (14-oct-26): el programa en una variable, `eval`, y
        # una shell que ejecuta lo que le llega por la entrada.
        "S=security; $S " + f,
        "S=/usr/bin/security; nice $S " + f,
        "eval \"security " + f + "\"",
        "eval nice security " + f,
        "echo 'security " + f + "' | sh",
        "echo 'security " + f + "' | bash -s",
        "printf '%s\\n' 'security " + f + "' | nice zsh",
        "python3 -c \"import subprocess;subprocess.run(['security','find-generic-password','-s','" + servicio + "','-w'])\"",
    ]


# Nombrar la clave no es ir a por ella, y el trabajo de todos los días con el Llavero sigue libre.
LIBRES = [
    "git commit -m 'fix: el guard niega security sobre " + HMAC + " con nice delante'",
    "git commit -m 'feat: clave " + ED + " del firmante, leida con /usr/bin/security'",
    "grep -rn " + HMAC + " tools/ .claude/",
    "grep -rn security tools/firmante.py | grep " + ED,
    "echo security " + ED,
    "rg -n 'security.*" + ED + "' tools/",
    "security find-generic-password -s btp-synapse-token -w",
    "nice security find-generic-password -s btp-synapse-token -w",
    "for s in btp-gmail btp-telegram; do security find-generic-password -s \"$s\" -w; done",
    "ls -la " + SOCK,
    "stat " + SOCK,
    "test -S " + SOCK + " && echo vivo",
    "python3 tools/firmante.py estado",
    "python3 tools/firmante.py publica",
    "cat tools/firmante.py",
    # Una nota que NOMBRA la clave y `security` es la entrada de un script, no código (falso
    # positivo que cazó verificacion el 14-oct-26).
    "python3 tools/archivar_nota.py --titulo x <<'EOF'\nla clave " + ED + " se lee con security find-generic-password\nEOF",
    "python3 -m py_compile .claude/hooks/ok_envio_prompt.py tools/firmante.py",
    "python3 tests/_corre_hook.py .claude/hooks/salida_guard.py < /tmp/p.json",
    "curl -s https://example.org/",
    "nc -z 127.0.0.1 8787",
    "python3 - <<'PY'\nimport socket\ns = socket.socket(socket.AF_UNIX)\ns.connect('/tmp/otro.sock')\nPY",
    "launchctl print gui/501/com.btp.firmante",
    "tail -5 tools/launchd/logs/firmante.err",
]

AL_SOCKET = [
    "nc -U " + SOCK,
    "nice nc -U " + SOCK,
    "echo x | nc -U tools/state/ok_envio/firmante.sock",
    "socat - UNIX-CONNECT:" + SOCK,
    "curl --unix-socket " + SOCK + " http://x/",
    "cd tools/state/ok_envio && nc -U firmante.sock",
    "nc -U tools/state/ok_envio/*.sock",
    "S=tools/state/ok_envio/firmante.sock; socat - UNIX-CONNECT:$S",
    "python3 -c \"import socket;s=socket.socket(socket.AF_UNIX);s.connect('" + SOCK + "');s.sendall(b'x')\"",
    "python3 - <<'PY'\nimport socket, os\ns = socket.socket(socket.AF_UNIX)\ns.connect(os.path.join('tools/state', 'ok_envio', 'firmante' + '.sock'))\nPY",
    "python3 - <<'PY'\nimport sys\nsys.path.insert(0, 'tools')\nimport firmante\nprint(firmante.pedir('s', 'p', 'h'))\nPY",
    "python3 -c \"import sys; sys.path.insert(0,'tools'); from firmante import pedir; pedir('s','p','h')\"",
    "node -e \"require('net').connect('" + SOCK + "')\"",
    "python3 tools/firmante.py servir",
    "/opt/homebrew/bin/python3 /Users/polaris/claudecode/tools/firmante.py servir --crear-clave-si-falta",
    "nohup tools/firmante.py servir",
    "python3 tests/_corre_hook.py .claude/hooks/ok_envio_prompt.py < /tmp/p.json",
]


class LaClaveNoSaleConUnEnvoltorio(unittest.TestCase):

    def _motivo(self, cmd):
        return SG._escribe_bash(cmd, CASA)

    def test_las_dos_claves_con_cualquier_envoltorio(self):
        for servicio in (HMAC, ED):
            for cmd in _envueltas(servicio):
                with self.subTest(cmd=cmd[:70]):
                    self.assertTrue(self._motivo(cmd), "PASA y no debería")

    def test_lo_que_solo_nombra_o_es_trabajo_normal_sigue_libre(self):
        for cmd in LIBRES:
            with self.subTest(cmd=cmd[:70]):
                self.assertEqual(self._motivo(cmd), "")

    def test_no_se_habla_con_el_socket_ni_se_lanza_el_firmante_a_mano(self):
        for cmd in AL_SOCKET:
            with self.subTest(cmd=cmd[:70]):
                self.assertTrue(self._motivo(cmd), "PASA y no debería")

    def test_si_el_analisis_peta_falla_cerrado_al_nombrar_la_clave_o_el_socket(self):
        """`_escribe_protegido` no deniega todo si su análisis peta, pero SÍ cuando la llamada
        nombra lo que protege. Ahí entran la clave nueva y el socket."""
        real = SG._escribe_protegido_bruto

        def _peta(tool, entrada):
            raise RuntimeError("análisis roto")
        SG._escribe_protegido_bruto = _peta
        try:
            for cmd in ("nice security " + _leer(ED), "nc -U " + SOCK, "security " + _leer(HMAC)):
                self.assertTrue(SG._escribe_protegido("Bash", dict(command=cmd)), cmd)
            self.assertEqual(SG._escribe_protegido("Bash", dict(command="ls -la")), "")
        finally:
            SG._escribe_protegido_bruto = real

    def test_por_el_hook_entero(self):
        """El mismo veredicto pasando por `main()`, como lo lanza el harness."""
        env = dict(os.environ, BTP_STATE_DIR=tempfile.mkdtemp(prefix="guard-llavero-"))
        env.pop("MURO_PROFILE", None)

        def decide(cmd):
            carga = dict(hook_event_name="PreToolUse", tool_name="Bash", tool_input=dict(command=cmd),
                         session_id="s", cwd=CASA, permission_mode="bypassPermissions")
            r = subprocess.run([sys.executable, HOOK], input=json.dumps(carga), capture_output=True,
                               text=True, timeout=60, env=env)
            if r.returncode == 2:
                return "deny"
            if not r.stdout.strip():
                return None
            return json.loads(r.stdout)["hookSpecificOutput"].get("permissionDecision")
        for cmd in ("nice security " + _leer(HMAC), "timeout 5 security " + _leer(ED),
                    "nc -U " + SOCK, "python3 tools/firmante.py servir"):
            self.assertEqual(decide(cmd), "deny", cmd)
        for cmd in ("python3 tools/firmante.py estado", "grep -rn " + ED + " tools/",
                    "nice security find-generic-password -s btp-synapse-token -w"):
            self.assertIsNone(decide(cmd), cmd)


if __name__ == "__main__":
    unittest.main(verbosity=1)
