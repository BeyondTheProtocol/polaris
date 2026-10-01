#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aprobaciones.py — quién aprueba qué, y el registro de lo que aprueba Vega (plan «Vega al mando», F4).

POR QUÉ (1-oct-26)
------------------
{{TITULAR}} decidió el 29-sep que Vega apruebe sola toda la fontanería (bugs, tareas, memorias,
agentes, rutinas, código con fusión, instalar herramientas auditadas) y que a ella solo le
pregunte lo clínico, las incongruencias y los informes que faltan. Enviar, publicar, pagar,
contactar o levantar un código rojo siguen siendo suyos. Hasta hoy no había registro de quién
aprueba qué, y en el directo contó que el orquestador «dice que hace cosas y no las hace». Por eso
cada aprobación de Vega exige la PRUEBA del efecto y cómo deshacerla, y el agente `verificacion`
revisa cada semana una muestra.

La política vive en `tools/config/politica_aprobacion.json` (versionada: cambiarla es un commit).
El registro, en `state/vega/aprobaciones.jsonl` (append-only).

Uso:
  python3 tools/aprobaciones.py nivel <tipo>                     # A | B | C (desconocido → B)
  python3 tools/aprobaciones.py registrar --tipo bug --que "…" --por-que "…" \
        --prueba "commit abc123 + test X en verde" --deshacer "git revert abc123"
  python3 tools/aprobaciones.py registrar --tipo clinico … --ok-titular "<cita o ref de su OK>"
  python3 tools/aprobaciones.py listar [--dias 7]
  python3 tools/aprobaciones.py muestra [--n 5]                  # para la revisión semanal
  python3 tools/aprobaciones.py propuestas                       # buzón de Vega sin resolver
  python3 tools/aprobaciones.py resolver <n> aprobada|descartada --por-que "…"

Salidas: rc 0 ok · rc 2 rechazado por política (C, o B sin OK de {{TITULAR}}, o sin prueba).
Ganchos de test: BTP_STATE_DIR, BTP_POLITICA.
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _politica_path():
    return os.environ.get("BTP_POLITICA") or os.path.join(RAIZ, "tools", "config",
                                                          "politica_aprobacion.json")


def _state():
    if os.environ.get("BTP_STATE_DIR"):
        return os.environ["BTP_STATE_DIR"]
    import seguimiento
    return seguimiento.STATE


def _registro():
    return os.path.join(_state(), "vega", "aprobaciones.jsonl")


def _propuestas():
    return os.path.join(_state(), "vega", "propuestas_hilos.jsonl")


def politica():
    with open(_politica_path(), encoding="utf-8") as fh:
        return json.load(fh)


def nivel(tipo):
    """A, B o C. Un tipo que la política no conoce es B: ante la duda, se pregunta."""
    for n in ("C", "B", "A"):
        if tipo in politica()["niveles"][n]["tipos"]:
            return n
    return "B"


class Rechazo(Exception):
    pass


