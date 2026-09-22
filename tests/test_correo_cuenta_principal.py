#!/usr/bin/env python3
"""tests/test_correo_cuenta_principal.py — que el pulso 24/7 no vuelva a vigilar la cuenta que no es.

EL INCIDENTE (20-sep-2026). `tools/state/correo/buzon.json` contenía la cuenta SECUNDARIA, y ese
es el fichero que leen DIEZ herramientas: vega_gate, correo_triage, centinela_ned, pendientes,
triage_route_correo, buzon_ideas, reel_digest, reconciliar_estado, elevenlabs_voz y el propio
correo_imap. Llevaba así desde el 25-jun.

La cadena era ésta, y basta con romper cualquier eslabón para repetirla:
  · `correo_imap.py` fija `DEFAULT_USER = "titular.mgp@gmail.com"`;
  · `once()` resuelve `user = user or BTP_GMAIL_USER or DEFAULT_USER`;
  · el plist del daemon exportaba `BTP_GMAIL_USER=titular@gmail.com`,
  → así que el pulso barría la secundaria y la escribía en el buzón de la principal.

Lo irónico: `once_todas()` se escribió el 11-jul justo para tapar un punto ciego que hizo perder
una invitación real, y su propia docstring dice que la principal «sigue usando SEEN/BUZON a
secas → mismo fichero que ya lee vega_gate/HOY/el agente». Se escribió y nunca se enganchó al
daemon, que siguió corriendo `once`.

Este test vigila las dos mitades: el plist (qué cuenta y qué subcomando) y el código (que
DEFAULT_USER siga siendo la principal y que esté en las cuentas del pulso).

Actualizado el mismo 20-sep unas horas después: `buzon.json` pasó a ser la UNIÓN de las cuentas
del pulso (decisión de {{TITULAR}}), porque mandar solo la principal arreglaba un punto ciego
creando otro — la secundaria salía del fichero y dejaba de alimentar al gate y al centinela.
"""
import os
import plistlib
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
ARBOL = os.path.dirname(AQUI)
sys.path.insert(0, os.path.join(ARBOL, "tools"))

PLIST = os.path.join(ARBOL, "tools", "launchd", "com.btp.correo-imap.plist")
PRINCIPAL = "titular.mgp@gmail.com"

FALLOS, OK = [], []


def ok(caso, cond, detalle=""):
    (OK if cond else FALLOS).append(caso)
    print(("  ok  " if cond else "  FALLO  ") + caso + ("" if cond else f" {detalle}"))


if not os.path.exists(PLIST):
    print("test_correo_cuenta_principal: sin plist (%s) — SALTADO" % PLIST)
    sys.exit(77)

with open(PLIST, "rb") as f:
    d = plistlib.load(f)

env = d.get("EnvironmentVariables") or {}
args = [str(a) for a in (d.get("ProgramArguments") or [])]

# --- 1. El plist: ni fija la cuenta, ni barre solo una -----------------------------------------
ok("el plist NO fija BTP_GMAIL_USER (fijarla cambia qué cuenta vive en buzon.json)",
   "BTP_GMAIL_USER" not in env, "-> %r" % env.get("BTP_GMAIL_USER"))
ok("el pulso corre `todas`, no `once` (si no, la otra cuenta no la barre nadie)",
   "todas" in args and "once" not in args, "-> %r" % args[-2:])
ok("el plist sigue apuntando a correo_imap.py",
   any(a.endswith("correo_imap.py") for a in args), "-> %r" % args)
ok("y corre desde casa base, no desde un worktree",
   not any(".claude/worktrees" in a for a in args), "-> %r" % args)

# --- 2. El código: la principal sigue siendo la principal --------------------------------------
import correo_imap  # noqa: E402

ok("DEFAULT_USER es la cuenta principal",
   correo_imap.DEFAULT_USER == PRINCIPAL, "-> %r" % correo_imap.DEFAULT_USER)
ok("la principal está entre las cuentas del pulso 24/7",
   PRINCIPAL in correo_imap.CUENTAS_DAEMON, "-> %r" % (correo_imap.CUENTAS_DAEMON,))
ok("el pulso vigila más de una cuenta",
   len(correo_imap.CUENTAS_DAEMON) >= 2, "-> %r" % (correo_imap.CUENTAS_DAEMON,))

# --- 3. Y el buzón que leen los diez trae TODAS las cuentas del pulso -------------------------
# Esta comprobación cambió de forma el 20-sep-2026, unas horas después de escribirse, y el
# porqué importa más que el cómo: antes exigía que `once_todas` mandara la cuenta por defecto a
# BUZON (`u = None if user == DEFAULT_USER`), que era la mitad del arreglo. {{TITULAR}} decidió la
# otra mitad: `buzon.json` es la UNIÓN de las cuentas del pulso, no el buzón de una de ellas.
# Con el reparto anterior se arreglaba un punto ciego creando otro — la secundaria salía de
# buzon.json y dejaba de alimentar al gate, al centinela y a los pendientes.
#
# Y se cae el `u = None`, que era justo el eslabón peligroso: resolvía la cuenta por
# BTP_GMAIL_USER, así que el ENTORNO decidía qué se barría. Ahora cada cuenta va con `user`
# explícito. Lo que este bloque vigila sigue siendo lo mismo: que nadie cambie el reparto de
# cuentas a fichero sin enterarse de quién lee buzon.json.
src = open(os.path.join(ARBOL, "tools", "correo_imap.py"), encoding="utf-8").read()
ok("once_todas compone el buzón compartido con la unión de las cuentas",
   "_componer_buzon_union(cuentas)" in src,
   "-> cambió el reparto de cuentas a fichero; revisa quién lee buzon.json")
ok("la unión se escribe en BUZON, que es el fichero que leen los demás",
   re.search(r"_write_atomic\(\s*BUZON\s*,", src) is not None,
   "-> si la unión no acaba en BUZON, los consumidores se quedan sin verla")
ok("ninguna cuenta se barre ya según el ENTORNO (sin `u = None`)",
   re.search(r"u\s*=\s*None\s+if\s+user\s*==\s*DEFAULT_USER", src) is None,
   "-> vuelve a haber un camino donde BTP_GMAIL_USER decide qué cuenta se barre")
ok("y el marcador por cuenta se siembra antes de cambiarle el sitio",
   "_siembra_marcador_por_cuenta(cuentas)" in src,
   "-> sin la siembra, la cuenta que venía del marcador global avisa de correo ya leído")

print(("FALLOS: " + ", ".join(FALLOS)) if FALLOS
      else "test_correo_cuenta_principal: %d/%d OK" % (len(OK), len(OK)))
sys.exit(1 if FALLOS else 0)
