#!/usr/bin/env python3
"""test_saldo_api.py — «no hay saldo» se dice con ese nombre, no se deduce de cuatro síntomas.

POR QUÉ EXISTE (18-sep-2026). El parte HOY llevaba cinco días sin componerse. El libro de deuda
tenía cuatro hallazgos —parte viejo, «correo-triaje parado», «boca muda», wa-tareas sin señal— y
ninguno decía lo que pasaba. Se diagnosticó como Llavero bloqueado, a partir de un rc 36 leído
desde una sesión SSH (donde ese código no significa eso), y se fue a arreglar lo que no estaba
roto. Mientras tanto la API de Anthropic llevaba desde el 17-sep contestando exactamente qué
pasaba —«Credit balance is too low»— en siete logs de daemon distintos, y nadie los leía.

El estimador de prepago tampoco servía: se declaraba `fiable: False` a la vez que afirmaba 7,43
USD restantes. Por eso este check NO estima: lee la respuesta literal de la API.

Fija las propiedades del check:
  1. La frase en un log reciente -> alerta que nombra la causa y dice que no es cosa del Mac.
  2. Sin la frase -> silencio.
  3. Un 400 viejo no es el de hoy: fuera de la ventana no cuenta.
  4. La evidencia se busca al FINAL del log (es donde está lo reciente; estos logs son enormes).

HERMÉTICO: monta un directorio de logs falso; no lee los de casa base ni toca la red.
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as h  # noqa: E402


class ElSaldoSeNombra(unittest.TestCase):
    def setUp(self):
        self._repo, self._hc = h.REPO, h.HC
        self._tmp = tempfile.TemporaryDirectory()
        h.REPO = self._tmp.name
        h.HC = os.path.join(self._tmp.name, "estado")     # los offsets viven aquí, no en casa base
        self.logs = os.path.join(self._tmp.name, "tools", "launchd", "logs")
        os.makedirs(self.logs)

    def tearDown(self):
        h.REPO, h.HC = self._repo, self._hc
        self._tmp.cleanup()

    def _pasada_previa(self):
        """Deja los offsets al día: simula que el check ya corrió antes (no arranque en frío)."""
        h._check_saldo_api()

    def _log(self, nombre, texto, edad_h=0):
        ruta = os.path.join(self.logs, nombre)
        with open(ruta, "w") as f:
            f.write(texto)
        if edad_h:
            viejo = time.time() - edad_h * 3600
            os.utime(ruta, (viejo, viejo))
        return ruta

    def test_la_frase_de_la_api_dispara_la_alerta(self):
        self._log("hoy-compose.out", "arrancando\n")
        self._pasada_previa()
        self._log("hoy-compose.out", 'arrancando\n{"result":"Credit balance is too low"}\n')
        alertas, info = h._check_saldo_api()
        self.assertEqual([c for c, _t in alertas], ["api_sin_saldo"])
        self.assertEqual(info["daemons_sin_saldo"], ["hoy-compose"])

    def test_el_aviso_dice_que_no_es_cosa_de_la_maquina(self):
        """El error de diagnóstico costó una noche: el aviso tiene que cortar esa rama."""
        self._log("hoy-compose.out", "arrancando\n")
        self._pasada_previa()
        self._log("hoy-compose.out", "arrancando\nCredit balance is too low\n")
        _clave, texto = h._check_saldo_api()[0][0]
        self.assertIn("recargar", texto)

    def test_sin_la_frase_no_alerta(self):
        self._log("hoy-compose.out", "todo bien, parte escrito\n")
        alertas, info = h._check_saldo_api()
        self.assertEqual(alertas, [])
        self.assertEqual(info["daemons_sin_saldo"], [])

    def test_un_400_viejo_no_es_el_de_hoy(self):
        """Estos logs guardan meses. Sin ventana, un incidente de agosto alerta para siempre."""
        self._log("prensa.out", "Credit balance is too low\n", edad_h=h._SALDO_VENTANA_H + 5)
        self.assertEqual(h._check_saldo_api()[0], [])

    def test_la_evidencia_se_busca_al_final_del_log(self):
        """Lo reciente está al final; leer el principio de un log de 400 MB es leer agosto."""
        relleno = "línea de ruido\n" * 20000
        self._log("correo.out", relleno)
        self._pasada_previa()
        self._log("correo.out", relleno + "Credit balance is too low\n")
        self.assertEqual([c for c, _t in h._check_saldo_api()[0]], ["api_sin_saldo"])

    def test_lo_viejo_del_principio_no_cuenta(self):
        relleno = "Credit balance is too low\n" + ("línea de ruido\n" * 20000)
        self._log("correo.out", relleno)
        self._pasada_previa()
        self._log("correo.out", relleno + "sigue trabajando\n")
        self.assertEqual(h._check_saldo_api()[0], [], "un 400 de hace meses no es el de ahora")

    def test_varios_daemons_se_listan(self):
        self._log("hoy-compose.out", "ok\n")
        self._log("correo-urgente.out", "ok\n")
        self._pasada_previa()
        self._log("hoy-compose.out", "ok\nCredit balance is too low\n")
        self._log("correo-urgente.out", "ok\nCredit balance is too low\n")
        _alertas, info = h._check_saldo_api()
        self.assertEqual(sorted(info["daemons_sin_saldo"]), ["correo-urgente", "hoy-compose"])


    def test_un_400_ya_leido_no_vuelve_a_alertar(self):
        """El fallo del 18-sep-26: el saldo ya recargado y el check seguía en SIN SALDO.

        Estos logs son append-only. Con una cola fija, la frase de las 23:00 sigue ahí a las 03:00
        y la alerta no se apaga nunca: el detector se vuelve ruido y se aprende a ignorarlo.
        """
        self._log("hoy-compose.out", "arrancando\n")
        self._pasada_previa()
        self._log("hoy-compose.out", "arrancando\nCredit balance is too low\n")
        self.assertEqual([c for c, _t in h._check_saldo_api()[0]], ["api_sin_saldo"])
        # segunda pasada, sin líneas nuevas: la incidencia ya se contó
        self.assertEqual(h._check_saldo_api()[0], [], "un 400 ya leído no es un 400 nuevo")

    def test_tras_recargar_el_check_se_calla(self):
        self._log("hoy-compose.out", "arrancando\n")
        self._pasada_previa()
        self._log("hoy-compose.out", "arrancando\nCredit balance is too low\n")
        h._check_saldo_api()
        self._log("hoy-compose.out", "arrancando\nCredit balance is too low\nparte escrito\n")
        alertas, info = h._check_saldo_api()
        self.assertEqual(alertas, [])
        self.assertEqual(info["daemons_sin_saldo"], [])

    def test_arranque_en_frio_anota_y_calla(self):
        """Sin offsets previos, leer el log entero es leer el pasado como si fuera el presente."""
        self._log("hoy-compose.out", "Credit balance is too low\n")
        alertas, info = h._check_saldo_api()
        self.assertEqual(alertas, [])
        self.assertTrue(info["arranque_en_frio"])

    def test_un_log_rotado_no_se_pierde(self):
        """Si el log se trunca, el offset viejo apunta más allá del final: hay que releer."""
        self._log("hoy-compose.out", "línea\n" * 500)
        self._pasada_previa()
        self._log("hoy-compose.out", "Credit balance is too low\n")   # rotado: más corto
        self.assertEqual([c for c, _t in h._check_saldo_api()[0]], ["api_sin_saldo"])

    def test_sin_directorio_de_logs_no_revienta(self):
        h.REPO = os.path.join(self._tmp.name, "no-existe")
        alertas, info = h._check_saldo_api()
        self.assertEqual(alertas, [])
        self.assertIn("sin directorio", info["estado"])

    def test_enganchado_en_el_ciclo_de_salud(self):
        """Un check que nadie invoca es decorativo."""
        with open(os.path.join(ROOT, "tools", "healthcheck.py"), encoding="utf-8") as f:
            fuente = f.read()
        marca = "sa_alertas, sa_info = _check_saldo_api()"
        self.assertIn(marca, fuente)
        self.assertIn("alertas.extend(sa_alertas)", fuente.split(marca, 1)[1][:300])


if __name__ == "__main__":
    unittest.main(verbosity=0)
