"""Manifiesto del tribunal: sha256 de cada salida y de cada script, commit del repo. Salida: MANIFIESTO_TRIBUNAL.json."""
import os
import subprocess

from _comun import OUT, guarda, sha256

AQUI = os.path.dirname(os.path.abspath(__file__))


def run(*a):
    sal = {f: sha256(os.path.join(OUT, f)) for f in sorted(os.listdir(OUT))
           if os.path.isfile(os.path.join(OUT, f)) and not f.startswith("MANIFIESTO") and not f.startswith("_tmp")}
    scr = {f: sha256(os.path.join(AQUI, f)) for f in sorted(os.listdir(AQUI)) if f.endswith((".py", ".json"))}
    try:
        commit = subprocess.check_output(["git", "-C", AQUI, "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        commit = "desconocido"
    guarda({"salidas": sal, "scripts": scr, "commit_al_generar": commit, "semilla": 20261016,
            "espec": "v2 EXPLORATORIA del tribunal (REGLAS_TRIBUNAL_v2.json); v1 = docs/flex/ESPEC_CONGELADA_v1.md sha256 6165b9ef"},
           "MANIFIESTO_TRIBUNAL.json")
    for f, h in sal.items():
        print(f, h)
