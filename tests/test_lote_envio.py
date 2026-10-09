#!/usr/bin/env python3
"""test_lote_envio.py — UNA orden de {{TITULAR}} envía N DMs que ella leyó (9-oct-26).

Qué se fija (ver tools/lote_envio.py):
  · sin lote, nada cambia: un batch que teclea en bypass sigue sin frenarse (canario de lo de hoy);
  · el lote vale para los ítems del manifiesto que ella tenía DELANTE, cada uno una vez, y para
    nada más: texto alterado, hilo cambiado, ítem repetido, ítem de fuera, lote de 11, manifiesto
    que no sale de un mensaje mío, orden que no es suya, lote caducado, mensaje posterior;
  · el permiso de lote no abre el de un solo uso (correo, Bash, gh siguen cerrados);
  · el aviso de «sin clave» distingue «no existe» de «no se puede leer».

Los tests no tocan el Llavero real: la clave va por entorno / CLAVE_TEST y `subprocess` se simula.
"""
import json
import os
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
from test_ok_envio_blindado import _Base, CLAVE, CASA, GMAIL, _decision, SALIDA  # noqa: E402

import lote_envio as L  # noqa: E402
import permiso_envio as P  # noqa: E402

BATCH = "mcp__claude-in-chrome__browser_batch"
COMPUTER = "mcp__claude-in-chrome__computer"
JS = "mcp__claude-in-chrome__javascript_tool"
U = "https://www.linkedin.com/messaging/thread/2-%s/"
URL = {n: U % n for n in "abcdefghijkl"}
TEXTOS = {"a": "Hola Ana, gracias por escribir. Un abrazo grande.",
          "b": "Gracias Berta, me hace mucha ilusión leerte.",
          "c": "Gracias Carlos, de verdad. Hablamos pronto."}


def _items(claves="abc"):
    return [{"para": "Persona " + k.upper(), "url": URL[k], "texto": TEXTOS[k]} for k in claves]


def _batch(clave="a", url=None, texto=None, tab=7, enviar="key", clic=False, extra=None, tab_type=None):
    acts = [{"name": "navigate", "input": {"url": url or URL[clave], "tabId": tab}}]
    if clic:
        acts.append({"name": "computer", "input": {"action": "left_click", "coordinate": [10, 20], "tabId": tab}})
    acts.append({"name": "computer", "input": {"action": "type", "text": texto if texto is not None else TEXTOS[clave],
                                               "tabId": tab if tab_type is None else tab_type}})
    if enviar == "key":
        acts.append({"name": "computer", "input": {"action": "key", "text": "Return", "tabId": tab}})
    elif enviar == "clic":
        acts.append({"name": "computer", "input": {"action": "left_click", "coordinate": [90, 90], "tabId": tab}})
    acts.extend(extra or [])
    return {"actions": acts}


