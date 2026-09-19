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

    print("test_radar_ned_diario: %d ok, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
