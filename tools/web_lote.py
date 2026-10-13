#!/usr/bin/env python3
"""web_lote.py — el lote de la web: N cambios preparados, UNA orden de {{TITULAR}}.

POR QUÉ EXISTE (13-oct-2026, plan «guardián de la web», aprobado por ella). Su queja: *«no quiero
ser yo el cuello de botella»*. La firma no era el problema; lo era firmar de uno en uno, con un
«fusiona» por PR y el SHA delante cada vez. Aquí ve una lista, contesta una vez y salen esos.

CÓMO SE ATA A LO QUE ELLA LEYÓ. No hay mecanismo nuevo de permiso, y por eso no toca ningún hook:

  1. `render` valida los PRs y escribe el MANIFIESTO, que va en el mensaje que ella lee: número de
     PR, commit, preview y peldaño de cada uno. Es de ese mensaje —lo escribe el harness en el
     transcript— de donde `permiso_envio` saca qué PRs y qué commits tenía delante.
  2. Su «fusiona» abre el permiso de siempre. `fusionar` llama a `web_publicar.fusionar` por cada
     PR con `atar_a_lo_visto`: PR y commit tienen que estar en lo que vio, y GitHub rechaza la
     fusión si la cabeza ya no es esa. Lo único distinto es que una orden vale para varios usos.
  3. Si nombra PRs («fusiona #268 y #270») salen solo esos. Los de peldaño 3 salen ÚNICAMENTE si
     los nombra: un «fusiona» a secas no arrastra un cambio de hero ni un dato clínico nuevo.

QUÉ NO ENTRA EN UN LOTE
  · un PR con el CI sin terminar o en rojo. El permiso dura 10 minutos y el CI de la web tarda
    2–3,5: se espera ANTES de enseñárselo, no después de su orden;
  · dos PRs que tocan el mismo fichero: al fusionar uno, el otro habría que rebasarlo, cambiaría
    de commit y ya no sería el que firmó. Van en un solo PR o en lotes sucesivos;
  · más de 3 cambios que le pidan leer algo (peldaños 2 y 3) o más de 5 mecánicos.

EL PELDAÑO NO SE BAJA A MANO. Quien prepara el lote lo declara, pero si el diff toca hero/H1 o
borra copy es 3, y si cambia copy existente es al menos 2, diga lo que diga la lista.

Uso:
  python3 tools/web_lote.py render lote.json     # valida y escribe el manifiesto para el mensaje
  python3 tools/web_lote.py fusionar lote.json   # tras su orden: fusiona y dice qué salió

`lote.json`: [{"pr": 268, "peldano": 1, "que": "Ancla «Lo último» siempre viva"}, …]
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import web_publicar as WP  # noqa: E402

TOPE_ATENCION = 3      # peldaños 2 y 3: lo que le pide leer (el tope que puso Vega)
TOPE_MECANICOS = 5     # peldaño 1
NOMBRES = {1: "mecánico", 2: "míralo", 3: "solo si lo nombras"}


RE_PREVIEW = re.compile(r"https://deploy-preview-\d+--[\w-]+\.netlify\.app")


def preview_en(texto):
    """La URL de la preview de Netlify dentro de un comentario del PR, limpia, o "".

    Netlify la escribe como enlace markdown (`[Preview](https://deploy-preview-268--…)`). Partir
    el comentario por espacios dejaba «Preview](https://…» en el manifiesto: se vio el 14-oct-26
    al probarlo contra el primer PR real (#268)."""
    m = RE_PREVIEW.search(texto or "")
    return m.group(0) if m else ""


def leer_pr(numero):
    """Todo lo que el lote necesita saber de un PR, o None. Aquí, y solo aquí, se habla con GitHub."""
    r = subprocess.run(["gh", "pr", "view", str(numero), "--json",
                        "number,state,headRefOid,url,title,files,statusCheckRollup,comments"],
                       cwd=WP.WEB, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return None
    cabeza = d.get("headRefOid") or ""
    WP.trae(WP.WEB, cabeza)
    frena, avisa = WP.revisa_copy(WP.WEB, "origin/" + WP.BASE, cabeza)
    preview = ""
    for c in d.get("comments") or []:
        preview = preview_en(c.get("body")) or preview
    return {"number": d.get("number"), "state": d.get("state"), "headRefOid": cabeza,
            "url": d.get("url"), "title": d.get("title"),
            "ci": WP.ci_estado(d.get("statusCheckRollup")),
            "files": [f.get("path") for f in d.get("files") or []],
            "frena": frena, "avisa": avisa, "preview": preview}


def validar(pedido, _lee=leer_pr):
    """(items, pegas). Con una sola pega no hay lote: se arregla antes de enseñárselo."""
    items, pegas, dueno = [], [], {}
    for p in pedido:
        n = p.get("pr")
        pr = _lee(n)
        if not pr:
            pegas.append("#%s: no encuentro el PR" % n)
            continue
        if (pr.get("state") or "").upper() != "OPEN":
            pegas.append("#%s: no está abierto (%s)" % (n, pr.get("state")))
            continue
        if pr.get("ci") != "verde":
            pegas.append("#%s: el CI está «%s»; al lote solo entra lo que ya está en verde"
                         % (n, pr.get("ci")))
        if len(pr.get("headRefOid") or "") != 40:
            pegas.append("#%s: no sé cuál es su commit" % n)
        peldano = int(p.get("peldano") or 1)
        if pr.get("frena"):
            peldano = 3
        elif pr.get("avisa"):
            peldano = max(peldano, 2)
        for f in pr.get("files") or []:
            if f in dueno:
                pegas.append("%s lo tocan #%s y #%s: van en un solo PR o en lotes sucesivos"
                             % (f, dueno[f], n))
            else:
                dueno[f] = n
        items.append({"pr": n, "sha": pr.get("headRefOid") or "", "peldano": peldano,
                      "que": p.get("que") or pr.get("title") or "", "url": pr.get("url") or "",
                      "preview": pr.get("preview") or "", "copy": (pr.get("frena") or []) +
                      (pr.get("avisa") or [])})
    atencion = sum(1 for i in items if i["peldano"] >= 2)
    if atencion > TOPE_ATENCION:
        pegas.append("%d cambios piden su atención y el tope es %d: lo demás, al lote siguiente"
                     % (atencion, TOPE_ATENCION))
    if len(items) - atencion > TOPE_MECANICOS:
        pegas.append("%d cambios mecánicos y el tope es %d" % (len(items) - atencion, TOPE_MECANICOS))
    return sorted(items, key=lambda i: i["peldano"]), pegas


def render(items):
    """El manifiesto, tal cual va en el mensaje. Cada PR con «#N» y su commit: es lo que el
    permiso busca en lo que ella tenía delante."""
    lineas = ["**Lote de la web: %d cambios, todos con el CI en verde.**" % len(items), ""]
    for peldano in (1, 2, 3):
        grupo = [i for i in items if i["peldano"] == peldano]
        if not grupo:
            continue
        lineas.append("**Peldaño %d, %s**" % (peldano, NOMBRES[peldano]))
        lineas.append("")
        lineas.append("| PR | Qué cambia | Commit | Preview |")
        lineas.append("|---|---|---|---|")
        for i in grupo:
            lineas.append("| #%d | %s | `%s` | %s |" % (
                i["pr"], i["que"].replace("|", "/"), i["sha"][:12],
                i["preview"] or "sin preview: mírala en el PR " + i["url"]))
        for i in grupo:
            for c in i["copy"][:3]:
                lineas.append("- #%d toca copy publicado: %s" % (i["pr"], c))
        lineas.append("")
    genericos = ", ".join("#%d" % i["pr"] for i in items if i["peldano"] < 3)
    tres = " y ".join("#%d" % i["pr"] for i in items if i["peldano"] == 3)
    if genericos:
        lineas.append("Con «fusiona» salen %s. Para sacar solo algunos, nómbralos con almohadilla: "
                      "«fusiona #N y #M»." % genericos)
    if tres:
        lineas.append("Lo de peldaño 3 hay que nombrarlo: «fusiona %s»." % tres)
    return "\n".join(lineas)


def fusionar(items, _fusiona=WP.fusionar, _cierra=WP.gasta_lo_que_quede):
    """[{pr, merged, motivo, despliegue}]. Fusiona en orden de peldaño. Si el permiso se acaba o
    nunca existió, no sigue probando: los que faltan quedan contados como no publicados."""
    out, sin_permiso = [], None
    usos = max(len(items), 1)      # tantos usos como PRs pueden salir, ni uno más
    for i in items:
        if sin_permiso:
            out.append({"pr": i["pr"], "merged": False, "motivo": sin_permiso})
            continue
        r = _fusiona(i["pr"], por="web_lote", que="fusionar", atar_a_lo_visto=True,
                     exige_nombrado=i["peldano"] == 3, usos_max=usos, tope_ci_s=0)
        out.append({"pr": i["pr"], "merged": bool(r.get("merged")),
                    "motivo": r.get("motivo_no_merge") or r.get("merge_error") or ""})
        if not r.get("merged") and r.get("pedido_por_titular") is False:
            sin_permiso = out[-1]["motivo"] or "sin permiso"
    # Si algún PR no salió (un peldaño 3 sin nombrar, un CI que se torció), al permiso le quedan
    # usos. No se dejan vivos: su orden era para ESTE lote, no para lo que venga en diez minutos.
    _cierra("web_lote")
    return out


def _carga(ruta):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    if len(argv) != 2 or argv[0] not in ("render", "fusionar"):
        print(__doc__)
        return 2
    items, pegas = validar(_carga(argv[1]))
    if pegas:
        print("⛔ así no hay lote (%d pega%s):" % (len(pegas), "" if len(pegas) == 1 else "s"))
        for p in pegas:
            print("  · %s" % p)
        return 1
    if argv[0] == "render":
        print(render(items))
        return 0
    res = fusionar(items)
    for r in res:
        print("  %s #%d%s" % ("✅" if r["merged"] else "🟡", r["pr"],
                             "" if r["merged"] else " NO se ha publicado: " + r["motivo"]))
    if any(r["merged"] for r in res):
        print("Fusionado no es desplegado: comprueba producción con "
              "`python3 tools/web_publicar.py verificar <url> \"<texto>\"`.")
    return 0 if all(r["merged"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
