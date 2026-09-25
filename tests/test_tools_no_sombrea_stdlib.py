#!/usr/bin/env python3
"""test_tools_no_sombrea_stdlib.py — ningún fichero de tools/ puede llamarse como la stdlib.

LA HISTORIA (20-sep-2026). El 21-jun nació `tools/queue.py` (la cola del lazo). Como `tools/` va
en `sys.path`, tapaba la `queue` de la stdlib que necesitan urllib3, pydicom y Whisper: la
transcripción de audios se caía y Drive fallaba. El parche de entonces fue defensivo — cuatro
módulos empezaron a BORRAR `tools/` del `sys.path` al importarse.

El 11-jul el fichero se renombró a `tools/cola.py` y la sombra desapareció. Los borrados se
quedaron, y **el remedio sobrevivió a la enfermedad durante más de dos meses haciendo daño**:
borraban también la entrada del llamador, así que cualquier import perezoso posterior de ese
proceso fallaba. Medido: el detector de frescura del healthcheck estuvo ciego 74 días, 1.334
fallos, sin avisar ni una vez.

Este test es el guard en la CAUSA: si alguien vuelve a crear `tools/queue.py`, `tools/email.py`
o `tools/json.py`, se pone rojo aquí — donde se arregla renombrando en dos minutos — en vez de
dos meses después, en un detector mudo.
"""
import os
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(RAIZ, "tools")


def nombres_stdlib():
    """Los nombres de la stdlib, también en Python 3.9.

    `sys.stdlib_module_names` nació en 3.10 y la batería corre con /usr/bin/python3, que aquí es
    3.9.6: la primera versión de este test se puso ROJA solo dentro de test_all.sh. Cuando el
    atributo no está, se derivan del propio directorio de la stdlib, que es igual de fiel."""
    nombres = set(getattr(sys, "stdlib_module_names", ()) or ())
    if nombres:
        return nombres
    import sysconfig
    nombres = set(sys.builtin_module_names)
    raiz = sysconfig.get_paths().get("stdlib") or ""
    for entrada in os.listdir(raiz) if os.path.isdir(raiz) else []:
        if entrada.endswith(".py"):
            nombres.add(entrada[:-3])
        elif os.path.isfile(os.path.join(raiz, entrada, "__init__.py")):
            nombres.add(entrada)
    return nombres


def sombras(carpeta):
    """Módulos de la stdlib tapados por un .py de esa carpeta."""
    stdlib = nombres_stdlib()
    return sorted(f[:-3] for f in os.listdir(carpeta)
                  if f.endswith(".py") and f[:-3] in stdlib)


class TestSinSombras(unittest.TestCase):

    def test_ningun_fichero_de_tools_tapa_la_stdlib(self):
        encontradas = sombras(TOOLS)
        self.assertEqual([], encontradas,
                         "tools/%s.py tapa un módulo de la stdlib: renómbralo (queue→cola, "
                         "email→correo…). Eso rompe deps de terceros que importan ese nombre."
                         % ", tools/".join(encontradas) if encontradas else "")

    def test_el_barrido_caza_una_sombra_de_verdad(self):
        """Sin esto, el test de arriba podría estar pasando por no mirar nada."""
        tmp = tempfile.mkdtemp(prefix="sombra-")
        open(os.path.join(tmp, "cola.py"), "w").close()          # nombre nuestro: no es sombra
        self.assertEqual([], sombras(tmp))
        open(os.path.join(tmp, "queue.py"), "w").close()         # el culpable histórico
        open(os.path.join(tmp, "email.py"), "w").close()
        self.assertEqual(["email", "queue"], sombras(tmp))

    def test_los_modulos_que_se_importan_no_tocan_el_path_del_llamador(self):
        """El daño real: `import drive` dejaba al proceso sin tools/ en el path.

        Se comprueba en un proceso aparte y con el módulo REAL, que es como se rompió: el
        healthcheck importaba correo_smtp y a partir de ahí no podía importar `seguimiento`.
        """
        import subprocess
        for modulo in ("correo_smtp", "drive", "wa_tracker"):
            codigo = (
                "import sys, os\n"
                "T = %r\n"
                "sys.path.insert(0, T)\n"
                "import %s\n"
                "import salida\n"                 # lo que fallaba: un hermano de tools/
                "print('OK')\n" % (TOOLS, modulo))
            p = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                               cwd=RAIZ, timeout=180)
            self.assertIn("OK", p.stdout,
                          "tras importar %s no se puede importar un hermano de tools/:\n%s"
                          % (modulo, (p.stdout + p.stderr)[-400:]))


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=0).result
    if res.wasSuccessful():
        print("✅ TOOLS NO TAPA LA STDLIB (%d casos)" % res.testsRun)
    sys.exit(0 if res.wasSuccessful() else 1)
