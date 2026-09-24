#!/usr/bin/env python3
"""tests/test_cde_fetch.py — que el parsing del CDE lea lo que de verdad manda el sitio (20-sep-2026).

Sin red: los fragmentos HTML de aquí son SINTÉTICOS pero calcados en estructura (mismas clases,
mismos atributos `id=`/`name=`, mismas etiquetas chinas) de lo que devolvió de verdad
chinadrugtrials.org.cn hoy, capturado en vivo con Python puro (sin navegador) al construir
`tools/cde_fetch.py`:
  · búsqueda `indication=乳腺癌`, `secondLevel=1` → 200, "共 <i>821</i> 条记录" en 42 páginas.
  · ficha por id interno de 32 hex → 200, con 主要研究者/电话/Email/入选标准/排除标准.
  · ficha pedida por el código CTRxxxxxxxx en vez del hash → "获取信息失败！" (no hay nada que leer).
  · búsqueda sin resultados → "暂无数据..." y "共 <i>0</i> 条记录".

También cubre, con doble (una función `_post` sustituida), el hallazgo de HOY que CORRIGE al
libro de deuda: una sesión que manda el formulario COMPLETO de 18 campos consigue los mismos
821 resultados que una sesión que sólo manda 3 — la deuda 'cde-secondlevel-sin-test-ni-
implementar' asumía que omitir campos rompía la búsqueda y que sólo `secondLevel=1` filtraba;
probado hoy con sesiones limpias, ninguna de las dos cosas se sostuvo. Lo que se prueba aquí es
lo que SÍ se verificó: que `SesionCDE.buscar()` manda los 18 campos igualmente (por si acaso
un navegador real se comporta distinto) y que el parser interpreta bien lo que vuelva.
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "tools"))

import cde_fetch  # noqa: E402

FALLOS, PASADOS = [], []


def ok(caso, condicion, detalle=""):
    if condicion:
        PASADOS.append(caso)
        print(f"  ok  {caso}")
    else:
        FALLOS.append(caso)
        print(f"  FALLO  {caso} {detalle}")


# --- fixture: página de listado con 2 filas, calcada de la estructura real -------------------
LISTADO = """
<div>
<table border="0" cellspacing="0" cellpadding="0" class="searchTable">
<tr class="Tab_title">
<th>序号</th><th>登记号</th><th>试验状态</th><th>药物名称</th><th>适应症</th><th>试验通俗题目</th>
</tr>
<tr style=" color:#535353">
<td height="40" >&nbsp;1</td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="cef5d26806204eca8fc5d04a96a30c2f" name="1">CTR20263478</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="cef5d26806204eca8fc5d04a96a30c2f" name="1">进行中&nbsp;尚未招募</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="cef5d26806204eca8fc5d04a96a30c2f" name="1">BYL719</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="cef5d26806204eca8fc5d04a96a30c2f" name="1">晚期乳腺癌</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="cef5d26806204eca8fc5d04a96a30c2f" name="1">Alpelisib继续用药研究</a></td>
</tr>
<tr style=" color:#535353">
<td height="40" >&nbsp;2</td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="312527acdb3f4550b603220ddc058780" name="2">CTR20263409</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="312527acdb3f4550b603220ddc058780" name="2">进行中&nbsp;尚未招募</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="312527acdb3f4550b603220ddc058780" name="2">来曲唑片</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="312527acdb3f4550b603220ddc058780" name="2">早期乳腺癌</a></td>
<td><a href="javascript:void(0)" onclick="getDetail(this.id)" id="312527acdb3f4550b603220ddc058780" name="2">来曲唑生物等效性试验</a></td>
</tr>
</table>
</div>
<div class="pull-right pageInfo">
 跳转到 <input type="text"> 页
 当前第 <i>1</i> 页，共 <i>42</i> 页，共 <i>821</i> 条记录
</div>
"""

VACIO = """
<table class="searchTable">
<tr class="Tab_title"><th>序号</th><th>登记号</th></tr>
<tr style="color:#535353" ><td height="30" colspan="6">暂无数据...</td></tr>
</table>
<div class="pull-right pageInfo">
 当前第 <i>0</i> 页，共 <i>0</i> 页，共 <i>0</i> 条记录
</div>
"""

FICHA_MAL = """
<html><body>
<div>首页 &gt; 试验公示和查询 &gt; 公示列表 &gt;详细信息 获取信息失败！</div>
</body></html>
"""

FICHA_BUENA = """
<html><body>
<tr><th width="15%">登记号</th><td width="35%">CTR20263478</td></tr>
<tr><th width="15%">试验通俗题目</th><td colspan="3">Alpelisib继续用药研究</td></tr>
<div>主要研究者信息</div>
<table class="searchDetailTable">
<tr><th width="10%">姓名</th><td width="18%">胡夕春</td></tr>
<tr><th>电话</th><td>021-64175590</td><th>Email</th><td>contacto@example.com</td></tr>
</table>
<table class="searchDetailTable">
<tr><th width="15%">入选标准</th><td colspan="3">
<table class="subSearch">
<tr><td width="10%">1</td><td width="90%" style="text-align: left;">firma consentimiento informado</td></tr>
<tr><td width="10%">2</td><td width="90%" style="text-align: left;">se beneficia del tratamiento previo</td></tr>
</table>
</td></tr>
<tr><th width="15%">排除标准</th><td colspan="3">
<table class="subSearch">
<tr><td width="10%">1</td><td width="90%" style="text-align: left;">toxicidad no resuelta</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""

