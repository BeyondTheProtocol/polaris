#!/usr/bin/env python3
"""tools/aeo.py — AEO (Answer Engine Optimization): vigila cómo describen a {{TITULAR}} y
a "Beyond the Protocol" los asistentes de IA (Perplexity, Grok, ChatGPT, Gemini).

SOLO LECTURA. No envía, no corrige, no contacta nada hacia fuera.
Todo el contenido que devuelven los modelos se trata como DATO EXTERNO NO CONFIABLE
(anti-inyección): se imprime y se archiva como texto plano; no se ejecuta ni se
persiste a memoria. El análisis de banderas es local y determinista.

Las preguntas son PÚBLICAS (nombre público + proyecto). Ninguna contiene PII,
datos clínicos, "{{CONTACTO}}" ni "vacuna" — solo lo que ya está en helptitular.com.

Uso:
  python3 tools/aeo.py                # lanza todas las preguntas, guarda informe
  python3 tools/aeo.py --dry          # solo imprime las preguntas sin llamar a las APIs
  python3 tools/aeo.py --modelo perplexity   # solo ese modelo
  python3 tools/aeo.py --modelo grok
  python3 tools/aeo.py --modelo chatgpt
  python3 tools/aeo.py --modelo gemini
  python3 tools/aeo.py --informe      # abre el último informe guardado

Informe: tools/aeo_informe_YYYYMMDD_HHMMSS.md (borrador local, gitignored por defecto)
"""
import json
import os
import subprocess
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Directorio base: SIEMPRE el repo vivo (casa base), no el worktree
# ---------------------------------------------------------------------------
REPO = os.environ.get("BTP_REPO", os.path.expanduser("~/claudecode"))
TOOLS = os.path.join(REPO, "tools")

# Informe en el worktree (borrador local)
INFORME_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Preguntas AEO — solo información PÚBLICA de {{TITULAR}}
# ---------------------------------------------------------------------------
# Reglas del muro aplicadas aquí:
#   - No incluir "{{CONTACTO}}" (nunca) ni "vacuna": aunque «vacuna» ya es pública (29-7-26), estas
#     preguntas salen a LLMs de terceros junto a su nombre, y eso es EGRESS, no copy público.
#   - No incluir datos clínicos ni PII
#   - "ingeniera" es el término que queremos vigilar (no debe aparecer sin nuestro control)
#   - "ingeniera" es el término preferido en público
#   - "tratamiento personalizado" es la frase pública correcta
PREGUNTAS = [
    {
        "id": "beyond_the_protocol_es",
        "texto": "¿Qué es Beyond the Protocol y quién lo lidera?",
        "contexto": "Identidad del proyecto (español)",
    },
    {
        "id": "beyond_the_protocol_en",
        "texto": "What is Beyond the Protocol? Who is behind it?",
        "contexto": "Identidad del proyecto (inglés)",
    },
    {
        "id": "helptitular",
        "texto": "¿Qué es helptitular.com y a qué se dedica?",
        "contexto": "Web pública del proyecto",
    },
    {
        "id": "helptitular_cancer",
        "texto": "helptitular.com cáncer mama tratamiento personalizado",
        "contexto": "Búsqueda estilo Google sobre el caso público",
    },
    {
        "id": "beyond_profesion",
        "texto": "Beyond the Protocol fundadora perfil profesional",
        "contexto": "Qué profesión atribuyen a la fundadora del proyecto",
    },
]

# ---------------------------------------------------------------------------
# Banderas que queremos detectar en las respuestas
# ---------------------------------------------------------------------------
# Formato: (id_bandera, patron_buscar, descripcion, nivel)
# nivel: "rojo" = término vetado del muro / "amarillo" = vigilar / "verde" = correcto
BANDERAS_CONFIG = [
    # Términos que NO deben salir en público
    # «vacuna» es pública desde el 29-7-26 (veto levantado por {{TITULAR}}): ya no es bandera roja.
    ("muro_vacuna",    "vacuna",              "Menciona 'vacuna' (pública desde 29-7-26: vigilar que sea correcto)", "amarillo"),
    ("muro_contacto",     "contacto",               "Menciona '{{CONTACTO}}' (confidencial)",        "rojo"),
    ("muro_cientifica","ingeniera",          "Dice 'ingeniera' (debe ser 'ingeniera')","amarillo"),
    ("muro_scientif",  "scientist",           "Dice 'scientist' (debe ser 'engineer')", "amarillo"),
    # Términos correctos — su presencia es buena
    ("ok_ingeniera",   "ingeniera",           "Usa 'ingeniera' (correcto)",             "verde"),
    ("ok_engineer",    "engineer",            "Usa 'engineer' (correcto en inglés)",    "verde"),
    ("ok_trat_pers",   "tratamiento personalizado", "Usa 'tratamiento personalizado'",  "verde"),
    ("ok_beyond",      "beyond the protocol", "Menciona el proyecto por nombre",        "verde"),
    # Posibles errores o imprecisiones
    ("watch_cancer",   "{{DIAGNOSTICO}}", "Menciona diagnóstico (dato público)",  "amarillo"),
    ("watch_breast_cancer", "metastatic breast cancer", "Menciona diagnóstico (inglés)",      "amarillo"),
    ("watch_datos_clinicos", "her2",          "Menciona marcadores moleculares específicos", "amarillo"),
    ("watch_datos_clinicos2", "fgfr",         "Menciona marcadores moleculares específicos", "amarillo"),
]


