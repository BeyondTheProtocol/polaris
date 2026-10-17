"""Monta el taller del vídeo de Orellana: todo lo que Remotion necesita y git ignora (remotion/public, node_modules).

Uso:  ~/claudecode/.venv/bin/python tools/video_polaris/taller_orellana.py [timeline_de_un_master.json]
      después:  DESTINO=<carpeta> bash remotion/render.sh <vN> Narrado orellana-circulo
      (con el venv de Polaris: el plano de la pantalla médica necesita PIL y numpy)

El 4-oct-2026 el worktree del rodaje se podó y con él se fue lo ignorado: recursos, clips procesados y las tomas de voz.
Por eso las FUENTES viven fuera del worktree y aquí solo se copian o se recalculan:
  recursos compartidos   07 · Marca/Videos-Polaris/recursos-remotion
  planos de Flow         07 · Marca/Videos-Polaris/flow
  fotos, clips crudos    _data/orellana-video-2026-10-02-fuera-de-rag/produccion  (fotos-originales/, stock/)
  sus vídeos (DJI)       produccion/dji-hoy/clips  (bajados de Drive; la tabla DJI dice qué tramo de cada uno)
  voz y tiempos          produccion/build-orellana-v9  (voz.wav y timeline.json, de narrado.py)

Los tiempos son los de la voz ya grabada y las escenas, las del guion de hoy: cambiar una escena en
guiones/orellana-circulo.es.json no toca la voz. Cambiar lo que DICE sí: eso es narrado.py, y pide su OK.
Sin voz.wav, voz.wav se deja en SILENCIO y marcado, y el render pide AUDIO_DE=<máster anterior> (solo cambia la imagen).
"""
import json, os, shutil, subprocess, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
BASE = os.path.expanduser("~/claudecode")
MARCA = os.path.join(BASE, "00_FUENTE-DE-VERDAD", "07 · Marca")
PROD = os.path.join(BASE, "_data", "orellana-video-2026-10-02-fuera-de-rag", "produccion")
GUION = os.path.join(AQUI, "guiones", "orellana-circulo.es.json")
REMOTION = os.path.join(AQUI, "remotion")
PUB = os.path.join(REMOTION, "public")
BUILD = os.path.join(PROD, "build-orellana-v10")  # la voz: la toma entera de la v9 con el capítulo «peticion» regrabado (narrado.py la deja aquí)
MASTER = os.path.join(BUILD, "timeline.json") if os.path.exists(os.path.join(BUILD, "timeline.json")) else \
    os.path.join(MARCA, "Videos-Orellana", "orellana-circulo-v8-timeline.json")
CLIPS = os.path.join(PROD, "dji-hoy", "clips")

ESC = "scale=1920:1080:flags=lanczos"
# Clips de ambiente de ElevenLabs (5 s, 1280×720, 24 fps) → sin audio, 1920×1080 y 30 fps:
#   teclado: ida y vuelta, para que el bucle no tenga corte · escritorio: a 0,42× con fotogramas interpolados ·
#   puerta: cerrada 9,6 s, se abre cuando dice «abrir puertas» y se queda abierta (nunca al revés: se cerraría).
STOCK = {
    "teclado": f"[0:v]{ESC},fps=30,split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1[v]",
    "escritorio": f"[0:v]minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1,setpts=2.4*PTS,fps=30,{ESC}[v]",
    "puerta": f"[0:v]{ESC},fps=30,tpad=start_mode=clone:start_duration=9.6:stop_mode=clone:stop_duration=4.6[v]",
}
V1080 = "scale=1920:1080:flags=lanczos,fps=30,format=yuv420p"
LENTO = "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1,setpts=2*PTS,"  # a la mitad, con fotogramas interpolados
# Sus planos de la cámara DJI (v9, 4-oct-2026: «ya no hace falta hacer vídeos de stock si alguno de los que he pasado
# sirve»). 4K HEVC de 10 bits → 1920×1080, 30 fps, sin audio. nombre: (fecha, número de clip, desde s, dura s, filtro, [recorte en 4K])
#   panel solo usa el tramo del panel de Polaris: antes se ve su lista de tareas, con nombres de terceros.
#   pantalla-medica va aparte (foco_lesion): todo desenfocado menos la lesión, porque el rótulo lleva una cifra de SUV.
#   El sexto valor, si lo hay, se aplica ANTES de reducir, sobre el 4K: un recorte «ancho:alto:x:y» o un filtro entero.
def _tiras(pol, paso):
    """Un polígono (lista de puntos) → las tiras horizontales (x, y, ancho, alto) que lo rellenan, de «paso» px de alto."""
    out, y, fin = [], min(p[1] for p in pol), max(p[1] for p in pol)
    while y < fin:
        yc = y + paso / 2
        xs = sorted(x1 + (yc - y1) * (x2 - x1) / (y2 - y1) for (x1, y1), (x2, y2) in zip(pol, pol[1:] + pol[:1]) if (y1 <= yc < y2) or (y2 <= yc < y1))
        out += [(round(a), y, round(b - a), paso) for a, b in zip(xs[::2], xs[1::2]) if b - a >= 1]
        y += paso
    return out


