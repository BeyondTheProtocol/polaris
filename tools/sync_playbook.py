#!/usr/bin/env python3
"""sync_playbook — sincroniza el playbook clínico (ESTADO-ACTUAL.md) entre las dos
máquinas de {{TITULAR}} (Air ↔ mini/Polaris) por Tailscale-SSH, conflict-safe.

Por qué: el playbook (`00_FUENTE-DE-VERDAD/ESTADO-ACTUAL.md`) es la foto del caso que el
orquestador lee para el parte diario. Es clínico y gitignored (no viaja por git, correcto),
así que las dos máquinas divergen sin coserse (verificado 11-jul: Air 9-jul, mini 27-jun).
Esto lo cose SIN git y SIN nube: rsync por SSH entre las dos máquinas de {{TITULAR}} (LAN-equivalente
por Tailscale), que el propio doc de infra bendice para lo clínico.

Diseño (revisado por contacto):
  - NO hay máquina canónica fija. La verdad es la copia editada más recientemente, salvo
    conflicto real.
  - Conflicto real por HASH-BASELINE: se guarda el sha256 del contenido que ambos lados
    acordaron en el último sync logrado. En cada corrida se compara el sha ACTUAL de cada
    lado contra ese baseline:
       · ambos == baseline .......... ya sincronizado, nada que hacer
       · solo uno != baseline ....... propaga ese lado al otro (sin ambigüedad)
       · ambos != baseline y difieren  CONFLICTO → avisa a {{TITULAR}}, NO toca nada
       · sin baseline y difieren ..... no se puede adivinar → CONFLICTO (no pisa)
    (mtime NO basta: no distingue "cambió uno" de "cambiaron los dos".)
  - Guardas: no propaga un fichero vacío/sospechosamente pequeño (anti-corrupto); hace
    BACKUP del fichero que va a pisar (con rotación); AUDITORÍA append-only en jsonl.
  - Por defecto DRY (no escribe nada, solo imprime el plan). --apply ejecuta.

BOOTSTRAP (--elige): la 1ª sync no tiene baseline y las dos copias ya divergen, así que la
  guarda de conflicto se niega a elegir (por diseño). Para arrancar, {{TITULAR}} decide QUÉ copia
  es la buena y se declara con --elige local|remoto: se propaga ese lado y se fija el baseline
  a su sha. Es la ÚNICA vía de pisar sin baseline; sigue respetando la guarda anti-corrupto.

Uso:
  python3 tools/sync_playbook.py                       # dry: dice qué haría
  python3 tools/sync_playbook.py --apply               # ejecuta el sync/propagación
  python3 tools/sync_playbook.py --remoto air          # (desde el mini) sincroniza contra el Air
  python3 tools/sync_playbook.py --elige local --apply # BOOTSTRAP: este equipo es la verdad
  python3 tools/sync_playbook.py --si-viejo 20 --apply # pull-al-abrir: solo intenta si hace >20 min
  python3 tools/sync_playbook.py --nudge-frescura 7 --apply # Fase 3: avisa si lleva >7 días sin cambiar
Requiere un alias SSH al otro lado (por defecto `polaris`; ver ~/.claude/polaris-access.sh).
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.environ.get("BTP_REPO") or os.path.expanduser("~/claudecode")
PLAYBOOK_REL = "00_FUENTE-DE-VERDAD/ESTADO-ACTUAL.md"
STATE = os.environ.get("BTP_STATE_DIR") or os.path.join(REPO, "tools", "state")
SYNC_DIR = os.path.join(STATE, "sync_playbook")
BASELINE = os.path.join(SYNC_DIR, "baseline.json")
BACKUP_DIR = os.path.join(SYNC_DIR, "backups")
LAST_ATTEMPT = os.path.join(SYNC_DIR, "last_attempt.txt")  # throttle del pull-al-abrir (--si-viejo)
CONTENT_SEEN = os.path.join(SYNC_DIR, "content_seen.json")  # Fase 3: sha + desde cuándo sin cambiar
LAST_NUDGE = os.path.join(SYNC_DIR, "last_nudge.txt")       # Fase 3: anti-spam del aviso de frescura

MIN_BYTES = 200        # por debajo = sospechoso (vacío/truncado): NUNCA se propaga
KEEP_BACKUPS = 20      # rotación: se conservan los N backups más recientes


# ─── utilidades ───────────────────────────────────────────────────────────────
def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _local_info(path):
    """(existe, sha256, tamaño) del fichero local. sha/tamaño None si no existe."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        return True, _sha256_bytes(data), len(data)
    except FileNotFoundError:
        return False, None, 0


