#!/usr/bin/env python3
"""tools/inventario.py — qué piezas de Polaris están VIVAS y cuáles no las llama nadie.

POR QUÉ (19-sep-2026, problema nº2 de `docs/lo-que-falta.md`). Aquel día había 189 herramientas,
33 agentes y 68 daemons (el 25-sep ya eran 250 y 34: la cifra escrita caduca, la viva la da esto), y el catálogo crece más rápido que la memoria de nadie. El auditor vigila los
charters de las cajas; el inventario no lo vigilaba nada. Síntoma real del mismo día: un modelo
local de 5 GB descargado una semana antes **al que ningún código llamaba**.

QUÉ MIDE (y qué NO). Esto no instrumenta nada ni gasta un token: cruza cuatro señales que ya
existen en el repo.

  1. 📌 referencias en código y config — ¿la nombra otra tool, un test, un agente, una regla?
  2. ⏰ daemons — ¿la ejecuta un plist de launchd?
  3. 🧾 registros del lazo — ¿aparece en el ledger del borde o en los logs de launchd?
  4. 📅 antigüedad — último commit que la tocó (`git log`)

Con eso clasifica cada herramienta:

  · `viva`      — la llama alguien que no es ella misma ni su test
  · `solo-test` — únicamente la nombra su propio test: existe para pasar, no para usarse
  · `huerfana`  — NADIE la nombra. Candidata a retirada
  · `entrada`   — nadie la nombra porque es un punto de entrada (daemon o CLI documentada)

**Clasificar no es borrar.** Una huérfana puede ser una pieza nueva a medio cablear, y por eso
la salida dice desde cuándo no se toca: decidir es de quien manda, no de este script.

Uso:
  python3 tools/inventario.py                 # resumen por estado
  python3 tools/inventario.py --huerfanas     # solo las que no llama nadie
  python3 tools/inventario.py --json
  python3 tools/inventario.py --agentes       # lo mismo para .claude/agents/
  python3 tools/inventario.py --viejas 60     # herramientas sin tocar en >60 días
  python3 tools/inventario.py --modelos       # modelos locales de Ollama no usados
  python3 tools/inventario.py --uso [--dias 30]   # acumula el uso real en state/uso_piezas.json
  python3 tools/inventario.py --ciclo [--proponer] # 60 días sin uso: observación; 90 y huérfana: retiro
  python3 tools/inventario.py --noche         # lo que corre com.btp.inventario-uso cada noche
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime

REPO = os.environ.get("BTP_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
AGENTES = os.path.join(REPO, ".claude", "agents")
# Dónde se busca quién nombra a quién. El estado vivo NO cuenta: que una tool aparezca en un log
# no prueba que alguien la llame hoy, y meterlo daría por viva media casa.
AMBITOS = ("tools", "tests", ".claude/agents", ".claude/rules", ".claude/hooks", "evals",
           "pipeline", "docs", "CLAUDE.md", "AGENTS.md", "README.md")
SALTAR = re.compile(r"^(_|test_)")


def _ficheros(carpeta, sufijo=".py"):
    if not os.path.isdir(carpeta):
        return []
    return sorted(f for f in os.listdir(carpeta) if f.endswith(sufijo))


def _grep(termino, palabra=False):
    """Ficheros del repo que mencionan el término, sin el estado vivo ni los .git.

    `palabra=True` exige límite de palabra: hace falta para buscar el módulo desnudo
    (`cn_fetch`, sin `.py`), que es como lo nombran el panel y las reglas. Sin eso,
    `inventario.py` daba por huérfanas piezas que sí se citan — 3 de 9 en la primera pasada."""
    cmd = ["git", "-C", REPO, "grep", "-l"] + (["-w"] if palabra else ["-F"]) + [termino, "--"]
    r = subprocess.run(cmd + list(AMBITOS), capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]


def _daemons():
    """Qué scripts ejecuta launchd, leídos de los plists del repo."""
    fuera = set()
    d = os.path.join(TOOLS, "launchd")
    for f in _ficheros(d, ".plist"):
        try:
            with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                texto = fh.read()
        except OSError:
            continue
        for m in re.finditer(r"([A-Za-z0-9_]+\.(?:py|sh))", texto):
            fuera.add(m.group(1))
    return fuera


def _ultimo_commit(rel):
    r = subprocess.run(["git", "-C", REPO, "log", "-1", "--format=%as", "--", rel],
                       capture_output=True, text=True)
    return (r.stdout or "").strip() or "?"


class _Indice:
    """Los ficheros de AMBITOS leídos UNA vez, para no lanzar 5 subprocesos por pieza.

    Medido el 25-sep-2026: con 4 `git grep` + 1 `git log` por herramienta, `inventario.py`
    tardaba 51 s sobre 223 tools. Mismo criterio que `_grep` (ficheros versionados del árbol
    de trabajo, sin el estado vivo), resuelto en memoria."""

    def __init__(self):
        r = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--"] + list(AMBITOS),
                           capture_output=True, text=True)
        self.textos = {}
        for rel in (r.stdout or "").split("\0"):
            if not rel:
                continue
            try:
                with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as fh:
                    t = fh.read()
            except OSError:
                continue
            if "\0" not in t[:2048]:                 # binarios fuera, como `git grep -I`
                self.textos[rel] = t
        self.palabras = {rel: set(re.findall(r"[A-Za-z0-9_]+", t))
                         for rel, t in self.textos.items()}
        self.donde = {}                          # palabra → ficheros que la contienen
        for rel, ws in self.palabras.items():
            for w in ws:
                self.donde.setdefault(w, set()).add(rel)

    def _candidatos(self, termino):
        """Ficheros que PUEDEN contener el término: los que tienen una palabra que contiene
        su palabra más larga. Así la búsqueda literal solo recorre unos pocos textos."""
        trozos = re.findall(r"[A-Za-z0-9_]+", termino)
        if not trozos:
            return self.textos.keys()
        clave = max(trozos, key=len)
        out = set()
        for w, rels in self.donde.items():
            if clave in w:
                out |= rels
        return out

    def grep(self, termino, palabra=False):
        if palabra and re.fullmatch(r"[A-Za-z0-9_]+", termino):
            return [rel for rel, ws in self.palabras.items() if termino in ws]
        if palabra:
            pat = re.compile(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(termino))
            return [rel for rel, t in self.textos.items() if pat.search(t)]
        return sorted(rel for rel in self._candidatos(termino) if termino in self.textos[rel])


def _ultimos_commits(carpeta_rel):
    """{ruta: fecha del último commit} de toda una carpeta en UNA llamada a git."""
    r = subprocess.run(["git", "-C", REPO, "log", "--format=@%as", "--name-only", "--",
                        carpeta_rel], capture_output=True, text=True)
    fechas, actual = {}, None
    for linea in (r.stdout or "").splitlines():
        if linea.startswith("@"):
            actual = linea[1:]
        elif linea.strip() and actual:
            fechas.setdefault(linea.strip(), actual)
    return fechas


def clasificar(nombre, por_daemon, carpeta_rel="tools", grep=None):
    """Devuelve (estado, quién la nombra). `nombre` es el fichero, p.ej. `onco.py`.

    La extensión se quita SIEMPRE, no solo a los `.py`: un agente se invoca por su nombre a
    secas (`subagent_type="diseno"`), y buscando «diseno.md» no aparece nadie. Con el corte
    solo para `.py`, 27 de los 33 agentes salían huérfanos siendo todos usados."""
    grep = grep or _grep
    modulo = os.path.splitext(nombre)[0]
    rel = os.path.join(carpeta_rel, nombre)
    citas = set()
    for termino in (nombre, "import %s" % modulo, "tools.%s" % modulo):
        for f in grep(termino):
            if f != rel:
                citas.add(f)
    for f in grep(modulo, palabra=True):      # el módulo desnudo: «cn_fetch» en el panel
        if f != rel:
            citas.add(f)
    propio_test = {c for c in citas if os.path.basename(c) in ("test_%s.py" % modulo,)}
    ajenas = citas - propio_test
    if nombre in por_daemon:
        return "entrada", sorted(citas)
    if ajenas:
        return "viva", sorted(ajenas)
    if propio_test:
        return "solo-test", sorted(propio_test)
    return "huerfana", []


def inventario(carpeta=TOOLS, sufijo=".py"):
    por_daemon = _daemons()
    carpeta_rel = os.path.relpath(carpeta, REPO)
    indice = _Indice()
    commits = _ultimos_commits(carpeta_rel)
    out = []
    for nombre in _ficheros(carpeta, sufijo):
        if SALTAR.match(nombre):
            continue
        estado, quien = clasificar(nombre, por_daemon, carpeta_rel, grep=indice.grep)
        rel = os.path.join(carpeta_rel, nombre)
        out.append({"pieza": nombre, "estado": estado, "citada_por": quien[:6],
                    "citas": len(quien), "ultimo_commit": commits.get(rel, "?")})
    return out


def _fecha_commit(valor):
    """Convierte YYYY-MM-DD a date; devuelve None para fechas no disponibles."""
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def herramientas_viejas(n_dias, filas=None, hoy=None):
    """Filtra herramientas cuyo último commit supera N días.

    Reutiliza la clasificación existente y ordena de más vieja a más nueva.
    Clasificar no implica borrar ni sugiere retirar herramientas.
    """
    if n_dias < 0:
        raise ValueError("N debe ser >= 0")
    hoy = hoy or date.today()
    filas = inventario() if filas is None else filas
    viejas = []
    for fila in filas:
        fecha = _fecha_commit(fila.get("ultimo_commit"))
        if fecha is None:
            continue
        dias = (hoy - fecha).days
        if dias > n_dias:
            copia = dict(fila)
            copia["dias_sin_tocar"] = dias
            viejas.append(copia)
    return sorted(viejas, key=lambda fila: (-fila["dias_sin_tocar"], fila["pieza"]))


def imprimir_herramientas_viejas(n_dias, filas=None):
    """Imprime la salida humana de --viejas N."""
    viejas = herramientas_viejas(n_dias, filas=filas)
    if not viejas:
        print("No hay herramientas sin tocar en más de %d días." % n_dias)
        return
    print("Herramientas sin tocar en más de %d días:" % n_dias)
    for fila in viejas:
        print("   %-34s %4d días  [%s]" % (
            fila["pieza"], fila["dias_sin_tocar"], fila["estado"]))
    print("\nClasificar no es borrar: esta salida no sugiere retirar nada.")


def _modelos_ollama(salida):
    """Extrae los nombres de modelo de la primera columna de `ollama list`."""
    lineas = [linea for linea in salida.splitlines() if linea.strip()]
    if lineas and lineas[0].split()[0].upper() == "NAME":
        lineas = lineas[1:]
    return [linea.split()[0] for linea in lineas if linea.split()]


def _nombres_citables(modelo):
    """Cómo puede aparecer un modelo en el código: entero, y sin `:latest` si lo lleva
    (`ollama pull nomic-embed-text` descarga `nomic-embed-text:latest`)."""
    m = modelo.lower()
    return (m, m[:-len(":latest")]) if m.endswith(":latest") else (m,)


def modelos_no_usados(ollama_salida=None, codigo=None):
    """Modelos descargados en Ollama que ningún fichero del repo nombra (issue #5).

    Primera versión: PR #23 de j7j7j7 (24-sep-2026). Incorporada con dos cambios:
      · se busca CADA modelo por su nombre. El PR prefiltraba con una lista cerrada de familias
        (llama|mistral|contacto|phi|qwen|deepseek|codellama), así que un modelo de otra familia
        (`nomic-embed-text`) salía «no usado» aunque el código lo citara;
      · si `git grep` falla de verdad (rc > 1), se dice. Antes el fallo daba un código vacío y
        todos los modelos salían «no usados»: justo el fail-open que el issue quería evitar.
    Los parámetros opcionales permiten probarlo sin llamar a Ollama ni a git."""
    if ollama_salida is None:
        try:
            resultado = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, check=True
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ollama no está instalado o no está en PATH") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("ollama list falló (código %d)" % exc.returncode) from exc
        ollama_salida = resultado.stdout

    modelos = _modelos_ollama(ollama_salida)
    if codigo is None:
        terminos = sorted({t for m in modelos for t in _nombres_citables(m)})
        if not terminos:
            return []
        orden = ["git", "-C", REPO, "grep", "-h", "-I", "-i", "-o", "-F"]
        for t in terminos:
            orden += ["-e", t]
        resultado = subprocess.run(orden + ["--"] + list(AMBITOS), capture_output=True, text=True)
        if resultado.returncode > 1:          # 1 = ninguna coincidencia, que es un resultado
            raise RuntimeError("git grep falló (código %d): %s" % (
                resultado.returncode, (resultado.stderr or "").strip()[:200]))
        codigo = resultado.stdout

    codigo = codigo.lower()
    return [m for m in modelos if not any(t in codigo for t in _nombres_citables(m))]


def imprimir_modelos_no_usados():
    """Imprime --modelos; devuelve 1 si Ollama no puede consultarse."""
    try:
        no_usados = modelos_no_usados()
    except RuntimeError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    if not no_usados:
        print("Todos los modelos descargados están mencionados en el código.")
        return 0
    print("Modelos descargados pero no mencionados en el código:")
    for modelo in no_usados:
        print("   - %s" % modelo)
    return 0


# ─── uso real y ciclo de vida (25-sep-2026) ──────────────────────────────────
# Idea de {{CONTACTO}} (https://contacto), con su agente KAI, revisión del 25-sep-2026:
# el uso lo rellena un script cada noche con trazas reales, nunca el modelo; a los 60 días sin
# uso, observación; a los 90, propuesta de retiro. Clasificar no es borrar: la retirada la
# ejecuta una sesión en rama (git mv a tools/retired/) con el OK de {{TITULAR}}, nunca este script.

PROYECTOS = os.environ.get("BTP_PROJECTS_DIR") or os.path.expanduser("~/.claude/projects")
CASA = os.environ.get("BTP_CASA_BASE") or os.path.expanduser("~/claudecode")
# Estado vivo en casa base aunque se lance desde un worktree (mismo criterio que anatomia.py).
ESTADO = os.environ.get("BTP_STATE_DIR") or os.path.join(
    CASA if os.path.isdir(os.path.join(CASA, "tools", "state")) else REPO, "tools", "state")
USO_JSON = "uso_piezas.json"
DIAS_OBSERVACION, DIAS_RETIRO = 60, 90

# Una pieza se USA cuando se ejecuta, no cuando se lee: `cat tools/x.py` no cuenta.
_RE_LANZA = re.compile(
    r"(?:python3?(?:\.\d+)?|/python|bash|sh|zsh)\s+(?:-[\w-]+\s+)*[\"']?(?:[\w./~-]*/)?"
    r"tools/([\w/-]+\.(?:py|sh))")
_RE_DIRECTO = re.compile(r"(?:^|[;&|(]\s*|&&\s*|\bthen\s+|\bdo\s+)[\"']?(?:[\w./~-]*/)?"
                         r"tools/([\w/-]+\.(?:py|sh))")
_RE_MODULO = re.compile(r"python3?\s+-m\s+tools\.(\w+)")


def piezas_lanzadas(comando):
    """Piezas (relativas a tools/) que un comando de shell ejecuta."""
    out = {m.group(1) for m in _RE_LANZA.finditer(comando or "")}
    out |= {m.group(1) for m in _RE_DIRECTO.finditer(comando or "")}
    out |= {m.group(1) + ".py" for m in _RE_MODULO.finditer(comando or "")}
    return out


def _usos_sesiones(dias, raiz=None):
    """{pieza: último ts} de los comandos Bash de los transcripts de los últimos `dias`."""
    raiz = raiz or PROYECTOS
    corte = datetime.now().timestamp() - dias * 86400
    vistos = {}
    for d, _, files in os.walk(raiz):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            ruta = os.path.join(d, fn)
            try:
                if os.path.getmtime(ruta) < corte:
                    continue
                fh = open(ruta, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for linea in fh:
                    if '"Bash"' not in linea or "tools" not in linea:
                        continue
                    try:
                        ev = json.loads(linea)
                    except ValueError:
                        continue
                    ts = (ev.get("timestamp") or "")[:19]
                    contenido = (ev.get("message") or {}).get("content")
                    if not ts or not isinstance(contenido, list):
                        continue
                    for c in contenido:
                        if isinstance(c, dict) and c.get("type") == "tool_use" \
                                and c.get("name") == "Bash":
                            for p in piezas_lanzadas((c.get("input") or {}).get("command")):
                                if ts > vistos.get(p, ""):
                                    vistos[p] = ts
    return vistos


def _usos_daemons():
    """{pieza: última escritura de su log}: lo que launchd ejecutó, por la hora de su log."""
    import plistlib
    vistos = {}
    d = os.path.join(TOOLS, "launchd")
    for f in _ficheros(d, ".plist"):
        try:
            with open(os.path.join(d, f), "rb") as fh:
                pl = plistlib.load(fh)
        except Exception:
            continue
        logs = [pl.get("StandardOutPath"), pl.get("StandardErrorPath")]
        mt = max((os.path.getmtime(l) for l in logs if l and os.path.exists(l)), default=None)
        if mt is None:
            continue
        ts = datetime.fromtimestamp(mt).strftime("%Y-%m-%dT%H:%M:%S")
        for arg in pl.get("ProgramArguments") or []:
            m = re.search(r"tools/([\w/-]+\.(?:py|sh))$", str(arg))
            if m and ts > vistos.get(m.group(1), ""):
                vistos[m.group(1)] = ts
    return vistos


def _usos_trazas(dias):
    """{pieza: último ts} de las trazas de observabilidad cuyo job o agente es una pieza."""
    vistos = {}
    d = os.path.join(ESTADO, "observabilidad")
    corte = (datetime.now().date().toordinal() - dias)
    for f in _ficheros(d, ".jsonl"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", f)
        if not m or datetime.strptime(m.group(1), "%Y-%m-%d").date().toordinal() < corte:
            continue
        try:
            with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                for linea in fh:
                    try:
                        ev = json.loads(linea)
                    except ValueError:
                        continue
                    ts = (ev.get("ts_fin") or ev.get("ts_ini") or "")[:19]
                    for k in ("job", "agente"):
                        nombre = str(ev.get(k) or "")
                        for p in (nombre + ".py", nombre + ".sh"):
                            if os.path.isfile(os.path.join(TOOLS, p)) and ts > vistos.get(p, ""):
                                vistos[p] = ts
        except OSError:
            continue
    return vistos


def leer_uso(estado=None):
    try:
        with open(os.path.join(estado or ESTADO, USO_JSON), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"_meta": {}, "piezas": {}}


def actualizar_uso(dias=2, estado=None, fuentes=None, hoy=None):
    """Suma lo visto a `uso_piezas.json`. El último uso NUNCA retrocede: los transcripts se
    purgan a ~30 días, así que lo acumulado es la única memoria de uso más larga que eso.
    `fecha_inicio_medida` = desde cuándo hay datos: antes de ella, «sin uso» es «sin dato»."""
    estado = estado or ESTADO
    hoy = hoy or datetime.now()
    if fuentes is None:
        fuentes = {"sesion": _usos_sesiones(dias), "daemon": _usos_daemons(),
                   "traza": _usos_trazas(dias)}
    uso = leer_uso(estado)
    meta = uso.setdefault("_meta", {})
    meta.setdefault("fecha_inicio_medida",
                    datetime.fromordinal(hoy.toordinal() - dias).strftime("%Y-%m-%d"))
    meta["actualizado"] = hoy.strftime("%Y-%m-%dT%H:%M:%S")
    meta["escribe"] = "tools/inventario.py --uso (nunca un modelo a mano)"
    piezas = uso.setdefault("piezas", {})
    nuevas = 0
    for fuente, vistos in fuentes.items():
        for pieza, ts in vistos.items():
            if not os.path.isfile(os.path.join(TOOLS, pieza)):
                continue                  # una ruta que ya no es pieza (o nunca lo fue)
            prev = piezas.get(pieza) or {}
            if ts > (prev.get("ultimo_uso") or ""):
                piezas[pieza] = {"ultimo_uso": ts, "fuente": fuente}
                nuevas += 1
    os.makedirs(estado, exist_ok=True)
    tmp = os.path.join(estado, USO_JSON + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(uso, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(estado, USO_JSON))
    return uso, nuevas


def ciclo(hoy=None, uso=None, filas=None, observacion=DIAS_OBSERVACION, retiro=DIAS_RETIRO):
    """Qué piezas llevan demasiado sin usarse. Devuelve filas con `fase`:
    `observacion` (≥60 días) o `propuesta-retiro` (≥90 días Y huérfana). Las `entrada`
    (las lanza launchd o son CLI documentada) no entran nunca: su cero lo vigila otro.
    Los días se cuentan desde lo más reciente de: último uso, último commit e inicio de
    la medida; así nada se da por muerto por falta de datos."""
    hoy = hoy or date.today()
    uso = uso if uso is not None else leer_uso()
    if filas is None:
        filas = inventario() + inventario(TOOLS, ".sh")
    inicio = (uso.get("_meta") or {}).get("fecha_inicio_medida") or hoy.isoformat()
    usos = uso.get("piezas") or {}

    def _u(pieza):
        return ((usos.get(pieza) or {}).get("ultimo_uso") or "")[:10]

    out = []
    for f in filas:
        citas = f.get("citada_por") or []
        # entrada: la lanza launchd o es CLI documentada. Citada por un hook: corre en cada
        # sesión aunque ningún comando la nombre. Ninguna de las dos entra en el ciclo.
        if f["estado"] == "entrada" or any(c.startswith(".claude/hooks/") for c in citas):
            continue
        # Una librería se usa cuando se usa quien la importa: hereda su último uso.
        u = max([_u(f["pieza"])] + [_u(c[len("tools/"):]) for c in citas
                                    if c.startswith("tools/")])
        ref = max(x for x in (u, f.get("ultimo_commit") or "", inicio) if x and x != "?")
        dias = (hoy - _fecha_commit(ref)).days if _fecha_commit(ref) else 0
        fase = None
        if dias >= retiro and f["estado"] in ("huerfana", "solo-test"):
            fase = "propuesta-retiro"
        elif dias >= observacion:
            fase = "observacion"
        if fase:
            fila = dict(f, fase=fase, dias_sin_uso=dias, ultimo_uso=u or None)
            out.append(fila)
    return sorted(out, key=lambda x: (-x["dias_sin_uso"], x["pieza"]))


def _solapes(pieza, n=3):
    try:
        import fichas
        return [p for p, _ in fichas.parecidas(pieza, n=n + 1) if p != pieza][:n]
    except Exception:
        return []


def proponer_ciclo(filas, estado=None):
    """Propone a Vega un hilo con las piezas NUEVAS en observación o retiro (no crea tarjeta:
    el gestor decide). Anti-spam: solo si la lista cambió desde la última propuesta."""
    estado = estado or ESTADO
    clave = sorted("%s:%s" % (f["pieza"], f["fase"]) for f in filas)
    marca = os.path.join(estado, "ciclo_piezas.json")
    try:
        with open(marca, encoding="utf-8") as fh:
            if json.load(fh).get("clave") == clave:
                return False
    except (OSError, ValueError):
        pass
    if filas:
        os.makedirs(os.path.join(estado, "vega"), exist_ok=True)
        with open(os.path.join(estado, "vega", "propuestas_hilos.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "titulo": "Ciclo de vida de tools: %d en observación, %d con retiro propuesto" % (
                    sum(f["fase"] == "observacion" for f in filas),
                    sum(f["fase"] == "propuesta-retiro" for f in filas)),
                "origen": "inventario.py --ciclo",
                "nota": "clasificar no es borrar; retirar = rama + git mv a tools/retired/ + OK",
                "piezas": [{"pieza": f["pieza"], "fase": f["fase"], "dias": f["dias_sin_uso"],
                            "solapes": _solapes(f["pieza"])} for f in filas],
            }, ensure_ascii=False) + "\n")
    with open(marca, "w", encoding="utf-8") as fh:
        json.dump({"clave": clave, "ts": datetime.now().isoformat(timespec="seconds")}, fh)
    return bool(filas)


def imprimir_ciclo(filas):
    if not filas:
        print("Ninguna pieza lleva %d días sin uso." % DIAS_OBSERVACION)
        return
    for fase, icono in (("propuesta-retiro", "🟠"), ("observacion", "👀")):
        grupo = [f for f in filas if f["fase"] == fase]
        if grupo:
            print("%s %s — %d" % (icono, fase, len(grupo)))
            for f in grupo:
                sol = _solapes(f["pieza"])
                print("   %-32s %4d días [%s]%s" % (f["pieza"], f["dias_sin_uso"], f["estado"],
                      ("  ~ " + ", ".join(sol)) if sol else ""))
    print("\nClasificar no es borrar: retirar es rama + git mv a tools/retired/ con OK.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Qué piezas están vivas y cuáles no llama nadie")
    p.add_argument("--huerfanas", action="store_true")
    p.add_argument("--agentes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--viejas", type=int, metavar="N",
                   help="herramientas cuyo último commit tiene más de N días")
    p.add_argument("--modelos", action="store_true",
                   help="modelos de Ollama descargados pero no mencionados en el repo")
    p.add_argument("--uso", action="store_true",
                   help="acumula el uso real (sesiones, launchd, trazas) en state/uso_piezas.json")
    p.add_argument("--dias", type=int, default=2, help="ventana de --uso (por defecto 2)")
    p.add_argument("--ciclo", action="store_true",
                   help="piezas con 60 días sin uso (observación) o 90 y huérfanas (retiro)")
    p.add_argument("--proponer", action="store_true",
                   help="con --ciclo: propone a Vega un hilo si la lista cambió")
    p.add_argument("--noche", action="store_true",
                   help="la pasada del daemon: --uso y luego --ciclo --proponer")
    a = p.parse_args(argv)

    if a.noche:
        uso, nuevas = actualizar_uso(a.dias)
        filas = ciclo(uso=uso)
        propuso = proponer_ciclo(filas)
        print("%s noche: %d con uso, %d actualizada(s), %d en el ciclo%s" % (
            datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), len(uso["piezas"]), nuevas,
            len(filas), " · propuesto a Vega" if propuso else ""))
        return 0

    if a.uso:
        uso, nuevas = actualizar_uso(a.dias)
        print("📈 uso_piezas.json: %d pieza(s) con uso registrado (%d actualizada(s)); medida "
              "desde %s" % (len(uso["piezas"]), nuevas, uso["_meta"]["fecha_inicio_medida"]))
        return 0
    if a.ciclo:
        filas = ciclo()
        if a.json:
            print(json.dumps(filas, ensure_ascii=False, indent=1))
        else:
            imprimir_ciclo(filas)
        if a.proponer:
            proponer_ciclo(filas)
        return 0

    if a.modelos:
        return imprimir_modelos_no_usados()

    filas = inventario(AGENTES, ".md") if a.agentes else inventario()
    if a.viejas is not None:
        if a.agentes:
            p.error("--viejas solo aplica a tools")
        try:
            imprimir_herramientas_viejas(a.viejas, filas=filas)
        except ValueError as exc:
            p.error(str(exc))
        return 0
    if a.huerfanas:
        filas = [f for f in filas if f["estado"] in ("huerfana", "solo-test")]
    if a.json:
        print(json.dumps(filas, ensure_ascii=False, indent=1))
        return 0

    por_estado = {}
    for f in filas:
        por_estado.setdefault(f["estado"], []).append(f)
    print("📦 %d pieza(s) en %s" % (len(filas), "agentes" if a.agentes else "tools"))
    for estado in ("huerfana", "solo-test", "entrada", "viva"):
        grupo = por_estado.get(estado) or []
        if not grupo:
            continue
        print("\n%s %s — %d" % ({"huerfana": "🔴", "solo-test": "🟠",
                                 "entrada": "⏰", "viva": "🟢"}[estado], estado, len(grupo)))
        if estado in ("huerfana", "solo-test") or a.huerfanas:
            for f in sorted(grupo, key=lambda x: x["ultimo_commit"]):
                print("   %-34s último commit %s%s" % (
                    f["pieza"], f["ultimo_commit"],
                    ("  ← " + f["citada_por"][0]) if f["citada_por"] else ""))
    print("\nClasificar no es borrar: una huérfana puede ser algo nuevo a medio cablear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
