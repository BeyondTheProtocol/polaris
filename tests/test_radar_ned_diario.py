#!/usr/bin/env python3
"""test_radar_ned_diario.py — el radar NED diario y su COLA DE VERIFICACIÓN.

Lo que protege (y por qué): el barrido es automático, pero ABRIR la fuente no lo es. Si la
cola se vacía sola, se duplica o deja de salir en la brújula, volvemos al fallo del 30-jul:
leads sin verificar presentados como hallazgos. Read-only, $0, SIN RED (los hits son fixtures).
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="radar_ned_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_REPO"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import radar_ned_diario as r   # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _res(hits_alta, hits_media=(), fecha="2026-09-18"):
    return {"fecha": fecha, "ts": fecha, "nuevos": len(hits_alta) + len(hits_media),
            "ventana": {"desde": fecha, "hasta": fecha},
            "alta_con_novedad": ["fgfr4"] if hits_alta else [],
            "triado": False,
            "temas": [
                {"clave": "fgfr4", "titulo": "FGFR4", "prio": "alta",
                 "hits": list(hits_alta), "errores": []},
                {"clave": "serd", "titulo": "SERD", "prio": "media",
                 "hits": list(hits_media), "errores": []},
            ]}


def _hit(uid, tipo="paper", ref=None):
    return {"uid": uid, "tipo": tipo, "titulo": "t-" + uid, "fecha": "2026-09-18",
            "ref": ref or uid, "url": "https://example.org/" + uid, "fuente": "fixture"}


def main():
    # 1. solo entra a la cola lo de prioridad ALTA, y las patentes NO (son contexto, no lead)
    n, total, _ = r.encola(_res([_hit("a1"), _hit("a2", "patente")], [_hit("m1")]))
    ok((n, total) == (1, 1), "encola solo ALTA y descarta patentes (n=%s total=%s)" % (n, total))

    # 2. idempotente: el mismo barrido dos veces no duplica
    n2, total2, _ = r.encola(_res([_hit("a1")]))
    ok((n2, total2) == (0, 1), "encolar dos veces no duplica")

    # 3. un lead cerrado NO vuelve a entrar aunque reaparezca en un barrido posterior
    class A:
        ref, veredicto = "a1", "abierto: dice X"
    ok(r.cmd_cerrar(A) == 0, "cerrar con veredicto funciona")
    n3, _, _ = r.encola(_res([_hit("a1")]))
    ok(n3 == 0, "un lead ya cerrado no reaparece en la cola")
    c = r.lee_cola()
    ok(len(c["pendientes"]) == 0 and len(c["cerrados"]) == 1, "el cerrado se archiva con veredicto")
    ok(c["cerrados"][0].get("veredicto", "").startswith("abierto"), "el veredicto se guarda")

    # 4. cerrar SIN veredicto no cierra nada: sin abrir la fuente no hay nada que cerrar
    r.encola(_res([_hit("b1")]))

    class B:
        ref, veredicto = "b1", None
    ok(r.cmd_cerrar(B) == 2, "cerrar sin veredicto se rechaza")
    ok(len(r.lee_cola()["pendientes"]) == 1, "el pendiente sigue en la cola tras el rechazo")

    # 5. la COLA SALE EN LA BRÚJULA (el mecanismo de «siempre en pantalla»)
    import contexto_lazo
    txt = contexto_lazo.bloque(con_estilo_telegram=False)
    ok("Radar NED" in txt and "SIN ABRIR" in txt, "la cola aparece en la brújula")
    ok("b1" in txt, "la brújula nombra el lead pendiente")

    # 6. un digest bueno no se machaca con una pasada vacía
    os.makedirs(r.RADAR_DIR, exist_ok=True)
    ruta = os.path.join(r.RADAR_DIR, "Radar-NED-diario-2026-09-18.md")
    with open(ruta, "w") as f:
        f.write("DIGEST BUENO")
    r.escribe_digest(_res([]))
    ok(open(ruta).read() == "DIGEST BUENO", "una pasada sin novedades no machaca el digest")
    r.escribe_digest(_res([_hit("c1")]))
    cuerpo = open(ruta).read()
    ok(cuerpo.startswith("DIGEST BUENO") and "Pasada adicional" in cuerpo,
       "una pasada con novedades se añade, no sustituye")

    # 8d. las marcas de validez externa: lo de otro subtipo o preclínico se avisa, no se cuela
    ok(r._flags("Pyroptosis in Triple-Negative Breast Cancer") == ["otro subtipo"], "marca TNBC")
    ok(r._flags("blockade in 4T1 mouse model") == ["preclinico"], "marca modelo preclínico")
    ok(r._flags("Dato-DXd in HR-positive HER2-negative breast cancer") == [], "no marca lo suyo")

    # 7. política del borde: ninguna query lleva citobanda ni variante (las bloquearía, con razón)
    import re
    sospechoso = re.compile(r"\b\d{1,2}[pq]\d{2}\b|\b[A-Z]\d{3}[A-Z]\b")
    malas = [t["clave"] for t in r.TEMAS
             if sospechoso.search(t["epmc"]) or sospechoso.search(t.get("ctgov") or "")]
    ok(not malas, "ninguna query lleva huella genómica (%s)" % malas)

    # 8b. la capa abierta existe y no está vacía: sin ella el radar solo ve lo que ya sabe buscar
    ok(len(r.TEMAS_ABIERTOS) >= 3, "hay capa abierta de descubrimiento")
    ok(any(t.get("ctgov_filtro") for t in r.TEMAS_ABIERTOS),
       "algún tema abierto busca ensayos recién REGISTRADOS, no solo actualizados")
    ok(all(t["prio"] == "abierta" for t in r.TEMAS_ABIERTOS), "los abiertos van con prioridad abierta")

    # 8c. el cupo de la capa abierta NO descarta en silencio: informa de lo que deja fuera
    muchos = [_hit("x%d" % i) for i in range(r.COLA_TOPE_ABIERTO + 4)]
    res_ab = _res([])
    res_ab["temas"].append({"clave": "nuevo-agente", "titulo": "abierto", "prio": "abierta",
                            "hits": muchos, "errores": []})
    n_ab, _, fuera = r.encola(res_ab)
    ok(n_ab == r.COLA_TOPE_ABIERTO and fuera == 4,
       "el cupo encola %s y reporta %s fuera (n=%s, fuera=%s)" % (r.COLA_TOPE_ABIERTO, 4, n_ab, fuera))

    # 8. toda diana declara prioridad válida y consulta en las dos fuentes
    ok(all(t["prio"] in ("alta", "media", "abierta") for t in r.TEMAS), "prioridades válidas")
    ok(all(t.get("epmc") and t.get("ctgov") for t in r.TEMAS_DIANA), "cada diana pregunta a las 2 fuentes")
    ok(all(t.get("epmc") for t in r.TEMAS), "todo tema pregunta al menos a la literatura")
    ok(len(r.TEMAS_MODALIDAD) >= 4, "hay capa de modalidades (celular, vacunas, radioligando...)")
    ok(any("CAR" in t["epmc"] for t in r.TEMAS_MODALIDAD), "la terapia celular se vigila por plataforma")
    ok(any("vaccine" in t["epmc"] for t in r.TEMAS_MODALIDAD), "las vacunas se vigilan por plataforma")

    # --- B2 (20-sep-2026): afiliacion ampliada + preprints aparte + revistas en chino ----------
    # Medido ese dia en Europe PMC: 860 registros de 2026 de Sun Yat-sen/Fudan/Peking Union sin la
    # palabra "China"; SRC:PPR AND AFF:"China" = 0 (los preprints no llevan afiliacion indexada).
    cn = {t["clave"]: t for t in r.TEMAS_CN}
    ok(all(t.get("cn") for t in r.TEMAS_CN), "todo tema chino lleva cn=True")
    for ciudad in ("Hong Kong", "Taiwan", "Shanghai", "Beijing", "Guangzhou", "Shenzhen", "Wuhan", "Tianjin"):
        ok('AFF:"%s"' % ciudad in r.AFF_CN, "AFF_CN incluye %s" % ciudad)
    con_aff = [k for k, t in cn.items() if k not in ("cn-preprints", "cn-revista-chi")]
    ok(con_aff and all(r.AFF_CN in cn[k]["epmc"] for k in con_aff),
       "los temas chinos por afiliacion usan el bloque AFF_CN entero (%s)" % con_aff)
    ok(not any('(AFF:"China") AND' in t["epmc"] for t in r.TEMAS_CN),
       "ningun tema chino se queda con AFF:\"China\" a secas")
    ok("cn-preprints" in cn and "SRC:PPR" in cn["cn-preprints"]["epmc"] and "AFF:" not in cn["cn-preprints"]["epmc"],
       "cn-preprints existe, es SRC:PPR y va SIN filtro de afiliacion")
    ok("cn-perfil" in cn and all(g in cn["cn-perfil"]["epmc"] for g in ("FGFR1", "CCND1", "FGF19", "FGFR4", "CDK2")),
       "cn-perfil pregunta por sus genes (nombre de gen, nunca banda: lo veta el test 7)")
    ok("cn-revista-chi" in cn and "LANG:chi" in cn["cn-revista-chi"]["epmc"]
       and 'ABSTRACT:"breast"' not in cn["cn-revista-chi"]["epmc"] and "TITLE:" in cn["cn-revista-chi"]["epmc"],
       "cn-revista-chi busca en LANG:chi por TITLE (muchas no traen abstract en ingles)")

    # --- B5 (20-sep-2026): etiquetas de calidad, derivadas de campos y no de juicio -------------
    E = r.ETIQUETAS
    et = r.etiquetas_calidad
    ok(et({"uid": "epmc:PPR:1", "tipo": "preprint", "titulo": "x"}) == [E["preprint"]], "preprint → sin peer review")
    ok(et({"uid": "ictrp:ChiCTR2600131806", "tipo": "ensayo", "ref": "ChiCTR2600131806", "resultados": False})
       == [E["registro-sin-resultados"]], "ChiCTR via ICTRP sin resultados → registro-CN sin resultados")
    ok(et({"uid": "ictrp:NCT07471776", "tipo": "ensayo", "ref": "NCT07471776", "resultados": False})
       == [E["registro-centros-cn"]], "NCT via ICTRP → centros en China, NO registro-CN")
    ok(et({"uid": "ictrp:ChiCTR2600131806", "tipo": "ensayo", "ref": "ChiCTR2600131806", "resultados": True})
       == [], "un registro CON resultados no se etiqueta 'sin resultados'")
    ok(et({"uid": "ctgov:NCT1:R", "tipo": "ensayo", "paises": ["China"], "alloc": "RANDOMIZED", "n": 300,
           "sponsor": "INDUSTRY"}) == [E["rct-cn"]], "RCT solo en China → validez externa limitada")
    ok(et({"uid": "ctgov:NCT2:R", "tipo": "ensayo", "paises": ["China"], "alloc": "NA", "n": 20,
           "sponsor": "OTHER"}) == [E["iit-cn"]], "1 brazo, academico, n<30, solo China → IIT-CN")
    ok(et({"uid": "ctgov:NCT3:R", "tipo": "ensayo", "paises": ["China"], "alloc": "NON_RANDOMIZED", "n": 200,
           "sponsor": "INDUSTRY"}) == [E["poblacion-cn"]], "no-RCT grande solo en China → poblacion-CN unica")
    ok(et({"uid": "ctgov:NCT4:R", "tipo": "ensayo", "paises": ["China", "Spain"], "alloc": "RANDOMIZED",
           "n": 300, "sponsor": "INDUSTRY"}) == [], "un ensayo China+Espana NO se etiqueta de poblacion china")
    ok(et({"uid": "ctgov:NCT5:R", "tipo": "ensayo", "paises": [], "alloc": "RANDOMIZED", "n": 30,
           "sponsor": "OTHER"}) == [], "sin paises no se afirma poblacion (mejor sin etiqueta que falsa)")
    ok(et({"uid": "epmc:MED:1", "tipo": "paper", "revista": "Zhonghua Zhong Liu Za Zhi"}) == [E["revista-cn"]],
       "revista en chino por nombre de revista")
    ok(et({"uid": "epmc:MED:2", "tipo": "paper", "revista": "Lancet Oncol"}, {"clave": "cn-revista-chi"})
       == [E["revista-cn"]], "todo lo del tema LANG:chi lleva [revista-CN]")
    ok(et({"uid": "onco:x", "tipo": "company", "titulo": "x"}) == [E["pr"]], "nota de empresa → PR, no evidencia")
    ok(et({"uid": "epmc:MED:3", "tipo": "paper", "revista": "Nature"}, {"clave": "cn-adc", "cn": True}) == [],
       "un paper chino en revista internacional no lleva etiqueta que el dato no sostenga")

    # la etiqueta viaja: al digest y a la cola
    res_cn = _res([])
    res_cn["temas"].append({"clave": "cn-adc", "titulo": "China ADC", "prio": "abierta", "cn": True,
                            "hits": [dict(_hit("cn1"), calidad=[E["preprint"]], tipo="preprint")], "errores": []})
    ok(E["preprint"] in r.digest_md(res_cn), "el digest imprime la etiqueta de calidad")
    r.encola(res_cn)
    lead_cn = next(p for p in r.lee_cola()["pendientes"] if p["uid"] == "cn1")
    ok(lead_cn.get("cn") is True and lead_cn.get("calidad") == [E["preprint"]],
       "la cola guarda cn y calidad del lead")

    # --- B5 gate lead → Radar: lo chino no sube sin ID abierto + poblacion + PMID/DOI ----------
    ok(r.gate_radar_cn({"uid": "epmc:MED:9", "tema": "fgfr4"}, "lo que sea") == (True, []),
       "el gate no aplica a un lead no chino")
    okg, faltan = r.gate_radar_cn(lead_cn, "abierto: dice X")
    ok(not okg and len(faltan) == 3, "un lead chino sin nada: faltan las 3 cosas (%s)" % len(faltan))
    okg, faltan = r.gate_radar_cn(lead_cn, "NCT07471776 abierto; población-CN única; sin DOI")
    ok(not okg and faltan == ["PMID o DOI cotejado"], "con ID y poblacion pero sin PMID/DOI sigue cerrado")
    okg, faltan = r.gate_radar_cn(lead_cn, "ChiCTR2600131806 abierto; población mixta; PMID 41688207 cotejado")
    ok(okg and faltan == [], "con ID + poblacion + PMID el gate abre")
    ok(r.gate_radar_cn(lead_cn, "NCT07471776; población-CN única; 10.1000/abc123")[0], "un DOI vale como (c)")

    class R:
        ref, veredicto, radar = "cn1", "abierto: dice X", True
    ok(r.cmd_cerrar(R) == 3, "cerrar --radar un lead chino sin las 3 cosas se RECHAZA")
    ok(any(p["uid"] == "cn1" for p in r.lee_cola()["pendientes"]), "y el lead sigue en la cola")

    class R2:
        ref, veredicto, radar = "cn1", "ChiCTR2600131806 abierto; población-CN única; PMID 41688207", True
    ok(r.cmd_cerrar(R2) == 0, "con las 3 cosas, cerrar --radar funciona")
    cerrado = next(p for p in r.lee_cola()["cerrados"] if p["uid"] == "cn1")
    ok(cerrado.get("radar") is True, "el cerrado queda marcado como subido a Radar")

    class R3:
        ref, veredicto, radar = "cn1", "abierto: dice X", False
    r.encola(res_cn)   # cn1 ya esta cerrado: no reentra
    ok(not any(p["uid"] == "cn1" for p in r.lee_cola()["pendientes"]), "un lead subido a Radar no reentra")

    # --- B4 (20-sep-2026): ChinaXiv FUERA del barrido diario, e ICTRP DENTRO -------------------
    def src(nombre):
        with open(os.path.join(ROOT, "tools", nombre), encoding="utf-8") as fh:
            return fh.read()
    for nombre in ("radar_ned_diario.py", "radar_cn_vps.py", "radar_ned_dia.sh", "radar_navegador.py"):
        ok("chinaxiv.org" not in src(nombre).lower(), "%s no consulta ChinaXiv" % nombre)
    ok(not any("chinaxiv" in n.lower() for n, _, _ in r.FUENTES), "ChinaXiv no es fuente del barrido")
    sh = src("radar_ned_dia.sh")
    ok("radar_cn_vps.py ictrp perfil" in sh and "--fuente ictrp" in sh,
       "el ciclo diario corre ICTRP por el VPS y lo ingiere")
    ok("no se finge" in sh, "si ICTRP no responde, el .sh lo DICE en vez de fingir cobertura")

    # --- TERAPIA PERSONALIZADA, familia entera (20-sep-2026) ----------------------------------
    # Encargo de {{TITULAR}}: "terapia personalizada es lo que mas me interesa... que se monitorice
    # continuamente". Tres piezas: (1) TIL tenia cero cobertura propia; (2) cada lead sale con su
    # familia; (3) una familia >30 dias a cero se DICE (consulta mal escrita / fuente cambiada /
    # de verdad nada: hoy no se distinguian). Todo sin red: fixtures y dobles.
    mod = {t["clave"]: t for t in r.TEMAS_MODALIDAD}
    ok("til" in mod, "existe el tema TIL")
    ok(all(w in mod["til"]["epmc"] for w in ("lifileucel", "adoptive cell", "infiltrating lymphocyte")),
       "TIL pregunta por lifileucel, ACT y linfocitos infiltrantes en Europe PMC")
    ok('ABSTRACT:"adoptive"' in mod["til"]["epmc"] and 'ABSTRACT:"tumor-infiltrating lymphocyte"' in mod["til"]["epmc"],
       "TIL exige contexto de TERAPIA junto a la palabra (TILs como biomarcador no es terapia)")
    ok("lifileucel" in mod["til"]["ctgov"] and "infiltrating lymphocytes" in mod["til"]["ctgov"],
       "TIL pregunta a ClinicalTrials.gov")
    ok("biespecifico" in mod and "T-cell engager" in mod["biespecifico"]["epmc"]
       and "engager" in mod["biespecifico"]["ctgov"], "los engagers de celulas T se vigilan por plataforma")
    ok('"CAR-NK"' in mod["celular"]["ctgov"], "CAR-NK entra en los ensayos de terapia celular")
    ok(all(w in cn["cn-celular"]["epmc"] for w in ("lifileucel", "TIL therapy", "adoptive cell"))
       and "TIL" in cn["cn-celular"]["ctgov"], "la capa china de terapia celular tambien cubre TIL")
    ok(all(w in mod["vacunas"]["epmc"] for w in ("dendritic cell vaccine", "oncolytic")),
       "celulas dendriticas y virus oncoliticos siguen cubiertos")
    import borde
    for k in ("til", "biespecifico"):
        for q, dest in ((mod[k]["epmc"], "europepmc"), (mod[k]["ctgov"], "clinicaltrials.gov")):
            okb, motivo = borde.egress_cientifico(q, destino=dest)
            ok(okb, "el borde deja salir la consulta de %s a %s (%s)" % (k, dest, motivo))

    # (2) clasificador de modalidad por titulo: el primer patron manda; sin patron, [otro]
    M = r.modalidad
    ok(M("Lifileucel in advanced melanoma") == "TIL", "lifileucel → TIL")
    ok(M("Adoptive cell therapy with expanded TILs in metastatic breast cancer") == "TIL", "ACT con TILs → TIL")
    ok(M("[RECRUITING] TIL Injection for the Treatment of Metastatic Solid Tumors") == "TIL", "TIL + treatment → TIL")
    ok(M("Tumor-infiltrating lymphocytes predict response to neoadjuvant chemotherapy") == "otro",
       "TILs como biomarcador NO es terapia → otro")
    ok(M("CAR-T cells targeting TROP2 in solid tumors") == "CAR-T", "CAR-T")
    ok(M("Chimeric antigen receptor natural killer cells") == "CAR-NK", "CAR-NK")
    ok(M("TCR-T therapy for solid tumors") == "TCR-T", "TCR-T")
    ok(M("Tarlatamab in extrapulmonary neuroendocrine carcinoma") == "biespecífico", "engager {{DIANA2}} → biespecifico")
    ok(M("Oncolytic virus combined with a peptide vaccine") == "virus-oncolítico", "oncolitico antes que vacuna")
    ok(M("Personalized mRNA neoantigen vaccine") == "vacuna", "vacuna")
    ok(M("Dato-DXd in HR-positive HER2-negative breast cancer") == "otro", "un ADC es [otro]")
    ok(M("") == "otro" and M(None) == "otro", "sin titulo → otro, no revienta")
    ok(set(r.FAMILIA) == {"CAR-T", "CAR-NK", "TCR-T", "TIL", "biespecífico", "virus-oncolítico", "vacuna"},
       "la FAMILIA vigilada es la de terapia personalizada, sin [otro]")

    # cada hit del barrido sale con su modalidad (doble de fuente: sin red)
    fuentes_reales = r.FUENTES
    r.FUENTES = [("doble", lambda tema, d, h, **kw: ([dict(_hit("dbl-" + tema["clave"]),
                                                          titulo="Lifileucel study")], None), {})]
    try:
        res_b, _ = r.barrido(dias=1, solo_tema="til")
    finally:
        r.FUENTES = fuentes_reales
    ok(res_b["temas"] and res_b["temas"][0]["hits"][0].get("modalidad") == "TIL",
       "barrido() etiqueta cada hit con su modalidad")
    # y el digest la imprime en cada lead
    res_m = _res([dict(_hit("m-til"), titulo="Lifileucel in melanoma", modalidad="TIL")])
    d = r.digest_md(res_m)
    ok("[TIL]" in d, "el digest imprime [TIL] junto al lead")
    ok("recuento por modalidad" not in d, "sin recuento30 el digest no inventa la seccion")
    res_m["recuento30"] = {"TIL": 4, "vacuna": 0}
    res_m["cobertura"] = ["⚠️ [vacuna] prueba"]
    d = r.digest_md(res_m)
    ok("recuento por modalidad" in d and "| [TIL] | 1 | 4 |" in d and "| [vacuna] | 0 | 0 |" in d,
       "el digest cierra con la tabla hoy / 30 dias")
    ok("- ⚠️ [vacuna] prueba" in d, "el digest imprime las lineas de cobertura")

    # (3) historial + recuento 30 dias + cobertura
    from datetime import date, timedelta
    hoy = date(2026, 10, 25)
    if os.path.exists(r.HISTORIAL):
        os.remove(r.HISTORIAL)
    h = r.registra_historial(res_m)
    ok(h["dias"]["2026-09-18"]["TIL"] == 1 and os.path.exists(r.HISTORIAL), "registra_historial guarda el dia")
    h = r.registra_historial(res_m)
    ok(r.carga_historial()["dias"]["2026-09-18"]["TIL"] == 2, "una segunda pasada SUMA, no sustituye")
    ok(r.registra_historial(res_m, persistir=False)["dias"]["2026-09-18"]["TIL"] == 3
       and r.carga_historial()["dias"]["2026-09-18"]["TIL"] == 2, "--dry calcula pero no toca disco")

    def hist(**por_fecha):
        return {"dias": {f.replace("_", "-"): dict({k: 0 for k in r.FAMILIA + ["otro"]}, **v)
                         for f, v in por_fecha.items()}}
    h30 = hist(**{"2026_09_20": {"TIL": 2}, "2026_09_26": {"TIL": 1, "vacuna": 3}, "2026_10_25": {"CAR-T": 1}})
    c = r.recuento_30(h30, hoy)
    ok(c["TIL"] == 1 and c["vacuna"] == 3 and c["CAR-T"] == 1,
       "recuento_30 suma solo lo de los ultimos 30 dias (26-sep entra, 20-sep no) (%s)" % c)
    ok(r.cobertura({"dias": {}}, hoy) and "primer barrido" in r.cobertura({"dias": {}}, hoy)[0],
       "sin historial: lo dice, no avisa de 7 familias a cero")
    corto = hist(**{"2026_10_21": {"vacuna": 1}, "2026_10_25": {"vacuna": 2}})
    lineas = r.cobertura(corto, hoy)
    ok(any("[TIL]" in l and "aun no se puede afirmar" in l and "5 dias" in l for l in lineas)
       and not any("mas de 30" in l and "lleva" in l for l in lineas),
       "historial corto: no afirma '>30 dias', dice cuantos dias hay")
    ok(not any("[vacuna]" in l for l in lineas), "la familia con lead no sale en cobertura")
    largo = hist(**{"2026_09_10": {"TIL": 1, "CAR-T": 1}, "2026_09_20": {"TIL": 1},
                    "2026_10_01": {"vacuna": 1}, "2026_10_25": {"vacuna": 1, "CAR-T": 1}})
    lineas = r.cobertura(largo, hoy)
    til = [l for l in lineas if "[TIL]" in l]
    ok(len(til) == 1 and "lleva 35 dias sin un solo lead" in til[0] and "corrio 2 dia(s)" in til[0],
       "TIL 35 dias a cero: lo dice con dias y con cuantas veces corrio el radar (%s)" % til)
    ok(not any("[CAR-T]" in l or "[vacuna]" in l for l in lineas), "CAR-T y vacuna con lead reciente no avisan")
    ok(any("[TCR-T]" in l and "mas de 30" in l for l in lineas), "una familia que NUNCA trajo nada avisa")
    completo = hist(**{"2026_10_25": {k: 1 for k in r.FAMILIA}})
    ok(r.cobertura(completo, hoy) == [], "toda la familia con lead → sin avisos")

    # reconstruccion desde los digests ya escritos (para que el recuento no arranque de cero)
    rdir = os.path.join(_TMP, "radar_fix")
    os.makedirs(rdir, exist_ok=True)
    with open(os.path.join(rdir, "Radar-NED-diario-2026-09-18.md"), "w") as f:
        f.write("# x\n\n- **paper** · 2026-09-17 · Lifileucel in melanoma ([10.1/a](https://doi.org/10.1/a))\n"
                "- **ensayo** · 2026-09-18 · [RECRUITING] CAR-T for solid tumors ([NCT1](https://x/NCT1)) ⚠️ otro subtipo\n"
                "- **paper** · 2026-09-18 · Dato-DXd in HR+ ([10.1/b](https://doi.org/10.1/b))\n")
    with open(os.path.join(rdir, "Radar-NED-diario-2026-09-19.md"), "w") as f:
        f.write("- **preprint** · 2026-09-19 · mRNA neoantigen vaccine ([10.1/c](https://doi.org/10.1/c)) [preprint]\n")
    with open(os.path.join(rdir, "otro-fichero.md"), "w") as f:
        f.write("- **paper** · 2026-09-19 · Lifileucel ([10.1/d](https://x))\n")
    rh = r.reconstruye_historial(rdir)
    ok(rh["dias"].get("2026-09-18", {}).get("TIL") == 1 and rh["dias"]["2026-09-18"]["CAR-T"] == 1
       and rh["dias"]["2026-09-18"]["otro"] == 1 and rh["dias"].get("2026-09-19", {}).get("vacuna") == 1
       and len(rh["dias"]) == 2, "reconstruye el historial desde los digests (solo Radar-NED-diario-*.md) (%s)" % rh)
    ok(r.reconstruye_historial(os.path.join(_TMP, "no-existe")) == {"dias": {}}, "sin carpeta → historial vacio")
    ok("registra_historial" in src("radar_navegador.py") and "rn.modalidad(" in src("radar_navegador.py"),
       "el carril de navegador (CTIS/ICTRP) tambien etiqueta y suma al historial")

    # ── Un DOI no es texto de markdown (20-sep-2026) ─────────────────────────────────────────
    # `_limpia` quita `_` porque en un TITULO es cursiva. Aplicado al DOI se lo comia:
    # `10.1007/82_2026_356` entraba a la cola como `10.1007/822026356`, que doi.org devuelve 404,
    # y el lead quedaba IRRESOLUBLE: quien lo triara no podia abrir la fuente, asi que o lo
    # cerraba a ciegas o lo dejaba pudrirse. Cazado vaciando la cola de 20 leads.
    ok(r._limpia_doi("10.1007/82_2026_356") == "10.1007/82_2026_356",
       "el DOI con guion bajo sobrevive entero")
    ok(r._limpia("10.1007/82_2026_356") != "10.1007/82_2026_356",
       "_limpia (la de titulos) SI se lo come: por eso hacen falta dos funciones")
    for bueno in ("10.1186/s13058-026-02295-8", "10.64898/2026.09.17.752386",
                  "10.1016/j.gene.2026.150394", "10.1007/s10585-026-10431-z"):
        ok(r._limpia_doi(bueno) == bueno, "DOI real intacto: %s" % bueno)
    ok(r._limpia_doi("  10.1016/j.gene.2026.150394\n") == "10.1016/j.gene.2026.150394",
       "recorta los extremos")
    # Sigue siendo dato EXTERNO y acaba dentro de una URL: lo que no es un DOI no se arregla a
    # medias, se tira. Colapsar los espacios internos dejaba pasar `10.1007/x ; rm -rf /`,
    # porque `;`, `-` y `/` son legales en un DOI.
    for malo in ("no-es-un-doi", "javascript:alert(1)", "10.1007/82_2026_356 ; rm -rf /",
                 "10.1234/x\nhttps://otro-sitio", "", "10.x/y"):
        ok(r._limpia_doi(malo) == "", "basura descartada: %r" % malo[:30])
    ok(r._limpia_doi(None) == "", "un doi ausente no revienta")

    # Y el caso que caza el MUTANTE de verdad: no basta con que `_limpia_doi` exista, el lead
    # tiene que SALIR con el DOI entero. Si alguien vuelve a poner `_limpia(r.get("doi"))` en
    # `fuente_epmc`, esto se pone rojo; probar la funcion aislada no lo veria.
    _get_real = r._get
    r._get = lambda url, params: {"resultList": {"result": [{
        "source": "MED", "id": "42754664", "doi": "10.1007/82_2026_356",
        "title": "Resolving Cancer: Specialized Pro-resolving Mediators",
        "firstPublicationDate": "2026-09-19", "journalTitle": "Curr Top Microbiol Immunol"}]}}
    try:
        hits, _err = r.fuente_epmc({"clave": "t", "epmc": "x"}, "2026-09-01", "2026-09-30")
    finally:
        r._get = _get_real
    ok(len(hits) == 1, "el lead de prueba entra")
    if hits:
        ok(hits[0]["ref"] == "10.1007/82_2026_356",
           "el lead guarda el DOI ENTERO, con sus guiones bajos")
        ok(hits[0]["url"] == "https://doi.org/10.1007/82_2026_356",
           "y la url que se le ofrece a quien lo tria RESUELVE")

    # ── El veredicto de un cierre NO es un titulo (20-sep-2026) ──────────────────────────────
    # `cmd_cerrar` guardaba `_limpia(veredicto)[:400]`, pero `_limpia` ya corta a
    # MAX_TITULO=160, asi que el [:400] era decorativo: TODOS los veredictos se guardaban
    # cortados justo por donde empieza la cita literal que los justifica. 39 quedaron asi, y el
    # rastro de POR QUE se descarto un ensayo dejo de ser auditable. Lo cazo el comite de
    # verificacion. Misma clase que el bug del DOI: reusar el saneador de titulos para lo que
    # no es un titulo.
    _largo = ("DESCARTADO por dos criterios citados de la fuente abierta hoy. " + "x" * 400 +
              " criterio literal al final que tiene que SOBREVIVIR.")
    _guardado = r._limpia_largo(_largo)[:2000]
    ok(len(_guardado) > 400, "un veredicto largo no se corta a la longitud de un titulo")
    ok(_guardado.endswith("que tiene que SOBREVIVIR."),
       "la cita literal del final del veredicto sobrevive")
    ok(len(r._limpia(_largo)) == r.MAX_TITULO,
       "_limpia (la de titulos) SI corta a MAX_TITULO: por eso no vale aqui")
    ok(r._limpia_largo("una\nlinea\tcon  control") == "una linea con control",
       "_limpia_largo sigue saneando saltos, tabuladores y espacios dobles")

    print("test_radar_ned_diario: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
