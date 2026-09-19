#!/usr/bin/env python3
"""md_to_pdf_pericial.py — Markdown -> PDF con formato de INFORME PERICIAL profesional (reportlab).

Estética sobria/institucional (NO la marca de campaña): Times en cuerpo, Helvetica-Bold en
titulares, azul institucional #1B3A6B como único acento, portada propia, cabecera/pie con
referencia + "Pág. X de Y", marca de agua BORRADOR en páginas interiores y bloque de firma.
Spec del comité de diseño (28/6/26). Sin deps externas más allá de reportlab.

Uso:
  python3 md_to_pdf_pericial.py entrada.md salida.pdf \
      [--ref "BTP/PER/2026-001"] [--caso "..."] [--lugar "España"] [--final]

--final quita la marca de agua y el "pendiente de firma" del pie (cuando el perito firme).
"""
import sys, re, html, argparse
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, HRFlowable)
from reportlab.pdfgen import canvas

ACCENT = colors.HexColor("#1B3A6B")
INK = colors.HexColor("#1A1A1A")
GREY = colors.HexColor("#888888")
GREY2 = colors.HexColor("#444444")
HAIR = colors.HexColor("#CCCCCC")
ZEBRA = colors.HexColor("#F5F5F5")

# Globales de decoración (se rellenan en main).
REF = "BTP/PER/2026-001"
CASO = ""
BORRADOR = True

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿⌀-⏿■-◿]")
def clean(t):
    for a, b in [("→", "->"), ("⟶", "->"), ("⇒", "=>"), ("≥", ">="), ("≤", "<="), ("×", "x"),
                 ("±", "+/-"), ("°C", "C"), ("°", ""), ("•", "-"), ("∩", " n "), ("⭐", "")]:
        t = t.replace(a, b)
    return EMOJI.sub("", t)
def inline(t):
    t = html.escape(clean(t))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
    t = re.sub(r"(?<![\*\w])\*(?!\*)(.+?)(?<!\*)\*(?![\*\w])", r"<i>\1</i>", t)
    return t


class PericialCanvas(canvas.Canvas):
    """Numeración 'Pág. X de Y' en dos pasadas + cabecera/pie + marca de agua (no en portada)."""
    def __init__(self, *a, **k):
        canvas.Canvas.__init__(self, *a, **k)
        self._pages = []
    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()
    def save(self):
        total = len(self._pages)
        for i, state in enumerate(self._pages):
            self.__dict__.update(state)
            self._decorate(i + 1, total)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)
    def _decorate(self, page, total):
        if page == 1:
            return  # portada limpia
        w, h = A4
        if BORRADOR:
            self.saveState()
            self.setFont("Helvetica-Bold", 72)
            self.setFillColor(HAIR)
            self.translate(w / 2, h / 2)
            self.rotate(45)
            self.drawCentredString(0, 0, "BORRADOR")
            self.restoreState()
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(GREY)
        # Cabecera
        self.drawString(3 * cm, h - 1.7 * cm, "Informe Pericial Informático  —  Ref. %s" % REF)
        self.setStrokeColor(HAIR)
        self.setLineWidth(0.4)
        self.line(3 * cm, h - 1.85 * cm, w - 2 * cm, h - 1.85 * cm)
        # Pie
        self.line(3 * cm, 1.85 * cm, w - 2 * cm, 1.85 * cm)
        estado = "BORRADOR pericial - pendiente de firma  ·  " if BORRADOR else ""
        pie = "Caso «%s»  ·  %sPág. %d de %d" % (CASO, estado, page, total)
        self.drawCentredString(w / 2, 1.55 * cm, pie)
        self.restoreState()


def build_styles():
    ss = getSampleStyleSheet()
    S = {}
    S["body"] = ParagraphStyle("body", parent=ss["BodyText"], fontName="Times-Roman",
                               fontSize=10.5, leading=15, spaceAfter=6, textColor=INK)
    S["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=13, leading=16,
                             spaceBefore=16, spaceAfter=6, textColor=INK)
    S["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11.5, leading=14,
                             spaceBefore=14, spaceAfter=5, textColor=ACCENT)
    S["h3"] = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10, leading=13,
                             spaceBefore=9, spaceAfter=3, textColor=INK)
    S["li"] = ParagraphStyle("li", parent=S["body"], leftIndent=16, spaceAfter=2)
    S["quote"] = ParagraphStyle("quote", parent=S["body"], leftIndent=10, textColor=GREY2,
                                fontName="Times-Italic")
    S["cell"] = ParagraphStyle("cell", parent=S["body"], fontSize=8.5, leading=11, spaceAfter=0)
    S["cellh"] = ParagraphStyle("cellh", parent=S["cell"], fontName="Helvetica-Bold",
                                textColor=colors.white)
    # Portada
    S["title"] = ParagraphStyle("title", fontName="Times-Bold", fontSize=22, leading=26,
                                alignment=TA_CENTER, textColor=INK)
    S["sub"] = ParagraphStyle("sub", fontName="Times-Italic", fontSize=13, leading=17,
                              alignment=TA_CENTER, textColor=GREY2)
    S["ref"] = ParagraphStyle("ref", fontName="Helvetica", fontSize=9, leading=12,
                              alignment=TA_CENTER, textColor=GREY)
    S["cover_b"] = ParagraphStyle("cover_b", fontName="Helvetica", fontSize=10, leading=15,
                                  alignment=TA_CENTER, textColor=INK)
    S["foot_c"] = ParagraphStyle("foot_c", fontName="Times-Italic", fontSize=8.5, leading=11,
                                 alignment=TA_CENTER, textColor=GREY)
    S["sign"] = ParagraphStyle("sign", parent=S["body"], spaceAfter=4)
    return S


