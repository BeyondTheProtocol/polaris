#!/usr/bin/env python3
"""Auditoría de comités del gabinete.

Comprueba dos cosas de CADA agente en `.claude/agents/*.md`:
  1. Que esté registrado en el catálogo
     `00_FUENTE-DE-VERDAD/04 · IA/Comites-Registro.md` (y avisa de entradas del
     registro que ya no tienen agente).
  2. Su CICLO DE VIDA: que declare `estado` válido (borrador|activo|archivado),
     `revision` (fecha ISO) y que no esté caducado (revisión vieja → WARN).

Pensado para la rutina diaria de auto-mejora: así nunca se crea un comité sin
guardarlo como equipo reinvocable, ni queda un agente "zombi" sin revisar.

Local y privado: solo lee ficheros, no toca nada hacia fuera.
Salida: 0 = todo cuadra · 1 = FALLO (agente sin registrar o `estado` inválido).
Los WARN (sin estado/revisión, o revisión caducada) NO hacen fallar: son deuda a saldar.

Con `--uso` añade la AUDITORÍA DE SUPERFICIE (30-jul-2026): cuántas veces se ha invocado de verdad
cada agente, contando `"subagent_type": "<slug>"` en los transcripts. Nació de medir que 13 de los
35 agentes tenían CERO invocaciones —el más nuevo, de hacía 19 días— mientras sus descripciones
seguían ocupando sitio en el system prompt de cada sesión y compitiendo por la atención del
enrutador. Es INFORMACIÓN, no un veredicto: no cambia el exit code y no archiva nada. Quién se
retira, quién se cablea y quién se queda lo propone el bucle de auto-mejora y lo decide {{TITULAR}}.

DOS FUENTES, NO UNA (30-jul-2026, misma tarde). Los transcripts solo ven al agente invocado DENTRO
de una sesión (`subagent_type`); un comité que corre como DAEMON por `run_agent.sh` o por el
dispatcher no aparece jamás. Con una sola fuente el informe cantaba CERO para el `orquestador`
—830 ejecuciones reales— y estuvo a punto de costar el archivado de 6 agentes que sí están
cableados. La segunda fuente es `tools/state/observabilidad/*.jsonl`, la bitácora que escriben
`run_agent.sh` y el hook `traza_subagente.py`.

No se suman a lo bruto: las líneas con `job == "sesion"` las escribe el hook para invocaciones que
los transcripts YA cuentan, así que se descartan (si no, doble conteo desde el 25-jul). Total =
transcripts (sesión) + observabilidad con `job != "sesion"` (daemon/dispatcher).
"""
import datetime
import glob
import json
import os
import re
import sys

# Casa base SIEMPRE: el registro vive en la fuente de verdad (gitignored), no en un
# worktree. Resolver desde __file__ daba un falso ROJO al correr desde una rama.
ROOT = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
AGENTS_DIR = os.path.join(ROOT, ".claude", "agents")

# Agentes built-in del sistema (no son comités del gabinete; no exigir registro)
BUILTIN = {
    "claude", "general-purpose", "claude-code-guide", "statusline-setup",
    "explore", "plan",
}

ESTADOS_VALIDOS = {"borrador", "activo", "archivado"}
STALE_DAYS = 90  # revisión más vieja que esto → WARN (¿sigue al día?)

# --- Auditoría de superficie (`--uso`) ---
TRANSCRIPTS = os.environ.get("BTP_TRANSCRIPTS") or os.path.expanduser("~/.claude/projects")
OBS_DIR = os.environ.get("BTP_OBS_DIR") or os.path.join(ROOT, "tools", "state", "observabilidad")
JOB_SESION = "sesion"   # marca del hook traza_subagente: ya lo cuentan los transcripts
DIAS_USO = 30           # ventana de "se usa" (por mtime del fichero de sesión)
RE_SUBAGENTE = re.compile(r'"subagent_type"\s*:\s*"([a-z0-9][a-z0-9-]*)"')
# Nombres históricos: el agente se renombró y las invocaciones viejas quedaron con el nombre viejo.
# Sin esto el audit canta un CERO falso justo en los agentes más usados. Comprobado el 30-jul-26:
# `contacto` acumulaba 15 llamadas y `sid` 8, ambas anteriores al commit 8da1861 que los renombró.
ALIAS = {
    "contacto": "consejero-marketing",
    "sid": "consejero-acceso",
    "prensa-nacional": "prensa",
    "prensa-internacional": "prensa",
}


def find_registry():
    hits = glob.glob(os.path.join(ROOT, "00_FUENTE-DE-VERDAD", "04*IA", "Comites-Registro.md"))
    return hits[0] if hits else None


def frontmatter(path):
    """Parseo mínimo del frontmatter YAML (key: value) entre las dos primeras '---'."""
    fm = {}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return fm
    if not lines or lines[0].strip() != "---":
        return fm
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", ln)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def _dias_desde(iso):
    try:
        d = datetime.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return (datetime.date.today() - d).days


