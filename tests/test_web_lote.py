#!/usr/bin/env python3
"""test_web_lote.py — una orden suya fusiona N PRs de la web, y solo los que vio.

POR QUÉ EXISTE (13-oct-2026, plan «guardián de la web»). {{TITULAR}} firmaba los cambios de la web de
uno en uno. El lote le deja firmar varios con una orden, y este test fija lo que eso NO afloja:

  1. al manifiesto solo entran PRs abiertos y con el CI en verde;
  2. dos PRs del mismo lote no tocan el mismo fichero (el segundo habría que rebasarlo y ya no
     sería el commit que ella firmó);
  3. el peldaño no lo baja nadie a mano: un PR que toca el hero es 3 diga lo que diga la lista;
  4. los topes: 3 cambios que piden su atención, 5 mecánicos;
  5. lo de peldaño 3 solo sale si ella nombra su número; el resto, atado a lo que tuvo delante;
  6. el manifiesto lleva cada número de PR y cada commit, que es de donde el permiso los lee.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("BTP_STATE_DIR", tempfile.mkdtemp(prefix="weblote-"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import web_lote as L  # noqa: E402


def _sha(c):
    return c * 40


def _pr(numero, ficheros, ci="verde", estado="OPEN", frena=(), avisa=()):
    return {"number": numero, "state": estado, "headRefOid": _sha("abcdef"[numero % 6]),
            "url": "https://example.invalid/pull/%d" % numero, "title": "PR %d" % numero,
            "ci": ci, "files": list(ficheros), "frena": list(frena), "avisa": list(avisa),
            "preview": "https://deploy-preview-%d--x.netlify.app" % numero}


def _lee(prs):
    porn = {p["number"]: p for p in prs}
    return lambda numero: porn.get(numero)


PEDIDO = [{"pr": 1, "peldano": 1, "que": "Ancla siempre viva"},
          {"pr": 2, "peldano": 2, "que": "Sello del pie derivado"},
          {"pr": 3, "peldano": 3, "que": "Entradas de cronología"}]
PRS = [_pr(1, ["app/components/TimelineEntry.vue"]),
       _pr(2, ["app/components/SiteFooter.vue"], avisa=["i18n/locales/es.json: copy existente «footer.updated»"]),
       _pr(3, ["content/es/timeline.yml", "app/data/caso.json"])]


class ElManifiesto(unittest.TestCase):

    def test_un_lote_sano_se_valida(self):
        items, pegas = L.validar(PEDIDO, _lee(PRS))
        self.assertEqual([], pegas)
        self.assertEqual([1, 2, 3], [i["peldano"] for i in items])

    def test_ci_que_no_esta_verde_no_entra(self):
        for ci in ("rojo", "pendiente", "sin-checks"):
            _, pegas = L.validar(PEDIDO, _lee([_pr(1, ["a.vue"], ci=ci)] + PRS[1:]))
            self.assertTrue(any("#1" in p and "CI" in p for p in pegas), ci)

    def test_pr_cerrado_o_inexistente_no_entra(self):
        _, pegas = L.validar(PEDIDO, _lee([_pr(1, ["a.vue"], estado="MERGED")] + PRS[1:]))
        self.assertTrue(any("#1" in p for p in pegas))
        _, pegas = L.validar(PEDIDO, _lee(PRS[1:]))
        self.assertTrue(any("#1" in p for p in pegas))

    def test_dos_prs_no_tocan_el_mismo_fichero(self):
        prs = [PRS[0], _pr(2, ["app/data/caso.json"]), PRS[2]]
        _, pegas = L.validar(PEDIDO, _lee(prs))
        self.assertTrue(any("app/data/caso.json" in p and "#2" in p and "#3" in p for p in pegas))

    def test_el_peldano_no_se_baja_a_mano(self):
        prs = [_pr(1, ["i18n/locales/es.json"], frena=["i18n/locales/es.json: hero/H1 «hero.title»"]),
               _pr(2, ["x.vue"], avisa=["copy existente"]), PRS[2]]
        pedido = [dict(PEDIDO[0]), dict(PEDIDO[1], peldano=1), dict(PEDIDO[2])]
        items, _ = L.validar(pedido, _lee(prs))
        por_pr = {i["pr"]: i["peldano"] for i in items}
        self.assertEqual(3, por_pr[1], "toca el hero: es 3 aunque la lista diga 1")
        self.assertEqual(2, por_pr[2], "cambia copy existente: no puede ser 1")

    def test_topes(self):
        muchos = [{"pr": n, "peldano": 2, "que": "x"} for n in range(1, 5)]
        _, pegas = L.validar(muchos, _lee([_pr(n, ["f%d.vue" % n]) for n in range(1, 5)]))
        self.assertTrue(any("atención" in p for p in pegas))
        mec = [{"pr": n, "peldano": 1, "que": "x"} for n in range(1, 7)]
        _, pegas = L.validar(mec, _lee([_pr(n, ["f%d.vue" % n]) for n in range(1, 7)]))
        self.assertTrue(any("mecánicos" in p for p in pegas))

    def test_el_manifiesto_lleva_numero_commit_y_preview(self):
        items, _ = L.validar(PEDIDO, _lee(PRS))
        texto = L.render(items)
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import permiso_envio as P
        self.assertEqual({1, 2, 3}, P._prs(texto), "el permiso lee los PRs del mensaje que ella ve")
        for it in items:
            self.assertIn(it["sha"][:12], texto)
            self.assertIn(it["preview"], texto)
        self.assertIn("#3", texto.split("solo si lo nombras")[1] if "solo si lo nombras" in texto else "")


class LaOrden(unittest.TestCase):

    def _corre(self, resultado=None):
        llamadas = []

        def fusiona(ref, **kw):
            llamadas.append((ref, kw))
            return (resultado or (lambda r, k: {"merged": True}))(ref, kw)
        items, _ = L.validar(PEDIDO, _lee(PRS))
        self.cierres = []
        return L.fusionar(items, _fusiona=fusiona, _cierra=self.cierres.append), llamadas

    def test_al_acabar_no_deja_el_permiso_vivo(self):
        """Si un PR no salió le quedan usos al permiso. Su orden era para este lote."""
        self._corre(lambda ref, kw: {"merged": ref != 3, "motivo_no_merge": "x"})
        self.assertEqual(["web_lote"], self.cierres)

    def test_no_pide_mas_usos_que_prs(self):
        _, ll = self._corre()
        self.assertEqual({3}, {kw["usos_max"] for _, kw in ll})

    def test_cada_pr_va_atado_a_lo_visto_y_sin_esperar_al_ci(self):
        _, ll = self._corre()
        self.assertEqual([1, 2, 3], [ref for ref, _ in ll])
        for _, kw in ll:
            self.assertEqual("fusionar", kw["que"])
            self.assertTrue(kw["atar_a_lo_visto"])
            self.assertEqual(0, kw["tope_ci_s"], "el CI ya estaba verde: su permiso dura 10 minutos")
            self.assertGreater(kw["usos_max"], 1, "una orden vale para todo el lote")

    def test_el_peldano_3_exige_que_lo_nombre(self):
        _, ll = self._corre()
        exige = {ref: kw["exige_nombrado"] for ref, kw in ll}
        self.assertEqual({1: False, 2: False, 3: True}, exige)

    def test_cuenta_lo_que_salio_y_lo_que_no(self):
        res, _ = self._corre(lambda ref, kw: {"merged": ref != 3,
                                              "motivo_no_merge": "solo salen si ella lo nombra"})
        self.assertEqual([1, 2], [r["pr"] for r in res if r["merged"]])
        self.assertIn("nombra", [r for r in res if not r["merged"]][0]["motivo"])

    def test_si_el_permiso_se_acaba_no_sigue_intentando(self):
        res, ll = self._corre(lambda ref, kw: {"merged": False, "pedido_por_titular": False,
                                               "motivo_no_merge": "nadie lo ha pedido en un mensaje"})
        self.assertEqual(1, len(ll), "sin permiso, no tiene sentido probar los demás")
        self.assertEqual(3, len(res), "pero los tres quedan contados como no publicados")


if __name__ == "__main__":
    unittest.main(verbosity=1)
