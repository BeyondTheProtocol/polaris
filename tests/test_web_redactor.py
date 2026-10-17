#!/usr/bin/env python3
"""test_web_redactor.py — el redactor escribe solo lo permitido, nunca fusiona y no insiste.

POR QUÉ EXISTE (17-oct-2026, plan «web al día»). Este fichero convierte hechos en un PR que
espera el «sí» de {{TITULAR}}. Lo que no puede pasar:
  · que toque algo fuera de su lista (hero, navegación, equipo, prensa);
  · que invente un estado sin plantilla aprobada, una cifra o un tag;
  · que abra un segundo PR con otro vivo (invalida su «sí» sellado);
  · que le recuerde en bucle (`feedback-todo-aviso-a-titular-necesita-anti-spam`).
Nada aquí habla con GitHub, con Claude ni con Telegram: todo va inyectado.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import web_redactor as R  # noqa: E402

HOY = datetime.date(2026, 10, 17)

CIENCIA_ES = """panelRows:
  - component: Exoma (WES)
    status: 'Material en el laboratorio'
    statusTone: curso
    implication: 'Base'
  - component: Fosfoproteómica
    status: 'No posible hoy'
    statusTone: 'no'
"""
CIENCIA_EN = CIENCIA_ES.replace("Exoma (WES)", "Exome (WES)").replace(
    "Material en el laboratorio", "Material at the lab").replace("Fosfoproteómica", "Phosphoproteomics") \
    .replace("No posible hoy", "Not possible today")
TL_ES = """entries:

  - date: '7 oct 2026'
    tag: Ensayos clínicos
    title: Empiezo el ensayo
    description: >
      Ya he empezado el tratamiento del ensayo.
    highlight: false

  - date: '9 sep 2026'
    tag: Molecular
    title: Otra
    description: >
      Algo.
    highlight: false
