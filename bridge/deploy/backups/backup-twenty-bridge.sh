#!/usr/bin/env bash
# Full backup of twenty-bridge (DB + k8s Secret) per general_docs/BACKUP_STANDARD.md.
# twenty-bridge stores only a mapping DB and has no uploaded files, so "full" =
# Postgres dump + encrypted Secret. Reference implementation / standard's exemplar.
#
# Deploy: copy to /root/backups/bin/ on the VPS, cron via /etc/cron.d/saldo-backups.
# Requires: kubectl (host), age, an age public key at /root/backups/bin/age-recipient.txt.
set -euo pipefail

SVC="twenty-bridge"; NS="twenty-bridge"
ROOT="/root/backups"; DEST="$ROOT/$SVC"; BIN="$ROOT/bin"
LOG="$BIN/backup.log"; RECIP="$BIN/age-recipient.txt"
RETENTION="${BACKUP_RETENTION:-7}"

ts()  { date +%Y%m%d-%H%M%S; }
log() { echo "$(date -Iseconds) backup[$SVC] $*" | tee -a "$LOG"; }
prune() { ls -1t "$1"/*"$2" 2>/dev/null | tail -n +$((RETENTION+1)) | xargs -r rm -f; }

mkdir -p "$DEST/db" "$DEST/secrets"; chmod 700 "$DEST" "$DEST/db" "$DEST/secrets"
STAMP="$(ts)"

# --- database (password from the pod's own env; local socket inside the pod) ---
DB="$DEST/db/$SVC-db-$STAMP.sql.gz"
if kubectl -n "$NS" exec statefulset/twenty-bridge-postgres -c postgres -- \
     sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
     | gzip > "$DB"; then
  chmod 600 "$DB"; log "db ok $DB ($(du -h "$DB" | cut -f1))"
else
  log "ERROR db dump failed"; rm -f "$DB"; exit 1
fi

# --- secrets (encrypted; refuse to ever write plaintext) ---
if [ ! -s "$RECIP" ]; then
  log "ERROR age recipient missing ($RECIP) — refusing to write plaintext secrets"; exit 1
fi
SEC="$DEST/secrets/$SVC-secrets-$STAMP.yaml.age"
kubectl -n "$NS" get secret twenty-bridge-secrets -o yaml | age -r "$(cat "$RECIP")" -o "$SEC"
chmod 600 "$SEC"; log "secrets ok $SEC"

# --- retention ---
prune "$DEST/db" ".sql.gz"; prune "$DEST/secrets" ".yaml.age"
log "done (retention=$RETENTION)"
