# Acuerdo de contribución a Polaris (CLA ligero)

## Por qué existe este documento

Polaris se publica bajo AGPL-3.0, pero la titular de los derechos
(Beyond the Protocol) también quiere poder ofrecer, a quien lo pida,
una licencia comercial distinta — sin tener que localizar y pedir
permiso a cada persona que haya contribuido código cada vez que surja
esa petición.

Un DCO (Developer Certificate of Origin, el `Signed-off-by` que usan
proyectos como el kernel de Linux) certifica que tienes derecho a
enviar tu contribución bajo la licencia del proyecto. Es ligero y basta
para proyectos que solo publican bajo una licencia. Pero **no basta
aquí**: un DCO solo dice "autorizo a que esto se use bajo AGPL-3.0", no
"autorizo a que el proyecto lo ofrezca también bajo otra licencia". Sin
un permiso más amplio, cada intento de vender una licencia comercial de
una versión de Polaris que incluya tu contribución necesitaría tu
autorización individual — inviable en la práctica.

Por eso este acuerdo va un paso más allá de un DCO puro, pero se queda
corto a propósito: no es un CLA corporativo de cesión de copyright
(esos existen para que la empresa sea la única dueña; aquí no hace
falta y sería pedir de más). Conservas tu copyright. Solo concedes una
licencia amplia para que el proyecto pueda seguir con su modelo de
doble vía: AGPL-3.0 gratis para quien la quiera, y una licencia
comercial para quien la necesite.

## El acuerdo

Al enviar una contribución (pull request, parche, o cualquier código,
documentación o contenido) a este repositorio, declaras y aceptas que:

1. **Tienes derecho a hacerlo.** La contribución es tuya, o tienes
   autorización de quien la creó, para enviarla bajo estos términos.
2. **Conservas tu copyright.** Este acuerdo no te lo transfiere ni te
   lo quita.
3. **Concedes a Beyond the Protocol una licencia** perpetua, mundial,
   no exclusiva, libre de regalías, irrevocable y con derecho a
   sublicenciar, para usar, copiar, modificar, distribuir, ejecutar en
   red y explotar comercialmente tu contribución de cualquier forma —
   incluida la de ofrecerla bajo licencias distintas de la AGPL-3.0
   (por ejemplo, una licencia comercial), sola o como parte de Polaris
   o de obras derivadas.
4. **Concedes también una licencia de patentes**, si aplica, sobre
   cualquier reivindicación de patente tuya que tu contribución
   necesariamente infrinja — limitada al uso de tu contribución sola o
   combinada con Polaris.
5. **No hay obligación de incorporarla.** Beyond the Protocol puede no
   usar tu contribución, y este acuerdo no crea relación laboral, de
   sociedad ni de representación entre las partes.
6. La contribución se entrega "tal cual" (as is), sin garantías de
   ningún tipo.

## Cómo se manifiesta la aceptación

Para que un PR se revise, tiene que traer las dos cosas:

1. `Signed-off-by: Tu Nombre <tu@email>` en el último commit
   (`git commit -s`).
2. Esta línea, tal cual, en la descripción del PR:

   > Acepto el Acuerdo de contribución de Polaris
   > (ACUERDO-CONTRIBUCION.md).

Sin las dos, el PR no se revisa — así lo dice CONTRIBUTING.md.

**Nota de proceso:** hoy esto se comprueba a mano, igual que ya se
revisa cada línea de cada PR (ver CONTRIBUTING.md, "cada línea que
entra se revisa a mano"). Si el volumen de PRs externos crece, vale la
pena automatizarlo con un bot tipo CLA-assistant que bloquee el merge
hasta que la persona lo acepte una vez y quede registrado; con el
volumen esperado de un proyecto personal, no hace falta ahora.
