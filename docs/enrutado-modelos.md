<!-- publicar: caso-explicito — este documento cuenta el caso a propósito: sin contexto
     clínico, nadie puede opinar con fundamento sobre qué modelo sirve para qué. La identidad
     (nombre, contactos, fecha de nacimiento) sigue fuera, aquí y en todo el repo. -->

# 🧠 Qué modelo se usa para qué, y por qué

> **Dónde vive esto:** `tools/enruta.py` decide y explica; `tools/borde.py` pone el límite de
> lo que puede salir. Los dos están publicados en este repo, así que la decisión se puede
> auditar sin ejecutar nada: `python3 tools/enruta.py "la tarea"` imprime a quién llamaría y
> con qué motivo, **sin gastar un token**.
>
> Lo que **no** se publica: las claves, y el ranking de salud medido en vivo (vive en
> `tools/state/`, que no viaja). El criterio sí; los datos de esa máquina, no.

## 🔢 El orden de criterios, y el primero no se negocia

| # | Criterio | Qué significa |
|---|---|---|
| 1️⃣ | 🚧 **El muro** | Si el contenido es clínico, genómico, PII o lleva un término vetado (`borde.clasificar`), solo quedan destinos de confianza. **Fail-closed: ante la duda, no sale.** |
| 2️⃣ | 🎯 **Capacidad** | Cada proveedor tiene aquello para lo que es mejor, con el motivo escrito al lado |
| 3️⃣ | 🩺 **Salud** | Un proveedor que no responde no se propone. Se **mide**, no se supone |
| 4️⃣ | 💸 **Coste o calidad** | Si la tarea pide volumen, gana el gratis. En todo lo demás gana el mejor: ahorrar un dólar en una decisión que importa es mal negocio |
| 5️⃣ | 🧑‍⚖️ **Panel** | Para lo crítico no se elige uno: se piden varios y se contrastan. **De casas distintas**, porque dos modelos de la misma familia se equivocan igual |

## 🗺️ Quién hace qué hoy

Esta es la lista **real** de lo que hay conectado hoy, con el modelo por defecto de cada carril
(los valores viven en `tools/<proveedor>.py`, así que se pueden comprobar):

| Proveedor | Modelo por defecto | Para qué | ¿Puede ver material sensible? |
|---|---|---|---|
| 🏠 **Local** (ollama) | `qwen3:8b` | De-identificar, extraer y clasificar. **Egress cero** | ✅ Sí, y es el **único** destino del dato crudo |
| 🟣 **Claude** | el runtime de Claude Code | Razonar, y todo lo que toca material sensible | ✅ Sí |
| ⚫ **Grok** | `grok-4.3` | **Rastrear**: lo que no está en los índices limpios — X en vivo, foros, fuentes marginales | ❌ No |
| 🔵 **Perplexity** | `perplexity/sonar` | Búsqueda con fuentes citadas **y carril multi-casa: 46 modelos con una sola clave** | ❌ No |
| 🟠 **ChatGPT** | `gpt-5` | Segunda voz del panel, razonar | ❌ No |
| 🔷 **Gemini** | `gemini-2.5-pro` | Tercera voz del panel, contexto largo | ❌ No |
| 🟩 **NVIDIA** | `nemotron-3-ultra-550b` | Volumen y tareas mecánicas. Gratis | ❌ Nunca |
| 🟨 **GLM** | `glm-5.2` | Alternativa barata. **Sin saldo desde el 19-sep-26**; su relevo es `perplexity/glm-5.3`, el mismo modelo con una clave que ya se paga | ❌ Nunca |

**Salvedades escritas en el propio código**, no aquí de adorno: Grok es el menos fiable del grupo
en citas y se cotejan siempre; el modelo local de-identifica y clasifica bien, pero **para razonar
no llega**; los carriles gratis no ven nada clínico jamás.

Y para evidencia médica, antes que cualquier LLM van los **MCP de literatura y datos**: `scite`,
PubMed/PMC, `biomcp` y `cbioportal` (ver `.mcp.json`).

### 🔎 Una clave, 46 modelos (19-sep-2026)

El Agent API de Perplexity sirve modelos de **varias casas** con la misma clave —Anthropic,
OpenAI, Google, xAI y los propios— y eso cambia dos cosas: hay suplente cuando a un proveedor se
le acaba el saldo, y se puede comparar sin abrir cuenta nueva. `python3 tools/perplexity.py --modelos`
lo lista en vivo.

