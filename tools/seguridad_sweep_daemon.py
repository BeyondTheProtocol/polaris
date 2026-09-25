#!/usr/bin/env python3
"""tools/seguridad_sweep_daemon.py — envoltorio DETERMINISTA del barrido de seguridad (C, 2/7/26).

Por qué existe: hasta hoy `seguridad_sweep.py` solo corría DENTRO del prompt de `auto-mejora`
(paso 0b de su charter). Eso significa que si `auto-mejora` cae por CUALQUIER motivo antes de
llegar a ese paso —crédito agotado, tope local, fallo del agente, el LLM se salta el paso— el
barrido de seguridad simplemente NO corre esa vuelta y nadie se entera hasta que {{TITULAR}} lo note
sola. Es la CLASE de fallo silencioso que el sistema ya trata en otros sitios (heartbeat propio +
autofix/aviso), y el barrido de seguridad —la pared compartida de TODA la constelación— no puede
depender de que un agente LLM llegue vivo a un paso concreto de su prompt.

BRECHA vs HIGIENE (14/7/26 — corrige la recaída del 13-jul: `audit_comites` en rojo por
`ceci`/`tipografia` sin registrar, CERO brechas reales, disparó código rojo y dejó a Vega
muda ~7,5h). `seguridad_sweep.run_checks()` ya clasifica cada check en rojo por su NATURALEZA:
  · BRECHA  → siempre dispara código rojo (para todo), exactamente como antes.
  · HIGIENE → NUNCA dispara código rojo. Avisa fuerte por `errores.registrar` (severidad
    OPERATIVO: "aviso siempre", sin autofix obligatorio ni parada), con el mismo anti-spam
    de 12h que ya usa el resto del sistema nervioso de errores.

Qué hace, en orden:
  1. Corre `seguridad_sweep.run_checks()` (la MISMA lógica que ya usaba auto-mejora: muro +
     choke-point + freno + cola + los 13 cortafuegos de cada caja + auditor de comités +
     gitleaks — ya separada en brecha/higiene).
  2. Escribe su PROPIO heartbeat (tools/state/heartbeat/seguridad-sweep.json, mismo formato ISO-Z
     que usan los demás daemons deterministas — p.ej. centinela_ned.py) para que healthcheck lo
     pueda vigilar como cualquier otra rutina NED. Estados: "ok" | "higiene_pendiente" |
     "critico_bloqueado" | "fallo".
  3. Si hay BRECHA en rojo (posible brecha real) → codigo_rojo.trigger(...): PARA todo, avisa
     FUERTE, explica. Esto es la MISMA severidad que tenía el charter de auto-mejora, solo que
     ahora corre pase lo que pase con el agente LLM.
  4. Si solo hay HIGIENE en rojo (sin brecha) → errores.registrar(..., severidad=OPERATIVO):
     avisa a {{TITULAR}}, pero el lazo sigue corriendo. Papeleo, no una amenaza al goal.
  5. Si el propio HARNESS falla (excepción al importar/ejecutar seguridad_sweep, no un rojo del
     sweep en sí) → aviso OPERATIVO vía errores.registrar (severidad OPERATIVO, sin código rojo:
     esto es fontanería del daemon, no una brecha detectada).
  6. Si `run_checks()` NO lanza pero devuelve algo que rompe el CONTRATO (no es un dict, o le
     falta la clave "brecha") → FAIL-CLOSED: se trata como BRECHA (código rojo), NUNCA como
     verde por omisión de clave (14/7/26, hallazgo del comité de verificación: la primera
     versión de este fix leía `resultado.get("brecha") or []` sin validar nada, así que un
     contrato roto se colaba silenciosamente como "sin brecha" — el mismo fail-open que todo
     este fix existe para cerrar, con otro disfraz).

Determinista, $0 salvo lo que gitleaks/tests tarden en CPU. Sin dependencias fuera de stdlib +
subprocess (seguridad_sweep.py ya lo es). Uso:
  python3 tools/seguridad_sweep_daemon.py            # corre el sweep, heartbeat, código rojo si toca
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
HB_DIR = os.path.join(STATE, "heartbeat")
HB_NAME = "seguridad-sweep"


def _heartbeat(estado):
    """Latido propio (mismo formato ISO-Z que centinela_ned.py) para que healthcheck vigile este
    daemon como cualquier otra rutina NED. Best-effort: nunca lanza."""
    try:
        os.makedirs(HB_DIR, exist_ok=True)
        tmp = os.path.join(HB_DIR, "." + HB_NAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"agente": HB_NAME,
                       "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "estado": estado}, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(HB_DIR, HB_NAME + ".json"))
    except Exception:
        pass


def _avisar_higiene(higiene):
    """HIGIENE en rojo: avisa fuerte por el sistema nervioso de errores (severidad OPERATIVO
    = 'aviso siempre', sin código rojo), nunca toca HALT. Best-effort: si el propio aviso
    peta, no debe tumbar al daemon (eso sería justo el bug que estamos arreglando, con
    otro nombre)."""
    nombres = ", ".join(n for n, _ in higiene)
    detalle = "; ".join("%s: %s" % (n, c) for n, c in higiene)[:1200]
    try:
        import errores
        errores.registrar(
            origen="seguridad_sweep_daemon",
            error="Barrido de seguridad: %d check(s) de HIGIENE en rojo (papeleo/completitud, "
                  "NO brecha): %s" % (len(higiene), nombres),
            severidad=errores.OPERATIVO,
            job="sweep_higiene",
            detalle=detalle,
        )
    except Exception as e:
        sys.stderr.write("seguridad_sweep_daemon: higiene en rojo y no pude avisar: %r\n" % e)


def _contrato_roto(resultado):
    """True si `resultado` no cumple el contrato mínimo de run_checks(): un dict con
    (al menos) la clave "brecha". No basta con que no lance — un dict vacío o de otra
    forma también rompe el contrato y antes se leía silenciosamente como "sin brecha"."""
    return not isinstance(resultado, dict) or "brecha" not in resultado


def run():
    """Corre el sweep y gestiona el resultado. Devuelve el exit code:
      0 = verde · 1 = BRECHA en rojo o contrato roto (código rojo) · 2 = harness caído ·
      3 = solo HIGIENE en rojo (avisado, sin código rojo).
    NUNCA deja una excepción sin capturar — el harness caído es justo el caso que este
    envoltorio existe para cubrir. Y NUNCA confía en la FORMA del resultado sin
    validarla — un contrato roto es tan peligroso como una excepción."""
    try:
        import seguridad_sweep
    except Exception as e:
        _heartbeat("fallo")
        try:
            import errores
            errores.registrar(
                origen="seguridad_sweep_daemon",
                error=e,
                severidad=errores.OPERATIVO,
                job="import_seguridad_sweep",
                detalle="No se pudo importar seguridad_sweep.py: el harness del barrido de "
                        "seguridad está caído (distinto de un ROJO del sweep en sí).",
            )
        except Exception:
            sys.stderr.write("seguridad_sweep_daemon: harness caído y no pude registrar el error: %r\n" % e)
        return 2

    try:
        resultado = seguridad_sweep.run_checks()
    except Exception as e:
        # Harness caído GENUINO (run_checks() petó al ejecutar): fontanería, no brecha
        # detectada — se mantiene exactamente como antes.
        _heartbeat("fallo")
        try:
            import errores
            errores.registrar(
                origen="seguridad_sweep_daemon",
                error=e,
                severidad=errores.OPERATIVO,
                job="ejecutar_seguridad_sweep",
                detalle="seguridad_sweep.run_checks() lanzó una excepción — harness caído, "
                        "no una brecha detectada.",
            )
        except Exception:
            sys.stderr.write("seguridad_sweep_daemon: harness caído y no pude registrar el error: %r\n" % e)
        return 2

    # CONTRATO: run_checks() puede terminar SIN lanzar y aun así devolver algo que no
    # sirve (None, una lista, un dict sin "brecha"...). A diferencia del harness caído
    # de arriba, esto NO es fontanería: no hay forma fiable de saber si el sistema está
    # seguro, así que fail-closed lo trata como BRECHA (código rojo), nunca como verde.
    if _contrato_roto(resultado):
        _heartbeat("critico_bloqueado")
        try:
            import codigo_rojo
            codigo_rojo.trigger(
                "Barrido de seguridad: contrato de run_checks() roto",
                "seguridad_sweep.run_checks() devolvió %r — no es un dict con clave "
                "'brecha'. FAIL-CLOSED: se trata como BRECHA porque no se puede "
                "confirmar que el sistema esté seguro." % (resultado,),
            )
        except Exception as e:
            sys.stderr.write("seguridad_sweep_daemon: contrato roto y no pude disparar código rojo: %r\n" % e)
        return 1

    brecha = resultado.get("brecha") or []
    higiene = resultado.get("higiene") or []

    if brecha:
        _heartbeat("critico_bloqueado")
        try:
            import codigo_rojo
            # CON LA EVIDENCIA DENTRO (31-jul-26). Hasta hoy aquí solo iba el NOMBRE del check, y
            # `run_checks()` ya devolvía su salida: se tiraba. Resultado: el informe decía
            # «gitleaks · secretos en rojo» ocho veces entre el 26 y el 27-jul y, al leerlo
            # después, era imposible saber si había habido una fuga real o un falso positivo —
            # que es exactamente la pregunta que hay que responder para decidir si se reanuda.
            # Sin la salida, un código rojo no se puede auditar, y un cortafuegos que no se
            # puede auditar acaba ignorándose. La rama de HIGIENE, menos grave, sí la guardaba.
            evidencia = "\n\n".join(
                "### %s\n```\n%s\n```" % (n, (c or "(el check no devolvió salida)").strip())
                for n, c in brecha)
            # «No terminó» y «falló» no son lo mismo, y hasta hoy llegaban con el mismo titular
            # (12-sep-2026). `_run` ya reintenta un check que se pasa de tiempo y, si el segundo
            # intento también expira, devuelve una salida que empieza por «NO TERMINÓ». Cuando
            # TODOS los rojos son de esos, no hay ninguna brecha probada: hay una máquina cargada
            # y una comprobación que no se pudo hacer. Se para igual —fail-closed, el muro manda—
            # pero el titular lo dice, para que ella pueda levantarlo en un minuto en vez de
            # pasar la tarde buscando una fuga que no existe. Si alguno falló de verdad, manda
            # el titular de BRECHA aunque haya timeouts mezclados.
            sin_comprobar = [n for n, c in brecha if str(c or "").lstrip().startswith("NO TERMINÓ")]
            todos_sin_comprobar = len(sin_comprobar) == len(brecha)
            if todos_sin_comprobar:
                motivo = "Barrido de seguridad: NO SE PUDO COMPROBAR (no es una brecha probada)"
                cabeza = (
                    "seguridad_sweep NO pudo terminar %d check(s) de BRECHA, ni siquiera al "
                    "reintentarlos con el doble de margen: %s.\n\n"
                    "**Esto NO prueba una brecha: prueba que no se pudo comprobar.** La causa "
                    "típica es la máquina cargada (varias sesiones pesadas a la vez). Se ha "
                    "parado igual, porque ante la duda se para, pero antes de buscar una fuga "
                    "conviene correr esos checks a mano: si salen verdes, era esto y se levanta."
                    % (len(brecha), ", ".join(sin_comprobar)))
            else:
                motivo = "Barrido de seguridad de la constelación en ROJO (BRECHA)"
                cabeza = (
                    "seguridad_sweep detectó %d check(s) de BRECHA en rojo: %s. Posible brecha "
                    "real — revisa el detalle antes de reanudar. Corrido por el daemon "
                    "determinista (no dependía de que auto-mejora llegara viva a su paso 0b)."
                    % (len(brecha), ", ".join(n for n, _ in brecha)))
                if sin_comprobar:
                    cabeza += ("\n\n⚠️ De esos, %d no llegaron a terminar (%s): esos no prueban "
                               "nada, mira los otros." % (len(sin_comprobar), ", ".join(sin_comprobar)))
            codigo_rojo.trigger(
                motivo,
                "%s\n\n## Lo que dijo cada check (la evidencia, para poder distinguir brecha real "
                "de falso positivo)\n\n%s" % (cabeza, evidencia),
            )
        except Exception as e:
            sys.stderr.write("seguridad_sweep_daemon: sweep en rojo y no pude disparar código rojo: %r\n" % e)
        return 1

    if higiene:
        _heartbeat("higiene_pendiente")
        _avisar_higiene(higiene)
        return 3

    _heartbeat("ok")
    return 0


def main(argv):
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
