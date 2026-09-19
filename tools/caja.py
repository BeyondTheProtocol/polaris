#!/usr/bin/env python3
"""tools/caja.py — recoge lo que dijeron N subagentes y lo entrega como UN dossier.

EL AGUJERO QUE TAPA: `paso_consolidacion.py` sabe juntar las contribuciones de varios
subagentes en un dossier priorizado y trazable, y lleva desde junio sin que nadie lo llame.
Cuando un turno lanza `comite-medico` + `verificacion`, {{TITULAR}} recibe DOS volcados y tiene
que juntarlos ella. Esto lo junta.

NO HACE FALTA QUE NADIE APUNTE NADA. El arnés ya deja en disco, por cada subagente:
    ~/.claude/projects/<proyecto>/<sesion>/subagents/agent-<id>.jsonl       ← la conversación
    ~/.claude/projects/<proyecto>/<sesion>/subagents/agent-<id>.meta.json   ← quién era
El `.meta.json` trae `agentType` (el slug del agente) y `description`; la salida del
subagente es el último bloque de texto del `.jsonl`. Recoger N salidas es leer un directorio.

DETERMINISTA: sin LLM, sin red. Solo transforma texto que ya está en tu disco.

LA FUENTE NO SE INVENTA. Si un subagente no cierra con el bloque JSON del contrato, su
contribución entra en modo degradado y sin fuente — y `paso_consolidacion` la marca y
devuelve rc=1. Eso es el diseño funcionando, no un fallo: una decisión sin fuente trazable
tiene que picar.

Uso:
  python3 tools/caja.py cosecha [--sesion <id|ruta>] [--desde <epoch>] [--json]
  python3 tools/caja.py dossier [--sesion <id|ruta>] [--desde <epoch>] [--caja <slug> | --intencion "..."]
  python3 tools/caja.py convocar --caja <slug>   # sienta al dueño y a los expertos del charter
  python3 tools/caja.py sesiones            # las últimas sesiones con subagentes en disco
"""
import argparse
import glob
import io
import json
import os
import re
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)

import paso_consolidacion  # noqa: E402  — el consolidador, como MÓDULO (no por subprocess)

PROJECTS = os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")

# Lo que el contrato le pide a cada asiento. Se busca el ÚLTIMO bloque del texto: si el
# agente razona en voz alta y cierra con su JSON, gana el cierre.
RE_BLOQUE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)

# ─── el contrato, para que VIAJE con cada asiento (13-sep-2026) ──────────────────────────
# El paso 2 metió el contrato en las órdenes de `decide_peticion`, pero los subagentes que se lanzan
# desde el chat no pasan por ahí: medido sobre el último panel real, 0 decisiones y los dos asientos
# sin bloque JSON. `.claude/hooks/contrato_asiento.py` añade este texto al prompt de cada asiento.
#
# Solo a los asientos de ANÁLISIS (los que la tabla de enrutado sienta en un panel, más los
# consejeros y el oncólogo virtual). NO a los de redacción (voz-titular, redes-contenido, prensa,
# comunidad), que devuelven un borrador y un JSON al final lo ensuciaría, ni a los operativos
# (git, tecnico, conserje-web…), cuyo trabajo no son decisiones.
ASIENTOS_CON_CONTRATO = frozenset({
    "comite-medico", "verificacion", "oncologo-virtual",
    "consejero-acceso", "consejero-arquitectura", "consejero-arneses", "consejero-marketing",
    "legal-burocracia", "finanzas-transparencia", "diseno",
})
MARCA_CONTRATO = "CONTRATO DE ASIENTO (la caja)"
PLANTILLA_CONTRATO = ('{"decisiones": [{"titulo": "", "impacto": "critica|alta|media", '
                      '"recomendacion": "", "porque": "", "fuente": ""}], '
                      '"acciones": [{"texto": "", "limite": "", "fuente": ""}]}')
