#!/usr/bin/env python3
"""test_tests_afectados.py — `test_all.sh --cambiados` elige las baterías que toca y no menos.

POR QUÉ EXISTE (26-sep-26). La suite completa tarda más de 10 minutos y varias sesiones la
lanzaban a la vez para comprobaciones intermedias. `tools/tests_afectados.py` elige solo las
baterías afectadas. Si se equivoca por DEFECTO, un fallo pasa sin verse hasta la suite completa;
por eso lo que más se fija aquí es que no se quede corto donde más duele (el muro).

Se fija:
  · tocar un hook del muro → entran todas las baterías del muro, aunque el grafo no las vea;
  · tocar `.claude/settings.json` → igual;
  · tocar `tests/test_all.sh` → TODO;
  · tocar un test → entra ese test;
  · tocar una tool → entran los tests que dependen de ella según el grafo;
  · tocar solo `.md` → ninguna batería;
  · una batería que test_all.sh no conoce no se cuela.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
# BTP_TESTS_AFECTADOS: la campaña de mutantes (tests/mutantes/tests_afectados.json) apunta aquí a una
# copia MUTADA de tools/tests_afectados.py; sin la variable, la de verdad.
if os.environ.get("BTP_TESTS_AFECTADOS"):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("tests_afectados", os.environ["BTP_TESTS_AFECTADOS"])
    T = importlib.util.module_from_spec(_spec)
    sys.modules["tests_afectados"] = T
    _spec.loader.exec_module(T)
else:
    import tests_afectados as T  # noqa: E402

DISPONIBLES = {"test_muro_fase0.py", "test_salida_guard.py", "test_ok_envio_blindado.py",
               "test_fuga.sh", "test_healthcheck_cpu.py", "test_bucles_colgados.py",
               "test_web_lint.py"}


class GrafoFalso:
    def __init__(self, deps):
        self.deps = deps

    def buscar(self, f):
        return f

    def quien(self, dst, hondo=False):
        return {d: {"import"} for d in self.deps.get(dst, [])}


class Seleccion(unittest.TestCase):

    def test_hook_del_muro_arrastra_todo_el_muro(self):
        todo, sel = T.afectados([".claude/hooks/salida_guard.py"], DISPONIBLES, GrafoFalso({}))
        self.assertFalse(todo)
        for b in ("test_muro_fase0.py", "test_salida_guard.py", "test_ok_envio_blindado.py",
                  "test_fuga.sh"):
            self.assertIn(b, sel)
        self.assertNotIn("test_web_lint.py", sel)

    def test_settings_tambien_es_muro(self):
        _todo, sel = T.afectados([".claude/settings.json"], DISPONIBLES, GrafoFalso({}))
        self.assertIn("test_muro_fase0.py", sel)

    def test_tocar_test_all_es_todo(self):
        todo, sel = T.afectados(["tests/test_all.sh"], DISPONIBLES, GrafoFalso({}))
        self.assertTrue(todo)
        self.assertEqual(sel, DISPONIBLES)

    def test_tocar_un_test_lo_incluye(self):
        _todo, sel = T.afectados(["tests/test_healthcheck_cpu.py"], DISPONIBLES, GrafoFalso({}))
        self.assertEqual(sel, {"test_healthcheck_cpu.py"})

    def test_tocar_una_tool_trae_sus_dependientes(self):
        g = GrafoFalso({"tools/bucles_colgados.py": ["tests/test_bucles_colgados.py",
                                                      "tools/healthcheck.py"]})
        _todo, sel = T.afectados(["tools/bucles_colgados.py"], DISPONIBLES, g)
        self.assertEqual(sel, {"test_bucles_colgados.py"})

    def test_lo_que_el_grafo_no_ve_entra_por_nombre(self):
        """Un .mjs no está en el grafo: entra el test que lo nombra (el repo real)."""
        _todo, sel = T.afectados(["tools/_chrome_headless.mjs"], T.baterias(), GrafoFalso({}))
        self.assertIn("test_chrome_headless_cierra.py", sel)

    def test_solo_documentacion_no_corre_nada(self):
        todo, sel = T.afectados(["README.md", "00_FUENTE-DE-VERDAD/x.md"], DISPONIBLES,
                                GrafoFalso({}))
        self.assertFalse(todo)
        self.assertEqual(sel, set())

    def test_bateria_desconocida_no_se_cuela(self):
        _todo, sel = T.afectados(["tests/test_que_no_esta_en_test_all.py"], DISPONIBLES,
                                 GrafoFalso({}))
        self.assertEqual(sel, set())

    def test_con_el_repo_real_no_revienta(self):
        self.assertTrue(T.baterias(), "no encontró baterías en test_all.sh")
        T.cambiados()


class PuertaDeFusion(unittest.TestCase):
    """10-oct-26 · la puerta por impacto: lo que puede romper el muro SIN que un grafo lo vea exige
    la suite COMPLETA; el resto, afectadas + núcleo fijo. Aquí se fija que tocar el muro sigue
    disparando la suite entera, con los ficheros REALES del repo (no solo con ejemplos)."""

    REALES = T.baterias()

    def test_cualquier_hook_real_exige_la_completa(self):
        carpeta = os.path.join(ROOT, ".claude", "hooks")
        hooks = sorted(os.listdir(carpeta))
        self.assertGreater(len(hooks), 20, "no vio los hooks: el test pasaría en vacío")
        for h in hooks:
            r = T.puerta([".claude/hooks/" + h], self.REALES, GrafoFalso({}))
            self.assertEqual(r["veredicto"], "COMPLETA", h)
            self.assertEqual(r["seleccion"], self.REALES, h)

    def test_cada_tool_que_un_hook_usa_exige_la_completa(self):
        muro = T.tools_de_hooks()
        self.assertGreater(len(muro), 20, "tools_de_hooks() salió casi vacío: pasaría en vacío")
        for need in ("tools/salida.py", "tools/_secrets.py", "tools/lector_clinico.py",
                     "tools/permiso_envio.py"):
            self.assertIn(need, muro, "ya no se ve que un hook use %s" % need)
        for t in sorted(muro):
            self.assertEqual(T.puerta([t], self.REALES, GrafoFalso({}))["veredicto"], "COMPLETA", t)

    def test_settings_runner_y_ayudantes_exigen_la_completa(self):
        for f in (".claude/settings.json", ".claude/settings.local.json", "tests/test_all.sh",
                  "tests/_entorno.py", "tools/tests_afectados.py", "tools/normas.json",
                  "tools/config/politica_aprobacion.json"):
            self.assertEqual(T.puerta([f], self.REALES, GrafoFalso({}))["veredicto"], "COMPLETA", f)

    def test_un_cambio_ajeno_al_muro_es_rapida_y_lleva_el_nucleo(self):
        r = T.puerta(["tools/frescura_dosier.py", "README.md"], self.REALES, GrafoFalso({}),
                     muro_tools=set())
        self.assertEqual(r["veredicto"], "RAPIDA")
        self.assertTrue(T.nucleo_muro(self.REALES) <= r["seleccion"])
        self.assertIn("test_fuga.sh", r["seleccion"])
        self.assertNotEqual(r["seleccion"], self.REALES, "la rápida no puede ser la completa")

    def test_solo_documentacion_es_rapida_con_solo_el_nucleo(self):
        r = T.puerta(["README.md"], self.REALES, GrafoFalso({}), muro_tools=set())
        self.assertEqual(r["veredicto"], "RAPIDA")
        self.assertEqual(r["seleccion"], T.nucleo_muro(self.REALES))

    def test_constitucion_reglas_y_agentes_exigen_la_completa(self):
        """Hueco hallado al medir (10-oct): un .md no tiene dependientes en el grafo, así que tocar
        CLAUDE.md o una regla caía en RAPIDA con solo el núcleo y no corría test_constitucion_*."""
        for f in ("CLAUDE.md", ".claude/rules/clinico.md", ".claude/agents/tecnico.md",
                  ".claude/skills/a11y/SKILL.md"):
            self.assertEqual(T.puerta([f], self.REALES, GrafoFalso({}))["veredicto"], "COMPLETA", f)
        self.assertEqual(T.puerta(["README.md"], self.REALES, GrafoFalso({}),
                                  muro_tools=set())["veredicto"], "RAPIDA")

    def test_mezcla_basta_un_fichero_del_muro(self):
        r = T.puerta(["README.md", "tools/frescura_dosier.py", ".claude/hooks/muro_guard.py"],
                     self.REALES, GrafoFalso({}))
        self.assertEqual(r["veredicto"], "COMPLETA")

    def test_el_cli_dice_la_primera_linea(self):
        import subprocess
        out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "tests_afectados.py"), "--puerta"],
                             capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL).stdout
        self.assertIn(out.splitlines()[0], ("COMPLETA", "RAPIDA"))


class PuertaFallaCerrada(unittest.TestCase):
    """10-oct-26 · revisión de consejero-arquitectura: la puerta es una LISTA BLANCA. Todo lo que no
    esté explícitamente en seguros exige la completa; un fallo del propio selector también."""

    REALES = T.baterias()
    SEGUROS_BASE = {"README.md", "tools/frescura_dosier.py"}

    def _p(self, ficheros, disponibles=None, **kw):
        kw.setdefault("rastreados", set(T._git("ls-tree", "-r", "--name-only", "HEAD").splitlines()))
        return T.puerta(ficheros, disponibles or self.REALES, GrafoFalso({}), **kw)["veredicto"]

    def test_base_inexistente_no_es_nada_que_correr(self):
        with self.assertRaises(RuntimeError):
            T.cambiados("rama-que-no-existe-jamas")

    def test_cli_con_base_inexistente_dice_completa(self):
        import subprocess
        for flag, esperado in (("--puerta", "COMPLETA"), ("--cambiados", "TODO")):
            out = subprocess.run([sys.executable, T.__file__, flag,   # el módulo BAJO PRUEBA (el mutado, en la campaña)
                                  "--base", "rama-que-no-existe-jamas"], capture_output=True, text=True,
                                 timeout=120, stdin=subprocess.DEVNULL)
            self.assertEqual(out.returncode, 0)
            self.assertEqual(out.stdout.splitlines()[0], esperado, flag)

    def test_lo_no_clasificado_exige_la_completa(self):
        for f in (".mcp.json", "tools/local.py", "tests/test_fuga.sh", "tests/test_muro_hook.py",
                  "tools/launchd/com.btp.healthcheck.plist", ".github/workflows/contribuciones.yml",
                  "tools/tool_que_no_existe_todavia.py", "tests/test_nuevo_sin_registrar.py",
                  "tools/fichas/tests_afectados.py.json", "package.json", "tools/algo.sh"):
            self.assertEqual(self._p([f]), "COMPLETA", f)

    def test_fichero_borrado_exige_la_completa(self):
        # aun si git lo diera por rastreado: si no está en disco se borró o renombró
        self.assertEqual(self._p(["tools/ya_no_existe_jamas.py"], rastreados={"tools/ya_no_existe_jamas.py"},
                                 muro_tools=set()), "COMPLETA")

    def test_tool_nueva_sin_clasificar_exige_la_completa(self):
        # existe en disco pero NO estaba en la base: nadie la ha clasificado
        self.assertEqual(self._p(["tools/frescura_dosier.py"], rastreados=set(), muro_tools=set()), "COMPLETA")

    def test_test_sin_registrar_exige_la_completa(self):
        # existe, no es del muro, pero test_all.sh no lo lista: nadie lo corre
        sin = set(self.REALES) - {"test_web_lint.py"}
        self.assertEqual(self._p(["tests/test_web_lint.py"], disponibles=sin, muro_tools=set()), "COMPLETA")
        self.assertEqual(self._p(["tests/test_web_lint.py"], muro_tools=set()), "RAPIDA")

    def test_tools_por_rol_son_muro_aunque_ningun_hook_las_use(self):
        for t in ("tools/local.py", "tools/nube_n1.py", "tools/identidad_paciente.py"):
            self.assertEqual(self._p([t], muro_tools=set(), con_red=set()), "COMPLETA", t)   # solo la lista por rol decide: sin cierre ni red

    def test_los_seguros_siguen_siendo_rapidos(self):
        # el reverso: la lista blanca no puede ser tan estrecha que nada pase
        self.assertEqual(self._p(["README.md"], muro_tools=set()), "RAPIDA")
        self.assertEqual(self._p(["tools/frescura_dosier.py"], muro_tools=set()), "RAPIDA")
        self.assertEqual(self._p(["tests/test_web_lint.py"], muro_tools=set()), "RAPIDA")

    def test_una_tool_a_dos_saltos_de_un_hook_es_muro(self):
        import tempfile
        raiz = tempfile.mkdtemp(prefix="puerta_saltos_")
        for d in (".claude/hooks", "tools"):
            os.makedirs(os.path.join(raiz, d))
        open(os.path.join(raiz, ".claude/hooks/h.py"), "w").write("import sys\nsys.path.insert(0, 'tools')\nimport uno\n")
        open(os.path.join(raiz, "tools/uno.py"), "w").write("def f():\n    import dos   # perezoso\n")
        open(os.path.join(raiz, "tools/dos.py"), "w").write("import tres\n")
        open(os.path.join(raiz, "tools/tres.py"), "w").write("x = 1\n")
        open(os.path.join(raiz, "tools/ajena.py"), "w").write("y = 2\n")
        self.assertEqual(T.tools_de_hooks(raiz, transitivo=False), {"tools/uno.py"})
        self.assertEqual(T.tools_de_hooks(raiz), {"tools/uno.py", "tools/dos.py", "tools/tres.py"})
        self.assertNotIn("tools/ajena.py", T.tools_de_hooks(raiz))

    def test_tools_reales_alcanzables_desde_un_hook_exigen_la_completa(self):
        directas = T.tools_de_hooks(transitivo=False)
        cierre = T.tools_de_hooks()
        self.assertTrue(directas < cierre, "el cierre no añade nada: pasaría en vacío")
        for t in ("tools/identidad_paciente.py", "tools/puerta_n1.py", "tools/laminillas_jaulas.py"):
            self.assertEqual(self._p([t]), "COMPLETA", t)


class ToolsConRed(unittest.TestCase):
    """10-oct-26 · el cierre del muro mide «lo alcanza un hook», no «puede sacar datos». Una tool que habla con la red
    (directamente o porque importa otra que lo hace) exige la COMPLETA. Y un lint INDEPENDIENTE (regex, no el ast de
    tests_afectados) comprueba cada tools/*.py: si parece usar la red y la puerta la deja por la vía rápida, rojo."""

    REALES = T.baterias()
    RASTREADOS = set(T._git("ls-tree", "-r", "--name-only", "HEAD").splitlines())
    RED = re.compile(
        r"^\s*(?:import|from)\s+(?:requests|httpx|aiohttp|smtplib|imaplib|poplib|ftplib|telnetlib|socket|ssl|paramiko|"
        r"googleapiclient|google\.|urllib\.request|http\.client|http\.server)"
        r"|^\s*from\s+urllib\s+import\s+[^\n]*request"
        r"|[\"'](?:curl|wget|ssh|scp|rsync|xurl)[\"' ]", re.M)

    def _p(self, f):
        return T.puerta([f], self.REALES, GrafoFalso({}), rastreados=self.RASTREADOS)["veredicto"]

    def test_las_cuatro_de_la_revision_van_a_completa(self):
        for f in ("tools/subir_historial_drive.py", "tools/correo_responder.py", "tools/calendar_write.py",
                  "tools/adjuntos_clinicos.py"):
            self.assertEqual(self._p(f), "COMPLETA", f)

    def test_correo_responder_no_importa_red_pero_la_alcanza(self):
        """El caso que un lint de imports directos no ve: llega a SMTP/IMAP por lo que importa."""
        directa = T._usa_red_directa(os.path.join(ROOT, "tools", "correo_responder.py"))
        self.assertFalse(directa, "si ahora usa la red directamente, este test ya no prueba la vía transitiva")
        self.assertIn("tools/correo_responder.py", T.tools_con_red())

    def test_lint_toda_tool_con_red_va_a_completa(self):
        vistas, malas = 0, []
        for f in sorted(os.listdir(os.path.join(ROOT, "tools"))):
            if not f.endswith(".py"):
                continue
            texto = open(os.path.join(ROOT, "tools", f), encoding="utf-8", errors="replace").read()
            if self.RED.search(texto):
                vistas += 1
                if self._p("tools/" + f) != "COMPLETA":
                    malas.append(f)
        self.assertGreater(vistas, 40, "el lint no vio tools con red: pasaría en vacío")
        self.assertEqual(malas, [], "tools que parecen usar la red y van por la vía rápida")

    def test_una_tool_sin_red_sigue_siendo_rapida(self):
        self.assertEqual(self._p("tools/frescura_dosier.py"), "RAPIDA")

    def test_instalar_el_muro_es_del_muro_por_rol(self):
        for f in ("tools/instala_muro_usuario.py", "tools/rebuild_agents.py"):
            self.assertEqual(self._p(f), "COMPLETA", f)

    def test_cada_marca_de_red_se_detecta_por_separado(self):
        """Cada módulo y cada programa de la lista, UNO A UNO en una tool de juguete: si se quita uno de la lista, rojo
        (en el repo real otras marcas de la misma tool lo taparían)."""
        import tempfile
        d = tempfile.mkdtemp(prefix="marcas_red_")
        os.makedirs(os.path.join(d, "tools"))
        esperadas = set()
        for i, m in enumerate(sorted(T._MODULOS_RED_RAIZ | T._MODULOS_RED_EXACTOS)):
            n = "m%d" % i
            open(os.path.join(d, "tools", n + ".py"), "w").write("import %s\n" % m)
            esperadas.add("tools/%s.py" % n)
        for j, c in enumerate(sorted(T._CLI_RED)):
            n = "c%d" % j
            open(os.path.join(d, "tools", n + ".py"), "w").write("import subprocess\nsubprocess.run(['%s', 'x'])\n" % c)
            esperadas.add("tools/%s.py" % n)
        self.assertGreater(len(esperadas), 25)
        self.assertEqual(esperadas - T.tools_con_red(d), set(), "marcas de la lista que no se detectan")
        for lit in ("imaplib", "googleapiclient", "smtplib", "urllib.request", "http.client", "socket", "curl", "ssh"):
            self.assertTrue(lit in T._MODULOS_RED_RAIZ | T._MODULOS_RED_EXACTOS | T._CLI_RED, lit)

    def test_urllib_parse_no_cuenta_como_red(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="sinred_")
        os.makedirs(os.path.join(d, "tools"))
        open(os.path.join(d, "tools", "a.py"), "w").write("import urllib.parse\nimport json\n")
        open(os.path.join(d, "tools", "b.py"), "w").write("from urllib import request\n")
        open(os.path.join(d, "tools", "c.py"), "w").write("import b\n")
        open(os.path.join(d, "tools", "d.py"), "w").write("import subprocess\nsubprocess.run(['curl', 'x'])\n")
        self.assertEqual(T.tools_con_red(d), {"tools/b.py", "tools/c.py", "tools/d.py"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
