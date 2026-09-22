#!/usr/bin/env python3
"""Consolidador: junta las contribuciones de varios subagentes en UN dossier.

Es DETERMINISTA a propósito: NO razona ni inventa nada. Toma lo que los subagentes ya
decidieron (JSON) y garantiza la FORMA y la SEGURIDAD del entregable:
  · trazabilidad   — cada decisión cita su fuente; si alguna queda huérfana, AVISA (y rc=1).
  · prioridad      — ordena por impacto (crítica > alta > media).
  · sin ocultar    — si hay más de TOPE_CABEZA decisiones, las de más van a "diferidas",
                     nunca se tiran en silencio.
La PROSA fina (TL;DR en la voz de {{TITULAR}}, matices) la pone el agente encima; este tool
asegura que el dossier siempre tenga la misma estructura, cite sus fuentes y no esconda nada.

Entrada (stdin o fichero): JSON
  {
    "intencion": "organiza X",
    "contribuciones": [
      {"agente": "agencia-viajes",
       "decisiones": [{"titulo":"...", "impacto":"critica|alta|media",
                       "recomendacion":"...", "porque":"...", "fuente":"opcional"}],
       "acciones":   [{"texto":"...", "limite":"opcional", "fuente":"opcional"}]}
    ]
  }
Salida: el dossier en markdown.  rc 0 = limpio · rc 1 = hay decisión sin fuente trazable.
Local y privado: solo transforma texto que ya tienes; no toca nada hacia fuera.
"""
import json
import sys

IMPACTOS = ["critica", "alta", "media"]  # orden de prioridad
ETIQUETA = {
    "critica": "🔴 CRÍTICA (go / no-go)",
    "alta": "🟠 ALTA (de esto depende)",
    "media": "🟡 MEDIA (acomodación)",
}
TOPE_CABEZA = 6  # nº máximo de decisiones "en cabeza"; el resto, a diferidas (no se ocultan)


def consolidar(data):
    intencion = data.get("intencion") or "(sin intención)"
    decisiones, acciones, avisos = [], [], []
    for c in data.get("contribuciones", []):
        ag = c.get("agente") or "?"
        for d in c.get("decisiones", []) or []:
            d = dict(d)
            d["_fuente"] = d.get("fuente") or ag
            if d.get("impacto") not in IMPACTOS:
                avisos.append("impacto inválido en «%s»: %r → lo trato como 'media'"
                              % (d.get("titulo", "?"), d.get("impacto")))
                d["impacto"] = "media"
            if not d.get("titulo"):
                avisos.append("decisión sin título (fuente %s)" % d["_fuente"])
            decisiones.append(d)
        for a in c.get("acciones", []) or []:
            a = dict(a)
            a["_fuente"] = a.get("fuente") or ag
            acciones.append(a)
    decisiones.sort(key=lambda d: IMPACTOS.index(d["impacto"]))
    cabeza, diferidas = decisiones[:TOPE_CABEZA], decisiones[TOPE_CABEZA:]
    if diferidas:
        avisos.append("%d decisiones por encima del tope de %d → van a «diferidas» (no se ocultan)"
                      % (len(diferidas), TOPE_CABEZA))
    huerfanas = [d for d in cabeza if d["_fuente"] == "?"]
    for d in huerfanas:
        avisos.append("decisión SIN fuente trazable: «%s» (⚠️ verifica antes de fiarte)"
                      % d.get("titulo", "?"))
    return intencion, cabeza, diferidas, acciones, avisos, bool(huerfanas)


def render(intencion, cabeza, diferidas, acciones, avisos):
    out = ["# Dossier: %s" % intencion,
           "\n## TL;DR\n_(lo rellena el agente, en la voz de {{TITULAR}})_",
           "\n## Decisiones (priorizadas por impacto)"]
    cur = None
    for d in cabeza:
        if d["impacto"] != cur:
            cur = d["impacto"]
            out.append("\n### %s" % ETIQUETA.get(cur, cur))
        linea = "- **%s**" % d.get("titulo", "?")
        if d.get("porque"):
            linea += " — %s" % d["porque"]
        out.append(linea)
        out.append("  → recomendación: %s  _(fuente: %s)_"
                   % (d.get("recomendacion", "—"), d["_fuente"]))
    if diferidas:
        out.append("\n### ⏭️ Diferidas (no caben en cabeza; aquí, a la vista, no ocultas)")
        for d in diferidas:
            out.append("- %s  _(fuente: %s)_" % (d.get("titulo", "?"), d["_fuente"]))
    out.append("\n## Acciones para ti")
    if not acciones:
        out.append("_(ninguna acción propuesta)_")
    for a in acciones:
        lim = "  · límite: %s" % a["limite"] if a.get("limite") else ""
        out.append("- [ ] %s%s  _(%s)_" % (a.get("texto", "?"), lim, a["_fuente"]))
    if avisos:
        out.append("\n## ⚠️ Validación (el consolidador avisa, no oculta)")
        for v in avisos:
            out.append("- %s" % v)
    return "\n".join(out)


def main(argv):
    raw = open(argv[0], encoding="utf-8").read() if argv else sys.stdin.read()
    data = json.loads(raw)
    intencion, cabeza, diferidas, acciones, avisos, hay_huerfana = consolidar(data)
    print(render(intencion, cabeza, diferidas, acciones, avisos))
    return 1 if hay_huerfana else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
