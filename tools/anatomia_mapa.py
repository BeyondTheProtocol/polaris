#!/usr/bin/env python3
"""El Mapa — la Anatomía dibujada como constelación, y exportable a GIF.

`anatomia.py` responde en LISTA: qué hay y qué late. Esto responde lo mismo
de un vistazo: un solo dibujo donde se ve el centro, los anillos, el muro
alrededor y la cadena hacia NED debajo. Para ENTENDER el sistema y para
enseñarlo, no para vigilarlo (eso es El Observatorio).

Los números NO se escriben a mano: salen de `anatomia.inventario()` y
`anatomia.pulso()`, y la cadena de abajo de `cumbre.load()`. Si el sistema
cambia, el dibujo cambia solo.

Manda el design system (07 · Marca/Design-System-Consolidado). De ahí:
  · dos fondos y solo dos — berenjena #2d1b3d o crema #faf6f0, nunca negro;
  · violeta firma #a44db2 (sobre berenjena, el claro #c77dd2). El #a855b5 que
    aún usa anatomia.py está marcado como STALE en el propio DS (D1);
  · coral #ff6b47 es ACCIÓN y va solo sobre berenjena: aquí lo lleva un único
    nodo, el eslabón donde estamos. Un foco coral, nunca repartido;
  · la estrella de 4 puntas es el motivo de la marca y tiene path canónico:
    se instancia, no se redibuja (ESTRELLA, copiada de Constellation.vue).

Solo lectura. Sin LLM (~0 tokens). Sin dependencias fuera de la stdlib para
dibujar; el GIF sí usa Chrome (headless) y ffmpeg, que ya están en la casa.

Dos caras, igual que la Anatomía:
  privada  — nombres reales de comités y cajas; para ella.
  publica  — los eslabones van por LISTA BLANCA (ver _cadena_svg). Es la que
             se puede compartir.

Todo el movimiento cabe en un ciclo de 4s y cada anillo gira exactamente el
paso entre dos de sus nodos, así que el bucle cierra sin salto por muy lento
que vaya. Para capturar un fotograma se congela con `--t`: cada elemento
lleva su desfase propio en `--o` y el delay real es `-(--o + --t)`, de modo
que el mismo CSS sirve vivo y congelado.

Uso:
  python3 tools/anatomia_mapa.py render [--cara publica] [--tema claro]
  python3 tools/anatomia_mapa.py gif [-o mapa.gif] [--fps 12] [--ancho 1000]
  python3 tools/anatomia_mapa.py png [-o mapa.png] [--t 1.2]
  python3 tools/anatomia_mapa.py serve [--puerto 8792]
"""

import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

import anatomia  # noqa: E402  (mismo directorio, es la fuente de los datos)

try:
    import cumbre  # noqa: E402
except Exception:  # pragma: no cover - la brújula puede no estar en un worktree
    cumbre = None

CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium",
          "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")

# ─── lienzo ──────────────────────────────────────────────────────────────────

W, H = 1400, 1320           # viewBox
CX, CY = 700, 622           # centro de la constelación
R_NUCLEO = 60
R_COMITES = 172
R_TOOLS = 246
R_RUTINAS = 312
R_MURO = 378
CICLO = 4.0                 # segundos: todo el movimiento cabe aquí

# El motivo de la marca. Path canónico de Constellation.vue (viewBox 0 0 20 20,
# centro 10,10). Se instancia con _estrella(); no se dibuja a mano.
ESTRELLA = ("M10 1.6 C10.8 5,11.4 6.2,12.6 7.4 C14 8.8,16.4 9.4,18.4 10 "
            "C16.2 10.8,14.2 11.4,12.8 12.7 C11.5 13.9,10.9 15.7,10 18.4 "
            "C9.3 15.9,8.5 14.2,7.2 12.9 C5.8 11.6,3.4 10.7,1.6 10 "
            "C3.8 9.1,5.8 8.4,7.2 7.1 C8.4 6,9.2 4.2,10 1.6 Z")

# Etiquetas cortas y seguras para la cadena de la brújula. El título real de
# un nodo lleva hospital y médico dentro; aquí no entra ninguno de los dos,
# ni en la cara privada, para que el dibujo se pueda enseñar tal cual.
ESLABON = {
    "biopsia": "biopsia",
    "screening-{{CENTRO}}": "screening",
    "esperando-resultados": "resultados",
    "dianas": "dianas",
    "puerta-ensayo": "puerta de ensayo",
    "fabricante": "fabricante",
    "acceso": "acceso",
    "vacuna": "vacuna",
}


# Las tipografías del design system. No se instalan en el sistema ni se bajan
# de la red: se copian del build de la web (las mismas que ella ya publica) y se
# incrustan en base64, para que el HTML siga siendo autocontenido. Si faltan, el
# dibujo cae a los fallbacks declarados en el DS (Georgia / ui-monospace) y lo
# dice por stderr en vez de mentir con una tipografía que no es.
FUENTES = (
    ("Fraunces", 600, "Fraunces-600-normal.woff"),
    ("Fraunces", 400, "Fraunces-400-normal.woff"),
    ("JetBrains Mono", 400, "JetBrains_Mono-400-normal.woff"),
)


