#!/usr/bin/env python3
"""La caja: cosechar lo que dijeron N subagentes y entregarlo como UN dossier.

Lo que estos tests protegen, por orden de importancia:
  1. Que la fuente NO se invente nunca. Una decisión sin fuente trazable tiene que picar
     (rc=1), no colarse con el nombre del agente puesto a dedo.
  2. Que un subagente que no cumple el contrato se DIGA, en vez de desaparecer en silencio.
  3. Que la cosecha lea de disco sin pedirle nada a nadie ni gastar un token.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

TMP = tempfile.mkdtemp(prefix="caja-test-")
os.environ["BTP_PROJECTS_DIR"] = TMP

import caja  # noqa: E402  — después de fijar BTP_PROJECTS_DIR
caja.PROJECTS = TMP


def _subagente(sesion, agid, tipo, descripcion, texto):
    """Escribe en disco un subagente falso con la forma REAL que deja el arnés."""
    d = os.path.join(TMP, "-proyecto", sesion, "subagents")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s.meta.json" % agid), "w", encoding="utf-8") as f:
        json.dump({"agentType": tipo, "description": descripcion,
                   "toolUseId": "toolu_x", "spawnDepth": 1,
                   "requestShape": "foreground"}, f)
    with open(os.path.join(d, "%s.jsonl" % agid), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "irrelevante"}}) + "\n")
        f.write(json.dumps({"message": {"role": "assistant",
                                        "content": [{"type": "text", "text": texto}]}}) + "\n")


CONTRATO = """He mirado lo que pedías.

