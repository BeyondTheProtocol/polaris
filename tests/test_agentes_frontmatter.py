#!/usr/bin/env python3
"""test_agentes_frontmatter.py — que una ficha de agente no pueda romperse en silencio.

POR QUÉ EXISTE (29-jul-2026). `consejero-arquitectura` llevaba **3 días roto** y nadie se enteró:
su línea `model:` del frontmatter medía 374 caracteres porque llevaba pegado un comentario largo
explicando la decisión del híbrido Opus. La API corta el campo `model` en **256**, así que cualquier
`Task(consejero-arquitectura)` moría con *API Error 400: 'model: String should have at most 256
characters'*.

Lo grave no fue el fallo, fue la FORMA del fallo: ese agente es la **puerta (a)** de supervisión del
lazo — la lente que juzga los `adoptar-idea` que `auto-mejora` AUTO-EJECUTA. No falló ruidoso: es que
no se podía invocar. Una puerta de supervisión que no suena no se distingue de una puerta que dice
que sí a todo, y encima el sistema seguía auto-ejecutando ideas al otro lado.

La lección no va a una memoria: va aquí. Detectar no basta ([[deuda.py]] R1) — el cierre de
`consejero-arquitectura-model-256-roto` se apoya en este test.
"""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTES = os.path.join(ROOT, ".claude", "agents")

# El techo real de la API. Se comprueba sobre el VALOR de `model:`, que es lo que viaja.
MAX_MODEL = 256


def _valor_model(path):
    """Devuelve (nlinea, valor) de la línea `model:` del frontmatter, o (None, None) si no hay.

    Solo mira el frontmatter (entre los dos `---`): un `model:` en el cuerpo es prosa, no config.
    """
    with open(path, encoding="utf-8") as f:
        lineas = f.read().splitlines()
    if not lineas or lineas[0].strip() != "---":
        return None, None
    for i, linea in enumerate(lineas[1:], start=2):
        if linea.strip() == "---":
            return None, None
        m = re.match(r"^model:\s*(.*)$", linea)
        if m:
            return i, m.group(1)
    return None, None


class FrontmatterDeAgentes(unittest.TestCase):

    def test_ningun_model_pasa_del_techo_de_la_api(self):
        fichas = sorted(glob.glob(os.path.join(AGENTES, "*.md")))
        self.assertTrue(fichas, "no encuentro fichas en .claude/agents/ — ¿ruta mal?")
        rotas = []
        for path in fichas:
            n, valor = _valor_model(path)
            if valor is None:
                continue
            if len(valor) > MAX_MODEL:
                rotas.append("   ⛔ %s:%d → model de %d caracteres (techo %d)\n      empieza por: %s…"
                             % (os.path.basename(path), n, len(valor), MAX_MODEL, valor[:70]))
        if rotas:
            self.fail("%d ficha(s) de agente con el campo `model:` por encima del techo de la API. "
                      "Ese agente NO se puede invocar: `Task(...)` devuelve API Error 400 y la "
                      "llamada muere sin ruido. Saca el comentario del frontmatter y bájalo al "
                      "cuerpo:\n%s" % (len(rotas), "\n".join(rotas)))

    def test_ningun_model_lleva_comentario_pegado(self):
        """La CAUSA, no el síntoma (2-sep-2026).

        El techo de 256 caracteres solo caza los comentarios LARGOS. El 2-sep-2026 siete
        agentes — asistente (Vega), orquestador, auto-mejora, git, prensa, periodista y
        coach-colaboracion — llevaban comentarios CORTOS pegados al valor:

            model: sonnet  # OJO: el lazo 24/7 la corre en HAIKU — com.btp.asistente.plist

        Cabían de sobra en 256, así que este fichero los daba por buenos. Pero el parser no
        trata el `#` como comentario YAML: manda el string entero como nombre de modelo y la
        API responde 404 model_not_found. Vega llevaba caída sin que nadie lo supiera.

        El techo se queda (es otra forma de romperlo). Esto cierra la clase entera: el valor
        de `model:` es un nombre de modelo y nada más. Las notas van al cuerpo del fichero.
        """
        rotas = []
        for path in sorted(glob.glob(os.path.join(AGENTES, "*.md"))):
            n, valor = _valor_model(path)
            if valor is None:
                continue
            if "#" in valor:
                rotas.append("   ⛔ %s:%d → model: %s\n      el parser manda TODO eso como "
                             "nombre de modelo → API 404 model_not_found"
                             % (os.path.basename(path), n, valor[:90]))
        if rotas:
            self.fail("%d ficha(s) de agente con un comentario pegado al valor de `model:`. "
                      "Ese agente NO se puede invocar. El `#` no se interpreta como comentario: "
                      "baja la nota al cuerpo del fichero y deja en `model:` solo el nombre "
                      "del modelo.\n%s" % (len(rotas), "\n".join(rotas)))

    def test_el_agente_que_lo_provoco_sigue_sano(self):
        """Guardia específica de la regresión: `consejero-arquitectura` es la puerta (a) del lazo."""
        path = os.path.join(AGENTES, "consejero-arquitectura.md")
        self.assertTrue(os.path.exists(path), "falta la ficha de consejero-arquitectura")
        n, valor = _valor_model(path)
        self.assertIsNotNone(valor, "consejero-arquitectura se quedó sin `model:` en el frontmatter")
        self.assertLessEqual(len(valor), MAX_MODEL,
                             "vuelve a estar roto: %d caracteres en la línea %s" % (len(valor), n))


if __name__ == "__main__":
    res = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestLoader().loadTestsFromTestCase(FrontmatterDeAgentes))
    if res.wasSuccessful():
        print("✅ FRONTMATTER DE AGENTES EN VERDE (%d casos · ningún `model:` pasa de %d chars)"
              % (res.testsRun, MAX_MODEL))
    raise SystemExit(0 if res.wasSuccessful() else 1)