def _remote_info(remoto, path):
    """(existe, sha256, tamaño) del fichero remoto vía ssh. Levanta si el ssh falla feo."""
    # Un solo ssh: imprime "SHA<tab>BYTES" o "MISSING". shasum -a 256 = disponible en macOS.
    cmd = (
        'f=%s; if [ -f "$f" ]; then printf "%%s %%s" '
        '"$(shasum -a 256 "$f" | cut -d" " -f1)" "$(wc -c < "$f" | tr -d " ")"; '
        'else printf MISSING; fi'
    ) % _rpath(path)
    out = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", remoto, cmd],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError("ssh a '%s' falló: %s" % (remoto, (out.stderr or "").strip()[:200]))
    s = out.stdout.strip()
    if s == "MISSING" or not s:
        return False, None, 0
    parts = s.split()
    return True, parts[0], (int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)


def _shq(p):
    return "'" + p.replace("'", "'\\''") + "'"


def _rpath(path):
    """Expresión de shell para un path REMOTO, expandiendo ~ correctamente (dentro de comillas
    simples la tilde NO se expande). `~/x` → `"$HOME"/'x'`."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"/' + _shq(path[2:])
    return _shq(path)


def _load_baseline(key):
    try:
        with open(BASELINE, encoding="utf-8") as f:
            return json.load(f).get(key)
    except Exception:
        return None


def _save_baseline(key, sha):
    os.makedirs(SYNC_DIR, exist_ok=True)
    try:
        with open(BASELINE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    data[key] = {"sha": sha, "ts": _now()}
    tmp = BASELINE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BASELINE)


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(entry):
    os.makedirs(SYNC_DIR, exist_ok=True)
    fecha = datetime.datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(SYNC_DIR, "auditoria-%s.jsonl" % fecha)
    entry = dict(entry, ts=_now())
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _backup_local(path, etiqueta):
    """Copia el fichero local que se va a pisar, antes de pisarlo. Con rotación."""
    if not os.path.exists(path):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, "ESTADO-ACTUAL.%s.%s.md" % (stamp, etiqueta))
    shutil.copy2(path, dest)
    # rotación
    todos = sorted(glob.glob(os.path.join(BACKUP_DIR, "ESTADO-ACTUAL.*.md")))
    for viejo in todos[:-KEEP_BACKUPS]:
        try:
            os.remove(viejo)
        except OSError:
            pass
    return dest


def _demasiado_reciente(minutos):
    """True si se intentó sync hace menos de `minutos` (throttle del pull-al-abrir).
    Sin registro previo → False (nunca se intentó → adelante)."""
    try:
        edad = datetime.datetime.now().timestamp() - os.path.getmtime(LAST_ATTEMPT)
    except OSError:
        return False
    return edad < minutos * 60


def _marca_intento():
    """Sella el timestamp del último intento de sync (para el throttle --si-viejo)."""
    os.makedirs(SYNC_DIR, exist_ok=True)
    with open(LAST_ATTEMPT, "w", encoding="utf-8") as f:
        f.write(_now() + "\n")


def _alerta(msg, dry):
    """Avisa a {{TITULAR}} de un conflicto. En dry solo imprime; respeta HALT vía salida.py."""
    if dry:
        print("  (dry) AVISARÍA a {{TITULAR}}: " + msg)
        return
    try:
        import salida
        salida.report_to_titular(msg, categoria="humano", urgente=False, voz="calida")
    except Exception as e:  # noqa: BLE001 — nunca romper por el canal de aviso
        print("  (no se pudo enviar el aviso: %s)" % e)


def nudge_frescura(dias, dry, local_path=None):
    """Fase 3: si el CONTENIDO del playbook lleva > `dias` sin cambiar, avisa a {{TITULAR}} para que
    lo refresque (el contenido clínico lo actualiza un humano; aquí NO se autogenera nada).
    'Sin cambiar' = el mismo sha256 persiste; se registra la 1ª vez que se vio ese sha.
    Anti-spam: como mucho un aviso al día."""
    local_path = local_path or os.path.join(REPO, PLAYBOOK_REL)
    exists, sha, _ = _local_info(local_path)
    if not exists:
        print("No hay playbook local; sin nudge.")
        return {"estado": "sin_playbook"}

    # ¿cambió el contenido desde la última vez que lo vimos?
    try:
        with open(CONTENT_SEEN, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    if seen.get("sha") != sha:
        if not dry:
            os.makedirs(SYNC_DIR, exist_ok=True)
            tmp = CONTENT_SEEN + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"sha": sha, "first_seen": _now()}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONTENT_SEEN)
        print("Contenido nuevo (sha %s); reinicio el reloj de frescura." % sha[:12])
        return {"estado": "contenido_fresco", "sha": sha}

    # mismo contenido: ¿cuántos días lleva?
    try:
        primero = datetime.datetime.strptime(seen["first_seen"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        primero = datetime.datetime.now()
    edad_dias = (datetime.datetime.now() - primero).total_seconds() / 86400.0
    print("Playbook sin cambios desde %s (%.1f días; umbral %d)." % (seen.get("first_seen"), edad_dias, dias))
    if edad_dias < dias:
        return {"estado": "fresco", "edad_dias": round(edad_dias, 1)}

    # está viejo: avisar, pero como mucho una vez al día
    hoy = datetime.datetime.now().strftime("%Y-%m-%d")
    try:
        with open(LAST_NUDGE, encoding="utf-8") as f:
            ultimo = f.read().strip()
    except Exception:
        ultimo = ""
    if ultimo == hoy:
        print("Ya avisé hoy; no repito (anti-spam).")
        return {"estado": "ya_avisado_hoy", "edad_dias": round(edad_dias, 1)}

    msg = ("📋 El playbook del caso (ESTADO-ACTUAL.md) lleva **%d días** sin cambios. Si hay novedades "
           "(citas, resultados, decisiones), un repaso rápido lo mantiene fresco para el parte diario." % int(edad_dias))
    print("→ NUDGE de frescura (%.1f días)." % edad_dias)
    _alerta(msg, dry)
    if not dry:
        os.makedirs(SYNC_DIR, exist_ok=True)
        with open(LAST_NUDGE, "w", encoding="utf-8") as f:
            f.write(hoy + "\n")
    _audit({"accion": "nudge_frescura", "edad_dias": round(edad_dias, 1), "dias_umbral": dias})
    return {"estado": "nudge_enviado", "edad_dias": round(edad_dias, 1)}


# ─── lógica principal ───────────────────────────────────────────────────────────
def run(remoto="polaris", local_path=None, remote_path=None, apply=False, elige=None):
    local_path = local_path or os.path.join(REPO, PLAYBOOK_REL)
    remote_path = remote_path or ("~/claudecode/" + PLAYBOOK_REL)
    dry = not apply
    key = "%s:%s" % (remoto, remote_path)

    l_exists, l_sha, l_size = _local_info(local_path)

    try:
        r_exists, r_sha, r_size = _remote_info(remoto, remote_path)
    except Exception as e:  # noqa: BLE001 — remoto inalcanzable: fail-soft, no es un error duro
        print("No pude alcanzar '%s' (%s). Sync pospuesto, no toco nada." % (remoto, e))
        _audit({"accion": "sin_conexion", "remoto": remoto, "detalle": str(e)[:200]})
        return {"estado": "sin_conexion", "remoto": remoto}

    base = _load_baseline(key)
    base_sha = base.get("sha") if base else None

    print("Playbook local:  %s (%s, %d B)" % (local_path, (l_sha or "—")[:12], l_size))
    print("Playbook remoto: %s:%s (%s, %d B)" % (remoto, remote_path, (r_sha or "—")[:12], r_size))
    print("Baseline última sync: %s" % ((base_sha or "—")[:12]))

    res = {"remoto": remoto, "local_sha": l_sha, "remote_sha": r_sha, "base_sha": base_sha}

    # BOOTSTRAP: {{TITULAR}} declara qué copia es la buena. Rompe el empate "sin baseline" y
    # deja fijado el baseline para que las corridas normales ya sepan seguir. Es la ÚNICA vía
    # de pisar sin baseline; sigue pasando por la guarda anti-corrupto de _propagar.
    if elige:
        gana_local = elige == "local"
        gana_exists = l_exists if gana_local else r_exists
        lado = "este equipo (local)" if gana_local else "%s (remoto)" % remoto
        if not gana_exists:
            print("BOOTSTRAP abortado: elegiste %s pero ahí el playbook no existe." % lado)
            _audit({"accion": "bootstrap_lado_ausente", "elige": elige})
            return dict(res, estado="bootstrap_lado_ausente", elige=elige)
        print("BOOTSTRAP: fijas %s como la copia buena." % lado)
        if l_exists and r_exists and l_sha == r_sha:
            print("  Ya son idénticos; solo fijo el baseline.")
            if not dry:
                _save_baseline(key, l_sha)
            _audit({"accion": "bootstrap_identicos", "elige": elige, "sha": l_sha})
            return dict(res, estado="bootstrap_identicos", elige=elige)
        if gana_local:
            return _propagar("local->remoto", local_path, remoto, remote_path, l_sha, l_size,
                             res, dry, motivo="BOOTSTRAP: eliges esta copia como verdad")
        return _propagar("remoto->local", local_path, remoto, remote_path, r_sha, r_size,
                         res, dry, motivo="BOOTSTRAP: eliges la copia de %s como verdad" % remoto)

    # Casos de existencia
    if not l_exists and not r_exists:
        print("Ninguno de los dos existe. Nada que hacer.")
        return dict(res, estado="ambos_ausentes")
    if l_exists and not r_exists:
        return _propagar("local->remoto", local_path, remoto, remote_path, l_sha, l_size,
                         res, dry, motivo="el remoto no lo tiene")
    if r_exists and not l_exists:
        return _propagar("remoto->local", local_path, remoto, remote_path, r_sha, r_size,
                         res, dry, motivo="el local no lo tiene")

    # Ambos existen
    if l_sha == r_sha:
        print("Idénticos. Ya están sincronizados.")
        if base_sha != l_sha and not dry:
            _save_baseline(key, l_sha)
        _audit({"accion": "sin_cambios", "sha": l_sha})
        return dict(res, estado="en_sync")

    l_changed = (l_sha != base_sha)
    r_changed = (r_sha != base_sha)

    if base_sha is None:
        # Sin baseline y difieren: no se puede saber quién manda → conflicto, no pisar.
        msg = ("🔀 El playbook (ESTADO-ACTUAL.md) difiere entre este equipo y %s y no hay "
               "referencia previa de sync, así que no sé cuál es el bueno. No he tocado nada. "
               "Elige tú cuál conservar." % remoto)
        print("CONFLICTO (sin baseline): difieren y no puedo elegir. NO toco nada.")
        _alerta(msg, dry)
        _audit({"accion": "conflicto_sin_baseline", "local_sha": l_sha, "remote_sha": r_sha})
        return dict(res, estado="conflicto")

    if l_changed and r_changed:
        msg = ("🔀 El playbook (ESTADO-ACTUAL.md) cambió en LAS DOS máquinas desde la última "
               "sincronización con %s. Para no pisar una edición tuya, NO he tocado nada. "
               "Dime con cuál te quedas y lo sincronizo." % remoto)
        print("CONFLICTO REAL: ambos cambiaron desde el baseline. NO toco nada.")
        _alerta(msg, dry)
        _audit({"accion": "conflicto", "local_sha": l_sha, "remote_sha": r_sha, "base_sha": base_sha})
        return dict(res, estado="conflicto")

    if l_changed:
        return _propagar("local->remoto", local_path, remoto, remote_path, l_sha, l_size,
                         res, dry, motivo="cambió aquí desde el último sync")
    else:  # r_changed
        return _propagar("remoto->local", local_path, remoto, remote_path, r_sha, r_size,
                         res, dry, motivo="cambió en %s desde el último sync" % remoto)


def _propagar(direccion, local_path, remoto, remote_path, src_sha, src_size, res, dry, motivo):
    """Propaga en una dirección concreta, con guardas anti-corrupto + backup del destino."""
    key = "%s:%s" % (remoto, remote_path)
    print("→ Propagar %s (%s)." % (direccion, motivo))

    # Guarda anti-corrupto: nunca propagar un fichero vacío/sospechosamente pequeño.
    if src_size < MIN_BYTES:
        msg = ("⚠️ Iba a sincronizar el playbook %s pero la copia de origen pesa solo %d B "
               "(sospechoso de vacío/truncado). No he tocado nada por seguridad." % (direccion, src_size))
        print("  ABORTA: origen %d B < %d B (anti-corrupto). NO toco nada." % (src_size, MIN_BYTES))
        _alerta(msg, dry)
        _audit({"accion": "aborta_anticorrupto", "direccion": direccion, "src_size": src_size})
        return dict(res, estado="aborta_anticorrupto")

    if dry:
        print("  (dry) copiaría origen→destino y guardaría baseline=%s. Sin escribir." % src_sha[:12])
        return dict(res, estado="propagaria", direccion=direccion)

    # BACKUP del destino que se va a pisar + rsync.
    if direccion == "local->remoto":
        # backup del remoto en el propio remoto, luego rsync local→remoto
        _remote_backup(remoto, remote_path)
        rc = _rsync(local_path, "%s:%s" % (remoto, remote_path))
    else:
        _backup_local(local_path, "pisado-local")
        rc = _rsync("%s:%s" % (remoto, remote_path), local_path)

    if rc != 0:
        print("  rsync falló (rc=%d). NO actualizo baseline." % rc)
        _audit({"accion": "rsync_fallo", "direccion": direccion, "rc": rc})
        return dict(res, estado="rsync_fallo", direccion=direccion)

    _save_baseline(key, src_sha)
    print("  ✓ Sincronizado (%s). Baseline=%s." % (direccion, src_sha[:12]))
    _audit({"accion": "propagado", "direccion": direccion, "sha": src_sha, "motivo": motivo})
    return dict(res, estado="propagado", direccion=direccion)


def _remote_backup(remoto, remote_path):
    """Copia de seguridad del fichero remoto ANTES de pisarlo, en el propio remoto."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    cmd = 'f=%s; [ -f "$f" ] && cp -p "$f" "$f.bak-%s" || true' % (_rpath(remote_path), stamp)
    subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", remoto, cmd],
                   capture_output=True, text=True, timeout=30)


