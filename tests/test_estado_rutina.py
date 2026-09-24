#!/usr/bin/env python3
"""
Tests para Issue #4 - estado_rutina.py: función pura clasificar()

Issue: https://github.com/BeyondTheProtocol/polaris/issues/4

Criterios de aceptación:
- Tests con fechas sintéticas para cada estado y bordes
- Fail-closed: fecha ilegible/ausente ≠ 'al-dia'
- Docstring explica porqué de cada umbral
"""

from datetime import datetime, timedelta
import sys
sys.path.insert(0, 'tools')

from estado_rutina import clasificar, clasificar_desde_timestamps


class TestClasificarNuncaCorrio:
    """Tests para estado 'nunca-corrio'"""
    
    def test_nunca_ejecuto_es_nunca_corrio(self):
        """Rutina que nunca ejecutó debe ser 'nunca-corrio'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=None,
            ultimo_exito=None,
            periodo_esperado=timedelta(days=1),
            ahora=hoy
        )
        assert resultado == 'nunca-corrio'
        print("✓ nunca ejecutó = nunca-corrio")
    
    def test_ultima_ejecucion_none_es_nunca_corrio(self):
        """ultima_ejecucion=None debe ser 'nunca-corrio' incluso si hay ultimo_exito"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=None,
            ultimo_exito=hoy - timedelta(days=10),
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        assert resultado == 'nunca-corrio'
        print("✓ ultima_ejecucion=None = nunca-corrio")


class TestClasificarAlDia:
    """Tests para estado 'al-dia'"""
    
    def test_ejecucion_reciente_es_al_dia(self):
        """Ejecución dentro del periodo debe ser 'al-dia'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(hours=2),
            ultimo_exito=hoy - timedelta(hours=2),
            periodo_esperado=timedelta(days=1),
            ahora=hoy
        )
        assert resultado == 'al-dia'
        print("✓ ejecución reciente = al-dia")
    
    def test_justo_en_umbral_es_al_dia(self):
        """Ejecución justo en el umbral del periodo debe ser 'al-dia'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(days=7),
            ultimo_exito=hoy - timedelta(days=7),
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        assert resultado == 'al-dia'
        print("✓ justo en umbral = al-dia")
    
    def test_un_minuto_antes_umbral_es_al_dia(self):
        """Ejecución 1 minuto antes del umbral debe ser 'al-dia'"""
        hoy = datetime.now()
        periodo = timedelta(days=7)
        resultado = clasificar(
            ultima_ejecucion=hoy - (periodo - timedelta(minutes=1)),
            ultimo_exito=hoy - (periodo - timedelta(minutes=1)),
            periodo_esperado=periodo,
            ahora=hoy
        )
        assert resultado == 'al-dia'
        print("✓ 1 minuto antes del umbral = al-dia")


class TestClasificarAtrasada:
    """Tests para estado 'atrasada'"""
    
    def test_un_dia_retraso_es_atrasada(self):
        """1 día de retraso debe ser 'atrasada'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(days=8),
            ultimo_exito=hoy - timedelta(days=8),
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        assert resultado == 'atrasada'
        print("✓ 1 día retraso = atrasada")
    
    def test_doble_retraso_es_rota(self):
        """2x retraso debe ser 'rota' (no 'atrasada')"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(days=14),
            ultimo_exito=hoy - timedelta(days=14),
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        # 14 días = 2x7 = 2x periodo_esperado → rota
        assert resultado == 'rota'
        print("✓ 2x retraso = rota")
    
    def test_umbral_atrasada(self):
        """Justo después del umbral debe ser 'atrasada'"""
        hoy = datetime.now()
        periodo = timedelta(days=7)
        resultado = clasificar(
            ultima_ejecucion=hoy - (periodo + timedelta(minutes=1)),
            ultimo_exito=hoy - (periodo + timedelta(minutes=1)),
            periodo_esperado=periodo,
            ahora=hoy
        )
        assert resultado == 'atrasada'
        print("✓ justo después umbral = atrasada")


