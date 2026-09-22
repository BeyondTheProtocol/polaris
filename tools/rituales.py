#!/usr/bin/env python3
"""tools/rituales.py — rituales semanales (patrón robado del "AI Chief of Staff" de Notion: la
CADENCIA, nunca la plataforma). LOCAL, $0, sin LLM, solo-lectura sobre seguimiento.json. Entrega por
el canal GRATIS (salida.report_to_titular). NUNCA escribe memoria, NUNCA contacta a nadie (muro).

  living-context (lunes)  : foto del "estado de tu mundo" para arrancar la semana con contexto.
  org-wrap       (viernes): cabos sueltos ABIERTOS que conviene cerrar antes del finde.

Ambos reúsan el renderizado wall-safe de seguimiento: por Telegram redacta nombres de tercero
(_redactar_personas) y oculta hilos privados (_oculto_en_telegram). El "estado de tu mundo" es para
LEER y revisar — deliberadamente NO auto-escribe memoria (la memoria auto-escrita se supervisa).

Uso:
  python3 tools/rituales.py living-context [--dry]   # lunes
  python3 tools/rituales.py org-wrap       [--dry]   # viernes
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seguimiento as sg  # noqa: E402 — motor determinista + render wall-safe


def _visibles(canal="telegram"):
    data = sg.recopilar()
    items = [i for i in data["items"]
             if not (canal == "telegram" and sg._oculto_en_telegram(i))]
    return data, items


def org_wrap(canal="telegram"):
    """Cabos sueltos: lo abierto que espera a terceros, está bloqueado o tiene plazo — para cerrar
    o delegar antes del finde. Determinista; el orden es por severidad (nunca por prosa)."""
    _data, items = _visibles(canal)
    abiertos = [i for i in items
                if str(i.get("estado", "")).lower() != "hecho"
                and (i.get("quien_espera") or i.get("plazo")
                     or str(i.get("estado", "")).lower() == "bloqueado"
                     or i.get("_sev") in ("roja", "ambar", "amarilla"))]
    abiertos.sort(key=lambda i: (sg.SEV_ORDEN.get(i.get("_sev", "info"), 3), not i.get("es_cuello")))
    lineas = ["🗓️ CIERRE DE SEMANA — cabos sueltos antes del finde"]
    if not abiertos:
        lineas.append("✅ Nada abierto pendiente de terceros ni con plazo. Semana limpia.")
    for i in abiertos[:15]:
        cuello = " ⭐NED" if i.get("es_cuello") else ""
        quien = (" · espera: " + i["quien_espera"]) if i.get("quien_espera") else ""
        plazo = (" · plazo " + i["plazo"]) if i.get("plazo") else ""
        lineas.append("• %s%s%s%s" % (i.get("titulo", "?"), cuello, plazo, quien))
    lineas.append("— Mira cuáles puedes cerrar o delegar hoy. (Ritual de viernes, local $0.)")
    txt = "\n".join(lineas)
    return sg._redactar_personas(txt) if canal == "telegram" else txt


def living_context(canal="telegram"):
    """Foto del 'estado de tu mundo': el cuello hacia NED + frentes abiertos por severidad + lo que
    espera a terceros. Para arrancar la semana con contexto. NO escribe memoria (es para leer/revisar)."""
    data, items = _visibles(canal)
    cuello = (data.get("cumbre") or {}).get("aqui_estamos") or "?"
    activos = [i for i in items if i.get("_sev") != "info"]
    por_sev = {s: sum(1 for i in activos if i.get("_sev") == s) for s in ("roja", "ambar", "amarilla")}
    esperando = [i for i in items if i.get("quien_espera")]
    lineas = [
        "🧭 ESTADO DE TU MUNDO — arranque de semana",
        "⭐ Cuello hacia NED ahora: %s" % cuello,
        "📊 Frentes abiertos: %d 🔴 · %d 🟠 · %d 🟡" % (por_sev["roja"], por_sev["ambar"], por_sev["amarilla"]),
    ]
    if esperando:
        lineas.append("⏳ Esperando a terceros (%d):" % len(esperando))
        for i in esperando[:8]:
            lineas.append("   • %s · %s" % (i.get("titulo", "?"), i.get("quien_espera", "?")))
    lineas.append("— Ritual de lunes (local $0). Es para LEER y revisar; no toco tu memoria sola.")
    txt = "\n".join(lineas)
    return sg._redactar_personas(txt) if canal == "telegram" else txt


def main(argv):
    cmd = argv[0] if argv else ""
    dry = "--dry" in argv
    if cmd not in ("org-wrap", "living-context"):
        print("uso: rituales.py [living-context|org-wrap] [--dry]", file=sys.stderr)
        return 2
    txt = org_wrap() if cmd == "org-wrap" else living_context()
    if dry:
        print(txt)
        return 0
    try:
        import salida
        salida.report_to_titular(txt)
        print("[enviado] " + cmd)
    except Exception as e:
        print("[rituales] no se pudo enviar: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
