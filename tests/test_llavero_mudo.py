#!/usr/bin/env python3
"""test_llavero_mudo.py — un Llavero bloqueado se dice, no se deduce de cuatro síntomas.

POR QUÉ EXISTE (17-sep-2026). El Mac se reinició el 13-sep 13:26 y el `login.keychain` quedó
bloqueado: desde ese momento ningún proceso pudo leer una clave. El lazo no se murió de golpe,
se fue apagando por trozos, y cada trozo abrió su propio hallazgo en el libro de deuda: el parte
HOY congelado desde el 13-sep, «correo-triaje parado», «boca muda», wa-tareas sin señal. Cuatro
días, siete hallazgos escalados, y ninguno decía lo único que había que hacer: desbloquearlo.

Peor: `hoy_compose.sh` hacía `exec run_agent.sh`, el agente imprimía «Falta btp-anthropic-api en
el Llavero» y salía con **0**. launchd lo apuntaba como pasada correcta. Falso verde perfecto:
el daemon en verde y el parte de cuatro días atrás.

Fija las dos mitades del arreglo:
  A. `healthcheck._check_llavero` nombra la causa, distingue «Llavero mudo» de «falta ese ítem»
     y no inventa alarmas con lo que no entiende.
  B. `hoy_compose.sh` no puede volver a salir en verde sin haber reescrito el parte.

HERMÉTICO: `subprocess.run` mockeado en A; en B, un repo falso vía BTP_REPO con un
`run_agent.sh` de mentira. No toca el Llavero real ni lanza ningún agente.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import healthcheck as h  # noqa: E402


class _R:
    def __init__(self, rc, stdout=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


def _mock(por_servicio, contexto="Aqua"):
    """Mapea servicio -> código de salida de `security`, y finge el contexto de sesión.

    `contexto` es lo que devolvería `launchctl managername`: "Aqua" en la sesión gráfica (donde
    corren los LaunchAgents), "Background" en una SSH o un job de fondo.
    """
    def run(cmd, **kw):
        if cmd[:2] == ["launchctl", "managername"]:
            return _R(0, contexto)
        srv = cmd[cmd.index("-s") + 1] if "-s" in cmd else ""
        return _R(por_servicio.get(srv, 0))
    return run


class ElLlaveroSeNombra(unittest.TestCase):
    def setUp(self):
        self._real = h.subprocess.run

    def tearDown(self):
        h.subprocess.run = self._real

    def test_llavero_legible_no_alerta(self):
        h.subprocess.run = _mock({s: 0 for s in h._LLAVERO_SONDAS})
        alertas, info = h._check_llavero()
        self.assertEqual(alertas, [])
        self.assertEqual(info["estado"], "legible")

    def test_llavero_mudo_alerta_con_la_causa(self):
        h.subprocess.run = _mock({s: 36 for s in h._LLAVERO_SONDAS})
        alertas, info = h._check_llavero()
        self.assertEqual(len(alertas), 1)
        clave, texto = alertas[0]
        self.assertEqual(clave, "llavero_bloqueado")
        self.assertEqual(info["estado"], "BLOQUEADO")

    def test_el_aviso_trae_el_comando_que_lo_arregla(self):
        """Un aviso que no dice qué hacer deja a {{TITULAR}} buscando. Aquí el arreglo va dentro."""
        h.subprocess.run = _mock({s: 36 for s in h._LLAVERO_SONDAS})
        _clave, texto = h._check_llavero()[0][0]
        self.assertIn("unlock-keychain", texto)


    def test_fuera_de_la_sesion_grafica_NO_concluye(self):
        """El fallo del 18-sep-26: rc 36 en una SSH no significa «bloqueado».

        El estado desbloqueado vive en la sesión de seguridad. Un proceso fuera de Aqua recibe 36
        aunque el Llavero esté abierto para los LaunchAgents, que son quienes trabajan. La primera
        versión alertó desde una SSH, se tomó por causa raíz del parte congelado, y la causa era
        el saldo de API agotado: se fue a arreglar lo que no estaba roto.
        """
        h.subprocess.run = _mock({s: 36 for s in h._LLAVERO_SONDAS}, contexto="Background")
        alertas, info = h._check_llavero()
        self.assertEqual(alertas, [], "no se puede acusar desde donde no se puede mirar")
        self.assertIn("no concluyente", info["estado"])

    def test_en_la_sesion_grafica_si_concluye(self):
        h.subprocess.run = _mock({s: 36 for s in h._LLAVERO_SONDAS}, contexto="Aqua")
        alertas, _info = h._check_llavero()
        self.assertEqual([c for c, _t in alertas], ["llavero_bloqueado"])

    def test_el_contexto_queda_anotado(self):
        """Sin verlo en el info{}, el próximo que lea el log repite el error de diagnóstico."""
        h.subprocess.run = _mock({s: 0 for s in h._LLAVERO_SONDAS}, contexto="Aqua")
        self.assertEqual(h._check_llavero()[1]["contexto"], "Aqua")

    def test_falta_un_item_NO_es_el_llavero(self):
        """errSecItemNotFound = esa integración no está montada. Alertar sería ruido diario."""
        h.subprocess.run = _mock({s: 44 for s in h._LLAVERO_SONDAS})
        alertas, info = h._check_llavero()
        self.assertEqual(alertas, [])
        self.assertIn("faltan", info["estado"])

    def test_mixto_no_alerta(self):
        """Una lee y otra no: el Llavero responde. No es la causa raíz que este check nombra."""
        s1, s2 = h._LLAVERO_SONDAS
        h.subprocess.run = _mock({s1: 0, s2: 36})
        self.assertEqual(h._check_llavero()[0], [])

    def test_sin_binario_security_calla(self):
        def run(cmd, **kw):
            raise FileNotFoundError("security")
        h.subprocess.run = run
        alertas, info = h._check_llavero()
        self.assertEqual(alertas, [])
        self.assertIn("security", info["estado"])

    def test_la_sonda_no_se_queda_con_el_secreto(self):
        """Solo se mira el código de salida. Un secreto en un info{} acaba en un log."""
        h.subprocess.run = _mock({s: 0 for s in h._LLAVERO_SONDAS})
        _alertas, info = h._check_llavero()
        self.assertNotIn("stdout", repr(info))
        self.assertEqual(set(info) - {"rc", "estado", "contexto"}, set())

    def test_enganchado_en_el_ciclo_de_salud(self):
        """Un check que nadie invoca es decorativo: el hueco costó cuatro días."""
        with open(os.path.join(ROOT, "tools", "healthcheck.py"), encoding="utf-8") as f:
            fuente = f.read()
        marca = "lv_alertas, lv_info = _check_llavero()"
        self.assertIn(marca, fuente)
        despues = fuente.split(marca, 1)[1][:300]
        self.assertIn("alertas.extend(lv_alertas)", despues)


class ElParteNoSaleEnVerdeSinParte(unittest.TestCase):
    """B: `hoy_compose.sh` comprueba el EFECTO (el fichero reescrito), no el código del agente."""

    def _repo_falso(self, tmp, escribe, salida_falsa=None):
        """Monta un repo mínimo con un run_agent.sh que escribe el parte o no."""
        os.makedirs(os.path.join(tmp, "tools"), exist_ok=True)
        hoy = os.path.join(tmp, "00_FUENTE-DE-VERDAD", "Gestion")
        os.makedirs(hoy, exist_ok=True)
        with open(os.path.join(hoy, "HOY.md"), "w") as f:
            f.write("parte viejo\n")
        os.utime(os.path.join(hoy, "HOY.md"), (1000000, 1000000))   # mtime fijo y antiguo
        ra = os.path.join(tmp, "tools", "run_agent.sh")
        cuerpo = ((salida_falsa or '#!/bin/bash\necho "Falta btp-anthropic-api en el Llavero."\nexit 0\n')
                  if not escribe else
                  '#!/bin/bash\necho "parte nuevo" > "$BTP_REPO/00_FUENTE-DE-VERDAD/Gestion/HOY.md"\nexit 0\n')
        with open(ra, "w") as f:
            f.write(cuerpo)
        os.chmod(ra, 0o755)
        return os.path.join(ROOT, "tools", "hoy_compose.sh")

    def test_sin_parte_escrito_sale_en_rojo(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._repo_falso(tmp, escribe=False)
            r = subprocess.run(["bash", script], capture_output=True, text=True,
                               env=dict(os.environ, BTP_REPO=tmp), timeout=60)
            self.assertNotEqual(r.returncode, 0, "un parte sin escribir no puede salir en verde")
            self.assertIn("Llavero", r.stderr)

    def test_con_parte_escrito_sale_en_verde(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._repo_falso(tmp, escribe=True)
            r = subprocess.run(["bash", script], capture_output=True, text=True,
                               env=dict(os.environ, BTP_REPO=tmp), timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])


    def test_la_causa_se_lee_no_se_supone(self):
        """El aviso afirmaba «Llavero bloqueado» como causa típica. Era el saldo. (18-sep-26)"""
        guion = '#!/bin/bash\necho \'{"result":"Credit balance is too low"}\'\nexit 0\n'
        with tempfile.TemporaryDirectory() as tmp:
            script = self._repo_falso(tmp, escribe=False, salida_falsa=guion)
            r = subprocess.run(["bash", script], capture_output=True, text=True,
                               env=dict(os.environ, BTP_REPO=tmp), timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("saldo", r.stderr.lower())
            self.assertNotIn("unlock-keychain", r.stderr, "no puede culpar al Llavero de esto")

    def test_causa_desconocida_no_inventa_ninguna(self):
        guion = '#!/bin/bash\necho "algo raro pasó"\nexit 3\n'
        with tempfile.TemporaryDirectory() as tmp:
            script = self._repo_falso(tmp, escribe=False, salida_falsa=guion)
            r = subprocess.run(["bash", script], capture_output=True, text=True,
                               env=dict(os.environ, BTP_REPO=tmp), timeout=60)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no reconocida", r.stderr)
            self.assertIn("algo raro", r.stderr, "debe citar lo que dijo el agente")

    def test_no_vuelve_el_exec_que_impedia_comprobar(self):
        """Con `exec` no queda nadie que mire el fichero: esa era la puerta del falso verde."""
        with open(os.path.join(ROOT, "tools", "hoy_compose.sh"), encoding="utf-8") as f:
            fuente = f.read()
        self.assertNotIn("exec \"$REPO/tools/run_agent.sh\"", fuente)


if __name__ == "__main__":
    unittest.main(verbosity=0)
