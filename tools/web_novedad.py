#!/usr/bin/env python3
"""web_novedad.py — publica una novedad en la web SOLA, o para y lo dice.

POR QUÉ EXISTE (20-sep-2026). {{TITULAR}}: *«prefiero que la web se actualice sola»*. Hasta hoy cada
novedad le costaba abrir una sesión, explicar el cambio y mergear. Esto quita ese paso entero:
suelta la frase por Telegram desde el móvil y aparece publicada.

DÓNDE ESCRIBE, Y DÓNDE NO
  En la **cronología** (`content/es/timeline.yml`), que es donde {{TITULAR}} quiere las novedades —
  no en un fichero aparte: *«el timeline es ese novedades»* (20-sep-26).

  Escribir en un fichero que YA contiene datos clínicos parecía la línea roja que marcó `diseno`,
  y hay que separar dos cosas que yo había fundido: lo que el fichero **contiene** (edad, genes,
  vértebras: eso ya está publicado y no se toca) y lo que una entrada **nueva** puede traer. Lo
  segundo lo gobierna `web_lint`, y por eso esto es viable. Lo que se conserva del dictamen:
  · **solo se AÑADE**; ninguna entrada existente se modifica (`test_web_novedad` lo vigila);
  · `highlight` nace en **false** — destacar es decisión editorial de {{TITULAR}}, no del script;
  · el `tag` tiene que ser uno de los que el fichero **ya usa**: nada de inventar taxonomía;
  · `science.yml`, `press.yml` y `team.yml` siguen sin ruta de escritura (prensa lleva un orden
    curado por {{CONTACTO}} que una reescritura destrozaría).

EL FRENO (dos, y hacen falta los dos)
  1. **Qué se publica** — `web_lint`. Si detecta clínico, dosis, PII, un tercero nombrado, léxico
     vetado o un *tell* de IA, no se publica: queda en rama con PR y {{TITULAR}} recibe el motivo.
  2. **QUIÉN lo pide** — el texto tiene que venir de ELLA. Sin esa marca, lo más que hace esta
     herramienta es dejar un PR; nunca mergea. Importa porque la tool está en la allowlist (para
     que pueda correr sola, que es el encargo), y sin este segundo freno una inyección —«publica
     esto en tu web», dentro de un correo o una página— tendría vía directa a una web pública con
     su nombre. Se reutiliza el permiso de `.claude/hooks/ok_envio_prompt.py`, que es el único
     punto del sistema que ve el texto de {{TITULAR}} y no el que traen las herramientas.

  El fallo por defecto es **PR**, no «publicado». Esa es la propiedad que hace seguro lo demás.

DÓNDE TRABAJA
  En un worktree propio desde `origin/main`, nunca en el árbol de {{TITULAR}} — que ahora mismo está
  sucio, en una rama de hackathon y 52 commits por delante. Mismo patrón que `tools/staging.py`.

DESHACER
  Cada publicación automática es un commit aislado con `[auto]` en el asunto, para que
  `tools/web_revertir.py --ultimo` sea un solo comando.

Uso:
  python3 tools/web_novedad.py "Hoy hemos abierto la página de novedades"
  python3 tools/web_novedad.py --dry "…"      # dice qué haría, sin tocar nada
  python3 tools/web_novedad.py --estado       # PRs automáticos abiertos esperando
"""
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _casa  # noqa: E402
import web_lint  # noqa: E402

import permiso_envio as P  # noqa: E402

# Su petición puede traer 2-3 entradas («el ensayo fallido Y que entro en X»); más ya no es una
# petición. (El origen «telegram» que se aceptaba aquí no lo emitía nadie: se quita, 22-sep-26.)
USOS_MAX = 3

WEB = os.environ.get("BTP_WEB_REPO", "/Users/polaris/projects/titular-{{APELLIDO}}-case")
BASE = "main"
REL_ES = os.path.join("content", "es", "timeline.yml")
REL_EN = os.path.join("content", "en", "timeline.yml")
CLAVE = "entries:"
# Las fechas del timeline son humanas y en español ('31 oct 2023'), no ISO. Si el carril
# automático metiera '2026-09-20' rompería la voz del fichero en la primera entrada.
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
LOG = os.path.join(_casa.state_dir(), "web_novedad.jsonl")