"""
TL_EN = TL_ES.replace("'7 oct 2026'", "'Oct 7, 2026'").replace("Ensayos clínicos", "Clinical trials") \
    .replace("'9 sep 2026'", "'Sep 9, 2026'")
TEXTOS = {"content/es/science.yml": CIENCIA_ES, "content/en/science.yml": CIENCIA_EN,
          "content/es/timeline.yml": TL_ES, "content/en/timeline.yml": TL_EN}
MAPA = {"afirmaciones": [{
    "id": "ciencia-wes", "componente": {"es": "Exoma (WES)", "en": "Exome (WES)"},
    "valor": {"es": "Material en el laboratorio", "en": "Material at the lab"}, "tono": "curso",
    "transiciones": {"resultados": {"es": "Primeros resultados ({mes} {año})",
                                    "en": "First results ({month} {year})", "tono": "curso"}}}]}


def hecho_a(destino="resultados", prioridad=1, clave="estado:wes"):
    return {"clave": clave, "carril": "A", "prioridad": prioridad, "estado": "encolado",
            "datos": {"id": "ciencia-wes", "destino": destino}}


def hecho_c(clave="pr:celula:280"):
    return {"clave": clave, "carril": "C", "prioridad": 3, "estado": "encolado",
            "datos": {"tipo": "pr", "ambito": "celula-a-celula",
                      "prs": [{"number": 280, "title": "feat(celula-a-celula): resultados",
                               "fecha": "2026-10-17"}]}}


ENTRADA = {"tag_es": "Molecular", "titulo_es": "Abro la página célula a célula",
           "descripcion_es": "Hemos publicado una página nueva con lo que salió del análisis.",
           "tag_en": "Molecular", "titulo_en": "The cell by cell page is live",
           "descripcion_en": "A new page with what came out of the analysis."}


def ask_fijo(*respuestas):
    cola = list(respuestas)
    return lambda _prompt: json.dumps(cola.pop(0)) if cola else ""


class EstadosDeCiencia(unittest.TestCase):
    def test_plantilla_con_fecha(self):
        t = R.texto_transicion(MAPA["afirmaciones"][0], "resultados", HOY)
        self.assertEqual(t, {"es": "Primeros resultados (octubre 2026)",
                             "en": "First results (October 2026)", "tono": "curso"})

    def test_sin_plantilla_no_se_inventa(self):
        self.assertIsNone(R.texto_transicion(MAPA["afirmaciones"][0], "hecho", HOY))
        plan = R.preparar([hecho_a("hecho")], MAPA, TEXTOS, HOY)
        self.assertEqual(plan["ficheros"], {})
        self.assertEqual(plan["fuera"][0][1], "sin_plantilla")

    def test_solo_cambia_su_bloque(self):
        nuevo = R.aplicar_estado(CIENCIA_ES, "Exoma (WES)", "Primeros resultados (octubre 2026)", "curso")
        self.assertIn("status: 'Primeros resultados (octubre 2026)'", nuevo)
        self.assertIn("status: 'No posible hoy'", nuevo)
        self.assertIn("implication: 'Base'", nuevo)
        with self.assertRaises(ValueError):
            R.aplicar_estado(CIENCIA_ES, "No existe", "x", "curso")

    def test_preparar_cambia_es_y_en(self):
        plan = R.preparar([hecho_a()], MAPA, TEXTOS, HOY)
        self.assertEqual(sorted(plan["ficheros"]), ["content/en/science.yml", "content/es/science.yml"])
        self.assertIn("First results (October 2026)", plan["ficheros"]["content/en/science.yml"])
        self.assertEqual(plan["cambios"][0]["antes"], "Material en el laboratorio")


class EntradasDeCronologia(unittest.TestCase):
    def test_entrada_nueva_solo_se_anade(self):
        plan = R.preparar([hecho_c()], MAPA, TEXTOS, HOY, _ask=ask_fijo(ENTRADA))
        es = plan["ficheros"]["content/es/timeline.yml"]
        for viejo in TL_ES.split("\n\n")[1:]:                       # lo que había sigue igual
            self.assertIn(viejo.strip("\n"), es)
        self.assertTrue(es.startswith("entries:\n\n  - date: '7 oct 2026'"))
        for trozo in ("Empiezo el ensayo", "title: Abro la página célula a célula",
                      "date: '17 oct 2026'", "highlight: false"):
            self.assertIn(trozo, es)
        self.assertIn("date: 'Oct 17, 2026'", plan["ficheros"]["content/en/timeline.yml"])
        self.assertNotIn("highlight: true", es)

    def test_tag_inventado_se_reintenta_y_si_no_fuera(self):
        malo = dict(ENTRADA, tag_es="Novedades")
        plan = R.preparar([hecho_c()], MAPA, TEXTOS, HOY, _ask=ask_fijo(malo, ENTRADA))
        self.assertIn("content/es/timeline.yml", plan["ficheros"])
        plan = R.preparar([hecho_c()], MAPA, TEXTOS, HOY, _ask=ask_fijo(malo, malo))
        self.assertEqual(plan["ficheros"], {})
        self.assertIn("tag inventado", plan["fuera"][0][2])

    def test_lint_que_falla_dos_veces_no_sale(self):
        clinico = dict(ENTRADA, descripcion_es="NY-BR-1 aparece en el 58% de las células tumorales.")
        plan = R.preparar([hecho_c()], MAPA, TEXTOS, HOY, _ask=ask_fijo(clinico, clinico))
        self.assertEqual(plan["ficheros"], {})
        self.assertIn("web_lint", plan["fuera"][0][2])

    def test_un_informe_no_se_redacta_solo(self):
        h = {"clave": "informe:x", "carril": "C", "prioridad": 3, "estado": "encolado",
             "datos": {"tipo": "informe", "fecha": "2026-10-15", "carpeta": "AP"}}
        plan = R.preparar([h], MAPA, TEXTOS, HOY, _ask=ask_fijo(ENTRADA))
        self.assertEqual(plan["fuera"][0][1], "necesita_humano")


class Limites(unittest.TestCase):
    def test_tope_de_tres(self):
        hs = [hecho_c("pr:%d" % i) for i in range(5)]
        plan = R.preparar(hs, MAPA, TEXTOS, HOY, _ask=ask_fijo(*[ENTRADA] * 5))
        self.assertEqual(len(plan["hechos"]), 3)

    def test_lista_cerrada_de_ficheros(self):
        self.assertEqual(R.fuera_de_lista(["content/es/science.yml", "app/data/caso.json"]), [])
        self.assertEqual(R.fuera_de_lista(["i18n/locales/es.json", "content/es/press.yml"]),
                         ["i18n/locales/es.json", "content/es/press.yml"])


class Ciclo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.creados, self.encargos = [], []

    def vistos(self, modo, **hechos):
        with open(os.path.join(self.dir, "al_dia_vistos_%s.json" % modo), "w") as f:
            json.dump({k: dict(v) for k, v in hechos.items()}, f)

    def lee(self, modo):
        with open(os.path.join(self.dir, "al_dia_vistos_%s.json" % modo)) as f:
            return json.load(f)

    def corre(self, modo, hoy=HOY, estado_pr="OPEN"):
        return R.redactar(modo, hoy, "/no/repo", dir_=self.dir, _textos=TEXTOS, _mapa=MAPA,
                          _ask=ask_fijo(ENTRADA),
                          _crear=lambda p, *_: self.creados.append(p) or 300 + len(self.creados),
                          _ci=lambda n: ("verde", {}), _encargar=self.encargos.append,
                          _estado_pr=lambda n: estado_pr, _cambios=lambda n: [])

    def test_sombra_solo_apunta(self):
        self.vistos("sombra", **{"estado:wes": hecho_a()})
        self.corre("sombra")
        self.assertEqual(self.creados, [])
        self.assertEqual(self.encargos, [])
        self.assertEqual(self.lee("sombra")["estado:wes"]["estado"], "propuesto_sombra")
        with open(os.path.join(self.dir, "al_dia_propuestas_sombra.jsonl")) as f:
            self.assertEqual(len(f.readlines()), 1)
        self.corre("sombra")                                  # no se vuelve a proponer
        with open(os.path.join(self.dir, "al_dia_propuestas_sombra.jsonl")) as f:
            self.assertEqual(len(f.readlines()), 1)

    def test_activo_un_pr_y_no_un_segundo_con_uno_vivo(self):
        self.vistos("activo", **{"estado:wes": hecho_a()})
        self.corre("activo")
        self.assertEqual(len(self.creados), 1)
        self.assertEqual(self.encargos, [301])
        v = self.lee("activo")
        v["pr:otro"] = hecho_c("pr:otro")
        self.vistos("activo", **v)
        self.corre("activo")
        self.assertEqual(len(self.creados), 1)                # el vivo bloquea otro PR

    def test_ci_rojo_no_encarga_telegram(self):
        self.vistos("activo", **{"estado:wes": hecho_a()})
        R.redactar("activo", HOY, "/no/repo", dir_=self.dir, _textos=TEXTOS, _mapa=MAPA,
                   _crear=lambda p, *_: 400, _ci=lambda n: ("rojo", {}), _encargar=self.encargos.append,
                   _estado_pr=lambda n: "OPEN", _cambios=lambda n: [])
        self.assertEqual(self.encargos, [])

    def test_recordatorios_uno_y_otro_solo_si_es_prioridad_1(self):
        self.vistos("activo", **{"estado:wes": hecho_a()})
        self.corre("activo")
        self.encargos.clear()
        self.corre("activo", HOY + datetime.timedelta(days=1))
        self.assertEqual(self.encargos, [])
        self.corre("activo", HOY + datetime.timedelta(days=3))
        self.corre("activo", HOY + datetime.timedelta(days=4))
        self.assertEqual(self.encargos, [301])
        self.corre("activo", HOY + datetime.timedelta(days=7))
        self.corre("activo", HOY + datetime.timedelta(days=12))
        self.assertEqual(self.encargos, [301, 301])           # P1: un segundo, y ya

    def test_prioridad_3_solo_un_recordatorio(self):
        self.vistos("activo", **{"pr:c": hecho_c("pr:c")})
        self.corre("activo")
        self.encargos.clear()
        for d in (3, 7, 14):
            self.corre("activo", HOY + datetime.timedelta(days=d))
        self.assertEqual(len(self.encargos), 1)

    def test_fusionado_se_marca_publicado_y_libera(self):
        self.vistos("activo", **{"estado:wes": hecho_a()})
        self.corre("activo")
        self.corre("activo", estado_pr="MERGED")
        self.assertEqual(self.lee("activo")["estado:wes"]["estado"], "publicado")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "al_dia_pr_vivo_activo.json")))


PIEZA_A = {"clave": "estado:wes", "carril": "A", "id": "ciencia-wes",
           "es": "Primeros resultados (octubre 2026)", "en": "First results (October 2026)",
           "tono": "curso", "antes": "Material en el laboratorio"}
PIEZA_C = dict(ENTRADA, clave="pr:c", carril="C", fecha_es="17 oct 2026", fecha_en="Oct 17, 2026")


class SusCambios(unittest.TestCase):
    def test_aplica_lo_que_pide(self):
        nueva = dict(PIEZA_A, es="Primeros resultados")
        piezas, motivo = R.aplicar_sus_cambios([PIEZA_A], "quita la fecha", TEXTOS,
                                               _ask=lambda _p: json.dumps({"piezas": [nueva]}))
        self.assertEqual(motivo, "")
        self.assertEqual(piezas[0]["es"], "Primeros resultados")
        self.assertEqual(piezas[0]["antes"], "Material en el laboratorio")   # no se toca

    def test_quitar_uno(self):
        piezas, _ = R.aplicar_sus_cambios([PIEZA_A, PIEZA_C], "quita el 2", TEXTOS,
                                          _ask=lambda _p: json.dumps({"piezas": [PIEZA_A]}))
        self.assertEqual([p["clave"] for p in piezas], ["estado:wes"])

    def test_no_cuela_un_cambio_nuevo(self):
        intruso = dict(PIEZA_A, clave="estado:otro")
        piezas, motivo = R.aplicar_sus_cambios([PIEZA_A], "x", TEXTOS,
                                               _ask=lambda _p: json.dumps({"piezas": [intruso]}))
        self.assertIsNone(piezas)
        self.assertIn("no estaba", motivo)

    def test_lo_que_vuelve_pasa_el_lint_y_el_tag(self):
        clin = dict(PIEZA_C, descripcion_es="NY-BR-1 aparece en el 58% de las células tumorales.")
        self.assertIsNone(R.aplicar_sus_cambios([PIEZA_C], "x", TEXTOS,
                                                _ask=lambda _p: json.dumps({"piezas": [clin]}))[0])
        tag = dict(PIEZA_C, tag_es="Novedades")
        self.assertIsNone(R.aplicar_sus_cambios([PIEZA_C], "x", TEXTOS,
                                                _ask=lambda _p: json.dumps({"piezas": [tag]}))[0])

    def test_rehace_los_ficheros_desde_las_piezas(self):
        plan = R.plan_desde_piezas([dict(PIEZA_A, es="Primeros resultados"), PIEZA_C], MAPA, TEXTOS)
        self.assertIn("status: 'Primeros resultados'", plan["ficheros"]["content/es/science.yml"])
        self.assertIn("title: Abro la página célula a célula", plan["ficheros"]["content/es/timeline.yml"])

    def test_retira_solo_los_borradores_de_ese_pr(self):
        pend = os.path.join(tempfile.mkdtemp(), "pending")
        os.makedirs(pend)
        for nombre, pr in (("a-web-publish-1.json", 301), ("b-web-publish-2.json", 302),
                           ("c-mail-contact-3.json", 301)):
            with open(os.path.join(pend, nombre), "w") as f:
                json.dump({"sellado": {"pr": pr}}, f)
        self.assertEqual(R.retirar_borradores_web(301, _pending=pend), 1)
        self.assertEqual(sorted(os.listdir(pend)), ["b-web-publish-2.json", "c-mail-contact-3.json"])
        self.assertEqual(os.listdir(os.path.join(os.path.dirname(pend), "retirado")),
                         ["a-web-publish-1.json"])


class BucleDeCambios(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cambios_dir = os.path.join(self.dir, "cambios")
        os.makedirs(self.cambios_dir)
        self.rehechos, self.retirados, self.encargos = [], [], []
        with open(os.path.join(self.dir, "al_dia_vistos_activo.json"), "w") as f:
            json.dump({"estado:wes": dict(hecho_a(), estado="propuesto")}, f)
        with open(os.path.join(self.dir, "al_dia_pr_vivo_activo.json"), "w") as f:
            json.dump({"pr": 301, "claves": ["estado:wes"], "desde": "2026-10-17", "recordatorios": 0,
                       "ci": "verde", "vueltas": 0, "rama": "auto/web-al-dia-2026-10-17",
                       "piezas": [PIEZA_A]}, f)

    def cambio(self, texto, n=0):
        with open(os.path.join(self.cambios_dir, "301-2026-10-18T10000%d.json" % n), "w") as f:
            json.dump({"pr": 301, "texto": texto}, f)

    def corre(self, nueva_es="Primeros resultados"):
        return R.redactar("activo", HOY + datetime.timedelta(days=1), "/no/repo", dir_=self.dir,
                          _textos=TEXTOS, _mapa=MAPA,
                          _ask=lambda _p: json.dumps({"piezas": [dict(PIEZA_A, es=nueva_es)]}),
                          _crear=lambda p, r, n: self.rehechos.append((p, r, n)) or n,
                          _ci=lambda n: ("verde", {}), _encargar=self.encargos.append,
                          _estado_pr=lambda n: "OPEN", _retirar=self.retirados.append,
                          _cambios=lambda n: R.cambios_suyos(n, _dir=self.cambios_dir))

    def vivo(self):
        with open(os.path.join(self.dir, "al_dia_pr_vivo_activo.json")) as f:
            return json.load(f)

    def test_su_cambio_rehace_el_mismo_pr_y_retira_el_telegram_viejo(self):
        self.cambio("quita la fecha")
        self.corre()
        p, rama, n = self.rehechos[0]
        self.assertEqual((rama, n), ("auto/web-al-dia-2026-10-17", 301))
        self.assertIn("status: 'Primeros resultados'", p["ficheros"]["content/es/science.yml"])
        self.assertEqual(self.retirados, [301])
        self.assertEqual(self.encargos, [301])
        self.assertEqual(self.vivo()["vueltas"], 1)
        self.assertEqual(os.listdir(os.path.join(self.cambios_dir, "aplicados")),
                         ["301-2026-10-18T100000.json"])
        self.corre()                                # ya aplicado: no se rehace otra vez
        self.assertEqual(len(self.rehechos), 1)

    def test_a_la_cuarta_vuelta_no_se_aplica(self):
        v = self.vivo()
        v["vueltas"] = 3
        with open(os.path.join(self.dir, "al_dia_pr_vivo_activo.json"), "w") as f:
            json.dump(v, f)
        self.cambio("otra vez")
        notas = self.corre()
        self.assertEqual(self.rehechos, [])
        self.assertIn("buzón", notas[0])
        self.assertTrue(os.listdir(os.path.join(self.cambios_dir, "sin_aplicar")))

    def test_ci_que_se_pone_verde_despues_encarga_el_telegram(self):
        v = self.vivo()
        v["ci"] = "pendiente"
        with open(os.path.join(self.dir, "al_dia_pr_vivo_activo.json"), "w") as f:
            json.dump(v, f)
        self.corre()
        self.assertEqual(self.encargos, [301])
        self.assertEqual(self.vivo()["ci"], "verde")


class CrearPrContraGitDePega(unittest.TestCase):
    """`crear_pr` de verdad contra un `origin` local: worktree desde origin/main, escribe, commitea y
    empuja la rama `auto/`. El último paso (`gh pr create`) falla aquí a propósito: no es GitHub."""

    def git(self, *a, cwd):
        import subprocess
        subprocess.run(["git"] + list(a), cwd=cwd, check=True, capture_output=True)

    def setUp(self):
        import subprocess
        base = tempfile.mkdtemp()
        self.origen, self.repo = os.path.join(base, "origen.git"), os.path.join(base, "web")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origen], check=True)
        subprocess.run(["git", "clone", "-q", self.origen, self.repo], check=True, capture_output=True)
        for rel, txt in TEXTOS.items():
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)), exist_ok=True)
            with open(os.path.join(self.repo, rel), "w") as f:
                f.write(txt)
        self.git("add", "-A", cwd=self.repo)
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base", cwd=self.repo)
        self.git("push", "-q", "origin", "HEAD:main", cwd=self.repo)

    def test_empuja_la_rama_con_solo_lo_permitido(self):
        import subprocess
        plan = R.preparar([hecho_a()], MAPA, TEXTOS, HOY)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        viejo = dict(os.environ)
        os.environ.update(env)
        try:
            with self.assertRaises(RuntimeError) as cm:
                R.crear_pr(plan, HOY, self.repo)
        finally:
            os.environ.clear()
            os.environ.update(viejo)
        self.assertIn("gh pr create", str(cm.exception))
        r = subprocess.run(["git", "--git-dir", self.origen, "show",
                            "auto/web-al-dia-2026-10-17:content/es/science.yml"],
                           capture_output=True, text=True)
        self.assertIn("Primeros resultados (octubre 2026)", r.stdout)
        r = subprocess.run(["git", "--git-dir", self.origen, "diff", "--name-only", "main",
                            "auto/web-al-dia-2026-10-17"], capture_output=True, text=True)
        self.assertEqual(sorted(r.stdout.split()), ["content/en/science.yml", "content/es/science.yml"])
        # el worktree temporal se retira: el repo no se queda con basura
        r = subprocess.run(["git", "-C", self.repo, "worktree", "list"], capture_output=True, text=True)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
