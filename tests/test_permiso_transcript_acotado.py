#!/usr/bin/env python3
"""test_permiso_transcript_acotado.py — el permiso solo se comprueba contra el transcript de SU
sesión, en la carpeta donde los escribe Claude Code (13-oct-26).

EL HUECO. `permiso_envio.contexto()` abría la ruta que trajera el permiso, fuera la que fuera.
La prueba de que «lo dijo ella» es una línea humana en el transcript, y los guards solo
protegen de escritura `~/.claude/projects/*.jsonl`. Con un permiso que apuntase a un fichero de
/tmp, esa prueba la escribía cualquiera con un `echo`.

QUÉ SE FIJA. La ruta tiene que llamarse `<sesión>.jsonl`, no ser un enlace, y quedar —ya
resuelta— dentro de la raíz de transcripts. La raíz sale de la base de usuarios del sistema, no
de `HOME` ni de ninguna variable: los tests la cambian con un atributo en proceso.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import permiso_envio as P  # noqa: E402

CLAVE = b"t" * 64
ORDEN = "envíalo a alguien@hospital.example"


class TranscriptAcotado(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="acotado-")
        self.raiz = os.path.join(self.tmp, "projects")
        self.fuera = os.path.join(self.tmp, "fuera")
        os.makedirs(os.path.join(self.raiz, "-proyecto"))
        os.makedirs(self.fuera)
        self._estado = os.environ.get("BTP_STATE_DIR")
        os.environ["BTP_STATE_DIR"] = os.path.join(self.tmp, "state")
        P.CLAVE_TEST = CLAVE
        P.RAIZ_TRANSCRIPTS_TEST = self.raiz
        P.MODO_TEST = "hmac"
        self.sesion = str(uuid.uuid4())

    def tearDown(self):
        P.CLAVE_TEST = None
        P.RAIZ_TRANSCRIPTS_TEST = None
        P.MODO_TEST = None
        if self._estado is None:
            os.environ.pop("BTP_STATE_DIR", None)
        else:
            os.environ["BTP_STATE_DIR"] = self._estado
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _transcript(self, carpeta, nombre=None):
        """Un transcript con SU orden como prompt humano. Devuelve (ruta, prompt_id)."""
        ruta = os.path.join(carpeta, (nombre or self.sesion) + ".jsonl")
        pid = str(uuid.uuid4())
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "promptId": pid, "origin": {"kind": "human"},
                                "isSidechain": False, "sessionId": self.sesion,
                                "message": {"role": "user", "content": ORDEN}}) + "\n")
        return ruta, pid

    def _vale(self, ruta, pid, sesion=None):
        """Emite un permiso FIRMADO que apunta a `ruta` y dice si al usarlo vale."""
        sesion = sesion or self.sesion
        P.emitir(ORDEN, sesion, pid, ruta, CLAVE)
        d, motivo, _ctx = P.validar(CLAVE, sesion)
        return bool(d), motivo

    def test_el_de_su_sesion_en_su_sitio_vale(self):
        ruta, pid = self._transcript(os.path.join(self.raiz, "-proyecto"))
        self.assertEqual(self._vale(ruta, pid), (True, ""))

    def test_fuera_de_la_carpeta_no(self):
        """El ataque: la misma línea humana, bien firmado el puntero, pero en una carpeta
        donde escribe cualquiera."""
        ruta, pid = self._transcript(self.fuera)
        vale, motivo = self._vale(ruta, pid)
        self.assertFalse(vale)
        self.assertIn("no está donde Claude Code", motivo)

    def test_un_enlace_dentro_que_apunta_fuera_no(self):
        real, pid = self._transcript(self.fuera)
        enlace = os.path.join(self.raiz, "-proyecto", self.sesion + ".jsonl")
        os.symlink(real, enlace)
        vale, motivo = self._vale(enlace, pid)
        self.assertFalse(vale)
        self.assertIn("enlace", motivo)

    def test_una_carpeta_enlazada_que_saca_fuera_no(self):
        """El fichero no es un enlace, pero la carpeta por la que se llega sí."""
        real, pid = self._transcript(self.fuera)
        os.symlink(self.fuera, os.path.join(self.raiz, "-enlazada"))
        vale, _ = self._vale(os.path.join(self.raiz, "-enlazada", self.sesion + ".jsonl"), pid)
        self.assertFalse(vale)

    def test_el_transcript_de_otra_sesion_no(self):
        ruta, pid = self._transcript(os.path.join(self.raiz, "-proyecto"), nombre=str(uuid.uuid4()))
        vale, motivo = self._vale(ruta, pid)
        self.assertFalse(vale)
        self.assertIn("no es el de su sesión", motivo)

    def test_un_enlace_con_mi_nombre_al_transcript_de_otra_sesion_no(self):
        real, pid = self._transcript(os.path.join(self.raiz, "-proyecto"), nombre=str(uuid.uuid4()))
        enlace = os.path.join(self.raiz, "-proyecto", self.sesion + ".jsonl")
        os.symlink(real, enlace)
        self.assertFalse(self._vale(enlace, pid)[0])

    def test_el_de_un_subagente_no(self):
        """Los de subagente viven en `<sesión>/subagents/agent-x.jsonl`: otro nombre."""
        sub = os.path.join(self.raiz, "-proyecto", self.sesion, "subagents")
        os.makedirs(sub)
        ruta, pid = self._transcript(sub, nombre="agent-abc")
        self.assertFalse(self._vale(ruta, pid)[0])

    def test_ruta_relativa_o_con_puntos_no(self):
        ruta, pid = self._transcript(self.fuera)
        relativa = os.path.join("..", "fuera", self.sesion + ".jsonl")
        self.assertFalse(self._vale(relativa, pid)[0])
        con_puntos = os.path.join(self.raiz, "-proyecto", "..", "..", "fuera", self.sesion + ".jsonl")
        self.assertFalse(self._vale(con_puntos, pid)[0])

    def test_sesion_con_barras_no(self):
        ruta, pid = self._transcript(os.path.join(self.raiz, "-proyecto"))
        self.assertIsNone(P.ruta_transcript(ruta, "../" + self.sesion)[0])
        self.assertIsNone(P.ruta_transcript(ruta, "")[0])

    def test_la_raiz_no_sale_del_entorno(self):
        """Sin la costura en proceso, ni `HOME` ni ninguna variable mueven la raíz: una tool
        que lanza el agente con el entorno cambiado sigue mirando la carpeta de verdad."""
        import pwd
        P.RAIZ_TRANSCRIPTS_TEST = None
        P.MODO_TEST = None
        de_verdad = os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir,
                                                  ".claude", "projects"))
        viejo = dict(os.environ)
        try:
            os.environ["HOME"] = self.fuera
            os.environ["BTP_TEST_RAIZ_TRANSCRIPTS"] = self.fuera
            os.environ["BTP_TRANSCRIPTS_DIR"] = self.fuera
            self.assertEqual(P.raiz_transcripts(), de_verdad)
            ruta, pid = self._transcript(self.fuera)
            self.assertFalse(self._vale(ruta, pid)[0])
        finally:
            os.environ.clear()
            os.environ.update(viejo)

    def test_el_codigo_de_produccion_no_lee_la_costura_del_lanzador(self):
        """Invariante de estructura: `BTP_TEST_*` solo lo lee tests/_corre_hook.py."""
        for rel in ("tools/permiso_envio.py", "tools/lote_envio.py", "tools/web_publicar.py",
                    "tools/web_novedad.py", "tools/ok_envio.py", ".claude/hooks/salida_guard.py",
                    ".claude/hooks/ok_envio_prompt.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                self.assertNotIn("BTP_TEST_", f.read(), rel)


if __name__ == "__main__":
    unittest.main(verbosity=1)
