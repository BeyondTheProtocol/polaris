#!/usr/bin/env python3
"""tools/guardian_evals.py — L9: el banco que VERIFICA AL VERIFICADOR.

No basta con que el Guardián «corra»: hay que MEDIR que CAZA los errores. Sobre un dosier
base VÁLIDO, planta MUTACIONES (quita un requerido, mete un campo desconocido, duplica un
id vivo = contradicción, caduca una fecha, rompe un enum, mete una pieza no-objeto) y exige
que el Guardián (L1 + frescura) marque cada mutación como NO entregable. En la clase crítica,
catch-rate = 100%. Más un CANARIO: un dosier malo conocido que SIEMPRE debe fallar; si el
Guardián lo bendice, algo se rompió dentro de él.

Principio #0 / fail-closed. Códigos de salida:
  0 = base limpia + todas las mutaciones cazadas + canario cazado (Guardián SANO)
  1 = alguna mutación o el canario NO cazados → AGUJERO en el Guardián
  2 = la base no es entregable → no se puede medir (fail-closed)
"""
import copy
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dosier_invariantes as di

# Regla de dominio SINTÉTICA (no clínica): prueba el MECANISMO de invariantes de dominio sin
# activar ninguna regla clínica real (las clínicas están en cuarentena pendiente de {{CONTACTO}}).
# El banco la inyecta en el checker; el contenido clínico nunca se enforca aquí.
_DOMINIO_SINTETICO = [("MARCADORTEST", "ENSAYOTEST", "regla sintética para probar el mecanismo")]


def base_valido(ahora):
    """Un dosier LIMPIO y entregable (relativo a `ahora`)."""
    conf = (ahora - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        {"id": "A", "bloque": "B1", "afirmacion": "dato genérico A", "fuentes": ["s1"],
         "confianza": "media", "status": "vivo", "confirmado_en": conf, "ttl_dias": 30},
        {"id": "B", "bloque": "B2", "afirmacion": "dato genérico B", "fuentes": ["s2"],
         "confianza": "alta", "status": "vivo", "confirmado_en": conf, "ttl_dias": 60},
    ]


def _m_campo_desconocido(ps):
    ps[0]["intruso"] = "x"; return ps


def _m_falta_requerido(ps):
    del ps[0]["afirmacion"]; return ps


def _m_enum_malo(ps):
    ps[0]["status"] = "zombi"; return ps


def _m_contradiccion(ps):
    ps.append(dict(ps[0])); return ps  # mismo id, dos vivas


def _m_caducado(ps):
    ps[0]["confirmado_en"] = "2026-01-01T00:00:00Z"; ps[0]["ttl_dias"] = 1; return ps


def _m_no_objeto(ps):
    ps[0] = "no soy un objeto"; return ps


def _m_dominio(ps):  # marcador SINTÉTICO con su ensayo HUÉRFANO → invariante de dominio (mecanismo)
    ps.append({"id": "MKR", "bloque": "B2", "afirmacion": "marcador MARCADORTEST presente",
               "fuentes": ["s"], "confianza": "media", "status": "vivo",
               "confirmado_en": ps[0]["confirmado_en"], "ttl_dias": 30}); return ps


def _m_vacio(ps):  # dosier VACÍO: nada vivo que entregar → debe bloquear (fail-closed central #1)
    return []


def _m_todo_superado(ps):  # todas las piezas a 'superado': ninguna viva → debe bloquear (#1)
    for p in ps:
        p["status"] = "superado"
    return ps


def _m_elegibilidad(ps):  # ensayo (B5+NCT); el stub de CT.gov lo da cerrado → descarte L3
    ps.append({"id": "B5x", "bloque": "B5", "afirmacion": "elegible para ensayo",
               "fuentes": ["NCT05098210"], "confianza": "media", "status": "vivo",
               "confirmado_en": ps[0]["confirmado_en"], "ttl_dias": 30}); return ps


MUTADORES = [
    ("campo_desconocido", _m_campo_desconocido),
    ("falta_requerido", _m_falta_requerido),
    ("enum_malo", _m_enum_malo),
    ("contradiccion", _m_contradiccion),
    ("caducado", _m_caducado),
    ("no_objeto", _m_no_objeto),
    ("dominio_huerfano", _m_dominio),
    ("elegibilidad_descartada", _m_elegibilidad),
    ("dosier_vacio", _m_vacio),
    ("todo_superado", _m_todo_superado),
]

# CANARIO: dosier malo conocido (contradicción) — SIEMPRE debe salir NO entregable.
def _canario(ahora):
    conf = (ahora - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = {"id": "DUP", "bloque": "B1", "afirmacion": "x", "fuentes": ["s"],
         "confianza": "media", "status": "vivo", "confirmado_en": conf, "ttl_dias": 30}
    return [p, dict(p)]  # dos vivas con el mismo id


def _checker_default(piezas, ahora):
    # CT.gov stub determinista (todo 'inexistente') + regla de dominio SINTÉTICA (no clínica):
    # el banco prueba el mecanismo sin red ni reglas clínicas activas.
    return di.evaluar(piezas, ahora,
                      comprobador_ct=lambda ncts: {n: "inexistente" for n in ncts},
                      invariantes_dominio=_DOMINIO_SINTETICO)


def _entregable(checker, piezas, ahora):
    return checker(piezas, ahora)[1]


def correr(ahora=None, checker=None):
    """Devuelve (resultados, sano, exit_code, resumen). checker(piezas, ahora)->(_,entregable,_)."""
    if ahora is None:
        ahora = datetime.now(timezone.utc)
    checker = checker or _checker_default
    resultados = []

    # 0) la base TIENE que ser entregable, si no, no se puede medir
    if not _entregable(checker, copy.deepcopy(base_valido(ahora)), ahora):
        return [], False, 2, "la base NO es entregable → no se puede medir (fail-closed)"

    cazadas = 0
    for nombre, fn in MUTADORES:
        ps = fn(copy.deepcopy(base_valido(ahora)))
        cazada = not _entregable(checker, ps, ahora)
        resultados.append({"mutacion": nombre, "cazada": cazada})
        if cazada:
            cazadas += 1

    canario_cazado = not _entregable(checker, _canario(ahora), ahora)
    resultados.append({"mutacion": "CANARIO", "cazada": canario_cazado})

    sano = (cazadas == len(MUTADORES)) and canario_cazado
    exit_code = 0 if sano else 1
    resumen = "catch-rate %d/%d mutaciones · canario %s" % (
        cazadas, len(MUTADORES), "cazado" if canario_cazado else "BENDECIDO (¡agujero!)")
    return resultados, sano, exit_code, resumen


def main(argv):
    resultados, sano, exit_code, resumen = correr()
    print("=== L9 · banco de pruebas del Guardián (verifica al verificador) ===")
    for r in resultados:
        print("  %s mutación '%s' %s" % (
            "✓" if r["cazada"] else "✗", r["mutacion"],
            "cazada" if r["cazada"] else "NO CAZADA (agujero)"))
    print(resumen)
    if exit_code == 0:
        print("✅ Guardián SANO (caza todo lo plantado)")
    elif exit_code == 2:
        print("🛑 no se pudo medir (base no entregable) → fail-closed")
    else:
        print("🛑 AGUJERO en el Guardián: algo plantado pasó sin cazar")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
