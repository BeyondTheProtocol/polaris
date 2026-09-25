#!/usr/bin/env python3
"""test_casa_estilo.py — la CASA DE ESTILO de los mensajes de Telegram (tools/salida.py).

Verifica, sin tocar la red, el contrato de la capa determinista `_casa_estilo` y su
enganche en el embudo `send()` y en el `caption` de `report_file_to_titular`:

  · Formato:   markdown crudo fuera; guion largo de inciso → coma; viñetas → «•» SOLO a
               inicio de línea; jerga/IDs internos a llano (sin romper genes/chat_id);
               muletillas en inglés; emojis y 💜 conservados; tope partido sin perder texto;
               caption truncado a ≤1000 con «…» (no re-recortado).
  · Voz:       "calida" (defecto) asegura 💜; "sobria" no lo añade.
  · Seguridad: FAIL-OPEN (excepción → texto crudo); IDEMPOTENCIA; alerta_critica NO pasa
               por la capa; OUTWARD intacto (el scrubber ve el texto original); reacciones
               no llaman a la capa; sin red.

Si la capa se amplía, añade aquí el caso (regresión permanente). Modelo de test sin pytest.
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import salida  # noqa: E402

SELF = "999000111"
OTHER = "555000222"

_pass = 0
_fail = 0
_delivers = []           # registro de llamadas al deliverer de TEXTO (la boca hacia fuera)


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def fake_deliver(chat_id, text, reply_to=None):
    _delivers.append((chat_id, text, reply_to))
    return True, "mock"


def setup_module(tmp):
    """Aísla estado y mockea credenciales/entrega (sin red, como test_salida.py)."""
    salida.OUTBOX = os.path.join(tmp, "outbox")
    salida.PENDING = os.path.join(salida.OUTBOX, "pending")
    salida.STATE = tmp
    salida.NOTIF_CFG = os.path.join(tmp, "notif", "config.json")
    salida.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    salida._self_chatid = lambda: SELF
    salida._DELIVERERS = {"telegram": fake_deliver}


# ─────────────────────────────────────────────────────────────────────────────
# A. La función pura _casa_estilo (formato determinista)
# ─────────────────────────────────────────────────────────────────────────────
def test_funcion_pura():
    cs = salida._casa_estilo

    # 1. Markdown crudo fuera.
    out = cs("**negrita** y `code` y [Docu](https://x.io/p)")
    check("md: negrita sin **", "**" not in out and "negrita" in out)
    check("md: code sin backticks", "`" not in out and "code" in out)
    check("md: enlace conserva url por defecto", "Docu" in out and "https://x.io/p" in out)
    h = cs("# Título de cabecera\ncuerpo")
    check("md: cabecera # fuera (inicio de línea)", h.lstrip().startswith("Título"))
    q = cs("> una cita\nsigue")
    check("md: cita > fuera", not q.lstrip().startswith(">") and "una cita" in q)
    tbl = cs("| a | b |\n|---|---|\n| 1 | 2 |")
    check("md: tabla sin pipes", "|" not in tbl and "a" in tbl and "2" in tbl)

    # 2. Guion largo: inciso → coma; viñeta de línea → «•».
    inc = cs("Esto es A — esto es B", voz="sobria")
    check("guion: inciso → coma", "—" not in inc and "Esto es A, esto es B" == inc)
    bl = cs("— primer ítem\n— segundo", voz="sobria")
    check("guion: viñeta de línea → •", bl == "• primer ítem\n• segundo")

    # 3. Viñetas mezcladas → todas «•», SOLO a inicio de línea.
    mix = cs("- uno\n* dos\n· tres\n– cuatro\n• cinco", voz="sobria")
    check("vinetas: todas •", mix == "• uno\n• dos\n• tres\n• cuatro\n• cinco")
    media = cs("El tope es de 3 a 4 cosas · aprox aquí", voz="sobria")
    check("vinetas: «·» a media frase NO se toca", "·" in media)

    # 4. Jerga / IDs internos → llano, con falsos positivos a salvo.
    j = cs("Recibido (ref 9a8b7c6d5e)", voz="sobria")
    check("jerga: (ref hex) fuera", "ref" not in j and j.strip() == "Recibido")
    j2 = cs("Todo OK rc=0 HTTP 200", voz="sobria")
    check("jerga: rc=0 y HTTP 200 fuera", "rc=" not in j2 and "HTTP" not in j2)
    j3 = cs("El dispatcher arrancó", voz="sobria")
    check("jerga: dispatcher→motor sin doble artículo", j3 == "El motor arrancó")
    fp = cs("Gen MET y KIT en chat_id de kb.py", voz="sobria")
    check("jerga: MET/KIT/chat_id/kb.py intactos",
          "MET" in fp and "KIT" in fp and "chat_id" in fp and "kb.py" in fp)
    j4 = cs("kb.py reindexado, rc=0.", voz="sobria")
    check("jerga: borrar deja la puntuación limpia (sin ',.')", j4 == "kb.py reindexado.")
    j5 = cs("A, B, listo.", voz="sobria")
    check("jerga: una coma legítima NO se come", j5 == "A, B, listo.")

    # 5. Muletillas en inglés de sistema.
    m = cs("Warning: revisa esto", voz="sobria")
    check("ingles: Warning:→Aviso:", m.startswith("Aviso:"))
    m2 = cs("La cosa Failed", voz="sobria")
    check("ingles: Failed→Falló", "Falló" in m2 and "Failed" not in m2)

    # 6. Español: acentos/ñ conservados; texto ya correcto no cambia (idempotencia).
    es = cs("Buenos días, niña, sin prisa 💜")
    check("espanol: acentos y ñ conservados", "í" in es and "ñ" in es)
    check("espanol: texto correcto no cambia", es == "Buenos días, niña, sin prisa 💜")

    # 7. Emojis y 💜.
    em = cs("✅ Listo el PDF 🌙")
    check("emojis: se conservan", "✅" in em and "🌙" in em)
    check("emojis: calida añade un 💜", em.count("💜") == 1)
    dup = cs("Hola 💜")
    check("emojis: no añade un segundo 💜", dup.count("💜") == 1)
    sob = cs("Aviso seco", voz="sobria")
    check("voz sobria: sin 💜", "💜" not in sob)
    alert = cs("🔴 Para todo, hay un problema")
    check("emojis: 🔴 no se toca y no recibe 💜", alert.startswith("🔴") and "💜" not in alert)


# ─────────────────────────────────────────────────────────────────────────────
# A2. META-FUGA (bug real 3/7/26): Vega mandó su jerga interna en crudo a
# Telegram — tabla markdown con pipes ("Plazo"/"edo" ilegibles) y meta-comentario
# de sandbox ("habría enviado", nombres de tools, rutas). Barrera determinista
# sobre el prompt (que ya lo prohíbe pero el modelo se lo salta).
# ─────────────────────────────────────────────────────────────────────────────
def test_meta_fuga():
    cs = salida._casa_estilo

    # 1. El mensaje REAL del bug (3/7/26): solo debe sobrevivir el recordatorio útil.
    bug_real = (
        "El entorno de sandbox bloquea la ejecución directa en esta sesión. "
        "Presento el recordatorio que **habría enviado** vía `salida.report_to_titular`:\n\n"
        "Recuerda confirmar el vuelo de Brussels Airlines antes de mañana.\n\n"
        "**📋 Resumen del barrido (para referencia):**\n\n"
        "| Hilo | Plazo | Estado |\n"
        "|----|----|----|\n"
        "| ✈️ Brussels Airlines | lunes 29/6 | `en_curso` → TU ACCIÓN |\n"
        "| 🎫 NMBS tren | bloqueado | por_confirmar |\n"
    )
    out = cs(bug_real)
    check("meta-fuga: bug real → solo el recordatorio útil",
          out == "Recuerda confirmar el vuelo de Brussels Airlines antes de mañana. 💜")
    check("meta-fuga: sin 'sandbox'", "sandbox" not in out.lower())
    check("meta-fuga: sin 'habría enviado'", "habría enviado" not in out.lower())
    check("meta-fuga: sin nombre de herramienta", "salida.report_to_titular" not in out and "salida.py" not in out)
    check("meta-fuga: sin tabla con pipes", "|" not in out)
    check("meta-fuga: sin 'Resumen del barrido'", "resumen del barrido" not in out.lower())
    check("meta-fuga: sin códigos de estado crudos",
          "en_curso" not in out and "por_confirmar" not in out)

    # 2. Frases meta sueltas, una a una (por si el bug real cambia de forma).
    check("meta-fuga: 'no tengo acceso a Bash' fuera",
          "bash" not in cs("No tengo acceso a Bash en este entorno.", voz="sobria").lower())
    check("meta-fuga: 'bloquea la ejecución' fuera",
          "bloquea" not in cs("Algo bloquea la ejecución directa aquí.", voz="sobria").lower())
    check("meta-fuga: ruta tools/… fuera",
          "tools/" not in cs("Mira tools/seguimiento.py para el dato.", voz="sobria"))
    check("meta-fuga: ruta _PRIVADO_… fuera",
          "_PRIVADO_" not in cs("Está en _PRIVADO_CORREO/hilo.txt", voz="sobria"))

    # 3. Códigos de estado crudos → llano, fuera de tabla también (prosa suelta).
    est = cs("El hilo está en_curso todavía; el otro sigue por_confirmar.", voz="sobria")
    check("meta-fuga: en_curso → 'en marcha'", "en marcha" in est and "en_curso" not in est)
    check("meta-fuga: por_confirmar → 'por confirmar'", "por confirmar" in est and "por_confirmar" not in est)

    # 4. CRÍTICO: un mensaje NORMAL con emojis + hora en negrita (itinerario, regla
    #    de {{TITULAR}} de formato tabla/secuencias) debe pasar INTACTO en su contenido,
    #    sin que la barrera de meta-fuga se lo cargue.
    itinerario = ("✈️ **14:30** salida vuelo Brussels Airlines\n"
                  "🚆 **17:10** llegada tren NMBS a Zúrich\n"
                  "🏨 **19:00** check-in hotel")
    out_it = cs(itinerario, voz="sobria")
    check("meta-fuga: itinerario con emoji+hora intacto (líneas)",
          out_it == "✈️ 14:30 salida vuelo Brussels Airlines\n"
                     "🚆 17:10 llegada tren NMBS a Zúrich\n"
                     "🏨 19:00 check-in hotel")

    # 5. Lista con viñetas normales (sin jerga) no se toca.
    lista = cs("- Llamar a la clínica\n- Confirmar el vuelo\n- Avisar a {{CONTACTO}}", voz="sobria")
    check("meta-fuga: lista de viñetas normal intacta",
          lista == "• Llamar a la clínica\n• Confirmar el vuelo\n• Avisar a {{CONTACTO}}")

    # 6. Genes/identificadores legítimos (MET, KIT, chat_id, kb.py) a salvo (no son
    #    meta-fuga aunque compartan substring con un patrón de ruta/tool).
    fp = cs("Gen MET y KIT en chat_id de kb.py, todo en marcha bien", voz="sobria")
    check("meta-fuga: MET/KIT/chat_id/kb.py intactos (no son meta-fuga)",
          "MET" in fp and "KIT" in fp and "chat_id" in fp and "kb.py" in fp)

    # 7. Idempotencia: aplicar la capa dos veces da el mismo resultado.
    once = cs(bug_real)
    twice = cs(once)
    check("meta-fuga: idempotente sobre el bug real", once == twice)

    # 8. No pierde el contenido útil: si TODO el mensaje es meta (sin recordatorio
    #    real detrás), no debe quedar vacío ni romper — cae a fail-open o a texto
    #    residual, nunca lanza excepción.
    solo_meta = "El sandbox bloquea la ejecución directa. Presento lo que habría enviado:"
    out_solo = cs(solo_meta)  # no debe lanzar
    check("meta-fuga: solo-meta no lanza excepción", isinstance(out_solo, str))


# ─────────────────────────────────────────────────────────────────────────────
# A3. FIRMA DE CIERRE (bug real 3/7/26): un mensaje que cierra con «— Nombre»
# (p. ej. "— Vega") se estaba convirtiendo en viñeta "• Nombre" porque
# _normaliza_vinetas trata cualquier "— " a inicio de línea como ítem de lista.
# _extrae_firma la aparta ANTES de ese paso y la reintegra intacta al final.
# ─────────────────────────────────────────────────────────────────────────────
def test_firma_de_cierre():
    cs = salida._casa_estilo

    # 1. Firma real tras un bloque de meta-fuga (el caso cazado en logs 3/7/26):
    #    sobrevive como guion, NUNCA como viñeta "•".
    con_firma = ("Dos cosas del viaje que no se pueden caer este finde:\n\n"
                 "✈️ Vuelo del 1/7 — confirma la asistencia especial.\n\n"
                 "— Vega")
    out = cs(con_firma, voz="sobria")
    check("firma: cierra con guion, no con viñeta", out.rstrip().endswith("— Vega"))
    check("firma: NO se convierte en '• Vega'", "• Vega" not in out)

    # 2. Con 💜 de cierre (voz cálida): la firma sigue siendo guion, el corazón
    #    se añade sin duplicarse ni romper el guion.
    out_calida = cs(con_firma)
    check("firma: voz cálida conserva '— Vega' con 💜 detrás",
          "— Vega" in out_calida and out_calida.count("💜") == 1)

    # 3. Idempotencia: aplicar la capa dos veces sobre un mensaje con firma da lo mismo.
    once = cs(con_firma)
    twice = cs(once)
    check("firma: idempotente", once == twice)

    # 4. Un ÚLTIMO ítem de lista con guion (sin línea en blanco antes) NO es una firma:
    #    no debe apartarse ni tratarse distinto al resto de la lista.
    lista = cs("— primer punto\n— segundo punto\n— Vega", voz="sobria")
    check("firma: último ítem de lista consecutiva se trata como viñeta, no firma",
          lista == "• primer punto\n• segundo punto\n• Vega")

    # 5. Un mensaje que es SOLO la firma (una línea) también se conserva intacto.
    solo_firma = cs("— Vega", voz="sobria")
    check("firma: mensaje de una sola línea con firma intacto", solo_firma == "— Vega")


# ─────────────────────────────────────────────────────────────────────────────
# B. Idempotencia y no-perder-contenido (bloqueantes según VERIF)
# ─────────────────────────────────────────────────────────────────────────────
def test_idempotencia():
    cs = salida._casa_estilo
    casos = [
        "**x** — y\n- z",
        "🔴 socorro, algo va mal",
        "Hola 💜",
        "Recibido (ref 9a8b7c6d5e) dispatcher rc=0",
        "| a | b |\n|---|---|\n| 1 | 2 |",
        "Buenos días, sin prisa",
        "z" * 1500,
    ]
    todos = True
    for c in casos:
        one = cs(c)
        two = cs(one)
        if one != two:
            todos = False
            print("  ✗ idempotencia rompe en %r → %r → %r" % (c[:30], one[:30], two[:30]))
    check("idempotencia: _casa_estilo(_casa_estilo(t)) == _casa_estilo(t)", todos)
    # caption idempotente también.
    cap = cs("x" * 1500, tipo="caption")
    check("idempotencia: caption", cs(cap, tipo="caption") == cap)


def test_no_pierde_contenido():
    cs = salida._casa_estilo
    # Un texto no vacío NUNCA debe quedar vacío tras la capa.
    for t in ["hola", "✅ listo", "- uno\n- dos", "**negrita**", "MET KIT chat_id"]:
        out = cs(t)
        check("no-vacio: %r mantiene contenido" % t[:18], out.strip() != "")


# ─────────────────────────────────────────────────────────────────────────────
# C. Partido (no recortar): texto largo → varios trozos sin perder palabras
# ─────────────────────────────────────────────────────────────────────────────
def test_partido():
    cs = salida._casa_estilo
    body = "\n".join("Línea %d con texto de relleno suficiente para sumar caracteres." % i
                     for i in range(200))
    estilizado = cs(body, voz="sobria")
    trozos = salida._partir_telegram(estilizado)
    check("partido: produce varios trozos", len(trozos) > 1)
    check("partido: ningún trozo > tope", all(len(x) <= salida._TG_TOPE_TEXTO for x in trozos))
    check("partido: cada trozo lleva pie (i/n)",
          all(re.search(r"\(\d+/\d+\)\s*$", x) for x in trozos))
    # Reconstruir sin los pies → no se pierden palabras significativas.
    sin_pie = [re.sub(r"\n\(\d+/\d+\)\s*$", "", x) for x in trozos]
    recon = " ".join(" ".join(s.split()) for s in sin_pie)
    palabras_orig = set(estilizado.split())
    palabras_recon = set(recon.split())
    check("partido: no pierde palabras", palabras_orig <= palabras_recon)
    # Un texto corto → un solo trozo, sin pie.
    uno = salida._partir_telegram(cs("corto", voz="sobria"))
    check("partido: corto = 1 trozo sin pie", len(uno) == 1 and "(1/" not in uno[0])


# ─────────────────────────────────────────────────────────────────────────────
# D. Caption: truncado a ≤1000 con «…», sin doble recorte
# ─────────────────────────────────────────────────────────────────────────────
def test_caption():
    cs = salida._casa_estilo
    out = cs("z" * 1500, tipo="caption")
    check("caption: ≤1000", len(out) <= salida._TG_TOPE_CAPTION)
    check("caption: termina en …", out.endswith("…"))
    # No se re-recorta: el deliverer hace caption[:1000]; el «…» debe sobrevivir.
    check("caption: «…» sobrevive a un caption[:1000]", out[:1000].endswith("…"))
    corto = cs("pie corto", tipo="caption")
    check("caption: corto no se trunca", "…" not in corto and "pie corto" in corto)


# ─────────────────────────────────────────────────────────────────────────────
# E. Fail-open: una excepción dentro de la capa → texto crudo, nunca lanza
# ─────────────────────────────────────────────────────────────────────────────
def test_fail_open():
    cs = salida._casa_estilo
    # None → "" sin lanzar.
    check("fail-open: None → ''", cs(None) == "")
    # Forzar que un paso interno lance: monkeypatch de _quitar_markdown.
    orig = salida._quitar_markdown
    salida._quitar_markdown = lambda t: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        crudo = "**texto** que NO debe perderse aunque la capa reviente"
        out = cs(crudo)
        check("fail-open: excepción → texto crudo idéntico", out == crudo)
    finally:
        salida._quitar_markdown = orig


# ─────────────────────────────────────────────────────────────────────────────
# F. Enganche en send(): REPORT se maquilla; OUTWARD intacto; reacciones fuera
# ─────────────────────────────────────────────────────────────────────────────
def test_send_engancha_report():
    _delivers.clear()
    r = salida.send("telegram", "report", None, "**hola** {{TITULAR}}")
    check("send: REPORT entrega", r["delivered"] and len(_delivers) == 1)
    entregado = _delivers[0][1]
    check("send: REPORT maquillado (sin ** y con 💜)",
          "**" not in entregado and "💜" in entregado)

    # voz sobria → sin 💜
    _delivers.clear()
    salida.send("telegram", "report", None, "aviso seco", voz="sobria")
    check("send: voz sobria sin 💜", "💜" not in _delivers[0][1])

    # texto largo → varios trozos (varias llamadas al deliverer), nada perdido.
    _delivers.clear()
    largo = "\n".join("Línea %d de relleno con bastante texto para llenar el tope." % i
                      for i in range(200))
    r = salida.send("telegram", "report", None, largo, voz="sobria")
    check("send: largo entrega en varios trozos", r["delivered"] and len(_delivers) > 1)
    check("send: trozos reportados en veredicto", r.get("trozos", 1) == len(_delivers))


def test_outward_no_se_maquilla():
    """OUTWARD va a borrador con el texto TAL CUAL (el scrubber de fuga ve el original)."""
    _delivers.clear()
    crudo = "**contacto** con — guion y `code`"
    r = salida.send("telegram", "contact", OTHER, crudo)
    check("outward: bloqueado a borrador", r["blocked"] and "draft" in r)
    check("outward: no toca el deliverer", len(_delivers) == 0)
    # Leer el borrador y comprobar que el texto NO se maquilló.
    draft_path = os.path.join(salida.PENDING, r["draft"])
    import json
    d = json.load(open(draft_path, encoding="utf-8"))
    check("outward: texto del borrador sin maquillar", d["texto"] == crudo)


def test_alerta_no_pasa_por_capa():
    """alerta_critica entrega directo, SIN _casa_estilo (VERIF AGUJERO 1)."""
    _delivers.clear()
    crudo = "🔴 **socorro** — algo va mal con `code`"
    r = salida.alerta_critica(crudo)
    check("alerta: entrega", r["delivered"])
    check("alerta: texto SIN maquillar (idéntico al crudo)",
          len(_delivers) == 1 and _delivers[0][1] == crudo)


def test_alerta_entrega_aunque_capa_reviente():
    """Aunque _casa_estilo estuviera roto, la alerta sigue saliendo (no la usa)."""
    orig = salida._casa_estilo
    salida._casa_estilo = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        _delivers.clear()
        r = salida.alerta_critica("🔴 sigue saliendo")
        check("alerta: sale aunque la capa reviente", r["delivered"] and len(_delivers) == 1)
    finally:
        salida._casa_estilo = orig


def test_reaccion_no_llama_capa():
    """react_to_titular no lleva prosa → no debe llamar a _casa_estilo."""
    llamada = {"n": 0}
    orig = salida._casa_estilo

    def spy(*a, **k):
        llamada["n"] += 1
        return orig(*a, **k)
    salida._casa_estilo = spy
    # Mock del deliverer de reacción para no tocar red.
    orig_react = salida._deliver_telegram_reaction
    salida._deliver_telegram_reaction = lambda cid, mid, emoji: (True, "mock")
    try:
        salida.react_to_titular(12345, "👍")
        check("reaccion: no invoca la casa de estilo", llamada["n"] == 0)
    finally:
        salida._casa_estilo = orig
        salida._deliver_telegram_reaction = orig_react


def test_sin_red():
    """La capa pura no abre sockets (se ejecuta sin token ni deliverer real)."""
    # _casa_estilo no toca el deliverer; basta con que devuelva un str.
    out = salida._casa_estilo("texto cualquiera con **md**")
    check("sin-red: devuelve str sin tocar el canal", isinstance(out, str))


def main():
    tmp = tempfile.mkdtemp(prefix="test_casa_estilo_")
    setup_module(tmp)
    test_funcion_pura()
    test_meta_fuga()
    test_firma_de_cierre()
    test_idempotencia()
    test_no_pierde_contenido()
    test_partido()
    test_caption()
    test_fail_open()
    test_send_engancha_report()
    test_outward_no_se_maquilla()
    test_alerta_no_pasa_por_capa()
    test_alerta_entrega_aunque_capa_reviente()
    test_reaccion_no_llama_capa()
    test_sin_red()
    print("RESULTADO casa de estilo: %d OK, %d fallos" % (_pass, _fail))
    print("✅ CASA DE ESTILO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
