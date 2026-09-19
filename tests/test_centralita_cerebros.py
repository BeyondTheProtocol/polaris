#!/usr/bin/env python3
"""test_centralita_cerebros.py — el ROSTER de la centralita y el MAPA del stack (27/7/26).

Nace de que {{TITULAR}} dijo *«ahora mismo hay 4 LLMs si tengo un montón»* y tenía razón: había 9 claves
vivas en el Llavero y solo 4 cerebros registrados. Grok y Perplexity llevaban meses fuera del router
sin que saltara nada, y OpenAI y GLM no los vigilaba nadie. Este fichero es el freno para que eso no
pueda repetirse en silencio.

Cubre:
  · CENSO — cada cerebro del registro REAL tiene cliente en disco, kind conocido y etiquetas válidas.
    Es el test que habría cazado el agujero de origen.
  · MURO — con el roster entero, lo sensible NUNCA sale a un cerebro de nube: ni promocionado por su
    perfil, ni pedido a mano con `prefer`.
  · CUERPO DE ERROR — un cliente que sale rc=0 imprimiendo «API error 429» se RELEVA, no se sirve
    como si fuera la respuesta del modelo (era el problema 6, y ahora afecta a 5 clientes).
  · PERFILES — el mapa del stack (Stack-IA-Recalibrado-2026-06-22) aplicado: cada clase de tarea cae
    en su especialista, y si el especialista no está, la cadena normal recoge.
  · NO-REGRESIÓN — sin perfil claro, el orden es exactamente el de antes de existir el mapa.

Estilo test_ia/test_borde: cada caso suma OK/fallo; exit = nº de fallos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres")
import json
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="roster_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_TEST_BATTERY"] = "1"          # ningún aviso real a {{TITULAR}} desde la batería
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "nh_a") + ":" + os.path.join(_TMP, "nh_b")
os.environ["BTP_CANARIOS"] = "CANARIO-ROSTER-4417"
_REGISTRO_REAL = os.path.join(ROOT, "tools", "peripheries.json")
os.environ["BTP_PERIPHERIES"] = os.path.join(_TMP, "peripheries.json")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ia      # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def set_registro(cerebros):
    json.dump({"cerebros": cerebros}, open(os.environ["BTP_PERIPHERIES"], "w"), ensure_ascii=False)


def registro_real():
    return (json.load(open(_REGISTRO_REAL, encoding="utf-8")) or {}).get("cerebros", [])


# ── 1 · CENSO: el registro real es coherente ────────────────────────────────────────────
KINDS = {"claude", "gemini", "cli_chain", "cli", "carril_gratis", "openai_local", "fugu"}


def test_censo():
    cerebros = registro_real()
    ok(len(cerebros) >= 9, "censo: el roster tiene los cerebros que {{TITULAR}} paga (>=9), no 4")
    nombres = [c.get("name") for c in cerebros]
    ok(len(nombres) == len(set(nombres)), "censo: sin nombres duplicados")
    for c in cerebros:
        n = c.get("name", "?")
        ok(c.get("kind") in KINDS, "censo[%s]: kind conocido (%s)" % (n, c.get("kind")))
        ok(isinstance(c.get("capability"), int) and isinstance(c.get("orden"), int),
           "censo[%s]: capability y orden son enteros" % n)
        ok(all(p in ia.PERFILES for p in (c.get("para") or [])),
           "censo[%s]: sus etiquetas 'para' existen en el mapa" % n)
        if c.get("kind") in ("cli_chain", "gemini"):
            binario = os.path.join(ROOT, "tools", c.get("bin") or (n + ".py"))
            ok(os.path.exists(binario), "censo[%s]: su cliente existe en disco (%s)"
               % (n, os.path.basename(binario)))
        if c.get("secret"):
            ok(str(c["secret"]).startswith("btp-"),
               "censo[%s]: el nombre del secreto sigue la convención del Llavero" % n)
        if c.get("solo_perfil"):
            ok(bool(c.get("para")), "censo[%s]: solo_perfil sin 'para' sería un cerebro muerto" % n)
    # Los que sirven lo CLÍNICO tienen que ser de confianza Y capaces (el freno de criticidad).
    trusted = [c for c in cerebros if c.get("trusted") and c.get("enabled")]
    ok(any(int(c.get("capability", 0)) >= 7 for c in trusted),
       "censo: queda al menos un cerebro de CONFIANZA capaz de lo crítico")


# ── 2 · MURO: el roster grande no abre ninguna puerta ───────────────────────────────────
SENSIBLE = "el informe de {{TITULAR}} {{APELLIDO}} dice HLA-A*02 y del 17p"


def test_muro():
    set_registro(registro_real())
    d = ia.por_que(SENSIBLE)
    ok(d["sensible"], "muro: el borde marca sensible el texto con PII + huella genómica")
    reg = {c["name"]: c for c in registro_real()}
    ok(all(reg.get(n, {}).get("trusted") for n in d["cadena"]),
       "muro: con el roster entero, lo sensible solo ve cerebros de CONFIANZA")
    # Aunque el perfil intente promocionar a un especialista de nube, lo sensible manda.
    d2 = ia.por_que(SENSIBLE + " busca papers con DOI", para="evidencia")
    ok(all(reg.get(n, {}).get("trusted") for n in d2["cadena"]),
       "muro: ni con el perfil 'evidencia' forzado sale a un buscador de nube")
    # Y el egress real: el borde deniega el destino de nube aunque se lo pidan a mano.
    import borde
    ok(not borde.egress_check(SENSIBLE, destino="grok", intencion="test").permitido,
       "muro: egress_check deniega el contenido sensible hacia un destino de nube")


# ── 3 · CUERPO DE ERROR: rc=0 con un error dentro no es una respuesta ───────────────────
def _cliente_falso(nombre, guion):
    """Escribe un cliente CLI de mentira que imprime `guion[modelo]` y sale con 0."""
    ruta = os.path.join(_TMP, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env python3\nimport sys\n"
                "g = %r\n"
                "m = sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else ''\n"
                "print(g.get(m, ''))\n" % guion)
    os.chmod(ruta, os.stat(ruta).st_mode | stat.S_IEXEC)
    return ruta


def test_cuerpo_error():
    for cuerpo, etq in (("OpenAI API error 429: rate limit", "429"),
                        ("Error de red: [Errno 8] nodename nor servname", "red"),
                        ("Falta la clave de GLM: Llavero (btp-glm-api).", "sin clave"),
                        ("", "vacío")):
        malo, _ = ia._cuerpo_es_error(cuerpo)
        ok(malo, "cuerpo de error detectado: %s" % etq)
    ok(not ia._cuerpo_es_error("La respuesta es 42.")[0],
       "cuerpo de error: una respuesta normal NO se confunde con un error")

    # Cadena de modelos: la punta devuelve un 429 con rc=0 → releva al estable, no sirve el error.
    _cliente_falso("falso.py", {"punta": "API error 429: too many requests", "estable": "RESPUESTA BUENA"})
    cerebro = {"name": "falso", "kind": "cli_chain", "bin": os.path.join(_TMP, "falso.py"),
               "models": ["punta", "estable"], "destino": "falso"}
    # `bin` absoluto: os.path.join con una ruta absoluta se queda con la absoluta.
    txt, _usd, fallo = ia._call_cli_chain(cerebro, "hola")
    ok(txt == "RESPUESTA BUENA" and fallo is None,
       "cadena de modelos: la punta da 429 (rc=0) → releva al estable y sirve SU respuesta")

    _cliente_falso("todo_mal.py", {"a": "API error 503", "b": "API error 429"})
    cerebro2 = dict(cerebro, name="todo_mal", bin=os.path.join(_TMP, "todo_mal.py"),
                    models=["a", "b"])
    txt2, _u2, fallo2 = ia._call_cli_chain(cerebro2, "hola")
    ok(txt2 is None and fallo2 == "limite",
       "cadena agotada por límite → fallo 'limite' (para que la centralita releve de cerebro)")


# ── 4 · PERFILES: el mapa del stack, aplicado ───────────────────────────────────────────
CASOS = (
    ("resume estos tres parrafos en una linea",            "volumen",      "nvidia-free"),
    ("busca papers sobre sesgo de publicacion",            "evidencia",    "consensus"),
    ("lo ultimo en preprints de biologia estructural",     "descubrir",    "perplexity"),
    ("que dice @contactoinpublic en X sobre esto",            "osint-x",      "grok"),
    ("hazme una landing con hero y CTA",                   "web-frontend", "glm"),
    ("arregla el traceback de tools/cola.py",              "codigo",       "claude"),
    ("explicame el ciclo del agua",                        "general",      None),
)


def test_perfiles():
    set_registro(registro_real())
    for prompt, perfil_esp, primero in CASOS:
        d = ia.por_que(prompt)
        ok(d["perfil"] == perfil_esp,
           "perfil de «%s…» = %s (salió %s)" % (prompt[:28], perfil_esp, d["perfil"]))
        if primero:
            ok(d["cadena"][:1] == [primero],
               "carril de «%s…» encabezado por %s (salió %s)"
               % (prompt[:28], primero, (d["cadena"] or ["-"])[0]))

    # El especialista PROMOCIONA, no filtra: detrás sigue la cadena entera como respaldo.
    d = ia.por_que("hazme una landing con hero y CTA")
    ok(len(d["cadena"]) > 1, "el perfil promociona pero NO deja al resto fuera (hay respaldo)")

    # Especialista caído → cae a la cadena normal sin romper ni quedarse mudo.
    sin_glm = [c for c in registro_real() if c.get("name") != "glm"]
    set_registro(sin_glm)
    d = ia.por_que("hazme una landing con hero y CTA")
    ok(d["perfil"] == "web-frontend" and d["cadena"] and d["cadena"][0] != "glm",
       "especialista ausente → sigue habiendo cadena (no se queda muda)")

    # `solo_perfil` fuera del relevo general: Perplexity no aparece en una tarea cualquiera.
    set_registro(registro_real())
    d = ia.por_que("explicame el ciclo del agua")
    ok("perplexity" not in d["cadena"] and "consensus" not in d["cadena"],
       "solo_perfil: el buscador NO entra en el relevo general")

    # `para` explícito manda sobre lo que detecte el clasificador.
    d = ia.por_que("explicame el ciclo del agua", para="descubrir")
    ok(d["cadena"][:1] == ["perplexity"], "`para` explícito manda sobre el clasificador")


# ── 5 · NO-REGRESIÓN: sin perfil, el orden es el de siempre ─────────────────────────────
def test_no_regresion():
    reg = registro_real()
    set_registro(reg)
    sin_mapa = [c.get("name") for c in ia._candidatos(reg, False)]           # como antes del mapa
    con_mapa = ia.por_que("explicame el ciclo del agua")["cadena"]
    ok(sin_mapa == con_mapa, "sin perfil claro, la cadena es EXACTAMENTE la de antes del mapa")
    # Y lo sustantivo no-sensible sigue teniendo a Gemini por delante de los nuevos de pago.
    cand = [c for c in ia._candidatos(reg, False) if int(c.get("capability", 0)) >= 5]
    ok(cand and cand[0].get("name") == "gemini",
       "el default SUSTANTIVO sigue siendo Gemini (los nuevos no le quitan el sitio)")


def main():
    test_censo()
    test_muro()
    test_cuerpo_error()
    test_perfiles()
    test_no_regresion()
    print("RESULTADO roster de la centralita: %d OK, %d fallos" % (_pass, _fail))
    if not _fail:
        print("✅ ROSTER Y MAPA EN VERDE")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
