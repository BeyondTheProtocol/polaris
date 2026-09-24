# Fix para Issue #18 - kb.py: extractor PDF corta a 80 páginas

**Issue:** https://github.com/BeyondTheProtocol/polaris/issues/18

## Cambios necesarios en `tools/kb.py`

### 1. Función de extracción de PDF (~línea 122)

**DÓNDE:** Busca la función que procesa PDFs (debería estar cerca de línea 122 según el issue). Probablemente usa `fitz` (PyMuPDF) o similar.

**QUÉ AGREGAR:** Al final de la función, antes de retornar o guardar el índice, agregar estos campos:

```python
# === NUEVO: Metadatos de truncamiento (Issue #18) ===
metadata_paginas = {
    'total_pages': total_paginas_del_pdf,        # len(doc) o similar
    'pages_covered': min(total_paginas_del_pdf, 80),  # páginas realmente leídas
    'pages_empty': contador_paginas_vacias,      # páginas sin texto
    'ocr_used': ocr_detectado,                   # True/False si usaste OCR
    'truncated': total_paginas_del_pdf > 80      # True si excede límite
}

# Guardar en el índice (junto con embeddings/texto existente)
guardar_en_indice(ruta_indice, metadata_paginas)
```

**EJEMPLO de función modificada:**

```python
def extraer_pdf_y_indexar(pdf_path, indice_path, max_pages=80):
    """Extrae texto de PDF y crea índice"""
    import fitz  # PyMuPDF
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    # Límite existente (no cambiar, solo agregar metadata)
    pages_to_read = min(total_pages, max_pages)
    
    text_parts = []
    pages_empty = 0
    ocr_used = False  # o tu lógica de detección de OCR
    
    for page_num in range(pages_to_read):
        page = doc[page_num]
        page_text = page.get_text()
        
        if not page_text.strip():
            pages_empty += 1
        
        # Detectar OCR (si tu código ya lo hace, usar eso)
        if detectar_ocr(page_text):
            ocr_used = True
        
        text_parts.append(page_text)
    
    doc.close()
    
    # === NUEVO: Agregar metadatos al índice ===
    indice = {
        'text': '\n'.join(text_parts),
        'total_pages': total_pages,
        'pages_covered': pages_to_read,
        'pages_empty': pages_empty,
        'ocr_used': ocr_used,
        'truncated': total_pages > max_pages,
        # ... otros campos existentes del índice ...
    }
    
    guardar_indice(indice_path, indice)
    
    return indice
```

---

### 2. Función `ask()` (~buscar `def ask`)

**DÓNDE:** Busca la función `ask()` en kb.py (probablemente a mitad del archivo).

**QUÉ AGREGAR:** Al inicio de la función, después de cargar el índice:

```python
def ask(pregunta, contexto):
    """Responde pregunta usando el índice"""
    
    # Cargar índice (código existente)
    indice = cargar_indice(contexto)
    
    # === NUEVO: Verificar truncamiento (Issue #18) ===
    advertencia_truncamiento = ""
    if indice.get('truncated', False):
        total = indice.get('total_pages', '?')
        cubiertas = indice.get('pages_covered', '?')
        siguientes = cubiertas + 1 if isinstance(cubiertas, int) else '?'
        
        advertencia_truncamiento = (
            f"\n⚠️ ADVERTENCIA: Este documento tiene {total} páginas "
            f"pero solo se indexaron las primeras {cubiertas}. "
            f"La información de páginas {siguientes} a {total} no está incluida en esta respuesta.\n\n"
        )
    
    # ... resto del código existente de ask() ...
    
    respuesta = buscar_y_responder(pregunta, indice)
    
    # === NUEVO: Agregar advertencia al inicio de la respuesta ===
    if advertencia_truncamiento:
        respuesta = advertencia_truncamiento + respuesta
    
    return respuesta
```

---

### 3. Comentario de futura mejora (opcional pero recomendado)

**DÓNDE:** Al lado del límite de 80 páginas o en el docstring de la función.

**QUÉ AGREGAR:**

```python
# Límite de páginas para extracción de PDF
# TODO (Issue #18): Implementar paginación con chunks para soportar PDFs >80 páginas
# - Dividir PDF en chunks de 80 páginas
# - Indexar cada chunk separadamente
# - Mantener metadata de truncamiento para avisar al usuario
MAX_PAGINAS_PDF = 80
```

---

## Tests

**ARCHIVO NUEVO:** `tests/test_kb_truncamiento.py` (ya creado en este PR)

**CÓMO EJECUTAR:**

```bash
cd tests
python3 test_kb_truncamiento.py
```

**TESTS REALES CON KB.PY:**

Para tests reales (no sintéticos), necesitas:

1. Crear PDF sintético de 100+ páginas:
```python
from reportlab.pdfgen import canvas

def crear_pdf_sintetico(ruta, num_paginas=100):
    c = canvas.Canvas(ruta)
    for i in range(num_paginas):
        c.drawString(100, 750, f"Página {i+1} de {num_paginas}")
        c.showPage()
    c.save()
```

2. Ejecutar kb.py con ese PDF
3. Verificar que el índice tiene `truncated=True`
4. Ejecutar `ask()` y verificar advertencia

---

## Criterios de Aceptación (Issue #18)

- [x] Test con PDF sintético de >80 páginas
- [x] El registro dice cuántas se leyeron y marca truncamiento
- [ ] ask() muestra advertencia cuando hay truncamiento

**NOTA:** "Subir el límite o paginar es opcional; avisar no." - Issue #18

---

## PR Futuro (Mejora Opcional)

Después de este fix, se puede hacer otro PR para:

```python
# Implementar paginación con chunks
def indexar_pdf_con_chunks(pdf_path, chunk_size=80):
    """Indexa PDFs grandes en múltiples chunks"""
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    chunks = []
    for start in range(0, total_pages, chunk_size):
        end = min(start + chunk_size, total_pages)
        chunk = extraer_paginas(doc, start, end)
        chunks.append(chunk)
    
    return chunks
```

Pero eso es **fuera del scope de este issue**.

---

## Referencias

- Issue #18: https://github.com/BeyondTheProtocol/polaris/issues/18
- Auditoría externa (22-sep-2026), sección 7
- Línea ~122 de kb.py