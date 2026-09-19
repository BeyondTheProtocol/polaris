#!/usr/bin/env bash
# tools/backup.sh — copia cifrada e incremental de Polaris (restic) → disco POLARIS-BACKUP.
#
# Protege lo IRREMPLAZABLE: la FUENTE DE VERDAD (incl. _PRIVADO_CLINICO), el sistema (tools,
# agentes, muro) y la MEMORIA durable (~/.claude/.../memory). restic cifra del lado cliente,
# así que aunque el disco USB no esté cifrado, los datos viajan/duermen cifrados con la clave
# del Llavero (`btp-restic-password`) — NUNCA en claro. Destino LOCAL/USB, JAMÁS nube pública
# (el muro: clínico crudo solo en infra de confianza).
#
# Idempotente y fail-safe: si el disco no está montado, NO es error (sale 0 y lo registra).
# ⚠️ Ese silencio es DELIBERADO aquí (el USB no siempre está enchufado), así que la vigilancia
# de "¿cuánto lleva sin copia con ÉXITO?" tiene que vivir FUERA: healthcheck debe avisar si el
# último backup correcto tiene más de N días. Sin eso, un backup ausente pasa inadvertido.
# Retención: 7 diarias / 4 semanales / 6 mensuales + prune (mantiene la copia acotada).
set -uo pipefail

REPO_DRIVE="/Volumes/POLARIS-BACKUP"
export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-$REPO_DRIVE/restic}"
PASS_CMD='security find-generic-password -s btp-restic-password -w'
# Rutas DERIVADAS del entorno, nunca hardcodeadas: este script corre en DOS máquinas con
# usuarios distintos (Polaris = `polaris`, MacBook Air = `titular`). Tenerlas fijas a un
# `/Users/<alguien>` fue la causa de que el backup muriera en silencio 13 días (12→25 jul 26):
# la migración al Air dejó sus rutas escritas aquí y en Polaris fallaba con rc=1.
SRC_REPO="${BTP_REPO:-$HOME/claudecode}"
LOG_DIR="$SRC_REPO/tools/launchd/logs"
LOG="$LOG_DIR/backup.log"
EXCLUDES="$SRC_REPO/tools/backup-excludes.txt"
mkdir -p "$LOG_DIR"

# La memoria durable vive en ~/.claude/projects/<slug>/memory, y el <slug> depende de la ruta
# del proyecto en CADA máquina (en el Air va por iCloud) → se DESCUBRE, no se hardcodea.
# SRC arranca con el repo, así que nunca queda vacío (seguro con `set -u` y bash 3.2 de macOS).
#
# TODOS los proyectos, no solo los que digan «claudecode» (31-jul-26). El filtro `*claudecode*`
# dejaba fuera `~/.claude/projects/-Users-polaris/memory`, que es la memoria de las sesiones
# abiertas desde el HOME y se inyecta en cada una de ellas. Eran 5 ficheros sin copia mientras
# el log decía «fuentes: …memory» y todo parecía cubierto. Un backup que parece completo y no lo
# es engaña más que no tener backup.
SRC=("$SRC_REPO")
for _mem in "$HOME"/.claude/projects/*/memory; do
  [ -d "$_mem" ] && SRC+=("$_mem")
done

# Gancho de prueba: enseña qué copiaría y sale, sin tocar el disco ni pedir la clave. Existe
# porque lo que protege TODO lo demás no tenía ni un test (31-jul-26).
if [ -n "${BTP_BACKUP_DRY:-}" ]; then
  printf 'fuentes: %s\n' "${SRC[*]}"
  [ "${#SRC[@]}" -eq 1 ] && printf 'AVISO: no veo la memoria durable\n'
  exit 0
fi

log(){ echo "$(date '+%Y-%m-%dT%H:%M:%S') backup: $*" | tee -a "$LOG"; }

# Guard 1: disco montado y escribible
if ! mount | grep -q " on $REPO_DRIVE "; then
  log "POLARIS-BACKUP no montado → salto (no es error)."; exit 0
fi
# Guard 2: el volumen se puede LEER. Si macOS lo bloquea por privacidad (TCC: "Volúmenes
# extraíbles" / Acceso total al disco), el síntoma imita a "no hay repo" y manda a init-ear un
# repo que SÍ existe. Se distingue explícitamente (nos costó una sesión entera el 25-jul-26).
if ! ls "$REPO_DRIVE" >/dev/null 2>&1; then
  log "ERROR: $REPO_DRIVE está montado pero ILEGIBLE (operation not permitted) → es permiso de"
  log "       macOS, NO un repo que falte. Concede Acceso total al disco / Volúmenes extraíbles"
  log "       al proceso que ejecuta este script (Terminal/launchd) y vuelve a lanzarlo."
  exit 1
fi
# Guard 3: el repo existe
if [ ! -e "$RESTIC_REPOSITORY/config" ]; then
  log "ERROR: no hay repo restic en $RESTIC_REPOSITORY (init manual primero)."; exit 1
fi

log "=== inicio · repo=$RESTIC_REPOSITORY ==="
[ "${#SRC[@]}" -eq 1 ] && log "AVISO: no veo la memoria durable (~/.claude/projects/*claudecode*/memory) → copio solo el repo."
log "fuentes: ${SRC[*]}"
restic backup "${SRC[@]}" \
  --password-command "$PASS_CMD" \
  --exclude-file "$EXCLUDES" \
  --exclude-caches \
  --tag auto --tag polaris \
  --host Polaris >>"$LOG" 2>&1
rc=$?
# 0 = ok · 3 = backup hecho pero algún fichero no se pudo leer (warning, no fatal)
if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
  log "ERROR: restic backup salió con rc=$rc"; exit "$rc"
fi
[ "$rc" -eq 3 ] && log "aviso: algunos ficheros no se pudieron leer (rc=3), copia hecha igual."

log "retención 7d/4w/6m + prune"
restic forget --password-command "$PASS_CMD" \
  --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune >>"$LOG" 2>&1 \
  || log "aviso: forget/prune devolvió error (revisar $LOG)"

log "check de integridad (estructura+metadatos)"
restic check --password-command "$PASS_CMD" >>"$LOG" 2>&1 \
  && log "check OK" || log "AVISO: check reportó problemas (revisar $LOG)"

log "=== fin (rc backup=$rc) ==="
exit 0
