#!/usr/bin/env python3
"""test_cosecha_correcciones.py — el minero determinista de correcciones de {{TITULAR}}.

Aísla TODO en un tmp (BTP_PROJECTS_DIR + BTP_MEMORY_DIR) y verifica el contrato:
  · un mensaje de {{TITULAR}} (claude-desktop) que corrige → aparece como candidato;
  · una lección que YA está en una memoria existente → NO se duplica (dedup);
  · el ruido se ignora: tool_result, [Request interrupted], <task-notification>,
    bloques DATOS/HOY, mensajes de `sdk-cli` (tareas/agentes) y sub-agentes (sidechain);
  · la ventana temporal corta lo viejo y --todo lo incluye;
  · es OFFLINE/FAIL-SOFT (un .jsonl corrupto no rompe el barrido) y NO escribe memoria.
"""
import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="test_cosecha_")
_PROJ = os.path.join(_TMP, "projects", "-Users-polaris-claudecode")
_MEM = os.path.join(_TMP, "memory")
os.makedirs(_PROJ, exist_ok=True)
os.makedirs(_MEM, exist_ok=True)
os.environ["BTP_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ["BTP_MEMORY_DIR"] = _MEM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cosecha_correcciones as cc  # noqa: E402

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def _ev(text, ts, entrypoint="claude-desktop", content=None, **extra):
    """Construye un evento de transcript tipo 'user'."""
    o = {
        "type": "user",
        "entrypoint": entrypoint,
        "timestamp": ts,
        "message": {"role": "user", "content": content if content is not None else text},
    }
    o.update(extra)
    return o


def _write_jsonl(nombre, eventos):
    with open(os.path.join(_PROJ, nombre), "w", encoding="utf-8") as f:
        for e in eventos:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# Fechas: una reciente (dentro de la ventana de 2 días) y una vieja.
import datetime  # noqa: E402
_now = datetime.datetime.now(datetime.timezone.utc)
TS_HOY = (_now - datetime.timedelta(hours=2)).isoformat().replace("+00:00", "Z")
TS_VIEJO = (_now - datetime.timedelta(days=30)).isoformat().replace("+00:00", "Z")


def main():
    # --- Memoria ya existente: una lección sobre "no usar guion largo como muletilla" ---
    with open(os.path.join(_MEM, "feedback-no-em-dash-tell-ia.md"), "w", encoding="utf-8") as f:
        f.write("# No usar guion largo como muletilla\n\n"
                "{{TITULAR}} corrigió: el guion largo em-dash suena a IA, evitarlo en español, "
                "escribir asimétrico y coloquial.\n")

    # --- Transcript con señal real + ruido variado ---
    _write_jsonl("sesion-real.jsonl", [
        # 1) Corrección real de {{TITULAR}} (rechazo + preferencia) → DEBE salir
        _ev("No, así no. En realidad prefiero que el resumen del viaje vaya en Notion, "
            "no en un PDF, que lo tengo más a mano.", TS_HOY),
        # 2) Regla nueva → DEBE salir
        _ev("A partir de ahora nunca pongas importes de recaudación en mensajes que salgan fuera.",
            TS_HOY),
        # 3) Lección que YA está en memoria (guion largo) → NO debe duplicarse
        _ev("Recuerda que el guion largo em-dash suena a IA, evítalo en español y escribe "
            "más coloquial y asimétrico.", TS_HOY),
        # 4) Mensaje neutro sin señal → NO sale
        _ev("Vale, sigue con eso entonces.", TS_HOY),
        # 5) tool_result disfrazado de user → NO sale
        _ev("", TS_HOY, content=[{"type": "tool_result", "tool_use_id": "x",
                                   "content": "no me convence el resultado", "is_error": False}]),
        # 6) interrupción de control → NO sale
        _ev("[Request interrupted by user]", TS_HOY),
        # 7) bloque inyectado <task-notification> → NO sale aunque tenga "deja de"
        _ev("<task-notification>\n<task-id>abc</task-id>\n deja de esperar, ya está\n</task-notification>",
            TS_HOY),
        # 8) parte automático de HOY → NO sale
        _ev("## ☀️ HOY EN 30 SEGUNDOS\n- nunca olvides la biopsia", TS_HOY),
        # 9) aviso de hook inyectado como user → NO sale (reglas_repetidas lo contaba como {{TITULAR}})
        _ev("Stop hook feedback:\n[gate_salida.py]: corrige esto, a partir de ahora nunca pongas citas",
            TS_HOY),
        _ev("PreToolUse hook did not allow: no es así, mejor otra ruta", TS_HOY),
    ])

    # --- Transcript de una tarea/agente (sdk-cli): NO es {{TITULAR}} aunque tenga señal ---
    _write_jsonl("sesion-agente.jsonl", [
        _ev("No es así, corrige el pipeline.", TS_HOY, entrypoint="sdk-cli"),
        # sub-agente (sidechain) en claude-desktop tampoco cuenta
        _ev("Mejor reescribe esto otra vez.", TS_HOY, isSidechain=True),
    ])

    # --- Transcript con una corrección VIEJA (fuera de ventana) ---
    _write_jsonl("sesion-vieja.jsonl", [
        _ev("No me gusta el color del botón, cámbialo.", TS_VIEJO),
    ])

    # --- Fichero corrupto: el barrido no debe caerse (fail-soft) ---
    with open(os.path.join(_PROJ, "corrupto.jsonl"), "w", encoding="utf-8") as f:
        f.write("{esto no es json valido\n")
        f.write(json.dumps(_ev("No, en realidad prefiero el tren al avión.", TS_HOY),
                           ensure_ascii=False) + "\n")

    # ============================ COSECHA (ventana 2 días) ============================
    cands = cc.cosechar(dias=2)
    textos = [c["texto"] for c in cands]

    check("captura la corrección real (Notion vs PDF)",
          any("Notion" in t and "prefiero" in t for t in textos))
    check("captura la regla nueva (importes recaudación)",
          any("recaudación" in t for t in textos))
    check("NO duplica la lección ya en memoria (guion largo)",
          not any("guion largo" in t for t in textos))
    check("ignora el mensaje neutro sin señal",
          not any(t.strip() == "Vale, sigue con eso entonces." for t in textos))
    check("ignora tool_result disfrazado", not any("no me convence el resultado" in t for t in textos))
    check("ignora [Request interrupted]", not any(t.startswith("[Request") for t in textos))
    check("ignora <task-notification>", not any("<task-notification>" in t for t in textos))
    check("ignora el parte de HOY", not any("HOY EN 30 SEGUNDOS" in t for t in textos))
    check("ignora avisos de hooks (Stop/PreToolUse)",
          not any("hook" in t.lower() for t in textos))
    check("ignora mensajes de sdk-cli (tareas/agentes)",
          not any("corrige el pipeline" in t for t in textos))
    check("ignora sub-agente (sidechain)", not any("reescribe esto otra vez" in t for t in textos))
    check("la corrección VIEJA queda fuera de la ventana",
          not any("color del botón" in t for t in textos))
    check("fail-soft: el .jsonl corrupto no rompe; recupera la línea buena que sí vale",
          any("tren al avión" in t for t in textos))

    # candidatos llevan metadata útil para el agente
    if cands:
        c0 = cands[0]
        check("cada candidato lleva señales etiquetadas", isinstance(c0.get("senales"), list) and c0["senales"])
        check("cada candidato lleva fecha y sesión", bool(c0.get("fecha")) and bool(c0.get("sesion")))

    # ============================ VENTANA --todo ============================
    todos = cc.cosechar(incluir_todo=True)
    textos_todo = [c["texto"] for c in todos]
    check("--todo SÍ incluye la corrección vieja", any("color del botón" in t for t in textos_todo))

    # ============================ NO escribe memoria ============================
    antes = set(os.listdir(_MEM))
    cc.cosechar(dias=2)
    despues = set(os.listdir(_MEM))
    check("el tool NO crea ni toca memorias (solo propone)", antes == despues)

    print("RESULTADO cosecha-correcciones: %d OK, %d fallos" % (_pass, _fail))
    print("✅ COSECHA-CORRECCIONES EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


if __name__ == "__main__":
    sys.exit(main())
