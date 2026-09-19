# Polaris

**Una caja es una unidad de trabajo con un goal declarado encima. Dentro se convoca a los
mejores expertos disponibles para que den la mejor solución que acerque a ese goal.**

Eso es todo el proyecto. El resto es la maquinaria que hace que esa frase se cumpla por
código y no por buena voluntad: que el goal no se pierda por el camino, que nada salga al
mundo sin permiso, y que una caja sin misión se retire sola.

Nació para sostener la investigación clínica de una persona con una enfermedad rara, donde
equivocarse de pregunta cuesta meses. Sirve para cualquier objetivo difícil con plazo.

## El ciclo

```
petición ──> CAJA.md ──> expertos ──> dossier ──> salida
             el goal     los que      una voz     gate humano
             y el        saben de     en vez de
             contrato    esto         N volcados
```

Cada flecha es un fichero que puedes leer:

| Paso | Qué hace |
|---|---|
| `tools/decide_peticion.py` | Decide quién responde: la sesión sola, un LLM externo, un comité o un panel |
| `Constelacion/<slug>/CAJA.md` | El charter: `## Goal`, dueño, expertos, arquetipo, presupuesto, cuándo caduca |
| `tools/caja.py convocar` | Lee el charter y **sienta** a su dueño y a sus expertos, con el goal como vara de éxito |
| `tools/caja.py dossier` | Recoge lo que dijeron todos y lo entrega como **un** dossier, titulado con el goal |
| `tools/salida.py` | La única puerta al mundo. Todo egress pasa por `send()` |
| `tools/audit_constelacion.py` | Audita cada charter contra 14 cortafuegos, fail-closed |

Una convocatoria real se ve así:

```
CAJA «donaciones-directas» — convocatoria. Ejecútala ANTES de responder.
  🎯 GOAL: Que la gente pueda apoyar la causa donando directamente, de forma legal y transparente.
  🧭 Zona autónoma (redactor-borrador): investiga y deja BORRADORES. No envía, no publica.
  🚪 Nada hacia fuera sin OK: publicar, contactar o pagar pasa por tools/salida.py.

  1. Agent(subagent_type="finanzas-transparencia")  — dueño.
  2. Agent(subagent_type="legal-burocracia")  — experto.
  🧺 Cierra con: python3 tools/caja.py dossier --caja donaciones-directas
```

## Las cuatro garantías

Ninguna es una instrucción al modelo. Las cuatro son código, y las cuatro tienen tests.

1. **El goal manda.** El `## Goal` del charter titula el dossier. Pedir una caja cuyo goal no
   se puede leer es un error (rc=2), no un dossier degradado en silencio.
2. **Nada sale sin OK.** *«Nada hacia fuera sin OK explícito»* [fuente: `tools/salida.py`]:
   el módulo decide, no el agente. Publicar, contactar o pagar se queda en borrador.
3. **Fail-closed**: *«ilegible = FALLO, nunca verde por defecto»* [fuente: auditor]. Vale
   igual para un slug fuera de `[a-z0-9-]` o una decisión sin fuente trazable.
4. **Dato, no instrucción**: el texto de una caja *«jamás se ejecuta»* [fuente: auditor];
   solo se lee y se compara con patrones. Es la defensa anti-inyección.

Y una quinta que no es del sistema sino de la caja: **`caduca` y `expira_si`**. Un charter
declara qué evento lo retira. El auditor avisa cuando una caja vence y sigue activa, para que
no queden zombis de misión consumiendo presupuesto.

## Lo que no está aquí

El contenido. `00_FUENTE-DE-VERDAD/` está en `.gitignore` y nunca entró en el historial:
informes, correo, mensajería y las cajas reales viven solo en disco local.

Y los **datos personales que los detectores buscan**. `zonas_clinicas.py`,
`adjuntos_clinicos.py` y `subir_historial_drive.py` reconocen cuándo un documento es de la
titular; su lista de marcas (fecha de nacimiento, identificadores, nombres de terceros) vivía
dentro del código, así que el detector era la fuga. Ahora vive en overlays `*.local.json`
gitignored. Los detectores se publican enteros y funcionan: lo que falta es la lista, que cada
cual escribe en local.

Este repo se **deriva** del privado con `python3 tools/publicar.py <destino>`, que sustituye
al titular y a los contactos por marcadores y **aborta si algo vetado sobrevive** al barrido.

## Arrancar

Requisitos: **macOS** (hay dependencias nativas: `ocrmac`, launchd), Python 3.9+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export BTP_REPO="$PWD"
export BTP_STATE_DIR="$PWD/tools/state"
git config core.hooksPath tools/githooks
bash tests/test_all.sh
```

Nada arranca solo: los `launchd` de `tools/launchd/` se instalan a mano, y `~/.btp.HALT` o
`.HALT` en la raíz frenan todo el lazo.

## Estado

En producción y en movimiento (~165 commits/mes, ~60.000 líneas de código y ~31.000 de
tests). Parte de `tests/test_all.sh` está en rojo **a propósito**: la deuda detectada y no
cerrada mantiene la batería roja hasta que se arregla (`python3 tools/deuda.py numero`). Un
verde completo no es el objetivo; que nada se cierre solo, sí.

## Contribuir

Lee [CONTRIBUTING.md](CONTRIBUTING.md). Resumen: los issues son bienvenidos, los PRs se
revisan línea a línea, y lo más valioso que puedes hacer es montar una caja para tu caso y
contar qué se rompió.

## Licencia

AGPL-3.0. Ver [LICENSE](LICENSE).