CONTRATO_ASIENTO = (
    "\n\n---\n📋 " + MARCA_CONTRATO + ": cierra tu respuesta con UN bloque ```json "
    "(máximo 3 decisiones) con esta forma:\n" + PLANTILLA_CONTRATO + "\n"
    "`fuente` = de dónde lo sabes: fichero, URL que hayas abierto, PMID/NCT, o «mi criterio». "
    "Si no hay decisiones que tomar, devuelve las listas vacías. Sin ese bloque tu trabajo entra "
    "degradado y sin fuente en el dossier que recibe {{TITULAR}}."
)


# ─── encontrar la sesión ─────────────────────────────────────────────────────────────────

def sesiones(limite=10):
    """Directorios de sesión que tienen subagentes, del más reciente al más viejo."""
    out = []
    for d in glob.glob(os.path.join(PROJECTS, "*", "*", "subagents")):
        metas = glob.glob(os.path.join(d, "*.meta.json"))
        if not metas:
            continue
        out.append({"sesion": os.path.basename(os.path.dirname(d)),
                    "dir": d,
                    "n": len(metas),
                    "ultimo": max(os.path.getmtime(m) for m in metas)})
    out.sort(key=lambda s: -s["ultimo"])
    return out[:limite]


def _dir_de(sesion=None):
    """La carpeta `subagents/` de una sesión. Sin argumento, la más reciente."""
    if sesion and os.path.isdir(sesion):
        return sesion if sesion.endswith("subagents") else os.path.join(sesion, "subagents")
    todas = sesiones(limite=200)
    if sesion:
        for s in todas:
            if s["sesion"].startswith(sesion):
                return s["dir"]
        return None
    return todas[0]["dir"] if todas else None


# ─── cosecha ─────────────────────────────────────────────────────────────────────────────

def _ultimo_texto(ruta_jsonl):
    """El último bloque de texto que escribió el subagente. Fail-soft: línea rota, se salta."""
    ultimo = ""
    try:
        with open(ruta_jsonl, encoding="utf-8") as f:
            for linea in f:
                try:
                    reg = json.loads(linea)
                except Exception:
                    continue
                msg = reg.get("message") or {}
                if msg.get("role") != "assistant":
                    continue
                for bloque in (msg.get("content") or []):
                    if isinstance(bloque, dict) and bloque.get("type") == "text":
                        txt = (bloque.get("text") or "").strip()
                        if txt:
                            ultimo = txt
    except OSError:
        return ""
    return ultimo


def cosecha(sesion=None, desde_ts=0):
    """Lo que dijo cada subagente de esta sesión, del más viejo al más nuevo."""
    d = _dir_de(sesion)
    if not d:
        return []
    out = []
    for meta_path in sorted(glob.glob(os.path.join(d, "*.meta.json"))):
        try:
            ts = os.path.getmtime(meta_path)
        except OSError:
            continue
        if ts < desde_ts:
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        cuerpo = _ultimo_texto(meta_path[: -len(".meta.json")] + ".jsonl")
        if not cuerpo:
            continue
        out.append({"agente": meta.get("agentType") or "?",
                    "descripcion": meta.get("description") or "",
                    "cuerpo": cuerpo,
                    "ts": ts})
    out.sort(key=lambda c: c["ts"])
    return out


# ─── parseo: el contrato, y el plan B honesto ────────────────────────────────────────────

def parsear(cuerpo, agente):
    """El bloque JSON del contrato. Si no está, degradado y SIN fuente inventada."""
    bloques = RE_BLOQUE.findall(cuerpo or "")
    for crudo in reversed(bloques):          # el último que cierre bien
        try:
            d = json.loads(crudo)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if "decisiones" in d or "acciones" in d:
            return {"agente": agente,
                    "decisiones": d.get("decisiones") or [],
                    "acciones": d.get("acciones") or [],
                    "degradado": False}
    return {"agente": agente, "decisiones": [], "acciones": [], "degradado": True}


def contribuciones(cosechado):
    """De lo cosechado a lo que come `paso_consolidacion.consolidar`."""
    return [parsear(c["cuerpo"], c["agente"]) for c in cosechado]