def dir_fuentes():
    """Dónde viven los .woff. El worktree no los tiene: son binarios y están
    en .gitignore, así que se busca también en casa base.

    Vale la carpeta que CONTIENE alguna fuente, no la que existe. Un worktree
    trae `tools/fonts/` vacía (git crea el árbol, los .woff se quedan fuera):
    aceptarla por existir cortaba la búsqueda en el primer candidato y devolvía
    un CSS sin @font-face — el mapa salía en Georgia y nadie se enteraba."""
    for d in (os.environ.get("BTP_FONTS"),
              os.path.join(AQUI, "fonts"),
              os.path.expanduser("~/claudecode/tools/fonts")):
        if d and os.path.isdir(d) and any(
                os.path.exists(os.path.join(d, f)) for _, _, f in FUENTES):
            return d
    return None


def fuentes_css(avisar=False):
    """Los @font-face en base64. Cadena vacía = tirar de fallback."""
    d = dir_fuentes()
    faltan, out = [], []
    for fam, peso, fich in FUENTES:
        p = os.path.join(d, fich) if d else None
        if not p or not os.path.exists(p):
            faltan.append(fich)
            continue
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        out.append("@font-face{font-family:'%s';font-style:normal;"
                   "font-weight:%d;font-display:block;"
                   "src:url(data:font/woff;base64,%s) format('woff')}"
                   % (fam, peso, b64))
    if faltan and avisar:
        sys.stderr.write("⚠️  sin %s → el dibujo usa los fallbacks del DS "
                         "(Georgia / mono del sistema)\n" % ", ".join(faltan))
    return "".join(out)


