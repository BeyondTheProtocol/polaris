"""Planos a pulso de una pantalla en los que el encuadre SIGUE a lo que importa, fotograma a fotograma (v10, 5-oct-2026).

  pantalla_tac  la consola de la SBRT con toda la ventana del TAC a la vista y centrada ({{TITULAR}}: «menos zoom, que se vea toda
                la pantalla de su ordenador»). Sigue a la ventana por el rótulo verde «Registro de Clipbox», cuyo ancho da la
                escala. Todo lo de fuera va desenfocado: el monitor de al lado lleva la lista con OTROS pacientes; y la barra
                de título, que lleva su número de paciente, también.
  tarjeta       su monitor el día de «polaris, completo» (25-jun): el encuadre se queda en la tarjeta oscura de la estrella,
                sin la barra lateral ni el panel de la derecha.

Necesita PIL y numpy (el venv de Polaris). Lee el original en 4K y escribe 1920×1080 sin audio.
Uso suelto: python planos_seguidos.py pantalla_tac|tarjeta <clip 4K> <desde s> <dura s> <salida.mp4>
"""
import subprocess, sys

W4, H4 = 3840, 2160


def _lee(src, desde, dura):
    import numpy as np
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(desde), "-t", str(dura), "-i", src, "-vf", "fps=30", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    n = W4 * H4 * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(H4, W4, 3)
    p.wait()


def _escribe(dst):
    return subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1920x1080", "-r", "30", "-i", "-", "-an",
                             "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", dst], stdin=subprocess.PIPE)


