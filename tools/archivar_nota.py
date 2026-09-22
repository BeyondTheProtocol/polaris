#!/usr/bin/env python3
"""tools/archivar_nota.py — persiste un ENTREGABLE que acabo de producir a la fuente de verdad.

PROBLEMA QUE RESUELVE: investigaciones / análisis / decisiones razonadas se quedaban SOLO en el chat
y se perdían (regla de {{TITULAR}} 28/6: «nada valioso vive solo en el chat»). Este tool es el comando de
1 paso para que archivar deje de tener fricción: escribe un .md DATADO en la carpeta correcta de
00_FUENTE-DE-VERDAD/, reindexa el RAG y devuelve la ruta clicable. NO es archivar.py (ése clasifica
Downloads→fuente); éste persiste un texto que YA tengo (lo paso por stdin).

Uso:
  echo "<contenido markdown>" | python3 tools/archivar_nota.py "<título>" [--tema "04 · IA/Notas"]
  python3 tools/archivar_nota.py "<título>" --tema "07 · Marca" < nota.md
Flags:
  --tema <subcarpeta>   carpeta dentro de 00_FUENTE-DE-VERDAD (def. "04 · IA/Notas").
  --no-reindex          no reindexar el RAG ahora (para archivar en lote; reindexa luego con kb.py index).
  --hilo                además PROPONE un hilo aparcado a Vega (no lo crea: el gestor decide).
  --estado BORRADOR     marca en la cabecera (def. BORRADOR; lo de fuera del muro nace en borrador).

Muro: solo escribe en la fuente de verdad (gitignored, local). Nada hacia fuera.
"""
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMA_DEFECTO = os.path.join("04 · IA", "Notas")


def _resolver_fv(root=ROOT):
    """Dónde vive la fuente de verdad. Devuelve (ruta, origen).

    La fuente de verdad es ESTADO VIVO en **casa base** y está gitignored: una nota escrita en el
    árbol de un worktree NO viaja en el merge y se pierde al borrar el worktree. Pasó el 12-sep-2026
    con cuatro entregables (y ya había una memoria de que pasaba, sin mecanismo que lo impidiera).
    Encima el reindex sí apuntaba a casa base (`kb.py` fuerza `~/claudecode`), así que el tool decía
    «RAG reindexado» mientras la nota quedaba fuera del índice. Aquí se alinean los dos.

    Orden: BTP_FV_DIR (tests y usos explícitos) → casa base (BTP_REPO o ~/claudecode) → este árbol.
    """
    env = os.environ.get("BTP_FV_DIR")
    if env:
        return env, "BTP_FV_DIR"
    base = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
    fv_base = os.path.join(base, "00_FUENTE-DE-VERDAD")
    if os.path.isdir(fv_base) and os.path.abspath(base) != os.path.abspath(root):
        return fv_base, "casa-base"
    if os.path.isdir(fv_base):
        return fv_base, "aqui"
    # Casa base no accesible: no perdemos la nota, pero hay que cantarlo (lo hace main()).
    return os.path.join(root, "00_FUENTE-DE-VERDAD"), "huerfano"


FV, FV_ORIGEN = _resolver_fv()