```json
{"decisiones": [{"titulo": "%s", "impacto": "%s", "recomendacion": "%s",
                 "porque": "%s", "fuente": "%s"}],
 "acciones": [{"texto": "%s", "fuente": "%s"}]}
```"""


class TestCaja(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _subagente("s1", "agent-a", "comite-medico", "busca evidencia",
                   CONTRATO % ("{{DIANA}} está medido y es positivo", "critica", "pedir IHQ de {{DIANA}}",
                               "es el único marcador de superficie positivo", "PET-{{TRAZADOR}} 26-may",
                               "pedir el corte a {{CENTRO}}", "PET-{{TRAZADOR}} 26-may"))
        _subagente("s1", "agent-b", "verificacion", "refuta lo anterior",
                   CONTRATO % ("la serie citada es de tumor NE puro", "alta", "no extrapolar",
                               "la población no coincide", "PMID abierto hoy",
                               "cotejar el resto de citas", "PMID abierto hoy"))
        _subagente("s1", "agent-c", "consejero-acceso", "sin contrato",
                   "Te cuento en prosa lo que encontré, sin bloque json ninguno.")
        # sesión aparte: una decisión SIN fuente, que es lo que tiene que picar
        _subagente("s2", "agent-d", "?", "huérfana",
                   '```json\n{"decisiones": [{"titulo": "algo sin respaldo", "impacto": "critica",'
                   ' "recomendacion": "x", "porque": "y"}]}\n```')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    # ── cosecha ──────────────────────────────────────────────────────────────────────
    def test_cosecha_lee_del_disco_sin_que_nadie_apunte_nada(self):
        c = caja.cosecha("s1")
        self.assertEqual(3, len(c))
        self.assertEqual(["comite-medico", "verificacion", "consejero-acceso"],
                         sorted([x["agente"] for x in c],
                                key=lambda a: ["comite-medico", "verificacion",
                                               "consejero-acceso"].index(a)))

    def test_cosecha_respeta_el_corte_temporal(self):
        futuro = 4102444800.0   # 2100
        self.assertEqual([], caja.cosecha("s1", desde_ts=futuro))

    def test_sesion_inexistente_no_revienta(self):
        self.assertEqual([], caja.cosecha("no-existe-esta-sesion"))

    # ── el contrato ──────────────────────────────────────────────────────────────────
    def test_parsea_el_bloque_del_contrato(self):
        c = [x for x in caja.cosecha("s1") if x["agente"] == "comite-medico"][0]
        d = caja.parsear(c["cuerpo"], c["agente"])
        self.assertFalse(d["degradado"])
        self.assertEqual(1, len(d["decisiones"]))
        self.assertEqual("{{DIANA}} está medido y es positivo", d["decisiones"][0]["titulo"])

    def test_sin_contrato_entra_degradado_y_no_se_inventa_nada(self):
        d = caja.parsear("prosa suelta, cero json", "consejero-acceso")
        self.assertTrue(d["degradado"])
        self.assertEqual([], d["decisiones"])
        self.assertEqual([], d["acciones"])

    def test_gana_el_ultimo_bloque_json(self):
        cuerpo = ('```json\n{"decisiones": [{"titulo": "viejo", "impacto": "media"}]}\n```\n'
                  'luego lo pensé mejor\n'
                  '```json\n{"decisiones": [{"titulo": "nuevo", "impacto": "media"}]}\n```')
        d = caja.parsear(cuerpo, "x")
        self.assertEqual("nuevo", d["decisiones"][0]["titulo"])

    def test_json_roto_no_revienta_y_cae_a_degradado(self):
        d = caja.parsear('```json\n{"decisiones": [ ROTO\n```', "x")
        self.assertTrue(d["degradado"])

    # ── dossier ──────────────────────────────────────────────────────────────────────
    def test_de_punta_a_punta_sale_UN_dossier_con_las_fuentes(self):
        md, rc = caja.dossier(caja.cosecha("s1"), "decidir la siguiente prueba")
        self.assertEqual(0, rc)
        self.assertIn("# Dossier: decidir la siguiente prueba", md)
        self.assertIn("{{DIANA}} está medido y es positivo", md)
        self.assertIn("la serie citada es de tumor NE puro", md)
        self.assertIn("PET-{{TRAZADOR}} 26-may", md)          # la fuente viaja hasta el dossier
        self.assertIn("pedir el corte a {{CENTRO}}", md)  # y las acciones también

    def test_el_que_no_cumplio_el_contrato_se_dice_en_voz_alta(self):
        md, _ = caja.dossier(caja.cosecha("s1"))
        self.assertIn("sin bloque json del contrato", md)
        self.assertIn("consejero-acceso", md)

    def test_una_decision_sin_fuente_pica(self):
        """El corazón del asunto: sin fuente trazable, rc=1 y aviso. Nunca se rellena."""
        md, rc = caja.dossier(caja.cosecha("s2"))
        self.assertEqual(1, rc)
        self.assertIn("SIN fuente trazable", md)

    def test_el_tope_de_cabeza_no_oculta_nada(self):
        muchas = [{"agente": "x", "degradado": False, "acciones": [],
                   "decisiones": [{"titulo": "d%d" % i, "impacto": "media",
                                   "recomendacion": "r", "fuente": "f"} for i in range(9)]}]
        partes = __import__("paso_consolidacion").consolidar(
            {"intencion": "t", "contribuciones": muchas})
        cabeza, diferidas = partes[1], partes[2]
        self.assertEqual(6, len(cabeza))
        self.assertEqual(3, len(diferidas))   # las de más se ven, no se tiran

    def test_no_escribe_absolutamente_nada_en_agents(self):
        """La caja consolida; no fabrica agentes. Si un día lo hace, que sea una decisión."""
        with open(os.path.join(RAIZ, "tools", "caja.py"), encoding="utf-8") as f:
            fuente = f.read()
        self.assertNotIn(".claude/agents", fuente)
        self.assertNotIn("agents_dir", fuente.lower())


class TestContratoEnLaOrden(unittest.TestCase):
    """El contrato se pide en la ORDEN, no en la ficha de cada agente.

    Así vale igual para los 33 agentes de disco y para un experto compuesto al vuelo, y
    cambiarlo no obliga a tocar 33 ficheros.
    """

    @classmethod
    def setUpClass(cls):
        import decide_peticion
        cls.dp = decide_peticion

    def test_con_dos_asientos_se_pide_el_contrato_y_el_dossier(self):
        d = self.dp.decidir("analiza a fondo el informe del panel molecular de hueso")
        o = self.dp.orden(d)
        self.assertGreaterEqual(len(d["comites"]), 2)
        self.assertIn("```json", o)
        self.assertIn("caja.py dossier", o)

    def test_con_un_solo_asiento_no_se_pide_nada(self):
        """Con una sola voz no hay nada que consolidar: pedirlo sería burocracia."""
        d = self.dp.decidir("revisa el copy de la landing")
        o = self.dp.orden(d)
        self.assertEqual(1, len(d["comites"]))
        self.assertNotIn("```json", o)
        self.assertNotIn("caja.py dossier", o)

    def test_el_contrato_nombra_la_fuente_como_obligatoria(self):
        d = self.dp.decidir("analiza a fondo el informe del panel molecular de hueso")
        o = self.dp.orden(d)
        self.assertIn("fuente", o)
        self.assertIn("PMID", o)      # se le dice qué cuenta como fuente, no solo que la ponga

    def test_lo_que_pide_el_contrato_es_lo_que_la_caja_sabe_parsear(self):
        """Si el contrato y el parser se separan, el dossier sale vacío y nadie se entera."""
        d = self.dp.decidir("analiza a fondo el informe del panel molecular de hueso")
        o = self.dp.orden(d)
        for clave in ("decisiones", "acciones", "impacto", "recomendacion", "titulo"):
            self.assertIn(clave, o, "el contrato no menciona %r" % clave)
        ejemplo = ('```json\n{"decisiones":[{"titulo":"t","impacto":"alta","recomendacion":"r",'
                   '"porque":"p","fuente":"f"}],"acciones":[{"texto":"x","fuente":"f"}]}\n```')
        parseado = caja.parsear(ejemplo, "comite-medico")
        self.assertFalse(parseado["degradado"])
        self.assertEqual(1, len(parseado["decisiones"]))
        self.assertEqual(1, len(parseado["acciones"]))


class TestGoalBajaDeLaCaja(unittest.TestCase):
    """El `## Goal` del charter gobierna el dossier — y si no se puede leer, PICA.

    Lo que protege, por orden:
      1. Que el goal escrito en `CAJA.md` llegue al dossier sin que nadie lo teclee.
      2. Que pedir una caja ilegible/inexistente/sin rellenar sea ERROR (rc=2), nunca
         un dossier degradado con "(sin intención declarada)" — ese era el único punto
         del flujo que se caía en silencio.
      3. Que el slug no pueda salirse de la Constelación (anti path-traversal, como A9).
    """

    @classmethod
    def setUpClass(cls):
        cls.raiz = tempfile.mkdtemp(prefix="caja-charter-")
        cls.constelacion = os.path.join(cls.raiz, "00_FUENTE-DE-VERDAD",
                                        "04 · IA", "Constelacion")
        os.makedirs(cls.constelacion)
        cls._root_previo = caja.ROOT
        caja.ROOT = cls.raiz

    @classmethod
    def tearDownClass(cls):
        caja.ROOT = cls._root_previo
        shutil.rmtree(cls.raiz, ignore_errors=True)

    def _caja(self, slug, cuerpo):
        d = os.path.join(self.constelacion, slug)
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "CAJA.md"), "w", encoding="utf-8") as f:
            f.write(cuerpo)
        return d

    # ── el camino feliz ──────────────────────────────────────────────────────────
    def test_goal_llega_al_dossier(self):
        self._caja("caja-buena", "---\ncaja: caja-buena\n---\n# X\n\n"
                                 "## Goal\nCerrar el hueco de la visita.\n\n"
                                 "## Resultado\nlo que sea\n")
        self.assertEqual("Cerrar el hueco de la visita.", caja.goal_de_caja("caja-buena"))

    def test_el_blockquote_de_la_plantilla_no_es_el_goal(self):
        self._caja("con-cita", "---\n---\n## Goal\n> PLANTILLA. no me leas\n"
                               "El goal de verdad.\n\n## Otra\n")
        self.assertEqual("El goal de verdad.", caja.goal_de_caja("con-cita"))

    def test_goal_multilinea_se_junta(self):
        self._caja("multi", "---\n---\n## Goal\nUna línea.\nY otra.\n\n## Fin\n")
        self.assertEqual("Una línea. Y otra.", caja.goal_de_caja("multi"))

    # ── fail-closed: cada escalón pica ───────────────────────────────────────────
    def test_caja_inexistente_pica(self):
        with self.assertRaises(caja.CajaError):
            caja.goal_de_caja("no-existe")

    def test_sin_seccion_goal_pica(self):
        self._caja("sin-goal", "---\n---\n# X\n\n## Resultado\nnada\n")
        with self.assertRaises(caja.CajaError):
            caja.goal_de_caja("sin-goal")

    def test_goal_vacio_pica(self):
        self._caja("vacia", "---\n---\n## Goal\n\n## Resultado\nnada\n")
        with self.assertRaises(caja.CajaError):
            caja.goal_de_caja("vacia")

    def test_placeholder_sin_rellenar_pica(self):
        """La plantilla copiada y no rellenada NO es un goal (mismo criterio que A10)."""
        self._caja("plantilla", "---\n---\n## Goal\n<una línea: qué consigue>\n\n## X\n")
        with self.assertRaises(caja.CajaError):
            caja.goal_de_caja("plantilla")

    def test_slug_no_puede_escapar(self):
        """Anti path-traversal: el slug es [a-z0-9-], como exige A9 del auditor."""
        for malo in ("../fuera", "/etc/passwd", "MAYUS", "con espacio", "", None):
            with self.assertRaises(caja.CajaError):
                caja.goal_de_caja(malo)

    # ── el CLI: rc=2, y no hay dos verdades ──────────────────────────────────────
    def test_cli_caja_ilegible_devuelve_2(self):
        self.assertEqual(2, caja.main(["dossier", "--caja", "no-existe"]))

    def test_cli_caja_e_intencion_son_incompatibles(self):
        self._caja("ambas", "---\n---\n## Goal\nAlgo.\n\n## X\n")
        self.assertEqual(2, caja.main(["dossier", "--caja", "ambas",
                                       "--intencion", "otra cosa"]))


class TestConvocar(unittest.TestCase):
    """La caja sienta a quien declaró. Era el eslabón que faltaba: el charter decía
    `dueno` y `expertos`, el auditor comprobaba que existieran, y nadie los convocaba.

    Lo que protege:
      1. Que el GOAL viaje en la orden — el asiento recibe la vara de éxito, no la tarea.
      2. Que una caja que no está en pie NO se convoque (gasta presupuesto y nadie lo pidió).
      3. Que el arquetipo se traduzca en una restricción explícita, no en un campo decorativo.
    """

    @classmethod
    def setUpClass(cls):
        cls.raiz = tempfile.mkdtemp(prefix="caja-convocar-")
        cls.constelacion = os.path.join(cls.raiz, "00_FUENTE-DE-VERDAD",
                                        "04 · IA", "Constelacion")
        os.makedirs(cls.constelacion)
        cls._root_previo = caja.ROOT
        caja.ROOT = cls.raiz

    @classmethod
    def tearDownClass(cls):
        caja.ROOT = cls._root_previo
        shutil.rmtree(cls.raiz, ignore_errors=True)

    def _caja(self, slug, estado="activa", dueno="comite-medico",
              expertos="[verificacion]", arquetipo="solo-lectura", goal="Cerrar el hueco."):
        d = os.path.join(self.constelacion, slug)
        os.makedirs(d, exist_ok=True)
        cuerpo = (
            "---\ncaja: {s}\nestado: {e}\ndueno: {d}\nexpertos: {x}\n"
            "arquetipo: {a}\nned_desbloquea: una decision\npresupuesto_usd: 5\n---\n"
            "# X\n\n## Goal\n{g}\n\n## Resultado\nlo que sea\n"
        ).format(s=slug, e=estado, d=dueno, x=expertos, a=arquetipo, g=goal)
        with io.open(os.path.join(d, "CAJA.md"), "w", encoding="utf-8") as f:
            f.write(cuerpo)

    def test_el_goal_viaja_en_la_orden(self):
        self._caja("viva", goal="Llegar entera a la cita.")
        orden, rc = caja.convocar("viva")
        self.assertEqual(0, rc)
        self.assertIn("Llegar entera a la cita.", orden)
        self.assertIn("vara de \u00e9xito", orden)

    def test_sienta_al_dueno_y_a_los_expertos(self):
        self._caja("dos", dueno="comite-medico", expertos="[verificacion, diseno]")
        orden, _ = caja.convocar("dos")
        for a in ("comite-medico", "verificacion", "diseno"):
            self.assertIn('subagent_type="%s"' % a, orden)

    def test_el_dueno_no_se_sienta_dos_veces(self):
        """Si el dueno tambien aparece en expertos, es UN asiento, no dos facturados."""
        self._caja("repe", dueno="comite-medico", expertos="[comite-medico, verificacion]")
        orden, _ = caja.convocar("repe")
        self.assertEqual(1, orden.count('subagent_type="comite-medico"'))

    def test_el_arquetipo_se_vuelve_restriccion(self):
        self._caja("lectora", arquetipo="solo-lectura")
        self.assertIn("NO redacta borradores", caja.convocar("lectora")[0])
        self._caja("redactora", arquetipo="redactor-borrador")
        self.assertIn("No env\u00eda, no publica", caja.convocar("redactora")[0])

    def test_una_caja_que_produce_un_artefacto_pide_la_ruta(self):
        """El tercer arquetipo: no toda caja devuelve decisiones. Una que renderiza algo
        entrega un FICHERO, y su asiento tiene que decir dónde quedó — si no, el dossier
        consolida opiniones sobre un artefacto que nadie sabe encontrar."""
        self._caja("render", arquetipo="productor")
        orden = caja.convocar("render")[0]
        self.assertIn("ARTEFACTO", orden)
        self.assertIn("RUTA", orden)
        self.assertIn("No env\u00eda, no publica", orden)   # sigue sin poder salir solo

    def test_la_orden_lleva_el_contrato_y_el_cierre(self):
        """Sin contrato el dossier no recoge nada; sin cierre, el goal no vuelve."""
        self._caja("completa")
        orden, _ = caja.convocar("completa")
        self.assertIn("decisiones", orden)
        self.assertIn("caja.py dossier --caja completa", orden)

    # -- fail-closed ------------------------------------------------------------
    def test_no_convoca_una_caja_que_no_esta_en_pie(self):
        for estado in ("propuesta", "en-pausa", "archivada"):
            self._caja("parada", estado=estado)
            with self.assertRaises(caja.CajaError):
                caja.convocar("parada")

    def test_sin_dueno_ni_expertos_pica(self):
        self._caja("vacia", dueno="", expertos="[]")
        with self.assertRaises(caja.CajaError):
            caja.convocar("vacia")

    def test_cli_devuelve_2_si_no_se_puede_convocar(self):
        self.assertEqual(2, caja.main(["convocar", "--caja", "no-existe"]))
        self.assertEqual(2, caja.main(["convocar"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
