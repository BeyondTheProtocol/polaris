#!/usr/bin/env python3
"""tools/inventario.py — qué piezas de Polaris están VIVAS y cuáles no las llama nadie.

POR QUÉ (19-sep-2026, problema nº2 de `docs/lo-que-falta.md`). Hay 189 herramientas, 33 agentes
y 68 daemons, y el catálogo crece más rápido que la memoria de nadie. El auditor vigila los
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


def clasificar(nombre, por_daemon, carpeta_rel="tools"):
    """Devuelve (estado, quién la nombra). `nombre` es el fichero, p.ej. `onco.py`.

    La extensión se quita SIEMPRE, no solo a los `.py`: un agente se invoca por su nombre a
    secas (`subagent_type="diseno"`), y buscando «diseno.md» no aparece nadie. Con el corte
    solo para `.py`, 27 de los 33 agentes salían huérfanos siendo todos usados."""
    modulo = os.path.splitext(nombre)[0]
    rel = os.path.join(carpeta_rel, nombre)
    citas = set()
    for termino in (nombre, "import %s" % modulo, "tools.%s" % modulo):
        for f in _grep(termino):
            if f != rel:
                citas.add(f)
    for f in _grep(modulo, palabra=True):
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
    out = []
    for nombre in _ficheros(carpeta, sufijo):
        if SALTAR.match(nombre):
            continue
        estado, quien = clasificar(nombre, por_daemon, os.path.relpath(carpeta, REPO))
        out.append({"pieza": nombre, "estado": estado, "citada_por": quien[:6],
                    "citas": len(quien), "ultimo_commit": _ultimo_commit(
                        os.path.join(os.path.relpath(carpeta, REPO), nombre))})
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


def modelos_no_usados(ollama_salida=None, codigo=None):
    """Devuelve modelos descargados en Ollama que no se mencionan literalmente.

    Los parámetros opcionales permiten pruebas sin invocar Ollama ni Git.
    """
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

    if codigo is None:
        resultado = subprocess.run(
            ["git", "-C", REPO, "grep", "-h", "-I", "-E",
             r"(llama|mistral|gemma|phi|qwen|deepseek|codellama)", "--", *AMBITOS],
            capture_output=True, text=True
        )
        codigo = resultado.stdout

    codigo = codigo.lower()
    return [modelo for modelo in _modelos_ollama(ollama_salida) if modelo.lower() not in codigo]


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


def main(argv=None):
    p = argparse.ArgumentParser(description="Qué piezas están vivas y cuáles no llama nadie")
    p.add_argument("--huerfanas", action="store_true")
    p.add_argument("--agentes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--viejas", type=int, metavar="N",
                   help="herramientas cuyo último commit tiene más de N días")
    p.add_argument("--modelos", action="store_true",
                   help="modelos de Ollama descargados pero no mencionados en el repo")
    a = p.parse_args(argv)

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
