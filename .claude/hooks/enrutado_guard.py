#!/usr/bin/env python3
"""enrutado_guard.py — hook UserPromptSubmit: RECLASIFICA la tarea en cada vuelta, no solo al entrar.

EL FALLO QUE LO ORIGINA (5-sep-2026, lo vio {{TITULAR}}):
  Una sesión entró por «ayúdame a interpretar esta imagen» (pregunta rápida, ejecución directa,
  clasificación correcta) y cuatro turnos después estaba haciendo el análisis exhaustivo del panel
  molecular de la metástasis ósea, que es el nodo ⭐NED del Tablero. Lo hizo un solo LLM a pelo: sin
  `comite-medico`, sin `verificacion`, sin cotejar una cita contra fuente primaria. Nada saltó.

  La causa no fue el olvido: **la clasificación se hacía una vez, al entrar, y no se revisaba
  aunque la tarea creciera**. `plan_nudge.sh` mira el ALCANCE del prompt; nadie miraba QUIÉN debía
  analizarlo. Este hook es la mitad que faltaba.

QUÉ HACE (desde el 11-sep-26, cuando {{TITULAR}} pidió que Polaris decida Y ejecute):
  1. Pasa el prompt por `tools/decide_peticion.py`: UNA decisión (directo / llm / comite / panel)
     que junta comité (`enruta_comite`) y LLM (`enruta`). Determinista, <1 s, sin red.
  2. Mira el CONTEXTO de la sesión: si ya se ABRIÓ material clínico (con una herramienta, no por
     nombrarlo), lo que venga hereda el nivel aunque la pregunta sea «¿y esto qué es?», y ningún
     LLM de fuera entra.
  3. Guarda la decisión en `tools/state/plan_enrutado/<sesion>.json` y la inyecta como ORDEN.
  4. Ignora las notificaciones del harness: no son mensajes de {{TITULAR}}.

QUIÉN HACE CUMPLIR LA ORDEN: un hook no puede lanzar un agente. El Stop
(`gate_salida.py::enrutado_incumplido`) lee el plan guardado y devuelve la respuesta si el turno no
muestra la ejecución. La decisión es de Polaris, no de {{TITULAR}} (su regla del 11-sep-26).

FAIL-OPEN sin excepciones: cualquier error sale 0 y calla. Bypass con BTP_ENRUTADO_OFF=1.
"""
import json
import os
import re
import sys

# El repo de LA SESIÓN, no casa base. Es la diferencia con `traza_subagente.py`: aquél escribe
# estado vivo (uno solo para todo el sistema) y por eso va siempre a casa base; éste LEE código y
# fichas de comité, y tiene que leer las del worktree donde se está trabajando. Si no, un worktree
# no puede probar su propia versión del enrutador y el hook queda mudo sin que nadie se entere.
AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = (os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("BTP_REPO")
        or os.path.dirname(os.path.dirname(AQUI)))

# Cuánto transcript se mira hacia atrás. Suficiente para ver el contexto de la sesión sin leerse
# medio megabyte en cada tecla.
MAX_LINEAS = 4000

# Una orden que EJECUTA la ventanilla clínica o abre el permiso clínico del muro. Anclada al inicio
# de un comando (tras `;`, `&&`, `|`, `(` o salto de línea), para que un texto entre comillas que
# la mencione no cuente.
USO_CLINICO = re.compile(
    r"(?:^|[;&|(\n])\s*(?:[A-Z_]+=\S*\s+)*(?:python3?\s+)?(?:\S*/)?tools/lector_clinico\.py\b"
    r"|(?:^|[;&|(\n\s])MURO_ALLOW_CLINICAL=1\b")


def _decidir(prompt):
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import enruta_comite
    return enruta_comite.decidir(prompt)


def _lineas_transcript(path):
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-MAX_LINEAS:]
    except Exception:
        return []


def _entradas_de_tools(lineas):
    """Los `input` de las herramientas que se USARON de verdad (tool_use del asistente).

    Solo eso (11-sep-26). Antes se buscaba texto suelto en la línea, y bastaba con que yo
    ESCRIBIERA «lector_clinico» explicando el sistema, o que saliera en el `cat` de un fichero,
    para declarar clínica una sesión que no había abierto nada clínico."""
    for ln in lineas:
        if '"tool_use"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        contenido = (d.get("message") or {}).get("content")
        if d.get("type") != "assistant" or not isinstance(contenido, list):
            continue
        for x in contenido:
            if isinstance(x, dict) and x.get("type") == "tool_use":
                yield x.get("name") or "", x.get("input") or {}