# ─── el goal baja de la caja (16-sep-2026) ───────────────────────────────────────────────
# El charter declara el goal ARRIBA (`CAJA.md` → `## Goal`) y hasta hoy nadie lo leía en
# runtime: el dossier recibía `--intencion` a mano y, si nadie la escribía, salía con
# "(sin intención declarada)". El goal quedaba escrito y sin gobernar nada.
# FAIL-CLOSED a propósito: pedir `--caja` y no poder leer su goal es un ERROR (rc=2), nunca
# un dossier degradado en silencio — es el único punto del flujo que se caía sin picar.
# La salida (`tools/salida.py`) NO entra aquí: su trabajo es el gate de lo que va hacia
# fuera, no conocer el goal de cada caja. Acoplarla al charter la rompería como puerta.

ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")  # casa base: la Constelación (00_FUENTE-DE-VERDAD) solo vive ahí
RE_SLUG = re.compile(r"^[a-z0-9-]+$")          # mismo criterio que audit_constelacion (A9, anti path-traversal)
RE_PLACEHOLDER = re.compile(r"<[^>\n]+>")      # mismo criterio que A10: la plantilla sin rellenar no cuela
SIN_INTENCION = "(sin intención declarada)"


class CajaError(Exception):
    """El charter no se pudo leer o no dice su goal. Nunca se degrada: se pica."""


def ruta_caja(slug):
    """`Constelacion/<slug>/CAJA.md` bajo la casa base. Fail-closed en cada escalón."""
    if not RE_SLUG.match(slug or ""):
        raise CajaError("slug inválido %r (solo [a-z0-9-])" % (slug,))
    base = glob.glob(os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "04*IA", "Constelacion"))
    if not base:
        raise CajaError("no encuentro la Constelación bajo %s (¿falta BTP_REPO?)" % ROOT)
    ruta = os.path.join(base[0], slug, "CAJA.md")
    if not os.path.isfile(ruta):
        raise CajaError("no existe la caja %r (%s)" % (slug, ruta))
    return ruta


def goal_de_caja(slug):
    """El `## Goal` del charter, como intención del dossier. FAIL-CLOSED."""
    ruta = ruta_caja(slug)
    try:
        with io.open(ruta, encoding="utf-8") as fh:
            texto = fh.read()
    except Exception as e:
        raise CajaError("CAJA.md ilegible (%s)" % (e,))
    m = re.search(r"^##\s+Goal\s*$(.*?)(?=^##\s|\Z)", texto, re.M | re.S)
    if not m:
        raise CajaError("la caja %r no tiene sección '## Goal'" % (slug,))
    # el charter cita la plantilla en blockquote (`> PLANTILLA…`): eso no es el goal
    goal = " ".join(ln.strip() for ln in m.group(1).splitlines()
                    if ln.strip() and not ln.strip().startswith(">"))
    if not goal:
        raise CajaError("el '## Goal' de %r está vacío" % (slug,))
    if RE_PLACEHOLDER.search(goal):
        raise CajaError("el '## Goal' de %r sigue siendo la plantilla sin rellenar: %s"
                        % (slug, goal[:70]))
    return goal


# ─── dossier ─────────────────────────────────────────────────────────────────────────────

def dossier(cosechado, intencion=SIN_INTENCION):
    """(markdown, rc). rc=1 si alguna decisión se quedó sin fuente trazable."""
    contribs = contribuciones(cosechado)
    data = {"intencion": intencion, "contribuciones": contribs}
    partes = paso_consolidacion.consolidar(data)
    intencion_, cabeza, diferidas, acciones, avisos, hay_huerfana = partes

    mudos = [c["agente"] for c in contribs if c["degradado"]]
    if mudos:
        # No se oculta ni se rellena: se dice quién no cumplió el contrato y qué se pierde.
        avisos.append("sin bloque json del contrato: %s → su trabajo NO entra en el dossier "
                      "(está entero en su propia respuesta)" % ", ".join(sorted(set(mudos))))
    md = paso_consolidacion.render(intencion_, cabeza, diferidas, acciones, avisos)
    if not cabeza and not acciones:
        md += ("\n\n_(ningún asiento devolvió decisiones en el formato del contrato: "
               "%d contribuciones cosechadas, 0 estructuradas)_" % len(contribs))
    return md, (1 if hay_huerfana else 0)



