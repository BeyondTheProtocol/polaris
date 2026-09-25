#!/usr/bin/env python3
"""Linter de honestidad — caza afirmaciones FUERTES sin sello de evidencia. Determinista, SIN LLM.

Origen ({{TITULAR}}, 28/6/26): el sistema le coló como HECHO un claim de red-team sin verificar
("{{CONTACTO}} = caja negra con telemetría sin documentar") y resultó FALSO. Regla suya, repetida:
**prefiero que digas "no lo sé / no lo puedo asegurar" antes que una falsa certeza.**
Memoria: feedback-honestidad-limites-avisar-no-inventar.

Qué hace: escanea un .md y marca cada línea que hace una AFIRMACIÓN FUERTE (absoluto, negativo
que mata una opción, certeza categórica) y NO lleva un SELLO de evidencia cerca. Sellos válidos:
  [verificado] [verificado: …] [fuente: …] [supuesto] [supuesto: …] [sin verificar] [inferido]
  …o una cita/URL en la propia línea (http…, NCT…, DOI, `ruta/fichero`, "según <fuente>").

Para qué sirve: que un SUPUESTO no se promueva a HECHO ni a decisión sin verificar. Lo corren
el red-team / auto-mejora / verificacion sobre docs de decisión, y va en test_all como guardia.

⚠️ LÍMITES (honestidad sobre el propio honestidad-linter, para no dar falsa confianza):
  · Caza una CLASE de patrón (afirmaciones categóricas), NO detecta toda falsedad. Una mentira
    redactada en tono suave y sin palabra-gatillo se le escapa. NO es una garantía.
  · Es ADVISORY por defecto (exit 0 + informe). Con --strict devuelve exit 1 si hay flags
    (úsalo cuando quieras que rompa el build).
  · La verdadera salvaguarda sigue siendo: VERIFICAR antes de afirmar + CITAR fuentes para que
    {{TITULAR}} pueda auditar. Esto es una capa más, no la única.

Uso:
  python3 tools/honestidad_lint.py --check <fichero.md> [--strict]
  python3 tools/honestidad_lint.py --repo [<raíz>] [--strict] [--desde-dias N] [--top N]
        # barre TODOS los .md bajo <raíz> (por defecto: 00_FUENTE-DE-VERDAD/), salvo los que se
        # EXIMEN a sí mismos en el frontmatter con `honestidad: exento`.
  python3 tools/honestidad_lint.py --selftest
"""
import os
import time
import re
import sys

# Disparadores de "afirmación fuerte" (es: + algunos en). Minúsculas; se busca como subcadena/palabra.
_TRIGGERS = [
    r"\bimposible\b", r"\bnunca\b", r"\bsiempre\b", r"\bjam[áa]s\b",
    r"\bgarantiza(?:do|mos)?\b", r"\b100\s*%\b", r"\bcero\s+(?:riesgo|fallos?|errores?)\b",
    r"\bsin\s+documentar\b", r"\bno\s+documentad", r"\bcaja\s+negra\b",
    r"\bno\s+soporta\b", r"\bno\s+funciona\b", r"\bno\s+tiene\b",
    r"\bdemostrad", r"\bprobad[oa]\s+que\b", r"\bseguro\s+que\b",
    r"\b(?:es|son)\s+(?:falso|falsos|falsa|falsas)\b",
    r"\btelemetr[íi]a\b", r"\bphone.?home\b", r"\bbackdoor\b",
    r"\bbypass\s+(?:total|imposible)\b", r"\bno\s+hay\s+(?:forma|manera)\b",
]
_TRIGGER_RE = re.compile("|".join(_TRIGGERS), re.I)

