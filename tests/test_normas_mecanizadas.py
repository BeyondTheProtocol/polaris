#!/usr/bin/env python3
"""test_normas_mecanizadas.py — Fase 1: dos normas de {{TITULAR}} dejan de depender de que yo me acuerde.

POR QUÉ EXISTE (31-jul-2026). La Fase 0 metió sus 198 normas en el registro y dejó el hueco a la
vista: solo 23 tienen un freno real. Esta es la primera tanda de frenos nuevos. Se eligieron por
riesgo, no por facilidad — las dos han hecho daño ya:

  · `feedback-secretos-solo-llavero` — los secretos viven SOLO en el Llavero de macOS. Un
    `*_secrets.json` en claro en disco es una clave esperando a que alguien la lea o la commitee.
  · `feedback-envio-opt-in-para-no-spamear-en-tests` — el 12-jul-2026 una pasada de tests le mandó
    **14 mensajes reales** a {{TITULAR}} por Telegram. `salida.send()` solo enmudece si
    `BTP_TEST_BATTERY=1` **y** el STATE está aislado; un test que importe `salida` sin las dos
    cosas puede entregarle de verdad.

Nota honesta sobre el alcance: NO se enlazaron las ~74 normas cuyo slug aparece en
`tests/test_recall_memoria.py` o `tools/indice_memoria.py`. Ese test evalúa el RANKING del recall
(que la memoria salga en el top-3), que es exactamente lo que este registro nace desconfiando:
una norma no está mecanizada porque se pueda recordar. Contarlas habría sido falso verde.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# Los que la propia batería excluye a propósito (test_all.sh lo documenta): necesitan que
# salida.send() llegue hasta la boca, así que NO los corre la suite y se lanzan a mano.
FUERA_DE_LA_BATERIA = {
    "test_avisos_origen.py", "test_casa_estilo.py", "test_observatorio.py",
    "test_salida.py", "test_tablero.py", "test_triage.py",
}


def _ficheros_test():
    return sorted(f for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py"))


class SecretosSoloEnElLlavero(unittest.TestCase):
    """feedback-secretos-solo-llavero"""

    def test_no_hay_ficheros_de_secreto_en_claro(self):
        """Incluye las VARIANTES con sufijo (31-jul-26).

        La primera versión solo miraba nombres acabados en `_secrets.json`. El mismo día, la
        limpieza de las claves dejó tres `.sobra.bak` con las claves dentro: el fichero seguía en
        claro, el freno no lo veía y además `*_secrets.json` del .gitignore tampoco los cubría, o
        sea que podían acabar commiteados. Renombrar no es limpiar.
        """
        encontrados = []
        for base, dirs, ficheros in os.walk(ROOT):
            dirs[:] = [d for d in dirs
                       if d not in (".git", "node_modules", "__pycache__", ".claude")]
            for f in ficheros:
                if ".example." in f:            # plantillas sin claves: es lo que se versiona
                    continue
                if "_secrets.json" in f or "_secrets.yaml" in f:
                    encontrados.append(os.path.relpath(os.path.join(base, f), ROOT))
        self.assertEqual(encontrados, [],
                         "secretos en claro en disco (van SOLO al Llavero): %s" % encontrados)


class LosTestsNoPuedenEscribirleATitular(unittest.TestCase):
    """feedback-envio-opt-in-para-no-spamear-en-tests"""

    def setUp(self):
        self.sospechosos = []
        for nombre in _ficheros_test():
            if nombre in FUERA_DE_LA_BATERIA:
                continue
            ruta = os.path.join(TESTS, nombre)
            with open(ruta, encoding="utf-8", errors="replace") as f:
                src = f.read()
            if not re.search(r"^\s*(import salida|from salida import)", src, re.M):
                continue
            protegido = (
                "BTP_TEST_BATTERY" in src                       # mutis explícito de salida.py
                or re.search(r"salida\.(send|report_to_titular|report)\s*=", src)   # monkeypatch
                or "monkeypatch" in src or "mock" in src or "MagicMock" in src
            )
            if not protegido:
                self.sospechosos.append(nombre)

    def test_ningun_test_de_la_bateria_puede_entregar_de_verdad(self):
        self.assertEqual(self.sospechosos, [],
                         "tests que importan `salida` sin BTP_TEST_BATTERY ni neutralizarla: %s"
                         % self.sospechosos)

    def test_la_lista_de_excluidos_sigue_fuera_de_la_bateria(self):
        """Si alguien mete un excluido en test_all.sh, la batería le manda Telegram real."""
        with open(os.path.join(TESTS, "test_all.sh"), encoding="utf-8") as f:
            runner = f.read()
        invocados = set(re.findall(r"^\s*runpy\s+(test_\w+\.py)", runner, re.M))
        colados = sorted(FUERA_DE_LA_BATERIA & invocados)
        self.assertEqual(colados, [], "excluidos que la batería SÍ corre: %s" % colados)


if __name__ == "__main__":
    unittest.main(verbosity=2)
