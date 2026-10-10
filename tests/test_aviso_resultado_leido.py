#!/usr/bin/env python3
"""test_aviso_resultado_leido.py — ningún tools/*.py descarta el resultado de `salida.report_to_titular`/`alerta_critica`
sin haberlo decidido a propósito (10-oct-26, deuda `entrega-dada-por-hecha-sin-mirar-resultado-de-salida`).

POR QUÉ: esas funciones NO lanzan cuando no entregan; devuelven un dict (`delivered`, `aplazado`, `retenido`,
`operativo`, `blocked`…). Una línea `salida.report_to_titular(txt)` suelta, seguida de «marco como avisado», da por
entregado un aviso que el cupo diario aplazó o que HALT frenó. Ese patrón estaba en ≥ 20 sitios.

HEURÍSTICA (la más simple que no da falsos positivos): se busca, por AST, toda llamada usada como SENTENCIA
(resultado descartado) y se exige que figure en la lista cerrada de abajo, con su motivo. Es un trinquete:
  · una llamada nueva que descarta el resultado → ROJO (usa `errores.aviso_en_camino(res)` o añádela aquí con motivo);
  · una entrada que ya no descarta nada → ROJO (baja el cupo: la lista no puede pudrirse).
No intenta adivinar si «luego marca algo»: eso es fuzzy y daría falsos positivos a montones; la revisión humana
de cada descarte se hizo UNA vez, al crear esta lista, y el motivo queda escrito.

Límite conocido: no ve un resultado asignado y luego ignorado (`r = salida.report_to_titular(...)` sin leer `r`), ni un
`salida` alias. Para eso hay tests de comportamiento por llamante (test_entrega_real_avisos.py).
"""
import ast
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOMBRES = ("report_to_titular", "alerta_critica")

# (fichero, función) → (cuántos descartes se permiten, motivo)
PERMITIDOS = {
    ("activar_daemon.py", "_gritar_si_quedo_apagado"): (1, "categoría operativa (siempre al log) y no marca nada después"),
    ("cronica.py", "estado"): (1, "informativo: no marca nada; un fallo se ve en el siguiente `estado`"),
    ("ia.py", "_aviso_clinico"): (1, "fire-and-forget de relevo clínico; no marca nada después"),
    ("ia.py", "_parar"): (1, "DEUDA CONOCIDA: `_debe_avisar_parado` sella el cooldown de 12 h ANTES de enviar; es el freno "
                              "clínico, no se toca aquí (ver deuda `entrega-dada-por-hecha-sin-mirar-resultado-de-salida`)"),
    ("ig_inbox.py", "_alert_expired"): (1, "aviso de fallo; no marca nada después"),
    ("nube_n1.py", "comprobar_24h"): (1, "levanta PuertaCerrada justo después: el fallo se propaga igual"),
    ("polaris_estado.py", "main"): (2, "CLI de informe: imprime el parte; no marca nada"),
    ("ramas.py", "_avisar_dudosos"): (1, "categoría operativa: `salida` siempre la guarda en el log, no puede bloquearse"),
    ("salud_memoria.py", "main"): (1, "CLI; no marca nada después"),
    ("staging.py", "_aprobar"): (1, "solo registra en el log de staging; no marca nada"),
    ("x_dms.py", "_alert_fallo"): (1, "aviso de fallo; no marca nada después"),
    ("x_guardados.py", "cmd_fetch"): (1, "aviso de fallo de lectura (antes de leer nada); no marca nada"),
}


def _nombre(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)


def descartes(fuente, ruta="<fuente>"):
    """{(fichero, función): [líneas]} de llamadas a report_to_titular/alerta_critica usadas como SENTENCIA."""
    out = {}
    base = os.path.basename(ruta)

    def recorre(nodo, fn):
        for hijo in ast.iter_child_nodes(nodo):
            f = hijo.name if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(hijo, ast.Expr) and isinstance(hijo.value, ast.Call) and _nombre(hijo.value) in NOMBRES:
                out.setdefault((base, f), []).append(hijo.lineno)
            recorre(hijo, f)
    recorre(ast.parse(fuente), "<módulo>")
    return out


def main():
    encontrados = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "tools", "*.py"))):
        if os.path.basename(p) == "salida.py":      # la definición, no un llamante
            continue
        try:
            fuente = open(p, encoding="utf-8").read()
            for k, v in descartes(fuente, p).items():
                encontrados.setdefault(k, []).extend(v)
        except SyntaxError as exc:
            print("  ✗ no se pudo parsear %s: %s" % (p, exc))
            return 1
    fallos = []
    for k, lineas in sorted(encontrados.items()):
        permitido = PERMITIDOS.get(k, (0, ""))[0]
        if len(lineas) > permitido:
            fallos.append("%s:%s descarta el resultado de salida %d vez/veces (permitido %d; líneas %s). Usa "
                          "`errores.aviso_en_camino(res)` y marca «avisado» solo si llegó, o añade la entrada con motivo."
                          % (k[0], k[1], len(lineas), permitido, lineas))
    for k, (cupo, _m) in sorted(PERMITIDOS.items()):
        if len(encontrados.get(k, [])) < cupo:
            fallos.append("%s:%s ya no descarta %d vez/veces: baja/quita la entrada de PERMITIDOS (la lista no puede pudrirse)"
                          % (k[0], k[1], cupo))

    # El detector se prueba a sí mismo (canario): si dejara de ver un descarte, la lista sería decorativa.
    prueba = "def f():\n    salida.report_to_titular('x')\n    marca = True\n\ndef g():\n    r = salida.report_to_titular('x')\n    return r\n"
    d = descartes(prueba)
    if d != {("<fuente>", "f"): [2]}:
        fallos.append("el detector no distingue el descarte (f) del uso del resultado (g): %r" % (d,))
    d2 = descartes("def h():\n    _sal.alerta_critica('x')\n")
    if d2 != {("<fuente>", "h"): [2]}:
        fallos.append("el detector no ve alerta_critica descartada: %r" % (d2,))

    for f in fallos:
        print("  ✗ " + f)
    n = sum(len(v) for v in encontrados.values())
    print("RESULTADO aviso_resultado_leido: %d descartes vistos (todos en la lista), %d fallos" % (n, len(fallos)))
    print("✅ AVISO_RESULTADO_LEIDO EN VERDE" if not fallos else "❌ revisar fallos")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
