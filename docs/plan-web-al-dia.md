# Plan: helptitular.com se mantiene al día sola (Vega detecta y propone, {{TITULAR}} dice «sí»)

17-oct-2026 · v2 · BORRADOR para el OK de {{TITULAR}} · rama `claude/keen-haslett-82bd5f`
Sustituye y amplía `docs/plan-cronologia-automatica.md`: la cronología pasa a ser uno de los cuatro carriles.

## TL;DR

- **Lo que pidió {{TITULAR}}:** que el sistema detecte la información nueva que merece estar en la web y proponga qué **añadir o cambiar en cualquier página**. Le llega por Telegram con el antes y el después, y se publica con su «sí».
- **El caso de hoy muestra dos huecos distintos** [verificado: `gh pr view 280/281` + `git show origin/main:content/es/science.yml`]:
  1. Los **estados escritos a mano** (`science.yml`: «Material en el laboratorio», «Pendiente: aún no enviada»…) no están atados a ninguna fuente interna. Nadie podía saber que el del single-cell había caducado. Lo arregló el PR #281 a mano.
  2. **`/datos`** se genera desde `fuente.json` con `caso_publico.py`. El guardián ya detecta cuándo es más viejo que sus fuentes (`senal_datos_viejos`), pero se queda en un hallazgo en sombra: nadie regenera ni propone.
- **La solución tiene tres piezas:**
  1. **Mapa de afirmaciones públicas:** un fichero que ata cada estado o cifra visible a su fuente interna.
  2. **Detector** determinista y sin IA, en la pasada diaria del guardián.
  3. **Redactor único diario:** prepara **un** PR con hasta 3 cambios, espera al CI y encarga el Telegram con el texto sellado.
- **El final del circuito ya existe y no se toca:** `web_lote.pedir_ok` → `salida._draft(sellado)` → su «sí» en `bot_telegram` → `web_publicar.fusionar_aprobado`, que comprueba el commit y la huella del texto.
- **Prioridad:** lo primero es **lo que ya es falso en la web**. Una web que dice «en marcha» cuando ya hay resultados es una mentira pública, y eso pesa más que cualquier novedad.

## 0. Decisiones de {{TITULAR}} (17-oct, respuesta a los UNKNOWNS)

| # | Decidió | Validación | Cómo queda en el plan |
|---|---|---|---|
| 1 | **Las cifras de dianas se publican** («son datos correctos y quiero enseñarlos porque ya me está pasando gente estudios») | **aprobado con matiz:** cada cifra se **coteja** contra su fuente antes del Telegram. En el cotejo de hoy salió que la web publica NY-BR-1 **≈50 de cada 100 (aprox.)**, mientras que la nota interna dice 58 % [verificado: `git show origin/main:public/celula/resultados.json`, líneas 23 y 27]. Si el redactor teclea la cifra, publica la equivocada. La corrección clínica de la cifra la firma {{CONTACTO}}; tu «sí» firma que se publique | §6 cotejo determinista |
| 2 | «Lo que consideres respecto a cómo navega mejor la gente» | lo decido yo con ese criterio | §7.7: la lista se mantiene. Hero, navegación y portadas de sección deciden por dónde entra y se mueve la gente; un cambio automático ahí le cambia el camino sin que lo vea |
| 3 | «Son 3 ciclos claramente»: lo leo como **una entrada por ciclo** | **aprobado con matiz:** entradas cortas, mismo tag, título sin cifras de dosis (el lint ya para dosis y pautas). Otra lectura posible es «solo inicio y fin»; sigo con la de por ciclo | carril C, fuente de ciclos en §3 |
| 4 | **El inglés se hace solo** a partir del español que apruebas | **aprobado con matiz:** se **adapta** con `voz-titular`, no se traduce palabra a palabra (`feedback-version-idioma-adaptar-no-traducir`). Va en el mismo PR y bajo la misma huella; el Telegram te enseña solo el español y una línea «inglés adaptado incluido» | §7 |
| 5 | «Lo que consideres» | 7 días en sombra, y antes de encenderlo te enseño 3 propuestas reales | §10 |
| 7 | **Lote** | aprobado | §7.1: «sí» publica todo; «sin el N» lo quita y reenvía |

## 1. Los cuatro carriles

