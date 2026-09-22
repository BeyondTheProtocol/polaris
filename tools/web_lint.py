#!/usr/bin/env python3
"""web_lint.py — el freno del contenido que se publica SOLO en la web.

POR QUÉ EXISTE (20-sep-2026). {{TITULAR}} quiere que la web se actualice sola, sin abrir una sesión.
Eso solo puede existir si hay algo que decida, sin criterio humano en el momento, qué NO puede
salir. Este es ese algo, y todo lo demás del carril automático depende de él.

QUÉ ES DISTINTO DE `borde.clasificar()`
  `borde` decide si un texto puede salir a un TERCERO (otro modelo, un buscador). Aquí la pregunta
  es otra: si puede publicarse **en la web de {{TITULAR}}**, que es suya y lleva su nombre. Por eso:
  · se reutilizan sus detectores de clínico/genómico y de identificadores duros (una sola fuente
    de verdad para los patrones: si mejoran allí, mejoran aquí);
  · **NO** se hereda el bloqueo por «nombre de {{TITULAR}}» — en su propia web su nombre es lo normal,
    y heredarlo haría que el lint bloqueara absolutamente todo;
  · se añade lo que `borde` no mira porque a un buscador no le importa: **dosis de fármacos**,
    léxico de marca y los *tells* de IA.

QUÉ MIRA (todo fail-closed: ante la duda, NO se publica solo)
  1. clínico y genómico — vía `borde` (genes, HGVS, variantes, marcadores, cifras).
  2. **dosis y pauta** — `borde` no lo caza y es justo lo que no puede salir sin un médico
     detrás: verificado el 20-sep-26, «abemaciclib a 100 mg diarios» le pasaba como limpio.
  3. identificadores duros — email, DNI, teléfono, historia clínica.
  4. terceros — médicos e instituciones nombrados sin permiso.
  5. léxico de marca (`.claude/rules/marca-copy.md`) — nunca «{{CONTACTO}}»; nunca «ingeniera»
     (es «ingeniera» + lo que construye).
  6. voz — los *tells* de IA que ningún filtro de palabras caza y que el comité `diseno` pidió
     mirar: guion largo de muletilla, tricolon, la antítesis «no es X, es Y».

Uso:
  python3 tools/web_lint.py "texto de la novedad"      # rc=0 publicable · rc=1 no
  python3 tools/web_lint.py --fichero <ruta>           # audita un fichero de contenido
  python3 tools/web_lint.py --json "…"
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import borde  # noqa: E402 — se reutilizan SUS patrones; no se copian aquí

# ── lo que `borde` no mira (a un buscador no le importa; a la web pública, sí) ───────────────

# Dosis y pauta. Publicar «X a 100 mg» es información de tratamiento sin médico detrás, y encima
# invita a que alguien se la aplique. Verificado el 20-sep-26: `borde.clasificar()` la deja pasar.
RE_DOSIS = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|ug|g|ml|ui|u\.?i\.?|mg/m2|mg/kg|gy|gray)\b"
    r"|\b(?:cada|c/)\s?\d+\s?(?:h|horas|d[ií]as|semanas)\b"
    r"|\b(?:\d+|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|doce)\s?"
    r"(?:comprimidos?|c[áa]psulas?|ciclos?|sesiones?|infusiones?|dosis)\b", re.I)

# Nombres de fármaco junto a una cifra: «abemaciclib 100», «letrozol 2,5». El sufijo farmacológico
# es más robusto que una lista cerrada de principios activos, que envejece mal.
RE_FARMACO = re.compile(
    r"\b\w{4,}(?:ciclib|tinib|mab|parib|mustina|platino|taxel|rubicina|zolomida|lutine|"
    r"trexato|fosfamida|citabina)\b\W{0,12}\d", re.I)

# Terceros: un médico o un centro nombrado en público necesita su permiso, no el nuestro.
RE_TERCEROS = re.compile(
    r"\b(dr\.?|dra\.?|doctor[a]?|prof\.?)\s+[A-ZÁÉÍÓÚÑ]\w+"
    r"|\b(vall\s?d.?hebron|{{CENTRO}}|dana.?farber|md\s?anderson|hospital\s+[A-ZÁÉÍÓÚÑ]\w+|"
    r"cl[íi]nica\s+[A-ZÁÉÍÓÚÑ]\w+|{{CENTRO}}|morales\s+meseguer|{{CENTRO}})\b", re.I)

# Léxico de marca (.claude/rules/marca-copy.md). «vacuna» NO está: su veto se levantó el 29-7-26.
MARCA = (
    (re.compile(r"\bcontacto\b", re.I), "«{{CONTACTO}}» no se nombra en público, nunca"),
    (re.compile(r"\bcient[íi]fic[ao]s?\b", re.I),
     "«ingeniera» atrae ataques: en público es «ingeniera» + lo que construye"),
)

# Voz. Lo que pidió `diseno`: un lint de palabras no caza la FORMA.
VOZ = (
    (re.compile(r"\w\s—\s\w"), "guion largo de muletilla: en español es un tell de IA fuerte"),
    (re.compile(r"\bno\s+(?:es|son|va\s+de)\s+(?:solo\s+)?[^,.;]{2,40}[,;]\s*(?:es|son|va\s+de)\s+",
                re.I), "antítesis «no es X, es Y»: suena a IA"),
    (re.compile(r"\b(\w+),\s+(\w+)\s+y\s+(\w+)\.", re.I), "tricolon decorativo"),
)

MIN_LARGO = 15


def _terminos_web(repo=None):
    """Términos que la web YA usa (i18n). Sirve para no inventar un sinónimo nuevo de algo que el
    sitio nombra de otra forma — lo pidió `diseno`: «biopsia líquida» donde el resto dice otra cosa
    rompe la regla de una etiqueta, un término. Si no se encuentra el fichero, esta capa se omite
    en silencio: es coherencia, no seguridad."""
    ruta = os.path.join(repo or "/Users/polaris/projects/titular-{{APELLIDO}}-case",
                        "i18n", "locales", "es.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            crudo = f.read().lower()
        return crudo
    except Exception:
        return None


def revisar(texto, *, repo=None):
    """[(clase, motivo)] — vacío significa publicable. Fail-closed por diseño."""
    fallos = []
    if not isinstance(texto, str) or len(texto.strip()) < MIN_LARGO:
        return [("formato", "texto vacío o demasiado corto para publicarse")]

    # 1-3. clínico, genómico e identificadores: los patrones son de `borde`, no se duplican.
    norm, _low = borde._normalizar(texto)
    es_clin, etiqueta = borde._clinico_o_genomico(norm)
    if es_clin:
        fallos.append(("clinico", "dato clínico/genómico (%s)" % etiqueta))
    for rx, etq in borde._ID_DURO:
        if rx.search(norm):
            fallos.append(("pii", "identificador directo (%s)" % etq))

    # 2-bis. dosis y fármacos: el hueco real de `borde`.
    m = RE_DOSIS.search(texto) or RE_FARMACO.search(texto)
    if m:
        fallos.append(("clinico", "dosis o pauta de tratamiento («%s»)" % m.group(0).strip()))

    # 4. terceros
    m = RE_TERCEROS.search(texto)
    if m:
        fallos.append(("terceros", "médico o institución nombrados («%s»)" % m.group(0).strip()))

    # 5. marca
    for rx, motivo in MARCA:
        if rx.search(texto):
            fallos.append(("marca", motivo))

    # 6. voz
    for rx, motivo in VOZ:
        if rx.search(texto):
            fallos.append(("voz", motivo))

    return fallos


def publicable(texto, *, repo=None):
    return not revisar(texto, repo=repo)


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if args and args[0] == "--fichero":
        if len(args) < 2:
            print("uso: web_lint.py --fichero <ruta>", file=sys.stderr)
            return 2
        with open(args[1], encoding="utf-8") as f:
            texto = f.read()
        etiqueta = args[1]
    elif args:
        texto, etiqueta = " ".join(args), "(texto)"
    else:
        print(__doc__)
        return 2

    fallos = revisar(texto)
    if as_json:
        print(json.dumps({"publicable": not fallos,
                          "fallos": [{"clase": c, "motivo": m} for c, m in fallos]},
                         ensure_ascii=False, indent=2))
        return 1 if fallos else 0
    if not fallos:
        print("✅ publicable sin intervención.")
        return 0
    print("⛔ NO se publica solo — %s:\n" % etiqueta)
    for clase, motivo in fallos:
        print("  · [%s] %s" % (clase, motivo))
    print("\nEsto queda en rama y PR para que lo mire {{TITULAR}}. No es un error: es el freno.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
