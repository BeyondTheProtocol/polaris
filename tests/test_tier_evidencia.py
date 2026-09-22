#!/usr/bin/env python3
"""test_tier_evidencia.py — un preclínico no puede llegar disfrazado de ensayo.

La regla 17 del protocolo ("etiqueta el tier; nunca aplanes un preclínico en «esto
funciona»") vivía solo en el prompt: se cumplía cuando el modelo se acordaba. Estos
tests fijan las dos propiedades por las que se mecanizó:

  1. El tier lo teje el CÓDIGO desde señales booleanas, no un modelo clasificando.
     Mismas señales -> mismo tier, siempre, y el porqué es auditable señal a señal.
  2. FAIL-CLOSED y ANTI-ASCENSO: sin señales -> `desconocido` (no entregable), y el
     léxico de un abstract NUNCA puede subir un estudio en ratones a RCT.

HERMÉTICO: el registro se mockea con XML sintético; no toca la red ni NCBI.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import tier_evidencia as t  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print("  ✗ %s" % name)


def xml(pubtypes=(), mesh=()):
    """XML mínimo de efetch: solo lo que el parser mira. Datos sintéticos."""
    p = "".join('<PublicationType UI="D0">%s</PublicationType>' % x for x in pubtypes)
    m = "".join('<DescriptorName UI="D0" MajorTopicYN="N">%s</DescriptorName>' % x for x in mesh)
    return "<PubmedArticle><PublicationTypeList>%s</PublicationTypeList>" \
           "<MeshHeadingList>%s</MeshHeadingList></PubmedArticle>" % (p, m)


def fake(pubtypes=(), mesh=()):
    return lambda pmid: xml(pubtypes, mesh)


def main():
    # ── 1. El registro decide el tier, sin LLM ──────────────────────────────────────
    r = t.clasifica_pmid("1", fetch=fake(["Journal Article", "Randomized Controlled Trial",
                                          "Clinical Trial, Phase III"], ["Humans"]))
    check("RCT catalogado -> tier rct", r["tier"] == "rct" and not r["preclinico"])

    r = t.clasifica_pmid("2", fetch=fake(["Journal Article", "Case Reports"], ["Humans"]))
    check("Case Reports -> caso_clinico", r["tier"] == "caso_clinico")

    # "Review" viaja SIEMPRE junto a "Meta-Analysis" en PubMed: si la señal débil ganara,
    # un metaanálisis se etiquetaría como opinión. Por eso la fuerte la desplaza.
    r = t.clasifica_pmid("3", fetch=fake(["Systematic Review", "Meta-Analysis", "Review"], ["Humans"]))
    check("metaanálisis NO se degrada a revisión narrativa", r["tier"] == "metaanalisis")

    r = t.clasifica_pmid("4", fetch=fake(["Practice Guideline"], ["Humans"]))
    check("guía de práctica clínica -> guia_clinica", r["tier"] == "guia_clinica")

    r = t.clasifica_pmid("5", fetch=fake(["Journal Article"], ["Humans", "Retrospective Studies"]))
    check("MeSH Retrospective Studies -> cohorte_retrospectiva",
          r["tier"] == "cohorte_retrospectiva")

    # ── 2. Lo preclínico se marca como preclínico, y manda sobre todo lo demás ───────
    r = t.clasifica_pmid("6", fetch=fake(["Journal Article"],
                                         ["Animals", "Mice", "Xenograft Model Antitumor Assays"]))
    check("ratón sin personas -> modelo_animal", r["tier"] == "modelo_animal")
    check("y lleva la bandera preclinico", r["preclinico"] is True)

    r = t.clasifica_pmid("7", fetch=fake(["Journal Article"], ["Cell Line, Tumor", "In Vitro Techniques"]))
    check("línea celular -> in_vitro + preclínico", r["tier"] == "in_vitro" and r["preclinico"])

    # El caso que la regla 17 existe para cazar: un paper en ratones cuyo catálogo arrastra
    # una etiqueta de ensayo. La degradación tiene que ganar SIEMPRE.
    r = t.clasifica_pmid("8", fetch=fake(["Journal Article", "Randomized Controlled Trial"],
                                         ["Animals", "Mice"]))
    check("ratón + etiqueta de RCT -> sigue siendo preclínico", r["tier"] == "modelo_animal")

    # ── 3. Fail-closed: sin señales no se inventa un tier ───────────────────────────
    r = t.clasifica_pmid("9", fetch=lambda p: "")
    check("registro mudo -> desconocido", r["tier"] == "desconocido")
    check("y NO es entregable", r["entregable"] is False)

    r = t.clasifica_pmid("10", fetch=fake(["Journal Article"], ["Humans"]))
    check("artículo sin señal de diseño -> desconocido", r["tier"] == "desconocido")
    check("y dice qué señales le faltan", len(r["faltan"]) > 0)

    # ── 4. ANTI-ASCENSO: el léxico solo puede degradar ──────────────────────────────
    abstract_raton = ("In this study we treated mice bearing xenograft tumors. "
                      "Unlike the randomized controlled trial reported by Smith et al., "
                      "our murine model showed complete regression.")
    r = t.teje(t.senales_desde_texto(abstract_raton))
    check("un abstract de ratón NO asciende a rct por decir 'randomized'", r["tier"] != "rct")
    check("y se queda en preclínico", r["preclinico"] is True)

    s = t.senales_desde_texto("randomized controlled trial in 340 patients, phase III")
    check("el léxico ni siquiera emite la señal 'rct'", "rct" not in s)

    # ── 5. La ruta LLM usa el MISMO tejedor (booleanos sí/no -> código) ─────────────
    r = t.teje({"rct": True, "humanos": True})
    check("desde respuestas sí/no -> mismo tier que el registro", r["tier"] == "rct")
    r = t.teje({"in_vitro": True})
    check("respuesta sí/no de banco -> in_vitro preclínico", r["tier"] == "in_vitro" and r["preclinico"])
    r = t.teje({})
    check("cuestionario vacío -> desconocido (no adivina)", r["tier"] == "desconocido")

    # None NUNCA cuenta como sí ni como no: es "no lo sé" y se propaga.
    r = t.teje({"rct": None, "humanos": True})
    check("una señal None no se lee como True", r["tier"] != "rct")

    # ── 6. El preprint se ve aunque el diseño sea fuerte ────────────────────────────
    r = t.clasifica_pmid("11", fetch=fake(["Preprint", "Randomized Controlled Trial"], ["Humans"]))
    check("RCT en preprint -> tier rct pero avisa del preprint",
          r["tier"] == "rct" and "PREPRINT" in r["motivo"])

    # ── 7. Cada tier tiene su texto (si no, el consumidor imprime un KeyError) ──────
    check("todos los tiers tienen etiqueta", all(k in t.TIER_TXT for k in t.TIERS))
    check("todas las señales tienen pregunta sí/no", all(isinstance(q, str) and q.endswith("?")
                                                         for q in t.SENALES.values()))

    print("RESULTADO tier_evidencia: %d OK, %d fallos" % (_pass, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
