#!/usr/bin/env python3
"""Tests de tools/dosier_invariantes.py — L1, suelo determinista del Guardián.

Sin red, sin modelo, `ahora` inyectado. Demuestra el EFECTO: schema con allowlist
cerrada (fail-closed), invariantes cruzados, y la clase CONTRADICCIÓN (dos piezas vivas
del mismo hecho) — el fallo que cazamos a mano (ESTADO-ACTUAL vs HOY).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import dosier_invariantes as di  # noqa: E402

# Bóveda de pega (24-sep-26): `cotejo_invariante` exige que el informe citado EXISTA (auditoría
# Gorgojo 1.1/1.6). El de `_COTEJO_OK` existe aquí, en un tmp; la bóveda real no se toca.
import tempfile  # noqa: E402
_BOVEDA = os.path.join(tempfile.mkdtemp(prefix="dosier_boveda_"), "_PRIVADO_CLINICO")
os.makedirs(os.path.join(_BOVEDA, "demo"))
with open(os.path.join(_BOVEDA, "demo", "informe.pdf"), "wb") as _f:
    _f.write(b"%PDF-1.4 informe sintetico de prueba")
os.environ["BTP_BOVEDA_CLINICA"] = _BOVEDA
import fuente_clinica as _fc  # noqa: E402
_fc._LOG = lambda *a: None   # no se escribe en el registro real de accesos clínicos

AHORA = datetime(2026, 6, 27, tzinfo=timezone.utc)
fallos = 0
total = 0


def check(nombre, cond):
    global fallos, total
    total += 1
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


def ev(piezas):
    return di.evaluar(piezas, AHORA)


def ev_dom(piezas):
    """Evalúa ACTIVANDO las reglas de dominio EN CUARENTENA (pendiente {{CONTACTO}}), para probar el
    MECANISMO de invariantes de dominio. En producción están inertes (no se enforca clínica)."""
    return di.evaluar(piezas, AHORA, invariantes_dominio=di.INVARIANTES_DOMINIO_PENDIENTE_CONTACTO)


def fresca(**kw):
    """Pieza VÁLIDA y FRESCA por defecto; kw sobrescribe campos."""
    p = {"id": "X", "bloque": "B1", "afirmacion": "algo genérico", "fuentes": ["src"],
         "confianza": "media", "status": "vivo",
         "confirmado_en": "2026-06-25T00:00:00Z", "ttl_dias": 30}
    p.update(kw)
    return p


# Cotejo VÁLIDO de demostración (para fixtures cuya afirmación toca una alteración: desde la capa
# L-cotejo, una afirmación de presencia/ausencia exige fuente primaria + fecha + plataforma).
_COTEJO_OK = {"fuente": "_PRIVADO_CLINICO/demo/informe.pdf", "fecha": "2026-06-25",
              "plataforma": "panel demo", "tipo_resultado": "presencia"}


# 1) dosier limpio → entregable
_, entregable, _ = ev([fresca(id="A"), fresca(id="B", bloque="B2")])
check("dosier limpio (schema+invariantes+frescura) → entregable", entregable)

# 2) campo DESCONOCIDO → rechazado (allowlist cerrada, fail-closed)
p = fresca(id="A"); p["intruso"] = "x"
_, entregable, bloq = ev([p])
check("campo desconocido → NO entregable (allowlist cerrada)",
      (not entregable) and any("DESCONOCIDO" in b for b in bloq))

# 3) falta campo requerido
p = fresca(id="A"); del p["afirmacion"]
_, entregable, bloq = ev([p])
check("falta requerido → NO entregable", (not entregable) and any("requerido" in b for b in bloq))

# 4) enum status inválido
_, entregable, bloq = ev([fresca(id="A", status="zombi")])
check("status inválido → NO entregable", (not entregable) and any("status inválido" in b for b in bloq))

# 5) enum confianza inválido
_, entregable, bloq = ev([fresca(id="A", confianza="altísima")])
check("confianza inválida → NO entregable", (not entregable) and any("confianza inválida" in b for b in bloq))

# 6) CONTRADICCIÓN: dos piezas VIVAS con el mismo id
_, entregable, bloq = ev([fresca(id="DUP"), fresca(id="DUP", bloque="B2")])
check("dos vivas con el mismo id → CONTRADICCIÓN, NO entregable",
      (not entregable) and any("CONTRADICCIÓN" in b for b in bloq))

# 7) VERSIONADO correcto: misma id, una viva + una superada → NO es contradicción
viva = fresca(id="V")
superada = fresca(id="V", status="superado", confirmado_en="2026-01-01T00:00:00Z", ttl_dias=1)
_, entregable, bloq = ev([viva, superada])
check("versionado (viva + superada, mismo id) → sin contradicción, entregable",
      entregable and not any("CONTRADICCIÓN" in b for b in bloq))

# 8) confianza ALTA con gate humano ABIERTO sin confirmar → incoherente
_, entregable, bloq = ev([fresca(id="A", confianza="alta", gate_humano="G2", confirmado_en=None)])
check("alta + gate abierto sin confirmar → NO entregable (incoherente)",
      (not entregable) and any("ALTA con el gate" in b for b in bloq))

# 9) confianza ALTA sin fuente
p = fresca(id="A", confianza="alta"); p["fuentes"] = []
_, entregable, bloq = ev([p])
check("alta sin fuente → NO entregable", (not entregable) and any("sin FUENTE" in b for b in bloq))

# 10) MECANISMO de dominio (regla en cuarentena ACTIVADA en el test): {{DIANA}} con su ensayo
#     ({{TRAZADOR}}) huérfano → bloquea. Prueba el mecanismo, no activa nada clínico en producción.
_, entregable, bloq = ev_dom([fresca(id="A", afirmacion="marcador {{DIANA}}+ confirmado")])
check("[mecanismo] {{DIANA}} sin su ensayo {{TRAZADOR}} → INVARIANTE de dominio, NO entregable",
      (not entregable) and any("INVARIANTE de dominio" in b for b in bloq))

# 11) MECANISMO satisfecho: {{DIANA}} + {{TRAZADOR}} presentes (ambas con cotejo válido — la afirmación de
#     {{DIANA}} es de presencia, así que la capa L-cotejo le exige fuente+fecha+plataforma).
_, entregable, bloq = ev_dom([
    fresca(id="A", afirmacion="marcador {{DIANA}}+ confirmado", cotejo=_COTEJO_OK),
    fresca(id="B", bloque="B2", afirmacion="imagen {{TRAZADOR}} pedida")])
check("[mecanismo] {{DIANA}} con su {{TRAZADOR}} → invariante satisfecho", entregable and not any("INVARIANTE" in b for b in bloq))

# 11b) CUARENTENA: por defecto (lista activa vacía) una regla clínica NO se enforca —
#      el mismo {{DIANA}} huérfano es ENTREGABLE (el contenido clínico espera la firma de {{CONTACTO}}).
_, entregable, bloq = ev([fresca(id="A", afirmacion="marcador {{DIANA}}+ confirmado", cotejo=_COTEJO_OK)])
check("regla de dominio CLÍNICA en cuarentena → NO se enforca por defecto (entregable)",
      entregable and not any("INVARIANTE de dominio" in b for b in bloq))

# 12) frescura integrada: pieza viva CADUCADA → NO entregable
_, entregable, bloq = ev([fresca(id="A", confirmado_en="2026-01-01T00:00:00Z", ttl_dias=7)])
check("pieza viva caducada (frescura) → NO entregable", (not entregable) and any("CADUCAD" in b for b in bloq))

# 13) formato: pieza no-objeto → schema falla, fail-closed
_, entregable, bloq = ev(["esto no es un objeto"])
check("pieza no-objeto → NO entregable (fail-closed)",
      (not entregable) and any("no es un objeto" in b for b in bloq))

# 14) FAIL-CLOSED central: dosier VACÍO → NO entregable (nada vivo que entregar)
_, entregable, bloq = ev([])
check("dosier vacío [] → NO entregable (sin ninguna pieza viva)",
      (not entregable) and any("SIN ninguna pieza VIVA" in b for b in bloq))

# 15) FAIL-CLOSED central: todas las piezas 'superado' → NO entregable (ninguna viva)
_, entregable, bloq = ev([fresca(id="A", status="superado"), fresca(id="B", status="superado")])
check("dosier con todo 'superado' → NO entregable (ninguna viva)",
      (not entregable) and any("SIN ninguna pieza VIVA" in b for b in bloq))

# 16) ttl_dias BOOLEANO (true) → schema lo rechaza (bool es int en Python: sería fail-OPEN)
p = fresca(id="A"); p["ttl_dias"] = True
_, entregable, bloq = ev([p])
check("ttl_dias booleano → NO entregable (schema excluye bool)",
      (not entregable) and any("booleano" in b for b in bloq))


print("RESULTADO dosier_invariantes (L1): %d OK, %d fallos" % (total - fallos, fallos))
print("✅ L1 EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
