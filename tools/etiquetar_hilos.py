#!/usr/bin/env python3
"""tools/etiquetar_hilos.py — que ningún hilo abierto se quede sin dueño, sin reloj y sin NED.

POR QUÉ EXISTE (30-jul-2026). El KPI-2 de `kpi_ned.py` mide la capa de vigilancia y salió parado:
de 133 hilos abiertos, **98 no declaran `objetivo_ned`** y 111 no tienen dueño. Un hilo sin las tres
cosas —quién lo lleva, qué es lo siguiente y contra qué reloj— no lo persigue nadie; existe en el
Tablero y no en la realidad. Y en esta casa el error caro no es equivocarse, es no enterarse.

Al mirarlos de cerca salió algo que el número solo no dice: **una parte de esos hilos no son hilos**.
Son fragmentos del habla de {{TITULAR}} que la captura verbal volcó como tareas («pues ponme tarea de
buscar mañana tienes lista de labs no?»). `triage_tareas.py` ya filtra eso EN LA PUERTA desde el
22-jun, pero nadie había pasado por lo que entró antes. Etiquetarlos subiría el porcentaje sin
mejorar la vigilancia ni un gramo — maquillar la métrica en vez de mover la realidad. Por eso este
barrido **clasifica antes de etiquetar**.

CÓMO FUNCIONA (dos carriles, como manda la casa):
  · Lo DETERMINISTA lo hace la tool: qué le falta a cada hilo, qué comité le toca por su categoría,
    y qué huele a ruido de captura (heurística conservadora y explicable, sin LLM).
  · El JUICIO lo pone quien revisa: el `objetivo_ned` de cada hilo y el veredicto final. La tool
    **PROPONE y no toca nada** hasta que alguien pasa el fichero revisado por `aplicar --si`.

Muro: solo escribe en `seguimiento.json` (local). No envía, no publica, no contacta. Cerrar un hilo
es reversible (`seguimiento.reabrir_tarea`). NUNCA propone cerrar nada clínico, legal ni privado:
ahí el coste de equivocarse no es simétrico.

Uso:
  python3 tools/etiquetar_hilos.py revisar [--json]        # qué falta y qué se propone
  python3 tools/etiquetar_hilos.py plantilla > cambios.json # esqueleto para rellenar el juicio
  python3 tools/etiquetar_hilos.py aplicar --fichero cambios.json        # ENSAYO, no escribe
  python3 tools/etiquetar_hilos.py aplicar --fichero cambios.json --si   # escribe de verdad
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Casa base: el Tablero es estado vivo y no está versionado (desde un worktree no existe).
CASA = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")

CERRADOS = ("hecho", "cerrado", "descartado")
RELOJ = ("plazo", "gate", "quien_espera")

# Categoría → comité que lo POSEE (registro de comités). Lo que no está aquí se deja en blanco
# a propósito: mejor un hueco visible que un dueño inventado.
# `personal` → `cuidado-integral` no es relleno: ese comité llevaba 40 días sin que nadie lo
# llamara, y no porque sobre, sino porque nada lo disparaba. Esto lo cablea.
DUENO_POR_CATEGORIA = {
    "clinico": "oncologo-virtual",
    "clinico-ensayo": "oncologo-virtual",
    "clinico-verificacion": "verificacion",
    "legal": "legal-burocracia",
    "legal-financiero": "legal-burocracia",
    "financiero": "finanzas-transparencia",
    "finanzas": "finanzas-transparencia",
    "compra": "finanzas-transparencia",
    "prensa": "prensa",
    "comunicacion": "comunidad",
    "redes": "redes-contenido",
    "contactos-red": "investigador",
    "contacto-entrante": "investigador",
    "web-tecnico": "tecnico",
    "infra": "tecnico",
    "sistema": "tecnico",
    "seguridad-infra": "tecnico",
    "marca": "diseno",
    "marca-verificacion": "diseno",
    "personal": "cuidado-integral",
    "acceso": "consejero-acceso",
}

# Categorías donde NUNCA se propone cerrar: el coste de equivocarse no es simétrico.
INTOCABLES = ("clinico", "clinico-ensayo", "clinico-verificacion", "legal", "legal-financiero")

# Señales de que un "hilo" es en realidad un trozo de conversación capturado. Conservadoras y
# explicables: la tool dice CUÁL disparó, para que se pueda discutir el criterio y no la corazonada.
ARRANQUES_DE_HABLA = (
    "pues ", "si y ", "sí y ", "a ver ", "entonces ", "y también ", "y tambien ",
    "lo que ", "lo queq", "hecho,", "quiero ", "necesito ", "necesigo ", "mandame ",
    "mándame ", "manda ", "deberia ", "debería ", "sigue dando ", "algo mas ", "algo más ",
    "saber como ", "saber cómo ",
)


def _seguimiento_path():
    return os.path.join(CASA, "tools", "state", "seguimiento.json")


def _cargar():
    with open(_seguimiento_path(), encoding="utf-8") as f:
        return json.load(f)


def abiertos(seg=None):
    seg = seg or _cargar()
    return [h for h in seg.get("hilos", []) if (h.get("estado") or "") not in CERRADOS]


def _senales_de_captura(h):
    """Por qué este hilo huele a fragmento de habla. Lista vacía = no huele."""
    if (h.get("categoria") or "") not in ("otros", ""):
        return []
    if (h.get("estado") or "") != "por_confirmar":
        return []
    t = (h.get("titulo") or "").strip()
    if not t:
        return []
    señales = []
    if t[0].islower():
        señales.append("empieza en minúscula")
    if "?" in t:
        señales.append("es una pregunta")
    bajo = t.lower()
    if any(bajo.startswith(a) for a in ARRANQUES_DE_HABLA):
        señales.append("arranca como una frase hablada")
    return señales


def diagnostico(h):
    falta = []
    if not h.get("objetivo_ned"):
        falta.append("objetivo_ned")
    if not h.get("dueno"):
        falta.append("dueno")
    if not any(h.get(k) for k in RELOJ):
        falta.append("reloj")
    señales = _senales_de_captura(h)
    return {
        "id": h.get("id"),
        "titulo": (h.get("titulo") or "")[:90],
        "categoria": h.get("categoria") or "",
        "estado": h.get("estado") or "",
        "privado": bool(h.get("privado")),
        "falta": falta,
        "dueno_propuesto": DUENO_POR_CATEGORIA.get(h.get("categoria") or ""),
        "señales_de_captura": señales,
        # Sugerencia, NUNCA decisión: 'revisar-si-cerrar' es una pregunta, no un veredicto.
        "sugerencia": ("revisar-si-cerrar"
                       if señales and (h.get("categoria") or "") not in INTOCABLES
                       and not h.get("privado") else "etiquetar"),
    }


def revisar(seg=None):
    ds = [diagnostico(h) for h in abiertos(seg)]
    return {
        "abiertos": len(ds),
        "completos": len([d for d in ds if not d["falta"]]),
        "sin_objetivo_ned": len([d for d in ds if "objetivo_ned" in d["falta"]]),
        "sin_dueno": len([d for d in ds if "dueno" in d["falta"]]),
        "sin_reloj": len([d for d in ds if "reloj" in d["falta"]]),
        "a_revisar_si_cerrar": len([d for d in ds if d["sugerencia"] == "revisar-si-cerrar"]),
        "con_dueno_proponible": len([d for d in ds
                                     if "dueno" in d["falta"] and d["dueno_propuesto"]]),
        "hilos": ds,
    }


def plantilla(seg=None):
    """Esqueleto para rellenar el JUICIO. Lo determinista viene puesto; lo demás, en blanco."""
    out = []
    for d in revisar(seg)["hilos"]:
        if not d["falta"]:
            continue
        out.append({
            "id": d["id"],
            "_titulo": d["titulo"],
            "_categoria": d["categoria"],
            "_falta": d["falta"],
            "_señales_de_captura": d["señales_de_captura"],
            "veredicto": d["sugerencia"],          # etiquetar | cerrar | dejar
            "objetivo_ned": "",                    # ← el juicio: cómo acerca a NED (o por qué no)
            "dueno": d["dueno_propuesto"] or "",
            "motivo_cierre": "",                   # obligatorio si veredicto == cerrar
        })
    return {"_doc": "Revisa 'veredicto' y rellena 'objetivo_ned'. Aplica con "
                    "`etiquetar_hilos.py aplicar --fichero <este> --si`.",
            "cambios": out}


def aplicar(cambios, escribir=False):
    """Aplica el fichero revisado. Sin `escribir`, es un ENSAYO: dice qué haría y no toca nada."""
    import _lock
    import seguimiento as sg

    plan, problemas = [], []
    for c in cambios.get("cambios", []):
        v = c.get("veredicto")
        if v == "dejar":
            continue
        if v == "cerrar" and not (c.get("motivo_cierre") or "").strip():
            problemas.append("%s: cerrar sin motivo escrito" % c.get("id"))
            continue
        if v == "etiquetar" and not (c.get("objetivo_ned") or "").strip():
            problemas.append("%s: etiquetar sin objetivo_ned" % c.get("id"))
            continue
        if v not in ("etiquetar", "cerrar"):
            problemas.append("%s: veredicto desconocido %r" % (c.get("id"), v))
            continue
        plan.append(c)
    if problemas:
        # Fail-closed: media aplicación deja el Tablero peor que como estaba.
        return {"aplicados": 0, "problemas": problemas, "plan": len(plan)}

    if not escribir:
        return {"ensayo": True, "haria": len(plan), "problemas": []}

    hechos = 0
    with _lock.lock("seguimiento", timeout=30):
        seg = _cargar()
        por_id = {h.get("id"): h for h in seg.get("hilos", [])}
        for c in plan:
            h = por_id.get(c["id"])
            if h is None:
                continue
            if c["veredicto"] == "cerrar":
                h["estado"] = "descartado"
                h["motivo_cierre"] = c["motivo_cierre"]
            else:
                h["objetivo_ned"] = c["objetivo_ned"].strip()
                if c.get("dueno") and not h.get("dueno"):
                    h["dueno"] = c["dueno"].strip()
                # Renombrar es parte de hacer accionable un hilo: varios entraron por la captura
                # verbal con el título tal cual lo dijo {{TITULAR}} («deberia mandar mail a lab para
                # los cores»). El texto original NO se pierde, se guarda en `titulo_original`.
                nuevo = (c.get("titulo") or "").strip()
                if nuevo and nuevo != h.get("titulo"):
                    h.setdefault("titulo_original", h.get("titulo"))
                    h["titulo"] = nuevo
            hechos += 1
        from datetime import datetime
        seg["actualizado"] = datetime.now().strftime("%Y-%m-%d")
        # Se escribe en la MISMA ruta de la que se leyó. Usar `sg.SEG` sería leer de un sitio y
        # escribir en otro en cuanto `BTP_REPO` no coincidiera: la forma más silenciosa de perder
        # el Tablero entero.
        sg._write_atomic(_seguimiento_path(), seg)
    return {"aplicados": hechos, "problemas": []}


def _print_revision(r):
    print("🎯 Hilos abiertos: %d · completos: %d" % (r["abiertos"], r["completos"]))
    print("   les falta → objetivo_ned: %d · dueño: %d · reloj: %d"
          % (r["sin_objetivo_ned"], r["sin_dueno"], r["sin_reloj"]))
    print("   dueño proponible por categoría: %d de %d"
          % (r["con_dueno_proponible"], r["sin_dueno"]))
    print("   🗣️  huelen a fragmento de habla capturado: %d (a revisar, NO a cerrar solos)"
          % r["a_revisar_si_cerrar"])
    print()
    for d in r["hilos"]:
        if not d["falta"]:
            continue
        marca = "🗣️" if d["sugerencia"] == "revisar-si-cerrar" else "  "
        print("%s %-34s [%-18s] falta: %-28s %s"
              % (marca, (d["id"] or "")[:34], d["categoria"][:18], ",".join(d["falta"]),
                 d["titulo"][:60]))


def main(argv):
    cmd = argv[0] if argv else "revisar"
    if cmd == "revisar":
        r = revisar()
        print(json.dumps(r, ensure_ascii=False, indent=2) if "--json" in argv
              else "", end="")
        if "--json" not in argv:
            _print_revision(r)
        return 0
    if cmd == "plantilla":
        print(json.dumps(plantilla(), ensure_ascii=False, indent=2))
        return 0
    if cmd == "aplicar":
        if "--fichero" not in argv:
            print("falta --fichero <ruta>")
            return 2
        ruta = argv[argv.index("--fichero") + 1]
        with open(ruta, encoding="utf-8") as f:
            cambios = json.load(f)
        r = aplicar(cambios, escribir="--si" in argv)
        if r.get("problemas"):
            print("❌ NO se aplicó nada (%d problema/s):" % len(r["problemas"]))
            for p in r["problemas"]:
                print("   · %s" % p)
            return 1
        if r.get("ensayo"):
            print("ensayo: aplicaría %d cambio(s). Repite con --si para escribir." % r["haria"])
        else:
            print("✓ aplicados %d cambio(s) al Tablero" % r["aplicados"])
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
