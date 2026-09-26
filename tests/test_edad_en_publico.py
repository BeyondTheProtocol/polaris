"""La edad de {{TITULAR}} SÍ se dice en público (decisión suya, 26-sep-26).

Un «ni edad» en el charter de `diseno` hizo que una sesión propusiera quitar «35 años» del
hero de helptitular.com. Este test impide que el veto vuelva a colarse en charters o reglas,
y que la norma canónica deje de decirlo en positivo.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VETO = re.compile(r"\bni\s+(la\s+)?edad\b|\bsin\s+(la\s+)?edad\b|nunca\s+(la\s+)?edad\b", re.I)

fallos = []
for f in sorted((ROOT / ".claude" / "agents").glob("*.md")) + sorted((ROOT / ".claude" / "rules").glob("*.md")):
    for n, linea in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if VETO.search(linea):
            fallos.append(f"{f.relative_to(ROOT)}:{n} veta la edad en público")

canon = (ROOT / ".claude" / "rules" / "marca-copy.md").read_text(encoding="utf-8")
if "La edad SÍ se dice" not in canon:
    fallos.append("marca-copy.md ya no dice que la edad SÍ se dice en público")

for x in fallos:
    print("❌", x)
if fallos:
    sys.exit(1)
print("✅ la edad no está vetada en público y la canónica lo dice")