def _suave(det, n):
    """Las detecciones (o None) de cada fotograma → una serie por coordenada, con los huecos interpolados y sin temblor."""
    import numpy as np
    ok = [k for k, d in enumerate(det) if d]
    if len(ok) < max(3, n // 4):
        sys.exit(f"solo {len(ok)} de {n} fotogramas localizados: el plano no se puede seguir")
    t = np.arange(n)
    nucleo = np.exp(-0.5 * (np.arange(-4, 5) / 1.6) ** 2); nucleo /= nucleo.sum()
    return [np.convolve(np.pad(np.interp(t, ok, [det[k][j] for k in ok]), 4, mode="edge"), nucleo, mode="valid") for j in range(len(det[ok[0]]))]


def _recorte(f, x1, y1, w, h):
    """El trozo x1,y1,w,h (en 960×540) del fotograma 4K, a 1920×1080; lo que cae fuera del fotograma se rellena con el borde."""
    import numpy as np
    from PIL import Image
    caja = [int(round(v * 4)) for v in (x1, y1, x1 + w, y1 + h)]
    M = 700
    fp = np.pad(f, ((M, M), (M, M), (0, 0)), mode="edge")
    return Image.fromarray(fp[caja[1] + M:caja[3] + M, caja[0] + M:caja[2] + M]).resize((1920, 1080), Image.LANCZOS), caja


def pantalla_tac(src, desde, dura, dst):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    K = 3.1                                          # ancho del panel sagital / ancho del rótulo
    IZQ, DER, ARR, ALTO = 2.05, 0.315, 0.08, 1.95    # bordes de la ventana, en anchos de panel desde la esquina del rótulo

    def rotulo(f):
        m = f.reshape(540, 4, 960, 4, 3).mean(axis=(1, 3))
        r, g, b = m[..., 0], m[..., 1], m[..., 2]
        verde = (g > 120) & (g - np.maximum(r, b) > 45)
        ys = np.where(verde.sum(axis=1) >= 5)[0]
        if not len(ys):
            return None
        xs = np.where(verde[ys[0]:ys[0] + 14].any(axis=0))[0]
        if len(xs) < 15:
            return None
        cortes = np.where(np.diff(xs) > 14)[0]
        ini, fin = (xs[cortes[-1] + 1] if len(cortes) else xs[0]), xs[-1]
        return None if fin >= 955 or fin - ini < 30 else (float(fin), float(ys[0]), float(fin - ini))

    marcos = list(_lee(src, desde, dura))
    xr, y0, s = _suave([rotulo(f) for f in marcos], len(marcos))
    P = K * s
    enc = _escribe(dst)
    for k, f in enumerate(marcos):
        vx1, vx2 = xr[k] - IZQ * P[k], xr[k] + DER * P[k]
        vy1 = y0[k] - ARR * P[k]; vy2 = vy1 + ALTO * P[k]
        h = 1.10 * (vy2 - vy1); w = h * 16 / 9
        im, caja = _recorte(f, (vx1 + vx2) / 2 - w / 2, (vy1 + vy2) / 2 - h / 2, w, h)
        e = 1920 / (w * 4)
        px = lambda x: (x * 4 - caja[0]) * e
        py = lambda y: (y * 4 - caja[1]) * e
        # lo nítido empieza DEBAJO de la barra de título y la de menús: en la barra van su nombre y su número de paciente
        mask = Image.new("L", im.size, 0)
        dr = ImageDraw.Draw(mask)
        dr.rectangle([max(px(vx1) - 6, px(0)), py(vy1 + 0.075 * (vy2 - vy1)), min(px(vx2) + 4, px(960)), min(py(vy2) + 30, py(540) - 8)], fill=255)
        # las dos líneas con fecha y hora (la de la imagen y «Tiempo explor.»): la fecha de su tratamiento no sale (brief)
        vw, vh = vx2 - vx1, vy2 - vy1
        for xa, xb, ya, yb in ((0.0, 0.47, 0.51, 0.58), (0.0, 0.47, 0.935, 1.0)):
            dr.rectangle([px(vx1 + xa * vw) - 8, py(vy1 + ya * vh), px(vx1 + xb * vw), py(vy1 + yb * vh)], fill=0)
        sal = Image.composite(im, im.filter(ImageFilter.GaussianBlur(24)), mask.filter(ImageFilter.GaussianBlur(6)))
        enc.stdin.write(sal.tobytes())
    enc.stdin.close(); enc.wait()


def tarjeta(src, desde, dura, dst):
    import numpy as np

    def caja_oscura(f):
        """La tarjeta azul noche sobre la página blanca: el bloque oscuro más alto (el marco del monitor es una raya, la cámara web es estrecha)."""
        m = f.reshape(540, 4, 960, 4, 3).mean(axis=(1, 3))
        osc = (m[..., 0] < 75) & (m[..., 1] < 80) & (m[..., 2] < 120) & (m[..., 2] > m[..., 0] + 6)
        def tramo(v):
            """El tramo seguido más largo de un vector de sí/no (a la izquierda hay un brazo de micro, también oscuro)."""
            mejor, ini = (0, 0), None
            for k in range(len(v) + 1):
                if k < len(v) and v[k]:
                    ini = k if ini is None else ini
                elif ini is not None:
                    mejor, ini = (max(mejor, (ini, k), key=lambda p: p[1] - p[0]), None)
            return mejor

        ya, yb = tramo(osc.sum(axis=1) > 150)
        if yb - ya < 60:
            return None
        xa, xb = tramo(osc[ya:yb].sum(axis=0) > 0.5 * (yb - ya))
        if xb - xa < 100:
            return None
        return float(xa), float(xb), float(ya), float(yb)

    marcos = list(_lee(src, desde, dura))
    x1, x2, y1, y2 = _suave([caja_oscura(f) for f in marcos], len(marcos))
    enc = _escribe(dst)
    from PIL import Image, ImageDraw, ImageFilter
    for k, f in enumerate(marcos):
        h = 1.14 * (y2[k] - y1[k]); w = h * 16 / 9
        im, caja = _recorte(f, (x1[k] + x2[k]) / 2 - w / 2, (y1[k] + y2[k]) / 2 - h / 2, w, h)
        e = 1920 / (w * 4)                           # lo de fuera de la tarjeta (restos de la interfaz) va desenfocado
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([(x1[k] * 4 - caja[0]) * e - 10, (y1[k] * 4 - caja[1]) * e - 10, (x2[k] * 4 - caja[0]) * e + 10, (y2[k] * 4 - caja[1]) * e + 10],
                                               radius=40, fill=255)
        im = Image.composite(im, im.filter(ImageFilter.GaussianBlur(22)), mask.filter(ImageFilter.GaussianBlur(5)))
        enc.stdin.write(im.tobytes())
    enc.stdin.close(); enc.wait()


def lesion_cerca(src, desde, dura, dst, lento=1.6):
    """Su portátil con la imagen de la prueba, en un encuadre cerrado y NÍTIDO sobre la imagen ({{TITULAR}}, 6-oct-2026: «los dos
    borrosos del principio, mejor escenas que no sean borrosas»). Antes iba todo desenfocado menos la lesión, porque encima de
    la imagen hay un rótulo con una cifra clínica y a la izquierda una conversación. Aquí el encuadre sigue a la lesión (lo
    cálido: su borde izquierdo y su borde de arriba, que no los tapa el dedo) y deja fuera el rótulo, la conversación y la
    escala. El tramo bueno es corto, así que sale a cámara lenta («lento» veces más largo), con fotogramas interpolados."""
    import numpy as np
    W, H, IZQ, ARR = 350, 197, 175, 40           # el encuadre y dónde cae en él la esquina de la lesión, en 960×540

    def esquina(f):
        m = f.reshape(540, 4, 960, 4, 3).mean(axis=(1, 3))
        r, g, b = m[..., 0], m[..., 1], m[..., 2]
        ys, xs = np.nonzero((r > 170) & (b < 130) & (r - b > 80) & (g > 60))
        return (float(np.percentile(xs, 2)), float(np.percentile(ys, 2))) if len(xs) > 2000 else None

    marcos = list(_lee(src, desde, dura))
    x1, y1 = _suave([esquina(f) for f in marcos], len(marcos))
    tmp = dst + ".tmp.mp4"
    enc = _escribe(tmp)
    for k, f in enumerate(marcos):
        im, _ = _recorte(f, x1[k] - IZQ, y1[k] - ARR, W, H)
        enc.stdin.write(im.tobytes())
    enc.stdin.close(); enc.wait()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-vf", f"minterpolate=fps={30 * lento:.0f}:mi_mode=mci:mc_mode=aobmc:vsbmc=1,setpts={lento}*PTS,fps=30", "-an",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", dst], check=True)
    import os
    os.remove(tmp)


if __name__ == "__main__":
    {"pantalla_tac": pantalla_tac, "tarjeta": tarjeta, "lesion_cerca": lesion_cerca}[sys.argv[1]](sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5])
