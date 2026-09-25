#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lo aplazado de CUALQUIER día llega al parte, y solo se archiva si el parte llegó.

BUG REAL (detectado el 24-sep-26): el parte sale a las 08:12 y solo leía el fichero de
aplazados de HOY. El primer aviso aplazado de cada día es de las 08:18, así que caía en
un fichero que el parte de mañana ya no miraba. 855 avisos sin entregar del 27-jul al
24-sep, entre ellos el CI público en rojo (10:41 del 24-sep): {{TITULAR}} se enteró por GitHub.

Lo que fija:
  1. un aviso aplazado AYER a las 08:18 aparece en el parte de hoy (el bug, reproducido).
  2. si el envío falla, no se archiva nada (main() con entrega fallida).
  3. si se entrega, se archiva en aplazados/entregados/ y lo que se aplazó MIENTRAS se
     enviaba se queda para el siguiente parte.
  4. el atasco viejo va resumido por tipo con los plazos vivos, no aviso a aviso.
  5. un aviso de salud con varias viñetas no entierra el CI: sale como línea propia."""
import importlib
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _escribe(tmp, dia, recs):
    d = os.path.join(tmp, "aplazados")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s.jsonl" % dia), "a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _modulos(tmp):
    os.environ["BTP_STATE_DIR"] = tmp
    os.environ["BTP_TEST_BATTERY"] = "1"      # ni un mensaje real a {{TITULAR}}
    import salida
    importlib.reload(salida)
    salida.STATE = tmp
    salida.OUTBOX = os.path.join(tmp, "outbox")
    os.makedirs(salida.OUTBOX, exist_ok=True)
    sys.modules["salida"] = salida
    sys.modules.pop("enviar_hoy", None)
    import enviar_hoy
    importlib.reload(enviar_hoy)
    return salida, enviar_hoy


def main():
    hoy = date.today()
    ayer = (hoy - timedelta(days=1)).isoformat()
    viejo = (hoy - timedelta(days=5)).isoformat()
    futuro = (hoy + timedelta(days=4)).isoformat()
    pasado = (hoy - timedelta(days=2)).isoformat()
    tmp = tempfile.mkdtemp(prefix="btp_aplazados_")
    try:
        _escribe(tmp, ayer, [
            {"ts": ayer + "T08:18:05", "fuente": "", "texto": "aviso de ayer a las 08:18"},
            {"ts": ayer + "T10:41:23", "fuente": "healthcheck",
             "texto": "🩺 Revisión de salud del lazo:\n- 🔧 Detecté el daemon X caído\n"
                      "- El CI del repo público está en ROJO (5 seguidas)"},
            {"ts": ayer + "T16:05:28", "fuente": "healthcheck",
             "texto": "🩺 Revisión de salud del lazo:\n- El CI del repo público está en ROJO (30 seguidas)"},
        ])
        _escribe(tmp, viejo, [
            {"ts": viejo + "T09:00:00", "fuente": "pendientes", "texto": "ESPERAN TU RESPUESTA viejo"},
            {"ts": viejo + "T09:01:00", "fuente": "pendientes", "texto": "ESPERAN TU RESPUESTA viejo 2"},
            {"ts": viejo + "T09:02:00", "fuente": "centinela",
             "texto": "Plazo T-7 — «Cita futura» se acerca: es en 9 dias (%s). No lo pierdas de vista." % futuro},
            {"ts": viejo + "T09:03:00", "fuente": "centinela",
             "texto": "Plazo T-3 — «Cita pasada» se acerca: es en 3 dias (%s). No lo pierdas de vista." % pasado},
        ])
        salida, enviar_hoy = _modulos(tmp)

        print("── 1. lo de ayer entra en el parte de hoy ──")
        lote = salida.aplazados_pendientes()
        check(len(lote["avisos"]) == 7, "aplazados_pendientes lee los 7 avisos de los dos días")
        texto = enviar_hoy._con_aplazados("PARTE", lote, hoy=hoy)
        check("aviso de ayer a las 08:18" in texto, "el aviso aplazado ayer a las 08:18 está en el parte")
        check("PARTE" in texto, "sin perder el cuerpo del parte")

        print("── 5. el CI no queda enterrado en el bloque de salud ──")
        lineas_ci = [l for l in texto.splitlines() if "CI del repo público" in l]
        check(len(lineas_ci) == 1 and "30 seguidas" in lineas_ci[0] and "(×2)" in lineas_ci[0],
              "el CI sale una vez, en su versión más reciente, con cuántas veces se avisó")
        check(any("daemon X caído" in l and "CI" not in l for l in texto.splitlines()),
              "cada viñeta del aviso de salud es su propia línea")

        print("── 4. el atasco viejo va resumido ──")
        check("ESPERAN TU RESPUESTA viejo" not in texto, "los avisos viejos no se reenvían uno a uno")
        check("2 de correos que esperan tu respuesta" in texto, "resumen por tipo con su cuenta")
        check("«Cita futura»" in texto, "el plazo que sigue vivo aparece")
        check("«Cita pasada»" not in texto, "el plazo ya pasado no aparece")

        print("── 2. si el envío falla, no se archiva nada ──")
        enviar_hoy.seguimiento.construir_hoy = lambda franja, canal: "PARTE"
        enviar_hoy._persistir_hoy_md = lambda _p: []
        enviar_hoy.report_to_titular = lambda text, **kw: {"delivered": False, "reason": "red caída"}
        sys.argv = ["enviar_hoy.py", "mañana"]
        rc = enviar_hoy.main()
        check(rc == 1, "main() devuelve 1 con la entrega fallida")
        check(len(salida.aplazados_pendientes()["avisos"]) == 7, "los 7 avisos siguen pendientes")
        check(not os.path.exists(os.path.join(tmp, "aplazados", "entregados")),
              "no se archivó nada")

        print("── 3. entregado: se archiva, y lo que llega durante el envío se queda ──")
        hoy_iso = hoy.isoformat()

        def _entrega_y_aplaza(text, **kw):
            # Otro proceso aplaza un aviso MIENTRAS el parte se está enviando.
            _escribe(tmp, ayer, [{"ts": ayer + "T23:59:00", "fuente": "", "texto": "llegó durante el envío"}])
            _escribe(tmp, hoy_iso, [{"ts": hoy_iso + "T08:12:30", "fuente": "", "texto": "hoy durante el envío"}])
            return {"delivered": True}
        enviar_hoy.report_to_titular = _entrega_y_aplaza
        rc = enviar_hoy.main()
        check(rc == 0, "main() devuelve 0 con la entrega hecha")
        quedan = [a["texto"] for a in salida.aplazados_pendientes()["avisos"]]
        check(sorted(quedan) == ["hoy durante el envío", "llegó durante el envío"],
              "solo quedan pendientes los dos que llegaron durante el envío")
        ent = os.path.join(tmp, "aplazados", "entregados")
        n_ent = sum(1 for f in os.listdir(ent) for _ in open(os.path.join(ent, f), encoding="utf-8"))
        check(n_ent == 7, "los 7 entregados están archivados con su texto completo")
        check(not os.path.exists(os.path.join(tmp, "aplazados", "%s.jsonl" % viejo)),
              "el fichero viejo ya vaciado desaparece de la cola")

        print("── el fichero de HOY se conserva aunque se vacíe ──")
        lote = salida.aplazados_pendientes()
        salida.vaciar_aplazados(lote["leidos"])
        check(os.path.exists(os.path.join(tmp, "aplazados", "%s.jsonl" % hoy_iso)),
              "el de hoy sigue existiendo (vacío): el aviso «a partir de aquí agrupo» no se repite")
        check(salida.aplazados_pendientes()["avisos"] == [], "y ya no queda nada pendiente")

        # ── Casos de la revisión adversarial de `verificacion` (24-sep) ──
        print("── dos partes solapados no archivan lo que llegó entre medias ──")
        _escribe(tmp, hoy_iso, [{"ts": hoy_iso + "T09:00:00", "fuente": "", "texto": "A"}])
        l1 = salida.aplazados_pendientes()
        l2 = salida.aplazados_pendientes()
        _escribe(tmp, hoy_iso, [{"ts": hoy_iso + "T09:01:00", "fuente": "", "texto": "C entre medias"}])
        salida.vaciar_aplazados(l1["leidos"])
        salida.vaciar_aplazados(l2["leidos"])
        quedan = [a["texto"] for a in salida.aplazados_pendientes()["avisos"]]
        check(quedan == ["C entre medias"], "el segundo vaciado no se lleva C, que no entró en ningún parte")
        salida.vaciar_aplazados(salida.aplazados_pendientes()["leidos"])

        print("── una línea a medias no se come el aviso siguiente ──")
        path_hoy = os.path.join(tmp, "aplazados", "%s.jsonl" % hoy_iso)
        with open(path_hoy, "a", encoding="utf-8") as fh:
            fh.write('{"ts": "cortado a medio escrib')
        lote = salida.aplazados_pendientes()
        check(lote["avisos"] == [], "la línea a medias no cuenta como leída")
        salida._aplazar("B tras la línea rota", fuente="")
        quedan = [a["texto"] for a in salida.aplazados_pendientes()["avisos"]]
        check(quedan == ["B tras la línea rota"], "el aviso nuevo va en su propia línea y se lee entero")

        print("── el texto del parte ──")
        ayer_ts = ayer + "T19:32:00"
        bloque = "\n".join(enviar_hoy._bloque_recientes([
            {"ts": ayer_ts, "fuente": "", "texto": "📎 Hoy vence: cerrar el inventario"},
            {"ts": ayer_ts, "fuente": "healthcheck", "texto": "🩺 Revisión de salud del lazo:\n- API da HTTP 429"},
            {"ts": ayer_ts, "fuente": "healthcheck", "texto": "🩺 Revisión de salud del lazo:\n- API da HTTP 500"},
            {"ts": ayer_ts, "fuente": "", "texto": "Deuda abierta: no cerré las ramas.\n- rama-uno\n- rama-dos"},
        ], hoy=hoy))
        check("ayer 19:32 · 📎 Hoy vence" in bloque, "lo de ayer lleva «ayer HH:MM»: un «hoy vence» no llega un día tarde")
        check("HTTP 429" in bloque and "HTTP 500" in bloque, "dos avisos que solo difieren en una cifra no se funden")
        check("Deuda abierta: no cerré las ramas. · rama-uno" in bloque,
              "un aviso que no es de salud conserva su cabecera")

        print("── si lo aplazado revienta, el parte sale igual y no se vacía nada ──")
        _escribe(tmp, hoy_iso, [{"ts": hoy_iso + "T10:00:00", "fuente": ["raro"], "texto": "x"}])
        enviados = []
        enviar_hoy.report_to_titular = lambda text, **kw: enviados.append(text) or {"delivered": True}
        orig = enviar_hoy._con_aplazados
        enviar_hoy._con_aplazados = lambda *a, **k: 1 / 0
        try:
            rc = enviar_hoy.main()
        finally:
            enviar_hoy._con_aplazados = orig
        check(rc == 0 and enviados and enviados[0].startswith("PARTE"), "el parte sale con su cuerpo")
        check("No pude añadir los avisos agrupados" in (enviados[0] if enviados else ""), "y dice que faltan los agrupados")
        check(len(salida.aplazados_pendientes()["avisos"]) == 2, "lo aplazado sigue pendiente")

        print("── un registro raro no deja fuera a los demás ──")
        lote = salida.aplazados_pendientes()
        texto = enviar_hoy._con_aplazados("PARTE", lote, hoy=hoy)
        check("B tras la línea rota" in texto and "No pude" not in texto,
              "fuente no-str se normaliza al leer: el resto de aplazados entra en el parte")
        orig_rez = enviar_hoy._bloque_rezagados
        enviar_hoy._bloque_rezagados = lambda *a: 1 / 0
        try:
            lote = salida.aplazados_pendientes()
            texto = enviar_hoy._con_aplazados("PARTE", lote, hoy=hoy)
        finally:
            enviar_hoy._bloque_rezagados = orig_rez
        check("B tras la línea rota" in texto and "de días anteriores (ZeroDivisionError)" in texto,
              "si un bloque falla, el otro sale igual y se dice cuál faltó")
        check(lote.get("fallo") is True, "y el lote queda marcado para no vaciarse")

        print("── la continuación de una viñeta de salud no se pierde ──")
        piezas = enviar_hoy._piezas("🩺 Revisión de salud del lazo:\n- 3 encargos cayeron.\n"
                                    "Están en queue/failed y NO se reintentan solos.\n- otro")
        check(piezas == ["3 encargos cayeron. Están en queue/failed y NO se reintentan solos.", "otro"],
              "la línea que sigue a una viñeta se pega a ella")
    finally:
        for k in ("BTP_STATE_DIR", "BTP_TEST_BATTERY"):
            os.environ.pop(k, None)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s)" % len(fallos))
        return 1
    print("✅ aplazados: todo lo pendiente llega al parte y solo se archiva si llegó")
    return 0


if __name__ == "__main__":
    sys.exit(main())
