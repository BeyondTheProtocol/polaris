#!/usr/bin/env python3
"""copy_web_guard.py — el copy PUBLICADO de la web no se cambia sin su OK; lo nuevo sí es mío.

NORMA (registro: tools/normas.json) `feedback-no-tocar-copy-web-sin-ok`, clase BLOQUEO:
«cambiar copy EXISTENTE de la web (sobre todo el hero/H1) NO es zona autónoma — requiere OK
explícito y bien entendido de {{TITULAR}}; páginas/secciones NUEVAS sí son autónomas».

LA PRUEBA ESTÁ EN SU PROPIO HISTORIAL. En ~/projects/titular-{{APELLIDO}}-case, 90 commits tocan
`i18n/locales/`, y ahí están los dos pares delito→arrepentimiento que originaron la norma:
  · 26514d8 «fix(hero): el H1 nombra el hecho clínico» → 02ccb94 «revert(hero): vuelve el H1
    y subtítulo al original (deshace #132)».
  · ff36416 «fix(team): subtítulo dice "Una ingeniera", no "Una paciente"» → 57a12fc Revert.
Cambiar copy publicado y deshacerlo es caro y se ve desde fuera.

POR QUÉ EL GATE VA EN EL COMMIT Y NO EN LA EDICIÓN. Lo obvio sería un PreToolUse sobre
Write/Edit, y no serviría: medido sobre TODOS los transcripts, hay **0** llamadas a Write/Edit
contra `i18n/locales` o `content/`, y **23 comandos Bash** que las tocan con `python3 - <<EOF`,
`sed -i` y redirecciones. Un gate sobre la tool escritora vigilaría una puerta por la que no
pasa nadie. Sobre el DIFF DE GIT da igual cómo se escribió el fichero: el registro ya lo
proponía así («bloquea merge de copy publicado sin OK»).

DOS NIVELES, Y LA RAZÓN DE QUE NO SEA UNO. Con «deniega cualquier cambio de copy existente»
el gate saltaría en **59 de 86** commits reales del repo (medido replicando su historial), y un
gate que se esquiva siempre enseña a esquivarlo. Con `ask` tampoco: este repo YA lo probó el
25-jul-26 y lo dejó escrito en `regla_en_accion.py` — «con `ask`, la escritura pasó … el `ask`
se resolvió solo sin que nadie viera nada». Lo que sí está comprobado ahí mismo es que `exit 2`
frena un `Bash`, que es justo donde vive este gate. Así que:

  · FRENA (exit 2) — 27 de 86 commits reales:
      · cualquier clave de HERO/H1 (`hero.*`, `*hero_*`, `*.h1`) cuyo VALOR cambie. Es lo que
        ella subraya, y su historial tiene DOS pares cambio→revert del hero: 26514d8 → 02ccb94
        y 4187131 → 4084efa. Cambiar el H1 y deshacerlo ya pasó dos veces.
      · cualquier clave BORRADA. Quitar copy publicado no se deshace mirando el diff.
  · AVISA, NO FRENA (exit 0 + additionalContext) — 32 de 86:
      · otros valores de copy existente, listados por clave. Se ven en el momento de commitear.
        Esto NO es un bloqueo y no se cuenta como tal en el registro.
  · PASA EN SILENCIO — 27 de 86:
      · claves NUEVAS, ficheros de `content/` nuevos, y ficheros de `content/` donde el diff
        solo añade líneas. Páginas y secciones nuevas SON zona autónoma, lo dice la norma.

ÁMBITO: cualquier clon del repo web (se reconoce por `nuxt.config.ts` + `i18n/locales/`), así
que cubre también los `~/projects/.mgc-staging/*` donde trabajan los agentes.

FAIL-OPEN, como sus hermanos. Si el JSON no parsea, si git no contesta o si algo revienta, NO
bloquea: lo que este guard no pueda juzgar, no lo juzga.
Escotilla explícita: `BTP_COPY_OK=1`.

LÍMITE: el copy de las páginas legales (`app/pages/aviso-legal.vue`, `cookies.vue`,
`privacidad.vue`) está escrito a mano en el .vue, no en i18n, y ahí no llega este guard: meter
`app/**/*.vue` entero haría saltar el gate en cada cambio de código.

Contrato de hooks (code.claude.com/docs/hooks): exit 0 permite · exit 2 deniega.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from singleton_guard import subcomandos, _git_sub  # noqa: E402  (mismo tokenizador)

LOCALES = os.path.join("i18n", "locales")
# Estricto a propósito: un `"title" in clave` pegaba con `nav.title`, `press.title`…
# y convertía «el hero» en «la mitad del fichero».


def _es_repo_web(raiz):
    return bool(raiz) and os.path.isfile(os.path.join(raiz, "nuxt.config.ts")) \
        and os.path.isdir(os.path.join(raiz, LOCALES))


def _raiz_repo(d):
    try:
        p = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        return os.path.abspath(p.stdout.strip()) if p.returncode == 0 else None
    except Exception:
        return None


def _git(raiz, *args):
    try:
        p = subprocess.run(["git", "-C", raiz] + list(args), capture_output=True, text=True, timeout=10)
        return p.stdout if p.returncode == 0 else None
    except Exception:
        return None


def _aplana(o, pre=""):
    """dict anidado → {ruta.con.puntos: texto}. Solo hojas de texto: eso es el copy."""
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_aplana(v, "%s.%s" % (pre, k) if pre else str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(_aplana(v, "%s[%d]" % (pre, i)))
    elif isinstance(o, str):
        out[pre] = o
    return out


def compara_locale(texto_viejo, texto_nuevo):
    """(cambiadas, borradas, nuevas) entre dos versiones de un locale. None si no se puede juzgar."""
    try:
        viejo = _aplana(json.loads(texto_viejo))
        nuevo = _aplana(json.loads(texto_nuevo))
    except Exception:
        return None
    cambiadas = [k for k in viejo if k in nuevo and viejo[k] != nuevo[k]]
    borradas = [k for k in viejo if k not in nuevo]
    nuevas = [k for k in nuevo if k not in viejo]
    return cambiadas, borradas, nuevas


def solo_anade(diff):
    """True si el diff unificado de un fichero de texto solo AÑADE líneas."""
    for linea in diff.splitlines():
        if linea.startswith("---") or linea.startswith("+++"):
            continue
        if linea.startswith("-"):
            return False
    return True


def _es_hero(clave):
    c = clave.lower()
    return c.startswith("hero.") or "hero_" in c or c.endswith(".h1") or ".h1." in c


def revisa_commit(raiz, incluye_no_indexado):
    """None, o (fichero, claves_que_frenan, claves_que_solo_avisan, frena)."""
    avisos = []
    rangos = ["--cached"] + ([""] if incluye_no_indexado else [])
    ficheros = set()
    for r in rangos:
        args = ["diff", "--name-only"] + ([r] if r else [])
        out = _git(raiz, *args)
        if out:
            ficheros.update(x.strip() for x in out.splitlines() if x.strip())
    for f in sorted(ficheros):
        if f.startswith(LOCALES) and f.endswith(".json"):
            viejo = _git(raiz, "show", "HEAD:%s" % f)
            if viejo is None:
                continue                       # locale nuevo: todo es nuevo → pasa
            nuevo = _git(raiz, "show", ":%s" % f)
            if nuevo is None or not nuevo.strip():
                try:
                    with open(os.path.join(raiz, f), encoding="utf-8") as fh:
                        nuevo = fh.read()
                except Exception:
                    continue
            res = compara_locale(viejo, nuevo)
            if res is None:
                continue                       # no parsea → no se juzga (fail-open)
            cambiadas, borradas, _nuevas = res
            if cambiadas or borradas:
                frenan = [k for k in cambiadas if _es_hero(k)] + borradas
                if frenan:
                    return f, frenan, [k for k in cambiadas if not _es_hero(k)], True
                avisos.append((f, cambiadas))
        elif f.startswith("content" + os.sep):
            viejo = _git(raiz, "show", "HEAD:%s" % f)
            if viejo is None:
                continue                       # fichero de contenido NUEVO → página nueva → pasa
            diff = _git(raiz, "diff", "--cached", "--unified=0", "--", f) or ""
            if diff and not solo_anade(diff):
                avisos.append((f, ["(texto modificado o borrado)"]))
    if avisos:
        f, claves = avisos[0]
        return f, [], claves, False
    return None


def main():
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return 0
    if os.environ.get("BTP_COPY_OK") == "1":
        return 0
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        return 0
    aqui = data.get("cwd")
    for sub in subcomandos(command):
        binario = os.path.basename(sub[0])
        if binario == "cd":
            d = next((a for a in sub[1:] if not a.startswith("-")), None)
            if d and "$" not in d:
                d = os.path.expanduser(d)
                aqui = d if os.path.isabs(d) else os.path.join(aqui or "", d)
            continue
        if binario != "git":
            continue
        args = sub[1:]
        destino = aqui
        for i, a in enumerate(args):
            if a == "-C" and i + 1 < len(args):
                destino = os.path.expanduser(args[i + 1])
        sg, resto = _git_sub(args)
        if sg != "commit" or not destino:
            continue
        raiz = _raiz_repo(destino)
        if not _es_repo_web(raiz):
            continue                            # no es la web: esta norma no aplica
        con_a = any(a.startswith("-") and not a.startswith("--") and "a" in a[1:] for a in resto) \
            or "--all" in resto
        hallazgo = revisa_commit(raiz, con_a)
        if not hallazgo:
            continue
        fichero, frenan, avisan, frena = hallazgo
        if not frena:
            # AVISO, no bloqueo: se pone delante de los ojos en el momento de commitear.
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext":
                    "COPY ⚠️ este commit cambia copy YA PUBLICADO de la web (%s): %s%s.\n"
                    "Copy existente NO es zona autónoma: necesita el OK explícito de {{TITULAR}}. "
                    "Esto es un AVISO, no un bloqueo — si no te lo ha dado, no lo commitees."
                    % (fichero, ", ".join(avisan[:6]),
                       (" (+%d más)" % (len(avisan) - 6)) if len(avisan) > 6 else ""),
            }}, ensure_ascii=False))
            return 0
        hero = [k for k in frenan if _es_hero(k)]
        sys.stderr.write(
            "COPY ⛔ este commit toca copy publicado que NO es zona autónoma.\n"
            "   fichero:  %s\n"
            "%s"
            "   toca:     %s%s\n"
            "   Necesita el OK explícito y bien entendido de {{TITULAR}}. Añadir claves o páginas\n"
            "   NUEVAS sí es autónomo — eso pasa solo y en silencio.\n"
            "   Precedente: el hero se cambió y hubo que revertirlo DOS veces (26514d8→02ccb94,\n"
            "   4187131→4084efa).\n"
            "   Si ya te dio el OK para ESTE cambio: BTP_COPY_OK=1.\n"
            % (fichero,
               ("   ⚠️  toca el HERO/H1, que es lo que ella señala expresamente: %s\n"
                % ", ".join(hero[:4])) if hero else
               "   ⚠️  BORRA copy publicado.\n",
               ", ".join(frenan[:6]),
               (" (+%d más)" % (len(frenan) - 6)) if len(frenan) > 6 else ""))
        return 2
    return 0


if __name__ == "__main__":
    # Watchdog (25-sep-26): si tardo más que el timeout de settings (10 s), deniego yo antes de
    # que Claude Code me cancele y lo convierta en un permitir. Ver _watchdog.py.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:  # sin el vigilante el guard sigue siendo el guard: su falta no lo tumba
        import _watchdog
    except ImportError:
        _watchdog = None
        sys.stderr.write("copy_web_guard: falta _watchdog.py; sigo sin vigilante\n")
    if _watchdog:
        _watchdog.armar(10, 'copy_web_guard')
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)   # FAIL-OPEN deliberado (ver cabecera)