# ─── convocar: la caja sienta a sus expertos (16-sep-2026) ───────────────────────────────
# El charter declaraba `dueno` y `expertos` y NADIE los leía: `audit_constelacion` verificaba
# en A7 que existieran en el registro, y ahí acababa. La caja sabía quién debía opinar y no
# los sentaba. Este es el cable que faltaba entre las dos puntas que ya existían.
#
# EMITE UNA ORDEN, NO LANZA: una tool de Python no puede abrir un subagente de Claude Code.
# Mismo patrón que `decide_peticion` (que ya resolvió esto): la decisión se escribe para la
# sesión, determinista y sin red, y es la sesión quien la ejecuta.
#
# El contrato del asiento se IMPORTA de `decide_peticion`: si viviera aquí también, dos
# formatos divergirían y el dossier dejaría de recoger la mitad del trabajo.

# Tres zonas autónomas, no dos. `productor` se añadió el 17-sep-2026 al ver que una caja
# que RENDERIZA algo (una malla 3D, un gráfico, un dataset) no cabía en ninguna: no es
# «investiga y archiva» ni «deja un borrador de texto». Sin él, el único formato de salida
# del sistema era un dossier de decisiones consolidadas — N expertos opinan y se juntan —,
# así que las cajas que producen COSAS no se podían declarar.
ARQUETIPOS = {
    "solo-lectura": "investiga, analiza y archiva. NO redacta borradores ni toca ficheros.",
    "redactor-borrador": "investiga y deja BORRADORES. No envía, no publica, no contacta, no paga.",
    "productor": ("produce un ARTEFACTO y lo deja en disco (render, gráfico, dataset, export). "
                  "Di en tu bloque JSON la RUTA del artefacto, que es tu entregable. "
                  "No envía, no publica, no contacta, no paga."),
}


def charter(slug):
    """El charter completo de una caja: (frontmatter, goal). FAIL-CLOSED en cada escalón."""
    ruta = ruta_caja(slug)
    try:
        with io.open(ruta, encoding="utf-8") as fh:
            texto = fh.read()
    except Exception as e:
        raise CajaError("CAJA.md ilegible (%s)" % (e,))
    sys.path.insert(0, AQUI)
    import audit_constelacion as AC      # el parser del charter vive con su auditor
    fm, _, err = AC._parse_frontmatter(texto)
    if fm is None:
        raise CajaError("charter de %r sin frontmatter válido: %s" % (slug, err))
    return fm, goal_de_caja(slug)