| Carril | Página / fichero | De dónde nace el cambio | Tipo | Peldaño |
|---|---|---|---|---|
| A. Estados | `/ciencia` (`content/{es,en}/science.yml` → `status`, `statusTone`), insignias de las herramientas | cambio de estado en el Tablero, una nota archivada con resultados, un informe nuevo del tipo que toca | **CAMBIAR** copy publicado | 2 (antes→después; 3 si toca `implication` con un dato clínico o lleva una cifra de diana) |
| B. Panel `/datos` | `app/data/caso.json`, `public/datos/caso.json` | `fuente.json` o `biomarcadores/timeline.json` más nuevos que `caso.json.generado` (ya lo detecta el guardián) | regenerar con `caso_publico.py` | analíticas ya selladas: 1 (aprobado el 24-sep); perfil curado: 3 |
| C. Cronología | `content/{es,en}/timeline.yml` | informe nuevo, nota con `--hito-web`, inicio o fin de línea o ensayo, PR que abre una página nueva | **AÑADIR** (nunca modificar) | 2 |
| D. Páginas de resultados | `/celula-a-celula` (`public/celula/resultados.json`) y las que vengan | nota de análisis con resultados nuevos, sellada por `verificacion` | añadir o cambiar cifras | **3 siempre**, con `comite-medico` antes del Telegram |

**Fases:** A, B y C en la fase 1. D en la fase 2: son cifras clínicas nuevas y necesitan la cadena de verificación delante. Hoy esas cifras las sube una sesión a mano, con tests de cifras propios (`scripts/test-celula-cifras.mjs`).

## 2. Pieza nueva: el mapa de afirmaciones públicas

`tools/state/web_mapa/afirmaciones.json`, versionado y revisable. Una fila por cada cosa que la web afirma y que puede caducar:

```json
{"id": "ciencia-scrna-flex",
 "fichero": "content/es/science.yml", "ancla": "panelRows[component=Single-cell RNA-seq (10x Genomics Flex)].status",
 "valor_publico": "Primeros resultados (octubre 2026)",
 "fuente": {"tipo": "tablero", "hilo": "<id de seguimiento.json>"},
 "transiciones": {"en_curso": "Material en el laboratorio", "resultados": "Primeros resultados ({mes} {año})", "hecho": "Hecho ({mes} {año})"}}
```

- **Por qué un mapa y no una IA leyendo la web:** «merece la pena» tiene que ser comprobable. Con el mapa, el detector sabe *qué* afirmación caducó y *por qué*. Sin él, cada pasada sería una opinión distinta.
- **Se siembra una vez a mano**, con las ~12 filas de `panelRows` de `science.yml`, las insignias de herramientas y los campos de estado de `/datos`. Lo hago yo y {{TITULAR}} lo ve en la sombra.
- **Test de cobertura:** todo `status:` de `science.yml` sin fila en el mapa pone el test en rojo. Así una fila nueva en la web no puede quedarse sin vigilancia, que es justo lo que pasó con el single-cell.
- Las transiciones son **copy ya escrito**. El detector elige la frase de la plantilla, no la redacta. Lo que no encaja en una plantilla pasa al redactor.
- **Cada frase de transición la aprueban una vez `voz-titular` y `diseno`**, en ES y en EN escritas aparte, antes de sembrar el mapa. Tope: 3 frases por estado (`diseno`).
- **Estado e `implication` van juntos:** si cambia el estado y la `implication` promete algo clínico («cierra el bucle», «guía combinaciones»), el cambio sube a peldaño 3 (`diseno`).
- **El Tablero solo no basta** (Vega: un hilo puede figurar «hecho» sin estarlo). La fila del mapa exige **evidencia**: `hecho_cuando` del hilo, una nota archivada o un informe. Sin evidencia, el hecho queda «por confirmar» y no se propone.
- **El mapa solo enlaza hilos no privados.**
- **Las dos direcciones:** si un hilo se reabre o retrocede, la web también queda falsa. Mismo carril, prioridad 1 (Vega).

## 3. Las fuentes de detección

| Fuente | Qué se lee | Identidad del hecho (dedup) | Alimenta |
|---|---|---|---|
| Bóveda (`historial/_eventos.jsonl`) | informe nuevo: fecha, carpeta (tipo) | fecha del informe + carpeta + tipo (no solo el sha, que cambia si se re-exporta) | A, C |
| Archivo de notas (`archivar_nota.py`) | solo notas con la bandera nueva `--hito-web` y una línea `afecta: <id del mapa>` | ruta de la nota | A, C, D |
| Tablero (`seguimiento.json`, 131 hilos) | **transición de estado** de un hilo enlazado desde el mapa (p. ej. «sangre germinal» pasa a enviada) | hilo + estado nuevo | A |
| Ciclos de tratamiento (uno a uno, decisión de {{TITULAR}}) | por localizar (UNKNOWN 3b) | ensayo + nº de ciclo + fecha | C |
| PR web fusionados | rama que **no** sea `auto/`, que cree una página o una sección | nº de PR | C |
| Fuentes del panel | `fuente.json` y `biomarcadores/timeline.json` más nuevos que `caso.json` | `generado` de la fuente | B |

