#!/usr/bin/env python3
"""test_ci_barrido.py — el guardia de la puerta de entrada del repo público.

19-sep-2026. `publicar.py` mira lo que SALE; `ci_barrido.py` mira lo que ENTRA por PR. Este
test protege las dos mitades de su utilidad:
  · que DETECTE lo que no puede entrar (clave, contacto, dato clínico crudo, ruta privada);
  · que NO ladre por lo normal. Un CI con falsos positivos se aprende a ignorar, y entonces
    deja de existir aunque siga corriendo.
"""
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))
import ci_barrido as cb  # noqa: E402


class TestDetecta(unittest.TestCase):
    def _claves(self, texto):
        return {h[0] for h in cb.revisar_texto(texto)}

    def test_lo_que_no_puede_entrar(self):
        casos = {
            "clave_api": "GH_TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz0123'",
            "clave_privada": "-----BEGIN OPENSSH PRIVATE KEY-----",
            "email": "escribe a juan.perez@gmail.com",
            "telefono": "mi movil es 612 34 56 78",
            "dni": "con DNI 12345678Z",
            "nhc": "NHC 9912345 del paciente",
            "hla": "tipado HLA-A*02:01",
            "vcf": "##fileformat=VCFv4.2",
            "secuencia": "ACGTACGTACGTACGTACGTACGTACGT",
            "hgvs": "la variante c.1521_1523delCTT",
        }
        for clave, texto in casos.items():
            self.assertIn(clave, self._claves(texto), "no detecta %s" % clave)

    def test_rutas_que_no_pintan_nada_aqui(self):
        for ruta in ("00_FUENTE-DE-VERDAD/informe.md", "tools/perfil.local.json",
                     "docs/algo.pdf", "tools/state/cola.json", "_PRIVADO_CORREO/x.eml"):
            self.assertTrue(cb.revisar_ruta(ruta), "deja pasar %s" % ruta)


class TestNoLadraPorLoNormal(unittest.TestCase):
    def test_el_vocabulario_del_dominio_no_es_una_fuga(self):
        for texto in ("un comentario sobre oncología y metástasis",
                      "FGFR4, {{DIANA2}} y TP53 son dianas",
                      "EPOCH = 978307200  # Core Data epoch",
                      "version = '1.0.0', puerto 8080, timeout 3600"):
            self.assertEqual(cb.revisar_texto(texto), [], "falso positivo en %r" % texto)

    def test_correos_que_no_son_de_nadie(self):
        for addr in ("noreply@anthropic.com", "contacto@dominio.com", "a@b.com",
                     "titular@gmail.com", "titular.mgp@gmail.com", "git@github.com",
                     "inmail-hit-reply@linkedin.com", "anatomia-push@btp.local",
                     "beyondtheprotocolteam@gmail.com", "medico@ejemplo.com"):
            self.assertTrue(cb._email_de_nadie(addr), "%s no debería saltar" % addr)

    def test_los_sitios_cuyo_trabajo_es_contener_esos_patrones(self):
        for ruta in ("tools/ci_barrido.py", "tools/deid.py", "tools/borde.py",
                     "tests/test_correo.py", "evals/muro.json",
                     "pipeline/data_ejemplo/ejemplo.vcf"):
            self.assertTrue(cb._exento(ruta), "%s debería estar exento" % ruta)
        self.assertFalse(cb._exento("tools/onco.py"))

    def test_los_ficheros_legales_pueden_llevar_un_contacto(self):
        """SECURITY.md sin dirección de contacto no sirve para nada."""
        for ruta in ("SECURITY.md", "CODE_OF_CONDUCT.md", "CITATION.cff", "NOTICE"):
            self.assertTrue(cb._exento(ruta), ruta)


class TestElArbolPublicoPasaSuPropioBarrido(unittest.TestCase):
    """Si el repo que publicamos no pasa el guardia, el guardia no vale para nadie."""

    def test_selftest_en_verde(self):
        self.assertEqual(cb.selftest(), 0)

    def test_el_workflow_se_publica(self):
        sys.path.insert(0, os.path.join(RAIZ, "tools"))
        import publicar  # noqa: E402
        self.assertIn(".github", publicar.INCLUIR)
        self.assertTrue(os.path.exists(os.path.join(RAIZ, ".github", "workflows",
                                                    "contribuciones.yml")))

    def test_las_baterias_que_dependen_de_lo_privado_no_se_publican(self):
        import publicar  # noqa: E402
        self.assertIn(os.path.join("tests", "test_correo.py"), publicar.EXCLUIR_FICHERO)
        # y el runner público no puede llamar a un fichero que no viaja
        runner = open(os.path.join(RAIZ, "tests", "test_all.sh")).read()
        cosido = publicar._coser_runner(runner)
        for rel in publicar.EXCLUIR_FICHERO:
            self.assertNotIn("runpy %s" % os.path.basename(rel), cosido)


if __name__ == "__main__":
    unittest.main()
