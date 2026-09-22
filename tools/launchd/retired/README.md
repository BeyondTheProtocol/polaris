# Daemons JUBILADOS — plist fuente fuera del roster

Movidos aquí el 29/6/26 ({{TITULAR}}: "soluciónalo"). NO estaban instalados ni corriendo; sus plist fuente hacían que `_daemons_roster()` (glob `tools/launchd/*.plist`) los marcara como "caídos" = falsas alarmas de Vega.

- parte-mediodia/tarde/noche → fundidos en el HOY único (hoy-compose+enviar-hoy).
- mail-barrido → la barrida batch la cubre `correo-imap` (tiempo real). NADIE lo invoca → si quieres la barrida diaria, reinstala su plist.
- email-archive → su función la llama `correo_outbox.py`, no necesita daemon propio.

Mover de vuelta a `tools/launchd/` para reactivar. El glob no entra en subcarpetas → quedan fuera del roster sin tocar healthcheck.py.
