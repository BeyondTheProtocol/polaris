#!/usr/bin/env python3
"""radar_contacto.py — SHIM. El radar de {{CONTACTO}} vive ahora en `radar_personas.py contacto`.

Se mantiene el nombre porque lo referencian `.claude/settings.auto-mejora.json` (allowlist de
Bash) y el paso 7 de `.claude/agents/auto-mejora.md`: romperlo dejaría a la rutina sin su
ayudante y, peor, en silencio.

Qué cambió (20-sep-2026): el radar de una sola persona se generalizó a los N gemelos de
criterio ({{CONTACTO}}, {{CONTACTO}}, {{CONTACTO}}, Sid, {{CONTACTO}}) con estado persistido y, sobre todo, con la
distinción que faltaba entre «sin novedad» y «carril bloqueado» — el agujero por el que este
radar se pasó 86 días sin un solo pase sin que nadie se enterara. Ver `radar_personas.py`.

  python3 tools/radar_personas.py plan contacto     # <- equivalente a `radar_contacto.py`
  python3 tools/radar_personas.py pase contacto     # ejecuta y deja estado
  python3 tools/radar_personas.py estado          # los 5 gemelos de un vistazo
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radar_personas  # noqa: E402


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    resto = [a for a in args if not a.startswith("--")]
    # `radar_contacto.py x` tiraba de la X de {{CONTACTO}}. {{CONTACTO}} NO tiene X (verificado 26-jun-2026),
    # así que ese subcomando siempre acababa en "omito": ahora se dice por qué, una vez.
    if resto and resto[0] == "x":
        print("[radar_contacto] {{CONTACTO}} no tiene X (verificado 26-jun-2026). Su radar depende de "
              "carriles web. Usa: python3 tools/radar_personas.py pase contacto")
        return 0
    cfg = radar_personas.cargar("contacto")
    if resto and resto[0] == "pase":
        return radar_personas.cmd_pase(cfg, as_json, "--interactivo" in args)
    radar_personas.cmd_plan(cfg, as_json)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