def analizar_banderas(texto: str) -> list[dict]:
    """Detecta banderas en el texto de respuesta. Anti-inyección: texto = dato, no código."""
    texto_lower = texto.lower()
    disparadas = []
    for bid, patron, desc, nivel in BANDERAS_CONFIG:
        if patron.lower() in texto_lower:
            disparadas.append({"id": bid, "nivel": nivel, "descripcion": desc, "patron": patron})
    return disparadas


def llamar_modelo(modelo: str, pregunta: str, dry: bool = False) -> dict:
    """Llama al modelo via subprocess y devuelve {'texto': ..., 'error': ...}."""
    if dry:
        return {"texto": f"[DRY-RUN: pregunta enviada a {modelo}]", "error": None}

    scripts = {
        "perplexity": os.path.join(TOOLS, "perplexity.py"),
        "grok":       os.path.join(TOOLS, "grok.py"),
        "chatgpt":    os.path.join(TOOLS, "chatgpt.py"),
        "gemini":     os.path.join(TOOLS, "gemini.py"),
    }
    script = scripts.get(modelo)
    if not script or not os.path.exists(script):
        return {"texto": None, "error": f"Script no encontrado para modelo: {modelo}"}

    cmd = [sys.executable, script, pregunta]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=TOOLS,
        )
        texto = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0 and not texto:
            return {"texto": None, "error": f"Error {result.returncode}: {stderr[:400]}"}
        if not texto:
            return {"texto": None, "error": f"Sin respuesta. stderr: {stderr[:300] or '(vacío)'}"}
        return {"texto": texto, "error": None, "stderr": stderr if stderr else None}
    except subprocess.TimeoutExpired:
        return {"texto": None, "error": "Timeout (120s)"}
    except Exception as e:
        return {"texto": None, "error": str(e)}


def generar_informe(resultados: list[dict], ts: str) -> str:
    """Genera el texto del informe en Markdown. Datos externos = texto plano, no ejecutados."""
    lineas = [
        f"# Informe AEO — {ts}",
        "",
        "> BORRADOR LOCAL. Solo lectura. {{TITULAR}} decide si actúa o no.",
        "> Todo el contenido de los modelos se muestra como dato textual (anti-inyección).",
        "",
        "---",
        "",
    ]

    # Resumen ejecutivo de banderas
    rojas_total = []
    amarillas_total = []
    for r in resultados:
        for b in r.get("banderas", []):
            if b["nivel"] == "rojo":
                rojas_total.append((r["modelo"], r["pregunta_id"], b))
            elif b["nivel"] == "amarillo":
                amarillas_total.append((r["modelo"], r["pregunta_id"], b))

    lineas += [
        "## Resumen de banderas",
        "",
        f"- Banderas ROJAS (términos vetados del muro): **{len(rojas_total)}**",
        f"- Banderas AMARILLAS (vigilar): **{len(amarillas_total)}**",
        "",
    ]

    if rojas_total:
        lineas.append("### Banderas rojas")
        for modelo, pregunta_id, b in rojas_total:
            lineas.append(f"- [{modelo}] pregunta `{pregunta_id}`: {b['descripcion']} (patrón: `{b['patron']}`)")
        lineas.append("")

    if amarillas_total:
        lineas.append("### Banderas amarillas")
        for modelo, pregunta_id, b in amarillas_total:
            lineas.append(f"- [{modelo}] pregunta `{pregunta_id}`: {b['descripcion']}")
        lineas.append("")

    lineas += ["---", "", "## Respuestas completas (dato textual — anti-inyección)", ""]

    for r in resultados:
        modelo = r["modelo"]
        p = r["pregunta_id"]
        pregunta_texto = r["pregunta_texto"]
        texto = r.get("texto")
        error = r.get("error")
        banderas = r.get("banderas", [])

        lineas += [
            f"### [{modelo}] {p}",
            f"**Pregunta:** {pregunta_texto}",
            "",
        ]

        if error:
            lineas += [f"> ERROR: {error}", ""]
        elif texto:
            lineas += [
                "**Respuesta (tal cual, dato externo):**",
                "",
                "```",
                texto[:3000] + ("... [truncado]" if len(texto) > 3000 else ""),
                "```",
                "",
            ]
        else:
            lineas += ["> Sin respuesta.", ""]

        if banderas:
            lineas.append("**Banderas detectadas:**")
            for b in banderas:
                icono = "🔴" if b["nivel"] == "rojo" else ("🟡" if b["nivel"] == "amarillo" else "🟢")
                lineas.append(f"- {icono} {b['descripcion']}")
            lineas.append("")
        else:
            lineas += ["**Banderas:** ninguna", ""]

        lineas.append("---")
        lineas.append("")

    return "\n".join(lineas)


