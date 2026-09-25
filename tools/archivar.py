#!/usr/bin/env python3
"""Archivador — clasifica ~/Downloads + ~/Desktop segun la taxonomia de
Beyond the Protocol y los copia a la carpeta "fuente de verdad".

- DRY-RUN por defecto: NO toca nada, solo escribe un manifest para revisar.
- Con --apply: COPIA (nunca mueve, nunca borra; originales intactos).
- Lo personal (WhatsApp/Telegram/fotos/apps) se OMITE.
- Lo clinico-sensible va a una subcarpeta PRIVADA.
"""
import os, re, sys, csv, shutil
from pathlib import Path
from datetime import datetime

HOME = Path.home()
SOURCES = [HOME / 'Downloads', HOME / 'Desktop']
ROOT = Path(__file__).resolve().parents[1]  # raíz del repo (este fichero vive en tools/)
CANON = ROOT / '00_FUENTE-DE-VERDAD'
APPLY = '--apply' in sys.argv

# Reglas ordenadas por prioridad (primer match gana).
RULES = [
  ('SKIP',     [r'whatsapp', r'telegram', r'\.dmg$', r'\.pkg$', r'^img_\d',
                r'\.heic$', r'photo ?booth', r'screenshot', r'captura de pantalla']),
  ('VIDEO',    [r'\.mov$', r'\.mp4$']),
  ('CLINICAL', [r'cl[ií]nic', r'historial', r'biops', r'rebiopsia', r'anatomia',
                r'patolog', r'anal[ií]tic', r'\bpet\b', r'dicom', r'\bmri\b',
                r'\brm[-_ ]', r'\btac\b', r'\bct[-_ ]', r'\.nii', r'\bseg\b',
                r'deposit', r'estimate', r'pnv21', r'fred.?hutch', r'antwortschreiben',
                r'int-\d', r'cdc importer', r'intro_questions', r'archivo-clinico',
                r'informe', r'oncolog', r'elacestrant', r'dipcan', r'hueso',
                r'vertebra', r'\bsuv\b', r'\bfdg\b', r'galio', r'dotato', r'resultados']),
  ('CONTACTO',    [r'contacto']),
  ('ASOC',     [r'asociaci', r'estatuto', r'\blegal', r'consentimiento', r'gdpr',
                r'rgpd', r'fundaci']),
  ('WEB',      [r'plausible', r'analytics', r'copywriting', r'helptitular']),
  ('MARCA',    [r'marca', r'dossier', r'slide', r'portada', r'spotify', r'illustration',
                r'gouache', r'midjourney', r'perfil', r'constelacion', r'logo', r'brand',
                r'l[áa]mina', r'postal', r'pegatina', r'forro', r'imprenta', r'nmdlv']),
  ('IA',       [r'revision', r'research', r'paper', r'biontech', r'clinical trials by',
                r'comit', r'radar', r'prompt', r'claude', r'fable', r'\bgpt']),
  ('REU',      [r'reuni[oó]n', r'meeting', r'\bacta', r'minutes']),
]
DEST = {
  'CLINICAL': '01 · Tratamiento/_PRIVADO_CLINICO',
  'CONTACTO': '02 · {{CONTACTO}}', 'ASOC': '03 · Asociación', 'IA': '04 · IA',
  'WEB': '05 · Web', 'REU': '06 · Reuniones', 'MARCA': '07 · Marca',
  'BANDEJA': '00 · Bandeja de entrada',
  'VIDEO': '00 · Bandeja de entrada/_VIDEOS (revisar)',
}
SENSITIVE = {'CLINICAL'}

def classify(name):
    low = name.lower()
    for cat, pats in RULES:
        for p in pats:
            if re.search(p, low):
                return cat
    return 'BANDEJA'

def human(n):
    f = float(n)
    for u in ['B', 'KB', 'MB', 'GB']:
        if f < 1024:
            return f"{f:.0f}{u}"
        f /= 1024
    return f"{f:.1f}TB"

rows, counts, bycat = [], {}, {}
for src in SOURCES:
    if not src.exists():
        continue
    for entry in sorted(src.iterdir()):
        if entry.name.startswith('.'):
            continue
        cat = classify(entry.name)
        if entry.is_dir():
            kind, size_s = 'carpeta', '—'
        else:
            kind = 'archivo'
            try:
                size_s = human(entry.stat().st_size)
            except OSError:
                size_s = '?'
        action = ('OMITIR (personal)' if cat == 'SKIP'
                  else 'REVISAR (vídeo)' if cat == 'VIDEO' else 'copiar')
        priv = 'PRIVADO' if cat in SENSITIVE else ('—' if cat in ('SKIP', 'VIDEO') else 'compartible')
        rows.append((str(entry), entry.name, kind, size_s, cat, priv, action))
        counts[action] = counts.get(action, 0) + 1
        bycat[cat] = bycat.get(cat, 0) + 1

manifest = Path(__file__).parent / 'manifest-archivado.csv'
with open(manifest, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['ruta', 'nombre', 'tipo', 'tamaño', 'categoria', 'privacidad', 'accion'])
    w.writerows(rows)

print(f"== ARCHIVADOR ({'APLICAR' if APPLY else 'DRY-RUN — no toco nada'}) ==")
print(f"Fuentes: ~/Downloads, ~/Desktop · {len(rows)} elementos\n")
print("Por categoría -> destino:")
for c in ['CLINICAL', 'IA', 'WEB', 'MARCA', 'ASOC', 'CONTACTO', 'REU', 'BANDEJA', 'VIDEO', 'SKIP']:
    if bycat.get(c):
        print(f"  {c:9} {bycat[c]:>4}  -> {DEST.get(c, '(no se copia)')}")
print("\nPor acción:")
for a, n in sorted(counts.items()):
    print(f"  {n:>4}  {a}")
print(f"\nManifest completo (revísalo): {manifest}")

if APPLY:
    copied = 0
    CANON.mkdir(parents=True, exist_ok=True)
    idx = CANON / 'INDICE.md'
    new = not idx.exists()
    with open(idx, 'a') as ix:
        if new:
            ix.write("# ÍNDICE — Fuente de verdad (Caso {{TITULAR}})\n\n"
                     "| Fecha | Nombre | Categoría | Privacidad | Ruta |\n|---|---|---|---|---|\n")
        for ruta, nombre, kind, size_s, cat, priv, action in rows:
            if action != 'copiar':
                continue
            dest = CANON / DEST[cat]
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / nombre
            try:
                if Path(ruta).is_dir():
                    if not target.exists():
                        shutil.copytree(ruta, target)
                else:
                    shutil.copy2(ruta, target)
                ix.write(f"| {datetime.now():%Y-%m-%d} | {nombre} | {cat} | {priv} | {DEST[cat]}/{nombre} |\n")
                copied += 1
            except Exception as e:
                print(f"  ! {nombre}: {e}")
    print(f"\nCopiados {copied} elementos a:\n  {CANON}\n(originales intactos). Índice: {idx}")
