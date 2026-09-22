#!/usr/bin/env python3
"""esquema_estado.py — el Tablero no puede tener hilos con el estado roto.

POR QUÉ EXISTE (20-sep-2026). `seguimiento.add_hilo()` ya es un sumidero validado: comprueba el
enum de estado, exige fechas ISO y normaliza etiqueta y prioridad con enums cerrados. El problema
no era la validación: era la **puerta de atrás**. Nada impide abrir `seguimiento.json` y escribir
un hilo a mano con `json.dump`, y eso es exactamente lo que ha pasado — incluido yo mismo esta
misma sesión, metiendo un hilo con `estado: "sin_abrir"`, que no existe.

Por qué importa y no es cosmético: la severidad del Tablero se calcula **solo** con fechas ISO y
enums de estado, nunca con prosa (esa es su defensa contra una orden plantada en texto libre).
Un hilo con `estado: "pendiente"` no casa con ningún enum, así que **no lo persigue nadie**: se
queda en el fichero, se cuenta en los totales y desaparece de los avisos. Un pendiente invisible
es peor que un pendiente: parece que está vigilado.

Idea original: «fuerza un esquema entre el agente y la base de datos, no te fíes de la frase»
(destilado de {{CONTACTO}} {{CONTACTO}}, 20-sep-26). Aquí la aplicación es al revés de lo esperado — la
validación ya estaba; lo que faltaba era **comprobar que nadie la rodea**.

Uso:
  python3 tools/esquema_estado.py            # audita el Tablero; rc=1 si hay hilos rotos
  python3 tools/esquema_estado.py --json     # lo mismo, para encadenar
  python3 tools/esquema_estado.py --arreglar # mapea los estados conocidos a su enum y guarda
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402

SEG = os.path.join(_casa.state_dir(), "seguimiento.json")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Estados que han aparecido de verdad por la puerta de atrás, y a qué enum equivalen. No se
# inventa: cada uno se mapea al que significa lo mismo, y lo que no esté aquí se reporta sin
# tocarlo — adivinar el estado de un pendiente ajeno sería peor que dejarlo roto y a la vista.
EQUIVALENCIAS = {
    "pendiente": "en_curso",
    "sin_abrir": "en_curso",
    "descartado": "abandonado",
    "cerrado": "hecho",
    "completado": "hecho",
    "en curso": "en_curso",
    "esperando_respuesta": "esperando",
}


def _estados_validos():
    import seguimiento
    return seguimiento.ESTADOS


def revisar(ruta=SEG):
    """[(id, [fallos])] de los hilos que no cumplen el esquema. Sin juicio ni arreglo."""
    validos = _estados_validos()
    with open(ruta, encoding="utf-8") as f:
        seg = json.load(f)
    rotos = []
    for h in seg.get("hilos", []):
        fallos = []
        est = h.get("estado")
        if est not in validos:
            fallos.append("estado inválido: %r" % est)
        for campo in ("plazo", "esperando_desde", "hecho_el"):
            v = h.get(campo)
            if v and not ISO.match(str(v)):
                fallos.append("%s no es ISO YYYY-MM-DD: %r" % (campo, v))
        if not h.get("id"):
            fallos.append("sin id")
        if not h.get("titulo"):
            fallos.append("sin titulo")
        if fallos:
            rotos.append((h.get("id") or h.get("titulo", "?")[:40], fallos))
    return rotos, len(seg.get("hilos", []))


def arreglar(ruta=SEG):
    """Mapea los estados conocidos a su enum. Lo que no sepa traducir, lo deja y lo dice."""
    validos = _estados_validos()
    with open(ruta, encoding="utf-8") as f:
        seg = json.load(f)
    tocados, sin_traducir = [], []
    for h in seg.get("hilos", []):
        est = h.get("estado")
        if est in validos:
            continue
        nuevo = EQUIVALENCIAS.get(str(est).strip().lower())
        if nuevo:
            h["estado"] = nuevo
            tocados.append((h.get("id"), est, nuevo))
        else:
            sin_traducir.append((h.get("id"), est))
    if tocados:
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(seg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ruta)
    return tocados, sin_traducir


def main():
    args = sys.argv[1:]
    if "--arreglar" in args:
        tocados, sin_traducir = arreglar()
        for hid, viejo, nuevo in tocados:
            print("· %s: %s → %s" % (hid, viejo, nuevo))
        for hid, est in sin_traducir:
            print("⛔ %s: estado %r sin equivalencia conocida — míralo a mano" % (hid, est))
        print("arreglados %d · sin traducir %d" % (len(tocados), len(sin_traducir)))
        return 1 if sin_traducir else 0
    rotos, total = revisar()
    if "--json" in args:
        print(json.dumps({"total": total, "rotos": [{"id": i, "fallos": f} for i, f in rotos]},
                         ensure_ascii=False, indent=2))
        return 1 if rotos else 0
    if not rotos:
        print("✅ %d hilos, todos con el esquema en regla." % total)
        return 0
    print("⛔ %d de %d hilos con el esquema roto (no los persigue nadie):\n" % (len(rotos), total))
    for hid, fallos in rotos:
        print("  · %-45s %s" % (hid[:45], "; ".join(fallos)))
    print("\nEntraron saltándose `seguimiento.add_hilo()`, que es el sumidero validado.")
    print("Arréglalos con: python3 tools/esquema_estado.py --arreglar")
    return 1


if __name__ == "__main__":
    sys.exit(main())
