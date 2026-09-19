#!/usr/bin/env python3
"""tools/lector_clinico.py — Ventanilla AUDITADA para leer dato clínico (N2).

Por qué (Capa C del guardián): la Capa A bloquea la herramienta Read sobre rutas clínicas.
Este script es el canal SANCIONADO para que los agentes clínicos (oncologo-virtual,
comite-medico, herramientas-medicas, verificacion) lean un informe crudo cuando el RAG no
basta — y deja REGISTRO de cada acceso, que hoy no existe.

Uso:  python3 tools/lector_clinico.py <ruta_fichero_clinico>
  · Solo sirve ficheros bajo el allowlist clínico (resuelve symlinks/`..` antes de decidir,
    para que no se pueda evadir la comprobación). Rechaza y registra lo demás.
  · Registra: timestamp, agente (env BTP_AGENT/CLAUDE_AGENT si está), resultado, ruta.
  · El log es LOCAL y gitignored (revela QUÉ informes se abrieron, no su contenido).

Límite honesto: NO es RBAC estricto — un agente con Bash podría leer por otra vía. Da (a) un
canal limpio ahora que la Capa A bloquea la herramienta Read, y (b) auditoría. Para que sea el
ÚNICO canal habría que quitar Bash a los clínicos (lockdown mayor, no hecho).
"""
import os, sys, time

HOME = os.path.expanduser("~")
REPO = os.environ.get("BTP_REPO") or os.path.join(HOME, "claudecode")

# La ventanilla sirve EXACTAMENTE lo que el guard deniega, usando el mismo predicado. Antes
# tenía su propia lista de raíces y había divergido: `_PRIVADO_EXPEDIENTE` (e `informes/`, y
# `docu enviada a nova/`) estaban denegados por el guard pero la ventanilla tampoco sabía
# servirlos → callejón sin salida, ni por la puerta ni por la ventana. Compartir el predicado
# cierra eso por construcción, no sincronizando dos listas que volverán a separarse.
# Import por ruta explícita: `.claude/hooks` no está en el sys.path de tools/, y meterlo
# tendría su propio riesgo (tools/queue.py ya sombrea el `queue` de la stdlib).
def _cargar_politica():
    import importlib.util
    ruta = os.path.join(REPO, ".claude", "hooks", "zonas_clinicas.py")
    if not os.path.exists(ruta):
        # …y si el repo resuelto no la tiene, prueba junto a este mismo fichero (worktrees).
        ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".claude", "hooks", "zonas_clinicas.py")
    spec = importlib.util.spec_from_file_location("zonas_clinicas", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Fail-CLOSED, al revés que los hooks: esto NO está en el camino caliente, y una ventanilla
# que no puede confirmar su propia política no debe servir nada.
try:
    ZC = _cargar_politica()
except Exception as _e:
    ZC, _ZC_ERR = None, repr(_e)

LOG = os.path.join(REPO, ".claude", "logs", "clinico-access.log")

def _log(agent, result, path):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{agent}\t{result}\t{path}\n")
    except Exception:
        pass  # el log nunca debe romper la lectura

def main(argv):
    if not argv:
        print("uso: python3 tools/lector_clinico.py <ruta>", file=sys.stderr)
        return 2
    agent = (os.environ.get("BTP_AGENT") or os.environ.get("CLAUDE_AGENT")
             or os.environ.get("USER") or "?")
    path = os.path.realpath(os.path.expanduser(argv[0]))  # resuelve symlinks/.. ANTES de decidir
    if ZC is None:
        _log(agent, "RECHAZADO-sin-politica", path)
        print("RECHAZADO: no pude cargar zonas_clinicas.py (%s). Una ventanilla que no sabe "
              "cuál es su allowlist no sirve nada." % _ZC_ERR, file=sys.stderr)
        return 1
    if not ZC.es_ruta_clinica(path):
        _log(agent, "RECHAZADO-fuera-de-allowlist", path)
        print(f"RECHAZADO: {path} no es zona clínica — léelo con las tools normales.",
              file=sys.stderr)
        return 1
    if not os.path.isfile(path):
        _log(agent, "no-existe", path)
        print("RECHAZADO: no existe o no es un fichero.", file=sys.stderr)
        return 1
    _log(agent, "LEIDO", path)
    with open(path, encoding="utf-8", errors="ignore") as f:
        sys.stdout.write(f.read())
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
