#!/usr/bin/env python3
"""test_healthcheck_llms.py — quedarse sin saldo tiene que avisar, y no es lo mismo que caerse.

19-sep-2026. {{TITULAR}}: «necesito que alguien vigile que todos los LLMs funcionan y no se quedan
sin saldo». Existía media pieza: `ia_health` sonda los endpoints con un GET, y **un GET responde
200 con el saldo a cero** —lo dice el comentario de su propia `refrescar_credito_claude`, que por
eso hace una llamada real… solo para Claude—. Del resto nadie sabía nada: ese mismo día GLM
devolvía «Insufficient balance» en cada llamada del enrutador y ninguna alerta saltó.

Lo que se protege aquí:
  · un proveedor sin saldo avisa con clave propia (`llm_sin_saldo:`), porque la acción es
    RECARGAR y es de {{TITULAR}};
  · un proveedor caído avisa distinto (`llm_caido:`), porque eso se reintenta y degrada solo;
  · los que están bien no dicen nada;
  · el throttle evita martillear a los proveedores en cada vuelta del lazo.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import healthcheck as hc  # noqa: E402

FUTURO = 9e9   # fuerza a saltarse el throttle


class TestDistingueSaldoDeCaida(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._hc = hc.HC
        hc.HC = self.tmp

    def tearDown(self):
        hc.HC = self._hc

    def _claves(self, salud, ahora=FUTURO):
        alertas, info = hc._check_llms(salud=salud, ahora=ahora)
        return {k for k, _ in alertas}, info

    def test_sin_saldo_tiene_clave_propia(self):
        # El proveedor de ejemplo NO puede ser `glm`: desde el 20-sep-26 está aparcado en el
        # catálogo y por diseño ya no alerta (ver TestProveedorAparcado). `zeta` no existe en
        # enruta.PROVEEDORES, así que se comporta como cualquier proveedor vivo sin saldo.
        claves, info = self._claves({
            "zeta": {"ok": False, "detalle": 'error 429: {"message":"Insufficient balance or no resource package"}'}})
        self.assertIn("llm_sin_saldo:zeta", claves)
        self.assertEqual(info["sin_saldo"], ["zeta"])

    def test_otras_formas_de_decir_sin_dinero(self):
        # cada vuelta con su reloj: si no, la segunda cae en el throttle y el test se engaña solo
        for i, detalle in enumerate(("credit balance is too low", "Payment Required",
                                     "exceeded your current quota", "billing: please recharge",
                                     "saldo agotado")):
            claves, _ = self._claves({"x": {"ok": False, "detalle": detalle}},
                                     ahora=FUTURO + i * 86400)
            self.assertIn("llm_sin_saldo:x", claves, detalle)

    def test_una_caida_no_se_confunde_con_falta_de_saldo(self):
        caido = {"grok": {"ok": False, "detalle": "timeout tras 60s"}}
        claves, info = self._claves(caido)                       # 1a pasada: se anota, no grita
        self.assertEqual(claves, set())
        self.assertEqual(info["caidos"], ["grok"])
        claves, info = self._claves(caido, ahora=FUTURO + 86400)  # 2a seguida: ahora sí
        self.assertIn("llm_caido:grok", claves)
        self.assertEqual(info["sin_saldo"], [])

    def test_un_hipo_de_red_no_despierta_a_nadie(self):
        """La medición real del 19-sep dio grok caído y medio minuto después estaba OK."""
        claves, _ = self._claves({"grok": {"ok": False, "detalle": "timeout tras 60s"}})
        self.assertEqual(claves, set())
        claves, _ = self._claves({"grok": {"ok": True, "detalle": "OK"}}, ahora=FUTURO + 86400)
        self.assertEqual(claves, set())

    def test_sin_saldo_NO_espera_a_la_segunda(self):
        """Quedarse sin dinero no se arregla solo: se avisa a la primera."""
        claves, _ = self._claves({"zeta": {"ok": False, "detalle": "Insufficient balance"}})
        self.assertIn("llm_sin_saldo:zeta", claves)

    def test_los_sanos_no_dicen_nada(self):
        claves, info = self._claves({"claude": {"ok": True, "detalle": "OK"},
                                     "local": {"ok": True, "detalle": "ollama"}})
        self.assertEqual(claves, set())
        self.assertEqual((info["sin_saldo"], info["caidos"]), ([], []))

    def test_no_martillea_a_los_proveedores_en_cada_vuelta(self):
        self._claves({"zeta": {"ok": False, "detalle": "Insufficient balance"}})   # deja estado
        alertas, info = hc._check_llms(salud={"zeta": {"ok": False, "detalle": "Insufficient balance"}})
        self.assertEqual(alertas, [])
        self.assertTrue(info.get("throttled"))
        self.assertEqual(info.get("sin_saldo"), ["zeta"])   # pero no olvida lo que sabía

    def test_un_proveedor_aparcado_no_alerta_por_saldo_ni_por_caida(self):
        """20-sep-26: `llm_sin_saldo:glm` era terminal (`intermitente`, 5 detecciones) y dejaba
        la batería roja sin salida. El prepago de GLM está a cero POR DECISIÓN del 19-sep, así
        que la condición se cumple en cada vuelta: cerrar la deuda solo la reabría. Una decisión
        no es una avería."""
        claves, info = self._claves({"glm": {"ok": False, "detalle": "Insufficient balance"}})
        self.assertEqual(claves, set())
        self.assertEqual(info["sin_saldo"], [])
        self.assertEqual(info["aparcados"], ["glm"])
        # y tampoco por caída, que es la otra rama de la función
        claves, info = self._claves({"glm": {"ok": False, "detalle": "timeout tras 60s"}},
                                    ahora=FUTURO + 86400)
        self.assertEqual(claves, set())
        self.assertEqual(info["caidos"], [])

    def test_el_catalogo_declara_por_que_glm_esta_aparcado(self):
        """El campo es para las máquinas; si alguien recarga GLM, se quita y vuelve a avisar."""
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import enruta
        self.assertTrue((enruta.PROVEEDORES["glm"].get("aparcado") or "").strip(),
                        "glm sin motivo de aparcado: el silencio tiene que estar justificado")
        aparcados = [n for n, m in enruta.PROVEEDORES.items() if m.get("aparcado")]
        self.assertEqual(["glm"], aparcados, "solo glm está aparcado hoy")

    def test_va_en_la_vuelta_del_healthcheck(self):
        fuente = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "tools", "healthcheck.py"), encoding="utf-8").read()
        self.assertIn("_check_llms()", fuente)


if __name__ == "__main__":
    unittest.main()
