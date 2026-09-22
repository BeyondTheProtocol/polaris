#!/usr/bin/env python3
"""tools/frescura_dosier.py — ¿el Dosier del protocolo está FRESCO? (núcleo determinista).

La piedra L3/«verificar la propia lista de referencia»: el Guardián no puede bendecir un
checklist contra un dosier CADUCADO. Este módulo NO juzga clínica ni llama a ningún modelo:
sobre el Dosier-JSONL (una pieza por línea), comprueba con ARITMÉTICA pura si cada pieza
`vivo` sigue dentro de su ventana de frescura (`confirmado_en` + `ttl_dias`).

Principio #0: lo load-bearing aquí es CÓDIGO (fechas + enums), no el juicio de un modelo.
Fail-closed: sin fecha, formato raro o gate humano sin firmar → NO-FRESCO, nunca lo contrario.

Estados por pieza:
  FRESCO         vivo + confirmado_en + (confirmado_en + ttl_dias) >= ahora
  CADUCADO       vivo + confirmado_en pero vencido
  NO_VERIFICABLE vivo + gate_humano abierto sin confirmar  (o fecha ilegible)
  SIN_CONFIRMAR  vivo, sin gate, sin confirmado_en
  NO_CUENTA      status != vivo (superado/obsoleto: no entra en «completo», no es error)
  ERROR          la línea no es un objeto JSON válido (fail-closed)

Códigos de salida:
  0 = todas las piezas `vivo` están FRESCO → fresco-entregable por esta capa
  1 = hay alguna vivo CADUCADA / SIN_CONFIRMAR / NO_VERIFICABLE → NO entregable (fail-closed)
  2 = error de formato en el JSONL → no se puede ni evaluar (fail-closed)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

FRESCO = "FRESCO"
CADUCADO = "CADUCADO"
NO_VERIFICABLE = "NO_VERIFICABLE"
SIN_CONFIRMAR = "SIN_CONFIRMAR"
NO_CUENTA = "NO_CUENTA"
ERROR = "ERROR"

# Estados que, en una pieza `vivo`, hacen el dosier NO entregable por frescura.
_BLOQUEANTES = {CADUCADO, NO_VERIFICABLE, SIN_CONFIRMAR}


def _parse_iso(s):
    """ISO-8601 UTC → datetime aware. Acepta sufijo Z. None/ilegible → None."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def estado_pieza(p, ahora):
    """Una pieza (dict) → (estado, detalle). Determinista; ahora = datetime aware UTC."""
    if p.get("status") != "vivo":
        return NO_CUENTA, "status=%s (no cuenta para completo)" % p.get("status")
    conf = _parse_iso(p.get("confirmado_en"))
    if p.get("confirmado_en") and conf is None:
        return NO_VERIFICABLE, "confirmado_en ilegible → fail-closed"
    if conf is None:
        if p.get("gate_humano"):
            return NO_VERIFICABLE, "gate humano %s abierto, sin confirmar" % p.get("gate_humano")
        return SIN_CONFIRMAR, "sin confirmado_en (no se puede datar la frescura)"
    if conf > ahora:
        return NO_VERIFICABLE, "confirmado_en está en el FUTURO → fail-closed"
    if isinstance(p.get("ttl_dias"), bool):
        # bool es subclase de int (True==1): `ttl_dias: true` colaría como fresco → fail-OPEN.
        return NO_VERIFICABLE, "ttl_dias booleano (no es un entero válido) → fail-closed"
    try:
        ttl = int(p.get("ttl_dias"))
    except (TypeError, ValueError):
        return NO_VERIFICABLE, "ttl_dias ausente o ilegible → fail-closed"
    if not (0 < ttl <= 36500):
        return NO_VERIFICABLE, "ttl_dias fuera de rango (0–36500 días) → fail-closed"
    vence = conf + timedelta(days=ttl)
    if vence < ahora:
        return CADUCADO, "venció el %s (ttl %dd)" % (vence.date().isoformat(), ttl)
    return FRESCO, "fresco hasta %s" % vence.date().isoformat()


def evaluar_frescura(piezas, ahora=None):
    """Lista de piezas (dicts) → (resultados, entregable_por_frescura, bloqueos).

    `ahora` se inyecta para tests deterministas; None → ahora UTC real.
    """
    if ahora is None:
        ahora = datetime.now(timezone.utc)
    resultados, bloqueos = [], []
    for p in piezas:
        if not isinstance(p, dict):
            est, det = ERROR, "la pieza no es un objeto JSON"
        else:
            est, det = estado_pieza(p, ahora)
        pid = (p.get("id") if isinstance(p, dict) else None) or "(sin id)"
        resultados.append({"id": pid, "estado": est, "detalle": det})
        if est == ERROR:
            bloqueos.append("pieza «%s»: formato inválido → fail-closed" % pid)
        elif est in _BLOQUEANTES:
            bloqueos.append("pieza «%s»: %s (%s)" % (pid, est, det))
    entregable = not bloqueos
    return resultados, entregable, bloqueos


def cargar_jsonl(path):
    """JSONL → (piezas, errores_de_linea). Una línea mala NO se ignora: se reporta."""
    piezas, errores = [], []
    with open(path, encoding="utf-8") as fh:
        for n, linea in enumerate(fh, 1):
            s = linea.strip()
            if not s or s.startswith("#"):
                continue
            try:
                piezas.append(json.loads(s))
            except json.JSONDecodeError as e:
                errores.append("línea %d: JSON inválido (%s)" % (n, e))
                piezas.append({"id": "(línea %d ilegible)" % n, "__error__": True})
    return piezas, errores


def main(argv):
    if not argv:
        print("uso: frescura_dosier.py <dosier.jsonl>")
        return 2
    piezas, errores = cargar_jsonl(argv[0])
    resultados, entregable, bloqueos = evaluar_frescura(piezas)
    print("=== Frescura del Dosier (determinista, sin red ni modelo) ===")
    for r in resultados:
        glyph = {FRESCO: "✓", CADUCADO: "✗", NO_VERIFICABLE: "?", SIN_CONFIRMAR: "?",
                 NO_CUENTA: "·", ERROR: "✗"}.get(r["estado"], "?")
        print("  %s [%-14s] %s — %s" % (glyph, r["estado"], r["id"], r["detalle"]))
    for e in errores:
        print("  ✗ %s" % e)
    if errores:
        print("FORMATO INVÁLIDO → fail-closed (no se puede evaluar el dosier)")
        return 2
    if entregable:
        print("✅ FRESCO: todas las piezas vivas están dentro de ventana")
        return 0
    print("🛑 NO entregable por frescura (fail-closed):")
    for b in bloqueos:
        print("   - %s" % b)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
