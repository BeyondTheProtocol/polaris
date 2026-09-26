#!/usr/bin/env python3
"""test_radar_encaje_n1.py — el encaje N1 del radar solo ordena, y sus candados muerden.

POR QUÉ EXISTE (22-sep-2026, plan `vast-orbiting-peach`). `radar_ned_diario.encaje_n1` manda a Jev
(TypeSafe, tercero en EE. UU.) el perfil N1 mínimo de {{TITULAR}} junto a los criterios públicos de cada
ensayo nuevo. Es egress de algo suyo, así que lo que hay que blindar no es la métrica sino:
  1. sin trust-cloud de {{TITULAR}} no sale nada;
  2. si ESTADO-ACTUAL §1 es posterior al perfil (cambió el tratamiento), no sale nada y se avisa:
     un perfil viejo diría «sin ADC previo» y daría falsos «encaja»;
  3. los papers no se puntúan con N1;
  4. nunca toca descartados ni cerrados (solo anota `p_encaje`);
  5. si Jev o CT.gov fallan, la cola queda intacta;
  6. el perfil no lleva términos vetados;
  7. orden: encaje → mama por Jev → el resto por llegada;
  8. mutantes: quitando el candado de trust o el de caducidad, este test se pone rojo.
Offline: todo con falsos. No lee la cola real ni ESTADO-ACTUAL.
"""
import copy
import inspect
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import bench_jev  # noqa: E402
import estado_actual  # noqa: E402
import radar_ned_diario as r  # noqa: E402

_pass = _fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


class Borde:
    def __init__(self, trusted=True, halt=False, canario=False):
        self.trusted, self.halt, self.canario, self.sellos = trusted, halt, canario, []

    def halted(self):
        return self.halt

    def es_trusted(self, destino):
        return self.trusted and destino == "jev-typesafe"

    def identificador_directo(self, texto):
        return False, "limpio"

    def hay_canario(self, texto):
        return self.canario

    def _sellar(self, ev):
        self.sellos.append(ev)


H = "0123456789abcdef"  # huella de §1 falsa: el test no lee ESTADO-ACTUAL


def _bench(fecha=(2026, 9, 21), huella=H):
    return types.SimpleNamespace(
        DESTINO_N1=bench_jev.DESTINO_N1, PERFIL_N1=bench_jev.PERFIL_N1, PERFIL_N1_FECHA=fecha,
        PERFIL_N1_HUELLA_S1=huella,
        INSTR_N1=bench_jev.INSTR_N1, _VETADAS_N1=bench_jev._VETADAS_N1,
        preguntar=None, _clave=lambda: "k")


class Jev:
    def __init__(self, falla_en=None):
        self.llamadas, self.falla_en = [], falla_en

    def __call__(self, texto, clave, instr=None):
        if self.falla_en is not None and len(self.llamadas) == self.falla_en:
            raise TimeoutError("jev caído")
        self.llamadas.append((texto, instr))
        return (0.9 if "NCT00000001" in texto else 0.2), 5


def get_ok(url, params):
    nct = url.rsplit("/", 1)[1]
    return {"protocolSection": {"identificationModule": {"briefTitle": f"Trial {nct}"},
                                "eligibilityModule": {"eligibilityCriteria": "Inclusion: HR+ MBC."}}}


def get_falla(url, params):
    raise ConnectionError("ct.gov caído")


def _cola():
    return {
        "pendientes": [
            {"uid": "e1", "tema": "adc", "tipo": "ensayo", "ref": "NCT00000002", "url": "", "encolado": "2026-09-20", "titulo": "A"},
            {"uid": "e2", "tema": "adc", "tipo": "ensayo", "ref": "NCT00000001", "url": "", "encolado": "2026-09-21", "titulo": "B"},
            {"uid": "p1", "tema": "adc", "tipo": "paper", "ref": "PMID 1", "url": "https://x/NCT00000003", "encolado": "2026-09-19", "titulo": "C", "p_jev": 0.8},
            {"uid": "c1", "tema": "celular", "tipo": "paper", "ref": "PMID 2", "url": "", "encolado": "2026-09-18", "titulo": "D"},
            {"uid": "cn", "tema": "cn-dianas", "tipo": "ensayo", "ref": "ChiCTR2600000001", "url": "", "encolado": "2026-09-17", "titulo": "E"},
        ],
        "cerrados": [{"uid": "x1", "tipo": "ensayo", "ref": "NCT00000009", "titulo": "F", "veredicto": "no"}],
        "descartados": [{"uid": "x2", "tipo": "ensayo", "ref": "NCT00000008", "titulo": "G"}],
    }


