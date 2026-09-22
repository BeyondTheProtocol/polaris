#!/usr/bin/env python3
"""El registro distingue MODELO de HERRAMIENTA, y nadie vuelve a contarlos juntos.

Nació de una corrección de {{TITULAR}} (12-sep-26): un gráfico para publicar decía «13 modelos»
cuando dos de ellos —consensus y scite— no generan texto, son buscadores de literatura con
API. Yo mismo lo había detectado días antes y aun así lo publiqué mal, así que la lección
deja de vivir en una memoria y pasa a ser un freno.
"""
import json, os, sys, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.environ.get("BTP_PERIPHERIES") or os.path.join(RAIZ, "tools", "peripheries.json")
NO_GENERATIVOS = {"consensus", "scite"}


class TestGenerativo(unittest.TestCase):
    def setUp(self):
        self.cer = json.load(open(REG, encoding="utf-8"))["cerebros"]

    def test_todos_declaran_el_campo(self):
        faltan = [c["name"] for c in self.cer if "generativo" not in c]
        self.assertEqual([], faltan,
                         "cerebros sin declarar si son modelo o herramienta: %s" % faltan)

    def test_los_buscadores_no_son_modelos(self):
        for c in self.cer:
            if c["name"] in NO_GENERATIVOS:
                self.assertFalse(c["generativo"],
                                 "%s devuelve papers, no texto: generativo debe ser false" % c["name"])

    def test_un_modelo_encendido_declara_que_modelo_es(self):
        """Un cerebro ENCENDIDO que dice ser generativo tiene que decir CUÁL sirve.

        Se exige solo a los encendidos a propósito: de uno apagado no se puede consultar el
        catálogo (fugu lleva meses devolviendo 403), y mientras esté apagado no engaña a nadie
        porque no se usa. En cuanto alguien lo encienda, este test se pone rojo hasta que
        declare sus modelos — que es justo cuando importa.

        Excepción: `carril_gratis` resuelve el modelo en la propia llamada (el tier decide),
        así que no puede declararlo por adelantado.
        """
        for c in self.cer:
            if not (c.get("generativo") and c.get("enabled")):
                continue
            if c.get("kind") == "carril_gratis":
                continue
            self.assertTrue(c.get("models") or c.get("model"),
                            "%s está encendido y dice ser un modelo, pero no declara cuál"
                            % c["name"])


if __name__ == "__main__":
    unittest.main(verbosity=0)
