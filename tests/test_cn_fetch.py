#!/usr/bin/env python3
"""tests/test_cn_fetch.py — que cn_fetch NO dé por leída una página que no leyó (20-sep-2026).

Nació de un hallazgo real: la ficha CTR20263054 del CDE devolvía 25.350 B por la vía «directo»
y `cn_fetch` la daba por buena. Eran scripts de reto anti-bot: 1 carácter visible y ni rastro
del id del ensayo. El sistema creía que había leído la ficha. Eso es falsa certeza, y el muro
prefiere «no pude» a un éxito inventado.

Sin red: todos los casos son texto sintético contra `valida()`, más un `fetch()` con vías
falsas para comprobar que una vía mala no corta la búsqueda y que la buena sí se devuelve.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "tools"))

import cn_fetch  # noqa: E402

FALLOS, PASADOS = [], []
URL_CDE = "http://www.chinadrugtrials.org.cn/clinicaltrials.searchlistdetail.dhtml?id=CTR20263054"


def ok(caso, condicion, detalle=""):
    if condicion:
        PASADOS.append(caso)
        print(f"  ok  {caso}")
    else:
        FALLOS.append(caso)
        print(f"  FALLO  {caso} {detalle}")


# 1. El caso que lo motivó: mucho HTML, cero texto, scripts de reto.
reto = ("<html><head><script src='/4QbVtADbnLVIc/c.FxJzG50F.js'></script>"
        "<script>window._cf_chl_opt={cType:'managed'};</script></head>"
        "<body><script>" + "var a=1;" * 3000 + "</script></body></html>")
ok("el reto anti-bot del CDE NO pasa", bool(cn_fetch.valida(reto, URL_CDE)),
   f"-> valida() devolvió {cn_fetch.valida(reto, URL_CDE)!r}")
ok("el reto pesa más que MIN_LEN (si no, el test no prueba nada)", len(reto) > cn_fetch.MIN_LEN)

# 2. Página larga y legible, pero de OTRO ensayo: el id de la URL no está.
otro = "<html><body><p>" + ("登记号 CTR20259999 试验专业题目 乳腺癌 " * 200) + "</p></body></html>"
ok("una ficha que no lleva el id de la URL NO pasa", "CTR20263054" in cn_fetch.valida(otro, URL_CDE))

# 3. La ficha correcta sí pasa.
buena = ("<html><body><h1>登记号 CTR20263054</h1><p>"
         + ("SSGJ-612 联合靶向HER2的ADC治疗HR阳性、HER2低表达转移性乳腺癌的II期临床研究 " * 60)
         + "</p></body></html>")
ok("la ficha de verdad SÍ pasa", cn_fetch.valida(buena, URL_CDE) == "")

# 4. Respuesta corta y 403 siguen cayendo (no se perdió lo que ya funcionaba).
ok("respuesta corta cae", "corta" in cn_fetch.valida("hola", URL_CDE))
ok("403 cae", "403" in cn_fetch.valida("403 Forbidden" + "x" * 2000, ""))

# 5. Sin id en la URL no se inventa una exigencia: una página legible pasa.
generica = "<html><body>" + ("texto legible de una web china cualquiera " * 60) + "</body></html>"
ok("sin id en la URL, una página legible pasa", cn_fetch.valida(generica, "http://x.cn/") == "")

# 6. texto_visible no cuenta el JavaScript como texto.
ok("texto_visible ignora <script>", len(cn_fetch.texto_visible(reto)) < cn_fetch.MIN_VISIBLE)

# 7. fetch(): una vía que devuelve el reto NO corta; la siguiente, buena, se devuelve.
cn_fetch.VIAS = [("falsa-reto", lambda u: reto), ("falsa-buena", lambda u: buena)]
via, texto, errores = cn_fetch.fetch(URL_CDE)
ok("fetch salta la vía del reto y se queda con la buena", via == "falsa-buena", f"-> via={via!r}")
ok("fetch deja escrito por qué descartó la mala", any("reto" in e for e in errores), f"-> {errores}")

# 8. `requiere` manda: si la cadena no está, esa vía no vale.
via2, _t2, _e2 = cn_fetch.fetch(URL_CDE, requiere="SI-B036")
ok("fetch con --requiere rechaza la página que no lo contiene", via2 == "", f"-> via={via2!r}")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_cn_fetch: %d/%d OK" % (len(PASADOS), len(PASADOS)))
sys.exit(1 if FALLOS else 0)
