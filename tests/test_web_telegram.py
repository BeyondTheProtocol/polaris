#!/usr/bin/env python3
"""test_web_telegram.py — su «sí» a un Telegram publica ESE texto de la web, y nada más.

POR QUÉ EXISTE (14-oct-2026, plan «guardián de la web» v3). {{TITULAR}}: *«yo lo que quiero es que me
llegue un telegram y me diga apruebas este texto y chimpún»*. Este test fija lo que eso no puede
aflojar:

  1. el Telegram ES el texto que se publica, con la orden de aprobar y la invitación a responder;
  2. su «sí» respondiendo al aviso aprueba; un «sí» suelto, un mensaje editado o un «sí, pero…»
     no;
  3. la fusión vuelve a exigir el código del Telegram: llamarla a mano con un «aprobado»
     inventado no publica nada;
  4. si cambian el PR, su versión o el texto que saldría (porque `main` se movió debajo), no sale;
  5. lo de hero, que por la vía de sesión exige nombrar el PR, sí sale aquí: ella lo leyó entero.

Nada toca la red ni GitHub.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="webtg-")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402
import web_publicar as WP  # noqa: E402
import web_lote as L  # noqa: E402

SHA = "c" * 40
TEXTOS = [("content/es/timeline.yml", "", "", "- date: '13 oct 2026'\n  title: Una novedad")]


class _R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


def _sandbox():
    salida.OUTBOX = os.path.join(tempfile.mkdtemp(prefix="outbox-"), "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    avisos = []
    salida.report_to_titular = lambda texto, **k: (avisos.append(texto) or {"delivered": True})
    # Como el bot o launchd: este proceso SÍ puede escribirle (chat y token legibles).
    salida._self_chatid = lambda: "123"
    salida.get_secret = lambda *a, **k: "token-de-prueba"
    return avisos


def _pr(numero=268, ci="verde"):
    return {"number": numero, "state": "OPEN", "headRefOid": SHA, "ci": ci,
            "preview": "https://deploy-preview-%d--helptitular-web.netlify.app" % numero}


def _pide(numero=268, textos=TEXTOS):
    avisos = _sandbox()
    nombre, motivo = L.pedir_ok(numero, _lee=lambda n: _pr(n), _textos=lambda *a: textos)
    return nombre, motivo, avisos


class ElAviso(unittest.TestCase):

    def test_el_telegram_es_el_texto_y_trae_como_aprobar(self):
        nombre, _, avisos = _pide()
        self.assertTrue(nombre)
        self.assertEqual(1, len(avisos))
        aviso = avisos[0]
        self.assertIn("Una novedad", aviso)
        self.assertIn("deploy-preview-268", aviso)
        self.assertIn("Responde «sí» a ESTE mensaje", aviso)
        self.assertIn("aprobar %s " % nombre, aviso)

    def test_si_este_proceso_no_puede_escribirle_no_queda_nada_en_disco(self):
        """14-oct-26, PR #272: desde una sesión (sin Llavero) el aviso se guardó como borrador,
        con el nonce en claro dentro, y la herramienta dijo que le había llegado."""
        avisos = _sandbox()
        salida._self_chatid = lambda: None             # como una sesión de Claude Code
        llamadas = []
        salida.report_to_titular = lambda texto, **k: llamadas.append(texto) or {"delivered": False}
        nombre, motivo = L.pedir_ok(268, _lee=lambda n: _pr(n), _textos=lambda *a: TEXTOS)
        self.assertIsNone(nombre)
        self.assertIn("NO le ha llegado", motivo)
        self.assertEqual([], llamadas, "ni se intenta: report_to_titular lo guardaría en disco")
        pendientes = os.listdir(salida.PENDING) if os.path.isdir(salida.PENDING) else []
        self.assertEqual([], pendientes, "ningún borrador con su código se queda esperando")

    def test_sin_texto_no_se_le_pide_nada(self):
        nombre, motivo, avisos = _pide(textos=[])
        self.assertIsNone(nombre)
        self.assertEqual([], avisos)
        self.assertIn("no cambia ningún texto", motivo)

    def test_sin_ci_verde_no_se_le_pide_nada(self):
        _sandbox()
        nombre, motivo = L.pedir_ok(268, _lee=lambda n: _pr(n, ci="pendiente"),
                                    _textos=lambda *a: TEXTOS)
        self.assertIsNone(nombre)
        self.assertIn("verde", motivo)

    def test_un_texto_largo_llega_entero_en_trozos(self):
        largo = [("content/es/timeline.yml", "", "", "§" * 9000)]
        nombre, _, avisos = _pide(textos=largo)
        self.assertGreater(len(avisos), 2)
        self.assertEqual(9000, sum(a.count("§") for a in avisos[:-1]), "llega entero, sin cortar")
        self.assertIn("aprobar %s " % nombre, avisos[-1])
        self.assertTrue(all(len(a) <= 4096 for a in avisos))


class SuRespuesta(unittest.TestCase):

    CITA = "🌐 Web · ¿apruebas este texto?\n…\naprobar 2026-x-web-publish-ab.json 0123456789abcdef"

    def test_si_respondiendo_al_aviso(self):
        for si in ("sí", "Si", "sí!", "ok", "vale", "dale", "Apruebo", "adelante", "publícalo", "sí 👍"):
            self.assertEqual(("2026-x-web-publish-ab.json", "0123456789abcdef"),
                             salida.aprobacion_por_respuesta(si, self.CITA), si)

    def test_el_aviso_tal_cual_le_llega_tras_la_casa_de_estilo(self):
        """14-oct-26: el primer «sí» real no se reconoció porque la casa de estilo añade « 💜»
        a la última línea del aviso, que es la de la orden. Se prueba con el texto que SALE."""
        import importlib
        real = importlib.reload(salida)              # sin los parches de otras pruebas
        try:
            nombre, _, avisos = _pide()
            final = real._casa_estilo(avisos[0], voz="calida", tipo="report")
            par = real.aprobacion_por_respuesta("si", final)
            self.assertIsNotNone(par, "el bot tiene que reconocer el aviso tal como ella lo recibe")
            self.assertEqual(nombre, par[0])
            self.assertEqual(268, real.cambios_a_aviso_web("añade un enlace", final))
        finally:
            importlib.reload(salida)

    def test_lo_que_no_es_un_si_limpio_no_aprueba(self):
        for no in ("no", "sí, pero cambia la fecha", "espera", "¿esto qué es?", ""):
            self.assertIsNone(salida.aprobacion_por_respuesta(no, self.CITA), no)
        self.assertIsNone(salida.aprobacion_por_respuesta("sí", None), "un sí suelto no aprueba")
        self.assertIsNone(salida.aprobacion_por_respuesta("sí", "un mensaje cualquiera del bot"))

    AVISO_WEB = ("🌐 Web · ¿apruebas este texto? (PR #272)\n…\n"
                 "aprobar 2026-10-14T140235-web-publish-a4d6f96bc0ad.json 0123456789abcdef")

    def test_una_respuesta_con_cambios_es_una_correccion_de_ese_pr(self):
        """14-oct-26: «añade link a x…» respondiendo al aviso del #272 se perdió en el triaje."""
        self.assertEqual(272, salida.cambios_a_aviso_web(
            "añade link a x anunciando que ya he empezado", self.AVISO_WEB))
        self.assertIsNone(salida.cambios_a_aviso_web("sí", self.AVISO_WEB), "un sí aprueba, no corrige")
        self.assertIsNone(salida.cambios_a_aviso_web("cambia esto", None))
        self.assertIsNone(salida.cambios_a_aviso_web("cambia esto", self.CITA),
                          "responder a un aviso que no es de la web no es una corrección web")

    def test_el_bot_apunta_los_cambios_y_no_los_manda_al_triaje(self):
        import bot_telegram as B
        import web_guardian
        apuntados, respuestas = [], []
        viejos = (salida.react_to_titular, salida.report_to_titular, B.healthcheck.mark_seen,
                  web_guardian.encargar_cambios)
        salida.react_to_titular = lambda *a, **k: None
        salida.report_to_titular = lambda t, **k: respuestas.append(t) or {"delivered": True}
        B.healthcheck.mark_seen = lambda *a, **k: None
        web_guardian.encargar_cambios = lambda n, t: apuntados.append((n, t))
        try:
            r = B.handle_message({"text": "añade link a x", "cita": self.AVISO_WEB, "message_id": 9})
        finally:
            (salida.react_to_titular, salida.report_to_titular, B.healthcheck.mark_seen,
             web_guardian.encargar_cambios) = viejos
        self.assertEqual("cambios-web:272", r)
        self.assertEqual([(272, "añade link a x")], apuntados)
        self.assertTrue(any("PR #272" in x and "No se publica nada" in x for x in respuestas))

    def test_encargar_cambios_lo_deja_para_quien_lo_aplique(self):
        import web_guardian
        import shutil
        shutil.rmtree(web_guardian.CAMBIOS, ignore_errors=True)
        web_guardian.encargar_cambios(272, "añade link a x")
        pend = web_guardian.cambios_pendientes()
        self.assertEqual(1, len(pend))
        self.assertEqual(272, pend[0]["pr"])
        with open(web_guardian.BUZON, encoding="utf-8") as f:
            self.assertIn("PR #272", f.read().splitlines()[-1])

    def test_el_bot_aprueba_con_su_si_y_no_con_un_mensaje_editado(self):
        import bot_telegram as B
        llamadas = []
        viejos = (salida.approve_and_deliver, salida.react_to_titular, salida.report_to_titular,
                  B.healthcheck.mark_seen)
        salida.approve_and_deliver = lambda n, c: llamadas.append((n, c)) or {"delivered": True, "reason": "ok"}
        salida.react_to_titular = lambda *a, **k: None
        salida.report_to_titular = lambda *a, **k: {"delivered": True}
        B.healthcheck.mark_seen = lambda *a, **k: None
        try:
            r = B.handle_message({"text": "sí", "cita": self.CITA, "message_id": 1})
            self.assertEqual("aprobacion-si:ok", r)
            self.assertEqual([("2026-x-web-publish-ab.json", "0123456789abcdef")], llamadas)
            B.handle_message({"text": "sí", "cita": self.CITA, "message_id": 2, "editado": True})
            self.assertEqual(1, len(llamadas), "un «sí» editado no aprueba")
        finally:
            (salida.approve_and_deliver, salida.react_to_titular, salida.report_to_titular,
             B.healthcheck.mark_seen) = viejos