def convocar(slug):
    """(orden, rc). La caja lee a quién declaró y escribe la orden que los sienta.

    No se convoca una caja que no está en pie: `propuesta` aún no se ha aprobado,
    `en-pausa` y `archivada` se retiraron a propósito. Sentar expertos sobre una caja
    muerta gasta presupuesto y produce un dossier que nadie pidió.
    """
    fm, goal = charter(slug)
    estado = (fm.get("estado") or "").strip()
    if estado != "activa":
        raise CajaError("la caja %r está %r, no 'activa': no se convoca" % (slug, estado or "?"))

    dueno = (fm.get("dueno") or "").strip()
    expertos = [e.strip() for e in (fm.get("expertos") or []) if e and e.strip()]
    asientos = [a for a in ([dueno] + expertos) if a]
    asientos = list(dict.fromkeys(asientos))            # el dueño puede repetirse en expertos
    if not asientos:
        raise CajaError("el charter de %r no declara dueño ni expertos: no hay a quién sentar"
                        % (slug,))

    arquetipo = (fm.get("arquetipo") or "").strip()
    limite = ARQUETIPOS.get(arquetipo, "atente a la zona autónoma que declara el charter.")

    sys.path.insert(0, AQUI)
    import decide_peticion as DP          # el contrato del asiento, en su única definición

    lineas = [
        "CAJA «%s» — convocatoria. Ejecútala ANTES de responder." % slug,
        "  🎯 GOAL: %s" % goal,
    ]
    if (fm.get("ned_desbloquea") or "").strip():
        lineas.append("  🔓 Desbloquea: %s" % fm["ned_desbloquea"].strip())
    lineas.append("  🧭 Zona autónoma (%s): %s" % (arquetipo or "sin arquetipo", limite))
    lineas.append("  🚪 Nada hacia fuera sin OK: publicar, contactar o pagar pasa por "
                  "tools/salida.py.")
    if fm.get("presupuesto_usd"):
        lineas.append("  💸 Presupuesto de la caja: %s USD (el freno de gasto es global)."
                      % fm["presupuesto_usd"])
    lineas.append("")
    for i, a in enumerate(asientos, 1):
        quien = "dueño" if a == dueno else "experto"
        lineas.append("  %d. Agent(subagent_type=\"%s\")  — %s. Dale el GOAL como vara de "
                      "éxito, no la tarea suelta." % (i, a, quien))
    if len(asientos) > 1:
        lineas.append("  → lanza en paralelo lo independiente; di dónde discrepan.")
    lineas.append(DP.CONTRATO)
    lineas.append("  🧺 Cierra con: python3 tools/caja.py dossier --caja %s" % slug)
    return "\n".join(lineas), 0


# ─── CLI ─────────────────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(description="Junta lo que dijeron N subagentes en UN dossier.")
    p.add_argument("accion", choices=["cosecha", "dossier", "convocar", "sesiones"])
    p.add_argument("--sesion", help="id de sesión (o su prefijo) o ruta; por defecto, la última")
    p.add_argument("--desde", type=float, default=0.0, help="epoch: solo subagentes posteriores")
    p.add_argument("--caja", help="slug de la caja: su '## Goal' (CAJA.md) es la intención del dossier")
    p.add_argument("--intencion", default=SIN_INTENCION,
                   help="intención a mano; incompatible con --caja (el goal manda)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.accion == "convocar":
        if not a.caja:
            print("convocar necesita --caja <slug>", file=sys.stderr)
            return 2
        try:
            orden, rc = convocar(a.caja)
        except CajaError as e:
            print("caja %r: %s" % (a.caja, e), file=sys.stderr)
            return 2
        print(orden)
        return rc

    if a.accion == "sesiones":
        for s in sesiones():
            print("%-38s %3d subagentes  %s" % (s["sesion"], s["n"],
                  time.strftime("%d-%b %H:%M", time.localtime(s["ultimo"]))))
        return 0

    # El goal manda sobre la intención tecleada: si se nombra la caja, no hay dos verdades.
    intencion = a.intencion
    if a.caja:
        if a.intencion != SIN_INTENCION:
            print("--caja y --intencion son incompatibles: el goal del charter manda.",
                  file=sys.stderr)
            return 2
        try:
            intencion = goal_de_caja(a.caja)
        except CajaError as e:
            print("caja %r: %s" % (a.caja, e), file=sys.stderr)
            return 2

    cosechado = cosecha(a.sesion, a.desde)
    if a.accion == "cosecha":
        if a.json:
            print(json.dumps(cosechado, ensure_ascii=False, indent=1))
        else:
            for c in cosechado:
                print("── %s · %s · %d car" % (c["agente"], c["descripcion"][:50], len(c["cuerpo"])))
            print("\n%d contribuciones" % len(cosechado))
        return 0

    if not cosechado:
        print("No hay subagentes que cosechar en esta sesión.", file=sys.stderr)
        return 2
    md, rc = dossier(cosechado, intencion)
    print(md)
    return rc


if __name__ == "__main__":
    sys.exit(main())