def _e(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _num(n):
    return "{:,}".format(int(n)).replace(",", ".")


def _pl(n, sing, plur=None):
    return "%s %s" % (_num(n), sing if n == 1 else (plur or sing + "s"))


def _pol(r, grados):
    """Punto polar. 0° arriba, y crece en sentido horario (como un reloj)."""
    a = math.radians(grados - 90)
    return CX + r * math.cos(a), CY + r * math.sin(a)


def _f(x):
    return ("%.2f" % x).rstrip("0").rstrip(".")


def _estrella(cx, cy, tam, clase="", extra=""):
    """Instancia del motivo. `tam` es el diámetro en px del destello."""
    k = tam / 20.0
    return ('<g transform="translate(%s %s) scale(%s) translate(-10 -10)" '
            'class="%s"%s><path d="%s"/></g>'
            % (_f(cx), _f(cy), _f(k), clase, extra, ESTRELLA))


# ─── datos ───────────────────────────────────────────────────────────────────

def cadena():
    """La cadena hacia NED, en eslabones cortos: (etiqueta, estado, es_foco)."""
    if cumbre is None:
        return []
    try:
        d = cumbre.load()
    except Exception:
        return []
    aqui = d.get("aqui_estamos")
    fuera = []
    for nodo in d.get("salientes", []):
        nid = nodo.get("id", "")
        fuera.append({
            # `conocida` = la etiqueta sale de ESLABON y por tanto la escribí yo.
            # Si no, es texto libre de la brújula y no se enseña en público.
            "etiqueta": ESLABON.get(nid) or nid.replace("-", " "),
            "conocida": nid in ESLABON,
            "estado": nodo.get("estado", "pendiente"),
            "foco": nid == aqui,
        })
    return fuera


def datos(cara="privada"):
    return anatomia.inventario(), anatomia.pulso(), cadena()


# ─── piezas del dibujo ───────────────────────────────────────────────────────

def _cielo():
    """Campo sutil de estrellas fuera del muro: el motivo de la marca.

    Posiciones deterministas (ángulo áureo), no aleatorias, para que dos
    ejecuciones den el mismo dibujo. Se descartan las que caerían sobre las
    columnas de texto: el motivo nunca pisa el mensaje (DS §5).
    """
    out = []
    for i in range(78):
        ang = (i * 137.508) % 360
        r = R_MURO + 34 + (i * 37) % 210
        x, y = _pol(r, ang)
        if not (20 < x < W - 20 and 130 < y < H - 130):
            continue
        if (x < 366 or x > 1034) and 210 < y < 620:      # leyenda y panel
            continue
        if y > 1030:                                      # la cadena de abajo
            continue
        tam = 5 + (i * 7) % 7
        out.append(_estrella(x, y, tam, "cielo",
                             ' style="--o:%ss"' % _f((i % 11) * 0.36)))
    return "".join(out)


def _anillo_comites(inv, pul):
    """Un punto por comité. Los que han corrido hoy van encendidos.

    El anillo gira justo el paso entre dos comités, así que a los 4s el dibujo
    es idéntico al del principio y el bucle no salta.
    """
    nombres = [c["nombre"] for c in inv["comites"]]
    activos = set(pul.get("con_traza", []))
    despiertos = set(pul.get("despiertos", []))
    paso = 360.0 / max(1, len(nombres))
    out = []
    for i, nom in enumerate(nombres):
        x, y = _pol(R_COMITES, i * paso)
        onda = (i / float(len(nombres))) * CICLO      # la luz recorre el anillo
        # Todos son estrellas: es la constelación de la marca, no un gráfico de
        # puntos. Los que han trabajado hoy brillan; el resto está ahí, apagado.
        if nom in despiertos:
            cls, tam = "cmt vivo", 21
        elif nom in activos:
            cls, tam = "cmt hoy", 18
        else:
            cls, tam = "cmt", 13
        out.append(_estrella(x, y, tam, cls, ' style="--o:%ss"' % _f(onda)))
    return ('<g class="gira-h" style="--paso:%sdeg">%s</g>'
            % (_f(paso), "".join(out)))


def _anillo_tools(inv):
    """Una rayita por herramienta, agrupadas en arcos por familia.

    No gira (los arcos no son regulares, no cerraría el bucle): en su lugar
    una onda de luz da la vuelta entera en un ciclo.
    """
    fams = inv["herramientas"]["familias"]
    total = sum(len(v) for v in fams.values()) or 1
    hueco = 3.2                                    # grados de aire entre familias
    libre = 360.0 - hueco * len(fams)
    paso = libre / total
    ang = 0.0
    out = []
    for lista in fams.values():
        for j in range(len(lista)):
            a = ang + (j + 0.5) * paso
            x1, y1 = _pol(R_TOOLS - 10, a)
            x2, y2 = _pol(R_TOOLS + 10, a)
            out.append('<line class="tk" x1="%s" y1="%s" x2="%s" y2="%s" '
                       'style="--o:%ss"/>'
                       % (_f(x1), _f(y1), _f(x2), _f(y2),
                          _f((a / 360.0) * CICLO)))
        ang += len(lista) * paso + hueco
    return "".join(out)


def _anillo_rutinas(inv):
    """Un punto por rutina 24/7: relleno si está cargada, hueco si no.

    Gira al revés que los comités, un paso por ciclo. El contragiro es lo que
    da sensación de maquinaria y sigue cerrando el bucle.
    """
    rut = inv["rutinas"]
    paso = 360.0 / max(1, len(rut))
    out = []
    for i, r in enumerate(rut):
        x, y = _pol(R_RUTINAS, i * paso)
        onda = (i / float(len(rut))) * CICLO
        if r.get("cargado"):
            out.append('<circle class="rt on" cx="%s" cy="%s" r="4.4" '
                       'style="--o:%ss"/>' % (_f(x), _f(y), _f(onda)))
        else:
            out.append('<circle class="rt off" cx="%s" cy="%s" r="4"/>'
                       % (_f(x), _f(y)))
    return ('<g class="gira-a" style="--paso:%sdeg">%s</g>'
            % (_f(paso), "".join(out)))


def _candado(x, y, o=None):
    """Un candado, no una cruz: se tiene que leer como «esto cierra»."""
    aro = ('<circle class="gd" cx="%s" cy="%s" r="11"%s/>'
           % (_f(x), _f(y),
              '' if o is None else ' style="--o:%ss"' % _f(o)))
    return (aro
            + '<path class="gd-i" d="M%s %s a3.1 3.1 0 0 1 6.2 0 v2.2"/>'
              % (_f(x - 3.1), _f(y - 0.6))
            + '<rect class="gd-b" x="%s" y="%s" width="9" height="6.4" rx="1.4"/>'
              % (_f(x - 4.5), _f(y + 1.2)))


def _guardas(inv):
    """Las guardas, clavadas en el muro."""
    g = inv["guardas"]
    paso = 360.0 / max(1, len(g))
    out = []
    for i in range(len(g)):
        x, y = _pol(R_MURO, i * paso + paso / 2)
        out.append(_candado(x, y, (i % 5) * 0.8))
    return "".join(out)


def _particulas():
    """Encargos saliendo del centro. Ninguno atraviesa el muro: se apagan.

    Cada uno lleva dos puntos más pequeños detrás, con retardo, para que deje
    estela en vez de parecer una mosca.
    """
    out = []
    ejes = (18, 74, 133, 196, 251, 307, 342)
    for i, grados in enumerate(ejes):
        base = i * (CICLO / len(ejes))
        cola = []
        for k, (r, op) in enumerate(((5.5, 1), (3.6, .55), (2.4, .28))):
            cola.append('<circle class="pt" cx="%d" cy="%d" r="%s" '
                        'style="--o:%ss;--op:%s"/>'
                        % (CX, CY, _f(r), _f(base + k * 0.11), op))
        out.append('<g transform="rotate(%s %s %s)">%s</g>'
                   % (grados, CX, CY, "".join(cola)))
    return "".join(out)


def _nucleo():
    """El orquestador, con dos ondas de sonar saliendo de él."""
    return (
        '<circle class="sonar" cx="%d" cy="%d" r="%d" style="--o:0s"/>'
        '<circle class="sonar" cx="%d" cy="%d" r="%d" style="--o:2s"/>'
        '<circle class="nu-halo" cx="%d" cy="%d" r="%d"/>'
        '<circle class="nucleo" cx="%d" cy="%d" r="%d"/>'
        '<text class="nu-t" x="%d" y="%d">orquestador</text>'
        '<text class="nu-s" x="%d" y="%d">REPARTE</text>'
        % (CX, CY, R_NUCLEO, CX, CY, R_NUCLEO, CX, CY, R_NUCLEO + 16,
           CX, CY, R_NUCLEO, CX, CY - 2, CX, CY + 22)
    )


def _leyenda(inv, pul):
    """Columna izquierda: qué es cada anillo, de dentro afuera."""
    n = inv["numeros"]
    hoy = len(pul.get("con_traza", []))
    filas = [
        ("nucleo", "el orquestador", "lee lo que le dices y reparte"),
        ("cmt", _pl(n["comites"], "comité", "comités"),
         "%d con trabajo hoy" % hoy),
        ("tk", _pl(n["tools"], "herramienta"),
         "%s líneas · %d familias" % (_num(n["lineas"]),
                                      len(inv["herramientas"]["familias"]))),
        ("rt", "%d de %d rutinas 24/7" % (n["rutinas_cargadas"], n["rutinas_total"]),
         "%s hoy, sin que las llames" % _pl(pul.get("hoy_total", 0), "vuelta")),
        ("gd", "el muro · %s" % _pl(n["guardas"], "guarda"),
         "nada sale de aquí sin tu OK"),
    ]
    out = []
    y = 272
    for cls, tit, sub in filas:
        if cls == "tk":
            for k in range(3):
                out.append('<line class="tk quieta" x1="%d" y1="%d" x2="%d" '
                           'y2="%d"/>' % (60 + k * 6, y - 13, 60 + k * 6, y + 1))
        elif cls == "cmt":
            out.append(_estrella(66, y - 5, 17, "cmt hoy quieta"))
        elif cls == "gd":
            out.append('<g class="quieta">%s</g>' % _candado(66, y - 5))
        else:
            out.append('<circle class="lg %s" cx="66" cy="%d" r="7"/>'
                       % (cls, y - 5))
        out.append('<text class="lg-t" x="92" y="%d">%s</text>' % (y, _e(tit)))
        out.append('<text class="lg-s" x="92" y="%d">%s</text>' % (y + 23, _e(sub)))
        y += 76
    return "".join(out)


def _panel_derecha(inv, pul):
    """Columna derecha: lo que no es un anillo pero cuenta."""
    n = inv["numeros"]
    co = inv["cola"]
    filas = [
        ("%d de %d cerebros" % (n["cerebros_on"], n["cerebros"]),
         "los modelos que piensan"),
        ("%s conexiones" % _num(n["mcp"]), "correo, papers, genómica, navegador"),
        (_pl(n["memorias"], "memoria"), "%s ficheros de estado" % _num(n["estado"])),
        (_pl(inv["memoria"]["tests"], "test"), "la red que sujeta los cambios"),
        ("%s en la cola" % _num(co.get("pending", 0) + co.get("processing", 0)),
         "%s hechos · %s caídos" % (_num(co.get("done", 0)),
                                    _num(co.get("failed", 0)))),
    ]
    out = []
    y = 272
    for tit, sub in filas:
        out.append('<text class="rg-t" x="1334" y="%d">%s</text>' % (y, _e(tit)))
        out.append('<text class="rg-s" x="1334" y="%d">%s</text>' % (y + 23, _e(sub)))
        y += 76
    return "".join(out)


def _fuera():
    """Lo que hay al otro lado del muro: tú arriba, el mundo abajo."""
    return (
        '<text class="fu-t" x="%d" y="%d">tú</text>'
        '<text class="fu-s" x="%d" y="%d">CHAT · TELEGRAM · VOZ</text>'
        '<path class="entra" d="M%d %d L%d %d"/>'
        '<text class="fu-t" x="%d" y="%d">el mundo</text>'
        '<text class="fu-s" x="%d" y="%d">CORREO · REDES · DINERO · MÉDICOS</text>'
        '<path class="sale" d="M%d %d L%d %d"/>'
        '<text class="fu-ok" x="%d" y="%d">solo con tu firma</text>'
        % (CX, CY - R_MURO - 76, CX, CY - R_MURO - 52,
           CX, CY - R_MURO - 42, CX, CY - R_MURO - 10,
           CX, CY + R_MURO + 66, CX, CY + R_MURO + 90,
           CX, CY + R_MURO + 10, CX, CY + R_MURO + 42,
           CX + 186, CY + R_MURO + 34)
    )


def _cadena_svg(cad, cara):
    """La cadena hacia NED, debajo del todo. Es el para qué de la máquina."""
    if not cad:
        return ""
    y = 1216
    x0, x1 = 92, 1150
    paso = (x1 - x0) / max(1, len(cad) - 1)
    out = ['<text class="cd-h" x="92" y="1146">LA CADENA HACIA NED</text>']
    out.append('<line class="cd-l" x1="%d" y1="%d" x2="%d" y2="%d"/>'
               % (x0, y, x1, y))
    for i, nodo in enumerate(cad):
        x = x0 + i * paso
        # Lista blanca, no scrubber: `_lexico_publico` caza léxico vetado, NO
        # nombres de médico ni de hospital. Un eslabón que yo no haya escrito
        # es texto libre de cumbre.json, así que en público sale por su
        # categoría. Fail-closed, como las cajas en la Anatomía.
        etq = nodo["etiqueta"]
        if cara == "publica" and not nodo.get("conocida"):
            etq = "(un paso más)"
        if nodo["foco"]:
            # El único coral de toda la pieza: un foco, nunca repartido (DS §5).
            out.append('<circle class="cd-halo" cx="%s" cy="%d" r="14"/>' % (_f(x), y))
            out.append('<circle class="cd foco" cx="%s" cy="%d" r="14"/>' % (_f(x), y))
            out.append('<text class="cd-aqui" x="%s" y="%d">AQUÍ ESTAMOS</text>'
                       % (_f(x), y - 32))
        elif nodo["estado"] in ("hecho", "resuelto"):
            out.append('<circle class="cd hecho" cx="%s" cy="%d" r="10"/>' % (_f(x), y))
        else:
            out.append('<circle class="cd" cx="%s" cy="%d" r="9"/>' % (_f(x), y))
        out.append('<text class="cd-t%s" x="%s" y="%d">%s</text>'
                   % (" on" if nodo["foco"] else "", _f(x), y + 38, _e(etq)))
    ex = x1 + 108
    out.append('<line class="cd-l" x1="%d" y1="%d" x2="%d" y2="%d"/>'
               % (x1, y, ex - 30, y))
    out.append(_estrella(ex, y, 46, "ned"))
    out.append('<text class="cd-ned" x="%s" y="%d">NED</text>' % (ex, y + 46))
    return "".join(out)


# ─── el SVG entero ───────────────────────────────────────────────────────────

def svg(inv, pul, cad, cara="privada"):
    n = inv["numeros"]
    hoy = pul.get("hoy_total", 0)
    fallos = pul.get("hoy_fallos", 0)

    cabecera = (
        '<text class="eyebrow" x="48" y="62">EL MAPA · ANATOMÍA DEL SISTEMA</text>'
        '<text class="h1" x="46" y="122">Polaris</text>'
        '<text class="h2" x="48" y="158">cómo está por dentro, ahora mismo · '
        '%s</text>' % _e(anatomia._fecha(inv["fecha"]))
    )
    # El punto va DETRÁS del texto: el texto es de ancho variable y anclado al
    # final, así que ponerlo delante lo pisaba.
    estado = ('<text class="pl-t" x="1330" y="66">%s · %s %s HOY%s</text>'
              '<circle class="pl-dot" cx="1348" cy="60" r="6"/>'
              % (_e(pul.get("ahora", "")), _num(hoy),
                 "VUELTA" if hoy == 1 else "VUELTAS",
                 (" · %s CAÍDAS" % _num(fallos)) if fallos else ""))

    muro = ('<circle class="muro" cx="%d" cy="%d" r="%d"/>'
            '<circle class="muro-i" cx="%d" cy="%d" r="%d"/>'
            % (CX, CY, R_MURO, CX, CY, R_MURO - 8))

    # Tres sectores encadenados con opacidad decreciente: SVG no tiene gradiente
    # cónico y un sector plano se ve como un trozo de tarta pegado.
    rr = R_RUTINAS + 28
    hojas = []
    for k, (a0, a1) in enumerate(((0, 16), (16, 32), (32, 50))):
        x0, y0 = _pol(rr, a0)
        x1, y1 = _pol(rr, a1)
        hojas.append('<path class="radar-p r%d" d="M%d %d L%s %s A %d %d 0 0 1 '
                     '%s %s Z"/>' % (k, CX, CY, _f(x0), _f(y0), rr, rr,
                                     _f(x1), _f(y1)))
    barrido = '<g class="radar">%s</g>' % "".join(hojas)

    return (
        '<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-labelledby="mapa-t"><title id="mapa-t">Polaris: '
        '%s, %s, %d de %d rutinas encendidas, todo dentro del muro</title>'
        '%s<rect class="fondo" width="%d" height="%d"/>'
        '<g aria-hidden="true">%s</g>'
        '%s%s'
        '<g class="const">%s%s%s%s%s%s%s%s</g>'
        '%s%s%s'
        '</svg>'
        % (W, H,
           _pl(n["comites"], "comité", "comités"), _pl(n["tools"], "herramienta"),
           n["rutinas_cargadas"], n["rutinas_total"],
           DEFS, W, H, _cielo(),
           cabecera, estado,
           barrido, muro, _guardas(inv), _anillo_rutinas(inv), _anillo_tools(inv),
           _anillo_comites(inv, pul), _particulas(), _nucleo(),
           _leyenda(inv, pul), _panel_derecha(inv, pul),
           _fuera() + _cadena_svg(cad, cara))
    )


DEFS = (
    '<defs>'
    '<radialGradient id="velo" cx="50%" cy="50%" r="50%">'
    '<stop offset="0%" stop-color="var(--velo)" stop-opacity=".85"/>'
    '<stop offset="100%" stop-color="var(--velo)" stop-opacity="0"/>'
    '</radialGradient>'
    '<filter id="glow" x="-80%" y="-80%" width="260%" height="260%">'
    '<feGaussianBlur stdDeviation="5" result="b"/>'
    '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    '</filter>'
    '<filter id="glow-x" x="-120%" y="-120%" width="340%" height="340%">'
    '<feGaussianBlur stdDeviation="11" result="b"/>'
    '<feMerge><feMergeNode in="b"/><feMergeNode in="b"/>'
    '<feMergeNode in="SourceGraphic"/></feMerge>'
    '</filter>'
    '<marker id="pa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
    'markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" '
    'fill="var(--mi)"/></marker>'
    '<marker id="pn" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
    'markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z" '
    'fill="var(--tx2)"/></marker>'
    '</defs>'
)

# Los tokens salen del design system consolidado. El violeta firma es #a44db2:
# el #a855b5 que arrastra anatomia.py está marcado como STALE en el propio DS.
TEMAS = {
    "oscuro": """
--bg:#2d1b3d;--fondo2:#241531;--velo:#2d1b3d;
--tx:#faf6f0;--tx2:#b9a6c4;--mi:#c77dd2;--mi2:#a44db2;--cta:#ff6b47;
--linea:#4a3559;--tenue:#3d2a4e""",
    "claro": """
--bg:#faf6f0;--fondo2:#f5efe6;--velo:#faf6f0;
--tx:#2d1b3d;--tx2:#3a3340;--mi:#a44db2;--mi2:#a44db2;--cta:#a44db2;
--linea:#dcd2c6;--tenue:#e8ded1""",
}

CSS = """
:root{__TEMA__;--t:0s}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg)}
svg{display:block;width:100%;height:auto;
font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif}
text{fill:var(--tx)}
.fondo{fill:var(--bg)}

/* Fraunces es la display de la marca; sin red, su fallback declarado es Georgia */
.h1{font-family:Fraunces,Georgia,'Times New Roman',serif;font-size:62px;
font-weight:600;letter-spacing:-.02em}
.h2{font-size:19px;fill:var(--tx2)}
.eyebrow,.pl-t,.cd-h,.cd-aqui,.fu-s,.nu-s{
font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:.14em;font-size:13px;fill:var(--tx2)}
.pl-t{font-size:13px;text-anchor:end}
.pl-dot{fill:var(--mi);filter:url(#glow);
animation:lat 2s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes lat{0%,100%{opacity:1}50%{opacity:.2}}

.cielo{fill:var(--mi2);opacity:.34;
animation:titila 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes titila{0%,100%{opacity:.14}50%{opacity:.5}}

.muro{fill:none;stroke:var(--linea);stroke-width:2.5;stroke-dasharray:9 8;
animation:ronda 4s linear infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes ronda{to{stroke-dashoffset:-68}}   /* 4 patrones de 17: cierra */
.muro-i{fill:none;stroke:var(--linea);stroke-width:1;opacity:.45}
.gd{fill:var(--bg);stroke:var(--tx2);stroke-width:1.6;
animation:guarda 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes guarda{0%,100%{stroke:var(--tx2)}50%{stroke:var(--mi)}}
.gd-i{fill:none;stroke:var(--tx2);stroke-width:1.5;stroke-linecap:round}
.gd-b{fill:var(--tx2)}

.radar{transform-origin:__CX__px __CY__px;
animation:gir 4s linear infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
.radar-p{fill:var(--mi)}
.radar-p.r0{opacity:.055}.radar-p.r1{opacity:.032}.radar-p.r2{opacity:.015}
@keyframes gir{to{transform:rotate(360deg)}}

/* cada anillo gira UN paso por ciclo: el bucle cierra aunque parezca continuo */
.gira-h,.gira-a{transform-origin:__CX__px __CY__px;
animation:pasoh 4s linear infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
.gira-a{animation-name:pasoa}
@keyframes pasoh{to{transform:rotate(var(--paso))}}
@keyframes pasoa{to{transform:rotate(calc(-1 * var(--paso)))}}

.tk{stroke:var(--tx2);stroke-width:2.4;stroke-linecap:round;opacity:.45;
animation:onda-tk 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes onda-tk{0%,100%{opacity:.3}8%{opacity:.95;stroke:var(--mi)}30%{opacity:.3}}
.tk.quieta{animation:none;opacity:.6}

.rt{fill:none}
.rt.on{fill:var(--mi);filter:url(#glow);
animation:res 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
.rt.off{stroke:var(--linea);stroke-width:1.6}
@keyframes res{0%,100%{opacity:.4}10%{opacity:1}40%{opacity:.4}}

.cmt{fill:var(--tx2);opacity:.52}
.cmt.hoy{fill:var(--mi2);stroke:none;filter:url(#glow);
animation:brilla 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
.cmt.vivo{fill:var(--mi);stroke:none;filter:url(#glow-x);
animation:brilla 2s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
.cmt.quieta,.quieta .gd{animation:none;filter:none;opacity:1}
@keyframes brilla{0%,100%{opacity:.55}12%{opacity:1}45%{opacity:.6}}

.pt{fill:var(--mi);filter:url(#glow);
animation:via 4s cubic-bezier(.35,0,.65,1) infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes via{
 0%{transform:translateY(0);opacity:0}
 10%{opacity:var(--op,1)}
 70%{opacity:calc(var(--op,1) * .8)}
 88%{transform:translateY(-__ALCANCE__px);opacity:0}
 100%{transform:translateY(-__ALCANCE__px);opacity:0}}

.sonar{fill:none;stroke:var(--mi);stroke-width:2;
animation:sonar 4s ease-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes sonar{0%{r:__RNUC__px;opacity:.55}100%{r:__RSON__px;opacity:0}}
.nu-halo{fill:var(--mi2);opacity:.3;filter:url(#glow-x);
animation:lat2 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes lat2{0%,100%{opacity:.2}50%{opacity:.55}}
.nucleo{fill:var(--mi2)}
.nu-t{fill:#faf6f0;font-size:19px;text-anchor:middle;
font-family:Fraunces,Georgia,serif;font-weight:600}
.nu-s{fill:#e8d4ed;font-size:11px;text-anchor:middle;letter-spacing:.16em}

.lg{stroke-width:1.6}
.lg.nucleo{fill:var(--mi2);stroke:none}
.lg.rt{fill:var(--mi);stroke:none}
.lg.gd{fill:var(--bg);stroke:var(--tx2)}
.lg-t{font-size:21px;font-family:Fraunces,Georgia,serif;font-weight:600}
.lg-s{font-size:15px;fill:var(--tx2)}
.rg-t{font-size:21px;text-anchor:end;font-family:Fraunces,Georgia,serif;
font-weight:600}
.rg-s{font-size:15px;fill:var(--tx2);text-anchor:end}

.fu-t{font-size:23px;text-anchor:middle;font-family:Fraunces,Georgia,serif;
font-weight:600}
.fu-s{font-size:12px;text-anchor:middle}
.fu-ok{font-size:15px;fill:var(--mi);text-anchor:middle}
.entra{stroke:var(--mi);stroke-width:2.5;marker-end:url(#pa)}
.sale{stroke:var(--tx2);stroke-width:2.5;stroke-dasharray:5 5;
marker-end:url(#pn)}

.cd-h{font-size:13px}
.cd-l{stroke:var(--linea);stroke-width:2}
.cd{fill:var(--bg);stroke:var(--linea);stroke-width:2}
.cd.hecho{fill:var(--mi2);stroke:none}
.cd.foco{fill:var(--cta);stroke:none;filter:url(#glow)}
.cd-halo{fill:none;stroke:var(--cta);stroke-width:2;
animation:onda 2s ease-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes onda{0%{r:14px;opacity:.9}100%{r:38px;opacity:0}}
.cd-t{font-size:16px;fill:var(--tx2);text-anchor:middle}
.cd-t.on{fill:var(--tx);font-size:18px;font-family:Fraunces,Georgia,serif;
font-weight:600}
.cd-aqui{font-size:12px;fill:var(--cta);text-anchor:middle}
.cd-ned{font-size:20px;text-anchor:middle;font-family:Fraunces,Georgia,serif;
font-weight:600}
.ned{fill:var(--mi);filter:url(#glow-x);
animation:brilla-ned 4s ease-in-out infinite;
animation-delay:calc(-1 * (var(--o,0s) + var(--t)))}
@keyframes brilla-ned{0%,100%{opacity:.8}50%{opacity:1}}

.congelado *{animation-play-state:paused !important}
@media (prefers-reduced-motion:reduce){*{animation:none !important}}
"""


def _css(tema="oscuro"):
    # replace y no %-format: el CSS está lleno de «0%» y «100%» de los keyframes.
    return (CSS.replace("__TEMA__", TEMAS[tema].strip())
            .replace("__CX__", str(CX)).replace("__CY__", str(CY))
            .replace("__ALCANCE__", str(R_MURO - R_NUCLEO - 30))
            .replace("__RNUC__", str(R_NUCLEO))
            .replace("__RSON__", str(R_MURO - 20)))


def render(cara="privada", t=None, tema="oscuro", inv=None, pul=None, cad=None):
    """El HTML entero, autocontenido. Sin red, sin CDN, sin fuentes externas.

    `t` congela la animación en ese segundo del ciclo (para capturar un frame).
    """
    if inv is None or pul is None or cad is None:
        inv, pul, cad = datos(cara)
    cuerpo = svg(inv, pul, cad, cara)
    congelado = ' class="congelado"' if t is not None else ""
    estilo_t = ":root{--t:%ss}" % _f(float(t)) if t is not None else ""
    return ("<!doctype html><html lang='es'><head><meta charset='utf-8'>"
            "<title>El Mapa · Polaris</title><style>%s%s%s</style></head>"
            "<body%s>%s</body></html>"
            % (fuentes_css(), _css(tema), estilo_t, congelado, cuerpo))


# ─── exportar ────────────────────────────────────────────────────────────────

def _chrome():
    for c in CHROME:
        if os.path.exists(c):
            return c
    c = shutil.which("chromium") or shutil.which("google-chrome")
    if c:
        return c
    raise SystemExit("no encuentro Chrome para capturar; instala Chrome o usa render")


def _captura(html, destino, ancho, alto, chrome=None):
    """Un fotograma con Chrome headless. Sin red: el HTML es autocontenido."""
    chrome = chrome or _chrome()
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "f.html")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(html)
        cmd = [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
               "--force-device-scale-factor=1", "--virtual-time-budget=1200",
               "--window-size=%d,%d" % (ancho, alto),
               "--screenshot=%s" % destino, "file://" + f]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if not os.path.exists(destino):
            raise SystemExit("Chrome no sacó la captura:\n%s" % r.stderr[-800:])
    return destino


