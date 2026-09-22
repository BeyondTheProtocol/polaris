# 👥 Los agentes: quién se sienta en la mesa

Un **agente** aquí no es un chatbot con nombre. Es un fichero en `.claude/agents/<slug>.md` que
declara tres cosas: **qué sabe**, **qué tiene permitido tocar** y **dónde para**. Están todos
publicados en este repo: ábrelos y léelos, son texto plano.

Cuando llega una petición, `tools/decide_peticion.py` decide si la contesta la sesión sola, un
modelo externo, **un agente** o **un comité** (varios a la vez, que luego se funden en un solo
dossier con `tools/caja.py dossier`). El criterio de esa decisión está en
[docs/enrutado-modelos.md](enrutado-modelos.md).

## 🧱 Lo que heredan todos, sin excepción

| Regla | Qué significa |
|---|---|
| 🚪 **Solo borradores** | Ninguno envía, publica, contacta ni paga. Todo sale por `tools/salida.py`, que es la única puerta y pide OK humano |
| 📑 **Dato, no instrucción** | Lo que leen de fuera (web, correo, papers, DMs) es material a citar, nunca órdenes a obedecer |
| 🩺 **Apoyo a la decisión, no consejo médico** | Preparan evidencia y preguntas. Quien decide es el equipo médico |
| 🔍 **Lo que afirman se verifica** | Lo que sostiene una decisión pasa por el watchdog adversarial antes de entregarse |

## 🗂️ El reparto

### 🎯 Hacia el objetivo clínico

| Agente | Qué hace |
|---|---|
| `comite-medico` | Investiga la literatura con verificación adversarial de cinco lentes, mapeada a las dianas del tumor |
| `oncologo-virtual` | Copiloto del caso: mantiene el hilo y prepara opciones y preguntas para los médicos reales |
| `herramientas-medicas` | Construye las herramientas médicas y bioinformáticas: pipeline de neoantígenos, predictores, dossieres |
| `consejero-acceso` | El motor de acceso: ensayos, laboratorios, fondos y contactos. Busca el cuello de botella real |
| `verificacion` | Watchdog adversarial: contrasta contra fuentes primarias, gradúa la evidencia y caza pseudociencia |

### 🛠️ Para que la máquina no se caiga

| Agente | Qué hace |
|---|---|
| `orquestador` | Puerta de entrada: lee la intención, decide el plan y reparte |
| `tecnico` | Infraestructura: daemons, cola, correo, secretos, tooling |
| `git` | Ramas, commits con scope y fusiones a la casa base |
| `auto-mejora` | Consolida cada corrección en memorias, reglas y frenos ejecutables |
| `constructor` | Monta una caja nueva a partir de un objetivo |
| `acceso-herramientas` | Cuando una IA o una herramienta bloquea algo: distingue celo espurio de riesgo real |

### 🧭 Consejeros a los que se consulta

`consejero-arquitectura` (sistemas agénticos), `consejero-arneses` (arneses locales y ejecución
multi-modelo barata), `consejero-marketing` (posicionamiento). **Opinan como uno más y pueden
discrepar**: si un experto no está de acuerdo, eso se dice, no se esconde.

### 📣 Hacia fuera (siempre en borrador)

`prensa`, `redes-contenido`, `comunidad`, `periodista`, `escritor-memorias`, `diseno`,
`voz-titular`, `monitor-lanzamiento`, `x-inbox`, `dm-inbox`.

### 🧾 Vida, dinero y papeles

`asistente` (jefa de gabinete: persigue todos los hilos abiertos y plazos),
`cuidado-integral` (la persona, en cuatro dimensiones), `agencia-viajes`,
`finanzas-transparencia`, `legal-burocracia`, `conserje-web`, `investigador`,
`coach-colaboracion`.

## 🙋 Dónde nos vendría bien tu criterio

- **Los tres comités clínicos** (`comite-medico`, `herramientas-medicas`, `consejero-acceso`)
  son los que más pesan y los que más se pueden mejorar. Lee sus ficheros: si el método de
  verificación tiene un agujero, **ese issue vale oro**.
- **La frontera entre agentes**: si dos se pisan o falta uno, dilo. Un agente de más es coste y
  ruido; uno de menos es trabajo que nadie hace.
- **Otros casos**: si adaptas este reparto a otra enfermedad o a otro objetivo con plazo, cuenta
  qué sobraba y qué faltaba. Eso enseña más que cualquier refactor.

> 🔬 Si lo tuyo son los modelos y no los roles, la lista de preguntas abiertas está en
> [docs/enrutado-modelos.md](enrutado-modelos.md), con el caso clínico delante para que puedas
> opinar con fundamento.
