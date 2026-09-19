#!/usr/bin/env python3
"""tools/_lexico_publico.py — fuente ÚNICA del léxico/PII vetado en PÚBLICO (Fase 0, H3).

La usa tools/salida.py para HACER VISIBLE una posible fuga antes de que {{TITULAR}} apruebe
un borrador hacia fuera (OUTWARD), y el auditor de cajas comparte estas mismas listas
(importadas) para no derivar. Solo DETECTA y gradúa; NO bloquea (decide el gate humano).
Normaliza Unicode/homoglifos/zero-width para que acentos o letras griegas no evadan.

Stdlib puro (re, unicodedata): sin red, sin deps — seguro de importar en el choke-point.
"""
import re
import unicodedata

# Léxico que NUNCA debe salir en público (el muro: ni "{{CONTACTO}}" ni "ingeniera").
# «vacuna» salió de la lista el 11-sep-26: {{TITULAR}} levantó ese veto el 29-7-26
# (.claude/rules/marca-copy.md). Con ella salen «inoculo» e «inmunizacion personalizada», que
# solo estaban para que no se colara «vacuna» por sinónimo. Solo el copy PÚBLICO; el borde de
# egress a LLMs de terceros (tools/borde.py) sigue tratando «vacuna» como tripwire.
LEXICO = ("contacto", "cientific", "metastas", "oncolog", "neoantigeno")
# Genes/biomarcadores distintivos (se omiten siglas ambiguas: ATM=cajero, KIT, MET/RET).
GENES = ("brca1", "brca2", "tp53", "esr1", "pik3ca", "rb1", "pten", "akt1", "erbb2",
         "her2", "her-2", "kras", "egfr", "palb2", "chek2", "cdh1", "alk", "eml4",
         "ros1", "ntrk", "braf", "fgfr", "flt3", "idh1", "idh2", "pdl1")

_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_HOMO = {ord(a): b for a, b in {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "і": "i", "ѕ": "s",
    "ј": "j", "ο": "o", "α": "a", "ε": "e", "ρ": "p", "ν": "v", "κ": "k",
    "ι": "i", "τ": "t", "0": "o",
}.items()}


def _norm(t):
    t = t.lower().translate(_ZW).translate(_HOMO)
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def _norm_nospace(t):
    return re.sub(r"\s+", "", _norm(t))


RE_GEN = re.compile(r"\b(?:%s)\b" % "|".join(GENES))
RE_GEN_PEGADO = re.compile(r"\b(?:%s)\d*[A-Za-z]\d{1,4}[A-Za-z*]" % "|".join(GENES), re.I)
RE_HGVS = re.compile(r"\b[cp]\.\d|\bp\.[A-Za-z]{3}\d+", re.I)
RE_AA = re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY]\d{2,4}[ACDEFGHIKLMNPQRSTVWY*]\b")
RE_CLIN = re.compile(r"mutaci|biopsia|al[eé]lic|\bvaf\b|gen[oé]?tic|\bexon\b|exón"
                     r"|oncolog|tumor|metasta|\bc\.\d|\bp\.", re.I)
RE_TEL_SEP = re.compile(r"(?<!\d)(?:\+?34[\s.\-]?)?[6-9]\d{0,2}(?:[\s.\-]\d{2,3}){2,3}(?!\d)")
RE_TEL_PLANO = re.compile(r"(?<!\d)[6-9]\d{8}(?!\d)")
RE_TEL_CTX = re.compile(r"tel[eé]fono|\btel\b|\btlf\b|tfno|whatsapp|wasap|m[oó]vil"
                        r"|ll[aá]ma|ll[aá]mame|\bfijo\b|\+34", re.I)


def revisar(texto):
    """Devuelve la lista de avisos (str) de léxico/PII público hallados en `texto`.
    Lista vacía = limpio. Pensado para contenido de cara al MUNDO (OUTWARD)."""
    if not texto:
        return []
    avisos = []
    bajo = _norm(texto)
    sin_esp = _norm_nospace(texto)
    for lex in LEXICO:
        if lex in bajo or lex in sin_esp:
            avisos.append("léxico vetado en público: '%s'" % lex)
    if RE_GEN.search(bajo) or RE_GEN_PEGADO.search(texto):
        avisos.append("gen/biomarcador oncológico nombrado (PII clínica)")
    if RE_HGVS.search(texto) or any(RE_AA.search(f) and RE_CLIN.search(f)
                                    for f in re.split(r"[.\n;:]", texto)):
        avisos.append("posible variante genómica (PII clínica)")
    if RE_TEL_SEP.search(texto) or (RE_TEL_PLANO.search(texto) and RE_TEL_CTX.search(texto)):
        avisos.append("posible teléfono (PII)")
    return avisos