def _aprobado_en_sending():
    """Un borrador web real, sellado, y ya reclamado a sending/ como haría approve_and_deliver.
    Devuelve (nombre, nonce)."""
    nombre, _, avisos = _pide()
    nonce = [x for x in avisos[0].splitlines() if x.startswith("aprobar ")][0].split()[2]
    os.makedirs(salida._sub("sending"), exist_ok=True)
    os.rename(os.path.join(salida.PENDING, nombre), os.path.join(salida._sub("sending"), nombre))
    return nombre, nonce


def _fusiona(aprobado, cabeza=SHA, textos=TEXTOS, frena=()):
    merges = []
    res = WP.fusionar(268, por="telegram", aprobado=aprobado, tope_ci_s=0, cwd=_TMP,
                      _info=lambda r, c=None: {"number": 268, "headRefOid": cabeza, "state": "OPEN"},
                      _ci=lambda r, c=None, t=None: ("verde", {"headRefOid": cabeza}),
                      _copy=lambda *a: (list(frena), []), _fetch=lambda *a: None,
                      _textos=lambda *a: textos,
                      _permiso=lambda q: (_ for _ in ()).throw(AssertionError("no debe pedir permiso de sesión")),
                      _merge=lambda cmd, cwd=None: merges.append(cmd) or _R())
    return res, merges


