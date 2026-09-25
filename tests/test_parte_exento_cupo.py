#!/usr/bin/env python3
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("sin-halt")
# -*- coding: utf-8 -*-
"""El parte diario (tools/enviar_hoy.py) tiene que salir SIEMPRE, aunque el cupo de
avisos (tools/salida.py, portero de ruido, 27-jul-26) esté agotado.

BUG REAL (29-jul-26): `enviar_hoy.py` llamaba a `report_to_titular(text)` sin marcar
nada especial, así que el parte pasaba por el MISMO cupo que las alertas de rutina.
Como el propio parte ES el mecanismo que vacía lo aplazado (`_aplazar()` promete "el
resto lo agrupo en el parte"), si el parte caía bajo el cupo, lo aplazado no salía
NUNCA — el cupo se convertía en un bloqueo PERMANENTE, no en un filtro de ruido. En
vivo: `com.btp.enviar-hoy` salía con exit 1 cada mañana, 3 avisos rutinarios se comían
el cupo antes de las 09:00 y el parte quedaba aplazado a sí mismo, para siempre.

Lo que este test fija:
  1. con el cupo agotado, `report_to_titular(..., categoria="parte")` NO se aplaza
     (a diferencia de categoria="humano", que sí).
  2. lo aplazado del día se incorpora al texto del parte (`enviar_hoy._con_aplazados`).
  3. `vaciar_aplazados_de_hoy()` limpia el fichero — y solo se debe llamar tras
     confirmar la entrega (lo verifica el propio flujo de `enviar_hoy.main`)."""
import importlib
import json
import os
import shutil
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "tools"))

fallos = []


def check(cond, etiqueta):
    print(("  ✅ " if cond else "  ❌ ") + etiqueta)
    if not cond:
        fallos.append(etiqueta)


def _salida_aislada(tmp, tope="3"):
    """Mismo patrón que test_portero_ruido.py: salida.py con estado en tmpdir, sin red."""
    os.environ["BTP_STATE_DIR"] = tmp
    os.environ["BTP_TOPE_AVISOS"] = tope
    os.environ["BTP_TEST_BATTERY"] = "1"     # ni un mensaje real a {{TITULAR}}
    import salida
    importlib.reload(salida)
    salida.STATE = tmp
    salida.OUTBOX = os.path.join(tmp, "outbox")
    os.makedirs(salida.OUTBOX, exist_ok=True)
    # Chat_id FALSO e inyectado. Lo que este test mide es el CUPO, no de dónde sale el
    # destino; sin esto dependía del secreto real (`tools/.telegram_secrets.json`, que
    # está en .gitignore y solo existe en casa base) y en cualquier worktree send() se
    # iba por la rama "sin chat_id → borrador" ANTES de llegar al mutis de batería. Un
    # test que solo pasa donde vive el secreto de producción no es un test.
    salida._self_chatid = lambda: "TEST-CHATID-FALSO"
    return salida


def _finge_entregados(salida, n):
    import time
    path = os.path.join(salida.OUTBOX, "audit-%s.jsonl" % time.strftime("%Y-%m-%d"))
    with open(path, "a", encoding="utf-8") as fh:
        for _ in range(n):
            fh.write(json.dumps({"ts": "x", "canal": "telegram", "accion": "report",
                                 "veredicto": "entregado"}) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="btp_parte_")
    try:
        salida = _salida_aislada(tmp, tope="3")

        print("── agotar el cupo con avisos de rutina ──")
        _finge_entregados(salida, 3)
        check(salida._presupuesto_agotado(), "el cupo (3) queda agotado")

        print("── un aviso normal SÍ se aplaza (control: el portero sigue vivo) ──")
        r_humano = salida.report_to_titular("aviso rutinario nº4", categoria="humano")
        check(isinstance(r_humano, dict) and r_humano.get("aplazado"),
              "categoria='humano' se aplaza con el cupo agotado (comportamiento previo intacto)")

        print("── el PARTE no se aplaza aunque el cupo esté agotado (el fix) ──")
        r_parte = salida.report_to_titular("PARTE DE HOY: texto del parte", categoria="parte")
        check(not r_parte.get("aplazado"),
              "categoria='parte' NO se aplaza con el cupo agotado")
        check("bateria de tests" in r_parte.get("reason", ""),
              "sigue su camino hasta send() (bloqueado solo por el MUTIS de test, no por el cupo)")

        print("── lo aplazado del día se incorpora al parte y luego se vacía ──")
        apl_antes = salida.aplazados_de_hoy()
        check(len(apl_antes) == 1 and "aviso rutinario nº4" in json.dumps(apl_antes, ensure_ascii=False),
              "el aviso aplazado sigue guardado, esperando al parte")

        # Import aislado de enviar_hoy con el mismo salida.py ya parcheado (misma STATE).
        sys.modules.pop("enviar_hoy", None)
        sys.modules["salida"] = salida
        import enviar_hoy
        importlib.reload(enviar_hoy)
        texto_base = "cuerpo del parte"
        texto_final = enviar_hoy._con_aplazados(texto_base)
        check("aviso rutinario nº4" in texto_final and texto_base in texto_final,
              "_con_aplazados incorpora el texto aplazado al parte, sin perder el cuerpo")

        salida.vaciar_aplazados_de_hoy()
        apl_despues = salida.aplazados_de_hoy()
        check(apl_despues == [], "tras vaciar_aplazados_de_hoy(), el fichero del día queda limpio")

        print("── canario: sin el fix, el parte se aplazaría igual que un aviso normal ──")
        # Reproduce la causa raíz: llamar SIN categoria="parte" (como hacía el enviar_hoy.py
        # roto) tiene que seguir aplazándose — si esto deja de fallar, es que report_to_titular
        # cambió su defecto y el resto del test ya no prueba lo que dice probar.
        r_sin_marcar = salida.report_to_titular("parte sin marcar categoria (bug reproducido)")
        check(bool(r_sin_marcar.get("aplazado")),
              "reproduce el bug: sin categoria='parte' el envío por defecto SÍ se aplaza")
    finally:
        for k in ("BTP_STATE_DIR", "BTP_TOPE_AVISOS", "BTP_TEST_BATTERY"):
            os.environ.pop(k, None)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("❌ %d fallo(s): %s" % (len(fallos), "; ".join(fallos)))
        return 1
    print("✅ parte diario exento del cupo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