def uso_por_agente(dias=None, transcripts=None):
    """Cuenta invocaciones reales por slug en los transcripts. Determinista, $0, solo lectura.

    `dias` acota por mtime del fichero de sesión (una sesión "vive" mientras se escribe en ella);
    None = todo el histórico. Devuelve (contador, ficheros_leidos).
    """
    raiz = transcripts or TRANSCRIPTS
    corte = None
    if dias:
        corte = datetime.datetime.now().timestamp() - dias * 86400
    cuenta, leidos = {}, 0
    for base, _dirs, ficheros in os.walk(raiz):
        for nombre in ficheros:
            if not nombre.endswith(".jsonl"):
                continue
            path = os.path.join(base, nombre)
            try:
                if corte is not None and os.path.getmtime(path) < corte:
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    leidos += 1
                    for linea in f:
                        # Filtro barato antes del regex: la inmensa mayoría de líneas no lo llevan.
                        if "subagent_type" not in linea:
                            continue
                        for slug in RE_SUBAGENTE.findall(linea):
                            slug = ALIAS.get(slug, slug)
                            cuenta[slug] = cuenta.get(slug, 0) + 1
            except OSError:
                continue
    return cuenta, leidos


def uso_daemon(dias=None, obs_dir=None):
    """Cuenta ejecuciones de comité que NO pasan por una sesión: daemons y dispatcher.

    Lee la bitácora `observabilidad-YYYY-MM-DD.jsonl` y descarta las líneas con
    `job == "sesion"` (esas las escribe el hook para llamadas que los transcripts ya cuentan;
    sumarlas sería contar dos veces). `dias` acota por la fecha del fichero, que es la fecha
    local de rotación. Devuelve (contador, ficheros_leidos).
    """
    raiz = obs_dir or OBS_DIR
    corte = None
    if dias:
        corte = datetime.date.today() - datetime.timedelta(days=dias)
    cuenta, leidos = {}, 0
    for path in sorted(glob.glob(os.path.join(raiz, "observabilidad-*.jsonl"))):
        if corte is not None:
            try:
                dia = datetime.date.fromisoformat(os.path.basename(path)[15:25])
            except ValueError:
                dia = None                      # nombre raro: no lo descartes por la fecha
            if dia is not None and dia < corte:
                continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                leidos += 1
                for linea in f:
                    try:
                        rec = json.loads(linea)
                    except ValueError:
                        continue                # línea a medio escribir: sáltala, no rompas
                    if not isinstance(rec, dict) or rec.get("job") == JOB_SESION:
                        continue
                    slug = rec.get("agente")
                    if not slug:
                        continue
                    slug = ALIAS.get(slug, slug)
                    cuenta[slug] = cuenta.get(slug, 0) + 1
        except OSError:
            continue
    return cuenta, leidos


