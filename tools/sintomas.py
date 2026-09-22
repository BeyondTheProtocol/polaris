#!/usr/bin/env python3
"""tools/sintomas.py — el diario de síntomas de {{TITULAR}}.

Un SITIO simple donde apuntar cómo se encuentra: qué nota, con qué intensidad, cuándo.
Datado y ordenado = justo lo que su equipo médico quiere ver, y le quita a ella la carga
de reconstruir "¿cuándo empezó esto?".

Filosofía:
  · REGISTRO de apoyo, NO consejo médico. Apunta y ordena; NO interpreta ni diagnostica.
    Si un síntoma cae en una lista corta y conservadora de "conviene contarlo pronto",
    se MARCA (bandera) para que lo lleve a sus médicos — nada más: ni alarma automática
    ni nada hacia fuera.
  · "Rápido, ampliable": lo mínimo = qué + intensidad (0-10). Lo demás (zona, desde cuándo,
    notas) es opcional, cuando tenga energía.
  · LOCAL y privado (muro): vive en _PRIVADO_CLINICO/ (gitignored), egress CERO. Sin red,
    sin LLM, stdlib pura. Esta tool NUNCA envía, publica ni contacta.
  · Estado vivo → CASA BASE (BTP_REPO o ~/claudecode), no el worktree, para que lo escrito
    desde cualquier sesión vaya al MISMO diario.

Uso:
  python3 tools/sintomas.py add "dolor de cabeza" --int 6 --zona "sien derecha" --notas "desde la siesta"
  python3 tools/sintomas.py add "cansancio" --int 4
  python3 tools/sintomas.py list [--dias 14]
  python3 tools/sintomas.py informe [--dias 30]   # resumen agrupado, listo para la consulta
  python3 tools/sintomas.py md                     # ruta del diario legible
"""
import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime, timedelta

# El diario vive en CASA BASE (gitignored), nunca en el worktree.
REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
DIR = os.path.join(REPO, "00_FUENTE-DE-VERDAD", "01 · Tratamiento", "_PRIVADO_CLINICO", "sintomas")
DIARIO = os.path.join(DIR, "diario.jsonl")        # fuente de verdad, append-only
LEGIBLE = os.path.join(DIR, "DIARIO-SINTOMAS.md")  # espejo humano (se regenera en cada apunte)

# Lista CORTA y conservadora de cosas que conviene que su equipo sepa PRONTO. No es un
# diagnóstico ni una urgencia automática: solo una bandera para no dejarlo pasar. Ante
# cualquier duda real, el criterio es de sus médicos (y, si algo es agudo, urgencias).
ALARMA = [
    "ahogo", "falta de aire", "me ahogo", "disnea", "no puedo respirar", "respirar",
    "dolor en el pecho", "dolor toracico", "opresion en el pecho",
    "dolor de cabeza intenso", "peor dolor de cabeza", "cefalea intensa",
    "vision doble", "perdida de vision", "veo borroso de repente",
    "convulsion", "ataque",
    "debilidad de un lado", "no muevo", "se me cae la cara", "no hablo bien", "habla pastosa",
    "entumecimiento de repente", "hormigueo de un lado",
    "fiebre alta", "fiebre con escalofrios", "tiritona",  # reservorio → riesgo de infección
    "sangrado", "sangre", "vomito sangre", "sangre en heces",
    "desmayo", "me desmaye", "perdida de conciencia",
    "dolor oseo nuevo", "dolor de huesos nuevo", "dolor de espalda intenso",
]


def _norm(s):
    """minúsculas sin tildes, para casar la lista de banderas sin depender de acentos."""
    s = (s or "").lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def es_alarma(*textos):
    """True si algo de lo escrito casa la lista conservadora de 'cuéntalo pronto'."""
    blob = _norm(" ".join(t for t in textos if t))
    return any(p in blob for p in ALARMA)


