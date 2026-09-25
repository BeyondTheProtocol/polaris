#!/usr/bin/env python3
"""Markdown -> PDF legible (reportlab): títulos, párrafos, viñetas, TABLAS, negrita/cursiva. Sin deps externas.
Uso: python3 md_to_pdf_pro.py entrada.md salida.pdf"""
import sys, re, html
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿⌀-⏿■-◿]")
def clean(t):
    for a, b in [("→","->"),("⟶","->"),("⇒","=>"),("≥",">="),("≤","<="),("×","x"),("±","+/-"),
                 ("°C","C"),("°",""),("⁷","7"),("¹","1"),("²","2"),("⁰","0"),("₂","2"),("₃","3"),("•","-")]:
        t = t.replace(a, b)
    return EMOJI.sub("", t)
def inline(t):
    t = html.escape(clean(t))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
    t = re.sub(r"(?<![\*\w])\*(?!\*)(.+?)(?<!\*)\*(?![\*\w])", r"<i>\1</i>", t)
    return t

def main():
    src, out = sys.argv[1], sys.argv[2]
    lines = open(src, encoding="utf-8").read().splitlines()
    ss = getSampleStyleSheet()
    PUR = colors.HexColor("#5b2a86")
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceBefore=12, spaceAfter=6, textColor=PUR)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4, textColor=PUR)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=11, spaceBefore=7, spaceAfter=3)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=13)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.5, leading=11)
    quote = ParagraphStyle("quote", parent=body, leftIndent=8, textColor=colors.HexColor("#555"), fontName="Helvetica-Oblique")
    li = ParagraphStyle("li", parent=body, leftIndent=12)
    def P(txt, st):
        try: return Paragraph(inline(txt), st)
        except Exception: return Paragraph(html.escape(clean(txt)), st)
    story, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("|") and i+1 < len(lines) and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i+1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i]): i += 1; continue
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append([P(c, cell) for c in cells]); i += 1
            if rows:
                t = Table(rows, repeatRows=1, hAlign="LEFT")
                t.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#ccc")),
                    ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#efe7f5")),("VALIGN",(0,0),(-1,-1),"TOP"),
                    ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
                    ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3)]))
                story += [t, Spacer(1, 6)]
            continue
        s = ln.strip()
        if not s: story.append(Spacer(1, 4))
        elif s.startswith("#### "): story.append(P(s[5:], h3))
        elif s.startswith("### "): story.append(P(s[4:], h3))
        elif s.startswith("## "): story.append(P(s[3:], h2))
        elif s.startswith("# "): story.append(P(s[2:], h1))
        elif s.startswith(">"): story.append(P(s.lstrip("> "), quote))
        elif re.match(r"^[-*] ", s): story.append(P("- " + s[2:], li))
        elif re.match(r"^\d+\. ", s): story.append(P(s, li))
        elif set(s) <= set("-—=*_"): pass
        else: story.append(P(s, body))
        i += 1
    SimpleDocTemplate(out, pagesize=A4, topMargin=18*mm, bottomMargin=18*mm,
                      leftMargin=16*mm, rightMargin=16*mm, title="Protocolo Maestro Coordinado - Biopsia").build(story)
    print("PDF:", out)

if __name__ == "__main__":
    main()