# Sellos de evidencia que "perdonan" la afirmación (la marcan como verificada o como supuesto
# declarado).
#
# Dos huecos cerrados el 25-jul-26, los dos verificados ejecutando el linter:
#  · `seg[úu]n\s+\w+` perdonaba CUALQUIER cosa con un «según X» delante. Probado:
#    «Según el red-team, X es una caja negra» → 0 flags; la misma frase sin el prefijo → 1.
#    Pero «según X» es exactamente el patrón de RELAY que CLAUDE.md prohíbe («nunca relayes
#    como HECHO un juicio de un doc o de un red-team sin verificarlo»), y es la formulación
#    literal del incidente que originó este tool. El linter premiaba el fallo que existe para
#    cazar. Fuera: si la fuente vale, se cita con [verificado: …] o con el enlace.
#  · `` `[^`]+` `` perdonaba cualquier backtick: bastaba poner una palabra en código para
#    silenciar la línea. Se acota a lo que de verdad es una referencia comprobable —una ruta
#    de fichero o un identificador con separador—, no una palabra suelta.
_SELLO_RE = re.compile(
    r"\[(?:verificado|fuente|supuesto|sin\s+verificar|inferido|estimado)\b[^\]]*\]"
    r"|https?://|\bNCT\d{6,}\b|\bdoi[:/]"
    r"|`[^`]*[/.][^`]*`",
    re.I,
)

# Líneas que NO se analizan: títulos, citas en bloque que ya marcan corrección/retractación, código.
_SKIP_PREFIX = ("#", "```", "> ⛔", "> 🔴", "~~")


def lint_text(texto):
    """Devuelve lista de (nº_línea, línea, gatillo) de afirmaciones fuertes sin sello."""
    flags = []
    en_codigo = False
    for i, ln in enumerate(texto.splitlines(), 1):
        s = ln.strip()
        if s.startswith("```"):
            en_codigo = not en_codigo
            continue
        if en_codigo or not s:
            continue
        if s.startswith(_SKIP_PREFIX):
            continue
        m = _TRIGGER_RE.search(s)
        if not m:
            continue
        if _SELLO_RE.search(s):
            continue  # tiene sello/cita → afirmación respaldada o declarada como supuesto
        flags.append((i, s, m.group(0)))
    return flags


def _check_file(ruta, strict):
    try:
        with open(ruta, encoding="utf-8") as f:
            texto = f.read()
    except OSError as e:
        print(f"honestidad_lint: no pude leer {ruta}: {e}", file=sys.stderr)
        return 2
    flags = lint_text(texto)
    if not flags:
        print(f"✅ {ruta}: sin afirmaciones fuertes sin sello.")
        return 0
    print(f"⚠️  {ruta}: {len(flags)} afirmación(es) fuerte(s) SIN sello de evidencia "
          f"(añade [verificado]/[fuente: …]/[supuesto: …] o una cita, o reescríbelo como duda):")
    for n, ln, trig in flags:
        recorte = (ln[:110] + "…") if len(ln) > 110 else ln
        print(f"  L{n} «{trig}» → {recorte}")
    # Advisory por defecto (exit 0); --strict rompe el build.
    return 1 if strict else 0


# ─── --repo: barrido de TODOS los .md (opt-OUT, 25-jul-26) ────────────────────────
# El repo de la fuente de verdad (00_FUENTE-DE-VERDAD/) está fuera del historial git (clínico/PII);
# por eso el barrido lee el filesystem en vivo, no `git ls-files`. Sin PyYAML (no es dependencia del
# proyecto): el frontmatter de este sistema es YAML simple `clave: valor` línea a línea, así que un
# regex por línea basta — evita meter una dependencia nueva para un caso de uso tan acotado.
#
# EL CONTRATO SE INVIRTIÓ (25-jul-26). Antes el barrido exigía `tipo: decision` en el frontmatter, y
# esa clave no existía en NINGUNA de las 23368 notas del repo: `--repo` recorría el disco entero para
# barrer cero documentos, mientras CLAUDE.md lo presentaba como el apoyo contra la falsa certeza.
# Y no iba a arreglarse solo, porque `archivar_nota.py` no escribe frontmatter: la cobertura futura
# también era cero. Un opt-in que nadie ejerce es una cobertura de mentira.
#
# Ahora barre TODO y el frontmatter sirve para EXIMIR (`honestidad: exento`), que es la dirección
# correcta: lo que se salta el linter tiene que ser una decisión escrita en el propio documento, no
# el silencio por defecto. Como barrer 23368 docs escupe miles de líneas, la salida por defecto es un
# RESUMEN por documento (los peores primero) en vez del volcado línea a línea de `--check`.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.S)
_EXENTO_RE = re.compile(r"^honestidad:\s*exento\s*$", re.I | re.M)
_REPO_RAIZ_DEFECTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "00_FUENTE-DE-VERDAD")

