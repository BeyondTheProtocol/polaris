#!/usr/bin/env python3
"""Test: el enrutador de LLMs elige bien, y sobre todo NO se salta el muro.

POR QUÉ EXISTE (2-sep-2026). `tools/enruta.py` decide a qué LLM va cada tarea. Es una pieza
peligrosa por definición: un enrutador que se equivoca no da un error, da una respuesta — y
si se equivoca en la dirección del muro, manda datos clínicos a un tercero sin que nadie lo
note. Por eso lo primero que se prueba aquí no es que acierte, es que no abra la puerta.

Los casos del muro son los que no pueden fallar nunca:
  · contenido sensible → solo destinos de confianza, el resto descartados con motivo
  · si el borde no puede clasificar → se asume sensible (fail-closed de verdad)
  · si además ningún destino de confianza está vivo → NADIE, se bloquea, no se degrada
    (`feedback-no-degradar-lo-critico-bloquear-avisar`)

La salud se inyecta en los tests (`estado=`) para que no dependan de la red ni gasten un token.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import enruta  # noqa: E402

TODOS_VIVOS = {n: {"ok": True, "detalle": "test"} for n in enruta.PROVEEDORES}


def _sin(*caidos):
    """Estado con todos vivos menos los que se nombren."""
    e = {n: {"ok": True, "detalle": "test"} for n in enruta.PROVEEDORES}
    for c in caidos:
        e[c] = {"ok": False, "detalle": "caído (test)"}
    return e


class ElMuroMandaPrimero(unittest.TestCase):
    """Los casos que no pueden fallar nunca."""

    def test_contenido_clinico_solo_va_a_destinos_de_confianza(self):
        d = enruta.elegir("analiza esto",
                          contenido="Paciente con variante PIK3CA H1047R y HLA-A*02:01",
                          estado=TODOS_VIVOS)
        self.assertTrue(d.sensible, "el borde tenía que marcar esto como sensible: %s"
                        % d.motivo_sensible)
        for nombre in d.elegidos:
            self.assertTrue(enruta.PROVEEDORES[nombre]["confianza"],
                            "%s NO es destino de confianza y ha salido elegido para contenido "
                            "sensible. Esto es una fuga." % nombre)

    def test_los_terceros_quedan_descartados_con_motivo(self):
        d = enruta.elegir("busca lo último sobre esto",
                          contenido="rs121913529 en cromosoma 12", estado=TODOS_VIVOS)
        self.assertTrue(d.sensible)
        descartados = dict(d.descartados)
        for tercero in ("grok", "perplexity", "gemini", "chatgpt", "nvidia", "glm"):
            self.assertIn(tercero, descartados,
                          "%s tenía que estar descartado por sensibilidad" % tercero)
            self.assertIn("sensible", descartados[tercero])

    def test_si_el_borde_falla_se_asume_sensible(self):
        """Fail-closed de verdad: sin guardia, la puerta se cierra, no se abre."""
        original = enruta._clasificar_muro
        try:
            def revienta(texto):
                raise RuntimeError("borde caído a propósito")
            # Se simula el fallo dentro de la función real, no saltándosela.
            import borde
            orig_clasificar = borde.clasificar
            borde.clasificar = revienta
            sensible, motivo = enruta._clasificar_muro("un texto cualquiera")
            self.assertTrue(sensible, "si el borde revienta hay que ASUMIR sensible")
            self.assertIn("asumo sensible", motivo)
        finally:
            borde.clasificar = orig_clasificar
            enruta._clasificar_muro = original

    def test_sensible_y_ningun_confiable_vivo_bloquea_no_degrada(self):
        estado = _sin("claude", "local")
        d = enruta.elegir("analiza", contenido="variante BRCA1 c.68_69delAG", estado=estado)
        self.assertTrue(d.sensible)
        self.assertEqual(d.elegidos, [],
                         "con contenido sensible y ningún destino de confianza vivo hay que "
                         "BLOQUEAR; ha elegido %s" % d.elegidos)
        self.assertIn("__bloqueado__", d.motivos)
        self.assertIn("no se degrada", d.motivos["__bloqueado__"])


class CrudoIdentificableSoloLocal(unittest.TestCase):
    """Arreglo A (13-sep-26, `plan-enrutado-crudo-solo-local-y-gate-por-check`): con contenido
    crudo identificable (PII directa: nombre/DNI/email/teléfono/NHC), `claude` deja de contar
    como destino de confianza para la decisión — lectura ESTRICTA de {{TITULAR}}. Antes, tanto el
    filtrado normal por capacidad como la 'caída segura' (pensada para cuando NADIE de
    confianza sigue vivo) colaban a `claude` con dato crudo delante: el bug que reprodujo
    @MrHydeDev en X. Con crudo, solo `local` vale; si no está, se bloquea, nunca se degrada."""

    PII = "Informe del caso, DNI 12345678Z, contacto alguien@ejemplo.com, NHC 999001"

    def test_deidentifica_con_pii_y_local_caido_bloquea_no_cae_a_claude(self):
        """Repro fila 2 de la tabla del plan: antes ['claude'] (caída segura mal aplicada)."""
        d = enruta.elegir("de-identifica este informe", contenido=self.PII, estado=_sin("local"))
        self.assertTrue(d.sensible)
        self.assertTrue(d.crudo, "DNI/email/NHC son identificador directo: %s" % d.motivo_crudo)
        self.assertNotIn("claude", d.elegidos,
                        "crudo identificable jamás puede caer a claude: %s" % d.elegidos)
        self.assertEqual(d.elegidos, [], "local caído + crudo → bloqueo, no degradar a claude")
        self.assertIn("__bloqueado__", d.motivos)
        self.assertIn("local", d.motivos["__bloqueado__"],
                     "el bloqueo tiene que decir que la salida es local, no un genérico")

    def test_clasifica_con_pii_todos_vivos_bloquea_no_cae_a_claude(self):
        """Repro fila 3 de la tabla del plan: antes ['claude'] con TODOS los proveedores vivos,
        porque 'clasifica' pide razonar y local no sirve para razonar (se bloquea igual: no
        hay ningún destino de confianza capaz para esa tarea con crudo delante)."""
        d = enruta.elegir("clasifica este informe", contenido=self.PII, estado=TODOS_VIVOS)
        self.assertTrue(d.sensible)
        self.assertTrue(d.crudo)
        self.assertNotIn("claude", d.elegidos,
                        "crudo identificable jamás puede caer a claude: %s" % d.elegidos)

    def test_n1_generico_sin_nombre_local_caido_sigue_cayendo_a_claude(self):
        """Canario: un dato clínico/genómico AISLADO (sin nombre) es N1, no crudo. La caída a
        claude para N1 sigue siendo legítima (`.claude/rules/clinico.md`) y no se rompe."""
        d = enruta.elegir("analiza esto", contenido="FGFR1 amplificado en mama HR+",
                          estado=_sin("local"))
        self.assertTrue(d.sensible)
        self.assertFalse(d.crudo, "una huella genómica aislada no es identificador directo: %s"
                         % d.motivo_crudo)
        self.assertEqual(d.elegidos, ["claude"])

    def test_decision_expone_crudo_para_auditoria(self):
        d = enruta.elegir("analiza", contenido=self.PII, estado=TODOS_VIVOS)
        self.assertTrue(d.crudo)
        self.assertIn("crudo", d.como_dict())
        self.assertIn("motivo_crudo", d.como_dict())

    def test_identificador_directo_positivos_identificador_duro(self):
        """Identificador DURO (DNI/NIE, NHC, email, teléfono): crudo SIEMPRE, con o sin nombre
        alrededor — corrección de {{TITULAR}} 13-sep-26 sobre la primera versión del arreglo."""
        import borde
        positivos = [
            ("email", "escríbeme a alguien@ejemplo.com"),
            ("DNI", "su DNI es 12345678Z"),
            ("teléfono", "mi número es +34 600 123 456"),
            ("NHC", "paciente con NHC 999001 en seguimiento"),
        ]
        for etiqueta, texto in positivos:
            with self.subTest(etiqueta=etiqueta):
                crudo, motivo = borde.identificador_directo(texto)
                self.assertTrue(crudo, "%s tenía que marcar crudo: %r" % (etiqueta, motivo))

    def test_identificador_directo_dni_con_nombre_sigue_siendo_crudo(self):
        """«Paciente Juana Pérez, DNI 12345678Z…» → crudo (el identificador duro manda, con o
        sin nombre delante; no hace falta relato clínico completo)."""
        import borde
        crudo, motivo = borde.identificador_directo(
            "Paciente Juana Pérez, DNI 12345678Z, acude a revisión")
        self.assertTrue(crudo, "DNI presente: tenía que marcar crudo: %r" % motivo)

    def test_identificador_directo_nombre_solo_no_es_crudo(self):
        """Corrección de {{TITULAR}} 13-sep-26 (`.claude/rules/clinico.md` L21-23): N2 es «relato o
        informe crudo», no un nombre suelto. Buscar su nombre público (monitorización de redes)
        es legítimo y no es el bug de @MrHydeDev — ese llevaba PII dura o relato clínico."""
        import borde
        for texto in ("el caso de {{TITULAR}} {{APELLIDO}}", "busca en X qué se dice de {{TITULAR}} {{APELLIDO}}",
                      "habló con {{CONTACTO}} ayer"):
            with self.subTest(texto=texto):
                crudo, motivo = borde.identificador_directo(texto)
                self.assertFalse(crudo, "nombre solo, sin nada clínico, NO es crudo: %r" % motivo)

    def test_identificador_directo_nombre_mas_clinico_es_crudo(self):
        """Nombre Y contenido clínico/genómico juntos SÍ son un relato identificable (N2)."""
        import borde
        crudo, motivo = borde.identificador_directo(
            "{{TITULAR}} {{APELLIDO}}, informe de biopsia, Ki67 alto")
        self.assertTrue(crudo, "nombre + dato clínico tenía que marcar crudo: %r" % motivo)

    def test_identificador_directo_canario_negativo_dato_clinico_aislado(self):
        """Un dato clínico/genómico SIN nombre (N1) no es identificador directo (N2)."""
        import borde
        crudo, motivo = borde.identificador_directo("PIK3CA H1047R")
        self.assertFalse(crudo, "un dato clínico aislado no es crudo identificable: %r" % motivo)

    def test_nombre_mas_clinico_local_caido_bloquea(self):
        """«{{TITULAR}} {{APELLIDO}}, informe de biopsia, Ki67…» con local caído → bloqueo (nunca
        claude): nombre + clínico es un relato identificable de verdad (N2)."""
        d = enruta.elegir("analiza esto",
                          contenido="{{TITULAR}} {{APELLIDO}}, informe de biopsia, Ki67 alto",
                          estado=_sin("local"))
        self.assertTrue(d.sensible)
        self.assertTrue(d.crudo, "nombre + clínico tenía que marcar crudo: %s" % d.motivo_crudo)
        self.assertEqual(d.elegidos, [], "local caído + crudo → bloqueo, nunca claude")

    def test_busqueda_de_su_nombre_con_local_caido_sigue_cayendo_a_claude(self):
        """«busca en X qué se dice de {{TITULAR}} {{APELLIDO}}» con local caído → ['claude'], como antes
        del arreglo: su nombre en una búsqueda pública es legítimo (monitorización), no lleva
        dato clínico, y la caída segura para eso sigue abierta."""
        d = enruta.elegir("busca en X qué se dice de {{TITULAR}} {{APELLIDO}}", estado=_sin("local"))
        self.assertTrue(d.sensible)
        self.assertFalse(d.crudo, "nombre solo en una búsqueda no es crudo: %s" % d.motivo_crudo)
        self.assertEqual(d.elegidos, ["claude"])


class EligeSegunLaTarea(unittest.TestCase):
    def test_buscar_en_x_va_a_grok(self):
        d = enruta.elegir("mira qué se dice en X sobre este ensayo", estado=TODOS_VIVOS)
        self.assertFalse(d.sensible, "esta frase no debería marcar sensible: %s" % d.motivo_sensible)
        self.assertEqual(d.primero, "grok",
                         "para X el primero tiene que ser grok, salió %s" % d.primero)

    def test_una_imagen_va_a_gemini(self):
        d = enruta.elegir("mira esta imagen y dime qué se ve", estado=TODOS_VIVOS)
        self.assertIn("vision", d.capacidades)
        self.assertEqual(d.primero, "gemini")

    def test_trabajo_masivo_va_al_carril_gratis(self):
        d = enruta.elegir("clasifica cada uno de los cientos de correos", estado=TODOS_VIVOS)
        self.assertIn("volumen", d.capacidades)
        self.assertEqual(d.primero, "nvidia",
                         "el trabajo masivo tiene que ir al carril gratis, salió %s" % d.primero)

    def test_deidentificar_va_a_local(self):
        """Quitar PII no puede exigir que la PII salga primero."""
        d = enruta.elegir("de-identifica este informe", estado=TODOS_VIVOS)
        self.assertEqual(d.primero, "local")

    def test_sin_pista_reconocible_cae_en_razonar(self):
        d = enruta.elegir("hazme un ñu con patatas", estado=TODOS_VIVOS)
        self.assertEqual(d.capacidades, ["razonar"])
        self.assertTrue(d.elegidos, "razonar siempre tiene que tener a alguien")


class SaludYPanel(unittest.TestCase):
    def test_un_proveedor_caido_no_se_propone(self):
        d = enruta.elegir("busca en X", estado=_sin("grok"))
        self.assertNotIn("grok", d.elegidos)
        self.assertIn("grok", dict(d.descartados))
        self.assertIn("no responde", dict(d.descartados)["grok"])

    def test_critico_devuelve_panel_de_casas_distintas(self):
        d = enruta.elegir("analiza y decide la estrategia", critico=True, estado=TODOS_VIVOS)
        self.assertTrue(d.panel)
        self.assertGreaterEqual(len(d.elegidos), 2,
                                "un panel de uno no es un panel: %s" % d.elegidos)
        casas = [enruta.PROVEEDORES[n]["destino"].split(":")[-1] for n in d.elegidos]
        self.assertEqual(len(casas), len(set(casas)),
                         "el panel repite casa (%s): dos modelos de la misma familia se "
                         "equivocan igual" % casas)

    def test_sin_critico_no_hay_panel(self):
        d = enruta.elegir("analiza esto", estado=TODOS_VIVOS)
        self.assertFalse(d.panel)


class ElCatalogoEsCoherente(unittest.TestCase):
    def test_solo_local_y_claude_son_de_confianza(self):
        """Si alguien marca un tercero como de confianza, esto lo canta."""
        confiables = {n for n, m in enruta.PROVEEDORES.items() if m["confianza"]}
        self.assertEqual(confiables, {"claude", "local"},
                         "solo claude (cleared) y local pueden recibir contenido sensible; "
                         "marcados: %s" % confiables)

    def test_todo_proveedor_declara_para_que_sirve(self):
        for nombre, meta in enruta.PROVEEDORES.items():
            self.assertTrue(meta.get("fuerzas"),
                            "%s no declara ninguna fuerza: nunca lo elegiría nadie" % nombre)
            for cap, motivo in meta["fuerzas"].items():
                self.assertTrue(motivo and len(motivo) > 10,
                                "%s/%s no explica POR QUÉ es bueno en eso" % (nombre, cap))

    def test_toda_capacidad_de_las_pistas_tiene_quien_la_sirva(self):
        servidas = set()
        for meta in enruta.PROVEEDORES.values():
            servidas |= set(meta["fuerzas"])
        huerfanas = set(enruta.PISTAS) - servidas
        self.assertEqual(huerfanas, set(),
                         "estas capacidades se detectan pero nadie las sirve: %s" % huerfanas)


class EjecutarNoSeSaltaElMuro(unittest.TestCase):
    """`ejecutar()` es quien de verdad manda datos fuera. Aquí no hay margen."""

    def test_sensible_no_llama_a_ningun_tercero(self):
        """El caso peor: «busca en X sobre este dato clínico». No puede salir nada.

        Se construye una Decision a mano con un tercero dentro, simulando que alguien
        se saltara el filtro de `elegir()`. `ejecutar()` tiene que volver a mirar y negarse:
        cinturón y tirantes. Una comprobación de más cuesta microsegundos; una de menos,
        una fuga irreversible.
        """
        d = enruta.Decision(elegidos=["grok"], motivos={}, capacidades=["redes"],
                            sensible=True, motivo_sensible="dato clínico/genómico (HLA)",
                            panel=False, descartados=[])
        res = enruta.ejecutar(d, "busca esto en X")
        self.assertEqual(len(res), 1)
        nombre, ok, salida = res[0]
        self.assertEqual(nombre, "grok")
        self.assertFalse(ok, "NO puede ejecutarse un tercero con contenido sensible")
        self.assertIn("BLOQUEADO", salida)

    def test_sin_elegidos_devuelve_bloqueo_y_no_inventa(self):
        d = enruta.Decision(elegidos=[], motivos={"__bloqueado__": "sin destino de confianza"},
                            capacidades=["razonar"], sensible=True, motivo_sensible="PII",
                            panel=False, descartados=[])
        res = enruta.ejecutar(d, "lo que sea")
        self.assertEqual(res[0][0], "__nadie__")
        self.assertFalse(res[0][1])

    def test_claude_no_se_ejecuta_por_subproceso(self):
        """Claude ya está corriendo: llamarse a sí mismo sería absurdo y caro.

        `local` SÍ se ejecuta desde el 2-sep-2026 (tools/local.py sobre ollama); antes de
        tener modelo util se devolvia como indicacion, y este test lo daba por sentado.
        """
        d = enruta.Decision(elegidos=["claude"], motivos={}, capacidades=["razonar"],
                            sensible=False, motivo_sensible="limpio",
                            panel=False, descartados=[])
        nombre_r, ok, salida = enruta.ejecutar(d, "hola")[0]
        self.assertEqual(nombre_r, "claude")
        self.assertTrue(ok)
        self.assertIn("no se ejecuta desde aqui", salida)

    def test_local_tiene_tool_y_flag_de_deid(self):
        """El cableado que hace util al modelo local, sin gastar una llamada.

        `deidentificar` no se pide con lenguaje natural: se pide con el flag de la tool, que
        lleva el prompt afinado. Sin el mapa, `--ejecutar "de-identifica esto"` mandaba la
        frase suelta y el modelo DEVOLVIA EL TEXTO CON LA PII DENTRO, aparentando exito.
        """
        self.assertEqual(enruta.PROVEEDORES["local"]["tool"], "local.py")
        self.assertEqual(enruta.FLAGS_POR_CAPACIDAD.get(("local", "deidentificar")), ["--deid"])


class DecidirEsRapidoYNoDejaANadie(unittest.TestCase):
    """11-sep-26: decidir tardaba >120 s (salud en serie) y una búsqueda de literatura sobre un
    gen daba NADIE. Las dos cosas hacían el enrutador inservible en un hook."""

    def setUp(self):
        import tempfile
        self._orig = (enruta.CACHE_SALUD, enruta.LOCK_REFRESCO)
        self._tmp = tempfile.mkdtemp()
        enruta.CACHE_SALUD = os.path.join(self._tmp, "salud.json")
        enruta.LOCK_REFRESCO = os.path.join(self._tmp, "lock")
        os.environ["BTP_ENRUTA_SIN_REFRESCO"] = "1"

    def tearDown(self):
        enruta.CACHE_SALUD, enruta.LOCK_REFRESCO = self._orig
        os.environ.pop("BTP_ENRUTA_SIN_REFRESCO", None)

    def test_sensible_sin_buscador_de_confianza_cae_a_claude(self):
        d = enruta.elegir("busca qué se ha publicado sobre FGFR1 amplificado",
                          contenido="FGFR1 amplificado en mama HR+", estado=TODOS_VIVOS)
        self.assertTrue(d.sensible)
        self.assertEqual(d.elegidos, ["claude"])
        self.assertIn("caída segura", d.motivos["claude"])

    def test_la_caida_segura_nunca_abre_a_un_tercero(self):
        d = enruta.elegir("busca en X", contenido="variante BRCA1 c.68_69delAG",
                          estado=TODOS_VIVOS)
        for n in d.elegidos:
            self.assertTrue(enruta.PROVEEDORES[n]["confianza"], n)

    def test_salud_rapida_usa_la_foto_vencida_sin_medir(self):
        import json, time
        json.dump({"ts": time.time() - 99999, "estado": {"grok": {"ok": False, "detalle": "x"}}},
                  open(enruta.CACHE_SALUD, "w"))
        t0 = time.time()
        e = enruta.salud_rapida()
        self.assertLess(time.time() - t0, 1.0)
        self.assertFalse(e["grok"]["ok"], "tenía que respetar la última foto conocida")
        self.assertIn("claude", e)

    def test_salud_rapida_sin_foto_no_bloquea(self):
        import time
        t0 = time.time()
        e = enruta.salud_rapida()
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(set(e), set(enruta.PROVEEDORES))

    def test_salud_prueba_en_paralelo_y_solo_lo_pedido(self):
        import time
        orig = enruta.probar
        vistos = []

        def lento(nombre, **kw):
            vistos.append(nombre)
            time.sleep(0.3)
            return True, "ok"
        enruta.probar = lento
        try:
            t0 = time.time()
            e = enruta.salud(refrescar=True, solo=["claude", "local", "grok"])
            self.assertLess(time.time() - t0, 0.8, "en serie serían 0,9 s: no va en paralelo")
        finally:
            enruta.probar = orig
        self.assertEqual(sorted(vistos), ["claude", "grok", "local"])
        self.assertTrue(e["grok"]["ok"])


class LasConsultasSeMidenComoBusqueda(unittest.TestCase):
    """11-sep-26: una BÚSQUEDA sale tal cual, así que no le aplica el embargo de palabras
    públicas: «vacuna» ya se puede decir (29-7-26). «{{CONTACTO}}», su nombre, PII, marcadores y
    huella genómica siguen sin salir nunca: es el mismo borde estricto, sin esas palabras."""

    def test_vacuna_en_una_busqueda_ya_no_bloquea(self):
        d = enruta.elegir("busca en X qué se dice de la vacuna de BioNTech", estado=TODOS_VIVOS)
        self.assertFalse(d.sensible, d.motivo_sensible)
        self.assertIn("grok", d.elegidos)

    def test_rastrear_tambien_es_una_busqueda(self):
        """19-sep-26: la capacidad `rastrear` nació fuera de CAPS_CONSULTA y el embargo de
        palabras públicas volvió a bloquear una búsqueda legítima. Una capacidad de BUSCAR se
        declara en los dos sitios, o el borde cambia de criterio sin que nadie lo decida."""
        d = enruta.elegir("rastrea en foros y en X qué se dice de la vacuna de BioNTech",
                          estado=TODOS_VIVOS)
        self.assertFalse(d.sensible, d.motivo_sensible)
        self.assertIn("grok", d.elegidos)
        self.assertIn("rastrear", enruta.CAPS_CONSULTA)

    def test_su_nombre_en_una_busqueda_sigue_bloqueando(self):
        d = enruta.elegir("busca en X qué se dice de {{TITULAR}} {{APELLIDO}}", estado=TODOS_VIVOS)
        self.assertTrue(d.sensible)
        self.assertNotIn("grok", d.elegidos)

    def test_huella_genomica_en_una_busqueda_sigue_bloqueando(self):
        for q in ("busca lo último sobre HLA-A*02:01", "busca papers de PIK3CA H1047R",
                  "busca en X a quien lleve rs121913529"):
            d = enruta.elegir(q, estado=TODOS_VIVOS)
            self.assertTrue(d.sensible, "%r tenía que bloquear: %s" % (q, d.motivo_sensible))

    def test_segunda_opinion_sigue_con_el_borde_estricto(self):
        d = enruta.elegir("contrasta si la vacuna encaja con lo que dije", estado=TODOS_VIVOS)
        self.assertTrue(d.sensible, "segunda opinión suele llevar su caso: borde estricto")

    def test_con_contenido_manda_el_borde_estricto(self):
        d = enruta.elegir("busca lo último sobre esto", contenido="la vacuna de la paciente",
                          estado=TODOS_VIVOS)
        self.assertTrue(d.sensible)

    def test_si_el_borde_falla_la_consulta_se_asume_sensible(self):
        import borde
        orig = borde.clasificar
        borde.clasificar = lambda *a, **k: 1 / 0
        try:
            sensible, motivo = enruta._clasificar_consulta("busca algo")
        finally:
            borde.clasificar = orig
        self.assertTrue(sensible)
        self.assertIn("asumo sensible", motivo)

    def test_contacto_y_marcadores_siguen_bloqueando_en_una_busqueda(self):
        for q in ("busca en X qué se dice de {{CONTACTO}}", "busca lo último sobre FGFR1 amplificado",
                  "busca en X la variante BRCA1 c.68_69delAG"):
            d = enruta.elegir(q, estado=TODOS_VIVOS)
            self.assertTrue(d.sensible, "%r tenía que bloquear: %s" % (q, d.motivo_sensible))

    def test_su_caso_en_una_busqueda_sigue_bloqueando(self):
        d = enruta.elegir("busca lo último sobre la vacuna para mi caso", estado=TODOS_VIVOS)
        self.assertTrue(d.sensible, d.motivo_sensible)

    def test_ejecutar_avisa_a_la_tool_de_que_es_una_busqueda(self):
        """El hilo entero: enruta elige Grok y grok.py tiene que medir igual, o lo rebota."""
        import subprocess
        vistos = []
        orig = subprocess.run

        class R:
            returncode, stdout, stderr = 0, "ok", ""

        def falso(cmd, **kw):
            vistos.append((kw.get("env") or {}).get("BTP_EGRESS_CONSULTA"))
            return R()
        subprocess.run = falso
        try:
            d = enruta.elegir("busca en X qué se dice de la vacuna de BioNTech", estado=TODOS_VIVOS)
            enruta.ejecutar(d, "busca en X qué se dice de la vacuna de BioNTech")
            # misma decisión, pero con material pegado: eso ya no es una búsqueda
            enruta.ejecutar(d, "busca en X qué se dice de esto", contenido="texto cualquiera")
        finally:
            subprocess.run = orig
        self.assertEqual(len(vistos), 2, "tenían que salir dos llamadas a la tool: %r" % vistos)
        self.assertEqual(vistos[0], "1", "una búsqueda tiene que llegar marcada a la tool")
        self.assertIsNone(vistos[-1], "con contenido, la tool mide con el borde estricto")

    def test_el_borde_de_la_tool_deja_pasar_la_busqueda_marcada(self):
        import borde
        viejo = os.environ.get(borde.ENV_CONSULTA)
        try:
            os.environ[borde.ENV_CONSULTA] = "1"
            self.assertTrue(borde.guard_cli("busca en X qué se dice de la vacuna de BioNTech", "grok"))
            self.assertFalse(borde.guard_cli("busca en X a {{TITULAR}} {{APELLIDO}}", "grok"))
            os.environ.pop(borde.ENV_CONSULTA)
            self.assertFalse(borde.guard_cli("busca en X qué se dice de la vacuna de BioNTech", "grok"))
        finally:
            if viejo is None:
                os.environ.pop(borde.ENV_CONSULTA, None)
            else:
                os.environ[borde.ENV_CONSULTA] = viejo

if __name__ == "__main__":
    casos = unittest.TestSuite([
        unittest.TestLoader().loadTestsFromTestCase(c)
        for c in (ElMuroMandaPrimero, CrudoIdentificableSoloLocal, EligeSegunLaTarea, SaludYPanel,
                  ElCatalogoEsCoherente, EjecutarNoSeSaltaElMuro,
                  DecidirEsRapidoYNoDejaANadie, LasConsultasSeMidenComoBusqueda)])
    res = unittest.TextTestRunner(verbosity=0).run(casos)
    if res.wasSuccessful():
        print("✅ ENRUTADOR EN VERDE (%d casos · el muro manda, la salud filtra, el panel diversifica)"
              % res.testsRun)
    raise SystemExit(0 if res.wasSuccessful() else 1)
