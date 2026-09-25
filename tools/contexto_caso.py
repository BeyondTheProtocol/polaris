#!/usr/bin/env python3
"""tools/contexto_caso.py — Cableado de RECUPERACIÓN de contexto para el cerebro GRATIS/LOCAL (F3b).

Plan-motor: typed-swinging-wand / F3b. El NÚCLEO de la fase: cuando una pregunta de CASO va al cerebro
GRATIS/LOCAL (Ollama, egress-cero), recibe contexto del caso REDACTADO (de-id como defensa en
profundidad), y la AUTORIDAD de egress es el BORDE. Pipeline:

    pregunta → kb LOCAL (BM25, scope private)  → de-id REDACTA (deid.py)
             → ensambla contexto → ia.ask(solo_gratis) → BORDE decide → cerebro local/gratis

REUSA, NO REINVENTA:
  · Recuperación: `kb.py` (BM25 en Python puro, índice LOCAL `.kb_index.json`). Se reusa su índice y
    su tokenizador/scoring (SOLO LECTURA); aquí solo se añade un acceso PROGRAMÁTICO (kb.ask imprime).
  · De-id: `deid.py` (que a su vez reusa las regex/deny-lists del borde, SOLO LECTURA).
  · Egress: el cerebro local es destino `local:` (de confianza, egress-cero) — para ÉL el caso NUNCA
    sale de la máquina. El cableado a `ia.ask` pasa por el BORDE, que es la AUTORIDAD de egress:
    PERMITE sensible→destino de confianza (local/cleared) y DENIEGA sensible→no-confiable (nube). Lo
    que necesita JUICIO CLÍNICO sigue yendo a Claude (clinico=True) — esto NO lo cambia.

OPCIÓN A (F3b/F3b-opción-A): el carril LOCAL ya NO se pre-bloquea por identificadores residuales.
Antes, `construir_contexto` REVALIDABA el ensamblado con el juez del muro y VACIABA la respuesta si
quedaban restos (nombres/fechas en TÍTULOS o rutas) → sobre el caso real descartaba TODO, respuesta
inútil. Ese pre-bloqueo estricto era REDUNDANTE para el local (egress-cero + el borde ya manda) y era
lo que lo rompía. Ahora: se SIGUE redactando (defensa en profundidad + limpieza), pero el carril local
NO descarta por residuos; la decisión de egress la toma `ia.ask`+el BORDE. Para destinos no-confiables
NADA se debilita: el borde sigue DENEGANDO sensible→nube. A no abre ninguna vía de fuga.

EMBEDDINGS: ninguno de nube. Si hicieran falta, SOLO local (BGE-M3, air-gap por `borde.embedding_
egress_check`). El BM25 local de kb.py basta para este núcleo → no se añade ningún embedding de nube.

GARANTÍA: la de-id por patrones es porosa por naturaleza (no es la garantía única). La garantía REAL
es ARQUITECTÓNICA: el cerebro sensible es Claude/local de confianza, y el BORDE bloquea cualquier
salida sensible hacia un destino no-confiable. La de-id reduce superficie; el borde cierra el egress.

CLI:
  python3 tools/contexto_caso.py "pregunta de caso"            # muestra el contexto de-identificado
  python3 tools/contexto_caso.py --ask "pregunta de caso"      # + responde con el cerebro GRATIS/LOCAL
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deid  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Scopes de ACCESO PLENO a la KB del caso (los mismos que `kb._allowed` deja ver todo). Lo que sale
# de aquí es N2 POR PROCEDENCIA, se detecte algo o no (24-sep-26, auditoría externa 3.3).
_SCOPES_CASO = ("private", "all", "clinico")


def _cargar_kb():
    """Importa kb.py (vive en el mismo tools/) sin ejecutar su CLI. SOLO LECTURA del índice."""
    import importlib
    return importlib.import_module("kb")


def recuperar_pasajes(pregunta, k=6, scope="private", index_path=None):
    """[(path, title, text, score), …] — los k pasajes más relevantes del índice LOCAL de kb,
    reusando su recuperación y su MURO. `scope='private'` = acceso pleno al caso (es el cerebro de
    la cajita; la de-id posterior es la que protege la salida). Devuelve [] si no hay índice.

    Delega en `kb.retrieve`, que despacha por backend: en producción (index_path=None) usa el
    índice FTS5 on-disk; con un `index_path=*.json` usa el lector BM25 legado (tests/callers)."""
    kb = _cargar_kb()
    return kb.retrieve(pregunta, k=k, scope=scope, db_path=index_path)


def construir_contexto(pregunta, k=6, scope="private", max_chars=4000, index_path=None,
                       pre_bloqueo=True):
    """dict {contexto, pasajes, descartados, procedencia, sin_identificadores_detectados, motivo}.
    Recupera → de-identifica (REDACTA) cada pasaje → ensambla.

    `procedencia` = "N2" si entra al menos un pasaje de la KB del caso con acceso pleno, "" si no.
    ESA es la señal que manda para el egress: quien llame a `ia.ask` con este contexto debe pasar
    `sensible_forzado=True` cuando sea "N2". `sin_identificadores_detectados` es informativo y NO
    certifica anonimato: redacta y verifica con los mismos patrones (auditoría 3.3, 24-sep-26:
    un nombre + domicilio + diagnóstico ficticios dieron 0 redacciones). `limpio` queda como alias
    deprecado de esa misma clave.

    `pre_bloqueo` (F3b/opción A):
      · True (defecto, carriles que NO son el local de confianza): además de redactar, REVALIDA cada
        pasaje con el juez del muro y DESCARTA el que no quede limpio (fail-closed). El `contexto`
        devuelto está GARANTIZADO limpio. Comportamiento histórico.
      · False (carril LOCAL/egress-cero de confianza): SIGUE redactando todos los pasajes (defensa en
        profundidad + limpieza), pero NO descarta por identificadores residuales (p. ej. nombre/fecha
        en un TÍTULO o ruta). El texto redactado se entrega tal cual; la AUTORIDAD de egress pasa a
        ser `ia.ask` + el BORDE (local de confianza = permite; no-confiable = el borde DENIEGA). Así el
        local deja de pre-bloquearse a sí mismo, sin abrir ninguna vía hacia un cerebro de nube."""
    pasajes = recuperar_pasajes(pregunta, k=k, scope=scope, index_path=index_path)
    trozos, incluidos, descartados, usados = [], [], [], 0
    for path, title, text, sc in pasajes:
        deid_txt, n, ok, motivo = deid.de_identificar_verificado(
            text, procedencia=("N2" if scope in _SCOPES_CASO else None))
        if pre_bloqueo and (not ok or deid_txt is None):
            # carril no-confiable: descarta lo que no quede limpio (fail-closed histórico)
            descartados.append({"path": path, "title": title, "motivo": motivo})
            continue
        if deid_txt is None:
            # carril local: NO descartamos; usamos el texto REDACTADO igualmente (deid() nunca
            # devuelve None, solo de_identificar_verificado lo hace al fallar el juez).
            deid_txt, n = deid.de_identificar(text)
        if usados + len(deid_txt) > max_chars and trozos:
            break
        trozos.append("[fuente: %s › %s]\n%s" % (path, title, deid_txt.strip()))
        usados += len(deid_txt)
        incluidos.append({"path": path, "title": title, "score": round(sc, 2), "redacciones": n})
    contexto = "\n\n---\n\n".join(trozos)
    ok_final, motivo_final = (deid.sin_identificadores_detectados(contexto) if contexto
                              else (True, "sin contexto"))
    procedencia = "N2" if (incluidos and scope in _SCOPES_CASO) else ""
    return {"contexto": contexto, "pasajes": incluidos, "descartados": descartados,
            "procedencia": procedencia,
            "sin_identificadores_detectados": ok_final, "limpio": ok_final,   # `limpio`: deprecado
            "motivo": motivo_final}


_SYSTEM_LOCAL = ("Eres un asistente que responde SOLO con el CONTEXTO de-identificado que se te da. "
                 "El contexto puede contener marcadores [REDACTADO] donde se ha ocultado información "
                 "sensible; no intentes adivinar lo que hay debajo. No das consejo médico: solo "
                 "organizas y resumes lo que aparece en el contexto. Responde en español llano.")


def preguntar_local(pregunta, k=6, scope="private", index_path=None):
    """Responde la pregunta de caso con el cerebro GRATIS/LOCAL (Ollama, egress-cero, destino
    `local:` de CONFIANZA), alimentándolo con contexto REDACTADO (de-id como defensa en profundidad).
    dict {respuesta, brain, contexto, pasajes, descartados, enviado_limpio}.

    OPCIÓN A (F3b): el carril local NO se pre-bloquea por identificadores residuales (nombre/fecha en
    un título o ruta). El cerebro local es egress-cero: el caso nunca sale de la máquina. La AUTORIDAD
    de egress es `ia.ask` + el BORDE: para un destino de confianza (`local:`) PERMITE el contenido
    sensible; para uno NO confiable (un cerebro de nube) el borde lo DENIEGA. Por eso aquí pasamos el
    contexto redactado tal cual y dejamos que el borde decida — el guard de fuga sigue intacto.
    Lo que pide JUICIO CLÍNICO real NO va por aquí — eso es `ia.ask(clinico=True)` (→ Claude)."""
    # pre_bloqueo=False: redacta pero NO descarta por residuos (el borde es la autoridad de egress).
    ctx = construir_contexto(pregunta, k=k, scope=scope, index_path=index_path, pre_bloqueo=False)
    import ia  # noqa: E402 — la centralita; el BORDE es la barrera de egress (autoridad real)
    prompt = ("CONTEXTO (de-identificado):\n%s\n\nPREGUNTA: %s\n\n"
              "Responde usando solo el contexto." %
              (ctx["contexto"] or "(sin contexto recuperado)", pregunta))
    # solo_gratis=True: este carril NO debe gastar Claude de pago; es el cerebro gratis/local. El
    # borde, dentro de ia.ask, deja pasar el contenido al destino local (confianza) y DENIEGA cualquier
    # destino de nube no-confiable (p. ej. el carril nvidia gratis) si el contexto es sensible.
    # Procedencia (auditoría 3.3): «solo_gratis» incluye el carril gratis de NUBE (nvidia), que no
    # es de confianza. Un pasaje del caso que el detector no reconoce iba a poder salir por ahí; con
    # `sensible_forzado` solo queda el local de confianza, que es lo que este carril promete.
    r = ia.ask(prompt, clinico=False, system=_SYSTEM_LOCAL, solo_gratis=True,
               sensible_forzado=(ctx.get("procedencia") == "N2"))
    respondio = r.get("text") is not None
    return {"respuesta": r.get("text"), "brain": r.get("brain"), "contexto": ctx["contexto"],
            "pasajes": ctx["pasajes"], "descartados": ctx["descartados"],
            "procedencia": ctx.get("procedencia"),
            # `enviado_limpio` medía «el cerebro contestó», no limpieza: se queda como alias deprecado.
            "respondio": respondio, "enviado_limpio": respondio, "motivo": r.get("motivo")}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--ask":
        r = preguntar_local(" ".join(argv[1:]))
        print("cerebro:", r["brain"], "| respondió:", r["respondio"], "| procedencia:", r["procedencia"] or "—")
        print("pasajes:", r["pasajes"])
        if r["descartados"]:
            print("descartados (quedaban identificadores):", r["descartados"])
        print("\n--- RESPUESTA ---\n", r["respuesta"])
        return 0
    ctx = construir_contexto(" ".join(argv))
    print("sin identificadores detectados:", ctx["sin_identificadores_detectados"], "(%s)" % ctx["motivo"],
          "· procedencia:", ctx["procedencia"] or "—", "(esto NO certifica anonimato)")
    print("pasajes incluidos:", ctx["pasajes"])
    if ctx["descartados"]:
        print("descartados (quedaban identificadores):", ctx["descartados"])
    print("\n--- CONTEXTO DE-IDENTIFICADO ---\n", ctx["contexto"] or "(vacío)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
