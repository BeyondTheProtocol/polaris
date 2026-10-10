#!/usr/bin/env python3
"""test_healthcheck_entrega.py — `_emitir_si_cambia` lee lo que `salida` hizo DE VERDAD (10-oct-26).

Bug: guardaba las claves como «ya avisadas» y devolvía True nada más llamar a `report_to_titular`, sin mirar su
resultado. Con el cupo diario agotado el aviso queda APLAZADO al parte; con HALT o un envío fallido ni siquiera
sale. Tres daños: (a) se daba por avisado un problema que {{TITULAR}} no vio y no se volvía a intentar; (b) un
«✅ Resuelto» de algo que nunca le llegó; (c) lo contrario, re-spamear cada ciclo.

Regla fijada aquí: entregado / aplazado / retenido = llega (se da por avisado, NO se repite cada ciclo);
bloqueado / fallo / resultado ilegible = no llegó (no se toca el estado, el siguiente ciclo reintenta, y el libro
de deuda no cuenta el reintento como una repetición). Aislado: salida FALSA, nada sale de verdad.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="hc_entrega_test_")
os.environ["BTP_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))

import healthcheck as hc   # noqa: E402
import salud               # noqa: E402
import salida              # noqa: E402
import deuda               # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── salida FALSA: devuelve el dict que diga `modo[prefijo]` (por defecto, entregado) ──────────────
RES = {
    "entregado": {"delivered": True, "blocked": False, "reason": "entregado (fake)"},
    "aplazado": {"delivered": False, "blocked": False, "reason": "aplazado al parte diario", "aplazado": True},
    "retenido": {"delivered": False, "blocked": False, "reason": "retenido (silencio nocturno)", "retenido": True},
    "operativo": {"delivered": False, "blocked": False, "reason": "operativo: guardado en log", "operativo": True},
    "bloqueado": {"delivered": False, "blocked": True, "reason": "HALT activo: salida en pausa total"},
    "ilegible": None,
}
_modo = {"resuelto": "entregado", "revision": "entregado"}
_sent = []


def _fake(texto, *a, **k):
    tipo = "resuelto" if str(texto).startswith("✅ Resuelto") else "revision"
    _sent.append(str(texto))
    return RES[_modo[tipo]]


salida.report_to_titular = _fake
hc._encolar_investigacion = lambda clave, texto: None

# libro de deuda aislado y contado: lo que importa es CUÁNTAS veces se anota, no el libro real.
_libro = {"abrir": [], "visto": [], "remitir": []}
deuda._cargar = lambda: {}
deuda.abrir = lambda clave, *a, **k: _libro["abrir"].append(clave)
deuda.visto = lambda clave, *a, **k: _libro["visto"].append(clave)
deuda.remitir = lambda clave, *a, **k: _libro["remitir"].append(clave)


def escenario(revision="entregado", resuelto="entregado"):
    """Estado limpio: HC y acuses nuevos, contadores a cero."""
    hc.HC = tempfile.mkdtemp(prefix="hc_", dir=_TMP)
    salud.HC = hc.HC
    salud.ACUSES = os.path.join(hc.HC, "acuses.json")
    _sent.clear()
    for v in _libro.values():
        v.clear()
    _modo["revision"], _modo["resuelto"] = revision, resuelto


def resueltos():
    return [m for m in _sent if m.startswith("✅ Resuelto")]


def revisiones():
    return [m for m in _sent if m.startswith("🩺")]


def estado():
    try:
        return json.load(open(os.path.join(hc.HC, "last_alert_state-humano.json"), encoding="utf-8"))
    except Exception:
        return {}


def aplazado_y_retenido_llegan():
    """Aplazado y retenido LLEGAN solos (parte / resumen de las 08:00): se dan por avisados, sin repetir."""
    for tipo in ("aplazado", "retenido"):
        escenario(revision=tipo, resuelto=tipo)
        ok(hc._emitir_si_cambia([("a", "fallo a")]) is True, "%s: llega solo → se da por avisado (True)" % tipo)
        ok("a" in estado().get("claves", []), "%s: la clave queda guardada como avisada" % tipo)
        n = len(_sent)
        ok(hc._emitir_si_cambia([("a", "fallo a")]) is False and len(_sent) == n,
           "%s: el ciclo siguiente NO re-avisa (anti-spam intacto)" % tipo)
        hc._emitir_si_cambia([])
        hc._emitir_si_cambia([])
        ok(len(resueltos()) == 1, "%s: al resolverse sale UN «Resuelto» (viaja en la misma cola, tras el aviso)" % tipo)


def bloqueado_no_se_da_por_avisado():
    """HALT / fallo de envío: {{TITULAR}} no lo vio → no se guarda, se reintenta, y no hay «Resuelto» fantasma."""
    escenario(revision="bloqueado", resuelto="bloqueado")
    ok(hc._emitir_si_cambia([("a", "fallo a")]) is False, "bloqueado: NO se devuelve True")
    ok("a" not in estado().get("claves", []), "bloqueado: la clave NO queda como ya avisada")
    _modo["revision"] = "entregado"
    n = len(revisiones())
    ok(hc._emitir_si_cambia([("a", "fallo a")]) is True and len(revisiones()) == n + 1,
       "bloqueado: en cuanto la salida vuelve, el MISMO problema se avisa (no se perdió)")
    # (b) no anunciar el cierre de algo que nunca llegó a ver
    escenario(revision="bloqueado", resuelto="entregado")
    hc._emitir_si_cambia([("a", "fallo a")])
    hc._emitir_si_cambia([])
    hc._emitir_si_cambia([])
    ok(resueltos() == [], "bloqueado: un problema que ella NO vio no genera «✅ Resuelto»")
    # ilegible (None) cuenta igual que bloqueado: no se puede afirmar que llegó
    escenario(revision="ilegible", resuelto="ilegible")
    ok(hc._emitir_si_cambia([("a", "fallo a")]) is False and "a" not in estado().get("claves", []),
       "resultado ilegible: no se da por avisado")


def reintentos_no_inflan_el_libro():
    """Reintentar un aviso que no llegó NO es 'repetirse' el fallo: el libro de deuda no debe escalar por eso."""
    escenario(revision="bloqueado")
    for _ in range(4):
        hc._emitir_si_cambia([("a", "fallo a")])
    ok(_libro["abrir"] == ["a"] and _libro["visto"] == [],
       "4 reintentos bloqueados: la clave se anota UNA vez y no se cuenta como repetición (abrir=%r visto=%r)"
       % (_libro["abrir"], _libro["visto"]))


def resuelto_bloqueado_se_reintenta_una_vez():
    """El «Resuelto» que no sale no se da por anunciado; cuando sale, sale UNA vez (ni se pierde ni se repite)."""
    escenario(revision="entregado", resuelto="bloqueado")
    hc._emitir_si_cambia([("a", "fallo a")])
    hc._emitir_si_cambia([])
    hc._emitir_si_cambia([])                      # intenta el Resuelto: bloqueado
    ok(estado().get("claves") == ["a"], "Resuelto bloqueado: el cierre NO se confirma (la clave sigue en la referencia)")
    _modo["resuelto"] = "entregado"
    hc._emitir_si_cambia([])
    ok(len(resueltos()) == 2, "Resuelto bloqueado: el siguiente ciclo lo reintenta (1 intento fallido + 1 bueno)")
    n = len(_sent)
    hc._emitir_si_cambia([])
    hc._emitir_si_cambia([])
    ok(len(_sent) == n, "…y una vez anunciado no se repite")


def resuelto_ok_con_aviso_bloqueado_no_se_repite():
    """Si el cierre SÍ llegó pero el aviso de lo que sigue activo no, el cierre no se vuelve a anunciar."""
    escenario(revision="entregado", resuelto="entregado")
    hc._emitir_si_cambia([("a", "fallo a"), ("b", "fallo b")])
    hc._emitir_si_cambia([("b", "fallo b")])      # 'a' ausente 1 ciclo
    _modo["revision"] = "bloqueado"
    hc._emitir_si_cambia([("b", "fallo b")])      # confirma 'a': Resuelto OK, digest de 'b' bloqueado
    ok(len(resueltos()) == 1, "el cierre de 'a' llegó")
    hc._emitir_si_cambia([("b", "fallo b")])
    hc._emitir_si_cambia([("b", "fallo b")])
    ok(len(resueltos()) == 1, "con el digest bloqueado, el cierre de 'a' NO se anuncia otra vez (spam)")


def operativo_va_al_log_y_cuenta():
    """Categoría operativa: su destino ES el log, así que 'operativo' es éxito; en la humana no lo es."""
    escenario()
    _modo["revision"] = "operativo"
    ok(hc._emitir_si_cambia([("a", "fallo a")], categoria="operativo") is True, "operativa: log operativo = avisado")
    escenario(revision="operativo")
    ok(hc._emitir_si_cambia([("a", "fallo a")]) is False, "humana: que caiga en el log NO es avisar a {{TITULAR}}")


def halt_levantado_no_repite_no_tengo_ejecutor():
    """Con HALT el aviso no sale y el acuse queda en «anotado; sin ejecutor». Al levantarse el HALT, el reintento
    NO puede repetir «no tengo ejecutor (el lazo está parado)» —ya falso— y debe encolar la investigación."""
    escenario(revision="bloqueado")
    encolados = []
    orig = hc._encolar_investigacion
    hc._encolar_investigacion = lambda clave, texto: (encolados.append(clave), None if len(encolados) == 1 else "job-77")[1]
    try:
        hc._emitir_si_cambia([("a", "fallo a")])          # HALT: no se encola (None) y el aviso no sale
        ok(salud.get("a")["nota"].startswith("anotado"), "HALT: el acuse queda «anotado; sin ejecutor»")
        _modo["revision"] = "entregado"                    # el lazo vuelve
        hc._emitir_si_cambia([("a", "fallo a")])
    finally:
        hc._encolar_investigacion = orig
    ok(len(encolados) == 2, "al volver el lazo se REINTENTA el encolado (acuse «anotado» obsoleto)")
    ok("job-77" in salud.get("a")["nota"], "el acuse pasa a «investigando (job…)»")
    ok(revisiones() and "lo estoy mirando" in revisiones()[-1] and "no tengo ejecutor" not in revisiones()[-1],
       "el aviso reintentado dice «lo estoy mirando», no la promesa vacía de antes: %r" % (revisiones()[-1:],))


def main():
    halt_levantado_no_repite_no_tengo_ejecutor()
    aplazado_y_retenido_llegan()
    bloqueado_no_se_da_por_avisado()
    reintentos_no_inflan_el_libro()
    resuelto_bloqueado_se_reintenta_una_vez()
    resuelto_ok_con_aviso_bloqueado_no_se_repite()
    operativo_va_al_log_y_cuenta()
    print("RESULTADO healthcheck_entrega: %d OK, %d fallos" % (_pass, _fail))
    print("✅ HEALTHCHECK_ENTREGA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
