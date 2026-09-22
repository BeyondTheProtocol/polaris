# 🧱 Lo que falta

Los cinco problemas abiertos de Polaris, dichos por su dueña. No es una lista de deseos: es
donde el sistema **duele hoy**. Si alguno es tu terreno, un issue argumentado vale más que un PR.

Las cifras salen del propio repo y del registro del lazo el 19-sep-2026. Lo tachado ya está
hecho; lo demás sigue abierto.

---

## 1. 🏠 El carril local está parado

- [x] Decir en la documentación que el carril local está **disponible pero sin uso real**
- [ ] Averiguar por qué no se le llama: ¿no hay tareas de ese tipo, o algo dejó de invocarlo?
- [x] Retirar el modelo local descargado que nadie cableaba (5 GB): se probó contra el que ya
      estaba y daba **la misma salida**, así que no aportaba nada

**Estado:** `ollama` corre con `qwen3:8b`, y el registro del muro tiene **122 llamadas**, la
última el **14-jul-2026**. Para comparar: `nvidia` 10.065, `claude` 8.720.

**Lo que sí se movió el 19-sep:** el carril de Perplexity pasó a servir **46 modelos de varias
casas con una sola clave**, así que un proveedor sin saldo ya tiene suplente sin abrir cuenta
nueva. No resuelve el punto 1 —el local sigue sin usarse—, pero quita la excusa de «no hay
alternativa».

**Por qué duele:** el carril local es el **único destino permitido para el dato crudo**. Si
está parado, o no se está de-identificando nada, o se está haciendo en otro sitio.

**Y hay un segundo síntoma del mismo mal:** hace una semana se descargó otro modelo local
(`qwen3-abliterated:8b`, 5 GB) y **ningún código sabe que existe** — no aparece en el enrutador
ni en ninguna herramienta. Instalar sin cablear deja piezas muertas que nadie audita.

---

## 2. 🧰 Demasiadas herramientas y agentes, y ninguna gestión de su ciclo de vida

- [x] Marcar lo muerto: `tools/inventario.py --huerfanas` cruza quién nombra a quién, los
      daemons y el último commit. **De 5 huérfanas a 0**: una estaba desenchufada (se enchufó)
      y cuatro eran CLIs de mano sin documentar (se documentaron)
- [x] Que cada agente declare **cada cuánto** se espera que trabaje (`ritmo:` en la ficha:
      permanente / a-demanda / estacional / dormido), con test que lo exige
- [ ] Detectar solapes automáticamente: dos piezas que hacen lo mismo con nombres distintos
- [ ] Un criterio de retirada, no solo de creación
- [ ] Que crear una herramienta nueva obligue a declarar qué reemplaza

**Estado:** **189 herramientas** en `tools/`, tres modelos descargados en local de los que
**solo uno está cableado**, **33 agentes** en `.claude/agents/` y **68
daemons** declarados. Cada problema nuevo tiende a crear una pieza nueva.

**Por qué duele:** el catálogo crece más rápido que la capacidad de recordarlo. Una herramienta
que nadie encuentra se reescribe, y entonces hay dos. El auditor vigila los charters de las
cajas, pero **nadie vigila el inventario**.

**Qué ayuda buscamos:** patrones de *tool discovery* y de retirada en sistemas agénticos con
cientos de piezas. ¿Registro con telemetría de uso? ¿Presupuesto de piezas por dominio?

---

## 3. 🔌 Todo está construido sobre Claude Code

- [ ] Que el lazo corra sin el runtime de Claude Code
- [ ] Un carril de ejecución alternativo probado de verdad, no solo escrito
- [ ] Separar lo que es **lógica del sistema** de lo que es **la herramienta que lo ejecuta**

**Estado:** existe `tools/borde_gateway.py`, una pasarela compatible con OpenAI que enruta por
el muro, y las herramientas son **stdlib pura a propósito** (`enruta.py` decide sin depender del
runtime). Pero el lazo, los agentes y los hooks son de Claude Code.

**Por qué duele:** un límite de cuota o un cambio de producto dejaría el sistema sin motor. Ya
pasó una vez: un tope semanal dejó un comité a medias.

**Qué ayuda buscamos:** experiencia real portando un arnés agéntico a otro runtime, sin
reescribirlo entero ni acabar con dos copias divergentes.

---

## 4. 🧠 Pierde contexto global entre sesiones

- [ ] Dejar de repetir a mano lo que el sistema ya sabe
- [ ] Que lo aprendido en una sesión esté disponible en la siguiente **sin recall aleatorio**
- [ ] Distinguir lo que debe cargarse siempre de lo que debe recuperarse solo cuando toca

**Estado:** hay memorias en fichero con índice, reglas cargadas siempre, un RAG local y un
registro de continuidad entre sesiones. Aun así, hay cosas que hay que volver a explicar.

**Por qué duele:** cada repetición es tiempo de una persona enferma, y algunas correcciones se
pierden justo cuando más falta hacen.

**Qué ayuda buscamos:** cómo decidir **qué entra en el contexto fijo** y qué se recupera bajo
demanda, sin inflar el prompt ni fiarlo todo a la suerte del recuperador.

---

## 5. ⏰ Las rutinas automáticas se caen y nadie se entera a tiempo

- [x] Que un daemon **deshabilitado** se detecte y se levante, en vez de reintentar a ciegas
- [x] Que una alerta no se multiplique sola: claves sin contadores, y una cadena no se itera
      letra a letra
- [x] Que un agente lanzado por launchd **no aparezca como «sin usar»**
- [x] Que **un LLM sin saldo** avise: el GET de la sonda vieja devolvía 200 con el saldo a
      cero, así que nadie se enteraba. Ahora hay llamada real cada 6 h
- [ ] Que una rutina que deja de correr **avise sola**, no se descubra semanas después
- [ ] Distinguir «no hay nada que hacer» de «esto lleva roto un mes»
- [ ] Reintento y recuperación, no solo detección

**Estado:** el libro de deuda tiene ahora mismo **60 hallazgos** de frescura o daemons, y los
contadores hablan solos: un agente de triaje de correo detectado como parado **679 veces**, un
reindexado que falla de forma intermitente **7 veces en 55 días**.

**Por qué duele:** las dos rutinas que más importan son leer el correo cada día y mejorarse una
vez por semana. Si se caen en silencio, el sistema parece vivo y no lo está.

**Qué ayuda buscamos:** patrones de *supervisión* para trabajos periódicos en una sola máquina:
heartbeat con umbral, reintento con marcha atrás, y una alerta que no se vuelva ruido.

> ✅ **19-sep-2026, arreglado parte del 5:** dos daemons (el que ejecuta la cola y el bot)
> llevaban días caídos por una marca `disabled` de launchd —invisible en `launchctl list` y con
> un error que solo decía «Input/output error»—. Sin ellos, los encargos que debían cerrar cada
> alerta nunca corrían: la alerta volvía a saltar y se encolaba otro encargo, en bucle. Además
> se arreglaron dos fallos que lo alimentaban: una cadena suelta que se iteraba **letra a letra**
> creando una alerta por carácter, y claves de alerta que **llevaban los contadores dentro**, así
> que cada número nuevo nacía como una alerta nueva. Los tres con test.

---

> 🙋 **Cómo ayudar:** abre un issue diciendo qué número atacas y con qué evidencia. Si tienes el
> arreglo, mejor; pero el diagnóstico correcto ya es media solución. Lee antes
> [CONTRIBUTING.md](../CONTRIBUTING.md): este repo es un espejo y un merge aquí se pierde en la
> siguiente regeneración.
