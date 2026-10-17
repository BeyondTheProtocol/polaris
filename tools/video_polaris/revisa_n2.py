"""Tamiz N2 de un vídeo o de una carpeta de fotogramas: OCR local de cada fotograma (tesseract por ocr_informes.ocr_image,
egress 0) y búsqueda de cifras clínicas, fechas, identificadores y nombres que no pueden salir. Vídeo EN v3 para Lexie (17-oct-2026).

Uso:  python3 revisa_n2.py <video.mp4 | carpeta> [--cada N] [--permitir "<regex>"]
      --cada N      un fotograma de cada N (por defecto 6 en vídeo: 5 por segundo a 30 fps; 1 en carpeta)
      --permitir    texto que SÍ puede salir aunque case (p. ej. la barra de escala de la biopsia)
Sale con código 1 si encuentra algo e imprime segundo, fotograma y texto leído. La hoja de contactos sigue haciendo falta
para lo que el OCR no lee (imagen médica sin texto, caras): esto es el tamiz de lo escrito.
"""
import argparse, glob, os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ocr_informes import ocr_image  # noqa: E402

PATRONES = {
    "cifra con unidad clínica": r"\d+([.,]\d+)?\s*(%|mm\b|cm\b|ml\b|SUV|U/m?L|ng/|mg\b|µm²|mm²|HU\b|Gy\b)",
    "SUV": r"\bSUV",
    "fecha": r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(19|20)\d{2}-\d{2}-\d{2}\b"
             r"|\b\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+20\d{2}",
    "identificador de informe o bloque": r"\bVH\d{2}|\b\d{2}-\d{4,6}-[A-Z]\b|\bNHC\b|\bBIO\d{4,}|\b24[bB]\d{3,}",
    "marca vetada": r"clinic\s*omics|chroma\s*logic|o[l]une|scien[t]ist",
    "nombre en un informe": r"paciente|patient\s*(name|id)|fecha de nacimiento|date of birth",
}


def avisos(texto, permitir=""):
    """Las coincidencias de una línea de texto: [(nombre del patrón, línea)]."""
    if permitir and re.search(permitir, texto, re.I):
        return []
    return [(n, texto) for n, p in PATRONES.items() if re.search(p, texto, re.I)]


def fotogramas(src, cada, tmp):
    if os.path.isdir(src):
        fs = sorted(glob.glob(os.path.join(src, "*.jpg")) + glob.glob(os.path.join(src, "*.png")))
        return [(i / 30, f) for i, f in enumerate(fs)][::cada]
    subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vf", f"select=not(mod(n\\,{cada}))", "-vsync", "vfr",
                    "-q:v", "2", os.path.join(tmp, "%05d.jpg")], check=True)
    return [((int(os.path.basename(f)[:5]) - 1) * cada / 30, f) for f in sorted(glob.glob(os.path.join(tmp, "*.jpg")))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("--cada", type=int); ap.add_argument("--permitir", default="")
    a = ap.parse_args()
    cada = a.cada or (1 if os.path.isdir(a.src) else 6)
    with tempfile.TemporaryDirectory() as tmp:
        fs = fotogramas(a.src, cada, tmp)
        with ThreadPoolExecutor(6) as ex:
            textos = list(ex.map(lambda tf: ocr_image(Path(tf[1])), fs))
    hallados = [(t, os.path.basename(f), n, l) for (t, f), txt in zip(fs, textos)
                for l in txt.splitlines() if l.strip() for n, l in avisos(l.strip(), a.permitir)]
    print(f"{len(fs)} fotogramas leídos, {sum(len(x.splitlines()) for x in textos)} líneas de texto, {len(hallados)} avisos")
    for t, f, n, l in hallados:
        print(f"  {t:7.2f} s  {f}  [{n}]  {l}")
    sys.exit(1 if hallados else 0)


if __name__ == "__main__":
    main()