# Carpetas que NO son prosa del sistema y cuyo barrido solo generaría ruido: volcados crudos de
# terceros (WhatsApp, DMs, correo, YouTube) y la cajita. No son una exención de honestidad — son
# material EXTERNO, y una afirmación fuerte ahí es de quien la escribió, no del sistema.
# `_PRIVADO_NUCLEO` va fuera por otra razón: son las palabras de {{TITULAR}} sobre su propia vida. Este
# linter mide la honestidad del SISTEMA al afirmar cosas, no la forma en que ella escribe lo suyo.
_DIRS_FUERA = ("_PRIVADO_WHATSAPP", "_PRIVADO_DMS", "_PRIVADO_CORREO", "_PRIVADO_YT",
               "_PRIVADO_SESIONES", "_PRIVADO_NUCLEO", "_cajita", "node_modules", ".git")


def _es_exento(texto):
    """True si el frontmatter YAML (entre --- ---) al principio del fichero declara
    `honestidad: exento`. Sin frontmatter o sin esa clave → False (se barre)."""
    m = _FRONTMATTER_RE.match(texto)
    if not m:
        return False
    return bool(_EXENTO_RE.search(m.group(1)))


def _docs_a_barrer(raiz, desde_dias=None):
    """Devuelve (rutas_a_barrer, n_exentos, n_fuera). Recorre <raiz> y coge todos los .md salvo los
    exentos por frontmatter, los de `_DIRS_FUERA` y —si se pide `desde_dias`— los más viejos que esa
    ventana. Fail-soft por fichero (uno ilegible no tira el barrido); orden determinista."""
    out, exentos, fuera = [], 0, 0
    if not os.path.isdir(raiz):
        return out, exentos, fuera
    corte = (time.time() - desde_dias * 86400) if desde_dias else None
    for dirpath, dirnames, filenames in os.walk(raiz):
        podados = [d for d in dirnames if d in _DIRS_FUERA]
        fuera += len(podados)
        dirnames[:] = sorted(d for d in dirnames if d not in _DIRS_FUERA)
        for nombre in sorted(filenames):
            if not nombre.lower().endswith(".md"):
                continue
            ruta = os.path.join(dirpath, nombre)
            try:
                if corte is not None and os.path.getmtime(ruta) < corte:
                    continue
                with open(ruta, encoding="utf-8") as f:
                    texto = f.read()
            except OSError:
                continue
            if _es_exento(texto):
                exentos += 1
                continue
            out.append(ruta)
    return out, exentos, fuera


def _check_repo(raiz, strict, desde_dias=None, top=20):
    """Barre TODOS los .md no exentos bajo <raiz>. Devuelve exit 1 solo en --strict.

    Imprime un resumen por documento (los de más flags primero) y el recuento COMPLETO: si hay más
    documentos con flags que los que caben en `--top`, lo dice explícitamente en vez de dejar que el
    recorte se lea como «eso es todo». Con `--desde-dias N` solo mira lo tocado en los últimos N
    días, que es lo que tiene sentido correr a diario."""
    docs, exentos, _fuera = _docs_a_barrer(raiz, desde_dias)
    ventana = (" tocados en los últimos %d días" % desde_dias) if desde_dias else ""
    if not docs:
        print(f"✅ {raiz}: ningún .md que barrer{ventana}.")
        return 0
    con_flags, total_flags, ilegibles = [], 0, 0
    for ruta in docs:
        try:
            with open(ruta, encoding="utf-8") as f:
                flags = lint_text(f.read())
        except OSError:
            ilegibles += 1
            continue
        if flags:
            con_flags.append((len(flags), ruta, flags[0]))
            total_flags += len(flags)
    print("honestidad_lint --repo: %d doc(s)%s bajo %s (%d exento(s) por frontmatter)\n"
          % (len(docs), ventana, raiz, exentos))
    if not con_flags:
        print("✅ sin afirmaciones fuertes sin sello.")
        return 1 if (strict and ilegibles) else 0
    con_flags.sort(key=lambda x: (-x[0], x[1]))
    print("⚠️  %d doc(s) con %d afirmación(es) fuerte(s) SIN sello de evidencia:\n"
          % (len(con_flags), total_flags))
    for n, ruta, (ln, linea, trig) in con_flags[:top]:
        recorte = (linea[:90] + "…") if len(linea) > 90 else linea
        print(f"  {n:>3} flag(s)  {os.path.relpath(ruta, raiz)}")
        print(f"           L{ln} «{trig}» → {recorte}")
    if len(con_flags) > top:
        print("\n  …y %d doc(s) más con flags (sube --top para verlos; el recuento de arriba ya "
              "los incluye)." % (len(con_flags) - top))
    print("\nDetalle de uno: python3 tools/honestidad_lint.py --check \"<ruta>\"")
    return 1 if strict else 0


