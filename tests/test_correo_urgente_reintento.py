#!/usr/bin/env python3
"""test_correo_urgente_reintento.py — un correo urgente cuyo aviso NO llegó (HALT, fallo de red) se vuelve a avisar.

Hallazgo (10-oct-26): de 158 `aviso-imap` desde el 26-jun, 20 quedaron BLOQUEADOS por «HALT activo» (todos
`urgente:ned`, 13-jul → 17-sep) y nunca se reintentaron. Mecanismo, verificado: `correo.reclamar_aviso` marca el
correo como avisado ANTES de enviar; `correo_imap.procesar` descartaba el resultado y avanzaba `last_uid`;
`centinela_ned.detectar()` reclamaba la MISMA clave, así que el ciclo siguiente la veía reclamada, daba el correo por
avisado y SELLABA (las dos redes se tapaban).

Aquí se prueba el COMPORTAMIENTO con un IMAP falso, un `salida` falso con HALT conmutable y el `detectar()` REAL:
  1. HALT → llega correo NED → se levanta el HALT → SE AVISA (poller); no se repite después.
  2. Con el centinela real de por medio: el reclamo se libera, el centinela no sella, y al levantar el HALT sale UN
     aviso (el del que llegue primero; el otro no duplica).
  3. Fallo de red (excepción y entrega fallida) ídem.
  4. HALT largo con varios correos → UN aviso agrupado al volver, no N.
  5. Aplazado al parte cuenta como en camino (no se repite cada pasada).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="correo_reintento_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import correo        # noqa: E402
import correo_imap as ci   # noqa: E402
import centinela_ned as cn  # noqa: E402
import cumbre        # noqa: E402
import seguimiento   # noqa: E402
import salida        # noqa: E402

cumbre.foco = lambda: {"id": "f", "titulo": "foco fijo", "estado": "en_curso"}
seguimiento.recopilar = lambda: {"items": []}
ci.PEND_ESPERA_S = 0          # sin esperas en el test (la espera se prueba aparte)

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── salida FALSA con HALT conmutable ──────────────────────────────────────────────────────────
MODO = {"v": "entregado"}     # entregado | halt | red | excepcion | aplazado
ENVIADOS = []


def _fake_report(texto, *a, **k):
    m = MODO["v"]
    if m == "excepcion":
        raise ConnectionError("red caída")
    if m == "halt":
        return {"delivered": False, "blocked": True, "reason": "HALT activo: salida en pausa total"}
    if m == "red":
        return {"delivered": False, "blocked": True, "reason": "entrega fallida (URLError) → borrador"}
    if m == "aplazado":
        ENVIADOS.append(("aplazado", texto))
        return {"delivered": False, "blocked": False, "reason": "aplazado al parte", "aplazado": True}
    ENVIADOS.append(("entregado", texto))
    return {"delivered": True, "blocked": False, "reason": "entregado"}


salida.report_to_titular = _fake_report


def llegados():
    return [t for _m, t in ENVIADOS]


class FakeMailbox:
    def __init__(self, uidvalidity, mensajes):
        self.uidvalidity, self._m = uidvalidity, dict(mensajes)

    def all_uids(self):
        return sorted(self._m)

    def recent_uids(self, limit):
        return self.all_uids()[-limit:]

    def fetch_header(self, uid):
        return self._m[uid]


def hdr(frm, subject, mid):
    return {"from": frm, "subject": subject, "date": "Mon, 22 Jun 2026 13:53:00 +0200",
            "message_id": mid, "flags": ""}


NED = "{{CONTACTO}} {{CONTACTO}} <contacto.contacto@{{CENTRO}}.ch>"


def avisar(texto, urgente):
    return salida.report_to_titular(texto, urgente=urgente, fuente="correo-imap")


def limpiar():
    shutil.rmtree(os.path.join(os.environ["BTP_STATE_DIR"]), ignore_errors=True)
    os.makedirs(os.environ["BTP_STATE_DIR"], exist_ok=True)
    del ENVIADOS[:]
    MODO["v"] = "entregado"


def baseline():
    """Poller: primera pasada (baseline) con un correo viejo, y centinela: su primera pasada (línea base)."""
    mb = FakeMailbox(1, {10: hdr("news@promos.com", "oferta", "<old@x>")})
    ci.procesar(mb, alertar=True, avisar=avisar)
    cn.run()
    del ENVIADOS[:]
    return {10: hdr("news@promos.com", "oferta", "<old@x>")}


def pend():
    return ci._pend_cargar(ci._pend_path(ci.SEEN))["items"]


def reclamado(rem, asu):
    """¿Está la clave reclamada en el ledger? (reclamar y, si se pudo, deshacer)."""
    libre = correo.reclamar_aviso(rem, asu)
    if libre:
        correo.liberar_aviso(rem, asu)
    return not libre


# ── 1. HALT → llega correo NED → se levanta el HALT → se avisa (poller) ───────────────────────
def poller_halt():
    limpiar()
    base = baseline()
    MODO["v"] = "halt"
    base[11] = hdr(NED, "update biopsia", "<n1@x>")
    r = ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(r["avisos"] == 0 and llegados() == [], "poller/HALT: no se avisó (nada llegó)")
    ok(len(pend()) == 1, "poller/HALT: el correo queda PENDIENTE aunque last_uid avanzó")
    ok(not reclamado("contacto.contacto@{{CENTRO}}.ch", "update biopsia"),
       "poller/HALT: el reclamo del ledger se LIBERÓ (si no, nadie volvería a avisar)")
    MODO["v"] = "entregado"        # se levanta el HALT
    r = ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)   # sin correo nuevo: solo reintenta la cola
    ok(r["avisos"] == 1 and len(llegados()) == 1 and "{{CONTACTO}}" in llegados()[0],
       "poller/HALT levantado: el correo SE AVISA (sin reprocesar el buzón entero)")
    ok(pend() == [], "poller: la cola queda vacía tras avisar")
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(len(llegados()) == 1, "poller: entregado → NO se repite en pasadas siguientes")


# ── 2. con el centinela REAL de por medio ─────────────────────────────────────────────────────
def con_centinela():
    limpiar()
    base = baseline()
    MODO["v"] = "halt"
    base[11] = hdr(NED, "update biopsia", "<n1@x>")
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)     # el poller falla y libera
    cn.run()                                                           # ciclo 1 del centinela: HALT
    cn.run()                                                           # ciclo 2 (150 s después): NO puede callar
    ok(llegados() == [], "centinela/HALT: nada entregado")
    ok(not reclamado("contacto.contacto@{{CENTRO}}.ch", "update biopsia"),
       "centinela/HALT: sin reclamo colgado tras dos ciclos fallidos")
    MODO["v"] = "entregado"
    cn.run()
    ok(len(llegados()) == 1 and "te ha escrito" in llegados()[0],
       "centinela: al levantar el HALT SE AVISA del correo (los dos ciclos HALT no lo taparon): %r" % (llegados(),))
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)     # el poller reintenta: ya lo avisó el centinela
    ok(len(llegados()) == 1, "poller+centinela: el aviso NO se duplica (el reclamo compartido lo evita)")
    ok(pend() == [], "poller: descarta de la cola lo que otro ya avisó")
    cn.run(); cn.run()
    ok(len(llegados()) == 1, "centinela: entregado → no se repite")


# ── 3. fallo de red (excepción y entrega fallida) ─────────────────────────────────────────────
def fallo_red():
    for modo in ("excepcion", "red"):
        limpiar()
        base = baseline()
        MODO["v"] = modo
        base[11] = hdr(NED, "resultado biopsia", "<n2@x>")
        ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
        ok(len(pend()) == 1, "red/%s: el correo queda pendiente" % modo)
        cn.run()
        MODO["v"] = "entregado"
        cn.run()
        ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
        ok(len(llegados()) == 1, "red/%s: al volver la red SE AVISA exactamente una vez: %r" % (modo, llegados()))


# ── 4. HALT largo con varios correos → UN aviso agrupado ──────────────────────────────────────
def halt_largo_agrupa():
    limpiar()
    base = baseline()
    MODO["v"] = "halt"
    for n in range(5):
        base[20 + n] = hdr(NED, "update biopsia %d" % n, "<m%d@x>" % n)
        ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(len(pend()) == 5, "HALT largo: los 5 correos urgentes siguen pendientes")
    MODO["v"] = "entregado"
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(len(llegados()) == 1 and "5 correos urgentes" in llegados()[0],
       "al volver: UN solo aviso agrupado, no 5: %r" % (llegados(),))
    ok(pend() == [], "y la cola queda vacía")
    # más de PEND_LISTA: se nombra el tope y se dice cuántos más
    limpiar()
    base = baseline()
    MODO["v"] = "halt"
    for n in range(12):
        base[40 + n] = hdr(NED, "urgente %d" % n, "<k%d@x>" % n)
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    MODO["v"] = "entregado"
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(len(llegados()) == 1 and "12 correos" in llegados()[0] and "y 4 más" in llegados()[0],
       "12 acumulados: un aviso con el tope de 8 nombrados y «y 4 más»")


# ── 5. espaciado, aplazado y reintentos fallidos ──────────────────────────────────────────────
def espaciado_y_aplazado():
    limpiar()
    base = baseline()
    ci.PEND_ESPERA_S = 3600
    MODO["v"] = "halt"
    base[11] = hdr(NED, "update biopsia", "<n1@x>")
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    MODO["v"] = "entregado"
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(llegados() == [] and len(pend()) == 1, "espaciado: dentro de la espera NO se reintenta (sin tormenta)")
    ci.PEND_ESPERA_S = 0
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(len(llegados()) == 1, "espaciado: pasada la espera, sale")
    limpiar()
    base = baseline()
    MODO["v"] = "aplazado"
    base[11] = hdr(NED, "update biopsia", "<n1@x>")
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(pend() == [] and len(llegados()) == 1, "aplazado al parte = en camino: no queda pendiente ni se repite")
    # varios reintentos fallidos no pierden el correo
    limpiar()
    base = baseline()
    MODO["v"] = "halt"
    base[11] = hdr(NED, "update biopsia", "<n1@x>")
    for _ in range(4):
        ci.procesar(FakeMailbox(1, base), alertar=True, avisar=avisar)
    ok(len(pend()) == 1, "cuatro pasadas con HALT: sigue UN pendiente (idempotente, sin duplicar)")


def main():
    for t in (poller_halt, con_centinela, fallo_red, halt_largo_agrupa, espaciado_y_aplazado):
        try:
            t()
        except Exception as exc:
            global _fail
            _fail += 1
            import traceback; traceback.print_exc()
            print("  ✗ %s lanzó %r" % (t.__name__, exc))
    print("RESULTADO correo_urgente_reintento: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CORREO_URGENTE_REINTENTO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
