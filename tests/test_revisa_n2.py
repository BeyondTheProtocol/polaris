"""El tamiz N2 de los vídeos (tools/video_polaris/revisa_n2.py) caza lo que no puede salir y deja pasar el texto normal."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "video_polaris"))
from revisa_n2 import avisos  # noqa: E402

CAZA = ["SUVmax 7.6", "26.9 mm", "40%", "3.31 mL", "12/09/2026", "2026-06-11", "11 Jun 2026", "VH26B000001",
        "bloque 26-28381-B", "ClinicOmics", "Chroma Logic", "Patient name", "scientist"]
PASA = ["Metastasis map", "helptitular.com/en/data", "Cell by cell", "Whole exome", "HLA typing",
        "I'm not asking for guarantees.", "Claude Science"]


def test_caza():
    for t in CAZA:
        assert avisos(t), f"no caza «{t}»"


def test_pasa():
    for t in PASA:
        assert not avisos(t), f"caza de más «{t}»: {avisos(t)}"


def test_permitir():
    assert not avisos("200 µm", r"^\d+ µm$") and not avisos("26.9 mm", r"26\.9")


if __name__ == "__main__":
    test_caza(); test_pasa(); test_permitir(); print("ok")
