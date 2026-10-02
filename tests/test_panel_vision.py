#!/usr/bin/env python3
"""tests/test_panel_vision.py — arnés de calibración del panel de visión (`tools/panel_vision/`;
plan «laminillas DFCI», actualización 3: «cada modelo se usa solo para lo que demuestre detectar»).

TODO SINTÉTICO: tejido inventado, errores sembrados de tipo, posición y magnitud conocidos, y un
Ollama falso en 127.0.0.1. Ninguna lámina real, ninguna API externa: los adaptadores externos se
comprueban con `socket.connect` vigilado.

Verifica el EFECTO, no que corre:
  · el error sembrado está, es del tamaño declarado y la diferencia con su gemelo sin error queda
    DENTRO del cuadrante declarado; los colores y textos pasan el detector de trazos de exporta_n1;
  · la puntuación cuenta inválidas, silencios y errores del adaptador como fallos; Wilson da sus
    valores; el criterio deja fuera al modelo en las tareas que no demuestra, y solo en esas;
  · `ollama` solo habla con loopback, sin proxies, y rechaza modelos «cloud» o sin visión;
    `claude` lanza AdaptadorPendiente y `gemini` (conectado a vision_n1, modelo fijado) falla
    cerrado sin las condiciones de la auditoría, sin abrir un socket ni importar vision_n1;
    `medgemma` es local (sus casos con la Puerta, en test_laminillas_n1: MedGemma y PanelGemini;
    aquí, que sus pesos están sellados y su fila de calibración sale de ese commit);
  · solo la calibración a ciegas habilita (un transcrito sin --ciega o marcado «no ciega», no);
  · la siembra sobre capas N1 (`siembra_n1`) cambia solo el cuadrante declarado, con la magnitud
    que dice (recuentos, desplazamiento medido, barra medida), escribe solo en <n1>/panel_vision,
    pasa cada imagen por la Puerta y, con la Puerta de verdad (subproceso, casa base falsa), una
    capa con un apellido rotulado no entra. Las capas «del piloto» son sintéticas y viven en un
    directorio temporal: nunca el ~/Laminillas-N1 real.

La parte de imágenes necesita numpy/scipy/Pillow: el test se re-ejecuta con el venv `patologia`;
sin él, corre solo la parte stdlib y lo dice.
"""
import base64
import http.server
import json
import math
import os
import re
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest

VENV_DIR = os.environ.get("BTP_VENV_PATOLOGIA") or os.path.expanduser("~/.polaris-venvs/patologia")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
try:
    import numpy as np
except ImportError:
    np = None
    if os.path.exists(VENV_PY) and not os.environ.get("BTP_PANEL_VISION_REEXEC"):
        os.environ["BTP_PANEL_VISION_REEXEC"] = "1"
        os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
HAY_NUMPY = np is not None
if not HAY_NUMPY:
    print("AVISO: sin numpy ni venv patologia (%s): solo la parte stdlib" % VENV_DIR)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "tools", "panel_vision")
sys.path.insert(0, PANEL)
import calibra as C  # noqa: E402

LADO = 384


def _m(vp, ne, vn, nc):
    return {"vp": vp, "n_error": ne, "vn": vn, "n_control": nc}


def _json(h, t=None, q=None):
    return json.dumps({"hay_error": h, "tipo": t, "cuadrante": q})


# ── estadística y criterio ─────────────────────────────────────────────────────────────────
class Wilson(unittest.TestCase):
    def test_valores_conocidos(self):
        lo, hi = C.wilson(24, 30)
        self.assertAlmostEqual(lo, 0.6269, places=3)
        self.assertAlmostEqual(hi, 0.9050, places=3)
        lo, hi = C.wilson(30, 30)
        self.assertAlmostEqual(lo, 30 / (30 + C.Z95 ** 2), places=6)
        self.assertEqual(hi, 1.0)
        self.assertEqual(C.wilson(0, 0), (0.0, 1.0))
        lo, hi = C.wilson(0, 30)
        self.assertEqual(lo, 0.0)
        self.assertGreater(hi, 0.1)

    def test_ic_no_muerde_con_los_umbrales_vigentes(self):
        """Lo que dice el docstring: con n≥30 y p≥0,8, el límite inferior ya es ≥0,6."""
        for n in range(30, 301):
            k = math.ceil(0.8 * n)
            self.assertGreaterEqual(C.wilson(k, n)[0], 0.6, n)


class Criterio(unittest.TestCase):
    def test_pasa_justo(self):
        self.assertTrue(C.decide(_m(24, 30, 24, 30))["usar"])

    def test_sensibilidad_baja(self):
        d = C.decide(_m(23, 30, 30, 30))
        self.assertFalse(d["usar"])
        self.assertTrue(any("sensibilidad" in x for x in d["motivos"]))

    def test_especificidad_baja(self):
        d = C.decide(_m(30, 30, 23, 30))
        self.assertFalse(d["usar"])
        self.assertTrue(any("especificidad" in x for x in d["motivos"]))

    def test_n_insuficiente_aunque_acierte_todo(self):
        d = C.decide(_m(29, 29, 30, 30))
        self.assertFalse(d["usar"])
        self.assertTrue(any("29 casos con error < 30" in x for x in d["motivos"]))
        self.assertFalse(C.decide(_m(30, 30, 29, 29))["usar"])

    def test_el_ic_muerde_si_se_bajan_otros_umbrales(self):
        d = C.decide(_m(8, 10, 10, 10), {"n_min": 10})
        self.assertFalse(d["usar"])
        self.assertTrue(any("IC95 inferior de sensibilidad" in x for x in d["motivos"]))


# ── formato de pregunta y respuesta ────────────────────────────────────────────────────────
class Respuesta(unittest.TestCase):
    OK = _json(True, "omitido", "superior_izquierdo")

    def test_valida(self):
        r, motivo = C.interpreta(self.OK, "nucleos")
        self.assertIsNone(motivo)
        self.assertEqual(r, {"hay_error": True, "tipo": "omitido", "cuadrante": "superior_izquierdo"})
        self.assertEqual(C.interpreta(_json(False), "registro")[0]["hay_error"], False)

    def test_envoltorio_tolerado(self):
        for txt in ("```json\n%s\n```" % self.OK, "```\n%s\n```" % self.OK, "<think>mmm</think>\n%s" % self.OK,
                    "  %s  " % self.OK):
            self.assertIsNotNone(C.interpreta(txt, "nucleos")[0], txt)

    def test_invalidas(self):
        malas = [
            "Here it is: " + self.OK,                                         # prosa alrededor
            json.dumps({"hay_error": True, "tipo": "omitido", "cuadrante": "superior_izquierdo", "x": 1}),
            json.dumps({"hay_error": True, "tipo": "omitido"}),
            _json(True, "pliegue", "superior_izquierdo"),                      # tipo de otra tarea
            _json(True, "omitido", "centro"),
            json.dumps({"hay_error": "true", "tipo": "omitido", "cuadrante": "superior_izquierdo"}),
            json.dumps({"hay_error": 1, "tipo": "omitido", "cuadrante": "superior_izquierdo"}),
            _json(False, None, "superior_izquierdo"),
            _json(False, "omitido", None),
            _json(True, "omitido", None),
            "[%s]" % self.OK, "", "null", "no error",
        ]
        for txt in malas:
            r, motivo = C.interpreta(txt, "nucleos")
            self.assertIsNone(r, txt)
            self.assertTrue(motivo)
        self.assertIsNone(C.interpreta(None, "nucleos")[0])

    def test_esquema_cerrado(self):
        for t in C.TAREAS:
            e = C.esquema_respuesta(t)
            self.assertFalse(e["additionalProperties"])
            self.assertEqual(sorted(e["required"]), sorted(C.CLAVES))
            self.assertEqual(e["properties"]["tipo"]["enum"], list(C.TIPOS[t]) + [None])
            self.assertEqual(e["properties"]["cuadrante"]["enum"], list(C.CUADRANTES) + [None])


class Pregunta(unittest.TestCase):
    CTX = {"registro": {"mpp": 2.0}, "figura": {"marcador": "Ki-67", "mpp": 0.5, "escala_um": 50, "escala_px": 100}}

    def test_cada_tarea_nombra_su_vocabulario(self):
        for t in C.TAREAS:
            p = C.pregunta(t, self.CTX.get(t))
            for x in C.TIPOS[t] + C.CUADRANTES + C.CLAVES:
                self.assertIn('"%s"' % x if x in C.TIPOS[t] or x in C.CUADRANTES else x, p)
            self.assertNotRegex(p, r"\{(mpp|marcador|escala_um|escala_px|tipos)\}")
            for otra in C.TAREAS:
                if otra != t:
                    for x in C.TIPOS[otra]:
                        self.assertNotIn('"%s"' % x, p, (t, x))

    def test_contexto_cerrado(self):
        """La pregunta solo admite las claves declaradas: la verdad no puede colarse por ahí."""
        with self.assertRaises(ValueError):
            C.pregunta("nucleos", {"tipo": "omitido"})
        with self.assertRaises(ValueError):
            C.pregunta("figura", {"marcador": "RE"})
        with self.assertRaises(ValueError):
            C.pregunta("tarea_inventada")

    def test_huella(self):
        h = C.huella_preguntas()
        self.assertEqual(h, C.huella_preguntas())
        viejo = C._TAREA_TXT["nucleos"]
        try:
            C._TAREA_TXT["nucleos"] = viejo + " "
            self.assertNotEqual(h, C.huella_preguntas())
        finally:
            C._TAREA_TXT["nucleos"] = viejo


# ── puntuación y uso ───────────────────────────────────────────────────────────────────────
def _verdad(tarea, hay, tipo=None, q=None, variante=None):
    return {"tarea": tarea, "hay_error": hay, "tipo": tipo, "cuadrante": q, "magnitud": None, "unidad": None,
            "variante": variante or ("error" if hay else "correcto")}


class Puntuacion(unittest.TestCase):
    def test_recuentos_fail_closed(self):
        SI, SD = "superior_izquierdo", "superior_derecho"
        v = {"e1": _verdad("nucleos", True, "omitido", SI), "e2": _verdad("nucleos", True, "fusionado", SI),
             "e3": _verdad("nucleos", True, "omitido", SI), "e4": _verdad("nucleos", True, "omitido", SI),
             "c1": _verdad("nucleos", False), "c2": _verdad("nucleos", False),
             "c3": _verdad("nucleos", False), "c4": _verdad("nucleos", False),
             "x1": _verdad("ck19", True, "estroma_incluido", SI)}
        R = lambda h, t=None, q=None: {"resp": {"hay_error": h, "tipo": t, "cuadrante": q}}  # noqa: E731
        r = {"e1": R(True, "omitido", SI),          # acierto completo
             "e2": R(True, "omitido", SI),          # cuadrante bien, tipo mal: cuenta, tipo no
             "e3": R(True, "omitido", SD),          # cuadrante mal: fallo estricto, acierto laxo
             # e4: sin respuesta
             "c1": R(False), "c2": {"resp": None, "motivo": "no es JSON"}, "c3": {"error": "Timeout"},
             "c4": R(True, "omitido", SI), "x1": R(False)}
        m = C.puntua(v, r, "nucleos")
        esperado = {"n_error": 4, "n_control": 4, "vp": 2, "vp_mal_cuadrante": 1, "fn": 2, "vn": 1, "fp": 3,
                    "invalidas": 1, "errores_adaptador": 1, "sin_respuesta": 1, "tipo_ok": 1}
        self.assertEqual({k: m[k] for k in esperado}, esperado)
        self.assertEqual(m["sensibilidad"]["p"], 0.5)
        self.assertEqual(m["sensibilidad_laxa"]["p"], 0.75)
        self.assertEqual(m["especificidad"]["p"], 0.25)
        self.assertEqual(m["acierto_tipo"], 0.5)
        self.assertEqual(m["por_tipo"]["omitido"]["n"], 3)
        self.assertFalse(m["decision"]["usar"])


