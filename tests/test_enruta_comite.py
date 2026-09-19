#!/usr/bin/env python3
"""Test: la centralita de COMITÉS decide quién analiza, y los dos frenos que la acompañan.

POR QUÉ EXISTE (5-sep-2026). Lo vio {{TITULAR}}, no el sistema: *«¿por qué no toca el comité médico
esto, si principalmente he hecho Polaris para esto?»*. Una sesión entró por «ayúdame a interpretar
esta imagen», creció hasta el análisis exhaustivo del panel molecular del nodo ⭐NED y lo hizo un
solo LLM a pelo. El watchdog, lanzado después a mano, encontró dos transcripciones falsas y una
dirección de magnitud invertida en lo ya entregado.

La causa NO fue el olvido: **nadie decidía quién analiza**. `enruta.py` decide MODELO,
`orquestadores.json` decide BINARIO, `audit_comites.py` audita a posteriori. Faltaba la pieza de
en medio, y faltaba que la clasificación se revisara cuando la tarea crece (se hacía una vez, al
entrar, y no se volvía a mirar).

Aquí se prueban las tres piezas:
  1. `tools/enruta_comite.py`      — la centralita: qué comité toca y si es obligatorio.
  2. `.claude/hooks/enrutado_guard.py` — avisa al ENTRAR, y hereda el nivel del contexto de sesión.
  3. `gate_salida.py::clinico_sin_comite` — avisa al SALIR, si van cifras sin que nadie las tocara.

Lo que más importa de todo esto es el caso `test_no_salta_con_cualquier_cosa`: un guard que grita
en cada turno se ignora, y un guard ignorado es peor que no tenerlo porque da falsa sensación de
red. La regla de {{TITULAR}} manda aquí: gastar en comités es decisión consciente, no reflejo.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))

import enruta_comite            # noqa: E402
import enrutado_guard           # noqa: E402
import gate_salida              # noqa: E402

# Compuesta a trozos a propósito: escrita entera, este fichero dispararía el `clinico_guard` cada
# vez que alguien corriera la batería desde bash.
ZONA = "_PRIVADO_" + "CLINICO"


class TestCentralita(unittest.TestCase):
    def test_material_molecular_exige_comite_y_watchdog(self):
        """El caso exacto del 5-sep: análisis de panel molecular."""
        d = enruta_comite.decidir("haz un análisis exhaustivo del informe molecular de hueso")
        self.assertTrue(d["obligatorio"])
        self.assertIn("comite-medico", d["comites"])
        self.assertIn("verificacion", d["comites"], "el comité sin watchdog es la mitad del freno")

    def test_biomarcadores_sueltos_tambien_disparan(self):
        """«¿qué significa HRD negativo y TMB 4?» no lleva la palabra «informe» y es igual de serio."""
        for frase in ("qué significa HRD negativo y TMB 4",
                      "el número de copias de CCND1 subió",
                      "esto reafirma el circuito autocrino de la lesión de cresta ilíaca"):
            with self.subTest(frase=frase):
                self.assertTrue(enruta_comite.decidir(frase)["obligatorio"], frase)

    def test_no_salta_con_cualquier_cosa(self):
        """Anti-ruido. Si grita siempre, se ignora, y entonces no protege de nada."""
        for frase in ("qué tal el día", "resume mis correos de hoy", "haz commit de esto",
                      "ordena la carpeta de descargas", "cuánto queda para la cita"):
            with self.subTest(frase=frase):
                self.assertFalse(enruta_comite.decidir(frase)["obligatorio"], frase)

    def test_la_x_de_la_red_social_no_casa_con_cualquier_x(self):
        """Regresión: un `\\bx\\b` suelto convertía media conversación en tarea de redes."""
        d = enruta_comite.decidir("el eje x del gráfico está mal")
        self.assertNotIn("redes-contenido", d["comites"])

    def test_profundidad_eleva_a_obligatorio(self):
        """Lo que iba por ALTO y encima pide profundidad deja de ser opcional."""
        suave = enruta_comite.decidir("mira la literatura sobre esto")
        fuerte = enruta_comite.decidir("mira la literatura sobre esto a fondo, es para decidir")
        self.assertEqual(suave["nivel"], enruta_comite.ALTO)
        self.assertTrue(fuerte["obligatorio"])

    def test_solo_propone_comites_que_existen(self):
        for c in enruta_comite.decidir("analiza el informe molecular")["comites"]:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, ".claude", "agents", c + ".md")), c)

    def test_nunca_revienta(self):
        for entrada in (None, "", " ", "x" * 5000, "🙂", 12345):
            with self.subTest(entrada=repr(entrada)[:20]):
                self.assertIsInstance(enruta_comite.decidir(entrada), dict)


class TestGuardDeEntrada(unittest.TestCase):
    def test_selftest_del_hook(self):
        """El hook es fail-open: sin este selftest, un ImportError lo deja mudo y nadie se entera."""
        self.assertEqual(enrutado_guard._selftest(), 0)

    def test_hereda_el_nivel_del_contexto_de_sesion(self):
        """LA pieza que faltaba: «¿y esto qué es?» tras haber abierto material clínico."""
        linea = enrutado_guard.linea_tool("Read", {"file_path": "01 · Tratamiento/%s/x.md" % ZONA})
        self.assertTrue(enrutado_guard._contexto_clinico([linea]))
        self.assertFalse(enrutado_guard._contexto_clinico(
            [enrutado_guard.linea_tool("Read", {"file_path": "README.md"})]))

    def test_nombrar_la_ventanilla_clinica_no_es_abrir_material_clinico(self):
        """Regresión 11-sep-26: explicar el sistema con la palabra «lector_clinico», o hacer `cat`
        de settings.json, declaraba clínica una sesión que no había abierto nada."""
        import json
        prosa = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "obliga a pasar por lector_" + "clinico.py"}]}})
        salida = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "... lector_" + "clinico.py (la ventanilla) ..."}]}})
        self.assertFalse(enrutado_guard._contexto_clinico([prosa, salida]))
        uso = enrutado_guard.linea_tool("Bash", {"command": "python3 tools/lector_" + "clinico.py x"})
        self.assertTrue(enrutado_guard._contexto_clinico([uso]))
        encadenado = enrutado_guard.linea_tool("Bash", {"command": "cd /repo && python3 tools/lector_"
                                                                   + "clinico.py x"})
        self.assertTrue(enrutado_guard._contexto_clinico([encadenado]))
        for mencion in ('python3 tools/deuda.py abrir "k" "el guard mira lector_' + 'clinico"',
                        "git commit -m 'arregla lector_" + "clinico.py en el guard'",
                        "grep -n lector_" + "clinico .claude/settings.json"):
            with self.subTest(mencion=mencion):
                self.assertFalse(enrutado_guard._contexto_clinico(
                    [enrutado_guard.linea_tool("Bash", {"command": mencion})]))

    def test_ve_que_el_comite_ya_trabajo_en_esta_sesion(self):
        hechos = enrutado_guard._comites_ya_trabajados(
            [enrutado_guard.linea_tool("Agent", {"subagent_type": "comite-medico"})])
        self.assertIn("comite-medico", hechos)

    def test_no_usa_corrio_hoy_como_prueba(self):
        """Que el comité corriera esta mañana por otra cosa no prueba que haya tocado ESTO.
        Usarlo metía el falso negativo que este hook viene a eliminar."""
        self.assertEqual(enrutado_guard._comites_ya_trabajados([]), set())


class TestGuardDeSalida(unittest.TestCase):
    CIFRAS = ("El número de copias de FGFR1 pasa de 13 a 33, lo que convierte al módulo en una "
              "diana con mPFS de 9,3 meses (HR=0,24).")

    def test_avisa_si_van_cifras_sin_comite(self):
        self.assertIsNotNone(gate_salida.clinico_sin_comite(self.CIFRAS, tools=["Bash", "kb.py"]))

    def test_calla_si_el_comite_toco_el_turno(self):
        self.assertIsNone(gate_salida.clinico_sin_comite(self.CIFRAS,
                                                         tools=["Agent", "comite-medico"]))
        self.assertIsNone(gate_salida.clinico_sin_comite(self.CIFRAS,
                                                         tools=["Agent", "verificacion"]))

    def test_no_acusa_a_ciegas_sin_transcript(self):
        """Si no se puede saber qué tools se usaron, no se acusa. Es la regla del fichero."""
        self.assertIsNone(gate_salida.clinico_sin_comite(self.CIFRAS, tools=None))

    def test_texto_sin_carga_clinica_no_dispara(self):
        self.assertIsNone(gate_salida.clinico_sin_comite(
            "te he dejado el vuelo a Zúrich a un clic, sale a las 9,3 de la mañana", tools=["Bash"]))

    def test_la_norma_esta_registrada_y_activa(self):
        activas, _modo = gate_salida._reglas_activas()
        checks = [c for c, _s, _m in activas]
        self.assertIn("clinico_sin_comite", checks,
                      "sin entrada en tools/normas.json el check existe pero no corre")


if __name__ == "__main__":
    unittest.main(verbosity=2)