# 1. Listado: total/páginas/página actual y las dos filas, con el id de 32 hex (no el CTR).
r = cde_fetch.parsear_lista(LISTADO)
ok("total = 821", r["total"] == 821, f"-> {r['total']}")
ok("42 páginas", r["paginas"] == 42, f"-> {r['paginas']}")
ok("página actual = 1", r["pagina_actual"] == 1)
ok("2 registros parseados", len(r["registros"]) == 2, f"-> {len(r['registros'])}")
ok("el id es el hash de 32 hex, no el CTR",
   r["registros"][0]["id"] == "cef5d26806204eca8fc5d04a96a30c2f")
ok("el registro trae el código CTR en su propio campo",
   r["registros"][0]["reg_no"] == "CTR20263478")
ok("el fármaco se lee bien", r["registros"][1]["farmaco"] == "来曲唑片")
ok("sin aviso cuando hay resultados", r["aviso"] == "")

# 2. Cero resultados: no se confunde con un error, y AVISA que 0 no prueba ausencia.
r0 = cde_fetch.parsear_lista(VACIO)
ok("0 resultados detectados", r0["total"] == 0)
ok("0 resultados no rompe el parser", r0["registros"] == [])
ok("cero resultados trae el aviso obligatorio",
   "NO prueba" in r0["aviso"], f"-> {r0['aviso']!r}")

# 3. Ficha pedida con el código CTR en vez del hash: falla limpio, con motivo.
fm = cde_fetch.parsear_ficha(FICHA_MAL, "CTR20263054")
ok("ficha con id equivocado -> ok=False", fm["ok"] is False)
ok("el motivo dice que el id no es el hash", "hash" in fm["motivo"])

# 4. Ficha buena: todos los campos que pedía el plan.
fb = cde_fetch.parsear_ficha(FICHA_BUENA, "cef5d26806204eca8fc5d04a96a30c2f")
ok("ficha buena -> ok=True", fb["ok"] is True)
ok("registro correcto", fb["reg_no"] == "CTR20263478")
ok("investigador principal", fb["investigador_principal"] == "胡夕春")
ok("teléfono", fb["telefono"] == "021-64175590")
ok("email", fb["email"] == "contacto@example.com")
ok("2 criterios de inclusión", len(fb["criterios_inclusion"]) == 2,
   f"-> {fb['criterios_inclusion']}")
ok("1 criterio de exclusión", len(fb["criterios_exclusion"]) == 1)

# 5. CORRECCIÓN a la deuda: SesionCDE.buscar() manda los 18 campos completos, sin depender de
# cuántos rellenó quien llama. Se sustituye el POST real por un doble que graba el form enviado.
capturado = {}


class _SesionFalsa(cde_fetch.SesionCDE):
    def _post(self, path, form, referer):  # noqa: D401 - doble de red
        capturado["form"] = form
        return LISTADO

    def _abrir(self):
        pass


s = _SesionFalsa()
s.buscar(indication="乳腺癌")
ok("el form manda los 18 campos, aunque sólo se pidió 'indication'",
   set(capturado["form"].keys()) == set(cde_fetch.CAMPOS),
   f"-> faltan {set(cde_fetch.CAMPOS) - set(capturado['form'].keys())}")
ok("el campo pedido llega con su valor", capturado["form"]["indication"] == "乳腺癌")
ok("los campos no pedidos van vacíos, no ausentes", capturado["form"]["drugs_name"] == "")

# 6. Mutante: si `parsear_lista` dejara de exigir el id de 32 hex (p.ej. aceptando cualquier
# `id="..."`), colaría basura. Comprobamos que SIN el atributo id la fila no cuenta como
# registro (canario: rompe el código a propósito y confirma que el test lo detecta).
SIN_ID = LISTADO.replace('id="cef5d26806204eca8fc5d04a96a30c2f"', 'data-x="cef5d26806204eca8fc5d04a96a30c2f"')
r_sin_id = cde_fetch.parsear_lista(SIN_ID)
ok("una fila sin id de 32 hex no se cuela como registro (mutante)",
   len(r_sin_id["registros"]) == 1, f"-> {len(r_sin_id['registros'])}")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_cde_fetch: %d/%d OK" % (len(PASADOS), len(PASADOS)))
sys.exit(1 if FALLOS else 0)
