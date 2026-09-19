#!/usr/bin/env python3
"""tools/activar_daemon.py — ÚNICA vía sancionada para encender un daemon launchd de BTP.

Contexto: ~/Library/LaunchAgents/ vive fuera de git, del worktree y de todo candado, así
que cualquier sesión podía cp+launchctl load sin comprobar si ya había un daemon para el
mismo trabajo, ni si el código salía de una rama sin fusionar. Aquí van los guardianes que
faltaban (plan anti-colisión; comités git + contacto).

Guardianes (fail-closed):
  1. NO-WORKTREE — rechaza si WorkingDirectory/ProgramArguments apuntan a .claude/worktrees/.
     Un daemon 24/7 corre desde CASA BASE, nunca desde una rama de feature.
  2. LABEL ÚNICO — rechaza si el Label ya está cargado (salvo --reemplaza).
  3. CONCERN ÚNICO — avisa si otro Label ACTIVO cubre el mismo 'concern' (launchd/REGISTRO.json);
     exige --reemplaza para seguir. Evita dos daemons haciendo el mismo trabajo.
Serializa con _lock.lock('launchd'). Stdlib puro. Apagar = launchctl bootout + rm del plist.

uso:
  activar_daemon.py <plist|label> [--reemplaza] [--kick] [--dry]
  activar_daemon.py --estado          # REGISTRO vs launchctl
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _lock  # noqa: E402

LAUNCHD_DIR = os.path.join(HERE, 'launchd')
REGISTRO = os.path.join(LAUNCHD_DIR, 'REGISTRO.json')
LA = os.path.expanduser('~/Library/LaunchAgents')
DOMAIN = 'gui/%d' % os.getuid()


def _labels_cargados():
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True).stdout
    except Exception:
        return set()
    labs = set()
    for ln in out.splitlines()[1:]:
        parts = ln.split('\t')
        if parts:
            labs.add(parts[-1].strip())
    return labs


def _esta_cargado(label):
    return label in _labels_cargados()


def _gritar_si_quedo_apagado(label, reemplaza):
    """Si veníamos de un `bootout` y el `bootstrap` no levantó, el daemon está APAGADO por culpa
    nuestra. Eso no se puede quedar en un mensaje de stderr que nadie lee: sale por el
    choke-point, que respeta el muro y el anti-spam. Fail-soft: avisar nunca puede romper esto.
    """
    if not reemplaza or _esta_cargado(label):
        return
    aviso = ('⚠️ Intenté recargar el daemon «%s» y se quedó APAGADO: el bootout salió bien pero '
             'el bootstrap falló tres veces. Nada lo está corriendo ahora mismo. Se levanta con '
             '`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/%s.plist`.' % (label, label))
    print('⚠️ %s quedó APAGADO tras el bootout' % label, file=sys.stderr)
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import salida
        salida.report_to_titular(aviso, categoria='operativo', fuente='activar_daemon')
    except Exception:
        pass


def _registro():
    try:
        return json.load(open(REGISTRO, encoding='utf-8')).get('daemons', {})
    except Exception:
        return {}


def _resolver_plist(arg):
    if arg.endswith('.plist') and os.path.exists(arg):
        return os.path.abspath(arg)
    cand = os.path.join(LAUNCHD_DIR, arg if arg.endswith('.plist') else arg + '.plist')
    return cand if os.path.exists(cand) else None


def _rutas_worktree(d):
    campos = [d.get('WorkingDirectory', '')]
    campos += [str(a) for a in d.get('ProgramArguments', [])]
    campos += [str(v) for v in (d.get('EnvironmentVariables') or {}).values()]
    campos += [d.get('StandardOutPath', ''), d.get('StandardErrorPath', '')]
    return [c for c in campos if '/.claude/worktrees/' in (c or '')]


_HOMES_CONOCIDOS = ('/Users/titular/', '/Users/polaris/')


def _reescribir_home(texto):
    # Reescribe los homes de dueno conocidos del repo-union (Air + mini) al $HOME de ESTA maquina, para
    # que un plist versionado con la ruta del portatil no se instale roto (exit 78 al disparar) ni al
    # reves. Scope ACOTADO a los dos usernames conocidos (no un /Users/<x>/ a ciegas -> fail-safe).
    # Idempotente: si ya esta en el home local, no-op. Consolida el sed de setup_polaris_air.sh (que solo
    # cubria polaris->$HOME). Devuelve (texto_nuevo, cambios) con cambios=[(viejo, nuevo, n)] para el --dry.
    home = os.path.expanduser('~') + '/'
    nuevo, cambios = texto, []
    for viejo in _HOMES_CONOCIDOS:
        if viejo != home:
            n = nuevo.count(viejo)
            if n:
                cambios.append((viejo, home, n))
                nuevo = nuevo.replace(viejo, home)
    return nuevo, cambios


def estado():
    reg, cargados = _registro(), _labels_cargados()
    print('%-34s %-9s %-9s %s' % ('Label', 'REGISTRO', 'launchctl', 'concern'))
    print('-' * 66)
    for lab in sorted(set(reg) | {l for l in cargados if l.startswith('com.btp.')}):
        print('%-34s %-9s %-9s %s' % (
            lab, 'si' if lab in reg else '-',
            'CARGADO' if lab in cargados else '-',
            reg.get(lab, {}).get('concern', '?')))
    faltan = [l for l in cargados if l.startswith('com.btp.') and l not in reg]
    if faltan:
        print('\n[!] cargados pero NO en REGISTRO (anadelos):', ', '.join(sorted(faltan)))
    return 0


def activar(arg, reemplaza=False, kick=False, dry=False):
    plist = _resolver_plist(arg)
    if not plist:
        print('x no encuentro plist para %r' % arg, file=sys.stderr)
        return 2
    try:
        d = plistlib.load(open(plist, 'rb'))
    except Exception as e:
        print('x plist ilegible: %s' % e, file=sys.stderr)
        return 2
    label = d.get('Label')
    if not label:
        print('x el plist no tiene Label', file=sys.stderr)
        return 2

    wt = _rutas_worktree(d)
    if wt:
        print('x GUARDIAN no-worktree: el plist apunta a un worktree, no a casa base:', file=sys.stderr)
        for r in wt:
            print('    ' + r, file=sys.stderr)
        print('  Un daemon 24/7 corre desde casa base (fusiona antes). Aborto.', file=sys.stderr)
        return 3

    cargados, reg = _labels_cargados(), _registro()
    if label in cargados and not reemplaza:
        print('x GUARDIAN label-unico: %s ya esta cargado. --reemplaza para recargar.' % label, file=sys.stderr)
        return 4

    concern = reg.get(label, {}).get('concern')
    if concern:
        gemelos = [l for l, m in reg.items()
                   if l != label and m.get('concern') == concern and l in cargados]
        if gemelos and not reemplaza:
            print('x GUARDIAN concern-unico: ya hay daemon(s) ACTIVOS con el trabajo %r:' % concern, file=sys.stderr)
            print('    ' + ', '.join(gemelos), file=sys.stderr)
            print('  Duplicado? Reconcilia, o --reemplaza si de verdad lo sustituye.', file=sys.stderr)
            return 5
    else:
        print('[!] %s no esta en REGISTRO.json - anadelo (concern, script).' % label)

    if dry:
        _, cambios = _reescribir_home(open(plist, encoding='utf-8').read())
        if cambios:
            print('DRY: reescribiria el home del repo-union al de esta maquina:')
            for viejo, nuevo, n in cambios:
                print('    %s -> %s  (x%d)' % (viejo, nuevo, n))
        else:
            print('DRY: sin reescritura de home (rutas ya en %s)' % (os.path.expanduser('~') + '/'))
        print('DRY: pasaria los guardianes y activaria %s desde %s' % (label, plist))
        return 0

    with _lock.lock('launchd', timeout=30):
        os.makedirs(LA, exist_ok=True)
        destino = os.path.join(LA, label + '.plist')
        # Reescribe el home del repo-union (Air+mini) al de ESTA maquina para que un plist versionado con
        # la ruta del portatil no se instale roto (exit 78 al disparar). Escribe SOLO en destino, NUNCA en
        # el plist fuente del repo. .bak del destino previo = red de rollback que shutil.copyfile no daba.
        contenido, _cam = _reescribir_home(open(plist, encoding='utf-8').read())
        if os.path.exists(destino):
            shutil.copyfile(destino, destino + '.bak')
        with open(destino, 'w', encoding='utf-8') as fh:
            fh.write(contenido)
        if reemplaza:
            subprocess.run(['launchctl', 'bootout', '%s/%s' % (DOMAIN, label)], capture_output=True, text=True)
        # REINTENTO + COMPROBACIÓN (31-jul-26). Con `--reemplaza` ya hemos hecho `bootout`: si el
        # `bootstrap` de después falla y nos vamos, el daemon queda DESCARGADO, no como estaba. El
        # 31-jul eso dejó el dispatcher del lazo 24/7 caído ~30 s con un `Bootstrap failed: 5:
        # Input/output error` — un error transitorio de launchd que se resuelve reintentando. Un
        # tool de ENCENDIDO que apaga en silencio es peor que no tenerlo: nadie va a mirar si
        # encendió, porque para eso lo llamas.
        r = None
        for intento in range(3):
            r = subprocess.run(['launchctl', 'bootstrap', DOMAIN, destino],
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
            if intento < 2:
                print('· bootstrap falló (%s), reintento %d/2…'
                      % ((r.stderr.strip() or r.stdout.strip())[:80], intento + 1), file=sys.stderr)
                time.sleep(1.5)
        if r.returncode != 0:
            print('x bootstrap fallo tras 3 intentos: %s'
                  % (r.stderr.strip() or r.stdout.strip()), file=sys.stderr)
            _gritar_si_quedo_apagado(label, reemplaza)
            return 6
        # No basta con que `bootstrap` devuelva 0: se comprueba que el label esté DE VERDAD
        # cargado. Verificar el efecto, no que el comando corrió.
        if not _esta_cargado(label):
            print('x bootstrap dijo OK pero %s NO aparece cargado' % label, file=sys.stderr)
            _gritar_si_quedo_apagado(label, reemplaza)
            return 6
        if kick:
            subprocess.run(['launchctl', 'kickstart', '-k', '%s/%s' % (DOMAIN, label)], capture_output=True, text=True)
    print('ok %s activado desde casa base%s' % (label, ' (kick)' if kick else ''))
    return 0


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if argv[0] == '--estado':
        return estado()
    return activar(argv[0], reemplaza='--reemplaza' in argv[1:],
                   kick='--kick' in argv[1:], dry='--dry' in argv[1:])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
