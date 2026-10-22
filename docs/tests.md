# Ejecutar los tests desde un fork

La entrada es [`tests/test_all.sh`](../tests/test_all.sh). Ejecuta las baterías Python y
Shell registradas, campañas de mutantes y un control de tests sin registrar. Un clon público
no incluye el contenido privado, los overlays personales ni el estado vivo: **un SKIP no es
una prueba superada**, y un resultado portátil no valida la instalación de producción.

## Preparar y ejecutar

Desde la raíz de tu clon público, con Git, Bash y Python 3.9+ disponibles:

```bash
export BTP_REPO="$PWD"
export BTP_STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/polaris-tests.XXXXXX")"
export BTP_ROJO_DIR="$BTP_STATE_DIR/rojos"
export BTP_TEST_BATTERY=1
BTP_PORTABLE=1 bash tests/test_all.sh
rc=$?
printf 'Código de salida: %s\nLogs: %s\n' "$rc" "$BTP_ROJO_DIR"
```

- `BTP_REPO`: raíz de este clon; fíjala antes de importar herramientas para evitar los valores
  por defecto que apuntan a la casa base.
- `BTP_STATE_DIR`: estado temporal de esta ejecución, separado de cualquier instalación viva.
- `BTP_ROJO_DIR`: directorio donde conservar los logs de las baterías fallidas.
- `BTP_TEST_BATTERY=1`: activa el modo de pruebas en los componentes que lo consultan.
- `BTP_PORTABLE=1`: omite las baterías de `SOLO_CASA_BASE` del runner. Úsalo también en un Mac
  ajeno a la instalación original. No instala dependencias ni convierte todos los fallos en SKIP.

Estas variables **no constituyen una sandbox ni garantizan cero red**. Usa un clon de pruebas,
sin datos ni credenciales reales. No cambies `HOME`, no copies contenido privado ni desactives
un HALT para conseguir que una prueba deje de saltar.

### Intérprete y dependencias

El runner prefiere **`/usr/bin/python3`** si existe y es ejecutable; solo en caso contrario usa
`python3` del `PATH`. Activar una venv **no cambia esa selección**. Algunos tests se relanzan
por su cuenta con un intérprete especializado.

El [workflow público](../.github/workflows/contribuciones.yml) prepara Python 3.12 en Ubuntu,
instala `poppler-utils` y `tesseract-ocr`, y ejecuta el modo portátil. En Debian/Ubuntu, esos
binarios se preparan con:

```bash
sudo apt-get update
sudo apt-get install poppler-utils tesseract-ocr
```

El [`requirements.txt`](../requirements.txt) incluye dependencias de la instalación macOS
(`ocrmac`, `pyobjc-*`): no es una receta universal para Linux. Tampoco lo instala el job de
la batería pública. Si un test necesita un paquete, comprueba **qué intérprete lo ejecuta**
y su guarda de dependencias: por ejemplo, instalar `pypdf` en una venv no lo hace visible al
Python del runner. Para comprobar un test concreto con tu venv, invócala explícitamente:

```bash
.venv/bin/python tests/test_kb_pdf_avisos.py
```

Este comando presupone una `.venv` ya preparada. Su resultado corresponde a ese test y a ese
intérprete, no a toda la batería.

## Qué se salta y por qué

La lista exacta vive en el runner, en [`tests/_entorno.py`](../tests/_entorno.py) y en las
guardas de cada test; cambia con el código. Estos son los mecanismos y ejemplos reales:

| Motivo | Tests / lugar | Cómo se reconoce |
|---|---|---|
| Solo instalación original | `test_fuga.sh`, `test_halt.sh` | Salen con 77 y «SKIP: necesita estar en ~/claudecode: el muro mira esa ruta, no un repo cualquiera». Comprueban la ubicación del código, no basta con cambiar `BTP_REPO`. |
| Lista portátil del runner | `test_xurl.py`, `test_x_guardados_enriquecido.py`, `test_llavero_mudo.py`, `test_bucles_colgados.py`, `test_plists_home.py`, `test_anatomia_tecnica.py`, `test_auto_mejora_turnos.py`, `test_digest.sh`, `test_muro_costura_rm.py`, `test_coste_repo.py`, `test_healthcheck_halt_inactividad.py` | Con `BTP_PORTABLE` definido y no vacío, el runner no los ejecuta y muestra «(solo casa base)». |
| Estado vivo ausente | `test_healthcheck.py` exige `estado` | «SKIP: necesita el estado vivo del lazo…». Crear una carpeta `tools/state` vacía no satisface esa condición. |
| Datos u overlays privados ausentes | Requisitos `contenido`, `identidad`, `nombres`, `perfil`, `zonas` en `_entorno.py`; por ejemplo, `test_investigacion_fuga.py` exige nombres y perfil | «SKIP: necesita …», con una línea por requisito que falta. No se distribuyen en el espejo. |
| HALT activo | Tests que llaman a `exige("sin-halt")` | «hay un HALT activo y el sistema está en pausa total». El bloqueo se respeta. |
| Entorno o fixtures del pipeline ausentes | `test_pipeline_alelos_muestra.py`, `test_pipeline_datos_reales.py` | «SALTADO: falta .venv-pipeline…», rc=77; el segundo también necesita los datos públicos indicados en su mensaje. |
| Dependencias de imagen ausentes | `test_visor3d.py` | Intenta `.venv-imagen`; sin ella, en modo portátil: «SKIP: sin .venv-imagen (modo portátil)», rc=77. Fuera del modo portátil es un fallo. |

También hay comprobaciones individuales que se omiten dentro de una batería, por ejemplo
por la marca `.espejo-publico` en `test_caso_publico.py`. Y `test_kb_hibrido.py` puede pasar
**solo el fallback FTS5** si falta el modelo: lee su resumen antes de afirmar que se probó
la recuperación vectorial. El contador final de SKIP no enumera toda esa cobertura parcial.

## Interpretar el resultado

- **0:** esa batería terminó sin fallos reportados. Revisa también los saltos internos.
- **77:** el runner cuenta esa batería como saltada y la muestra aparte.
- **Otro código:** fallo; el runner muestra «ROJO», el nombre, el código y la ruta del log.

El resumen final separa baterías saltadas y fallidas. Puede mostrar «TODO EN VERDE» junto a
un número de saltos: significa que no falló lo ejecutado, no que se comprobó todo.
Sin `BTP_ROJO_DIR`, los logs van a una carpeta temporal propia `rojo.XXXXXX`; con `CI`
definido y sin directorio explícito van a `/tmp`. El mensaje de cada fallo da la ruta exacta.

Ante un rojo, abre ese log y repite **solo la batería afectada** con el mismo entorno e
intérprete, por ejemplo `/usr/bin/python3 tests/test_kb_fts5.py` o `bash tests/test_halt.sh`.
Distingue dependencia ausente, condición local y regresión del cambio; si hace falta, contrasta
con la base sin tu parche. Documenta el resultado: no atribuyas todos los rojos al entorno.

No sustituyas el runner por un bucle sobre todos los `test_*`: al final de `test_all.sh` hay
exclusiones deliberadas de tests que pueden alcanzar envíos reales. Para añadir una prueba,
consulta [CONTRIBUTING.md](../CONTRIBUTING.md): fixtures sintéticos, registro en el runner y
comprobación de que la prueba falla al romper el comportamiento que protege.
