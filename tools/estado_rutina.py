#!/usr/bin/env python3
"""
estado_rutina.py - Clasificar el estado de una rutina

Issue: https://github.com/BeyondTheProtocol/polaris/issues/4

Función pura para distinguir «nada que hacer» de «lleva roto un mes».

Sin red ni disco. Solo lógica de fechas.
"""

from datetime import datetime, timedelta
from typing import Literal, Optional


def clasificar(
    ultima_ejecucion: Optional[datetime],
    ultimo_exito: Optional[datetime],
    periodo_esperado: timedelta,
    ahora: datetime
) -> Literal['al-dia', 'atrasada', 'rota', 'nunca-corrio']:
    """
    Clasifica el estado de una rutina basado en su historial de ejecución.
    
    Función pura: sin red, sin disco, sin efectos secundarios.
    
    Args:
        ultima_ejecucion: Fecha de la última ejecución (éxito o fallo)
        ultimo_exito: Fecha del último éxito conocido
        periodo_esperado: Periodo esperado entre ejecuciones (ej. timedelta(days=7))
        ahora: Fecha actual para comparar
    
    Returns:
        'al-dia': La rutina ejecutó recientemente (dentro del periodo esperado)
        'atrasada': La rutina no ejecutó en el periodo esperado, pero hay éxitos recientes
        'rota': La rutina no ejecutó exitosamente en mucho tiempo (>2x periodo)
        'nunca-corrio': La rutina nunca ha ejecutado
    
    Criterios de clasificación:
        - 'nunca-corrio': ultima_ejecucion es None
        - 'rota': ultimo_exito es None O (ahora - ultimo_exito) > 2 * periodo_esperado
        - 'atrasada': (ahora - ultima_ejecucion) > periodo_esperado PERO ultimo_exito es reciente
        - 'al-dia': (ahora - ultima_ejecucion) <= periodo_esperado
    
    Fail-closed:
        - Fechas ausentes o ilegibles NO devuelven 'al-dia'
        - Mejor clasificar como 'rota' o 'nunca-corrio' que como 'al-dia' por error
    """
    
    # Caso 1: Nunca ha ejecutado
    if ultima_ejecucion is None:
        return 'nunca-corrio'
    
    # Caso 2: Nunca ha tenido éxito (ejecutó pero siempre falla)
    if ultimo_exito is None:
        # Verificar si ejecutó recientemente pero siempre falla
        delta_ejecucion = ahora - ultima_ejecucion
        if delta_ejecucion > periodo_esperado * 2:
            return 'rota'
        else:
            # Ejecutó recientemente pero sin éxitos = potencialmente rota
            return 'rota'
    
    # Calcular deltas
    delta_ejecucion = ahora - ultima_ejecucion
    delta_exito = ahora - ultimo_exito
    
    # Caso 3: Éxito muy antiguo (>2x periodo) = rota
    if delta_exito > periodo_esperado * 2:
        return 'rota'
    
    # Caso 4: No ejecutó en el periodo esperado pero último éxito es reciente = atrasada
    if delta_ejecucion > periodo_esperado:
        return 'atrasada'
    
    # Caso 5: Ejecutó recientemente y último éxito es reciente = al-dia
    return 'al-dia'


# === Funciones auxiliares para facilitar uso ===

def clasificar_desde_timestamps(
    ultima_ejecucion_ts: Optional[float],
    ultimo_exito_ts: Optional[float],
    periodo_esperado_dias: int,
    ahora_ts: Optional[float] = None
) -> Literal['al-dia', 'atrasada', 'rota', 'nunca-corrio']:
    """
    Versión de clasificar() que acepta timestamps Unix.
    
    Args:
        ultima_ejecucion_ts: Timestamp de última ejecución (o None)
        ultimo_exito_ts: Timestamp de último éxito (o None)
        periodo_esperado_dias: Días esperados entre ejecuciones
        ahora_ts: Timestamp actual (default: time.time())
    
    Returns:
        Estado de la rutina: 'al-dia', 'atrasada', 'rota', 'nunca-corrio'
    """
    import time
    
    if ahora_ts is None:
        ahora_ts = time.time()
    
    # Convertir timestamps a datetime
    ultima_ejecucion = datetime.fromtimestamp(ultima_ejecucion_ts) if ultima_ejecucion_ts else None
    ultimo_exito = datetime.fromtimestamp(ultimo_exito_ts) if ultimo_exito_ts else None
    ahora = datetime.fromtimestamp(ahora_ts)
    periodo_esperado = timedelta(days=periodo_esperado_dias)
    
    return clasificar(ultima_ejecucion, ultimo_exito, periodo_esperado, ahora)


# === Ejemplos de uso ===

if __name__ == '__main__':
    # Ejemplo 1: Rutina diaria que ejecutó hoy
    hoy = datetime.now()
    estado = clasificar(
        ultima_ejecucion=hoy - timedelta(hours=2),
        ultimo_exito=hoy - timedelta(hours=2),
        periodo_esperado=timedelta(days=1),
        ahora=hoy
    )
    print(f"Ejemplo 1 (ejecutó hace 2h, diaria): {estado}")
    # Esperado: 'al-dia'
    
    # Ejemplo 2: Rutina semanal que no ejecutó en 10 días
    estado = clasificar(
        ultima_ejecucion=hoy - timedelta(days=10),
        ultimo_exito=hoy - timedelta(days=10),
        periodo_esperado=timedelta(days=7),
        ahora=hoy
    )
    print(f"Ejemplo 2 (no ejecutó en 10 días, semanal): {estado}")
    # Esperado: 'atrasada'
    
    # Ejemplo 3: Rutina que nunca ejecutó
    estado = clasificar(
        ultima_ejecucion=None,
        ultimo_exito=None,
        periodo_esperado=timedelta(days=1),
        ahora=hoy
    )
    print(f"Ejemplo 3 (nunca ejecutó): {estado}")
    # Esperado: 'nunca-corrio'
    
    # Ejemplo 4: Rutina que ejecutó ayer pero último éxito fue hace 1 mes
    estado = clasificar(
        ultima_ejecucion=hoy - timedelta(days=1),
        ultimo_exito=hoy - timedelta(days=30),
        periodo_esperado=timedelta(days=7),
        ahora=hoy
    )
    print(f"Ejemplo 4 (último éxito hace 30 días, semanal): {estado}")
    # Esperado: 'rota'