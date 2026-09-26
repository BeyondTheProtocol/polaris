#!/usr/bin/env python3
"""tools/fichas.py — una ficha por pieza de `tools/`: para qué sirve, cuándo sí, cuándo no, qué
reemplaza y cuál es su test. Sin ficha no se da de alta una herramienta.

Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026.

POR QUÉ (25-sep-2026, problema nº2 de `docs/lo-que-falta.md`). El catálogo crecía sin que nadie
supiera qué había: los documentos decían 189 herramientas cuando ya eran 250, y una herramienta
que nadie encuentra se reescribe, y entonces hay dos. La ficha obliga a mirar antes de crear y
deja escrito qué hace cada pieza, para que la siguiente sesión amplíe en vez de duplicar.

DÓNDE. `tools/fichas/<pieza>.json`, un fichero por pieza (`hooks/instalar.sh` →
`hooks__instalar.sh.json`). Uno por fichero y no un JSON gordo a propósito: con muchas sesiones en
paralelo dando de alta herramientas, un fichero único chocaría en cada fusión.

QUÉ NO HAY AQUÍ: el uso. El uso lo mide `inventario.py --uso` con trazas reales y va a
`tools/state/uso_piezas.json`; nunca lo escribe un modelo a mano.

LAS HEREDADAS. Las 250 piezas que ya existían el 25-sep recibieron una ficha `semilla` (el «para»
sacado de su docstring). Solo ellas pueden quedarse en semilla, y la lista `_heredadas.txt` solo
puede encoger: al completar una ficha, se quita de la lista. Una pieza nueva nace con ficha
completa.

Uso:
  python3 tools/fichas.py comprobar            # errores de fichas; exit 1 si hay alguno
  python3 tools/fichas.py sembrar              # crea fichas semilla para lo que no tenga (solo heredadas)
  python3 tools/fichas.py parecidas <nombre>   # las 5 piezas más parecidas a un nombre nuevo
  python3 tools/fichas.py plantilla <pieza>    # imprime una ficha vacía para rellenar
"""
import datetime
import json
import os
import re
import sys

RAIZ = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMPOS = ("para", "cuando_si", "cuando_no", "reemplaza", "test", "alta", "origen")
# Carpetas de tools/ que no son piezas: estado vivo, lo retirado, las propias fichas y activos.
NO_PIEZA = {"state", "retired", "fichas", "__pycache__", "fonts", "templates", "visor3d_web",
            "visor_video_x", "config", "personas", "node_modules"}
EXT = (".py", ".sh")
AVISO = "busca antes de crear; si hay algo parecido, amplíalo"


def dir_fichas(raiz=RAIZ):
    return os.path.join(raiz, "tools", "fichas")


def es_pieza(rel):
    """`rel` relativo a tools/. ¿Cuenta como pieza del catálogo?"""
    partes = rel.replace(os.sep, "/").split("/")
    if any(p in NO_PIEZA for p in partes[:-1]):
        return False
    nombre = partes[-1]
    return nombre.endswith(EXT) and not nombre.startswith(("_", "test_"))


def piezas(raiz=RAIZ):
    """Todas las piezas de tools/ (rutas relativas a tools/, con `/`)."""
    base = os.path.join(raiz, "tools")
    out = []
    for d, subdirs, files in os.walk(base):
        subdirs[:] = sorted(s for s in subdirs if s not in NO_PIEZA and not s.startswith("."))
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/")
            if es_pieza(rel):
                out.append(rel)
    return sorted(out)


def ruta_ficha(rel, raiz=RAIZ):
    return os.path.join(dir_fichas(raiz), rel.replace("/", "__") + ".json")