def _resultados(modelos):
    return {"protocolo": C.PROTOCOLO, "huella_preguntas": C.huella_preguntas(),
            "modelos": {k: {"tareas": {t: _m(*c) for t, c in ts.items()}} for k, ts in modelos.items()}}


class Uso(unittest.TestCase):
    def setUp(self):
        self.res = _resultados({"a@1": {"nucleos": (27, 30, 28, 30), "ck19": (20, 30, 30, 30)},
                                "b@2": {"nucleos": (30, 30, 10, 30)}})

    def test_cada_modelo_solo_donde_demuestra(self):
        self.assertEqual(C.modelos_autorizados(self.res, "nucleos"), ["a@1"])
        self.assertEqual(C.modelos_autorizados(self.res, "ck19"), [])
        self.assertEqual(C.modelos_autorizados(self.res, "registro"), [])      # sin calibrar = no

    def test_no_se_fia_del_usar_escrito(self):
        self.res["modelos"]["a@1"]["tareas"]["ck19"]["decision"] = {"usar": True, "motivos": []}
        self.assertEqual(C.modelos_autorizados(self.res, "ck19"), [])

    def test_otra_huella_no_autoriza(self):
        self.res["huella_preguntas"] = "0" * 64
        self.assertEqual(C.modelos_autorizados(self.res, "nucleos"), [])
        self.res["huella_preguntas"] = C.huella_preguntas()
        self.res["protocolo"] = "panel-vision/0"
        self.assertEqual(C.modelos_autorizados(self.res, "nucleos"), [])

    def test_desde_fichero(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "resultados.json")
            with open(ruta, "w") as fh:
                json.dump(self.res, fh)
            self.assertEqual(C.modelos_autorizados(ruta, "nucleos"), ["a@1"])


# ── recursos antes de cada lote ────────────────────────────────────────────────────────────
class Recursos(unittest.TestCase):
    def test_pct_libre(self):
        self.assertEqual(C.pct_libre("x\nPageouts: 5\n\nSystem-wide memory free percentage: 80%\n"), 80)
        with self.assertRaises(C.SinMemoria):
            C.pct_libre("nada")

    def _espera(self, libres, otros):
        libres, otros, sueños = list(libres), list(otros), []
        r = C.espera_recursos(lambda: otros.pop(0), libre=lambda: libres.pop(0), duerme=sueños.append)
        return r, sueños

    def test_pasa_sin_esperar(self):
        r, sueños = self._espera([40], [[]])
        self.assertEqual((r["libre_pct"], r["esperado_s"], sueños), (40, 0, []))

    def test_espera_memoria_y_otro_modelo(self):
        r, sueños = self._espera([20, 30, 30], [[], ["qwen3:8b (5.2 GB)"], []])
        self.assertEqual(sueños, [300, 300])
        self.assertEqual(r["esperado_s"], 600)

    def test_a_la_hora_para(self):
        sueños = []
        with self.assertRaises(C.SinMemoria) as cm:
            C.espera_recursos(lambda: [], libre=lambda: 10, duerme=sueños.append)
        self.assertEqual(sum(sueños), 3600)                 # 12 esperas de 5 min, 13 miradas
        self.assertIn("60 min", str(cm.exception))


# ── adaptadores ─────────────────────────────────────────────────────────────────────────────
class _SinRed:
    """Vigila socket.connect: cualquier intento queda anotado y falla."""

    def __enter__(self):
        self.intentos = []
        self._orig = socket.socket.connect
        intentos = self.intentos

        def connect(sock, addr):
            intentos.append(addr)
            raise OSError("red prohibida en este test")
        socket.socket.connect = connect
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._orig


class _CasaBase:
    """BTP_REPO apuntando a una casa base falsa (con o sin auditorias.json) mientras dura el bloque."""

    def __init__(self, auditoria=None):
        self.auditoria = auditoria

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="pv-casa-")
        if self.auditoria is not None:
            os.makedirs(os.path.join(self.tmp, "tools", "panel_vision"))
            with open(os.path.join(self.tmp, "tools", "panel_vision", "auditorias.json"), "w") as fh:
                json.dump({"gemini": self.auditoria}, fh)
        self.antes = os.environ.get("BTP_REPO")
        os.environ["BTP_REPO"] = self.tmp
        return self

    def __exit__(self, *exc):
        if self.antes is None:
            os.environ.pop("BTP_REPO", None)
        else:
            os.environ["BTP_REPO"] = self.antes
        shutil.rmtree(self.tmp, ignore_errors=True)


class Externos(unittest.TestCase):
    def test_lanzan_pendiente_sin_red(self):
        """Claude: interfaz pendiente. Gemini: conectado, pero sin `condiciones_cumplidas: true` en
        la auditoría de casa base cierra ANTES de importar vision_n1 (ni socket, ni estado)."""
        self.assertEqual(C.PROVEEDORES_EXTERNOS, ("claude", "gemini"))
        importado_antes = "vision_n1" in sys.modules
        sin_condiciones = {"veredicto": "apto con condiciones", "modelo": "gemini-3.1-pro-preview",
                           "condiciones_cumplidas": False}
        for p, modelo in (("claude", "modelo-x"), ("gemini", "")):
            a = C.adaptador(p + ":" + modelo)
            self.assertEqual(a.destino, "vision-n1:" + p)
            self.assertFalse(a.local)
            for aud in (None, sin_condiciones):
                with _CasaBase(aud), _SinRed() as red:
                    with self.assertRaises(C.AdaptadorPendiente) as cm:
                        a.comprueba()
                    self.assertIsInstance(cm.exception, NotImplementedError)
                    self.assertIn("vision_n1", str(cm.exception))
                    self.assertIn("legal-burocracia", str(cm.exception))
                    if p == "claude":
                        with self.assertRaises(NotImplementedError):
                            a.responder("p", "/no/existe.png", C.esquema_respuesta("nucleos"))
                self.assertEqual(red.intentos, [], p)
        self.assertEqual("vision_n1" in sys.modules, importado_antes)   # gemini cerró sin importarlo
        with self.assertRaises(ValueError):
            C.adaptador("gemini:modelo-x")                            # modelo fijado (auditoría 2-oct)
        self.assertEqual(C.adaptador("gemini").destino, "vision-n1:gemini")
        self.assertEqual(C.adaptador("gemini").modelo, "gemini-3.1-pro-preview")   # auditoría 2-oct
        for apagado in ("gemini:gemini-3-pro-preview", "gemini:gemini-3-pro"):
            with self.assertRaises(ValueError, msg=apagado):
                C.adaptador(apagado)
        with self.assertRaises(ValueError):
            C.adaptador("openai:gpt")


class HostLocal(unittest.TestCase):
    def test_solo_loopback(self):
        self.assertEqual(C.host_local("http://127.0.0.1:11434"), "http://127.0.0.1:11434")
        self.assertEqual(C.host_local("http://localhost:11434/"), "http://127.0.0.1:11434")
        self.assertEqual(C.host_local("http://[::1]:8080"), "http://[::1]:8080")
        self.assertEqual(C.host_local("http://127.0.0.2"), "http://127.0.0.2:11434")
        for malo in ("http://192.168.1.10:11434", "http://example.com:11434", "https://127.0.0.1:11434",
                     "http://127.0.0.1.nip.io:11434", "http://user:pw@127.0.0.1:11434", "http://127.0.0.1:11434/v1",
                     "http://0.0.0.0:11434", "http://10.0.0.1", "", None, "ftp://127.0.0.1"):
            with self.assertRaises(C.NoLocal, msg=malo):
                C.host_local(malo)

    def test_modelos_cloud_fuera(self):
        for m in ("qwen3-vl:235b-cloud", "gpt-oss:120b-cloud", "x:cloud", "cloud/qwen"):
            with self.assertRaises(C.NoLocal, msg=m):
                C.Ollama(m)
        C.Ollama("qwen3-vl:8b")
        C.Ollama("soundcloudish:1b")
        with self.assertRaises(C.NoLocal):
            C.Ollama("qwen3-vl:8b", host="http://192.168.1.2:11434")


class _FalsoOllama(http.server.BaseHTTPRequestHandler):
    show = {}
    tags = {}
    chat = {}
    ps = {}
    peticiones = []

    def log_message(self, *a):
        pass

    def _responde(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        type(self).peticiones.append(("GET", self.path, None))
        self._responde(type(self).ps if self.path == "/api/ps" else type(self).tags)

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).peticiones.append(("POST", self.path, cuerpo))
        self._responde(type(self).show if self.path == "/api/show" else {} if self.path == "/api/generate"
                       else type(self).chat)