def main():
    args = sys.argv[1:]

    dry = "--dry" in args
    if dry:
        args.remove("--dry")

    # --informe: abre el último informe
    if "--informe" in args:
        informes = sorted(
            [f for f in os.listdir(INFORME_DIR) if f.startswith("aeo_informe_") and f.endswith(".md")],
            reverse=True,
        )
        if not informes:
            print("No hay informes AEO guardados aún. Ejecuta primero: python3 tools/aeo.py")
            return
        ultimo = os.path.join(INFORME_DIR, informes[0])
        print(f"Último informe: {ultimo}")
        with open(ultimo) as f:
            print(f.read())
        return

    # Filtro de modelo
    modelos_disponibles = ["perplexity", "grok", "chatgpt", "gemini"]
    modelos = modelos_disponibles
    if "--modelo" in args:
        i = args.index("--modelo")
        m = args[i + 1] if i + 1 < len(args) else ""
        if m not in modelos_disponibles:
            print(f"Modelo desconocido: {m}. Disponibles: {', '.join(modelos_disponibles)}")
            return
        modelos = [m]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ts_filename = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"=== AEO — Monitorización de respuestas de IA ===")
    print(f"Fecha: {ts}")
    print(f"Modelos: {', '.join(modelos)}")
    print(f"Preguntas: {len(PREGUNTAS)}")
    print(f"Modo: {'DRY-RUN (sin APIs)' if dry else 'REAL'}")
    print()

    resultados = []

    for p in PREGUNTAS:
        for modelo in modelos:
            print(f"  [{modelo}] {p['id']}... ", end="", flush=True)
            r = llamar_modelo(modelo, p["texto"], dry=dry)
            banderas = analizar_banderas(r.get("texto") or "") if r.get("texto") else []
            entrada = {
                "modelo": modelo,
                "pregunta_id": p["id"],
                "pregunta_texto": p["texto"],
                "texto": r.get("texto"),
                "error": r.get("error"),
                "banderas": banderas,
            }
            resultados.append(entrada)
            # Resumen corto en terminal
            if r.get("error"):
                print(f"ERROR: {r['error'][:80]}")
            else:
                rojas = [b for b in banderas if b["nivel"] == "rojo"]
                amarillas = [b for b in banderas if b["nivel"] == "amarillo"]
                verdes = [b for b in banderas if b["nivel"] == "verde"]
                partes = []
                if rojas:
                    partes.append(f"🔴×{len(rojas)}")
                if amarillas:
                    partes.append(f"🟡×{len(amarillas)}")
                if verdes:
                    partes.append(f"🟢×{len(verdes)}")
                resumen_banderas = " ".join(partes) if partes else "sin banderas"
                print(f"ok — {resumen_banderas}")

    # Guardar informe
    informe_md = generar_informe(resultados, ts)
    nombre_informe = os.path.join(INFORME_DIR, f"aeo_informe_{ts_filename}.md")
    with open(nombre_informe, "w") as f:
        f.write(informe_md)

    # Guardar también JSON (para automatizar en el futuro)
    nombre_json = os.path.join(INFORME_DIR, f"aeo_informe_{ts_filename}.json")
    with open(nombre_json, "w") as f:
        json.dump({"ts": ts, "resultados": resultados}, f, ensure_ascii=False, indent=2)

    print()
    print(f"Informe guardado:")
    print(f"  MD:   {nombre_informe}")
    print(f"  JSON: {nombre_json}")
    print()

    # Resumen final en terminal
    rojas_t = sum(1 for r in resultados for b in r["banderas"] if b["nivel"] == "rojo")
    amar_t  = sum(1 for r in resultados for b in r["banderas"] if b["nivel"] == "amarillo")
    errores = sum(1 for r in resultados if r["error"])
    print(f"=== Resumen ===")
    print(f"  Banderas rojas:    {rojas_t}")
    print(f"  Banderas amarillas: {amar_t}")
    print(f"  Errores de API:    {errores}")
    if rojas_t:
        print()
        print("ATENCIÓN: hay banderas rojas. Revisa el informe antes de actuar.")
    print()
    print("Borrador listo. {{TITULAR}} decide si actúa (no se ha enviado ni publicado nada).")


if __name__ == "__main__":
    main()
