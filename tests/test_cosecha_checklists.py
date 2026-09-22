#!/usr/bin/env python3
"""Tests de tools/cosecha_checklists.py — parseo de cabos sueltos en .md + garantías del muro.

Aislado: prueba el parser puro (_candidatos_en_fichero) sobre un .md temporal y verifica que el
módulo no tiene NINGUNA vía de salida (anti-egress). No toca el estado vivo.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import cosecha_checklists as cc  # noqa: E402

OK = 0
FAIL = 0


def check(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print("  ok ·", msg)
    else:
        FAIL += 1
        print("  ✗  FALLO ·", msg)


MD = """# Paquete de prueba

## Documentos listos
| # | Doc | Estado |
|---|-----|--------|
| 1 | Dossier v3 | Listo |

## Pendientes de {{TITULAR}} antes de entregar
| Pendiente | Por qué | Acción de {{TITULAR}} |
|---|---|---|
| **1. ~~Confirmar @ exacto~~ RESUELTO** | ya | Ninguna acción requerida. |
| **2. Apelación TikTok pendiente** | acredita | Exportar los 4 correos en PDF. |

## Notas varias (NO es zona de pendientes)
- Esto no debería capturarse porque la sección no es de pendientes.

## Por archivar (pendiente)
- [ ] Sellar las capturas E3 y E4 con eIDAS
- [x] Esto ya está hecho, no capturar
- Bloqueo: depende de la biopsia
- Fuente: project-algo
- Archivar correos-prueba con hash SHA-256
"""


def main():
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(MD)
        ruta = f.name
    try:
        cands = cc._candidatos_en_fichero(ruta)
        titulos = [t for t, _ in cands]
        blob = " || ".join(titulos)

        check(any("Apelación TikTok" in t for t in titulos),
              "captura fila de tabla en zona de pendientes")
        check(any("Sellar las capturas" in t for t in titulos),
              "captura casilla sin marcar [ ]")
        check(any("hash SHA-256" in t for t in titulos),
              "captura ítem de lista en zona 'Por archivar (pendiente)'")
        check(not any("RESUELTO" in t for t in titulos),
              "descarta filas resueltas (~~/RESUELTO/Ninguna acción)")
        check(not any("ya está hecho" in t for t in titulos),
              "descarta casilla marcada [x]")
        check(not any(t.lower().startswith("bloqueo") for t in titulos)
              and "Bloqueo: depende" not in blob,
              "descarta etiquetas de campo (Bloqueo:/Fuente:)")
        check(not any("no debería capturarse" in t for t in titulos),
              "NO captura listas fuera de una zona de pendientes")

        # Anti-egress: el módulo no importa nada que mande datos fuera.
        src = open(cc.__file__, encoding="utf-8").read()
        for prohibido in ("smtplib", "requests", "urllib.request", "import socket"):
            check(prohibido not in src, "sin vía de salida: %s ausente" % prohibido)
    finally:
        os.unlink(ruta)

    print("\nRESULTADO cosecha_checklists: %d OK, %d fallos" % (OK, FAIL))
    if FAIL:
        print("❌ COSECHA EN ROJO")
        sys.exit(1)
    print("✅ COSECHA EN VERDE")


if __name__ == "__main__":
    main()
