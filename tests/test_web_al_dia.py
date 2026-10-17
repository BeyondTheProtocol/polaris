#!/usr/bin/env python3
"""test_web_al_dia.py — el detector de «la web aún no cuenta esto» ni calla ni repite.

POR QUÉ EXISTE (17-oct-2026, plan «web al día»). Los resultados del single-cell llegaron y la web
siguió diciendo «en marcha». Este detector tiene que ver esa clase de hecho (en las dos
direcciones), no proponer nada sin evidencia y no avisar dos veces de lo mismo, que es el bug de
clase de todo aviso sin dedup (`feedback-todo-aviso-a-titular-necesita-anti-spam`).
"""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import web_al_dia as A  # noqa: E402

HOY = datetime.date(2026, 10, 17)
DESDE = datetime.date(2026, 10, 7)


def fila(id_, tono, hilo):
    return {"id": id_, "pagina": "ciencia", "tono": tono, "valor": {"es": "X", "en": "X"},
            "fuente": {"tipo": "tablero", "hilo": hilo}}


MAPA = {"afirmaciones": [fila("ciencia-wes", "curso", "h-wes"),
                         fila("ciencia-panel", "hecho", "h-panel"),
                         fila("ciencia-germinal", "pendiente", "h-priv"),
                         {"id": "ciencia-sin-fuente", "pagina": "ciencia", "tono": "curso",
                          "fuente": None}]}


class CarrilA(unittest.TestCase):
    def test_avanza_con_evidencia_se_encola_prioridad_1(self):
        hs = A.hechos_estados(MAPA, {"h-wes": {"estado": "hecho"}}, {"ciencia-wes": "nota.md"})
        self.assertEqual([(h["carril"], h["prioridad"], h["estado"]) for h in hs], [("A", 1, "encolado")])

    def test_sin_evidencia_queda_por_confirmar(self):
        """El Tablero solo no basta: el hilo del scRNA seguía «esperando» con los resultados
        ya publicados (17-oct)."""
        hs = A.hechos_estados(MAPA, {"h-wes": {"estado": "hecho"}}, {})
        self.assertEqual([h["estado"] for h in hs], ["por_confirmar"])

    def test_retroceso_tambien_es_falso(self):
        hs = A.hechos_estados(MAPA, {"h-panel": {"estado": "esperando"}}, {"ciencia-panel": "n.md"})
        self.assertEqual(len(hs), 1)
        self.assertIn("retrocede", hs[0]["detalle"])

    def test_coherente_no_da_nada(self):
        self.assertEqual(A.hechos_estados(MAPA, {"h-wes": {"estado": "esperando"},
                                                 "h-panel": {"estado": "hecho"}}, {}), [])

    def test_hilo_privado_no_se_usa(self):
        self.assertEqual(A.hechos_estados(MAPA, {"h-priv": {"estado": "hecho", "privado": True}},
                                          {"ciencia-germinal": "n.md"}), [])


class CarrilesByC(unittest.TestCase):
    def test_datos_atrasados(self):
        hs = A.hechos_datos([{"clave": "datos-atrasados", "titulo": "t", "detalle": "d"},
                             {"clave": "sitemap-con-404", "titulo": "x", "detalle": "y"}])
        self.assertEqual([(h["carril"], h["prioridad"], h["estado"]) for h in hs], [("B", 2, "encolado")])

    def test_informes_solo_posteriores_a_la_ultima_entrada(self):
        hs = A.hechos_informes(["· 2026-10-15 · Anatomía patológica · informe",
                                "· 2026-10-01 · Analíticas · viejo", "basura"], DESDE)
        self.assertEqual(len(hs), 1)
        self.assertNotIn("informe", hs[0]["detalle"])   # la descripción no se copia

    def test_prs_excluye_auto_y_mecanicos(self):
        prs = [{"number": 280, "title": "feat(celula-a-celula): primeros resultados",
                "headRefName": "x", "mergedAt": "2026-10-17T08:46:51Z"},
               {"number": 278, "title": "fix(a11y): anillo", "headRefName": "y", "mergedAt": "2026-10-15T00:00:00Z"},
               {"number": 290, "title": "feat: cronología", "headRefName": "auto/novedad-x",
                "mergedAt": "2026-10-17T00:00:00Z"},
               {"number": 260, "title": "feat(viejo)", "headRefName": "z", "mergedAt": "2026-10-01T00:00:00Z"}]
        self.assertEqual([h["clave"] for h in A.hechos_prs(prs, DESDE)], ["pr:celula-a-celula:280"])

    def test_prs_de_un_ambito_son_un_hecho_y_si_uno_toco_la_cronologia_ya_esta_contado(self):
        """El caso real del 17-oct: #280–#283 son una página; #282 ya metió su entrada."""
        def pr(n, files=()):
            return {"number": n, "title": "feat(celula-a-celula): x", "headRefName": "r%d" % n,
                    "mergedAt": "2026-10-17T10:00:00Z", "files": [{"path": f} for f in files]}
        self.assertEqual(len(A.hechos_prs([pr(280), pr(281), pr(283)], DESDE)), 1)
        self.assertEqual(A.hechos_prs([pr(280), pr(282, ["content/es/timeline.yml"]), pr(283)], DESDE), [])

    def test_detectar_ordena_por_prioridad(self):
        hs = A.detectar(MAPA, {"h-wes": {"estado": "hecho"}}, {"ciencia-wes": "n"},
                        [{"clave": "datos-atrasados", "titulo": "t", "detalle": "d"}],
                        ["· 2026-10-15 · AP · x"], [], DESDE)
        self.assertEqual([h["prioridad"] for h in hs], [1, 2, 3])


