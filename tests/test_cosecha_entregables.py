#!/usr/bin/env python3
"""test_cosecha_entregables.py — red automática: detecta entregables que se quedaron solo en el chat.
Aislado (BTP_PROJECTS_DIR a un tmp con transcripts sintéticos). Determinista, sin red."""
import datetime
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="cosent_test_")
os.environ["BTP_PROJECTS_DIR"] = _TMP
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cosecha_entregables as ce  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


_NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _deliv(tag):
    """Entregable sustancial: KW (investigación) + estructura (## ×2, Fuentes) + longitud ≥ MIN_LEN."""
    return ("## %s — investigación\n\n" % tag + ("Detalle %s con bastante texto de relleno. " % tag) * 40
            + "\n\n## Recomendación\n\nFuentes:\n- uno\n- dos")


def _sesion(nombre, eventos):
    d = os.path.join(_TMP, nombre)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "s.jsonl"), "w", encoding="utf-8") as f:
        for e in eventos:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _msg(text):
    return {"type": "assistant", "timestamp": _NOW, "message": {"content": [{"type": "text", "text": text}]}}


def _write_fv():
    return {"type": "assistant", "timestamp": _NOW,
            "message": {"content": [{"type": "tool_use", "name": "Write",
                                     "input": {"file_path": "/x/00_FUENTE-DE-VERDAD/n.md"}}]}}


def main():
    # proj-a: entregable ALFA SIN archivar → debe flaguearse
    _sesion("proj-a", [_msg(_deliv("ALFA"))])
    # proj-b: entregable BETA pero la sesión SÍ archivó a la fuente → NO se flaguea
    _sesion("proj-b", [_msg(_deliv("BETA")), _write_fv()])
    # proj-c: mensaje corto y sin chicha → no es entregable
    _sesion("proj-c", [_msg("respuesta corta, nada que archivar")])

    cands = ce.cosechar(dias=7)
    textos = " ".join(c["texto"] for c in cands)
    ok("ALFA" in textos, "entregable sustancial SIN archivar → flagueado")
    ok("BETA" not in textos, "entregable en sesión que SÍ archivó a la fuente → NO flagueado (sin falso positivo)")
    ok(len(cands) == 1, "solo 1 candidato (el corto y el ya-archivado quedan fuera): %d" % len(cands))

    # un mensaje largo PERO sin palabra de entregable → no cuenta
    _sesion("proj-d", [_msg("Texto larguísimo de charla. " * 60)])
    cands2 = ce.cosechar(dias=7)
    ok(len(cands2) == 1, "texto largo sin tema-entregable NO se flaguea (sigue habiendo 1)")

    print("RESULTADO cosecha_entregables: %d OK, %d fallos" % (_pass, _fail))
    print("✅ COSECHA_ENTREGABLES EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
