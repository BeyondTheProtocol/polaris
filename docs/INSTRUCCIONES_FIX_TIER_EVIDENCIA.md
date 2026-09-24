# Fix para Issue #13 - tier_evidencia.py: RCT requiere aleatorización explícita

**Issue:** https://github.com/BeyondTheProtocol/polaris/issues/13

## Problemas Identificados

### 1. Línea ~123: "Controlled Clinical Trial" → RCT (incorrecto)

**Problema:** El tipo de publicación `Controlled Clinical Trial` se convierte automáticamente en RCT, pero la existencia de controles no acredita que hubo aleatorización.

**Solución:** Exigir campo `randomized=True` explícito.

### 2. Línea ~197: "Clinical Trial, Phase III" → RCT (incorrecto)

**Problema:** `Clinical Trial, Phase III` se convierte en RCT, pero la fase no implica aleatorización.

**Solución:** Exigir campo `randomized=True` explícito.

### 3. humanos=True borra marca preclínica

**Problema:** `humanos=True` borra la marca preclínica aunque `in_vitro=True`. La señal humana puede venir de muestras, no de pacientes.

**Solución:** Permitir que estudio sea humano y preclínico a la vez (muestras humanas in vitro).

---

## Cambios Necesarios en `tools/tier_evidencia.py`

### CAMBIO 1: Función que determina tier por tipo de publicación (~línea 123)

**DÓNDE:** Busca la sección que asigna tier basado en `publication_type` o similar.

**ANTES (incorrecto):**
```python
if publication_type in ['Randomized Controlled Trial', 'Controlled Clinical Trial']:
    tier = 'RCT'
```

**DESPUÉS (correcto):**
```python
if publication_type == 'Randomized Controlled Trial':
    tier = 'RCT'
elif publication_type == 'Controlled Clinical Trial':
    # Solo es RCT si hay evidencia explícita de aleatorización
    if metadata.get('randomized', False):
        tier = 'RCT'
    else:
        tier = 'Controlled Trial'  # Bajar a tier inferior
```

---

### CAMBIO 2: Función que determina tier por fase (~línea 197)

**DÓNDE:** Busca la sección que asigna tier basado en `phase` o similar.

**ANTES (incorrecto):**
```python
if phase in ['Phase III', 'Phase 3']:
    tier = 'RCT'
```

**DESPUÉS (correcto):**
```python
if phase in ['Phase III', 'Phase 3']:
    # Fase III + aleatorización explícita = RCT
    if metadata.get('randomized', False):
        tier = 'RCT'
    else:
        tier = 'Controlled Trial'  # Fase III sin aleatorización ≠ RCT
```

---

### CAMBIO 3: Función que determina nivel preclínico

**DÓNDE:** Busca la función que determina si un estudio es preclínico (probablemente cerca del final del archivo).

**ANTES (incorrecto):**
```python
def es_preclinico(humanos, in_vitro, in_vivo):
    if humanos:
        return False  # Borra marca preclínica
    return in_vitro or not in_vivo
```

**DESPUÉS (correcto):**
```python
def es_preclinico(humanos, in_vitro, in_vivo):
    # in_vitro = preclínico, incluso si es de muestras humanas
    if in_vitro:
        return True
    
    # in_vivo no humano = preclínico
    if in_vivo and not humanos:
        return True
    
    # in_vivo humano = clínico (no preclínico)
    if in_vivo and humanos:
        return False
    
    return False
```

---

## Tests

**ARCHIVO NUEVO:** `tests/test_tier_evidencia.py` (ya creado en este PR)

**CÓMO EJECUTAR:**
```bash
cd tests
python3 test_tier_evidencia.py
```

---

## Criterios de Aceptación (Issue #13)

- [ ] Tests con metadatos sintéticos: fase III sin aleatorización no da RCT
- [ ] 'Randomized Controlled Trial' sí da RCT
- [ ] humano + in_vitro conserva la marca preclínica
- [ ] Ningún tier sube sin la señal que lo justifica

---

## Referencias

- Issue #13: https://github.com/BeyondTheProtocol/polaris/issues/13
- Auditoría externa (22-sep-2026), punto 1.3
- Líneas ~123 y ~197 de tier_evidencia.py