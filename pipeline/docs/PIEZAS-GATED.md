# Piezas que esperan una clave/licencia/decisión de {{TITULAR}}

> Estas NO se instalan solas: requieren un registro académico, un token, o una
> decisión de runtime que solo puede tomar {{TITULAR}}. Para cada una está el **paso
> EXACTO**. Todo lo demás (lo que SÍ corre hoy) está en `INSTALADO.md`.
>
> Muro: ninguna de estas manda datos crudos fuera. NetMHCpan/pVACtools corren en
> **local/Docker** (egress cero). OncoKB/COSMIC reciben solo terminología genérica.

---

## 1. NetMHCpan 4.1 / NetMHCIIpan 4.x — licencia académica DTU (GRATIS, no automatizable)

**Por qué gated:** DTU exige aceptar una licencia académica y rellenar un formulario;
el binario se descarga con un enlace personal por email. No hay instalación anónima.

**Paso exacto para {{TITULAR}}:**
1. Ir a https://services.healthtech.dtu.dk/services/NetMHCpan-4.1/ → botón
   *"Downloads"* / *"Academic software download"*.
2. Rellenar el formulario (nombre, institución/email académico, uso no comercial).
   Para institución vale el proyecto/su afiliación; uso = investigación.
3. Llega un email con el enlace al `.tar.gz` (Linux) y al fichero de datos.
4. Guardar ambos en `~/claudecode/pipeline/vendor/netMHCpan/` (carpeta gitignored).
   Avisarme: yo lo descomprimo, ajusto el `NMHOME`/`data` y lo enchufo al pipeline.
5. Repetir para NetMHCIIpan-4.x (clase II, epítopos CD4) en la misma página.

**Nota arm64:** los binarios DTU son x86_64/Linux → en este Mac corren vía Docker
(linux/amd64) o en el lab/nube. MHCflurry (ya instalado) cubre el hueco mientras
tanto como predictor abierto; NetMHCpan se añade como predictor de referencia.

---

## 2. pVACtools (orquestador estándar) — vía DOCKER (decisión de runtime)

**Por qué gated:** el `pip install` falla en arm64+py3.12 (pins viejos de pandas que
compilan de fuente). El camino soportado y reproducible del Griffith Lab es el
**contenedor oficial** `griffithlab/pvactools` (verificado en Docker Hub: tags
`latest`, `7.0.1`). Necesita un runtime de contenedores, que ahora mismo NO está
arrancado (hay CLI de Docker pero sin daemon ni Docker Desktop).

**Paso exacto para {{TITULAR}} (elige UNO):**
- **Opción A (recomendada, ligera en Mac):** instalar Colima como runtime:
  `brew install colima docker` y luego `colima start`. Avisarme y yo hago
  `docker pull griffithlab/pvactools:7.0.1` y cableo el wrapper.
- **Opción B:** instalar Docker Desktop para Mac (https://www.docker.com/products/docker-desktop/)
  y abrirlo una vez. Igual: avísame y yo tiro de la imagen.
- En el lab/nube privada con datos reales, pVACtools corre nativo en Linux x86_64
  (lo más rápido). El pipeline ya está escrito para enchufarlo ahí.

**Privacidad:** la imagen oficial corre **100 % local**; obligar a `--iedb-install-directory`
local para que las predicciones NO salgan a la API pública de IEDB (egress cero).

---

## 3. OncoKB — token de API (GRATIS para académicos)

**Por qué gated:** requiere registro y un token personal; su licencia PROHÍBE usar
sus datos para entrenar/embeddings de agentes.

**Paso exacto para {{TITULAR}}:**
1. Registrarse en https://www.oncokb.org/account/register (email de proyecto, no nominativo).
2. Solicitar acceso académico (Profile → API Access) — aprueban en días.
3. Copiar el token y guardarlo en el **Llavero** (nunca en el chat ni en fichero):
   `security add-generic-password -a btp -s ONCOKB_TOKEN -w` (pega el token al pedirlo).
4. Avisarme: yo añado el cliente `oncokb` que lee el token del Llavero. Solo se le
   manda terminología genérica (gen + variante, p. ej. `PIK3CA E545K`), nunca PII.
5. ANTES de mandar variantes reales: email a `contact@oncokb.org` confirmando su
   política de retención (lo pide el comité de tooling).

---

## 4. COSMIC — registro + descarga (GRATIS académico/no comercial)

**Por qué gated:** la descarga del Census/mutaciones exige login (cuenta COSMIC);
no hay API anónima. La licencia comercial es de QIAGEN.

**Paso exacto para {{TITULAR}}:**
1. Crear cuenta en https://cancer.sanger.ac.uk/cosmic/register (uso académico/no comercial).
2. Descargar el **Cancer Gene Census** (CSV) y, si se quiere, las firmas mutacionales,
   desde https://cancer.sanger.ac.uk/cosmic/download
3. Guardar los CSV en `~/claudecode/pipeline/vendor/cosmic/` (gitignored).
4. Avisarme: yo añado un lector LOCAL del Census (¿es driver? contexto poblacional)
   que complementa a OncoKB para priorizar variantes. Cero egress (todo en local).

---

## 5. AlphaFold3 (predicción de complejos de novo) — opcional, NO necesario para el dossier

**Por qué gated/aparcado:** la AlphaFold **DB** (estructuras ya calculadas) ya está
cableada y funciona sin clave (ver `INSTALADO.md`). AlphaFold3 *de novo* (para modelar
un péptido-MHC concreto) requiere pesos bajo licencia de Google DeepMind y mucha GPU.

**Decisión:** no se necesita para entregar el dossier de neoantígenos. Si en el futuro
un experto pide modelar un complejo pMHC concreto, se valora en su momento (GPU + licencia).

---

## Resumen de lo que falta de {{TITULAR}}

| Pieza | Acción de {{TITULAR}} | Tiempo | Bloquea el dossier? |
|---|---|---|---|
| NetMHCpan / NetMHCIIpan | formulario DTU → email → guardar tar.gz | ~10 min + espera email | No (MHCflurry cubre) |
| pVACtools (Docker) | `brew install colima docker && colima start` (o Docker Desktop) | ~10 min | No (pipeline corre con MHCflurry) |
| OncoKB | registro + token al Llavero | ~5 min + aprobación | No (mejora priorización) |
| COSMIC | registro + descargar Census CSV | ~10 min | No (mejora priorización) |

**Ninguna de estas bloquea arrancar el pipeline el día de la biopsia.** Lo que llega
de Zúrich (VCF + HLA) ya tiene el motor que corre hoy. Estas piezas suben la calidad
y la robustez (predictor de referencia, oráculo de drivers, contexto poblacional).
