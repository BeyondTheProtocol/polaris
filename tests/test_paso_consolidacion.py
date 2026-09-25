#!/usr/bin/env python3
"""Tests del consolidador (tools/paso_consolidacion.py). Local, sin red, sin estado vivo."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import paso_consolidacion as pc  # noqa: E402

fallos = 0


def check(nombre, cond):
    global fallos
    print(("  ✓ " if cond else "  ✗ ") + nombre)
    if not cond:
        fallos += 1


# a) prioridad: crítica > alta > media
_, cab, _, _, _, _ = pc.consolidar({"intencion": "x", "contribuciones": [
    {"agente": "a", "decisiones": [
        {"titulo": "M", "impacto": "media"},
        {"titulo": "C", "impacto": "critica"},
        {"titulo": "A", "impacto": "alta"}]}]})
check("ordena por impacto (crítica→alta→media)", [d["titulo"] for d in cab] == ["C", "A", "M"])

# b) trazabilidad: la fuente cae al agente si la decisión no la trae
_, cab, _, _, _, _ = pc.consolidar({"intencion": "x", "contribuciones": [
    {"agente": "agencia-viajes", "decisiones": [{"titulo": "t", "impacto": "alta"}]}]})
check("hereda la fuente del agente", cab[0]["_fuente"] == "agencia-viajes")

# c) decisión huérfana (sin agente ni fuente) → avisa y rc=1
_, cab, _, _, avisos, huerf = pc.consolidar({"intencion": "x", "contribuciones": [
    {"decisiones": [{"titulo": "sin dueño", "impacto": "alta"}]}]})
check("detecta decisión huérfana", huerf and any("SIN fuente" in v for v in avisos))

# d) tope de 6: 8 decisiones → 6 en cabeza, 2 diferidas, con aviso (no se ocultan)
decs = [{"titulo": "d%d" % i, "impacto": "media", "fuente": "a"} for i in range(8)]
_, cab, dif, _, avisos, _ = pc.consolidar({"intencion": "x", "contribuciones": [{"agente": "a", "decisiones": decs}]})
check("tope 6 + diferidas a la vista", len(cab) == 6 and len(dif) == 2 and any("diferidas" in v for v in avisos))

# e) impacto inválido → se trata como 'media' y se avisa (fail-safe, no se cae)
_, cab, _, _, avisos, _ = pc.consolidar({"intencion": "x", "contribuciones": [
    {"agente": "a", "decisiones": [{"titulo": "y", "impacto": "GODMODE"}]}]})
check("impacto inválido → media + aviso", cab[0]["impacto"] == "media" and any("inválido" in v for v in avisos))

# f) render no rompe y cita la fuente
txt = pc.render(*pc.consolidar({"intencion": "viaje", "contribuciones": [
    {"agente": "logistica", "decisiones": [{"titulo": "T", "impacto": "critica", "recomendacion": "tren"}],
     "acciones": [{"texto": "reservar", "limite": "hoy"}]}]})[:5])
check("el render cita la fuente y la acción", "fuente: logistica" in txt and "reservar" in txt)

print(("RESULTADO paso_consolidacion: %d OK, %d fallos" % (6 - fallos, fallos)))
print("✅ CONSOLIDADOR EN VERDE" if not fallos else "❌ revisar fallos")
sys.exit(0 if not fallos else 1)
