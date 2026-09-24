#!/usr/bin/env python3
"""test_caso_publico.py — el panel clínico público /datos no puede publicar lo que no debe.

POR QUÉ EXISTE (24-sep-2026). `tools/caso_publico.py` es la única puerta por la que dato clínico
del caso llega a una web pública indexada. {{TITULAR}} quiere que llegue casi todo («nos importa poco
la privacidad»), así que lo que protege aquí es lo poco que sigue fuera y la honestidad del dato:
  · un dato sin fuente o sin sello NO sale (fail-closed, y no se escribe NADA a medias);
  · nombres de terceros, claves administrativas y léxico vetado abortan el build;
  · la cronología de la web se lee con la precisión de cada fecha: «ene–feb 2024» es una franja,
    no un día inventado, y lo ilegible se avisa y no se dibuja;
  · el público es subconjunto estricto del privado (no se cuela nada que no esté curado);
  · la excepción del lint vale SOLO para la estructura del caso: la misma frase clínica suelta
    sigue bloqueada por `web_lint.revisar()`.

Autocontenido (BTP_REPO a un tmp), corre igual en CI.
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_TMP = tempfile.mkdtemp(prefix="caso_publico_test_")
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "tools", "state")
os.environ["BTP_CASO_FUENTE"] = os.path.join(_TMP, "fuente.json")

_RUTA = os.environ.get("BTP_CASO_PUBLICO") or os.path.join(ROOT, "tools", "caso_publico.py")
_spec = importlib.util.spec_from_file_location("caso_publico", _RUTA)
cp = importlib.util.module_from_spec(_spec)
sys.modules["caso_publico"] = cp
_spec.loader.exec_module(cp)

import web_lint  # noqa: E402
import seguimiento as seg  # noqa: E402

WEB = os.path.join(_TMP, "web")

TIMELINE_ES = """entries:

  - date: '2021'
    tag: Antecedentes
    title: Un primer aviso
    description: >
      Texto largo que
      sigue aquí.
    highlight: false

  - date: 'ene–feb 2024'
    tag: Primera línea
    title: 'Empieza el primer tratamiento'
    description: >
      Algo.
    highlight: true

  - date: '8–9 abr 2026'
    tag: IA
    title: Poner todo por escrito
    description: >
      Algo.
    highlight: false

  - date: 'primavera 2024'
    tag: Molecular
    title: Una fecha que nadie puede leer
    description: >
      Algo.
    highlight: false

  - date: '9 sep 2026'
    tag: Progresión
    title: Se acaba el ensayo
    description: >
      Algo.
    highlight: false
    link: https://clinicaltrials.gov/study/NCT07222267
"""

TIMELINE_EN = """entries:
  - date: '2021'
    tag: Background
    title: A first warning
    description: >
      x
    highlight: false
  - date: 'Jan–Feb 2024'
    tag: First line
    title: First treatment starts
    description: >
      x
    highlight: true
  - date: 'Apr 8–9, 2026'
    tag: AI
    title: Writing it all down
    description: >
      x
    highlight: false
  - date: 'spring 2024'
    tag: Molecular
    title: Unreadable
    description: >
      x
    highlight: false
  - date: 'Sep 9, 2026'
    tag: Progression
    title: The trial ends
    description: >
      x
    highlight: false
