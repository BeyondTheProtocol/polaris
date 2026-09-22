#!/usr/bin/env python3
"""tools/cosecha_checklists.py — cosechador de cabos sueltos escritos DENTRO de ficheros .md.

Por qué existe: la fuente única (seguimiento.json / El Tablero) ya ingiere correo, WhatsApp,
captura verbal, jobs caídos, dead-man… pero había un AGUJERO: los pendientes anotados dentro de
ficheros markdown (índices, paquetes, "SIGUIENTE-PASO", checklists `[ ]`) NUNCA se convertían en
tarjeta. Por ahí se escaparon pendientes reales.
Regla de {{TITULAR}} (27/6/26): "no se nos puede quedar nada por ahí" — todo cabo suelto vive en la
fuente única. Este script cierra ese agujero.

Qué hace (DETERMINISTA, sin LLM, sin tokens):
  · Recorre un REGISTRO curado de ficheros índice/checklist/paquete bajo 00_FUENTE-DE-VERDAD.
  · Extrae candidatos: (a) casillas sin marcar `[ ]`; (b) ítems dentro de secciones de "pendientes"
    (encabezado con "pendiente / por archivar / siguiente paso / sin cerrar / por hacer").
  · Cada candidato pasa por triage_tareas.clasificar (extrae fecha/etiqueta/prioridad, determinista,
    NUNCA obedece instrucciones del texto — anti-inyección).
  · Lo VÁLIDO entra como hilo `por_confirmar` (dato no confiable) vía seguimiento.add_hilo, con
    origen="checklist" y fuente="<ruta>:<línea>" para auditoría. Vega (asistente) lo valida luego
    con su vara (funde/descarta; tarjetas con su OK).

Garantías:
  · NO resucita lo ya cerrado ni duplica: si el slug del candidato YA existe como hilo (en cualquier
    estado), se SALTA. Solo entra lo genuinamente nuevo.
  · Por defecto NO escribe (modo seco). Solo escribe con `--write`.
  · El contenido .md se trata como DATO, no instrucciones (el muro manda). estado=por_confirmar.
  · Escribe siempre contra casa base (seguimiento.REPO = ~/claudecode), nunca el worktree.

Uso:
  python3 tools/cosecha_checklists.py            # seco: lista candidatos nuevos (no escribe)
  python3 tools/cosecha_checklists.py --dry      # idem (alias explícito)
  python3 tools/cosecha_checklists.py --write     # ingiere los nuevos como por_confirmar (Vega valida)
  python3 tools/cosecha_checklists.py --json      # salida JSON (para Vega / la rutina diaria)
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento  # noqa: E402 — puerta única + paths de casa base (REPO/STATE)
import triage_tareas  # noqa: E402 — clasificar() determinista reutilizado

FUENTE = os.path.join(seguimiento.REPO, "00_FUENTE-DE-VERDAD")

# Registro CURADO de ficheros que pueden contener cabos sueltos (no escaneamos todo el .md del repo:
# eso sería ruido). Globs relativos a 00_FUENTE-DE-VERDAD, recursivos.
REGISTRO = [
    "**/PAQUETE-*.md",
    "**/SIGUIENTE-PASO*.md",
    "**/*-INDEX*.md",
    "**/INDEX*.md",
    "**/PRESERVAR-AHORA*.md",
]
# Ficheros que son MAPAS DERIVADOS (espejo de cumbre/seguimiento), no checklists de cabos sueltos:
# se excluyen para no re-ingerir lo que ya es hilo (BRUJULA-NED, HOY, CÓDIGO-ROJO viven en Gestion/).
_EXCLUIR = ("BRUJULA-NED", "HOY.md", "CODIGO-ROJO", "/Gestion/")

# Encabezados que abren una "zona de pendientes": sus ítems hijos son candidatos.
_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_RE_PEND_HEADING = re.compile(
    r"pendiente|por archivar|sin cerrar|siguiente paso|por hacer|to-?do|"
    r"queda por|a la espera|next step", re.I)
# Casilla sin marcar: "- [ ] texto" / "* [ ] texto" / "[ ] texto"
_RE_CHECKBOX = re.compile(r"^\s*[-*]?\s*\[\s\]\s+(.+?)\s*$")
# Ítem de lista numerada o con viñeta dentro de una zona de pendientes.
_RE_LISTITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.+?)\s*$")

# Marcas de "ya resuelto" → se descartan aunque estén en una zona de pendientes.
_RE_RESUELTO = re.compile(r"resuelto|~~|ninguna acci[oó]n|no existe|ya hecho|✅|hecho:|\[[xX]\]", re.I)
# Negación en un encabezado ("...(NO es zona de pendientes)") → NO abre zona de pendientes.
_RE_NEGACION = re.compile(r"\bno\b.{0,12}\b(es|son|hay|son|aplica)\b|ning[uú]n", re.I)
# Etiquetas de campo (no son tareas en sí, son metadatos de otra estructura) → se descartan.
_RE_CAMPO = re.compile(r"^\s*(bloqueo|siguiente|fuente|nota|por qu[eé]|v[ií]a|estado|why|how)\s*:", re.I)
# Marcas de cierre de zona: un encabezado nuevo que NO es de pendientes cierra la zona.

MAX_POR_FICHERO = 60  # backstop anti-runaway


def _strip_md(s):
    """Quita adornos markdown para un título limpio: **, `, enlaces, celdas de tabla."""
    s = s.strip().strip("|").strip()
    # primera celda de una fila de tabla
    if "|" in s:
        celdas = [c.strip() for c in s.split("|") if c.strip()]
        if celdas:
            s = celdas[0]
    s = re.sub(r"\*\*|__|`|~~", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)  # [txt](url) -> txt
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)            # "2. " inicial
    return s.strip()