**Lo que NO se lee:** correo, redes, DMs ni WhatsApp. Es texto que escribe cualquiera, y una frase plantada no puede tener vía a una web pública con su nombre. Es la regla que ya cumple el guardián.

## 4. Cómo se decide qué «merece la pena» (determinista, por orden)

1. **La web afirma algo que ya es falso** (una fila del mapa cuya fuente cambió de estado): **siempre merece**, prioridad 1. Ejemplo: el single-cell de hoy.
2. **`/datos` más viejo que sus fuentes:** merece, prioridad 2. Si solo cambian analíticas selladas, peldaño 1: viaja sin pedirle lectura, como se aprobó el 24-sep.
3. **Hito del caso sin entrada en la cronología** (informe, resultados, inicio o fin de línea, página nueva): merece, prioridad 3.
4. **No merece:** commits internos, PR mecánicos (`fix`, `chore`, `a11y` sin página nueva), notas sin `--hito-web`, ciclos sueltos de un mismo tratamiento, y nada que solo exista en una fuente externa.

**El redactor no decide si algo merece.** Solo redacta lo que el detector ya marcó. El juicio queda en reglas que se pueden testear.

## 5. Antispam (`feedback-todo-aviso-a-titular-necesita-anti-spam`)

- `state/web_mapa/vistos.json`: identidad → `encolado` → `propuesto` → `publicado` / `recordado` / `descartado`. El detector escribe `encolado` **al detectar**, así que un hecho que espera no se vuelve a encolar.
- **1 PR y 1 Telegram de la web al día**, con hasta 3 cambios que pidan lectura (`TOPE_ATENCION` de `web_lote`) y hasta 5 mecánicos. Lo que no cabe espera al día siguiente, sin aviso.
- **Un solo PR vivo** de este carril, apuntado en `vistos.json` y protegido con `_lock`. Dos PRs que tocan el mismo fichero invalidan su «sí» al rebasar, y `web_lote.validar` ya lo prohíbe.
- Si no responde: 1 recordatorio a los 3 días y luego silencio. Queda una línea en el parte semanal.
- **Excepción para la prioridad 1** (la web dice algo falso): 2º recordatorio a los 7 días y un hilo «aprobar el cambio de la web» en el Tablero, con plazo, que se cierra solo al fusionar. Una mentira pública no puede acabar en silencio (Vega).
- Cuenta dentro del **tope general de 3 avisos no urgentes** de `salida.py`, respeta el **silencio de 23 a 8 h** y lleva `origen: "web-al-dia"` para que sus 👍/👎 se midan. Las vueltas de cambios no cuentan contra el tope.

## 6. El freno de contenido: `web_lint`

- Corre sobre **cada texto nuevo o cambiado**, antes del Telegram. Si falla, el redactor reescribe una vez sin la cifra o el nombre. Si vuelve a fallar, el cambio no se le enseña: queda una línea en el parte, sin el texto.
- **Cifras de dianas: se publican, pero cotejadas** (decisión de {{TITULAR}}, §0). La deuda `web-lint-no-caza-diana-con-porcentaje` cambia de arreglo:
  1. El lint pasa a **detectar** «diana + cifra» (hoy no la ve). No la bloquea: la marca como `cifra-diana`.
  2. Una cifra marcada solo sale si coincide **literalmente** con un valor de una fuente permitida: `public/celula/resultados.json`, que ya se publicó con su OK, o una nota archivada con sello `verificado` que la contenga. Lo comprueba el código, no el redactor.
  3. **El redactor nunca teclea cifras**, las copia de la fuente. Si la cifra no casa con ninguna, el cambio no se le enseña: queda en el parte como «cifra sin cotejar».
  4. Test de regresión con el caso real: «58 %» contra una fuente que dice 50 aprox. → no sale.
  Hasta que esto esté en verde, no se pasa a modo activo.
