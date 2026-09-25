#!/usr/bin/env python3
"""test_honestidad_lint_repo.py — el barrido del linter cubre algo de verdad.

Hallazgo medio nº10 de la auditoría del 25-jul-26: `--repo` exigía `tipo: decision` en el
frontmatter y esa clave no existía en NINGUNA de las notas del repo. El barrido recorría el disco
entero para revisar cero documentos, mientras CLAUDE.md lo presenta como el apoyo contra la falsa
certeza. Y no iba a arreglarse solo: `archivar_nota.py` tampoco escribía frontmatter, así que la
cobertura futura también era cero. Un opt-in que nadie ejerce es una cobertura de mentira.

Ahora barre TODO y el frontmatter sirve para EXIMIR. Este test fija la dirección del contrato: si
alguien vuelve a invertirlo, la cobertura cae a cero otra vez y aquí se ve.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import honestidad_lint as hl  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


MALO = "El arnes es una caja negra con telemetria sin documentar.\n"


def _escribir(raiz, rel, texto):
    p = os.path.join(raiz, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(texto)
    return p


def main():
    with tempfile.TemporaryDirectory() as tmp:
        _escribir(tmp, "suelta.md", MALO)                                   # sin frontmatter
        _escribir(tmp, "sub/con-fm.md", "---\ntipo: nota\n---\n\n" + MALO)  # frontmatter cualquiera
        _escribir(tmp, "vieja-decision.md", "---\ntipo: decision\n---\n\n" + MALO)
        _escribir(tmp, "exenta.md", "---\nhonestidad: exento\n---\n\n" + MALO)
        _escribir(tmp, "_PRIVADO_WHATSAPP/chat.md", MALO)                   # material externo
        _escribir(tmp, "_PRIVADO_NUCLEO/diario.md", MALO)                   # sus palabras, no del sistema
        _escribir(tmp, "no-es-md.txt", MALO)

        docs, exentos, _fuera = hl._docs_a_barrer(tmp)
        rels = sorted(os.path.relpath(d, tmp) for d in docs)

        check("un .md SIN frontmatter se barre (era el caso del 99% del repo)",
              "suelta.md" in rels)
        check("un .md con frontmatter de otro tipo también se barre",
              os.path.join("sub", "con-fm.md") in rels)
        check("`tipo: decision` ya no es requisito, sigue entrando",
              "vieja-decision.md" in rels)
        check("`honestidad: exento` EXIME (la única forma de salirse, y va escrita en el doc)",
              "exenta.md" not in rels)
        check("y se cuenta cuántos se eximieron (la exención no es silenciosa)", exentos == 1)
        check("los volcados de WhatsApp quedan fuera (son de terceros, no del sistema)",
              not any("_PRIVADO_WHATSAPP" in r for r in rels))
        check("_PRIVADO_NUCLEO queda fuera (son las palabras de {{TITULAR}} sobre su vida)",
              not any("_PRIVADO_NUCLEO" in r for r in rels))
        check("los que no son .md no se tocan", not any(r.endswith(".txt") for r in rels))
        check("cobertura REAL > 0 (el bug era exactamente que fuera 0)", len(docs) >= 3)

        # Advisory por defecto, exit 1 con --strict: mismo contrato que --check
        check("advisory por defecto (no rompe el build)", hl._check_repo(tmp, strict=False) == 0)
        check("--strict rompe el build cuando hay flags", hl._check_repo(tmp, strict=True) == 1)

        # Ventana temporal: --desde-dias N solo mira lo tocado hace poco (lo que corre a diario)
        viejo = _escribir(tmp, "antigua.md", MALO)
        hace_un_mes = os.path.getmtime(viejo) - 30 * 86400
        os.utime(viejo, (hace_un_mes, hace_un_mes))
        recientes, _e, _f = hl._docs_a_barrer(tmp, desde_dias=7)
        check("--desde-dias deja fuera lo viejo",
              not any(os.path.basename(d) == "antigua.md" for d in recientes))
        check("y conserva lo reciente", any(os.path.basename(d) == "suelta.md" for d in recientes))

    # Un repo vacío no es un error (no todo el mundo tiene notas), pero tampoco un verde falso
    with tempfile.TemporaryDirectory() as vacio:
        check("raíz sin .md → verde silencioso, no excepción",
              hl._check_repo(vacio, strict=True) == 0)

    print("test_honestidad_lint_repo: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
