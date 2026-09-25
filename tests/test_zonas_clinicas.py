#!/usr/bin/env python3
"""test_zonas_clinicas.py — la fuente única de zonas clínicas.

Dos cosas hay que blindar a la vez, y son las que se contradicen: que las carpetas donde
vive el N2 se denieguen SIEMPRE (aunque no existan en esta máquina), y que el trabajo
normal del repo NO se paralice. Los falsos positivos de aquí no son teóricos: mientras se
auditaba esto, tres comandos legítimos (dos greps y un `ls -d`) fueron denegados por
MENCIONAR una ruta clínica, y uno de ellos fue el que destapó el problema.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("zonas")
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
import zonas_clinicas as ZC  # noqa: E402

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
HOME = os.path.expanduser("~")
_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # ── DEBE denegarse: donde vive de verdad el N2 ──────────────────────────────────
    deny = [
        os.path.join(REPO, "informes", "07-laboratorio", "x.pdf"),
        os.path.join(REPO, "informes"),
        os.path.join(REPO, "docu enviada a nova", "01_BIOPSIA_ONCO.pdf"),
        os.path.join(HOME, "Clinico-PRIVADO", "algo.md"),
        "/Users/polaris/Clinico-PRIVADO/algo.md",
        "/Users/polaris/DICOM_Seattle/serie1/IM0001.dcm",
        os.path.join(REPO, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "_PRIVADO_CLINICO", "a.md"),
        os.path.join(REPO, "00_FUENTE-DE-VERDAD", "05 · Legal-Finanzas", "_PRIVADO_EXPEDIENTE", "b.md"),
        os.path.join(REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_NUCLEO", "diario.md"),
        os.path.join(REPO, "_PRIVADO_SESIONES", "s.jsonl"),
        # Rutas de OTRA máquina, que no existen en este disco: el predicado no puede
        # depender del filesystem o se abriría solo al cambiar de ordenador.
        "/Users/otro-usuario/Library/Mobile Documents/.../MI VIDA/00_Salud/informe.pdf",
        # Se deniega por el PREFIJO genérico «historial clinico», no por el nombre de nadie:
        # por eso el caso se puede escribir sintético y sigue cubriendo lo mismo.
        "/Users/otro-usuario/claudecode/Historial clinico de la titular 2026/x.pdf",
        os.path.join(REPO, "{{CARPETA_PRUEBA}}", "cores.md"),
    ]
    # Las carpetas que se llaman con el nombre y la fecha de nacimiento de la titular NO
    # pueden vivir en un test versionado: se leen del overlay local, como hace el módulo.
    # Sin overlay el caso no corre, y se dice — mejor un hueco declarado que PHI en git.
    for raiz in ZC._overlay().get("raices_repo", []):
        deny.append(os.path.join(REPO, raiz, "r.pdf"))
    if not ZC.hay_overlay():
        print("  ⚠️  sin zonas_clinicas.local.json: no se prueban las raíces locales")
    for p in deny:
        check("DENY %s" % p.replace(REPO, "<repo>").replace(HOME, "~"), ZC.es_ruta_clinica(p))

    # ── DEBE pasar: el trabajo normal del repo ──────────────────────────────────────
    allow = [
        os.path.join(REPO, "tools", "seguimiento.py"),
        os.path.join(REPO, "00_FUENTE-DE-VERDAD", "Gestion", "HOY.md"),
        os.path.join(REPO, "00_FUENTE-DE-VERDAD", "04 · IA", "Notas", "x.md"),
        os.path.join(REPO, "tools", "informes_web.py"),      # «informes» como parte de un nombre
        os.path.join(REPO, "docs", "informes", "README.md"),  # «informes» que NO es la raíz
        os.path.join(REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_DMS", "dm.json"),   # exento
        "/tmp/scratch/informes.txt",
        os.path.join(REPO, ".claude", "hooks", "zonas_clinicas.py"),
    ]
    for p in allow:
        check("ALLOW %s" % p.replace(REPO, "<repo>"), not ZC.es_ruta_clinica(p))

    # ── El `pattern` de Glob/Grep: solo familia A ───────────────────────────────────
    check("pattern con _PRIVADO_CLINICO → clínico", ZC.es_patron_clinico("**/_PRIVADO_CLINICO/**"))
    check("pattern con 00_Salud → clínico", ZC.es_patron_clinico("**/00_Salud/*.pdf"))
    check("pattern 'informes' NO cuelga el repo", not ZC.es_patron_clinico("**/informes/*"))
    check("pattern normal pasa", not ZC.es_patron_clinico("tools/*.py"))
    check("pattern _PRIVADO_DMS pasa (exento)", not ZC.es_patron_clinico("**/_PRIVADO_DMS/*"))

    # ── Tokens de bash: el patrón de grep NO es una ruta ────────────────────────────
    # Los tres comandos que fueron denegados de verdad mientras se auditaba esto.
    for cmd in (["grep", "-rn", "_PRIVADO_CLINICO", "tools/"],
                ["grep", "-rn", "informes", "tools/"],
                ["rg", "00_Salud", "tools/"],
                ["ls", "-d", os.path.join(REPO, "00_FUENTE-DE-VERDAD")],
                ["git", "log", "--oneline", "--", "tools/lector_clinico.py"]):
        check("token ALLOW: %s" % " ".join(cmd)[:52],
              not ZC.rutas_clinicas_en_tokens(cmd, cwd=REPO))

    # …pero si el token es de verdad una RUTA clínica, se caza igual.
    for cmd in (["cat", os.path.join(REPO, "informes", "alta.pdf")],
                ["head", "-20", os.path.join(HOME, "Clinico-PRIVADO", "x.md")],
                ["grep", "-rn", "algo", os.path.join(REPO, "informes")],
                ["cp", os.path.join(REPO, "docu enviada a nova", "a.pdf"), "/tmp/x"],
                # -e mueve el patrón fuera de la posición: el operando vuelve a ser ruta
                ["grep", "-e", "patron", os.path.join(REPO, "informes", "y.txt")]):
        check("token DENY: %s" % " ".join(cmd)[:52].replace(REPO, "<repo>"),
              bool(ZC.rutas_clinicas_en_tokens(cmd, cwd=REPO)))

    # Glob en el token: `cat ~/Clin*/x.pdf` no puede colarse por no tener el nombre literal.
    if os.path.isdir(os.path.join(HOME, "Clinico-PRIVADO")):
        check("glob que expande a clínico se caza",
              bool(ZC.rutas_clinicas_en_tokens(["cat", os.path.join(HOME, "Clin*")], cwd=REPO)))

    # ── Familia D: `_PRIVADO_INSTAGRAM/reels/*.md` sale; el resto de la carpeta NO ──
    # (deuda privado-instagram-bloquea-mineria-buzon, 11-sep-26). reels/ = digests de reels
    # públicos de terceros (reel_digest.py); la raíz = comentarios/menciones con PII de terceros.
    IG = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_INSTAGRAM")
    for p in (os.path.join(IG, "reels", "DaCGXkHNE3M.md"),
              os.path.join(IG, "REELS", "x.md")):            # normaliza mayúsculas igual
        check("D ALLOW %s" % p.replace(REPO, "<repo>"), not ZC.es_ruta_clinica(p))
    for p in (IG,                                              # listar el padre: NO
              os.path.join(IG, "reels"),                       # el directorio en sí: NO (cd/-C)
              os.path.join(IG, "reels", "sub", "x.md"),        # sub-carpeta de reels: NO
              os.path.join(IG, "reels", "x.json"),             # otra extensión: NO
              os.path.join(IG, "reels", ".md"),
              os.path.join(IG, "reels", "{a,..}.md"),          # llaves: NO
              os.path.join(IG, "digest_2026-09-10.md"),        # comentarios en la raíz: NO
              os.path.join(IG, "raw.json"),
              os.path.join(IG, "comentarios", "reels"),        # 'reels' no directo tras el padre
              os.path.join(IG, "reels_viejos", "x.md"),        # prefijo parecido: NO
              os.path.join(IG, "reels", "..", "digest.md"),    # travesía de vuelta al padre
              os.path.join(IG, "reels", "_PRIVADO_CLINICO", "a.md"),   # otro clínico dentro
              os.path.join(REPO, "_PRIVADO_CLINICO", "_PRIVADO_INSTAGRAM", "reels", "x.md"),
              os.path.join(REPO, "00_FUENTE-DE-VERDAD", "_PRIVADO_WHATSAPP", "reels", "x.md"),
              os.path.join(REPO, "informes", "_PRIVADO_INSTAGRAM", "reels", "x.md")):
        check("D DENY %s" % p.replace(REPO, "<repo>"), ZC.es_ruta_clinica(p))
    check("D pattern reels pasa", not ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/reels/*.md"))
    check("D pattern del padre NO", ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/*.md"))
    check("D pattern comodín en la hija NO", ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/re*/*"))
    check("D pattern con travesía NO",
          ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/reels/../*.json"))
    # En Bash la familia D NO aplica (derivar el directorio del fichero exento abre la raíz):
    # `cat reels/a.md` por Bash sigue denegado; la lectura va por Read.
    check("D token cat reels por Bash sigue DENY",
          bool(ZC.rutas_clinicas_en_tokens(["cat", os.path.join(IG, "reels", "a.md")], cwd=REPO)))
    check("D token ls del padre NO", bool(ZC.rutas_clinicas_en_tokens(["ls", IG], cwd=REPO)))
    # El pie que cazó `verificacion`: `cd` a reels y luego `cat ../comentarios.json`. El `..` se
    # resuelve contra el cwd del hook, así que lo que tiene que caer es el `cd` (y el `-C`).
    for cmd in (["cd", os.path.join(IG, "reels")],
                ["pushd", os.path.join(IG, "reels")],
                ["ls", os.path.join(IG, "reels")],
                ["tar", "-C", os.path.join(IG, "reels"), "-cf", "/tmp/a.tar", ".."],
                ["git", "-C", os.path.join(IG, "reels"), "status"]):
        check("D token DENY: %s" % " ".join(cmd).replace(REPO, "<repo>")[:60],
              bool(ZC.rutas_clinicas_en_tokens(cmd, cwd=REPO)))
    # Las cinco derivaciones que cazó `verificacion` en la 2ª ronda (tokens tal y como los ve el hook).
    X = os.path.join(IG, "reels", "x.md")
    for cmd in (["find", X, "-execdir", "cat", "../c.json", ";"],
                ["echo", X],                                   # | xargs dirname | xargs cat {}/..
                ["dirname", X],                                # D=$(dirname …); cat "$D/../c.json"
                ["cd", X],
                ["ls", "-la", X],
                ["head", X]):
        check("D token DENY en Bash: %s" % " ".join(cmd[:2]).replace(REPO, "<repo>")[:50],
              bool(ZC.rutas_clinicas_en_tokens(cmd, cwd=REPO)))
    check("D pattern llaves con travesía NO",
          ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/reels/{..,x}/*.json"))
    check("D pattern ** + travesía NO",
          ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/reels/**/../*.json"))
    check("D pattern reels/* sin .md NO", ZC.es_patron_clinico("**/_PRIVADO_INSTAGRAM/reels/*"))
    # Symlink: un enlace dentro de reels/ que apunta a la raíz (o a lo clínico) se deniega.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ig = os.path.join(td, "_PRIVADO_INSTAGRAM")
        os.makedirs(os.path.join(ig, "reels"))
        open(os.path.join(ig, "digest.md"), "w").close()
        os.symlink(os.path.join(ig, "digest.md"), os.path.join(ig, "reels", "trampa.md"))
        check("D symlink reels→raíz se caza",
              ZC.es_ruta_clinica(os.path.join(ig, "reels", "trampa.md")))
        open(os.path.join(ig, "reels", "ok.md"), "w").close()
        check("D fichero real en reels pasa",
              not ZC.es_ruta_clinica(os.path.join(ig, "reels", "ok.md")))
        # Un DIRECTORIO con nombre `.md` (para poder hacer cd dentro y subir con ..): NO.
        os.makedirs(os.path.join(ig, "reels", "falso.md"))
        check("D directorio disfrazado de .md se caza",
              ZC.es_ruta_clinica(os.path.join(ig, "reels", "falso.md")))
        os.symlink(ig, os.path.join(ig, "reels", "enlace.md"))
        check("D symlink .md a la carpeta padre se caza",
              ZC.es_ruta_clinica(os.path.join(ig, "reels", "enlace.md")))

    # ── Ruta relativa: el cwd manda ─────────────────────────────────────────────────
    check("relativa 'informes/x' desde el repo → clínico", ZC.es_ruta_clinica("informes/x", cwd=REPO))
    check("relativa 'informes/x' desde /tmp → NO", not ZC.es_ruta_clinica("informes/x", cwd="/tmp"))
    check("travesía ../informes se resuelve",
          ZC.es_ruta_clinica(os.path.join(REPO, "tools", "..", "informes", "x"), cwd=REPO))

    # ── Normalización: tildes y mayúsculas no abren un hueco ────────────────────────
    check("mayúsculas dan igual", ZC.es_ruta_clinica(os.path.join(REPO, "INFORMES", "x")))
    check("_privado_clinico en minúsculas", ZC.es_segmento_clinico("_privado_clinico"))
    check("_PRIVADO_CLINICO en mayúsculas", ZC.es_segmento_clinico("_PRIVADO_CLINICO"))

    # ── Paridad con el fallback inline de los hooks ─────────────────────────────────
    # Los hooks importan este módulo con try/except (un import roto denegaría TODA llamada
    # del lazo 24/7). El fallback tiene que ser un SUBCONJUNTO de lo canónico: así, si algún
    # día falla el import, el muro se degrada a lo de antes pero nunca a menos.
    sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
    import importlib.util
    for hook in ("muro_guard.py", "clinico_guard.py"):
        spec = importlib.util.spec_from_file_location(
            "h_" + hook[:-3], os.path.join(ROOT, ".claude", "hooks", hook))
        m = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(m)
        except SystemExit:
            pass
        fb = getattr(m, "_CLINICAL_HINTS_FALLBACK", None)
        if fb is None:
            continue
        # Cada marcador del fallback tiene que seguir siendo clínico bajo el módulo nuevo.
        for h in fb:
            seg = h.strip("/").split("/")[-1]
            check("%s: el fallback %r sigue cubierto" % (hook, h),
                  ZC.es_segmento_clinico(seg) or ZC.es_ruta_clinica(os.path.join(REPO, seg)))

    # ── Las raíces de familia B dentro del repo tienen que estar gitignoradas ───────
    import subprocess
    for r in ZC.RAICES_EN_REPO:
        p = os.path.join(REPO, r)
        if not os.path.exists(p):
            continue
        rc = subprocess.run(["git", "-C", REPO, "check-ignore", "-q", p]).returncode
        check("gitignored: %s" % r, rc == 0)

    print("RESULTADO zonas_clinicas: %d OK, %d fallos" % (_pass, _fail))
    print("✅ ZONAS CLÍNICAS EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