- `web_lint` ya para el léxico del muro («{{CONTACTO}}», «ingeniera») y a los terceros nombrados [verificado: docstring y constantes `MARCA`/`RE_TERCEROS` de `tools/web_lint.py`]. Se aplica a todo texto nuevo, no solo a las cifras, como pide `diseno`.
- **Edades:** la edad sí se dice en público, por regla de {{TITULAR}} del 26-sep (`feedback-edad-si-se-dice-en-publico`). El automático no la **recalcula** por su cuenta: si una entrada la menciona, la toma del copy ya aprobado. Así queda acotado el matiz de `diseno`, que no puede leerse como un veto.
- Excepción ya auditada: `/datos` tiene su propio freno (`web_lint.revisar_caso`, que exige sello en cada dato) y `caso_publico.py` es fail-closed. No se duplica.
- Clínico: los carriles A y C pueden llevar **cifras de dianas cotejadas** (punto anterior). Marcadores diagnósticos, variantes y dosis siguen parados por el lint como hoy. La corrección clínica la firma {{CONTACTO}}; el «sí» de {{TITULAR}} firma la publicación.

## 7. La firma atada al contenido

1. El Telegram es `web_lote.pedir_ok`: **antes → después** de cada texto (`textos_del_pr`), la preview de Netlify y una línea de qué acerca a NED. **Formato:** lista numerada, un bloque corto por cambio (📄 página · ❌ antes · ✅ después), la preview al final y nada de párrafos. Sin tablas: Telegram no las renderiza (regla de Vega en `asistente.md`). Así se resuelve la discrepancia con `diseno`, que pedía tabla.
   - **«sí»** publica todo. **«sin el 2»** (o cualquier otro cambio) rehace el PR sin ese cambio y le reenvía un mensaje corto con lo que queda. No se publica un subconjunto que no haya visto sellado, porque la firma va atada al contenido.
2. El borrador va sellado con PR + commit + huella del texto (`salida._draft(sellado=…)`). Si algo cambia antes de su «sí», no sale.
3. Su «sí» citando el aviso dispara `fusionar_aprobado`, que vuelve a comprobar commit y huella contra GitHub y luego verifica el despliegue.
4. Si responde con cambios, se rehacen **en el mismo PR**, con commit y huella nuevos, se retira el borrador viejo y se le reenvía. Máximo 3 vueltas.
5. `web_novedad.proponer()` y el nuevo `web_al_dia.proponer()` **nunca** llaman a `fusionar` ni miran el permiso de sesión. Va en el código y un test lo vigila.
6. Cambiar copy ya publicado sigue siendo gate suyo (`marca-copy.md`). Esto no lo relaja: le pone el antes y el después delante y su «sí» es el OK.
7. **Siempre a mano, fuera del automático** (lista cerrada en código): hero/H1, navegación, «quién soy», la página de la vacuna, `team.yml`, `press.yml` y cualquier cita de personas o instituciones (`diseno`, impacto crítico).
8. Los títulos de la cronología son narrativos y no salen de plantilla: los redacta el redactor y los pasa `voz-titular`.

## 8. Cómo encaja (flujo)

```
07:35 guardián (launchd, sin IA)
  └─ detector: mapa + bóveda + notas + Tablero + PR + fuentes del panel
       └─ vistos.json: hechos nuevos → encolado (prioridad 1/2/3)
08:00 redactor único (cola, cerebro de confianza vía enruta.py, nada externo)
  └─ ≤3 cambios: plantilla del mapa | caso_publico.py | entrada de cronología
  └─ web_lint → worktree desde origin/main → 1 PR rama auto/web-al-dia-AAAA-MM-DD
  └─ esperar_ci → encargar_pedir_ok + kick al guardián
Telegram con antes/después ─ «sí» ─ fusionar_aprobado ─ despliegue verificado
```

## 9. Tests (todos en `tests/test_all.sh`)

1. Mapa: todo `status:` de `science.yml` tiene fila (cobertura). Una fila con su fuente en un estado nuevo → 1 cambio propuesto con el texto de la plantilla.
2. Caso de hoy como fixture: hilo del single-cell en «resultados» + `science.yml` diciendo «Material en el laboratorio» → propuesta de prioridad 1 con el antes y el después correctos.
3. Dedup: el mismo hecho en dos pasadas con un PR vivo → 1 trabajo, 1 PR, 1 Telegram. Tras el recordatorio, silencio.
4. Tope: 5 hechos el mismo día → 3 en el PR y 2 esperando, sin aviso extra.
5. Exclusión: rama `auto/`, PR mecánicos, notas sin `--hito-web`.
6. Lint: dos fallos → sin encargo `pedir_ok` y una línea en el parte sin el texto. Regresión «NY-BR-1 … 58 %» → `clinico`.
7. `proponer()` no fusiona aunque haya permiso abierto. Espera al CI. Con el CI en rojo no hay encargo.
8. Cronología: solo añade, `highlight: false`, tag existente.
9. Sombra: no escribe en `pedir_ok/` ni en el buzón; solo en `sombra.jsonl`.

