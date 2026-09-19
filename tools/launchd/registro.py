#!/usr/bin/env python3
"""tools/launchd/registro.py — fuente unica de daemons launchd + su 'concern' (que trabajo hace).

Lee esto ANTES de crear un plist nuevo: "ya hay algo para este trabajo?". Lo consume
activar_daemon.py para el guardian de duplicados. Parte del plan anti-colision (R2).

  registro.py estado        # REGISTRO vs launchctl (que deberia / que esta cargado)
  registro.py regenera      # re-escanea launchd/*.plist -> actualiza REGISTRO.json
                            #   preserva concern/nota existentes; concern nuevo = short-label
  registro.py concern LABEL # imprime el concern de un label
"""
import glob
import json
import os
import plistlib
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRO = os.path.join(HERE, 'REGISTRO.json')


def _script_de(d):
    args = [str(a) for a in d.get('ProgramArguments', [])]
    for a in args:
        if a.endswith('.py') or a.endswith('.sh'):
            return os.path.basename(a)
    return os.path.basename(args[1]) if len(args) > 1 else (args[0] if args else '?')


def _cargar():
    try:
        return json.load(open(REGISTRO, encoding='utf-8'))
    except Exception:
        return {'_nota': ('Fuente unica de daemons launchd. Lee esto ANTES de crear un plist '
                          'nuevo (que trabajo ya esta cubierto?). Lo usa activar_daemon.py. '
                          'concern = identificador del TRABAJO (no del label); dos daemons con '
                          'el mismo concern = duplicado.'),
                'daemons': {}}


def regenera():
    reg = _cargar()
    dae = reg.setdefault('daemons', {})
    vistos = set()
    for p in sorted(glob.glob(os.path.join(HERE, 'com.btp.*.plist'))):
        try:
            d = plistlib.load(open(p, 'rb'))
        except Exception:
            continue
        label = d.get('Label')
        if not label:
            continue
        vistos.add(label)
        prev = dae.get(label, {})
        dae[label] = {
            'script': _script_de(d),
            'concern': prev.get('concern') or label.replace('com.btp.', ''),
            'nota': prev.get('nota', ''),
        }
    huerfanos = [l for l in dae if l not in vistos]
    json.dump(reg, open(REGISTRO, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('REGISTRO.json: %d daemons' % len(dae))
    if huerfanos:
        print('  (en REGISTRO pero sin plist:', ', '.join(huerfanos), '- revisa)')
    return 0


def _labels_cargados():
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True).stdout
    except Exception:
        return set()
    return {ln.split('\t')[-1].strip() for ln in out.splitlines()[1:] if ln.split('\t')}


def estado():
    reg = _cargar().get('daemons', {})
    cargados = _labels_cargados()
    dup = {}
    for l, m in reg.items():
        dup.setdefault(m.get('concern'), []).append(l)
    print('%-34s %-9s %s' % ('Label', 'launchctl', 'concern'))
    print('-' * 60)
    for lab in sorted(set(reg) | {l for l in cargados if l.startswith('com.btp.')}):
        print('%-34s %-9s %s' % (lab, 'CARGADO' if lab in cargados else '-',
                                 reg.get(lab, {}).get('concern', '?? NO EN REGISTRO')))
    colisiones = {c: ls for c, ls in dup.items()
                  if len([l for l in ls if l in cargados]) > 1}
    if colisiones:
        print('\n[!] MISMO concern con >1 daemon ACTIVO (posible duplicado):')
        for c, ls in colisiones.items():
            print('  %s -> %s' % (c, ', '.join(l for l in ls if l in cargados)))
    return 0


def main(argv):
    cmd = argv[0] if argv else 'estado'
    if cmd == 'regenera':
        return regenera()
    if cmd == 'estado':
        return estado()
    if cmd == 'concern' and len(argv) > 1:
        print(_cargar().get('daemons', {}).get(argv[1], {}).get('concern', '(no en registro)'))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
