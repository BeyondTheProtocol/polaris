#!/usr/bin/env python3
"""test_buzon_ideas.py — captura DETERMINISTA de enlaces al buzón, desacoplada del análisis.

Aísla casa base en un tmp (BTP_REPO) — el buzón vivo se resuelve a casa base, nunca al
worktree. Verifica que:
  · un mensaje con URL → aparece el stub «⏳ pendiente de minar» en el buzón;
  · un segundo mensaje con la MISMA URL no la duplica (dedup);
  · varias URLs en un mensaje → varias entradas; repetida dentro del mismo mensaje no duplica;
  · la extracción limpia la puntuación de cierre pegada (»,, ).) y respeta paréntesis legítimos;
  · es FAIL-SOFT (un buzón inescribible no lanza) y OFFLINE (no abre nada);
  · el enganche en bot_telegram.handle_message captura sin romper el flujo del lazo.
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_buzon_")
# Casa base aislada: BTP_REPO override + BTP_STATE_DIR para la cola del bot.
os.environ["BTP_REPO"] = _TMP
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import buzon_ideas    # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _buzon_texto():
    try:
        with open(buzon_ideas.BUZON, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def main():
    # El buzón debe resolver a casa base (BTP_REPO), no al árbol del worktree.
    check("buzón resuelve a casa base (BTP_REPO)", buzon_ideas.BUZON.startswith(_TMP))

    # 1. mensaje con URL → stub «⏳ pendiente de minar» en el buzón.
    nuevas = buzon_ideas.capturar("mira esto: https://www.instagram.com/reel/ABC123/")
    txt = _buzon_texto()
    check("captura 1 URL", nuevas == ["https://www.instagram.com/reel/ABC123/"])
    check("aparece el stub pendiente", "⏳ pendiente de minar" in txt)
    check("aparece la URL", "https://www.instagram.com/reel/ABC123/" in txt)
    check("lleva sello de fecha en backticks", "`20" in txt)

    # 2. segundo mensaje con la MISMA URL → no duplica (dedup).
    nuevas2 = buzon_ideas.capturar("otra vez https://www.instagram.com/reel/ABC123/")
    txt2 = _buzon_texto()
    check("dedup: no añade la URL repetida", nuevas2 == [])
    check("dedup: sigue habiendo UNA sola ocurrencia",
          txt2.count("https://www.instagram.com/reel/ABC123/") == 1)

    # 3. varias URLs en un mensaje + una repetida dentro del mismo mensaje.
    nuevas3 = buzon_ideas.capturar(
        "dos links https://x.com/a/status/1 y https://x.com/b/status/2 "
        "y otra vez https://x.com/a/status/1")
    check("captura las 2 URLs nuevas, sin la repetida intra-mensaje",
          nuevas3 == ["https://x.com/a/status/1", "https://x.com/b/status/2"])
    txt3 = _buzon_texto()
    check("x.com/a aparece una sola vez", txt3.count("https://x.com/a/status/1") == 1)
    check("x.com/b aparece una sola vez", txt3.count("https://x.com/b/status/2") == 1)

    # 4. limpieza de puntuación de cierre pegada y respeto de paréntesis legítimos.
    check("recorta punto final",
          buzon_ideas.extraer_urls("ve a https://foo.com/bar.") == ["https://foo.com/bar"])
    check("recorta paréntesis/comilla de cierre de prosa",
          buzon_ideas.extraer_urls("(https://foo.com/x)") == ["https://foo.com/x"])
    check("respeta paréntesis legítimos de la URL",
          buzon_ideas.extraer_urls("https://en.wikipedia.org/wiki/Foo_(bar)")
          == ["https://en.wikipedia.org/wiki/Foo_(bar)"])
    check("acepta www. sin esquema",
          buzon_ideas.extraer_urls("www.ejemplo.com/ruta") == ["www.ejemplo.com/ruta"])

    # 5. mensaje SIN URL → no escribe nada nuevo.
    antes = _buzon_texto()
    n0 = buzon_ideas.capturar("hola, ¿cómo va todo?")
    check("sin URL → no captura", n0 == [])
    check("sin URL → buzón intacto", _buzon_texto() == antes)

    # 6. FAIL-SOFT: un buzón apuntando a una ruta imposible NO lanza (devuelve []).
    saved = buzon_ideas.BUZON
    try:
        buzon_ideas.BUZON = os.path.join(_TMP, "no", "\x00bad", "x.md")  # ruta inválida
        r = buzon_ideas.capturar("https://falla.example/x")
        check("fail-soft: error tragado, devuelve []", r == [])
    finally:
        buzon_ideas.BUZON = saved

    # 7. enganche en el lazo: handle_message captura la URL sin romper el flujo.
    import salida          # noqa: E402
    import cola as q      # noqa: E402
    import bot_telegram    # noqa: E402

    reports = []
    bot_telegram.salida.report_to_titular = lambda text, **kw: reports.append(text) or {"delivered": True}
    bot_telegram.handle_message({"kind": "text", "text": "guárdame https://nuevo.example/articulo"})
    txt7 = _buzon_texto()
    check("handle_message deposita la URL en el buzón", "https://nuevo.example/articulo" in txt7)
    check("handle_message sigue su flujo (encola/responde)", len(reports) >= 1)
    # y el flujo de acción de siempre no se rompe: hay un job de triage encolado.
    st = q.get_status()
    check("handle_message encola el triaje como siempre", st.get("pending", 0) >= 1)

    print("RESULTADO buzón-ideas: %d OK, %d fallos" % (_pass, _fail))
    print("✅ BUZÓN-IDEAS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