class DedupYSombra(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def hs(self, evidencia=True):
        return A.hechos_estados(MAPA, {"h-wes": {"estado": "hecho"}},
                                {"ciencia-wes": "n.md"} if evidencia else {})

    def test_el_mismo_hecho_dos_pasadas_se_apunta_una_vez(self):
        self.assertEqual(len(A.registrar(self.hs(), "sombra", HOY, self.dir)), 1)
        self.assertEqual(A.registrar(self.hs(), "sombra", HOY, self.dir), [])
        with open(os.path.join(self.dir, "al_dia_sombra.jsonl")) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_por_confirmar_que_gana_evidencia_vuelve_a_salir(self):
        self.assertEqual(A.registrar(self.hs(False), "sombra", HOY, self.dir)[0]["estado"], "por_confirmar")
        self.assertEqual(A.registrar(self.hs(False), "sombra", HOY, self.dir), [])
        self.assertEqual(A.registrar(self.hs(True), "sombra", HOY, self.dir)[0]["estado"], "encolado")

    def test_lo_visto_en_sombra_no_tapa_el_modo_activo(self):
        A.registrar(self.hs(), "sombra", HOY, self.dir)
        self.assertEqual(len(A.registrar(self.hs(), "activo", HOY, self.dir)), 1)

    def test_activo_no_escribe_el_registro_de_sombra(self):
        A.registrar(self.hs(), "activo", HOY, self.dir)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "al_dia_sombra.jsonl")))


class NotasAfecta(unittest.TestCase):
    def test_lee_afecta_y_salta_privado_y_viejo(self):
        raiz = tempfile.mkdtemp()
        os.makedirs(os.path.join(raiz, "04 · IA", "Notas"))
        os.makedirs(os.path.join(raiz, "_PRIVADO_WHATSAPP"))
        buena = os.path.join(raiz, "04 · IA", "Notas", "scrna.md")
        with open(buena, "w") as f:
            f.write("# Resultados\nafecta: ciencia-scrna-flex\n")
        with open(os.path.join(raiz, "_PRIVADO_WHATSAPP", "chat.md"), "w") as f:
            f.write("afecta: ciencia-wes\n")
        vieja = os.path.join(raiz, "04 · IA", "Notas", "vieja.md")
        with open(vieja, "w") as f:
            f.write("afecta: ciencia-panel-tejido\n")
        hace = time.time() - 200 * 86400
        os.utime(vieja, (hace, hace))
        with open(os.path.join(raiz, "04 · IA", "Notas", "wes.md"), "w") as f:
            f.write("afecta: ciencia-wes → resultados\n")
        n = A.leer_notas_afecta(raiz, hoy=datetime.date.today())
        self.assertEqual(set(n), {"ciencia-scrna-flex", "ciencia-wes"})
        self.assertEqual(n["ciencia-wes"][1], "resultados")

    def test_el_destino_de_la_nota_llega_al_hecho(self):
        hs = A.hechos_estados(MAPA, {"h-wes": {"estado": "hecho"}}, {"ciencia-wes": ("n.md", "resultados")})
        self.assertEqual(hs[0]["datos"], {"id": "ciencia-wes", "sentido": "avanza", "destino": "resultados"})
        self.assertEqual(hs[0]["evidencia"], "n.md")
        hs = A.hechos_estados(MAPA, {"h-wes": {"estado": "hecho"}}, {"ciencia-wes": "n.md"})
        self.assertEqual(hs[0]["datos"]["destino"], "hecho")


if __name__ == "__main__":
    unittest.main(verbosity=2)