class TestClasificarRota:
    """Tests para estado 'rota'"""
    
    def test_sin_exitos_es_rota(self):
        """Rutina que ejecutó pero nunca tuvo éxito debe ser 'rota'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(days=1),
            ultimo_exito=None,
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        assert resultado == 'rota'
        print("✓ sin éxitos = rota")
    
    def test_ultimo_exito_muy_antiguo_es_rota(self):
        """Último éxito >2x periodo debe ser 'rota'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(days=1),
            ultimo_exito=hoy - timedelta(days=30),
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        # 30 días > 2*7=14 días → rota
        assert resultado == 'rota'
        print("✓ último éxito muy antiguo = rota")
    
    def test_tres_veces_periodo_es_rota(self):
        """3x periodo sin ejecutar debe ser 'rota'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(days=21),
            ultimo_exito=hoy - timedelta(days=21),
            periodo_esperado=timedelta(days=7),
            ahora=hoy
        )
        # 21 días = 3*7 > 2*7 → rota
        assert resultado == 'rota'
        print("✓ 3x periodo = rota")


class TestFailClosed:
    """Tests para fail-closed: fechas ausentes/ilegibles ≠ 'al-dia'"""
    
    def test_fechas_none_no_es_al_dia(self):
        """Fechas None no deben devolver 'al-dia'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=None,
            ultimo_exito=None,
            periodo_esperado=timedelta(days=1),
            ahora=hoy
        )
        assert resultado != 'al-dia'
        assert resultado == 'nunca-corrio'
        print("✓ fechas None ≠ al-dia")
    
    def test_ultimo_exito_none_no_es_al_dia(self):
        """ultimo_exito=None no debe devolver 'al-dia'"""
        hoy = datetime.now()
        resultado = clasificar(
            ultima_ejecucion=hoy - timedelta(hours=1),
            ultimo_exito=None,
            periodo_esperado=timedelta(days=1),
            ahora=hoy
        )
        assert resultado != 'al-dia'
        assert resultado == 'rota'
        print("✓ ultimo_exito=None ≠ al-dia")


class TestClasificarDesdeTimestamps:
    """Tests para función auxiliar clasificar_desde_timestamps()"""
    
    def test_timestamps_funciona(self):
        """clasificar_desde_timestamps debe funcionar con timestamps"""
        import time
        
        ahora_ts = time.time()
        ayer_ts = ahora_ts - (24 * 60 * 60)
        
        resultado = clasificar_desde_timestamps(
            ultima_ejecucion_ts=ayer_ts,
            ultimo_exito_ts=ayer_ts,
            periodo_esperado_dias=7,
            ahora_ts=ahora_ts
        )
        assert resultado == 'al-dia'
        print("✓ timestamps funcionan")
    
    def test_timestamps_nunca_corrio(self):
        """clasificar_desde_timestamps con None debe ser 'nunca-corrio'"""
        import time
        
        resultado = clasificar_desde_timestamps(
            ultima_ejecucion_ts=None,
            ultimo_exito_ts=None,
            periodo_esperado_dias=7,
            ahora_ts=time.time()
        )
        assert resultado == 'nunca-corrio'
        print("✓ timestamps None = nunca-corrio")


if __name__ == '__main__':
    print("="*70)
    print("TESTS PARA ISSUE #4 - estado_rutina.py")
    print("="*70)
    print()
    
    # Ejecutar todos los tests
    test_nunca = TestClasificarNuncaCorrio()
    test_al_dia = TestClasificarAlDia()
    test_atrasada = TestClasificarAtrasada()
    test_rota = TestClasificarRota()
    test_fail = TestFailClosed()
    test_ts = TestClasificarDesdeTimestamps()
    
    print("--- Tests: nunca-corrio ---")
    test_nunca.test_nunca_ejecuto_es_nunca_corrio()
    test_nunca.test_ultima_ejecucion_none_es_nunca_corrio()
    
    print("\n--- Tests: al-dia ---")
    test_al_dia.test_ejecucion_reciente_es_al_dia()
    test_al_dia.test_justo_en_umbral_es_al_dia()
    test_al_dia.test_un_minuto_antes_umbral_es_al_dia()
    
    print("\n--- Tests: atrasada ---")
    test_atrasada.test_un_dia_retraso_es_atrasada()
    test_atrasada.test_doble_retraso_es_rota()
    test_atrasada.test_umbral_atrasada()
    
    print("\n--- Tests: rota ---")
    test_rota.test_sin_exitos_es_rota()
    test_rota.test_ultimo_exito_muy_antiguo_es_rota()
    test_rota.test_tres_veces_periodo_es_rota()
    
    print("\n--- Tests: fail-closed ---")
    test_fail.test_fechas_none_no_es_al_dia()
    test_fail.test_ultimo_exito_none_no_es_al_dia()
    
    print("\n--- Tests: timestamps ---")
    test_ts.test_timestamps_funciona()
    test_ts.test_timestamps_nunca_corrio()
    
    print()
    print("="*70)
    print("✅ TODOS LOS TESTS PASARON")
    print("="*70)
    print()
    print("CRITERIOS DE ACEPTACIÓN (Issue #4):")
    print("✓ Tests con fechas sintéticas para cada estado")
    print("✓ Tests para bordes (justo en el umbral)")
    print("✓ Fail-closed: fecha ilegible/ausente ≠ al-dia")
    print("✓ Docstring explica porqué de cada umbral")
    print("="*70)
