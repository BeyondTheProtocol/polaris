#!/usr/bin/env python3
"""tools/estado_rutina.py — distinguir «no había nada que hacer» de «lleva un mes rota».

POR QUÉ (issue #4 del repo público; `docs/lo-que-falta.md`, punto 5). Una rutina que deja de
correr no avisa, y desde fuera un «no ha hecho nada» se parece mucho a un «está rota». Esta
función pura pone nombre al estado con las fechas que ya se tienen. Sin red ni disco: quien
llama le da las fechas, y así se prueba con fechas sintéticas, bordes incluidos.

Primera versión: PR #22 de j7j7j7 (24-sep-2026). Incorporada aquí con cambios: umbral de «rota»
coherente con sus tests, fechas ilegibles o del futuro que no pueden dar `al-dia`, y el porqué
de cada umbral.

LOS ESTADOS Y SUS UMBRALES (P = periodo esperado entre ejecuciones)
  · nunca-corrio  no hay ninguna ejecución registrada.
  · al-dia        la última ejecución tiene P o menos, y el último éxito menos de 2P.
                  Justo en P sigue al día: le toca, pero aún no se le puede exigir.
  · atrasada      la última ejecución tiene más de P: le tocaba y no ha corrido. Un retraso
                  suelto pasa (reinicio, portátil cerrado), así que avisa sin dar la rutina
                  por perdida.
  · rota          el último éxito tiene 2P o más, o nunca lo hubo. A 2P ya ha pasado una
                  ventana ENTERA sin un solo éxito además de la que tocaba: dos seguidos no son
                  casualidad. Justo en 2P ya cuenta como rota.

FAIL-CLOSED (el issue lo exige: una fecha ilegible o ausente no puede dar `al-dia`)
  · Una fecha que no es fecha, una del futuro (reloj mal puesto o dato corrupto) o un éxito
    posterior a la última ejecución (incoherente) dan `rota`: no se puede afirmar que va bien.
  · Un periodo cero o negativo es un error de configuración de quien llama, no un estado:
    lanza ValueError.
"""
from datetime import datetime, timedelta

ESTADOS = ("al-dia", "atrasada", "rota", "nunca-corrio")


def clasificar(ultima_ejecucion, ultimo_exito, periodo_esperado, ahora):
    """→ 'al-dia' · 'atrasada' · 'rota' · 'nunca-corrio'. Umbrales y porqué: docstring del módulo.

    `ultima_ejecucion`: la última vez que corrió, con éxito o sin él (None si nunca).
    `ultimo_exito`: la última vez que terminó bien (None si nunca).
    `periodo_esperado`: timedelta entre ejecuciones. `ahora`: la fecha de referencia."""
    if not isinstance(periodo_esperado, timedelta) or periodo_esperado <= timedelta(0):
        raise ValueError("periodo_esperado tiene que ser un timedelta positivo: %r"
                         % (periodo_esperado,))
    if ultima_ejecucion is None:
        return "nunca-corrio"
    for fecha in (ultima_ejecucion, ultimo_exito):
        if fecha is not None and (not isinstance(fecha, datetime) or fecha > ahora):
            return "rota"                     # ilegible o del futuro: no se puede dar por buena
    if ultimo_exito is None or ultimo_exito > ultima_ejecucion:
        return "rota"                         # nunca terminó bien, o las fechas no cuadran
    if ahora - ultimo_exito >= periodo_esperado * 2:
        return "rota"
    if ahora - ultima_ejecucion > periodo_esperado:
        return "atrasada"
    return "al-dia"


def clasificar_desde_timestamps(ultima_ejecucion_ts, ultimo_exito_ts, periodo_esperado_dias,
                                ahora_ts=None):
    """Lo mismo con epoch Unix. None es «no hay»; 0 es una fecha de verdad (1970), no ausencia:
    tratarla como ausente convertía un dato corrupto en «nunca corrió»."""
    import time

    def fecha(ts):
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(float(ts))
        except (TypeError, ValueError, OverflowError, OSError):
            return "ilegible"                  # no es fecha: clasificar() la da por rota
    ahora = datetime.fromtimestamp(time.time() if ahora_ts is None else float(ahora_ts))
    return clasificar(fecha(ultima_ejecucion_ts), fecha(ultimo_exito_ts),
                      timedelta(days=periodo_esperado_dias), ahora)