Con un cuidado que no es menor: **el agente solo busca si se le pide** (`tools: web_search`). Sin
eso responde de memoria — en la primera prueba se inventó un ensayo clínico entero y devolvió cero
fuentes. Un carril de citas sin citas no es más barato: es otro peor, disfrazado.

### 🩺 Y alguien vigila que todo esto siga vivo

Desde el 19-sep, cada seis horas se comprueba que **cada proveedor responde de verdad** (llamada
real, no un `GET` que devuelve 200 con el saldo a cero) y se distinguen dos cosas que exigen
acciones distintas: **sin saldo** (hay que recargar) y **caído** (se reintenta y degrada solo, y
avisa solo tras dos pasadas seguidas, porque un timeout suelto no es una avería).

**Toda cita se abre y se coteja** antes de usarse, venga del modelo que venga. Un modelo que
inventa un PMID en un dossier clínico no es un error de estilo.

## 🚫 Lo que nunca sale de la máquina

- Datos crudos: VCF, tipado HLA, DICOM, informes enteros. Van **solo** al modelo local.
- PII y claves, en cualquier destino no confiable.
- Si algo tiene que salir sí o sí, sale **de-identificado** y el barrido lo comprueba antes.

## 🙋 Aquí es donde nos puedes ayudar de verdad

### 🩺 El caso, porque sin él no se puede opinar bien

Este arnés no es un experimento de ingeniería. Sostiene un caso real, y lo contamos con
detalle a propósito: para saber si un modelo sirve hay que saber para qué.

- **Diagnóstico:** cáncer de mama **metastásico**, receptor hormonal positivo y HER2 negativo,
  con **diferenciación neuroendocrina**. Es una combinación poco frecuente.
- **Por qué complica todo:** las guías de mama están escritas para el subtipo común. Un tumor
  con componente neuroendocrino se comporta en parte como otra enfermedad, y la literatura que
  aplica está repartida entre dos mundos que casi no se citan entre sí: mama y tumores
  neuroendocrinos.
- **Consecuencia práctica:** muchos ensayos de mama no encajan, y los de tumores
  neuroendocrinos suelen excluir mama. Encontrar lo que sí encaja es trabajo de buscar fino,
  no de preguntar a un chatbot.
- **Hacia dónde va el sistema:** ordenar el perfil del tumor, encontrar dianas y llegar a un
  tratamiento personalizado, por la vía más rápida que sea segura.

Un modelo que lee mal un molecular, que se inventa un ensayo o que no distingue un resultado
en ratón de uno en pacientes **cuesta semanas**. Por eso esto no es una pregunta académica.

Si trabajas con modelos, con bioinformática o con literatura médica, **estas son las preguntas
abiertas** y una opinión fundada vale más que un PR:

| 🧩 Tarea | Lo que usamos hoy | Lo que queremos saber |
|---|---|---|
| 📚 Leer literatura y **no inventar citas** | scite + MCP de literatura, cotejo obligatorio | ¿Hay algo mejor que un buscador LLM para recall en oncología de subtipos raros? |
| 🧬 Interpretar informes moleculares | Claude + cotejo humano | ¿Algún modelo o herramienta específica de genómica clínica que merezca la pena, y se pueda correr **local**? |
| 🔬 Predecir presentación de péptidos (pipeline de neoantígenos) | MHCflurry local; NetMHCpan y pVACtools como piezas opcionales | ¿Qué predictor darías por bueno hoy, y con qué evidencia? |
| 🧾 Encontrar ensayos que encajen | Registros oficiales (no LLM) + radar propio | ¿Qué fuente cubre mejor ensayos fuera de EE. UU. y Europa? |
| 🩸 Tumores «fríos» e inmunoterapia | Comité con verificación adversarial | Mecanismos trasladables que estemos pasando por alto |
| 🏠 Modelos abiertos en local | qwen3:8b para tareas mecánicas | ¿Qué modelo abierto razona lo bastante bien para material sensible, en un Mac? |

**Cómo proponer algo** → abre un issue con la plantilla **🧠 Propuesta de modelo o herramienta**
y cuenta: qué modelo o herramienta, **para cuál de esas tareas**, con qué evidencia (un
benchmark, un paper, tu experiencia real), y si puede correr **en local** o exigiría sacar datos
fuera. Esa última parte decide casi siempre.

> ⚖️ **Una cosa que no hacemos:** pedirle a un modelo que decida el tratamiento. El sistema
> prepara evidencia y ordena el caso; **quien decide es el equipo médico**. Un modelo mejor
> acorta el camino hasta esa conversación, no la sustituye.
