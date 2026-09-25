#!/usr/bin/env python3
"""test_honestidad_lint.py — el linter de honestidad caza afirmaciones fuertes sin sello.

Verifica: (1) el selftest interno pasa; (2) caza el claim real que falló el 28/6
("telemetría sin documentar / caja negra"); (3) NO marca el mismo claim cuando lleva
sello [supuesto]/[verificado]; (4) no rompe el build en modo advisory.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import honestidad_lint as H  # noqa: E402

_fail = 0


def ok(cond, nombre):
    global _fail
    print(("  [OK]  " if cond else "  [FAIL]") + " " + nombre)
    if not cond:
        _fail += 1


print("TEST honestidad_lint")

# (1) selftest interno
ok(H._selftest() == 0, "selftest interno en verde")

# (2) caza el claim real del 28/6
caso = "{{CONTACTO}} es una caja negra single-author con telemetría sin documentar."
ok(len(H.lint_text(caso)) >= 1, "marca el claim falso real (caja negra/telemetría sin sello)")

# (3) NO lo marca con sello declarado
ok(len(H.lint_text(caso + " [supuesto: red-team sin verificar]")) == 0,
   "no marca el mismo claim con [supuesto]")
ok(len(H.lint_text("Auditado: cero telemetría [verificado: github.com/achetronic/contacto].")) == 0,
   "no marca un claim con [verificado] + fuente")

# (4) líneas neutras y tituladas no se marcan
ok(len(H.lint_text("# Telemetría\nEl arnés es local y simple.")) == 0,
   "ignora títulos y frases neutras")

# (5) advisory no rompe (exit 0) aunque haya flags; --strict sí
import tempfile  # noqa: E402
with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
    f.write(caso)
    ruta = f.name
ok(H._check_file(ruta, strict=False) == 0, "modo advisory devuelve 0 (no rompe build)")
ok(H._check_file(ruta, strict=True) == 1, "modo --strict devuelve 1 con flags")
os.unlink(ruta)

# (6) --repo (D, 2/7/26): barre todos los .md `tipo: decision` bajo una raíz, ignora el resto.
import shutil  # noqa: E402
_repo_tmp = tempfile.mkdtemp(prefix="honestidad_repo_")
try:
    # doc de decisión CON flag sin sello → debe detectarse
    with open(os.path.join(_repo_tmp, "decision-mala.md"), "w", encoding="utf-8") as f:
        f.write("---\ntipo: decision\nfecha: 2026-07-02\n---\n\n" + caso + "\n")
    # doc de decisión LIMPIO (con sello) → no debe aportar flags
    with open(os.path.join(_repo_tmp, "decision-buena.md"), "w", encoding="utf-8") as f:
        f.write("---\ntipo: decision\n---\n\n" + caso + " [supuesto: pendiente de verificar]\n")
    # CONTRATO INVERTIDO el 25-jul-26 (hallazgo medio nº10): antes había que declarar
    # `tipo: decision` para que el barrido te mirara, y como esa clave no existía en NINGUNA nota
    # del repo, `--repo` revisaba CERO documentos mientras CLAUDE.md lo vendía como el apoyo contra
    # la falsa certeza. Ahora se barre todo y el frontmatter sirve para EXIMIR. Este bloque
    # comprueba la dirección NUEVA; el detalle fino vive en test_honestidad_lint_repo.py.
    with open(os.path.join(_repo_tmp, "nota-normal.md"), "w", encoding="utf-8") as f:
        f.write("# Nota\n\n" + caso + "\n")
    with open(os.path.join(_repo_tmp, "otra-cosa.md"), "w", encoding="utf-8") as f:
        f.write("---\ntipo: referencia\n---\n\n" + caso + "\n")
    with open(os.path.join(_repo_tmp, "exenta.md"), "w", encoding="utf-8") as f:
        f.write("---\nhonestidad: exento\n---\n\n" + caso + "\n")

    docs, exentos, _fuera = H._docs_a_barrer(_repo_tmp)
    nombres = sorted(os.path.basename(d) for d in docs)
    ok(nombres == ["decision-buena.md", "decision-mala.md", "nota-normal.md", "otra-cosa.md"],
       "_docs_a_barrer coge TODO menos lo exento (%r)" % nombres)
    ok(exentos == 1, "el doc con `honestidad: exento` se cuenta como exención, no se barre")

    ok(H._check_repo(_repo_tmp, strict=False) == 0, "--repo advisory: no rompe build aunque haya flags")
    ok(H._check_repo(_repo_tmp, strict=True) == 1, "--repo --strict: rompe si ALGÚN doc falla")

    # raíz sin ningún .md → verde, no falla por ausencia
    _repo_vacio = tempfile.mkdtemp(prefix="honestidad_repo_vacio_")
    ok(H._check_repo(_repo_vacio, strict=True) == 0, "raíz sin .md → verde, no falla")
    shutil.rmtree(_repo_vacio, ignore_errors=True)

    # CLI end-to-end: main(["--repo", ruta, "--strict"])
    ok(H.main(["--repo", _repo_tmp, "--strict"]) == 1, "CLI --repo <raíz> --strict propaga el exit 1")
    ok(H.main(["--repo", os.path.join(_repo_tmp, "solo-buena-no-existe")]) == 0,
       "CLI --repo a una raíz sin docs → verde (fail-soft, no revienta)")
finally:
    shutil.rmtree(_repo_tmp, ignore_errors=True)

if _fail:
    print("TEST honestidad_lint: %d fallo(s)" % _fail)
    sys.exit(1)
print("HONESTIDAD-LINT EN VERDE")
sys.exit(0)