def _contexto_clinico(lineas):
    """¿Esta sesión ya ha ABIERTO material clínico? Solo cuenta lo que hizo una herramienta."""
    if not lineas:
        return False
    sys.path.insert(0, AQUI)
    try:
        import zonas_clinicas
    except Exception:
        zonas_clinicas = None

    for _nombre, entrada in _entradas_de_tools(lineas):
        for campo in ("file_path", "command", "pattern", "path"):
            valor = entrada.get(campo)
            if not isinstance(valor, str):
                continue
            # EJECUTAR la ventanilla, no nombrarla: un commit o un `deuda.py abrir "…"` que
            # hablan de ella no han abierto nada (visto en la prueba real del 11-sep-26).
            if campo == "command" and USO_CLINICO.search(valor):
                return True
            if zonas_clinicas is None:
                continue
            # Se usa `es_ruta_clinica` (un path suelto) y no `rutas_clinicas_en_tokens`, que espera
            # una lista de tokens ya troceada y devuelve vacío con un path entero.
            try:
                if zonas_clinicas.es_ruta_clinica(valor[:400]):
                    return True
            except Exception:
                continue
    return False


def _comites_ya_trabajados(lineas):
    """Comités invocados en ESTA sesión, leídos del transcript.

    A propósito NO se consulta `observabilidad` («¿corrió hoy?»): que el comité médico corriera
    esta mañana sobre la agenda de prensa no dice nada sobre el informe que se está mirando ahora.
    Usarlo como prueba de trabajo hecho metía justo el falso negativo que este hook viene a
    eliminar.
    """
    return {e.get("subagent_type") for _n, e in _entradas_de_tools(lineas)
            if isinstance(e.get("subagent_type"), str)}


def linea_tool(nombre, entrada):
    """Una línea de transcript con un tool_use, en el formato real del harness (para tests)."""
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": nombre, "input": entrada}]}})


def _selftest():
    """Comprueba las piezas SIN la red fail-open, que si no un ImportError deja el hook mudo y
    nadie se entera. Es justo el modo de fallo que tuvo este fichero el día que se escribió."""
    d = _decidir("analiza el informe molecular de hueso y dime qué dianas ves")
    assert d["obligatorio"], "un análisis molecular tiene que exigir comité"
    assert "comite-medico" in d["comites"] and "verificacion" in d["comites"], d
    assert not _decidir("qué tal el día")["obligatorio"], "no puede saltar con cualquier cosa"
    # La cadena se compone a trozos a propósito: escrita entera, este mismo fichero dispararía el
    # `clinico_guard` cada vez que alguien corriera el test desde bash.
    zona = "_PRIVADO_" + "CLINICO"
    assert _contexto_clinico([linea_tool("Read", {"file_path": "01 · Tratamiento/%s/x.md" % zona})]), \
        "no detecta que la sesión ya está en material clínico"
    assert not _contexto_clinico([linea_tool("Read", {"file_path": "README.md"})])
    texto = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "el guard obliga a pasar por lector_" + "clinico.py"}]}})
    assert not _contexto_clinico([texto]), "nombrar la ventanilla no es abrir material clínico"
    assert "comite-medico" in _comites_ya_trabajados(
        [linea_tool("Agent", {"subagent_type": "comite-medico"})])
    print("enrutado_guard --selftest: OK")
    return 0


def main():
    if os.environ.get("BTP_ENRUTADO_OFF") == "1":
        return 0
    if sys.argv[1:2] == ["--selftest"]:
        return _selftest()
    try:
        datos = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    prompt = (datos.get("prompt") or datos.get("user_input") or "").strip()
    if not prompt:
        return 0

    sys.path.insert(0, os.path.join(REPO, "tools"))
    import decide_peticion as dp
    sesion = str(datos.get("session_id") or datos.get("transcript_path") or "sin-id")
    if dp.es_del_sistema(prompt):
        # Una notificación del harness NO es un mensaje de {{TITULAR}}: ni decide ni pisa el plan del
        # turno en curso (el Stop de este turno sigue exigiendo lo que ella pidió).
        return 0

    lineas = _lineas_transcript(datos.get("transcript_path"))
    ya = _comites_ya_trabajados(lineas)
    # Herencia de contexto: si la sesión ya abrió material clínico y el comité aún no ha tocado
    # nada, lo que venga hereda el nivel. Si ya trabajó en esta sesión, no se le vuelve a exigir
    # por herencia (sí si la propia petición lo pide: eso decide el texto, no el contexto).
    clinica = _contexto_clinico(lineas)
    heredado = clinica and not {"comite-medico", "verificacion"} <= ya
    d = dp.decidir(prompt, contexto_clinico=heredado, sesion_sensible=clinica)
    dp.guardar(sesion, d)
    if d["nivel"] == dp.DIRECTO:
        return 0

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": d["orden"],
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        sys.exit(main())                    # aquí SÍ queremos que un fallo se vea
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