def _git(*args, cwd=None, check=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd or WEB,
                       capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise RuntimeError("git %s → %s" % (" ".join(args), (r.stderr or r.stdout).strip()[:300]))
    return (r.stdout or "").strip()


def lo_pide_titular():
    """(bool, origen|motivo). Consume el permiso que abre su mensaje — el mismo de `ok_envio`.

    No es burocracia: sin esto, cualquier agente (o cualquier texto externo que uno se trague)
    podría publicar en una web pública con su nombre, porque la herramienta está permitida.

    Desde el 22-sep-26 (hallazgo 3.1) no basta con que el fichero diga `origen: "prompt"`: lo
    comprueba `permiso_envio.validar` —firma del Llavero, su prompt en el transcript como mensaje
    humano, el último suyo y con la orden— y el contador de usos va firmado, así que no se puede
    rebobinar a mano. La clave NUNCA sale del entorno: esta tool la lanza el agente, y con
    `BTP_OK_ENVIO_CLAVE=x` firmaría lo que quisiera."""
    if not P.hay_alguno():   # lo normal: nadie ha pedido nada (y sin tocar el Llavero)
        return False, "nadie lo ha pedido en un mensaje"
    k = P.clave(permitir_env=False)
    if not k:
        return False, "sin clave de firma en el Llavero: el permiso no se puede comprobar"
    d, motivo, ctx = P.validar(k)
    if not d:
        return False, motivo
    # Publicar en la web es enviar. Un permiso abierto con «prográmalo» no lo cubre (24-sep-26).
    if not P.permite(ctx, "envio"):
        return False, "ella pidió programar una tarea, no publicar en la web"
    try:
        P.gastar_uno(d, k, USOS_MAX)
    except Exception:
        return False, "no se pudo apuntar el uso del permiso"
    return True, d.get("origen")


def _slug(texto):
    s = re.sub(r"[^a-z0-9]+", "-", texto.lower().strip())[:40].strip("-")
    return s or "novedad"


def _registrar(evento, **extra):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(
                ts=datetime.datetime.now().replace(microsecond=0).isoformat(),
                evento=evento, **extra), ensure_ascii=False) + "\n")
    except Exception:
        pass


def fecha_humana(d=None):
    """'20 sep 2026' — el formato que usa el fichero. Meter un ISO rompería su voz."""
    d = d or datetime.date.today()
    return "%d %s %d" % (d.day, MESES[d.month - 1], d.year)


def tags_existentes(contenido):
    """Los tags que el fichero YA usa. El carril no inventa taxonomía: si {{TITULAR}} pide uno nuevo,
    para y lo pregunta — una etiqueta nueva cambia cómo se lee la cronología entera."""
    return sorted(set(re.findall(r"^\s*tag:\s*(.+?)\s*$", contenido, re.M)))


def partir(texto):
    """(título, descripción). Acepta 'Título | descripción'; si no, la primera oración es el
    título y el resto la descripción. No REDACTA nada: reparte lo que ella escribió, porque
    inventarle prosa sería poner copy suyo en su web sin que lo haya escrito."""
    if "|" in texto:
        titulo, _, desc = texto.partition("|")
        return titulo.strip(), desc.strip() or titulo.strip()
    m = re.match(r"\s*(.+?[.!?])\s+(.+)", texto.strip(), re.S)
    if m:
        return m.group(1).strip().rstrip(".!?"), m.group(2).strip()
    limpio = texto.strip().rstrip(".!?")
    return limpio, texto.strip()


RE_URL = re.compile(r"https?://\S+")