"""

FUENTE = {
    "version": 1,
    "actualizado": "2026-09-24",
    "fuentes": {
        "ap": {"ruta": "01 · Tratamiento/informes-privados.md",
               "publico": "Informes de anatomía patológica del primario"},
        "mol": {"ruta": "01 · Tratamiento/tabla-molecular.md",
                "publico": "Tabla de alteraciones por muestra"},
    },
    "ficha": {
        "diagnostico": {"valor": "Carcinoma de mama con diferenciación neuroendocrina",
                        "fuente": "ap", "sello": "verificado"},
        "receptores": [{"marcador": "RE", "valor": "95 / 100 / 85 %", "fuente": "ap",
                        "sello": "verificado"}],
    },
    "lineas": [{"id": "1L", "tratamiento": "letrozol + ribociclib", "inicio": "2024-02",
                "fin": "2025-01", "motivo_fin": "progresión", "valor": "1L",
                "fuente": "ap", "sello": "verificado"}],
    "molecular": {
        "alteraciones": [{"gen": "ESR1", "valor": "p.{{VARIANTE}}", "por_muestra": {"L1": "p.{{VARIANTE}}"},
                          "fuente": "mol", "sello": "verificado"}],
    },
    "material": [{"muestra": "Bloque del primario", "codigo": "24B-1043 A1",
                  "donde": "Vall d'Hebron", "valor": "bloque", "fuente": "ap",
                  "sello": "inferido"}],
    "reservorio": [{"fecha": "2026-03-24", "longitud_mm": 150.8, "margen_mm": 5, "valor": 150.8,
                    "fuente": "ap", "sello": "verificado"}],
}

BIO = {
    "generado": "2026-09-24T19:40:06", "n_analiticas": 3, "rango_fechas": ["2026-01-01", "2026-09-08"],
    "grupos": {"marcadores": {"nombre": "Marcadores tumorales", "analitos": [
        {"key": "ca153", "nombre": "CA 15.3", "unidad": "UI/mL", "ref": {"low": 0.0, "high": 23.5},
         "puntos": [
             {"fecha": "2026-01-01", "valor": 20.0, "fuera": False, "confianza": "alta", "fuente": "a.md"},
             {"fecha": "2026-09-08", "valor": 509.5, "fuera": True, "confianza": "alta", "fuente": "b.md",
              "ref_low": 0.0, "ref_high": 30.0},
             {"fecha": "2026-05-01", "valor": 99999.0, "fuera": True, "confianza": "baja", "fuente": "c.md"},
             {"fecha": "2026-06-01", "valor": 40.0, "fuera": True, "confianza": "alta", "fuente": "d.md",
              "ref_low": None, "ref_high": None},
             {"fecha": "2026-08-19", "valor": 60.0, "fuera": False, "confianza": "alta", "fuente": "e.md",
              "ref_low": None, "ref_high": None},
         ]}]},
        "hematologia": {"nombre": "Hematología", "analitos": [
            {"key": "hemoglobina", "nombre": "Hemoglobina", "unidad": "g/dL",
             "ref": {"low": 11.7, "high": 16.1},
             "puntos": [{"fecha": "2026-09-08", "valor": 7.8, "fuera": True, "confianza": "alta",
                         "fuente": "b.md"}]}]}},
}


def _preparar(fuente=None):
    shutil.rmtree(_TMP, ignore_errors=True)
    for lang, txt in (("es", TIMELINE_ES), ("en", TIMELINE_EN)):
        os.makedirs(os.path.join(WEB, "content", lang), exist_ok=True)
        with open(os.path.join(WEB, "content", lang, "timeline.yml"), "w", encoding="utf-8") as f:
            f.write(txt)
    os.makedirs(os.path.join(_TMP, "tools", "state", "biomarcadores"), exist_ok=True)
    with open(cp.BIOMARCADORES, "w", encoding="utf-8") as f:
        json.dump(BIO, f)
    with open(cp.FUENTE, "w", encoding="utf-8") as f:
        json.dump(FUENTE if fuente is None else fuente, f, ensure_ascii=False)


def _publico():
    return os.path.join(WEB, cp.SALIDA_WEB)


class Construye(unittest.TestCase):

    def setUp(self):
        _preparar()
        self.pub = cp.cmd_build(WEB)

    def test_escribe_privado_y_publico(self):
        self.assertTrue(os.path.exists(cp.PRIVADO))
        self.assertTrue(os.path.exists(_publico()))
        with open(_publico(), encoding="utf-8") as a, \
                open(os.path.join(WEB, cp.DESCARGA_WEB), encoding="utf-8") as b:
            self.assertEqual(a.read(), b.read(), "la descarga y la página deben ser el mismo dato")

    def test_publico_es_subconjunto_del_privado_y_sin_rutas(self):
        with open(cp.PRIVADO, encoding="utf-8") as f:
            priv = json.load(f)
        with open(_publico(), encoding="utf-8") as f:
            pub = json.load(f)
        self.assertEqual(cp.es_subconjunto(pub, priv), [])
        for fu in pub["fuentes"].values():
            self.assertNotIn("ruta", fu)
        self.assertNotIn("informes-privados", json.dumps(pub, ensure_ascii=False))

    def test_molecular_solo_en_el_privado_y_reservorio_en_ambos(self):
        """v2 (24-sep-26): lo molecular ya está en /ciencia; /datos se queda en la clínica."""
        with open(cp.PRIVADO, encoding="utf-8") as f:
            priv = json.load(f)
        self.assertIn("molecular", priv)
        self.assertNotIn("molecular", self.pub)
        self.assertNotIn("p.{{VARIANTE}}", json.dumps(self.pub, ensure_ascii=False))
        self.assertEqual(self.pub["reservorio"][0]["longitud_mm"], 150.8)

    def test_precision_de_las_fechas_de_la_cronologia(self):
        ev = {e["titulo"]["es"]: e for e in self.pub["eventos"]}
        franja = ev["Empieza el primer tratamiento"]
        self.assertEqual((franja["desde"], franja["hasta"], franja["precision"]),
                         ("2024-01-01", "2024-02-29", "mes"))
        anio = ev["Un primer aviso"]
        self.assertEqual(anio["precision"], "anio")
        dia = ev["Se acaba el ensayo"]
        self.assertEqual((dia["desde"], dia["hasta"], dia["precision"]),
                         ("2026-09-09", "2026-09-09", "dia"))

    def test_fecha_ilegible_se_avisa_y_no_se_dibuja(self):
        titulos = [e["titulo"]["es"] for e in self.pub["eventos"]]
        self.assertNotIn("Una fecha que nadie puede leer", titulos)
        avisos = [a for a in self.pub["avisos"] if a["tipo"] == "fecha_no_interpretable"]
        self.assertEqual(len(avisos), 1)
        self.assertIn("primavera 2024", avisos[0]["detalle"])

    def test_lo_no_clinico_se_guarda_pero_no_se_dibuja(self):
        ev = {e["titulo"]["es"]: e for e in self.pub["eventos"]}
        self.assertFalse(ev["Poner todo por escrito"]["dibujar"])
        self.assertTrue(ev["Se acaba el ensayo"]["dibujar"])

    def test_titulos_en_emparejados_por_fecha(self):
        ev = {e["titulo"]["es"]: e for e in self.pub["eventos"]}
        self.assertEqual(ev["Se acaba el ensayo"]["titulo"].get("en"), "The trial ends")

    def test_analiticas_fuera_de_rango_con_direccion_y_sin_lo_dudoso(self):
        ca = self.pub["analiticas"]["grupos"]["marcadores"]["analitos"][0]
        self.assertEqual([p["f"] for p in ca["puntos"]],
                         ["2026-01-01", "2026-06-01", "2026-08-19", "2026-09-08"])
        # sin rango propio y sin marca del informe: se juzga contra la MISMA banda del ×LSN
        self.assertEqual(ca["puntos"][2]["fuera"], "alto")
        ult = ca["puntos"][-1]
        self.assertEqual((ult["fuera"], ult["hi"], ult["ref_de"]), ("alto", 30.0, "informe"))
        # informe sin rango (clave presente y a None): banda más frecuente, y se dice
        sin = ca["puntos"][1]
        self.assertEqual((sin["hi"], sin["ref_de"], sin["fuera"]), (23.5, "banda", "alto"))
        self.assertIsNone(ca["puntos"][0]["fuera"])
        hb = self.pub["analiticas"]["grupos"]["hematologia"]["analitos"][0]["puntos"][0]
        self.assertEqual(hb["fuera"], "bajo")


class FallaCerrado(unittest.TestCase):

    def _falla(self, fuente, trozo):
        _preparar(fuente)
        with self.assertRaises(cp.ErrorCaso) as cm:
            cp.cmd_build(WEB)
        self.assertTrue(any(trozo in x for x in cm.exception.fallos),
                        "esperaba «%s» en %s" % (trozo, cm.exception.fallos))
        self.assertFalse(os.path.exists(_publico()), "no debe quedar un caso.json a medias")
        self.assertFalse(os.path.exists(cp.PRIVADO))

    def test_dato_sin_fuente(self):
        f = copy.deepcopy(FUENTE)
        del f["ficha"]["diagnostico"]["fuente"]
        self._falla(f, "ficha.diagnostico")

    def test_fuente_no_declarada(self):
        f = copy.deepcopy(FUENTE)
        f["lineas"][0]["fuente"] = "inventada"
        self._falla(f, "lineas[0]")

    def test_sello_no_valido(self):
        f = copy.deepcopy(FUENTE)
        f["material"][0]["sello"] = "seguro"
        self._falla(f, "material[0]")

    def test_persona_nombrada(self):
        f = copy.deepcopy(FUENTE)
        f["lineas"][0]["nota"] = "Lo pautó la Dra. Zurbarán en consulta"
        self._falla(f, "persona nombrada")

    def test_nombre_de_la_deny_list(self):
        f = copy.deepcopy(FUENTE)
        f["material"][0]["donde"] = "Laboratorio de Quimerina"
        viejo = seg._NOMBRES_DENY
        seg._NOMBRES_DENY = frozenset(viejo | {"quimerina"})
        try:
            self._falla(f, "deny-list")
        finally:
            seg._NOMBRES_DENY = viejo

    def test_clave_administrativa(self):
        f = copy.deepcopy(FUENTE)
        f["material"][0]["estado"] = "CIP: MUGO123456789012"
        self._falla(f, "clave administrativa")

    def test_lexico_vetado(self):
        f = copy.deepcopy(FUENTE)
        f["fuentes"]["ap"]["publico"] = "Informe de la ingeniera del caso"
        self._falla(f, "ingeniera")

    def test_contacto(self):
        f = copy.deepcopy(FUENTE)
        f["ficha"]["diagnostico"]["valor"] = "Ver {{CONTACTO}}"
        self._falla(f, "{{CONTACTO}}")

    def test_cronologia_con_forma_desconocida(self):
        _preparar()
        with open(os.path.join(WEB, "content", "es", "timeline.yml"), "a", encoding="utf-8") as fh:
            fh.write("otra_cosa: [1, 2]\n")
        with self.assertRaises(cp.ErrorCaso):
            cp.cmd_build(WEB)


class SoloLoDelEsquema(unittest.TestCase):

    def test_clave_desconocida_no_se_publica_y_se_avisa(self):
        f = copy.deepcopy(FUENTE)
        f["notas_internas"] = {"valor": "algo interno", "fuente": "ap", "sello": "inferido"}
        f["ficha"]["comentario_libre"] = {"valor": "x", "fuente": "ap", "sello": "inferido"}
        _preparar(f)
        pub = cp.cmd_build(WEB)
        self.assertNotIn("notas_internas", pub)
        self.assertNotIn("comentario_libre", pub["ficha"])
        detalles = " ".join(a["detalle"] for a in pub["avisos"])
        self.assertIn("notas_internas", detalles)
        self.assertIn("ficha.comentario_libre", detalles)


class LaExcepcionEsSoloParaElCaso(unittest.TestCase):

    def test_lo_clinico_pasa_dentro_del_caso_y_no_suelto(self):
        _preparar()
        pub = cp.cmd_build(WEB)
        self.assertEqual(web_lint.revisar_caso(pub), [])
        suelto = "La variante ESR1 p.{{VARIANTE}} aparece en la ultima biopsia liquida del caso."
        self.assertIn("clinico", {c for c, _ in web_lint.revisar(suelto)})

    def test_un_dato_sin_sello_rompe_el_caso(self):
        _preparar()
        pub = cp.cmd_build(WEB)
        pub["ficha"]["diagnostico"].pop("sello")
        self.assertIn("estructura", {c for c, _ in web_lint.revisar_caso(pub)})


class LectorDeFechas(unittest.TestCase):

    def test_selftest(self):
        self.assertEqual(cp._selftest(), 0)

    def test_iso_parcial(self):
        self.assertEqual(cp.fecha_iso_parcial("2024-02")["hasta"], "2024-02-29")
        self.assertEqual(cp.fecha_iso_parcial("2024")["precision"], "anio")
        self.assertIsNone(cp.fecha_iso_parcial("2024-13"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
