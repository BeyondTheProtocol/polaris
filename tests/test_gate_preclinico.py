#!/usr/bin/env python3
"""test_gate_preclinico.py — un resultado en ratones no sale con cara de que «funciona».

POR QUÉ EXISTE (17-sep-2026). La regla 17 del protocolo —etiquetar el tier de evidencia y
no aplanar nunca un preclínico en «esto funciona»— vivía SOLO en el prompt. El gate ya sabía
cazar una cita FABRICADA, pero no una cita REAL usada para sostener una promesa que el
estudio no sostiene: un xenoinjerto citado bajo «esto reduce el tumor» salía limpio.

El daño es asimétrico y peor que el de la cita inventada: una cita falsa se desmiente
enseñando el registro; una esperanza construida sobre un estudio en ratones ya se ha leído.

Fija las propiedades del check, sin tocar la red (subprocess mockeado):
  1. Afirmar eficacia sobre una cita preclínica sin nombrarlo → se caza.
  2. Si la frase YA dice «en ratones» / «in vitro», la norma se cumple y el check CALLA.
  3. Describir sin prometer no se toca (no es el check de estilo).
  4. Una cita clínica (RCT) no dispara nada.
  5. Red caída o tier desconocido → PENDIENTE: se avisa, nunca bloquea, nunca se da por
     comprobado (auditoría Gorgojo 1.4; antes era fail-open silencioso).
  6. Sin PMID no se consulta el registro (ni coste ni latencia).
  7. La etiqueta cubre SU frase, no el texto entero (auditoría Gorgojo 1.4).
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import gate_salida as g  # noqa: E402

# Respuestas sintéticas (>MIN_CHARS). El PMID es inventado a propósito: el registro se mockea.
APLANADO = ("Sobre la vía que preguntabas, hay trabajo que apunta a que el inhibidor funciona y "
            "reduce el tumor de forma consistente (PMID: 41299692), así que puede valer la pena "
            "llevárselo a tu oncóloga en la visita del mes que viene y preguntarle por ello.")
ETIQUETADO = ("Sobre la vía que preguntabas, el inhibidor funciona y reduce el tumor EN RATONES, "
              "en modelo de xenoinjerto (PMID: 41299692); en personas no se ha probado todavía, "
              "así que sirve para preguntar, no para pedir el fármaco en la próxima visita.")
DESCRIPTIVO = ("El trabajo (PMID: 41299692) mide el volumen tumoral a las seis semanas y describe "
               "el comportamiento de la vía, sin más lectura por ahora. Te lo dejo apuntado en la "
               "fuente de verdad para cuando toque revisar el bloque de dianas con calma.")
SIN_CITA = ("He fusionado la rama y la suite está en verde: 211 comprobaciones del muro sin fallos. "
            "Esto funciona y reduce el tiempo de cada turno a la mitad, así que lo dejo activo y "
            "te lo cuento mañana con el resto del estado del sistema.")


class _Resp:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


def _mock(preclinico, etiqueta="modelo animal (PRECLÍNICO)"):
    """Sustituye la llamada real a tier_evidencia.py por un veredicto fijo."""
    def run(cmd, **kw):
        ids = [a for a in cmd[3:]]
        return _Resp(json.dumps([{"id": i, "tier": "modelo_animal" if preclinico else "rct",
                                  "etiqueta": etiqueta, "preclinico": preclinico,
                                  "entregable": True, "motivo": "test"} for i in ids]))
    return run


def _mock_por_id(mapa, llamadas=None):
    """Veredicto por PMID: 'preclinico' | 'clinico' | 'desconocido'. Anota qué se consultó."""
    def run(cmd, **kw):
        ids = [a for a in cmd[3:]]
        if llamadas is not None:
            llamadas.extend(ids)
        out = []
        for i in ids:
            v = mapa.get(i, "clinico")
            out.append({"id": i, "tier": {"preclinico": "modelo_animal", "clinico": "rct"}.get(v, "desconocido"),
                        "etiqueta": {"preclinico": "modelo animal (PRECLÍNICO)",
                                     "clinico": "ensayo aleatorizado (RCT)"}.get(v, "desconocido"),
                        "preclinico": v == "preclinico", "entregable": v != "desconocido",
                        "motivo": "test"})
        return _Resp(json.dumps(out))
    return run


class GatePreclinico(unittest.TestCase):
    def setUp(self):
        self._real = g.subprocess.run
        # Si falta el clasificador el check sale por la puerta de atrás y queda decorativo.
        self.assertTrue(os.path.exists(os.path.join(g.REPO, "tools", "tier_evidencia.py")),
                        "falta tools/tier_evidencia.py: el check quedaría inerte")

    def tearDown(self):
        g.subprocess.run = self._real

    def test_afirmar_eficacia_sobre_un_preclinico_se_caza(self):
        g.subprocess.run = _mock(True)
        motivo = g.preclinico_aplanado(APLANADO)
        self.assertIsNotNone(motivo)
        self.assertIn("PRECLÍNICA", motivo)

    def test_si_la_frase_ya_dice_en_ratones_el_check_calla(self):
        """La norma no es «no cites preclínico», es «di que lo es». Cumplida → silencio."""
        g.subprocess.run = _mock(True)
        self.assertIsNone(g.preclinico_aplanado(ETIQUETADO))

    def test_describir_sin_prometer_no_se_toca(self):
        g.subprocess.run = _mock(True)
        self.assertIsNone(g.preclinico_aplanado(DESCRIPTIVO))

    def test_una_cita_clinica_no_dispara(self):
        g.subprocess.run = _mock(False, "ensayo aleatorizado (RCT)")
        self.assertIsNone(g.preclinico_aplanado(APLANADO))

    def test_red_caida_queda_pendiente_y_no_bloquea(self):
        """Auditoría Gorgojo 1.4: un fallo de verificación NO es vía libre silenciosa (antes
        devolvía None y la promesa salía como si se hubiera comprobado). Tampoco es un bloqueo:
        la red no puede romper una conversación. Queda PENDIENTE: aviso, nunca «verificado»."""
        def boom(*a, **k):
            raise OSError("red caída")
        g.subprocess.run = boom
        motivo = g.preclinico_aplanado(APLANADO)
        self.assertIsNotNone(motivo)
        self.assertTrue(motivo.startswith(g.PENDIENTE))
        self.assertEqual(g._bloquean([("preclinico_aplanado", "s", motivo)], "bloqueo"), [])

    def test_tier_desconocido_queda_pendiente_y_no_bloquea(self):
        """Un paper recién indexado aún no tiene MeSH. Bloquear ahí sería ruido constante; callar,
        un «verificado» por defecto. Se avisa como pendiente."""
        def run(cmd, **kw):
            return _Resp(json.dumps([{"id": "x", "tier": "desconocido", "preclinico": False,
                                      "entregable": False}]))
        g.subprocess.run = run
        motivo = g.preclinico_aplanado(APLANADO)
        self.assertIsNotNone(motivo)
        self.assertTrue(motivo.startswith(g.PENDIENTE))
        self.assertEqual(g._bloquean([("preclinico_aplanado", "s", motivo)], "bloqueo"), [])

    def test_un_pendiente_no_tapa_una_violacion_real(self):
        """Si un PMID del párrafo es preclínico y otro no se pudo resolver, manda la violación."""
        g.subprocess.run = _mock_por_id({"PMID:41299692": "preclinico", "PMID:30137196": "desconocido"})
        texto = ("El inhibidor reduce el tumor de forma consistente (PMID: 41299692, PMID: 30137196), "
                 "así que merece la pena llevárselo a tu oncóloga en la próxima visita del mes.")
        motivo = g.preclinico_aplanado(texto)
        self.assertIsNotNone(motivo)
        self.assertFalse(motivo.startswith(g.PENDIENTE))

    def test_sin_pmid_no_toca_la_red(self):
        def boom(*a, **k):
            raise AssertionError("no debe consultar el registro sin PMID en el texto")
        g.subprocess.run = boom
        self.assertIsNone(g.preclinico_aplanado(SIN_CITA))

    # ── Auditoría Gorgojo 1.4 (22-sep-26): la etiqueta cubre SU afirmación, no el texto entero ──
    def test_gorgojo_14_etiqueta_en_otra_frase_no_exime(self):
        """Reproducido el 24-sep: «A mejora la supervivencia (PMID A). B, en ratones (PMID B)»
        pasaba sin consultar el registro porque «en ratones» aparecía en ALGÚN sitio."""
        llamadas = []
        g.subprocess.run = _mock_por_id({"PMID:30137196": "preclinico"}, llamadas)
        texto = ("El fármaco A mejora la supervivencia en {{DIAGNOSTICO}} (PMID: 30137196). "
                 "El fármaco B, en cambio, solo se ha probado en ratones (PMID: 12345678). "
                 "Por eso el A es la opción que yo pondría encima de la mesa en la consulta.")
        motivo = g.preclinico_aplanado(texto)
        self.assertIn("PMID:30137196", llamadas, "no consultó el registro por la afirmación sobre A")
        self.assertIsNotNone(motivo)
        self.assertFalse(motivo.startswith(g.PENDIENTE))

    def test_gorgojo_14_la_cita_de_la_frase_manda_sobre_la_del_parrafo(self):
        """A cita su propio PMID (clínico); B, etiquetado, cita otro (preclínico). Juzgar A por el
        PMID de B sería un falso positivo: se mira la cita de SU frase."""
        g.subprocess.run = _mock_por_id({"PMID:30137196": "clinico", "PMID:12345678": "preclinico"})
        texto = ("El fármaco A mejora la supervivencia en {{DIAGNOSTICO}} (PMID: 30137196). "
                 "El fármaco B, en cambio, solo se ha probado en ratones (PMID: 12345678). "
                 "Te lo dejo apuntado para la consulta del mes que viene con la oncóloga.")
        self.assertIsNone(g.preclinico_aplanado(texto))

    def test_gorgojo_14_la_cita_en_la_frase_siguiente_cuenta(self):
        """«A reduce el tumor. Fuente: PMID X.» son dos frases y UNA unidad de evidencia."""
        g.subprocess.run = _mock_por_id({"PMID:41299692": "preclinico"})
        texto = ("Hay trabajo reciente que apunta a que el inhibidor reduce el tumor de forma clara. "
                 "Fuente: PMID: 41299692. Merece la pena llevárselo a tu oncóloga en la visita.")
        self.assertIsNotNone(g.preclinico_aplanado(texto))

    def test_gorgojo_14_un_pmid_ya_etiquetado_no_contamina_otra_frase_sin_cita(self):
        """«A funciona.» (sin cita) + «B, en ratones (PMID B).» → el PMID de B ya está dicho como
        preclínico en su frase: no se le endosa a A."""
        g.subprocess.run = _mock_por_id({"PMID:12345678": "preclinico"})
        texto = ("En la consulta comentaron que el fármaco A funciona bien en casos como el tuyo. "
                 "El fármaco B solo se ha probado en ratones (PMID: 12345678), así que es otra cosa.")
        self.assertIsNone(g.preclinico_aplanado(texto))

    def test_gorgojo_14_una_abreviatura_no_parte_la_frase(self):
        """«et al.» no es fin de frase: la etiqueta de detrás sigue cubriendo la afirmación."""
        g.subprocess.run = _mock_por_id({"PMID:41299692": "preclinico"})
        texto = ("Según Smith et al. el inhibidor reduce el tumor, en ratones y con xenoinjerto "
                 "(PMID: 41299692), así que sirve para preguntar, no para pedirlo todavía en consulta.")
        self.assertIsNone(g.preclinico_aplanado(texto))

    def test_hablar_del_gate_con_codigo_en_linea_no_bloquea(self):
        """Falso positivo real del replay (24-sep): explicar el check citando su caso de prueba."""
        g.subprocess.run = _mock(True)
        texto = ("- Check vivo en casa base: la frase con `PMID: 41299692` bajo \"reduce el tumor\" "
                 "**bloquea**, y `_bloquean` confirma que bloquea aunque el gate global esté en aviso.")
        self.assertIsNone(g.preclinico_aplanado(texto))

    def test_citar_la_frase_de_prueba_entre_comillas_no_bloquea(self):
        """Segundo falso positivo real del replay: una fila de tabla que CITA el caso de prueba."""
        g.subprocess.run = _mock(True)
        texto = ("| Caso | Resultado |\n|---|---|\n"
                 "| \"el inhibidor funciona y reduce el tumor (PMID: 41299692)\" | **bloquea** |\n"
                 "| lo mismo con «en ratones» en la frase | pasa |")
        self.assertIsNone(g.preclinico_aplanado(texto))

    def test_una_fila_de_tabla_es_su_propia_unidad(self):
        """La etiqueta de una fila no cubre la promesa de otra."""
        g.subprocess.run = _mock_por_id({"PMID:41299692": "preclinico"})
        texto = ("| Lead | Qué dice | Tier |\n|---|---|---|\n"
                 "| Fármaco B (PMID 12345678) | frena el crecimiento | en ratones |\n"
                 "| Fármaco A (PMID 41299692) | el inhibidor funciona y reduce el tumor | ensayo |")
        self.assertIsNotNone(g.preclinico_aplanado(texto))

    def test_gorgojo_14_etiqueta_en_otro_parrafo_no_exime(self):
        g.subprocess.run = _mock_por_id({"PMID:41299692": "preclinico"})
        texto = ("El inhibidor reduce el tumor de forma consistente (PMID: 41299692).\n\n"
                 "Aparte: lo de la otra vía está visto solo in vitro, en líneas celulares.")
        self.assertIsNotNone(g.preclinico_aplanado(texto))

    def test_check_registrado_en_el_gate(self):
        self.assertIn("preclinico_aplanado", g.CHECKS)

    def test_check_registrado_como_norma(self):
        """Sin norma en tools/normas.json el check no se activa: quedaría decorativo."""
        import normas
        checks = [r["check"] for r in normas.reglas_salida()]
        self.assertIn("preclinico_aplanado", checks)

    def test_bloquea_aunque_el_gate_global_este_en_aviso(self):
        """Su modo propio manda: una esperanza falsa, una vez leída, tampoco se deshace."""
        self.assertEqual(g._bloquean([("preclinico_aplanado", "s", "m")], "aviso"),
                         ["preclinico_aplanado"])


if __name__ == "__main__":
    unittest.main(verbosity=0)
