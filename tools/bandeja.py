#!/usr/bin/env python3
"""tools/bandeja.py — espejo HUMANO del lazo en BANDEJA.md (raíz del repo).

Append-only y FAIL-SOFT: jamás lanza hacia el caller (un fallo aquí NUNCA puede romper
el lazo ni el muro). Es solo la VENTANA de {{TITULAR}} —su "una puerta"—: lo que entra por
Telegram y lo que el lazo procesa se anota aquí en lenguaje llano, para que lo vea sin
bucear en la cola. El audit canónico sigue siendo Gestion/PANEL-LAZO.md y el outbox.

BANDEJA.md es LOCAL y está gitignored (puede llevar texto suyo). No metas aquí PII clínica
cruda: por Telegram van intenciones y avisos, no el volcado clínico (eso, en el chat local).
Sin dependencias (stdlib).
"""
import os
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BTP_BANDEJA permite redirigir el fichero (tests lo apuntan a un tmp para no ensuciar
# la bandeja real). Por defecto, BANDEJA.md en la raíz del repo.
BANDEJA = os.environ.get("BTP_BANDEJA") or os.path.join(REPO, "BANDEJA.md")


def anota(linea):
    """Añade UNA línea con sello de tiempo al final de la bandeja. Nunca falla."""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M")
        with open(BANDEJA, "a", encoding="utf-8") as f:
            f.write("- `%s` %s\n" % (ts, str(linea).replace("\n", " ")[:500]))
    except Exception:
        pass


def entrada(text, ref=None):
    r = " (ref %s)" % ref if ref else ""
    anota("📥 recibido por Telegram%s: %s" % (r, (text or "")[:300]))


def voz(ref=None):
    r = " (ref %s)" % ref if ref else ""
    anota("🎙️ nota de voz recibida%s — la transcribo en sesión" % r)


def resultado(job, did, awaiting=None):
    s = "✅ procesado · job %s · %s" % (job, did)
    if awaiting and str(awaiting) not in ("—", "-", ""):
        s += " · ⏳ espera tu OK: %s" % awaiting
    anota(s)


def fallo(job, motivo=""):
    anota("⚠️ no pude procesar · job %s · %s" % (job, motivo or "revisa PANEL-LAZO"))


def _arg(a, name, default=""):
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def main(argv):
    # CLI fina para que el dispatcher (bash) pueda espejar sin python inline.
    if not argv:
        print("uso: bandeja.py {entrada|voz|resultado|fallo|anota} ...")
        return 2
    cmd, a = argv[0], argv[1:]
    if cmd == "entrada":
        entrada(_arg(a, "--text"), _arg(a, "--ref") or None)
    elif cmd == "voz":
        voz(_arg(a, "--ref") or None)
    elif cmd == "resultado":
        resultado(_arg(a, "--job") or "-", _arg(a, "--did"), _arg(a, "--awaiting") or None)
    elif cmd == "fallo":
        fallo(_arg(a, "--job") or "-", _arg(a, "--motivo"))
    elif cmd == "anota":
        anota(_arg(a, "--text"))
    else:
        print("comando desconocido")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
