#!/usr/bin/env python3
"""test_coste_repo.py — el medidor de gasto ve TODO el repo, no solo casa base.

Hallazgo medio nº15 de la auditoría del 25-jul-26: el Observatorio medía `coste.THIS_PROJECT` y ya
está, ignorando los ~89 directorios de worktree de `~/.claude/projects`. Y CLAUDE.md ORDENA aislar
cada sesión en su worktree, así que lo invisible era justamente el trabajo de construcción. Medido
el 25-jul: el panel enseñaba 105M de tokens «hoy» cuando el total real del repo era 1.381M — el 92%
fuera del tablero. Bajo Max el recurso escaso es la CUOTA, así que se podía llegar a un rate-limit
en mitad de algo clínico con el panel en verde.

Segundo defecto del mismo sitio: `hoy = dias[-1]` era el último día CON DATOS, no hoy. Con el lazo
dos días callado, el panel enseñaba el consumo de anteayer bajo el rótulo «hoy».
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import coste  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    orig = coste.PROJECTS
    with tempfile.TemporaryDirectory() as tmp:
        coste.PROJECTS = tmp
        base = coste.THIS_PROJECT
        for d in (base,
                  base + "--claude-worktrees-rama-uno",
                  base + "--claude-worktrees-rama-dos",
                  "-Users-polaris",                        # otro repo del usuario
                  "-private-tmp-claude-501--scratchpad",   # cwd de scratchpad, no es el repo
                  "-Users-polaris-claudecodex"):           # prefijo parecido pero es otro proyecto
            os.makedirs(os.path.join(tmp, d))
        # Un fichero suelto que empieza igual: no es directorio, no cuenta
        open(os.path.join(tmp, base + "--suelto"), "w").close()

        proys = coste.proyectos_del_repo()
        nombres = [os.path.basename(p) for p in proys]

        check("casa base entra", base in nombres)
        check("los worktrees entran (eran el 92% invisible)",
              base + "--claude-worktrees-rama-uno" in nombres
              and base + "--claude-worktrees-rama-dos" in nombres)
        check("otro repo del usuario no entra", "-Users-polaris" not in nombres)
        check("los cwd de scratchpad no entran",
              not any(n.startswith("-private-tmp") for n in nombres))
        check("un prefijo parecido de OTRO proyecto sí entra por diseño (mismo árbol)",
              "-Users-polaris-claudecodex" in nombres)
        check("un fichero suelto no se confunde con un proyecto",
              base + "--suelto" not in nombres)
        check("casa base va la primera (el desglose la separa del resto)",
              nombres and nombres[0] == base)
        check("es_casa_base distingue casa base de un worktree",
              coste.es_casa_base(proys[0])
              and not coste.es_casa_base(os.path.join(tmp, base + "--claude-worktrees-rama-uno")))
        check("es_casa_base tolera la barra final",
              coste.es_casa_base(os.path.join(tmp, base) + os.sep))

    # Sin directorio de proyectos no explota: devuelve vacío
    coste.PROJECTS = os.path.join(tempfile.gettempdir(), "no-existe-jamas-btp")
    check("sin ~/.claude/projects → lista vacía, sin excepción",
          coste.proyectos_del_repo() == [])

    coste.PROJECTS = orig
    # Contra el disco real: el repo tiene MÁS de un proyecto (si esto vuelve a dar 1, el panel
    # volvió a medir solo casa base).
    reales = coste.proyectos_del_repo()
    check("en el disco real hay más de un proyecto del repo", len(reales) > 1)

    # `hoy` es hoy, no «el último día con datos»
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import time
    with open(os.path.join(ROOT, "tools", "observatorio.py"), encoding="utf-8") as f:
        src = f.read()
    check("observatorio ya no usa dias[-1] como «hoy»", "hoy = dias[-1]" not in src)
    check("observatorio usa la fecha real del sistema",
          'hoy = time.strftime("%Y-%m-%d")' in src)
    check("y el desglose casa base / ramas está en la salida",
          "hoy_tok_worktrees" in src and "hoy_tok_casa_base" in src)
    del time

    print("test_coste_repo: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