def tapa(*zonas, s=18, borde=14, apaga=False, cuando=None):
    """Desenfoca zonas fijas (cámara quieta) con el borde difuminado, para que no se note el parche: caras de terceros al
    fondo o una pantalla con datos. Cada zona es un rectángulo (x, y, ancho, alto) o un polígono [(x, y), …] cuando el borde
    va inclinado o hay que rodear una cabeza (el comité de diseño vio los escalones de dos cajas juntas). Con coordenadas de
    1920×1080 va en el quinto valor (después de reducir); con coordenadas del 4K y s y borde mayores, en el sexto.
    «apaga» además oscurece el parche (un monitor que brilla). «cuando» = (desde, hasta) en segundos del plano ya montado:
    el parche solo actúa en ese tramo (alguien que asoma un momento)."""
    cajas = [c for z in zonas for c in (_tiras(list(z), max(4, borde // 2)) if isinstance(z, list) else [z])]
    blancos = "".join(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=white:t=fill," for x, y, w, h in cajas)
    solo = f":enable='between(t,{cuando[0]},{cuando[1]})'" if cuando else ""
    return (f"format=gbrp,split=3[a][b][m];[m]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,{blancos}"
            f"gblur=sigma={borde},format=gbrp[mk];[b]gblur=sigma={s}{solo}" + (",eq=brightness=-0.22:saturation=0.35,format=gbrp" if apaga else "") +
            "[bl];[a][bl][mk]maskedmerge,")


def acerca(x, y, zoom, t0, t1, hasta):
    """De plano entero a un encuadre «zoom» veces más cerrado centrado en x,y (del 4K): quieto hasta t0, llega en t1."""
    e = f"clip((on-{t0 * 30:.0f})/{(t1 - t0) * 30:.0f},0,1)"
    s = f"({e})*({e})*(3-2*({e}))"
    return (f"fps=30,zoompan=z='1+{zoom - 1:.4f}*{s}':x='(1920+{x - 1920:.0f}*{s})-iw/zoom/2':y='(1080+{y - 1080:.0f}*{s})-ih/zoom/2'"
            f":d=1:s=1920x1080:fps=30,setpts=N/(30*TB),trim=duration={hasta}")  # sin el setpts, zoompan hereda la base de tiempos de la cámara


# v10 (5-oct-2026): los clips los eligió ella, uno a uno, viéndolos («vamos a elegir los clips primero y luego ponemos bien
# los vídeos»). Cada tramo está escogido para que no salga la cara de nadie más, salvo en el escenario y en el pódcast.
DJI = {
    # su monitor con la conversación con la IA y su mano. Lleva desenfoque: a 1080p se leía la conversación y, en el terminal
    # de al lado, un error con el principio de una clave de API. Recorte: fuera el retrato de la pared
    "ia-monitor": ("20260623", "0164", 14.4, 5, "gblur=sigma=7,", "3072:1728:768:432"),
    "{{CIUDAD}}-camino": ("20260708", "0125", 14.3, 3.6, ""),  # las dos de espaldas y, al girar la cámara, el hospital
    "consulta": ("20260708", "0126", 4.4, 5, "", "2611:1469:0:691"),  # recorte: fuera el rótulo vertical de la recepción y la placa de la sala
    "biopsia": ("20260708", "0130", 1, 14, ""),
    "trabajando": ("20260609", "0037", 84, 14, ""),
    # «trabajando» (el portátil en el hotel) abre «me puse a investigar con IA», limpio: quiere verse en el ordenador de
    # primeras, sin planos borrosos y «más profesional» que en el tren de noche (6-oct). Para no repetirlo, bajo «esperar, o
    # construir» va este otro, con velo: ella de perfil ante el monitor de casa (a contraluz; como fondo oscurecido no importa)
    "monitor-casa": ("20260623", "0163", 12, 8, ""),
    "google-puerta": ("20260707", "0117", 11.4, 3.2, "", "2880:1620:960:540"),
    # grabado en espejo (se endereza); al fondo hay dos personas que miran a cámara: van desenfocadas
    # (un solo polígono: todo el fondo a la izquierda de su silueta, por encima de la línea del hombro)
    "google-trabajando": ("20260707", "0090", 3, 3.2, tapa([(0, 100), (590, 100), (590, 395), (560, 420), (300, 570), (275, 625), (0, 625)], borde=24), "hflip"),
    "panel": ("20260622", "0160", 5.6, 7.4, LENTO),
    # el mismo plano para ir DETRÁS de la lista, con velo: desenfocado, porque al abrirse el encuadre asoma otra ventana con
    # texto («Remote connection password…») y un pósit, y el velo solo no los tapa (verificación, 6-oct)
    "panel-fondo": ("20260622", "0160", 5.6, 7.4, LENTO + "gblur=sigma=11,"),
    "cuaderno": ("20260615", "0114", 133, 10, "", "hflip"),  # grabado en espejo: el cartel del fondo salía al revés
    # el saludo con el micro puesto, sin la persona del borde izquierdo. Al técnico, detrás, se le llegan a ver contacto y nariz
    # a ratos (verificación, 6-oct): va un parche sobre la zona por la que se mueve su cabeza durante todo el plano
    "micro": ("20260609", "0071", 59.0, 2.8, "setpts=1.1*PTS,fps=30," + tapa((180, 500, 430, 430), borde=16), "2688:1512:920:60"),
    # primero las cuatro y luego ella (lo pidió así). La pantalla del escenario va desenfocada desde el 4K: en ella se leen el
    # nombre y el cargo de otra ponente, «paciente de CMm», y el logo y el lema de la asociación que organiza
    # (polígono que sigue el borde inclinado de la pantalla y rodea por arriba las tres cabezas que la pisan; el segundo es la
    # pantalla pequeña de la izquierda, con el logo)
    "mesa-redonda": ("20260609", "0076", 1370, 6.5, "", tapa(
        [(780, 0), (3840, 0), (3840, 470), (3200, 540), (3200, 458), (2850, 458), (2850, 578), (2130, 656), (2130, 612), (1890, 612), (1890, 682),
         (1460, 730), (1460, 700), (1260, 700), (1260, 752), (710, 810)],
        [(0, 0), (480, 0), (480, 584), (0, 644)], s=46, borde=28) + acerca(3020, 901, 2.3415, 0.6, 1.4, 6)),  # a 1,4 s ya solo sale ella
    "podcast": ("20260616", "0138", 15.5, 7, "", "2880:1620:480:150"),
    # del 3,2 al 9,8: mano a la boca, abre y bebe; la pastilla no se ve. Recorte: fuera la banda azul de la sábana, que lleva
    # (en espejo) el nombre del hospital, y {{TITULAR}} pidió que no se diga ni se escriba
    # Estaba grabado en espejo (el número de la pared salía al revés): se endereza
    "medicacion": ("20260715", "0174", 3.2, 6.6, "setpts=PTS/2,", "crop=2900:1632:940:0,hflip"),
    # la sala de la SBRT a cámara rápida (×8), desde que sale el personal; el monitor de pared lleva sus datos: desenfocado
    "sbrt-rapido": ("20260620", "0152", 250, 120, "setpts=PTS/8,fps=30," + tapa((1776, 370, 144, 130), (1085, 432, 60, 46), borde=10, apaga=True)),
    "ensayo": ("20260715", "0177", 29.5, 14.5, ""),
    # ella en un puente de Zúrich, apoyada en la barandilla, señalando el río: va donde antes volvía a salir su foto (6-oct:
    # «cuando vuelve a salir, mejor un vídeo»; de cuatro candidatos eligió este). Recorte: fuera la acera de la derecha, por
    # donde vienen dos peatones de frente
    "puente": ("20260709", "0149", 23.2, 3.9, "", "2996:1686:0:300"),
    # NO se usan: la consola de radioterapia en plano abierto (0153, 0155-0157 del 20-jun: nombres y número de historia de
    # otros pacientes), el congreso (0073: presentadora y público) ni el camino a Zúrich antes del segundo 13 (su acompañante de cara).
}
FOCO = {"pantalla-medica": ("20260609", "0043", 5.6, 6.0)}
# Planos de una pantalla, a pulso, en los que el encuadre sigue a lo que importa (planos_seguidos.py): nombre: (fecha, clip, desde, dura, función)
SEGUIDOS = {
    # la imagen de la prueba en su portátil, en encuadre cerrado y nítido (6-oct: no quiere planos borrosos al principio)
    "prueba-imagen": ("20260609", "0043", 6.2, 1.8, "lesion_cerca"),
    "tac-pantalla": ("20260620", "0154", 19.5, 3.5, "pantalla_tac"),
    "estrella": ("20260625", "0172", 0.3, 4, "tarjeta"),
}
IMAGENES = [  # (origen, destino en public/, recorte «ancho:alto:x:y» o None)
    (os.path.join(MARCA, "titular_dreamy_cosmic_gouache_illustration_a_spiral_staircas_77e402c5-e034-4b08-a911-708c8c3c8c48.png"), "orellana/lamina.png", None),
    (os.path.join(MARCA, "titular_dreamy_cosmic_gouache_illustration_a_spiral_staircas_77e402c5-e034-4b08-a911-708c8c3c8c48.png"), "orellana/escalera-detalle.jpg", "1920:1080:0:912"),
]
COPIAS = [
    (os.path.join(MARCA, "Videos-Polaris", "flow", "portada-estrella-noche.mp4"), "narrado/flow/portada.mp4"),
    (os.path.join(MARCA, "Videos-Polaris", "flow", "cierre-amanecer.mp4"), "narrado/flow/cierre.mp4"),
    (os.path.join(PROD, "fotos-originales", "retrato-horizontal.jpg"), "orellana/retrato-horizontal.jpg"),
    (os.path.join(PROD, "fotos-originales", "retrato-vertical.jpg"), "orellana/retrato-vertical.jpg"),
    (os.path.join(PROD, "fotos-originales", "portatil.jpg"), "orellana/portatil.jpg"),
    (os.path.join(PROD, "polaris-timeline.json"), "timeline.json"),  # Root.tsx lo importa aunque solo se pinte «Narrado»
]


def recursos():
    shutil.copytree(os.path.join(MARCA, "Videos-Polaris", "recursos-remotion"), PUB, dirs_exist_ok=True)
    for src, rel in COPIAS:
        dst = os.path.join(PUB, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    for nombre, filtro in STOCK.items():
        src, dst = os.path.join(PROD, "stock", nombre + "-crudo.mp4"), os.path.join(PUB, "narrado", "stock", nombre + ".mp4")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", filtro, "-map", "[v]", "-an",
                            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", dst], check=True)


def giros_fluidos(carpetas=("mama", "higado")):
    """Los visores 3D son capturas de pantalla del visor girando, y la captura va a trompicones: el visor pintaba a unos
    20 fotogramas por segundo y se grabó a 30, así que cada tres fotogramas hay uno repetido y los otros dos dan pasos
    desiguales. {{TITULAR}} lo vio el 6-oct («el de la mama pega un tropezón al girar, tiene que girar fluido»). Aquí la
    secuencia se vuelve a repartir: se quitan los repetidos, se mide cuánto avanza el giro de un fotograma al siguiente y
    cada fotograma de salida se saca de mezclar los dos reales entre los que cae, a paso parejo (el ritmo lento del giro
    se respeta; lo que se quita es el traqueteo). Lee de la carpeta de marca y escribe en public/.
    El otro tropezón, el salto del último fotograma al primero, lo evita Narrado.tsx yendo y volviendo (Secuencia, «desde»)."""
    import numpy as np
    from PIL import Image
    origen = os.path.join(MARCA, "Videos-Polaris", "recursos-remotion")
    for d in carpetas:
        fs = sorted(x for x in os.listdir(os.path.join(origen, d)) if x.endswith(".jpg"))
        peq = [np.asarray(Image.open(os.path.join(origen, d, f)).convert("L").resize((265, 265)), dtype=np.float32) for f in fs]
        dif = np.array([np.abs(peq[k + 1] - peq[k]).mean() for k in range(len(peq) - 1)])
        unicos = [0] + [k + 1 for k, v in enumerate(dif) if v >= 0.2 * np.median(dif)]      # el primero de cada tanda de repetidos
        pos = np.concatenate([[0], np.cumsum([np.abs(peq[b] - peq[a]).mean() for a, b in zip(unicos, unicos[1:])])])  # avance del giro en cada fotograma real
        ritmo = np.convolve(np.pad(dif, 10, mode="edge"), np.ones(21) / 21, mode="valid")   # el paso medio alrededor de cada fotograma
        meta = np.concatenate([[0], np.cumsum(ritmo)]) * pos[-1] / ritmo.sum()               # dónde tiene que estar el giro en cada fotograma de salida
        for m, f in enumerate(fs):
            j = min(int(np.searchsorted(pos, meta[m], side="right")), len(unicos) - 1)
            a, b = unicos[j - 1], unicos[j]
            frac = float(np.clip((meta[m] - pos[j - 1]) / max(pos[j] - pos[j - 1], 1e-9), 0, 1))
            A = Image.open(os.path.join(origen, d, fs[a])).convert("RGB")
            (A if frac < 0.02 else Image.blend(A, Image.open(os.path.join(origen, d, fs[b])).convert("RGB"), frac)).save(os.path.join(PUB, d, f), quality=93)


def clip_dji(fecha, num):
    return next(os.path.join(CLIPS, f) for f in sorted(os.listdir(CLIPS)) if f.startswith("DJI_" + fecha) and f.split("_")[2] == num and f.endswith(".MP4"))


def foco_lesion(src, desde, dura, dst):
    """La pantalla con «cosas médicas» sin que se lea nada: todo desenfocado menos la lesión (lo cálido de la imagen),
    seguida fotograma a fotograma porque el plano es a pulso. Necesita PIL y numpy (el venv de Polaris)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    W, H = 1920, 1080
    lee = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(desde), "-t", str(dura), "-i", src, "-vf", V1080.replace("yuv420p", "rgb24"),
                            "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    escribe = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "30", "-i", "-", "-an",
                                "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", dst], stdin=subprocess.PIPE)
    caja = None
    while True:
        raw = lee.stdout.read(W * H * 3)
        if len(raw) < W * H * 3:
            break
        a = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
        r, gg, b = (a[::4, ::4, k].astype(int) for k in range(3))
        ys, xs = np.nonzero((r > 170) & (b < 130) & (r - b > 80) & (gg > 60))
        if len(xs) > 150:
            nueva = np.array([np.percentile(xs, 3), np.percentile(ys, 3), np.percentile(xs, 97), np.percentile(ys, 97)]) * 4
            caja = nueva if caja is None else 0.7 * caja + 0.3 * nueva
        im = Image.fromarray(a)
        borroso = im.filter(ImageFilter.GaussianBlur(11))
        if caja is not None:
            cx, cy, rx, ry = (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2, 0.8 * (caja[2] - caja[0]), 0.8 * (caja[3] - caja[1])
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
            borroso = Image.composite(im, borroso, m.filter(ImageFilter.GaussianBlur(28)))
        escribe.stdin.write(borroso.tobytes())
    escribe.stdin.close(); escribe.wait(); lee.wait()


def planos():
    """Los planos de la cámara DJI y las imágenes de marca y prensa. Solo rehace lo que falta o es más viejo que su fuente."""
    dest = os.path.join(PUB, "narrado", "dji"); os.makedirs(dest, exist_ok=True)
    viejo = lambda dst, src: not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src)
    for nombre, (fecha, num, desde, dura, antes, *recorte) in DJI.items():
        src, dst = clip_dji(fecha, num), os.path.join(dest, nombre + ".mp4")
        if viejo(dst, src):
            # primero a 1080p y después el filtro: interpolar fotogramas en 4K tarda cuatro minutos por clip
            previo = "" if not recorte else (recorte[0].rstrip(",") if not recorte[0][0].isdigit() else "crop=" + recorte[0]) + ","
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(desde), "-t", str(dura), "-i", src, "-vf", previo + "scale=1920:1080:flags=lanczos," + antes + "fps=30,format=yuv420p", "-an",
                            "-c:v", "libx264", "-preset", "slow", "-crf", "18", dst], check=True)
    for nombre, (fecha, num, desde, dura) in FOCO.items():
        src, dst = clip_dji(fecha, num), os.path.join(dest, nombre + ".mp4")
        if viejo(dst, src):
            foco_lesion(src, desde, dura, dst)
    import planos_seguidos
    for nombre, (fecha, num, desde, dura, funcion) in SEGUIDOS.items():
        src, dst = clip_dji(fecha, num), os.path.join(dest, nombre + ".mp4")
        if viejo(dst, src):
            getattr(planos_seguidos, funcion)(src, desde, dura, dst)
    for src, rel, recorte in IMAGENES:
        dst = os.path.join(PUB, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
        if viejo(dst, src):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src] + (["-vf", "crop=" + recorte] if recorte else []) + [dst], check=True)


def linea_de_tiempo(g, master):
    """Los tiempos (frases y palabras) del máster con las escenas del guion de hoy. Si el texto hablado no coincide,
    se para: los tiempos de otra narración no valen y esa voz ya no existe."""
    T = json.load(open(master, encoding="utf-8"))
    if [c["id"] for c in g["capitulos"]] != [c["id"] for c in T["capitulos"]]:
        sys.exit("los capítulos del guion no son los del máster: hay que regenerar la voz (narrado.py)")
    for c, m in zip(g["capitulos"], T["capitulos"]):
        if [f.get("pantalla") or f["texto"] for f in c["frases"]] != [f["texto"] for f in m["frases"]]:
            sys.exit(f"el texto de «{c['id']}» no es el del máster: hay que regenerar la voz (narrado.py)")
        m.update({k: c[k] for k in ("kicker", "titulo", "escena")})
    T["cifras"].update(g.get("cifras_extra", {}))
    if g.get("aviso"):
        T["aviso"] = g["aviso"] | {"entrada": g["entrada"]}
    return T


def audio(g, T):
    """Música y efectos salen de la línea de tiempo (son deterministas). La voz, de las tomas; sin ellas, silencio marcado."""
    import narrado
    nd = os.path.join(PUB, "narrado")
    narrado.cama_musical(os.path.join(narrado.MARCA, g["musica"]), T["total"], os.path.join(nd, "musica.wav"))
    narrado.efectos(T, g, os.path.join(nd, "sfx.wav"))
    voz, marca = os.path.join(BUILD, "voz.wav"), os.path.join(nd, "voz.SILENCIO")
    if os.path.exists(voz):
        shutil.copyfile(voz, os.path.join(nd, "voz.wav"))
        if os.path.exists(marca):
            os.remove(marca)
        return True
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(T["total"]),
                    os.path.join(nd, "voz.wav")], check=True)
    open(marca, "w").write("voz.wav es silencio: renderiza con AUDIO_DE=<máster anterior>\n")
    return False


def faltan(T):
    """Todo fichero que nombra una escena tiene que estar en public/ (un staticFile que no existe rompe el render a medias)."""
    malos = []

    def mira(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("src", "video", "fondo", "lamina", "foto") and isinstance(v, str) and not os.path.exists(os.path.join(PUB, v)):
                    malos.append(v)
                elif k == "dir" and not os.path.exists(os.path.join(PUB, v, "0001.jpg")):
                    malos.append(v + "/0001.jpg")
                else:
                    mira(v)
        elif isinstance(o, list):
            for x in o:
                mira(x)
    mira([c["escena"] for c in T["capitulos"]])
    return malos


def main(master=MASTER):
    g = json.load(open(GUION, encoding="utf-8"))
    recursos()
    giros_fluidos()
    planos()
    T = linea_de_tiempo(g, master)
    os.makedirs(os.path.join(PUB, "narrado"), exist_ok=True)
    with open(os.path.join(PUB, "narrado", "timeline.json"), "w", encoding="utf-8") as fh:
        json.dump(T, fh, ensure_ascii=False, indent=1)
    con_voz = audio(g, T)
    if not os.path.exists(os.path.join(REMOTION, "node_modules", ".bin", "remotion")):
        subprocess.run(["npm", "ci"], cwd=REMOTION, check=True)  # el node_modules no viaja entre worktrees
    malos = faltan(T)
    if malos:
        sys.exit("faltan en public/: " + ", ".join(malos))
    print(f"✓ taller montado: {T['total']} s, {len(T['capitulos'])} capítulos, tiempos de {os.path.basename(master)}")
    print("  voz: " + ("la de las tomas guardadas" if con_voz else "SILENCIO → render con AUDIO_DE=<máster anterior> (solo cambia la imagen)"))


if __name__ == "__main__":
    main(*sys.argv[1:2])
