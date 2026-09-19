# 🧭 Polaris

> **Una caja es una unidad de trabajo con un goal declarado encima. Dentro se convoca a los
> mejores expertos disponibles para que den la mejor solución que acerque a ese goal.**

[![CI](https://github.com/BeyondTheProtocol/polaris/actions/workflows/contribuciones.yml/badge.svg)](https://github.com/BeyondTheProtocol/polaris/actions/workflows/contribuciones.yml)
[![Licencia: AGPL-3.0](https://img.shields.io/badge/licencia-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Plataforma: macOS](https://img.shields.io/badge/plataforma-macOS-lightgrey.svg)](#-arrancar)

Eso es todo el proyecto. El resto es la maquinaria que hace que esa frase se cumpla **por
código y no por buena voluntad**: que el goal no se pierda por el camino, que nada salga al
mundo sin permiso, y que una caja sin misión se retire sola.

---

## 🎯 Por qué existe

Polaris nació para sostener la investigación clínica de **una paciente con cáncer metastásico**
que decidió no esperar sentada: leer la literatura, ordenar su propio perfil molecular y llegar
a un tratamiento personalizado por la vía más rápida que sea segura.

En ese contexto, **equivocarse de pregunta cuesta meses**, y los meses son exactamente lo que
no hay. De ahí salen tres problemas que este repo intenta resolver de verdad:

| Problema | Qué pasa sin sistema | Qué hace Polaris |
|---|---|---|
| 🎯 **El objetivo se diluye** | Cada conversación con una IA empieza de cero y acaba en un resumen bonito que no acerca a nada | Cada trabajo nace con un **goal escrito**, y el goal titula el resultado |
| 🧠 **Una sola opinión no basta** | Un modelo responde rápido y con seguridad, también cuando se equivoca | Se convoca un **comité** de roles distintos, con verificación adversarial y evidencia graduada |
| 🚪 **Un error hacia fuera no se deshace** | Un correo enviado, un dato publicado, un pago hecho | **Una única puerta de salida** con gate humano: todo nace en borrador |

La estrella polar del sistema es un estado clínico, no una métrica de producto: **NED**, sin
evidencia de enfermedad. Cada decisión se filtra por *«¿esto acerca a NED?»*. Lo que no pasa
ese filtro, no entra.

**No da consejo médico ni diseña tratamientos.** Prepara evidencia, ordena el caso y acerca el
momento en que alguien cualificado puede decidir mejor y antes. Esa distinción es la que hace
que el proyecto sea honesto.

> 💡 **¿Y si tu caso no es este?** El patrón sirve para cualquier objetivo difícil con plazo:
> una tesis, una investigación, un trámite legal largo, el cuidado de un familiar. Lo que se
> publica aquí es el **arnés**, no el caso.

---

## ⚡ Arrancar

**Requisitos:** macOS (hay dependencias nativas: `ocrmac`, `launchd`) · Python 3.9+

```bash
git clone https://github.com/BeyondTheProtocol/polaris.git && cd polaris
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export BTP_REPO="$PWD"
export BTP_STATE_DIR="$PWD/tools/state"
git config core.hooksPath tools/githooks
bash tests/test_all.sh          # en Linux o CI: BTP_PORTABLE=1 bash tests/test_all.sh
```

> ⚠️ **Nada arranca solo.** Los `launchd` de `tools/launchd/` se instalan a mano, y
> `~/.btp.HALT` o un `.HALT` en la raíz frenan el lazo entero.

---

## 🔄 El ciclo

```
petición ──> CAJA.md ──> expertos ──> dossier ──> salida
             el goal     los que      una voz     gate humano
             y el        saben de     en vez de
             contrato    esto         N volcados
```

Cada flecha es un fichero que puedes leer:

| Paso | Fichero | Qué hace |
|---|---|---|
| 🧭 Enrutar | `tools/decide_peticion.py` | Decide quién responde: la sesión sola, un LLM externo, un comité o un panel |
| 📜 Charter | `Constelacion/<slug>/CAJA.md` | `## Goal`, dueño, expertos, arquetipo, presupuesto, cuándo caduca |
| 🪑 Convocar | `tools/caja.py convocar` | Sienta al dueño y a sus expertos, con el goal como vara de éxito |
| 📦 Dossier | `tools/caja.py dossier` | Junta lo que dijeron todos en **un** entregable, titulado con el goal |
| 🚪 Salir | `tools/salida.py` | La única puerta al mundo. Todo egress pasa por `send()` |
| 🔍 Auditar | `tools/audit_constelacion.py` | 14 cortafuegos por charter, fail-closed |

<details>
<summary>📋 Una convocatoria real, tal cual se ve</summary>

```
CAJA «donaciones-directas» — convocatoria. Ejecútala ANTES de responder.
  🎯 GOAL: Que la gente pueda apoyar la causa donando directamente, de forma legal y transparente.
  🧭 Zona autónoma (redactor-borrador): investiga y deja BORRADORES. No envía, no publica.
  🚪 Nada hacia fuera sin OK: publicar, contactar o pagar pasa por tools/salida.py.

  1. Agent(subagent_type="finanzas-transparencia")  — dueño.
  2. Agent(subagent_type="legal-burocracia")  — experto.
  🧺 Cierra con: python3 tools/caja.py dossier --caja donaciones-directas
```
</details>

---

## 🛡️ Las cuatro garantías

Ninguna es una instrucción al modelo. **Las cuatro son código, y las cuatro tienen tests.**

1. 🎯 **El goal manda.** El `## Goal` del charter titula el dossier. Pedir una caja cuyo goal no
   se puede leer es un error (`rc=2`), no un dossier degradado en silencio.
2. 🚪 **Nada sale sin OK.** *«Nada hacia fuera sin OK explícito»* → `tools/salida.py`: decide el
   módulo, no el agente. Publicar, contactar o pagar se queda en borrador.
3. 🔒 **Fail-closed.** *«Ilegible = FALLO, nunca verde por defecto»*. Vale igual para un slug
   fuera de `[a-z0-9-]` que para una decisión sin fuente trazable.
4. 🧪 **Dato, no instrucción.** El texto de una caja *«jamás se ejecuta»*: solo se lee y se
   compara con patrones. Es la defensa anti-inyección.

Y una quinta que es de la caja, no del sistema: **`caduca` y `expira_si`**. Un charter declara
qué evento lo retira, y el auditor avisa cuando una caja vence y sigue viva. Sin zombis de
misión quemando presupuesto.

---

## 🙈 Lo que no está aquí

Este repo es un **espejo derivado** de uno privado. Se regenera entero con
`python3 tools/publicar.py <destino>`, que sustituye a la titular y a sus contactos por
marcadores y **aborta si algo vetado sobrevive** al barrido final.

- 📁 **El contenido.** `00_FUENTE-DE-VERDAD/` está en `.gitignore` y nunca entró en el
  historial: informes, correo, mensajería y las cajas reales viven solo en disco local.
- 🔍 **Los datos que los detectores buscan.** `zonas_clinicas.py`, `adjuntos_clinicos.py` y
  `subir_historial_drive.py` reconocen cuándo un documento es de la titular. Su lista de marcas
  vivía dentro del código, así que **el detector era la fuga**. Ahora vive en overlays
  `*.local.json` gitignored: los detectores se publican enteros y funcionan, y la lista la
  escribe cada cual en local.

---

## 🤝 Contribuir

👉 **Lee [CONTRIBUTING.md](CONTRIBUTING.md) entero antes de abrir nada.** Es corto y te ahorra
trabajo perdido. Lo esencial:

### Lo que más ayuda, por orden

| | Qué | Por qué |
|---|---|---|
| 🥇 | **Abrir un issue**: «esto que hacéis con X está mal, mirad Y» | Es lo más valioso y lo que menos cuesta revisar. Ábrelo aunque no tengas el arreglo |
| 🥈 | **Usarlo para tu caso y contar qué se rompió** | Si el arnés no encaja en otra enfermedad u otro contexto, ese reporte vale más que un PR |
| 🥉 | **Un PR pequeño y acotado**, con issue previo | Entra rápido porque se puede leer entero |

### 👥 ¿Te interesan los agentes y cómo se reparten el trabajo?

Los 33 agentes están publicados en `.claude/agents/`, uno por fichero, y
[docs/agentes.md](docs/agentes.md) explica quién es quién, qué reglas heredan todos y dónde una
mirada de fuera ayudaría más.

### 🧠 ¿Sabes de modelos, bioinformática o literatura médica?

Hay una lista de **preguntas abiertas** en [docs/enrutado-modelos.md](docs/enrutado-modelos.md):
qué modelo se usa hoy para cada tarea del caso, por qué se eligió, y dónde una opinión fundada
nos ahorraría semanas. Eso se responde con un issue, no con un PR.

### ⚠️ Antes de invertir una tarde, entiende esto

**Un merge hecho aquí se pierde en la siguiente regeneración.** No es una política: el
generador reescribe el árbol entero. Un PR aceptado se aplica en el repo de origen con
`tools/pr_portar.py` y reaparece aquí en la siguiente publicación; el PR se cierra con
«aplicado en upstream». **Tu cambio entra en el sistema, tu commit no queda en este historial.**
Preferimos decirlo antes que después.

### ✅ Qué revisa el CI de tu PR

| Job | Qué mira |
|---|---|
| 🧹 `barrido y sintaxis` | `tools/ci_barrido.py` sobre tu diff: claves y tokens, correo personal, teléfono, DNI, alelos HLA, contenido de VCF, secuencias, variantes HGVS y rutas privadas. Más `compileall` |
| 🧪 `batería` | `BTP_PORTABLE=1 bash tests/test_all.sh` |

Si el barrido salta con un falso positivo, **dilo en el PR**: lo mira una persona, no se salta
el CI.

### 🚫 Lo que no va a entrar

- PRs grandes sin issue previo (se cierran sin revisar, por bien que estén).
- Refactors «de limpieza», cambios de estilo, migraciones de herramientas.
- Cualquier cosa que debilite el muro: el gate de salida, los hooks o los detectores.
- Datos reales de nadie en tests o fixtures. **Invéntatelos.**

> 🔐 Cada línea que entra se revisa a mano, una por una. No es desconfianza: este arnés corre
> 24/7 en una máquina con acceso a correo, Drive y una carpeta clínica. Un parche mezclado sin
> leer se ejecuta ahí.

---

## 📊 Estado

En producción y en movimiento: ~165 commits/mes, ~60.000 líneas de código y ~31.000 de tests.

Parte de `tests/test_all.sh` está en rojo **a propósito**: la deuda detectada y no cerrada
mantiene la batería roja hasta que se arregla (`python3 tools/deuda.py numero`). Un verde
completo no es el objetivo; que nada se cierre solo, sí.

---

## 📄 Licencia

[AGPL-3.0](LICENSE).