def registrar(tipo, que, por_que, prueba, deshacer, *, ok_titular="", quien="vega", ahora=None):
    """Apunta una aprobación. Lanza Rechazo si la política no la permite o falta la prueba."""
    n = nivel(tipo)
    if n == "C":
        raise Rechazo("«%s» es nivel C: nunca sin la firma de {{TITULAR}}, y no por aquí (gate de salida)."
                      % tipo)
    if n == "B" and not (ok_titular or "").strip():
        raise Rechazo("«%s» es nivel B: hace falta el OK de {{TITULAR}} (--ok-titular)." % tipo)
    if not (prueba or "").strip():
        raise Rechazo("Sin prueba del efecto no hay aprobación: «hecho» no es una prueba.")
    if not (deshacer or "").strip():
        raise Rechazo("Falta cómo deshacerlo (--deshacer).")
    fila = {"ts": (ahora or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S"), "nivel": n, "tipo": tipo,
            "que": que, "por_que": por_que, "prueba": prueba, "deshacer": deshacer,
            "quien": quien, "ok_titular": ok_titular or None,
            "politica_v": politica().get("version")}
    os.makedirs(os.path.dirname(_registro()), exist_ok=True)
    with open(_registro(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
    return fila


def _leer(ruta):
    try:
        with open(ruta, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []


def listar(dias=7, ahora=None):
    desde = ((ahora or datetime.now()) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")
    return [f for f in _leer(_registro()) if f.get("ts", "") >= desde]


def muestra(n=5, dias=7, semilla=None):
    """Muestra al azar de las aprobaciones de la semana para `verificacion`. Las B van siempre."""
    filas = listar(dias)
    b = [f for f in filas if f.get("nivel") == "B"]
    a = [f for f in filas if f.get("nivel") != "B"]
    rnd = random.Random(semilla)
    return b + rnd.sample(a, min(len(a), max(0, n - len(b))))


def resumen(dias=7):
    filas = listar(dias)
    por_nivel = {}
    for f in filas:
        por_nivel[f.get("nivel")] = por_nivel.get(f.get("nivel"), 0) + 1
    return {"dias": dias, "total": len(filas), "por_nivel": por_nivel,
            "propuestas_sin_resolver": len(propuestas_abiertas())}


# ── Buzón de propuestas de Vega (propuestas_hilos.jsonl): hasta hoy nadie lo leía ───────────

def propuestas_abiertas():
    return [(i, p) for i, p in enumerate(_leer(_propuestas())) if not p.get("estado")]


def resolver(indice, estado, por_que, prueba=""):
    """Marca la propuesta nº `indice` como aprobada o descartada, y la apunta en el registro."""
    if estado not in ("aprobada", "descartada"):
        raise Rechazo("estado debe ser aprobada|descartada")
    filas = _leer(_propuestas())
    if not 0 <= indice < len(filas) or filas[indice].get("estado"):
        raise Rechazo("no hay propuesta abierta con ese número")
    p = filas[indice]
    tipo = "clinico" if p.get("clinico") else "propuesta_hilo"
    fila = registrar(tipo, "%s propuesta: %s" % (estado, p.get("titulo", "")[:160]), por_que,
                     prueba or "propuesta marcada %s en propuestas_hilos.jsonl" % estado,
                     "quitar el campo estado de la línea %d" % (indice + 1),
                     ok_titular=os.environ.get("BTP_OK_TITULAR", ""))
    p["estado"] = estado
    p["resuelta"] = fila["ts"]
    tmp = _propuestas() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    os.replace(tmp, _propuestas())
    return p


def _arg(argv, nombre, defecto=""):
    if nombre in argv:
        i = argv.index(nombre)
        if i + 1 < len(argv):
            return argv[i + 1]
    return defecto


def main(argv):
    cmd = argv[0] if argv else "resumen"
    try:
        if cmd == "nivel" and len(argv) > 1:
            print(nivel(argv[1]))
        elif cmd == "registrar":
            f = registrar(_arg(argv, "--tipo"), _arg(argv, "--que"), _arg(argv, "--por-que"),
                          _arg(argv, "--prueba"), _arg(argv, "--deshacer"),
                          ok_titular=_arg(argv, "--ok-titular"), quien=_arg(argv, "--quien", "vega"))
            print("✅ registrada (%s · %s)" % (f["nivel"], f["tipo"]))
        elif cmd == "listar":
            for f in listar(int(_arg(argv, "--dias", "7"))):
                print("%s · %s · %s · %s · prueba: %s" % (f["ts"][:16], f["nivel"], f["tipo"],
                                                          f["que"][:80], f["prueba"][:80]))
        elif cmd == "muestra":
            print(json.dumps(muestra(int(_arg(argv, "--n", "5"))), ensure_ascii=False, indent=1))
        elif cmd == "propuestas":
            for i, p in propuestas_abiertas():
                print("%d · %s · %s" % (i, p.get("origen", "?"), p.get("titulo", "")[:110]))
        elif cmd == "resolver" and len(argv) > 2:
            p = resolver(int(argv[1]), argv[2], _arg(argv, "--por-que"), _arg(argv, "--prueba"))
            print("✅ %s: %s" % (p["estado"], p.get("titulo", "")[:100]))
        else:
            print(json.dumps(resumen(), ensure_ascii=False))
    except Rechazo as e:
        print("⛔ %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