def leer(rel, raiz=RAIZ):
    try:
        with open(ruta_ficha(rel, raiz), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def heredadas(raiz=RAIZ):
    try:
        with open(os.path.join(dir_fichas(raiz), "_heredadas.txt"), encoding="utf-8") as fh:
            return {l.strip() for l in fh if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


def problemas_de(ficha, rel, raiz=RAIZ, hered=None, mirar_test=True):
    """Lo que le falta a UNA ficha. Lista vacía = vale."""
    if ficha is None:
        return ["%s: sin ficha (%s)" % (rel, os.path.relpath(ruta_ficha(rel, raiz), raiz))]
    hered = heredadas(raiz) if hered is None else hered
    estado = ficha.get("estado_ficha")
    if ficha.get("pieza") != rel:
        return ["%s: la ficha dice pieza=%r" % (rel, ficha.get("pieza"))]
    if estado == "semilla":
        if rel not in hered:
            return ["%s: ficha semilla, pero no es heredada: una pieza nueva nace con ficha "
                    "completa (%s)" % (rel, ", ".join(CAMPOS))]
        return [] if (ficha.get("para") or "").strip() else ["%s: semilla sin «para»" % rel]
    if estado != "completa":
        return ["%s: estado_ficha=%r (vale semilla|completa)" % (rel, estado)]
    fuera = ["%s: falta «%s»" % (rel, c) for c in CAMPOS if not str(ficha.get(c) or "").strip()]
    if not fuera and mirar_test and not os.path.isfile(os.path.join(raiz, ficha["test"])):
        fuera.append("%s: su test %s no existe" % (rel, ficha["test"]))
    return fuera


def comprobar(raiz=RAIZ):
    """Todos los problemas del catálogo: piezas sin ficha, fichas malas, fichas sin pieza,
    y heredadas que ya no hacen falta en la lista (la lista solo encoge)."""
    hered = heredadas(raiz)
    todas = piezas(raiz)
    fuera = []
    for rel in todas:
        fuera += problemas_de(leer(rel, raiz), rel, raiz, hered)
    vivas = {rel.replace("/", "__") + ".json" for rel in todas}
    d = dir_fichas(raiz)
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if f.endswith(".json") and f not in vivas:
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    retirada = json.load(fh).get("estado_ficha") == "retirada"
            except (OSError, ValueError):
                retirada = False
            if not retirada:
                fuera.append("ficha sin pieza: tools/fichas/%s" % f)
    for rel in sorted(hered):
        fi = leer(rel, raiz)
        if rel not in todas or (fi and fi.get("estado_ficha") != "semilla"):
            fuera.append("_heredadas.txt: sobra %s (ya no es semilla o no existe): quítala" % rel)
    return fuera


# ─── siembra ─────────────────────────────────────────────────────────────────

def _primera_linea(ruta):
    """La primera frase útil del docstring (.py) o del comentario de cabecera (.sh)."""
    try:
        with open(ruta, encoding="utf-8", errors="replace") as fh:
            lineas = fh.read(6000).splitlines()
    except OSError:
        return ""
    nombre = os.path.basename(ruta)
    for l in lineas:
        t = l.strip().strip('"\'').strip()
        if ruta.endswith(".sh"):
            if not l.startswith("#") or l.startswith("#!"):
                continue
            t = l.lstrip("#").strip()
        elif l.startswith("#") or not t or t.startswith(("import ", "from ")):
            continue
        t = re.sub(r"^(tools/)?[\w/.-]*%s\s*[—:-]+\s*" % re.escape(nombre), "", t).strip()
        if len(t) > 8 and not t.startswith(("-*-", "coding")):
            return t[:240]
    return ""


def sembrar(raiz=RAIZ, hoy=None):
    """Crea fichas semilla para las piezas heredadas que no tengan. No toca las que existen."""
    hoy = hoy or datetime.date.today().isoformat()
    hered = heredadas(raiz)
    os.makedirs(dir_fichas(raiz), exist_ok=True)
    creadas = []
    for rel in piezas(raiz):
        if rel not in hered or os.path.exists(ruta_ficha(rel, raiz)):
            continue
        modulo = os.path.splitext(os.path.basename(rel))[0]
        test = "tests/test_%s.py" % modulo
        ficha = {"pieza": rel, "estado_ficha": "semilla",
                 "para": _primera_linea(os.path.join(raiz, "tools", rel)) or "(sin docstring)",
                 "cuando_si": "", "cuando_no": "", "reemplaza": "",
                 "test": test if os.path.isfile(os.path.join(raiz, test)) else "",
                 "alta": "heredada, ficha sembrada el %s" % hoy, "origen": ""}
        with open(ruta_ficha(rel, raiz), "w", encoding="utf-8") as fh:
            json.dump(ficha, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        creadas.append(rel)
    return creadas


def plantilla(rel):
    return {"pieza": rel, "estado_ficha": "completa", "para": "", "cuando_si": "",
            "cuando_no": "", "reemplaza": "", "test": "tests/test_%s.py" %
            os.path.splitext(os.path.basename(rel))[0],
            "alta": datetime.date.today().isoformat(), "origen": ""}


# ─── parecidas: el «busca antes de crear» ───────────────────────────────────

def parecidas(texto, raiz=RAIZ, n=5):
    """Las n piezas más parecidas a un nombre o descripción: [(pieza, para)].
    La puntuación es la de `capacidades.py` (IDF sobre las fichas), no una segunda copia."""
    sys.path.insert(0, os.path.join(raiz, "tools"))
    import capacidades
    q = re.sub(r"\.(py|sh)$", "", texto).replace("_", " ").replace("/", " ")
    return [(pieza, para) for _, pieza, para in capacidades.buscar_herramientas(q, n, raiz)]


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "comprobar":
        fuera = comprobar()
        for f in fuera:
            print("  ❌ " + f)
        print("%s %d pieza(s), %d problema(s)" % ("✅" if not fuera else "❌",
                                                   len(piezas()), len(fuera)))
        return 1 if fuera else 0
    if cmd == "sembrar":
        c = sembrar()
        print("🌱 %d ficha(s) semilla creadas" % len(c))
        return 0
    if cmd == "parecidas" and len(argv) > 1:
        for rel, para in parecidas(" ".join(argv[1:])):
            print("   %-32s %s" % (rel, para[:90]))
        return 0
    if cmd == "plantilla" and len(argv) > 1:
        print(json.dumps(plantilla(argv[1]), ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