## 10. UNKNOWNS (los 1-5 y el 7 los respondió {{TITULAR}} el 17-oct: ver §0; quedan abiertos el 3b y el 6)

3b. **No lo sé: de dónde saco los ciclos.** No encuentro una fuente fiable: 0 menciones en `calendar_sync.config.json`, 2 en `seguimiento.json`. Lo primero de la fase 1 es buscarla en el calendario, los correos del hospital y la bóveda. Si no aparece, el ciclo entra con una marca del Tablero.

(Lo que sigue es el texto original de las preguntas, para que quede el registro.)


1. **Cifras de dianas fuera de los carriles A y C**, y cierre del hueco del lint antes del modo activo. Recomendación: sí.
2. **El mapa lo siembro yo, ~12 filas, y te lo enseño en la sombra.** La lista de lo que queda siempre a mano (§7.7) ¿te vale así, o quieres sacar alguna página más?
3. **Ciclos de tratamiento:** no encuentro una fuente fiable [verificado: 0 en `calendar_sync.config.json`, 2 menciones en `seguimiento.json`]. Propuesta: solo inicio o fin de línea o ensayo, desde el hilo del Tablero.
4. **Inglés:** el cambio en inglés va en el mismo PR y el mismo Telegram, y un «sí» vale para los dos. Lo redacta `voz-titular`, no se traduce. ¿Vale, o prefieres verlo aparte?
5. **Modo activo:** 7 días en sombra y te enseño 3 propuestas reales antes de encenderlo.
6. **No lo sé:**
   - si un trabajo de `cola.py` puede leer el Llavero;
   - cómo se retira hoy un borrador de `salida` ya enviado;
   - si `/celula-a-celula` tiene una fuente interna única de la que sale `resultados.json` (no abrí el pipeline que la genera).

   Es lo primero que miro al implementar.
7. **Aprobación por cambio o por lote:** recomiendo un mensaje con hasta 3 cambios numerados. «sí» publica todo; «sin el N» quita ese cambio, rehace el PR y te reenvía lo que queda para un «sí» corto. La alternativa es un Telegram por cambio: más mensajes, pero con cada «sí» independiente.

## 11. Revisión de comités (17-oct)

| Quién | Veredicto | Qué entró en el plan |
|---|---|---|
| Vega (`asistente`), v1 y v2 | aprobado con matiz | tope general de 3/día, silencio 23-8, `origen` propio, cerrar el lint antes del modo activo, exigir evidencia además del Tablero, solo hilos no privados, dirección inversa, 2º recordatorio cuando la web dice algo falso |
| `consejero-arquitectura` (v1) | aprobado con matiz | redactor único diario, estado `encolado`, esperar al CI, cerrojo, exclusión por la rama `auto/`, retirar el borrador viejo |
| `diseno` (v2) | aprobado con matiz | plantillas aprobadas una vez en ES y EN, `implication` atada al estado, lista cerrada de lo que va a mano, sin edades, títulos de la cronología sin plantilla, Telegram escaneable |

**Discrepancia resuelta:** `diseno` pedía el Telegram en tabla; la regla de Vega prohíbe tablas en Telegram. Gana la regla, y el formato es una lista numerada con anclas de emoji.
**Discrepancia abierta (UNKNOWN 7):** `diseno` quiere un «sí/no» por cambio. La firma atada al contenido sella el PR entero. Mi propuesta es «sí» o «sin el N» y reenviar.
**Nota:** `diseno` leyó una rama local de la web sin campos `status:`. En `origin/main` sí están [verificado: `git show origin/main:content/es/science.yml`].
**No consultados:** `consejero-acceso` (el hook lo pedía; este plan no toca acceso a ensayos ni contactos) y `comite-medico` (entra en la fase 2, carril D).

## 12. Esfuerzo

- **Fase 1** (mapa + detector + redactor + carriles A, B, C + tests): unas 1,5-2 jornadas.
- **Fase 2** (carril D con `comite-medico` y `verificacion` delante): otra jornada, después de una semana de sombra de la fase 1.
