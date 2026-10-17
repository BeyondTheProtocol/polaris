#!/usr/bin/env python3
"""web_mapa.py — el mapa de lo que helptitular.com AFIRMA y puede caducar.

POR QUÉ EXISTE (17-oct-2026, plan «web al día», `docs/plan-web-al-dia.md` §2, aprobado por
{{TITULAR}}). Ese día llegaron los primeros resultados del single-cell y /ciencia siguió diciendo
«Material en el laboratorio» hasta que alguien lo corrigió a mano (PR #281). Nadie podía saberlo:
los estados de `science.yml` están escritos a mano y no apuntan a ninguna fuente interna. Una web
que dice «en marcha» cuando ya hay resultados es una mentira pública.

QUÉ ES
  `tools/web_afirmaciones.json`, versionado: una fila por cada afirmación pública que puede
  caducar (hoy, los `status` de las pruebas en /ciencia), con su valor publicado en ES y EN y la
  fuente interna que la respalda. Este módulo lo lee y comprueba dos cosas contra la web PUBLICADA
  (`origin/main`, no el checkout, que suele estar en otra rama):
    · **cobertura** — toda afirmación de la web tiene fila. Una prueba nueva en /ciencia sin fila
      se quedaría sin vigilar, que es justo lo que pasó con el single-cell;
    · **deriva** — el valor publicado coincide con el del mapa. Si alguien cambia la web a mano,
      el mapa tiene que seguirlo, o el detector compararía contra un estado que ya no existe.

QUIÉN LO USA
  `web_al_dia.py` (detecta que la fuente de una afirmación cambió) y `web_redactor.py` (escribe el
  estado nuevo con la frase de `transiciones`, que aprobaron `voz-titular` y `diseno` el 17-oct-26).
  Un destino sin frase aprobada no se propone.

Uso:
  python3 tools/web_mapa.py comprobar      # cobertura + deriva contra la web publicada (rc=1 si falla)
  python3 tools/web_mapa.py lista          # qué afirma la web y de qué fuente cuelga cada cosa
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAPA = os.path.join(HERE, "web_afirmaciones.json")
WEB = os.environ.get("BTP_WEB_REPO", "/Users/polaris/projects/titular-{{APELLIDO}}-case")
IDIOMAS = ("es", "en")
CIENCIA = "content/{idioma}/science.yml"


def cargar(ruta=MAPA):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def _limpia(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def estados_ciencia(texto):
    """{componente: {"status": …, "statusTone": …}} de un `science.yml`, sin parser de YAML (el
    repo no lleva dependencias). Solo mira `- component:` y sus `status`/`statusTone`."""
    out, comp = {}, None
    for linea in (texto or "").splitlines():
        m = re.match(r"\s*- component:\s*(.+)", linea)
        if m:
            comp = _limpia(m.group(1))
            continue
        m = re.match(r"\s+(status|statusTone):\s*(.+)", linea)
        if m and comp:
            out.setdefault(comp, {})[m.group(1)] = _limpia(m.group(2))
    return {c: v for c, v in out.items() if "status" in v}


def publicado(rel, repo=WEB):
    """El fichero tal y como está en la web publicada, o None si no se puede leer."""
    try:
        r = subprocess.run(["git", "-C", repo, "show", "origin/main:" + rel],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def comprobar(mapa, textos):
    """[motivo] — vacío si el mapa cubre la web y coincide con ella.

    `textos` = {idioma: contenido de science.yml}. Un idioma ilegible es un fallo, no un pase:
    un comprobador mudo se lee como «todo bien»."""
    fallos = []
    filas = [a for a in mapa.get("afirmaciones") or [] if a.get("pagina") == "ciencia"]
    for idioma in IDIOMAS:
        texto = textos.get(idioma)
        if texto is None:
            fallos.append("no pude leer %s" % CIENCIA.format(idioma=idioma))
            continue
        web = estados_ciencia(texto)
        en_mapa = {}
        for a in filas:
            comp = (a.get("componente") or {}).get(idioma)
            if not comp:
                fallos.append("%s: le falta el componente en %s" % (a.get("id"), idioma))
                continue
            en_mapa[comp] = a
        for comp, v in sorted(web.items()):
            a = en_mapa.get(comp)
            if not a:
                fallos.append("[%s] «%s» está en la web y no en el mapa: nadie vigila si caduca"
                              % (idioma, comp))
                continue
            esperado = (a.get("valor") or {}).get(idioma)
            if v.get("status") != esperado:
                fallos.append("[%s] «%s»: la web dice «%s» y el mapa «%s». Si se cambió a mano, "
                              "pon el mapa al día" % (idioma, comp, v.get("status"), esperado))
            if a.get("tono") and v.get("statusTone") != a["tono"]:
                fallos.append("[%s] «%s»: tono «%s» en la web, «%s» en el mapa"
                              % (idioma, comp, v.get("statusTone"), a["tono"]))
        for comp in sorted(set(en_mapa) - set(web)):
            fallos.append("[%s] «%s» está en el mapa y ya no en la web" % (idioma, comp))
    return fallos


def textos_publicados(repo=WEB):
    return {i: publicado(CIENCIA.format(idioma=i), repo) for i in IDIOMAS}


def main(argv):
    orden = argv[0] if argv else "comprobar"
    mapa = cargar()
    if orden == "lista":
        for a in mapa.get("afirmaciones") or []:
            f = a.get("fuente") or {}
            print("· %-30s %-36s ← %s" % (a.get("id"), (a.get("valor") or {}).get("es"),
                                           f.get("hilo") or f.get("tipo") or "sin fuente atada"))
        return 0
    if orden == "comprobar":
        fallos = comprobar(mapa, textos_publicados())
        if not fallos:
            print("el mapa cubre /ciencia y coincide con la web publicada (%d afirmaciones)."
                  % len(mapa.get("afirmaciones") or []))
            return 0
        print("🟡 el mapa y la web no cuadran:")
        for f in fallos:
            print("  · " + f)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
