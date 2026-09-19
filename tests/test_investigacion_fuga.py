#!/usr/bin/env python3
"""test_investigacion_fuga.py — GUARDIA DE FUGA del espejo de investigación → Notion (EGRESS, muro).

Es el espejo del guardia de calendar_sync (test de privacidad): FALLA si algo que delata a TERCEROS o
rompe el embargo intenta salir. Allowlist fail-closed POR-ÍTEM: nada sale sin `publico:true` Y sin
pasar el gate (PII / términos vetados / nombres de terceros). Las DIANAS del caso son PÚBLICAS
(directiva de {{TITULAR}} 25/6) → SÍ salen; se prueba que la VÁLVULA `_DIANAS_VETADAS` sigue operativa por
si un término llegara a bloquear NED. Cubre los DOS niveles de veda: EMBARGO temporal
(vacuna/neoantígenos, se levanta a mano) vs. DURABLE ({{CONTACTO}}, PII). Y la PARIDAD: el espejo de TAREAS
pre-reveal no cambia. Aísla todo en un tmp; no toca el repo."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres", "perfil")
import os
import sys
import json
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_invfuga_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import seguimiento as s  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def _seed(items):
    s._write_atomic(s.INVESTIGACION, {"items": items})


def _set_embargo(reveal):
    """reveal=None → borra el flag (embargo ACTIVO por defecto, fail-safe)."""
    if reveal is None:
        if os.path.exists(s.EMBARGO_FLAG):
            os.remove(s.EMBARGO_FLAG)
    else:
        s._write_atomic(s.EMBARGO_FLAG, {"reveal": reveal})


def _ids(canal="investigacion"):
    return {r.get("id") for r in s.construir_export(canal)}


def _md_limpio(md, prohibidos):
    """True si NINGÚN token prohibido aparece en el markdown (case-insensitive)."""
    low = md.lower()
    return not any(p.lower() in low for p in prohibidos)


# ── Semilla: un benigno publicable + un benigno con campos opcionales vacíos + toda la galería de
#    intentos de fuga, todos marcados publico:true para probar que el GATE (no el flag) los frena. ──
ITEMS = [
    {"id": "benigno", "titulo": "Open-source pipeline for variant annotation",
     "autores": "Doe J, Roe A", "enlace": "https://doi.org/10.1/abc",
     "relevancia": "Metodo general reproducible", "publico": True},                 # SOBREVIVE
    {"id": "benigno2", "titulo": "Reproducible methods in clinical bioinformatics",
     "autores": "", "relevancia": "", "enlace": "", "publico": True},               # SOBREVIVE (opcionales vacíos)
    {"id": "nomarcado", "titulo": "General review of immunotherapy"},               # sin publico → fuera
    {"id": "nomarcado2", "titulo": "Another generic review", "publico": False},     # publico:false → fuera
    {"id": "diana-tit", "titulo": "FGFR1 amplification in advanced disease", "publico": True},   # diana PÚBLICA → sale
    {"id": "diana-rel", "titulo": "A perfectly generic methods paper",
     "relevancia": "Relevante para {{LOCUS}} y CDK2", "publico": True},                 # diana PÚBLICA → sale
    {"id": "diana-ofus", "titulo": "Notes on FGFR-1 and CDK4/6 signaling", "publico": True},     # diana PÚBLICA → sale
    {"id": "emb", "titulo": "Personalized neoantigen vaccine advances", "publico": True},        # EMBARGO (no diana)
    {"id": "contacto", "titulo": "Manufacturing collaboration overview with {{CONTACTO}}", "publico": True},  # DURABLE
    {"id": "tercero", "titulo": "Notes from {{CONTACTO}} on the protocol", "publico": True},           # nombre tercero
    {"id": "email", "titulo": "Reach the author at john@lab.org", "publico": True},               # email
    {"id": "dinero", "titulo": "A 50k grant for the study", "publico": True},                     # importe
]


def main():
    # ── FASE 1 · EMBARGO ACTIVO (sin flag = por defecto). ───────────────────────────────────────
    _set_embargo(None)
    _seed(ITEMS)
    salen = _ids()
    check("sobreviven benignos + dianas PÚBLICAS marcados publico:true",
          salen == {"benigno", "benigno2", "diana-tit", "diana-rel", "diana-ofus"})
    for malo in ("nomarcado", "nomarcado2", "emb", "contacto", "tercero", "email", "dinero"):
        check("excluido: %s" % malo, malo not in salen)

    # Campos: el export nunca lleva el flag interno `publico` ni nada fuera de la allowlist.
    rec = next(r for r in s.construir_export("investigacion") if r["id"] == "benigno")
    check("export solo trae INVEST_CAMPOS", set(rec) == set(s.INVEST_CAMPOS))
    check("export no filtra el flag publico", "publico" not in rec)

    # El markdown: las dianas (públicas) SÍ aparecen; lo de terceros/embargo NO.
    md = s.render_investigacion_md()
    check("md pre-reveal: las dianas (públicas) SÍ aparecen", "fgfr1" in md.lower())
    check("md pre-reveal sin fugas (embargo/terceros/{{CONTACTO}})", _md_limpio(
        md, ["neoantigen", "vaccine", "vacuna", "contacto", "contacto", "@lab.org", "50k"]))
    check("md anuncia que es lista curada (no toda la investigación)",
          "no es toda la investigación" in md.lower())
    check("md renderiza enlaces CLICABLES (no texto pelado)", "](https://doi.org/10.1/abc)" in md)

    # VÁLVULA de dianas: vacía por defecto (dianas públicas), pero el MECANISMO sigue vivo — si un
    # término concreto llegara a bloquear NED, se añade aquí y vuelve a taparse.
    check("dianas no vetadas por defecto", s._DIANAS_VETADAS == ())
    _orig = s._DIANAS_VETADAS
    try:
        s._DIANAS_VETADAS = ("fgfr1",)
        check("válvula operativa: al vetar 'fgfr1' la diana se excluye", "diana-tit" not in _ids())
    finally:
        s._DIANAS_VETADAS = _orig
    check("válvula restaurada: la diana vuelve a salir", "diana-tit" in _ids())

    # ── FASE 1b · OFUSCACIÓN (el fallo que cazó verificacion). Todo publico:true, embargo activo. ──
    _set_embargo(None)
    OFUS = [
        {"id": "benigno", "titulo": "Open methods benchmark", "publico": True},     # control: sale
        {"id": "o-spaced", "titulo": "Notes with O l u n e team", "publico": True},
        {"id": "o-hyphen", "titulo": "Ol-une collaboration", "publico": True},
        {"id": "o-cyr", "titulo": "Оlune partnership review", "publico": True},      # О cirílica
        # «vacuna» ya es pública (29-7-26): la clase ofuscación se prueba con «neoantígeno», aún embargado.
        {"id": "v-diaeresis", "titulo": "neöantigen manufacturing", "publico": True},
        {"id": "v-zwsp", "titulo": "neo​antigen design notes", "publico": True},    # zero-width
        {"id": "route-spaced", "titulo": "mrna 4157 early data", "publico": True},
        {"id": "route-name", "titulo": "{{VACUNA}} results", "publico": True},
        {"id": "name-homo", "titulo": "Paper by Yаnnick", "publico": True},          # а cirílica
        {"id": "email-fw", "titulo": "write to john＠lab.org", "publico": True},      # @ fullwidth
    ]
    _seed(OFUS)
    salen = _ids()
    check("ofuscación: solo el control benigno sale", salen == {"benigno"})
    for malo in ("o-spaced", "o-hyphen", "o-cyr", "v-diaeresis", "v-zwsp",
                 "route-spaced", "route-name", "name-homo", "email-fw"):
        check("ofuscación bloqueada: %s" % malo, malo not in salen)
    # borde (carril ingeniero) caza la MISMA clase con la MISMA fuente (seg._canon).
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import borde  # noqa: E402
    check("borde caza 'O l u n e'", borde.clasificar("trabajo con O l u n e")[0])
    check("borde caza 'mrna 4157' (separador propio)", borde.clasificar("ensayo mrna 4157")[0])
    check("borde caza homoglifo 'vacüna'", borde.clasificar("vacüna fase 1")[0])

    # ── FASE 1c · datos malformados (no-str) → fail-closed, sin crash. ──────────────────────────
    _seed([
        {"id": "ok", "titulo": "Clean robust title", "autores": 123, "enlace": 999, "publico": True},
        {"id": "bad-title", "titulo": 42, "publico": True},
        {"id": "not-dict"},
        ["soy", "una", "lista"],
    ])
    salen = _ids()
    check("no-str: título no-str excluido", "bad-title" not in salen)
    check("no-str: benigno con autores/enlace no-str sobrevive", "ok" in salen)
    check("no-str: render no crashea", isinstance(s.render_investigacion_md(), str))

    # ── FASE 1d · el ENLACE también pasa el gate (defensa en profundidad, verificacion 25/6). ────
    _set_embargo(None)
    _seed([
        {"id": "link-ok", "titulo": "Methods paper A",
         "enlace": "https://doi.org/10.1/fgfr1-mbc", "publico": True},        # diana en enlace → sale
        {"id": "link-pii", "titulo": "Methods paper B",
         "enlace": "https://x.com/?u=contacto@lab.org", "publico": True},       # email/tercero → fuera
        {"id": "link-veto", "titulo": "Methods paper C",
         "enlace": "https://x.com/contacto-deck", "publico": True},               # {{CONTACTO}} en enlace → fuera
    ])
    salen = _ids()
    check("enlace con diana (FGFR1) SÍ sale (dianas públicas)", "link-ok" in salen)
    check("enlace con PII (email/tercero) excluido", "link-pii" not in salen)
    check("enlace con término durable ({{CONTACTO}}) excluido", "link-veto" not in salen)

    # ── FASE 2 · corrupción y vacío (fail-closed). ──────────────────────────────────────────────
    with open(s.INVESTIGACION, "w", encoding="utf-8") as f:
        f.write("{ esto no es json ::: ")
    check("json corrupto → export vacío", s.construir_export("investigacion") == [])
    check("json corrupto → load vacío", s.load_investigacion() == [])
    _seed([])
    check("sin items → export vacío", s.construir_export("investigacion") == [])
    check("vacío → md lo dice honestamente",
          "sin papers marcados como públicos" in s.render_investigacion_md().lower())

    # ── FASE 3 · REVEAL ({{TITULAR}} levanta el embargo a mano). ──────────────────────────────────────
    _seed(ITEMS)
    _set_embargo(True)
    salen = _ids()
    check("post-reveal: el item de vacuna/neoantígeno YA sale", "emb" in salen)
    check("post-reveal: {{CONTACTO}} SIGUE vetado (durable)", "contacto" not in salen)
    for diana in ("diana-tit", "diana-rel", "diana-ofus"):
        check("post-reveal: diana (pública) sale (%s)" % diana, diana in salen)
    check("post-reveal: tercero/email/importe siguen vetados",
          not ({"tercero", "email", "dinero"} & salen))
    md2 = s.render_investigacion_md()
    check("md post-reveal sin fugas durables", _md_limpio(
        md2, ["contacto", "contacto", "@lab.org", "50k"]))

    # ── FASE 4 · PARIDAD del espejo de TAREAS (no romper lo que ya funciona). ────────────────────
    _set_embargo(None)   # embargo activo = comportamiento de hoy
    s._write_atomic(s.SEG, {"hilos": [
        {"id": "t-ok", "titulo": "Revisar analytics de la web", "categoria": "infra",
         "estado": "en_curso"},
        {"id": "t-vac", "titulo": "Preparar la vacuna personalizada", "categoria": "infra",
         "estado": "en_curso"},                       # «vacuna» levantada 29-7-26 → sale ya
        {"id": "t-neo", "titulo": "Pipeline de neoantígenos", "categoria": "infra",
         "estado": "en_curso"},                       # término embargo → fuera pre-reveal
        {"id": "t-contacto", "titulo": "Llamar a {{CONTACTO}}", "categoria": "infra", "estado": "en_curso"},
        {"id": "t-priv", "titulo": "Tema privado", "categoria": "infra", "estado": "en_curso",
         "privado": True},
    ]})
    tareas = _ids("notion")
    check("tareas pre-reveal: la limpia sale", "t-ok" in tareas)
    check("tareas pre-reveal: 'vacuna' YA sale (levantada por {{TITULAR}} 29-7-26)", "t-vac" in tareas)
    check("tareas pre-reveal: 'neoantígenos' excluida (embargo)", "t-neo" not in tareas)
    check("tareas pre-reveal: '{{CONTACTO}}' excluida (durable)", "t-contacto" not in tareas)
    check("tareas pre-reveal: privado excluido", "t-priv" not in tareas)
    # Tras el reveal, el espejo de tareas también puede nombrar el tratamiento (única fuente de veda).
    _set_embargo(True)
    check("tareas post-reveal: 'vacuna' sigue saliendo", "t-vac" in _ids("notion"))
    check("tareas post-reveal: 'neoantígenos' ya puede salir", "t-neo" in _ids("notion"))
    check("tareas post-reveal: '{{CONTACTO}}' sigue fuera", "t-contacto" not in _ids("notion"))

    # ── FASE 5 · investigacion-add entra SIEMPRE publico:false. ──────────────────────────────────
    _seed([])
    iid = s.investigacion_add({"titulo": "Un paper recién capturado", "autores": "X"})
    nuevo = next(it for it in s.load_investigacion() if it["id"] == iid)
    check("investigacion-add entra publico:false", nuevo.get("publico") is False)
    check("investigacion-add no auto-publica", s.construir_export("investigacion") == [])

    # ── FASE 6 · investigacion-sync (volcado de la base curada): publico:true + el gate RE-FILTRA. ──
    _set_embargo(None)
    n = s.investigacion_sync([
        {"titulo": "FGFR1 amplification methods", "autores": "Wu C",
         "enlace": "https://doi.org/10.9/x", "relevancia": "Resumen publico claro"},   # limpio → sale
        {"titulo": "Neoantigen vaccine roadmap", "relevancia": "early data"},           # embargo → fuera
        {"titulo": "Deck shared with {{CONTACTO}}", "relevancia": "n/a"},                       # {{CONTACTO}} → fuera
        {"titulo": "   ", "relevancia": "sin titulo"},                                   # vacío → ignorado
    ])
    check("sync ignora el item sin título", n == 3)
    fuente = s.load_investigacion()
    check("sync mete los 3 como publico:true", len(fuente) == 3 and all(it["publico"] is True for it in fuente))
    salen = _ids()
    check("sync+gate: el limpio (diana pública) SALE", "fgfr1-amplification-methods" in salen)
    check("sync+gate: embargo re-filtrado pese a publico:true", "neoantigen-vaccine-roadmap" not in salen)
    check("sync+gate: {{CONTACTO}} re-filtrado pese a publico:true", "deck-shared-with-contacto" not in salen)
    check("sync idempotente: vacío → fuente vacía", s.investigacion_sync([]) == 0 and s.load_investigacion() == [])
    check("sync valida tipo (no-lista → error)", _raises(lambda: s.investigacion_sync({"x": 1})))

    print("✅ test_investigacion_fuga OK (%d checks)" % _pass if not _fail
          else "❌ test_investigacion_fuga: %d fallo(s) de %d" % (_fail, _pass + _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