def comprobar(mod):
    """Corre los casos contra `mod` (el módulo real o un mutante). Devuelve la lista de fallos."""
    fallos = []

    def f(cond, name):
        if not cond:
            fallos.append(name)

    # 1. sin trust, no se llama a Jev
    c, jev = _cola(), Jev()
    n, motivo = mod.encaje_n1(c["pendientes"], preguntar=jev, get=get_ok, bench=_bench(),
                              borde=Borde(trusted=False), fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n == 0 and not jev.llamadas, "sin trust no se llama a Jev")
    f("trust" in motivo, "sin trust, el motivo lo dice")

    # 2. perfil caducado: no se llama a Jev y queda aviso
    c, jev = _cola(), Jev()
    n, motivo = mod.encaje_n1(c["pendientes"], preguntar=jev, get=get_ok, bench=_bench(),
                              borde=Borde(), fecha_clinica=(2026, 9, 28), huella_s1=H)
    f(n == 0 and not jev.llamadas, "perfil caducado no llama a Jev")
    f("caducado" in motivo and "ESTADO-ACTUAL" in motivo, "perfil caducado avisa en el log")

    # 2b. HALT activo: nada sale
    c, jev = _cola(), Jev()
    n, _ = mod.encaje_n1(c["pendientes"], preguntar=jev, get=get_ok, bench=_bench(),
                         borde=Borde(halt=True), fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n == 0 and not jev.llamadas, "con HALT no se llama a Jev")

    # 3 + 4. camino bueno: solo ensayos con NCT; papers y ChiCTR fuera; cerrados/descartados intactos
    c, jev, bd = _cola(), Jev(), Borde()
    antes = copy.deepcopy(c)
    n, motivo = mod.encaje_n1(c["pendientes"], preguntar=jev, get=get_ok, bench=_bench(),
                              borde=bd, fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n == 2 and motivo == "ok", "puntúa los 2 ensayos con NCT")
    por_uid = {p["uid"]: p for p in c["pendientes"]}
    f(por_uid["e2"].get("p_encaje") == 0.9 and por_uid["e1"].get("p_encaje") == 0.2, "guarda p_encaje")
    f(all("p_encaje" not in por_uid[u] for u in ("p1", "c1", "cn")), "papers y registros sin NCT no se puntúan")
    f(all(i == bench_jev.INSTR_N1 for _, i in jev.llamadas), "usa INSTR_N1")
    f(all(t.startswith(bench_jev.PERFIL_N1) for t, _ in jev.llamadas), "sale el perfil N1 de bench_jev")
    f(len(bd.sellos) == 2, "cada envío se sella en el borde")
    f(c["cerrados"] == antes["cerrados"] and c["descartados"] == antes["descartados"],
      "cerrados y descartados intactos")
    f([p["uid"] for p in c["pendientes"]] == [p["uid"] for p in antes["pendientes"]],
      "no quita ni reordena pendientes")
    # segunda pasada: no repite
    n2, _ = mod.encaje_n1(c["pendientes"], preguntar=jev, get=get_ok, bench=_bench(),
                          borde=bd, fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n2 == 0 and len(jev.llamadas) == 2, "no re-puntúa lo ya puntuado")

    # 5. fallos de red: la cola queda intacta
    c = _cola()
    antes = copy.deepcopy(c)
    n, _ = mod.encaje_n1(c["pendientes"], preguntar=Jev(falla_en=0), get=get_ok, bench=_bench(),
                         borde=Borde(), fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n == 0 and c == antes, "Jev caído: cola intacta")
    c = _cola()
    n, _ = mod.encaje_n1(c["pendientes"], preguntar=Jev(), get=get_falla, bench=_bench(),
                         borde=Borde(), fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n == 0 and c == antes, "CT.gov caído: cola intacta")
    c = _cola()
    n, _ = mod.encaje_n1(c["pendientes"], preguntar=Jev(), get=get_ok, bench=_bench(),
                         borde=Borde(canario=True), fecha_clinica=(2026, 9, 13), huella_s1=H)
    f(n == 0 and c == antes, "canario: no sale")
    return fallos


