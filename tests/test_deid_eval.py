#!/usr/bin/env python3
"""test_deid_eval.py — el banco de medida de la de-identificación mide bien, y la cifra no baja.

POR QUÉ (25-sep-26, P4 paso 5; idea de {{CONTACTO}} (https://contacto), con su agente KAI,
revisión del 25-sep-2026). «Hecho cuando hay una cifra de recall medida», y una cifra que nadie
vigila se pudre. Tres bloques:
  1. El banco en sí, con un corpus brat inventado de dos documentos: estricto frente a parcial,
     precisión por carácter, fugas literales, y que se niega a medir si el .ann no cuadra.
  2. Piezas nuevas de deid.py que el banco usa: el mapa de posiciones normalizado→original y la
     capa de campos de filiación («Nombre: X»).
  3. SUELO sobre MEDDOCAN test (si está en _data/): la cifra del 25-sep-26 no puede empeorar sin que
     esto se ponga rojo. Solo regex: 2 s. Con NER: ~70 s y solo si existe .venv-deid.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")

import os
import sys
import tempfile
import unicodedata
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import deid  # noqa: E402
import deid_eval  # noqa: E402

MEDDOCAN = os.path.join(os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode"),
                        "_data", "meddocan", "meddocan", "test", "brat")


def _brat(d, nombre, texto, anotaciones):
    with open(os.path.join(d, nombre + ".txt"), "w", encoding="utf-8", newline="") as fh:
        fh.write(texto)
    with open(os.path.join(d, nombre + ".ann"), "w", encoding="utf-8") as fh:
        for i, (a, b, et) in enumerate(anotaciones, 1):
            fh.write("T%d\t%s %d %d\t%s\n" % (i, et, a, b, texto[a:b]))


class ElBancoMideBien(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = "Tel 612345678. Vive en Calle Luna 3 con su hermano {{CONTACTO}} Soto."
        def en(sub):
            return t.index(sub), t.index(sub) + len(sub)
        _brat(self.tmp.name, "d1", t, [en("612345678") + ("NUMERO_TELEFONO",),
                                      en("Calle Luna 3") + ("CALLE",),
                                      en("hermano {{CONTACTO}} Soto") + ("FAMILIARES_SUJETO_ASISTENCIA",)])
        self.docs = deid_eval.cargar_brat(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_estricto_parcial_y_precision(self):
        t = self.docs[0][1]
        dets = [[(4, 13, "x"), (23, 28, "x"), (0, 3, "x")]]   # tel entero, media calle, «Tel» de más
        r = deid_eval.puntuar(self.docs, dets)
        self.assertEqual(r["entidades"], 3)
        self.assertEqual(r["por_etiqueta"]["NUMERO_TELEFONO"]["cubiertas"], 1)
        self.assertEqual(r["por_etiqueta"]["CALLE"]["cubiertas"], 0, "media calle no es calle tapada")
        self.assertEqual(r["por_etiqueta"]["CALLE"]["parciales"], 1)
        self.assertAlmostEqual(r["recall_estricto"], 1 / 3, places=3)
        # tapados visibles: 9 (tel) + 5 (Calle) + 3 (Tel) = 17, de los que 14 son identificador
        self.assertAlmostEqual(r["precision_caracter"], 14 / 17, places=3)
        self.assertEqual(t[23:28], "Calle")

    def test_fuga_literal_en_la_salida(self):
        f = deid_eval.fugas(self.docs, ["Tel [R]. Vive en Calle Luna 3 con su [R]."])
        self.assertEqual(f["por_etiqueta"]["CALLE"]["fugas"], 1)
        self.assertEqual(f["por_etiqueta"]["NUMERO_TELEFONO"]["fugas"], 0)
        self.assertEqual(f["por_etiqueta"]["FAMILIARES_SUJETO_ASISTENCIA"]["fugas"], 0)

    def test_no_mide_si_el_ann_no_cuadra(self):
        with open(os.path.join(self.tmp.name, "d1.ann"), "a", encoding="utf-8") as fh:
            fh.write("T9\tCALLE 0 3\tNada\n")
        self.assertEqual(deid_eval.main(["--brat", self.tmp.name, "--capas", "regex",
                                         "--sin-diccionario"]), 3)


class PiezasDeDeid(unittest.TestCase):
    def test_mapa_normalizado_igual_que_normalizar_entero(self):
        t = "Teléfono ﬁjo:​ 612 · Ñandú ü café́ ½ fin"
        norm, mapa = deid._mapa_normalizado(t)
        self.assertEqual(norm, deid.borde._normalizar(t)[0])
        self.assertEqual(len(mapa), len(norm))
        self.assertEqual(mapa, sorted(mapa), "el mapa no puede ir hacia atrás")

    def test_detectar_devuelve_posiciones_del_original(self):
        t = "Teléfono móvil: 612345678, gracias"
        spans = [(a, b) for a, b, c in deid.detectar(t) if c == "regex:telefono"]
        self.assertTrue(spans)
        self.assertEqual(t[spans[0][0]:spans[0][1]].replace(" ", ""), "612345678")

    def test_campo_nombre_tapa_el_valor_no_la_etiqueta(self):
        casos = {
            "Nombre:  Ignacio.\nApellidos: Rico Pedroza\n": ("Ignacio", "Rico", "Pedroza"),
            "Paciente: Ana López NHC: 4455": ("Ana", "Lopez"),
            "| **Paciente:** | | Ana López Ruiz |": ("Ana", "Ruiz"),
            "Nom i cognoms: Jordi Puig i Vila\n": ("Jordi", "Puig", "Vila"),
        }
        for texto, nombres in casos.items():
            out = deid.de_identificar(texto)[0]
            for n in nombres:
                self.assertNotIn(n, out, "%r escapa en %r" % (n, texto))
        self.assertIn("Apellidos:", deid.de_identificar("Apellidos: Rico\n")[0])

    def test_campos_de_asistencia_y_celdas_de_tabla(self):
        casos = {"| **Sexo** | Mujer |": "Mujer", "**Sexo:** F\n": " F",
                 "| Nº Cama | VERDE4 |": "VERDE4",
                 "**Nombre del solicitante:** Garcia {{CONTACTO}}, {{CONTACTO}}\n": "{{CONTACTO}}",
                 "**Centro de Salud:** C.S. VISTA ALEGRE | x": "VISTA",
                 "| Nº Acto | 24-1184494 |": "1184494"}
        for texto, secreto in casos.items():
            self.assertNotIn(secreto, deid.de_identificar(texto)[0], "escapa en %r" % texto)

    def test_edad_en_celda_compuesta_sin_corchetes_rotos(self):
        for texto in ("| **F.Nacim (Edad)** | {{FECHA_NAC}} 33 Años | | **N.S.S.** |",
                      "| F.Nacim(Edad) | {{FECHA_NAC}} (30 Años) | Nº Acto |"):
            out = deid.de_identificar(texto)[0]
            self.assertNotIn("Anos", out)
            self.assertNotIn("[[", out)
            self.assertNotIn("])", out)

    def test_campos_que_no_se_tocan(self):
        for texto in ("**Servicio:** Hematologia", "Diagnostico: Ca mama", "| Campo | Valor |",
                      "| Nombre y categoria | Fecha y hora |", "Revision por su medico de familia"):
            self.assertEqual(deid.de_identificar(texto)[0], texto)

    def test_campo_nombre_no_muerde_sin_separador(self):
        for texto in ("Nombre del estudio: TAC abdominal", "El paciente refiere dolor abdominal"):
            self.assertEqual(deid.de_identificar(texto)[0], deid.borde._normalizar(texto)[0])


class MuestraPorLaVentanilla(unittest.TestCase):
    """Los modos que escriben en zona clínica solo van por la ventanilla y solo sobre zona clínica."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.zona = os.path.join(self.tmp.name, "_PRIVADO_CLINICO", "_deid_muestra")
        self.origen = os.path.join(self.tmp.name, "_PRIVADO_CLINICO", "informe.md")
        os.makedirs(os.path.dirname(self.origen))
        with open(self.origen, "w", encoding="utf-8") as fh:
            fh.write("Paciente: Ana Gil Pérez\nDra. Luz Marín. Control de Ana Gil Pérez el 3/2/2025.\n")
        self._env = os.environ.get("BTP_VENTANILLA")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("BTP_VENTANILLA", None)
        else:
            os.environ["BTP_VENTANILLA"] = self._env
        self.tmp.cleanup()

    def test_fuera_de_la_ventanilla_se_niega(self):
        os.environ.pop("BTP_VENTANILLA", None)
        self.assertEqual(deid_eval.preparar(self.zona, [self.origen]), 1)
        self.assertFalse(os.path.exists(self.zona))

    def test_fuera_de_zona_clinica_se_niega(self):
        os.environ["BTP_VENTANILLA"] = "1"
        fuera = os.path.join(self.tmp.name, "publico")
        self.assertEqual(deid_eval.preparar(fuera, [self.origen]), 1)
        self.assertFalse(os.path.exists(fuera))

    def test_prepara_anota_y_mide(self):
        os.environ["BTP_VENTANILLA"] = "1"
        self.assertEqual(deid_eval.preparar(self.zona, [self.origen]), 0)
        self.assertEqual(oct(os.stat(os.path.join(self.zona, "01.txt")).st_mode & 0o777), "0o600")
        # repetir no duplica ni pisa
        self.assertEqual(deid_eval.preparar(self.zona, [self.origen]), 0)
        self.assertEqual(len([f for f in os.listdir(self.zona) if f.endswith(".txt")]), 1)
        anot = [{"texto": "Ana Gil Pérez", "etiqueta": "NOMBRE_SUJETO_ASISTENCIA"},
                {"texto": "Pérez", "etiqueta": "NOMBRE_SUJETO_ASISTENCIA"},
                {"texto": "Luz Marín", "etiqueta": "NOMBRE_PERSONAL_SANITARIO"},
                {"texto": "3/2/2025", "etiqueta": "FECHAS"}]
        self.assertEqual(deid_eval.guardar_anotacion(self.zona, "01", anot), 0)
        docs = deid_eval.cargar_brat(self.zona)
        etiquetas = sorted(e for _a, _b, e, _s in docs[0][2])
        self.assertEqual(etiquetas.count("NOMBRE_SUJETO_ASISTENCIA"), 2,
                         "«Pérez» dentro del nombre no cuenta aparte; el nombre, en sus 2 apariciones")
        self.assertEqual(deid_eval.offsets_cuadran(docs), 0)

    def test_revision_marca_y_se_niega_fuera(self):
        os.environ["BTP_VENTANILLA"] = "1"
        deid_eval.preparar(self.zona, [self.origen])
        deid_eval.guardar_anotacion(self.zona, "01", [{"texto": "Luz Marín",
                                                       "etiqueta": "NOMBRE_PERSONAL_SANITARIO"}])
        self.assertEqual(deid_eval.revision(self.zona, ["01"]), 0)
        with open(os.path.join(self.zona, "REVISION-01.md"), encoding="utf-8") as fh:
            self.assertIn("⟦Luz Marín⟧", fh.read())
        os.environ.pop("BTP_VENTANILLA")
        self.assertEqual(deid_eval.revision(self.zona, ["01"]), 1)

    def test_detalle_fuera_de_la_ventanilla_se_niega(self):
        os.environ.pop("BTP_VENTANILLA", None)
        _brat(os.path.dirname(self.origen), "x", "Ana", [(0, 3, "NOMBRE")])
        r = deid_eval.main(["--brat", os.path.dirname(self.origen), "--capas", "regex",
                            "--sin-diccionario", "--detalle"])
        self.assertEqual(r, 1)

    def test_contexto_desambigua(self):
        t = "| **Sexo** | F |\n| **F. Nacimiento** | 1990 |"
        lineas, faltan = deid_eval.anotacion_a_brat(
            t, [{"texto": "F", "contexto": "**Sexo** | F", "etiqueta": "SEXO"}])
        self.assertEqual(faltan, [])
        self.assertEqual(len(lineas), 1, lineas)
        self.assertIn("SEXO %d %d" % (t.index("| F |") + 2, t.index("| F |") + 3), lineas[0])

    def test_frontera_de_palabra(self):
        t = "Servicio Murciano. Hospital de Murcia. Anatomía de Ana."
        lineas, faltan = deid_eval.anotacion_a_brat(t, [{"texto": "Murcia", "etiqueta": "TERRITORIO"},
                                                        {"texto": "Ana", "etiqueta": "NOMBRE"}])
        self.assertEqual(faltan, [])
        self.assertEqual(len(lineas), 2, "ni «Murciano» ni «Anatomía» cuentan: %r" % lineas)
        _l, faltan = deid_eval.anotacion_a_brat("Murciano", [{"texto": "Murcia", "etiqueta": "T"}])
        self.assertEqual(faltan, ["Murcia"], "solo dentro de otra palabra = no está")

    def test_texto_que_no_esta_no_guarda_nada(self):
        os.environ["BTP_VENTANILLA"] = "1"
        deid_eval.preparar(self.zona, [self.origen])
        r = deid_eval.guardar_anotacion(self.zona, "01", [{"texto": "Nadie", "etiqueta": "X"}])
        self.assertEqual(r, 4)
        self.assertFalse(os.path.exists(os.path.join(self.zona, "01.ann")))


