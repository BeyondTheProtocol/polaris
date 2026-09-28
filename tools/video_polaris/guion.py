"""Guion del vídeo por idioma y anclas de palabra (lo comparten voz, palabras, montaje y sfx).

guion_voz.json es el EN firmado; guion_voz_<idioma>.json, cada traducción (28-sep: ES).
El montaje busca palabras por prefijo («polaris», «two», «takes»…). Esas claves son las del EN;
cada guion traducido lleva un mapa "anclas" {clave_en: prefijo_en_su_idioma}. Sin mapa, la clave
es el prefijo (EN). Remotion recibe el mismo mapa en timeline.json, así que la fuente es una sola.
"""
import json, os, re, unicodedata

AQUI = os.path.dirname(os.path.abspath(__file__))


def ruta(idioma="en"):
    return os.path.join(AQUI, "guion_voz.json" if idioma == "en" else f"guion_voz_{idioma}.json")


def cargar(idioma="en"):
    g = json.load(open(ruta(idioma), encoding="utf-8"))
    g.setdefault("idioma", idioma)
    g.setdefault("anclas", {})
    return g


def norm(s):
    """minúsculas sin tildes ni signos: «¿Médicos,» → «medicos»; «sign-off» conserva el guion."""
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[^a-z0-9-]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def ancla(g, clave):
    return g.get("anclas", {}).get(clave, clave)


def idioma_de_args(argv):
    """--idioma es|en (por defecto en) → (idioma, resto de argumentos)."""
    if "--idioma" in argv:
        i = argv.index("--idioma")
        return argv[i + 1], argv[:i] + argv[i + 2:]
    return "en", argv