def cover(S, caso, lugar, fecha):
    el = [Spacer(1, 5 * cm)]
    el.append(HRFlowable(width="100%", thickness=0.6, color=INK, spaceAfter=18))
    el.append(Paragraph("INFORME PERICIAL INFORMÁTICO", S["title"]))
    el.append(Spacer(1, 14))
    el.append(Paragraph(html.escape(caso), S["sub"]))
    el.append(Spacer(1, 8))
    el.append(Paragraph("Referencia interna: %s" % html.escape(REF), S["ref"]))
    el.append(Spacer(1, 1.6 * cm))
    el.append(HRFlowable(width="40%", thickness=0.4, color=HAIR, spaceAfter=16))
    el.append(Paragraph("<b>Perito:</b> [a designar]", S["cover_b"]))
    el.append(Paragraph("<b>Titulación / colegiación:</b> [a designar]", S["cover_b"]))
    el.append(Paragraph("A instancia de: Dña. {{TITULAR}} {{APELLIDO}} (perjudicada) y su letrada", S["cover_b"]))
    el.append(Spacer(1, 12))
    el.append(Paragraph("%s, %s" % (html.escape(lugar), fecha), S["cover_b"]))
    el.append(Spacer(1, 3.2 * cm))
    el.append(HRFlowable(width="100%", thickness=0.6, color=INK, spaceAfter=8))
    el.append(Paragraph("DOCUMENTO BORRADOR — PENDIENTE DE FIRMA PERICIAL · Confidencial"
                        if BORRADOR else "Documento pericial · Confidencial", S["foot_c"]))
    el.append(PageBreak())
    return el


def signature(S, lugar):
    el = [Spacer(1, 1.2 * cm)]
    el.append(HRFlowable(width="100%", thickness=0.3, color=HAIR, spaceAfter=14))
    el.append(Paragraph("En %s, a ____ de ________________ de 20____" % html.escape(lugar), S["sign"]))
    el.append(Spacer(1, 1.8 * cm))
    el.append(Paragraph("Fdo.: ______________________________________", S["sign"]))
    el.append(Paragraph("[Nombre del perito]", S["sign"]))
    el.append(Paragraph("[Titulación · Colegio profesional · nº de colegiado]", S["sign"]))
    el.append(Spacer(1, 6))
    el.append(Paragraph("<i>Sello electrónico cualificado (eIDAS / QES) o sello del colegio: "
                        "[pendiente]</i>", S["quote"]))
    return el


def parse_md(lines, S):
    story, i = [], 0
    skip_first_h1 = True  # el título ya está en la portada
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1]):
            rows, first = [], True
            while i < len(lines) and lines[i].strip().startswith("|"):
                if re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i]):
                    i += 1
                    continue
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                st = S["cellh"] if first else S["cell"]
                rows.append([Paragraph(inline(c) if not first else html.escape(clean(c)), st) for c in cells])
                first = False
                i += 1
            if rows:
                t = Table(rows, repeatRows=1, hAlign="LEFT")
                style = [("GRID", (0, 0), (-1, -1), 0.4, HAIR),
                         ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                         ("LINEABOVE", (0, 0), (-1, 0), 1.0, ACCENT),
                         ("VALIGN", (0, 0), (-1, -1), "TOP"),
                         ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                         ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
                for r in range(2, len(rows), 2):
                    style.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
                t.setStyle(TableStyle(style))
                story += [t, Spacer(1, 8)]
            continue
        s = ln.strip()
        if not s:
            story.append(Spacer(1, 3))
        elif s.startswith("#### "):
            story.append(Paragraph(inline(s[5:]), S["h3"]))
        elif s.startswith("### "):
            story.append(Paragraph(inline(s[4:]), S["h3"]))
        elif s.startswith("## "):
            story.append(Paragraph(inline(s[3:]), S["h2"]))
        elif s.startswith("# "):
            if skip_first_h1:
                skip_first_h1 = False
            else:
                story.append(Paragraph(inline(s[2:]), S["h1"]))
        elif s.startswith(">"):
            story.append(Paragraph(inline(s.lstrip("> ")), S["quote"]))
        elif re.match(r"^[-*] ", s):
            story.append(Paragraph("- " + inline(s[2:]), S["li"]))
        elif re.match(r"^\d+\. ", s):
            story.append(Paragraph(inline(s), S["li"]))
        elif set(s) <= set("-—=*_"):
            pass
        else:
            story.append(Paragraph(inline(s), S["body"]))
        i += 1
    return story


def main():
    global REF, CASO, BORRADOR
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--ref", default="BTP/PER/2026-001")
    ap.add_argument("--caso", default='Prueba digital de ciberacoso en redes — caso "cuenta tumbada"')
    ap.add_argument("--lugar", default="España")
    ap.add_argument("--fecha", default="28 de junio de 2026")
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    REF, CASO, BORRADOR = a.ref, a.caso, (not a.final)
    S = build_styles()
    lines = open(a.src, encoding="utf-8").read().splitlines()
    story = cover(S, a.caso, a.lugar, a.fecha) + parse_md(lines, S) + signature(S, a.lugar)
    SimpleDocTemplate(a.out, pagesize=A4, topMargin=2.5 * cm, bottomMargin=2.5 * cm,
                      leftMargin=3 * cm, rightMargin=2 * cm,
                      title="Informe Pericial Informático", author="Perito a designar").build(
        story, canvasmaker=PericialCanvas)
    print("PDF pericial:", a.out)


if __name__ == "__main__":
    main()