def _slug(texto, maxlen=60):
    """Slug seguro para nombre de fichero: sin acentos raros de path, espacios→-, sin barras."""
    s = texto.strip().lower()
    s = s.replace("/", "-").replace("\\", "-")
    s = re.sub(r"[^\w\sáéíóúñü-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s).strip("-")
    return (s[:maxlen].strip("-")) or "nota"


def _parse_args(argv):
    opts = {"tema": TEMA_DEFECTO, "reindex": True, "hilo": False, "estado": "BORRADOR", "titulo": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--tema" and i + 1 < len(argv):
            opts["tema"] = argv[i + 1]; i += 2
        elif a == "--estado" and i + 1 < len(argv):
            opts["estado"] = argv[i + 1]; i += 2
        elif a == "--no-reindex":
            opts["reindex"] = False; i += 1
        elif a == "--hilo":
            opts["hilo"] = True; i += 1
        elif opts["titulo"] is None:
            opts["titulo"] = a; i += 1
        else:
            i += 1
    return opts


def _proponer_hilo_a_vega(titulo, ruta_rel):
    """Deja una PROPUESTA de hilo aparcado en el buzón de Vega (no crea la tarjeta: el gestor decide,
    feedback-tarjetas-necesitan-ok-del-gestor). Fail-soft: si no se puede, no rompe el archivado."""
    try:
        d = os.path.join(ROOT, "tools", "state", "vega")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "propuestas_hilos.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "titulo": titulo, "ruta": ruta_rel, "origen": "archivar_nota",
                "nota": "investigación/entregable archivado; ¿registrar como hilo aparcado?",
            }, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


VENV_PY = os.path.join(os.path.expanduser("~/claudecode"), ".venv", "bin", "python3")


def _build(kb):
    """Reindexa el RAG SIN heredar el intérprete de quien llamó a este tool.

    `kb.build()` se niega (rc=1) cuando el intérprete que corre no tiene pypdf, porque indexar sin
    él dejaría fuera todos los PDFs del historial clínico. Y a archivar_nota.py se le invoca casi
    siempre con el `python3` del PATH (Homebrew), que NO lo tiene: pypdf vive en el `.venv` del
    repo. Hasta el 20-sep-2026 esto se construía EN PROCESO, así que toda nota archivada con el
    python del sistema salía con «NO se reindexó el RAG» y había que reindexar a mano — pasó con
    la nota de la campaña de visores 3D. `historial_sync.reindexar()` y el daemon
    com.btp.kb-reindex ya hacían lo correcto (subproceso con el venv); esto los alinea.

    Si el `kb` que llega no sabe decir si puede leer PDFs (un doble de test), se construye en
    proceso como siempre: la delegación no debe cambiar lo que ya estaba probado.
    """
    puede = getattr(kb, "hay_pypdf", None)
    if puede is None or puede() or not os.path.exists(VENV_PY):
        return kb.build()
    import subprocess
    r = subprocess.run([VENV_PY, os.path.join(ROOT, "tools", "kb.py"), "index"],
                       capture_output=True, timeout=3600)
    if r.returncode:
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-600:])
    return r.returncode


def _en_el_indice(kb, rel):
    """¿La nota recién archivada se puede YA encontrar buscando? Verifica el efecto, no que corrió.

    Consulta directa al .db: si la ruta no está entre los `path` indexados, el reindex no sirvió,
    diga lo que diga el código de salida.
    """
    try:
        import sqlite3 as _sq
        if not os.path.exists(kb.DB):
            return False
        # Las dos mitades no hablan el mismo dialecto de ruta: aquí `rel` cuelga del repo
        # («00_FUENTE-DE-VERDAD/04 · IA/Notas/x.md») y el índice guarda la ruta relativa a la
        # propia fuente de verdad («04 · IA/Notas/x.md»). Comparar en crudo daba SIEMPRE «no
        # indexado», que es mentir en la otra dirección. Se prueban las dos formas.
        raiz = os.path.basename(os.path.normpath(FV))
        candidatos = [rel]
        if rel.startswith(raiz + os.sep):
            candidatos.append(rel[len(raiz) + 1:])
        con = _sq.connect("file:%s?mode=ro" % kb.DB, uri=True)
        try:
            for cand in candidatos:
                if con.execute("SELECT 1 FROM chunks WHERE path = ? LIMIT 1", (cand,)).fetchone():
                    return True
        finally:
            con.close()
        return False
    except Exception:
        return False   # si no se puede comprobar, NO se declara reindexado


