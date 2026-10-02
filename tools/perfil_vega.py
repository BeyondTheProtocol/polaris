#!/usr/bin/env python3
"""tools/perfil_vega.py — quien ESCRIBE el perfil de trabajo de {{TITULAR}} para Vega.

POR QUÉ (1-oct-2026, plan «Vega aprende y se adelanta», eslabón 2, aprobado por {{TITULAR}})
-------------------------------------------------------------------------------------
`asistente.md` decía mantener un perfil vivo en `_PRIVADO_NUCLEO/asistente/perfil-trabajo.md`.
Medido el 1-oct: seguía siendo la plantilla del 21-jun, con 0 reacciones. Ningún script lo
escribía, y `auto-mejora` (paso 3b) consolidaba un perfil vacío. Mientras, desde el 27-jun hubo
191 memorias `feedback-*` nuevas o cambiadas, 46 sobre Vega o los avisos, que no llegaban a Vega.

QUÉ HACE
--------
Reescribe UNA sección del perfil, entre marcas, con tres bloques. Lo demás del fichero es de
{{TITULAR}} y no se toca.
  · Reglas suyas que tocan a Vega: una línea por memoria `feedback-*`, su `description` y el
    enlace `[[slug]]`. APUNTA a la memoria, no la copia: la fuente sigue siendo la memoria.
  · Dichas en sesión: las «Reglas nuevas» que la continuidad apuntó en 14 días. Si alguna no
    tiene memoria, es una corrección sin capturar (señal para `auto-mejora`).
  · Señales de la semana: avisos entregados y aplazados, hilos cerrados por origen y, cuando
    exista, reacciones de Telegram. Esto es lo que no está en ninguna memoria.

SU CONTROL (el perfil es de ella)
---------------------------------
  · Borra una línea y no vuelve (se guarda como vetada).
  · Borra la sección entera, marcas incluidas, y el destilador no la vuelve a escribir (opt-out).
Determinista: sin LLM, sin red. Lo lanza `hoy_compose.sh` cada mañana antes del parte.

Uso:
  python3 tools/perfil_vega.py destilar [--seco]
  python3 tools/perfil_vega.py bloque          # la sección actual, para el contexto de Vega
"""
import glob
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _casa import casa_base  # noqa: E402

