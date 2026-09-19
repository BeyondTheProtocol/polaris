#!/usr/bin/env python3
"""tools/elegibilidad_ensayos.py — L3: «CT.gov nunca confirma, solo descarta».

La VERDAD de elegibilidad para un ensayo NO vive en ClinicalTrials.gov: que un ensayo
siga «RECRUITING» NO significa que la paciente sea elegible. El caso PNV21 lo prueba —
CT.gov decía RECRUITING y aun así la descalificaron en el centro. La elegibilidad la
confirma el CENTRO (una confirmación HUMANA con TTL corto = `confirmado_en`). CT.gov solo
sirve para DESCARTAR: si el ensayo no existe / está cerrado → señal de descarte.

Para piezas de ensayo (bloque B5, o con un NCT en `fuentes`, o que hablan de elegibilidad):
  · CT.gov dice cerrado/inexistente → DESCARTE (flag; el ensayo ya no está).
  · CT.gov no resoluble → fail-closed (flag).
  · CT.gov dice abierto → NO confirma elegibilidad. Si no hay confirmación humana FRESCA
    del centro (`confirmado_en` dentro de `ttl_dias`) → flag «elegibilidad no confirmada».
  · confianza «alta» sin confirmación del centro → flag (CT.gov no la otorga).

Principio #0: CT.gov es señal externa, NUNCA veredicto de elegibilidad. fail-closed.

⚠️ LÍMITE CONOCIDO v0.1 (honestidad: avisar, no fingir cobertura): este módulo NO lee el
TABLERO de ensayos (fuente única de estado 🔴/load-bearing). Hoy solo cruza CT.gov (descarte)
con el `confirmado_en` de la propia pieza. Consecuencia: la protección PNV21 depende de que
NADIE meta un `confirmado_en` espurio; un ensayo marcado 🔴-en-el-Tablero con fecha fresca
inventada PASARÍA. Aceptable como v0.1 porque el Tablero aún es prosa, no JSONL. Follow-up:
cablear el Tablero cuando sea estructurado (ver doc Reglas-clinicas-PENDIENTE-{{CONTACTO}}).
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_NCT_RE = re.compile(r'NCT\d{8}', re.I)
# Estados de CT.gov que DESCARTAN (el ensayo ya no recluta / no existe).
_DESCARTE = {"inexistente", "cerrado", "terminado", "retirado", "completado", "suspendido"}


def _es_pieza_ensayo(p):
    af = (p.get("afirmacion") or "").lower()
    fuentes = " ".join(p.get("fuentes") or [])
    return (p.get("bloque") == "B5" or bool(_NCT_RE.search(fuentes))
            or "elegib" in af or "ensayo" in af or "trial" in af)


def _ncts(p):
    return sorted(set(m.group(0).upper() for m in _NCT_RE.finditer(
        " ".join(p.get("fuentes") or []) + " " + (p.get("afirmacion") or ""))))


def _fresca(confirmado_en, ttl_dias, ahora):
    if not confirmado_en:
        return False
    try:
        t = datetime.fromisoformat(str(confirmado_en).replace("Z", "+00:00"))
        if not t.tzinfo:
            t = t.replace(tzinfo=timezone.utc)
        ttl = int(ttl_dias)
    except (ValueError, TypeError):
        return False
    return (t + timedelta(days=ttl)) >= ahora


def _comprobador_real(ncts):
    """Default: estado de CT.gov vía verifica_citas (existe→'abierto', no_existe→'inexistente').
    NOTA: es un proxy grueso (existencia, no overallStatus). Inyecta un comprobador fino
    en producción/tests. Si no se puede importar → fail-closed (todo 'no_resoluble')."""
    try:
        from verifica_citas import verifica
    except Exception:
        return {n: "no_resoluble" for n in ncts}
    out = {}
    for r in verifica(ncts):
        est = r.get("estado")
        out[r.get("id")] = ("abierto" if est == "existe"
                            else "inexistente" if est == "no_existe" else "no_resoluble")
    return out


def evaluar_elegibilidad(piezas, ahora=None, comprobador_ct=None):
    """Aplica la regla a las piezas de ensayo. Devuelve lista de bloqueos (strings)."""
    if ahora is None:
        ahora = datetime.now(timezone.utc)
    comp = comprobador_ct or _comprobador_real
    bloqueos = []
    for p in piezas:
        if not isinstance(p, dict) or p.get("status") != "vivo" or not _es_pieza_ensayo(p):
            continue
        pid = p.get("id", "(sin id)")
        ncts = _ncts(p)
        estados = comp(ncts) if ncts else {}
        # 1) CT.gov solo DESCARTA
        for n in ncts:
            est = estados.get(n, "no_resoluble")
            if est in _DESCARTE:
                bloqueos.append("DESCARTE: ensayo %s de «%s» no disponible en CT.gov (%s) → "
                                "no elegible / revisar" % (n, pid, est))
            elif est == "no_resoluble":
                bloqueos.append("no se pudo comprobar %s de «%s» en CT.gov → fail-closed" % (n, pid))
        # 2) CT.gov NO confirma: la elegibilidad la confirma el CENTRO (humano + TTL)
        if not _fresca(p.get("confirmado_en"), p.get("ttl_dias"), ahora):
            bloqueos.append("«%s»: elegibilidad NO confirmada por el centro (CT.gov no confirma "
                            "elegibilidad —ni 'RECRUITING'—; solo una confirmación humana fresca "
                            "del centro la otorga). [regla PNV21]" % pid)
        # 3) no se puede declarar «alta» sin la confirmación del centro
        if p.get("confianza") == "alta" and not _fresca(p.get("confirmado_en"), p.get("ttl_dias"), ahora):
            bloqueos.append("«%s»: confianza ALTA en elegibilidad sin confirmación fresca del "
                            "centro → CT.gov no la justifica" % pid)
    return bloqueos


def main(argv):
    if not argv:
        print("uso: elegibilidad_ensayos.py <dosier.jsonl>")
        return 2
    import frescura_dosier as fd
    piezas, errores = fd.cargar_jsonl(argv[0])
    if errores:
        for e in errores:
            print("  ✗ %s" % e)
        print("FORMATO INVÁLIDO → fail-closed")
        return 2
    bloqueos = evaluar_elegibilidad(piezas)
    print("=== L3 · elegibilidad de ensayos (CT.gov solo descarta) ===")
    if not bloqueos:
        print("✅ sin descartes ni elegibilidades sin confirmar")
        return 0
    print("🛑 elegibilidad NO entregable (fail-closed):")
    for b in bloqueos:
        print("   - %s" % b)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
