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

## 🔐 Niveles de sensibilidad (regla de {{TITULAR}}, 5/7/26)
El riesgo **no es binario**: depende de **QUÉ** mandas, no de **QUÉ herramienta**.

| Nivel | Qué es | Riesgo |
|---|---|---|
| **N0 público** | nombre de gen, subtipo en general, ensayo publicado | cero |
| **N1 clínico estructurado SIN nombre** | coordenada de variante, biomarcador, «HR+/HER2− Ki67 x%» | bajo (una variante **no eres tú**) |
| **N2 relato o informe crudo** | nombre + edad + fecha + hospital + historia | **el único peligroso** (re-identificable; acosador activo + perfil público) |

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