def _leer():
    if not os.path.exists(DIARIO):
        return []
    out = []
    with open(DIARIO, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001 — una línea corrupta no tumba el diario
                continue
    return out


def _nuevo_id(existentes, ahora):
    base = ahora.strftime("%Y%m%d-%H%M%S")
    ids = {e.get("id") for e in existentes}
    if base not in ids:
        return base
    n = 2
    while "%s-%d" % (base, n) in ids:
        n += 1
    return "%s-%d" % (base, n)


def add(sintoma, intensidad=None, zona=None, desde=None, notas=None, via="cli"):
    """Apunta UN síntoma. Devuelve la entrada (dict). Lo mínimo = sintoma; el resto opcional."""
    sintoma = (sintoma or "").strip()
    if not sintoma:
        raise ValueError("hace falta decir qué síntoma")
    if intensidad is not None:
        intensidad = int(intensidad)
        if not 0 <= intensidad <= 10:
            raise ValueError("la intensidad va de 0 a 10")
    ahora = datetime.now()
    existentes = _leer()
    entrada = {
        "id": _nuevo_id(existentes, ahora),
        "ts": ahora.isoformat(timespec="seconds"),
        "fecha": ahora.strftime("%Y-%m-%d"),
        "hora": ahora.strftime("%H:%M"),
        "sintoma": sintoma,
        "intensidad": intensidad,
        "zona": (zona or "").strip() or None,
        "desde": (desde or "").strip() or None,
        "notas": (notas or "").strip() or None,
        "alarma": es_alarma(sintoma, notas, zona),
        "via": via,
    }
    os.makedirs(DIR, exist_ok=True)
    with open(DIARIO, "a", encoding="utf-8") as f:
        f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    _regenerar_legible(existentes + [entrada])
    return entrada


def actualizar(entry_id=None, *, intensidad=None, zona=None, desde=None, notas=None, sintoma=None):
    """Corrige/añade campos a una entrada ya apuntada (la MÁS RECIENTE si no se da id).
    Para el flujo natural "apunto el síntoma y luego digo la intensidad"."""
    ent = _leer()
    if not ent:
        raise ValueError("no hay nada que actualizar")
    if entry_id:
        idx = next((i for i, e in enumerate(ent) if e.get("id") == entry_id), None)
        if idx is None:
            raise ValueError("no encuentro la entrada %s" % entry_id)
    else:
        idx = max(range(len(ent)), key=lambda i: ent[i].get("ts", ""))
    e = ent[idx]
    for k, v in (("sintoma", sintoma), ("zona", zona), ("desde", desde), ("notas", notas)):
        if v is not None:
            e[k] = str(v).strip() or None
    if intensidad is not None:
        intensidad = int(intensidad)
        if not 0 <= intensidad <= 10:
            raise ValueError("la intensidad va de 0 a 10")
        e["intensidad"] = intensidad
    e["alarma"] = es_alarma(e.get("sintoma"), e.get("notas"), e.get("zona"))
    with open(DIARIO, "w", encoding="utf-8") as f:
        for x in ent:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    _regenerar_legible(ent)
    return e


def listar(dias=14):
    """Entradas de los últimos `dias`, más recientes primero."""
    corte = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
    return sorted([e for e in _leer() if e.get("fecha", "") >= corte],
                  key=lambda e: e.get("ts", ""), reverse=True)


def _barra(intensidad):
    if intensidad is None:
        return "—"
    llenas = round(intensidad)
    return "█" * llenas + "·" * (10 - llenas) + " %d/10" % intensidad


def _regenerar_legible(entradas):
    """Espejo humano cronológico (lo más nuevo arriba), agrupado por día. Fail-soft."""
    try:
        entradas = sorted(entradas, key=lambda e: e.get("ts", ""), reverse=True)
        L = ["# Diario de síntomas",
             "",
             "> Registro personal de apoyo, **no consejo médico**. Para llevar a tus consultas.",
             "> Privado y local. Lo marcado con 🚩 conviene comentarlo pronto con tu equipo.",
             ""]
        dia_actual = None
        for e in entradas:
            if e.get("fecha") != dia_actual:
                dia_actual = e.get("fecha")
                L.append("\n## %s\n" % dia_actual)
            flag = "🚩 " if e.get("alarma") else ""
            linea = "- **%s** · %s%s" % (e.get("hora", "--:--"), flag, e.get("sintoma", ""))
            if e.get("intensidad") is not None:
                linea += " · _%s_" % _barra(e["intensidad"])
            extra = []
            if e.get("zona"):
                extra.append("zona: %s" % e["zona"])
            if e.get("desde"):
                extra.append("desde: %s" % e["desde"])
            if e.get("notas"):
                extra.append(e["notas"])
            if extra:
                linea += "  \n  " + " · ".join(extra)
            L.append(linea)
        os.makedirs(DIR, exist_ok=True)
        with open(LEGIBLE, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    except Exception:  # noqa: BLE001 — el espejo legible nunca puede romper el apunte
        pass


def informe(dias=30):
    """Resumen agrupado por síntoma, listo para la consulta. Devuelve texto markdown."""
    ent = listar(dias=dias)
    if not ent:
        return "No hay síntomas apuntados en los últimos %d días." % dias
    grupos = {}
    for e in ent:
        grupos.setdefault(_norm(e["sintoma"]), []).append(e)
    L = ["# Resumen de síntomas — últimos %d días" % dias,
         "_Registro de apoyo, no consejo médico. Generado para tu consulta._", ""]
    banderas = [e for e in ent if e.get("alarma")]
    if banderas:
        L.append("> 🚩 **Para comentar pronto:** " +
                 ", ".join(sorted({e["sintoma"] for e in banderas})) + ".\n")
    for _, es in sorted(grupos.items(), key=lambda kv: -len(kv[1])):
        nombre = es[0]["sintoma"]
        ints = [e["intensidad"] for e in es if e.get("intensidad") is not None]
        veces = len(es)
        prim = min(e["fecha"] for e in es)
        ult = max(e["fecha"] for e in es)
        cab = "## %s — %d vez%s" % (nombre, veces, "" if veces == 1 else "es")
        if ints:
            cab += " · intensidad %d–%d/10 (última %d)" % (min(ints), max(ints), ints[0])
        L.append(cab)
        L.append("Del %s al %s." % (prim, ult))
        for e in es[:6]:
            det = "- %s %s" % (e["fecha"], e["hora"])
            if e.get("intensidad") is not None:
                det += " · %d/10" % e["intensidad"]
            for k in ("zona", "desde", "notas"):
                if e.get(k):
                    det += " · %s" % e[k]
            L.append(det)
        if veces > 6:
            L.append("- … y %d más" % (veces - 6))
        L.append("")
    return "\n".join(L)


def _cli():
    p = argparse.ArgumentParser(description="Diario de síntomas de {{TITULAR}} (local, de apoyo).")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="apunta un síntoma")
    a.add_argument("sintoma")
    a.add_argument("--int", "--intensidad", dest="intensidad", type=int, default=None)
    a.add_argument("--zona", default=None)
    a.add_argument("--desde", default=None)
    a.add_argument("--notas", default=None)
    a.add_argument("--via", default="cli")

    st = sub.add_parser("set", help="corrige/añade campos a una entrada (la última por defecto)")
    st.add_argument("--id", default=None)
    st.add_argument("--int", "--intensidad", dest="intensidad", type=int, default=None)
    st.add_argument("--zona", default=None)
    st.add_argument("--desde", default=None)
    st.add_argument("--notas", default=None)
    st.add_argument("--sintoma", default=None)

    li = sub.add_parser("list", help="lista los últimos días")
    li.add_argument("--dias", type=int, default=14)

    inf = sub.add_parser("informe", help="resumen agrupado para la consulta")
    inf.add_argument("--dias", type=int, default=30)

    sub.add_parser("md", help="ruta del diario legible")

    args = p.parse_args()
    if args.cmd == "add":
        e = add(args.sintoma, intensidad=args.intensidad, zona=args.zona,
                desde=args.desde, notas=args.notas, via=args.via)
        flag = "  🚩 conviene contarlo pronto a tu equipo" if e["alarma"] else ""
        print("Apuntado: %s%s%s" % (
            e["sintoma"],
            (" (%d/10)" % e["intensidad"]) if e["intensidad"] is not None else "",
            flag))
    elif args.cmd == "set":
        e = actualizar(entry_id=args.id, intensidad=args.intensidad, zona=args.zona,
                       desde=args.desde, notas=args.notas, sintoma=args.sintoma)
        flag = "  🚩 conviene contarlo pronto a tu equipo" if e["alarma"] else ""
        print("Actualizado: %s%s%s" % (
            e["sintoma"],
            (" (%d/10)" % e["intensidad"]) if e["intensidad"] is not None else "",
            flag))
    elif args.cmd == "list":
        for e in listar(dias=args.dias):
            flag = "🚩 " if e.get("alarma") else ""
            i = (" %d/10" % e["intensidad"]) if e.get("intensidad") is not None else ""
            print("%s %s  %s%s%s" % (e["fecha"], e["hora"], flag, e["sintoma"], i))
    elif args.cmd == "informe":
        print(informe(dias=args.dias))
    elif args.cmd == "md":
        print(LEGIBLE)
    else:
        p.print_help()


if __name__ == "__main__":
    sys.exit(_cli())