class LaFusion(unittest.TestCase):

    def test_con_su_si_sale_atado_a_la_version(self):
        res, merges = _fusiona(_aprobado_en_sending())
        self.assertTrue(res["merged"], res.get("motivo_no_merge"))
        self.assertEqual(SHA, merges[0][merges[0].index("--match-head-commit") + 1])

    def test_un_aprobado_inventado_no_publica(self):
        """Llamar a fusionar a mano desde Python con un «aprobado» no basta: hace falta el
        código que solo le llegó a ella."""
        nombre, _ = _aprobado_en_sending()
        for falso in ((nombre, "0" * 16), ("no-existe.json", "0" * 16)):
            res, merges = _fusiona(falso)
            self.assertFalse(res["merged"], falso)
            self.assertEqual([], merges)

    def test_si_cambia_la_version_no_sale(self):
        res, merges = _fusiona(_aprobado_en_sending(), cabeza="d" * 40)
        self.assertFalse(res["merged"])
        self.assertEqual([], merges)

    def test_si_el_texto_que_saldria_ya_no_es_el_que_leyo_no_sale(self):
        otro = [("content/es/timeline.yml", "", "", "- date: '13 oct 2026'\n  title: Otra cosa")]
        res, merges = _fusiona(_aprobado_en_sending(), textos=otro)
        self.assertFalse(res["merged"])
        self.assertIn("ya no es el que aprobaste", res["motivo_no_merge"])
        self.assertEqual([], merges)

    def test_el_hero_sale_porque_lo_leyo_entero(self):
        res, _ = _fusiona(_aprobado_en_sending(), frena=["i18n/locales/es.json: hero/H1 «hero.title»"])
        self.assertTrue(res["merged"], res.get("motivo_no_merge"))