@unittest.skipUnless(os.path.isdir(MEDDOCAN), "sin MEDDOCAN en _data/: se salta el suelo")
class SueloMeddocan(unittest.TestCase):
    """Cifras del 25-sep-26 (tools/deid_eval.py, 250 documentos, sin diccionario), tras la capa de
    campos de asistencia: regex 0,4916 estricto / 897 fugas · regex+ner 0,9463 / 25 fugas.
    Margen pequeño a propósito."""

    @classmethod
    def setUpClass(cls):
        cls._dicc = deid._DICC
        deid._DICC = []
        cls.docs = deid_eval.cargar_brat(MEDDOCAN)

    @classmethod
    def tearDownClass(cls):
        deid._DICC = cls._dicc

    def test_suelo_regex(self):
        r = deid_eval.evaluar(self.docs, ("regex",))["regex"]
        self.assertGreaterEqual(r["cobertura"]["recall_estricto"], 0.48)
        self.assertGreaterEqual(r["cobertura"]["por_etiqueta"]["NOMBRE_SUJETO_ASISTENCIA"]["recall"], 0.99)
        self.assertLessEqual(r["fugas_salida_real"]["fugas"], 920)

    @unittest.skipUnless(deid._python_ner(), "sin .venv-deid: se salta el suelo con NER")
    def test_suelo_regex_mas_ner(self):
        r = deid_eval.evaluar(self.docs, ("regex+ner",))["regex+ner"]
        self.assertGreaterEqual(r["cobertura"]["recall_estricto"], 0.94)
        self.assertGreaterEqual(r["cobertura"]["precision_caracter"], 0.92)
        self.assertLessEqual(r["fugas_salida_real"]["fugas"], 28)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=1).result
    if res.wasSuccessful():
        print("✅ BANCO DE MEDIDA EN VERDE (%d casos, %d saltados)" % (res.testsRun, len(res.skipped)))
    raise SystemExit(0 if res.wasSuccessful() else 1)
