#!/usr/bin/env python3
"""test_adjuntos_clinicos.py — garantías del bajador de informes médicos del correo.

Verifica SIN tocar la red:
  · estructural: NUNCA importa smtplib (no puede enviar correo).
  · egress fail-closed: la única conexión posible es imap.gmail.com:993.
  · la puntuación separa informe de nómina, y el autoenvío mudo no arrastra el ruido.
  · las imágenes embebidas (firma `image001.jpg`) y los logos pequeños NO son candidatos:
    es lo que inundaba el cajón de dudosos con 2.400 entradas.
  · el parser de BODYSTRUCTURE saca nombre, Content-ID y octetos de una parte real.
  · dry-run no escribe un solo byte.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import adjuntos_clinicos as ac   # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


# ── el muro ───────────────────────────────────────────────────────────────
fuente = open(os.path.join(ROOT, "tools", "adjuntos_clinicos.py"), encoding="utf-8").read()
check("no importa smtplib", "import smtplib" not in fuente)
check("no usa STORE/COPY/EXPUNGE", not any(x in fuente for x in ('"STORE"', '"COPY"', "expunge(")))
check("solo lectura explícita", "readonly=True" in fuente)
check("reusa el cortafuegos de egress", "_assert_host" in fuente or "ea._connect" in fuente)

import correo_imap as ci   # noqa: E402
try:
    ci._assert_host("imap.malo.com", 993)
    check("egress fail-closed", False)
except Exception:
    check("egress fail-closed", True)


# ── puntuación ────────────────────────────────────────────────────────────
def score(f, s, n):
    return ac.puntuar(f, s, n)[0]


CLARO, REVISAR = ac.UMBRAL_FUERTE, ac.UMBRAL_REVISAR
check("informe oncológico autoenviado es claro",
      score("{{TITULAR}} <titular.mgp@gmail.com>", "", "Ultimo informe oncologico.pdf") >= CLARO)
check("remitente clínico basta",
      score("info <info@{{CENTRO}}.net>", "docu", "Formulario.pdf") >= CLARO)
check("Guardant es claro",
      score("x <a@b.com>", "", "GUARDANT_Report_SOLTI-2401.pdf") >= CLARO)
check("PET-TAC es claro",
      score("{{TITULAR}} <titular.mgp@gmail.com>", "", "Pettac-marzo.pdf") >= CLARO)
check("alta de autónomo queda fuera",
      score("gestor <x@asesores.es>", "Nómina", "ALTA AUTONOMO 01-03-2021.pdf") < REVISAR)
check("informe de prácticas de carrera queda fuera",
      score("x <a@b.com>", "Informe Práctica 2", "informe_Practica_2.docx") < REVISAR)
check("logo autoenviado queda fuera",
      score("{{TITULAR}} <titular.mgp@gmail.com>", "", "bookings_logo_2020.png") < REVISAR)
check("escaneo mudo autoenviado llega al menos a revisar",
      score("{{TITULAR}} <titular.mgp@gmail.com>", "fotos", "Scanned Documents.pdf") >= REVISAR)
check("DICOM siempre es claro",
      score("x <a@b.com>", "", "DICOM.zip") >= CLARO)


# ── BODYSTRUCTURE: nombre, inline y tamaño ────────────────────────────────
META_FIRMA = ('12 (FETCH (UID 991 BODYSTRUCTURE (("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL '
              '"7BIT" 10 1)("IMAGE" "JPEG" ("NAME" "image001.jpg") "<image001@01D5.ES>" NIL '
              '"BASE64" 8462 118)("APPLICATION" "PDF" ("NAME" "Informe RMN.pdf") NIL NIL '
              '"BASE64" 130000 2000) "MIXED")')
partes = dict((n, (i, o)) for n, i, o in ac._partes_de_bodystructure(META_FIRMA))
check("saca los dos adjuntos", set(partes) == {"image001.jpg", "Informe RMN.pdf"})
check("marca inline la firma embebida", partes.get("image001.jpg", (False, 0))[0] is True)
check("el PDF no es inline", partes.get("Informe RMN.pdf", (True, 0))[0] is False)
check("saca los octetos", partes.get("Informe RMN.pdf", (False, 0))[1] == 130000)
check("umbral de imagen mínima definido", ac.MIN_IMAGEN >= 10_000)


# ── dry-run no escribe ────────────────────────────────────────────────────
antes = os.path.exists(ac.MANIFEST) and os.path.getmtime(ac.MANIFEST)
res = ac.bajar([{"cuenta": "titular.mgp@gmail.com", "uid": "1", "fichero": "x.pdf",
                 "from": "a@b.c", "subject": "s", "date": "", "score": 9, "motivos": []}],
               apply=False)
despues = os.path.exists(ac.MANIFEST) and os.path.getmtime(ac.MANIFEST)
check("dry-run no toca el manifest", antes == despues)
check("dry-run cuenta candidatos sin bajar", res["bajados"] == 0 and res["candidatos"] >= 0)

print("test_adjuntos_clinicos: %d ok, %d fallos" % (_pass, _fail))
sys.exit(1 if _fail else 0)
