#!/usr/bin/env python3
"""tools/memoria_contradicciones.py — memorias que se contradicen o se han quedado viejas. SOLO INFORMA.

POR QUÉ (26-sep-26). La «capa 5» de la memoria (olvidar lo que ya no vale) no existía: una memoria
vieja y su corrección convivían y el recall traía la versión vieja. `verificacion` encontró 4 casos
reales, uno clínico, y la clase dominante no era «dos memorias opuestas» sino «una memoria vieja por
dentro»: el principio (lo que inyecta `memoria_radar.ficha`) decía una cosa y más abajo se corregía.
Idea de partida: el PDF «Agent Memory — The 5-Layer Playbook» (síntesis independiente, no de
Anthropic). Comité: verificacion + consejero-arneses (opción B, léxico y sin vectores: el brazo
semántico ya se midió el 25-jul y no mejoró el recall).

QUÉ HACE: lee las memorias y lista CANDIDATAS por cuatro reglas. No edita nada: la memoria es
superficie de ataque y «clasificar no es borrar»; lo corrige {{TITULAR}} o el comité.
  R1 corrección fuera de la ficha: más abajo hay «ya no / REDEFINIÓ / DESCARTADA / ACTUALIZACIÓN…»
     y lo que inyecta el recall no lo avisa.
  R2 el nombre dice lo contrario: el slug lleva «no-X / sin-X / nunca-X» y el cuerpo dice que X sí.
  R3 afirma lo que dice OTRA memoria («`y` sigue diciendo…»): caduca en cuanto la otra se corrige.
  R4 cita como vigente una memoria DESCARTADA, sin decir que lo está.

Uso:  python3 tools/memoria_contradicciones.py [--json]
Local, stdlib, sin red. `BTP_MEMORY_DIR` apunta a otra carpeta (tests).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memoria_radar as MR  # noqa: E402

# Solo MARCADORES DE ESTADO escritos como tales (mayúsculas o fórmula de derogación). Un «ya no» o un
# «descartado» en prosa corriente («ya no es un secreto», «vías descartadas») no es corregir la
# memoria: la primera versión, con re.I y verbos sueltos, dio 41 candidatas de 404, casi todas ruido.
_REEMPLAZO = re.compile(
    r"REDEFINI[OÓ]|SUPERSEDED|\bya\s+NO\b|(?:^|[.:*]\s*)Retiro\b|CORRECCI[OÓ]N\b|DESCARTAD[AO]\b|"
    r"(?i:queda\s+anulad|deja\s+de\s+valer|sustituye\s+a\b|ya\s+no\s+vale\b)", re.M)
# Si lo que ya inyecta el recall avisa de la corrección, está atendida.
_ATENDIDA = re.compile(r"vigente|hist[oó]ric|es historia|estado actual|CORREGID|DESCARTAD|🪦|"
                       r"SUPERSEDED|REDEFINI|ya\s+no\b", re.I)
_NEGACION_SLUG = re.compile(r"(?:^|-)(?:no|sin|nunca)-([a-z0-9]+)")
_DICE_OTRA = re.compile(
    r"(?:`([a-z][a-z0-9-]{3,})`|\[\[([a-z][a-z0-9-]{3,})\]\])[^.\n]{0,40}?"
    r"\b(sigue diciendo|todav[ií]a dice|a[uú]n dice|no nombra|sigue sin)\b", re.I)


def _linea_de(body, pos):
    ini = body.rfind("\n", 0, pos) + 1
    fin = body.find("\n", pos)
    return re.sub(r"\s+", " ", body[ini:fin if fin != -1 else None]).strip()[:160]


def revisar(corpus=None):
    """Lista de hallazgos {regla, slug, detalle, cita}. Nunca lanza por una memoria rota."""
    corpus = corpus if corpus is not None else MR._corpus()
    por_slug = {d["slug"]: d for d in corpus}
    out = []
    for d in corpus:
        try:
            body, slug = d["body"], d["slug"]
            f = MR.ficha(d)
            # R1 · corrección por debajo de lo que el recall inyecta
            if not _ATENDIDA.search(f):
                corte = body.find(f[:40]) if f[:40] in body else 0
                visto = len(f) + corte
                m = _REEMPLAZO.search(body, visto)
                if m:
                    out.append({"regla": "R1", "slug": slug,
                                "detalle": "hay una corrección más abajo que el recall no enseña",
                                "cita": _linea_de(body, m.start())})
            # R2 · el nombre dice lo contrario que el cuerpo
            for w in _NEGACION_SLUG.findall(slug):
                if len(w) < 4 or re.search(r"nombre[^.]{0,60}hist[oó]ric", f, re.I):
                    continue
                m = re.search(r"\bretiro\b[^.\n]{0,80}|\b%s\b[^.\n]{0,30}\bs[ií]\b\s+(es|se|va)" % re.escape(w),
                              body, re.I)
                if m:
                    out.append({"regla": "R2", "slug": slug,
                                "detalle": "el nombre dice «no-%s» y el cuerpo lo matiza o lo retira" % w,
                                "cita": _linea_de(body, m.start())})
            # R3 · afirma lo que dice otra memoria
            for m in _DICE_OTRA.finditer(body):
                otra = m.group(1) or m.group(2)
                if otra in por_slug and otra != slug:
                    out.append({"regla": "R3", "slug": slug,
                                "detalle": "afirma lo que dice [[%s]]; caduca si esa se corrige" % otra,
                                "cita": _linea_de(body, m.start())})
            # R4 · cita como vigente una memoria descartada
            for otra in sorted(d.get("enlaces") or ()):
                o = por_slug.get(otra)
                if not o or otra == slug or not re.search(r"DESCARTAD|🪦|SUPERSEDED", MR.ficha(o)):
                    continue
                for m in re.finditer(r"\[\[%s\]\]" % re.escape(otra), body):
                    entorno = body[max(0, m.start() - 150):m.end() + 150]
                    if not re.search(r"descartad|hist[oó]ric|ya no|🪦|superseded|retirad", entorno, re.I):
                        out.append({"regla": "R4", "slug": slug,
                                    "detalle": "cita [[%s]], que está DESCARTADA, sin decirlo" % otra,
                                    "cita": _linea_de(body, m.start())})
                        break
        except Exception as e:                       # una memoria rota no tumba el informe
            out.append({"regla": "ERROR", "slug": d.get("slug", "?"), "detalle": repr(e), "cita": ""})
    return out


def main(argv):
    hallazgos = revisar()
    if "--json" in argv:
        print(json.dumps(hallazgos, ensure_ascii=False, indent=1))
        return 0
    n = len(MR._corpus())
    print("Memorias revisadas: %d · candidatas: %d (SOLO informe: nada se edita)" % (n, len(hallazgos)))
    for regla in ("R1", "R2", "R3", "R4", "ERROR"):
        grupo = [h for h in hallazgos if h["regla"] == regla]
        if grupo:
            print("\n%s (%d)" % (regla, len(grupo)))
            for h in grupo:
                print("  · %s — %s\n      «%s»" % (h["slug"], h["detalle"], h["cita"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
