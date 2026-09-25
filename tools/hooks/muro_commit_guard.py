#!/usr/bin/env python3
"""Guardián del MURO en cada commit.

La constitución del sistema (`CLAUDE.md`) no se cambia sin la firma de {{TITULAR}}. Un sistema
que se reescribe a sí mismo (auto-mejora, lazo, agentes) NO debe poder reescribir su propio
muro a escondidas. Si un commit toca un fichero protegido y falta la firma
(`BTP_MURO_OK=1`), se BLOQUEA con un mensaje claro.

Se activa con `bash tools/hooks/instalar.sh` (pone `core.hooksPath=tools/hooks`).
Mismo patrón de "acto humano explícito" que `BTP_PRESENCE_OK` en codigo_rojo.
"""
import os
import subprocess
import sys

PROTEGIDOS = {"CLAUDE.md"}  # el muro / constitución


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                         capture_output=True, text=True)
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def bloquea(staged, env):
    """Devuelve (bloquear?, ficheros_protegidos_tocados). Determinista, testeable."""
    tocados = sorted(set(staged) & PROTEGIDOS)
    if tocados and env.get("BTP_MURO_OK") != "1":
        return True, tocados
    return False, tocados


def main():
    bloq, tocados = bloquea(staged_files(), os.environ)
    if bloq:
        sys.stderr.write(
            "\n🧱 MURO PROTEGIDO — commit BLOQUEADO\n"
            "Este commit toca la constitución del sistema: %s\n"
            "El muro no se cambia sin la firma de {{TITULAR}}. Si de verdad lo quieres cambiar TÚ:\n"
            "  BTP_MURO_OK=1 git commit …\n"
            "(Así ni el lazo ni la auto-mejora pueden reescribir el muro a escondidas.)\n\n"
            % ", ".join(tocados))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
