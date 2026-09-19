# Cómo contribuir

Lee esto entero antes de abrir nada. Es corto y te ahorra trabajo perdido.

## Qué es este repo, para que no haya sorpresas

Un sistema **personal, en producción, con un solo usuario**, que corre 24/7 en un Mac
concreto con acceso a correo, Drive y una carpeta clínica. No es una librería ni busca
usuarios. Está público porque el patrón puede servirle a alguien más.

Esto tiene una consecuencia directa: **cada línea que entra se revisa a mano, una por
una.** No es desconfianza, es el modelo de amenaza — un parche que se mezcle sin leer
ejecuta código en esa máquina con esos accesos.

## Este repo es un espejo — léelo antes de invertir trabajo

El árbol que ves aquí se **deriva** de un repo privado con `tools/publicar.py`, que lo
regenera entero. Consecuencia directa y honesta: **un merge hecho aquí se pierde en la
siguiente regeneración**. No es una política, es cómo está construido.

Así que un PR aceptado se aplica en el repo de origen y aparece aquí en la siguiente
publicación, y el PR se cierra con «aplicado en upstream». Tu cambio entra en el sistema,
pero **tu commit no queda en este historial**. Con un repo derivado no le conocemos arreglo
limpio [verificado: el generador reescribe el árbol entero], y preferimos decirlo antes de que dediques una tarde.

Lo que sí ganas: nada entra en el sistema vivo sin pasar por la casa base, donde están los
tests, el muro y el gate de fusión. Ningún PR toca producción directamente.

## Lo que de verdad ayuda, por orden

1. **Issues.** "Esto que hacéis con X está mal, mirad Y." Es la contribución más
   valiosa y la que menos cuesta revisar. Abre uno aunque no tengas el arreglo.
2. **Usarlo para tu caso y contar qué se rompió.** Si coges el arnés para otra
   enfermedad u otro contexto y algo no encaja, ese reporte vale más que un PR.
3. **Un PR pequeño y acotado**, si ya hablamos del problema en un issue.

## Lo que no va a entrar

- PRs grandes sin issue previo. Se cierran sin revisar, por mucho que estén bien.
- Refactors "de limpieza", cambios de estilo, o migraciones de herramientas.
- Nada que toque `tools/salida.py`, `.claude/hooks/` o `tools/githooks/` sin
  discusión previa: son el muro.
- Dependencias nuevas, salvo que resuelvan algo que no se puede resolver sin ellas.

## Reglas del código, si mandas un PR

- **Tests o no entra.** `bash tests/test_all.sh`. Y aplica el canario del autor:
  rompe a propósito el código que cubre tu test y confirma que va ROJO. Un test que
  pasa con el código roto es decorativo.
- **Fail-closed.** Ante lo ambiguo, lo ilegible o lo ausente, devuelve error. Un verde por
  defecto o una degradación silenciosa se rechaza en revisión.
- **Nada hacia fuera.** Ningún camino nuevo puede publicar, contactar, pagar ni
  desplegar sin pasar por `tools/salida.py`.
- **Sin rutas absolutas.** Usa `BTP_REPO` y `BTP_STATE_DIR`.
- **Comentarios que digan el porqué**, no el qué. Y el "porqué-no" de las
  alternativas que descartaste: una decisión sin su *Do not* se revierte sola.
- Commits convencionales (`feat:`, `fix:`, `chore:`, `docs:`) con ámbito.

## Privacidad — lo que nunca debe entrar

Este repo versiona el sistema, no el caso. Mantén fuera del código, los comentarios, los
tests, los mensajes de commit y los nombres de rama:

- Nombres de personas reales, hospitales, médicos o identificadores de paciente.
- Datos clínicos: diagnósticos, biomarcadores, cifras de informes, fechas de pruebas.
- Rutas a `00_FUENTE-DE-VERDAD/` con contenido real, ni fixtures copiados de ahí.

Los tests usan datos sintéticos. Si necesitas un fixture, invéntalo.

`gitleaks` corre sobre cada PR, pero solo caza secretos: los nombres y los datos
clínicos no los ve nadie más que tú.

## Seguridad

¿Encontraste algo que permita egress sin gate, ejecución de texto de las cajas, o fuga
de la carpeta clínica? **No abras un issue público.** Escribe en privado por el correo
del perfil de GitHub.
