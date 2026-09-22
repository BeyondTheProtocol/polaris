#!/usr/bin/env python3
"""Tests de anatomia_push.py — lo que sube al panel, y cómo sube.

Lo que se protege:
  · que el fichero que se publica NO deje nada legible (es la única barrera si
    alguien llega al repo)
  · que el ida y vuelta cuadre siempre y que una clave distinta NO abra nada
  · que la clave no acabe impresa por ningún sitio (se quemó así una vez)
"""

import base64
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import anatomia_push as ap  # noqa: E402

# Frases, no claves: desde el 25-jul-26 la clave se DERIVA con PBKDF2 y no
# existe guardada en ninguna parte (ni en el servidor del panel).
CLAVE = "frase-de-prueba-una-larga"
OTRA = "frase-de-prueba-otra-bien-distinta"


class TestCifrado(unittest.TestCase):

    def test_ida_y_vuelta(self):
        html = "<p>hola, soy el mapa</p>"
        self.assertEqual(ap.descifrar(ap.cifrar(html, CLAVE), CLAVE), html)

    def test_con_otra_clave_no_abre(self):
        """Que falle del todo, no que devuelva basura a medias."""
        sellado = ap.cifrar("<p>secreto</p>", CLAVE)
        with self.assertRaises(Exception):
            ap.descifrar(sellado, OTRA)

    def test_si_lo_tocan_no_abre(self):
        """AES-GCM lleva sello: un byte cambiado invalida el fichero entero."""
        sellado = ap.cifrar("<p>secreto</p>" * 40, CLAVE)
        crudo = bytearray(base64.b64decode(sellado))
        crudo[40] ^= 0x01
        with self.assertRaises(Exception):
            ap.descifrar(base64.b64encode(bytes(crudo)).decode(), CLAVE)

    def test_dos_veces_no_dan_lo_mismo(self):
        """Nonce nuevo cada vez: si no, se filtra que el contenido no cambió."""
        html = "<p>igual</p>"
        self.assertNotEqual(ap.cifrar(html, CLAVE), ap.cifrar(html, CLAVE))

    def test_lleva_su_propia_sal(self):
        """La sal va DENTRO del fichero: el navegador la necesita para derivar la
        misma clave, y ser distinta cada vez impide precalcular contra ella."""
        a = base64.b64decode(ap.cifrar("<p>x</p>", CLAVE))[:16]
        b = base64.b64decode(ap.cifrar("<p>x</p>", CLAVE))[:16]
        self.assertEqual(len(a), 16)
        self.assertNotEqual(a, b)

    def test_es_texto_plano_para_git(self):
        sellado = ap.cifrar("<p>x</p>", CLAVE)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9+/=]+", sellado))


class TestNoSeEscapaNada(unittest.TestCase):

    def test_el_fichero_publicado_no_lleva_nada_legible(self):
        """La prueba de fuego: se cifra el mapa REAL y se busca dentro cada
        nombre de comité. Si aparece uno, el repo privado sería lo único que
        protege, y el plan dice que hacen falta las tres capas.

        Se busca en los BYTES del cifrado, no en su texto base64, y se ignoran
        los nombres de menos de 4 letras. Los dos matices son la razón de que
        este test fuera FLAKY hasta el 31-jul-26 y saliera rojo ~1 de cada 10
        baterías sin que nadie lo reprodujera a mano:

          · El base64 usa un alfabeto de 64 símbolos. En un cifrado de ~15.000
            caracteres, la probabilidad de que aparezca POR AZAR una secuencia
            de 3 letras es ~15.000/64³ ≈ 6 %. Y como cada cifrado lleva sal
            nueva, salía distinto cada vez: unas veces pasaba y otras no.
            Medido: 3 de 30 cifrados contenían «git».
          · En bytes crudos el alfabeto es 256, así que el mismo azar cae a
            ~15.000/256⁴ ≈ 0,0000035 para 4 letras. Despreciable.

        `git` es el único comité que se queda fuera, y no se pierde nada: que
        aparezcan tres letras sueltas en un cifrado no es una filtración. Un
        test que va y viene es peor que uno rojo, porque enseña a ignorarlo.
        """
        import anatomia
        html = anatomia.render(cara="privada")
        sellado = ap.cifrar(html, CLAVE)
        crudo = base64.b64decode(sellado + "=" * (-len(sellado) % 4))
        for c in anatomia.comites():
            nombre = c["nombre"]
            if len(nombre) < 4:
                continue
            self.assertNotIn(nombre.encode("utf-8"), crudo)
        for marca in ("La Anatomía", "<svg", "comités", "healthcheck", "<!doctype"):
            self.assertNotIn(marca.encode("utf-8"), crudo)

    def test_comprime_de_verdad(self):
        """Se sube cada pocos minutos: si no comprimiera, el repo engordaría."""
        import anatomia
        html = anatomia.render(cara="privada")
        self.assertLess(len(ap.cifrar(html, CLAVE)), len(html))