class ElOutbox(unittest.TestCase):

    def test_el_destino_web_tiene_que_ser_el_pr_sellado(self):
        ok = {"pr": 268, "sha": SHA}
        self.assertTrue(salida._web_dest_ok("BeyondTheProtocol/titular-{{APELLIDO}}-case#268", ok))
        self.assertFalse(salida._web_dest_ok("BeyondTheProtocol/titular-{{APELLIDO}}-case#269", ok))
        self.assertFalse(salida._web_dest_ok("otro/repo#268", ok))
        self.assertFalse(salida._web_dest_ok("BeyondTheProtocol/titular-{{APELLIDO}}-case#268", {"pr": 268}))

    def test_aprobar_lo_reclama_y_lo_publica_por_la_web(self):
        nombre, _, avisos = _pide()
        nonce = [x for x in avisos[0].splitlines() if x.startswith("aprobar ")][0].split()[2]
        vistos = []
        viejo = salida._entregar_web
        salida._entregar_web = lambda b, n: vistos.append((b, n)) or ("ok", "comprobado en vivo")
        try:
            res = salida.approve_and_deliver(nombre, nonce)
        finally:
            salida._entregar_web = viejo
        self.assertTrue(res["delivered"], res.get("reason"))
        self.assertEqual([(nombre, nonce)], vistos)
        self.assertTrue(os.path.exists(os.path.join(salida._sub("sent"), nombre)))


class GhDesdeElBot(unittest.TestCase):
    """El bot y launchd no traen GH_TOKEN, y la sesión guardada de gh está caída (14-oct-26)."""

    def test_sin_variable_lo_lee_del_fichero_privado(self):
        ruta = os.path.join(tempfile.mkdtemp(), "gh-token.env")
        with open(ruta, "w") as f:
            f.write("export GH_TOKEN=tok-de-prueba\nexport GH_TOKEN_CONTACTO=otro\n")
        viejo_fichero, viejo_env = WP.FICHERO_TOKEN_GH, os.environ.pop("GH_TOKEN", None)
        WP.FICHERO_TOKEN_GH = ruta
        try:
            env = WP._entorno_gh()
            self.assertEqual("tok-de-prueba", env["GH_TOKEN"])
            os.environ["GH_TOKEN"] = "ya-puesto"
            self.assertIsNone(WP._entorno_gh(), "si ya viene puesto, no se toca")
        finally:
            WP.FICHERO_TOKEN_GH = viejo_fichero
            os.environ.pop("GH_TOKEN", None)
            if viejo_env is not None:
                os.environ["GH_TOKEN"] = viejo_env

    def test_sin_fichero_no_rompe(self):
        viejo_fichero, viejo_env = WP.FICHERO_TOKEN_GH, os.environ.pop("GH_TOKEN", None)
        WP.FICHERO_TOKEN_GH = "/no/existe"
        try:
            self.assertIsNone(WP._entorno_gh())
        finally:
            WP.FICHERO_TOKEN_GH = viejo_fichero
            if viejo_env is not None:
                os.environ["GH_TOKEN"] = viejo_env


class LeerElTextoDelPR(unittest.TestCase):

    def test_saca_lo_que_cambia_en_textos_y_contenido(self):
        d = tempfile.mkdtemp(prefix="webtg-repo-")

        def g(*a):
            return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a),
                                  capture_output=True, text=True, check=True).stdout.strip()
        g("init", "-q", "-b", "main")
        os.makedirs(os.path.join(d, "i18n", "locales"))
        os.makedirs(os.path.join(d, "content", "es"))
        os.makedirs(os.path.join(d, "app", "components"))
        with open(os.path.join(d, "i18n", "locales", "es.json"), "w") as f:
            json.dump({"footer": {"updated": "junio de"}}, f)
        with open(os.path.join(d, "content", "es", "timeline.yml"), "w") as f:
            f.write("entries:\n  - date: '2021'\n")
        with open(os.path.join(d, "app", "components", "X.vue"), "w") as f:
            f.write("<template><div/></template>\n")
        g("add", "-A")
        g("commit", "-q", "-m", "base")
        g("checkout", "-q", "-b", "rama")
        with open(os.path.join(d, "i18n", "locales", "es.json"), "w") as f:
            json.dump({"footer": {"updated": "octubre de"}}, f)
        with open(os.path.join(d, "content", "es", "timeline.yml"), "a") as f:
            f.write("  - date: '13 oct 2026'\n")
        with open(os.path.join(d, "app", "components", "X.vue"), "w") as f:
            f.write("<template><span/></template>\n")
        g("add", "-A")
        g("commit", "-q", "-m", "cambio")
        textos = WP.textos_del_pr(d, "main", g("rev-parse", "HEAD"))
        self.assertIn(("i18n/locales/es.json", "footer.updated", "junio de", "octubre de"), textos)
        self.assertTrue(any(t[0] == "content/es/timeline.yml" and "13 oct 2026" in t[3] for t in textos))
        self.assertFalse(any("X.vue" in t[0] for t in textos), "el código sin texto no se le enseña")
        msg = WP.render_textos(268, textos, "https://p", g("rev-parse", "HEAD"))
        self.assertIn("Antes: junio de", msg)
        self.assertIn("Ahora: octubre de", msg)
        self.assertEqual(WP.huella_textos(textos), WP.huella_textos(list(textos)))


if __name__ == "__main__":
    unittest.main(verbosity=1)