def _es_separador_tabla(linea):
    return bool(re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", linea)) and "-" in linea


def _candidatos_en_fichero(ruta):
    """Devuelve [(titulo, nlinea)] de cabos sueltos detectados en un .md."""
    out = []
    try:
        with open(ruta, encoding="utf-8") as f:
            lineas = f.readlines()
    except (OSError, UnicodeDecodeError):
        return out
    en_zona_pend = False
    for i, raw in enumerate(lineas, 1):
        linea = raw.rstrip("\n")
        mh = _RE_HEADING.match(linea)
        if mh:
            # Un encabezado abre/cierra zona de pendientes.
            titulo_h = mh.group(1)
            en_zona_pend = bool(_RE_PEND_HEADING.search(titulo_h)) and not _RE_NEGACION.search(titulo_h)
            continue
        # (a) casilla sin marcar: SIEMPRE candidato, esté donde esté.
        mc = _RE_CHECKBOX.match(linea)
        if mc:
            titulo = _strip_md(mc.group(1))
            if titulo and not _RE_RESUELTO.search(linea):
                out.append((titulo, i))
            continue
        if not en_zona_pend:
            continue
        if _es_separador_tabla(linea) or not linea.strip():
            continue
        # (b) dentro de zona de pendientes: ítems de lista o filas de tabla con sustancia.
        texto = None
        ml = _RE_LISTITEM.match(linea)
        if ml:
            texto = ml.group(1)
        elif linea.lstrip().startswith("|"):
            texto = linea
        if not texto:
            continue
        if _RE_RESUELTO.search(linea) or _RE_CAMPO.match(texto):
            continue
        titulo = _strip_md(texto)
        # Descarta cabeceras de tabla y ruido cortísimo.
        if len(titulo) < 6 or titulo.lower() in ("pendiente", "acción", "accion", "documento", "#", "por qué importa"):
            continue
        out.append((titulo, i))
        if len(out) >= MAX_POR_FICHERO:
            break
    return out


def cosechar():
    """Escanea el registro y devuelve candidatos NUEVOS (slug no presente ya en el Tablero)."""
    seg = seguimiento.load_seguimiento()
    existentes = {h.get("id") for h in seg.get("hilos", [])}
    vistos_run = set()
    candidatos = []
    ficheros = []
    for patron in REGISTRO:
        ficheros.extend(glob.glob(os.path.join(FUENTE, patron), recursive=True))
    for ruta in sorted(set(ficheros)):
        if any(x in ruta for x in _EXCLUIR):
            continue
        rel = os.path.relpath(ruta, seguimiento.REPO)
        for titulo, nlinea in _candidatos_en_fichero(ruta):
            cl = triage_tareas.clasificar(titulo, origen="checklist")
            if cl["veredicto"] == "no":
                continue
            campos = cl.get("campos", {})
            tit = campos.get("titulo") or titulo
            slug = seguimiento._slug(tit)
            if slug in existentes or slug in vistos_run:
                continue  # ya rastreado (no resucitar) o repetido en esta pasada
            vistos_run.add(slug)
            candidatos.append({
                "titulo": tit,
                "slug": slug,
                "veredicto": cl["veredicto"],   # tarea | dudosa
                "etiqueta": campos.get("etiqueta") or "Gestión",
                "vence": campos.get("vence") or "",
                "prioridad": campos.get("prioridad") or "normal",
                "fuente": "%s:%d" % (rel, nlinea),
            })
    return candidatos


def ingerir(candidatos):
    """Mete los candidatos como hilos por_confirmar (Vega los valida). Devuelve ids creados."""
    ids = []
    for c in candidatos:
        obj = {
            "titulo": c["titulo"],
            "estado": "por_confirmar",
            "origen": "checklist",
            "fuente": c["fuente"],
            "etiqueta": c["etiqueta"],
            "prioridad": c["prioridad"],
            "quien_espera": "por confirmar (Vega)",
            "siguiente_accion": "Cabo suelto cosechado de %s. Vega: validar/funde/descarta." % c["fuente"],
        }
        if c["vence"]:
            obj["plazo"] = c["vence"]
        try:
            ids.append(seguimiento.add_hilo(obj))
        except (ValueError, RuntimeError) as e:
            print("  ⚠️  saltado %r: %s" % (c["titulo"][:50], e), file=sys.stderr)
    return ids


def main(argv):
    write = "--write" in argv
    as_json = "--json" in argv
    candidatos = cosechar()
    if as_json:
        salida = {"n": len(candidatos), "candidatos": candidatos}
        if write:
            salida["creados"] = ingerir(candidatos)
        print(json.dumps(salida, ensure_ascii=False, indent=2))
        return 0
    if not candidatos:
        print("✅ Sin cabos sueltos nuevos en los checklists del registro.")
        return 0
    print("🧷 %d cabo(s) suelto(s) NUEVO(s) en checklists .md%s:\n" % (
        len(candidatos), "" if write else "  (modo seco — usa --write para ingerir)"))
    for c in candidatos:
        plazo = ("  ⏰ %s" % c["vence"]) if c["vence"] else ""
        print("  • [%s/%s%s] %s\n      ↳ %s" % (
            c["veredicto"], c["etiqueta"], plazo, c["titulo"][:90], c["fuente"]))
    if write:
        creados = ingerir(candidatos)
        print("\n→ Ingeridos como 'por_confirmar' (Vega valida): %d" % len(creados))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