class TestLosDosLadosHablanIgual(unittest.TestCase):
    """El riesgo real de este montaje: que Python cifre de una manera y Node
    descifre de otra. Aquí se cifra en Python y se descifra en Node de verdad."""

    def test_python_cifra_node_descifra(self):
        import json
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("sin node en esta máquina")
        frase = "una-frase-cualquiera-para-el-test"
        html = "<h1>La Anatomía</h1><p>acentos ñ, € y comillas «así»</p>"
        entrada = json.dumps({"frase": frase, "enc": ap.cifrar(html, frase)})
        guion = """
import crypto from "node:crypto"; import zlib from "node:zlib";
let s=""; process.stdin.on("data",c=>s+=c).on("end",()=>{
  const d=JSON.parse(s), b=Buffer.from(d.enc,"base64");
  const k=crypto.pbkdf2Sync(d.frase,b.subarray(0,16),600000,32,"sha256");
  const x=crypto.createDecipheriv("aes-256-gcm",k,b.subarray(16,28));
  x.setAuthTag(b.subarray(b.length-16));
  const g=Buffer.concat([x.update(b.subarray(28,b.length-16)),x.final()]);
  process.stdout.write(zlib.gunzipSync(g).toString("utf-8"));});
"""
        r = subprocess.run([node, "--input-type=module", "-e", guion],
                           input=entrada, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, html)


class TestSecreto(unittest.TestCase):

    def test_la_clave_nunca_sale_por_stdout(self):
        """Se imprimió una vez y quedó escrita en la transcripción del chat, que
        se guarda: un secreto impreso está quemado. Aquí se comprueba el
        comportamiento real, no el código: se llama a la copia de la clave y se
        mira que el valor NO aparezca en lo que escribe por pantalla."""
        import contextlib
        import io
        # `gitleaks:allow` NO es decoración. Sin él, esta línea de mentira paró
        # el sistema entero: la regla `generic-api-key` la leyó como un secreto
        # de verdad, el barrido de seguridad se puso en ROJO y disparó un CÓDIGO
        # ROJO que dejó el lazo con HALT desde el 26-jul 06:00 hasta el 27 por
        # la tarde. El valor va además sin pinta de clave (sin `=` final) para
        # no volver a rozar la regla.
        secreta = "valor-de-mentira-que-no-debe-salir"  # gitleaks:allow
        copiado = {}

        original_pb = ap._al_portapapeles
        import _secrets
        original_get = _secrets.get
        try:
            _secrets.get = lambda *a, **k: secreta
            ap._al_portapapeles = lambda v: copiado.setdefault("v", v) or True
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                ap._copiar_frase()
        finally:
            ap._al_portapapeles = original_pb
            _secrets.get = original_get

        self.assertEqual(copiado.get("v"), secreta, "no llegó al portapapeles")
        self.assertNotIn(secreta, salida.getvalue(),
                         "la clave se imprimió por pantalla")

    def test_sin_frase_no_cifra(self):
        for vacia in ("", None):
            with self.assertRaises(Exception):
                ap.cifrar("<p>x</p>", vacia)


class TestIdentidadGitNoDependeDeRed(unittest.TestCase):
    """12-sep-2026, daemon_fallando:com.btp.anatomia-push (8x): commit --amend fallaba con
    'fatal: no es posible auto-detectar la dirección de correo (se obtuvo
    polaris@Polaris.(none))' cuando ni user.name/user.email (local o global) estaban puestos
    y el hostname no se podía resolver (Tailscale/mDNS caído, mismo fallo de red que ese día
    tumbaba enviar-hoy y calendar-sync). El commit del snapshot NO puede depender de resolver
    nada por red: necesita una identidad LOCAL fija en el propio clon de datos."""

    def _repo_vacio(self, tmp):
        import subprocess
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        return repo

    def test_preparar_clon_fija_identidad_local(self):
        import tempfile
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_vacio(tmp)
            clon_previo = ap.CLON
            try:
                ap.CLON = repo
                ap._asegurar_identidad_git()
                nombre = subprocess.run(["git", "config", "--local", "user.name"], cwd=repo,
                                        capture_output=True, text=True).stdout.strip()
                correo = subprocess.run(["git", "config", "--local", "user.email"], cwd=repo,
                                        capture_output=True, text=True).stdout.strip()
            finally:
                ap.CLON = clon_previo
        self.assertTrue(nombre)
        self.assertTrue(correo)

    def test_commit_sobrevive_sin_identidad_global_ni_hostname(self):
        """Simula el escenario real: sin GIT_AUTHOR_*/GIT_COMMITTER_* en el entorno y sin
        ~/.gitconfig global (HOME vacío) — como si el hostname no se pudiera resolver."""
        import tempfile
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_vacio(tmp)
            with open(os.path.join(repo, "f.txt"), "w", encoding="utf-8") as f:
                f.write("x")
            env_sin_identidad = {k: v for k, v in os.environ.items()
                                  if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
            env_sin_identidad["HOME"] = tmp  # sin .gitconfig global que rescate la identidad
            clon_previo = ap.CLON
            try:
                ap.CLON = repo
                ap._asegurar_identidad_git()
                subprocess.run(["git", "add", "f.txt"], cwd=repo, env=env_sin_identidad,
                               check=True)
                r = subprocess.run(["git", "commit", "-m", "x"], cwd=repo,
                                   env=env_sin_identidad, capture_output=True, text=True)
            finally:
                ap.CLON = clon_previo
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if res.wasSuccessful():
        print("✅ EL EMPUJE DEL PANEL EN VERDE (%d tests)" % res.testsRun)
        sys.exit(0)
    print("❌ EL EMPUJE DEL PANEL EN ROJO: %d fallos, %d errores"
          % (len(res.failures), len(res.errors)))
    sys.exit(1)