REPO = casa_base()
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
PERFIL = os.environ.get("BTP_PERFIL_VEGA") or os.path.join(
    REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_NUCLEO", "asistente", "perfil-trabajo.md")
MEMORIA = os.environ.get("BTP_MEMORIA_DIR") or os.path.expanduser(
    "~/.claude/projects/-Users-polaris-claudecode/memory")
ESTADO = os.path.join(STATE, "vega", "perfil_estado.json")
REACCIONES = os.path.join(STATE, "vega", "reacciones.jsonl")

MARCA_INI = ("<!-- VEGA-PERFIL-AUTO:INICIO · lo escribe tools/perfil_vega.py cada mañana. "
             "Borra una línea y no vuelve; borra la sección entera y no se vuelve a escribir. -->")
MARCA_FIN = "<!-- VEGA-PERFIL-AUTO:FIN -->"
RE_SECCION = re.compile(re.escape("<!-- VEGA-PERFIL-AUTO:INICIO") + r".*?" + re.escape(MARCA_FIN),
                        re.S)
RE_CLAVE = re.compile(r" ?<!--k:([^ ]+) -->")

# Qué memorias tocan a Vega: las que hablan de avisos, tareas, el parte o cómo pregunta.
RELEVANTE = re.compile(r"vega|asistente|aviso|avisar|recordatori|tablero|tarea|el parte|"
                       r"telegram|proactiv|follonera|ruido|pregunt|menú|seguimiento", re.I)
MAX_REGLAS = 40
MAX_SESION = 10
DIAS_SESION = 14
DIAS_SENALES = 7


# ─── fuentes ──────────────────────────────────────────────────────────────────────────────

def _descripcion(ruta):
    try:
        with open(ruta, encoding="utf-8") as fh:
            cab = fh.read(2000)
    except OSError:
        return ""
    m = re.search(r"^description:\s*(.+)$", cab, re.M)
    return m.group(1).strip().strip('"').replace('\\"', '"') if m else ""


def reglas_feedback():
    """[(clave, línea, fecha)] de las feedback-* que tocan a Vega, de la más nueva a la más vieja.
    La clave es el slug del fichero: así el enlace [[slug]] siempre resuelve."""
    out = []
    for ruta in glob.glob(os.path.join(MEMORIA, "feedback-*.md")):
        slug = os.path.basename(ruta)[:-3]
        desc = _descripcion(ruta)
        if not desc or not RELEVANTE.search(slug + " " + desc):
            continue
        if len(desc) > 220:
            desc = desc[:217].rstrip() + "…"
        fecha = date.fromtimestamp(os.path.getmtime(ruta)).isoformat()
        out.append((slug, "%s · [[%s]] · %s" % (desc, slug, fecha), fecha))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


RE_REGLAS = re.compile(r"\**Reglas nuevas:?\**:?\s*(.+?)(?=\n\s*\n|\n\**[A-ZÁÉÍÓÚ][a-záéíóú]+:|\Z)",
                       re.S)


def reglas_sesion(dias=DIAS_SESION):
    """[(clave, línea, fecha)] de las «Reglas nuevas» de la continuidad confiable reciente."""
    try:
        import continuity
        bloques = continuity.recent(300)
    except Exception:
        return []
    corte = (date.today() - timedelta(days=dias)).isoformat()
    out, vistas = [], set()
    for b in bloques:
        cab, _, cuerpo = b.partition("\n")
        fecha = cab[3:13]
        if "[confiable]" not in cab or fecha < corte:
            continue
        m = RE_REGLAS.search(cuerpo)
        if not m:
            continue
        texto = " ".join(m.group(1).split())
        if not texto or texto.upper().startswith("NADA") or texto.lower() in vistas:
            continue
        vistas.add(texto.lower())
        if len(texto) > 240:
            texto = texto[:237].rstrip() + "…"
        clave = "s-" + hashlib.sha1(texto.lower().encode("utf-8")).hexdigest()[:10]
        out.append((clave, "%s · (sesión %s)" % (texto, fecha), fecha))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _veredictos_salida(dia):
    ruta = os.path.join(STATE, "outbox", "audit-%s.jsonl" % dia)
    ent = apl = 0
    try:
        with open(ruta, encoding="utf-8", errors="ignore") as fh:
            for linea in fh:
                if '"veredicto": "entregado"' in linea:
                    ent += 1
                elif '"veredicto": "aplazado"' in linea:
                    apl += 1
    except OSError:
        return None
    return ent, apl


def senales(dias=DIAS_SENALES):
    """Líneas de señal de comportamiento. Sin texto de terceros: solo cuentas."""
    lineas = []
    hoy = date.today()
    dias_l = [(hoy - timedelta(days=i)).isoformat() for i in range(dias)]
    tot_e = tot_a = n = 0
    for d in dias_l:
        v = _veredictos_salida(d)
        if v:
            tot_e, tot_a, n = tot_e + v[0], tot_a + v[1], n + 1
    if n:
        lineas.append("Avisos (%d días con registro): %.0f entregados/día y %.0f aplazados al parte/día"
                      % (n, tot_e / n, tot_a / n))
    try:
        import seguimiento
        cerr = {}
        for h in seguimiento.load_seguimiento().get("hilos", []):
            if h.get("estado") == "hecho" and (h.get("hecho_el") or "") >= dias_l[-1]:
                o = h.get("origen") or "manual"
                cerr[o] = cerr.get(o, 0) + 1
        if cerr:
            lineas.append("Hilos cerrados en %d días, por origen: %s" % (dias, ", ".join(
                "%s %d" % kv for kv in sorted(cerr.items(), key=lambda kv: -kv[1]))))
    except Exception:
        pass
    reac = {}
    try:
        with open(REACCIONES, encoding="utf-8") as fh:
            for linea in fh:
                try:
                    r = json.loads(linea)
                except Exception:
                    continue
                if str(r.get("ts", ""))[:10] >= dias_l[-1]:
                    reac[r.get("senal", "?")] = reac.get(r.get("senal", "?"), 0) + 1
    except OSError:
        pass
    lineas.append("Reacciones a Vega en %d días: %s" % (dias, ", ".join(
        "%s %d" % kv for kv in sorted(reac.items())) if reac else "ninguna registrada todavía"))
    return lineas


# ─── estado y escritura ───────────────────────────────────────────────────────────────────

def _estado():
    try:
        with open(ESTADO, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        d = {}
    d.setdefault("escritas", [])
    d.setdefault("vetadas", [])
    return d


def _guardar_estado(d):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, ESTADO)


def render(vetadas):
    vet = set(vetadas)
    fb = [t for t in reglas_feedback() if t[0] not in vet][:MAX_REGLAS]
    se = [t for t in reglas_sesion() if t[0] not in vet][:MAX_SESION]
    partes = [MARCA_INI,
              "## Lo aprendido (automático, %s)" % datetime.now().strftime("%Y-%m-%d %H:%M"),
              "",
              "### Reglas suyas que tocan a Vega (de la más nueva a la más vieja)"]
    partes += ["- %s <!--k:%s -->" % (t, c) for c, t, _f in fb] or ["- (ninguna)"]
    partes += ["", "### Dichas en sesión (%d días; si no tienen memoria, falta capturarlas)"
               % DIAS_SESION]
    partes += ["- %s <!--k:%s -->" % (t, c) for c, t, _f in se] or ["- (ninguna)"]
    partes += ["", "### Señales de la semana"] + ["- " + s for s in senales()]
    partes += [MARCA_FIN]
    return "\n".join(partes), [c for c, _t, _f in fb] + [c for c, _t, _f in se]


def destilar(seco=False):
    """Reescribe la sección automática. Devuelve un dict con lo que hizo."""
    try:
        with open(PERFIL, encoding="utf-8") as fh:
            actual = fh.read()
    except OSError:
        return {"resultado": "sin perfil", "ruta": PERFIL}
    est = _estado()
    m = RE_SECCION.search(actual)
    if not m and est["escritas"]:
        # Ya se escribió alguna vez y ella quitó la sección entera: opt-out, no se reescribe.
        return {"resultado": "opt-out: la sección se borró a mano"}
    nuevas_vetadas = []
    if m:
        presentes = set(RE_CLAVE.findall(m.group(0)))
        nuevas_vetadas = [c for c in est["escritas"] if c not in presentes]
    vetadas = sorted(set(est["vetadas"]) | set(nuevas_vetadas))
    seccion, claves = render(vetadas)
    if m:
        nuevo = actual[:m.start()] + seccion + actual[m.end():]
    else:
        nuevo = actual.rstrip("\n") + "\n\n" + seccion + "\n"
    res = {"resultado": "seco" if seco else "escrito", "lineas": len(claves),
           "vetadas_nuevas": len(nuevas_vetadas), "vetadas": len(vetadas)}
    if seco:
        res["seccion"] = seccion
        return res
    tmp = PERFIL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(nuevo)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PERFIL)
    est.update(escritas=claves, vetadas=vetadas, ultima=datetime.now().isoformat(timespec="seconds"))
    _guardar_estado(est)
    return res


def bloque(max_chars=6000):
    """La sección actual sin marcas ni claves, para el contexto de Vega. '' si no hay."""
    try:
        with open(PERFIL, encoding="utf-8") as fh:
            m = RE_SECCION.search(fh.read())
    except OSError:
        return ""
    if not m:
        return ""
    texto = "\n".join(RE_CLAVE.sub("", m.group(0)).splitlines()[1:-1])
    if len(texto) > max_chars:
        texto = texto[:max_chars].rstrip() + "\n… (sigue en el perfil)"
    return ("== CÓMO TRABAJA TITULAR (tu perfil vivo; privado: nunca lo pegues por Telegram) ==\n"
            + texto)


def main(argv):
    if not argv or argv[0] not in ("destilar", "bloque"):
        print(__doc__)
        return 2
    if argv[0] == "bloque":
        print(bloque())
        return 0
    res = destilar(seco="--seco" in argv)
    if "seccion" in res:
        print(res.pop("seccion"))
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
