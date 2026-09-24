#!/usr/bin/env python3
"""token_rotacion_guard.py — canjear un refresh token NO es un diagnóstico de solo lectura.

NORMA (registro: tools/normas.json) `feedback-refresh-token-es-de-un-solo-uso`, clase BLOQUEO:
«canjear un refresh token es una operación DESTRUCTIVA (rotación de un solo uso) — nunca lo
hagas como diagnóstico "de solo lectura" sin guardar el par nuevo».

POR QUÉ ES DESTRUCTIVO Y NO LO PARECE. Un `grant_type=refresh_token` contra el token endpoint
devuelve un access token nuevo Y un refresh token nuevo, e invalida el viejo. Si ese canje se
hace «solo para ver si el token sigue vivo» y no se guarda el par que vuelve, el token que
estaba guardado queda QUEMADO: a partir de ahí hace falta un login humano con navegador.

Y ya se sabe lo que cuesta, porque está escrito en `tools/_oauth_refresh.py`: **Consensus estuvo
28 días sin responder mientras el panel decía 🟢 OK**, justo porque el carril no podía renovarse
solo y nadie estaba delante para hacer el login. scite es peor: 15 minutos de vida por token.

LA PUERTA YA EXISTE. `_oauth_refresh.renovar(state_dir, server_url)` hace el canje Y GUARDA el
par nuevo con permisos 0600. Este guard no inventa nada: deniega la forma cruda y nombra la
puerta, igual que `singleton_guard` hace con `git_mutex` y `activar_daemon`.

QUÉ DETECTA. El marcador es `grant_type=refresh_token`, que es el parámetro del propio estándar
OAuth 2.0: si aparece en el comando, ese comando canjea. No es heurística. Sobre los ~2.900
comandos Bash reales de los transcripts aparece **0 veces**, así que esto no puede romper nada
que se estuviera haciendo — y sigue mereciendo la pena porque el error es de una sola vez y se
paga en días de carril muerto.

Lo que NO detecta, y por eso la norma queda PARCIAL: un canje hecho dentro de un `.py` que el
comando solo invoca. Ahí el guard ve `python3 x.py` y no puede saber qué hace dentro.

FAIL-OPEN, como sus hermanos. Escotilla: `BTP_TOKEN_OK=1`.
Contrato de hooks (code.claude.com/docs/hooks): exit 0 permite · exit 2 deniega.
"""
import json
import os
import re
import sys

# El parámetro del estándar OAuth 2.0 (RFC 6749 §6). Tolera comillas, espacios y JSON.
_RE_CANJE = re.compile(r"""grant_type\s*["']?\s*[=:]\s*["']?\s*refresh_token""", re.I)
# La puerta sancionada: si el comando la invoca, es EXACTAMENTE lo que hay que hacer.
_PUERTA = ("_oauth_refresh", "oauth_refresh.py")


def canjea(command):
    return bool(_RE_CANJE.search(command or ""))


def usa_la_puerta(command):
    c = (command or "").lower()
    return any(p in c for p in _PUERTA)


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return 0
    if os.environ.get("BTP_TOKEN_OK") == "1":
        return 0
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not canjea(command) or usa_la_puerta(command):
        return 0
    sys.stderr.write(
        "TOKEN ⛔ eso canjea un refresh token, y el canje es de UN SOLO USO.\n"
        "   El servidor devuelve un par nuevo e invalida el viejo. Si no guardas el que vuelve,\n"
        "   el token guardado queda QUEMADO y hace falta un login humano con navegador — que es\n"
        "   como Consensus estuvo 28 días mudo con el panel en verde.\n"
        "   La puerta que canjea Y GUARDA el par nuevo (0600) ya existe:\n"
        "     python3 -c \"import sys; sys.path.insert(0,'tools'); import _oauth_refresh; \\\n"
        "                 print(_oauth_refresh.renovar(<state_dir>, <server_url>))\"\n"
        "   Si de verdad quieres canjearlo a mano y guardarlo tú: BTP_TOKEN_OK=1.\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)   # FAIL-OPEN deliberado (ver cabecera)