# ─── self-test (corre en tests/test_honestidad_lint.py) ──────────────────────────
_MALO = "El arnés tiene telemetría sin documentar y es una caja negra."
_BUENO_SELLO = "El arnés tiene telemetría sin documentar [supuesto: sin auditar el repo]."
_BUENO_VERIF = "Auditado el repo: cero telemetría [verificado: github.com/achetronic/contacto]."
_NEUTRO = "El arnés es local y corre en la terminal."


def _selftest():
    casos = [
        (_MALO, 1, "afirmación fuerte sin sello → 1 flag"),
        (_BUENO_SELLO, 0, "mismo claim con [supuesto] → 0 flags"),
        (_BUENO_VERIF, 0, "claim con [verificado]+fuente → 0 flags"),
        # Regresión 25-jul-26: «según X» perdonaba cualquier cosa, y «según X» es EXACTAMENTE
        # el patrón de relay que CLAUDE.md prohíbe y el que originó este tool.
        ("Según el red-team, el arnés tiene telemetría sin documentar.", 1,
         "relay «según X» ya NO perdona (era el hueco del incidente)"),
        # …y un backtick suelto tampoco: hacía falta poner una palabra en código para callarlo.
        ("Es una `caja` negra con telemetría sin documentar.", 1,
         "backtick de una palabra suelta ya NO perdona"),
        ("Tiene telemetría sin documentar (ver `tools/borde.py`).", 0,
         "backtick con ruta comprobable sí perdona"),
        (_NEUTRO, 0, "frase neutra → 0 flags"),
    ]
    fallos = 0
    for texto, esperado, desc in casos:
        got = len(lint_text(texto))
        ok = got == esperado
        print(f"  [{'OK' if ok else 'FAIL'}] {desc} (esperado {esperado}, got {got})")
        if not ok:
            fallos += 1
    if fallos:
        print(f"HONESTIDAD-LINT SELFTEST: {fallos} fallo(s)")
        return 1
    print("HONESTIDAD-LINT SELFTEST EN VERDE")
    return 0


def main(argv=None):
    argv = argv or sys.argv[1:]
    if "--selftest" in argv:
        return _selftest()
    if "--check" in argv:
        i = argv.index("--check")
        try:
            ruta = argv[i + 1]
        except IndexError:
            print("Uso: honestidad_lint.py --check <fichero.md> [--strict]", file=sys.stderr)
            return 2
        return _check_file(ruta, strict="--strict" in argv)
    if "--repo" in argv:
        i = argv.index("--repo")
        # <raíz> es opcional y posicional: el siguiente arg si no es otra flag; si no, el defecto.
        raiz = _REPO_RAIZ_DEFECTO
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            raiz = argv[i + 1]

        def _num(flag, defecto):
            if flag in argv:
                try:
                    return int(argv[argv.index(flag) + 1])
                except (IndexError, ValueError):
                    pass
            return defecto

        return _check_repo(raiz, strict="--strict" in argv,
                           desde_dias=_num("--desde-dias", None), top=_num("--top", 20))
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
