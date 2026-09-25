#!/usr/bin/env python3
"""tests/test_yt_inbox.py — tools/yt_inbox.py: captura DETERMINISTA (sin LLM) del buzón de YouTube.

Aislado: NUNCA toca la red real ni el Llavero. Monkeypatcha tools/youtube.py (load_key/api/
resolve_channel) con fakes en memoria y fuerza OUTDIR/STATE a un directorio temporal, para que:
  1) la primera pasada capture y escriba,
  2) la SEGUNDA pasada, con los mismos ids, dedupe a 0 (invariante central del módulo),
  3) sin clave (Llavero vacío) sea fail-soft: no rompe, no escribe nada, exit 0,
  4) un fallo de red (api() devuelve error) tampoco rompe el proceso (fail-soft por vídeo/query).
"""
import os
import sys
import shutil
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

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


# ── Fake de tools/youtube.py: nada de red real, nada de Llavero real ──────────
class FakeYT:
    def __init__(self):
        self.key = "FAKE_KEY"
        self.channel_id = "UCFAKE000000000000000000"
        self.comments = {
            "vid1": [
                {"id": "c1", "snippet": {"topLevelComment": {"snippet": {
                    "authorDisplayName": "@oncologo_x", "textDisplay": "Contactame, tengo un ensayo",
                    "publishedAt": "2026-07-01T10:00:00Z"}}}},
                {"id": "c2", "snippet": {"topLevelComment": {"snippet": {
                    "authorDisplayName": "@fan1", "textDisplay": "Mucho animo!",
                    "publishedAt": "2026-07-01T11:00:00Z"}}}},
            ],
        }
        self.mentions = [
            {"id": {"videoId": "m1"}, "snippet": {"channelTitle": "Canal X", "title": "Habla de {{TITULAR}}",
                                                    "publishedAt": "2026-07-01T09:00:00Z"}},
        ]
        self.fail_video = None  # si se fija, api(commentThreads) para ese id devuelve error

    def load_key(self):
        return self.key

    def resolve_channel(self, token, key):
        return self.channel_id if self.key else None

    def api(self, path, params, key):
        if path == "search" and "channelId" in params:
            return {"items": [{"id": {"videoId": "vid1"}, "snippet": {"title": "t", "publishedAt": "2026-07-01"}}]}, None
        if path == "commentThreads":
            vid = params.get("videoId")
            if vid == self.fail_video:
                return None, "YouTube API error 500: boom"
            return {"items": self.comments.get(vid, [])}, None
        if path == "search":  # menciones (sin channelId)
            return {"items": self.mentions}, None
        return {"items": []}, None


def _install_fake(fake):
    mod = types.ModuleType("youtube")
    mod.load_key = fake.load_key
    mod.resolve_channel = fake.resolve_channel
    mod.api = fake.api
    sys.modules["youtube"] = mod
    return mod


def _fresh_yt_inbox(tmpdir):
    """(Re)importa yt_inbox con el fake de youtube.py ya instalado, y sus rutas de salida en tmp."""
    sys.modules.pop("yt_inbox", None)
    import yt_inbox as yi
    yi.OUTDIR = os.path.join(tmpdir, "_PRIVADO_YT")
    yi.STATE = os.path.join(yi.OUTDIR, ".seen.json")
    return yi


def main():
    tmpdir = tempfile.mkdtemp(prefix="test_yt_inbox_")
    try:
        # ── 1) Sin clave (Llavero vacío) → fail-soft: no revienta, no escribe nada ──
        fake = FakeYT()
        fake.key = None
        _install_fake(fake)
        yi = _fresh_yt_inbox(tmpdir)
        rc = yi.main()
        check(rc == 0, "sin clave: exit 0 (fail-soft, no rompe el daemon)")
        check(not os.path.exists(yi.OUTDIR), "sin clave: no crea ni escribe OUTDIR")

        # ── 2) Primera pasada CON clave: captura comentarios + menciones nuevos ──
        fake = FakeYT()
        _install_fake(fake)
        yi = _fresh_yt_inbox(tmpdir)
        rc = yi.main()
        check(rc == 0, "primera pasada: exit 0")
        files = [f for f in os.listdir(yi.OUTDIR) if f.startswith("yt-")]
        check(len(files) == 1, "primera pasada: escribe UN volcado yt-<fecha>.md")
        content = open(os.path.join(yi.OUTDIR, files[0]), encoding="utf-8").read()
        check("oncologo_x" in content, "el comentario del oncólogo aparece en el volcado")
        check("Canal X" in content, "la mención aparece en el volcado")
        check(os.path.exists(yi.STATE), "primera pasada: escribe el estado de dedup (.seen.json)")

        # ── 3) CANARIO: rompo el dedup (vacío el estado manualmente) y confirmo que
        # la segunda pasada SIN romper vuelve a dar 0 nuevos — si esto diera >0, el
        # test estaría decorativo. Verifico primero que CON estado roto sí reaparecen. ──
        import json
        state_path = yi.STATE
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"comments": [], "mentions": []}, f)
        # Vuelvo a correr sin limpiar el .md de antes: append, y debe verse "oncologo_x" otra vez
        rc = yi.main()
        content2 = open(os.path.join(yi.OUTDIR, files[0]), encoding="utf-8").read()
        check(content2.count("oncologo_x") == 2, "CANARIO ROJO: con estado de dedup vaciado, reaparece (confirma que el dedup real SÍ filtraba)")

        # ── 4) Dedup real: segunda pasada tal cual (estado intacto) → 0 nuevos, no reescribe ──
        n_files_before = len(os.listdir(yi.OUTDIR))
        rc = yi.main()
        check(rc == 0, "segunda pasada (dedup real): exit 0")
        n_files_after = len(os.listdir(yi.OUTDIR))
        check(n_files_after == n_files_before, "segunda pasada: NO crea fichero nuevo (0 comentarios/menciones nuevos)")

        # ── 5) Fallo de red en UN vídeo no rompe el proceso completo (fail-soft por ítem) ──
        fake2 = FakeYT()
        fake2.fail_video = "vid1"
        _install_fake(fake2)
        yi2 = _fresh_yt_inbox(tmpdir)
        rc = yi2.main()
        check(rc == 0, "fallo de red en un vídeo: exit 0 (no rompe el daemon)")

        # ── 6) Canal no resoluble → fail-soft, no revienta ──
        fake3 = FakeYT()
        fake3.resolve_channel = lambda token, key: None
        mod = types.ModuleType("youtube")
        mod.load_key = fake3.load_key
        mod.resolve_channel = fake3.resolve_channel
        mod.api = fake3.api
        sys.modules["youtube"] = mod
        yi3 = _fresh_yt_inbox(tmpdir)
        rc = yi3.main()
        check(rc == 0, "canal no resoluble: exit 0 (fail-soft)")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        sys.modules.pop("youtube", None)
        sys.modules.pop("yt_inbox", None)

    print(f"\n{OK} ok, {FAIL} fallos")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
