#!/usr/bin/env python3
"""tools/codigo_rojo.py — CORTAFUEGOS de emergencia del sistema (regla inquebrantable).

Regla de {{TITULAR}} (21/6/26): si CUALQUIER cosa que el sistema haga o descubra **impide,
bloquea, reduce las probabilidades o pone en peligro el GOAL (NED — y su ruta de hoy, la
vacuna)**, hay que PARAR TODAS LAS MÁQUINAS, AVISARLE FUERTE, y EXPLICARLE en detalle qué
está pasando. Esto es CÓDIGO, no solo una instrucción al modelo.

`trigger(motivo, detalle)` hace, en orden:
  1. PARA: crea ambos kill-switches (~/.btp.HALT y $REPO/.HALT) → el lazo no arranca nada
     nuevo y el muro deniega todo en el agente en vuelo.
  2. EXPLICA: escribe un informe detallado en Gestion/CODIGO-ROJO.md (prominente).
  3. AVISA FUERTE: manda la alerta a Telegram por salida.alerta_critica (atraviesa el HALT
     — es el único mensaje que debe pasar siempre).
  4. APAGA: descarga los daemons de launchd (btp_run.sh stop), best-effort.

Levantar el código rojo es un acto HUMANO explícito (gated por BTP_PRESENCE_OK, que el
lazo no tiene): `clear`. Sin dependencias (stdlib).
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import salida

HOME = os.path.expanduser("~")
# Casa base SIEMPRE (no el worktree): si esto se dispara desde un worktree, el .HALT local,
# el informe y btp_run deben aterrizar en el sistema vivo, no en un árbol efímero gitignored.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
HALT_FILES = (os.path.join(HOME, ".btp.HALT"), os.path.join(REPO, ".HALT"))
ROJO_MD = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "Gestion", "CODIGO-ROJO.md")
BTP_RUN = os.path.join(REPO, "tools", "btp_run.sh")


def _halt_all(motivo):
    sello = "CÓDIGO ROJO: %s @ %s\n" % (motivo, datetime.now().isoformat())
    for h in HALT_FILES:
        try:
            with open(h, "a", encoding="utf-8") as f:
                f.write(sello)
        except Exception as e:
            sys.stderr.write("codigo_rojo: no pude escribir %s (%r)\n" % (h, e))


def _write_report(motivo, detalle):
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    bloque = (
        "\n\n# 🔴 CÓDIGO ROJO — %s\n\n"
        "## Motivo\n%s\n\n"
        "## Qué está pasando (detalle)\n%s\n\n"
        "## Qué he PARADO\n"
        "- Lazo autónomo: HALT activado (`~/.btp.HALT` y `.HALT`) → ningún agente nuevo "
        "arranca y el muro deniega TODO en el que estuviera en vuelo.\n"
        "- launchd: intento descargar dispatcher / bot / healthcheck (`btp_run.sh stop`).\n\n"
        "## Qué necesito de ti\n"
        "- LEE esto y decide. **No reanudes** hasta entender y resolver lo que lo disparó.\n"
        "- Reanudar (solo tú): `rm ~/.btp.HALT && rm ~/claudecode/.HALT` y luego "
        "`tools/btp_run.sh start` si procede.\n"
        % (ts, motivo, detalle or "(sin detalle adicional)"))
    try:
        os.makedirs(os.path.dirname(ROJO_MD), exist_ok=True)
        fd = os.open(ROJO_MD, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, bloque.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception as e:
        sys.stderr.write("codigo_rojo: no pude escribir el informe (%r)\n" % e)


HUELLAS = os.path.join(REPO, "tools", "state", "codigo_rojo_motivos.json")
RECORDATORIO_H = 24     # si la condición sigue viva mañana, un aviso más. Uno, no diez.


def _clave_motivo(motivo):
    """Huella estable de un motivo: minúsculas, sin dígitos ni espacios de más.

    Los dígitos se van a propósito: «detectó 2 check(s)» y «detectó 3 check(s)» son la MISMA
    condición contada distinto, y si la huella los separase el anti-repetición no serviría de nada.
    """
    s = re.sub(r"\d+", "#", str(motivo or "").lower())
    return re.sub(r"\s+", " ", s).strip()[:200]


def _huellas_cargar():
    try:
        with open(HUELLAS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _huellas_guardar(d):
    try:
        os.makedirs(os.path.dirname(HUELLAS), exist_ok=True)
        tmp = HUELLAS + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, HUELLAS)
    except Exception as e:
        sys.stderr.write("codigo_rojo: no pude guardar las huellas (%r)\n" % e)


def _debe_avisar(motivo, habia_halt=None):
    """¿Toca mandar la alerta de socorro, o este motivo ya avisó y la condición sigue igual?

    POR QUÉ (12-sep-2026). De 17 activaciones históricas, **15 eran el mismo motivo**: el barrido
    de seguridad en rojo, 10 de ellas en 48 horas (26-27 jul). Solo 2 fueron eventos únicos, y uno
    de esos dos era el que de verdad importaba: Fred Hutch declarando a {{TITULAR}} no elegible para la
    vacuna. Verificado contra `Gestion/CODIGO-ROJO.md` y el audit de `salida.py` — las 10 alertas
    SÍ se entregaron con HTTP 200, o sea que el canal funcionaba: el problema era que gritaba diez
    veces por lo mismo.

    Ese canal atraviesa el HALT y el silencio nocturno a propósito, y el propio docstring de
    `salida.alerta_critica` avisa de no desgastarlo. Diez avisos de socorro por una condición que
    ya estaba avisada hacen que el siguiente, el de verdad, compita con el ruido.

    Las tres garantías, en este orden:
      1. Un motivo **nuevo** avisa SIEMPRE, aunque el HALT ya esté puesto por otro. Una amenaza
         distinta no puede quedar tapada por otra: eso sería silenciar un código rojo.
      2. El mismo motivo, con el HALT todavía puesto, NO vuelve a avisar. Para las máquinas igual
         y lo escribe en el informe igual; lo único que se calla es la alerta repetida.
      3. Si la condición sigue viva 24 h después, un recordatorio. Uno.
    Y `clear()` (acto humano) borra las huellas: tras levantarlo, todo vuelve a avisar.
    """
    clave = _clave_motivo(motivo)
    d = _huellas_cargar()
    prev = d.get(clave)
    ahora = time.time()
    if not prev:
        d[clave] = {"primera": ahora, "ultimo_aviso": ahora, "veces": 1,
                    "motivo": str(motivo)[:300]}
        _huellas_guardar(d)
        return True, "motivo nuevo"
    # Sin HALT puesto, la condición se resolvió y vuelve: es un evento nuevo, avisa.
    # `habia_halt` lo mide el llamante ANTES de poner el HALT — si se mirara aquí, `trigger` ya
    # lo habría creado él mismo dos líneas antes y esta rama no se daría nunca. Lo cazó
    # tests/test_codigo_rojo_repeticion.py con el caso de alguien que levanta el HALT a mano.
    if habia_halt is None:
        habia_halt = any(os.path.exists(h) for h in HALT_FILES)
    if not habia_halt:
        d[clave] = {"primera": ahora, "ultimo_aviso": ahora,
                    "veces": int(prev.get("veces", 1)) + 1, "motivo": str(motivo)[:300]}
        _huellas_guardar(d)
        return True, "volvió tras levantarse el HALT"
    prev["veces"] = int(prev.get("veces", 1)) + 1
    horas = (ahora - float(prev.get("ultimo_aviso", 0))) / 3600.0
    if horas >= RECORDATORIO_H:
        prev["ultimo_aviso"] = ahora
        d[clave] = prev
        _huellas_guardar(d)
        return True, "recordatorio tras %.0f h sin resolverse" % horas
    d[clave] = prev
    _huellas_guardar(d)
    return False, ("ya avisado hace %.1f h y el HALT sigue puesto (repetición nº %d)"
                   % (horas, prev["veces"]))


def _stop_launchd():
    try:
        subprocess.run(["bash", BTP_RUN, "stop"], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        sys.stderr.write("codigo_rojo: btp_run stop falló (%r)\n" % e)


def trigger(motivo, detalle=""):
    """Activa el código rojo: para todo, explica, avisa fuerte. Idempotente y best-effort
    en cada paso (un fallo no impide los demás)."""
    # Se mira ANTES de parar: una vez puesto el HALT ya no se puede saber si venía de antes, y
    # esa diferencia es la que distingue «esta condición sigue sin resolverse» de «volvió».
    habia_halt = any(os.path.exists(h) for h in HALT_FILES)
    _halt_all(motivo)                       # 1. PARAR primero (cortar la hemorragia)
    _write_report(motivo, detalle)          # 2. EXPLICAR
    alerta = ("🔴🔴🔴 CÓDIGO ROJO 🔴🔴🔴\n\n"
              "He PARADO todas las máquinas porque algo amenaza el objetivo (NED).\n\n"
              "MOTIVO: %s\n\n%s\n\n"
              "Detalle completo en Gestion/CODIGO-ROJO.md. No reanudes hasta hablarlo."
              % (motivo, (detalle or "")[:1200]))
    # 3. AVISAR (atraviesa el HALT) — pero UNA vez por motivo mientras la condición siga viva.
    # Parar y explicar se hacen SIEMPRE; lo único que se calla es la alerta repetida.
    avisar, por_que = _debe_avisar(motivo, habia_halt)
    if avisar:
        res = salida.alerta_critica(alerta)
    else:
        res = {"delivered": False, "blocked": False, "reason": "alerta no repetida: %s" % por_que}
    _stop_launchd()                         # 4. APAGAR los daemons
    sys.stderr.write("🔴 CÓDIGO ROJO activado: %s | alerta: %s (%s)\n"
                     % (motivo, res.get("reason"), por_que))
    return res


def clear(motivo="resuelto por {{TITULAR}}"):
    """Levanta el código rojo (acto HUMANO): quita ambos HALT. Gated por BTP_PRESENCE_OK
    (el lazo no lo tiene → no puede auto-levantarse)."""
    if os.environ.get("BTP_PRESENCE_OK") != "1":
        print("clear: acto humano — requiere BTP_PRESENCE_OK=1 (el lazo no puede levantarlo)")
        return False
    for h in HALT_FILES:
        try:
            if os.path.exists(h):
                os.remove(h)
        except Exception as e:
            sys.stderr.write("codigo_rojo.clear: no pude quitar %s (%r)\n" % (h, e))
    # Borra las huellas del anti-repetición: tras levantarlo, TODO vuelve a avisar. Si no se
    # limpiaran, una condición que reapareciera mañana se encontraría con su propia huella y se
    # callaría — que es exactamente el fallo que este mecanismo existe para no cometer.
    try:
        if os.path.exists(HUELLAS):
            os.remove(HUELLAS)
    except Exception as e:
        sys.stderr.write("codigo_rojo.clear: no pude limpiar las huellas (%r)\n" % e)
    print("código rojo levantado:", motivo)
    return True


def main(argv):
    if not argv or argv[0] not in ("trigger", "clear", "status"):
        print('uso: codigo_rojo.py trigger "<motivo>" ["<detalle>"] | clear | status')
        return 2
    if argv[0] == "trigger":
        motivo = argv[1] if len(argv) > 1 else "sin motivo especificado"
        detalle = argv[2] if len(argv) > 2 else ""
        trigger(motivo, detalle)
        return 0
    if argv[0] == "clear":
        return 0 if clear() else 1
    activos = [h for h in HALT_FILES if os.path.exists(h)]
    print("CÓDIGO ROJO ACTIVO ⛔ (%s)" % ", ".join(activos) if activos else "sin código rojo (sistema libre)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
