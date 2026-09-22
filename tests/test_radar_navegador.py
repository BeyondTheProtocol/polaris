#!/usr/bin/env python3
"""test_radar_navegador.py — el carril de registros sin API (CTIS, ICTRP, ChiCTR, CDE).

Lo que protege: que lo extraido con el navegador (a mano o por el VPS) NO se pierda (se ingiere,
se deduplica y entra en la cola con prioridad alta, porque no lo trae ninguna otra fuente), que el
recordatorio de barrer siga funcionando, y desde el 20-sep-2026 el carril chino: ICTRP como espejo
de ChiCTR con parser puro testeable sin Playwright, el CDE por PERFIL y no por 乳腺癌 a secas, y
que cada lead chino salga con su etiqueta de calidad. Sin red: la entrada son fixtures. $0.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="radar_nav_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_navegador as rv   # noqa: E402
import radar_cn_vps as cnv     # noqa: E402  (importable SIN playwright: lo importa en main)

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class A:
    def __init__(self, **kw):
        self.fuente = kw.get("fuente")
        self.fichero = kw.get("fichero")
        self.terminos = kw.get("terminos")
        self.dry = kw.get("dry", False)


def main():
    # 1. las cuatro fuentes estan declaradas con url, pasos y plantilla de ficha
    ok(set(rv.FUENTES) == {"ctis", "chictr", "cde", "ictrp"}, "las cuatro fuentes sin API estan declaradas")
    ok(all(f.get("url") and f.get("pasos") and "{id}" in f.get("ficha", "")
           for f in rv.FUENTES.values()), "cada fuente trae url, pasos y ficha parametrizada")
    ok(set(rv.FUENTES_CN) == {"ictrp", "chictr", "cde"}, "las fuentes chinas son ictrp/chictr/cde")

    # 2. nunca barrido => todas pendientes (es lo que la brujula saca en cada sesion)
    ok(len(rv.pendientes()) == 4, "sin barridos previos, las cuatro estan pendientes")
    ok(rv.dias_desde("ctis") is None, "sin marca, dias_desde es None")

    # 3. ingesta: normaliza, deduplica y sella la marca
    f = os.path.join(_TMP, "ctis.json")
    with open(f, "w") as fh:
        json.dump([{"id": "2025-522707-26-00", "fecha": "15/09/2026",
                    "titulo": "IEV407 en mama avanzada", "cond": "Advanced HR+/HER2- breast cancer",
                    "loc": "Germany/Italy"},
                   {"id": "2026-527524-27-00", "titulo": "ODEON"}], fh)
    ok(rv.cmd_ingerir(A(fuente="ctis", fichero=f, dry=True)) == 0, "ingesta en dry no falla")
    ok(rv.dias_desde("ctis") is None, "una ingesta en dry NO sella la marca")
    ok(rv.cmd_ingerir(A(fuente="ctis", fichero=f, terminos="breast cancer")) == 0, "ingesta real")
    ok(rv.dias_desde("ctis") == 0, "la ingesta real sella la marca de hoy")
    ok(len(rv.pendientes()) == 3, "la fuente barrida sale de pendientes")

    # 4. la segunda ingesta del mismo fichero no duplica nada
    import radar_ned_diario as rn
    n_antes = len(rn.lee_cola()["pendientes"])
    rv.cmd_ingerir(A(fuente="ctis", fichero=f, terminos="breast cancer"))
    ok(len(rn.lee_cola()["pendientes"]) == n_antes, "reingerir lo mismo no duplica la cola")

    # 5. lo del navegador entra con prioridad ALTA: no lo trae ninguna otra fuente, no puede
    #    quedarse fuera del cupo de la capa abierta
    c = rn.lee_cola()
    ok(any(p["uid"].startswith("ctis:") for p in c["pendientes"]), "el lead de CTIS esta en la cola")
    ok(all(not p.get("cn") for p in c["pendientes"] if p["uid"].startswith("ctis:")),
       "un lead de CTIS no se marca como chino")

    # 6. un JSON que no es lista se rechaza sin romper nada
    malo = os.path.join(_TMP, "malo.json")
    open(malo, "w").write('"no soy una lista"')
    ok(rv.cmd_ingerir(A(fuente="ctis", fichero=malo)) == 2, "un JSON con forma incorrecta se rechaza")
    ok(rv.cmd_ingerir(A(fuente="inventada", fichero=f)) == 2, "una fuente desconocida se rechaza")

    # --- B1 (20-sep-2026): ICTRP, espejo OMS de ChiCTR -----------------------------------------
    # 7. la ficha de ICTRP abre por TrialID (verificado HTTP 200 el 20-sep desde Espana y el VPS)
    ok("Trial2.aspx?TrialID={id}" in rv.FUENTES["ictrp"]["ficha"], "la ficha de ICTRP es Trial2.aspx?TrialID=")
    ok(cnv.ICTRP_PAIS == "China" and cnv.ICTRP_CONDICION == "breast cancer",
       "la bateria ICTRP es 'breast cancer' x China")
    ok(set(cnv.ICTRP_INTERVENCIONES) == {"neoantigen", "personalized vaccine", "mRNA vaccine",
                                         "tumor infiltrating lymphocyte", "CDK2", "FGFR", "{{DIANA2}}",
                                         "TROP2", "antibody-drug conjugate"},
       "los 9 pares de intervencion del plan estan en la bateria")

    # 8. el parser de filas es PURO (sin Playwright) y digiere las filas reales medidas el 20-sep
    filas = [
        "Recruitment status\tProspective Registration\tMain ID\t\xa0\tPublic Title\tDate of Registration\tResults available",
        "Recruiting\t\tChiCTR2600131806\t\n\tA single-arm, exploratory clinical study of Dalpiciclib "
        "combined with letrozole in HR-positive, HER2-negative breast cancer\t2026-09-07\t\xa0",
        "Authorised\t\tEUCTR2020-005620-12-ES\t\n\tA Study of Dato-DXd Versus Chemotherapy in Hormone "
        "Receptor-positive, HER2-negative Breast Cancer\t2021-10-06\t\xa0",
        "Recruiting\t\tNCT07471776\t\n\tStudy on Using TROP2-PET\t2026-03-10\tYes",
        "Recruiting\t\tChiCTR2600131806\t\n\tDUPLICADA\t2026-09-07\t\xa0",
        "\xa0\t\xa0\t\xa0\t\xa0\t\t\xa0\t\xa0",
        "No results were found.",
    ]
    p = cnv.parse_filas_ictrp(filas)
    ok([x["id"] for x in p] == ["ChiCTR2600131806", "EUCTR2020-005620-12-ES", "NCT07471776"],
       "el parser saca los IDs, ignora cabecera/vacios y deduplica (%s)" % [x["id"] for x in p])
    ok(p[0]["fecha"] == "2026-09-07" and p[0]["estado"] == "Recruiting" and p[0]["registro"] == "ChiCTR",
       "fecha, estado y registro de la fila ChiCTR")
    ok(p[0]["resultados"] is False and p[2]["resultados"] is True, "la columna 'Results available' se lee")
    ok(cnv.PALABRAS_PERFIL.search(p[0]["titulo"]) and not cnv.PALABRAS_PERFIL.search(
        "Application of spouse support breastfeeding training model"),
       "el filtro de perfil deja pasar HR+/HER2- y frena la lactancia")

    # 9. ingesta de ICTRP: prefijo de estado, etiqueta de calidad, marca cn, y los avisos del VPS
    fi = os.path.join(_TMP, "ictrp.json")
    with open(fi, "w") as fh:
        json.dump(p + [{"error": "ventana 14d: ICTRP declara 170 y solo se leyeron 100"}], fh)
    ok(rv.cmd_ingerir(A(fuente="ictrp", fichero=fi, terminos="perfil")) == 0, "ingesta de ICTRP")
    c = rn.lee_cola()
    lead = next((x for x in c["pendientes"] if x["uid"] == "ictrp:ChiCTR2600131806"), None)
    ok(lead is not None, "el ChiCTR entra en la cola via ICTRP")
    ok(lead and lead["titulo"].startswith("[Recruiting]"), "el titulo lleva el estado delante")
    ok(lead and lead.get("cn") is True, "un lead de ICTRP se marca como chino")
    ok(lead and rn.ETIQUETAS["registro-sin-resultados"] in lead.get("calidad", []),
       "un ChiCTR sin resultados lleva [registro-CN · sin resultados]")
    nct = next((x for x in c["pendientes"] if x["uid"] == "ictrp:EUCTR2020-005620-12-ES"), None)
    ok(nct and rn.ETIQUETAS["registro-centros-cn"] in nct.get("calidad", [])
       and rn.ETIQUETAS["registro-sin-resultados"] not in nct.get("calidad", []),
       "un EUCTR con centros en China NO se etiqueta como registro chino")
    ok(not any("error" in (x.get("uid") or "") for x in c["pendientes"]),
       "un item {error} del VPS no entra en la cola como lead")
    ok(len(rv.pendientes()) == 2, "ictrp ya no esta pendiente tras ingerir")

    # --- B3 (20-sep-2026): CDE por perfil, no por 乳腺癌 generico -------------------------------
    ok(len(cnv.TERMINOS_CDE) >= 15 and "乳腺癌" not in cnv.TERMINOS_CDE,
       "el CDE no se busca por 乳腺癌 a secas (asi entro un ALK de pulmon)")
    ok({"SKB264", "TQB3616", "SHR6390", "ZL-1310", "SI-B036"} <= set(cnv.TERMINOS_CDE),
       "los codigos de molecula del perfil estan en TERMINOS_CDE")
    ok(any("新抗原" in t for t in cnv.TERMINOS_CDE) and any("神经内分泌" in t for t in cnv.TERMINOS_CDE),
       "neoantigeno y neuroendocrino en chino estan en TERMINOS_CDE")
    pasos_cde = " ".join(rv.FUENTES["cde"]["pasos"])
    ok("SKB264" in pasos_cde and "新抗原" in pasos_cde, "el plan manual del CDE lista los terminos de perfil")
    ok("Return" in pasos_cde and "form_input" in pasos_cde, "el plan del CDE avisa de teclear + Return")

    print("test_radar_navegador: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