def _entrada_yaml(texto, fecha, tag, link=None):
    """Una entrada del timeline, con el formato exacto del fichero: bloque folded (`>`) en la
    descripción y `highlight: false`.

    `highlight` nace en false a propósito: destacar una entrada es decisión editorial de {{TITULAR}}
    (lo marcó `diseno`), y un script que se auto-destaca acabaría con una cronología toda en
    negrita, que es lo mismo que ninguna."""
    titulo, desc = partir(texto)
    sangrado = "      "
    cuerpo = "\n".join(sangrado + l.strip() for l in _envolver(desc, 92))
    salida = ("  - date: '%s'\n    tag: %s\n    title: %s\n    description: >\n%s\n"
              "    highlight: false\n" % (fecha, tag, titulo, cuerpo))
    if link:
        # El fichero ya usa este par para enlazar a X y a prensa; se copia su convención.
        etiqueta = "Leerlo en X →" if "x.com" in link or "twitter.com" in link else "Ver más →"
        salida += "    link: %s\n    linkLabel: '%s'\n" % (link, etiqueta)
    return salida + "\n"


def _envolver(texto, ancho):
    palabras, linea, out = texto.split(), "", []
    for p in palabras:
        if len(linea) + len(p) + 1 > ancho:
            out.append(linea)
            linea = p
        else:
            linea = (linea + " " + p).strip()
    if linea:
        out.append(linea)
    return out or [texto]


_MES_N = {m: i + 1 for i, m in enumerate(MESES)}


def orden_fecha(txt):
    """Ordena las fechas humanas del fichero ('2021', '31 oct 2023', '8 jul 2026').

    Hace falta porque la cronología va ascendente y una entrada RETROACTIVA —como la biopsia de
    julio publicada en septiembre— acababa al final, detrás de hechos posteriores. Una cronología
    desordenada se ve rota a simple vista. Lo que no sabe parsear va al final, nunca en medio:
    colocar a ciegas sería peor que dejarlo visible."""
    t = (txt or "").strip().strip("'\"").lower()
    m = re.match(r"(?:(\d{1,2})\s+)?([a-zá-ú]{3,})?\s*(\d{4})", t)
    if not m:
        return (9999, 99, 99)
    dia, mes, anio = m.group(1), m.group(2), m.group(3)
    return (int(anio), _MES_N.get((mes or "")[:3], 99), int(dia or 0))


def _insertar_ordenado(actual, bloque, fecha):
    """Mete el bloque donde le toca por fecha, sin tocar ni reformatear el resto."""
    partes = re.split(r"\n(?=  - date:)", actual)
    cabeza, entradas = partes[0], partes[1:]
    nueva = orden_fecha(fecha)
    pos = len(entradas)
    for i, e in enumerate(entradas):
        m = re.match(r"  - date:\s*(.+)", e)
        if m and orden_fecha(m.group(1)) > nueva:
            pos = i
            break
    entradas.insert(pos, bloque.strip("\n"))
    return cabeza.rstrip("\n") + "\n\n" + "\n\n".join(e.strip("\n") for e in entradas) + "\n"


def mover_entrada(ruta, titulo):
    """Recoloca UNA entrada por su fecha, dejando el resto exactamente donde está.

    Es a propósito mucho más tímido que reordenar el fichero: el primer intento ordenaba las 30
    entradas de golpe y mandó al final las fechas con rango («feb–abr 2024», «8–9 abr 2026»),
    que este parser no entiende. Tocar lo que no sabes leer es peor que dejar una entrada
    descolocada. Devuelve True si la movió."""
    with open(ruta, encoding="utf-8") as f:
        actual = f.read()
    partes = re.split(r"\n(?=  - date:)", actual)
    cabeza, entradas = partes[0], partes[1:]
    idx = next((i for i, e in enumerate(entradas) if ("title: " + titulo) in e), None)
    if idx is None:
        raise RuntimeError("no encuentro la entrada %r" % titulo)
    bloque = entradas.pop(idx)
    m = re.match(r"  - date:\s*(.+)", bloque)
    mia = orden_fecha(m.group(1)) if m else (9999, 99, 99)
    if mia == (9999, 99, 99):
        raise RuntimeError("no sé leer la fecha de esa entrada; no la muevo a ciegas")
    pos = len(entradas)
    for i, e in enumerate(entradas):
        me = re.match(r"  - date:\s*(.+)", e)
        clave = orden_fecha(me.group(1)) if me else None
        # solo comparo con vecinas cuya fecha SÍ entiendo; las que no, se quedan donde están
        if clave and clave != (9999, 99, 99) and clave > mia:
            pos = i
            break
    entradas.insert(pos, bloque)
    nuevo = cabeza.rstrip("\n") + "\n\n" + "\n\n".join(e.strip("\n") for e in entradas) + "\n"
    if nuevo == actual:
        return False
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(nuevo)
    return True


