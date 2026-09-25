#!/usr/bin/env python3
"""test_verifica_citas.py — el primer filtro contra citas fabricadas.

Este tool existe para cazar el fallo nº1 de los buscadores LLM: citar papers y ensayos que NO
existen. Y tenía un agujero que lo dejaba ciego justo donde más se usa: el DOI solo se extraía
si la cadena venía «pelada» (empezando por 10./doi/http), así que una referencia bibliográfica
normal caía a `desconocido` → `no_resoluble`, y `.claude/agents/verificacion.md` instruye
explícitamente NO acusar ante `no_resoluble` (lo trata como red caída). Un DOI fabricado dentro
de una bibliografía pasaba limpio.

HERMÉTICO: los comprobadores se mockean, así que no toca la red ni depende de Crossref.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import verifica_citas as v  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # ── clasificación: de dónde se saca el id ───────────────────────────────────────
    casos = [
        # (cadena, tipo esperado, id esperado)
        ("Pérez J, et al. Lancet Oncol. 2025;26(4):e123. doi:10.1016/j.annonc.2025.01.001",
         "doi", "10.1016/j.annonc.2025.01.001"),
        ("Smith A. Nature. 2024. https://doi.org/10.1038/s41586-024-07123-4",
         "doi", "10.1038/s41586-024-07123-4"),
        ("10.1016/j.annonc.2025.01.001", "doi", "10.1016/j.annonc.2025.01.001"),
        ("Estudio CONTACTO-1 (NCT07222267), fase II, {{CENTRO}}", "nct", "NCT07222267"),
        ("PMID: 38472196", "pmid", "38472196"),
        ("38472196", "pmid", "38472196"),
    ]
    for cadena, tipo, cid in casos:
        t, i = v.clasifica(cadena)
        check("clasifica %-6s en «%s…»" % (tipo, cadena[:34]), t == tipo and i == cid)

    # Un DOI dentro de una cita larga NO puede seguir cayendo a «desconocido»: ese era el bug.
    t, _ = v.clasifica("Autor X. Revista Y. 2025. doi:10.1234/abcd.efgh")
    check("DOI embebido ya no cae a desconocido", t == "doi")

    # Falsos positivos: una dosis clínica no es un DOI (10. seguido de <4 dígitos).
    t, _ = v.clasifica("Dosis de 10.5/mg cada 12h")
    check("una dosis clínica NO se toma por DOI", t != "doi")
    t, _ = v.clasifica("Ratio 10.25/kg en el protocolo")
    check("otro decimal con barra tampoco", t != "doi")

    # ── estados: lo ilegible NO se disfraza de «la red falló» ────────────────────────
    r = v.verifica(["esto no es una cita, es una frase suelta"])[0]
    check("cadena sin id → no_parseable", r["estado"] == v.NO_PARSE)
    check("no_parseable ≠ no_resoluble", v.NO_PARSE != v.NO_RES)
    check("el detalle dice que NO es la red",
          "red" in r["detalle"].lower() and "mano" in r["detalle"].lower())

    # ── el veredicto llega entero desde el comprobador ───────────────────────────────
    orig = dict(v._CHECKERS)
    try:
        v._CHECKERS["doi"] = lambda cid: (v.FABRICADA, "no está en Crossref", "crossref")
        r = v.verifica(["Pérez J. Lancet. 2025. doi:10.1016/S1470-2045(25)99999-9"])[0]
        check("DOI fabricado DENTRO de una cita → FABRICADA", r["estado"] == v.FABRICADA)
        check("y conserva el id extraído", r["id"].startswith("10.1016/"))

        v._CHECKERS["doi"] = lambda cid: (v.EXISTE, "ok", "crossref")
        r = v.verifica(["Pérez J. Lancet. 2025. doi:10.1016/j.real.2025.01.001"])[0]
        check("DOI que existe → existe", r["estado"] == v.EXISTE)

        v._CHECKERS["doi"] = lambda cid: (v.NO_RES, "timeout", "—")
        r = v.verifica(["doi:10.1016/j.x.2025.01.001"])[0]
        check("la red caída SÍ es no_resoluble", r["estado"] == v.NO_RES)
    finally:
        v._CHECKERS.clear()
        v._CHECKERS.update(orig)

    # Varias citas de golpe: cada una con su veredicto, sin contaminarse.
    orig = dict(v._CHECKERS)
    try:
        v._CHECKERS["doi"] = lambda cid: (v.FABRICADA, "no existe", "crossref")
        v._CHECKERS["nct"] = lambda cid: (v.EXISTE, "ok", "clinicaltrials.gov")
        # DOI con prefijo realista (4+ dígitos): «10.1/x» NO es un DOI y el clasificador hace
        # bien en rechazarlo — es la misma guarda que evita confundir una dosis con una cita.
        res = v.verifica(["Autor. Rev. 2025. doi:10.1016/j.xxxx.2025.01.001",
                          "Ensayo (NCT07222267)",
                          "frase sin id"])
        check("cada cita conserva su veredicto",
              [r["estado"] for r in res] == [v.FABRICADA, v.EXISTE, v.NO_PARSE])
    finally:
        v._CHECKERS.clear()
        v._CHECKERS.update(orig)

    print("RESULTADO verifica_citas: %d OK, %d fallos" % (_pass, _fail))
    print("✅ FILTRO DE CITAS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