class _LoteBase(_Base):

    def setUp(self):
        super().setUp()
        self._viejo = os.environ.get("BTP_STATE_DIR")
        os.environ["BTP_STATE_DIR"] = self.tmp          # para P/L en proceso (ruta del permiso)
        P.CLAVE_TEST = CLAVE.encode()

    def tearDown(self):
        P.CLAVE_TEST = None
        if self._viejo is None:
            os.environ.pop("BTP_STATE_DIR", None)
        else:
            os.environ["BTP_STATE_DIR"] = self._viejo

    def _asistente(self, texto):
        self._linea({"type": "assistant", "isSidechain": False,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": texto}]}})

    def _log(self):
        try:
            with open(os.path.join(self.tmp, "salida_guard.jsonl"), encoding="utf-8") as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []

    def _permitidos(self):
        return [r for r in self._log() if r["resultado"] == "lote_permitido"]

    def _lote(self):
        return os.path.join(self.tmp, "ok_envio", self.sesion + ".lote")

    def _abrir(self, items=None, orden="envíalos", manifiesto=None):
        """Presenta el lote, ella da su orden y corre el hook. Devuelve la salida del hook."""
        self._asistente(manifiesto if manifiesto is not None else L.render(items or _items()))
        pid = self._humano(orden)
        return self._emitir(orden, pid)

    def _tipea(self, entrada, tool=BATCH):
        return self._salida(tool, entrada)


# ═══ 0 · Lo de hoy no cambia ═════════════════════════════════════════════════════════════════
class SinLoteNadaCambia(_LoteBase):

    def test_canario_un_batch_que_teclea_en_bypass_hoy_no_se_frena(self):
        """Fija el punto de partida: en bypass un clic solo AVISA. Si esto cambiara sin lote, el
        resto de pruebas no mediría lo que creen medir."""
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(self._permitidos(), [])
        self.assertEqual([r["resultado"] for r in self._log()], ["avisado"])

    def test_orden_sin_manifiesto_abre_el_permiso_de_siempre(self):
        pid = self._humano("envíalo a doctora@hospital.example")
        r = self._emitir("envíalo a doctora@hospital.example", pid)
        self.assertTrue(os.path.exists(self._token()))
        self.assertFalse(os.path.exists(self._lote()))
        self.assertNotIn("LOTE", r.stdout)


# ═══ 1 · El camino feliz ═════════════════════════════════════════════════════════════════════
class ElLoteSale(_LoteBase):

    def test_n_items_con_una_sola_orden_cada_uno_una_vez(self):
        r = self._abrir()
        self.assertTrue(os.path.exists(self._lote()), r.stdout + r.stderr)
        self.assertIn("LOTE de 3", r.stdout)
        self.assertEqual(self._tipea(_batch("a")), None)
        self.assertEqual(self._tipea(_batch("c")), None)
        self.assertEqual(len(self._permitidos()), 2)
        # el b sigue pendiente y el a ya no
        self.assertEqual(self._tipea(_batch("a")), "deny")
        self.assertEqual(self._tipea(_batch("b")), None)
        self.assertEqual(len(self._permitidos()), 3)

    def test_agotado_el_lote_se_cierra_y_queda_en_el_libro(self):
        self._abrir(_items("ab"))
        self._tipea(_batch("a"))
        self._tipea(_batch("b"))
        self.assertFalse(os.path.exists(self._lote()))
        with open(os.path.join(self.tmp, "ok_envio_usados.jsonl"), encoding="utf-8") as f:
            self.assertIn("lote", f.read())

    def test_el_lote_no_abre_el_permiso_de_un_solo_uso(self):
        """Correo, Bash y gh siguen cerrados: este permiso es solo de lote."""
        self._abrir()
        self.assertFalse(os.path.exists(self._token()))
        self.assertEqual(self._enviar(), "deny")
        self.assertEqual(self._salida("Bash", {"command": "gh pr merge 224 --match-head-commit " + "a" * 40}),
                         "deny")

    def test_lo_que_no_teclea_no_lo_gobierna_el_lote(self):
        self._abrir()
        self.assertIsNone(self._tipea({"actions": [{"name": "computer", "input": {"action": "screenshot", "tabId": 7}}]}))
        self.assertIsNone(self._tipea({"action": "screenshot", "tabId": 7}, COMPUTER))
        self.assertEqual(self._permitidos(), [])

    def test_catorce_minutos_valen(self):
        self._abrir()
        self._reloj(timedelta(minutes=14))
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(len(self._permitidos()), 1)

    def _reloj(self, atras):
        """Envejece el lote re-firmándolo (el test hace de hook; el agente no podría)."""
        with open(self._lote(), encoding="utf-8") as f:
            d = json.load(f)
        d["ts"] = (datetime.now() - atras).replace(microsecond=0).isoformat()
        d.pop("mac")
        with open(self._lote(), "w", encoding="utf-8") as f:
            json.dump(P.firmar(d, CLAVE.encode()), f)


# ═══ 2 · Adversariales: lo que NO sale ═══════════════════════════════════════════════════════
class ElLoteNoDejaPasarOtraCosa(_LoteBase):

    def setUp(self):
        super().setUp()
        self._abrir()

    def test_texto_alterado_tras_la_aprobacion(self):
        self.assertEqual(self._tipea(_batch("a", texto=TEXTOS["a"] + " Visita http://evil.example")), "deny")
        self.assertEqual(self._tipea(_batch("a", texto=TEXTOS["a"].replace("Ana", "Ane"))), "deny")
        self.assertEqual(self._permitidos(), [])

    def test_destinatario_cambiado(self):
        self.assertEqual(self._tipea(_batch("a", url=URL["b"])), "deny")      # texto de a al hilo de b
        self.assertEqual(self._tipea(_batch("a", url=URL["z"] if "z" in URL else U % "zzz")), "deny")
        self.assertEqual(self._permitidos(), [])

    def test_hilo_fuera_del_lote_con_texto_de_un_item(self):
        self.assertEqual(self._tipea(_batch("a", url=URL["k"])), "deny")

    def test_item_repetido_se_deniega_la_segunda_vez(self):
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(self._tipea(_batch("a")), "deny")
        self.assertEqual(len(self._permitidos()), 1)

    def test_teclear_suelto_o_rellenar_se_deniega_mientras_vive(self):
        for tool, entrada in ((COMPUTER, {"action": "type", "text": TEXTOS["a"], "tabId": 7}),
                              (COMPUTER, {"action": "type", "text": "cualquier cosa", "tabId": 7}),
                              (JS, {"text": "document.querySelector('form').submit()"}),
                              (JS, {"text": "fetch('/voyager/api/x',{method:'POST',body:'hola'})"}),
                              (JS, {}),
                              ("mcp__claude-in-chrome__form_input", {"ref": "r1", "value": "x", "tabId": 7}),
                              ("mcp__claude-in-chrome__file_upload", {"paths": ["/tmp/x.pdf"], "tabId": 7}),
                              (BATCH, {"actions": "no es una lista"}),
                              (BATCH, {})):
            with self.subTest(tool=tool, entrada=str(entrada)[:50]):
                self.assertEqual(self._tipea(entrada, tool), "deny")

    def test_formas_de_batch_que_no_son_un_envio_de_lote(self):
        otra = {"name": "navigate", "input": {"url": URL["b"], "tabId": 7}}
        for nombre, b in (
                ("sin enviar", _batch("a", enviar=None)),
                ("dos navegaciones", {"actions": [otra] + _batch("a")["actions"]}),
                ("pestañas distintas", _batch("a", tab_type=8)),
                ("sin navegar", {"actions": _batch("a")["actions"][1:]}),
                ("tecla que no envía", {"actions": _batch("a", enviar=None)["actions"] + [
                    {"name": "computer", "input": {"action": "key", "text": "Tab", "tabId": 7}}]}),
                ("un clic antes de teclear (cambia de hilo)", _batch("a", clic=True)),
                ("clic de enviar en vez de Enter", _batch("a", enviar="clic")),
                ("JS dentro", _batch("a", extra=[{"name": "javascript_tool", "input": {"text": "1+1"}}])),
                ("tecleo doble", _batch("a", extra=[{"name": "computer", "input": {"action": "type", "text": "x", "tabId": 7}}])),
                ("rellenar", _batch("a", extra=[{"name": "form_input", "input": {"ref": "r", "value": "v", "tabId": 7}}]))):
            with self.subTest(nombre):
                self.assertEqual(self._tipea(b), "deny")
        self.assertEqual(self._permitidos(), [])

    def test_type_o_navigate_sin_tabid(self):
        a = _batch("a")["actions"]
        for nombre, acts in (
                ("type sin tabId", [a[0], {"name": "computer", "input": {"action": "type", "text": TEXTOS["a"]}}, a[2]]),
                ("navigate sin tabId", [{"name": "navigate", "input": {"url": URL["a"]}}, a[1], a[2]]),
                ("ninguna acción con tabId", [{"name": "navigate", "input": {"url": URL["a"]}},
                                              {"name": "computer", "input": {"action": "type", "text": TEXTOS["a"]}},
                                              {"name": "computer", "input": {"action": "key", "text": "Return"}}]),
                ("Enter sin tabId", [a[0], a[1], {"name": "computer", "input": {"action": "key", "text": "Return"}}]),
                ("tabId no numérico", [a[0], {"name": "computer", "input": {"action": "type", "text": TEXTOS["a"],
                                                                            "tabId": "7"}}, a[2]])):
            with self.subTest(nombre):
                self.assertEqual(self._tipea({"actions": acts}), "deny")
        # una captura sin tabId (neutra) no estorba
        cap = {"name": "computer", "input": {"action": "screenshot"}}
        self.assertIsNone(self._tipea({"actions": [cap] + a}))
        self.assertEqual(len(self._permitidos()), 1)

    def test_texto_en_nfd_casa_con_el_manifiesto_en_nfc(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", TEXTOS["b"] + " ¿Qué tal estás? Canción.")
        self.setUp()
        self._abrir([{"para": "Berta", "url": URL["b"], "texto": TEXTOS["b"] + " ¿Qué tal estás? Canción."}])
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        self.assertIsNone(self._tipea(_batch("b", texto=nfd)))
        self.assertEqual(len(self._permitidos()), 1)

    def test_enter_con_campos_extra_o_captura_en_otra_pestana(self):
        a = _batch("a")["actions"]
        for nombre, acts in (
                ("Enter con repeat", [a[0], a[1], {"name": "computer", "input": {"action": "key", "text": "Return",
                                                                                "tabId": 7, "repeat": 5}}]),
                ("Enter con modifiers", [a[0], a[1], {"name": "computer", "input": {"action": "key", "text": "Return",
                                                                                   "tabId": 7, "modifiers": "shift"}}]),
                ("captura en otra pestaña", [{"name": "computer", "input": {"action": "screenshot", "tabId": 8}}] + a)):
            with self.subTest(nombre):
                self.assertEqual(self._tipea({"actions": acts}), "deny")
        ok = a[:2] + [{"name": "computer", "input": {"action": "key", "text": "Return", "tabId": 7,
                                                      "action_summary": "Envía el mensaje"}}]
        self.assertIsNone(self._tipea({"actions": ok}))
        self.assertEqual(len(self._permitidos()), 1)

    def test_un_manifiesto_nuevo_despues_de_su_orden_no_cuenta(self):
        """Lo que vale es lo que ella tenía delante, no lo que yo escriba después."""
        self._asistente(L.render([{"para": "X", "url": URL["d"], "texto": "Hola, mensaje nuevo sin leer."}]))
        self.assertEqual(self._tipea(_batch("d", texto="Hola, mensaje nuevo sin leer.")), "deny")

    def test_un_mensaje_suyo_posterior_cancela_el_lote(self):
        self._humano("espera, déjame ver una cosa")
        self.assertIsNone(self._tipea(_batch("a")))          # vuelve a lo de siempre (avisado), sin lote
        self.assertEqual(self._permitidos(), [])
        self.assertFalse(os.path.exists(self._lote()))

    def test_lote_caducado(self):
        _ElLote = ElLoteSale
        _ElLote._reloj(self, timedelta(minutes=16))
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(self._permitidos(), [])
        self.assertFalse(os.path.exists(self._lote()))

    def test_fichero_de_lote_escrito_a_mano_no_vale(self):
        os.remove(self._lote())
        os.makedirs(os.path.dirname(self._lote()), exist_ok=True)
        forjado = {"tipo": "lote", "session_id": self.sesion, "prompt_id": "x", "n": 1, "gastados": [],
                   "ts": datetime.now().replace(microsecond=0).isoformat(), "mac": "0" * 64,
                   "transcript_path": self.transcript, "hash_prompt": "h"}
        with open(self._lote(), "w", encoding="utf-8") as f:
            json.dump(forjado, f)
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(self._permitidos(), [])
        self.assertFalse(os.path.exists(self._lote()), "el forjado se borra")

    def test_lote_firmado_con_otra_clave_no_vale(self):
        with open(self._lote(), encoding="utf-8") as f:
            d = json.load(f)
        d.pop("mac")
        with open(self._lote(), "w", encoding="utf-8") as f:
            json.dump(P.firmar(d, b"b" * 32), f)
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(self._permitidos(), [])

    def test_gastados_retocados_a_mano_rompen_la_firma(self):
        self._tipea(_batch("a"))
        with open(self._lote(), encoding="utf-8") as f:
            d = json.load(f)
        d["gastados"] = []                                   # intento de «resucitar» el ítem a
        with open(self._lote(), "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(len(self._permitidos()), 1)

    def test_escribir_el_lote_a_mano_lo_deniega_el_muro(self):
        ruta = os.path.join(CASA, "tools", "state", "ok_envio", "x.lote")
        self.assertEqual(self._salida("Write", {"file_path": ruta, "content": "{}"}), "deny")
        self.assertEqual(self._salida("Bash", {"command": "echo '{}' > tools/state/ok_envio/x.lote"}), "deny")


# ═══ 3 · El lote no se abre cuando no debe ═══════════════════════════════════════════════════
class ElLoteNoSeAbre(_LoteBase):

    def test_once_items(self):
        bloque = "LOTE DE ENVÍO (11)\n" + "\n".join(
            "[%d] Para: P%d | %s | huella %s\n```texto\n%s\n```" % (
                i, i, URL[c], L.huella(URL[c], "Hola %d, gracias." % i)[:8], "Hola %d, gracias." % i)
            for i, c in enumerate("abcdefghijk", 1)) + "\nFIN DEL LOTE"
        r = self._abrir(manifiesto=bloque)
        self.assertFalse(os.path.exists(self._lote()))
        self.assertIn("máximo es 10", r.stdout)
        with self.assertRaises(ValueError):
            L.render([{"para": "P", "url": URL[c], "texto": "Hola %d." % i} for i, c in enumerate("abcdefghijk")])

    def test_diez_si_entran(self):
        items = [{"para": "P" + c, "url": URL[c], "texto": "Gracias %s, un abrazo." % c} for c in "abcdefghij"]
        self._abrir(items)
        self.assertTrue(os.path.exists(self._lote()))

    def test_manifiesto_solo_en_una_herramienta_o_nota_mia_no_en_un_mensaje(self):
        """Un bloque dentro de un tool_use no es lo que ella leyó."""
        self._herramienta("Bash", {"command": "cat <<'X'\n" + L.render(_items()) + "\nX"})
        pid = self._humano("envíalos")
        self._emitir("envíalos", pid)
        self.assertFalse(os.path.exists(self._lote()))

    def test_manifiesto_de_un_subagente_no_es_lo_que_ella_leyo(self):
        """isSidechain distinto de False: lo escribió un subagente, ella no lo tenía delante."""
        self._linea({"type": "assistant", "isSidechain": True,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": L.render(_items())}]}})
        pid = self._humano("envíalos")
        self._emitir("envíalos", pid)
        self.assertFalse(os.path.exists(self._lote()))

    def test_sin_la_clave_issidechain_el_lote_no_se_abre(self):
        """Estricto en el lote (código nuevo): una línea sin `isSidechain` no cuenta como principal."""
        self._linea({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": L.render(_items())}]}})
        pid = self._humano("envíalos")
        self._emitir("envíalos", pid)
        self.assertFalse(os.path.exists(self._lote()))

    def test_contexto_del_permiso_normal_solo_cambia_con_lineas_laterales(self):
        """El permiso de un solo uso: la línea principal se lee igual; la lateral ya no cuenta."""
        self._asistente("PRINCIPAL aquí")
        self._linea({"type": "assistant", "isSidechain": True,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "LATERAL aquí"}]}})
        self._herramienta("mcp__x__create_draft", {"to": ["a@b.example"], "body": "principal"})
        pid = self._humano("envíalo a a@b.example")
        self._emitir("envíalo a a@b.example", pid)
        with open(self._token(), encoding="utf-8") as f:
            d = json.load(f)
        ok, _m, ctx = P.contexto(d)
        self.assertTrue(ok)
        self.assertEqual(ctx["visto_textos"], ["PRINCIPAL aquí"])

    def test_un_borrador_de_una_linea_lateral_sigue_contando_como_cambio(self):
        """El filtro lateral es solo para TEXTOS: un update_draft de un subagente tras su OK frena."""
        self._herramienta(GMAIL + "create_draft", {"to": ["doctora@hospital.example"], "draftId": "D1", "body": "uno"})
        self._ordenar("envíalo a doctora@hospital.example")
        self._linea({"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_x", "name": GMAIL + "update_draft",
             "input": {"to": ["doctora@hospital.example"], "draftId": "D1", "body": "dos"}}]}})
        self.assertEqual(self._enviar({"to": ["doctora@hospital.example"], "draftId": "D1", "body": "uno"}), "deny")

    def test_dos_manifiestos_delante(self):
        self._asistente(L.render(_items("a")))
        r = self._abrir(_items("bc"))
        self.assertFalse(os.path.exists(self._lote()))
        self.assertIn("2 manifiestos", r.stdout)

    def test_orden_que_elige_cambia_o_espera_no_abre_lote(self):
        for orden in ("envía el 2", "envíalos pero cambia el 3", "envíalos sin el segundo",
                      "solo envía el de Ana", "envíalos mañana", "envíalos todos menos Berta",
                      "Envía el de Persona A", "envíalos a Persona A y Persona B",
                      "envíalos cuando te diga", "envíalos todavía no", "envía los de ayer",
                      "mándalos aún no", "envíalos?", "¿envíalos?", "envíalos, ¿no?",
                      "si quieres, envíalos", "envíalos si te parece bien", "envíalos a Pedro",
                      "envíalos a Men", "envíalos, el resto no", "envíalos, los demás no",
                      "envíalos y el resto no", "envíalos y para", "envíalos y cierra sesión",
                      "mándalos y publica el post", "envíalos a ver", "no envíalos", "envíalos no",
                      "sí", "ok", "dale", "envíalos 2 y 3", "envíalos todos ya ahora", "envíalos, por favor y gracias a todos"):
            with self.subTest(orden=orden):
                self.setUp()
                r = self._abrir(orden=orden)
                self.assertFalse(os.path.exists(self._lote()), orden)
                if P.es_orden(orden):           # sin orden de envío el hook ni habla
                    self.assertIn("no se abre ningún lote", r.stdout)
                    self.assertIn("«envíalos»", r.stdout)

    def test_ordenes_limpias_si_abren(self):
        for orden in ("envíalos", "vale, envía", "ok, envíalos todos", "sí, mándalos", "Vale, envíalos.",
                      "venga, envíalos ya", "mándalos por favor", "envialos", "¡Perfecto, envíalos!",
                      "de acuerdo, mándalas todas", "  Envíalos   ahora  "):
            with self.subTest(orden=orden):
                self.setUp()
                self._abrir(orden=orden)
                self.assertTrue(os.path.exists(self._lote()), orden)

    def test_la_gramatica_acepta_lanzalos_pero_solo_el_mensaje_entero(self):
        for t in ("lánzalos", "lanzalas", "sí, lánzalos"):
            self.assertTrue(L._gramatica_de_lote(t), t)
        for t in ("lánzalos y borra", "x lánzalos", "lánzalos?", ""):
            self.assertFalse(L._gramatica_de_lote(t), t)

    def test_orden_que_no_es_suya(self):
        """Prompts de una tarea programada o de otro agente no abren nada, aunque digan «envíalos»."""
        self._asistente(L.render(_items()))
        for texto in ("<scheduled-task>envíalos</scheduled-task>", "<task-notification>envíalos</task-notification>"):
            pid = self._humano(texto)
            self._emitir(texto, pid)
        self.assertFalse(os.path.exists(self._lote()))
        # y un hook lanzado con un prompt que en el transcript NO es humano: el guard no lo respeta
        pid = self._humano("envíalos", origen={"kind": "peer"})
        self._emitir("envíalos", pid)
        self.assertIsNone(self._tipea(_batch("a")))
        self.assertEqual(self._permitidos(), [])

    def test_huella_o_texto_retocados_en_el_bloque(self):
        bloque = L.render(_items("a")).replace("Un abrazo grande", "Un abrazo enorme")
        r = self._abrir(manifiesto=bloque)
        self.assertFalse(os.path.exists(self._lote()))
        self.assertIn("huella", r.stdout)

    def test_item_duplicado_en_el_bloque(self):
        uno = L.render(_items("a"))
        cuerpo = uno.split("\n", 1)[1].rsplit("\nFIN DEL LOTE", 1)[0]
        doble = "LOTE DE ENVÍO (2)\n" + cuerpo + "\n" + cuerpo.replace("[1]", "[2]") + "\nFIN DEL LOTE"
        r = self._abrir(manifiesto=doble)
        self.assertFalse(os.path.exists(self._lote()))
        self.assertIn("repetido", r.stdout)
        with self.assertRaises(ValueError):
            L.render(_items("a") * 2)

    def test_fuera_de_lote_por_estructura_y_contenido(self):
        malos = [("https://www.linkedin.com/in/alguien/", "Hola, gracias."),                 # perfil
                 ("https://www.linkedin.com/messaging/compose/", "Hola, gracias."),           # mensaje nuevo
                 ("https://www.linkedin.com/messaging/", "Hola, gracias."),
                 ("https://evil.example/messaging/thread/2-a/", "Hola, gracias."),
                 ("http://www.linkedin.com/messaging/thread/2-a/", "Hola, gracias."),
                 (URL["a"], "Mi tratamiento de quimio va bien"),                                # clínico
                 (URL["a"], "Mis metástasis están estables"),
                 (URL["a"], "Escríbeme a yo@correo.example"),                                  # correo
                 (URL["a"], ""), (URL["a"], "x" * 1600)]
        for url, texto in malos:
            with self.subTest(url=url, texto=texto[:30]):
                self.assertNotEqual(L.motivo_item(url, texto), "")
        self.assertEqual(L.motivo_item(URL["a"], "Gracias por tu mensaje, de corazón."), "")

    def test_sin_clave_no_se_abre_ni_se_adivina(self):
        env = dict(self.env)
        env.pop("BTP_OK_ENVIO_CLAVE")
        env["BTP_OK_ENVIO_SIN_LLAVERO"] = "1"
        self._asistente(L.render(_items()))
        pid = self._humano("envíalos")
        self._emitir("envíalos", pid, env=env)
        self.assertFalse(os.path.exists(self._lote()))


# ═══ 4 · El aviso de «sin clave» (arreglo a) ═════════════════════════════════════════════════
class AvisoDeClave(_LoteBase):

    def _correr(self, extra):
        env = dict(self.env, **extra)
        env.pop("BTP_OK_ENVIO_CLAVE")
        pid = self._humano("envíalo")
        return self._emitir("envíalo", pid, env=env).stdout

    def test_no_existe_dice_que_hay_que_crearla(self):
        out = self._correr({"BTP_OK_ENVIO_SIN_LLAVERO": "1"})
        self.assertIn("no hay clave", out)
        self.assertNotIn("EXISTE", out)

    def test_existe_pero_no_se_puede_leer_lo_dice_y_no_manda_a_crearla(self):
        out = self._correr({"BTP_OK_ENVIO_LLAVERO_ILEGIBLE": "1"})
        self.assertIn("EXISTE", out)
        self.assertIn("no la crees", out)
        self.assertNotIn("no hay clave de firma", out)

    def _rc(self, codigo, out=""):
        return mock.Mock(returncode=codigo, stdout=out, stderr="")

    def test_estado_del_llavero_por_codigo_de_salida(self):
        for rc, out, esperado in ((0, "abc\n", ("abc", "ok")), (44, "", (None, "no-existe")),
                                  (36, "", (None, "ilegible")), (1, "", (None, "ilegible")),
                                  (0, "", (None, "ilegible"))):
            with self.subTest(rc=rc):
                with mock.patch.object(P.subprocess, "run", return_value=self._rc(rc, out)):
                    self.assertEqual(P._llavero_estado(), esperado)
        with mock.patch.object(P.subprocess, "run", side_effect=subprocess.TimeoutExpired("security", 5)):
            self.assertEqual(P._llavero_estado(), (None, "ilegible"))

    def test_no_se_crea_una_clave_que_existe_pero_no_se_lee(self):
        P.CLAVE_TEST = None
        llamadas = []

        def falso(cmd, **kw):
            llamadas.append(cmd[1])
            return self._rc(36)
        with mock.patch.object(P.subprocess, "run", side_effect=falso), \
                mock.patch.object(P, "_state_aislado", return_value=False):
            k, est = P.clave_estado(crear=True)
        self.assertEqual((k, est), (None, "ilegible"))
        self.assertNotIn("add-generic-password", llamadas)

    def test_si_no_existe_si_se_crea(self):
        P.CLAVE_TEST = None
        llamadas = []

        def falso(cmd, **kw):
            llamadas.append(cmd[1])
            if cmd[1] == "add-generic-password":
                return self._rc(0)
            return self._rc(44) if llamadas.count("find-generic-password") == 1 else self._rc(0, "k" * 64)
        with mock.patch.object(P.subprocess, "run", side_effect=falso), \
                mock.patch.object(P, "_state_aislado", return_value=False):
            k, est = P.clave_estado(crear=True)
        self.assertEqual(est, "ok")
        self.assertIn("add-generic-password", llamadas)

    def test_el_permiso_normal_sigue_caducando_a_los_10_minutos(self):
        """Subir el lote a 15 no puede alargar el permiso de siempre."""
        self._ordenar("envíalo a doctora@hospital.example")
        with open(self._token(), encoding="utf-8") as f:
            d = json.load(f)
        d["ts"] = (datetime.now() - timedelta(minutes=11)).replace(microsecond=0).isoformat()
        d.pop("mac")
        with open(self._token(), "w", encoding="utf-8") as f:
            json.dump(P.firmar(d, CLAVE.encode()), f)
        self.assertEqual(self._enviar({"to": ["doctora@hospital.example"], "body": "b"}), "deny")


if __name__ == "__main__":
    unittest.main(verbosity=2)
