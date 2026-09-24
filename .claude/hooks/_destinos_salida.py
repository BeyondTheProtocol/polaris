"""_destinos_salida.py — A DÓNDE puede llevar datos una orden, y qué tools MCP solo leen.

POR QUÉ EXISTE (24-sep-2026, auditoría externa de Marcos Gorgojo, hallazgo 3.2). `salida_guard.py`
preguntaba «¿es un programa que sé que envía?» (smtplib, correo_smtp, xurl, curl a Gmail/X/Slack…).
Todo lo demás pasaba: `curl -X POST` a un host cualquiera, `wget --post-file`, `requests.post`, `scp`,
`nc`, `ssh host 'curl…'`, `git push` a un remoto recién añadido, un relay con `socat`, y en MCP
`send_later`, `share_file`, `file_upload` o `create_file` de Drive (14/14 reproducidas). Perseguir
programa a programa no cierra nunca. Esto invierte el eje: lo que LLEVA DATOS a la red se juzga por el
DESTINO, contra esta lista.

DE DÓNDE SALE LA LISTA (medido, no supuesto). Replay de 23.940 órdenes Bash reales: los hosts que
recibieron una llamada CON CUERPO en todo el historial son estos. Con ellos, lo legítimo sigue pasando.

CÓMO SE AMPLÍA. Como el propio hook: en una rama, con replay (`tools/replay_guard.py`) y con la
fusión que firma {{TITULAR}}. Vive en `.claude/hooks/` a propósito: el lazo no puede escribir ahí
(`muro_guard.SELF_PROTECT`) y en sesión interactiva nada cambia hasta que se fusiona a casa base. Un
JSON que un agente pudiera editar en caliente no sería una lista de destinos: sería una sugerencia.

LO QUE ESTO NO CIERRA (dicho, no escondido): un binario propio que abra sockets por dentro, la
exfiltración por DNS y cualquier canal que no se lea en el texto de la orden. Es el límite de analizar
texto; lo cierra el broker con credenciales separadas que propone el auditor (en deuda).
"""
import re

# Hosts que pueden RECIBIR un cuerpo (POST/PUT/subida). Nada más. `localhost` va aparte.
HOSTS_CARGA = frozenset({
    # lo suyo
    "panel.helptitular.com", "helptitular.com", "www.helptitular.com", "anatomia-panel.netlify.app",
    # cerebros y APIs que ya usan las tools (la sensibilidad la decide el borde, no esto)
    "api.anthropic.com", "api.perplexity.ai", "consensus.app", "api.typesafe.ai",
    # registros y datos de investigación que se consultan por POST
    "euclinicaltrials.eu", "reec.aemps.gob.es", "www.chinadrugtrials.org.cn",
    "www.chictr.org.cn", "chictr.org.cn",      # registro chino: su buscador va por POST (replay 24-sep)
    "repo-prod.prod.sagebase.org",
    # renovar un token de Google no es mandar nada
    "oauth2.googleapis.com",
    # el VPS de Hong Kong del radar chino (`tools/cn_fetch.py`)
    "47.243.53.161",
})

# La propia máquina. Llevarle datos no los saca de aquí. El agujero de un relay (un proxy local que
# reenvía fuera) se cierra en su origen, denegando MONTAR el relay (ver RELAYS), no vetando
# localhost: se miden decenas de puertos locales legítimos (Ollama, servidores de desarrollo).
LOCALES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})

# Montar un relay o un túnel ES abrir una salida. 0 apariciones en todo el historial medido.
RELAYS = frozenset({"socat", "ngrok", "cloudflared", "localtunnel", "lt", "bore", "frpc", "chisel"})

# `git push` solo a los repos de la organización (el host solo no basta: github.com/cualquiera
# también es github.com). Un remoto que es una ruta local no saca nada de la máquina.
GIT_REMOTOS = re.compile(r"(^|[/@:])github\.com[:/]BeyondTheProtocol/", re.I)

# ── MCP ─────────────────────────────────────────────────────────────────────────────────────────
# Servidores que NO hablan con un servicio de fuera, o que ya se juzgan por su acción (navegador,
# escritorio): no se les aplica la regla de verbos.
MCP_NO_EXTERNOS = re.compile(
    r"^mcp__(ccd_|visualize|6f616b42_|claude_code_ios_simulator|mcp_registry|"
    r"claude_browser|claude_in_chrome|computer_use|terminal)", re.I)

# Verbos que solo LEEN. Lo que no casa aquí, en un servidor externo, escribe fuera de la máquina:
# se le pregunta a {{TITULAR}} (o se le avisa, según el modo). Medido sobre las 133 tools MCP usadas.
VERBO_LECTURA = re.compile(
    r"^(get|list|search|read|fetch|download|find|count|query|resolve|lookup|convert|status|"
    r"check|show|explain|think|paginate|collect|guide|read_me|wait)(_|$)|"
    r"(_getter|_searcher|_analyzer|_predictor|_status|_usage|_next_steps)$", re.I)

# Verbos que MANDAN aunque no estuvieran en la lista vieja (`__send_later` se escapaba por el `$`).
VERBO_ENVIO_EXTRA = re.compile(
    r"^(send_later|send_\w+|share_\w*|publish\w*|post_\w+|upload\w*|file_upload|invite\w*)$", re.I)