def archivar(titulo, contenido, tema=TEMA_DEFECTO, estado="BORRADOR", reindex=True, hilo=False):
    """Escribe el .md datado en FV/<tema>/, (re)indexa el RAG y devuelve (ruta_abs, ruta_rel, reindexado)."""
    hoy = datetime.date.today().isoformat()
    carpeta = os.path.join(FV, tema)
    os.makedirs(carpeta, exist_ok=True)
    nombre = "%s-%s.md" % (_slug(titulo), hoy)
    ruta = os.path.join(carpeta, nombre)
    cuerpo = contenido if contenido.lstrip().startswith("#") else \
        "# %s\n\n> Estado: **%s** · %s · archivado con archivar_nota.py\n\n%s" % (titulo, estado, hoy, contenido)
    # Frontmatter con `tipo: decision`: dice de qué clase es la nota (lo que se archiva aquí
    # —investigación, análisis, decisiones razonadas— es justo lo que sostiene decisiones). Ya NO es
    # lo que decide si honestidad_lint la barre: desde el 25-jul-26 el linter barre todo por defecto
    # y el frontmatter sirve para lo contrario, EXIMIR (`honestidad: exento`). Se mantiene porque el
    # tipo es información útil por sí misma, no porque nada dependa de ella.
    if not cuerpo.lstrip().startswith("---"):
        cuerpo = ("---\ntipo: decision\ntitulo: %s\nfecha: %s\nestado: %s\n---\n\n%s"
                  % (titulo.replace("\n", " "), hoy, estado, cuerpo))
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(cuerpo.rstrip() + "\n")
    os.replace(tmp, ruta)
    # Relativo al PADRE de la fuente de verdad, no a ROOT: desde un worktree, ROOT es otro árbol y
    # salía una ruta con «../../..» que no servía para enlazar ni para buscar.
    rel = os.path.relpath(ruta, os.path.dirname(os.path.normpath(FV)))

    reindexado, porque_no = False, ""
    if reindex:
        try:
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import kb
            # `kb.build()` NO lanza excepción cuando se niega a indexar: devuelve 1 y lo explica
            # por stderr (sin pypdf se perderían los PDFs del historial, así que prefiere no tocar
            # el índice). Nadie miraba ese retorno, así que este tool imprimía «RAG reindexado»
            # sobre un índice intacto. Detectado 5 veces antes de cerrarse (20-sep-2026): el tool
            # mentía, que es lo peor que puede hacer el sistema.
            rc = _build(kb)
            if rc:
                porque_no = "el reindexado falló (rc=%s); mira su aviso justo arriba" % rc
            else:
                # Y no basta con que build() no protestara: se comprueba el EFECTO, que es que la
                # nota recién escrita se pueda encontrar buscando.
                reindexado = _en_el_indice(kb, rel)
                if not reindexado:
                    porque_no = "kb.build() terminó pero la nota no aparece en el índice"
        except Exception as e:
            porque_no = "%r" % e
        if not reindexado:
            print("aviso: NO se reindexó el RAG (%s). La nota está archivada, pero todavía NO se "
                  "encuentra con kb.py ask. Hazlo con:\n       %s/.venv/bin/python3 tools/kb.py index"
                  % (porque_no, os.path.expanduser("~/claudecode")), file=sys.stderr)
    if hilo:
        _proponer_hilo_a_vega(titulo, rel)
    return ruta, rel, reindexado


def main(argv):
    opts = _parse_args(argv)
    if not opts["titulo"]:
        print(__doc__, file=sys.stderr)
        return 2
    contenido = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not contenido.strip():
        print("error: sin contenido (pásalo por stdin). Ej: echo \"texto\" | archivar_nota.py \"Título\"",
              file=sys.stderr)
        return 2
    if FV_ORIGEN == "huerfano":
        print("⚠️  casa base no accesible: archivo en ESTE árbol (%s). La nota NO viaja en el merge "
              "—la fuente de verdad está gitignored— así que cópiala a mano o se pierde." % FV,
              file=sys.stderr)
    _, rel, reidx = archivar(opts["titulo"], contenido, tema=opts["tema"], estado=opts["estado"],
                             reindex=opts["reindex"], hilo=opts["hilo"])
    print("✓ archivado: %s%s" % (rel, "  (RAG reindexado)" if reidx
                                 else "  ⚠️ SIN reindexar: aún no se encuentra con kb.py ask"))
    if opts["hilo"]:
        print("  + propuesto a Vega como posible hilo aparcado")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