def _rsync(src, dst):
    return subprocess.run(
        ["rsync", "-q", "-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes", src, dst],
        capture_output=True, text=True, timeout=120,
    ).returncode


def main(argv):
    ap = argparse.ArgumentParser(description="Sincroniza el playbook clínico Air↔mini, conflict-safe.")
    ap.add_argument("--apply", action="store_true", help="ejecuta (por defecto: dry, no escribe)")
    ap.add_argument("--remoto", default="polaris", help="alias SSH del otro equipo (def: polaris)")
    ap.add_argument("--local", default=None, help="ruta local del playbook (def: casa base)")
    ap.add_argument("--remote-path", default=None, help="ruta remota del playbook")
    ap.add_argument("--elige", choices=["local", "remoto"], default=None,
                    help="BOOTSTRAP: declara qué copia es la buena y rompe el empate sin baseline")
    ap.add_argument("--si-viejo", type=int, default=None, metavar="MIN",
                    help="pull-al-abrir: solo intenta si el último intento fue hace >MIN minutos")
    ap.add_argument("--nudge-frescura", type=int, default=None, metavar="DIAS",
                    help="Fase 3: avisa a {{TITULAR}} si el playbook lleva >DIAS sin cambiar (no sincroniza)")
    a = ap.parse_args(argv)

    # Fase 3: aviso de frescura (no toca el sync).
    if a.nudge_frescura is not None:
        res = nudge_frescura(a.nudge_frescura, dry=not a.apply, local_path=a.local)
        print("\n" + json.dumps(res, ensure_ascii=False))
        return 0

    # Throttle del pull-al-abrir: si se intentó hace poco, no repetir (barato en cada sesión).
    if a.si_viejo is not None and _demasiado_reciente(a.si_viejo):
        print("Sync intentado hace <%d min; no repito." % a.si_viejo)
        print("\n" + json.dumps({"estado": "throttled", "si_viejo": a.si_viejo}, ensure_ascii=False))
        return 0
    if a.si_viejo is not None:
        _marca_intento()

    res = run(remoto=a.remoto, local_path=a.local, remote_path=a.remote_path,
              apply=a.apply, elige=a.elige)
    print("\n" + json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
