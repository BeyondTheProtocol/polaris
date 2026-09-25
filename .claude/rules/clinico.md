---
paths:
  - "00_FUENTE-DE-VERDAD/_PRIVADO_*/**"
  - "tools/lector_clinico.py"
  - "tools/deid.py"
  - "tools/cascada_clinica.py"
  - "tools/correo_imap.py"
  - "tools/email_archive.py"
---

# Dato clínico y privado: cómo se toca

## La ventanilla auditada
La lectura de rutas clínicas pasa por `tools/lector_clinico.py`, vigilada por el hook `.claude/hooks/clinico_guard.py` (PreToolUse). No la rodees: si un acceso no cabe por ahí, es que hay que revisar el diseño, no saltarse la ventanilla.

**Originales binarios (PDF escaneado, DICOM, imagen).** Hasta el 19-sep-26 la ventanilla los servía decodificados como UTF-8 con `errors="ignore"`, así que salían destrozados y en silencio: pdftoppm renderizaba páginas en blanco y tesseract devolvía 0 bytes, y ningún original de imagen se podía cotejar de verdad. Ya no. Los modos:

| Qué quieres | Comando | Qué pasa |
|---|---|---|
| Leer un informe de texto | `python3 tools/lector_clinico.py <ruta>` | sale entero, byte a byte |
| **Leer un PDF** (el caso normal) | `python3 tools/lector_clinico.py --texto <ruta.pdf>` | su capa de texto, **marcada como derivación** |
| Tubear el original a un programa | `python3 tools/lector_clinico.py --binario <ruta> \| pdftoppm …` | bytes crudos por stdout |
| Trabajar con el fichero en disco | `python3 tools/lector_clinico.py --a <destino> <ruta>` | copia idéntica (mismo sha256) |
| OCRear un escaneado | `python3 tools/lector_clinico.py procesa ocr_informes -- --apply --dir "<carpeta>"` | sidecars `.ocr.txt`, 100% local |
| **Saber qué hay o dónde está algo** | `python3 tools/lector_clinico.py listar "anatomia patologica"` | **rutas, nunca contenido** |
| Sacar basura del archivo | `python3 tools/lector_clinico.py retirar <ruta> --motivo "…"` | **mueve a `_RETIRADOS/`, no borra** |

El destino de `--a` tiene que ser **zona clínica** por el mismo predicado con el que se decide si se puede leer el origen (`zonas_clinicas.es_ruta_clinica`, el del guard): fail-closed, si no lo es no se escribe nada. Y **no sobrescribe**: un destino que ya existe —o que es un hardlink del original, o un symlink que sale de la zona, o un directorio— se rechaza. La copia nace en 0600.

**`--texto` NO es el original, y el sistema lo sabe.** Sirve la capa de texto del PDF (o el sidecar `.ocr.txt` si el PDF está escaneado y ya se OCReó), pero avisa en stderr y lo registra en el log con su propio resultado —`LEIDO-texto-pdftotext` / `LEIDO-texto-sidecar-ocr`, nunca `LEIDO`—. Esa distinción es la que faltaba cuando unas notas decían «verificado contra el original» apoyándose en transcripciones: **si vas a negar o afirmar algo clínico sobre el original, cotéjalo con `--a`, no con `--texto`.** Si el PDF está escaneado y no hay sidecar, la ventanilla lo dice y manda a OCRearlo; nunca devuelve vacío como si fuera el informe. El sidecar pasa por la misma puerta que el original (un enlace que salga de la zona se rechaza).

**`listar` existe porque el muro dejaba el sistema manco.** La ventanilla sabía servir un fichero si ya tenías su ruta, pero no había forma sancionada de preguntar «¿qué hay?» o «¿dónde está esto?», y el guard bloquea `ls`/`find`/`glob` sobre zona clínica. Cualquier hallazgo que nombrara un fichero moría ahí, y la salida fácil era rodear el guard, que es peor. Devuelve **rutas, nunca contenido**: busca sin tildes ni mayúsculas (`anatomia` encuentra «Anatomía»), no sigue ni nombra enlaces que salgan de la zona, corta en 200 resultados (`--max`) y deja su línea en el log. Sin `--dir` busca en las raíces conocidas; una carpeta clínica suelta fuera de ellas necesita `--dir`.

