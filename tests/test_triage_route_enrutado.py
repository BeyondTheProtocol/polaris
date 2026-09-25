#!/usr/bin/env python3
"""test_triage_route_enrutado.py — el lazo de Telegram decide quién responde (11-sep-26).

En el chat, `decide_peticion` decide y el Stop lo exige. En el lazo no hay Stop, así que la
decisión se fija en el job ANTES de lanzar: `triage_route` encola con el comité dueño como
`agente`. Lo que no puede fallar:
  · un resumen clínico → `comite-medico` y `criticidad=critico`;
  · charla → sin agente, como siempre;
  · un resumen con PII → nunca se ordena un LLM de fuera;
  · si la decisión revienta → el job sale igual que antes (fail-open hacia lo de hoy).
Estado aislado en un tmp; no toca la cola real ni la red.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres", "identidad")
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_triage_enrutado_")
os.environ["BTP_STATE_DIR"] = os.path.join(_TMP, "state")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import cola as q            # noqa: E402
import decide_peticion      # noqa: E402
import enruta               # noqa: E402
import triage_route         # noqa: E402

_pass = 0
_fail = 0

# Salud fija: el test no mide proveedores ni gasta red. Y si alguien refresca, se apunta.
_TODOS = {n: {"ok": True, "detalle": "test"} for n in enruta.PROVEEDORES}
enruta._cache_leer = lambda permitir_vencida=False: (dict(_TODOS), True)
_REFRESCOS = []
enruta._refrescar_de_fondo = lambda *a, **k: _REFRESCOS.append(1)


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _out(resumen, accion="investigar"):
    inner = json.dumps({"resumen": resumen, "accion": accion, "seguro": True}, ensure_ascii=False)
    return json.dumps({"result": inner, "is_error": False})


def _escalar(resumen, accion="investigar"):
    estado, det = triage_route.route(_out(resumen, accion))
    job = q.dequeue()
    if job:
        q.mark_done(job)
    return estado, det, job


def main():
    # 1. clínico → comité médico, crítico, con verificación pedida.
    estado, det, job = _escalar("interpreta el informe de FoundationOne y qué terapia encaja")
    check("clínico: escalado", estado == "escalado" and job is not None)
    check("clínico: agente comite-medico", job and job["agente"] == "comite-medico")
    check("clínico: criticidad critico", job and job["criticidad"] == "critico")
    check("clínico: pide verificacion", job and "verificacion" in job["intencion"])
    check("clínico: la traza lleva la decisión", "agente=comite-medico" in det)
    check("clínico: la traza NO lleva el texto", "FoundationOne" not in det)

    # 2. charla → sin agente, rutina (lo de siempre).
    estado, det, job = _escalar("gracias, luego lo miro", accion="otro")
    check("charla: escalado", estado == "escalado" and job is not None)
    check("charla: sin agente", job and job["agente"] is None)
    check("charla: rutina", job and job["criticidad"] == "rutina")

    # 3. búsqueda genérica en redes → orden de enruta.py en la intención.
    estado, det, job = _escalar("busca en X qué se dice de la vacuna de BioNTech")
    check("búsqueda: ordena enruta.py --ejecutar", job and "enruta.py --ejecutar" in job["intencion"])

    # 4. PII → jamás un LLM de fuera.
    estado, det, job = _escalar("busca en X qué se dice de {{TITULAR}} {{APELLIDO}}")
    check("PII: no ordena LLM de fuera", job and "enruta.py --ejecutar" not in job["intencion"])
    check("PII: la traza dice llms=-", "llms=-" in det)

    # 5. la decisión revienta → el job sale igual que antes.
    orig = decide_peticion.decidir
    decide_peticion.decidir = lambda *a, **k: 1 / 0
    try:
        estado, det, job = _escalar("interpreta el informe de FoundationOne")
    finally:
        decide_peticion.decidir = orig
    check("fallo: sigue escalando", estado == "escalado" and job is not None)
    check("fallo: sin agente (como antes)", job and job["agente"] is None)
    check("fallo: la traza lo dice", "enrutado no disponible" in det)

    # 6. un comité que no existe como agente no se encola (el dispatcher lo rompería).
    orig = decide_peticion.decidir
    decide_peticion.decidir = lambda *a, **k: {"nivel": "comite", "comites": ["comite-fantasma"],
                                               "llms": [], "sensible": False}
    try:
        estado, det, job = _escalar("algo")
    finally:
        decide_peticion.decidir = orig
    check("comité inexistente: sin agente", job and job["agente"] is None)

    # 8. la lista clínica es la misma que la de run_agent.sh (si no, la etiqueta miente).
    import re
    sh = open(os.path.join(ROOT, "tools", "run_agent.sh"), encoding="utf-8").read()
    m = re.search(r"case \"\$\{BTP_AGENT:-\}\" in\s*\n\s*([\w|-]+)\)\s*CLINICO=1", sh)
    check("run_agent.sh tiene el case clínico", m is not None)
    if m:
        check("misma lista clínica que run_agent.sh",
              set(m.group(1).split("|")) == set(triage_route.COMITES_CLINICOS))
    estado, det, job = _escalar("busca un paper sobre gatos")
    if job and job["agente"] == "verificacion":
        check("verificacion sola va como critico", job["criticidad"] == "critico")

    # 9. siglas de consumo no son genes: no mandan a comité médico.
    for txt in ("pásame el MP3 de la charla", "¿me compro la PS5 o espero a GPT5?"):
        estado, det, job = _escalar(txt, accion="otro")
        check("no es gen: %s" % txt, job and job["agente"] != "comite-medico")
    estado, det, job = _escalar("qué dice el informe de FGFR1 y TP53")
    check("un gen de verdad sí va a comité médico", job and job["agente"] == "comite-medico")

    # 10. comportamiento conocido: el resumen puede nombrar un comité y se respeta. El comité
    #     elegido nunca tiene más permisos que el muro del perfil privilegiado.
    estado, det, job = _escalar("usa comite-medico: qué tratamiento encaja")
    check("un comité nombrado en el texto se elige (diseño)", job and job["agente"] == "comite-medico")

    # 11. el triaje no abre red: nunca refresca la salud.
    check("no refresca la salud de los proveedores", _REFRESCOS == [])

    # 7. lo de siempre sigue cerrado: no-seguro no escala.
    unsafe = json.dumps({"result": '{"resumen": "x", "accion": "investigar", "seguro": false}'})
    e, _ = triage_route.route(unsafe)
    check("no-seguro: rechazado", e == "rechazado" and q.dequeue() is None)

    print("test_triage_route_enrutado: %d OK, %d fallos" % (_pass, _fail))
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