def main():
    fallos = comprobar(r)
    for x in fallos:
        ok(False, x)
    ok(not fallos, "el módulo real pasa todos los casos")

    # 4b. por construcción: el código del encaje no nombra descartados ni cerrados
    src = inspect.getsource(r.encaje_n1) + inspect.getsource(r.candados_n1)
    ok(not any(k in src for k in ('"descartados"', "'descartados'", '"cerrados"', "'cerrados'")),
       "encaje_n1 no toca las claves descartados/cerrados")

    # 6. perfil sin vetadas ni identificador; fecha de perfil bien formada
    perfil = bench_jev.PERFIL_N1.lower()
    ok(not any(v in perfil for v in bench_jev._VETADAS_N1), "perfil N1 sin términos vetados")
    ok(len(bench_jev.PERFIL_N1_FECHA) == 3, "PERFIL_N1_FECHA es (año, mes, día)")

    # 7. orden: encaje → mama por Jev → resto por llegada
    pend = [
        {"uid": "resto", "tema": "celular", "encolado": "2026-09-01"},
        {"uid": "mama", "tema": "adc", "p_jev": 0.99, "encolado": "2026-09-02"},
        {"uid": "enc-bajo", "tema": "adc", "p_encaje": 0.3, "p_jev": 0.1, "encolado": "2026-09-03"},
        {"uid": "enc-alto", "tema": "celular", "p_encaje": 0.8, "encolado": "2026-09-04"},
        {"uid": "mama-baja", "tema": "adc", "p_jev": 0.2, "encolado": "2026-09-05"},
    ]
    ok([p["uid"] for p in sorted(pend, key=r.clave_orden)]
       == ["enc-alto", "enc-bajo", "mama", "mama-baja", "resto"], "orden encaje → jev → llegada")

    # 2c. la fecha clínica mira también las «Novedades del …» de §1 (un cambio de línea entra así)
    est = "## 1. Clínico (al 11-sep-2026)\n- **Novedades del 25-sep:** empieza TB06\n## 2. Otro\n"
    ok(estado_actual.fecha_clinica(est) == (2026, 9, 25), "fecha clínica = la novedad más reciente")
    ok(estado_actual.lee_fecha_clinica("/no/existe")[0] is None, "ESTADO-ACTUAL ausente → None")
    real, estado_actual.ESTADO = estado_actual.ESTADO, "/no/existe/ESTADO-ACTUAL.md"
    try:
        ok(not r.candados_n1(bench=_bench(), borde=Borde(), fecha_clinica=None)[0],
           "sin ESTADO-ACTUAL legible, el candado queda cerrado")
    finally:
        estado_actual.ESTADO = real

    # 8. mutantes: quitar un candado tiene que poner el test en rojo
    fuente = inspect.getsource(r)
    for nombre, viejo, nuevo in (
        ("sin candado de trust", "if not borde.es_trusted(bench.DESTINO_N1):", "if False:"),
        ("sin candado de caducidad", "if fecha_clinica > tuple(bench.PERFIL_N1_FECHA):", "if False:"),
    ):
        ok(viejo in fuente, f"mutante «{nombre}»: la línea existe")
        mut = types.ModuleType("radar_mutante")
        mut.__file__ = r.__file__
        exec(compile(fuente.replace(viejo, nuevo, 1), "radar_mutante", "exec"), mut.__dict__)
        ok(bool(comprobar(mut)), f"mutante «{nombre}»: el test lo caza")

    print(f"test_radar_encaje_n1: {_pass} ok, {_fail} fallos")
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
