#!/usr/bin/env python3
"""test_dominios_con_dueno.py — ningún dominio crítico puede quedarse sin agente que lo atienda.

POR QUÉ EXISTE (12-sep-2026). Ese día se encargó al agente `tecnico` diagnosticar por qué llevaban
12 pasadas fallando los logins de correo —el canal por el que llegan las respuestas de sus
oncólogas— y **rebotó el encargo**: su ficha solo hablaba de G0DM0D3, la web, dashboards e
integraciones, así que la infraestructura interna de Polaris no era de nadie. Tenía razón en no
tocar a ciegas un dominio fuera de su ámbito; el problema era que ese dominio no tenía dueño.

Un agente que rechaza por ámbito es correcto. Un dominio sin dueño es un agujero: nadie recoge el
fallo, y la alarma envejece en el libro de deuda mientras el sistema parece sano. Este test
convierte «que no vuelva a pasar» en algo que se comprueba solo.

No valida calidad ni redacción: solo que cada dominio crítico aparezca en la ficha de algún agente
activo. Si un dominio nuevo se vuelve crítico, se añade aquí y se le busca dueño.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTES = os.path.join(ROOT, ".claude", "agents")

# dominio → palabras que bastan para considerarlo cubierto por una ficha
DOMINIOS = {
    "daemons / rutinas 24-7": ("launchd", "daemon"),
    "correo (IMAP/SMTP)": ("imap", "smtp", "correo"),
    "cola de encargos": ("cola.py", "cola de", "dispatcher"),
    "secretos y credenciales": ("llavero", "secret", "oauth"),
    "muro y privacidad": ("muro", "egress", "privacidad"),
    "caso clínico": ("clínic", "clinic", "oncolog"),
    "git y ramas": ("git", "rama", "worktree"),
    "web publicada": ("helptitular", "web"),
    "evidencia y verificación": ("verific", "evidencia", "fuente primaria"),
}

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    fichas = {}
    for nombre in sorted(os.listdir(AGENTES)):
        if not nombre.endswith(".md"):
            continue
        txt = open(os.path.join(AGENTES, nombre), encoding="utf-8").read().lower()
        if "estado: activo" in txt or "estado:" not in txt:
            fichas[nombre[:-3]] = txt

    ok(len(fichas) >= 20, "hay fichas de agentes que leer (%d)" % len(fichas))

    for dominio, claves in DOMINIOS.items():
        duenos = [a for a, t in fichas.items() if any(k in t for k in claves)]
        ok(bool(duenos), "«%s» tiene al menos un agente que lo cubre" % dominio)

    # El caso concreto que lo provocó: la infraestructura interna, que rebotó sin dueño.
    #
    # OJO — «mencionar» no es «poder arreglar», y el primer intento de este test lo confundía:
    # pasaba en verde aunque se le quitara el ámbito al técnico, porque Vega (`asistente`) también
    # nombra los daemons y el correo... pero Vega SOLO AVISA y deja borradores, nunca ejecuta. Un
    # dominio vigilado por quien no puede repararlo sigue sin dueño, que es exactamente la
    # situación del 12-sep. Así que aquí se exige un agente que además pueda ARREGLARLO.
    solo_avisa = ("solo avisa", "nunca contacta", "solo borradores", "no ejecuta", "sin ejecutar")
    infra = [a for a, t in fichas.items()
             if "launchd" in t and ("imap" in t or "correo" in t)
             and not any(s in t for s in solo_avisa)]
    ok(bool(infra),
       "la INFRAESTRUCTURA interna (daemons + correo) tiene un dueño que puede ARREGLARLA, "
       "no solo vigilarla — es lo que rebotó el 12-sep y dejó 12 pasadas de fallo sin recoger")

    print("RESULTADO dominios_con_dueno: %d OK, %d fallos" % (_pass, _fail))
    print("✅ DOMINIOS CON DUEÑO EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