def _alta_por_agente():
    """Fecha de alta de cada `.md` en git, en UNA sola llamada. {} si no se puede (no es fatal).

    Ojo: sin `--follow` (imposible con varias rutas a la vez), un agente renombrado figura como
    dado de alta el día del renombrado. No distorsiona el informe porque sus invocaciones viejas
    ya se recuperan por `ALIAS`.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "log", "--diff-filter=A", "--name-only",
             "--format=@%ad", "--date=short", "--", ".claude/agents/"],
            capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    altas, fecha = {}, None
    for linea in out.splitlines():
        linea = linea.strip()
        if linea.startswith("@"):
            fecha = linea[1:]
        elif linea.endswith(".md") and fecha:
            slug = os.path.basename(linea)[:-3]
            altas[slug] = fecha            # el log va de reciente a antiguo: gana la última vista
    return altas


def _informe_uso(agents):
    cuenta, leidos = uso_por_agente()
    reciente, _ = uso_por_agente(dias=DIAS_USO)
    daemon, obs_leidos = uso_daemon()
    daemon_rec, _ = uso_daemon(dias=DIAS_USO)
    altas = _alta_por_agente()

    filas = []
    for slug in sorted(agents):
        ses, dae = cuenta.get(slug, 0), daemon.get(slug, 0)
        ult30 = reciente.get(slug, 0) + daemon_rec.get(slug, 0)
        edad = _dias_desde(altas.get(slug, ""))
        filas.append((ses + dae, ult30, slug, edad, ses, dae))
    filas.sort(key=lambda r: (r[0], r[1]))

    print("\n📊 USO REAL (%d ficheros de sesión + %d días de bitácora · ventana reciente: %d días)"
          % (leidos, obs_leidos, DIAS_USO))
    for total, ult30, slug, edad, ses, dae in filas:
        marca = "💤" if total == 0 else ("· " if ult30 == 0 else "✅")
        vida = "" if edad is None else "  · alta hace %d d" % edad
        print("   %s %-24s total %4d (sesión %3d + daemon %4d) · últimos %d d: %4d%s"
              % (marca, slug, total, ses, dae, DIAS_USO, ult30, vida))

    dormidos = [f for f in filas if f[0] == 0]
    frios = [f for f in filas if f[0] > 0 and f[1] == 0]
    print("   → 💤 nunca invocados: %d · · sin uso en %d días: %d · ✅ vivos: %d"
          % (len(dormidos), DIAS_USO, len(frios), len(filas) - len(dormidos) - len(frios)))
    if dormidos:
        print("     Para cada 💤 el bucle propone (no ejecuta): retirar · cablear · mantener con "
              "motivo. Tocar el roster es gate de {{TITULAR}}.")
        print("     ⚠️ 'nunca invocado' NO significa 'sobra': puede ser útil y no tener nada que lo "
              "DISPARE (fallo de cableado, no de utilidad).")
    return 0


def main():
    reg = find_registry()
    if not reg:
        print("❌ No encuentro el Registro de Comités (04 · IA/Comites-Registro.md).")
        return 1

    with open(reg, encoding="utf-8") as f:
        reg_text = f.read()

    # Agentes de gabinete en disco (slug → ruta)
    paths = {
        os.path.basename(p)[:-3]: p
        for p in glob.glob(os.path.join(AGENTS_DIR, "*.md"))
    }
    for b in BUILTIN:
        paths.pop(b, None)
    agents = set(paths)

    # Slugs que el registro declara como AGENTE = los de su ÚLTIMA columna.
    # Antes se cogía cualquier span en backticks de todo el fichero, así que un recolector
    # (`correo-imap`), una skill (`deep-research`) o una caja (`viaje-{{CIUDAD}}-prueba`)
    # citados en una descripción salían como "entrada obsoleta". Ese falso positivo se
    # re-reportó ≥6 veces del 19 al 25-jul sin que nadie pudiera cerrarlo: el heurístico
    # era el bug, no el registro (norma «detectar no es arreglar», 30-jul-26).
    reg_slugs = set()
    for linea in reg_text.splitlines():
        if not linea.lstrip().startswith("|"):
            continue
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        if celdas:
            reg_slugs.update(re.findall(r"`([a-z][a-z0-9-]+)`", celdas[-1]))

    missing = sorted(a for a in agents if a not in reg_slugs)
    ghosts = sorted(
        s for s in reg_slugs
        if "-" in s and s not in agents
        and not s.startswith(("feedback-", "reference-", "project-", "user-", "insights-"))
        and not os.path.exists(os.path.join(AGENTS_DIR, s + ".md"))
        and not s.endswith((".md", ".py", ".json"))
    )

    # Ciclo de vida
    estado_invalido, sin_estado, sin_revision, caducados = [], [], [], []
    for slug in sorted(agents):
        fm = frontmatter(paths[slug])
        est = fm.get("estado")
        if est is None:
            sin_estado.append(slug)
        elif est not in ESTADOS_VALIDOS:
            estado_invalido.append((slug, est))
        rev = fm.get("revision")
        if rev is None:
            sin_revision.append(slug)
        else:
            d = _dias_desde(rev)
            if d is not None and d > STALE_DAYS:
                caducados.append((slug, rev, d))

    print(f"🏛️  Agentes de gabinete en disco: {len(agents)}")
    print(f"📒 Registrados correctamente:      {len(agents) - len(missing)}/{len(agents)}")
    con_estado = len(agents) - len(sin_estado)
    print(f"🪪 Con ciclo de vida (estado):     {con_estado}/{len(agents)}")

    # FALLOS (hacen exit 1)
    if missing:
        print("\n⚠️  AGENTES SIN REGISTRAR (añádelos al catálogo):")
        for a in missing:
            print(f"   · {a}  →  falta fila en Comites-Registro.md")
    if estado_invalido:
        print("\n❌ ESTADO inválido (debe ser borrador|activo|archivado):")
        for a, est in estado_invalido:
            print(f"   · {a}  →  estado: {est!r}")
    if not missing and not estado_invalido:
        print("✅ Todos registrados y con estado válido.")

    # WARN (deuda; NO hacen fallar)
    if sin_estado:
        print("\nℹ️  Sin ciclo de vida (migración pendiente — estado/revision/version):")
        print("   · " + ", ".join(sin_estado))
    if caducados:
        print(f"\nℹ️  Revisión caducada (>{STALE_DAYS} días — ¿siguen al día?):")
        for a, rev, d in caducados:
            print(f"   · {a}  →  revisión {rev} ({d} días)")
    if ghosts:
        print("\nℹ️  Posibles entradas obsoletas en el registro (slug sin agente en disco):")
        for g in ghosts:
            print(f"   · {g}  (¿renombrado/borrado? revisa)")

    # Auditoría de superficie: informativa, NO toca el exit code (papeleo que apaga el sistema
    # entero ya pasó dos veces en julio — corrección del 14-jul-26).
    if "--uso" in sys.argv[1:]:
        _informe_uso(agents)

    ok = not missing and not estado_invalido
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
