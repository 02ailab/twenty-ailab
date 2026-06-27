#!/usr/bin/env bash
# Weekly offsite sync of /root/backups per general_docs/BACKUP_STANDARD.md.
# Bundles the whole backup tree -> age-encrypts -> ships via rclone.
# STUB until OFFSITE_REMOTE is set: with no remote it does NOTHING (no local bundle,
# to avoid filling the VPS disk) and exits 0. Set OFFSITE_REMOTE when a 2nd server
# exists to enable shipping.
set -euo pipefail

ROOT="/root/backups"; STAGE="$ROOT/_offsite"; BIN="$ROOT/bin"
LOG="$BIN/backup.log"; RECIP="$BIN/age-recipient.txt"
OFFSITE_REMOTE="${OFFSITE_REMOTE:-}"      # e.g. "remote:saldo-backups" (rclone) — empty = stub
STAGE_KEEP="${STAGE_KEEP:-4}"

log() { echo "$(date -Iseconds) offsite-sync $*" | tee -a "$LOG"; }

if [ -z "$OFFSITE_REMOTE" ]; then
  log "remote not configured (OFFSITE_REMOTE empty) — nothing to do (stub)"
  exit 0
fi

mkdir -p "$STAGE"; chmod 700 "$STAGE"
[ -s "$RECIP" ] || { log "ERROR age recipient missing ($RECIP) — aborting"; exit 1; }

BUNDLE="$STAGE/saldo-backups-$(date +%Y%m%d-%H%M%S).tar.age"
tar -C "$ROOT" --exclude=_offsite --exclude=bin -czf - . | age -r "$(cat "$RECIP")" -o "$BUNDLE"
chmod 600 "$BUNDLE"
log "staged $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"

rclone copy "$BUNDLE" "$OFFSITE_REMOTE" --log-file "$LOG" --log-level INFO
log "synced $BUNDLE -> $OFFSITE_REMOTE"

# local stage retention
ls -1t "$STAGE"/saldo-backups-*.tar.age 2>/dev/null | tail -n +$((STAGE_KEEP+1)) | xargs -r rm -f
log "done"
