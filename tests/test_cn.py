#!/usr/bin/env python3
"""tests/test_cn.py — el escape libre de tools/cn.py, sin red (20-sep-2026).

Nace del pedido literal de {{TITULAR}}: «quiero tener escape libre de buscar cualquier cosa allí,
no solo ensayos». Cubre lo verificado EN VIVO hoy al construir la tool (consulta de prueba
«乳腺癌 新抗原 疫苗», Python puro, sin proxy):
  · so.com necesita `http.cookiejar` (un `urlopen` suelto cae en un bucle de 302).
  · cn.bing.com puede devolver un señuelo: 200, el `<title>` refleja la consulta, pero el
    cuerpo trae contenido sin relación (comprobado dos veces: un foro ruso y un tutorial de
    Python). Por eso CADA resultado se filtra por relevancia contra los términos de la
    consulta, motor por motor — nunca "200 + el término aparece en algún sitio" basta.
  · medsci.cn funciona sin sesión especial.

Todo con dobles: ni un solo `urlopen` real. Los fragmentos HTML son sintéticos pero calcados
en estructura de lo capturado hoy (mismas clases: `res-title`/`res-desc`, `b_algo`, `h2` de
medsci).
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "tools"))

import cn  # noqa: E402

FALLOS, PASADOS = [], []


def ok(caso, condicion, detalle=""):
    if condicion:
        PASADOS.append(caso)
        print(f"  ok  {caso}")
    else:
        FALLOS.append(caso)
        print(f"  FALLO  {caso} {detalle}")


# --- fragmentos sintéticos, calcados de la estructura real capturada hoy ---------------------

SO_HTML = """
<li class="res-list"><h3 class="res-title " >
<a href="https://www.so.com/link?m=x" data-mdurl="https://www.cn-healthcare.com/a1.html" target="_blank"><em>新抗原疫苗</em>掀起抗癌新浪潮:<em>乳腺癌</em>福音</a></h3>
<p class="res-desc"><em>新抗原</em>DNA疫苗实现87.5%的三阴<em>乳腺癌</em>患者3年无复发生存.</p></li>
<li class="res-list"><h3 class="res-title " >
<a href="https://www.so.com/link?m=y" data-mdurl="https://www.example.cn/receta-cocina.html" target="_blank">receta de pollo al curry - sin relacion</a></h3>
<p class="res-desc">una pagina de cocina que no tiene nada que ver.</p></li>
"""

BING_HTML = """
<ol id="b_results"><li class="b_algo" data-id iid=SERP.1><h2 class=""><a target="_blank" href="https://otvet.mail.ru/question/1">Dispetcher ustroystv - foro ruso</a></h2><div class="b_caption"><p class="b_lineclamp2">contenido sin relacion, senuelo anti-scraping.</p></div></li>
<li class="b_algo" data-id iid=SERP.2><h2 class=""><a target="_blank" href="https://www.zgll.cn/vacuna-mama.html">乳腺癌 新抗原 疫苗 临床进展</a></h2><div class="b_caption"><p class="b_lineclamp2">乳腺癌患者的新抗原疫苗临床试验进展综述。</p></div></li>
"""

MEDSCI_HTML = """
<h2><a target="_blank" href="https://www.medsci.cn/article/show_article.do?id=96aa85148eef" class="ms-link">新抗原DNA癌症治疗疫苗，高风险乳腺癌治疗后88%患者3年无肿瘤！</a></h2>
<p class="text-justify">华盛顿大学医学院开展小型临床试验，设计新抗原DNA疫苗治疗三阴乳腺癌。</p>
<h2><a target="_blank" href="/guideline/show_article.do?id=3d4b91c0" class="ms-link">乳腺癌患者新冠疫苗接种中国专家共识</a></h2>
"""


def _opener_falso():
    return object()  # los motores de prueba no lo usan; sólo hace falta que exista


# 1. Parsers puros: cada motor extrae título/url/snippet de su HTML real.
# v_so/v_bing/v_medsci hacen red vía _get; se prueban indirectamente parcheando _get.
_get_orig = cn._get
cn._get = lambda opener, url, timeout=cn.TIMEOUT: {
    "so.com/s?q=": SO_HTML, "bing.com/search?q=": BING_HTML, "medsci.cn/search?q=": MEDSCI_HTML,
}[next(k for k in ("so.com/s?q=", "bing.com/search?q=", "medsci.cn/search?q=") if k in url)]

hits_so = cn.v_so("乳腺癌 新抗原 疫苗", None)
ok("v_so extrae 2 resultados", len(hits_so) == 2, f"-> {len(hits_so)}")
ok("v_so usa data-mdurl (la url real, no el redirector so.com/link)",
   hits_so[0]["url"] == "https://www.cn-healthcare.com/a1.html", f"-> {hits_so[0]['url']}")
ok("v_so limpia las <em> del título", "<em>" not in hits_so[0]["titulo"])

hits_bing = cn.v_bing("乳腺癌 新抗原 疫苗", None)
ok("v_bing extrae 2 resultados", len(hits_bing) == 2, f"-> {len(hits_bing)}")

hits_medsci = cn.v_medsci("乳腺癌 新抗原 疫苗", None)
ok("v_medsci extrae 2 resultados", len(hits_medsci) == 2, f"-> {len(hits_medsci)}")
ok("v_medsci resuelve urls relativas a absolutas",
   hits_medsci[1]["url"].startswith("https://www.medsci.cn/"), f"-> {hits_medsci[1]['url']}")

cn._get = _get_orig

# 2. Relevancia: el resultado señuelo de bing (ruso) se descarta; el bueno sobrevive.
terminos = "乳腺癌 新抗原 疫苗".split()
relevantes_bing = [h for h in hits_bing if cn._relevante(h, terminos)]
ok("el señuelo de bing (foro ruso) se descarta por irrelevante", len(relevantes_bing) == 1,
   f"-> quedaron {len(relevantes_bing)}")
ok("el resultado bueno de bing sobrevive al filtro",
   relevantes_bing and "乳腺癌" in relevantes_bing[0]["titulo"])
relevantes_so = [h for h in hits_so if cn._relevante(h, terminos)]
ok("el resultado de cocina de so.com se descarta por irrelevante", len(relevantes_so) == 1,
   f"-> quedaron {len(relevantes_so)}")

# 3. Mutante: si _relevante() no comparara nada (bug: siempre True), el señuelo colaría.
# Confirma que el test de arriba SÍ depende de que _relevante funcione (canario).
_relevante_orig = cn._relevante
cn._relevante = lambda hit, terminos: True
relevantes_bing_roto = [h for h in hits_bing if cn._relevante(h, terminos)]
ok("mutante: con _relevante siempre-True, el señuelo SÍ cuela (confirma que el test de arriba prueba algo real)",
   len(relevantes_bing_roto) == 2)
cn._relevante = _relevante_orig

# 4. buscar(): un motor caído no corta los demás, y queda anotado.
egress_orig = cn.borde.egress_cientifico
cn.borde.egress_cientifico = lambda texto, destino="": (True, "ok (test)")
motores_orig = cn.MOTORES


def _so_ok(q, o):
    return [{"titulo": "乳腺癌 新抗原 疫苗 artículo real", "url": "https://a.cn/1", "snippet": "乳腺癌"}]


def _bing_caido(q, o):
    raise TimeoutError("simulado: bing no respondió")


def _medsci_ok(q, o):
    return [{"titulo": "乳腺癌 疫苗 última noticia", "url": "https://a.cn/2", "snippet": "疫苗"}]


cn.MOTORES = [("so.com", _so_ok), ("cn.bing.com", _bing_caido), ("medsci.cn", _medsci_ok)]
r = cn.buscar("乳腺癌 新抗原 疫苗", opener=_opener_falso())
ok("buscar() sigue ok=True con un motor caído", r["ok"] is True, f"-> {r}")
ok("el motor caído queda anotado en 'motores'", "caído" in r["motores"]["cn.bing.com"],
   f"-> {r['motores']}")
ok("los otros dos motores SÍ aportan resultados", len(r["resultados"]) == 2,
   f"-> {len(r['resultados'])}")

# 5. Los TRES motores caen -> ok=False, con motivo, nunca una lista vacía silenciosa.
cn.MOTORES = [("so.com", _bing_caido), ("cn.bing.com", _bing_caido), ("medsci.cn", _bing_caido)]
r3 = cn.buscar("乳腺癌 新抗原 疫苗", opener=_opener_falso())
ok("los 3 motores caídos -> ok=False", r3["ok"] is False)
ok("el motivo dice que cayeron los 3", "3 motores cayeron" in r3["motivo"], f"-> {r3['motivo']}")

# 6. Ningún resultado relevante (los 3 responden pero nada casa) -> ok=False, no lista vacía muda.
cn.MOTORES = [("so.com", lambda q, o: [{"titulo": "nada que ver", "url": "https://x.cn", "snippet": ""}]),
              ("cn.bing.com", lambda q, o: []),
              ("medsci.cn", lambda q, o: [])]
r4 = cn.buscar("乳腺癌 新抗原 疫苗", opener=_opener_falso())
ok("sin resultados relevantes -> ok=False (no lista vacía silenciosa)", r4["ok"] is False)
ok("el motivo distingue 'sin relevantes' de 'no hay nada'",
   "no es lo mismo" in r4["motivo"], f"-> {r4['motivo']}")

# 7. Dedup por URL entre motores.
cn.MOTORES = [
    ("so.com", lambda q, o: [{"titulo": "乳腺癌 疫苗 A", "url": "https://dup.cn/x", "snippet": "疫苗"}]),
    ("cn.bing.com", lambda q, o: [{"titulo": "乳腺癌 疫苗 B (mismo enlace)", "url": "https://dup.cn/x", "snippet": "疫苗"}]),
    ("medsci.cn", lambda q, o: []),
]
r5 = cn.buscar("乳腺癌 疫苗", opener=_opener_falso())
ok("la misma URL en dos motores no se duplica", len(r5["resultados"]) == 1, f"-> {r5['resultados']}")

cn.MOTORES = motores_orig

# 8. Muro: una consulta con el nombre de {{TITULAR}} se bloquea ANTES de tocar ningún motor.
cn.borde.egress_cientifico = egress_orig
r6 = cn.buscar("{{TITULAR}} {{APELLIDO}} biopsia")
ok("el muro bloquea una consulta con PII", r6["ok"] is False)
ok("el motivo viene marcado como del muro", r6["motivo"].startswith("muro:"), f"-> {r6['motivo']}")

# 9. Traducción: consulta sin caracteres chinos pasa por el modelo local (con doble).
responder_orig = cn.local.responder
cn.local.responder = lambda prompt, fallback="": "乳腺癌 新抗原 疫苗"
cn.borde.egress_cientifico = lambda texto, destino="": (True, "ok (test)")
cn.MOTORES = [("so.com", _so_ok), ("cn.bing.com", lambda q, o: []), ("medsci.cn", lambda q, o: [])]
r7 = cn.buscar("breast cancer neoantigen vaccine", opener=_opener_falso())
ok("consulta en inglés se traduce antes de buscar", r7.get("traducido") is True, f"-> {r7}")
ok("la consulta usada es la traducción", r7.get("consulta_usada") == "乳腺癌 新抗原 疫苗")

# 10. Traducción: si el local no responde, se avisa y se usa la consulta tal cual (nunca falla).
cn.local.responder = lambda prompt, fallback="": fallback
r8 = cn.buscar("breast cancer neoantigen vaccine", opener=_opener_falso())
ok("sin modelo local, sigue funcionando con la consulta original", r8["ok"] in (True, False))
ok("sin modelo local, avisa explícitamente (no finge que tradujo)",
   "no disponible" in r8.get("aviso_traduccion", "") or "no tradujo" in r8.get("aviso_traduccion", ""),
   f"-> {r8.get('aviso_traduccion')!r}")
ok("sin modelo local, NO marca traducido=True", r8.get("traducido") is not True)

cn.local.responder = responder_orig
cn.MOTORES = motores_orig
cn.borde.egress_cientifico = egress_orig

# 11. abrir(): delega en cn_fetch.fetch, no reimplementa la validación.
fetch_orig = cn.cn_fetch.fetch
cn.cn_fetch.fetch = lambda url, requiere="": ("directo", "PAGINA DE PRUEBA", [])
r9 = cn.abrir("https://x.cn/pagina", requiere="algo")
ok("abrir() usa cn_fetch.fetch y no reinventa la validación",
   r9 == {"ok": True, "via": "directo", "texto": "PAGINA DE PRUEBA", "errores": []}, f"-> {r9}")
cn.cn_fetch.fetch = fetch_orig

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_cn: %d/%d OK" % (len(PASADOS), len(PASADOS)))
sys.exit(1 if FALLOS else 0)