**`retirar` no borra, aparta.** Un fichero clínico borrado no vuelve (esta zona está gitignorada a propósito), y casi siempre lo que parece basura es un archivado torpe. Mueve a `_RETIRADOS/` junto a donde estaba — sigue siendo zona clínica, sigue protegido, y se deshace arrastrándolo de vuelta. Exige `--motivo`, que queda escrito en un `.retirada.txt` al lado con la fecha y quién fue: una retirada sin razón no se puede revisar dentro de seis meses. No sobrescribe una retirada anterior del mismo nombre. **Destruir de verdad sigue sin tener puerta aquí, y es deliberado**: eso lo hace una persona, a mano y sabiendo lo que hace.

Un binario sin `--binario` ni `--a` se **rechaza**: volcar megas de PDF al contexto de un agente lo envenena y cuesta dinero. Freno: `tests/test_lector_clinico_binario.py` (43 casos, campaña de mutantes contra cada defensa).

## 🔐 Niveles de sensibilidad (regla de {{TITULAR}}, 5/7/26)
El riesgo **no es binario**: depende de **QUÉ** mandas, no de **QUÉ herramienta**.

| Nivel | Qué es | Riesgo |
|---|---|---|
| **N0 público** | nombre de gen, subtipo en general, ensayo publicado | cero |
| **N1 clínico estructurado SIN nombre** | coordenada de variante, biomarcador, «HR+/HER2− Ki67 x%» | bajo (una variante **no eres tú**) |
| **N2 relato o informe crudo** | nombre + edad + fecha + hospital + historia | **el único peligroso** (re-identificable; perfil público) |

**Clave que responde «¿el muro cuesta NED?»:** las bases de datos (UniProt, ClinVar, AlphaGenome, PubMed…) responden sobre **BIOLOGÍA, no sobre TI**. Tu nombre no añade ni un bit de señal. Por eso el muro **casi nunca cuesta NED**: lo que acerca a NED es N0-N1, que son seguros.

**Protocolo cuando un tool de ALTO valor NED pide N2** (NO bloquear por reflejo ni volcar crudo):
1. **De-identificar** (`deid.py`, deja N1), o
2. exigir **contrato real** (BAA/enterprise, no «confía en que no logueo»), o
3. que {{TITULAR}} lo **acepte CONSCIENTE** para ese uso concreto.

Ponerle el riesgo con nombre y apellidos cada vez (qué nivel, peligro concreto, mitigación) y **decide ella**. **Nunca N2 crudo a un SaaS sin contrato.**

**La distinción N1/N2 ya vive en código** (13-sep-26, plan `plan-enrutado-crudo-solo-local-y-gate-por-check`): `tools/borde.py::identificador_directo()` detecta el crudo N2 (nombre/DNI/email/teléfono/NHC) por separado de `clasificar()` (que también marca N1). `tools/enruta.py::elegir()` lo usa para que, con crudo, `claude` deje de ser destino válido en la decisión: solo `local` vale, lectura estricta. Local caído + crudo → bloqueo con las 3 opciones de arriba, nunca degradar a claude.

Detalle: memoria [[feedback-niveles-sensibilidad-datos]], [[feedback-muro-egress-no-nacionalidad]].

## Otras reglas al tocar esto
- **No versionar** lo clínico ni los secretos (ya en `.gitignore`). El repo no se sube a GitHub jamás: [[project-repo-clinico-en-historial]].
- **No reproducir cifras clínicas** sin cotejarlas contra la fuente primaria: [[feedback-cotejar-siempre-fuente-clinica]].
- **Verificar la identidad del paciente** en cualquier informe antes de usarlo (hay informes de terceros en el archivo): [[feedback-verificar-identidad-paciente-en-informe]].
- Secretos: **solo Llavero**, nunca en fichero: [[feedback-secretos-solo-llavero]].
