#!/usr/bin/env python3
"""test_cost_guard.py — batería del freno de gasto (P1, fail-closed A2).

Aísla COST/HALT_FILES en un tmp y verifica: suma, coste pesimista, tope, modo
conservador, concurrencia, y el caso estrella: estado ilegible → crea .HALT.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cost_guard as cg  # noqa: E402
import types as _types  # noqa: E402

# CORTAFUEGOS DE LA BATERIA (13-jul-2026). `salida` es la UNICA boca al exterior: entrega
# Telegram REAL a {{TITULAR}}. cost_guard hace `import salida` PEREZOSO dentro de
# _nudge_bloqueo_esencial(), asi que cualquier bloque que fuerce un bloqueo ESENCIAL alcanzaba
# la boca REAL y le mandaba un mensaje de verdad. Paso el 12-jul: 14 Telegram espurios con los
# $25/$30 de este mismo fixture (add_cost(25.0) sobre TOPE_DIARIO_USD=30.0), uno por cada
# ejecucion de la bateria; y el barrido de seguridad corre este test a diario a las 06:00, asi
# que se repetia cada manana. El stub se instala AQUI, a nivel de modulo y ANTES de cualquier
# test, para que NINGUNA ruta pueda alcanzar la boca real — la seguridad no puede depender de
# acordarse de stubbear bloque a bloque. Los bloques que instrumentan `salida` ponen su propio
# doble y RESTAURAN este (nunca `del`, que dejaria la boca real al descubierto).
_entregas_globales = []
import os as _os_bat, tempfile as _tf_bat
# MURO DE LA BATERIA (14-jul-2026). Estas dos senales juntas hacen que `salida.send()` se niegue a
# entregar de verdad (ver salida._bajo_bateria_test). Van a NIVEL DE MODULO, antes de cualquier
# import, y se HEREDAN a los subprocesos — que es lo que el stub de sys.modules no podia cubrir.
_os_bat.environ["BTP_TEST_BATTERY"] = "1"
_os_bat.environ.setdefault("BTP_STATE_DIR", _tf_bat.mkdtemp(prefix="bateria_"))

_STUB_SALIDA = _types.ModuleType("salida")
_STUB_SALIDA.report_to_titular = lambda texto, **kw: (
    _entregas_globales.append(texto)
    or {"delivered": True, "blocked": False, "reason": "stub de test (no sale nada)"})
sys.modules["salida"] = _STUB_SALIDA

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def setup(tmp):
    cg.STATE = tmp
    cg.COST = os.path.join(tmp, "cost")
    cg.LOCKDIR = os.path.join(cg.COST, "lock")
    cg.LIMITS = os.path.join(cg.COST, "limits.json")
    cg.DEGRADED = os.path.join(tmp, "healthcheck", "degraded.flag")
    cg.HALT_FILES = (os.path.join(tmp, ".btp.HALT"), os.path.join(tmp, ".HALT"))
    cg.TOPE_DIARIO_USD = 30.0
    cg.TOPE_JOB_USD = 3.0
    cg._ensure_dirs()


def main():
    tmp = tempfile.mkdtemp(prefix="test_cost_")
    setup(tmp)

    # 1. suma básica: dict con total_cost_usd, y float.
    check("today empieza en 0", cg.today_spent() == 0.0)
    cg.add_cost({"total_cost_usd": 0.42}, job_id="j1")
    cg.add_cost(0.08, job_id="j2")
    check("suma dict+float", abs(cg.today_spent() - 0.50) < 1e-9)

    # 2. coste pesimista: JSON sin total_cost_usd → cuenta el tope del job, nunca 0.
    antes = cg.today_spent()
    usd = cg.add_cost({"foo": "bar"}, job_id="j3", tope_job_usd=2.0)
    check("coste pesimista = tope job", usd == 2.0 and cg.today_spent() == antes + 2.0)
    usd2 = cg.add_cost("", job_id="j4")          # stdin vacío → pesimista default (3.0)
    check("stdin vacío → pesimista default", usd2 == 3.0)

    # 3. tope diario: con gastado ≈ tope → check_before_job = False.
    cg.add_cost(float(cg.TOPE_DIARIO_USD), job_id="big")   # empuja por encima del tope
    ok, motivo, restante = cg.check_before_job()
    check("tope alcanzado → ok False", ok is False and restante <= 0)

    # 3b. H4: exigir presupuesto de un job entero — restante < job_cap → ok False.
    tmp_h4 = tempfile.mkdtemp(prefix="test_cost_h4_")
    setup(tmp_h4)
    cg.add_cost(28.5, job_id="casi")     # restante 1.5 < tope_job 3.0
    okh4, _, resth4 = cg.check_before_job()
    check("restante<job_cap → ok False (H4)", okh4 is False and 0 < resth4 < 3.0)

    # 4. modo conservador: degraded.flag recorta el tope efectivo.
    tmp2 = tempfile.mkdtemp(prefix="test_cost2_")
    setup(tmp2)
    cg.add_cost(10.0, job_id="x")        # 10 de 30 → ok en normal
    ok_n, _, _ = cg.check_before_job()
    check("normal con 10/30 → ok", ok_n is True)
    os.makedirs(os.path.dirname(cg.DEGRADED), exist_ok=True)
    open(cg.DEGRADED, "w").close()       # tope efectivo = 30*0.3 = 9 < 10 gastado
    ok_d, _, _ = cg.check_before_job()
    check("degradado con 10/9 → ok False", ok_d is False)

    # 5. caso estrella: estado del día ILEGIBLE → _panic crea .HALT y aborta.
    tmp3 = tempfile.mkdtemp(prefix="test_cost3_")
    setup(tmp3)
    open(cg._today_path(), "w").write("{ esto no es json valido")
    paniced = False
    try:
        cg.today_spent()
    except SystemExit:
        paniced = True
    check("estado ilegible → SystemExit", paniced)
    check("estado ilegible → crea .HALT", os.path.exists(cg.HALT_FILES[0]) and os.path.exists(cg.HALT_FILES[1]))

    # 6. concurrencia: muchas sumas no se pisan (lock).
    tmp4 = tempfile.mkdtemp(prefix="test_cost4_")
    setup(tmp4)
    for i in range(20):
        cg.add_cost(0.01, job_id="c%d" % i)
    check("20 sumas de 0.01 = 0.20", abs(cg.today_spent() - 0.20) < 1e-9)
    check("n_jobs contó 20", json.load(open(cg._today_path()))["n_jobs"] == 20)

    # 7. TRES carriles de reserva (interactivo > esencial > rutina). Garantiza que ni la cháchara
    #    rutinaria ni el trabajo de fondo "esencial" (Vega) dejen a {{TITULAR}} EN VIVO sin Claude.
    #    tope 30 → reserva_int = 4.5, reserva_ese = 6, job_cap = 3 →
    #      rutina necesita 13.5 · esencial necesita 7.5 · interactivo necesita 3.
    tmp5 = tempfile.mkdtemp(prefix="test_cost5_")
    setup(tmp5)
    cg.add_cost(21.0, job_id="r")                          # restante 9 de 30
    ok_rut, _, _ = cg.check_before_job()
    ok_ese, _, _ = cg.check_before_job(esencial=True)
    ok_int, _, _ = cg.check_before_job(interactivo=True)
    check("9/30: rutina bloqueada (debe dejar ambas franjas)", ok_rut is False)
    check("9/30: esencial pasa (deja solo la franja interactiva)", ok_ese is True)
    check("9/30: interactivo pasa", ok_int is True)

    # La franja interactiva es EXCLUSIVA de {{TITULAR}}: con MUY poco saldo, ni el trabajo esencial
    # de fondo (Vega) la toca — solo su charla EN VIVO. Éste es el corazón de "reserva para ti".
    tmp5b = tempfile.mkdtemp(prefix="test_cost5b_")
    setup(tmp5b)
    cg.add_cost(25.0, job_id="r")                          # restante 5 de 30
    ok_rut2, _, _ = cg.check_before_job()
    ok_ese2, _, _ = cg.check_before_job(esencial=True)
    ok_int2, _, _ = cg.check_before_job(interactivo=True)
    check("5/30: rutina bloqueada", ok_rut2 is False)
    check("5/30: esencial bloqueado (NO se come la franja de {{TITULAR}})", ok_ese2 is False)
    check("5/30: interactivo ({{TITULAR}} en vivo) SIGUE pasando", ok_int2 is True)

    cg.add_cost(30.0, job_id="vacio")                     # hucha vacía
    ok_int0, _, _ = cg.check_before_job(interactivo=True)
    check("ni lo interactivo GASTA con hucha vacía (el caller baja a local, no bloquea)", ok_int0 is False)
    # el techo de dinero NO sube por las reservas: el clamp absoluto sigue mandando.
    check("reservas no suben el techo (clamp absoluto intacto)", cg.TOPE_MAX_ABSOLUTO == 540.0)

    # 8. override SOLO-HOY: sube el tope del día y se auto-clampa al techo absoluto.
    tmp6 = tempfile.mkdtemp(prefix="test_cost6_")
    setup(tmp6)
    ovr = os.path.join(cg.COST, "override-" + cg.datetime.now().strftime("%Y-%m-%d") + ".json")
    open(ovr, "w").write('{"tope_diario_usd": 12.0}')
    check("override sube el tope del día", cg._limits()[0] == 12.0)
    open(ovr, "w").write('{"tope_diario_usd": 999.0}')
    check("override clampado al techo absoluto", cg._limits()[0] == cg.TOPE_MAX_ABSOLUTO)
    open(ovr, "w").write("basura no-json")
    check("override ilegible → se ignora (manda baseline, no panic)", cg._limits()[0] == cg.TOPE_DIARIO_USD)

    # 9. aprobar_tope_hoy («sube» de {{TITULAR}}): ESCRIBE el override del día y sube el tope efectivo,
    #    clampado al techo absoluto. Es lo que dispara bot_telegram al recibir «sube» (DETERMINISTA,
    #    sin LLM → sin el deadlock de "necesitar gasto para aprobar gasto"). Auto-expira mañana.
    tmp7 = tempfile.mkdtemp(prefix="test_cost7_")
    setup(tmp7)
    open(cg.LIMITS, "w").write('{"tope_diario_usd": 5.4}')   # baseline rutinaria bajada
    check("baseline antes de aprobar = 5.4", cg._limits()[0] == 5.4)
    nuevo = cg.aprobar_tope_hoy()                            # sin monto → al techo absoluto
    check("aprobar sin monto → techo absoluto",
          nuevo == cg.TOPE_MAX_ABSOLUTO and cg._limits()[0] == cg.TOPE_MAX_ABSOLUTO)
    ovr2 = os.path.join(cg.COST, "override-" + cg.datetime.now().strftime("%Y-%m-%d") + ".json")
    check("aprobar escribió el override del día", os.path.exists(ovr2))
    nuevo2 = cg.aprobar_tope_hoy(monto=999.0)                # monto excesivo → clampado al techo
    check("aprobar con monto excesivo → clampado al techo", nuevo2 == cg.TOPE_MAX_ABSOLUTO)

    # 10. tipo_bloqueo: distingue tope_local de prepago_agotado de None.
    #     El tope local es el presupuesto que NOSOTROS fijamos (recuperable con 1 clic);
    #     el prepago agotado es que la API de Anthropic está a cero (hay que recargar).
    tmp8 = tempfile.mkdtemp(prefix="test_cost8_")
    setup(tmp8)
    # Agota el tope diario → check_before_job retorna motivo con [tope_local]
    cg.add_cost(float(cg.TOPE_DIARIO_USD), job_id="big")
    ok_t, motivo_t, _ = cg.check_before_job()
    check("tope local → ok False", ok_t is False)
    check("tope local → motivo contiene [tope_local]", "[tope_local]" in motivo_t)
    check("tipo_bloqueo(motivo_tope_local) == 'tope_local'", cg.tipo_bloqueo(motivo_t) == "tope_local")
    check("tipo_bloqueo('[prepago_agotado] texto') == 'prepago_agotado'",
          cg.tipo_bloqueo("[prepago_agotado] crédito Anthropic a 0") == "prepago_agotado")
    check("tipo_bloqueo(None) == None", cg.tipo_bloqueo(None) is None)
    check("tipo_bloqueo('ok rutina ...') == None", cg.tipo_bloqueo("ok rutina") is None)
    # Techo absoluto = ceiling autorizado por {{TITULAR}} (27/6/26: €500/día ≈ $500). Pineado a
    # propósito: si alguien vuelve a cambiar el clamp de dinero sin querer, este test salta.
    check("tope MAX_ABSOLUTO = ceiling autorizado (540.0 ≈ 500€)", cg.TOPE_MAX_ABSOLUTO == 540.0)

    # 10b. DOS CUBOS (25/7/26): lo que corre por SUSCRIPCIÓN (plan Max ya pagado) no puede comerse
    #      el presupuesto de DINERO de la API. Bug real: la auto-mejora, que va por suscripción, se
    #      aplazó por un contador de dólares que no reflejaba dinero suyo.
    tmp_via = tempfile.mkdtemp(prefix="test_cost_via_")
    setup(tmp_via)
    cg.add_cost(2.0, job_id="sub-1", via="suscripcion")
    d_via = cg._read_today()
    check("gasto por suscripción NO suma al cubo de dinero", d_via.get("gastado_usd", 0.0) == 0.0)
    check("gasto por suscripción suma a su propio cubo", d_via.get("cuota_suscripcion_usd") == 2.0)
    check("el evento queda etiquetado con su vía", d_via["eventos"][-1].get("via") == "suscripcion")
    cg.add_cost(1.0, job_id="api-1")
    check("sin vía explícita → cuenta como dinero (fail-closed)", cg._read_today()["gastado_usd"] == 1.0)
    check("el evento sin vía explícita se etiqueta 'api'", cg._read_today()["eventos"][-1].get("via") == "api")
    # Con el tope diario AGOTADO en dinero, un job por suscripción sigue pasando y uno de API no.
    cg.add_cost(float(cg.TOPE_DIARIO_USD), job_id="agota")
    ok_api, motivo_api, _ = cg.check_before_job()
    ok_sub, motivo_sub, _ = cg.check_before_job(via="suscripcion")
    check("tope agotado → la API se frena", ok_api is False and "[tope_local]" in motivo_api)
    check("tope agotado → la suscripción PASA (no gasta dinero)", ok_sub is True)
    check("el motivo dice por qué pasa", "SUSCRIPCIÓN" in motivo_sub)
    check("una vía desconocida NO abre la mano (fail-closed)", cg.check_before_job(via="gratis")[0] is False)
    check("el mes solo suma dinero real, no cuota de plan",
          cg.month_spent() == cg._read_today()["gastado_usd"])
    # El cortacircuitos de runaway sigue valiendo también por suscripción: un bucle que quema la
    # CUOTA es igual de grave que uno que quema dinero (lo que se agota es su capacidad, no su banco).

    # 9. cortacircuitos «de golpe» ({{TITULAR}} 27/6/26): un cargo >= TOPE_GOLPE_USD dispara código rojo.
    import types
    _rojo = []
    _fake = types.ModuleType("codigo_rojo")
    _fake.trigger = lambda motivo, detalle="": _rojo.append((motivo, detalle))
    sys.modules["codigo_rojo"] = _fake
    cg.add_cost(1.0, job_id="normal")                 # cargo normal NO dispara
    check("cargo normal no dispara el cortacircuitos", len(_rojo) == 0)
    cg.add_cost(float(cg.TOPE_GOLPE_USD), job_id="runaway")   # >= 500 → código rojo
    check("gasto de golpe (>=500) dispara código rojo", len(_rojo) == 1)
    del sys.modules["codigo_rojo"]

    # 11. No-mudo (11-jul-2026): job ESENCIAL bloqueado por [tope_local] AVISA por salida.py
    #     (antes callaba del todo). Rutina/interactivo bloqueados NO avisan (a propósito: solo
    #     lo esencial, para no ser ruido de fondo). Cooldown: no repite en <6h; si `salida` no
    #     entrega (HALT/silencio nocturno), no sella y reintenta en la próxima pasada.
    import types as _types
    tmp9 = tempfile.mkdtemp(prefix="test_cost9_")
    setup(tmp9)
    cg.add_cost(float(cg.TOPE_DIARIO_USD), job_id="agota")   # agota el tope diario

    entregas = []
    _fake_salida = _types.ModuleType("salida")
    _fake_salida.report_to_titular = lambda texto, **kw: (entregas.append(texto) or {"delivered": True})
    sys.modules["salida"] = _fake_salida
    try:
        ok_rut3, _, _ = cg.check_before_job()                  # rutina bloqueada
        check("rutina bloqueada NO avisa (a propósito)", ok_rut3 is False and entregas == [])
        ok_int3, _, _ = cg.check_before_job(interactivo=True)  # interactivo bloqueado
        check("interactivo bloqueado NO avisa", ok_int3 is False and entregas == [])
        ok_ese3, motivo_ese3, _ = cg.check_before_job(esencial=True)   # esencial bloqueado
        check("esencial bloqueado SÍ avisa", ok_ese3 is False and len(entregas) == 1)
        check("el aviso menciona el motivo real ([tope_local])", "[tope_local]" in entregas[0])
        check("se sella el cooldown tras entregar", os.path.exists(cg._nudge_mark_path()))

        # 2º bloqueo esencial INMEDIATO → cooldown activo, no reenvía.
        cg.check_before_job(esencial=True)
        check("cooldown suprime el reenvío inmediato", len(entregas) == 1)
    finally:
        sys.modules["salida"] = _STUB_SALIDA   # restaura el cortafuegos, NUNCA del

    # Si `salida` NO entrega (HALT/silencio nocturno), no se sella → reintenta en la próxima.
    tmp10 = tempfile.mkdtemp(prefix="test_cost10_")
    setup(tmp10)
    cg.add_cost(float(cg.TOPE_DIARIO_USD), job_id="agota2")
    entregas2 = []
    _fake_salida2 = _types.ModuleType("salida")
    _fake_salida2.report_to_titular = lambda texto, **kw: (entregas2.append(texto) or {"delivered": False})
    sys.modules["salida"] = _fake_salida2
    try:
        cg.check_before_job(esencial=True)
        check("avisar no entregado → no sella cooldown", not os.path.exists(cg._nudge_mark_path()))
        cg.check_before_job(esencial=True)
        check("sin sellar, reintenta en la próxima pasada", len(entregas2) == 2)
    finally:
        sys.modules["salida"] = _STUB_SALIDA   # restaura el cortafuegos, NUNCA del

    # 11. SALDO PREPAGO (aviso 17-jul): registrar recarga, medir gasto desde entonces, estimar saldo.
    tmp11 = tempfile.mkdtemp(prefix="test_cost11_")
    setup(tmp11)
    check("sin recarga → saldo_prepago None", cg.saldo_prepago() is None)
    rec = cg.registrar_recarga(50)
    check("registrar_recarga anota monto", rec["monto_usd"] == 50.0)
    ur = cg.ultima_recarga()
    check("ultima_recarga devuelve la última", ur is not None and ur["monto_usd"] == 50.0)
    # gasto de HOY (mismo día que la recarga → cuenta) = 40 de 50 → frac 0.8
    cg.add_cost(40.0, job_id="gasto_post_recarga")
    sp = cg.saldo_prepago()
    check("saldo_prepago frac ≈ 0.8", sp is not None and abs(sp["frac"] - 0.8) < 1e-6)
    check("saldo_prepago restante ≈ 10", abs(sp["restante"] - 10.0) < 1e-6)
    # segunda recarga (la última manda): $100 nuevo, gasto ya $40 → frac 0.4
    cg.registrar_recarga(100)
    sp2 = cg.saldo_prepago()
    check("2ª recarga manda (frac ≈ 0.4)", abs(sp2["frac"] - 0.4) < 1e-6 and sp2["monto"] == 100.0)

    print("RESULTADO cost_guard.py: %d OK, %d fallos" % (_pass, _fail))
    print("✅ COST_GUARD EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
