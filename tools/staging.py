#!/usr/bin/env python3
"""staging.py — "antesala": ver y probar una feature como en PRODUCCIÓN, en privado,
desde cualquier dispositivo de {{TITULAR}} (por Tailscale), ANTES de mandarla a producción.

Privado por diseño: el índice escucha solo en localhost y los relays SOLO en la IP de
Tailscale de Polaris (la tailnet de {{TITULAR}}, NUNCA internet). No despliega nada: "mandar a
producción" lleva al PR para que mergee ella/{{CONTACTO}} (gate del muro). Egress-cero, sin LLM.

Reusa el patrón de relay de tools/preview_remoto.py y las convenciones del repo.

USO (normalmente lo lanzo yo; el interruptor para {{TITULAR}} es tools/staging.sh):
  python3 tools/staging.py web <rama> [--modo prod|dev]   # web helptitular.com
  python3 tools/staging.py port <N> [--nombre "X"] [--pr URL]  # un tool con interfaz web
  python3 tools/staging.py on | off | estado
Luego, en su dispositivo (con Tailscale):  http://100.114.113.73:3010
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # ~/claudecode
WEB_REPO = os.environ.get("BTP_WEB_REPO", "/Users/titular/projects/titular-{{APELLIDO}}-case")
WT_BASE = os.environ.get("BTP_STAGING_WT", "/Users/titular/projects/.mgc-staging")
STATE_DIR = os.path.join(REPO, "tools", "state", "staging")
CURRENT = os.path.join(STATE_DIR, "current.json")
DAEMON_PID = os.path.join(STATE_DIR, "daemon.pid")
LOG = os.path.join(REPO, "tools", "launchd", "logs", "staging.log")

TS_IP = "100.114.113.73"     # Polaris en la tailnet (solo la red privada de {{TITULAR}})
INDEX_LOCAL, INDEX_TS = 4000, 3010   # página índice (la antesala)
APP_LOCAL, APP_TS = 4010, 3011       # app en pruebas (web build o tool)

PNPM = "/opt/homebrew/bin/pnpm"

sys.path.insert(0, os.path.join(REPO, "tools"))  # para importar portguard (tool hermana)
import portguard  # noqa: E402 — arranque limpio de puertos (libera huérfanos propios)


# ──────────────────────────── estado (current.json) ────────────────────────────
def _ensure_dirs():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)


def load_current():
    try:
        with open(CURRENT) as f:
            return json.load(f)
    except Exception:
        return {}


def save_current(d):
    _ensure_dirs()
    tmp = CURRENT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CURRENT)


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _log(msg):
    _ensure_dirs()
    try:
        with open(LOG, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ──────────────────────────── utilidades de red ────────────────────────────
def _port_open(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _wait_port(port, secs=120):
    end = time.time() + secs
    while time.time() < end:
        if _port_open("127.0.0.1", port):
            return True
        time.sleep(1)
    return False


# ──────────────────────────── relay TCP (patrón de preview_remoto.py) ────────────────────────────
def _pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def _relay(listen_port, target_resolver):
    """Escucha SOLO en TS_IP:listen_port y reenvía a 127.0.0.1:target_resolver().
    target_resolver es una función → puerto local (permite destino dinámico)."""
    # Arranque LIMPIO: libera al relay huérfano propio que siguiera ocupando ESTE puerto y reintenta
    # (port-scoped: no toca el otro relay/índice de la antesala, que escuchan en otros puertos).
    try:
        srv = portguard.reusable_tcp_server(TS_IP, listen_port, markers=("staging.py",), backlog=128, log=_log)
    except OSError as e:
        _log("relay %d no pudo escuchar en %s (%s). ¿Tailscale activo?" % (listen_port, TS_IP, e))
        return
    _log("relay vivo: %s:%d -> 127.0.0.1 (dinámico)" % (TS_IP, listen_port))
    while True:
        try:
            cli, _ = srv.accept()
        except Exception as e:
            # Un error transitorio (p.ej. EMFILE, interrupción) NO debe matar el relay:
            # registrar, esperar un poco y seguir aceptando. (El bug previo: break → puerto
            # "escuchando" pero muerto, conexiones colgadas.)
            _log("relay %d accept err: %s" % (listen_port, e))
            time.sleep(0.2)
            continue
        # El conectar arriba se hace en su propio hilo → el accept loop nunca se bloquea.
        threading.Thread(target=_serve_conn, args=(cli, target_resolver), daemon=True).start()


def _serve_conn(client, target_resolver):
    tgt = target_resolver()
    if not tgt:
        client.close()
        return
    up = None
    for host in ("127.0.0.1", "::1"):
        try:
            up = socket.create_connection((host, tgt), timeout=10)
            break
        except Exception:
            up = None
    if up is None:
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, up), daemon=True).start()
    threading.Thread(target=_pipe, args=(up, client), daemon=True).start()


# ──────────────────────────── página índice (la antesala) ────────────────────────────
PROVISIONAL = "Pruebas antes de producción"   # nombre provisional → pasa por 07·Marca


def _estado_payload():
    c = load_current()
    app_local = c.get("app_local", APP_LOCAL)
    c = dict(c)
    c["app_viva"] = _port_open("127.0.0.1", app_local)
    c["app_url"] = "http://%s:%d/" % (TS_IP, APP_TS)
    return c


def _index_html():
    c = _estado_payload()
    kind = c.get("kind")
    status = c.get("status", "vacio")
    viva = c.get("app_viva")
    if kind == "web":
        cargado = "Web · rama <code>%s</code> · modo %s" % (c.get("rama", "?"), c.get("modo", "prod"))
    elif kind == "port":
        cargado = "%s · puerto %s" % (c.get("nombre", "Herramienta"), c.get("app_local", "?"))
    else:
        cargado = "Nada cargado todavía."
    desde = c.get("started_at", "")
    pr = c.get("pr_url")

    if status == "compilando":
        badge = ('<span class="b b-warn">compilando…</span>')
        ver_ok = False
    elif status == "error":
        badge = ('<span class="b b-err">error en el build</span>')
        ver_ok = False
    elif kind and viva:
        badge = ('<span class="b b-ok">listo</span>')
        ver_ok = True
    elif kind:
        badge = ('<span class="b b-warn">arrancando…</span>')
        ver_ok = False
    else:
        badge = ('<span class="b b-mut">en espera</span>')
        ver_ok = False

    ver_btn = ('<a class="btn btn-go" href="http://%s:%d/" target="_blank" rel="noopener">Ver la feature</a>'
               % (TS_IP, APP_TS)) if ver_ok else \
              ('<span class="btn btn-dis" aria-disabled="true">Ver la feature</span>')

    if pr:
        prod_btn = ('<a class="btn btn-prod" href="%s" target="_blank" rel="noopener">'
                    'Mandar a producción →</a>') % pr
        prod_hint = "Te lleva al PR en GitHub. El merge lo hacéis tú o {{CONTACTO}}. No despliega solo."
    else:
        prod_btn = ('<button class="btn btn-prod" id="apr">Doy el OK a esta feature</button>')
        prod_hint = "Aún no hay PR abierto. Tu OK me avisa para abrirlo. No despliega solo."

    return """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(prov)s</title>
