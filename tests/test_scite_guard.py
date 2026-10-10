#!/usr/bin/env python3
"""test_scite_guard.py — lo que sale a scite pasa por el muro (26-sep-2026, hallazgo A1).

Fija que `.claude/hooks/scite_guard.py`:
  · deja pasar terminología (gen, fármaco, DOI) y deniega nombre, huella genómica, tres señas y
    escritura de colecciones, en modo `bloquea`;
  · en modo `sombra` (el del rodaje) deja pasar lo mismo pero apunta «habría denegado» y avisa;
  · nunca copia el texto de la consulta a su log;
  · con el plazo vencido, deniega (watchdog);
  · está enganchado en `.claude/settings.json` con la matcher de `_evidencia.matcher()` (10-oct-26: los conectores
    de claude.ai llegan como `mcp__<uuid>__…` y con `mcp__scite__.*` salían sin mirar);
  · reconoce cada conector de evidencia por servidor y, si el identificador cambia, por nombre.
Y que `borde.senas_caso` cuenta bien las cinco señas.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ARBOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = os.path.join(ARBOL, ".claude", "hooks", "scite_guard.py")
sys.path.insert(0, os.path.join(ARBOL, "tools"))
import borde  # noqa: E402

_pass = _fail = 0


def check(nombre, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ " + nombre)


def run(tool, ti, modo, estado, extra=None):
    env = dict(os.environ, BTP_STATE_DIR=estado, BTP_SCITE_GUARD=modo)
    env.pop("BTP_SCITE_OK", None)
    env.update(extra or {})
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, H], input=json.dumps({"tool_name": tool, "tool_input": ti}),
                       capture_output=True, text=True, timeout=30, env=env)
    dt = time.monotonic() - t0
    try:
        dec = json.loads(p.stdout)["hookSpecificOutput"]
    except Exception:
        dec = {}
    return p.returncode, dec, dt


S = "mcp__scite__search_literature"
CASOS = [  # (descripción, tool, tool_input, debe_denegar)
    ("gen + fármaco", S, {"term": "FGFR4 inhibitor breast cancer"}, False),
    ("DOI", "mcp__scite__read_fulltext", {"doi": "10.1038/s41586-020-2649-2"}, False),
    ("histología sola", S, {"term": "lobular breast cancer endocrine resistance"}, False),
    ("nombre de la paciente", S, {"term": "{{TITULAR}} {{APELLIDO}} FGFR4"}, True),
    ("rsID", S, {"term": "MUTYH rs36053993 breast"}, True),
    ("HLA", S, {"term": "neoantigen HLA-A*02:01 breast"}, True),
    ("tres señas", S, {"term": "52 años carcinoma lobulillar RH+ HER2-low"}, True),
    ("tres señas repartidas en dos campos", S,
     {"term": "lobular ER+ breast cancer", "filters": {"q": "52 year-old after palbociclib"}}, True),
    ("escribir colección", "mcp__scite__create_collection", {"name": "caso"}, True),
    ("nota de colección", "mcp__scite__update_collection_note", {"note": "x"}, True),
]

# Conectores de claude.ai (10-oct-26). Datos inventados, nunca del caso.
SENAS = "mujer de 61 años, carcinoma lobular RE positivo HER2 negativo, tercera línea"
CON = {"scite": "mcp__c3041ca0-441e-42c5-9506-d697a7a03413__search_literature",
       "elicit": "mcp__2f62c1c8-d0b5-4cb4-9cbf-8dd48a783cf1__search_papers",
       "consensus": "mcp__fd386fb4-6a93-4061-9ba4-c645c3668c05__search",
       "scholar-gateway": "mcp__572eb2c5-1ab7-49e0-9551-fe96bcbb5f2d__semanticSearch",
       "pubmed": "mcp__b08b78ef-28ae-4b17-baf9-5cfd6efef4b0__search_articles",
       "biorxiv": "mcp__2b889bc6-e3f1-498b-bc68-58ad91fadea3__search_preprints",
       "clinical-trials": "mcp__94719adc-11f1-487e-b7fd-68ec3a3b8b7c__search_trials"}
for _fam, _tool in CON.items():
    CASOS.append(("conector %s · genérico" % _fam, _tool, {"query": "FGFR4 inhibitor breast cancer"}, False))
    CASOS.append(("conector %s · señas juntas" % _fam, _tool, {"query": SENAS}, True))
CASOS += [
    ("biomcp · genérico", "mcp__biomcp__article_searcher", {"query": "FGFR4 inhibitor breast cancer"}, False),
    ("biomcp · señas juntas", "mcp__biomcp__article_searcher", {"query": SENAS}, True),
    ("conector scite · escribir colección",
     "mcp__c3041ca0-441e-42c5-9506-d697a7a03413__create_collection", {"name": "x"}, True),
    ("elicit · subir fichero", "mcp__2f62c1c8-d0b5-4cb4-9cbf-8dd48a783cf1__stage_file_upload",
     {"filename": "x.csv"}, True),
    ("identificador de conector nuevo · se reconoce por nombre",
     "mcp__00000000-0000-0000-0000-000000000000__search_literature", {"term": SENAS}, True),
    ("identificador nuevo · elicit por nombre",
     "mcp__00000000-0000-0000-0000-000000000000__search_papers", {"query": SENAS}, True),
]
# MCP que no son de evidencia: el guard corre (matcher mcp__.*) y no dice nada.
AJENAS = ["mcp__x__search_posts_all", "mcp__b47695e8-1614-4b1d-81db-9d1d88117c68__search_threads",
          "mcp__1cc209a7-1f1f-42f3-90a5-0f6c1c9a777c__notion-search", "mcp__ccd_session__mark_chapter",
          "mcp__4954673b-12d5-40fa-9049-4474228f4a0d__creative_list_voices"]


def main():
    with tempfile.TemporaryDirectory() as est:
        lentos = []
        for desc, tool, ti, niega in CASOS:
            rc, dec, dt = run(tool, ti, "bloquea", est)
            lentos.append(dt)
            d = dec.get("permissionDecision")
            check("bloquea · %s → %s" % (desc, "deny" if niega else "pasa"),
                  rc == 0 and ((d == "deny") if niega else (d is None)))
        check("latencia < 3 s por llamada (timeout de settings: 5 s); peor: %.2f s" % max(lentos),
              max(lentos) < 3)

        # sombra: nada se deniega, pero lo que habría caído queda apuntado y se avisa
        rc, dec, _ = run(S, {"term": "52 años carcinoma lobulillar RH+ HER2-low"}, "sombra", est)
        check("sombra · deja pasar SIN aprobar (un allow se saltaría el aviso de permiso)",
              rc == 0 and "permissionDecision" not in dec)
        check("sombra · avisa de que habría denegado",
              "habría DENEGADO" in (dec.get("additionalContext") or ""))
        log = open(os.path.join(est, "scite_guard.jsonl"), encoding="utf-8").read()
        check("sombra · queda «habria-denegado» en el log", '"habria-denegado"' in log)
        check("el log NO lleva el texto de las consultas",
              "lobulillar" not in log and "{{APELLIDO}}" not in log and "rs36053993" not in log)

        rc, dec, _ = run(S, {"term": "{{TITULAR}} {{APELLIDO}} FGFR4"}, "bloquea", est, {"BTP_SCITE_OK": "1"})
        check("escotilla BTP_SCITE_OK=1 deja pasar", dec.get("permissionDecision") is None)
        check("…y queda registrada", '"allow-escotilla"' in open(
            os.path.join(est, "scite_guard.jsonl"), encoding="utf-8").read())

        rc, dec, _ = run("Bash", {"command": "ls"}, "bloquea", est)
        check("herramienta que no es scite: ni la mira", rc == 0 and not dec)
        antes = open(os.path.join(est, "scite_guard.jsonl"), encoding="utf-8").read()
        for tool in AJENAS:
            rc, dec, _ = run(tool, {"query": SENAS}, "bloquea", est)
            check("MCP ajena %s: ni la mira" % tool, rc == 0 and not dec)
        check("…y no deja línea en el log",
              antes == open(os.path.join(est, "scite_guard.jsonl"), encoding="utf-8").read())
        for fam, tool in CON.items():
            check("conector %s deja línea en el log" % fam, ('"tool": "%s"' % tool) in antes)

        # modo por fichero de estado (el que se cambia con el OK de {{TITULAR}})
        open(os.path.join(est, "scite_guard_modo"), "w").write("bloquea\n")
        env = dict(os.environ, BTP_STATE_DIR=est)
        env.pop("BTP_SCITE_GUARD", None)
        p = subprocess.run([sys.executable, H], input=json.dumps(
            {"tool_name": "mcp__scite__create_collection", "tool_input": {"name": "x"}}),
            capture_output=True, text=True, timeout=30, env=env)
        check("el fichero de modo manda cuando no hay variable",
              '"deny"' in p.stdout)

        # watchdog: plazo vencido → deniega (exit 2), como exige test_guard_timeout
        site = os.path.join(est, "site")
        os.makedirs(site)
        open(os.path.join(site, "sitecustomize.py"), "w").write(
            "import os, time\n_f = os.fork\n"
            "def _l():\n    pid = _f()\n    if pid == 0:\n        time.sleep(20)\n    return pid\n"
            "os.fork = _l\n")
        rc, dec, dt = run(S, {"term": "FGFR4"}, "bloquea", est,
                          {"PYTHONPATH": site, "BTP_WATCHDOG_S": "1"})
        check("plazo vencido → deniega (exit 2) en %.1f s" % dt, rc == 2 and dt < 5)

    # senas_caso
    for texto, n in (("FGFR4 inhibitor breast cancer", 0),
                     ("52 años carcinoma lobulillar RH+ HER2-low", 3),
                     ("woman 52 year-old lobular ER+ after progression on palbociclib", 4),
                     ("PIK3CA H1047R segunda línea", 2),
                     ("net benefit of endocrine therapy", 0)):
        check("senas_caso(%r) = %d" % (texto, n), borde.senas_caso(texto)[0] == n)

    # camino del lazo (tools/scite_mcp.py): misma política, antes de tocar la red (exit 3)
    vpy = os.path.expanduser("~/claudecode/.venv-consensus/bin/python")
    if os.path.exists(vpy):
        cli = os.path.join(ARBOL, "tools", "scite_mcp.py")
        for desc, args in (("colección", ["--tool", "create_collection", "x"]),
                           ("tres señas", ["52 años carcinoma lobulillar RH+ HER2-low"])):
            p = subprocess.run([vpy, cli] + args, capture_output=True, text=True, timeout=60,
                               stdin=subprocess.DEVNULL)
            check("lazo · %s → exit 3 sin salir" % desc, p.returncode == 3 and "BORDE" in p.stderr)
    else:
        print("  (sin .venv-consensus: no pruebo el camino del lazo)")

    s = json.load(open(os.path.join(ARBOL, ".claude", "settings.json"), encoding="utf-8"))
    sys.path.insert(0, os.path.dirname(H))
    import re
    import _evidencia
    mt = _evidencia.matcher()
    enganchado = [h for m in s["hooks"]["PreToolUse"] if m.get("matcher") == mt
                  for h in m.get("hooks", []) if h.get("command", "").endswith("scite_guard.py")]
    check("enganchado en settings.json con la matcher de _evidencia.matcher() y timeout 5",
          len(enganchado) == 1 and enganchado[0].get("timeout") == 5)
    rx = re.compile(mt)
    for _d, tool, _ti, _n in CASOS:
        check("la matcher casa %s" % tool, bool(rx.fullmatch(tool)))
    for tool in AJENAS + ["Bash", "Read"]:
        check("la matcher NO casa %s (el guard ni arranca)" % tool, not rx.fullmatch(tool))
    for srv in _evidencia.SERVIDORES:
        check("servidor %s cubierto por la matcher" % srv, bool(rx.fullmatch("mcp__%s__x" % srv)))
    check("_evidencia.py está junto al guard (sin él no arranca)",
          os.path.exists(os.path.join(os.path.dirname(H), "_evidencia.py")))

    print("test_scite_guard: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