class OllamaLocal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FalsoOllama)
        cls.host = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _FalsoOllama.show = {"capabilities": ["completion", "vision"],
                             "details": {"family": "falso", "parameter_size": "1B", "quantization_level": "Q4"}}
        _FalsoOllama.tags = {"models": [{"name": "falso-vl:1b", "model": "falso-vl:1b", "digest": "d1g3st"}]}
        _FalsoOllama.chat = {"message": {"role": "assistant", "content": _json(False)}}
        _FalsoOllama.ps = {"models": []}
        _FalsoOllama.peticiones = []

    def test_otros_grandes_y_descarga(self):
        o = C.Ollama("falso-vl:1b", host=self.host)
        _FalsoOllama.ps = {"models": [{"name": "falso-vl:1b", "size": 9 * 10 ** 9},     # él mismo: no cuenta
                                      {"name": "llama3.2:3b", "size": 2 * 10 ** 9},     # pequeño: no cuenta
                                      {"name": "qwen3:8b", "size": 5 * 10 ** 9}]}
        otros = o.otros_grandes()
        self.assertEqual(len(otros), 1)
        self.assertTrue(otros[0].startswith("qwen3:8b"))
        o.descarga()
        self.assertEqual(_FalsoOllama.peticiones[-1][1:], ("/api/generate", {"model": "falso-vl:1b", "keep_alive": 0}))

    def test_recursos_ollama_descarga_el_suyo_antes_de_medir(self):
        o = C.Ollama("falso-vl:1b", host=self.host)
        sueños = []
        r = C.recursos_ollama(o, libre=lambda: 60, duerme=sueños.append)        # no está cargado: no descarga
        self.assertEqual((r["libre_pct"], sueños), (60, []))
        self.assertFalse(any(p[1] == "/api/generate" for p in _FalsoOllama.peticiones))
        _FalsoOllama.ps = {"models": [{"name": "falso-vl:1b", "size": 9 * 10 ** 9}]}
        _FalsoOllama.peticiones = []
        C.recursos_ollama(o, libre=lambda: 60, duerme=sueños.append)
        rutas = [p[1] for p in _FalsoOllama.peticiones]
        self.assertEqual(rutas, ["/api/ps", "/api/generate", "/api/ps"])        # mira, descarga, mide
        self.assertEqual(sueños, [3.0])

    def test_comprueba_sella_digest(self):
        cfg = C.Ollama("falso-vl:1b", host=self.host).comprueba()
        self.assertEqual(cfg["digest"], "d1g3st")
        self.assertEqual(cfg["parametros"], "1B")

    def test_remoto_rechazado(self):
        _FalsoOllama.show = dict(_FalsoOllama.show, remote_host="https://ollama.com:443", remote_model="x")
        with self.assertRaises(C.NoLocal):
            C.Ollama("falso-vl:1b", host=self.host).comprueba()

    def test_sin_vision_rechazado(self):
        _FalsoOllama.show = {"capabilities": ["completion"]}
        with self.assertRaises(C.SinVision):
            C.Ollama("falso-vl:1b", host=self.host).comprueba()

    def test_peticion_cerrada(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "x.png")
            datos = b"\x89PNG\r\n\x1a\n-sintetico-"
            with open(ruta, "wb") as fh:
                fh.write(datos)
            o = C.Ollama("falso-vl:1b", host=self.host)
            esquema = C.esquema_respuesta("ck19")
            self.assertEqual(o.responder("¿error?", ruta, esquema), _json(False))
            _m_, ruta_api, cuerpo = _FalsoOllama.peticiones[-1]
            self.assertEqual(ruta_api, "/api/chat")
            self.assertEqual(cuerpo["model"], "falso-vl:1b")
            self.assertIs(cuerpo["stream"], False)
            self.assertEqual(cuerpo["format"], esquema)
            self.assertEqual(len(cuerpo["messages"]), 1)
            self.assertEqual(cuerpo["messages"][0]["images"], [base64.b64encode(datos).decode()])
            self.assertEqual(cuerpo["options"], {"temperature": 0.0, "seed": 0})
            self.assertNotIn("think", cuerpo)               # el modelo no declara «thinking»
            _FalsoOllama.show["capabilities"].append("thinking")
            o2 = C.Ollama("falso-vl:1b", host=self.host)
            o2.responder("¿error?", ruta, esquema)
            self.assertIs(_FalsoOllama.peticiones[-1][2]["think"], False)
            o3 = C.Ollama("falso-vl:1b", host=self.host, num_ctx=4096)        # contexto explícito
            o3.responder("¿error?", ruta, esquema)
            self.assertEqual(_FalsoOllama.peticiones[-1][2]["options"], {"temperature": 0.0, "seed": 0, "num_ctx": 4096})
            self.assertEqual(o3.config["num_ctx"], 4096)
            self.assertNotIn("num_ctx", o2.config)                             # otra configuración, otra clave
            self.assertNotEqual(C._clave_modelo(o2.nombre, o2.config), C._clave_modelo(o3.nombre, o3.config))
            for malo in (100, "4096", True, 4096.0):
                with self.assertRaises(ValueError, msg=malo):
                    C.Ollama("falso-vl:1b", host=self.host, num_ctx=malo)

    def test_ignora_proxies_del_entorno(self):
        viejo = {k: os.environ.get(k) for k in ("http_proxy", "HTTP_PROXY", "no_proxy", "NO_PROXY")}
        try:
            os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
            os.environ.pop("no_proxy", None)
            os.environ.pop("NO_PROXY", None)
            self.assertEqual(C.Ollama("falso-vl:1b", host=self.host).comprueba()["digest"], "d1g3st")
        finally:
            for k, v in viejo.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ── imágenes sintéticas ────────────────────────────────────────────────────────────────────
