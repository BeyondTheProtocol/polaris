#!/usr/bin/env python3
"""tools/instagram_dm.py — capa de RESPUESTA a DMs de Instagram (borrador con gate del muro).

División de trabajo (no se duplica nada):
  · ig_inbox.py  LEE la bandeja (anti-baneo, agent-browser) y deja _cajita/ig_dms/raw-FECHA.json.
  · instagram.py LEE posts/comentarios por la API oficial.
  · ESTA tool construye la RESPUESTA: reusa lo que ig_inbox dejó para listarte los hilos a los
    que puedes contestar, y crea la respuesta como BORRADOR vía salida.send(OUTWARD contact) →
    outbox/pending con nonce. NUNCA envía.

El envío real lo hace salida.approve_and_deliver SOLO tras tu OK por Telegram (challenge-response
con nonce). Y hoy, hasta que Meta apruebe el permiso instagram_manage_messages (App Review), ese
envío FALLA LIMPIO y el borrador se queda «a un clic» — nada sale a ciegas. (Ver salida.py.)

El texto debe venir YA en tu voz: pásalo antes por los agentes voz-titular + verificacion. Aquí
solo se draftea (el scrubber de fuga de salida.py revisa PII/léxico y lo hace VISIBLE en el
borrador, pero no sustituye a la revisión de voz).

⚠️ Nota honesta [a verificar en Fase 2]: el destinatario de la Messaging API oficial es el IGSID
(id con ámbito de Instagram). El id que trae la bandeja web (from_id = pk) puede NO coincidir con
ese IGSID; la correspondencia se confirma cuando esté el permiso de mensajes. Como el envío está
gated hasta entonces, el borrador se prepara igual y se valida el id al encender la Fase 2.

Uso:
  python3 tools/instagram_dm.py                                  # lista hilos recientes (última lectura)
  python3 tools/instagram_dm.py responder --to @handle --texto "Hola, ..."
  python3 tools/instagram_dm.py responder --to <IGSID> --texto-file ruta.txt
  python3 tools/instagram_dm.py responder --to @handle --texto "..." --dry   # enseña, no draftea
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
OUT_DIR = os.path.join(ROOT, "_cajita", "ig_dms")

import salida  # única boca de salida (gate del muro)

# Triaje de leads: misma fuente de verdad que ig_inbox / x_dms (import perezoso y tolerante:
# si agent-browser/x_dms no carga en este entorno, seguimos sin la señal de lead).
try:
    from x_dms import _flag as _lead_flag  # noqa: E402
except Exception:  # pragma: no cover - entorno sin x_dms
    def _lead_flag(_text):
        return []


def _latest_raw():
    """Lo más reciente que dejó ig_inbox.py: lista de DMs nuevos (o [] si no hay lectura)."""
    files = sorted(glob.glob(os.path.join(OUT_DIR, "raw-*.json")))
    if not files:
        return []
    try:
        return (json.load(open(files[-1], encoding="utf-8")) or {}).get("new") or []
    except Exception:
        return []


def _threads(msgs):
    """Agrupa los DMs por contacto y deja el último mensaje de cada uno. Marca posibles leads."""
    by_contact = {}
    for m in msgs:
        key = m.get("from_id") or m.get("conv")
        if not key:
            continue
        prev = by_contact.get(key)
        if prev is None or (m.get("ts") or 0) >= (prev.get("ts") or 0):
            by_contact[key] = m
    out = []
    for key, m in by_contact.items():
        señales = _lead_flag((m.get("text") or "") + " " + (m.get("name") or ""))
        out.append({
            "from_id": m.get("from_id") or "",
            "conv": m.get("conv") or "",
            "handle": m.get("handle") or "",
            "name": m.get("name") or "",
            "text": m.get("text") or "",
            "ts": m.get("ts") or 0,
            "inbox": m.get("inbox") or "",
            "lead": señales,
        })
    return sorted(out, key=lambda t: -t["ts"])


def _resolve_dest(to, threads):
    """Resuelve `to` a un id de destinatario. Acepta @handle (lo busca en los hilos leídos) o un
    id directo (IGSID/from_id). Devuelve (dest_id, etiqueta_legible) o (None, motivo_error)."""
    to = (to or "").strip()
    if not to:
        return None, "destinatario vacío"
    if to.startswith("@"):
        handle = to[1:].lower()
        for t in threads:
            if (t["handle"] or "").lower() == handle:
                return (t["from_id"] or None), ("@" + t["handle"])
        return None, ("no encuentro a %s en la última lectura de DMs — corre `ig_inbox.py` "
                      "o pásame su IGSID directamente" % to)
    # id directo
    return to, to


def _read_texto(args):
    if args.texto_file:
        with open(args.texto_file, encoding="utf-8") as f:
            return f.read().strip()
    return (args.texto or "").strip()


def cmd_list(_args):
    threads = _threads(_latest_raw())
    if not threads:
        print("No hay lectura reciente de DMs. Corre primero:  python3 tools/ig_inbox.py")
        return 0
    print("Hilos recientes de Instagram (para responder con `responder --to`):\n")
    for t in threads:
        who = ("@" + t["handle"]) if t["handle"] else (t["name"] or t["from_id"])
        tag = " [solicitud]" if t["inbox"] == "solicitudes" else ""
        lead = (" 🎯 " + ", ".join(t["lead"])) if t["lead"] else ""
        prev = (t["text"][:80] or "(sin texto)").replace("\n", " ")
        print("• %s%s  (id %s)%s" % (who, tag, t["from_id"] or "?", lead))
        print("    “%s”" % prev)
    print("\nResponder:  python3 tools/instagram_dm.py responder --to @handle --texto \"...\"")
    return 0


def cmd_responder(args):
    threads = _threads(_latest_raw())
    dest, label = _resolve_dest(args.to, threads)
    if not dest:
        print("✗ %s" % label)
        return 2
    texto = _read_texto(args)
    if not texto:
        print("✗ falta el texto (--texto o --texto-file). Pásalo ya en tu voz (voz-titular).")
        return 2

    if args.dry:
        print("DRY · NO se draftea. Se prepararía un borrador de DM de Instagram:")
        print("  para: %s  (id %s)" % (label, dest))
        avisos = salida._revisar_publico(texto)
        if avisos:
            print("  ⚠️ posible fuga (revísalo): %s" % "; ".join(avisos))
        print("  texto:\n    %s" % texto.replace("\n", "\n    "))
        return 0

    res = salida.send("instagram", "contact", dest, texto)
    if res.get("draft"):
        print("✅ Borrador de DM de Instagram creado para %s." % label)
        print("   %s" % res.get("reason"))
        if res.get("avisos_fuga"):
            print("   ⚠️ posible fuga: %s" % "; ".join(res["avisos_fuga"]))
        print("   Lo apruebas tú por Telegram (aprobar <borrador> <nonce>). Borrador: %s"
              % res["draft"])
        return 0
    print("✗ no se pudo crear el borrador: %s" % res.get("reason"))
    return 1


def main(argv=None):
    p = argparse.ArgumentParser(description="Respuesta a DMs de Instagram (borrador con gate).")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="lista los hilos recientes (de la última lectura)")
    r = sub.add_parser("responder", help="crea un borrador de respuesta (no envía)")
    r.add_argument("--to", required=True, help="@handle (de la última lectura) o IGSID directo")
    r.add_argument("--texto", help="texto de la respuesta, ya en tu voz")
    r.add_argument("--texto-file", dest="texto_file", help="fichero con el texto de la respuesta")
    r.add_argument("--dry", action="store_true", help="enseña lo que se draftearía, sin escribir nada")
    args = p.parse_args(argv)
    if args.cmd == "responder":
        return cmd_responder(args)
    return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
