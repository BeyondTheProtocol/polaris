## Qué cambia y por qué

<!-- Una o dos frases. Si no hay issue previo, explica el problema aquí y espera antes de
     escribir código: los PRs grandes sin issue se cierran sin revisar. -->

Issue relacionado: #

## Antes de pedir revisión

- [ ] 🧪 `bash tests/test_all.sh` en verde (en Linux: `BTP_PORTABLE=1 bash tests/test_all.sh`)
- [ ] 🧹 `python3 tools/ci_barrido.py --diff origin/master...HEAD` limpio
- [ ] 🔒 Ningún dato real de nadie en código, tests ni fixtures
- [ ] 🛡️ No debilita el muro (gate de salida, hooks, detectores). Si lo toca, dilo aquí:

## Lo que hay que saber antes de invertir tiempo

Este repo es un **espejo derivado**: un merge aquí se pierde en la siguiente regeneración.
Un PR aceptado se aplica en el repo de origen y reaparece publicado; el PR se cierra con
«aplicado en upstream». Tu cambio entra en el sistema, tu commit no queda en este historial.