def png(salida="mapa.png", cara="privada", t=0.0, ancho=1400, tema="oscuro"):
    alto = int(round(ancho * H / float(W)))
    _captura(render(cara=cara, t=t, tema=tema), os.path.abspath(salida),
             ancho, alto)
    print("PNG → %s  (%dx%d)" % (salida, ancho, alto))
    return salida


def gif(salida="mapa.gif", cara="privada", fps=12, ancho=1000, tema="oscuro"):
    """Un ciclo entero (4s) fotograma a fotograma, y ffmpeg lo cose."""
    if not shutil.which("ffmpeg"):
        raise SystemExit("hace falta ffmpeg para el GIF (brew install ffmpeg)")
    chrome = _chrome()
    alto = int(round(ancho * H / float(W)))
    total = int(round(CICLO * fps))
    inv, pul, cad = datos(cara)
    salida = os.path.abspath(salida)
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(total):
            t = (i / float(fps)) % CICLO
            _captura(render(cara=cara, t=t, tema=tema, inv=inv, pul=pul, cad=cad),
                     os.path.join(tmp, "f%04d.png" % i), ancho, alto, chrome)
            sys.stderr.write("\r  fotograma %d/%d" % (i + 1, total))
            sys.stderr.flush()
        sys.stderr.write("\n")
        paleta = os.path.join(tmp, "pal.png")
        # el degradado y los glows necesitan paleta ancha o aparecen bandas
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
                        "-i", os.path.join(tmp, "f%04d.png"),
                        "-vf", "palettegen=max_colors=256:stats_mode=diff", paleta],
                       check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
                        "-i", os.path.join(tmp, "f%04d.png"), "-i", paleta,
                        "-lavfi", "paletteuse=dither=sierra2_4a",
                        "-loop", "0", salida], check=True)
    mb = os.path.getsize(salida) / 1024.0 / 1024.0
    print("GIF → %s  (%dx%d · %d fotogramas · %.1f MB)"
          % (salida, ancho, alto, total, mb))
    return salida