def _escribir(worktree, texto, fecha, tag="Divulgación", link=None):
    """Añade la entrada al FINAL: el timeline va en orden cronológico ascendente, así que lo
    nuevo va abajo. Inserción quirúrgica, sin `yaml.dump`: una reescritura del fichero cambiaría
    el formato de las 23 entradas que ya están publicadas y se comería sus comentarios."""
    ruta = os.path.join(worktree, REL_ES)
    if not os.path.exists(ruta):
        raise RuntimeError("no encuentro %s — el timeline debería existir ya" % REL_ES)
    with open(ruta, encoding="utf-8") as f:
        actual = f.read()
    if CLAVE not in actual:
        raise RuntimeError("%s no tiene la clave `%s`; no lo toco a ciegas" % (REL_ES, CLAVE))
    disponibles = tags_existentes(actual)
    if disponibles and tag not in disponibles:
        raise RuntimeError("el tag %r no existe en la cronología (hay: %s). No invento taxonomía."
                           % (tag, ", ".join(disponibles)))
    contenido = _insertar_ordenado(actual, _entrada_yaml(texto, fecha, tag, link), fecha)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta, False


RE_FECHA = re.compile(r"@(\d{1,2}\s+(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)"
                      r"(?:\s+\d{4})?)\s*", re.I)


def publicar(texto, *, dry=False, tag=None, fecha=None):
    """Devuelve un dict con lo que ha pasado. No lanza: los fallos se cuentan, no se esconden."""
    # `@9 sep` → el hito se data cuando PASÓ, no cuando se publica. Sin esto, la primera entrada
    # real salió con la fecha de hoy para un hecho de once días antes (20-sep-26).
    m = RE_FECHA.search(texto)
    if m:
        fecha = m.group(1).strip()
        if not re.search(r"\d{4}$", fecha):
            fecha += " %d" % datetime.date.today().year
        texto = RE_FECHA.sub("", texto, count=1).strip()
    fecha = fecha or fecha_humana()
    # «#Equipo ya somos cuatro» → tag Equipo. Así ella elige la etiqueta sin aprenderse una CLI.
    # Los tags de dos palabras («Tercera línea», «Ensayos clínicos») se escriben con guion bajo:
    # `#Ensayos_clínicos`. Sin eso, el parser cortaba en el primer espacio y proponía un tag que
    # no existe — pasó en la primera publicación real (20-sep-26).
    m = re.match(r"\s*#([\w][\wáéíóúñÁÉÍÓÚÑ_]*)\s+(.+)", texto, re.S)
    if m:
        tag, texto = m.group(1).replace("_", " ").strip(), m.group(2).strip()
    tag = tag or "Divulgación"
    link = None
    m = RE_URL.search(texto)
    if m:                       # si pega el enlace en el mensaje, va al campo `link`, no al cuerpo
        link = m.group(0).rstrip(".,;)")
        texto = RE_URL.sub("", texto).strip()
    fallos = web_lint.revisar(texto)
    titulo, _desc = partir(texto)
    res = {"texto": texto, "fecha": fecha, "tag": tag, "titulo": titulo, "link": link,
           "publicable": not fallos,
           "fallos": [{"clase": c, "motivo": m} for c, m in fallos]}

    if dry:
        res["accion"] = "publicaría sola" if not fallos else "pararía y avisaría"
        return res

    if not os.path.isdir(WEB):
        res["error"] = "no encuentro el repo de la web en %s" % WEB
        return res

    rama = "auto/novedad-%s-%s" % (datetime.date.today().isoformat(), _slug(texto))
    wt = tempfile.mkdtemp(prefix="webnov-")
    try:
        _git("fetch", "origin", BASE)
        # worktree desde origin/main: el árbol de {{TITULAR}} no se toca ni se lee
        _git("worktree", "add", "-b", rama, wt, "origin/" + BASE)
        ruta, era_nuevo = _escribir(wt, texto, fecha, tag, link)
        _git("add", os.path.relpath(ruta, wt), cwd=wt)
        asunto = "[auto] cronología: %s" % titulo[:60]
        cuerpo = ("Publicado por tools/web_novedad.py tras pasar tools/web_lint.py.\n"
                  "Deshacer: python3 tools/web_revertir.py --ultimo\n")
        _git("commit", "-q", "-m", asunto, "-m", cuerpo, cwd=wt)
        _git("push", "-q", "origin", rama, cwd=wt)
        res.update(rama=rama, fichero_nuevo=era_nuevo)

        pr = subprocess.run(
            ["gh", "pr", "create", "--base", BASE, "--head", rama, "--title", asunto,
             "--body", cuerpo + ("\nLint: limpio." if not fallos else
                                 "\n⚠️ El lint ha parado esto:\n" +
                                 "\n".join("- [%s] %s" % (c, m) for c, m in fallos))],
            cwd=wt, capture_output=True, text=True, timeout=180)
        res["pr"] = (pr.stdout or pr.stderr).strip().splitlines()[-1] if pr.stdout or pr.stderr else ""

        suyo, origen = lo_pide_titular()
        res["pedido_por_titular"] = suyo
        if not suyo:
            res["motivo_no_merge"] = origen
        if not fallos and suyo:
            m = subprocess.run(["gh", "pr", "merge", "--squash", "--delete-branch", rama],
                               cwd=wt, capture_output=True, text=True, timeout=180)
            res["merged"] = m.returncode == 0
            if m.returncode != 0:
                res["merge_error"] = (m.stderr or m.stdout).strip()[:300]
        else:
            res["merged"] = False
    except Exception as e:
        res["error"] = str(e)[:400]
    finally:
        subprocess.run(["git", "-C", WEB, "worktree", "remove", "--force", wt],
                       capture_output=True, timeout=60)
    _registrar("publicar", **{k: v for k, v in res.items() if k != "texto"})
    return res


