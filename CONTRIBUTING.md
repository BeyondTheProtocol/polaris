# Cómo contribuir

¿Primera vez? Empieza por un issue con la etiqueta
[`good first issue`](https://github.com/BeyondTheProtocol/polaris/labels/good%20first%20issue).
Son tareas pequeñas, sin datos clínicos, que se pueden hacer en una tarde.

## Dos cosas que conviene saber antes

- **Es un sistema personal en producción**, con un solo usuario y acceso a correo y a una
  carpeta clínica. Cada línea que entra se revisa a mano.
- **Este repo es un espejo.** Se regenera desde uno privado con `tools/publicar.py`. Un PR
  aceptado se aplica en origen y se cierra con «aplicado en upstream»: tu cambio entra, pero
  tu commit no queda en este historial.

## Qué ayuda más

1. **Un issue**, aunque no traigas el arreglo: «esto está mal, mirad esto otro».
2. **Un PR pequeño** sobre un issue ya hablado.
3. Ideas para los problemas grandes: [`docs/lo-que-falta.md`](docs/lo-que-falta.md).

No entran: PRs grandes sin issue previo, refactors de estilo, dependencias nuevas, ni
cambios en `tools/salida.py`, `.claude/hooks/` o `tools/githooks/` sin discutirlo antes.

## Si mandas un PR

- **Con test.** `bash tests/test_all.sh`. Rompe a propósito lo que cubre tu test y comprueba
  que se pone rojo.
- **Fail-closed:** ante lo ambiguo, error; nunca un verde por defecto.
- **Sin rutas absolutas:** usa `BTP_REPO` y `BTP_STATE_DIR`.
- **Nada de datos reales:** ni nombres, ni hospitales, ni cifras clínicas. Los fixtures, inventados.
- Commits convencionales (`feat:`, `fix:`, `docs:`) y firmados con `git commit -s`, aceptando
  en el PR el [Acuerdo de contribución](ACUERDO-CONTRIBUCION.md). No te quita el copyright.

## Seguridad

Si encuentras una forma de sacar datos sin pasar por el muro, **no abras un issue público**:
escribe al correo del perfil de GitHub.