def _chunks(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    i, out = 8, []
    while i < len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        out.append((png[i + 4:i + 8].decode("ascii"), png[i + 8:i + 8 + n]))
        i += 12 + n
    return out


def _componentes(m):
    from scipy import ndimage as ndi
    return ndi.label(m, structure=np.ones((3, 3), bool))[1]


def _desplazamiento(a, b):
    """(dx, dy) en px de b respecto de a, por correlación cruzada (FFT)."""
    r = np.fft.ifft2(np.fft.fft2(b) * np.conj(np.fft.fft2(a))).real
    dy, dx = np.unravel_index(int(np.argmax(r)), r.shape)
    if dy > a.shape[0] // 2:
        dy -= a.shape[0]
    if dx > a.shape[1] // 2:
        dx -= a.shape[1]
    return dx, dy


@unittest.skipUnless(HAY_NUMPY, "sin numpy")
class Sinteticos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sinteticos as S
        cls.S = S

    def caso(self, tarea, semilla=3, tipo=None, q=None, variante=None, **kw):
        return self.S.genera_caso(tarea, semilla, LADO, tipo, q, variante, **kw)

    def test_lado_y_png_sin_metadatos(self):
        S = self.S
        c = S.genera_caso("figura", 1, 1024)
        png = S.png_bytes(c["rgb"])
        tipos = [t for t, _ in _chunks(png)]
        self.assertEqual(set(tipos), {"IHDR", "IDAT", "IEND"})
        w, h = struct.unpack(">II", _chunks(png)[0][1][:8])
        self.assertEqual((w, h), (1024, 1024))
        for malo in (1026, 1025, 254, 383, True, 512.0):
            with self.assertRaises(ValueError, msg=malo):
                S.genera_caso("figura", 1, malo)
        with self.assertRaises(ValueError):
            S.png_bytes(np.zeros((1100, 10, 3), np.uint8))

    def test_determinista(self):
        a = self.S.png_bytes(self.caso("nucleos", 5, "omitido", "inferior_derecho")["rgb"])
        b = self.S.png_bytes(self.caso("nucleos", 5, "omitido", "inferior_derecho")["rgb"])
        c = self.S.png_bytes(self.caso("nucleos", 6, "omitido", "inferior_derecho")["rgb"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_error_visible_y_confinado_a_su_cuadrante(self):
        """La diferencia con el gemelo sin error (misma semilla) existe y no sale del cuadrante."""
        S = self.S
        k = 0
        for tarea in C.TAREAS:
            for tipo in C.TIPOS[tarea]:
                q = C.CUADRANTES[k % 4]
                k += 1
                for semilla in (11, 12):
                    e = self.caso(tarea, semilla, tipo, q)
                    v = e["verdad"]
                    self.assertTrue(v["hay_error"])
                    self.assertEqual((v["tipo"], v["cuadrante"]), (tipo, q))
                    self.assertIsNotNone(v["magnitud"])
                    kw = {"disposicion": v["detalle"]["disposicion"]} if tarea == "figura" else {}
                    g = self.caso(tarea, semilla, **kw)
                    self.assertFalse(g["verdad"]["hay_error"])
                    diff = (e["rgb"] != g["rgb"]).any(axis=2)
                    self.assertGreater(int(diff.sum()), 20, (tarea, tipo, semilla))
                    fuera = diff & ~S.mascara_cuadrante(q, LADO)
                    self.assertEqual(int(fuera.sum()), 0, (tarea, tipo, semilla, q))

    def test_nucleos_recuentos_exactos(self):
        q = "superior_derecho"
        c = self.caso("nucleos", 21)
        n = int(c["verdad"]["detalle"]["n_nucleos"])
        self.assertEqual(_componentes(c["capas"]["contorno"]), n)          # un contorno por núcleo
        for tipo, signo in (("omitido", -1), ("fusionado", -1), ("fuera_de_nucleo", +1)):
            e = self.caso("nucleos", 21, tipo, q)
            k = e["verdad"]["magnitud"]
            self.assertEqual(_componentes(e["capas"]["contorno"]), n + signo * k, tipo)
            if tipo == "omitido":
                lab = e["capas"]["lab"]
                for nid in e["capas"]["sin_contorno"]:
                    ys, xs = np.nonzero(lab == nid)
                    self.assertTrue(self.S.mascara_cuadrante(q, LADO)[ys, xs].all())
                self.assertEqual(len(e["capas"]["sin_contorno"]), k)
            if tipo == "fusionado":
                self.assertEqual(len(e["capas"]["sin_contorno"]), 2 * k)
                self.assertEqual(_componentes(e["capas"]["extra"]), k)
            if tipo == "fuera_de_nucleo":
                self.assertEqual(int((e["capas"]["lab"][e["capas"]["falsos"]] > 0).sum()), 0)
                self.assertEqual(_componentes(e["capas"]["falsos"]), k)

    def test_ck19_area_y_naturaleza_del_cambio(self):
        from scipy import ndimage as ndi
        for semilla in (31, 32, 33):
            for tipo in C.TIPOS["ck19"]:
                e = self.caso("ck19", semilla, tipo, "inferior_izquierdo")
                cap = e["capas"]
                cambio = cap["mascara"] ^ cap["control"]
                self.assertAlmostEqual(float(cambio.sum()) * 0.25, e["verdad"]["magnitud"], delta=0.06)
                rgb = e["rgb"].astype(float)
                rb = rgb[..., 0] - rgb[..., 2]                       # marrón DAB: R > B
                estroma = ~ndi.binary_dilation(cap["epitelio"], iterations=3) & ~cap["contorno"]
                ref = float(rb[estroma].mean())
                r_menos_b = float(rb[cambio & ~cap["contorno"]].mean())
                if tipo == "estroma_incluido":
                    self.assertEqual(int((cambio & cap["epitelio"]).sum()), 0)
                    self.assertLess(r_menos_b, ref + 5, "el estroma incluido tiene color de estroma")
                else:
                    self.assertTrue(cambio.any())
                    self.assertFalse((cambio & cap["mascara"]).any(), "lo excluido queda fuera de la máscara")
                    self.assertGreater(r_menos_b, ref + 25, "el epitelio excluido es marrón (DAB)")

    def test_registro_desplazamiento_medido(self):
        S = self.S
        for semilla in (41, 42, 43):
            for q in C.CUADRANTES:
                e = self.caso("registro", semilla, "desalineacion", q)
                mpp = e["verdad"]["detalle"]["mpp"]
                d = e["verdad"]["magnitud"]
                self.assertTrue(S.ERROR_REGISTRO_UM[0] <= d <= S.ERROR_REGISTRO_UM[1])
                sel = S.mascara_cuadrante(q, LADO)
                ys, xs = np.nonzero(sel)
                sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
                dx, dy = _desplazamiento(e["capas"]["A"][sl], e["capas"]["B"][sl])
                self.assertAlmostEqual(math.hypot(dx, dy) * mpp, d, delta=1.5 * mpp + 2, msg=(semilla, q))
                for otro in C.CUADRANTES:
                    if otro != q:
                        self.assertLessEqual(e["verdad"]["detalle"]["desplazamiento_um"][otro], S.JITTER_REGISTRO_UM)
        g = self.caso("registro", 41)
        self.assertLessEqual(max(g["verdad"]["detalle"]["desplazamiento_um"].values()), self.S.JITTER_REGISTRO_UM)

    def _barra(self, c):
        x0, y0, x1, y1 = c["capas"]["cajas"]["escala"]
        caja = c["rgb"][y0:y1, x0:x1]
        ys, xs = np.nonzero((caja == np.array(self.S.COLOR_BARRA)).all(axis=2))
        return int(xs.max() - xs.min() + 1)

    def test_figura_elementos(self):
        S = self.S
        g = self.caso("figura", 51)
        self.assertEqual(self._barra(g), g["contexto"]["escala_px"])
        e = self.caso("figura", 51, "escala", "inferior_derecho")
        f = e["verdad"]["magnitud"]
        self.assertGreaterEqual(abs(f - 1), 0.4)
        self.assertEqual(self._barra(e), int(round(e["contexto"]["escala_um"] / S.MPP_FIGURA * f)))
        e = self.caso("figura", 51, "etiqueta", "superior_izquierdo")
        self.assertNotEqual(e["verdad"]["detalle"]["etiqueta_dibujada"], e["contexto"]["marcador"])
        self.assertNotIn('"%s"' % e["verdad"]["detalle"]["etiqueta_dibujada"], C.pregunta("figura", e["contexto"]))
        for tipo, esperado in ((None, S.COLOR_POS), ("leyenda", S.COLOR_NEG)):
            c = self.caso("figura", 52, tipo, "superior_derecho" if tipo else None)
            x0, y0, _x1, _y1 = c["capas"]["cajas"]["leyenda"]
            self.assertEqual(tuple(int(v) for v in c["rgb"][y0 + 3, x0 + 3]), esperado)

    def test_figura_sin_palabras_de_4_alfanumericos(self):
        """exporta_n1 rechaza una imagen N1 con ≥4 alfanuméricos seguidos leídos por OCR."""
        S = self.S
        textos = list(S.MARCADORES) + ["pos", "neg"] + ["%d um" % L for L in S.ESCALAS_FIGURA_UM]
        for t in textos:
            self.assertIsNone(re.search(r"[A-Za-z0-9]{4,}", t), t)

    def test_artefacto_marcado_no_es_error(self):
        S = self.S
        q = "inferior_derecho"
        naranja = np.array(S.COLOR_QC)
        for tipo in C.TIPOS["artefacto"]:
            m = self.caso("artefacto", 61, tipo, q, "marcado")
            self.assertFalse(m["verdad"]["hay_error"])
            self.assertEqual(m["verdad"]["detalle"]["cuadrante_artefacto"], q)
            px = (m["rgb"] == naranja).all(axis=2)
            self.assertGreater(int(px.sum()), 30)
            self.assertEqual(int((px & ~S.mascara_cuadrante(q, LADO)).sum()), 0)
            e = self.caso("artefacto", 61, tipo, q)
            self.assertTrue(e["verdad"]["hay_error"])
            self.assertEqual(int((e["rgb"] == naranja).all(axis=2).sum()), 0)
            diff = (m["rgb"] != e["rgb"]).any(axis=2)          # solo cambia el contorno
            self.assertTrue((diff <= px).all())
        limpio = self.caso("artefacto", 61, variante="limpio")
        self.assertEqual(int((limpio["rgb"] == naranja).all(axis=2).sum()), 0)

    def test_artefacto_es_el_que_dice(self):
        from scipy import ndimage as ndi
        q = "superior_izquierdo"
        g = self.caso("artefacto", 71)["rgb"].astype(float).sum(axis=2)
        for tipo in C.TIPOS["artefacto"]:
            e = self.caso("artefacto", 71, tipo, q)
            z = e["capas"]["zona"]
            x = e["rgb"].astype(float).sum(axis=2)
            if tipo == "desenfoque":
                nucleo = ndi.binary_erosion(z, iterations=6)
                self.assertLess(float(ndi.laplace(x)[nucleo].var()), 0.5 * float(ndi.laplace(g)[nucleo].var()))
            elif tipo == "pliegue":
                self.assertLess(float(x[z].mean()), float(g[z].mean()) - 40)
            else:
                anillo = z & ~ndi.binary_erosion(z, iterations=4)
                self.assertLess(float(x[anillo].mean()), float(g[anillo].mean()) - 40)

    def test_compatible_con_el_detector_de_trazos_de_exporta_n1(self):
        try:
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import exporta_n1
            from PIL import Image
        except Exception as e:  # noqa: BLE001
            self.skipTest("exporta_n1 no importa aquí (%s)" % type(e).__name__)
        casos = [("nucleos", "fuera_de_nucleo", None), ("ck19", "estroma_incluido", None),
                 ("registro", "desalineacion", None), ("figura", "leyenda", None),
                 ("artefacto", "burbuja", "marcado"), ("artefacto", "pliegue", None)]
        for tarea, tipo, var in casos:
            for semilla in (81, 82):
                c = self.caso(tarea, semilla, tipo, "superior_izquierdo", var)
                n, umbral = exporta_n1.trazos_de_rotulador(Image.fromarray(c["rgb"], "RGB"))
                self.assertLess(n, umbral, (tarea, tipo, semilla, n))
                g = self.caso(tarea, semilla)
                n, umbral = exporta_n1.trazos_de_rotulador(Image.fromarray(g["rgb"], "RGB"))
                self.assertLess(n, umbral, (tarea, "control", semilla, n))


# ── conjunto, correr y puntuar de punta a punta ────────────────────────────────────────────
class _Oraculo:
    """Responde con la verdad (para las tareas de `sabe`) y «sin error» en el resto."""
    local = True

    def __init__(self, dir_, sabe=C.TAREAS, nombre="oraculo:test", fallos=0):
        with open(os.path.join(dir_, "verdad.json")) as fh:
            self.verdad = json.load(fh)
        self.sabe, self.nombre, self.fallos = set(sabe), nombre, fallos
        self.config = {"adaptador": "oraculo", "sabe": sorted(self.sabe)}
        self.llamadas = 0

    def comprueba(self):
        return dict(self.config)

    def responder(self, prompt, ruta_png, esquema):
        self.llamadas += 1
        if self.fallos:
            self.fallos -= 1
            raise RuntimeError("caído")
        v = self.verdad[os.path.basename(ruta_png)[:-4]]
        if v["tarea"] in self.sabe and v["hay_error"]:
            return "```json\n" + _json(True, v["tipo"], v["cuadrante"]) + "\n```"
        return _json(False)


class _Mentiroso(_Oraculo):
    def responder(self, prompt, ruta_png, esquema):
        v = self.verdad[os.path.basename(ruta_png)[:-4]]
        return _json(True, C.TIPOS[v["tarea"]][0], "superior_izquierdo")


@unittest.skipUnless(HAY_NUMPY, "sin numpy")
class Conjunto(unittest.TestCase):
    N = 4

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="panel-vision-")
        cls.dir = os.path.join(cls.tmp, "set")
        cls.man = C.genera_conjunto(cls.dir, n=cls.N, semilla=5, lado=320)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copia(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        shutil.rmtree(d)
        shutil.copytree(self.dir, d, ignore=shutil.ignore_patterns("respuestas", "resultados.json"))
        return d

    def test_plan_equilibrado(self):
        plan = C.plan_conjunto(30, 7)
        self.assertEqual(plan, C.plan_conjunto(30, 7))
        self.assertEqual(len({p["id"] for p in plan}), len(plan))
        for t in C.TAREAS:
            err = [p for p in plan if p["tarea"] == t and p["clase"] == "error"]
            ctl = [p for p in plan if p["tarea"] == t and p["clase"] == "control"]
            self.assertEqual((len(err), len(ctl)), (30, 30))
            T = len(C.TIPOS[t])
            bloques = 30 // (4 * T)
            for tipo in C.TIPOS[t]:
                self.assertGreaterEqual(sum(p["tipo"] == tipo for p in err), 4 * bloques, (t, tipo))
            for q in C.CUADRANTES:
                self.assertGreaterEqual(sum(p["cuadrante"] == q for p in err), T * bloques, (t, q))
            if t == "artefacto":
                self.assertEqual(sum(p["variante"] == "marcado" for p in ctl), 15)
                self.assertEqual(sum(p["variante"] == "limpio" for p in ctl), 15)

    def test_nombres_opacos_y_sin_verdad_en_las_preguntas(self):
        preg = C._lee_jsonl(os.path.join(self.dir, "preguntas.jsonl"))
        self.assertEqual(len(preg), 2 * self.N * len(C.TAREAS))
        palabras = set(C.CUADRANTES) | {x for ts in C.TIPOS.values() for x in ts} | {"error", "control"}
        for p in preg:
            self.assertEqual(set(p), {"id", "tarea", "png", "prompt"})
            self.assertRegex(os.path.basename(p["png"]), r"^[0-9a-f]{12}\.png$")
        for f in os.listdir(os.path.join(self.dir, "casos")):
            self.assertFalse(any(w in f for w in palabras), f)
        with open(os.path.join(self.dir, "verdad.json")) as fh:
            verd = json.load(fh)
        for t in C.TAREAS:
            prompts = {p["prompt"] for p in preg if p["tarea"] == t}
            if t != "figura":                       # misma pregunta para errores y controles
                self.assertEqual(len(prompts), 1, t)
        for p in preg:
            if p["tarea"] == "figura" and verd[p["id"]]["tipo"] == "etiqueta":
                self.assertNotIn('"%s"' % verd[p["id"]]["detalle"]["etiqueta_dibujada"], p["prompt"])

    def test_integridad_y_no_sobrescribe(self):
        C.carga_conjunto(self.dir)
        with self.assertRaises(FileExistsError):
            C.genera_conjunto(self.dir, n=1, lado=320)
        d = self.copia()
        un_png = os.path.join(d, "casos", sorted(os.listdir(os.path.join(d, "casos")))[0])
        with open(un_png, "ab") as fh:
            fh.write(b"\0")
        with self.assertRaises(ValueError):
            C.carga_conjunto(d)
        d = self.copia()
        ruta = os.path.join(d, "verdad.json")
        with open(ruta) as fh:
            verd = json.load(fh)
        cid = next(k for k, v in verd.items() if v["hay_error"])
        verd[cid]["cuadrante"] = "inferior_derecho" if verd[cid]["cuadrante"] != "inferior_derecho" else "superior_izquierdo"
        with open(ruta, "w") as fh:
            json.dump(verd, fh)
        with self.assertRaises(ValueError):
            C.carga_conjunto(d)

    def test_semilla_sin_sitio_pasa_a_la_siguiente(self):
        import sinteticos as S
        plan = C.plan_conjunto(1, 9, ("figura",))
        mala = plan[0]["semilla"]
        orig = S.genera_caso

        def caprichoso(tarea, semilla, *a, **k):
            if semilla == mala:
                raise RuntimeError("sin sitio")
            return orig(tarea, semilla, *a, **k)
        S.genera_caso = caprichoso
        try:
            d = os.path.join(self.tmp, "reintento")
            C.genera_conjunto(d, n=1, semilla=9, lado=320, tareas=("figura",))
        finally:
            S.genera_caso = orig
        with open(os.path.join(d, "verdad.json")) as fh:
            verd = json.load(fh)
        self.assertEqual(verd[plan[0]["id"]]["semilla"], mala + 7919)
        C.carga_conjunto(d)

    def test_oraculo_reanudable_y_n_insuficiente(self):
        d = self.copia()
        o = _Oraculo(d)
        r = C.correr(d, o)
        self.assertEqual(r["nuevas"], 2 * self.N * len(C.TAREAS))
        self.assertEqual(C.correr(d, _Oraculo(d))["nuevas"], 0)              # reanudable
        res = C.puntua_conjunto(d)
        (clave, mod), = res["modelos"].items()
        for t, m in mod["tareas"].items():
            self.assertEqual((m["sensibilidad"]["p"], m["especificidad"]["p"]), (1.0, 1.0), t)
            self.assertEqual(m["acierto_tipo"], 1.0)
            self.assertFalse(m["decision"]["usar"])                          # n=4 < 30
        self.assertEqual(res["uso"], {t: [] for t in C.TAREAS})
        self.assertTrue(os.path.exists(os.path.join(d, "resultados.json")))
        self.assertEqual(C.modelos_autorizados(os.path.join(d, "resultados.json"), "nucleos"), [])

    def test_cada_modelo_solo_para_lo_que_demuestra(self):
        d = self.copia()
        C.correr(d, _Oraculo(d, sabe=("nucleos", "registro"), nombre="especialista"))
        C.correr(d, _Mentiroso(d, nombre="mentiroso"))
        laxo = {"n_min": self.N, "ic_inferior": 0.3}
        res = C.puntua_conjunto(d, umbral=laxo)
        esp = next(k for k in res["modelos"] if k.startswith("especialista@"))
        self.assertEqual(res["uso"], {"nucleos": [esp], "ck19": [], "registro": [esp], "figura": [], "artefacto": []})
        ment = next(v for k, v in res["modelos"].items() if k.startswith("mentiroso@"))
        for t, m in ment["tareas"].items():
            self.assertEqual(m["especificidad"]["p"], 0.0, t)
            self.assertFalse(m["decision"]["usar"])

    def test_errores_del_adaptador_paran_y_se_reintentan(self):
        d = self.copia()
        with self.assertRaises(RuntimeError):
            C.correr(d, _Oraculo(d, fallos=10))
        res = C.puntua_conjunto(d, escribe=False)
        (_c, mod), = res["modelos"].items()
        self.assertEqual(sum(m["errores_adaptador"] for m in mod["tareas"].values()), 3)
        r = C.correr(d, _Oraculo(d))
        self.assertEqual(r["nuevas"], 2 * self.N * len(C.TAREAS))           # los 3 caídos, otra vez
        res = C.puntua_conjunto(d, escribe=False)
        (_c, mod), = res["modelos"].items()
        self.assertEqual(sum(m["errores_adaptador"] for m in mod["tareas"].values()), 0)
        self.assertTrue(all(m["especificidad"]["p"] == 1.0 for m in mod["tareas"].values()))

    def test_externo_no_escribe_nada(self):
        d = self.copia()
        with _CasaBase(), _SinRed() as red:
            with self.assertRaises(C.AdaptadorPendiente):
                C.correr(d, C.adaptador("gemini:gemini-3.1-pro-preview"))
        self.assertEqual(red.intentos, [])
        self.assertFalse(os.path.exists(os.path.join(d, "respuestas")))

    def test_protocolo_cambiado_no_corre(self):
        d = self.copia()
        viejo = C._TAREA_TXT["ck19"]
        try:
            C._TAREA_TXT["ck19"] = viejo + " "
            with self.assertRaises(ValueError):
                C.correr(d, _Oraculo(d))
        finally:
            C._TAREA_TXT["ck19"] = viejo

    def test_recursos_antes_de_cada_lote(self):
        d = self.copia()
        llamadas = []
        r = C.correr(d, _Oraculo(d), lote=3, recursos=lambda: llamadas.append(1) or {"libre_pct": 50})
        total = 2 * self.N * len(C.TAREAS)
        self.assertEqual(r["nuevas"], total)
        self.assertEqual(len(llamadas), math.ceil(total / 3))

    def test_sin_memoria_para_y_conserva_lo_hecho(self):
        d = self.copia()
        n = [0]

        def recursos():
            n[0] += 1
            if n[0] == 3:
                raise C.SinMemoria("sin memoria")
            return {"libre_pct": 50}
        with self.assertRaises(C.SinMemoria):
            C.correr(d, _Oraculo(d), lote=4, recursos=recursos)
        regs = C._lee_jsonl(C._fichero_respuestas(d, C._clave_modelo("oraculo:test", _Oraculo(d).config)))
        self.assertEqual(len(regs), 8)                                     # dos lotes de 4
        self.assertEqual(C.correr(d, _Oraculo(d))["nuevas"], 2 * self.N * len(C.TAREAS) - 8)   # reanuda

    def _transcribe(self, d, ids, nombre="r.jsonl", malo=None):
        with open(os.path.join(d, "verdad.json")) as fh:
            verd = json.load(fh)
        ruta = os.path.join(d, nombre)
        with open(ruta, "w") as fh:
            for cid in ids:
                v = verd[cid]
                fh.write(json.dumps({"id": cid, "crudo": _json(True, v["tipo"], v["cuadrante"]) if v["hay_error"]
                                     else _json(False)}) + "\n")
            if malo:
                fh.write(json.dumps(malo) + "\n")
        return ruta, verd

    def test_transcrito_salta_lo_que_falta(self):
        d = self.copia()
        preg = C._lee_jsonl(os.path.join(d, "preguntas.jsonl"))
        ids = [p["id"] for p in preg if p["tarea"] == "registro"]
        ruta, _v = self._transcribe(d, ids)
        a = C.adaptador("transcrito:claude-sesion", respuestas=ruta, evaluador="Claude, Read, a ciegas", ciega=True)
        r = C.correr(d, a)
        self.assertEqual(r["nuevas"], len(ids))
        res = C.puntua_conjunto(d, umbral={"n_min": self.N, "ic_inferior": 0.3}, escribe=False)
        (clave, mod), = res["modelos"].items()
        self.assertTrue(clave.startswith("transcrito:claude-sesion@"))
        self.assertEqual(mod["config"]["evaluador"], "Claude, Read, a ciegas")
        self.assertIs(mod["config"]["ciega"], True)
        self.assertEqual(mod["tareas"]["registro"]["sensibilidad"]["p"], 1.0)
        self.assertEqual(mod["tareas"]["nucleos"]["sin_respuesta"], 2 * self.N)   # lo que falta, falla
        self.assertEqual(res["uso"]["registro"], [clave])
        self.assertEqual(res["uso"]["nucleos"], [])

    def test_solo_la_calibracion_ciega_habilita(self):
        """Un transcrito sin --ciega, o marcado «no ciega» al exportar, se informa y no habilita;
        el ciego, con los MISMOS aciertos, sí."""
        d = self.copia()
        preg = C._lee_jsonl(os.path.join(d, "preguntas.jsonl"))
        ruta, _v = self._transcribe(d, [p["id"] for p in preg if p["tarea"] == "registro"])
        C.correr(d, C.adaptador("transcrito:vidente", respuestas=ruta, evaluador="conocía el generador"))
        C.correr(d, C.adaptador("transcrito:ciego", respuestas=ruta, evaluador="Claude, a ciegas", ciega=True))
        laxo = {"n_min": self.N, "ic_inferior": 0.3}
        res = C.puntua_conjunto(d, umbral=laxo, escribe=False)
        ciego = next(k for k in res["modelos"] if k.startswith("transcrito:ciego@"))
        vidente = next(k for k in res["modelos"] if k.startswith("transcrito:vidente@"))
        self.assertTrue(res["modelos"][vidente]["tareas"]["registro"]["decision"]["usar"])   # pasa el criterio…
        self.assertEqual(res["uso"]["registro"], [ciego])                                   # …y no habilita
        self.assertIn("--ciega", C.excluido(res["modelos"][vidente]))
        res = C.puntua_conjunto(d, umbral=laxo, escribe=False,
                                no_ciegas={"transcrito:ciego": "el evaluador conocía el generador"})
        self.assertEqual(res["uso"]["registro"], [])
        self.assertEqual(C.excluido(res["modelos"][ciego]), "no ciega: el evaluador conocía el generador")
        salida = os.path.join(d, "cal.json")
        out = C.exporta_calibracion(d, salida, umbral=laxo, no_ciegas={vidente: "el evaluador conocía el generador"})
        fila = next(r for r in out["resumen"] if r["modelo"] == vidente and r["tarea"] == "registro")
        self.assertFalse(fila["habilitado"])
        self.assertIn("no ciega: el evaluador conocía el generador", fila["motivos"])
        self.assertTrue(next(r for r in out["resumen"] if r["modelo"] == ciego and r["tarea"] == "registro")
                        ["habilitado"])
        with open(salida) as fh:
            disco = json.load(fh)
        self.assertEqual(disco["modelos"][vidente]["no_ciega"], "el evaluador conocía el generador")
        disco["umbral"] = laxo
        self.assertEqual(C.tabla_uso(disco, laxo)["registro"], [ciego])       # la consulta, desde el fichero
        with self.assertRaises(ValueError):
            C.puntua_conjunto(d, escribe=False, no_ciegas={"transcrito:nadie": "x"})
        for malo in (["sin-igual"], ["=motivo"], ["clave="]):
            with self.assertRaises(ValueError):
                C.no_ciegas_de(malo)
        self.assertEqual(C.main(["exportar", "--dir", d, "--salida", salida, "--no-ciega",
                                 "transcrito:ciego=conocía el generador"]), 0)
        with open(salida) as fh:
            self.assertEqual(C.tabla_uso(json.load(fh), laxo)["registro"], [])

    def test_transcrito_exige_formato_y_evaluador(self):
        d = self.copia()
        ruta, _v = self._transcribe(d, [], malo={"id": "x", "crudo": "{}", "verdad": "chivato"})
        with self.assertRaises(ValueError):
            C.correr(d, C.adaptador("transcrito:x", respuestas=ruta, evaluador="yo"))
        for kw in ({"respuestas": ruta, "evaluador": " "}, {"respuestas": "/no/existe", "evaluador": "yo"}):
            with self.assertRaises(ValueError):
                C.adaptador("transcrito:x", **kw)
        with self.assertRaises(ValueError):
            C.adaptador("transcrito:con espacio", respuestas=ruta, evaluador="yo")

    def test_exportar_calibracion(self):
        d = self.copia()
        C.correr(d, _Oraculo(d, sabe=("ck19",), nombre="especialista"))
        salida = os.path.join(d, "calibracion_sintetica.json")
        laxo = {"n_min": self.N, "ic_inferior": 0.3}
        out = C.exporta_calibracion(d, salida, umbral=laxo, notas=["nota de prueba"])
        with open(salida) as fh:
            disco = json.load(fh)
        self.assertEqual(disco["sha_conjunto"], self.man["sha_conjunto"])
        self.assertEqual(len(disco["casos"]), 2 * self.N * len(C.TAREAS))
        (clave, resp), = disco["respuestas"].items()
        self.assertEqual(len(resp), len(disco["casos"]))
        cid = next(k for k, v in disco["casos"].items() if v["tarea"] == "ck19" and v["hay_error"])
        self.assertEqual(resp[cid], [True, disco["casos"][cid]["tipo"], disco["casos"][cid]["cuadrante"]])
        self.assertIn("nota de prueba", disco["limites"])
        self.assertEqual(len(disco["resumen"]), len(C.TAREAS))
        self.assertTrue(next(r for r in disco["resumen"] if r["tarea"] == "ck19")["habilitado"])
        self.assertEqual(out["uso"]["ck19"], [clave])
        # la consulta del panel recalcula con el UMBRAL vigente (n_min 30): con n=4, nadie
        self.assertEqual(C.modelos_autorizados(salida, "ck19"), [])
        self.assertEqual(C.main(["uso", "--fichero", salida]), 0)
        self.assertEqual(C.main(["exportar", "--dir", d, "--salida", salida]), 0)
        texto = json.dumps(disco, ensure_ascii=False)
        for prohibido in ("rgb", "png\"", "crudo"):
            self.assertNotIn(prohibido, texto)

    def test_cli(self):
        d = self.copia()
        with _SinRed():
            self.assertEqual(C.main(["correr", "--dir", d, "--modelo", "claude:opus"]), 3)
            self.assertEqual(C.main(["correr", "--dir", d, "--modelo", "ollama:x:1b-cloud"]), 3)
            self.assertEqual(C.main(["correr", "--dir", d, "--modelo", "ollama:x", "--host", "http://10.0.0.1:11434"]), 3)
        self.assertEqual(C.main(["generar", "--dir", d, "--n", "1", "--lado", "320"]), 2)
        C.correr(d, _Oraculo(d))
        self.assertEqual(C.main(["puntuar", "--dir", d]), 0)
        self.assertEqual(C.main(["uso", "--dir", d]), 0)


# ── contrato con `laminillas_proc tribunal-listo` (punto 5-bis del plan) ──────────────────────
class Auditorias(unittest.TestCase):
    def test_forma_y_gemini_no_apto_todavia(self):
        with open(os.path.join(PANEL, "auditorias.json"), encoding="utf-8") as fh:
            aud = json.load(fh)
        self.assertEqual(aud["gemini"]["veredicto"], "apto con condiciones")    # auditoría del 2-oct
        self.assertEqual(aud["gemini"]["modelo"], C.MODELO_EXTERNO_DEFECTO["gemini"])
        self.assertIn(aud["gemini"]["veredicto"], ("apto", "apto con condiciones", "no apto"))


@unittest.skipUnless(HAY_NUMPY, "sin numpy")
class ContratoTribunal(unittest.TestCase):
    """`exportar` y `revisar` producen exactamente lo que lee `laminillas_proc.tribunal_listo`, con
    el `calibra` DE VERDAD (no el falso de test_laminillas): sintética, N1 «real» (otro conjunto,
    puntuada después), capas del piloto, revisiones y auditorías."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import laminillas_proc
        cls.P = laminillas_proc
        cls.tmp = tempfile.mkdtemp(prefix="panel-tribunal-")
        cls.sint = os.path.join(cls.tmp, "sint")
        cls.real = os.path.join(cls.tmp, "real")
        C.genera_conjunto(cls.sint, n=30, semilla=11, lado=256, tareas=("registro",))
        C.genera_conjunto(cls.real, n=30, semilla=12, lado=256, tareas=("registro",))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.d = tempfile.mkdtemp(dir=self.tmp)
        self.repo = os.path.join(self.d, "repo")
        self.n1 = os.path.join(self.d, "n1")
        os.makedirs(os.path.join(self.repo, "tools", "panel_vision"))
        os.makedirs(os.path.join(self.n1, C.N1_PANEL))
        shutil.copy(os.path.join(PANEL, "auditorias.json"), os.path.join(self.repo, self.P.AUDITORIAS))
        for dd in (self.sint, self.real):
            shutil.rmtree(os.path.join(dd, "respuestas"), ignore_errors=True)
        self.viejo = C._ahora

    def tearDown(self):
        C._ahora = self.viejo

    def _exporta(self, dir_, salida, cuando):
        C._ahora = lambda: cuando
        C.correr(dir_, _Oraculo(dir_, nombre="oraculo:registro"))
        C.exporta_calibracion(dir_, salida)
        C._ahora = self.viejo

    def _capas(self, n=2):
        """n capas «del piloto» (PNG del conjunto real) en N1, con manifiesto y contexto."""
        preg = [p for p in C._lee_jsonl(os.path.join(self.real, "preguntas.jsonl"))][:n]
        man, lista, ctx = {}, [], {}
        for p in preg:
            nombre = os.path.basename(p["png"])
            shutil.copy(os.path.join(self.real, p["png"]), os.path.join(self.n1, nombre))
            with open(os.path.join(self.n1, nombre), "rb") as fh:
                man[nombre] = {"sha256": C._sha(fh.read())}
            lista.append(nombre)
            ctx[nombre] = {"mpp": 6.0}
        with open(os.path.join(self.n1, "manifiesto.json"), "w") as fh:
            json.dump({"version": 1, "ficheros": man}, fh)
        with open(os.path.join(self.n1, C.N1_PANEL, C.CAPAS_PILOTO), "w") as fh:
            json.dump({"tareas": {"registro": lista}, "contexto": ctx}, fh)
        return lista

    def _listo(self):
        return self.P.tribunal_listo(repo=self.repo, n1=self.n1, calibra=C, confia=lambda d: False, log=None)

    def test_de_punta_a_punta(self):
        sal_s = os.path.join(self.repo, self.P.CALIB_SINTETICA)
        sal_r = os.path.join(self.n1, self.P.N1_PANEL, self.P.CALIB_N1)
        self._exporta(self.sint, sal_s, "2026-10-02T10:00:00+00:00")
        r = self._listo()
        self.assertTrue(r["i"]["sintetica"]["vale"], r["i"])
        self.assertFalse(r["i"]["n1_real"]["vale"])                       # aún sin la N1 real
        self._exporta(self.real, sal_r, "2026-10-03T10:00:00+00:00")
        capas = self._capas()
        r = self._listo()
        self.assertTrue(r["i"]["n1_real"]["vale"], r["i"])
        with open(sal_r, encoding="utf-8") as fh:
            clave = next(iter(json.load(fh)["modelos"]))
        self.assertEqual(r["ii"]["habilitados"]["registro"], [clave])
        self.assertFalse(r["listo"])                                      # (ii) sin revisiones
        self.assertEqual({x["fichero"] for x in r["ii"]["sin_revisar"]}, set(capas))
        o = _Oraculo(self.real, nombre="oraculo:registro")
        rev = C.revisa_capas(self.n1, sal_r, o)
        self.assertEqual((rev["clave"], rev["tareas"], rev["nuevas"]), (clave, ["registro"], 2))
        self.assertEqual(C.revisa_capas(self.n1, sal_r, o)["nuevas"], 0)  # reanudable
        filas = C._lee_jsonl(os.path.join(self.n1, self.P.N1_PANEL, self.P.REVISIONES))
        self.assertTrue(all({"clave", "tarea", "fichero", "sha256"} <= set(f) and not f.get("error") for f in filas))
        r = self._listo()
        self.assertTrue(r["listo"], r["faltan"])
        # «apto con condiciones» habilita solo con las condiciones cumplidas (constan desde el 2-oct,
        # 11eaadb); en la copia se fijan las dos cosas para no depender de su estado de hoy.
        ruta_aud = os.path.join(self.repo, self.P.AUDITORIAS)
        with open(ruta_aud, encoding="utf-8") as fh:
            aud = json.load(fh)
        self.assertEqual(aud["gemini"]["veredicto"], "apto con condiciones")
        for cumplidas in (False, True):
            aud["gemini"]["condiciones_cumplidas"] = cumplidas
            with open(ruta_aud, "w", encoding="utf-8") as fh:
                json.dump(aud, fh)
            r = self._listo()
            self.assertEqual(r["iii"]["auditoria_apto"], cumplidas)
            self.assertFalse(r["iii"]["usa_gemini"])                      # sin Gemini calibrado ni trust-cloud
            self.assertTrue(r["listo"], r["faltan"])

    def test_misma_calibracion_o_anterior_no_vale(self):
        sal_s = os.path.join(self.repo, self.P.CALIB_SINTETICA)
        sal_r = os.path.join(self.n1, self.P.N1_PANEL, self.P.CALIB_N1)
        self._exporta(self.sint, sal_s, "2026-10-02T10:00:00+00:00")
        self._exporta(self.sint, sal_r, "2026-10-03T10:00:00+00:00")     # el mismo conjunto
        self.assertIn("mismo conjunto", self._listo()["i"]["n1_real"]["motivo"])
        self._exporta(self.real, sal_r, "2026-10-01T10:00:00+00:00")     # antes que la sintética
        self.assertIn("antes", self._listo()["i"]["n1_real"]["motivo"])

    def test_revisar_exige_la_configuracion_calibrada_y_el_sha(self):
        sal_r = os.path.join(self.n1, self.P.N1_PANEL, self.P.CALIB_N1)
        self._exporta(self.real, sal_r, "2026-10-03T10:00:00+00:00")
        capas = self._capas(1)
        with self.assertRaises(ValueError):
            C.revisa_capas(self.n1, sal_r, _Oraculo(self.real, nombre="otro"))
        with open(os.path.join(self.n1, capas[0]), "ab") as fh:           # la capa ya no es la del manifiesto
            fh.write(b"\0")
        C.revisa_capas(self.n1, sal_r, _Oraculo(self.real, nombre="oraculo:registro"))
        (fila,) = C._lee_jsonl(os.path.join(self.n1, self.P.N1_PANEL, self.P.REVISIONES))
        self.assertIn("sha256", fila["error"])
        with open(os.path.join(self.n1, C.N1_PANEL, C.CAPAS_PILOTO), "w") as fh:
            json.dump({"tareas": {"registro": capas}}, fh)                 # registro sin mpp
        with self.assertRaises(ValueError):
            C.revisa_capas(self.n1, sal_r, _Oraculo(self.real, nombre="oraculo:registro"))


# ── siembra sobre capas N1 del piloto (5-bis (i), segunda parte) ─────────────────────────────
NOMBRE_CAPA = {"nucleos": "T-NUC", "ck19": "T-CK19", "registro": "T-REG", "figura": "T-FIG", "artefacto": "T-ART"}


def _n1_falso(raiz, tareas=C.TAREAS, lado=512, semillas=(7,)):
    """Un ~/Laminillas-N1 FALSO en `raiz`: capas «del piloto» que imitan el formato N1 (PNG RGB con
    nombre opaco y mpp, sha256 en el manifiesto, capas_piloto.json con contexto y cajas). Los
    píxeles son los controles de `sinteticos` (tejido inventado). Nunca el ~/Laminillas-N1 real."""
    import sinteticos as S
    n1 = os.path.join(raiz, "Laminillas-N1")
    os.makedirs(os.path.join(n1, C.N1_PANEL))
    man, lista, ctx, siembra = {}, {}, {}, {}
    for t in tareas:
        for k, s in enumerate(semillas):
            c = S.genera_caso(t, s, lado, variante="limpio" if t == "artefacto" else None)
            mpp = c["contexto"].get("mpp", 0.5)
            nombre = "%s%s__L0__mpp%.4f.png" % (NOMBRE_CAPA[t], "-%d" % k if k else "", mpp)
            datos = S.png_bytes(c["rgb"])
            with open(os.path.join(n1, nombre), "wb") as fh:
                fh.write(datos)
            man[nombre] = {"sha256": C._sha(datos), "tipo": "png", "bytes": len(datos)}
            lista.setdefault(t, []).append(nombre)
            if t in ("registro", "figura"):
                ctx[nombre] = dict(c["contexto"])
            if t == "figura":
                siembra[nombre] = {"cajas": {e: list(v) for e, v in c["capas"]["cajas"].items()}}
    with open(os.path.join(n1, "manifiesto.json"), "w") as fh:
        json.dump({"version": 1, "ficheros": man}, fh)
    with open(os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO), "w") as fh:
        json.dump({"tareas": lista, "contexto": ctx, "siembra": siembra}, fh)
    return n1


class _PuertaFalsa:
    """Hace de Puerta en los tests en proceso: anota cada imagen, texto y capa que pasa por ella y
    puede rechazar la imagen n-ésima. La de verdad se prueba en `PuertaDeVerdad`."""
    nombre = "falsa (test)"

    def __init__(self, rechaza_en=None):
        self.fuentes, self.textos, self.imagenes, self.rechaza_en = [], [], 0, rechaza_en

    def fuente(self, ruta, sha256):
        with open(ruta, "rb") as fh:
            datos = fh.read()
        if C._sha(datos) != sha256:
            raise RuntimeError("sha256 distinto del manifiesto")
        self.fuentes.append(os.path.basename(ruta))
        return datos

    def imagen(self, rgb):
        import sinteticos as S
        self.imagenes += 1
        if self.imagenes == self.rechaza_en:
            raise RuntimeError("PuertaCerrada (falsa): texto en la imagen")
        return S.png_bytes(rgb)

    def texto(self, texto, donde):
        self.textos.append(donde)


@unittest.skipUnless(HAY_NUMPY, "sin numpy")
class SiembraN1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import siembra_n1 as N
        import sinteticos as S
        cls.N, cls.S = N, S
        cls.tmp = tempfile.mkdtemp(prefix="panel-n1-")
        cls.n1 = _n1_falso(cls.tmp)
        cls.capas = cls.N._carga_capas(cls.n1, _PuertaFalsa())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def caso(self, tarea, semilla, tipo=None, q=None, variante=None, lado=256):
        f = self.capas[tarea][0]
        return self.N.siembra_caso(f["rgb"], tarea, semilla, tipo, q, variante, f["mpp"], f["contexto"], f["cajas"],
                                   lado)

    def sembrado(self, tarea, tipo, q, variante=None, lado=256):
        for s in range(1, 60):
            try:
                return self.caso(tarea, s, tipo, q, variante, lado)
            except self.N.SinSitio:
                continue
        self.fail("sin sitio para %s %s en %s" % (tarea, tipo, q))

    def confinado(self, c):
        diff = (c["rgb"] != c["base"]).any(axis=2)
        q = c["verdad"]["cuadrante"] or c["verdad"]["detalle"].get("cuadrante_artefacto")
        H, W = diff.shape
        self.assertGreater(int(diff.sum()), 20, c["verdad"])
        self.assertEqual(int((diff & ~self.N.mascara_q(q, H, W)).sum()), 0, c["verdad"])
        return diff

    def test_nucleos_recuentos_exactos(self):
        amb = lambda c, k: _componentes(self.N._cerca(c[k], self.S.COLOR_NUCLEOS))  # noqa: E731
        for k, (tipo, signo) in enumerate((("omitido", -1), ("fusionado", -1), ("fuera_de_nucleo", +1))):
            c = self.sembrado("nucleos", tipo, C.CUADRANTES[k])
            v = c["verdad"]
            self.assertEqual((v["hay_error"], v["tipo"], v["cuadrante"]), (True, tipo, C.CUADRANTES[k]))
            self.confinado(c)
            self.assertEqual(amb(c, "rgb"), amb(c, "base") + signo * v["magnitud"], tipo)

    def test_ck19_un_nido_menos_o_un_estroma_mas(self):
        from scipy import ndimage as ndi

        def lab(c, k):
            return _componentes(ndi.binary_fill_holes(self.N._cerca(c[k], self.S.COLOR_CK19)))
        for tipo, signo in (("epitelio_excluido", -1), ("estroma_incluido", +1)):
            c = self.sembrado("ck19", tipo, "inferior_derecho")
            self.confinado(c)
            self.assertEqual(lab(c, "rgb"), lab(c, "base") + signo, tipo)
            self.assertGreater(c["verdad"]["magnitud"], 20)
            self.assertEqual(c["verdad"]["unidad"], "um2")
            _cH, dab = self.N._concentraciones(c["base"], self.S.VH, self.S.VDAB)
            cambio = (c["rgb"] != c["base"]).any(axis=2)
            if tipo == "estroma_incluido":                 # lo que encierra no es marrón
                dentro = ndi.binary_fill_holes(cambio) & ~cambio
                self.assertLess(float(dab[dentro].mean()), 0.1)

    def test_registro_desplazamiento_medido(self):
        mpp = self.capas["registro"][0]["mpp"]
        for q in C.CUADRANTES:
            c = self.sembrado("registro", "desalineacion", q)
            self.confinado(c)
            d = c["verdad"]["magnitud"]
            self.assertTrue(self.S.ERROR_REGISTRO_UM[0] <= d <= self.S.ERROR_REGISTRO_UM[1])
            _a0, b0, _r = self.N.separa_registro(c["base"])
            _a1, b1, _r = self.N.separa_registro(c["rgb"])
            y0, y1, x0, x1 = self.N._q(q, *b0.shape)
            dx, dy = _desplazamiento(b0[y0:y1, x0:x1], b1[y0:y1, x0:x1])
            self.assertAlmostEqual(math.hypot(dx, dy) * mpp, d, delta=1.5 * mpp + 2, msg=q)
        g = self.caso("registro", 3)
        self.assertFalse(g["verdad"]["hay_error"])
        self.assertLessEqual(g["verdad"]["detalle"]["jitter_um"], self.S.JITTER_REGISTRO_UM)

    def test_separa_registro_invierte_pinta_registro(self):
        rng = np.random.default_rng(1)
        wa, wb = rng.random((40, 40)), rng.random((40, 40))
        rgb = np.clip(np.rint(self.S.pinta_registro(wa, wb)), 0, 255).astype(np.uint8)
        a, b, r = self.N.separa_registro(rgb)
        self.assertLess(float(np.abs(self.S.pinta_registro(a, b) - rgb).max()), 4.0)
        self.assertLess(float(r.max()), 4.0)

    def test_figura_cada_elemento(self):
        f = self.capas["figura"][0]
        ctx, cajas = f["contexto"], f["cajas"]
        for tipo in C.TIPOS["figura"]:
            c = self.caso("figura", 2, tipo)
            v = c["verdad"]
            diff = self.confinado(c)
            x0, y0, x1, y1 = cajas[tipo]
            self.assertEqual(v["cuadrante"], self.N.cuadrante_de_caja(x0, y0, x1, y1, *diff.shape))
            if tipo == "escala":
                barra = self.N._cerca(c["rgb"], self.S.COLOR_BARRA, 6)
                ys, xs = np.nonzero(barra[y0:y1])
                self.assertEqual(int(xs.max() - xs.min() + 1), int(round(ctx["escala_px"] * v["magnitud"])))
                self.assertGreaterEqual(abs(v["magnitud"] - 1), 0.4)
            elif tipo == "leyenda":
                antes = self.N._cerca(c["base"][y0:y1, x0:x1], self.S.COLOR_POS, 4)
                self.assertTrue(antes.any())
                self.assertTrue(self.N._cerca(c["rgb"][y0:y1, x0:x1], self.S.COLOR_NEG, 4)[antes].all())
            else:
                self.assertNotEqual(v["detalle"]["etiqueta_dibujada"], ctx["marcador"])
                self.assertIsNone(re.search(r"[A-Za-z0-9]{4,}", v["detalle"]["etiqueta_dibujada"]))
        g = self.caso("figura", 2)
        self.assertTrue((g["rgb"] == g["base"]).all())

    def test_artefacto_sin_marcar_y_marcado(self):
        naranja = np.array(self.S.COLOR_QC)
        for tipo in C.TIPOS["artefacto"]:
            e = self.sembrado("artefacto", tipo, "superior_derecho")
            self.confinado(e)
            self.assertEqual(int((e["rgb"] == naranja).all(axis=2).sum()), 0)
            m = self.sembrado("artefacto", tipo, "superior_derecho", "marcado")
            self.assertFalse(m["verdad"]["hay_error"])
            self.assertEqual(m["verdad"]["detalle"]["cuadrante_artefacto"], "superior_derecho")
            self.confinado(m)
            self.assertGreater(int((m["rgb"] == naranja).all(axis=2).sum()), 30)

    def test_vista_y_controles(self):
        a = self.caso("nucleos", 5)
        b = self.caso("nucleos", 6)
        self.assertEqual(a["rgb"].shape, (256, 256, 3))
        self.assertTrue((a["rgb"] == a["base"]).all())
        self.assertFalse(np.array_equal(a["rgb"], b["rgb"]))           # otra vista, otra imagen
        self.assertEqual(set(a["vista"]), {"recorte", "orientacion"})

    def test_mpp_distinto_del_de_la_pregunta(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        n1 = _n1_falso(d, tareas=("ck19",), lado=256)
        ruta = os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO)
        with open(ruta) as fh:
            capas = json.load(fh)
        capas["contexto"] = {capas["tareas"]["ck19"][0]: {"mpp": 2.0044}}
        with open(ruta, "w") as fh:
            json.dump(capas, fh)
        with self.assertRaises(ValueError) as cm:
            self.N.genera_conjunto_n1(n1, n=1, lado=256, puerta=_PuertaFalsa())
        self.assertIn("0.5", str(cm.exception))

    def test_conjunto_formato_puerta_y_solo_en_panel(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        n1 = _n1_falso(d, lado=384)
        antes = sorted(os.listdir(n1))
        with open(os.path.join(n1, "manifiesto.json")) as fh:
            man_n1 = fh.read()
        p = _PuertaFalsa()
        for fuera in (os.path.join(d, "fuera"), n1, os.path.join(n1, C.N1_PANEL, "..", "x")):
            with self.assertRaises(ValueError):
                self.N.genera_conjunto_n1(n1, fuera, n=1, lado=256, puerta=p)
        self.assertEqual((p.fuentes, p.imagenes), ([], 0))                     # ni una capa leída
        man = self.N.genera_conjunto_n1(n1, n=2, lado=256, puerta=p)
        dir_ = os.path.join(n1, C.N1_PANEL, self.N.DIR_CONJUNTO)
        total = 2 * 2 * len(C.TAREAS)
        self.assertEqual(len(man["casos"]), total)
        self.assertEqual(p.imagenes, total)                                    # cada imagen, por la Puerta
        self.assertEqual(len(p.textos), total)                                 # y cada pregunta
        self.assertEqual(sorted(p.fuentes), sorted(f for t in C.TAREAS for f in man["origen"]["capas"][t]))
        self.assertEqual(man["origen"]["tipo"], "capas-n1-sembradas")
        _m, preg, verd = C.carga_conjunto(dir_)                                 # el formato de genera_conjunto
        self.assertEqual(man["huella_preguntas"], C.huella_preguntas())
        for pr in preg:
            self.assertRegex(os.path.basename(pr["png"]), r"^[0-9a-f]{12}\.png$")
            self.assertLessEqual({"capa", "sha256", "recorte", "orientacion"}, set(verd[pr["id"]]["origen"]))
        self.assertEqual(sorted(os.listdir(n1)), antes)                        # la raíz de N1, intacta
        with open(os.path.join(n1, "manifiesto.json")) as fh:
            self.assertEqual(fh.read(), man_n1)
        with self.assertRaises(FileExistsError):
            self.N.genera_conjunto_n1(n1, n=1, lado=256, puerta=p)
        # correr + exportar → calibracion_n1.json con su origen y sus límites
        C.correr(dir_, _Oraculo(dir_, nombre="oraculo:n1"))
        out = C.exporta_calibracion(dir_, os.path.join(n1, C.N1_PANEL, "calibracion_n1.json"))
        self.assertEqual(out["origen"]["tipo"], "capas-n1-sembradas")
        self.assertIn("REALES", out["que"])
        self.assertTrue(out["regenerar"].startswith("calibra.py sembrar-n1"))
        self.assertTrue(any("pocas capas" in x for x in out["limites"]))

    def test_rechazo_de_la_puerta_para_sin_manifiesto(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        n1 = _n1_falso(d, tareas=("nucleos",), lado=384)
        with self.assertRaises(RuntimeError):
            self.N.genera_conjunto_n1(n1, n=2, lado=256, puerta=_PuertaFalsa(rechaza_en=3))
        dir_ = os.path.join(n1, C.N1_PANEL, self.N.DIR_CONJUNTO)
        self.assertFalse(os.path.exists(os.path.join(dir_, "manifiesto.json")))
        self.assertEqual(len(os.listdir(os.path.join(dir_, "casos"))), 2)     # solo lo que pasó
        with open(os.path.join(n1, "manifiesto.json")) as fh:                  # una capa tocada no entra
            man = json.load(fh)
        nombre = next(iter(man["ficheros"]))
        with open(os.path.join(n1, nombre), "ab") as fh:
            fh.write(b"\0")
        with self.assertRaises(RuntimeError):
            self.N.genera_conjunto_n1(n1, os.path.join(n1, C.N1_PANEL, "otro"), n=1, lado=256,
                                      puerta=_PuertaFalsa())
        with open(os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO), "w") as fh:
            json.dump({"tareas": {"nucleos": ["no-esta.png"]}}, fh)
        with self.assertRaises(ValueError):
            self.N.genera_conjunto_n1(n1, os.path.join(n1, C.N1_PANEL, "otro2"), n=1, lado=256,
                                      puerta=_PuertaFalsa())

    def test_n1_real_para_el_tribunal(self):
        """sembrar-n1 → correr → exportar a calibracion_n1.json: tribunal-listo la da por buena
        (otro conjunto que la sintética, puntuada después) y habilita al que demostró."""
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import laminillas_proc as P
        d = tempfile.mkdtemp(dir=self.tmp)
        repo = os.path.join(d, "repo")
        os.makedirs(os.path.join(repo, "tools", "panel_vision"))
        shutil.copy(os.path.join(PANEL, "auditorias.json"), os.path.join(repo, P.AUDITORIAS))
        sint = os.path.join(d, "sint")
        C.genera_conjunto(sint, n=30, semilla=11, lado=256, tareas=("registro",))
        viejo = C._ahora
        try:
            C._ahora = lambda: "2026-10-02T10:00:00+00:00"
            C.correr(sint, _Oraculo(sint, nombre="oraculo:x"))
            C.exporta_calibracion(sint, os.path.join(repo, P.CALIB_SINTETICA))
            n1 = _n1_falso(d, tareas=("nucleos",), lado=512)
            C._ahora = lambda: "2026-10-03T10:00:00+00:00"
            self.N.genera_conjunto_n1(n1, n=30, lado=256, puerta=_PuertaFalsa())
            dir_ = os.path.join(n1, C.N1_PANEL, self.N.DIR_CONJUNTO)
            C.correr(dir_, _Oraculo(dir_, nombre="oraculo:x"))
            C.exporta_calibracion(dir_, os.path.join(n1, P.N1_PANEL, P.CALIB_N1))
        finally:
            C._ahora = viejo
        r = P.tribunal_listo(repo=repo, n1=n1, calibra=C, confia=lambda dd: False, log=None)
        self.assertTrue(r["i"]["n1_real"]["vale"], r["i"])
        (clave,) = r["ii"]["habilitados"]["nucleos"]
        self.assertTrue(clave.startswith("oraculo:x@"))


@unittest.skipUnless(HAY_NUMPY and shutil.which("tesseract"), "sin numpy o sin tesseract")
class PuertaDeVerdad(unittest.TestCase):
    """`sembrar-n1` con la Puerta de N1 de VERDAD (puerta_n1 + exporta_n1, OCR incluido) en un
    subproceso con una casa base falsa (BTP_REPO, BTP_STATE_DIR): una capa limpia da su conjunto;
    una capa con un apellido de la titular rotulado no entra y no queda conjunto."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="panel-n1-puerta-")
        casa = os.path.join(self.tmp, "casa")
        estado = os.path.join(casa, "tools", "state")
        os.makedirs(os.path.join(estado, "borde"))
        for nombre, dd in (("perfil.local.json", {"titular": {"nombre": "Leocadia Rosa",
                                                              "apellidos": "Quintanar Tallon"}}),
                           ("nombres.local.json", {"nombres": ["eustaquio", "fulgencio"]}),
                           ("identidad.local.json", {"ids": ["X9988776Q"]})):
            with open(os.path.join(casa, "tools", nombre), "w") as fh:
                json.dump(dd, fh)
        with open(os.path.join(estado, "borde", "canarios.json"), "w") as fh:
            json.dump(["CANARIO-7f3a9c11"], fh)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("BTP_")}
        self.env.update(BTP_REPO=casa, BTP_STATE_DIR=estado, BTP_HALT_FILES=os.path.join(self.tmp, "no-halt"),
                        BTP_TEST_BATTERY="1", BTP_N1_DIR=os.path.join(self.tmp, "Laminillas-N1"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sembrar(self, n1, *extra):
        import subprocess
        return subprocess.run([sys.executable, os.path.join(PANEL, "calibra.py"), "sembrar-n1", "--n1", n1,
                               "--n", "1", "--lado", "256"] + list(extra), capture_output=True, text=True,
                              timeout=900, env=self.env, cwd=self.tmp, stdin=subprocess.DEVNULL)

    def test_limpia_entra_y_rotulada_no(self):
        n1 = _n1_falso(self.tmp, tareas=("ck19",), lado=320)
        r = self.sembrar(n1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 casos sembrados sobre 1 capas N1", r.stdout)
        dir_ = os.path.join(n1, C.N1_PANEL, "conjunto_n1")
        man, _p, _v = C.carga_conjunto(dir_)
        self.assertIn("revalidar_n1", man["origen"]["puerta"])
        self.assertEqual(len(man["casos"]), 2)
        # una capa con el apellido rotulado en los píxeles: la Puerta no la deja entrar
        from PIL import Image, ImageDraw
        import sinteticos as S
        img = Image.fromarray(S.genera_caso("ck19", 9, 320)["rgb"])
        ImageDraw.Draw(img).text((20, 120), "QUINTANAR", fill=(60, 60, 60), font=S._fuente(40))
        datos = S.png_bytes(np.array(img))
        nombre = "T-CK19-9__L0__mpp0.5000.png"
        with open(os.path.join(n1, nombre), "wb") as fh:
            fh.write(datos)
        with open(os.path.join(n1, "manifiesto.json")) as fh:
            man_n1 = json.load(fh)
        man_n1["ficheros"][nombre] = {"sha256": C._sha(datos), "tipo": "png"}
        with open(os.path.join(n1, "manifiesto.json"), "w") as fh:
            json.dump(man_n1, fh)
        with open(os.path.join(n1, C.N1_PANEL, C.CAPAS_PILOTO), "w") as fh:
            json.dump({"tareas": {"ck19": [nombre]}}, fh)
        r = self.sembrar(n1, "--dir", os.path.join(n1, C.N1_PANEL, "rotulada"))
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("PuertaCerrada", r.stdout)
        self.assertIn("OCR", r.stdout)                                     # la paró el OCR, no otra cosa
        self.assertNotIn("QUINTANAR", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(n1, C.N1_PANEL, "rotulada", "manifiesto.json")))


class MedGemmaSellado(unittest.TestCase):
    """MedGemma 4B (2-oct-26): pesos bajados por la ventanilla y sellados en pesos.json (commit de 40
    hex, sha256 y bytes de cada fichero, `.sha256` que casa), y su fila en calibracion_sintetica.json
    hecha con ESE commit, sobre los 300 casos, con «habilitado» recalculable desde los recuentos."""

    PESOS = os.path.join(ROOT, "tools", "laminillas_stack", "pesos.json")
    CALIB = os.path.join(PANEL, "calibracion_sintetica.json")
    FICHEROS = {"README.md", "added_tokens.json", "chat_template.jinja", "config.json", "generation_config.json",
                "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
                "model.safetensors.index.json", "preprocessor_config.json", "processor_config.json",
                "special_tokens_map.json", "tokenizer.json", "tokenizer.model", "tokenizer_config.json"}

    def _entrada(self):
        import medgemma_local as M
        ent, motivo = M._entrada(self.PESOS)
        self.assertIsNone(motivo)
        return ent

    def test_pesos_sellados_por_la_ventanilla(self):
        import fnmatch
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import laminillas_pesos as LP
        finally:
            sys.path.pop(0)
        ent = self._entrada()                                  # .sha256 casa, repo, commit, estado OK
        self.assertRegex(ent["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(ent["sello"], "sellado-hoy")
        self.assertEqual(set(ent["ficheros"]), self.FICHEROS)
        for nombre, meta in ent["ficheros"].items():
            self.assertRegex(meta["sha256"], r"^[0-9a-f]{64}$", nombre)
            self.assertGreater(meta["bytes"], 0, nombre)
            self.assertTrue(any(fnmatch.fnmatch(nombre, p) for p in LP.MODELOS["medgemma"]["patrones"]), nombre)
        self.assertGreater(sum(m["bytes"] for m in ent["ficheros"].values()), 8 * 10 ** 9)   # bf16 entero
        self.assertEqual(LP.MODELOS["medgemma"]["repo"], ent["repo"])

    def test_calibrado_con_el_commit_sellado_y_decision_recalculable(self):
        ent = self._entrada()
        with open(self.CALIB, encoding="utf-8") as fh:
            cal = json.load(fh)
        claves = [k for k, d in cal["modelos"].items() if d["config"].get("adaptador") == "medgemma-local"]
        self.assertEqual(len(claves), 1, claves)
        clave = claves[0]
        d = cal["modelos"][clave]
        self.assertEqual(d["config"]["commit"], ent["commit"])
        self.assertEqual((d["config"]["decodificacion"], d["config"]["dispositivo"]), ("voraz", "mps"))
        self.assertNotIn("no_ciega", d)
        resp = cal["respuestas"][clave]
        self.assertEqual(len(resp), 300)
        self.assertTrue(all(r is not None for r in resp.values()))
        for t, m in d["tareas"].items():
            self.assertEqual(m["sensibilidad"]["n"] + m["especificidad"]["n"], 60, t)
            fila = next(f for f in cal["resumen"] if f["modelo"] == clave and f["tarea"] == t)
            self.assertEqual(fila["habilitado"], C.decide(m)["usar"], t)
            self.assertEqual(clave in C.modelos_autorizados(self.CALIB, t), fila["habilitado"], t)


if __name__ == "__main__":
    unittest.main(verbosity=1)
