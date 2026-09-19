#!/usr/bin/env python3
"""test_borde.py — evals del BORDE no-bypassable (F0-mínima del motor, plan typed-swinging-wand).

Verifica los invariantes ROCA, en estado AISLADO (BTP_STATE_DIR temporal, HALT y canarios por
env) para no tocar el sistema vivo:
  1. FAIL-CLOSED: clínico/genómico/PII/término vetado → DENY a destino no confiable; ALLOW a
     trusted (local). Forzar untrusted con dato sensible → rechaza.
  2. VÁLVULA de declassificación: deny-by-default + tipo + presupuesto + "el valor sigue limpio".
  3. AIR-GAP de embeddings: API externa → DENY; modelo local a destino local → ALLOW.
  4. TRAZA hash-chained: íntegra tras operar; un borrado/alteración la rompe (detectado).
  5. REVOCACIÓN + anti-replay: sesión revocada → DENY.
  6. CANARIOS: canario en la salida → DENY + alarma.
  7. RESILIENCIA (run_agent.sh): límite de capacidad → degrada Opus→Sonnet→Haiku; cadena
     agotada → aplaza (no muere).
Estilo igual que test_muro_fase0: cada caso suma OK/fallo; exit = nº de fallos.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _entorno import exige as _exige
_exige("nombres", "identidad")
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Estado AISLADO + canario + HALT inexistentes ANTES de importar borde (lee STATE al importar).
_TMP = tempfile.mkdtemp(prefix="borde_test_")
os.environ["BTP_STATE_DIR"] = _TMP
os.environ["BTP_HALT_FILES"] = os.path.join(_TMP, "no_halt_a") + ":" + os.path.join(_TMP, "no_halt_b")
os.environ["BTP_CANARIOS"] = "CANARIO-XYZZY-7788"
os.environ["BTP_BORDE_PRESUPUESTO"] = "3"
sys.path.insert(0, os.path.join(ROOT, "tools"))
import borde  # noqa: E402

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def main():
    # ── 1. FAIL-CLOSED por sensibilidad ──────────────────────────────────────────────────
    sensibles = [
        ("clínico marcador", "El valor de KI-67 fue muy alto"),
        ("genómico variante", "se observó la variante p.Arg175His"),
        ("genómico rsID", "el SNP rs12345678 aparece"),
        ("genómico HLA", "tipo HLA-A*02:01"),
        ("genotipo VCF", "el genotipo es 0/1 en esa posición"),
        ("término vetado", "el plan de la vacuna personalizada"),
        ("PII {{TITULAR}}", "el caso de {{TITULAR}} {{APELLIDO}}"),
        ("PII nombre deny", "hablé con {{CONTACTO}} ayer"),
        ("PII email", "escríbeme a alguien@ejemplo.com"),
        ("PII teléfono", "mi número es +34 600 123 456"),
        # 2-sep-2026: faltaba, y era una fuga CON sello de aprobación. `deid.py` se
        # autoverifica contra clasificar(), así que un NHC salía intacto Y marcado LIMPIO.
        ("PII NHC", "paciente con NHC 999001 en seguimiento"),
        ("PII NHC con dos puntos", "NHC: 999001"),
        ("PII num. historia", "el número de historia 999001 consta en el archivo"),
        ("PII historia clínica", "historia clinica 999001"),
        # 2-sep-2026: el SEGUNDO apellido faltaba. Fuera de España se toma el último apellido
        # como EL apellido, así que «patient Perez» en un informe de Zúrich o de Boston pasaba
        # como limpio.
        ("PII apellido suelto", "la paciente Perez acude a consulta"),
        ("PII apellido con tilde", "Sra. Pérez"),
        ("PII apellido en informe extranjero", "patient Perez, follow-up visit"),
    ]
    for name, txt in sensibles:
        ok(not borde.egress_check(txt, destino="nvidia").permitido, "DENY untrusted: %s" % name)
        # el MISMO dato sensible a destino TRUSTED (local) sí pasa
        ok(borde.egress_check(txt, destino="local:consejero-arneses").permitido, "ALLOW trusted: %s" % name)

    # El otro lado del NHC: un número suelto NO puede marcarse sensible. Sin la etiqueta
    # delante, cualquier recuento o referencia llenaría el juez de falsos positivos y acabaría
    # ignorándose, que es como se muere un guardia.
    no_sensibles_nhc = [
        ("número suelto", "el resultado fue 999001"),
        ("recuento", "el paciente tiene 999001 leucocitos"),
        ("pedido", "pedido 12345 enviado ayer"),
        # El apellido no puede pasarse de frenada: los límites de palabra tienen que aguantar.
        ("palabra que contiene el apellido", "el gato es muy perezoso"),
        ("apellido parecido", "el paciente Perezagua no es ella"),
    ]
    for name, txt in no_sensibles_nhc:
        s_, m_ = borde.clasificar(txt)
        ok(not s_, "NO falso positivo de NHC: %s (dijo %r)" % (name, m_))

    # Regresiones de la auditoría verificacion (23/6): clases de evasión que ANTES colaban.
    evasiones = [
        ("neuroendocrino (regex muerto C2)", "carcinoma neuroendocrino de mama"),
        ("HGVS corto C3", "la mutación R175H está presente"),
        ("HGVS corto V600E", "se detectó V600E"),
        ("HLA espaciado C3", "tipo HLA A 02 01"),
        ("HLA con guion suelto", "alelo HLA-A presente"),
        ("exón", "deleción del exón 19 de un gen"),
        ("cromosoma palabra", "amplificación en cromosoma 8"),
        ("citobanda con contexto", "deleción 17p observada"),
        ("homoglifo término vetado M2", "vacüna personalizada"),
        ("espaciado término vetado M2", "el plan de la v a c u n a"),
        ("guion término vetado M2", "trabajo de neo-antígeno"),
    ]
    for name, txt in evasiones:
        ok(not borde.egress_check(txt, destino="nvidia").permitido, "DENY evasión: %s" % name)

    limpios = [
        ("genérico", "¿cuál es el mejor patrón para reintentos con backoff?"),
        ("operativo", "resume estos tres párrafos en una frase"),
        ("no sobre-bloquea Q4", "el informe del Q4 va con 3 secciones"),
    ]
    for name, txt in limpios:
        ok(borde.egress_check(txt, destino="nvidia").permitido, "ALLOW limpio untrusted: %s" % name)

    # vacío → DENY (nada que enviar)
    ok(not borde.egress_check("", destino="nvidia").permitido, "DENY vacío")
    # confianza fail-closed: destino raro no es trusted
    ok(not borde.es_trusted("openrouter"), "openrouter NO trusted")
    ok(not borde.es_trusted("gemini"), "gemini NO trusted por defecto")
    ok(borde.es_trusted("local:kb"), "local: SÍ trusted")
    # M1: BTP_BORDE_TRUSTED no puede colar un endpoint de nube; sí admite local:/cleared:
    os.environ["BTP_BORDE_TRUSTED"] = "grok,local:vectordb"
    ok(not borde.es_trusted("grok"), "BTP_BORDE_TRUSTED NO cuela un externo (grok)")
    ok(borde.es_trusted("local:vectordb"), "BTP_BORDE_TRUSTED admite local:")
    del os.environ["BTP_BORDE_TRUSTED"]
    # m1: input no-string → fail-closed (no excepción)
    ok(not borde.egress_check(["lista"], destino="nvidia").permitido, "no-string → DENY (m1)")
    ok(not borde.egress_check({"d": 1}, destino="nvidia").permitido, "dict → DENY (m1)")

    # ── 2. Válvula de declassificación ───────────────────────────────────────────────────
    ok(borde.declasificar("veredicto", "aprobado", sesion="d1").permitido, "declass campo OK")
    ok(not borde.declasificar("campo_raro", "x", sesion="d1").permitido, "declass deny-by-default")
    ok(not borde.declasificar("veredicto", "quizá", sesion="d1").permitido, "declass enum inválido")
    ok(not borde.declasificar("score", 5, sesion="d1").permitido, "declass score fuera de rango")
    # un 'resumen' que RELAVA un secreto (sigue clínico) → DENY aunque el campo esté permitido
    ok(not borde.declasificar("resumen_publico", "el KI-67 salió alto", sesion="d1").permitido,
       "declass no relava secreto")
    # presupuesto de revelación (3): la 4ª revelación de la sesión cae
    s = "presup"
    ok(borde.declasificar("estado", "hecho", sesion=s).permitido, "presup 1")
    ok(borde.declasificar("estado", "hecho", sesion=s).permitido, "presup 2")
    ok(borde.declasificar("estado", "hecho", sesion=s).permitido, "presup 3")
    ok(not borde.declasificar("estado", "hecho", sesion=s).permitido, "presup agotado (4ª)")

    # ── 2b. Política ingeniera (radar de literatura) ────────────────────────────────────
    # ciencia genérica PASA (no es identificador), aunque lleve términos vetados en público
    ok(borde.egress_cientifico("neoantigen vaccine in HR+ breast cancer")[0], "ingeniero genérico PASA")
    ok(borde.egress_cientifico("{{DIANA}} PRRT neuroendocrine tumors")[0], "diana genérica PASA")
    # identificador de paciente DENY
    ok(not borde.egress_cientifico("vaccine trial for {{TITULAR}} {{APELLIDO}}")[0], "ingeniero + nombre {{TITULAR}} DENY")
    ok(not borde.egress_cientifico("papers on variant p.Arg175His here")[0], "ingeniero + variante DENY")
    ok(not borde.egress_cientifico("HLA-A*02:01 and rs12345678")[0], "ingeniero + HLA/rsID DENY")
    # 11-sep-26: un rango HGVS (c.68_69delAG) no casaba por el `\b` final y salía sin gen al lado.
    for v in ("papers on c.68_69delAG", "c.5266dupC founder", "c.1234+1G>A splice",
              "p.(Arg175His) frequency"):
        ok(not borde.egress_cientifico(v)[0], "ingeniero + variante HGVS DENY: %s" % v)
    ok(borde.egress_cientifico("p. ej. vacunas de neoantígenos en mama")[0],
       "«p. ej.» no es una variante: PASA")
    # Consulta del enrutador: sin el embargo de palabras, con todo lo demás igual.
    ok(not borde.clasificar("busca la vacuna de BioNTech", veto_publico=False)[0],
       "consulta sin veto público: «vacuna» PASA")
    ok(borde.clasificar("busca la vacuna de BioNTech")[0], "por defecto el veto público sigue")
    for q in ("busca {{CONTACTO}}", "busca FGFR1 amplificado", "busca a {{TITULAR}} {{APELLIDO}}"):
        ok(borde.clasificar(q, veto_publico=False)[0], "consulta sin veto público sigue DENY: %s" % q)
    # verificacion (11-sep-26): sin el embargo, la frase sobre SU caso tiene que volver al estricto.
    for q in ("busca lo último sobre la vacuna para mi caso",
              "busca papers sobre mi vacuna de neoantígenos personalizada",
              "busca en X la vacuna de neoantígenos para una paciente de Madrid, 41 años",
              "search X for news on my vaccine"):
        ok(borde.clasificar_consulta(q)[0], "consulta sobre SU caso DENY: %s" % q)
    ok(not borde.clasificar_consulta("busca en X qué se dice de la vacuna de BioNTech")[0],
       "consulta genérica con «vacuna» PASA")
    ok(borde.egress_check("busca la vacuna de BioNTech", destino="grok", consulta=True).permitido,
       "egress_check en modo consulta deja salir «vacuna»")
    # Segunda ronda de verificacion: huecos de primera persona que tienen que cerrar…
    for q in ("busca la vacuna que me van a hacer", "busca vacunas de neoantígenos para gente como yo",
              "busca en X: tengo {{DIAGNOSTICO}}, vacuna", "me han diagnosticado, busca vacuna",
              "soy paciente de {{DIAGNOSTICO}}, busca vacuna", "busca novedades de nuestra vacuna",
              "busca qué dice mi onco de la vacuna", "busca la vacuna para mi mujer",
              "busca vacuna para una mujer nacida en 1985", "busca m i c a s o y la vacuna",
              "busca la vacuna de neoantígenos de Beyond the Protocol"):
        ok(borde.clasificar_consulta(q)[0], "consulta sobre SU caso DENY: %s" % q)
    # …y búsquedas legítimas con años que NO pueden caer (el tripwire de edad era demasiado ancho).
    for q in ("busca papers de supervivencia a 5 años con vacunas de neoantígenos",
              "busca ensayos de vacunas de los últimos 10 años", "busca vacunas en mayores de 65 años",
              "busca seguimiento a 3 años de {{VACUNA2}}", "busca Beyond the Protocol en prensa",
              "busca supervivencia de pacientes tratadas a 10 años con vacuna"):
        ok(not borde.clasificar_consulta(q)[0], "búsqueda legítima PASA: %s" % q)
    # La env de consulta solo la heredan los carriles de búsqueda.
    import os as _os
    _os.environ[borde.ENV_CONSULTA] = "1"
    try:
        ok(borde.guard_cli("busca la vacuna de BioNTech", "grok"), "grok hereda el modo consulta")
        ok(not borde.guard_cli("busca la vacuna de BioNTech", "chatgpt"), "chatgpt NO lo hereda")
        ok(not borde.guard_cli("busca la vacuna de BioNTech", "drive"), "drive NO lo hereda")
    finally:
        _os.environ.pop(borde.ENV_CONSULTA, None)
    red, _ = borde.de_identificar("the study (n.100) found")
    ok(red.count("(") == red.count(")"), "de_identificar no se come un ')' que no abrió: %r" % red)
    ok(not borde.egress_check("busca la vacuna de BioNTech", destino="grok").permitido,
       "egress_check por defecto sigue con el embargo")
    ok(not borde.egress_check("busca a {{TITULAR}} {{APELLIDO}}", destino="grok", consulta=True).permitido,
       "egress_check en modo consulta sigue cerrando su nombre")
    # deid no puede dejar la cola de la variante a la vista (regresión que vio verificacion).
    for v in ("TP53 c.524G>A y p.Arg175His", "c.5266dupC", "p.(Arg175His)", "c.68_69delAG"):
        red, _ = borde.de_identificar(v)
        ok(not any(t in red for t in ("G>A", "His", "dupC", "delAG")),
           "de_identificar no deja residuo: %r → %r" % (v, red))
    # Agujero cazado el 14-jul-26: la HGVS GENOMICA (g.) no se detectaba, y el formato
    # tipico de un VCF (chr17:g.7676154G>A) salia limpio por el carril ingeniero.
    ok(not borde.egress_cientifico("chr17:g.7676154G>A somatic")[0], "ingeniero + chr:g. HGVS DENY")
    ok(not borde.egress_cientifico("g.7676154G>A")[0], "ingeniero + g. HGVS DENY")
    ok(borde.egress_cientifico("FGFR1 amplification neuroendocrine breast cancer")[0], "diana FGFR1 a nivel de tema PASA")
    ok(not borde.egress_cientifico("contact contacto about results")[0], "ingeniero + tercero DENY")

    # ── 3. Air-gap de embeddings ─────────────────────────────────────────────────────────
    ok(not borde.embedding_egress_check("text-embedding-3-large", "openai").permitido,
       "embedding API externa DENY")
    ok(not borde.embedding_egress_check("bge-m3", "openai").permitido,
       "embedding local pero destino externo DENY")
    ok(borde.embedding_egress_check("bge-m3", "local:vectordb").permitido,
       "embedding bge-m3 local ALLOW")

    # ── 5. Revocación + anti-replay ──────────────────────────────────────────────────────
    ok(borde.egress_check("texto limpio", destino="nvidia", sesion="viva").permitido,
       "sesión viva ALLOW")
    borde.revocar("muerta")
    ok(not borde.egress_check("texto limpio", destino="nvidia", sesion="muerta").permitido,
       "sesión revocada DENY")
    ok(borde.revocada("muerta"), "revocada persiste (anti-replay)")

    # ── 6. Canarios ──────────────────────────────────────────────────────────────────────
    v = borde.egress_check("informe normal CANARIO-XYZZY-7788 cola", destino="nvidia")
    ok(not v.permitido and v.alarma, "canario en egress DENY + alarma")
    vd = borde.declasificar("resumen_publico", "todo bien CANARIO-XYZZY-7788", sesion="c1")
    ok(not vd.permitido and vd.alarma, "canario en declass DENY + alarma")

    # ── 0. HALT corta todo ───────────────────────────────────────────────────────────────
    halt = os.path.join(_TMP, "no_halt_a")
    open(halt, "w").write("x")
    ok(not borde.egress_check("texto limpio", destino="nvidia").permitido, "HALT → DENY todo")
    os.remove(halt)

    # ── 4. Integridad de la cadena de traza ──────────────────────────────────────────────
    okc, det = borde.verificar_cadena()
    ok(okc, "cadena íntegra tras operar (%s)" % det)
    # alterar un evento → la cadena debe romperse
    import glob
    ledgers = sorted(glob.glob(os.path.join(_TMP, "borde", "ledger-*.jsonl")))
    if ledgers:
        lines = open(ledgers[0], encoding="utf-8").read().splitlines()
        rec = json.loads(lines[0]); rec["motivo"] = "ALTERADO"
        lines[0] = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        open(ledgers[0], "w", encoding="utf-8").write("\n".join(lines) + "\n")
        okb, _ = borde.verificar_cadena()
        ok(not okb, "cadena ROTA tras alterar un evento (detectado)")
        # borrar el primer evento → salto de secuencia
        open(ledgers[0], "w", encoding="utf-8").write("\n".join(lines[1:]) + "\n")
        okd, _ = borde.verificar_cadena()
        ok(not okd, "cadena ROTA tras borrar un evento (salto de secuencia)")
    else:
        ok(False, "había ledger para alterar")

    # ── 6b. Levantar el veto PÚBLICO de «vacuna» no abre el borde de egress ─────────────
    _test_veto_publico_no_abre_borde()

    # ── 7. Resiliencia de run_agent.sh (degradación + aplazar) ───────────────────────────
    _test_run_agent()

    print("RESULTADO borde F0: %d OK, %d fallos" % (_pass, _fail))
    print("✅ F0-MÍNIMA EN VERDE" if _fail == 0 else "❌ revisar fallos")
    return _fail


def _fake_claude(script_path, behavior):
    """Escribe un 'claude' falso: lee --model y responde según `behavior` (dict modelo→json)."""
    body = (
        "#!/bin/bash\n"
        "m=''\n"
        "while [ $# -gt 0 ]; do [ \"$1\" = --model ] && { m=\"$2\"; shift 2; continue; }; shift; done\n"
        "case \"$m\" in\n"
    )
    for modelo, jsonout in behavior.items():
        body += "  %s) echo '%s' ;;\n" % (modelo, jsonout.replace("'", "'\\''"))
    body += "  *) echo '{\"is_error\":true}' ;;\nesac\nexit 0\n"
    open(script_path, "w").write(body)
    os.chmod(script_path, 0o755)


def _run_agent(behavior, modelo_pedido):
    fake = os.path.join(_TMP, "fake_claude.sh")
    _fake_claude(fake, behavior)
    env = dict(os.environ, BTP_CLAUDE_BIN=fake, BTP_API_KEY_OVERRIDE="x",
               BTP_MODEL=modelo_pedido, BTP_COST_GUARDED="1", BTP_AGENT="comite-medico")
    p = subprocess.run(["bash", os.path.join(ROOT, "tools", "run_agent.sh"), "haz X"],
                       capture_output=True, text=True, env=env)
    hb = os.path.join(ROOT, "tools", "state", "heartbeat", "comite-medico.json")
    return p.stdout, p.stderr, p.returncode


def _test_run_agent():
    """Los cuatro casos de aquí arrancan `tools/run_agent.sh`, que sale solo si hay un HALT
    («MURO: HALT activo → no arranco», run_agent.sh:18, mirando las rutas a pelo sin pasar
    por BTP_HALT_FILES). Con el lazo parado no prueban nada, así que se DICEN y se saltan —
    los otros 143 casos de esta batería siguen corriendo, que es la razón de no poner la
    guarda arriba del fichero: perderían la cobertura del muro entera por cuatro casos.
    """
    _halt = [os.path.expanduser("~/.btp.HALT"), os.path.join(ROOT, ".HALT")]
    if any(os.path.exists(h) for h in _halt):
        print("  SKIP: 4 casos de run_agent saltados — hay un HALT activo y el lazo está parado")
        return

    lim = '{\"api_error_status\": 429, \"is_error\": true, \"error\": \"overloaded\"}'
    okj = '{\"result\": \"hola\", \"is_error\": false, \"total_cost_usd\": 0.0}'
    # opus y sonnet topados, haiku responde → debe salir el JSON de haiku
    out, err, rc = _run_agent({"opus": lim, "sonnet": lim, "haiku": okj}, "opus")
    ok('"result": "hola"' in out, "run_agent degrada opus→sonnet→haiku y entrega haiku")
    ok("degrado a sonnet" in err and "degrado a haiku" in err, "run_agent logea la degradación")
    # toda la cadena topada + agente CLÍNICO (comite-medico) → 🔴 BLOQUEO crítico (no muere callado,
    # y NO degrada a un cerebro flojo): rc 75 y el stderr lo dice (freno de criticidad).
    out2, err2, rc2 = _run_agent({"opus": lim, "sonnet": lim, "haiku": lim}, "opus")
    ok(rc2 == 75 and ("BLOQUEO" in err2 or "aplazo" in err2),
       "run_agent BLOQUEA (clínico) cuando se agota la cadena por límite")
    # primer modelo responde a la primera → sin degradación
    out3, err3, rc3 = _run_agent({"sonnet": okj, "haiku": okj}, "sonnet")
    ok('"result": "hola"' in out3 and "degrado" not in err3, "run_agent sin límite no degrada")


def _test_veto_publico_no_abre_borde():
    """Deuda embargo-publico-flag-contradice-claude-md (11-sep-26). {{TITULAR}} levantó «vacuna» para
    el COPY PÚBLICO; eso vive en seguimiento._terminos_vetados_ahora(). El borde de egress a LLMs
    de terceros usa la lista ENTERA (_TERMINOS_VETADOS) y no puede heredar ese levantamiento, ni
    siquiera con el flag reveal:true: publicar una palabra y mandar su caso a un tercero son dos
    reglas distintas."""
    seg = borde.seg
    ok("vacuna" not in seg._terminos_vetados_ahora(), "copy público: «vacuna» levantada")
    ok("vacuna" in seg._TERMINOS_VETADOS, "egress: _TERMINOS_VETADOS conserva «vacuna»")
    for texto in ("ayúdame con la vacuna personalizada de este informe",
                  "vacüna fase 1", "neoantígenos del panel"):
        ok(borde.clasificar(texto)[0], "borde estricto sigue marcando: %s" % texto)
    original = seg._embargo_activo
    try:
        seg._embargo_activo = lambda: False           # como si {{TITULAR}} pusiera reveal:true
        ok(seg._terminos_vetados_ahora() == seg._VETO_DURABLE, "reveal: copy solo veta lo durable")
        ok(borde.clasificar("la vacuna personalizada")[0],
           "reveal tampoco abre el borde de egress («vacuna»)")
        ok(borde.clasificar("neoantígenos")[0], "reveal tampoco abre el borde (neoantígenos)")
        ok(borde.clasificar("{{CONTACTO}}")[0], "«{{CONTACTO}}» durable en el borde")
    finally:
        seg._embargo_activo = original


if __name__ == "__main__":
    sys.exit(main())
