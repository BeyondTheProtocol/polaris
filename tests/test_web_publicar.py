#!/usr/bin/env python3
"""test_web_publicar.py — nada se fusiona a la web sin permiso, sin CI y sin mirar el copy.

POR QUÉ EXISTE (13-oct-2026, plan «guardián de la web»). `web_novedad` fusionaba 3 segundos
después de crear el PR y `web_revertir` sin permiso alguno. `tools/web_publicar.py` es ahora el
único punto de paso, y este test fija lo que no puede volver a pasar:

  1. sin permiso firmado de ESTA sesión, no se fusiona (y ni siquiera se espera al CI);
  2. CI rojo, pendiente o inexistente: no se fusiona;
  3. un PR que toca hero/H1 o borra una clave solo sale si ELLA nombró ese PR;
  4. el merge va atado a la cabeza que se leyó (`--match-head-commit`);
  5. el permiso se vuelve a mirar después de esperar al CI;
  6. `web_novedad` y `web_revertir` no llevan ya un `gh pr merge` propio.

Nada toca la red ni GitHub: las piezas de fuera se inyectan.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="webpub-")
os.environ["BTP_STATE_DIR"] = _TMP          # el log de la herramienta, fuera del estado vivo
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
import web_publicar as WP  # noqa: E402

SHA = "a" * 40


class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _info(estado="OPEN", cabeza=SHA, numero=7):
    return lambda ref, cwd=None: {"number": numero, "headRefOid": cabeza, "state": estado,
                                  "url": "https://example.invalid/pr/%d" % numero}


def _fusiona(**kw):
    """`fusionar` con todo lo de fuera sustituido. Devuelve (resultado, llamadas)."""
    llamadas = {"ci": 0, "merge": [], "permiso": 0}
    permisos = list(kw.pop("permisos", [({"x": 1}, "", {}, b"k")] * 2))

    def permiso(que):
        llamadas["permiso"] += 1
        return permisos.pop(0) if permisos else (None, "caducó", {}, b"k")

    def ci(ref, cwd=None, tope=None):
        llamadas["ci"] += 1
        return kw.pop("ci", "verde"), {"headRefOid": kw.pop("cabeza_tras_ci", SHA)}

    def merge(cmd, cwd=None):
        llamadas["merge"].append(cmd)
        return _R(kw.pop("merge_rc", 0))

    gastos = []
    # 14-oct-26: el uso único se gasta con `consumir` (mirar y apuntar bajo cerrojo), que dice si
    # el uso es de esta llamada. `ya_gastado=True` simula que otra fusión se adelantó.
    viejo_marcar, viejo_uno = WP.P.consumir, WP.P.gastar_uno
    libre = not kw.pop("ya_gastado", False)
    WP.P.consumir = lambda d, por="": (gastos.append(("uno", por)) or libre)
    WP.P.gastar_uno = lambda d, k, n: (gastos.append(("varios", n)) or (d if libre else None))
    try:
        res = WP.fusionar(
            "auto/x", por="test", cwd=_TMP,
            que=kw.pop("que", "envio"), usos_max=kw.pop("usos_max", 1),
            atar_a_lo_visto=kw.pop("atar", False), exige_nombrado=kw.pop("exige", False),
            _info=kw.pop("info", _info()), _ci=ci, _permiso=permiso, _merge=merge,
            _copy=lambda repo, base, cab: kw.pop("copy", ([], [])),
            _fetch=lambda repo, cab: None)
    finally:
        WP.P.consumir, WP.P.gastar_uno = viejo_marcar, viejo_uno
    llamadas["gastos"] = gastos
    return res, llamadas


class ElCI(unittest.TestCase):

    def test_verde_solo_si_todo_termino_bien(self):
        ok = {"status": "COMPLETED", "conclusion": "SUCCESS"}
        self.assertEqual("verde", WP.ci_estado([ok, {"state": "SUCCESS"}]))
        self.assertEqual("verde", WP.ci_estado([ok, {"status": "COMPLETED", "conclusion": "SKIPPED"}]))

    def test_pendiente_rojo_y_vacio(self):
        ok = {"status": "COMPLETED", "conclusion": "SUCCESS"}
        self.assertEqual("pendiente", WP.ci_estado([ok, {"status": "IN_PROGRESS"}]))
        self.assertEqual("pendiente", WP.ci_estado([ok, {"state": "PENDING"}]))
        self.assertEqual("rojo", WP.ci_estado([ok, {"status": "COMPLETED", "conclusion": "FAILURE"}]))
        self.assertEqual("rojo", WP.ci_estado([{"state": "ERROR"}, {"status": "QUEUED"}]))
        self.assertEqual("sin-checks", WP.ci_estado([]))
        self.assertEqual("sin-checks", WP.ci_estado(None))

    def test_esperar_no_se_queda_colgado(self):
        vistas = []
        estado, _ = WP.esperar_ci(
            "x", tope_s=0, _dormir=vistas.append,
            _info=lambda r, c=None: {"statusCheckRollup": [{"status": "QUEUED"}]})
        self.assertEqual("pendiente", estado)
        self.assertEqual([], vistas, "con tope 0 mira una vez y vuelve")


class SinPermisoNoSale(unittest.TestCase):

    def test_sin_permiso_ni_se_espera_al_ci(self):
        res, ll = _fusiona(permisos=[(None, "nadie lo ha pedido en un mensaje", {}, None)])
        self.assertFalse(res["merged"])
        self.assertEqual(0, ll["ci"])
        self.assertEqual([], ll["merge"])
        self.assertIn("nadie", res["motivo_no_merge"])

    def test_el_permiso_se_vuelve_a_mirar_tras_el_ci(self):
        res, ll = _fusiona(permisos=[({"x": 1}, "", {}, b"k"), (None, "el permiso caducó", {}, b"k")])
        self.assertFalse(res["merged"])
        self.assertEqual(2, ll["permiso"])
        self.assertEqual([], ll["merge"])
        self.assertEqual([], ll["gastos"], "un permiso caducado no se gasta")

    def test_sin_sesion_no_hay_permiso(self):
        viejo = (WP.P.hay_alguno, WP.P.clave)
        WP.P.hay_alguno, WP.P.llave = (lambda: True), (lambda permitir_env=False: (b"k", ""))
        guardado = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            d, motivo, _, _ = WP.permiso("envio")
        finally:
            WP.P.hay_alguno, WP.P.clave = viejo
            if guardado is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = guardado
        self.assertIsNone(d)
        self.assertIn("sesión", motivo)

    def test_el_permiso_se_pide_para_esta_sesion_y_no_para_la_mas_reciente(self):
        """La deuda `web_novedad_permiso_sin_sesion`: `validar(k)` sin sesión cogía el permiso
        válido más reciente de CUALQUIER sesión."""
        pedidas = []
        viejo = (WP.P.hay_alguno, WP.P.llave, WP.P.validar)
        WP.P.hay_alguno, WP.P.llave = (lambda: True), (lambda permitir_env=False: (b"k", ""))
        WP.P.validar = lambda k, sesion=None: (pedidas.append(sesion) or (None, "no", {}))
        guardado = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sesion-de-prueba"
        try:
            WP.permiso("envio")
        finally:
            WP.P.hay_alguno, WP.P.llave, WP.P.validar = viejo
            if guardado is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = guardado
        self.assertEqual(["sesion-de-prueba"], pedidas)


class SinCINoSale(unittest.TestCase):

    def test_rojo_pendiente_y_sin_checks(self):
        for estado in ("rojo", "pendiente", "sin-checks"):
            res, ll = _fusiona(ci=estado)
            self.assertFalse(res["merged"], estado)
            self.assertEqual([], ll["merge"], estado)
            self.assertEqual([], ll["gastos"], "no se gasta el permiso si no se fusiona: " + estado)

    def test_si_la_cabeza_cambia_durante_la_espera_no_sale(self):
        res, ll = _fusiona(cabeza_tras_ci="b" * 40)
        self.assertFalse(res["merged"])
        self.assertEqual([], ll["merge"])

    def test_un_pr_cerrado_no_se_toca(self):
        res, ll = _fusiona(info=_info(estado="MERGED"))
        self.assertFalse(res["merged"])
        self.assertEqual(0, ll["ci"])


class ElCopyPublicado(unittest.TestCase):

    def test_hero_sin_nombrar_el_pr_no_sale(self):
        res, ll = _fusiona(copy=(["i18n/locales/es.json: hero/H1 «hero.title»"], []))
        self.assertFalse(res["merged"])
        self.assertEqual([], ll["merge"])
        self.assertIn("nombra", res["motivo_no_merge"])

    def test_hero_con_el_pr_nombrado_por_ella_si_sale(self):
        ctx = {"prs_suyos": {7}}
        res, ll = _fusiona(copy=(["i18n/locales/es.json: hero/H1 «hero.title»"], []),
                           permisos=[({"x": 1}, "", ctx, b"k")] * 2)
        self.assertTrue(res["merged"])

    def test_la_escotilla_de_entorno_del_hook_aqui_no_vale(self):
        os.environ["BTP_COPY_OK"] = "1"
        try:
            res, _ = _fusiona(copy=(["i18n/locales/es.json: clave borrada «nav.map»"], []))
        finally:
            os.environ.pop("BTP_COPY_OK", None)
        self.assertFalse(res["merged"])

    def test_lo_que_solo_avisa_no_frena(self):
        res, _ = _fusiona(copy=([], ["i18n/locales/es.json: copy existente «team.subtitle»"]))
        self.assertTrue(res["merged"])
        self.assertEqual(1, len(res["copy_avisa"]))


class ElMerge(unittest.TestCase):

    def test_va_atado_a_la_cabeza_leida(self):
        res, ll = _fusiona()
        self.assertTrue(res["merged"])
        cmd = ll["merge"][0]
        self.assertEqual(SHA, cmd[cmd.index("--match-head-commit") + 1])
        self.assertIn("7", cmd, "fusiona por NÚMERO, no por rama: la rama puede moverse")
        self.assertEqual([("uno", "test")], ll["gastos"])

    def test_la_novedad_puede_gastar_varios_usos(self):
        _, ll = _fusiona(usos_max=3)
        self.assertEqual([("varios", 3)], ll["gastos"])

    def test_atado_a_lo_que_ella_vio(self):
        """Con `atar_a_lo_visto`, un PR que no estaba delante de ella no sale aunque haya permiso."""
        res, ll = _fusiona(atar=True, que="fusionar")
        self.assertFalse(res["merged"])
        self.assertEqual([], ll["merge"])
        ctx = {"prs_vistos": {7}, "shas_vistos": {SHA[:8]}}
        res, _ = _fusiona(atar=True, que="fusionar", permisos=[({"x": 1}, "", ctx, b"k")] * 2)
        self.assertTrue(res["merged"])

    def test_el_peldano_3_no_viaja_en_un_fusiona_generico(self):
        """Lote: lo que exige ser nombrado no sale con un «fusiona» a secas, aunque el PR
        estuviera delante de ella y el CI esté verde."""
        visto = {"prs_vistos": {7}, "shas_vistos": {SHA[:8]}}
        res, ll = _fusiona(exige=True, atar=True, que="fusionar",
                           permisos=[({"x": 1}, "", visto, b"k")] * 2)
        self.assertFalse(res["merged"])
        self.assertEqual(0, ll["ci"])
        self.assertIn("nombra", res["motivo_no_merge"])
        nombrado = dict(visto, prs_suyos={7})
        res, _ = _fusiona(exige=True, atar=True, que="fusionar",
                          permisos=[({"x": 1}, "", nombrado, b"k")] * 2)
        self.assertTrue(res["merged"])

    def test_numeros_sin_almohadilla_no_fusionan_todo(self):
        """«fusiona 7 y 9»: el permiso no reconoce números sueltos, `prs_suyos` queda vacío y la
        orden se leería como «todo lo que vi». Saldría justo el que quiso dejar fuera."""
        visto = {"prs_vistos": {7, 8, 9}, "shas_vistos": {SHA[:8]}, "prs_suyos": set(),
                 "texto": "fusiona 7 y 9"}
        res, ll = _fusiona(atar=True, que="fusionar", permisos=[({"x": 1}, "", visto, b"k")] * 2)
        self.assertFalse(res["merged"])
        self.assertEqual([], ll["merge"])
        self.assertIn("#7", res["motivo_no_merge"])
        for texto in ("fusiona", "fusiona, son las 8 y voy con prisa el día 13", "dale, fusiona todo"):
            ctx = dict(visto, prs_vistos={7, 21, 22}, texto=texto)
            self.assertEqual("", WP.numeros_sin_almohadilla(ctx), texto)
        bien = dict(visto, prs_suyos={7, 9}, texto="fusiona #7 y #9")
        self.assertEqual("", WP.numeros_sin_almohadilla(bien))

    def test_sin_worktree_propio_no_se_toca_el_checkout_compartido(self):
        vistos = []
        viejo = WP._run
        WP._run = lambda cmd, cwd=None, timeout=180: vistos.append((cmd, cwd)) or _R()
        try:
            WP._merge_real(["gh", "pr", "merge", "7", "--squash"], cwd=None)
        finally:
            WP._run = viejo
        cmd, cwd = vistos[0]
        self.assertEqual(WP.REPO, cmd[cmd.index("-R") + 1])
        self.assertNotEqual(WP.WEB, cwd)

    def test_si_el_merge_falla_se_dice(self):
        res, _ = _fusiona(merge_rc=1)
        self.assertFalse(res["merged"])
        self.assertIn("merge", res["motivo_no_merge"])

    def test_si_otra_fusion_gasto_el_permiso_antes_no_se_fusiona(self):
        """14-oct-26: el uso se reserva con cerrojo ANTES de fusionar. Si otra llamada se
        adelantó, esta no llega a lanzar `gh pr merge`."""
        for usos in (1, 3):
            res, ll = _fusiona(ya_gastado=True, usos_max=usos)
            self.assertFalse(res["merged"])
            self.assertEqual(ll["merge"], [], "no se lanzó ningún merge")
            self.assertIn("ya abrió", res["motivo_no_merge"])

    def test_queda_apuntado(self):
        _fusiona()
        with open(WP.LOG, encoding="utf-8") as f:
            ultima = json.loads(f.read().strip().splitlines()[-1])
        self.assertEqual("test", ultima["por"])
        self.assertIn("merged", ultima)


def _repo_web(viejo, nuevo, contenido=None):
    """Repo git mínimo con un locale en `main` y otro en una rama. Devuelve (ruta, base, cabeza)."""
    d = tempfile.mkdtemp(prefix="webpub-repo-")

    def g(*a):
        return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a),
                              capture_output=True, text=True, check=True).stdout.strip()
    g("init", "-q", "-b", "main")
    ruta = os.path.join(d, "i18n", "locales")
    os.makedirs(ruta)
    os.makedirs(os.path.join(d, "content", "es"))
    with open(os.path.join(ruta, "es.json"), "w") as f:
        f.write(viejo if isinstance(viejo, str) else json.dumps(viejo))
    with open(os.path.join(d, "content", "es", "timeline.yml"), "w") as f:
        f.write("entries:\n  - date: '2021'\n    title: uno\n")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("checkout", "-q", "-b", "rama")
    with open(os.path.join(ruta, "es.json"), "w") as f:
        f.write(nuevo if isinstance(nuevo, str) else json.dumps(nuevo))
    if contenido is not None:
        with open(os.path.join(d, "content", "es", "timeline.yml"), "w") as f:
            f.write(contenido)
    g("add", "-A")
    g("commit", "-q", "-m", "cambio")
    return d, "main", g("rev-parse", "HEAD")


class RevisaElDiffDelPR(unittest.TestCase):

    BASE = {"hero": {"title": "Hola"}, "team": {"subtitle": "Una ingeniera"}, "nav": {"map": "Mapa"}}

    def _mira(self, nuevo, contenido=None):
        return WP.revisa_copy(*_repo_web(self.BASE, nuevo, contenido))

    def test_hero_cambiado_frena(self):
        frenan, _ = self._mira(dict(self.BASE, hero={"title": "Otra cosa"}))
        self.assertEqual(1, len(frenan))
        self.assertIn("hero.title", frenan[0])

    def test_clave_borrada_frena(self):
        frenan, _ = self._mira({"hero": {"title": "Hola"}, "team": {"subtitle": "Una ingeniera"}})
        self.assertTrue(any("nav.map" in x for x in frenan))

    def test_otro_copy_existente_avisa_y_no_frena(self):
        frenan, avisan = self._mira(dict(self.BASE, team={"subtitle": "Una paciente"}))
        self.assertEqual([], frenan)
        self.assertTrue(any("team.subtitle" in x for x in avisan))

    def test_clave_nueva_pasa_en_silencio(self):
        self.assertEqual(([], []), self._mira(dict(self.BASE, nuevo={"bloque": "Texto nuevo"})))

    def test_un_locale_ilegible_frena(self):
        """Al revés que el hook (fail-open en cada commit): al publicar, lo que no se puede
        leer no sale."""
        frenan, _ = self._mira("{esto no es json")
        self.assertEqual(1, len(frenan))

    def test_contenido_que_solo_anade_pasa_y_el_que_quita_avisa(self):
        solo_anade = "entries:\n  - date: '2021'\n    title: uno\n  - date: '2022'\n    title: dos\n"
        self.assertEqual(([], []), self._mira(self.BASE, solo_anade))
        _, avisan = self._mira(self.BASE, "entries:\n  - date: '2021'\n    title: otro\n")
        self.assertTrue(any("timeline.yml" in x for x in avisan))

    def test_clasifica_el_hero_igual_que_el_hook(self):
        import copy_web_guard as G
        for clave in ("hero.title", "hero.subtitle", "home.hero_kicker", "press.h1", "a.h1.b",
                      "nav.title", "team.subtitle", "pathways.title", "heroic.note"):
            self.assertEqual(G._es_hero(clave), WP._es_hero(clave), clave)


class LoDemas(unittest.TestCase):

    def test_la_aguja_no_lleva_caracteres_que_el_html_escapa(self):
        self.assertEqual("Entro en el ensayo", WP.aguja_segura("Entro en el ensayo"))
        self.assertEqual("la biopsia de julio", WP.aguja_segura("«A&B» <i>la biopsia de julio"[6:]))
        self.assertEqual("", WP.aguja_segura(""))

    def test_mergeado_no_se_cuenta_como_desplegado(self):
        self.assertIn("no se ha comprobado", WP.explica_despliegue({"merged": True}))
        res = {"merged": True, "desplegado": False,
               "verifica": {"url": "https://example.invalid/t", "texto": "algo"}}
        self.assertIn("AÚN no", WP.explica_despliegue(res))

    def test_nadie_mas_lleva_un_merge_propio(self):
        """El punto único deja de serlo en cuanto otra herramienta web fusiona por su cuenta."""
        for nombre in ("web_novedad.py", "web_revertir.py"):
            with open(os.path.join(ROOT, "tools", nombre), encoding="utf-8") as f:
                fuente = f.read()
            self.assertNotIn('"merge"', fuente, nombre)
        with open(os.path.join(ROOT, "tools", "web_novedad.py"), encoding="utf-8") as f:
            self.assertIn("WP.fusionar(", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=1)