<style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
 margin:0;padding:24px 16px;line-height:1.5;background:#f6f6f4;color:#1d1d1b}
@media(prefers-color-scheme:dark){body{background:#16161a;color:#ececec}.card{background:#222228!important;border-color:#33333a!important}code{background:#2c2c33!important}}
.wrap{max-width:560px;margin:0 auto}
h1{font-size:22px;font-weight:600;margin:0 0 2px}
.sub{color:#6b6b66;font-size:13px;margin:0 0 20px}
.card{background:#fff;border:1px solid #e6e6e1;border-radius:14px;padding:18px;margin:0 0 16px}
.lbl{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#8a8a85;margin:0 0 6px}
.cargado{font-size:17px;margin:0 0 12px}
code{background:#f0f0ec;border-radius:6px;padding:1px 6px;font-size:.92em}
.b{display:inline-block;font-size:13px;font-weight:600;border-radius:999px;padding:4px 12px}
.b-ok{background:#e1f5ee;color:#0f6e56}.b-warn{background:#faeeda;color:#854f0b}
.b-err{background:#fceaea;color:#a32d2d}.b-mut{background:#eee;color:#777}
.desde{color:#8a8a85;font-size:13px;margin:10px 0 0}
.btn{display:block;width:100%%;text-align:center;text-decoration:none;font-size:17px;
 font-weight:600;padding:15px;border-radius:12px;margin:10px 0 0;border:1px solid transparent;cursor:pointer}
.btn-go{background:#1d1d1b;color:#fff}
.btn-prod{background:#0f6e56;color:#fff}
.btn-dis{background:#ececec;color:#aaa;cursor:not-allowed}
.hint{color:#8a8a85;font-size:13px;margin:8px 0 0}
.foot{color:#9a9a95;font-size:12px;text-align:center;margin:22px 0 0;line-height:1.6}
</style></head><body><div class="wrap">
<h1>%(prov)s</h1>
<p class="sub">Nombre provisional (pendiente de marca) · privado, solo tus dispositivos</p>
<div class="card">
  <p class="lbl">Qué hay cargado</p>
  <p class="cargado">%(cargado)s</p>
  %(badge)s
  <p class="desde">%(desde)s</p>
  %(ver)s
</div>
<div class="card">
  <p class="lbl">Cuando lo hayas probado</p>
  %(prod)s
  <p class="hint">%(prod_hint)s</p>
</div>
<p class="foot">Esto es una simulación de producción en privado por Tailscale.<br>
No sale a internet y no despliega nada por su cuenta.</p>
</div>
<script>
setTimeout(function(){location.reload()}, 6000);
var a=document.getElementById('apr');
if(a){a.onclick=function(){a.disabled=true;a.textContent='Avisando…';
 fetch('/api/aprobar',{method:'POST'}).then(function(){a.textContent='Listo, avisado ✓';})
 .catch(function(){a.textContent='No pude avisar, díselo tú';});};}
</script>
</body></html>""" % {
        "prov": PROVISIONAL, "cargado": cargado, "badge": badge,
        "desde": ("Cargado: " + desde) if desde else "",
        "ver": ver_btn, "prod": prod_btn, "prod_hint": prod_hint,
    }


def _start_index_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/api/estado"):
                body = json.dumps(_estado_payload(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = _index_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.startswith("/api/aprobar"):
                _aprobar()
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

    # Arranque LIMPIO: liberar al daemon huérfano propio que siguiera ocupando el puerto del índice
    # (matarlo aquí suelta también sus relays 3010/3011, que viven en el mismo proceso) y reintentar.
    httpd = portguard.http_server(ThreadingHTTPServer, "127.0.0.1", INDEX_LOCAL, H,
                                  markers=("staging.py",), log=_log)   # solo localhost
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _log("índice vivo en 127.0.0.1:%d" % INDEX_LOCAL)


def _aprobar():
    c = load_current()
    desc = c.get("rama") or c.get("nombre") or "(sin nombre)"
    _log("{{TITULAR}} dio el OK a: %s" % desc)
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import salida  # noqa
        salida.report_to_titular(
            "Diste el OK en la antesala a «%s». Lo preparo para producción (PR) y te lo dejo "
            "a un clic para que lo mergees tú o {{CONTACTO}}." % desc)
    except Exception as e:
        _log("no pude avisar por salida.py: %s" % e)


# ──────────────────────────── servidor estático (build de producción) ────────────────────────────
def serve_static(directory, port):
    """Sirve la carpeta de `nuxt generate` (.output/public) con URLs limpias estilo Netlify."""
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def translate_path(self, path):
            p = super().translate_path(path)
            if os.path.isfile(p):
                return p
            # URLs limpias estilo Netlify: /donaciones → donaciones.html (preferido sobre el
            # dir donaciones/ que solo guarda el _payload.json), luego dir/index.html.
            base = p.rstrip("/").rstrip(os.sep)
            if base and os.path.isfile(base + ".html"):
                return base + ".html"
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, "index.html")):
                return os.path.join(p, "index.html")
            return p

        def list_directory(self, path):   # nada de listados de directorio
            self.send_error(404, "Not found")
            return None

    handler = functools.partial(H, directory=directory)
    # Arranque LIMPIO: libera al servidor estático huérfano propio que siguiera en este puerto.
    httpd = portguard.http_server(ThreadingHTTPServer, "127.0.0.1", port, handler,
                                  markers=("staging.py",), log=_log)   # solo localhost (lo expone el relay)
    httpd.serve_forever()


# ──────────────────────────── daemon (índice + relays) ────────────────────────────
def serve():
    _ensure_dirs()
    with open(DAEMON_PID, "w") as f:
        f.write(str(os.getpid()))
    _start_index_server()
    threading.Thread(target=_relay, args=(INDEX_TS, lambda: INDEX_LOCAL), daemon=True).start()
    threading.Thread(
        target=_relay,
        args=(APP_TS, lambda: load_current().get("app_local", APP_LOCAL)),
        daemon=True,
    ).start()
    _log("antesala arriba: http://%s:%d (índice) / :%d (app)" % (TS_IP, INDEX_TS, APP_TS))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def ensure_daemon():
    if _port_open("127.0.0.1", INDEX_LOCAL):
        return
    _ensure_dirs()
    out = open(LOG, "a")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "_serve"],
                     stdout=out, stderr=out, start_new_session=True, cwd=REPO)
    for _ in range(20):
        if _port_open("127.0.0.1", INDEX_LOCAL):
            return
        time.sleep(0.3)


def stop_daemon():
    try:
        with open(DAEMON_PID) as f:
            pid = int(f.read().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    try:
        os.remove(DAEMON_PID)
    except Exception:
        pass


# ──────────────────────────── app: web (worktree + build) ────────────────────────────
def _run(cmd, cwd, secs=900):
    print("  $ %s" % " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, timeout=secs).returncode


def _sanitize(rama):
    return rama.replace("/", "-").replace(" ", "_")


def ensure_worktree(rama):
    os.makedirs(WT_BASE, exist_ok=True)
    wt = os.path.join(WT_BASE, _sanitize(rama))
    if os.path.isdir(os.path.join(wt, ".git")) or os.path.exists(os.path.join(wt, ".git")):
        subprocess.run(["git", "-C", wt, "checkout", "--detach", rama], check=False)
        return wt
    # --detach: una foto de la rama para construir, sin chocar si ya está abierta en el repo.
    rc = subprocess.run(["git", "-C", WEB_REPO, "worktree", "add", "--detach", wt, rama]).returncode
    if rc != 0:
        raise SystemExit("No pude crear el worktree de la rama '%s' en %s" % (rama, WEB_REPO))
    return wt


def _pr_url(rama):
    try:
        r = subprocess.run(["gh", "pr", "view", rama, "--json", "url", "-q", ".url"],
                           cwd=WEB_REPO, capture_output=True, text=True, timeout=20)
        url = r.stdout.strip()
        return url if url.startswith("http") else None
    except Exception:
        return None


def stop_app():
    c = load_current()
    pid = c.get("app_pid")
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def cmd_web(rama, modo):
    stop_app()
    save_current({"kind": "web", "rama": rama, "modo": modo, "app_local": APP_LOCAL,
                  "status": "compilando", "started_at": _now()})
    wt = ensure_worktree(rama)
    print("→ Preparando dependencias (pnpm install)…")
    if _run([PNPM, "install"], wt) != 0:
        save_current({"kind": "web", "rama": rama, "modo": modo, "status": "error",
                      "started_at": _now()})
        raise SystemExit("Falló pnpm install")
    if modo == "prod":
        print("→ Build de producción (pnpm generate)…")
        if _run([PNPM, "generate"], wt) != 0:
            save_current({"kind": "web", "rama": rama, "modo": modo, "status": "error",
                          "started_at": _now()})
            raise SystemExit("Falló pnpm generate")
        # Preset estático: se sirve la carpeta generada (pnpm preview no levanta servidor).
        pub = os.path.join(wt, ".output", "public")
        cmd = [sys.executable, os.path.abspath(__file__), "_static", pub, str(APP_LOCAL)]
    else:
        cmd = [PNPM, "dev", "--port", str(APP_LOCAL)]
    print("→ Sirviendo: %s" % " ".join(cmd))
    out = open(os.path.join(os.path.dirname(LOG), "staging-app.log"), "a")
    proc = subprocess.Popen(cmd, cwd=wt, stdout=out, stderr=out, start_new_session=True)
    if not _wait_port(APP_LOCAL, 120):
        save_current({"kind": "web", "rama": rama, "modo": modo, "status": "error",
                      "app_pid": proc.pid, "started_at": _now()})
        raise SystemExit("La app no levantó en el puerto %d" % APP_LOCAL)
    save_current({"kind": "web", "rama": rama, "modo": modo, "app_local": APP_LOCAL,
                  "app_pid": proc.pid, "pr_url": _pr_url(rama), "status": "listo",
                  "started_at": _now()})
    ensure_daemon()
    _ok_banner()


def cmd_port(n, nombre, pr):
    stop_app()
    save_current({"kind": "port", "nombre": nombre or "Herramienta", "app_local": int(n),
                  "pr_url": pr, "status": "listo", "started_at": _now()})
    ensure_daemon()
    _ok_banner()


def _ok_banner():
    print("\n✅ Antesala lista. Abre en tu iPhone/iPad/portátil (con Tailscale):")
    print("   👉 http://%s:%d" % (TS_IP, INDEX_TS))
    print("   (la feature en sí: http://%s:%d )" % (TS_IP, APP_TS))


# ──────────────────────────── on/off/estado ────────────────────────────
def cmd_on():
    ensure_daemon()
    print("Antesala encendida.")
    _ok_banner()


def cmd_off():
    stop_app()
    stop_daemon()
    save_current({})
    print("⏹  Antesala parada (app + relays + índice). Los worktrees se conservan;")
    print("   para borrarlos:  python3 tools/staging.py limpiar")


def cmd_limpiar():
    subprocess.run(["git", "-C", WEB_REPO, "worktree", "prune"], check=False)
    if os.path.isdir(WT_BASE):
        for d in os.listdir(WT_BASE):
            wt = os.path.join(WT_BASE, d)
            subprocess.run(["git", "-C", WEB_REPO, "worktree", "remove", "--force", wt], check=False)
    print("Worktrees de staging limpiados.")


def cmd_estado():
    c = load_current()
    print("== Antesala ==")
    print("  índice (localhost:%d): %s" % (INDEX_LOCAL, "vivo" if _port_open("127.0.0.1", INDEX_LOCAL) else "parado"))
    print("  relay índice  %s:%d : %s" % (TS_IP, INDEX_TS, "escuchando" if _port_open(TS_IP, INDEX_TS) else "no"))
    print("  relay app     %s:%d : %s" % (TS_IP, APP_TS, "escuchando" if _port_open(TS_IP, APP_TS) else "no"))
    if c:
        print("  cargado: %s (%s) — %s" % (c.get("kind"), c.get("status"), c.get("rama") or c.get("nombre") or ""))
        print("  app local :%s viva: %s" % (c.get("app_local"), _port_open("127.0.0.1", c.get("app_local", APP_LOCAL))))
        if c.get("pr_url"):
            print("  PR: %s" % c["pr_url"])
    else:
        print("  (nada cargado)")
    print("  URL para {{TITULAR}}: http://%s:%d" % (TS_IP, INDEX_TS))


# ──────────────────────────── main ────────────────────────────
def _flag(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "estado"
    if cmd == "web":
        if len(a) < 2:
            raise SystemExit("Uso: staging.py web <rama> [--modo prod|dev]")
        cmd_web(a[1], _flag(a, "--modo", "prod"))
    elif cmd == "port":
        if len(a) < 2:
            raise SystemExit("Uso: staging.py port <N> [--nombre X] [--pr URL]")
        cmd_port(a[1], _flag(a, "--nombre"), _flag(a, "--pr"))
    elif cmd == "on":
        cmd_on()
    elif cmd == "off":
        cmd_off()
    elif cmd == "limpiar":
        cmd_limpiar()
    elif cmd == "_serve":
        serve()
    elif cmd == "_static":
        serve_static(a[1], int(a[2]))
    else:
        cmd_estado()


if __name__ == "__main__":
    main()
