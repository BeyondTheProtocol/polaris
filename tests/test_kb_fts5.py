#!/usr/bin/env python3
"""Tests-gate del muro para kb.py (backend FTS5 del RAG).

Sintético y rápido: construye un índice FTS5 temporal con pasajes de prueba (incluido
uno CLÍNICO mal etiquetado como 'public') y verifica que el gate de sensibilidad
—que RECOMPUTA la sensibilidad sobre el CONTENIDO devuelto— nunca sirve PII clínica a
un scope que no debe verla. Fail-closed: si algo clínico se cuela, el test FALLA.

Correr:  python3 tests/test_kb.py
No requiere el corpus real ni red. Exit 0 = verde.
"""
import io, os, sys, sqlite3, tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import kb  # noqa: E402
from kb import _sensitivity  # noqa: E402

# Pasajes sintéticos. NO son datos reales de {{TITULAR}}: cadenas de laboratorio genéricas
# para ejercitar los regex del muro (HGVS canónico, gen+lenguaje clínico).
# El muro reserva 'private' para PII FUERTE: mutación HGVS (p.Val600Glu), teléfono,
# y rutas _PRIVADO_. El perfil molecular publicable (FGFR1 amplificado) es 'internal'
# A PROPÓSITO (tesis abierta) — no es fuga servirlo a agentes internos, solo al público.
DOCS = [
    # (path, sensitivity_ETIQUETADA, body)   ← la etiqueta puede MENTIR a propósito
    ("04 · IA/proyecto.md", "internal",
     "El proyecto Beyond the Protocol busca una vacuna de peptidos contra el tumor."),
    ("01 · Tratamiento/informe.md", "private",
     "Resultado de la biopsia: se detecta la mutacion p.Val600Glu en el analisis."),
    ("00 · Bandeja/correo.md", "internal",   # FGFR1 = perfil molecular público a propósito
     "El gen FGFR1 aparece amplificado segun el informe de la biopsia clinica."),
    # TRAMPA: PII FUERTE (HGVS) pero ETIQUETADA 'public' → el escaneo de contenido
    # DEBE reclasificarla como private y bloquearla en public E internal.
    ("04 · IA/nota-mal-etiquetada.md", "public",
     "Nota suelta: la mutacion p.Val600Glu confirma el diagnostico. palabraclave zorbax."),
    ("Mails/contacto.md", "private",
     "Llamame al +34 600 123 456 para hablar del caso."),
]

# Marcadores de PII FUERTE que NUNCA pueden salir a public/internal (no FGFR1/biopsia,
# que son 'internal' publicable a propósito).
CLINICAL_MARKERS = ("p.val600glu", "600 123 456")


def _build_temp_index(dbpath):
    con = sqlite3.connect(dbpath)
    con.execute(kb._SCHEMA)
    con.executemany(
        "INSERT INTO chunks(path, title, sensitivity, body) VALUES (?,?,?,?)",
        [(p, os.path.basename(p), s, b) for (p, s, b) in DOCS])
    con.commit()
    con.close()


def _ask(q, scope, k=10):
    buf = io.StringIO()
    with redirect_stdout(buf):
        kb.ask(q, k=k, scope=scope)
    return buf.getvalue().lower()


def _leaks_clinical(out):
    return [m for m in CLINICAL_MARKERS if m in out]


def main():
    fails = []

    # Precondición: el muro clasifica el CONTENIDO como private por PII fuerte,
    # AUNQUE la etiqueta diga otra cosa (la nota mal-etiquetada 'public' con HGVS incluida).
    expect_private = ("01 · Tratamiento/informe.md",
                      "04 · IA/nota-mal-etiquetada.md",
                      "Mails/contacto.md")
    for path, _label, body in DOCS:
        sens = _sensitivity(path, body)
        if path in expect_private and sens != "private":
            fails.append(f"[precond] PII fuerte NO clasificada private: {path} -> {sens}")

    tmp = tempfile.mkdtemp()
    kb.DB = os.path.join(tmp, ".kb_index.db")
    _build_temp_index(kb.DB)

    # (1) scope=public: NADA clínico puede salir.
    for q in ("mutacion", "biopsia", "fgfr1", "vacuna proyecto", "zorbax"):
        leaked = _leaks_clinical(_ask(q, "public"))
        if leaked:
            fails.append(f"[public] fuga clínica en query '{q}': {leaked}")

    # (2) scope=internal: todo menos private → sin PII fuerte. Nota: 'biopsia'/'mutacion'
    #     también matchean el doc HGVS PRIVATE, que DEBE quedar bloqueado.
    for q in ("mutacion", "biopsia", "zorbax", "contacto llamame"):
        leaked = _leaks_clinical(_ask(q, "internal"))
        if leaked:
            fails.append(f"[internal] fuga de PII fuerte en query '{q}': {leaked}")

    # (3) TRAMPA fila mal-etiquetada: la palabra única 'zorbax' vive en un doc
    #     etiquetado 'public' pero con PII. NO debe salir en public ni internal.
    for scope in ("public", "internal"):
        out = _ask("zorbax", scope)
        if "zorbax" in out or _leaks_clinical(out):
            fails.append(f"[mal-etiquetada] doc clínico etiquetado 'public' se sirvió en scope={scope}")

    # (4) Contraprueba: con scope=all (agente clínico) SÍ se recupera lo clínico.
    out = _ask("mutacion", "all")
    if "p.val600glu" not in out:
        fails.append("[all] el agente clínico (scope=all) NO recuperó el pasaje clínico esperado")

    # (5) El doc de proyecto (no clínico) SÍ sale en internal.
    if "beyond the protocol" not in _ask("vacuna proyecto", "internal"):
        fails.append("[internal] el doc de proyecto no-clínico no se recuperó")

    # (6) Contraprueba de diseño: el perfil molecular publicable (FGFR1) SÍ se sirve
    #     a scope=internal (es 'internal' a propósito, no PII a bloquear).
    if "fgfr1" not in _ask("fgfr1 amplificado", "internal"):
        fails.append("[internal] el perfil molecular público (FGFR1) no se recuperó en internal")

    print("=" * 60)
    if fails:
        print(f"❌ FALLO — {len(fails)} problema(s):")
        for f in fails:
            print("   -", f)
        sys.exit(1)
    print("✅ VERDE — el muro FTS5 bloquea PII clínica en todos los casos,")
    print("   incluida la fila mal-etiquetada; scope=all sí la recupera.")
    sys.exit(0)


if __name__ == "__main__":
    main()
