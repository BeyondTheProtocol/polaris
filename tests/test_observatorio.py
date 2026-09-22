#!/usr/bin/env python3
"""test_observatorio.py — batería de El Observatorio (sala de control solo-lectura).

Verifica, SIN tocar la red ni enviar nada, que:
  · el host de escucha es loopback (jamás 0.0.0.0) — guardia del muro;
  · recopilar_todo() devuelve TODAS las secciones y aísla fallos (una caída no tumba el resto);
  · el servidor responde 200 en / (con el título) y JSON en /api/estado;
  · una ruta inexistente da 404;
  · `parte --dry` construye texto y NO envía (no llama a la boca de salida).

Si añades una fuente o cambias el contrato del JSON → añade el caso aquí (regresión permanente).
"""
import json
import os
import sys
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import observatorio as ob  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# 1) Guardia del muro: solo loopback.
check("HOST es loopback", ob._host_es_privado(ob.HOST))
check("rechaza 0.0.0.0", not ob._host_es_privado("0.0.0.0"))
check("rechaza IP de red", not ob._host_es_privado("192.168.1.50"))

# 2) recopilar_todo: contrato completo + aislamiento de fallos.
d = ob.recopilar_todo()
SECCIONES = ["config", "sesiones", "gasto", "rutinas", "cajas", "agentes", "hilos", "salud", "halt", "actividad", "borradores", "urls", "generado"]
for s in SECCIONES:
    check("recopilar_todo tiene '%s'" % s, s in d)
check("urls.movil apunta a Tailscale", ob.TS_IP in d.get("urls", {}).get("movil", ""))

# Aislamiento: si una fuente revienta, su tarjeta trae _error y el resto sigue.
_orig = ob.estado_salud
ob.estado_salud = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
d2 = ob.recopilar_todo()
check("una fuente caída no tumba el resto", "_error" in d2.get("salud", {}) and "config" in d2)
ob.estado_salud = _orig

# 2b) Señal de procesos (13-sep-26): anticipa el «fork failed» que dejó la shell muerta horas.
check("procesos: uso bajo → ok", ob.clasificar_procesos(100, 2666, 0)["nivel"] == "ok")
check("procesos: ≥60 % del límite → aviso", ob.clasificar_procesos(1700, 2666, 0)["nivel"] == "aviso")
check("procesos: ≥85 % del límite → alerta", ob.clasificar_procesos(2300, 2666, 0)["nivel"] == "alerta")
check("procesos: Chrome sin ventana acumulado → aviso aunque el uso sea bajo",
      ob.clasificar_procesos(100, 2666, 5)["nivel"] == "aviso")
check("procesos: límite desconocido no revienta (pct None, ok)",
      ob.clasificar_procesos(100, 0, 0)["pct"] is None and ob.clasificar_procesos(100, 0, 0)["nivel"] == "ok")
check("procesos: la alerta trae consejo de reiniciar", "reiniciar" in ob.clasificar_procesos(2300, 2666, 0)["consejo"])
_er = d.get("errores", {})
check("estado_errores incluye 'procesos' (medido o con _error aislado)",
      isinstance(_er, dict) and ("_error" in _er or "procesos" in _er))
if isinstance(_er, dict) and isinstance(_er.get("procesos"), dict) and "_error" not in _er["procesos"]:
    check("procesos medido de verdad: usados > 0 y límite > 0",
          _er["procesos"]["usados"] > 0 and _er["procesos"]["limite"] > 0)

# 3) Servidor HTTP en un puerto efímero de loopback.
from http.server import ThreadingHTTPServer  # noqa: E402
srv = ThreadingHTTPServer(("127.0.0.1", 0), ob.Handler)
port = srv.server_address[1]
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()
try:
    base = "http://127.0.0.1:%d" % port
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        body = r.read().decode("utf-8")
        check("GET / responde 200", r.status == 200)
        check("/ trae el título 'El Observatorio'", "El Observatorio" in body)
    with urllib.request.urlopen(base + "/api/estado", timeout=15) as r:
        api = json.loads(r.read().decode("utf-8"))
        check("/api/estado es JSON con config", r.status == 200 and "config" in api)
    try:
        urllib.request.urlopen(base + "/no-existe", timeout=10)
        check("ruta inexistente da 404", False)
    except urllib.error.HTTPError as e:
        check("ruta inexistente da 404", e.code == 404)
finally:
    srv.shutdown()

# 4) parte --dry: construye texto y NO envía (la boca de salida no se invoca).
import salida  # noqa: E402
_sent = []
_orig_send = salida.report_to_titular
_orig_alert = salida.alerta_critica
salida.report_to_titular = lambda *a, **k: (_sent.append(("report", k.get("dry"))) or {"dry": k.get("dry"), "delivered": False})
salida.alerta_critica = lambda *a, **k: (_sent.append(("alerta", None)) or {"delivered": True})
try:
    texto = ob.construir_parte()
    check("construir_parte da texto no vacío", isinstance(texto, str) and len(texto) > 20)
    check("el parte menciona el Observatorio", "Observatorio" in texto)
    ob.enviar_parte(dry=True)
    check("parte --dry NO entrega de verdad (dry)", _sent and _sent[-1] == ("report", True))
finally:
    salida.report_to_titular = _orig_send
    salida.alerta_critica = _orig_alert

# 5) Handler._send (item #24, 15-jul): si el cliente ya colgo (navegador cerrado/refrescado a
#    mitad de respuesta), _send NUNCA debe dejar escapar la excepcion -- eso es lo que llenaba
#    observatorio.err de tracebacks (BrokenPipeError al reintentar un 500 sobre la MISMA
#    conexion muerta). Pero SOLO para desconexiones de cliente: cualquier OTRO fallo debe
#    seguir subiendo (no queremos tragarnos un bug real).
class _FakeWfileRoto:
    def __init__(self, excepcion):
        self._exc = excepcion

    def write(self, data):
        raise self._exc


class _FakeHandlerSend:
    def __init__(self, excepcion):
        self.wfile = _FakeWfileRoto(excepcion)
        self.cabeceras = []

    def send_response(self, code):
        self.cabeceras.append(("status", code))

    def send_header(self, k, v):
        self.cabeceras.append((k, v))

    def end_headers(self):
        pass


for exc in (BrokenPipeError(32, "Broken pipe"),
            ConnectionResetError(54, "Connection reset by peer"),
            ConnectionAbortedError(53, "Software caused connection abort")):
    fh = _FakeHandlerSend(exc)
    try:
        ob.Handler._send(fh, 200, "text/plain; charset=utf-8", b"hola")
        ok = True
    except Exception:
        ok = False
    check("_send absorbe %s del cliente sin propagar" % type(exc).__name__, ok)

fh_otro = _FakeHandlerSend(ValueError("esto NO es una desconexion de cliente"))
try:
    ob.Handler._send(fh_otro, 200, "text/plain; charset=utf-8", b"hola")
    check("_send NO se traga un ValueError real (sigue subiendo)", False)
except ValueError:
    check("_send NO se traga un ValueError real (sigue subiendo)", True)

print("test_observatorio: %d OK, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