def serve(puerto=8792, cara="privada", tema="oscuro"):
    """Solo 127.0.0.1, mismo criterio que la Anatomía y El Observatorio."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            c = "publica" if "cara=publica" in self.path else cara
            tm = "claro" if "tema=claro" in self.path else tema
            b = render(cara=c, tema=tm).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    print("El Mapa en http://127.0.0.1:%d  (cara: %s · tema: %s)"
          % (puerto, cara, tema))
    HTTPServer(("127.0.0.1", puerto), H).serve_forever()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv):
    cmd = argv[0] if argv else "render"

    def val(flag, defecto=None):
        return argv[argv.index(flag) + 1] if flag in argv else defecto

    cara = val("--cara", "privada")
    if cara not in ("privada", "publica"):
        raise SystemExit("--cara: privada | publica")
    tema = val("--tema", "oscuro")
    if tema not in TEMAS:
        raise SystemExit("--tema: oscuro | claro")

    if cmd == "render":
        html = render(cara=cara, tema=tema)
        destino = val("-o")
        if destino:
            with open(destino, "w", encoding="utf-8") as f:
                f.write(html)
            print(destino)
        else:
            sys.stdout.write(html)
        return 0
    if cmd == "png":
        png(val("-o", "mapa.png"), cara, float(val("--t", 0.9)),
            int(val("--ancho", 1400)), tema)
        return 0
    if cmd == "gif":
        gif(val("-o", "mapa.gif"), cara, int(val("--fps", 12)),
            int(val("--ancho", 1000)), tema)
        return 0
    if cmd == "serve":
        return serve(int(val("--puerto", 8792)), cara, tema)
    if cmd == "fuentes":
        origen = val("--copiar-de")
        if origen:
            destino = os.path.expanduser("~/claudecode/tools/fonts")
            os.makedirs(destino, exist_ok=True)
            for _, _, fich in FUENTES:
                p = os.path.join(origen, fich)
                if os.path.exists(p):
                    shutil.copy2(p, os.path.join(destino, fich))
                    print("copiada %s" % fich)
                else:
                    print("no está en el origen: %s" % fich)
        d = dir_fuentes()
        print("carpeta: %s" % (d or "(ninguna) → fallback a Georgia"))
        for fam, peso, fich in FUENTES:
            hay = d and os.path.exists(os.path.join(d, fich))
            print("  %s %s  %s" % ("✓" if hay else "·", fam, fich))
        return 0
    if cmd == "json":
        inv, pul, cad = datos(cara)
        print(json.dumps({"inventario": inv["numeros"], "cadena": cad},
                         ensure_ascii=False, indent=1))
        return 0
    sys.stdout.write(__doc__.split("Uso:")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
