#!/usr/bin/env python3
"""tools/normas.py — el REGISTRO de las normas de {{TITULAR}} y su MECANISMO.

POR QUÉ EXISTE (25-jul-2026). {{TITULAR}}: *«me dices vale, lo guardo, no volverá a pasar, pero en
realidad sí»*. Tenía razón y se puede medir: **183 memorias `feedback`** (normas suyas) y solo **18**
aparecían en código ejecutable. El resto vivía como texto que yo tenía que recordar, y el recall de
memorias es BM25 top-3/5 **al enviar ella el prompt** — así que la norma llegaba (o no) cuando ella
escribía, mientras el incumplimiento ocurre **cuando yo respondo**.

Esto no es opinión: la propia documentación de Claude Code dice que CLAUDE.md y la auto-memoria son
«context, not enforced configuration» y que si una regla debe cumplirse SIEMPRE, hay que hacerla un
hook. Y está medido por qué se degrada un recordatorio: `arXiv:2307.03172` (lo del medio del contexto
se atiende peor), `arXiv:2601.04170` (deriva de agentes) y `arXiv:2605.10481` (deriva de
restricciones), del que se toma el marco: una restricción sirve si es **fresca, heredada, aplicable
y auditable**; si no, está *afirmada* pero no *mantenida*.

QUÉ HACE: lee `tools/normas.json` (el registro, versionado) y responde tres preguntas —
  · ¿qué normas tienen mecanismo de verdad y cuáles siguen dependiendo del criterio?
  · ¿existe el mecanismo que dice tener cada una? (si no, es una regresión y se canta)
  · ¿cuántas de las 183 están cubiertas? (el número que hace VISIBLE el hueco)

Uso:
  python3 tools/normas.py list                 # tabla por clase
  python3 tools/normas.py estado               # métrica + huecos (JSON con --json)
  python3 tools/normas.py verificar            # ¿el mecanismo declarado EXISTE? exit 1 si no
  python3 tools/normas.py salida               # las reglas activas del gate (las usa el hook)
  python3 tools/normas.py modo                 # 'aviso' | 'bloqueo'

NO decide nada sobre el muro ni envía nada: es un registro de solo lectura.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("BTP_REPO") or os.path.dirname(HERE)
# `BTP_NORMAS_JSON` aísla el registro en tests (13-sep-26): probar que un check PROMOVIDO bloquea
# de verdad exige un `normas.json` de mentira, y `gate_salida.py` lo consulta desde un subproceso
# nuevo (no comparte memoria con el test) — sin este override habría que escribir sobre el
# registro real para probarlo. Sin la variable, comportamiento idéntico al de siempre.
REGISTRO = os.environ.get("BTP_NORMAS_JSON") or os.path.join(HERE, "normas.json")
# Las memorias viven fuera del repo (son del usuario, no del proyecto).
MEM_DIR = os.environ.get("BTP_MEM_DIR") or os.path.expanduser(
    "~/.claude/projects/-Users-polaris-claudecode/memory")
CLASES = ("bloqueo", "salida", "lint-doc", "contexto")
PROPIEDADES = ("fresca", "heredada", "aplicable", "auditable")


def cargar(path=None):
    with open(path or REGISTRO, encoding="utf-8") as f:
        d = json.load(f)
    normas = d.get("normas") or []
    for n in normas:
        if n.get("clase") not in CLASES:
            raise ValueError("clase desconocida en %s: %r" % (n.get("slug"), n.get("clase")))
    return d


def modo(d=None):
    """'aviso' (detecta y avisa) o 'bloqueo' (no entrega hasta arreglarlo)."""
    d = d or cargar()
    m = (d.get("_modo_gate") or "aviso").strip().lower()
    return m if m in ("aviso", "bloqueo") else "aviso"


def reglas_salida(d=None):
    """Slugs de las normas que el gate de salida vigila, con su función y su modo.

    `modo` por norma (31-jul-26): el interruptor global sirve para estrenar el gate, pero no todas
    las normas merecen el mismo trato. `convergencia` salta ~10 veces al día y bloquear a ciegas
    sería un estorbo; una cita fabricada, en cambio, no admite modo aviso — cuando ella la lee, ya
    la ha leído. Sin campo `modo`, la norma sigue el interruptor global: nada cambia por defecto.
    """
    d = d or cargar()
    out = []
    for n in d.get("normas", []):
        if n.get("clase") != "salida":
            continue
        m = re.search(r"gate_salida\.py::(\w+)", n.get("mecanismo") or "")
        if m:
            mo = (n.get("modo") or "").strip().lower()
            out.append({"slug": n["slug"], "check": m.group(1), "que": n.get("que", ""),
                        "modo": mo if mo in ("sombra", "aviso", "bloqueo") else None})
    return out


def mecanismos_duplicados(d=None):
    """Mecanismos declarados por más de una norma. Un hallazgo suyo se apuntaría dos veces."""
    d = d or cargar()
    vistos, repes = set(), []
    for n in d.get("normas", []):
        mec = n.get("mecanismo")
        if not mec:
            continue
        if mec in vistos and mec not in repes:
            repes.append(mec)
        vistos.add(mec)
    return repes


def _memorias_feedback():
    try:
        return sorted(f[:-3] for f in os.listdir(MEM_DIR)
                      if f.startswith("feedback-") and f.endswith(".md"))
    except Exception:
        return []


# Una memoria feedback recién escrita tiene este margen para entrar en el registro antes de que
# la batería se ponga ROJA. Sin él, cada memoria nueva la rompía al instante, y el coste caía en
# quien corriera la batería DESPUÉS, no en quien la escribió: cuatro veces en dos días (20-21 sep),
# siempre con memorias de otras sesiones. Dentro del margen NO es invisible: se lista en la
# batería y `indice_memoria.py` la canta al escribirla. Pasado el margen, rojo como siempre.
GRACIA_SIN_CLASIFICAR_S = 72 * 3600


def sin_clasificar(d=None, ahora=None):
    """(pendientes, vencidas): memorias feedback que NO están en el registro, separadas por si
    siguen dentro de GRACIA_SIN_CLASIFICAR_S desde que se escribieron (mtime) o ya no."""
    import time as _t
    d = d or cargar()
    slugs = {n["slug"] for n in d.get("normas", [])}
    t = ahora if ahora is not None else _t.time()
    pendientes, vencidas = [], []
    for m in _memorias_feedback():
        if m in slugs:
            continue
        try:
            edad = t - os.path.getmtime(os.path.join(MEM_DIR, m + ".md"))
        except OSError:
            edad = GRACIA_SIN_CLASIFICAR_S + 1          # si no se puede datar, no hay gracia
        (pendientes if edad < GRACIA_SIN_CLASIFICAR_S else vencidas).append(m)
    return pendientes, vencidas


def _mecanismo_existe(mec):
    """¿El fichero que dice aplicar la norma existe de verdad? (la parte antes de '::')."""
    if not mec:
        return None
    encontrado = False
    for trozo in re.split(r"\s*[+·]\s*|,\s*", mec):
        ruta = trozo.split("::")[0].strip()
        ruta = re.sub(r"^parcial:\s*", "", ruta).strip()
        if not ruta or " " in ruta and not ruta.endswith((".py", ".sh", ".json")):
            continue
        if os.path.exists(os.path.join(REPO, ruta)):
            encontrado = True
        else:
            return False
    return encontrado or None


def estado(d=None):
    d = d or cargar()
    normas = d.get("normas", [])
    por_clase = {c: [n for n in normas if n.get("clase") == c] for c in CLASES}
    # «Con mecanismo» = hay un mecanismo declarado Y existe (31-jul-26). Antes contaba
    # `aplicable`, que es otra cosa: si la norma viene al caso. Con 29 normas el error pasaba
    # desapercibido; al entrar las 169 de la Fase 0 el registro pasó a cantar «189 de 198 con
    # mecanismo» cuando solo 20 lo tienen. Eso es FALSO VERDE, justo lo que este registro existe
    # para evitar: el hueco tiene que verse.
    con_mecanismo = [n for n in normas
                     if n.get("mecanismo") and _mecanismo_existe(n.get("mecanismo")) is not False]
    total_mem = len(_memorias_feedback())
    huecos = [{"slug": n["slug"], "mecanismo": n.get("mecanismo")}
              for n in normas if _mecanismo_existe(n.get("mecanismo")) is False]
    return {
        "memorias_feedback": total_mem,
        "en_registro": len(normas),
        "con_mecanismo_aplicable": len(con_mecanismo),
        "por_clase": {c: len(v) for c, v in por_clase.items()},
        "repetidas_cubiertas": len([n for n in normas if n.get("repetida") and n in con_mecanismo]),
        "repetidas_en_registro": len([n for n in normas if n.get("repetida")]),
        "mecanismos_que_faltan": huecos,
        "modo_gate": modo(d),
    }


SNAP = os.path.join(os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state"),
                    "normas_snapshot.json")
DIAS_ESTANCADO = 30


def latido_y_meta(d=None, hoy_ts=None):
    """R3 del plan «que quede arreglado»: deja latido y comprueba que el número SE MUEVE.

    Si en `DIAS_ESTANCADO` días la cobertura no ha subido, eso ES un hallazgo (se abre en el libro de
    deuda): un contador parado significa que nadie está convirtiendo normas en mecanismos, que es
    exactamente cómo murieron los intentos anteriores. Devuelve (estancado, dias, delta).
    """
    import time
    e = estado(d)
    ahora = hoy_ts or time.time()
    prev = {}
    try:
        with open(SNAP, encoding="utf-8") as f:
            prev = json.load(f) or {}
    except Exception:
        prev = {}
    cob = e["con_mecanismo_aplicable"]
    base_ts = float(prev.get("ts") or ahora)
    base_cob = int(prev.get("cobertura", cob))
    dias = int((ahora - base_ts) / 86400)
    estancado = dias >= DIAS_ESTANCADO and cob <= base_cob
    nuevo = {"ts": ahora if (cob > base_cob or not prev) else base_ts,
             "cobertura": max(cob, base_cob) if cob > base_cob else base_cob,
             "ultimo_visto": ahora, "ultima_cobertura": cob}
    try:
        os.makedirs(os.path.dirname(SNAP), exist_ok=True)
        with open(SNAP, "w", encoding="utf-8") as f:
            json.dump(nuevo, f, ensure_ascii=False)
        hb = os.path.join(os.path.dirname(SNAP), "heartbeat", "normas.json")
        os.makedirs(os.path.dirname(hb), exist_ok=True)
        from datetime import datetime, timezone
        with open(hb, "w", encoding="utf-8") as f:
            json.dump({"agente": "normas",
                       "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "estado": "estancado" if estancado else "ok", "cobertura": cob}, f,
                      ensure_ascii=False)
    except Exception:
        pass
    if estancado:
        try:
            sys.path.insert(0, HERE)
            import deuda
            deuda.abrir("normas_cobertura_estancada",
                        "La cobertura de normas con mecanismo lleva %d días sin subir (%d): nadie "
                        "está convirtiendo normas en mecanismos." % (dias, cob), ned="medio")
        except Exception:
            pass
    return estancado, dias, cob - base_cob


def _print_list(d):
    for c in CLASES:
        ns = [n for n in d["normas"] if n.get("clase") == c]
        if not ns:
            continue
        print("\n── %s (%d) ──" % (c, len(ns)))
        for n in ns:
            props = "".join(p[0].upper() if n.get(p) else "·" for p in PROPIEDADES)
            marca = "🔁" if n.get("repetida") else "  "
            print("  %s [%s] %-52s %s" % (marca, props, n["slug"], (n.get("que") or "")[:60]))
    print("\n  Propiedades: F=fresca H=heredada A=aplicable U=auditable (arXiv:2605.10481) · 🔁 = "
          "norma que {{TITULAR}} ya tuvo que repetir")


def main(argv):
    cmd = argv[0] if argv else "estado"
    d = cargar()
    if cmd == "list":
        _print_list(d)
        return 0
    if cmd == "salida":
        for r in reglas_salida(d):
            print("%-22s %s" % (r["check"], r["slug"]))
        return 0
    if cmd == "modo":
        print(modo(d))
        return 0
    if cmd == "verificar":
        e = estado(d)
        if e["mecanismos_que_faltan"]:
            print("❌ %d norma(s) dicen tener un mecanismo que NO existe (regresión):"
                  % len(e["mecanismos_que_faltan"]))
            for h in e["mecanismos_que_faltan"]:
                print("   · %s → %s" % (h["slug"], h["mecanismo"]))
            return 1
        print("✅ todos los mecanismos declarados existen (%d normas en el registro)"
              % e["en_registro"])
        return 0
    if cmd == "latido":
        estancado, dias, delta = latido_y_meta(d)
        print("latido escrito · cobertura %s · %d día(s) desde la última subida%s"
              % (estado(d)["con_mecanismo_aplicable"], dias,
                 " → ⚠️ ESTANCADO, abierto en el libro de deuda" if estancado else ""))
        return 1 if estancado else 0
    if cmd == "estado":
        e = estado(d)
        if "--json" in argv:
            print(json.dumps(e, ensure_ascii=False, indent=2))
            return 0
        print("Normas de {{TITULAR}} con mecanismo: %d de %d en el registro "
              "(memorias `feedback` totales: %d)"
              % (e["con_mecanismo_aplicable"], e["en_registro"], e["memorias_feedback"]))
        print("  por clase: " + " · ".join("%s=%d" % (k, v) for k, v in e["por_clase"].items()))
        print("  de las que {{TITULAR}} tuvo que REPETIR: %d de %d ya tienen mecanismo"
              % (e["repetidas_cubiertas"], e["repetidas_en_registro"]))
        print("  gate de salida en modo: %s" % e["modo_gate"])
        if e["mecanismos_que_faltan"]:
            print("  ⚠️  %d mecanismo(s) declarados que NO existen → `normas.py verificar`"
                  % len(e["mecanismos_que_faltan"]))
        pend = e["memorias_feedback"] - e["en_registro"]
        if pend > 0:
            print("  quedan %d memorias `feedback` sin clasificar en el registro (hueco VISIBLE, "
                  "no invisible)" % pend)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