def estado():
    """PRs automáticos abiertos: lo que el freno paró y sigue esperando a {{TITULAR}}."""
    r = subprocess.run(["gh", "pr", "list", "--search", "[auto] in:title", "--state", "open",
                        "--json", "number,title,url,createdAt"],
                       cwd=WEB, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(r.stdout or "[]")
    except Exception:
        return []


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    args = [a for a in args if not a.startswith("--")]
    if "--estado" in sys.argv:
        abiertos = estado()
        if not abiertos:
            print("no hay novedades automáticas esperando.")
            return 0
        print("%d PR(s) automáticos abiertos (el lint los paró):" % len(abiertos))
        for p in abiertos:
            print("  · #%s %s\n    %s" % (p["number"], p["title"], p["url"]))
        return 0
    if not args:
        print(__doc__)
        return 2

    res = publicar(" ".join(args), dry=dry)
    if res.get("error"):
        print("⛔ %s" % res["error"], file=sys.stderr)
        return 2
    if dry:
        print("(dry) %s" % res["accion"])
    if not res["publicable"]:
        print("🟡 NO se publica sola. El lint ha parado esto:")
        for f in res["fallos"]:
            print("   · [%s] %s" % (f["clase"], f["motivo"]))
        if res.get("pr"):
            print("   Queda en PR para {{TITULAR}}: %s" % res["pr"])
        return 1
    if dry:
        return 0
    if res.get("merged"):
        print("✅ publicado en la cronología. Deshacer: python3 tools/web_revertir.py --ultimo")
        return 0
    if not res.get("pedido_por_titular"):
        print("🟡 El lint dio verde, pero esto no lo ha pedido {{TITULAR}} en un mensaje (%s)."
              % res.get("motivo_no_merge", "?"))
        print("   Queda en PR, sin publicar: %s" % res.get("pr", "?"))
        print("   Es el freno de quién, no el de qué. Si lo pide ella, sale solo.")
        return 1
    print("🟡 lint limpio y pedido por ella, pero el merge no salió (%s). PR: %s"
          % (res.get("merge_error", "?"), res.get("pr", "?")))
    return 1


if __name__ == "__main__":
    sys.exit(main())
